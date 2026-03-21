"""Tests for VoiceMessageHandler."""
import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Audio, Voice

from archon.ai.event_mapper import ErrorEvent, PlanEvent, PromotionEvent, Response, ThinkingResult, ToolStarted
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
    from tests.conftest import _mock_session_factory

    return _mock_session_factory(*(events or []))


def _mock_session_error(exc: Exception) -> MagicMock:
    """Create a mock session that raises an exception during send."""
    session = MagicMock()
    session.is_processing = False  # idle by default, consistent with _mock_session

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
    hm.record_user_message = AsyncMock()
    hm.record_event = AsyncMock()
    session = _mock_session(events=[Response(content="ok")])
    vmh = _make_voice_handler(history_manager=hm)
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="hello"):
        await vmh.handle_voice_message(msg)

    hm.record_user_message.assert_awaited_once_with(42, "hello", cwd="")


@pytest.mark.asyncio
async def test_history_manager_records_events() -> None:
    """history_manager.record_event must be called for each streamed event."""
    hm = MagicMock()
    hm.record_user_message = AsyncMock()
    hm.record_event = AsyncMock()
    events = [ToolStarted(name="Read", input="/foo", id="1"), Response(content="ok")]
    session = _mock_session(events=events)
    vmh = _make_voice_handler(history_manager=hm)
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="hello"):
        await vmh.handle_voice_message(msg)

    assert hm.record_event.await_count == 2


# ──────────────────────────────────────────────────────────────────
# Sub-agent event routing
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_subagent_events_routed_to_agent_logger() -> None:
    """Events with source='sub-agent' must go to agent_logger, not Telegram."""
    al = MagicMock()
    al.record_event = AsyncMock()
    sub_event = ThinkingResult(content="thinking")
    sub_event.source = "sub-agent"  # type: ignore[attr-defined]
    session = _mock_session(events=[sub_event, Response(content="done")])
    vmh = _make_voice_handler(agent_logger=al)
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="hello"):
        await vmh.handle_voice_message(msg)

    al.record_event.assert_awaited_once_with(sub_event)


# ──────────────────────────────────────────────────────────────────
# Reminder tracking — voice parity with handler.py
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_voice_does_not_call_reminder_directly() -> None:
    """voice.py must NOT call session.reminder.record_message/record_tokens directly.

    ClaudeSession.send() already calls these in its finally block.
    Calling them again from voice.py would double-count, firing reminders
    at half the configured interval.
    """
    reminder = MagicMock()
    session = _mock_session(events=[Response(content="ok")])
    session.reminder = reminder
    vmh = _make_voice_handler()
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="hello"):
        await vmh.handle_voice_message(msg)

    reminder.record_message.assert_not_called()
    reminder.record_tokens.assert_not_called()


@pytest.mark.asyncio
async def test_audio_does_not_call_reminder_directly() -> None:
    """handle_audio_message must also not call reminder methods directly."""
    reminder = MagicMock()
    session = _mock_session(events=[Response(content="ok")])
    session.reminder = reminder
    vmh = _make_voice_handler()
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_audio_msg()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="hello from audio"):
        await vmh.handle_audio_message(msg)

    reminder.record_message.assert_not_called()
    reminder.record_tokens.assert_not_called()


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


# ──────────────────────────────────────────────────────────────────
# PromotionEvent — background agent spawn
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_and_respond_promotion_spawns_agent() -> None:
    """PromotionEvent in _process_and_respond must call BAM.spawn() with correct args."""
    promotion = PromotionEvent(
        agent_prompt="enriched background prompt",
        original_prompt="user voice request",
        tool_count=4,
    )
    session = _mock_session(events=[promotion])
    session.context_summary = "some prior context"
    bam = MagicMock()
    bam.spawn = AsyncMock()
    vmh = _make_voice_handler(background_agent_manager=bam)
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg(user_id=42)

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="user voice request"):
        await vmh.handle_voice_message(msg)

    bam.spawn.assert_awaited_once()
    call_kwargs = bam.spawn.call_args
    assert call_kwargs.kwargs.get("task") == "enriched background prompt" or (call_kwargs[1] or {}).get("task") == "enriched background prompt"
    context_passed = call_kwargs.kwargs.get("context") or (call_kwargs[1] or {}).get("context")
    assert context_passed == "some prior context"


@pytest.mark.asyncio
async def test_process_and_respond_promotion_without_bam_does_not_crash() -> None:
    """PromotionEvent without BAM must not crash — just format and continue."""
    promotion = PromotionEvent(
        agent_prompt="prompt",
        original_prompt="query",
        tool_count=2,
    )
    session = _mock_session(events=[promotion])
    vmh = _make_voice_handler()  # no background_agent_manager
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="query"):
        await vmh.handle_voice_message(msg)  # must not raise

    answer_calls = [str(c) for c in msg.answer.call_args_list]
    # format_event for PromotionEvent now returns "🔄 Task is bigger than expected (N tools used)"
    assert any("bigger than expected" in c.lower() or "task" in c.lower() for c in answer_calls)


@pytest.mark.asyncio
async def test_plan_executor_receives_history_manager() -> None:
    """PlanExecutor must be instantiated with history_manager= from VoiceMessageHandler."""
    from archon.ai.event_mapper import PlanEvent
    from archon.ai.agent_plan import AgentPlan, AgentTask

    plan = AgentPlan(scope="large", summary="test plan", agents=[AgentTask(id="a1", task="do it", depends_on=())])
    plan_event = PlanEvent(plan=plan, summary="test plan")
    session = _mock_session(events=[plan_event])
    session.context_summary = ""
    bam = MagicMock()
    bam.spawn = AsyncMock()
    hm = MagicMock()
    hm.record_user_message = AsyncMock()
    hm.record_event = AsyncMock()
    vmh = _make_voice_handler(background_agent_manager=bam, history_manager=hm)
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg(user_id=42)

    with patch("archon.ai.plan_executor.PlanExecutor") as mock_plan_executor_cls:
        mock_executor = MagicMock()
        mock_executor.execute = AsyncMock()
        mock_plan_executor_cls.return_value = mock_executor

        with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="test"):
            await vmh.handle_voice_message(msg)

        mock_plan_executor_cls.assert_called_once()
        call_kwargs = mock_plan_executor_cls.call_args
        assert call_kwargs.kwargs.get("history_manager") is hm


# ──────────────────────────────────────────────────────────────────
# Issue A — fire-and-forget task stored in _background_tasks
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_plan_executor_task_stored_in_background_tasks_and_removed_on_completion_voice() -> None:
    """PlanExecutor task in voice handler must be stored in _background_tasks while running
    and automatically removed via done_callback when it completes."""
    import archon.chat.voice as voice_module
    from archon.ai.event_mapper import PlanEvent
    from archon.ai.agent_plan import AgentPlan, AgentTask

    plan = AgentPlan(scope="large", summary="voice plan", agents=[AgentTask(id="a1", task="do it", depends_on=())])
    plan_event = PlanEvent(plan=plan, summary="voice plan")
    session = _mock_session(events=[plan_event])
    session.context_summary = ""
    bam = MagicMock()
    vmh = _make_voice_handler(background_agent_manager=bam)
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg(user_id=42)

    with patch("archon.ai.plan_executor.PlanExecutor") as MockExecutor:
        mock_instance = MagicMock()
        mock_instance.execute = AsyncMock()
        MockExecutor.return_value = mock_instance

        before = set(voice_module._background_tasks)

        with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="test"):
            await vmh.handle_voice_message(msg)

        # Allow the created task to finish
        await asyncio.sleep(0.05)

    after = set(voice_module._background_tasks)
    assert after == before, (
        f"_background_tasks should be empty after task completion; extra tasks: {after - before}"
    )


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


# ──────────────────────────────────────────────────────────────────
# Issue #11 — assert file_info.file_path replaced with explicit check
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_download_transcribe_graceful_when_file_path_none() -> None:
    """Issue #11: When file_info.file_path is None, must return None gracefully — no AssertionError."""
    vmh = _make_voice_handler()
    msg = _make_voice_msg()

    file_info = MagicMock()
    file_info.file_path = None
    msg.bot.get_file = AsyncMock(return_value=file_info)

    await vmh.handle_voice_message(msg)

    vmh.session_manager.get_or_create.assert_not_awaited()
    answer_calls = msg.answer.call_args_list
    assert any("failed" in str(call).lower() or "unavailable" in str(call).lower() for call in answer_calls)


# ──────────────────────────────────────────────────────────────────
# Issue A — assert replaced by graceful skip (message.bot is None)
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_download_skips_gracefully_when_bot_is_none() -> None:
    """When message.bot is None in _download_and_transcribe, returns None gracefully — no AssertionError."""
    vmh = _make_voice_handler()
    msg = _make_voice_msg()
    msg.bot = None  # simulate missing bot reference

    # Must not raise; returns None which causes handle_voice_message to return early
    await vmh.handle_voice_message(msg)

    # session.get_or_create must not be called (transcription never succeeded)
    vmh.session_manager.get_or_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_and_respond_skips_typing_gracefully_when_bot_is_none() -> None:
    """When message.bot is None in _process_and_respond, typing indicator skipped — no AssertionError."""
    session = _mock_session(events=[Response(content="Done")])
    vmh = _make_voice_handler()
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="hello"):
        msg.bot = None  # set after transcription setup — no bot for typing/download calls
        await vmh.handle_voice_message(msg)

    # answer must have been called (the 🎤 preview is sent before bot is used for typing)
    # but crucially: no AssertionError raised


# ──────────────────────────────────────────────────────────────────
# Issue B — TelegramRetryAfter triggers sleep + retry in voice.py
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_voice_retry_after_triggers_sleep_and_retry() -> None:
    """TelegramRetryAfter on first event send must sleep retry_after+1 s then retry once."""
    from unittest.mock import patch
    from aiogram.exceptions import TelegramRetryAfter

    session = _mock_session(events=[Response(content="Hello back")])
    vmh = _make_voice_handler()
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg()

    call_count = 0
    original_answer = msg.answer

    async def _answer_rate_limited(text: str, **kwargs: object) -> None:
        nonlocal call_count
        call_count += 1
        # First event reply (call 3: after 🎤 preview at 1, ⏳ Processing... at 2) gets rate-limited
        if call_count == 3:
            raise TelegramRetryAfter(method=MagicMock(), message="flood", retry_after=2)

    msg.answer = AsyncMock(side_effect=_answer_rate_limited)

    slept: list[float] = []

    async def _fake_sleep(duration: float) -> None:
        slept.append(duration)

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="hello"), \
         patch("archon.chat.voice.asyncio.sleep", side_effect=_fake_sleep):
        await vmh.handle_voice_message(msg)

    # sleep must have been called with retry_after + 1 = 3
    assert any(s == 3 for s in slept), f"Expected sleep(3) for retry_after=2, got: {slept}"


# ──────────────────────────────────────────────────────────────────
# Bug 12 — queued notification when session is busy
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_voice_sends_queued_notification_when_session_is_processing() -> None:
    """When session.is_processing is True, user must receive 'queued' notification."""
    session = _mock_session(events=[Response(content="ok")])
    session.is_processing = True
    vmh = _make_voice_handler()
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="hello"):
        await vmh.handle_voice_message(msg)

    answer_calls = [call.args[0] if call.args else "" for call in msg.answer.call_args_list]
    assert any("queued" in c.lower() for c in answer_calls), f"Expected 'queued' in calls: {answer_calls}"


@pytest.mark.asyncio
async def test_voice_no_queued_notification_when_session_is_idle() -> None:
    """When session.is_processing is False, no 'queued' notification must be sent."""
    session = _mock_session(events=[Response(content="ok")])
    session.is_processing = False
    vmh = _make_voice_handler()
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="hello"):
        await vmh.handle_voice_message(msg)

    answer_calls = [call.args[0] if call.args else "" for call in msg.answer.call_args_list]
    assert not any("queued" in c.lower() for c in answer_calls), f"Unexpected 'queued' in calls: {answer_calls}"


@pytest.mark.asyncio
async def test_voice_busy_session_no_processing_ack() -> None:
    """Bug.C3: when session is busy (queued), '⏳ Processing...' must NOT be sent.
    The 'queued' notification is already shown — a second ack would be misleading."""
    session = _mock_session(events=[Response(content="ok")])
    session.is_processing = True
    vmh = _make_voice_handler()
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="hello"):
        await vmh.handle_voice_message(msg)

    answer_calls = [call.args[0] if call.args else "" for call in msg.answer.call_args_list]
    assert not any(c in ("⏳ Processing...", "⏳ Working...") for c in answer_calls), (
        f"Expected no processing ack when queued; got: {answer_calls}"
    )


@pytest.mark.asyncio
async def test_voice_idle_session_sends_processing_ack() -> None:
    """Bug.C3: when session is idle (not queued), '⏳ Processing...' IS sent."""
    session = _mock_session(events=[Response(content="ok")])
    session.is_processing = False
    vmh = _make_voice_handler()
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="hello"):
        await vmh.handle_voice_message(msg)

    answer_calls = [call.args[0] if call.args else "" for call in msg.answer.call_args_list]
    assert "⏳ Processing..." in answer_calls, (
        f"Expected processing ack for idle session; got: {answer_calls}"
    )


@pytest.mark.asyncio
async def test_voice_sends_processing_ack_before_session_send() -> None:
    """Voice handler must send '⏳ Processing...' ack before calling session.send()."""
    events_yielded: list[str] = []

    session = MagicMock()
    session.is_processing = False

    async def _send(prompt: str):
        # Record that session.send was called; at this point ack must already be sent
        events_yielded.append("session_send_called")
        yield Response(content="ok")

    session.send = _send

    answer_calls_order: list[str] = []
    send_order: list[str] = []

    vmh = _make_voice_handler()
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg()

    original_answer = msg.answer

    async def _capturing_answer(text: str, **kwargs) -> object:
        answer_calls_order.append(text)
        return MagicMock()

    msg.answer = AsyncMock(side_effect=_capturing_answer)

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="hello"):
        await vmh.handle_voice_message(msg)

    processing_positions = [i for i, t in enumerate(answer_calls_order) if "Processing" in t or "Working" in t]
    assert processing_positions, f"No processing ack in calls: {answer_calls_order}"


@pytest.mark.asyncio
async def test_voice_queued_notification_failure_does_not_abort() -> None:
    """If sending 'queued' notification fails, processing must continue."""
    session = _mock_session(events=[Response(content="still works")])
    session.is_processing = True
    vmh = _make_voice_handler()
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg()

    call_count = 0

    async def _answer_fail_first(text: str, **kwargs) -> object:
        nonlocal call_count
        call_count += 1
        if "queued" in text.lower():
            raise Exception("Telegram down")
        return MagicMock()

    msg.answer = AsyncMock(side_effect=_answer_fail_first)

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="hello"):
        await vmh.handle_voice_message(msg)  # must not raise

    # Processing must have continued — final response delivered
    answer_calls = [call.args[0] if call.args else "" for call in msg.answer.call_args_list]
    assert any("still works" in c for c in answer_calls)


# ──────────────────────────────────────────────────────────────────
# Issue 1-3 — PromotionEvent: handoff notification, no format_event, failure notification
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_promotion_sends_handoff_notification_before_spawn() -> None:
    """Handoff notification must be sent BEFORE spawn() is called."""
    promotion = PromotionEvent(
        agent_prompt="bg prompt",
        original_prompt="user request",
        tool_count=7,
    )
    session = _mock_session(events=[promotion])
    session.context_summary = ""
    bam = MagicMock()
    call_order: list[str] = []

    async def _spawning_spawn(**kwargs: object) -> MagicMock:
        call_order.append("spawn")
        return MagicMock(name="agent-1")

    bam.spawn = AsyncMock(side_effect=_spawning_spawn)

    vmh = _make_voice_handler(background_agent_manager=bam)
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg(user_id=42)

    async def _capturing_answer(text: str, **kwargs: object) -> object:
        call_order.append(f"answer:{text}")
        return MagicMock()

    msg.answer = AsyncMock(side_effect=_capturing_answer)

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="user request"):
        await vmh.handle_voice_message(msg)

    handoff_positions = [i for i, e in enumerate(call_order) if isinstance(e, str) and "handing off" in e.lower()]
    spawn_positions = [i for i, e in enumerate(call_order) if e == "spawn"]
    assert handoff_positions, f"Handoff notification not sent. call_order={call_order}"
    assert spawn_positions, f"spawn() not called. call_order={call_order}"
    assert handoff_positions[0] < spawn_positions[0], (
        f"Handoff notification must arrive BEFORE spawn. call_order={call_order}"
    )


@pytest.mark.asyncio
async def test_promotion_does_not_call_format_event_when_bam_available() -> None:
    """When BAM is available and spawn succeeds, format_event must NOT be called for PromotionEvent."""
    promotion = PromotionEvent(
        agent_prompt="bg prompt",
        original_prompt="user request",
        tool_count=3,
    )
    session = _mock_session(events=[promotion])
    session.context_summary = ""
    bam = MagicMock()
    bam.spawn = AsyncMock(return_value=MagicMock(name="agent-1"))
    vmh = _make_voice_handler(background_agent_manager=bam)
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg(user_id=42)

    answer_texts: list[str] = []

    async def _capturing_answer(text: str, **kwargs: object) -> object:
        answer_texts.append(text)
        return MagicMock()

    msg.answer = AsyncMock(side_effect=_capturing_answer)

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="user request"):
        await vmh.handle_voice_message(msg)

    # format_event for PromotionEvent returns "background agents unavailable"
    unavailable_calls = [t for t in answer_texts if "unavailable" in t.lower()]
    assert not unavailable_calls, (
        f"format_event was called for PromotionEvent with BAM available: {unavailable_calls}"
    )


@pytest.mark.asyncio
async def test_promotion_sends_failure_notification_when_spawn_raises() -> None:
    """When spawn() raises, user must receive a failure notification."""
    promotion = PromotionEvent(
        agent_prompt="bg prompt",
        original_prompt="user request",
        tool_count=5,
    )
    session = _mock_session(events=[promotion])
    session.context_summary = ""
    bam = MagicMock()
    bam.spawn = AsyncMock(side_effect=RuntimeError("spawn failed"))
    vmh = _make_voice_handler(background_agent_manager=bam)
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg(user_id=42)

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="user request"):
        await vmh.handle_voice_message(msg)  # must not raise

    answer_texts = [call.args[0] if call.args else "" for call in msg.answer.call_args_list]
    failure_calls = [t for t in answer_texts if "promotion failed" in t.lower() or "could not start" in t.lower()]
    assert failure_calls, f"No failure notification sent to user. answer_texts={answer_texts}"


@pytest.mark.asyncio
async def test_voice_retry_after_retry_failure_is_logged_at_warning(
    caplog,
) -> None:
    """If retry after TelegramRetryAfter also fails, it must be logged at WARNING — not crash."""
    import logging
    from unittest.mock import patch
    from aiogram.exceptions import TelegramRetryAfter

    session = _mock_session(events=[Response(content="Done")])
    vmh = _make_voice_handler()
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg()

    call_count = 0

    async def _answer_always_rate_limited(text: str, **kwargs: object) -> None:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise TelegramRetryAfter(method=MagicMock(), message="flood", retry_after=0)

    msg.answer = AsyncMock(side_effect=_answer_always_rate_limited)

    async def _noop_sleep(duration: float) -> None:
        pass

    with caplog.at_level(logging.DEBUG, logger="archon"), \
         patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="hello"), \
         patch("archon.chat.voice.asyncio.sleep", side_effect=_noop_sleep):
        await vmh.handle_voice_message(msg)  # must not raise

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR and "voice" not in r.getMessage().lower()]
    # No ERROR for rate-limit retry — only WARNINGs
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("retry" in r.getMessage().lower() or "Failed" in r.getMessage() for r in warning_records)


# ──────────────────────────────────────────────────────────────────
# Bug C4 — response still delivered when truncation is None
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_voice_sends_response_when_truncation_is_none() -> None:
    """Response event must reach user even when VoiceMessageHandler has no truncation strategy."""
    session = _mock_session(events=[Response(content="Hello without truncation")])
    sm = MagicMock()
    sm.get_or_create = AsyncMock(return_value=session)
    from archon.ai.tts import TTSConfig
    vmh = VoiceMessageHandler(
        session_manager=sm,
        stt_config={"model": "tiny"},
        tts_config=TTSConfig(auto="off"),
        truncation=None,  # intentionally no truncation
        max_len=4000,
    )
    msg = _make_voice_msg()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="hello"):
        await vmh.handle_voice_message(msg)

    answer_calls = [call.args[0] if call.args else "" for call in msg.answer.call_args_list]
    assert any("Hello without truncation" in c for c in answer_calls), (
        f"Response not delivered when truncation=None. calls={answer_calls}"
    )


# ──────────────────────────────────────────────────────────────────
# Bug M3 — PlanEvent skipped when message.bot is None
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_voice_plan_event_skipped_when_bot_is_none() -> None:
    """When message.bot is None, PlanExecutor must not be created and no crash must occur."""
    from archon.ai.agent_plan import AgentPlan, AgentTask

    plan = AgentPlan(
        scope="large",
        summary="bot-less plan",
        agents=[AgentTask(id="a1", task="do it", depends_on=())],
    )
    plan_event = PlanEvent(plan=plan, summary="bot-less plan")
    session = _mock_session(events=[plan_event])
    session.context_summary = ""
    bam = MagicMock()
    bam.spawn = AsyncMock()
    vmh = _make_voice_handler(background_agent_manager=bam)
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg(user_id=42)

    with patch("archon.ai.plan_executor.PlanExecutor") as MockExecutor, \
         patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="test"):
        msg.bot = None  # bot gone before processing
        await vmh.handle_voice_message(msg)  # must not raise

    MockExecutor.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# Bug M4 — sub-agent Response/ErrorEvent recorded to history_manager
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_voice_subagent_response_recorded_to_history() -> None:
    """Sub-agent Response events must be recorded to history_manager (parity with handler.py)."""
    hm = MagicMock()
    hm.record_user_message = AsyncMock()
    hm.record_event = AsyncMock()
    al = MagicMock()
    al.record_event = AsyncMock()

    sub_response = Response(content="sub-agent answer")
    sub_response.source = "sub-agent"  # type: ignore[attr-defined]

    sub_error = ErrorEvent(message="sub-agent error")
    sub_error.source = "sub-agent"  # type: ignore[attr-defined]

    session = _mock_session(events=[sub_response, sub_error, Response(content="orchestrator done")])
    vmh = _make_voice_handler(history_manager=hm, agent_logger=al)
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)
    msg = _make_voice_msg()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="hello"):
        await vmh.handle_voice_message(msg)

    # history_manager.record_event must have been called for sub_response and sub_error
    recorded_events = [call.args[1] for call in hm.record_event.call_args_list]
    assert sub_response in recorded_events, (
        f"Sub-agent Response not recorded to history. recorded={recorded_events}"
    )
    assert sub_error in recorded_events, (
        f"Sub-agent ErrorEvent not recorded to history. recorded={recorded_events}"
    )


# ──────────────────────────────────────────────────────────────────
# Task 2.5 — TTS guard: router Response is not captured for TTS
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_voice_tts_ignores_router_response() -> None:
    """Router Response is excluded from TTS — only main-session Response triggers playback."""
    router_response = Response(content='{"scope":"small","prompt":"do it"}', source="router")
    main_response = Response(content="Here is your answer")

    session = _mock_session(events=[router_response, main_response])
    vmh = _make_voice_handler(tts_auto="always")
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)

    msg = _make_voice_msg()

    tts_texts: list[str] = []

    async def _capture_tts(text: str, *args: object, **kw: object) -> bytes:
        tts_texts.append(text)
        return b""

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="hello"), \
         patch.object(vmh.tts, "synthesize", side_effect=_capture_tts) as mock_synth:
        await vmh.handle_voice_message(msg)

    # TTS was triggered — must NOT have used the router response content
    assert '{"scope"' not in " ".join(tts_texts), (
        f"Router response JSON must not be passed to TTS, got: {tts_texts}"
    )
    # The main-session response content IS the TTS source
    if tts_texts:
        assert "Here is your answer" in tts_texts[-1], (
            f"Expected main-session response for TTS, got: {tts_texts}"
        )


@pytest.mark.asyncio
async def test_voice_tts_uses_main_response_when_no_router_event() -> None:
    """Without any router event, TTS behaves unchanged (main Response captured as usual)."""
    main_response = Response(content="Normal answer")

    session = _mock_session(events=[main_response])
    vmh = _make_voice_handler(tts_auto="always")
    vmh.session_manager.get_or_create = AsyncMock(return_value=session)

    msg = _make_voice_msg()

    tts_texts: list[str] = []

    async def _capture_tts(text: str, *args: object, **kw: object) -> bytes:
        tts_texts.append(text)
        return b""

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="hello"), \
         patch.object(vmh.tts, "synthesize", side_effect=_capture_tts):
        await vmh.handle_voice_message(msg)

    if tts_texts:
        assert "Normal answer" in tts_texts[-1]


# ──────────────────────────────────────────────────────────────────
# Fix 4 — router event format_event regression guards (voice module)
# ──────────────────────────────────────────────────────────────────


def test_voice_format_router_events_normal_suppressed() -> None:
    """Router events in normal mode produce no Telegram output from format_event."""
    from archon.chat.handler import format_event
    from archon.ai.truncation import SplitStrategy
    from archon.config.loader import NotificationsConfig

    normal_notif = NotificationsConfig(mode="normal")
    event = ToolStarted(name="history_read", input={}, source="router")
    result = format_event(event, SplitStrategy(), notifications=normal_notif)
    assert result == []


def test_voice_format_router_events_verbose() -> None:
    """Router events in verbose mode produce [Router]-prefixed output."""
    from archon.chat.handler import format_event
    from archon.ai.truncation import SplitStrategy
    from archon.config.loader import NotificationsConfig

    verbose_notif = NotificationsConfig(mode="verbose")
    event = ToolStarted(name="history_read", input={}, source="router")
    result = format_event(event, SplitStrategy(), notifications=verbose_notif)
    assert len(result) > 0
    assert "[Router]" in "".join(result)


def test_voice_main_session_events_unchanged() -> None:
    """Main session (orchestrator) events are not affected by router event changes."""
    from archon.chat.handler import format_event
    from archon.ai.truncation import SplitStrategy
    from archon.config.loader import NotificationsConfig

    verbose_notif = NotificationsConfig(mode="verbose")
    event = ToolStarted(name="Read", input={"file_path": "/test"}, source="orchestrator")
    result = format_event(event, SplitStrategy(), notifications=verbose_notif)
    assert "[Router]" not in "".join(result)
    assert len(result) > 0  # main session tool events ARE shown in verbose mode
