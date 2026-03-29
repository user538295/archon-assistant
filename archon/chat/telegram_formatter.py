"""Telegram message formatting — converts archon events to Telegram-ready strings."""

import html
from typing import TYPE_CHECKING

from archon.ai.event_mapper import (
    ClassificationEvent,
    ContextInjectedEvent,
    ErrorEvent,
    Event,
    FallbackNoticeEvent,
    PlanEvent,
    PromotionEvent,
    RecoveryEvent,
    ReminderInjectedEvent,
    Response,
    RoutingEvent,
    SkillInjectedEvent,
    SubagentStarted,
    SubagentStopped,
    ThinkingResult,
    ToolResult,
    ToolStarted,
    is_router_event,
)
from archon.ai.tool_result_policy import (
    should_suppress_tool_result,
    summarize_tool_result,
)
from archon.ai.truncation import TruncationStrategy
from archon.chat.md_formatter import md_to_html
from archon.chat.telegram_delivery import render_split_messages

if TYPE_CHECKING:
    from archon.config.loader import NotificationsConfig

DEFAULT_MAX_LEN = 4000


def _brief_result(content: str) -> str:
    """Return a single-line brief summary of tool output.

    Cuts at whichever natural boundary comes first:
    1. After the second period (end of second sentence) vs before the first newline
    2. Fallback: after the first period (end of first sentence)
    3. Hard cut at 160 chars as a last resort
    """
    text = content.strip()
    if not text:
        return "✓ ok"
    p1 = text.find(".")
    p2 = text.find(".", p1 + 1) if p1 >= 0 else -1
    nl = text.find("\n")
    candidates: list[int] = []
    if p2 > 0:
        candidates.append(p2 + 1)  # cut after 2nd period (include it)
    if nl > 0:
        candidates.append(nl)  # cut before newline (exclude it)
    if candidates:
        return f"✓ {text[: min(candidates)]}"
    if p1 > 0:
        return f"✓ {text[: p1 + 1]}"  # fallback: cut after 1st period
    return f"✓ {text[:160]}"


def _task_summary(task: str, max_len: int = 200) -> str:
    """Return the first non-empty line of a task text."""
    for line in task.split("\n"):
        stripped = line.strip()
        if stripped:
            return stripped if len(stripped) <= max_len else stripped[:max_len].rstrip() + "…"
    return ""


def format_event(
    event: Event,
    truncation: TruncationStrategy,
    max_len: int = DEFAULT_MAX_LEN,
    notifications: "NotificationsConfig | None" = None,
) -> list[str]:
    """Format an archon event into one or more Telegram message strings.

    Visibility matrix per mode:
      quiet   — Response, ErrorEvent, SubagentStarted, SubagentStopped only
                (ThinkingResult, ToolStarted, ToolResult, ClassificationEvent,
                RoutingEvent, and ReminderInjectedEvent are filtered here and
                also suppressed upstream in handle_message)
      normal  — Thinking complete, no tools (ToolStarted/ToolResult suppressed)
      verbose — Thinking complete, Tool name only (no args), brief ToolResult,
                ClassificationEvent, RoutingEvent, ReminderInjectedEvent
      debug   — Thinking complete, Tool name + args, full ToolResult,
                ClassificationEvent, RoutingEvent, ReminderInjectedEvent
      None    — treated as "debug" for backward compatibility

    Invariant: PlanEvent, SubagentStarted, SubagentStopped, Response, and ErrorEvent
    are always delivered to the user regardless of mode — they can never be
    suppressed.  ReminderInjectedEvent is NOT in this list — it is mode-gated
    (verbose/debug only).  Do NOT add mode-gating to the always-delivered branches.
    """
    mode = notifications.mode if notifications else "debug"

    # Router events: suppress in quiet/normal, render with [Router] prefix in verbose/debug.
    # ErrorEvent from router is always visible in all modes.
    if is_router_event(event):
        if isinstance(event, ErrorEvent):
            return [f"❌ [Router] Error: {html.escape(event.message)}"]
        if isinstance(event, Response):
            return []  # routing decision — history-only in all modes
        if mode in ("quiet", "normal"):
            return []
        # verbose / debug: render with [Router] prefix
        if isinstance(event, ToolStarted):
            result = f"🔧 [Router] {html.escape(event.name)}"
            if mode == "debug" and event.input:
                result += f"\n{html.escape(str(event.input))[:500]}"
            return [result]
        if isinstance(event, ToolResult):
            summary = (event.content or "")[:160]
            return [f"📤 [Router] {html.escape(summary)}"]
        if isinstance(event, ThinkingResult):
            if mode == "debug":
                return [f"💭 [Router] Thinking:\n{html.escape(event.content or '')}"]
            return []  # verbose: ThinkingResult is history-only
        return []  # fallback for any other router event type

    if isinstance(event, ClassificationEvent):
        if mode not in ("verbose", "debug"):
            return []
        return [f"🏷 {event.intent} ({event.confidence:.0%})"]

    if isinstance(event, RoutingEvent):
        if mode not in ("verbose", "debug"):
            return []
        return [f"🔀 {event.routing}"]

    if isinstance(event, ThinkingResult):
        if mode not in ("normal", "verbose", "debug"):
            return []
        return render_split_messages(
            event.content,
            "💭 Thinking:\n",
            truncation,
            max_len,
            md_to_html,
        )

    if isinstance(event, ToolStarted):
        if mode in ("quiet", "normal"):
            return []
        name = html.escape(event.name)
        id_tag = f" [{event.id}]" if event.id else ""
        if mode == "debug" and event.input:
            return render_split_messages(
                event.input,
                f"🔧 Tool{id_tag}: {name}\n",
                truncation,
                max_len,
                html.escape,
            )
        return [f"🔧 Tool{id_tag}: {name}"]

    if isinstance(event, ToolResult):
        if mode in ("quiet", "normal"):
            return []
        id_tag = f" [{event.id}]" if event.id else ""
        if should_suppress_tool_result(event):
            id_prefix = f"[{event.id}] " if event.id else ""
            return [f"📤 {id_prefix}{summarize_tool_result(event)}"]
        if mode == "debug":
            return render_split_messages(
                event.content,
                f"📤 Result{id_tag}:\n",
                truncation,
                max_len,
                md_to_html,
            )
        # verbose: brief single-line summary with Markdown formatting
        id_prefix = f"[{event.id}] " if event.id else ""
        return [f"📤 {id_prefix}{md_to_html(_brief_result(event.content))}"]

    if isinstance(event, PlanEvent):
        n = len(event.plan.agents)
        agent_word = "agent" if n == 1 else "agents"
        if n > 1:
            bullets = "\n".join(f"• {html.escape(_task_summary(a.task))}" for a in event.plan.agents)
            body = f"📋 Plan: {html.escape(event.summary)}\n{bullets}\n🔄 Spawning {n} {agent_word}..."
        else:
            body = f"📋 Plan: {html.escape(event.summary)}\n🔄 Spawning {n} {agent_word}..."
        return [body]

    if isinstance(event, Response):
        return render_split_messages(
            event.content,
            "✅ Response:\n",
            truncation,
            max_len,
            md_to_html,
        )
    if isinstance(event, ErrorEvent):
        return [f"❌ Error: {html.escape(event.message)}"]

    if isinstance(event, SubagentStarted):
        # Always notify regardless of notification mode — agent lifecycle is critical info
        display = (
            html.escape(event.agent_name)
            if event.agent_name
            else (html.escape(event.agent_type) if event.agent_type else "unknown")
        )
        return [f"🤖 Agent <b>{display}</b> started"]

    if isinstance(event, SubagentStopped):
        # Always notify regardless of notification mode — agent lifecycle is critical info
        display = (
            html.escape(event.agent_name)
            if event.agent_name
            else (html.escape(event.agent_type) if event.agent_type else "unknown")
        )
        return [f"🤖 Agent <b>{display}</b> done"]

    if isinstance(event, PromotionEvent):
        return [f"⚠️ Task grew too large for inline handling ({event.tool_count} tools used) — background agents unavailable"]

    if isinstance(event, FallbackNoticeEvent):
        return [f"⚠️ {html.escape(event.reason)}"]

    if isinstance(event, RecoveryEvent):
        return [f"🔄 {html.escape(event.message)}"]

    if isinstance(event, ReminderInjectedEvent):
        if mode not in ("verbose", "debug"):
            return []
        return [f"🔔 Reminder injected (message {event.message_count})"]

    if isinstance(event, ContextInjectedEvent):
        if mode not in ("verbose", "debug"):
            return []
        if event.injection_type == "rag_retrieval" and event.detail:
            return [f"🔍 RAG: {html.escape(event.detail)}"]
        label = f"📌 Context injected [{html.escape(event.injection_type)}] ({event.size_chars} chars)"
        if event.detail:
            label += f": {html.escape(event.detail)}"
        return [label]

    if isinstance(event, SkillInjectedEvent):
        if mode not in ("verbose", "debug"):
            return []
        return [f"🎯 Skill injected: {html.escape(event.skill_name)} ({event.size_chars} chars)"]

    return []  # pragma: no cover
