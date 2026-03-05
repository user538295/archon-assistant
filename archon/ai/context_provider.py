"""ContextProvider protocol — minimal interface for history context injection."""
from typing import Protocol


class ContextProvider(Protocol):
    """Read-only history context interface consumed by SessionManager."""

    def get_recent_context(self) -> str | None:
        """Return recent compacted summaries, or None if none exist."""
        ...

    def startup_context_prompt(self, qmd_enabled: bool = False) -> str:
        """Return the system prompt explaining history structure to the LLM."""
        ...
