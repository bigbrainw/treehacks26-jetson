"""
Focus Assistant - LLM that organizes session data and decides when/how to help.

Uses Ollama or any OpenAI-compatible local endpoint. No cloud API calls.
Routes to task-specific context handlers (code, browser, terminal) for richer context.
"""

import json
from dataclasses import dataclass
from typing import Optional

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

from activity_tracker import ActivityContext
from agent.context_handlers import ContextRouter


def _parse_json_response(text: str) -> Optional[dict]:
    """Extract JSON from LLM output (Ollama may add extra text)."""
    text = text.strip()
    if "```" in text:
        for part in text.split("```"):
            part = part.strip().lstrip("json")
            if part.startswith("{"):
                try:
                    return json.loads(part)
                except json.JSONDecodeError:
                    pass
    idx = text.find("{")
    if idx >= 0:
        depth, start = 0, idx
        for i, c in enumerate(text[idx:], idx):
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


@dataclass
class AssistantResponse:
    """Agent's decision on whether and how to help."""
    should_help: bool
    message: str
    reason: str
    action_type: str  # e.g. "offer_explanation", "suggest_break", "encourage_focus", "none"


SYSTEM_PROMPT = """You are a focus assistant that watches over a user's work session. You receive:
- What app/file/page they're on
- How long they've been on it
- Their inferred mental state from EEG: focused, stuck, distracted, or unknown
- Recent activity history

Your job: decide whether to offer help, and if so, what to say. Be brief and non-intrusive.
- If focused: usually don't interrupt. Only suggest a break if they've been at it >30 min.
- If stuck: consider offering help - explain a concept, suggest a resource, or ask what's blocking them.
- If distracted: gently nudge back to focus, or suggest a short break if they've been switching a lot.

Respond with JSON only: {"should_help": bool, "message": str, "reason": str, "action_type": str}
action_type: "offer_explanation" | "suggest_break" | "encourage_focus" | "offer_resources" | "none"
"""


def _build_context_prompt(
    app: str,
    window_title: str,
    context_type: str,
    duration_sec: float,
    mental_state: str,
    recent_sessions: list[dict],
    enriched: Optional[str] = None,
) -> str:
    """Build the user message with session context and task-specific enrichment."""
    duration_min = round(duration_sec / 60, 1)
    lines = [
        f"Current context:",
        f"- App: {app}",
        f"- Page/file: {window_title}",
        f"- Type: {context_type}",
        f"- Time on this: {duration_min} minutes",
        f"- Mental state (EEG): {mental_state}",
    ]
    if enriched:
        lines.append(f"\nTask context: {enriched}")
    if recent_sessions:
        lines.append("\nRecent activity (last sessions):")
        for s in recent_sessions[:5]:
            d = s.get("duration_seconds")
            d_str = f"{round(d/60, 1)}m" if d else "?"
            lines.append(f"- {s.get('app_name', '?')}: {s.get('window_title', '?')} ({d_str})")
    return "\n".join(lines)


class FocusAssistant:
    """
    LLM-powered assistant. Uses Ollama or local OpenAI-compatible API.
    No cloud calls - runs on edge (Jetson, etc.).
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        context_router: Optional[ContextRouter] = None,
    ):
        self.base_url = base_url or "http://localhost:11434/v1"
        self.model = model or "phi3:mini"
        self._router = context_router or ContextRouter()
        self._client = None
        if OpenAI:
            self._client = OpenAI(
                base_url=self.base_url,
                api_key="ollama",  # Ollama ignores it; required by client
            )

    def decide(
        self,
        app_name: str,
        window_title: str,
        context_type: str,
        duration_seconds: float,
        mental_state: str,
        recent_sessions: list[dict],
        activity_context: Optional[ActivityContext] = None,
    ) -> AssistantResponse:
        """
        Decide whether to help and what feedback to give.
        Returns AssistantResponse. If LLM unavailable, returns a safe default.
        """
        if not self._client:
            return self._default_response(mental_state, duration_seconds)

        enriched = None
        if activity_context:
            _, enc = self._router.route(activity_context)
            enriched = enc.extra_for_prompt

        prompt = _build_context_prompt(
            app_name, window_title, context_type,
            duration_seconds, mental_state, recent_sessions,
            enriched=enriched,
        )
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
            )
            text = resp.choices[0].message.content.strip()
            data = _parse_json_response(text)
            if not data:
                raise ValueError("Could not parse JSON from model output")
            return AssistantResponse(
                should_help=data.get("should_help", False),
                message=data.get("message", ""),
                reason=data.get("reason", ""),
                action_type=data.get("action_type", "none"),
            )
        except Exception as e:
            return self._default_response(mental_state, duration_seconds, error=str(e))

    def _default_response(
        self,
        mental_state: str,
        duration_seconds: float,
        error: Optional[str] = None,
    ) -> AssistantResponse:
        """Fallback when LLM is unavailable."""
        if error:
            return AssistantResponse(
                should_help=False,
                message="",
                reason=f"LLM unavailable: {error}",
                action_type="none",
            )
        # Simple heuristics when no LLM
        if mental_state == "stuck":
            return AssistantResponse(
                should_help=True,
                message="You've been on this for a while. Need help? I can explain concepts or suggest resources.",
                reason="Mental state: stuck",
                action_type="offer_explanation",
            )
        if mental_state == "distracted" and duration_seconds > 300:
            return AssistantResponse(
                should_help=True,
                message="Seems like you might be switching a lot. Want to take a short break and come back focused?",
                reason="Mental state: distracted, long session",
                action_type="suggest_break",
            )
        return AssistantResponse(
            should_help=False,
            message="",
            reason="No intervention needed",
            action_type="none",
        )
