"""S3.2 — Graceful shutdown tests.

Verifies that Gateway._run() calls stop_all(), closes the bot session,
emits the correct log messages, and enforces a 5-second timeout on cleanup.
Also covers Problem A (per-component shutdown timeouts) and Problem B (signal handlers).
"""
import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock, call, patch

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


# ──────────────────────────────────────────────────────────────────
# Problem A — per-component shutdown timeouts
# ──────────────────────────────────────────────────────────────────


def _make_hanging_mcp_mock() -> MagicMock:
    """MCP server whose stop() hangs indefinitely."""
    m = MagicMock()
    m.start = AsyncMock()

    async def _hang() -> None:
        await asyncio.sleep(100)

    m.stop = AsyncMock(side_effect=_hang)
    return m


def _make_hanging_cron_mock() -> MagicMock:
    """CronScheduler whose stop() hangs indefinitely."""
    m = MagicMock()
    m.start = AsyncMock()

    async def _hang() -> None:
        await asyncio.sleep(100)

    m.stop = AsyncMock(side_effect=_hang)
    return m


def _make_hanging_bg_manager_mock() -> MagicMock:
    """BackgroundAgentManager whose stop_all() hangs indefinitely."""
    m = MagicMock()

    async def _hang() -> None:
        await asyncio.sleep(100)

    m.stop_all = AsyncMock(side_effect=_hang)
    return m


async def test_hung_cron_scheduler_stop_times_out_and_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """cron_scheduler.stop() hanging past timeout must be cancelled with a warning logged."""
    import logging

    mock_mgr, mock_bot, mock_dp = _patched_run(AsyncMock())
    mock_cron = _make_hanging_cron_mock()

    with (
        patch("archon.config.loader.load_config", return_value=_make_config()),
        patch("archon.gateway.gateway.setup_logging"),
        patch("archon.gateway.gateway.SessionManager", return_value=mock_mgr),
        patch("archon.gateway.gateway.create_bot", return_value=mock_bot),
        patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp),
        patch("archon.gateway.gateway._setup_dp"),
        patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_mcp_mock()),
        patch("archon.gateway.gateway.CronScheduler", return_value=mock_cron),
        caplog.at_level(logging.WARNING, logger="archon"),
    ):
        with patch("archon.gateway.gateway._SHUTDOWN_TIMEOUT", 0.05):
            await Gateway._run()

    assert any("cron_scheduler.stop() timed out" in r.message for r in caplog.records)
    mock_bot.session.close.assert_awaited_once()


async def test_hung_bg_manager_stop_times_out_and_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """bg_manager.stop_all() hanging past timeout must be cancelled with a warning logged."""
    import logging

    mock_mgr, mock_bot, mock_dp = _patched_run(AsyncMock())
    mock_bg = _make_hanging_bg_manager_mock()

    with (
        patch("archon.config.loader.load_config", return_value=_make_config()),
        patch("archon.gateway.gateway.setup_logging"),
        patch("archon.gateway.gateway.SessionManager", return_value=mock_mgr),
        patch("archon.gateway.gateway.create_bot", return_value=mock_bot),
        patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp),
        patch("archon.gateway.gateway._setup_dp"),
        patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_mcp_mock()),
        patch("archon.gateway.gateway.BackgroundAgentManager", return_value=mock_bg),
        caplog.at_level(logging.WARNING, logger="archon"),
    ):
        with patch("archon.gateway.gateway._SHUTDOWN_TIMEOUT", 0.05):
            await Gateway._run()

    assert any("bg_manager.stop_all() timed out" in r.message for r in caplog.records)
    mock_bot.session.close.assert_awaited_once()


async def test_hung_mcp_server_stop_times_out_and_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """bg_mcp_server.stop() hanging past timeout must be cancelled with a warning logged."""
    import logging

    mock_mgr, mock_bot, mock_dp = _patched_run(AsyncMock())

    with (
        patch("archon.config.loader.load_config", return_value=_make_config()),
        patch("archon.gateway.gateway.setup_logging"),
        patch("archon.gateway.gateway.SessionManager", return_value=mock_mgr),
        patch("archon.gateway.gateway.create_bot", return_value=mock_bot),
        patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp),
        patch("archon.gateway.gateway._setup_dp"),
        patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_hanging_mcp_mock()),
        caplog.at_level(logging.WARNING, logger="archon"),
    ):
        with patch("archon.gateway.gateway._SHUTDOWN_TIMEOUT", 0.05):
            await Gateway._run()

    assert any("bg_mcp_server.stop() timed out" in r.message for r in caplog.records)
    mock_bot.session.close.assert_awaited_once()


async def test_all_component_timeouts_still_close_bot_session() -> None:
    """Even when cron, bg_manager, mcp_server, and session_manager all hang, bot.session closes."""
    mock_mgr, mock_bot, mock_dp = _patched_run(AsyncMock())
    mock_cron = _make_hanging_cron_mock()
    mock_bg = _make_hanging_bg_manager_mock()
    mock_mcp = _make_hanging_mcp_mock()

    async def _slow_stop_all() -> None:
        await asyncio.sleep(100)

    mock_mgr.stop_all = AsyncMock(side_effect=_slow_stop_all)

    with (
        patch("archon.config.loader.load_config", return_value=_make_config()),
        patch("archon.gateway.gateway.setup_logging"),
        patch("archon.gateway.gateway.SessionManager", return_value=mock_mgr),
        patch("archon.gateway.gateway.create_bot", return_value=mock_bot),
        patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp),
        patch("archon.gateway.gateway._setup_dp"),
        patch("archon.gateway.gateway.ArchonMCPServer", return_value=mock_mcp),
        patch("archon.gateway.gateway.CronScheduler", return_value=mock_cron),
        patch("archon.gateway.gateway.BackgroundAgentManager", return_value=mock_bg),
    ):
        with patch("archon.gateway.gateway._SHUTDOWN_TIMEOUT", 0.05):
            await Gateway._run()

    mock_bot.session.close.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────
# Problem B — asyncio signal handler registration
# ──────────────────────────────────────────────────────────────────


async def test_signal_handlers_registered_for_sigterm_and_sigint() -> None:
    """Gateway._run() must register asyncio signal handlers for SIGTERM and SIGINT."""
    mock_mgr, mock_bot, mock_dp = _patched_run(AsyncMock())
    registered_signals: list[signal.Signals] = []

    def _fake_add_signal_handler(sig: int, callback: object, *args: object) -> None:
        registered_signals.append(signal.Signals(sig))

    with (
        patch("archon.config.loader.load_config", return_value=_make_config()),
        patch("archon.gateway.gateway.setup_logging"),
        patch("archon.gateway.gateway.SessionManager", return_value=mock_mgr),
        patch("archon.gateway.gateway.create_bot", return_value=mock_bot),
        patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp),
        patch("archon.gateway.gateway._setup_dp"),
        patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_mcp_mock()),
    ):
        loop = asyncio.get_running_loop()
        with patch.object(loop, "add_signal_handler", side_effect=_fake_add_signal_handler):
            await Gateway._run()

    assert signal.SIGTERM in registered_signals, "SIGTERM handler not registered"
    assert signal.SIGINT in registered_signals, "SIGINT handler not registered"


async def test_signal_handler_calls_stop_polling_on_sigterm() -> None:
    """When SIGTERM is received, the signal handler must call dp.stop_polling()."""
    mock_mgr, mock_bot, mock_dp = _patched_run(AsyncMock())
    mock_dp.stop_polling = AsyncMock()

    captured_handlers: dict[int, tuple] = {}

    def _fake_add_signal_handler(sig: int, callback: object, *args: object) -> None:
        captured_handlers[sig] = (callback, args)

    with (
        patch("archon.config.loader.load_config", return_value=_make_config()),
        patch("archon.gateway.gateway.setup_logging"),
        patch("archon.gateway.gateway.SessionManager", return_value=mock_mgr),
        patch("archon.gateway.gateway.create_bot", return_value=mock_bot),
        patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp),
        patch("archon.gateway.gateway._setup_dp"),
        patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_mcp_mock()),
    ):
        loop = asyncio.get_running_loop()
        with patch.object(loop, "add_signal_handler", side_effect=_fake_add_signal_handler):
            await Gateway._run()

    # Invoke the captured SIGTERM handler and let the event loop process it
    assert signal.SIGTERM in captured_handlers, "SIGTERM handler was not registered"
    callback, args = captured_handlers[signal.SIGTERM]
    callback(*args)
    # Allow created tasks to run
    await asyncio.sleep(0)

    mock_dp.stop_polling.assert_awaited_once()
