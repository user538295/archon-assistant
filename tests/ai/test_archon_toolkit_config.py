"""Tests for get_model and set_model tools — Task 5.1."""
import json
import logging
import pytest
from unittest.mock import AsyncMock, MagicMock

from aiohttp.test_utils import TestClient, TestServer

from archon.ai.archon_toolkit import ArchonToolkit


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_session_manager(model: str = "claude-sonnet-4-6") -> MagicMock:
    sm = MagicMock()
    sm.get_model.return_value = model
    sm.set_model = MagicMock()
    return sm


def _make_config(
    default: str = "claude-sonnet-4-6",
    available: list[str] | None = None,
) -> MagicMock:
    if available is None:
        available = ["claude-sonnet-4-6", "claude-haiku-4-5"]
    config = MagicMock()
    config.models.default = default
    config.models.available = available
    return config


def _make_toolkit(
    *,
    session_manager: object | None = None,
    config: object | None = None,
) -> ArchonToolkit:
    return ArchonToolkit(session_manager=session_manager, config=config)


def _rpc(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    req: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        req["params"] = params
    return req


# ──────────────────────────────────────────────────────────────────
# Unit tests — get_model
# ──────────────────────────────────────────────────────────────────


class TestGetModelReturnsCurrent:
    async def test_get_model_returns_current(self) -> None:
        """get_model returns the model from session_manager."""
        sm = _make_session_manager(model="claude-haiku-4-5")
        toolkit = _make_toolkit(session_manager=sm)

        result = await toolkit.call_tool("get_model", {})

        sm.get_model.assert_called_once()
        assert result == "claude-haiku-4-5"

    async def test_get_model_falls_back_to_config_when_no_session_manager(self) -> None:
        """get_model falls back to config.models.default when session_manager is None."""
        config = _make_config(default="claude-sonnet-4-6")
        toolkit = _make_toolkit(session_manager=None, config=config)

        result = await toolkit.call_tool("get_model", {})

        assert result == "claude-sonnet-4-6"

    async def test_get_model_falls_back_to_config_when_session_returns_none(self) -> None:
        """get_model falls back to config.models.default when session_manager.get_model() returns None."""
        sm = MagicMock()
        sm.get_model.return_value = None
        config = _make_config(default="claude-sonnet-4-6")
        toolkit = _make_toolkit(session_manager=sm, config=config)

        result = await toolkit.call_tool("get_model", {})

        assert result == "claude-sonnet-4-6"

    async def test_get_model_raises_when_neither_available(self) -> None:
        """get_model raises RuntimeError when both session_manager and config are None."""
        toolkit = _make_toolkit(session_manager=None, config=None)

        with pytest.raises(RuntimeError):
            await toolkit.call_tool("get_model", {})


# ──────────────────────────────────────────────────────────────────
# Unit tests — set_model
# ──────────────────────────────────────────────────────────────────


class TestSetModelValid:
    async def test_set_model_valid(self) -> None:
        """set_model with a known model calls session_manager.set_model and returns success."""
        sm = _make_session_manager()
        config = _make_config(available=["claude-sonnet-4-6", "claude-haiku-4-5"])
        toolkit = _make_toolkit(session_manager=sm, config=config)

        result = await toolkit.call_tool("set_model", {"model": "claude-haiku-4-5"})

        sm.set_model.assert_called_once_with("claude-haiku-4-5")
        assert result == "Model set to claude-haiku-4-5."

    async def test_set_model_returns_confirmation_message(self) -> None:
        """set_model returns 'Model set to <model>.' on success."""
        sm = _make_session_manager()
        config = _make_config(available=["claude-sonnet-4-6"])
        toolkit = _make_toolkit(session_manager=sm, config=config)

        result = await toolkit.call_tool("set_model", {"model": "claude-sonnet-4-6"})

        assert result == "Model set to claude-sonnet-4-6."


class TestSetModelInvalid:
    async def test_set_model_invalid(self) -> None:
        """set_model with unknown model returns error listing available models."""
        sm = _make_session_manager()
        config = _make_config(available=["claude-sonnet-4-6", "claude-haiku-4-5"])
        toolkit = _make_toolkit(session_manager=sm, config=config)

        result = await toolkit.call_tool("set_model", {"model": "gpt-4"})

        # Should NOT call set_model on session_manager
        sm.set_model.assert_not_called()
        assert "gpt-4" in result or "invalid" in result.lower()
        assert "claude-sonnet-4-6" in result
        assert "claude-haiku-4-5" in result

    async def test_set_model_invalid_does_not_change_model(self) -> None:
        """set_model with invalid model does not mutate session_manager."""
        sm = _make_session_manager(model="claude-sonnet-4-6")
        config = _make_config(available=["claude-sonnet-4-6"])
        toolkit = _make_toolkit(session_manager=sm, config=config)

        await toolkit.call_tool("set_model", {"model": "unknown-model"})

        sm.set_model.assert_not_called()


class TestSetModelMissingDeps:
    async def test_set_model_raises_without_config(self) -> None:
        """set_model raises RuntimeError when config is not available."""
        sm = _make_session_manager()
        toolkit = _make_toolkit(session_manager=sm, config=None)

        with pytest.raises(RuntimeError):
            await toolkit.call_tool("set_model", {"model": "claude-sonnet-4-6"})

    async def test_set_model_raises_without_session_manager(self) -> None:
        """set_model raises RuntimeError when session_manager is not available."""
        config = _make_config()
        toolkit = _make_toolkit(session_manager=None, config=config)

        with pytest.raises(RuntimeError):
            await toolkit.call_tool("set_model", {"model": "claude-sonnet-4-6"})


# ──────────────────────────────────────────────────────────────────
# Integration — both MCP servers expose get_model
# ──────────────────────────────────────────────────────────────────


class TestGetModelViaBothMcp:
    async def test_get_model_via_background_mcp(self) -> None:
        """get_model is callable via the background ArchonMCPServer."""
        from archon.ai.archon_mcp_server import ArchonMCPServer
        from archon.ai.background_agent_manager import BackgroundAgentManager

        bot = MagicMock()
        bot.send_message = AsyncMock()
        sm_for_bam = MagicMock()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm_for_bam)

        sm = _make_session_manager(model="claude-haiku-4-5")
        toolkit = _make_toolkit(session_manager=sm)

        server = ArchonMCPServer(
            manager=manager, host="127.0.0.1", port=18310, toolkit=toolkit,
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            resp = await client.post(
                "/mcp/42",
                json=_rpc("tools/call", {"name": "get_model", "arguments": {}}),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
            assert "claude-haiku-4-5" in data["result"]["content"][0]["text"]
        finally:
            await client.close()

    async def test_get_model_via_orch_mcp(self, tmp_path) -> None:
        """get_model is callable via ArchonOrchestratorMCPServer."""
        from archon.ai.archon_orch_mcp_server import ArchonOrchestratorMCPServer

        sm = _make_session_manager(model="claude-sonnet-4-6")
        toolkit = _make_toolkit(session_manager=sm)

        server = ArchonOrchestratorMCPServer(
            history_root=str(tmp_path), toolkit=toolkit,
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            resp = await client.post(
                "/mcp",
                json=_rpc("tools/call", {"name": "get_model", "arguments": {}}),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
            assert "claude-sonnet-4-6" in data["result"]["content"][0]["text"]
        finally:
            await client.close()


# ──────────────────────────────────────────────────────────────────
# Integration — set_model via MCP server
# ──────────────────────────────────────────────────────────────────


class TestSetModelViaMcp:
    async def test_set_model_via_mcp(self) -> None:
        """set_model is callable via the background MCP server and changes model."""
        from archon.ai.archon_mcp_server import ArchonMCPServer
        from archon.ai.background_agent_manager import BackgroundAgentManager

        bot = MagicMock()
        bot.send_message = AsyncMock()
        sm_for_bam = MagicMock()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm_for_bam)

        sm = _make_session_manager(model="claude-sonnet-4-6")
        config = _make_config(available=["claude-sonnet-4-6", "claude-haiku-4-5"])
        toolkit = _make_toolkit(session_manager=sm, config=config)

        server = ArchonMCPServer(
            manager=manager, host="127.0.0.1", port=18311, toolkit=toolkit,
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            resp = await client.post(
                "/mcp/42",
                json=_rpc(
                    "tools/call",
                    {"name": "set_model", "arguments": {"model": "claude-haiku-4-5"}},
                ),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
            assert "claude-haiku-4-5" in data["result"]["content"][0]["text"]
            sm.set_model.assert_called_once_with("claude-haiku-4-5")
        finally:
            await client.close()


# ──────────────────────────────────────────────────────────────────
# E2E — roundtrip: set_model then get_model, verify archon_status
# ──────────────────────────────────────────────────────────────────


class TestSetModelThenGetModelRoundtrip:
    async def test_set_model_then_get_model_roundtrip(self) -> None:
        """E2E: set model, get model, assert match; archon_status also reflects change."""
        sm = _make_session_manager(model="claude-sonnet-4-6")
        config = _make_config(
            default="claude-sonnet-4-6",
            available=["claude-sonnet-4-6", "claude-haiku-4-5"],
        )
        # archon_status also reads notifications.mode — ensure it's JSON-serializable
        config.notifications.mode = "normal"

        # Simulate set_model mutating the return value of get_model
        new_model: list[str] = ["claude-sonnet-4-6"]

        def _get_model() -> str:
            return new_model[0]

        def _set_model(model: str) -> None:
            new_model[0] = model

        sm.get_model.side_effect = _get_model
        sm.set_model.side_effect = _set_model

        toolkit = _make_toolkit(session_manager=sm, config=config)

        # Set model
        set_result = await toolkit.call_tool("set_model", {"model": "claude-haiku-4-5"})
        assert set_result == "Model set to claude-haiku-4-5."

        # Get model — should reflect the change
        get_result = await toolkit.call_tool("get_model", {})
        assert get_result == "claude-haiku-4-5"

        # archon_status should also reflect the new model
        status_json = await toolkit.call_tool("archon_status", {})
        status = json.loads(status_json)
        assert status["model"] == "claude-haiku-4-5"

    async def test_set_model_audit_logged_at_warning(self, caplog) -> None:
        """set_model emits a WARNING-level audit log entry."""
        sm = _make_session_manager()
        config = _make_config(available=["claude-sonnet-4-6", "claude-haiku-4-5"])
        toolkit = _make_toolkit(session_manager=sm, config=config)

        with caplog.at_level(logging.WARNING, logger="archon"):
            await toolkit.call_tool("set_model", {"model": "claude-haiku-4-5"})

        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("set_model" in m and "claude-haiku-4-5" in m for m in warning_messages)
