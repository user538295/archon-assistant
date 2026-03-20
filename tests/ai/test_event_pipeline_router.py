"""Tests for the full-stack router event flow through Pipeline.

These tests verify that events from route_task() are:
  - tagged with source='router' by Pipeline.send()
  - yielded before any main-session events
  - excluded from the pipeline's own final output (TaskOutput not forwarded)
  - invisible to chat in quiet/normal mode (exercised via format_event)
  - all visible in debug mode
"""

import json
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.classification import Classification
from archon.ai.classifier import ClassifierResult
from archon.ai.decomposer import TaskOutput
from archon.ai.event_mapper import (
    Response,
    RoutingEvent,
    ThinkingResult,
    ToolResult,
    ToolStarted,
    is_router_event,
)
from tests.conftest import _RouteTaskGenMock


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _mock_classifier(intent: str = "task", confidence: float = 0.9) -> MagicMock:
    classifier = MagicMock()
    classifier.start = AsyncMock()
    classifier.stop = AsyncMock()
    classifier.model = "claude-haiku-4-5-20251001"
    classifier.usage_stats = None
    classifier.classify = AsyncMock(
        return_value=ClassifierResult(
            classification=Classification(intent=intent, confidence=confidence),
            raw_response=json.dumps({"intent": intent, "confidence": confidence}),
            duration_s=0.05,
            parse_error="",
            error="",
        )
    )
    return classifier


def _mock_decomposer(
    answer_events: list | None = None,
    route_task_result: TaskOutput | None = None,
    route_task_pre_events: list | None = None,
) -> MagicMock:
    """Build a mock Decomposer that yields pre_events then route_task_result from route_task()."""
    decomposer = MagicMock()
    decomposer.start = AsyncMock()
    decomposer.stop = AsyncMock()
    decomposer.is_processing = False
    decomposer.processing_seconds = None
    decomposer.idle_seconds = 5.0
    decomposer.send_count = 0
    decomposer.usage_stats = None
    decomposer.diagnostics = {"is_alive": True}
    decomposer.model = "claude-sonnet-4-6"
    decomposer.is_alive = True
    decomposer.flush_pending_context = MagicMock()
    decomposer.recover_session = AsyncMock()
    decomposer.recent_events = MagicMock(return_value=[])
    decomposer.track_context = MagicMock()

    if answer_events is None:
        answer_events = [Response(content="Main response.")]

    async def _answer(prompt: str) -> AsyncGenerator:
        for event in answer_events:
            yield event

    decomposer.answer = _answer
    decomposer.activate_skill = MagicMock()
    decomposer.inject_context = MagicMock()
    decomposer.context_summary = ""
    decomposer.reminder = None

    task_output = route_task_result or TaskOutput(
        scope="small", summary="Quick task", prompt="Do the thing"
    )
    decomposer.route_task = _RouteTaskGenMock(
        task_output,
        events=route_task_pre_events or [],
    )
    return decomposer


def _make_pipeline(classifier=None, decomposer=None):
    from archon.ai.pipeline import Pipeline

    if classifier is None:
        classifier = _mock_classifier()
    if decomposer is None:
        decomposer = _mock_decomposer()

    with patch("archon.ai.pipeline.Classifier", return_value=classifier):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            pipeline = Pipeline()

    return pipeline, classifier, decomposer


async def _collect(pipeline, prompt: str = "test") -> list:
    return [e async for e in pipeline.send(prompt)]


# ──────────────────────────────────────────────────────────────────
# Test 1: Router events emitted before main-session events
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_router_events_precede_main_session_events() -> None:
    """Events from route_task() must appear before main-session events in Pipeline.send()."""
    router_tool = ToolStarted(name="ListHistory", id=1)
    decomposer = _mock_decomposer(
        route_task_pre_events=[router_tool],
        route_task_result=TaskOutput(scope="small", prompt="do it"),
        answer_events=[Response(content="Main answer")],
    )
    pipeline, _, _ = _make_pipeline(decomposer=decomposer)
    events = await _collect(pipeline, "do something")

    # Find router events and main response in stream
    router_indices = [i for i, e in enumerate(events) if is_router_event(e)]
    response_indices = [i for i, e in enumerate(events) if isinstance(e, Response) and not is_router_event(e)]

    assert router_indices, "Expected at least one router event"
    assert response_indices, "Expected at least one main-session Response"
    assert max(router_indices) < min(response_indices), (
        "All router events must precede main-session events"
    )


# ──────────────────────────────────────────────────────────────────
# Test 2: Pipeline tags route_task() events with source='router'
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_yields_router_events_tagged() -> None:
    """Pipeline.send() re-tags all route_task() events with source='router'."""
    router_tool = ToolStarted(name="ReadHistory", id=2)
    router_result = ToolResult(content="some context", id=2)
    decomposer = _mock_decomposer(
        route_task_pre_events=[router_tool, router_result],
        route_task_result=TaskOutput(scope="small", prompt="x"),
    )
    pipeline, _, _ = _make_pipeline(decomposer=decomposer)
    events = await _collect(pipeline, "test")

    router_events_in_stream = [e for e in events if is_router_event(e)]
    assert len(router_events_in_stream) >= 2, (
        f"Expected tagged router events, got: {router_events_in_stream}"
    )
    for evt in router_events_in_stream:
        assert getattr(evt, "source", None) == "router", (
            f"Event {evt} must have source='router'"
        )


# ──────────────────────────────────────────────────────────────────
# Test 3: TaskOutput is consumed, not forwarded to caller
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_task_output_consumed_not_yielded() -> None:
    """The TaskOutput sentinel from route_task() must NOT appear in Pipeline.send() output."""
    decomposer = _mock_decomposer(
        route_task_result=TaskOutput(scope="small", prompt="x"),
    )
    pipeline, _, _ = _make_pipeline(decomposer=decomposer)
    events = await _collect(pipeline, "do something")

    task_outputs = [e for e in events if isinstance(e, TaskOutput)]
    assert len(task_outputs) == 0, (
        f"TaskOutput must not be forwarded to caller, got: {task_outputs}"
    )


# ──────────────────────────────────────────────────────────────────
# Test 4: Chat flow produces no router events
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_intent_produces_no_router_events() -> None:
    """Chat intent bypasses route_task() entirely — no router events in the stream."""
    decomposer = _mock_decomposer(
        answer_events=[Response(content="Hello!")],
    )
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.98),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "hi there")

    router_events = [e for e in events if is_router_event(e)]
    assert router_events == [], (
        f"Chat intent must produce no router events, got: {router_events}"
    )


# ──────────────────────────────────────────────────────────────────
# Test 5: Debug mode — all router events visible via format_event
# ──────────────────────────────────────────────────────────────────


def test_debug_mode_all_router_events_visible() -> None:
    """In debug mode, all non-Response router events yield non-empty format_event output."""
    from archon.ai.truncation import SplitStrategy
    from archon.chat.handler import format_event
    from archon.config.loader import NotificationsConfig

    notif = NotificationsConfig(mode="debug")
    split = SplitStrategy()

    router_events = [
        ToolStarted(name="ListHistory", id=1, source="router"),
        ToolResult(content="history data", id=1, source="router"),
        ThinkingResult(content="planning route", source="router"),
    ]
    for evt in router_events:
        result = format_event(evt, split, notifications=notif)
        assert result != [], (
            f"Debug mode must show {type(evt).__name__} router event, got []"
        )


# ──────────────────────────────────────────────────────────────────
# Test 6: RoutingEvent is always present and correct
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_routing_event_still_present_with_router_events() -> None:
    """RoutingEvent appears in stream regardless of router events being present."""
    router_tool = ToolStarted(name="ListHistory", id=1)
    decomposer = _mock_decomposer(
        route_task_pre_events=[router_tool],
        route_task_result=TaskOutput(scope="small", prompt="do it"),
        answer_events=[Response(content="Done")],
    )
    pipeline, _, _ = _make_pipeline(decomposer=decomposer)
    events = await _collect(pipeline, "do it")

    routing_events = [e for e in events if isinstance(e, RoutingEvent)]
    assert len(routing_events) == 1
    assert routing_events[0].routing == "task_direct"
