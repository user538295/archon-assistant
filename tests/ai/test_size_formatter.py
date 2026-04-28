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


# ---------------------------------------------------------------------------
# measure_size — returns raw int count
# ---------------------------------------------------------------------------


def test_measure_size_chars():
    from archon.ai.size_formatter import measure_size

    assert measure_size("hello world", "chars") == 11


def test_measure_size_empty():
    from archon.ai.size_formatter import measure_size

    assert measure_size("", "chars") == 0


def test_measure_size_words():
    from archon.ai.size_formatter import measure_size

    assert measure_size("foo bar baz", "words") == 3


def test_measure_size_unknown_unit():
    from archon.ai.size_formatter import measure_size

    with pytest.raises(ValueError, match="Unknown size_unit"):
        measure_size("hello", "bytes")


# ---------------------------------------------------------------------------
# measure_size — extended unit coverage
# ---------------------------------------------------------------------------


def test_measure_size_codepoints_ascii():
    from archon.ai.size_formatter import measure_size

    assert measure_size("hello", "codepoints") == 5


def test_measure_size_codepoints_unicode():
    """len() counts Unicode code points, so emoji = 1 codepoint."""
    from archon.ai.size_formatter import measure_size

    assert measure_size("\U0001F600", "codepoints") == 1


def test_measure_size_chars_equals_codepoints():
    """chars and codepoints are equivalent in Python 3 (both count code points)."""
    from archon.ai.size_formatter import measure_size

    text = "caf\u00e9 \U0001F600"
    assert measure_size(text, "chars") == measure_size(text, "codepoints")


def test_measure_size_lines_single():
    from archon.ai.size_formatter import measure_size

    assert measure_size("hello world", "lines") == 1


def test_measure_size_lines_multi():
    from archon.ai.size_formatter import measure_size

    assert measure_size("line1\nline2\nline3", "lines") == 3


def test_measure_size_lines_trailing_newline():
    from archon.ai.size_formatter import measure_size

    # trailing newline does not add a phantom extra line (splitlines behaviour)
    assert measure_size("line1\nline2\n", "lines") == 2


def test_measure_size_sentences():
    from archon.ai.size_formatter import measure_size

    assert measure_size("Hello. World! How are you?", "sentences") == 3


def test_measure_size_tokens(monkeypatch):
    from archon.ai.size_formatter import measure_size
    import archon.ai.size_formatter as sf

    mock_enc = MagicMock()
    mock_enc.encode.return_value = [1, 2, 3, 4]
    monkeypatch.setattr(sf, "_tiktoken_enc", mock_enc)
    assert measure_size("four tokens here", "tokens") == 4


@pytest.mark.parametrize("unit", ["chars", "codepoints", "words", "lines", "sentences", "tokens"])
def test_measure_size_empty_all_units(unit, monkeypatch):
    """Empty string returns 0 for every unit (including tokens — early return fires before tiktoken import)."""
    import sys
    from archon.ai.size_formatter import measure_size
    import archon.ai.size_formatter as sf

    monkeypatch.setattr(sf, "_tiktoken_enc", None)
    monkeypatch.delitem(sys.modules, "tiktoken", raising=False)
    assert measure_size("", unit) == 0
