"""
Read active browser tab URL and optionally page content when user is browsing.

Firefox: sessionstore-backups/recovery.jsonlz4
Chrome: Session Storage (more complex)
"""

import glob
import json
import os
import re
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError


def _get_firefox_active_tab() -> Optional[tuple[str, str]]:
    """
    Get (url, title) of active tab in Firefox.
    Returns None if not Firefox or parsing fails.
    """
    home = Path.home()
    pattern = home / ".mozilla/firefox/*default*/sessionstore-backups/recovery.jsonlz4"
    files = sorted(glob.glob(str(pattern)), key=os.path.getmtime, reverse=True)
    if not files:
        pattern = home / ".mozilla/firefox/*/sessionstore-backups/recovery.jsonlz4"
        files = sorted(glob.glob(str(pattern)), key=os.path.getmtime, reverse=True)
    for fpath in files[:2]:
        try:
            data = _decompress_mozlz4(fpath)
            if not data:
                continue
            j = json.loads(data)
            windows = j.get("windows", [])
            if not windows:
                continue
            win_idx = j.get("selectedWindow", 0)
            win = windows[win_idx] if win_idx < len(windows) else windows[0]
            tabs = win.get("tabs", [])
            tab_idx = win.get("selected", win.get("index", 1))
            if isinstance(tab_idx, int):
                tab_idx = tab_idx - 1 if tab_idx > 0 else 0
            tab_idx = max(0, min(tab_idx, len(tabs) - 1)) if tabs else 0
            tab = tabs[tab_idx]
            entries = tab.get("entries", [])
            if not entries:
                continue
            entry = entries[-1]
            url = entry.get("url", "")
            title = entry.get("title", "") or tab.get("label", "")
            if url and url.startswith(("http://", "https://")):
                return (url, title or url[:60])
        except Exception:
            continue
    return None


def _decompress_mozlz4(filepath: str) -> Optional[str]:
    """Decompress Mozilla jsonlz4 format."""
    try:
        import lz4.block
    except ImportError:
        return None
    try:
        with open(filepath, "rb") as f:
            header = f.read(8)
            if header[:8] != b"mozLz40\x00":
                return None
            compressed = f.read()
        decompressed = lz4.block.decompress(compressed, uncompressed_size=len(compressed) * 4)
        return decompressed.decode("utf-8", errors="replace")
    except Exception:
        return None


def _fetch_page_text(url: str, max_chars: int = 4000) -> Optional[str]:
    """Fetch URL and extract plain text. Returns None on failure."""
    try:
        req = Request(url, headers={"User-Agent": "FocusAgent/1.0"})
        with urlopen(req, timeout=5) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        text = _strip_html(html)
        return (text[:max_chars] + "...") if len(text) > max_chars else text
    except (URLError, OSError, ValueError):
        return None


def _strip_html(html: str) -> str:
    """Simple HTML to text extraction."""
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def get_active_page_info(
    app_name: str,
    fetch_content: bool = True,
) -> Optional[dict]:
    """
    Get URL and optionally content of what the user is reading.
    Returns dict with: url, title, snippet (text preview).
    """
    app = app_name.lower()
    if "firefox" in app:
        result = _get_firefox_active_tab()
    else:
        return None
    if not result:
        return None
    url, title = result
    out = {"url": url, "title": title, "snippet": None}
    if fetch_content:
        snippet = _fetch_page_text(url)
        out["snippet"] = snippet
    return out
