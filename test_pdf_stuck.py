#!/usr/bin/env python3
"""
PDF Stuck Test — POST Preview + Neurable PDF pages to /eeg, check feedback.

Agent can take 30–90+ sec (WebSearch, LLM). Use --timeout 90 for ngrok.

Usage:
  python test_pdf_stuck.py --url https://YOUR_NGROK_URL
  python test_pdf_stuck.py --url http://localhost:8765 --timeout 60
  python test_pdf_stuck.py --url https://xxx.ngrok-free.app --timeout 90 --pages 5,12,20
"""

import argparse
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import config
    DEFAULT_URL = getattr(config, "JETSON_BASE", None) or "http://localhost:8765"
except Exception:
    DEFAULT_URL = "http://localhost:8765"


def send_stuck_request(
    jetson_url: str,
    page: int,
    total_pages: int = 42,
    attempt: int = 1,
    timeout: float = 90,
) -> tuple[requests.Response | None, dict]:
    """POST stuck trigger for a PDF page. Returns (response, context)."""
    url = jetson_url.rstrip("/") + "/eeg"
    window_title = f"Neurable_Whitepaper.pdf — Page {page} of {total_pages}"
    ctx = {
        "app_name": "Preview",
        "window_title": window_title,
        "context_type": "pdf",
        "context_id": f"Preview::{window_title}",
        "reading_section": f"Page {page} of {total_pages}",
        "duration_seconds": 45,
        "mental_state": "stuck",
    }
    body = {
        "timestamp": time.time(),
        "context": ctx,
        "streams": {"met": {"met": [True, 0.4, True, 0.5], "time": time.time()}},
    }
    headers = {"Content-Type": "application/json"}
    if "ngrok" in url.lower():
        headers["ngrok-skip-browser-warning"] = "1"

    r = requests.post(url, json=body, headers=headers, timeout=timeout)
    return r, ctx


def main():
    p = argparse.ArgumentParser(description="PDF stuck test — POST Preview+PDF to /eeg")
    p.add_argument("--url", default=DEFAULT_URL, help="Jetson base URL (default: config or localhost:8765)")
    p.add_argument("--pages", default="5,12,20", help="Comma-separated page numbers (default: 5,12,20)")
    p.add_argument("--timeout", type=float, default=90, help="Request timeout in seconds (default: 90 for agent)")
    p.add_argument("--total", type=int, default=42, help="Total pages in PDF (default: 42)")
    args = p.parse_args()

    if not requests:
        print("Error: pip install requests")
        return 1

    base = args.url.rstrip("/")
    pages = [int(x.strip()) for x in args.pages.split(",") if x.strip()]
    print("\n--- PDF Stuck Test ---")
    print(f"  File: Neurable_Whitepaper.pdf")
    print(f"  URL: {base}/eeg")
    print(f"  Pages: {pages}")
    print(f"  Timeout: {args.timeout}s per request\n")

    for i, page in enumerate(pages):
        print(f"[{i+1}] POST {base}/eeg")
        print(f"    App: Preview | File: Neurable_Whitepaper.pdf — Page {page} of {args.total} | Page: {page} | Page {page} of {args.total}")
        try:
            r, ctx = send_stuck_request(base, page, args.total, i + 1, timeout=args.timeout)
            print(f"    Response: {r.status_code}")
            if r.status_code == 200:
                data = r.json() if r.text else {}
                feedback = data.get("feedback", "")
                if feedback:
                    print(f"    Feedback: {feedback[:100]}...")
                else:
                    print(f"    Feedback: (empty)")
            else:
                print(f"    Error: {r.text[:200]}")
        except requests.exceptions.Timeout:
            print(f"    ERROR: Request timed out after {args.timeout}s (agent/LLM slow). Try --timeout 120")
            return 1
        except Exception as e:
            print(f"    ERROR: {e}")
            return 1

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
