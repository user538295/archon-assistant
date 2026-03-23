"""Telegram bot — factory functions and command registration."""
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeDefault, Message

from archon.chat.commands import (
    agents_command,
    cancel_agent_callback,
    clear_command,
    command_command,
    context_command,
    debug_command,
    model_callback,
    models_command,
    normal_command,
    notify_callback,
    notify_command,
    quiet_command,
    restart_command,
    scheduled_command,
    skill_command,
    skills_command,
    status_command,
    stop_command,
    tasks_command,
    toggle_job_callback,
    verbose_command,
)

logger = logging.getLogger("archon")

BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="start",      description="Start the bot"),
    BotCommand(command="status",     description="Show session status and uptime"),
    BotCommand(command="context",    description="Show context window usage"),
    BotCommand(command="stop",       description="Stop current Claude session"),
    BotCommand(command="clear",      description="Clear context and start fresh"),
    BotCommand(command="restart",    description="Restart the Archon daemon"),
    BotCommand(command="notify",     description="Manage notification verbosity"),
    BotCommand(command="skills",     description="List available Claude Code skills"),
    BotCommand(command="skill",      description="Activate a skill for your next message"),
    BotCommand(command="models",     description="Show or switch the Claude model"),
    BotCommand(command="agents",     description="List all available agent types"),
    BotCommand(command="tasks",      description="List running background agents"),
    BotCommand(command="scheduled",  description="List scheduled jobs and their status"),
    BotCommand(command="command",    description="List or run a Claude Code command"),
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
    dp.message.register(quiet_command, Command("quiet"))
    dp.message.register(normal_command, Command("normal"))
    dp.message.register(verbose_command, Command("verbose"))
    dp.message.register(debug_command, Command("debug"))
    dp.message.register(skills_command, Command("skills"))
    dp.message.register(skill_command, Command("skill"))
    dp.message.register(models_command, Command("models"))
    dp.message.register(models_command, Command("model"))  # hidden alias
    dp.message.register(agents_command, Command("agents"))
    dp.message.register(tasks_command, Command("tasks"))
    dp.message.register(tasks_command, Command("running_agents"))  # hidden alias
    dp.message.register(scheduled_command, Command("scheduled"))
    dp.message.register(scheduled_command, Command("jobs"))  # hidden alias
    dp.message.register(command_command, Command("command"))
    dp.message.register(command_command, Command("commands"))  # hidden alias (plural)
    dp.callback_query.register(notify_callback, F.data.startswith("notify:"))
    dp.callback_query.register(model_callback, F.data.startswith("model:"))
    dp.callback_query.register(cancel_agent_callback, F.data.startswith("cancel_agent:"))
    dp.callback_query.register(toggle_job_callback, F.data.startswith("toggle_job:"))
    return dp
