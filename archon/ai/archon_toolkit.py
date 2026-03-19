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
import re
import shutil
import time
import tomllib
from pathlib import Path
from typing import Any, Callable, Awaitable

import tomli_w
from croniter import croniter  # type: ignore[import-untyped]

from archon.ai.event_mapper import ToolStarted, ToolResult
from archon.config.loader import atomic_write, save_notifications_config
from archon.config.config_rw import get_config_value, set_config_value

logger = logging.getLogger("archon")

_MAX_LOG_ARGS_LEN = 200
_JOB_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,50}$")
_MIN_CRON_INTERVAL_SECONDS = 300  # 5 minutes
_MAX_SCHEDULED_JOBS = 20
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


_VALID_NOTIFICATION_MODES: frozenset[str] = frozenset({"quiet", "normal", "verbose", "debug"})


_SET_NOTIFICATION_MODE_SCHEMA: dict[str, Any] = {
    "name": "set_notification_mode",
    "description": (
        "Set the global Telegram notification mode. "
        "Valid modes: quiet, normal, verbose, debug."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "user_id": {
                "type": "integer",
                "description": "The Telegram user ID making the request",
            },
            "mode": {
                "type": "string",
                "description": "Notification mode: quiet, normal, verbose, or debug",
                "enum": ["quiet", "normal", "verbose", "debug"],
            },
        },
        "required": ["user_id", "mode"],
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


_GET_MODEL_SCHEMA: dict[str, Any] = {
    "name": "get_model",
    "description": "Get the current Claude model used for new sessions.",
    "inputSchema": {
        "type": "object",
        "properties": {},
    },
}


_SET_MODEL_SCHEMA: dict[str, Any] = {
    "name": "set_model",
    "description": "Set the Claude model for new sessions. Must be in the available models list.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "model": {
                "type": "string",
                "description": "The model ID to use, e.g. 'claude-sonnet-4-6'",
            },
        },
        "required": ["model"],
    },
}


_LIST_SKILLS_SCHEMA: dict[str, Any] = {
    "name": "list_skills",
    "description": "List all available Claude Code skills with their names and descriptions.",
    "inputSchema": {
        "type": "object",
        "properties": {},
    },
}


_LIST_SCHEDULED_TASKS_SCHEMA: dict[str, Any] = {
    "name": "list_scheduled_tasks",
    "description": "List all configured scheduled jobs with their status and next run time.",
    "inputSchema": {
        "type": "object",
        "properties": {},
    },
}


_ADD_SCHEDULED_TASK_SCHEMA: dict[str, Any] = {
    "name": "add_scheduled_task",
    "description": (
        "Create a new scheduled job that runs a Claude prompt on a cron schedule. "
        "The job is created as disabled — use /scheduled in Telegram to review and enable it. "
        "Minimum interval: 5 minutes. Maximum 20 jobs total."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Job name: alphanumeric, underscores and hyphens only, 1-50 chars",
            },
            "cron": {
                "type": "string",
                "description": "Cron expression (5 fields), minimum interval 5 minutes",
            },
            "prompt": {
                "type": "string",
                "description": "The prompt to send to Claude when the job runs",
            },
            "timeout_seconds": {
                "type": "number",
                "description": "Per-step timeout in seconds (default 60.0)",
                "default": 60.0,
            },
        },
        "required": ["name", "cron", "prompt"],
    },
}


_UPDATE_SCHEDULED_TASK_SCHEMA: dict[str, Any] = {
    "name": "update_scheduled_task",
    "description": (
        "Update an existing scheduled job. Only provided fields are changed. "
        "Cron validation rules apply (5-field, minimum 5-minute interval). "
        "Triggers scheduler reload on success."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Job name to update (must already exist)",
            },
            "cron": {
                "type": "string",
                "description": "New cron expression (5 fields, min 5-minute interval). Omit to keep current.",
            },
            "prompt": {
                "type": "string",
                "description": "New prompt to run. Omit to keep current.",
            },
            "enabled": {
                "type": "boolean",
                "description": "Enable or disable the job. Omit to keep current.",
            },
            "timeout_seconds": {
                "type": "number",
                "description": "New per-step timeout in seconds. Omit to keep current.",
            },
        },
        "required": ["name"],
    },
}


_REMOVE_SCHEDULED_TASK_SCHEMA: dict[str, Any] = {
    "name": "remove_scheduled_task",
    "description": (
        "Remove a scheduled job by name. "
        "Refuses if the job is currently running. "
        "Triggers scheduler reload on success."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Job name to remove (alphanumeric, underscores and hyphens only, 1-50 chars)",
            },
        },
        "required": ["name"],
    },
}


_GET_JOB_CONFIG_SCHEMA: dict[str, Any] = {
    "name": "get_job_config",
    "description": (
        "Read the full configuration of a scheduled job by name. "
        "Returns the job.toml contents as JSON."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Job name (alphanumeric, underscores and hyphens only, 1-50 chars)",
            },
        },
        "required": ["name"],
    },
}


_GET_CONFIG_SCHEMA: dict[str, Any] = {
    "name": "get_config",
    "description": (
        "Read a single configuration value by dot-notation path (e.g. 'notifications.mode'). "
        "Sensitive paths (containing 'token', 'password', 'secret', or 'key') are redacted."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Dot-notation path to the config value, e.g. 'notifications.mode'",
            },
        },
        "required": ["path"],
    },
}


_SET_CONFIG_SCHEMA: dict[str, Any] = {
    "name": "set_config",
    "description": (
        "Write a configuration value by dot-notation path (e.g. 'notifications.mode'). "
        "The value is coerced to the appropriate type (int, float, bool, or string). "
        "Any path in config.toml may be set — no allowlist restrictions."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Dot-notation path to the config key, e.g. 'notifications.mode'",
            },
            "value": {
                "type": "string",
                "description": "Value to set (coerced to int/float/bool/string as appropriate)",
            },
        },
        "required": ["path", "value"],
    },
}

_SENSITIVE_RE = re.compile(r"(token|password|secret|key)", re.IGNORECASE)


def _redact_sensitive_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of d with values for sensitive keys replaced by '***'."""
    result: dict[str, Any] = {}
    for k, v in d.items():
        if _SENSITIVE_RE.search(str(k)):
            result[k] = "***"
        elif isinstance(v, dict):
            result[k] = _redact_sensitive_dict(v)
        elif isinstance(v, list):
            result[k] = [_redact_sensitive_dict(item) if isinstance(item, dict) else item for item in v]
        else:
            result[k] = v
    return result

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
        config_file: str | Path | None = None,
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
        self._config_file = config_file
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
        self.register_tool(
            "set_notification_mode",
            _SET_NOTIFICATION_MODE_SCHEMA,
            self._handle_set_notification_mode,
        )
        self.register_tool(
            "get_model",
            _GET_MODEL_SCHEMA,
            self._handle_get_model,
        )
        self.register_tool(
            "set_model",
            _SET_MODEL_SCHEMA,
            self._handle_set_model,
        )
        self.register_tool(
            "list_skills",
            _LIST_SKILLS_SCHEMA,
            self._handle_list_skills,
        )
        self.register_tool(
            "list_scheduled_tasks",
            _LIST_SCHEDULED_TASKS_SCHEMA,
            self._handle_list_scheduled_tasks,
        )
        self.register_tool(
            "add_scheduled_task",
            _ADD_SCHEDULED_TASK_SCHEMA,
            self._handle_add_scheduled_task,
        )
        self.register_tool(
            "update_scheduled_task",
            _UPDATE_SCHEDULED_TASK_SCHEMA,
            self._handle_update_scheduled_task,
        )
        self.register_tool(
            "remove_scheduled_task",
            _REMOVE_SCHEDULED_TASK_SCHEMA,
            self._handle_remove_scheduled_task,
        )
        self.register_tool(
            "get_job_config",
            _GET_JOB_CONFIG_SCHEMA,
            self._handle_get_job_config,
        )
        self.register_tool(
            "get_config",
            _GET_CONFIG_SCHEMA,
            self._handle_get_config,
        )
        self.register_tool(
            "set_config",
            _SET_CONFIG_SCHEMA,
            self._handle_set_config,
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
            model = self._session_manager.get_model()
            if model is None and self._config is not None:
                model = self._config.models.default
            if model is None:
                model = "unknown"
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

    async def _handle_set_notification_mode(
        self, arguments: dict[str, Any], *, user_id: int | None = None,
    ) -> str:
        """Set the global notification mode."""
        if self._config is None:
            raise RuntimeError("config not available")

        mode: str = str(arguments.get("mode", ""))
        if mode not in _VALID_NOTIFICATION_MODES:
            return (
                f"Invalid mode {mode!r}. Valid modes: "
                + ", ".join(sorted(_VALID_NOTIFICATION_MODES))
                + "."
            )

        self._config.notifications.mode = mode
        if self._config_file is not None:
            save_notifications_config(self._config.notifications, self._config_file)
        logger.warning(
            "set_notification_mode: mode changed to %r by user=%s", mode, user_id
        )
        return f"Notification mode set to {mode}."

    async def _handle_get_model(
        self, arguments: dict[str, Any], *, user_id: int | None = None,
    ) -> str:
        """Return the current Claude model."""
        if self._session_manager is not None:
            model = self._session_manager.get_model()
            if model is not None:
                return model
        if self._config is not None:
            return self._config.models.default or "default"
        raise RuntimeError("Neither session_manager nor config is available")

    async def _handle_set_model(
        self, arguments: dict[str, Any], *, user_id: int | None = None,
    ) -> str:
        """Set the Claude model for new sessions."""
        if self._config is None:
            raise RuntimeError("config not available")
        if self._session_manager is None:
            raise RuntimeError("session_manager not available")

        model: str = str(arguments.get("model", ""))
        available: list[str] = self._config.models.available
        if model not in available:
            return (
                f"Invalid model {model!r}. Available models: "
                + ", ".join(available)
                + "."
            )

        self._session_manager.set_model(model)
        logger.warning("set_model: model changed to %r by user=%s", model, user_id)
        return f"Model set to {model}."

    async def _handle_list_skills(
        self, arguments: dict[str, Any], *, user_id: int | None = None,
    ) -> str:
        """Return all available skills as a JSON array of {name, description}."""
        if self._skill_loader is None:
            return "No skills available."
        skills = self._skill_loader.skills
        if not skills:
            return "No skills available."
        return json.dumps([{"name": s.name, "description": s.description} for s in skills])

    async def _handle_list_scheduled_tasks(
        self, arguments: dict[str, Any], *, user_id: int | None = None,
    ) -> str:
        """Return all scheduled jobs as a JSON array with status and next run time."""
        if self._job_scheduler is None:
            return "No scheduled jobs."

        statuses = self._job_scheduler.job_statuses
        if not statuses:
            return "No scheduled jobs."

        job_configs = {j.name: j for j in self._job_scheduler.job_configs}
        next_runs = self._job_scheduler.next_run_times()

        result = []
        for name, status in statuses.items():
            cfg = job_configs.get(name)
            if cfg is None:
                continue  # skip stale status entries with no matching config
            next_run = next_runs.get(name)
            result.append({
                "name": name,
                "enabled": cfg.enabled,
                "cron": cfg.cron,
                "last_run": status.last_run.isoformat() if status.last_run is not None else None,
                "last_result": status.last_result,
                "last_error": status.last_error,
                "next_run": next_run.isoformat() if next_run is not None else None,
                "run_count": status.run_count,
            })
        return json.dumps(result)

    async def _handle_add_scheduled_task(
        self, arguments: dict[str, Any], *, user_id: int | None = None,
    ) -> str:
        """Create a new scheduled job bundle and reload the scheduler."""
        if self._job_scheduler is None:
            raise RuntimeError("job_scheduler not available")

        name: str = str(arguments.get("name", ""))
        cron: str = str(arguments.get("cron", ""))
        prompt: str = str(arguments.get("prompt", ""))
        timeout_seconds: float = float(arguments.get("timeout_seconds", 60.0))

        # Validate name
        if not _JOB_NAME_RE.match(name):
            return (
                f"Invalid job name {name!r}. "
                "Name must match ^[a-zA-Z0-9_-]{1,50}$ (alphanumeric, underscores, hyphens, 1-50 chars)."
            )

        # Validate cron expression
        try:
            # Validate by constructing croniter (raises on bad expression)
            from datetime import datetime, timezone as _tz
            _now = datetime.now(_tz.utc)
            _it = croniter(cron, _now)
            # Compute minimum interval across 10 consecutive run pairs to catch
            # non-uniform schedules like "0,3 * * * *" that bypass a 2-point check.
            _runs = [_it.get_next(datetime) for _ in range(11)]  # 11 points = 10 intervals
            _min_interval = min((_runs[i + 1] - _runs[i]).total_seconds() for i in range(10))
        except Exception:
            return f"Invalid cron expression {cron!r}. Please use a valid 5-field cron expression."

        if _min_interval < _MIN_CRON_INTERVAL_SECONDS:
            return f"Cron schedule too frequent: minimum interval is 5 minutes."

        # Check max jobs limit
        current_jobs = self._job_scheduler.job_configs
        if len(current_jobs) >= _MAX_SCHEDULED_JOBS:
            return (
                f"Maximum of {_MAX_SCHEDULED_JOBS} scheduled jobs reached. "
                "Remove an existing job before adding a new one."
            )

        # Determine jobs directory from scheduler
        raw_jobs_dir = self._job_scheduler.jobs_dir
        if raw_jobs_dir is None:
            raise RuntimeError("job_scheduler.jobs_dir is not configured")
        jobs_dir = Path(raw_jobs_dir)

        # Check for duplicate
        job_dir = jobs_dir / name
        if job_dir.exists():
            return f"Job {name!r} already exists at {job_dir}. Remove it first."

        # Build TOML document — use tomli_w for safe serialization (no injection)
        doc: dict[str, Any] = {
            "name": name,
            "cron": cron,
            "enabled": False,
            "timeout_seconds": timeout_seconds,
            "pipeline": {"run_prompt": prompt},
        }
        toml_bytes = tomli_w.dumps(doc).encode("utf-8")

        # Write the job bundle atomically; clean up the directory if write fails
        job_dir.mkdir(parents=True, exist_ok=False)
        job_file = job_dir / "job.toml"
        try:
            atomic_write(job_file, toml_bytes.decode("utf-8"))
        except Exception:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise

        # Reload the scheduler so it picks up the new (disabled) job
        self._job_scheduler.reload_jobs()

        # Send Telegram notification if bot is available
        if self._bot is not None and user_id is not None:
            msg = (
                f"📋 New scheduled job '{name}' created (disabled). "
                "Use /scheduled to review and enable."
            )
            try:
                await self._bot.send_message(chat_id=user_id, text=msg)
            except Exception as exc:
                logger.warning("Failed to send add_scheduled_task notification: %s", exc)

        return f"Job '{name}' created (disabled). Use /scheduled in Telegram to review and enable."

    async def _handle_update_scheduled_task(
        self, arguments: dict[str, Any], *, user_id: int | None = None,
    ) -> str:
        """Update fields of an existing scheduled job and reload the scheduler."""
        if self._job_scheduler is None:
            raise RuntimeError("job_scheduler not available")

        name: str = str(arguments.get("name", ""))

        # Validate name
        if not _JOB_NAME_RE.match(name):
            return (
                f"Invalid job name {name!r}. "
                "Name must match ^[a-zA-Z0-9_-]{1,50}$ (alphanumeric, underscores, hyphens, 1-50 chars)."
            )

        # Locate the job file
        raw_jobs_dir = self._job_scheduler.jobs_dir
        if raw_jobs_dir is None:
            raise RuntimeError("job_scheduler.jobs_dir is not configured")
        jobs_dir = Path(raw_jobs_dir)
        job_file = jobs_dir / name / "job.toml"

        if not job_file.exists():
            return f"Job {name!r} not found at {job_file}."

        # Read current TOML
        current = tomllib.loads(job_file.read_text())

        # Validate and apply cron if provided
        new_cron: str | None = arguments.get("cron")
        if new_cron is not None:
            new_cron = str(new_cron)
            try:
                from datetime import datetime, timezone as _tz
                _now = datetime.now(_tz.utc)
                _it = croniter(new_cron, _now)
                _runs = [_it.get_next(datetime) for _ in range(11)]
                _min_interval = min((_runs[i + 1] - _runs[i]).total_seconds() for i in range(10))
            except Exception:
                return f"Invalid cron expression {new_cron!r}. Please use a valid 5-field cron expression."
            if _min_interval < _MIN_CRON_INTERVAL_SECONDS:
                return "Cron schedule too frequent: minimum interval is 5 minutes."

        # Build updated document — merge provided fields
        updated = dict(current)
        changed: list[str] = []

        if new_cron is not None and new_cron != updated.get("cron"):
            updated["cron"] = new_cron
            changed.append("cron")

        if "prompt" in arguments:
            new_prompt = str(arguments["prompt"])
            pipeline = dict(updated.get("pipeline", {}))
            if pipeline.get("run_prompt") != new_prompt:
                pipeline["run_prompt"] = new_prompt
                updated["pipeline"] = pipeline
                changed.append("prompt")

        if "enabled" in arguments:
            new_enabled = bool(arguments["enabled"])
            if updated.get("enabled") != new_enabled:
                updated["enabled"] = new_enabled
                changed.append("enabled")

        if "timeout_seconds" in arguments:
            new_timeout = float(arguments["timeout_seconds"])
            if updated.get("timeout_seconds") != new_timeout:
                updated["timeout_seconds"] = new_timeout
                changed.append("timeout_seconds")

        if not changed:
            return f"Job '{name}': no fields changed."

        # Write back atomically
        toml_bytes = tomli_w.dumps(updated).encode("utf-8")
        logger.warning("update_scheduled_task: updating %r fields=%r by user=%s", name, changed, user_id)
        atomic_write(job_file, toml_bytes.decode("utf-8"))

        # Reload the scheduler
        self._job_scheduler.reload_jobs()

        return f"Job '{name}' updated. Changed fields: {', '.join(changed)}."

    async def _handle_remove_scheduled_task(
        self, arguments: dict[str, Any], *, user_id: int | None = None,
    ) -> str:
        """Remove a scheduled job bundle and reload the scheduler."""
        if self._job_scheduler is None:
            raise RuntimeError("job_scheduler not available")

        name: str = str(arguments.get("name", ""))

        # Validate name — same regex as add/update
        if not _JOB_NAME_RE.match(name):
            return (
                f"Invalid job name {name!r}. "
                "Name must match ^[a-zA-Z0-9_-]{1,50}$ (alphanumeric, underscores, hyphens, 1-50 chars)."
            )

        # Resolve jobs directory
        raw_jobs_dir = self._job_scheduler.jobs_dir
        if raw_jobs_dir is None:
            raise RuntimeError("job_scheduler.jobs_dir is not configured")
        jobs_dir = Path(raw_jobs_dir)
        job_dir = jobs_dir / name

        # Check existence
        if not job_dir.exists() and not job_dir.is_symlink():
            return f"Job {name!r} not found."

        # Safety: refuse if job is currently running
        statuses = self._job_scheduler.job_statuses
        status = statuses.get(name)
        if status is not None and status.is_running:
            return f"Job {name!r} is currently running — cannot remove while active."

        # Security: reject symlinks
        if job_dir.is_symlink():
            return f"Job {name!r} rejected: directory is a symlink."

        # Security: reject if any content is a symlink (recursive)
        for child in job_dir.rglob("*"):
            if child.is_symlink():
                return f"Job {name!r} rejected: contains symlink {child.name!r}."

        # Remove the directory; always reload to keep scheduler in sync
        error: str | None = None
        try:
            shutil.rmtree(job_dir)
            logger.warning("remove_scheduled_task: removed job %r by user=%s", name, user_id)
        except Exception as exc:
            error = str(exc)
        finally:
            self._job_scheduler.reload_jobs()

        if error:
            return f"Error removing job '{name}': {error}"
        return f"Job '{name}' removed."

    async def _handle_get_job_config(
        self, arguments: dict[str, Any], *, user_id: int | None = None,
    ) -> str:
        """Return the full configuration of a scheduled job as JSON."""
        if self._job_scheduler is None:
            raise RuntimeError("job_scheduler not available")

        name: str = str(arguments.get("name", ""))

        # Validate name
        if not _JOB_NAME_RE.match(name):
            return (
                f"Invalid job name {name!r}. "
                "Name must match ^[a-zA-Z0-9_-]{1,50}$ (alphanumeric, underscores, hyphens, 1-50 chars)."
            )

        # Resolve jobs directory
        raw_jobs_dir = self._job_scheduler.jobs_dir
        if raw_jobs_dir is None:
            raise RuntimeError("job_scheduler.jobs_dir is not configured")
        jobs_dir = Path(raw_jobs_dir).resolve()

        job_dir = jobs_dir / name

        # Security: reject symlink directory
        if job_dir.is_symlink():
            return f"Job '{name}' rejected: directory is a symlink."

        job_file = job_dir / "job.toml"

        # Check existence
        if not job_file.exists():
            return f"Job '{name}' not found."

        # Security: reject symlink file
        if job_file.is_symlink():
            return f"Job '{name}' rejected: job.toml is a symlink."

        # Security: resolved path must be under jobs_dir
        try:
            job_file.resolve().relative_to(jobs_dir)
        except ValueError:
            return f"Job '{name}' rejected: path is outside jobs directory."

        try:
            content = tomllib.loads(job_file.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            return f"Failed to read job '{name}': invalid TOML — {e}"
        except OSError as e:
            return f"Failed to read job '{name}': {e.strerror}"

        return json.dumps(content)

    async def _handle_get_config(
        self, arguments: dict[str, Any], *, user_id: int | None = None,
    ) -> str:
        """Return a config value by dot-notation path, redacting sensitive keys."""
        path: str = str(arguments.get("path", ""))
        config_file = self._config_file
        if config_file is None:
            config_file = Path("~/.archon/config.toml").expanduser()

        # Redact if any path component matches sensitive keywords
        if any(_SENSITIVE_RE.search(part) for part in path.split(".")):
            return json.dumps("***")

        try:
            value = get_config_value(path, Path(config_file))
        except KeyError:
            return f"Config key '{path}' not found."
        except FileNotFoundError:
            return "Config file not found."
        except tomllib.TOMLDecodeError:
            return "Config file is invalid TOML."
        except PermissionError:
            return "Config file not readable."

        if isinstance(value, dict):
            value = _redact_sensitive_dict(value)

        return str(json.dumps(value))

    async def _handle_set_config(
        self, arguments: dict[str, Any], *, user_id: int | None = None,
    ) -> str:
        """Write a config value by dot-notation path; returns coerced value on success."""
        path: str = str(arguments.get("path", ""))
        value: str = str(arguments.get("value", ""))
        config_file = self._config_file
        if config_file is None:
            config_file = Path("~/.archon/config.toml").expanduser()

        log_value = "***" if _SENSITIVE_RE.search(path) else value
        logger.warning("set_config: setting %r = %r by user=%s", path, log_value, user_id)

        try:
            set_config_value(path, value, Path(config_file))
        except FileNotFoundError:
            return "Config file not found."
        except PermissionError:
            return "Permission denied reading config file."
        except ValueError as exc:
            return str(exc)

        try:
            coerced = get_config_value(path, Path(config_file))
            coerced_repr = json.dumps(coerced)
            display_repr = '"***"' if _SENSITIVE_RE.search(path) else coerced_repr
            return f"config.{path} set to {display_repr}."
        except Exception:
            display_repr = '"***"' if _SENSITIVE_RE.search(path) else json.dumps(value)
            return f"config.{path} set to {display_repr} (write succeeded, value not verified)."

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
