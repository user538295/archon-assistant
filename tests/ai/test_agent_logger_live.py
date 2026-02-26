"""Live integration tests for AgentLogger — FR.003.

These tests actually write files to a temporary directory and verify the
content is readable and correct.  Marked with @pytest.mark.live so they
are excluded from the fast unit test run.
"""
import pytest
from datetime import datetime, timezone
from pathlib import Path

from archon.ai.agent_logger import AgentLogger
from archon.ai.event_mapper import (
    ErrorEvent,
    Response,
    SubagentStarted,
    SubagentStopped,
    ThinkingResult,
    ToolResult,
    ToolStarted,
)


@pytest.mark.live
def test_live_agent_logger_creates_file_on_disk(tmp_path: Path) -> None:
    """AgentLogger actually creates a .md file in the given directory."""
    logger = AgentLogger(str(tmp_path))
    logger.record_event(SubagentStarted(agent_id="a1", agent_type="general", agent_name="Nova"))
    logger.record_event(Response(content="Task done."))
    logger.record_event(SubagentStopped(agent_id="a1", agent_type="general", agent_name="Nova"))

    md_files = list(tmp_path.glob("*.md"))
    assert len(md_files) == 1, f"Expected 1 .md file, found: {md_files}"
    assert md_files[0].stat().st_size > 0, "Log file must not be empty"


@pytest.mark.live
def test_live_agent_logger_content_is_readable_markdown(tmp_path: Path) -> None:
    """The created log file contains valid Markdown with expected headers."""
    logger = AgentLogger(str(tmp_path))
    logger.record_event(SubagentStarted(agent_id="a1", agent_type="tester", agent_name="Sage"))
    logger.record_event(ThinkingResult(content="Let me think about this."))
    logger.record_event(ToolStarted(name="Bash", input="ls -la", id=1))
    logger.record_event(ToolResult(content="total 8\n...", id=1))
    logger.record_event(Response(content="Done with the task."))
    logger.record_event(SubagentStopped(agent_id="a1", agent_type="tester", agent_name="Sage"))

    md_files = list(tmp_path.glob("*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text(encoding="utf-8")

    # Verify markdown structure
    assert "# Agent: Sage" in content
    assert "**Type:** tester" in content
    assert "**Started:**" in content
    assert "### 💭 Thinking" in content
    assert "Let me think about this." in content
    assert "### 🔧 Tool: Bash" in content
    assert "ls -la" in content
    assert "### 📤 Result" in content
    assert "total 8" in content
    assert "### ✅ Response" in content
    assert "Done with the task." in content
    assert "## Completed" in content
    assert "**Duration:**" in content


@pytest.mark.live
def test_live_agent_logger_continuous_write_survives_exception(tmp_path: Path) -> None:
    """Partial log is readable after a simulated crash mid-session.

    The file must contain all events recorded before the simulated exception.
    """
    logger = AgentLogger(str(tmp_path))
    logger.record_event(SubagentStarted(agent_id="a1", agent_type="general", agent_name="Atlas"))
    logger.record_event(ThinkingResult(content="Initial thought"))
    logger.record_event(ToolStarted(name="Read", input="/config.toml", id=1))

    # Simulate crash — do NOT call SubagentStopped (process died mid-session)
    # The file should still be readable with partial content
    md_files = list(tmp_path.glob("*.md"))
    assert len(md_files) == 1, "File must exist even without finalization"

    content = md_files[0].read_text(encoding="utf-8")
    assert "# Agent: Atlas" in content, "Header must be present"
    assert "Initial thought" in content, "Events written before crash must be readable"
    assert "### 🔧 Tool: Read" in content, "Tool event must be present"
    # The file is incomplete (no ## Completed) but still readable
    assert "## Completed" not in content, "No completion footer expected after simulated crash"
