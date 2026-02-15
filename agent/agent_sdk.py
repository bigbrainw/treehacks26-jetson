"""
Claude Agent SDK integration - uses WebSearch, WebFetch for real synthesis.

When user is stuck, the agent searches the web, fetches pages, and summarizes
key concepts—better than raw API calls.
"""

from typing import Optional

from agent.assistant import AssistantResponse, _rewrite_if_question

try:
    from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock
    import anyio
    _SDK_AVAILABLE = True
except ImportError:
    _SDK_AVAILABLE = False


def _run_agent_sync(prompt: str, system: str, tools: list[str]) -> str:
    """Run Agent SDK query synchronously, return final text."""

    if not _SDK_AVAILABLE:
        return ""

    async def _run():
        last_text = ""
        options = ClaudeAgentOptions(
            system_prompt=system,
            allowed_tools=tools,
            max_turns=5,
        )
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if hasattr(block, "text") and block.text:
                        last_text = block.text
        return last_text

    try:
        return anyio.run(_run)
    except Exception:
        return ""


def build_agent_sdk_prompt(
    window_title: str,
    reading_section: Optional[str] = None,
    app_name: Optional[str] = None,
    context_type: Optional[str] = None,
    prepared_resources: Optional[str] = None,
    mental_state_metrics: Optional[dict] = None,
) -> tuple[str, str]:
    """Build (prompt, system) for Agent SDK. Exposed for tests."""
    topic = window_title or "this topic"
    section_hint = f" (section: {reading_section})" if reading_section else ""
    app_hint = ""
    if app_name or context_type:
        parts = []
        if app_name:
            parts.append(f"app: {app_name}")
        if context_type:
            type_desc = {"pdf": "PDF document", "file": "code/editor", "website": "webpage", "browser": "browser"}.get(context_type, context_type)
            parts.append(f"content type: {type_desc}")
        app_hint = f" ({', '.join(parts)})" if parts else ""

    system = """You are a focus assistant. EEG metrics (engagement, stress, relaxation, focus) indicate the user's state.

ALWAYS start your response with 1–2 sentences explaining what the brain state means (e.g. "Your EEG shows high stress (0.9) and moderate focus (0.5)—that usually means you're wrestling with dense material."). Then deliver the main help.

THREE STATES:
1. FOCUSING: high engagement, low stress → Short encouragement: "Keep going!", "You've got this."
2. WANDERING: low engagement/focus → Brief nudge: "Your focus drifted. Try getting back to [topic]."
3. STUCK: high stress + low focus = user is struggling with the CONTENT. Your job is CONTENT HELP:
   - Use WebSearch/WebFetch to find and read the actual document (e.g. "Neurable whitepaper page 10").
   - SUMMARIZE the key points on that page/section in plain language. Explain what it says, not generic advice.
   - DO NOT give burnout/break advice as the main response. Do NOT lead with "take a break", "step away", "try a different section"—that misses the point. The user is stuck on the material; explain the material.
   - Optional: One short line at the end like "A 2-min break can help consolidate." is fine, but the bulk of your response must be: what this page actually says and what the key concepts mean.

- NEVER output raw "- Title URL:" lists. Write prose only.
- NEVER ask questions. End with a concrete summary—no "Would you like...", "Should I...". """

    ms_blk = ""
    if mental_state_metrics:
        parts = [f"{k}={float(v):.2f}" for k, v in mental_state_metrics.items() if isinstance(v, (int, float)) and v is not None]
        if parts:
            ms_blk = f"\n\nEEG metrics (0-1): {', '.join(parts)}. Interpret: focusing / wandering / stuck."

    prompt = f"""User is on: {topic}{app_hint}{section_hint}.{ms_blk}

EEG metrics above indicate focusing / wandering / stuck. First explain what the brain state means, then respond.
When STUCK: fetch the document and SUMMARIZE what this page/section says—explain the content, not break advice.
Output ONLY your message. No preamble. Do not ask questions."""
    if prepared_resources:
        prompt += f"\n\nPre-fetched context (use or supplement with your own search):\n{prepared_resources[:1500]}"
    return prompt, system


def decide_with_agent_sdk(
    window_title: str,
    mental_state: str,
    prepared_resources: Optional[str] = None,
    reading_section: Optional[str] = None,
    app_name: Optional[str] = None,
    context_type: Optional[str] = None,
    mental_state_metrics: Optional[dict] = None,
) -> Optional[AssistantResponse]:
    """
    Use Claude Agent SDK (WebSearch, WebFetch) to produce guidance.
    Interprets metrics: focusing → encourage; wandering → nudge; stuck → full help.
    """
    if not _SDK_AVAILABLE:
        return None

    prompt, system = build_agent_sdk_prompt(
        window_title, reading_section, app_name, context_type, prepared_resources, mental_state_metrics
    )

    try:
        text = _run_agent_sync(
            prompt=prompt,
            system=system,
            tools=["WebSearch", "WebFetch"],
        )
        if text and len(text.strip()) > 10:
            msg = _rewrite_if_question(text.strip(), prepared_resources) or text.strip()
            return AssistantResponse(
                should_help=True,
                message=msg,
                reason="Agent SDK synthesis (WebSearch/WebFetch)",
                action_type="offer_explanation",
            )
    except Exception:
        pass
    return None
