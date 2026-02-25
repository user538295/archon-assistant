"""Tests for ArchonMCPServer — S15.3."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from archon.ai.archon_mcp_server import ArchonMCPServer
from archon.ai.background_agent_manager import AgentRun, BackgroundAgentManager


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_manager() -> BackgroundAgentManager:
    bot = MagicMock()
    bot.send_message = AsyncMock()
    sm = MagicMock()
    manager = BackgroundAgentManager(bot=bot, session_manager=sm)
    return manager


async def _post_mcp(client: TestClient, user_id: int, body: dict) -> dict:
    resp = await client.post(f"/mcp/{user_id}", json=body)
    return await resp.json()


def _rpc(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    req: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        req["params"] = params
    return req


# ──────────────────────────────────────────────────────────────────
# mcp_url_for
# ──────────────────────────────────────────────────────────────────


class TestMcpUrlFor:
    def test_default_url(self) -> None:
        manager = _make_manager()
        server = ArchonMCPServer(manager=manager)
        assert server.mcp_url_for(42) == "http://localhost:18182/mcp/42"

    def test_custom_host_and_port(self) -> None:
        manager = _make_manager()
        server = ArchonMCPServer(manager=manager, host="0.0.0.0", port=9999)
        assert server.mcp_url_for(7) == "http://0.0.0.0:9999/mcp/7"

    def test_different_user_ids_produce_different_urls(self) -> None:
        manager = _make_manager()
        server = ArchonMCPServer(manager=manager)
        assert server.mcp_url_for(1) != server.mcp_url_for(2)


# ──────────────────────────────────────────────────────────────────
# HTTP handler — test via aiohttp TestClient
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def mcp_client():
    """Provide a TestClient connected to the MCP server's aiohttp app."""
    manager = _make_manager()
    server = ArchonMCPServer(manager=manager, host="127.0.0.1", port=18299)
    # Access the internal aiohttp app directly — no need to bind a real port
    client = TestClient(TestServer(server._app))
    await client.start_server()
    yield client, manager
    await client.close()


class TestInitialize:
    async def test_initialize_returns_protocol_version(self, mcp_client) -> None:
        client, _ = mcp_client
        resp = await _post_mcp(client, 42, _rpc("initialize"))
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 1
        result = resp["result"]
        assert "protocolVersion" in result
        assert result["protocolVersion"] == "2024-11-05"

    async def test_initialize_returns_server_info(self, mcp_client) -> None:
        client, _ = mcp_client
        resp = await _post_mcp(client, 42, _rpc("initialize"))
        info = resp["result"]["serverInfo"]
        assert info["name"] == "archon-background-agents"

    async def test_initialize_returns_capabilities(self, mcp_client) -> None:
        client, _ = mcp_client
        resp = await _post_mcp(client, 42, _rpc("initialize"))
        caps = resp["result"]["capabilities"]
        assert "tools" in caps


class TestToolsList:
    async def test_tools_list_returns_one_tool(self, mcp_client) -> None:
        client, _ = mcp_client
        resp = await _post_mcp(client, 42, _rpc("tools/list"))
        tools = resp["result"]["tools"]
        assert len(tools) == 1

    async def test_tools_list_tool_name(self, mcp_client) -> None:
        client, _ = mcp_client
        resp = await _post_mcp(client, 42, _rpc("tools/list"))
        tool = resp["result"]["tools"][0]
        assert tool["name"] == "spawn_background_agent"

    async def test_tools_list_schema_has_task_required(self, mcp_client) -> None:
        client, _ = mcp_client
        resp = await _post_mcp(client, 42, _rpc("tools/list"))
        tool = resp["result"]["tools"][0]
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert "task" in schema["properties"]
        assert "task" in schema["required"]

    async def test_tools_list_schema_has_context_and_name(self, mcp_client) -> None:
        client, _ = mcp_client
        resp = await _post_mcp(client, 42, _rpc("tools/list"))
        tool = resp["result"]["tools"][0]
        schema = tool["inputSchema"]
        assert "context" in schema["properties"]
        assert "name" in schema["properties"]


class TestToolsCall:
    async def test_spawn_background_agent_happy_path(self, mcp_client) -> None:
        client, manager = mcp_client
        main_session = MagicMock(inject_context=MagicMock(), is_alive=True)
        manager._session_manager.get_or_create = AsyncMock(return_value=main_session)

        # Patch ClaudeSession so no real subprocess is started
        from archon.ai.background_agent_manager import AgentRun
        async def _fake_run_agent(run):  # noqa: ANN001
            run.status = "completed"
            run.result = "done"

        with patch.object(manager, "_run_agent", side_effect=_fake_run_agent):
            resp = await _post_mcp(
                client, 42,
                _rpc("tools/call", {"name": "spawn_background_agent",
                                     "arguments": {"task": "do something"}}),
            )

        assert resp["jsonrpc"] == "2.0"
        result = resp["result"]
        assert result["isError"] is False
        content = result["content"]
        assert len(content) == 1
        text = content[0]["text"]
        assert "started" in text.lower() or "run_id" in text.lower()

    async def test_spawn_passes_user_id_from_url(self, mcp_client) -> None:
        client, manager = mcp_client
        main_session = MagicMock(inject_context=MagicMock(), is_alive=True)
        manager._session_manager.get_or_create = AsyncMock(return_value=main_session)

        captured_user_ids: list[int] = []

        original_spawn = manager.spawn

        async def _spy_spawn(user_id: int, **kwargs: object) -> AgentRun:  # type: ignore[override]
            captured_user_ids.append(user_id)
            run = AgentRun(
                run_id="test-id", name="Atlas", task=kwargs.get("task", ""),  # type: ignore[arg-type]
                context="", user_id=user_id, started_at=0.0,
            )
            return run

        manager.spawn = _spy_spawn  # type: ignore[assignment]

        await _post_mcp(
            client, 99,
            _rpc("tools/call", {"name": "spawn_background_agent",
                                 "arguments": {"task": "check user_id"}}),
        )
        assert captured_user_ids == [99]

    async def test_spawn_passes_context_when_provided(self, mcp_client) -> None:
        client, manager = mcp_client
        captured_kwargs: list[dict] = []

        async def _spy_spawn(user_id: int, **kwargs: object) -> AgentRun:  # type: ignore[override]
            captured_kwargs.append(dict(kwargs))
            run = AgentRun(
                run_id="x", name="Nova", task=kwargs.get("task", ""),  # type: ignore[arg-type]
                context=kwargs.get("context", ""),  # type: ignore[arg-type]
                user_id=user_id, started_at=0.0,
            )
            return run

        manager.spawn = _spy_spawn  # type: ignore[assignment]

        await _post_mcp(
            client, 1,
            _rpc("tools/call", {"name": "spawn_background_agent",
                                 "arguments": {"task": "t", "context": "my context"}}),
        )
        assert captured_kwargs[0]["context"] == "my context"

    async def test_unknown_tool_name_returns_error(self, mcp_client) -> None:
        client, _ = mcp_client
        resp = await _post_mcp(
            client, 42,
            _rpc("tools/call", {"name": "nonexistent_tool", "arguments": {}}),
        )
        assert "error" in resp
        assert resp["error"]["code"] == -32602

    async def test_missing_task_param_returns_error(self, mcp_client) -> None:
        client, _ = mcp_client
        resp = await _post_mcp(
            client, 42,
            _rpc("tools/call", {"name": "spawn_background_agent", "arguments": {}}),
        )
        assert "error" in resp
        assert resp["error"]["code"] == -32602

    async def test_max_parallel_exceeded_returns_tool_error(self, mcp_client) -> None:
        """When max_parallel is exceeded, return isError=True (not a JSON-RPC error)."""
        client, manager = mcp_client

        async def _raise_runtime(*args: object, **kwargs: object) -> None:
            raise RuntimeError("Max parallel agents exceeded")

        manager.spawn = _raise_runtime  # type: ignore[assignment]

        resp = await _post_mcp(
            client, 42,
            _rpc("tools/call", {"name": "spawn_background_agent",
                                 "arguments": {"task": "overflow"}}),
        )
        assert resp["jsonrpc"] == "2.0"
        result = resp["result"]
        assert result["isError"] is True


class TestErrorHandling:
    async def test_unknown_method_returns_method_not_found(self, mcp_client) -> None:
        client, _ = mcp_client
        resp = await _post_mcp(client, 42, _rpc("unknown/method"))
        assert "error" in resp
        assert resp["error"]["code"] == -32601

    async def test_invalid_json_returns_400(self, mcp_client) -> None:
        client, _ = mcp_client
        raw = await client.post(
            "/mcp/42",
            data=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert raw.status == 400


# ──────────────────────────────────────────────────────────────────
# start / stop lifecycle
# ──────────────────────────────────────────────────────────────────


class TestLifecycle:
    async def test_server_starts_and_stops_without_error(self) -> None:
        manager = _make_manager()
        server = ArchonMCPServer(manager=manager, host="127.0.0.1", port=18298)
        await server.start()
        await server.stop()  # must not raise

    async def test_stop_without_start_is_noop(self) -> None:
        manager = _make_manager()
        server = ArchonMCPServer(manager=manager)
        await server.stop()  # must not raise
