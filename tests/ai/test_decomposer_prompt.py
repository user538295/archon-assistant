"""Tests for decomposer prompt content — FIX-032."""
from archon.ai.prompts import load_prompt

FALLBACK_SECTION = "## Fallback Strategy When Tools Fail"
COMMITMENT_SECTION = "## Commitment = Immediate Action"


def test_decomposer_prompt_contains_fallback_section() -> None:
    content = load_prompt("decomposer")
    assert FALLBACK_SECTION in content


def test_decomposer_prompt_contains_commitment_section() -> None:
    content = load_prompt("decomposer")
    assert COMMITMENT_SECTION in content


def test_decomposer_prompt_loads_without_error() -> None:
    content = load_prompt("decomposer")
    assert isinstance(content, str)
    assert len(content) > 0
    assert FALLBACK_SECTION in content
    assert COMMITMENT_SECTION in content
