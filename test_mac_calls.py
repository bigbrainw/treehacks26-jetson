#!/usr/bin/env python3
"""
Test all Mac → Jetson calls from the Mac side.

Verifies: connectivity, POST /eeg, GET /feedback, WebSocket (activity, mental_state, reading_help, feedback push).
Run on Mac before starting the full app. Processor must be running on Jetson.

Usage:
  python test_mac_calls.py                           # Full test (POST /eeg, WebSocket push)
  python test_mac_calls.py --quick                   # Connectivity + ActivityMonitor only
  python test_mac_calls.py --url https://YOUR_NGROK  # Test against ngrok URL
  python test_mac_calls.py --url http://JETSON_IP:8765
"""

import argparse
import json
import sys
import threading
import time
from pathlib import Path
from typing import Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import requests
except ImportError:
    requests = None

try:
    import websocket
except ImportError:
    websocket = None

try:
    import config
    DEFAULT_BASE = getattr(config, "JETSON_BASE", "http://localhost:8765").rstrip("/")
    DEFAULT_WS = getattr(config, "JETSON_WS_URL", "ws://localhost:8765")
except Exception:
    DEFAULT_BASE = "http://localhost:8765"
    DEFAULT_WS = "ws://localhost:8765"


def _headers(base: str) -> dict:
    h = {"Content-Type": "application/json"}
    if "ngrok" in base.lower():
        h["ngrok-skip-browser-warning"] = "1"
    return h


def test_connectivity(base: str, timeout: float) -> Tuple[bool, str]:
    """Can we reach the processor?"""
    try:
        r = requests.get(f"{base}/feedback", headers=_headers(base), timeout=5)
        return r.status_code in (200, 404), f"GET {base}/feedback -> {r.status_code}"
    except requests.exceptions.ConnectionError as e:
        return False, f"Connection failed: {e}"
    except Exception as e:
        return False, str(e)


def test_get_feedback(base: str, timeout: float) -> Tuple[bool, str]:
    """GET /feedback returns JSON with feedback key."""
    try:
        r = requests.get(f"{base}/feedback", headers=_headers(base), timeout=5)
        if r.status_code != 200:
            return False, f"Status {r.status_code}: {r.text[:150]}"
        data = r.json()
        if "feedback" not in data:
            return False, f"No 'feedback' key in response: {list(data.keys())}"
        return True, f"feedback len={len(data.get('feedback') or '')}"
    except Exception as e:
        return False, str(e)


def test_post_eeg_top_level(base: str, timeout: float) -> Tuple[bool, str]:
    """POST /eeg with activity + mental_state at top level (Mac app format)."""
    body = {
        "timestamp": time.time(),
        "activity": {
            "app_name": "Chrome",
            "window_title": "Test Page — arXiv",
            "context_type": "website",
            "context_id": "Chrome::arxiv.org",
            "duration_seconds": 180.5,
        },
        "mental_state": {
            "engagement": 0.32,
            "stress": 0.58,
            "relaxation": 0.42,
            "focus": 0.35,
        },
        "user_feedback": None,
    }
    try:
        r = requests.post(f"{base}/eeg", json=body, headers=_headers(base), timeout=timeout)
        if r.status_code != 200:
            return False, f"Status {r.status_code}: {r.text[:200]}"
        data = r.json()
        fb = data.get("feedback", "")
        if fb:
            return True, f"feedback len={len(fb)}"
        return False, "No feedback in response (cooldown or agent issue)"
    except Exception as e:
        return False, str(e)


def test_post_eeg_context_nested(base: str, timeout: float) -> Tuple[bool, str]:
    """POST /eeg with mental_state inside context (StreamToJetson format)."""
    body = {
        "timestamp": time.time(),
        "streams": {"met": {"met": [True, 0.35, True, 0.5, 0.35, True, 0.6], "time": time.time()}},
        "context": {
            "app_name": "Chrome",
            "window_title": "Lecture Notes...",
            "context_type": "website",
            "context_id": "Chrome::example.edu",
            "duration_seconds": 180.5,
            "mental_state": {
                "engagement": 0.32,
                "stress": 0.58,
                "relaxation": 0.42,
                "focus": 0.35,
            },
            "user_feedback": None,
        },
    }
    try:
        r = requests.post(f"{base}/eeg", json=body, headers=_headers(base), timeout=timeout)
        if r.status_code != 200:
            return False, f"Status {r.status_code}: {r.text[:200]}"
        data = r.json()
        fb = data.get("feedback", "")
        if fb:
            return True, f"feedback len={len(fb)}"
        return False, "No feedback in response"
    except Exception as e:
        return False, str(e)


def test_websocket(ws_url: str, base: str, timeout: float) -> Tuple[bool, str]:
    """WebSocket: connect, send activity + reading_help, receive feedback push."""
    if not websocket:
        return False, "pip install websocket-client"

    received = {"feedback": None, "done": False}
    ws_err = [None]  # mutable to capture in closures

    def on_message(ws, message):
        try:
            data = json.loads(message)
            if data.get("type") == "feedback":
                received["feedback"] = data.get("feedback", "")
                received["done"] = True
        except Exception:
            pass

    def on_error(ws, err):
        ws_err[0] = str(err) if err else "Unknown"

    try:
        ws = websocket.WebSocketApp(
            ws_url,
            on_message=on_message,
            on_error=on_error,
        )
        t = threading.Thread(target=lambda: ws.run_forever(), daemon=True)
        t.start()
        time.sleep(2)
        if ws_err[0]:
            return False, f"WebSocket error: {ws_err[0]}"

        # Send activity
        ws.send(json.dumps({
            "type": "activity",
            "timestamp": time.time(),
            "activity": {
                "app_name": "Chrome",
                "window_title": "Test",
                "context_type": "website",
                "context_id": "Chrome::test",
            },
        }))
        time.sleep(0.5)

        # Send reading_help (triggers agent)
        ws.send(json.dumps({
            "type": "reading_help",
            "timestamp": time.time(),
            "activity": {
                "app_name": "Chrome",
                "window_title": "Test Page",
                "context_type": "website",
                "context_id": "Chrome::test",
                "duration_seconds": 180,
            },
            "mental_state_metrics": {
                "engagement": 0.32,
                "stress": 0.58,
                "focus": 0.35,
            },
            "user_feedback": None,
        }))
        start = time.time()
        while not received["done"] and (time.time() - start) < timeout:
            time.sleep(0.5)
        ws.close()

        if received["feedback"]:
            return True, f"received feedback len={len(received['feedback'])}"
        return False, "No feedback push received (agent may be on cooldown)"
    except Exception as e:
        return False, str(e)


def test_activity_mac() -> Tuple[bool, str]:
    """On macOS: can we get current activity via ActivityMonitor?"""
    if sys.platform != "darwin":
        return True, "skipped (not macOS)"
    try:
        from activity import ActivityMonitor
        mon = ActivityMonitor(poll_interval=0.5)
        ctx = mon.get_current_activity()
        if ctx:
            app = getattr(ctx, "app_name", "") or ""
            title = getattr(ctx, "window_title", "") or ""
            return True, f"app={app[:20]}, title={title[:30]}..."
        return False, "No activity returned"
    except ImportError as e:
        return False, f"activity_mac import failed: {e}"
    except Exception as e:
        return False, str(e)


def main():
    p = argparse.ArgumentParser(description="Test all Mac → Jetson calls")
    p.add_argument("--url", default=None, help=f"Processor base URL (default: {DEFAULT_BASE})")
    p.add_argument("--timeout", type=float, default=90, help="Timeout for agent calls")
    p.add_argument("--quick", action="store_true", help="Skip agent tests (POST /eeg, WS reading_help); only connectivity + activity")
    args = p.parse_args()

    base = (args.url or DEFAULT_BASE).rstrip("/")
    ws_url = base.replace("https://", "wss://").replace("http://", "ws://")
    if "/feedback" in ws_url or "/eeg" in ws_url:
        ws_url = ws_url.split("/feedback")[0].split("/eeg")[0]

    if not requests:
        print("Error: pip install requests")
        return 1

    print("\n=== Mac → Jetson Call Tests ===\n")
    print(f"  Base URL: {base}")
    print(f"  WebSocket: {ws_url}")
    print(f"  Timeout: {args.timeout}s" + (" (quick: no agent tests)" if args.quick else "") + "\n")

    results = []

    # 1. Connectivity
    ok, msg = test_connectivity(base, args.timeout)
    results.append(("Connectivity (GET /feedback)", ok, msg))
    print(f"  1. Connectivity:   {'✓' if ok else '✗'} {msg}")

    if not ok:
        print("\n  Cannot reach processor. Is it running? Check JETSON_BASE / --url.")
        return 1

    # 2. GET /feedback
    ok, msg = test_get_feedback(base, args.timeout)
    results.append(("GET /feedback", ok, msg))
    print(f"  2. GET /feedback:   {'✓' if ok else '✗'} {msg}")

    if not args.quick:
        # 3. POST /eeg (top-level format)
        ok, msg = test_post_eeg_top_level(base, args.timeout)
        results.append(("POST /eeg (top-level)", ok, msg))
        print(f"  3. POST /eeg (1):  {'✓' if ok else '✗'} {msg}")

        # 4. POST /eeg (context.mental_state format) - skip if 3 just ran (cooldown)
        time.sleep(2)
        ok, msg = test_post_eeg_context_nested(base, args.timeout)
        results.append(("POST /eeg (context)", ok, msg))
        print(f"  4. POST /eeg (2):  {'✓' if ok else '✗'} {msg}")

        # 5. WebSocket
        ok, msg = test_websocket(ws_url, base, args.timeout)
        results.append(("WebSocket push", ok, msg))
        print(f"  5. WebSocket:      {'✓' if ok else '✗'} {msg}")
    else:
        # Quick: WebSocket connect only (no reading_help)
        if websocket:
            try:
                ws = websocket.WebSocketApp(ws_url)
                t = threading.Thread(target=lambda: ws.run_forever(), daemon=True)
                t.start()
                time.sleep(2)
                ws.close()
                results.append(("WebSocket connect", True, "connected"))
                print(f"  3. WebSocket:      ✓ connected")
            except Exception as e:
                results.append(("WebSocket connect", False, str(e)))
                print(f"  3. WebSocket:      ✗ {e}")
        else:
            results.append(("WebSocket connect", False, "websocket-client not installed"))
            print(f"  3. WebSocket:      ✗ pip install websocket-client")

    # 6. Activity monitor (Mac only)
    ok, msg = test_activity_mac()
    results.append(("ActivityMonitor", ok, msg))
    print(f"  6. ActivityMonitor:{'✓' if ok else '✗'} {msg}")

    # Summary
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n  --- {passed}/{total} passed ---\n")

    if passed < total:
        print("  Tips:")
        print("  - Connectivity/feedback fail: ensure processor is running")
        print("  - POST /eeg no feedback: agent cooldown (180s), or ANTHROPIC_API_KEY not set")
        print("  - WebSocket fail: check JETSON_WS_URL / WebSocket URL")
        print("  - ActivityMonitor fail: needs activity_mac.py on macOS")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
