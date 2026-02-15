#!/usr/bin/env python3
"""
Focus Agent — main application.

Real activity monitoring + time-on-page tracking + EEG (real or mock) → Jetson.
When you stay on difficult content too long, triggers agent for help. Feedback in overlay.

Usage:
  python app.py                            # Real activity + mock EEG
  python app.py --eeg                     # Real Emotiv headset (requires .env)
  python app.py --mock --long 45           # 45 sec on page before trigger
  python app.py --mock --no-feedback       # No overlay window
"""
import argparse
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import websocket
except ImportError:
    websocket = None

import config
from activity import ActivityMonitor
from agent_request import build_agent_request, build_post_eeg_body, build_reading_help_ws_message
from data_schema import (
    ActivitySnapshot,
    CollectorPayload,
    EEGMetricsSnapshot,
    MentalStateSnapshot,
)
from feedback_window import FeedbackWindow
from mental_state_parser import parse_met_to_mental_state
from time_tracker import SessionTracker, SessionEvent, SessionEventType


# Overlay window identifiers — when focused, use last real context for session/help
OVERLAY_APP_TITLES = ("Agent Feedback", "Python | Agent Feedback")
OVERLAY_APP_NAME = "Python"


def _is_overlay(ctx) -> bool:
    """True if ctx is the feedback overlay (don't use for session tracking)."""
    if not ctx:
        return False
    title = (getattr(ctx, "window_title", None) or "").strip()
    app = (getattr(ctx, "app_name", None) or "").strip()
    if "Agent Feedback" in title:
        return True
    if app == OVERLAY_APP_NAME and any(t in title for t in OVERLAY_APP_TITLES):
        return True
    return False


def _ctx_to_snapshot(ctx, duration_seconds: float | None = None) -> ActivitySnapshot | None:
    if not ctx:
        return None
    return ActivitySnapshot(
        app_name=getattr(ctx, "app_name", "") or "",
        window_title=getattr(ctx, "window_title", "") or "",
        context_type=getattr(ctx, "context_type", "app") or "app",
        context_id=getattr(ctx, "context_id", "") or "",
        reading_section=getattr(ctx, "reading_section", None),
        duration_seconds=duration_seconds,
    )


# --- Shared state ---
class AppState:
    def __init__(self):
        self.last_mental_state: MentalStateSnapshot | None = None
        self.lock = threading.Lock()

    def set_mental_state(self, ms: MentalStateSnapshot):
        with self.lock:
            self.last_mental_state = ms

    def get_mental_state(self) -> MentalStateSnapshot | None:
        with self.lock:
            return self.last_mental_state


def run_app(
    jetson_ws_url: str,
    jetson_http_base: str,
    use_mock_eeg: bool = True,
    show_feedback: bool = True,
    warn_sec: float = 120,
    long_sec: float = 180,
    follow_up_interval_sec: float = 300,
    poll_interval: float = 0.3,
) -> None:
    if not websocket:
        print("Error: pip install websocket-client")
        sys.exit(1)

    state = AppState()
    running = True
    ws_ref: dict = {"ws": None}
    activity = ActivityMonitor(poll_interval=poll_interval)
    session_tracker = SessionTracker(
        warn_threshold_sec=min(warn_sec, max(1, long_sec - 30)),
        long_threshold_sec=long_sec,
        follow_up_interval_sec=follow_up_interval_sec,
    )

    # Overlay exclusion: when overlay is focused, use last real context for session/help
    last_real_context = [None]  # list to allow mutation in closure

    def stop(_=None, __=None):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    # Feedback window — polls GET /feedback so agent response reliably appears
    feedback_cb = None
    if show_feedback:
        poll_url = f"{jetson_http_base.rstrip('/')}/feedback"
        win = FeedbackWindow(width=360, height=160, use_poll=True, poll_url=poll_url)
        feedback_cb = win.update_feedback
        win.root.protocol("WM_DELETE_WINDOW", lambda: (stop(), win.root.destroy()))
        win.update_feedback("Monitoring... Stay on a difficult page to trigger help.")

    def send_payload(payload: CollectorPayload):
        if ws_ref["ws"] and ws_ref["ws"].sock and ws_ref["ws"].sock.connected:
            try:
                ws_ref["ws"].send(json.dumps(payload.to_dict()))
                act = payload.activity
                ms = payload.mental_state
                parts = []
                if act and (act.app_name or act.context_type):
                    title = (act.window_title or "")[:35]
                    parts.append(f"{act.app_name or '?'} | {title}{'...' if len(act.window_title or '') > 35 else ''} | {act.context_type or '?'}")
                if ms:
                    ms_parts = []
                    if ms.engagement is not None:
                        ms_parts.append(f"eng={ms.engagement:.2f}")
                    if ms.stress is not None:
                        ms_parts.append(f"stress={ms.stress:.2f}")
                    if ms.focus is not None:
                        ms_parts.append(f"focus={ms.focus:.2f}")
                    if ms.relaxation is not None:
                        ms_parts.append(f"relax={ms.relaxation:.2f}")
                    parts.append(f"mental_state=[{', '.join(ms_parts) or 'metrics'}]")
                print(f"  [WS] Sent to backend: {payload.type}" + (" | " + " | ".join(parts) if parts else ""))
            except Exception as e:
                print("  Send error:", e)

    def _make_activity_snapshot(duration_seconds: float | None = None) -> ActivitySnapshot | None:
        ctx = activity.get_current_activity()
        if not ctx:
            return None
        return _ctx_to_snapshot(ctx, duration_seconds)

    # Session events → help request (POST /eeg + WebSocket reading_help)
    def on_session_event(event: SessionEvent):
        if event.event_type not in (SessionEventType.LONG_THRESHOLD, SessionEventType.FOLLOW_UP):
            return
        user_feedback = (
            "(Still on this – try a different angle)"
            if event.event_type == SessionEventType.FOLLOW_UP
            else None
        )
        ms = state.get_mental_state()
        act = _ctx_to_snapshot(event.context, event.duration_seconds)
        req = build_agent_request(act, ms, user_feedback=user_feedback)

        # WebSocket: reading_help
        if ws_ref["ws"] and ws_ref["ws"].sock and ws_ref["ws"].sock.connected:
            try:
                d = build_reading_help_ws_message(req)
                ws_ref["ws"].send(json.dumps(d))
                title = (act.window_title or "")[:35] if act else ""
                desc = f"{act.app_name or '?'} | {title}{'...' if len(act.window_title or '') > 35 else ''} | {act.context_type or '?'}" if act else "reading_help"
                print(f"  [WS] Sent to backend: reading_help | {desc}")
            except Exception:
                pass
        print(f"  [Session] {event.event_type.value} after {event.duration_seconds:.0f}s on: {event.context.display_name}")
        # POST /eeg for immediate feedback
        if feedback_cb:
            try:
                import requests
                streams_met = ms.metrics if ms and ms.metrics else None
                body = build_post_eeg_body(req, streams_met=streams_met)
                r = requests.post(
                    f"{jetson_http_base}/eeg",
                    json=body,
                    headers={"Content-Type": "application/json", "ngrok-skip-browser-warning": "1"},
                    timeout=15,
                )
                print(f"  [HTTP] POST {jetson_http_base}/eeg -> {r.status_code}", end="")
                if r.status_code == 200 and r.text:
                    data = r.json()
                    fb = data.get("feedback") or data.get("message")
                    if fb:
                        feedback_cb(fb)
                        print(f" | feedback {len(fb)} chars")
                    else:
                        print(f" | no feedback in response")
                else:
                    print()
            except Exception as e:
                print(f" | error: {e}")
                if feedback_cb:
                    feedback_cb("(No response from Jetson – check connection)")

    session_tracker.on_session_event(on_session_event)

    # WebSocket
    def on_message(ws, message):
        try:
            data = json.loads(message)
            if data.get("type") == "feedback" and feedback_cb:
                feedback_cb(data.get("feedback", ""))
        except (json.JSONDecodeError, KeyError):
            pass

    def on_open(ws):
        print("  Connected to Jetson")

    def on_close(ws, *args):
        print("  Disconnected from Jetson")

    ws = websocket.WebSocketApp(
        jetson_ws_url,
        on_message=on_message if feedback_cb else None,
        on_open=on_open,
        on_close=on_close,
    )
    ws_ref["ws"] = ws
    ws_thread = threading.Thread(target=lambda: ws.run_forever(), daemon=True)
    ws_thread.start()

    time.sleep(1)

    # --- EEG source ---
    if use_mock_eeg:
        MOCK_MET = {"met": [True, 0.65, True, 0.42, 0.38, True, 0.55], "time": 0}
        mock_count = [0]

        def mock_eeg_loop():
            while running:
                mock_count[0] += 1
                t = time.time()
                met = dict(MOCK_MET)
                met["time"] = t
                c = mock_count[0]
                met["met"] = [
                    True, 0.55 + 0.15 * ((c % 5) / 5),  # eng.isActive, eng
                    True, 0.4, 0.35,                     # exc.isActive, exc, lex
                    True, 0.35 + 0.2 * ((c % 7) / 7),    # str.isActive, str
                    True, 0.4 + 0.2 * ((c % 3) / 3),     # rel.isActive, rel
                    True, 0.45,                           # int.isActive, int
                    True, 0.5 + 0.2 * ((c % 11) / 11),   # attention.isActive, attention
                ]
                ms = parse_met_to_mental_state(met)
                state.set_mental_state(ms)
                ctx = activity.get_current_activity()
                # Overlay exclusion: use last real context for payloads
                effective_ctx = last_real_context[0] if _is_overlay(ctx) else ctx
                sess = session_tracker.get_current_session()
                dur = sess.duration_seconds if sess else None
                if effective_ctx:
                    act = _ctx_to_snapshot(effective_ctx, dur)
                    send_payload(CollectorPayload(type="activity", timestamp=t, activity=act))
                    send_payload(CollectorPayload(
                        type="eeg", timestamp=t,
                        eeg=EEGMetricsSnapshot(metrics=met),
                        activity=act,
                    ))
                    send_payload(CollectorPayload(type="mental_state", timestamp=t, mental_state=ms))
                time.sleep(config.POLL_INTERVAL)

        eeg_thread = threading.Thread(target=mock_eeg_loop, daemon=True)
    else:
        if not config.EMOTIV_CLIENT_ID or not config.EMOTIV_CLIENT_SECRET:
            print("Error: Real EEG requires EMOTIV_CLIENT_ID and EMOTIV_CLIENT_SECRET in .env")
            sys.exit(1)
        try:
            from eeg import EmotivCortexClient

            def on_metrics(metrics: dict):
                t = time.time()
                ms = parse_met_to_mental_state(metrics)
                state.set_mental_state(ms)
                ctx = activity.get_current_activity()
                effective_ctx = last_real_context[0] if _is_overlay(ctx) else ctx
                sess = session_tracker.get_current_session()
                if effective_ctx:
                    act = _ctx_to_snapshot(effective_ctx, sess.duration_seconds if sess else None)
                    send_payload(CollectorPayload(
                        type="eeg", timestamp=t,
                        eeg=EEGMetricsSnapshot(metrics=metrics),
                        activity=act,
                    ))
                    send_payload(CollectorPayload(type="mental_state", timestamp=t, mental_state=ms))

            emotiv = EmotivCortexClient(
                client_id=config.EMOTIV_CLIENT_ID,
                client_secret=config.EMOTIV_CLIENT_SECRET,
                on_metrics=on_metrics,
                streams=["met"],
            )
            emotiv.connect()
            print("  Emotiv Cortex: connecting... (met only)")

            def activity_sender():
                while running:
                    ctx = activity.get_current_activity()
                    effective_ctx = last_real_context[0] if _is_overlay(ctx) else ctx
                    if effective_ctx:
                        sess = session_tracker.get_current_session()
                        dur = sess.duration_seconds if sess else None
                        act = _ctx_to_snapshot(effective_ctx, dur)
                        send_payload(CollectorPayload(type="activity", timestamp=time.time(), activity=act))
                    time.sleep(config.POLL_INTERVAL)

            eeg_thread = threading.Thread(target=activity_sender, daemon=True)
        except Exception as e:
            print(f"Emotiv error: {e}")
            sys.exit(1)

    eeg_thread.start()

    # --- Main poll loop: activity + session tracker (with overlay exclusion) ---
    def poll_loop():
        while running:
            ctx = activity.get_current_activity()
            if ctx and not _is_overlay(ctx):
                last_real_context[0] = ctx
            # Feed session tracker with last real context when overlay is focused
            effective = ctx if not _is_overlay(ctx) else last_real_context[0]
            session_tracker.update(effective)
            time.sleep(poll_interval)

    poll_thread = threading.Thread(target=poll_loop, daemon=True)
    poll_thread.start()

    print("\n--- Focus Agent ---")
    print(f"  Jetson: {jetson_http_base}")
    print(f"  Activity: real (app, window, context type)")
    print(f"  EEG: {'mock' if use_mock_eeg else 'real Emotiv'}")
    print(f"  Triggers: long={long_sec}s, follow-up every {follow_up_interval_sec}s")
    if show_feedback:
        print("  Feedback: overlay window")
    print("  Stay on a difficult page to get help. Ctrl+C or close window to stop.\n")

    if show_feedback:
        win.run()
    else:
        while running:
            time.sleep(0.5)

    print("\nStopped.")


def main():
    p = argparse.ArgumentParser(description="Focus Agent — real activity + time on page + EEG → Jetson")
    p.add_argument("--url", default=None, help="Jetson base URL (default: from config)")
    p.add_argument("--eeg", action="store_true", dest="real_eeg", help="Use real Emotiv headset. Default: mock EEG.")
    p.add_argument("--no-feedback", action="store_true", help="No overlay window")
    p.add_argument("--warn", type=float, default=None, help="Warn threshold (sec). Default: from config or 120.")
    p.add_argument("--long", type=int, default=None, help="Seconds on page before stuck trigger. Default: from config or 180.")
    p.add_argument("--poll", type=float, default=0.3)
    args = p.parse_args()

    base = args.url or config.JETSON_BASE.rstrip("/")
    ws_url = config.JETSON_WS_URL or base.replace("https://", "wss://").replace("http://", "ws://")

    warn_sec = args.warn if args.warn is not None else getattr(config, "WARN_SESSION_THRESHOLD", 20)
    long_sec = args.long if args.long is not None else getattr(config, "LONG_SESSION_THRESHOLD", 30)
    follow_up = getattr(config, "FOLLOW_UP_INTERVAL", 300)

    run_app(
        jetson_ws_url=ws_url,
        jetson_http_base=base,
        use_mock_eeg=not args.real_eeg,
        show_feedback=not args.no_feedback,
        warn_sec=warn_sec,
        long_sec=long_sec,
        follow_up_interval_sec=follow_up,
        poll_interval=args.poll,
    )


if __name__ == "__main__":
    main()
