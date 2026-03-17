"""Tests for FileHandler."""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Audio, Document, File, Message, PhotoSize, Sticker, Video, VideoNote

from archon.ai.attachment_store import AttachmentStore
from archon.chat.file_handler import FileHandler
from archon.chat.media_group_collector import MediaGroupCollector


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
    msg.media_group_id = None
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
            assert "Failed to download" in msg.answer.call_args[0][0]
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
    msg.media_group_id = None
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
            assert "Failed to download" in msg.answer.call_args[0][0]
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


    @pytest.mark.asyncio
    async def test_photo_file_path_none_sends_error(self, tmp_path: Path) -> None:
        """When Telegram returns file_path=None, user gets an error message."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_photo_message()
        file_obj = MagicMock(spec=File)
        file_obj.file_path = None
        msg.bot.get_file = AsyncMock(return_value=file_obj)

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_photo(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.answer.assert_called_once()
            assert "failed to download" in msg.answer.call_args[0][0].lower()
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_document_file_path_none_sends_error(self, tmp_path: Path) -> None:
        """When Telegram returns file_path=None for document, user gets an error message."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message()
        file_obj = MagicMock(spec=File)
        file_obj.file_path = None
        msg.bot.get_file = AsyncMock(return_value=file_obj)

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_document(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.answer.assert_called_once()
            assert "failed to download" in msg.answer.call_args[0][0].lower()
            mock_hm.assert_not_called()


class TestDownloadFileMethod:
    @pytest.mark.asyncio
    async def test_download_file_returns_none_on_null_file_path(self, tmp_path: Path) -> None:
        """_download_file returns None when file.file_path is None."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = MagicMock(spec=Message)
        msg.bot = MagicMock()
        file_obj = MagicMock(spec=File)
        file_obj.file_path = None
        msg.bot.get_file = AsyncMock(return_value=file_obj)

        result = await handler._download_file(msg, "file123")
        assert result is None


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


# ──────────────────────────────────────────────────────────────────
# Media group helpers
# ──────────────────────────────────────────────────────────────────


def _mock_group_photo_message(
    group_id: str = "album1",
    file_id: str = "photo_gid",
    file_unique_id: str = "uniq_gid",
    file_size: int = 1024,
    caption: str | None = None,
) -> Message:
    """Create a mock Telegram photo message that belongs to a media group."""
    photo_large = MagicMock(spec=PhotoSize)
    photo_large.file_size = file_size
    photo_large.file_id = file_id
    photo_large.file_unique_id = file_unique_id
    photo_large.width = 1920
    photo_large.height = 1080

    msg = MagicMock(spec=Message)
    msg.photo = [photo_large]
    msg.document = None
    msg.caption = caption
    msg.media_group_id = group_id
    msg.answer = AsyncMock()
    msg.from_user = MagicMock(id=42)
    msg.chat = MagicMock(id=100)

    file_obj = MagicMock(spec=File)
    file_obj.file_path = f"photos/{file_id}.jpg"
    msg.bot = MagicMock()
    msg.bot.get_file = AsyncMock(return_value=file_obj)
    msg.bot.download_file = AsyncMock(return_value=BytesIO(b"image data"))
    msg.bot.send_chat_action = AsyncMock()

    return msg


def _mock_group_document_message(
    group_id: str = "album1",
    file_name: str = "report.pdf",
    mime_type: str = "application/pdf",
    file_id: str = "doc_gid",
    file_size: int = 1024,
    caption: str | None = None,
) -> Message:
    """Create a mock Telegram document message that belongs to a media group."""
    doc = MagicMock(spec=Document)
    doc.file_name = file_name
    doc.mime_type = mime_type
    doc.file_size = file_size
    doc.file_id = file_id

    msg = MagicMock(spec=Message)
    msg.photo = None
    msg.document = doc
    msg.caption = caption
    msg.media_group_id = group_id
    msg.answer = AsyncMock()
    msg.from_user = MagicMock(id=42)
    msg.chat = MagicMock(id=100)

    file_obj = MagicMock(spec=File)
    file_obj.file_path = f"documents/{file_name}"
    msg.bot = MagicMock()
    msg.bot.get_file = AsyncMock(return_value=file_obj)
    msg.bot.download_file = AsyncMock(return_value=BytesIO(b"file content"))
    msg.bot.send_chat_action = AsyncMock()

    return msg


# ──────────────────────────────────────────────────────────────────
# Media group tests
# ──────────────────────────────────────────────────────────────────


class TestMediaGroupIntegration:
    """Test media group handling in FileHandler."""

    @pytest.mark.asyncio
    async def test_photo_album_combined_prompt(self, tmp_path: Path) -> None:
        """3 photos with same media_group_id -> one combined prompt."""
        collector = MediaGroupCollector(timeout=0.1)
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store, media_group_collector=collector)

        msg1 = _mock_group_photo_message("album1", file_id="p1", file_unique_id="u1")
        msg2 = _mock_group_photo_message("album1", file_id="p2", file_unique_id="u2")
        msg3 = _mock_group_photo_message(
            "album1", file_id="p3", file_unique_id="u3", caption="Compare these"
        )

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:

            async def add_later() -> None:
                await asyncio.sleep(0.02)
                await handler.handle_photo(
                    message=msg2, session_manager=MagicMock(), truncation=MagicMock()
                )
                await asyncio.sleep(0.02)
                await handler.handle_photo(
                    message=msg3, session_manager=MagicMock(), truncation=MagicMock()
                )

            task = asyncio.create_task(add_later())
            await handler.handle_photo(
                message=msg1, session_manager=MagicMock(), truncation=MagicMock()
            )
            await task

            # handle_message called exactly once with combined prompt
            mock_hm.assert_called_once()
            prompt = mock_hm.call_args.kwargs["prompt_override"]
            # Should contain 3 attachment blocks
            assert prompt.count("[Attachment:") == 3
            # Caption from the first captioned message
            assert "User message: Compare these" in prompt

    @pytest.mark.asyncio
    async def test_mixed_album_photo_and_document(self, tmp_path: Path) -> None:
        """Photo + document in same group -> correct pipelines, one combined prompt."""
        collector = MediaGroupCollector(timeout=0.1)
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store, media_group_collector=collector)

        msg_photo = _mock_group_photo_message(
            "mix1", file_id="p1", file_unique_id="u1", caption="Review both"
        )
        msg_doc = _mock_group_document_message(
            "mix1", file_name="data.csv", mime_type="text/csv", file_id="d1"
        )

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:

            async def add_doc() -> None:
                await asyncio.sleep(0.02)
                await handler.handle_document(
                    message=msg_doc, session_manager=MagicMock(), truncation=MagicMock()
                )

            task = asyncio.create_task(add_doc())
            await handler.handle_photo(
                message=msg_photo, session_manager=MagicMock(), truncation=MagicMock()
            )
            await task

            mock_hm.assert_called_once()
            prompt = mock_hm.call_args.kwargs["prompt_override"]
            assert prompt.count("[Attachment:") == 2
            assert "User message: Review both" in prompt

    @pytest.mark.asyncio
    async def test_non_group_photo_unaffected(self, tmp_path: Path) -> None:
        """Photo without media_group_id is handled normally (no grouping)."""
        collector = MediaGroupCollector(timeout=0.1)
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store, media_group_collector=collector)
        msg = _mock_photo_message()
        msg.media_group_id = None

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_photo(
                message=msg, session_manager=MagicMock(), truncation=MagicMock()
            )
            mock_hm.assert_called_once()
            prompt = mock_hm.call_args.kwargs["prompt_override"]
            assert prompt.count("[Attachment:") == 1

    @pytest.mark.asyncio
    async def test_non_group_document_unaffected(self, tmp_path: Path) -> None:
        """Document without media_group_id is handled normally (no grouping)."""
        collector = MediaGroupCollector(timeout=0.1)
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store, media_group_collector=collector)
        msg = _mock_document_message()
        msg.media_group_id = None

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_document(
                message=msg, session_manager=MagicMock(), truncation=MagicMock()
            )
            mock_hm.assert_called_once()

    @pytest.mark.asyncio
    async def test_media_group_caption_from_first_captioned(self, tmp_path: Path) -> None:
        """Caption is taken from the first message that has a caption."""
        collector = MediaGroupCollector(timeout=0.1)
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store, media_group_collector=collector)

        msg1 = _mock_group_photo_message("g1", file_id="p1", file_unique_id="u1", caption=None)
        msg2 = _mock_group_photo_message(
            "g1", file_id="p2", file_unique_id="u2", caption="First caption"
        )

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:

            async def add_second() -> None:
                await asyncio.sleep(0.02)
                await handler.handle_photo(
                    message=msg2, session_manager=MagicMock(), truncation=MagicMock()
                )

            task = asyncio.create_task(add_second())
            await handler.handle_photo(
                message=msg1, session_manager=MagicMock(), truncation=MagicMock()
            )
            await task

            mock_hm.assert_called_once()
            prompt = mock_hm.call_args.kwargs["prompt_override"]
            assert "User message: First caption" in prompt

    @pytest.mark.asyncio
    async def test_media_group_no_caption(self, tmp_path: Path) -> None:
        """Media group without any captions gets the 'no message' prompt."""
        collector = MediaGroupCollector(timeout=0.1)
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store, media_group_collector=collector)

        msg1 = _mock_group_photo_message("g1", file_id="p1", file_unique_id="u1")
        msg2 = _mock_group_photo_message("g1", file_id="p2", file_unique_id="u2")

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:

            async def add_second() -> None:
                await asyncio.sleep(0.02)
                await handler.handle_photo(
                    message=msg2, session_manager=MagicMock(), truncation=MagicMock()
                )

            task = asyncio.create_task(add_second())
            await handler.handle_photo(
                message=msg1, session_manager=MagicMock(), truncation=MagicMock()
            )
            await task

            mock_hm.assert_called_once()
            prompt = mock_hm.call_args.kwargs["prompt_override"]
            assert "without a message" in prompt

    @pytest.mark.asyncio
    async def test_media_group_cancelled_returns_early(self, tmp_path: Path) -> None:
        """When collector.close() cancels a group, handle_photo returns early (no crash)."""
        collector = MediaGroupCollector(timeout=10.0)
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store, media_group_collector=collector)

        msg = _mock_group_photo_message("g1", file_id="p1", file_unique_id="u1")

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            task = asyncio.create_task(
                handler.handle_photo(
                    message=msg, session_manager=MagicMock(), truncation=MagicMock()
                )
            )
            await asyncio.sleep(0.01)
            collector.close()
            await task

            # handle_message should NOT have been called — group was cancelled
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_media_group_cancelled_document_returns_early(self, tmp_path: Path) -> None:
        """When collector.close() cancels a group, handle_document returns early."""
        collector = MediaGroupCollector(timeout=10.0)
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store, media_group_collector=collector)

        msg = _mock_document_message()
        msg.media_group_id = "g1"

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            task = asyncio.create_task(
                handler.handle_document(
                    message=msg, session_manager=MagicMock(), truncation=MagicMock()
                )
            )
            await asyncio.sleep(0.01)
            collector.close()
            await task

            mock_hm.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# Video mock helpers
# ──────────────────────────────────────────────────────────────────


def _mock_video_message(
    file_size: int = 2048,
    file_id: str = "vid123",
    file_unique_id: str = "vid_uniq",
    file_name: str | None = "clip.mp4",
    mime_type: str = "video/mp4",
    caption: str | None = None,
    is_video_note: bool = False,
) -> Message:
    """Create a mock Telegram message with a video (or video_note)."""
    msg = MagicMock(spec=Message)
    msg.caption = caption
    msg.media_group_id = None
    msg.answer = AsyncMock()
    msg.from_user = MagicMock(id=42)
    msg.chat = MagicMock(id=100)

    file_obj = MagicMock(spec=File)
    file_obj.file_path = "videos/clip.mp4"
    msg.bot = MagicMock()
    msg.bot.get_file = AsyncMock(return_value=file_obj)
    msg.bot.download_file = AsyncMock(return_value=BytesIO(b"video data"))
    msg.bot.send_chat_action = AsyncMock()

    if is_video_note:
        msg.video = None
        vn = MagicMock(spec=VideoNote)
        vn.file_size = file_size
        vn.file_id = file_id
        vn.file_unique_id = file_unique_id
        vn.file_name = None  # VideoNote has no file_name
        vn.mime_type = None
        msg.video_note = vn
    else:
        vid = MagicMock(spec=Video)
        vid.file_size = file_size
        vid.file_id = file_id
        vid.file_unique_id = file_unique_id
        vid.file_name = file_name
        vid.mime_type = mime_type
        msg.video = vid
        msg.video_note = None

    return msg


class TestHandleVideo:
    @pytest.mark.asyncio
    async def test_video_downloaded_and_saved(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_video_message()

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_video(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.bot.get_file.assert_called_once()
            msg.bot.download_file.assert_called_once()
            mock_hm.assert_called_once()
            prompt = mock_hm.call_args.kwargs["prompt_override"]
            assert "[Attachment:" in prompt

    @pytest.mark.asyncio
    async def test_video_prompt_contains_video_note(self, tmp_path: Path) -> None:
        """Video prompt should include the video-specific note."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_video_message()

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_video(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            prompt = mock_hm.call_args.kwargs["prompt_override"]
            assert "video" in prompt.lower()

    @pytest.mark.asyncio
    async def test_video_note_handled(self, tmp_path: Path) -> None:
        """video_note (round video) is handled correctly."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_video_message(is_video_note=True)

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_video(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            mock_hm.assert_called_once()

    @pytest.mark.asyncio
    async def test_video_caption_included(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_video_message(caption="Check this out")

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_video(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            prompt = mock_hm.call_args.kwargs["prompt_override"]
            assert "User message: Check this out" in prompt

    @pytest.mark.asyncio
    async def test_video_size_rejection(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_video_message(file_size=25 * 1024 * 1024)

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_video(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.answer.assert_called_once()
            assert "too large" in msg.answer.call_args[0][0]
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_video_no_video_returns_early(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = MagicMock(spec=Message)
        msg.video = None
        msg.video_note = None

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_video(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_video_download_failure(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_video_message()
        msg.bot.get_file = AsyncMock(side_effect=Exception("network error"))

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_video(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.answer.assert_called_once()
            assert "Failed to download" in msg.answer.call_args[0][0]
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_video_download_timeout(self, tmp_path: Path) -> None:
        """TimeoutError on get_file produces user-friendly error message."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_video_message()
        msg.bot.get_file = AsyncMock(side_effect=asyncio.TimeoutError())

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_video(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.answer.assert_called_once()
            assert "Failed to download" in msg.answer.call_args[0][0]
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_video_save_failure(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        store.save = MagicMock(side_effect=OSError("disk full"))
        handler = FileHandler(store)
        msg = _mock_video_message()

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_video(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.answer.assert_called_once()
            assert "Failed to save" in msg.answer.call_args[0][0]
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_video_di_parameters_forwarded(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_video_message()
        sm = MagicMock()
        trunc = MagicMock()
        notif = MagicMock()
        hm = MagicMock()
        al = MagicMock()
        bam = MagicMock()

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_video(
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


# ──────────────────────────────────────────────────────────────────
# Sticker mock helpers
# ──────────────────────────────────────────────────────────────────


def _mock_sticker_message(
    file_id: str = "stk123",
    file_unique_id: str = "stk_uniq",
    is_animated: bool = False,
    is_video: bool = False,
    file_size: int = 512,
    caption: str | None = None,
) -> Message:
    """Create a mock Telegram message with a sticker."""
    sticker = MagicMock(spec=Sticker)
    sticker.file_id = file_id
    sticker.file_unique_id = file_unique_id
    sticker.is_animated = is_animated
    sticker.is_video = is_video
    sticker.file_size = file_size

    msg = MagicMock(spec=Message)
    msg.sticker = sticker
    msg.caption = caption
    msg.answer = AsyncMock()
    msg.from_user = MagicMock(id=42)
    msg.chat = MagicMock(id=100)

    ext = ".tgs" if is_animated else ".webm" if is_video else ".webp"
    file_obj = MagicMock(spec=File)
    file_obj.file_path = f"stickers/sticker{ext}"
    msg.bot = MagicMock()
    msg.bot.get_file = AsyncMock(return_value=file_obj)
    msg.bot.download_file = AsyncMock(return_value=BytesIO(b"sticker data"))
    msg.bot.send_chat_action = AsyncMock()

    return msg


class TestHandleSticker:
    @pytest.mark.asyncio
    async def test_static_sticker_saved_as_webp(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_sticker_message()

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_sticker(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            mock_hm.assert_called_once()
            prompt = mock_hm.call_args.kwargs["prompt_override"]
            assert "[Attachment:" in prompt
            assert ".webp" in prompt

    @pytest.mark.asyncio
    async def test_animated_sticker_tgs(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_sticker_message(is_animated=True)

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_sticker(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            mock_hm.assert_called_once()
            prompt = mock_hm.call_args.kwargs["prompt_override"]
            assert ".tgs" in prompt

    @pytest.mark.asyncio
    async def test_video_sticker_webm(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_sticker_message(is_video=True)

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_sticker(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            mock_hm.assert_called_once()
            prompt = mock_hm.call_args.kwargs["prompt_override"]
            assert ".webm" in prompt

    @pytest.mark.asyncio
    async def test_sticker_size_rejection(self, tmp_path: Path) -> None:
        """Oversized stickers are rejected with error message."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_sticker_message(file_size=25 * 1024 * 1024)

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_sticker(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.answer.assert_called_once()
            assert "too large" in msg.answer.call_args[0][0]
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_sticker_no_sticker_returns_early(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = MagicMock(spec=Message)
        msg.sticker = None

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_sticker(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_sticker_download_failure(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_sticker_message()
        msg.bot.get_file = AsyncMock(side_effect=Exception("network error"))

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_sticker(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.answer.assert_called_once()
            assert "Failed to download" in msg.answer.call_args[0][0]
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_sticker_save_failure(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        store.save = MagicMock(side_effect=OSError("disk full"))
        handler = FileHandler(store)
        msg = _mock_sticker_message()

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_sticker(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.answer.assert_called_once()
            assert "Failed to save" in msg.answer.call_args[0][0]
            mock_hm.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# Audio-as-attachment mock helpers
# ──────────────────────────────────────────────────────────────────


def _mock_audio_message(
    file_size: int = 1024,
    file_id: str = "aud123",
    file_unique_id: str = "aud_uniq",
    file_name: str | None = "track.mp3",
    mime_type: str = "audio/mpeg",
    caption: str | None = None,
) -> Message:
    """Create a mock Telegram message with an audio file."""
    audio = MagicMock(spec=Audio)
    audio.file_size = file_size
    audio.file_id = file_id
    audio.file_unique_id = file_unique_id
    audio.file_name = file_name
    audio.mime_type = mime_type

    msg = MagicMock(spec=Message)
    msg.audio = audio
    msg.caption = caption
    msg.answer = AsyncMock()
    msg.from_user = MagicMock(id=42)
    msg.chat = MagicMock(id=100)

    file_obj = MagicMock(spec=File)
    file_obj.file_path = "audio/track.mp3"
    msg.bot = MagicMock()
    msg.bot.get_file = AsyncMock(return_value=file_obj)
    msg.bot.download_file = AsyncMock(return_value=BytesIO(b"audio data"))
    msg.bot.send_chat_action = AsyncMock()

    return msg


class TestHandleAudioAttachment:
    @pytest.mark.asyncio
    async def test_audio_downloaded_and_saved(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_audio_message()

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_audio_attachment(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.bot.get_file.assert_called_once()
            msg.bot.download_file.assert_called_once()
            mock_hm.assert_called_once()
            prompt = mock_hm.call_args.kwargs["prompt_override"]
            assert "[Attachment:" in prompt

    @pytest.mark.asyncio
    async def test_audio_caption_included(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_audio_message(caption="Transcribe this")

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_audio_attachment(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            prompt = mock_hm.call_args.kwargs["prompt_override"]
            assert "User message: Transcribe this" in prompt

    @pytest.mark.asyncio
    async def test_audio_size_rejection(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_audio_message(file_size=25 * 1024 * 1024)

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_audio_attachment(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.answer.assert_called_once()
            assert "too large" in msg.answer.call_args[0][0]
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_audio_no_audio_returns_early(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = MagicMock(spec=Message)
        msg.audio = None

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_audio_attachment(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_audio_no_filename_uses_fallback(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_audio_message(file_name=None)

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_audio_attachment(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            mock_hm.assert_called_once()
            prompt = mock_hm.call_args.kwargs["prompt_override"]
            assert "audio_" in prompt

    @pytest.mark.asyncio
    async def test_audio_download_failure(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_audio_message()
        msg.bot.get_file = AsyncMock(side_effect=Exception("network error"))

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_audio_attachment(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.answer.assert_called_once()
            assert "Failed to download" in msg.answer.call_args[0][0]
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_audio_save_failure(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        store.save = MagicMock(side_effect=OSError("disk full"))
        handler = FileHandler(store)
        msg = _mock_audio_message()

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_audio_attachment(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.answer.assert_called_once()
            assert "Failed to save" in msg.answer.call_args[0][0]
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_audio_di_parameters_forwarded(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_audio_message()
        sm = MagicMock()
        trunc = MagicMock()
        notif = MagicMock()
        hm = MagicMock()
        al = MagicMock()
        bam = MagicMock()

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_audio_attachment(
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


# ──────────────────────────────────────────────────────────────────
# Archive document tests (Task 31)
# ──────────────────────────────────────────────────────────────────


class TestArchiveDocument:
    @pytest.mark.asyncio
    async def test_zip_prompt_asks_intent(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message(file_name="data.zip", mime_type="application/zip")

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_document(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            prompt = mock_hm.call_args.kwargs["prompt_override"]
            assert "archive" in prompt.lower()

    @pytest.mark.asyncio
    async def test_tar_gz_prompt_asks_intent(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message(file_name="backup.tar.gz", mime_type="application/gzip")

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_document(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            prompt = mock_hm.call_args.kwargs["prompt_override"]
            assert "archive" in prompt.lower()

    @pytest.mark.asyncio
    async def test_rar_prompt_asks_intent(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message(
            file_name="files.rar", mime_type="application/x-rar-compressed"
        )

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_document(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            prompt = mock_hm.call_args.kwargs["prompt_override"]
            assert "archive" in prompt.lower()

    @pytest.mark.asyncio
    async def test_unknown_binary_saved_with_generic_metadata(self, tmp_path: Path) -> None:
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message(
            file_name="data.bin", mime_type="application/octet-stream"
        )

        with patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm:
            await handler.handle_document(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            mock_hm.assert_called_once()
            prompt = mock_hm.call_args.kwargs["prompt_override"]
            assert "[Attachment:" in prompt


# ──────────────────────────────────────────────────────────────────
# _download_file reuse — handle_photo and handle_document must use it
# ──────────────────────────────────────────────────────────────────


class TestDownloadFileReuse:
    @pytest.mark.asyncio
    async def test_handle_photo_uses_download_file(self, tmp_path: Path) -> None:
        """handle_photo must delegate to _download_file instead of inline download."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_photo_message()

        with (
            patch.object(
                handler, "_download_file", new_callable=AsyncMock,
                return_value=(b"image data", "photos/file_123.jpg"),
            ) as mock_dl,
            patch("archon.chat.handler.handle_message", new_callable=AsyncMock),
        ):
            await handler.handle_photo(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            mock_dl.assert_called_once_with(msg, "photo123")

    @pytest.mark.asyncio
    async def test_handle_document_uses_download_file(self, tmp_path: Path) -> None:
        """handle_document must delegate to _download_file instead of inline download."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message()

        with (
            patch.object(
                handler, "_download_file", new_callable=AsyncMock,
                return_value=(b"file content", "documents/report.pdf"),
            ) as mock_dl,
            patch("archon.chat.handler.handle_message", new_callable=AsyncMock),
        ):
            await handler.handle_document(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            mock_dl.assert_called_once_with(msg, "abc123")

    @pytest.mark.asyncio
    async def test_handle_photo_download_none_sends_error(self, tmp_path: Path) -> None:
        """When _download_file returns None, handle_photo sends error to user."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_photo_message()

        with (
            patch.object(handler, "_download_file", new_callable=AsyncMock, return_value=None),
            patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm,
        ):
            await handler.handle_photo(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.answer.assert_called_once()
            assert "Failed to download" in msg.answer.call_args[0][0]
            mock_hm.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_document_download_none_sends_error(self, tmp_path: Path) -> None:
        """When _download_file returns None, handle_document sends error to user."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message()

        with (
            patch.object(handler, "_download_file", new_callable=AsyncMock, return_value=None),
            patch("archon.chat.handler.handle_message", new_callable=AsyncMock) as mock_hm,
        ):
            await handler.handle_document(
                message=msg,
                session_manager=MagicMock(),
                truncation=MagicMock(),
            )
            msg.answer.assert_called_once()
            assert "Failed to download" in msg.answer.call_args[0][0]
            mock_hm.assert_not_called()
