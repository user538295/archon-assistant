"""S3.2 — Graceful shutdown tests.

Verifies that Gateway._run() calls stop_all(), closes the bot session,
emits the correct log messages, and enforces a 5-second timeout on cleanup.
Also covers Problem A (parallel shutdown with single budget) and Problem B (signal handlers).
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
        patch("archon.gateway.gateway.ArchonRouterMCPServer", return_value=_make_mcp_mock()),
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
        patch("archon.gateway.gateway.ArchonRouterMCPServer", return_value=_make_mcp_mock()),
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
        patch("archon.gateway.gateway.ArchonRouterMCPServer", return_value=_make_mcp_mock()),
        caplog.at_level(logging.INFO, logger="archon"),
    ):
        await Gateway._run()

    messages = [r.message for r in caplog.records]
    assert any("shutdown initiated" in m.lower() for m in messages)
    assert any("shutdown complete" in m.lower() for m in messages)


# ──────────────────────────────────────────────────────────────────
# Parallel shutdown with single 5-second budget
# ──────────────────────────────────────────────────────────────────


def _make_hanging_mock(method: str = "stop") -> MagicMock:
    """Return a mock whose given method hangs indefinitely."""
    m = MagicMock()
    m.start = AsyncMock()

    async def _hang() -> None:
        await asyncio.sleep(100)

    setattr(m, method, AsyncMock(side_effect=_hang))
    return m


async def test_slow_stop_all_is_cancelled_after_timeout() -> None:
    """stop_all() hanging beyond the budget triggers unified timeout."""
    mock_mgr, mock_bot, mock_dp = _patched_run(AsyncMock())

    async def _hang_stop_all() -> None:
        await asyncio.sleep(100)

    mock_mgr.stop_all = AsyncMock(side_effect=_hang_stop_all)

    with (
        patch("archon.config.loader.load_config", return_value=_make_config()),
        patch("archon.gateway.gateway.setup_logging"),
        patch("archon.gateway.gateway.SessionManager", return_value=mock_mgr),
        patch("archon.gateway.gateway.create_bot", return_value=mock_bot),
        patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp),
        patch("archon.gateway.gateway._setup_dp"),
        patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_mcp_mock()),
        patch("archon.gateway.gateway.ArchonRouterMCPServer", return_value=_make_mcp_mock()),
    ):
        with patch("archon.gateway.gateway._SHUTDOWN_TIMEOUT", 0.05):
            await Gateway._run()

    # stop_all was called (but timed out along with everything else)
    mock_mgr.stop_all.assert_awaited_once()


async def test_hung_component_triggers_unified_timeout(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Any single hanging component triggers the unified 'Shutdown timed out' warning."""
    import logging

    mock_mgr, mock_bot, mock_dp = _patched_run(AsyncMock())
    mock_job_scheduler = _make_hanging_mock("stop")

    with (
        patch("archon.config.loader.load_config", return_value=_make_config()),
        patch("archon.gateway.gateway.setup_logging"),
        patch("archon.gateway.gateway.SessionManager", return_value=mock_mgr),
        patch("archon.gateway.gateway.create_bot", return_value=mock_bot),
        patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp),
        patch("archon.gateway.gateway._setup_dp"),
        patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_mcp_mock()),
        patch("archon.gateway.gateway.ArchonRouterMCPServer", return_value=_make_mcp_mock()),
        patch("archon.gateway.gateway.JobScheduler", return_value=mock_job_scheduler),
        caplog.at_level(logging.WARNING, logger="archon"),
    ):
        with patch("archon.gateway.gateway._SHUTDOWN_TIMEOUT", 0.05):
            await Gateway._run()

    assert any("Shutdown timed out" in r.message for r in caplog.records)


async def test_all_components_hang_still_completes(caplog: pytest.LogCaptureFixture) -> None:
    """Even when all components hang, shutdown completes via the unified timeout."""
    import logging

    mock_mgr, mock_bot, mock_dp = _patched_run(AsyncMock())
    async def _hang() -> None:
        await asyncio.sleep(100)

    mock_mgr.stop_all = AsyncMock(side_effect=_hang)
    mock_bot.session.close = AsyncMock(side_effect=_hang)

    with (
        patch("archon.config.loader.load_config", return_value=_make_config()),
        patch("archon.gateway.gateway.setup_logging"),
        patch("archon.gateway.gateway.SessionManager", return_value=mock_mgr),
        patch("archon.gateway.gateway.create_bot", return_value=mock_bot),
        patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp),
        patch("archon.gateway.gateway._setup_dp"),
        patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_hanging_mock()),
        patch("archon.gateway.gateway.ArchonRouterMCPServer", return_value=_make_mcp_mock()),
        patch("archon.gateway.gateway.JobScheduler", return_value=_make_hanging_mock()),
        patch("archon.gateway.gateway.BackgroundAgentManager", return_value=_make_hanging_mock("stop_all")),
        caplog.at_level(logging.WARNING, logger="archon"),
    ):
        with patch("archon.gateway.gateway._SHUTDOWN_TIMEOUT", 0.05):
            await Gateway._run()

    assert any("Shutdown timed out" in r.message for r in caplog.records)


# ──────────────────────────────────────────────────────────────────
# Problem B — asyncio signal handler registration
# ──────────────────────────────────────────────────────────────────


async def test_register_signals_called_with_loop_and_callback() -> None:
    """Gateway._run() must delegate signal registration to get_runtime().register_signals()."""
    mock_mgr, mock_bot, mock_dp = _patched_run(AsyncMock())
    mock_runtime = MagicMock()
    mock_runtime.register_signals = MagicMock()

    with (
        patch("archon.config.loader.load_config", return_value=_make_config()),
        patch("archon.gateway.gateway.setup_logging"),
        patch("archon.gateway.gateway.SessionManager", return_value=mock_mgr),
        patch("archon.gateway.gateway.create_bot", return_value=mock_bot),
        patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp),
        patch("archon.gateway.gateway._setup_dp"),
        patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_mcp_mock()),
        patch("archon.gateway.gateway.ArchonRouterMCPServer", return_value=_make_mcp_mock()),
        patch("archon.gateway.gateway.get_runtime", return_value=mock_runtime),
    ):
        await Gateway._run()

    mock_runtime.register_signals.assert_called_once()
    args = mock_runtime.register_signals.call_args
    assert args[0][0] is not None, "loop argument must not be None"
    # The callback must be dp.stop_polling (an async callable)
    assert args[0][1] == mock_dp.stop_polling


async def test_bot_session_closed_after_services() -> None:
    """bot.session.close() must run AFTER all service stops complete (not in parallel)."""
    call_order: list[str] = []

    mock_mgr, mock_bot, mock_dp = _patched_run(AsyncMock())

    async def _record_stop_all() -> None:
        await asyncio.sleep(0.01)  # Simulate some work
        call_order.append("session_manager.stop_all")

    async def _record_bot_close() -> None:
        call_order.append("bot.session.close")

    mock_mgr.stop_all = AsyncMock(side_effect=_record_stop_all)
    mock_bot.session.close = AsyncMock(side_effect=_record_bot_close)

    with (
        patch("archon.config.loader.load_config", return_value=_make_config()),
        patch("archon.gateway.gateway.setup_logging"),
        patch("archon.gateway.gateway.SessionManager", return_value=mock_mgr),
        patch("archon.gateway.gateway.create_bot", return_value=mock_bot),
        patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp),
        patch("archon.gateway.gateway._setup_dp"),
        patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_mcp_mock()),
        patch("archon.gateway.gateway.ArchonRouterMCPServer", return_value=_make_mcp_mock()),
    ):
        await Gateway._run()

    assert "session_manager.stop_all" in call_order
    assert "bot.session.close" in call_order
    # bot.session.close must come after session_manager.stop_all
    assert call_order.index("session_manager.stop_all") < call_order.index("bot.session.close")


async def test_shutdown_callback_is_async() -> None:
    """The callback passed to register_signals() must be an async callable."""
    mock_mgr, mock_bot, mock_dp = _patched_run(AsyncMock())
    mock_dp.stop_polling = AsyncMock()
    captured_callback = None
    mock_runtime = MagicMock()

    def _capture_register(loop: object, callback: object) -> None:
        nonlocal captured_callback
        captured_callback = callback

    mock_runtime.register_signals = MagicMock(side_effect=_capture_register)

    with (
        patch("archon.config.loader.load_config", return_value=_make_config()),
        patch("archon.gateway.gateway.setup_logging"),
        patch("archon.gateway.gateway.SessionManager", return_value=mock_mgr),
        patch("archon.gateway.gateway.create_bot", return_value=mock_bot),
        patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp),
        patch("archon.gateway.gateway._setup_dp"),
        patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_mcp_mock()),
        patch("archon.gateway.gateway.ArchonRouterMCPServer", return_value=_make_mcp_mock()),
        patch("archon.gateway.gateway.get_runtime", return_value=mock_runtime),
    ):
        await Gateway._run()

    assert captured_callback is not None, "register_signals was not called"
    assert asyncio.iscoroutinefunction(captured_callback), "callback must be async"


# ──────────────────────────────────────────────────────────────────
# Epic 12 Task 1.1 — bg_toolkit_mcp_server stopped during shutdown
# ──────────────────────────────────────────────────────────────────


async def test_gateway_stop_all_within_5s_budget() -> None:
    """All services with slow stop complete fast thanks to parallel shutdown (asyncio.gather)."""
    import time

    mock_mgr, mock_bot, mock_dp = _patched_run(AsyncMock())

    async def _slow_stop() -> None:
        await asyncio.sleep(0.5)

    # Make all stoppable services sleep 0.5s each — sequential would take >3s
    mock_mgr.stop_all = AsyncMock(side_effect=_slow_stop)
    mock_bot.session.close = AsyncMock(side_effect=_slow_stop)
    mock_mcp = _make_mcp_mock()
    mock_mcp.stop = AsyncMock(side_effect=_slow_stop)
    mock_job_scheduler = MagicMock()
    mock_job_scheduler.start = AsyncMock()
    mock_job_scheduler.stop = AsyncMock(side_effect=_slow_stop)
    mock_job_scheduler.job_configs = []

    router_mocks: list[MagicMock] = []

    def _make_slow_router(**kwargs):
        m = _make_mcp_mock()
        m.stop = AsyncMock(side_effect=_slow_stop)
        m.mcp_url = "http://localhost:18183/mcp"
        m.token = "fake_token"
        router_mocks.append(m)
        return m

    with (
        patch("archon.config.loader.load_config", return_value=_make_config()),
        patch("archon.gateway.gateway.setup_logging"),
        patch("archon.gateway.gateway.SessionManager", return_value=mock_mgr),
        patch("archon.gateway.gateway.create_bot", return_value=mock_bot),
        patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp),
        patch("archon.gateway.gateway._setup_dp"),
        patch("archon.gateway.gateway.ArchonMCPServer", return_value=mock_mcp),
        patch("archon.gateway.gateway.ArchonRouterMCPServer", side_effect=_make_slow_router),
        patch("archon.gateway.gateway.JobScheduler", return_value=mock_job_scheduler),
    ):
        start = time.monotonic()
        await Gateway._run()
        elapsed = time.monotonic() - start

    # All services sleep 0.5s each but run in parallel via asyncio.gather.
    # Sequential would take >3s (6 × 0.5s). Parallel completes in ~0.5s + overhead.
    # Wide margin avoids CI flakiness while still proving parallelism.
    assert elapsed < 3.0, f"stop_all() took {elapsed:.1f}s — expected <3s (parallel shutdown)"


async def test_gateway_single_router_mcp_server() -> None:
    """Gateway creates exactly one ArchonRouterMCPServer (no separate bg_toolkit server)."""
    mock_mgr, mock_bot, mock_dp = _patched_run(AsyncMock())

    router_server_instances: list[MagicMock] = []

    def _make_router_server(**kwargs):
        m = _make_mcp_mock()
        m.mcp_url = "http://localhost:18183/mcp"
        m.token = "fake_token"
        router_server_instances.append(m)
        return m

    with (
        patch("archon.config.loader.load_config", return_value=_make_config()),
        patch("archon.gateway.gateway.setup_logging"),
        patch("archon.gateway.gateway.SessionManager", return_value=mock_mgr),
        patch("archon.gateway.gateway.create_bot", return_value=mock_bot),
        patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp),
        patch("archon.gateway.gateway._setup_dp"),
        patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_mcp_mock()),
        patch("archon.gateway.gateway.ArchonRouterMCPServer", side_effect=_make_router_server),
    ):
        await Gateway._run()

    # Task 1.2: only one ArchonRouterMCPServer instance (no separate bg_toolkit server)
    assert len(router_server_instances) == 1
    router_server_instances[0].stop.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────
# C1-3 — CancelledError propagates through _safe_stop
# ──────────────────────────────────────────────────────────────────


async def test_safe_stop_propagates_cancelled_error() -> None:
    """CancelledError from one stop() propagates out of the gather rather than being swallowed.

    After C1-1, _safe_stop no longer catches CancelledError. When a service's stop()
    raises CancelledError the outer asyncio.timeout() context manager absorbs it correctly.
    Concretely: the gateway must still reach 'shutdown complete' even when one service
    raises CancelledError (the timeout fires and gateway logs the timeout warning).
    """
    import logging

    mock_mgr, mock_bot, mock_dp = _patched_run(AsyncMock())

    cancelled_service_called = False
    remaining_service_called = False

    async def _raise_cancelled() -> None:
        nonlocal cancelled_service_called
        cancelled_service_called = True
        raise asyncio.CancelledError("simulated cancellation")

    async def _normal_stop() -> None:
        nonlocal remaining_service_called
        remaining_service_called = True

    mock_mgr.stop_all = AsyncMock(side_effect=_raise_cancelled)
    # Use a custom job_scheduler so we can track the remaining stop
    mock_job_scheduler = MagicMock()
    mock_job_scheduler.start = AsyncMock()
    mock_job_scheduler.stop = AsyncMock(side_effect=_normal_stop)
    mock_job_scheduler.job_configs = []

    with (
        patch("archon.config.loader.load_config", return_value=_make_config()),
        patch("archon.gateway.gateway.setup_logging"),
        patch("archon.gateway.gateway.SessionManager", return_value=mock_mgr),
        patch("archon.gateway.gateway.create_bot", return_value=mock_bot),
        patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp),
        patch("archon.gateway.gateway._setup_dp"),
        patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_mcp_mock()),
        patch("archon.gateway.gateway.ArchonRouterMCPServer", return_value=_make_mcp_mock()),
        patch("archon.gateway.gateway.JobScheduler", return_value=mock_job_scheduler),
    ):
        # Gateway must complete (not hang) — CancelledError triggers the timeout
        with patch("archon.gateway.gateway._SHUTDOWN_TIMEOUT", 0.2):
            await Gateway._run()

    # The service that raised CancelledError was indeed called
    assert cancelled_service_called
    # With return_exceptions=True, the remaining service also completes normally
    assert remaining_service_called is True
