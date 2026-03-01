"""Tests for EventRenderer — unit tests for Markdown rendering and suppression."""
import pytest

from archon.ai.agent_plan import AgentPlan, AgentTask
from archon.ai.event_mapper import (
    ClassificationEvent,
    ErrorEvent,
    PlanEvent,
    Response,
    RoutingEvent,
    SubagentStarted,
    SubagentStopped,
    ThinkingResult,
    ToolResult,
    ToolStarted,
    WaveCompleted,
    WaveStarted,
)
from archon.ai.event_renderer import EventRenderer, _format_size


# ──────────────────────────────────────────────────────────────────
# _format_size helper
# ──────────────────────────────────────────────────────────────────


def test_format_size_bytes() -> None:
    """Values below 1024 are formatted as bytes."""
    assert _format_size(0) == "0 B"
    assert _format_size(1) == "1 B"
    assert _format_size(1023) == "1023 B"


def test_format_size_kilobytes() -> None:
    """Values >= 1024 are formatted as KB with 1 decimal place."""
    assert _format_size(1024) == "1.0 KB"
    assert _format_size(2048) == "2.0 KB"
    assert _format_size(1536) == "1.5 KB"


# ──────────────────────────────────────────────────────────────────
# Non-suppressed tool result — full content rendered
# ──────────────────────────────────────────────────────────────────


def test_non_suppressed_tool_renders_full_content() -> None:
    """A ToolResult with a non-suppressed tool_name shows full content in a fenced block."""
    renderer = EventRenderer()
    event = ToolResult(content="output line 1\noutput line 2", tool_name="Bash", is_error=False)
    result = renderer.render(event)
    assert "```\noutput line 1\noutput line 2\n```" in result
    assert "✓ Bash" not in result


# ──────────────────────────────────────────────────────────────────
# Suppressed tool result, success — compact summary line
# ──────────────────────────────────────────────────────────────────


def test_suppressed_tool_success_renders_summary() -> None:
    """Successful Read result is suppressed — shows compact summary, not content."""
    renderer = EventRenderer()
    event = ToolResult(content="line1\nline2", tool_name="Read", is_error=False)
    result = renderer.render(event)
    assert "✓ Read completed (2 lines," in result
    assert "line1" not in result
    assert "line2" not in result


def test_suppressed_tool_success_no_fenced_block() -> None:
    """Suppressed successful result must NOT include a fenced code block."""
    renderer = EventRenderer()
    event = ToolResult(content="secret data", tool_name="Glob", is_error=False)
    result = renderer.render(event)
    assert "```" not in result


# ──────────────────────────────────────────────────────────────────
# Suppressed tool result, error — full content logged
# ──────────────────────────────────────────────────────────────────


def test_suppressed_tool_error_renders_full_content() -> None:
    """A failed Read result is NOT suppressed — full content is logged for debugging."""
    renderer = EventRenderer()
    event = ToolResult(content="Error: file not found", tool_name="Read", is_error=True)
    result = renderer.render(event)
    assert "Error: file not found" in result
    assert "```\nError: file not found\n```" in result
    assert "✓ Read" not in result


# ──────────────────────────────────────────────────────────────────
# Empty / unknown tool name — full content rendered
# ──────────────────────────────────────────────────────────────────


def test_unknown_tool_name_renders_full_content() -> None:
    """Empty tool_name is not in suppressed set — full content is rendered."""
    renderer = EventRenderer()
    event = ToolResult(content="some data", tool_name="", is_error=False)
    result = renderer.render(event)
    assert "some data" in result
    assert "```\nsome data\n```" in result
    assert "✓ " not in result


# ──────────────────────────────────────────────────────────────────
# All four default suppressed tools
# ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("tool_name", ["Read", "Glob", "Grep", "WebFetch"])
def test_all_default_suppressed_tools_show_summary(tool_name: str) -> None:
    """Each of the four default suppressed tools shows only the summary on success."""
    renderer = EventRenderer()
    event = ToolResult(content="line a\nline b\nline c", tool_name=tool_name, is_error=False)
    result = renderer.render(event)
    assert f"✓ {tool_name} completed (3 lines," in result
    assert "line a" not in result


# ──────────────────────────────────────────────────────────────────
# Custom suppression set
# ──────────────────────────────────────────────────────────────────


def test_custom_suppressed_set_suppresses_custom_tool() -> None:
    """EventRenderer with a custom set suppresses only the custom tool."""
    renderer = EventRenderer(suppressed_tools=frozenset({"MyTool"}))
    suppressed_event = ToolResult(content="hidden content", tool_name="MyTool", is_error=False)
    result = renderer.render(suppressed_event)
    assert "✓ MyTool completed" in result
    assert "hidden content" not in result


def test_custom_suppressed_set_does_not_suppress_read() -> None:
    """When suppressed_tools is a custom set that excludes Read, Read is rendered in full."""
    renderer = EventRenderer(suppressed_tools=frozenset({"MyTool"}))
    visible_event = ToolResult(content="visible data", tool_name="Read", is_error=False)
    result = renderer.render(visible_event)
    assert "visible data" in result
    assert "```\nvisible data\n```" in result
    assert "✓ Read" not in result


# ──────────────────────────────────────────────────────────────────
# Summary format — correct line count and size
# ──────────────────────────────────────────────────────────────────


def test_summary_line_count_correct() -> None:
    """Summary shows the correct number of lines."""
    renderer = EventRenderer()
    content = "line1\nline2\nline3"
    event = ToolResult(content=content, tool_name="Read", is_error=False)
    result = renderer.render(event)
    assert "3 lines" in result


def test_summary_size_correct() -> None:
    """Summary shows the correct byte size."""
    renderer = EventRenderer()
    content = "abc"  # 3 bytes
    event = ToolResult(content=content, tool_name="Grep", is_error=False)
    result = renderer.render(event)
    assert "3 B" in result


def test_summary_size_kb_when_large() -> None:
    """Summary shows KB for content >= 1024 bytes."""
    renderer = EventRenderer()
    content = "x" * 2048  # 2048 bytes → 2.0 KB
    event = ToolResult(content=content, tool_name="Read", is_error=False)
    result = renderer.render(event)
    assert "2.0 KB" in result


def test_summary_empty_content_zero_lines() -> None:
    """Empty content produces 0 lines in the summary."""
    renderer = EventRenderer()
    event = ToolResult(content="", tool_name="Glob", is_error=False)
    result = renderer.render(event)
    assert "0 lines" in result


# ──────────────────────────────────────────────────────────────────
# Smoke tests for other event types
# ──────────────────────────────────────────────────────────────────


def test_thinking_result_rendered() -> None:
    """ThinkingResult renders as a '💭 Thinking' section."""
    renderer = EventRenderer()
    event = ThinkingResult(content="I should check the config.")
    result = renderer.render(event)
    assert "### 💭 Thinking" in result
    assert "I should check the config." in result


def test_tool_started_rendered() -> None:
    """ToolStarted renders as a '🔧 Tool:' section with fenced input."""
    renderer = EventRenderer()
    event = ToolStarted(name="Bash", input="ls -la", id=1)
    result = renderer.render(event)
    assert "### 🔧 Tool: Bash [1]" in result
    assert "```\nls -la\n```" in result


def test_tool_started_no_id_omits_id_tag() -> None:
    """ToolStarted with id=0 must not show '[0]' in output."""
    renderer = EventRenderer()
    event = ToolStarted(name="Read", input="/tmp/file.txt")
    result = renderer.render(event)
    assert "[0]" not in result


def test_response_rendered_with_last_question() -> None:
    """Response renders a '✅ Response' section including the blockquote."""
    renderer = EventRenderer()
    event = Response(content="Here are the results.")
    result = renderer.render(event, last_question="What files are here?")
    assert "### ✅ Response" in result
    assert "Here are the results." in result
    assert '> User: "What files are here?"' in result


def test_response_rendered_without_last_question() -> None:
    """Response with no last_question omits the blockquote."""
    renderer = EventRenderer()
    event = Response(content="Here are the results.")
    result = renderer.render(event)
    assert "### ✅ Response" in result
    assert "> User:" not in result


def test_response_question_truncated_at_120_chars() -> None:
    """Questions longer than 120 chars are truncated with '...' in the blockquote."""
    renderer = EventRenderer()
    long_q = "x" * 150
    event = Response(content="done")
    result = renderer.render(event, last_question=long_q)
    assert '> User: "' + "x" * 120 + '..."' in result


def test_error_event_rendered() -> None:
    """ErrorEvent renders as a '❌ Error' section."""
    renderer = EventRenderer()
    event = ErrorEvent(message="SDK timeout")
    result = renderer.render(event)
    assert "### ❌ Error" in result
    assert "SDK timeout" in result


def test_unknown_event_type_returns_empty_string() -> None:
    """An unrecognised event type returns an empty string (no crash)."""

    class _UnknownEvent:
        source: str = "orchestrator"

    renderer = EventRenderer()
    result = renderer.render(_UnknownEvent())  # type: ignore[arg-type]
    assert result == ""


# ──────────────────────────────────────────────────────────────────
# ClassificationEvent rendering
# ──────────────────────────────────────────────────────────────────


def test_classification_event_renders_heading() -> None:
    """ClassificationEvent renders a '🏷 Classification' heading."""
    renderer = EventRenderer()
    event = ClassificationEvent(intent="task", confidence=0.92)
    result = renderer.render(event)
    assert "### 🏷 Classification" in result


def test_classification_event_renders_json() -> None:
    """ClassificationEvent renders the classification as inline JSON."""
    renderer = EventRenderer()
    event = ClassificationEvent(intent="chat", confidence=0.85)
    result = renderer.render(event)
    assert '`{"intent": "chat", "confidence": 0.85}`' in result


def test_classification_event_renders_task_intent() -> None:
    """ClassificationEvent with task intent renders correctly."""
    renderer = EventRenderer()
    event = ClassificationEvent(intent="task", confidence=0.0)
    result = renderer.render(event)
    assert '"intent": "task"' in result
    assert '"confidence": 0.0' in result


# ──────────────────────────────────────────────────────────────────
# RoutingEvent rendering
# ──────────────────────────────────────────────────────────────────


def test_routing_event_renders_heading() -> None:
    """RoutingEvent renders a '🔀 Routing' heading."""
    renderer = EventRenderer()
    event = RoutingEvent(routing="direct", model="claude-sonnet-4-6")
    result = renderer.render(event)
    assert "### 🔀 Routing" in result


def test_routing_event_direct_renders_decision() -> None:
    """RoutingEvent with direct routing shows 'direct response'."""
    renderer = EventRenderer()
    event = RoutingEvent(routing="direct", model="claude-sonnet-4-6")
    result = renderer.render(event)
    assert "direct response" in result


def test_routing_event_agent_plan_renders_decision() -> None:
    """RoutingEvent with agent_plan routing shows 'agent plan'."""
    renderer = EventRenderer()
    event = RoutingEvent(routing="agent_plan", model="claude-sonnet-4-6")
    result = renderer.render(event)
    assert "agent plan" in result


def test_routing_event_renders_model() -> None:
    """RoutingEvent includes the decomposer model name."""
    renderer = EventRenderer()
    event = RoutingEvent(routing="direct", model="claude-opus-4-6")
    result = renderer.render(event)
    assert "claude-opus-4-6" in result


# ──────────────────────────────────────────────────────────────────
# PlanEvent rendering
# ──────────────────────────────────────────────────────────────────


def test_plan_event_renders_heading() -> None:
    """PlanEvent renders a '📋 Plan' heading."""
    renderer = EventRenderer()
    plan = AgentPlan(
        scope="large",
        summary="Refactor auth module",
        agents=[AgentTask(id="a1", task="Extract middleware")],
    )
    event = PlanEvent(plan=plan, summary=plan.summary)
    result = renderer.render(event)
    assert "### 📋 Plan" in result


def test_plan_event_renders_summary() -> None:
    """PlanEvent includes the plan summary text."""
    renderer = EventRenderer()
    plan = AgentPlan(
        scope="large",
        summary="Refactor auth module",
        agents=[AgentTask(id="a1", task="Extract middleware")],
    )
    event = PlanEvent(plan=plan, summary=plan.summary)
    result = renderer.render(event)
    assert "Refactor auth module" in result


def test_plan_event_renders_agent_count() -> None:
    """PlanEvent shows the number of agents."""
    renderer = EventRenderer()
    plan = AgentPlan(
        scope="large",
        summary="Big task",
        agents=[
            AgentTask(id="a1", task="Research"),
            AgentTask(id="a2", task="Implement"),
            AgentTask(id="a3", task="Test", depends_on=["a2"]),
        ],
    )
    event = PlanEvent(plan=plan, summary=plan.summary)
    result = renderer.render(event)
    assert "3 agents" in result


def test_plan_event_renders_agent_ids() -> None:
    """PlanEvent lists the agent IDs."""
    renderer = EventRenderer()
    plan = AgentPlan(
        scope="large",
        summary="Split work",
        agents=[
            AgentTask(id="a1", task="Research"),
            AgentTask(id="a2", task="Implement", depends_on=["a1"]),
        ],
    )
    event = PlanEvent(plan=plan, summary=plan.summary)
    result = renderer.render(event)
    assert "a1" in result
    assert "a2" in result


# ──────────────────────────────────────────────────────────────────
# SubagentStarted rendering
# ──────────────────────────────────────────────────────────────────


def test_subagent_started_renders_heading() -> None:
    """SubagentStarted renders a '🤖 Agent started' heading."""
    renderer = EventRenderer()
    event = SubagentStarted(agent_id="abc123", agent_type="background", agent_name="Atlas")
    result = renderer.render(event)
    assert "### 🤖 Agent" in result
    assert "started" in result


def test_subagent_started_renders_name() -> None:
    """SubagentStarted includes the agent name."""
    renderer = EventRenderer()
    event = SubagentStarted(agent_id="abc123", agent_type="background", agent_name="Atlas")
    result = renderer.render(event)
    assert "Atlas" in result


def test_subagent_started_renders_task() -> None:
    """SubagentStarted includes the agent task when provided."""
    renderer = EventRenderer()
    event = SubagentStarted(
        agent_id="abc", agent_type="bg", agent_name="Bot",
        agent_task="Refactor the auth module",
    )
    result = renderer.render(event)
    assert "Refactor the auth module" in result


def test_subagent_started_falls_back_to_type() -> None:
    """SubagentStarted uses agent_type when agent_name is empty."""
    renderer = EventRenderer()
    event = SubagentStarted(agent_id="abc", agent_type="background")
    result = renderer.render(event)
    assert "background" in result


# ──────────────────────────────────────────────────────────────────
# SubagentStopped rendering
# ──────────────────────────────────────────────────────────────────


def test_subagent_stopped_renders_heading() -> None:
    """SubagentStopped renders a '🤖 Agent completed' heading."""
    renderer = EventRenderer()
    event = SubagentStopped(agent_id="abc123", agent_type="background", agent_name="Atlas")
    result = renderer.render(event)
    assert "### 🤖 Agent" in result
    assert "completed" in result


def test_subagent_stopped_renders_name() -> None:
    """SubagentStopped includes the agent name."""
    renderer = EventRenderer()
    event = SubagentStopped(agent_id="abc123", agent_type="background", agent_name="Atlas")
    result = renderer.render(event)
    assert "Atlas" in result


# ──────────────────────────────────────────────────────────────────
# WaveStarted rendering
# ──────────────────────────────────────────────────────────────────


def test_wave_started_renders_heading() -> None:
    """WaveStarted renders a '🌊 Wave' heading."""
    renderer = EventRenderer()
    event = WaveStarted(wave_number=1, agent_ids=["a1", "a2"])
    result = renderer.render(event)
    assert "### 🌊 Wave 1" in result
    assert "started" in result


def test_wave_started_renders_agent_ids() -> None:
    """WaveStarted lists the agent IDs."""
    renderer = EventRenderer()
    event = WaveStarted(wave_number=1, agent_ids=["a1", "a2"])
    result = renderer.render(event)
    assert "a1" in result
    assert "a2" in result


# ──────────────────────────────────────────────────────────────────
# WaveCompleted rendering
# ──────────────────────────────────────────────────────────────────


def test_wave_completed_renders_heading() -> None:
    """WaveCompleted renders a '🌊 Wave completed' heading."""
    renderer = EventRenderer()
    event = WaveCompleted(wave_number=2, agent_ids=["a3"])
    result = renderer.render(event)
    assert "### 🌊 Wave 2" in result
    assert "completed" in result


def test_wave_completed_renders_failures() -> None:
    """WaveCompleted shows failed agent IDs when present."""
    renderer = EventRenderer()
    event = WaveCompleted(wave_number=1, agent_ids=["a1", "a2"], failed_ids=["a1"])
    result = renderer.render(event)
    assert "a1" in result
    assert "failed" in result.lower()


def test_wave_completed_no_failures() -> None:
    """WaveCompleted with no failures shows all succeeded."""
    renderer = EventRenderer()
    event = WaveCompleted(wave_number=1, agent_ids=["a1", "a2"])
    result = renderer.render(event)
    assert "failed" not in result.lower()
