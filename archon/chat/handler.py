"""Message handler — forwards user messages to Claude and sends formatted event replies."""
import asyncio
import html
import logging
from typing import TYPE_CHECKING

from aiogram.types import Message

from archon.ai.event_mapper import (
    ErrorEvent,
    Event,
    Response,
    ThinkingResult,
    ThinkingStarted,
    ToolResult,
    ToolStarted,
)
from archon.ai.session_manager import SessionManager
from archon.ai.truncation import TruncationStrategy

if TYPE_CHECKING:
    from archon.config.loader import NotificationsConfig

logger = logging.getLogger("archon")

DEFAULT_MAX_LEN = 4000
_TYPING_INTERVAL = 4  # seconds; Telegram typing action expires after ~5s


async def _keep_typing(message: Message) -> None:
    """Refresh the typing indicator every 4 seconds after the initial send."""
    assert message.bot is not None
    while True:
        await asyncio.sleep(_TYPING_INTERVAL)
        await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")


def _brief_result(content: str) -> str:
    """Return a single-line brief summary of tool output."""
    if not content.strip():
        return "✓ ok"
    first_line = content.strip().split("\n")[0][:80]
    return f"✓ {first_line}"


def format_event(
    event: Event,
    truncation: TruncationStrategy,
    max_len: int = DEFAULT_MAX_LEN,
    notifications: "NotificationsConfig | None" = None,
) -> list[str]:
    """Format an archon event into one or more Telegram message strings."""
    if isinstance(event, ThinkingStarted):
        return ["💭 Thinking..."]
    if isinstance(event, ThinkingResult):
        if notifications and not notifications.show_thinking_result:
            return []
        escaped = html.escape(event.content)
        return [f"💭 Thought:\n{chunk}" for chunk in truncation.apply(escaped, max_len)]
    if isinstance(event, ToolStarted):
        name = html.escape(event.name)
        id_tag = f" [{event.id}]" if event.id else ""
        if event.input:
            return [f"🔧 Tool{id_tag}: {name}\n{chunk}" for chunk in truncation.apply(html.escape(event.input), max_len)]
        return [f"🔧 Tool{id_tag}: {name}"]
    if isinstance(event, ToolResult):
        id_tag = f" [{event.id}]" if event.id else ""
        if notifications and notifications.brief_tool_output:
            id_prefix = f"[{event.id}] " if event.id else ""
            return [f"📤 {id_prefix}{html.escape(_brief_result(event.content))}"]
        escaped = html.escape(event.content)
        return [f"📤 Result{id_tag}:\n{chunk}" for chunk in truncation.apply(escaped, max_len)]
    if isinstance(event, Response):
        escaped = html.escape(event.content)
        return [f"✅ Response:\n{chunk}" for chunk in truncation.apply(escaped, max_len)]
    if isinstance(event, ErrorEvent):
        return [f"❌ Error: {html.escape(event.message)}"]
    return []  # pragma: no cover


async def handle_message(
    message: Message,
    session_manager: SessionManager,
    truncation: TruncationStrategy,
    max_len: int = DEFAULT_MAX_LEN,
    notifications: "NotificationsConfig | None" = None,
) -> None:
    """Forward an incoming text message to Claude and reply with formatted events."""
    if message.text is None or message.from_user is None:
        return

    user_id = message.from_user.id
    logger.info("Message from user %d: %.50s", user_id, message.text)

    session = await session_manager.get_or_create(user_id)

    concise = notifications is not None and notifications.concise_mode
    if concise:
        await message.answer("⏳ Working...")

    assert message.bot is not None
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    typing_task = asyncio.create_task(_keep_typing(message))
    try:
        async for event in session.send(message.text):
            if concise and not isinstance(event, (Response, ErrorEvent)):
                continue
            for text in format_event(event, truncation, max_len, notifications):
                await message.answer(text)
    except Exception as exc:
        logger.error("Error processing message for user %d: %s", user_id, exc)
        await message.answer(f"❌ Error: {html.escape(str(exc))}")
    finally:
        typing_task.cancel()
