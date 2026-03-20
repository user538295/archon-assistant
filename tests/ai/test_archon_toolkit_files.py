"""Tests for list_attachments toolkit tool — Epic 13 Task 2."""
import json
import os
import shutil
import socket
import time
from datetime import date
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, MagicMock

from aiohttp.test_utils import TestClient, TestServer

from archon.ai.archon_toolkit import ArchonToolkit
from archon.ai.attachment_store import AttachmentStore


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_toolkit(*, attachment_store: object | None = None) -> ArchonToolkit:
    return ArchonToolkit(attachment_store=attachment_store)


def _rpc(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    req: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        req["params"] = params
    return req


def _mock_store(entries: list[dict] | None = None) -> MagicMock:
    """Create a mock AttachmentStore with list_entries returning given entries."""
    store = MagicMock()
    store.list_entries.return_value = entries if entries is not None else []
    return store


# ──────────────────────────────────────────────────────────────────
# Unit tests — 2.3
# ──────────────────────────────────────────────────────────────────


class TestListAttachmentsSuccess:
    async def test_list_attachments_success(self) -> None:
        """Returns JSON array with all 8 fields in correct structure."""
        entries = [
            {
                "filename": "photo.png",
                "path": "2026-03-20/photo.png",
                "abs_path": "/tmp/attachments/2026-03-20/photo.png",
                "size_bytes": 1024,
                "size_human": "1.0 KB",
                "mime_type": "image/png",
                "date": "2026-03-20",
                "mtime": "2026-03-20T10:00:00+00:00",
            }
        ]
        store = _mock_store(entries)
        toolkit = _make_toolkit(attachment_store=store)

        result = await toolkit.call_tool("list_attachments", {})
        parsed = json.loads(result)

        assert len(parsed) == 1
        entry = parsed[0]
        assert entry["filename"] == "photo.png"
        assert entry["path"] == "2026-03-20/photo.png"
        assert entry["abs_path"] == "/tmp/attachments/2026-03-20/photo.png"
        assert entry["size_bytes"] == 1024
        assert entry["size_human"] == "1.0 KB"
        assert entry["mime_type"] == "image/png"
        assert entry["date"] == "2026-03-20"
        assert entry["mtime"] == "2026-03-20T10:00:00+00:00"
        store.list_entries.assert_called_once()


class TestListAttachmentsDateFilter:
    async def test_list_attachments_date_filter(self) -> None:
        """date param is passed through to store."""
        store = _mock_store([])
        toolkit = _make_toolkit(attachment_store=store)

        await toolkit.call_tool("list_attachments", {"date": "2026-03-20"})

        store.list_entries.assert_called_once()
        call_kwargs = store.list_entries.call_args[1]
        assert call_kwargs["date"] == "2026-03-20"


class TestListAttachmentsMimeFilter:
    async def test_list_attachments_mime_filter(self) -> None:
        """mime_pattern param is passed through as mime_prefix to store."""
        store = _mock_store([])
        toolkit = _make_toolkit(attachment_store=store)

        await toolkit.call_tool("list_attachments", {"mime_pattern": "image/"})

        store.list_entries.assert_called_once()
        call_kwargs = store.list_entries.call_args[1]
        assert call_kwargs["mime_prefix"] == "image/"


class TestListAttachmentsLimitDefault:
    async def test_list_attachments_limit_default(self) -> None:
        """Default limit is 50."""
        store = _mock_store([])
        toolkit = _make_toolkit(attachment_store=store)

        await toolkit.call_tool("list_attachments", {})

        call_kwargs = store.list_entries.call_args[1]
        assert call_kwargs["limit"] == 50


class TestListAttachmentsLimitClamped:
    async def test_list_attachments_limit_clamped(self) -> None:
        """limit > 200 is clamped to 200."""
        store = _mock_store([])
        toolkit = _make_toolkit(attachment_store=store)

        await toolkit.call_tool("list_attachments", {"limit": 999})

        call_kwargs = store.list_entries.call_args[1]
        assert call_kwargs["limit"] == 200


class TestListAttachmentsEmpty:
    async def test_list_attachments_empty(self) -> None:
        """Returns '[]' when no files."""
        store = _mock_store([])
        toolkit = _make_toolkit(attachment_store=store)

        result = await toolkit.call_tool("list_attachments", {})
        assert result == "[]"


class TestListAttachmentsMissingStore:
    async def test_list_attachments_missing_store(self) -> None:
        """Raises RuntimeError when attachment_store is None."""
        toolkit = _make_toolkit(attachment_store=None)

        with pytest.raises(RuntimeError, match="attachment_store not available"):
            await toolkit.call_tool("list_attachments", {})


class TestListAttachmentsLimitInvalid:
    async def test_list_attachments_limit_invalid(self) -> None:
        """Non-numeric limit returns an error string."""
        store = _mock_store([])
        toolkit = _make_toolkit(attachment_store=store)

        result = await toolkit.call_tool("list_attachments", {"limit": "abc"})

        assert "Invalid limit" in result
        store.list_entries.assert_not_called()


class TestListAttachmentsLimitNegative:
    async def test_list_attachments_limit_negative(self) -> None:
        """Negative limit is clamped to 1."""
        store = _mock_store([])
        toolkit = _make_toolkit(attachment_store=store)

        await toolkit.call_tool("list_attachments", {"limit": -5})

        call_kwargs = store.list_entries.call_args[1]
        assert call_kwargs["limit"] == 1


class TestListAttachmentsLimitZero:
    async def test_list_attachments_limit_zero(self) -> None:
        """Zero limit is clamped to 1."""
        store = _mock_store([])
        toolkit = _make_toolkit(attachment_store=store)

        await toolkit.call_tool("list_attachments", {"limit": 0})

        call_kwargs = store.list_entries.call_args[1]
        assert call_kwargs["limit"] == 1


class TestListAttachmentsInvalidDate:
    async def test_list_attachments_invalid_date(self) -> None:
        """Invalid date format returns an error string."""
        store = _mock_store([])
        toolkit = _make_toolkit(attachment_store=store)

        result = await toolkit.call_tool("list_attachments", {"date": "not-a-date"})

        assert "Invalid date format" in result
        store.list_entries.assert_not_called()


class TestListAttachmentsMimeNonString:
    async def test_list_attachments_mime_non_string(self) -> None:
        """Non-string mime_pattern is coerced to string."""
        store = _mock_store([])
        toolkit = _make_toolkit(attachment_store=store)

        await toolkit.call_tool("list_attachments", {"mime_pattern": 123})

        call_kwargs = store.list_entries.call_args[1]
        assert call_kwargs["mime_prefix"] == "123"


class TestListAttachmentsStoreError:
    async def test_list_attachments_store_error(self) -> None:
        """OSError from list_entries propagates (not caught by handler)."""
        store = MagicMock()
        store.list_entries.side_effect = OSError("Permission denied")
        toolkit = _make_toolkit(attachment_store=store)

        with pytest.raises(OSError, match="Permission denied"):
            await toolkit.call_tool("list_attachments", {})


# ──────────────────────────────────────────────────────────────────
# Integration tests — 2.5
# ──────────────────────────────────────────────────────────────────


class TestListAttachmentsViaMcp:
    async def test_list_attachments_via_mcp(self, tmp_path: Path) -> None:
        """list_attachments is callable via ArchonRouterMCPServer when in allowed_tools."""
        from archon.ai.archon_router_mcp_server import ArchonRouterMCPServer

        store = _mock_store([{
            "filename": "test.txt",
            "path": "2026-03-20/test.txt",
            "abs_path": "/tmp/attachments/2026-03-20/test.txt",
            "size_bytes": 42,
            "size_human": "42 B",
            "mime_type": "text/plain",
            "date": "2026-03-20",
            "mtime": "2026-03-20T12:00:00+00:00",
        }])
        toolkit = _make_toolkit(attachment_store=store)

        server = ArchonRouterMCPServer(
            history_root=str(tmp_path), toolkit=toolkit,
            allowed_tools=frozenset({"list_attachments"}),
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            # Verify tool is listed via /mcp/{user_id}
            resp = await client.post(
                "/mcp/42",
                json=_rpc("tools/list"),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            tool_names = {t["name"] for t in data["result"]["tools"]}
            assert "list_attachments" in tool_names

            # Call tool via /mcp/{user_id}
            resp = await client.post(
                "/mcp/42",
                json=_rpc(
                    "tools/call",
                    {"name": "list_attachments", "arguments": {}},
                ),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
            result_text = data["result"]["content"][0]["text"]
            parsed = json.loads(result_text)
            assert len(parsed) == 1
            assert parsed[0]["filename"] == "test.txt"
        finally:
            await client.close()


class TestListAttachmentsBlockedWhenNotAllowed:
    async def test_list_attachments_blocked_when_not_allowed(self, tmp_path: Path) -> None:
        """list_attachments is not exposed when not in allowed_tools."""
        from archon.ai.archon_router_mcp_server import ArchonRouterMCPServer

        store = _mock_store([])
        toolkit = _make_toolkit(attachment_store=store)

        # allowed_tools does NOT include list_attachments
        server = ArchonRouterMCPServer(
            history_root=str(tmp_path), toolkit=toolkit,
            allowed_tools=frozenset({"archon_status"}),
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            # Tool should not appear in tools/list on user route
            resp = await client.post(
                "/mcp/42",
                json=_rpc("tools/list"),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            tool_names = {t["name"] for t in data["result"]["tools"]}
            assert "list_attachments" not in tool_names

            # Calling it should be rejected
            resp = await client.post(
                "/mcp/42",
                json=_rpc(
                    "tools/call",
                    {"name": "list_attachments", "arguments": {}},
                ),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert "error" in data
            assert data["error"]["code"] == -32602
        finally:
            await client.close()


# ──────────────────────────────────────────────────────────────────
# E2E test — 2.6
# ──────────────────────────────────────────────────────────────────


class TestListAttachmentsE2eRealStore:
    async def test_list_attachments_e2e_real_store(self, tmp_path: Path) -> None:
        """E2E: create real AttachmentStore, save files, call toolkit, verify JSON and order."""
        store = AttachmentStore(tmp_path / "attachments")
        d = date(2026, 3, 20)
        store.save("photo.png", b"\x89PNG" + b"\x00" * 100, d)
        # Ensure different mtime by touching the file after a brief delay
        time.sleep(0.05)
        store.save("document.pdf", b"%PDF-1.4" + b"\x00" * 50, d)

        toolkit = _make_toolkit(attachment_store=store)

        result = await toolkit.call_tool("list_attachments", {})
        parsed = json.loads(result)

        assert len(parsed) == 2
        filenames = [e["filename"] for e in parsed]
        assert set(filenames) == {"photo.png", "document.pdf"}
        # Newest first — document.pdf was saved last
        assert filenames[0] == "document.pdf"
        assert filenames[1] == "photo.png"

        # Verify each entry has all 8 expected keys
        for entry in parsed:
            assert "filename" in entry
            assert "path" in entry
            assert "abs_path" in entry
            assert "size_bytes" in entry
            assert "size_human" in entry
            assert "mime_type" in entry
            assert "date" in entry
            assert "mtime" in entry

        # Verify date filter works
        result_filtered = await toolkit.call_tool(
            "list_attachments", {"date": "2026-03-20"}
        )
        parsed_filtered = json.loads(result_filtered)
        assert len(parsed_filtered) == 2

        # No match on wrong date
        result_empty = await toolkit.call_tool(
            "list_attachments", {"date": "2026-01-01"}
        )
        assert result_empty == "[]"

        # MIME filter
        result_images = await toolkit.call_tool(
            "list_attachments", {"mime_pattern": "image/"}
        )
        parsed_images = json.loads(result_images)
        assert len(parsed_images) == 1
        assert parsed_images[0]["filename"] == "photo.png"


# ──────────────────────────────────────────────────────────────────
# Live E2E test — 2.7
# ──────────────────────────────────────────────────────────────────


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


_LIVE_TIMEOUT = 120.0
_USER_ID = 999_003


@pytest.mark.live
@pytest.mark.skipif(
    shutil.which("claude") is None,
    reason="claude binary not found in PATH",
)
async def test_list_attachments_live_agent(tmp_path: Path) -> None:
    """Live E2E: background agent calls list_attachments via MCP and references the filename."""
    import asyncio
    from archon.ai.agent_logger import AgentLogger
    from archon.ai.archon_router_mcp_server import ArchonRouterMCPServer
    from archon.ai.background_agent_manager import BackgroundAgentManager

    port = _find_free_port()
    history_dir = str(tmp_path / "history")
    bot = _stub_bot()
    sm = _stub_session_manager()

    # Create real AttachmentStore with a test file
    attachments_dir = tmp_path / "attachments"
    store = AttachmentStore(attachments_dir)
    store.save("test_report.pdf", b"%PDF-1.4 test content", date(2026, 3, 20))

    # Create real toolkit with attachment store
    bam_for_toolkit = BackgroundAgentManager(bot=bot, session_manager=sm)
    toolkit = ArchonToolkit(
        bg_manager=bam_for_toolkit,
        attachment_store=store,
    )

    # Start real ArchonRouterMCPServer with list_attachments allowed
    mcp_server = ArchonRouterMCPServer(
        history_root=history_dir,
        host="127.0.0.1",
        port=port,
        toolkit=toolkit,
        allowed_tools=frozenset({"list_attachments"}),
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
                "You have access to an MCP tool called 'list_attachments'. "
                "Call the list_attachments tool exactly once, then reply with the filename you found. "
                "Do NOT skip the tool call — always use list_attachments."
            ),
        )

        assert run._task_ref is not None
        async with asyncio.timeout(_LIVE_TIMEOUT):
            await run._task_ref

        assert run.status == "completed", f"Agent status: {run.status}, error: {run.error}"

        # Read the agent log file
        assert run.log_path is not None, "Agent log path not set"
        assert run.log_path.exists(), f"Agent log file not found: {run.log_path}"
        log_content = run.log_path.read_text(encoding="utf-8")

        # Verify agent called list_attachments
        assert "list_attachments" in log_content, (
            f"Expected 'list_attachments' in agent log, got:\n{log_content[:2000]}"
        )

        # Verify agent response references the test filename
        assert run.result is not None, "Agent must produce a non-empty result"
        assert "test_report" in run.result.lower() or "test_report" in log_content.lower(), (
            f"Expected 'test_report' in agent result or log.\n"
            f"Result: {run.result[:500]}\n"
            f"Log: {log_content[:2000]}"
        )

    finally:
        await manager.stop_all()
        await mcp_server.stop()
