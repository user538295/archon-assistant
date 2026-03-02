"""Message handler — forwards user messages to Claude and sends formatted event replies."""
import asyncio
import contextlib
import html
import logging
import random
import time
from typing import TYPE_CHECKING

from aiogram.types import Message

from archon.chat.md_formatter import md_to_html
from archon.ai.event_mapper import (
    ClassificationEvent,
    ErrorEvent,
    Event,
    PlanEvent,
    Response,
    ReviewEvent,
    RoutingEvent,
    SubagentStarted,
    SubagentStopped,
    ThinkingResult,
    ToolResult,
    ToolStarted,
)
from archon.ai.plan_executor import PlanExecutor
from archon.ai.session_manager import SessionManager
from archon.ai.truncation import TruncationStrategy

if TYPE_CHECKING:
    from archon.ai.agent_logger import AgentLogger
    from archon.ai.background_agent_manager import BackgroundAgentManager
    from archon.ai.history_manager import HistoryManager
    from archon.config.loader import NotificationsConfig

logger = logging.getLogger("archon")

DEFAULT_MAX_LEN = 4000
_TYPING_COOLDOWN_SECS = 4.0  # Telegram typing bubble lasts ~5 s; re-send at most once per 4 s
_BEACON_WORDS: tuple[str, ...] = (
    "Pondering",
    "Contemplating",
    "Deliberating",
    "Ruminating",
    "Cogitating",
    "Noodling",
    "Mulling",
    "Brewing",
    "Marinating",
    "Percolating",
    "Scheming",
    "Conjuring",
    "Summoning",
    "Synthesizing",
    "Manifesting",
)


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
        candidates.append(p2 + 1)   # cut after 2nd period (include it)
    if nl > 0:
        candidates.append(nl)        # cut before newline (exclude it)
    if candidates:
        return f"✓ {text[:min(candidates)]}"
    if p1 > 0:
        return f"✓ {text[:p1 + 1]}"  # fallback: cut after 1st period
    return f"✓ {text[:160]}"


def _partial_status_text(tool_count: int, thinking_count: int, word: str = "Working") -> str:
    """Format a partial-mode status update with live event counts."""
    parts = []
    if tool_count > 0:
        parts.append(f"{tool_count} tool{'s' if tool_count != 1 else ''}")
    if thinking_count > 0:
        parts.append(f"{thinking_count} thinking")
    if parts:
        return f"⏳ {word}... ({', '.join(parts)})"
    return f"⏳ {word}..."


async def _partial_update_task(message: Message, interval_secs: float, counts: dict[str, int]) -> None:
    """Periodically send a status update while Claude is processing (quiet beacon mode)."""
    call_count = 0
    while True:
        await asyncio.sleep(interval_secs)
        word = "Working" if call_count == 0 else random.choice(_BEACON_WORDS)
        call_count += 1
        if message.bot is not None:
            await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
        await message.answer(_partial_status_text(counts["tools"], counts["thinking"], word))


def _resolve_agent_mode(notifications: "NotificationsConfig | None") -> str:
    """Return the effective notification mode for sub-agent lifecycle events.

    Resolution order:
    1. `notifications.agents.mode` if explicitly set (not None) → use that
    2. `notifications.mode` if notifications is provided → inherit orchestrator mode
    3. Fall back to "debug" (backward-compat when notifications is None)

    NOTE: This function is intentionally *not* used to suppress agent lifecycle
    events.  SubagentStarted / SubagentStopped / ErrorEvent are always delivered
    to the user regardless of the resolved mode — see the invariant in
    format_event.  This helper exists as an informational utility (e.g. future
    detail-level variation) and is directly tested in test_subagent_integration.py.
    Do NOT add ``if agent_mode == "quiet": return []`` logic to format_event
    for lifecycle events; the tests at test_handle_message_quiet_orch_*_subagent_*
    would catch such a regression.
    """
    if notifications is None:
        return "debug"
    if notifications.agents.mode is not None:
        return notifications.agents.mode
    return notifications.mode


def format_event(
    event: Event,
    truncation: TruncationStrategy,
    max_len: int = DEFAULT_MAX_LEN,
    notifications: "NotificationsConfig | None" = None,
) -> list[str]:
    """Format an archon event into one or more Telegram message strings.

    Visibility matrix per mode:
      quiet   — Response, ErrorEvent, SubagentStarted, SubagentStopped only
                (ThinkingResult, ToolStarted, ToolResult are filtered here and
                also suppressed upstream in handle_message)
      normal  — Tool name only, brief ToolResult, no thinking
      verbose — Tool name + args, brief ToolResult, thinking complete
      debug   — Tool name + args, full ToolResult, thinking complete
      None    — treated as "debug" for backward compatibility

    Invariant: SubagentStarted, SubagentStopped, Response, and ErrorEvent are
    always delivered to the user regardless of mode — they can never be
    suppressed.  Do NOT add mode-gating to those branches.
    """
    mode = notifications.mode if notifications else "debug"

    if isinstance(event, ClassificationEvent):
        if mode not in ("verbose", "debug"):
            return []
        return [f"🏷 {event.intent} ({event.confidence:.0%})"]

    if isinstance(event, ReviewEvent):
        if mode not in ("verbose", "debug"):
            return []
        return [f"🔍 Review: {event.original_intent} ({event.original_confidence:.0%}) → {event.updated_intent} ({event.updated_confidence:.0%})"]

    if isinstance(event, RoutingEvent):
        if mode not in ("verbose", "debug"):
            return []
        return [f"🔀 {event.routing}"]

    if isinstance(event, ThinkingResult):
        if mode not in ("verbose", "debug"):
            return []
        return [f"💭 Thinking:\n{md_to_html(chunk)}" for chunk in truncation.apply(event.content, max_len)]

    if isinstance(event, ToolStarted):
        if mode == "quiet":
            return []
        name = html.escape(event.name)
        id_tag = f" [{event.id}]" if event.id else ""
        if mode in ("verbose", "debug") and event.input:
            return [f"🔧 Tool{id_tag}: {name}\n{chunk}" for chunk in truncation.apply(html.escape(event.input), max_len)]
        return [f"🔧 Tool{id_tag}: {name}"]

    if isinstance(event, ToolResult):
        if mode == "quiet":
            return []
        id_tag = f" [{event.id}]" if event.id else ""
        if mode == "debug":
            return [f"📤 Result{id_tag}:\n{md_to_html(chunk)}" for chunk in truncation.apply(event.content, max_len)]
        # normal or verbose: brief single-line summary with Markdown formatting
        id_prefix = f"[{event.id}] " if event.id else ""
        return [f"📤 {id_prefix}{md_to_html(_brief_result(event.content))}"]

    if isinstance(event, PlanEvent):
        n = len(event.plan.agents)
        return [f"📋 Plan: {html.escape(event.summary)}\n🔄 Spawning {n} agent{'s' if n != 1 else ''}..."]

    if isinstance(event, Response):
        return [f"✅ Response:\n{md_to_html(chunk)}" for chunk in truncation.apply(event.content, max_len)]
    if isinstance(event, ErrorEvent):
        return [f"❌ Error: {html.escape(event.message)}"]

    if isinstance(event, SubagentStarted):
        # Always notify regardless of notification mode — agent lifecycle is critical info
        display = html.escape(event.agent_name) if event.agent_name else (
            html.escape(event.agent_type) if event.agent_type else "unknown"
        )
        return [f"🤖 Agent <b>{display}</b> started"]

    if isinstance(event, SubagentStopped):
        # Always notify regardless of notification mode — agent lifecycle is critical info
        display = html.escape(event.agent_name) if event.agent_name else (
            html.escape(event.agent_type) if event.agent_type else "unknown"
        )
        return [f"🤖 Agent <b>{display}</b> done"]

    return []  # pragma: no cover


async def handle_message(
    message: Message,
    session_manager: SessionManager,
    truncation: TruncationStrategy,
    max_len: int = DEFAULT_MAX_LEN,
    notifications: "NotificationsConfig | None" = None,
    cwd: str = "",
    history_manager: "HistoryManager | None" = None,
    agent_logger: "AgentLogger | None" = None,
    background_agent_manager: "BackgroundAgentManager | None" = None,
) -> None:
    """Forward an incoming text message to Claude and reply with formatted events."""
    if message.text is None or message.from_user is None:
        return

    user_id = message.from_user.id
    logger.info("Message received from user %d (%d chars)", user_id, len(message.text))

    if history_manager is not None:
        history_manager.record_user_message(user_id, message.text, cwd=cwd)

    session = await session_manager.get_or_create(user_id)

    # If the orchestrator is still streaming a response (e.g. while a background
    # agent is running), notify the user immediately that their message is queued.
    # The send() call below will wait for the lock before processing it (Bug.005).
    if session.is_processing:
        try:
            await message.answer("⏳ Previous request still processing — your message is queued")
        except Exception as exc:
            logger.warning(
                "Failed to send 'queued' notification to user %d (%s) — continuing",
                user_id, type(exc).__name__,
            )

    mode = notifications.mode if notifications else "debug"
    quiet_active = mode == "quiet"
    counts: dict[str, int] = {"tools": 0, "thinking": 0}
    update_task: asyncio.Task[None] | None = None

    # Throttled typing helper — skips the API call if called again within
    # _TYPING_COOLDOWN_SECS to avoid Telegram flood control on SendChatAction.
    # Swallows Telegram errors internally: a failed typing bubble must not
    # abort AI processing.
    last_typing_at: float = 0.0

    async def _send_typing() -> None:
        nonlocal last_typing_at
        now = time.monotonic()
        if now - last_typing_at < _TYPING_COOLDOWN_SECS:
            return
        assert message.bot is not None
        last_typing_at = now  # rate-limit retries regardless of outcome
        try:
            await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
        except Exception as exc:
            logger.warning(
                "Failed to send typing indicator to user %d (%s)",
                user_id, type(exc).__name__,
            )

    if quiet_active:
        # A Telegram network error here must not prevent AI work from starting.
        try:
            await message.answer("⏳ Working...")
        except Exception as exc:
            logger.warning(
                "Failed to send 'Working' acknowledgement to user %d (%s) — continuing",
                user_id, type(exc).__name__,
            )

    await _send_typing()

    if quiet_active and notifications is not None and notifications.interval_minutes > 0:
        interval_secs = notifications.interval_minutes * 60.0
        update_task = asyncio.create_task(_partial_update_task(message, interval_secs, counts))

    try:
        async for event in session.send(message.text):
            # FR.003: sub-agent events go to AgentLogger only — not to Telegram.
            if getattr(event, "source", "orchestrator") == "sub-agent":
                if agent_logger is not None:
                    agent_logger.record_event(event)
                continue

            # Re-read mode on every event so mid-query /verbose, /quiet, etc. take effect.
            currently_quiet = notifications is not None and notifications.mode == "quiet"

            # Cancel the quiet beacon if the user switched away from quiet mode.
            if not currently_quiet and update_task is not None and not update_task.done():
                update_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await update_task
                update_task = None

            # Start the beacon if the user just switched INTO quiet+interval mode mid-query.
            if currently_quiet and update_task is None and notifications is not None and notifications.interval_minutes > 0:
                interval_secs = notifications.interval_minutes * 60.0
                update_task = asyncio.create_task(_partial_update_task(message, interval_secs, counts))

            if history_manager is not None:
                history_manager.record_event(user_id, event)
            if currently_quiet:
                if isinstance(event, (SubagentStarted, SubagentStopped, PlanEvent)):
                    # INVARIANT: agent lifecycle events and plan events are ALWAYS
                    # delivered, regardless of notification mode.
                    pass  # fall through to format_event unconditionally
                elif isinstance(event, ToolStarted):
                    counts["tools"] += 1
                    continue
                elif isinstance(event, ThinkingResult):
                    counts["thinking"] += 1
                    continue
                elif not isinstance(event, (Response, ErrorEvent)):
                    continue  # ToolResult, etc. always suppressed in quiet
            # PlanEvent → launch PlanExecutor as detached async task
            if isinstance(event, PlanEvent) and background_agent_manager is not None:
                assert message.bot is not None
                executor = PlanExecutor(
                    bam=background_agent_manager,
                    bot=message.bot,
                    user_id=user_id,
                    cwd=cwd,
                    history_manager=history_manager,
                )
                asyncio.create_task(
                    executor.execute(event.plan),
                    name=f"plan-executor-{user_id}",
                )

            for text in format_event(event, truncation, max_len, notifications):
                await _send_typing()
                try:
                    await message.answer(text, parse_mode="HTML")
                except Exception as exc:
                    # Telegram network flap — log and continue; don't abort Claude's work.
                    logger.warning(
                        "Failed to deliver event reply to user %d (%s) — continuing",
                        user_id, type(exc).__name__,
                    )
    except Exception as exc:
        logger.error("Error processing message for user %d (%s)", user_id, type(exc).__name__)
        try:
            await message.answer(f"❌ Error: {html.escape(str(exc))}")
        except Exception:
            logger.warning(
                "Failed to send error notification to user %d",
                user_id,
                exc_info=True,
            )
    finally:
        if update_task is not None:
            update_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await update_task
