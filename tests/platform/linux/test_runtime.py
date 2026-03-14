"""Tests for LinuxRuntime — T28 (inherited signals), T29 (restart), T30 (find_binary)."""
from __future__ import annotations

import os
import signal
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from archon.platform.linux.runtime import LinuxRuntime
from tests.platform.conftest import mock_loop


async def _shutdown_stub() -> None:
    pass


# ── T28: inherited register_signals ─────────────────────────────────


class TestLinuxRuntimeSignals:
    """Verify that LinuxRuntime inherits register_signals correctly."""

    def test_register_signals_adds_sigterm_and_sigint(self) -> None:
        rt = LinuxRuntime()
        loop = mock_loop()
        rt.register_signals(loop, _shutdown_stub)

        sigs = [call.args[0] for call in loop.add_signal_handler.call_args_list]
        assert signal.SIGTERM in sigs
        assert signal.SIGINT in sigs

    def test_first_signal_creates_task(self) -> None:
        rt = LinuxRuntime()
        loop = mock_loop()
        rt.register_signals(loop, _shutdown_stub)

        handler = loop.add_signal_handler.call_args_list[0].args[1]
        handler()

        loop.create_task.assert_called_once()

    def test_second_signal_is_ignored(self) -> None:
        rt = LinuxRuntime()
        loop = mock_loop()
        rt.register_signals(loop, _shutdown_stub)

        handler = loop.add_signal_handler.call_args_list[0].args[1]
        handler()  # first
        handler()  # second — idempotent guard

        loop.create_task.assert_called_once()


# ── T29: restart_process ─────────────────────────────────────────────


class TestLinuxRuntimeRestart:
    """LinuxRuntime.restart_process delegates to os.execv."""

    def test_execv_called_with_correct_args(self) -> None:
        rt = LinuxRuntime()
        with patch("archon.platform.linux.runtime.os.execv") as mock_execv:
            rt.restart_process()
            mock_execv.assert_called_once_with(
                sys.executable, [sys.executable] + sys.argv
            )

    def test_oserror_propagates(self) -> None:
        rt = LinuxRuntime()
        with patch(
            "archon.platform.linux.runtime.os.execv",
            side_effect=OSError("exec failed"),
        ):
            with pytest.raises(OSError, match="exec failed"):
                rt.restart_process()


# ── T30: find_binary ─────────────────────────────────────────────────


class TestLinuxRuntimeFindBinary:
    """LinuxRuntime.find_binary — which → ~/.local/bin → /usr/local/bin → extra."""

    def test_found_via_which(self) -> None:
        rt = LinuxRuntime()
        with patch(
            "archon.platform.linux.runtime.shutil.which",
            return_value="/usr/bin/ffmpeg",
        ):
            result = rt.find_binary("ffmpeg")

        assert result == Path("/usr/bin/ffmpeg")

    def test_found_via_local_bin(self) -> None:
        rt = LinuxRuntime()
        home = Path.home()
        local_path = home / ".local" / "bin" / "mytool"

        with (
            patch("archon.platform.linux.runtime.shutil.which", return_value=None),
            patch.object(
                Path, "is_file", autospec=True,
                side_effect=lambda p: p == local_path,
            ),
            patch("archon.platform.linux.runtime.os.access", return_value=True),
        ):
            result = rt.find_binary("mytool")

        assert result == local_path

    def test_not_found_anywhere(self) -> None:
        rt = LinuxRuntime()
        with (
            patch("archon.platform.linux.runtime.shutil.which", return_value=None),
            patch.object(Path, "is_file", autospec=True, return_value=False),
        ):
            result = rt.find_binary("nonexistent")

        assert result is None

    def test_extra_paths_fallback(self) -> None:
        rt = LinuxRuntime()
        custom = Path("/custom/bin/tool")
        with (
            patch("archon.platform.linux.runtime.shutil.which", return_value=None),
            patch.object(
                Path, "is_file", autospec=True,
                side_effect=lambda p: p == custom,
            ),
            patch("archon.platform.linux.runtime.os.access", return_value=True),
        ):
            result = rt.find_binary("tool", extra_paths=[custom])

        assert result == custom

    def test_search_order_prefers_which(self) -> None:
        """shutil.which result takes priority over hardcoded paths."""
        rt = LinuxRuntime()
        with (
            patch(
                "archon.platform.linux.runtime.shutil.which",
                return_value="/usr/bin/tool",
            ),
            patch.object(Path, "is_file", autospec=True, return_value=True),
        ):
            result = rt.find_binary("tool")

        assert result == Path("/usr/bin/tool")

    def test_empty_name_returns_none(self) -> None:
        rt = LinuxRuntime()
        assert rt.find_binary("") is None

    def test_rejects_directory(self) -> None:
        """A directory matching the name must not be returned."""
        rt = LinuxRuntime()
        with (
            patch("archon.platform.linux.runtime.shutil.which", return_value=None),
            patch.object(Path, "is_file", autospec=True, return_value=False),
        ):
            result = rt.find_binary("somedir")
        assert result is None

    def test_rejects_non_executable(self) -> None:
        """A file without the executable bit must not be returned."""
        rt = LinuxRuntime()
        with (
            patch("archon.platform.linux.runtime.shutil.which", return_value=None),
            patch.object(Path, "is_file", autospec=True, return_value=True),
            patch("archon.platform.linux.runtime.os.access", return_value=False),
        ):
            result = rt.find_binary("noexec")
        assert result is None

    def test_home_unset_skips_local_bin(self) -> None:
        """When HOME is unset (containers), ~/.local/bin is skipped gracefully."""
        rt = LinuxRuntime()
        with (
            patch("archon.platform.linux.runtime.shutil.which", return_value=None),
            patch(
                "archon.platform.linux.runtime.Path.home",
                side_effect=RuntimeError("HOME unset"),
            ),
            patch.object(Path, "is_file", autospec=True, return_value=False),
        ):
            result = rt.find_binary("tool")
        assert result is None

    def test_extra_path_directory_rejected(self) -> None:
        """Extra path that is a directory must not be returned."""
        rt = LinuxRuntime()
        dir_path = Path("/custom/bin/tool")
        with (
            patch("archon.platform.linux.runtime.shutil.which", return_value=None),
            patch.object(Path, "is_file", autospec=True, return_value=False),
        ):
            result = rt.find_binary("tool", extra_paths=[dir_path])
        assert result is None
