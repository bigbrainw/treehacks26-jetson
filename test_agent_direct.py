#!/usr/bin/env python3
"""Test agent.decide() directly (no processor)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent import MultiTurnAssistant

assistant = MultiTurnAssistant()
decision = assistant.decide(
    app_name="Chrome",
    window_title="Complex Paper — arXiv",
    context_type="website",
    duration_seconds=180.5,
    mental_state="stuck",
    recent_sessions=[],
    mental_state_metrics={
        "engagement": 0.32,
        "stress": 0.58,
        "relaxation": 0.42,
        "focus": 0.35,
    },
)

print("should_help:", decision.should_help)
print("message:", repr(decision.message))
print("reason:", decision.reason)
print()
if decision.message:
    print("PASS: Agent returned message")
else:
    print("FAIL: No message")
