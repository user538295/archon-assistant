"""Tests for file handler registration in gateway."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from aiogram import Dispatcher

from archon.ai.attachment_store import AttachmentStore
from archon.ai.session_manager import SessionManager
from archon.chat.bot import create_dispatcher
from archon.chat.file_handler import FileHandler
from archon.chat.handler import handle_message
from archon.config.loader import AccessConfig, Config, LoggingConfig, OutputConfig, SessionConfig
from archon.gateway.gateway import _setup_dp


_FAKE_TOKEN = "12345:AAFakeTokenForTestingPurposesOnly123"


def _make_config() -> Config:
    return Config(
        telegram_bot_token=_FAKE_TOKEN,
        access=AccessConfig(allowed_user_ids=[123]),
        session=SessionConfig(working_directory="/tmp"),
        output=OutputConfig(),
        logging=LoggingConfig(),
    )


def _mock_session_manager() -> MagicMock:
    return MagicMock(spec=SessionManager)


class TestFileHandlerRegistration:
    def test_document_handler_registered_when_attachment_store_provided(self, tmp_path: Path) -> None:
        """FileHandler.handle_document is registered when an AttachmentStore is given."""
        cfg = _make_config()
        dp = create_dispatcher()
        store = AttachmentStore(tmp_path)

        _setup_dp(dp, cfg, _mock_session_manager(), attachment_store=store)

        callbacks = [h.callback for h in dp.message.handlers]
        file_handler_callbacks = [
            cb for cb in callbacks
            if hasattr(cb, "__self__") and isinstance(cb.__self__, FileHandler)
        ]
        # photo handler + document handler = 2
        assert len(file_handler_callbacks) == 2

    def test_handlers_not_registered_when_attachment_store_is_none(self) -> None:
        """No FileHandler is registered when attachment_store is None."""
        cfg = _make_config()
        dp = create_dispatcher()

        _setup_dp(dp, cfg, _mock_session_manager(), attachment_store=None)

        callbacks = [h.callback for h in dp.message.handlers]
        file_handler_callbacks = [
            cb for cb in callbacks
            if hasattr(cb, "__self__") and isinstance(cb.__self__, FileHandler)
        ]
        assert len(file_handler_callbacks) == 0

    def test_photo_handler_registered_before_document_handler(self, tmp_path: Path) -> None:
        """Photo handler must come before document handler in the handler list."""
        cfg = _make_config()
        dp = create_dispatcher()
        store = AttachmentStore(tmp_path)

        _setup_dp(dp, cfg, _mock_session_manager(), attachment_store=store)

        photo_idx = None
        doc_idx = None
        for i, h in enumerate(dp.message.handlers):
            cb = h.callback
            if hasattr(cb, "__self__") and isinstance(cb.__self__, FileHandler):
                if cb.__func__.__name__ == "handle_photo":
                    photo_idx = i
                elif cb.__func__.__name__ == "handle_document":
                    doc_idx = i

        assert photo_idx is not None, "Photo handler not found"
        assert doc_idx is not None, "Document handler not found"
        assert photo_idx < doc_idx, (
            f"Photo handler (idx={photo_idx}) must be before "
            f"document handler (idx={doc_idx})"
        )

    def test_document_handler_registered_before_generic_text_handler(self, tmp_path: Path) -> None:
        """Document handler must come before handle_message in the handler list."""
        cfg = _make_config()
        dp = create_dispatcher()
        store = AttachmentStore(tmp_path)

        _setup_dp(dp, cfg, _mock_session_manager(), attachment_store=store)

        doc_idx = None
        text_idx = None
        for i, h in enumerate(dp.message.handlers):
            cb = h.callback
            if hasattr(cb, "__self__") and isinstance(cb.__self__, FileHandler):
                if cb.__func__.__name__ == "handle_document":
                    doc_idx = i
            elif cb is handle_message:
                text_idx = i

        assert doc_idx is not None, "Document handler not found"
        assert text_idx is not None, "Generic text handler not found"
        assert doc_idx < text_idx, (
            f"Document handler (idx={doc_idx}) must be before "
            f"generic text handler (idx={text_idx})"
        )
