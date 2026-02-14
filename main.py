#!/usr/bin/env python3
"""
Focus Agent - Tracks activity, time-on-task, and mental state (EEG).
Multi-turn LLM agent: asks questions, adjusts help based on feedback, follows up.
"""

import signal
import sys
import time

import config
from activity_tracker import ActivityMonitor, ActivityContext
from agent import FocusAssistant, MultiTurnAssistant
from eeg import EEGBridge, EmotivCortexClient
from storage import Storage
from time_tracker import SessionTracker, SessionEvent, SessionEventType


def main():
    storage = Storage(config.DB_PATH)
    AssistantClass = MultiTurnAssistant if config.MULTITURN_AGENT else FocusAssistant
    assistant = AssistantClass(
        base_url=config.OLLAMA_BASE_URL,
        model=config.OLLAMA_MODEL,
    )
    monitor = ActivityMonitor(poll_interval=config.POLL_INTERVAL)
    session_tracker = SessionTracker(
        warn_threshold_sec=config.WARN_SESSION_THRESHOLD,
        long_threshold_sec=config.LONG_SESSION_THRESHOLD,
        follow_up_interval_sec=config.FOLLOW_UP_INTERVAL,
    )
    eeg_bridge = EEGBridge()

    emotiv_client = None
    if config.EMOTIV_CLIENT_ID and config.EMOTIV_CLIENT_SECRET:
        try:
            emotiv_client = EmotivCortexClient(
                client_id=config.EMOTIV_CLIENT_ID,
                client_secret=config.EMOTIV_CLIENT_SECRET,
                on_metrics=eeg_bridge.store_metrics,
            )
            emotiv_client.connect()
            eeg_bridge.set_emotiv_client(emotiv_client)
            print("  Emotiv Cortex: connecting...")
        except Exception as e:
            print(f"  Emotiv: {e}")

    current_session_id = None
    prev_context_id = None

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

    def on_session_event(event: SessionEvent):
        print(f"  [Event] {event.event_type.value}: {event.context.display_name} ({event.duration_seconds:.0f}s)")
        if event.event_type in (SessionEventType.LONG_THRESHOLD, SessionEventType.FOLLOW_UP):
            print("    -> Triggering mental state check (Emotiv metrics)")
            eeg_bridge.handle_session_event(event, storage)

    def on_mental_state(ctx: ActivityContext, duration: float, state, *, is_follow_up: bool = False):
        state_val = state.value
        print(f"  [Mental State] {state_val}: {ctx.display_name} (on page {duration:.0f}s)")
        if state_val in ("stuck", "distracted", "unknown"):
            recent = storage.get_recent_sessions(limit=8)
            kwargs = dict(
                app_name=ctx.app_name,
                window_title=ctx.window_title,
                context_type=ctx.context_type,
                duration_seconds=duration,
                mental_state=state_val,
                recent_sessions=recent,
                activity_context=ctx,
            )
            if isinstance(assistant, MultiTurnAssistant):
                kwargs["user_feedback"] = (
                    "(Still on this - try a different angle)" if is_follow_up else None
                )
            decision = assistant.decide(**kwargs)
            if decision.should_help and decision.message:
                print(f"\n  >>> Agent: {decision.message}")

    monitor.on_context_change(on_context_change)
    session_tracker.on_session_event(on_session_event)
    eeg_bridge.on_mental_state_detected(on_mental_state)

    running = True
    def stop(_=None, __=None):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    print("Focus Agent running. Multi-turn: ask → adjust → follow up.")
    print(f"  Warn={config.WARN_SESSION_THRESHOLD}s, Long={config.LONG_SESSION_THRESHOLD}s, Follow-up every 90s")

    while running:
        ctx = monitor.get_current_activity()
        session = session_tracker.update(ctx)
        if session and running:
            mins = session.duration_seconds / 60
            st = "LONG" if session.duration_seconds >= config.LONG_SESSION_THRESHOLD else "WARN" if session.duration_seconds >= config.WARN_SESSION_THRESHOLD else ""
            print(f"\r  {session.context.display_name[:50]} ... {mins:.1f}m {st}    ", end="", flush=True)
        time.sleep(config.POLL_INTERVAL)

    if emotiv_client:
        emotiv_client.close()
    print("\nShutting down.")


if __name__ == "__main__":
    main()
    sys.exit(0)
