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
    session.flush_pending_context = MagicMock()
    session.recent_events = MagicMock(return_value=[])
    return session


def _make_decomposer(session_events=None, orch_events=None, summary_events=None, **kwargs):
    """Build a Decomposer with mocked main, orchestration, and summary sessions.

    Orch and summary sessions are pre-injected into the Decomposer's lazy slots so
    that _ensure_orch_session() / _ensure_summary_session() return them immediately
    without trying to start a real SDK subprocess.

    Returns (decomposer, main_session, orch_session, summary_session).
    """
    from archon.ai.decomposer import Decomposer

    if session_events is None:
        session_events = [Response(content="Done.")]
    if orch_events is None:
        orch_events = [Response(content='{"scope":"small","summary":"Direct handling","prompt":"original prompt"}')]
    if summary_events is None:
        summary_events = [Response(content="User discussed topic X.")]

    main_session = _mock_session(*session_events)
    orch_session = _mock_session(*orch_events)
    summary_session = _mock_session(*summary_events)

    # Only the main session is created during __init__; orch/summary are lazy.
    with patch(
        "archon.ai.decomposer.ClaudeSession",
        return_value=main_session,
    ):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            decomposer = Decomposer(**kwargs)

    # Pre-inject lazy sessions so _ensure_*_session() returns them instantly.
    decomposer._orch_session = orch_session
    decomposer._summary_session = summary_session

    return decomposer, main_session, orch_session, summary_session


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


@pytest.mark.asyncio
async def test_route_task_substitutes_history_dir_in_prompt() -> None:
    """route_task must substitute {history_dir} in the prompt with the configured value."""
    import archon.config as _config_module

    small_json = json.dumps({"scope": "small", "summary": "test", "prompt": "do it"})
    custom_dir = "/custom/history/root"
    prompt_with_placeholder = "Use {history_dir}/daily for summaries."

    mock_cfg = MagicMock()
    mock_cfg.history.directory = custom_dir
    mock_cfg.models.available = []

    with patch("archon.ai.decomposer.load_prompt", return_value=prompt_with_placeholder):
        with patch.object(_config_module, "_config", mock_cfg):
            decomposer, _, orch, _ = _make_decomposer(
                orch_events=[Response(content=small_json)],
            )
            await decomposer.route_task("do something")

    assert len(orch._send_calls) == 1
    instruction = orch._send_calls[0]
    assert "{history_dir}" not in instruction
    assert custom_dir in instruction


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


def test_parse_task_output_large_agents_null() -> None:
    """scope='large' with agents=null falls back to scope='small' with original prompt.

    data.get("agents", []) returns None (not []) when the JSON contains "agents": null,
    so isinstance(None, list) is False and the code falls through to the fallback.
    """
    decomposer, _, _, _ = _make_decomposer()
    result = decomposer._parse_task_output(
        '{"scope": "large", "summary": "Build it", "agents": null}',
        "build it",
    )
    assert result.scope == "small"
    assert result.prompt == "build it"


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
async def test_start_starts_main_session_only() -> None:
    """start() starts only the main session. Orch and summary are lazy-started on first use."""
    decomposer, main, orch, summary = _make_decomposer()
    await decomposer.start()
    main.start.assert_awaited_once()
    # Orch and summary sessions are lazy — NOT started at Decomposer.start()
    orch.start.assert_not_awaited()
    summary.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_stop_stops_all_sessions() -> None:
    decomposer, main, orch, summary = _make_decomposer()
    await decomposer.stop()
    main.stop.assert_awaited_once()
    orch.stop.assert_awaited_once()
    summary.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_skips_sessions_not_started() -> None:
    """stop() is a no-op for sessions that were never lazy-started."""
    from archon.ai.decomposer import Decomposer

    main_session = _mock_session(Response(content="Done."))

    with patch("archon.ai.decomposer.ClaudeSession", return_value=main_session):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            with patch("archon.ai.decomposer.load_workspace_agents", return_value=None):
                decomposer = Decomposer()

    # Lazy sessions are None — stop() must not raise
    assert decomposer._orch_session is None
    assert decomposer._summary_session is None
    await decomposer.stop()  # must not raise
    main_session.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_orch_session_returns_cached_session() -> None:
    """_ensure_orch_session() returns the existing session if already started (no duplicate start)."""
    decomposer, _, orch, _ = _make_decomposer()
    # orch is already pre-injected
    result = await decomposer._ensure_orch_session()
    assert result is orch
    orch.start.assert_not_awaited()  # no new start — session was already there


@pytest.mark.asyncio
async def test_ensure_summary_session_returns_cached_session() -> None:
    """_ensure_summary_session() returns the existing session if already started."""
    decomposer, _, _, summary = _make_decomposer()
    result = await decomposer._ensure_summary_session()
    assert result is summary
    summary.start.assert_not_awaited()


# ──────────────────────────────────────────────────────────────────
# _orch_session construction parameters
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_orch_session_created_with_max_turns_5() -> None:
    """_orch_session must be created with max_turns=5 to allow history research turns.

    The orch session is lazy — it is created on first use (first route_task() call),
    not at Decomposer() init. So we verify the parameters when it is first created.
    """
    from archon.ai.decomposer import Decomposer

    constructor_calls: list[dict] = []

    def _capturing_session(**kwargs):
        constructor_calls.append(kwargs)
        s = _mock_session(Response(content="{}"))
        return s

    with patch("archon.ai.decomposer.ClaudeSession", side_effect=_capturing_session):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            with patch("archon.ai.decomposer.load_workspace_agents", return_value=None):
                decomposer = Decomposer()
                await decomposer._ensure_orch_session()

    # First call: main session. Second call: orch session (lazy-started).
    assert len(constructor_calls) == 2
    orch_kwargs = constructor_calls[1]
    assert orch_kwargs.get("max_turns") == 5, (
        f"_orch_session must be created with max_turns=5, got: {orch_kwargs.get('max_turns')!r}"
    )


@pytest.mark.asyncio
async def test_orch_session_created_with_tools_empty_list() -> None:
    """_orch_session must be created with tools=[] to disable all default SDK tools.

    tools=None (the default) enables all tools (Bash, Read, Write, etc.).
    The orch session is only supposed to use MCP-provided history tools,
    so tools=[] is required to prevent side effects during routing.

    The orch session is lazy — verified when first created via _ensure_orch_session().
    """
    from archon.ai.decomposer import Decomposer

    constructor_calls: list[dict] = []

    def _capturing_session(**kwargs):
        constructor_calls.append(kwargs)
        return _mock_session(Response(content="{}"))

    with patch("archon.ai.decomposer.ClaudeSession", side_effect=_capturing_session):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            with patch("archon.ai.decomposer.load_workspace_agents", return_value=None):
                decomposer = Decomposer()
                await decomposer._ensure_orch_session()

    # First call: main session. Second call: orch session (lazy-started).
    assert len(constructor_calls) == 2
    orch_kwargs = constructor_calls[1]
    assert orch_kwargs.get("tools") == [], (
        f"_orch_session must be created with tools=[], got: {orch_kwargs.get('tools')!r}"
    )


# ──────────────────────────────────────────────────────────────────
# Context tracking — answer() turn buffer
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_orch_session_start_failure_does_not_cache_broken_session() -> None:
    """If _ensure_orch_session()'s start() raises, _orch_session stays None.

    A failed start must not cache an unstarted session. Otherwise every subsequent
    call to _ensure_orch_session() would return the broken session and always fail.
    """
    from archon.ai.decomposer import Decomposer

    broken_session = _mock_session()
    broken_session.start.side_effect = RuntimeError("SDK subprocess failed to start")

    call_count = 0

    def _session_factory(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: main session (always succeeds)
            return _mock_session(Response(content="ok"))
        return broken_session

    with patch("archon.ai.decomposer.ClaudeSession", side_effect=_session_factory):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            with patch("archon.ai.decomposer.load_workspace_agents", return_value=None):
                decomposer = Decomposer()
                with pytest.raises(RuntimeError, match="SDK subprocess failed to start"):
                    await decomposer._ensure_orch_session()
                # Session must NOT be cached — stays None so next call retries creation.
                assert decomposer._orch_session is None


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
    """After 20 orch calls, the old session is stopped and _orch_session is set to None (lazy).

    The new session is NOT eagerly started inside _reset_orch_if_needed() — it is
    lazy-created on the next route_task() call via _ensure_orch_session().
    """
    from archon.ai.decomposer import _ORCH_RESET_THRESHOLD

    decomposer, _, orch, _ = _make_decomposer()
    # Simulate threshold - 1 calls already made
    decomposer._orch_call_count = _ORCH_RESET_THRESHOLD - 1

    with patch("archon.ai.decomposer.load_workspace_agents", return_value=None):
        await decomposer._reset_orch_if_needed()

    # Old session was stopped
    orch.stop.assert_awaited_once()
    # _orch_session is None — new session is NOT yet created (lazy)
    assert decomposer._orch_session is None
    assert decomposer._orch_call_count == 0


@pytest.mark.asyncio
async def test_orch_reset_preserves_context_summary() -> None:
    """_context_summary is unchanged after orch reset."""
    from archon.ai.decomposer import _ORCH_RESET_THRESHOLD

    decomposer, _, _, _ = _make_decomposer()
    decomposer._context_summary = "Important context about auth."
    decomposer._orch_call_count = _ORCH_RESET_THRESHOLD - 1

    new_orch = _mock_session(Response(content='{"scope":"small","summary":"x","prompt":"y"}'))

    with patch("archon.ai.decomposer.ClaudeSession", return_value=new_orch):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            with patch("archon.ai.decomposer.load_workspace_agents", return_value=None):
                await decomposer._reset_orch_if_needed()

    assert decomposer._context_summary == "Important context about auth."


@pytest.mark.asyncio
async def test_summary_session_resets_independently() -> None:
    """Summary session resets every _SUMMARY_RESET_THRESHOLD calls.

    On reset: old session is stopped, None'd, then a fresh one is lazy-created and started.
    """
    from archon.ai.decomposer import _SUMMARY_RESET_THRESHOLD

    decomposer, _, _, summary = _make_decomposer(
        summary_events=[Response(content="Summary.")],
    )
    decomposer._summary_call_count = _SUMMARY_RESET_THRESHOLD - 1
    decomposer._pending_turns.append(("q", "a"))

    new_summary = _mock_session(Response(content="New summary."))

    with patch("archon.ai.decomposer.ClaudeSession", return_value=new_summary):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            await decomposer._refresh_summary()

    # Old summary session was stopped
    summary.stop.assert_awaited_once()
    # A fresh session was created and started
    new_summary.start.assert_awaited_once()
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
async def test_start_logs_debug_when_agents_md_missing(tmp_path, caplog) -> None:
    """Missing agents.md logs at debug level and skips injection silently."""
    import logging

    decomposer, main_session, _, _ = _make_decomposer(cwd=str(tmp_path))

    with caplog.at_level(logging.DEBUG, logger="archon"):
        await decomposer.start()

    main_session.inject_context.assert_not_called()
    assert any("agents.md" in r.message for r in caplog.records)
    assert all(r.levelno <= logging.DEBUG for r in caplog.records if "agents.md" in r.message)


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
async def test_inject_workspace_agents_both_main_and_orch_session(tmp_path) -> None:
    """Injection goes to both main and orch sessions — summary session is untouched."""
    agents_file = tmp_path / "agents.md"
    agents_file.write_text("- researcher: Does research")

    decomposer, main_session, orch_session, summary_session = _make_decomposer(
        cwd=str(tmp_path)
    )
    await decomposer.start()

    main_session.inject_context.assert_called_once()
    orch_session.inject_context.assert_called_once()
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

    with patch("archon.ai.agent_loader.asyncio.to_thread", side_effect=_spy_to_thread):
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


# ──────────────────────────────────────────────────────────────────
# context_provider — history context injection into _orch_session
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_orch_session_receives_history_context_at_first_use() -> None:
    """_orch_session.inject_context is called with combined history on first use (lazy start).

    The orch session is lazy — context is injected when _ensure_orch_session() first runs,
    not at Decomposer.start(). This test simulates the first route_task() call.
    """
    mock_provider = MagicMock()
    mock_provider.startup_context_prompt.return_value = "## History\nSome prompt"
    mock_provider.get_recent_context.return_value = "Yesterday summary"

    decomposer, _, orch, _ = _make_decomposer(context_provider=mock_provider)
    # Clear the pre-injected orch session to test lazy creation with context injection
    decomposer._orch_session = None

    with patch("archon.ai.decomposer.ClaudeSession", return_value=orch):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            with patch("archon.ai.decomposer.load_workspace_agents", return_value=None):
                await decomposer._ensure_orch_session()

    orch.inject_context.assert_called_once()
    call_arg = orch.inject_context.call_args[0][0]
    assert "## History" in call_arg
    assert "Yesterday summary" in call_arg
    assert "\n\n---\n\n" in call_arg
    mock_provider.startup_context_prompt.assert_called_with(qmd_enabled=False)


@pytest.mark.asyncio
async def test_orch_session_receives_no_context_when_provider_is_none() -> None:
    """When context_provider=None, _orch_session.inject_context is NOT called during start()."""
    decomposer, _, orch, _ = _make_decomposer()
    await decomposer.start()

    orch.inject_context.assert_not_called()


@pytest.mark.asyncio
async def test_orch_session_receives_context_after_reset() -> None:
    """After _reset_orch_if_needed() triggers a reset, the next _ensure_orch_session()
    call (lazy creation) injects history context into the new session.
    """
    mock_provider = MagicMock()
    mock_provider.startup_context_prompt.return_value = "## History\nReset prompt"
    mock_provider.get_recent_context.return_value = "Reset context"

    decomposer, _, orch, _ = _make_decomposer(context_provider=mock_provider)

    # Force reset threshold
    from archon.ai.decomposer import _ORCH_RESET_THRESHOLD
    decomposer._orch_call_count = _ORCH_RESET_THRESHOLD - 1

    with patch("archon.ai.decomposer.load_workspace_agents", return_value=None):
        await decomposer._reset_orch_if_needed()

    # After reset, _orch_session is None — lazy
    assert decomposer._orch_session is None

    # Now simulate the next route_task() lazy-starting the new session
    new_orch = _mock_session(Response(content='{"scope":"small","summary":"x","prompt":"y"}'))
    with patch("archon.ai.decomposer.ClaudeSession", return_value=new_orch):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            with patch("archon.ai.decomposer.load_workspace_agents", return_value=None):
                await decomposer._ensure_orch_session()

    # inject_context must have been called on the new session with history context
    assert new_orch.inject_context.call_count == 1
    call_arg = new_orch.inject_context.call_args[0][0]
    assert "## History" in call_arg
    assert "Reset context" in call_arg


@pytest.mark.asyncio
async def test_orch_session_receives_agents_md(tmp_path) -> None:
    """When agents.md exists in cwd, _orch_session.inject_context is also called with its content."""
    agents_content = "## Agent A\nDoes things"
    (tmp_path / "agents.md").write_text(agents_content, encoding="utf-8")

    decomposer, _, orch, _ = _make_decomposer(cwd=str(tmp_path))
    await decomposer.start()

    # orch.inject_context should have been called (at least once) with agents content
    calls = [call[0][0] for call in orch.inject_context.call_args_list]
    agents_calls = [c for c in calls if "Workspace Agents" in c and "Agent A" in c]
    assert agents_calls, f"Expected orch inject_context with agents.md content, got: {calls}"


@pytest.mark.asyncio
async def test_orch_session_receives_both_history_and_agents_md(tmp_path) -> None:
    """When both context_provider and agents.md exist, _orch_session gets both injections on first use."""
    mock_provider = MagicMock()
    mock_provider.startup_context_prompt.return_value = "## History\nHistory prompt"
    mock_provider.get_recent_context.return_value = "History context"

    (tmp_path / "agents.md").write_text("## Agent B\nDoes other things", encoding="utf-8")

    decomposer, _, orch, _ = _make_decomposer(
        context_provider=mock_provider, cwd=str(tmp_path)
    )
    # Clear pre-injected orch session to test lazy-start context injection
    decomposer._orch_session = None

    with patch("archon.ai.decomposer.ClaudeSession", return_value=orch):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            await decomposer._ensure_orch_session()

    # Should have 2 inject_context calls: history context + agents.md
    assert orch.inject_context.call_count == 2


# ──────────────────────────────────────────────────────────────────
# Fix 1: _reset_orch_if_needed re-injects agents.md after reset
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_orch_session_agents_md_reinjected_after_reset(tmp_path) -> None:
    """After _reset_orch_if_needed() triggers a reset, the next _ensure_orch_session()
    call (lazy creation) injects agents.md content into the new session.
    """
    from archon.ai.decomposer import _ORCH_RESET_THRESHOLD

    agents_content = "## Agent X\nDoes workspace things"
    (tmp_path / "agents.md").write_text(agents_content, encoding="utf-8")

    decomposer, _, orch, _ = _make_decomposer(cwd=str(tmp_path))

    # Force orch reset
    decomposer._orch_call_count = _ORCH_RESET_THRESHOLD - 1

    await decomposer._reset_orch_if_needed()

    # After reset, _orch_session is None — lazy
    assert decomposer._orch_session is None

    # Simulate the next route_task() lazy-starting the new session
    new_orch = _mock_session(Response(content='{"scope":"small","summary":"x","prompt":"y"}'))
    with patch("archon.ai.decomposer.ClaudeSession", return_value=new_orch):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            await decomposer._ensure_orch_session()

    # agents.md content must appear in inject_context calls on the new session
    all_injected = [call[0][0] for call in new_orch.inject_context.call_args_list]
    agents_calls = [c for c in all_injected if "Workspace Agents" in c and "Agent X" in c]
    assert agents_calls, (
        f"Expected new _orch_session.inject_context with agents.md content after reset, "
        f"got calls: {all_injected}"
    )


# ──────────────────────────────────────────────────────────────────
# Fix 2: context_provider errors in start() and _reset_orch_if_needed are guarded
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_continues_when_context_provider_raises() -> None:
    """When context_provider.startup_context_prompt() raises, start() completes without error."""
    mock_provider = MagicMock()
    mock_provider.startup_context_prompt.side_effect = RuntimeError("history read failed")

    decomposer, main, orch, _ = _make_decomposer(context_provider=mock_provider)

    # Must not raise
    await decomposer.start()

    # Main session must still have started
    main.start.assert_awaited_once()

    # _orch_session.inject_context must NOT have been called (exception before injection)
    orch.inject_context.assert_not_called()


@pytest.mark.asyncio
async def test_reset_orch_continues_when_context_provider_raises(caplog) -> None:
    """When context_provider raises during lazy _ensure_orch_session(), the orch session
    is still created and started. The error is logged but does not propagate.

    Since _reset_orch_if_needed() no longer eagerly starts the new session, the
    context_provider error now surfaces in _ensure_orch_session() (next route_task() call).
    """
    import logging

    from archon.ai.decomposer import _ORCH_RESET_THRESHOLD

    mock_provider = MagicMock()
    mock_provider.startup_context_prompt.return_value = "## History"
    mock_provider.get_recent_context.side_effect = RuntimeError("context read failed")

    decomposer, _, orch, _ = _make_decomposer(context_provider=mock_provider)

    decomposer._orch_call_count = _ORCH_RESET_THRESHOLD - 1

    with patch("archon.ai.decomposer.load_workspace_agents", return_value=None):
        await decomposer._reset_orch_if_needed()

    # Old session was stopped, _orch_session is now None (lazy)
    orch.stop.assert_awaited()
    assert decomposer._orch_session is None

    # Simulate the next route_task() lazy-starting the new session — context_provider raises
    new_orch = _mock_session(Response(content='{"scope":"small","summary":"x","prompt":"y"}'))
    with caplog.at_level(logging.WARNING, logger="archon"):
        with patch("archon.ai.decomposer.ClaudeSession", return_value=new_orch):
            with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
                with patch("archon.ai.decomposer.load_workspace_agents", return_value=None):
                    # Must not raise — context injection errors are guarded
                    await decomposer._ensure_orch_session()

    # New session was started despite the context_provider error
    new_orch.start.assert_awaited()

    # Warning was logged about failed injection
    assert any("Failed to inject" in r.message for r in caplog.records), (
        f"Expected warning log about failed injection, got: {[r.message for r in caplog.records]}"
    )


# ──────────────────────────────────────────────────────────────────
# Timeout guard on _orch_session.send() in route_task()
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_task_times_out_and_falls_back(monkeypatch) -> None:
    """If _orch_session.send() hangs forever, route_task() times out and
    returns TaskOutput(scope='small', prompt=original_prompt)."""
    original_prompt = "do something important"

    # Build a decomposer whose orch_session.send() hangs indefinitely
    decomposer, _, orch_session, _ = _make_decomposer()

    async def _hanging_send(prompt: str):
        await asyncio.sleep(9999)
        # Never yields — satisfies the type checker
        return
        yield  # noqa: E501 — make it an async generator

    orch_session.send = _hanging_send

    monkeypatch.setattr("archon.ai.decomposer._ORCH_TIMEOUT_S", 0.05)
    result = await decomposer.route_task(original_prompt)

    assert result.scope == "small"
    assert result.prompt == original_prompt


@pytest.mark.asyncio
async def test_route_task_reset_timeout_falls_back(monkeypatch) -> None:
    """If _reset_orch_if_needed() hangs (e.g. stop() stalls), route_task() falls back
    to scope='small' with the original prompt instead of blocking indefinitely."""
    original_prompt = "do something while reset hangs"

    decomposer, _, orch_session, _ = _make_decomposer()

    # Make orch stop() hang indefinitely to simulate a stalled SDK subprocess
    async def _hanging_stop():
        await asyncio.sleep(9999)

    orch_session.stop = _hanging_stop

    # Force the reset threshold so _reset_orch_if_needed() actually runs stop/start
    from archon.ai.decomposer import _ORCH_RESET_THRESHOLD
    decomposer._orch_call_count = _ORCH_RESET_THRESHOLD - 1

    monkeypatch.setattr("archon.ai.decomposer._ORCH_RESET_TIMEOUT_S", 0.05)
    result = await decomposer.route_task(original_prompt)

    assert result.scope == "small"
    assert result.prompt == original_prompt


# ─── New: is_fallback field and trivial scope ───


@pytest.mark.asyncio
async def test_route_task_fallback_includes_is_fallback_flag_on_reset_timeout(monkeypatch) -> None:
    """When _reset_orch_if_needed() times out, result.is_fallback is True."""
    original_prompt = "build a feature"
    decomposer, _, orch_session, _ = _make_decomposer()

    async def _hanging_stop():
        await asyncio.sleep(9999)

    orch_session.stop = _hanging_stop
    from archon.ai.decomposer import _ORCH_RESET_THRESHOLD
    decomposer._orch_call_count = _ORCH_RESET_THRESHOLD - 1
    monkeypatch.setattr("archon.ai.decomposer._ORCH_RESET_TIMEOUT_S", 0.05)

    result = await decomposer.route_task(original_prompt)

    assert result.is_fallback is True


@pytest.mark.asyncio
async def test_route_task_fallback_silent_on_reset_timeout(monkeypatch) -> None:
    """When _reset_orch_if_needed() times out, the fallback is silent (fallback_reason is empty).

    Timeout-based fallbacks are internal routing decisions — not user-visible errors.
    The system silently falls back to inline execution without alarming the user.
    """
    original_prompt = "build a feature"
    decomposer, _, orch_session, _ = _make_decomposer()

    async def _hanging_stop():
        await asyncio.sleep(9999)

    orch_session.stop = _hanging_stop
    from archon.ai.decomposer import _ORCH_RESET_THRESHOLD
    decomposer._orch_call_count = _ORCH_RESET_THRESHOLD - 1
    monkeypatch.setattr("archon.ai.decomposer._ORCH_RESET_TIMEOUT_S", 0.05)

    result = await decomposer.route_task(original_prompt)

    # Silent fallback: is_fallback=True but empty reason (no user alarm)
    assert result.is_fallback is True
    assert result.fallback_reason == ""


@pytest.mark.asyncio
async def test_route_task_reset_non_timeout_exception_falls_back(monkeypatch) -> None:
    """If _reset_orch_if_needed() raises a non-TimeoutError (e.g. RuntimeError from SDK crash),
    route_task() catches it, logs an error, and falls back silently to scope='small'."""
    original_prompt = "do something"
    decomposer, _, orch_session, _ = _make_decomposer()

    async def _crashing_stop():
        raise RuntimeError("SDK subprocess failed")

    orch_session.stop = _crashing_stop

    # Force the reset threshold so _reset_orch_if_needed() actually runs stop/start
    from archon.ai.decomposer import _ORCH_RESET_THRESHOLD
    decomposer._orch_call_count = _ORCH_RESET_THRESHOLD - 1

    result = await decomposer.route_task(original_prompt)

    assert result.scope == "small"
    assert result.prompt == original_prompt
    assert result.is_fallback is True
    assert result.fallback_reason == ""


@pytest.mark.asyncio
async def test_route_task_fallback_includes_is_fallback_flag_on_send_timeout(monkeypatch) -> None:
    """When _orch_session.send() times out, result.is_fallback is True and reason is empty (silent)."""
    original_prompt = "do something important"
    decomposer, _, orch_session, _ = _make_decomposer()

    async def _hanging_send(prompt: str):
        await asyncio.sleep(9999)
        return
        yield  # noqa: E501

    orch_session.send = _hanging_send
    monkeypatch.setattr("archon.ai.decomposer._ORCH_TIMEOUT_S", 0.05)

    result = await decomposer.route_task(original_prompt)

    assert result.is_fallback is True
    assert result.fallback_reason == ""


@pytest.mark.asyncio
async def test_route_task_fallback_on_exception() -> None:
    """When _orch_session.send() raises, result.is_fallback is True and reason mentions inline."""
    decomposer, _, orch, _ = _make_decomposer()

    async def _crashing_send(prompt: str):
        raise RuntimeError("connection lost")
        yield  # noqa: E501

    orch.send = _crashing_send

    result = await decomposer.route_task("build something big")

    assert result.is_fallback is True
    assert "inline" in result.fallback_reason.lower() or "attempting" in result.fallback_reason.lower()


def test_parse_task_output_trivial_scope() -> None:
    """Raw JSON with scope='trivial' → TaskOutput(scope='trivial', summary=..., prompt=...)."""
    decomposer, _, _, _ = _make_decomposer()
    raw = json.dumps({"scope": "trivial", "summary": "Quick answer", "prompt": "tell me"})
    result = decomposer._parse_task_output(raw, "tell me")

    assert result.scope == "trivial"
    assert result.summary == "Quick answer"
    assert result.prompt == "tell me"


def test_parse_task_output_small_scope_is_not_fallback() -> None:
    """Valid small scope result must have is_fallback=False."""
    decomposer, _, _, _ = _make_decomposer()
    raw = json.dumps({"scope": "small", "summary": "Fix typo", "prompt": "fix it"})
    result = decomposer._parse_task_output(raw, "fix it")

    assert result.is_fallback is False


def test_parse_task_output_trivial_scope_is_not_fallback() -> None:
    """Valid trivial scope result must have is_fallback=False."""
    decomposer, _, _, _ = _make_decomposer()
    raw = json.dumps({"scope": "trivial", "summary": "Quick", "prompt": "tell me"})
    result = decomposer._parse_task_output(raw, "tell me")

    assert result.is_fallback is False


def test_parse_task_output_parse_failure_is_fallback() -> None:
    """Invalid JSON → fallback result with is_fallback=True."""
    decomposer, _, _, _ = _make_decomposer()
    result = decomposer._parse_task_output("not valid json at all", "do something")

    assert result.is_fallback is True


def test_parse_task_output_unknown_scope_is_fallback() -> None:
    """scope='medium' (unknown) → fallback result with is_fallback=True."""
    decomposer, _, _, _ = _make_decomposer()
    result = decomposer._parse_task_output(
        '{"scope": "medium", "summary": "X"}',
        "original prompt",
    )

    assert result.is_fallback is True


@pytest.mark.asyncio
async def test_route_task_pending_turns_not_appended_for_small_scope() -> None:
    """When route_task returns scope='small', _pending_turns is NOT appended after route_task."""
    small_json = json.dumps({"scope": "small", "summary": "Quick task", "prompt": "do it"})
    decomposer, _, _, _ = _make_decomposer(
        orch_events=[Response(content=small_json)],
    )
    initial_len = len(decomposer._pending_turns)

    await decomposer.route_task("quick task")

    # For small/trivial scope, pending_turns should NOT grow (no summary appended)
    assert len(decomposer._pending_turns) == initial_len


@pytest.mark.asyncio
async def test_route_task_pending_turns_not_appended_for_trivial_scope() -> None:
    """When route_task returns scope='trivial', _pending_turns is NOT appended after route_task."""
    trivial_json = json.dumps({"scope": "trivial", "summary": "Quick lookup", "prompt": "tell me"})
    decomposer, _, _, _ = _make_decomposer(
        orch_events=[Response(content=trivial_json)],
    )
    initial_len = len(decomposer._pending_turns)

    await decomposer.route_task("tell me something")

    assert len(decomposer._pending_turns) == initial_len


@pytest.mark.asyncio
async def test_route_task_pending_turns_appended_for_large_scope() -> None:
    """When route_task returns scope='large', _pending_turns IS appended after route_task."""
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
    initial_len = len(decomposer._pending_turns)

    await decomposer.route_task("refactor the auth module")

    # Large scope: pending_turns grows (summary recorded)
    assert len(decomposer._pending_turns) > initial_len


# ──────────────────────────────────────────────────────────────────
# Bug fixes: C1, C2, C5
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_task_acloses_orch_generator_on_timeout() -> None:
    """C1: gen.aclose() is called when orch.send() times out.

    The generator must be explicitly closed so that _send_lock is released
    promptly even when the asyncio.timeout() fires mid-iteration.  We verify
    this by wrapping the async generator in a spy object whose aclose() we can
    intercept (aclose is read-only on native async generators so we use a wrapper).
    """
    aclose_called = False

    async def _slow_send(prompt: str):
        await asyncio.sleep(100)  # simulate a hung generator
        yield Response(content="{}")

    class _SpyGen:
        """Thin wrapper around an async generator that records aclose() calls."""

        def __init__(self, inner):
            self._inner = inner

        def __aiter__(self):
            return self

        async def __anext__(self):
            return await self._inner.__anext__()

        async def aclose(self):
            nonlocal aclose_called
            aclose_called = True
            await self._inner.aclose()

    def _spying_send(prompt: str):
        return _SpyGen(_slow_send(prompt))

    decomposer, _, orch, _ = _make_decomposer()
    orch.send = _spying_send

    # Patch the timeout to fire immediately (1 ms) so the test is fast.
    with patch("archon.ai.decomposer._ORCH_TIMEOUT_S", 0.001):
        result = await decomposer.route_task("test prompt")

    assert result.is_fallback
    assert aclose_called, "gen.aclose() must be called on timeout so _send_lock is released"


@pytest.mark.asyncio
async def test_route_task_orch_init_timeout_falls_back_silently() -> None:
    """C2: When _ensure_orch_session() hangs, route_task falls back silently.

    Without a timeout on the init call the Pipeline lock (held by Pipeline.send())
    would be held forever.  The fix wraps the call in _ORCH_RESET_TIMEOUT_S.
    """
    import asyncio as _asyncio

    original_ensure = None

    async def _hanging_ensure():
        # Simulate a hung SDK start
        await _asyncio.sleep(100)
        raise RuntimeError("should not reach here")

    decomposer, _, _, _ = _make_decomposer()
    decomposer._ensure_orch_session = _hanging_ensure  # type: ignore[method-assign]

    with patch("archon.ai.decomposer._ORCH_RESET_TIMEOUT_S", 0.001):
        result = await decomposer.route_task("test prompt")

    assert result.is_fallback
    assert result.scope == "small"
    assert result.prompt == "test prompt"


@pytest.mark.asyncio
async def test_reset_orch_nulled_before_stop() -> None:
    """C5: _orch_session is set to None BEFORE old_session.stop() is awaited.

    If a timeout fires during stop(), _orch_session must already be None so
    there is no zombie reference to a partially-stopped session.
    """
    from archon.ai.decomposer import _ORCH_RESET_THRESHOLD

    null_after_stop: bool | None = None  # True = nulled before stop, False = after

    async def _spy_stop():
        nonlocal null_after_stop
        # At the moment stop() is awaited, check whether _orch_session is already None
        null_after_stop = decomposer._orch_session is None

    decomposer, _, orch, _ = _make_decomposer()
    orch.stop = _spy_stop  # type: ignore[method-assign]

    decomposer._orch_call_count = _ORCH_RESET_THRESHOLD - 1

    new_orch = _mock_session(Response(content='{"scope":"small","summary":"x","prompt":"y"}'))

    with patch("archon.ai.decomposer.ClaudeSession", return_value=new_orch):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            with patch("archon.ai.decomposer.load_workspace_agents", return_value=None):
                await decomposer._reset_orch_if_needed()

    assert null_after_stop is True, (
        "_orch_session must be set to None BEFORE old_session.stop() is called "
        "to prevent zombie state on timeout"
    )


# ──────────────────────────────────────────────────────────────────
# BUG FIX: stop() main session error must not propagate
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stop_main_session_error_does_not_propagate() -> None:
    """stop() must not raise even if the main session's stop() throws."""
    decomposer, main, _, _ = _make_decomposer()
    main.stop = AsyncMock(side_effect=RuntimeError("disconnect failed"))

    # Must complete without raising
    await decomposer.stop()


# ──────────────────────────────────────────────────────────────────
# BUG FIX: _refresh_summary() stop timeout must not hang
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_summary_summary_stop_timeout_does_not_hang() -> None:
    """_refresh_summary() must not hang when summary_session.stop() is slow.

    Without fix: await summary_session.stop() at reset threshold has no timeout,
    so a hanging SDK hangs _refresh_summary() indefinitely.
    With fix: asyncio.timeout(_SUMMARY_RESET_TIMEOUT_S) limits the stop() call.
    """
    from archon.ai.decomposer import _SUMMARY_RESET_THRESHOLD

    decomposer, _, _, summary = _make_decomposer()

    # Make summary_session.stop() hang longer than the expected timeout constant
    async def _slow_stop() -> None:
        await asyncio.sleep(60)

    summary.stop = _slow_stop  # type: ignore[method-assign]

    # Set counter so next call triggers the reset path
    decomposer._summary_call_count = _SUMMARY_RESET_THRESHOLD - 1

    # Add a pending turn so _refresh_summary proceeds past the early-return guard
    decomposer._pending_turns.append(("q", "a"))

    # After the slow session is stopped/nulled, _ensure_summary_session will
    # create a new ClaudeSession — patch it so we don't hit the real SDK.
    new_summary = _mock_session(Response(content="new summary"))
    with patch("archon.ai.decomposer.ClaudeSession", return_value=new_summary):
        # Without the fix this hangs for 60s; with the fix it completes in ≤10s.
        # We give it 15s headroom to avoid CI flakiness.
        try:
            async with asyncio.timeout(15.0):
                await decomposer._refresh_summary()
        except TimeoutError:
            pytest.fail(
                "_refresh_summary() hung for >15s — "
                "summary_session.stop() timeout not applied"
            )


# ──────────────────────────────────────────────────────────────────
# BUG-15 — Cost carryover across orch / summary session resets
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_orch_reset_preserves_cost_in_usage_stats() -> None:
    """After an orch session reset, the pre-reset cost must appear in usage_stats.

    BUG-15: _reset_orch_if_needed() stopped the old session and discarded its
    cost. Only the new (empty) session's cost was reported. Accumulated costs
    must be carried over so callers see the full lifetime cost.
    """
    from archon.ai.decomposer import _ORCH_RESET_THRESHOLD, Decomposer

    decomposer, _, orch, _ = _make_decomposer()
    # Give the current orch session a known cost
    orch.usage_stats = {"total_cost_usd": 0.05, "cumulative_cache_creation": 0}
    decomposer._orch_call_count = _ORCH_RESET_THRESHOLD - 1

    new_orch = _mock_session(Response(content='{"scope":"small","summary":"x","prompt":"y"}'))
    new_orch.usage_stats = None  # fresh session has no cost yet

    with patch("archon.ai.decomposer.ClaudeSession", return_value=new_orch):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            with patch("archon.ai.decomposer.load_workspace_agents", return_value=None):
                await decomposer._reset_orch_if_needed()

    # After reset: new session is active with no cost, but carryover must be preserved.
    # usage_stats["sessions"]["orchestration"]["cost_usd"] must include the old cost.
    decomposer._session.usage_stats = {"total_cost_usd": 0.01}
    stats = decomposer.usage_stats
    assert stats is not None
    orch_cost = stats["sessions"]["orchestration"]["cost_usd"]
    assert orch_cost == pytest.approx(0.05), (
        f"Expected orch cost carryover 0.05, got {orch_cost!r}. "
        "Pre-reset cost must be accumulated, not discarded."
    )


@pytest.mark.asyncio
async def test_summary_reset_preserves_cost_in_usage_stats() -> None:
    """After a summary session reset, the pre-reset cost must appear in usage_stats.

    BUG-15: same issue for the summary session reset path in _refresh_summary().
    """
    from archon.ai.decomposer import _SUMMARY_RESET_THRESHOLD

    decomposer, _, _, summary = _make_decomposer(
        summary_events=[Response(content="Summary.")],
    )
    # Give the current summary session a known cost
    summary.usage_stats = {"total_cost_usd": 0.03, "cumulative_cache_creation": 0}
    decomposer._summary_call_count = _SUMMARY_RESET_THRESHOLD - 1
    decomposer._pending_turns.append(("q", "a"))

    new_summary = _mock_session(Response(content="New summary."))
    new_summary.usage_stats = None  # fresh session has no cost yet

    with patch("archon.ai.decomposer.ClaudeSession", return_value=new_summary):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            await decomposer._refresh_summary()

    # usage_stats["sessions"]["summary"]["cost_usd"] must include the old cost.
    decomposer._session.usage_stats = {"total_cost_usd": 0.01}
    stats = decomposer.usage_stats
    assert stats is not None
    summary_cost = stats["sessions"]["summary"]["cost_usd"]
    assert summary_cost == pytest.approx(0.03), (
        f"Expected summary cost carryover 0.03, got {summary_cost!r}. "
        "Pre-reset cost must be accumulated, not discarded."
    )


# ──────────────────────────────────────────────────────────────────
# BUG-D — No eager session pre-start in _reset_orch_if_needed()
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reset_orch_does_not_eagerly_start_new_session() -> None:
    """After _reset_orch_if_needed() triggers a reset, session.start() must NOT
    be called eagerly. The next route_task() call lazy-creates it via
    _ensure_orch_session().

    BUG-D: the eager await self._ensure_orch_session() at the end of
    _reset_orch_if_needed() called session.start() while the Pipeline._lock
    was held, wasting 2-5s on SDK subprocess spawn for no reason.
    """
    from archon.ai.decomposer import _ORCH_RESET_THRESHOLD

    decomposer, _, orch, _ = _make_decomposer()
    decomposer._orch_call_count = _ORCH_RESET_THRESHOLD - 1

    # Track how many ClaudeSession instances are started inside _reset_orch_if_needed
    started_sessions: list = []
    original_ensure = decomposer._ensure_orch_session

    async def _spy_ensure():
        result = await original_ensure()
        started_sessions.append(result)
        return result

    # Patch ClaudeSession so a new one is created on reset
    new_orch = _mock_session(Response(content='{"scope":"small","summary":"x","prompt":"y"}'))

    with patch("archon.ai.decomposer.ClaudeSession", return_value=new_orch):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            with patch("archon.ai.decomposer.load_workspace_agents", return_value=None):
                # Reset clears the session and must NOT start a new one
                await decomposer._reset_orch_if_needed()

    # After reset: _orch_session must be None (lazy, not eagerly pre-started).
    assert decomposer._orch_session is None, (
        "_orch_session must be None after reset — lazy creation deferred to next route_task(). "
        f"Got: {decomposer._orch_session!r}"
    )
    # new_orch.start must NOT have been called inside _reset_orch_if_needed()
    new_orch.start.assert_not_awaited()
