"""Tests for Pipeline — routing algorithm with Classifier + Decomposer."""

import json
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.agent_plan import AgentPlan, AgentTask
from archon.ai.classification import Classification
from archon.ai.classifier import ClassifierResult
from archon.ai.decomposer import ReviewResult, TaskOutput
from archon.ai.event_mapper import (
    ClassificationEvent,
    ErrorEvent,
    Event,
    PlanEvent,
    Response,
    ReviewEvent,
    RoutingEvent,
    ThinkingResult,
    ToolResult,
    ToolStarted,
)
from archon.ai.pipeline import Pipeline


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


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
    """Collect all events from pipeline.send()."""
    return [e async for e in pipeline.send(prompt)]


# ──────────────────────────────────────────────────────────────────
# Step 1: Classification always happens
# ──────────────────────────────────────────────────────────────────


async def test_send_yields_classification_event() -> None:
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.85),
    )
    events = await _collect(pipeline, "hi")

    ce = [e for e in events if isinstance(e, ClassificationEvent)]
    assert len(ce) == 1
    assert ce[0].intent == "chat"
    assert ce[0].confidence == 0.85


async def test_classification_event_includes_model() -> None:
    pipeline, _, _ = _make_pipeline()
    events = await _collect(pipeline)

    ce = [e for e in events if isinstance(e, ClassificationEvent)]
    assert ce[0].model == "claude-haiku-4-5-20251001"


async def test_classification_event_includes_duration() -> None:
    pipeline, _, _ = _make_pipeline()
    events = await _collect(pipeline)

    ce = [e for e in events if isinstance(e, ClassificationEvent)]
    assert ce[0].duration_s >= 0.0


async def test_classification_event_includes_raw_response() -> None:
    raw = '{"intent": "chat", "confidence": 0.85}'
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.85, raw=raw),
    )
    events = await _collect(pipeline, "hi")

    ce = [e for e in events if isinstance(e, ClassificationEvent)]
    assert ce[0].raw_response == raw


async def test_classification_event_includes_parse_error() -> None:
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(parse_error="no JSON found"),
    )
    events = await _collect(pipeline)

    ce = [e for e in events if isinstance(e, ClassificationEvent)]
    assert ce[0].parse_error == "no JSON found"


async def test_classification_event_no_parse_error_on_success() -> None:
    pipeline, _, _ = _make_pipeline()
    events = await _collect(pipeline)

    ce = [e for e in events if isinstance(e, ClassificationEvent)]
    assert ce[0].parse_error == ""


# ──────────────────────────────────────────────────────────────────
# Classifier failure → ErrorEvent + default classification
# ──────────────────────────────────────────────────────────────────


async def test_classifier_error_yields_error_event() -> None:
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(
            intent="task", confidence=0.0,
            error="Classifier failed: SDK connection lost",
        ),
    )
    events = await _collect(pipeline)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(errors) >= 1
    assert "Classifier" in errors[0].message


async def test_classifier_crash_raw_response_empty() -> None:
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(
            intent="task", confidence=0.0,
            error="Classifier failed: boom", raw="",
        ),
    )
    events = await _collect(pipeline)

    ce = [e for e in events if isinstance(e, ClassificationEvent)]
    assert ce[0].raw_response == ""


# ──────────────────────────────────────────────────────────────────
# Step 2: Review when confidence < 0.8
# ──────────────────────────────────────────────────────────────────


async def test_low_confidence_triggers_review() -> None:
    """When confidence < 0.8, Decomposer.review() is called."""
    pipeline, _, decomposer = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.5),
    )
    events = await _collect(pipeline)

    decomposer.review.assert_awaited_once()
    review_events = [e for e in events if isinstance(e, ReviewEvent)]
    assert len(review_events) == 1
    assert review_events[0].original_intent == "chat"
    assert review_events[0].original_confidence == 0.5


async def test_high_confidence_skips_review() -> None:
    """When confidence >= 0.8, no review happens."""
    pipeline, _, decomposer = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
    )
    events = await _collect(pipeline)

    decomposer.review.assert_not_awaited()
    review_events = [e for e in events if isinstance(e, ReviewEvent)]
    assert len(review_events) == 0


async def test_review_event_shows_updated_values() -> None:
    pipeline, _, decomposer = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.3),
        decomposer=_mock_decomposer(
            review_result=ReviewResult(intent="task", confidence=0.85, estimated_tools=2),
        ),
    )
    events = await _collect(pipeline)

    re = [e for e in events if isinstance(e, ReviewEvent)]
    assert re[0].updated_intent == "task"
    assert re[0].updated_confidence == 0.85
    assert re[0].estimated_tools == 2


# ──────────────────────────────────────────────────────────────────
# Step 3a: Low confidence routing paths
# ──────────────────────────────────────────────────────────────────


async def test_low_conf_still_low_chat_answers_directly() -> None:
    """Low conf + still low after review + chat → answer directly."""
    pipeline, _, decomposer = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.3),
        decomposer=_mock_decomposer(
            review_result=ReviewResult(intent="chat", confidence=0.5, estimated_tools=0),
            answer_events=[Response(content="Hello there!")],
        ),
    )
    events = await _collect(pipeline)

    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1
    assert responses[0].content == "Hello there!"

    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert routing[0].routing == "chat_direct"


async def test_low_conf_still_low_task_many_tools_yields_plan() -> None:
    """Low conf + still low after review + task + estimated_tools > 1 → large task."""
    pipeline, _, decomposer = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.3),
        decomposer=_mock_decomposer(
            review_result=ReviewResult(intent="task", confidence=0.5, estimated_tools=3),
            route_task_result=TaskOutput(
                scope="large",
                summary="Big refactor",
                agents=[
                    AgentTask(id="a1", task="Research"),
                    AgentTask(id="a2", task="Implement", depends_on=["a1"]),
                ],
            ),
        ),
    )
    events = await _collect(pipeline)

    plans = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plans) == 1
    assert plans[0].plan.scope == "large"
    assert len(plans[0].plan.agents) == 2

    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert routing[0].routing == "agent_plan"


async def test_low_conf_still_low_task_single_tool_answers_directly() -> None:
    """Low conf + still low + task + estimated_tools <= 1 → answer directly."""
    pipeline, _, decomposer = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.3),
        decomposer=_mock_decomposer(
            review_result=ReviewResult(intent="task", confidence=0.5, estimated_tools=1),
            answer_events=[Response(content="Here is the fix")],
        ),
    )
    events = await _collect(pipeline)

    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1

    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert routing[0].routing == "task_direct"


# ──────────────────────────────────────────────────────────────────
# Step 3b: High confidence routing paths
# ──────────────────────────────────────────────────────────────────


async def test_high_conf_chat_answers_directly() -> None:
    """High confidence + chat → answer directly."""
    pipeline, _, decomposer = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.95),
        decomposer=_mock_decomposer(
            answer_events=[Response(content="Hi!")],
        ),
    )
    events = await _collect(pipeline)

    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1
    assert responses[0].content == "Hi!"

    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert routing[0].routing == "chat_direct"


async def test_high_conf_task_small_yields_plan_event() -> None:
    """High confidence + task + scope=small → PlanEvent with single agent."""
    pipeline, _, decomposer = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=_mock_decomposer(
            route_task_result=TaskOutput(
                scope="small",
                summary="Fix typo",
                prompt="Fix the typo in README.md",
            ),
        ),
    )
    events = await _collect(pipeline)

    plans = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plans) == 1
    assert plans[0].plan.scope == "small"
    assert len(plans[0].plan.agents) == 1
    assert plans[0].plan.agents[0].task == "Fix the typo in README.md"

    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert routing[0].routing == "agent_spawn"
    assert routing[0].agent_count == 1


async def test_high_conf_task_large_yields_multi_agent_plan() -> None:
    """High confidence + task + scope=large → PlanEvent with multi-agent."""
    pipeline, _, decomposer = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=_mock_decomposer(
            route_task_result=TaskOutput(
                scope="large",
                summary="Refactor auth",
                agents=[
                    AgentTask(id="a1", task="Extract middleware"),
                    AgentTask(id="a2", task="Update imports", depends_on=["a1"]),
                ],
            ),
        ),
    )
    events = await _collect(pipeline)

    plans = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plans) == 1
    assert plans[0].plan.scope == "large"
    assert len(plans[0].plan.agents) == 2
    assert plans[0].summary == "Refactor auth"

    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert routing[0].routing == "agent_plan"
    assert routing[0].agent_count == 2
    assert routing[0].wave_count == 2


# ──────────────────────────────────────────────────────────────────
# answer() streams thinking and tool events
# ──────────────────────────────────────────────────────────────────


async def test_answer_streams_decomposer_events() -> None:
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.95),
        decomposer=_mock_decomposer(
            answer_events=[
                ThinkingResult(content="thinking..."),
                ToolStarted(name="Read", input="/file"),
                ToolResult(content="contents"),
                Response(content="Here is the answer"),
            ],
        ),
    )
    events = await _collect(pipeline)

    types = [type(e).__name__ for e in events]
    assert "ClassificationEvent" in types
    assert "ThinkingResult" in types
    assert "ToolStarted" in types
    assert "ToolResult" in types
    assert "Response" in types


# ──────────────────────────────────────────────────────────────────
# RoutingEvent always last
# ──────────────────────────────────────────────────────────────────


async def test_routing_event_is_last() -> None:
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.95),
        decomposer=_mock_decomposer(
            answer_events=[ThinkingResult(content="t"), Response(content="a")],
        ),
    )
    events = await _collect(pipeline)

    assert isinstance(events[-1], RoutingEvent)


async def test_routing_event_source_is_pipeline() -> None:
    pipeline, _, _ = _make_pipeline()
    events = await _collect(pipeline)

    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert routing[0].source == "pipeline"


async def test_routing_event_model_empty_when_none() -> None:
    pipeline, _, _ = _make_pipeline(
        decomposer=_mock_decomposer(model=None),
    )
    events = await _collect(pipeline)

    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert routing[0].model == ""


async def test_routing_event_model_set() -> None:
    pipeline, _, _ = _make_pipeline(
        decomposer=_mock_decomposer(model="claude-sonnet-4-6"),
    )
    events = await _collect(pipeline)

    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert routing[0].model == "claude-sonnet-4-6"


# ──────────────────────────────────────────────────────────────────
# Lifecycle
# ──────────────────────────────────────────────────────────────────


async def test_start_starts_both() -> None:
    pipeline, classifier, decomposer = _make_pipeline()
    await pipeline.start()

    classifier.start.assert_awaited_once()
    decomposer.start.assert_awaited_once()


async def test_stop_stops_both() -> None:
    pipeline, classifier, decomposer = _make_pipeline()
    await pipeline.stop()

    classifier.stop.assert_awaited_once()
    decomposer.stop.assert_awaited_once()


async def test_stop_still_stops_decomposer_when_classifier_fails() -> None:
    classifier = _mock_classifier()
    classifier.stop = AsyncMock(side_effect=RuntimeError("crash"))
    pipeline, _, decomposer = _make_pipeline(classifier=classifier)

    await pipeline.stop()

    decomposer.stop.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────
# Delegation (duck-typing surface)
# ──────────────────────────────────────────────────────────────────


def test_is_processing_delegates() -> None:
    pipeline, _, decomposer = _make_pipeline()
    decomposer.is_processing = True
    assert pipeline.is_processing is True


def test_processing_seconds_delegates() -> None:
    pipeline, _, decomposer = _make_pipeline()
    decomposer.processing_seconds = 12.5
    assert pipeline.processing_seconds == 12.5


def test_idle_seconds_delegates() -> None:
    pipeline, _, decomposer = _make_pipeline()
    decomposer.idle_seconds = 3.0
    assert pipeline.idle_seconds == 3.0


def test_diagnostics_delegates() -> None:
    pipeline, _, decomposer = _make_pipeline()
    decomposer.diagnostics = {"is_alive": True, "send_count": 5}
    assert pipeline.diagnostics == {"is_alive": True, "send_count": 5}


def test_usage_stats_delegates() -> None:
    pipeline, _, decomposer = _make_pipeline()
    decomposer.usage_stats = {"total_cost_usd": 0.05}
    assert pipeline.usage_stats == {"total_cost_usd": 0.05}


def test_send_count_delegates() -> None:
    pipeline, _, decomposer = _make_pipeline()
    decomposer.send_count = 7
    assert pipeline.send_count == 7


def test_recent_events_delegates() -> None:
    pipeline, _, decomposer = _make_pipeline()
    decomposer.recent_events.return_value = [(1.0, Response(content="hi"))]
    assert len(pipeline.recent_events(5)) == 1


def test_activate_skill_delegates() -> None:
    pipeline, _, decomposer = _make_pipeline()
    skill = MagicMock()
    pipeline.activate_skill(skill)
    decomposer.activate_skill.assert_called_once_with(skill)


def test_inject_context_delegates() -> None:
    pipeline, _, decomposer = _make_pipeline()
    pipeline.inject_context("some context")
    decomposer.inject_context.assert_called_once_with("some context")


def test_is_alive_delegates() -> None:
    pipeline, _, decomposer = _make_pipeline()
    decomposer.is_alive = True
    assert pipeline.is_alive is True


def test_model_delegates() -> None:
    pipeline, _, decomposer = _make_pipeline()
    decomposer.model = "claude-sonnet-4-6"
    assert pipeline.model == "claude-sonnet-4-6"


# ──────────────────────────────────────────────────────────────────
# Plan event source
# ──────────────────────────────────────────────────────────────────


async def test_plan_event_source_is_pipeline() -> None:
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
    )
    events = await _collect(pipeline)

    plans = [e for e in events if isinstance(e, PlanEvent)]
    assert plans[0].source == "pipeline"


# ──────────────────────────────────────────────────────────────────
# Edge: review upgrades confidence above threshold
# ──────────────────────────────────────────────────────────────────


async def test_review_upgrades_confidence_uses_high_conf_path() -> None:
    """When review raises confidence >= 0.8, use high-confidence routing."""
    pipeline, _, decomposer = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.3),
        decomposer=_mock_decomposer(
            review_result=ReviewResult(intent="task", confidence=0.9, estimated_tools=2),
            route_task_result=TaskOutput(
                scope="small",
                summary="Quick task",
                prompt="Do the thing",
            ),
        ),
    )
    events = await _collect(pipeline)

    # Should have gone through review, then high-confidence task path
    review_events = [e for e in events if isinstance(e, ReviewEvent)]
    assert len(review_events) == 1

    # Since confidence is now 0.9 (>= 0.8), it should route as task
    decomposer.route_task.assert_awaited_once()

    plans = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plans) == 1
