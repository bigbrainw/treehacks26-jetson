"""Base interface for task-specific context handlers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

from activity_tracker import ActivityContext


@dataclass
class EnrichedContext:
    """Context enriched by a task-specific handler (for LLM prompt)."""
    extra_for_prompt: str       # Additional text to add to LLM prompt
    handler_name: str           # Which handler produced this
    mcp_available: bool = False # Whether MCP was used (for debugging)


class ContextHandler(ABC):
    """
    Base for task-specific context handlers.
    Each handler can use MCPs or tools to gather deeper context.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Handler identifier."""
        pass

    @abstractmethod
    def applies_to(self, context_type: str, app_name: str) -> bool:
        """Whether this handler applies to the current context."""
        pass

    @abstractmethod
    def enrich(self, ctx: ActivityContext, **kwargs: Any) -> EnrichedContext:
        """
        Gather task-specific context (e.g. via MCP).
        Returns extra info to include in the LLM prompt.
        """
        pass
