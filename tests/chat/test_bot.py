"""Tests for Telegram bot bootstrap — S2.1."""
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommandScopeAllPrivateChats, BotCommandScopeDefault, Message

from archon.chat.bot import BOT_COMMANDS, create_bot, create_dispatcher, setup_bot_commands, start_command
from archon.chat.commands import clear_command, model_command


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


def test_create_dispatcher_registers_model_command() -> None:
    """model_command must be registered as a message handler in the dispatcher."""
    dp = create_dispatcher()
    handlers = dp.observers["message"].handlers
    callbacks = [h.callback for h in handlers]
    assert model_command in callbacks


def test_bot_commands_includes_model() -> None:
    """The BOT_COMMANDS list must include the /model command."""
    command_names = [cmd.command for cmd in BOT_COMMANDS]
    assert "model" in command_names


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
