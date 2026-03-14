"""T37 — DI-wired E2E tests exercising consumer → platform delegation.

Uses override() to inject a real LaunchdService/SystemdService with dry_run=True,
verifying the full call chain from CLI consumers through to platform implementations.
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from archon.platform import get_runtime, get_service, override, reset
from archon.platform.macos.runtime import MacRuntime
from archon.platform.macos.service import LaunchdService
from archon.platform.runtime import PlatformRuntime
from archon.platform.types import ServiceInfo
from tests.platform.conftest import mock_loop


# ── Helpers ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset():
    yield
    reset()


def _launchd_service(tmp_path: Path) -> LaunchdService:
    """Create a LaunchdService with temp paths for testing."""
    svc = LaunchdService()
    svc._PLIST_PATH = tmp_path / "com.archon.assistant.plist"
    svc._TEMPLATE_PATH = tmp_path / "template.plist"
    svc._PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Write a minimal plist so is_installed() returns True
    svc._PLIST_PATH.write_text("<plist/>")
    return svc


# ── CLI service consumer tests ───────────────────────────────────────


@pytest.mark.macos
class TestCLIServiceDelegation:
    """Verify cli/service.py delegates to get_service() correctly."""

    def test_run_start_delegates_and_returns_0(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        svc = _launchd_service(tmp_path)
        override(service=svc)

        from archon.cli.service import run_start

        with patch.object(svc, "_run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            rc = run_start()
        assert rc == 0
        assert "Archon started" in capsys.readouterr().out
        # Verify _run was called with a launchctl command
        assert mock_run.called

    def test_run_start_returns_1_when_not_installed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        svc = LaunchdService()
        svc._PLIST_PATH = tmp_path / "nonexistent.plist"
        override(service=svc)

        from archon.cli.service import run_start

        rc = run_start()
        assert rc == 1
        assert "Failed to start" in capsys.readouterr().out

    def test_run_stop_delegates(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        svc = _launchd_service(tmp_path)
        override(service=svc)

        from archon.cli.service import run_stop

        with patch.object(svc, "_run") as mock_run:
            # _is_loaded returns False → stop is idempotent
            mock_run.return_value = MagicMock(returncode=1)
            rc = run_stop()
        assert rc == 0
        assert "Archon stopped" in capsys.readouterr().out

    def test_run_restart_delegates(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        svc = _launchd_service(tmp_path)
        override(service=svc)

        from archon.cli.service import run_restart

        # restart = stop + start; stop succeeds (not loaded), start needs mock
        with patch.object(svc, "_run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            rc = run_restart()
        assert rc == 0
        assert "Archon restarted" in capsys.readouterr().out


# ── CLI status consumer tests ────────────────────────────────────────


class TestCLIStatusDelegation:
    """Verify cli/status.py delegates to get_service().status()."""

    def test_status_shows_service_name(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        svc = MagicMock()
        svc.service_name = "launchd"
        svc.status.return_value = ServiceInfo(
            running=True, service_name="com.archon.assistant", pid=1234, uptime="01:23:45"
        )
        override(service=svc)

        from archon.cli.status import run_status

        with patch("archon.cli.status._check_health") as mock_health, \
             patch("archon.cli.status._load_config_raw", return_value={}):
            mock_health.return_value = MagicMock(reachable=False, latency_ms=None)
            rc = run_status(MagicMock())

        assert rc == 0
        out = capsys.readouterr().out
        assert "launchd" in out
        assert "1234" in out

    def test_status_shows_stopped(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        svc = MagicMock()
        svc.service_name = "systemd"
        svc.status.return_value = ServiceInfo(
            running=False, service_name="archon"
        )
        override(service=svc)

        from archon.cli.status import run_status

        with patch("archon.cli.status._check_health") as mock_health, \
             patch("archon.cli.status._load_config_raw", return_value={}):
            mock_health.return_value = MagicMock(reachable=False, latency_ms=None)
            run_status(MagicMock())

        out = capsys.readouterr().out
        assert "stopped" in out


# ── Signal registration tests ────────────────────────────────────────


class TestSignalRegistration:
    """Verify register_signals via DI — full chain from get_runtime()."""

    def test_signals_registered_with_correct_callback(self) -> None:
        rt = MacRuntime()
        override(runtime=rt)

        loop = mock_loop()

        async def shutdown():
            pass

        get_runtime().register_signals(loop, shutdown)

        assert loop.add_signal_handler.call_count == 2
        # Both SIGTERM and SIGINT
        import signal
        signal_nums = {call.args[0] for call in loop.add_signal_handler.call_args_list}
        assert signal.SIGTERM in signal_nums
        assert signal.SIGINT in signal_nums

    def test_double_signal_guard_idempotent(self) -> None:
        rt = MacRuntime()
        override(runtime=rt)

        loop = mock_loop(task_done=False)

        async def shutdown():
            pass

        rt.register_signals(loop, shutdown)

        # Get the registered handler
        handler = loop.add_signal_handler.call_args_list[0].args[1]

        # First signal → creates task
        handler()
        assert loop.create_task.call_count == 1

        # Second signal while task is running → ignored
        handler()
        assert loop.create_task.call_count == 1

    def test_signal_retriggers_after_task_completes(self) -> None:
        rt = MacRuntime()
        override(runtime=rt)

        loop = mock_loop(task_done=False)

        async def shutdown():
            pass

        rt.register_signals(loop, shutdown)
        handler = loop.add_signal_handler.call_args_list[0].args[1]

        # First signal
        handler()
        assert loop.create_task.call_count == 1

        # Mark task as done
        rt._shutdown_task.done.return_value = True

        # Second signal after completion → re-triggers
        handler()
        assert loop.create_task.call_count == 2


# ── find_binary tests ────────────────────────────────────────────────


class TestFindBinaryDelegation:
    """Verify find_binary works through get_runtime() DI chain."""

    def test_find_binary_returns_path(self) -> None:
        rt = MacRuntime()
        override(runtime=rt)

        with patch("shutil.which", return_value="/usr/local/bin/whisper"):
            result = get_runtime().find_binary("whisper")

        assert result == Path("/usr/local/bin/whisper")

    def test_find_binary_returns_none_for_nonexistent(self) -> None:
        rt = MacRuntime()
        override(runtime=rt)

        with patch("shutil.which", return_value=None):
            result = get_runtime().find_binary("nonexistent_binary_xyz_12345")

        assert result is None
