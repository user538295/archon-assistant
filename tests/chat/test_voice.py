"""Tests for VoiceMessageHandler."""
import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Audio, Voice

from archon.ai.event_mapper import Response, ThinkingResult, ToolStarted
from archon.ai.truncation import SplitStrategy
from archon.ai.tts import TTSConfig
from archon.chat.voice import VoiceMessageHandler, _MIME_EXT_MAP


def _make_voice_handler(
    tts_auto: str = "off",
    truncation=None,
    notifications=None,
    history_manager=None,
    agent_logger=None,
    background_agent_manager=None,
) -> VoiceMessageHandler:
    """Create a VoiceMessageHandler with mocked dependencies."""
    sm = MagicMock()
    sm.get_or_create = AsyncMock()
    tts_cfg = TTSConfig(auto=tts_auto)
    return VoiceMessageHandler(
        session_manager=sm,
        stt_config={"model": "tiny"},
        tts_config=tts_cfg,
        truncation=truncation or SplitStrategy(),
        max_len=4000,
        notifications=notifications,
        history_manager=history_manager,
        agent_logger=agent_logger,
        background_agent_manager=background_agent_manager,
    )


def _mock_session(events: list[object] | None = None) -> MagicMock:
    """Create a mock session that yields given events."""
    session = MagicMock()

    async def _send(prompt: str) -> AsyncGenerator[object, None]:
        for ev in (events or []):
            yield ev

    session.send = _send
    return session


def _mock_session_error(exc: Exception) -> MagicMock:
    """Create a mock session that raises an exception during send."""
    session = MagicMock()

    async def _send(prompt: str) -> AsyncGenerator[object, None]:
        raise exc
        yield  # noqa: unreachable — makes this an async generator

    session.send = _send
    return session


def _make_voice_msg(file_id: str = "abc123", duration: int = 5, user_id: int = 42) -> MagicMock:
    """Create a mocked voice Message with proper download support."""
    msg = MagicMock()
    msg.voice = MagicMock(spec=Voice)
    msg.voice.file_id = file_id
    msg.voice.duration = duration
    msg.audio = None
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.chat = MagicMock()
    msg.chat.id = user_id
    msg.answer = AsyncMock(return_value=MagicMock())
    msg.answer_voice = AsyncMock()
    msg.bot = MagicMock()
    msg.bot.get_file = AsyncMock(return_value=MagicMock(file_path="voice/file.ogg"))
    msg.bot.send_chat_action = AsyncMock()

    async def _fake_download(file_path, dest):
        Path(dest).write_bytes(b"\x00" * 100)

    msg.bot.download_file = AsyncMock(side_effect=_fake_download)
    return msg


def _make_audio_msg(file_id: str = "audio456", user_id: int = 42, mime_type: str = "audio/mpeg") -> MagicMock:
    """Create a mocked audio Message."""
    msg = MagicMock()
    msg.audio = MagicMock(spec=Audio)
    msg.audio.file_id = file_id
    msg.audio.mime_type = mime_type
    msg.voice = None
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.chat = MagicMock()
    msg.chat.id = user_id
    msg.answer = AsyncMock(return_value=MagicMock())
    msg.answer_voice = AsyncMock()
    msg.bot = MagicMock()
    msg.bot.get_file = AsyncMock(return_value=MagicMock(file_path="audio/file.mp3"))
    msg.bot.send_chat_action = AsyncMock()

    async def _fake_download(file_path, dest):
        Path(dest).write_bytes(b"\x00" * 100)

    msg.bot.download_file = AsyncMock(side_effect=_fake_download)
    return msg


# ──────────────────────────────────────────────────────────────────
# Construction — the original crash (TTSConfig.is_enabled)
# ──────────────────────────────────────────────────────────────────


def test_voice_handler_constructs_with_tts_off() -> None:
    """TTSConfig(auto='off') must not crash; tts must be None."""
    vmh = _make_voice_handler(tts_auto="off")
    assert vmh.tts is None


def test_voice_handler_constructs_with_tts_inbound() -> None:
    """TTSConfig(auto='inbound') must create a TTSHandler."""
    vmh = _make_voice_handler(tts_auto="inbound")
    assert vmh.tts is not None


def test_voice_handler_constructs_with_tts_always() -> None:
    """TTSConfig(auto='always') must create a TTSHandler."""
    vmh = _make_voice_handler(tts_auto="always")
    assert vmh.tts is not None


# ──────────────────────────────────────────────────────────────────
# MIME type → extension mapping
# ──────────────────────────────────────────────────────────────────


def test_mime_ext_map_mp3() -> None:
    assert _MIME_EXT_MAP["audio/mpeg"] == ".mp3"


def test_mime_ext_map_unknown_returns_default() -> None:
    """Unknown MIME type must fall back to .ogg via dict.get."""
    assert _MIME_EXT_MAP.get("video/mp4", ".ogg") == ".ogg"


def test_mime_ext_map_none_returns_default() -> None:
    assert _MIME_EXT_MAP.get(None, ".ogg") == ".ogg"


# ──────────────────────────────────────────────────────────────────
# handle_voice_message — guard clause
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_voice_no_attachment() -> None:
    """Message without voice attachment must send error."""
    vmh = _make_voice_handler()
    msg = MagicMock()
    msg.voice = None
    msg.answer = AsyncMock()

    await vmh.handle_voice_message(msg)
    msg.answer.assert_awaited_once()
    assert "No voice attachment" in msg.answer.call_args[0][0]


# ──────────────────────────────────────────────────────────────────
# handle_voice_message — transcribe + process flow
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_voice_transcribes_and_processes() -> None:
    """Voice message must be transcribed, then response streamed to Telegram."""
    session = _mock_session(events=[Response(content="Hello back")])
    vmh = _make_voice_handler()
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="hello world"):
        await vmh.handle_voice_message(msg)

    vmh.session_manager.get_or_create.assert_awaited_once_with(42)
    answer_calls = [str(c) for c in msg.answer.call_args_list]
    assert any("Hello back" in c for c in answer_calls)


@pytest.mark.asyncio
async def test_handle_voice_empty_transcription() -> None:
    """Empty transcription must send error, not process."""
    vmh = _make_voice_handler()
    msg = _make_voice_msg()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value=""):
        await vmh.handle_voice_message(msg)

    vmh.session_manager.get_or_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_voice_shows_transcription_preview() -> None:
    """Transcription must be shown to user with 🎤 prefix."""
    session = _mock_session(events=[Response(content="ok")])
    vmh = _make_voice_handler()
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="hello world"):
        await vmh.handle_voice_message(msg)

    answer_calls = [str(c) for c in msg.answer.call_args_list]
    assert any("🎤" in c and "hello world" in c for c in answer_calls)


@pytest.mark.asyncio
async def test_handle_voice_truncates_long_transcription_preview() -> None:
    """Transcription preview > 200 chars must be truncated with '...'."""
    long_text = "a" * 300
    session = _mock_session(events=[Response(content="ok")])
    vmh = _make_voice_handler()
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value=long_text):
        await vmh.handle_voice_message(msg)

    answer_calls = [call.args[0] if call.args else "" for call in msg.answer.call_args_list]
    preview_calls = [c for c in answer_calls if "🎤" in c]
    assert preview_calls
    assert len(preview_calls[0]) < 250  # 200 chars + emoji prefix + ...


# ──────────────────────────────────────────────────────────────────
# handle_voice_message — error paths
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_voice_transcription_timeout() -> None:
    """Transcription timeout must send error to user, not crash."""
    vmh = _make_voice_handler()
    msg = _make_voice_msg()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, side_effect=asyncio.TimeoutError):
        await vmh.handle_voice_message(msg)

    answer_calls = [str(c) for c in msg.answer.call_args_list]
    assert any("timed out" in c.lower() for c in answer_calls)
    vmh.session_manager.get_or_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_voice_session_error() -> None:
    """Error during session.send() must be caught and reported."""
    session = _mock_session_error(RuntimeError("Claude crashed"))
    vmh = _make_voice_handler()
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="hello"):
        await vmh.handle_voice_message(msg)  # must not raise

    answer_calls = [str(c) for c in msg.answer.call_args_list]
    assert any("Error" in c for c in answer_calls)


@pytest.mark.asyncio
async def test_handle_voice_typing_failure_continues() -> None:
    """Typing indicator failure must not prevent processing."""
    session = _mock_session(events=[Response(content="ok")])
    vmh = _make_voice_handler()
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg()
    msg.bot.send_chat_action = AsyncMock(side_effect=Exception("Telegram error"))

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="hello"):
        await vmh.handle_voice_message(msg)  # must not raise

    vmh.session_manager.get_or_create.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────
# handle_audio_message — guard clause + happy path
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_audio_no_attachment() -> None:
    """Message without audio attachment must send error."""
    vmh = _make_voice_handler()
    msg = MagicMock()
    msg.audio = None
    msg.answer = AsyncMock()

    await vmh.handle_audio_message(msg)
    msg.answer.assert_awaited_once()
    assert "No audio attachment" in msg.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_handle_audio_transcribes_and_processes() -> None:
    """Audio file must be transcribed and processed like voice."""
    session = _mock_session(events=[Response(content="Audio reply")])
    vmh = _make_voice_handler()
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_audio_msg()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="hello from audio"):
        await vmh.handle_audio_message(msg)

    vmh.session_manager.get_or_create.assert_awaited_once_with(42)
    answer_calls = [str(c) for c in msg.answer.call_args_list]
    assert any("Audio reply" in c for c in answer_calls)


@pytest.mark.asyncio
async def test_handle_audio_uses_correct_extension() -> None:
    """Audio messages must use extension from MIME type for download."""
    vmh = _make_voice_handler()
    msg = _make_audio_msg(mime_type="audio/mpeg")

    captured_paths: list[str] = []
    original_download = msg.bot.download_file.side_effect

    async def _capture_download(file_path, dest):
        captured_paths.append(str(dest))
        await original_download(file_path, dest)

    msg.bot.download_file = AsyncMock(side_effect=_capture_download)

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value=""):
        await vmh.handle_audio_message(msg)

    assert captured_paths
    assert captured_paths[0].endswith(".mp3")


# ──────────────────────────────────────────────────────────────────
# History recording
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_history_manager_records_user_message() -> None:
    """history_manager.record_user_message must be called with transcribed text."""
    hm = MagicMock()
    session = _mock_session(events=[Response(content="ok")])
    vmh = _make_voice_handler(history_manager=hm)
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="hello"):
        await vmh.handle_voice_message(msg)

    hm.record_user_message.assert_called_once_with(42, "hello", cwd="")


@pytest.mark.asyncio
async def test_history_manager_records_events() -> None:
    """history_manager.record_event must be called for each streamed event."""
    hm = MagicMock()
    events = [ToolStarted(name="Read", input="/foo", id="1"), Response(content="ok")]
    session = _mock_session(events=events)
    vmh = _make_voice_handler(history_manager=hm)
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="hello"):
        await vmh.handle_voice_message(msg)

    assert hm.record_event.call_count == 2


# ──────────────────────────────────────────────────────────────────
# Sub-agent event routing
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_subagent_events_routed_to_agent_logger() -> None:
    """Events with source='sub-agent' must go to agent_logger, not Telegram."""
    al = MagicMock()
    sub_event = ThinkingResult(content="thinking")
    sub_event.source = "sub-agent"  # type: ignore[attr-defined]
    session = _mock_session(events=[sub_event, Response(content="done")])
    vmh = _make_voice_handler(agent_logger=al)
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="hello"):
        await vmh.handle_voice_message(msg)

    al.record_event.assert_called_once_with(sub_event)


# ──────────────────────────────────────────────────────────────────
# Reminder tracking — voice parity with handler.py
# ──────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────
# TTS response — integration
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tts_response_sent_when_enabled() -> None:
    """When TTS auto='always', a voice note must be generated after the response."""
    session = _mock_session(events=[Response(content="TTS this")])
    vmh = _make_voice_handler(tts_auto="always")
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="test"), \
         patch.object(vmh.tts, "synthesize", new_callable=AsyncMock) as mock_synth:
        async def _create_file(text, path):
            path.write_bytes(b"\x00" * 50)
            return path
        mock_synth.side_effect = _create_file

        await vmh.handle_voice_message(msg)

    mock_synth.assert_awaited_once()
    msg.answer_voice.assert_awaited_once()


@pytest.mark.asyncio
async def test_tts_response_not_sent_when_off() -> None:
    """When TTS auto='off', no voice note must be generated."""
    session = _mock_session(events=[Response(content="No TTS")])
    vmh = _make_voice_handler(tts_auto="off")
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="test"):
        await vmh.handle_voice_message(msg)

    msg.answer_voice.assert_not_awaited()


@pytest.mark.asyncio
async def test_tts_synthesis_failure_does_not_crash() -> None:
    """TTS synthesis failure must be swallowed — text response was already sent."""
    session = _mock_session(events=[Response(content="TTS will fail")])
    vmh = _make_voice_handler(tts_auto="always")
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="test"), \
         patch.object(vmh.tts, "synthesize", new_callable=AsyncMock, side_effect=RuntimeError("TTS boom")):
        await vmh.handle_voice_message(msg)  # must not raise

    # Text response was still delivered
    answer_calls = [str(c) for c in msg.answer.call_args_list]
    assert any("TTS will fail" in c for c in answer_calls)


@pytest.mark.asyncio
async def test_tts_no_output_file_does_not_crash() -> None:
    """If TTS synthesize succeeds but creates no file, must not crash."""
    session = _mock_session(events=[Response(content="TTS empty")])
    vmh = _make_voice_handler(tts_auto="always")
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="test"), \
         patch.object(vmh.tts, "synthesize", new_callable=AsyncMock):
        # synthesize doesn't create the file
        await vmh.handle_voice_message(msg)  # must not raise

    msg.answer_voice.assert_not_awaited()


# ──────────────────────────────────────────────────────────────────
# Gateway — handler registration order
# ──────────────────────────────────────────────────────────────────


def test_voice_handlers_registered_before_generic_handler() -> None:
    """When voice is enabled, voice handlers must be registered BEFORE handle_message."""
    from archon.chat.bot import create_dispatcher
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

    cfg = Config(
        telegram_bot_token="12345:AAFakeToken",
        access=AccessConfig(allowed_user_ids=[100]),
        session=SessionConfig(working_directory="/tmp"),
        output=OutputConfig(),
        logging=LoggingConfig(),
        voice=VoiceConfig(
            enabled=True,
            stt=VoiceSTTConfig(model="tiny"),
            tts=VoiceTTSConfig(auto="off"),
        ),
    )
    dp = create_dispatcher()
    sm = MagicMock()
    sm.get_or_create = AsyncMock()
    _setup_dp(dp, cfg, sm)

    handlers = dp.message.handlers
    handler_names = []
    for h in handlers:
        cb = h.callback
        name = getattr(cb, "__name__", "") or getattr(cb, "__qualname__", "")
        handler_names.append(name)

    voice_positions = [i for i, n in enumerate(handler_names) if "voice" in n.lower() or "audio" in n.lower()]
    text_positions = [i for i, n in enumerate(handler_names) if n == "handle_message"]

    assert voice_positions, f"Voice handlers not found in {handler_names}"
    assert text_positions, f"handle_message not found in {handler_names}"
    for vp in voice_positions:
        for tp in text_positions:
            assert vp < tp, f"Voice handler at {vp} must be before handle_message at {tp}: {handler_names}"


def test_voice_disabled_no_voice_handlers_registered() -> None:
    """When voice.enabled=False, no voice handlers must be registered."""
    from archon.chat.bot import create_dispatcher
    from archon.config.loader import AccessConfig, Config, LoggingConfig, OutputConfig, SessionConfig
    from archon.gateway.gateway import _setup_dp

    cfg = Config(
        telegram_bot_token="12345:AAFakeToken",
        access=AccessConfig(allowed_user_ids=[100]),
        session=SessionConfig(working_directory="/tmp"),
        output=OutputConfig(),
        logging=LoggingConfig(),
    )
    dp = create_dispatcher()
    sm = MagicMock()
    _setup_dp(dp, cfg, sm)

    handlers = dp.message.handlers
    handler_names = []
    for h in handlers:
        cb = h.callback
        name = getattr(cb, "__name__", "") or getattr(cb, "__qualname__", "")
        handler_names.append(name)

    voice_handlers = [n for n in handler_names if "voice" in n.lower() or "audio" in n.lower()]
    assert not voice_handlers, f"Voice handlers found but voice is disabled: {voice_handlers}"
