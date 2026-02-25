"""S5.4 — Graceful shutdown e2e test.

Runs Gateway._run() in a background task with:
- A real SessionManager pre-loaded with a mock ClaudeSession
- A signal-responsive start_polling mock (mirrors aiogram's handle_signals=True)
- Mock bot and config boundaries

Sends SIGINT via os.kill(), waits for shutdown, and verifies all conditions.
"""
import asyncio
import logging
import os
import signal
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.claude_session import ClaudeSession
from archon.ai.session_manager import SessionManager
from archon.config.loader import AccessConfig, Config, LoggingConfig, OutputConfig, SessionConfig
from archon.gateway.gateway import Gateway


def _make_mcp_mock() -> MagicMock:
    """Return a MagicMock for ArchonMCPServer that won't attempt any port binding."""
    m = MagicMock()
    m.start = AsyncMock()
    m.stop = AsyncMock()
    return m


_USER_ID = 42


def _make_config() -> Config:
    return Config(
        telegram_bot_token="12345:fake",
        access=AccessConfig(allowed_user_ids=[_USER_ID]),
        session=SessionConfig(working_directory="/tmp"),
        output=OutputConfig(),
        logging=LoggingConfig(),
    )


def _make_mock_session() -> MagicMock:
    """Return a mock ClaudeSession that tracks is_alive across stop()."""
    session = MagicMock(spec=ClaudeSession)
    session.start = AsyncMock()
    session.is_alive = True

    async def _stop() -> None:
        session.is_alive = False

    session.stop = AsyncMock(side_effect=_stop)
    return session


async def _signal_responsive_polling(*args: object, **kwargs: object) -> None:
    """Simulate aiogram's start_polling(handle_signals=True): stop on SIGINT/SIGTERM."""
    loop = asyncio.get_event_loop()
    stop = asyncio.Event()
    loop.add_signal_handler(signal.SIGINT, stop.set)
    loop.add_signal_handler(signal.SIGTERM, stop.set)
    try:
        await stop.wait()
    finally:
        loop.remove_signal_handler(signal.SIGINT)
        loop.remove_signal_handler(signal.SIGTERM)


async def test_sigint_triggers_graceful_shutdown(caplog: pytest.LogCaptureFixture) -> None:
    """Full shutdown e2e: SIGINT → stop_all → bot disconnect within 5s."""
    mock_session = _make_mock_session()
    mgr = SessionManager(timeout=60, session_factory=lambda _: mock_session)
    await mgr.get_or_create(user_id=_USER_ID)  # pre-load one active session

    mock_bot = MagicMock()
    mock_bot.session = MagicMock()
    mock_bot.session.close = AsyncMock()

    mock_dp = MagicMock()
    mock_dp.start_polling = _signal_responsive_polling

    with (
        patch("archon.config.loader.load_config", return_value=_make_config()),
        patch("archon.gateway.gateway.setup_logging"),
        patch("archon.gateway.gateway.SessionManager", return_value=mgr),
        patch("archon.gateway.gateway.create_bot", return_value=mock_bot),
        patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp),
        patch("archon.gateway.gateway._setup_dp"),
        patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_mcp_mock()),
        caplog.at_level(logging.INFO, logger="archon"),
    ):
        task = asyncio.create_task(Gateway._run())
        await asyncio.sleep(0.05)  # let gateway reach start_polling

        start = time.monotonic()
        os.kill(os.getpid(), signal.SIGINT)

        await asyncio.wait_for(task, timeout=10.0)
        elapsed = time.monotonic() - start

    # All sessions stopped
    assert not mock_session.is_alive
    mock_session.stop.assert_awaited_once()

    # Bot session disconnected
    mock_bot.session.close.assert_awaited_once()

    # Shutdown completed within 5 seconds
    assert elapsed < 5.0

    # Log messages present
    messages = [r.message for r in caplog.records]
    assert any("shutdown initiated" in m for m in messages)
    assert any("shutdown complete" in m for m in messages)
