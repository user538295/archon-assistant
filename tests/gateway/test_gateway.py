"""Tests for gateway wiring — H3 + S3.1."""
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, Update, User

from archon.ai.event_mapper import Response
from archon.ai.session_manager import SessionManager
from archon.chat.bot import create_dispatcher
from archon.chat.middleware import WhitelistMiddleware
from archon.config.loader import AccessConfig, Config, LoggingConfig, OutputConfig, SessionConfig
from archon.gateway.gateway import _setup_dp, register_middleware

_FAKE_TOKEN = "12345:AAFakeTokenForTestingPurposesOnly123"
_ALLOWED_ID = 100
_BLOCKED_ID = 999


def _make_config(allowed_user_ids: list[int] | None = None) -> Config:
    return Config(
        telegram_bot_token=_FAKE_TOKEN,
        access=AccessConfig(allowed_user_ids=allowed_user_ids or [_ALLOWED_ID]),
        session=SessionConfig(working_directory="/tmp"),
        output=OutputConfig(),
        logging=LoggingConfig(),
    )


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


def _mock_session_manager(events: list[object] | None = None) -> MagicMock:
    session = MagicMock()

    async def _send(prompt: str) -> AsyncGenerator[object, None]:
        for ev in (events or []):
            yield ev

    session.send = _send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    return mgr


def _whitelist_middlewares(dp: Dispatcher) -> list[object]:
    return list(dp.message.middleware._middlewares)


# ──────────────────────────────────────────────────────────────────
# create_dispatcher intentionally has no WhitelistMiddleware
# ──────────────────────────────────────────────────────────────────


def test_create_dispatcher_has_no_whitelist_middleware() -> None:
    """create_dispatcher() must NOT register WhitelistMiddleware — gateway's job."""
    dp = create_dispatcher()
    for mw in _whitelist_middlewares(dp):
        assert not isinstance(mw, WhitelistMiddleware)


# ──────────────────────────────────────────────────────────────────
# register_middleware wires WhitelistMiddleware onto the dispatcher
# ──────────────────────────────────────────────────────────────────


def test_register_middleware_adds_whitelist_middleware() -> None:
    dp = create_dispatcher()
    register_middleware(dp, allowed_user_ids=[42, 99])
    types = [type(mw) for mw in _whitelist_middlewares(dp)]
    assert WhitelistMiddleware in types


def test_register_middleware_passes_allowed_ids() -> None:
    dp = create_dispatcher()
    register_middleware(dp, allowed_user_ids=[7])
    mws = [mw for mw in _whitelist_middlewares(dp) if isinstance(mw, WhitelistMiddleware)]
    assert len(mws) == 1
    assert mws[0]._allowed == frozenset([7])


# ──────────────────────────────────────────────────────────────────
# _setup_dp wiring — S3.1
# ──────────────────────────────────────────────────────────────────


def test_setup_dp_registers_whitelist_middleware() -> None:
    cfg = _make_config(allowed_user_ids=[42])
    dp = create_dispatcher()
    _setup_dp(dp, cfg, _mock_session_manager())
    types = [type(mw) for mw in _whitelist_middlewares(dp)]
    assert WhitelistMiddleware in types


def test_setup_dp_injects_session_manager() -> None:
    cfg = _make_config()
    mgr = _mock_session_manager()
    dp = create_dispatcher()
    _setup_dp(dp, cfg, mgr)
    assert dp["session_manager"] is mgr


def test_setup_dp_injects_max_len_from_config() -> None:
    cfg = _make_config()
    cfg.output.max_message_length = 1234
    dp = create_dispatcher()
    _setup_dp(dp, cfg, _mock_session_manager())
    assert dp["max_len"] == 1234


def test_setup_dp_injects_cwd_from_config() -> None:
    cfg = _make_config()
    dp = create_dispatcher()
    _setup_dp(dp, cfg, _mock_session_manager())
    assert dp["cwd"] == "/tmp"


# ──────────────────────────────────────────────────────────────────
# message → session → reply integration — S3.1
# ──────────────────────────────────────────────────────────────────


async def test_whitelisted_message_reaches_session() -> None:
    mgr = _mock_session_manager()
    cfg = _make_config(allowed_user_ids=[_ALLOWED_ID])
    dp = create_dispatcher()
    _setup_dp(dp, cfg, mgr)

    bot = Bot(token=_FAKE_TOKEN)
    with patch("aiogram.Bot.send_chat_action", new_callable=AsyncMock):
        await dp.feed_update(bot, _make_update(_ALLOWED_ID, text="do it"))

    mgr.get_or_create.assert_awaited_once_with(_ALLOWED_ID)


async def test_blocked_message_does_not_reach_session() -> None:
    mgr = _mock_session_manager()
    cfg = _make_config(allowed_user_ids=[_ALLOWED_ID])
    dp = create_dispatcher()
    _setup_dp(dp, cfg, mgr)

    bot = Bot(token=_FAKE_TOKEN)
    await dp.feed_update(bot, _make_update(_BLOCKED_ID, text="hack"))

    mgr.get_or_create.assert_not_called()


async def test_session_response_is_sent_back_to_chat() -> None:
    mgr = _mock_session_manager(events=[Response(content="OK")])
    cfg = _make_config(allowed_user_ids=[_ALLOWED_ID])
    dp = create_dispatcher()
    _setup_dp(dp, cfg, mgr)

    bot = Bot(token=_FAKE_TOKEN)

    # Patch Message.answer at class level to avoid real HTTP calls.
    # When patched this way (not a descriptor), called as mock(text) without self.
    with patch("aiogram.types.Message.answer", new_callable=AsyncMock) as mock_answer, \
         patch("aiogram.Bot.send_chat_action", new_callable=AsyncMock):
        mock_answer.return_value = MagicMock(message_id=1)
        await dp.feed_update(bot, _make_update(_ALLOWED_ID, text="hi"))

    mock_answer.assert_awaited()
    texts = [str(call.args[0]) for call in mock_answer.call_args_list]
    assert any("✅ Response:" in t and "OK" in t for t in texts)
