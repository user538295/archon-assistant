"""ArchonRouterMCPServer — read-only HTTP MCP server for router history access.

Exposes three tools to the _router_session:
  - history_list  : list entries in a directory under the history directory
  - history_read  : read a file from the configured history directory
  - history_grep  : search for a pattern in a history file

Path restriction is enforced via resolved Path comparison (not string prefix).
All paths must resolve to a location under the configured history directory.

Authentication: every POST /mcp request must carry a valid
  Authorization: Bearer <token>
header.  The token is generated at construction time via secrets.token_hex(32)
and exposed via the `token` property so the gateway can thread it to the SDK's
MCP client config (headers={"Authorization": "Bearer <token>"}).

GET /health is unauthenticated (used for health-check probes only).

MCP methods implemented:
  initialize   -> server capabilities
  tools/list   -> history_list + history_read + history_grep descriptors
  tools/call   -> delegates to the appropriate handler
"""
import asyncio
import hmac
import json
import logging
import re
import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import web

if TYPE_CHECKING:
    from archon.ai.archon_toolkit import ArchonToolkit

logger = logging.getLogger("archon")

# ── Path restriction ────────────────────────────────────────────────


def _is_allowed_path(path_str: str, history_root: Path) -> bool:
    """Return True iff the resolved path is under history_root."""
    try:
        resolved = Path(path_str).expanduser().resolve()
        return resolved == history_root or history_root in resolved.parents
    except Exception:
        return False


def _grep_file(path: Path, compiled: "re.Pattern[str]") -> list[str]:
    """Read *path* and return all lines matching *compiled*.

    Lines longer than 10,000 characters are skipped to bound worst-case CPU time.

    This is a pure sync function intended to run inside asyncio.to_thread so that
    both the I/O and the regex CPU work are off the event loop.
    """
    raw = path.read_text(encoding="utf-8", errors="replace")
    return [line for line in raw.splitlines() if len(line) <= 10000 and compiled.search(line)]


# ── Tool descriptors ────────────────────────────────────────────────

_HISTORY_LIST_TOOL: dict[str, Any] = {
    "name": "history_list",
    "description": (
        "List entries in a directory under ~/.archon/history/. "
        "Returns a sorted list of names; directories are shown with a trailing '/'. "
        "Use this to discover which history files exist before reading them."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path to a directory. Must be under ~/.archon/history/",
            },
        },
        "required": ["path"],
    },
}

_HISTORY_READ_TOOL: dict[str, Any] = {
    "name": "history_read",
    "description": (
        "Read a file from the Archon history directory. "
        "Only files under ~/.archon/history/ are accessible. "
        "Files larger than 50,000 characters are truncated; "
        "prefer history_grep for targeted search in large files."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path to the file. Must be under ~/.archon/history/",
            },
        },
        "required": ["path"],
    },
}

_HISTORY_GREP_TOOL: dict[str, Any] = {
    "name": "history_grep",
    "description": (
        "Search for a pattern in a history file. "
        "Only files under ~/.archon/history/ are accessible. "
        "Returns at most 200 matching lines; use a more specific pattern if results are truncated."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Text or regex pattern to search for",
            },
            "path": {
                "type": "string",
                "description": "Absolute path to the file. Must be under ~/.archon/history/",
            },
        },
        "required": ["pattern", "path"],
    },
}

# ── JSON-RPC helpers ───────────────────────────────────────────────

_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603

_MAX_FILE_CHARS = 50_000
_MAX_GREP_MATCHES = 200


def _ok(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _tool_error(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": True}


class ArchonRouterMCPServer:
    """Read-only HTTP MCP server exposing history_list, history_read, and history_grep tools.

    Access control: every POST /mcp must include
        Authorization: Bearer <token>
    where <token> is the value returned by the `token` property.
    GET /health is exempt from auth.
    """

    def __init__(
        self,
        history_root: str | None = None,
        host: str = "localhost",
        port: int = 18183,
        toolkit: "ArchonToolkit | None" = None,
    ) -> None:
        if history_root is None:
            from archon.config import config
            history_root = config.history.directory
        self._history_root: Path = Path(history_root).expanduser().resolve()
        self._host: str = host
        self._port: int = port
        self._runner: web.AppRunner | None = None
        self._token: str = secrets.token_hex(32)
        self._toolkit: "ArchonToolkit | None" = toolkit

        self._app = web.Application()
        self._app.router.add_get("/health", self._handle_health)
        self._app.router.add_post("/mcp", self._handle_post)

    # ── Public API ─────────────────────────────────────────────────

    @property
    def token(self) -> str:
        """The Bearer token required to call POST /mcp."""
        return self._token

    @property
    def mcp_url(self) -> str:
        return f"http://{self._host}:{self._port}/mcp"

    async def start(self, host: str = "localhost", port: int = 18183) -> None:
        """Start the aiohttp web server."""
        self._host = host
        self._port = port
        if not self._history_root.exists():
            logger.warning(
                "History directory %s does not exist — history_read and history_grep will return file-not-found errors",
                self._history_root,
            )
        self._runner = web.AppRunner(self._app, tcp_keepalive=False)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        logger.info("ArchonRouterMCPServer started on %s:%d", self._host, self._port)

    async def stop(self) -> None:
        """Gracefully stop the web server."""
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            logger.info("ArchonRouterMCPServer stopped")

    # ── HTTP handlers ──────────────────────────────────────────────

    async def _handle_health(self, _request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def _handle_post(self, request: web.Request) -> web.Response:
        # ── Authentication ────────────────────────────────────────
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or not hmac.compare_digest(auth[len("Bearer "):], self._token):
            return web.Response(status=401, text="Unauthorized")

        try:
            body = await request.json()
        except Exception:
            return web.Response(status=400, text="Invalid JSON")

        if not isinstance(body, dict):
            return web.Response(status=400, text="Invalid JSON-RPC request")

        request_id = body.get("id")
        method = body.get("method", "")

        try:
            result = await self._dispatch(method, body.get("params", {}))
            response = _ok(request_id, result)
        except _RpcError as exc:
            response = _error(request_id, exc.code, exc.message)
        except Exception as exc:
            logger.exception("ArchonRouterMCPServer unexpected error")
            response = _error(request_id, _INTERNAL_ERROR, str(exc))

        return web.json_response(response)

    async def _dispatch(self, method: str, params: Any) -> Any:
        if method == "initialize":
            return self._handle_initialize()
        if method == "tools/list":
            return self._handle_tools_list()
        if method == "tools/call":
            return await self._handle_tools_call(params)
        raise _RpcError(_METHOD_NOT_FOUND, f"Method not found: {method!r}")

    def _handle_initialize(self) -> dict[str, Any]:
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "archon-router-history", "version": "1.0"},
        }

    def _handle_tools_list(self) -> dict[str, Any]:
        tools: list[dict[str, Any]] = [_HISTORY_LIST_TOOL, _HISTORY_READ_TOOL, _HISTORY_GREP_TOOL]
        if self._toolkit:
            tools.extend(self._toolkit.tool_definitions)
        return {"tools": tools}

    async def _handle_tools_call(self, params: Any) -> dict[str, Any]:
        tool_name = params.get("name") if isinstance(params, dict) else None
        arguments = params.get("arguments", {}) if isinstance(params, dict) else {}

        if tool_name == "history_list":
            return await self._tool_history_list(arguments)
        if tool_name == "history_read":
            return await self._tool_history_read(arguments)
        if tool_name == "history_grep":
            return await self._tool_history_grep(arguments)

        # Delegate to toolkit if the tool is registered there
        if self._toolkit and tool_name in self._toolkit.tool_names:
            # Router sessions have no per-user path — user_id=None by design (see plan §User-scoped authorization)
            # event_callback not passed — MCP-routed calls are logged by SDK's own event system
            result_text = await self._toolkit.call_tool(tool_name, arguments, user_id=None)
            return _tool_ok(result_text)

        raise _RpcError(_INVALID_PARAMS, f"Unknown tool: {tool_name!r}")

    async def _tool_history_list(self, args: dict[str, Any]) -> dict[str, Any]:
        path_str = args.get("path", "")
        if not _is_allowed_path(path_str, self._history_root):
            return _tool_error("Access denied: path must be under the configured history directory")
        path = Path(path_str).expanduser()
        if not path.exists():
            return _tool_error(f"Directory not found: {path}")
        if not path.is_dir():
            return _tool_error("Path is a file, not a directory. Provide a directory path.")
        entries = await asyncio.to_thread(
            lambda: sorted(
                entry.name + ("/" if entry.is_dir() else "")
                for entry in path.iterdir()
                if entry.exists()
            )
        )
        return _tool_ok("\n".join(entries) if entries else "(empty)")

    async def _tool_history_read(self, args: dict[str, Any]) -> dict[str, Any]:
        path_str = args.get("path", "")
        if not _is_allowed_path(path_str, self._history_root):
            return _tool_error("Access denied: path must be under the configured history directory")
        path = Path(path_str).expanduser()
        if not path.exists():
            return _tool_error(f"File not found: {path}")
        if not path.is_file():
            return _tool_error(
                "Path is a directory, not a file. "
                "Use history_grep to search within it or provide a specific file path."
            )
        content = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")
        if len(content) > _MAX_FILE_CHARS:
            truncated = content[:_MAX_FILE_CHARS]
            notice = (
                f"\n[Truncated: file is {len(content):,} chars, showing first {_MAX_FILE_CHARS:,}. "
                "Use history_grep to search for specific content.]"
            )
            content = truncated + notice
        return _tool_ok(content)

    async def _tool_history_grep(self, args: dict[str, Any]) -> dict[str, Any]:
        path_str = args.get("path", "")
        pattern = args.get("pattern", "")
        if not pattern.strip():
            return _tool_error("Pattern must not be empty. Provide a search term or regex pattern.")
        if not _is_allowed_path(path_str, self._history_root):
            return _tool_error("Access denied: path must be under the configured history directory")
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            return _tool_error(f"Invalid regex pattern: {exc}")
        path = Path(path_str).expanduser()
        if not path.exists():
            return _tool_error(f"File not found: {path}")
        if not path.is_file():
            return _tool_error(
                "Path is a directory, not a file. Provide a specific file path."
            )
        matches = await asyncio.to_thread(_grep_file, path, compiled)
        if len(matches) > _MAX_GREP_MATCHES:
            omitted = len(matches) - _MAX_GREP_MATCHES
            matches = matches[:_MAX_GREP_MATCHES]
            matches.append(
                f"[Truncated: {omitted} more matches omitted. Use a more specific pattern.]"
            )
        text = "\n".join(matches) if matches else "(no matches)"
        return _tool_ok(text)


# ── Internal exception types ──────────────────────────────────────


class _RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)
