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
# check_torch
# ---------------------------------------------------------------------------

def test_check_torch_installed(installer: VoiceInstaller) -> None:
    with patch("importlib.util.find_spec", return_value=MagicMock()):
        assert installer.check_torch() is True


def test_check_torch_missing(installer: VoiceInstaller) -> None:
    with patch("importlib.util.find_spec", return_value=None):
        assert installer.check_torch() is False


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


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------

def test_run_non_interactive_success(installer: VoiceInstaller) -> None:
    with (
        patch.object(installer, "check_whisper", return_value=True),
        patch.object(installer, "check_ffmpeg", return_value=True),
        patch.object(installer, "configure_stt_model") as mock_configure,
    ):
        rc = installer.run(non_interactive=True)
    assert rc == 0
    mock_configure.assert_called_once_with("medium")


def test_run_non_interactive_uses_medium_model(installer: VoiceInstaller) -> None:
    with (
        patch.object(installer, "check_whisper", return_value=True),
        patch.object(installer, "check_ffmpeg", return_value=True),
        patch.object(installer, "configure_stt_model") as mock_configure,
    ):
        installer.run(non_interactive=True)
    mock_configure.assert_called_once_with("medium")


def test_run_user_declines(installer: VoiceInstaller) -> None:
    with (
        patch("builtins.input", return_value="n"),
        patch.object(installer, "install_deps") as mock_install,
        patch.object(installer, "check_whisper", return_value=False),
        patch.object(installer, "check_ffmpeg", return_value=True),
        patch.object(installer, "configure_stt_model"),
    ):
        rc = installer.run(non_interactive=False)
    assert rc == 1
    mock_install.assert_not_called()


def test_run_user_accepts(installer: VoiceInstaller) -> None:
    with (
        patch("builtins.input", side_effect=["y", ""]),
        patch.object(installer, "check_whisper", return_value=True),
        patch.object(installer, "check_ffmpeg", return_value=True),
        patch.object(installer, "configure_stt_model") as mock_configure,
    ):
        rc = installer.run(non_interactive=False)
    assert rc == 0
    mock_configure.assert_called_once_with("medium")


def test_run_installs_when_whisper_missing(installer: VoiceInstaller) -> None:
    with (
        patch.object(installer, "check_whisper", return_value=False),
        patch.object(installer, "check_ffmpeg", return_value=True),
        patch.object(installer, "install_deps") as mock_install,
        patch.object(installer, "configure_stt_model"),
    ):
        installer.run(non_interactive=True)
    mock_install.assert_called_once()


def test_run_skips_install_when_whisper_present(installer: VoiceInstaller) -> None:
    with (
        patch.object(installer, "check_whisper", return_value=True),
        patch.object(installer, "check_ffmpeg", return_value=True),
        patch.object(installer, "install_deps") as mock_install,
        patch.object(installer, "configure_stt_model"),
    ):
        installer.run(non_interactive=True)
    mock_install.assert_not_called()


def test_run_ffmpeg_missing_still_returns_zero(installer: VoiceInstaller) -> None:
    with (
        patch.object(installer, "check_whisper", return_value=True),
        patch.object(installer, "check_ffmpeg", return_value=False),
        patch.object(installer, "configure_stt_model"),
    ):
        rc = installer.run(non_interactive=True)
    assert rc == 0


def test_run_install_deps_failure_returns_one(installer: VoiceInstaller) -> None:
    import subprocess
    with (
        patch.object(installer, "check_whisper", return_value=False),
        patch.object(installer, "check_ffmpeg", return_value=True),
        patch.object(installer, "install_deps", side_effect=subprocess.CalledProcessError(1, "uv")),
        patch.object(installer, "configure_stt_model"),
    ):
        rc = installer.run(non_interactive=True)
    assert rc == 1


def test_run_install_deps_file_not_found_returns_one(installer: VoiceInstaller) -> None:
    with (
        patch.object(installer, "check_whisper", return_value=False),
        patch.object(installer, "check_ffmpeg", return_value=True),
        patch.object(installer, "install_deps", side_effect=FileNotFoundError("uv not found")),
        patch.object(installer, "configure_stt_model"),
    ):
        rc = installer.run(non_interactive=True)
    assert rc == 1


def test_run_interactive_model_tiny(installer: VoiceInstaller) -> None:
    with (
        patch("builtins.input", side_effect=["y", "tiny"]),
        patch.object(installer, "check_whisper", return_value=True),
        patch.object(installer, "check_ffmpeg", return_value=True),
        patch.object(installer, "configure_stt_model") as mock_configure,
    ):
        installer.run(non_interactive=False)
    mock_configure.assert_called_once_with("tiny")


def test_run_interactive_model_invalid_falls_back_to_medium(installer: VoiceInstaller) -> None:
    with (
        patch("builtins.input", side_effect=["y", "huge"]),
        patch.object(installer, "check_whisper", return_value=True),
        patch.object(installer, "check_ffmpeg", return_value=True),
        patch.object(installer, "configure_stt_model") as mock_configure,
    ):
        installer.run(non_interactive=False)
    mock_configure.assert_called_once_with("medium")


def test_run_interactive_model_empty_falls_back_to_medium(installer: VoiceInstaller) -> None:
    with (
        patch("builtins.input", side_effect=["y", ""]),
        patch.object(installer, "check_whisper", return_value=True),
        patch.object(installer, "check_ffmpeg", return_value=True),
        patch.object(installer, "configure_stt_model") as mock_configure,
    ):
        installer.run(non_interactive=False)
    mock_configure.assert_called_once_with("medium")


# ---------------------------------------------------------------------------
# Console integration tests (Task 2.2)
# ---------------------------------------------------------------------------

def test_voice_run_non_interactive_suppresses_enable_hint(tmp_path: Path) -> None:
    console = MagicMock()
    installer = VoiceInstaller(config_file=str(tmp_path / "config.toml"), console=console)
    with (
        patch.object(installer, "check_whisper", return_value=True),
        patch.object(installer, "check_ffmpeg", return_value=True),
        patch.object(installer, "configure_stt_model"),
    ):
        installer.run(non_interactive=True)
    enable_hint_calls = [
        call for call in console.success.call_args_list
        if "Enable with" in str(call)
    ]
    assert enable_hint_calls == []


def test_voice_run_interactive_shows_enable_hint(tmp_path: Path) -> None:
    console = MagicMock()
    installer = VoiceInstaller(config_file=str(tmp_path / "config.toml"), console=console)
    with (
        patch("builtins.input", side_effect=["y", ""]),
        patch.object(installer, "check_whisper", return_value=True),
        patch.object(installer, "check_ffmpeg", return_value=True),
        patch.object(installer, "configure_stt_model"),
    ):
        installer.run(non_interactive=False)
    enable_hint_calls = [
        call for call in console.success.call_args_list
        if "archon voice enable" in str(call)
    ]
    assert len(enable_hint_calls) >= 1


def test_voice_run_whisper_missing_torch_absent_shows_download_message(tmp_path: Path) -> None:
    console = MagicMock()
    installer = VoiceInstaller(config_file=str(tmp_path / "config.toml"), console=console)
    with (
        patch.object(installer, "check_whisper", return_value=False),
        patch.object(installer, "check_torch", return_value=False),
        patch.object(installer, "install_deps"),
        patch.object(installer, "check_ffmpeg", return_value=True),
        patch.object(installer, "configure_stt_model"),
    ):
        installer.run(non_interactive=True)
    download_msg_calls = [
        call for call in console.info.call_args_list
        if "~2 GB" in str(call)
    ]
    assert len(download_msg_calls) >= 1


def test_voice_run_whisper_missing_torch_present_shows_no_download_message(tmp_path: Path) -> None:
    console = MagicMock()
    installer = VoiceInstaller(config_file=str(tmp_path / "config.toml"), console=console)
    with (
        patch.object(installer, "check_whisper", return_value=False),
        patch.object(installer, "check_torch", return_value=True),
        patch.object(installer, "install_deps"),
        patch.object(installer, "check_ffmpeg", return_value=True),
        patch.object(installer, "configure_stt_model"),
    ):
        installer.run(non_interactive=True)
    no_download_calls = [
        call for call in console.info.call_args_list
        if "no large download needed" in str(call)
    ]
    assert len(no_download_calls) >= 1
