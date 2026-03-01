"""Tests for Classifier — standalone classification wrapper."""

import time
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.classification import Classification, ClassificationResult
from archon.ai.event_mapper import Response, ThinkingResult


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _mock_session(*events, is_processing=False):
    """Build a mock ClaudeSession that yields given events from send()."""
    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.is_processing = is_processing

    async def _send(prompt: str) -> AsyncGenerator:
        for event in events:
            yield event

    session.send = _send
    return session


def _make_classifier(session_events=None):
    """Build a Classifier with a mocked session."""
    from archon.ai.classifier import Classifier

    if session_events is None:
        session_events = [Response(content='{"intent": "task", "confidence": 0.9}')]

    mock_session = _mock_session(*session_events)

    with patch("archon.ai.classifier.ClaudeSession", return_value=mock_session):
        with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
            classifier = Classifier()

    return classifier, mock_session


# ──────────────────────────────────────────────────────────────────
# classify() returns valid ClassificationResult
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_returns_valid_classification() -> None:
    classifier, _ = _make_classifier(
        session_events=[Response(content='{"intent": "chat", "confidence": 0.85}')],
    )
    result = await classifier.classify("hello")
    assert result.classification == Classification(intent="chat", confidence=0.85)


@pytest.mark.asyncio
async def test_classify_returns_task_for_task_intent() -> None:
    classifier, _ = _make_classifier(
        session_events=[Response(content='{"intent": "task", "confidence": 0.95}')],
    )
    result = await classifier.classify("refactor the auth module")
    assert result.classification.intent == "task"
    assert result.classification.confidence == 0.95


# ──────────────────────────────────────────────────────────────────
# classify() defaults on bad JSON
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_returns_default_on_bad_json() -> None:
    classifier, _ = _make_classifier(
        session_events=[Response(content="I cannot classify this")],
    )
    result = await classifier.classify("test")
    assert result.classification == Classification(intent="task", confidence=0.0)
    assert result.parse_error != ""


@pytest.mark.asyncio
async def test_classify_returns_default_when_no_response() -> None:
    """When session yields events but no Response, default to task."""
    classifier, _ = _make_classifier(
        session_events=[ThinkingResult(content="hmm")],
    )
    result = await classifier.classify("test")
    assert result.classification == Classification(intent="task", confidence=0.0)


# ──────────────────────────────────────────────────────────────────
# classify() defaults on crash
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_returns_default_on_crash() -> None:
    from archon.ai.classifier import Classifier

    mock_session = MagicMock()
    mock_session.start = AsyncMock()
    mock_session.stop = AsyncMock()

    async def _crashing_send(prompt: str):
        raise RuntimeError("SDK connection lost")
        yield  # noqa: E501

    mock_session.send = _crashing_send

    with patch("archon.ai.classifier.ClaudeSession", return_value=mock_session):
        with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
            classifier = Classifier()

    result = await classifier.classify("test")
    assert result.classification == Classification(intent="task", confidence=0.0)
    assert result.error != ""


# ──────────────────────────────────────────────────────────────────
# classify() includes timing
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_includes_timing() -> None:
    classifier, _ = _make_classifier()
    result = await classifier.classify("test")
    assert result.duration_s >= 0.0


@pytest.mark.asyncio
async def test_classify_includes_raw_response() -> None:
    raw = '{"intent": "chat", "confidence": 0.85}'
    classifier, _ = _make_classifier(
        session_events=[Response(content=raw)],
    )
    result = await classifier.classify("hi")
    assert result.raw_response == raw


@pytest.mark.asyncio
async def test_classify_raw_response_empty_on_crash() -> None:
    from archon.ai.classifier import Classifier

    mock_session = MagicMock()
    mock_session.start = AsyncMock()
    mock_session.stop = AsyncMock()

    async def _crashing_send(prompt: str):
        raise RuntimeError("boom")
        yield  # noqa: E501

    mock_session.send = _crashing_send

    with patch("archon.ai.classifier.ClaudeSession", return_value=mock_session):
        with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
            classifier = Classifier()

    result = await classifier.classify("test")
    assert result.raw_response == ""


# ──────────────────────────────────────────────────────────────────
# Session created with Haiku, no tools, max_turns=1
# ──────────────────────────────────────────────────────────────────


def test_session_created_with_haiku_model() -> None:
    from archon.ai.classifier import Classifier

    with patch("archon.ai.classifier.ClaudeSession") as MockSession:
        MockSession.return_value = MagicMock()
        with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
            Classifier()

    call_kwargs = MockSession.call_args.kwargs
    assert call_kwargs["model"] == "claude-haiku-4-5-20251001"


def test_session_created_with_no_tools() -> None:
    from archon.ai.classifier import Classifier

    with patch("archon.ai.classifier.ClaudeSession") as MockSession:
        MockSession.return_value = MagicMock()
        with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
            Classifier()

    call_kwargs = MockSession.call_args.kwargs
    assert call_kwargs["tools"] == []


def test_session_created_with_max_turns_one() -> None:
    from archon.ai.classifier import Classifier

    with patch("archon.ai.classifier.ClaudeSession") as MockSession:
        MockSession.return_value = MagicMock()
        with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
            Classifier()

    call_kwargs = MockSession.call_args.kwargs
    assert call_kwargs["max_turns"] == 1


# ──────────────────────────────────────────────────────────────────
# Lifecycle
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_starts_session() -> None:
    classifier, mock_session = _make_classifier()
    await classifier.start()
    mock_session.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_stops_session() -> None:
    classifier, mock_session = _make_classifier()
    await classifier.stop()
    mock_session.stop.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────
# model property
# ──────────────────────────────────────────────────────────────────


def test_model_returns_haiku() -> None:
    from archon.ai.classifier import Classifier, _CLASSIFIER_MODEL
    classifier, _ = _make_classifier()
    assert classifier.model == _CLASSIFIER_MODEL
