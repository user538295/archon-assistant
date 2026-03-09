"""Tests for Pipeline — routing algorithm with Classifier + Decomposer."""

import json
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.classification import Classification
from archon.ai.classifier import ClassifierResult
from archon.ai.decomposer import TaskOutput
from archon.ai.event_mapper import (
    ClassificationEvent,
    ErrorEvent,
    Event,
    PlanEvent,
    PromotionEvent,
    Response,
    RoutingEvent,
    ThinkingResult,
    ToolResult,
    ToolStarted,
)
from archon.ai.pipeline import Pipeline, _TOOL_PROMOTION_THRESHOLD


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _mock_classifier(intent="task", confidence=0.9, error="", parse_error="", raw=None):
    """Build a mock Classifier that returns a fixed ClassifierResult."""
    classifier = MagicMock()
    classifier.start = AsyncMock()
    classifier.stop = AsyncMock()
    classifier.model = "claude-haiku-4-5-20251001"
    classifier.usage_stats = None  # default: no data
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


async def test_classifier_crash_stops_pipeline() -> None:
    """After a classifier error, only ErrorEvent is yielded — no ClassificationEvent or decomposer output."""
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(
            intent="task", confidence=0.0,
            error="Classifier failed: boom", raw="",
        ),
    )
    events = await _collect(pipeline)

    assert len(events) == 1
    assert isinstance(events[0], ErrorEvent)
    # No ClassificationEvent, RoutingEvent, or Response should appear
    assert not any(isinstance(e, (ClassificationEvent, RoutingEvent, Response)) for e in events)


# ──────────────────────────────────────────────────────────────────
# Step 2: Chat high-confidence routing
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


async def test_classification_event_has_no_estimated_tools() -> None:
    """ClassificationEvent must not have an estimated_tools field."""
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
    )
    events = await _collect(pipeline)

    ce = [e for e in events if isinstance(e, ClassificationEvent)]
    assert not hasattr(ce[0], "estimated_tools")


# ──────────────────────────────────────────────────────────────────
# Phase 2: Runtime promotion safety net (chat path only)
# ──────────────────────────────────────────────────────────────────


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


async def test_track_context_delegates_to_decomposer() -> None:
    """Pipeline.track_context delegates to Decomposer.track_context."""
    decomposer = _mock_decomposer()
    pipeline, _, _ = _make_pipeline(decomposer=decomposer)

    pipeline.track_context("prompt", "summary")

    decomposer.track_context.assert_called_once_with("prompt", "summary")


# ──────────────────────────────────────────────────────────────────
# reminder param wiring — US-006
# ──────────────────────────────────────────────────────────────────


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


# ──────────────────────────────────────────────────────────────────
# flush_pending_context delegation and route_task flush
# ──────────────────────────────────────────────────────────────────


async def test_flush_pending_context_delegates_to_decomposer() -> None:
    """pipeline.flush_pending_context() calls decomposer.flush_pending_context."""
    decomposer = _mock_decomposer()
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(decomposer=decomposer)

    pipeline.flush_pending_context()

    decomposer.flush_pending_context.assert_called_once()


# ──────────────────────────────────────────────────────────────────
# New routing logic: ALL tasks → route_task; only chat+high-conf → answer
# ──────────────────────────────────────────────────────────────────


async def test_chat_high_confidence_routes_to_answer_directly() -> None:
    """chat + confidence >= 0.8 → answer() called, route_task NOT called, RoutingEvent routing='chat'."""
    decomposer = _mock_decomposer(answer_events=[Response(content="Hi!")])
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.9),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "hello")

    decomposer.route_task.assert_not_awaited()

    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1
    assert responses[0].content == "Hi!"

    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert routing[0].routing == "chat"


async def test_chat_low_confidence_routes_to_route_task() -> None:
    """chat + confidence < 0.8 → route_task() called, answer() NOT called."""
    decomposer = _mock_decomposer(
        route_task_result=TaskOutput(scope="small", summary="Quick task", prompt="Do the thing"),
    )
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.7),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "maybe a task?")

    decomposer.route_task.assert_awaited_once()

    # answer() was never called (no Response from answer mock)
    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 0

    plans = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plans) == 1


async def test_task_any_confidence_routes_to_route_task() -> None:
    """task + high confidence → route_task() called, answer() NOT called."""
    decomposer = _mock_decomposer(
        route_task_result=TaskOutput(scope="small", summary="Quick task", prompt="Do the thing"),
    )
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.95),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "build a feature")

    decomposer.route_task.assert_awaited_once()

    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 0

    plans = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plans) == 1


async def test_task_low_confidence_routes_to_route_task() -> None:
    """task + low confidence → route_task() called (no review step)."""
    decomposer = _mock_decomposer(
        route_task_result=TaskOutput(scope="small", summary="Quick task", prompt="Do the thing"),
    )
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.5),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "do something")

    decomposer.route_task.assert_awaited_once()

    plans = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plans) == 1


async def test_task_routes_to_route_task_no_review() -> None:
    """task intent never triggers review."""
    decomposer = _mock_decomposer(
        route_task_result=TaskOutput(scope="small", summary="Quick task", prompt="Do the thing"),
    )
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.3),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "do something")

    # No ReviewEvent — it no longer exists; just verify route_task was called
    assert any(isinstance(e, RoutingEvent) for e in events)


async def test_route_task_always_flushes_pending_context() -> None:
    """Whenever route_task() is called, flush_pending_context is called first."""
    decomposer = _mock_decomposer(
        route_task_result=TaskOutput(scope="small", summary="Quick task", prompt="Do the thing"),
    )
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=decomposer,
    )
    await _collect(pipeline, "big task")

    decomposer.flush_pending_context.assert_called_once()


async def test_chat_high_confidence_does_not_flush_pending_context() -> None:
    """chat + high confidence goes to answer() — flush is NOT called."""
    decomposer = _mock_decomposer(answer_events=[Response(content="Hi!")])
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.9),
        decomposer=decomposer,
    )
    await _collect(pipeline, "hello")

    decomposer.flush_pending_context.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# _yield_plan — dual-prompt for small scope (Step 6)
# ──────────────────────────────────────────────────────────────────


def test_small_scope_dual_prompt_when_enriched() -> None:
    """When orch returns a resolved prompt that differs from original, both are included."""
    pipeline, _, _ = _make_pipeline()
    task_output = TaskOutput(
        scope="small",
        summary="x",
        prompt="Rewrite /path/to/script.py in Python",
    )
    events = pipeline._yield_plan(task_output, "rewrite the script from yesterday")
    plan_event = next(e for e in events if isinstance(e, PlanEvent))
    task = plan_event.plan.agents[0].task
    assert task.startswith("[Original user request]: rewrite the script from yesterday")
    assert "[Resolved context]: Rewrite /path/to/script.py in Python" in task


def test_small_scope_no_dual_prompt_when_same() -> None:
    """When orch returns the same prompt as original, no dual format is used."""
    pipeline, _, _ = _make_pipeline()
    task_output = TaskOutput(
        scope="small",
        summary="x",
        prompt="rewrite the script from yesterday",
    )
    events = pipeline._yield_plan(task_output, "rewrite the script from yesterday")
    plan_event = next(e for e in events if isinstance(e, PlanEvent))
    task = plan_event.plan.agents[0].task
    assert "[Original user request]:" not in task
    assert task == "rewrite the script from yesterday"


def test_small_scope_no_dual_prompt_when_no_resolved_prompt() -> None:
    """When task_output.prompt is None, fallback to original prompt without dual format."""
    pipeline, _, _ = _make_pipeline()
    task_output = TaskOutput(scope="small", summary="x", prompt=None)
    events = pipeline._yield_plan(task_output, "do something")
    plan_event = next(e for e in events if isinstance(e, PlanEvent))
    assert plan_event.plan.agents[0].task == "do something"
