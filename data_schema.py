"""
Shared data schema for collector (Mac) <-> processor (Jetson) communication.

All structures are JSON-serializable for transport over WebSocket/HTTP.
"""

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Activity (from Mac - monitoring)
# ---------------------------------------------------------------------------


@dataclass
class ActivitySnapshot:
    """Serializable activity context - sent from collector to processor."""

    app_name: str
    window_title: str
    context_type: str  # "app" | "website" | "file" | "browser" | "terminal"
    context_id: str
    detected_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ActivitySnapshot":
        return cls(
            app_name=d.get("app_name", ""),
            window_title=d.get("window_title", ""),
            context_type=d.get("context_type", "app"),
            context_id=d.get("context_id", ""),
            detected_at=d.get("detected_at", time.time()),
        )

    def to_activity_context(self) -> "ActivityContext":
        """Convert to ActivityContext for processor-side logic."""
        from activity_tracker import ActivityContext
        return ActivityContext(
            app_name=self.app_name,
            window_title=self.window_title,
            context_type=self.context_type,
            context_id=self.context_id,
            detected_at=self.detected_at,
        )

    @classmethod
    def from_activity_context(cls, ctx: "ActivityContext") -> "ActivitySnapshot":
        """Create from ActivityContext (e.g. from collector's ActivityMonitor)."""
        return cls(
            app_name=ctx.app_name,
            window_title=ctx.window_title,
            context_type=ctx.context_type,
            context_id=ctx.context_id,
            detected_at=ctx.detected_at,
        )


# ---------------------------------------------------------------------------
# EEG metrics (from Mac - Emotiv)
# ---------------------------------------------------------------------------


@dataclass
class EEGMetricsSnapshot:
    """Serializable EEG metrics - sent from collector to processor."""

    metrics: dict  # e.g. eng, attention, str, rel, cognitiveStress
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"metrics": self.metrics, "timestamp": self.timestamp}

    @classmethod
    def from_dict(cls, d: dict) -> "EEGMetricsSnapshot":
        return cls(
            metrics=d.get("metrics", {}),
            timestamp=d.get("timestamp", time.time()),
        )


# ---------------------------------------------------------------------------
# Combined payload (single message to Jetson)
# ---------------------------------------------------------------------------


@dataclass
class CollectorPayload:
    """
    Single payload from Mac collector to Jetson processor.
    Can contain activity, EEG metrics, or both.
    """

    type: str  # "activity" | "eeg" | "heartbeat"
    activity: Optional[ActivitySnapshot] = None
    eeg: Optional[EEGMetricsSnapshot] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "type": self.type,
            "timestamp": self.timestamp,
        }
        if self.activity:
            d["activity"] = self.activity.to_dict()
        if self.eeg:
            d["eeg"] = self.eeg.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CollectorPayload":
        activity = None
        if "activity" in d:
            activity = ActivitySnapshot.from_dict(d["activity"])

        eeg = None
        if "eeg" in d:
            eeg = EEGMetricsSnapshot.from_dict(d["eeg"])

        return cls(
            type=d.get("type", "unknown"),
            activity=activity,
            eeg=eeg,
            timestamp=d.get("timestamp", time.time()),
        )
