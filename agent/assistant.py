"""
Focus Assistant - LLM that organizes session data and decides when/how to help.

Uses Anthropic Claude API.
Routes to task-specific context handlers (code, browser, terminal) for richer context.
"""

import json
from dataclasses import dataclass
from typing import Optional

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None

from activity_tracker import ActivityContext
from agent.context_handlers import ContextRouter


def _rewrite_if_question(message: str, prepared_resources: Optional[str] = None) -> str:
    """If model asked a question despite instructions, rewrite using prepared data only."""
    forbidden = (
        "what's blocking",
        "whats blocking",
        "need help?",
        "want me to help",
        "want me to explain",
        "want me to walk",
    )
    msg_lower = message.lower().strip()
    if not any(f in msg_lower for f in forbidden):
        return message
    if prepared_resources:
        lines = [l.strip() for l in prepared_resources.split("\n") if l.strip() and not l.startswith("Related")]
        summary = " ".join(lines[:6])[:400].strip() if lines else ""
        return summary if summary else ""
    return ""


def _parse_json_response(text: str) -> Optional[dict]:
    """Extract JSON from LLM output."""
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


SUMMARIZE_PROMPT = """You are a study assistant. Convert these web search results into a clear, helpful summary.

STRICT FORMAT:
1. Write 2-3 short PROSE paragraphs explaining the key concepts. Use full sentences.
2. At the very end, add "For more: [Descriptive Title](url)" for 1-2 best resources.
3. FORBIDDEN: Do NOT output "- Title URL: https://" or bullet lists of links.
4. FORBIDDEN: Do NOT copy the raw search result format.

Example output: "The paper introduces transformer architecture based on self-attention. Each position computes attention weights over all others using query, key, value projections. Scaled dot-product prevents gradient vanishing. For more: [Illustrated Transformer](https://jalammar.github.io/illustrated-transformer/)\""""


def _summarize_resources(client, model: str, window_title: str, prepared_resources: str) -> Optional[str]:
    """Two-step: get LLM to synthesize resources into prose. Returns synthesis or None."""
    if not client or not prepared_resources or len(prepared_resources) < 100:
        return None
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=1024,
            system=SUMMARIZE_PROMPT,
            messages=[{"role": "user", "content": f"Topic: {window_title}\n\nResources:\n{prepared_resources}"}],
        )
        text = (resp.content[0].text or "").strip()
        if not text or "URL:" in text[:200]:
            return None
        return text
    except Exception:
        return None


def _fallback_summary(resources: str) -> str:
    """When LLM synthesis fails, build a simple paragraph from resources."""
    lines = [l.strip() for l in resources.split("\n") if l.strip() and not l.startswith("Related")]
    parts = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("- "):
            title = line[2:].strip()
            if title and "URL:" not in title:
                parts.append(title)
        i += 1
    if not parts:
        return ""
    intro = "Key resources for this topic: "
    return intro + ". ".join(parts[:4])[:500]

SYSTEM_PROMPT = """You are a focus assistant. When EEG = stuck, you DELIVER guidance. NEVER ask questions.

Your message must be helpful prose—2-4 short paragraphs explaining key concepts + 1-2 links at the end.
Do NOT output raw link lists or "Title URL:" format.

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
    prepared_resources: Optional[str] = None,
    reading_section: Optional[str] = None,
) -> str:
    """Build the user message. prepared_resources = pre-fetched background data to DELIVER."""
    duration_min = round(duration_sec / 60, 1)
    lines = [
        f"User is on: {window_title}",
        f"- App: {app} | Type: {context_type}",
        f"- Time on this: {duration_min} min | Mental state: {mental_state}",
        "",
    ]
    if reading_section:
        lines.append(f"Section user is reading: {reading_section}")
        lines.append("")
    if mental_state == "stuck":
        lines.append("EEG = stuck. Your message = the synthesis (prose only, no raw link dumps).")
        lines.append("")
    if prepared_resources:
        lines.append(f"Prepared resources:\n{prepared_resources}")
        lines.append("")
    if enriched:
        lines.append(f"Task context: {enriched}")
    if recent_sessions:
        lines.append("\nRecent activity (last sessions):")
        for s in recent_sessions[:5]:
            d = s.get("duration_seconds")
            d_str = f"{round(d/60, 1)}m" if d else "?"
            lines.append(f"- {s.get('app_name', '?')}: {s.get('window_title', '?')} ({d_str})")
    return "\n".join(lines)


class FocusAssistant:
    """
    LLM-powered assistant using Anthropic Claude.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        context_router: Optional[ContextRouter] = None,
    ):
        self.api_key = api_key or ""
        self.model = model or "claude-3-5-sonnet-20241022"
        self._router = context_router or ContextRouter()
        self._client = None
        if Anthropic and self.api_key:
            self._client = Anthropic(api_key=self.api_key)

    def decide(
        self,
        app_name: str,
        window_title: str,
        context_type: str,
        duration_seconds: float,
        mental_state: str,
        recent_sessions: list[dict],
        activity_context: Optional[ActivityContext] = None,
        prepared_resources: Optional[str] = None,
    ) -> AssistantResponse:
        """
        Decide what to say. prepared_resources = pre-fetched in background, deliver when EEG=stuck.
        """
        # Try Claude Agent SDK first when stuck (WebSearch, WebFetch for better synthesis)
        if mental_state == "stuck":
            try:
                import config
                if getattr(config, "USE_AGENT_SDK", True):
                    from agent.agent_sdk import decide_with_agent_sdk
                    reading_section = getattr(activity_context, "reading_section", None) if activity_context else None
                    out = decide_with_agent_sdk(
                        window_title, mental_state,
                        prepared_resources=prepared_resources,
                        reading_section=reading_section,
                    )
                    if out and out.message:
                        return out
            except Exception:
                pass

        if not self._client:
            return self._default_response(mental_state, duration_seconds)

        enriched = None
        if activity_context:
            _, enc = self._router.route(activity_context, skip_web_enrichment=(prepared_resources is not None))
            enriched = enc.extra_for_prompt

        reading_section = getattr(activity_context, "reading_section", None) if activity_context else None

        # Step 1: When stuck with resources, first get a synthesis (forces real summarization)
        resources_for_prompt = prepared_resources
        synthesis = None
        if mental_state == "stuck":
            synthesis = _summarize_resources(
                self._client, self.model, window_title, prepared_resources or ""
            )
            if synthesis:
                resources_for_prompt = f"Synthesis (use this as the basis for your message):\n{synthesis}"

        prompt = _build_context_prompt(
            app_name, window_title, context_type,
            duration_seconds, mental_state, recent_sessions,
            enriched=enriched,
            prepared_resources=resources_for_prompt,
            reading_section=reading_section,
        )
        try:
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text.strip()
            data = _parse_json_response(text)
            if not data:
                raise ValueError("Could not parse JSON from model output")
            msg = data.get("message", "")

            # If second step produced a raw link dump, use synthesis directly
            is_link_dump = "URL:" in msg or (msg.count("- ") >= 2 and "http" in msg)
            if mental_state == "stuck" and synthesis and is_link_dump:
                msg = synthesis

            msg = _rewrite_if_question(msg, prepared_resources)
            return AssistantResponse(
                should_help=data.get("should_help", False),
                message=msg,
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
        """Fallback when LLM is unavailable. No hardcoded content—only from processed resources."""
        if error:
            return AssistantResponse(
                should_help=False,
                message="",
                reason=f"LLM unavailable: {error}",
                action_type="none",
            )
        return AssistantResponse(
            should_help=False,
            message="",
            reason="LLM unavailable, no prepared resources",
            action_type="none",
        )
