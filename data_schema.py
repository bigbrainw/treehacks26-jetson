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
    duration_seconds: Optional[float] = None  # Time on this context (Mac app)

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
            duration_seconds=d.get("duration_seconds"),
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
# Mental state (derived from EEG - Mac app)
# ---------------------------------------------------------------------------


# Descriptions for agent prompt (0–1 scale)
_METRIC_DESCRIPTIONS = {
    "engagement": "immersion in activity",
    "stress": "emotional tension",
    "relaxation": "calm focus",
    "focus": "sustained attention",
    "excitement": "short-term arousal",
    "interest": "attraction / aversion to stimuli",
}


@dataclass
class MentalStateSnapshot:
    """EEG-derived mental state metrics. Agent interprets the combination."""

    engagement: Optional[float] = None   # 0–1 immersion
    stress: Optional[float] = None       # 0–1 emotional tension
    focus: Optional[float] = None       # 0–1 sustained attention
    relaxation: Optional[float] = None  # 0–1 calm focus
    excitement: Optional[float] = None  # 0–1 short-term arousal
    interest: Optional[float] = None    # 0–1 attraction to stimuli
    metrics: Optional[dict] = None      # Raw met stream (eng, str, rel, etc.)

    def to_dict(self) -> dict:
        d: dict = {}
        for k in ("engagement", "stress", "focus", "relaxation", "excitement", "interest"):
            v = getattr(self, k, None)
            if v is not None:
                d[k] = v
        if self.metrics is not None:
            d["metrics"] = self.metrics
        return d

    def format_for_agent(self) -> str:
        """Format metrics for agent prompt with descriptions."""
        parts = []
        for k, desc in _METRIC_DESCRIPTIONS.items():
            v = getattr(self, k, None)
            if v is not None:
                parts.append(f"  {k}={v:.2f} (0–1, {desc})")
        if self.metrics and not parts:
            for mk, mv in self.metrics.items():
                if isinstance(mv, (int, float)):
                    parts.append(f"  {mk}={float(mv):.2f}")
        if not parts:
            return ""
        return "EEG metrics (interpret the combination—e.g. high stress + low focus = stuck):\n" + "\n".join(parts)


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
    Can contain activity, EEG metrics, mental command, mental state, or any combination.
    """

    type: str  # "activity" | "eeg" | "mental_command" | "mental_state" | "heartbeat"
    activity: Optional[ActivitySnapshot] = None
    eeg: Optional[EEGMetricsSnapshot] = None
    mental_command: Optional[MentalCommandSnapshot] = None
    mental_state: Optional["MentalStateSnapshot"] = None
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
        if self.mental_state:
            d["mental_state"] = self.mental_state.to_dict()
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

        mental_state = None
        if "mental_state" in d:
            ms = d["mental_state"]
            if isinstance(ms, dict):
                mental_state = MentalStateSnapshot(
                    engagement=ms.get("engagement"),
                    stress=ms.get("stress"),
                    focus=ms.get("focus"),
                    relaxation=ms.get("relaxation"),
                    excitement=ms.get("excitement"),
                    interest=ms.get("interest"),
                    metrics=ms.get("metrics"),
                )

        return cls(
            type=d.get("type", "unknown"),
            activity=activity,
            eeg=eeg,
            mental_command=mental_command,
            mental_state=mental_state,
            timestamp=d.get("timestamp", time.time()),
        )


def parse_mac_payload(d: dict) -> "CollectorPayload | dict | None":
    """
    Parse Mac client payloads (activity, eeg, mental_state, reading_help, mental_command).
    Returns CollectorPayload for types we handle, or raw dict for reading_help.
    """
    msg_type = d.get("type", "")
    ts = d.get("timestamp", time.time())

    # Activity from top-level or nested
    def _act_from(dict_or_none):
        if not dict_or_none:
            return None
        a = dict_or_none
        return ActivitySnapshot(
            app_name=a.get("app_name", ""),
            window_title=a.get("window_title", ""),
            context_type=a.get("context_type", "app"),
            context_id=a.get("context_id", "") or f"{a.get('app_name','')}::{a.get('window_title','')[:50]}",
            reading_section=a.get("reading_section"),
            detected_at=ts,
        ) if (a.get("app_name") or a.get("window_title")) else None

    if msg_type == "activity":
        act = _act_from(d.get("activity"))
        return CollectorPayload(type="activity", activity=act, timestamp=ts) if act else None

    if msg_type == "eeg":
        eeg_data = d.get("eeg", {})
        metrics_raw = eeg_data.get("metrics", eeg_data) if isinstance(eeg_data, dict) else {}
        met = metrics_raw.get("met") if isinstance(metrics_raw, dict) else metrics_raw
        act = _act_from(d.get("activity")) or _act_from(eeg_data.get("activity"))
        return CollectorPayload(
            type="eeg",
            activity=act,
            eeg=EEGMetricsSnapshot(metrics={"met": met, "raw": metrics_raw}, timestamp=ts),
            timestamp=ts,
        )

    if msg_type == "mental_state":
        ms = d.get("mental_state", {})
        eng = ms.get("engagement")
        stress = ms.get("stress")
        rel = ms.get("relaxation")
        att = ms.get("focus")
        met = ms.get("metrics", {}).get("met") if isinstance(ms.get("metrics"), dict) else None
        metrics = {}
        if met is not None:
            metrics["met"] = met
        if eng is not None:
            metrics["eng"] = float(eng)
        if stress is not None:
            metrics["str"] = float(stress)
        if rel is not None:
            metrics["rel"] = float(rel)
        if att is not None:
            metrics["attention"] = float(att)
        if metrics:
            return CollectorPayload(
                type="eeg",
                eeg=EEGMetricsSnapshot(metrics=metrics, timestamp=ts),
                timestamp=ts,
            )
        return None

    if msg_type == "reading_help":
        act_data = d.get("activity", {})
        act = _act_from(act_data)
        duration = act_data.get("duration_seconds", 10.0) if isinstance(act_data, dict) else 10.0
        ms = d.get("mental_state_metrics") or d.get("mental_state")
        return {"_reading_help": True, "activity": act, "user_feedback": d.get("user_feedback"), "duration_seconds": duration, "mental_state_metrics": ms, "timestamp": ts}

    if msg_type == "mental_command":
        mc = d.get("mental_command", {})
        if isinstance(mc, dict):
            return CollectorPayload(
                type="mental_command",
                mental_command=MentalCommandSnapshot(
                    action=mc.get("action", "neutral"),
                    power=float(mc.get("power", 0)),
                    timestamp=ts,
                ),
                timestamp=ts,
            )
        return None

    return None
