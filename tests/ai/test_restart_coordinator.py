"""Tests for RestartCoordinator — TDD red phase."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from archon.ai.restart_coordinator import RestartCoordinator


# ---------------------------------------------------------------------------
# Async scheduling tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_sets_event_after_delay() -> None:
    """schedule() with short delay fires the event; wait() returns reason."""
    coord = RestartCoordinator()
    result = coord.schedule("config changed", delay_seconds=0.1)

    assert isinstance(result, str)
    reason, delay = await coord.wait()
    assert reason == "config changed"
    assert delay == 0.1


@pytest.mark.asyncio
async def test_schedule_raises_if_already_scheduled() -> None:
    """Calling schedule() twice raises RuntimeError."""
    coord = RestartCoordinator()
    coord.schedule("first", delay_seconds=5.0)

    with pytest.raises(RuntimeError, match="already scheduled"):
        coord.schedule("second", delay_seconds=1.0)

    coord.cancel()


@pytest.mark.asyncio
async def test_is_scheduled_property() -> None:
    """is_scheduled is False before schedule, True after."""
    coord = RestartCoordinator()
    assert coord.is_scheduled is False

    coord.schedule("update", delay_seconds=5.0)
    assert coord.is_scheduled is True

    coord.cancel()


@pytest.mark.asyncio
async def test_cancel_prevents_restart() -> None:
    """cancel() prevents the event from being set."""
    coord = RestartCoordinator()
    coord.schedule("rollback", delay_seconds=0.1)
    coord.cancel()

    assert coord.is_scheduled is False

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(coord.wait(), timeout=0.3)


@pytest.mark.asyncio
async def test_cancel_after_countdown_completes_clears_event() -> None:
    """cancel() after the countdown has already fired resets the coordinator cleanly.

    Without _event.clear() in cancel(), wait() would return immediately
    with stale data — a ghost restart.
    """
    coord = RestartCoordinator()
    coord.schedule("late cancel", delay_seconds=0.01)

    # Let the countdown fire
    await asyncio.sleep(0.05)
    assert coord.is_scheduled is False  # task is done

    # Cancel after the countdown already completed
    coord.cancel()

    # Coordinator must be fully reset: wait() must NOT return immediately
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(coord.wait(), timeout=0.2)

    assert coord.is_scheduled is False


# ---------------------------------------------------------------------------
# Cross-process rate-limiting tests
# ---------------------------------------------------------------------------


def test_check_restart_allowed_no_file(tmp_path: Path) -> None:
    """No restart file exists → restart is allowed."""
    coord = RestartCoordinator()
    assert coord.check_restart_allowed(tmp_path / "restart.stamp") is True


def test_check_restart_allowed_recent(tmp_path: Path) -> None:
    """Restart file with recent timestamp → restart NOT allowed."""
    stamp = tmp_path / "restart.stamp"
    stamp.write_text(str(time.time()))

    coord = RestartCoordinator()
    assert coord.check_restart_allowed(stamp) is False


def test_check_restart_allowed_old(tmp_path: Path) -> None:
    """Restart file with old timestamp (>60s ago) → restart allowed."""
    stamp = tmp_path / "restart.stamp"
    stamp.write_text(str(time.time() - 120))

    coord = RestartCoordinator()
    assert coord.check_restart_allowed(stamp) is True


def test_write_restart_timestamp(tmp_path: Path) -> None:
    """write_restart_timestamp writes a parseable float timestamp."""
    stamp = tmp_path / "restart.stamp"

    coord = RestartCoordinator()
    coord.write_restart_timestamp(stamp)

    assert stamp.exists()
    ts = float(stamp.read_text().strip())
    assert abs(ts - time.time()) < 2.0
