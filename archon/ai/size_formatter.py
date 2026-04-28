"""Utility for measuring text size in various units."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal, get_args

if TYPE_CHECKING:
    import tiktoken as _tiktoken_type

SizeUnit = Literal["chars", "codepoints", "words", "tokens", "lines", "sentences"]
VALID_SIZE_UNITS: frozenset[str] = frozenset(get_args(SizeUnit))

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


def measure_size(text: str, unit: str) -> int:
    """Return the raw numeric size of text in the given unit.

    Note: in Python 3, len(str) returns the number of Unicode code points,
    so chars and codepoints are equivalent. The distinction is preserved
    for user-facing clarity; both avoid a grapheme-cluster dependency.
    """
    if unit not in VALID_SIZE_UNITS:
        raise ValueError(
            f"Unknown size_unit: {unit!r}. Valid: {', '.join(sorted(VALID_SIZE_UNITS))}"
        )
    if not text:
        return 0
    if unit == "chars":
        return len(text)
    if unit == "codepoints":
        return len(text)
    if unit == "words":
        return len(text.split())
    if unit == "lines":
        return len(text.splitlines())
    if unit == "sentences":
        return _count_sentences(text)
    # unit == "tokens"
    return _count_tokens(text)


def format_size(text: str, unit: str) -> str:
    """Return a human-readable size string for *text* in the given *unit*."""
    return f"{measure_size(text, unit)} {unit}"
