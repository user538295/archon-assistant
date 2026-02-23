"""Truncation strategies for long Claude output — S1.3."""
from abc import ABC, abstractmethod

from archon.config.loader import ConfigError


class TruncationStrategy(ABC):
    @abstractmethod
    def apply(self, text: str, max_len: int) -> list[str]:
        """Split text into chunks of at most max_len characters."""
        ...


class SplitStrategy(TruncationStrategy):
    """Splits text into chunks whose total length (label + content) ≤ max_len."""

    def apply(self, text: str, max_len: int) -> list[str]:
        if len(text) <= max_len:
            return [text]
        # Estimate N to compute label width, then derive content budget
        n_est = -(-len(text) // max_len)  # ceil division
        label_w = len(f"[{n_est}/{n_est}] ")
        content_max = max(1, max_len - label_w)
        raw = [text[i : i + content_max] for i in range(0, len(text), content_max)]
        n = len(raw)
        # One recomputation if actual N uses a wider label (digit boundary crossings)
        actual_label_w = len(f"[{n}/{n}] ")
        if actual_label_w > label_w:
            content_max = max(1, max_len - actual_label_w)
            raw = [text[i : i + content_max] for i in range(0, len(text), content_max)]
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
