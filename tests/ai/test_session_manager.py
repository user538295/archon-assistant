"""Tests for SessionManager — S1.4."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

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
