"""Claude session — wraps ClaudeSDKClient to provide typed event streaming."""
import asyncio
import logging
import os
import random
import time
from collections import deque
from typing import TYPE_CHECKING, Any, AsyncGenerator

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, ClaudeSDKClient, HookMatcher
from claude_agent_sdk import ResultMessage as _ResultMessage

from archon.ai.event_mapper import (
    ErrorEvent,
    Event,
    EventMapper,
    Response,
    SubagentStarted,
    SubagentStopped,
)

if TYPE_CHECKING:
    from archon.ai.skill_loader import Skill

logger = logging.getLogger("archon")

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
        "context injected into your next message."
    ),
    "auto": (
        "You have access to a `spawn_background_agent` MCP tool. "
        "Use it when running a long task in the background would keep the main "
        "conversation more responsive."
    ),
    "manual": (
        "You have access to a `spawn_background_agent` MCP tool. "
        "Only use it when the user explicitly asks you to run something in the background."
    ),
}


def _build_system_prompt(
    skills: "list[Skill]",
    spawn_rule: str | None = None,
) -> str | None:
    """Build the system prompt combining the skill registry and spawn-rule hint.

    Returns None when both the skill list is empty and spawn_rule is None.
    """
    parts: list[str] = []

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
        plugins: list[dict] | None = None,
        agents: dict[str, AgentDefinition] | None = None,
        qmd_url: str | None = None,
        background_agent_mcp_url: str | None = None,
        spawn_rule: str | None = None,
    ) -> None:
        self._cwd = cwd
        self._model = model
        self._skills: list[Skill] = list(skills) if skills else []
        self._plugins: list[dict] = list(plugins) if plugins else []
        self._agents = agents
        self._qmd_url = qmd_url  # None = QMD disabled; full MCP endpoint URL otherwise
        self._background_agent_mcp_url = background_agent_mcp_url
        self._spawn_rule = spawn_rule
        self._pending_skills: list[Skill] = []
        # One-shot context injection — cleared after each send()
        self._pending_context: list[str] = []
        self._client: ClaudeSDKClient | None = None
        self._mapper = EventMapper()
        self._connected = False
        # Side-channel queue: subagent hook callbacks push events here;
        # send() drains them between regular SDK events.
        self._hook_queue: asyncio.Queue[Event] = asyncio.Queue()
        # FR.001: name registry — maps agent_id → assigned human-readable name.
        self._active_agent_names: dict[str, str] = {}
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

    def _build_hooks(self) -> dict:
        """Create SubagentStart/Stop hook matchers that push events into the hook queue."""
        queue = self._hook_queue
        session = self  # captured so closures can call name-registry methods

        async def _on_subagent_start(hook_input: Any, tool_use_id: str | None, ctx: Any) -> dict:
            agent_id   = hook_input.get("agent_id", "")
            agent_type = hook_input.get("agent_type", "")
            agent_name = session._assign_agent_name(agent_id)
            queue.put_nowait(SubagentStarted(
                agent_id=agent_id,
                agent_type=agent_type,
                agent_name=agent_name,
            ))
            return {"continue_": True}

        async def _on_subagent_stop(hook_input: Any, tool_use_id: str | None, ctx: Any) -> dict:
            agent_id   = hook_input.get("agent_id", "")
            agent_type = hook_input.get("agent_type", "")
            agent_name = session._release_agent_name(agent_id) or ""
            queue.put_nowait(SubagentStopped(
                agent_id=agent_id,
                agent_type=agent_type,
                agent_name=agent_name,
            ))
            return {"continue_": True}

        return {
            "SubagentStart": [HookMatcher(hooks=[_on_subagent_start])],
            "SubagentStop":  [HookMatcher(hooks=[_on_subagent_stop])],
        }

    def _assign_agent_name(self, agent_id: str) -> str:
        """Assign a unique human-readable name to agent_id from the pool.

        Idempotent: returns the existing name if already assigned.
        Falls back to a truncated agent_id when the pool is exhausted.
        """
        if agent_id in self._active_agent_names:
            return self._active_agent_names[agent_id]
        in_use = set(self._active_agent_names.values())
        available = [n for n in _AGENT_NAMES if n not in in_use]
        name = random.choice(available) if available else (agent_id[:8] or "Agent")
        self._active_agent_names[agent_id] = name
        return name

    def _release_agent_name(self, agent_id: str) -> str | None:
        """Release the name assigned to agent_id, making it available again.

        Returns the released name, or None if the agent_id was not registered.
        """
        return self._active_agent_names.pop(agent_id, None)

    async def start(self) -> None:
        """Connect the SDK client and start the Claude process."""
        # Build MCP server configs.
        # Injected per-session via ClaudeAgentOptions — never touches ~/.claude/settings.json.
        # URLs are pre-built from config host+port in gateway._run() so this layer
        # stays decoupled from host/port concerns.
        mcp_servers: dict = {}
        if self._qmd_url is not None:
            mcp_servers["qmd"] = {"type": "http", "url": self._qmd_url}
        if self._background_agent_mcp_url is not None:
            mcp_servers["archon"] = {"type": "http", "url": self._background_agent_mcp_url}

        # EnterPlanMode/ExitPlanMode require an interactive TTY dialog that
        # cannot be shown in a headless SDK session.
        # When background agents are enabled, also disable the native Task tool so
        # Claude uses the MCP spawn_background_agent route instead.
        disallowed: list[str] = ["EnterPlanMode", "ExitPlanMode"]
        if self._background_agent_mcp_url is not None:
            disallowed.append("Task")

        options = ClaudeAgentOptions(
            permission_mode="bypassPermissions",
            cwd=self._cwd,
            system_prompt=_build_system_prompt(self._skills, self._spawn_rule),
            model=self._model,
            plugins=self._plugins or [],
            hooks=self._build_hooks(),
            agents=self._agents or None,
            disallowed_tools=disallowed,
            mcp_servers=mcp_servers,
        )
        self._client = ClaudeSDKClient(options=options)
        # Strip CLAUDECODE so the subprocess isn't rejected as a nested session
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

    def inject_context(self, text: str) -> None:
        """Queue context text to be prepended to the next outgoing send() call (one-shot).

        Multiple calls accumulate; all are prepended in order before the user prompt.
        The queue is cleared at the start of each send().

        Typical use: background agent completion results injected by
        BackgroundAgentManager so the main session receives the output as context.
        """
        self._pending_context.append(text)
        logger.debug("Context queued for next message (%d chars)", len(text))

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

        try:
            # Build the full prompt by prepending context blocks then skill blocks.
            # Order: [context blocks] → [skill blocks] → [user prompt]
            prefix_parts: list[str] = []

            if self._pending_context:
                prefix_parts.append("\n\n".join(self._pending_context))
                self._pending_context.clear()

            if self._pending_skills:
                skill_blocks = "\n\n".join(
                    f"[Skill: {s.name}]\n{s.content}\n[End Skill: {s.name}]"
                    for s in self._pending_skills
                )
                prefix_parts.append(skill_blocks)
                self._pending_skills.clear()

            if prefix_parts:
                full_prompt = "\n\n".join(prefix_parts) + "\n\n" + prompt
            else:
                full_prompt = prompt

            # Discard any stale hook events left over from the previous send().
            while not self._hook_queue.empty():
                self._hook_queue.get_nowait()

            await self._client.query(full_prompt)

            async def _intercept():
                """Yield raw SDK messages, capturing ResultMessage metadata as a side-effect."""
                async for msg in self._client.receive_response():  # type: ignore[union-attr]
                    if isinstance(msg, _ResultMessage):
                        self._last_usage = msg.usage
                        if msg.total_cost_usd is not None:
                            self._total_cost_usd += msg.total_cost_usd
                        self._num_turns = msg.num_turns
                        self._last_duration_ms = msg.duration_ms
                        if msg.usage:
                            self._cumulative_cache_creation += msg.usage.get(
                                "cache_creation_input_tokens", 0
                            )
                    yield msg

            async for event in self._mapper.map_messages(_intercept()):
                # Drain subagent hook events that arrived before this SDK-derived event.
                while not self._hook_queue.empty():
                    hook_event = self._hook_queue.get_nowait()
                    self._event_log.append((time.monotonic(), hook_event))
                    yield hook_event
                # Log event; mark response time for terminal event types.
                self._event_log.append((time.monotonic(), event))
                if isinstance(event, (Response, ErrorEvent)):
                    self._last_response_at = time.monotonic()
                yield event

            # Final drain — catch any hook events that fired after the last SDK message.
            while not self._hook_queue.empty():
                hook_event = self._hook_queue.get_nowait()
                self._event_log.append((time.monotonic(), hook_event))
                yield hook_event
        finally:
            # Reset processing flag and release the send lock regardless of how
            # the generator exits (normal completion, early break, or exception).
            self._processing = False
            self._send_lock.release()

    async def stop(self) -> None:
        """Disconnect the SDK client."""
        if self._client is not None and self._connected:
            try:
                await self._client.disconnect()
            except RuntimeError as exc:
                # anyio cancel scope can't be exited from a different task during shutdown
                logger.warning("Session disconnect skipped: %s", exc)
            finally:
                self._connected = False
            logger.info("Claude session stopped")

    @property
    def model(self) -> str | None:
        """The model override passed to this session, or None for SDK default."""
        return self._model

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
        }

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

    def is_stuck(self, threshold_seconds: float = 120.0) -> bool:
        """Return True if currently processing and duration exceeds threshold_seconds."""
        if not self._processing or self._last_send_at is None:
            return False
        return (time.monotonic() - self._last_send_at) > threshold_seconds

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
