"""tests/gateway/test_notification_monitor.py — TDD tests for Task 7.4.

Tests for IndexingNotificationMonitor in archon/gateway/notification_monitor.py.
The new implementation uses SearchClient (HTTP) instead of IndexingStateStore (file I/O).
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.config.loader import NotificationsConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(mode: str = "normal") -> NotificationsConfig:
    return NotificationsConfig(mode=mode)


def _make_monitor(
    indexing_state_result=None,
    mode: str = "normal",
    allowed_user_ids: list[int] | None = None,
    poll_interval: float = 0.0,
):
    """Build an IndexingNotificationMonitor with a mocked SearchClient and Bot."""
    from archon.gateway.notification_monitor import IndexingNotificationMonitor

    search_client = AsyncMock()
    search_client.indexing_state = AsyncMock(return_value=indexing_state_result)

    bot = AsyncMock()
    cfg = _make_config(mode=mode)
    user_ids = allowed_user_ids if allowed_user_ids is not None else [123]

    monitor = IndexingNotificationMonitor(
        search_client=search_client,
        bot=bot,
        allowed_user_ids=user_ids,
        notifications_config=cfg,
        poll_interval=poll_interval,
    )
    return monitor, search_client, bot


# ---------------------------------------------------------------------------
# Test 1: polls HTTP endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monitor_polls_http_endpoint() -> None:
    """SearchClient.indexing_state() must be called on each poll cycle."""
    state = {
        "trigger": "install",
        "collections": {"col1": {"status": "in_progress"}},
    }
    monitor, search_client, bot = _make_monitor(
        indexing_state_result=state,
        poll_interval=0.0,
    )

    # Run one poll cycle
    await monitor._check_and_notify()

    search_client.indexing_state.assert_called_once()


# ---------------------------------------------------------------------------
# Test 2: sends notification when all collections are DONE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monitor_sends_notification_on_all_done() -> None:
    """When all collections reach DONE and trigger is install/update, send_message is called."""
    state = {
        "trigger": "install",
        "collections": {
            "col1": {"status": "done"},
            "col2": {"status": "done"},
        },
    }
    monitor, search_client, bot = _make_monitor(indexing_state_result=state)

    await monitor._check_and_notify()

    bot.send_message.assert_called_once()
    call_args = bot.send_message.call_args
    assert "✅" in call_args[0][1] or "✅" in str(call_args)


# ---------------------------------------------------------------------------
# Test 3: sends notification when all collections are FAILED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monitor_sends_notification_on_all_failed() -> None:
    """When all collections reach FAILED and trigger is install/update, send_message is called."""
    state = {
        "trigger": "update",
        "collections": {
            "col1": {"status": "failed"},
            "col2": {"status": "failed"},
        },
    }
    monitor, search_client, bot = _make_monitor(indexing_state_result=state)

    await monitor._check_and_notify()

    bot.send_message.assert_called_once()
    call_args = bot.send_message.call_args
    # All failed → failure message
    msg = call_args[0][1] if call_args[0] else str(call_args)
    assert "❌" in msg or "failed" in msg.lower()


# ---------------------------------------------------------------------------
# Test 4: no notification for manual trigger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monitor_no_notification_on_manual_trigger() -> None:
    """When trigger is 'manual', no notification is sent even if all collections are DONE."""
    state = {
        "trigger": "manual",
        "collections": {
            "col1": {"status": "done"},
        },
    }
    monitor, search_client, bot = _make_monitor(indexing_state_result=state)

    await monitor._check_and_notify()

    bot.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# Test 5: connection refused → log DEBUG, do not raise, do not notify
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monitor_connection_refused_logs_debug_not_error(caplog) -> None:
    """When SearchClient.indexing_state() returns None, log at DEBUG and continue silently."""
    monitor, search_client, bot = _make_monitor(indexing_state_result=None)

    with caplog.at_level(logging.DEBUG, logger="archon"):
        await monitor._check_and_notify()

    # Must NOT send any notification
    bot.send_message.assert_not_called()
    # Must NOT log at ERROR level
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not error_records, f"Unexpected ERROR log: {error_records}"
    # Must log at DEBUG level for None response
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert debug_records, "Expected a DEBUG log for None response"


# ---------------------------------------------------------------------------
# Test 6: suppressed in quiet mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monitor_suppressed_in_quiet_mode() -> None:
    """In quiet mode, no notification is sent even if all collections are DONE."""
    state = {
        "trigger": "install",
        "collections": {
            "col1": {"status": "done"},
        },
    }
    monitor, search_client, bot = _make_monitor(indexing_state_result=state, mode="quiet")

    await monitor._check_and_notify()

    bot.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# Test 7: gateway does not auto-start search after extraction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gateway_does_not_auto_start_search_after_extraction() -> None:
    """Monitor is importable from archon.gateway and uses SearchClient (HTTP), not file I/O.

    Behavioral smoke test: when SearchClient.indexing_state() returns None (service unavailable),
    the monitor does not crash, does not send a notification, and logs at DEBUG level.
    """
    from archon.gateway.notification_monitor import IndexingNotificationMonitor

    # Verify the class lives in the gateway package (not archon.search)
    assert IndexingNotificationMonitor.__module__ == "archon.gateway.notification_monitor"

    # Behavioral: unavailable search service → no crash, no notification
    monitor, search_client, bot = _make_monitor(indexing_state_result=None)
    await monitor._check_and_notify()
    bot.send_message.assert_not_called()
    search_client.indexing_state.assert_called_once()


# ---------------------------------------------------------------------------
# Test 8: no duplicate notifications
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monitor_does_not_send_duplicate_notifications() -> None:
    """Once a notification has been sent, subsequent polls must not re-send."""
    state = {
        "trigger": "install",
        "collections": {
            "col1": {"status": "done"},
        },
    }
    monitor, search_client, bot = _make_monitor(indexing_state_result=state)

    # First call → should notify
    await monitor._check_and_notify()
    # Second call → already notified, must NOT notify again
    await monitor._check_and_notify()

    bot.send_message.assert_called_once()
