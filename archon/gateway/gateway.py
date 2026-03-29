"""Gateway — orchestrates bot, session manager, and routing in a single asyncio loop."""
import asyncio
import html
import importlib.util
import logging
import os
import time
from collections.abc import Awaitable
from datetime import datetime, timedelta, timezone
from enum import Enum
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
from archon.chat.command_loader import CommandLoader
from archon.chat.file_handler import FileHandler
from archon.chat.handler import handle_message
from archon.chat.media_group_collector import MediaGroupCollector
from archon.chat.voice import VoiceMessageHandler
from archon.chat.middleware import WhitelistMiddleware
from archon.config.loader import Config, ConfigError, RagConfig
from archon.gateway.startup_guard import should_send_startup_notification
from archon.gateway.startup_notification import send_startup_notification
from archon.log_setup import setup_logging
from archon.platform import get_rag_service, get_runtime
from archon.version import get_version

logger = logging.getLogger("archon")

_SHUTDOWN_TIMEOUT: float = 5.0


async def _ensure_rag_server(host: str, port: int) -> bool:
    """Check whether the RAG MCP server is reachable at *host*:*port*.

    For remote hosts: skips TCP probe and returns True unconditionally.
    For localhost: attempts a TCP connection with a 2-second timeout.
    Returns True if reachable, False otherwise.
    """
    if host not in ("localhost", "127.0.0.1"):
        logger.info("RAG server host is %s — skipping probe; assuming running", host)
        return True

    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=2.0
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, asyncio.TimeoutError):
        logger.warning("RAG server unreachable at %s:%d", host, port)
        return False


class RagState(str, Enum):
    """Possible RAG server states detected at gateway startup."""
    RUNNING = "RUNNING"
    NOT_INSTALLED = "NOT_INSTALLED"
    NOT_REGISTERED = "NOT_REGISTERED"
    NOT_RUNNING = "NOT_RUNNING"


async def _detect_rag_state(cfg: RagConfig) -> RagState:
    """Detect the current RAG server state.

    1. If TCP probe succeeds → RUNNING
    2. If lancedb is not importable → NOT_INSTALLED
    3. If service is not registered (plist/unit missing) → NOT_REGISTERED
    4. Otherwise → NOT_RUNNING (packages installed + registered, but server not started)
    """
    if await _ensure_rag_server(cfg.host, cfg.port):
        return RagState.RUNNING

    if importlib.util.find_spec("lancedb") is None:
        return RagState.NOT_INSTALLED

    if not get_rag_service().is_installed():
        return RagState.NOT_REGISTERED

    return RagState.NOT_RUNNING


async def _auto_start_rag_service(host: str, port: int) -> bool:
    """Start the RAG service and wait for it to become reachable.

    1. Starts the service via ``asyncio.to_thread`` (non-blocking).
    2. Returns ``False`` immediately if the exit code is non-zero.
    3. Polls ``_ensure_rag_server`` up to 10 times (1s apart).
    4. Returns ``True`` if the server responds within 10s, ``False`` on timeout.
    """
    exit_code: int = await asyncio.to_thread(get_rag_service().start)
    if exit_code != 0:
        logger.warning("RAG service failed to start (exit code %d)", exit_code)
        return False

    for _ in range(10):
        if await _ensure_rag_server(host, port):
            logger.info("RAG service started successfully")
            return True
        await asyncio.sleep(1)

    logger.warning("RAG service did not become reachable within 10 seconds")
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
    _suppressed_events = frozenset(cfg.history.suppressed_events)
    _history_manager = history_manager if history_manager is not None else (
        HistoryManager(cfg.history.directory, suppressed_tools=_suppressed, suppressed_events=_suppressed_events) if cfg.history.enabled else None
    )
    _agent_logger = AgentLogger(cfg.history.directory, suppressed_tools=_suppressed, suppressed_events=_suppressed_events) if cfg.history.enabled else None
    dp["history_manager"] = _history_manager
    dp["agent_logger"] = _agent_logger
    dp["job_scheduler"] = job_scheduler
    dp["background_agent_manager"] = background_agent_manager
    command_loader = CommandLoader(
        project_dir=Path(cfg.session.working_directory) / ".claude" / "commands"
    )
    dp["command_loader"] = command_loader
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


def _register_rag_state_notification(
    dp: Dispatcher,
    *,
    rag_state: RagState,
    auto_started: bool,
    allowed_user_ids: list[int],
) -> None:
    """Register a startup hook that notifies users about the RAG state.

    Does nothing when *rag_state* is ``RUNNING`` (all is well, no message needed).
    For all other states a per-user HTML message is sent with actionable guidance.
    Per-user errors are swallowed so one failing user cannot block the others.
    """
    if rag_state == RagState.RUNNING:
        return

    if rag_state == RagState.NOT_RUNNING and auto_started:
        message = "✅ <b>RAG started automatically.</b>"
    elif rag_state == RagState.NOT_RUNNING:
        message = (
            "⚠️ <b>RAG service failed to start.</b>\n"
            "Check: <code>archon rag status</code>\n"
            "Logs: <code>archon logs</code>"
        )
    elif rag_state == RagState.NOT_REGISTERED:
        message = (
            "⚠️ <b>RAG packages installed but service not registered.</b>\n"
            "Run: <code>archon rag install</code>"
        )
    else:  # NOT_INSTALLED
        message = (
            "⚠️ <b>RAG is enabled but not installed.</b>\n"
            "Run: <code>archon rag install</code> (~150MB)\n"
            "Then: <code>archon rag start</code>"
        )

    async def _startup_hook(bot: Bot, **_: object) -> None:
        for user_id in allowed_user_ids:
            try:
                await bot.send_message(user_id, message, parse_mode="HTML")
            except Exception:
                logger.warning(
                    "Failed to send RAG state notification to user %d",
                    user_id,
                    exc_info=True,
                )

    dp.startup.register(_startup_hook)


def _register_deprecated_rag_notification(
    dp: Dispatcher,
    *,
    allowed_user_ids: list[int],
) -> None:
    """Register a startup hook that warns users about the deprecated [rag] history_collection key."""
    async def _startup_hook(bot: Bot, **_: object) -> None:
        message = (
            "⚠️ <b>Deprecated config:</b> <code>[rag] history_collection</code> "
            "is no longer supported and has been ignored. "
            "Remove it from your config.toml to silence this warning."
        )
        for user_id in allowed_user_ids:
            try:
                await bot.send_message(user_id, message, parse_mode="HTML")
            except Exception:
                logger.warning(
                    "Failed to send deprecated config notification to user %d",
                    user_id,
                    exc_info=True,
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
        try:
            asyncio.run(cls._run())
        except ConfigError as exc:
            import sys as _sys
            # Write to the original stderr in case setup_logging() hasn't run yet.
            print(f"Cannot start Archon: {exc}", file=_sys.__stderr__)
            logger.critical("Cannot start Archon: %s", exc)
            raise SystemExit(1) from None

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

        # RAG: detect state and (if needed) auto-start before sessions begin.
        # rag_url is None when RAG is disabled, not installed, not registered, or start failed.
        rag_url: str | None = None
        rag_state: RagState | None = None
        auto_started: bool = False
        if cfg.rag.enabled:
            rag_state = await _detect_rag_state(cfg.rag)
            if rag_state == RagState.RUNNING:
                rag_url = f"http://{cfg.rag.host}:{cfg.rag.port}/mcp"
                logger.info("RAG MCP endpoint: %s", rag_url)
            elif rag_state == RagState.NOT_RUNNING:
                auto_started = await _auto_start_rag_service(cfg.rag.host, cfg.rag.port)
                if auto_started:
                    rag_url = f"http://{cfg.rag.host}:{cfg.rag.port}/mcp"
                    logger.info("RAG MCP endpoint (auto-started): %s", rag_url)
                else:
                    logger.warning("RAG auto-start failed — RAG integration disabled for this session")
            else:
                logger.warning("RAG state=%s — RAG integration disabled for this session", rag_state.value)

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
            attachment_store=attachment_store,
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

        # Single ArchonRouterMCPServer with per-route tool filtering:
        # - /mcp (anonymous) → history tools only (no toolkit)
        # - /mcp/{user_id} (background agents) → history + allowed toolkit tools
        BG_AGENT_ALLOWED_TOOLS = frozenset({
            "archon_status", "list_running_agents", "get_config",
            "get_job_config", "send_notification",
            "send_file", "list_attachments",
        })
        router_mcp_server = ArchonRouterMCPServer(
            history_root=cfg.history.directory,
            host="localhost",
            port=cfg.background_agents.router_mcp_port,
            toolkit=toolkit,
            allowed_tools=BG_AGENT_ALLOWED_TOOLS,
        )

        session_manager = SessionManager(
            timeout=cfg.session.inactivity_timeout_seconds,
            cwd=cfg.session.working_directory,
            skill_loader=skill_loader,
            plugin_loader=plugin_loader,
            agent_loader=agent_loader,
            rag_url=rag_url,
            background_agent_mcp_server=bg_mcp_server,
            spawn_rule=cfg.background_agents.spawn_rule,
            history_compactor=history_compactor,
            reminder_config=cfg.reminder if cfg.reminder.enabled else None,
            tool_promotion_threshold=cfg.background_agents.tool_promotion_threshold,
            router_mcp_url=router_mcp_server.mcp_url,
            router_mcp_headers={"Authorization": f"Bearer {router_mcp_server.token}"},
            auto_compact_threshold=cfg.history.auto_compact_threshold,
        )
        if cfg.models.default:
            session_manager.set_model(cfg.models.default)
            logger.info("Default model set to %s from config", cfg.models.default)

        _suppressed = frozenset(cfg.history.suppressed_tool_results)
        _suppressed_events = frozenset(cfg.history.suppressed_events)
        bg_agent_logger = AgentLogger(cfg.history.directory, suppressed_tools=_suppressed, suppressed_events=_suppressed_events) if cfg.history.enabled else None
        shared_history_manager = HistoryManager(cfg.history.directory, suppressed_tools=_suppressed, suppressed_events=_suppressed_events) if cfg.history.enabled else None
        bg_manager = BackgroundAgentManager(
            bot=bot,
            session_manager=session_manager,
            max_parallel=cfg.background_agents.max_parallel,
            model=cfg.models.default,
            cwd=cfg.session.working_directory,
            rag_url=rag_url,
            agent_logger=bg_agent_logger,
            beacon_interval_minutes=cfg.background_agents.beacon_interval_minutes,
            history_manager=shared_history_manager,
            bg_mcp_server=router_mcp_server,
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
            notifications=cfg.notifications,
            history_config=cfg.history,
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
        if cfg.rag.enabled and rag_state is not None and rag_state != RagState.RUNNING:
            _register_rag_state_notification(
                dp,
                rag_state=rag_state,
                auto_started=auto_started,
                allowed_user_ids=cfg.access.allowed_user_ids,
            )
        if cfg.rag.deprecated_history_collection:
            _register_deprecated_rag_notification(
                dp,
                allowed_user_ids=cfg.access.allowed_user_ids,
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
