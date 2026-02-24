"""Message handler — forwards user messages to Claude and sends formatted event replies."""
import asyncio
import contextlib
import html
import logging
import random
from typing import TYPE_CHECKING

from aiogram.types import Message

from archon.chat.md_formatter import md_to_html
from archon.ai.event_mapper import (
    ErrorEvent,
    Event,
    Response,
    SubagentStarted,
    SubagentStopped,
    ThinkingResult,
    ThinkingStarted,
    ToolResult,
    ToolStarted,
)
from archon.ai.session_manager import SessionManager
from archon.ai.truncation import TruncationStrategy

if TYPE_CHECKING:
    from archon.ai.history_manager import HistoryManager
    from archon.config.loader import NotificationsConfig

logger = logging.getLogger("archon")

DEFAULT_MAX_LEN = 4000
_TYPING_INTERVAL = 4  # seconds; Telegram typing action expires after ~5s
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


async def _keep_typing(message: Message) -> None:
    """Refresh the typing indicator every 4 seconds after the initial send."""
    assert message.bot is not None
    while True:
        await asyncio.sleep(_TYPING_INTERVAL)
        await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")


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
        await message.answer(_partial_status_text(counts["tools"], counts["thinking"], word))


def format_event(
    event: Event,
    truncation: TruncationStrategy,
    max_len: int = DEFAULT_MAX_LEN,
    notifications: "NotificationsConfig | None" = None,
) -> list[str]:
    """Format an archon event into one or more Telegram message strings.

    Visibility matrix per mode:
      quiet   — Response and ErrorEvent only (everything else filtered here; also
                suppressed upstream in handle_message)
      normal  — Tool name only, brief ToolResult, no thinking
      verbose — Tool name + args, brief ToolResult, thinking start + result
      debug   — Tool name + args, full ToolResult, thinking start + result
      None    — treated as "debug" for backward compatibility
    """
    mode = notifications.mode if notifications else "debug"

    if isinstance(event, ThinkingStarted):
        return ["💭 Thinking..."] if mode in ("verbose", "debug") else []

    if isinstance(event, ThinkingResult):
        if mode not in ("verbose", "debug"):
            return []
        return [f"💭 Thought:\n{md_to_html(chunk)}" for chunk in truncation.apply(event.content, max_len)]

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
            escaped = html.escape(event.content)
            return [f"📤 Result{id_tag}:\n{chunk}" for chunk in truncation.apply(escaped, max_len)]
        # normal or verbose: brief single-line summary
        id_prefix = f"[{event.id}] " if event.id else ""
        return [f"📤 {id_prefix}{html.escape(_brief_result(event.content))}"]

    if isinstance(event, Response):
        return [f"✅ Response:\n{md_to_html(chunk)}" for chunk in truncation.apply(event.content, max_len)]
    if isinstance(event, ErrorEvent):
        return [f"❌ Error: {html.escape(event.message)}"]

    if isinstance(event, SubagentStarted):
        if mode == "quiet":
            return []
        agent_type = html.escape(event.agent_type) if event.agent_type else "unknown"
        return [f"🤖 Agent: <b>{agent_type}</b> started"]

    if isinstance(event, SubagentStopped):
        if mode == "quiet":
            return []
        agent_type = html.escape(event.agent_type) if event.agent_type else "unknown"
        return [f"🤖 Agent: <b>{agent_type}</b> done"]

    return []  # pragma: no cover


async def handle_message(
    message: Message,
    session_manager: SessionManager,
    truncation: TruncationStrategy,
    max_len: int = DEFAULT_MAX_LEN,
    notifications: "NotificationsConfig | None" = None,
    cwd: str = "",
    history_manager: "HistoryManager | None" = None,
) -> None:
    """Forward an incoming text message to Claude and reply with formatted events."""
    if message.text is None or message.from_user is None:
        return

    user_id = message.from_user.id
    logger.info("Message from user %d: %.50s", user_id, message.text)

    if history_manager is not None:
        history_manager.record_user_message(user_id, message.text, cwd=cwd)

    session = await session_manager.get_or_create(user_id)

    mode = notifications.mode if notifications else "debug"
    quiet_active = mode == "quiet"
    counts: dict[str, int] = {"tools": 0, "thinking": 0}
    update_task: asyncio.Task[None] | None = None

    if quiet_active:
        await message.answer("⏳ Working...")

    assert message.bot is not None
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    typing_task = asyncio.create_task(_keep_typing(message))

    if quiet_active and notifications is not None and notifications.interval_minutes > 0:
        interval_secs = notifications.interval_minutes * 60.0
        update_task = asyncio.create_task(_partial_update_task(message, interval_secs, counts))

    try:
        async for event in session.send(message.text):
            # Re-read mode on every event so mid-query /verbose, /quiet, etc. take effect.
            currently_quiet = notifications is not None and notifications.mode == "quiet"

            # Cancel the quiet beacon if the user switched away from quiet mode.
            if not currently_quiet and update_task is not None and not update_task.done():
                update_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await update_task
                update_task = None

            if history_manager is not None:
                history_manager.record_event(user_id, event)
            if currently_quiet:
                if isinstance(event, ToolStarted):
                    counts["tools"] += 1
                elif isinstance(event, ThinkingStarted):
                    counts["thinking"] += 1
                elif isinstance(event, SubagentStarted):
                    counts["tools"] += 1  # count sub-agents as tools in beacon summary
                if not isinstance(event, (Response, ErrorEvent)):
                    continue
            for text in format_event(event, truncation, max_len, notifications):
                await message.answer(text)
    except Exception as exc:
        logger.error("Error processing message for user %d: %s", user_id, exc)
        await message.answer(f"❌ Error: {html.escape(str(exc))}")
    finally:
        if update_task is not None:
            update_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await update_task
        typing_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await typing_task
