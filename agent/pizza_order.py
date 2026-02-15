"""
Pizza order action - triggered by mental command.

Providers:
- mcpizza: Domino's via pizzapi (https://github.com/GrahamMcBain/mcpizza)
- zomato: Zomato MCP built into app (requires ZOMATO_ACCESS_TOKEN)

No order placed; flow stops before payment.
"""

from typing import Optional


def _run_zomato(location: Optional[str] = None) -> str:
    """Zomato MCP flow - search restaurants, browse. No order placed."""
    try:
        import config
        from agent.zomato_mcp_client import order_pizza_via_zomato
        addr = location or getattr(config, "PIZZA_DELIVERY_ADDRESS", "475 Via Ortega, Stanford, CA 94305")
        return order_pizza_via_zomato(addr)
    except Exception as e:
        return f"Zomato order error: {e}"


def _run_mcpizza(location: Optional[str] = None) -> str:
    """MCPizza-style Domino's flow via pizzapi. No order placed."""
    try:
        import config
        from agent.mcpizza_order import order_pizza_via_mcpizza
        addr = location or getattr(config, "PIZZA_DELIVERY_ADDRESS", "475 Via Ortega, Stanford, CA 94305")
        return order_pizza_via_mcpizza(addr)
    except Exception as e:
        return f"MCPizza order error: {e}"


def _run_search_fallback(location: Optional[str] = None) -> str:
    """Use Agent SDK to search for pizza options."""
    try:
        from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage
        import anyio
    except ImportError:
        return "Claude Agent SDK not installed. Run: pip install claude-agent-sdk"

    from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage
    import anyio
    loc_hint = f" User location hint: {location}." if location else ""

    system = """You are a pizza ordering assistant. When the user wants to order pizza, you help them.
- Use WebSearch to find pizza delivery options, local pizzerias, or Domino's/Papa John's/etc. ordering links
- Provide 2-3 concrete options: direct order links or phone numbers
- Keep it brief: 2-4 short paragraphs + links
- If you can't actually place an order, give the user the best links/numbers to complete the order themselves."""

    prompt = f"""The user just triggered a mental command to order pizza. Help them order.{loc_hint}

Output ONLY the helpful ordering info: options, links, or next steps. No preamble."""

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
        result = anyio.run(_run)
        return (result or "Could not get pizza options. Try searching 'pizza delivery near me'").strip()
    except Exception as e:
        return f"Pizza order helper error: {e}"


def order_pizza(location: Optional[str] = None) -> str:
    """
    Order pizza: uses MCPizza or Zomato per PIZZA_PROVIDER. No order placed.
    Falls back to Agent SDK search if provider unavailable.
    """
    try:
        import config
        provider = getattr(config, "PIZZA_PROVIDER", "mcpizza")
        if provider == "zomato":
            result = _run_zomato(location)
        else:
            result = _run_mcpizza(location)
        if "not installed" in result.lower() or "error" in result.lower():
            return _run_search_fallback(location)
        return result
    except Exception:
        return _run_search_fallback(location)
