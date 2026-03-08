"""Unit tests for ContextReminder (US-002)."""
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


# 12. notify property reflects config value
def test_notify_property_true(workspace: Path) -> None:
    cfg = ReminderConfig(enabled=True, interval_messages=5, interval_tokens=100, notify=True)
    r = ContextReminder(cfg, workspace)
    assert r.notify is True


def test_notify_property_false(config: ReminderConfig, workspace: Path) -> None:
    r = ContextReminder(config, workspace)
    assert r.notify is False


# 13. build_reminder_message() returns empty XML wrapper when file disappears (TOCTOU fix)
def test_build_reminder_message_file_missing(
    config: ReminderConfig, reminder_file: Path, workspace: Path
) -> None:
    r = ContextReminder(config, workspace)
    reminder_file.unlink()  # simulate deletion between should_inject() and build_reminder_message()
    msg = r.build_reminder_message()  # must not raise
    assert "<system_reminder" in msg
    assert "</system_reminder>" in msg
