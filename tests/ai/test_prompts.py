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
    for name in ("classifier", "decomposer", "route_task"):
        content = load_prompt(name)
        assert isinstance(content, str)


# ── Prompt content validation ──────────────────────────────────


def test_route_task_prompt_contains_scope_heuristics() -> None:
    content = load_prompt("route_task")
    assert "small" in content.lower()
    assert "large" in content.lower()


def test_route_task_prompt_contains_plan_json_schema() -> None:
    content = load_prompt("route_task")
    assert '"scope": "large"' in content
    assert '"agents"' in content
    assert '"depends_on"' in content


def test_decomposer_prompt_is_simplified() -> None:
    content = load_prompt("decomposer")
    assert "Phase 1 scope" not in content
    assert "Do not attempt to delegate" not in content


def test_system_reminder_contains_file_size_warning() -> None:
    content = load_prompt("system_reminder")
    assert "500 KB" in content
    assert "Read tool" in content
