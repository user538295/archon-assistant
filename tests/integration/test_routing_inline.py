"""Integration tests for the full pipeline routing with inline execution.

These tests exercise the complete Pipeline.send() flow end-to-end with mocked
Classifier and Decomposer, verifying the new routing logic:
  - scope='trivial' or scope='small' → inline via _task_direct_monitored
  - scope='large' → PlanEvent + background agents
  - FallbackNoticeEvent ordering when is_fallback=True
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
    FallbackNoticeEvent,
    PlanEvent,
    PromotionEvent,
    Response,
    RoutingEvent,
    ToolResult,
    ToolStarted,
)
from archon.ai.pipeline import Pipeline, _TOOL_PROMOTION_THRESHOLD


# ──────────────────────────────────────────────────────────────────
# Helpers (mirrors test_pipeline.py pattern)
# ──────────────────────────────────────────────────────────────────


def _mock_classifier(intent: str = "task", confidence: float = 0.9) -> MagicMock:
    classifier = MagicMock()
    classifier.start = AsyncMock()
    classifier.stop = AsyncMock()
    classifier.model = "claude-haiku-4-5-20251001"
    classifier.usage_stats = None
    classifier.classify = AsyncMock(return_value=ClassifierResult(
        classification=Classification(intent=intent, confidence=confidence),
        raw_response=json.dumps({"intent": intent, "confidence": confidence}),
        duration_s=0.05,
        parse_error="",
        error="",
    ))
    return classifier


def _mock_decomposer(
    answer_events=None,
    route_task_result=None,
    model: str = "claude-sonnet-4-6",
) -> MagicMock:
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
    decomposer.flush_pending_context = MagicMock()
    decomposer.recent_events = MagicMock(return_value=[])
    decomposer.track_context = MagicMock()
    # All callable and property attributes are explicitly set above; no auto-created attrs relied upon.

    if answer_events is None:
        answer_events = [Response(content="Default response.")]

    async def _answer(prompt: str) -> AsyncGenerator:
        for event in answer_events:
            yield event

    decomposer.answer = _answer
    decomposer.route_task = AsyncMock(
        return_value=route_task_result or TaskOutput(
            scope="small", summary="Quick task", prompt="Do the thing",
        )
    )
    decomposer.activate_skill = MagicMock()
    decomposer.inject_context = MagicMock()
    decomposer.context_summary = ""
    decomposer.reminder = None
    return decomposer


def _make_pipeline(classifier=None, decomposer=None) -> tuple:
    if classifier is None:
        classifier = _mock_classifier()
    if decomposer is None:
        decomposer = _mock_decomposer()

    with patch("archon.ai.pipeline.Classifier", return_value=classifier):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            pipeline = Pipeline()

    return pipeline, classifier, decomposer


async def _collect(pipeline: Pipeline, prompt: str = "test") -> list:
    return [e async for e in pipeline.send(prompt)]


# ──────────────────────────────────────────────────────────────────
# Integration test 1: trivial scope → inline end-to-end
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trivial_task_executes_inline_end_to_end() -> None:
    """Full pipeline: Classifier(task) + route_task(scope='trivial') → inline execution.

    Expected: no PlanEvent, RoutingEvent(routing='task_direct'), Response in output.
    """
    decomposer = _mock_decomposer(
        answer_events=[Response(content="Quick answer.")],
        route_task_result=TaskOutput(
            scope="trivial",
            summary="Quick lookup",
            prompt="What is 2+2?",
        ),
    )
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "what is 2+2?")

    # No PlanEvent — trivial scope goes inline
    plans = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plans) == 0, f"Expected no PlanEvent for trivial scope, got: {plans}"

    # RoutingEvent must say 'task_direct'
    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert len(routing) == 1
    assert routing[0].routing == "task_direct"

    # Response must appear
    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1
    assert responses[0].content == "Quick answer."

    # route_task must have been called
    decomposer.route_task.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────
# Integration test 2: small scope + tool promotion end-to-end
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_small_task_tool_promotion_e2e() -> None:
    """Full pipeline: Classifier(task) + route_task(scope='small') + N tools → PromotionEvent.

    When tool count reaches the threshold, PromotionEvent fires and Response is NOT yielded
    (generator closed on promotion).
    """
    tools = []
    for i in range(1, _TOOL_PROMOTION_THRESHOLD + 1):
        tools.append(ToolStarted(name=f"Tool{i}", id=i))
        tools.append(ToolResult(content=f"r{i}", id=i))
    tools.append(Response(content="Should not appear — promotion fired first"))

    decomposer = _mock_decomposer(
        answer_events=tools,
        route_task_result=TaskOutput(
            scope="small",
            summary="Small fix",
            prompt="Fix it",
        ),
    )
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "fix it")

    promotions = [e for e in events if isinstance(e, PromotionEvent)]
    assert len(promotions) == 1, f"Expected exactly one PromotionEvent, got: {promotions}"
    assert promotions[0].tool_count == _TOOL_PROMOTION_THRESHOLD

    # No Response after promotion (generator was closed)
    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 0, f"Expected no Response after promotion, got: {responses}"


# ──────────────────────────────────────────────────────────────────
# Integration test 3: large scope → background agents
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_large_task_still_spawns_agents() -> None:
    """Full pipeline: Classifier(task) + route_task(scope='large') → PlanEvent + agent_plan routing."""
    decomposer = _mock_decomposer(
        route_task_result=TaskOutput(
            scope="large",
            summary="Big refactor",
            agents=[
                AgentTask(id="a1", task="Do the first part"),
                AgentTask(id="a2", task="Do the second part", depends_on=("a1",)),
            ],
        ),
    )
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "refactor everything")

    plans = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plans) == 1, f"Expected exactly one PlanEvent for large scope, got: {plans}"
    assert len(plans[0].plan.agents) == 2

    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert len(routing) == 1
    assert routing[0].routing == "agent_plan"

    # route_task called
    decomposer.route_task.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────
# Integration test 4: fallback notice ordering
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fallback_notice_shown_before_inline_execution() -> None:
    """Full pipeline: route_task returns TaskOutput(is_fallback=True, scope='small').

    FallbackNoticeEvent must appear in the event stream BEFORE RoutingEvent('task_direct').
    """
    decomposer = _mock_decomposer(
        answer_events=[Response(content="Done inline after fallback.")],
        route_task_result=TaskOutput(
            scope="small",
            summary="Direct handling",
            prompt="Do the thing",
            is_fallback=True,
            fallback_reason="Routing check timed out — trying to handle directly",
        ),
    )
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "do the thing")

    fallbacks = [e for e in events if isinstance(e, FallbackNoticeEvent)]
    assert len(fallbacks) == 1, f"Expected one FallbackNoticeEvent, got: {fallbacks}"
    assert "timed out" in fallbacks[0].reason

    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert len(routing) == 1
    assert routing[0].routing == "task_direct"

    # FallbackNoticeEvent must come BEFORE RoutingEvent
    fallback_idx = next(i for i, e in enumerate(events) if isinstance(e, FallbackNoticeEvent))
    routing_idx = next(i for i, e in enumerate(events) if isinstance(e, RoutingEvent))
    assert fallback_idx < routing_idx, (
        f"FallbackNoticeEvent (idx={fallback_idx}) must precede RoutingEvent (idx={routing_idx})"
    )

    # Response must still be delivered inline
    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1


# ──────────────────────────────────────────────────────────────────
# Integration test 5: fallback + inline + promotion full chain
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fallback_then_inline_then_promotion_full_chain() -> None:
    """Full chain: FallbackNoticeEvent → RoutingEvent(task_direct) → PromotionEvent."""
    tools = []
    for i in range(1, _TOOL_PROMOTION_THRESHOLD + 1):
        tools.append(ToolStarted(name=f"Tool{i}", id=i))
        tools.append(ToolResult(content=f"r{i}", id=i))
    tools.append(Response(content="Should not reach"))

    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=_mock_decomposer(
            route_task_result=TaskOutput(
                scope="small",
                prompt="do something",
                is_fallback=True,
                fallback_reason="Routing check timed out — trying to handle directly",
            ),
            answer_events=tools,
        ),
    )
    events = await _collect(pipeline, "do something")

    fallback_idx = next(i for i, e in enumerate(events) if isinstance(e, FallbackNoticeEvent))
    routing_idx = next(
        i for i, e in enumerate(events)
        if isinstance(e, RoutingEvent) and e.routing == "task_direct"
    )
    promotion_idx = next(i for i, e in enumerate(events) if isinstance(e, PromotionEvent))

    assert fallback_idx < routing_idx < promotion_idx
    assert not any(isinstance(e, Response) for e in events)


# ──────────────────────────────────────────────────────────────────
# Integration test 6: flush scope boundary tests
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_flush_called_for_large_scope() -> None:
    """large scope: flush_pending_context IS called before spawning agents."""
    decomposer = _mock_decomposer(
        route_task_result=TaskOutput(
            scope="large", summary="Big task",
            agents=[AgentTask(id="a1", task="do it")],
        ),
    )
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task"),
        decomposer=decomposer,
    )
    await _collect(pipeline, "big task")
    decomposer.flush_pending_context.assert_called_once()


@pytest.mark.asyncio
async def test_flush_not_called_for_trivial_scope() -> None:
    """trivial scope: flush_pending_context must NOT be called (inline execution needs context)."""
    decomposer = _mock_decomposer(
        route_task_result=TaskOutput(scope="trivial", summary="Quick", prompt="answer me"),
        answer_events=[Response(content="Here you go")],
    )
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task"),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "quick question")
    assert any(isinstance(e, Response) for e in events), "inline path must yield a Response"
    decomposer.flush_pending_context.assert_not_called()
