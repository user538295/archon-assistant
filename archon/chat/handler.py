"""Message handler — forwards user messages to Claude and sends formatted event replies."""
import asyncio
import html
import logging

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

logger = logging.getLogger("archon")

DEFAULT_MAX_LEN = 4000
_TYPING_INTERVAL = 4  # seconds; Telegram typing action expires after ~5s


async def _keep_typing(message: Message) -> None:
    """Refresh the typing indicator every 4 seconds after the initial send."""
    while True:
        await asyncio.sleep(_TYPING_INTERVAL)
        await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")


def format_event(event: Event, truncation: TruncationStrategy, max_len: int = DEFAULT_MAX_LEN) -> list[str]:
    """Format an archon event into one or more Telegram message strings."""
    if isinstance(event, ThinkingStarted):
        return ["💭 Thinking..."]
    if isinstance(event, ThinkingResult):
        escaped = html.escape(event.content)
        return [f"💭 Thought:\n{chunk}" for chunk in truncation.apply(escaped, max_len)]
    if isinstance(event, ToolStarted):
        name = html.escape(event.name)
        if event.input:
            return [f"🔧 Tool: {name}\n{chunk}" for chunk in truncation.apply(html.escape(event.input), max_len)]
        return [f"🔧 Tool: {name}"]
    if isinstance(event, ToolResult):
        escaped = html.escape(event.content)
        return [f"📤 Result:\n{chunk}" for chunk in truncation.apply(escaped, max_len)]
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
) -> None:
    """Forward an incoming text message to Claude and reply with formatted events."""
    if message.text is None or message.from_user is None:
        return

    user_id = message.from_user.id
    logger.info("Message from user %d: %.50s", user_id, message.text)

    session = await session_manager.get_or_create(user_id)
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    typing_task = asyncio.create_task(_keep_typing(message))
    try:
        async for event in session.send(message.text):
            for text in format_event(event, truncation, max_len):
                await message.answer(text)
    except Exception as exc:
        logger.error("Error processing message for user %d: %s", user_id, exc)
        await message.answer(f"❌ Error: {exc}")
    finally:
        typing_task.cancel()
