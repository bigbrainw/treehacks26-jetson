"""Linux-specific activity detection using X11."""

import re
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


def get_active_window_x11() -> Optional[WindowInfo]:
    """
    Get the currently active window using xprop (X11).
    Returns None if not on X11 or if detection fails.
    """
    try:
        # Get active window ID
        result = subprocess.run(
            ["xprop", "-root", "_NET_ACTIVE_WINDOW"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        if result.returncode != 0:
            return None

        match = re.search(r"0x[0-9a-fA-F]+", result.stdout)
        if not match:
            return None

        window_id = match.group(0)

        # Get window properties
        props = subprocess.run(
            ["xprop", "-id", window_id, "WM_CLASS", "WM_NAME"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        if props.returncode != 0:
            return WindowInfo(app_name="unknown", window_title="", window_id=window_id)

        wm_class = ""
        wm_name = ""

        for line in props.stdout.strip().split("\n"):
            if "WM_CLASS" in line:
                # WM_CLASS(STRING) = "app_instance", "AppName"
                class_match = re.search(r'"([^"]+)",\s*"([^"]+)"', line)
                if class_match:
                    wm_class = class_match.group(2)  # Use class name, not instance
            elif "WM_NAME" in line:
                # WM_NAME(STRING) = "Window Title"
                name_match = re.search(r'=\s*"([^"]*)"', line)
                if name_match:
                    wm_name = name_match.group(1)

        app_name = wm_class or "unknown"
        # Clean up title - remove trailing app name if duplicated
        if wm_name.endswith(f" - {app_name}"):
            wm_name = wm_name[: -len(f" - {app_name}")].strip()

        return WindowInfo(
            app_name=app_name,
            window_title=wm_name,
            window_id=window_id,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def infer_context_type(app_name: str, window_title: str) -> str:
    """
    Infer whether this is an app, website, or file based on app/title.
    """
    app_lower = app_name.lower()
    title_lower = window_title.lower()

    # Browser detection - title often contains URL or site name
    browsers = ["firefox", "chrome", "chromium", "brave", "edge", "vivaldi", "opera"]
    if any(b in app_lower for b in browsers):
        if "http" in title_lower or "www." in title_lower or ".com" in title_lower:
            return "website"
        return "browser"

    # IDE/editor - usually editing files
    editors = ["code", "cursor", "vim", "gvim", "sublime", "gedit", "kate"]
    if any(e in app_lower for e in editors):
        return "file"

    # Terminal
    if "terminal" in app_lower or "gnome-terminal" in app_lower or "konsole" in app_lower:
        return "terminal"

    return "app"
