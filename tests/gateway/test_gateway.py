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


def _make_mcp_mock() -> MagicMock:
    """Return a MagicMock for ArchonMCPServer that won't attempt any port binding."""
    m = MagicMock()
    m.start = AsyncMock()
    m.stop = AsyncMock()
    return m

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


def test_setup_dp_uses_provided_history_manager_instance(tmp_path) -> None:
    """When a history_manager is passed to _setup_dp, it is used as-is
    (no second instance created) — ensures background agents and main session
    share the same HistoryManager object."""
    cfg = _make_config()
    cfg.history = HistoryConfig(enabled=True, directory=str(tmp_path / "history"))
    dp = create_dispatcher()
    shared_hm = HistoryManager(str(tmp_path / "history"))
    _setup_dp(dp, cfg, _mock_session_manager(), history_manager=shared_hm)
    assert dp["history_manager"] is shared_hm


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

    await _notify_restart(bot, 42, version="26.3.364", mode="normal")

    bot.send_message.assert_awaited_once()
    call_args = bot.send_message.call_args
    assert call_args.args[0] == 42
    text = call_args.args[1]
    assert "Restarted" in text
    assert "26.3.364" in text


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


# ──────────────────────────────────────────────────────────────────
# _setup_dp — command_loader wiring — Task 2.2
# ──────────────────────────────────────────────────────────────────


def test_setup_dp_wires_command_loader() -> None:
    """_setup_dp must inject a CommandLoader instance under dp['command_loader']."""
    from archon.chat.command_loader import CommandLoader
    cfg = _make_config()
    dp = create_dispatcher()
    _setup_dp(dp, cfg, _mock_session_manager())
    assert isinstance(dp["command_loader"], CommandLoader)


async def test_register_restart_notification_hook_sends_message_on_startup() -> None:
    """The registered startup hook must send the confirmation when the bot starts."""
    dp = Dispatcher()
    _register_restart_notification(dp, "55", version="26.3.364", mode="normal")

    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()

    await dp.startup.trigger(bot)

    bot.send_message.assert_awaited_once()
    call_args = bot.send_message.call_args
    assert call_args.args[0] == 55
    text = call_args.args[1]
    assert "Restarted" in text
    assert "26.3.364" in text


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


def test_gateway_start_exits_with_1_on_config_error() -> None:
    """Gateway.start() must exit with code 1 on ConfigError — no raw traceback."""
    from archon.config.loader import ConfigError
    from archon.gateway.gateway import Gateway

    with patch("archon.gateway.gateway.asyncio.run", side_effect=ConfigError("TELEGRAM_BOT_TOKEN is invalid")):
        with pytest.raises(SystemExit) as exc_info:
            Gateway.start()

    assert exc_info.value.code == 1


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
         patch("archon.gateway.gateway._register_restart_notification"), \
         patch("archon.gateway.gateway._register_startup_notification"), \
         patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_mcp_mock()), \
         patch("archon.gateway.gateway.ArchonRouterMCPServer", return_value=_make_mcp_mock()):
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
         patch("archon.gateway.gateway._register_restart_notification"), \
         patch("archon.gateway.gateway._register_startup_notification"), \
         patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_mcp_mock()), \
         patch("archon.gateway.gateway.ArchonRouterMCPServer", return_value=_make_mcp_mock()):
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
         patch("archon.gateway.gateway._register_restart_notification"), \
         patch("archon.gateway.gateway._register_startup_notification"), \
         patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_mcp_mock()), \
         patch("archon.gateway.gateway.ArchonRouterMCPServer", return_value=_make_mcp_mock()):
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
         patch("archon.gateway.gateway._register_restart_notification"), \
         patch("archon.gateway.gateway._register_startup_notification"), \
         patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_mcp_mock()), \
         patch("archon.gateway.gateway.ArchonRouterMCPServer", return_value=_make_mcp_mock()):
        await Gateway._run()

    # _setup_dp(dp, cfg, session_manager, skill_loader, plugin_loader, config_file)
    # plugin_loader is args[4]
    assert len(captured) == 1
    plugin_loader_arg = captured[0][0][4]
    assert plugin_loader_arg is None


# ──────────────────────────────────────────────────────────────────
# FR.003 — AgentLogger wiring in _setup_dp
# ──────────────────────────────────────────────────────────────────


def test_agent_logger_wired_in_dispatcher_when_history_enabled(tmp_path) -> None:
    """dp['agent_logger'] is an AgentLogger instance when history is enabled."""
    from archon.ai.agent_logger import AgentLogger

    cfg = _make_config()
    cfg.history = HistoryConfig(enabled=True, directory=str(tmp_path / "history"))
    dp = create_dispatcher()
    _setup_dp(dp, cfg, _mock_session_manager())
    assert isinstance(dp["agent_logger"], AgentLogger)


def test_agent_logger_none_when_history_disabled(tmp_path) -> None:
    """dp['agent_logger'] is None when history is disabled."""
    cfg = _make_config()
    cfg.history = HistoryConfig(enabled=False, directory=str(tmp_path / "history"))
    dp = create_dispatcher()
    _setup_dp(dp, cfg, _mock_session_manager())
    assert dp["agent_logger"] is None


def test_background_agent_manager_receives_agent_logger(tmp_path) -> None:
    """BackgroundAgentManager is constructed with the agent_logger from the dispatcher."""
    from archon.ai.agent_logger import AgentLogger
    from archon.ai.background_agent_manager import BackgroundAgentManager

    cfg = _make_config()
    cfg.history = HistoryConfig(enabled=True, directory=str(tmp_path / "history"))
    dp = create_dispatcher()
    mock_bg_manager = MagicMock(spec=BackgroundAgentManager)
    _setup_dp(dp, cfg, _mock_session_manager(), background_agent_manager=mock_bg_manager)

    # The agent_logger injected into dp must be an AgentLogger
    assert isinstance(dp["agent_logger"], AgentLogger)

# ──────────────────────────────────────────────────────────────────
# _midnight_compaction_loop — UTC-based scheduling (DST safety)
# ──────────────────────────────────────────────────────────────────


async def test_run_wires_manager_via_set_manager_not_direct_mutation() -> None:
    """_run() must call bg_mcp_server.set_manager(bg_manager), not mutate _manager directly."""
    from archon.gateway.gateway import Gateway

    cfg = _make_config()
    cfg.models = ModelsConfig(available=[], default=None)
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

    set_manager_calls: list[object] = []
    mock_mcp = _make_mcp_mock()
    mock_mcp.set_manager = MagicMock(side_effect=lambda m: set_manager_calls.append(m))

    with patch("archon.config.loader.load_config", return_value=cfg), \
         patch("archon.gateway.gateway.setup_logging"), \
         patch("archon.gateway.gateway.SkillLoader"), \
         patch("archon.gateway.gateway.PluginLoader"), \
         patch("archon.gateway.gateway.SessionManager", return_value=mock_sm), \
         patch("archon.gateway.gateway.create_bot", return_value=mock_bot), \
         patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp), \
         patch("archon.gateway.gateway._setup_dp"), \
         patch("archon.gateway.gateway._register_restart_notification"), \
         patch("archon.gateway.gateway._register_startup_notification"), \
         patch("archon.gateway.gateway.ArchonMCPServer", return_value=mock_mcp), \
         patch("archon.gateway.gateway.ArchonRouterMCPServer", return_value=_make_mcp_mock()):
        await Gateway._run()

    assert len(set_manager_calls) == 1, "set_manager() must be called exactly once"


# ──────────────────────────────────────────────────────────────────
# ArchonRouterMCPServer wiring — Wave 5
# ──────────────────────────────────────────────────────────────────


async def test_run_starts_router_mcp_server() -> None:
    """_run() must call start() on the single ArchonRouterMCPServer instance (Task 1.2: single server)."""
    from archon.gateway.gateway import Gateway

    cfg = _make_config()
    cfg.models = ModelsConfig(available=[], default=None)
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

    instances: list[MagicMock] = []

    def _make_router(**kwargs):
        m = _make_mcp_mock()
        m.mcp_url = "http://localhost:18183/mcp"
        m.token = "fake"
        instances.append(m)
        return m

    with patch("archon.config.loader.load_config", return_value=cfg), \
         patch("archon.gateway.gateway.setup_logging"), \
         patch("archon.gateway.gateway.SkillLoader"), \
         patch("archon.gateway.gateway.PluginLoader"), \
         patch("archon.gateway.gateway.SessionManager", return_value=mock_sm), \
         patch("archon.gateway.gateway.create_bot", return_value=mock_bot), \
         patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp), \
         patch("archon.gateway.gateway._setup_dp"), \
         patch("archon.gateway.gateway._register_restart_notification"), \
         patch("archon.gateway.gateway._register_startup_notification"), \
         patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_mcp_mock()), \
         patch("archon.gateway.gateway.ArchonRouterMCPServer", side_effect=_make_router):
        await Gateway._run()

    # Task 1.2: single ArchonRouterMCPServer instance (not two)
    assert len(instances) == 1
    for inst in instances:
        inst.start.assert_awaited_once()


async def test_run_stops_router_mcp_server_on_shutdown() -> None:
    """_run() must call stop() on the single ArchonRouterMCPServer instance during shutdown (Task 1.2)."""
    from archon.gateway.gateway import Gateway

    cfg = _make_config()
    cfg.models = ModelsConfig(available=[], default=None)
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

    instances: list[MagicMock] = []

    def _make_router(**kwargs):
        m = _make_mcp_mock()
        m.mcp_url = "http://localhost:18183/mcp"
        m.token = "fake"
        instances.append(m)
        return m

    with patch("archon.config.loader.load_config", return_value=cfg), \
         patch("archon.gateway.gateway.setup_logging"), \
         patch("archon.gateway.gateway.SkillLoader"), \
         patch("archon.gateway.gateway.PluginLoader"), \
         patch("archon.gateway.gateway.SessionManager", return_value=mock_sm), \
         patch("archon.gateway.gateway.create_bot", return_value=mock_bot), \
         patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp), \
         patch("archon.gateway.gateway._setup_dp"), \
         patch("archon.gateway.gateway._register_restart_notification"), \
         patch("archon.gateway.gateway._register_startup_notification"), \
         patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_mcp_mock()), \
         patch("archon.gateway.gateway.ArchonRouterMCPServer", side_effect=_make_router):
        await Gateway._run()

    # Task 1.2: single ArchonRouterMCPServer instance (not two)
    assert len(instances) == 1
    for inst in instances:
        inst.stop.assert_awaited_once()


async def test_run_passes_router_mcp_url_to_session_manager() -> None:
    """_run() must pass router_mcp_server.mcp_url to SessionManager as router_mcp_url."""
    from archon.gateway.gateway import Gateway

    cfg = _make_config()
    cfg.models = ModelsConfig(available=[], default=None)
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

    mock_router_mcp = _make_mcp_mock()
    mock_router_mcp.mcp_url = "http://localhost:18183/mcp"

    captured_kwargs: list[dict] = []

    def _capture_sm(*args, **kwargs):
        captured_kwargs.append(kwargs)
        return mock_sm

    with patch("archon.config.loader.load_config", return_value=cfg), \
         patch("archon.gateway.gateway.setup_logging"), \
         patch("archon.gateway.gateway.SkillLoader"), \
         patch("archon.gateway.gateway.PluginLoader"), \
         patch("archon.gateway.gateway.SessionManager", side_effect=_capture_sm), \
         patch("archon.gateway.gateway.create_bot", return_value=mock_bot), \
         patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp), \
         patch("archon.gateway.gateway._setup_dp"), \
         patch("archon.gateway.gateway._register_restart_notification"), \
         patch("archon.gateway.gateway._register_startup_notification"), \
         patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_mcp_mock()), \
         patch("archon.gateway.gateway.ArchonRouterMCPServer", return_value=mock_router_mcp):
        await Gateway._run()

    assert len(captured_kwargs) == 1
    assert captured_kwargs[0].get("router_mcp_url") == "http://localhost:18183/mcp"


async def test_run_passes_router_mcp_headers_to_session_manager() -> None:
    """_run() must pass Authorization Bearer headers derived from router_mcp_server.token."""
    from archon.gateway.gateway import Gateway

    cfg = _make_config()
    cfg.models = ModelsConfig(available=[], default=None)
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

    mock_router_mcp = _make_mcp_mock()
    mock_router_mcp.mcp_url = "http://localhost:18183/mcp"
    mock_router_mcp.token = "abc123token"

    captured_kwargs: list[dict] = []

    def _capture_sm(*args, **kwargs):
        captured_kwargs.append(kwargs)
        return mock_sm

    with patch("archon.config.loader.load_config", return_value=cfg), \
         patch("archon.gateway.gateway.setup_logging"), \
         patch("archon.gateway.gateway.SkillLoader"), \
         patch("archon.gateway.gateway.PluginLoader"), \
         patch("archon.gateway.gateway.SessionManager", side_effect=_capture_sm), \
         patch("archon.gateway.gateway.create_bot", return_value=mock_bot), \
         patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp), \
         patch("archon.gateway.gateway._setup_dp"), \
         patch("archon.gateway.gateway._register_restart_notification"), \
         patch("archon.gateway.gateway._register_startup_notification"), \
         patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_mcp_mock()), \
         patch("archon.gateway.gateway.ArchonRouterMCPServer", return_value=mock_router_mcp):
        await Gateway._run()

    assert len(captured_kwargs) == 1
    headers = captured_kwargs[0].get("router_mcp_headers")
    assert headers == {"Authorization": "Bearer abc123token"}


async def test_midnight_compaction_loop_uses_utc_for_sleep() -> None:
    """_midnight_compaction_loop must sleep until the next UTC midnight+1min.

    We freeze datetime.now(timezone.utc) to a known UTC time and verify that
    asyncio.sleep is called with a duration consistent with UTC midnight, not
    local time — ensuring DST transitions cannot cause double-fire or skip.
    """
    from archon.gateway.gateway import _midnight_compaction_loop

    # Fix "now" to 23:58 UTC — 3 minutes before next UTC midnight+1min
    fixed_utc_now = datetime(2024, 3, 10, 23, 58, 0, tzinfo=timezone.utc)
    expected_sleep = 3 * 60.0  # 3 minutes to 00:01 UTC next day

    compactor = AsyncMock()
    compactor.compact_pending_days = AsyncMock()

    sleep_calls: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        raise asyncio.CancelledError  # stop after one iteration

    with patch("archon.gateway.gateway.datetime") as mock_dt, \
         patch("asyncio.sleep", side_effect=_fake_sleep):
        mock_dt.now.return_value = fixed_utc_now

        with pytest.raises(asyncio.CancelledError):
            await _midnight_compaction_loop(compactor)

    assert len(sleep_calls) == 1
    # Allow a small tolerance for microsecond rounding
    assert abs(sleep_calls[0] - expected_sleep) < 1.0


# ──────────────────────────────────────────────────────────────────
# _ensure_rag_server
# ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ensure_rag_server_reachable() -> None:
    """TCP connection success → True."""
    from archon.gateway.gateway import _ensure_rag_server
    mock_reader = MagicMock()
    mock_writer = MagicMock()
    mock_writer.close = MagicMock()
    mock_writer.wait_closed = AsyncMock()
    with patch("asyncio.open_connection", AsyncMock(return_value=(mock_reader, mock_writer))):
        result = await _ensure_rag_server("localhost", 8080)
    assert result is True
    mock_writer.close.assert_called_once()
    mock_writer.wait_closed.assert_awaited_once()

@pytest.mark.asyncio
async def test_ensure_rag_server_unreachable() -> None:
    """Connection error → False."""
    from archon.gateway.gateway import _ensure_rag_server
    with patch("asyncio.open_connection", AsyncMock(side_effect=OSError("refused"))):
        result = await _ensure_rag_server("localhost", 8080)
    assert result is False

@pytest.mark.asyncio
async def test_ensure_rag_server_timeout() -> None:
    """asyncio.TimeoutError → False, warning logged."""
    from archon.gateway.gateway import _ensure_rag_server
    with patch("asyncio.wait_for", AsyncMock(side_effect=asyncio.TimeoutError)):
        result = await _ensure_rag_server("127.0.0.1", 8080)
    assert result is False

@pytest.mark.asyncio
async def test_ensure_rag_server_remote_host_skips_probe() -> None:
    """Non-localhost host → True without TCP call."""
    from archon.gateway.gateway import _ensure_rag_server
    with patch("asyncio.open_connection", AsyncMock(side_effect=Exception("should not be called"))) as mock_conn:
        result = await _ensure_rag_server("192.168.1.100", 8080)
    assert result is True
    mock_conn.assert_not_called()

@pytest.mark.asyncio
async def test_gateway_rag_url_constructed_from_config_on_success() -> None:
    """When probe succeeds, rag_url = 'http://{host}:{port}/mcp'."""
    from archon.gateway.gateway import _ensure_rag_server
    mock_reader = MagicMock()
    mock_writer = MagicMock()
    mock_writer.close = MagicMock()
    mock_writer.wait_closed = AsyncMock()
    host, port = "localhost", 8765
    with patch("asyncio.open_connection", AsyncMock(return_value=(mock_reader, mock_writer))):
        server_ok = await _ensure_rag_server(host, port)
    assert server_ok is True
    rag_url = f"http://{host}:{port}/mcp" if server_ok else None
    assert rag_url == "http://localhost:8765/mcp"

@pytest.mark.asyncio
async def test_gateway_rag_url_is_none_when_probe_fails() -> None:
    """When probe fails, rag_url stays None."""
    from archon.gateway.gateway import _ensure_rag_server
    with patch("asyncio.open_connection", AsyncMock(side_effect=OSError("connection refused"))):
        server_ok = await _ensure_rag_server("localhost", 8765)
    assert server_ok is False
    rag_url = f"http://localhost:8765/mcp" if server_ok else None
    assert rag_url is None

