"""Event renderer — shared Markdown rendering for history and agent log files."""
import json
from datetime import datetime, timezone

RESPONSE_HEADING = "✅ Response"

from archon.ai.agent_plan import topological_sort
from archon.ai.event_mapper import (
    ClassificationEvent,
    ErrorEvent,
    Event,
    FallbackNoticeEvent,
    PlanEvent,
    PromotionEvent,
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
from archon.ai.tool_result_policy import (
    DEFAULT_SUPPRESSED_TOOLS,
    format_tool_result_size,
    should_suppress_tool_result,
    summarize_tool_result,
)


class EventRenderer:
    """Renders SDK events to Markdown strings for log files.

    Suppresses the body of successful read-like tool results, replacing them
    with a compact summary line.  Failed tool results are always logged in full
    so debugging information is preserved.

    Args:
        suppressed_tools: Set of tool names whose successful results are
            suppressed.  Defaults to {"Read", "Glob", "Grep", "WebFetch"}.
    """

    def __init__(
        self,
        suppressed_tools: frozenset[str] | None = None,
    ) -> None:
        self._suppressed = (
            suppressed_tools if suppressed_tools is not None else DEFAULT_SUPPRESSED_TOOLS
        )

    def render(self, event: Event, last_question: str = "") -> str:
        """Render *event* to a Markdown string.

        Args:
            event: The event to render.
            last_question: The most recent user question (used only for
                :class:`Response` events to emit a contextual blockquote).

        Returns:
            A Markdown string to append to the log file, or ``""`` if the
            event type produces no output.
        """
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S %Z")
        if isinstance(event, ThinkingResult):
            return f"\n### 💭 Thinking · {ts}\n\n{event.content}\n"
        if isinstance(event, ToolStarted):
            id_tag = f" [{event.id}]" if event.id else ""
            return f"\n### 🔧 Tool: {event.name}{id_tag} · {ts}\n\n```\n{event.input}\n```\n"
        if isinstance(event, ToolResult):
            return self._render_tool_result(event, ts)
        if isinstance(event, Response):
            q_ctx = (
                f'> User: "{last_question[:120]}{"..." if len(last_question) > 120 else ""}"\n\n'
                if last_question
                else ""
            )
            return f"\n### {RESPONSE_HEADING} · {ts}\n\n{q_ctx}{event.content}\n\n---\n"
        if isinstance(event, ErrorEvent):
            return f"\n### ❌ Error · {ts}\n\n{event.message}\n\n---\n"
        if isinstance(event, ClassificationEvent):
            classification_json = json.dumps(
                {"intent": event.intent, "confidence": event.confidence},
            )
            meta = f" · {event.duration_s}s · model: {event.model}" if event.model else ""
            if event.raw_response:
                raw_section = f"\n\n```\n{event.raw_response}\n```\n"
            else:
                raw_section = "\n\nClassifier output: (empty)\n"
            error_section = f"\n⚠️ Parse error: {event.parse_error}\n" if event.parse_error else ""
            return f"\n### 🏷 Classification · {ts}\n\n`{classification_json}`{meta}\n{raw_section}{error_section}"
        if isinstance(event, RoutingEvent):
            routing_labels = {
                "chat": "Routing: direct chat response",
                "task_direct": "Routing: direct task response",
                "agent_plan": f"Routing: agent plan — {event.agent_count} agents, {event.wave_count} waves",
            }
            decision = routing_labels.get(event.routing, f"Routing: {event.routing}")
            return f"\n### 🔀 Pipeline · {ts}\n\n{decision}\nModel: {event.model}\n"
        if isinstance(event, FallbackNoticeEvent):
            return f"\n### ⚠️ Routing Fallback · {ts}\n\n{event.reason}\n"
        if isinstance(event, PromotionEvent):
            prompt_preview = event.original_prompt[:120] + ("..." if len(event.original_prompt) > 120 else "")
            agent_prompt_preview = event.agent_prompt[:800] + ("..." if len(event.agent_prompt) > 800 else "")
            return (
                f"\n### 🔄 Task Promoted · {ts}\n\n"
                f"Task escalated to background agent after {event.tool_count} tool calls.\n"
                f"Original prompt: {prompt_preview}\n\n"
                f"**Agent prompt (truncated)**:\n\n```\n{agent_prompt_preview}\n```\n"
            )
        if isinstance(event, PlanEvent):
            agents_line = ", ".join(f"{a.id} ({a.task})" for a in event.plan.agents)
            try:
                waves = topological_sort(event.plan)
                waves_line = " → ".join(
                    "[" + ", ".join(a.id for a in w) + "]" for w in waves
                )
            except ValueError:
                waves_line = "(cycle detected)"
            return (
                f"\n### 📋 Plan · {ts}\n\n"
                f"Summary: {event.summary}\n"
                f"Agents: {agents_line}\n"
                f"Waves: {waves_line}\n"
            )
        if isinstance(event, SubagentStarted):
            name = event.agent_name or event.agent_type or "unknown"
            task_line = f"\nTask: {event.agent_task}\n" if event.agent_task else "\n"
            return f"\n### 🤖 Agent {name} started · {ts}\n{task_line}"
        if isinstance(event, SubagentStopped):
            name = event.agent_name or event.agent_type or "unknown"
            return f"\n### 🤖 Agent {name} completed · {ts}\n"
        if isinstance(event, WaveStarted):
            ids = ", ".join(event.agent_names)
            return f"\n### 🌊 Wave {event.wave_number} started · {ts}\n\nAgents: {ids}\n"
        if isinstance(event, WaveCompleted):
            ids = ", ".join(event.agent_names)
            if event.failed_names:
                failed = ", ".join(event.failed_names)
                return f"\n### 🌊 Wave {event.wave_number} completed · {ts}\n\nAgents: {ids}\nFailed: {failed}\n"
            return f"\n### 🌊 Wave {event.wave_number} completed · {ts}\n\nAgents: {ids}\n"
        return ""

    def _render_tool_result(self, event: ToolResult, ts: str) -> str:
        """Render a :class:`ToolResult` event, applying suppression if appropriate."""
        id_tag = f" [{event.id}]" if event.id else ""
        header = f"\n### 📤 Result{id_tag} · {ts}\n\n"
        if should_suppress_tool_result(event, self._suppressed):
            return f"{header}{summarize_tool_result(event, self._suppressed)}\n"
        return f"{header}```\n{event.content}\n```\n"
