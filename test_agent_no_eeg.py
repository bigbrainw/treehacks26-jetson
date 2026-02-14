#!/usr/bin/env python3
"""
Test the full agent pipeline WITHOUT EEG data.

Includes: activity tracking, time-on-task, context handlers (code/browser/terminal),
web reader (Firefox URL + content), multi-turn agent (Ollama).
Skips: Emotiv, EEG metrics, mental-state classification.

Use when you want to test the agent with real activity and Ollama, but no headset.

Usage:
  python test_agent_no_eeg.py                    # real activity, mental_state=stuck
  python test_agent_no_eeg.py --dummy           # dummy activity (no X11)
  python test_agent_no_eeg.py --mental distracted
  python test_agent_no_eeg.py --warn 3 --long 6  # custom thresholds (seconds)
"""

import argparse
import signal
import sys
import tempfile
import time
from pathlib import Path

import config
config.DB_PATH = Path(tempfile.gettempdir()) / "focus_agent_no_eeg_test.db"

from activity_tracker import ActivityMonitor, ActivityContext
from agent import MultiTurnAssistant
from storage import Storage
from time_tracker import SessionTracker, SessionEvent, SessionEventType


class DummyActivityMonitor:
    """Fixed context for headless/dummy mode."""

    def __init__(self, context: ActivityContext):
        self._context = context
        self._callbacks = []

    def on_context_change(self, cb):
        self._callbacks.append(cb)

    def get_current_activity(self) -> ActivityContext:
        return self._context


def run(args):
    storage = Storage(config.DB_PATH)
    assistant = MultiTurnAssistant(
        base_url=config.OLLAMA_BASE_URL,
        model=config.OLLAMA_MODEL,
    )
    WARN = args.warn
    LONG = args.long
    session_tracker = SessionTracker(
        warn_threshold_sec=WARN,
        long_threshold_sec=LONG,
        follow_up_interval_sec=max(30, LONG // 2),
    )
    mental_state = args.mental

    if args.dummy:
        ctx = ActivityContext(
            "Cursor", "main.py - treehacks26", "file", "Cursor::main.py",
        )
        monitor = DummyActivityMonitor(ctx)
        print("  Mode: dummy (fixed context)")
    else:
        monitor = ActivityMonitor(poll_interval=1.0)
        print("  Mode: real activity (X11)")

    current_session_id = None
    prev_context_id = None

    def on_context_change(new_ctx, prev_ctx):
        nonlocal current_session_id, prev_context_id
        if hasattr(assistant, "clear_conversation") and prev_context_id:
            assistant.clear_conversation(prev_context_id)
        if current_session_id:
            prev_session = session_tracker.get_current_session()
            duration = prev_session.duration_seconds if prev_session else 0
            storage.end_session(current_session_id, duration)
        current_session_id = storage.start_session(new_ctx)
        prev_context_id = new_ctx.context_id

    def on_session_event(event: SessionEvent):
        print(f"  [Event] {event.event_type.value}: {event.context.display_name} ({event.duration_seconds:.0f}s)")
        if event.event_type in (SessionEventType.LONG_THRESHOLD, SessionEventType.FOLLOW_UP):
            is_follow_up = event.event_type == SessionEventType.FOLLOW_UP
            ctx = event.context
            recent = storage.get_recent_sessions(limit=8)
            kwargs = dict(
                app_name=ctx.app_name,
                window_title=ctx.window_title,
                context_type=ctx.context_type,
                duration_seconds=event.duration_seconds,
                mental_state=mental_state,
                recent_sessions=recent,
                activity_context=ctx,
            )
            kwargs["user_feedback"] = (
                "(Still on this - try a different angle)" if is_follow_up else None
            )
            decision = assistant.decide(**kwargs)
            print(f"    -> Agent (no EEG, mental_state={mental_state}): ", end="")
            if decision.should_help and decision.message:
                print(f"{decision.message[:120]}...")
            else:
                print("(no message)")

    monitor.on_context_change(on_context_change)
    session_tracker.on_session_event(on_session_event)

    running = True
    def stop(_, __):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    print("\n--- Focus Agent Test (NO EEG) ---")
    print(f"  Mental state: {mental_state} (fixed, no Emotiv)")
    print(f"  Thresholds: warn={WARN}s, long={LONG}s")
    print("  Features: activity, context handlers, web reader, multi-turn Ollama")
    print("  Ctrl+C to stop\n")

    start = time.time()
    while running and (time.time() - start < args.duration):
        ctx = monitor.get_current_activity()
        session = session_tracker.update(ctx)
        if session:
            d = session.duration_seconds
            st = "LONG" if d >= LONG else "WARN" if d >= WARN else ""
            print(f"\r  {session.context.display_name[:55]} ... {d:.1f}s {st}   ", end="", flush=True)
        time.sleep(0.5)

    print("\n\nDone.")


def main():
    p = argparse.ArgumentParser(description="Test agent without EEG")
    p.add_argument("--dummy", action="store_true", help="Use dummy activity (no X11)")
    p.add_argument("--mental", default="stuck", choices=["stuck", "distracted", "focused", "unknown"],
                   help="Fixed mental state (default: stuck)")
    p.add_argument("--warn", type=int, default=3, help="Warn threshold (sec)")
    p.add_argument("--long", type=int, default=6, help="Long threshold (sec)")
    p.add_argument("--duration", type=int, default=60, help="Max run time (sec)")
    args = p.parse_args()
    run(args)


if __name__ == "__main__":
    main()
    sys.exit(0)
