"""
Zomato MCP client - built into the app (not Cursor MCP).

Connects to https://mcp-server.zomato.com/mcp via Streamable HTTP.
Requires ZOMATO_ACCESS_TOKEN (OAuth). Zomato whitelists only specific redirect URIs;
see https://github.com/Zomato/mcp-server-manifest - you can obtain a token via
Postman (oauth.pstmn.io is whitelisted) or another whitelisted client.
"""

import json
import logging
import sys
from typing import Any, Optional

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

logger = logging.getLogger(__name__)

ZOMATO_MCP_URL = "https://mcp-server.zomato.com/mcp"


def _extract_text(result: Any) -> str:
    """Extract human-readable text from CallToolResult."""
    if result.isError:
        parts = [f"Error: {result}"]
    else:
        parts = []
    for block in getattr(result, "content", []) or []:
        if hasattr(block, "text") and block.text:
            parts.append(block.text)
    if getattr(result, "structuredContent", None):
        try:
            parts.append(json.dumps(result.structuredContent, indent=2))
        except Exception:
            pass
    return "\n".join(parts) if parts else str(result)


def _log(msg: str) -> None:
    print(f"  [Zomato] {msg}", file=sys.stderr, flush=True)


async def _run_zomato_order(address: str) -> str:
    """
    Connect to Zomato MCP, discover restaurants near address, browse menu, prepare order.
    Does NOT place the order or pay.
    """
    token = _get_token()
    if not token:
        return (
            "Zomato MCP requires ZOMATO_ACCESS_TOKEN. OAuth redirect URIs are limited; "
            "see https://github.com/Zomato/mcp-server-manifest. You can obtain a token "
            "via Postman (oauth.pstmn.io is whitelisted) or Cursor MCP, then set it in .env"
        )

    headers = {"Authorization": f"Bearer {token}"}
    http_client = create_mcp_http_client(headers=headers)

    lines = ["Zomato - order prepared, NOT placed:\n"]

    try:
        async with http_client:
            async with streamable_http_client(
                ZOMATO_MCP_URL,
                http_client=http_client,
                terminate_on_close=True,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    _log("Connected to Zomato MCP")

                    # Discover available tools
                    tools_result = await session.list_tools()
                    tool_names = [t.name for t in tools_result.tools]
                    _log(f"Tools: {tool_names}")

                    # Try restaurant search / discovery - typical names: search_restaurants, find_restaurants, etc.
                    search_tool = None
                    for name in ("search_restaurants", "find_restaurants", "discover_restaurants", "search_nearby"):
                        if name in tool_names:
                            search_tool = name
                            break
                    if not search_tool and tool_names:
                        search_tool = tool_names[0]

                    if search_tool:
                        _log(f"Calling {search_tool} for address: {address}")
                        result = await session.call_tool(
                            search_tool,
                            arguments={"address": address, "query": "pizza"},
                        )
                        text = _extract_text(result)
                        if result.isError:
                            lines.append(f"Search: {text[:200]}")
                        else:
                            lines.append(f"✓ Restaurants near {address[:40]}...")
                            if len(text) > 500:
                                lines.append(text[:500] + "...")
                            else:
                                lines.append(text)

                    lines.append("\n⚠ Order NOT placed. Complete in Zomato app or website.")
                    return "\n".join(lines)

    except Exception as e:
        _log(f"Error: {e}")
        return f"Zomato MCP error: {e}"


def _get_token() -> Optional[str]:
    try:
        import config
        token = getattr(config, "ZOMATO_ACCESS_TOKEN", "") or ""
        return token.strip() or None
    except Exception:
        import os
        return os.environ.get("ZOMATO_ACCESS_TOKEN", "").strip() or None


def order_pizza_via_zomato(address: str = "475 Via Ortega, Stanford, CA 94305") -> str:
    """Synchronous entry: run Zomato MCP pizza flow. No order placed."""
    import anyio
    return anyio.run(_run_zomato_order, address)
