"""Shared helpers for Telegram-safe message rendering and splitting."""

from collections.abc import Callable

from archon.ai.truncation import TruncationStrategy


def render_split_messages(
    text: str,
    prefix: str,
    truncation: TruncationStrategy,
    max_len: int,
    renderer: Callable[[str], str],
) -> list[str]:
    """Split text so the final rendered Telegram messages stay within max_len."""
    rendered = f"{prefix}{renderer(text)}"
    if len(rendered) <= max_len:
        return [rendered]

    budget_hi = max(1, max_len - len(prefix))
    budget_lo = 1
    best: list[str] | None = None

    while budget_lo <= budget_hi:
        budget = (budget_lo + budget_hi) // 2
        candidate = [
            f"{prefix}{renderer(chunk)}" for chunk in truncation.apply(text, budget)
        ]
        if all(len(message) <= max_len for message in candidate):
            best = candidate
            budget_lo = budget + 1
        else:
            budget_hi = budget - 1

    if best is not None:
        return best

    # Defensive fallback for absurdly small max_len values; real Telegram limits
    # are much larger, so the first character should always fit in practice.
    return [f"{prefix}{renderer(text[:1])}"]
