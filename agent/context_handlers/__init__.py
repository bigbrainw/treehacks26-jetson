"""
Task-specific context handlers - different MCPs/agents per context type.

Each handler understands a specific task (coding, browsing, terminal, etc.)
and can gather richer context via MCPs or tools for the LLM.
"""

from .base import ContextHandler, EnrichedContext
from .router import ContextRouter
from .handlers import (
    CodeHandler,
    BrowserHandler,
    TerminalHandler,
    DefaultHandler,
)

__all__ = [
    "ContextHandler",
    "EnrichedContext",
    "ContextRouter",
    "CodeHandler",
    "BrowserHandler",
    "TerminalHandler",
    "DefaultHandler",
]
