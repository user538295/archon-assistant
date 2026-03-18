"""Tests for agent tools — Task 2.1 (list_running_agents) & Task 2.2 (get_agent_status)."""
import json
import time
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, MagicMock

from aiohttp.test_utils import TestClient, TestServer

from archon.ai.archon_toolkit import ArchonToolkit


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_toolkit(
    *,
    bg_manager: object | None = None,
    config: object | None = None,
) -> ArchonToolkit:
    return ArchonToolkit(bg_manager=bg_manager, config=config)


def _mock_agent(
    name: str = "Atlas",
    status: str = "running",
    task: str = "Do something",
    run_id: str = "abc123",
    started_at: float | None = None,
    user_id: int = 42,
    result: str | None = None,
    error: str | None = None,
    log_path: Path | None = None,
) -> MagicMock:
    agent = MagicMock()
    agent.run_id = run_id
    agent.name = name
    agent.task = task
    agent.started_at = started_at or time.monotonic() - 30
    agent.status = status
    agent.user_id = user_id
    agent.result = result
    agent.error = error
    agent.log_path = log_path
    return agent


def _rpc(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    req: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        req["params"] = params
    return req


# ──────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────


class TestListRunningAgentsReturnsJsonArray:
    async def test_list_running_agents_returns_json_array(self) -> None:
        """With 2 running agents, returns JSON array with correct fields."""
        bg = MagicMock()
        bg.list_running.return_value = [
            _mock_agent(name="Atlas", run_id="aaa", task="Build something"),
            _mock_agent(name="Nova", run_id="bbb", task="Test something"),
        ]

        toolkit = _make_toolkit(bg_manager=bg)
        result = await toolkit.call_tool("list_running_agents", {}, user_id=42)
        data = json.loads(result)

        assert isinstance(data, list)
        assert len(data) == 2
        for entry in data:
            assert "run_id" in entry
            assert "name" in entry
            assert "task_summary" in entry
            assert "age_seconds" in entry
            assert "status" in entry


class TestListRunningAgentsEmpty:
    async def test_list_running_agents_empty(self) -> None:
        """With no running agents, returns plain text message."""
        bg = MagicMock()
        bg.list_running.return_value = []

        toolkit = _make_toolkit(bg_manager=bg)
        result = await toolkit.call_tool("list_running_agents", {}, user_id=42)

        assert result == "No running agents."


class TestListRunningAgentsTruncatesTask:
    async def test_list_running_agents_truncates_task(self) -> None:
        """Long task is truncated to 100 chars in task_summary."""
        long_task = "A" * 200
        bg = MagicMock()
        bg.list_running.return_value = [_mock_agent(task=long_task)]

        toolkit = _make_toolkit(bg_manager=bg)
        result = await toolkit.call_tool("list_running_agents", {}, user_id=42)
        data = json.loads(result)

        assert len(data[0]["task_summary"]) == 100


class TestListRunningAgentsFilterByName:
    async def test_list_running_agents_filter_by_name(self) -> None:
        """With name filter, uses list_all and filters by name."""
        bg = MagicMock()
        bg.list_all.return_value = [
            _mock_agent(name="Atlas", status="running", run_id="aaa"),
            _mock_agent(name="Nova", status="completed", run_id="bbb"),
            _mock_agent(name="Iris", status="cancelled", run_id="ccc"),
        ]

        toolkit = _make_toolkit(bg_manager=bg)
        result = await toolkit.call_tool(
            "list_running_agents", {"name": "Atlas"}, user_id=42,
        )
        data = json.loads(result)

        assert len(data) == 1
        assert data[0]["name"] == "Atlas"


class TestListRunningAgentsFilterCaseInsensitive:
    async def test_list_running_agents_filter_by_name_case_insensitive(self) -> None:
        """Name filter is case-insensitive."""
        bg = MagicMock()
        bg.list_all.return_value = [
            _mock_agent(name="Atlas", run_id="aaa"),
        ]

        toolkit = _make_toolkit(bg_manager=bg)
        result = await toolkit.call_tool(
            "list_running_agents", {"name": "atlas"}, user_id=42,
        )
        data = json.loads(result)

        assert len(data) == 1
        assert data[0]["name"] == "Atlas"


class TestListRunningAgentsFilterNotFound:
    async def test_list_running_agents_filter_by_name_not_found(self) -> None:
        """Name filter with no match returns friendly message."""
        bg = MagicMock()
        bg.list_all.return_value = [
            _mock_agent(name="Atlas", run_id="aaa"),
        ]

        toolkit = _make_toolkit(bg_manager=bg)
        result = await toolkit.call_tool(
            "list_running_agents", {"name": "Unknown"}, user_id=42,
        )

        assert result == "No agent named 'Unknown' found."


class TestListRunningAgentsFilterIncludesCompleted:
    async def test_list_running_agents_filter_by_name_includes_completed(self) -> None:
        """Name filter searches all statuses including completed."""
        bg = MagicMock()
        bg.list_all.return_value = [
            _mock_agent(name="Nova", status="completed", run_id="bbb"),
        ]

        toolkit = _make_toolkit(bg_manager=bg)
        result = await toolkit.call_tool(
            "list_running_agents", {"name": "Nova"}, user_id=42,
        )
        data = json.loads(result)

        assert len(data) == 1
        assert data[0]["status"] == "completed"


class TestListRunningAgentsViaMcp:
    async def test_list_running_agents_via_mcp(self) -> None:
        """Tool is callable via the background MCP server."""
        from archon.ai.archon_mcp_server import ArchonMCPServer
        from archon.ai.background_agent_manager import BackgroundAgentManager

        bot = MagicMock()
        bot.send_message = AsyncMock()
        sm = MagicMock()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm)

        bg = MagicMock()
        bg.list_running.return_value = []

        toolkit = _make_toolkit(bg_manager=bg)

        server = ArchonMCPServer(
            manager=manager, host="127.0.0.1", port=18296, toolkit=toolkit,
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
            assert "list_running_agents" in tool_names

            # tools/call
            resp = await client.post(
                "/mcp/42",
                json=_rpc(
                    "tools/call",
                    {"name": "list_running_agents", "arguments": {}},
                ),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
            assert data["result"]["content"][0]["text"] == "No running agents."
        finally:
            await client.close()


class TestListRunningAgentsMissingBgManager:
    async def test_list_running_agents_missing_bg_manager(self) -> None:
        """Without bg_manager, raises RuntimeError."""
        toolkit = _make_toolkit(bg_manager=None)

        with pytest.raises(RuntimeError, match="bg_manager not available"):
            await toolkit.call_tool("list_running_agents", {}, user_id=42)


class TestListRunningAgentsNoUserId:
    async def test_list_running_agents_no_user_id(self) -> None:
        """Without user_id, returns friendly message."""
        bg = MagicMock()
        toolkit = _make_toolkit(bg_manager=bg)
        result = await toolkit.call_tool("list_running_agents", {})
        assert result == "No user context available."


class TestListRunningAgentsMultipleSameName:
    async def test_list_running_agents_multiple_same_name(self) -> None:
        """Name filter returns all agents with that name (not just the first)."""
        bg = MagicMock()
        bg.list_all.return_value = [
            _mock_agent(name="Atlas", status="completed", run_id="aaa"),
            _mock_agent(name="Atlas", status="running", run_id="bbb"),
            _mock_agent(name="Nova", status="running", run_id="ccc"),
        ]

        toolkit = _make_toolkit(bg_manager=bg)
        result = await toolkit.call_tool(
            "list_running_agents", {"name": "Atlas"}, user_id=42,
        )
        data = json.loads(result)

        assert len(data) == 2
        assert all(d["name"] == "Atlas" for d in data)


# ──────────────────────────────────────────────────────────────────
# Task 2.2 — get_agent_status tests
# ──────────────────────────────────────────────────────────────────


class TestGetAgentStatusRunning:
    async def test_get_agent_status_running(self) -> None:
        """Running agent returns JSON with all expected fields."""
        agent = _mock_agent(
            name="Atlas", run_id="abc123", status="running",
            task="Do something important",
        )
        bg = MagicMock()
        bg.get_run.return_value = agent

        toolkit = _make_toolkit(bg_manager=bg)
        result = await toolkit.call_tool(
            "get_agent_status", {"run_id": "abc123"}, user_id=42,
        )
        data = json.loads(result)

        assert data["run_id"] == "abc123"
        assert data["name"] == "Atlas"
        assert data["status"] == "running"
        assert data["task_summary"] == "Do something important"
        assert data["age_seconds"] > 0
        assert data["result"] is None
        assert data["error"] is None
        assert data["log_path"] is None


class TestGetAgentStatusCompleted:
    async def test_get_agent_status_completed(self) -> None:
        """Completed agent includes result and log_path."""
        agent = _mock_agent(
            name="Nova", run_id="def456", status="completed",
            task="Build feature", result="done",
            log_path=Path("/tmp/log.md"),
        )
        bg = MagicMock()
        bg.get_run.return_value = agent

        toolkit = _make_toolkit(bg_manager=bg)
        result = await toolkit.call_tool(
            "get_agent_status", {"run_id": "def456"}, user_id=42,
        )
        data = json.loads(result)

        assert data["status"] == "completed"
        assert data["result"] == "done"
        assert data["log_path"] == "/tmp/log.md"


class TestGetAgentStatusNotFound:
    async def test_get_agent_status_not_found(self) -> None:
        """Non-existent run_id returns friendly message."""
        bg = MagicMock()
        bg.get_run.return_value = None

        toolkit = _make_toolkit(bg_manager=bg)
        result = await toolkit.call_tool(
            "get_agent_status", {"run_id": "abc123"}, user_id=42,
        )

        assert result == "Agent abc123 not found."


class TestGetAgentStatusWrongUserRejected:
    async def test_get_agent_status_wrong_user_rejected(self) -> None:
        """Agent owned by user 42, queried by user 99 — returns not found."""
        agent = _mock_agent(run_id="abc123", user_id=42)
        bg = MagicMock()
        bg.get_run.return_value = agent

        toolkit = _make_toolkit(bg_manager=bg)
        result = await toolkit.call_tool(
            "get_agent_status", {"run_id": "abc123"}, user_id=99,
        )

        assert result == "Agent abc123 not found."


class TestGetAgentStatusNoUserIdSkipsAuth:
    async def test_get_agent_status_no_user_id_skips_auth(self) -> None:
        """Without user_id (orchestrator path), agent is returned regardless of owner."""
        agent = _mock_agent(
            name="Atlas", run_id="abc123", user_id=42,
        )
        bg = MagicMock()
        bg.get_run.return_value = agent

        toolkit = _make_toolkit(bg_manager=bg)
        result = await toolkit.call_tool(
            "get_agent_status", {"run_id": "abc123"},
        )
        data = json.loads(result)

        assert data["run_id"] == "abc123"
        assert data["name"] == "Atlas"


class TestGetAgentStatusViaMcp:
    async def test_get_agent_status_via_mcp(self) -> None:
        """Tool is callable via the background MCP server."""
        from archon.ai.archon_mcp_server import ArchonMCPServer
        from archon.ai.background_agent_manager import BackgroundAgentManager

        bot = MagicMock()
        bot.send_message = AsyncMock()
        sm = MagicMock()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm)

        agent = _mock_agent(name="Atlas", run_id="xyz789", user_id=42)
        bg = MagicMock()
        bg.get_run.return_value = agent

        toolkit = _make_toolkit(bg_manager=bg)

        server = ArchonMCPServer(
            manager=manager, host="127.0.0.1", port=18297, toolkit=toolkit,
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            # tools/list — verify get_agent_status is registered
            resp = await client.post(
                "/mcp/42",
                json=_rpc("tools/list"),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            tool_names = {t["name"] for t in data["result"]["tools"]}
            assert "get_agent_status" in tool_names

            # tools/call
            resp = await client.post(
                "/mcp/42",
                json=_rpc(
                    "tools/call",
                    {"name": "get_agent_status", "arguments": {"run_id": "xyz789"}},
                ),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
            content = json.loads(data["result"]["content"][0]["text"])
            assert content["run_id"] == "xyz789"
            assert content["name"] == "Atlas"
        finally:
            await client.close()


class TestGetAgentStatusMissingBgManager:
    async def test_get_agent_status_missing_bg_manager(self) -> None:
        """Without bg_manager, raises RuntimeError."""
        toolkit = _make_toolkit(bg_manager=None)

        with pytest.raises(RuntimeError, match="bg_manager not available"):
            await toolkit.call_tool("get_agent_status", {"run_id": "abc"}, user_id=42)
