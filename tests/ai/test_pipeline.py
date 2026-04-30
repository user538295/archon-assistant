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
    ContextInjectedEvent,
    ErrorEvent,
    Event,
    FallbackNoticeEvent,
    PlanEvent,
    PromotionEvent,
    RecoveryEvent,
    Response,
    RoutingEvent,
    ThinkingResult,
    ToolResult,
    ToolStarted,
)
from archon.ai.pipeline import Pipeline, _TOOL_PROMOTION_THRESHOLD, _read_recent_user_messages


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
    from tests.conftest import _RouteTaskGenMock
    decomposer.route_task = _RouteTaskGenMock(
        route_task_result or TaskOutput(scope="small", summary="Quick task", prompt="Do the thing")
    )
    decomposer.activate_skill = MagicMock()
    decomposer.inject_context = MagicMock()
    decomposer.track_context = MagicMock()
    decomposer.flush_pending_context = MagicMock()
    decomposer.recover_session = AsyncMock()
    decomposer.force_kill_for_recovery = MagicMock()
    decomposer.restart_session = AsyncMock()
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


async def test_routing_event_model_fallback_when_none() -> None:
    """Bug 22: when model is None (no [models] section), use '(sdk-default)' instead of ''."""
    pipeline, _, _ = _make_pipeline(
        decomposer=_mock_decomposer(model=None),
    )
    events = await _collect(pipeline)

    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert routing[0].model == "(sdk-default)"


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


def test_pipeline_context_percentage_delegates() -> None:
    """Pipeline.context_percentage() must delegate to decomposer, not recompute from usage_stats."""
    pipeline, _, decomposer = _make_pipeline()
    decomposer.context_percentage = MagicMock(return_value=37)
    assert pipeline.context_percentage() == 37
    decomposer.context_percentage.assert_called_once_with()


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
    decomposer.inject_context.assert_called_once_with("some context", "context", None)


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


async def test_generator_abandoned_on_promotion() -> None:
    """When promotion triggers mid-stream, the generator is abandoned (not aclose'd).

    gen.aclose() triggers anyio cancel-scope poisoning that breaks ALL
    subsequent anyio operations in the same asyncio task.  Instead, the
    subprocess is killed via force_kill_for_recovery() and the session
    is restarted in a clean asyncio task.
    """
    post_promotion_yielded = False

    async def _answer_with_enough_tools(prompt: str):
        nonlocal post_promotion_yielded
        for i in range(1, _TOOL_PROMOTION_THRESHOLD + 1):
            yield ToolStarted(name=f"Tool{i}", id=i)
            yield ToolResult(content=f"r{i}", id=i)
        # This should never be reached — promotion stops iteration before here.
        post_promotion_yielded = True
        yield Response(content="should not be yielded")

    decomposer = _mock_decomposer()
    decomposer.answer = _answer_with_enough_tools

    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.95),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "do many tools")

    promotions = [e for e in events if isinstance(e, PromotionEvent)]
    assert len(promotions) == 1, "Expected exactly one PromotionEvent"
    assert not post_promotion_yielded, "Generator continued after promotion"
    # Session killed + restarted (not aclose'd)
    decomposer.force_kill_for_recovery.assert_called_once()
    decomposer.restart_session.assert_awaited_once()


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
    events = [e async for e in pipeline._task_direct_monitored("do something", Classification(intent="task", confidence=0.95))]
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


@pytest.mark.asyncio
async def test_pipeline_emits_fallback_notice_event_on_router_timeout() -> None:
    """Pipeline emits FallbackNoticeEvent with correct reason when router times out.

    FIX-028 Task 2.2: When route_task returns is_fallback=True with the timeout
    reason string, Pipeline must emit FallbackNoticeEvent unconditionally (regardless
    of notification mode — that filtering happens in handler.py, not Pipeline).
    """
    decomposer = _mock_decomposer(
        route_task_result=TaskOutput(
            scope="small",
            prompt="do it",
            is_fallback=True,
            fallback_reason="Router timed out — handling directly",
        ),
        answer_events=[Response(content="Done inline.")],
    )
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.9),
        decomposer=decomposer,
    )
    events = await _collect(pipeline, "do it")

    fallback_events = [e for e in events if isinstance(e, FallbackNoticeEvent)]
    assert len(fallback_events) == 1
    assert fallback_events[0].reason == "Router timed out — handling directly"


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
    events = [e async for e in pipeline._task_direct_monitored("do many things", Classification(intent="task", confidence=0.95))]
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
    events = [e async for e in pipeline._task_direct_monitored("do something", Classification(intent="task", confidence=0.95))]
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
    events = [e async for e in pipeline._task_direct_monitored("do something", Classification(intent="task", confidence=0.95))]
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
    monkeypatch.setattr("archon.ai.pipeline._RETRY_TIMEOUT_S", 0.05)
    pipeline, _, _ = _make_pipeline(decomposer=decomposer)
    events = [e async for e in pipeline._task_direct_monitored("do something", Classification(intent="task", confidence=0.95))]

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

    # First call times out — route through send() so pipeline._lock is actually acquired.
    # With recovery enabled, the timeout triggers recovery + retry (call_count==2 yields Response).
    first_events = [e async for e in pipeline.send("first")]
    from archon.ai.event_mapper import RecoveryEvent
    recovery_events = [e for e in first_events if isinstance(e, RecoveryEvent)]
    assert len(recovery_events) >= 1, "Expected RecoveryEvent(s) on timeout"
    assert recovery_events[0].phase == "timeout_detected"
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


# ──────────────────────────────────────────────────────────────────
# Issue #18: bg MCP headers forwarded to Decomposer
# ──────────────────────────────────────────────────────────────────


def test_bg_mcp_headers_forwarded_to_decomposer() -> None:
    """background_agent_mcp_headers must be forwarded to Decomposer."""
    headers = {"Authorization": "Bearer bg-token"}
    with patch("archon.ai.pipeline.Classifier") as MockClassifier:
        MockClassifier.return_value = MagicMock()
        with patch("archon.ai.pipeline.Decomposer") as MockDecomposer:
            MockDecomposer.return_value = MagicMock()
            Pipeline(
                background_agent_mcp_url="http://localhost:18182/mcp/1",
                background_agent_mcp_headers=headers,
            )

    _, kwargs = MockDecomposer.call_args
    assert kwargs.get("background_agent_mcp_headers") == headers


def test_bg_mcp_headers_none_when_not_provided_to_pipeline() -> None:
    """When background_agent_mcp_headers is not provided, Decomposer must receive None."""
    with patch("archon.ai.pipeline.Classifier") as MockClassifier:
        MockClassifier.return_value = MagicMock()
        with patch("archon.ai.pipeline.Decomposer") as MockDecomposer:
            MockDecomposer.return_value = MagicMock()
            Pipeline(background_agent_mcp_url="http://localhost:18182/mcp/1")

    _, kwargs = MockDecomposer.call_args
    assert kwargs.get("background_agent_mcp_headers") is None


# ──────────────────────────────────────────────────────────────────
# Task 1.1: search_url — Pipeline does NOT pass search_url to Classifier; Decomposer still gets it
# ──────────────────────────────────────────────────────────────────


def test_pipeline_does_not_pass_search_url_to_classifier() -> None:
    """Pipeline must NOT pass search_url to Classifier (Task 1.1)."""
    with patch("archon.ai.pipeline.Classifier") as MockClassifier:
        MockClassifier.return_value = MagicMock()
        with patch("archon.ai.pipeline.Decomposer") as MockDecomposer:
            MockDecomposer.return_value = MagicMock()
            Pipeline(search_url="http://localhost:6333")

    _, clf_kwargs = MockClassifier.call_args
    assert "search_url" not in clf_kwargs


def test_pipeline_passes_search_url_to_decomposer() -> None:
    """Pipeline must still pass search_url to Decomposer."""
    with patch("archon.ai.pipeline.Classifier") as MockClassifier:
        MockClassifier.return_value = MagicMock()
        with patch("archon.ai.pipeline.Decomposer") as MockDecomposer:
            MockDecomposer.return_value = MagicMock()
            Pipeline(search_url="http://localhost:6333")

    _, dec_kwargs = MockDecomposer.call_args
    assert dec_kwargs.get("search_url") == "http://localhost:6333"


def test_pipeline_inject_context_forwards_type() -> None:
    """Pipeline.inject_context forwards injection_type and detail to Decomposer."""
    pipeline, _, decomposer = _make_pipeline()
    pipeline.inject_context("x", "history", detail="f1.md")
    decomposer.inject_context.assert_called_once_with("x", "history", "f1.md")


def test_pipeline_inject_context_forwards_detail_none() -> None:
    """Pipeline.inject_context with no detail forwards None to Decomposer."""
    pipeline, _, decomposer = _make_pipeline()
    pipeline.inject_context("x", "history")
    decomposer.inject_context.assert_called_once_with("x", "history", None)


async def test_send_retags_context_injected_event_source_to_router() -> None:
    """Pipeline.send() must set source='router' on ContextInjectedEvents from route_task().

    The event is constructed with source='orchestrator' (default) by route_task.
    Pipeline.send() applies dataclasses.replace(item, source='router') to all events
    yielded by route_task — this test verifies that re-tagging actually happens.
    """
    from tests.conftest import _RouteTaskGenMock

    router_event = ContextInjectedEvent(
        injection_type="router_workspace_agents",
        size_value=100,
        size_unit="chars",
        source="orchestrator",  # default — will be re-tagged by Pipeline
    )
    task_output = TaskOutput(scope="small", summary="Routed", prompt="Do it")

    decomposer = _mock_decomposer()
    decomposer.route_task = _RouteTaskGenMock(task_output, events=[router_event])

    pipeline, _, _ = _make_pipeline(decomposer=decomposer)
    events = await _collect(pipeline)

    context_injected = [e for e in events if isinstance(e, ContextInjectedEvent)]
    assert context_injected, "Expected at least one ContextInjectedEvent in pipeline output"
    assert context_injected[0].source == "router", (
        f"Expected source='router' after Pipeline re-tagging, got source='{context_injected[0].source}'"
    )


# ──────────────────────────────────────────────────────────────────
# context_window_overrides forwarding
# ──────────────────────────────────────────────────────────────────


def test_pipeline_forwards_overrides_to_decomposer() -> None:
    """Pipeline passes context_window_overrides through to Decomposer._session."""
    overrides = {"claude-sonnet-4-6": 500_000}

    with patch("archon.ai.pipeline.Classifier"):
        with patch("archon.ai.pipeline.Decomposer") as MockDecomposer:
            mock_decomposer_instance = MagicMock()
            MockDecomposer.return_value = mock_decomposer_instance
            Pipeline(context_window_overrides=overrides)

    _, kwargs = MockDecomposer.call_args
    assert kwargs.get("context_window_overrides") == overrides


# ──────────────────────────────────────────────────────────────────
# router_gen.aclose() timeout protection
# ──────────────────────────────────────────────────────────────────


async def test_pipeline_router_gen_aclose_has_timeout(caplog) -> None:
    """pipeline.send() must not hang if router_gen.aclose() blocks indefinitely.

    Regression guard for FIX-028 Task 1.2: the aclose() call must be wrapped in
    asyncio.wait_for(_ACLOSE_TIMEOUT_S) so that a stuck generator cannot block the
    pipeline forever.  A warning must be logged when the timeout fires.
    """
    import asyncio
    import logging
    from unittest.mock import patch

    _FAST_TIMEOUT = 0.1

    task_output = TaskOutput(scope="small", summary="Quick task", prompt="Do the thing")

    # Build a router_gen whose aclose() hangs forever.
    class _HangingGen:
        """Async generator that yields task_output then hangs in aclose()."""

        def __init__(self) -> None:
            self._yielded = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._yielded:
                self._yielded = True
                return task_output
            raise StopAsyncIteration

        async def aclose(self):
            await asyncio.sleep(9999)  # hang indefinitely

    hanging_gen = _HangingGen()

    decomposer = _mock_decomposer()
    decomposer.route_task = lambda *args, **kwargs: hanging_gen

    pipeline, _, _ = _make_pipeline(decomposer=decomposer)

    with patch("archon.ai.pipeline._ACLOSE_TIMEOUT_S", _FAST_TIMEOUT):
        start = asyncio.get_event_loop().time()
        with caplog.at_level(logging.WARNING, logger="archon"):
            await asyncio.wait_for(_collect(pipeline), timeout=_FAST_TIMEOUT + 2)
        elapsed = asyncio.get_event_loop().time() - start

    # Must complete within the mocked fast timeout (not hang for 9999 s)
    assert elapsed < _FAST_TIMEOUT + 1, (
        f"pipeline.send() took {elapsed:.1f}s — aclose() timeout not enforced"
    )
    # Warning must be logged when timeout fires
    assert any("router_gen.aclose()" in r.message for r in caplog.records), (
        "Expected warning about router_gen.aclose() timeout, got: " + str([r.message for r in caplog.records])
    )


# ──────────────────────────────────────────────────────────────────
# Rolling-deadline wait_for pattern — Task 1.3 (FIX-028)
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_direct_monitored_timeout_fires_during_consumer_async_work(monkeypatch) -> None:
    """Rolling-deadline timeout fires even when consumer does async work between iterations.

    The consumer calls await asyncio.sleep(0.01) between events; the generator yields one
    event and then hangs. With _TASK_DIRECT_TIMEOUT_S=0.05, the deadline should be exceeded
    and RecoveryEvent(phase='timeout_detected') must be yielded — not silence.
    """
    import asyncio as _asyncio

    async def _one_then_hang(prompt: str) -> AsyncGenerator:
        yield Response(content="partial")
        await _asyncio.sleep(9999)

    decomposer = _mock_decomposer()
    decomposer.answer = _one_then_hang

    monkeypatch.setattr("archon.ai.pipeline._TASK_DIRECT_TIMEOUT_S", 0.05)
    monkeypatch.setattr("archon.ai.pipeline._RETRY_TIMEOUT_S", 0.05)
    pipeline, _, _ = _make_pipeline(decomposer=decomposer)

    events: list[Event] = []
    async for event in pipeline._task_direct_monitored("do something", Classification(intent="task", confidence=0.95)):
        await _asyncio.sleep(0.01)  # async work between iterations
        events.append(event)

    recovery = [e for e in events if isinstance(e, RecoveryEvent)]
    assert any(e.phase == "timeout_detected" for e in recovery), (
        f"Expected RecoveryEvent(phase='timeout_detected'), got: {events}"
    )


@pytest.mark.asyncio
async def test_task_direct_monitored_aclose_called_on_timeout(monkeypatch) -> None:
    """gen.aclose() must be called when the primary loop times out."""
    import asyncio as _asyncio

    aclose_called = False

    class _HangingGen:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await _asyncio.sleep(9999)
            raise StopAsyncIteration

        async def aclose(self):
            nonlocal aclose_called
            aclose_called = True

    decomposer = _mock_decomposer()
    decomposer.answer = lambda prompt: _HangingGen()

    monkeypatch.setattr("archon.ai.pipeline._TASK_DIRECT_TIMEOUT_S", 0.05)
    monkeypatch.setattr("archon.ai.pipeline._RETRY_TIMEOUT_S", 0.05)
    pipeline, _, _ = _make_pipeline(decomposer=decomposer)

    events = [e async for e in pipeline._task_direct_monitored("do something", Classification(intent="task", confidence=0.95))]

    assert aclose_called, "gen.aclose() must be called after timeout"
    recovery = [e for e in events if isinstance(e, RecoveryEvent)]
    assert any(e.phase == "timeout_detected" for e in recovery)


@pytest.mark.asyncio
async def test_task_direct_monitored_aclose_cancelled_error_is_handled(monkeypatch) -> None:
    """CancelledError raised inside primary gen.aclose() during timeout recovery must not propagate.

    Only the FIRST gen (primary) raises CancelledError from aclose(). The retry gen (second call)
    completes normally so the retry cleanup does not also raise, which would be a separate issue.
    """
    import asyncio as _asyncio

    call_count = 0

    class _HangingGenAcloseRaises:
        """Primary gen: hangs in __anext__, raises CancelledError from aclose()."""

        def __aiter__(self):
            return self

        async def __anext__(self):
            await _asyncio.sleep(9999)
            raise StopAsyncIteration

        async def aclose(self):
            raise _asyncio.CancelledError("simulated stale cancel")

    class _NormalHangingGen:
        """Retry gen: also hangs (so retry times out), but aclose() completes normally."""

        def __aiter__(self):
            return self

        async def __anext__(self):
            await _asyncio.sleep(9999)
            raise StopAsyncIteration

        async def aclose(self):
            pass  # normal cleanup

    def _answer_factory(prompt: str):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _HangingGenAcloseRaises()
        return _NormalHangingGen()

    decomposer = _mock_decomposer()
    decomposer.answer = _answer_factory

    monkeypatch.setattr("archon.ai.pipeline._TASK_DIRECT_TIMEOUT_S", 0.05)
    monkeypatch.setattr("archon.ai.pipeline._RETRY_TIMEOUT_S", 0.05)
    pipeline, _, _ = _make_pipeline(decomposer=decomposer)

    # Must not raise — CancelledError from primary gen.aclose() must be swallowed
    events = [e async for e in pipeline._task_direct_monitored("do something", Classification(intent="task", confidence=0.95))]

    # Pipeline should still yield the recovery event, not crash
    recovery = [e for e in events if isinstance(e, RecoveryEvent)]
    assert any(e.phase == "timeout_detected" for e in recovery), (
        f"Expected RecoveryEvent after aclose() CancelledError, got: {events}"
    )


@pytest.mark.asyncio
async def test_task_direct_monitored_negative_remaining_time(monkeypatch) -> None:
    """The remaining <= 0 guard fires when the deadline elapses BETWEEN loop iterations.

    The generator yields two events quickly (no sleeps). loop.time() is mocked so that
    the second call (at the top of the second iteration) returns a value past the deadline.
    This means `remaining = deadline - loop.time()` is <= 0 on the second iteration and
    `if remaining <= 0: raise TimeoutError` fires BEFORE calling wait_for.
    RecoveryEvent(phase='timeout_detected') must be yielded and event_b must NOT
    be collected (from the primary gen — it is not reached before the timeout).
    """
    import asyncio as _asyncio

    event_a = Response(content="first")
    event_b = Response(content="second — should not be reached")

    answer_call_count = 0

    async def _answer_factory(prompt: str) -> AsyncGenerator:
        nonlocal answer_call_count
        answer_call_count += 1
        if answer_call_count == 1:
            # Primary gen: yields event_a quickly; event_b is never reached because
            # remaining <= 0 fires before the second wait_for call.
            yield event_a
            yield event_b  # never reached due to deadline
        else:
            # Retry gen: yields a simple response to let the retry path complete cleanly.
            yield Response(content="retry response")

    decomposer = _mock_decomposer()
    decomposer.answer = _answer_factory

    # Mock loop.time() to simulate time advancing past the deadline between iterations.
    # Call 1: deadline = loop.time() + TIMEOUT  (initial setup)
    # Call 2: remaining = deadline - loop.time()  (first iteration — must succeed, so stay at real_time)
    # Call 3+: remaining = deadline - loop.time()  (second iteration — return past deadline)
    real_loop = _asyncio.get_event_loop()
    real_time = real_loop.time()
    time_call_count = 0

    def _mock_time():
        nonlocal time_call_count
        time_call_count += 1
        # Calls 1-2: normal time (deadline setup + first iteration remaining check)
        if time_call_count <= 2:
            return real_time
        return real_time + 1000.0  # way past deadline — remaining <= 0 fires

    mock_loop = MagicMock(wraps=real_loop)
    mock_loop.time = _mock_time

    monkeypatch.setattr("archon.ai.pipeline._TASK_DIRECT_TIMEOUT_S", 0.05)
    monkeypatch.setattr("archon.ai.pipeline._RETRY_TIMEOUT_S", 0.05)
    pipeline, _, _ = _make_pipeline(decomposer=decomposer)

    with patch("archon.ai.pipeline.asyncio.get_running_loop", return_value=mock_loop):
        collected = [e async for e in pipeline._task_direct_monitored("do something", Classification(intent="task", confidence=0.95))]

    recovery = [e for e in collected if isinstance(e, RecoveryEvent)]
    assert any(e.phase == "timeout_detected" for e in recovery), (
        f"Expected RecoveryEvent(phase='timeout_detected') after inter-iteration deadline elapsed, got: {collected}"
    )
    # event_b must NOT have been yielded from the primary gen — the guard fired first
    assert event_b not in collected, (
        f"event_b should not be yielded when remaining <= 0 fires before second wait_for"
    )
    # event_a WAS yielded (first iteration completed normally)
    assert event_a in collected, (
        f"event_a should have been yielded in the first iteration, got: {collected}"
    )


@pytest.mark.asyncio
async def test_task_direct_monitored_happy_path_completes_with_delays() -> None:
    """Generator yields 3 events with small async delays; all complete well within deadline.

    Verifies that the sentinel/StopAsyncIteration handling in _task_direct_monitored
    works correctly for normal completion — no RecoveryEvent, no ErrorEvent.
    """
    import asyncio as _asyncio

    events_to_yield = [
        Response(content="first"),
        Response(content="second"),
        Response(content="third"),
    ]

    async def _delayed_gen(prompt: str) -> AsyncGenerator:
        for ev in events_to_yield:
            await _asyncio.sleep(0.001)
            yield ev

    decomposer = _mock_decomposer()
    decomposer.answer = _delayed_gen

    pipeline, _, _ = _make_pipeline(decomposer=decomposer)

    collected = [e async for e in pipeline._task_direct_monitored("do something", Classification(intent="task", confidence=0.95))]

    assert collected == events_to_yield, (
        f"Expected all 3 events, got: {collected}"
    )
    assert not any(isinstance(e, RecoveryEvent) for e in collected), (
        f"No RecoveryEvent expected for happy path, got: {collected}"
    )
    assert not any(isinstance(e, ErrorEvent) for e in collected), (
        f"No ErrorEvent expected for happy path, got: {collected}"
    )


@pytest.mark.asyncio
async def test_task_direct_retry_aclose_cancelled_error_is_handled(monkeypatch) -> None:
    """CancelledError raised inside retry_gen.aclose() must not propagate.

    Primary gen: hangs (triggers primary timeout).
    After recovery, retry gen: also hangs (triggers retry timeout).
    retry_gen.aclose() raises CancelledError in the retry finally block.
    Assert no exception propagates and ErrorEvent is yielded.
    """
    import asyncio as _asyncio

    call_count = 0

    class _HangingGenNormalClose:
        """Primary gen: hangs in __anext__, aclose() completes normally."""

        def __aiter__(self):
            return self

        async def __anext__(self):
            await _asyncio.sleep(9999)
            raise StopAsyncIteration

        async def aclose(self):
            pass  # normal cleanup

    class _HangingGenAcloseRaises:
        """Retry gen: hangs in __anext__, raises CancelledError from aclose()."""

        def __aiter__(self):
            return self

        async def __anext__(self):
            await _asyncio.sleep(9999)
            raise StopAsyncIteration

        async def aclose(self):
            raise _asyncio.CancelledError("simulated stale cancel from retry gen")

    def _answer_factory(prompt: str):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _HangingGenNormalClose()
        return _HangingGenAcloseRaises()

    decomposer = _mock_decomposer()
    decomposer.answer = _answer_factory

    monkeypatch.setattr("archon.ai.pipeline._TASK_DIRECT_TIMEOUT_S", 0.05)
    monkeypatch.setattr("archon.ai.pipeline._RETRY_TIMEOUT_S", 0.05)
    pipeline, _, _ = _make_pipeline(decomposer=decomposer)

    # Must not raise — CancelledError from retry_gen.aclose() must be swallowed
    events = [e async for e in pipeline._task_direct_monitored("do something", Classification(intent="task", confidence=0.95))]

    # Pipeline should yield ErrorEvent after retry timeout, not crash
    error_events = [e for e in events if isinstance(e, ErrorEvent)]
    assert error_events, (
        f"Expected ErrorEvent after retry timeout with aclose() CancelledError, got: {events}"
    )


@pytest.mark.asyncio
async def test_task_direct_retry_timeout_fires_during_consumer_async_work(monkeypatch) -> None:
    """Rolling-deadline timeout on the retry path fires even when consumer does async work.

    Scenario: primary gen hangs → triggers primary timeout → recovery → no BAM → retry path.
    The retry gen yields one event then hangs. The consumer does async sleep between events.
    With _RETRY_TIMEOUT_S=0.05, the deadline must be exceeded and ErrorEvent yielded.
    """
    import asyncio as _asyncio

    call_count = 0

    async def _answer_factory(prompt: str) -> AsyncGenerator:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Primary gen: hangs immediately
            await _asyncio.sleep(9999)
        else:
            # Retry gen: yield one event, then hang
            yield Response(content="retry partial")
            await _asyncio.sleep(9999)

    decomposer = _mock_decomposer()
    decomposer.answer = _answer_factory

    monkeypatch.setattr("archon.ai.pipeline._TASK_DIRECT_TIMEOUT_S", 0.05)
    monkeypatch.setattr("archon.ai.pipeline._RETRY_TIMEOUT_S", 0.05)
    pipeline, _, _ = _make_pipeline(decomposer=decomposer)

    events: list[Event] = []
    async for event in pipeline._task_direct_monitored("do something", Classification(intent="task", confidence=0.95)):
        await _asyncio.sleep(0.01)  # async work between iterations
        events.append(event)

    error_events = [e for e in events if isinstance(e, ErrorEvent)]
    assert error_events, (
        f"Expected ErrorEvent after retry timeout with consumer async work, got: {events}"
    )


@pytest.mark.asyncio
async def test_task_direct_retry_negative_remaining_time(monkeypatch) -> None:
    """Retry loop raises TimeoutError immediately when deadline already elapsed.

    Uses a very small _RETRY_TIMEOUT_S so the deadline is exceeded by the time the
    retry loop starts (no monkeypatching of loop.time() — that breaks asyncio internals).
    """
    import asyncio as _asyncio

    call_count = 0

    async def _answer_factory(prompt: str) -> AsyncGenerator:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Primary gen: hangs to trigger primary timeout
            await _asyncio.sleep(9999)
        else:
            # Retry gen: sleeps long enough to exhaust the tiny retry deadline
            await _asyncio.sleep(9999)
            yield Response(content="never reached")

    decomposer = _mock_decomposer()
    decomposer.answer = _answer_factory

    # Primary timeout short; retry timeout extremely short so deadline is likely
    # elapsed before the retry loop even starts (or fires on the first __anext__).
    monkeypatch.setattr("archon.ai.pipeline._TASK_DIRECT_TIMEOUT_S", 0.05)
    monkeypatch.setattr("archon.ai.pipeline._RETRY_TIMEOUT_S", 0.0001)
    pipeline, _, _ = _make_pipeline(decomposer=decomposer)

    events = [e async for e in pipeline._task_direct_monitored("do something", Classification(intent="task", confidence=0.95))]

    error_events = [e for e in events if isinstance(e, ErrorEvent)]
    assert error_events, (
        f"Expected ErrorEvent when retry deadline already elapsed, got: {events}"
    )


# ──────────────────────────────────────────────────────────────────
# Task 3.2 — Classifier events yielded unconditionally
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_yields_classifier_events_unconditionally() -> None:
    """ThinkingResult from classifier must appear before ClassificationEvent in stream."""
    classifier = _mock_classifier(intent="task", confidence=0.9)
    classifier.classify = AsyncMock(return_value=ClassifierResult(
        classification=Classification(intent="task", confidence=0.9),
        duration_s=0.1,
        events=[ThinkingResult(content="clf thinking")],
    ))

    pipeline, _, _ = _make_pipeline(classifier=classifier)
    events = await _collect(pipeline)

    thinking_events = [e for e in events if isinstance(e, ThinkingResult) and getattr(e, "source", "") == "classifier"]
    classification_events = [e for e in events if isinstance(e, ClassificationEvent)]
    assert len(thinking_events) == 1, f"Expected 1 classifier ThinkingResult, got: {thinking_events}"

    # classifier ThinkingResult must come before ClassificationEvent
    thinking_idx = events.index(thinking_events[0])
    clf_idx = events.index(classification_events[0])
    assert thinking_idx < clf_idx, "classifier ThinkingResult must precede ClassificationEvent"


@pytest.mark.asyncio
async def test_pipeline_classifier_events_stamped_with_classifier_source() -> None:
    """ThinkingResult from classifier must have source='classifier'."""
    classifier = _mock_classifier(intent="task", confidence=0.9)
    classifier.classify = AsyncMock(return_value=ClassifierResult(
        classification=Classification(intent="task", confidence=0.9),
        duration_s=0.1,
        events=[ThinkingResult(content="classifier is pondering")],
    ))

    pipeline, _, _ = _make_pipeline(classifier=classifier)
    events = await _collect(pipeline)

    thinking_events = [e for e in events if isinstance(e, ThinkingResult)]
    classifier_events = [e for e in thinking_events if getattr(e, "source", "") == "classifier"]
    assert len(classifier_events) == 1
    assert classifier_events[0].content == "classifier is pondering"
    assert classifier_events[0].source == "classifier"


@pytest.mark.asyncio
async def test_pipeline_drops_non_thinking_classifier_events() -> None:
    """Non-ThinkingResult events from classifier must be silently dropped."""
    classifier = _mock_classifier(intent="task", confidence=0.9)
    classifier.classify = AsyncMock(return_value=ClassifierResult(
        classification=Classification(intent="task", confidence=0.9),
        duration_s=0.1,
        events=[ErrorEvent(message="timeout")],
    ))

    pipeline, _, _ = _make_pipeline(classifier=classifier)
    events = await _collect(pipeline)

    # Assert the ErrorEvent is not in the stream at all (any source), not just source="classifier"
    leaked_error_events = [
        e for e in events
        if isinstance(e, ErrorEvent) and getattr(e, "message", "") == "timeout"
    ]
    assert not leaked_error_events, (
        f"Non-ThinkingResult classifier events must be dropped entirely, got: {leaked_error_events}"
    )


# ──────────────────────────────────────────────────────────────────
# Task 1.1 — _retry_after_timeout() helper
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_after_timeout_helper_streams_events(monkeypatch) -> None:
    """_retry_after_timeout() yields events from decomposer on recovered session."""
    from archon.ai.event_mapper import Response

    decomposer = _mock_decomposer(answer_events=[Response(content="Retried successfully.")])
    pipeline, _, _ = _make_pipeline(decomposer=decomposer)

    events = [e async for e in pipeline._retry_after_timeout([], "original prompt")]

    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1
    assert responses[0].content == "Retried successfully."


@pytest.mark.asyncio
async def test_retry_after_timeout_handles_secondary_timeout(monkeypatch) -> None:
    """When retry generator hangs, secondary recover_session() is called and ErrorEvent yielded."""
    import asyncio as _asyncio

    async def _hanging_answer(prompt: str) -> AsyncGenerator:
        await _asyncio.sleep(9999)
        yield  # never reached — makes it an async generator

    decomposer = _mock_decomposer()
    decomposer.answer = _hanging_answer
    decomposer.recover_session = AsyncMock()

    monkeypatch.setattr("archon.ai.pipeline._RETRY_TIMEOUT_S", 0.01)
    pipeline, _, _ = _make_pipeline(decomposer=decomposer)

    events = [e async for e in pipeline._retry_after_timeout([], "some prompt")]

    error_events = [e for e in events if isinstance(e, ErrorEvent)]
    assert error_events, f"Expected ErrorEvent when retry hangs, got: {events}"
    assert decomposer.recover_session.await_count == 1, (
        f"Expected exactly 1 secondary recover_session() call, got: {decomposer.recover_session.await_count}"
    )
    # Message must reference both the primary timeout and the retry timeout
    msg = error_events[0].message
    assert "Retry also timed out" in msg, f"Expected 'Retry also timed out' in error message, got: {msg!r}"
    assert "timed out after" in msg, f"Expected timeout duration reference in error message, got: {msg!r}"


@pytest.mark.asyncio
async def test_retry_after_timeout_secondary_recovery_timeout(monkeypatch) -> None:
    """When both retry and secondary recovery hang, ErrorEvent yielded with no propagation."""
    import asyncio as _asyncio

    async def _hanging_answer(prompt: str) -> AsyncGenerator:
        await _asyncio.sleep(9999)
        yield  # never reached

    async def _hanging_recover():
        await _asyncio.sleep(9999)

    decomposer = _mock_decomposer()
    decomposer.answer = _hanging_answer
    decomposer.recover_session = AsyncMock(side_effect=_hanging_recover)

    monkeypatch.setattr("archon.ai.pipeline._RETRY_TIMEOUT_S", 0.01)
    monkeypatch.setattr("archon.ai.pipeline._RECOVERY_TIMEOUT_S", 0.01)
    pipeline, _, _ = _make_pipeline(decomposer=decomposer)

    # Must not raise — both timeouts must be handled gracefully
    events = [e async for e in pipeline._retry_after_timeout([], "some prompt")]

    error_events = [e for e in events if isinstance(e, ErrorEvent)]
    assert error_events, f"Expected ErrorEvent when both retry and recovery hang, got: {events}"


@pytest.mark.asyncio
async def test_retry_after_timeout_secondary_recovery_fails(monkeypatch) -> None:
    """When retry hangs and recovery raises, ErrorEvent yielded; recover_session called exactly once."""
    import asyncio as _asyncio

    async def _hanging_answer(prompt: str) -> AsyncGenerator:
        await _asyncio.sleep(9999)
        yield  # never reached

    decomposer = _mock_decomposer()
    decomposer.answer = _hanging_answer
    decomposer.recover_session = AsyncMock(side_effect=RuntimeError("boom"))

    monkeypatch.setattr("archon.ai.pipeline._RETRY_TIMEOUT_S", 0.01)
    pipeline, _, _ = _make_pipeline(decomposer=decomposer)

    events = [e async for e in pipeline._retry_after_timeout([], "some prompt")]

    error_events = [e for e in events if isinstance(e, ErrorEvent)]
    assert error_events, f"Expected ErrorEvent when recovery raises RuntimeError, got: {events}"
    assert decomposer.recover_session.await_count == 1, (
        f"Expected exactly 1 recover_session() call, got: {decomposer.recover_session.await_count}"
    )


@pytest.mark.asyncio
async def test_retry_path_does_not_reclose_original_gen(monkeypatch) -> None:
    """The outer finally calls gen.aclose() exactly once (in timeout handler), never again.

    To verify: count aclose() calls on the primary generator. The timeout handler closes it
    once (setting gen_closed=True). The outer finally must skip it because gen_closed is True.
    Total aclose() count must be exactly 1.
    """
    import asyncio as _asyncio

    aclose_count = 0

    class _CountingHangingGen:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await _asyncio.sleep(9999)
            raise StopAsyncIteration

        async def aclose(self):
            nonlocal aclose_count
            aclose_count += 1

    call_count = 0

    def _answer_factory(prompt: str):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _CountingHangingGen()
        # Retry gen: complete quickly
        async def _quick():
            yield Response(content="retry done")
        return _quick()

    decomposer = _mock_decomposer()
    decomposer.answer = _answer_factory

    monkeypatch.setattr("archon.ai.pipeline._TASK_DIRECT_TIMEOUT_S", 0.05)
    pipeline, _, _ = _make_pipeline(decomposer=decomposer)

    events = [e async for e in pipeline._task_direct_monitored("do something", Classification(intent="task", confidence=0.95))]

    assert aclose_count == 1, (
        f"gen.aclose() must be called exactly once (in timeout handler), got {aclose_count} calls"
    )


@pytest.mark.asyncio
async def test_retry_after_timeout_closes_generator_on_exception(monkeypatch) -> None:
    """retry_gen.aclose() is called in finally even when __anext__ raises a RuntimeError.

    Only TimeoutError is caught by _retry_after_timeout; RuntimeError propagates.
    """
    aclose_called = False

    class _RaisingGen:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise RuntimeError("iter error")

        async def aclose(self):
            nonlocal aclose_called
            aclose_called = True

    decomposer = _mock_decomposer()
    decomposer.answer = lambda prompt: _RaisingGen()
    pipeline, _, _ = _make_pipeline(decomposer=decomposer)

    # _retry_after_timeout only catches TimeoutError; RuntimeError propagates to caller.
    with pytest.raises(RuntimeError, match="iter error"):
        _ = [e async for e in pipeline._retry_after_timeout([], "some prompt")]

    assert aclose_called, "retry_gen.aclose() must be called in finally even on exception"


@pytest.mark.asyncio
async def test_retry_after_timeout_uses_build_retry_prompt(monkeypatch) -> None:
    """_retry_after_timeout calls _build_retry_prompt(tool_pairs, prompt) and passes result to answer()."""
    from archon.ai.event_mapper import Response

    tool_pairs = [
        (ToolStarted(name="Read", input="/some/file"), ToolResult(content="file contents")),
    ]

    answer_prompts: list[str] = []

    async def _capture_answer(prompt: str) -> AsyncGenerator:
        answer_prompts.append(prompt)
        yield Response(content="done")

    with patch("archon.ai.pipeline._build_retry_prompt") as mock_build:
        mock_build.return_value = "BUILT_RETRY_PROMPT"

        decomposer = _mock_decomposer()
        decomposer.answer = _capture_answer
        pipeline, _, _ = _make_pipeline(decomposer=decomposer)

        events = [e async for e in pipeline._retry_after_timeout(tool_pairs, "original prompt")]

    mock_build.assert_called_once_with(tool_pairs, "original prompt")
    assert answer_prompts == ["BUILT_RETRY_PROMPT"], (
        f"answer() must receive the built retry prompt, got: {answer_prompts}"
    )


@pytest.mark.asyncio
async def test_retry_after_timeout_aclose_timeout_handled(monkeypatch) -> None:
    """Hanging aclose() on retry_gen is swallowed; method completes and yields ErrorEvent.

    C1-I-22: Tests the _ACLOSE_TIMEOUT_S guard in the finally block.
    """
    import asyncio as _asyncio

    class _HangingGen:
        """Generator that hangs on both __anext__ and aclose()."""

        def __aiter__(self):
            return self

        async def __anext__(self):
            await _asyncio.sleep(9999)
            raise StopAsyncIteration  # never reached

        async def aclose(self):
            await _asyncio.sleep(9999)  # simulates a hung aclose()

    decomposer = _mock_decomposer()
    decomposer.answer = lambda prompt: _HangingGen()
    decomposer.recover_session = AsyncMock()

    monkeypatch.setattr("archon.ai.pipeline._RETRY_TIMEOUT_S", 0.01)
    monkeypatch.setattr("archon.ai.pipeline._ACLOSE_TIMEOUT_S", 0.01)
    pipeline, _, _ = _make_pipeline(decomposer=decomposer)

    # Must complete without hanging; a hanging aclose() must be timed out and suppressed.
    events = [e async for e in pipeline._retry_after_timeout([], "some prompt")]

    error_events = [e for e in events if isinstance(e, ErrorEvent)]
    assert error_events, f"Expected ErrorEvent when retry gen hangs, got: {events}"


@pytest.mark.asyncio
async def test_retry_after_timeout_deadline_already_expired(monkeypatch) -> None:
    """When _RETRY_TIMEOUT_S is negative the deadline is already past on entry.

    C1-I-24: The retry_remaining <= 0 branch fires on the first iteration,
    so TimeoutError is raised before the first __anext__ call and the response
    is never consumed — only an ErrorEvent is yielded.
    """
    from archon.ai.event_mapper import Response

    decomposer = _mock_decomposer(answer_events=[Response(content="should not appear")])
    decomposer.recover_session = AsyncMock()

    # Negative timeout → deadline is immediately in the past
    monkeypatch.setattr("archon.ai.pipeline._RETRY_TIMEOUT_S", -1)
    pipeline, _, _ = _make_pipeline(decomposer=decomposer)

    events = [e async for e in pipeline._retry_after_timeout([], "some prompt")]

    error_events = [e for e in events if isinstance(e, ErrorEvent)]
    assert error_events, f"Expected ErrorEvent when deadline is already past, got: {events}"

    response_events = [e for e in events if isinstance(e, Response)]
    assert not response_events, (
        f"Response must not appear when deadline check fires before first item, got: {response_events}"
    )


@pytest.mark.asyncio
async def test_retry_after_timeout_cancellation_cleans_up(monkeypatch) -> None:
    """CancelledError propagates and retry_gen is closed when the caller cancels.

    C1-I-28: Simulates external cancellation via asyncio.wait_for.
    """
    import asyncio as _asyncio

    aclose_called = False

    class _HangingGen:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await _asyncio.sleep(9999)
            raise StopAsyncIteration  # never reached

        async def aclose(self):
            nonlocal aclose_called
            aclose_called = True

    decomposer = _mock_decomposer()
    decomposer.answer = lambda prompt: _HangingGen()

    # Use a long retry timeout so the generator hangs waiting for __anext__
    monkeypatch.setattr("archon.ai.pipeline._RETRY_TIMEOUT_S", 9999.0)
    monkeypatch.setattr("archon.ai.pipeline._ACLOSE_TIMEOUT_S", 1.0)
    pipeline, _, _ = _make_pipeline(decomposer=decomposer)

    async def _drain():
        return [e async for e in pipeline._retry_after_timeout([], "some prompt")]

    # External cancellation via wait_for should propagate as CancelledError
    with pytest.raises((_asyncio.CancelledError, TimeoutError)):
        await _asyncio.wait_for(_drain(), timeout=0.05)

    # aclose() should have been called during cleanup
    assert aclose_called, "retry_gen.aclose() must be called when CancelledError propagates"


# ──────────────────────────────────────────────────────────────────
# Task 1.2 — tool_count > 0 guard on BAM promotion path
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeout_zero_tool_count_retries_not_promotes() -> None:
    """BAM enabled + tool_count==0 after timeout → RecoveryEvent(retrying), no PromotionEvent."""
    import asyncio as _asyncio

    decomposer = _mock_decomposer()

    async def _hang(prompt: str):
        await _asyncio.sleep(999999)
        yield Response(content="unreachable")  # pragma: no cover

    decomposer.answer = _hang
    decomposer.recover_session = AsyncMock()

    with patch("archon.ai.pipeline.Classifier", return_value=_mock_classifier(intent="chat", confidence=0.95)):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            pipeline = Pipeline(has_background_agents=True)

    with patch("archon.ai.pipeline._TASK_DIRECT_TIMEOUT_S", 0.05):
        with patch("archon.ai.pipeline._RETRY_TIMEOUT_S", 0.05):
            events = [e async for e in pipeline.send("Ping")]

    promotions = [e for e in events if isinstance(e, PromotionEvent)]
    recovery = [e for e in events if isinstance(e, RecoveryEvent)]

    assert len(promotions) == 0, "tool_count==0 must NOT produce PromotionEvent"
    retrying = [r for r in recovery if r.phase == "retrying"]
    assert len(retrying) == 1, "tool_count==0 must fall through to retry path"


@pytest.mark.asyncio
async def test_timeout_nonzero_tool_count_promotes_task() -> None:
    """BAM enabled + tool_count>0 after timeout → PromotionEvent yielded with correct tool_count."""
    import asyncio as _asyncio

    decomposer = _mock_decomposer()

    async def _tool_then_hang(prompt: str):
        yield ToolStarted(name="Read", input={})
        await _asyncio.sleep(999999)
        yield Response(content="unreachable")  # pragma: no cover

    decomposer.answer = _tool_then_hang
    decomposer.recover_session = AsyncMock()
    decomposer.track_context = MagicMock()
    decomposer.flush_pending_context = MagicMock()

    with patch("archon.ai.pipeline.Classifier", return_value=_mock_classifier(intent="task", confidence=0.95)):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            pipeline = Pipeline(has_background_agents=True)

    with patch("archon.ai.pipeline._TASK_DIRECT_TIMEOUT_S", 0.05):
        events = [e async for e in pipeline.send("analyze this")]

    promotions = [e for e in events if isinstance(e, PromotionEvent)]
    assert len(promotions) == 1, "tool_count>0 must produce PromotionEvent"
    assert promotions[0].tool_count == 1


# ──────────────────────────────────────────────────────────────────
# Task 2.2: mid-stream promotion unaffected by intent guard
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mid_stream_promotion_unaffected_by_intent_guard() -> None:
    """Mid-stream promotion (threshold path) fires even with intent='chat'.

    The intent guard only applies to the timeout recovery path.
    When tool_count >= _tool_promotion_threshold during active streaming
    (not a timeout), PromotionEvent must still be emitted regardless of intent.
    The absence of RecoveryEvent(phase='timeout_detected') confirms this is
    the mid-stream path, not the timeout recovery path.
    """
    # threshold=1 → first ToolStarted triggers mid-stream promotion
    tools = [
        ToolStarted(name="Read", id="1"),
        ToolResult(content="result", id="1"),
        ToolStarted(name="Grep", id="2"),  # second tool — goes past the first ToolStarted promotion
    ]
    decomposer = _mock_decomposer(answer_events=tools)

    with patch("archon.ai.pipeline.Classifier", return_value=_mock_classifier(intent="chat", confidence=0.95)):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            pipeline = Pipeline(tool_promotion_threshold=1)

    events = [e async for e in pipeline.send("analyze this")]

    promotions = [e for e in events if isinstance(e, PromotionEvent)]
    recovery = [e for e in events if isinstance(e, RecoveryEvent)]

    assert len(promotions) == 1, "mid-stream promotion must fire regardless of chat intent"
    # Confirm this is the mid-stream path, not timeout recovery
    assert not any(r.phase == "timeout_detected" for r in recovery), (
        "timeout_detected must not appear — this is mid-stream promotion, not timeout recovery"
    )


# ──────────────────────────────────────────────────────────────────
# _read_recent_user_messages — FEAT-036 Task 2.2
# ──────────────────────────────────────────────────────────────────

from datetime import date
from pathlib import Path
from unittest.mock import patch as _patch

from archon.ai.pipeline import _read_recent_user_messages


_HISTORY_FIXTURE = """\
## 10:00:00 UTC · User 123456

write tests

> 🔧 Tool: bash
> 📤 Result: ok

## 10:05:00 UTC · Claude
this is a claude section, should not be extracted

## 10:10:00 UTC · User 123456

fix the bug
"""


def test_read_recent_messages_happy_path(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    today = date(2026, 4, 30)
    (sessions_dir / "2026-04-30.md").write_text(_HISTORY_FIXTURE, encoding="utf-8")

    result = _read_recent_user_messages(str(tmp_path), today=today)

    assert result == ["write tests", "fix the bug"]


def test_read_recent_messages_returns_last_n_when_over_limit(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    today = date(2026, 4, 30)
    lines = []
    for i in range(1, 8):
        lines.append(f"## 10:0{i}:00 UTC · User 123456\n\nmessage {i}\n")
    (sessions_dir / "2026-04-30.md").write_text("\n".join(lines), encoding="utf-8")

    result = _read_recent_user_messages(str(tmp_path), today=today, limit=5)

    assert len(result) == 5
    assert result == [f"message {i}" for i in range(3, 8)]


def test_read_recent_messages_fewer_than_limit(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    today = date(2026, 4, 30)
    content = "## 10:01:00 UTC · User 123456\n\na\n\n## 10:02:00 UTC · User 123456\n\nb\n\n## 10:03:00 UTC · User 123456\n\nc\n"
    (sessions_dir / "2026-04-30.md").write_text(content, encoding="utf-8")

    result = _read_recent_user_messages(str(tmp_path), today=today, limit=5)

    assert result == ["a", "b", "c"]


def test_read_recent_messages_no_file(tmp_path):
    result = _read_recent_user_messages(str(tmp_path / "nonexistent"), today=date(2026, 4, 30))

    assert result == []


def test_read_recent_messages_io_error(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    today = date(2026, 4, 30)
    (sessions_dir / "2026-04-30.md").write_text("## 10:00:00 UTC · User 123456\n\nhello\n", encoding="utf-8")

    with _patch.object(Path, "read_text", side_effect=OSError("disk error")):
        import logging
        with _patch.object(logging.getLogger("archon"), "warning") as mock_warn:
            result = _read_recent_user_messages(str(tmp_path), today=today)

    assert result == []
    mock_warn.assert_called_once()


def test_read_recent_messages_unicode_error(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    today = date(2026, 4, 30)
    (sessions_dir / "2026-04-30.md").write_bytes(b"\xff\xfe invalid utf8")

    with _patch.object(Path, "read_text", side_effect=UnicodeDecodeError("utf-8", b"", 0, 1, "invalid")):
        import logging
        with _patch.object(logging.getLogger("archon"), "warning") as mock_warn:
            result = _read_recent_user_messages(str(tmp_path), today=today)

    assert result == []
    mock_warn.assert_called_once()


def test_read_recent_messages_truncates_long_messages(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    today = date(2026, 4, 30)
    long_msg = "x" * 300
    (sessions_dir / "2026-04-30.md").write_text(
        f"## 10:00:00 UTC · User\n{long_msg}\n", encoding="utf-8"
    )

    result = _read_recent_user_messages(str(tmp_path), today=today)

    assert len(result) == 1
    assert len(result[0]) == 200


def test_read_recent_messages_oldest_first(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    today = date(2026, 4, 30)
    content = (
        "## 10:01:00 UTC · User\nfirst\n\n"
        "## 10:02:00 UTC · User\nsecond\n\n"
        "## 10:03:00 UTC · User\nthird\n"
    )
    (sessions_dir / "2026-04-30.md").write_text(content, encoding="utf-8")

    result = _read_recent_user_messages(str(tmp_path), today=today)

    assert result == ["first", "second", "third"]


def test_read_recent_messages_multiline_takes_first_line(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    today = date(2026, 4, 30)
    content = "## 10:00:00 UTC · User\nfirst line\nsecond line\nthird line\n"
    (sessions_dir / "2026-04-30.md").write_text(content, encoding="utf-8")

    result = _read_recent_user_messages(str(tmp_path), today=today)

    assert result == ["first line"]


def test_read_recent_messages_skips_archon_and_event_lines(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    today = date(2026, 4, 30)
    content = (
        "## 10:05:00 UTC · User\n"
        "> this starts with > so skip this line\n"
        "actually this is the message\n"
        "\n"
        "## 10:06:00 UTC · User\n"
        "### heading to skip\n"
        "real content here\n"
    )
    (sessions_dir / "2026-04-30.md").write_text(content, encoding="utf-8")

    result = _read_recent_user_messages(str(tmp_path), today=today)

    assert result == ["actually this is the message", "real content here"]


# TEST-3: limit=0 must return []
def test_read_recent_messages_limit_zero_returns_empty(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    today = date(2026, 4, 30)
    content = "## 10:00:00 UTC · User\nhello\n"
    (sessions_dir / "2026-04-30.md").write_text(content, encoding="utf-8")
    result = _read_recent_user_messages(str(tmp_path), today=today, limit=0)
    assert result == []


# IMPL-2: FileNotFoundError from read_text must return [] without warning
def test_read_recent_messages_file_deleted_mid_read_no_warning(tmp_path):
    """FileNotFoundError from read_text must return [] without logging a warning."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    today = date(2026, 4, 30)
    (sessions_dir / "2026-04-30.md").write_text("## 10:00:00 UTC · User\nhello\n", encoding="utf-8")

    with _patch.object(Path, "read_text", side_effect=FileNotFoundError("deleted")):
        import logging
        with _patch.object(logging.getLogger("archon"), "warning") as mock_warn:
            result = _read_recent_user_messages(str(tmp_path), today=today)

    assert result == []
    mock_warn.assert_not_called()


# TEST-1: today=None default branch uses UTC
def test_read_recent_messages_default_today_uses_utc(tmp_path):
    """When today=None, function should use datetime.now(timezone.utc).date()."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    fixed_date = date(2026, 4, 30)
    (sessions_dir / "2026-04-30.md").write_text("## 10:00:00 UTC · User\nutc message\n", encoding="utf-8")

    from unittest.mock import MagicMock
    import archon.ai.pipeline as pipeline_module
    mock_dt = MagicMock()
    mock_dt.now.return_value.date.return_value = fixed_date
    with _patch.object(pipeline_module, "datetime", mock_dt):
        result = _read_recent_user_messages(str(tmp_path))

    assert result == ["utc message"]


# TEST-2: Empty user section with only >-lines, followed by Claude content
def test_read_recent_messages_empty_user_section_claude_content_limitation(tmp_path):
    """Known limitation: if a User section has no valid message lines, content
    from following non-User sections may be extracted. Document this behavior.
    The second User section ("real message") is always correctly extracted.
    """
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    today = date(2026, 4, 30)
    content = (
        "## 10:00:00 UTC · User\n"
        "> only tool output\n"
        "\n"
        "### 🤖 Claude started\n"
        "Claude response text\n"
        "\n"
        "## 10:05:00 UTC · User\n"
        "real message\n"
    )
    (sessions_dir / "2026-04-30.md").write_text(content, encoding="utf-8")
    result = _read_recent_user_messages(str(tmp_path), today=today)
    # Second User section always works
    assert "real message" in result


# ──────────────────────────────────────────────────────────────────
# Pipeline context injection — FEAT-036 Task 2.3
# ──────────────────────────────────────────────────────────────────


def _make_pipeline_with_history(history_dir, classifier=None, decomposer=None):
    """Build a Pipeline with history_dir set and mocked Classifier/Decomposer."""
    if classifier is None:
        classifier = _mock_classifier()
    if decomposer is None:
        decomposer = _mock_decomposer()

    with patch("archon.ai.pipeline.Classifier", return_value=classifier):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            pipeline = Pipeline(history_dir=history_dir)

    return pipeline, classifier, decomposer


async def test_pipeline_passes_recent_context_to_classifier(tmp_path) -> None:
    """Pipeline.send() reads recent messages and passes them to classify()."""
    classifier = _mock_classifier(intent="task", confidence=0.9)
    pipeline, clf, _ = _make_pipeline_with_history(str(tmp_path), classifier=classifier)

    with patch(
        "archon.ai.pipeline._read_recent_user_messages",
        return_value=["msg1", "msg2"],
    ) as mock_read:
        with patch("archon.ai.pipeline.asyncio.to_thread", new=AsyncMock(return_value=["msg1", "msg2"])):
            await _collect(pipeline, "continue")

    clf.classify.assert_called_once()
    _, kwargs = clf.classify.call_args
    assert kwargs.get("recent_context") == ["msg1", "msg2"]


async def test_pipeline_context_empty_list_passes_none(tmp_path) -> None:
    """When _read_recent_user_messages returns [], classify() receives recent_context=None."""
    classifier = _mock_classifier(intent="task", confidence=0.9)
    pipeline, clf, _ = _make_pipeline_with_history(str(tmp_path), classifier=classifier)

    with patch("archon.ai.pipeline.asyncio.to_thread", new=AsyncMock(return_value=[])):
        await _collect(pipeline, "continue")

    clf.classify.assert_called_once()
    _, kwargs = clf.classify.call_args
    assert kwargs.get("recent_context") is None


async def test_pipeline_no_history_dir_passes_none() -> None:
    """When history_dir=None, classify() receives recent_context=None without calling reader."""
    classifier = _mock_classifier(intent="task", confidence=0.9)

    with patch("archon.ai.pipeline.Classifier", return_value=classifier):
        with patch("archon.ai.pipeline.Decomposer", return_value=_mock_decomposer()):
            pipeline = Pipeline(history_dir=None)

    with patch("archon.ai.pipeline._read_recent_user_messages") as mock_read:
        await _collect(pipeline, "continue")

    mock_read.assert_not_called()
    classifier.classify.assert_called_once()
    _, kwargs = classifier.classify.call_args
    assert kwargs.get("recent_context") is None


async def test_pipeline_context_uses_to_thread(tmp_path) -> None:
    """Pipeline.send() uses asyncio.to_thread to call _read_recent_user_messages."""
    classifier = _mock_classifier(intent="task", confidence=0.9)
    pipeline, clf, _ = _make_pipeline_with_history(str(tmp_path), classifier=classifier)

    captured_calls = []

    async def mock_to_thread(fn, *args, **kwargs):
        captured_calls.append((fn, args))
        return []

    with patch("archon.ai.pipeline.asyncio.to_thread", side_effect=mock_to_thread):
        await _collect(pipeline, "continue")

    assert len(captured_calls) == 1
    fn, args = captured_calls[0]
    assert fn is _read_recent_user_messages
    assert args[0] == str(tmp_path)


async def test_pipeline_context_read_error_falls_back(tmp_path) -> None:
    """Unexpected exception from asyncio.to_thread → recent_context=None, classify still called."""
    classifier = _mock_classifier(intent="task", confidence=0.9)
    pipeline, clf, _ = _make_pipeline_with_history(str(tmp_path), classifier=classifier)

    with patch("archon.ai.pipeline.asyncio.to_thread", new=AsyncMock(side_effect=RuntimeError("boom"))):
        await _collect(pipeline, "continue")

    clf.classify.assert_called_once()
    _, kwargs = clf.classify.call_args
    assert kwargs.get("recent_context") is None


def test_pipeline_history_dir_stored() -> None:
    """Pipeline stores history_dir as _history_dir attribute."""
    with patch("archon.ai.pipeline.Classifier"):
        with patch("archon.ai.pipeline.Decomposer"):
            p = Pipeline(history_dir="/tmp/h")
    assert p._history_dir == "/tmp/h"


def test_pipeline_no_history_dir_defaults_none() -> None:
    """Pipeline._history_dir defaults to None when not provided."""
    with patch("archon.ai.pipeline.Classifier"):
        with patch("archon.ai.pipeline.Decomposer"):
            p = Pipeline()
    assert p._history_dir is None
