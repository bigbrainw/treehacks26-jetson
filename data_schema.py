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
    reading_section: Optional[str] = None  # Section of paper/doc user is reading (from monitoring)
    page_content: Optional[str] = None  # Extracted PDF page text (Mac only, for stuck-point analysis)

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
            reading_section=d.get("reading_section"),
            page_content=d.get("page_content"),
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
            reading_section=self.reading_section,
            page_content=self.page_content,
        )

    @classmethod
    def from_activity_context(cls, ctx: "ActivityContext") -> "ActivitySnapshot":
        """Create from ActivityContext (e.g. from received payload)."""
        return cls(
            app_name=ctx.app_name,
            window_title=ctx.window_title,
            context_type=ctx.context_type,
            context_id=ctx.context_id,
            detected_at=ctx.detected_at,
            reading_section=getattr(ctx, "reading_section", None),
            page_content=getattr(ctx, "page_content", None),
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
class MentalCommandSnapshot:
    """Mental command from Emotiv com stream (act, pow)."""

    action: str  # e.g. push, pull, lift
    power: float  # 0-1
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"action": self.action, "power": self.power, "timestamp": self.timestamp}

    @classmethod
    def from_dict(cls, d: dict) -> "MentalCommandSnapshot":
        return cls(
            action=d.get("action", "neutral"),
            power=float(d.get("power", 0)),
            timestamp=d.get("timestamp", time.time()),
        )


@dataclass
class CollectorPayload:
    """
    Single payload from Mac collector to Jetson processor.
    Can contain activity, EEG metrics, mental command, or any combination.
    """

    type: str  # "activity" | "eeg" | "mental_command" | "heartbeat"
    activity: Optional[ActivitySnapshot] = None
    eeg: Optional[EEGMetricsSnapshot] = None
    mental_command: Optional[MentalCommandSnapshot] = None
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
        if self.mental_command:
            d["mental_command"] = self.mental_command.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CollectorPayload":
        activity = None
        if "activity" in d:
            activity = ActivitySnapshot.from_dict(d["activity"])

        eeg = None
        if "eeg" in d:
            eeg = EEGMetricsSnapshot.from_dict(d["eeg"])

        mental_command = None
        if "mental_command" in d:
            mental_command = MentalCommandSnapshot.from_dict(d["mental_command"])

        return cls(
            type=d.get("type", "unknown"),
            activity=activity,
            eeg=eeg,
            mental_command=mental_command,
            timestamp=d.get("timestamp", time.time()),
        )
