"""
Snack suggestion agent - decides what you're having based on preferences + budget.

No order placed. Use WebSearch when available to find real options.
Budget is strictly enforced so suggestions stay affordable.
Checks current time so only open/24hr options are suggested.
"""

import re
import sys
from datetime import datetime
from typing import Optional

try:
    import config
except ImportError:
    config = None


def _parse_budget(budget: str | float) -> float:
    """Parse '$15', '15', 15 -> 15.0."""
    if isinstance(budget, (int, float)):
        return float(budget)
    s = str(budget).strip()
    s = re.sub(r"[^\d.]", "", s)
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0


def _current_time_str() -> str:
    """Current local time for context (e.g. '10:45 PM, Saturday')."""
    now = datetime.now()
    return now.strftime("%I:%M %p, %A").lstrip("0")  # e.g. 10:45 PM, Saturday


def _run_with_agent_sdk(preferences: str, budget: float, address: str) -> Optional[str]:
    """Use Claude Agent SDK (WebSearch) to find real options within budget."""
    try:
        from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage
        import anyio
    except ImportError:
        return None

    time_str = _current_time_str()
    system = """You are a late-night snack advisor. The user gives preferences and a strict budget.
- Use WebSearch to find real delivery/pickup options (DoorDash, Uber Eats, local spots, convenience stores)
- CHECK CURRENT TIME: many places close late at night. Only suggest options that are OPEN or 24-hour.
- Prefer 24hr spots, late-night delivery (DoorDash/Uber Eats), or convenience stores if it's late.
- ALL suggestions MUST be at or under the user's budget. Never suggest anything over budget.
- Return 2–4 specific options with estimated prices (e.g. "Large pepperoni pizza - ~$12 from Domino's")
- Include where to order (app/site) so the user can complete the order themselves
- Be concise: option name, price, source. No fluff.
- Do NOT place any order. You only suggest."""

    prompt = f"""Preferences: {preferences}
Budget: ${budget:.2f} max (STRICT - nothing over this)
Delivery address area: {address}

CURRENT TIME: {time_str} — only suggest places that are open or deliver at this hour.

Find 2–4 snack options that fit preferences, stay under ${budget:.2f}, and are actually available now.
List each with: name, estimated price, where to order."""

    async def _run():
        last_text = ""
        options = ClaudeAgentOptions(
            system_prompt=system,
            allowed_tools=["WebSearch", "WebFetch"],
            max_turns=4,
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
        return None


def _run_with_messages_api(preferences: str, budget: float, address: str) -> Optional[str]:
    """Fallback: Claude Messages API without WebSearch."""
    try:
        from anthropic import Anthropic
    except ImportError:
        return None

    api_key = getattr(config, "ANTHROPIC_API_KEY", None) if config else None
    model = getattr(config, "ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929") if config else None
    if not api_key:
        return None

    time_str = _current_time_str()
    client = Anthropic(api_key=api_key)
    system = """You are a late-night snack advisor. Suggest 2–4 options based on preferences and budget.
- CHECK CURRENT TIME: many places close late. Only suggest options that are OPEN or 24-hour (delivery apps, 7-Eleven, etc.).
- ALL options MUST be at or under the user's budget.
- Include estimated prices and where to order (DoorDash, Uber Eats, Domino's, 7-Eleven, etc.)
- Be specific: item name, ~price, source. No order placement—only suggestions."""

    prompt = f"""Preferences: {preferences}
Budget: ${budget:.2f} max (STRICT)
Area: {address}

CURRENT TIME: {time_str} — only suggest places open or delivering now.

Suggest 2–4 snacks within budget that are actually available at this hour."""

    try:
        resp = client.messages.create(
            model=model or "claude-3-5-sonnet-20241022",
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip() if resp.content else None
    except Exception:
        return None


def suggest_snack(
    preferences: str,
    budget: str | float,
    address: Optional[str] = None,
) -> str:
    """
    Suggest late-night snacks based on preferences and budget.
    Does NOT place any order.

    Args:
        preferences: What you like (e.g. "savory, not too heavy, I love chips and ice cream")
        budget: Max spend (e.g. "$15", "15", 15)
        address: Delivery area (default from config)

    Returns:
        Suggestions as text; all options under budget.
    """
    budget_val = _parse_budget(budget)
    if budget_val <= 0:
        return "Please set a positive budget (e.g. 15 or $15)."

    addr = address
    if not addr and config:
        addr = getattr(config, "PIZZA_DELIVERY_ADDRESS", "Stanford, CA")
    addr = addr or "Stanford, CA"

    # Prefer Agent SDK (WebSearch) for real options
    result = _run_with_agent_sdk(preferences, budget_val, addr)
    if not result:
        result = _run_with_messages_api(preferences, budget_val, addr)

    if not result:
        return "Could not get suggestions (check ANTHROPIC_API_KEY). Try: pip install claude-agent-sdk anthropic"

    footer = "\n\n⚠ No order placed. Use the apps/sites above to order when you're ready."
    return result.strip() + footer


def main():
    """CLI: python -m agent.snack_suggestion \"I like savory chips\" 15"""
    import argparse
    p = argparse.ArgumentParser(description="Suggest late-night snacks within budget (no order placed)")
    p.add_argument("preferences", help="What you like (e.g. savory, chips, ice cream)")
    p.add_argument("budget", help="Max spend, e.g. 15 or $15")
    p.add_argument("--address", "-a", help="Delivery area (default from config)")
    args = p.parse_args()

    result = suggest_snack(args.preferences, args.budget, args.address)
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
