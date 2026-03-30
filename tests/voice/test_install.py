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


# ---------------------------------------------------------------------------
# install_deps
# ---------------------------------------------------------------------------

def test_install_deps_calls_uv(installer: VoiceInstaller) -> None:
    import sys
    with patch("archon.voice.install.subprocess.run") as mock_run:
        installer.install_deps()
    mock_run.assert_called_once_with(
        ["uv", "pip", "install", "--python", sys.executable, "openai-whisper"],
        check=True,
    )


def test_install_deps_propagates_failure(installer: VoiceInstaller) -> None:
    import subprocess
    with patch(
        "archon.voice.install.subprocess.run",
        side_effect=subprocess.CalledProcessError(1, "uv"),
    ):
        with pytest.raises(subprocess.CalledProcessError):
            installer.install_deps()


def test_install_deps_file_not_found(installer: VoiceInstaller) -> None:
    with patch(
        "archon.voice.install.subprocess.run",
        side_effect=FileNotFoundError("uv not found"),
    ):
        with pytest.raises(FileNotFoundError):
            installer.install_deps()


# ---------------------------------------------------------------------------
# configure_stt_model
# ---------------------------------------------------------------------------

def test_configure_writes_model(tmp_path: Path) -> None:
    import tomllib
    config_file = tmp_path / "config.toml"
    config_file.write_text("[access]\nallowed_user_ids = []\n")
    installer = VoiceInstaller(config_file=str(config_file))
    installer.configure_stt_model("tiny")
    with open(config_file, "rb") as f:
        data = tomllib.load(f)
    assert data["voice"]["stt"]["model"] == "tiny"


def test_configure_creates_voice_section(tmp_path: Path) -> None:
    import tomllib
    config_file = tmp_path / "config.toml"
    config_file.write_text("[access]\nallowed_user_ids = []\n")
    installer = VoiceInstaller(config_file=str(config_file))
    installer.configure_stt_model("medium")
    with open(config_file, "rb") as f:
        data = tomllib.load(f)
    assert "voice" in data
    assert "stt" in data["voice"]
    assert data["voice"]["stt"]["model"] == "medium"


def test_configure_creates_stt_subsection(tmp_path: Path) -> None:
    import tomllib
    config_file = tmp_path / "config.toml"
    config_file.write_text("[voice]\nenabled = false\n")
    installer = VoiceInstaller(config_file=str(config_file))
    installer.configure_stt_model("small")
    with open(config_file, "rb") as f:
        data = tomllib.load(f)
    assert data["voice"]["stt"]["model"] == "small"
    assert data["voice"]["enabled"] is False


def test_configure_preserves_comments(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text("# top comment\n[access]\nallowed_user_ids = []\n")
    installer = VoiceInstaller(config_file=str(config_file))
    installer.configure_stt_model("tiny")
    content = config_file.read_text()
    assert "# top comment" in content


def test_configure_missing_config_logs_warning(tmp_path: Path) -> None:
    import logging
    config_file = tmp_path / "config.toml"
    installer = VoiceInstaller(config_file=str(config_file))
    with patch(
        "archon.config.config_rw.set_config_value",
        side_effect=FileNotFoundError("not found"),
    ):
        with patch.object(logging.getLogger("archon"), "warning") as mock_warn:
            installer.configure_stt_model("medium")
            mock_warn.assert_called_once()
            args = mock_warn.call_args[0]
            assert "not found" in str(args) or str(config_file) in str(args)


def test_configure_overwrites_existing_model(tmp_path: Path) -> None:
    import tomllib
    config_file = tmp_path / "config.toml"
    config_file.write_text("[voice.stt]\nmodel = \"tiny\"\n")
    installer = VoiceInstaller(config_file=str(config_file))
    installer.configure_stt_model("medium")
    with open(config_file, "rb") as f:
        data = tomllib.load(f)
    assert data["voice"]["stt"]["model"] == "medium"
