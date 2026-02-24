"""Gateway — orchestrates bot, session manager, and routing in a single asyncio loop."""
import asyncio
import contextlib
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher

from archon.ai.agent_loader import AgentLoader
from archon.ai.cron_scheduler import CronScheduler
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
_STUCK_THRESHOLD_SECS: float = 120.0
_STUCK_POLL_INTERVAL_SECS: float = 30.0
_QMD_DAEMON_STARTUP_WAIT: float = 2.0  # seconds to wait after launching daemon


async def _ensure_qmd_daemon(host: str, port: int) -> bool:
    """Ensure the QMD MCP daemon is reachable at *host*:*port*.

    For ``host == "localhost"`` (the default): checks the PID file and starts
    the daemon via ``qmd mcp --http --daemon`` if it is not already running.

    For remote hosts: skips the start attempt (cannot manage remote processes)
    and returns True unconditionally so the caller can attempt the connection.

    Returns True if daemon is confirmed running (or assumed running for remote
    hosts), False on local startup failure.
    Failure is logged as a warning — Archon continues without QMD rather than
    refusing to start.  The daemon is intentionally NOT stopped at shutdown;
    it is a user-owned process that may serve other tools beyond Archon.
    """
    import shutil
    from pathlib import Path

    if host not in ("localhost", "127.0.0.1"):
        # Remote host — assume the user manages the daemon themselves.
        logger.info("QMD daemon host is %s — skipping local start; assuming it is running", host)
        return True

    if not shutil.which("qmd"):
        logger.warning("QMD enabled in config but 'qmd' not found in PATH — disabling QMD")
        return False

    pid_file = Path.home() / ".cache" / "qmd" / "mcp.pid"

    # Check if daemon is already alive
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            # os.kill(pid, 0) raises OSError if process is dead
            os.kill(pid, 0)
            logger.info("QMD daemon already running (PID %d, port %d)", pid, port)
            return True
        except (ValueError, OSError):
            logger.info("QMD daemon PID file stale — will restart daemon")

    # Start daemon
    logger.info("Starting QMD MCP daemon on port %d...", port)
    try:
        proc = await asyncio.create_subprocess_exec(
            "qmd", "mcp", "--http", "--port", str(port), "--daemon",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        if proc.returncode != 0:
            err = stderr.decode().strip() if stderr else "(no output)"
            logger.warning("QMD daemon failed to start (exit %d): %s — disabling QMD", proc.returncode, err)
            return False
    except asyncio.TimeoutError:
        logger.warning("QMD daemon start timed out — disabling QMD")
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("QMD daemon start error: %s — disabling QMD", exc)
        return False

    # Give the daemon a moment to write its PID file and open the port
    await asyncio.sleep(_QMD_DAEMON_STARTUP_WAIT)

    # Verify it's alive
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            logger.info("QMD daemon started successfully (PID %d, port %d)", pid, port)
            return True
        except (ValueError, OSError):
            pass

    logger.warning("QMD daemon started but PID file not found — disabling QMD")
    return False


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
    agent_loader: AgentLoader | None = None,
    config_file: str = "config.toml",
    cron_scheduler: CronScheduler | None = None,
) -> None:
    """Wire middleware, handlers, and data dependencies onto the dispatcher."""
    register_middleware(dp, cfg.access.allowed_user_ids)
    dp["session_manager"] = session_manager
    dp["skill_loader"] = skill_loader if skill_loader is not None else SkillLoader()
    dp["plugin_loader"] = plugin_loader
    dp["agent_loader"] = agent_loader
    dp["truncation"] = _make_truncation(cfg.output.truncation_strategy)
    dp["max_len"] = cfg.output.max_message_length
    dp["cwd"] = cfg.session.working_directory
    dp["notifications"] = cfg.notifications
    dp["config_file"] = config_file
    dp["models_config"] = cfg.models
    dp["history_manager"] = HistoryManager(cfg.history.directory) if cfg.history.enabled else None
    dp["cron_scheduler"] = cron_scheduler
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


async def _stuck_monitor(
    session_manager: SessionManager,
    bot: Bot,
    poll_interval: float = _STUCK_POLL_INTERVAL_SECS,
    threshold: float = _STUCK_THRESHOLD_SECS,
) -> None:
    """Periodically notify users when their session has been stuck longer than threshold.

    Sends a single notification per stuck episode; re-notifies if the session
    recovers and gets stuck again.
    """
    alerted: set[int] = set()
    while True:
        await asyncio.sleep(poll_interval)
        try:
            stuck = set(session_manager.stuck_sessions(threshold))
            # Clear alerts for sessions that are no longer stuck
            alerted &= stuck
            # Notify newly stuck sessions (first time only per episode)
            for user_id in stuck - alerted:
                alerted.add(user_id)
                processing = session_manager.processing_sessions()
                elapsed_secs = processing.get(user_id, 0.0)
                mins = int(elapsed_secs // 60)
                try:
                    await bot.send_message(
                        user_id,
                        f"⏳ Agent is still working... ({mins} min elapsed)",
                    )
                    logger.info("Stuck notification sent to user %d (%d min)", user_id, mins)
                except Exception:
                    logger.warning(
                        "Failed to send stuck notification to user %d",
                        user_id,
                        exc_info=True,
                    )
        except Exception:
            logger.warning("Stuck monitor iteration error", exc_info=True)


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
        agent_loader = AgentLoader()
        agent_loader.load_all()  # eager load so warnings appear at startup

        # QMD: ensure daemon is running before sessions start.
        # qmd_url is None when QMD is disabled or fails to start; set to the
        # full MCP endpoint URL otherwise (built once here from config host+port).
        qmd_url: str | None = None
        if cfg.qmd.enabled:
            daemon_ok = await _ensure_qmd_daemon(cfg.qmd.host, cfg.qmd.port)
            if daemon_ok:
                qmd_url = f"http://{cfg.qmd.host}:{cfg.qmd.port}/mcp"
                logger.info("QMD MCP endpoint: %s", qmd_url)
            else:
                logger.warning("QMD integration disabled for this session")

        session_manager = SessionManager(
            timeout=cfg.session.inactivity_timeout_seconds,
            cwd=cfg.session.working_directory,
            skill_loader=skill_loader,
            plugin_loader=plugin_loader,
            agent_loader=agent_loader,
            qmd_url=qmd_url,
        )
        if cfg.models.default:
            session_manager.set_model(cfg.models.default)
            logger.info("Default model set to %s from config", cfg.models.default)
        bot = create_bot(cfg.telegram_bot_token)
        dp = create_dispatcher()
        cron_scheduler = CronScheduler(
            config=cfg.cron,
            bot=bot,
            model=cfg.models.default or None,
            jobs_dir_base=Path(config_file).parent,
        )
        _setup_dp(dp, cfg, session_manager, skill_loader, plugin_loader, agent_loader, config_file, cron_scheduler)

        dp.startup.register(setup_bot_commands)
        _register_restart_notification(dp, os.environ.pop("ARCHON_RESTART_NOTIFY_CHAT_ID", None))

        await cron_scheduler.start()
        stuck_task = asyncio.create_task(_stuck_monitor(session_manager, bot))
        try:
            logger.info("Bot polling started")
            await dp.start_polling(bot)
        finally:
            stuck_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stuck_task
            logger.info("Archon shutdown initiated")
            await cron_scheduler.stop()
            try:
                await asyncio.wait_for(session_manager.stop_all(), timeout=_SHUTDOWN_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("Session cleanup timed out after %.0fs", _SHUTDOWN_TIMEOUT)
            await bot.session.close()
            logger.info("Archon shutdown complete")
