"""Integration tests for Pipeline — Classifier + Decomposer routing flow.

Verifies that classification info flows correctly through the pipeline
to the Decomposer, using the new Classifier/Decomposer architecture:
- Classification is yielded as ClassificationEvent
- Low confidence (<0.8) triggers Decomposer.review()
- High confidence (>=0.8) routes directly via the classification result
"""

import json
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.agent_plan import AgentTask
from archon.ai.classification import Classification
from archon.ai.classifier import ClassifierResult
from archon.ai.decomposer import ReviewResult, TaskOutput
from archon.ai.event_mapper import ClassificationEvent, Response, ReviewEvent, RoutingEvent
from archon.ai.pipeline import Pipeline


# ------------------------------------------------------------------
# Helpers (same pattern as test_pipeline.py)
# ------------------------------------------------------------------


def _mock_classifier(intent="task", confidence=0.9, error="", parse_error="", raw=None):
    """Build a mock Classifier that returns a fixed ClassifierResult."""
    classifier = MagicMock()
    classifier.start = AsyncMock()
    classifier.stop = AsyncMock()
    classifier.model = "claude-haiku-4-5-20251001"
    if raw is None:
        raw = json.dumps({"intent": intent, "confidence": confidence})
    classifier.classify = AsyncMock(return_value=ClassifierResult(
        classification=Classification(intent=intent, confidence=confidence),
        raw_response=raw,
        duration_s=0.1,
        parse_error=parse_error,
        error=error,
    ))
    return classifier


def _mock_decomposer(
    answer_events=None,
    review_result=None,
    route_task_result=None,
    model="claude-sonnet-4-6",
):
    """Build a mock Decomposer."""
    decomposer = MagicMock()
    decomposer.start = AsyncMock()
    decomposer.stop = AsyncMock()
    decomposer.is_processing = False
    decomposer.processing_seconds = None
    decomposer.idle_seconds = 5.0
    decomposer.send_count = 0
    decomposer.usage_stats = None
    decomposer.diagnostics = {"is_alive": True}
    decomposer.model = model
    decomposer.is_alive = True

    if answer_events is None:
        answer_events = [Response(content="Done.")]

    async def _answer(prompt: str) -> AsyncGenerator:
        for event in answer_events:
            yield event

    decomposer.answer = _answer
    decomposer.review = AsyncMock(return_value=review_result or ReviewResult(
        intent="task", confidence=0.9, estimated_tools=1,
    ))
    decomposer.route_task = AsyncMock(return_value=route_task_result or TaskOutput(
        scope="small", summary="Quick task", prompt="Do the thing",
    ))
    decomposer.activate_skill = MagicMock()
    decomposer.inject_context = MagicMock()
    decomposer.recent_events = MagicMock(return_value=[])
    return decomposer


def _make_pipeline(classifier=None, decomposer=None):
    """Build a Pipeline with mocked Classifier and Decomposer."""
    if classifier is None:
        classifier = _mock_classifier()
    if decomposer is None:
        decomposer = _mock_decomposer()

    with patch("archon.ai.pipeline.Classifier", return_value=classifier):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            pipeline = Pipeline()

    return pipeline, classifier, decomposer


async def _collect(pipeline, prompt="test"):
    return [e async for e in pipeline.send(prompt)]


# ------------------------------------------------------------------
# Classification info flows to the routing layer
# ------------------------------------------------------------------


async def test_high_confidence_task_answers_directly() -> None:
    """High-confidence task with estimated_tools=0 goes to answer() directly."""
    pipeline, classifier, decomposer = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.92),
        decomposer=_mock_decomposer(
            answer_events=[Response(content="Done.")],
        ),
    )
    events = await _collect(pipeline, "write a test")

    # Classifier was called with the user prompt
    classifier.classify.assert_awaited_once_with("write a test")

    # route_task NOT called (estimated_tools=0)
    decomposer.route_task.assert_not_awaited()

    # No review (high confidence)
    decomposer.review.assert_not_awaited()

    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert routing[0].routing == "task_direct"


async def test_high_confidence_chat_routes_to_answer() -> None:
    """High-confidence chat classification triggers Decomposer.answer()."""
    pipeline, classifier, decomposer = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.95),
        decomposer=_mock_decomposer(
            answer_events=[Response(content="Hello!")],
        ),
    )
    events = await _collect(pipeline, "hi")

    classifier.classify.assert_awaited_once_with("hi")
    decomposer.review.assert_not_awaited()
    decomposer.route_task.assert_not_awaited()

    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1
    assert responses[0].content == "Hello!"


async def test_low_confidence_triggers_review_before_routing() -> None:
    """Low-confidence classification triggers Decomposer.review() before routing."""
    pipeline, _, decomposer = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.5),
        decomposer=_mock_decomposer(
            review_result=ReviewResult(intent="task", confidence=0.9, estimated_tools=1),
        ),
    )
    events = await _collect(pipeline, "do something")

    # Review was called with the prompt and original classification
    decomposer.review.assert_awaited_once()
    call_args = decomposer.review.call_args
    assert call_args[0][0] == "do something"
    assert call_args[0][1].intent == "task"
    assert call_args[0][1].confidence == 0.5


async def test_classification_event_yielded_for_task() -> None:
    """ClassificationEvent is yielded with correct intent and confidence."""
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.88),
    )
    events = await _collect(pipeline, "refactor the auth module")

    classification_events = [e for e in events if isinstance(e, ClassificationEvent)]
    assert len(classification_events) == 1
    assert classification_events[0].intent == "task"
    assert classification_events[0].confidence == 0.88


async def test_classification_event_yielded_for_chat() -> None:
    """ClassificationEvent is yielded for chat classification."""
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.95),
    )
    events = await _collect(pipeline, "hi")

    classification_events = [e for e in events if isinstance(e, ClassificationEvent)]
    assert len(classification_events) == 1
    assert classification_events[0].intent == "chat"
    assert classification_events[0].confidence == 0.95


async def test_malformed_classifier_defaults_to_task_with_review() -> None:
    """When classifier returns garbage (task, 0.0), review is triggered due to low confidence."""
    pipeline, _, decomposer = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.0),
        decomposer=_mock_decomposer(
            review_result=ReviewResult(intent="task", confidence=0.5, estimated_tools=1),
            answer_events=[Response(content="I'll help with that.")],
        ),
    )
    events = await _collect(pipeline, "do something")

    # Default classification event
    ce = [e for e in events if isinstance(e, ClassificationEvent)]
    assert len(ce) == 1
    assert ce[0].intent == "task"
    assert ce[0].confidence == 0.0

    # Review was triggered (confidence 0.0 < 0.8)
    decomposer.review.assert_awaited_once()

    # ReviewEvent was yielded
    review_events = [e for e in events if isinstance(e, ReviewEvent)]
    assert len(review_events) == 1


async def test_classification_event_is_first_event() -> None:
    """ClassificationEvent is always the first event yielded."""
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.75),
    )
    events = await _collect(pipeline, "hey")

    assert isinstance(events[0], ClassificationEvent)
    assert events[0].intent == "chat"
    assert events[0].confidence == 0.75
