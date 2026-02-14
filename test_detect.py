#!/usr/bin/env python3
"""
Test real activity detection - see what window you're actually looking at.

Run this, then switch between apps (browser, Cursor, terminal, etc.).
Verify the agent detects each one correctly.

Usage: python test_detect.py
"""

import signal
import sys
import time

from activity_tracker import ActivityMonitor

def main():
    monitor = ActivityMonitor(poll_interval=0.5)
    last_id = None
    running = True

    def stop(_, __):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, stop)

    print("Detecting what you're looking at. Switch between apps to test.")
    print("(Ctrl+C to stop)\n")

    while running:
        ctx = monitor.get_current_activity()
        if ctx:
            if ctx.context_id != last_id:
                last_id = ctx.context_id
                print(f"  App:      {ctx.app_name}")
                print(f"  Title:    {ctx.window_title[:70]}" + ("..." if len(ctx.window_title) > 70 else ""))
                print(f"  Type:     {ctx.context_type}")
                print(f"  >>> Looking at: {ctx.display_name[:60]}")
                print()
            else:
                print(f"\r  ► {ctx.display_name[:72]}   ", end="", flush=True)
        else:
            print("\r  (no window detected - X11? headless?)          ", end="", flush=True)
        time.sleep(0.5)

    print("\nDone.")


if __name__ == "__main__":
    main()
    sys.exit(0)
