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
from archon.chat.voice import VoiceMessageHandler
from archon.config.loader import (
    AccessConfig,
    Config,
    LoggingConfig,
    OutputConfig,
    SessionConfig,
    VoiceConfig,
    VoiceSTTConfig,
    VoiceTTSConfig,
)
from archon.gateway.gateway import _setup_dp


_FAKE_TOKEN = "12345:AAFakeTokenForTestingPurposesOnly123"


def _make_config(voice_enabled: bool = False) -> Config:
    return Config(
        telegram_bot_token=_FAKE_TOKEN,
        access=AccessConfig(allowed_user_ids=[123]),
        session=SessionConfig(working_directory="/tmp"),
        output=OutputConfig(),
        logging=LoggingConfig(),
        voice=VoiceConfig(
            enabled=voice_enabled,
            stt=VoiceSTTConfig(),
            tts=VoiceTTSConfig(),
        ),
    )


def _mock_session_manager() -> MagicMock:
    return MagicMock(spec=SessionManager)


def _get_handler_names(dp: Dispatcher) -> list[str]:
    """Extract handler method names from dispatcher in order."""
    names = []
    for h in dp.message.handlers:
        cb = h.callback
        if hasattr(cb, "__func__"):
            names.append(cb.__func__.__name__)
        elif hasattr(cb, "__name__"):
            names.append(cb.__name__)
        else:
            names.append(str(cb))
    return names


class TestFileHandlerRegistration:
    def test_all_file_handlers_registered_when_attachment_store_provided(self, tmp_path: Path) -> None:
        """All FileHandler methods are registered when an AttachmentStore is given."""
        cfg = _make_config()
        dp = create_dispatcher()
        store = AttachmentStore(tmp_path)

        _setup_dp(dp, cfg, _mock_session_manager(), attachment_store=store)

        callbacks = [h.callback for h in dp.message.handlers]
        file_handler_callbacks = [
            cb for cb in callbacks
            if hasattr(cb, "__self__") and isinstance(cb.__self__, FileHandler)
        ]
        file_handler_names = {cb.__func__.__name__ for cb in file_handler_callbacks}
        # sticker + photo + video + audio_attachment (voice disabled) + document = 5
        assert "handle_sticker" in file_handler_names
        assert "handle_photo" in file_handler_names
        assert "handle_video" in file_handler_names
        assert "handle_document" in file_handler_names
        assert "handle_audio_attachment" in file_handler_names

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

    def test_canonical_handler_order(self, tmp_path: Path) -> None:
        """Handlers are registered in canonical order: sticker, photo, video, audio, document, text."""
        cfg = _make_config()
        dp = create_dispatcher()
        store = AttachmentStore(tmp_path)

        _setup_dp(dp, cfg, _mock_session_manager(), attachment_store=store)

        names = _get_handler_names(dp)

        sticker_idx = names.index("handle_sticker")
        photo_idx = names.index("handle_photo")
        video_idx = names.index("handle_video")
        audio_idx = names.index("handle_audio_attachment")
        doc_idx = names.index("handle_document")
        text_idx = names.index("handle_message")

        assert sticker_idx < photo_idx < video_idx < audio_idx < doc_idx < text_idx

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
        assert doc_idx < text_idx

    def test_voice_enabled_registers_voice_audio_handler(self, tmp_path: Path) -> None:
        """When voice is enabled, VoiceMessageHandler handles audio (not FileHandler)."""
        cfg = _make_config(voice_enabled=True)
        dp = create_dispatcher()
        store = AttachmentStore(tmp_path)

        _setup_dp(dp, cfg, _mock_session_manager(), attachment_store=store)

        names = _get_handler_names(dp)
        # Voice handler's audio method should be registered, not file handler's
        assert "handle_audio_message" in names
        assert "handle_audio_attachment" not in names

    def test_voice_disabled_registers_file_audio_handler(self, tmp_path: Path) -> None:
        """When voice is disabled, FileHandler handles audio as attachment."""
        cfg = _make_config(voice_enabled=False)
        dp = create_dispatcher()
        store = AttachmentStore(tmp_path)

        _setup_dp(dp, cfg, _mock_session_manager(), attachment_store=store)

        names = _get_handler_names(dp)
        assert "handle_audio_attachment" in names
        # Voice handler's methods should NOT be registered
        assert "handle_voice_message" not in names
        assert "handle_audio_message" not in names

    def test_sticker_handler_registered(self, tmp_path: Path) -> None:
        """Sticker handler is registered when attachment store is provided."""
        cfg = _make_config()
        dp = create_dispatcher()
        store = AttachmentStore(tmp_path)

        _setup_dp(dp, cfg, _mock_session_manager(), attachment_store=store)

        names = _get_handler_names(dp)
        assert "handle_sticker" in names

    def test_video_handler_registered(self, tmp_path: Path) -> None:
        """Video handler is registered when attachment store is provided."""
        cfg = _make_config()
        dp = create_dispatcher()
        store = AttachmentStore(tmp_path)

        _setup_dp(dp, cfg, _mock_session_manager(), attachment_store=store)

        names = _get_handler_names(dp)
        assert "handle_video" in names
