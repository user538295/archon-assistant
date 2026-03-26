"""Tests for MacRuntime — T17 (inherited signals), T18 (restart), T19 (find_binary)."""
from __future__ import annotations

import os
import signal
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from archon.platform.macos.runtime import MacRuntime
from tests.platform.conftest import mock_loop


async def _shutdown_stub() -> None:
    pass


# ── T17: inherited register_signals ─────────────────────────────────


@pytest.mark.macos
class TestMacRuntimeSignals:
    """Verify that MacRuntime inherits register_signals correctly."""

    def test_register_signals_adds_sigterm_and_sigint(self) -> None:
        rt = MacRuntime()
        loop = mock_loop()
        rt.register_signals(loop, _shutdown_stub)

        sigs = [call.args[0] for call in loop.add_signal_handler.call_args_list]
        assert signal.SIGTERM in sigs
        assert signal.SIGINT in sigs

    def test_first_signal_creates_task(self) -> None:
        rt = MacRuntime()
        loop = mock_loop()
        rt.register_signals(loop, _shutdown_stub)

        # Extract the handler registered for SIGTERM and call it.
        handler = loop.add_signal_handler.call_args_list[0].args[1]
        handler()

        loop.create_task.assert_called_once()

    def test_second_signal_is_ignored(self) -> None:
        rt = MacRuntime()
        loop = mock_loop()
        rt.register_signals(loop, _shutdown_stub)

        handler = loop.add_signal_handler.call_args_list[0].args[1]
        handler()  # first
        handler()  # second — idempotent guard

        loop.create_task.assert_called_once()


# ── T18: restart_process ─────────────────────────────────────────────


@pytest.mark.macos
class TestMacRuntimeRestart:
    """MacRuntime.restart_process delegates to os.execv."""

    def test_execv_called_with_correct_args(self) -> None:
        rt = MacRuntime()
        with patch("archon.platform.macos.runtime.os.execv") as mock_execv:
            rt.restart_process()
            mock_execv.assert_called_once_with(
                sys.executable, [sys.executable] + sys.argv
            )

    def test_oserror_propagates(self) -> None:
        rt = MacRuntime()
        with patch(
            "archon.platform.macos.runtime.os.execv",
            side_effect=OSError("exec failed"),
        ):
            with pytest.raises(OSError, match="exec failed"):
                rt.restart_process()


# ── T19: find_binary ─────────────────────────────────────────────────


@pytest.mark.macos
class TestMacRuntimeFindBinary:
    """MacRuntime.find_binary — which → homebrew → /usr/local → extra."""

    def test_found_via_which(self) -> None:
        rt = MacRuntime()
        with patch(
            "archon.platform.macos.runtime.shutil.which",
            return_value="/usr/bin/ffmpeg",
        ):
            result = rt.find_binary("ffmpeg")

        assert result == Path("/usr/bin/ffmpeg")

    def test_found_via_homebrew(self) -> None:
        rt = MacRuntime()
        brew_path = Path("/opt/homebrew/bin/mytool")

        with (
            patch("archon.platform.macos.runtime.shutil.which", return_value=None),
            patch.object(
                Path, "is_file", autospec=True,
                side_effect=lambda p: p == brew_path,
            ),
            patch("archon.platform.macos.runtime.os.access", return_value=True),
        ):
            result = rt.find_binary("mytool")

        assert result == brew_path

    def test_not_found_anywhere(self) -> None:
        rt = MacRuntime()
        with (
            patch("archon.platform.macos.runtime.shutil.which", return_value=None),
            patch.object(Path, "is_file", autospec=True, return_value=False),
        ):
            result = rt.find_binary("nonexistent")

        assert result is None

    def test_extra_paths_fallback(self) -> None:
        rt = MacRuntime()
        custom = Path("/custom/bin/tool")
        with (
            patch("archon.platform.macos.runtime.shutil.which", return_value=None),
            patch.object(
                Path, "is_file", autospec=True,
                side_effect=lambda p: p == custom,
            ),
            patch("archon.platform.macos.runtime.os.access", return_value=True),
        ):
            result = rt.find_binary("tool", extra_paths=[custom])

        assert result == custom

    def test_search_order_prefers_which(self) -> None:
        """shutil.which result takes priority over hardcoded paths."""
        rt = MacRuntime()
        with (
            patch(
                "archon.platform.macos.runtime.shutil.which",
                return_value="/usr/bin/tool",
            ),
            patch.object(Path, "is_file", autospec=True, return_value=True),
        ):
            result = rt.find_binary("tool")

        # Should return the which result, not the homebrew path.
        assert result == Path("/usr/bin/tool")

    def test_empty_name_returns_none(self) -> None:
        rt = MacRuntime()
        assert rt.find_binary("") is None

    def test_rejects_directory(self) -> None:
        """A directory matching the name must not be returned."""
        rt = MacRuntime()
        with (
            patch("archon.platform.macos.runtime.shutil.which", return_value=None),
            patch.object(Path, "is_file", autospec=True, return_value=False),
        ):
            result = rt.find_binary("somedir")
        assert result is None

    def test_rejects_non_executable(self) -> None:
        """A file without the executable bit must not be returned."""
        rt = MacRuntime()
        brew_path = Path("/opt/homebrew/bin/noexec")
        with (
            patch("archon.platform.macos.runtime.shutil.which", return_value=None),
            patch.object(
                Path, "is_file", autospec=True,
                side_effect=lambda p: p == brew_path,
            ),
            patch("archon.platform.macos.runtime.os.access", return_value=False),
        ):
            result = rt.find_binary("noexec")
        assert result is None

    def test_extra_path_directory_rejected(self) -> None:
        """Extra path that is a directory must not be returned."""
        rt = MacRuntime()
        dir_path = Path("/custom/bin/tool")
        with (
            patch("archon.platform.macos.runtime.shutil.which", return_value=None),
            patch.object(Path, "is_file", autospec=True, return_value=False),
        ):
            result = rt.find_binary("tool", extra_paths=[dir_path])
        assert result is None


# ── detect_gpu_type ───────────────────────────────────────────────────


@pytest.mark.macos
class TestMacRuntimeDetectGpuType:
    """MacRuntime.detect_gpu_type returns apple_silicon on arm64, none on x86_64."""

    def test_detect_gpu_type_returns_apple_silicon_on_arm64(self) -> None:
        rt = MacRuntime()
        with patch("archon.platform.macos.runtime.platform.machine", return_value="arm64"):
            result = rt.detect_gpu_type()
        assert result == "apple_silicon"

    def test_detect_gpu_type_returns_none_on_intel_mac_via_mac_runtime(self) -> None:
        rt = MacRuntime()
        with patch("archon.platform.macos.runtime.platform.machine", return_value="x86_64"):
            result = rt.detect_gpu_type()
        assert result == "none"
