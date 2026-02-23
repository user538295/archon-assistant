"""Telegram bot — factory functions and command handlers."""
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from archon.chat.commands import (
    concise_command,
    filter_command,
    settings_command,
    status_command,
    stop_command,
)

logger = logging.getLogger("archon")


async def start_command(message: Message) -> None:
    """Handle the /start command — greet the user."""
    user_id = message.from_user.id if message.from_user else "unknown"
    logger.info("/start from user %s", user_id)
    await message.answer(
        "Hello! I'm Archon, your Claude Code bridge. Send me a message to get started."
    )


def create_bot(token: str) -> Bot:
    """Create a Bot instance with HTML parse mode."""
    return Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def create_dispatcher() -> Dispatcher:
    """Create a Dispatcher with all command handlers registered."""
    dp = Dispatcher()
    dp.message.register(start_command, CommandStart())
    dp.message.register(status_command, Command("status"))
    dp.message.register(stop_command, Command("stop"))
    dp.message.register(concise_command, Command("concise"))
    dp.message.register(filter_command, Command("filter"))
    dp.message.register(settings_command, Command("settings"))
    return dp
