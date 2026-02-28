"""Tests for STT module — STTHandler."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.stt import STTHandler


# ──────────────────────────────────────────────────────────────────
# STTHandler._find_whisper_binary
# ──────────────────────────────────────────────────────────────────


def test_find_whisper_falls_back_to_path() -> None:
    """When no standard path exists, whisper_bin falls back to 'whisper'."""
    with patch("archon.ai.stt.Path.exists", return_value=False):
        h = STTHandler()
    assert str(h.whisper_bin) == "whisper"


# ──────────────────────────────────────────────────────────────────
# STTHandler.transcribe — command construction
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transcribe_includes_output_dir(tmp_path: Path) -> None:
    """Whisper command must include --output_dir pointing to the audio file's parent."""
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"\x00" * 100)

    captured_cmd: list[str] = []

    async def fake_exec(*cmd, **_kw):
        captured_cmd.extend(cmd)
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"hello world", b""))
        return proc

    h = STTHandler(model="tiny")
    h.whisper_bin = Path("whisper")

    # Also create the .txt file that whisper would produce
    txt_file = tmp_path / "voice.txt"
    txt_file.write_text("hello world")

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        result = await h.transcribe(audio)

    assert "--output_dir" in captured_cmd
    idx = captured_cmd.index("--output_dir")
    assert captured_cmd[idx + 1] == str(tmp_path)
    assert result == "hello world"


@pytest.mark.asyncio
async def test_transcribe_includes_language_when_set(tmp_path: Path) -> None:
    """When language is set, --language must appear in the command."""
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"\x00" * 100)
    txt_file = tmp_path / "voice.txt"
    txt_file.write_text("szia")

    captured_cmd: list[str] = []

    async def fake_exec(*cmd, **_kw):
        captured_cmd.extend(cmd)
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"szia", b""))
        return proc

    h = STTHandler(model="tiny", language="hu")
    h.whisper_bin = Path("whisper")

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        await h.transcribe(audio)

    assert "--language" in captured_cmd
    idx = captured_cmd.index("--language")
    assert captured_cmd[idx + 1] == "hu"


@pytest.mark.asyncio
async def test_transcribe_file_not_found(tmp_path: Path) -> None:
    """Transcribing a non-existent file must raise FileNotFoundError."""
    h = STTHandler()
    with pytest.raises(FileNotFoundError):
        await h.transcribe(tmp_path / "nonexistent.ogg")


@pytest.mark.asyncio
async def test_transcribe_reads_txt_file(tmp_path: Path) -> None:
    """Whisper writes a .txt file; transcribe must read it and clean up."""
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"\x00" * 100)
    txt_file = tmp_path / "voice.txt"
    txt_file.write_text("  transcribed text  ")

    async def fake_exec(*cmd, **_kw):
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc

    h = STTHandler(model="tiny")
    h.whisper_bin = Path("whisper")

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        result = await h.transcribe(audio)

    assert result == "transcribed text"
    assert not txt_file.exists(), ".txt file must be cleaned up"


@pytest.mark.asyncio
async def test_transcribe_falls_back_to_stdout(tmp_path: Path) -> None:
    """When no .txt file exists, transcribe must use stdout."""
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"\x00" * 100)

    async def fake_exec(*cmd, **_kw):
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"stdout text", b""))
        return proc

    h = STTHandler(model="tiny")
    h.whisper_bin = Path("whisper")

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        result = await h.transcribe(audio)

    assert result == "stdout text"
