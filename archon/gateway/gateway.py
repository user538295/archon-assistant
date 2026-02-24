"""Gateway — orchestrates bot, session manager, and routing in a single asyncio loop."""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher

from archon.ai.history_manager import HistoryManager
from archon.ai.plugin_loader import PluginLoader
from archon.ai.session_manager import SessionManager
from archon.ai.skill_loader import SkillLoader
from archon.ai.truncation import SplitStrategy, TruncationStrategy
from archon.chat.bot import create_bot, create_dispatcher, setup_bot_commands
from archon.chat.handler import handle_message
from archon.chat.middleware import WhitelistMiddleware
from archon.config.loader import Config, ConfigError
from archon.log_setup import setup_logging

logger = logging.getLogger("archon")

_SHUTDOWN_TIMEOUT: float = 5.0


def register_middleware(dp: Dispatcher, allowed_user_ids: list[int]) -> None:
    """Register WhitelistMiddleware on message and callback_query routers."""
    mw = WhitelistMiddleware(allowed_user_ids=allowed_user_ids)
    dp.message.middleware(mw)
    dp.callback_query.middleware(mw)


def _make_truncation(strategy: str) -> TruncationStrategy:
    if strategy == "split":
        return SplitStrategy()
    raise ConfigError(f"Unknown truncation_strategy: {strategy!r}")


def _setup_dp(
    dp: Dispatcher,
    cfg: Config,
    session_manager: SessionManager,
    skill_loader: SkillLoader | None = None,
    plugin_loader: PluginLoader | None = None,
    config_file: str = "config.toml",
) -> None:
    """Wire middleware, handlers, and data dependencies onto the dispatcher."""
    register_middleware(dp, cfg.access.allowed_user_ids)
    dp["session_manager"] = session_manager
    dp["skill_loader"] = skill_loader if skill_loader is not None else SkillLoader()
    dp["plugin_loader"] = plugin_loader
    dp["truncation"] = _make_truncation(cfg.output.truncation_strategy)
    dp["max_len"] = cfg.output.max_message_length
    dp["cwd"] = cfg.session.working_directory
    dp["notifications"] = cfg.notifications
    dp["config_file"] = config_file
    dp["models_config"] = cfg.models
    dp["history_manager"] = HistoryManager(cfg.history.directory) if cfg.history.enabled else None
    dp.message.register(handle_message)


async def _notify_restart(bot: Bot, chat_id: int) -> None:
    """Send the post-restart confirmation message to *chat_id*.

    Swallows all exceptions so that a transient Telegram error cannot prevent
    the bot from starting. Logs the full traceback at WARNING so failures are
    still visible in the logs.
    """
    try:
        await bot.send_message(chat_id, "✅ Restarted. Archon ready.")
        logger.info("Restart notification sent to chat %d", chat_id)
    except Exception:
        logger.warning(
            "Failed to send restart notification to chat %d",
            chat_id,
            exc_info=True,
        )


def _register_restart_notification(dp: Dispatcher, restart_chat_id: str | None) -> None:
    """Register a startup hook on *dp* that sends the restart confirmation.

    The hook fires inside ``dp.start_polling`` once the bot session is live,
    which avoids calling ``bot.send_message`` before the aiohttp session has
    been initialised.

    Does nothing when *restart_chat_id* is ``None``.
    """
    if not restart_chat_id:
        return
    chat_id = int(restart_chat_id)

    async def _startup_hook(bot: Bot, **_: object) -> None:
        await _notify_restart(bot, chat_id)

    dp.startup.register(_startup_hook)


class Gateway:
    """Orchestrator — wires the Telegram bot and session manager together."""

    @classmethod
    def start(cls) -> None:
        """Synchronous entry point called from main.py."""
        asyncio.run(cls._run())

    @classmethod
    async def _run(cls) -> None:
        from archon.config.loader import load_config

        config_file = "config.toml"
        cfg = load_config(config_file=config_file)
        setup_logging(cfg.logging)
        logger.info("Archon gateway starting")

        skill_loader = SkillLoader()
        plugin_loader: PluginLoader | None = (
            PluginLoader(
                plugins_dir=cfg.plugins.plugins_dir or None,
                settings_path=cfg.plugins.settings_path or None,
            )
            if cfg.plugins.enabled
            else None
        )
        if plugin_loader is not None:
            plugin_loader.load_all()  # eager load so warnings appear at startup
        session_manager = SessionManager(
            timeout=cfg.session.inactivity_timeout_seconds,
            cwd=cfg.session.working_directory,
            skill_loader=skill_loader,
            plugin_loader=plugin_loader,
        )
        if cfg.models.default:
            session_manager.set_model(cfg.models.default)
            logger.info("Default model set to %s from config", cfg.models.default)
        bot = create_bot(cfg.telegram_bot_token)
        dp = create_dispatcher()
        _setup_dp(dp, cfg, session_manager, skill_loader, plugin_loader, config_file)

        dp.startup.register(setup_bot_commands)
        _register_restart_notification(dp, os.environ.pop("ARCHON_RESTART_NOTIFY_CHAT_ID", None))

        try:
            logger.info("Bot polling started")
            await dp.start_polling(bot)
        finally:
            logger.info("Archon shutdown initiated")
            try:
                await asyncio.wait_for(session_manager.stop_all(), timeout=_SHUTDOWN_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("Session cleanup timed out after %.0fs", _SHUTDOWN_TIMEOUT)
            await bot.session.close()
            logger.info("Archon shutdown complete")
