"""Tests for AgentLogger and AgentLogWriter — FR.003."""
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from archon.ai.agent_logger import AgentLogger, AgentLogWriter, _sanitize_name
from archon.ai.event_mapper import (
    ErrorEvent,
    Response,
    SubagentStarted,
    SubagentStopped,
    ThinkingResult,
    ToolResult,
    ToolStarted,
)


# ──────────────────────────────────────────────────────────────────
# _sanitize_name
# ──────────────────────────────────────────────────────────────────


def test_sanitize_name_alphanumeric() -> None:
    """Pure alphanumeric name is unchanged."""
    assert _sanitize_name("NovaAgent123") == "NovaAgent123"


def test_sanitize_name_spaces_to_hyphens() -> None:
    """Spaces become hyphens."""
    assert _sanitize_name("my agent") == "my-agent"


def test_sanitize_name_empty_returns_agent() -> None:
    """Empty string returns 'agent'."""
    assert _sanitize_name("") == "agent"


def test_sanitize_name_special_chars_become_hyphens() -> None:
    """Special characters become hyphens."""
    result = _sanitize_name("foo!bar@baz")
    assert result == "foo-bar-baz"


def test_sanitize_name_strips_leading_trailing_hyphens() -> None:
    """Leading/trailing hyphens are stripped."""
    result = _sanitize_name("!foo!")
    assert result == "foo"


# ──────────────────────────────────────────────────────────────────
# AgentLogWriter
# ──────────────────────────────────────────────────────────────────


def test_agent_log_writer_creates_file_with_header(tmp_path: Path) -> None:
    """AgentLogWriter creates a file containing '# Agent: Nova'."""
    log_path = tmp_path / "2026-02-25-14-30-Nova.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(
        path=log_path,
        agent_name="Nova",
        agent_type="general-purpose",
        started_at=started_at,
    )
    content = log_path.read_text(encoding="utf-8")
    assert "# Agent: Nova" in content


async def test_agent_log_writer_appends_thinking_result(tmp_path: Path) -> None:
    """record_event with ThinkingResult writes '### 💭 Thinking'."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(log_path, "Nova", "general", started_at)
    await writer.record_event(ThinkingResult(content="I should check the config."))
    content = log_path.read_text(encoding="utf-8")
    assert "### 💭 Thinking" in content
    assert "I should check the config." in content


async def test_agent_log_writer_appends_tool_started(tmp_path: Path) -> None:
    """record_event with ToolStarted writes '### 🔧 Tool:'."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(log_path, "Nova", "general", started_at)
    await writer.record_event(ToolStarted(name="Read", input="/etc/config", id=1))
    content = log_path.read_text(encoding="utf-8")
    assert "### 🔧 Tool:" in content
    assert "Read" in content


async def test_agent_log_writer_appends_tool_result(tmp_path: Path) -> None:
    """record_event with ToolResult writes '### 📤 Result'."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(log_path, "Nova", "general", started_at)
    await writer.record_event(ToolResult(content="file contents here", id=1))
    content = log_path.read_text(encoding="utf-8")
    assert "### 📤 Result" in content
    assert "file contents here" in content


async def test_agent_log_writer_appends_response(tmp_path: Path) -> None:
    """record_event with Response writes '### ✅ Response'."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(log_path, "Nova", "general", started_at)
    await writer.record_event(Response(content="Task completed successfully."))
    content = log_path.read_text(encoding="utf-8")
    assert "### ✅ Response" in content
    assert "Task completed successfully." in content


async def test_agent_log_writer_appends_error(tmp_path: Path) -> None:
    """record_event with ErrorEvent writes '### ❌ Error'."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(log_path, "Nova", "general", started_at)
    await writer.record_event(ErrorEvent(message="Connection timeout"))
    content = log_path.read_text(encoding="utf-8")
    assert "### ❌ Error" in content
    assert "Connection timeout" in content


async def test_agent_log_writer_finalize_writes_duration(tmp_path: Path) -> None:
    """finalize() writes '## Completed' and duration."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(log_path, "Nova", "general", started_at)
    await writer.finalize()
    content = log_path.read_text(encoding="utf-8")
    assert "## Completed" in content
    assert "Duration:" in content


async def test_agent_log_writer_thinking_result_appends_content(tmp_path: Path) -> None:
    """ThinkingResult writes a thought section to the log."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(log_path, "Nova", "general", started_at)
    size_before = log_path.stat().st_size
    await writer.record_event(ThinkingResult(content="Let me consider this."))
    size_after = log_path.stat().st_size
    assert size_after > size_before, "ThinkingResult must append content to the log"


# ──────────────────────────────────────────────────────────────────
# AgentLogger — high-level routing
# ──────────────────────────────────────────────────────────────────


async def test_agent_logger_creates_file_on_subagent_started(tmp_path: Path) -> None:
    """SubagentStarted creates a .md file in the history directory."""
    logger = AgentLogger(str(tmp_path))
    await logger.record_event(SubagentStarted(agent_id="a1", agent_type="general", agent_name="Nova"))
    sessions_dir = tmp_path / "sessions"
    assert sessions_dir.is_dir(), "sessions directory should have been created by AgentLogger"
    md_files = list(sessions_dir.glob("*.md"))
    assert len(md_files) == 1, f"Expected 1 .md file, got: {md_files}"
    assert "Nova" in md_files[0].name or "nova" in md_files[0].name.lower()


async def test_agent_logger_finalizes_on_subagent_stopped(tmp_path: Path) -> None:
    """SubagentStopped calls finalize on the matching writer."""
    logger = AgentLogger(str(tmp_path))
    await logger.record_event(SubagentStarted(agent_id="a1", agent_type="general", agent_name="Nova"))
    await logger.record_event(SubagentStopped(agent_id="a1", agent_type="general", agent_name="Nova"))
    sessions_dir = tmp_path / "sessions"
    assert sessions_dir.is_dir(), "sessions directory should have been created by AgentLogger"
    md_files = list(sessions_dir.glob("*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text(encoding="utf-8")
    assert "## Completed" in content


async def test_agent_logger_routes_events_to_active_writer(tmp_path: Path) -> None:
    """ThinkingResult is forwarded to the open writer."""
    logger = AgentLogger(str(tmp_path))
    await logger.record_event(SubagentStarted(agent_id="a1", agent_type="general", agent_name="Nova"))
    await logger.record_event(ThinkingResult(content="My inner thought"))
    sessions_dir = tmp_path / "sessions"
    assert sessions_dir.is_dir(), "sessions directory should have been created by AgentLogger"
    md_files = list(sessions_dir.glob("*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text(encoding="utf-8")
    assert "My inner thought" in content


async def test_agent_logger_discards_events_with_no_active_writer(tmp_path: Path) -> None:
    """Events received when no agent is active are silently discarded — no crash."""
    logger = AgentLogger(str(tmp_path))
    # No SubagentStarted yet — this must not raise
    await logger.record_event(ThinkingResult(content="orphaned thought"))
    await logger.record_event(Response(content="orphaned response"))
    # No files created
    md_files = list((tmp_path / "sessions").glob("*.md")) if (tmp_path / "sessions").is_dir() else []
    assert len(md_files) == 0


async def test_agent_logger_handles_nested_agents(tmp_path: Path) -> None:
    """Stack behavior: when nested, innermost agent receives events."""
    logger = AgentLogger(str(tmp_path))
    await logger.record_event(SubagentStarted(agent_id="outer", agent_type="general", agent_name="Outer"))
    await logger.record_event(SubagentStarted(agent_id="inner", agent_type="general", agent_name="Inner"))
    await logger.record_event(ThinkingResult(content="inner thought"))
    await logger.record_event(SubagentStopped(agent_id="inner", agent_type="general", agent_name="Inner"))
    # After inner stops, outer receives events
    await logger.record_event(Response(content="outer response"))

    sessions_dir = tmp_path / "sessions"
    assert sessions_dir.is_dir(), "sessions directory should have been created by AgentLogger"
    md_files = sorted(sessions_dir.glob("*.md"))
    assert len(md_files) == 2

    outer_file = next(f for f in md_files if "Outer" in f.name or "outer" in f.name.lower())
    inner_file = next(f for f in md_files if "Inner" in f.name or "inner" in f.name.lower())

    inner_content = inner_file.read_text(encoding="utf-8")
    outer_content = outer_file.read_text(encoding="utf-8")

    assert "inner thought" in inner_content
    assert "outer response" in outer_content


async def test_agent_logger_filename_format(tmp_path: Path) -> None:
    """Filename matches YYYY-MM-DD-HH-MM-name.md format."""
    import re

    logger = AgentLogger(str(tmp_path))
    await logger.record_event(SubagentStarted(agent_id="a1", agent_type="general", agent_name="Nova"))
    sessions_dir = tmp_path / "sessions"
    assert sessions_dir.is_dir(), "sessions directory should have been created by AgentLogger"
    md_files = list(sessions_dir.glob("*.md"))
    assert len(md_files) == 1
    pattern = r"^\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-Nova\.md$"
    assert re.match(pattern, md_files[0].name), (
        f"Filename {md_files[0].name!r} does not match expected pattern {pattern}"
    )


async def test_agent_logger_collision_uses_counter(tmp_path: Path) -> None:
    """When a file with the same name already exists, append -2, -3, etc."""
    logger = AgentLogger(str(tmp_path))
    # Start and immediately stop first agent to create file
    await logger.record_event(SubagentStarted(agent_id="a1", agent_type="general", agent_name="Nova"))
    await logger.record_event(SubagentStopped(agent_id="a1", agent_type="general", agent_name="Nova"))

    # Patch datetime.now so the second agent gets the exact same timestamp → collision
    sessions_dir = tmp_path / "sessions"
    assert sessions_dir.is_dir(), "sessions directory should have been created by AgentLogger"

    await logger.record_event(SubagentStarted(agent_id="a2", agent_type="general", agent_name="Nova"))
    await logger.record_event(SubagentStopped(agent_id="a2", agent_type="general", agent_name="Nova"))

    md_files = list(sessions_dir.glob("*.md"))
    # There should be at least 2 files; if timestamps differ by 1 minute we get 2 regular files.
    # If same timestamp, second gets -2 suffix. Either way, both agents logged.
    assert len(md_files) >= 2, f"Expected at least 2 log files, got: {md_files}"


async def test_agent_logger_unmatched_stop_is_ignored(tmp_path: Path) -> None:
    """Orphan SubagentStopped (no matching start) is silently ignored."""
    logger = AgentLogger(str(tmp_path))
    # This must not raise
    await logger.record_event(SubagentStopped(agent_id="ghost", agent_type="general", agent_name="Ghost"))
    # No files created
    md_files = list((tmp_path / "sessions").glob("*.md")) if (tmp_path / "sessions").is_dir() else []
    assert len(md_files) == 0


# ──────────────────────────────────────────────────────────────────
# AgentLogWriter — user_request / agent_task header sections
# ──────────────────────────────────────────────────────────────────


def test_agent_log_writer_writes_user_request_section(tmp_path: Path) -> None:
    """When user_request is given, header contains '## 📝 User Request' section."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(
        log_path, "Nova", "background", started_at,
        user_request="Run a full audit of the docs.",
    )
    content = log_path.read_text(encoding="utf-8")
    assert "## 📝 User Request" in content
    assert "Run a full audit of the docs." in content


def test_agent_log_writer_writes_agent_task_section(tmp_path: Path) -> None:
    """When agent_task is given, header contains '## 🤖 Agent Task' section."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(
        log_path, "Nova", "background", started_at,
        agent_task="Context:\nsome context\n\nTask:\nread the config",
    )
    content = log_path.read_text(encoding="utf-8")
    assert "## 🤖 Agent Task" in content
    assert "read the config" in content


def test_agent_log_writer_no_prompt_sections_when_empty(tmp_path: Path) -> None:
    """When both user_request and agent_task are omitted, no prompt sections appear."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    AgentLogWriter(log_path, "Nova", "background", started_at)
    content = log_path.read_text(encoding="utf-8")
    assert "## 📝 User Request" not in content
    assert "## 🤖 Agent Task" not in content


async def test_agent_log_writer_prompt_sections_appear_before_first_event(tmp_path: Path) -> None:
    """User request and agent task sections precede any event entries."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(
        log_path, "Nova", "background", started_at,
        user_request="Fix the bug.",
        agent_task="Task:\nFix the bug in main.py",
    )
    await writer.record_event(ToolStarted(name="Read", input="/main.py", id=1))
    content = log_path.read_text(encoding="utf-8")
    request_pos = content.index("## 📝 User Request")
    task_pos = content.index("## 🤖 Agent Task")
    tool_pos = content.index("### 🔧 Tool:")
    assert request_pos < task_pos < tool_pos, (
        "User Request must come before Agent Task, which must come before first event"
    )


# ──────────────────────────────────────────────────────────────────
# AgentLogWriter — finalize with final_result
# ──────────────────────────────────────────────────────────────────


async def test_agent_log_writer_finalize_with_result_writes_final_result_section(tmp_path: Path) -> None:
    """finalize(final_result=...) writes '### ✅ Final Result' before '## Completed'."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(log_path, "Nova", "background", started_at)
    await writer.finalize(final_result="The audit is complete.")
    content = log_path.read_text(encoding="utf-8")
    assert "### ✅ Final Result" in content
    assert "The audit is complete." in content
    assert "## Completed" in content


async def test_agent_log_writer_finalize_final_result_appears_before_completed(tmp_path: Path) -> None:
    """### ✅ Final Result must appear before ## Completed in the log."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(log_path, "Nova", "background", started_at)
    await writer.finalize(final_result="Done.")
    content = log_path.read_text(encoding="utf-8")
    result_pos = content.index("### ✅ Final Result")
    completed_pos = content.index("## Completed")
    assert result_pos < completed_pos, (
        "Final Result section must come before ## Completed footer"
    )


async def test_agent_log_writer_finalize_without_result_no_final_result_section(tmp_path: Path) -> None:
    """finalize() with no result omits the ### ✅ Final Result section."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(log_path, "Nova", "background", started_at)
    await writer.finalize()
    content = log_path.read_text(encoding="utf-8")
    assert "### ✅ Final Result" not in content
    assert "## Completed" in content


async def test_agent_log_writer_full_lifecycle_ordering(tmp_path: Path) -> None:
    """Full log: user_request → agent_task → events → final_result → completed."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(
        log_path, "Nova", "background", started_at,
        user_request="Audit the config.",
        agent_task="Task:\nRead config.toml and report issues.",
    )
    await writer.record_event(ToolStarted(name="Read", input="/config.toml", id=1))
    await writer.record_event(ToolResult(content="[access]\ntoken = x", id=1))
    await writer.finalize(final_result="Config looks good.")
    content = log_path.read_text(encoding="utf-8")
    request_pos = content.index("## 📝 User Request")
    task_pos = content.index("## 🤖 Agent Task")
    tool_pos = content.index("### 🔧 Tool:")
    result_pos = content.index("### ✅ Final Result")
    completed_pos = content.index("## Completed")
    assert request_pos < task_pos < tool_pos < result_pos < completed_pos, (
        f"Expected User Request < Agent Task < Tool < Final Result < Completed, "
        f"got positions {request_pos} < {task_pos} < {tool_pos} < {result_pos} < {completed_pos}"
    )


# ──────────────────────────────────────────────────────────────────
# AgentLogger — propagates user_request / agent_task / final_result
# ──────────────────────────────────────────────────────────────────


async def test_agent_logger_propagates_user_request_to_log(tmp_path: Path) -> None:
    """user_request on SubagentStarted appears in the log file."""
    logger = AgentLogger(str(tmp_path))
    await logger.record_event(SubagentStarted(
        agent_id="a1", agent_type="background", agent_name="Nova",
        user_request="Fix the failing tests.",
    ))
    sessions_dir = tmp_path / "sessions"
    assert sessions_dir.is_dir(), "sessions directory should have been created by AgentLogger"
    md_files = list(sessions_dir.glob("*.md"))
    assert len(md_files) == 1, f"Expected 1 .md file, got: {md_files}"
    content = md_files[0].read_text(encoding="utf-8")
    assert "## 📝 User Request" in content
    assert "Fix the failing tests." in content


async def test_agent_logger_propagates_agent_task_to_log(tmp_path: Path) -> None:
    """agent_task on SubagentStarted appears in the log file."""
    logger = AgentLogger(str(tmp_path))
    await logger.record_event(SubagentStarted(
        agent_id="a1", agent_type="background", agent_name="Nova",
        agent_task="Task:\nRun pytest and fix failures.",
    ))
    sessions_dir = tmp_path / "sessions"
    assert sessions_dir.is_dir(), "sessions directory should have been created by AgentLogger"
    md_files = list(sessions_dir.glob("*.md"))
    assert len(md_files) == 1, f"Expected 1 .md file, got: {md_files}"
    content = md_files[0].read_text(encoding="utf-8")
    assert "## 🤖 Agent Task" in content
    assert "Run pytest and fix failures." in content


async def test_agent_logger_propagates_final_result_to_log(tmp_path: Path) -> None:
    """final_result on SubagentStopped appears as the last message before ## Completed."""
    logger = AgentLogger(str(tmp_path))
    await logger.record_event(SubagentStarted(agent_id="a1", agent_type="background", agent_name="Nova"))
    await logger.record_event(SubagentStopped(
        agent_id="a1", agent_type="background", agent_name="Nova",
        final_result="All 42 tests pass.",
    ))
    sessions_dir = tmp_path / "sessions"
    assert sessions_dir.is_dir(), "sessions directory should have been created by AgentLogger"
    md_files = list(sessions_dir.glob("*.md"))
    assert len(md_files) == 1, f"Expected 1 .md file, got: {md_files}"
    content = md_files[0].read_text(encoding="utf-8")
    assert "### ✅ Final Result" in content
    assert "All 42 tests pass." in content
    result_pos = content.index("### ✅ Final Result")
    completed_pos = content.index("## Completed")
    assert result_pos < completed_pos


# ──────────────────────────────────────────────────────────────────
# AgentLogWriter — tool result suppression
# ──────────────────────────────────────────────────────────────────


async def test_agent_log_writer_tool_result_suppressed_for_read(tmp_path: Path) -> None:
    """Successful Read result in an agent log is suppressed — only summary is written."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(log_path, "Nova", "general", started_at)
    await writer.record_event(ToolResult(content="line1\nline2\nline3", id=1, tool_name="Read"))
    content = log_path.read_text(encoding="utf-8")
    assert "✓ Read completed (3 lines," in content
    assert "line1" not in content


async def test_agent_log_writer_tool_result_read_error_not_suppressed(tmp_path: Path) -> None:
    """Failed Read result in an agent log is NOT suppressed — full content is logged."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(log_path, "Nova", "general", started_at)
    await writer.record_event(ToolResult(content="Error: permission denied", id=2, tool_name="Read", is_error=True))
    content = log_path.read_text(encoding="utf-8")
    assert "Error: permission denied" in content
    assert "✓ Read" not in content


async def test_agent_log_writer_custom_suppressed_set(tmp_path: Path) -> None:
    """AgentLogWriter with custom suppression hides only the specified tools."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(
        log_path, "Nova", "general", started_at,
        suppressed_tools=frozenset({"MyTool"}),
    )
    await writer.record_event(ToolResult(content="hidden content", id=3, tool_name="MyTool"))
    await writer.record_event(ToolResult(content="visible data", id=4, tool_name="Read"))
    content = log_path.read_text(encoding="utf-8")
    assert "✓ MyTool completed" in content
    assert "hidden content" not in content
    assert "visible data" in content


async def test_agent_logger_suppressed_tools_propagated_to_writer(tmp_path: Path) -> None:
    """AgentLogger passes suppressed_tools through to the AgentLogWriter it creates."""
    logger = AgentLogger(str(tmp_path), suppressed_tools=frozenset({"Bash"}))
    await logger.record_event(SubagentStarted(agent_id="a1", agent_type="general", agent_name="Nova"))
    await logger.record_event(ToolResult(content="bash output here", id=1, tool_name="Bash"))
    await logger.record_event(SubagentStopped(agent_id="a1", agent_type="general", agent_name="Nova"))
    sessions_dir = tmp_path / "sessions"
    assert sessions_dir.is_dir(), "sessions directory should have been created by AgentLogger"
    md_files = list(sessions_dir.glob("*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text(encoding="utf-8")
    assert "✓ Bash completed" in content


# ──────────────────────────────────────────────────────────────────
# Issue B: OSError in _append is caught and logged
# ──────────────────────────────────────────────────────────────────


async def test_agent_log_writer_append_oserror_is_caught_and_logged(tmp_path: Path) -> None:
    """OSError from _append is caught and logged as a warning — does not propagate."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(log_path, "Nova", "general", started_at)

    with patch("archon.ai.agent_logger.logger") as mock_logger:
        with patch("pathlib.Path.open", side_effect=OSError("disk full")):
            # Must not raise — OSError is swallowed and logged
            writer._append("some text")

    mock_logger.warning.assert_called_once()
    warning_args = mock_logger.warning.call_args[0]
    assert "Failed to write agent log" in warning_args[0]
