"""macOS-specific activity detection using AppleScript."""

import subprocess
import time
from dataclasses import dataclass
from typing import Optional

from activity_tracker import ActivityContext


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


def get_reading_section_macos(app_name: str, window_title: str) -> Optional[str]:
    """
    Try to get which section/region the user is reading.
    Uses: selected text, browser URL (incl. #anchor), or focused text snippet.
    Returns None if not detectable. Requires Accessibility permission.
    """
    result: Optional[str] = None

    # 1. Browser: get URL (may contain #section or path like /docs/section)
    app_lower = app_name.lower()
    if any(b in app_lower for b in ["chrome", "safari", "firefox", "edge", "brave"]):
        url = _get_browser_url_macos(app_name)
        if url:
            # Use path + hash as section hint (e.g. /docs/api#auth, #heading-2)
            if "#" in url:
                result = url.split("#", 1)[1].strip() or url[:80]
            else:
                result = url[:120] if len(url) > 120 else url
            return result

    # 2. Selected text (strong signal – user is reading/selecting)
    selected = _get_selected_text_macos()
    if selected and len(selected.strip()) > 2:
        # First line or first 100 chars as section hint
        first_line = selected.split("\n")[0].strip()
        return (first_line[:100] + "…") if len(first_line) > 100 else first_line

    # 3. Focused element value (e.g. current paragraph in editor)
    focused_val = _get_focused_value_macos()
    if focused_val and len(focused_val.strip()) > 5:
        first_line = focused_val.split("\n")[0].strip()
        return (first_line[:80] + "…") if len(first_line) > 80 else first_line

    return result


def _get_browser_url_macos(app_name: str) -> Optional[str]:
    """Get current tab URL from browser via AppleScript."""
    try:
        app_map = {
            "google chrome": "get URL of active tab of front window",
            "chrome": "get URL of active tab of front window",
            "safari": "get URL of current tab of front window",
            "firefox": "get URL of active tab of front window",
            "microsoft edge": "get URL of active tab of front window",
            "brave browser": "get URL of active tab of front window",
        }
        cmd = app_map.get(app_name.lower())
        if not cmd:
            return None
        script = f'tell application "{app_name}" to {cmd}'
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if r.returncode == 0 and r.stdout and r.stdout.strip().startswith(("http", "file")):
            return r.stdout.strip()
    except Exception:
        pass
    return None


def _get_selected_text_macos() -> Optional[str]:
    """Get AXSelectedText via AppleScript. Requires Accessibility permission."""
    try:
        script = """
        tell application "System Events"
            set frontApp to first process whose frontmost is true
            try
                set foc to focused element of front window of frontApp
                if foc is not missing value then
                    set v to value of attribute "AXSelectedText" of foc
                    if v is not missing value and v is not "" then
                        return v as text
                    end if
                end if
            end try
        end tell
        return ""
        """
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=2)
        if r.returncode == 0 and r.stdout and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return None


def _get_focused_value_macos() -> Optional[str]:
    """Get AXValue of focused element (e.g. text field content)."""
    try:
        script = """
        tell application "System Events"
            set frontApp to first process whose frontmost is true
            try
                set foc to focused element of front window of frontApp
                if foc is not missing value then
                    set v to value of attribute "AXValue" of foc
                    if v is not missing value and v is not "" then
                        return v as text
                    end if
                end if
            end try
        end tell
        return ""
        """
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=2)
        if r.returncode == 0 and r.stdout and len(r.stdout.strip()) > 2:
            return r.stdout.strip()
    except Exception:
        pass
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


# Feedback overlay identifiers — never report as current activity (use last real)
_OVERLAY_APP_NAMES = ("python", "python3", "tk", "tcl")
_OVERLAY_TITLE_MARKER = "agent feedback"


def _is_overlay_window(win: WindowInfo) -> bool:
    """True if this window is the Focus Agent feedback overlay."""
    if not win:
        return False
    app = (win.app_name or "").lower().strip()
    title = (win.window_title or "").lower().strip()
    if _OVERLAY_TITLE_MARKER in title:
        return True
    if app in _OVERLAY_APP_NAMES and "agent" in title and "feedback" in title:
        return True
    return False


class ActivityMonitor:
    """
    macOS activity monitor. Polls frontmost app/window and returns ActivityContext.
    Filters out the feedback overlay — when overlay is frontmost, returns last real context.
    """

    def __init__(self, poll_interval: float = 0.5):
        self.poll_interval = poll_interval
        self._last_real: Optional[WindowInfo] = None

    def get_current_activity(self) -> Optional[ActivityContext]:
        """Get current frontmost window as ActivityContext. Overlay → last real context."""
        win = get_active_window_macos()
        if win and _is_overlay_window(win):
            win = self._last_real
        elif win:
            self._last_real = win
        if not win:
            return None
        context_type = infer_context_type(win.app_name, win.window_title)
        reading_section = get_reading_section_macos(win.app_name, win.window_title)
        return ActivityContext(
            app_name=win.app_name,
            window_title=win.window_title,
            context_type=context_type,
            context_id=win.context_id,
            reading_section=reading_section,
        )
