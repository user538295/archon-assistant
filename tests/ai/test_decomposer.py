"""Tests for Decomposer — the brain that evaluates, answers, and plans."""

import asyncio
import json
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.event_mapper import Response, ThinkingResult, ToolStarted


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _mock_session(*events, is_processing=False):
    """Build a mock ClaudeSession that yields given events from send()."""
    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.is_processing = is_processing
    session.processing_seconds = None
    session.idle_seconds = 5.0
    session.send_count = 0
    session.usage_stats = None
    session.diagnostics = {"is_alive": True}
    session.model = "claude-sonnet-4-6"
    session.is_alive = True
    session._send_calls: list[str] = []

    async def _send(prompt: str) -> AsyncGenerator:
        session._send_calls.append(prompt)
        for event in events:
            yield event

    session.send = _send
    session.activate_skill = MagicMock()
    session.inject_context = MagicMock()
    session.recent_events = MagicMock(return_value=[])
    return session


def _make_decomposer(session_events=None, orch_events=None, summary_events=None, **kwargs):
    """Build a Decomposer with mocked main, orchestration, and summary sessions.

    Returns (decomposer, main_session, orch_session, summary_session).
    """
    from archon.ai.decomposer import Decomposer

    if session_events is None:
        session_events = [Response(content="Done.")]
    if orch_events is None:
        orch_events = [Response(content='{"intent":"task","confidence":0.9}')]
    if summary_events is None:
        summary_events = [Response(content="User discussed topic X.")]

    main_session = _mock_session(*session_events)
    orch_session = _mock_session(*orch_events)
    summary_session = _mock_session(*summary_events)

    with patch(
        "archon.ai.decomposer.ClaudeSession",
        side_effect=[main_session, orch_session, summary_session],
    ):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            decomposer = Decomposer(**kwargs)

    return decomposer, main_session, orch_session, summary_session


# ──────────────────────────────────────────────────────────────────
# review() — re-evaluates low-confidence classification
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_review_returns_updated_classification() -> None:
    from archon.ai.classification import Classification

    review_json = json.dumps({"intent": "task", "confidence": 0.9, "estimated_tools": 3})
    decomposer, _, _, _ = _make_decomposer(
        orch_events=[Response(content=review_json)],
    )
    classification = Classification(intent="chat", confidence=0.5)
    result = await decomposer.review("refactor auth", classification)

    assert result.intent == "task"
    assert result.confidence == 0.9
    assert result.estimated_tools == 3


@pytest.mark.asyncio
async def test_review_graceful_fallback_on_parse_error() -> None:
    from archon.ai.classification import Classification

    decomposer, _, _, _ = _make_decomposer(
        orch_events=[Response(content="I think this is a task")],
    )
    classification = Classification(intent="chat", confidence=0.5)
    result = await decomposer.review("test", classification)

    # Should fall back to original classification values
    assert result.intent == "chat"
    assert result.confidence == 0.5
    assert result.estimated_tools == 0


@pytest.mark.asyncio
async def test_review_sends_prompt_to_orchestration_session() -> None:
    """review() must use the orchestration session, not the main session."""
    from archon.ai.classification import Classification

    review_json = json.dumps({"intent": "task", "confidence": 0.85, "estimated_tools": 2})
    decomposer, main, orch, _ = _make_decomposer(
        orch_events=[Response(content=review_json)],
    )
    classification = Classification(intent="chat", confidence=0.5)
    await decomposer.review("hello there", classification)

    # Orchestration session should have been called
    assert len(orch._send_calls) == 1
    assert "hello there" in orch._send_calls[0]
    assert "[INTERNAL:" in orch._send_calls[0]
    # Main session must NOT be touched
    assert len(main._send_calls) == 0


@pytest.mark.asyncio
async def test_review_crash_falls_back_to_original() -> None:
    """When orchestration session raises during review, fall back to original."""
    from archon.ai.classification import Classification

    decomposer, _, orch, _ = _make_decomposer()

    async def _crashing_send(prompt: str):
        raise RuntimeError("connection lost")
        yield  # noqa: E501

    orch.send = _crashing_send

    classification = Classification(intent="chat", confidence=0.4)
    result = await decomposer.review("test", classification)

    assert result.intent == "chat"
    assert result.confidence == 0.4
    assert result.estimated_tools == 0


# ── _parse_review() edge cases ─────────────────────────────────


def test_parse_review_non_dict_json() -> None:
    from archon.ai.classification import Classification

    decomposer, _, _, _ = _make_decomposer()
    fallback = Classification(intent="task", confidence=0.5)
    result = decomposer._parse_review("[1, 2, 3]", fallback)
    assert result.intent == "task"
    assert result.confidence == 0.5


def test_parse_review_invalid_intent() -> None:
    from archon.ai.classification import Classification

    decomposer, _, _, _ = _make_decomposer()
    fallback = Classification(intent="task", confidence=0.5)
    result = decomposer._parse_review('{"intent": "unknown", "confidence": 0.9}', fallback)
    assert result.intent == "task"  # falls back


def test_parse_review_out_of_range_confidence() -> None:
    from archon.ai.classification import Classification

    decomposer, _, _, _ = _make_decomposer()
    fallback = Classification(intent="task", confidence=0.5)
    result = decomposer._parse_review('{"intent": "chat", "confidence": 5.0}', fallback)
    assert result.confidence == 1.0  # clamped


def test_parse_review_negative_confidence() -> None:
    from archon.ai.classification import Classification

    decomposer, _, _, _ = _make_decomposer()
    fallback = Classification(intent="task", confidence=0.5)
    result = decomposer._parse_review('{"intent": "chat", "confidence": -1.0}', fallback)
    assert result.confidence == 0.0  # clamped


def test_parse_review_non_numeric_confidence() -> None:
    from archon.ai.classification import Classification

    decomposer, _, _, _ = _make_decomposer()
    fallback = Classification(intent="task", confidence=0.5)
    result = decomposer._parse_review('{"intent": "chat", "confidence": "high"}', fallback)
    assert result.confidence == 0.5  # falls back


def test_parse_review_non_numeric_estimated_tools() -> None:
    from archon.ai.classification import Classification

    decomposer, _, _, _ = _make_decomposer()
    fallback = Classification(intent="task", confidence=0.5)
    result = decomposer._parse_review('{"intent": "task", "confidence": 0.9, "estimated_tools": "many"}', fallback)
    assert result.estimated_tools == 0  # falls back


def test_parse_review_negative_estimated_tools_clamped_to_zero() -> None:
    """Negative estimated_tools from LLM is clamped to 0 — not propagated downstream."""
    from archon.ai.classification import Classification

    decomposer, _, _, _ = _make_decomposer()
    fallback = Classification(intent="task", confidence=0.5)
    result = decomposer._parse_review(
        '{"intent": "task", "confidence": 0.9, "estimated_tools": -5}', fallback
    )
    assert result.estimated_tools == 0


# ──────────────────────────────────────────────────────────────────
# answer() — streams events from session
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_answer_streams_events() -> None:
    decomposer, _, _, _ = _make_decomposer(
        session_events=[
            ThinkingResult(content="thinking..."),
            Response(content="Here is the answer"),
        ],
    )
    events = [e async for e in decomposer.answer("hello")]

    assert len(events) == 2
    assert isinstance(events[0], ThinkingResult)
    assert isinstance(events[1], Response)


@pytest.mark.asyncio
async def test_answer_sends_prompt_to_main_session() -> None:
    """answer() must use the main session, not the orchestration session."""
    decomposer, main, orch, _ = _make_decomposer()
    _ = [e async for e in decomposer.answer("what is 2+2")]

    assert len(main._send_calls) == 1
    assert "what is 2+2" in main._send_calls[0]
    # Orchestration session must NOT be touched
    assert len(orch._send_calls) == 0


# ──────────────────────────────────────────────────────────────────
# route_task() — returns small or large TaskOutput
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_task_returns_small_scope() -> None:
    small_json = json.dumps({
        "scope": "small",
        "summary": "Fix the typo",
        "prompt": "Fix the typo in README.md line 5",
    })
    decomposer, _, _, _ = _make_decomposer(
        orch_events=[Response(content=small_json)],
    )
    result = await decomposer.route_task("fix the typo in readme")

    assert result.scope == "small"
    assert result.summary == "Fix the typo"
    assert result.prompt == "Fix the typo in README.md line 5"
    assert result.agents is None


@pytest.mark.asyncio
async def test_route_task_returns_large_scope() -> None:
    large_json = json.dumps({
        "scope": "large",
        "summary": "Refactor auth module",
        "agents": [
            {"id": "a1", "task": "Extract middleware"},
            {"id": "a2", "task": "Update imports", "depends_on": ["a1"]},
        ],
    })
    decomposer, _, _, _ = _make_decomposer(
        orch_events=[Response(content=large_json)],
    )
    result = await decomposer.route_task("refactor the auth module")

    assert result.scope == "large"
    assert result.summary == "Refactor auth module"
    assert result.prompt is None
    assert result.agents is not None
    assert len(result.agents) == 2


@pytest.mark.asyncio
async def test_route_task_graceful_fallback_on_bad_json() -> None:
    decomposer, _, _, _ = _make_decomposer(
        orch_events=[Response(content="Let me handle this directly")],
    )
    result = await decomposer.route_task("do something")

    # Fallback: treat as small task with the prompt as-is
    assert result.scope == "small"
    assert result.prompt is not None


@pytest.mark.asyncio
async def test_route_task_sends_prompt_to_orchestration_session() -> None:
    """route_task() must use the orchestration session, not the main session."""
    small_json = json.dumps({"scope": "small", "summary": "test", "prompt": "do it"})
    decomposer, main, orch, _ = _make_decomposer(
        orch_events=[Response(content=small_json)],
    )
    await decomposer.route_task("big task here")

    assert len(orch._send_calls) == 1
    assert "big task here" in orch._send_calls[0]
    # Main session must NOT be touched
    assert len(main._send_calls) == 0


@pytest.mark.asyncio
async def test_route_task_crash_falls_back_to_small() -> None:
    """When orchestration session raises during route_task, fall back to small scope."""
    decomposer, _, orch, _ = _make_decomposer()

    async def _crashing_send(prompt: str):
        raise RuntimeError("connection lost")
        yield  # noqa: E501

    orch.send = _crashing_send

    result = await decomposer.route_task("build something big")

    assert result.scope == "small"
    assert result.prompt == "build something big"


@pytest.mark.asyncio
async def test_route_task_sends_internal_tag() -> None:
    """route_task sends the INTERNAL orchestration tag via orchestration session."""
    small_json = json.dumps({"scope": "small", "summary": "test", "prompt": "do it"})
    decomposer, _, orch, _ = _make_decomposer(
        orch_events=[Response(content=small_json)],
    )
    await decomposer.route_task("do something")

    assert len(orch._send_calls) == 1
    assert "[INTERNAL:" in orch._send_calls[0]


# ── _parse_task_output() edge cases ────────────────────────────


def test_parse_task_output_large_empty_agents() -> None:
    decomposer, _, _, _ = _make_decomposer()
    result = decomposer._parse_task_output('{"scope": "large", "summary": "X", "agents": []}', "prompt")
    assert result.scope == "small"  # falls back


def test_parse_task_output_large_agents_missing_id() -> None:
    decomposer, _, _, _ = _make_decomposer()
    result = decomposer._parse_task_output(
        '{"scope": "large", "summary": "X", "agents": [{"task": "do it"}]}',
        "prompt",
    )
    assert result.scope == "small"  # agent without id is invalid


def test_parse_task_output_large_agents_missing_task() -> None:
    decomposer, _, _, _ = _make_decomposer()
    result = decomposer._parse_task_output(
        '{"scope": "large", "summary": "X", "agents": [{"id": "a1"}]}',
        "prompt",
    )
    assert result.scope == "small"  # agent without task is invalid


def test_parse_task_output_unknown_scope() -> None:
    decomposer, _, _, _ = _make_decomposer()
    result = decomposer._parse_task_output(
        '{"scope": "medium", "summary": "X"}',
        "original prompt",
    )
    assert result.scope == "small"
    assert result.prompt == "original prompt"


def test_parse_task_output_non_dict_json() -> None:
    decomposer, _, _, _ = _make_decomposer()
    result = decomposer._parse_task_output("[1, 2, 3]", "prompt")
    assert result.scope == "small"


# ──────────────────────────────────────────────────────────────────
# Duck-typing surface delegates to session
# ──────────────────────────────────────────────────────────────────


def test_is_processing_delegates() -> None:
    decomposer, main, _, _ = _make_decomposer()
    main.is_processing = True
    assert decomposer.is_processing is True


def test_model_delegates() -> None:
    decomposer, main, _, _ = _make_decomposer()
    main.model = "claude-sonnet-4-6"
    assert decomposer.model == "claude-sonnet-4-6"


def test_diagnostics_delegates() -> None:
    decomposer, main, _, _ = _make_decomposer()
    main.diagnostics = {"is_alive": True, "send_count": 5}
    assert decomposer.diagnostics == {"is_alive": True, "send_count": 5}


def test_usage_stats_delegates() -> None:
    decomposer, main, orch, summary = _make_decomposer()
    orch.usage_stats = None
    summary.usage_stats = None
    main.usage_stats = {"total_cost_usd": 0.05}
    stats = decomposer.usage_stats
    assert stats is not None
    assert stats["total_cost_usd"] == 0.05  # core field preserved


def test_usage_stats_includes_sessions_key() -> None:
    decomposer, main, orch, summary = _make_decomposer()
    main.usage_stats = {"total_cost_usd": 0.05}
    orch.usage_stats = None
    summary.usage_stats = None
    stats = decomposer.usage_stats
    assert stats is not None
    assert "sessions" in stats


def test_usage_stats_sessions_has_orchestration() -> None:
    decomposer, main, orch, summary = _make_decomposer()
    main.usage_stats = {"total_cost_usd": 0.05}
    orch.usage_stats = {"total_cost_usd": 0.01, "cumulative_cache_creation": 500}
    summary.usage_stats = None
    stats = decomposer.usage_stats
    assert stats is not None
    assert "orchestration" in stats["sessions"]
    assert stats["sessions"]["orchestration"]["cost_usd"] == 0.01
    assert stats["sessions"]["orchestration"]["cumulative_cache_creation"] == 500


def test_usage_stats_sessions_has_summary() -> None:
    decomposer, main, orch, summary = _make_decomposer()
    main.usage_stats = {"total_cost_usd": 0.05}
    orch.usage_stats = None
    summary.usage_stats = {"total_cost_usd": 0.002, "cumulative_cache_creation": 200}
    stats = decomposer.usage_stats
    assert stats is not None
    assert "summary" in stats["sessions"]
    assert stats["sessions"]["summary"]["cost_usd"] == 0.002
    assert stats["sessions"]["summary"]["cumulative_cache_creation"] == 200


def test_usage_stats_sub_session_zero_when_no_data() -> None:
    decomposer, main, orch, summary = _make_decomposer()
    main.usage_stats = {"total_cost_usd": 0.05}
    orch.usage_stats = None
    summary.usage_stats = None
    stats = decomposer.usage_stats
    assert stats is not None
    assert stats["sessions"]["orchestration"]["cost_usd"] == 0.0
    assert stats["sessions"]["orchestration"]["cumulative_cache_creation"] == 0
    assert stats["sessions"]["summary"]["cost_usd"] == 0.0
    assert stats["sessions"]["summary"]["cumulative_cache_creation"] == 0


def test_usage_stats_none_when_main_has_no_data() -> None:
    decomposer, main, _, _ = _make_decomposer()
    main.usage_stats = None
    assert decomposer.usage_stats is None


def test_usage_stats_total_cost_is_main_session_only() -> None:
    """total_cost_usd must reflect main session cost only — NOT including orch/summary.

    Contract: Pipeline.usage_stats adds sub-session costs on top. If Decomposer ever
    aggregates them here too, Pipeline will double-count. This test guards that contract.
    """
    decomposer, main, orch, summary = _make_decomposer()
    main.usage_stats = {"total_cost_usd": 0.05}
    orch.usage_stats = {"total_cost_usd": 0.01, "cumulative_cache_creation": 0}
    summary.usage_stats = {"total_cost_usd": 0.003, "cumulative_cache_creation": 0}
    stats = decomposer.usage_stats
    assert stats is not None
    # Must be exactly 0.05 — sub-session costs stay in sessions dict, not in total
    assert stats["total_cost_usd"] == 0.05


def test_activate_skill_delegates() -> None:
    decomposer, main, _, _ = _make_decomposer()
    skill = MagicMock()
    decomposer.activate_skill(skill)
    main.activate_skill.assert_called_once_with(skill)


def test_inject_context_delegates() -> None:
    decomposer, main, _, _ = _make_decomposer()
    decomposer.inject_context("some context")
    main.inject_context.assert_called_once_with("some context")


def test_is_alive_delegates() -> None:
    decomposer, main, _, _ = _make_decomposer()
    main.is_alive = True
    assert decomposer.is_alive is True


def test_send_count_delegates() -> None:
    decomposer, main, _, _ = _make_decomposer()
    main.send_count = 3
    assert decomposer.send_count == 3


def test_processing_seconds_delegates() -> None:
    decomposer, main, _, _ = _make_decomposer()
    main.processing_seconds = 12.5
    assert decomposer.processing_seconds == 12.5


def test_idle_seconds_delegates() -> None:
    decomposer, main, _, _ = _make_decomposer()
    main.idle_seconds = 3.0
    assert decomposer.idle_seconds == 3.0


def test_recent_events_delegates() -> None:
    decomposer, main, _, _ = _make_decomposer()
    main.recent_events.return_value = [(1.0, Response(content="hi"))]
    assert len(decomposer.recent_events(5)) == 1


# ──────────────────────────────────────────────────────────────────
# Lifecycle
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_starts_all_sessions() -> None:
    decomposer, main, orch, summary = _make_decomposer()
    await decomposer.start()
    main.start.assert_awaited_once()
    orch.start.assert_awaited_once()
    summary.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_stops_all_sessions() -> None:
    decomposer, main, orch, summary = _make_decomposer()
    await decomposer.stop()
    main.stop.assert_awaited_once()
    orch.stop.assert_awaited_once()
    summary.stop.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────
# _orch_session must be created with tools=[] (no filesystem access)
# ──────────────────────────────────────────────────────────────────


def test_orch_session_created_with_empty_tools() -> None:
    """_orch_session must have tools=[] so orchestration calls cannot invoke filesystem tools."""
    from archon.ai.decomposer import Decomposer

    constructor_calls: list[dict] = []

    def _capturing_session(**kwargs):
        constructor_calls.append(kwargs)
        return _mock_session(Response(content="{}"))

    with patch("archon.ai.decomposer.ClaudeSession", side_effect=_capturing_session):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            Decomposer()

    # Three sessions created: main (index 0), orch (index 1), summary (index 2)
    assert len(constructor_calls) == 3
    orch_kwargs = constructor_calls[1]
    assert orch_kwargs.get("tools") == [], (
        f"_orch_session must be created with tools=[], got: {orch_kwargs.get('tools')!r}"
    )


# ──────────────────────────────────────────────────────────────────
# Regression: orchestration calls must not pollute answer context
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_review_does_not_pollute_answer_session() -> None:
    """review() uses orchestration session; answer() uses main session.

    Regression test: previously both used the same session, so review's
    JSON-generation instructions leaked into the conversation context and
    caused answer() to return raw JSON instead of natural language.
    """
    from archon.ai.classification import Classification

    review_json = json.dumps({"intent": "chat", "confidence": 0.95})
    decomposer, main, orch, _ = _make_decomposer(
        session_events=[Response(content="Pong!")],
        orch_events=[Response(content=review_json)],
    )

    # Step 1: review (should use orch session)
    classification = Classification(intent="chat", confidence=0.5)
    review = await decomposer.review("ping", classification)
    assert review.intent == "chat"

    # Step 2: answer (should use main session)
    events = [e async for e in decomposer.answer("ping")]
    assert len(events) == 1
    assert isinstance(events[0], Response)
    assert events[0].content == "Pong!"

    # Main session received only the user prompt, no INTERNAL tag
    assert len(main._send_calls) == 1
    assert "[INTERNAL:" not in main._send_calls[0]

    # Orch session received only the review, no user prompt
    assert len(orch._send_calls) == 1
    assert "[INTERNAL:" in orch._send_calls[0]


# ──────────────────────────────────────────────────────────────────
# Context tracking — answer() turn buffer
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_answer_tracks_turn_in_buffer() -> None:
    """answer() with a Response appends (prompt, response) to _pending_turns."""
    decomposer, _, _, _ = _make_decomposer(
        session_events=[Response(content="Hello back!")],
    )
    _ = [e async for e in decomposer.answer("hi there")]
    # Allow fire-and-forget summary task to be created
    await asyncio.sleep(0)

    assert len(decomposer._pending_turns) >= 0  # may have been drained by summary
    # Check that at least the summary session was called (turn was tracked)
    # or the turn is still pending
    # The key invariant: either the turn is in _pending_turns or was sent to summary
    total = len(decomposer._pending_turns) + len(decomposer._summary_session._send_calls)
    assert total >= 1


@pytest.mark.asyncio
async def test_answer_skips_tracking_when_no_response() -> None:
    """If answer() yields no Response, nothing is tracked."""
    decomposer, _, _, _ = _make_decomposer(
        session_events=[ThinkingResult(content="thinking...")],
    )
    _ = [e async for e in decomposer.answer("hi")]
    await asyncio.sleep(0)

    assert len(decomposer._pending_turns) == 0
    assert decomposer._summary_task is None


@pytest.mark.asyncio
async def test_answer_schedules_summary_after_tracking() -> None:
    """After answer() with Response, _summary_task is created."""
    decomposer, _, _, _ = _make_decomposer(
        session_events=[Response(content="Done!")],
    )
    _ = [e async for e in decomposer.answer("do it")]

    assert decomposer._summary_task is not None


@pytest.mark.asyncio
async def test_schedule_summary_skips_when_already_running() -> None:
    """If summary task is in-flight, _schedule_summary does not replace it."""
    decomposer, _, _, _ = _make_decomposer(
        session_events=[Response(content="Done!")],
    )

    # Create a long-running fake task
    async def _slow():
        await asyncio.sleep(10)

    decomposer._summary_task = asyncio.create_task(_slow())
    original_task = decomposer._summary_task

    # Schedule should skip because task is running
    decomposer._schedule_summary()
    assert decomposer._summary_task is original_task

    # Cleanup
    decomposer._summary_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await decomposer._summary_task


# ──────────────────────────────────────────────────────────────────
# Context tracking — Haiku summarization
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_summary_updates_context_summary() -> None:
    """After _refresh_summary(), _context_summary is set to Haiku output."""
    decomposer, _, _, summary = _make_decomposer(
        summary_events=[Response(content="User asked about tests.")],
    )
    decomposer._pending_turns.append(("write tests", "I wrote 5 tests"))

    await decomposer._refresh_summary()

    assert decomposer._context_summary == "User asked about tests."


@pytest.mark.asyncio
async def test_refresh_summary_clears_summarized_turns() -> None:
    """Summarized turns are removed from _pending_turns."""
    decomposer, _, _, _ = _make_decomposer(
        summary_events=[Response(content="Summary.")],
    )
    decomposer._pending_turns.append(("q1", "a1"))
    decomposer._pending_turns.append(("q2", "a2"))

    await decomposer._refresh_summary()

    assert len(decomposer._pending_turns) == 0


@pytest.mark.asyncio
async def test_refresh_summary_preserves_turns_added_during_summarization() -> None:
    """Turns appended during Haiku survive the drain."""
    decomposer, _, _, summary_session = _make_decomposer(
        summary_events=[Response(content="Summary.")],
    )
    decomposer._pending_turns.append(("q1", "a1"))

    # Monkey-patch the summary session to append a turn mid-flight
    original_send = summary_session.send

    async def _send_and_append(prompt):
        decomposer._pending_turns.append(("q2", "a2"))
        async for event in original_send(prompt):
            yield event

    summary_session.send = _send_and_append

    await decomposer._refresh_summary()

    # q1 was in snapshot → drained. q2 arrived during → preserved.
    assert len(decomposer._pending_turns) == 1
    assert decomposer._pending_turns[0] == ("q2", "a2")


@pytest.mark.asyncio
async def test_refresh_summary_self_schedules_when_pending_turns_remain() -> None:
    """If new turns arrived during Haiku, another task is created."""
    decomposer, _, _, summary_session = _make_decomposer(
        summary_events=[Response(content="Summary.")],
    )
    decomposer._pending_turns.append(("q1", "a1"))

    original_send = summary_session.send

    async def _send_and_append(prompt):
        decomposer._pending_turns.append(("q2", "a2"))
        async for event in original_send(prompt):
            yield event

    summary_session.send = _send_and_append

    await decomposer._refresh_summary()

    # A new task should be scheduled for the remaining turn
    assert decomposer._summary_task is not None
    assert not decomposer._summary_task.done()

    # Cleanup
    decomposer._summary_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await decomposer._summary_task


@pytest.mark.asyncio
async def test_refresh_summary_does_not_self_schedule_on_failure() -> None:
    """On Haiku error, no self-scheduling (prevents infinite retry loops)."""
    decomposer, _, _, summary_session = _make_decomposer()
    decomposer._pending_turns.append(("q1", "a1"))

    async def _failing_send(prompt):
        raise RuntimeError("Haiku down")
        yield  # noqa: E501 — make it an async generator

    summary_session.send = _failing_send

    await decomposer._refresh_summary()

    # No self-scheduling on failure
    assert decomposer._summary_task is None
    # Turns preserved in buffer
    assert len(decomposer._pending_turns) == 1


@pytest.mark.asyncio
async def test_refresh_summary_incorporates_previous_summary() -> None:
    """Prompt includes previous _context_summary for incremental summarization."""
    decomposer, _, _, summary_session = _make_decomposer(
        summary_events=[Response(content="Updated summary.")],
    )
    decomposer._context_summary = "Previous context about auth."
    decomposer._pending_turns.append(("add tests", "Tests added"))

    await decomposer._refresh_summary()

    # The summary session should have received the previous summary
    assert len(summary_session._send_calls) == 1
    assert "Previous summary:" in summary_session._send_calls[0]
    assert "Previous context about auth." in summary_session._send_calls[0]


@pytest.mark.asyncio
async def test_refresh_summary_failure_keeps_turns() -> None:
    """On Haiku error, turns stay in buffer for next attempt."""
    decomposer, _, _, summary_session = _make_decomposer()
    decomposer._pending_turns.append(("q1", "a1"))
    decomposer._pending_turns.append(("q2", "a2"))

    async def _failing_send(prompt):
        raise RuntimeError("Haiku error")
        yield  # noqa: E501

    summary_session.send = _failing_send

    await decomposer._refresh_summary()

    assert len(decomposer._pending_turns) == 2


# ──────────────────────────────────────────────────────────────────
# Context tracking — _build_orch_context
# ──────────────────────────────────────────────────────────────────


def test_build_orch_context_returns_summary() -> None:
    """Returns formatted _context_summary when present."""
    decomposer, _, _, _ = _make_decomposer()
    decomposer._context_summary = "User discussed auth refactoring."

    result = decomposer._build_orch_context()

    assert "User discussed auth refactoring." in result
    assert "[Main-session context" in result


def test_build_orch_context_empty_when_no_summary() -> None:
    """Returns empty string when _context_summary is empty."""
    decomposer, _, _, _ = _make_decomposer()
    decomposer._context_summary = ""

    assert decomposer._build_orch_context() == ""


# ──────────────────────────────────────────────────────────────────
# Context tracking — review/route_task integration
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_review_awaits_pending_summary() -> None:
    """If summary task is running, review() awaits it before proceeding."""
    from archon.ai.classification import Classification

    review_json = json.dumps({"intent": "task", "confidence": 0.9})
    decomposer, _, _, _ = _make_decomposer(
        orch_events=[Response(content=review_json)],
    )

    # Create a fast-completing summary task that sets context
    async def _fast_summary():
        decomposer._context_summary = "User worked on auth."

    decomposer._summary_task = asyncio.create_task(_fast_summary())

    classification = Classification(intent="chat", confidence=0.5)
    await decomposer.review("test", classification)

    # After review, the summary task should be done
    assert decomposer._summary_task.done()


@pytest.mark.asyncio
async def test_review_proceeds_after_summary_timeout() -> None:
    """If summary task exceeds timeout, review() proceeds without waiting."""
    from archon.ai.classification import Classification
    from archon.ai.decomposer import _SUMMARY_WAIT_TIMEOUT

    review_json = json.dumps({"intent": "task", "confidence": 0.9})
    decomposer, _, orch, _ = _make_decomposer(
        orch_events=[Response(content=review_json)],
    )

    # Create a slow summary task that outlasts the timeout
    async def _slow_summary():
        await asyncio.sleep(_SUMMARY_WAIT_TIMEOUT + 5)

    decomposer._summary_task = asyncio.create_task(_slow_summary())

    classification = Classification(intent="chat", confidence=0.5)
    result = await decomposer.review("test", classification)

    # review() should have completed despite the slow summary
    assert result.intent == "task"
    assert len(orch._send_calls) == 1
    # The summary task should still be running (shield prevented cancellation)
    assert not decomposer._summary_task.done()

    # Cleanup
    decomposer._summary_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await decomposer._summary_task


@pytest.mark.asyncio
async def test_review_includes_conversation_context() -> None:
    """answer() → review() → orch prompt contains context."""
    from archon.ai.classification import Classification

    review_json = json.dumps({"intent": "task", "confidence": 0.9})
    decomposer, _, orch, _ = _make_decomposer(
        session_events=[Response(content="Auth refactored!")],
        orch_events=[Response(content=review_json)],
        summary_events=[Response(content="User asked to refactor auth.")],
    )

    # First, answer to build context
    _ = [e async for e in decomposer.answer("refactor auth")]
    # Wait for summary task to complete
    if decomposer._summary_task:
        await decomposer._summary_task

    # Now review should include context
    classification = Classification(intent="chat", confidence=0.5)
    await decomposer.review("now add tests", classification)

    assert len(orch._send_calls) == 1
    assert "[Main-session context" in orch._send_calls[0]
    assert "User asked to refactor auth." in orch._send_calls[0]


@pytest.mark.asyncio
async def test_review_no_context_on_first_message() -> None:
    """First review() has no context block."""
    from archon.ai.classification import Classification

    review_json = json.dumps({"intent": "task", "confidence": 0.9})
    decomposer, _, orch, _ = _make_decomposer(
        orch_events=[Response(content=review_json)],
    )

    classification = Classification(intent="chat", confidence=0.5)
    await decomposer.review("hello", classification)

    assert len(orch._send_calls) == 1
    assert "[Main-session context" not in orch._send_calls[0]


@pytest.mark.asyncio
async def test_route_task_awaits_and_includes_context() -> None:
    """route_task() awaits pending summary and includes context."""
    small_json = json.dumps({"scope": "small", "summary": "test", "prompt": "do it"})
    decomposer, _, orch, _ = _make_decomposer(
        session_events=[Response(content="Done!")],
        orch_events=[Response(content=small_json)],
        summary_events=[Response(content="User fixed a bug.")],
    )

    # Build context via answer
    _ = [e async for e in decomposer.answer("fix the bug")]
    if decomposer._summary_task:
        await decomposer._summary_task

    await decomposer.route_task("now deploy it")

    assert len(orch._send_calls) == 1
    assert "[Main-session context" in orch._send_calls[0]
    assert "User fixed a bug." in orch._send_calls[0]


# ──────────────────────────────────────────────────────────────────
# Context tracking — track_context() public API
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_track_context_appends_and_schedules_summary() -> None:
    """External callers can inject context entries via track_context()."""
    decomposer, _, _, _ = _make_decomposer()

    decomposer.track_context("user request", "[Task escalated to background agent]")

    assert len(decomposer._pending_turns) >= 0  # may be drained already
    # Summary should be scheduled
    assert decomposer._summary_task is not None

    # Cleanup
    if not decomposer._summary_task.done():
        decomposer._summary_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await decomposer._summary_task


# ──────────────────────────────────────────────────────────────────
# Context tracking — orch session reset
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_orch_reset_restarts_session_after_threshold() -> None:
    """After 20 orch calls, session is restarted."""
    from archon.ai.decomposer import _ORCH_RESET_THRESHOLD

    review_json = json.dumps({"intent": "task", "confidence": 0.9})
    decomposer, _, orch, _ = _make_decomposer(
        orch_events=[Response(content=review_json)],
    )

    # Simulate threshold - 1 calls already made
    decomposer._orch_call_count = _ORCH_RESET_THRESHOLD - 1

    await decomposer._reset_orch_if_needed()

    # Should have restarted
    orch.stop.assert_awaited_once()
    orch.start.assert_awaited_once()
    assert decomposer._orch_call_count == 0


@pytest.mark.asyncio
async def test_orch_reset_preserves_context_summary() -> None:
    """_context_summary is unchanged after orch reset."""
    from archon.ai.decomposer import _ORCH_RESET_THRESHOLD

    decomposer, _, _, _ = _make_decomposer()
    decomposer._context_summary = "Important context about auth."
    decomposer._orch_call_count = _ORCH_RESET_THRESHOLD - 1

    await decomposer._reset_orch_if_needed()

    assert decomposer._context_summary == "Important context about auth."


@pytest.mark.asyncio
async def test_summary_session_resets_independently() -> None:
    """Summary session resets every _SUMMARY_RESET_THRESHOLD calls."""
    from archon.ai.decomposer import _SUMMARY_RESET_THRESHOLD

    decomposer, _, _, summary = _make_decomposer(
        summary_events=[Response(content="Summary.")],
    )
    decomposer._summary_call_count = _SUMMARY_RESET_THRESHOLD - 1
    decomposer._pending_turns.append(("q", "a"))

    await decomposer._refresh_summary()

    # Summary session should have been restarted
    summary.stop.assert_awaited_once()
    assert summary.start.await_count >= 1
    assert decomposer._summary_call_count == 0


# ──────────────────────────────────────────────────────────────────
# US-001: Read agents.md from workspace on session start
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_injects_agents_md_when_present(tmp_path) -> None:
    """agents.md content is injected into the main session on start()."""
    agents_file = tmp_path / "agents.md"
    agents_file.write_text("# Available Agents\n- researcher: Does research")

    decomposer, main_session, _, _ = _make_decomposer(cwd=str(tmp_path))
    await decomposer.start()

    main_session.inject_context.assert_called_once()
    injected = main_session.inject_context.call_args[0][0]
    assert "researcher" in injected


@pytest.mark.asyncio
async def test_start_logs_info_when_agents_md_missing(tmp_path, caplog) -> None:
    """Missing agents.md logs info and skips injection silently."""
    import logging

    decomposer, main_session, _, _ = _make_decomposer(cwd=str(tmp_path))

    with caplog.at_level(logging.INFO, logger="archon"):
        await decomposer.start()

    main_session.inject_context.assert_not_called()
    assert any("agents.md" in r.message for r in caplog.records)
    assert all(r.levelno <= logging.INFO for r in caplog.records if "agents.md" in r.message)


@pytest.mark.asyncio
async def test_start_skips_injection_when_agents_md_empty(tmp_path) -> None:
    """Empty agents.md is not injected."""
    agents_file = tmp_path / "agents.md"
    agents_file.write_text("")

    decomposer, main_session, _, _ = _make_decomposer(cwd=str(tmp_path))
    await decomposer.start()

    main_session.inject_context.assert_not_called()


@pytest.mark.asyncio
async def test_start_logs_warning_on_read_error(tmp_path, caplog) -> None:
    """File read errors are caught and logged at warning level; session starts normally."""
    import logging

    agents_file = tmp_path / "agents.md"
    agents_file.write_text("content")
    agents_file.chmod(0o000)  # make unreadable

    decomposer, main_session, _, _ = _make_decomposer(cwd=str(tmp_path))

    try:
        with caplog.at_level(logging.WARNING, logger="archon"):
            await decomposer.start()

        # Session should still start (no crash)
        main_session.start.assert_awaited_once()
        main_session.inject_context.assert_not_called()
        assert any(
            r.levelno == logging.WARNING and "agents.md" in r.message
            for r in caplog.records
        )
    finally:
        agents_file.chmod(0o644)


@pytest.mark.asyncio
async def test_start_does_not_inject_when_no_cwd() -> None:
    """No cwd → no agents.md read attempted, no injection."""
    decomposer, main_session, _, _ = _make_decomposer(cwd=None)
    await decomposer.start()

    main_session.inject_context.assert_not_called()


@pytest.mark.asyncio
async def test_agents_md_read_on_every_start(tmp_path) -> None:
    """agents.md is re-read on every start(), not cached across sessions."""
    agents_file = tmp_path / "agents.md"
    agents_file.write_text("# First content")

    decomposer, main_session, _, _ = _make_decomposer(cwd=str(tmp_path))
    await decomposer.start()
    await decomposer.stop()

    # Simulate a new session creation (new Decomposer with updated file)
    agents_file.write_text("# Updated content")

    decomposer2, main_session2, _, _ = _make_decomposer(cwd=str(tmp_path))
    await decomposer2.start()
    await decomposer2.stop()

    injected2 = main_session2.inject_context.call_args[0][0]
    assert "Updated content" in injected2


# ──────────────────────────────────────────────────────────────────
# US-002: Inject agents.md into Decomposer context before history
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inject_workspace_agents_uses_header(tmp_path) -> None:
    """agents.md content is labeled with '# Workspace Agents' header."""
    agents_file = tmp_path / "agents.md"
    agents_file.write_text("- researcher: Does research")

    decomposer, main_session, _, _ = _make_decomposer(cwd=str(tmp_path))
    await decomposer.start()

    main_session.inject_context.assert_called_once()
    injected = main_session.inject_context.call_args[0][0]
    assert injected.startswith("# Workspace Agents\n\n")
    assert "researcher" in injected


@pytest.mark.asyncio
async def test_inject_workspace_agents_only_main_session(tmp_path) -> None:
    """Injection goes to main session only — orch and summary sessions are untouched."""
    agents_file = tmp_path / "agents.md"
    agents_file.write_text("- researcher: Does research")

    decomposer, main_session, orch_session, summary_session = _make_decomposer(
        cwd=str(tmp_path)
    )
    await decomposer.start()

    main_session.inject_context.assert_called_once()
    orch_session.inject_context.assert_not_called()
    summary_session.inject_context.assert_not_called()


@pytest.mark.asyncio
async def test_inject_workspace_agents_on_session_resume(tmp_path) -> None:
    """Fresh read and injection happen on every start() — including after resume."""
    agents_file = tmp_path / "agents.md"
    agents_file.write_text("- harbor: Manages background agents")

    decomposer, main_session, _, _ = _make_decomposer(cwd=str(tmp_path))
    await decomposer.start()

    # Simulate inactivity-timeout resume: file changes, start() called again on same instance
    agents_file.write_text("- harbor: Updated capabilities")
    await decomposer.start()

    calls = main_session.inject_context.call_args_list
    assert len(calls) == 2
    assert "harbor" in calls[0][0][0]
    assert "Updated capabilities" in calls[1][0][0]


# ──────────────────────────────────────────────────────────────────
# US-003: Unit tests for agents.md loading and injection
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inject_workspace_agents_no_instance_caching(tmp_path) -> None:
    """start() re-reads agents.md each time; no instance-level cache is used."""
    agents_file = tmp_path / "agents.md"
    agents_file.write_text("# First content")

    decomposer, main_session, _, _ = _make_decomposer(cwd=str(tmp_path))
    await decomposer.start()

    agents_file.write_text("# Second content")
    await decomposer.start()

    calls = main_session.inject_context.call_args_list
    assert len(calls) == 2
    assert "First content" in calls[0][0][0]
    assert "Second content" in calls[1][0][0]


@pytest.mark.asyncio
async def test_agents_md_injected_before_first_answer(tmp_path) -> None:
    """agents.md injection happens during start(), before any answer() history."""
    agents_file = tmp_path / "agents.md"
    agents_file.write_text("- researcher: Does research")

    call_order: list[str] = []
    decomposer, main_session, _, _ = _make_decomposer(
        cwd=str(tmp_path),
        session_events=[Response(content="Done.")],
    )
    main_session.inject_context.side_effect = lambda *a, **kw: call_order.append("inject")

    await decomposer.start()
    call_order.append("start_done")
    async for _ in decomposer.answer("hello"):
        pass
    call_order.append("answer_done")

    assert call_order[0] == "inject"
    assert call_order.index("start_done") < call_order.index("answer_done")


@pytest.mark.asyncio
async def test_inject_workspace_agents_reads_via_thread(tmp_path) -> None:
    """_inject_workspace_agents() dispatches the blocking read to asyncio.to_thread."""
    agents_file = tmp_path / "agents.md"
    agents_file.write_text("- researcher: Does research")

    decomposer, main_session, _, _ = _make_decomposer(cwd=str(tmp_path))

    thread_calls: list[str] = []

    original_to_thread = asyncio.to_thread

    async def _spy_to_thread(func, *args, **kwargs):  # type: ignore[override]
        thread_calls.append(getattr(func, "__name__", str(func)))
        return await original_to_thread(func, *args, **kwargs)

    with patch("archon.ai.decomposer.asyncio.to_thread", side_effect=_spy_to_thread):
        await decomposer.start()

    assert any("read_text" in call for call in thread_calls), (
        f"Expected asyncio.to_thread to be called with read_text, got: {thread_calls}"
    )
    main_session.inject_context.assert_called_once()


# ──────────────────────────────────────────────────────────────────
# route_task file path extraction from session events
# ──────────────────────────────────────────────────────────────────


def _make_decomposer_with_events(tmp_path, recent_events_return, orch_response=None):
    """Helper: build a Decomposer whose _session.recent_events() returns given events."""
    from archon.ai.decomposer import Decomposer

    route_json = orch_response or '{"scope":"small","summary":"Test","prompt":"do it"}'
    orch_session = _mock_session(Response(content=route_json))
    main_session = _mock_session(Response(content="ok"))
    main_session.recent_events = MagicMock(return_value=recent_events_return)
    summary_session = _mock_session(Response(content="summary"))

    call_count = 0

    def _make_session(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return main_session
        if call_count == 2:
            return orch_session
        return summary_session

    return Decomposer, orch_session, _make_session


class TestRouteTaskFilePathExtraction:
    async def test_route_task_includes_recent_file_paths(self, tmp_path) -> None:
        """route_task() includes file paths from _session.recent_events() in instruction."""
        from archon.ai.event_mapper import ToolStarted

        Decomposer, orch_session, _make_session = _make_decomposer_with_events(
            tmp_path,
            [(1.0, ToolStarted(name="Read", input="/abs/path/to/config.py"))],
        )

        with patch("archon.ai.decomposer.ClaudeSession", side_effect=_make_session):
            d = Decomposer(cwd=str(tmp_path))
            await d.start()
            await d.route_task("do something")
            await d.stop()

        assert len(orch_session._send_calls) == 1
        assert "/abs/path/to/config.py" in orch_session._send_calls[0]

    async def test_route_task_no_paths_block_when_no_tool_events(self, tmp_path) -> None:
        """route_task() works normally when session has no recent tool events."""
        Decomposer, orch_session, _make_session = _make_decomposer_with_events(tmp_path, [])

        with patch("archon.ai.decomposer.ClaudeSession", side_effect=_make_session):
            d = Decomposer(cwd=str(tmp_path))
            await d.start()
            result = await d.route_task("do something")
            await d.stop()

        assert result.scope in ("small", "large")
        assert "[Files accessed" not in orch_session._send_calls[0]

    async def test_extract_deduplicates_paths(self, tmp_path) -> None:
        """_extract_recent_file_paths() deduplicates repeated paths."""
        from archon.ai.event_mapper import ToolStarted

        Decomposer, orch_session, _make_session = _make_decomposer_with_events(
            tmp_path,
            [
                (1.0, ToolStarted(name="Read", input="/project/config.py")),
                (2.0, ToolStarted(name="Edit", input="/project/config.py")),
                (3.0, ToolStarted(name="Read", input="/project/other.py")),
            ],
        )

        with patch("archon.ai.decomposer.ClaudeSession", side_effect=_make_session):
            d = Decomposer(cwd=str(tmp_path))
            await d.start()
            await d.route_task("do something")
            await d.stop()

        instruction = orch_session._send_calls[0]
        assert instruction.count("/project/config.py") == 1
        assert "/project/other.py" in instruction

    async def test_extract_handles_bare_path_string(self, tmp_path) -> None:
        """_extract_recent_file_paths() handles bare path strings (not JSON dicts)."""
        from archon.ai.event_mapper import ToolStarted

        Decomposer, orch_session, _make_session = _make_decomposer_with_events(
            tmp_path,
            [(1.0, ToolStarted(name="Read", input="/some/file.py"))],
        )

        with patch("archon.ai.decomposer.ClaudeSession", side_effect=_make_session):
            d = Decomposer(cwd=str(tmp_path))
            await d.start()
            await d.route_task("do something")
            await d.stop()

        assert "/some/file.py" in orch_session._send_calls[0]

    async def test_extract_ignores_regex_patterns(self, tmp_path) -> None:
        """_extract_recent_file_paths() only extracts file_path/path, not pattern (regex)."""
        from archon.ai.event_mapper import ToolStarted

        Decomposer, orch_session, _make_session = _make_decomposer_with_events(
            tmp_path,
            [(1.0, ToolStarted(name="Grep", input='{"pattern": "def foo", "path": "/src/"}'))],
        )

        with patch("archon.ai.decomposer.ClaudeSession", side_effect=_make_session):
            d = Decomposer(cwd=str(tmp_path))
            await d.start()
            await d.route_task("do something")
            await d.stop()

        instruction = orch_session._send_calls[0]
        assert "/src/" in instruction
        assert "def foo" not in instruction

    async def test_extract_caps_at_15_paths(self, tmp_path) -> None:
        """_extract_recent_file_paths() returns at most 15 unique paths."""
        from archon.ai.event_mapper import ToolStarted

        events = [
            (float(i), ToolStarted(name="Read", input=f"/project/file{i}.py"))
            for i in range(20)
        ]
        Decomposer, orch_session, _make_session = _make_decomposer_with_events(
            tmp_path, events
        )

        with patch("archon.ai.decomposer.ClaudeSession", side_effect=_make_session):
            d = Decomposer(cwd=str(tmp_path))
            await d.start()
            await d.route_task("do something")
            await d.stop()

        instruction = orch_session._send_calls[0]
        # Extract the files block and count paths
        start = instruction.find("[Files accessed")
        end = instruction.find("[End files]")
        assert start != -1 and end != -1
        files_block = instruction[start:end]
        path_count = files_block.count("/project/file")
        assert path_count == 15

    async def test_extract_ignores_bash_commands(self, tmp_path) -> None:
        """_extract_recent_file_paths() must skip ToolStarted events where name='Bash'."""
        from archon.ai.event_mapper import ToolStarted

        Decomposer, orch_session, _make_session = _make_decomposer_with_events(
            tmp_path,
            [(1.0, ToolStarted(name="Bash", input="cd /project && make build"))],
        )

        with patch("archon.ai.decomposer.ClaudeSession", side_effect=_make_session):
            d = Decomposer(cwd=str(tmp_path))
            await d.start()
            await d.route_task("do something")
            await d.stop()

        instruction = orch_session._send_calls[0]
        assert "cd /project && make build" not in instruction
        assert "[Files accessed" not in instruction

    async def test_extract_returns_most_recent_paths(self, tmp_path) -> None:
        """_extract_recent_file_paths() must return the 15 most recent paths, not oldest."""
        from archon.ai.event_mapper import ToolStarted

        events = [
            (float(i), ToolStarted(name="Read", input=f"/project/file{i}.py"))
            for i in range(20)
        ]
        Decomposer, orch_session, _make_session = _make_decomposer_with_events(
            tmp_path, events
        )

        with patch("archon.ai.decomposer.ClaudeSession", side_effect=_make_session):
            d = Decomposer(cwd=str(tmp_path))
            await d.start()
            await d.route_task("do something")
            await d.stop()

        instruction = orch_session._send_calls[0]
        # Most recent is file19.py; oldest is file0.py
        assert "/project/file19.py" in instruction
        assert "/project/file0.py" not in instruction

    def test_context_summary_property_returns_summary(self) -> None:
        """context_summary property delegates to _context_summary."""
        from archon.ai.decomposer import Decomposer
        from archon.ai.event_mapper import Response

        main_session = _mock_session(Response(content="ok"))
        orch_session = _mock_session(Response(content="{}"))
        summary_session = _mock_session(Response(content="summary"))

        call_count = 0

        def _make_session(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return main_session
            if call_count == 2:
                return orch_session
            return summary_session

        with patch("archon.ai.decomposer.ClaudeSession", side_effect=_make_session):
            d = Decomposer()
            d._context_summary = "Previous conversation about config module"
            assert d.context_summary == "Previous conversation about config module"
