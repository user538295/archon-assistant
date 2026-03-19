"""Tests for the _restart_watcher function in gateway.py.

Tests the restart watcher in isolation — does NOT run full Gateway._run().
Follows the same patterns as test_shutdown.py.
"""
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.restart_coordinator import RestartCoordinator


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_bot(allowed_user_ids: list[int] | None = None) -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


def _make_config(allowed_user_ids: list[int] | None = None) -> MagicMock:
    cfg = MagicMock()
    cfg.access.allowed_user_ids = allowed_user_ids or [111, 222]
    return cfg


def _make_history_manager() -> MagicMock:
    hm = MagicMock()
    hm.record_archon_message = AsyncMock()
    return hm


# ──────────────────────────────────────────────────────────────────
# test_restart_watcher_triggers_restart
# ──────────────────────────────────────────────────────────────────


async def test_restart_watcher_triggers_restart(tmp_path: Path) -> None:
    """After coordinator fires, _restart_watcher must call restart_process()."""
    from archon.gateway.gateway import _restart_watcher

    coordinator = RestartCoordinator()
    bot = _make_bot()
    cfg = _make_config()
    hm = _make_history_manager()
    mock_runtime = MagicMock()
    mock_runtime.restart_process = MagicMock()

    coordinator.schedule("test reason", delay_seconds=0.01)

    with patch("archon.gateway.gateway.get_runtime", return_value=mock_runtime):
        with patch("archon.gateway.gateway._shutting_down", False):
            await _restart_watcher(coordinator, bot, cfg, hm, restart_file=tmp_path / ".last_restart")

    mock_runtime.restart_process.assert_called_once()


# ──────────────────────────────────────────────────────────────────
# test_restart_watcher_sends_notification
# ──────────────────────────────────────────────────────────────────


async def test_restart_watcher_sends_notification(tmp_path: Path) -> None:
    """bot.send_message must be called for each whitelisted user."""
    from archon.gateway.gateway import _restart_watcher

    coordinator = RestartCoordinator()
    bot = _make_bot()
    cfg = _make_config(allowed_user_ids=[111, 222, 333])
    hm = _make_history_manager()
    mock_runtime = MagicMock()
    mock_runtime.restart_process = MagicMock()

    coordinator.schedule("config updated", delay_seconds=0.01)

    with patch("archon.gateway.gateway.get_runtime", return_value=mock_runtime):
        with patch("archon.gateway.gateway._shutting_down", False):
            await _restart_watcher(coordinator, bot, cfg, hm, restart_file=tmp_path / ".last_restart")

    assert bot.send_message.await_count == 3
    # Check each user ID received a notification
    called_uids = {call.args[0] for call in bot.send_message.call_args_list}
    assert called_uids == {111, 222, 333}
    # Check the message contains the reason
    for call in bot.send_message.call_args_list:
        assert "config updated" in call.args[1]


# ──────────────────────────────────────────────────────────────────
# test_restart_watcher_writes_timestamp
# ──────────────────────────────────────────────────────────────────


async def test_restart_watcher_writes_timestamp(tmp_path: Path) -> None:
    """The .last_restart file must be created with a timestamp."""
    from archon.gateway.gateway import _restart_watcher

    coordinator = RestartCoordinator()
    bot = _make_bot()
    cfg = _make_config()
    hm = _make_history_manager()
    mock_runtime = MagicMock()
    mock_runtime.restart_process = MagicMock()

    restart_file = tmp_path / ".last_restart"
    coordinator.schedule("test", delay_seconds=0.01)

    with patch("archon.gateway.gateway.get_runtime", return_value=mock_runtime):
        with patch("archon.gateway.gateway._shutting_down", False):
            await _restart_watcher(coordinator, bot, cfg, hm, restart_file=restart_file)

    assert restart_file.exists()
    ts = float(restart_file.read_text().strip())
    assert ts > 0


# ──────────────────────────────────────────────────────────────────
# test_restart_watcher_appends_history
# ──────────────────────────────────────────────────────────────────


async def test_restart_watcher_appends_history(tmp_path: Path) -> None:
    """History manager must receive a restart message."""
    from archon.gateway.gateway import _restart_watcher

    coordinator = RestartCoordinator()
    bot = _make_bot()
    cfg = _make_config()
    hm = _make_history_manager()
    mock_runtime = MagicMock()
    mock_runtime.restart_process = MagicMock()

    coordinator.schedule("agent requested", delay_seconds=0.01)

    with patch("archon.gateway.gateway.get_runtime", return_value=mock_runtime):
        with patch("archon.gateway.gateway._shutting_down", False):
            await _restart_watcher(coordinator, bot, cfg, hm, restart_file=tmp_path / ".last_restart")

    hm.record_archon_message.assert_awaited_once()
    msg = hm.record_archon_message.call_args.args[0]
    assert "restart" in msg.lower() or "Restart" in msg


# ──────────────────────────────────────────────────────────────────
# test_restart_watcher_no_history_manager
# ──────────────────────────────────────────────────────────────────


async def test_restart_watcher_no_history_manager(tmp_path: Path) -> None:
    """When history_manager is None, watcher must not crash."""
    from archon.gateway.gateway import _restart_watcher

    coordinator = RestartCoordinator()
    bot = _make_bot()
    cfg = _make_config()
    mock_runtime = MagicMock()
    mock_runtime.restart_process = MagicMock()

    coordinator.schedule("test", delay_seconds=0.01)

    with patch("archon.gateway.gateway.get_runtime", return_value=mock_runtime):
        with patch("archon.gateway.gateway._shutting_down", False):
            await _restart_watcher(coordinator, bot, cfg, None, restart_file=tmp_path / ".last_restart")

    mock_runtime.restart_process.assert_called_once()


# ──────────────────────────────────────────────────────────────────
# test_restart_watcher_cancelled_cleanly
# ──────────────────────────────────────────────────────────────────


async def test_restart_watcher_cancelled_cleanly() -> None:
    """Cancelling the watcher task must not raise or leave side effects."""
    from archon.gateway.gateway import _restart_watcher

    coordinator = RestartCoordinator()
    bot = _make_bot()
    cfg = _make_config()
    hm = _make_history_manager()
    mock_runtime = MagicMock()

    # Do NOT schedule a restart — the watcher will block on coordinator.wait()
    task = asyncio.create_task(
        _restart_watcher(coordinator, bot, cfg, hm, restart_file=Path("/tmp/.test_restart")),
    )
    await asyncio.sleep(0.02)  # Let it start waiting
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    # No restart_process call, no notifications
    bot.send_message.assert_not_awaited()
    mock_runtime.restart_process.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# test_restart_watcher_skips_during_shutdown
# ──────────────────────────────────────────────────────────────────


async def test_restart_watcher_skips_during_shutdown(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If _shutting_down is True, the watcher must skip the restart."""
    from archon.gateway.gateway import _restart_watcher

    coordinator = RestartCoordinator()
    bot = _make_bot()
    cfg = _make_config()
    hm = _make_history_manager()
    mock_runtime = MagicMock()
    mock_runtime.restart_process = MagicMock()

    coordinator.schedule("test", delay_seconds=0.01)

    with patch("archon.gateway.gateway.get_runtime", return_value=mock_runtime):
        with patch("archon.gateway.gateway._shutting_down", True):
            with caplog.at_level(logging.WARNING, logger="archon"):
                await _restart_watcher(coordinator, bot, cfg, hm, restart_file=tmp_path / ".last_restart")

    mock_runtime.restart_process.assert_not_called()
    assert any("shutdown" in r.message.lower() for r in caplog.records)


# ──────────────────────────────────────────────────────────────────
# test_restart_watcher_notification_failure_swallowed
# ──────────────────────────────────────────────────────────────────


async def test_restart_watcher_notification_failure_swallowed(tmp_path: Path) -> None:
    """A failing send_message must not prevent the restart."""
    from archon.gateway.gateway import _restart_watcher

    coordinator = RestartCoordinator()
    bot = _make_bot()
    bot.send_message = AsyncMock(side_effect=RuntimeError("Telegram down"))
    cfg = _make_config()
    hm = _make_history_manager()
    mock_runtime = MagicMock()
    mock_runtime.restart_process = MagicMock()

    coordinator.schedule("test", delay_seconds=0.01)

    with patch("archon.gateway.gateway.get_runtime", return_value=mock_runtime):
        with patch("archon.gateway.gateway._shutting_down", False):
            await _restart_watcher(coordinator, bot, cfg, hm, restart_file=tmp_path / ".last_restart")

    # Restart still happens despite notification failure
    mock_runtime.restart_process.assert_called_once()


# ──────────────────────────────────────────────────────────────────
# test_restart_watcher_history_failure_swallowed
# ──────────────────────────────────────────────────────────────────


async def test_restart_watcher_history_failure_swallowed(tmp_path: Path) -> None:
    """A failing history_manager.record_archon_message must not prevent the restart."""
    from archon.gateway.gateway import _restart_watcher

    coordinator = RestartCoordinator()
    bot = _make_bot()
    cfg = _make_config()
    hm = _make_history_manager()
    hm.record_archon_message = AsyncMock(side_effect=RuntimeError("disk full"))
    mock_runtime = MagicMock()
    mock_runtime.restart_process = MagicMock()

    coordinator.schedule("test", delay_seconds=0.01)

    with patch("archon.gateway.gateway.get_runtime", return_value=mock_runtime):
        with patch("archon.gateway.gateway._shutting_down", False):
            await _restart_watcher(coordinator, bot, cfg, hm, restart_file=tmp_path / ".last_restart")

    # Restart still happens despite history failure
    mock_runtime.restart_process.assert_called_once()
