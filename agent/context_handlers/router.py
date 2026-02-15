"""
Routes to the right context handler based on task type.
Web search/synthesis via Claude Agent SDK (WebSearch, WebFetch) when user is stuck.
"""

from typing import Optional

from activity_tracker import ActivityContext

from .base import ContextHandler, EnrichedContext
from .handlers import CodeHandler, BrowserHandler, TerminalHandler, PDFHandler, DefaultHandler

try:
    import config
    _fetch_web = getattr(config, "FETCH_WEB_CONTENT", True)
except ImportError:
    _fetch_web = True


class ContextRouter:
    """
    Picks the right handler for the current context.
    Order matters: more specific handlers first.
    """

    def __init__(self, fetch_web_content: Optional[bool] = None):
        fetch = fetch_web_content if fetch_web_content is not None else _fetch_web
        self._handlers = [
            PDFHandler(),
            CodeHandler(),
            BrowserHandler(fetch_page_content=fetch),
            TerminalHandler(),
            DefaultHandler(),
        ]

    def add_handler(self, handler: ContextHandler, index: int = 0):
        """Register a custom handler (inserted before default)."""
        self._handlers.insert(index, handler)

    def route(self, ctx: ActivityContext, skip_web_enrichment: bool = False) -> tuple[ContextHandler, EnrichedContext]:
        """Find handler and enrich context. (skip_web_enrichment kept for API compat.)"""
        for h in self._handlers:
            if h.applies_to(ctx.context_type, ctx.app_name):
                enriched = h.enrich(ctx)
                return h, enriched
        return DefaultHandler(), DefaultHandler().enrich(ctx)
