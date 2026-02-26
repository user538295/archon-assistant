"""Integration tests for ArchonMCPServer + BackgroundAgentManager working together — S15.3 + S15.2.

These tests exercise the HTTP layer (ArchonMCPServer) wired to real BackgroundAgentManager
instances, using mocked ClaudeSession to avoid real SDK calls.  They verify that:

  - An HTTP POST to tools/call actually spawns a BackgroundAgentManager task
  - Agent completion sends a Telegram notification (FR.003: NOT injected into main session)
  - The max_parallel limit is enforced through the MCP interface
  - Cancelling via manager.cancel() after MCP spawn sets status to 'cancelled'
"""
import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer
from unittest.mock import AsyncMock, MagicMock, patch

from archon.ai.archon_mcp_server import ArchonMCPServer
from archon.ai.background_agent_manager import BackgroundAgentManager


# ── Helpers ────────────────────────────────────────────────────────


def _make_mock_claude_session(result: str = "integrated result") -> MagicMock:
    """Fast mock ClaudeSession that completes immediately with *result*."""
    from archon.ai.event_mapper import Response

    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()

    async def _send(prompt: str):  # type: ignore[return]
        yield Response(content=result)

    session.send = _send
    return session


def _make_slow_claude_session() -> MagicMock:
    """Mock ClaudeSession that blocks in send() indefinitely (simulates long-running agent)."""
    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()

    async def _send(prompt: str):  # type: ignore[return]
        await asyncio.sleep(60)
        from archon.ai.event_mapper import Response
        yield Response(content="never")

    session.send = _send
    return session


def _rpc(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    req: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        req["params"] = params
    return req


async def _post_mcp(client: TestClient, user_id: int, body: dict) -> dict:
    resp = await client.post(f"/mcp/{user_id}", json=body)
    return await resp.json()


def _spawn_call(task: str, context: str = "") -> dict:
    return _rpc("tools/call", {
        "name": "spawn_background_agent",
        "arguments": {"task": task, "context": context},
    })


# ── Test 1: HTTP POST spawns a BackgroundAgentManager task ─────────


class TestMcpServerSpawnsAgent:
    async def test_mcp_server_spawns_agent_via_http_post(self) -> None:
        """HTTP POST to tools/call must spawn a background agent and return isError=False."""
        bot = MagicMock()
        bot.send_message = AsyncMock()
        sm = MagicMock()
        sm.get_or_create = AsyncMock(return_value=MagicMock())

        mock_agent = _make_mock_claude_session("done")
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=mock_agent):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm, max_parallel=5)
            server = ArchonMCPServer(manager=manager)
            client = TestClient(TestServer(server._app))
            await client.start_server()

            resp = await _post_mcp(client, 42, _spawn_call("count words in README"))

            if manager.list_all(42)[0]._task_ref:
                await asyncio.wait_for(
                    asyncio.shield(manager.list_all(42)[0]._task_ref), timeout=5.0
                )

            await client.close()

        assert resp["result"]["isError"] is False
        assert len(manager.list_all(42)) == 1
        assert manager.list_all(42)[0].task == "count words in README"


# ── Test 2: Telegram notification sent on agent completion ──────────


class TestMcpIntegrationNotification:
    async def test_mcp_server_triggers_telegram_notification(self) -> None:
        """Completing an agent spawned via MCP must send a Telegram ✅ notification to the correct user."""
        bot = MagicMock()
        bot.send_message = AsyncMock()
        sm = MagicMock()
        sm.get_or_create = AsyncMock(return_value=MagicMock())

        mock_agent = _make_mock_claude_session("notification payload")
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=mock_agent):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            server = ArchonMCPServer(manager=manager)
            client = TestClient(TestServer(server._app))
            await client.start_server()

            await _post_mcp(client, 99, _spawn_call("check notifications"))

            run = manager.list_all(99)[0]
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

            await client.close()

        # spawn notification + completion notification = 2 calls
        assert bot.send_message.await_count == 2
        calls = bot.send_message.call_args_list
        # First call: spawn notification
        assert calls[0][0][0] == 99
        assert "🤖" in calls[0][0][1]
        assert "spawned" in calls[0][0][1].lower()
        # Second call: completion notification
        assert calls[1][0][0] == 99
        assert "🤖" in calls[1][0][1]
        assert "completed" in calls[1][0][1].lower()


# ── Test 3: Max parallel enforced through the MCP interface ─────────


class TestMcpMaxParallel:
    async def test_mcp_respects_max_parallel_limit(self) -> None:
        """Spawning more agents than max_parallel via MCP must return isError=True for the excess request."""
        bot = MagicMock()
        bot.send_message = AsyncMock()
        sm = MagicMock()
        sm.get_or_create = AsyncMock(return_value=MagicMock())

        slow_session = _make_slow_claude_session()
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=slow_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm, max_parallel=2)
            server = ArchonMCPServer(manager=manager)
            client = TestClient(TestServer(server._app))
            await client.start_server()

            # Fill the pool to capacity (max_parallel=2)
            for _ in range(2):
                ok_resp = await _post_mcp(client, 1, _spawn_call("background work"))
                assert ok_resp["result"]["isError"] is False

            # The 3rd request must be rejected with a tool error
            overflow_resp = await _post_mcp(client, 1, _spawn_call("one too many"))

            await client.close()

        assert overflow_resp["result"]["isError"] is True


# ── Test 4: Cancel agent via manager after MCP spawn ────────────────


class TestMcpCancelWorkflow:
    async def test_mcp_cancel_agent_workflow(self) -> None:
        """Spawning via MCP then cancelling via manager.cancel() must transition status to 'cancelled'."""
        bot = MagicMock()
        bot.send_message = AsyncMock()
        sm = MagicMock()
        sm.get_or_create = AsyncMock(return_value=MagicMock())

        slow_session = _make_slow_claude_session()
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=slow_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            server = ArchonMCPServer(manager=manager)
            client = TestClient(TestServer(server._app))
            await client.start_server()

            await _post_mcp(client, 5, _spawn_call("long running task"))

            run = manager.list_all(5)[0]
            assert run.status == "running"

            cancelled = await manager.cancel(run.run_id)
            # Give asyncio a moment to propagate CancelledError through the task
            await asyncio.sleep(0.05)

            await client.close()

        assert cancelled is True
        assert run.status == "cancelled"


# ── Test 5: FR.15 — agent beacon fires via HTTP spawn ───────────────


def _make_pausing_session_for_integration(pause_secs: float = 0.15) -> MagicMock:
    """Mock session that yields events then pauses (long enough for beacon to fire)."""
    from archon.ai.event_mapper import Response, ToolStarted, ThinkingResult

    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()

    async def _send(prompt: str):  # type: ignore[return]
        yield ToolStarted(name="Read")
        yield ThinkingResult(content="pondering")
        await asyncio.sleep(pause_secs)
        yield Response(content="beacon integration result")

    session.send = _send
    return session


class TestMcpBeaconIntegration:
    async def test_beacon_fires_and_sends_new_messages_via_mcp(self) -> None:
        """When an agent is spawned via MCP and a short beacon interval is configured,
        send_message is called for each beacon — edit_message_text is never called."""
        sent_msg = MagicMock()
        sent_msg.message_id = 8888

        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=sent_msg)
        bot.edit_message_text = AsyncMock()

        sm = MagicMock()
        sm.get_or_create = AsyncMock(return_value=MagicMock())

        pausing_session = _make_pausing_session_for_integration(pause_secs=0.20)
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=pausing_session):
            # beacon_interval_minutes=0.001 ≈ 60ms → fires during 200ms pause
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, beacon_interval_minutes=0.001
            )
            server = ArchonMCPServer(manager=manager)
            client = TestClient(TestServer(server._app))
            await client.start_server()

            resp = await _post_mcp(client, 77, _spawn_call("beacon integration task"))
            assert resp["result"]["isError"] is False

            run = manager.list_all(77)[0]
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

            await client.close()

        # No edits ever — every beacon is a new message
        bot.edit_message_text.assert_not_called()
        # spawn + at least 1 beacon + completion = at least 3 send_message calls
        calls = bot.send_message.call_args_list
        assert len(calls) >= 3, (
            f"Expected ≥3 send_message calls (spawn + beacon + completion), got {len(calls)}"
        )
        # All calls use chat_id 77
        for c in calls:
            assert c[0][0] == 77
        # Second call (first beacon) contains the agent name
        first_beacon_text: str = calls[1][0][1]
        assert run.name in first_beacon_text

    async def test_beacon_disabled_via_mcp_when_interval_zero(self) -> None:
        """When beacon_interval_minutes=0, spawning via MCP never calls edit_message_text."""
        sent_msg = MagicMock()
        sent_msg.message_id = 1234

        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=sent_msg)
        bot.edit_message_text = AsyncMock()

        sm = MagicMock()
        sm.get_or_create = AsyncMock(return_value=MagicMock())

        pausing_session = _make_pausing_session_for_integration(pause_secs=0.20)
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=pausing_session):
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, beacon_interval_minutes=0
            )
            server = ArchonMCPServer(manager=manager)
            client = TestClient(TestServer(server._app))
            await client.start_server()

            await _post_mcp(client, 99, _spawn_call("no beacon mcp task"))

            run = manager.list_all(99)[0]
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

            await client.close()

        bot.edit_message_text.assert_not_called()
