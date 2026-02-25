"""S3.2 — Graceful shutdown tests.

Verifies that Gateway._run() calls stop_all(), closes the bot session,
emits the correct log messages, and enforces a 5-second timeout on cleanup.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.config.loader import AccessConfig, Config, LoggingConfig, OutputConfig, SessionConfig
from archon.gateway.gateway import Gateway


def _make_mcp_mock() -> MagicMock:
    """Return a MagicMock for ArchonMCPServer that won't attempt any port binding."""
    m = MagicMock()
    m.start = AsyncMock()
    m.stop = AsyncMock()
    return m


def _make_config() -> Config:
    return Config(
        telegram_bot_token="12345:fake",
        access=AccessConfig(allowed_user_ids=[1]),
        session=SessionConfig(working_directory="/tmp"),
        output=OutputConfig(),
        logging=LoggingConfig(),
    )


def _patched_run(mock_start_polling: AsyncMock) -> tuple:
    """Return (mock_mgr, mock_bot) wired into Gateway._run() with a controllable start_polling."""
    mock_mgr = MagicMock()
    mock_mgr.stop_all = AsyncMock()

    mock_bot = MagicMock()
    mock_bot.session = MagicMock()
    mock_bot.session.close = AsyncMock()

    mock_dp = MagicMock()
    mock_dp.start_polling = mock_start_polling

    return mock_mgr, mock_bot, mock_dp


# ──────────────────────────────────────────────────────────────────
# Normal shutdown path
# ──────────────────────────────────────────────────────────────────


async def test_stop_all_called_when_polling_ends() -> None:
    mock_mgr, mock_bot, mock_dp = _patched_run(AsyncMock())  # start_polling returns immediately

    with (
        patch("archon.config.loader.load_config", return_value=_make_config()),
        patch("archon.gateway.gateway.setup_logging"),
        patch("archon.gateway.gateway.SessionManager", return_value=mock_mgr),
        patch("archon.gateway.gateway.create_bot", return_value=mock_bot),
        patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp),
        patch("archon.gateway.gateway._setup_dp"),
        patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_mcp_mock()),
    ):
        await Gateway._run()

    mock_mgr.stop_all.assert_awaited_once()


async def test_bot_session_closed_when_polling_ends() -> None:
    mock_mgr, mock_bot, mock_dp = _patched_run(AsyncMock())

    with (
        patch("archon.config.loader.load_config", return_value=_make_config()),
        patch("archon.gateway.gateway.setup_logging"),
        patch("archon.gateway.gateway.SessionManager", return_value=mock_mgr),
        patch("archon.gateway.gateway.create_bot", return_value=mock_bot),
        patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp),
        patch("archon.gateway.gateway._setup_dp"),
        patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_mcp_mock()),
    ):
        await Gateway._run()

    mock_bot.session.close.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────
# Log messages
# ──────────────────────────────────────────────────────────────────


async def test_shutdown_logs_initiated_and_complete(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    mock_mgr, mock_bot, mock_dp = _patched_run(AsyncMock())

    with (
        patch("archon.config.loader.load_config", return_value=_make_config()),
        patch("archon.gateway.gateway.setup_logging"),
        patch("archon.gateway.gateway.SessionManager", return_value=mock_mgr),
        patch("archon.gateway.gateway.create_bot", return_value=mock_bot),
        patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp),
        patch("archon.gateway.gateway._setup_dp"),
        patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_mcp_mock()),
        caplog.at_level(logging.INFO, logger="archon"),
    ):
        await Gateway._run()

    messages = [r.message for r in caplog.records]
    assert any("shutdown initiated" in m for m in messages)
    assert any("shutdown complete" in m for m in messages)


# ──────────────────────────────────────────────────────────────────
# 5-second timeout on stop_all
# ──────────────────────────────────────────────────────────────────


async def test_slow_stop_all_is_cancelled_after_timeout() -> None:
    """stop_all() hanging beyond 5s must be cancelled; bot session still closes."""
    mock_mgr, mock_bot, mock_dp = _patched_run(AsyncMock())

    async def _slow_stop_all() -> None:
        await asyncio.sleep(10)  # will be cancelled by the 5s timeout

    mock_mgr.stop_all = AsyncMock(side_effect=_slow_stop_all)

    with (
        patch("archon.config.loader.load_config", return_value=_make_config()),
        patch("archon.gateway.gateway.setup_logging"),
        patch("archon.gateway.gateway.SessionManager", return_value=mock_mgr),
        patch("archon.gateway.gateway.create_bot", return_value=mock_bot),
        patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp),
        patch("archon.gateway.gateway._setup_dp"),
        patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_mcp_mock()),
    ):
        # Run with a short timeout override so the test doesn't take 5s
        with patch("archon.gateway.gateway._SHUTDOWN_TIMEOUT", 0.05):
            await Gateway._run()

    # stop_all was called (but timed out)
    mock_mgr.stop_all.assert_awaited_once()
    # bot session still closed despite timeout
    mock_bot.session.close.assert_awaited_once()
