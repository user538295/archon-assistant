"""Tests for Telegram bot bootstrap — S2.1."""
from unittest.mock import AsyncMock, MagicMock

from aiogram import Bot, Dispatcher
from aiogram.types import Message

from archon.chat.bot import create_bot, create_dispatcher, start_command


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
