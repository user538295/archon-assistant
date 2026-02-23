"""Message handler — forwards user messages to Claude and sends formatted event replies."""
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


def format_event(event: Event, truncation: TruncationStrategy, max_len: int = DEFAULT_MAX_LEN) -> list[str]:
    """Format an archon event into one or more Telegram message strings."""
    if isinstance(event, ThinkingStarted):
        return ["💭 Thinking..."]
    if isinstance(event, ThinkingResult):
        return [f"💭 Thought:\n{chunk}" for chunk in truncation.apply(event.content, max_len)]
    if isinstance(event, ToolStarted):
        return [f"🔧 Tool: {event.name}"]
    if isinstance(event, ToolResult):
        return [f"📤 Result:\n{chunk}" for chunk in truncation.apply(event.content, max_len)]
    if isinstance(event, Response):
        return [f"✅ Response:\n{chunk}" for chunk in truncation.apply(event.content, max_len)]
    if isinstance(event, ErrorEvent):
        return [f"❌ Error: {event.message}"]
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
    try:
        async for event in session.send(message.text):
            for text in format_event(event, truncation, max_len):
                await message.answer(text)
    except Exception as exc:
        logger.error("Error processing message for user %d: %s", user_id, exc)
        await message.answer(f"❌ Error: {exc}")
