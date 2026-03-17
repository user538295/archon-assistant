"""Tests for TruncationStrategy — S1.3."""
import pytest

from archon.ai.truncation import SplitStrategy, get_truncation_strategy


def test_single_chunk_no_split() -> None:
    """Text fits within max_len — returned unchanged as a single item."""
    result = SplitStrategy().apply("hello", max_len=100)
    assert result == ["hello"]


def test_exact_fit_single_item() -> None:
    """Text of exactly max_len is returned as a single item."""
    text = "B" * 100
    result = SplitStrategy().apply(text, max_len=100)
    assert result == [text]


def test_multi_chunk_count() -> None:
    """250 chars with max_len=100 produces 3 chunks."""
    result = SplitStrategy().apply("A" * 250, max_len=100)
    assert len(result) == 3


def test_label_format() -> None:
    """Multi-chunk labels follow the [i/N] prefix format."""
    # 100 chars / max_len=60: label_w=6, content_max=54, ceil(100/54)=2 chunks
    result = SplitStrategy().apply("A" * 100, max_len=60)
    assert len(result) == 2
    assert result[0].startswith("[1/2] ")
    assert result[1].startswith("[2/2] ")


def test_total_chunk_length_within_max_len() -> None:
    """Each chunk including its label does not exceed max_len."""
    max_len = 100
    result = SplitStrategy().apply("X" * 300, max_len=max_len)
    for chunk in result:
        assert len(chunk) <= max_len


def test_total_chunk_length_within_max_len_at_digit_boundary() -> None:
    """Chunks stay within max_len even when N crosses a digit boundary (e.g. 9→10)."""
    max_len = 1000
    # 9000 chars: est N=9, but after label-width adjustment N can become 10
    result = SplitStrategy().apply("X" * 9000, max_len=max_len)
    for chunk in result:
        assert len(chunk) <= max_len


def test_chunks_reconstruct_original_text() -> None:
    """All chunk content concatenated equals the original text."""
    text = "Z" * 250
    result = SplitStrategy().apply(text, max_len=100)
    reconstructed = "".join(chunk.split("] ", 1)[1] for chunk in result)
    assert reconstructed == text


def test_get_truncation_strategy_split() -> None:
    """get_truncation_strategy('split') returns a SplitStrategy instance."""
    assert isinstance(get_truncation_strategy("split"), SplitStrategy)


def test_get_truncation_strategy_unknown_raises() -> None:
    """get_truncation_strategy raises ConfigError for unknown names."""
    from archon.config.loader import ConfigError
    with pytest.raises(ConfigError):
        get_truncation_strategy("unknown")


# ──────────────────────────────────────────────────────────────────
# Empty string input — Medium gap
# ──────────────────────────────────────────────────────────────────


def test_empty_string_returns_single_item() -> None:
    """Empty string fits within any max_len and is returned as a single-element list."""
    result = SplitStrategy().apply("", max_len=100)
    assert result == [""]


def test_empty_string_returns_list_of_length_one() -> None:
    result = SplitStrategy().apply("", max_len=4000)
    assert len(result) == 1


# ──────────────────────────────────────────────────────────────────
# Boundary tests — T5
# ──────────────────────────────────────────────────────────────────


def test_max_len_zero_raises_zero_division() -> None:
    """max_len=0: ceil division by zero is not guarded — raises ZeroDivisionError."""
    with pytest.raises(ZeroDivisionError):
        SplitStrategy().apply("abc", max_len=0)


def test_max_len_one_still_produces_chunks() -> None:
    """max_len=1: extremely small limit, content_max clamped to 1."""
    result = SplitStrategy().apply("ab", max_len=1)
    assert len(result) >= 1
    reconstructed = "".join(chunk.split("] ", 1)[1] for chunk in result)
    assert reconstructed == "ab"


def test_max_len_negative_still_produces_chunks() -> None:
    """max_len=-1: negative limit, content_max clamped to 1."""
    result = SplitStrategy().apply("abc", max_len=-1)
    assert len(result) >= 1
    reconstructed = "".join(chunk.split("] ", 1)[1] for chunk in result)
    assert reconstructed == "abc"


def test_multibyte_unicode_preserves_characters() -> None:
    """Multi-byte Unicode characters must not be split mid-character."""
    # Mix of 1-byte, 2-byte, 3-byte, and 4-byte characters
    text = "Hello\u00e9\u4e16\U0001f600" * 5  # 40 chars worth of multi-byte
    result = SplitStrategy().apply(text, max_len=20)
    assert len(result) >= 2
    reconstructed = "".join(chunk.split("] ", 1)[1] for chunk in result)
    assert reconstructed == text


def test_newline_only_content() -> None:
    """Content consisting only of newlines splits correctly."""
    text = "\n" * 50
    result = SplitStrategy().apply(text, max_len=20)
    assert len(result) >= 2
    reconstructed = "".join(chunk.split("] ", 1)[1] for chunk in result)
    assert reconstructed == text
