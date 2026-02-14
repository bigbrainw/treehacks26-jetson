"""
Multi-turn agent - asks questions, adjusts help based on feedback, follows up.

Pattern: agent sees stuck → asks clarifying question → user responds →
agent adjusts help → can follow up. Conversation history informs each turn.
"""

import json
from dataclasses import dataclass, field
from typing import Optional

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

from activity_tracker import ActivityContext
from agent.context_handlers import ContextRouter
from agent.assistant import (
    AssistantResponse,
    _parse_json_response,
    _build_context_prompt,
)


MULTITURN_SYSTEM = """You are a focus assistant in a MULTI-TURN conversation. The user may be stuck, distracted, or need help.

Your job: reason about the full conversation, ask clarifying questions when helpful, and adjust your help based on user feedback.

Flow:
1. First turn: Offer help. Consider asking ONE short clarifying question (e.g. "What's blocking you - the concept or the implementation?").
2. Later turns: Use the user's response to tailor your help. Be specific. Offer concrete next steps.
3. Follow up: If they're still stuck, try a different angle (example, diagram, simpler explanation).

Keep messages brief. One main point per message.
Respond with JSON only: {"should_help": bool, "message": str, "reason": str, "action_type": str}
action_type: "ask_question" | "offer_explanation" | "suggest_break" | "follow_up" | "none"
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
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        context_router: Optional[ContextRouter] = None,
    ):
        self.base_url = base_url or "http://localhost:11434/v1"
        self.model = model or "phi3:mini"
        self._router = context_router or ContextRouter()
        self._client = None
        if OpenAI:
            self._client = OpenAI(base_url=self.base_url, api_key="ollama")
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
    ) -> AssistantResponse:
        """
        Decide what to say. If user_feedback is provided, this is a follow-up turn.
        Maintains conversation history so agent can reason across turns.
        """
        context_id = activity_context.context_id if activity_context else f"{app_name}::{window_title}"
        if user_feedback:
            self.add_user_feedback(context_id, user_feedback)

        enriched = None
        if activity_context:
            _, enc = self._router.route(activity_context)
            enriched = enc.extra_for_prompt

        base_prompt = _build_context_prompt(
            app_name, window_title, context_type,
            duration_seconds, mental_state, recent_sessions,
            enriched=enriched,
        )

        if not self._client:
            return self._default_response(mental_state, duration_seconds)

        conv = self._conversations.get(context_id)
        messages = [{"role": "system", "content": MULTITURN_SYSTEM}, {"role": "user", "content": base_prompt}]
        if conv and conv.turns:
            for t in conv.turns[-self._max_conversation_age_turns:]:
                messages.append({"role": t.role, "content": t.content})
            messages.append({
                "role": "user",
                "content": "Based on the conversation and any user response, what do you say next?",
            })

        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.4,
            )
            text = resp.choices[0].message.content.strip()
            data = _parse_json_response(text)
            if not data:
                raise ValueError("Could not parse JSON from model output")
            out = AssistantResponse(
                should_help=data.get("should_help", False),
                message=data.get("message", ""),
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
            return self._default_response(mental_state, duration_seconds, error=str(e))

    def clear_conversation(self, context_id: str):
        """Reset when user switches context."""
        if context_id in self._conversations:
            del self._conversations[context_id]

    def _default_response(
        self,
        mental_state: str,
        duration_seconds: float,
        error: Optional[str] = None,
    ) -> AssistantResponse:
        if error:
            return AssistantResponse(
                should_help=False, message="", reason=f"LLM: {error}", action_type="none",
            )
        if mental_state == "stuck":
            return AssistantResponse(
                should_help=True,
                message="You've been on this a while. What's blocking you - the concept or getting it to work?",
                reason="Mental state: stuck",
                action_type="ask_question",
            )
        if mental_state == "distracted" and duration_seconds > 300:
            return AssistantResponse(
                should_help=True,
                message="Seems like you're switching a lot. Want a short break to reset?",
                reason="Mental state: distracted",
                action_type="suggest_break",
            )
        return AssistantResponse(
            should_help=False, message="", reason="No intervention", action_type="none",
        )
