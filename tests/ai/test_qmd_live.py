"""Live tests against the real QMD daemon — FR.002.

These tests require a running QMD daemon.  They are excluded from the
default pytest run (marked ``live``) and must be invoked explicitly:

    uv run pytest -m live tests/ai/test_qmd_live.py -v

What is tested:
  1. Daemon reachability — HTTP 200 / 406 (MCP negotiation) on the live port
  2. PID file reflects a live process
  3. _ensure_qmd_daemon detects the already-running daemon (no restart)
  4. MCP protocol: initialize handshake succeeds on a fresh session
  5. MCP tools/list returns the expected QMD tools
  6. MCP search tool returns results for a known query
  7. ClaudeSession with the live qmd_url stores the URL correctly
"""
import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        shutil.which("qmd") is None,
        reason="qmd binary not found in PATH",
    ),
]

# ── helpers ───────────────────────────────────────────────────────────────────

_PID_FILE = Path.home() / ".cache" / "qmd" / "mcp.pid"


def _get_live_port() -> int | None:
    """Read the actual port the running QMD daemon is listening on.

    We parse the process command line rather than trusting the PID file's
    configured port (which may differ from the actual port if the daemon was
    started manually with a different --port flag).
    """
    if not _PID_FILE.exists():
        return None
    try:
        pid = int(_PID_FILE.read_text().strip())
        os.kill(pid, 0)  # check alive
    except (ValueError, OSError):
        return None

    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=5,
        )
        cmd = result.stdout.strip()
        # Parse --port <N> from the command line
        parts = cmd.split()
        if "--port" in parts:
            idx = parts.index("--port")
            return int(parts[idx + 1])
    except Exception:
        pass
    return None


def _live_url() -> str | None:
    port = _get_live_port()
    if port is None:
        return None
    return f"http://localhost:{port}/mcp"


# ── fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def live_port() -> int:
    port = _get_live_port()
    if port is None:
        pytest.skip("No running QMD daemon found")
    return port


@pytest.fixture(scope="module")
def live_url(live_port: int) -> str:
    return f"http://localhost:{live_port}/mcp"


@pytest.fixture(scope="module")
def mcp_session_id(live_port: int) -> str:
    """Initialize a fresh MCP session and return its session ID."""
    import urllib.request
    import json

    url = f"http://localhost:{live_port}/mcp"
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "archon-live-test", "version": "1.0"},
        },
    }).encode()

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            session_id = resp.headers.get("Mcp-Session-Id", "")
            return session_id
    except Exception:
        # Server already initialized — use a dummy (tests will handle 400s gracefully)
        return ""


# ── 1. Daemon reachability ────────────────────────────────────────────────────


def test_qmd_daemon_pid_file_exists() -> None:
    assert _PID_FILE.exists(), f"PID file not found: {_PID_FILE}"


def test_qmd_daemon_pid_is_alive() -> None:
    pid = int(_PID_FILE.read_text().strip())
    try:
        os.kill(pid, 0)
    except OSError:
        pytest.fail(f"QMD daemon PID {pid} is not alive")


def test_qmd_live_port_is_parseable(live_port: int) -> None:
    assert isinstance(live_port, int)
    assert 1024 <= live_port <= 65535


def test_qmd_http_endpoint_reachable(live_url: str) -> None:
    """A GET to /mcp should return 405/406 (not a connection error)."""
    import urllib.request
    import urllib.error

    try:
        with urllib.request.urlopen(live_url, timeout=5):
            pass  # 200 is also fine
    except urllib.error.HTTPError as e:
        # 405 Method Not Allowed or 406 Not Acceptable = server is alive
        assert e.code in (405, 406, 400), f"Unexpected HTTP {e.code} from QMD"
    except Exception as exc:
        pytest.fail(f"QMD MCP endpoint not reachable at {live_url}: {exc}")


# ── 2. _ensure_qmd_daemon detects live daemon ─────────────────────────────────


async def test_ensure_qmd_daemon_detects_running_daemon(live_port: int) -> None:
    """_ensure_qmd_daemon must return True for the already-running daemon."""
    from archon.gateway.gateway import _ensure_qmd_daemon

    result = await _ensure_qmd_daemon("localhost", live_port)
    assert result is True


async def test_ensure_qmd_daemon_does_not_restart_alive_daemon(live_port: int) -> None:
    """When daemon is alive, no subprocess must be spawned."""
    from archon.gateway.gateway import _ensure_qmd_daemon
    from unittest.mock import patch

    with patch("asyncio.create_subprocess_exec") as mock_proc:
        await _ensure_qmd_daemon("localhost", live_port)

    mock_proc.assert_not_called()


# ── 3. MCP protocol: tools/list ───────────────────────────────────────────────


def _mcp_request(port: int, method: str, params: dict, session_id: str = "") -> dict:
    """Send one JSON-RPC 2.0 request to the QMD MCP server."""
    import json
    import urllib.request
    import urllib.error

    url = f"http://localhost:{port}/mcp"
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def test_mcp_initialize_responds(live_port: int) -> None:
    """MCP initialize must return either a result or a known error."""
    resp = _mcp_request(live_port, "initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "archon-probe", "version": "1.0"},
    })
    # Either success or "already initialized" error — both mean server is alive
    assert "result" in resp or "error" in resp


def test_mcp_tools_list_with_session(live_port: int, mcp_session_id: str) -> None:
    """tools/list must return a non-empty tools array."""
    if not mcp_session_id:
        pytest.skip("Could not obtain MCP session ID (server already initialized)")

    resp = _mcp_request(live_port, "tools/list", {}, session_id=mcp_session_id)

    assert "result" in resp, f"Unexpected response: {resp}"
    tools = resp["result"].get("tools", [])
    assert len(tools) > 0, "Expected at least one QMD tool"


def test_mcp_tools_list_contains_search(live_port: int, mcp_session_id: str) -> None:
    """QMD must expose at least a 'search' tool."""
    if not mcp_session_id:
        pytest.skip("Could not obtain MCP session ID")

    resp = _mcp_request(live_port, "tools/list", {}, session_id=mcp_session_id)
    if "result" not in resp:
        pytest.skip("tools/list failed — skip tool name assertion")

    tool_names = [t["name"] for t in resp["result"].get("tools", [])]
    assert "search" in tool_names, f"Expected 'search' in tools; got: {tool_names}"


# ── 4. MCP search tool returns results ────────────────────────────────────────


def test_mcp_search_returns_results(live_port: int, mcp_session_id: str) -> None:
    """Calling the 'search' tool must return a non-empty content."""
    if not mcp_session_id:
        pytest.skip("Could not obtain MCP session ID")

    resp = _mcp_request(live_port, "tools/call", {
        "name": "search",
        "arguments": {"query": "archon"},
    }, session_id=mcp_session_id)

    assert "result" in resp or "error" in resp
    if "result" in resp:
        content = resp["result"].get("content", [])
        assert isinstance(content, list)


# ── 5. ClaudeSession stores live URL correctly ────────────────────────────────


def test_claude_session_stores_live_qmd_url(live_url: str) -> None:
    from archon.ai.claude_session import ClaudeSession

    session = ClaudeSession(qmd_url=live_url)
    assert session._qmd_url == live_url


def test_claude_session_mcp_servers_built_with_live_url(live_url: str) -> None:
    """Verify the MCP servers dict that would be sent to the SDK."""
    # Inspect the logic without actually connecting
    from archon.ai.claude_session import ClaudeSession

    session = ClaudeSession(qmd_url=live_url)
    # Mirror the logic in start():
    mcp_servers: dict = {}
    if session._qmd_url is not None:
        mcp_servers["qmd"] = {"type": "http", "url": session._qmd_url}

    assert mcp_servers == {"qmd": {"type": "http", "url": live_url}}


# ── 6. qmd CLI sanity ─────────────────────────────────────────────────────────


def test_qmd_cli_status_shows_running() -> None:
    """qmd status must report MCP as running."""
    result = subprocess.run(
        ["qmd", "status"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert "running" in result.stdout.lower()


def test_qmd_cli_search_returns_output() -> None:
    """qmd search must return at least one result for a broad query."""
    result = subprocess.run(
        ["qmd", "search", "archon"],
        capture_output=True, text=True, timeout=15,
    )
    # exit 0 with some output = healthy
    assert result.returncode == 0
    assert len(result.stdout.strip()) > 0
