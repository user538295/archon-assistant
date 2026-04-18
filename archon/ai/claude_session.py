"""Claude session — wraps ClaudeSDKClient to provide typed event streaming."""
import asyncio
import logging
import os
import time
from collections import deque
from typing import TYPE_CHECKING, Any, AsyncGenerator

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk import ResultMessage as _ResultMessage

from archon.ai.constants import get_context_window
from archon.ai.event_mapper import (
    ContextInjectedEvent,
    ErrorEvent,
    Event,
    EventMapper,
    ReminderInjectedEvent,
    Response,
    SkillInjectedEvent,
)
from archon.ai.reminder import ContextReminder

if TYPE_CHECKING:
    from archon.ai.skill_loader import Skill

logger = logging.getLogger("archon")

# Serializes concurrent start() calls during the os.environ mutation + SDK connect
# window so two sessions don't race on the process-global CLAUDECODE env var.
# Lazy-initialized so the lock is always created on the running event loop
# (avoids "bound to a different event loop" errors across pytest runs).
_ENV_LOCK: asyncio.Lock | None = None


def _get_env_lock() -> asyncio.Lock:
    global _ENV_LOCK  # noqa: PLW0603
    if _ENV_LOCK is None:
        _ENV_LOCK = asyncio.Lock()
    return _ENV_LOCK

# Pool of 30 unique human-readable names assigned to sub-agents at spawn time.
# Stored here so tests can import _AGENT_NAMES directly.
_AGENT_NAMES: list[str] = [
    "Atlas",  "Sage",   "Orion",  "Nova",   "Echo",
    "Cipher", "Dusk",   "Ember",  "Flux",   "Gale",
    "Harbor", "Iris",   "Jade",   "Kite",   "Lyra",
    "Mist",   "Nexus",  "Onyx",   "Pearl",  "Quest",
    "Raven",  "Sable",  "Terra",  "Umbra",  "Vega",
    "Wisp",   "Xara",   "Yara",   "Zara",   "Zephyr",
]


_SPAWN_RULE_HINTS: dict[str, str] = {
    "eager": (
        "You have access to a `spawn_background_agent` MCP tool. "
        "When a task involves multiple independent steps or parallel workstreams, "
        "proactively use this tool to run subtasks in the background while the main "
        "conversation stays interactive. You will receive each agent's result as "
        "context injected into your next message. "
        "Always pass the user's original message as the `user_request` parameter."
    ),
    "auto": (
        "You have access to a `spawn_background_agent` MCP tool. "
        "Use it when running a long task in the background would keep the main "
        "conversation more responsive. "
        "Always pass the user's original message as the `user_request` parameter."
    ),
    "manual": (
        "You have access to a `spawn_background_agent` MCP tool. "
        "Only use it when the user explicitly asks you to run something in the background. "
        "Always pass the user's original message as the `user_request` parameter."
    ),
}


def _build_system_prompt(
    skills: "list[Skill]",
    spawn_rule: str | None = None,
    system_prompt: str | None = None,
) -> str | None:
    """Build the system prompt combining custom prompt, skill registry, and spawn-rule hint.

    Returns None when all inputs are empty/None.
    """
    parts: list[str] = []

    if system_prompt:
        parts.append(system_prompt)

    if skills:
        lines = ["Available skills:"]
        for skill in skills:
            lines.append(f"- {skill.name}: {skill.description}")
        parts.append("\n".join(lines))

    if spawn_rule is not None:
        hint = _SPAWN_RULE_HINTS.get(spawn_rule)
        if hint:
            parts.append(hint)

    return "\n\n".join(parts) if parts else None


class ClaudeSession:
    """Manages a single Claude conversation via the Claude Agent SDK."""

    def __init__(
        self,
        cwd: str | None = None,
        skills: "list[Skill] | None" = None,
        model: str | None = None,
        plugins: list[dict[str, Any]] | None = None,
        agents: dict[str, AgentDefinition] | None = None,
        search_url: str | None = None,
        background_agent_mcp_url: str | None = None,
        mcp_headers: dict[str, str] | None = None,
        spawn_rule: str | None = None,
        system_prompt: str | None = None,
        tools: list[str] | None = None,
        max_turns: int | None = None,
        reminder: ContextReminder | None = None,
        context_window_overrides: dict[str, int] | None = None,
        disable_thinking: bool = False,
    ) -> None:
        self._cwd = cwd
        self._model = model
        self._system_prompt = system_prompt
        self._tools = tools
        self._max_turns = max_turns
        self._skills: list[Skill] = list(skills) if skills else []
        self._plugins: list[dict[str, Any]] = list(plugins) if plugins else []
        self._agents = agents
        self._search_url = search_url  # None = search disabled; full MCP endpoint URL otherwise
        self._background_agent_mcp_url = background_agent_mcp_url
        self._mcp_headers: dict[str, str] = dict(mcp_headers) if mcp_headers else {}
        self._spawn_rule = spawn_rule
        self._reminder: ContextReminder | None = reminder
        self._context_window_overrides: dict[str, int] | None = dict(context_window_overrides) if context_window_overrides else None
        self._disable_thinking = disable_thinking
        self._pending_skills: list[Skill] = []
        # One-shot context injection — cleared after each send()
        # Each entry is a (text, injection_type, detail) tuple.
        self._pending_context: list[tuple[str, str, str | None]] = []
        self._client: ClaudeSDKClient | None = None
        self._mapper = EventMapper()
        self._connected = False
        # Usage tracking — populated after each completed response
        self._last_usage: dict[str, Any] | None = None
        self._total_cost_usd: float = 0.0
        self._num_turns: int = 0
        self._last_duration_ms: int = 0
        # Cumulative cache_creation across all turns.
        # cache_read_input_tokens is inflated by the number of tool calls per turn
        # (each Anthropic API call within one SDK query reads the full cache), so it
        # must NOT be used for context window size.  cache_creation_input_tokens only
        # increments with genuinely new content, so summing it gives the true context size.
        self._cumulative_cache_creation: int = 0
        # Concurrency guard — prevents two callers from using the SDK client
        # simultaneously.  A second send() while the first is in-flight yields
        # an immediate ErrorEvent instead of silently corrupting the stream.
        self._send_lock: asyncio.Lock = asyncio.Lock()
        # Diagnostics — S14.1
        self._processing: bool = False
        self._last_send_at: float | None = None       # time.monotonic()
        self._last_response_at: float | None = None   # time.monotonic()
        self._send_count: int = 0
        self._event_log: deque[tuple[float, Event]] = deque(maxlen=200)

    async def start(self) -> None:
        """Connect the SDK client and start the Claude process."""
        # Build MCP server configs.
        # Injected per-session via ClaudeAgentOptions — never touches ~/.claude/settings.json.
        # URLs are pre-built from config host+port in gateway._run() so this layer
        # stays decoupled from host/port concerns.
        mcp_servers: dict[str, Any] = {}
        if self._search_url is not None:
            mcp_servers["search"] = {"type": "http", "url": self._search_url}
        if self._background_agent_mcp_url is not None:
            # background_agent_mcp_url is a generic MCP URL parameter despite its name —
            # it registers any MCP server under the 'archon' key.  For the main session
            # it points to the background-agent spawn server; for the router session in
            # Decomposer it points to the history MCP (history_read/history_grep only).
            archon_cfg: dict[str, Any] = {"type": "http", "url": self._background_agent_mcp_url}
            if self._mcp_headers:
                archon_cfg["headers"] = self._mcp_headers
            mcp_servers["archon"] = archon_cfg

        # EnterPlanMode/ExitPlanMode require an interactive TTY dialog that
        # cannot be shown in a headless SDK session.
        # Task is always disabled: the orchestrator must never run sub-agents
        # synchronously via the SDK's native Task tool — that would block the
        # main session's send() for the entire sub-agent duration and prevent
        # the user from sending new messages.  Background agents are always
        # spawned asynchronously via the MCP spawn_background_agent tool instead.
        disallowed: list[str] = ["EnterPlanMode", "ExitPlanMode", "Task"]

        thinking_cfg: dict[str, str] | None = {"type": "disabled"} if self._disable_thinking else None
        options = ClaudeAgentOptions(
            permission_mode="bypassPermissions",
            cwd=self._cwd,
            system_prompt=_build_system_prompt(self._skills, self._spawn_rule, self._system_prompt),
            model=self._model,
            plugins=self._plugins or [],  # type: ignore[arg-type]  # dict matches SdkPluginConfig TypedDict
            agents=self._agents or None,
            disallowed_tools=disallowed,
            mcp_servers=mcp_servers,
            tools=self._tools,
            max_turns=self._max_turns,
            max_buffer_size=10 * 1024 * 1024,
            **({} if thinking_cfg is None else {"thinking": thinking_cfg}),
        )
        self._client = ClaudeSDKClient(options=options)
        # Strip CLAUDECODE so the subprocess isn't rejected as a nested session.
        # _ENV_LOCK serializes concurrent start() calls so two sessions never
        # race on the process-global os.environ during this window.
        async with _get_env_lock():
            claudecode = os.environ.pop("CLAUDECODE", None)
            try:
                await self._client.connect()
            finally:
                if claudecode is not None:
                    os.environ["CLAUDECODE"] = claudecode
        self._connected = True
        logger.info("Claude session started (cwd=%s)", self._cwd)

    def activate_skill(self, skill: "Skill") -> None:
        """Queue a skill for one-shot injection into the next outgoing message."""
        self._pending_skills.append(skill)
        logger.info("Skill queued for next message: %s", skill.name)

    def inject_context(
        self,
        text: str,
        injection_type: str = "context",
        detail: str | None = None,
    ) -> None:
        """Queue context text to be prepended to the next outgoing send() call (one-shot).

        Multiple calls accumulate; all are prepended in order before the user prompt.
        The queue is cleared at the start of each send().

        Args:
            text: The context text to prepend.
            injection_type: Tag identifying the source (e.g. "context", "history", "skill").
            detail: Optional extra detail (e.g. filename) forwarded to the injection event.

        Typical use: background agent completion results injected by
        BackgroundAgentManager so the main session receives the output as context.
        """
        self._pending_context.append((text, injection_type, detail))
        logger.debug("Context queued for next message (%d chars)", len(text))

    def flush_pending_context(self) -> None:
        """Discard queued context (called when main session is not used for this message)."""
        if self._pending_context:
            logger.debug("Flushing %d pending context entries", len(self._pending_context))
            self._pending_context.clear()

    async def send(self, prompt: str) -> AsyncGenerator[Event, None]:
        """Send a prompt and yield typed archon events for the response.

        If skills are queued via activate_skill(), their full bodies are prepended
        as labelled context blocks before the user prompt (one-shot injection).
        The queue is cleared after the first send.
        """
        if self._client is None or not self._connected:
            raise RuntimeError("Session not started")

        # Concurrency guard: wait for any in-flight send() to finish before
        # starting a new one.  The SDK client is not reentrant — two concurrent
        # query() calls would corrupt the response stream.  By awaiting the lock
        # we queue the new request so it runs immediately after the previous one
        # completes, instead of returning an error to the user (Bug.005).
        await self._send_lock.acquire()
        # Diagnostics: mark the start of processing before any yields.
        self._processing = True
        self._last_send_at = time.monotonic()
        self._send_count += 1
        intercept_gen = None  # assigned inside try; checked in finally for drain

        try:
            # Reset usage tracking for this turn so stale data from a previous
            # successful turn is not exposed when this send() fails before its
            # own ResultMessage arrives (prevents double-counting in handler.py).
            self._last_usage = None
            # Guard: True only after client.query(full_prompt) succeeds.
            # record_message/record_tokens must not fire if the user message
            # was never actually sent (e.g. reminder injection raised first).
            _user_message_queued = False

            # Inject context reminder as a separate SDK turn before the user prompt.
            if self._reminder is not None and self._reminder.should_inject():
                msg_count = self._reminder.message_count
                reminder_msg = self._reminder.build_reminder_message()
                logger.info(
                    "Injecting REMINDER.md into session (triggered at message %d)", msg_count
                )
                await self._client.query(reminder_msg)
                async for _msg in self._client.receive_response():
                    if isinstance(_msg, _ResultMessage):
                        if _msg.total_cost_usd is not None:
                            self._total_cost_usd += _msg.total_cost_usd
                        if _msg.usage:
                            # Note: repeated reminder injections incrementally inflate
                            # _cumulative_cache_creation by the reminder file's token size on every
                            # injection cycle. This is intentional — the reminder IS genuine new content
                            # written to the KV cache each time. However, it means the context window
                            # progress bar advances faster than user content alone would suggest.
                            self._cumulative_cache_creation += (
                                _msg.usage.get("cache_creation_input_tokens") or 0
                            )
                yield ReminderInjectedEvent(message_count=msg_count)

            # Build the full prompt by prepending context blocks then skill blocks.
            # Order: [context blocks] → [skill blocks] → [user prompt]
            prefix_parts: list[str] = []

            for text, injection_type, detail in self._pending_context:
                prefix_parts.append(text)
                yield ContextInjectedEvent(injection_type=injection_type, size_chars=len(text), detail=detail)
            self._pending_context.clear()

            for s in self._pending_skills:
                skill_block = f"[Skill: {s.name}]\n{s.content}\n[End Skill: {s.name}]"
                prefix_parts.append(skill_block)
                yield SkillInjectedEvent(skill_name=s.name, size_chars=len(skill_block))
            self._pending_skills.clear()

            if prefix_parts:
                full_prompt = "\n\n".join(prefix_parts) + "\n\n" + prompt
            else:
                full_prompt = prompt

            await self._client.query(full_prompt)
            _user_message_queued = True  # mark AFTER user query succeeds

            async def _intercept() -> AsyncGenerator[Any, None]:
                """Yield raw SDK messages, capturing ResultMessage metadata as a side-effect."""
                async for msg in self._client.receive_response():  # type: ignore[union-attr]
                    if isinstance(msg, _ResultMessage):
                        self._last_usage = msg.usage
                        if msg.total_cost_usd is not None:
                            self._total_cost_usd += msg.total_cost_usd
                        self._num_turns = msg.num_turns
                        self._last_duration_ms = msg.duration_ms
                        if msg.usage:
                            self._cumulative_cache_creation += (
                                msg.usage.get("cache_creation_input_tokens") or 0
                            )
                    yield msg

            intercept_gen = _intercept()
            async for event in self._mapper.map_messages(intercept_gen):
                # Log event; mark response time for terminal event types.
                self._event_log.append((time.monotonic(), event))
                if isinstance(event, (Response, ErrorEvent)):
                    self._last_response_at = time.monotonic()
                yield event
        finally:
            # Nested try/finally ensures _processing and lock release run even on
            # CancelledError (a BaseException in Python 3.9+), which is NOT caught
            # by the inner except clauses below.  Without this nesting, a cancellation
            # during the drain's await would skip the lock release permanently (BUG-13).
            try:
                # Drain any remaining SDK messages to ensure the ResultMessage
                # side-effects (usage stats) are captured even when the consumer
                # exits early (e.g. task promotion in _task_direct_monitored).
                # Timeout guards against a slow or hung SDK stream holding the lock.
                if intercept_gen is not None:
                    try:
                        async def _drain() -> None:
                            async for _ in intercept_gen:
                                pass

                        await asyncio.wait_for(_drain(), timeout=5.0)
                    except asyncio.TimeoutError:
                        logger.warning(
                            "Generator drain timed out after 5s — ResultMessage metadata"
                            " may not have been captured for this turn"
                        )
                        # Close the generator to release underlying resources
                        # (avoids async generator leak when the SDK stream is still running)
                        try:
                            await asyncio.wait_for(intercept_gen.aclose(), timeout=2.0)
                        except Exception:
                            logger.warning(
                                "intercept_gen.aclose() timed out or failed — subprocess may be"
                                " orphaned; will be cleaned up on next stop()",
                                exc_info=True,
                            )
                    except Exception:
                        pass  # generator already closed; nothing to drain
            finally:
                # GUARANTEED to run even on CancelledError / BaseException.
                # Reminder tracking runs before lock release as a defensive ordering:
                # if record_message/record_tokens ever become async, they must complete
                # before the next send() starts, or the _last_usage reset at the top of
                # the try block could race with reading it here.
                if self._reminder is not None and _user_message_queued:
                    self._reminder.record_message()
                    if self._last_usage is not None:
                        usage = self._last_usage
                        # Tracks conversational activity only: input_tokens (non-cached
                        # user input) + output_tokens.  cache_creation_input_tokens is
                        # excluded because the cold-cache first turn writes the entire
                        # system prompt to cache (~20-50K+), which would blow the
                        # threshold after 1-2 turns before any real context drift.
                        input_t = usage.get("input_tokens") or 0
                        output_t = usage.get("output_tokens") or 0
                        delta = input_t + output_t
                        logger.debug(
                            "Reminder token delta: %d (input=%d, output=%d, cc=%d)",
                            delta, input_t, output_t,
                            usage.get("cache_creation_input_tokens") or 0,
                        )
                        self._reminder.record_tokens(delta)
                self._processing = False
                if self._send_lock.locked():
                    self._send_lock.release()

    def force_kill_for_recovery(self) -> None:
        """Kill subprocess and reset state without anyio operations.

        Called during tool-count promotion when the SDK generator is
        abandoned.  Normal disconnect/close use anyio internally and
        would fail due to stale cancel scopes on the current task.
        """
        self._processing = False
        if self._send_lock.locked():
            self._send_lock.release()
        # Create a fresh lock so the abandoned send() generator's finally block
        # (which runs via GC) releases the OLD lock, not this new one.
        self._send_lock = asyncio.Lock()
        # Kill the subprocess via OS signal — completely bypasses anyio.
        if self._client is not None:
            transport = getattr(self._client, "_transport", None)
            if transport is not None:
                proc = getattr(transport, "_process", None)
                if proc is not None:
                    pid = getattr(proc, "pid", None)
                    if pid is not None:
                        try:
                            os.kill(pid, 9)  # SIGKILL
                        except (ProcessLookupError, OSError):
                            pass
        # Abandon old client — its anyio state dies with it.
        self._client = None
        self._connected = False

    async def stop(self) -> None:
        """Disconnect the SDK client."""
        if self._client is not None and self._connected:
            try:
                await self._client.disconnect()
            except (Exception, asyncio.CancelledError) as exc:
                # disconnect() can fail for various reasons (CancelledError or RuntimeError
                # from anyio cancel scope, OSError, anyio.ClosedResourceError, etc.).  For
                # any failure, fall back to closing the transport directly so the subprocess
                # is terminated.
                logger.warning("Session disconnect skipped: %s", exc)
                transport = getattr(self._client, "_transport", None)
                if transport is not None:
                    try:
                        await transport.close()
                    except (Exception, asyncio.CancelledError) as transport_exc:
                        logger.debug("Transport close after disconnect failure: %s", transport_exc)
                        # Last resort: force-kill the subprocess via OS signal.
                        proc = getattr(transport, "_process", None)
                        if proc is not None:
                            pid = getattr(proc, "pid", None)
                            if pid is not None:
                                try:
                                    os.kill(pid, 9)  # SIGKILL
                                except (ProcessLookupError, OSError):
                                    pass
                # Clear pending asyncio cancellation left by anyio cancel scopes
                # so subsequent awaits in this task are not affected.
                task = asyncio.current_task()
                if task is not None:
                    while task.cancelling() > 0:
                        task.uncancel()
            finally:
                self._connected = False
            logger.info("Claude session stopped")

    @property
    def model(self) -> str | None:
        """The model override passed to this session, or None for SDK default."""
        return self._model

    @property
    def reminder(self) -> ContextReminder | None:
        """The ContextReminder attached to this session, or None if not configured."""
        return self._reminder

    @property
    def is_alive(self) -> bool:
        """True if the session is connected."""
        return self._connected

    @property
    def usage_stats(self) -> dict[str, Any] | None:
        """Return a usage snapshot from the last response, or None if no response yet.

        Keys:
          usage           — raw token dict from the SDK (input_tokens, output_tokens, …)
          total_cost_usd  — accumulated cost across all turns in this session
          num_turns       — turn count from the most recent ResultMessage
          last_duration_ms — wall-clock duration of the most recent turn
        """
        if self._last_usage is None:
            return None
        return {
            "usage": self._last_usage,
            "cumulative_cache_creation": self._cumulative_cache_creation,
            "total_cost_usd": self._total_cost_usd,
            "num_turns": self._num_turns,
            "last_duration_ms": self._last_duration_ms,
            # user_turns counts send() calls on THIS session instance.
            # For the main session (Decomposer._session), this is the number of
            # messages answered directly via answer().  Messages routed through
            # route_task() use _router_session and do NOT increment this counter.
            "user_turns": self._send_count,
            "context_window": get_context_window(self._model, self._context_window_overrides),
        }

    def context_percentage(self) -> int:
        """Return the estimated context window usage as a percentage (0–100+, not clamped)."""
        stats = self.usage_stats
        if stats is None:
            return 0
        usage = stats.get("usage") or {}
        input_t = usage.get("input_tokens") or 0
        cumul_cc = stats.get("cumulative_cache_creation") or 0
        context_window = stats["context_window"]
        if not context_window:
            return 0
        return round(100 * (cumul_cc + input_t) / context_window)

    # ── Diagnostics — S14.1 ────────────────────────────────────────

    @property
    def is_processing(self) -> bool:
        """True while send() is being iterated (a request is in flight)."""
        return self._processing

    @property
    def processing_seconds(self) -> float | None:
        """Seconds elapsed since send() was called; None when not processing."""
        if not self._processing or self._last_send_at is None:
            return None
        return time.monotonic() - self._last_send_at

    @property
    def idle_seconds(self) -> float | None:
        """Seconds since the last Response/ErrorEvent; None if never responded."""
        if self._last_response_at is None:
            return None
        return time.monotonic() - self._last_response_at

    @property
    def send_count(self) -> int:
        """Total number of send() calls that started executing in this session."""
        return self._send_count

    def recent_events(self, n: int = 20) -> list[tuple[float, Event]]:
        """Return the last n (timestamp, event) pairs from the event log.

        Returns an empty list when n <= 0 or the log is empty.
        """
        if n <= 0:
            return []
        log = list(self._event_log)
        return log[-n:]

    @property
    def diagnostics(self) -> dict[str, Any]:
        """Comprehensive state snapshot for debugging and the /status command."""
        now = time.monotonic()
        return {
            "is_alive": self._connected,
            "is_processing": self._processing,
            "processing_seconds": (now - self._last_send_at)
                if self._processing and self._last_send_at is not None else None,
            "idle_seconds": (now - self._last_response_at)
                if self._last_response_at is not None else None,
            "send_count": self._send_count,
            "recent_events": self.recent_events(10),
            "usage_stats": self.usage_stats,
        }
