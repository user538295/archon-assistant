"""Telegram bot — factory functions and command registration."""
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeDefault, Message

from archon.chat.commands import (
    agents_command,
    clear_command,
    context_command,
    debug_command,
    jobs_command,
    model_callback,
    model_command,
    normal_command,
    notify_callback,
    notify_command,
    restart_command,
    settings_command,
    skill_command,
    skills_command,
    status_command,
    stop_command,
    verbose_command,
)

logger = logging.getLogger("archon")

BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="start",   description="Start the bot"),
    BotCommand(command="status",  description="Show session status and uptime"),
    BotCommand(command="context", description="Show context window usage"),
    BotCommand(command="stop",    description="Stop current Claude session"),
    BotCommand(command="clear",   description="Clear context and start fresh"),
    BotCommand(command="restart", description="Restart the Archon daemon"),
    BotCommand(command="notify",  description="Manage notification settings"),
    BotCommand(command="normal",  description="Switch to normal mode"),
    BotCommand(command="verbose", description="Switch to verbose mode"),
    BotCommand(command="debug",   description="Switch to debug mode"),
    BotCommand(command="settings", description="Show notification settings panel"),
    BotCommand(command="skills",  description="List available Claude Code skills"),
    BotCommand(command="skill",   description="Activate a skill for your next message"),
    BotCommand(command="model",   description="Show or switch the Claude model"),
    BotCommand(command="agents",  description="List all available agent types"),
    BotCommand(command="jobs",    description="List scheduled cron jobs and their status"),
]


async def setup_bot_commands(bot: Bot) -> None:
    """Register bot commands with Telegram so they appear in the '/' command menu.

    Commands are set for two scopes to guarantee visibility:
    - BotCommandScopeDefault: universal fallback for all chat types
    - BotCommandScopeAllPrivateChats: overrides Default in private chats (higher priority)

    Both must be kept in sync; if only one scope is set the other may serve a stale
    cached list that was registered by a previous bot version.
    """
    logger.info("Registering %d bot commands with Telegram", len(BOT_COMMANDS))
    await bot.set_my_commands(commands=BOT_COMMANDS, scope=BotCommandScopeDefault())
    await bot.set_my_commands(commands=BOT_COMMANDS, scope=BotCommandScopeAllPrivateChats())
    logger.info("Bot commands registered successfully")


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
    dp.message.register(context_command, Command("context"))
    dp.message.register(stop_command, Command("stop"))
    dp.message.register(clear_command, Command("clear"))
    dp.message.register(restart_command, Command("restart"))
    dp.message.register(notify_command, Command("notify"))
    dp.message.register(normal_command, Command("normal"))
    dp.message.register(verbose_command, Command("verbose"))
    dp.message.register(debug_command, Command("debug"))
    dp.message.register(settings_command, Command("settings"))
    dp.message.register(skills_command, Command("skills"))
    dp.message.register(skill_command, Command("skill"))
    dp.message.register(model_command, Command("model"))
    dp.message.register(agents_command, Command("agents"))
    dp.message.register(jobs_command, Command("jobs"))
    dp.callback_query.register(notify_callback, F.data.startswith("notify:"))
    dp.callback_query.register(model_callback, F.data.startswith("model:"))
    return dp
