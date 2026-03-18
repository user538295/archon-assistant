"""Tests for agent tools — Task 2.1 (list_running_agents), Task 2.2 (get_agent_status), Task 2.3 (cancel_agent), Task 2.4 (read_agent_log)."""
import asyncio
import errno
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiohttp.test_utils import TestClient, TestServer

from archon.ai.archon_toolkit import ArchonToolkit
from archon.ai.background_agent_manager import BackgroundAgentManager
from tests.ai.conftest import _make_slow_claude_session


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


# ──────────────────────────────────────────────────────────────────
# Task 2.3 — cancel_agent tests
# ──────────────────────────────────────────────────────────────────


class TestCancelAgentSuccess:
    async def test_cancel_agent_success(self) -> None:
        """Cancel succeeds — returns confirmation message."""
        agent = _mock_agent(run_id="abc123", user_id=42)
        bg = MagicMock()
        bg.get_run.return_value = agent
        bg.cancel = AsyncMock(return_value=True)

        toolkit = _make_toolkit(bg_manager=bg)
        result = await toolkit.call_tool(
            "cancel_agent", {"run_id": "abc123"}, user_id=42,
        )

        assert result == "Agent abc123 cancelled."
        bg.cancel.assert_awaited_once_with("abc123")


class TestCancelAgentNotFound:
    async def test_cancel_agent_not_found(self) -> None:
        """Cancel returns False — agent not found or already finished."""
        bg = MagicMock()
        bg.cancel = AsyncMock(return_value=False)

        toolkit = _make_toolkit(bg_manager=bg)
        result = await toolkit.call_tool(
            "cancel_agent", {"run_id": "abc123"},
        )

        assert result == "Agent abc123 not found or already finished."


class TestCancelAgentMissingManager:
    async def test_cancel_agent_missing_manager(self) -> None:
        """Without bg_manager, raises RuntimeError."""
        toolkit = _make_toolkit(bg_manager=None)

        with pytest.raises(RuntimeError, match="bg_manager not available"):
            await toolkit.call_tool("cancel_agent", {"run_id": "abc"}, user_id=42)


class TestCancelAgentWrongUserRejected:
    async def test_cancel_agent_wrong_user_rejected(self) -> None:
        """Agent owned by user 42, cancelled by user 99 — returns not found."""
        agent = _mock_agent(run_id="abc123", user_id=42)
        bg = MagicMock()
        bg.get_run.return_value = agent
        bg.cancel = AsyncMock(return_value=True)

        toolkit = _make_toolkit(bg_manager=bg)
        result = await toolkit.call_tool(
            "cancel_agent", {"run_id": "abc123"}, user_id=99,
        )

        assert result == "Agent abc123 not found."
        bg.cancel.assert_not_called()


class TestCancelAgentNoUserIdSkipsAuth:
    async def test_cancel_agent_no_user_id_skips_auth(self) -> None:
        """Without user_id (orchestrator path), skips auth check."""
        bg = MagicMock()
        bg.cancel = AsyncMock(return_value=True)

        toolkit = _make_toolkit(bg_manager=bg)
        result = await toolkit.call_tool(
            "cancel_agent", {"run_id": "abc123"},
        )

        assert result == "Agent abc123 cancelled."
        bg.get_run.assert_not_called()
        bg.cancel.assert_awaited_once_with("abc123")


class TestCancelAgentViaMcp:
    async def test_cancel_agent_via_mcp(self) -> None:
        """Tool is callable via the background MCP server."""
        from archon.ai.archon_mcp_server import ArchonMCPServer
        from archon.ai.background_agent_manager import BackgroundAgentManager

        bot = MagicMock()
        bot.send_message = AsyncMock()
        sm = MagicMock()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm)

        agent = _mock_agent(run_id="abc123", user_id=42)
        bg = MagicMock()
        bg.get_run.return_value = agent
        bg.cancel = AsyncMock(return_value=True)

        toolkit = _make_toolkit(bg_manager=bg)

        server = ArchonMCPServer(
            manager=manager, host="127.0.0.1", port=18298, toolkit=toolkit,
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            # tools/list — verify cancel_agent is registered
            resp = await client.post(
                "/mcp/42",
                json=_rpc("tools/list"),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            tool_names = {t["name"] for t in data["result"]["tools"]}
            assert "cancel_agent" in tool_names

            # tools/call
            resp = await client.post(
                "/mcp/42",
                json=_rpc(
                    "tools/call",
                    {"name": "cancel_agent", "arguments": {"run_id": "abc123"}},
                ),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
            assert data["result"]["content"][0]["text"] == "Agent abc123 cancelled."
        finally:
            await client.close()


# ──────────────────────────────────────────────────────────────────
# Task 2.4 — read_agent_log tests
# ──────────────────────────────────────────────────────────────────


class TestReadAgentLogSuccess:
    async def test_read_agent_log_success(self, tmp_path: Path) -> None:
        """Agent with log file — returns log content."""
        log_file = tmp_path / "sessions" / "2026-03-18-10-00-atlas.md"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_content = "# Agent: Atlas\nSome log content here.\n"
        log_file.write_text(log_content, encoding="utf-8")

        mock_config = MagicMock()
        mock_config.history.directory = str(tmp_path)

        agent = _mock_agent(run_id="abc123", user_id=42, log_path=log_file)
        bg = MagicMock()
        bg.get_run.return_value = agent

        toolkit = _make_toolkit(bg_manager=bg, config=mock_config)
        result = await toolkit.call_tool(
            "read_agent_log",
            {"run_id": "abc123"},
            user_id=42,
        )

        assert "# Agent: Atlas" in result
        assert "Some log content here." in result


class TestReadAgentLogTailLines:
    async def test_read_agent_log_tail_lines(self, tmp_path: Path) -> None:
        """With 200 lines and tail_lines=50, returns only the last 50 lines."""
        log_file = tmp_path / "sessions" / "2026-03-18-10-00-atlas.md"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"line {i}" for i in range(1, 201)]
        log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        mock_config = MagicMock()
        mock_config.history.directory = str(tmp_path)

        agent = _mock_agent(run_id="abc123", user_id=42, log_path=log_file)
        bg = MagicMock()
        bg.get_run.return_value = agent

        toolkit = _make_toolkit(bg_manager=bg, config=mock_config)
        result = await toolkit.call_tool(
            "read_agent_log",
            {"run_id": "abc123", "tail_lines": 50},
            user_id=42,
        )

        result_lines = [l for l in result.splitlines() if l]
        assert len(result_lines) == 50
        assert "line 151" in result
        assert "line 200" in result
        assert "line 100" not in result
        assert "line 150" not in result


class TestReadAgentLogNotFound:
    async def test_read_agent_log_not_found(self) -> None:
        """Non-existent run_id returns error message."""
        bg = MagicMock()
        bg.get_run.return_value = None

        toolkit = _make_toolkit(bg_manager=bg)
        result = await toolkit.call_tool(
            "read_agent_log",
            {"run_id": "abc123"},
            user_id=42,
        )

        assert "not found" in result.lower()


class TestReadAgentLogPathTraversalBlocked:
    async def test_read_agent_log_path_traversal_blocked(self, tmp_path: Path) -> None:
        """log_path with path traversal outside sessions dir is rejected."""
        history_dir = tmp_path / "history"
        sessions_dir = history_dir / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)

        # A real file outside the sessions directory
        outside_file = tmp_path / "secret.md"
        outside_file.write_text("secret content", encoding="utf-8")

        # Mock config pointing to the controlled history directory
        mock_config = MagicMock()
        mock_config.history.directory = str(history_dir)

        agent = _mock_agent(run_id="abc123", user_id=42, log_path=outside_file)
        bg = MagicMock()
        bg.get_run.return_value = agent

        toolkit = _make_toolkit(bg_manager=bg, config=mock_config)
        result = await toolkit.call_tool(
            "read_agent_log",
            {"run_id": "abc123"},
            user_id=42,
        )

        assert "outside" in result.lower() or "invalid" in result.lower() or "not allowed" in result.lower()


class TestReadAgentLogSymlinkBlocked:
    async def test_read_agent_log_symlink_blocked(self, tmp_path: Path) -> None:
        """log_path that is a symlink is rejected."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)

        # Create a real file and a symlink pointing to it inside sessions dir
        real_file = tmp_path / "real.md"
        real_file.write_text("sensitive content", encoding="utf-8")
        symlink_path = sessions_dir / "2026-03-18-10-00-atlas.md"
        symlink_path.symlink_to(real_file)

        agent = _mock_agent(run_id="abc123", user_id=42, log_path=symlink_path)
        bg = MagicMock()
        bg.get_run.return_value = agent

        toolkit = _make_toolkit(bg_manager=bg)
        result = await toolkit.call_tool(
            "read_agent_log",
            {"run_id": "abc123"},
            user_id=42,
        )

        assert "invalid" in result.lower() or "symlink" in result.lower() or "error" in result.lower()


class TestReadAgentLogWrongUserRejected:
    async def test_read_agent_log_wrong_user_rejected(self, tmp_path: Path) -> None:
        """Agent owned by user 42, queried by user 99 — returns not found."""
        log_file = tmp_path / "sessions" / "2026-03-18-10-00-atlas.md"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text("content", encoding="utf-8")

        agent = _mock_agent(run_id="abc123", user_id=42, log_path=log_file)
        bg = MagicMock()
        bg.get_run.return_value = agent

        toolkit = _make_toolkit(bg_manager=bg)
        result = await toolkit.call_tool(
            "read_agent_log",
            {"run_id": "abc123"},
            user_id=99,
        )

        assert "not found" in result.lower()


class TestReadAgentLogViaMcp:
    async def test_read_agent_log_via_mcp(self, tmp_path: Path) -> None:
        """Tool is callable via the background MCP server."""
        from archon.ai.archon_mcp_server import ArchonMCPServer
        from archon.ai.background_agent_manager import BackgroundAgentManager

        log_file = tmp_path / "sessions" / "2026-03-18-10-00-atlas.md"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text("# Agent log content\n", encoding="utf-8")

        mock_config = MagicMock()
        mock_config.history.directory = str(tmp_path)

        bot = MagicMock()
        bot.send_message = AsyncMock()
        sm = MagicMock()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm)

        agent = _mock_agent(run_id="abc123", user_id=42, log_path=log_file)
        bg = MagicMock()
        bg.get_run.return_value = agent

        toolkit = _make_toolkit(bg_manager=bg, config=mock_config)

        server = ArchonMCPServer(
            manager=manager, host="127.0.0.1", port=18299, toolkit=toolkit,
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            # tools/list — verify read_agent_log is registered
            resp = await client.post(
                "/mcp/42",
                json=_rpc("tools/list"),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            tool_names = {t["name"] for t in data["result"]["tools"]}
            assert "read_agent_log" in tool_names

            # tools/call
            resp = await client.post(
                "/mcp/42",
                json=_rpc(
                    "tools/call",
                    {"name": "read_agent_log", "arguments": {"run_id": "abc123"}},
                ),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
            assert "# Agent log content" in data["result"]["content"][0]["text"]
        finally:
            await client.close()


class TestReadAgentLogNoLogPath:
    async def test_read_agent_log_no_log_path(self) -> None:
        """Agent with log_path=None returns error message."""
        agent = _mock_agent(run_id="abc123", user_id=42, log_path=None)
        bg = MagicMock()
        bg.get_run.return_value = agent

        toolkit = _make_toolkit(bg_manager=bg)
        result = await toolkit.call_tool(
            "read_agent_log",
            {"run_id": "abc123"},
            user_id=42,
        )

        assert "not available" in result.lower() or "not found" in result.lower() or "no log" in result.lower()


class TestReadAgentLogMissingBgManager:
    async def test_read_agent_log_missing_bg_manager(self) -> None:
        """Without bg_manager, raises RuntimeError."""
        toolkit = _make_toolkit(bg_manager=None)

        with pytest.raises(RuntimeError, match="bg_manager not available"):
            await toolkit.call_tool("read_agent_log", {"run_id": "abc"}, user_id=42)


class TestReadAgentLogNoUserIdSkipsAuth:
    async def test_read_agent_log_no_user_id_skips_auth(self, tmp_path: Path) -> None:
        """Without user_id (orchestrator path), ownership check is skipped."""
        history_dir = tmp_path / "history"
        sessions_dir = history_dir / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)

        log_file = sessions_dir / "2026-03-18-10-00-atlas.md"
        log_file.write_text("# Agent: Atlas\nLog content.\n", encoding="utf-8")

        mock_config = MagicMock()
        mock_config.history.directory = str(history_dir)

        # Agent owned by user 42
        agent = _mock_agent(run_id="abc123", user_id=42, log_path=log_file)
        bg = MagicMock()
        bg.get_run.return_value = agent

        toolkit = _make_toolkit(bg_manager=bg, config=mock_config)
        # Call without user_id — should return log content, not "not found"
        result = await toolkit.call_tool("read_agent_log", {"run_id": "abc123"})

        assert "# Agent: Atlas" in result
        assert "Log content." in result


class TestReadAgentLogOsError:
    async def test_read_agent_log_os_error(self, tmp_path: Path) -> None:
        """OSError during read returns 'Failed to read log' without leaking file path."""
        history_dir = tmp_path / "history"
        sessions_dir = history_dir / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)

        log_file = sessions_dir / "2026-03-18-10-00-atlas.md"
        log_file.write_text("content", encoding="utf-8")

        mock_config = MagicMock()
        mock_config.history.directory = str(history_dir)

        agent = _mock_agent(run_id="abc123", user_id=42, log_path=log_file)
        bg = MagicMock()
        bg.get_run.return_value = agent

        toolkit = _make_toolkit(bg_manager=bg, config=mock_config)

        with patch.object(Path, "read_text", side_effect=OSError(errno.EACCES, "Permission denied")):
            result = await toolkit.call_tool("read_agent_log", {"run_id": "abc123"}, user_id=42)

        assert "Failed to read log" in result
        assert str(tmp_path) not in result


class TestReadAgentLogUnicodeError:
    async def test_read_agent_log_unicode_error(self, tmp_path: Path) -> None:
        """UnicodeDecodeError during read returns the exact non-UTF-8 error message."""
        history_dir = tmp_path / "history"
        sessions_dir = history_dir / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)

        log_file = sessions_dir / "2026-03-18-10-00-atlas.md"
        log_file.write_text("content", encoding="utf-8")

        mock_config = MagicMock()
        mock_config.history.directory = str(history_dir)

        agent = _mock_agent(run_id="abc123", user_id=42, log_path=log_file)
        bg = MagicMock()
        bg.get_run.return_value = agent

        toolkit = _make_toolkit(bg_manager=bg, config=mock_config)

        with patch.object(
            Path, "read_text",
            side_effect=UnicodeDecodeError("utf-8", b"", 0, 1, "invalid start byte"),
        ):
            result = await toolkit.call_tool("read_agent_log", {"run_id": "abc123"}, user_id=42)

        assert result == "Failed to read log: file contains non-UTF-8 content"


# ──────────────────────────────────────────────────────────────────
# E2E tests — real BackgroundAgentManager (Tasks 2.1–2.4)
# ──────────────────────────────────────────────────────────────────


class TestListRunningAgentsWithRealBam:
    @pytest.mark.asyncio
    async def test_list_running_agents_with_real_bam(self, toolkit_with_real_bam) -> None:
        """Spawn a slow agent, call list_running_agents, assert it appears."""
        user_id = 42
        task_text = "E2E test task for list_running_agents"

        toolkit, bam, _sm, _bot = toolkit_with_real_bam()

        # Patch must stay active for the duration of the test so that the
        # asyncio task (which runs after spawn() returns) sees the mock.
        with patch("archon.ai.background_agent_manager.ClaudeSession",
                   return_value=_make_slow_claude_session(delay=30.0)):
            run = await bam.spawn(user_id=user_id, task=task_text)
            # Yield to the event loop so the agent task starts running
            await asyncio.sleep(0)

            try:
                result = await toolkit.call_tool("list_running_agents", {}, user_id=user_id)
                agents = json.loads(result)

                assert isinstance(agents, list)
                assert len(agents) == 1
                assert agents[0]["name"] == run.name
                assert agents[0]["status"] == "running"
            finally:
                await bam.cancel(run.run_id)
                await asyncio.wait_for(run.done.wait(), timeout=5.0)


class TestGetAgentStatusWithRealBam:
    @pytest.mark.asyncio
    async def test_get_agent_status_with_real_bam(self, toolkit_with_real_bam) -> None:
        """Spawn slow agent → running; cancel → cancelled."""
        user_id = 42

        toolkit, bam, _sm, _bot = toolkit_with_real_bam()

        with patch("archon.ai.background_agent_manager.ClaudeSession",
                   return_value=_make_slow_claude_session(delay=30.0)):
            run = await bam.spawn(user_id=user_id, task="E2E status task")
            await asyncio.sleep(0)

            try:
                # While running
                result = await toolkit.call_tool(
                    "get_agent_status", {"run_id": run.run_id}, user_id=user_id,
                )
                data = json.loads(result)
                assert data["status"] == "running"
                assert data["run_id"] == run.run_id
            finally:
                await bam.cancel(run.run_id)
                await asyncio.wait_for(run.done.wait(), timeout=5.0)

        # After cancellation (patch no longer needed — agent already done)
        result = await toolkit.call_tool(
            "get_agent_status", {"run_id": run.run_id}, user_id=user_id,
        )
        data = json.loads(result)
        assert data["status"] == "cancelled"


class TestCancelAgentWithRealBam:
    @pytest.mark.asyncio
    async def test_cancel_agent_with_real_bam(self, toolkit_with_real_bam) -> None:
        """Call cancel_agent, wait for done, assert cancelled and list empty."""
        user_id = 42

        toolkit, bam, _sm, _bot = toolkit_with_real_bam()

        with patch("archon.ai.background_agent_manager.ClaudeSession",
                   return_value=_make_slow_claude_session(delay=30.0)):
            run = await bam.spawn(user_id=user_id, task="E2E cancel task")
            await asyncio.sleep(0)

            result = await toolkit.call_tool(
                "cancel_agent", {"run_id": run.run_id}, user_id=user_id,
            )
            assert result == f"Agent {run.run_id} cancelled."

            await asyncio.wait_for(run.done.wait(), timeout=5.0)

        assert run.status == "cancelled"

        # No running agents remain
        list_result = await toolkit.call_tool("list_running_agents", {}, user_id=user_id)
        assert list_result == "No running agents."


class TestReadAgentLogWithRealBam:
    @pytest.mark.asyncio
    async def test_read_agent_log_with_real_bam(
        self, toolkit_with_real_bam, tmp_path: Path,
    ) -> None:
        """Spawn agent, write a log file, call read_agent_log, assert content."""
        user_id = 42
        task_text = "E2E read log task"

        # Set up a real log file
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir(parents=True)
        log_file = sessions_dir / "2026-01-01-00-00-test.md"
        log_file.write_text(f"# Agent log\nTask: {task_text}\n", encoding="utf-8")

        mock_config = MagicMock()
        mock_config.history.directory = str(tmp_path)

        toolkit, bam, _sm, _bot = toolkit_with_real_bam(config=mock_config)

        with patch("archon.ai.background_agent_manager.ClaudeSession",
                   return_value=_make_slow_claude_session(delay=30.0)):
            run = await bam.spawn(user_id=user_id, task=task_text)
            await asyncio.sleep(0)

            # Attach log file directly on the real AgentRun
            run.log_path = log_file

            try:
                result = await toolkit.call_tool(
                    "read_agent_log", {"run_id": run.run_id}, user_id=user_id,
                )
                assert task_text in result
                assert "# Agent log" in result
            finally:
                await bam.cancel(run.run_id)
                await asyncio.wait_for(run.done.wait(), timeout=5.0)
