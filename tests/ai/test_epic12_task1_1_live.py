"""Live E2E tests for Epic 12 Task 1.1 — background agents calling Archon toolkit via MCP.

Verifies the full chain:
  1. ArchonRouterMCPServer exposes toolkit tools to background agents
  2. Agent log contains toolkit tool entries (e.g. archon_status)
  3. Main session history does NOT contain toolkit calls from the agent
  4. No toolkit Telegram messages during agent execution

Run manually with:
  uv run pytest tests/ai/test_epic12_task1_1_live.py -m live -v

Requires a real Claude API key (ANTHROPIC_API_KEY env var) and the ``claude``
binary in PATH.
"""

import asyncio
import shutil
import socket
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, MagicMock

from archon.ai.agent_logger import AgentLogger
from archon.ai.archon_router_mcp_server import ArchonRouterMCPServer
from archon.ai.archon_toolkit import ArchonToolkit
from archon.ai.background_agent_manager import BackgroundAgentManager
from archon.ai.history_manager import HistoryManager

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        shutil.which("claude") is None,
        reason="claude binary not found in PATH",
    ),
]

_TIMEOUT = 120.0  # overall test timeout — agents may take time to complete
_USER_ID = 999_002  # synthetic; never reaches Telegram

# Tools background agents are allowed to call (matches gateway.py)
_BG_AGENT_ALLOWED_TOOLS = frozenset({
    "archon_status", "list_running_agents", "get_config",
    "get_job_config", "send_notification",
})


def _find_free_port() -> int:
    """Return an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _stub_bot(message_id: int = 99901) -> MagicMock:
    """Telegram Bot stub — captures calls but sends nothing."""
    sent = MagicMock()
    sent.message_id = message_id

    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=sent)
    bot.edit_message_text = AsyncMock()
    return bot


def _stub_session_manager() -> MagicMock:
    """Minimal SessionManager stub."""
    sm = MagicMock()
    sm.get_or_create = AsyncMock()
    sm.track_context = MagicMock()
    sm.inject_agent_context = MagicMock()
    return sm


async def test_agent_log_contains_toolkit_tool_entries(tmp_path: Path) -> None:
    """Background agent calling archon_status via MCP produces tool entries in agent log.

    Full chain: real ArchonRouterMCPServer -> real ClaudeSession -> real SDK ->
    agent calls archon_status -> AgentLogger writes to log file ->
    log file contains archon_status tool entries.
    """

    port = _find_free_port()
    history_dir = str(tmp_path / "history")
    bot = _stub_bot()
    sm = _stub_session_manager()

    # Create a real toolkit with archon_status available
    bam_for_toolkit = BackgroundAgentManager(bot=bot, session_manager=sm)
    toolkit = ArchonToolkit(bg_manager=bam_for_toolkit)

    # Start real ArchonRouterMCPServer with toolkit tools
    mcp_server = ArchonRouterMCPServer(
        history_root=history_dir,
        host="127.0.0.1",
        port=port,
        toolkit=toolkit,
        allowed_tools=_BG_AGENT_ALLOWED_TOOLS,
    )
    await mcp_server.start(host="127.0.0.1", port=port)

    # Create agent logger writing to tmp_path
    agent_logger = AgentLogger(directory=history_dir)

    # Create real BackgroundAgentManager with MCP server
    manager = BackgroundAgentManager(
        bot=bot,
        session_manager=sm,
        agent_logger=agent_logger,
        bg_mcp_server=mcp_server,
        beacon_interval_minutes=0,  # disable beacon for cleaner test
    )

    try:
        run = await manager.spawn(
            user_id=_USER_ID,
            task=(
                "You have access to an MCP tool called 'archon_status'. "
                "Call the archon_status tool exactly once, then reply with the word DONE. "
                "Do NOT skip the tool call — always use archon_status."
            ),
        )

        assert run._task_ref is not None
        async with asyncio.timeout(_TIMEOUT):
            await run._task_ref

        assert run.status == "completed", f"Agent status: {run.status}, error: {run.error}"

        # Read the agent log file
        assert run.log_path is not None, "Agent log path not set"
        assert run.log_path.exists(), f"Agent log file not found: {run.log_path}"
        log_content = run.log_path.read_text(encoding="utf-8")

        # The agent log must contain a tool entry for archon_status
        assert "archon_status" in log_content, (
            f"Expected 'archon_status' in agent log, got:\n{log_content[:2000]}"
        )
        # The tool marker emoji should be present (EventRenderer writes "### 🔧 Tool: ...")
        assert "🔧" in log_content, (
            f"Expected tool marker '🔧' in agent log, got:\n{log_content[:2000]}"
        )

    finally:
        await manager.stop_all()
        await mcp_server.stop()


async def test_main_history_does_not_contain_toolkit_calls(tmp_path: Path) -> None:
    """Main session history must NOT contain toolkit calls from the background agent.

    Only the agent's final Response (wrapped with agent name) should appear in
    main history — not individual ToolStarted/ToolResult events from the agent.
    """

    port = _find_free_port()
    history_dir = str(tmp_path / "history")
    bot = _stub_bot()
    sm = _stub_session_manager()

    bam_for_toolkit = BackgroundAgentManager(bot=bot, session_manager=sm)
    toolkit = ArchonToolkit(bg_manager=bam_for_toolkit)

    mcp_server = ArchonRouterMCPServer(
        history_root=history_dir,
        host="127.0.0.1",
        port=port,
        toolkit=toolkit,
        allowed_tools=_BG_AGENT_ALLOWED_TOOLS,
    )
    await mcp_server.start(host="127.0.0.1", port=port)

    agent_logger = AgentLogger(directory=history_dir)
    history_manager = HistoryManager(directory=history_dir)

    manager = BackgroundAgentManager(
        bot=bot,
        session_manager=sm,
        agent_logger=agent_logger,
        history_manager=history_manager,
        bg_mcp_server=mcp_server,
        beacon_interval_minutes=0,
    )

    try:
        run = await manager.spawn(
            user_id=_USER_ID,
            task=(
                "You have access to an MCP tool called 'archon_status'. "
                "Call the archon_status tool exactly once, then reply with the word DONE. "
                "Do NOT skip the tool call — always use archon_status."
            ),
        )

        assert run._task_ref is not None
        async with asyncio.timeout(_TIMEOUT):
            await run._task_ref

        assert run.status == "completed", f"Agent status: {run.status}, error: {run.error}"

        # First, verify the agent actually called the tool (prevents vacuous negative assertion)
        assert run.log_path is not None and run.log_path.exists(), "Agent log must exist"
        agent_log = run.log_path.read_text(encoding="utf-8")
        assert "archon_status" in agent_log, (
            "Agent must have called archon_status for this test to be meaningful. "
            f"Agent log:\n{agent_log[:2000]}"
        )

        # Read the main history file (daily YYYY-MM-DD.md)
        sessions_dir = tmp_path / "history" / "sessions"
        main_history_files = sorted(sessions_dir.glob("????-??-??.md"))

        main_history_content = ""
        for f in main_history_files:
            main_history_content += f.read_text(encoding="utf-8")

        # The agent result must be recorded (BAM records Response to history_manager)
        assert run.result, "Agent must produce a non-empty result"
        assert run.name in main_history_content, (
            f"Expected agent name '{run.name}' in main history, got:\n{main_history_content[:2000]}"
        )

        # archon_status tool call must NOT be in main history
        # (it's only in the agent's own log file)
        # Check that no "🔧 Tool: archon_status" or "### 🔧" lines from the agent appear
        # in the main history. The agent result goes through as Response, not as ToolStarted.
        tool_lines = [
            line for line in main_history_content.splitlines()
            if "archon_status" in line and "🔧" in line
        ]
        assert not tool_lines, (
            f"Main history should NOT contain toolkit tool entries from agent, "
            f"but found: {tool_lines}"
        )

    finally:
        await manager.stop_all()
        await mcp_server.stop()


async def test_no_toolkit_telegram_messages_during_agent_execution(tmp_path: Path) -> None:
    """The Telegram bot must NOT receive toolkit tool events during agent execution.

    Only spawn and completion notifications should go through bot.send_message.
    No '🔧' tool messages from the agent's toolkit calls should appear.
    """

    port = _find_free_port()
    history_dir = str(tmp_path / "history")
    bot = _stub_bot()
    sm = _stub_session_manager()

    bam_for_toolkit = BackgroundAgentManager(bot=bot, session_manager=sm)
    toolkit = ArchonToolkit(bg_manager=bam_for_toolkit)

    mcp_server = ArchonRouterMCPServer(
        history_root=history_dir,
        host="127.0.0.1",
        port=port,
        toolkit=toolkit,
        allowed_tools=_BG_AGENT_ALLOWED_TOOLS,
    )
    await mcp_server.start(host="127.0.0.1", port=port)

    agent_logger = AgentLogger(directory=history_dir)

    manager = BackgroundAgentManager(
        bot=bot,
        session_manager=sm,
        agent_logger=agent_logger,
        bg_mcp_server=mcp_server,
        beacon_interval_minutes=0,
    )

    try:
        run = await manager.spawn(
            user_id=_USER_ID,
            task=(
                "You have access to an MCP tool called 'archon_status'. "
                "Call the archon_status tool exactly once, then reply with the word DONE. "
                "Do NOT skip the tool call — always use archon_status."
            ),
        )

        assert run._task_ref is not None
        async with asyncio.timeout(_TIMEOUT):
            await run._task_ref

        assert run.status == "completed", f"Agent status: {run.status}, error: {run.error}"

        # First, verify the agent actually called the tool (prevents vacuous negative assertion)
        assert run.log_path is not None and run.log_path.exists(), "Agent log must exist"
        agent_log = run.log_path.read_text(encoding="utf-8")
        assert "archon_status" in agent_log, (
            "Agent must have called archon_status for this test to be meaningful. "
            f"Agent log:\n{agent_log[:2000]}"
        )

        # Inspect all bot.send_message calls
        all_calls = bot.send_message.call_args_list

        # Extract all message texts sent to Telegram
        telegram_texts = []
        for call in all_calls:
            args = call.args
            kwargs = call.kwargs
            # send_message(chat_id, text, ...) or send_message(chat_id=..., text=...)
            text = args[1] if len(args) > 1 else kwargs.get("text", "")
            telegram_texts.append(text)

        # No Telegram message should contain toolkit tool events
        # (spawn notification has "🤖", completion has "✅ 🤖" — neither has "🔧 Tool:")
        tool_messages = [t for t in telegram_texts if "🔧" in t and "archon_status" in t]
        assert not tool_messages, (
            f"Telegram should NOT receive toolkit tool events, "
            f"but found messages: {tool_messages}"
        )

        # Verify spawn + completion notifications are present
        spawn_msgs = [t for t in telegram_texts if "spawned" in t]
        assert spawn_msgs, "Expected spawn notification in Telegram messages"

        completion_msgs = [t for t in telegram_texts if "✅" in t]
        assert completion_msgs, "Expected completion notification in Telegram messages"

    finally:
        await manager.stop_all()
        await mcp_server.stop()
