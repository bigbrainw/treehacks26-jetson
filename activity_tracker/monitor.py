"""Activity monitor - tracks app, website, file in use. Linux (X11) and macOS supported."""

import platform
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .linux import get_active_window_x11, infer_context_type as infer_context_type_linux, WindowInfo


@dataclass
class ActivityContext:
    """Represents the user's current activity context."""

    app_name: str
    window_title: str
    context_type: str  # "app" | "website" | "file" | "browser" | "terminal"
    context_id: str
    detected_at: float = field(default_factory=time.time)

    @property
    def display_name(self) -> str:
        """Human-readable description."""
        if self.context_type == "website" and self.window_title:
            return f"{self.app_name}: {self.window_title[:60]}..."
        if self.window_title:
            return f"{self.app_name} - {self.window_title[:60]}"
        return self.app_name


class ActivityMonitor:
    """
    Monitors what the user is currently doing (app, website, file).
    Polls periodically and detects context changes.
    """

    def __init__(self, poll_interval: float = 2.0):
        self.poll_interval = poll_interval
        self._last_context: Optional[ActivityContext] = None
        self._on_change_callbacks: list[Callable[[ActivityContext, Optional[ActivityContext]]]] = []

    def on_context_change(self, callback: Callable[[ActivityContext, Optional[ActivityContext]], None]):
        """Register callback for when user switches context."""
        self._on_change_callbacks.append(callback)

    def get_current_activity(self) -> Optional[ActivityContext]:
        """Get the current activity context. Returns None if detection fails."""
        system = platform.system()
        if system == "Linux":
            winfo = get_active_window_x11()
            infer_context_type = infer_context_type_linux
        elif system == "Darwin":
            from .macos import get_active_window_macos, infer_context_type as infer_context_type_macos
            winfo = get_active_window_macos()
            infer_context_type = infer_context_type_macos
        else:
            return self._last_context

        if not winfo:
            return self._last_context

        context_type = infer_context_type(winfo.app_name, winfo.window_title)
        ctx = ActivityContext(
            app_name=winfo.app_name,
            window_title=winfo.window_title,
            context_type=context_type,
            context_id=winfo.context_id,
        )

        # Check for change
        if self._last_context is None or self._last_context.context_id != ctx.context_id:
            prev = self._last_context
            self._last_context = ctx
            for cb in self._on_change_callbacks:
                try:
                    cb(ctx, prev)
                except Exception:
                    pass

        return ctx

    def get_last_context(self) -> Optional[ActivityContext]:
        """Return the last known context (for time calculations)."""
        return self._last_context
