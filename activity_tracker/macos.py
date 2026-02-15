"""macOS-specific activity detection using AppleScript."""

import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class WindowInfo:
    """Info about the currently focused window."""
    app_name: str
    window_title: str
    window_id: Optional[str] = None

    @property
    def context_id(self) -> str:
        """Unique ID for this context (app + page/title)."""
        return f"{self.app_name}::{self.window_title}"


def get_active_window_macos() -> Optional[WindowInfo]:
    """
    Get the currently active window using AppleScript (System Events).
    Returns None if detection fails.
    """
    try:
        # Get app name and window title via System Events (works for most apps)
        script = """
        tell application "System Events"
            set frontApp to first application process whose frontmost is true
            set appName to name of frontApp
            try
                set windowName to ""
                if (count of windows of frontApp) > 0 then
                    set windowName to name of front window of frontApp
                end if
                return appName & "|||" & windowName
            on error
                return appName & "|||"
            end try
        end tell
        """
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None

        parts = result.stdout.strip().split("|||", 1)
        app_name = parts[0].strip() if parts else "unknown"
        window_title = parts[1].strip() if len(parts) > 1 else ""

        return WindowInfo(
            app_name=app_name or "unknown",
            window_title=window_title,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def infer_context_type(app_name: str, window_title: str) -> str:
    """
    Infer whether this is an app, website, or file based on app/title.
    """
    app_lower = app_name.lower()
    title_lower = window_title.lower()

    browsers = ["safari", "chrome", "firefox", "brave", "edge", "opera"]
    if any(b in app_lower for b in browsers):
        if "http" in title_lower or "www." in title_lower or ".com" in title_lower:
            return "website"
        return "browser"

    editors = ["cursor", "visual studio code", "code", "vim", "sublime"]
    if any(e in app_lower for e in editors):
        return "file"

    if "terminal" in app_lower or "iterm" in app_lower:
        return "terminal"

    return "app"
