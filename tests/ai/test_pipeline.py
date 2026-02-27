"""Tests for Pipeline — Multi-Agent Task #3."""

import logging
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.classification import Classification
from archon.ai.event_mapper import (
    ClassificationEvent,
    ErrorEvent,
    Response,
    ThinkingResult,
    ToolStarted,
)
from archon.ai.pipeline import Pipeline


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _mock_session(*events, is_processing=False):
    """Build a mock ClaudeSession that yields given events from send()."""
    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.is_processing = is_processing
    session.processing_seconds = None
    session.idle_seconds = 5.0
    session.send_count = 0
    session.usage_stats = None
    session.diagnostics = {"is_alive": True}

    session._send_calls: list[str] = []

    async def _send(prompt: str) -> AsyncGenerator:
        session._send_calls.append(prompt)
        for event in events:
            yield event

    session.send = _send
    session.activate_skill = MagicMock()
    session.inject_context = MagicMock()
    session.recent_events = MagicMock(return_value=[])
    return session


def _make_pipeline(classifier_events=None, decomposer_events=None):
    """Build a Pipeline with mocked Classifier and Decomposer sessions."""
    if classifier_events is None:
        classifier_events = [Response(content='{"intent": "task", "confidence": 0.9}')]
    if decomposer_events is None:
        decomposer_events = [Response(content="Done.")]

    mock_classifier = _mock_session(*classifier_events)
    mock_decomposer = _mock_session(*decomposer_events)

    with patch("archon.ai.pipeline.ClaudeSession", side_effect=[mock_classifier, mock_decomposer]):
        with patch("archon.ai.pipeline.load_prompt", return_value="mock prompt"):
            pipeline = Pipeline()

    return pipeline, mock_classifier, mock_decomposer


# ──────────────────────────────────────────────────────────────────
# Routing logic
# ──────────────────────────────────────────────────────────────────


async def test_send_calls_classifier_then_decomposer() -> None:
    pipeline, classifier, decomposer = _make_pipeline()
    events = [e async for e in pipeline.send("hello")]

    # Classifier must have been called with the user prompt
    assert len(classifier._send_calls) == 1
    assert "hello" in classifier._send_calls[0]

    # Decomposer must have been called
    assert len(decomposer._send_calls) == 1


async def test_send_yields_classification_event() -> None:
    pipeline, _, _ = _make_pipeline(
        classifier_events=[Response(content='{"intent": "chat", "confidence": 0.85}')],
    )
    events = [e async for e in pipeline.send("hi")]

    classification_events = [e for e in events if isinstance(e, ClassificationEvent)]
    assert len(classification_events) == 1
    assert classification_events[0].intent == "chat"
    assert classification_events[0].confidence == 0.85


async def test_send_yields_decomposer_events() -> None:
    pipeline, _, _ = _make_pipeline(
        decomposer_events=[ThinkingResult(content="thinking..."), Response(content="answer")],
    )
    events = [e async for e in pipeline.send("question")]

    # Should contain ClassificationEvent + decomposer events
    types = [type(e).__name__ for e in events]
    assert "ClassificationEvent" in types
    assert "ThinkingResult" in types
    assert "Response" in types


async def test_send_does_not_yield_classifier_internal_events() -> None:
    """Classifier's ThinkingResult/ToolStarted should NOT leak to the user."""
    pipeline, _, _ = _make_pipeline(
        classifier_events=[
            ThinkingResult(content="classifying..."),
            Response(content='{"intent": "task", "confidence": 0.7}'),
        ],
    )
    events = [e async for e in pipeline.send("do something")]

    # Only the parsed ClassificationEvent should come from the classifier, not its ThinkingResult
    thinking_events = [e for e in events if isinstance(e, ThinkingResult)]
    # The only ThinkingResult should be from the decomposer (none in this case)
    assert all(e.content != "classifying..." for e in thinking_events)


async def test_malformed_classifier_json_defaults_to_task(caplog: pytest.LogCaptureFixture) -> None:
    pipeline, _, _ = _make_pipeline(
        classifier_events=[Response(content="I cannot classify this")],
    )
    with caplog.at_level(logging.WARNING, logger="archon"):
        events = [e async for e in pipeline.send("test")]

    classification_events = [e for e in events if isinstance(e, ClassificationEvent)]
    assert len(classification_events) == 1
    assert classification_events[0].intent == "task"
    assert classification_events[0].confidence == 0.0


# ──────────────────────────────────────────────────────────────────
# Lifecycle
# ──────────────────────────────────────────────────────────────────


async def test_start_starts_both_sessions() -> None:
    pipeline, classifier, decomposer = _make_pipeline()
    await pipeline.start()

    classifier.start.assert_awaited_once()
    decomposer.start.assert_awaited_once()


async def test_stop_stops_both_sessions() -> None:
    pipeline, classifier, decomposer = _make_pipeline()
    await pipeline.stop()

    classifier.stop.assert_awaited_once()
    decomposer.stop.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────
# Delegation (duck-typing surface)
# ──────────────────────────────────────────────────────────────────


def test_is_processing_delegates_to_decomposer() -> None:
    pipeline, _, decomposer = _make_pipeline()
    decomposer.is_processing = True
    assert pipeline.is_processing is True


def test_processing_seconds_delegates_to_decomposer() -> None:
    pipeline, _, decomposer = _make_pipeline()
    decomposer.processing_seconds = 12.5
    assert pipeline.processing_seconds == 12.5


def test_idle_seconds_delegates_to_decomposer() -> None:
    pipeline, _, decomposer = _make_pipeline()
    decomposer.idle_seconds = 3.0
    assert pipeline.idle_seconds == 3.0


def test_diagnostics_delegates_to_decomposer() -> None:
    pipeline, _, decomposer = _make_pipeline()
    decomposer.diagnostics = {"is_alive": True, "send_count": 5}
    assert pipeline.diagnostics == {"is_alive": True, "send_count": 5}


def test_usage_stats_delegates_to_decomposer() -> None:
    pipeline, _, decomposer = _make_pipeline()
    decomposer.usage_stats = {"total_cost_usd": 0.05}
    assert pipeline.usage_stats == {"total_cost_usd": 0.05}


def test_send_count_delegates_to_decomposer() -> None:
    pipeline, _, decomposer = _make_pipeline()
    decomposer.send_count = 7
    assert pipeline.send_count == 7


def test_recent_events_delegates_to_decomposer() -> None:
    pipeline, _, decomposer = _make_pipeline()
    decomposer.recent_events.return_value = [(1.0, Response(content="hi"))]
    assert len(pipeline.recent_events(5)) == 1


def test_activate_skill_delegates_to_decomposer() -> None:
    pipeline, _, decomposer = _make_pipeline()
    skill = MagicMock()
    pipeline.activate_skill(skill)
    decomposer.activate_skill.assert_called_once_with(skill)


def test_inject_context_delegates_to_decomposer() -> None:
    pipeline, _, decomposer = _make_pipeline()
    pipeline.inject_context("some context")
    decomposer.inject_context.assert_called_once_with("some context")


def test_is_alive_delegates_to_decomposer() -> None:
    pipeline, _, decomposer = _make_pipeline()
    decomposer.is_alive = True
    assert pipeline.is_alive is True


def test_model_delegates_to_decomposer() -> None:
    pipeline, _, decomposer = _make_pipeline()
    decomposer.model = "claude-sonnet-4-5"
    assert pipeline.model == "claude-sonnet-4-5"
