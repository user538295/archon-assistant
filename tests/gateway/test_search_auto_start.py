"""Tests for Task E.1: _detect_search_state(), Task E.2: _wait_for_search_service(), and
Task E.3: _register_search_state_notification() in gateway.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot, Dispatcher

from archon.config.loader import SearchConfig


def _make_rag_cfg(host: str = "localhost", port: int = 8282) -> SearchConfig:
    cfg = SearchConfig(url=f"http://{host}:{port}")
    return cfg


# ──────────────────────────────────────────────────────────────────
# TestDetectSearchState
# ──────────────────────────────────────────────────────────────────


class TestDetectSearchState:
    async def test_returns_running_when_probe_succeeds(self) -> None:
        from archon.gateway.gateway import SearchState, _detect_search_state

        with patch(
            "archon.gateway.gateway._ensure_search_server", new_callable=AsyncMock, return_value=True
        ):
            result = await _detect_search_state(_make_rag_cfg())

        assert result == SearchState.RUNNING

    async def test_returns_not_installed_when_lancedb_missing(self) -> None:
        from archon.gateway.gateway import SearchState, _detect_search_state

        with (
            patch(
                "archon.gateway.gateway._ensure_search_server",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("archon.gateway.gateway.importlib.util.find_spec", return_value=None),
        ):
            result = await _detect_search_state(_make_rag_cfg())

        assert result == SearchState.NOT_INSTALLED

    async def test_returns_not_running_when_packages_installed_probe_fails(
        self,
    ) -> None:
        from archon.gateway.gateway import SearchState, _detect_search_state

        with (
            patch(
                "archon.gateway.gateway._ensure_search_server",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "archon.gateway.gateway.importlib.util.find_spec",
                return_value=MagicMock(),  # non-None → lancedb importable
            ),
        ):
            result = await _detect_search_state(_make_rag_cfg())

        assert result == SearchState.NOT_RUNNING


# ──────────────────────────────────────────────────────────────────
# TestAutoStartRagService
# ──────────────────────────────────────────────────────────────────


class TestWaitForSearchService:
    async def test_returns_true_when_server_becomes_reachable(self) -> None:
        from archon.gateway.gateway import _wait_for_search_service

        sleep_call_count = 0

        async def mock_sleep(seconds: float) -> None:
            nonlocal sleep_call_count
            sleep_call_count += 1

        with (
            patch(
                "archon.gateway.gateway._ensure_search_server",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("asyncio.sleep", side_effect=mock_sleep),
        ):
            result = await _wait_for_search_service("localhost", 8282)

        assert result is True
        assert sleep_call_count == 0, "No sleep needed when probe succeeds immediately"

    async def test_returns_false_when_server_does_not_respond_within_timeout(self) -> None:
        from archon.gateway.gateway import _wait_for_search_service

        async def mock_ensure(host: str, port: int) -> bool:
            return False  # always unreachable

        sleep_call_count = 0

        async def mock_sleep(seconds: float) -> None:
            nonlocal sleep_call_count
            sleep_call_count += 1

        with (
            patch("archon.gateway.gateway._ensure_search_server", side_effect=mock_ensure),
            patch("asyncio.sleep", side_effect=mock_sleep),
        ):
            result = await _wait_for_search_service("localhost", 8282)

        assert result is False
        assert sleep_call_count == 30, "Should poll 30 times before giving up (30s timeout)"


# ──────────────────────────────────────────────────────────────────
# TestSearchStateNotification
# ──────────────────────────────────────────────────────────────────


def _make_dispatcher() -> Dispatcher:
    return Dispatcher()


def _make_bot() -> MagicMock:
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()
    return bot


class TestSearchStateNotification:
    async def test_not_installed_message_sent_to_all_users(self) -> None:
        from archon.gateway.gateway import SearchState, _register_search_state_notification

        dp = _make_dispatcher()
        _register_search_state_notification(
            dp,
            search_state=SearchState.NOT_INSTALLED,
            auto_started=False,
            allowed_user_ids=[100, 200],
        )

        bot = _make_bot()
        await dp.startup.trigger(bot)

        assert bot.send_message.await_count == 2
        text = bot.send_message.call_args_list[0].args[1]
        assert "archon search install" in text
        assert "archon search start" in text
        for call in bot.send_message.call_args_list:
            assert call.kwargs.get("parse_mode") == "HTML"

    async def test_not_running_auto_started_true_sends_success(self) -> None:
        from archon.gateway.gateway import SearchState, _register_search_state_notification

        dp = _make_dispatcher()
        _register_search_state_notification(
            dp,
            search_state=SearchState.NOT_RUNNING,
            auto_started=True,
            allowed_user_ids=[100],
        )

        bot = _make_bot()
        await dp.startup.trigger(bot)

        bot.send_message.assert_awaited_once()
        text = bot.send_message.call_args.args[1]
        assert "✅" in text
        assert "started automatically" in text

    async def test_not_running_auto_started_false_sends_failure(self) -> None:
        from archon.gateway.gateway import SearchState, _register_search_state_notification

        dp = _make_dispatcher()
        _register_search_state_notification(
            dp,
            search_state=SearchState.NOT_RUNNING,
            auto_started=False,
            allowed_user_ids=[100],
        )

        bot = _make_bot()
        await dp.startup.trigger(bot)

        bot.send_message.assert_awaited_once()
        text = bot.send_message.call_args.args[1]
        assert "⚠️" in text
        assert "archon search status" in text

    def test_running_no_notification_registered(self) -> None:
        from archon.gateway.gateway import SearchState, _register_search_state_notification

        dp = _make_dispatcher()
        before = len(dp.startup.handlers)

        _register_search_state_notification(
            dp,
            search_state=SearchState.RUNNING,
            auto_started=False,
            allowed_user_ids=[100],
        )

        assert len(dp.startup.handlers) == before

    async def test_per_user_error_isolation(self) -> None:
        from archon.gateway.gateway import SearchState, _register_search_state_notification

        dp = _make_dispatcher()
        _register_search_state_notification(
            dp,
            search_state=SearchState.NOT_INSTALLED,
            auto_started=False,
            allowed_user_ids=[100, 200],
        )

        bot = _make_bot()
        # First user raises, second should still receive the message
        bot.send_message.side_effect = [Exception("Telegram error"), None]
        await dp.startup.trigger(bot)

        assert bot.send_message.await_count == 2
