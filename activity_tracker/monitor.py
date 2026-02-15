"""
ActivityContext - data structure for received activity (no monitoring).
Data arrives via WebSocket/HTTP; this module only defines the structure.
"""

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ActivityContext:
    """Represents the user's current activity context (from received data)."""

    app_name: str
    window_title: str
    context_type: str  # "app" | "website" | "file" | "browser" | "terminal" | "pdf"
    context_id: str
    detected_at: float = field(default_factory=time.time)
    reading_section: Optional[str] = None  # Section of paper/doc user is on
    page_content: Optional[str] = None  # Extracted text from current PDF page

    @property
    def display_name(self) -> str:
        """Human-readable description."""
        if self.context_type == "website" and self.window_title:
            return f"{self.app_name}: {self.window_title[:60]}..."
        if self.window_title:
            return f"{self.app_name} - {self.window_title[:60]}"
        return self.app_name
