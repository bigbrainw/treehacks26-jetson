"""
Multi-turn agent - asks questions, adjusts help based on feedback, follows up.

Pattern: agent sees stuck → asks clarifying question → user responds →
agent adjusts help → can follow up. Conversation history informs each turn.
"""

import json
from dataclasses import dataclass, field
from typing import Optional

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None

from activity_tracker import ActivityContext
from agent.context_handlers import ContextRouter
from agent.assistant import (
    AssistantResponse,
    _parse_json_response,
    _build_context_prompt,
    _rewrite_if_question,
    _summarize_resources,
    _fallback_summary,
)


MULTITURN_SYSTEM = """Focus assistant. EEG = stuck means DELIVER guidance. NEVER ask questions.

CRITICAL: SYNTHESIZE the prepared resources—do NOT dump raw titles/URLs.
1. Read the resources, then write 2–4 short paragraphs explaining KEY CONCEPTS in plain language.
2. Connect ideas, highlight what matters, explain jargon.
3. End with 1–2 links as "For more: [title](url)".

BAD: "- Title URL: https://... - Title URL: ..."
GOOD: "Self-attention lets each position attend to all others via Q,K,V. Scaled dot-product stabilizes training. For a visual walkthrough: [link]"

Respond with JSON only: {"should_help": bool, "message": str, "reason": str, "action_type": str}
action_type: "offer_explanation" | "suggest_break" | "follow_up" | "none"
"""


@dataclass
class Turn:
    role: str  # "user" or "assistant"
    content: str


@dataclass
class Conversation:
    context_id: str
    turns: list[Turn] = field(default_factory=list)

    def add_user(self, content: str):
        self.turns.append(Turn(role="user", content=content))

    def add_assistant(self, content: str):
        self.turns.append(Turn(role="assistant", content=content))

    def to_messages(self) -> list[dict]:
        return [{"role": t.role, "content": t.content} for t in self.turns]


class MultiTurnAssistant:
    """
    Multi-turn agent: maintains conversation per context, reasons about feedback,
    asks questions, adjusts help, follows up.
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
        self._conversations: dict[str, Conversation] = {}
        self._max_conversation_age_turns = 6  # trim old

    def add_user_feedback(self, context_id: str, feedback: str):
        """Record user's response for next agent turn."""
        if context_id not in self._conversations:
            self._conversations[context_id] = Conversation(context_id=context_id)
        self._conversations[context_id].add_user(feedback.strip())

    def decide(
        self,
        app_name: str,
        window_title: str,
        context_type: str,
        duration_seconds: float,
        mental_state: str,
        recent_sessions: list[dict],
        activity_context: Optional[ActivityContext] = None,
        user_feedback: Optional[str] = None,
        prepared_resources: Optional[str] = None,
    ) -> AssistantResponse:
        """
        Decide what to say. prepared_resources = pre-fetched in background. user_feedback = follow-up.
        """
        context_id = activity_context.context_id if activity_context else f"{app_name}::{window_title}"
        if user_feedback:
            self.add_user_feedback(context_id, user_feedback)

        enriched = None
        if activity_context:
            _, enc = self._router.route(activity_context, skip_web_enrichment=(prepared_resources is not None))
            enriched = enc.extra_for_prompt

        reading_section = getattr(activity_context, "reading_section", None) if activity_context else None

        # Try Claude Agent SDK first when stuck (WebSearch, WebFetch for better synthesis)
        if mental_state == "stuck":
            try:
                import config
                if getattr(config, "USE_AGENT_SDK", True):
                    from agent.agent_sdk import decide_with_agent_sdk
                    out = decide_with_agent_sdk(
                        window_title, mental_state,
                        prepared_resources=prepared_resources,
                        reading_section=reading_section,
                    )
                    if out and out.message:
                        return out
            except Exception:
                pass

        # Step 1: When stuck with resources, get a synthesis first (real summarization)
        synthesis = None
        if mental_state == "stuck" and self._client and prepared_resources and len((prepared_resources or "")) > 100:
            synthesis = _summarize_resources(
                self._client, self.model, window_title, prepared_resources
            )
            if synthesis and len(synthesis) > 50:
                return AssistantResponse(
                    should_help=True,
                    message=synthesis,
                    reason="Mental state: stuck, synthesized from prepared resources",
                    action_type="offer_explanation",
                )

            # Synthesis failed—use simple fallback to avoid raw link dump
            fallback = _fallback_summary(prepared_resources)
            if fallback:
                return AssistantResponse(
                    should_help=True,
                    message=fallback,
                    reason="Mental state: stuck, fallback summary",
                    action_type="offer_explanation",
                )

        resources_for_prompt = prepared_resources
        base_prompt = _build_context_prompt(
            app_name, window_title, context_type,
            duration_seconds, mental_state, recent_sessions,
            enriched=enriched,
            prepared_resources=resources_for_prompt,
            reading_section=reading_section,
        )

        if not self._client:
            return self._default_response(
                mental_state, duration_seconds,
                prepared_resources=prepared_resources,
                app_name=app_name, window_title=window_title, context_type=context_type,
                activity_context=activity_context,
            )

        conv = self._conversations.get(context_id)
        messages: list[dict] = [{"role": "user", "content": base_prompt}]
        if conv and conv.turns:
            for t in conv.turns[-self._max_conversation_age_turns:]:
                messages.append({"role": t.role, "content": t.content})
            messages.append({
                "role": "user",
                "content": "Based on the conversation and any user response, what do you say next?",
            })

        try:
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=MULTITURN_SYSTEM,
                messages=messages,
            )
            text = resp.content[0].text.strip()
            data = _parse_json_response(text)
            if not data:
                raise ValueError("Could not parse JSON from model output")
            msg = data.get("message", "")

            # Post-process: if output is a raw link dump, replace with fallback
            is_link_dump = "URL:" in msg or (msg.strip().startswith("- ") and "http" in msg)
            if mental_state == "stuck" and prepared_resources:
                if is_link_dump:
                    fb = _fallback_summary(prepared_resources)
                    msg = (synthesis if synthesis and len(synthesis) > 50 else None) or fb or msg

            msg = _rewrite_if_question(msg, prepared_resources)
            out = AssistantResponse(
                should_help=data.get("should_help", False),
                message=msg,
                reason=data.get("reason", ""),
                action_type=data.get("action_type", "none"),
            )
            if out.message and conv:
                conv.add_assistant(out.message)
            elif out.message and context_id not in self._conversations:
                self._conversations[context_id] = Conversation(context_id=context_id)
                self._conversations[context_id].add_assistant(out.message)
            return out
        except Exception as e:
            return self._default_response(
                mental_state, duration_seconds, error=str(e),
                prepared_resources=prepared_resources,
                app_name=app_name, window_title=window_title, context_type=context_type,
                activity_context=activity_context,
            )

    def clear_conversation(self, context_id: str):
        """Reset when user switches context."""
        if context_id in self._conversations:
            del self._conversations[context_id]

    def _default_response(
        self,
        mental_state: str,
        duration_seconds: float,
        error: Optional[str] = None,
        prepared_resources: Optional[str] = None,
        app_name: str = "",
        window_title: str = "",
        context_type: str = "",
        activity_context: Optional[ActivityContext] = None,
    ) -> AssistantResponse:
        """No hardcoded content—only deliver from background-processed resources."""
        if error:
            return AssistantResponse(
                should_help=False, message="", reason=f"LLM: {error}", action_type="none",
            )
        if mental_state == "stuck":
            resources = prepared_resources or ""
            if resources:
                lines = [l.strip() for l in resources.split("\n") if l.strip() and not l.startswith("Related")]
                summary = " ".join(lines[:6])[:400].strip() if lines else ""
                msg = summary if summary else ""
            else:
                msg = ""
            return AssistantResponse(
                should_help=bool(msg),
                message=msg,
                reason="Mental state: stuck" if msg else "No prepared resources",
                action_type="offer_explanation" if msg else "none",
            )
        if mental_state == "distracted" and duration_seconds > 300:
            return AssistantResponse(
                should_help=False,
                message="",
                reason="Mental state: distracted (no processed content)",
                action_type="none",
            )
        return AssistantResponse(
            should_help=False, message="", reason="No intervention", action_type="none",
        )
