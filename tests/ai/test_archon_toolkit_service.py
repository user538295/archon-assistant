"""Tests for archon_status tool — Task 1.5."""
import json
import time

import pytest
from unittest.mock import AsyncMock, MagicMock
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
# test_archon_status_via_orch_mcp
# ──────────────────────────────────────────────────────────────────


class TestArchonStatusViaOrchMcp:
    async def test_archon_status_via_orch_mcp(self, tmp_path) -> None:
        """archon_status appears in orch tools/list and returns valid JSON via tools/call."""
        from archon.ai.archon_orch_mcp_server import ArchonOrchestratorMCPServer

        cfg = MagicMock()
        cfg.notifications.mode = "quiet"

        toolkit = _make_toolkit(config=cfg, gateway_started_at=time.monotonic() - 5)

        server = ArchonOrchestratorMCPServer(
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
