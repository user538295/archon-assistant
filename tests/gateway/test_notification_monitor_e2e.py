"""tests/gateway/test_notification_monitor_e2e.py — Suite 5: IndexingNotificationMonitor (H5.1–H5.8, E5.1–E5.7).

Real IndexingNotificationMonitor with mocked Telegram bot and SearchClient HTTP via AsyncMock.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

from archon.config.loader import NotificationsConfig
from archon.gateway.notification_monitor import IndexingNotificationMonitor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(trigger: str, **collections: dict) -> dict:
    return {"trigger": trigger, "collections": collections}


def _done() -> dict:
    return {"status": "done"}


def _failed() -> dict:
    return {"status": "failed"}


def _running() -> dict:
    return {"status": "in_progress"}


def _make_monitor(
    *,
    indexing_state_return=None,
    indexing_state_side_effect=None,
    mode: str = "normal",
    allowed_user_ids: list[int] | None = None,
    poll_interval: float = 0.0,
):
    search_client = AsyncMock()
    if indexing_state_side_effect is not None:
        search_client.indexing_state = AsyncMock(side_effect=indexing_state_side_effect)
    else:
        search_client.indexing_state = AsyncMock(return_value=indexing_state_return)

    bot = AsyncMock()
    cfg = NotificationsConfig(mode=mode)
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
# H5.1 — all DONE → notification sent exactly once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_H5_1_all_done_sends_notification_once() -> None:
    """H5.1: all collections DONE with install trigger → notification sent exactly once."""
    state = _make_state("install", col1=_done(), col2=_done())
    monitor, search_client, bot = _make_monitor(indexing_state_return=state)

    await monitor._check_and_notify()
    await monitor._check_and_notify()  # second call must be suppressed

    bot.send_message.assert_called_once()
    args, kwargs = bot.send_message.call_args
    assert args[0] == 123
    msg = args[1]
    assert "✅" in msg
    assert kwargs.get("parse_mode") == "HTML"


# ---------------------------------------------------------------------------
# H5.2 — mix DONE + FAILED → warning summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_H5_2_mix_done_failed_sends_warning_summary() -> None:
    """H5.2: mix of DONE and FAILED → notification contains failure summary (⚠️)."""
    state = _make_state("install", col1=_done(), col2=_failed())
    monitor, search_client, bot = _make_monitor(indexing_state_return=state)

    await monitor._check_and_notify()

    bot.send_message.assert_called_once()
    msg = bot.send_message.call_args[0][1]
    assert "⚠️" in msg


# ---------------------------------------------------------------------------
# H5.3 — quiet mode → no notification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_H5_3_quiet_mode_no_notification() -> None:
    """H5.3: quiet mode → no notification even when all collections are DONE."""
    state = _make_state("install", col1=_done())
    monitor, search_client, bot = _make_monitor(indexing_state_return=state, mode="quiet")

    await monitor._check_and_notify()

    bot.send_message.assert_not_called()
    assert not monitor._notified, "quiet mode must not latch _notified=True"


# ---------------------------------------------------------------------------
# H5.4 — trigger="manual" → no notification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_H5_4_manual_trigger_no_notification() -> None:
    """H5.4: trigger='manual' → no notification sent."""
    state = _make_state("manual", col1=_done())
    monitor, search_client, bot = _make_monitor(indexing_state_return=state)

    await monitor._check_and_notify()

    bot.send_message.assert_not_called()
    assert not monitor._notified, "trigger='manual' must not latch _notified=True"


# ---------------------------------------------------------------------------
# H5.5 — one DONE, one RUNNING → keeps polling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_H5_5_running_collection_keeps_polling() -> None:
    """H5.5: one DONE, one RUNNING → monitor keeps polling; fires notification once all terminal."""
    non_terminal = _make_state("install", col1=_done(), col2=_running())
    terminal = _make_state("install", col1=_done(), col2=_done())

    monitor, search_client, bot = _make_monitor(
        indexing_state_side_effect=[non_terminal, non_terminal, terminal],
        poll_interval=0.0,
    )

    await monitor._check_and_notify()
    await monitor._check_and_notify()
    assert bot.send_message.call_count == 0  # still non-terminal

    await monitor._check_and_notify()
    assert bot.send_message.call_count == 1  # terminal reached → fires
    assert search_client.indexing_state.call_count == 3


# ---------------------------------------------------------------------------
# H5.6 — all DONE → sends, then stops sending
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_H5_6_all_done_sends_then_stops() -> None:
    """H5.6: all DONE → sends notification on first terminal poll, suppresses all subsequent polls."""
    state = _make_state("install", col1=_done())
    monitor, search_client, bot = _make_monitor(indexing_state_return=state, poll_interval=0.0)

    await monitor._check_and_notify()
    assert bot.send_message.call_count == 1

    await monitor._check_and_notify()
    await monitor._check_and_notify()
    assert bot.send_message.call_count == 1


# ---------------------------------------------------------------------------
# H5.7 — trigger="install" fires after terminal state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_H5_7_install_trigger_fires_after_non_terminal_polling() -> None:
    """H5.7: trigger='install' → notification fires once terminal state reached after polling through non-terminal."""
    non_terminal = _make_state("install", col1=_running())
    terminal = _make_state("install", col1=_done())

    monitor, search_client, bot = _make_monitor(
        indexing_state_side_effect=[non_terminal, terminal]
    )

    await monitor._check_and_notify()
    bot.send_message.assert_not_called()  # still running

    await monitor._check_and_notify()
    bot.send_message.assert_called_once()
    msg = bot.send_message.call_args[0][1]
    assert "✅" in msg


# ---------------------------------------------------------------------------
# H5.8 — trigger="update" fires after terminal state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_H5_8_update_trigger_fires_on_terminal() -> None:
    """H5.8: trigger='update' → notification fires once all collections reach a terminal state."""
    state = _make_state("update", col1=_done(), col2=_failed())
    monitor, search_client, bot = _make_monitor(indexing_state_return=state)

    await monitor._check_and_notify()

    bot.send_message.assert_called_once()
    msg = bot.send_message.call_args[0][1]
    assert "⚠️" in msg


# ---------------------------------------------------------------------------
# E5.1 — indexing_state() returns None → logs and retries, no crash
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_E5_1_indexing_state_none_no_crash(caplog) -> None:
    """E5.1: indexing_state() returns None → logs debug, returns without crash, retries on next call."""
    monitor, search_client, bot = _make_monitor(indexing_state_return=None)

    with caplog.at_level(logging.DEBUG, logger="archon"):
        await monitor._check_and_notify()
        await monitor._check_and_notify()

    bot.send_message.assert_not_called()
    assert search_client.indexing_state.call_count == 2
    assert not monitor._notified


# ---------------------------------------------------------------------------
# E5.2 — state = {} → keeps polling (no "collections" key)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_E5_2_empty_state_keeps_polling() -> None:
    """E5.2: state={} → no collections key → keeps polling without notification."""
    monitor, search_client, bot = _make_monitor(indexing_state_return={})

    await monitor._check_and_notify()
    await monitor._check_and_notify()

    bot.send_message.assert_not_called()
    assert search_client.indexing_state.call_count == 2
    assert not monitor._notified


# ---------------------------------------------------------------------------
# E5.3 — first call succeeds, subsequent calls return None → continues polling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_E5_3_first_success_then_none_continues_polling() -> None:
    """E5.3: first call returns running state (no notification), subsequent calls return None → continues polling."""
    running_state = _make_state("install", col1=_running())
    monitor, search_client, bot = _make_monitor(
        indexing_state_side_effect=[running_state, None, None]
    )

    await monitor._check_and_notify()
    await monitor._check_and_notify()
    await monitor._check_and_notify()

    bot.send_message.assert_not_called()
    assert search_client.indexing_state.call_count == 3
    assert not monitor._notified


# ---------------------------------------------------------------------------
# E5.4 — state dict has no "trigger" key → no notification, no crash
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_E5_4_no_trigger_key_no_notification() -> None:
    """E5.4: state dict missing 'trigger' key → no notification sent, no crash."""
    state = {"collections": {"col1": _done()}}  # no "trigger" key
    monitor, search_client, bot = _make_monitor(indexing_state_return=state)

    await monitor._check_and_notify()

    bot.send_message.assert_not_called()
    assert not monitor._notified


# ---------------------------------------------------------------------------
# E5.5 — trigger="sync" → no notification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_E5_5_sync_trigger_no_notification() -> None:
    """E5.5: trigger='sync' → not in ('install', 'update') → no notification."""
    state = _make_state("sync", col1=_done())
    monitor, search_client, bot = _make_monitor(indexing_state_return=state)

    await monitor._check_and_notify()

    bot.send_message.assert_not_called()
    assert not monitor._notified


# ---------------------------------------------------------------------------
# E5.6 — _send_to_all raises mid-notification → _notified already True (regression guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_E5_6_send_to_all_raises_notified_set_before_await(caplog) -> None:
    """E5.6: _send_to_all raises → _notified was set True BEFORE the await (ordering regression guard).

    Production code (notification_monitor.py ~line 77-78):
        self._notified = True          # set BEFORE await
        await self._send_to_all(msg)   # may raise

    Asserts:
    (a) Exception propagates out of _check_and_notify and is logged at ERROR by the caller (run()).
    (b) _notified=True is stuck → subsequent poll cycles skip re-notification.
    # TODO C1-I-43: consider reset _notified on send failure to allow retry
    """
    state = _make_state("install", col1=_done())
    monitor, search_client, bot = _make_monitor(indexing_state_return=state)

    send_error = RuntimeError("Telegram unavailable")
    _archon_logger = logging.getLogger("archon")

    with patch.object(monitor, "_send_to_all", new=AsyncMock(side_effect=send_error)):
        with caplog.at_level(logging.ERROR, logger="archon"):
            # _check_and_notify does not catch — run() would catch and log ERROR.
            # Simulate that: catch here and log as run() does.
            try:
                await monitor._check_and_notify()
            except RuntimeError as exc:
                _archon_logger.error(
                    "IndexingNotificationMonitor: unexpected error in poll cycle: %s",
                    exc,
                )

    # (a) ERROR was logged (as run() would produce)
    assert any(
        "unexpected error" in r.message
        for r in caplog.records
        if r.levelno >= logging.ERROR
    ), "expected ERROR log from simulated run() error handler"

    # (b) _notified is True — set before the failing await, so it stays True
    assert monitor._notified is True

    # Subsequent poll is skipped due to _notified=True; indexing_state only called once total
    await monitor._check_and_notify()
    assert search_client.indexing_state.call_count == 1


# ---------------------------------------------------------------------------
# E5.7 — CancelledError during indexing_state() → propagates cleanly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_E5_7_cancelled_error_propagates_cleanly() -> None:
    """E5.7: CancelledError raised during await indexing_state() → propagates, no partial state written."""
    monitor, search_client, bot = _make_monitor(
        indexing_state_side_effect=asyncio.CancelledError
    )

    with pytest.raises(asyncio.CancelledError):
        await monitor._check_and_notify()

    # No notification sent, no partial state
    bot.send_message.assert_not_called()
    assert not monitor._notified
