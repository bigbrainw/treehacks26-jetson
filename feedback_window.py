"""
Small overlay window to display agent feedback to the user.
Polls GET /feedback from the Jetson processor; expects {"feedback": "message"}.
"""

import json
import threading
import time
from pathlib import Path

try:
    import urllib.request
    _URLLIB_AVAILABLE = True
except ImportError:
    _URLLIB_AVAILABLE = False

import tkinter as tk

sys_path = Path(__file__).resolve().parent
if str(sys_path) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(sys_path))

try:
    import config
    ws_url = getattr(config, "JETSON_WS_URL", None) or "ws://localhost:8765"
    if getattr(config, "FEEDBACK_POLL_URL", None):
        FEEDBACK_URL = config.FEEDBACK_POLL_URL
    else:
        # Derive from WebSocket: wss://xxx.ngrok-free.app -> https://xxx.ngrok-free.app/feedback
        base = ws_url.replace("ws://", "http://").replace("wss://", "https://").rstrip("/")
        if "/ws" in base:
            base = base.split("/ws")[0]
        FEEDBACK_URL = f"{base}/feedback"
except Exception:
    FEEDBACK_URL = "http://localhost:8765/feedback"


class FeedbackWindow:
    """Floating window showing the latest agent feedback."""

    def __init__(self, width: int = 320, height: int = 120, poll_url: str | None = None, use_poll: bool = True):
        self.root = tk.Tk()
        self.root.title("Agent Feedback")
        self.root.resizable(True, True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#1a1a2e", highlightbackground="#16213e")
        x = self.root.winfo_screenwidth() - width - 20
        self.root.geometry(f"{width}x{height}+{x}+20")

        self._poll_url = poll_url or FEEDBACK_URL
        self._last_feedback = ""
        self._polling = True
        self._use_poll = use_poll

        # Content frame
        frame = tk.Frame(self.root, bg="#1a1a2e", padx=12, pady=12)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            frame,
            text="Agent",
            font=("Helvetica", 10),
            fg="#a0a0a0",
            bg="#1a1a2e",
        ).pack(anchor=tk.W)

        self.feedback_label = tk.Label(
            frame,
            text="Waiting for feedback...",
            font=("Helvetica", 13),
            fg="#e8e8e8",
            bg="#1a1a2e",
            wraplength=width - 32,
            justify=tk.LEFT,
            anchor=tk.W,
        )
        self.feedback_label.pack(anchor=tk.W, fill=tk.BOTH, expand=True)

    def update_feedback(self, text: str, allow_clear: bool = False) -> None:
        """Update the displayed feedback. Thread-safe via root.after(0, ...).
        By default, empty text does NOT clear—keeps last result visible."""
        def _set():
            if text or allow_clear:
                self.feedback_label.config(text=text or "—")
        try:
            self.root.after(0, _set)
        except tk.TclError:
            pass

    def _poll(self) -> None:
        """Background thread: poll GET /feedback and update window."""
        while self._polling and _URLLIB_AVAILABLE:
            try:
                req = urllib.request.Request(self._poll_url)
                if "ngrok" in self._poll_url.lower():
                    req.add_header("ngrok-skip-browser-warning", "1")
                with urllib.request.urlopen(req, timeout=5) as r:
                    data = json.loads(r.read().decode())
                    msg = (data.get("feedback") or "").strip()
                    if msg and msg != self._last_feedback:
                        self._last_feedback = msg
                        self.update_feedback(msg)
            except Exception:
                pass
            time.sleep(2)

    def run(self) -> None:
        """Start poll thread (if use_poll) and mainloop."""
        if self._use_poll and _URLLIB_AVAILABLE:
            t = threading.Thread(target=self._poll, daemon=True)
            t.start()
        self.root.mainloop()

    def stop(self) -> None:
        """Stop polling (call before destroying)."""
        self._polling = False


def create_and_run_feedback_window(poll_url: str | None = None) -> FeedbackWindow:
    """Create the window and run. Polls Jetson GET /feedback for agent messages."""
    w = FeedbackWindow(poll_url=poll_url)
    w.run()
    return w


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Agent feedback overlay - polls Jetson /feedback")
    p.add_argument("--url", default=FEEDBACK_URL, help="URL to poll, e.g. http://JETSON_IP:8765/feedback")
    args = p.parse_args()
    create_and_run_feedback_window(poll_url=args.url)
