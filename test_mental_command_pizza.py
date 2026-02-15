#!/usr/bin/env python3
"""
Test mental command → pizza order flow (no Emotiv required).

Uses MCPizza (Domino's via pizzapi) - find store, add pizza, show total. No order placed.

Usage:
  python test_mental_command_pizza.py              # MCPizza flow
  python test_mental_command_pizza.py --search-only   # Agent SDK search fallback
  python test_mental_command_pizza.py --uber-eats-only  # Uber Eats browser (optional)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from agent.pizza_order import order_pizza, _run_mcpizza, _run_search_fallback

try:
    from agent.uber_eats_flow import run_uber_eats_flow
    _UBER_EATS_AVAILABLE = True
except ImportError:
    _UBER_EATS_AVAILABLE = False


def main():
    p = argparse.ArgumentParser(description="Test mental command → pizza order")
    p.add_argument("--search-only", action="store_true", help="Use Agent SDK search only")
    p.add_argument("--uber-eats-only", action="store_true", help="Run Uber Eats browser flow")
    p.add_argument("--address", default=None, help="Delivery address")
    args = p.parse_args()

    print("--- Mental Command → Pizza Order Test ---")
    print(f"  PIZZA_DELIVERY_ADDRESS: {getattr(config, 'PIZZA_DELIVERY_ADDRESS', config.UBER_EATS_DELIVERY_ADDRESS)}")
    print()

    if args.uber_eats_only and _UBER_EATS_AVAILABLE:
        print("  Mode: Uber Eats flow (browser)\n")
        addr = args.address or getattr(config, "UBER_EATS_DELIVERY_ADDRESS", "475 Via Ortega, Stanford, CA 94305")
        from agent.uber_eats_flow import run_uber_eats_flow
        r = run_uber_eats_flow(delivery_address=addr, headless=True)
        lines = ["Uber Eats flow:"]
        for s in r.steps:
            lines.append(f"  {'✓' if s.success else '✗'} {s.step}")
        result = "\n".join(lines)
    elif args.uber_eats_only:
        result = "Uber Eats flow not available (playwright)"
    elif args.search_only:
        print("  Mode: Agent SDK search only\n")
        result = _run_search_fallback(args.address)
    else:
        print("  Mode: MCPizza (Domino's)\n")
        result = order_pizza(args.address)

    print("  Result:")
    print(result)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
