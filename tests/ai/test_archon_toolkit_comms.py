"""Tests for send_notification tool — Task 4.1."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from aiohttp.test_utils import TestClient, TestServer

from archon.ai.archon_toolkit import ArchonToolkit


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_toolkit(*, bot: object | None = None, clock=None) -> ArchonToolkit:
    kwargs = {"bot": bot}
    if clock is not None:
        kwargs["clock"] = clock
    return ArchonToolkit(**kwargs)


def _rpc(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    req: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        req["params"] = params
    return req


# ──────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────


class TestSendNotificationSuccess:
    async def test_send_notification_success(self) -> None:
        """send_notification calls bot.send_message with correct args and returns success."""
        bot = MagicMock()
        bot.send_message = AsyncMock()

        toolkit = _make_toolkit(bot=bot)
        result = await toolkit.call_tool(
            "send_notification",
            {"user_id": 42, "message": "Hello!"},
        )

        bot.send_message.assert_called_once_with(chat_id=42, text="Hello!")
        assert result == "Notification sent."


class TestSendNotificationRateLimited:
    async def test_send_notification_rate_limited(self) -> None:
        """Second call within 10s is rejected with rate limit message."""
        clock_value = 0.0

        def _clock() -> float:
            return clock_value

        bot = MagicMock()
        bot.send_message = AsyncMock()

        toolkit = _make_toolkit(bot=bot, clock=_clock)

        # First call at t=0 — should succeed
        result1 = await toolkit.call_tool(
            "send_notification",
            {"user_id": 42, "message": "First"},
        )
        assert result1 == "Notification sent."

        # Second call at t=5 — within 10s window
        clock_value = 5.0
        result2 = await toolkit.call_tool(
            "send_notification",
            {"user_id": 42, "message": "Second"},
        )
        assert result2 == "Rate limited. Wait 5s."
        # bot.send_message should only have been called once
        assert bot.send_message.call_count == 1


class TestSendNotificationRateLimitExpires:
    async def test_send_notification_rate_limit_expires(self) -> None:
        """Call after 10+ seconds succeeds (rate limit expired)."""
        clock_value = 0.0

        def _clock() -> float:
            return clock_value

        bot = MagicMock()
        bot.send_message = AsyncMock()

        toolkit = _make_toolkit(bot=bot, clock=_clock)

        # First call at t=0
        result1 = await toolkit.call_tool(
            "send_notification",
            {"user_id": 42, "message": "First"},
        )
        assert result1 == "Notification sent."

        # Second call at t=11 — after 10s window
        clock_value = 11.0
        result2 = await toolkit.call_tool(
            "send_notification",
            {"user_id": 42, "message": "After window"},
        )
        assert result2 == "Notification sent."
        assert bot.send_message.call_count == 2


class TestSendNotificationTruncatesLongMessage:
    async def test_send_notification_truncates_long_message(self) -> None:
        """Message exceeding 4000 chars is truncated with '… [truncated]' suffix."""
        bot = MagicMock()
        bot.send_message = AsyncMock()

        long_message = "x" * 5000
        toolkit = _make_toolkit(bot=bot)
        result = await toolkit.call_tool(
            "send_notification",
            {"user_id": 42, "message": long_message},
        )

        assert result == "Notification sent."
        _, call_kwargs = bot.send_message.call_args
        sent_text = call_kwargs["text"]
        assert len(sent_text) <= 4000
        assert sent_text.endswith("… [truncated]")


class TestSendNotificationTelegramError:
    async def test_send_notification_telegram_error(self) -> None:
        """Exception from bot.send_message returns error string, no propagation."""
        bot = MagicMock()
        bot.send_message = AsyncMock(side_effect=Exception("Telegram down"))

        toolkit = _make_toolkit(bot=bot)
        result = await toolkit.call_tool(
            "send_notification",
            {"user_id": 42, "message": "Hello"},
        )

        assert result == "Failed to send: Telegram down"


class TestSendNotificationViaMcp:
    async def test_send_notification_via_mcp(self) -> None:
        """send_notification is callable via the background MCP server."""
        from archon.ai.archon_mcp_server import ArchonMCPServer
        from archon.ai.background_agent_manager import BackgroundAgentManager

        bot = MagicMock()
        bot.send_message = AsyncMock()
        sm = MagicMock()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm)

        toolkit = _make_toolkit(bot=bot)

        server = ArchonMCPServer(
            manager=manager, host="127.0.0.1", port=18297, toolkit=toolkit,
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            resp = await client.post(
                "/mcp/42",
                json=_rpc(
                    "tools/call",
                    {"name": "send_notification", "arguments": {"user_id": 42, "message": "Hi"}},
                ),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
            assert "Notification sent." in data["result"]["content"][0]["text"]
            bot.send_message.assert_called_once_with(chat_id=42, text="Hi")
        finally:
            await client.close()


class TestSendNotificationBotNotAvailable:
    async def test_send_notification_bot_not_available(self) -> None:
        """Without bot, call_tool raises RuntimeError."""
        toolkit = _make_toolkit(bot=None)

        with pytest.raises(RuntimeError, match="bot not available"):
            await toolkit.call_tool("send_notification", {"user_id": 42, "message": "Hi"})


class TestSendNotificationRateLimitLifecycle:
    async def test_send_notification_rate_limit_lifecycle(self) -> None:
        """E2E: send (ok) → immediate resend (rate limited) → after 10s (ok again)."""
        clock_value = 0.0

        def _clock() -> float:
            return clock_value

        bot = MagicMock()
        bot.send_message = AsyncMock()

        toolkit = _make_toolkit(bot=bot, clock=_clock)

        # First send — should succeed
        r1 = await toolkit.call_tool(
            "send_notification",
            {"user_id": 99, "message": "First"},
        )
        assert r1 == "Notification sent."

        # Immediate second send — rate limited
        r2 = await toolkit.call_tool(
            "send_notification",
            {"user_id": 99, "message": "Second"},
        )
        assert r2.startswith("Rate limited.")

        # Advance clock past 10s
        clock_value = 10.1
        r3 = await toolkit.call_tool(
            "send_notification",
            {"user_id": 99, "message": "Third"},
        )
        assert r3 == "Notification sent."

        assert bot.send_message.call_count == 2
