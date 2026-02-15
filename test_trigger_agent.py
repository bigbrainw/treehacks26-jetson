#!/usr/bin/env python3
"""
End-to-end test: trigger the agent and verify feedback.

Sends activity + EEG (or mental command) to the processor, then checks GET /feedback.
Processor must be running first (python processor_main.py or via ngrok).

Usage:
  # Quick test: mental command → pizza flow (instant)
  python test_trigger_agent.py
  python test_trigger_agent.py --url https://YOUR_NGROK_URL

  # Full test: stuck flow (needs LONG threshold ~3 min, or --short + processor with LONG=8)
  python test_trigger_agent.py --mental-state
  LONG_SESSION_THRESHOLD=8 python processor_main.py &  # in another terminal
  python test_trigger_agent.py --mental-state --short
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    import urllib.request
    _HAS_URLLIB = True
except ImportError:
    _HAS_URLLIB = False


def _get_base_url(url: str | None) -> str:
    if url:
        return url.rstrip("/")
    try:
        import config
        base = (getattr(config, "JETSON_WS_URL", "") or "ws://localhost:8765")
        base = base.replace("ws://", "http://").replace("wss://", "https://").rstrip("/")
        if "/ws" in base:
            base = base.split("/ws")[0]
        return base
    except Exception:
        return "http://localhost:8765"


def _post_eeg(base_url: str, body: dict, headers: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"{base_url}/eeg",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    if "ngrok" in base_url.lower():
        req.add_header("ngrok-skip-browser-warning", "1")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def _get_feedback(base_url: str) -> str:
    req = urllib.request.Request(f"{base_url}/feedback")
    if "ngrok" in base_url.lower():
        req.add_header("ngrok-skip-browser-warning", "1")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode()).get("feedback", "")


def run_mental_command_test(base_url: str) -> bool:
    """Send mental command (push) -> triggers pizza flow immediately -> check feedback."""
    print("  [1/3] Sending mental command (push, power=0.8)...")
    body = {
        "timestamp": time.time(),
        "context": {
            "app_name": "Test",
            "window_title": "test_trigger_agent.py",
            "context_type": "file",
            "context_id": "test-mental-cmd",
        },
        "streams": {
            "com": ["push", 0.8],
        },
    }
    try:
        resp = _post_eeg(base_url, body)
        print(f"  [2/3] POST /eeg response: ok={resp.get('ok')}, feedback_len={len(resp.get('feedback', ''))}")
    except Exception as e:
        print(f"  POST /eeg failed: {e}")
        return False

    print("  [3/3] Waiting for agent (pizza flow ~5-15s)...")
    feedback = ""
    for _ in range(12):
        time.sleep(1)
        try:
            feedback = _get_feedback(base_url)
            if feedback and len(feedback) > 20:
                break
        except Exception as e:
            if "404" in str(e):
                print(f"  Note: GET /feedback returned 404. Ensure processor has latest code (with /feedback route).")
                return False
            pass
    if feedback and len(feedback) > 20:
        print(f"\n  ✓ Agent feedback received ({len(feedback)} chars):")
        print(f"    {feedback[:200]}...")
        return True
    print(f"  ✗ No feedback yet (got: {repr(feedback)[:80]})")
    return False


def run_pdf_stuck_test(base_url: str) -> bool:
    """POST Preview + PDF with mental_state=stuck → immediate agent feedback. Checks for PDF (not Python file)."""
    print("  [1/3] POST /eeg (Preview + Neurable_Whitepaper.pdf, mental_state=stuck)...")
    body = {
        "timestamp": time.time(),
        "context": {
            "app_name": "Preview",
            "window_title": "Neurable_Whitepaper.pdf — Page 5 of 42",
            "context_type": "pdf",
            "context_id": "Preview::Neurable_Whitepaper.pdf",
            "reading_section": "Page 5 of 42",
            "duration_seconds": 45,
            "mental_state": "stuck",
        },
        "streams": {"met": {"met": [True, 0.4, True, 0.5], "time": time.time()}},
    }
    try:
        resp = _post_eeg(base_url, body)
        feedback = resp.get("feedback", "")
        print(f"  [2/3] Response: ok={resp.get('ok')}, feedback_len={len(feedback)}")
    except Exception as e:
        print(f"  POST /eeg failed: {e}")
        return False

    print("  [3/3] Checking feedback content...")
    if not feedback or len(feedback) < 30:
        print(f"  ✗ No usable feedback (got: {repr(feedback)[:100]})")
        return False

    # Should NOT say "Python file" when context is PDF
    if "python file" in feedback.lower():
        print(f"  ✗ BUG: Feedback says 'Python file' for PDF context!")
        print(f"    Excerpt: {feedback[:250]}...")
        return False

    # Ideally mentions PDF/document
    if "pdf" in feedback.lower() or "document" in feedback.lower():
        print(f"  ✓ Correct: Feedback refers to PDF/document")
    else:
        print(f"  ✓ OK: No Python-file confusion (feedback may not explicitly say PDF)")

    print(f"\n  Feedback preview: {feedback[:180]}...")
    return True


def run_mental_state_test(base_url: str, wait_sec: int) -> bool:
    """Send activity + stuck EEG metrics, wait for threshold, check feedback."""
    print(f"  [1/4] Sending activity + EEG (stuck metrics)...")
    body = {
        "timestamp": time.time(),
        "context": {
            "app_name": "Cursor",
            "window_title": "Understanding transformers - Attention Is All You Need",
            "context_type": "file",
            "context_id": "test-stuck-e2e",
        },
        "streams": {
            "met": {"eng": 0.4, "attention": 0.4, "str": 0.7, "rel": 0.2},
        },
    }
    try:
        _post_eeg(base_url, body)
    except Exception as e:
        print(f"  POST /eeg failed: {e}")
        return False

    print(f"  [2/4] Keeping context alive for {wait_sec}s (threshold)...")
    for i in range(wait_sec):
        time.sleep(1)
        try:
            body["timestamp"] = time.time()
            body["streams"]["met"] = {"eng": 0.4, "attention": 0.4, "str": 0.7, "rel": 0.2}
            _post_eeg(base_url, body)
        except Exception:
            pass
        print(f"\r    {i+1}/{wait_sec}s  ", end="", flush=True)
    print()

    print("  [3/4] Checking GET /feedback...")
    time.sleep(1)
    try:
        feedback = _get_feedback(base_url)
        if feedback and len(feedback) > 20:
            print(f"\n  ✓ Agent feedback received ({len(feedback)} chars):")
            print(f"    {feedback[:200]}...")
            return True
        print(f"  ✗ No feedback (got: {repr(feedback)[:80]})")
        return False
    except Exception as e:
        print(f"  GET /feedback failed: {e}")
        return False


def main():
    p = argparse.ArgumentParser(description="Trigger agent and verify feedback")
    p.add_argument("--url", help="Processor base URL (default: from JETSON_WS_URL or localhost:8765)")
    p.add_argument(
        "--mental-state",
        action="store_true",
        help="Test stuck flow (needs LONG threshold). Use --short for 8s wait.",
    )
    p.add_argument(
        "--short",
        action="store_true",
        help="Use 8s threshold (set LONG_SESSION_THRESHOLD=8). Requires processor restart.",
    )
    p.add_argument("--pdf", action="store_true", help="Test PDF stuck flow (Preview + Neurable PDF, immediate trigger)")
    args = p.parse_args()

    base_url = _get_base_url(args.url)
    print(f"Processor: {base_url}\n")

    if not _HAS_URLLIB:
        print("Error: urllib not available")
        return 1

    if args.pdf:
        ok = run_pdf_stuck_test(base_url)
    elif args.mental_state:
        wait = 8 if args.short else 180
        if args.short:
            print("  Tip: Start processor with LONG_SESSION_THRESHOLD=8 for quick test:")
            print("       LONG_SESSION_THRESHOLD=8 WARN_SESSION_THRESHOLD=4 python processor_main.py\n")
        ok = run_mental_state_test(base_url, wait)
    else:
        ok = run_mental_command_test(base_url)

    print("\n" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
