"""Tests for SessionManager — S1.4."""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from archon.ai.claude_session import ClaudeSession
from archon.ai.pipeline import Pipeline
from archon.ai.session_manager import SessionManager


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_mock_session() -> ClaudeSession:
    session = MagicMock(spec=ClaudeSession)
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.is_alive = True
    session.is_processing = False  # default to idle so eviction proceeds normally
    return session


def _factory_for(sessions: list[ClaudeSession]):
    """Return a factory that pops from the given list."""
    it = iter(sessions)

    def factory(cwd):  # type: ignore[misc]
        return next(it)

    return factory


# ──────────────────────────────────────────────────────────────────
# get_or_create
# ──────────────────────────────────────────────────────────────────


async def test_get_or_create_returns_new_session() -> None:
    mock = _make_mock_session()
    mgr = SessionManager(timeout=60, session_factory=lambda _: mock)

    session = await mgr.get_or_create(user_id=1)

    assert session is mock
    mock.start.assert_awaited_once()


async def test_get_or_create_reuses_existing_session() -> None:
    mock = _make_mock_session()
    mgr = SessionManager(timeout=60, session_factory=lambda _: mock)

    s1 = await mgr.get_or_create(user_id=1)
    s2 = await mgr.get_or_create(user_id=1)

    assert s1 is s2
    mock.start.assert_awaited_once()  # start called only once


async def test_get_or_create_different_users_get_different_sessions() -> None:
    mock_a = _make_mock_session()
    mock_b = _make_mock_session()
    mgr = SessionManager(timeout=60, session_factory=_factory_for([mock_a, mock_b]))

    sa = await mgr.get_or_create(user_id=1)
    sb = await mgr.get_or_create(user_id=2)

    assert sa is mock_a
    assert sb is mock_b
    mock_a.start.assert_awaited_once()
    mock_b.start.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────
# stop
# ──────────────────────────────────────────────────────────────────


async def test_stop_calls_session_stop_and_removes_from_registry() -> None:
    mock = _make_mock_session()
    mgr = SessionManager(timeout=60, session_factory=lambda _: mock)

    await mgr.get_or_create(user_id=1)
    await mgr.stop(user_id=1)

    mock.stop.assert_awaited_once()
    assert 1 not in mgr._sessions


async def test_stop_nonexistent_user_is_noop() -> None:
    mgr = SessionManager(timeout=60)
    await mgr.stop(user_id=999)  # must not raise


async def test_stop_cancels_inactivity_timer() -> None:
    mock = _make_mock_session()
    mgr = SessionManager(timeout=60, session_factory=lambda _: mock)

    await mgr.get_or_create(user_id=1)
    assert 1 in mgr._timers

    await mgr.stop(user_id=1)

    assert 1 not in mgr._timers


# ──────────────────────────────────────────────────────────────────
# stop_all
# ──────────────────────────────────────────────────────────────────


async def test_stop_all_stops_every_session() -> None:
    mock_a = _make_mock_session()
    mock_b = _make_mock_session()
    mgr = SessionManager(timeout=60, session_factory=_factory_for([mock_a, mock_b]))

    await mgr.get_or_create(user_id=1)
    await mgr.get_or_create(user_id=2)
    await mgr.stop_all()

    mock_a.stop.assert_awaited_once()
    mock_b.stop.assert_awaited_once()
    assert len(mgr._sessions) == 0


async def test_stop_all_clears_timers() -> None:
    mock_a = _make_mock_session()
    mock_b = _make_mock_session()
    mgr = SessionManager(timeout=60, session_factory=_factory_for([mock_a, mock_b]))

    await mgr.get_or_create(user_id=1)
    await mgr.get_or_create(user_id=2)
    await mgr.stop_all()

    assert len(mgr._timers) == 0


async def test_stop_all_on_empty_registry_is_noop() -> None:
    mgr = SessionManager(timeout=60)
    await mgr.stop_all()  # must not raise


# ──────────────────────────────────────────────────────────────────
# inactivity timeout
# ──────────────────────────────────────────────────────────────────


async def test_inactivity_timeout_evicts_session() -> None:
    mock = _make_mock_session()
    mgr = SessionManager(timeout=0.05, session_factory=lambda _: mock)  # 50 ms

    await mgr.get_or_create(user_id=1)
    await asyncio.sleep(0.15)  # wait longer than timeout

    mock.stop.assert_awaited_once()
    assert 1 not in mgr._sessions


async def test_activity_resets_inactivity_timer() -> None:
    mock = _make_mock_session()
    mgr = SessionManager(timeout=0.1, session_factory=lambda _: mock)  # 100 ms

    await mgr.get_or_create(user_id=1)
    await asyncio.sleep(0.07)  # 70 ms — not yet evicted
    await mgr.get_or_create(user_id=1)  # reset timer
    await asyncio.sleep(0.07)  # 70 ms more — still within new 100 ms window

    mock.stop.assert_not_called()  # not evicted yet
    assert 1 in mgr._sessions


# ──────────────────────────────────────────────────────────────────
# has_session — S2.4
# ──────────────────────────────────────────────────────────────────


def test_has_session_false_before_create() -> None:
    mgr = SessionManager(timeout=60)
    assert not mgr.has_session(1)


async def test_has_session_true_after_create() -> None:
    mock = _make_mock_session()
    mgr = SessionManager(timeout=60, session_factory=lambda _: mock)

    await mgr.get_or_create(user_id=1)

    assert mgr.has_session(1)


async def test_has_session_false_after_stop() -> None:
    mock = _make_mock_session()
    mgr = SessionManager(timeout=60, session_factory=lambda _: mock)

    await mgr.get_or_create(user_id=1)
    await mgr.stop(user_id=1)

    assert not mgr.has_session(1)


# ──────────────────────────────────────────────────────────────────
# session_started_at — S2.4
# ──────────────────────────────────────────────────────────────────


def test_session_started_at_none_for_unknown() -> None:
    mgr = SessionManager(timeout=60)
    assert mgr.session_started_at(999) is None


async def test_session_started_at_returns_monotonic_time() -> None:
    mock = _make_mock_session()
    mgr = SessionManager(timeout=60, session_factory=lambda _: mock)

    before = time.monotonic()
    await mgr.get_or_create(user_id=1)
    after = time.monotonic()

    t = mgr.session_started_at(1)
    assert t is not None
    assert before <= t <= after


async def test_session_started_at_cleared_on_stop() -> None:
    mock = _make_mock_session()
    mgr = SessionManager(timeout=60, session_factory=lambda _: mock)

    await mgr.get_or_create(user_id=1)
    await mgr.stop(user_id=1)

    assert mgr.session_started_at(1) is None


async def test_session_started_at_cleared_on_stop_all() -> None:
    mock = _make_mock_session()
    mgr = SessionManager(timeout=60, session_factory=lambda _: mock)

    await mgr.get_or_create(user_id=1)
    await mgr.stop_all()

    assert mgr.session_started_at(1) is None


# ──────────────────────────────────────────────────────────────────
# concurrency — H2
# ──────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────
# context_stats — /context command
# ──────────────────────────────────────────────────────────────────


def test_context_stats_none_for_no_session() -> None:
    mgr = SessionManager(timeout=60)
    assert mgr.context_stats(999) is None


async def test_context_stats_delegates_to_session_usage_stats() -> None:
    mock = _make_mock_session()
    mock.usage_stats = {
        "usage": {"input_tokens": 500},
        "total_cost_usd": 0.01,
        "num_turns": 2,
        "last_duration_ms": 800,
    }
    mgr = SessionManager(timeout=60, session_factory=lambda _: mock)

    await mgr.get_or_create(user_id=1)
    stats = mgr.context_stats(1)

    assert stats is not None
    assert stats["num_turns"] == 2


async def test_context_stats_returns_none_when_session_has_no_stats() -> None:
    mock = _make_mock_session()
    mock.usage_stats = None
    mgr = SessionManager(timeout=60, session_factory=lambda _: mock)

    await mgr.get_or_create(user_id=1)
    stats = mgr.context_stats(1)

    assert stats is None


# ──────────────────────────────────────────────────────────────────
# concurrency — H2
# ──────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────
# get_model / set_model — Critical gap: zero tests
# ──────────────────────────────────────────────────────────────────


def test_get_model_returns_none_by_default() -> None:
    mgr = SessionManager(timeout=60)
    assert mgr.get_model() is None


def test_set_model_changes_get_model() -> None:
    mgr = SessionManager(timeout=60)
    mgr.set_model("claude-opus-4-5")
    assert mgr.get_model() == "claude-opus-4-5"


def test_set_model_none_resets_model() -> None:
    mgr = SessionManager(timeout=60)
    mgr.set_model("claude-opus-4-5")
    mgr.set_model(None)
    assert mgr.get_model() is None


async def test_set_model_propagates_to_new_sessions() -> None:
    """set_model() must affect the model visible to the factory when new sessions are created."""
    captured_models: list[str | None] = []

    def _capturing_factory(cwd: str | None) -> ClaudeSession:
        captured_models.append(mgr.get_model())
        return _make_mock_session()

    mgr = SessionManager(timeout=60, session_factory=_capturing_factory)
    mgr.set_model("claude-sonnet-4-5")
    await mgr.get_or_create(user_id=1)

    assert captured_models == ["claude-sonnet-4-5"]


# ──────────────────────────────────────────────────────────────────
# Default factory with skill_loader / plugin_loader — High gap
# ──────────────────────────────────────────────────────────────────


async def test_default_factory_creates_pipeline() -> None:
    """Default factory must create Pipeline (not bare ClaudeSession)."""
    from unittest.mock import patch

    mock_session = _make_mock_session()
    with patch("archon.ai.session_manager.Pipeline", return_value=mock_session) as MockPipeline:
        mgr = SessionManager(timeout=60)
        await mgr.get_or_create(user_id=1)

    MockPipeline.assert_called_once()


async def test_default_factory_calls_skill_loader_load_all() -> None:
    """Default factory must call skill_loader.load_all() when creating a session."""
    from unittest.mock import MagicMock, patch

    mock_skill_loader = MagicMock()
    mock_skill_loader.load_all.return_value = []

    mgr = SessionManager(timeout=60, skill_loader=mock_skill_loader)

    mock_session = _make_mock_session()
    with patch("archon.ai.session_manager.Pipeline", return_value=mock_session):
        await mgr.get_or_create(user_id=1)

    mock_skill_loader.load_all.assert_called_once()


async def test_default_factory_calls_plugin_loader_methods() -> None:
    """Default factory must call plugin_loader.get_skills() and get_sdk_configs()."""
    from unittest.mock import MagicMock, patch

    mock_plugin_loader = MagicMock()
    mock_plugin_loader.get_skills.return_value = []
    mock_plugin_loader.get_sdk_configs.return_value = []

    mgr = SessionManager(timeout=60, plugin_loader=mock_plugin_loader)

    mock_session = _make_mock_session()
    with patch("archon.ai.session_manager.Pipeline", return_value=mock_session):
        await mgr.get_or_create(user_id=1)

    mock_plugin_loader.get_skills.assert_called_once()
    mock_plugin_loader.get_sdk_configs.assert_called_once()


async def test_default_factory_passes_model_to_session() -> None:
    """Default factory must pass the current _model to ClaudeSession."""
    from unittest.mock import MagicMock, call, patch

    mgr = SessionManager(timeout=60)
    mgr.set_model("claude-opus-4-5")

    mock_session = _make_mock_session()
    with patch("archon.ai.session_manager.Pipeline", return_value=mock_session) as MockPipeline:
        await mgr.get_or_create(user_id=1)

    _, kwargs = MockPipeline.call_args
    assert kwargs.get("model") == "claude-opus-4-5"


# ──────────────────────────────────────────────────────────────────
# concurrency — H2
# ──────────────────────────────────────────────────────────────────


async def test_concurrent_get_or_create_does_not_double_start() -> None:
    start_count = 0

    async def slow_start() -> None:
        nonlocal start_count
        start_count += 1
        await asyncio.sleep(0)  # yield so the other coroutine can run

    mock = _make_mock_session()
    mock.start = AsyncMock(side_effect=slow_start)
    mgr = SessionManager(timeout=60, session_factory=lambda _: mock)

    s1, s2 = await asyncio.gather(
        mgr.get_or_create(user_id=1),
        mgr.get_or_create(user_id=1),
    )

    assert s1 is s2
    assert start_count == 1  # start must be called exactly once


# ──────────────────────────────────────────────────────────────────
# Diagnostics — S14.1
# ──────────────────────────────────────────────────────────────────


def _make_diag_session(
    is_processing: bool = False,
    processing_seconds: float | None = None,
) -> "MagicMock":
    """Mock ClaudeSession pre-configured with diagnostic attribute values."""
    session = MagicMock(spec=ClaudeSession)
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.is_alive = True
    session.is_processing = is_processing
    session.processing_seconds = processing_seconds
    session.diagnostics = {
        "is_alive": True,
        "is_processing": is_processing,
        "processing_seconds": processing_seconds,
        "idle_seconds": 5.0 if not is_processing else None,
        "send_count": 3,
        "recent_events": [],
        "usage_stats": None,
    }
    return session


class TestSessionManagerDiagnostics:
    """S14.1 — SessionManager aggregation of per-session diagnostics."""

    # ── happy paths ─────────────────────────────────────────────────

    def test_session_diagnostics_none_for_unknown_user(self) -> None:
        mgr = SessionManager(timeout=60)
        assert mgr.session_diagnostics(999) is None

    async def test_session_diagnostics_returns_dict_for_known_user(self) -> None:
        mock = _make_diag_session()
        mgr = SessionManager(timeout=60, session_factory=lambda _: mock)
        await mgr.get_or_create(user_id=1)

        result = mgr.session_diagnostics(1)
        assert result is not None
        assert "is_alive" in result
        assert "is_processing" in result

    def test_processing_sessions_empty_when_no_sessions(self) -> None:
        mgr = SessionManager(timeout=60)
        assert mgr.processing_sessions() == {}

    async def test_processing_sessions_empty_when_idle(self) -> None:
        mock = _make_diag_session(is_processing=False, processing_seconds=None)
        mgr = SessionManager(timeout=60, session_factory=lambda _: mock)
        await mgr.get_or_create(user_id=1)
        assert mgr.processing_sessions() == {}

    async def test_processing_sessions_includes_active_session(self) -> None:
        mock = _make_diag_session(is_processing=True, processing_seconds=15.3)
        mgr = SessionManager(timeout=60, session_factory=lambda _: mock)
        await mgr.get_or_create(user_id=1)

        result = mgr.processing_sessions()
        assert 1 in result
        assert result[1] == pytest.approx(15.3)

    async def test_processing_sessions_excludes_stopped_session(self) -> None:
        mock = _make_diag_session(is_processing=True, processing_seconds=10.0)
        mgr = SessionManager(timeout=60, session_factory=lambda _: mock)
        await mgr.get_or_create(user_id=1)
        await mgr.stop(user_id=1)

        assert mgr.processing_sessions() == {}


# ──────────────────────────────────────────────────────────────────
# Background agent wiring — S15.4
# ──────────────────────────────────────────────────────────────────


def test_session_manager_stores_background_agent_mcp_server() -> None:
    """SessionManager must store the bg_mcp_server reference for use in the default factory."""
    from unittest.mock import patch

    mock_server = MagicMock()
    sm = SessionManager(timeout=60, background_agent_mcp_server=mock_server)
    assert sm._bg_mcp_server is mock_server


async def test_get_or_create_calls_mcp_url_for_with_user_id() -> None:
    """Default factory must call mcp_url_for(uid) on the server when background_agent_mcp_server is set."""
    from unittest.mock import patch

    mock_server = MagicMock()
    mock_server.mcp_url_for.return_value = "http://localhost:18182/mcp/42"

    mock_session = _make_mock_session()
    with patch("archon.ai.session_manager.Pipeline", return_value=mock_session):
        sm = SessionManager(timeout=60, background_agent_mcp_server=mock_server)
        await sm.get_or_create(user_id=42)

    mock_server.mcp_url_for.assert_called_once_with(42)


async def test_get_or_create_passes_mcp_url_to_claude_session() -> None:
    """URL returned by mcp_url_for must be forwarded as background_agent_mcp_url to ClaudeSession."""
    from unittest.mock import patch

    mock_server = MagicMock()
    mock_server.mcp_url_for.return_value = "http://localhost:18182/mcp/7"

    mock_session = _make_mock_session()
    with patch("archon.ai.session_manager.Pipeline", return_value=mock_session) as MockPipeline:
        sm = SessionManager(timeout=60, background_agent_mcp_server=mock_server)
        await sm.get_or_create(user_id=7)

    _, kwargs = MockPipeline.call_args
    assert kwargs.get("background_agent_mcp_url") == "http://localhost:18182/mcp/7"


async def test_get_or_create_no_mcp_url_when_server_none() -> None:
    """When background_agent_mcp_server is None, background_agent_mcp_url passed to ClaudeSession must be None."""
    from unittest.mock import patch

    mock_session = _make_mock_session()
    with patch("archon.ai.session_manager.Pipeline", return_value=mock_session) as MockPipeline:
        sm = SessionManager(timeout=60, background_agent_mcp_server=None)
        await sm.get_or_create(user_id=1)

    _, kwargs = MockPipeline.call_args
    assert kwargs.get("background_agent_mcp_url") is None


async def test_get_or_create_passes_spawn_rule_to_claude_session() -> None:
    """spawn_rule must be forwarded to ClaudeSession by the default factory."""
    from unittest.mock import patch

    mock_session = _make_mock_session()
    with patch("archon.ai.session_manager.Pipeline", return_value=mock_session) as MockPipeline:
        sm = SessionManager(timeout=60, spawn_rule="eager")
        await sm.get_or_create(user_id=1)

    _, kwargs = MockPipeline.call_args
    assert kwargs.get("spawn_rule") == "eager"


async def test_get_or_create_passes_tool_promotion_threshold_to_pipeline() -> None:
    """tool_promotion_threshold must be forwarded to Pipeline by the default factory."""
    from unittest.mock import patch

    mock_session = _make_mock_session()
    with patch("archon.ai.session_manager.Pipeline", return_value=mock_session) as MockPipeline:
        sm = SessionManager(timeout=60, tool_promotion_threshold=7)
        await sm.get_or_create(user_id=1)

    _, kwargs = MockPipeline.call_args
    assert kwargs.get("tool_promotion_threshold") == 7


# ──────────────────────────────────────────────────────────────────
# HistoryCompactor context injection
# ──────────────────────────────────────────────────────────────────


async def test_history_context_injected_on_new_session() -> None:
    """inject_context is called with both the startup prompt and past summaries."""
    mock_session = _make_mock_session()
    mock_compactor = MagicMock()
    mock_compactor.startup_context_prompt.return_value = "## Conversation history\npath info"
    mock_compactor.get_recent_context.return_value = "## Day 1 summary"

    mgr = SessionManager(
        timeout=60,
        session_factory=lambda _: mock_session,
        history_compactor=mock_compactor,
    )
    await mgr.get_or_create(user_id=1)

    mock_session.inject_context.assert_called_once()
    call_arg = mock_session.inject_context.call_args[0][0]
    assert "Conversation history" in call_arg
    assert "Day 1 summary" in call_arg


async def test_history_context_not_injected_when_compactor_none() -> None:
    """Without a HistoryCompactor, inject_context must not be called."""
    mock_session = _make_mock_session()
    mgr = SessionManager(timeout=60, session_factory=lambda _: mock_session)
    await mgr.get_or_create(user_id=1)
    mock_session.inject_context.assert_not_called()


async def test_startup_prompt_injected_even_without_summaries() -> None:
    """Startup prompt is always injected even when get_recent_context returns None."""
    mock_session = _make_mock_session()
    mock_compactor = MagicMock()
    mock_compactor.startup_context_prompt.return_value = "## Conversation history\npath info"
    mock_compactor.get_recent_context.return_value = None

    mgr = SessionManager(
        timeout=60,
        session_factory=lambda _: mock_session,
        history_compactor=mock_compactor,
    )
    await mgr.get_or_create(user_id=1)

    mock_session.inject_context.assert_called_once()
    call_arg = mock_session.inject_context.call_args[0][0]
    assert "Conversation history" in call_arg


async def test_history_context_not_injected_on_existing_session() -> None:
    """inject_context is only called when a new session is created, not on reuse."""
    mock_session = _make_mock_session()
    mock_compactor = MagicMock()
    mock_compactor.startup_context_prompt.return_value = "## Conversation history"
    mock_compactor.get_recent_context.return_value = "Some context"

    mgr = SessionManager(
        timeout=60,
        session_factory=lambda _: mock_session,
        history_compactor=mock_compactor,
    )
    await mgr.get_or_create(user_id=1)  # creates session → inject
    mock_session.inject_context.reset_mock()
    await mgr.get_or_create(user_id=1)  # reuses session → no inject

    mock_session.inject_context.assert_not_called()


async def test_startup_prompt_passes_rag_enabled_when_qmd_url_set() -> None:
    """startup_context_prompt is called with rag_enabled=True when qmd_url is set."""
    mock_session = _make_mock_session()
    mock_compactor = MagicMock()
    mock_compactor.startup_context_prompt.return_value = "prompt"
    mock_compactor.get_recent_context.return_value = None

    mgr = SessionManager(
        timeout=60,
        session_factory=lambda _: mock_session,
        history_compactor=mock_compactor,
        qmd_url="http://localhost:8181/mcp",
    )
    await mgr.get_or_create(user_id=1)

    mock_compactor.startup_context_prompt.assert_called_once_with(rag_enabled=True)


async def test_startup_prompt_passes_rag_disabled_when_no_qmd_url() -> None:
    """startup_context_prompt is called with rag_enabled=False when qmd_url is None."""
    mock_session = _make_mock_session()
    mock_compactor = MagicMock()
    mock_compactor.startup_context_prompt.return_value = "prompt"
    mock_compactor.get_recent_context.return_value = None

    mgr = SessionManager(
        timeout=60,
        session_factory=lambda _: mock_session,
        history_compactor=mock_compactor,
        qmd_url=None,
    )
    await mgr.get_or_create(user_id=1)

    mock_compactor.startup_context_prompt.assert_called_once_with(rag_enabled=False)


# ── inject_agent_context ──────────────────────────────────────────


class TestInjectAgentContext:
    async def test_calls_inject_context_on_active_session(self) -> None:
        mock_session = MagicMock()
        mock_session.inject_context = MagicMock()
        mgr = SessionManager(timeout=60)
        mgr._sessions[42] = mock_session

        mgr.inject_agent_context(user_id=42, text="hello from agent")

        mock_session.inject_context.assert_called_once_with("hello from agent")

    async def test_is_noop_when_no_session_exists(self) -> None:
        mgr = SessionManager(timeout=60)

        # Must not raise when no session is registered for the user
        mgr.inject_agent_context(user_id=999, text="no session here")


# ──────────────────────────────────────────────────────────────────
# ReminderConfig wiring — US-006
# ──────────────────────────────────────────────────────────────────


class TestReminderConfigWiring:
    """SessionManager must create ContextReminder when reminder_config is enabled."""

    async def test_passes_reminder_to_pipeline_when_enabled(self, tmp_path) -> None:
        """When reminder_config.enabled=True and cwd is set, Pipeline receives a ContextReminder."""
        from pathlib import Path
        from unittest.mock import patch

        from archon.config.loader import ReminderConfig

        cfg = ReminderConfig(enabled=True, interval_messages=5, interval_tokens=100)
        mock_session = _make_mock_session()

        with patch("archon.ai.session_manager.Pipeline", return_value=mock_session) as MockPipeline:
            mgr = SessionManager(timeout=60, cwd=str(tmp_path), reminder_config=cfg)
            await mgr.get_or_create(user_id=1)

        _, kwargs = MockPipeline.call_args
        reminder = kwargs.get("reminder")
        assert reminder is not None

    async def test_passes_no_reminder_when_config_none(self, tmp_path) -> None:
        """When reminder_config=None, Pipeline receives reminder=None."""
        from unittest.mock import patch

        mock_session = _make_mock_session()

        with patch("archon.ai.session_manager.Pipeline", return_value=mock_session) as MockPipeline:
            mgr = SessionManager(timeout=60, cwd=str(tmp_path), reminder_config=None)
            await mgr.get_or_create(user_id=1)

        _, kwargs = MockPipeline.call_args
        assert kwargs.get("reminder") is None

    async def test_passes_no_reminder_when_disabled(self, tmp_path) -> None:
        """When reminder_config.enabled=False, Pipeline receives reminder=None."""
        from unittest.mock import patch

        from archon.config.loader import ReminderConfig

        cfg = ReminderConfig(enabled=False, interval_messages=5, interval_tokens=100)
        mock_session = _make_mock_session()

        with patch("archon.ai.session_manager.Pipeline", return_value=mock_session) as MockPipeline:
            mgr = SessionManager(timeout=60, cwd=str(tmp_path), reminder_config=cfg)
            await mgr.get_or_create(user_id=1)

        _, kwargs = MockPipeline.call_args
        assert kwargs.get("reminder") is None

    async def test_passes_no_reminder_when_cwd_none(self) -> None:
        """When cwd=None, no ContextReminder can be created — Pipeline receives reminder=None."""
        from unittest.mock import patch

        from archon.config.loader import ReminderConfig

        cfg = ReminderConfig(enabled=True, interval_messages=5, interval_tokens=100)
        mock_session = _make_mock_session()

        with patch("archon.ai.session_manager.Pipeline", return_value=mock_session) as MockPipeline:
            mgr = SessionManager(timeout=60, cwd=None, reminder_config=cfg)
            await mgr.get_or_create(user_id=1)

        _, kwargs = MockPipeline.call_args
        assert kwargs.get("reminder") is None

    async def test_reminder_uses_cwd_as_workspace_dir(self, tmp_path) -> None:
        """ContextReminder workspace_dir must be Path(cwd)."""
        from pathlib import Path
        from unittest.mock import patch

        from archon.ai.reminder import ContextReminder
        from archon.config.loader import ReminderConfig

        cfg = ReminderConfig(enabled=True, interval_messages=5, interval_tokens=100)
        mock_session = _make_mock_session()
        created_reminders: list[ContextReminder] = []

        real_init = ContextReminder.__init__

        def _capture_init(self, config, workspace_dir):
            real_init(self, config, workspace_dir)
            created_reminders.append(self)

        with patch("archon.ai.session_manager.Pipeline", return_value=mock_session):
            with patch.object(ContextReminder, "__init__", _capture_init):
                mgr = SessionManager(timeout=60, cwd=str(tmp_path), reminder_config=cfg)
                await mgr.get_or_create(user_id=1)

        assert len(created_reminders) == 1
        assert created_reminders[0]._file.parent == Path(str(tmp_path))


# ──────────────────────────────────────────────────────────────────
# router_mcp_url wiring — Wave 5
# ──────────────────────────────────────────────────────────────────


async def test_router_mcp_url_passed_to_pipeline() -> None:
    """router_mcp_url must be forwarded to Pipeline by the default factory."""
    from unittest.mock import patch

    mock_session = _make_mock_session()
    with patch("archon.ai.session_manager.Pipeline", return_value=mock_session) as MockPipeline:
        sm = SessionManager(timeout=60, router_mcp_url="http://localhost:18183/mcp")
        await sm.get_or_create(user_id=1)

    _, kwargs = MockPipeline.call_args
    assert kwargs.get("router_mcp_url") == "http://localhost:18183/mcp"


async def test_router_mcp_url_none_when_not_provided() -> None:
    """When router_mcp_url is not provided, Pipeline receives router_mcp_url=None."""
    from unittest.mock import patch

    mock_session = _make_mock_session()
    with patch("archon.ai.session_manager.Pipeline", return_value=mock_session) as MockPipeline:
        sm = SessionManager(timeout=60)
        await sm.get_or_create(user_id=1)

    _, kwargs = MockPipeline.call_args
    assert kwargs.get("router_mcp_url") is None


async def test_router_mcp_headers_passed_to_pipeline() -> None:
    """router_mcp_headers must be forwarded to Pipeline by the default factory."""
    from unittest.mock import patch

    mock_session = _make_mock_session()
    headers = {"Authorization": "Bearer testtoken"}
    with patch("archon.ai.session_manager.Pipeline", return_value=mock_session) as MockPipeline:
        sm = SessionManager(timeout=60, router_mcp_headers=headers)
        await sm.get_or_create(user_id=1)

    _, kwargs = MockPipeline.call_args
    assert kwargs.get("router_mcp_headers") == headers


async def test_router_mcp_headers_none_when_not_provided() -> None:
    """When router_mcp_headers is not provided, Pipeline receives router_mcp_headers=None."""
    from unittest.mock import patch

    mock_session = _make_mock_session()
    with patch("archon.ai.session_manager.Pipeline", return_value=mock_session) as MockPipeline:
        sm = SessionManager(timeout=60)
        await sm.get_or_create(user_id=1)

    _, kwargs = MockPipeline.call_args
    assert kwargs.get("router_mcp_headers") is None


# ──────────────────────────────────────────────────────────────────
# context_provider wiring — Bug fix
# ──────────────────────────────────────────────────────────────────


async def test_context_provider_passed_to_pipeline() -> None:
    """history_compactor must be forwarded as context_provider to Pipeline by the default factory."""
    from unittest.mock import MagicMock, patch

    mock_compactor = MagicMock()
    mock_compactor.startup_context_prompt.return_value = "prompt"
    mock_compactor.get_recent_context.return_value = None

    mock_session = _make_mock_session()
    with patch("archon.ai.session_manager.Pipeline", return_value=mock_session) as MockPipeline:
        sm = SessionManager(timeout=60, history_compactor=mock_compactor)
        await sm.get_or_create(user_id=1)

    _, kwargs = MockPipeline.call_args
    assert kwargs.get("context_provider") is mock_compactor


async def test_context_provider_none_when_no_compactor() -> None:
    """When history_compactor is None, Pipeline receives context_provider=None."""
    from unittest.mock import patch

    mock_session = _make_mock_session()
    with patch("archon.ai.session_manager.Pipeline", return_value=mock_session) as MockPipeline:
        sm = SessionManager(timeout=60, history_compactor=None)
        await sm.get_or_create(user_id=1)

    _, kwargs = MockPipeline.call_args
    assert kwargs.get("context_provider") is None


# ──────────────────────────────────────────────────────────────────
# Bug 2: eviction deferred when session is actively processing
# ──────────────────────────────────────────────────────────────────


async def test_eviction_deferred_when_session_is_processing() -> None:
    """Eviction timer must not destroy a session that is actively processing."""
    mock = _make_mock_session()
    mock.is_processing = True  # session reports active processing

    mgr = SessionManager(timeout=0.05, session_factory=lambda _: mock)  # 50 ms
    await mgr.get_or_create(user_id=1)

    await asyncio.sleep(0.15)  # well past timeout

    # stop() must NOT have been called while processing
    mock.stop.assert_not_called()
    # session must still be in registry (rescheduled, not evicted)
    assert 1 in mgr._sessions


async def test_eviction_proceeds_when_session_is_idle() -> None:
    """Normal eviction must still work when session is not processing."""
    mock = _make_mock_session()
    mock.is_processing = False  # session is idle

    mgr = SessionManager(timeout=0.05, session_factory=lambda _: mock)  # 50 ms
    await mgr.get_or_create(user_id=1)

    await asyncio.sleep(0.15)  # well past timeout

    mock.stop.assert_awaited_once()
    assert 1 not in mgr._sessions


# ──────────────────────────────────────────────────────────────────
# Bug 3: _locks dict memory leak
# ──────────────────────────────────────────────────────────────────


async def test_stop_removes_lock_from_locks_dict() -> None:
    """stop() must clean up the per-user lock to prevent a memory leak."""
    mock = _make_mock_session()
    mgr = SessionManager(timeout=60, session_factory=lambda _: mock)

    await mgr.get_or_create(user_id=1)
    assert 1 in mgr._locks  # lock was created

    await mgr.stop(user_id=1)

    assert 1 not in mgr._locks


async def test_stop_all_clears_locks_dict() -> None:
    """stop_all() must clear all locks to prevent a memory leak."""
    mock_a = _make_mock_session()
    mock_b = _make_mock_session()
    mgr = SessionManager(timeout=60, session_factory=_factory_for([mock_a, mock_b]))

    await mgr.get_or_create(user_id=1)
    await mgr.get_or_create(user_id=2)
    assert len(mgr._locks) == 2

    await mgr.stop_all()

    assert mgr._locks == {}


# ──────────────────────────────────────────────────────────────────
# BUG-Sess-B: stop_all() must run session.stop() calls concurrently
# ──────────────────────────────────────────────────────────────────


async def test_stop_all_runs_session_stops_concurrently() -> None:
    """stop_all() must gather all session.stop() calls — not run them sequentially.

    Each session.stop() sleeps for 0.15 s. With 3 sessions:
    - Sequential: total ≥ 0.45 s
    - Concurrent:  total ≈ 0.15 s

    The test asserts total elapsed time < 0.35 s to prove concurrency.
    """
    DELAY = 0.15

    async def _slow_stop() -> None:
        await asyncio.sleep(DELAY)

    def _make_slow_session() -> ClaudeSession:
        s = _make_mock_session()
        s.stop = AsyncMock(side_effect=_slow_stop)
        return s

    sessions = [_make_slow_session() for _ in range(3)]
    mgr = SessionManager(timeout=60, session_factory=_factory_for(sessions))

    for uid in range(1, 4):
        await mgr.get_or_create(user_id=uid)

    start = asyncio.get_event_loop().time()
    await mgr.stop_all()
    elapsed = asyncio.get_event_loop().time() - start

    # Sequential would take ≥ 3 × 0.15 = 0.45 s; concurrent takes ≈ 0.15 s
    assert elapsed < 0.35, f"stop_all took {elapsed:.3f}s — expected concurrent execution"
    assert len(mgr._sessions) == 0


# ──────────────────────────────────────────────────────────────────
# BUG-Sess-C: stop() must keep lock until after session.stop() completes
# ──────────────────────────────────────────────────────────────────


async def test_stop_lock_present_during_session_stop() -> None:
    """The per-user lock must remain in _locks while session.stop() is awaited.

    If the lock is removed before stop() completes, a concurrent get_or_create()
    would create a new lock + start a second session while the old one is still
    being torn down — the eviction race described in BUG-Sess-C.
    """
    lock_present_during_stop: list[bool] = []

    async def _record_lock_presence() -> None:
        # Called as session.stop() — record whether the lock is still in _locks
        lock_present_during_stop.append(1 in mgr._locks)

    mock = _make_mock_session()
    mock.stop = AsyncMock(side_effect=_record_lock_presence)

    mgr = SessionManager(timeout=60, session_factory=lambda _: mock)
    await mgr.get_or_create(user_id=1)

    await mgr.stop(user_id=1)

    assert lock_present_during_stop == [True], (
        "Lock was NOT present during session.stop() — eviction race possible"
    )
    # Lock must be removed after stop completes
    assert 1 not in mgr._locks


# ──────────────────────────────────────────────────────────────────
# Bug F2: stop_all() must not abort on first exception
# ──────────────────────────────────────────────────────────────────


async def test_stop_all_continues_after_session_stop_raises() -> None:
    """stop_all() must attempt stop() on ALL sessions even if one raises."""
    mock_a = _make_mock_session()
    mock_b = _make_mock_session()
    mock_c = _make_mock_session()

    # First session raises on stop()
    mock_a.stop = AsyncMock(side_effect=RuntimeError("SDK crash"))

    mgr = SessionManager(timeout=60, session_factory=_factory_for([mock_a, mock_b, mock_c]))

    await mgr.get_or_create(user_id=1)
    await mgr.get_or_create(user_id=2)
    await mgr.get_or_create(user_id=3)

    # Must not raise even though mock_a.stop raises
    await mgr.stop_all()

    # All three sessions must have had stop() called
    mock_a.stop.assert_awaited_once()
    mock_b.stop.assert_awaited_once()
    mock_c.stop.assert_awaited_once()

    # Registry must be empty afterwards
    assert len(mgr._sessions) == 0


# ──────────────────────────────────────────────────────────────────
# Issue #18: bg MCP headers passed through to Pipeline
# ──────────────────────────────────────────────────────────────────


async def test_get_or_create_passes_bg_mcp_headers_to_pipeline() -> None:
    """mcp_headers_for(uid) result must be forwarded as background_agent_mcp_headers to Pipeline."""
    from unittest.mock import patch

    mock_server = MagicMock()
    mock_server.mcp_url_for.return_value = "http://localhost:18182/mcp/42"
    mock_server.mcp_headers_for.return_value = {"Authorization": "Bearer bg-secret"}

    mock_session = _make_mock_session()
    with patch("archon.ai.session_manager.Pipeline", return_value=mock_session) as MockPipeline:
        sm = SessionManager(timeout=60, background_agent_mcp_server=mock_server)
        await sm.get_or_create(user_id=42)

    _, kwargs = MockPipeline.call_args
    assert kwargs.get("background_agent_mcp_headers") == {"Authorization": "Bearer bg-secret"}
    mock_server.mcp_headers_for.assert_called_once_with(42)


async def test_get_or_create_no_bg_mcp_headers_when_server_none() -> None:
    """When background_agent_mcp_server is None, background_agent_mcp_headers must be None."""
    from unittest.mock import patch

    mock_session = _make_mock_session()
    with patch("archon.ai.session_manager.Pipeline", return_value=mock_session) as MockPipeline:
        sm = SessionManager(timeout=60, background_agent_mcp_server=None)
        await sm.get_or_create(user_id=1)

    _, kwargs = MockPipeline.call_args
    assert kwargs.get("background_agent_mcp_headers") is None


# ──────────────────────────────────────────────────────────────────
# auto_compact_if_needed — Task 3
# ──────────────────────────────────────────────────────────────────


def _make_compact_session(context_pct: int, is_processing: bool = False) -> MagicMock:
    """Mock session with context_percentage() and is_processing."""
    session = _make_mock_session()
    session.context_percentage = MagicMock(return_value=context_pct)
    session.inject_context = MagicMock()
    session.is_processing = is_processing
    return session


def _make_history_compactor() -> MagicMock:
    """Mock HistoryCompactor with compact_today, startup_context_prompt, etc."""
    compactor = MagicMock()
    compactor.compact_today = AsyncMock()
    compactor.startup_context_prompt.return_value = "## History\npath info"
    compactor.get_recent_context.return_value = None
    compactor.get_context_files.return_value = []
    return compactor


class TestAutoCompact:
    """Task 3 — auto_compact_if_needed in SessionManager."""

    async def test_auto_compact_not_triggered_when_disabled(self) -> None:
        """threshold=0 means disabled; returns None."""
        mock = _make_compact_session(context_pct=95)
        mgr = SessionManager(timeout=60, session_factory=lambda _: mock, auto_compact_threshold=0)
        await mgr.get_or_create(user_id=1)

        result = await mgr.auto_compact_if_needed(user_id=1)

        assert result is None
        mock.stop.assert_not_called()

    async def test_auto_compact_not_triggered_below_threshold(self) -> None:
        """context at 50%, threshold 80% -> no compaction."""
        mock = _make_compact_session(context_pct=50)
        mgr = SessionManager(timeout=60, session_factory=lambda _: mock, auto_compact_threshold=80)
        await mgr.get_or_create(user_id=1)

        result = await mgr.auto_compact_if_needed(user_id=1)

        assert result is None
        mock.stop.assert_not_called()

    async def test_auto_compact_triggered_at_threshold(self) -> None:
        """context at 80%, threshold 80% -> compaction triggered, returns 80."""
        mock1 = _make_compact_session(context_pct=80)
        mock2 = _make_compact_session(context_pct=5)
        sessions = iter([mock1, mock2])
        mgr = SessionManager(
            timeout=60,
            session_factory=lambda _: next(sessions),
            auto_compact_threshold=80,
        )
        await mgr.get_or_create(user_id=1)

        result = await mgr.auto_compact_if_needed(user_id=1)

        assert result == 80
        mock1.stop.assert_awaited_once()

    async def test_auto_compact_triggered_above_threshold(self) -> None:
        """context at 95%, threshold 80% -> returns 95."""
        mock1 = _make_compact_session(context_pct=95)
        mock2 = _make_compact_session(context_pct=5)
        sessions = iter([mock1, mock2])
        mgr = SessionManager(
            timeout=60,
            session_factory=lambda _: next(sessions),
            auto_compact_threshold=80,
        )
        await mgr.get_or_create(user_id=1)

        result = await mgr.auto_compact_if_needed(user_id=1)

        assert result == 95

    async def test_auto_compact_skipped_when_processing(self) -> None:
        """If session is_processing=True, skip compaction and return None."""
        mock = _make_compact_session(context_pct=85, is_processing=True)
        mgr = SessionManager(timeout=60, session_factory=lambda _: mock, auto_compact_threshold=80)
        await mgr.get_or_create(user_id=1)

        result = await mgr.auto_compact_if_needed(user_id=1)

        assert result is None
        mock.stop.assert_not_called()

    async def test_auto_compact_no_active_session(self) -> None:
        """No session exists -> returns None without crashing."""
        mgr = SessionManager(timeout=60, auto_compact_threshold=80)

        result = await mgr.auto_compact_if_needed(user_id=999)

        assert result is None

    async def test_auto_compact_without_compactor(self) -> None:
        """No history_compactor -> session still stopped/recreated, compact_today NOT called."""
        mock1 = _make_compact_session(context_pct=85)
        mock2 = _make_compact_session(context_pct=5)
        sessions = iter([mock1, mock2])
        mgr = SessionManager(
            timeout=60,
            session_factory=lambda _: next(sessions),
            auto_compact_threshold=80,
            history_compactor=None,
        )
        await mgr.get_or_create(user_id=1)

        result = await mgr.auto_compact_if_needed(user_id=1)

        assert result == 85
        mock1.stop.assert_awaited_once()

    async def test_auto_compact_compact_today_failure(self) -> None:
        """compact_today() raises -> session still stopped/recreated, exception swallowed."""
        from unittest.mock import patch

        from archon.ai.history_compactor import HistoryCompactor

        mock1 = _make_compact_session(context_pct=85)
        mock2 = _make_compact_session(context_pct=5)
        sessions = iter([mock1, mock2])
        mock_compactor = _make_history_compactor()
        mock_compactor.compact_today = AsyncMock(side_effect=RuntimeError("SDK error"))
        # Force isinstance(mock_compactor, HistoryCompactor) to return True
        mock_compactor.__class__ = HistoryCompactor

        mgr = SessionManager(
            timeout=60,
            session_factory=lambda _: next(sessions),
            auto_compact_threshold=80,
            history_compactor=mock_compactor,
        )
        await mgr.get_or_create(user_id=1)

        with patch("archon.ai.session_manager.logger") as mock_logger:
            result = await mgr.auto_compact_if_needed(user_id=1)
            # Give background task a chance to run and swallow the error
            await asyncio.sleep(0.05)

        # Session recreated despite compact_today failure
        assert result == 85
        mock1.stop.assert_awaited_once()
        # Logger.warning must be called when compact_today() raises
        mock_logger.warning.assert_called()

    async def test_auto_compact_returns_percentage(self) -> None:
        """Returned int matches context_percentage() value."""
        mock1 = _make_compact_session(context_pct=73)
        mock2 = _make_compact_session(context_pct=5)
        sessions = iter([mock1, mock2])
        mgr = SessionManager(
            timeout=60,
            session_factory=lambda _: next(sessions),
            auto_compact_threshold=70,
        )
        await mgr.get_or_create(user_id=1)

        result = await mgr.auto_compact_if_needed(user_id=1)

        assert result == 73

    async def test_auto_compact_logs_duration(self) -> None:
        """logger.info must be called with timing info when compaction fires."""
        import logging
        from unittest.mock import patch

        mock1 = _make_compact_session(context_pct=85)
        mock2 = _make_compact_session(context_pct=5)
        sessions = iter([mock1, mock2])
        mgr = SessionManager(
            timeout=60,
            session_factory=lambda _: next(sessions),
            auto_compact_threshold=80,
        )
        await mgr.get_or_create(user_id=1)

        with patch("archon.ai.session_manager.logger") as mock_logger:
            await mgr.auto_compact_if_needed(user_id=1)

        mock_logger.info.assert_called()
        # Check that at least one call mentions the percentage or timing
        calls_text = " ".join(str(c) for c in mock_logger.info.call_args_list)
        assert "85" in calls_text or "compac" in calls_text.lower()

    async def test_auto_compact_second_call_returns_none(self) -> None:
        """After compaction, new session is below threshold; second call returns None."""
        mock1 = _make_compact_session(context_pct=85)
        mock2 = _make_compact_session(context_pct=5)  # fresh session, well below threshold
        sessions = iter([mock1, mock2])
        mgr = SessionManager(
            timeout=60,
            session_factory=lambda _: next(sessions),
            auto_compact_threshold=80,
        )
        await mgr.get_or_create(user_id=1)

        first = await mgr.auto_compact_if_needed(user_id=1)
        second = await mgr.auto_compact_if_needed(user_id=1)

        assert first == 85
        assert second is None

    async def test_auto_compact_creates_fresh_session_with_history(self) -> None:
        """After compact, new session has history context injected."""
        mock1 = _make_compact_session(context_pct=85)
        mock2 = _make_compact_session(context_pct=5)
        sessions = iter([mock1, mock2])
        mock_compactor = _make_history_compactor()
        mock_compactor.get_recent_context.return_value = "## Summary"

        mgr = SessionManager(
            timeout=60,
            session_factory=lambda _: next(sessions),
            auto_compact_threshold=80,
            history_compactor=mock_compactor,
        )
        await mgr.get_or_create(user_id=1)
        mock1.inject_context.reset_mock()
        mock2.inject_context.reset_mock()

        await mgr.auto_compact_if_needed(user_id=1)

        # New session (mock2) should have context injected
        mock2.inject_context.assert_called_once()
        injected = mock2.inject_context.call_args[0][0]
        assert "Summary" in injected

    async def test_auto_compact_background_tasks_tracked(self) -> None:
        """_background_tasks set is populated when compaction fires with a HistoryCompactor."""
        from archon.ai.history_compactor import HistoryCompactor

        mock1 = _make_compact_session(context_pct=85)
        mock2 = _make_compact_session(context_pct=5)
        sessions = iter([mock1, mock2])
        mock_compactor = _make_history_compactor()
        # Force isinstance check
        mock_compactor.__class__ = HistoryCompactor

        mgr = SessionManager(
            timeout=60,
            session_factory=lambda _: next(sessions),
            auto_compact_threshold=80,
            history_compactor=mock_compactor,
        )
        await mgr.get_or_create(user_id=1)

        # Capture background_tasks state right after call (before task runs)
        await mgr.auto_compact_if_needed(user_id=1)

        # Give background task time to complete
        await asyncio.sleep(0.05)
        # Verify compact_today was actually called (not just that the set exists)
        mock_compactor.compact_today.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────
# was_evicted() — Task 1.1
# ──────────────────────────────────────────────────────────────────


def test_was_evicted_returns_false_before_any_eviction() -> None:
    """Fresh SessionManager: was_evicted(42) returns False."""
    mgr = SessionManager(timeout=60)
    assert mgr.was_evicted(42) is False


async def test_was_evicted_returns_true_after_eviction() -> None:
    """After _evict_after() runs, was_evicted(42) returns True."""
    mock = _make_mock_session()
    mock.is_processing = False
    mgr = SessionManager(timeout=60, session_factory=lambda _: mock)
    await mgr.get_or_create(user_id=42)

    # Patch sleep to return immediately so _evict_after fires synchronously
    from unittest.mock import patch

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await mgr._evict_after(42)

    assert mgr.was_evicted(42) is True


async def test_was_evicted_cleared_after_create_session() -> None:
    """After set _evicted_users directly, _create_session clears the flag."""
    mock = _make_mock_session()
    mgr = SessionManager(timeout=60, session_factory=lambda _: mock)
    mgr._evicted_users = {42}

    # Need a lock to call _create_session
    mgr._locks[42] = asyncio.Lock()
    await mgr._create_session(42)

    assert mgr.was_evicted(42) is False


def test_was_evicted_unrelated_user_unaffected() -> None:
    """Evicting user 42 does not affect was_evicted(99)."""
    mgr = SessionManager(timeout=60)
    mgr._evicted_users = {42}
    assert mgr.was_evicted(99) is False


async def test_was_evicted_cleared_by_stop_all() -> None:
    """stop_all() clears all eviction flags."""
    mgr = SessionManager(timeout=60)
    mgr._evicted_users = {42}
    await mgr.stop_all()
    assert mgr.was_evicted(42) is False


async def test_explicit_stop_clears_eviction_flag() -> None:
    """stop(42) clears the eviction flag for that user."""
    mock = _make_mock_session()
    mgr = SessionManager(timeout=60, session_factory=lambda _: mock)
    await mgr.get_or_create(user_id=42)
    mgr._evicted_users = {42}

    await mgr.stop(42)

    assert mgr.was_evicted(42) is False


async def test_eviction_deferred_does_not_set_was_evicted() -> None:
    """When session is_processing=True, _evict_after reschedules without setting was_evicted."""
    from unittest.mock import patch

    mock = _make_mock_session()
    mock.is_processing = True
    mgr = SessionManager(timeout=60, session_factory=lambda _: mock)
    await mgr.get_or_create(user_id=42)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await mgr._evict_after(42)

    # Eviction was deferred — flag must NOT be set
    assert mgr.was_evicted(42) is False
    # Session still registered
    assert 42 in mgr._sessions
    # A rescheduled timer must have been created
    assert 42 in mgr._timers


async def test_was_evicted_set_even_when_stop_raises() -> None:
    """Even if stop() raises, _evict_after sets was_evicted via try/finally."""
    from unittest.mock import patch

    mock = _make_mock_session()
    mock.is_processing = False
    mgr = SessionManager(timeout=60, session_factory=lambda _: mock)
    await mgr.get_or_create(user_id=42)

    # Make stop() raise
    original_stop = mgr.stop

    async def _raising_stop(user_id: int) -> None:
        raise RuntimeError("stop failed")

    mgr.stop = _raising_stop  # type: ignore[method-assign]

    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(RuntimeError):
            await mgr._evict_after(42)

    assert mgr.was_evicted(42) is True


async def test_full_eviction_lifecycle() -> None:
    """Full lifecycle: evict → was_evicted True → get_or_create → was_evicted False."""
    from unittest.mock import patch

    mock = _make_mock_session()
    mock.is_processing = False
    mgr = SessionManager(timeout=60, session_factory=lambda _: mock)
    await mgr.get_or_create(user_id=42)

    # Trigger eviction
    with patch("asyncio.sleep", new_callable=AsyncMock):
        await mgr._evict_after(42)

    assert mgr.was_evicted(42) is True

    # Re-create session via the public API
    await mgr.get_or_create(user_id=42)

    assert mgr.was_evicted(42) is False


async def test_auto_compact_clears_eviction_flag() -> None:
    """auto_compact_if_needed recreates session via _create_session, which clears was_evicted."""
    mock1 = _make_compact_session(context_pct=85)
    mock2 = _make_compact_session(context_pct=5)
    sessions = iter([mock1, mock2])
    mgr = SessionManager(
        timeout=60,
        session_factory=lambda _: next(sessions),
        auto_compact_threshold=80,
    )
    await mgr.get_or_create(user_id=42)
    # Simulate a prior eviction
    mgr._evicted_users = {42}

    await mgr.auto_compact_if_needed(user_id=42)

    assert mgr.was_evicted(42) is False
