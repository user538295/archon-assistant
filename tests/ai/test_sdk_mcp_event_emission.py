"""Spike test: verify the SDK emits ToolStarted/ToolResult events for MCP tool calls.

Task 0.0 from Epic 12 — integration spike to confirm that when a ClaudeSession
is configured with an MCP server URL, tool call events for MCP tools appear in
the event stream returned by session.send().

Run manually with:
  uv run pytest tests/ai/test_sdk_mcp_event_emission.py -m live -v

Requires a real Claude API key (ANTHROPIC_API_KEY env var) and the ``claude``
binary in PATH.

Fallback verification:
  Confirmed on claude-agent-sdk 0.1.46 (2026-03-20).  ToolStarted and
  ToolResult events appear in the event stream when the model calls an MCP
  tool via ``background_agent_mcp_url``.  The tool name in ToolStarted.name
  matches the MCP tool name (``echo``), and ToolResult.tool_name also contains
  the tool name.  The model may invoke built-in tools before the MCP tool,
  so ordering assertions must be scoped to the specific MCP tool.
  If the SDK version changes and this test breaks, re-run manually and
  update this comment.
"""

import asyncio
import shutil
import socket

import pytest

from archon.ai.claude_session import ClaudeSession
from archon.ai.event_mapper import ToolResult, ToolStarted

uvicorn = pytest.importorskip("uvicorn", reason="uvicorn not installed")
mcp_fastmcp = pytest.importorskip(
    "mcp.server.fastmcp", reason="mcp package not installed"
)
FastMCP = mcp_fastmcp.FastMCP

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        shutil.which("claude") is None,
        reason="claude binary not found in PATH",
    ),
]

_TIMEOUT = 90.0  # overall test timeout in seconds


def _find_free_port() -> int:
    """Return an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _create_mcp_server() -> "FastMCP":
    """Create a minimal FastMCP server with a single echo tool."""
    server = FastMCP(name="spike-echo", host="127.0.0.1", port=0)

    @server.tool(name="echo", description="Echo the input text back verbatim")
    def echo(text: str) -> str:
        """Return the input text unchanged."""
        return f"ECHO: {text}"

    return server


async def test_sdk_emits_tool_events_for_mcp_tool_call() -> None:
    """Start an in-process MCP server, point a ClaudeSession at it, and verify
    that ToolStarted and ToolResult events appear when the model calls the tool.
    """
    port = _find_free_port()
    mcp = _create_mcp_server()

    # Build the Starlette ASGI app and serve it with uvicorn in the background.
    mcp.settings.port = port
    mcp.settings.host = "127.0.0.1"
    app = mcp.streamable_http_app()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    # Run uvicorn as a background task so we can interact with it.
    server_task = asyncio.create_task(server.serve())

    session: ClaudeSession | None = None
    try:
        # Wait for the server to be ready.
        for _ in range(50):
            if server.started:
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("MCP server did not start within 5 seconds")

        mcp_url = f"http://127.0.0.1:{port}/mcp"

        session = ClaudeSession(
            background_agent_mcp_url=mcp_url,
            system_prompt=(
                "You have access to an MCP tool called 'echo'. "
                "When the user asks you to echo something, call the echo tool with "
                "the text parameter set to exactly what the user asked you to echo. "
                "Do NOT skip the tool call — always use the echo tool."
            ),
            max_turns=3,
        )
        await session.start()

        events = []
        async with asyncio.timeout(_TIMEOUT):
            async for event in session.send(
                "Use the echo tool to echo 'hello from spike test'"
            ):
                events.append(event)

        # --- Assertions ---

        # Verify ToolStarted with a name containing "echo" is present.
        # The SDK may namespace MCP tools (e.g. "mcp__archon__echo" or just "echo")
        # so we check with `in` rather than exact equality.
        tool_started_events = [
            e for e in events if isinstance(e, ToolStarted) and "echo" in e.name
        ]
        assert len(tool_started_events) >= 1, (
            f"Expected at least one ToolStarted with 'echo' in name, "
            f"got events: {[(type(e).__name__, getattr(e, 'name', '')) for e in events]}"
        )

        # Verify a corresponding ToolResult is present.
        tool_result_events = [e for e in events if isinstance(e, ToolResult)]
        assert len(tool_result_events) >= 1, (
            f"Expected at least one ToolResult, "
            f"got events: {[type(e).__name__ for e in events]}"
        )

        # Verify echo ToolStarted comes before the echo ToolResult.
        # The model may call built-in tools first, so we compare echo-specific events only.
        first_echo_started_idx = next(
            i for i, e in enumerate(events) if isinstance(e, ToolStarted) and "echo" in e.name
        )
        first_echo_result_idx = next(
            i for i, e in enumerate(events)
            if isinstance(e, ToolResult) and "echo" in e.tool_name
        )
        assert first_echo_started_idx < first_echo_result_idx, (
            f"Echo ToolStarted (index {first_echo_started_idx}) should precede "
            f"echo ToolResult (index {first_echo_result_idx})"
        )

        # Verify the ToolResult.tool_name also contains "echo".
        echo_tool_results = [
            e for e in tool_result_events if "echo" in e.tool_name
        ]
        assert len(echo_tool_results) >= 1, (
            f"Expected ToolResult with 'echo' in tool_name, "
            f"got tool_names: {[e.tool_name for e in tool_result_events]}"
        )

        # Verify the echo response content is somewhere in the results.
        echo_content_results = [
            e
            for e in tool_result_events
            if "ECHO:" in e.content or "hello from spike test" in e.content
        ]
        assert len(echo_content_results) >= 1, (
            f"Expected ToolResult containing echo output, "
            f"got results: {[e.content[:100] for e in tool_result_events]}"
        )
    finally:
        # Wrap each cleanup step independently so failures don't cascade.
        if session is not None:
            try:
                await asyncio.wait_for(session.stop(), timeout=10.0)
            except Exception:
                pass  # don't mask assertion errors
        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            server_task.cancel()
