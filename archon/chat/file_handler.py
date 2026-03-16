"""Telegram file attachment handlers."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from aiogram.types import Message

from archon.ai.attachment_prompt import build_attachment_prompt
from archon.ai.attachment_store import AttachmentStore
from archon.ai.attachment_types import AttachmentInfo, check_file_size, detect_mime_type
from archon.ai.image_resizer import ImageResizer, ResizeResult

if TYPE_CHECKING:
    from archon.ai.agent_logger import AgentLogger
    from archon.ai.background_agent_manager import BackgroundAgentManager
    from archon.ai.history_manager import HistoryManager
    from archon.ai.session_manager import SessionManager
    from archon.ai.truncation import TruncationStrategy
    from archon.config.loader import NotificationsConfig

logger = logging.getLogger("archon")

_DOWNLOAD_TIMEOUT = 30  # seconds

# Image MIME types that should go through the image pipeline when sent as documents
_IMAGE_DOCUMENT_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


class FileHandler:
    """Handles file attachments from Telegram messages."""

    def __init__(
        self,
        attachment_store: AttachmentStore,
        image_resizer: ImageResizer | None = None,
    ) -> None:
        self._store = attachment_store
        self._resizer = image_resizer or ImageResizer()

    def _process_image(
        self,
        rel_path: Path,
        mime: str,
        data: bytes,
    ) -> AttachmentInfo:
        """Run image resize pipeline and build AttachmentInfo."""
        saved_abs = self._store.base_dir / rel_path
        resize_result: ResizeResult = self._resizer.resize_if_needed(saved_abs)

        info = AttachmentInfo(
            path=rel_path,
            mime_type=mime,
            size_bytes=len(data),
            dimensions=resize_result.original_dimensions,
        )
        if resize_result.resized and resize_result.resized_path:
            info.resized_from = resize_result.original_dimensions
            info.dimensions = resize_result.new_dimensions
            info.resized_path = resize_result.resized_path.relative_to(
                self._store.base_dir
            )
            info.resized_size_bytes = resize_result.resized_path.stat().st_size

        return info

    async def handle_photo(
        self,
        message: Message,
        session_manager: "SessionManager",
        truncation: "TruncationStrategy",
        max_len: int = 4000,
        notifications: "NotificationsConfig | None" = None,
        cwd: str = "",
        history_manager: "HistoryManager | None" = None,
        agent_logger: "AgentLogger | None" = None,
        background_agent_manager: "BackgroundAgentManager | None" = None,
    ) -> None:
        """Handle incoming photo attachment."""
        from archon.chat.handler import handle_message

        if not message.photo:
            return

        # Use largest photo (last in array -- Telegram sends ascending size)
        photo = message.photo[-1]

        # File size check
        error = check_file_size(photo.file_size)
        if error:
            await message.answer(error)
            return

        # Download
        try:
            file = await asyncio.wait_for(
                message.bot.get_file(photo.file_id),
                timeout=_DOWNLOAD_TIMEOUT,
            )
            buf = await asyncio.wait_for(
                message.bot.download_file(file.file_path),
                timeout=_DOWNLOAD_TIMEOUT,
            )
        except asyncio.TimeoutError:
            await message.answer("File download timed out. Please try again.")
            return
        except Exception:
            logger.exception("Failed to download photo")
            await message.answer("Failed to download the file. Please try again.")
            return

        if buf is None:
            await message.answer("Failed to download the file. Please try again.")
            return

        data = buf.read()

        # Determine filename -- Telegram photos don't have filenames
        ext = Path(file.file_path).suffix if file.file_path else ".jpg"
        filename = f"photo_{photo.file_unique_id}{ext}"

        # Save
        try:
            rel_path = self._store.save(filename=filename, data=data)
        except Exception:
            logger.exception("Failed to save photo")
            await message.answer(
                "Failed to save the file. Check disk space and permissions."
            )
            return

        # Process through image pipeline
        mime = detect_mime_type(filename, None)
        info = self._process_image(rel_path, mime, data)

        prompt = build_attachment_prompt([info], caption=message.caption)

        await handle_message(
            message=message,
            session_manager=session_manager,
            truncation=truncation,
            max_len=max_len,
            notifications=notifications,
            cwd=cwd,
            history_manager=history_manager,
            agent_logger=agent_logger,
            background_agent_manager=background_agent_manager,
            prompt_override=prompt,
        )

    async def handle_document(
        self,
        message: Message,
        session_manager: "SessionManager",
        truncation: "TruncationStrategy",
        max_len: int = 4000,
        notifications: "NotificationsConfig | None" = None,
        cwd: str = "",
        history_manager: "HistoryManager | None" = None,
        agent_logger: "AgentLogger | None" = None,
        background_agent_manager: "BackgroundAgentManager | None" = None,
    ) -> None:
        """Handle incoming document attachment."""
        from archon.chat.handler import handle_message

        doc = message.document
        if doc is None:
            return

        # File size check
        error = check_file_size(doc.file_size)
        if error:
            await message.answer(error)
            return

        # Download file
        try:
            file = await asyncio.wait_for(
                message.bot.get_file(doc.file_id),
                timeout=_DOWNLOAD_TIMEOUT,
            )
            buf = await asyncio.wait_for(
                message.bot.download_file(file.file_path),
                timeout=_DOWNLOAD_TIMEOUT,
            )
        except asyncio.TimeoutError:
            await message.answer("File download timed out. Please try again.")
            return
        except Exception:
            logger.exception("Failed to download document %s", doc.file_id)
            await message.answer("Failed to download the file. Please try again.")
            return

        if buf is None:
            await message.answer("Failed to download the file. Please try again.")
            return

        data = buf.read()

        # Save to disk
        try:
            rel_path = self._store.save(
                filename=doc.file_name or "document",
                data=data,
            )
        except Exception:
            logger.exception("Failed to save document")
            await message.answer(
                "Failed to save the file. Check disk space and permissions."
            )
            return

        # Build attachment info
        mime = detect_mime_type(doc.file_name or "document", doc.mime_type)

        if mime in _IMAGE_DOCUMENT_MIMES:
            info = self._process_image(rel_path, mime, data)
        else:
            info = AttachmentInfo(
                path=rel_path,
                mime_type=mime,
                size_bytes=len(data),
            )

        # Build prompt and delegate to handle_message
        prompt = build_attachment_prompt([info], caption=message.caption)

        await handle_message(
            message=message,
            session_manager=session_manager,
            truncation=truncation,
            max_len=max_len,
            notifications=notifications,
            cwd=cwd,
            history_manager=history_manager,
            agent_logger=agent_logger,
            background_agent_manager=background_agent_manager,
            prompt_override=prompt,
        )
