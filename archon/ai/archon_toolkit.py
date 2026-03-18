"""ArchonToolkit — central registry for Archon control-plane MCP tools.

Provides a unified call_tool() dispatcher with audit logging and optional
event_callback for session history integration. MCP servers (ArchonMCPServer,
ArchonOrchestratorMCPServer) delegate registered toolkit tool calls here.

Tools are registered via register_tool() — future tasks add real tools;
this module is the scaffold.
"""
import json
import logging
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
    ) -> None:
        self._session_manager = session_manager
        self._bg_manager = bg_manager
        self._restart_coordinator = restart_coordinator
        self._bot = bot
        self._config = config
        self._skill_loader = skill_loader
        self._job_scheduler = job_scheduler
        self._gateway_started_at = gateway_started_at

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
            "archon_restart",
            _ARCHON_RESTART_SCHEMA,
            self._handle_archon_restart,
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


def _truncate(text: str, max_len: int) -> str:
    """Truncate text to max_len, appending '...' if truncated."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."
