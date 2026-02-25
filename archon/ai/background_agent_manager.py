"""BackgroundAgentManager — spawns isolated ClaudeSession tasks in the background.

Each user can have up to ``max_parallel`` concurrent background agents.  Agents
run as asyncio tasks, report results via Telegram, and inject their output into
the main session's next ``send()`` call via ``ClaudeSession.inject_context()``.
"""
import asyncio
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from archon.ai.claude_session import ClaudeSession, _AGENT_NAMES

from archon.ai.event_mapper import Response, SubagentStarted, SubagentStopped

if TYPE_CHECKING:
    from aiogram import Bot
    from archon.ai.agent_logger import AgentLogger
    from archon.ai.session_manager import SessionManager

logger = logging.getLogger("archon")


@dataclass
class AgentRun:
    """Runtime state for a single background agent execution."""
    run_id: str              # uuid4 hex string
    name: str                # human-readable name from _AGENT_NAMES pool
    task: str                # task description as given
    context: str             # context passed at spawn time
    user_id: int
    started_at: float        # time.monotonic()
    status: str = "running"  # "running" | "completed" | "failed" | "cancelled"
    result: str | None = None
    error: str | None = None
    _task_ref: asyncio.Task | None = field(default=None, repr=False, compare=False)


class BackgroundAgentManager:
    """Manage background agent runs for all users.

    Design principles:
    - ``spawn()`` creates an asyncio Task and returns immediately (fire-and-forget).
    - Each agent runs in its own isolated ``ClaudeSession`` with no shared state.
    - On completion: send a Telegram message to the user + call ``inject_context()``
      on the main session so the result is available in the next ``send()`` call.
    - Name pool: shared globally across all users to avoid same-name concurrent agents.
    """

    def __init__(
        self,
        bot: "Bot",
        session_manager: "SessionManager",
        max_parallel: int = 5,
        model: str | None = None,
        cwd: str | None = None,
        qmd_url: str | None = None,
        agent_logger: "AgentLogger | None" = None,
    ) -> None:
        self._bot = bot
        self._session_manager = session_manager
        self._max_parallel = max_parallel
        self._model = model
        self._cwd = cwd
        self._qmd_url = qmd_url
        self._agent_logger = agent_logger

        # All runs, keyed by run_id.
        self._runs: dict[str, AgentRun] = {}

        # Names currently assigned to running agents (global across all users).
        self._active_names: set[str] = set()

    # ── Public API ────────────────────────────────────────────────

    async def spawn(
        self,
        user_id: int,
        task: str,
        context: str = "",
        name: str | None = None,
    ) -> AgentRun:
        """Start a background agent and return an ``AgentRun`` immediately.

        Raises ``RuntimeError`` if the user already has ``max_parallel`` running agents.
        """
        running = self.list_running(user_id)
        if len(running) >= self._max_parallel:
            raise RuntimeError(
                f"Max parallel agents ({self._max_parallel}) already running for user {user_id}"
            )

        run_id = uuid.uuid4().hex
        agent_name = self._assign_name(preferred=name)
        run = AgentRun(
            run_id=run_id,
            name=agent_name,
            task=task,
            context=context,
            user_id=user_id,
            started_at=time.monotonic(),
        )
        self._runs[run_id] = run
        run._task_ref = asyncio.create_task(
            self._run_agent(run),
            name=f"bg-agent-{agent_name}",
        )
        logger.info("Background agent %r spawned for user %d (run_id=%s)", agent_name, user_id, run_id)
        return run

    def list_running(self, user_id: int) -> list[AgentRun]:
        """Return all AgentRun objects for *user_id* with status == 'running'."""
        return [
            r for r in self._runs.values()
            if r.user_id == user_id and r.status == "running"
        ]

    def list_all(self, user_id: int) -> list[AgentRun]:
        """Return all AgentRun objects for *user_id* regardless of status."""
        return [r for r in self._runs.values() if r.user_id == user_id]

    async def cancel(self, run_id: str) -> bool:
        """Cancel an in-progress agent. Returns True if found, False otherwise."""
        run = self._runs.get(run_id)
        if run is None:
            return False
        if run._task_ref is not None and not run._task_ref.done():
            run._task_ref.cancel()
        return True

    def get_run(self, run_id: str) -> AgentRun | None:
        """Return the AgentRun for *run_id*, or None if not found."""
        return self._runs.get(run_id)

    async def stop_all(self) -> None:
        """Cancel all running agents. Called at daemon shutdown."""
        tasks = []
        for run in list(self._runs.values()):
            if run.status == "running" and run._task_ref is not None and not run._task_ref.done():
                run._task_ref.cancel()
                tasks.append(run._task_ref)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("BackgroundAgentManager stopped; cancelled %d agent(s)", len(tasks))

    # ── Name pool management ──────────────────────────────────────

    def _assign_name(self, preferred: str | None = None) -> str:
        """Assign a unique human-readable name from the pool.

        If *preferred* is set and not in use, use it.
        Otherwise pick a random available name from the pool.
        Falls back to a short UUID hex when the pool is exhausted.
        """
        if preferred and preferred not in self._active_names:
            self._active_names.add(preferred)
            return preferred
        available = [n for n in _AGENT_NAMES if n not in self._active_names]
        name = random.choice(available) if available else f"Agent-{uuid.uuid4().hex[:6]}"
        self._active_names.add(name)
        return name

    def _release_name(self, name: str) -> None:
        """Return *name* to the available pool."""
        self._active_names.discard(name)

    # ── Agent execution ───────────────────────────────────────────

    async def _run_agent(self, run: AgentRun) -> None:
        """Execute the agent task in an isolated ClaudeSession.

        On success: update run state, send Telegram ✅, call inject_context().
        On failure: update run state, send Telegram ❌.
        On cancellation: update run state silently (user-initiated).
        """
        session = ClaudeSession(
            model=self._model,
            cwd=self._cwd,
            qmd_url=self._qmd_url,
        )
        try:
            await session.start()
            prompt = f"Context:\n{run.context}\n\nTask:\n{run.task}" if run.context else run.task
            result = ""
            # FR.003: open per-agent log file before events start arriving
            if self._agent_logger is not None:
                self._agent_logger.record_event(
                    SubagentStarted(
                        agent_id=run.run_id,
                        agent_name=run.name,
                        agent_type="background",
                        source="sub-agent",
                    )
                )
            try:
                async for event in session.send(prompt):
                    # FR.003: tag all background agent events as sub-agent and log them
                    event.source = "sub-agent"
                    if self._agent_logger is not None:
                        self._agent_logger.record_event(event)
                    if isinstance(event, Response):
                        result = event.content
            finally:
                # FR.003: finalize the log file regardless of how the event loop exits
                if self._agent_logger is not None:
                    self._agent_logger.record_event(
                        SubagentStopped(
                            agent_id=run.run_id,
                            agent_name=run.name,
                            agent_type="background",
                            source="sub-agent",
                        )
                    )
            await session.stop()

            run.status = "completed"
            run.result = result
            logger.info("Background agent %r completed (user=%d)", run.name, run.user_id)

            await self._notify_success(run)
            await self._inject_result(run)

        except asyncio.CancelledError:
            run.status = "cancelled"
            self._release_name(run.name)
            logger.info("Background agent %r cancelled (user=%d)", run.name, run.user_id)
            try:
                await session.stop()
            except Exception:
                pass
            raise  # must re-raise CancelledError

        except Exception as exc:
            run.status = "failed"
            run.error = str(exc)
            self._release_name(run.name)
            logger.exception("Background agent %r failed (user=%d)", run.name, run.user_id)
            try:
                await session.stop()
            except Exception:
                pass
            await self._notify_failure(run)
        else:
            self._release_name(run.name)

    async def _inject_result(self, run: AgentRun) -> None:
        """Call inject_context() on the user's main session (if alive)."""
        try:
            main_session = await self._session_manager.get_or_create(run.user_id)
            context_text = (
                f"[Background agent {run.name} completed]\n"
                f"Task: {run.task}\n"
                f"Response:\n{run.result}\n"
                f"[End agent {run.name}]"
            )
            main_session.inject_context(context_text)
            logger.debug("Context injected for user %d from agent %r", run.user_id, run.name)
        except Exception as exc:
            logger.warning(
                "Failed to inject context for user %d from agent %r: %s",
                run.user_id, run.name, exc,
            )

    async def _notify_success(self, run: AgentRun) -> None:
        result_snippet = (run.result or "")[:800]
        msg = f"✅ Background agent <b>{run.name}</b> completed\n{result_snippet}"
        await self._send_notification(run.user_id, msg)

    async def _notify_failure(self, run: AgentRun) -> None:
        error_snippet = (run.error or "")[:400]
        msg = f"❌ Background agent <b>{run.name}</b> failed\n{error_snippet}"
        await self._send_notification(run.user_id, msg)

    async def _send_notification(self, user_id: int, text: str) -> None:
        try:
            await self._bot.send_message(user_id, text, parse_mode="HTML")
        except Exception as exc:
            logger.warning(
                "Failed to send background agent notification to user %d: %s",
                user_id, exc,
            )
