"""Tests for archon/ai/size_formatter.py — TDD: tests written before implementation."""

import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _remove_tiktoken(monkeypatch):
    """Remove tiktoken from sys.modules so lazy-import state is reset."""
    monkeypatch.delitem(sys.modules, "tiktoken", raising=False)


# ---------------------------------------------------------------------------
# format_size — basic units
# ---------------------------------------------------------------------------


def test_format_size_chars():
    from archon.ai.size_formatter import format_size

    assert format_size("hello world", "chars") == "11 chars"


def test_format_size_codepoints_ascii():
    from archon.ai.size_formatter import format_size

    assert format_size("hello", "codepoints") == "5 codepoints"


def test_format_size_codepoints_unicode():
    from archon.ai.size_formatter import format_size

    assert format_size("café", "codepoints") == "4 codepoints"


def test_format_size_words():
    from archon.ai.size_formatter import format_size

    assert format_size("foo bar baz", "words") == "3 words"


def test_format_size_lines_single():
    from archon.ai.size_formatter import format_size

    assert format_size("hello", "lines") == "1 lines"


def test_format_size_lines_multi():
    from archon.ai.size_formatter import format_size

    assert format_size("a\nb\nc", "lines") == "3 lines"


def test_format_size_lines_trailing_newline():
    from archon.ai.size_formatter import format_size

    # splitlines() does not include the empty string after a trailing newline
    assert format_size("a\nb\n", "lines") == "2 lines"


def test_format_size_sentences():
    from archon.ai.size_formatter import format_size

    assert format_size("Hello. World!", "sentences") == "2 sentences"


def test_format_size_sentences_abbreviation():
    from archon.ai.size_formatter import format_size

    # Deliberate imprecision: "Dr." is treated as a sentence boundary
    assert format_size("Dr. Smith went home.", "sentences") == "2 sentences"


# ---------------------------------------------------------------------------
# Unicode / special cases
# ---------------------------------------------------------------------------


def test_format_size_codepoints_zwj():
    from archon.ai.size_formatter import format_size

    # 👨‍👩‍👧‍👦 is a ZWJ sequence: 4 emoji + 3 ZWJ = 7 codepoints, 1 visual glyph
    assert format_size("👨\u200d👩\u200d👧\u200d👦", "codepoints") == "7 codepoints"


# ---------------------------------------------------------------------------
# Empty string fast-path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("unit", ["chars", "codepoints", "words", "tokens", "lines", "sentences"])
def test_format_size_empty_all_units(unit, monkeypatch):
    _remove_tiktoken(monkeypatch)
    from archon.ai import size_formatter

    # Reset module-level encoder cache so the empty fast-path is hit before tiktoken loads
    size_formatter._tiktoken_enc = None

    from archon.ai.size_formatter import format_size

    assert format_size("", unit) == f"0 {unit}"


# ---------------------------------------------------------------------------
# Tokens (mocked tiktoken)
# ---------------------------------------------------------------------------


def test_format_size_tokens(monkeypatch):
    _remove_tiktoken(monkeypatch)

    mock_enc = MagicMock()
    mock_enc.encode.return_value = [1, 2, 3]  # 3 tokens

    mock_tiktoken = MagicMock()
    mock_tiktoken.get_encoding.return_value = mock_enc

    monkeypatch.setitem(sys.modules, "tiktoken", mock_tiktoken)

    from archon.ai import size_formatter

    size_formatter._tiktoken_enc = None  # reset cached encoder

    from archon.ai.size_formatter import format_size

    assert format_size("abc", "tokens") == "3 tokens"
    mock_tiktoken.get_encoding.assert_called_once_with("cl100k_base")


# ---------------------------------------------------------------------------
# Unknown unit
# ---------------------------------------------------------------------------


def test_format_size_unknown_unit():
    from archon.ai.size_formatter import format_size

    with pytest.raises(ValueError, match="Unknown size_unit"):
        format_size("hello", "bytes")


# ---------------------------------------------------------------------------
# Lazy-import: tiktoken must NOT be imported for non-token units
# ---------------------------------------------------------------------------


def test_tiktoken_not_imported_for_chars(monkeypatch):
    _remove_tiktoken(monkeypatch)

    from archon.ai.size_formatter import format_size

    format_size("x", "chars")
    assert "tiktoken" not in sys.modules


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def test_count_tokens_private(monkeypatch):
    _remove_tiktoken(monkeypatch)

    mock_enc = MagicMock()
    mock_enc.encode.return_value = [10, 20]

    mock_tiktoken = MagicMock()
    mock_tiktoken.get_encoding.return_value = mock_enc

    monkeypatch.setitem(sys.modules, "tiktoken", mock_tiktoken)

    from archon.ai import size_formatter

    size_formatter._tiktoken_enc = None

    from archon.ai.size_formatter import _count_tokens

    assert _count_tokens("hi") == 2


def test_count_sentences_private():
    from archon.ai.size_formatter import _count_sentences

    assert _count_sentences("Hello. World! How are you?") == 3
