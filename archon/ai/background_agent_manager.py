"""BackgroundAgentManager — spawns isolated ClaudeSession tasks in the background.

Each user can have up to ``max_parallel`` concurrent background agents. Agents
run as asyncio tasks and report results via Telegram notification on completion.
Agent events are written to per-agent Markdown log files via ``AgentLogger``
(FR.003) and are never streamed into the main session's chat output.
On completion, the result is injected into the main session as background context
with explicit "do not echo" framing, so Claude can answer follow-up questions
without re-sending the result (which was already delivered via Telegram).

FR.15 — Per-agent working beacon
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
While a background agent is running, the manager periodically notifies the user
with live tool/thinking counts.  The design is **sleep-first, send-always**:

1. The beacon task sleeps for ``beacon_interval_minutes`` before every action
   (including the very first).  This means short-lived agents (completing before
   the first interval elapses) produce no beacon at all — intentional.
2. On every fire a **new** Telegram message is sent.  New messages generate a
   push notification the user actually receives, and message history stays clean
   (no in-place edits scramble the chat log).

Example 6-minute run (2-minute interval)::

    🤖 Agent Harbor spawned.
    🤖 Agent Harbor is working...
    🤖 Agent Harbor is working... (6 tools, 3 thinking)
    🤖 Agent Harbor is pondering... (20 tools, 5 thinking)
    ✅ 🤖 Agent Harbor completed.

The spawn notification ("🤖 Agent Harbor spawned.") is **never modified**.

The update interval is controlled by ``beacon_interval_minutes`` (default: 2).
Setting it to 0 disables the beacon entirely.  The orchestrator's own quiet
beacon (``handler._partial_update_task``) is completely separate and unaffected.
"""

import asyncio
import contextlib
import html
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from archon.ai.agent_loader import load_workspace_agents
from archon.ai.claude_session import _AGENT_NAMES, ClaudeSession
from archon.ai.reminder import build_reminder_injection
from archon.ai.event_mapper import (
    ErrorEvent,
    Response,
    SubagentStarted,
    SubagentStopped,
    ThinkingResult,
    ToolStarted,
)
from archon.ai.truncation import SplitStrategy
from archon.chat.md_formatter import md_to_html
from archon.chat.telegram_delivery import render_split_messages

if TYPE_CHECKING:
    from aiogram import Bot

    from archon.ai.agent_logger import AgentLogger
    from archon.ai.archon_router_mcp_server import ArchonRouterMCPServer
    from archon.ai.history_manager import HistoryManager
    from archon.ai.session_manager import SessionManager

# Context preview lengths — keep context strings short to avoid polluting prompts
_SPAWN_TASK_PREVIEW = 300
logger = logging.getLogger("archon")

_COMPLETED_RUN_TTL_HOURS: int = 24

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
    "concocting",
    "tinkering",
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
    return f"🤖 Agent <b>{html.escape(name)}</b> is {word}...{stats}"


@dataclass
class AgentRun:
    """Runtime state for a single background agent execution."""

    run_id: str  # uuid4 hex string
    name: str  # human-readable name from _AGENT_NAMES pool
    task: str  # task description as given
    context: str  # context passed at spawn time
    user_id: int
    started_at: float  # time.monotonic()
    user_request: str = ""  # original Telegram message that triggered the spawn
    status: str = "running"  # "running" | "completed" | "failed" | "cancelled"
    result: str | None = None
    error: str | None = None
    log_path: Path | None = None  # path to the agent's Markdown log file
    _task_ref: asyncio.Task[None] | None = field(
        default=None, repr=False, compare=False
    )
    done: asyncio.Event = field(
        default_factory=asyncio.Event, repr=False, compare=False
    )


class BackgroundAgentManager:
    """Manage background agent runs for all users.

    Design principles:
    - ``spawn()`` creates an asyncio Task and returns immediately (fire-and-forget).
    - Each agent runs in its own isolated ``ClaudeSession`` with no shared state.
    - On completion: send a Telegram ✅ notification to the user.
    - Agent events are logged to a per-agent Markdown file via ``AgentLogger`` (FR.003).
    - Agent output is never streamed into the main session's chat output.
    - On completion, the result is injected as framed background context into the
      main session so Claude can answer follow-up questions without echoing the result.
    - Name pool: shared globally across all users to avoid same-name concurrent agents.
    - FR.15: while running, periodically send a new beacon message with live counts.
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
        history_manager: "HistoryManager | None" = None,
        bg_mcp_server: "ArchonRouterMCPServer | None" = None,
    ) -> None:
        self._bot = bot
        self._session_manager = session_manager
        self._max_parallel = max_parallel
        self._model = model
        self._cwd = cwd
        self._qmd_url = qmd_url
        self._agent_logger = agent_logger
        self._beacon_interval_minutes = beacon_interval_minutes
        self._history_manager = history_manager
        self._bg_mcp_server = bg_mcp_server

        # All runs, keyed by run_id.
        self._runs: dict[str, AgentRun] = {}

        # Names currently assigned to running agents (global across all users).
        self._active_names: set[str] = set()

    # ── Internal — run pruning ─────────────────────────────────────

    def _prune_completed_runs(self) -> None:
        """Remove completed/failed/cancelled runs older than ``_COMPLETED_RUN_TTL_HOURS``."""
        cutoff = time.monotonic() - (_COMPLETED_RUN_TTL_HOURS * 3600)
        to_remove = [
            run_id
            for run_id, run in self._runs.items()
            if run.status in ("completed", "failed", "cancelled")
            and run.started_at < cutoff
        ]
        for run_id in to_remove:
            del self._runs[run_id]
        if to_remove:
            logger.debug("Pruned %d old completed run(s) from _runs", len(to_remove))

    # ── Public API ────────────────────────────────────────────────

    async def spawn(
        self,
        user_id: int,
        task: str,
        context: str = "",
        user_request: str = "",
    ) -> AgentRun:
        """Start a background agent and return an ``AgentRun`` immediately.

        Raises ``RuntimeError`` if the user already has ``max_parallel`` running agents.
        """
        self._prune_completed_runs()
        running = self.list_running(user_id)
        if len(running) >= self._max_parallel:
            raise RuntimeError(
                f"Max parallel agents ({self._max_parallel}) already running for user {user_id}"
            )

        run_id = uuid.uuid4().hex
        agent_name = self._assign_name()
        run = AgentRun(
            run_id=run_id,
            name=agent_name,
            task=task,
            context=context,
            user_id=user_id,
            started_at=time.monotonic(),
            user_request=user_request,
        )
        try:
            agent_task = asyncio.create_task(
                self._run_agent(run),
                name=f"bg-agent-{agent_name}",
            )
        except Exception:
            self._release_name(agent_name)
            raise
        run._task_ref = agent_task
        self._runs[run_id] = run
        logger.info(
            "Background agent %r spawned for user %d (run_id=%s)",
            agent_name,
            user_id,
            run_id,
        )
        # Track spawn for router routing (Haiku summary) — no main-session injection at spawn
        try:
            self._session_manager.track_context(
                user_id,
                run.user_request or run.task,
                f"[Agent {agent_name} started — task: {run.task[:_SPAWN_TASK_PREVIEW]}]",
            )
        except Exception:
            logger.warning("Failed to track spawn context for agent %r", agent_name, exc_info=True)
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

    def _assign_name(self) -> str:
        """Assign a unique human-readable name from the pool.

        Falls back to a suffixed pool name when the pool is exhausted.
        """
        available = [n for n in _AGENT_NAMES if n not in self._active_names]
        name = (
            random.choice(available)
            if available
            else f"{random.choice(_AGENT_NAMES)}-{uuid.uuid4().hex[:6]}"
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

        FR.15: while the agent is running, a beacon task periodically sends
        new messages with live tool/thinking counts.
        """
        mcp_kwargs: dict[str, object] = {}
        if self._bg_mcp_server is not None:
            mcp_kwargs["background_agent_mcp_url"] = self._bg_mcp_server.mcp_url_for(run.user_id)
            mcp_kwargs["mcp_headers"] = self._bg_mcp_server.mcp_headers_for(run.user_id)
        session = ClaudeSession(
            model=self._model,
            cwd=self._cwd,
            rag_url=self._qmd_url,
            **mcp_kwargs,  # type: ignore[arg-type]
        )
        counts: dict[str, int] = {"tools": 0, "thinking": 0}
        beacon_task: asyncio.Task[None] | None = None

        try:  # outer try/finally ensures done is always set
            await session.start()

            # Inject agents.md so agent knows where to find history files
            ctx = await load_workspace_agents(self._cwd)
            if ctx is not None:
                session.inject_context(ctx)
                if self._history_manager is not None:
                    await self._history_manager.record_archon_message(
                        f"📌 AGENTS.md injected into agent {run.name!r}"
                    )

            # Inject REMINDER.md so agent has current project constraints
            if self._cwd is not None:
                try:
                    reminder_ctx = build_reminder_injection(Path(self._cwd))
                    if reminder_ctx is not None:
                        session.inject_context(reminder_ctx)
                        if self._history_manager is not None:
                            await self._history_manager.record_archon_message(
                                f"📌 REMINDER.md injected into agent {run.name!r}"
                            )
                except Exception:
                    logger.warning(
                        "Failed to inject REMINDER.md into agent %r", run.name, exc_info=True
                    )

            # FR.15: start beacon task if enabled.  The beacon sleeps for
            # beacon_interval_minutes before its first fire, so it does not
            # depend on the spawn notification message in any way.
            if self._beacon_interval_minutes > 0:
                beacon_task = asyncio.create_task(
                    self._agent_beacon_task(run.user_id, run, counts),
                    name=f"bg-agent-beacon-{run.name}",
                )

            # Build the full prompt before opening the log so it can be
            # recorded as the first message (agent_task field).
            prompt = (
                f"Context:\n{run.context}\n\nTask:\n{run.task}"
                if run.context
                else run.task
            )
            # FR.003: open per-agent log file before events start arriving.
            # user_request and agent_task are written as the first two sections
            # so the log opens with the full picture of what was asked.
            if self._agent_logger is not None:
                await self._agent_logger.record_event(
                    SubagentStarted(
                        agent_id=run.run_id,
                        agent_name=run.name,
                        agent_type="background",
                        user_request=run.user_request,
                        agent_task=prompt,
                        source="sub-agent",
                    )
                )
                run.log_path = self._agent_logger.get_log_path(run.run_id)
            result = ""
            try:
                async for event in session.send(prompt):
                    # FR.003: tag all background agent events as sub-agent and log them
                    event.source = "sub-agent"
                    if self._agent_logger is not None:
                        await self._agent_logger.record_event(event)
                    # FR.15: track live event counts for the beacon
                    if isinstance(event, ToolStarted):
                        counts["tools"] += 1
                    elif isinstance(event, ThinkingResult):
                        counts["thinking"] += 1
                    elif isinstance(event, Response):
                        result = event.content
            finally:
                # session.stop() first — must not be skipped even if the logger
                # await below re-raises CancelledError (F3 fix).
                try:
                    await session.stop()
                except Exception:
                    logger.warning(
                        "session.stop() failed during cleanup of agent %r",
                        run.name,
                        exc_info=True,
                    )
                # FR.003: finalize the log file regardless of how the event loop exits.
                if self._agent_logger is not None:
                    try:
                        await self._agent_logger.record_event(
                            SubagentStopped(
                                agent_id=run.run_id,
                                agent_name=run.name,
                                agent_type="background",
                                source="sub-agent",
                            )
                        )
                    except Exception:
                        logger.warning(
                            "Failed to finalize agent log for %r", run.name, exc_info=True
                        )

            run.status = "completed"
            run.result = result
            logger.info(
                "Background agent %r completed (user=%d)", run.name, run.user_id
            )

            if self._history_manager is not None and result:
                try:
                    await self._history_manager.record_event(
                        run.user_id,
                        Response(content=f"[Agent {run.name}]\n{result}"),
                    )
                except Exception:
                    logger.warning(
                        "Failed to record agent %r result to history (user=%d)",
                        run.name, run.user_id, exc_info=True,
                    )

            # Cancel beacon before sending the completion notification so no
            # stale "working" message arrives after the ✅ message.
            if beacon_task is not None and not beacon_task.done():
                beacon_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await beacon_task

            # Post-completion notifications and context injection: failures here must
            # NOT reset run.status to "failed" — the agent did complete (F5 fix).
            try:
                await self._notify_success(run)
                self._session_manager.track_context(
                    run.user_id,
                    run.user_request or run.task,
                    f"[Background agent {run.name} completed — result already delivered]",
                )
                completion_ctx = (
                    f"[BACKGROUND STATUS — do not echo, summarize, or mention to the user]\n"
                    f"Background agent '{run.name}' completed successfully. "
                    f"The full result was already delivered to the user via Telegram.\n"
                    f"[END BACKGROUND STATUS]"
                )
                self._session_manager.inject_agent_context(run.user_id, completion_ctx)
            except Exception:
                logger.error(
                    "Failed to notify/inject after agent %r completion",
                    run.name,
                    exc_info=True,
                )

        except asyncio.CancelledError:
            run.status = "cancelled"
            logger.info(
                "Background agent %r cancelled (user=%d)", run.name, run.user_id
            )
            if beacon_task is not None and not beacon_task.done():
                beacon_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await beacon_task
            raise  # must re-raise CancelledError

        except Exception as exc:
            run.status = "failed"
            run.error = str(exc)
            logger.exception(
                "Background agent %r failed (user=%d)", run.name, run.user_id
            )
            if beacon_task is not None and not beacon_task.done():
                beacon_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await beacon_task
            await self._notify_failure(run)
            if self._history_manager is not None:
                try:
                    await self._history_manager.record_event(
                        run.user_id,
                        ErrorEvent(
                            message=f"Agent {run.name} failed: {run.error or 'unknown error'}",
                            source="background-agent",
                        ),
                    )
                except Exception:
                    logger.warning(
                        "Failed to record agent %r failure to history (user=%d)",
                        run.name, run.user_id, exc_info=True,
                    )
        finally:
            # _release_name in finally ensures BaseException subclasses (F6 fix)
            # never leak the name — called exactly once regardless of exit path.
            self._release_name(run.name)
            run.done.set()

    # ── FR.15: Agent beacon ───────────────────────────────────────

    async def _agent_beacon_task(
        self,
        chat_id: int,
        run: AgentRun,
        counts: dict[str, int],
    ) -> None:
        """Periodically notify the user with live tool/thinking counts.

        Design: **sleep-first, send-always**.

        - Every iteration begins with ``await asyncio.sleep(interval_secs)`` so
          that short-lived agents (finishing before the first interval elapses)
          produce zero beacon messages — intentional, avoids noise.
        - Every fire sends a **new** Telegram message.

        Telegram API errors are logged as warnings and swallowed so a transient
        flap never kills the beacon loop.
        """
        interval_secs = self._beacon_interval_minutes * 60.0
        call_count = 0
        while True:
            await asyncio.sleep(interval_secs)
            word = "working" if call_count == 0 else random.choice(_AGENT_BEACON_WORDS)
            call_count += 1
            text = _agent_status_text(
                run.name, counts["tools"], counts["thinking"], word
            )
            try:
                await self._bot.send_message(chat_id, text, parse_mode="HTML")
            except Exception as exc:
                logger.warning(
                    "Agent beacon update failed for %r (user=%d): %s",
                    run.name,
                    run.user_id,
                    exc,
                )

    # ── Notifications ─────────────────────────────────────────────

    async def _notify_spawn(self, run: AgentRun) -> None:
        """Send the spawn notification.  The spawn message is never modified later."""
        msg = f"🤖 Agent <b>{html.escape(run.name)}</b> spawned."
        try:
            await self._bot.send_message(run.user_id, msg, parse_mode="HTML")
        except Exception as exc:
            logger.warning(
                "Failed to send spawn notification to user %d: %s",
                run.user_id,
                exc,
            )

    async def _notify_success(self, run: AgentRun) -> None:
        """Send the full agent result to the user.

        Markdown in the result is rendered chunk-by-chunk so Telegram HTML is
        never sliced mid-tag. If the header and rendered result fit in one
        message, they are combined; otherwise the header is sent first and the
        result follows as numbered chunks.
        """
        header = f"✅ 🤖 Agent <b>{html.escape(run.name)}</b> completed"
        if not run.result:
            header = f"✅ 🤖 Agent <b>{html.escape(run.name)}</b> completed (no results returned)"
            await self._send_notification(run.user_id, header)
            return

        result_chunks = render_split_messages(
            run.result,
            "",
            SplitStrategy(),
            _TELEGRAM_MAX_LEN,
            md_to_html,
        )
        result_chunks = [chunk.replace("] ", "]\n", 1) for chunk in result_chunks]
        combined = f"{header}\n{result_chunks[0]}"
        if len(result_chunks) == 1 and len(combined) <= _TELEGRAM_MAX_LEN:
            await self._send_notification(run.user_id, combined)
            return

        await self._send_notification(run.user_id, header)
        for chunk in result_chunks:
            await self._send_notification(run.user_id, chunk)

    async def _notify_failure(self, run: AgentRun) -> None:
        error_snippet = html.escape((run.error or "")[:400])
        msg = f"❌ Agent <b>{html.escape(run.name)}</b> failed\n{error_snippet}"
        await self._send_notification(run.user_id, msg)

    async def _send_notification(self, user_id: int, text: str) -> None:
        try:
            await self._bot.send_message(user_id, text, parse_mode="HTML")
        except Exception as exc:
            logger.warning(
                "Failed to send background agent notification to user %d: %s",
                user_id,
                exc,
            )
