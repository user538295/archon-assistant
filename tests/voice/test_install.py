"""Tests for VoiceInstaller check methods and status() — Task 1.2."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from archon.voice.install import VoiceInstaller


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def installer(tmp_path: Path) -> VoiceInstaller:
    config_file = str(tmp_path / "config.toml")
    return VoiceInstaller(config_file=config_file)


# ---------------------------------------------------------------------------
# check_whisper
# ---------------------------------------------------------------------------

def test_check_whisper_importable(installer: VoiceInstaller) -> None:
    mock_module = MagicMock()
    mock_module.load_model = MagicMock()
    with patch("archon.voice.install.importlib.import_module", return_value=mock_module):
        assert installer.check_whisper() is True


def test_check_whisper_missing(installer: VoiceInstaller) -> None:
    with patch("archon.voice.install.importlib.import_module", side_effect=ImportError("no module")):
        assert installer.check_whisper() is False


def test_check_whisper_wrong_package(installer: VoiceInstaller) -> None:
    mock_module = MagicMock(spec=[])  # no attributes
    with patch("archon.voice.install.importlib.import_module", return_value=mock_module):
        assert installer.check_whisper() is False


# ---------------------------------------------------------------------------
# check_ffmpeg
# ---------------------------------------------------------------------------

def test_check_ffmpeg_found(installer: VoiceInstaller) -> None:
    with patch("archon.voice.install.shutil.which", return_value="/usr/bin/ffmpeg"):
        assert installer.check_ffmpeg() is True


def test_check_ffmpeg_missing(installer: VoiceInstaller) -> None:
    with patch("archon.voice.install.shutil.which", return_value=None):
        assert installer.check_ffmpeg() is False


# ---------------------------------------------------------------------------
# check_edge_tts
# ---------------------------------------------------------------------------

def test_check_edge_tts_importable(installer: VoiceInstaller) -> None:
    mock_module = MagicMock()
    with patch("archon.voice.install.importlib.import_module", return_value=mock_module):
        assert installer.check_edge_tts() is True


def test_check_edge_tts_missing(installer: VoiceInstaller) -> None:
    with patch("archon.voice.install.importlib.import_module", side_effect=ImportError("no module")):
        assert installer.check_edge_tts() is False


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def test_status_all_present(installer: VoiceInstaller) -> None:
    with (
        patch.object(installer, "check_whisper", return_value=True),
        patch.object(installer, "check_ffmpeg", return_value=True),
        patch.object(installer, "check_edge_tts", return_value=True),
    ):
        result = installer.status()

    assert result == {
        "whisper_installed": True,
        "ffmpeg_found": True,
        "edge_tts_installed": True,
    }


def test_status_partial(installer: VoiceInstaller) -> None:
    with (
        patch.object(installer, "check_whisper", return_value=False),
        patch.object(installer, "check_ffmpeg", return_value=True),
        patch.object(installer, "check_edge_tts", return_value=True),
    ):
        result = installer.status()

    assert result == {
        "whisper_installed": False,
        "ffmpeg_found": True,
        "edge_tts_installed": True,
    }


# ---------------------------------------------------------------------------
# __init__ config_file default
# ---------------------------------------------------------------------------

def test_default_config_file() -> None:
    installer = VoiceInstaller()
    expected = str(Path.home() / ".archon" / "config.toml")
    assert installer._config_file == expected


def test_custom_config_file(tmp_path: Path) -> None:
    custom = str(tmp_path / "custom.toml")
    installer = VoiceInstaller(config_file=custom)
    assert installer._config_file == custom
