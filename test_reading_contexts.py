#!/usr/bin/env python3
"""
Test cases: user reading difficult content + time on page + EEG shows need for help.

Each case defines only: what the user is reading, and (optionally) how long they've been on it.
The program does the rest: prefetch (Bright Data), context handlers, agent. Prep comes from
the program—not hardcoded here.

Usage:
  python test_reading_contexts.py              # run all 5
  python test_reading_contexts.py --case 1    # run case 1
  python test_reading_contexts.py --list      # list cases
  python test_reading_contexts.py --long 30   # 30 sec on page before trigger
"""

import argparse
import sys
import tempfile
import time
from pathlib import Path

import config
config.DB_PATH = Path(tempfile.gettempdir()) / "focus_agent_reading_test.db"

from activity_tracker import ActivityContext
from agent import MultiTurnAssistant
from agent.context_handlers import ContextRouter
from storage import Storage
from time_tracker import SessionTracker, SessionEvent, SessionEventType


# 5 test cases: what the user is reading (content only; prep comes from the program)
READING_TEST_CASES = [
    {
        "id": 1,
        "name": "Technical academic paper",
        "ctx": ActivityContext(
            app_name="Firefox",
            window_title="Attention Is All You Need — arXiv",
            context_type="website",
            context_id="Firefox::arxiv.org/abs/1706.03762",
        ),
    },
    {
        "id": 2,
        "name": "University lecture notes",
        "ctx": ActivityContext(
            app_name="Chrome",
            window_title="CS224N Lecture 5 — Backpropagation and Neural Networks",
            context_type="website",
            context_id="Chrome::cs224n.stanford.edu",
        ),
    },
    {
        "id": 3,
        "name": "Complex research article",
        "ctx": ActivityContext(
            app_name="Safari",
            window_title="Understanding Large Language Models: A Survey — Nature Reviews",
            context_type="website",
            context_id="Safari::nature.com/articles",
        ),
    },
    {
        "id": 4,
        "name": "Technical documentation",
        "ctx": ActivityContext(
            app_name="Firefox",
            window_title="React Hooks — A Complete Guide | React Docs",
            context_type="website",
            context_id="Firefox://react.dev/docs",
        ),
    },
    {
        "id": 5,
        "name": "Philosophy / theory essay",
        "ctx": ActivityContext(
            app_name="Chrome",
            window_title="The Concept of Mind by Gilbert Ryle — Stanford Encyclopedia of Philosophy",
            context_type="website",
            context_id="Chrome://plato.stanford.edu",
        ),
    },
]


class DummyActivityMonitor:
    """Fixed context for testing."""

    def __init__(self, context: ActivityContext):
        self._context = context
        self._callbacks = []

    def on_context_change(self, cb):
        self._callbacks.append(cb)

    def get_current_activity(self) -> ActivityContext:
        return self._context


def run_case(case: dict, mental_state: str = "stuck", warn: int = 20, long: int = 30) -> tuple[bool, str | None]:
    """Run a single reading-context test case. Returns (success, agent_message or None)."""
    ctx = case["ctx"]
    storage = Storage(config.DB_PATH)
    assistant = MultiTurnAssistant(
        api_key=config.ANTHROPIC_API_KEY,
        model=config.ANTHROPIC_MODEL,
    )
    session_tracker = SessionTracker(
        warn_threshold_sec=warn,
        long_threshold_sec=long,
        follow_up_interval_sec=max(30, long // 2),
    )
    monitor = DummyActivityMonitor(ctx)

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

    agent_message = []

    def on_session_event(event: SessionEvent):
        if event.event_type in (SessionEventType.LONG_THRESHOLD, SessionEventType.FOLLOW_UP):
            recent = storage.get_recent_sessions(limit=8)
            # Agent SDK does WebSearch/WebFetch when stuck
            prepared = None
            kwargs = dict(
                app_name=ctx.app_name,
                window_title=ctx.window_title,
                context_type=ctx.context_type,
                duration_seconds=event.duration_seconds,
                mental_state=mental_state,
                recent_sessions=recent,
                activity_context=ctx,
                prepared_resources=prepared,
            )
            kwargs["user_feedback"] = (
                "(Still on this - try a different angle)"
                if event.event_type == SessionEventType.FOLLOW_UP else None
            )
            decision = assistant.decide(**kwargs)
            if decision.should_help and decision.message:
                agent_message.append(decision.message)

    monitor.on_context_change(on_context_change)
    session_tracker.on_session_event(on_session_event)

    # Run for long enough to hit LONG_THRESHOLD
    start = time.time()
    while time.time() - start < long + 2:
        c = monitor.get_current_activity()
        session_tracker.update(c)
        time.sleep(0.3)

    msg = agent_message[0] if agent_message else None
    return len(agent_message) > 0, msg


def run_context_handler_test(case: dict) -> tuple[str, bool]:
    """Verify the context router picks the right handler and produces enriched output."""
    ctx = case["ctx"]
    router = ContextRouter()
    handler, enriched = router.route(ctx)
    ok = handler.name in ("browser", "default") and len(enriched.extra_for_prompt) > 0
    return enriched.extra_for_prompt[:200], ok


def main():
    p = argparse.ArgumentParser(description="Test reading-context scenarios (lectures, papers, articles)")
    p.add_argument("--list", action="store_true", help="List test cases and exit")
    p.add_argument("--case", type=int, choices=[1, 2, 3, 4, 5], help="Run specific case only")
    p.add_argument("--mental", default="stuck", choices=["stuck", "distracted", "focused"],
                   help="Mental state (default: stuck)")
    p.add_argument("--warn", type=int, default=20, help="Warn threshold (sec)")
    p.add_argument("--long", type=int, default=30, help="Long threshold (sec) — e.g. 30s on page, EEG=stuck")
    p.add_argument("--context-only", action="store_true",
                   help="Only test context handler enrichment, skip full agent run")
    args = p.parse_args()

    cases = READING_TEST_CASES if args.case is None else [c for c in READING_TEST_CASES if c["id"] == args.case]

    if args.list:
        print("\n--- Reading Context Test Cases ---")
        print("  Each case: what user is reading. Prep from program (Bright Data).")
        print("  Scenario: user stays on page N sec, EEG shows need help.\n")
        for c in READING_TEST_CASES:
            print(f"  {c['id']}. {c['name']}: {c['ctx'].display_name}")
        print()
        return 0

    print("\n--- Reading Context Tests (lectures, papers, articles) ---")
    print(f"  Mental state: {args.mental}")
    print(f"  Thresholds: warn={args.warn}s, long={args.long}s")
    if args.context_only:
        print("  Mode: context handler only (no agent run)\n")
    else:
        print("  Mode: full agent run\n")

    passed = 0
    failed = 0

    for case in cases:
        print(f"Case {case['id']}: {case['name']}")
        print(f"  Reading: {case['ctx'].display_name}")

        if args.context_only:
            extra, ok = run_context_handler_test(case)
            print(f"  Enriched (preview): {extra[:120]}...")
            if ok:
                print("  PASS: Context handler produced enrichment")
                passed += 1
            else:
                print("  FAIL: Context handler issue")
                failed += 1
        else:
            try:
                ok, msg = run_case(case, args.mental, args.warn, args.long)
                if ok:
                    print("  PASS: Agent responded with help")
                    if msg:
                        print(f"  >>> {msg}")
                    passed += 1
                else:
                    print("  FAIL: Agent did not respond (check ANTHROPIC_API_KEY)")
                    failed += 1
            except Exception as e:
                print(f"  ERROR: {e}")
                failed += 1
        print()

    print("--- Summary ---")
    print(f"  Passed: {passed}, Failed: {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
