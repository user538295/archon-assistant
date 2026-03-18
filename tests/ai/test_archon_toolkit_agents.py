"""Tests for list_running_agents tool — Task 2.1."""
import json
import time

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
) -> MagicMock:
    agent = MagicMock()
    agent.run_id = run_id
    agent.name = name
    agent.task = task
    agent.started_at = started_at or time.monotonic() - 30
    agent.status = status
    agent.user_id = user_id
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
