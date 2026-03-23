"""Integration tests for file attachment flows (documents, photos, images-as-documents).

These tests exercise the full path: Telegram message -> FileHandler
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
from aiogram.types import Document, File, Message, PhotoSize

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
    msg.media_group_id = None
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
        assert "Failed to download" in msg.answer.call_args[0][0]

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


# ──────────────────────────────────────────────────────────────────
# Helpers: photo messages
# ──────────────────────────────────────────────────────────────────


def _mock_photo_message(
    file_size: int = 1024,
    file_id: str = "photo123",
    file_unique_id: str = "uniq123",
    caption: str | None = "What's in this image?",
    data: bytes = b"fake image data",
) -> Message:
    """Create a mock Telegram message with a photo attachment."""
    photo_small = MagicMock(spec=PhotoSize)
    photo_small.file_size = 512
    photo_small.file_id = "small_id"

    photo_large = MagicMock(spec=PhotoSize)
    photo_large.file_size = file_size
    photo_large.file_id = file_id
    photo_large.file_unique_id = file_unique_id

    msg = MagicMock(spec=Message)
    msg.photo = [photo_small, photo_large]
    msg.caption = caption
    msg.media_group_id = None
    msg.text = None
    msg.answer = AsyncMock()
    msg.from_user = MagicMock(id=42)
    msg.chat = MagicMock(id=100)

    file_obj = MagicMock(spec=File)
    file_obj.file_path = "photos/file_123.jpg"
    msg.bot = MagicMock()
    msg.bot.get_file = AsyncMock(return_value=file_obj)
    msg.bot.download_file = AsyncMock(return_value=BytesIO(data))
    msg.bot.send_chat_action = AsyncMock()

    return msg


# ──────────────────────────────────────────────────────────────────
# Integration: photo (image) flow
# ──────────────────────────────────────────────────────────────────


class TestImageFlowIntegration:
    """End-to-end: Telegram photo -> download -> save -> resize check -> prompt -> response."""

    @pytest.mark.asyncio
    async def test_photo_full_flow(self, tmp_path: Path) -> None:
        """Photo: download -> save -> prompt with image metadata -> response."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_photo_message(caption="What's in this image?")
        session_mgr = _mock_session_manager(Response(content="I can see metadata only"))

        await handler.handle_photo(
            message=msg,
            session_manager=session_mgr,
            truncation=SplitStrategy(),
        )

        # File was saved
        saved_files = list(tmp_path.rglob("photo_*"))
        assert len(saved_files) >= 1

        # Response sent to user
        msg.answer.assert_called()

    @pytest.mark.asyncio
    async def test_photo_prompt_contains_visual_analysis_note(self, tmp_path: Path) -> None:
        """Photo prompt should include 'visual analysis not available' note."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_photo_message()

        # Capture the prompt passed to session
        captured_prompts: list[str] = []
        session = MagicMock()
        session.is_processing = False

        async def _send(prompt: str) -> AsyncGenerator:
            captured_prompts.append(prompt)
            yield Response(content="done")

        session.send = _send

        session_mgr = MagicMock(spec=SessionManager)
        session_mgr.get_or_create = AsyncMock(return_value=session)

        await handler.handle_photo(
            message=msg,
            session_manager=session_mgr,
            truncation=SplitStrategy(),
        )

        assert len(captured_prompts) == 1
        assert "Visual analysis is not available" in captured_prompts[0]

    @pytest.mark.asyncio
    async def test_photo_oversized_rejected(self, tmp_path: Path) -> None:
        """Photo exceeding size limit is rejected before download."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_photo_message(file_size=25 * 1024 * 1024)

        await handler.handle_photo(
            message=msg,
            session_manager=MagicMock(),
            truncation=SplitStrategy(),
        )

        msg.answer.assert_called_once()
        assert "too large" in msg.answer.call_args[0][0]


# ──────────────────────────────────────────────────────────────────
# Integration: image sent as document (uncompressed)
# ──────────────────────────────────────────────────────────────────


class TestAttachmentHistoryLogging:
    """Verify that attachment prompts (with full metadata) are recorded in session history."""

    @pytest.mark.asyncio
    async def test_attachment_prompt_logged_to_history(self, tmp_path: Path) -> None:
        """Attachment metadata should be recorded in session history via history_manager."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message(
            file_name="code.py",
            mime_type="text/x-python",
            caption="review this",
        )

        history_manager = MagicMock()
        history_manager.record_user_message = AsyncMock()
        history_manager.record_archon_message = AsyncMock()
        history_manager.record_event = AsyncMock()

        session_mgr = _mock_session_manager(Response(content="ok"))

        await handler.handle_document(
            message=msg,
            session_manager=session_mgr,
            truncation=SplitStrategy(),
            history_manager=history_manager,
        )

        history_manager.record_user_message.assert_called_once()
        recorded_text = history_manager.record_user_message.call_args[0][1]
        assert "[Attachment:" in recorded_text
        assert "code.py" in recorded_text
        assert "Python file" in recorded_text
        assert "User message: review this" in recorded_text

    @pytest.mark.asyncio
    async def test_photo_prompt_logged_to_history(self, tmp_path: Path) -> None:
        """Photo attachment metadata should be recorded in session history."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_photo_message(caption="What is this?")

        history_manager = MagicMock()
        history_manager.record_user_message = AsyncMock()
        history_manager.record_archon_message = AsyncMock()
        history_manager.record_event = AsyncMock()

        session_mgr = _mock_session_manager(Response(content="ok"))

        await handler.handle_photo(
            message=msg,
            session_manager=session_mgr,
            truncation=SplitStrategy(),
            history_manager=history_manager,
        )

        history_manager.record_user_message.assert_called_once()
        recorded_text = history_manager.record_user_message.call_args[0][1]
        assert "[Attachment:" in recorded_text
        assert "Visual analysis is not available" in recorded_text
        assert "User message: What is this?" in recorded_text


# ──────────────────────────────────────────────────────────────────
# Integration: image sent as document (uncompressed)
# ──────────────────────────────────────────────────────────────────


class TestImageAsDocumentIntegration:
    """Image sent as document (uncompressed) routes through image pipeline."""

    @pytest.mark.asyncio
    async def test_jpeg_document_gets_image_treatment(self, tmp_path: Path) -> None:
        """JPEG sent as document gets the image prompt (visual analysis note)."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message(
            file_name="photo.jpg",
            mime_type="image/jpeg",
            caption="Check this photo",
        )

        captured_prompts: list[str] = []
        session = MagicMock()
        session.is_processing = False

        async def _send(prompt: str) -> AsyncGenerator:
            captured_prompts.append(prompt)
            yield Response(content="done")

        session.send = _send

        session_mgr = MagicMock(spec=SessionManager)
        session_mgr.get_or_create = AsyncMock(return_value=session)

        await handler.handle_document(
            message=msg,
            session_manager=session_mgr,
            truncation=SplitStrategy(),
        )

        assert len(captured_prompts) == 1
        assert "Visual analysis is not available" in captured_prompts[0]

    @pytest.mark.asyncio
    async def test_pdf_document_gets_document_treatment(self, tmp_path: Path) -> None:
        """PDF document gets the document prompt (pdftotext note, no image note)."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message(
            file_name="report.pdf",
            mime_type="application/pdf",
            caption="Summarize",
        )

        captured_prompts: list[str] = []
        session = MagicMock()
        session.is_processing = False

        async def _send(prompt: str) -> AsyncGenerator:
            captured_prompts.append(prompt)
            yield Response(content="done")

        session.send = _send

        session_mgr = MagicMock(spec=SessionManager)
        session_mgr.get_or_create = AsyncMock(return_value=session)

        await handler.handle_document(
            message=msg,
            session_manager=session_mgr,
            truncation=SplitStrategy(),
        )

        assert len(captured_prompts) == 1
        assert "pdftotext" in captured_prompts[0]
        assert "Visual analysis" not in captured_prompts[0]


# ──────────────────────────────────────────────────────────────────
# Helpers: video messages
# ──────────────────────────────────────────────────────────────────


def _mock_video_message(
    file_size: int = 5 * 1024 * 1024,
    file_id: str = "vid123",
    file_unique_id: str = "viduniq",
    mime_type: str = "video/mp4",
    caption: str | None = "Check this video",
    is_video_note: bool = False,
) -> Message:
    """Create a mock Telegram message with a video or video_note attachment."""
    from aiogram.types import Video, VideoNote

    msg = MagicMock(spec=Message)
    msg.caption = caption
    msg.text = None
    msg.answer = AsyncMock()
    msg.from_user = MagicMock(id=42)
    msg.chat = MagicMock(id=100)
    msg.media_group_id = None

    if is_video_note:
        vn = MagicMock(spec=VideoNote)
        vn.file_size = file_size
        vn.file_id = file_id
        vn.file_unique_id = file_unique_id
        msg.video = None
        msg.video_note = vn
    else:
        vid = MagicMock(spec=Video)
        vid.file_size = file_size
        vid.file_id = file_id
        vid.file_unique_id = file_unique_id
        vid.file_name = "clip.mp4"
        vid.mime_type = mime_type
        msg.video = vid
        msg.video_note = None

    file_obj = MagicMock(spec=File)
    file_obj.file_path = "videos/clip.mp4"
    msg.bot = MagicMock()
    msg.bot.get_file = AsyncMock(return_value=file_obj)
    msg.bot.download_file = AsyncMock(return_value=BytesIO(b"video data"))
    msg.bot.send_chat_action = AsyncMock()
    return msg


# ──────────────────────────────────────────────────────────────────
# Integration: video flow
# ──────────────────────────────────────────────────────────────────


class TestVideoFlowIntegration:
    """End-to-end: Telegram video -> download -> save -> prompt -> session -> response."""

    @pytest.mark.asyncio
    async def test_video_full_flow(self, tmp_path: Path) -> None:
        """Video: download -> save -> prompt -> response streamed back."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_video_message()
        session_mgr = _mock_session_manager(Response(content="Video received"))

        await handler.handle_video(
            message=msg,
            session_manager=session_mgr,
            truncation=SplitStrategy(),
        )

        # File was saved to disk
        saved_files = list(tmp_path.rglob("clip.mp4"))
        assert len(saved_files) == 1

        # Session was created for the user
        session_mgr.get_or_create.assert_called_once_with(42)

        # Response was delivered back
        msg.answer.assert_called()

    @pytest.mark.asyncio
    async def test_video_note_full_flow(self, tmp_path: Path) -> None:
        """Video note (round message): download -> save -> prompt -> response."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_video_message(is_video_note=True)
        session_mgr = _mock_session_manager(Response(content="Got it"))

        await handler.handle_video(
            message=msg,
            session_manager=session_mgr,
            truncation=SplitStrategy(),
        )

        # File was saved (video_note has no file_name, uses generated name)
        saved_files = list(tmp_path.rglob("video_*"))
        assert len(saved_files) == 1

        msg.answer.assert_called()


# ──────────────────────────────────────────────────────────────────
# Helpers: sticker messages
# ──────────────────────────────────────────────────────────────────


def _mock_sticker_message(
    is_animated: bool = False,
    is_video: bool = False,
    file_id: str = "stk123",
    file_unique_id: str = "stkuniq",
) -> Message:
    """Create a mock Telegram message with a sticker attachment."""
    from aiogram.types import Sticker

    sticker = MagicMock(spec=Sticker)
    sticker.file_id = file_id
    sticker.file_unique_id = file_unique_id
    sticker.file_size = 512
    sticker.is_animated = is_animated
    sticker.is_video = is_video

    msg = MagicMock(spec=Message)
    msg.sticker = sticker
    msg.caption = None
    msg.text = None
    msg.answer = AsyncMock()
    msg.from_user = MagicMock(id=42)
    msg.chat = MagicMock(id=100)
    msg.media_group_id = None

    file_obj = MagicMock(spec=File)
    file_obj.file_path = "stickers/sticker.webp"
    msg.bot = MagicMock()
    msg.bot.get_file = AsyncMock(return_value=file_obj)
    msg.bot.download_file = AsyncMock(return_value=BytesIO(b"sticker data"))
    msg.bot.send_chat_action = AsyncMock()
    return msg


# ──────────────────────────────────────────────────────────────────
# Integration: sticker flow
# ──────────────────────────────────────────────────────────────────


class TestStickerFlowIntegration:
    """End-to-end: Telegram sticker -> download -> save -> prompt -> session -> response."""

    @pytest.mark.asyncio
    async def test_static_sticker_flow(self, tmp_path: Path) -> None:
        """Static WebP sticker: download -> save -> prompt -> response."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_sticker_message()
        session_mgr = _mock_session_manager(Response(content="Sticker saved"))

        await handler.handle_sticker(
            message=msg,
            session_manager=session_mgr,
            truncation=SplitStrategy(),
        )

        saved_files = list(tmp_path.rglob("sticker_*"))
        assert len(saved_files) == 1
        msg.answer.assert_called()

    @pytest.mark.asyncio
    async def test_animated_sticker_flow(self, tmp_path: Path) -> None:
        """Animated (TGS) sticker: download -> save -> prompt -> response."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_sticker_message(is_animated=True)
        session_mgr = _mock_session_manager(Response(content="Animated sticker"))

        await handler.handle_sticker(
            message=msg,
            session_manager=session_mgr,
            truncation=SplitStrategy(),
        )

        saved_files = list(tmp_path.rglob("sticker_*"))
        assert len(saved_files) == 1
        msg.answer.assert_called()


# ──────────────────────────────────────────────────────────────────
# Integration: archive flow
# ──────────────────────────────────────────────────────────────────


class TestArchiveFlowIntegration:
    """Verify ZIP/archive documents trigger the archive-specific prompt."""

    @pytest.mark.asyncio
    async def test_zip_file_asks_user_intent(self, tmp_path: Path) -> None:
        """ZIP file with no caption: prompt should mention 'archive'."""
        store = AttachmentStore(tmp_path)
        handler = FileHandler(store)
        msg = _mock_document_message(
            file_name="backup.zip",
            mime_type="application/zip",
            caption=None,
        )

        captured_prompts: list[str] = []
        session = MagicMock()
        session.is_processing = False

        async def _send(prompt: str) -> AsyncGenerator:
            captured_prompts.append(prompt)
            yield Response(content="What should I do?")

        session.send = _send
        session_mgr = MagicMock(spec=SessionManager)
        session_mgr.get_or_create = AsyncMock(return_value=session)

        await handler.handle_document(
            message=msg,
            session_manager=session_mgr,
            truncation=SplitStrategy(),
        )

        assert len(captured_prompts) == 1
        assert "archive" in captured_prompts[0].lower()


# ──────────────────────────────────────────────────────────────────
# Regression: voice handler registration not broken by file handler
# ──────────────────────────────────────────────────────────────────


class TestVoiceAudioRegression:
    """Verify voice handler registration is not broken by file handler changes."""

    @pytest.mark.asyncio
    async def test_voice_handler_registered_when_voice_enabled(self) -> None:
        """When voice is enabled, voice handler is registered and audio_attachment is NOT."""
        from aiogram import Dispatcher

        from archon.gateway.gateway import _setup_dp

        dp = Dispatcher()
        cfg = MagicMock()
        cfg.output.truncation_strategy = "split"
        cfg.output.max_message_length = 4000
        cfg.output.head_chars = 1500
        cfg.output.tail_chars = 1500
        cfg.session.working_directory = "/tmp"
        cfg.notifications = MagicMock()
        cfg.access.allowed_user_ids = [123]
        cfg.history.enabled = False
        cfg.voice.enabled = True
        cfg.voice.stt.model = "medium"
        cfg.voice.stt.language = None
        cfg.voice.tts.provider = "openai"
        cfg.voice.tts.model = "tts-1"
        cfg.voice.tts.voice = "nova"
        cfg.voice.tts.auto = "off"
        cfg.voice.tts.max_text_length = 3000
        cfg.voice.tts.edge_voice = "en-US"
        cfg.models = MagicMock()

        store = AttachmentStore(Path("/tmp/test_att"))
        sm = MagicMock(spec=SessionManager)

        _setup_dp(dp=dp, cfg=cfg, session_manager=sm, attachment_store=store)

        # Collect callback names from registered message handlers
        handler_names: list[str] = []
        for h in dp.message.handlers:
            cb = getattr(h, "callback", None)
            if cb:
                name = getattr(cb, "__name__", "") or getattr(
                    cb, "__func__", lambda: ""
                ).__name__
                handler_names.append(name)

        # Voice handler must be registered
        assert any("voice" in n.lower() for n in handler_names), (
            f"Expected voice handler, got: {handler_names}"
        )
        # FileHandler.handle_audio_attachment must NOT be registered (voice takes precedence)
        assert "handle_audio_attachment" not in handler_names, (
            "handle_audio_attachment should not be registered when voice is enabled"
        )
