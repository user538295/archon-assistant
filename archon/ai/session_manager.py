"""Session manager — per-user ClaudeSession registry with inactivity eviction."""
import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from claude_agent_sdk import AgentDefinition

from archon.ai.claude_session import ClaudeSession
from archon.ai.event_mapper import INJECTION_TYPE_BACKGROUND_AGENT_COMPLETION, INJECTION_TYPE_HISTORY
from archon.ai.pipeline import Pipeline, _TOOL_PROMOTION_THRESHOLD

if TYPE_CHECKING:
    from archon.ai.agent_loader import Agent, AgentLoader
    from archon.ai.context_provider import ContextProvider
    from archon.ai.plugin_loader import PluginLoader
    from archon.ai.reminder import ContextReminder
    from archon.ai.skill_loader import SkillLoader
    from archon.config.loader import ReminderConfig


def _build_sdk_agents(agents: "list[Agent] | None") -> dict[str, AgentDefinition] | None:
    """Convert a list of :class:`~archon.ai.agent_loader.Agent` objects to an SDK dict.

    Only agents with non-empty name are included.  Returns ``None`` when the
    input is ``None`` or an empty list so callers can use the result directly
    as the ``agents`` parameter of :class:`~archon.ai.claude_session.ClaudeSession`.
    """
    if not agents:
        return None
    return {
        agent.name: AgentDefinition(
            description=agent.description,
            prompt=agent.prompt,
            tools=agent.tools if agent.tools else None,
            model=agent.model,  # type: ignore[arg-type]  # SDK accepts str | None
        )
        for agent in agents
    }


logger = logging.getLogger("archon")


class SessionManager:
    """Maintain a per-user ClaudeSession registry with inactivity timeout."""

    def __init__(
        self,
        timeout: float | int,
        cwd: str | None = None,
        session_factory: Callable[[str | None], ClaudeSession] | None = None,
        skill_loader: "SkillLoader | None" = None,
        plugin_loader: "PluginLoader | None" = None,
        agent_loader: "AgentLoader | None" = None,
        rag_url: str | None = None,
        background_agent_mcp_server: "Any | None" = None,
        spawn_rule: str | None = None,
        history_compactor: "ContextProvider | None" = None,
        reminder_config: "ReminderConfig | None" = None,
        tool_promotion_threshold: int = _TOOL_PROMOTION_THRESHOLD,
        router_mcp_url: str | None = None,
        router_mcp_headers: dict[str, str] | None = None,
        auto_compact_threshold: int = 0,
    ) -> None:
        self._timeout = timeout
        self._cwd = cwd
        self._model: str | None = None
        self._agent_loader = agent_loader
        self._rag_url = rag_url
        self._bg_mcp_server = background_agent_mcp_server  # ArchonMCPServer | None
        self._spawn_rule = spawn_rule
        self._tool_promotion_threshold = tool_promotion_threshold
        self._history_compactor: "ContextProvider | None" = history_compactor
        self._reminder_config: "ReminderConfig | None" = reminder_config
        self._router_mcp_url = router_mcp_url
        self._router_mcp_headers = router_mcp_headers
        self._auto_compact_threshold = auto_compact_threshold
        if session_factory is not None:
            self._factory: Callable[[str | None, int | None], ClaudeSession] = (
                lambda c, uid: session_factory(c)
            )
        else:
            def _default_factory(c: str | None, uid: int | None = None) -> ClaudeSession:
                personal_skills = skill_loader.load_all() if skill_loader else []
                plugin_skills = plugin_loader.get_skills() if plugin_loader else []
                sdk_plugins = plugin_loader.get_sdk_configs() if plugin_loader else []

                # Filesystem agents (archon-only) — loaded via AgentLoader
                loader_agents = (
                    [a for a in self._agent_loader.load_all() if a.is_archon]
                    if self._agent_loader
                    else []
                )
                merged_agents = _build_sdk_agents(loader_agents)

                bg_url: str | None = None
                bg_headers: dict[str, str] | None = None
                if self._bg_mcp_server is not None and uid is not None:
                    bg_url = self._bg_mcp_server.mcp_url_for(uid)
                    bg_headers = self._bg_mcp_server.mcp_headers_for(uid)

                reminder: "ContextReminder | None" = None
                rc = self._reminder_config
                if rc is not None and rc.enabled and c is not None:
                    from archon.ai.reminder import ContextReminder
                    reminder = ContextReminder(config=rc, workspace_dir=Path(c))

                return Pipeline(  # type: ignore[return-value]  # Pipeline duck-types as ClaudeSession
                    cwd=c,
                    skills=personal_skills + plugin_skills,
                    model=self._model,
                    plugins=sdk_plugins,
                    agents=merged_agents,
                    rag_url=self._rag_url,
                    background_agent_mcp_url=bg_url,
                    background_agent_mcp_headers=bg_headers,
                    spawn_rule=self._spawn_rule,
                    reminder=reminder,
                    tool_promotion_threshold=self._tool_promotion_threshold,
                    router_mcp_url=self._router_mcp_url,
                    router_mcp_headers=self._router_mcp_headers,
                    context_provider=self._history_compactor,
                    has_background_agents=self._bg_mcp_server is not None,
                )
            self._factory = _default_factory
        self._sessions: dict[int, ClaudeSession] = {}
        self._timers: dict[int, asyncio.Task[None]] = {}
        self._started_at: dict[int, float] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._evicted_users: set[int] = set()
        self._background_tasks: set[asyncio.Task[Any]] = set()

    async def get_or_create(self, user_id: int) -> ClaudeSession:
        """Return existing session or create and start a new one."""
        if user_id not in self._locks:
            self._locks[user_id] = asyncio.Lock()
        async with self._locks[user_id]:
            if user_id not in self._sessions:
                await self._create_session(user_id)
        self._reset_timer(user_id)
        return self._sessions[user_id]

    def has_session(self, user_id: int) -> bool:
        """Return True if user has an active session."""
        return user_id in self._sessions

    def get_model(self) -> str | None:
        """Return the current model override, or None if using the SDK default."""
        return self._model

    def set_model(self, model: str | None) -> None:
        """Set the model override; takes effect for all new sessions created afterwards."""
        self._model = model

    def session_started_at(self, user_id: int) -> float | None:
        """Return the monotonic start time of the session, or None if not active."""
        return self._started_at.get(user_id)

    def context_stats(self, user_id: int) -> dict[str, Any] | None:
        """Return usage snapshot for the user's active session, or None.

        Returns None when there is no active session or when the session has
        not yet received a response (no ResultMessage captured yet).
        """
        session = self._sessions.get(user_id)
        return session.usage_stats if session is not None else None

    async def _teardown_session(self, user_id: int) -> None:
        """Lock-free teardown helper — stop and remove session WITHOUT removing the lock.

        Must be called while holding the per-user lock.

        Callers that manage the lock lifecycle (e.g. ``auto_compact_if_needed``)
        use this instead of ``stop()`` so they can control when the lock is released.
        """
        if user_id in self._timers:
            self._timers.pop(user_id).cancel()
        self._started_at.pop(user_id, None)
        session = self._sessions.pop(user_id, None)
        if session is not None:
            await session.stop()
            logger.info("Session stopped for user %d", user_id)

    async def _create_session(self, user_id: int) -> None:
        """Lock-free session creation helper — build, start, store, and inject history.

        Must be called while holding the per-user lock.
        """
        self._evicted_users.discard(user_id)
        session = self._factory(self._cwd, user_id)
        await session.start()
        self._sessions[user_id] = session
        self._started_at[user_id] = time.monotonic()
        logger.info("Session created for user %d", user_id)
        if self._history_compactor is not None:
            rag_enabled = self._rag_url is not None
            prompt = self._history_compactor.startup_context_prompt(rag_enabled=rag_enabled)
            ctx = self._history_compactor.get_recent_context()
            injected = prompt if not ctx else f"{prompt}\n\n---\n\n{ctx}"
            files = self._history_compactor.get_context_files()
            file_names = [f.name for f in files]
            detail = ", ".join(file_names) if file_names else None
            session.inject_context(injected, INJECTION_TYPE_HISTORY, detail=detail)
            if file_names:
                logger.info(
                    "Injecting history into main session (user=%d): %s",
                    user_id,
                    ", ".join(file_names),
                )
            else:
                logger.info(
                    "Injecting history into main session (user=%d): startup prompt only (no compacted/partial files)",
                    user_id,
                )
        self._reset_timer(user_id)

    async def _background_compact_today(self, user_id: int) -> None:
        """Fire-and-forget: run compact_today() for *user_id* and log duration."""
        from archon.ai.history_compactor import HistoryCompactor
        if not isinstance(self._history_compactor, HistoryCompactor):
            return
        compactor = self._history_compactor
        start = time.monotonic()
        try:
            await compactor.compact_today()
            elapsed = time.monotonic() - start
            logger.info("compact_today() for user %d completed in %.1fs", user_id, elapsed)
        except Exception:
            logger.warning("compact_today() for user %d failed", user_id, exc_info=True)

    async def auto_compact_if_needed(self, user_id: int) -> int | None:
        """Check context usage and recycle the session if the threshold is exceeded.

        Returns the context percentage that triggered compaction, or ``None`` if
        compaction was skipped (disabled, no session, below threshold, or streaming).
        """
        if self._auto_compact_threshold == 0:
            return None
        session = self._sessions.get(user_id)
        if session is None:
            return None
        pct = session.context_percentage()
        if pct < self._auto_compact_threshold:
            return None
        if getattr(session, "is_processing", False):
            logger.info("Auto-compaction skipped for user %d (is_processing)", user_id)
            return None

        # Acquire the lock to prevent race with get_or_create during teardown+recreate
        if user_id not in self._locks:
            self._locks[user_id] = asyncio.Lock()
        async with self._locks[user_id]:
            # Re-check session inside lock in case it changed
            session = self._sessions.get(user_id)
            if session is None:
                return None
            pct = session.context_percentage()
            if pct < self._auto_compact_threshold:
                return None

            logger.info(
                "Auto-compaction triggered for user %d (context=%d%%, threshold=%d%%)",
                user_id,
                pct,
                self._auto_compact_threshold,
            )
            start = time.monotonic()

            # Schedule background compaction (fire-and-forget)
            from archon.ai.history_compactor import HistoryCompactor
            if isinstance(self._history_compactor, HistoryCompactor):
                task = asyncio.create_task(self._background_compact_today(user_id))
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)

            await self._teardown_session(user_id)
            await self._create_session(user_id)

            elapsed = time.monotonic() - start
            logger.info(
                "Session recycled for user %d in %.1fs (was at %d%% context)",
                user_id,
                elapsed,
                pct,
            )
            return pct

    async def stop(self, user_id: int) -> None:
        """Explicitly stop and remove a session."""
        self._evicted_users.discard(user_id)
        await self._teardown_session(user_id)
        self._locks.pop(user_id, None)  # remove lock only after stop() finishes

    @staticmethod
    async def _stop_with_timeout(session: Any, user_id: int, timeout: float = 3.0) -> None:
        """Stop a single session with a per-session timeout."""
        try:
            await asyncio.wait_for(session.stop(), timeout=timeout)
            logger.info("Session stopped for user %d (stop_all)", user_id)
        except asyncio.TimeoutError:
            logger.error("Session stop timed out for user %d (stop_all)", user_id)
        except Exception:
            logger.error("Session stop failed for user %d (stop_all)", user_id, exc_info=True)

    async def stop_all(self) -> None:
        """Stop all sessions (called at shutdown)."""
        for task in self._timers.values():
            task.cancel()
        self._timers.clear()
        self._started_at.clear()
        for task in list(self._background_tasks):
            task.cancel()
        self._background_tasks.clear()
        sessions = list(self._sessions.items())
        if sessions:
            await asyncio.gather(
                *(self._stop_with_timeout(s, uid) for uid, s in sessions),
                return_exceptions=True,
            )
        self._sessions.clear()
        self._locks.clear()
        self._evicted_users.clear()

    def _reset_timer(self, user_id: int) -> None:
        """Cancel any existing inactivity timer and start a fresh one."""
        if user_id in self._timers:
            self._timers[user_id].cancel()
        self._timers[user_id] = asyncio.create_task(self._evict_after(user_id))

    async def _evict_after(self, user_id: int) -> None:
        """Sleep for the inactivity timeout then evict the session.

        If the session is actively processing, reschedule instead of evicting
        to avoid destroying a live SDK stream mid-flight.
        """
        await asyncio.sleep(self._timeout)
        session = self._sessions.get(user_id)
        if session is not None and getattr(session, "is_processing", False):
            logger.info("Session for user %d is active — deferring eviction", user_id)
            self._timers[user_id] = asyncio.create_task(self._evict_after(user_id))
            return
        logger.info("Evicting inactive session for user %d", user_id)
        # Remove self from timers first so stop() doesn't cancel the running task
        self._timers.pop(user_id, None)
        # stop() discards the flag; finally re-adds it so eviction is recorded
        # even if teardown raises. _create_session clears it when a new session starts.
        try:
            await self.stop(user_id)
        finally:
            self._evicted_users.add(user_id)

    def track_context(self, user_id: int, prompt: str, summary: str) -> None:
        """Record context in the user's session for orchestration awareness."""
        session = self._sessions.get(user_id)
        # Duck-typed: Pipeline and ClaudeSession both implement track_context
        if session is not None and hasattr(session, "track_context"):
            session.track_context(prompt, summary)

    def inject_agent_context(self, user_id: int, text: str) -> None:
        """Forward text to the user's session via ClaudeSession.inject_context().

        The one-shot guarantee (text prepended to the next prompt and then
        discarded) is owned by ClaudeSession.inject_context — see that method
        for the exact contract.  This method is a no-op when no session exists.

        Used by BackgroundAgentManager to keep the main conversation brain aware
        of agent spawns (not completions — see record_agent_completion for that).
        """
        session = self._sessions.get(user_id)
        if session is not None:
            session.inject_context(text, INJECTION_TYPE_BACKGROUND_AGENT_COMPLETION)

    # ── Diagnostics — S14.1 ────────────────────────────────────────

    def session_diagnostics(self, user_id: int) -> "dict[str, Any] | None":
        """Return the full diagnostics dict for a user's session, or None if no session."""
        session = self._sessions.get(user_id)
        return session.diagnostics if session is not None else None

    def processing_sessions(self) -> "dict[int, float]":
        """Return {user_id: processing_seconds} for all currently-processing sessions."""
        result: dict[int, float] = {}
        for uid, session in self._sessions.items():
            secs = session.processing_seconds
            if secs is not None:
                result[uid] = secs
        return result

    def was_evicted(self, user_id: int) -> bool:
        """Return True if the user's session was evicted due to inactivity."""
        return user_id in self._evicted_users

