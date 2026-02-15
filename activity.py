"""
Activity monitoring - frontmost app/window detection.

On macOS: use activity_mac.ActivityMonitor for real monitoring.
Fallback: MockActivityMonitor for testing on non-Mac or when Mac APIs unavailable.
"""

import sys
from typing import Optional

from activity_tracker import ActivityContext

# Try Mac-specific monitor first
if sys.platform == "darwin":
    try:
        from activity_mac import ActivityMonitor  # type: ignore
    except ImportError:
        ActivityMonitor = None  # type: ignore
else:
    ActivityMonitor = None  # type: ignore


class MockActivityMonitor:
    """Returns a static/dummy context. Use on non-Mac or for testing."""

    def __init__(self, poll_interval: float = 0.5):
        self.poll_interval = poll_interval
        self._static = ActivityContext(
            app_name="Mock",
            window_title="mock-window",
            context_type="app",
            context_id="mock::mock-window",
        )

    def get_current_activity(self) -> Optional[ActivityContext]:
        return self._static


# Export: use real ActivityMonitor on Mac if available, else Mock
if ActivityMonitor is not None:
    pass  # ActivityMonitor already set from activity_mac
else:
    ActivityMonitor = MockActivityMonitor  # type: ignore
