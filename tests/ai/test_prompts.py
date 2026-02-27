"""Tests for prompt file loader — Multi-Agent Task #2."""

import pytest

from archon.ai.prompts import load_prompt


def test_load_classifier_prompt() -> None:
    content = load_prompt("classifier")
    assert len(content) > 0


def test_load_decomposer_prompt() -> None:
    content = load_prompt("decomposer")
    assert len(content) > 0


def test_load_nonexistent_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_prompt("nonexistent")


def test_prompts_are_utf8_strings() -> None:
    for name in ("classifier", "decomposer"):
        content = load_prompt(name)
        assert isinstance(content, str)
