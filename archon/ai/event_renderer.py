"""Event renderer — shared Markdown rendering for history and agent log files."""
import json
from datetime import datetime, timezone

from archon.ai.event_mapper import (
    ClassificationEvent,
    ErrorEvent,
    Event,
    PlanEvent,
    Response,
    RoutingEvent,
    ThinkingResult,
    ToolResult,
    ToolStarted,
)

_DEFAULT_SUPPRESSED: frozenset[str] = frozenset({"Read", "Glob", "Grep", "WebFetch"})


def _format_size(byte_count: int) -> str:
    """Format a byte count as a human-readable string (B or KB)."""
    if byte_count < 1024:
        return f"{byte_count} B"
    return f"{byte_count / 1024:.1f} KB"


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
            suppressed_tools if suppressed_tools is not None else _DEFAULT_SUPPRESSED
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
            return f"\n### ✅ Response · {ts}\n\n{q_ctx}{event.content}\n\n---\n"
        if isinstance(event, ErrorEvent):
            return f"\n### ❌ Error · {ts}\n\n{event.message}\n\n---\n"
        if isinstance(event, ClassificationEvent):
            classification_json = json.dumps(
                {"intent": event.intent, "confidence": event.confidence},
            )
            return f"\n### 🏷 Classification · {ts}\n\n`{classification_json}`\n"
        if isinstance(event, RoutingEvent):
            decision = "direct response" if event.routing == "direct" else "agent plan"
            return f"\n### 🔀 Routing · {ts}\n\nDecision: {decision}\nModel: {event.model}\n"
        if isinstance(event, PlanEvent):
            agent_count = len(event.plan.agents)
            agent_ids = ", ".join(a.id for a in event.plan.agents)
            return (
                f"\n### 📋 Plan · {ts}\n\n"
                f"{event.summary}\n"
                f"Agents: {agent_count} agents ({agent_ids})\n"
            )
        return ""

    def _render_tool_result(self, event: ToolResult, ts: str) -> str:
        """Render a :class:`ToolResult` event, applying suppression if appropriate."""
        id_tag = f" [{event.id}]" if event.id else ""
        header = f"\n### 📤 Result{id_tag} · {ts}\n\n"
        suppress = event.tool_name in self._suppressed and not event.is_error
        if suppress:
            lines = len(event.content.splitlines())
            size = _format_size(len(event.content.encode("utf-8")))
            tool = event.tool_name or "tool"
            return f"{header}✓ {tool} completed ({lines} lines, {size})\n"
        return f"{header}```\n{event.content}\n```\n"
