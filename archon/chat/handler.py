"""Message handler — forwards user messages to Claude and sends formatted event replies."""
import html
import logging
import time
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
_TYPING_COOLDOWN_SECS = 4.0  # Telegram typing bubble lasts ~5 s; re-send at most once per 4 s


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


def format_event(
    event: Event,
    truncation: TruncationStrategy,
    max_len: int = DEFAULT_MAX_LEN,
    notifications: "NotificationsConfig | None" = None,
) -> list[str]:
    """Format an archon event into one or more Telegram message strings.

    Visibility matrix per mode:
      normal  — Tool name only, brief ToolResult, no thinking
      verbose — Tool name + args, brief ToolResult, thinking start + result
      debug   — Tool name + args, full ToolResult, thinking start + result
      None    — treated as "debug" for backward compatibility

    Agent lifecycle events (SubagentStarted, SubagentStopped), Response, and
    ErrorEvent are always sent regardless of mode — they cannot be suppressed.
    """
    mode = notifications.mode if notifications else "debug"

    if isinstance(event, ThinkingStarted):
        return ["💭 Thinking..."] if mode in ("verbose", "debug") else []

    if isinstance(event, ThinkingResult):
        if mode not in ("verbose", "debug"):
            return []
        return [f"💭 Thought:\n{md_to_html(chunk)}" for chunk in truncation.apply(event.content, max_len)]

    if isinstance(event, ToolStarted):
        name = html.escape(event.name)
        id_tag = f" [{event.id}]" if event.id else ""
        if mode in ("verbose", "debug") and event.input:
            return [f"🔧 Tool{id_tag}: {name}\n{chunk}" for chunk in truncation.apply(html.escape(event.input), max_len)]
        return [f"🔧 Tool{id_tag}: {name}"]

    if isinstance(event, ToolResult):
        id_tag = f" [{event.id}]" if event.id else ""
        if mode == "debug":
            return [f"📤 Result{id_tag}:\n{md_to_html(chunk)}" for chunk in truncation.apply(event.content, max_len)]
        # normal or verbose: brief single-line summary with Markdown formatting
        id_prefix = f"[{event.id}] " if event.id else ""
        return [f"📤 {id_prefix}{md_to_html(_brief_result(event.content))}"]

    if isinstance(event, Response):
        return [f"✅ Response:\n{md_to_html(chunk)}" for chunk in truncation.apply(event.content, max_len)]
    if isinstance(event, ErrorEvent):
        return [f"❌ Error: {html.escape(event.message)}"]

    if isinstance(event, SubagentStarted):
        # Always notify — agent lifecycle is critical info, cannot be suppressed
        display = html.escape(event.agent_name) if event.agent_name else (
            html.escape(event.agent_type) if event.agent_type else "unknown"
        )
        return [f"🤖 Agent <b>{display}</b> started"]

    if isinstance(event, SubagentStopped):
        # Always notify — agent lifecycle is critical info, cannot be suppressed
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
) -> None:
    """Forward an incoming text message to Claude and reply with formatted events.

    An "⏳ Working..." acknowledgement is always sent first, regardless of mode,
    so the user immediately knows their message was received and processing has begun.
    Agent lifecycle events (SubagentStarted/SubagentStopped), Response, and ErrorEvent
    are also always delivered regardless of mode — they cannot be suppressed.
    """
    if message.text is None or message.from_user is None:
        return

    user_id = message.from_user.id
    logger.info("Message received from user %d (%d chars)", user_id, len(message.text))

    if history_manager is not None:
        history_manager.record_user_message(user_id, message.text, cwd=cwd)

    session = await session_manager.get_or_create(user_id)

    # Throttled typing helper — skips the API call if called again within
    # _TYPING_COOLDOWN_SECS to avoid Telegram flood control on SendChatAction.
    last_typing_at: float = 0.0

    async def _send_typing() -> None:
        nonlocal last_typing_at
        now = time.monotonic()
        if now - last_typing_at < _TYPING_COOLDOWN_SECS:
            return
        assert message.bot is not None
        await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
        last_typing_at = now

    # Always notify the user that processing has started — regardless of mode.
    await message.answer("⏳ Working...")
    await _send_typing()

    try:
        async for event in session.send(message.text):
            if history_manager is not None:
                history_manager.record_event(user_id, event)
            for text in format_event(event, truncation, max_len, notifications):
                await _send_typing()
                await message.answer(text)
    except Exception as exc:
        logger.error("Error processing message for user %d (%s)", user_id, type(exc).__name__)
        await message.answer(f"❌ Error: {html.escape(str(exc))}")
