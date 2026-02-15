"""
MCPizza-style pizza ordering via Domino's API (pizzapi).

Uses the same approach as https://github.com/GrahamMcBain/mcpizza:
find store → search menu → add to order → prepare (no actual placement).
Safe: order is built and total shown, but never placed.
"""

import sys
from typing import Optional

try:
    from pizzapi import Address, Customer, Order, Store
    _PIZZAPI_AVAILABLE = True
except ImportError:
    _PIZZAPI_AVAILABLE = False


def _log(msg: str):
    print(f"  [MCPizza] {msg}", file=sys.stderr, flush=True)


def _parse_address(addr: str) -> tuple[str, str, str, str]:
    """Parse '475 Via Ortega, Stanford, CA 94305' into street, city, state, zip."""
    parts = [p.strip() for p in addr.split(",")]
    if len(parts) >= 4:
        return parts[0], parts[1], parts[2], parts[3].split()[-1] if parts[3] else "94305"
    if len(parts) == 3:
        return parts[0], parts[1], parts[2], "94305"
    if len(parts) == 2:
        return parts[0], parts[1], "CA", "94305"
    if len(parts) == 1 and len(parts[0]) == 5 and parts[0].isdigit():
        return "1 Main St", "Unknown", "CA", parts[0]
    return "475 Via Ortega", "Stanford", "CA", "94305"


def order_pizza_via_mcpizza(address: str = "475 Via Ortega, Stanford, CA 94305") -> str:
    """
    MCPizza-style flow: find Domino's store, search pizza, show order summary.
    Does NOT place the order. Falls back to MCPizza API when pizzapi has menu issues.
    """
    if not _PIZZAPI_AVAILABLE:
        return order_pizza_via_mcpizza_api(address)

    lines = ["MCPizza (Domino's) - order prepared, NOT placed:\n"]

    try:
        street, city, state, zipcode = _parse_address(address)
        _log(f"Address: {street}, {city}, {state} {zipcode}")

        # Step 1: Find store
        _log("Finding nearest Domino's...")
        addr_obj = Address(street, city, state, zipcode)
        try:
            store = addr_obj.closest_store()
        except Exception as e:
            return f"Could not find Domino's store: {e}"

        if not store:
            return "No Domino's store found for that address."

        store_id = getattr(store, "store_id", getattr(store, "id", "?"))
        store_addr = f"{getattr(store, 'street', '')} {getattr(store, 'city', '')}" or str(store_id)
        lines.append(f"✓ Store: {store_id} - {store_addr}")

        # Step 2: Search menu for pizza
        _log("Searching menu for pizza...")
        try:
            menu = store.get_menu()
        except Exception as e:
            _log(f"Menu load failed: {e} - using MCPizza API")
            return order_pizza_via_mcpizza_api(address)

        item_code = None
        item_name = None
        variants = getattr(menu, "variants", {}) or {}
        for code, v in variants.items():
            name = (v.get("Name") or "").lower()
            if "pizza" in name or "pepperoni" in name or "cheese" in name:
                item_code = code
                item_name = v.get("Name", code)
                break
        if not item_code and variants:
            item_code = next(iter(variants.keys()), None)
            item_name = variants.get(item_code, {}).get("Name", item_code) if item_code else None
        if not item_code:
            return order_pizza_via_mcpizza_api(address)

        lines.append(f"✓ Item: {item_name or item_code} ({item_code})")

        # Step 3: Build order (no placement)
        _log("Building order (customer placeholder)...")
        customer = Customer("Focus", "User", "focus@local.dev", "5555555555")
        order = Order(store, customer, addr_obj)
        order.add_item(item_code)

        # Step 4: Get price via pay_with(card=False) - validates order, NO placement
        total = "~$15-25 (estimate)"
        try:
            order.pay_with(card=False)
            amounts = order.data.get("Amounts", {})
            total = amounts.get("Customer", total)
            if isinstance(total, (int, float)):
                total = f"${total:.2f}"
        except Exception as e:
            _log(f"Price check: {e}")

        lines.append(f"✓ Added to cart: 1x {item_name or item_code}")
        lines.append(f"\nTotal: {total}")
        lines.append("\n⚠ Order NOT placed. Complete at dominos.com or the app.")

        return "\n".join(lines)

    except Exception as e:
        _log(f"Error: {e}")
        return order_pizza_via_mcpizza_api(address)


def order_pizza_via_mcpizza_api(address: str) -> str:
    """Call MCPizza HTTP API (mcpizza.vercel.app). Handles find_store + search_menu with mock fallback."""
    import urllib.request
    import json

    lines = ["MCPizza (Domino's via https://github.com/GrahamMcBain/mcpizza) - NOT placed:\n"]
    try:
        url = "https://mcpizza.vercel.app/api/mcp"
        # find_dominos_store
        req = urllib.request.Request(
            url,
            data=json.dumps({
                "method": "tools/call",
                "params": {"name": "find_dominos_store", "arguments": {"address": address}},
            }).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        content = data.get("content", data.get("result", {}).get("content", [{}]))
        text = content[0].get("text", "{}") if content else "{}"
        try:
            store_info = json.loads(text)
            addr = store_info.get("address", store_info.get("store_id", ""))
            lines.append(f"✓ Store: {addr}")
        except json.JSONDecodeError:
            lines.append(f"✓ Store: {text[:80]}")

        # search_menu
        req2 = urllib.request.Request(
            url,
            data=json.dumps({
                "method": "tools/call",
                "params": {"name": "search_menu", "arguments": {"query": "pizza"}},
            }).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req2, timeout=15) as r2:
            data2 = json.loads(r2.read().decode())
        content2 = data2.get("content", data2.get("result", {}).get("content", [{}]))
        text2 = content2[0].get("text", "[]") if content2 else "[]"
        try:
            items = json.loads(text2)
            if items:
                for it in items[:3]:
                    lines.append(f"✓ {it.get('name', '')} - {it.get('price', '')} (code: {it.get('code', '')})")
            else:
                lines.append("✓ Pizza options available (see dominos.com)")
        except json.JSONDecodeError:
            lines.append(f"✓ Menu: {text2[:100]}")

        lines.append("\n⚠ Order NOT placed. Complete at dominos.com or the Domino's app.")
        return "\n".join(lines)
    except Exception as e:
        return f"MCPizza API error: {e}"
