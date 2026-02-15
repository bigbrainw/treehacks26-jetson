"""
PDF page content extraction - macOS Preview.

Gets the open document path via AXDocument (AppleScript) and extracts
text from the current page using pypdf. Runs on Mac where the PDF file exists.
"""

import platform
import subprocess
from pathlib import Path
from typing import Optional


def _get_preview_document_path() -> Optional[str]:
    """
    Get the file path of the document open in Preview via System Events.
    Requires Accessibility permissions. Returns None on failure.
    """
    if platform.system() != "Darwin":
        return None
    try:
        script = '''
        tell application "System Events"
            tell process "Preview"
                if (count of windows) > 0 then
                    set docURL to value of attribute "AXDocument" of window 1
                    if docURL is not missing value and docURL is not "" then
                        return docURL
                    end if
                end if
            end tell
        end tell
        return ""
        '''
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        url = result.stdout.strip()
        if not url.startswith("file://"):
            return None
        from urllib.parse import unquote, urlparse
        parsed = urlparse(url)
        path = unquote(parsed.path) if parsed.path else None
        return path or None
    except Exception:
        return None


def extract_pdf_page_text(file_path: str, page_num: int) -> Optional[str]:
    """
    Extract text from a specific page of a PDF.
    Uses pypdf. Returns None if extraction fails.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader
        except ImportError:
            return None

    path = Path(file_path)
    if not path.exists() or not path.suffix.lower() == ".pdf":
        return None

    try:
        reader = PdfReader(str(path))
        if page_num < 1 or page_num > len(reader.pages):
            return None
        page = reader.pages[page_num - 1]
        text = page.extract_text()
        return (text or "").strip() if text else None
    except Exception:
        return None


def get_preview_page_content(page_num: int) -> Optional[str]:
    """
    Get text content of the current page in macOS Preview.
    Returns the extracted page text or None.
    """
    if platform.system() != "Darwin":
        return None
    path = _get_preview_document_path()
    if not path:
        return None
    return extract_pdf_page_text(path, page_num)
