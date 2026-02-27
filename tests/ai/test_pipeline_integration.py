"""Integration tests for Pipeline — Multi-Agent Task #4.

Verifies that classification is prepended to the Decomposer prompt
so the Decomposer can adjust its behavior based on intent.
"""

from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.event_mapper import ClassificationEvent, Response, ThinkingResult
from archon.ai.pipeline import Pipeline


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _mock_session(*events):
    """Build a mock ClaudeSession that yields given events from send()."""
    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.is_processing = False
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
# Classification is prepended to Decomposer prompt
# ──────────────────────────────────────────────────────────────────


async def test_decomposer_receives_classification_prefix() -> None:
    """Decomposer prompt must start with classification JSON."""
    pipeline, _, decomposer = _make_pipeline(
        classifier_events=[Response(content='{"intent": "task", "confidence": 0.92}')],
    )
    _ = [e async for e in pipeline.send("write a test")]

    assert len(decomposer._send_calls) == 1
    prompt = decomposer._send_calls[0]
    assert '"intent": "task"' in prompt
    assert '"confidence": 0.92' in prompt


async def test_decomposer_receives_original_user_message() -> None:
    """Decomposer prompt must contain the original user message."""
    pipeline, _, decomposer = _make_pipeline()
    _ = [e async for e in pipeline.send("hello world")]

    prompt = decomposer._send_calls[0]
    assert "hello world" in prompt


async def test_decomposer_prompt_has_classification_before_user_message() -> None:
    """Classification must appear before the user message in the prompt."""
    pipeline, _, decomposer = _make_pipeline(
        classifier_events=[Response(content='{"intent": "chat", "confidence": 0.85}')],
    )
    _ = [e async for e in pipeline.send("how are you")]

    prompt = decomposer._send_calls[0]
    classification_pos = prompt.find('"intent"')
    user_msg_pos = prompt.find("how are you")
    assert classification_pos < user_msg_pos


async def test_chat_intent_prepended_for_conversational_message() -> None:
    """Chat classification is correctly prepended."""
    pipeline, _, decomposer = _make_pipeline(
        classifier_events=[Response(content='{"intent": "chat", "confidence": 0.95}')],
    )
    _ = [e async for e in pipeline.send("hi")]

    prompt = decomposer._send_calls[0]
    assert '"intent": "chat"' in prompt
    assert "hi" in prompt


async def test_task_intent_prepended_for_action_message() -> None:
    """Task classification is correctly prepended."""
    pipeline, _, decomposer = _make_pipeline(
        classifier_events=[Response(content='{"intent": "task", "confidence": 0.88}')],
    )
    _ = [e async for e in pipeline.send("refactor the auth module")]

    prompt = decomposer._send_calls[0]
    assert '"intent": "task"' in prompt
    assert "refactor the auth module" in prompt


async def test_malformed_classifier_defaults_prepend_task() -> None:
    """When classifier returns garbage, default task intent is prepended."""
    pipeline, _, decomposer = _make_pipeline(
        classifier_events=[Response(content="not json")],
    )
    _ = [e async for e in pipeline.send("do something")]

    prompt = decomposer._send_calls[0]
    assert '"intent": "task"' in prompt
    assert '"confidence": 0.0' in prompt
    assert "do something" in prompt


async def test_classification_event_still_yielded() -> None:
    """ClassificationEvent must still be yielded alongside the prepended prompt."""
    pipeline, _, _ = _make_pipeline(
        classifier_events=[Response(content='{"intent": "chat", "confidence": 0.75}')],
    )
    events = [e async for e in pipeline.send("hey")]

    classification_events = [e for e in events if isinstance(e, ClassificationEvent)]
    assert len(classification_events) == 1
    assert classification_events[0].intent == "chat"
    assert classification_events[0].confidence == 0.75
