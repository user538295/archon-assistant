"""ArchonToolkit — central registry for Archon control-plane MCP tools.

Provides a unified call_tool() dispatcher with audit logging and optional
event_callback for session history integration. MCP servers (ArchonMCPServer,
ArchonOrchestratorMCPServer) delegate registered toolkit tool calls here.

Tools are registered via register_tool() — future tasks add real tools;
this module is the scaffold.
"""
import json
import logging
import math
import time
from pathlib import Path
from typing import Any, Callable, Awaitable

from archon.ai.event_mapper import ToolStarted, ToolResult

logger = logging.getLogger("archon")

_MAX_LOG_ARGS_LEN = 200
_MAX_LOG_RESULT_LEN = 200

_ARCHON_STATUS_SCHEMA: dict[str, Any] = {
    "name": "archon_status",
    "description": (
        "Check Archon daemon health and state — uptime, active sessions, "
        "running agents, model, notification mode."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {},
    },
}


_LIST_RUNNING_AGENTS_SCHEMA: dict[str, Any] = {
    "name": "list_running_agents",
    "description": (
        "List background agents. Without a name filter, shows only running agents. "
        "With a name filter, searches all agents (including completed) by name."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Filter by agent name, e.g. 'Atlas'. "
                    "Searches all agents including completed ones."
                ),
            },
        },
    },
}


_GET_AGENT_STATUS_SCHEMA: dict[str, Any] = {
    "name": "get_agent_status",
    "description": "Get detailed status of a specific background agent by run_id.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "run_id": {
                "type": "string",
                "description": "The agent's run_id (UUID hex string)",
            },
        },
        "required": ["run_id"],
    },
}


_CANCEL_AGENT_SCHEMA: dict[str, Any] = {
    "name": "cancel_agent",
    "description": "Cancel a running background agent by run_id.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "run_id": {
                "type": "string",
                "description": "The agent's run_id to cancel",
            },
        },
        "required": ["run_id"],
    },
}


_READ_AGENT_LOG_SCHEMA: dict[str, Any] = {
    "name": "read_agent_log",
    "description": "Read the last N lines of a background agent's Markdown log file.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "run_id": {
                "type": "string",
                "description": "The agent's run_id",
            },
            "tail_lines": {
                "type": "integer",
                "description": "Number of lines to return from the end of the log (1–500, default 100)",
                "default": 100,
            },
        },
        "required": ["run_id"],
    },
}


_GET_AGENT_BY_NAME_SCHEMA: dict[str, Any] = {
    "name": "get_agent_by_name",
    "description": (
        "Get full details of a background agent by name. "
        "If multiple agents share the same name, returns the most recent one."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Agent name like 'Atlas', 'Nova', etc.",
            },
        },
        "required": ["name"],
    },
}


_GET_SESSION_STATUS_SCHEMA: dict[str, Any] = {
    "name": "get_session_status",
    "description": "Get the current status of a user's active Claude session.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "user_id": {
                "type": "integer",
                "description": "The Telegram user ID whose session to query",
            },
        },
        "required": ["user_id"],
    },
}


_GET_CONTEXT_STATS_SCHEMA: dict[str, Any] = {
    "name": "get_context_stats",
    "description": "Get token usage, cost, and turn statistics for a user's active Claude session.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "user_id": {
                "type": "integer",
                "description": "Telegram user ID to query",
            },
        },
        "required": ["user_id"],
    },
}


_SEND_NOTIFICATION_SCHEMA: dict[str, Any] = {
    "name": "send_notification",
    "description": (
        "Send a Telegram notification message to a user. "
        "Rate-limited to one message per user per 10 seconds."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "user_id": {
                "type": "integer",
                "description": "The Telegram user ID to send the notification to",
            },
            "message": {
                "type": "string",
                "description": "The message text to send (max 4000 chars; longer messages are truncated)",
            },
        },
        "required": ["user_id", "message"],
    },
}


_ARCHON_RESTART_SCHEMA: dict[str, Any] = {
    "name": "archon_restart",
    "description": (
        "Schedule a safe graceful restart of the Archon daemon. "
        "The restart happens after a configurable delay."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Why the restart is needed",
            },
            "delay_seconds": {
                "type": "number",
                "description": "Delay before restart in seconds (2-60, default 5)",
                "default": 5.0,
            },
        },
        "required": ["reason"],
    },
}


class ArchonToolkit:
    """Central registry and dispatcher for Archon control-plane tools."""

    def __init__(
        self,
        *,
        session_manager: Any = None,
        bg_manager: Any = None,
        restart_coordinator: Any = None,
        bot: Any = None,
        config: Any = None,
        skill_loader: Any = None,
        job_scheduler: Any = None,
        gateway_started_at: float | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._session_manager = session_manager
        self._bg_manager = bg_manager
        self._restart_coordinator = restart_coordinator
        self._bot = bot
        self._config = config
        self._skill_loader = skill_loader
        self._job_scheduler = job_scheduler
        self._gateway_started_at = gateway_started_at
        self._clock: Callable[[], float] = clock if clock is not None else time.monotonic
        self._notification_last_sent: dict[int, float] = {}

        # Instance attributes — each instance has its own independent list/set
        self.tool_definitions: list[dict[str, Any]] = []
        self.tool_names: set[str] = set()
        self._handlers: dict[
            str, Callable[..., Awaitable[str]]
        ] = {}

        # Register built-in tools
        self.register_tool(
            "archon_status",
            _ARCHON_STATUS_SCHEMA,
            self._handle_archon_status,
        )
        self.register_tool(
            "list_running_agents",
            _LIST_RUNNING_AGENTS_SCHEMA,
            self._handle_list_running_agents,
        )
        self.register_tool(
            "get_agent_status",
            _GET_AGENT_STATUS_SCHEMA,
            self._handle_get_agent_status,
        )
        self.register_tool(
            "cancel_agent",
            _CANCEL_AGENT_SCHEMA,
            self._handle_cancel_agent,
        )
        self.register_tool(
            "archon_restart",
            _ARCHON_RESTART_SCHEMA,
            self._handle_archon_restart,
        )
        self.register_tool(
            "read_agent_log",
            _READ_AGENT_LOG_SCHEMA,
            self._handle_read_agent_log,
        )
        self.register_tool(
            "get_agent_by_name",
            _GET_AGENT_BY_NAME_SCHEMA,
            self._handle_get_agent_by_name,
        )
        self.register_tool(
            "get_session_status",
            _GET_SESSION_STATUS_SCHEMA,
            self._handle_get_session_status,
        )
        self.register_tool(
            "get_context_stats",
            _GET_CONTEXT_STATS_SCHEMA,
            self._handle_get_context_stats,
        )
        self.register_tool(
            "send_notification",
            _SEND_NOTIFICATION_SCHEMA,
            self._handle_send_notification,
        )

    def set_late_deps(
        self,
        *,
        session_manager: Any = None,
        bg_manager: Any = None,
        job_scheduler: Any = None,
    ) -> None:
        """Set dependencies that are only available after initial construction.

        Uses the same late-wiring pattern as ArchonMCPServer.set_manager().
        """
        if session_manager is not None:
            self._session_manager = session_manager
        if bg_manager is not None:
            self._bg_manager = bg_manager
        if job_scheduler is not None:
            self._job_scheduler = job_scheduler

    def register_tool(
        self,
        name: str,
        schema: dict[str, Any],
        handler: Callable[..., Awaitable[str]],
    ) -> None:
        """Register a tool with its schema and async handler."""
        if name in self.tool_names:
            raise ValueError(f"Tool already registered: {name!r}")
        self.tool_definitions.append(schema)
        self.tool_names.add(name)
        self._handlers[name] = handler

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        user_id: int | None = None,
        event_callback: Callable[..., Awaitable[None]] | None = None,
    ) -> str:
        """Dispatch a tool call by name.

        Raises ValueError for unknown tool names.
        Logs every call at INFO level for audit purposes.
        Emits ToolStarted/ToolResult via event_callback if provided.
        """
        truncated_args = _truncate(json.dumps(arguments, default=str), _MAX_LOG_ARGS_LEN)
        logger.info("MCP tool call: %s(%s) by user=%s", name, truncated_args, user_id)

        handler = self._handlers.get(name)
        if handler is None:
            raise ValueError(f"Unknown tool: {name!r}")

        # Emit ToolStarted before execution
        if event_callback is not None:
            await event_callback(ToolStarted(name=name, input=truncated_args))

        try:
            result = await handler(arguments, user_id=user_id)
        except Exception as exc:
            logger.error("MCP tool %s failed: %s", name, exc)
            if event_callback is not None:
                await event_callback(ToolResult(content=f"Error: {exc}", tool_name=name, is_error=True))
            raise

        # Emit ToolResult after execution
        if event_callback is not None:
            truncated_result = _truncate(result, _MAX_LOG_RESULT_LEN)
            await event_callback(ToolResult(content=truncated_result, tool_name=name))

        return result

    # ── Built-in tool handlers ────────────────────────────────────

    async def _handle_list_running_agents(
        self, arguments: dict[str, Any], *, user_id: int | None = None,
    ) -> str:
        """List background agents, optionally filtered by name."""
        if self._bg_manager is None:
            raise RuntimeError("bg_manager not available")
        # list_running/list_all require user_id (BAM filters by user).
        # get_agent_status doesn't need it (lookup by run_id).
        if user_id is None:
            return "No user context available."

        name_filter: str | None = arguments.get("name")

        if name_filter is not None:
            agents = self._bg_manager.list_all(user_id)
            agents = [a for a in agents if a.name.lower() == name_filter.lower()]
            if not agents:
                return f"No agent named '{name_filter}' found."
        else:
            agents = self._bg_manager.list_running(user_id)
            if not agents:
                return "No running agents."

        now = time.monotonic()
        result = [
            {
                "run_id": a.run_id,
                "name": a.name,
                "task_summary": a.task[:100],
                "age_seconds": round(now - a.started_at, 1),
                "status": a.status,
            }
            for a in agents
        ]
        return json.dumps(result)

    async def _handle_get_agent_status(
        self, arguments: dict[str, Any], *, user_id: int | None = None,
    ) -> str:
        """Return detailed status of a specific agent by run_id."""
        if self._bg_manager is None:
            raise RuntimeError("bg_manager not available")

        run_id: str = arguments["run_id"]
        run = self._bg_manager.get_run(run_id)

        if run is None:
            return f"Agent {run_id} not found."

        if user_id is not None and run.user_id != user_id:
            return f"Agent {run_id} not found."

        return json.dumps({
            "run_id": run.run_id,
            "name": run.name,
            "status": run.status,
            "task_summary": run.task[:100],
            "age_seconds": round(time.monotonic() - run.started_at, 1),
            "result": run.result,
            "error": run.error,
            "log_path": str(run.log_path) if run.log_path is not None else None,
        })

    async def _handle_cancel_agent(
        self, arguments: dict[str, Any], *, user_id: int | None = None,
    ) -> str:
        """Cancel a running background agent by run_id."""
        if self._bg_manager is None:
            raise RuntimeError("bg_manager not available")

        run_id: str = arguments["run_id"]

        # User-scoped authorization check
        if user_id is not None:
            run = self._bg_manager.get_run(run_id)
            if run is None or run.user_id != user_id:
                return f"Agent {run_id} not found."

        cancelled: bool = await self._bg_manager.cancel(run_id)
        if cancelled:
            return f"Agent {run_id} cancelled."
        return f"Agent {run_id} not found or already finished."

    async def _handle_archon_status(
        self, arguments: dict[str, Any], *, user_id: int | None = None,
    ) -> str:
        """Return daemon status as a JSON string."""
        if self._gateway_started_at is not None:
            uptime = time.monotonic() - self._gateway_started_at
        else:
            uptime = 0

        if self._session_manager is not None:
            processing_sessions = len(self._session_manager.processing_sessions())
        else:
            processing_sessions = 0

        if self._bg_manager is not None and user_id is not None:
            running_agents = len(self._bg_manager.list_running(user_id))
        else:
            running_agents = 0

        if self._config is not None:
            notification_mode = self._config.notifications.mode
        else:
            notification_mode = "unknown"

        if self._session_manager is not None:
            model = self._session_manager.get_model() or "default"
        else:
            model = "unknown"

        if self._restart_coordinator is not None:
            restart_scheduled = self._restart_coordinator.is_scheduled
        else:
            restart_scheduled = False

        return json.dumps({
            "uptime_seconds": round(uptime, 2),
            "processing_sessions": processing_sessions,
            "running_agents": running_agents,
            "notification_mode": notification_mode,
            "model": model,
            "restart_scheduled": restart_scheduled,
        })

    async def _handle_archon_restart(
        self, arguments: dict[str, Any], *, user_id: int | None = None,
    ) -> str:
        """Schedule a graceful daemon restart."""
        if self._restart_coordinator is None:
            raise RuntimeError("restart_coordinator not available")

        reason: str = arguments["reason"]
        delay: float = float(arguments.get("delay_seconds", 5.0))
        delay = max(2.0, min(60.0, delay))

        restart_file = Path.home() / ".archon" / ".last_restart"
        if not self._restart_coordinator.check_restart_allowed(restart_file):
            return "Restart denied: last restart was less than 60s ago."

        try:
            self._restart_coordinator.schedule(reason, delay)
        except RuntimeError:
            return "Restart already scheduled."

        return f"Restart scheduled in {delay}s. Reason: {reason}"

    async def _handle_read_agent_log(
        self, arguments: dict[str, Any], *, user_id: int | None = None,
    ) -> str:
        """Return the last tail_lines lines of an agent's Markdown log file."""
        if self._bg_manager is None:
            raise RuntimeError("bg_manager not available")

        run_id: str = arguments["run_id"]
        tail_lines: int = max(1, min(500, int(arguments.get("tail_lines", 100))))

        run = self._bg_manager.get_run(run_id)

        if run is None:
            return f"Agent {run_id} not found."

        if user_id is not None and run.user_id != user_id:
            return f"Agent {run_id} not found."

        log_path: Path | None = run.log_path
        if log_path is None:
            return f"Agent {run_id} log not available."

        # Security: reject symlinks
        if log_path.is_symlink():
            return "Invalid log path: symlinks are not allowed."

        # Security: resolve and verify the path is inside the sessions directory
        resolved = log_path.resolve()
        sessions_dir = self._sessions_dir()
        if sessions_dir is None:
            return "Cannot read log: configuration not available."
        try:
            resolved.relative_to(sessions_dir)
        except ValueError:
            return "Invalid log path: outside sessions directory."

        if not resolved.exists():
            return f"Agent {run_id} log file not found."

        try:
            content = resolved.read_text(encoding="utf-8")
        except OSError as e:
            return f"Failed to read log: {e.strerror}"
        except UnicodeDecodeError:
            return "Failed to read log: file contains non-UTF-8 content"

        lines = content.splitlines()
        tail = lines[-tail_lines:]
        return "\n".join(tail)

    async def _handle_get_agent_by_name(
        self, arguments: dict[str, Any], *, user_id: int | None = None,
    ) -> str:
        """Return full details of the most recent agent matching name."""
        if user_id is None:
            return "No user context available."

        if self._bg_manager is None:
            raise RuntimeError("bg_manager not available")

        name: str = arguments["name"]
        agents = self._bg_manager.list_all(user_id)
        matches = [a for a in agents if a.name.lower() == name.lower()]

        if not matches:
            return f"No agent named '{name}' found."

        # Most recent = highest started_at
        run = max(matches, key=lambda a: a.started_at)

        return json.dumps({
            "run_id": run.run_id,
            "name": run.name,
            "status": run.status,
            "age_seconds": round(time.monotonic() - run.started_at, 1),
            "task": run.task,
            "context": run.context,
            "user_request": run.user_request,
            "result": run.result,
            "error": run.error,
            "log_path": str(run.log_path) if run.log_path is not None else None,
        })

    async def _handle_get_session_status(
        self, arguments: dict[str, Any], *, user_id: int | None = None,
    ) -> str:
        """Return status of a user's active Claude session."""
        if self._session_manager is None:
            raise RuntimeError("session_manager not available")

        try:
            target_user_id = int(arguments["user_id"])
        except (KeyError, ValueError, TypeError):
            return "Invalid user_id argument."

        if user_id is not None and target_user_id != user_id:
            return f"No active session for user {target_user_id}."
        diagnostics = self._session_manager.session_diagnostics(target_user_id)

        if diagnostics is None:
            return f"No active session for user {target_user_id}."

        return json.dumps({
            "is_processing": diagnostics["is_processing"],
            "processing_seconds": diagnostics["processing_seconds"],
            "idle_seconds": diagnostics["idle_seconds"],
            "send_count": diagnostics["send_count"],
            "is_alive": diagnostics["is_alive"],
            "model": self._session_manager.get_model(),
        })

    async def _handle_get_context_stats(
        self, arguments: dict[str, Any], *, user_id: int | None = None,
    ) -> str:
        """Return token usage and cost statistics for a user's active Claude session."""
        if self._session_manager is None:
            raise RuntimeError("session_manager not available")

        try:
            target_user_id = int(arguments["user_id"])
        except (KeyError, ValueError, TypeError):
            return "Invalid user_id argument."

        if user_id is not None and target_user_id != user_id:
            return f"No active session for user {target_user_id}."

        stats = self._session_manager.context_stats(target_user_id)

        if stats is None:
            return f"No active session for user {target_user_id}."

        return json.dumps(stats)

    async def _handle_send_notification(
        self, arguments: dict[str, Any], *, user_id: int | None = None,
    ) -> str:
        """Send a Telegram notification to a user with rate limiting."""
        if self._bot is None:
            raise RuntimeError("bot not available")

        try:
            target_user_id = int(arguments["user_id"])
        except (KeyError, ValueError, TypeError):
            return "Invalid user_id argument."

        message: str = str(arguments.get("message", ""))

        # Rate limiting — 10s window per user_id
        now = self._clock()
        last_sent = self._notification_last_sent.get(target_user_id)
        if last_sent is not None:
            elapsed = now - last_sent
            if elapsed < 10.0:
                remaining = math.ceil(10.0 - elapsed)
                return f"Rate limited. Wait {remaining}s."

        # Truncate long messages
        _MAX_MESSAGE_LEN = 4000
        _TRUNCATION_SUFFIX = "… [truncated]"
        if len(message) > _MAX_MESSAGE_LEN:
            message = message[:_MAX_MESSAGE_LEN - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX

        try:
            await self._bot.send_message(chat_id=target_user_id, text=message)
        except Exception as exc:
            return f"Failed to send: {exc}"

        self._notification_last_sent[target_user_id] = now
        return "Notification sent."

    def _sessions_dir(self) -> Path | None:
        """Return the resolved sessions directory for path validation.

        Returns None when config is unavailable — callers must handle this case.
        """
        if self._config is None:
            return None
        return Path(self._config.history.directory).expanduser().resolve() / "sessions"


def _truncate(text: str, max_len: int) -> str:
    """Truncate text to max_len, appending '...' if truncated."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."
