"""Tests for Classification schema and parser — Multi-Agent Task #1."""

import logging

import pytest

from archon.ai.classification import Classification, ClassificationResult, parse_classification


# ──────────────────────────────────────────────────────────────────
# Happy paths
# ──────────────────────────────────────────────────────────────────


def test_parse_chat_intent() -> None:
    result = parse_classification('{"intent": "chat", "confidence": 0.92}')
    assert result.classification == Classification(intent="chat", confidence=0.92)


def test_parse_task_intent() -> None:
    result = parse_classification('{"intent": "task", "confidence": 0.85}')
    assert result.classification == Classification(intent="task", confidence=0.85)


def test_extra_fields_ignored() -> None:
    raw = '{"intent": "chat", "confidence": 0.5, "reason": "greeting"}'
    result = parse_classification(raw)
    assert result.classification == Classification(intent="chat", confidence=0.5)


# ──────────────────────────────────────────────────────────────────
# Malformed JSON
# ──────────────────────────────────────────────────────────────────


def test_malformed_json_defaults_to_task(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="archon"):
        result = parse_classification("not json at all")
    assert result.classification == Classification(intent="task", confidence=0.0)
    assert "malformed" in caplog.text.lower() or "parse" in caplog.text.lower()


def test_empty_string_defaults_to_task(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="archon"):
        result = parse_classification("")
    assert result.classification == Classification(intent="task", confidence=0.0)


def test_json_array_defaults_to_task(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="archon"):
        result = parse_classification('[1, 2, 3]')
    assert result.classification == Classification(intent="task", confidence=0.0)


# ──────────────────────────────────────────────────────────────────
# Missing fields
# ──────────────────────────────────────────────────────────────────


def test_missing_intent_defaults_to_task(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="archon"):
        result = parse_classification('{"confidence": 0.9}')
    assert result.classification == Classification(intent="task", confidence=0.0)


def test_missing_confidence_defaults_to_task(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="archon"):
        result = parse_classification('{"intent": "chat"}')
    assert result.classification == Classification(intent="task", confidence=0.0)


# ──────────────────────────────────────────────────────────────────
# Invalid values
# ──────────────────────────────────────────────────────────────────


def test_invalid_intent_defaults_to_task(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="archon"):
        result = parse_classification('{"intent": "unknown", "confidence": 0.8}')
    assert result.classification == Classification(intent="task", confidence=0.0)


def test_confidence_above_one_clamped() -> None:
    result = parse_classification('{"intent": "chat", "confidence": 1.5}')
    assert result.classification.confidence == 1.0


def test_confidence_below_zero_clamped() -> None:
    result = parse_classification('{"intent": "task", "confidence": -0.3}')
    assert result.classification.confidence == 0.0


def test_confidence_exactly_zero() -> None:
    result = parse_classification('{"intent": "task", "confidence": 0.0}')
    assert result.classification == Classification(intent="task", confidence=0.0)


def test_confidence_exactly_one() -> None:
    result = parse_classification('{"intent": "chat", "confidence": 1.0}')
    assert result.classification == Classification(intent="chat", confidence=1.0)


# ──────────────────────────────────────────────────────────────────
# JSON extraction from mixed text (Bug 2 fix)
# ──────────────────────────────────────────────────────────────────


def test_json_wrapped_in_markdown_fences() -> None:
    """Classifier sometimes wraps JSON in markdown code fences."""
    raw = '```json\n{"intent": "task", "confidence": 0.95}\n```'
    result = parse_classification(raw)
    assert result.classification == Classification(intent="task", confidence=0.95)


def test_json_with_preamble_text() -> None:
    """Classifier sometimes adds preamble text before JSON."""
    raw = 'Here is the classification:\n{"intent": "chat", "confidence": 0.8}'
    result = parse_classification(raw)
    assert result.classification == Classification(intent="chat", confidence=0.8)


def test_json_with_trailing_text() -> None:
    """Classifier sometimes adds explanation after JSON."""
    raw = '{"intent": "task", "confidence": 0.9}\nThis is a task because...'
    result = parse_classification(raw)
    assert result.classification == Classification(intent="task", confidence=0.9)


def test_json_surrounded_by_prose() -> None:
    """Classifier wraps JSON in explanatory text on both sides."""
    raw = 'Based on analysis:\n{"intent": "chat", "confidence": 0.75}\nThe user is greeting.'
    result = parse_classification(raw)
    assert result.classification == Classification(intent="chat", confidence=0.75)


def test_json_in_markdown_fences_without_lang() -> None:
    """Markdown fences without language specifier."""
    raw = '```\n{"intent": "task", "confidence": 0.6}\n```'
    result = parse_classification(raw)
    assert result.classification == Classification(intent="task", confidence=0.6)


def test_no_json_object_at_all_defaults() -> None:
    """When there's truly no JSON object, should still default."""
    result = parse_classification("I think this is a chat message")
    assert result.classification == Classification(intent="task", confidence=0.0)


# ──────────────────────────────────────────────────────────────────
# Log messages must never leak raw content (security)
# ──────────────────────────────────────────────────────────────────


def test_parse_returns_classification_result() -> None:
    """parse_classification returns a ClassificationResult with classification and error."""
    result = parse_classification('{"intent": "task", "confidence": 0.9}')
    assert isinstance(result, ClassificationResult)
    assert result.classification == Classification(intent="task", confidence=0.9)
    assert result.error is None


def test_parse_error_set_on_failure() -> None:
    """parse_classification sets error message on parse failure."""
    result = parse_classification("not json at all")
    assert result.classification == Classification(intent="task", confidence=0.0)
    assert result.error is not None
    assert "no JSON object found" in result.error


def test_parse_error_set_on_malformed_json() -> None:
    """parse_classification sets error for extractable but invalid JSON."""
    result = parse_classification("{broken: json}")
    assert result.error is not None


def test_parse_error_none_on_success() -> None:
    """parse_classification returns None error on successful parse."""
    result = parse_classification('{"intent": "chat", "confidence": 0.5}')
    assert result.error is None


def test_warning_does_not_leak_raw_content_no_json(caplog: pytest.LogCaptureFixture) -> None:
    """When no JSON is found, the warning must not contain the raw input."""
    sensitive = "You're absolutely right. That's a fair and important criticism."
    with caplog.at_level(logging.WARNING, logger="archon"):
        parse_classification(sensitive)
    assert sensitive not in caplog.text
    assert sensitive[:30] not in caplog.text


def test_warning_does_not_leak_raw_content_malformed(caplog: pytest.LogCaptureFixture) -> None:
    """When JSON extraction yields malformed JSON, raw input must not leak."""
    sensitive = "Sure! Here is the answer: {broken json content"
    with caplog.at_level(logging.WARNING, logger="archon"):
        parse_classification(sensitive)
    assert sensitive not in caplog.text
    assert "broken json content" not in caplog.text


def test_nested_braces_extracts_outermost() -> None:
    """When JSON contains nested objects, extract the outermost valid one."""
    raw = '{"intent": "task", "confidence": 0.85, "meta": {"reason": "complex"}}'
    result = parse_classification(raw)
    assert result.classification == Classification(intent="task", confidence=0.85)
