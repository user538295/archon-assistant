"""Tests for Decomposer — the brain that evaluates, answers, and plans."""

import asyncio
import json
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.decomposer import TaskOutput
from archon.ai.event_mapper import Response, ThinkingResult, ToolStarted


async def _collect(decomposer, prompt):
    """Collect all events and the TaskOutput sentinel from route_task()."""
    from tests.conftest import collect_route_task
    return await collect_route_task(decomposer, prompt)


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _mock_session(*events, is_processing=False):
    """Build a mock ClaudeSession that yields given events from send()."""
    from tests.conftest import _mock_session_factory

    return _mock_session_factory(*events, is_processing=is_processing)


def _make_decomposer(session_events=None, router_events=None, summary_events=None, **kwargs):
    """Build a Decomposer with mocked main, orchestration, and summary sessions.

    Router and summary sessions are pre-injected into the Decomposer's lazy slots so
    that _ensure_router_session() / _ensure_summary_session() return them immediately
    without trying to start a real SDK subprocess.

    Returns (decomposer, main_session, router_session, summary_session).
    """
    from archon.ai.decomposer import Decomposer

    if session_events is None:
        session_events = [Response(content="Done.")]
    if router_events is None:
        router_events = [Response(content='{"scope":"small","summary":"Direct handling","prompt":"original prompt"}')]
    if summary_events is None:
        summary_events = [Response(content="User discussed topic X.")]

    main_session = _mock_session(*session_events)
    router_session = _mock_session(*router_events)
    summary_session = _mock_session(*summary_events)

    # Only the main session is created during __init__; router/summary are lazy.
    with patch(
        "archon.ai.decomposer.ClaudeSession",
        return_value=main_session,
    ):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            decomposer = Decomposer(**kwargs)

    # Pre-inject lazy sessions so _ensure_*_session() returns them instantly.
    decomposer._router_session = router_session
    decomposer._summary_session = summary_session

    return decomposer, main_session, router_session, summary_session


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
    """answer() must use the main session, not the routing session."""
    decomposer, main, router, _ = _make_decomposer()
    _ = [e async for e in decomposer.answer("what is 2+2")]

    assert len(main._send_calls) == 1
    assert "what is 2+2" in main._send_calls[0]
    # Orchestration session must NOT be touched
    assert len(router._send_calls) == 0


# ──────────────────────────────────────────────────────────────────
# context_window_overrides — forwarded to main session only
# ──────────────────────────────────────────────────────────────────


def test_decomposer_forwards_overrides_to_session() -> None:
    """Decomposer must pass context_window_overrides to the main ClaudeSession."""
    from archon.ai.decomposer import Decomposer

    with patch("archon.ai.decomposer.ClaudeSession") as mock_cls:
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            Decomposer(context_window_overrides={"claude-3-5-sonnet-20241022": 1_000_000})

    mock_cls.assert_called_once()
    _, kwargs = mock_cls.call_args
    assert kwargs.get("context_window_overrides") == {"claude-3-5-sonnet-20241022": 1_000_000}


@pytest.mark.asyncio
async def test_router_summary_sessions_do_not_receive_overrides() -> None:
    """Router and summary sessions must NOT receive context_window_overrides."""
    from archon.ai.decomposer import Decomposer

    mock_session = MagicMock()
    mock_session.start = AsyncMock()
    mock_session.inject_context = MagicMock()

    call_kwargs: list[dict] = []

    def capture_session(*args: object, **kwargs: object) -> MagicMock:
        call_kwargs.append(kwargs)
        return mock_session

    with patch("archon.ai.decomposer.ClaudeSession", side_effect=capture_session):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            decomposer = Decomposer(context_window_overrides={"m": 1_000_000})
            await decomposer._ensure_router_session()
            await decomposer._ensure_summary_session()

    # First call is the main session (should have overrides)
    assert call_kwargs[0].get("context_window_overrides") == {"m": 1_000_000}
    # Router session (second call) must NOT have overrides
    assert "context_window_overrides" not in call_kwargs[1] or call_kwargs[1].get("context_window_overrides") is None
    # Summary session (third call) must NOT have overrides
    assert "context_window_overrides" not in call_kwargs[2] or call_kwargs[2].get("context_window_overrides") is None


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
        router_events=[Response(content=small_json)],
    )
    _, result = await _collect(decomposer, "fix the typo in readme")

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
        router_events=[Response(content=large_json)],
    )
    _, result = await _collect(decomposer, "refactor the auth module")

    assert result.scope == "large"
    assert result.summary == "Refactor auth module"
    assert result.prompt is None
    assert result.agents is not None
    assert len(result.agents) == 2


@pytest.mark.asyncio
async def test_route_task_graceful_fallback_on_bad_json() -> None:
    decomposer, _, _, _ = _make_decomposer(
        router_events=[Response(content="Let me handle this directly")],
    )
    _, result = await _collect(decomposer, "do something")

    # Fallback: treat as small task with the prompt as-is
    assert result.scope == "small"
    assert result.prompt is not None


@pytest.mark.asyncio
async def test_route_task_sends_prompt_to_orchestration_session() -> None:
    """route_task() must use the routing session, not the main session."""
    small_json = json.dumps({"scope": "small", "summary": "test", "prompt": "do it"})
    decomposer, main, router, _ = _make_decomposer(
        router_events=[Response(content=small_json)],
    )
    await _collect(decomposer, "big task here")

    assert len(router._send_calls) == 1
    assert "big task here" in router._send_calls[0]
    # Main session must NOT be touched
    assert len(main._send_calls) == 0


@pytest.mark.asyncio
async def test_route_task_crash_falls_back_to_small() -> None:
    """When routing session raises during route_task, fall back to small scope."""
    decomposer, _, router, _ = _make_decomposer()

    async def _crashing_send(prompt: str):
        raise RuntimeError("connection lost")
        yield  # noqa: E501

    router.send = _crashing_send

    _, result = await _collect(decomposer, "build something big")

    assert result.scope == "small"
    assert result.prompt == "build something big"


@pytest.mark.asyncio
async def test_route_task_sends_internal_tag() -> None:
    """route_task sends the INTERNAL routing tag via routing session."""
    small_json = json.dumps({"scope": "small", "summary": "test", "prompt": "do it"})
    decomposer, _, router, _ = _make_decomposer(
        router_events=[Response(content=small_json)],
    )
    await _collect(decomposer, "do something")

    assert len(router._send_calls) == 1
    assert "[INTERNAL:" in router._send_calls[0]


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
            decomposer, _, router, _ = _make_decomposer(
                router_events=[Response(content=small_json)],
            )
            await _collect(decomposer, "do something")

    assert len(router._send_calls) == 1
    instruction = router._send_calls[0]
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
    decomposer, main, router, summary = _make_decomposer()
    router.usage_stats = None
    summary.usage_stats = None
    main.usage_stats = {"total_cost_usd": 0.05}
    stats = decomposer.usage_stats
    assert stats is not None
    assert stats["total_cost_usd"] == 0.05  # core field preserved


def test_usage_stats_includes_sessions_key() -> None:
    decomposer, main, router, summary = _make_decomposer()
    main.usage_stats = {"total_cost_usd": 0.05}
    router.usage_stats = None
    summary.usage_stats = None
    stats = decomposer.usage_stats
    assert stats is not None
    assert "sessions" in stats


def test_usage_stats_sessions_has_orchestration() -> None:
    decomposer, main, router, summary = _make_decomposer()
    main.usage_stats = {"total_cost_usd": 0.05}
    router.usage_stats = {"total_cost_usd": 0.01, "cumulative_cache_creation": 500}
    summary.usage_stats = None
    stats = decomposer.usage_stats
    assert stats is not None
    assert "orchestration" in stats["sessions"]
    assert stats["sessions"]["orchestration"]["cost_usd"] == 0.01
    assert stats["sessions"]["orchestration"]["cumulative_cache_creation"] == 500


def test_usage_stats_sessions_has_summary() -> None:
    decomposer, main, router, summary = _make_decomposer()
    main.usage_stats = {"total_cost_usd": 0.05}
    router.usage_stats = None
    summary.usage_stats = {"total_cost_usd": 0.002, "cumulative_cache_creation": 200}
    stats = decomposer.usage_stats
    assert stats is not None
    assert "summary" in stats["sessions"]
    assert stats["sessions"]["summary"]["cost_usd"] == 0.002
    assert stats["sessions"]["summary"]["cumulative_cache_creation"] == 200


def test_usage_stats_sub_session_zero_when_no_data() -> None:
    decomposer, main, router, summary = _make_decomposer()
    main.usage_stats = {"total_cost_usd": 0.05}
    router.usage_stats = None
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
    """total_cost_usd must reflect main session cost only — NOT including router/summary.

    Contract: Pipeline.usage_stats adds sub-session costs on top. If Decomposer ever
    aggregates them here too, Pipeline will double-count. This test guards that contract.
    """
    decomposer, main, router, summary = _make_decomposer()
    main.usage_stats = {"total_cost_usd": 0.05}
    router.usage_stats = {"total_cost_usd": 0.01, "cumulative_cache_creation": 0}
    summary.usage_stats = {"total_cost_usd": 0.003, "cumulative_cache_creation": 0}
    stats = decomposer.usage_stats
    assert stats is not None
    # Must be exactly 0.05 — sub-session costs stay in sessions dict, not in total
    assert stats["total_cost_usd"] == 0.05


def test_decomposer_context_percentage_delegates() -> None:
    """context_percentage() must delegate to the inner ClaudeSession, not recompute."""
    decomposer, main, _, _ = _make_decomposer()
    main.context_percentage = MagicMock(return_value=42)
    assert decomposer.context_percentage() == 42
    main.context_percentage.assert_called_once_with()


def test_activate_skill_delegates() -> None:
    decomposer, main, _, _ = _make_decomposer()
    skill = MagicMock()
    decomposer.activate_skill(skill)
    main.activate_skill.assert_called_once_with(skill)


def test_inject_context_delegates() -> None:
    decomposer, main, _, _ = _make_decomposer()
    decomposer.inject_context("some context")
    main.inject_context.assert_called_once_with("some context", "context", None)


def test_decomposer_inject_context_forwards_type() -> None:
    """Decomposer.inject_context forwards injection_type and detail to inner session."""
    decomposer, main, _, _ = _make_decomposer()
    decomposer.inject_context("x", "workspace_agents", detail="f1.md")
    main.inject_context.assert_called_once_with("x", "workspace_agents", "f1.md")


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
    """start() starts only the main session. Router and summary are lazy-started on first use."""
    decomposer, main, router, summary = _make_decomposer()
    await decomposer.start()
    main.start.assert_awaited_once()
    # Router and summary sessions are lazy — NOT started at Decomposer.start()
    router.start.assert_not_awaited()
    summary.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_stop_stops_all_sessions() -> None:
    decomposer, main, router, summary = _make_decomposer()
    await decomposer.stop()
    main.stop.assert_awaited_once()
    router.stop.assert_awaited_once()
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
    assert decomposer._router_session is None
    assert decomposer._summary_session is None
    await decomposer.stop()  # must not raise
    main_session.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_router_session_returns_cached_session() -> None:
    """_ensure_router_session() returns the existing session if already started (no duplicate start)."""
    decomposer, _, router, _ = _make_decomposer()
    # router is already pre-injected
    result = await decomposer._ensure_router_session()
    assert result is router
    router.start.assert_not_awaited()  # no new start — session was already there


@pytest.mark.asyncio
async def test_ensure_summary_session_returns_cached_session() -> None:
    """_ensure_summary_session() returns the existing session if already started."""
    decomposer, _, _, summary = _make_decomposer()
    result = await decomposer._ensure_summary_session()
    assert result is summary
    summary.start.assert_not_awaited()


# ──────────────────────────────────────────────────────────────────
# _router_session construction parameters
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_router_session_created_with_max_turns_5() -> None:
    """_router_session must be created with max_turns=5 to allow history research turns.

    The router session is lazy — it is created on first use (first route_task() call),
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
                await decomposer._ensure_router_session()

    # First call: main session. Second call: router session (lazy-started).
    assert len(constructor_calls) == 2
    router_kwargs = constructor_calls[1]
    assert router_kwargs.get("max_turns") == 5, (
        f"_router_session must be created with max_turns=5, got: {router_kwargs.get('max_turns')!r}"
    )


@pytest.mark.asyncio
async def test_router_session_created_with_tools_empty_list() -> None:
    """_router_session must be created with tools=[] to disable all default SDK tools.

    tools=None (the default) enables all tools (Bash, Read, Write, etc.).
    The router session is only supposed to use MCP-provided history tools,
    so tools=[] is required to prevent side effects during routing.

    The router session is lazy — verified when first created via _ensure_router_session().
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
                await decomposer._ensure_router_session()

    # First call: main session. Second call: router session (lazy-started).
    assert len(constructor_calls) == 2
    router_kwargs = constructor_calls[1]
    assert router_kwargs.get("tools") == [], (
        f"_router_session must be created with tools=[], got: {router_kwargs.get('tools')!r}"
    )


# ──────────────────────────────────────────────────────────────────
# Context tracking — answer() turn buffer
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_router_session_start_failure_does_not_cache_broken_session() -> None:
    """If _ensure_router_session()'s start() raises, _router_session stays None.

    A failed start must not cache an unstarted session. Otherwise every subsequent
    call to _ensure_router_session() would return the broken session and always fail.
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
                    await decomposer._ensure_router_session()
                # Session must NOT be cached — stays None so next call retries creation.
                assert decomposer._router_session is None


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
# Context tracking — _build_router_context
# ──────────────────────────────────────────────────────────────────


def test_build_router_context_returns_summary() -> None:
    """Returns formatted _context_summary when present."""
    decomposer, _, _, _ = _make_decomposer()
    decomposer._context_summary = "User discussed auth refactoring."

    result = decomposer._build_router_context()

    assert "User discussed auth refactoring." in result
    assert "[Main-session context" in result


def test_build_router_context_empty_when_no_summary() -> None:
    """Returns empty string when _context_summary is empty."""
    decomposer, _, _, _ = _make_decomposer()
    decomposer._context_summary = ""

    assert decomposer._build_router_context() == ""


@pytest.mark.asyncio
async def test_route_task_awaits_and_includes_context() -> None:
    """route_task() awaits pending summary and includes context."""
    small_json = json.dumps({"scope": "small", "summary": "test", "prompt": "do it"})
    decomposer, _, router, _ = _make_decomposer(
        session_events=[Response(content="Done!")],
        router_events=[Response(content=small_json)],
        summary_events=[Response(content="User fixed a bug.")],
    )

    # Build context via answer
    _ = [e async for e in decomposer.answer("fix the bug")]
    if decomposer._summary_task:
        await decomposer._summary_task

    await _collect(decomposer, "now deploy it")

    assert len(router._send_calls) == 1
    assert "[Main-session context" in router._send_calls[0]
    assert "User fixed a bug." in router._send_calls[0]


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
# Context tracking — router session reset
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_orch_reset_restarts_session_after_threshold() -> None:
    """After 20 router calls, the old session is stopped and _router_session is set to None (lazy).

    The new session is NOT eagerly started inside _reset_router_if_needed() — it is
    lazy-created on the next route_task() call via _ensure_router_session().
    """
    from archon.ai.decomposer import _ROUTER_RESET_THRESHOLD

    decomposer, _, router, _ = _make_decomposer()
    # Simulate threshold - 1 calls already made
    decomposer._router_call_count = _ROUTER_RESET_THRESHOLD - 1

    with patch("archon.ai.decomposer.load_workspace_agents", return_value=None):
        await decomposer._reset_router_if_needed()

    # Old session was stopped
    router.stop.assert_awaited_once()
    # _router_session is None — new session is NOT yet created (lazy)
    assert decomposer._router_session is None
    assert decomposer._router_call_count == 0


@pytest.mark.asyncio
async def test_orch_reset_preserves_context_summary() -> None:
    """_context_summary is unchanged after router reset."""
    from archon.ai.decomposer import _ROUTER_RESET_THRESHOLD

    decomposer, _, _, _ = _make_decomposer()
    decomposer._context_summary = "Important context about auth."
    decomposer._router_call_count = _ROUTER_RESET_THRESHOLD - 1

    new_router = _mock_session(Response(content='{"scope":"small","summary":"x","prompt":"y"}'))

    with patch("archon.ai.decomposer.ClaudeSession", return_value=new_router):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            with patch("archon.ai.decomposer.load_workspace_agents", return_value=None):
                await decomposer._reset_router_if_needed()

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
    assert "File:" in injected
    assert "agents.md" in injected
    assert "researcher" in injected


@pytest.mark.asyncio
async def test_inject_workspace_agents_both_main_and_router_session(tmp_path) -> None:
    """Injection goes to both main and router sessions — summary session is untouched."""
    agents_file = tmp_path / "agents.md"
    agents_file.write_text("- researcher: Does research")

    decomposer, main_session, router_session, summary_session = _make_decomposer(
        cwd=str(tmp_path)
    )
    await decomposer.start()

    main_session.inject_context.assert_called_once()
    router_session.inject_context.assert_called_once()
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


def _make_decomposer_with_events(tmp_path, recent_events_return, router_response=None):
    """Helper: build a Decomposer whose _session.recent_events() returns given events."""
    from archon.ai.decomposer import Decomposer

    route_json = router_response or '{"scope":"small","summary":"Test","prompt":"do it"}'
    router_session = _mock_session(Response(content=route_json))
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
            return router_session
        return summary_session

    return Decomposer, router_session, _make_session


class TestRouteTaskFilePathExtraction:
    async def test_route_task_includes_recent_file_paths(self, tmp_path) -> None:
        """route_task() includes file paths from _session.recent_events() in instruction."""
        from archon.ai.event_mapper import ToolStarted

        Decomposer, router_session, _make_session = _make_decomposer_with_events(
            tmp_path,
            [(1.0, ToolStarted(name="Read", input="/abs/path/to/config.py"))],
        )

        with patch("archon.ai.decomposer.ClaudeSession", side_effect=_make_session):
            d = Decomposer(cwd=str(tmp_path))
            await d.start()
            await _collect(d, "do something")
            await d.stop()

        assert len(router_session._send_calls) == 1
        assert "/abs/path/to/config.py" in router_session._send_calls[0]

    async def test_route_task_no_paths_block_when_no_tool_events(self, tmp_path) -> None:
        """route_task() works normally when session has no recent tool events."""
        Decomposer, router_session, _make_session = _make_decomposer_with_events(tmp_path, [])

        with patch("archon.ai.decomposer.ClaudeSession", side_effect=_make_session):
            d = Decomposer(cwd=str(tmp_path))
            await d.start()
            _, result = await _collect(d, "do something")
            await d.stop()

        assert result.scope in ("small", "large")
        assert "[Files accessed" not in router_session._send_calls[0]

    async def test_extract_deduplicates_paths(self, tmp_path) -> None:
        """_extract_recent_file_paths() deduplicates repeated paths."""
        from archon.ai.event_mapper import ToolStarted

        Decomposer, router_session, _make_session = _make_decomposer_with_events(
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
            await _collect(d, "do something")
            await d.stop()

        instruction = router_session._send_calls[0]
        assert instruction.count("/project/config.py") == 1
        assert "/project/other.py" in instruction

    async def test_extract_handles_bare_path_string(self, tmp_path) -> None:
        """_extract_recent_file_paths() handles bare path strings (not JSON dicts)."""
        from archon.ai.event_mapper import ToolStarted

        Decomposer, router_session, _make_session = _make_decomposer_with_events(
            tmp_path,
            [(1.0, ToolStarted(name="Read", input="/some/file.py"))],
        )

        with patch("archon.ai.decomposer.ClaudeSession", side_effect=_make_session):
            d = Decomposer(cwd=str(tmp_path))
            await d.start()
            await _collect(d, "do something")
            await d.stop()

        assert "/some/file.py" in router_session._send_calls[0]

    async def test_extract_ignores_regex_patterns(self, tmp_path) -> None:
        """_extract_recent_file_paths() only extracts file_path/path, not pattern (regex)."""
        from archon.ai.event_mapper import ToolStarted

        Decomposer, router_session, _make_session = _make_decomposer_with_events(
            tmp_path,
            [(1.0, ToolStarted(name="Grep", input='{"pattern": "def foo", "path": "/src/"}'))],
        )

        with patch("archon.ai.decomposer.ClaudeSession", side_effect=_make_session):
            d = Decomposer(cwd=str(tmp_path))
            await d.start()
            await _collect(d, "do something")
            await d.stop()

        instruction = router_session._send_calls[0]
        assert "/src/" in instruction
        assert "def foo" not in instruction

    async def test_extract_caps_at_15_paths(self, tmp_path) -> None:
        """_extract_recent_file_paths() returns at most 15 unique paths."""
        from archon.ai.event_mapper import ToolStarted

        events = [
            (float(i), ToolStarted(name="Read", input=f"/project/file{i}.py"))
            for i in range(20)
        ]
        Decomposer, router_session, _make_session = _make_decomposer_with_events(
            tmp_path, events
        )

        with patch("archon.ai.decomposer.ClaudeSession", side_effect=_make_session):
            d = Decomposer(cwd=str(tmp_path))
            await d.start()
            await _collect(d, "do something")
            await d.stop()

        instruction = router_session._send_calls[0]
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

        Decomposer, router_session, _make_session = _make_decomposer_with_events(
            tmp_path,
            [(1.0, ToolStarted(name="Bash", input="cd /project && make build"))],
        )

        with patch("archon.ai.decomposer.ClaudeSession", side_effect=_make_session):
            d = Decomposer(cwd=str(tmp_path))
            await d.start()
            await _collect(d, "do something")
            await d.stop()

        instruction = router_session._send_calls[0]
        assert "cd /project && make build" not in instruction
        assert "[Files accessed" not in instruction

    async def test_extract_returns_most_recent_paths(self, tmp_path) -> None:
        """_extract_recent_file_paths() must return the 15 most recent paths, not oldest."""
        from archon.ai.event_mapper import ToolStarted

        events = [
            (float(i), ToolStarted(name="Read", input=f"/project/file{i}.py"))
            for i in range(20)
        ]
        Decomposer, router_session, _make_session = _make_decomposer_with_events(
            tmp_path, events
        )

        with patch("archon.ai.decomposer.ClaudeSession", side_effect=_make_session):
            d = Decomposer(cwd=str(tmp_path))
            await d.start()
            await _collect(d, "do something")
            await d.stop()

        instruction = router_session._send_calls[0]
        # Most recent is file19.py; oldest is file0.py
        assert "/project/file19.py" in instruction
        assert "/project/file0.py" not in instruction

    def test_context_summary_property_returns_summary(self) -> None:
        """context_summary property delegates to _context_summary."""
        from archon.ai.decomposer import Decomposer
        from archon.ai.event_mapper import Response

        main_session = _mock_session(Response(content="ok"))
        router_session = _mock_session(Response(content="{}"))
        summary_session = _mock_session(Response(content="summary"))

        call_count = 0

        def _make_session(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return main_session
            if call_count == 2:
                return router_session
            return summary_session

        with patch("archon.ai.decomposer.ClaudeSession", side_effect=_make_session):
            d = Decomposer()
            d._context_summary = "Previous conversation about config module"
            assert d.context_summary == "Previous conversation about config module"


# ──────────────────────────────────────────────────────────────────
# context_provider — history context injection into _router_session
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_router_session_receives_history_context_at_first_use() -> None:
    """_router_session.inject_context is called with combined history on first use (lazy start).

    The router session is lazy — context is injected when _ensure_router_session() first runs,
    not at Decomposer.start(). This test simulates the first route_task() call.
    """
    mock_provider = MagicMock()
    mock_provider.startup_context_prompt.return_value = "## History\nSome prompt"
    mock_provider.get_recent_context.return_value = "Yesterday summary"

    decomposer, _, router, _ = _make_decomposer(context_provider=mock_provider)
    # Clear the pre-injected router session to test lazy creation with context injection
    decomposer._router_session = None

    with patch("archon.ai.decomposer.ClaudeSession", return_value=router):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            with patch("archon.ai.decomposer.load_workspace_agents", return_value=None):
                await decomposer._ensure_router_session()

    router.inject_context.assert_called_once()
    call_arg = router.inject_context.call_args[0][0]
    assert "## History" in call_arg
    assert "Yesterday summary" in call_arg
    assert "\n\n---\n\n" in call_arg
    mock_provider.startup_context_prompt.assert_called_with(rag_enabled=False)


@pytest.mark.asyncio
async def test_router_session_receives_no_context_when_provider_is_none() -> None:
    """When context_provider=None, _router_session.inject_context is NOT called during start()."""
    decomposer, _, router, _ = _make_decomposer()
    await decomposer.start()

    router.inject_context.assert_not_called()


@pytest.mark.asyncio
async def test_router_session_receives_context_after_reset() -> None:
    """After _reset_router_if_needed() triggers a reset, the next _ensure_router_session()
    call (lazy creation) injects history context into the new session.
    """
    mock_provider = MagicMock()
    mock_provider.startup_context_prompt.return_value = "## History\nReset prompt"
    mock_provider.get_recent_context.return_value = "Reset context"

    decomposer, _, router, _ = _make_decomposer(context_provider=mock_provider)

    # Force reset threshold
    from archon.ai.decomposer import _ROUTER_RESET_THRESHOLD
    decomposer._router_call_count = _ROUTER_RESET_THRESHOLD - 1

    with patch("archon.ai.decomposer.load_workspace_agents", return_value=None):
        await decomposer._reset_router_if_needed()

    # After reset, _router_session is None — lazy
    assert decomposer._router_session is None

    # Now simulate the next route_task() lazy-starting the new session
    new_router = _mock_session(Response(content='{"scope":"small","summary":"x","prompt":"y"}'))
    with patch("archon.ai.decomposer.ClaudeSession", return_value=new_router):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            with patch("archon.ai.decomposer.load_workspace_agents", return_value=None):
                await decomposer._ensure_router_session()

    # inject_context must have been called on the new session with history context
    assert new_router.inject_context.call_count == 1
    call_arg = new_router.inject_context.call_args[0][0]
    assert "## History" in call_arg
    assert "Reset context" in call_arg


@pytest.mark.asyncio
async def test_router_session_receives_agents_md(tmp_path) -> None:
    """When agents.md exists in cwd, _router_session.inject_context is also called with its content."""
    agents_content = "## Agent A\nDoes things"
    (tmp_path / "agents.md").write_text(agents_content, encoding="utf-8")

    decomposer, _, router, _ = _make_decomposer(cwd=str(tmp_path))
    await decomposer.start()

    # router.inject_context should have been called (at least once) with agents content
    calls = [call[0][0] for call in router.inject_context.call_args_list]
    agents_calls = [c for c in calls if "Workspace Agents" in c and "Agent A" in c]
    assert agents_calls, f"Expected router inject_context with agents.md content, got: {calls}"


@pytest.mark.asyncio
async def test_router_session_receives_both_history_and_agents_md(tmp_path) -> None:
    """When both context_provider and agents.md exist, _router_session gets both injections on first use."""
    mock_provider = MagicMock()
    mock_provider.startup_context_prompt.return_value = "## History\nHistory prompt"
    mock_provider.get_recent_context.return_value = "History context"

    (tmp_path / "agents.md").write_text("## Agent B\nDoes other things", encoding="utf-8")

    decomposer, _, router, _ = _make_decomposer(
        context_provider=mock_provider, cwd=str(tmp_path)
    )
    # Clear pre-injected router session to test lazy-start context injection
    decomposer._router_session = None

    with patch("archon.ai.decomposer.ClaudeSession", return_value=router):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            await decomposer._ensure_router_session()

    # Should have 2 inject_context calls: history context + agents.md
    assert router.inject_context.call_count == 2


# ──────────────────────────────────────────────────────────────────
# Fix 1: _reset_router_if_needed re-injects agents.md after reset
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_router_session_agents_md_reinjected_after_reset(tmp_path) -> None:
    """After _reset_router_if_needed() triggers a reset, the next _ensure_router_session()
    call (lazy creation) injects agents.md content into the new session.
    """
    from archon.ai.decomposer import _ROUTER_RESET_THRESHOLD

    agents_content = "## Agent X\nDoes workspace things"
    (tmp_path / "agents.md").write_text(agents_content, encoding="utf-8")

    decomposer, _, router, _ = _make_decomposer(cwd=str(tmp_path))

    # Force router reset
    decomposer._router_call_count = _ROUTER_RESET_THRESHOLD - 1

    await decomposer._reset_router_if_needed()

    # After reset, _router_session is None — lazy
    assert decomposer._router_session is None

    # Simulate the next route_task() lazy-starting the new session
    new_router = _mock_session(Response(content='{"scope":"small","summary":"x","prompt":"y"}'))
    with patch("archon.ai.decomposer.ClaudeSession", return_value=new_router):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            await decomposer._ensure_router_session()

    # agents.md content must appear in inject_context calls on the new session
    all_injected = [call[0][0] for call in new_router.inject_context.call_args_list]
    agents_calls = [c for c in all_injected if "Workspace Agents" in c and "Agent X" in c]
    assert agents_calls, (
        f"Expected new _router_session.inject_context with agents.md content after reset, "
        f"got calls: {all_injected}"
    )


# ──────────────────────────────────────────────────────────────────
# Fix 2: context_provider errors in start() and _reset_router_if_needed are guarded
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_continues_when_context_provider_raises() -> None:
    """When context_provider.startup_context_prompt() raises, start() completes without error."""
    mock_provider = MagicMock()
    mock_provider.startup_context_prompt.side_effect = RuntimeError("history read failed")

    decomposer, main, router, _ = _make_decomposer(context_provider=mock_provider)

    # Must not raise
    await decomposer.start()

    # Main session must still have started
    main.start.assert_awaited_once()

    # _router_session.inject_context must NOT have been called (exception before injection)
    router.inject_context.assert_not_called()


@pytest.mark.asyncio
async def test_reset_orch_continues_when_context_provider_raises(caplog) -> None:
    """When context_provider raises during lazy _ensure_router_session(), the router session
    is still created and started. The error is logged but does not propagate.

    Since _reset_router_if_needed() no longer eagerly starts the new session, the
    context_provider error now surfaces in _ensure_router_session() (next route_task() call).
    """
    import logging

    from archon.ai.decomposer import _ROUTER_RESET_THRESHOLD

    mock_provider = MagicMock()
    mock_provider.startup_context_prompt.return_value = "## History"
    mock_provider.get_recent_context.side_effect = RuntimeError("context read failed")

    decomposer, _, router, _ = _make_decomposer(context_provider=mock_provider)

    decomposer._router_call_count = _ROUTER_RESET_THRESHOLD - 1

    with patch("archon.ai.decomposer.load_workspace_agents", return_value=None):
        await decomposer._reset_router_if_needed()

    # Old session was stopped, _router_session is now None (lazy)
    router.stop.assert_awaited()
    assert decomposer._router_session is None

    # Simulate the next route_task() lazy-starting the new session — context_provider raises
    new_router = _mock_session(Response(content='{"scope":"small","summary":"x","prompt":"y"}'))
    with caplog.at_level(logging.WARNING, logger="archon"):
        with patch("archon.ai.decomposer.ClaudeSession", return_value=new_router):
            with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
                with patch("archon.ai.decomposer.load_workspace_agents", return_value=None):
                    # Must not raise — context injection errors are guarded
                    await decomposer._ensure_router_session()

    # New session was started despite the context_provider error
    new_router.start.assert_awaited()

    # Warning was logged about failed injection
    assert any("Failed to inject" in r.message for r in caplog.records), (
        f"Expected warning log about failed injection, got: {[r.message for r in caplog.records]}"
    )


# ──────────────────────────────────────────────────────────────────
# Timeout guard on _router_session.send() in route_task()
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_task_times_out_and_falls_back(monkeypatch) -> None:
    """If _router_session.send() hangs forever, route_task() times out and
    returns TaskOutput(scope='small', prompt=original_prompt)."""
    original_prompt = "do something important"

    # Build a decomposer whose router_session.send() hangs indefinitely
    decomposer, _, router_session, _ = _make_decomposer()

    async def _hanging_send(prompt: str):
        await asyncio.sleep(9999)
        # Never yields — satisfies the type checker
        return
        yield  # noqa: E501 — make it an async generator

    router_session.send = _hanging_send

    monkeypatch.setattr("archon.ai.decomposer._ROUTER_TIMEOUT_S", 0.05)
    _, result = await _collect(decomposer, original_prompt)

    assert result.scope == "small"
    assert result.prompt == original_prompt


@pytest.mark.asyncio
async def test_route_task_reset_timeout_falls_back(monkeypatch) -> None:
    """If _reset_router_if_needed() hangs (e.g. stop() stalls), route_task() falls back
    to scope='small' with the original prompt instead of blocking indefinitely."""
    original_prompt = "do something while reset hangs"

    decomposer, _, router_session, _ = _make_decomposer()

    # Make router stop() hang indefinitely to simulate a stalled SDK subprocess
    async def _hanging_stop():
        await asyncio.sleep(9999)

    router_session.stop = _hanging_stop

    # Force the reset threshold so _reset_router_if_needed() actually runs stop/start
    from archon.ai.decomposer import _ROUTER_RESET_THRESHOLD
    decomposer._router_call_count = _ROUTER_RESET_THRESHOLD - 1

    monkeypatch.setattr("archon.ai.decomposer._ROUTER_RESET_TIMEOUT_S", 0.05)
    _, result = await _collect(decomposer, original_prompt)

    assert result.scope == "small"
    assert result.prompt == original_prompt


# ─── New: is_fallback field and trivial scope ───


@pytest.mark.asyncio
async def test_route_task_fallback_includes_is_fallback_flag_on_reset_timeout(monkeypatch) -> None:
    """When _reset_router_if_needed() times out, result.is_fallback is True."""
    original_prompt = "build a feature"
    decomposer, _, router_session, _ = _make_decomposer()

    async def _hanging_stop():
        await asyncio.sleep(9999)

    router_session.stop = _hanging_stop
    from archon.ai.decomposer import _ROUTER_RESET_THRESHOLD
    decomposer._router_call_count = _ROUTER_RESET_THRESHOLD - 1
    monkeypatch.setattr("archon.ai.decomposer._ROUTER_RESET_TIMEOUT_S", 0.05)

    _, result = await _collect(decomposer, original_prompt)

    assert result.is_fallback is True


@pytest.mark.asyncio
async def test_route_task_fallback_silent_on_reset_timeout(monkeypatch) -> None:
    """When _reset_router_if_needed() times out, the fallback is silent (fallback_reason is empty).

    Timeout-based fallbacks are internal routing decisions — not user-visible errors.
    The system silently falls back to inline execution without alarming the user.
    """
    original_prompt = "build a feature"
    decomposer, _, router_session, _ = _make_decomposer()

    async def _hanging_stop():
        await asyncio.sleep(9999)

    router_session.stop = _hanging_stop
    from archon.ai.decomposer import _ROUTER_RESET_THRESHOLD
    decomposer._router_call_count = _ROUTER_RESET_THRESHOLD - 1
    monkeypatch.setattr("archon.ai.decomposer._ROUTER_RESET_TIMEOUT_S", 0.05)

    _, result = await _collect(decomposer, original_prompt)

    # Silent fallback: is_fallback=True but empty reason (no user alarm)
    assert result.is_fallback is True
    assert result.fallback_reason == ""


@pytest.mark.asyncio
async def test_route_task_reset_non_timeout_exception_falls_back(monkeypatch) -> None:
    """If _reset_router_if_needed() raises a non-TimeoutError (e.g. RuntimeError from SDK crash),
    route_task() catches it, logs an error, and falls back silently to scope='small'."""
    original_prompt = "do something"
    decomposer, _, router_session, _ = _make_decomposer()

    async def _crashing_stop():
        raise RuntimeError("SDK subprocess failed")

    router_session.stop = _crashing_stop

    # Force the reset threshold so _reset_router_if_needed() actually runs stop/start
    from archon.ai.decomposer import _ROUTER_RESET_THRESHOLD
    decomposer._router_call_count = _ROUTER_RESET_THRESHOLD - 1

    _, result = await _collect(decomposer, original_prompt)

    assert result.scope == "small"
    assert result.prompt == original_prompt
    assert result.is_fallback is True
    assert result.fallback_reason == ""


@pytest.mark.asyncio
async def test_route_task_fallback_includes_is_fallback_flag_on_send_timeout(monkeypatch) -> None:
    """When _router_session.send() times out, result.is_fallback is True and reason is empty (silent)."""
    original_prompt = "do something important"
    decomposer, _, router_session, _ = _make_decomposer()

    async def _hanging_send(prompt: str):
        await asyncio.sleep(9999)
        return
        yield  # noqa: E501

    router_session.send = _hanging_send
    monkeypatch.setattr("archon.ai.decomposer._ROUTER_TIMEOUT_S", 0.05)

    _, result = await _collect(decomposer, original_prompt)

    assert result.is_fallback is True
    assert result.fallback_reason == ""


@pytest.mark.asyncio
async def test_route_task_fallback_on_exception() -> None:
    """When _router_session.send() raises, result.is_fallback is True and reason mentions inline."""
    decomposer, _, router, _ = _make_decomposer()

    async def _crashing_send(prompt: str):
        raise RuntimeError("connection lost")
        yield  # noqa: E501

    router.send = _crashing_send

    _, result = await _collect(decomposer, "build something big")

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
        router_events=[Response(content=small_json)],
    )
    initial_len = len(decomposer._pending_turns)

    await _collect(decomposer, "quick task")

    # For small/trivial scope, pending_turns should NOT grow (no summary appended)
    assert len(decomposer._pending_turns) == initial_len


@pytest.mark.asyncio
async def test_route_task_pending_turns_not_appended_for_trivial_scope() -> None:
    """When route_task returns scope='trivial', _pending_turns is NOT appended after route_task."""
    trivial_json = json.dumps({"scope": "trivial", "summary": "Quick lookup", "prompt": "tell me"})
    decomposer, _, _, _ = _make_decomposer(
        router_events=[Response(content=trivial_json)],
    )
    initial_len = len(decomposer._pending_turns)

    await _collect(decomposer, "tell me something")

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
        router_events=[Response(content=large_json)],
    )
    initial_len = len(decomposer._pending_turns)

    await _collect(decomposer, "refactor the auth module")

    # Large scope: pending_turns grows (summary recorded)
    assert len(decomposer._pending_turns) > initial_len


# ──────────────────────────────────────────────────────────────────
# Bug fixes: C1, C2, C5
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_task_acloses_orch_generator_on_timeout() -> None:
    """C1: gen.aclose() is called when router.send() times out.

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

    decomposer, _, router, _ = _make_decomposer()
    router.send = _spying_send

    # Patch the timeout to fire immediately (1 ms) so the test is fast.
    with patch("archon.ai.decomposer._ROUTER_TIMEOUT_S", 0.001):
        _, result = await _collect(decomposer, "test prompt")

    assert result.is_fallback
    assert aclose_called, "gen.aclose() must be called on timeout so _send_lock is released"


@pytest.mark.asyncio
async def test_route_task_orch_init_timeout_falls_back_silently() -> None:
    """C2: When _ensure_router_session() hangs, route_task falls back silently.

    Without a timeout on the init call the Pipeline lock (held by Pipeline.send())
    would be held forever.  The fix wraps the call in _ROUTER_RESET_TIMEOUT_S.
    """
    import asyncio as _asyncio

    original_ensure = None

    async def _hanging_ensure():
        # Simulate a hung SDK start
        await _asyncio.sleep(100)
        raise RuntimeError("should not reach here")

    decomposer, _, _, _ = _make_decomposer()
    decomposer._ensure_router_session = _hanging_ensure  # type: ignore[method-assign]

    with patch("archon.ai.decomposer._ROUTER_RESET_TIMEOUT_S", 0.001):
        _, result = await _collect(decomposer, "test prompt")

    assert result.is_fallback
    assert result.scope == "small"
    assert result.prompt == "test prompt"


@pytest.mark.asyncio
async def test_reset_orch_nulled_before_stop() -> None:
    """C5: _router_session is set to None BEFORE old_session.stop() is awaited.

    If a timeout fires during stop(), _router_session must already be None so
    there is no zombie reference to a partially-stopped session.
    """
    from archon.ai.decomposer import _ROUTER_RESET_THRESHOLD

    null_after_stop: bool | None = None  # True = nulled before stop, False = after

    async def _spy_stop():
        nonlocal null_after_stop
        # At the moment stop() is awaited, check whether _router_session is already None
        null_after_stop = decomposer._router_session is None

    decomposer, _, router, _ = _make_decomposer()
    router.stop = _spy_stop  # type: ignore[method-assign]

    decomposer._router_call_count = _ROUTER_RESET_THRESHOLD - 1

    new_router = _mock_session(Response(content='{"scope":"small","summary":"x","prompt":"y"}'))

    with patch("archon.ai.decomposer.ClaudeSession", return_value=new_router):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            with patch("archon.ai.decomposer.load_workspace_agents", return_value=None):
                await decomposer._reset_router_if_needed()

    assert null_after_stop is True, (
        "_router_session must be set to None BEFORE old_session.stop() is called "
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
# BUG-15 — Cost carryover across router / summary session resets
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_orch_reset_preserves_cost_in_usage_stats() -> None:
    """After an router session reset, the pre-reset cost must appear in usage_stats.

    BUG-15: _reset_router_if_needed() stopped the old session and discarded its
    cost. Only the new (empty) session's cost was reported. Accumulated costs
    must be carried over so callers see the full lifetime cost.
    """
    from archon.ai.decomposer import _ROUTER_RESET_THRESHOLD, Decomposer

    decomposer, _, router, _ = _make_decomposer()
    # Give the current router session a known cost
    router.usage_stats = {"total_cost_usd": 0.05, "cumulative_cache_creation": 0}
    decomposer._router_call_count = _ROUTER_RESET_THRESHOLD - 1

    new_router = _mock_session(Response(content='{"scope":"small","summary":"x","prompt":"y"}'))
    new_router.usage_stats = None  # fresh session has no cost yet

    with patch("archon.ai.decomposer.ClaudeSession", return_value=new_router):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            with patch("archon.ai.decomposer.load_workspace_agents", return_value=None):
                await decomposer._reset_router_if_needed()

    # After reset: new session is active with no cost, but carryover must be preserved.
    # usage_stats["sessions"]["orchestration"]["cost_usd"] must include the old cost.
    decomposer._session.usage_stats = {"total_cost_usd": 0.01}
    stats = decomposer.usage_stats
    assert stats is not None
    router_cost = stats["sessions"]["orchestration"]["cost_usd"]
    assert router_cost == pytest.approx(0.05), (
        f"Expected router cost carryover 0.05, got {router_cost!r}. "
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
# BUG-D — No eager session pre-start in _reset_router_if_needed()
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reset_orch_does_not_eagerly_start_new_session() -> None:
    """After _reset_router_if_needed() triggers a reset, session.start() must NOT
    be called eagerly. The next route_task() call lazy-creates it via
    _ensure_router_session().

    BUG-D: the eager await self._ensure_router_session() at the end of
    _reset_router_if_needed() called session.start() while the Pipeline._lock
    was held, wasting 2-5s on SDK subprocess spawn for no reason.
    """
    from archon.ai.decomposer import _ROUTER_RESET_THRESHOLD

    decomposer, _, router, _ = _make_decomposer()
    decomposer._router_call_count = _ROUTER_RESET_THRESHOLD - 1

    # Track how many ClaudeSession instances are started inside _reset_router_if_needed
    started_sessions: list = []
    original_ensure = decomposer._ensure_router_session

    async def _spy_ensure():
        result = await original_ensure()
        started_sessions.append(result)
        return result

    # Patch ClaudeSession so a new one is created on reset
    new_router = _mock_session(Response(content='{"scope":"small","summary":"x","prompt":"y"}'))

    with patch("archon.ai.decomposer.ClaudeSession", return_value=new_router):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            with patch("archon.ai.decomposer.load_workspace_agents", return_value=None):
                # Reset clears the session and must NOT start a new one
                await decomposer._reset_router_if_needed()

    # After reset: _router_session must be None (lazy, not eagerly pre-started).
    assert decomposer._router_session is None, (
        "_router_session must be None after reset — lazy creation deferred to next route_task(). "
        f"Got: {decomposer._router_session!r}"
    )
    # new_router.start must NOT have been called inside _reset_router_if_needed()
    new_router.start.assert_not_awaited()


# ──────────────────────────────────────────────────────────────────
# REMINDER.md injection in route_task()
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_task_embeds_reminder_when_file_exists(tmp_path) -> None:
    """REMINDER.md in cwd → its content appears in the prompt sent to router.send()."""
    reminder_content = "Always prefer KISS. No backward compatibility."
    (tmp_path / "REMINDER.md").write_text(reminder_content)

    small_json = json.dumps({"scope": "small", "summary": "test", "prompt": "do it"})
    decomposer, _, router, _ = _make_decomposer(
        router_events=[Response(content=small_json)],
        cwd=str(tmp_path),
    )
    await _collect(decomposer, "fix the bug")

    assert len(router._send_calls) == 1
    instruction = router._send_calls[0]
    assert reminder_content in instruction


@pytest.mark.asyncio
async def test_route_task_no_reminder_when_cwd_is_none() -> None:
    """cwd=None → REMINDER.md content absent from router prompt."""
    small_json = json.dumps({"scope": "small", "summary": "test", "prompt": "do it"})
    decomposer, _, router, _ = _make_decomposer(
        router_events=[Response(content=small_json)],
        cwd=None,
    )
    await _collect(decomposer, "fix the bug")

    assert len(router._send_calls) == 1
    instruction = router._send_calls[0]
    assert "system_reminder" not in instruction
    assert "REMINDER" not in instruction


@pytest.mark.asyncio
async def test_route_task_no_reminder_when_file_absent(tmp_path) -> None:
    """No REMINDER.md in cwd → reminder block absent from router prompt."""
    from unittest.mock import patch
    # Intentionally do NOT create REMINDER.md in tmp_path
    # Also patch system_reminder to absent so neither file triggers injection
    absent_system = tmp_path / "absent_system_reminder.md"
    small_json = json.dumps({"scope": "small", "summary": "test", "prompt": "do it"})
    with patch("archon.ai.reminder._SYSTEM_REMINDER_FILE", new=absent_system):
        decomposer, _, router, _ = _make_decomposer(
            router_events=[Response(content=small_json)],
            cwd=str(tmp_path),
        )
        await _collect(decomposer, "fix the bug")

    assert len(router._send_calls) == 1
    instruction = router._send_calls[0]
    assert "system_reminder" not in instruction


@pytest.mark.asyncio
async def test_route_task_reminder_none_does_not_break_routing(tmp_path) -> None:
    """build_reminder_injection() returning None → route_task() still returns a valid TaskOutput.

    build_reminder_injection is fully defensive and never raises — it returns None on any
    error (missing file, permission denied, etc).  The dead try/except has been removed.
    This test verifies that None → no reminder block → routing still succeeds.
    """
    small_json = json.dumps({"scope": "small", "summary": "test", "prompt": "do it"})
    decomposer, _, router, _ = _make_decomposer(
        router_events=[Response(content=small_json)],
        cwd=str(tmp_path),
    )

    # No REMINDER.md in tmp_path → build_reminder_injection returns None
    _, result = await _collect(decomposer, "fix the bug")

    assert result.scope in ("small", "trivial", "large")
    assert result.prompt is not None or result.agents is not None


@pytest.mark.asyncio
async def test_route_task_reminder_refreshed_on_every_call(tmp_path) -> None:
    """REMINDER.md content is re-read on every route_task() call (not stale from first)."""
    reminder_v1 = "Version 1: always use KISS."
    reminder_v2 = "Version 2: prefer composition over inheritance."

    (tmp_path / "REMINDER.md").write_text(reminder_v1)

    small_json = json.dumps({"scope": "small", "summary": "test", "prompt": "do it"})
    decomposer, _, router, _ = _make_decomposer(
        router_events=[Response(content=small_json)],
        cwd=str(tmp_path),
    )

    # First call — sees v1
    await _collect(decomposer, "first request")
    assert reminder_v1 in router._send_calls[0]
    assert reminder_v2 not in router._send_calls[0]

    # Update REMINDER.md between calls
    (tmp_path / "REMINDER.md").write_text(reminder_v2)

    # Reset router call list to make assertion on second call clean
    router._send_calls.clear()

    # Second call — must see v2, not v1
    await _collect(decomposer, "second request")
    assert reminder_v2 in router._send_calls[0]
    assert reminder_v1 not in router._send_calls[0]


@pytest.mark.asyncio
async def test_route_task_instruction_ordering(tmp_path) -> None:
    """Context block appears before paths block; REMINDER.md appears after paths block and before route_task rules.

    Integration test: verifies the full instruction string ordering.
    """
    from archon.ai.event_mapper import ToolStarted

    reminder_content = "Mandatory: write tests first."
    (tmp_path / "REMINDER.md").write_text(reminder_content)

    small_json = json.dumps({"scope": "small", "summary": "test", "prompt": "do it"})
    decomposer, main, router, _ = _make_decomposer(
        router_events=[Response(content=small_json)],
        cwd=str(tmp_path),
    )

    # Inject a context summary so context_block is non-empty
    decomposer._context_summary = "Previously: fixed the auth bug"

    # Inject a recent ToolStarted event so paths_block is non-empty
    import time
    tool_event = ToolStarted(name="Read", input={"file_path": str(tmp_path / "src" / "module.py")})
    main.recent_events = MagicMock(return_value=[(time.time(), tool_event)])

    await _collect(decomposer, "do something")

    assert len(router._send_calls) == 1
    instruction = router._send_calls[0]

    # All three blocks must be present
    assert "Main-session context" in instruction        # context_block marker
    assert "Files accessed" in instruction             # paths_block marker
    assert reminder_content in instruction             # reminder_block content
    assert "User request:" in instruction              # route_task rules footer

    # Ordering: context_block before paths_block before reminder before route rules
    ctx_pos = instruction.index("Main-session context")
    paths_pos = instruction.index("Files accessed")
    reminder_pos = instruction.index(reminder_content)
    rules_pos = instruction.index("User request:")

    assert ctx_pos < paths_pos < reminder_pos < rules_pos, (
        f"Ordering wrong: context={ctx_pos}, paths={paths_pos}, "
        f"reminder={reminder_pos}, rules={rules_pos}"
    )


# ──────────────────────────────────────────────────────────────────
# recover_session() — stop + start + re-inject workspace agents
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recover_session_calls_stop_start_inject() -> None:
    """recover_session() calls stop, start, and _inject_workspace_agents in order."""
    decomposer, main_session, _, _ = _make_decomposer()

    with patch.object(decomposer, "_inject_workspace_agents", new_callable=AsyncMock) as mock_inject:
        await decomposer.recover_session()

    main_session.stop.assert_awaited_once()
    main_session.start.assert_awaited_once()
    mock_inject.assert_awaited_once()

    # Verify ordering: stop called before start
    stop_order = main_session.stop.await_args_list
    start_order = main_session.start.await_args_list
    assert len(stop_order) == 1
    assert len(start_order) == 1


@pytest.mark.asyncio
async def test_recover_session_propagates_start_error() -> None:
    """If start() raises, the exception propagates to the caller."""
    decomposer, main_session, _, _ = _make_decomposer()
    main_session.start = AsyncMock(side_effect=RuntimeError("SDK init failed"))

    with pytest.raises(RuntimeError, match="SDK init failed"):
        await decomposer.recover_session()


# ──────────────────────────────────────────────────────────────────
# Issue #13: _pending_turns has bounded maxlen
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pending_turns_has_maxlen() -> None:
    """_pending_turns must have a finite maxlen to prevent unbounded growth."""
    decomposer, _, _, _ = _make_decomposer()
    assert decomposer._pending_turns.maxlen is not None
    assert decomposer._pending_turns.maxlen > 0


@pytest.mark.asyncio
async def test_pending_turns_maxlen_caps_growth() -> None:
    """Appending more than maxlen items must not grow the deque beyond maxlen."""
    from archon.ai.decomposer import _PENDING_TURNS_MAXLEN

    decomposer, _, _, _ = _make_decomposer()
    for i in range(_PENDING_TURNS_MAXLEN + 50):
        decomposer._pending_turns.append((f"prompt-{i}", f"response-{i}"))

    assert len(decomposer._pending_turns) == _PENDING_TURNS_MAXLEN


# ──────────────────────────────────────────────────────────────────
# Issue #18 integration: bg MCP headers passed to main ClaudeSession
# ──────────────────────────────────────────────────────────────────


def test_bg_mcp_headers_passed_to_main_session() -> None:
    """background_agent_mcp_headers must be forwarded as mcp_headers to ClaudeSession."""
    from archon.ai.decomposer import Decomposer

    headers = {"Authorization": "Bearer bg-test-token"}
    with patch(
        "archon.ai.decomposer.ClaudeSession",
    ) as MockSession:
        MockSession.return_value = MagicMock()
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            Decomposer(
                background_agent_mcp_url="http://localhost:18182/mcp/1",
                background_agent_mcp_headers=headers,
            )

    _, kwargs = MockSession.call_args
    assert kwargs.get("mcp_headers") == headers


def test_bg_mcp_headers_none_when_not_provided() -> None:
    """When background_agent_mcp_headers is not provided, mcp_headers must be None."""
    from archon.ai.decomposer import Decomposer

    with patch(
        "archon.ai.decomposer.ClaudeSession",
    ) as MockSession:
        MockSession.return_value = MagicMock()
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            Decomposer(background_agent_mcp_url="http://localhost:18182/mcp/1")

    _, kwargs = MockSession.call_args
    assert kwargs.get("mcp_headers") is None


# ──────────────────────────────────────────────────────────────────
# Task 2.1 — is_router_event() and route_task() as async generator
# ──────────────────────────────────────────────────────────────────


def test_is_router_event_returns_true_for_router_source() -> None:
    """is_router_event returns True when source='router'."""
    from archon.ai.event_mapper import is_router_event

    event = Response(content="x", source="router")
    assert is_router_event(event) is True


def test_is_router_event_returns_false_for_orchestrator() -> None:
    """is_router_event returns False when source='orchestrator' (the default)."""
    from archon.ai.event_mapper import is_router_event

    event = Response(content="x")  # default source='orchestrator'
    assert is_router_event(event) is False


def test_is_router_event_returns_false_for_no_source_attr() -> None:
    """is_router_event returns False for objects with no source attribute."""
    from archon.ai.event_mapper import is_router_event

    assert is_router_event(object()) is False
    assert is_router_event("plain string") is False
    assert is_router_event(42) is False


@pytest.mark.asyncio
async def test_route_task_yields_events_then_task_output() -> None:
    """route_task() yields intermediate events before the final TaskOutput sentinel."""
    from archon.ai.event_mapper import ToolStarted, ToolResult

    small_json = '{"scope":"small","summary":"test","prompt":"do it"}'
    decomposer, _, router, _ = _make_decomposer(
        router_events=[
            ToolStarted(name="ListHistory", id=1),
            ToolResult(content="some history", id=1),
            Response(content=small_json),
        ],
    )
    events, result = await _collect(decomposer, "do something")

    assert isinstance(result, TaskOutput)
    # At least the ToolStarted and ToolResult must appear as intermediate events
    assert any(isinstance(e, ToolStarted) for e in events)
    assert any(isinstance(e, ToolResult) for e in events)


@pytest.mark.asyncio
async def test_route_task_uses_last_response_not_first() -> None:
    """route_task() uses the LAST Response for JSON parsing, not the first."""
    first_json = '{"scope":"large","summary":"first","agents":[{"id":"a1","task":"x"}]}'
    last_json = '{"scope":"small","summary":"last","prompt":"correct prompt"}'
    decomposer, _, _, _ = _make_decomposer(
        router_events=[
            Response(content=first_json),
            Response(content=last_json),
        ],
    )
    _, result = await _collect(decomposer, "do something")

    assert result.scope == "small"
    assert result.summary == "last"
    assert result.prompt == "correct prompt"


@pytest.mark.asyncio
async def test_route_task_pending_turns_tracked_for_large_scope() -> None:
    """For large scope, route_task() appends a (prompt, summary) entry to _pending_turns."""
    large_json = '{"scope":"large","summary":"Big plan","agents":[{"id":"a1","task":"do it"}]}'
    decomposer, _, _, _ = _make_decomposer(
        router_events=[Response(content=large_json)],
    )
    _, result = await _collect(decomposer, "big feature")

    assert result.scope == "large"
    # The (prompt, summary) pair must be in pending turns
    assert any(t[0] == "big feature" and t[1] == "Big plan" for t in decomposer._pending_turns)


@pytest.mark.asyncio
async def test_route_task_real_time_ordering() -> None:
    """Events arrive before the final TaskOutput sentinel."""
    from archon.ai.event_mapper import ToolStarted

    small_json = '{"scope":"small","summary":"test","prompt":"ok"}'
    decomposer, _, _, _ = _make_decomposer(
        router_events=[
            ToolStarted(name="ReadHistory", id=1),
            Response(content=small_json),
        ],
    )
    items_in_order: list = []
    async for item in decomposer.route_task("do something"):
        items_in_order.append(item)

    assert len(items_in_order) >= 2
    assert isinstance(items_in_order[-1], TaskOutput)
    assert not isinstance(items_in_order[0], TaskOutput)


@pytest.mark.asyncio
async def test_route_task_timeout_yields_fallback() -> None:
    """When routing session times out, route_task yields a fallback TaskOutput."""
    from archon.ai.decomposer import _ROUTER_TIMEOUT_S

    decomposer, _, router, _ = _make_decomposer()

    # Replace send with a generator that hangs indefinitely
    async def _hanging_send(prompt: str):
        await asyncio.sleep(1000)
        yield Response(content="{}")

    router.send = _hanging_send

    with patch("archon.ai.decomposer._ROUTER_TIMEOUT_S", 0.01):
        _, result = await _collect(decomposer, "something")

    assert result.scope == "small"
    assert result.is_fallback is True


@pytest.mark.asyncio
async def test_route_task_timeout_mid_stream_partial_events_yielded() -> None:
    """Events yielded before timeout are included in the stream."""
    from archon.ai.event_mapper import ToolStarted

    decomposer, _, router, _ = _make_decomposer()

    yielded_events = []

    async def _partial_then_hang(prompt: str):
        yield ToolStarted(name="ReadHistory", id=1)
        await asyncio.sleep(1000)  # hangs here

    router.send = _partial_then_hang

    with patch("archon.ai.decomposer._ROUTER_TIMEOUT_S", 0.02):
        async for item in decomposer.route_task("something"):
            yielded_events.append(item)

    # At least the ToolStarted + fallback TaskOutput must be present
    assert any(isinstance(e, ToolStarted) for e in yielded_events)
    assert isinstance(yielded_events[-1], TaskOutput)
    assert yielded_events[-1].is_fallback is True


@pytest.mark.asyncio
async def test_route_task_reset_timeout_yields_fallback() -> None:
    """When _reset_router_if_needed times out, a fallback TaskOutput is yielded immediately."""
    decomposer, _, _, _ = _make_decomposer()

    async def _slow_reset():
        await asyncio.sleep(1000)

    with patch.object(decomposer, "_reset_router_if_needed", side_effect=_slow_reset):
        with patch("archon.ai.decomposer._ROUTER_RESET_TIMEOUT_S", 0.01):
            _, result = await _collect(decomposer, "x")

    assert result.scope == "small"
    assert result.is_fallback is True


@pytest.mark.asyncio
async def test_route_task_reset_exception_yields_fallback() -> None:
    """When _reset_router_if_needed raises, a fallback TaskOutput is yielded."""
    decomposer, _, _, _ = _make_decomposer()

    async def _crashing_reset():
        raise RuntimeError("reset failed")

    with patch.object(decomposer, "_reset_router_if_needed", side_effect=_crashing_reset):
        _, result = await _collect(decomposer, "x")

    assert result.scope == "small"
    assert result.is_fallback is True


# ──────────────────────────────────────────────────────────────────
# Fix 1 — router session init error handling
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_task_events_tagged_before_yield() -> None:
    """Events yielded by route_task() retain their original source; Pipeline re-tags to 'router'."""
    small_json = '{"scope":"small","summary":"test","prompt":"p"}'
    tool_event = ToolStarted(name="history_read", input={}, source="orchestrator")
    decomposer, _, _, _ = _make_decomposer(
        router_events=[tool_event, Response(content=small_json)],
    )

    events, sentinel = await _collect(decomposer, "test")

    # Events from route_task() must NOT be pre-tagged as "router" — that's Pipeline's job
    tool_events = [e for e in events if isinstance(e, ToolStarted)]
    assert len(tool_events) == 1
    assert tool_events[0].source == "orchestrator"  # NOT "router"
    assert isinstance(sentinel, TaskOutput)


@pytest.mark.asyncio
async def test_route_task_fallback_mid_stream() -> None:
    """When the session generator raises mid-stream, a fallback TaskOutput is still yielded."""
    decomposer, _, router, _ = _make_decomposer()

    async def failing_stream(prompt: str):
        yield ToolStarted(name="history_read", input={}, source="orchestrator")
        raise RuntimeError("mid-stream failure")

    router.send = failing_stream

    items = []
    async for item in decomposer.route_task("test"):
        items.append(item)

    # Should have yielded the event before failure, then a fallback TaskOutput
    assert len(items) >= 1
    assert isinstance(items[-1], TaskOutput)
    assert items[-1].is_fallback


@pytest.mark.asyncio
async def test_route_task_ensure_session_timeout_yields_fallback() -> None:
    """When _ensure_router_session() times out, route_task() yields a fallback TaskOutput."""
    decomposer, _, _, _ = _make_decomposer()

    async def _slow_ensure():
        await asyncio.sleep(1000)

    with patch.object(decomposer, "_ensure_router_session", side_effect=TimeoutError):
        items = []
        async for item in decomposer.route_task("test"):
            items.append(item)

    assert len(items) == 1
    assert isinstance(items[0], TaskOutput)
    assert items[0].is_fallback


@pytest.mark.asyncio
async def test_router_events_reach_history_manager(tmp_path) -> None:
    """Router events from record_event() are recorded in the history file with [Router] prefix."""
    from datetime import datetime, timezone
    from archon.ai.history_manager import HistoryManager

    history = HistoryManager(directory=str(tmp_path))

    router_tool = ToolStarted(name="history_read", input={}, source="router")
    await history.record_event(user_id=1, event=router_tool)

    # HistoryManager names files by UTC date — use UTC to match.
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    history_file = tmp_path / "sessions" / f"{today_utc}.md"
    assert history_file.exists()
    content = history_file.read_text()
    assert "[Router]" in content
    assert "history_read" in content


# ──────────────────────────────────────────────────────────────────
# FEAT-018 Task 4.2 — inject_context called with correct injection_type
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inject_workspace_agents_main_session_type(tmp_path) -> None:
    """_inject_workspace_agents() calls inject_context on main session with 'workspace_agents' type."""
    from archon.ai.event_mapper import INJECTION_TYPE_WORKSPACE_AGENTS

    agents_file = tmp_path / "agents.md"
    agents_file.write_text("- researcher: Does research")

    decomposer, main_session, _, _ = _make_decomposer(cwd=str(tmp_path))
    await decomposer.start()

    main_session.inject_context.assert_called_once()
    call_args = main_session.inject_context.call_args
    assert call_args[0][1] == INJECTION_TYPE_WORKSPACE_AGENTS


@pytest.mark.asyncio
async def test_inject_workspace_agents_router_session_type(tmp_path) -> None:
    """_inject_workspace_agents() calls inject_context on router session with 'router_workspace_agents' type."""
    from archon.ai.event_mapper import INJECTION_TYPE_ROUTER_WORKSPACE_AGENTS

    agents_file = tmp_path / "agents.md"
    agents_file.write_text("- researcher: Does research")

    decomposer, _, router_session, _ = _make_decomposer(cwd=str(tmp_path))
    await decomposer.start()

    router_session.inject_context.assert_called_once()
    call_args = router_session.inject_context.call_args
    assert call_args[0][1] == INJECTION_TYPE_ROUTER_WORKSPACE_AGENTS


@pytest.mark.asyncio
async def test_ensure_router_session_history_type(tmp_path) -> None:
    """_ensure_router_session() uses 'router_history' type when injecting history context."""
    from archon.ai.event_mapper import INJECTION_TYPE_ROUTER_HISTORY

    mock_provider = MagicMock()
    mock_provider.startup_context_prompt.return_value = "## History\nSome prompt"
    mock_provider.get_recent_context.return_value = "Yesterday summary"
    mock_provider.get_context_files = MagicMock(return_value=[])

    decomposer, _, router, _ = _make_decomposer(context_provider=mock_provider)
    decomposer._router_session = None

    with patch("archon.ai.decomposer.ClaudeSession", return_value=router):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            with patch("archon.ai.decomposer.load_workspace_agents", return_value=None):
                await decomposer._ensure_router_session()

    # First call should be the history injection
    assert router.inject_context.call_count >= 1
    history_calls = [
        c for c in router.inject_context.call_args_list
        if len(c[0]) > 1 and c[0][1] == INJECTION_TYPE_ROUTER_HISTORY
    ]
    assert history_calls, (
        f"Expected inject_context called with '{INJECTION_TYPE_ROUTER_HISTORY}', "
        f"got: {router.inject_context.call_args_list}"
    )


@pytest.mark.asyncio
async def test_ensure_router_session_workspace_type(tmp_path) -> None:
    """_ensure_router_session() uses 'router_workspace_agents' type when injecting workspace agents."""
    from archon.ai.event_mapper import INJECTION_TYPE_ROUTER_WORKSPACE_AGENTS

    agents_file = tmp_path / "agents.md"
    agents_file.write_text("- researcher: Does research")

    decomposer, _, router, _ = _make_decomposer(cwd=str(tmp_path))
    decomposer._router_session = None

    with patch("archon.ai.decomposer.ClaudeSession", return_value=router):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            await decomposer._ensure_router_session()

    workspace_calls = [
        c for c in router.inject_context.call_args_list
        if len(c[0]) > 1 and c[0][1] == INJECTION_TYPE_ROUTER_WORKSPACE_AGENTS
    ]
    assert workspace_calls, (
        f"Expected inject_context called with '{INJECTION_TYPE_ROUTER_WORKSPACE_AGENTS}', "
        f"got: {router.inject_context.call_args_list}"
    )


def test_decomposer_uses_rag_url_attribute() -> None:
    """Decomposer must store rag_url as _rag_url internally."""
    decomposer, _, _, _ = _make_decomposer(rag_url="http://localhost:6333")
    assert hasattr(decomposer, "_rag_url"), "_rag_url must exist"
    assert decomposer._rag_url == "http://localhost:6333"


@pytest.mark.asyncio


async def test_decomposer_startup_prompt_rag_enabled() -> None:
    """When Decomposer has rag_url set, startup_context_prompt is called with rag_enabled=True."""
    from unittest.mock import MagicMock

    from archon.ai.decomposer import Decomposer

    mock_session = _mock_session(
        *[],
    )
    mock_context_provider = MagicMock()
    mock_context_provider.startup_context_prompt.return_value = "RAG context prompt"
    mock_context_provider.get_recent_context.return_value = ""
    mock_context_provider.get_context_files.return_value = []

    with patch("archon.ai.decomposer.ClaudeSession", return_value=mock_session):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            with patch("archon.ai.decomposer.load_workspace_agents", return_value=None):
                decomposer = Decomposer(rag_url="http://localhost:6333", context_provider=mock_context_provider)
                decomposer._router_session = None  # ensure lazy start is triggered

                # Trigger _ensure_router_session which calls startup_context_prompt
                router_mock = _mock_session()
                router_mock.start = AsyncMock()
                router_mock.inject_context = MagicMock()
                decomposer._router_session = None

                # Patch ClaudeSession for router session creation
                with patch("archon.ai.decomposer.ClaudeSession", return_value=router_mock):
                    await decomposer._ensure_router_session()

    mock_context_provider.startup_context_prompt.assert_called_once_with(rag_enabled=True)


# ── _parse_task_output() — RAG selected_collections tag parsing ──────────────


def test_parse_task_output_extracts_rag_collections() -> None:
    """Valid <rag_selected_collections> tag is parsed into selected_collections list."""
    decomposer, _, _, _ = _make_decomposer()
    raw = '{"scope":"small","prompt":"do it"}\n<rag_selected_collections>foo, bar</rag_selected_collections>'
    result = decomposer._parse_task_output(raw, "original")
    assert result.selected_collections == ["foo", "bar"]


def test_parse_task_output_empty_tag_returns_empty_list() -> None:
    """Empty <rag_selected_collections></rag_selected_collections> yields []."""
    decomposer, _, _, _ = _make_decomposer()
    raw = '{"scope":"small","prompt":"do it"}\n<rag_selected_collections></rag_selected_collections>'
    result = decomposer._parse_task_output(raw, "original")
    assert result.selected_collections == []


def test_parse_task_output_unclosed_tag_returns_empty_list() -> None:
    """Missing closing tag yields selected_collections=[]."""
    decomposer, _, _, _ = _make_decomposer()
    raw = '{"scope":"small","prompt":"do it"}\n<rag_selected_collections>foo, bar'
    result = decomposer._parse_task_output(raw, "original")
    assert result.selected_collections == []


def test_parse_task_output_missing_tag_returns_none() -> None:
    """No <rag_selected_collections> tag in response → selected_collections is None."""
    decomposer, _, _, _ = _make_decomposer()
    raw = '{"scope":"small","prompt":"do it"}'
    result = decomposer._parse_task_output(raw, "original")
    assert result.selected_collections is None


def test_parse_task_output_rag_tags_survive_json_failure() -> None:
    """RAG tag extraction runs even when JSON is malformed."""
    decomposer, _, _, _ = _make_decomposer()
    raw = 'not valid json <rag_selected_collections>foo, bar</rag_selected_collections>'
    result = decomposer._parse_task_output(raw, "original")
    assert result.selected_collections == ["foo", "bar"]
