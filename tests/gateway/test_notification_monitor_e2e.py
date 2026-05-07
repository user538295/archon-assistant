"""tests/gateway/test_notification_monitor_e2e.py — Suite 5: IndexingNotificationMonitor (H5.1–H5.8).

Real IndexingNotificationMonitor with mocked Telegram bot and SearchClient HTTP via AsyncMock.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

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
    msg = bot.send_message.call_args[0][1]
    assert "✅" in msg


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


# ---------------------------------------------------------------------------
# H5.5 — one DONE, one RUNNING → keeps polling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_H5_5_running_collection_keeps_polling() -> None:
    """H5.5: one DONE, one RUNNING → monitor keeps polling without sending notification."""
    state = _make_state("install", col1=_done(), col2=_running())
    monitor, search_client, bot = _make_monitor(indexing_state_return=state, poll_interval=0.0)

    await monitor._check_and_notify()
    await monitor._check_and_notify()
    await monitor._check_and_notify()

    bot.send_message.assert_not_called()
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
async def test_H5_7_install_trigger_fires_on_terminal() -> None:
    """H5.7: trigger='install' → notification fires once all collections reach a terminal state."""
    state = _make_state("install", col1=_done(), col2=_done())
    monitor, search_client, bot = _make_monitor(indexing_state_return=state)

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
