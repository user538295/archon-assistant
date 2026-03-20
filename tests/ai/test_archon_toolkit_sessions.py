"""Tests for session management tools — Task 3.1 (get_session_status) and Task 3.2 (get_context_stats)."""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from aiohttp.test_utils import TestClient, TestServer

from archon.ai.archon_toolkit import ArchonToolkit
from archon.ai.session_manager import SessionManager


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_toolkit(*, session_manager: object | None = None) -> ArchonToolkit:
    return ArchonToolkit(session_manager=session_manager)


def _rpc(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    req: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        req["params"] = params
    return req


def _mock_diagnostics(
    *,
    is_alive: bool = True,
    is_processing: bool = False,
    processing_seconds: float | None = None,
    idle_seconds: float | None = 10.5,
    send_count: int = 3,
) -> dict:
    return {
        "is_alive": is_alive,
        "is_processing": is_processing,
        "processing_seconds": processing_seconds,
        "idle_seconds": idle_seconds,
        "send_count": send_count,
        "recent_events": [],
        "usage_stats": {},
    }


# ──────────────────────────────────────────────────────────────────
# Unit tests
# ──────────────────────────────────────────────────────────────────


class TestGetSessionStatusActive:
    async def test_get_session_status_active(self) -> None:
        """Active session returns JSON with all required fields."""
        sm = MagicMock()
        sm.session_diagnostics.return_value = _mock_diagnostics(
            is_alive=True,
            is_processing=True,
            processing_seconds=5.2,
            idle_seconds=None,
            send_count=7,
        )
        sm.get_model.return_value = "claude-opus-4-5"

        toolkit = _make_toolkit(session_manager=sm)
        result = await toolkit.call_tool(
            "get_session_status", {"user_id": 42}, user_id=42
        )
        data = json.loads(result)

        assert data["is_alive"] is True
        assert data["is_processing"] is True
        assert data["processing_seconds"] == pytest.approx(5.2)
        assert data["idle_seconds"] is None
        assert data["send_count"] == 7
        assert data["model"] == "claude-opus-4-5"
        sm.session_diagnostics.assert_called_once_with(42)


class TestGetSessionStatusNoSession:
    async def test_get_session_status_no_session(self) -> None:
        """When session_diagnostics returns None, returns friendly message."""
        sm = MagicMock()
        sm.session_diagnostics.return_value = None

        toolkit = _make_toolkit(session_manager=sm)
        result = await toolkit.call_tool(
            "get_session_status", {"user_id": 99}, user_id=99
        )

        assert result == "No active session for user 99."
        sm.session_diagnostics.assert_called_once_with(99)


class TestGetSessionStatusAllFields:
    async def test_get_session_status_returns_only_required_fields(self) -> None:
        """Response contains exactly the specified fields (not extra internal ones)."""
        sm = MagicMock()
        sm.session_diagnostics.return_value = _mock_diagnostics()
        sm.get_model.return_value = None

        toolkit = _make_toolkit(session_manager=sm)
        result = await toolkit.call_tool(
            "get_session_status", {"user_id": 1}, user_id=1
        )
        data = json.loads(result)

        expected_keys = {"is_processing", "processing_seconds", "idle_seconds", "send_count", "is_alive", "model"}
        assert set(data.keys()) == expected_keys


class TestGetSessionStatusModelNone:
    async def test_get_session_status_model_none_when_no_override(self) -> None:
        """When no model override, model field is None."""
        sm = MagicMock()
        sm.session_diagnostics.return_value = _mock_diagnostics()
        sm.get_model.return_value = None

        toolkit = _make_toolkit(session_manager=sm)
        result = await toolkit.call_tool(
            "get_session_status", {"user_id": 1}, user_id=1
        )
        data = json.loads(result)

        assert data["model"] is None


class TestGetSessionStatusMissingSessionManager:
    async def test_get_session_status_no_session_manager_raises(self) -> None:
        """Without session_manager, raises RuntimeError."""
        toolkit = _make_toolkit(session_manager=None)

        with pytest.raises(RuntimeError, match="session_manager not available"):
            await toolkit.call_tool("get_session_status", {"user_id": 1}, user_id=1)


# ──────────────────────────────────────────────────────────────────
# Integration test via MCP server
# ──────────────────────────────────────────────────────────────────


class TestGetSessionStatusViaMcp:
    async def test_get_session_status_via_mcp(self) -> None:
        """Tool is callable via the background MCP server and returns correct result."""
        from archon.ai.archon_mcp_server import ArchonMCPServer
        from archon.ai.background_agent_manager import BackgroundAgentManager

        bot = MagicMock()
        bot.send_message = AsyncMock()
        bam_sm = MagicMock()
        manager = BackgroundAgentManager(bot=bot, session_manager=bam_sm)

        sm = MagicMock()
        sm.session_diagnostics.return_value = None

        toolkit = _make_toolkit(session_manager=sm)

        server = ArchonMCPServer(
            manager=manager, host="127.0.0.1", port=18297, toolkit=toolkit,
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            # Verify tool is listed
            resp = await client.post(
                "/mcp/42",
                json=_rpc("tools/list"),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            tool_names = {t["name"] for t in data["result"]["tools"]}
            assert "get_session_status" in tool_names

            # Call tool — no session
            resp = await client.post(
                "/mcp/42",
                json=_rpc(
                    "tools/call",
                    {"name": "get_session_status", "arguments": {"user_id": 42}},
                ),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
            assert data["result"]["content"][0]["text"] == "No active session for user 42."
        finally:
            await client.close()


# ──────────────────────────────────────────────────────────────────
# Task 3.2 — get_context_stats tests
# ──────────────────────────────────────────────────────────────────

_SAMPLE_STATS: dict = {
    "usage": {
        "input_tokens": 1000,
        "output_tokens": 200,
        "cache_read_input_tokens": 50,
        "cache_creation_input_tokens": 10,
    },
    "cumulative_cache_creation": 10,
    "total_cost_usd": 0.0042,
    "num_turns": 3,
    "last_duration_ms": 1234,
    "user_turns": 2,
}


class TestGetContextStatsActive:
    async def test_get_context_stats_active(self) -> None:
        """Active session with stats returns JSON with all required fields."""
        sm = MagicMock()
        sm.context_stats.return_value = _SAMPLE_STATS

        toolkit = _make_toolkit(session_manager=sm)
        result = await toolkit.call_tool(
            "get_context_stats", {"user_id": 42}, user_id=42
        )
        data = json.loads(result)

        assert "usage" in data
        assert "total_cost_usd" in data
        assert "num_turns" in data
        assert "last_duration_ms" in data
        assert "user_turns" in data
        assert "cumulative_cache_creation" in data
        sm.context_stats.assert_called_once_with(42)


class TestGetContextStatsNoSession:
    async def test_get_context_stats_no_session(self) -> None:
        """When context_stats returns None, returns friendly message."""
        sm = MagicMock()
        sm.context_stats.return_value = None

        toolkit = _make_toolkit(session_manager=sm)
        result = await toolkit.call_tool(
            "get_context_stats", {"user_id": 99}, user_id=99
        )

        assert result == "No active session for user 99."
        sm.context_stats.assert_called_once_with(99)


class TestGetContextStatsMissingSessionManager:
    async def test_get_context_stats_no_session_manager_raises(self) -> None:
        """Without session_manager, raises RuntimeError."""
        toolkit = _make_toolkit(session_manager=None)

        with pytest.raises(RuntimeError, match="session_manager not available"):
            await toolkit.call_tool("get_context_stats", {"user_id": 1}, user_id=1)


class TestGetContextStatsInvalidUserId:
    async def test_get_context_stats_invalid_user_id(self) -> None:
        """Non-integer user_id argument returns a descriptive error message."""
        sm = MagicMock()

        toolkit = _make_toolkit(session_manager=sm)
        result = await toolkit.call_tool(
            "get_context_stats", {"user_id": "abc"}, user_id=None
        )

        assert result == "Invalid user_id argument."
        sm.context_stats.assert_not_called()


class TestGetContextStatsWrongUserRejected:
    async def test_get_context_stats_wrong_user_rejected(self) -> None:
        """Caller user_id=42 cannot query user_id=99's context stats."""
        sm = MagicMock()
        sm.context_stats.return_value = _SAMPLE_STATS

        toolkit = _make_toolkit(session_manager=sm)
        result = await toolkit.call_tool(
            "get_context_stats", {"user_id": 99}, user_id=42
        )

        assert result == "No active session for user 99."
        sm.context_stats.assert_not_called()


class TestGetContextStatsViaMcp:
    async def test_get_context_stats_via_mcp(self) -> None:
        """Tool is callable via the background MCP server and returns correct result."""
        from archon.ai.archon_mcp_server import ArchonMCPServer
        from archon.ai.background_agent_manager import BackgroundAgentManager

        bot = MagicMock()
        bot.send_message = AsyncMock()
        bam_sm = MagicMock()
        manager = BackgroundAgentManager(bot=bot, session_manager=bam_sm)

        sm = MagicMock()
        sm.context_stats.return_value = None

        toolkit = _make_toolkit(session_manager=sm)

        server = ArchonMCPServer(
            manager=manager, host="127.0.0.1", port=18298, toolkit=toolkit,
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            # Verify tool is listed
            resp = await client.post(
                "/mcp/42",
                json=_rpc("tools/list"),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            tool_names = {t["name"] for t in data["result"]["tools"]}
            assert "get_context_stats" in tool_names

            # Call tool — no session
            resp = await client.post(
                "/mcp/42",
                json=_rpc(
                    "tools/call",
                    {"name": "get_context_stats", "arguments": {"user_id": 42}},
                ),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
            assert data["result"]["content"][0]["text"] == "No active session for user 42."
        finally:
            await client.close()


class TestGetContextStatsWithRealSessionManager:
    async def test_get_context_stats_with_real_session_manager(self) -> None:
        """E2E: create real SessionManager, check stats before and after mocking usage_stats."""
        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.stop = AsyncMock()
        mock_session.is_alive = True
        mock_session.is_processing = False
        mock_session.usage_stats = None  # no response yet

        sm = SessionManager(timeout=60, session_factory=lambda _: mock_session)
        toolkit = ArchonToolkit(session_manager=sm)

        # Before session creation — no active session
        result = await toolkit.call_tool(
            "get_context_stats", {"user_id": 1}, user_id=1
        )
        assert result == "No active session for user 1."

        # Create session
        await sm.get_or_create(user_id=1)

        # Session exists but usage_stats is None (no response yet).
        # context_stats() returns None for both "no session" and "session with no stats",
        # so the tool correctly returns "No active session" in both cases.
        # This is by design — KISS: SessionManager.context_stats() is the source of truth.
        result = await toolkit.call_tool(
            "get_context_stats", {"user_id": 1}, user_id=1
        )
        assert result == "No active session for user 1."

        # Simulate a response having been received
        mock_session.usage_stats = _SAMPLE_STATS

        result = await toolkit.call_tool(
            "get_context_stats", {"user_id": 1}, user_id=1
        )
        data = json.loads(result)
        assert "total_cost_usd" in data
        assert "num_turns" in data
        assert "user_turns" in data

        # Stop the session
        await sm.stop(user_id=1)


# ──────────────────────────────────────────────────────────────────
# E2E test with real SessionManager
# ──────────────────────────────────────────────────────────────────


class TestGetSessionStatusWithRealSessionManager:
    async def test_get_session_status_with_real_session_manager(self) -> None:
        """E2E: create real SessionManager, create a session, check is_alive."""
        # Build a mock session that satisfies ClaudeSession interface
        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.stop = AsyncMock()
        mock_session.is_alive = True
        mock_session.is_processing = False
        mock_session.diagnostics = {
            "is_alive": True,
            "is_processing": False,
            "processing_seconds": None,
            "idle_seconds": 1.0,
            "send_count": 0,
            "recent_events": [],
            "usage_stats": {},
        }

        sm = SessionManager(timeout=60, session_factory=lambda _: mock_session)
        toolkit = ArchonToolkit(session_manager=sm)

        # Before session creation — no active session
        result = await toolkit.call_tool(
            "get_session_status", {"user_id": 1}, user_id=1
        )
        assert result == "No active session for user 1."

        # Create session
        await sm.get_or_create(user_id=1)

        # Session is now active
        result = await toolkit.call_tool(
            "get_session_status", {"user_id": 1}, user_id=1
        )
        data = json.loads(result)
        assert "is_processing" in data
        assert "processing_seconds" in data
        assert "idle_seconds" in data
        assert "send_count" in data
        assert "is_alive" in data
        assert "model" in data
        assert data["is_alive"] is True

        # Stop the session
        await sm.stop(user_id=1)

        # After stop — no active session
        result = await toolkit.call_tool(
            "get_session_status", {"user_id": 1}, user_id=1
        )
        assert result == "No active session for user 1."


# ──────────────────────────────────────────────────────────────────
# Authorization checks
# ──────────────────────────────────────────────────────────────────


class TestGetSessionStatusWrongUserRejected:
    async def test_get_session_status_wrong_user_rejected(self) -> None:
        """Background agent with user_id=42 cannot query user_id=99's session."""
        sm = MagicMock()
        sm.session_diagnostics.return_value = _mock_diagnostics()
        sm.get_model.return_value = "claude-opus-4-5"

        toolkit = _make_toolkit(session_manager=sm)
        result = await toolkit.call_tool(
            "get_session_status", {"user_id": 99}, user_id=42
        )

        assert result == "No active session for user 99."
        sm.session_diagnostics.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# Invalid user_id argument
# ──────────────────────────────────────────────────────────────────


class TestGetSessionStatusInvalidUserId:
    async def test_get_session_status_invalid_user_id(self) -> None:
        """Non-integer user_id argument returns a descriptive error message."""
        sm = MagicMock()

        toolkit = _make_toolkit(session_manager=sm)
        result = await toolkit.call_tool(
            "get_session_status", {"user_id": "abc"}, user_id=None
        )

        assert result == "Invalid user_id argument."
        sm.session_diagnostics.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# Orchestrator MCP server path
# ──────────────────────────────────────────────────────────────────


class TestGetSessionStatusViaOrchMcp:
    async def test_get_session_status_via_router_mcp(self, tmp_path) -> None:
        """Tool is callable via ArchonRouterMCPServer and returns correct result."""
        from archon.ai.archon_router_mcp_server import ArchonRouterMCPServer

        sm = MagicMock()
        sm.session_diagnostics.return_value = None

        toolkit = _make_toolkit(session_manager=sm)

        server = ArchonRouterMCPServer(history_root=str(tmp_path), toolkit=toolkit)
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            # Verify tool is listed
            resp = await client.post(
                "/mcp",
                json=_rpc("tools/list"),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            tool_names = {t["name"] for t in data["result"]["tools"]}
            assert "get_session_status" in tool_names

            # Call tool — no session (user_id=None on router path)
            resp = await client.post(
                "/mcp",
                json=_rpc(
                    "tools/call",
                    {"name": "get_session_status", "arguments": {"user_id": 42}},
                ),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
            assert data["result"]["content"][0]["text"] == "No active session for user 42."
        finally:
            await client.close()


# ──────────────────────────────────────────────────────────────────
# Task 3.2 — additional tests
# ──────────────────────────────────────────────────────────────────


class TestGetContextStatsMissingUserIdKey:
    async def test_get_context_stats_missing_user_id_key(self) -> None:
        """Empty arguments dict (missing user_id key) returns descriptive error."""
        sm = MagicMock()

        toolkit = _make_toolkit(session_manager=sm)
        result = await toolkit.call_tool(
            "get_context_stats", {}, user_id=None
        )

        assert result == "Invalid user_id argument."
        sm.context_stats.assert_not_called()


class TestGetContextStatsOrcTrustPath:
    async def test_get_context_stats_user_id_none_caller_succeeds(self) -> None:
        """user_id=None (router trust path) skips auth check and queries the target user."""
        sm = MagicMock()
        sm.context_stats.return_value = _SAMPLE_STATS

        toolkit = _make_toolkit(session_manager=sm)
        result = await toolkit.call_tool(
            "get_context_stats", {"user_id": 42}, user_id=None
        )

        data = json.loads(result)
        assert "total_cost_usd" in data
        assert "num_turns" in data
        sm.context_stats.assert_called_once_with(42)


class TestGetContextStatsViaOrchMcp:
    async def test_get_context_stats_via_router_mcp(self, tmp_path) -> None:
        """Tool is callable via ArchonRouterMCPServer and returns correct result."""
        from archon.ai.archon_router_mcp_server import ArchonRouterMCPServer

        sm = MagicMock()
        sm.context_stats.return_value = None

        toolkit = _make_toolkit(session_manager=sm)

        server = ArchonRouterMCPServer(history_root=str(tmp_path), toolkit=toolkit)
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            # Verify tool is listed
            resp = await client.post(
                "/mcp",
                json=_rpc("tools/list"),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            tool_names = {t["name"] for t in data["result"]["tools"]}
            assert "get_context_stats" in tool_names

            # Call tool — no session (user_id=None on router path)
            resp = await client.post(
                "/mcp",
                json=_rpc(
                    "tools/call",
                    {"name": "get_context_stats", "arguments": {"user_id": 42}},
                ),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
            assert data["result"]["content"][0]["text"] == "No active session for user 42."
        finally:
            await client.close()
