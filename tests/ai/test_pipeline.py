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
    FallbackNoticeEvent,
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
    decomposer.flush_pending_context = MagicMock()
    decomposer.recent_events = MagicMock(return_value=[])
    decomposer.context_summary = ""
    decomposer.reminder = None
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
    """chat + confidence < 0.8 → route_task() called; scope="small" → inline execution (Response yielded)."""
    decomposer = _mock_decomposer(
        answer_events=[Response(content="Done inline.")],
        route_task_result=TaskOutput(scope="small", summary="Quick task", prompt="Do the thing"),
    )
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.7),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "maybe a task?")

    decomposer.route_task.assert_awaited_once()

    # scope="small" → inline execution → Response IS yielded
    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1

    # scope="small" no longer produces a PlanEvent — goes inline
    plans = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plans) == 0


async def test_task_any_confidence_routes_to_route_task() -> None:
    """task + high confidence → route_task() called; scope="small" → inline execution (Response yielded)."""
    decomposer = _mock_decomposer(
        answer_events=[Response(content="Done inline.")],
        route_task_result=TaskOutput(scope="small", summary="Quick task", prompt="Do the thing"),
    )
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.95),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "build a feature")

    decomposer.route_task.assert_awaited_once()

    # scope="small" → inline execution → Response IS yielded
    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1

    # scope="small" no longer produces a PlanEvent — goes inline
    plans = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plans) == 0


async def test_task_low_confidence_routes_to_route_task() -> None:
    """task + low confidence → route_task() called; scope="small" → inline execution (no PlanEvent)."""
    decomposer = _mock_decomposer(
        answer_events=[Response(content="Done inline.")],
        route_task_result=TaskOutput(scope="small", summary="Quick task", prompt="Do the thing"),
    )
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.5),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "do something")

    decomposer.route_task.assert_awaited_once()

    # scope="small" → inline execution → no PlanEvent
    plans = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plans) == 0


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


async def test_large_scope_flushes_pending_context() -> None:
    """large scope triggers flush_pending_context before spawning background agents."""
    from archon.ai.agent_plan import AgentTask as _AgentTask
    decomposer = _mock_decomposer(
        route_task_result=TaskOutput(
            scope="large",
            summary="Big task",
            agents=[_AgentTask(id="a1", task="do it")],
        ),
    )
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=decomposer,
    )
    await _collect(pipeline, "big task")

    decomposer.flush_pending_context.assert_called_once()


async def test_small_scope_does_not_flush_pending_context() -> None:
    """small scope routes inline — flush_pending_context must NOT be called."""
    decomposer = _mock_decomposer(
        route_task_result=TaskOutput(scope="small", summary="Quick task", prompt="Do it"),
        answer_events=[Response(content="Done.")],
    )
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "small task")
    assert any(isinstance(e, Response) for e in events), "inline path must yield a Response"
    decomposer.flush_pending_context.assert_not_called()


async def test_trivial_scope_does_not_flush_pending_context() -> None:
    """trivial scope routes inline — flush_pending_context must NOT be called."""
    decomposer = _mock_decomposer(
        route_task_result=TaskOutput(scope="trivial", summary="Quick", prompt="answer me"),
        answer_events=[Response(content="Here you go")],
    )
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "quick question")
    assert any(isinstance(e, Response) for e in events), "inline path must yield a Response"
    decomposer.flush_pending_context.assert_not_called()


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


async def test_task_direct_prompt_empty_string_falls_back_to_original() -> None:
    """When route_task returns prompt='', answer() receives the original user prompt."""
    captured: list[str] = []

    decomposer = _mock_decomposer(
        route_task_result=TaskOutput(scope="small", summary="Quick task", prompt=""),
        answer_events=[Response(content="Done.")],
    )
    original_answer = decomposer.answer

    async def _capturing_answer(p: str) -> AsyncGenerator:
        captured.append(p)
        async for e in original_answer(p):
            yield e

    decomposer.answer = _capturing_answer
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=decomposer,
    )
    await _collect(pipeline, "original user prompt")

    assert len(captured) == 1
    assert captured[0] == "original user prompt"


# ──────────────────────────────────────────────────────────────────
# _yield_plan — large scope pass-through (Step 6)
# ──────────────────────────────────────────────────────────────────


def test_yield_plan_large_scope_passes_agents_unchanged() -> None:
    """_yield_plan with large scope passes agent tasks through unchanged without dual-prompt wrapping."""
    from archon.ai.agent_plan import AgentTask as _AgentTask
    pipeline, _, _ = _make_pipeline()
    task_output = TaskOutput(
        scope="large",
        summary="x",
        agents=[_AgentTask(id="primary", task="Rewrite /path/to/script.py in Python")],
    )
    events = pipeline._yield_plan(task_output)
    plan_event = next(e for e in events if isinstance(e, PlanEvent))
    task = plan_event.plan.agents[0].task
    # large scope: agent tasks passed through unchanged, no dual-prompt wrapping
    assert task == "Rewrite /path/to/script.py in Python"
    assert "[Original user request]:" not in task


def test_yield_plan_large_scope_no_dual_prompt_when_same_prompt() -> None:
    """_yield_plan with large scope and single agent: task passed through unchanged when task matches user request."""
    from archon.ai.agent_plan import AgentTask as _AgentTask
    pipeline, _, _ = _make_pipeline()
    task_output = TaskOutput(
        scope="large",
        summary="x",
        agents=[_AgentTask(id="primary", task="rewrite the script from yesterday")],
    )
    events = pipeline._yield_plan(task_output)
    plan_event = next(e for e in events if isinstance(e, PlanEvent))
    task = plan_event.plan.agents[0].task
    assert "[Original user request]:" not in task
    assert task == "rewrite the script from yesterday"


def test_yield_plan_large_scope_no_dual_prompt_when_no_prompt() -> None:
    """_yield_plan with large scope: agent task passed through unchanged when no resolved prompt enrichment applies."""
    from archon.ai.agent_plan import AgentTask as _AgentTask
    pipeline, _, _ = _make_pipeline()
    task_output = TaskOutput(
        scope="large",
        summary="do something",
        agents=[_AgentTask(id="primary", task="do something")],
    )
    events = pipeline._yield_plan(task_output)
    plan_event = next(e for e in events if isinstance(e, PlanEvent))
    assert plan_event.plan.agents[0].task == "do something"


# ──────────────────────────────────────────────────────────────────
# Resource leak fix: generator closed on promotion
# ──────────────────────────────────────────────────────────────────


async def test_answer_generator_closed_on_promotion() -> None:
    """When promotion triggers mid-stream, aclose() must be called on the generator."""
    aclose_called = False

    async def _answer_with_enough_tools(prompt: str):
        nonlocal aclose_called
        try:
            for i in range(1, _TOOL_PROMOTION_THRESHOLD + 1):
                yield ToolStarted(name=f"Tool{i}", id=i)
                yield ToolResult(content=f"r{i}", id=i)
            # Generator body never reaches here on promotion — GeneratorExit is thrown
            yield Response(content="should not be yielded")
        except GeneratorExit:
            aclose_called = True
            raise

    decomposer = _mock_decomposer()
    decomposer.answer = _answer_with_enough_tools

    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.95),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "do many tools")

    promotions = [e for e in events if isinstance(e, PromotionEvent)]
    assert len(promotions) == 1, "Expected exactly one PromotionEvent"
    assert aclose_called, "aclose() was not called on the generator — resource leak detected"


# ──────────────────────────────────────────────────────────────────
# _yield_plan — large scope passes agent tasks through unchanged
# ──────────────────────────────────────────────────────────────────


def test_large_scope_plan_passes_agent_tasks_through_unchanged() -> None:
    """scope='large' → PlanEvent with agent tasks verbatim — no dual-prompt wrapping."""
    from archon.ai.agent_plan import AgentPlan, AgentTask
    from archon.ai.decomposer import TaskOutput

    pipeline, _, _ = _make_pipeline()
    task_output = TaskOutput(
        scope="large",
        summary="Rewrite and test bin script",
        agents=[
            AgentTask(id="a1", task="Rewrite /path/to/collect_bins.sh in Python"),
            AgentTask(id="a2", task="Write tests for the Python script", depends_on=("a1",)),
        ],
    )
    events = pipeline._yield_plan(task_output)

    plan_events = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plan_events) == 1, "Expected exactly one PlanEvent for large scope"

    plan = plan_events[0].plan
    assert len(plan.agents) == 2

    # Agent tasks must be passed through unchanged — no dual-prompt wrapping
    assert plan.agents[0].task == "Rewrite /path/to/collect_bins.sh in Python"
    assert plan.agents[1].task == "Write tests for the Python script"

    # Explicitly document the design choice: dual-prompt format is NOT applied
    assert "[Original user request]:" not in plan.agents[0].task
    assert "[Original user request]:" not in plan.agents[1].task


# ─── New routing: trivial/small → inline, large → agents ───


async def test_trivial_scope_routes_inline() -> None:
    """scope='trivial' → answer() is called, RoutingEvent routing='task_direct', no PlanEvent."""
    decomposer = _mock_decomposer(
        answer_events=[Response(content="Quick answer.")],
        route_task_result=TaskOutput(scope="trivial", summary="Quick lookup", prompt="What is 2+2?"),
    )
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "what is 2+2?")

    decomposer.route_task.assert_awaited_once()

    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1
    assert responses[0].content == "Quick answer."

    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert routing[0].routing == "task_direct"


async def test_small_scope_routes_inline() -> None:
    """scope='small' → answer() is called, RoutingEvent routing='task_direct', no PlanEvent."""
    decomposer = _mock_decomposer(
        answer_events=[Response(content="Small task done.")],
        route_task_result=TaskOutput(scope="small", summary="Small fix", prompt="Fix typo"),
    )
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "fix the typo")

    decomposer.route_task.assert_awaited_once()

    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1

    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert routing[0].routing == "task_direct"


async def test_large_scope_still_spawns_plan() -> None:
    """scope='large' still → PlanEvent, RoutingEvent routing='agent_plan', no Response from answer()."""
    from archon.ai.agent_plan import AgentTask as _AgentTask

    decomposer = _mock_decomposer(
        answer_events=[Response(content="Should not be yielded.")],
        route_task_result=TaskOutput(
            scope="large",
            summary="Big refactor",
            agents=[
                _AgentTask(id="a1", task="Do the first part"),
                _AgentTask(id="a2", task="Do the second part", depends_on=("a1",)),
            ],
        ),
    )
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "refactor everything")

    plans = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plans) == 1

    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert routing[0].routing == "agent_plan"

    # answer() must NOT have been called for large scope
    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 0


async def test_trivial_scope_no_plan_event() -> None:
    """scope='trivial' → no PlanEvent in the event stream."""
    decomposer = _mock_decomposer(
        answer_events=[Response(content="Done.")],
        route_task_result=TaskOutput(scope="trivial", summary="Quick", prompt="Tell me"),
    )
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "tell me something")

    plans = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plans) == 0


async def test_small_scope_no_plan_event() -> None:
    """scope='small' → no PlanEvent in the event stream."""
    decomposer = _mock_decomposer(
        answer_events=[Response(content="Done.")],
        route_task_result=TaskOutput(scope="small", summary="Small", prompt="Do it"),
    )
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "do it")

    plans = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plans) == 0


async def test_fallback_notice_event_yielded_when_is_fallback() -> None:
    """When route_task returns TaskOutput(is_fallback=True, fallback_reason=<non-empty>),
    FallbackNoticeEvent is yielded before RoutingEvent."""
    from archon.ai.event_mapper import FallbackNoticeEvent

    decomposer = _mock_decomposer(
        answer_events=[Response(content="Done inline.")],
        route_task_result=TaskOutput(
            scope="small",
            summary="Direct handling",
            prompt="Do the thing",
            is_fallback=True,
            fallback_reason="Could not plan this task — attempting inline",
        ),
    )
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "do the thing")

    fallbacks = [e for e in events if isinstance(e, FallbackNoticeEvent)]
    assert len(fallbacks) == 1
    assert fallbacks[0].reason != ""

    # FallbackNoticeEvent must appear before the RoutingEvent
    fallback_idx = next(i for i, e in enumerate(events) if isinstance(e, FallbackNoticeEvent))
    routing_idx = next(i for i, e in enumerate(events) if isinstance(e, RoutingEvent))
    assert fallback_idx < routing_idx


async def test_fallback_notice_not_yielded_when_not_fallback() -> None:
    """When route_task returns TaskOutput(is_fallback=False), no FallbackNoticeEvent."""
    from archon.ai.event_mapper import FallbackNoticeEvent

    decomposer = _mock_decomposer(
        answer_events=[Response(content="Done.")],
        route_task_result=TaskOutput(
            scope="small",
            summary="Quick task",
            prompt="Do the thing",
            is_fallback=False,
        ),
    )
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "do the thing")

    fallbacks = [e for e in events if isinstance(e, FallbackNoticeEvent)]
    assert len(fallbacks) == 0


async def test_trivial_scope_tool_promotion_safety_net() -> None:
    """scope='trivial' uses _task_direct_monitored → PromotionEvent fires when tool threshold reached."""
    tools = []
    for i in range(1, _TOOL_PROMOTION_THRESHOLD + 1):
        tools.append(ToolStarted(name=f"Tool{i}", id=i))
        tools.append(ToolResult(content=f"r{i}", id=i))
    tools.append(Response(content="Should not be yielded"))

    decomposer = _mock_decomposer(
        answer_events=tools,
        route_task_result=TaskOutput(scope="trivial", summary="Quick", prompt="Tell me"),
    )
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "tell me something")

    promotions = [e for e in events if isinstance(e, PromotionEvent)]
    assert len(promotions) == 1
    assert promotions[0].tool_count == _TOOL_PROMOTION_THRESHOLD


async def test_small_scope_tool_promotion_safety_net() -> None:
    """scope='small' uses _task_direct_monitored → PromotionEvent fires when tool threshold reached."""
    tools = []
    for i in range(1, _TOOL_PROMOTION_THRESHOLD + 1):
        tools.append(ToolStarted(name=f"Tool{i}", id=i))
        tools.append(ToolResult(content=f"r{i}", id=i))
    tools.append(Response(content="Should not be yielded"))

    decomposer = _mock_decomposer(
        answer_events=tools,
        route_task_result=TaskOutput(scope="small", summary="Small", prompt="Do it"),
    )
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "do it")

    promotions = [e for e in events if isinstance(e, PromotionEvent)]
    assert len(promotions) == 1
    assert promotions[0].tool_count == _TOOL_PROMOTION_THRESHOLD


async def test_task_direct_routing_event_before_answer() -> None:
    """For scope='small', RoutingEvent(routing='task_direct') appears before any Response."""
    decomposer = _mock_decomposer(
        answer_events=[ThinkingResult(content="thinking..."), Response(content="Done.")],
        route_task_result=TaskOutput(scope="small", summary="Quick", prompt="Do it"),
    )
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "do it")

    routing_idx = next(i for i, e in enumerate(events) if isinstance(e, RoutingEvent))
    response_idx = next(i for i, e in enumerate(events) if isinstance(e, Response))
    assert routing_idx < response_idx

    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert routing[0].routing == "task_direct"


async def test_task_direct_uses_enriched_prompt_when_resolved() -> None:
    """When route_task returns a prompt that differs from original, answer() receives enriched format."""
    received_prompts: list[str] = []

    async def _capturing_answer(prompt: str):
        received_prompts.append(prompt)
        yield Response(content="Done.")

    decomposer = _mock_decomposer(
        route_task_result=TaskOutput(
            scope="small",
            summary="Fix typo",
            prompt="Fix the typo in /path/to/README.md line 5",
        ),
    )
    decomposer.flush_pending_context = MagicMock()
    decomposer.answer = _capturing_answer

    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=decomposer,
    )
    await _collect(pipeline, "fix the typo in readme")

    assert len(received_prompts) == 1
    prompt_used = received_prompts[0]
    assert "[Original user request]" in prompt_used
    assert "[Resolved context]" in prompt_used
    assert "fix the typo in readme" in prompt_used
    assert "Fix the typo in /path/to/README.md line 5" in prompt_used


async def test_task_direct_uses_original_prompt_when_not_enriched() -> None:
    """When route_task returns the same prompt as original, answer() receives the original unchanged."""
    received_prompts: list[str] = []

    async def _capturing_answer(prompt: str):
        received_prompts.append(prompt)
        yield Response(content="Done.")

    original = "fix the typo in readme"
    decomposer = _mock_decomposer(
        route_task_result=TaskOutput(
            scope="small",
            summary="Fix typo",
            prompt=original,  # same as original → no enrichment
        ),
    )
    decomposer.flush_pending_context = MagicMock()
    decomposer.answer = _capturing_answer

    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=decomposer,
    )
    await _collect(pipeline, original)

    assert len(received_prompts) == 1
    prompt_used = received_prompts[0]
    assert "[Original user request]" not in prompt_used
    assert prompt_used == original


async def test_chat_high_conf_still_never_calls_route_task() -> None:
    """Regression guard: chat + high confidence bypasses route_task entirely."""
    decomposer = _mock_decomposer(answer_events=[Response(content="Hi!")])
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.95),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "hello there")

    decomposer.route_task.assert_not_awaited()

    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1

    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert routing[0].routing == "chat"


@pytest.mark.asyncio
async def test_defensive_guard_is_fallback_large_scope_routes_inline() -> None:
    """Defensive guard: is_fallback=True + scope='large' is demoted to inline (no PlanEvent, no flush)."""
    from archon.ai.agent_plan import AgentTask as _AgentTask
    from archon.ai.event_mapper import FallbackNoticeEvent

    decomposer = _mock_decomposer(
        route_task_result=TaskOutput(
            scope="large",
            summary="Big task",
            agents=[_AgentTask(id="a1", task="do it")],
            is_fallback=True,
            fallback_reason="Could not plan this task — attempting inline",
        ),
        answer_events=[Response(content="Done inline.")],
    )
    decomposer.flush_pending_context = MagicMock()
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "big task")

    # Must yield FallbackNoticeEvent (non-empty reason)
    assert any(isinstance(e, FallbackNoticeEvent) for e in events)
    # Must NOT spawn agents — no PlanEvent
    assert not any(isinstance(e, PlanEvent) for e in events)
    # Must route inline — RoutingEvent routing="task_direct"
    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert any(r.routing == "task_direct" for r in routing)
    # Must yield Response from inline execution
    assert any(isinstance(e, Response) for e in events)
    # flush_pending_context must NOT be called (not large scope after demotion)
    decomposer.flush_pending_context.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# Fix A — flush_pending_context() called on promotion path
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_flush_called_on_tool_promotion() -> None:
    """flush_pending_context() is called when tool promotion fires in _task_direct_monitored."""
    tools = [ToolStarted(name="Tool1", id="1"), ToolStarted(name="Tool2", id="2")]
    decomposer = _mock_decomposer(answer_events=tools)
    with patch("archon.ai.pipeline.Classifier", return_value=_mock_classifier()):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            pipeline = Pipeline(tool_promotion_threshold=2)
    events = [e async for e in pipeline._task_direct_monitored("do something")]
    assert any(isinstance(e, PromotionEvent) for e in events)
    decomposer.flush_pending_context.assert_called_once()


# ──────────────────────────────────────────────────────────────────
# Fix B — promotion prompt notes pending (None) tool result
# ──────────────────────────────────────────────────────────────────


def test_build_promotion_prompt_notes_pending_result() -> None:
    """_build_promotion_prompt marks tool results not yet available at promotion time."""
    from archon.ai.pipeline import _build_promotion_prompt
    started = ToolStarted(name="Write", input="output.py", id="1")
    result = _build_promotion_prompt([(started, None)], "fix the bug")
    assert "not yet available" in result


# ──────────────────────────────────────────────────────────────────
# Fix C — empty fallback_reason suppresses FallbackNoticeEvent (silent internal routing)
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fallback_notice_event_suppressed_when_reason_empty() -> None:
    """FallbackNoticeEvent is NOT emitted when TaskOutput.fallback_reason is empty.

    Timeout-based fallbacks use fallback_reason="" because the system is working
    fine — the fallback is a silent internal routing decision, not a user-visible error.
    """
    decomposer = _mock_decomposer(
        route_task_result=TaskOutput(scope="small", summary="x", prompt="do it", is_fallback=True, fallback_reason=""),
        answer_events=[Response(content="Done inline.")],
    )
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "do it")
    fallback_events = [e for e in events if isinstance(e, FallbackNoticeEvent)]
    assert len(fallback_events) == 0


# ──────────────────────────────────────────────────────────────────
# Fix E — _mock_decomposer has explicit flush_pending_context mock
# ──────────────────────────────────────────────────────────────────


def test_mock_decomposer_has_explicit_flush_pending_context() -> None:
    """_mock_decomposer sets flush_pending_context as an explicit MagicMock."""
    decomposer = _mock_decomposer()
    assert hasattr(decomposer, "flush_pending_context")
    assert callable(decomposer.flush_pending_context)
    decomposer.flush_pending_context()
    decomposer.flush_pending_context.assert_called_once()


# ──────────────────────────────────────────────────────────────────
# Fix F — tool_promotion_threshold=0 disables promotion
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_promotion_threshold_zero_disables_promotion() -> None:
    """tool_promotion_threshold=0 disables promotion — PromotionEvent never emitted."""
    tools: list = []
    for i in range(1, 22):
        tools.append(ToolStarted(name=f"Tool{i}", id=str(i)))
        tools.append(ToolResult(content=f"r{i}", id=str(i)))
    tools.append(Response(content="All done"))

    decomposer = _mock_decomposer(answer_events=tools)
    with patch("archon.ai.pipeline.Classifier", return_value=_mock_classifier()):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            pipeline = Pipeline(tool_promotion_threshold=0)
    events = [e async for e in pipeline._task_direct_monitored("do many things")]
    assert not any(isinstance(e, PromotionEvent) for e in events)
    tool_starts = [e for e in events if isinstance(e, ToolStarted)]
    assert len(tool_starts) == 21


# ──────────────────────────────────────────────────────────────────
# Fix G — tool_promotion_threshold=1 promotes on first tool
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_promotion_threshold_one_promotes_on_first_tool() -> None:
    """tool_promotion_threshold=1 promotes after the very first tool call."""
    tools = [ToolStarted(name="Read", id="1"), ToolResult(content="file content", id="1"), Response(content="Done")]
    decomposer = _mock_decomposer(answer_events=tools)
    with patch("archon.ai.pipeline.Classifier", return_value=_mock_classifier()):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            pipeline = Pipeline(tool_promotion_threshold=1)
    events = [e async for e in pipeline._task_direct_monitored("do something")]
    promotions = [e for e in events if isinstance(e, PromotionEvent)]
    assert len(promotions) == 1
    assert promotions[0].tool_count == 1
    assert not any(isinstance(e, Response) for e in events)


# ──────────────────────────────────────────────────────────────────
# Fix H — ToolStarted without matching ToolResult (mid-stream error)
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_direct_monitored_tool_started_without_result() -> None:
    """ToolStarted followed by ErrorEvent (no ToolResult) does not crash and yields both events."""
    tools = [ToolStarted(name="Read", id="1"), ErrorEvent(message="session failed", source="sdk")]
    decomposer = _mock_decomposer(answer_events=tools)
    pipeline, _, _ = _make_pipeline(decomposer=decomposer)
    events = [e async for e in pipeline._task_direct_monitored("do something")]
    assert any(isinstance(e, ToolStarted) for e in events)
    assert any(isinstance(e, ErrorEvent) for e in events)


@pytest.mark.asyncio
async def test_task_direct_monitored_times_out(monkeypatch) -> None:
    """If answer() hangs indefinitely, _task_direct_monitored times out and yields ErrorEvent.

    Bug 03: 19-minute hang for simple response. A wall-clock timeout guards the generator.
    """
    import asyncio as _asyncio

    async def _hanging_answer(prompt: str) -> AsyncGenerator:
        await _asyncio.sleep(9999)
        return
        yield  # noqa: E501 — make it an async generator

    decomposer = _mock_decomposer()
    decomposer.answer = _hanging_answer

    monkeypatch.setattr("archon.ai.pipeline._TASK_DIRECT_TIMEOUT_S", 0.05)
    pipeline, _, _ = _make_pipeline(decomposer=decomposer)
    events = [e async for e in pipeline._task_direct_monitored("do something")]

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(errors) == 1
    assert "timed out" in errors[0].message.lower() or "timeout" in errors[0].message.lower()


@pytest.mark.asyncio
async def test_timeout_does_not_deadlock_next_call(monkeypatch) -> None:
    """After a timeout, gen.aclose() releases the real lock so the next call is not blocked.

    Uses a real asyncio.Lock (simulating ClaudeSession._send_lock) acquired inside
    answer() and released in its finally block — proving that gen.aclose() propagates
    the cancellation into the generator and releases the lock.
    """
    import asyncio as _asyncio

    send_lock = _asyncio.Lock()

    async def _hanging_answer(prompt: str) -> AsyncGenerator:
        async with send_lock:
            await _asyncio.sleep(9999)
            yield  # make it an async generator

    call_count = 0

    async def _answer_dispatcher(prompt: str) -> AsyncGenerator:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            async for event in _hanging_answer(prompt):
                yield event
        else:
            yield Response(content="second call succeeded")

    decomposer = _mock_decomposer()
    decomposer.answer = _answer_dispatcher

    monkeypatch.setattr("archon.ai.pipeline._TASK_DIRECT_TIMEOUT_S", 0.05)
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.95),
        decomposer=decomposer,
    )

    # First call times out — route through send() so pipeline._lock is actually acquired
    first_events = [e async for e in pipeline.send("first")]
    errors = [e for e in first_events if isinstance(e, ErrorEvent)]
    assert len(errors) == 1, "Expected timeout ErrorEvent on first call"
    assert not send_lock.locked(), "send_lock must be released after gen.aclose() — lock leak detected"
    # pipeline._lock is the asyncio.Lock that serializes concurrent send() calls.
    # This assertion is now meaningful: send() held _lock during the first call, and it
    # must be released after the timeout so the next call is not blocked.
    assert not pipeline._lock.locked(), "pipeline._lock must be released after timeout — deadlock risk"

    # Second call must complete without deadlock
    second_events = await _asyncio.wait_for(
        _collect_list(pipeline.send("second")),
        timeout=2.0,
    )
    responses = [e for e in second_events if isinstance(e, Response)]
    assert len(responses) == 1, "Second call must succeed after timeout — no deadlock"
    assert responses[0].content == "second call succeeded"


async def _collect_list(gen) -> list:
    return [e async for e in gen]


# ──────────────────────────────────────────────────────────────────
# Concurrency serialization — Bug 04/05/06
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_serializes_concurrent_calls() -> None:
    """Two concurrent send() calls must be serialized: second waits for first to finish."""
    import asyncio

    execution_order: list[str] = []

    async def _slow_answer(prompt: str) -> AsyncGenerator:
        execution_order.append(f"start:{prompt}")
        await asyncio.sleep(0)  # yield control so the second send() can try to acquire the lock
        yield Response(content=f"Done:{prompt}")
        execution_order.append(f"end:{prompt}")

    decomposer = _mock_decomposer()
    decomposer.answer = _slow_answer
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.95),
        decomposer=decomposer,
    )

    async def _run(prompt: str) -> list[Event]:
        return [e async for e in pipeline.send(prompt)]

    results = await asyncio.gather(_run("first"), _run("second"))

    # Both calls must have completed
    assert len(results[0]) > 0
    assert len(results[1]) > 0

    # Serialized: the second "start" must appear AFTER the first "end"
    first_end = execution_order.index("end:first")
    second_start = execution_order.index("start:second")
    assert second_start > first_end, (
        f"Second send() started before first finished: {execution_order}"
    )


@pytest.mark.asyncio
async def test_is_processing_true_while_lock_held() -> None:
    """is_processing must return True as soon as send() acquires the lock (before decomposer starts)."""
    import asyncio

    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.95),
    )

    # Before any send, not processing
    assert pipeline.is_processing is False

    # Start iterating but pause after classification so we can check is_processing
    gen = pipeline.send("hello")
    # Advance to first yielded event — lock is now held
    first_event = await gen.__anext__()
    assert isinstance(first_event, ClassificationEvent)
    assert pipeline.is_processing is True

    # Drain and close
    async for _ in gen:
        pass


@pytest.mark.asyncio
async def test_send_lock_released_on_generator_abandon() -> None:
    """If the caller abandons the generator early (aclose()), the lock must be released."""
    import asyncio

    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.95),
    )

    gen = pipeline.send("hello")
    # Advance once so lock is acquired
    await gen.__anext__()
    # Abandon the generator without exhausting it
    await gen.aclose()

    # Lock must be released — a new send() should complete without hanging
    done = asyncio.create_task(_collect(pipeline, "after"))
    result = await asyncio.wait_for(done, timeout=2.0)
    assert len(result) > 0


# ──────────────────────────────────────────────────────────────────
# classify() timeout — Bug M5
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_timeout_falls_back_to_task_intent() -> None:
    """When classify() hangs past _CLASSIFY_TIMEOUT_S, pipeline falls back to task intent
    and still delivers a response — the lock must not be held indefinitely."""
    import asyncio

    # Classifier that hangs on the first call, returns normally on subsequent calls
    call_count = 0
    hanging_classifier = MagicMock()
    hanging_classifier.start = AsyncMock()
    hanging_classifier.stop = AsyncMock()
    hanging_classifier.model = "claude-haiku-4-5-20251001"
    hanging_classifier.usage_stats = None

    async def _hang_once(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            await asyncio.sleep(9999)
        return ClassifierResult(
            classification=Classification(intent="task", confidence=0.9),
            raw_response='{"intent": "task", "confidence": 0.9}',
            duration_s=0.1,
        )

    hanging_classifier.classify = _hang_once

    pipeline, _, _ = _make_pipeline(classifier=hanging_classifier)

    # Run with a very short timeout by patching _CLASSIFY_TIMEOUT_S
    with patch("archon.ai.pipeline._CLASSIFY_TIMEOUT_S", 0.05):
        events = await asyncio.wait_for(_collect(pipeline, "do something"), timeout=2.0)

    # Must yield a ClassificationEvent with the fallback task intent
    classification_events = [e for e in events if isinstance(e, ClassificationEvent)]
    assert len(classification_events) == 1
    ce = classification_events[0]
    assert ce.intent == "task"
    assert ce.confidence == 0.0

    # Lock must be released after timeout — a second send() must complete
    done = asyncio.create_task(_collect(pipeline, "follow-up"))
    result = await asyncio.wait_for(done, timeout=2.0)
    assert len(result) > 0


# ──────────────────────────────────────────────────────────────────
# Fix F1 — promotion aclose() must have a timeout
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_promotion_aclose_timeout_releases_lock(monkeypatch) -> None:
    """When promotion triggers and gen.aclose() hangs, the pipeline must not hold
    the lock indefinitely. The aclose() must be bounded by _ACLOSE_TIMEOUT_S so that
    a subsequent send() can complete.

    Bug F1: bare 'await gen.aclose()' in the promotion path had no timeout — a hung
    SDK subprocess during cleanup would permanently block the Pipeline._lock.
    """
    import asyncio as _asyncio

    monkeypatch.setattr("archon.ai.pipeline._ACLOSE_TIMEOUT_S", 0.05)

    hang_event = _asyncio.Event()

    async def _answer_with_hanging_aclose(prompt: str) -> AsyncGenerator:
        try:
            yield ToolStarted(name="Read", id="1")
            # Generator hangs here — simulates SDK hang after promotion
            await _asyncio.sleep(9999)
            yield Response(content="never reached")
        except GeneratorExit:
            # Simulate a hung aclose(): block until test releases it
            await _asyncio.shield(_asyncio.sleep(9999))

    decomposer = _mock_decomposer()
    decomposer.answer = _answer_with_hanging_aclose

    with patch("archon.ai.pipeline.Classifier", return_value=_mock_classifier(intent="chat", confidence=0.95)):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            pipeline = Pipeline(tool_promotion_threshold=1)

    # The send() must complete within _ACLOSE_TIMEOUT_S + margin (not hang forever)
    events = await _asyncio.wait_for(
        _collect_list(pipeline.send("trigger promotion")),
        timeout=2.0,
    )

    # A PromotionEvent must have been emitted
    assert any(isinstance(e, PromotionEvent) for e in events), "PromotionEvent expected"

    # The pipeline lock must be released so a second send() can proceed
    assert not pipeline._lock.locked(), "pipeline._lock must be released after promotion aclose() timeout"

    # A follow-up send() must complete without deadlock
    async def _simple_answer(prompt: str) -> AsyncGenerator:
        yield Response(content="ok")

    decomposer.answer = _simple_answer
    follow_up = await _asyncio.wait_for(
        _collect_list(pipeline.send("follow-up")),
        timeout=2.0,
    )
    assert any(isinstance(e, Response) for e in follow_up), "Follow-up send() must succeed after promotion timeout"
