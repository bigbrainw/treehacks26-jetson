"""
Claude Agent SDK integration - uses WebSearch, WebFetch for real synthesis.

When user is stuck, the agent searches the web, fetches pages, and summarizes
key concepts—better than raw API calls.
"""

from typing import Optional

from agent.assistant import AssistantResponse

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


def decide_with_agent_sdk(
    window_title: str,
    mental_state: str,
    prepared_resources: Optional[str] = None,
    reading_section: Optional[str] = None,
) -> Optional[AssistantResponse]:
    """
    Use Claude Agent SDK (WebSearch, WebFetch) to produce helpful guidance.
    Returns AssistantResponse or None if SDK unavailable / not stuck.
    """
    if not _SDK_AVAILABLE or mental_state != "stuck":
        return None

    topic = window_title or "this topic"
    section_hint = f" (section: {reading_section})" if reading_section else ""

    system = """You are a focus assistant. The user is stuck and needs help understanding.
Your job: synthesize key concepts into 2-4 short paragraphs of clear, helpful prose.
- Use WebSearch and WebFetch to gather and verify information
- Explain concepts in plain language, define jargon
- End with 1-2 most useful links as "For more: [title](url)"
- NEVER output raw "- Title URL:" lists. Write prose only."""

    prompt = f"""User is stuck on: {topic}{section_hint}.

Provide a concise summary of key concepts to help them understand. Use web search and fetch as needed.
Output ONLY the helpful guidance (2-4 paragraphs + 1-2 links). No preamble."""
    if prepared_resources:
        prompt += f"\n\nPre-fetched context (use or supplement with your own search):\n{prepared_resources[:1500]}"

    try:
        text = _run_agent_sync(
            prompt=prompt,
            system=system,
            tools=["WebSearch", "WebFetch"],
        )
        if text and len(text) > 50:
            return AssistantResponse(
                should_help=True,
                message=text.strip(),
                reason="Agent SDK synthesis (WebSearch/WebFetch)",
                action_type="offer_explanation",
            )
    except Exception:
        pass
    return None
