"""ContextProvider protocol — minimal interface for history context injection."""
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class ContextProvider(Protocol):
    """Read-only history context interface consumed by SessionManager."""

    def get_recent_context(self) -> str | None:
        """Return recent compacted summaries, or None if none exist."""
        ...

    def get_context_files(self) -> list[Path]:
        """Return the files loaded by the most recent get_recent_context() call."""
        ...

    def startup_context_prompt(self, search_enabled: bool = False) -> str:
        """Return the system prompt explaining history structure to the LLM."""
        ...
