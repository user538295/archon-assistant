"""Tests for FileHandler."""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Document, File, Message, PhotoSize

from archon.ai.attachment_store import AttachmentStore
from archon.chat.file_handler import FileHandler


def _mock_document_message(
    file_name: str = "report.pdf",
    mime_type: str = "application/pdf",
    file_size: int = 1024,
    file_id: str = "abc123",
    caption: str | None = None,
) -> Message:
    """Create a mock Telegram message with a document."""
    doc = MagicMock(spec=Document)
    doc.file_name = file_name
    doc.mime_type = mime_type
    doc.file_size = file_size
    doc.file_id = file_id

    msg = MagicMock(spec=Message)
    msg.document = doc
    msg.caption = caption
    msg.answer = AsyncMock()
    msg.from_user = MagicMock(id=42)
    msg.chat = MagicMock(id=100)

    # Mock bot
    file_obj = MagicMock(spec=File)
    file_obj.file_path = "documents/report.pdf"
    msg.bot = MagicMock()
    msg.bot.get_file = AsyncMock(return_value=file_obj)
    msg.bot.download_file = AsyncMock(return_value=BytesIO(b"file content"))
    msg.bot.send_chat_action = AsyncMock()

    return msg


class TestHandleDocument:
    @pytest.mark.asyncio
    async def test_document_downloaded_and_saved(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message()

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_document(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )

            # File was downloaded
            msg.bot.get_file.assert_called_once()
            msg.bot.download_file.assert_called_once()

            # handle_message was called with prompt_override
            mock_hm.assert_called_once()
            call_kwargs = mock_hm.call_args.kwargs
            assert call_kwargs.get("prompt_override") is not None
            prompt = call_kwargs["prompt_override"]
            assert "[Attachment:" in prompt

    @pytest.mark.asyncio
    async def test_prompt_contains_pdf_note(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message(mime_type="application/pdf")

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_document(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            prompt = mock_hm.call_args.kwargs["prompt_override"]
            assert "pdftotext" in prompt

    @pytest.mark.asyncio
    async def test_caption_included(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message(caption="Summarize this")

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_document(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            prompt = mock_hm.call_args.kwargs["prompt_override"]
            assert "User message: Summarize this" in prompt

    @pytest.mark.asyncio
    async def test_no_caption_asks_user(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message(caption=None)

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_document(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            prompt = mock_hm.call_args.kwargs["prompt_override"]
            assert "without a message" in prompt

    @pytest.mark.asyncio
    async def test_file_size_rejection(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message(file_size=25 * 1024 * 1024)

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_document(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            # Should reply with error, NOT call handle_message
            msg.answer.assert_called_once()
            assert "too large" in msg.answer.call_args[0][0]
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_download_timeout(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message()
        msg.bot.download_file = AsyncMock(side_effect=asyncio.TimeoutError())

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_document(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.answer.assert_called_once()
            assert "timed out" in msg.answer.call_args[0][0]
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_download_failure(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message()
        msg.bot.get_file = AsyncMock(side_effect=Exception("network error"))

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_document(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.answer.assert_called_once()
            assert "Failed to download" in msg.answer.call_args[0][0]
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_failure(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        store.save = MagicMock(side_effect=OSError("disk full"))
        handler = FileHandler(store)
        msg = _mock_document_message()

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_document(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.answer.assert_called_once()
            assert "Failed to save" in msg.answer.call_args[0][0]
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_di_parameters_forwarded(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message()
        sm = MagicMock()
        trunc = MagicMock()
        notif = MagicMock()
        hm = MagicMock()
        al = MagicMock()
        bam = MagicMock()

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_document(
                message=msg,
                session_manager=sm,
                truncation=trunc,
                max_len=5000,
                notifications=notif,
                cwd="/work",
                history_manager=hm,
                agent_logger=al,
                background_agent_manager=bam,
            )
            kwargs = mock_hm.call_args.kwargs
            assert kwargs["session_manager"] is sm
            assert kwargs["truncation"] is trunc
            assert kwargs["max_len"] == 5000
            assert kwargs["notifications"] is notif
            assert kwargs["cwd"] == "/work"
            assert kwargs["history_manager"] is hm
            assert kwargs["agent_logger"] is al
            assert kwargs["background_agent_manager"] is bam

    @pytest.mark.asyncio
    async def test_no_document_returns_early(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = MagicMock(spec=Message)
        msg.document = None

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_document(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_download_file_network_error(self, tmp_path: Path) -> None:
        """Network error on download_file (not get_file) produces user-friendly message."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message()
        msg.bot.download_file = AsyncMock(side_effect=Exception("Connection reset"))

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_document(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.answer.assert_called_once()
            assert "Failed to download" in msg.answer.call_args[0][0]
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_bot_api_400_error(self, tmp_path: Path) -> None:
        """Bot API returns 400 for oversized files from cloud API."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message(file_size=1024)
        msg.bot.get_file = AsyncMock(
            side_effect=Exception("Bad Request: file is too big"),
        )

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_document(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.answer.assert_called_once()
            assert "Failed to download" in msg.answer.call_args[0][0]
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_permission_denied(self, tmp_path: Path) -> None:
        """PermissionError on save produces user-friendly message."""
        store = AttachmentStore(tmp_path)
        store.save = MagicMock(side_effect=PermissionError("Permission denied"))
        handler = FileHandler(store)
        msg = _mock_document_message()

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_document(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.answer.assert_called_once()
            assert "Failed to save" in msg.answer.call_args[0][0]
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_download_returns_none(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message()
        msg.bot.download_file = AsyncMock(return_value=None)

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_document(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.answer.assert_called_once()
            assert "Failed to download" in msg.answer.call_args[0][0]
            mock_hm.assert_not_called()


def _mock_photo_message(
    file_size: int = 1024,
    file_id: str = "photo123",
    file_unique_id: str = "uniq123",
    width: int = 3024,
    height: int = 4032,
    caption: str | None = None,
) -> Message:
    """Create a mock Telegram message with photos."""
    photo_small = MagicMock(spec=PhotoSize)
    photo_small.file_size = 512
    photo_small.file_id = "small_id"

    photo_large = MagicMock(spec=PhotoSize)
    photo_large.file_size = file_size
    photo_large.file_id = file_id
    photo_large.file_unique_id = file_unique_id
    photo_large.width = width
    photo_large.height = height

    msg = MagicMock(spec=Message)
    msg.photo = [photo_small, photo_large]
    msg.caption = caption
    msg.answer = AsyncMock()
    msg.from_user = MagicMock(id=42)
    msg.chat = MagicMock(id=100)

    file_obj = MagicMock(spec=File)
    file_obj.file_path = "photos/file_123.jpg"
    msg.bot = MagicMock()
    msg.bot.get_file = AsyncMock(return_value=file_obj)
    msg.bot.download_file = AsyncMock(return_value=BytesIO(b"image data"))
    msg.bot.send_chat_action = AsyncMock()

    return msg


class TestHandlePhoto:
    @pytest.mark.asyncio
    async def test_photo_downloaded_and_saved(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_photo_message()

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_photo(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.bot.get_file.assert_called_once_with("photo123")  # largest photo
            mock_hm.assert_called_once()

    @pytest.mark.asyncio
    async def test_photo_prompt_has_image_note(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_photo_message()

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_photo(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            prompt = mock_hm.call_args.kwargs.get("prompt_override", "")
            assert "Visual analysis is not available" in prompt

    @pytest.mark.asyncio
    async def test_largest_photo_selected(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_photo_message()

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock):
            await handler.handle_photo(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            # Should use the last (largest) photo's file_id
            msg.bot.get_file.assert_called_with("photo123")

    @pytest.mark.asyncio
    async def test_photo_size_rejection(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_photo_message(file_size=25 * 1024 * 1024)

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_photo(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.answer.assert_called_once()
            assert "too large" in msg.answer.call_args[0][0]
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_photo_returns_early(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = MagicMock(spec=Message)
        msg.photo = []

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_photo(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_photo_none_returns_early(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = MagicMock(spec=Message)
        msg.photo = None

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_photo(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_photo_download_timeout(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_photo_message()
        msg.bot.get_file = AsyncMock(side_effect=asyncio.TimeoutError())

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_photo(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.answer.assert_called_once()
            assert "timed out" in msg.answer.call_args[0][0]
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_photo_download_failure(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_photo_message()
        msg.bot.get_file = AsyncMock(side_effect=Exception("network error"))

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_photo(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.answer.assert_called_once()
            assert "Failed to download" in msg.answer.call_args[0][0]
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_photo_download_returns_none(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_photo_message()
        msg.bot.download_file = AsyncMock(return_value=None)

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_photo(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.answer.assert_called_once()
            assert "Failed to download" in msg.answer.call_args[0][0]
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_photo_save_failure(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        store.save = MagicMock(side_effect=OSError("disk full"))
        handler = FileHandler(store)
        msg = _mock_photo_message()

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_photo(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.answer.assert_called_once()
            assert "Failed to save" in msg.answer.call_args[0][0]
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_photo_caption_forwarded(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_photo_message(caption="What is this?")

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_photo(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            prompt = mock_hm.call_args.kwargs.get("prompt_override", "")
            assert "User message: What is this?" in prompt


class TestImageAsDocument:
    @pytest.mark.asyncio
    async def test_document_with_image_mime_handled_as_image(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message(file_name="photo.jpg", mime_type="image/jpeg")

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_document(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            prompt = mock_hm.call_args.kwargs.get("prompt_override", "")
            assert "Visual analysis is not available" in prompt

    @pytest.mark.asyncio
    async def test_document_with_png_handled_as_image(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message(file_name="screenshot.png", mime_type="image/png")

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_document(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            prompt = mock_hm.call_args.kwargs.get("prompt_override", "")
            assert "Visual analysis is not available" in prompt

    @pytest.mark.asyncio
    async def test_document_with_pdf_not_handled_as_image(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message(file_name="doc.pdf", mime_type="application/pdf")

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_document(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            prompt = mock_hm.call_args.kwargs.get("prompt_override", "")
            assert "pdftotext" in prompt
            assert "Visual analysis" not in prompt

    @pytest.mark.asyncio
    async def test_svg_not_handled_as_image(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message(file_name="icon.svg", mime_type="image/svg+xml")

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_document(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            prompt = mock_hm.call_args.kwargs.get("prompt_override", "")
            # SVG should NOT be treated as image
            assert "Visual analysis" not in prompt
