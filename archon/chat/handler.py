"""Message handler — forwards user messages to Claude and sends formatted event replies."""

import asyncio
import contextlib
import html
import logging
import random
import time
from typing import TYPE_CHECKING

from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import Message

from archon.ai.event_mapper import (
    ErrorEvent,
    FallbackNoticeEvent,
    PlanEvent,
    PromotionEvent,
    RecoveryEvent,
    Response,
    SubagentStarted,
    SubagentStopped,
    ThinkingResult,
    ToolStarted,
    is_router_event,
)
from archon.ai.plan_executor import PlanExecutor
from archon.ai.session_manager import SessionManager
from archon.ai.truncation import TruncationStrategy
from archon.chat.telegram_formatter import _task_summary  # noqa: F401 — re-exported for callers
from archon.chat.telegram_formatter import format_event  # noqa: F401 — re-exported for callers

if TYPE_CHECKING:
    from archon.ai.agent_logger import AgentLogger
    from archon.ai.background_agent_manager import BackgroundAgentManager
    from archon.ai.history_manager import HistoryManager
    from archon.config.loader import NotificationsConfig

logger = logging.getLogger("archon")

DEFAULT_MAX_LEN = 4000

# Holds strong references to fire-and-forget tasks so they are not GC'd
# before completion.  Each task removes itself via done_callback.
_background_tasks: set["asyncio.Task[None]"] = set()

_TYPING_COOLDOWN_SECS = (
    4.0  # Telegram typing bubble lasts ~5 s; re-send at most once per 4 s
)
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
    "Concocting",
    "Tinkering",
)


def _partial_status_text(
    tool_count: int, thinking_count: int, word: str = "Working"
) -> str:
    """Format a partial-mode status update with live event counts."""
    parts = []
    if tool_count > 0:
        parts.append(f"{tool_count} tool{'s' if tool_count != 1 else ''}")
    if thinking_count > 0:
        parts.append(f"{thinking_count} thinking")
    if parts:
        return f"⏳ {word}... ({', '.join(parts)})"
    return f"⏳ {word}..."


async def _partial_update_task(
    message: Message,
    interval_secs: float,
    counts: dict[str, int],
    history_manager: "HistoryManager | None" = None,
) -> None:
    """Periodically send a status update while Claude is processing (quiet beacon mode)."""
    call_count = 0
    while True:
        await asyncio.sleep(interval_secs)
        word = "Working" if call_count == 0 else random.choice(_BEACON_WORDS)
        call_count += 1
        if message.bot is not None:
            await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
        beacon_text = _partial_status_text(counts["tools"], counts["thinking"], word)
        await message.answer(beacon_text)
        if history_manager is not None:
            await history_manager.record_archon_message(beacon_text)


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


async def check_auto_compact(
    session_manager: "SessionManager",
    user_id: int,
    message: "Message",
    history_manager: "HistoryManager | None",
    notifications: "NotificationsConfig | None",
) -> None:
    """Check and trigger auto-compaction after response delivery."""
    try:
        pct = await session_manager.auto_compact_if_needed(user_id)
        if pct is not None:
            note = f"⚙️ Auto-compaction triggered (context: {pct}% of 200K)"
            logger.info(note)
            if history_manager is not None:
                await history_manager.record_archon_message(note)
            mode = notifications.mode if notifications else "debug"
            if mode in ("verbose", "debug"):
                await message.answer(note)
    except Exception:
        logger.error("Auto-compaction check failed for user %d", user_id, exc_info=True)


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
    prompt_override: str | None = None,
) -> None:
    """Forward an incoming text message to Claude and reply with formatted events."""
    text = prompt_override or message.text
    if text is None or message.from_user is None:
        return

    user_id = message.from_user.id
    logger.info("Message received from user %d (%d chars)", user_id, len(text))

    if history_manager is not None:
        await history_manager.record_user_message(user_id, text, cwd=cwd)

    session = await session_manager.get_or_create(user_id)

    # If the orchestrator is still streaming a response (e.g. while a background
    # agent is running), notify the user immediately that their message is queued.
    # The send() call below will wait for the lock before processing it (Bug.005).
    was_queued = session.is_processing
    if was_queued:
        try:
            await message.answer(
                "⏳ Previous request still processing — your message is queued"
            )
            if history_manager is not None:
                await history_manager.record_archon_message(
                    "⏳ Previous request still processing — your message is queued"
                )
        except Exception as exc:
            logger.warning(
                "Failed to send 'queued' notification to user %d (%s) — continuing",
                user_id,
                type(exc).__name__,
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
        if message.bot is None:
            logger.warning("message.bot is None, skipping typing indicator")
            return
        last_typing_at = now  # rate-limit retries regardless of outcome
        try:
            await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
        except Exception as exc:
            logger.warning(
                "Failed to send typing indicator to user %d (%s)",
                user_id,
                type(exc).__name__,
            )

    # Only send the "Processing..." ack if the message is starting immediately
    # (not queued). When queued, the user already got the queued notification.
    if not was_queued:
        ack = "⏳ Working..." if quiet_active else "⏳ Processing..."
        try:
            await message.answer(ack)
            if history_manager is not None:
                await history_manager.record_archon_message(ack)
        except Exception as exc:
            logger.warning(
                "Failed to send acknowledgement to user %d (%s) — continuing",
                user_id,
                type(exc).__name__,
            )

    await _send_typing()

    if (
        quiet_active
        and notifications is not None
        and notifications.interval_minutes > 0
    ):
        interval_secs = notifications.interval_minutes * 60.0
        update_task = asyncio.create_task(
            _partial_update_task(message, interval_secs, counts, history_manager)
        )

    try:
        async for event in session.send(text):
            # FR.003: sub-agent events go to AgentLogger only — not to Telegram.
            # Sub-agent events are intentionally excluded from session history — logged via AgentLogger only.
            if getattr(event, "source", "orchestrator") == "sub-agent":
                if agent_logger is not None:
                    await agent_logger.record_event(event)
                if history_manager is not None and isinstance(event, (Response, ErrorEvent)):
                    await history_manager.record_event(user_id, event)
                continue

            # Re-read mode on every event so mid-query /verbose, /quiet, etc. take effect.
            currently_quiet = (
                notifications is not None and notifications.mode == "quiet"
            )

            # Cancel the quiet beacon if the user switched away from quiet mode.
            if (
                not currently_quiet
                and update_task is not None
                and not update_task.done()
            ):
                update_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await update_task
                update_task = None

            # Start the beacon if the user just switched INTO quiet+interval mode mid-query.
            if (
                currently_quiet
                and update_task is None
                and notifications is not None
                and notifications.interval_minutes > 0
            ):
                interval_secs = notifications.interval_minutes * 60.0
                update_task = asyncio.create_task(
                    _partial_update_task(message, interval_secs, counts, history_manager)
                )

            if history_manager is not None:
                await history_manager.record_event(user_id, event)
            if currently_quiet:
                # Router events: suppress without counting (they don't affect beacon counts)
                if is_router_event(event):
                    if not isinstance(event, ErrorEvent):
                        continue
                    # Router ErrorEvent: fall through to format_event (always visible)
                if getattr(event, "source", "") == "classifier":
                    continue
                elif isinstance(
                    event, (SubagentStarted, SubagentStopped, PlanEvent, PromotionEvent, FallbackNoticeEvent, RecoveryEvent)
                ):
                    # INVARIANT: agent lifecycle, plan, promotion, and fallback events are ALWAYS
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
                if message.bot is None:
                    logger.error("message.bot is None in event loop, skipping PlanExecutor")
                    continue
                executor = PlanExecutor(
                    bam=background_agent_manager,
                    bot=message.bot,
                    user_id=user_id,
                    cwd=cwd,
                    history_manager=history_manager,
                    context_summary=getattr(session, "context_summary", ""),
                )
                _task = asyncio.create_task(
                    executor.execute(event.plan),
                    name=f"plan-executor-{user_id}",
                )
                _background_tasks.add(_task)
                _task.add_done_callback(_background_tasks.discard)

            # PromotionEvent → spawn background agent for promoted task
            if isinstance(event, PromotionEvent) and background_agent_manager is not None:
                # Send the "handing off" notification BEFORE spawn() so it arrives
                # before the "🤖 Agent X spawned." message that spawn() sends internally.
                handoff_msg = f"🔄 Task is bigger than expected — handing off to a background agent ({event.tool_count} tools used)"
                try:
                    await message.answer(handoff_msg, parse_mode="HTML")
                    if history_manager is not None:
                        await history_manager.record_archon_message(handoff_msg)
                except Exception as exc:
                    logger.warning(
                        "Failed to deliver promotion notification to user %d (%s) — continuing",
                        user_id, type(exc).__name__,
                    )
                try:
                    run = await background_agent_manager.spawn(
                        user_id=user_id,
                        task=event.agent_prompt,
                        user_request=text,
                        context=getattr(session, "context_summary", ""),
                    )
                    logger.info(
                        "Task promoted to agent %r (user=%d, tools=%d)",
                        run.name, user_id, event.tool_count,
                    )
                except Exception as exc:
                    logger.error("Failed to spawn promoted agent for user %d: %s", user_id, exc)
                    try:
                        failure_msg = f"⚠️ Task promotion failed — could not start background agent ({event.tool_count} tools used)"
                        await message.answer(failure_msg, parse_mode="HTML")
                        if history_manager is not None:
                            await history_manager.record_archon_message(failure_msg)
                    except Exception as notify_exc:
                        logger.warning(
                            "Failed to deliver spawn-failure notification to user %d (%s) — continuing",
                            user_id, type(notify_exc).__name__,
                        )
                continue  # skip format_event for PromotionEvent

            for part in format_event(event, truncation, max_len, notifications):
                await _send_typing()
                try:
                    await message.answer(part, parse_mode="HTML")
                except TelegramRetryAfter as exc:
                    await asyncio.sleep(exc.retry_after + 1)
                    try:
                        await message.answer(part, parse_mode="HTML")
                    except Exception as retry_exc:
                        logger.warning(
                            "Failed to deliver event reply after retry-after to user %d (%s) — continuing",
                            user_id,
                            type(retry_exc).__name__,
                        )
                except Exception as exc:
                    # Telegram network flap — log and continue; don't abort Claude's work.
                    logger.warning(
                        "Failed to deliver event reply to user %d (%s) — continuing",
                        user_id,
                        type(exc).__name__,
                    )
        await check_auto_compact(session_manager, user_id, message, history_manager, notifications)
    except asyncio.CancelledError:
        # CancelledError is a BaseException, not caught by `except Exception`.
        # Notify the user, then re-raise so aiogram handles task cleanup.
        logger.warning("Message processing cancelled for user %d — task received CancelledError", user_id)
        interrupted_text = "⚙️ Processing was interrupted unexpectedly. The system is recovering — please resend your message."
        try:
            await message.answer(interrupted_text)
            if history_manager is not None:
                await history_manager.record_archon_message(interrupted_text)
        except Exception:
            logger.warning("Failed to deliver cancellation notice to user %d", user_id)
        raise
    except Exception as exc:
        logger.error(
            "Error processing message for user %d (%s)", user_id, type(exc).__name__
        )
        try:
            error_text = f"❌ Error: {html.escape(str(exc))}"
            await message.answer(error_text)
            if history_manager is not None:
                await history_manager.record_archon_message(error_text)
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
