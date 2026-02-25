"""Tests for gateway wiring — H3 + S3.1."""
import asyncio
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, Update, User

from archon.ai.event_mapper import Response
from archon.ai.session_manager import SessionManager
from archon.chat.bot import create_dispatcher
from archon.chat.middleware import WhitelistMiddleware
from archon.ai.history_manager import HistoryManager
from archon.config.loader import AccessConfig, Config, HistoryConfig, LoggingConfig, ModelsConfig, OutputConfig, PluginsConfig, SessionConfig
from archon.gateway.gateway import _notify_restart, _register_restart_notification, _setup_dp, register_middleware

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


def test_setup_dp_injects_notifications_from_config() -> None:
    cfg = _make_config()
    dp = create_dispatcher()
    _setup_dp(dp, cfg, _mock_session_manager())
    assert dp["notifications"] is cfg.notifications


def test_setup_dp_injects_config_file() -> None:
    cfg = _make_config()
    dp = create_dispatcher()
    _setup_dp(dp, cfg, _mock_session_manager(), config_file="/tmp/config.toml")
    assert dp["config_file"] == "/tmp/config.toml"


def test_setup_dp_injects_history_manager_when_enabled(tmp_path) -> None:
    cfg = _make_config()
    cfg.history = HistoryConfig(enabled=True, directory=str(tmp_path / "history"))
    dp = create_dispatcher()
    _setup_dp(dp, cfg, _mock_session_manager())
    assert isinstance(dp["history_manager"], HistoryManager)


def test_setup_dp_injects_none_history_manager_when_disabled(tmp_path) -> None:
    cfg = _make_config()
    cfg.history = HistoryConfig(enabled=False, directory=str(tmp_path / "history"))
    dp = create_dispatcher()
    _setup_dp(dp, cfg, _mock_session_manager())
    assert dp["history_manager"] is None


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


# ──────────────────────────────────────────────────────────────────
# _notify_restart — sends confirmation, swallows errors
# ──────────────────────────────────────────────────────────────────


async def test_notify_restart_sends_correct_message() -> None:
    """_notify_restart must call bot.send_message with the right chat_id and text."""
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()

    await _notify_restart(bot, 42)

    bot.send_message.assert_awaited_once_with(42, "✅ Restarted. Archon ready.")


async def test_notify_restart_does_not_raise_on_send_failure() -> None:
    """_notify_restart must swallow exceptions so a Telegram error cannot crash startup."""
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock(side_effect=Exception("API down"))

    await _notify_restart(bot, 99)  # must not raise


async def test_notify_restart_logs_warning_with_exc_info_on_failure() -> None:
    """Failed send must be logged at WARNING level with exc_info so the traceback is visible."""
    import logging

    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock(side_effect=RuntimeError("boom"))

    with patch("archon.gateway.gateway.logger") as mock_logger:
        await _notify_restart(bot, 7)

    mock_logger.warning.assert_called_once()
    call_kwargs = mock_logger.warning.call_args[1]
    assert call_kwargs.get("exc_info") is True


# ──────────────────────────────────────────────────────────────────
# _register_restart_notification — startup-hook registration
# ──────────────────────────────────────────────────────────────────


def test_register_restart_notification_adds_startup_hook_when_chat_id_given() -> None:
    """A startup hook must be registered on dp when a chat ID string is provided."""
    dp = Dispatcher()
    before = len(dp.startup.handlers)

    _register_restart_notification(dp, "12345")

    assert len(dp.startup.handlers) == before + 1


def test_register_restart_notification_no_hook_when_chat_id_is_none() -> None:
    """No startup hook must be registered when restart_chat_id is None."""
    dp = Dispatcher()
    before = len(dp.startup.handlers)

    _register_restart_notification(dp, None)

    assert len(dp.startup.handlers) == before


async def test_register_restart_notification_hook_sends_message_on_startup() -> None:
    """The registered startup hook must send the confirmation when the bot starts."""
    dp = Dispatcher()
    _register_restart_notification(dp, "55")

    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()

    await dp.startup.trigger(bot)

    bot.send_message.assert_awaited_once_with(55, "✅ Restarted. Archon ready.")


async def test_register_restart_notification_hook_uses_integer_chat_id() -> None:
    """The chat ID stored in the env var (a string) must be converted to int for send_message."""
    dp = Dispatcher()
    _register_restart_notification(dp, "999")

    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()

    await dp.startup.trigger(bot)

    chat_id_used = bot.send_message.call_args[0][0]
    assert isinstance(chat_id_used, int)
    assert chat_id_used == 999


# ──────────────────────────────────────────────────────────────────
# _make_truncation — unknown strategy raises ConfigError (High gap)
# ──────────────────────────────────────────────────────────────────


def test_make_truncation_unknown_strategy_raises_config_error() -> None:
    from archon.config.loader import ConfigError
    from archon.gateway.gateway import _make_truncation

    with pytest.raises(ConfigError, match="Unknown truncation_strategy"):
        _make_truncation("headtail")


def test_make_truncation_split_returns_split_strategy() -> None:
    from archon.ai.truncation import SplitStrategy
    from archon.gateway.gateway import _make_truncation

    result = _make_truncation("split")
    assert isinstance(result, SplitStrategy)


# ──────────────────────────────────────────────────────────────────
# _setup_dp — skill_loader injection (Medium gap)
# ──────────────────────────────────────────────────────────────────


def test_setup_dp_with_provided_skill_loader_injects_it() -> None:
    """When a skill_loader is explicitly passed, it must end up in dp['skill_loader']."""
    from archon.ai.skill_loader import SkillLoader

    cfg = _make_config()
    dp = create_dispatcher()
    custom_loader = MagicMock(spec=SkillLoader)
    _setup_dp(dp, cfg, _mock_session_manager(), skill_loader=custom_loader)

    assert dp["skill_loader"] is custom_loader


def test_setup_dp_with_none_skill_loader_creates_default() -> None:
    """When skill_loader=None is passed, a default SkillLoader() must be injected."""
    from archon.ai.skill_loader import SkillLoader

    cfg = _make_config()
    dp = create_dispatcher()
    _setup_dp(dp, cfg, _mock_session_manager(), skill_loader=None)

    assert isinstance(dp["skill_loader"], SkillLoader)


def test_setup_dp_injects_plugin_loader_when_provided() -> None:
    """When plugin_loader is provided, it must be set in dp['plugin_loader']."""
    from archon.ai.plugin_loader import PluginLoader

    cfg = _make_config()
    dp = create_dispatcher()
    mock_plugin_loader = MagicMock(spec=PluginLoader)
    _setup_dp(dp, cfg, _mock_session_manager(), plugin_loader=mock_plugin_loader)

    assert dp["plugin_loader"] is mock_plugin_loader


# ──────────────────────────────────────────────────────────────────
# Gateway._run() — default model set at startup (High gap)
# ──────────────────────────────────────────────────────────────────


async def test_run_with_default_model_calls_set_model() -> None:
    """_run() must call session_manager.set_model(cfg.models.default) when configured."""
    from archon.gateway.gateway import Gateway

    cfg = _make_config()
    cfg.models = ModelsConfig(available=[], default="claude-opus-4-5")
    cfg.plugins = PluginsConfig(enabled=False)

    mock_sm = MagicMock(spec=SessionManager)
    mock_sm.set_model = MagicMock()
    mock_sm.stop_all = AsyncMock()

    mock_bot = MagicMock()
    mock_bot.session = MagicMock()
    mock_bot.session.close = AsyncMock()

    mock_dp = MagicMock()
    mock_dp.startup = MagicMock()
    mock_dp.startup.register = MagicMock()
    mock_dp.start_polling = AsyncMock()

    with patch("archon.config.loader.load_config", return_value=cfg), \
         patch("archon.gateway.gateway.setup_logging"), \
         patch("archon.gateway.gateway.SkillLoader"), \
         patch("archon.gateway.gateway.PluginLoader"), \
         patch("archon.gateway.gateway.SessionManager", return_value=mock_sm), \
         patch("archon.gateway.gateway.create_bot", return_value=mock_bot), \
         patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp), \
         patch("archon.gateway.gateway._setup_dp"), \
         patch("archon.gateway.gateway._register_restart_notification"):
        await Gateway._run()

    mock_sm.set_model.assert_called_once_with("claude-opus-4-5")


async def test_run_without_default_model_does_not_call_set_model() -> None:
    """_run() must NOT call set_model when cfg.models.default is None."""
    from archon.gateway.gateway import Gateway

    cfg = _make_config()
    cfg.models = ModelsConfig(available=[], default=None)
    cfg.plugins = PluginsConfig(enabled=False)

    mock_sm = MagicMock(spec=SessionManager)
    mock_sm.set_model = MagicMock()
    mock_sm.stop_all = AsyncMock()

    mock_bot = MagicMock()
    mock_bot.session = MagicMock()
    mock_bot.session.close = AsyncMock()

    mock_dp = MagicMock()
    mock_dp.startup = MagicMock()
    mock_dp.startup.register = MagicMock()
    mock_dp.start_polling = AsyncMock()

    with patch("archon.config.loader.load_config", return_value=cfg), \
         patch("archon.gateway.gateway.setup_logging"), \
         patch("archon.gateway.gateway.SkillLoader"), \
         patch("archon.gateway.gateway.PluginLoader"), \
         patch("archon.gateway.gateway.SessionManager", return_value=mock_sm), \
         patch("archon.gateway.gateway.create_bot", return_value=mock_bot), \
         patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp), \
         patch("archon.gateway.gateway._setup_dp"), \
         patch("archon.gateway.gateway._register_restart_notification"):
        await Gateway._run()

    mock_sm.set_model.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# Gateway._run() — plugins disabled path (Medium gap)
# ──────────────────────────────────────────────────────────────────


async def test_run_with_plugins_disabled_does_not_instantiate_plugin_loader() -> None:
    """When cfg.plugins.enabled=False, PluginLoader must never be instantiated."""
    from archon.gateway.gateway import Gateway

    cfg = _make_config()
    cfg.models = ModelsConfig()
    cfg.plugins = PluginsConfig(enabled=False)

    mock_sm = MagicMock(spec=SessionManager)
    mock_sm.stop_all = AsyncMock()

    mock_bot = MagicMock()
    mock_bot.session = MagicMock()
    mock_bot.session.close = AsyncMock()

    mock_dp = MagicMock()
    mock_dp.startup = MagicMock()
    mock_dp.startup.register = MagicMock()
    mock_dp.start_polling = AsyncMock()

    with patch("archon.config.loader.load_config", return_value=cfg), \
         patch("archon.gateway.gateway.setup_logging"), \
         patch("archon.gateway.gateway.SkillLoader"), \
         patch("archon.gateway.gateway.PluginLoader") as MockPluginLoader, \
         patch("archon.gateway.gateway.SessionManager", return_value=mock_sm), \
         patch("archon.gateway.gateway.create_bot", return_value=mock_bot), \
         patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp), \
         patch("archon.gateway.gateway._setup_dp"), \
         patch("archon.gateway.gateway._register_restart_notification"):
        await Gateway._run()

    MockPluginLoader.assert_not_called()


async def test_run_with_plugins_disabled_passes_none_to_setup_dp() -> None:
    """When plugins are disabled, _setup_dp must receive plugin_loader=None."""
    from archon.gateway.gateway import Gateway

    cfg = _make_config()
    cfg.models = ModelsConfig()
    cfg.plugins = PluginsConfig(enabled=False)

    mock_sm = MagicMock(spec=SessionManager)
    mock_sm.stop_all = AsyncMock()

    mock_bot = MagicMock()
    mock_bot.session = MagicMock()
    mock_bot.session.close = AsyncMock()

    mock_dp = MagicMock()
    mock_dp.startup = MagicMock()
    mock_dp.startup.register = MagicMock()
    mock_dp.start_polling = AsyncMock()

    captured: list[tuple] = []

    def _capture(*args: object, **kwargs: object) -> None:
        captured.append((args, kwargs))

    with patch("archon.config.loader.load_config", return_value=cfg), \
         patch("archon.gateway.gateway.setup_logging"), \
         patch("archon.gateway.gateway.SkillLoader"), \
         patch("archon.gateway.gateway.PluginLoader"), \
         patch("archon.gateway.gateway.SessionManager", return_value=mock_sm), \
         patch("archon.gateway.gateway.create_bot", return_value=mock_bot), \
         patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp), \
         patch("archon.gateway.gateway._setup_dp", side_effect=_capture), \
         patch("archon.gateway.gateway._register_restart_notification"):
        await Gateway._run()

    # _setup_dp(dp, cfg, session_manager, skill_loader, plugin_loader, config_file)
    # plugin_loader is args[4]
    assert len(captured) == 1
    plugin_loader_arg = captured[0][0][4]
    assert plugin_loader_arg is None


# ──────────────────────────────────────────────────────────────────
# _stuck_monitor — stuck session notification
# ──────────────────────────────────────────────────────────────────


async def test_stuck_monitor_notifies_user_when_stuck() -> None:
    """_stuck_monitor must send a message to the user when their session is stuck."""
    from archon.gateway.gateway import _stuck_monitor

    session_manager = MagicMock(spec=SessionManager)
    session_manager.stuck_sessions = MagicMock(return_value=[42])
    session_manager.processing_sessions = MagicMock(return_value={42: 130.0})

    bot = MagicMock()
    bot.send_message = AsyncMock()

    task = asyncio.create_task(_stuck_monitor(session_manager, bot, poll_interval=0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    bot.send_message.assert_awaited_once_with(42, "⏳ Agent is still working... (2 min elapsed)")


async def test_stuck_monitor_does_not_notify_same_session_twice() -> None:
    """_stuck_monitor must NOT send duplicate notifications for the same stuck episode."""
    from archon.gateway.gateway import _stuck_monitor

    session_manager = MagicMock(spec=SessionManager)
    session_manager.stuck_sessions = MagicMock(return_value=[7])
    session_manager.processing_sessions = MagicMock(return_value={7: 150.0})

    bot = MagicMock()
    bot.send_message = AsyncMock()

    # Run two poll cycles
    task = asyncio.create_task(_stuck_monitor(session_manager, bot, poll_interval=0.01))
    await asyncio.sleep(0.04)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Despite multiple poll cycles, only one notification for the same episode
    assert bot.send_message.await_count == 1


async def test_stuck_monitor_re_notifies_after_recovery() -> None:
    """_stuck_monitor must re-notify if a session recovers then gets stuck again."""
    from archon.gateway.gateway import _stuck_monitor

    call_count = 0

    def _stuck_sessions(threshold: float) -> list[int]:
        nonlocal call_count
        call_count += 1
        # 1st poll: stuck; 2nd poll: clear; 3rd poll: stuck again
        if call_count in (1, 3):
            return [5]
        return []

    session_manager = MagicMock(spec=SessionManager)
    session_manager.stuck_sessions = MagicMock(side_effect=_stuck_sessions)
    session_manager.processing_sessions = MagicMock(return_value={5: 125.0})

    bot = MagicMock()
    bot.send_message = AsyncMock()

    # Run three poll cycles (3 × 0.01s + buffer)
    task = asyncio.create_task(_stuck_monitor(session_manager, bot, poll_interval=0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Two notifications: one per stuck episode
    assert bot.send_message.await_count == 2


async def test_stuck_monitor_swallows_send_failure() -> None:
    """_stuck_monitor must NOT crash when bot.send_message raises."""
    from archon.gateway.gateway import _stuck_monitor

    session_manager = MagicMock(spec=SessionManager)
    session_manager.stuck_sessions = MagicMock(return_value=[99])
    session_manager.processing_sessions = MagicMock(return_value={99: 200.0})

    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=Exception("Telegram down"))

    task = asyncio.create_task(_stuck_monitor(session_manager, bot, poll_interval=0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # No crash — exception was swallowed


async def test_stuck_monitor_no_notification_when_no_stuck_sessions() -> None:
    """_stuck_monitor must not send anything when no sessions are stuck."""
    from archon.gateway.gateway import _stuck_monitor

    session_manager = MagicMock(spec=SessionManager)
    session_manager.stuck_sessions = MagicMock(return_value=[])
    session_manager.processing_sessions = MagicMock(return_value={})

    bot = MagicMock()
    bot.send_message = AsyncMock()

    task = asyncio.create_task(_stuck_monitor(session_manager, bot, poll_interval=0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    bot.send_message.assert_not_called()
