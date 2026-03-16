"""Integration tests for the document attachment flow.

These tests exercise the full path: Telegram document -> FileHandler.handle_document
-> download -> AttachmentStore.save -> build_attachment_prompt -> handle_message
-> session.send() -> formatted response back to user.

Unlike the unit tests in test_file_handler.py (which mock handle_message),
these let the real handle_message run end-to-end.
"""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Document, File, Message

from archon.ai.attachment_store import AttachmentStore
from archon.ai.event_mapper import Response
from archon.ai.session_manager import SessionManager
from archon.ai.truncation import SplitStrategy
from archon.chat.file_handler import FileHandler


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _mock_document_message(
    file_name: str = "report.pdf",
    mime_type: str = "application/pdf",
    file_size: int = 1024,
    file_id: str = "abc123",
    caption: str | None = "Summarize this",
    data: bytes = b"PDF content here",
) -> Message:
    """Create a mock Telegram message with a document attachment."""
    doc = MagicMock(spec=Document)
    doc.file_name = file_name
    doc.mime_type = mime_type
    doc.file_size = file_size
    doc.file_id = file_id

    msg = MagicMock(spec=Message)
    msg.document = doc
    msg.caption = caption
    msg.text = None
    msg.answer = AsyncMock()
    msg.from_user = MagicMock(id=42)
    msg.chat = MagicMock(id=100)

    file_obj = MagicMock(spec=File)
    file_obj.file_path = f"documents/{file_name}"
    msg.bot = MagicMock()
    msg.bot.get_file = AsyncMock(return_value=file_obj)
    msg.bot.download_file = AsyncMock(return_value=BytesIO(data))
    msg.bot.send_chat_action = AsyncMock()

    return msg


def _mock_session_manager(*events: object) -> SessionManager:
    """Session manager whose session.send() yields the given events."""
    session = MagicMock()
    session.is_processing = False

    async def _send(prompt: str) -> AsyncGenerator:
        for event in events:
            yield event

    session.send = _send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])
    return mgr


# ──────────────────────────────────────────────────────────────────
# Integration: full document flow
# ──────────────────────────────────────────────────────────────────


class TestDocumentFlowIntegration:
    """End-to-end: Telegram document -> download -> save -> prompt -> session -> response."""

    @pytest.mark.asyncio
    async def test_pdf_document_full_flow(self, tmp_path: Path) -> None:
        """PDF document: download -> save -> prompt with CLI note -> response streamed back."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message(
            file_name="report.pdf",
            mime_type="application/pdf",
            caption="Summarize this report",
        )
        session_mgr = _mock_session_manager(Response(content="Here is the summary..."))

        await handler.handle_document(
            message=msg,
            session_manager=session_mgr,
            truncation=SplitStrategy(),
        )

        # File was saved to disk
        saved_files = list(tmp_path.rglob("report.pdf"))
        assert len(saved_files) == 1

        # Session was created for the user
        session_mgr.get_or_create.assert_called_once_with(42)

        # Response was delivered back to the user via message.answer
        answers = [
            call.args[0]
            for call in msg.answer.call_args_list
            if call.args and isinstance(call.args[0], str)
        ]
        assert any("summary" in a.lower() for a in answers)

    @pytest.mark.asyncio
    async def test_python_file_full_flow(self, tmp_path: Path) -> None:
        """Python file: download -> save -> prompt -> response."""
        code = b"def hello():\n    print('hi')\n"
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message(
            file_name="utils.py",
            mime_type="text/x-python",
            caption="Review this code",
            data=code,
        )
        session_mgr = _mock_session_manager(Response(content="Code looks good"))

        await handler.handle_document(
            message=msg,
            session_manager=session_mgr,
            truncation=SplitStrategy(),
        )

        saved_files = list(tmp_path.rglob("utils.py"))
        assert len(saved_files) == 1
        assert saved_files[0].read_bytes() == code

        answers = [
            call.args[0]
            for call in msg.answer.call_args_list
            if call.args and isinstance(call.args[0], str)
        ]
        assert any("Code looks good" in a for a in answers)

    @pytest.mark.asyncio
    async def test_csv_file_full_flow(self, tmp_path: Path) -> None:
        """CSV file: download -> save -> prompt -> response."""
        csv_data = b"name,age\nAlice,30\n"
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message(
            file_name="data.csv",
            mime_type="text/csv",
            caption="Analyze this data",
            data=csv_data,
        )
        session_mgr = _mock_session_manager(Response(content="Data analysis complete"))

        await handler.handle_document(
            message=msg,
            session_manager=session_mgr,
            truncation=SplitStrategy(),
        )

        saved_files = list(tmp_path.rglob("data.csv"))
        assert len(saved_files) == 1
        assert saved_files[0].read_bytes() == csv_data

    @pytest.mark.asyncio
    async def test_oversized_file_rejected_before_download(self, tmp_path: Path) -> None:
        """File exceeding 20 MB is rejected before download."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message(file_size=25 * 1024 * 1024)
        session_mgr = _mock_session_manager()

        await handler.handle_document(
            message=msg,
            session_manager=session_mgr,
            truncation=SplitStrategy(),
        )

        msg.answer.assert_called_once()
        assert "too large" in msg.answer.call_args[0][0]
        # Download should NOT have been attempted
        msg.bot.get_file.assert_not_called()
        # Session should NOT have been created
        session_mgr.get_or_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_download_timeout_handled(self, tmp_path: Path) -> None:
        """Download timeout produces user-friendly error."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message()
        msg.bot.download_file = AsyncMock(side_effect=asyncio.TimeoutError())

        await handler.handle_document(
            message=msg,
            session_manager=MagicMock(),
            truncation=SplitStrategy(),
        )

        msg.answer.assert_called_once()
        assert "timed out" in msg.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_no_caption_prompts_to_ask_user(self, tmp_path: Path) -> None:
        """When no caption is provided, the prompt asks Claude to ask the user."""
        prompts_seen: list[str] = []

        async def _capturing_send(prompt: str) -> AsyncGenerator:
            prompts_seen.append(prompt)
            yield Response(content="What would you like me to do?")

        session = MagicMock()
        session.is_processing = False
        session.send = _capturing_send

        mgr = MagicMock(spec=SessionManager)
        mgr.get_or_create = AsyncMock(return_value=session)
        mgr.pop_last_injected_files = MagicMock(return_value=[])

        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message(caption=None)

        await handler.handle_document(
            message=msg,
            session_manager=mgr,
            truncation=SplitStrategy(),
        )

        assert len(prompts_seen) == 1
        assert "without a message" in prompts_seen[0]

    @pytest.mark.asyncio
    async def test_prompt_contains_attachment_path(self, tmp_path: Path) -> None:
        """The prompt passed to the session includes the saved file path."""
        prompts_seen: list[str] = []

        async def _capturing_send(prompt: str) -> AsyncGenerator:
            prompts_seen.append(prompt)
            yield Response(content="Done")

        session = MagicMock()
        session.is_processing = False
        session.send = _capturing_send

        mgr = MagicMock(spec=SessionManager)
        mgr.get_or_create = AsyncMock(return_value=session)
        mgr.pop_last_injected_files = MagicMock(return_value=[])

        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message(file_name="notes.txt", mime_type="text/plain")

        await handler.handle_document(
            message=msg,
            session_manager=mgr,
            truncation=SplitStrategy(),
        )

        assert len(prompts_seen) == 1
        assert "[Attachment:" in prompts_seen[0]
        assert "notes.txt" in prompts_seen[0]


# ──────────────────────────────────────────────────────────────────
# Concurrency: file + text messages serialized via session
# ──────────────────────────────────────────────────────────────────


class TestConcurrency:
    """Verify file and text messages both go through handle_message and both complete."""

    @pytest.mark.asyncio
    async def test_concurrent_file_and_text(self, tmp_path: Path) -> None:
        """File attachment and text message run concurrently via handle_message.

        Both should complete successfully. The session's send() is called twice
        (once for each message).
        """
        call_order: list[str] = []

        async def _tracking_send(prompt: str) -> AsyncGenerator:
            label = "file" if "[Attachment:" in prompt else "text"
            call_order.append(f"start:{label}")
            await asyncio.sleep(0.01)
            call_order.append(f"end:{label}")
            yield Response(content="done")

        session = MagicMock()
        session.is_processing = False
        session.send = _tracking_send

        session_mgr = MagicMock(spec=SessionManager)
        session_mgr.get_or_create = AsyncMock(return_value=session)
        session_mgr.pop_last_injected_files = MagicMock(return_value=[])

        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg_file = _mock_document_message(caption="review")

        msg_text = MagicMock(spec=Message)
        msg_text.text = "Hello"
        msg_text.from_user = MagicMock(id=42)
        msg_text.chat = MagicMock(id=100)
        msg_text.answer = AsyncMock()
        msg_text.bot = MagicMock()
        msg_text.bot.send_chat_action = AsyncMock()

        from archon.chat.handler import handle_message

        # Run both concurrently — they should both complete
        await asyncio.gather(
            handler.handle_document(
                message=msg_file,
                session_manager=session_mgr,
                truncation=SplitStrategy(),
            ),
            handle_message(
                message=msg_text,
                session_manager=session_mgr,
                truncation=SplitStrategy(),
            ),
        )

        # Both should have been processed
        assert len(call_order) == 4
        assert call_order.count("start:file") == 1
        assert call_order.count("start:text") == 1
        assert call_order.count("end:file") == 1
        assert call_order.count("end:text") == 1

    @pytest.mark.asyncio
    async def test_multiple_files_all_saved(self, tmp_path: Path) -> None:
        """Multiple file attachments sent concurrently all get saved."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        session_mgr = _mock_session_manager(Response(content="done"))

        filenames = ["a.txt", "b.txt", "c.txt"]
        tasks = []
        for name in filenames:
            msg = _mock_document_message(
                file_name=name,
                mime_type="text/plain",
                data=f"content of {name}".encode(),
                caption=f"Process {name}",
            )
            tasks.append(
                handler.handle_document(
                    message=msg,
                    session_manager=session_mgr,
                    truncation=SplitStrategy(),
                )
            )

        await asyncio.gather(*tasks)

        # All files should be saved
        saved = {f.name for f in tmp_path.rglob("*.txt")}
        assert saved == set(filenames)
