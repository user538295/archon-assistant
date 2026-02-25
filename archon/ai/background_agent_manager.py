"""BackgroundAgentManager — spawns isolated ClaudeSession tasks in the background.

Each user can have up to ``max_parallel`` concurrent background agents.  Agents
run as asyncio tasks and report results via Telegram notification on completion.
Agent events are written to per-agent Markdown log files via ``AgentLogger``
(FR.003) — they are never injected into the main session's chat stream.

FR.15 — Per-agent working beacon
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
While a background agent is running, the manager periodically edits the
spawn-notification message in-place to show live tool/thinking counts:

    🤖 Agent <b>Atlas</b> is working... (3 tools, 1 thinking)

The update interval is controlled by ``beacon_interval_minutes`` (default: 2).
Setting it to 0 disables the beacon entirely.  The orchestrator's own quiet
beacon (``handler._partial_update_task``) is completely separate and unaffected.
"""

import asyncio
import contextlib
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from archon.ai.claude_session import _AGENT_NAMES, ClaudeSession
from archon.ai.event_mapper import (
    Response,
    SubagentStarted,
    SubagentStopped,
    ThinkingStarted,
    ToolStarted,
)

if TYPE_CHECKING:
    from aiogram import Bot

    from archon.ai.agent_logger import AgentLogger
    from archon.ai.session_manager import SessionManager

logger = logging.getLogger("archon")

# Telegram enforces a 4096-char hard limit; stay safely below it.
_TELEGRAM_MAX_LEN = 4000

# Rotating verbs used by the agent beacon after the first "working" edit.
# "working" is intentionally excluded — it is always used for the first edit.
_AGENT_BEACON_WORDS: tuple[str, ...] = (
    "pondering",
    "deliberating",
    "cogitating",
    "noodling",
    "mulling",
    "brewing",
    "percolating",
    "scheming",
    "conjuring",
    "synthesizing",
)


def _agent_status_text(
    name: str,
    tool_count: int,
    thinking_count: int,
    word: str = "working",
) -> str:
    """Format the per-agent beacon status message.

    Examples::

        🤖 Agent <b>Atlas</b> is working...
        🤖 Agent <b>Atlas</b> is working... (3 tools)
        🤖 Agent <b>Atlas</b> is pondering... (3 tools, 1 thinking)
    """
    parts: list[str] = []
    if tool_count > 0:
        parts.append(f"{tool_count} tool{'s' if tool_count != 1 else ''}")
    if thinking_count > 0:
        parts.append(f"{thinking_count} thinking")
    stats = f" ({', '.join(parts)})" if parts else ""
    return f"🤖 Agent <b>{name}</b> is {word}...{stats}"


@dataclass
class AgentRun:
    """Runtime state for a single background agent execution."""

    run_id: str  # uuid4 hex string
    name: str  # human-readable name from _AGENT_NAMES pool
    task: str  # task description as given
    context: str  # context passed at spawn time
    user_id: int
    started_at: float  # time.monotonic()
    status: str = "running"  # "running" | "completed" | "failed" | "cancelled"
    result: str | None = None
    error: str | None = None
    _task_ref: asyncio.Task | None = field(default=None, repr=False, compare=False)
    # FR.15 — beacon fields
    beacon_message_id: int | None = field(default=None)
    _beacon_ready: asyncio.Event = field(
        default_factory=asyncio.Event, repr=False, compare=False
    )


class BackgroundAgentManager:
    """Manage background agent runs for all users.

    Design principles:
    - ``spawn()`` creates an asyncio Task and returns immediately (fire-and-forget).
    - Each agent runs in its own isolated ``ClaudeSession`` with no shared state.
    - On completion: send a Telegram ✅ notification to the user.
    - Agent events are logged to a per-agent Markdown file via ``AgentLogger`` (FR.003).
    - Agent output is never injected into the main session's chat stream.
    - Name pool: shared globally across all users to avoid same-name concurrent agents.
    - FR.15: while running, periodically edit the spawn notification with live counts.
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
        beacon_interval_minutes: int = 2,
    ) -> None:
        self._bot = bot
        self._session_manager = session_manager
        self._max_parallel = max_parallel
        self._model = model
        self._cwd = cwd
        self._qmd_url = qmd_url
        self._agent_logger = agent_logger
        self._beacon_interval_minutes = beacon_interval_minutes

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
        logger.info(
            "Background agent %r spawned for user %d (run_id=%s)",
            agent_name,
            user_id,
            run_id,
        )
        await self._notify_spawn(run)
        return run

    def list_running(self, user_id: int) -> list[AgentRun]:
        """Return all AgentRun objects for *user_id* with status == 'running'."""
        return [
            r
            for r in self._runs.values()
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
            if (
                run.status == "running"
                and run._task_ref is not None
                and not run._task_ref.done()
            ):
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
        name = (
            random.choice(available) if available else f"Agent-{uuid.uuid4().hex[:6]}"
        )
        self._active_names.add(name)
        return name

    def _release_name(self, name: str) -> None:
        """Return *name* to the available pool."""
        self._active_names.discard(name)

    # ── Agent execution ───────────────────────────────────────────

    async def _run_agent(self, run: AgentRun) -> None:
        """Execute the agent task in an isolated ClaudeSession.

        On success: update run state, send Telegram ✅.
        On failure: update run state, send Telegram ❌.
        On cancellation: update run state silently (user-initiated).
        Agent output is logged via AgentLogger (FR.003) — never injected into
        the main session.

        FR.15: while the agent is running, a beacon task periodically edits
        the spawn notification with live tool/thinking counts.
        """
        session = ClaudeSession(
            model=self._model,
            cwd=self._cwd,
            qmd_url=self._qmd_url,
        )
        counts: dict[str, int] = {"tools": 0, "thinking": 0}
        beacon_task: asyncio.Task | None = None

        try:
            await session.start()

            # FR.15: wait for spawn() to finish _notify_spawn (sets beacon_message_id
            # and fires _beacon_ready).  In production the spawn notification completes
            # before session.start() finishes; the wait is a correctness guarantee.
            await run._beacon_ready.wait()
            if run.beacon_message_id is not None and self._beacon_interval_minutes > 0:
                beacon_task = asyncio.create_task(
                    self._agent_beacon_task(run.user_id, run.beacon_message_id, run, counts),
                    name=f"bg-agent-beacon-{run.name}",
                )

            prompt = (
                f"Context:\n{run.context}\n\nTask:\n{run.task}"
                if run.context
                else run.task
            )
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
                    # FR.15: track live event counts for the beacon
                    if isinstance(event, ToolStarted):
                        counts["tools"] += 1
                    elif isinstance(event, ThinkingStarted):
                        counts["thinking"] += 1
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
            logger.info(
                "Background agent %r completed (user=%d)", run.name, run.user_id
            )

            # Cancel beacon before sending the completion notification so no
            # stale "working" edit arrives after the ✅ message.
            if beacon_task is not None and not beacon_task.done():
                beacon_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await beacon_task

            await self._notify_success(run)

        except asyncio.CancelledError:
            run.status = "cancelled"
            self._release_name(run.name)
            logger.info(
                "Background agent %r cancelled (user=%d)", run.name, run.user_id
            )
            if beacon_task is not None and not beacon_task.done():
                beacon_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await beacon_task
            try:
                await session.stop()
            except Exception:
                pass
            raise  # must re-raise CancelledError

        except Exception as exc:
            run.status = "failed"
            run.error = str(exc)
            self._release_name(run.name)
            logger.exception(
                "Background agent %r failed (user=%d)", run.name, run.user_id
            )
            if beacon_task is not None and not beacon_task.done():
                beacon_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await beacon_task
            try:
                await session.stop()
            except Exception:
                pass
            await self._notify_failure(run)
        else:
            self._release_name(run.name)

    # ── FR.15: Agent beacon ───────────────────────────────────────

    async def _agent_beacon_task(
        self,
        chat_id: int,
        message_id: int,
        run: AgentRun,
        counts: dict[str, int],
    ) -> None:
        """Periodically edit the spawn notification with live tool/thinking counts.

        Sleeps for ``beacon_interval_minutes × 60`` seconds, then edits the
        message.  The first edit always uses "working"; subsequent edits rotate
        through ``_AGENT_BEACON_WORDS``.  Telegram API errors are swallowed
        silently so a flap never kills the beacon loop.
        """
        interval_secs = self._beacon_interval_minutes * 60.0
        call_count = 0
        while True:
            await asyncio.sleep(interval_secs)
            word = "working" if call_count == 0 else random.choice(_AGENT_BEACON_WORDS)
            call_count += 1
            text = _agent_status_text(run.name, counts["tools"], counts["thinking"], word)
            try:
                await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    parse_mode="HTML",
                )
            except Exception as exc:
                logger.debug(
                    "Agent beacon edit failed for %r (user=%d): %s",
                    run.name,
                    run.user_id,
                    exc,
                )

    # ── Notifications ─────────────────────────────────────────────

    async def _notify_spawn(self, run: AgentRun) -> None:
        """Send spawn notification and capture the message_id for the beacon (FR.15).

        The ``_beacon_ready`` event is always set in ``finally`` so ``_run_agent``
        never hangs waiting for it — even if the Telegram call fails.
        """
        msg = f"🤖 Agent <b>{run.name}</b> spawned."
        try:
            sent = await self._bot.send_message(run.user_id, msg, parse_mode="HTML")
            if sent is not None and hasattr(sent, "message_id"):
                run.beacon_message_id = sent.message_id
        except Exception as exc:
            logger.warning(
                "Failed to send spawn notification to user %d: %s",
                run.user_id,
                exc,
            )
        finally:
            # Always unblock _run_agent's beacon_ready.wait()
            run._beacon_ready.set()

    async def _notify_success(self, run: AgentRun) -> None:
        """Send the full agent result to the user.

        If header + result fits in one Telegram message (≤4000 chars) it is
        sent as a single message.  Otherwise the header is sent first, then the
        result is split into ≤4000-char chunks labelled [1/N], [2/N], …
        """
        result = run.result or ""
        header = f"✅ 🤖 Agent <b>{run.name}</b> completed"
        combined = f"{header}\n{result}" if result else header
        if len(combined) <= _TELEGRAM_MAX_LEN:
            await self._send_notification(run.user_id, combined)
        else:
            await self._send_notification(run.user_id, header)
            await self._send_long_message(run.user_id, result)

    async def _notify_failure(self, run: AgentRun) -> None:
        error_snippet = (run.error or "")[:400]
        msg = f"❌ Agent <b>{run.name}</b> failed\n{error_snippet}"
        await self._send_notification(run.user_id, msg)

    async def _send_long_message(self, user_id: int, text: str) -> None:
        """Split *text* into ≤4000-char chunks and send each as a separate message.

        Single-chunk texts are sent without a label.  Multi-chunk texts are
        labelled [1/N], [2/N], … so the user can follow the sequence.
        """
        chunks = [text[i : i + _TELEGRAM_MAX_LEN] for i in range(0, len(text), _TELEGRAM_MAX_LEN)]
        if len(chunks) == 1:
            await self._send_notification(user_id, chunks[0])
        else:
            for idx, chunk in enumerate(chunks, 1):
                await self._send_notification(user_id, f"[{idx}/{len(chunks)}]\n{chunk}")

    async def _send_notification(self, user_id: int, text: str) -> None:
        try:
            await self._bot.send_message(user_id, text, parse_mode="HTML")
        except Exception as exc:
            logger.warning(
                "Failed to send background agent notification to user %d: %s",
                user_id,
                exc,
            )
