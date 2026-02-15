"""
Build agent request payloads for POST /eeg and WebSocket reading_help.

Unified request format so both endpoints get consistent activity + mental state.
"""

import time
from typing import Any, Optional

from data_schema import ActivitySnapshot, MentalStateSnapshot


def _derive_mental_state_label(ms: MentalStateSnapshot | None) -> str:
    """Map metrics to stuck/distracted/focused for agent."""
    if ms is None:
        return "stuck"
    eng = ms.engagement or 0.5
    stress = ms.stress or 0.4
    focus = ms.focus or 0.5
    if eng < 0.4 and stress > 0.5:
        return "stuck"
    if focus < 0.35:
        return "distracted"
    return "focused"


def build_agent_request(
    act: ActivitySnapshot | None,
    ms: MentalStateSnapshot | None,
    user_feedback: Optional[str] = None,
) -> dict[str, Any]:
    """
    Build a request object used by both POST /eeg and WebSocket reading_help.

    Returns: {activity, mental_state_metrics, mental_state_label, user_feedback, duration_seconds}
    """
    act_dict: dict = {}
    duration = 10.0
    if act:
        act_dict = {
            "app_name": act.app_name,
            "window_title": act.window_title,
            "context_type": act.context_type,
            "context_id": act.context_id,
            "reading_section": act.reading_section,
            "duration_seconds": act.duration_seconds or 10.0,
        }
        duration = act_dict["duration_seconds"]

    mental_metrics = ms.to_dict() if ms else {}
    mental_label = _derive_mental_state_label(ms)

    return {
        "activity": act_dict,
        "mental_state_metrics": mental_metrics,
        "mental_state_label": mental_label,
        "user_feedback": user_feedback,
        "duration_seconds": duration,
    }


def build_post_eeg_body(req: dict, streams_met: Optional[dict] = None) -> dict:
    """
    Build body for POST /eeg. Processor expects streams + context (or activity + mental_state).
    """
    ts = time.time()
    act = req.get("activity") or {}
    duration = req.get("duration_seconds", 10.0)
    mental_metrics = req.get("mental_state_metrics") or {}
    mental_label = req.get("mental_state_label", "stuck")
    user_feedback = req.get("user_feedback")

    met = streams_met
    if met is None:
        met = {"met": [True, 0.4, True, 0.5], "time": ts}
    elif isinstance(met, dict) and "met" not in met:
        met = {"met": list(met.values()) if met else [True, 0.4, True, 0.5], "time": ts}
    elif isinstance(met, dict):
        met = dict(met)
        met.setdefault("time", ts)

    return {
        "timestamp": ts,
        "streams": {"met": met},
        "context": {
            "app_name": act.get("app_name", ""),
            "window_title": act.get("window_title", ""),
            "context_type": act.get("context_type", "app"),
            "context_id": act.get("context_id", ""),
            "duration_seconds": duration,
            "mental_state": mental_label,
            "mental_state_metrics": mental_metrics,
            "user_feedback": user_feedback,
        },
    }


def build_reading_help_ws_message(req: dict) -> dict:
    """Build WebSocket message for type=reading_help."""
    return {
        "type": "reading_help",
        "timestamp": time.time(),
        "activity": req.get("activity") or {},
        "mental_state_metrics": req.get("mental_state_metrics") or {},
        "user_feedback": req.get("user_feedback"),
    }
