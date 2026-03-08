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
    PromotionEvent,
    Response,
    ReviewEvent,
    RoutingEvent,
    ThinkingResult,
    ToolResult,
    ToolStarted,
)
from archon.ai.pipeline import Pipeline, _TOOL_PROMOTION_THRESHOLD


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _mock_classifier(intent="task", confidence=0.9, error="", parse_error="", raw=None, estimated_tools=0):
    """Build a mock Classifier that returns a fixed ClassifierResult."""
    classifier = MagicMock()
    classifier.start = AsyncMock()
    classifier.stop = AsyncMock()
    classifier.model = "claude-haiku-4-5-20251001"
    classifier.usage_stats = None  # default: no data
    if raw is None:
        raw = json.dumps({"intent": intent, "confidence": confidence})
    classifier.classify = AsyncMock(return_value=ClassifierResult(
        classification=Classification(intent=intent, confidence=confidence, estimated_tools=estimated_tools),
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
    decomposer.track_context = MagicMock()
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
    assert routing[0].routing == "chat"


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
    assert routing[0].routing == "chat"


async def test_high_conf_task_answers_directly() -> None:
    """High confidence + task (estimated_tools=0) → answer directly (task_direct)."""
    pipeline, _, decomposer = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=_mock_decomposer(
            answer_events=[Response(content="Here is the fix.")],
        ),
    )
    events = await _collect(pipeline)

    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1
    assert responses[0].content == "Here is the fix."

    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert routing[0].routing == "task_direct"

    # route_task NOT called (no review, estimated_tools=0)
    decomposer.route_task.assert_not_awaited()


async def test_low_conf_many_tools_yields_multi_agent_plan() -> None:
    """Low conf + review estimated_tools > 1 → route_task → multi-agent plan."""
    pipeline, _, decomposer = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.3),
        decomposer=_mock_decomposer(
            review_result=ReviewResult(intent="task", confidence=0.5, estimated_tools=3),
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
# RoutingEvent always before answer events
# ──────────────────────────────────────────────────────────────────


async def test_routing_event_is_before_answer() -> None:
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.95),
        decomposer=_mock_decomposer(
            answer_events=[ThinkingResult(content="t"), Response(content="a")],
        ),
    )
    events = await _collect(pipeline)

    routing_idx = next(i for i, e in enumerate(events) if isinstance(e, RoutingEvent))
    first_answer_idx = next(i for i, e in enumerate(events) if isinstance(e, Response))
    assert routing_idx < first_answer_idx


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
    """Pipeline passes total_cost_usd through when all sub-session costs are zero.

    Uses a decomposer mock with the production shape (sessions key present with zero costs).
    Without classifier cost or sub-session costs, total_cost_usd stays as the main session value.
    """
    pipeline, classifier, decomposer = _make_pipeline()
    classifier.usage_stats = None
    # Production shape: Decomposer always includes sessions key
    decomposer.usage_stats = {
        "total_cost_usd": 0.05,
        "sessions": {
            "orchestration": {"cost_usd": 0.0, "cumulative_cache_creation": 0},
            "summary":       {"cost_usd": 0.0, "cumulative_cache_creation": 0},
        },
    }
    stats = pipeline.usage_stats
    assert stats is not None
    assert stats["total_cost_usd"] == 0.05  # no sub-session costs to add


def test_usage_stats_none_when_decomposer_returns_none() -> None:
    pipeline, _, decomposer = _make_pipeline()
    decomposer.usage_stats = None
    assert pipeline.usage_stats is None


def test_usage_stats_includes_sessions_key() -> None:
    pipeline, classifier, decomposer = _make_pipeline()
    classifier.usage_stats = None
    decomposer.usage_stats = {"total_cost_usd": 0.05, "sessions": {}}
    stats = pipeline.usage_stats
    assert stats is not None
    assert "sessions" in stats


def test_usage_stats_sessions_has_classifier_key() -> None:
    pipeline, classifier, decomposer = _make_pipeline()
    classifier.usage_stats = None
    decomposer.usage_stats = {"total_cost_usd": 0.05, "sessions": {}}
    stats = pipeline.usage_stats
    assert "classifier" in stats["sessions"]


def test_usage_stats_total_cost_is_aggregate() -> None:
    pipeline, classifier, decomposer = _make_pipeline()
    classifier.usage_stats = {"total_cost_usd": 0.01, "cumulative_cache_creation": 100}
    decomposer.usage_stats = {
        "total_cost_usd": 0.05,
        "sessions": {
            "orchestration": {"cumulative_cache_creation": 0, "cost_usd": 0.002},
            "summary":       {"cumulative_cache_creation": 0, "cost_usd": 0.001},
        },
    }
    stats = pipeline.usage_stats
    assert stats is not None
    assert abs(stats["total_cost_usd"] - 0.063) < 0.0001  # 0.05 + 0.01 + 0.002 + 0.001


def test_usage_stats_classifier_cost_in_sessions() -> None:
    pipeline, classifier, decomposer = _make_pipeline()
    classifier.usage_stats = {"total_cost_usd": 0.007, "cumulative_cache_creation": 300}
    decomposer.usage_stats = {"total_cost_usd": 0.05, "sessions": {}}
    stats = pipeline.usage_stats
    assert stats is not None
    assert stats["sessions"]["classifier"]["cost_usd"] == 0.007
    assert stats["sessions"]["classifier"]["cumulative_cache_creation"] == 300


def test_usage_stats_classifier_zero_when_no_data() -> None:
    pipeline, classifier, decomposer = _make_pipeline()
    classifier.usage_stats = None
    decomposer.usage_stats = {"total_cost_usd": 0.05, "sessions": {}}
    stats = pipeline.usage_stats
    assert stats is not None
    assert stats["sessions"]["classifier"]["cost_usd"] == 0.0
    assert stats["sessions"]["classifier"]["cumulative_cache_creation"] == 0


def test_usage_stats_cumulative_cache_creation_unchanged() -> None:
    """top-level cumulative_cache_creation is main session only (for context window bar)."""
    pipeline, classifier, decomposer = _make_pipeline()
    classifier.usage_stats = {"total_cost_usd": 0.01, "cumulative_cache_creation": 9999}
    decomposer.usage_stats = {"total_cost_usd": 0.05, "cumulative_cache_creation": 5000, "sessions": {}}
    stats = pipeline.usage_stats
    assert stats is not None
    assert stats["cumulative_cache_creation"] == 5000  # main session only


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
    """PlanEvent has source='pipeline' — trigger via review with estimated_tools > 1."""
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.3),
        decomposer=_mock_decomposer(
            review_result=ReviewResult(intent="task", confidence=0.5, estimated_tools=3),
            route_task_result=TaskOutput(
                scope="large",
                summary="Plan test",
                agents=[AgentTask(id="a1", task="Do it")],
            ),
        ),
    )
    events = await _collect(pipeline)

    plans = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plans) == 1
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


# ──────────────────────────────────────────────────────────────────
# Edge: exact threshold boundary (0.8)
# ──────────────────────────────────────────────────────────────────


async def test_exact_threshold_uses_high_confidence_path() -> None:
    """Confidence == 0.8 (exact threshold) goes to high-confidence path (no review)."""
    pipeline, _, decomposer = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.8),
        decomposer=_mock_decomposer(
            answer_events=[Response(content="Done.")],
        ),
    )
    events = await _collect(pipeline)

    decomposer.review.assert_not_awaited()
    review_events = [e for e in events if isinstance(e, ReviewEvent)]
    assert len(review_events) == 0

    # Should route as task_direct (high confidence, estimated_tools=0)
    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert routing[0].routing == "task_direct"


async def test_just_below_threshold_triggers_review() -> None:
    """Confidence 0.79 triggers review."""
    pipeline, _, decomposer = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.79),
    )
    events = await _collect(pipeline)

    decomposer.review.assert_awaited_once()
    review_events = [e for e in events if isinstance(e, ReviewEvent)]
    assert len(review_events) == 1


# ──────────────────────────────────────────────────────────────────
# Edge: cyclic dependency in plan
# ──────────────────────────────────────────────────────────────────


async def test_cyclic_plan_yields_zero_waves() -> None:
    """When Decomposer returns a plan with cycles, wave_count=0."""
    pipeline, _, decomposer = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.3),
        decomposer=_mock_decomposer(
            review_result=ReviewResult(intent="task", confidence=0.5, estimated_tools=3),
            route_task_result=TaskOutput(
                scope="large",
                summary="Cyclic plan",
                agents=[
                    AgentTask(id="a1", task="Do A", depends_on=["a2"]),
                    AgentTask(id="a2", task="Do B", depends_on=["a1"]),
                ],
            ),
        ),
    )
    events = await _collect(pipeline)

    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert len(routing) == 1
    assert routing[0].routing == "agent_plan"
    assert routing[0].wave_count == 0
    assert routing[0].agent_count == 2


# ──────────────────────────────────────────────────────────────────
# Edge: low conf + review upgrades to chat
# ──────────────────────────────────────────────────────────────────


async def test_review_changes_intent_to_chat() -> None:
    """Review changes intent from task to chat with high confidence."""
    pipeline, _, decomposer = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.3),
        decomposer=_mock_decomposer(
            review_result=ReviewResult(intent="chat", confidence=0.9, estimated_tools=0),
            answer_events=[Response(content="Just a conversation")],
        ),
    )
    events = await _collect(pipeline)

    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert routing[0].routing == "chat"

    decomposer.route_task.assert_not_awaited()


# ──────────────────────────────────────────────────────────────────
# Phase 1: Classifier estimated_tools in routing
# ──────────────────────────────────────────────────────────────────


async def test_high_conf_task_estimated_tools_gt1_routes_to_plan() -> None:
    """High confidence + task + classifier estimated_tools=3 → route_task (plan)."""
    pipeline, _, decomposer = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9, estimated_tools=3),
        decomposer=_mock_decomposer(
            route_task_result=TaskOutput(
                scope="small", summary="Investigation", prompt="Do research",
            ),
        ),
    )
    events = await _collect(pipeline)

    # Review was NOT triggered (high confidence)
    decomposer.review.assert_not_awaited()
    # route_task WAS called (estimated_tools > 1)
    decomposer.route_task.assert_awaited_once()
    plans = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plans) == 1


async def test_high_conf_task_estimated_tools_le1_routes_direct() -> None:
    """High confidence + task + classifier estimated_tools=1 → task_direct."""
    pipeline, _, decomposer = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9, estimated_tools=1),
        decomposer=_mock_decomposer(
            answer_events=[Response(content="Quick fix.")],
        ),
    )
    events = await _collect(pipeline)

    decomposer.review.assert_not_awaited()
    decomposer.route_task.assert_not_awaited()
    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert routing[0].routing == "task_direct"


async def test_classification_event_includes_estimated_tools() -> None:
    """ClassificationEvent should carry estimated_tools from classifier."""
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9, estimated_tools=5),
    )
    events = await _collect(pipeline)

    ce = [e for e in events if isinstance(e, ClassificationEvent)]
    assert ce[0].estimated_tools == 5


# ──────────────────────────────────────────────────────────────────
# Phase 2: Runtime promotion safety net
# ──────────────────────────────────────────────────────────────────


async def test_task_direct_promotes_at_threshold() -> None:
    """3+ ToolStarted events triggers PromotionEvent, no Response yielded."""
    tools = []
    for i in range(1, _TOOL_PROMOTION_THRESHOLD + 1):
        tools.append(ToolStarted(name=f"Tool{i}", input=f"arg{i}", id=i))
        tools.append(ToolResult(content=f"result{i}", id=i))
    tools.append(Response(content="Final answer"))

    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9, estimated_tools=0),
        decomposer=_mock_decomposer(answer_events=tools),
    )
    events = await _collect(pipeline, "investigate this code")

    promotions = [e for e in events if isinstance(e, PromotionEvent)]
    assert len(promotions) == 1
    assert promotions[0].tool_count == _TOOL_PROMOTION_THRESHOLD
    assert promotions[0].original_prompt == "investigate this code"

    # Response should NOT appear (stream was interrupted)
    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 0


async def test_task_direct_no_promotion_under_threshold() -> None:
    """2 tools (under default threshold of 10) — normal completion with Response."""
    tools = [
        ToolStarted(name="Read", input="/file1", id=1),
        ToolResult(content="content1", id=1),
        ToolStarted(name="Read", input="/file2", id=2),
        ToolResult(content="content2", id=2),
        Response(content="Here's the answer"),
    ]
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=_mock_decomposer(answer_events=tools),
    )
    events = await _collect(pipeline)

    promotions = [e for e in events if isinstance(e, PromotionEvent)]
    assert len(promotions) == 0

    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1


async def test_task_direct_no_tools_no_promotion() -> None:
    """Zero tools — normal completion, no promotion."""
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=_mock_decomposer(answer_events=[Response(content="Quick.")]),
    )
    events = await _collect(pipeline)

    promotions = [e for e in events if isinstance(e, PromotionEvent)]
    assert len(promotions) == 0

    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1


async def test_promotion_event_includes_original_prompt() -> None:
    """PromotionEvent carries the user's original prompt."""
    tools = []
    for i in range(1, _TOOL_PROMOTION_THRESHOLD + 1):
        tools.append(ToolStarted(name=f"T{i}", id=i))
        tools.append(ToolResult(content=f"r{i}", id=i))
    tools.append(Response(content="done"))

    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=_mock_decomposer(answer_events=tools),
    )
    events = await _collect(pipeline, "find all auth endpoints")

    promotions = [e for e in events if isinstance(e, PromotionEvent)]
    assert promotions[0].original_prompt == "find all auth endpoints"


async def test_chat_direct_promotes_when_threshold_exceeded() -> None:
    """Chat path promotes to background agent when tool count reaches threshold."""
    tools = []
    for i in range(1, _TOOL_PROMOTION_THRESHOLD + 1):
        tools.append(ToolStarted(name=f"Tool{i}", id=i))
        tools.append(ToolResult(content=f"r{i}", id=i))
    tools.append(Response(content="Chat response"))

    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.95),
        decomposer=_mock_decomposer(answer_events=tools),
    )
    events = await _collect(pipeline)

    promotions = [e for e in events if isinstance(e, PromotionEvent)]
    assert len(promotions) == 1
    assert promotions[0].tool_count == _TOOL_PROMOTION_THRESHOLD


async def test_chat_direct_no_promotion_below_threshold() -> None:
    """Chat path does not promote when tool count is below threshold."""
    tools = []
    for i in range(1, _TOOL_PROMOTION_THRESHOLD):  # one fewer than threshold
        tools.append(ToolStarted(name=f"Tool{i}", id=i))
        tools.append(ToolResult(content=f"r{i}", id=i))
    tools.append(Response(content="Chat response"))

    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.95),
        decomposer=_mock_decomposer(answer_events=tools),
    )
    events = await _collect(pipeline)

    promotions = [e for e in events if isinstance(e, PromotionEvent)]
    assert len(promotions) == 0

    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1


async def test_promotion_enriched_prompt_truncates_results() -> None:
    """Tool results longer than 500 chars are truncated in the promotion prompt."""
    long_result = "x" * 1000
    tools = []
    for i in range(1, _TOOL_PROMOTION_THRESHOLD + 1):
        tools.append(ToolStarted(name=f"Read", input=f"/file{i}", id=i))
        tools.append(ToolResult(content=long_result, id=i))
    tools.append(Response(content="done"))

    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=_mock_decomposer(answer_events=tools),
    )
    events = await _collect(pipeline, "check code")

    promotions = [e for e in events if isinstance(e, PromotionEvent)]
    assert len(promotions) == 1
    # Each tool result should be truncated — no 1000-char blocks
    assert long_result not in promotions[0].agent_prompt
    assert "..." in promotions[0].agent_prompt


async def test_build_promotion_prompt_format() -> None:
    """Unit test for _build_promotion_prompt()."""
    from archon.ai.pipeline import _build_promotion_prompt

    tool_pairs = [
        (ToolStarted(name="Read", input="/a.py", id=1), ToolResult(content="file content here", id=1)),
        (ToolStarted(name="Grep", input="pattern", id=2), ToolResult(content="match found", id=2)),
    ]
    result = _build_promotion_prompt(tool_pairs, "original request text")

    assert "[CONTINUATION:" in result
    assert "Tool 1: Read(/a.py)" in result
    assert "file content here" in result
    assert "Tool 2: Grep(pattern)" in result
    assert "Original request: original request text" in result


# ──────────────────────────────────────────────────────────────────
# Escalation context tracking
# ──────────────────────────────────────────────────────────────────


async def test_escalation_records_context() -> None:
    """After PromotionEvent, decomposer.track_context is called with escalation info."""
    tools = []
    for i in range(1, _TOOL_PROMOTION_THRESHOLD + 1):
        tools.append(ToolStarted(name=f"Tool{i}", input=f"arg{i}", id=i))
        tools.append(ToolResult(content=f"result{i}", id=i))
    tools.append(Response(content="Final answer"))

    decomposer = _mock_decomposer(answer_events=tools)
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9, estimated_tools=0),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "investigate this code")

    # PromotionEvent should have been yielded
    promotions = [e for e in events if isinstance(e, PromotionEvent)]
    assert len(promotions) == 1

    # track_context should have been called with escalation info
    decomposer.track_context.assert_called_once()
    call_args = decomposer.track_context.call_args
    assert call_args[0][0] == "investigate this code"
    assert "escalated" in call_args[0][1].lower()


async def test_track_context_delegates_to_decomposer() -> None:
    """Pipeline.track_context delegates to Decomposer.track_context."""
    decomposer = _mock_decomposer()
    pipeline, _, _ = _make_pipeline(decomposer=decomposer)

    pipeline.track_context("prompt", "summary")

    decomposer.track_context.assert_called_once_with("prompt", "summary")


# ──────────────────────────────────────────────────────────────────
# reminder param wiring — US-006
# ──────────────────────────────────────────────────────────────────


async def test_custom_tool_promotion_threshold_respected() -> None:
    """Pipeline with tool_promotion_threshold=5 must not promote at 3 tools."""
    tools = []
    for i in range(1, 4):  # exactly 3 tools — below the custom threshold of 5
        tools.append(ToolStarted(name=f"Tool{i}", input=f"arg{i}", id=i))
        tools.append(ToolResult(content=f"result{i}", id=i))
    tools.append(Response(content="Final answer"))

    with patch("archon.ai.pipeline.Classifier", return_value=_mock_classifier(intent="task", confidence=0.9, estimated_tools=0)):
        with patch("archon.ai.pipeline.Decomposer", return_value=_mock_decomposer(answer_events=tools)):
            pipeline = Pipeline(tool_promotion_threshold=5)

    events = await _collect(pipeline, "investigate this code")

    promotions = [e for e in events if isinstance(e, PromotionEvent)]
    assert len(promotions) == 0, "Should not promote when tool_count < tool_promotion_threshold"

    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1, "Response must be delivered when below threshold"


async def test_custom_tool_promotion_threshold_triggers_at_configured_value() -> None:
    """Pipeline with tool_promotion_threshold=5 promotes exactly at 5 tools."""
    tools = []
    for i in range(1, 6):  # exactly 5 tools
        tools.append(ToolStarted(name=f"Tool{i}", input=f"arg{i}", id=i))
        tools.append(ToolResult(content=f"result{i}", id=i))
    tools.append(Response(content="Final answer"))

    with patch("archon.ai.pipeline.Classifier", return_value=_mock_classifier(intent="task", confidence=0.9, estimated_tools=0)):
        with patch("archon.ai.pipeline.Decomposer", return_value=_mock_decomposer(answer_events=tools)):
            pipeline = Pipeline(tool_promotion_threshold=5)

    events = await _collect(pipeline, "investigate this code")

    promotions = [e for e in events if isinstance(e, PromotionEvent)]
    assert len(promotions) == 1
    assert promotions[0].tool_count == 5


async def test_tool_promotion_threshold_0_disables_promotion() -> None:
    """threshold=0 must never promote, even with many tools."""
    tools = []
    for i in range(1, 20):  # 19 tools — well above any reasonable threshold
        tools.append(ToolStarted(name=f"Tool{i}", input=f"arg{i}", id=i))
        tools.append(ToolResult(content=f"result{i}", id=i))
    tools.append(Response(content="Final answer"))

    with patch("archon.ai.pipeline.Classifier", return_value=_mock_classifier(intent="task", confidence=0.9, estimated_tools=0)):
        with patch("archon.ai.pipeline.Decomposer", return_value=_mock_decomposer(answer_events=tools)):
            pipeline = Pipeline(tool_promotion_threshold=0)

    events = await _collect(pipeline, "do a lot of work")

    promotions = [e for e in events if isinstance(e, PromotionEvent)]
    assert len(promotions) == 0, "threshold=0 must disable promotion entirely"

    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1


async def test_tool_promotion_threshold_1_promotes_on_first_tool() -> None:
    """threshold=1 must promote on the very first ToolStarted, before any result."""
    tools = [
        ToolStarted(name="ReadFile", input="/etc/hosts", id=1),
        ToolResult(content="127.0.0.1 localhost", id=1),
        Response(content="Done."),
    ]

    with patch("archon.ai.pipeline.Classifier", return_value=_mock_classifier(intent="task", confidence=0.9, estimated_tools=0)):
        with patch("archon.ai.pipeline.Decomposer", return_value=_mock_decomposer(answer_events=tools)):
            pipeline = Pipeline(tool_promotion_threshold=1)

    events = await _collect(pipeline, "read a file")

    promotions = [e for e in events if isinstance(e, PromotionEvent)]
    assert len(promotions) == 1
    assert promotions[0].tool_count == 1

    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 0


def test_pipeline_passes_reminder_to_decomposer() -> None:
    """Pipeline must forward the reminder kwarg to Decomposer."""
    from unittest.mock import MagicMock, patch

    from archon.ai.reminder import ContextReminder

    mock_reminder = MagicMock(spec=ContextReminder)

    with patch("archon.ai.pipeline.Classifier"):
        with patch("archon.ai.pipeline.Decomposer") as MockDecomposer:
            Pipeline(reminder=mock_reminder)

    _, kwargs = MockDecomposer.call_args
    assert kwargs.get("reminder") is mock_reminder


def test_pipeline_reminder_none_by_default() -> None:
    """Pipeline passes reminder=None to Decomposer when not provided."""
    from unittest.mock import patch

    with patch("archon.ai.pipeline.Classifier"):
        with patch("archon.ai.pipeline.Decomposer") as MockDecomposer:
            Pipeline()

    _, kwargs = MockDecomposer.call_args
    assert kwargs.get("reminder") is None
