"""Tests for WhitelistMiddleware — S2.2."""
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import CallbackQuery, Message, TelegramObject

from archon.chat.middleware import WhitelistMiddleware


def _mock_message(user_id: int | None) -> Message:
    msg = MagicMock(spec=Message)
    if user_id is not None:
        msg.from_user = MagicMock(id=user_id)
    else:
        msg.from_user = None
    return msg


# ──────────────────────────────────────────────────────────────────
# Whitelisted user
# ──────────────────────────────────────────────────────────────────


async def test_whitelisted_user_reaches_handler() -> None:
    handler: AsyncMock = AsyncMock(return_value="ok")
    mw = WhitelistMiddleware(allowed_user_ids=[123])

    await mw(handler, _mock_message(123), {})

    handler.assert_awaited_once()


async def test_whitelisted_user_data_passed_to_handler() -> None:
    data: dict[str, Any] = {"key": "value"}
    handler: AsyncMock = AsyncMock(return_value=None)
    mw = WhitelistMiddleware(allowed_user_ids=[42])
    msg = _mock_message(42)

    await mw(handler, msg, data)

    handler.assert_awaited_once_with(msg, data)


async def test_multiple_allowed_users_all_pass() -> None:
    handler: AsyncMock = AsyncMock(return_value=None)
    mw = WhitelistMiddleware(allowed_user_ids=[1, 2, 3])

    for uid in [1, 2, 3]:
        await mw(handler, _mock_message(uid), {})

    assert handler.await_count == 3


# ──────────────────────────────────────────────────────────────────
# Non-whitelisted user
# ──────────────────────────────────────────────────────────────────


async def test_non_whitelisted_user_is_dropped() -> None:
    handler: AsyncMock = AsyncMock()
    mw = WhitelistMiddleware(allowed_user_ids=[123])

    await mw(handler, _mock_message(999), {})

    handler.assert_not_called()


async def test_non_whitelisted_drop_returns_none() -> None:
    handler: AsyncMock = AsyncMock()
    mw = WhitelistMiddleware(allowed_user_ids=[123])

    result = await mw(handler, _mock_message(999), {})

    assert result is None


async def test_from_user_none_is_dropped() -> None:
    """Message without from_user (e.g. channel post) must be dropped."""
    handler: AsyncMock = AsyncMock()
    mw = WhitelistMiddleware(allowed_user_ids=[123])

    await mw(handler, _mock_message(None), {})

    handler.assert_not_called()


async def test_empty_whitelist_drops_everyone() -> None:
    handler: AsyncMock = AsyncMock()
    mw = WhitelistMiddleware(allowed_user_ids=[])

    await mw(handler, _mock_message(42), {})

    handler.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# CallbackQuery events — Critical gap
# ──────────────────────────────────────────────────────────────────


def _mock_callback_query(user_id: int | None) -> CallbackQuery:
    cb = MagicMock(spec=CallbackQuery)
    if user_id is not None:
        cb.from_user = MagicMock(id=user_id)
    else:
        cb.from_user = None
    return cb


async def test_callback_query_whitelisted_user_reaches_handler() -> None:
    """Whitelisted user's CallbackQuery must pass through to the handler."""
    handler: AsyncMock = AsyncMock(return_value="ok")
    mw = WhitelistMiddleware(allowed_user_ids=[123])

    await mw(handler, _mock_callback_query(123), {})

    handler.assert_awaited_once()


async def test_callback_query_non_whitelisted_user_is_dropped() -> None:
    """Non-whitelisted user's CallbackQuery must be silently dropped."""
    handler: AsyncMock = AsyncMock()
    mw = WhitelistMiddleware(allowed_user_ids=[123])

    await mw(handler, _mock_callback_query(999), {})

    handler.assert_not_called()


async def test_callback_query_from_user_none_is_dropped() -> None:
    """CallbackQuery without from_user must be dropped."""
    handler: AsyncMock = AsyncMock()
    mw = WhitelistMiddleware(allowed_user_ids=[123])

    await mw(handler, _mock_callback_query(None), {})

    handler.assert_not_called()


async def test_callback_query_drop_returns_none() -> None:
    handler: AsyncMock = AsyncMock()
    mw = WhitelistMiddleware(allowed_user_ids=[123])

    result = await mw(handler, _mock_callback_query(999), {})

    assert result is None


async def test_callback_query_whitelisted_data_passed_to_handler() -> None:
    data: dict[str, Any] = {"key": "value"}
    handler: AsyncMock = AsyncMock(return_value=None)
    mw = WhitelistMiddleware(allowed_user_ids=[42])
    cb = _mock_callback_query(42)

    await mw(handler, cb, data)

    handler.assert_awaited_once_with(cb, data)


# ──────────────────────────────────────────────────────────────────
# Non-Message / Non-CallbackQuery pass-through — Medium gap
# ──────────────────────────────────────────────────────────────────


async def test_non_message_non_callback_is_denied_by_default() -> None:
    """Any TelegramObject that is neither a Message nor a CallbackQuery must be
    denied by default (deny-by-default policy)."""
    handler: AsyncMock = AsyncMock(return_value="ok")
    mw = WhitelistMiddleware(allowed_user_ids=[])  # nobody allowed

    other_event = MagicMock(spec=TelegramObject)
    result = await mw(handler, other_event, {})

    handler.assert_not_awaited()
    assert result is None
