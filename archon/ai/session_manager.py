"""Session manager — per-user ClaudeSession registry with inactivity eviction."""
import asyncio
import logging
import time
from typing import Callable

from archon.ai.claude_session import ClaudeSession

logger = logging.getLogger("archon")


class SessionManager:
    """Maintain a per-user ClaudeSession registry with inactivity timeout."""

    def __init__(
        self,
        timeout: float | int,
        cwd: str | None = None,
        session_factory: Callable[[str | None], ClaudeSession] | None = None,
    ) -> None:
        self._timeout = timeout
        self._cwd = cwd
        self._factory: Callable[[str | None], ClaudeSession] = (
            session_factory if session_factory is not None else lambda c: ClaudeSession(cwd=c)
        )
        self._sessions: dict[int, ClaudeSession] = {}
        self._timers: dict[int, asyncio.Task[None]] = {}
        self._started_at: dict[int, float] = {}

    async def get_or_create(self, user_id: int) -> ClaudeSession:
        """Return existing session or create and start a new one."""
        if user_id not in self._sessions:
            session = self._factory(self._cwd)
            await session.start()
            self._sessions[user_id] = session
            self._started_at[user_id] = time.monotonic()
            logger.info("Session created for user %d", user_id)
        self._reset_timer(user_id)
        return self._sessions[user_id]

    def has_session(self, user_id: int) -> bool:
        """Return True if user has an active session."""
        return user_id in self._sessions

    def session_started_at(self, user_id: int) -> float | None:
        """Return the monotonic start time of the session, or None if not active."""
        return self._started_at.get(user_id)

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
