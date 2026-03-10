"""E2E smoke tests for the multi-agent pipeline — Task #6.

Patches Classifier and Decomposer at the Pipeline import level, so
Pipeline, classification routing, and SessionManager all run with real code.

Scenarios:
  - Chat flow end-to-end (high confidence)
  - Task flow end-to-end → route_task
  - Malformed classifier (task, 0.0) → route_task (no review)
  - Two users: independent Pipelines
  - Session lifecycle: create -> send -> stop -> recreate
"""

import json
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.agent_plan import AgentTask
from archon.ai.classification import Classification
from archon.ai.classifier import ClassifierResult
from archon.ai.decomposer import TaskOutput
from archon.ai.event_mapper import (
    ClassificationEvent,
    PlanEvent,
    Response,
    RoutingEvent,
    ThinkingResult,
    ToolResult,
    ToolStarted,
)
from archon.ai.session_manager import SessionManager


# ------------------------------------------------------------------
# Helpers (same mock pattern as test_pipeline.py)
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
    decomposer.review = AsyncMock()
    decomposer.route_task = AsyncMock(return_value=route_task_result or TaskOutput(
        scope="small", summary="Quick task", prompt="Do the thing",
    ))
    decomposer.activate_skill = MagicMock()
    decomposer.inject_context = MagicMock()
    decomposer.recent_events = MagicMock(return_value=[])
    return decomposer


# ------------------------------------------------------------------
# Chat flow end-to-end
# ------------------------------------------------------------------


async def test_e2e_chat_flow() -> None:
    """User sends a greeting -> classified as chat -> Decomposer responds conversationally."""
    classifier = _mock_classifier(intent="chat", confidence=0.95)
    decomposer = _mock_decomposer(
        answer_events=[Response(content="Hello! How can I help you today?")],
    )

    with patch("archon.ai.pipeline.Classifier", return_value=classifier):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            mgr = SessionManager(timeout=60)
            session = await mgr.get_or_create(user_id=1)
            events = [e async for e in session.send("hi there")]

    # ClassificationEvent must be first
    assert isinstance(events[0], ClassificationEvent)
    assert events[0].intent == "chat"
    assert events[0].confidence == 0.95

    # Decomposer response must follow
    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1
    assert "Hello" in responses[0].content

    # Classifier received the raw user prompt
    classifier.classify.assert_awaited_once_with("hi there")

    # No review
    decomposer.review.assert_not_awaited()

    # No route_task (chat + high confidence)
    decomposer.route_task.assert_not_awaited()


# ------------------------------------------------------------------
# Task flow end-to-end
# ------------------------------------------------------------------


async def test_e2e_task_flow() -> None:
    """User sends a task request -> classified as task -> routed via route_task -> inline."""
    classifier = _mock_classifier(intent="task", confidence=0.92)
    decomposer = _mock_decomposer(
        route_task_result=TaskOutput(scope="small", summary="Test written.", prompt="write a unit test"),
        answer_events=[Response(content="Done.")],
    )

    with patch("archon.ai.pipeline.Classifier", return_value=classifier):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            mgr = SessionManager(timeout=60)
            session = await mgr.get_or_create(user_id=1)
            events = [e async for e in session.send("write a unit test")]

    # Classification
    assert events[0].intent == "task"

    # Task → route_task → inline (no PlanEvent for small scope)
    decomposer.route_task.assert_awaited_once()

    plans = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plans) == 0

    # RoutingEvent says task_direct
    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert any(r.routing == "task_direct" for r in routing)

    # Response is delivered
    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1

    # No review
    decomposer.review.assert_not_awaited()


# ------------------------------------------------------------------
# Malformed classifier -> task(0.0) -> route_task (no review)
# ------------------------------------------------------------------


async def test_e2e_malformed_classifier_fallback() -> None:
    """Classifier returns garbage -> defaults to task(0.0) -> route_task (no review)."""
    classifier = _mock_classifier(intent="task", confidence=0.0, raw="Sorry, I can't classify this.")
    decomposer = _mock_decomposer(
        route_task_result=TaskOutput(scope="small", summary="Quick task", prompt="Do the thing"),
    )

    with patch("archon.ai.pipeline.Classifier", return_value=classifier):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            mgr = SessionManager(timeout=60)
            session = await mgr.get_or_create(user_id=1)
            events = [e async for e in session.send("do something complex")]

    # Default classification: task with 0.0 confidence
    classification = events[0]
    assert isinstance(classification, ClassificationEvent)
    assert classification.intent == "task"
    assert classification.confidence == 0.0

    # No review — review is removed from routing
    decomposer.review.assert_not_awaited()

    # route_task IS called (task → always route_task)
    decomposer.route_task.assert_awaited_once()

    plans = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plans) == 0


# ------------------------------------------------------------------
# Two users: independent Pipelines
# ------------------------------------------------------------------


async def test_e2e_two_users_independent_pipelines() -> None:
    """Two users get independent Pipeline instances with separate sessions."""
    classifier_1 = _mock_classifier(intent="chat", confidence=0.9)
    decomposer_1 = _mock_decomposer(
        answer_events=[Response(content="Hi user 1!")],
    )
    classifier_2 = _mock_classifier(intent="task", confidence=0.85)
    decomposer_2 = _mock_decomposer(
        route_task_result=TaskOutput(scope="small", summary="Built for user 2!", prompt="build something"),
        answer_events=[Response(content="Built for user 2!")],
    )

    with patch(
        "archon.ai.pipeline.Classifier",
        side_effect=[classifier_1, classifier_2],
    ):
        with patch(
            "archon.ai.pipeline.Decomposer",
            side_effect=[decomposer_1, decomposer_2],
        ):
            mgr = SessionManager(timeout=60)
            session_1 = await mgr.get_or_create(user_id=1)
            session_2 = await mgr.get_or_create(user_id=2)

            # Different pipeline instances
            assert session_1 is not session_2

            events_1 = [e async for e in session_1.send("hello")]
            events_2 = [e async for e in session_2.send("build something")]

    # User 1: chat classification → answer directly
    assert events_1[0].intent == "chat"
    assert any(isinstance(e, Response) and "user 1" in e.content for e in events_1)

    # User 2: task classification → route_task → inline (no PlanEvent for small scope)
    assert events_2[0].intent == "task"
    plans_2 = [e for e in events_2 if isinstance(e, PlanEvent)]
    assert len(plans_2) == 0
    routing_2 = [e for e in events_2 if isinstance(e, RoutingEvent)]
    assert any(r.routing == "task_direct" for r in routing_2)


# ------------------------------------------------------------------
# Session lifecycle: create -> send -> stop -> recreate
# ------------------------------------------------------------------


async def test_e2e_session_lifecycle() -> None:
    """Session can be created, used, stopped, and recreated."""
    classifier_1 = _mock_classifier(intent="chat", confidence=0.8)
    decomposer_1 = _mock_decomposer(
        answer_events=[Response(content="First session.")],
    )
    classifier_2 = _mock_classifier(intent="task", confidence=0.9)
    decomposer_2 = _mock_decomposer(
        route_task_result=TaskOutput(scope="small", summary="Second session done.", prompt="second"),
        answer_events=[Response(content="Second session done.")],
    )

    with patch(
        "archon.ai.pipeline.Classifier",
        side_effect=[classifier_1, classifier_2],
    ):
        with patch(
            "archon.ai.pipeline.Decomposer",
            side_effect=[decomposer_1, decomposer_2],
        ):
            mgr = SessionManager(timeout=60)

            # Create and use first session
            session_1 = await mgr.get_or_create(user_id=1)
            events_1 = [e async for e in session_1.send("first message")]
            assert any(isinstance(e, Response) and "First" in e.content for e in events_1)

            # Stop the session
            await mgr.stop(user_id=1)
            assert not mgr.has_session(1)

            # Recreate -- should get a fresh pipeline
            session_2 = await mgr.get_or_create(user_id=1)
            assert session_2 is not session_1

            events_2 = [e async for e in session_2.send("second message")]

    # Second session: task classification → route_task → inline (no PlanEvent for small scope)
    assert events_2[0].intent == "task"
    plans_2 = [e for e in events_2 if isinstance(e, PlanEvent)]
    assert len(plans_2) == 0
    routing_2 = [e for e in events_2 if isinstance(e, RoutingEvent)]
    assert any(r.routing == "task_direct" for r in routing_2)
