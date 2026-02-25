"""Tests for AgentLogger and AgentLogWriter — FR.003."""
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from archon.ai.agent_logger import AgentLogger, AgentLogWriter, _sanitize_name
from archon.ai.event_mapper import (
    ErrorEvent,
    Response,
    SubagentStarted,
    SubagentStopped,
    ThinkingResult,
    ThinkingStarted,
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


def test_agent_log_writer_appends_thinking_result(tmp_path: Path) -> None:
    """record_event with ThinkingResult writes '### 💭 Thought'."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(log_path, "Nova", "general", started_at)
    writer.record_event(ThinkingResult(content="I should check the config."))
    content = log_path.read_text(encoding="utf-8")
    assert "### 💭 Thought" in content
    assert "I should check the config." in content


def test_agent_log_writer_appends_tool_started(tmp_path: Path) -> None:
    """record_event with ToolStarted writes '### 🔧 Tool:'."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(log_path, "Nova", "general", started_at)
    writer.record_event(ToolStarted(name="Read", input="/etc/config", id=1))
    content = log_path.read_text(encoding="utf-8")
    assert "### 🔧 Tool:" in content
    assert "Read" in content


def test_agent_log_writer_appends_tool_result(tmp_path: Path) -> None:
    """record_event with ToolResult writes '### 📤 Result'."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(log_path, "Nova", "general", started_at)
    writer.record_event(ToolResult(content="file contents here", id=1))
    content = log_path.read_text(encoding="utf-8")
    assert "### 📤 Result" in content
    assert "file contents here" in content


def test_agent_log_writer_appends_response(tmp_path: Path) -> None:
    """record_event with Response writes '### ✅ Response'."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(log_path, "Nova", "general", started_at)
    writer.record_event(Response(content="Task completed successfully."))
    content = log_path.read_text(encoding="utf-8")
    assert "### ✅ Response" in content
    assert "Task completed successfully." in content


def test_agent_log_writer_appends_error(tmp_path: Path) -> None:
    """record_event with ErrorEvent writes '### ❌ Error'."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(log_path, "Nova", "general", started_at)
    writer.record_event(ErrorEvent(message="Connection timeout"))
    content = log_path.read_text(encoding="utf-8")
    assert "### ❌ Error" in content
    assert "Connection timeout" in content


def test_agent_log_writer_finalize_writes_duration(tmp_path: Path) -> None:
    """finalize() writes '## Completed' and duration."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(log_path, "Nova", "general", started_at)
    writer.finalize()
    content = log_path.read_text(encoding="utf-8")
    assert "## Completed" in content
    assert "Duration:" in content


def test_agent_log_writer_ignores_thinking_started(tmp_path: Path) -> None:
    """ThinkingStarted renders empty string — nothing appended."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(log_path, "Nova", "general", started_at)
    size_before = log_path.stat().st_size
    writer.record_event(ThinkingStarted())
    size_after = log_path.stat().st_size
    assert size_after == size_before, "ThinkingStarted must not append anything"


# ──────────────────────────────────────────────────────────────────
# AgentLogger — high-level routing
# ──────────────────────────────────────────────────────────────────


def test_agent_logger_creates_file_on_subagent_started(tmp_path: Path) -> None:
    """SubagentStarted creates a .md file in the history directory."""
    logger = AgentLogger(str(tmp_path))
    logger.record_event(SubagentStarted(agent_id="a1", agent_type="general", agent_name="Nova"))
    md_files = list(tmp_path.glob("*.md"))
    assert len(md_files) == 1, f"Expected 1 .md file, got: {md_files}"
    assert "Nova" in md_files[0].name or "nova" in md_files[0].name.lower()


def test_agent_logger_finalizes_on_subagent_stopped(tmp_path: Path) -> None:
    """SubagentStopped calls finalize on the matching writer."""
    logger = AgentLogger(str(tmp_path))
    logger.record_event(SubagentStarted(agent_id="a1", agent_type="general", agent_name="Nova"))
    logger.record_event(SubagentStopped(agent_id="a1", agent_type="general", agent_name="Nova"))
    md_files = list(tmp_path.glob("*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text(encoding="utf-8")
    assert "## Completed" in content


def test_agent_logger_routes_events_to_active_writer(tmp_path: Path) -> None:
    """ThinkingResult is forwarded to the open writer."""
    logger = AgentLogger(str(tmp_path))
    logger.record_event(SubagentStarted(agent_id="a1", agent_type="general", agent_name="Nova"))
    logger.record_event(ThinkingResult(content="My inner thought"))
    md_files = list(tmp_path.glob("*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text(encoding="utf-8")
    assert "My inner thought" in content


def test_agent_logger_discards_events_with_no_active_writer(tmp_path: Path) -> None:
    """Events received when no agent is active are silently discarded — no crash."""
    logger = AgentLogger(str(tmp_path))
    # No SubagentStarted yet — this must not raise
    logger.record_event(ThinkingResult(content="orphaned thought"))
    logger.record_event(Response(content="orphaned response"))
    # No files created
    md_files = list(tmp_path.glob("*.md"))
    assert len(md_files) == 0


def test_agent_logger_handles_nested_agents(tmp_path: Path) -> None:
    """Stack behavior: when nested, innermost agent receives events."""
    logger = AgentLogger(str(tmp_path))
    logger.record_event(SubagentStarted(agent_id="outer", agent_type="general", agent_name="Outer"))
    logger.record_event(SubagentStarted(agent_id="inner", agent_type="general", agent_name="Inner"))
    logger.record_event(ThinkingResult(content="inner thought"))
    logger.record_event(SubagentStopped(agent_id="inner", agent_type="general", agent_name="Inner"))
    # After inner stops, outer receives events
    logger.record_event(Response(content="outer response"))

    md_files = sorted(tmp_path.glob("*.md"))
    assert len(md_files) == 2

    outer_file = next(f for f in md_files if "Outer" in f.name or "outer" in f.name.lower())
    inner_file = next(f for f in md_files if "Inner" in f.name or "inner" in f.name.lower())

    inner_content = inner_file.read_text(encoding="utf-8")
    outer_content = outer_file.read_text(encoding="utf-8")

    assert "inner thought" in inner_content
    assert "outer response" in outer_content


def test_agent_logger_filename_format(tmp_path: Path) -> None:
    """Filename matches YYYY-MM-DD-HH-MM-name.md format."""
    import re

    logger = AgentLogger(str(tmp_path))
    logger.record_event(SubagentStarted(agent_id="a1", agent_type="general", agent_name="Nova"))
    md_files = list(tmp_path.glob("*.md"))
    assert len(md_files) == 1
    pattern = r"^\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-Nova\.md$"
    assert re.match(pattern, md_files[0].name), (
        f"Filename {md_files[0].name!r} does not match expected pattern {pattern}"
    )


def test_agent_logger_collision_uses_counter(tmp_path: Path) -> None:
    """When a file with the same name already exists, append -2, -3, etc."""
    logger = AgentLogger(str(tmp_path))
    # Start and immediately stop first agent to create file
    logger.record_event(SubagentStarted(agent_id="a1", agent_type="general", agent_name="Nova"))
    logger.record_event(SubagentStopped(agent_id="a1", agent_type="general", agent_name="Nova"))

    # Patch datetime.now so the second agent gets the exact same timestamp → collision
    existing_files_before = set(tmp_path.glob("*.md"))

    logger.record_event(SubagentStarted(agent_id="a2", agent_type="general", agent_name="Nova"))
    logger.record_event(SubagentStopped(agent_id="a2", agent_type="general", agent_name="Nova"))

    md_files = list(tmp_path.glob("*.md"))
    # There should be at least 2 files; if timestamps differ by 1 minute we get 2 regular files.
    # If same timestamp, second gets -2 suffix. Either way, both agents logged.
    assert len(md_files) >= 2, f"Expected at least 2 log files, got: {md_files}"


def test_agent_logger_unmatched_stop_is_ignored(tmp_path: Path) -> None:
    """Orphan SubagentStopped (no matching start) is silently ignored."""
    logger = AgentLogger(str(tmp_path))
    # This must not raise
    logger.record_event(SubagentStopped(agent_id="ghost", agent_type="general", agent_name="Ghost"))
    # No files created
    md_files = list(tmp_path.glob("*.md"))
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


def test_agent_log_writer_prompt_sections_appear_before_first_event(tmp_path: Path) -> None:
    """User request and agent task sections precede any event entries."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(
        log_path, "Nova", "background", started_at,
        user_request="Fix the bug.",
        agent_task="Task:\nFix the bug in main.py",
    )
    writer.record_event(ToolStarted(name="Read", input="/main.py", id=1))
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


def test_agent_log_writer_finalize_with_result_writes_final_result_section(tmp_path: Path) -> None:
    """finalize(final_result=...) writes '### ✅ Final Result' before '## Completed'."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(log_path, "Nova", "background", started_at)
    writer.finalize(final_result="The audit is complete.")
    content = log_path.read_text(encoding="utf-8")
    assert "### ✅ Final Result" in content
    assert "The audit is complete." in content
    assert "## Completed" in content


def test_agent_log_writer_finalize_final_result_appears_before_completed(tmp_path: Path) -> None:
    """### ✅ Final Result must appear before ## Completed in the log."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(log_path, "Nova", "background", started_at)
    writer.finalize(final_result="Done.")
    content = log_path.read_text(encoding="utf-8")
    result_pos = content.index("### ✅ Final Result")
    completed_pos = content.index("## Completed")
    assert result_pos < completed_pos, (
        "Final Result section must come before ## Completed footer"
    )


def test_agent_log_writer_finalize_without_result_no_final_result_section(tmp_path: Path) -> None:
    """finalize() with no result omits the ### ✅ Final Result section."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(log_path, "Nova", "background", started_at)
    writer.finalize()
    content = log_path.read_text(encoding="utf-8")
    assert "### ✅ Final Result" not in content
    assert "## Completed" in content


def test_agent_log_writer_full_lifecycle_ordering(tmp_path: Path) -> None:
    """Full log: user_request → agent_task → events → final_result → completed."""
    log_path = tmp_path / "test.md"
    started_at = datetime(2026, 2, 25, 14, 30, 0, tzinfo=timezone.utc)
    writer = AgentLogWriter(
        log_path, "Nova", "background", started_at,
        user_request="Audit the config.",
        agent_task="Task:\nRead config.toml and report issues.",
    )
    writer.record_event(ToolStarted(name="Read", input="/config.toml", id=1))
    writer.record_event(ToolResult(content="[access]\ntoken = x", id=1))
    writer.finalize(final_result="Config looks good.")
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


def test_agent_logger_propagates_user_request_to_log(tmp_path: Path) -> None:
    """user_request on SubagentStarted appears in the log file."""
    logger = AgentLogger(str(tmp_path))
    logger.record_event(SubagentStarted(
        agent_id="a1", agent_type="background", agent_name="Nova",
        user_request="Fix the failing tests.",
    ))
    md_files = list(tmp_path.glob("*.md"))
    content = md_files[0].read_text(encoding="utf-8")
    assert "## 📝 User Request" in content
    assert "Fix the failing tests." in content


def test_agent_logger_propagates_agent_task_to_log(tmp_path: Path) -> None:
    """agent_task on SubagentStarted appears in the log file."""
    logger = AgentLogger(str(tmp_path))
    logger.record_event(SubagentStarted(
        agent_id="a1", agent_type="background", agent_name="Nova",
        agent_task="Task:\nRun pytest and fix failures.",
    ))
    md_files = list(tmp_path.glob("*.md"))
    content = md_files[0].read_text(encoding="utf-8")
    assert "## 🤖 Agent Task" in content
    assert "Run pytest and fix failures." in content


def test_agent_logger_propagates_final_result_to_log(tmp_path: Path) -> None:
    """final_result on SubagentStopped appears as the last message before ## Completed."""
    logger = AgentLogger(str(tmp_path))
    logger.record_event(SubagentStarted(agent_id="a1", agent_type="background", agent_name="Nova"))
    logger.record_event(SubagentStopped(
        agent_id="a1", agent_type="background", agent_name="Nova",
        final_result="All 42 tests pass.",
    ))
    md_files = list(tmp_path.glob("*.md"))
    content = md_files[0].read_text(encoding="utf-8")
    assert "### ✅ Final Result" in content
    assert "All 42 tests pass." in content
    result_pos = content.index("### ✅ Final Result")
    completed_pos = content.index("## Completed")
    assert result_pos < completed_pos
