"""Truncation strategies for long Claude output — S1.3."""
from abc import ABC, abstractmethod

from archon.config.loader import ConfigError


class TruncationStrategy(ABC):
    @abstractmethod
    def apply(self, text: str, max_len: int) -> list[str]:
        """Split text into chunks of at most max_len characters."""
        ...


class SplitStrategy(TruncationStrategy):
    """Splits text into ≤max_len content chunks, labeled [1/N] … [N/N]."""

    def apply(self, text: str, max_len: int) -> list[str]:
        if len(text) <= max_len:
            return [text]
        raw = [text[i : i + max_len] for i in range(0, len(text), max_len)]
        n = len(raw)
        return [f"[{i + 1}/{n}] {chunk}" for i, chunk in enumerate(raw)]


_STRATEGIES: dict[str, type[TruncationStrategy]] = {
    "split": SplitStrategy,
}


def get_truncation_strategy(name: str) -> TruncationStrategy:
    """Return a TruncationStrategy instance selected by name."""
    cls = _STRATEGIES.get(name)
    if cls is None:
        raise ConfigError(f"Unknown truncation strategy: {name!r}")
    return cls()
