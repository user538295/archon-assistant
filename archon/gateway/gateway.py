"""Gateway — orchestrates bot, session manager, and routing in a single asyncio loop."""
import asyncio
import html
import logging
import os
import time
from collections.abc import Awaitable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message

from archon.ai.archon_toolkit import ArchonToolkit
from archon.ai.attachment_store import AttachmentStore
from archon.ai.agent_loader import AgentLoader
from archon.ai.agent_logger import AgentLogger
from archon.ai.archon_mcp_server import ArchonMCPServer
from archon.ai.archon_router_mcp_server import ArchonRouterMCPServer
from archon.ai.background_agent_manager import BackgroundAgentManager
from archon.ai.job_scheduler import JobScheduler
from archon.ai.history_compactor import HistoryCompactor
from archon.ai.history_manager import HistoryManager
from archon.ai.plugin_loader import PluginLoader
from archon.ai.restart_coordinator import RestartCoordinator
from archon.ai.session_manager import SessionManager
from archon.ai.skill_loader import SkillLoader
from archon.ai.truncation import SplitStrategy, TruncationStrategy
from archon.ai.tts import TTSConfig
from archon.chat.bot import create_bot, create_dispatcher, setup_bot_commands
from archon.chat.file_handler import FileHandler
from archon.chat.handler import handle_message
from archon.chat.media_group_collector import MediaGroupCollector
from archon.chat.voice import VoiceMessageHandler
from archon.chat.middleware import WhitelistMiddleware
from archon.config.loader import Config, ConfigError
from archon.gateway.startup_guard import should_send_startup_notification
from archon.gateway.startup_notification import send_startup_notification
from archon.log_setup import setup_logging
from archon.platform import get_runtime
from archon.version import get_version

logger = logging.getLogger("archon")

_SHUTDOWN_TIMEOUT: float = 5.0
_QMD_DAEMON_STARTUP_WAIT: float = 2.0  # seconds to wait after launching daemon


async def _ensure_qmd_daemon(host: str, port: int, binary_path: str = "") -> bool:
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
    from pathlib import Path

    from archon.platform import get_runtime

    if host not in ("localhost", "127.0.0.1"):
        # Remote host — assume the user manages the daemon themselves.
        logger.info("QMD daemon host is %s — skipping local start; assuming it is running", host)
        return True

    extra = [Path(binary_path).expanduser()] if binary_path else None
    qmd_bin = get_runtime().find_binary("qmd", extra_paths=extra)
    if not qmd_bin:
        if binary_path:
            logger.warning(
                "QMD enabled but 'qmd' not found in PATH or at configured "
                "binary_path '%s' — disabling QMD", binary_path,
            )
        else:
            logger.warning("QMD enabled in config but 'qmd' not found in PATH — disabling QMD")
        return False
    qmd_cmd = str(qmd_bin)

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
            qmd_cmd, "mcp", "--http", "--port", str(port), "--daemon",
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
    job_scheduler: JobScheduler | None = None,
    background_agent_manager: BackgroundAgentManager | None = None,
    bg_mcp_server: ArchonMCPServer | None = None,
    history_manager: HistoryManager | None = None,
    attachment_store: AttachmentStore | None = None,
) -> None:
    """Wire middleware, handlers, and data dependencies onto the dispatcher."""
    register_middleware(dp, cfg.access.allowed_user_ids)
    dp["attachment_store"] = attachment_store
    dp["session_manager"] = session_manager
    dp["skill_loader"] = skill_loader if skill_loader is not None else SkillLoader()
    dp["plugin_loader"] = plugin_loader
    dp["agent_loader"] = agent_loader
    dp["truncation"] = _make_truncation(cfg.output.truncation_strategy)
    dp["max_len"] = cfg.output.max_message_length
    dp["cwd"] = cfg.session.working_directory
    dp["attachments_dir"] = cfg.session.attachments_dir
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
    dp["job_scheduler"] = job_scheduler
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

    # File handlers MUST be registered BEFORE the generic text handler
    # so aiogram dispatches file messages to the correct handler first.
    # Canonical order: sticker → photo → video → audio (mutually exclusive) → document.
    if attachment_store is not None:
        media_group_collector = MediaGroupCollector()
        dp["media_group_collector"] = media_group_collector
        file_handler = FileHandler(
            attachment_store,
            media_group_collector=media_group_collector,
        )
        dp.message.register(file_handler.handle_sticker, F.sticker)
        dp.message.register(file_handler.handle_photo, F.photo)
        dp.message.register(file_handler.handle_video, F.video | F.video_note)
        # Audio: mutually exclusive with voice handler
        if not cfg.voice.enabled:
            dp.message.register(file_handler.handle_audio_attachment, F.audio)
        dp.message.register(file_handler.handle_document, F.document)

    dp.message.register(handle_message)


async def _notify_restart(
    bot: Bot,
    chat_id: int,
    *,
    version: str = "",
    mode: str = "normal",
    skill_count: int = 0,
    plugin_count: int = 0,
    agent_count: int = 0,
    job_count: int = 0,
) -> None:
    """Send the post-restart confirmation message to *chat_id*.

    Includes version and timestamp. In verbose/debug mode, also includes
    loader counts. Swallows all exceptions so that a transient Telegram error
    cannot prevent the bot from starting.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    safe_version = html.escape(version)

    lines = [
        "\u2705 <b>Restarted. Archon ready.</b>",
        f"Version: {safe_version}",
        timestamp,
    ]

    if mode in ("verbose", "debug"):
        lines.append(
            f"Skills: {skill_count} \u00b7 Plugins: {plugin_count} "
            f"\u00b7 Agents: {agent_count} \u00b7 Jobs: {job_count}"
        )

    message = "\n".join(lines)

    try:
        await bot.send_message(chat_id, message, parse_mode="HTML")
        logger.info("Restart notification sent to chat %d", chat_id)
    except Exception:
        logger.warning(
            "Failed to send restart notification to chat %d",
            chat_id,
            exc_info=True,
        )


def _register_restart_notification(
    dp: Dispatcher,
    restart_chat_id: str | None,
    *,
    version: str = "",
    mode: str = "normal",
    skill_count: int = 0,
    plugin_count: int = 0,
    agent_count: int = 0,
    job_count: int = 0,
) -> None:
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
        await _notify_restart(
            bot, chat_id,
            version=version,
            mode=mode,
            skill_count=skill_count,
            plugin_count=plugin_count,
            agent_count=agent_count,
            job_count=job_count,
        )

    dp.startup.register(_startup_hook)


def _register_startup_notification(
    dp: Dispatcher,
    *,
    allowed_user_ids: list[int],
    mode: str,
    version: str,
    skill_count: int,
    plugin_count: int,
    agent_count: int,
    job_count: int,
    restart_chat_id: int | None,
) -> None:
    """Register a startup hook that broadcasts a startup notification.

    Does nothing in ``quiet`` mode. When a crash-loop is detected (via
    :func:`should_send_startup_notification`), the broadcast is skipped
    and a warning is logged instead.
    """
    if mode == "quiet":
        return

    async def _startup_hook(bot: Bot, **_: object) -> None:
        if not await should_send_startup_notification():
            logger.warning("Crash-loop detected — skipping startup notification broadcast")
            return
        await send_startup_notification(
            bot,
            allowed_user_ids,
            mode=mode,
            version=version,
            skill_count=skill_count,
            plugin_count=plugin_count,
            agent_count=agent_count,
            job_count=job_count,
            restart_chat_id=restart_chat_id,
        )

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


async def _periodic_attachment_cleanup(store: AttachmentStore, max_age_hours: float) -> None:
    """Run attachment cleanup every 6 hours."""
    while True:
        await asyncio.sleep(6 * 3600)
        try:
            deleted = await asyncio.to_thread(store.cleanup, max_age_hours)
            if deleted:
                logger.info("Periodic cleanup: removed %d expired attachments", deleted)
        except Exception:
            logger.exception("Periodic attachment cleanup failed")


_shutting_down: bool = False


async def _restart_watcher(
    coordinator: RestartCoordinator,
    bot: Bot,
    cfg: Any,
    history_manager: HistoryManager | None,
    *,
    restart_file: Path | None = None,
) -> None:
    """Wait for a scheduled restart, notify users, and replace the process.

    Follows the same pattern as the ``/restart`` command in ``commands.py``.
    """
    reason, _delay = await coordinator.wait()

    if _shutting_down:
        logger.warning("Restart watcher fired during shutdown — skipping restart")
        return

    # Notify all whitelisted users
    for uid in cfg.access.allowed_user_ids:
        try:
            await bot.send_message(uid, f"\U0001f504 Restart scheduled by agent: {reason}")
        except Exception:
            logger.warning("Failed to send restart notification to user %d", uid, exc_info=True)

    # Append to history
    if history_manager is not None:
        try:
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S %Z")
            await history_manager.record_archon_message(f"Restart scheduled: {reason} ({ts})")
        except Exception:
            logger.warning("Failed to record restart in history — proceeding with restart", exc_info=True)

    # Write restart timestamp for rate limiting
    target = restart_file or (Path.home() / ".archon" / ".last_restart")
    coordinator.write_restart_timestamp(target)

    logger.info("Restart requested: %s", reason)
    get_runtime().restart_process()


class Gateway:
    """Orchestrator — wires the Telegram bot and session manager together."""

    @classmethod
    def start(cls) -> None:
        """Synchronous entry point called from main.py."""
        asyncio.run(cls._run())

    @classmethod
    async def _run(cls) -> None:
        global _shutting_down  # noqa: PLW0603
        _shutting_down = False

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
            daemon_ok = await _ensure_qmd_daemon(cfg.qmd.host, cfg.qmd.port, cfg.qmd.binary_path)
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

        # Attachment store: create, run startup cleanup, schedule periodic cleanup.
        attachment_store = AttachmentStore(cfg.session.attachments_dir)
        _cleanup_task: asyncio.Task[None] | None = None
        if cfg.session.attachments_cleanup_hours > 0:
            deleted = await asyncio.to_thread(attachment_store.cleanup, cfg.session.attachments_cleanup_hours)
            if deleted:
                logger.info("Startup cleanup: removed %d expired attachments", deleted)
            _cleanup_task = asyncio.create_task(
                _periodic_attachment_cleanup(
                    attachment_store, cfg.session.attachments_cleanup_hours,
                ),
                name="attachment-cleanup-periodic",
            )

        # Restart coordinator + toolkit: created before MCP servers so
        # toolkit can be passed at construction. Late deps are wired after.
        restart_coordinator = RestartCoordinator()
        toolkit = ArchonToolkit(
            restart_coordinator=restart_coordinator,
            bot=bot,
            config=cfg,
            config_file=config_file,
            skill_loader=skill_loader,
            gateway_started_at=time.monotonic(),
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
            toolkit=toolkit,
        )
        logger.info(
            "Background agents MCP server on port %d (spawn_rule=%r, max_parallel=%d)",
            cfg.background_agents.port,
            cfg.background_agents.spawn_rule,
            cfg.background_agents.max_parallel,
        )

        router_mcp_server = ArchonRouterMCPServer(
            history_root=cfg.history.directory,
            host="localhost",
            port=cfg.background_agents.router_mcp_port,
            toolkit=toolkit,
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
            router_mcp_url=router_mcp_server.mcp_url,
            router_mcp_headers={"Authorization": f"Bearer {router_mcp_server.token}"},
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
        # JobScheduler is created below — wire late deps after it exists.
        job_scheduler = JobScheduler(
            config=cfg.schedule,
            bot=bot,
            allowed_user_ids=cfg.access.allowed_user_ids,
            model=cfg.models.default,
            jobs_dir_base=Path(config_file).parent,
            cwd=cfg.session.working_directory,
        )
        # Wire late dependencies into toolkit now that all components exist.
        toolkit.set_late_deps(
            session_manager=session_manager,
            bg_manager=bg_manager,
            job_scheduler=job_scheduler,
        )

        _setup_dp(
            dp, cfg, session_manager, skill_loader, plugin_loader, agent_loader,
            config_file, job_scheduler, bg_manager, bg_mcp_server,
            history_manager=shared_history_manager,
            attachment_store=attachment_store,
        )

        dp.startup.register(setup_bot_commands)

        # Spawn restart watcher — waits for RestartCoordinator to fire.
        restart_watcher_task = asyncio.create_task(
            _restart_watcher(restart_coordinator, bot, cfg, shared_history_manager),
            name="restart-watcher",
        )

        # Pre-compute startup info for notification hooks (avoid subprocess
        # calls inside async hooks).
        version = get_version()
        notification_mode = cfg.notifications.mode
        restart_chat_id_str = os.environ.pop("ARCHON_RESTART_NOTIFY_CHAT_ID", None)
        try:
            restart_chat_id_int: int | None = int(restart_chat_id_str) if restart_chat_id_str else None
        except (ValueError, TypeError):
            logger.warning("Invalid ARCHON_RESTART_NOTIFY_CHAT_ID: %r", restart_chat_id_str)
            restart_chat_id_str = None
            restart_chat_id_int = None

        try:
            skill_count = len(skill_loader.load_all())
            plugin_count = len(plugin_loader.load_all()) if plugin_loader else 0
            agent_count = len(agent_loader.load_all())
            job_count = len(job_scheduler.job_configs)
        except Exception:
            logger.warning("Failed to collect loader counts for startup notification", exc_info=True)
            skill_count = plugin_count = agent_count = job_count = 0

        _register_restart_notification(
            dp, restart_chat_id_str,
            version=version,
            mode=notification_mode,
            skill_count=skill_count,
            plugin_count=plugin_count,
            agent_count=agent_count,
            job_count=job_count,
        )
        _register_startup_notification(
            dp,
            allowed_user_ids=cfg.access.allowed_user_ids,
            mode=notification_mode,
            version=version,
            skill_count=skill_count,
            plugin_count=plugin_count,
            agent_count=agent_count,
            job_count=job_count,
            restart_chat_id=restart_chat_id_int,
        )

        await bg_mcp_server.start()
        await router_mcp_server.start(host="localhost", port=cfg.background_agents.router_mcp_port)
        await job_scheduler.start()

        # Register asyncio-safe signal handlers so launchd SIGTERM/SIGINT
        # reliably triggers a graceful shutdown even under double-signal conditions.
        loop = asyncio.get_running_loop()
        get_runtime().register_signals(loop, dp.stop_polling)

        try:
            logger.info("Bot polling started")
            await dp.start_polling(bot, handle_signals=False)
        finally:
            _shutting_down = True
            logger.info("Archon shutdown initiated")
            restart_watcher_task.cancel()
            for task in _compaction_tasks:
                task.cancel()
            if _cleanup_task is not None:
                _cleanup_task.cancel()
            mgc = dp.get("media_group_collector")
            if mgc is not None:
                mgc.close()
            async def _safe_stop(coro: Awaitable[Any], label: str) -> None:
                try:
                    await coro
                except Exception:
                    logger.warning("%s failed during shutdown", label, exc_info=True)

            try:
                async with asyncio.timeout(_SHUTDOWN_TIMEOUT):
                    # Phase 1: stop all services (they may need the bot HTTP session)
                    await asyncio.gather(
                        _safe_stop(job_scheduler.stop(), "job_scheduler.stop()"),
                        _safe_stop(bg_manager.stop_all(), "bg_manager.stop_all()"),
                        _safe_stop(bg_mcp_server.stop(), "bg_mcp_server.stop()"),
                        _safe_stop(router_mcp_server.stop(), "router_mcp_server.stop()"),
                        _safe_stop(session_manager.stop_all(), "session_manager.stop_all()"),
                    )
                    # Phase 2: close bot session LAST
                    await _safe_stop(bot.session.close(), "bot.session.close()")
            except TimeoutError:
                logger.warning("Shutdown timed out after %.0fs", _SHUTDOWN_TIMEOUT)
            logger.info("Archon shutdown complete")
