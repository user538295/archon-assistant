"""Tests for file toolkit tools — Epic 13 Tasks 2 & 3."""
import json
import os
import shutil
import socket
import time
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
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


# ══════════════════════════════════════════════════════════════════
# send_file tests — Epic 13 Task 3
# ══════════════════════════════════════════════════════════════════


def _make_send_file_toolkit(
    *,
    bot: object | None = None,
    config: object | None = None,
    attachment_store: object | None = None,
    clock: object | None = None,
) -> ArchonToolkit:
    kwargs: dict = {"bot": bot}
    if config is not None:
        kwargs["config"] = config
    if attachment_store is not None:
        kwargs["attachment_store"] = attachment_store
    if clock is not None:
        kwargs["clock"] = clock
    return ArchonToolkit(**kwargs)


def _make_config_with_cwd(cwd: str, allowed_user_ids: list[int] | None = None) -> MagicMock:
    config = MagicMock()
    config.session.working_directory = cwd
    config.access.allowed_user_ids = allowed_user_ids or [42]
    return config


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_document = AsyncMock()
    return bot


def _make_attachment_store(base_dir: Path) -> MagicMock:
    store = MagicMock()
    store.base_dir = base_dir
    return store


# ──────────────────────────────────────────────────────────────────
# Unit tests — 3.2
# ──────────────────────────────────────────────────────────────────


class TestSendFileSuccess:
    async def test_send_file_success(self, tmp_path: Path) -> None:
        """bot.send_document called with FSInputFile, returns success with filename and size."""
        test_file = tmp_path / "report.txt"
        test_file.write_text("hello world")

        bot = _make_bot()
        config = _make_config_with_cwd(str(tmp_path))
        store = _make_attachment_store(tmp_path / "attachments")
        toolkit = _make_send_file_toolkit(bot=bot, config=config, attachment_store=store)

        result = await toolkit.call_tool(
            "send_file",
            {"user_id": 42, "file_path": str(test_file)},
        )

        bot.send_document.assert_called_once()
        assert "report.txt" in result
        assert "sent" in result.lower()

        call_kwargs = bot.send_document.call_args
        document = call_kwargs.kwargs.get("document") or call_kwargs[1].get("document")
        from aiogram.types import FSInputFile
        assert isinstance(document, FSInputFile)


class TestSendFileWithCaption:
    async def test_send_file_with_caption(self, tmp_path: Path) -> None:
        """caption passed through to send_document (HTML-escaped)."""
        test_file = tmp_path / "doc.txt"
        test_file.write_text("content")

        bot = _make_bot()
        config = _make_config_with_cwd(str(tmp_path))
        store = _make_attachment_store(tmp_path / "attachments")
        toolkit = _make_send_file_toolkit(bot=bot, config=config, attachment_store=store)

        await toolkit.call_tool(
            "send_file",
            {"user_id": 42, "file_path": str(test_file), "caption": "See <this>"},
        )

        bot.send_document.assert_called_once()
        call_kwargs = bot.send_document.call_args[1]
        assert call_kwargs["caption"] == "See &lt;this&gt;"


class TestSendFileRelativePath:
    async def test_send_file_relative_path(self, tmp_path: Path) -> None:
        """relative path resolved against CWD from config."""
        test_file = tmp_path / "data.csv"
        test_file.write_text("a,b,c")

        bot = _make_bot()
        config = _make_config_with_cwd(str(tmp_path))
        store = _make_attachment_store(tmp_path / "attachments")
        toolkit = _make_send_file_toolkit(bot=bot, config=config, attachment_store=store)

        result = await toolkit.call_tool(
            "send_file",
            {"user_id": 42, "file_path": "data.csv"},
        )

        bot.send_document.assert_called_once()
        assert "data.csv" in result


class TestSendFileFromAttachmentsDir:
    async def test_send_file_from_attachments_dir(self, tmp_path: Path) -> None:
        """path within attachment store allowed."""
        att_dir = tmp_path / "attachments"
        att_dir.mkdir()
        att_file = att_dir / "photo.png"
        att_file.write_bytes(b"\x89PNG" + b"\x00" * 10)

        bot = _make_bot()
        cwd = tmp_path / "project"
        cwd.mkdir()
        config = _make_config_with_cwd(str(cwd))
        store = _make_attachment_store(att_dir)
        toolkit = _make_send_file_toolkit(bot=bot, config=config, attachment_store=store)

        result = await toolkit.call_tool(
            "send_file",
            {"user_id": 42, "file_path": str(att_file)},
        )

        bot.send_document.assert_called_once()
        assert "photo.png" in result


class TestSendFilePathEscapeRejected:
    async def test_send_file_path_escape_rejected(self, tmp_path: Path) -> None:
        """path outside CWD + attachments_dir returns error string."""
        cwd = tmp_path / "project"
        cwd.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("secret")

        bot = _make_bot()
        config = _make_config_with_cwd(str(cwd))
        store = _make_attachment_store(tmp_path / "attachments")
        toolkit = _make_send_file_toolkit(bot=bot, config=config, attachment_store=store)

        result = await toolkit.call_tool(
            "send_file",
            {"user_id": 42, "file_path": str(outside)},
        )

        assert "not allowed" in result.lower() or "outside" in result.lower()
        bot.send_document.assert_not_called()


class TestSendFileSymlinkEscapeRejected:
    async def test_send_file_symlink_escape_rejected(self, tmp_path: Path) -> None:
        """symlink pointing outside allowed dirs rejected."""
        cwd = tmp_path / "project"
        cwd.mkdir()
        outside = tmp_path / "secret.txt"
        outside.write_text("secret data")
        link = cwd / "link.txt"
        os.symlink(outside, link)

        bot = _make_bot()
        config = _make_config_with_cwd(str(cwd))
        store = _make_attachment_store(tmp_path / "attachments")
        toolkit = _make_send_file_toolkit(bot=bot, config=config, attachment_store=store)

        result = await toolkit.call_tool(
            "send_file",
            {"user_id": 42, "file_path": str(link)},
        )

        assert "not allowed" in result.lower() or "outside" in result.lower()
        bot.send_document.assert_not_called()


class TestSendFileNotFound:
    async def test_send_file_not_found(self, tmp_path: Path) -> None:
        """nonexistent path returns error string."""
        bot = _make_bot()
        config = _make_config_with_cwd(str(tmp_path))
        store = _make_attachment_store(tmp_path / "attachments")
        toolkit = _make_send_file_toolkit(bot=bot, config=config, attachment_store=store)

        result = await toolkit.call_tool(
            "send_file",
            {"user_id": 42, "file_path": str(tmp_path / "nope.txt")},
        )

        assert "not found" in result.lower() or "does not exist" in result.lower()
        bot.send_document.assert_not_called()


class TestSendFileIsDirectory:
    async def test_send_file_is_directory(self, tmp_path: Path) -> None:
        """directory path returns error string."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        bot = _make_bot()
        config = _make_config_with_cwd(str(tmp_path))
        store = _make_attachment_store(tmp_path / "attachments")
        toolkit = _make_send_file_toolkit(bot=bot, config=config, attachment_store=store)

        result = await toolkit.call_tool(
            "send_file",
            {"user_id": 42, "file_path": str(subdir)},
        )

        assert "not a file" in result.lower() or "directory" in result.lower()
        bot.send_document.assert_not_called()


class TestSendFileTooLarge:
    async def test_send_file_too_large(self, tmp_path: Path) -> None:
        """file > 50 MB returns error string (without uploading)."""
        test_file = tmp_path / "big.bin"
        test_file.write_bytes(b"\x00" * 10)  # small real file

        bot = _make_bot()
        config = _make_config_with_cwd(str(tmp_path))
        store = _make_attachment_store(tmp_path / "attachments")
        toolkit = _make_send_file_toolkit(bot=bot, config=config, attachment_store=store)

        # Intercept os.stat to report > 50 MB for the target file only
        real_stat = test_file.stat()
        big_stat = os.stat_result((
            real_stat.st_mode, real_stat.st_ino, real_stat.st_dev,
            real_stat.st_nlink, real_stat.st_uid, real_stat.st_gid,
            51 * 1024 * 1024,  # st_size
            int(real_stat.st_atime), int(real_stat.st_mtime), int(real_stat.st_ctime),
        ))
        resolved_str = str(test_file.resolve())
        original_os_stat = os.stat

        def patched_os_stat(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
            if str(path) == resolved_str:
                return big_stat
            return original_os_stat(path, *args, **kwargs)

        with patch("os.stat", side_effect=patched_os_stat):
            result = await toolkit.call_tool(
                "send_file",
                {"user_id": 42, "file_path": str(test_file)},
            )

        assert "too large" in result.lower()
        bot.send_document.assert_not_called()


class TestSendFileBotUnavailable:
    async def test_send_file_bot_unavailable(self, tmp_path: Path) -> None:
        """raises RuntimeError when bot is None."""
        config = _make_config_with_cwd(str(tmp_path))
        toolkit = _make_send_file_toolkit(bot=None, config=config)

        with pytest.raises(RuntimeError, match="bot not available"):
            await toolkit.call_tool(
                "send_file",
                {"user_id": 42, "file_path": "/some/file.txt"},
            )


class TestSendFileInvalidUserId:
    async def test_send_file_invalid_user_id(self, tmp_path: Path) -> None:
        """returns error string for missing/invalid user_id."""
        bot = _make_bot()
        config = _make_config_with_cwd(str(tmp_path))
        toolkit = _make_send_file_toolkit(bot=bot, config=config)

        result = await toolkit.call_tool(
            "send_file",
            {"file_path": "/some/file.txt"},
        )

        assert "invalid" in result.lower() or "user_id" in result.lower()
        bot.send_document.assert_not_called()


class TestSendFileNonWhitelistedUserRejected:
    async def test_send_file_non_whitelisted_user_rejected(self, tmp_path: Path) -> None:
        """user_id not in allowed_user_ids returns error string."""
        bot = _make_bot()
        config = _make_config_with_cwd(str(tmp_path), allowed_user_ids=[42])
        store = _make_attachment_store(tmp_path / "attachments")
        toolkit = _make_send_file_toolkit(bot=bot, config=config, attachment_store=store)

        result = await toolkit.call_tool(
            "send_file",
            {"user_id": 999, "file_path": str(tmp_path / "file.txt")},
        )

        assert "not allowed" in result.lower() or "unauthorized" in result.lower()
        bot.send_document.assert_not_called()


class TestSendFileTelegramError:
    async def test_send_file_telegram_error(self, tmp_path: Path) -> None:
        """Telegram API exception caught, returns error string."""
        test_file = tmp_path / "doc.txt"
        test_file.write_text("data")

        bot = _make_bot()
        bot.send_document = AsyncMock(side_effect=Exception("Telegram API error"))
        config = _make_config_with_cwd(str(tmp_path))
        store = _make_attachment_store(tmp_path / "attachments")
        toolkit = _make_send_file_toolkit(bot=bot, config=config, attachment_store=store)

        result = await toolkit.call_tool(
            "send_file",
            {"user_id": 42, "file_path": str(test_file)},
        )

        assert "failed" in result.lower()
        assert "doc.txt" in result


class TestSendFileRateLimited:
    async def test_send_file_rate_limited(self, tmp_path: Path) -> None:
        """second call within 10s returns rate-limit message."""
        clock_value = 0.0

        def _clock() -> float:
            return clock_value

        test_file = tmp_path / "file.txt"
        test_file.write_text("content")

        bot = _make_bot()
        config = _make_config_with_cwd(str(tmp_path))
        store = _make_attachment_store(tmp_path / "attachments")
        toolkit = _make_send_file_toolkit(
            bot=bot, config=config, attachment_store=store, clock=_clock,
        )

        # First call at t=0
        r1 = await toolkit.call_tool(
            "send_file",
            {"user_id": 42, "file_path": str(test_file)},
        )
        assert "sent" in r1.lower()

        # Second call at t=5 — within 10s window
        clock_value = 5.0
        r2 = await toolkit.call_tool(
            "send_file",
            {"user_id": 42, "file_path": str(test_file)},
        )
        assert "rate limited" in r2.lower()
        assert bot.send_document.call_count == 1


class TestSendFileRateLimitExpires:
    async def test_send_file_rate_limit_expires(self, tmp_path: Path) -> None:
        """call after 10s succeeds (injectable clock)."""
        clock_value = 0.0

        def _clock() -> float:
            return clock_value

        test_file = tmp_path / "file.txt"
        test_file.write_text("content")

        bot = _make_bot()
        config = _make_config_with_cwd(str(tmp_path))
        store = _make_attachment_store(tmp_path / "attachments")
        toolkit = _make_send_file_toolkit(
            bot=bot, config=config, attachment_store=store, clock=_clock,
        )

        # First call at t=0
        await toolkit.call_tool(
            "send_file",
            {"user_id": 42, "file_path": str(test_file)},
        )

        # Second call at t=11 — after 10s window
        clock_value = 11.0
        r2 = await toolkit.call_tool(
            "send_file",
            {"user_id": 42, "file_path": str(test_file)},
        )
        assert "sent" in r2.lower()
        assert bot.send_document.call_count == 2


class TestSendFileRateLimitPerUser:
    async def test_send_file_rate_limit_per_user(self, tmp_path: Path) -> None:
        """Rate limit is per-user — different users can send within the same window."""
        clock_value = 0.0

        def _clock() -> float:
            return clock_value

        test_file = tmp_path / "file.txt"
        test_file.write_text("content")

        bot = _make_bot()
        config = _make_config_with_cwd(str(tmp_path), allowed_user_ids=[42, 43])
        store = _make_attachment_store(tmp_path / "attachments")
        toolkit = _make_send_file_toolkit(
            bot=bot, config=config, attachment_store=store, clock=_clock,
        )

        # User 42 sends at t=0
        r1 = await toolkit.call_tool(
            "send_file",
            {"user_id": 42, "file_path": str(test_file)},
        )
        assert "sent" in r1.lower()

        # User 43 sends at t=5 — different user, should succeed
        clock_value = 5.0
        r2 = await toolkit.call_tool(
            "send_file",
            {"user_id": 43, "file_path": str(test_file)},
        )
        assert "sent" in r2.lower()
        assert bot.send_document.call_count == 2


class TestSendFileCaptionTruncated:
    async def test_send_file_caption_truncated(self, tmp_path: Path) -> None:
        """caption > 1024 chars (after escape) truncated with suffix."""
        test_file = tmp_path / "file.txt"
        test_file.write_text("content")

        bot = _make_bot()
        config = _make_config_with_cwd(str(tmp_path))
        store = _make_attachment_store(tmp_path / "attachments")
        toolkit = _make_send_file_toolkit(bot=bot, config=config, attachment_store=store)

        long_caption = "<" * 2000
        await toolkit.call_tool(
            "send_file",
            {"user_id": 42, "file_path": str(test_file), "caption": long_caption},
        )

        call_kwargs = bot.send_document.call_args[1]
        assert len(call_kwargs["caption"]) <= 1024
        assert call_kwargs["caption"].endswith("… [truncated]")


class TestSendFileCaptionEscapeThenTruncate:
    async def test_send_file_caption_escape_then_truncate(self, tmp_path: Path) -> None:
        """Truncation happens after HTML escape — 500 '<' becomes 2000-char '&lt;' then truncated."""
        test_file = tmp_path / "file.txt"
        test_file.write_text("content")

        bot = _make_bot()
        config = _make_config_with_cwd(str(tmp_path))
        store = _make_attachment_store(tmp_path / "attachments")
        toolkit = _make_send_file_toolkit(bot=bot, config=config, attachment_store=store)

        # 500 '<' chars → after html.escape each becomes '&lt;' (4 chars) = 2000 chars
        caption = "<" * 500
        await toolkit.call_tool(
            "send_file",
            {"user_id": 42, "file_path": str(test_file), "caption": caption},
        )

        call_kwargs = bot.send_document.call_args[1]
        assert len(call_kwargs["caption"]) <= 1024


class TestSendFileCaptionEntityNotSplit:
    async def test_send_file_caption_entity_not_split(self, tmp_path: Path) -> None:
        """Truncation must not split HTML entities like &lt; mid-entity."""
        test_file = tmp_path / "file.txt"
        test_file.write_text("content")

        bot = _make_bot()
        config = _make_config_with_cwd(str(tmp_path))
        store = _make_attachment_store(tmp_path / "attachments")
        toolkit = _make_send_file_toolkit(bot=bot, config=config, attachment_store=store)

        # Build a caption where the cut point falls inside an HTML entity
        # Fill most of the caption with safe chars, then add '<' near the cut point
        safe_prefix = "a" * 1000
        caption = safe_prefix + "<" * 50  # escape expands '<' to '&lt;' (4 chars each)
        await toolkit.call_tool(
            "send_file",
            {"user_id": 42, "file_path": str(test_file), "caption": caption},
        )

        call_kwargs = bot.send_document.call_args[1]
        sent_caption = call_kwargs["caption"]
        assert len(sent_caption) <= 1024
        # Verify no broken entity: no '&' without a following ';' before end/suffix
        import re
        # Check there's no incomplete entity (& followed by chars but no ;)
        assert not re.search(r"&[a-zA-Z]{1,5}[^;]?$", sent_caption.rstrip("… [truncated]"))


class TestSendFileCaptionHtmlEscaped:
    async def test_send_file_caption_html_escaped(self, tmp_path: Path) -> None:
        """caption with <>&  chars is HTML-escaped before sending."""
        test_file = tmp_path / "file.txt"
        test_file.write_text("content")

        bot = _make_bot()
        config = _make_config_with_cwd(str(tmp_path))
        store = _make_attachment_store(tmp_path / "attachments")
        toolkit = _make_send_file_toolkit(bot=bot, config=config, attachment_store=store)

        await toolkit.call_tool(
            "send_file",
            {"user_id": 42, "file_path": str(test_file), "caption": "a < b & c > d"},
        )

        call_kwargs = bot.send_document.call_args[1]
        assert "&lt;" in call_kwargs["caption"]
        assert "&amp;" in call_kwargs["caption"]
        assert "&gt;" in call_kwargs["caption"]


# ──────────────────────────────────────────────────────────────────
# Integration tests — 3.4
# ──────────────────────────────────────────────────────────────────


class TestSendFileViaMcp:
    async def test_send_file_via_mcp(self, tmp_path: Path) -> None:
        """send_file is callable via ArchonRouterMCPServer when in allowed_tools."""
        from archon.ai.archon_router_mcp_server import ArchonRouterMCPServer

        test_file = tmp_path / "report.txt"
        test_file.write_text("report content")

        bot = _make_bot()
        config = _make_config_with_cwd(str(tmp_path), allowed_user_ids=[42])
        store = _make_attachment_store(tmp_path / "attachments")
        toolkit = _make_send_file_toolkit(bot=bot, config=config, attachment_store=store)

        server = ArchonRouterMCPServer(
            history_root=str(tmp_path / "history"),
            toolkit=toolkit,
            allowed_tools=frozenset({"send_file"}),
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            # Verify tool is listed
            resp = await client.post(
                "/mcp/42",
                json=_rpc("tools/list"),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            tool_names = {t["name"] for t in data["result"]["tools"]}
            assert "send_file" in tool_names

            # Call tool
            resp = await client.post(
                "/mcp/42",
                json=_rpc(
                    "tools/call",
                    {"name": "send_file", "arguments": {
                        "user_id": 42,
                        "file_path": str(test_file),
                    }},
                ),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
            assert "report.txt" in data["result"]["content"][0]["text"]
        finally:
            await client.close()


class TestSendFileBlockedWhenNotAllowed:
    async def test_send_file_blocked_when_not_allowed(self, tmp_path: Path) -> None:
        """send_file is not exposed when not in allowed_tools."""
        from archon.ai.archon_router_mcp_server import ArchonRouterMCPServer

        bot = _make_bot()
        config = _make_config_with_cwd(str(tmp_path))
        toolkit = _make_send_file_toolkit(bot=bot, config=config)

        server = ArchonRouterMCPServer(
            history_root=str(tmp_path / "history"),
            toolkit=toolkit,
            allowed_tools=frozenset({"archon_status"}),
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            resp = await client.post(
                "/mcp/42",
                json=_rpc("tools/list"),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            tool_names = {t["name"] for t in data["result"]["tools"]}
            assert "send_file" not in tool_names

            resp = await client.post(
                "/mcp/42",
                json=_rpc(
                    "tools/call",
                    {"name": "send_file", "arguments": {
                        "user_id": 42,
                        "file_path": "/some/file.txt",
                    }},
                ),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert "error" in data
            assert data["error"]["code"] == -32602
        finally:
            await client.close()


# ──────────────────────────────────────────────────────────────────
# E2E test — 3.5
# ──────────────────────────────────────────────────────────────────


class TestSendFileE2eRealFile:
    async def test_send_file_e2e_real_file(self, tmp_path: Path) -> None:
        """E2E: create real file in tmp CWD, mock bot, call toolkit, verify bot.send_document."""
        cwd = tmp_path / "project"
        cwd.mkdir()
        test_file = cwd / "analysis.txt"
        test_file.write_text("Analysis results\nLine 2\nLine 3")

        att_dir = tmp_path / "attachments"
        att_dir.mkdir()
        store = AttachmentStore(att_dir)

        bot = _make_bot()
        config = _make_config_with_cwd(str(cwd), allowed_user_ids=[42])
        toolkit = ArchonToolkit(
            bot=bot,
            config=config,
            attachment_store=store,
        )

        result = await toolkit.call_tool(
            "send_file",
            {"user_id": 42, "file_path": str(test_file)},
        )

        assert "analysis.txt" in result
        assert "sent" in result.lower()
        bot.send_document.assert_called_once()

        # Verify FSInputFile path
        call_kwargs = bot.send_document.call_args[1]
        fs_input = call_kwargs["document"]
        assert str(test_file) in str(fs_input.path)


# ──────────────────────────────────────────────────────────────────
# Live E2E test — 3.6
# ──────────────────────────────────────────────────────────────────


@pytest.mark.live
@pytest.mark.skipif(
    shutil.which("claude") is None,
    reason="claude binary not found in PATH",
)
async def test_send_file_live_agent(tmp_path: Path) -> None:
    """Live E2E: background agent calls send_file via MCP."""
    import asyncio
    from archon.ai.agent_logger import AgentLogger
    from archon.ai.archon_router_mcp_server import ArchonRouterMCPServer
    from archon.ai.background_agent_manager import BackgroundAgentManager

    port = _find_free_port()
    history_dir = str(tmp_path / "history")
    bot = _stub_bot()
    bot.send_document = AsyncMock()
    sm = _stub_session_manager()

    # Create real file in tmp CWD
    cwd = tmp_path / "project"
    cwd.mkdir()
    test_file = cwd / "results.txt"
    test_file.write_text("Test results data")

    # Create real attachment store
    att_dir = tmp_path / "attachments"
    store = AttachmentStore(att_dir)

    # Config with CWD pointing to tmp
    config = _make_config_with_cwd(str(cwd), allowed_user_ids=[_USER_ID])

    # Create real toolkit
    bam_for_toolkit = BackgroundAgentManager(bot=bot, session_manager=sm)
    toolkit = ArchonToolkit(
        bg_manager=bam_for_toolkit,
        bot=bot,
        config=config,
        attachment_store=store,
    )

    # Start real ArchonRouterMCPServer with send_file allowed
    mcp_server = ArchonRouterMCPServer(
        history_root=history_dir,
        host="127.0.0.1",
        port=port,
        toolkit=toolkit,
        allowed_tools=frozenset({"send_file"}),
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
                f"You have access to an MCP tool called 'send_file'. "
                f"Call the send_file tool to send the file at '{test_file}' "
                f"to user_id {_USER_ID}. Do NOT skip the tool call."
            ),
        )

        assert run._task_ref is not None
        async with asyncio.timeout(_LIVE_TIMEOUT):
            await run._task_ref

        assert run.status == "completed", f"Agent status: {run.status}, error: {run.error}"

        # Read the agent log
        assert run.log_path is not None, "Agent log path not set"
        assert run.log_path.exists(), f"Agent log file not found: {run.log_path}"
        log_content = run.log_path.read_text(encoding="utf-8")

        # Verify agent called send_file
        assert "send_file" in log_content, (
            f"Expected 'send_file' in agent log, got:\n{log_content[:2000]}"
        )

        # Verify bot.send_document was called
        assert bot.send_document.call_count >= 1, (
            "Expected bot.send_document to be called at least once"
        )

    finally:
        await manager.stop_all()
        await mcp_server.stop()
