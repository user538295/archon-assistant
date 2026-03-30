"""Tests for archon/cli/voice_cmd.py — run_voice() CLI dispatcher (Task 1.6)."""
from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

from archon.cli.voice_cmd import _CONFIG_PATH, run_voice


def _args(**kwargs):
    ns = argparse.Namespace(voice_command=None)
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------


def test_install_dispatches_to_voice_installer():
    args = _args(voice_command="install", non_interactive=False)
    with patch("archon.cli.voice_cmd.VoiceInstaller") as MockInstaller:
        instance = MockInstaller.return_value
        instance.run.return_value = 0
        rc = run_voice(args)
    instance.run.assert_called_once_with(non_interactive=False)
    assert rc == 0


def test_install_non_interactive_flag():
    args = _args(voice_command="install", non_interactive=True)
    with patch("archon.cli.voice_cmd.VoiceInstaller") as MockInstaller:
        instance = MockInstaller.return_value
        instance.run.return_value = 0
        run_voice(args)
    instance.run.assert_called_once_with(non_interactive=True)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_all_present(capsys):
    args = _args(voice_command="status")
    with patch("archon.cli.voice_cmd.VoiceInstaller") as MockInstaller:
        instance = MockInstaller.return_value
        instance.status.return_value = {
            "whisper_installed": True,
            "edge_tts_installed": True,
            "ffmpeg_found": True,
        }
        rc = run_voice(args)
    out = capsys.readouterr().out
    assert "installed" in out
    assert "found" in out
    assert rc == 0


def test_status_partial(capsys):
    args = _args(voice_command="status")
    with patch("archon.cli.voice_cmd.VoiceInstaller") as MockInstaller:
        instance = MockInstaller.return_value
        instance.status.return_value = {
            "whisper_installed": False,
            "edge_tts_installed": True,
            "ffmpeg_found": True,
        }
        run_voice(args)
    out = capsys.readouterr().out
    assert "not installed" in out


# ---------------------------------------------------------------------------
# enable / disable
# ---------------------------------------------------------------------------


def test_enable_calls_set_config_value():
    args = _args(voice_command="enable")
    with patch("archon.config.config_rw.set_config_value") as mock_set:
        run_voice(args)
    mock_set.assert_called_once_with("voice.enabled", "true", _CONFIG_PATH)


def test_disable_calls_set_config_value():
    args = _args(voice_command="disable")
    with patch("archon.config.config_rw.set_config_value") as mock_set:
        run_voice(args)
    mock_set.assert_called_once_with("voice.enabled", "false", _CONFIG_PATH)


def test_enable_prints_restart_hint(capsys):
    args = _args(voice_command="enable")
    with patch("archon.config.config_rw.set_config_value"):
        run_voice(args)
    out = capsys.readouterr().out
    assert "restart" in out


def test_disable_prints_restart_hint(capsys):
    args = _args(voice_command="disable")
    with patch("archon.config.config_rw.set_config_value"):
        run_voice(args)
    out = capsys.readouterr().out
    assert "restart" in out


# ---------------------------------------------------------------------------
# edge cases
# ---------------------------------------------------------------------------


def test_no_subcommand_returns_zero():
    args = _args(voice_command=None)
    rc = run_voice(args)
    assert rc == 0


def test_unknown_subcommand_returns_one():
    args = _args(voice_command="bogus")
    rc = run_voice(args)
    assert rc == 1
