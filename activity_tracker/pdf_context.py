"""
Parse PDF context from window title (e.g. Preview - Neurable_Whitepaper.pdf – Page 7 of 21).
"""

import re
from typing import Optional


def parse_pdf_window_title(app_name: str, window_title: str) -> Optional[dict]:
    """
    Parse PDF viewer window title to extract doc name and page.
    Supports: "Doc.pdf – Page 7 of 21", "Doc.pdf - Page 7 of 21"
    Returns dict with: doc_name, page_num, total_pages, reading_section
    or None if not a PDF viewer title.
    """
    app_lower = app_name.lower()
    if "preview" not in app_lower and "acrobat" not in app_lower and "evince" not in app_lower:
        return None

    title = (window_title or "").strip()
    if not title or ".pdf" not in title.lower():
        return None

    # Match: "filename.pdf – Page 7 of 21" or "filename.pdf - Page 7 of 21" (en/em dash, hyphen)
    m = re.search(r"(.+?\.pdf)\s*[\u2013\u2014\-]\s*Page\s+(\d+)\s+of\s+(\d+)", title, re.I)
    if m:
        doc_name = m.group(1).strip()
        page_num = int(m.group(2))
        total_pages = int(m.group(3))
        return {
            "doc_name": doc_name,
            "page_num": page_num,
            "total_pages": total_pages,
            "reading_section": f"Page {page_num} of {total_pages}",
        }

    # Fallback: just doc name, no page
    if ".pdf" in title.lower():
        parts = re.split(r"\s*[\u2013\u2014\-]\s*", title, maxsplit=1)
        doc_name = (parts[0] or "").strip()
        if doc_name:
            return {
                "doc_name": doc_name,
                "page_num": 1,
                "total_pages": 1,
                "reading_section": "Page 1",
            }
    return None


def infer_pdf_context_type(app_name: str, window_title: str) -> str:
    """Return 'pdf' if this is a PDF viewer with PDF in title."""
    if parse_pdf_window_title(app_name, window_title):
        return "pdf"
    return "app"


def infer_context_type(app_name: str, window_title: str) -> str:
    """
    Infer context type from app_name and window_title.
    Use when building ActivityContext from incoming data (no monitoring).
    """
    app_lower = app_name.lower()
    title_lower = (window_title or "").lower()

    if infer_pdf_context_type(app_name, window_title) == "pdf":
        return "pdf"

    browsers = ["safari", "chrome", "firefox", "brave", "edge", "opera", "chromium", "vivaldi"]
    if any(b in app_lower for b in browsers):
        if "http" in title_lower or "www." in title_lower or ".com" in title_lower:
            return "website"
        return "browser"

    editors = ["cursor", "visual studio code", "code", "vim", "sublime", "gvim", "gedit", "kate"]
    if any(e in app_lower for e in editors):
        return "file"

    if any(t in app_lower for t in ["terminal", "iterm", "gnome-terminal", "konsole"]):
        return "terminal"

    return "app"
