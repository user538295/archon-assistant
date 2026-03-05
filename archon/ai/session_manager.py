"""Session manager — per-user ClaudeSession registry with inactivity eviction."""
import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Callable

from claude_agent_sdk import AgentDefinition

from archon.ai.claude_session import ClaudeSession
from archon.ai.pipeline import Pipeline

if TYPE_CHECKING:
    from archon.ai.agent_loader import Agent, AgentLoader
    from archon.ai.context_provider import ContextProvider
    from archon.ai.plugin_loader import PluginLoader
    from archon.ai.skill_loader import SkillLoader


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
        qmd_url: str | None = None,
        background_agent_mcp_server: "Any | None" = None,
        spawn_rule: str | None = None,
        history_compactor: "ContextProvider | None" = None,
    ) -> None:
        self._timeout = timeout
        self._cwd = cwd
        self._model: str | None = None
        self._agent_loader = agent_loader
        self._qmd_url = qmd_url
        self._bg_mcp_server = background_agent_mcp_server  # ArchonMCPServer | None
        self._spawn_rule = spawn_rule
        self._history_compactor: "ContextProvider | None" = history_compactor
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
                if self._bg_mcp_server is not None and uid is not None:
                    bg_url = self._bg_mcp_server.mcp_url_for(uid)

                return Pipeline(  # type: ignore[return-value]  # Pipeline duck-types as ClaudeSession
                    cwd=c,
                    skills=personal_skills + plugin_skills,
                    model=self._model,
                    plugins=sdk_plugins,
                    agents=merged_agents,
                    qmd_url=self._qmd_url,
                    background_agent_mcp_url=bg_url,
                    spawn_rule=self._spawn_rule,
                )
            self._factory = _default_factory
        self._sessions: dict[int, ClaudeSession] = {}
        self._timers: dict[int, asyncio.Task[None]] = {}
        self._started_at: dict[int, float] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    async def get_or_create(self, user_id: int) -> ClaudeSession:
        """Return existing session or create and start a new one."""
        if user_id not in self._locks:
            self._locks[user_id] = asyncio.Lock()
        async with self._locks[user_id]:
            if user_id not in self._sessions:
                session = self._factory(self._cwd, user_id)
                await session.start()
                self._sessions[user_id] = session
                self._started_at[user_id] = time.monotonic()
                logger.info("Session created for user %d", user_id)
                if self._history_compactor is not None:
                    qmd_enabled = self._qmd_url is not None
                    prompt = self._history_compactor.startup_context_prompt(
                        qmd_enabled=qmd_enabled
                    )
                    ctx = self._history_compactor.get_recent_context()
                    injected = prompt if not ctx else f"{prompt}\n\n---\n\n{ctx}"
                    session.inject_context(injected)
                    logger.info(
                        "Injected history context (%d chars) for user %d",
                        len(injected),
                        user_id,
                    )
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

    async def stop(self, user_id: int) -> None:
        """Explicitly stop and remove a session."""
        if user_id in self._timers:
            self._timers.pop(user_id).cancel()
        self._started_at.pop(user_id, None)
        session = self._sessions.pop(user_id, None)
        if session is not None:
            await session.stop()
            logger.info("Session stopped for user %d", user_id)

    async def stop_all(self) -> None:
        """Stop all sessions (called at shutdown)."""
        for task in self._timers.values():
            task.cancel()
        self._timers.clear()
        self._started_at.clear()
        for user_id, session in list(self._sessions.items()):
            await session.stop()
            logger.info("Session stopped for user %d (stop_all)", user_id)
        self._sessions.clear()

    def _reset_timer(self, user_id: int) -> None:
        """Cancel any existing inactivity timer and start a fresh one."""
        if user_id in self._timers:
            self._timers[user_id].cancel()
        self._timers[user_id] = asyncio.create_task(self._evict_after(user_id))

    async def _evict_after(self, user_id: int) -> None:
        """Sleep for the inactivity timeout then evict the session."""
        await asyncio.sleep(self._timeout)
        logger.info("Evicting inactive session for user %d", user_id)
        # Remove self from timers first so stop() doesn't cancel the running task
        self._timers.pop(user_id, None)
        await self.stop(user_id)

    def track_context(self, user_id: int, prompt: str, summary: str) -> None:
        """Record context in the user's session for orchestration awareness."""
        session = self._sessions.get(user_id)
        if session is not None and hasattr(session, "track_context"):
            session.track_context(prompt, summary)

    def inject_agent_context(self, user_id: int, text: str) -> None:
        """Forward text to the user's session via ClaudeSession.inject_context().

        The one-shot guarantee (text prepended to the next prompt and then
        discarded) is owned by ClaudeSession.inject_context — see that method
        for the exact contract.  This method is a no-op when no session exists.

        Used by BackgroundAgentManager to keep the main conversation brain aware
        of agent spawns and completions.
        """
        session = self._sessions.get(user_id)
        if session is not None:
            session.inject_context(text)

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

