"""Tests for Classification schema and parser — Multi-Agent Task #1."""

import logging

import pytest

from archon.ai.classification import Classification, parse_classification


# ──────────────────────────────────────────────────────────────────
# Happy paths
# ──────────────────────────────────────────────────────────────────


def test_parse_chat_intent() -> None:
    result = parse_classification('{"intent": "chat", "confidence": 0.92}')
    assert result == Classification(intent="chat", confidence=0.92)


def test_parse_task_intent() -> None:
    result = parse_classification('{"intent": "task", "confidence": 0.85}')
    assert result == Classification(intent="task", confidence=0.85)


def test_extra_fields_ignored() -> None:
    raw = '{"intent": "chat", "confidence": 0.5, "reason": "greeting"}'
    result = parse_classification(raw)
    assert result == Classification(intent="chat", confidence=0.5)


# ──────────────────────────────────────────────────────────────────
# Malformed JSON
# ──────────────────────────────────────────────────────────────────


def test_malformed_json_defaults_to_task(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="archon"):
        result = parse_classification("not json at all")
    assert result == Classification(intent="task", confidence=0.0)
    assert "malformed" in caplog.text.lower() or "parse" in caplog.text.lower()


def test_empty_string_defaults_to_task(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="archon"):
        result = parse_classification("")
    assert result == Classification(intent="task", confidence=0.0)


def test_json_array_defaults_to_task(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="archon"):
        result = parse_classification('[1, 2, 3]')
    assert result == Classification(intent="task", confidence=0.0)


# ──────────────────────────────────────────────────────────────────
# Missing fields
# ──────────────────────────────────────────────────────────────────


def test_missing_intent_defaults_to_task(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="archon"):
        result = parse_classification('{"confidence": 0.9}')
    assert result == Classification(intent="task", confidence=0.0)


def test_missing_confidence_defaults_to_task(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="archon"):
        result = parse_classification('{"intent": "chat"}')
    assert result == Classification(intent="task", confidence=0.0)


# ──────────────────────────────────────────────────────────────────
# Invalid values
# ──────────────────────────────────────────────────────────────────


def test_invalid_intent_defaults_to_task(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="archon"):
        result = parse_classification('{"intent": "unknown", "confidence": 0.8}')
    assert result == Classification(intent="task", confidence=0.0)


def test_confidence_above_one_clamped() -> None:
    result = parse_classification('{"intent": "chat", "confidence": 1.5}')
    assert result.confidence == 1.0


def test_confidence_below_zero_clamped() -> None:
    result = parse_classification('{"intent": "task", "confidence": -0.3}')
    assert result.confidence == 0.0


def test_confidence_exactly_zero() -> None:
    result = parse_classification('{"intent": "task", "confidence": 0.0}')
    assert result == Classification(intent="task", confidence=0.0)


def test_confidence_exactly_one() -> None:
    result = parse_classification('{"intent": "chat", "confidence": 1.0}')
    assert result == Classification(intent="chat", confidence=1.0)
