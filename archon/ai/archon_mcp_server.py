"""ArchonMCPServer — minimal HTTP MCP server exposing the spawn_background_agent tool.

Implements the MCP protocol (Model Context Protocol) over HTTP using JSON-RPC 2.0.
Each user's main ClaudeSession is given the URL:

    http://localhost:{port}/mcp/{user_id}

The user_id in the URL path routes spawn requests to the BackgroundAgentManager
with the correct user context.

MCP methods implemented:
  initialize   → server capabilities
  tools/list   → spawn_background_agent descriptor
  tools/call   → delegates to BackgroundAgentManager.spawn()
"""
import json
import logging
from typing import TYPE_CHECKING, Any

from aiohttp import web

if TYPE_CHECKING:
    from archon.ai.background_agent_manager import BackgroundAgentManager

logger = logging.getLogger("archon")

# ── Tool descriptor ────────────────────────────────────────────────

_SPAWN_TOOL: dict[str, Any] = {
    "name": "spawn_background_agent",
    "description": (
        "Spawn a background agent to run a task asynchronously while the main conversation "
        "remains interactive. The agent runs in an isolated Claude session. When done, you "
        "receive its output as context injected into your next message."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "The task for the agent to perform",
            },
            "context": {
                "type": "string",
                "description": "Relevant context or data the agent needs",
                "default": "",
            },
            "user_request": {
                "type": "string",
                "description": (
                    "The original user message that triggered this spawn. "
                    "Recorded as the first entry in the agent's log file so the "
                    "full picture is preserved. Always include this."
                ),
                "default": "",
            },
        },
        "required": ["task"],
    },
}

# ── JSON-RPC helpers ───────────────────────────────────────────────

_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603


def _ok(request_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_ok(request_id: Any, text: str) -> dict:
    return _ok(request_id, {
        "content": [{"type": "text", "text": text}],
        "isError": False,
    })


def _tool_error(request_id: Any, text: str) -> dict:
    return _ok(request_id, {
        "content": [{"type": "text", "text": text}],
        "isError": True,
    })


class ArchonMCPServer:
    """Minimal HTTP MCP server serving the ``spawn_background_agent`` tool."""

    def __init__(
        self,
        manager: "BackgroundAgentManager",
        host: str = "localhost",
        port: int = 18182,
    ) -> None:
        self._manager = manager
        self._host = host
        self._port = port
        self._runner: web.AppRunner | None = None

        self._app = web.Application()
        self._app.router.add_post("/mcp/{user_id}", self._handle_post)

    # ── Public API ─────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the aiohttp web server."""
        self._runner = web.AppRunner(self._app, tcp_keepalive=False)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        logger.info("ArchonMCPServer started on %s:%d", self._host, self._port)

    async def stop(self) -> None:
        """Gracefully stop the web server."""
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            logger.info("ArchonMCPServer stopped")

    def mcp_url_for(self, user_id: int) -> str:
        """Return the MCP endpoint URL for a specific user session."""
        return f"http://{self._host}:{self._port}/mcp/{user_id}"

    # ── HTTP handler ───────────────────────────────────────────────

    async def _handle_post(self, request: web.Request) -> web.Response:
        """Handle all JSON-RPC 2.0 MCP POST requests."""
        user_id_str = request.match_info.get("user_id", "0")
        try:
            user_id = int(user_id_str)
        except ValueError:
            user_id = 0

        # Parse JSON body
        try:
            body = await request.json()
        except (json.JSONDecodeError, Exception):
            return web.Response(status=400, text="Invalid JSON")

        request_id = body.get("id")
        method = body.get("method", "")

        # Dispatch
        try:
            result = await self._dispatch(method, body.get("params", {}), user_id)
            response = _ok(request_id, result)
        except _RpcError as exc:
            response = _error(request_id, exc.code, exc.message)
        except _ToolError as exc:
            response = _tool_error(request_id, exc.message)
        except Exception as exc:
            logger.exception("MCP handler unexpected error for user %d", user_id)
            response = _error(request_id, _INTERNAL_ERROR, str(exc))

        return web.json_response(response)

    async def _dispatch(self, method: str, params: Any, user_id: int) -> Any:
        """Dispatch a JSON-RPC method and return the result dict."""
        if method == "initialize":
            return self._handle_initialize()
        if method == "tools/list":
            return self._handle_tools_list()
        if method == "tools/call":
            return await self._handle_tools_call(params, user_id)
        raise _RpcError(_METHOD_NOT_FOUND, f"Method not found: {method!r}")

    def _handle_initialize(self) -> dict:
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "archon-background-agents", "version": "1.0"},
        }

    def _handle_tools_list(self) -> dict:
        return {"tools": [_SPAWN_TOOL]}

    async def _handle_tools_call(self, params: Any, user_id: int) -> dict:
        tool_name = params.get("name") if isinstance(params, dict) else None
        if tool_name != "spawn_background_agent":
            raise _RpcError(_INVALID_PARAMS, f"Unknown tool: {tool_name!r}")

        arguments = params.get("arguments", {}) if isinstance(params, dict) else {}
        task = arguments.get("task")
        if not task:
            raise _RpcError(_INVALID_PARAMS, "Required parameter 'task' is missing")

        context = arguments.get("context", "")
        user_request = arguments.get("user_request", "")

        try:
            run = await self._manager.spawn(
                user_id=user_id,
                task=task,
                context=context,
                user_request=user_request,
            )
        except RuntimeError as exc:
            # Parallel limit exceeded or other user-visible errors — return as tool error
            return {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            }

        text = f"Agent {run.name} started (run_id: {run.run_id})"
        return {
            "content": [{"type": "text", "text": text}],
            "isError": False,
        }


# ── Internal exception types ──────────────────────────────────────


class _RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class _ToolError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)
