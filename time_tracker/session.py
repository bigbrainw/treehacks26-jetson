"""Tracks time spent on each context and emits events when thresholds are exceeded."""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from activity_tracker import ActivityContext


class SessionEventType(Enum):
    """Types of session events for EEG / mental state triggers."""
    CONTEXT_CHANGED = "context_changed"
    WARN_THRESHOLD = "warn_threshold"
    LONG_THRESHOLD = "long_threshold"
    FOLLOW_UP = "follow_up"  # Still on same page - multi-turn follow-up


@dataclass
class SessionEvent:
    """Event emitted when session state changes."""
    event_type: SessionEventType
    context: ActivityContext
    duration_seconds: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class ActiveSession:
    """Represents an ongoing session on a single context."""
    context: ActivityContext
    started_at: float
    last_activity_at: float

    @property
    def duration_seconds(self) -> float:
        return time.time() - self.started_at


class SessionTracker:
    """
    Tracks how long the user has been on the same page/app/file.
    Emits events when duration exceeds thresholds (for EEG mental state checking).
    """

    def __init__(
        self,
        warn_threshold_sec: float = 300,
        long_threshold_sec: float = 600,
        follow_up_interval_sec: float = 90,  # Emit follow_up every N sec after long
    ):
        self.warn_threshold = warn_threshold_sec
        self.long_threshold = long_threshold_sec
        self.follow_up_interval = follow_up_interval_sec
        self._current_session: Optional[ActiveSession] = None
        self._warn_emitted: bool = False
        self._long_emitted: bool = False
        self._last_follow_up_at: float = 0
        self._event_callbacks: list[Callable[[SessionEvent], None]] = []

    def on_session_event(self, callback: Callable[[SessionEvent], None]):
        """Register callback for session events (e.g. EEG trigger)."""
        self._event_callbacks.append(callback)

    def _emit(self, event: SessionEvent):
        for cb in self._event_callbacks:
            try:
                cb(event)
            except Exception:
                pass

    def update(self, context: Optional[ActivityContext]) -> Optional[ActiveSession]:
        """
        Update with current activity context.
        Returns current active session if user is still on same context.
        """
        now = time.time()

        if context is None:
            return self._current_session

        # Check if context changed
        if self._current_session is None or self._current_session.context.context_id != context.context_id:
            self._last_follow_up_at = 0
            self._current_session = ActiveSession(
                context=context,
                started_at=now,
                last_activity_at=now,
            )
            self._warn_emitted = False
            self._long_emitted = False
            self._emit(SessionEvent(
                event_type=SessionEventType.CONTEXT_CHANGED,
                context=context,
                duration_seconds=0,
            ))
            return self._current_session

        # Same context - update and check thresholds
        self._current_session.last_activity_at = now
        duration = self._current_session.duration_seconds

        if duration >= self.long_threshold and not self._long_emitted:
            self._long_emitted = True
            self._last_follow_up_at = now
            self._emit(SessionEvent(
                event_type=SessionEventType.LONG_THRESHOLD,
                context=context,
                duration_seconds=duration,
            ))
        elif duration >= self.long_threshold and (now - self._last_follow_up_at) >= self.follow_up_interval:
            self._last_follow_up_at = now
            self._emit(SessionEvent(
                event_type=SessionEventType.FOLLOW_UP,
                context=context,
                duration_seconds=duration,
            ))
        elif duration >= self.warn_threshold and not self._warn_emitted:
            self._warn_emitted = True
            self._emit(SessionEvent(
                event_type=SessionEventType.WARN_THRESHOLD,
                context=context,
                duration_seconds=duration,
            ))

        return self._current_session

    def get_current_session(self) -> Optional[ActiveSession]:
        """Get the current session (same page for a while)."""
        return self._current_session
