#!/usr/bin/env python3
"""
Test the Focus Agent with dummy data - no X11 or Emotiv required.

Simulates:
- User on same "file" for ~8 seconds
- Warn at 2s, Long at 4s (accelerated thresholds)
- Dummy Emotiv metrics (focused, stuck, distracted) to test classification

Run: python test_agent.py
"""

import sys
import tempfile
import threading
import time
from pathlib import Path

# Use temp DB for test
import config
config.DB_PATH = Path(tempfile.gettempdir()) / "focus_agent_test.db"

from activity_tracker import ActivityContext
from agent import FocusAssistant
from eeg import EEGBridge
from storage import Storage
from time_tracker import SessionTracker, SessionEvent, SessionEventType


class DummyActivityMonitor:
    """Returns a fixed context to simulate user on same page."""

    def __init__(self, context: ActivityContext):
        self._context = context
        self._callbacks = []

    def on_context_change(self, callback):
        self._callbacks.append(callback)

    def get_current_activity(self) -> ActivityContext:
        return self._context


class SwitchingActivityMonitor:
    """
    Returns different contexts at different times to simulate task switching.
    schedule: list of (switch_at_seconds, ActivityContext)
    """

    def __init__(self, schedule: list[tuple[float, ActivityContext]], start_time: float | None = None):
        self._schedule = sorted(schedule, key=lambda x: x[0])
        self._start = start_time or time.time()
        self._callbacks = []
        self._last_context: ActivityContext | None = None

    def on_context_change(self, callback):
        self._callbacks.append(callback)

    def get_current_activity(self) -> ActivityContext:
        elapsed = time.time() - self._start
        ctx = self._schedule[-1][1]
        for switch_at, c in self._schedule:
            if elapsed >= switch_at:
                ctx = c
        # Detect change and fire callback (same logic as real monitor)
        if self._last_context is None or self._last_context.context_id != ctx.context_id:
            prev = self._last_context
            self._last_context = ctx
            for cb in self._callbacks:
                try:
                    cb(ctx, prev)
                except Exception:
                    pass
        return ctx


def run_test(mental_state_scenario: str = "focused"):
    """
    Run agent test. mental_state_scenario: "focused" | "stuck" | "distracted"
    """
    # Dummy metrics to inject (Emotiv "met" stream format)
    DUMMY_METRICS = {
        "focused": {"eng": 0.75, "attention": 0.8, "str": 0.2, "rel": 0.5},
        "stuck": {"eng": 0.4, "attention": 0.5, "str": 0.8, "rel": 0.1},
        "distracted": {"eng": 0.2, "attention": 0.25, "str": 0.3, "rel": 0.6},
    }
    metrics = DUMMY_METRICS.get(mental_state_scenario, DUMMY_METRICS["focused"])

    # Fixed context - user "stuck" on this file
    dummy_ctx = ActivityContext(
        app_name="Cursor",
        window_title="test_agent.py - treehacks26",
        context_type="file",
        context_id="Cursor::test_agent.py",
    )

    # Short thresholds: warn 2s, long 4s
    WARN = 2
    LONG = 4
    POLL = 0.5

    storage = Storage(config.DB_PATH)
    monitor = DummyActivityMonitor(dummy_ctx)
    session_tracker = SessionTracker(warn_threshold_sec=WARN, long_threshold_sec=LONG)
    eeg_bridge = EEGBridge()

    events_seen = []
    mental_state_seen = []

    def on_context_change(new_ctx, prev_ctx):
        events_seen.append(("context_change", new_ctx.display_name))
        storage.start_session(new_ctx)

    def on_session_event(event: SessionEvent):
        events_seen.append((event.event_type.value, event.context.display_name, event.duration_seconds))
        if event.event_type == SessionEventType.LONG_THRESHOLD:
            eeg_bridge.handle_session_event(event, storage)

    def on_mental_state(ctx, duration, state):
        mental_state_seen.append((state.value, ctx.display_name, duration))
        # Agent decides whether to help (runs fallback when no API key)
        assistant = FocusAssistant(
            api_key=config.ANTHROPIC_API_KEY,
            model=config.ANTHROPIC_MODEL,
        )
        decision = assistant.decide(
            ctx.app_name, ctx.window_title, ctx.context_type,
            duration, state.value, storage.get_recent_sessions(5),
            activity_context=ctx,
        )
        if decision.should_help and decision.message:
            mental_state_seen.append(("agent_feedback", decision.message[:60],))

    monitor.on_context_change(on_context_change)
    session_tracker.on_session_event(on_session_event)
    eeg_bridge.on_mental_state_detected(on_mental_state)

    # Feed dummy metrics into bridge (simulates Emotiv stream)
    def inject_metrics():
        for _ in range(20):  # ~2 seconds worth at 0.1 Hz
            eeg_bridge.store_metrics(metrics.copy())
            time.sleep(0.1)

    inject_thread = threading.Thread(target=inject_metrics, daemon=True)
    inject_thread.start()
    time.sleep(0.5)  # Let some metrics accumulate

    print(f"\n--- Focus Agent Test (scenario: {mental_state_scenario}) ---")
    print(f"  Simulating: user on '{dummy_ctx.display_name}'")
    print(f"  Thresholds: warn={WARN}s, long={LONG}s")
    print(f"  Dummy metrics: eng={metrics.get('eng')}, attention={metrics.get('attention')}, stress={metrics.get('str')}")
    print()

    start = time.time()
    while time.time() - start < 8:
        ctx = monitor.get_current_activity()
        session = session_tracker.update(ctx)
        if session:
            duration = session.duration_seconds
            status = "LONG" if duration >= LONG else "WARN" if duration >= WARN else ""
            print(f"\r  [{duration:.1f}s] {dummy_ctx.display_name[:40]} {status}    ", end="", flush=True)
        time.sleep(POLL)

    print("\n")
    print("--- Results ---")
    for ev in events_seen:
        if len(ev) == 3:
            print(f"  Event: {ev[0]} | {ev[1]} ({ev[2]:.1f}s)")
        else:
            print(f"  Event: {ev[0]} | {ev[1]}")
    for ms in mental_state_seen:
        if len(ms) == 3:
            print(f"  Mental State: {ms[0]} | {ms[1]} (duration {ms[2]:.1f}s)")
        else:
            print(f"  Agent: {ms[1]}")

    # Verify expected behavior
    event_types = [e[0] for e in events_seen]
    ok = (
        "warn_threshold" in event_types
        and "long_threshold" in event_types
        and len(mental_state_seen) >= 1
    )
    if ok:
        classified = mental_state_seen[0][0]
        expected = mental_state_scenario
        if classified == expected:
            print(f"\n  PASS: Classified as '{classified}' (expected '{expected}')")
        else:
            print(f"\n  Mismatch: got '{classified}', expected '{expected}'")
    else:
        print("\n  FAIL: Did not see warn + long + mental state")

    return ok


def run_switch_test():
    """
    Test that the agent detects task switching.
    User switches: Cursor (file) -> Firefox (github) -> Cursor (different file)
    """
    WARN = 2
    LONG = 4
    POLL = 0.5

    ctx_cursor_a = ActivityContext("Cursor", "main.py - treehacks26", "file", "Cursor::main.py")
    ctx_firefox = ActivityContext("Firefox", "github.com/...", "website", "Firefox::github.com")
    ctx_cursor_b = ActivityContext("Cursor", "config.py - treehacks26", "file", "Cursor::config.py")

    # Switch at 2.5s and 5.5s
    schedule = [
        (0, ctx_cursor_a),
        (2.5, ctx_firefox),
        (5.5, ctx_cursor_b),
    ]
    monitor = SwitchingActivityMonitor(schedule)
    storage = Storage(config.DB_PATH)
    session_tracker = SessionTracker(warn_threshold_sec=WARN, long_threshold_sec=LONG)
    eeg_bridge = EEGBridge()

    events_seen = []
    current_session_id = None

    def on_context_change(new_ctx, prev_ctx):
        nonlocal current_session_id
        if current_session_id:
            prev_session = session_tracker.get_current_session()
            duration = prev_session.duration_seconds if prev_session else 0
            storage.end_session(current_session_id, duration)
        current_session_id = storage.start_session(new_ctx)
        events_seen.append(("context_change", new_ctx.display_name, None))

    def on_session_event(event: SessionEvent):
        events_seen.append((event.event_type.value, event.context.display_name, event.duration_seconds))

    monitor.on_context_change(on_context_change)
    session_tracker.on_session_event(on_session_event)

    # Dummy metrics for any long-threshold (we may or may not hit it)
    def inject_metrics():
        m = {"eng": 0.7, "attention": 0.75, "str": 0.2, "rel": 0.5}
        for _ in range(25):
            eeg_bridge.store_metrics(m.copy())
            time.sleep(0.1)

    threading.Thread(target=inject_metrics, daemon=True).start()
    time.sleep(0.3)

    print("\n--- Focus Agent Test: Task Switching ---")
    print("  Schedule: Cursor (main.py) -> Firefox (github) -> Cursor (config.py)")
    print("  Switch at 2.5s, 5.5s | Warn=2s, Long=4s")
    print()

    start = time.time()
    while time.time() - start < 9:
        ctx = monitor.get_current_activity()
        session = session_tracker.update(ctx)
        if session:
            d = session.duration_seconds
            st = "LONG" if d >= LONG else "WARN" if d >= WARN else ""
            print(f"\r  [{time.time()-start:.1f}s] {ctx.display_name[:45]} ... {d:.1f}s {st}    ", end="", flush=True)
        time.sleep(POLL)

    print("\n")
    print("--- Results ---")
    context_changes = [e for e in events_seen if e[0] == "context_change"]
    warns = [e for e in events_seen if e[0] == "warn_threshold"]
    longs = [e for e in events_seen if e[0] == "long_threshold"]

    for ev in events_seen:
        if ev[2] is not None:
            print(f"  {ev[0]}: {ev[1]} ({ev[2]:.1f}s)")
        else:
            print(f"  {ev[0]}: {ev[1]}")

    # Verify: 3 context changes (initial + 2 switches), session resets on each switch
    ok = (
        len(context_changes) >= 3
        and context_changes[0][1] != context_changes[1][1]
        and context_changes[1][1] != context_changes[2][1]
    )
    if ok:
        print(f"\n  PASS: Detected {len(context_changes)} context switches")
        print(f"        Cursor (main) -> Firefox (github) -> Cursor (config)")
    else:
        print(f"\n  FAIL: Expected 3+ context changes, got {len(context_changes)}")
        print(f"        {[e[1] for e in context_changes]}")

    return ok


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "switch":
        ok = run_switch_test()
    else:
        scenario = sys.argv[1] if len(sys.argv) > 1 else "focused"
        ok = run_test(scenario)
    sys.exit(0 if ok else 1)
