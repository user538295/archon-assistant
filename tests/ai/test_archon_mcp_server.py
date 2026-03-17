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


async def _post_mcp(
    client: TestClient,
    user_id: int,
    body: dict,
    token: str | None = None,
) -> dict:
    headers: dict[str, str] = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    resp = await client.post(f"/mcp/{user_id}", json=body, headers=headers)
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
    yield client, manager, server
    await client.close()


@pytest.fixture
async def mcp_client_with_whitelist():
    """Provide a TestClient for a server with allowed_user_ids=[42, 99]."""
    manager = _make_manager()
    server = ArchonMCPServer(
        manager=manager, host="127.0.0.1", port=18296, allowed_user_ids=[42, 99]
    )
    client = TestClient(TestServer(server._app))
    await client.start_server()
    yield client, manager, server
    await client.close()


class TestInitialize:
    async def test_initialize_returns_protocol_version(self, mcp_client) -> None:
        client, _, server = mcp_client
        resp = await _post_mcp(client, 42, _rpc("initialize"), token=server.token)
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 1
        result = resp["result"]
        assert "protocolVersion" in result
        assert result["protocolVersion"] == "2024-11-05"

    async def test_initialize_returns_server_info(self, mcp_client) -> None:
        client, _, server = mcp_client
        resp = await _post_mcp(client, 42, _rpc("initialize"), token=server.token)
        info = resp["result"]["serverInfo"]
        assert info["name"] == "archon-background-agents"

    async def test_initialize_returns_capabilities(self, mcp_client) -> None:
        client, _, server = mcp_client
        resp = await _post_mcp(client, 42, _rpc("initialize"), token=server.token)
        caps = resp["result"]["capabilities"]
        assert "tools" in caps


class TestToolsList:
    async def test_tools_list_returns_one_tool(self, mcp_client) -> None:
        client, _, server = mcp_client
        resp = await _post_mcp(client, 42, _rpc("tools/list"), token=server.token)
        tools = resp["result"]["tools"]
        assert len(tools) == 1

    async def test_tools_list_tool_name(self, mcp_client) -> None:
        client, _, server = mcp_client
        resp = await _post_mcp(client, 42, _rpc("tools/list"), token=server.token)
        tool = resp["result"]["tools"][0]
        assert tool["name"] == "spawn_background_agent"

    async def test_tools_list_schema_has_task_required(self, mcp_client) -> None:
        client, _, server = mcp_client
        resp = await _post_mcp(client, 42, _rpc("tools/list"), token=server.token)
        tool = resp["result"]["tools"][0]
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert "task" in schema["properties"]
        assert "task" in schema["required"]

    async def test_tools_list_schema_has_context_but_not_name(self, mcp_client) -> None:
        """context is a valid optional parameter; name is not exposed (pool auto-assigns)."""
        client, _, server = mcp_client
        resp = await _post_mcp(client, 42, _rpc("tools/list"), token=server.token)
        tool = resp["result"]["tools"][0]
        schema = tool["inputSchema"]
        assert "context" in schema["properties"]
        assert "name" not in schema["properties"]  # name is auto-assigned from pool, not caller-controlled

    async def test_tools_list_schema_has_user_request(self, mcp_client) -> None:
        """user_request is an optional property in the tool schema (not in required)."""
        client, _, server = mcp_client
        resp = await _post_mcp(client, 42, _rpc("tools/list"), token=server.token)
        tool = resp["result"]["tools"][0]
        schema = tool["inputSchema"]
        assert "user_request" in schema["properties"], (
            "user_request must appear in spawn_background_agent inputSchema"
        )
        assert "user_request" not in schema.get("required", []), (
            "user_request must be optional (not in required)"
        )


class TestToolsCall:
    async def test_spawn_background_agent_happy_path(self, mcp_client) -> None:
        client, manager, server = mcp_client
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
                token=server.token,
            )

        assert resp["jsonrpc"] == "2.0"
        result = resp["result"]
        assert result["isError"] is False
        content = result["content"]
        assert len(content) == 1
        text = content[0]["text"]
        assert "started" in text.lower() or "run_id" in text.lower()

    async def test_spawn_passes_user_id_from_url(self, mcp_client) -> None:
        client, manager, server = mcp_client
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
            token=server.token,
        )
        assert captured_user_ids == [99]

    async def test_spawn_passes_context_when_provided(self, mcp_client) -> None:
        client, manager, server = mcp_client
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
            token=server.token,
        )
        assert captured_kwargs[0]["context"] == "my context"

    async def test_spawn_passes_user_request_when_provided(self, mcp_client) -> None:
        """user_request argument is forwarded to manager.spawn() as user_request kwarg."""
        client, manager, server = mcp_client
        captured_kwargs: list[dict] = []

        async def _spy_spawn(user_id: int, **kwargs: object) -> AgentRun:  # type: ignore[override]
            captured_kwargs.append(dict(kwargs))
            run = AgentRun(
                run_id="x", name="Nova", task=kwargs.get("task", ""),  # type: ignore[arg-type]
                context="", user_id=user_id, started_at=0.0,
            )
            return run

        manager.spawn = _spy_spawn  # type: ignore[assignment]

        await _post_mcp(
            client, 1,
            _rpc("tools/call", {"name": "spawn_background_agent",
                                 "arguments": {"task": "t", "user_request": "Run the audit"}}),
            token=server.token,
        )
        assert captured_kwargs[0]["user_request"] == "Run the audit"

    async def test_spawn_user_request_defaults_to_empty_string(self, mcp_client) -> None:
        """When user_request is absent from arguments, spawn() receives user_request=''."""
        client, manager, server = mcp_client
        captured_kwargs: list[dict] = []

        async def _spy_spawn(user_id: int, **kwargs: object) -> AgentRun:  # type: ignore[override]
            captured_kwargs.append(dict(kwargs))
            run = AgentRun(
                run_id="x", name="Nova", task=kwargs.get("task", ""),  # type: ignore[arg-type]
                context="", user_id=user_id, started_at=0.0,
            )
            return run

        manager.spawn = _spy_spawn  # type: ignore[assignment]

        await _post_mcp(
            client, 1,
            _rpc("tools/call", {"name": "spawn_background_agent",
                                 "arguments": {"task": "t"}}),
            token=server.token,
        )
        assert captured_kwargs[0]["user_request"] == ""

    async def test_unknown_tool_name_returns_error(self, mcp_client) -> None:
        client, _, server = mcp_client
        resp = await _post_mcp(
            client, 42,
            _rpc("tools/call", {"name": "nonexistent_tool", "arguments": {}}),
            token=server.token,
        )
        assert "error" in resp
        assert resp["error"]["code"] == -32602

    async def test_missing_task_param_returns_error(self, mcp_client) -> None:
        client, _, server = mcp_client
        resp = await _post_mcp(
            client, 42,
            _rpc("tools/call", {"name": "spawn_background_agent", "arguments": {}}),
            token=server.token,
        )
        assert "error" in resp
        assert resp["error"]["code"] == -32602

    async def test_max_parallel_exceeded_returns_tool_error(self, mcp_client) -> None:
        """When max_parallel is exceeded, return isError=True (not a JSON-RPC error)."""
        client, manager, server = mcp_client

        async def _raise_runtime(*args: object, **kwargs: object) -> None:
            raise RuntimeError("Max parallel agents exceeded")

        manager.spawn = _raise_runtime  # type: ignore[assignment]

        resp = await _post_mcp(
            client, 42,
            _rpc("tools/call", {"name": "spawn_background_agent",
                                 "arguments": {"task": "overflow"}}),
            token=server.token,
        )
        assert resp["jsonrpc"] == "2.0"
        result = resp["result"]
        assert result["isError"] is True


class TestErrorHandling:
    async def test_unknown_method_returns_method_not_found(self, mcp_client) -> None:
        client, _, server = mcp_client
        resp = await _post_mcp(client, 42, _rpc("unknown/method"), token=server.token)
        assert "error" in resp
        assert resp["error"]["code"] == -32601

    async def test_invalid_json_returns_400(self, mcp_client) -> None:
        client, _, server = mcp_client
        raw = await client.post(
            "/mcp/42",
            data=b"not json",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {server.token}",
            },
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

    async def test_start_disables_tcp_keepalive(self) -> None:
        """AppRunner must be created with tcp_keepalive=False.

        On macOS, aiohttp's default tcp_keepalive=True calls
        setsockopt(SO_KEEPALIVE) on every incoming connection.  Loopback
        (Unix-domain / AF_INET localhost) sockets on macOS reject that call
        with OSError [Errno 22] Invalid argument, spamming the log with
        uncaught asyncio callback errors.  Passing tcp_keepalive=False to
        AppRunner sets RequestHandler._tcp_keepalive = False so the
        setsockopt() call is never attempted.
        """
        from unittest.mock import patch as _patch

        manager = _make_manager()
        server = ArchonMCPServer(manager=manager, host="127.0.0.1", port=18297)

        mock_site = MagicMock()
        mock_site.start = AsyncMock()

        mock_runner = MagicMock()
        mock_runner.setup = AsyncMock()

        with _patch("archon.ai.archon_mcp_server.web.AppRunner", return_value=mock_runner) as mock_runner_cls, \
             _patch("archon.ai.archon_mcp_server.web.TCPSite", return_value=mock_site):
            await server.start()

        mock_runner_cls.assert_called_once()
        _args, kwargs = mock_runner_cls.call_args
        assert kwargs.get("tcp_keepalive") is False, (
            "AppRunner must be created with tcp_keepalive=False to prevent "
            "OSError [Errno 22] on macOS loopback sockets"
        )


# ──────────────────────────────────────────────────────────────────
# GET /health endpoint
# ──────────────────────────────────────────────────────────────────


class TestHealth:
    async def test_health_returns_200_ok(self, mcp_client) -> None:
        client, _, _ = mcp_client
        resp = await client.get("/health")
        assert resp.status == 200

    async def test_health_body_is_status_ok(self, mcp_client) -> None:
        client, _, _ = mcp_client
        resp = await client.get("/health")
        data = await resp.json()
        assert data == {"status": "ok"}


# ──────────────────────────────────────────────────────────────────
# Whitelist enforcement
# ──────────────────────────────────────────────────────────────────


class TestWhitelist:
    async def test_allowed_user_id_passes(self, mcp_client_with_whitelist) -> None:
        """A whitelisted user_id receives a normal JSON-RPC response (not 403)."""
        client, _, server = mcp_client_with_whitelist
        resp = await client.post(
            "/mcp/42",
            json=_rpc("initialize"),
            headers={"Authorization": f"Bearer {server.token}"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert "result" in data

    async def test_unauthorized_user_id_returns_403(self, mcp_client_with_whitelist) -> None:
        """A non-whitelisted user_id is rejected with HTTP 403."""
        client, _, server = mcp_client_with_whitelist
        resp = await client.post(
            "/mcp/999",
            json=_rpc("initialize"),
            headers={"Authorization": f"Bearer {server.token}"},
        )
        assert resp.status == 403

    async def test_unauthorized_user_id_response_body(self, mcp_client_with_whitelist) -> None:
        """The 403 response body contains a JSON error field."""
        client, _, server = mcp_client_with_whitelist
        resp = await client.post(
            "/mcp/999",
            json=_rpc("initialize"),
            headers={"Authorization": f"Bearer {server.token}"},
        )
        data = await resp.json()
        assert "error" in data

    async def test_no_whitelist_allows_any_user(self, mcp_client) -> None:
        """When allowed_user_ids is not set, all user_ids are allowed (backward compat)."""
        client, _, server = mcp_client
        resp = await client.post(
            "/mcp/12345",
            json=_rpc("initialize"),
            headers={"Authorization": f"Bearer {server.token}"},
        )
        assert resp.status == 200

    async def test_second_allowed_user_id_passes(self, mcp_client_with_whitelist) -> None:
        """All IDs in the whitelist are accepted."""
        client, _, server = mcp_client_with_whitelist
        resp = await client.post(
            "/mcp/99",
            json=_rpc("initialize"),
            headers={"Authorization": f"Bearer {server.token}"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert "result" in data


# ──────────────────────────────────────────────────────────────────
# set_manager — public API for deferred circular-dependency wiring
# ──────────────────────────────────────────────────────────────────


class TestSetManager:
    def test_set_manager_replaces_none(self) -> None:
        """set_manager() must replace an initial None manager."""
        server = ArchonMCPServer(manager=None)
        assert server._manager is None

        manager = _make_manager()
        server.set_manager(manager)
        assert server._manager is manager


# ──────────────────────────────────────────────────────────────────
# Issue #18: Bearer token authentication
# ──────────────────────────────────────────────────────────────────


class TestBearerTokenAuth:
    def test_token_generated_on_init(self) -> None:
        """Server must generate a non-empty token on construction."""
        server = ArchonMCPServer(manager=None)
        assert server.token
        assert len(server.token) == 64  # 32 bytes hex-encoded

    def test_token_is_unique_per_instance(self) -> None:
        """Each server instance must have a unique token."""
        s1 = ArchonMCPServer(manager=None)
        s2 = ArchonMCPServer(manager=None)
        assert s1.token != s2.token

    def test_mcp_headers_for_returns_bearer(self) -> None:
        """mcp_headers_for() must return Authorization: Bearer <token>."""
        server = ArchonMCPServer(manager=None)
        headers = server.mcp_headers_for(42)
        assert headers == {"Authorization": f"Bearer {server.token}"}

    async def test_request_without_token_returns_401(self, mcp_client) -> None:
        """A POST without Authorization header must get 401."""
        client, _, _ = mcp_client
        resp = await client.post("/mcp/42", json=_rpc("initialize"))
        assert resp.status == 401

    async def test_request_with_wrong_token_returns_401(self, mcp_client) -> None:
        """A POST with an incorrect bearer token must get 401."""
        client, _, _ = mcp_client
        resp = await client.post(
            "/mcp/42",
            json=_rpc("initialize"),
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status == 401

    async def test_request_with_valid_token_succeeds(self, mcp_client) -> None:
        """A POST with the correct bearer token must succeed."""
        client, _, server = mcp_client
        resp = await _post_mcp(client, 42, _rpc("initialize"), token=server.token)
        assert "result" in resp

    async def test_health_endpoint_no_auth_required(self, mcp_client) -> None:
        """GET /health must not require authentication."""
        client, _, _ = mcp_client
        resp = await client.get("/health")
        assert resp.status == 200
