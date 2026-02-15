"""
Task-specific context handlers.
Plug in MCPs to gather richer context per task type.
"""

from activity_tracker import ActivityContext

from .base import ContextHandler, EnrichedContext
from .web_reader import get_active_page_info


class PDFHandler(ContextHandler):
    """Context for PDF reading (Preview, Acrobat). Uses page content for stuck-point analysis."""

    name = "pdf"

    def applies_to(self, context_type: str, app_name: str) -> bool:
        return context_type == "pdf"

    def enrich(self, ctx: ActivityContext, **kwargs) -> EnrichedContext:
        page_content = getattr(ctx, "page_content", None) or ""
        section = getattr(ctx, "reading_section", None) or ""
        extra = (
            f"Task: reading PDF. "
            f"Document: {ctx.window_title or 'unknown'}. "
            f"Section: {section}. "
        )
        if page_content:
            extra += f"Page content available ({len(page_content)} chars). Use prepared resources for page-specific stuck-point analysis."
        else:
            extra += "Use doc title for related search."
        return EnrichedContext(
            extra_for_prompt=extra,
            handler_name=self.name,
            mcp_available=bool(page_content),
        )


class CodeHandler(ContextHandler):
    """
    Context for coding (IDE, editor).
    MCP: filesystem - read open file, language, symbols.
    """
    name = "code"

    def applies_to(self, context_type: str, app_name: str) -> bool:
        return context_type == "file" or app_name.lower() in (
            "cursor", "code", "vim", "gvim", "sublime", "gedit", "kate",
        )

    def enrich(self, ctx: ActivityContext, **kwargs) -> EnrichedContext:
        # TODO: MCP filesystem - read current file, get language, extract symbols
        # For now: infer file path from window title (e.g. "main.py - project")
        title = ctx.window_title or ""
        parts = title.split(" - ")
        file_hint = parts[0].strip() if parts else ""
        extra = (
            f"Task: coding/editing. "
            f"File (from title): {file_hint or 'unknown'}. "
            f"When stuck: offer code explanation, suggest debug steps, or point to docs."
        )
        if file_hint and ("." in file_hint):
            ext = file_hint.split(".")[-1].lower()
            extra += f" Extension: .{ext}."
        return EnrichedContext(
            extra_for_prompt=extra,
            handler_name=self.name,
            mcp_available=False,
        )


class BrowserHandler(ContextHandler):
    """
    Context for web browsing. Reads active tab URL and optionally page content.
    Firefox: sessionstore; Chrome: TODO.
    """
    name = "browser"

    def __init__(self, fetch_page_content: bool = True):
        self.fetch_page_content = fetch_page_content

    def applies_to(self, context_type: str, app_name: str) -> bool:
        app = app_name.lower()
        return context_type in ("website", "browser") or any(
            b in app for b in ["firefox", "chrome", "chromium", "brave", "edge"]
        )

    def enrich(self, ctx: ActivityContext, **kwargs) -> EnrichedContext:
        info = get_active_page_info(ctx.app_name, fetch_content=self.fetch_page_content)
        if info:
            url = info.get("url", "")
            title = info.get("title", "")
            snippet = info.get("snippet", "")
            extra = (
                f"Task: web browsing. "
                f"URL: {url[:100]}. "
                f"Title: {title[:80] or 'unknown'}."
            )
            if snippet:
                extra += f" Page snippet: {snippet[:600]}..."
            extra += " Infer from this content what might block or confuse them; offer specific help."
        else:
            title = ctx.window_title or ""
            extra = (
                f"Task: reading. "
                f"Content: {title[:120] or 'unknown'}. "
                f"Infer from this topic what might block or confuse them (e.g. key concepts, dense sections); offer specific explanation or resources."
            )
        return EnrichedContext(
            extra_for_prompt=extra,
            handler_name=self.name,
            mcp_available=info is not None,
        )


class TerminalHandler(ContextHandler):
    """
    Context for terminal/shell.
    MCP: terminal - get current command, cwd, last output.
    """
    name = "terminal"

    def applies_to(self, context_type: str, app_name: str) -> bool:
        return context_type == "terminal" or any(
            t in app_name.lower() for t in ["terminal", "konsole", "gnome-terminal"]
        )

    def enrich(self, ctx: ActivityContext, **kwargs) -> EnrichedContext:
        # TODO: MCP terminal - get current command, cwd, last lines
        # For now: title sometimes shows path or command
        title = ctx.window_title or ""
        extra = (
            f"Task: terminal/shell. "
            f"Context (from title): {title[:80] or 'unknown'}. "
            f"When stuck: suggest command syntax, explain error, or offer alternatives."
        )
        return EnrichedContext(
            extra_for_prompt=extra,
            handler_name=self.name,
            mcp_available=False,
        )


class DefaultHandler(ContextHandler):
    """Fallback for generic apps (documents, email, etc.)."""
    name = "default"

    def applies_to(self, context_type: str, app_name: str) -> bool:
        return True

    def enrich(self, ctx: ActivityContext, **kwargs) -> EnrichedContext:
        extra = (
            f"Task: generic app. "
            f"Context: {ctx.app_name} - {ctx.window_title or 'unknown'}. "
            f"Offer general help or suggest a break if stuck."
        )
        return EnrichedContext(
            extra_for_prompt=extra,
            handler_name=self.name,
            mcp_available=False,
        )
