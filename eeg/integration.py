"""
EEG Integration - Emotiv Cortex API + performance metrics.

When SessionTracker emits LONG_THRESHOLD, we use Emotiv performance metrics
(engagement, attention, stress) to infer: focused | stuck | distracted.
"""

import time
from enum import Enum
from typing import Callable, Optional

from activity_tracker import ActivityContext
from time_tracker import SessionEvent, SessionEventType


class MentalState(Enum):
    """Inferred mental state from Emotiv EEG metrics."""
    FOCUSED = "focused"       # Actively working
    STUCK = "stuck"           # Confused, blocked, needing help
    DISTRACTED = "distracted" # Mind wandering
    UNKNOWN = "unknown"       # No EEG / not yet classified


def _metrics_to_state(metrics: dict) -> MentalState:
    """
    Map Emotiv performance metrics to mental state.
    EPOC/Insight/Flex: eng, attention, str, rel
    MN8: attention, cognitiveStress
    """
    def v(key: str):
        val = metrics.get(key)
        if val is None or (isinstance(val, float) and (val != val)):  # NaN
            return None
        return float(val) if isinstance(val, (int, float)) else None

    eng = v("eng")
    attention = v("attention")
    str_val = v("str")
    rel = v("rel")
    stress = v("cognitiveStress")

    # MN8: attention + cognitiveStress
    if attention is not None and stress is not None:
        if attention > 0.5 and stress < 0.5:
            return MentalState.FOCUSED
        if attention > 0.4 and stress > 0.5:
            return MentalState.STUCK
        if attention < 0.4:
            return MentalState.DISTRACTED
        return MentalState.UNKNOWN

    # EPOC/Insight/Flex: eng, attention, str, rel
    if eng is None and attention is None:
        return MentalState.UNKNOWN

    att = attention if attention is not None else eng
    if att is None:
        return MentalState.UNKNOWN

    # Focused: high engagement/attention, low stress
    if att > 0.5 and (str_val is None or str_val < 0.5) and (rel is None or rel >= 0.3):
        return MentalState.FOCUSED

    # Stuck: high stress, moderate attention (trying but frustrated)
    if str_val is not None and str_val > 0.5 and att > 0.3:
        return MentalState.STUCK

    # Distracted: low engagement/attention
    if att < 0.35:
        return MentalState.DISTRACTED

    return MentalState.UNKNOWN


class EEGBridge:
    """
    Bridge between activity tracking and Emotiv Cortex EEG.
    On LONG_THRESHOLD: samples recent metrics and classifies mental state.
    """

    def __init__(self, emotiv_client=None):
        self._state_callbacks: list[Callable[[ActivityContext, float, MentalState], None]] = []
        self._emotiv = emotiv_client
        self._recent_metrics: list[tuple[float, dict]] = []
        self._max_samples = 15  # ~30s at 2 Hz, 5s at 0.1 Hz basic

    def store_metrics(self, metrics: dict):
        """Call from Emotiv client's on_metrics to buffer metrics for classification."""
        self._recent_metrics.append((time.time(), metrics.copy()))
        self._recent_metrics = self._recent_metrics[-self._max_samples:]

    def set_emotiv_client(self, client):
        """Set Emotiv Cortex client (streaming 'met')."""
        self._emotiv = client

    def get_last_metrics(self) -> Optional[dict]:
        """Return most recent EEG metrics for agent context (engagement, stress, etc.)."""
        if not self._recent_metrics:
            return None
        _, m = self._recent_metrics[-1]
        return m.copy() if m else None

    def on_mental_state_detected(
        self,
        callback: Callable[..., None],
    ):
        """Register callback for when we determine user's mental state."""
        self._state_callbacks.append(callback)

    def handle_session_event(self, event: SessionEvent, storage=None):
        """
        Called when SessionTracker emits an event.
        LONG_THRESHOLD or FOLLOW_UP: classify mental state and trigger agent.
        """
        if event.event_type not in (SessionEventType.LONG_THRESHOLD, SessionEventType.FOLLOW_UP):
            return

        state = MentalState.UNKNOWN
        if self._recent_metrics:
            # Use most recent non-null metrics
            for ts, m in reversed(self._recent_metrics):
                s = _metrics_to_state(m)
                if s != MentalState.UNKNOWN:
                    state = s
                    break

        is_follow_up = event.event_type == SessionEventType.FOLLOW_UP
        self._emit_state(event.context, event.duration_seconds, state, is_follow_up=is_follow_up)

        if storage:
            storage.record_eeg_trigger(
                session_id=None,
                event_type="long_threshold",
                duration_at_trigger=event.duration_seconds,
                mental_state=state.value if state != MentalState.UNKNOWN else None,
            )

    def _emit_state(
        self,
        context: ActivityContext,
        duration: float,
        state: MentalState,
        *,
        is_follow_up: bool = False,
    ):
        for cb in self._state_callbacks:
            try:
                cb(context, duration, state, is_follow_up=is_follow_up)
            except TypeError:
                cb(context, duration, state)
