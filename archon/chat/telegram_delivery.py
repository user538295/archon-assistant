"""Shared helpers for Telegram-safe message rendering and splitting."""

import logging
from collections.abc import Callable

from archon.ai.truncation import TruncationStrategy

_log = logging.getLogger("archon")


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

    # Defensive fallback: binary search found no valid split (absurdly small
    # max_len or content that expands massively under rendering).  Emit a clear
    # placeholder so the user knows something was discarded rather than silently
    # receiving a single character.
    notice = f"[Content too large to display — {len(text)} chars]"
    _log.error(
        "render_split_messages: could not fit content into max_len=%d; "
        "content length=%d; emitting truncation notice",
        max_len,
        len(text),
    )
    return [f"{prefix}{notice}"]
