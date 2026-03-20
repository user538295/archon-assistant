"""Tests for archon_status and archon_restart tools — Tasks 1.5 & 1.6."""
import json
import time
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp.test_utils import TestClient, TestServer

from archon.ai.archon_toolkit import ArchonToolkit
from archon.ai.restart_coordinator import RestartCoordinator


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_toolkit(
    *,
    session_manager: object | None = None,
    bg_manager: object | None = None,
    config: object | None = None,
    restart_coordinator: object | None = None,
    gateway_started_at: float | None = None,
) -> ArchonToolkit:
    return ArchonToolkit(
        session_manager=session_manager,
        bg_manager=bg_manager,
        config=config,
        restart_coordinator=restart_coordinator,
        gateway_started_at=gateway_started_at,
    )


def _rpc(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    req: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        req["params"] = params
    return req


# ──────────────────────────────────────────────────────────────────
# test_archon_status_returns_json
# ──────────────────────────────────────────────────────────────────


class TestArchonStatusReturnsJson:
    async def test_archon_status_returns_json(self) -> None:
        """With all deps mocked, archon_status returns JSON with all 6 keys."""
        sm = MagicMock()
        sm.processing_sessions.return_value = {1: 5.0}
        sm.get_model.return_value = "claude-sonnet-4-6"

        bg = MagicMock()
        mock_agent = MagicMock()
        bg.list_running.return_value = [mock_agent]

        cfg = MagicMock()
        cfg.notifications.mode = "normal"

        rc = MagicMock()
        rc.is_scheduled = False

        started_at = time.monotonic() - 100

        toolkit = _make_toolkit(
            session_manager=sm,
            bg_manager=bg,
            config=cfg,
            restart_coordinator=rc,
            gateway_started_at=started_at,
        )

        result = await toolkit.call_tool("archon_status", {}, user_id=42)
        data = json.loads(result)

        assert data["uptime_seconds"] >= 100
        assert data["processing_sessions"] == 1
        assert data["running_agents"] == 1
        assert data["notification_mode"] == "normal"
        assert data["model"] == "claude-sonnet-4-6"
        assert data["restart_scheduled"] is False


# ──────────────────────────────────────────────────────────────────
# test_archon_status_missing_deps_partial
# ──────────────────────────────────────────────────────────────────


class TestArchonStatusMissingDeps:
    async def test_archon_status_missing_deps_partial(self) -> None:
        """With only config, missing deps degrade to defaults."""
        cfg = MagicMock()
        cfg.notifications.mode = "normal"

        toolkit = _make_toolkit(config=cfg)

        result = await toolkit.call_tool("archon_status", {})
        data = json.loads(result)

        assert data["uptime_seconds"] == 0
        assert data["processing_sessions"] == 0
        assert data["running_agents"] == 0
        assert data["notification_mode"] == "normal"
        assert data["model"] == "unknown"
        assert data["restart_scheduled"] is False


# ──────────────────────────────────────────────────────────────────
# test_archon_status_via_bg_mcp
# ──────────────────────────────────────────────────────────────────


class TestArchonStatusViaBgMcp:
    async def test_archon_status_via_bg_mcp(self) -> None:
        """archon_status appears in tools/list and returns valid JSON via tools/call."""
        from archon.ai.archon_mcp_server import ArchonMCPServer
        from archon.ai.background_agent_manager import BackgroundAgentManager

        bot = MagicMock()
        bot.send_message = AsyncMock()
        sm = MagicMock()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm)

        cfg = MagicMock()
        cfg.notifications.mode = "verbose"

        toolkit = _make_toolkit(config=cfg, gateway_started_at=time.monotonic() - 10)

        server = ArchonMCPServer(
            manager=manager, host="127.0.0.1", port=18294, toolkit=toolkit,
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            # tools/list
            resp = await client.post(
                "/mcp/42",
                json=_rpc("tools/list"),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            tool_names = {t["name"] for t in data["result"]["tools"]}
            assert "archon_status" in tool_names

            # tools/call
            resp = await client.post(
                "/mcp/42",
                json=_rpc("tools/call", {"name": "archon_status", "arguments": {}}),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
            status = json.loads(data["result"]["content"][0]["text"])
            assert "uptime_seconds" in status
        finally:
            await client.close()


# ──────────────────────────────────────────────────────────────────
# test_archon_status_via_router_mcp
# ──────────────────────────────────────────────────────────────────


class TestArchonStatusViaOrchMcp:
    async def test_archon_status_via_router_mcp(self, tmp_path) -> None:
        """archon_status appears in router tools/list and returns valid JSON via tools/call."""
        from archon.ai.archon_router_mcp_server import ArchonRouterMCPServer

        cfg = MagicMock()
        cfg.notifications.mode = "quiet"

        toolkit = _make_toolkit(config=cfg, gateway_started_at=time.monotonic() - 5)

        server = ArchonRouterMCPServer(
            history_root=str(tmp_path), toolkit=toolkit,
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            # tools/list
            resp = await client.post(
                "/mcp",
                json=_rpc("tools/list"),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            tool_names = {t["name"] for t in data["result"]["tools"]}
            assert "archon_status" in tool_names

            # tools/call
            resp = await client.post(
                "/mcp",
                json=_rpc("tools/call", {"name": "archon_status", "arguments": {}}),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
            status = json.loads(data["result"]["content"][0]["text"])
            assert "uptime_seconds" in status
            assert status["notification_mode"] == "quiet"
        finally:
            await client.close()


# ──────────────────────────────────────────────────────────────────
# test_archon_status_full_stack
# ──────────────────────────────────────────────────────────────────


class TestArchonStatusFullStack:
    async def test_archon_status_full_stack(self) -> None:
        """With real RestartCoordinator + mocked deps, call_tool returns correct values."""
        sm = MagicMock()
        sm.processing_sessions.return_value = {}
        sm.get_model.return_value = "claude-sonnet-4-6"

        bg = MagicMock()
        bg.list_running.return_value = []

        cfg = MagicMock()
        cfg.notifications.mode = "debug"

        rc = RestartCoordinator()

        started_at = time.monotonic() - 0.1

        toolkit = _make_toolkit(
            session_manager=sm,
            bg_manager=bg,
            config=cfg,
            restart_coordinator=rc,
            gateway_started_at=started_at,
        )

        result = await toolkit.call_tool("archon_status", {}, user_id=99)
        data = json.loads(result)

        assert data["uptime_seconds"] > 0
        assert data["restart_scheduled"] is False
        assert data["processing_sessions"] == 0
        assert data["running_agents"] == 0
        assert data["notification_mode"] == "debug"
        assert data["model"] == "claude-sonnet-4-6"


# ══════════════════════════════════════════════════════════════════
# archon_restart tests — Task 1.6
# ══════════════════════════════════════════════════════════════════


class TestArchonRestartSchedules:
    async def test_archon_restart_schedules(self) -> None:
        """Happy path: coordinator schedules restart and returns confirmation."""
        rc = MagicMock()
        rc.check_restart_allowed.return_value = True
        rc.schedule.return_value = "Restart scheduled in 10s: config change"

        toolkit = _make_toolkit(restart_coordinator=rc)
        result = await toolkit.call_tool(
            "archon_restart",
            {"reason": "config change", "delay_seconds": 10},
        )

        rc.schedule.assert_called_once_with("config change", 10)
        assert "Restart scheduled" in result


class TestArchonRestartClampsDelay:
    async def test_archon_restart_clamps_delay(self) -> None:
        """Delay is clamped to [2.0, 60.0] range."""
        rc = MagicMock()
        rc.check_restart_allowed.return_value = True
        rc.schedule.return_value = "ok"

        toolkit = _make_toolkit(restart_coordinator=rc)

        # Too low — clamped to 2.0
        await toolkit.call_tool(
            "archon_restart", {"reason": "low", "delay_seconds": 0.5},
        )
        rc.schedule.assert_called_with("low", 2.0)

        # Too high — clamped to 60.0
        await toolkit.call_tool(
            "archon_restart", {"reason": "high", "delay_seconds": 100},
        )
        rc.schedule.assert_called_with("high", 60.0)


class TestArchonRestartAlreadyScheduled:
    async def test_archon_restart_already_scheduled(self) -> None:
        """If coordinator.schedule raises RuntimeError, return friendly message."""
        rc = MagicMock()
        rc.check_restart_allowed.return_value = True
        rc.schedule.side_effect = RuntimeError("Restart already scheduled")

        toolkit = _make_toolkit(restart_coordinator=rc)
        result = await toolkit.call_tool(
            "archon_restart", {"reason": "test"},
        )

        assert result == "Restart already scheduled."


class TestArchonRestartMissingCoordinator:
    async def test_archon_restart_missing_coordinator_raises(self) -> None:
        """Without restart_coordinator, call_tool raises RuntimeError."""
        toolkit = _make_toolkit(restart_coordinator=None)

        with pytest.raises(RuntimeError, match="restart_coordinator not available"):
            await toolkit.call_tool("archon_restart", {"reason": "test"})


class TestArchonRestartRateLimited:
    async def test_archon_restart_rate_limited_cross_process(self) -> None:
        """If check_restart_allowed returns False, return rate-limit message."""
        rc = MagicMock()
        rc.check_restart_allowed.return_value = False

        toolkit = _make_toolkit(restart_coordinator=rc)
        result = await toolkit.call_tool(
            "archon_restart", {"reason": "test"},
        )

        assert result == "Restart denied: last restart was less than 60s ago."
        rc.schedule.assert_not_called()


class TestArchonRestartViaBgMcp:
    async def test_archon_restart_via_bg_mcp(self) -> None:
        """archon_restart is listed and callable via the background MCP server."""
        from archon.ai.archon_mcp_server import ArchonMCPServer
        from archon.ai.background_agent_manager import BackgroundAgentManager

        bot = MagicMock()
        bot.send_message = AsyncMock()
        sm = MagicMock()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm)

        rc = MagicMock()
        rc.check_restart_allowed.return_value = True
        rc.schedule.return_value = "ok"

        toolkit = _make_toolkit(restart_coordinator=rc)

        server = ArchonMCPServer(
            manager=manager, host="127.0.0.1", port=18295, toolkit=toolkit,
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            # tools/list
            resp = await client.post(
                "/mcp/42",
                json=_rpc("tools/list"),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            tool_names = {t["name"] for t in data["result"]["tools"]}
            assert "archon_restart" in tool_names

            # tools/call
            resp = await client.post(
                "/mcp/42",
                json=_rpc(
                    "tools/call",
                    {"name": "archon_restart", "arguments": {"reason": "mcp test"}},
                ),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
        finally:
            await client.close()


class TestArchonRestartViaOrchMcp:
    async def test_archon_restart_via_router_mcp(self, tmp_path: Path) -> None:
        """archon_restart is listed and callable via the orchestrator MCP server."""
        from archon.ai.archon_router_mcp_server import ArchonRouterMCPServer

        rc = MagicMock()
        rc.check_restart_allowed.return_value = True
        rc.schedule.return_value = "ok"

        toolkit = _make_toolkit(restart_coordinator=rc)

        server = ArchonRouterMCPServer(
            history_root=str(tmp_path), toolkit=toolkit,
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            # tools/list
            resp = await client.post(
                "/mcp",
                json=_rpc("tools/list"),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            tool_names = {t["name"] for t in data["result"]["tools"]}
            assert "archon_restart" in tool_names

            # tools/call
            resp = await client.post(
                "/mcp",
                json=_rpc(
                    "tools/call",
                    {"name": "archon_restart", "arguments": {"reason": "router test"}},
                ),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
        finally:
            await client.close()


class TestArchonRestartThenStatus:
    async def test_archon_restart_then_status_shows_scheduled(self) -> None:
        """After scheduling restart with real coordinator, status shows restart_scheduled=True."""
        sm = MagicMock()
        sm.processing_sessions.return_value = {}
        sm.get_model.return_value = "default"

        bg = MagicMock()
        bg.list_running.return_value = []

        cfg = MagicMock()
        cfg.notifications.mode = "normal"

        rc = RestartCoordinator()

        toolkit = _make_toolkit(
            session_manager=sm,
            bg_manager=bg,
            config=cfg,
            restart_coordinator=rc,
            gateway_started_at=time.monotonic(),
        )

        try:
            # Schedule with real coordinator — need check_restart_allowed to pass
            with patch.object(
                RestartCoordinator, "check_restart_allowed", return_value=True,
            ):
                result = await toolkit.call_tool(
                    "archon_restart", {"reason": "test", "delay_seconds": 30},
                )
            assert "Restart scheduled" in result

            # Now status should reflect the pending restart
            status_raw = await toolkit.call_tool("archon_status", {}, user_id=1)
            status = json.loads(status_raw)
            assert status["restart_scheduled"] is True
        finally:
            # Cleanup: cancel the pending restart so the asyncio task doesn't leak
            rc.cancel()


class TestArchonRestartDefaultDelay:
    async def test_archon_restart_default_delay(self) -> None:
        """When delay_seconds is omitted, default 5.0 is passed to schedule()."""
        rc = MagicMock()
        rc.check_restart_allowed.return_value = True
        rc.schedule.return_value = "ok"

        toolkit = _make_toolkit(restart_coordinator=rc)
        await toolkit.call_tool("archon_restart", {"reason": "no delay"})

        rc.schedule.assert_called_once_with("no delay", 5.0)


class TestArchonRestartRateLimitWithRealFile:
    async def test_archon_restart_rate_limit_real_file(self, tmp_path: Path) -> None:
        """Rate limiter works with a real timestamp file (not mocked)."""
        rc = RestartCoordinator()
        restart_file = tmp_path / ".last_restart"

        # Write a recent timestamp — should be rate-limited
        rc.write_restart_timestamp(restart_file)
        assert rc.check_restart_allowed(restart_file) is False

        # Write an old timestamp — should be allowed
        restart_file.write_text(str(time.time() - 120))
        assert rc.check_restart_allowed(restart_file) is True
