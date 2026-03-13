"""Gateway — orchestrates bot, session manager, and routing in a single asyncio loop."""
import asyncio
import logging
import os
import signal
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message

from archon.ai.agent_loader import AgentLoader
from archon.ai.agent_logger import AgentLogger
from archon.ai.archon_mcp_server import ArchonMCPServer
from archon.ai.archon_orch_mcp_server import ArchonOrchestratorMCPServer
from archon.ai.background_agent_manager import BackgroundAgentManager
from archon.ai.cron_scheduler import CronScheduler
from archon.ai.history_compactor import HistoryCompactor
from archon.ai.history_manager import HistoryManager
from archon.ai.plugin_loader import PluginLoader
from archon.ai.session_manager import SessionManager
from archon.ai.skill_loader import SkillLoader
from archon.ai.truncation import SplitStrategy, TruncationStrategy
from archon.ai.tts import TTSConfig
from archon.chat.bot import create_bot, create_dispatcher, setup_bot_commands
from archon.chat.handler import handle_message
from archon.chat.voice import VoiceMessageHandler
from archon.chat.middleware import WhitelistMiddleware
from archon.config.loader import Config, ConfigError
from archon.log_setup import setup_logging

logger = logging.getLogger("archon")

_SHUTDOWN_TIMEOUT: float = 5.0
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

    # Check if daemon is already alive via PID file
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            # os.kill(pid, 0) raises OSError if process is dead
            os.kill(pid, 0)
            logger.info("QMD daemon already running (PID %d, port %d)", pid, port)
            return True
        except (ValueError, OSError):
            logger.info("QMD daemon PID file stale — will check HTTP")

    # Fallback: HTTP probe (daemon may be running without a PID file)
    try:
        import urllib.request
        import urllib.error
        req = urllib.request.Request(
            f"http://{host}:{port}/mcp",
            method="GET",
            headers={"Accept": "text/event-stream"},
        )
        urllib.request.urlopen(req, timeout=2)
        logger.info("QMD daemon reachable at %s:%d (no PID file)", host, port)
        return True
    except urllib.error.HTTPError:
        # Any HTTP error means the server is responding
        logger.info("QMD daemon reachable at %s:%d (no PID file)", host, port)
        return True
    except Exception:
        logger.info("QMD daemon not reachable — will start daemon")

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
    background_agent_manager: BackgroundAgentManager | None = None,
    bg_mcp_server: ArchonMCPServer | None = None,
    history_manager: HistoryManager | None = None,
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
    _suppressed = frozenset(cfg.history.suppressed_tool_results)
    _history_manager = history_manager if history_manager is not None else (
        HistoryManager(cfg.history.directory, suppressed_tools=_suppressed) if cfg.history.enabled else None
    )
    _agent_logger = AgentLogger(cfg.history.directory, suppressed_tools=_suppressed) if cfg.history.enabled else None
    dp["history_manager"] = _history_manager
    dp["agent_logger"] = _agent_logger
    dp["cron_scheduler"] = cron_scheduler
    dp["background_agent_manager"] = background_agent_manager
    dp["bg_mcp_server"] = bg_mcp_server
    # Voice handlers MUST be registered BEFORE the generic text handler
    # so aiogram dispatches voice/audio messages to the voice handler first.
    if cfg.voice.enabled:
        tts_cfg = TTSConfig(
            provider=cfg.voice.tts.provider,  # type: ignore[arg-type]  # config str validated at load
            model=cfg.voice.tts.model,
            voice=cfg.voice.tts.voice,
            auto=cfg.voice.tts.auto,  # type: ignore[arg-type]  # config str validated at load
            max_text_length=cfg.voice.tts.max_text_length,
            edge_voice=cfg.voice.tts.edge_voice,
        )
        vmh = VoiceMessageHandler(
            session_manager=session_manager,
            stt_config={
                "model": cfg.voice.stt.model,
                "language": cfg.voice.stt.language,
            },
            tts_config=tts_cfg,
            truncation=_make_truncation(cfg.output.truncation_strategy),
            max_len=cfg.output.max_message_length,
            notifications=cfg.notifications,
            cwd=cfg.session.working_directory,
            history_manager=_history_manager,
            agent_logger=_agent_logger,
            background_agent_manager=background_agent_manager,
        )
        dp.message.register(vmh.handle_voice_message, F.voice)
        dp.message.register(vmh.handle_audio_message, F.audio)
        logger.info(
            "Voice enabled: STT=%s lang=%s, TTS=%s/%s auto=%s",
            cfg.voice.stt.model, cfg.voice.stt.language or "auto",
            cfg.voice.tts.provider, cfg.voice.tts.voice, cfg.voice.tts.auto,
        )

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


async def _midnight_compaction_loop(compactor: HistoryCompactor) -> None:
    """Run history compaction once per day at just past midnight."""
    while True:
        now = datetime.now(timezone.utc)
        next_midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=1, second=0, microsecond=0
        )
        await asyncio.sleep((next_midnight - now).total_seconds())
        try:
            await compactor.compact_pending_days()
        except Exception:
            logger.warning("Midnight history compaction failed", exc_info=True)


class Gateway:
    """Orchestrator — wires the Telegram bot and session manager together."""

    @classmethod
    def start(cls) -> None:
        """Synchronous entry point called from main.py."""
        asyncio.run(cls._run())

    @classmethod
    async def _run(cls) -> None:
        from archon.config.loader import load_config

        archon_home = Path.home() / ".archon"
        config_file = str(archon_home / "config.toml")
        cfg = load_config(
            env_file=str(archon_home / ".env"),
            config_file=config_file,
        )
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

        bot = create_bot(cfg.telegram_bot_token)

        # History compaction: compact past days at startup and schedule nightly runs.
        # Tasks are stored so the GC cannot collect them before they finish.
        history_compactor: HistoryCompactor | None = None
        _compaction_tasks: list[asyncio.Task[None]] = []
        if cfg.history.enabled and cfg.history.compaction_enabled:
            history_compactor = HistoryCompactor(
                history_dir=cfg.history.directory,
                context_days=cfg.history.context_days,
            )
            _compaction_tasks.append(
                asyncio.create_task(
                    history_compactor.compact_pending_days(),
                    name="history-compact-startup",
                )
            )
            _compaction_tasks.append(
                asyncio.create_task(
                    history_compactor.compact_today(),
                    name="history-compact-today",
                )
            )
            _compaction_tasks.append(
                asyncio.create_task(
                    _midnight_compaction_loop(history_compactor),
                    name="history-compact-midnight",
                )
            )

        # Background agents: build MCP server + manager before SessionManager so
        # the server object can be passed into the session factory.
        # Background agents: MCP server + manager always start unconditionally.
        # The Task tool is always disabled in the orchestrator (see ClaudeSession.start()),
        # so spawn_background_agent via MCP is the only route for sub-agent execution.
        # This ensures the orchestrator's send() turn always ends quickly and the user
        # can send new messages without waiting for a sub-agent to finish (Bug.005).
        bg_mcp_server = ArchonMCPServer(
            manager=None,
            host=cfg.background_agents.host,
            port=cfg.background_agents.port,
            allowed_user_ids=cfg.access.allowed_user_ids,
        )
        logger.info(
            "Background agents MCP server on port %d (spawn_rule=%r, max_parallel=%d)",
            cfg.background_agents.port,
            cfg.background_agents.spawn_rule,
            cfg.background_agents.max_parallel,
        )

        orch_mcp_server = ArchonOrchestratorMCPServer(
            history_root=cfg.history.directory,
            host="localhost",
            port=cfg.background_agents.orch_mcp_port,
        )

        session_manager = SessionManager(
            timeout=cfg.session.inactivity_timeout_seconds,
            cwd=cfg.session.working_directory,
            skill_loader=skill_loader,
            plugin_loader=plugin_loader,
            agent_loader=agent_loader,
            qmd_url=qmd_url,
            background_agent_mcp_server=bg_mcp_server,
            spawn_rule=cfg.background_agents.spawn_rule,
            history_compactor=history_compactor,
            reminder_config=cfg.reminder if cfg.reminder.enabled else None,
            tool_promotion_threshold=cfg.background_agents.tool_promotion_threshold,
            orch_mcp_url=orch_mcp_server.mcp_url,
            orch_mcp_headers={"Authorization": f"Bearer {orch_mcp_server.token}"},
        )
        if cfg.models.default:
            session_manager.set_model(cfg.models.default)
            logger.info("Default model set to %s from config", cfg.models.default)

        _suppressed = frozenset(cfg.history.suppressed_tool_results)
        bg_agent_logger = AgentLogger(cfg.history.directory, suppressed_tools=_suppressed) if cfg.history.enabled else None
        shared_history_manager = HistoryManager(cfg.history.directory, suppressed_tools=_suppressed) if cfg.history.enabled else None
        bg_manager = BackgroundAgentManager(
            bot=bot,
            session_manager=session_manager,
            max_parallel=cfg.background_agents.max_parallel,
            model=cfg.models.default,
            cwd=cfg.session.working_directory,
            qmd_url=qmd_url,
            agent_logger=bg_agent_logger,
            beacon_interval_minutes=cfg.background_agents.beacon_interval_minutes,
            history_manager=shared_history_manager,
        )
        # Wire manager into the MCP server via the public API (circular dependency resolved)
        bg_mcp_server.set_manager(bg_manager)

        dp = create_dispatcher()
        cron_scheduler = CronScheduler(
            config=cfg.cron,
            bot=bot,
            allowed_user_ids=cfg.access.allowed_user_ids,
            model=cfg.models.default,
            jobs_dir_base=Path(config_file).parent,
            cwd=cfg.session.working_directory,
        )
        _setup_dp(
            dp, cfg, session_manager, skill_loader, plugin_loader, agent_loader,
            config_file, cron_scheduler, bg_manager, bg_mcp_server,
            history_manager=shared_history_manager,
        )

        dp.startup.register(setup_bot_commands)
        _register_restart_notification(dp, os.environ.pop("ARCHON_RESTART_NOTIFY_CHAT_ID", None))

        await bg_mcp_server.start()
        await orch_mcp_server.start(host="localhost", port=cfg.background_agents.orch_mcp_port)
        await cron_scheduler.start()

        # Register asyncio-safe signal handlers so launchd SIGTERM/SIGINT
        # reliably triggers a graceful shutdown even under double-signal conditions.
        loop = asyncio.get_running_loop()
        _shutdown_task: asyncio.Task[None] | None = None

        def _signal_handler(sig: int) -> None:
            nonlocal _shutdown_task
            logger.info("Received signal %s — initiating shutdown", signal.Signals(sig).name)
            if _shutdown_task is None or _shutdown_task.done():
                _shutdown_task = loop.create_task(dp.stop_polling())

        loop.add_signal_handler(signal.SIGTERM, _signal_handler, signal.SIGTERM)
        loop.add_signal_handler(signal.SIGINT, _signal_handler, signal.SIGINT)

        try:
            logger.info("Bot polling started")
            await dp.start_polling(bot)
        finally:
            logger.info("Archon shutdown initiated")
            for task in _compaction_tasks:
                task.cancel()
            try:
                await asyncio.wait_for(cron_scheduler.stop(), timeout=_SHUTDOWN_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("cron_scheduler.stop() timed out after %.0fs", _SHUTDOWN_TIMEOUT)
            try:
                await asyncio.wait_for(bg_manager.stop_all(), timeout=_SHUTDOWN_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("bg_manager.stop_all() timed out after %.0fs", _SHUTDOWN_TIMEOUT)
            try:
                await asyncio.wait_for(bg_mcp_server.stop(), timeout=_SHUTDOWN_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("bg_mcp_server.stop() timed out after %.0fs", _SHUTDOWN_TIMEOUT)
            try:
                await asyncio.wait_for(orch_mcp_server.stop(), timeout=_SHUTDOWN_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("orch_mcp_server.stop() timed out after %.0fs", _SHUTDOWN_TIMEOUT)
            try:
                await asyncio.wait_for(session_manager.stop_all(), timeout=_SHUTDOWN_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("Session cleanup timed out after %.0fs", _SHUTDOWN_TIMEOUT)
            await bot.session.close()
            logger.info("Archon shutdown complete")
