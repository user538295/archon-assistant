"""Tests for crash-loop guard — startup notification gating."""
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from archon.gateway.startup_guard import should_send_startup_notification


# ──────────────────────────────────────────────────────────────────
# 1. First start (no file exists) → returns True, creates file
# ──────────────────────────────────────────────────────────────────


async def test_first_start_returns_true(tmp_path: Path) -> None:
    """First-ever start (no .last_startup file) must return True."""
    stamp_file = tmp_path / ".last_startup"
    result = await should_send_startup_notification(stamp_file)
    assert result is True


async def test_first_start_creates_file(tmp_path: Path) -> None:
    """First-ever start must write the timestamp file."""
    stamp_file = tmp_path / ".last_startup"
    await should_send_startup_notification(stamp_file)
    assert stamp_file.exists()


async def test_first_start_writes_valid_iso_timestamp(tmp_path: Path) -> None:
    """The written timestamp must be a valid ISO 8601 datetime."""
    stamp_file = tmp_path / ".last_startup"
    await should_send_startup_notification(stamp_file)
    content = stamp_file.read_text().strip()
    parsed = datetime.fromisoformat(content)
    assert parsed.tzinfo is not None  # must be timezone-aware


# ──────────────────────────────────────────────────────────────────
# 2. Normal restart (>= 30s since last) → returns True
# ──────────────────────────────────────────────────────────────────


async def test_normal_restart_returns_true(tmp_path: Path) -> None:
    """Restart after >= 30s must return True."""
    stamp_file = tmp_path / ".last_startup"
    old_time = datetime.now(timezone.utc) - timedelta(seconds=60)
    stamp_file.write_text(old_time.isoformat())

    result = await should_send_startup_notification(stamp_file)
    assert result is True


async def test_normal_restart_updates_timestamp(tmp_path: Path) -> None:
    """Restart after >= 30s must update the timestamp file."""
    stamp_file = tmp_path / ".last_startup"
    old_time = datetime.now(timezone.utc) - timedelta(seconds=60)
    stamp_file.write_text(old_time.isoformat())

    await should_send_startup_notification(stamp_file)

    new_content = stamp_file.read_text().strip()
    new_time = datetime.fromisoformat(new_content)
    assert new_time > old_time


# ──────────────────────────────────────────────────────────────────
# 2b. Boundary: elapsed == 30s → returns True (not suppressed)
# ──────────────────────────────────────────────────────────────────


async def test_boundary_exactly_30s_returns_true(tmp_path: Path) -> None:
    """Elapsed time exactly at threshold (30s) must NOT be suppressed."""
    stamp_file = tmp_path / ".last_startup"
    boundary_time = datetime.now(timezone.utc) - timedelta(seconds=30)
    stamp_file.write_text(boundary_time.isoformat())

    result = await should_send_startup_notification(stamp_file)
    assert result is True


# ──────────────────────────────────────────────────────────────────
# 3. Crash-loop restart (< 30s since last) → returns False
# ──────────────────────────────────────────────────────────────────


async def test_crash_loop_returns_false(tmp_path: Path) -> None:
    """Restart within < 30s must return False (crash-loop guard)."""
    stamp_file = tmp_path / ".last_startup"
    recent_time = datetime.now(timezone.utc) - timedelta(seconds=5)
    stamp_file.write_text(recent_time.isoformat())

    result = await should_send_startup_notification(stamp_file)
    assert result is False


async def test_crash_loop_still_updates_timestamp(tmp_path: Path) -> None:
    """Even when suppressing (returning False), the timestamp must be updated."""
    stamp_file = tmp_path / ".last_startup"
    recent_time = datetime.now(timezone.utc) - timedelta(seconds=5)
    stamp_file.write_text(recent_time.isoformat())

    await should_send_startup_notification(stamp_file)

    new_content = stamp_file.read_text().strip()
    new_time = datetime.fromisoformat(new_content)
    assert new_time > recent_time


# ──────────────────────────────────────────────────────────────────
# 4. Corrupted file content → returns True (treated as first start)
# ──────────────────────────────────────────────────────────────────


async def test_corrupted_file_returns_true(tmp_path: Path) -> None:
    """Corrupted/unreadable file content must be treated as first start."""
    stamp_file = tmp_path / ".last_startup"
    stamp_file.write_text("not-a-timestamp\ngarbage")

    result = await should_send_startup_notification(stamp_file)
    assert result is True


async def test_empty_file_returns_true(tmp_path: Path) -> None:
    """Empty file must be treated as corrupted (first start)."""
    stamp_file = tmp_path / ".last_startup"
    stamp_file.write_text("")

    result = await should_send_startup_notification(stamp_file)
    assert result is True


async def test_corrupted_file_overwrites_with_valid_timestamp(tmp_path: Path) -> None:
    """Corrupted file must be overwritten with a valid timestamp."""
    stamp_file = tmp_path / ".last_startup"
    stamp_file.write_text("corrupted!")

    await should_send_startup_notification(stamp_file)

    content = stamp_file.read_text().strip()
    parsed = datetime.fromisoformat(content)
    assert parsed.tzinfo is not None


# ──────────────────────────────────────────────────────────────────
# 5. Directory doesn't exist → creates it and returns True
# ──────────────────────────────────────────────────────────────────


async def test_missing_directory_returns_true(tmp_path: Path) -> None:
    """When the parent directory doesn't exist, it must be created and return True."""
    stamp_file = tmp_path / "nonexistent" / "subdir" / ".last_startup"
    result = await should_send_startup_notification(stamp_file)
    assert result is True


async def test_missing_directory_creates_path(tmp_path: Path) -> None:
    """When the parent directory doesn't exist, it must be created along with the file."""
    stamp_file = tmp_path / "nonexistent" / "subdir" / ".last_startup"
    await should_send_startup_notification(stamp_file)
    assert stamp_file.exists()


# ──────────────────────────────────────────────────────────────────
# 6. Clock jump backward → returns True (not a crash loop)
# ──────────────────────────────────────────────────────────────────


async def test_negative_elapsed_returns_true(tmp_path: Path) -> None:
    """A clock jump backward (negative elapsed) must not suppress the notification."""
    stamp_file = tmp_path / ".last_startup"
    future_time = datetime.now(timezone.utc) + timedelta(seconds=60)
    stamp_file.write_text(future_time.isoformat())

    result = await should_send_startup_notification(stamp_file)
    assert result is True
