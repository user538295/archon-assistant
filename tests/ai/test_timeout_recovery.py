"""Tests for timeout recovery — session recovery + background agent promotion."""

import asyncio
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.classification import Classification
from archon.ai.classifier import ClassifierResult
from archon.ai.decomposer import TaskOutput
from archon.ai.event_mapper import (
    ErrorEvent,
    Event,
    PromotionEvent,
    RecoveryEvent,
    Response,
    ToolResult,
    ToolStarted,
)
from archon.ai.pipeline import (
    Pipeline,
    _RECOVERY_TIMEOUT_S,
    _RETRY_TIMEOUT_S,
    _TASK_DIRECT_TIMEOUT_S,
    _build_retry_prompt,
)


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _mock_classifier(intent="chat", confidence=0.95):
    """Build a mock Classifier that returns a high-confidence chat result.

    Using chat+high confidence routes directly to _task_direct_monitored
    (skipping orch routing), which is what we want for testing timeouts.
    """
    classifier = MagicMock()
    classifier.start = AsyncMock()
    classifier.stop = AsyncMock()
    classifier.model = "claude-haiku-4-5-20251001"
    classifier.usage_stats = None
    classifier.classify = AsyncMock(return_value=ClassifierResult(
        classification=Classification(intent=intent, confidence=confidence),
        raw_response='{"intent": "chat", "confidence": 0.95}',
        duration_s=0.1,
    ))
    return classifier


def _mock_decomposer(
    answer_events=None,
    hang_forever=False,
    hang_then_events=None,
    model="claude-sonnet-4-6",
):
    """Build a mock Decomposer.

    Args:
        answer_events: Events to yield from answer(). Default: [Response("Done.")].
        hang_forever: If True, answer() hangs indefinitely (triggers timeout).
        hang_then_events: List of [first_call_events, second_call_events].
            First call hangs, second call yields events.
    """
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
    decomposer.recover_session = AsyncMock()
    decomposer.track_context = MagicMock()
    decomposer.flush_pending_context = MagicMock()
    decomposer.activate_skill = MagicMock()
    decomposer.inject_context = MagicMock()
    decomposer.recent_events = MagicMock(return_value=[])
    decomposer.context_summary = ""
    decomposer.reminder = None

    call_count = 0

    if hang_then_events is not None:
        async def _answer(prompt: str) -> AsyncGenerator:
            nonlocal call_count
            idx = call_count
            call_count += 1
            if idx == 0:
                # First call: hang forever
                await asyncio.sleep(999999)
                yield Response(content="unreachable")  # pragma: no cover
            else:
                # Subsequent calls: yield events
                events = hang_then_events[min(idx, len(hang_then_events)) - 1]
                for event in events:
                    yield event
        decomposer.answer = _answer
    elif hang_forever:
        async def _answer_hang(prompt: str) -> AsyncGenerator:
            await asyncio.sleep(999999)
            yield Response(content="unreachable")  # pragma: no cover
        decomposer.answer = _answer_hang
    else:
        if answer_events is None:
            answer_events = [Response(content="Done.")]

        async def _answer_normal(prompt: str) -> AsyncGenerator:
            for event in answer_events:
                yield event
        decomposer.answer = _answer_normal

    decomposer.route_task = AsyncMock(return_value=TaskOutput(
        scope="small", summary="Quick task", prompt="Do the thing",
    ))

    return decomposer


def _make_pipeline(classifier=None, decomposer=None, has_background_agents=False):
    """Build a Pipeline with mocked Classifier and Decomposer."""
    if classifier is None:
        classifier = _mock_classifier()
    if decomposer is None:
        decomposer = _mock_decomposer()

    with patch("archon.ai.pipeline.Classifier", return_value=classifier):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            pipeline = Pipeline(has_background_agents=has_background_agents)

    return pipeline, classifier, decomposer


async def _collect(pipeline, prompt="test"):
    """Collect all events from pipeline.send()."""
    return [e async for e in pipeline.send(prompt)]


# ──────────────────────────────────────────────────────────────────
# Test 1: Timeout → recovery → promotion (BAM available)
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeout_recovery_promotes_to_background() -> None:
    """When task_direct times out and BAM is available, yield recovery events + PromotionEvent."""
    decomposer = _mock_decomposer(hang_forever=True)
    pipeline, _, _ = _make_pipeline(decomposer=decomposer, has_background_agents=True)

    with patch("archon.ai.pipeline._TASK_DIRECT_TIMEOUT_S", 0.05):
        events = await _collect(pipeline, "complex task")

    # Filter to relevant event types
    recovery_events = [e for e in events if isinstance(e, RecoveryEvent)]
    promotion_events = [e for e in events if isinstance(e, PromotionEvent)]

    assert len(recovery_events) == 3
    assert recovery_events[0].phase == "timeout_detected"
    assert recovery_events[1].phase == "session_recovered"
    assert recovery_events[2].phase == "promoting"

    assert len(promotion_events) == 1
    assert promotion_events[0].original_prompt == "complex task"

    decomposer.recover_session.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────
# Test 2: Timeout → recovery → retry (no BAM) → success
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeout_recovery_no_bam_retries_simplified() -> None:
    """When task_direct times out without BAM, retry with simplified prompt."""
    decomposer = _mock_decomposer(
        hang_then_events=[[Response(content="Retry succeeded")]],
    )
    pipeline, _, _ = _make_pipeline(decomposer=decomposer, has_background_agents=False)

    with patch("archon.ai.pipeline._TASK_DIRECT_TIMEOUT_S", 0.05):
        events = await _collect(pipeline, "complex task")

    recovery_events = [e for e in events if isinstance(e, RecoveryEvent)]
    responses = [e for e in events if isinstance(e, Response)]

    assert recovery_events[0].phase == "timeout_detected"
    assert recovery_events[1].phase == "session_recovered"
    assert recovery_events[2].phase == "retrying"

    assert len(responses) == 1
    assert responses[0].content == "Retry succeeded"

    decomposer.recover_session.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────
# Test 3: Timeout → recovery → retry also times out → ErrorEvent
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeout_recovery_no_bam_retry_also_times_out() -> None:
    """When both attempts time out (no BAM), yield ErrorEvent. recover_session called twice."""
    decomposer = _mock_decomposer(hang_forever=True)
    pipeline, _, _ = _make_pipeline(decomposer=decomposer, has_background_agents=False)

    with (
        patch("archon.ai.pipeline._TASK_DIRECT_TIMEOUT_S", 0.05),
        patch("archon.ai.pipeline._RETRY_TIMEOUT_S", 0.05),
    ):
        events = await _collect(pipeline, "complex task")

    recovery_events = [e for e in events if isinstance(e, RecoveryEvent)]
    error_events = [e for e in events if isinstance(e, ErrorEvent)]

    assert recovery_events[0].phase == "timeout_detected"
    assert recovery_events[1].phase == "session_recovered"
    assert recovery_events[2].phase == "retrying"

    assert len(error_events) == 1
    assert "timed out" in error_events[0].message.lower()

    # recover_session called twice: once before retry, once after retry timeout
    assert decomposer.recover_session.await_count == 2


# ──────────────────────────────────────────────────────────────────
# Test 4: Session recovery fails → ErrorEvent
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeout_recovery_fails_yields_error() -> None:
    """When recover_session raises, yield ErrorEvent after timeout_detected."""
    decomposer = _mock_decomposer(hang_forever=True)
    decomposer.recover_session = AsyncMock(side_effect=RuntimeError("SDK broken"))
    pipeline, _, _ = _make_pipeline(decomposer=decomposer, has_background_agents=True)

    with patch("archon.ai.pipeline._TASK_DIRECT_TIMEOUT_S", 0.05):
        events = await _collect(pipeline, "complex task")

    recovery_events = [e for e in events if isinstance(e, RecoveryEvent)]
    error_events = [e for e in events if isinstance(e, ErrorEvent)]

    assert len(recovery_events) == 1
    assert recovery_events[0].phase == "timeout_detected"

    assert len(error_events) == 1
    assert "recovery failed" in error_events[0].message.lower()


# ──────────────────────────────────────────────────────────────────
# Test 5: After recovery, pipeline lock released → next call works
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeout_recovery_no_deadlock_next_call() -> None:
    """After timeout + recovery, pipeline lock is released and next send() works."""
    call_count = 0

    async def _answer(prompt: str) -> AsyncGenerator:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            await asyncio.sleep(999999)
            yield Response(content="unreachable")  # pragma: no cover
        else:
            yield Response(content="second call works")

    decomposer = _mock_decomposer()
    decomposer.answer = _answer
    pipeline, _, _ = _make_pipeline(decomposer=decomposer, has_background_agents=True)

    # First call: times out
    with patch("archon.ai.pipeline._TASK_DIRECT_TIMEOUT_S", 0.05):
        events1 = await _collect(pipeline, "first")

    assert any(isinstance(e, RecoveryEvent) for e in events1)

    # Second call: should work (lock released)
    events2 = await _collect(pipeline, "second")
    responses = [e for e in events2 if isinstance(e, Response)]
    assert len(responses) == 1
    assert responses[0].content == "second call works"


# ──────────────────────────────────────────────────────────────────
# Test 6: Promotion includes partial results
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeout_promotion_includes_partial_results() -> None:
    """PromotionEvent.agent_prompt contains tool names from partial results."""
    call_count = 0

    async def _answer(prompt: str) -> AsyncGenerator:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield ToolStarted(name="Read", input="/path/file.py", id=1)
            yield ToolResult(content="file contents here", id=1, tool_name="Read")
            yield ToolStarted(name="Grep", input="pattern", id=2)
            # Hangs before Grep result
            await asyncio.sleep(999999)
            yield Response(content="unreachable")  # pragma: no cover
        else:
            yield Response(content="ok")

    decomposer = _mock_decomposer()
    decomposer.answer = _answer
    pipeline, _, _ = _make_pipeline(decomposer=decomposer, has_background_agents=True)

    with patch("archon.ai.pipeline._TASK_DIRECT_TIMEOUT_S", 0.05):
        events = await _collect(pipeline, "analyze code")

    promotions = [e for e in events if isinstance(e, PromotionEvent)]
    assert len(promotions) == 1
    assert "Read" in promotions[0].agent_prompt
    assert "Grep" in promotions[0].agent_prompt
    assert "file contents here" in promotions[0].agent_prompt


# ──────────────────────────────────────────────────────────────────
# Test 7: Context tracking after promotion
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeout_recovery_tracks_context() -> None:
    """After promotion, track_context + flush_pending_context are called."""
    decomposer = _mock_decomposer(hang_forever=True)
    pipeline, _, _ = _make_pipeline(decomposer=decomposer, has_background_agents=True)

    with patch("archon.ai.pipeline._TASK_DIRECT_TIMEOUT_S", 0.05):
        await _collect(pipeline, "task")

    decomposer.track_context.assert_called_once()
    args = decomposer.track_context.call_args[0]
    assert args[0] == "task"
    assert "timed out" in args[1].lower()
    decomposer.flush_pending_context.assert_called_once()


# ──────────────────────────────────────────────────────────────────
# Test 8: _build_retry_prompt includes partial results
# ──────────────────────────────────────────────────────────────────


def test_build_retry_prompt_includes_partial_results() -> None:
    """Retry prompt contains tool names, partial results, and original prompt."""
    tool_pairs = [
        (ToolStarted(name="Read", input="/file.py"), ToolResult(content="contents")),
        (ToolStarted(name="Grep", input="pattern"), None),
    ]
    result = _build_retry_prompt(tool_pairs, "find the bug")

    assert "Read" in result
    assert "Grep" in result
    assert "contents" in result
    assert "timed out before completion" in result
    assert "find the bug" in result
    assert "TIMEOUT RECOVERY" in result
    assert "3-4 tool calls" in result


# ──────────────────────────────────────────────────────────────────
# Test 9: _build_retry_prompt with empty tool pairs
# ──────────────────────────────────────────────────────────────────


def test_build_retry_prompt_empty_tool_pairs() -> None:
    """Retry prompt works with no partial results."""
    result = _build_retry_prompt([], "find the bug")
    assert "find the bug" in result
    assert "TIMEOUT RECOVERY" in result
