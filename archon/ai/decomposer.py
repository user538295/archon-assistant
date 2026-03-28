"""Decomposer — the brain that evaluates, answers, and plans."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncGenerator, AsyncIterator

from pathlib import Path

from archon.ai.agent_loader import load_workspace_agents
from archon.ai.agent_plan import AgentTask
from archon.ai.classification import extract_json_object
from archon.ai.claude_session import ClaudeSession
from archon.ai.constants import DEFAULT_FAST_MODEL
from archon.ai.event_mapper import (
    Event,
    INJECTION_TYPE_ROUTER_HISTORY,
    INJECTION_TYPE_ROUTER_WORKSPACE_AGENTS,
    INJECTION_TYPE_WORKSPACE_AGENTS,
    Response,
    ToolStarted,
)
from archon.ai.prompts import load_prompt
from archon.ai.reminder import build_reminder_injection

if TYPE_CHECKING:
    from claude_agent_sdk import AgentDefinition

    from archon.ai.context_provider import ContextProvider
    from archon.ai.reminder import ContextReminder

    from archon.ai.skill_loader import Skill

logger = logging.getLogger("archon")

_SUMMARIZER_MODEL = DEFAULT_FAST_MODEL
_SUMMARIZER_PROMPT = (
    "Summarize the conversation exchanges below in 70-100 words. "
    "Focus on: topics discussed, actions taken or planned, decisions made. "
    "Preserve any specific file paths, module names, or identifiers mentioned. "
    "Be factual and brief."
)
_PENDING_TURNS_MAXLEN = 200
_ROUTER_RESET_THRESHOLD = 20
_SUMMARY_RESET_THRESHOLD = 30
_SUMMARY_WAIT_TIMEOUT = 3.0
_ROUTER_TIMEOUT_S: float = 60.0
_ROUTER_RESET_TIMEOUT_S: float = 30.0
_SUMMARY_RESET_TIMEOUT_S: float = 10.0


_RAG_COLLECTIONS_RE = re.compile(
    r"<rag_selected_collections>(.*?)</rag_selected_collections>",
    re.DOTALL,
)


def _extract_rag_selected_collections(raw: str) -> list[str] | None:
    """Extract names from <rag_selected_collections> tag.

    Returns:
        None  — tag absent from response
        []    — tag present but yields zero valid names (empty or unclosed)
        [..] — list of stripped, non-empty names
    """
    # Unclosed tag: tag opens but no closing tag present
    if "<rag_selected_collections>" in raw and "</rag_selected_collections>" not in raw:
        return []

    match = _RAG_COLLECTIONS_RE.search(raw)
    if match is None:
        return None

    content = match.group(1)
    # Split by comma first; if that yields only one non-empty token with internal whitespace,
    # also try splitting by newline to support both "name1, name2" and "name1\nname2" formats.
    names = [n.strip() for n in content.split(",")]
    names = [n for n in names if n]
    # If we got a single name containing a newline, split further by whitespace lines
    if len(names) == 1 and "\n" in names[0]:
        names = [n.strip() for n in names[0].split("\n")]
        names = [n for n in names if n]
    return names


@dataclass
class TaskOutput:
    """Result from Decomposer.route_task() — trivial, small, or large task."""

    scope: str  # "trivial", "small", or "large"
    summary: str = ""
    prompt: str | None = None  # present for scope="trivial" or "small"
    agents: list[AgentTask] | None = None  # present for scope="large"
    is_fallback: bool = False  # True when router failed and fell back
    fallback_reason: str = ""  # user-friendly fallback reason
    selected_collections: list[str] | None = None  # RAG: None=absent, []=empty/unclosed


class Decomposer:
    """The brain that evaluates, answers, and plans.

    Wraps a ClaudeSession (user-selected model, full capabilities).
    Maintains conversation context across calls.
    """

    def __init__(
        self,
        cwd: str | None = None,
        skills: list[Skill] | None = None,
        model: str | None = None,
        plugins: list[dict[str, Any]] | None = None,
        agents: dict[str, AgentDefinition] | None = None,
        rag_url: str | None = None,
        background_agent_mcp_url: str | None = None,
        background_agent_mcp_headers: dict[str, str] | None = None,
        spawn_rule: str | None = None,
        reminder: "ContextReminder | None" = None,
        context_provider: "ContextProvider | None" = None,
        router_mcp_url: str | None = None,
        router_mcp_headers: dict[str, str] | None = None,
    ) -> None:
        self._cwd = cwd
        self._model = model
        self._rag_url = rag_url
        self._context_provider = context_provider
        self._router_mcp_url = router_mcp_url
        self._router_mcp_headers = router_mcp_headers
        prompt = load_prompt("decomposer")
        self._session = ClaudeSession(
            cwd=cwd,
            skills=skills,
            model=model,
            plugins=plugins,
            agents=agents,
            rag_url=rag_url,
            background_agent_mcp_url=background_agent_mcp_url,
            mcp_headers=background_agent_mcp_headers,
            spawn_rule=spawn_rule,
            system_prompt=prompt,
            reminder=reminder,
        )
        # Lazy sessions — created and started on first use to reduce startup
        # resource contention (Bug 03/07: 4 SDK subprocesses at first message).
        self._router_session: ClaudeSession | None = None
        self._summary_session: ClaudeSession | None = None
        # Context tracking — Haiku summarization of answer() turns
        self._pending_turns: deque[tuple[str, str]] = deque(maxlen=_PENDING_TURNS_MAXLEN)
        self._context_summary: str = ""
        self._summary_task: asyncio.Task[None] | None = None
        self._router_call_count: int = 0
        self._summary_call_count: int = 0
        # BUG-15: accumulated costs from previous router/summary sessions that were reset.
        self._router_cost_carryover: float = 0.0
        self._summary_cost_carryover: float = 0.0

    async def start(self) -> None:
        """Start the main session only. Router and summary sessions are lazy-started on first use."""
        await self._session.start()
        await self._inject_workspace_agents()

    async def _inject_workspace_agents(self) -> None:
        """Read agents.md from the workspace directory and inject into the main session.

        Router session receives the injection only if it has already been started.
        """
        ctx = await load_workspace_agents(self._cwd)
        if ctx is None:
            return
        self._session.inject_context(ctx, INJECTION_TYPE_WORKSPACE_AGENTS)
        if self._router_session is not None:
            self._router_session.inject_context(ctx, INJECTION_TYPE_ROUTER_WORKSPACE_AGENTS)

    def force_kill_for_recovery(self) -> None:
        """Kill session subprocess and reset locks for recovery.

        Delegates to ``ClaudeSession.force_kill_for_recovery()``.
        """
        self._session.force_kill_for_recovery()

    async def restart_session(self) -> None:
        """Create a fresh session (start + inject agents, no stop).

        Used after ``force_kill_for_recovery()`` where the old subprocess
        is already dead.  Must run in a clean asyncio task (no stale
        anyio cancel scopes).
        """
        logger.info("Decomposer: starting fresh session after force-kill")
        await self._session.start()
        await self._inject_workspace_agents()
        logger.info("Decomposer: fresh session ready")

    async def recover_session(self) -> None:
        """Stop and restart the main session after a timeout.

        Re-injects workspace agents after restart.
        Caller must guard with a timeout.
        """
        logger.info("Decomposer: recovering main session (stop + start)")
        await self._session.stop()
        await self._session.start()
        await self._inject_workspace_agents()
        logger.info("Decomposer: main session recovered")

    async def stop(self) -> None:
        # Cancel in-flight summary task
        if self._summary_task and not self._summary_task.done():
            self._summary_task.cancel()
            try:
                await self._summary_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._summary_session is not None:
            try:
                await self._summary_session.stop()
            except Exception:
                logger.error("Summary session stop failed", exc_info=True)
        if self._router_session is not None:
            try:
                await self._router_session.stop()
            except Exception:
                logger.error("Router session stop failed", exc_info=True)
        try:
            await self._session.stop()
        except Exception:
            logger.error("Main session stop failed", exc_info=True)

    # ── Lazy session factories ──────────────────────────────────────

    async def _ensure_router_session(self) -> ClaudeSession:
        """Return the router session, creating and starting it on first use."""
        if self._router_session is None:
            router_prompt = load_prompt("orchestrator")
            # Passing router_mcp_url via background_agent_mcp_url — the ClaudeSession
            # parameter is generic (registers under the 'archon' MCP key); the router
            # session only exposes history_read/history_grep tools, not background
            # agent spawn tools.
            session = ClaudeSession(
                cwd=self._cwd,
                model=self._model,
                background_agent_mcp_url=self._router_mcp_url,
                mcp_headers=self._router_mcp_headers,
                system_prompt=router_prompt,
                tools=[],
                max_turns=5,
            )
            await session.start()
            # Assign only after successful start so a failed start doesn't cache a broken session.
            self._router_session = session
            logger.debug("Router session lazy-started")
            # Inject context that was available at Decomposer.start() time.
            if self._context_provider is not None:
                try:
                    ctx_prompt = self._context_provider.startup_context_prompt(rag_enabled=self._rag_url is not None)
                    ctx = self._context_provider.get_recent_context()
                    injected = ctx_prompt if not ctx else f"{ctx_prompt}\n\n---\n\n{ctx}"
                    self._router_session.inject_context(injected, INJECTION_TYPE_ROUTER_HISTORY)
                    files = self._context_provider.get_context_files()
                    if files:
                        logger.info(
                            "Injecting history into router session: %s",
                            ", ".join(f.name for f in files),
                        )
                    else:
                        logger.info("Injecting history into router session: startup prompt only")
                except Exception as exc:
                    logger.warning(
                        "Failed to inject history context into router session: %s", exc
                    )
            workspace_ctx = await load_workspace_agents(self._cwd)
            if workspace_ctx is not None:
                self._router_session.inject_context(workspace_ctx, INJECTION_TYPE_ROUTER_WORKSPACE_AGENTS)
        return self._router_session

    async def _ensure_summary_session(self) -> ClaudeSession:
        """Return the summary session, creating and starting it on first use."""
        if self._summary_session is None:
            session = ClaudeSession(
                model=_SUMMARIZER_MODEL,
                system_prompt=_SUMMARIZER_PROMPT,
                tools=[],
                max_turns=1,
            )
            await session.start()
            # Assign only after successful start so a failed start doesn't cache a broken session.
            self._summary_session = session
            logger.debug("Summary session lazy-started")
        return self._summary_session

    # ── Mode 2: Answer directly ────────────────────────────────────

    async def answer(self, prompt: str) -> AsyncGenerator[Event, None]:
        """Answer directly — full streaming with thinking, tools, response."""
        last_response = ""
        try:
            async for event in self._session.send(prompt):
                if isinstance(event, Response):
                    last_response = event.content
                yield event
        finally:
            if last_response:
                self._pending_turns.append((prompt, last_response))
                self._schedule_summary()

    # ── Mode 3: Route a task (decide scope) ────────────────────────

    async def route_task(
        self, prompt: str, rag_pre_context: str | None = None
    ) -> AsyncGenerator[Event | TaskOutput, None]:
        """Route a task — Decomposer decides scope in one call.

        Yields every router session event immediately as it arrives, then
        yields exactly one TaskOutput sentinel as the final item.
        On parse failure, falls back to scope="small" with the original prompt.

        Args:
            prompt: The user message to route.
            rag_pre_context: Optional RAG collection block from RagContextProvider.
                When present, appended at the end of the instruction so the LLM
                reasons about routing first, then outputs collection selection.
        """
        await self._await_pending_summary()
        try:
            async with asyncio.timeout(_ROUTER_RESET_TIMEOUT_S):
                await self._reset_router_if_needed()
        except TimeoutError:
            logger.warning(
                "_reset_router_if_needed() timed out after %.0fs — falling back to small scope",
                _ROUTER_RESET_TIMEOUT_S,
            )
            # Silent fallback — timeout is an internal routing detail, not a user error.
            yield TaskOutput(scope="small", prompt=prompt, is_fallback=True, fallback_reason="")
            return
        except Exception as exc:
            logger.error("_reset_router_if_needed() failed: %s — falling back to small scope", exc, exc_info=True)
            yield TaskOutput(scope="small", prompt=prompt, is_fallback=True, fallback_reason="")
            return

        context = self._build_router_context()
        context_block = f"\n\n{context}\n\n" if context else "\n\n"

        file_paths = self._extract_recent_file_paths()
        paths_block = f"\n\n{file_paths}\n" if file_paths else ""

        from archon.config import config as _cfg
        route_prompt = load_prompt("route_task").replace(
            "{history_dir}", _cfg.history.directory
        )
        reminder_block = ""
        if self._cwd is not None:
            reminder_ctx = build_reminder_injection(Path(self._cwd))
            if reminder_ctx is not None:
                reminder_block = f"\n\n{reminder_ctx}"

        rag_block = f"\n\n{rag_pre_context}" if rag_pre_context else ""

        instruction = (
            f"[INTERNAL: pipeline orchestration — not a user message]"
            f"{context_block}"
            f"{paths_block}"
            f"{reminder_block}"
            f"{route_prompt}\n\nUser request: {prompt}"
            f"{rag_block}"
        )

        # C2: wrap _ensure_router_session() in a timeout so a hanging SDK init
        # does not hold the Pipeline lock forever.
        try:
            async with asyncio.timeout(_ROUTER_RESET_TIMEOUT_S):
                router = await self._ensure_router_session()
        except TimeoutError:
            logger.warning("router session init timed out")
            yield TaskOutput(scope="small", prompt=prompt, is_fallback=True, fallback_reason="")
            return
        except Exception as exc:
            logger.error("router session init failed: %s", exc, exc_info=True)
            yield TaskOutput(scope="small", prompt=prompt, is_fallback=True, fallback_reason="")
            return

        last_response: Response | None = None
        # C1: store generator in a variable so we can aclose() it on timeout.
        gen = router.send(instruction)
        try:
            try:
                async with asyncio.timeout(_ROUTER_TIMEOUT_S):
                    async for event in gen:
                        yield event
                        if isinstance(event, Response):
                            last_response = event  # capture LAST Response (no break)
            except TimeoutError:
                logger.warning(
                    "_router_session.send() timed out after %.0fs for prompt: %.100s",
                    _ROUTER_TIMEOUT_S,
                    prompt,
                )
                # Silent fallback — timeout is an internal routing detail, not a user error.
                yield TaskOutput(scope="small", prompt=prompt, is_fallback=True, fallback_reason="")
                return
            except Exception as exc:
                logger.error("Decomposer route_task failed: %s", exc, exc_info=True)
                yield TaskOutput(scope="small", summary="Direct handling", prompt=prompt,
                                 is_fallback=True,
                                 fallback_reason="Could not plan this task — attempting inline")
                return
        finally:
            try:
                await asyncio.wait_for(gen.aclose(), timeout=5.0)
            except Exception:
                logger.warning(
                    "route_task: gen.aclose() timed out or failed for prompt: %.100s",
                    prompt,
                    exc_info=True,
                )

        raw_response = last_response.content if last_response is not None else ""
        task_output = self._parse_task_output(raw_response, prompt)
        if task_output.scope == "large" and task_output.summary:
            self._pending_turns.append((prompt, task_output.summary))
            self._schedule_summary()
        yield task_output

    def _parse_task_output(self, raw: str, original_prompt: str) -> TaskOutput:
        """Parse route_task JSON response with graceful fallback.

        RAG tag extraction runs first on the raw text, independently of JSON parsing.
        """
        _fallback_reason = "Could not plan this task — attempting inline"

        # Extract RAG collections tag first — independent of JSON parsing
        rag_collections = _extract_rag_selected_collections(raw)

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            extracted = extract_json_object(raw)
            if extracted is None:
                logger.warning("route_task parse failed: no JSON found")
                return TaskOutput(
                    scope="small", summary="Direct handling", prompt=original_prompt,
                    is_fallback=True, fallback_reason=_fallback_reason,
                    selected_collections=rag_collections,
                )
            try:
                data = json.loads(extracted)
            except (json.JSONDecodeError, TypeError):
                logger.warning("route_task parse failed: malformed JSON")
                return TaskOutput(
                    scope="small", summary="Direct handling", prompt=original_prompt,
                    is_fallback=True, fallback_reason=_fallback_reason,
                    selected_collections=rag_collections,
                )

        if not isinstance(data, dict):
            return TaskOutput(
                scope="small", summary="Direct handling", prompt=original_prompt,
                is_fallback=True, fallback_reason=_fallback_reason,
                selected_collections=rag_collections,
            )

        scope = data.get("scope")
        summary = data.get("summary", "")

        if scope == "large":
            agents_raw = data.get("agents", [])
            if isinstance(agents_raw, list) and agents_raw:
                agents = []
                for entry in agents_raw:
                    if isinstance(entry, dict):
                        agent_id = entry.get("id", "")
                        task = entry.get("task", "")
                        depends_on = entry.get("depends_on", [])
                        if agent_id and task:
                            agents.append(
                                AgentTask(id=agent_id, task=task, depends_on=tuple(depends_on))
                            )
                if agents:
                    return TaskOutput(
                        scope="large", summary=summary, agents=agents,
                        selected_collections=rag_collections,
                    )

            # Large scope but invalid agents — fall back
            logger.warning("route_task: scope=large but agents invalid")
            return TaskOutput(
                scope="small",
                summary=summary or "Direct handling",
                prompt=original_prompt,
                is_fallback=True, fallback_reason=_fallback_reason,
                selected_collections=rag_collections,
            )

        if scope in ("trivial", "small"):
            prompt_text = data.get("prompt", original_prompt)
            return TaskOutput(
                scope=scope, summary=summary, prompt=prompt_text,
                selected_collections=rag_collections,
            )

        # Unknown scope — fall back
        return TaskOutput(
            scope="small", summary="Direct handling", prompt=original_prompt,
            is_fallback=True, fallback_reason=_fallback_reason,
            selected_collections=rag_collections,
        )

    # ── Context tracking ───────────────────────────────────────────

    def _schedule_summary(self) -> None:
        """Schedule a Haiku summary task, skipping if one is already running."""
        if self._summary_task and not self._summary_task.done():
            return  # already running; new turns picked up by self-scheduling
        self._summary_task = asyncio.create_task(self._refresh_summary())

    async def _refresh_summary(self) -> None:
        """Summarize pending turns using Haiku (fire-and-forget)."""
        snapshot = list(self._pending_turns)
        if not snapshot:
            return

        summary_session = await self._ensure_summary_session()

        # Reset summary session periodically to clear accumulated SDK history.
        self._summary_call_count += 1
        if self._summary_call_count >= _SUMMARY_RESET_THRESHOLD:
            # BUG-15: accumulate old session cost before discarding the session.
            old_stats = summary_session.usage_stats or {}
            self._summary_cost_carryover += old_stats.get("total_cost_usd", 0.0)
            try:
                async with asyncio.timeout(_SUMMARY_RESET_TIMEOUT_S):
                    await summary_session.stop()
            except TimeoutError:
                logger.warning(
                    "Summary session stop timed out after %.1fs", _SUMMARY_RESET_TIMEOUT_S
                )
            self._summary_session = None
            summary_session = await self._ensure_summary_session()
            self._summary_call_count = 0

        parts: list[str] = []
        if self._context_summary:
            parts.append(f"Previous summary:\n{self._context_summary}")
        parts.append("New exchanges:")
        for prompt, response in snapshot:
            parts.append(f"User: {prompt}")
            parts.append(f"Assistant: {response}")

        try:
            summary = ""
            async for event in summary_session.send("\n".join(parts)):
                if isinstance(event, Response):
                    summary = event.content
            if summary:
                self._context_summary = summary
                # Single-task ordering guaranteed: no other coroutine can modify
                # _pending_turns between these lines because asyncio is cooperative
                # and we hold no yields here.
                for _ in range(min(len(snapshot), len(self._pending_turns))):
                    self._pending_turns.popleft()
                # Self-schedule if new turns arrived during this run
                if self._pending_turns:
                    self._summary_task = asyncio.create_task(self._refresh_summary())
        except Exception:
            logger.warning(
                "Context summarization failed, keeping turns in buffer",
                exc_info=True,
            )
            # No self-scheduling on failure — prevents infinite retry loops.
            # Next answer() or track_context() call will schedule again.

    async def _await_pending_summary(self) -> None:
        """Wait for in-flight summary to complete (with timeout)."""
        if self._summary_task and not self._summary_task.done():
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._summary_task),
                    timeout=_SUMMARY_WAIT_TIMEOUT,
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                logger.debug(
                    "Summary wait timed out after %.1fs", _SUMMARY_WAIT_TIMEOUT
                )

    def _build_router_context(self) -> str:
        """Build context block from Haiku summary for routing prompts."""
        if not self._context_summary:
            return ""
        return (
            "[Main-session context for routing:]\n"
            f"{self._context_summary}\n"
            "[End context]"
        )

    @property
    def context_summary(self) -> str:
        """Return the current Haiku-generated conversation summary."""
        return self._context_summary

    def _extract_recent_file_paths(self) -> str:
        """Extract file paths from the main session's recent tool calls.

        Reads _session.recent_events() — a deque of (timestamp, Event) pairs
        capped at 200 entries. Extracts paths from ToolStarted inputs (Read,
        Write, Edit, Glob, Grep). Works on any machine, any project — no
        dependency on any local file existing.

        Returns empty string when no tool calls have been made yet (e.g. first
        user message in a session).
        """
        paths: list[str] = []
        for _, event in self._session.recent_events(50):
            if not isinstance(event, ToolStarted) or not event.input:
                continue
            if event.name == "Bash":
                continue
            if isinstance(event.input, str):
                try:
                    data = json.loads(event.input)
                except (json.JSONDecodeError, TypeError):
                    # Bare path string (e.g. "/project/file.py" from _tool_input_text)
                    stripped = event.input.strip()
                    if stripped and ("/" in stripped or stripped.startswith("~")):
                        paths.append(stripped)
                    continue
            else:
                data = event.input
            if not isinstance(data, dict):
                continue
            for key in ("file_path", "path"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    paths.append(val.strip())
        if not paths:
            return ""
        unique = list(dict.fromkeys(reversed(paths)))[:15]  # dedupe, cap at 15 most-recent
        return (
            "[Files accessed in this session — use for absolute path references:]\n"
            + "\n".join(unique)
            + "\n[End files]"
        )

    async def _reset_router_if_needed(self) -> None:
        """Periodically restart the router session to clear accumulated history."""
        self._router_call_count += 1
        if self._router_call_count < _ROUTER_RESET_THRESHOLD:
            return
        # If router session was never started (lazy), nothing to reset.
        if self._router_session is not None:
            # BUG-15: accumulate old session cost before discarding the session.
            old_stats = self._router_session.usage_stats or {}
            self._router_cost_carryover += old_stats.get("total_cost_usd", 0.0)
            # Null the reference BEFORE stop() so a timeout during stop() leaves no zombie.
            old_session = self._router_session
            self._router_session = None
            await old_session.stop()
        self._router_call_count = 0

    def track_context(self, prompt: str, summary: str) -> None:
        """Record a context entry from an external source (escalation, agent completion)."""
        self._pending_turns.append((prompt, summary))
        self._schedule_summary()

    # ── Context management ─────────────────────────────────────────

    def inject_context(self, text: str, injection_type: str = "context", detail: str | None = None) -> None:
        self._session.inject_context(text, injection_type, detail)

    def flush_pending_context(self) -> None:
        self._session.flush_pending_context()

    def activate_skill(self, skill: Skill) -> None:
        self._session.activate_skill(skill)

    # ── Duck-typing surface (delegated to inner session) ───────────

    @property
    def is_processing(self) -> bool:
        return self._session.is_processing

    @property
    def processing_seconds(self) -> float | None:
        return self._session.processing_seconds

    @property
    def idle_seconds(self) -> float | None:
        return self._session.idle_seconds

    @property
    def diagnostics(self) -> dict[str, Any]:
        return self._session.diagnostics

    @property
    def usage_stats(self) -> dict[str, Any] | None:
        """Return usage stats for the main session with sub-session data attached.

        CONTRACT: ``total_cost_usd`` reflects the main (answer) session cost only.
        It does NOT include orchestration or summary session costs.
        Pipeline.usage_stats adds all sub-session costs on top — never change this
        to aggregate costs here or Pipeline will double-count them.
        """
        main = self._session.usage_stats
        if main is None:
            return None

        def _sub_stats(s: ClaudeSession | None, carryover: float) -> dict[str, Any]:
            if s is None:
                return {"cost_usd": carryover, "cumulative_cache_creation": 0}
            u = s.usage_stats or {}
            return {
                "cost_usd": carryover + u.get("total_cost_usd", 0.0),
                "cumulative_cache_creation": u.get("cumulative_cache_creation", 0),
            }

        return {
            **main,
            "sessions": {
                "orchestration": _sub_stats(self._router_session, self._router_cost_carryover),
                "summary": _sub_stats(self._summary_session, self._summary_cost_carryover),
            },
        }

    def context_percentage(self) -> int:
        """Return context window usage percentage, delegating to the inner session."""
        return self._session.context_percentage()

    @property
    def send_count(self) -> int:
        return self._session.send_count

    @property
    def is_alive(self) -> bool:
        return self._session.is_alive

    @property
    def model(self) -> str | None:
        return self._session.model

    def recent_events(self, n: int = 20) -> list[tuple[float, Event]]:
        return self._session.recent_events(n)

    @property
    def reminder(self) -> "ContextReminder | None":
        return self._session.reminder
