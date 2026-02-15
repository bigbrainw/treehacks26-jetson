#!/usr/bin/env python3
"""
Test unified payload: activity + mental_state → agent response.

Sends the exact format and checks POST /eeg response + GET /feedback.
Processor must be running. Use --url for ngrok.

Usage:
  python test_unified_mental_state.py
  python test_unified_mental_state.py --url https://YOUR_NGROK_URL --timeout 90
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import requests
except ImportError:
    requests = None

try:
    import config
    DEFAULT_URL = getattr(config, "JETSON_BASE", "http://localhost:8765").rstrip("/")
except Exception:
    DEFAULT_URL = "http://localhost:8765"


def main():
    p = argparse.ArgumentParser(description="Test unified activity+mental_state payload")
    p.add_argument("--url", default=DEFAULT_URL, help="Processor base URL")
    p.add_argument("--timeout", type=float, default=90, help="Request timeout (agent can be slow)")
    args = p.parse_args()

    if not requests:
        print("Error: pip install requests")
        return 1

    base = args.url.rstrip("/")
    # Exact format from user
    body = {
        "timestamp": time.time(),
        "activity": {
            "app_name": "Chrome",
            "window_title": "Complex Paper — arXiv",
            "context_type": "website",
            "context_id": "Chrome::arxiv.org",
            "reading_section": None,
            "duration_seconds": 180.5,
        },
        "mental_state": {
            "engagement": 0.32,
            "stress": 0.58,
            "relaxation": 0.42,
            "focus": 0.35,
            "excitement": None,
            "interest": None,
            "metrics": {
                "met": [True, 0.32, True, 0.4, 0.35, True, 0.58, True, 0.42, True, 0.45, True, 0.35],
                "time": time.time(),
            },
        },
        "user_feedback": None,
    }

    print(f"\n--- Test unified mental state ---")
    print(f"  URL: {base}/eeg")
    print(f"  Activity: {body['activity']['app_name']} — {body['activity']['window_title']}")
    print(f"  Mental state: eng={body['mental_state']['engagement']}, stress={body['mental_state']['stress']}, focus={body['mental_state']['focus']}")
    print(f"  (Expected: stuck — high stress, low focus)\n")

    headers = {"Content-Type": "application/json"}
    if "ngrok" in base.lower():
        headers["ngrok-skip-browser-warning"] = "1"

    try:
        print("  POST /eeg ...")
        r = requests.post(f"{base}/eeg", json=body, headers=headers, timeout=args.timeout)
        print(f"  Status: {r.status_code}")

        if r.status_code != 200:
            print(f"  Error: {r.text[:300]}")
            return 1

        data = r.json()
        feedback = data.get("feedback", "")
        print(f"  Response ok: {data.get('ok', True)}")
        print(f"  Feedback length: {len(feedback)}")

        if feedback:
            print(f"\n  >>> Feedback:\n  {feedback[:500]}{'...' if len(feedback) > 500 else ''}\n")
            print("  PASS")
        else:
            print("\n  No feedback in response. Checking GET /feedback ...")
            try:
                r2 = requests.get(f"{base}/feedback", headers=headers, timeout=5)
                fb2 = r2.json().get("feedback", "") if r2.status_code == 200 else ""
                if fb2:
                    print(f"  GET /feedback: {fb2[:300]}...")
                else:
                    print("  GET /feedback also empty.")
                    print("  Possible: ANTHROPIC_API_KEY not set, agent returned no message, or cooldown.")
            except Exception as e:
                print(f"  GET /feedback failed: {e}")
            print("  FAIL (no feedback)")
            return 1

    except requests.exceptions.Timeout:
        print(f"  TIMEOUT after {args.timeout}s. Agent/WebSearch may be slow. Try --timeout 120")
        return 1
    except Exception as e:
        print(f"  Error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
