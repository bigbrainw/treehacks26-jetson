#!/usr/bin/env python3
"""
Jetson Processor - Receives activity + EEG from Mac, runs agent.

Runs on Jetson Nano (Linux). Does NOT monitor locally.
All data from Mac via:
  - WebSocket (collector.py): activity + EEG
  - HTTP POST /eeg (StreamToJetson): {timestamp, streams: {met, pow, mot, dev}, context?: {...}}

Usage on Jetson:
  python processor_main.py --port 8765
  # Mac: python collector.py --url ws://JETSON_IP:8765 (sends activity + EEG)
"""

import argparse
import asyncio
import concurrent.futures
import json
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from activity_tracker import ActivityContext
from agent import FocusAssistant, MultiTurnAssistant
from data_schema import ActivitySnapshot, CollectorPayload, parse_mac_payload
from eeg import EEGBridge
from storage import Storage
from time_tracker import SessionTracker, SessionEvent, SessionEventType


def _met_to_metrics(met) -> dict:
    """Convert met stream to metrics dict for EEGBridge. Handles dict, list, or nested {met: [...], time: ...}."""
    if met is None:
        return {}
    # Nested format: {"met": [0.5, 0.3, ...], "time": ...}
    if isinstance(met, dict) and "met" in met:
        met = met["met"]
    if isinstance(met, dict):
        return {k: float(v) for k, v in met.items() if isinstance(v, (int, float))}
    # Cortex SDK may pass list: [eng, interest, relaxation, stress, attention, ...] (bools coerced to float)
    if isinstance(met, (list, tuple)):
        labels = ["eng", "interest", "relaxation", "stress", "attention", "focus"]
        result = {}
        for i, v in enumerate(met):
            if i >= len(labels):
                break
            if isinstance(v, (int, float)):
                result[labels[i]] = float(v)
            elif isinstance(v, bool):
                result[labels[i]] = 1.0 if v else 0.0
        return result
    return {}


def run_processor(port: int):
    """Run Jetson: receive activity + EEG from Mac (WebSocket) → agent. No local monitoring."""
    storage = Storage(config.DB_PATH)
    AssistantClass = MultiTurnAssistant if config.MULTITURN_AGENT else FocusAssistant
    assistant = AssistantClass(
        api_key=config.ANTHROPIC_API_KEY,
        model=config.ANTHROPIC_MODEL,
    )
    session_tracker = SessionTracker(
        warn_threshold_sec=config.WARN_SESSION_THRESHOLD,
        long_threshold_sec=config.LONG_SESSION_THRESHOLD,
        follow_up_interval_sec=config.FOLLOW_UP_INTERVAL,
    )
    eeg_bridge = EEGBridge()

    current_session_id = None
    prev_context_id = None
    current_context_from_mac: ActivityContext | None = None
    prefetch_cache: dict[str, str] = {}  # context_id -> prepared_resources
    last_feedback_at: float = 0
    feedback_cooldown_sec: float = getattr(config, "FEEDBACK_COOLDOWN_SEC", 180)
    _prefetch_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    latest_feedback: str = ""  # agent + mental-command messages
    connected_ws_clients: set = set()  # push feedback over WebSocket to collector

    def _prefetch_worker(ctx: ActivityContext):
        """Background: PDF page analysis. Web search via Agent SDK when stuck."""
        try:
            from agent.pdf_pipeline import build_pdf_prepared_resources

            section = getattr(ctx, "reading_section", None)
            page_content = getattr(ctx, "page_content", None) or ""

            if ctx.context_type == "pdf" and page_content:
                from activity_tracker.pdf_context import parse_pdf_window_title
                parsed = parse_pdf_window_title(ctx.app_name, ctx.window_title or "")
                doc_name = parsed.get("doc_name", ctx.window_title or "") if parsed else ctx.window_title or ""
                page_num = parsed.get("page_num", 1) if parsed else 1
                result = build_pdf_prepared_resources(
                    page_content, doc_name, page_num, section, web_related=None,
                    api_key=config.ANTHROPIC_API_KEY, model=config.ANTHROPIC_MODEL,
                )
            else:
                # Non-PDF: Agent SDK WebSearch/WebFetch when decide() is called
                result = None

            if result:
                prefetch_cache[ctx.context_id] = result
        except Exception:
            pass

    def _start_prefetch(ctx: ActivityContext):
        """Start background prefetch for this context."""
        _prefetch_executor.submit(_prefetch_worker, ctx)

    def on_context_change(new_ctx: ActivityContext, prev_ctx: ActivityContext | None):
        nonlocal current_session_id, prev_context_id
        if hasattr(assistant, "clear_conversation") and prev_context_id:
            assistant.clear_conversation(prev_context_id)
        if current_session_id:
            prev_session = session_tracker.get_current_session()
            duration = prev_session.duration_seconds if prev_session else 0
            storage.end_session(current_session_id, duration)
        current_session_id = storage.start_session(new_ctx)
        prev_context_id = new_ctx.context_id
        # Prefetch in background so it's ready when EEG triggers
        _start_prefetch(new_ctx)

    def on_session_event(event: SessionEvent):
        print(f"  [Event] {event.event_type.value}: {event.context.display_name} ({event.duration_seconds:.0f}s)")
        if event.event_type in (SessionEventType.LONG_THRESHOLD, SessionEventType.FOLLOW_UP):
            print("    -> Mental state check (EEG)")
            eeg_bridge.handle_session_event(event, storage)

    def _normalize_mental_state_metrics(raw: dict | None) -> dict | None:
        """Normalize to agent format: engagement, stress, relaxation, focus, excitement, interest (0–1)."""
        if not raw:
            return None
        key_map = {"eng": "engagement", "str": "stress", "rel": "relaxation", "attention": "focus", "int": "interest"}
        m = {}
        # Accept Mac format (engagement, stress, ...) or raw (eng, str, ...)
        for k, v in raw.items():
            if isinstance(v, (int, float)) and k in key_map:
                m[key_map[k]] = float(v)
            elif isinstance(v, (int, float)) and k in ("engagement", "stress", "relaxation", "focus", "excitement", "interest"):
                m[k] = float(v)
        # Unwrap nested metrics (Mac sends {metrics: {eng, str, ...}})
        nested = raw.get("metrics") if isinstance(raw.get("metrics"), dict) else None
        if nested:
            for k, v in nested.items():
                if isinstance(v, (int, float)) and k in key_map and key_map[k] not in m:
                    m[key_map[k]] = float(v)
        return m if m else raw

    def on_mental_state(ctx: ActivityContext, duration: float, state, *, is_follow_up: bool = False):
        nonlocal latest_feedback, last_feedback_at
        state_val = state.value
        print(f"  [Mental State] {state_val}: {ctx.display_name} (on page {duration:.0f}s)")
        # Always generate a response—for stuck, distracted, focused, unknown
        # Cooldown: skip unless follow-up (except first time)
        if not is_follow_up and last_feedback_at > 0 and (time.time() - last_feedback_at) < feedback_cooldown_sec:
            print(f"  [Cooldown] Skipping agent ({int(feedback_cooldown_sec - (time.time() - last_feedback_at))}s left)")
            return
        recent = storage.get_recent_sessions(limit=8)
        prepared = prefetch_cache.get(ctx.context_id, "")
        raw_metrics = eeg_bridge.get_last_metrics()
        mental_state_metrics = _normalize_mental_state_metrics(raw_metrics)
        ms_hint = state_val if state_val in ("stuck", "distracted", "focused") else "unknown"
        kwargs = dict(
            app_name=ctx.app_name,
            window_title=ctx.window_title,
            context_type=ctx.context_type,
            duration_seconds=duration,
            mental_state=state_val,
            recent_sessions=recent,
            activity_context=ctx,
            prepared_resources=prepared or None,
            mental_state_metrics=mental_state_metrics,
        )
        if isinstance(assistant, MultiTurnAssistant):
            kwargs["user_feedback"] = (
                "(Still on this - try a different angle)" if is_follow_up else None
            )
        decision = assistant.decide(**kwargs)
        # Always show feedback (same as handle_reading_help)
        msg = (decision.message or "").strip() if decision.should_help else ""
        if not msg:
            msg = {
                "stuck": "You appear stuck. Try a different section or take a short break.",
                "distracted": "Your focus seems to have drifted. Try getting back to the content.",
                "focused": "Good job, keep going!",
            }.get(ms_hint, "Good job, keep going!")
        latest_feedback = msg
        last_feedback_at = time.time()
        print(f"\n  >>> Agent: {msg[:80]}...")
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_broadcast_feedback(msg))
        except RuntimeError:
            pass

    session_tracker.on_session_event(on_session_event)
    eeg_bridge.on_mental_state_detected(on_mental_state)

    def handle_streams_payload(body: dict):
        """Handle POST /eeg. Supports:
        - context: {app_name, window_title, ..., mental_state, mental_state_metrics}
        - activity: {...}, mental_state: {engagement, stress, ...} (unified format)
        """
        nonlocal current_context_from_mac
        from activity_tracker.pdf_context import infer_context_type

        ctx_data = body.get("context") or {}
        activity_data = body.get("activity") or ctx_data
        # mental_state can be top-level or nested in context (StreamToJetson puts it in context)
        mental_state_obj = body.get("mental_state") or ctx_data.get("mental_state")
        streams = body.get("streams", {})
        ts = body.get("timestamp", time.time())

        # Build activity from top-level activity or context
        if activity_data and (activity_data.get("app_name") or activity_data.get("window_title")):
            app = activity_data.get("app_name", "")
            title = activity_data.get("window_title", "")
            ctx_type = activity_data.get("context_type") or infer_context_type(app, title)
            act = ActivitySnapshot(
                app_name=app,
                window_title=title or "",
                context_type=ctx_type,
                context_id=activity_data.get("context_id", "") or f"{app}::{str(title)[:50]}",
                detected_at=ts,
                reading_section=activity_data.get("reading_section"),
                page_content=activity_data.get("page_content"),
                duration_seconds=activity_data.get("duration_seconds"),
            )
            ctx = act.to_activity_context()
            prev_ctx = current_context_from_mac
            if prev_ctx is None or prev_ctx.context_id != ctx.context_id:
                on_context_change(ctx, prev_ctx)
            current_context_from_mac = ctx
            session_tracker.update(ctx)

            # Trigger agent when we have mental state or explicit help request (Mac session event)
            ms_metrics = None
            if isinstance(mental_state_obj, dict):
                ms_metrics = _normalize_mental_state_metrics(mental_state_obj)
            else:
                ms_metrics = _normalize_mental_state_metrics(ctx_data.get("mental_state_metrics"))
            has_mental = ctx_data.get("mental_state") or ctx_data.get("mental_state_metrics") or (isinstance(mental_state_obj, dict) and len(mental_state_obj) > 0)
            trigger = has_mental  # Always trigger when Mac sends activity + mental state (session event)
            if trigger:
                print(f"  [Trigger] POST /eeg -> calling agent (ms_metrics={bool(ms_metrics)})")

            if trigger:
                overlay_hint = ("Agent Feedback" in (title or "")) or (app == "Python" and "Agent" in (title or ""))
                if overlay_hint and prev_ctx and prev_ctx.context_id != ctx.context_id:
                    act = ActivitySnapshot(
                        app_name=prev_ctx.app_name,
                        window_title=prev_ctx.window_title,
                        context_type=prev_ctx.context_type,
                        context_id=prev_ctx.context_id,
                        detected_at=prev_ctx.detected_at,
                        reading_section=getattr(prev_ctx, "reading_section", None),
                        page_content=getattr(prev_ctx, "page_content", None),
                    )
                handle_reading_help(
                    act,
                    activity_data.get("user_feedback") or ctx_data.get("user_feedback") or body.get("user_feedback"),
                    activity_data.get("duration_seconds") or ctx_data.get("duration_seconds", 10.0),
                    ms_metrics,
                )

        met = streams.get("met")
        if isinstance(met, dict) and "met" in met and "time" in met:
            met = met.get("met")
        metrics = _met_to_metrics(met)
        if metrics:
            eeg_bridge.store_metrics(metrics)
        com = streams.get("com")
        if isinstance(com, (list, tuple)) and len(com) >= 2:
            act_cmd = com[0] if isinstance(com[0], str) else "neutral"
            pow_val = float(com[1]) if isinstance(com[1], (int, float)) else 0.0
            if act_cmd and act_cmd != "neutral":
                _handle_mental_command(act_cmd, pow_val)

    def _handle_mental_command(action: str, power: float):
        """When mental command matches pizza trigger, order pizza."""
        nonlocal latest_feedback
        cmd = getattr(config, "MENTAL_COMMAND_PIZZA", "push")
        thresh = getattr(config, "MENTAL_COMMAND_POWER_THRESHOLD", 0.5)
        if action == cmd and power >= thresh:
            print(f"\n  [Mental Command] {action} (power={power:.2f}) → Ordering pizza!")
            try:
                from agent.pizza_order import order_pizza
                msg = order_pizza()
                latest_feedback = msg
                print(f"\n  >>> Pizza: {msg[:500]}{'...' if len(msg) > 500 else ''}")
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(_broadcast_feedback(msg))
                except RuntimeError:
                    pass
            except Exception as e:
                err_msg = f"Pizza order error: {e}"
                latest_feedback = err_msg
                print(f"  Pizza order error: {e}")
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(_broadcast_feedback(err_msg))
                except RuntimeError:
                    pass

    def _metrics_from_eeg(eeg_snap) -> dict:
        """Normalize EEG metrics for EEGBridge (handles met array or flat dict)."""
        m = eeg_snap.metrics if hasattr(eeg_snap, "metrics") else {}
        if not m:
            return {}
        # Already flat (eng, str, rel, attention)
        if any(k in m for k in ("eng", "attention", "str", "rel", "cognitiveStress")):
            return {k: float(v) for k, v in m.items() if isinstance(v, (int, float))}
        # Raw met array
        met = m.get("met") if isinstance(m.get("met"), (list, tuple)) else m
        return _met_to_metrics(met)

    def handle_ws_payload(payload: CollectorPayload):
        nonlocal current_context_from_mac
        if payload.activity:
            ctx = payload.activity.to_activity_context()
            prev_ctx = current_context_from_mac
            if prev_ctx is None or prev_ctx.context_id != ctx.context_id:
                on_context_change(ctx, prev_ctx)
            current_context_from_mac = ctx
            session_tracker.update(ctx)
        if payload.eeg:
            metrics = _metrics_from_eeg(payload.eeg)
            if metrics:
                eeg_bridge.store_metrics(metrics)
        if payload.mental_command:
            _handle_mental_command(
                payload.mental_command.action,
                payload.mental_command.power,
            )

    def handle_reading_help(activity, user_feedback: str | None, duration_seconds: float = 10.0, mental_state_metrics: dict | None = None):
        """Mac determined stuck; trigger agent immediately with real activity."""
        nonlocal current_context_from_mac, latest_feedback, last_feedback_at
        if not activity:
            return
        # Cooldown: avoid rapid-fire feedback (e.g. PDF page flips)
        now = time.time()
        if (now - last_feedback_at) < feedback_cooldown_sec and not user_feedback:
            print(f"  [Cooldown] Skipping agent ({int(feedback_cooldown_sec - (now - last_feedback_at))}s left)")
            return
        # Overlay (Python | Agent Feedback) can steal focus; use last real work context
        ti = (activity.window_title or "").lower()
        ap = (activity.app_name or "").lower()
        if ("agent feedback" in ti) or (ap == "python" and "agent" in ti):
            if current_context_from_mac and current_context_from_mac.context_id != activity.context_id:
                activity = ActivitySnapshot(
                    app_name=current_context_from_mac.app_name,
                    window_title=current_context_from_mac.window_title,
                    context_type=current_context_from_mac.context_type,
                    context_id=current_context_from_mac.context_id,
                    detected_at=current_context_from_mac.detected_at,
                    reading_section=getattr(current_context_from_mac, "reading_section", None),
                    page_content=getattr(current_context_from_mac, "page_content", None),
                )
        ctx = activity.to_activity_context()
        prev_ctx = current_context_from_mac
        if prev_ctx is None or prev_ctx.context_id != ctx.context_id:
            on_context_change(ctx, prev_ctx)
        current_context_from_mac = ctx
        session_tracker.update(ctx)
        duration = duration_seconds
        recent = storage.get_recent_sessions(limit=8)
        prepared = prefetch_cache.get(ctx.context_id, "")
        # Derive hint from metrics (agent will interpret fully)
        ms_hint = "unknown"
        if mental_state_metrics:
            eng = mental_state_metrics.get("engagement", 0.5)
            stress = mental_state_metrics.get("stress", 0.4)
            focus = mental_state_metrics.get("focus", 0.5)
            if eng < 0.4 and stress > 0.5:
                ms_hint = "stuck"
            elif focus < 0.35 or eng < 0.4:
                ms_hint = "wandering"
            elif eng >= 0.5 and stress < 0.5:
                ms_hint = "focused"
        kwargs = dict(
            app_name=ctx.app_name,
            window_title=ctx.window_title,
            context_type=ctx.context_type,
            duration_seconds=duration,
            mental_state=ms_hint,
            recent_sessions=recent,
            activity_context=ctx,
            prepared_resources=prepared or None,
            mental_state_metrics=mental_state_metrics,
        )
        if isinstance(assistant, MultiTurnAssistant):
            kwargs["user_feedback"] = user_feedback
        decision = assistant.decide(**kwargs)
        print(f"  [Agent] should_help={decision.should_help}, msg_len={len(decision.message or '')}, reason={decision.reason[:50] if decision.reason else ''}")
        # Always generate a response—no matter what state. At 30s we always show something.
        msg = (decision.message or "").strip() if decision.should_help else ""
        if not msg:
            msg = {
                "stuck": "You appear stuck. Try a different section or take a short break. Set ANTHROPIC_API_KEY for full help.",
                "wandering": "Your focus seems to have drifted. Try getting back to the content.",
                "focused": "Good job, keep going!",
            }.get(ms_hint, "Good job, keep going!")
        latest_feedback = msg
        last_feedback_at = time.time()
        print(f"\n  >>> Agent: {msg[:80]}...")
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_broadcast_feedback(msg))
        except RuntimeError:
            pass

    try:
        from aiohttp import web
        from aiohttp import WSMsgType
    except ImportError:
        print("Error: pip install aiohttp")
        sys.exit(1)

    running = True

    def stop(_=None, __=None):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    # HTTP POST /eeg - Mac stuck trigger or StreamToJetson format
    async def http_eeg(req):
        try:
            body = await req.json()
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)
        handle_streams_payload(body)
        return web.json_response({"ok": True, "feedback": latest_feedback})

    # GET /feedback - fallback for clients that can't use WebSocket push
    async def http_feedback(req):
        return web.json_response({"feedback": latest_feedback})

    async def _broadcast_feedback(msg: str):
        """Push feedback to all connected WebSocket clients (collector on Mac)."""
        if not msg or not connected_ws_clients:
            return
        payload = json.dumps({"type": "feedback", "feedback": msg})
        dead = []
        for ws in list(connected_ws_clients):
            try:
                await ws.send_str(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            connected_ws_clients.discard(ws)

    # HTTP POST /suggest_snack - preferences + budget, agent suggests (no order)
    async def http_suggest_snack(req):
        try:
            body = await req.json()
            preferences = body.get("preferences", "").strip()
            budget = body.get("budget", 0)
            address = body.get("address")
            if not preferences:
                return web.json_response({"error": "preferences required"}, status=400)
            from agent.snack_suggestion import suggest_snack
            result = suggest_snack(preferences=preferences, budget=budget, address=address)
            return web.json_response({"suggestion": result})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    # WebSocket - Mac sends activity, eeg, mental_state, reading_help, mental_command
    async def websocket_handler(req):
        ws = web.WebSocketResponse()
        await ws.prepare(req)
        connected_ws_clients.add(ws)
        print("  Mac connected via WebSocket")
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        parsed = parse_mac_payload(data)
                        if isinstance(parsed, dict) and parsed.get("_reading_help"):
                            handle_reading_help(
                                parsed.get("activity"),
                                parsed.get("user_feedback"),
                                parsed.get("duration_seconds", 10.0),
                                _normalize_mental_state_metrics(parsed.get("mental_state_metrics")),
                            )
                        elif parsed is not None:
                            handle_ws_payload(parsed)
                        else:
                            payload = CollectorPayload.from_dict(data)
                            handle_ws_payload(payload)
                    except (json.JSONDecodeError, KeyError, TypeError):
                        pass
        finally:
            connected_ws_clients.discard(ws)
            print("  Mac disconnected")
        return ws

    app = web.Application()
    app.router.add_get("/feedback", http_feedback)
    app.router.add_post("/eeg", http_eeg)
    app.router.add_post("/suggest_snack", http_suggest_snack)
    app.router.add_get("/", websocket_handler)
    app.router.add_get("/ws", websocket_handler)

    async def main_loop():
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        print(f"Processor on 0.0.0.0:{port}")
        print(f"  WebSocket push - agent feedback to connected clients")
        print(f"  WebSocket / or /ws - activity + EEG from Mac collector")
        print(f"  POST /eeg  - streams + optional context (StreamToJetson)")
        print(f"  POST /suggest_snack - preferences + budget → snack suggestions (no order)\n")

        while running:
            ctx = current_context_from_mac
            session = session_tracker.update(ctx)
            if session and running:
                mins = session.duration_seconds / 60
                st = (
                    "LONG"
                    if session.duration_seconds >= config.LONG_SESSION_THRESHOLD
                    else "WARN"
                    if session.duration_seconds >= config.WARN_SESSION_THRESHOLD
                    else ""
                )
                print(
                    f"\r  {session.context.display_name[:50]} ... {mins:.1f}m {st}    ",
                    end="",
                    flush=True,
                )
            await asyncio.sleep(config.POLL_INTERVAL)

        await runner.cleanup()

    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        pass
    print("\nShutting down.")


def main():
    p = argparse.ArgumentParser(description="Jetson processor - receives activity + EEG from Mac via WebSocket")
    p.add_argument("--port", type=int, default=config.JETSON_WS_PORT, help="Port for HTTP + WebSocket")
    args = p.parse_args()
    run_processor(args.port)


if __name__ == "__main__":
    main()
    sys.exit(0)
