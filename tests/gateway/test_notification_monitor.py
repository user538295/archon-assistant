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


# ---------------------------------------------------------------------------
# M12.1: run() loop calls _check_and_notify() on each iteration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_loop_calls_check_and_notify_multiple_times() -> None:
    """run() must call _check_and_notify() on each poll iteration until cancelled."""
    monitor, search_client, bot = _make_monitor(
        indexing_state_result=None,  # no terminal state → keeps looping
        poll_interval=0.0,
    )

    call_count = 0
    original = monitor._check_and_notify

    async def counting_check() -> None:
        nonlocal call_count
        call_count += 1
        await original()
        if call_count >= 3:
            raise asyncio.CancelledError

    monitor._check_and_notify = counting_check  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        await monitor.run()

    assert call_count >= 3


# ---------------------------------------------------------------------------
# M12.2: run() calls asyncio.sleep with the configured poll_interval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_sleeps_for_poll_interval() -> None:
    """run() must sleep for poll_interval seconds between poll cycles."""
    poll_interval = 42.0
    monitor, search_client, bot = _make_monitor(
        indexing_state_result=None,
        poll_interval=poll_interval,
    )

    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        raise asyncio.CancelledError  # cancel after first sleep

    with patch("archon.gateway.notification_monitor.asyncio.sleep", side_effect=fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await monitor.run()

    assert sleep_calls == [poll_interval]


# ---------------------------------------------------------------------------
# M12.3: CancelledError propagates cleanly out of run()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_propagates_cancelled_error() -> None:
    """CancelledError raised inside run()'s sleep must propagate to the caller."""
    monitor, search_client, bot = _make_monitor(
        indexing_state_result=None,
        poll_interval=0.0,
    )

    async def raise_cancel(seconds: float) -> None:
        raise asyncio.CancelledError

    with patch("archon.gateway.notification_monitor.asyncio.sleep", side_effect=raise_cancel):
        with pytest.raises(asyncio.CancelledError):
            await monitor.run()


# ---------------------------------------------------------------------------
# M12.4: Unexpected exception in _check_and_notify is logged at ERROR, not re-raised
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_logs_unexpected_exception_at_error_level(caplog) -> None:
    """An unexpected exception inside _check_and_notify must be caught, logged at ERROR, and the loop continues."""
    monitor, search_client, bot = _make_monitor(
        indexing_state_result=None,
        poll_interval=0.0,
    )

    call_count = 0

    async def exploding_check() -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("boom")
        raise asyncio.CancelledError

    monitor._check_and_notify = exploding_check  # type: ignore[method-assign]

    with caplog.at_level(logging.ERROR, logger="archon"):
        with pytest.raises(asyncio.CancelledError):
            await monitor.run()

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert error_records, "Expected ERROR log for unexpected exception in poll cycle"
    assert "boom" in error_records[0].getMessage() or "boom" in str(error_records[0].exc_info)


# ---------------------------------------------------------------------------
# M12.5: _send_to_all partial failure — one succeeds, one fails → WARNING logged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_to_all_partial_failure_logs_warning(caplog) -> None:
    """When one user's send_message raises, a WARNING is logged and others still receive the message."""
    from archon.gateway.notification_monitor import IndexingNotificationMonitor

    search_client = AsyncMock()
    bot = AsyncMock()
    cfg = _make_config(mode="normal")

    # user 111 → success, user 222 → raises
    async def send_side_effect(user_id: int, message: str, **kwargs: object) -> None:
        if user_id == 222:
            raise Exception("delivery failed")

    bot.send_message = AsyncMock(side_effect=send_side_effect)

    monitor = IndexingNotificationMonitor(
        search_client=search_client,
        bot=bot,
        allowed_user_ids=[111, 222],
        notifications_config=cfg,
        poll_interval=0.0,
    )

    with caplog.at_level(logging.WARNING, logger="archon"):
        await monitor._send_to_all("test message")

    # user 111 should receive the message
    delivered_to = [
        (c.args[0] if c.args else c.kwargs.get("chat_id") or c.kwargs.get("user_id"))
        for c in bot.send_message.call_args_list
    ]
    assert 111 in delivered_to
    # WARNING must be logged for user 222
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warning_records, "Expected WARNING log for failed delivery"
    assert "222" in warning_records[0].getMessage()


# ---------------------------------------------------------------------------
# M12.6: _send_to_all with no allowed_user_ids logs WARNING and skips send
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_to_all_empty_user_ids_logs_warning(caplog) -> None:
    """When allowed_user_ids is empty, a WARNING is logged and no send_message is called."""
    monitor, search_client, bot = _make_monitor(
        indexing_state_result=None,
        allowed_user_ids=[],
        poll_interval=0.0,
    )

    with caplog.at_level(logging.WARNING, logger="archon"):
        await monitor._send_to_all("test message")

    bot.send_message.assert_not_called()
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warning_records, "Expected WARNING when no user IDs are configured"


# ---------------------------------------------------------------------------
# M12.7: _build_message with mixed done/failed → warning message with counts
# ---------------------------------------------------------------------------


def test_build_message_mixed_done_and_failed() -> None:
    """When some collections are done and some are failed, the message is a warning with failure count."""
    monitor, _, _ = _make_monitor(indexing_state_result=None)

    collections = {
        "col_ok": {"status": "done"},
        "col_bad": {"status": "failed"},
        "col_bad2": {"status": "failed"},
    }
    message = monitor._build_message(collections)

    # Should be a warning message (not pure success ✅, not pure failure ❌)
    assert "⚠️" in message or "warning" in message.lower() or "failed" in message.lower()
    # Should mention the failure count — both conditions required
    assert "2" in message and "failed" in message.lower()
