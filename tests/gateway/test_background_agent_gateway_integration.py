"""Gateway integration tests for Background Agent Execution (FR.014) — S15.4.

Verifies that Gateway._run() and _setup_dp() correctly wire the background agent
components (ArchonMCPServer and BackgroundAgentManager):

  - _setup_dp injects background_agent_manager into the dispatcher data bag
  - Gateway._run() always instantiates ArchonMCPServer and BackgroundAgentManager
  - Gateway._run() calls bg_mcp_server.start() before dp.start_polling()
  - Gateway._run() calls bg_manager.stop_all() and bg_mcp_server.stop() on shutdown
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.chat.bot import create_dispatcher
from archon.config.loader import (
    AccessConfig,
    BackgroundAgentsConfig,
    Config,
    LoggingConfig,
    OutputConfig,
    SessionConfig,
)
from archon.gateway.gateway import Gateway, _setup_dp


# ── Config / mock helpers ──────────────────────────────────────────


def _make_config() -> Config:
    return Config(
        telegram_bot_token="12345:fake",
        access=AccessConfig(allowed_user_ids=[1]),
        session=SessionConfig(working_directory="/tmp"),
        output=OutputConfig(),
        logging=LoggingConfig(),
        background_agents=BackgroundAgentsConfig(
            spawn_rule="auto",
            max_parallel=5,
            host="localhost",
            port=18299,  # non-default port to avoid conflicts with a live server
        ),
    )


def _make_mock_bot() -> MagicMock:
    bot = MagicMock()
    bot.session = MagicMock()
    bot.session.close = AsyncMock()
    return bot


def _make_mock_session_manager() -> MagicMock:
    """Return a MagicMock whose async methods are properly set as AsyncMocks."""
    sm = MagicMock()
    sm.stop_all = AsyncMock()
    return sm


def _make_mock_dp(polling_side_effect=None) -> MagicMock:
    dp = MagicMock()
    dp.start_polling = AsyncMock(side_effect=polling_side_effect)
    dp.startup = MagicMock()
    return dp


# ── Group 1: _setup_dp wiring ──────────────────────────────────────


class TestSetupDpBackgroundAgentWiring:
    def test_setup_dp_injects_background_agent_manager(self) -> None:
        """_setup_dp must bind background_agent_manager into dp['background_agent_manager']."""
        cfg = _make_config()
        dp = create_dispatcher()
        mock_sm = MagicMock()
        mock_bg_manager = MagicMock()

        _setup_dp(dp, cfg, mock_sm, background_agent_manager=mock_bg_manager)

        assert dp["background_agent_manager"] is mock_bg_manager

    def test_setup_dp_background_agent_manager_is_none_by_default(self) -> None:
        """_setup_dp must set dp['background_agent_manager'] to None when the arg is omitted."""
        cfg = _make_config()
        dp = create_dispatcher()
        mock_sm = MagicMock()

        _setup_dp(dp, cfg, mock_sm)

        assert dp["background_agent_manager"] is None


# ── Group 2: Gateway._run() ───────────────────────────────────────


class TestGatewayRunWithBackgroundAgents:
    async def test_gateway_instantiates_mcp_server(self) -> None:
        """Gateway._run() must always instantiate ArchonMCPServer."""
        mock_bg_server = MagicMock()
        mock_bg_server.start = AsyncMock()
        mock_bg_server.stop = AsyncMock()

        mock_bg_manager = MagicMock()
        mock_bg_manager.stop_all = AsyncMock()

        mock_orch_mcp = MagicMock()
        mock_orch_mcp.start = AsyncMock()
        mock_orch_mcp.stop = AsyncMock()

        with (
            patch("archon.config.loader.load_config", return_value=_make_config()),
            patch("archon.gateway.gateway.setup_logging"),
            patch("archon.gateway.gateway.SessionManager", return_value=_make_mock_session_manager()),
            patch("archon.gateway.gateway.create_bot", return_value=_make_mock_bot()),
            patch("archon.gateway.gateway.create_dispatcher", return_value=_make_mock_dp()),
            patch("archon.gateway.gateway.ArchonMCPServer", return_value=mock_bg_server) as MockMCPServer,
            patch("archon.gateway.gateway.BackgroundAgentManager", return_value=mock_bg_manager),
            patch("archon.gateway.gateway.ArchonOrchestratorMCPServer", return_value=mock_orch_mcp),
            patch("archon.gateway.gateway._setup_dp"),
            patch("archon.gateway.gateway.CronScheduler") as MockCron,
        ):
            MockCron.return_value.start = AsyncMock()
            MockCron.return_value.stop = AsyncMock()
            await Gateway._run()

        MockMCPServer.assert_called_once()
        # Verify config values are forwarded
        _, kwargs = MockMCPServer.call_args
        assert kwargs.get("host") == "localhost"
        assert kwargs.get("port") == 18299

    async def test_gateway_instantiates_bg_manager(self) -> None:
        """Gateway._run() must always instantiate BackgroundAgentManager."""
        mock_bg_server = MagicMock()
        mock_bg_server.start = AsyncMock()
        mock_bg_server.stop = AsyncMock()

        mock_bg_manager = MagicMock()
        mock_bg_manager.stop_all = AsyncMock()

        mock_orch_mcp = MagicMock()
        mock_orch_mcp.start = AsyncMock()
        mock_orch_mcp.stop = AsyncMock()

        with (
            patch("archon.config.loader.load_config", return_value=_make_config()),
            patch("archon.gateway.gateway.setup_logging"),
            patch("archon.gateway.gateway.SessionManager", return_value=_make_mock_session_manager()),
            patch("archon.gateway.gateway.create_bot", return_value=_make_mock_bot()),
            patch("archon.gateway.gateway.create_dispatcher", return_value=_make_mock_dp()),
            patch("archon.gateway.gateway.ArchonMCPServer", return_value=mock_bg_server),
            patch("archon.gateway.gateway.BackgroundAgentManager", return_value=mock_bg_manager) as MockBGManager,
            patch("archon.gateway.gateway.ArchonOrchestratorMCPServer", return_value=mock_orch_mcp),
            patch("archon.gateway.gateway._setup_dp"),
            patch("archon.gateway.gateway.CronScheduler") as MockCron,
        ):
            MockCron.return_value.start = AsyncMock()
            MockCron.return_value.stop = AsyncMock()
            await Gateway._run()

        MockBGManager.assert_called_once()

    async def test_gateway_starts_mcp_server_before_polling(self) -> None:
        """Gateway._run() must await bg_mcp_server.start() before dp.start_polling()."""
        call_order: list[str] = []

        mock_bg_server = MagicMock()

        async def _server_start() -> None:
            call_order.append("mcp_start")

        async def _polling(*args, **kwargs) -> None:
            call_order.append("polling")

        mock_bg_server.start = _server_start
        mock_bg_server.stop = AsyncMock()

        mock_bg_manager = MagicMock()
        mock_bg_manager.stop_all = AsyncMock()

        mock_dp = _make_mock_dp(polling_side_effect=_polling)

        mock_orch_mcp = MagicMock()
        mock_orch_mcp.start = AsyncMock()
        mock_orch_mcp.stop = AsyncMock()

        with (
            patch("archon.config.loader.load_config", return_value=_make_config()),
            patch("archon.gateway.gateway.setup_logging"),
            patch("archon.gateway.gateway.SessionManager", return_value=_make_mock_session_manager()),
            patch("archon.gateway.gateway.create_bot", return_value=_make_mock_bot()),
            patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp),
            patch("archon.gateway.gateway.ArchonMCPServer", return_value=mock_bg_server),
            patch("archon.gateway.gateway.BackgroundAgentManager", return_value=mock_bg_manager),
            patch("archon.gateway.gateway.ArchonOrchestratorMCPServer", return_value=mock_orch_mcp),
            patch("archon.gateway.gateway._setup_dp"),
            patch("archon.gateway.gateway.CronScheduler") as MockCron,
        ):
            MockCron.return_value.start = AsyncMock()
            MockCron.return_value.stop = AsyncMock()
            await Gateway._run()

        assert call_order == ["mcp_start", "polling"], (
            f"Expected mcp_start before polling, got: {call_order}"
        )

    async def test_gateway_stops_bg_manager_and_mcp_server_on_shutdown(self) -> None:
        """Gateway._run() must call bg_manager.stop_all() and bg_mcp_server.stop() in the finally block."""
        mock_bg_server = MagicMock()
        mock_bg_server.start = AsyncMock()
        mock_bg_server.stop = AsyncMock()

        mock_bg_manager = MagicMock()
        mock_bg_manager.stop_all = AsyncMock()

        mock_orch_mcp = MagicMock()
        mock_orch_mcp.start = AsyncMock()
        mock_orch_mcp.stop = AsyncMock()

        with (
            patch("archon.config.loader.load_config", return_value=_make_config()),
            patch("archon.gateway.gateway.setup_logging"),
            patch("archon.gateway.gateway.SessionManager", return_value=_make_mock_session_manager()),
            patch("archon.gateway.gateway.create_bot", return_value=_make_mock_bot()),
            patch("archon.gateway.gateway.create_dispatcher", return_value=_make_mock_dp()),
            patch("archon.gateway.gateway.ArchonMCPServer", return_value=mock_bg_server),
            patch("archon.gateway.gateway.BackgroundAgentManager", return_value=mock_bg_manager),
            patch("archon.gateway.gateway.ArchonOrchestratorMCPServer", return_value=mock_orch_mcp),
            patch("archon.gateway.gateway._setup_dp"),
            patch("archon.gateway.gateway.CronScheduler") as MockCron,
        ):
            MockCron.return_value.start = AsyncMock()
            MockCron.return_value.stop = AsyncMock()
            await Gateway._run()

        mock_bg_manager.stop_all.assert_awaited_once()
        mock_bg_server.stop.assert_awaited_once()
