"""Tests for Telegram bot bootstrap — S2.1."""
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommandScopeAllPrivateChats, BotCommandScopeDefault, Message

from archon.chat.bot import BOT_COMMANDS, create_bot, create_dispatcher, setup_bot_commands, start_command
from archon.chat.commands import (
    agents_command,
    clear_command,
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
    verbose_command,
)


# ──────────────────────────────────────────────────────────────────
# start_command handler
# ──────────────────────────────────────────────────────────────────


async def test_start_command_replies_with_greeting() -> None:
    msg = MagicMock(spec=Message)
    msg.answer = AsyncMock()
    msg.from_user = MagicMock(id=42)

    await start_command(msg)

    msg.answer.assert_awaited_once()
    text: str = msg.answer.call_args[0][0]
    assert len(text) > 0


async def test_start_command_answer_contains_archon() -> None:
    msg = MagicMock(spec=Message)
    msg.answer = AsyncMock()
    msg.from_user = MagicMock(id=1)

    await start_command(msg)

    text: str = msg.answer.call_args[0][0]
    assert "Archon" in text


async def test_start_command_handles_missing_from_user() -> None:
    """Handler must not raise when from_user is None."""
    msg = MagicMock(spec=Message)
    msg.answer = AsyncMock()
    msg.from_user = None

    await start_command(msg)

    msg.answer.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────
# create_bot factory
# ──────────────────────────────────────────────────────────────────


def test_create_bot_returns_bot_instance() -> None:
    bot = create_bot("12345:fake_token_for_testing")
    assert isinstance(bot, Bot)


def test_create_bot_stores_token() -> None:
    token = "12345:fake_token_for_testing"
    bot = create_bot(token)
    assert bot.token == token


# ──────────────────────────────────────────────────────────────────
# create_dispatcher factory
# ──────────────────────────────────────────────────────────────────


def test_create_dispatcher_returns_dispatcher() -> None:
    dp = create_dispatcher()
    assert isinstance(dp, Dispatcher)


def test_create_dispatcher_registers_start_command() -> None:
    """start_command must be registered as a message handler in the dispatcher."""
    dp = create_dispatcher()
    handlers = dp.observers["message"].handlers
    callbacks = [h.callback for h in handlers]
    assert start_command in callbacks


def test_create_dispatcher_registers_clear_command() -> None:
    """clear_command must be registered as a message handler in the dispatcher."""
    dp = create_dispatcher()
    handlers = dp.observers["message"].handlers
    callbacks = [h.callback for h in handlers]
    assert clear_command in callbacks


def test_create_dispatcher_registers_models_command() -> None:
    """models_command must be registered as a message handler in the dispatcher."""
    dp = create_dispatcher()
    handlers = dp.observers["message"].handlers
    callbacks = [h.callback for h in handlers]
    assert models_command in callbacks


def test_bot_commands_includes_models() -> None:
    """The BOT_COMMANDS list must include the /models command."""
    command_names = [cmd.command for cmd in BOT_COMMANDS]
    assert "models" in command_names


# ──────────────────────────────────────────────────────────────────
# setup_bot_commands
# ──────────────────────────────────────────────────────────────────


async def test_setup_bot_commands_sets_default_scope() -> None:
    """setup_bot_commands must call set_my_commands for BotCommandScopeDefault."""
    bot = MagicMock(spec=Bot)
    bot.set_my_commands = AsyncMock(return_value=True)

    await setup_bot_commands(bot)

    scopes_used = [c.kwargs["scope"] for c in bot.set_my_commands.call_args_list]
    assert any(isinstance(s, BotCommandScopeDefault) for s in scopes_used)


async def test_setup_bot_commands_sets_all_private_chats_scope() -> None:
    """setup_bot_commands must call set_my_commands for BotCommandScopeAllPrivateChats."""
    bot = MagicMock(spec=Bot)
    bot.set_my_commands = AsyncMock(return_value=True)

    await setup_bot_commands(bot)

    scopes_used = [c.kwargs["scope"] for c in bot.set_my_commands.call_args_list]
    assert any(isinstance(s, BotCommandScopeAllPrivateChats) for s in scopes_used)


async def test_setup_bot_commands_called_twice() -> None:
    """setup_bot_commands must call set_my_commands exactly twice (one per scope)."""
    bot = MagicMock(spec=Bot)
    bot.set_my_commands = AsyncMock(return_value=True)

    await setup_bot_commands(bot)

    assert bot.set_my_commands.await_count == 2


async def test_setup_bot_commands_passes_full_command_list() -> None:
    """Both set_my_commands calls must receive the full BOT_COMMANDS list."""
    bot = MagicMock(spec=Bot)
    bot.set_my_commands = AsyncMock(return_value=True)

    await setup_bot_commands(bot)

    for c in bot.set_my_commands.call_args_list:
        assert c.kwargs["commands"] == BOT_COMMANDS


# ──────────────────────────────────────────────────────────────────
# Dispatcher — all 15 command + 2 callback registrations (High gap)
# ──────────────────────────────────────────────────────────────────


def test_create_dispatcher_registers_status_command() -> None:
    dp = create_dispatcher()
    callbacks = [h.callback for h in dp.observers["message"].handlers]
    assert status_command in callbacks


def test_create_dispatcher_registers_context_command() -> None:
    dp = create_dispatcher()
    callbacks = [h.callback for h in dp.observers["message"].handlers]
    assert context_command in callbacks


def test_create_dispatcher_registers_stop_command() -> None:
    dp = create_dispatcher()
    callbacks = [h.callback for h in dp.observers["message"].handlers]
    assert stop_command in callbacks


def test_create_dispatcher_registers_restart_command() -> None:
    dp = create_dispatcher()
    callbacks = [h.callback for h in dp.observers["message"].handlers]
    assert restart_command in callbacks


def test_create_dispatcher_registers_notify_command() -> None:
    dp = create_dispatcher()
    callbacks = [h.callback for h in dp.observers["message"].handlers]
    assert notify_command in callbacks


def test_create_dispatcher_registers_quiet_command() -> None:
    dp = create_dispatcher()
    callbacks = [h.callback for h in dp.observers["message"].handlers]
    assert quiet_command in callbacks


def test_create_dispatcher_registers_normal_command() -> None:
    dp = create_dispatcher()
    callbacks = [h.callback for h in dp.observers["message"].handlers]
    assert normal_command in callbacks


def test_create_dispatcher_registers_verbose_command() -> None:
    dp = create_dispatcher()
    callbacks = [h.callback for h in dp.observers["message"].handlers]
    assert verbose_command in callbacks


def test_create_dispatcher_registers_debug_command() -> None:
    dp = create_dispatcher()
    callbacks = [h.callback for h in dp.observers["message"].handlers]
    assert debug_command in callbacks


def test_create_dispatcher_registers_skills_command() -> None:
    dp = create_dispatcher()
    callbacks = [h.callback for h in dp.observers["message"].handlers]
    assert skills_command in callbacks


def test_create_dispatcher_registers_skill_command() -> None:
    dp = create_dispatcher()
    callbacks = [h.callback for h in dp.observers["message"].handlers]
    assert skill_command in callbacks


def test_create_dispatcher_registers_all_message_commands() -> None:
    """Every command handler must be present in the message observer."""
    from archon.chat.commands import command_command
    dp = create_dispatcher()
    callbacks = [h.callback for h in dp.observers["message"].handlers]
    expected = [
        start_command, status_command, context_command, stop_command,
        clear_command, restart_command, notify_command, quiet_command,
        normal_command, verbose_command, debug_command,
        skills_command, skill_command, models_command,
        agents_command, tasks_command, scheduled_command,
        command_command,
    ]
    missing = [fn.__name__ for fn in expected if fn not in callbacks]
    assert missing == [], f"Missing handlers: {missing}"


def test_create_dispatcher_registers_notify_callback() -> None:
    """notify_callback must be registered in the callback_query observer."""
    dp = create_dispatcher()
    callbacks = [h.callback for h in dp.observers["callback_query"].handlers]
    assert notify_callback in callbacks


def test_create_dispatcher_registers_model_callback() -> None:
    """model_callback must be registered in the callback_query observer."""
    dp = create_dispatcher()
    callbacks = [h.callback for h in dp.observers["callback_query"].handlers]
    assert model_callback in callbacks


# ──────────────────────────────────────────────────────────────────
# BOT_COMMANDS completeness — Medium gap
# ──────────────────────────────────────────────────────────────────


def test_bot_commands_count_is_14() -> None:
    """BOT_COMMANDS must list exactly 14 commands."""
    assert len(BOT_COMMANDS) == 14


def test_bot_commands_contains_all_expected_names() -> None:
    """Every expected command name must appear in BOT_COMMANDS."""
    command_names = {cmd.command for cmd in BOT_COMMANDS}
    expected = {
        "start", "status", "context", "stop", "clear", "restart",
        "notify", "skills", "skill", "models", "agents", "tasks", "scheduled",
        "command",
    }
    assert command_names == expected


# ──────────────────────────────────────────────────────────────────
# Dispatcher — alias routing tests
# ──────────────────────────────────────────────────────────────────


def test_create_dispatcher_model_alias_registered():
    """models_command is registered for both /models and /model."""
    dp = create_dispatcher()
    model_handlers = [h for h in dp.message.handlers
                      if h.callback is models_command]
    assert len(model_handlers) == 2, "models_command must be registered for both /models and /model"


def test_create_dispatcher_tasks_alias_registered():
    """tasks_command is registered for both /tasks and /running_agents."""
    dp = create_dispatcher()
    tasks_handlers = [h for h in dp.message.handlers
                      if h.callback is tasks_command]
    assert len(tasks_handlers) == 2, "tasks_command must be registered for both /tasks and /running_agents"


def test_create_dispatcher_scheduled_alias_registered():
    """scheduled_command is registered for both /scheduled and /jobs."""
    dp = create_dispatcher()
    scheduled_handlers = [h for h in dp.message.handlers
                          if h.callback is scheduled_command]
    assert len(scheduled_handlers) == 2, "scheduled_command must be registered for both /scheduled and /jobs"


# ──────────────────────────────────────────────────────────────────
# command_command registration — Task 2.2
# ──────────────────────────────────────────────────────────────────


def test_create_dispatcher_registers_command_command() -> None:
    """command_command must be registered for Command('command') in the dispatcher."""
    from archon.chat.commands import command_command
    dp = create_dispatcher()
    callbacks = [h.callback for h in dp.observers["message"].handlers]
    assert command_command in callbacks


def test_create_dispatcher_registers_commands_alias() -> None:
    """command_command must also be registered for Command('commands') (plural alias)."""
    from archon.chat.commands import command_command
    dp = create_dispatcher()
    command_handlers = [h for h in dp.message.handlers if h.callback is command_command]
    assert len(command_handlers) == 2, "command_command must be registered for both /command and /commands"


def test_bot_commands_includes_command() -> None:
    """BOT_COMMANDS must contain an entry with command='command'."""
    command_names = [cmd.command for cmd in BOT_COMMANDS]
    assert "command" in command_names
