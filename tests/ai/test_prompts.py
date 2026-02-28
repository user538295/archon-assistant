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


# ── Phase 2 Task #3: Decomposer prompt scope decision ────────


def test_decomposer_prompt_contains_scope_heuristics() -> None:
    content = load_prompt("decomposer")
    assert "small" in content.lower()
    assert "large" in content.lower()


def test_decomposer_prompt_contains_plan_json_schema() -> None:
    content = load_prompt("decomposer")
    assert '"scope": "large"' in content
    assert '"agents"' in content
    assert '"depends_on"' in content


def test_decomposer_prompt_no_phase1_restriction() -> None:
    content = load_prompt("decomposer")
    assert "Phase 1 scope" not in content
    assert "Do not attempt to delegate" not in content
