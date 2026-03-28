"""Unit tests for ContextReminder (US-002)."""
import logging
import pytest
from pathlib import Path
from unittest.mock import patch

from archon.ai.reminder import ContextReminder, build_reminder_injection, _merge_contents, _read_file_safe
from archon.config.loader import ReminderConfig

import archon.ai.reminder as reminder_module


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


@pytest.fixture
def patched_system_file(tmp_path: Path):
    """Patch _SYSTEM_REMINDER_FILE to a tmp_path location (absent by default)."""
    system_file = tmp_path / "system_reminder.md"
    with patch("archon.ai.reminder._SYSTEM_REMINDER_FILE", new=system_file):
        yield system_file


# 1. Disabled — should_inject() returns False regardless
def test_disabled(workspace: Path) -> None:
    cfg = ReminderConfig(enabled=False, interval_messages=1, interval_tokens=1)
    r = ContextReminder(cfg, workspace)
    (workspace / "REMINDER.md").write_text("x")
    r.record_message()
    r.record_tokens(1000)
    assert r.should_inject() is False


# 2. Both files absent — should_inject() returns False even if thresholds exceeded
def test_file_absent(config: ReminderConfig, workspace: Path, tmp_path: Path) -> None:
    system_file = tmp_path / "absent_system.md"  # does not exist
    with patch("archon.ai.reminder._SYSTEM_REMINDER_FILE", new=system_file):
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
def test_wraps_content(
    config: ReminderConfig, reminder_file: Path, workspace: Path, patched_system_file: Path
) -> None:
    r = ContextReminder(config, workspace)
    msg = r.build_reminder_message()
    assert "<system_reminder" in msg
    assert "Stay focused." in msg
    assert "</system_reminder>" in msg


# 8. Hot-reload — file re-read on every call to build_reminder_message()
def test_hot_reload(
    config: ReminderConfig, reminder_file: Path, workspace: Path, patched_system_file: Path
) -> None:
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
    config: ReminderConfig, reminder_file: Path, workspace: Path, patched_system_file: Path
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
# build_reminder_injection helper
# ──────────────────────────────────────────────────────────────────

def test_build_reminder_injection_returns_wrapped_content(
    tmp_path: Path, patched_system_file: Path
) -> None:
    (tmp_path / "REMINDER.md").write_text("Mandatory constraint.", encoding="utf-8")
    result = build_reminder_injection(tmp_path)
    assert result is not None
    assert "<system_reminder" in result
    assert "Mandatory constraint." in result
    assert "</system_reminder>" in result


def test_build_reminder_injection_file_missing(
    tmp_path: Path, patched_system_file: Path
) -> None:
    assert build_reminder_injection(tmp_path) is None


def test_build_reminder_injection_empty_file(
    tmp_path: Path, patched_system_file: Path
) -> None:
    (tmp_path / "REMINDER.md").write_text("", encoding="utf-8")
    assert build_reminder_injection(tmp_path) is None


def test_build_reminder_injection_whitespace_only(
    tmp_path: Path, patched_system_file: Path
) -> None:
    (tmp_path / "REMINDER.md").write_text("   \n  \t  ", encoding="utf-8")
    assert build_reminder_injection(tmp_path) is None


def test_build_reminder_injection_ioerror_returns_none_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, patched_system_file: Path
) -> None:
    reminder_file = tmp_path / "REMINDER.md"
    reminder_file.write_text("content", encoding="utf-8")
    with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
        with caplog.at_level(logging.WARNING, logger="archon"):
            result = build_reminder_injection(tmp_path)
    assert result is None
    assert any("permission denied" in r.message or "reminder" in r.message.lower() for r in caplog.records)


# ── Fix 1: curly-brace safety ─────────────────────────────────────

def test_build_reminder_injection_handles_curly_braces_in_content(
    tmp_path: Path, patched_system_file: Path
) -> None:
    """REMINDER.md with curly braces must NOT raise KeyError (Fix 1)."""
    (tmp_path / "REMINDER.md").write_text('Use dict {key: value} syntax', encoding="utf-8")
    result = build_reminder_injection(tmp_path)
    assert result is not None
    assert "{key: value}" in result


def test_build_reminder_message_handles_curly_braces_in_content(
    config: ReminderConfig, workspace: Path, patched_system_file: Path
) -> None:
    """build_reminder_message() with curly braces in REMINDER.md must NOT raise (Fix 1 + 5)."""
    (workspace / "REMINDER.md").write_text('{"json": "example"}', encoding="utf-8")
    r = ContextReminder(config, workspace)
    result = r.build_reminder_message()
    assert '{"json": "example"}' in result
    assert "<system_reminder" in result


# ── Fix 2: OSError catch in build_reminder_message() ─────────────

def test_build_reminder_message_handles_permission_error(
    config: ReminderConfig, workspace: Path, patched_system_file: Path
) -> None:
    """build_reminder_message() must not propagate PermissionError — return empty XML (Fix 2)."""
    r = ContextReminder(config, workspace)
    with patch("archon.ai.reminder._read_file_safe", return_value=None):
        result = r.build_reminder_message()
    assert "<system_reminder" in result
    assert "</system_reminder>" in result


# ── Fix 6: size warning threshold ────────────────────────────────

def test_build_reminder_injection_warns_when_file_is_large(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, patched_system_file: Path
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


# ── Task 1.2: _merge_contents and _read_file_safe ─────────────────

def test_merge_both_present() -> None:
    assert _merge_contents("system content", "user content") == "system content\n\nuser content"


def test_merge_system_only() -> None:
    assert _merge_contents("system content", None) == "system content"


def test_merge_user_only() -> None:
    assert _merge_contents(None, "user content") == "user content"


def test_merge_both_none() -> None:
    assert _merge_contents(None, None) is None


def test_read_file_safe_missing(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    p = tmp_path / "nonexistent.md"
    with caplog.at_level(logging.WARNING, logger="archon"):
        result = _read_file_safe(p)
    assert result is None
    assert any("nonexistent" in r.message or str(p) in r.message for r in caplog.records)


def test_read_file_safe_whitespace(tmp_path: Path) -> None:
    p = tmp_path / "ws.md"
    p.write_text("   \n\t  ", encoding="utf-8")
    assert _read_file_safe(p) is None


def test_read_file_safe_valid(tmp_path: Path) -> None:
    p = tmp_path / "valid.md"
    content = "  Hello world  \n"
    p.write_text(content, encoding="utf-8")
    result = _read_file_safe(p)
    assert result == content  # raw unstripped content returned


# ── Task 1.1: system_reminder.md exists ──────────────────────────

def test_system_reminder_file_exists() -> None:
    """_SYSTEM_REMINDER_FILE must resolve to an existing, non-empty file."""
    path = Path(reminder_module.__file__).parent / "prompts" / "system_reminder.md"
    assert path.exists(), f"system_reminder.md not found at {path}"
    assert path.read_text(encoding="utf-8").strip(), "system_reminder.md is empty"


# ── Task 1.3: Updated injection with system+user merge ────────────

def test_build_reminder_injection_merged(tmp_path: Path, patched_system_file: Path) -> None:
    """Both files present → XML contains both sections; system content precedes user content."""
    patched_system_file.write_text("## System Rules\nsystem content", encoding="utf-8")
    (tmp_path / "REMINDER.md").write_text("## User Rules\nuser content", encoding="utf-8")
    result = build_reminder_injection(tmp_path)
    assert result is not None
    assert "<system_reminder" in result
    assert "system content" in result
    assert "user content" in result
    assert result.index("system content") < result.index("user content")


def test_build_reminder_injection_user_absent(tmp_path: Path, patched_system_file: Path) -> None:
    """Only system file present → XML contains system section only."""
    patched_system_file.write_text("## System Rules\nsystem content", encoding="utf-8")
    result = build_reminder_injection(tmp_path)
    assert result is not None
    assert "system content" in result


def test_build_reminder_injection_system_absent(
    tmp_path: Path, patched_system_file: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Only user file present → XML contains user section only."""
    (tmp_path / "REMINDER.md").write_text("user content", encoding="utf-8")
    result = build_reminder_injection(tmp_path)
    assert result is not None
    assert "user content" in result


def test_build_reminder_injection_both_absent(tmp_path: Path, patched_system_file: Path) -> None:
    """Neither file present → returns None."""
    result = build_reminder_injection(tmp_path)
    assert result is None


def test_context_reminder_build_message_merged(
    config: ReminderConfig, tmp_path: Path, patched_system_file: Path
) -> None:
    """build_reminder_message() returns merged XML when both files present."""
    patched_system_file.write_text("## System Rules\nsystem content", encoding="utf-8")
    (tmp_path / "REMINDER.md").write_text("user content", encoding="utf-8")
    r = ContextReminder(config, tmp_path)
    msg = r.build_reminder_message()
    assert "system content" in msg
    assert "user content" in msg
    assert msg.index("system content") < msg.index("user content")


def test_context_reminder_build_message_system_absent(
    config: ReminderConfig, tmp_path: Path, patched_system_file: Path,
    caplog: pytest.LogCaptureFixture
) -> None:
    """System file missing → user-only XML."""
    (tmp_path / "REMINDER.md").write_text("user content", encoding="utf-8")
    r = ContextReminder(config, tmp_path)
    msg = r.build_reminder_message()
    assert "user content" in msg
    assert "<system_reminder" in msg


def test_should_inject_system_only_no_user_file(
    config: ReminderConfig, tmp_path: Path, patched_system_file: Path
) -> None:
    """User file absent, system file present, thresholds exceeded → should_inject() returns True."""
    patched_system_file.write_text("system content", encoding="utf-8")
    r = ContextReminder(config, tmp_path)
    for _ in range(config.interval_messages):
        r.record_message()
    assert r.should_inject() is True


def test_context_reminder_counters_reset_on_none_merge(
    config: ReminderConfig, tmp_path: Path, patched_system_file: Path
) -> None:
    """Both files absent → counters are still reset after build_reminder_message()."""
    r = ContextReminder(config, tmp_path)
    r.record_message()
    r.record_tokens(50)
    r.build_reminder_message()
    assert r.message_count == 0


def test_build_reminder_message_handles_curly_braces_in_system_content(
    config: ReminderConfig, tmp_path: Path, patched_system_file: Path
) -> None:
    """System file with curly braces → no format-string error, content preserved."""
    patched_system_file.write_text("Use dict {key: value} syntax", encoding="utf-8")
    r = ContextReminder(config, tmp_path)
    result = r.build_reminder_message()
    assert "{key: value}" in result
    assert "<system_reminder" in result


# ── Control Plane safety rules (Task 1.4 / Task 2.1) ─────────────

_WORKSPACE_REMINDER = Path(__file__).resolve().parents[2] / "workspace" / "REMINDER.md"
_SYSTEM_REMINDER_PATH = Path(__file__).resolve().parents[2] / "archon" / "ai" / "prompts" / "system_reminder.md"


def test_workspace_reminder_no_control_plane() -> None:
    """workspace/REMINDER.md must NOT contain the Archon Control Plane section."""
    content = _WORKSPACE_REMINDER.read_text()
    assert "## Archon Control Plane" not in content


def test_reminder_contains_control_plane_section() -> None:
    """system_reminder.md must contain the Archon Control Plane section."""
    content = _SYSTEM_REMINDER_PATH.read_text()
    assert "## Archon Control Plane" in content


def test_reminder_lists_mcp_tools() -> None:
    """Control Plane section must list key MCP tools."""
    content = _SYSTEM_REMINDER_PATH.read_text()
    assert "archon_restart" in content
    assert "archon_status" in content


def test_reminder_forbids_shell_commands() -> None:
    """Control Plane section must explicitly forbid dangerous shell commands."""
    content = _SYSTEM_REMINDER_PATH.read_text()
    for cmd in ("launchctl", "systemctl", "kill", "pkill", "killall"):
        assert cmd in content, f"system_reminder.md should mention '{cmd}' as forbidden"


def test_reminder_lists_all_tools() -> None:
    """system_reminder.md must list all 24 MCP tools grouped by category."""
    content = _SYSTEM_REMINDER_PATH.read_text()
    expected_tools = [
        # Service
        "archon_status",
        "archon_restart",
        # Agents
        "list_running_agents",
        "get_agent_status",
        "cancel_agent",
        "read_agent_log",
        "get_agent_by_name",
        # Sessions
        "get_session_status",
        "get_context_stats",
        # Comms
        "send_notification",
        "set_notification_mode",
        # Model
        "get_model",
        "set_model",
        # Model & Config
        "list_skills",
        "list_scheduled_tasks",
        # Schedule
        "add_scheduled_task",
        "update_scheduled_task",
        "remove_scheduled_task",
        # Config & Job Access (Phase 7)
        "get_config",
        "set_config",
        "get_job_config",
        # Files
        "list_attachments",
        "send_file",
        # RAG
        "rag_status",
    ]
    for tool in expected_tools:
        assert tool in content, f"system_reminder.md missing tool: {tool}"

    # Reverse check: expected list must match actual toolkit registrations
    from archon.ai.archon_toolkit import ArchonToolkit
    toolkit = ArchonToolkit()
    actual_tool_names = toolkit.tool_names
    assert set(expected_tools) == actual_tool_names, (
        f"expected_tools list does not match ArchonToolkit registrations.\n"
        f"Missing from expected: {actual_tool_names - set(expected_tools)}\n"
        f"Extra in expected: {set(expected_tools) - actual_tool_names}"
    )
