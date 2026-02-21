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
    result = SplitStrategy().apply("A" * 200, max_len=100)
    assert result[0].startswith("[1/2] ")
    assert result[1].startswith("[2/2] ")


def test_chunk_content_within_max_len() -> None:
    """Content portion of each chunk (after label) does not exceed max_len."""
    max_len = 100
    result = SplitStrategy().apply("X" * 300, max_len=max_len)
    for chunk in result:
        content = chunk.split("] ", 1)[1]
        assert len(content) <= max_len


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
