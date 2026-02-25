"""Tests for SessionManager — S1.4."""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from archon.ai.claude_session import ClaudeSession
from archon.ai.session_manager import SessionManager


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_mock_session() -> ClaudeSession:
    session = MagicMock(spec=ClaudeSession)
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.is_alive = True
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


async def test_default_factory_calls_skill_loader_load_all() -> None:
    """Default factory must call skill_loader.load_all() when creating a session."""
    from unittest.mock import MagicMock, patch

    mock_skill_loader = MagicMock()
    mock_skill_loader.load_all.return_value = []

    mgr = SessionManager(timeout=60, skill_loader=mock_skill_loader)

    mock_session = _make_mock_session()
    with patch("archon.ai.session_manager.ClaudeSession", return_value=mock_session):
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
    with patch("archon.ai.session_manager.ClaudeSession", return_value=mock_session):
        await mgr.get_or_create(user_id=1)

    mock_plugin_loader.get_skills.assert_called_once()
    mock_plugin_loader.get_sdk_configs.assert_called_once()


async def test_default_factory_passes_model_to_session() -> None:
    """Default factory must pass the current _model to ClaudeSession."""
    from unittest.mock import MagicMock, call, patch

    mgr = SessionManager(timeout=60)
    mgr.set_model("claude-opus-4-5")

    mock_session = _make_mock_session()
    with patch("archon.ai.session_manager.ClaudeSession", return_value=mock_session) as MockSession:
        await mgr.get_or_create(user_id=1)

    _, kwargs = MockSession.call_args
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
    with patch("archon.ai.session_manager.ClaudeSession", return_value=mock_session):
        sm = SessionManager(timeout=60, background_agent_mcp_server=mock_server)
        await sm.get_or_create(user_id=42)

    mock_server.mcp_url_for.assert_called_once_with(42)


async def test_get_or_create_passes_mcp_url_to_claude_session() -> None:
    """URL returned by mcp_url_for must be forwarded as background_agent_mcp_url to ClaudeSession."""
    from unittest.mock import patch

    mock_server = MagicMock()
    mock_server.mcp_url_for.return_value = "http://localhost:18182/mcp/7"

    mock_session = _make_mock_session()
    with patch("archon.ai.session_manager.ClaudeSession", return_value=mock_session) as MockClaude:
        sm = SessionManager(timeout=60, background_agent_mcp_server=mock_server)
        await sm.get_or_create(user_id=7)

    _, kwargs = MockClaude.call_args
    assert kwargs.get("background_agent_mcp_url") == "http://localhost:18182/mcp/7"


async def test_get_or_create_no_mcp_url_when_server_none() -> None:
    """When background_agent_mcp_server is None, background_agent_mcp_url passed to ClaudeSession must be None."""
    from unittest.mock import patch

    mock_session = _make_mock_session()
    with patch("archon.ai.session_manager.ClaudeSession", return_value=mock_session) as MockClaude:
        sm = SessionManager(timeout=60, background_agent_mcp_server=None)
        await sm.get_or_create(user_id=1)

    _, kwargs = MockClaude.call_args
    assert kwargs.get("background_agent_mcp_url") is None


async def test_get_or_create_passes_spawn_rule_to_claude_session() -> None:
    """spawn_rule must be forwarded to ClaudeSession by the default factory."""
    from unittest.mock import patch

    mock_session = _make_mock_session()
    with patch("archon.ai.session_manager.ClaudeSession", return_value=mock_session) as MockClaude:
        sm = SessionManager(timeout=60, spawn_rule="eager")
        await sm.get_or_create(user_id=1)

    _, kwargs = MockClaude.call_args
    assert kwargs.get("spawn_rule") == "eager"
