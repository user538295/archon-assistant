"""Tests for ArchonOrchestratorMCPServer — Step 2 orchestration redesign."""
import pytest
from pathlib import Path
from unittest.mock import mock_open, patch
from aiohttp.test_utils import TestClient, TestServer

from archon.ai.archon_orch_mcp_server import ArchonOrchestratorMCPServer


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _rpc(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    req: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        req["params"] = params
    return req


async def _post_mcp(client: TestClient, body: dict) -> dict:
    resp = await client.post("/mcp", json=body)
    return await resp.json()


# ──────────────────────────────────────────────────────────────────
# Fixture
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def orch_client():
    """Provide a TestClient connected to the orchestrator MCP server's aiohttp app."""
    server = ArchonOrchestratorMCPServer()
    client = TestClient(TestServer(server._app))
    await client.start_server()
    yield client
    await client.close()


# ──────────────────────────────────────────────────────────────────
# initialize
# ──────────────────────────────────────────────────────────────────


class TestInitialize:
    async def test_initialize_returns_capabilities(self, orch_client: TestClient) -> None:
        resp = await _post_mcp(orch_client, _rpc("initialize"))
        assert resp["jsonrpc"] == "2.0"
        result = resp["result"]
        assert "serverInfo" in result
        assert "capabilities" in result
        assert "tools" in result["capabilities"]


# ──────────────────────────────────────────────────────────────────
# tools/list
# ──────────────────────────────────────────────────────────────────


class TestToolsList:
    async def test_tools_list_returns_exactly_two_tools(self, orch_client: TestClient) -> None:
        resp = await _post_mcp(orch_client, _rpc("tools/list"))
        tools = resp["result"]["tools"]
        assert len(tools) == 2
        names = {t["name"] for t in tools}
        assert names == {"history_read", "history_grep"}


# ──────────────────────────────────────────────────────────────────
# history_read
# ──────────────────────────────────────────────────────────────────


class TestHistoryRead:
    async def test_history_read_valid_path(self, orch_client: TestClient) -> None:
        """A valid path under ~/.archon/history/ returns file content."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "2026-01-01.md")
        file_content = "# Session log\nsome content here"

        with patch("archon.ai.archon_orch_mcp_server.Path") as mock_path_cls:
            # Build a mock that behaves correctly for the path restriction check
            # and the actual read
            real_path = Path(valid_path)
            mock_instance = mock_path_cls.return_value
            mock_instance.expanduser.return_value = real_path
            # Path(path_str).expanduser().resolve() used in _is_allowed_path
            real_resolved = real_path.resolve()
            mock_instance.expanduser.return_value.__class__ = Path

            # Use a targeted patch instead
            pass

        # Use a simpler approach: patch builtins.open and Path.exists/read_text
        with patch("archon.ai.archon_orch_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=file_content):
            resp = await _post_mcp(
                orch_client,
                _rpc("tools/call", {"name": "history_read", "arguments": {"path": valid_path}}),
            )

        assert resp["result"]["isError"] is False
        assert file_content in resp["result"]["content"][0]["text"]

    async def test_history_read_path_outside_history_is_denied(self, orch_client: TestClient) -> None:
        """A path outside ~/.archon/history/ is denied with isError=true."""
        resp = await _post_mcp(
            orch_client,
            _rpc("tools/call", {"name": "history_read", "arguments": {"path": "/etc/passwd"}}),
        )
        assert resp["result"]["isError"] is True
        assert "Access denied" in resp["result"]["content"][0]["text"]

    async def test_history_read_path_traversal_denied(self, orch_client: TestClient) -> None:
        """Path traversal attempt is blocked after resolve()."""
        traversal = "~/.archon/history/../../../etc/passwd"
        resp = await _post_mcp(
            orch_client,
            _rpc("tools/call", {"name": "history_read", "arguments": {"path": traversal}}),
        )
        assert resp["result"]["isError"] is True
        assert "Access denied" in resp["result"]["content"][0]["text"]

    async def test_history_read_file_not_found(self, orch_client: TestClient) -> None:
        """Valid path but missing file returns isError=true."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "nonexistent-9999-99-99.md")

        with patch("archon.ai.archon_orch_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=False):
            resp = await _post_mcp(
                orch_client,
                _rpc("tools/call", {"name": "history_read", "arguments": {"path": valid_path}}),
            )

        assert resp["result"]["isError"] is True
        assert "not found" in resp["result"]["content"][0]["text"].lower()


# ──────────────────────────────────────────────────────────────────
# history_grep
# ──────────────────────────────────────────────────────────────────


class TestHistoryGrep:
    async def test_history_grep_valid_path_returns_matches(self, orch_client: TestClient) -> None:
        """Pattern matching returns only matching lines."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "2026-01-01.md")
        file_content = "line one\nerror occurred here\nanother line\nerror again"

        with patch("archon.ai.archon_orch_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=file_content):
            resp = await _post_mcp(
                orch_client,
                _rpc("tools/call", {
                    "name": "history_grep",
                    "arguments": {"pattern": "error", "path": valid_path},
                }),
            )

        assert resp["result"]["isError"] is False
        text = resp["result"]["content"][0]["text"]
        assert "error occurred here" in text
        assert "error again" in text
        assert "line one" not in text

    async def test_history_grep_no_matches_returns_placeholder(self, orch_client: TestClient) -> None:
        """When pattern matches nothing, return '(no matches)'."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "2026-01-01.md")
        file_content = "line one\nline two\nline three"

        with patch("archon.ai.archon_orch_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=file_content):
            resp = await _post_mcp(
                orch_client,
                _rpc("tools/call", {
                    "name": "history_grep",
                    "arguments": {"pattern": "zzz_nomatch", "path": valid_path},
                }),
            )

        assert resp["result"]["isError"] is False
        assert resp["result"]["content"][0]["text"] == "(no matches)"

    async def test_history_grep_path_outside_history_is_denied(self, orch_client: TestClient) -> None:
        """history_grep also enforces path restriction."""
        resp = await _post_mcp(
            orch_client,
            _rpc("tools/call", {
                "name": "history_grep",
                "arguments": {"pattern": "root", "path": "/etc/passwd"},
            }),
        )
        assert resp["result"]["isError"] is True
        assert "Access denied" in resp["result"]["content"][0]["text"]


# ──────────────────────────────────────────────────────────────────
# Unknown tool
# ──────────────────────────────────────────────────────────────────


class TestUnknownTool:
    async def test_unknown_tool_returns_error(self, orch_client: TestClient) -> None:
        """tools/call with an unknown tool name returns a JSON-RPC error."""
        resp = await _post_mcp(
            orch_client,
            _rpc("tools/call", {"name": "rm_rf", "arguments": {}}),
        )
        assert "error" in resp
        assert resp["error"]["code"] == -32602


# ──────────────────────────────────────────────────────────────────
# GET /health
# ──────────────────────────────────────────────────────────────────


class TestHealth:
    async def test_health_endpoint(self, orch_client: TestClient) -> None:
        resp = await orch_client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert data == {"status": "ok"}
