"""Utility for measuring text size in various units."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import tiktoken as _tiktoken_type

VALID_SIZE_UNITS: frozenset[str] = frozenset(
    {"chars", "codepoints", "words", "tokens", "lines", "sentences"}
)

_tiktoken_enc: "_tiktoken_type.Encoding | None" = None


def _get_enc() -> "_tiktoken_type.Encoding":
    global _tiktoken_enc
    if _tiktoken_enc is None:
        import tiktoken  # lazy import

        _tiktoken_enc = tiktoken.get_encoding("cl100k_base")
    return _tiktoken_enc


def _count_tokens(text: str) -> int:
    enc = _get_enc()
    return len(enc.encode(text))


def _count_sentences(text: str) -> int:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return len([p for p in parts if p])


def format_size(text: str, unit: str) -> str:
    """Return a human-readable size string for *text* in the given *unit*.

    Note: in Python 3, len(str) returns the number of Unicode code points,
    so chars and codepoints are equivalent. The distinction is preserved
    for user-facing clarity; both avoid a grapheme-cluster dependency.
    """
    if unit not in VALID_SIZE_UNITS:
        raise ValueError(
            f"Unknown size_unit: {unit!r}. Valid: {', '.join(sorted(VALID_SIZE_UNITS))}"
        )
    if not text:
        return f"0 {unit}"

    if unit == "chars":
        # len(str) counts Unicode code points in Python 3
        return f"{len(text)} chars"
    if unit == "codepoints":
        # Identical to chars in Python 3 — both count Unicode code points
        return f"{len(text)} codepoints"
    if unit == "words":
        return f"{len(text.split())} words"
    if unit == "lines":
        count = len(text.splitlines()) or (0 if not text else 1)
        return f"{count} lines"
    if unit == "sentences":
        return f"{_count_sentences(text)} sentences"
    # unit == "tokens"
    return f"{_count_tokens(text)} tokens"
