"""Chat + AI integration test — S5.2.

Wires Dispatcher + WhitelistMiddleware + message handler + SessionManager
with a mock ClaudeSession, verifying the full Telegram→AI pathway.
"""
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, Update, User

from archon.ai.session_manager import SessionManager
from archon.ai.truncation import SplitStrategy
from archon.chat.handler import handle_message
from archon.chat.middleware import WhitelistMiddleware

_WHITELISTED_ID = 100
_NON_WHITELISTED_ID = 999
_FAKE_TOKEN = "12345:AAFakeTokenForTestingPurposesOnly123"


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_update(user_id: int, text: str = "hello") -> Update:
    user = User(id=user_id, is_bot=False, first_name="Test")
    chat = Chat(id=user_id, type="private")
    msg = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=user,
        text=text,
    )
    return Update(update_id=1, message=msg)


def _mock_session_manager() -> tuple[MagicMock, list[str]]:
    """Return (session_manager_mock, captured_send_prompts)."""
    prompts: list[str] = []

    session = MagicMock()

    async def _send(prompt: str) -> AsyncGenerator[object, None]:
        prompts.append(prompt)
        for _ in ():
            yield  # never runs; presence makes _send an async generator

    session.send = _send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    return mgr, prompts


def _build_dp(allowed_ids: list[int], mgr: MagicMock) -> Dispatcher:
    dp = Dispatcher()
    dp["session_manager"] = mgr
    dp["truncation"] = SplitStrategy()
    dp.message.middleware(WhitelistMiddleware(allowed_user_ids=allowed_ids))
    dp.message.register(handle_message)
    return dp


# ──────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────


async def test_whitelisted_message_reaches_session() -> None:
    """Message from a whitelisted user must reach get_or_create and session.send()."""
    mgr, prompts = _mock_session_manager()
    dp = _build_dp(allowed_ids=[_WHITELISTED_ID], mgr=mgr)
    bot = Bot(token=_FAKE_TOKEN)

    await dp.feed_update(bot, _make_update(_WHITELISTED_ID, text="do it"))

    mgr.get_or_create.assert_awaited_once_with(_WHITELISTED_ID)
    assert prompts == ["do it"]


async def test_non_whitelisted_message_is_silently_dropped() -> None:
    """Message from a non-whitelisted user must not create or touch any session."""
    mgr, prompts = _mock_session_manager()
    dp = _build_dp(allowed_ids=[_WHITELISTED_ID], mgr=mgr)
    bot = Bot(token=_FAKE_TOKEN)

    await dp.feed_update(bot, _make_update(_NON_WHITELISTED_ID))

    mgr.get_or_create.assert_not_called()
    assert prompts == []
