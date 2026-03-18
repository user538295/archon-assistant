"""ArchonToolkit — central registry for Archon control-plane MCP tools.

Provides a unified call_tool() dispatcher with audit logging and optional
event_callback for session history integration. MCP servers (ArchonMCPServer,
ArchonOrchestratorMCPServer) delegate registered toolkit tool calls here.

Tools are registered via register_tool() — future tasks add real tools;
this module is the scaffold.
"""
import json
import logging
from typing import Any, Callable, Awaitable

from archon.ai.event_mapper import ToolStarted, ToolResult

logger = logging.getLogger("archon")

_MAX_LOG_ARGS_LEN = 200
_MAX_LOG_RESULT_LEN = 200


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
        self._handlers: dict[str, Callable[[dict[str, Any]], Awaitable[str]]] = {}

    def register_tool(
        self,
        name: str,
        schema: dict[str, Any],
        handler: Callable[[dict[str, Any]], Awaitable[str]],
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
            result = await handler(arguments)
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


def _truncate(text: str, max_len: int) -> str:
    """Truncate text to max_len, appending '...' if truncated."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."
