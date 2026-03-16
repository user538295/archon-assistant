"""Unit tests for ContextReminder (US-002)."""
import logging
import pytest
from pathlib import Path

from archon.ai.reminder import ContextReminder
from archon.config.loader import ReminderConfig


@pytest.fixture
def config() -> ReminderConfig:
    return ReminderConfig(enabled=True, interval_messages=5, interval_tokens=100)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def reminder_file(workspace: Path) -> Path:
    f = workspace / "REMINDER.md"
    f.write_text("Stay focused.")
    return f


# 1. Disabled — should_inject() returns False regardless
def test_disabled(workspace: Path) -> None:
    cfg = ReminderConfig(enabled=False, interval_messages=1, interval_tokens=1)
    r = ContextReminder(cfg, workspace)
    (workspace / "REMINDER.md").write_text("x")
    r.record_message()
    r.record_tokens(1000)
    assert r.should_inject() is False


# 2. File absent — should_inject() returns False even if thresholds exceeded
def test_file_absent(config: ReminderConfig, workspace: Path) -> None:
    r = ContextReminder(config, workspace)
    for _ in range(config.interval_messages):
        r.record_message()
    r.record_tokens(config.interval_tokens)
    assert r.should_inject() is False


# 3. Message threshold reached
def test_message_threshold(config: ReminderConfig, reminder_file: Path, workspace: Path) -> None:
    r = ContextReminder(config, workspace)
    for _ in range(config.interval_messages):
        r.record_message()
    assert r.should_inject() is True


# 4. Token threshold reached
def test_token_threshold(config: ReminderConfig, reminder_file: Path, workspace: Path) -> None:
    r = ContextReminder(config, workspace)
    r.record_tokens(config.interval_tokens)
    assert r.should_inject() is True


# 5. Whichever threshold fires first (token before message)
def test_whichever_first(config: ReminderConfig, reminder_file: Path, workspace: Path) -> None:
    r = ContextReminder(config, workspace)
    r.record_tokens(config.interval_tokens)  # token fires first
    assert r.should_inject() is True


# 6. Counters reset after build_reminder_message()
def test_reset_after_inject(config: ReminderConfig, reminder_file: Path, workspace: Path) -> None:
    r = ContextReminder(config, workspace)
    for _ in range(config.interval_messages):
        r.record_message()
    assert r.should_inject() is True
    r.build_reminder_message()
    assert r.should_inject() is False


# 7. build_reminder_message() wraps content in XML block
def test_wraps_content(config: ReminderConfig, reminder_file: Path, workspace: Path) -> None:
    r = ContextReminder(config, workspace)
    msg = r.build_reminder_message()
    assert "<system_reminder" in msg
    assert "Stay focused." in msg
    assert "</system_reminder>" in msg


# 8. Hot-reload — file re-read on every call to build_reminder_message()
def test_hot_reload(config: ReminderConfig, reminder_file: Path, workspace: Path) -> None:
    r = ContextReminder(config, workspace)
    reminder_file.write_text("Version 1")
    msg1 = r.build_reminder_message()
    assert "Version 1" in msg1

    reminder_file.write_text("Version 2")
    msg2 = r.build_reminder_message()
    assert "Version 2" in msg2


# 9. Below both thresholds — should_inject() returns False
def test_below_threshold(config: ReminderConfig, reminder_file: Path, workspace: Path) -> None:
    r = ContextReminder(config, workspace)
    r.record_message()  # 1 < 5
    r.record_tokens(50)  # 50 < 100
    assert r.should_inject() is False


# 10. message_count property reflects the current count
def test_message_count_property(config: ReminderConfig, workspace: Path) -> None:
    r = ContextReminder(config, workspace)
    assert r.message_count == 0
    r.record_message()
    r.record_message()
    assert r.message_count == 2


# 11. message_count resets to zero after build_reminder_message()
def test_message_count_resets(config: ReminderConfig, reminder_file: Path, workspace: Path) -> None:
    r = ContextReminder(config, workspace)
    r.record_message()
    r.record_message()
    r.build_reminder_message()
    assert r.message_count == 0


# 13. build_reminder_message() returns empty XML wrapper when file disappears (TOCTOU fix)
def test_build_reminder_message_file_missing(
    config: ReminderConfig, reminder_file: Path, workspace: Path
) -> None:
    r = ContextReminder(config, workspace)
    reminder_file.unlink()  # simulate deletion between should_inject() and build_reminder_message()
    msg = r.build_reminder_message()  # must not raise
    assert "<system_reminder" in msg
    assert "</system_reminder>" in msg


# 14. No duplicate property definitions — message_count defined exactly once
def test_no_duplicate_message_count_property(workspace: Path, config: ReminderConfig) -> None:
    """Python silently uses the last property definition when a class has duplicates.
    This test verifies that message_count is defined only once by checking the source.
    """
    import inspect
    source = inspect.getsource(ContextReminder)
    # Count occurrences of the property decorator followed by 'def message_count'
    import re
    matches = re.findall(r"@property\s+def message_count", source)
    assert len(matches) == 1, (
        f"message_count property defined {len(matches)} times — expected exactly 1"
    )



# ──────────────────────────────────────────────────────────────────
# read_and_wrap staticmethod + build_reminder_injection helper
# ──────────────────────────────────────────────────────────────────

from archon.ai.reminder import build_reminder_injection  # noqa: E402


def test_read_and_wrap_returns_xml_wrapped_content(tmp_path: Path) -> None:
    f = tmp_path / "REMINDER.md"
    f.write_text("Stay on task.", encoding="utf-8")
    result = ContextReminder.read_and_wrap(f)
    assert "<system_reminder" in result
    assert "Stay on task." in result
    assert "</system_reminder>" in result


def test_build_reminder_message_delegates_to_read_and_wrap(
    config: ReminderConfig, reminder_file: Path, workspace: Path
) -> None:
    """build_reminder_message() produces the same XML structure as read_and_wrap()."""
    r = ContextReminder(config, workspace)
    msg = r.build_reminder_message()
    direct = ContextReminder.read_and_wrap(reminder_file)
    # Both must produce identically-structured XML (same wrapper, same content)
    assert msg == direct


def test_build_reminder_injection_returns_wrapped_content(tmp_path: Path) -> None:
    (tmp_path / "REMINDER.md").write_text("Mandatory constraint.", encoding="utf-8")
    result = build_reminder_injection(tmp_path)
    assert result is not None
    assert "<system_reminder" in result
    assert "Mandatory constraint." in result
    assert "</system_reminder>" in result


def test_build_reminder_injection_file_missing(tmp_path: Path) -> None:
    assert build_reminder_injection(tmp_path) is None


def test_build_reminder_injection_empty_file(tmp_path: Path) -> None:
    (tmp_path / "REMINDER.md").write_text("", encoding="utf-8")
    assert build_reminder_injection(tmp_path) is None


def test_build_reminder_injection_whitespace_only(tmp_path: Path) -> None:
    (tmp_path / "REMINDER.md").write_text("   \n  \t  ", encoding="utf-8")
    assert build_reminder_injection(tmp_path) is None


def test_build_reminder_injection_ioerror_returns_none_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from unittest.mock import patch
    reminder_file = tmp_path / "REMINDER.md"
    reminder_file.write_text("content", encoding="utf-8")
    with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
        with caplog.at_level(logging.WARNING, logger="archon"):
            result = build_reminder_injection(tmp_path)
    assert result is None
    assert any("permission denied" in r.message or "REMINDER" in r.message for r in caplog.records)


# ── Fix 1: curly-brace safety ─────────────────────────────────────

def test_build_reminder_injection_handles_curly_braces_in_content(tmp_path: Path) -> None:
    """REMINDER.md with curly braces must NOT raise KeyError (Fix 1)."""
    (tmp_path / "REMINDER.md").write_text('Use dict {key: value} syntax', encoding="utf-8")
    result = build_reminder_injection(tmp_path)
    assert result is not None
    assert "{key: value}" in result


def test_build_reminder_message_handles_curly_braces_in_content(
    config: ReminderConfig, workspace: Path
) -> None:
    """build_reminder_message() with curly braces in REMINDER.md must NOT raise (Fix 1 + 5)."""
    (workspace / "REMINDER.md").write_text('{"json": "example"}', encoding="utf-8")
    r = ContextReminder(config, workspace)
    result = r.build_reminder_message()
    assert '{"json": "example"}' in result
    assert "<system_reminder" in result


# ── Fix 2: OSError catch in build_reminder_message() ─────────────

def test_build_reminder_message_handles_permission_error(
    config: ReminderConfig, workspace: Path
) -> None:
    """build_reminder_message() must not propagate PermissionError — return empty XML (Fix 2)."""
    from unittest.mock import patch
    r = ContextReminder(config, workspace)
    with patch.object(
        ContextReminder, "read_and_wrap", side_effect=PermissionError("denied")
    ):
        result = r.build_reminder_message()
    assert "<system_reminder" in result
    assert "</system_reminder>" in result


# ── Fix 6: size warning threshold ────────────────────────────────

def test_build_reminder_injection_warns_when_file_is_large(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """REMINDER.md > 8000 chars must log a WARNING but still return content (Fix 6)."""
    (tmp_path / "REMINDER.md").write_text("x" * 8001, encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="archon"):
        result = build_reminder_injection(tmp_path)
    assert result is not None  # warning does not suppress content
    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "8001" in m
        for m in warning_messages
    ), f"Expected size warning with char count, got: {warning_messages}"


# ── should_inject() debug logging ────────────────────────────────


def test_should_inject_logs_message_threshold(
    config: ReminderConfig, reminder_file: Path, workspace: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """should_inject() logs which threshold triggered — message threshold only."""
    r = ContextReminder(config, workspace)
    for _ in range(config.interval_messages):
        r.record_message()
    with caplog.at_level(logging.DEBUG, logger="archon"):
        result = r.should_inject()
    assert result is True
    debug_msgs = [rec.message for rec in caplog.records if rec.levelno == logging.DEBUG]
    assert any("message threshold" in m for m in debug_msgs), (
        f"Expected 'message threshold' in debug log, got: {debug_msgs}"
    )
    # Token threshold NOT reached, so it should not be mentioned as triggered
    assert not any("token threshold" in m for m in debug_msgs), (
        f"'token threshold' should not appear when only messages triggered: {debug_msgs}"
    )


def test_should_inject_logs_token_threshold(
    config: ReminderConfig, reminder_file: Path, workspace: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """should_inject() logs which threshold triggered — token threshold only."""
    r = ContextReminder(config, workspace)
    r.record_tokens(config.interval_tokens)
    with caplog.at_level(logging.DEBUG, logger="archon"):
        result = r.should_inject()
    assert result is True
    debug_msgs = [rec.message for rec in caplog.records if rec.levelno == logging.DEBUG]
    assert any("token threshold" in m for m in debug_msgs), (
        f"Expected 'token threshold' in debug log, got: {debug_msgs}"
    )
    assert not any("message threshold" in m for m in debug_msgs), (
        f"'message threshold' should not appear when only tokens triggered: {debug_msgs}"
    )


def test_should_inject_logs_both_thresholds(
    config: ReminderConfig, reminder_file: Path, workspace: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """should_inject() logs both thresholds when both are exceeded."""
    r = ContextReminder(config, workspace)
    for _ in range(config.interval_messages):
        r.record_message()
    r.record_tokens(config.interval_tokens)
    with caplog.at_level(logging.DEBUG, logger="archon"):
        result = r.should_inject()
    assert result is True
    debug_msgs = [rec.message for rec in caplog.records if rec.levelno == logging.DEBUG]
    assert any("message threshold" in m for m in debug_msgs), (
        f"Expected 'message threshold' in debug log, got: {debug_msgs}"
    )
    assert any("token threshold" in m for m in debug_msgs), (
        f"Expected 'token threshold' in debug log, got: {debug_msgs}"
    )


def test_should_inject_no_log_when_not_triggered(
    config: ReminderConfig, reminder_file: Path, workspace: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """should_inject() returns False and emits no threshold debug log."""
    r = ContextReminder(config, workspace)
    r.record_message()  # 1 < 5
    r.record_tokens(10)  # 10 < 100
    with caplog.at_level(logging.DEBUG, logger="archon"):
        result = r.should_inject()
    assert result is False
    debug_msgs = [rec.message for rec in caplog.records if rec.levelno == logging.DEBUG]
    assert not any("threshold" in m for m in debug_msgs), (
        f"No threshold log expected when not triggered, got: {debug_msgs}"
    )
