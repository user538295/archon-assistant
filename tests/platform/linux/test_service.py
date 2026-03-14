"""Tests for Linux SystemdService (T20–T27)."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from archon.platform import override
from archon.platform.linux.service import SystemdService, _SERVICE_NAME, _UNIT_PATH
from archon.platform.types import ServiceInfo


@pytest.fixture
def svc() -> SystemdService:
    return SystemdService()


# ── T20 — scaffold + is_installed ──


class TestServiceName:
    def test_service_name_is_systemd(self, svc: SystemdService) -> None:
        assert svc.service_name == "systemd"


class TestIsInstalled:
    def test_returns_true_when_unit_exists(
        self, svc: SystemdService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        unit = tmp_path / "archon.service"
        unit.touch()
        monkeypatch.setattr(
            "archon.platform.linux.service._UNIT_PATH", unit
        )
        assert svc.is_installed() is True

    def test_returns_false_when_unit_missing(
        self, svc: SystemdService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        unit = tmp_path / "archon.service"
        monkeypatch.setattr(
            "archon.platform.linux.service._UNIT_PATH", unit
        )
        assert svc.is_installed() is False


# ── T21 — start ──


class TestStart:
    def test_success(self, svc: SystemdService) -> None:
        with patch.object(svc, "_run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            assert svc.start() == 0
            mock_run.assert_called_once_with(
                ["systemctl", "--user", "start", "archon"], dry_run=False
            )

    def test_failure_rc1(self, svc: SystemdService) -> None:
        with patch.object(svc, "_run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="failed"
            )
            assert svc.start() == 1

    def test_failure_logs_stderr(self, svc: SystemdService, caplog: pytest.LogCaptureFixture) -> None:
        with patch.object(svc, "_run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="unit not found"
            )
            with caplog.at_level(logging.WARNING, logger="archon"):
                svc.start()
        assert "unit not found" in caplog.text

    def test_file_not_found(self, svc: SystemdService) -> None:
        with patch.object(svc, "_run", side_effect=FileNotFoundError):
            assert svc.start() == 1

    def test_dry_run(self, svc: SystemdService) -> None:
        rc = svc.start(dry_run=True)
        assert rc == 0
        assert ["systemctl", "--user", "start", "archon"] in svc.command_log


# ── T22 — stop ──


class TestStop:
    def test_success(self, svc: SystemdService) -> None:
        with patch.object(svc, "_run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            assert svc.stop() == 0
            mock_run.assert_called_once_with(
                ["systemctl", "--user", "stop", "archon"], dry_run=False
            )

    def test_failure_rc1(self, svc: SystemdService) -> None:
        with patch.object(svc, "_run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="failed"
            )
            assert svc.stop() == 1

    def test_file_not_found(self, svc: SystemdService) -> None:
        with patch.object(svc, "_run", side_effect=FileNotFoundError):
            assert svc.stop() == 1

    def test_dry_run(self, svc: SystemdService) -> None:
        rc = svc.stop(dry_run=True)
        assert rc == 0
        assert ["systemctl", "--user", "stop", "archon"] in svc.command_log


# ── T23 — status ──


class TestStatus:
    def _mock_runtime(self, uptime: str | None = "01:23:45") -> MagicMock:
        rt = MagicMock()
        rt.process_uptime.return_value = uptime
        override(runtime=rt)
        return rt

    def test_active_with_pid(self, svc: SystemdService) -> None:
        self._mock_runtime("02:30:00")
        with patch.object(svc, "_run") as mock_run:
            mock_run.side_effect = [
                subprocess.CompletedProcess([], 0, stdout="active\n", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="MainPID=1234\n", stderr=""),
            ]
            info = svc.status()
        assert info == ServiceInfo(running=True, label="archon", pid=1234, uptime="02:30:00")

    def test_inactive(self, svc: SystemdService) -> None:
        self._mock_runtime()
        with patch.object(svc, "_run") as mock_run:
            mock_run.side_effect = [
                subprocess.CompletedProcess([], 3, stdout="inactive\n", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="MainPID=0\n", stderr=""),
            ]
            info = svc.status()
        assert info == ServiceInfo(running=False, label="archon", pid=None, uptime=None)

    def test_failed(self, svc: SystemdService) -> None:
        self._mock_runtime()
        with patch.object(svc, "_run") as mock_run:
            mock_run.side_effect = [
                subprocess.CompletedProcess([], 3, stdout="failed\n", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="MainPID=0\n", stderr=""),
            ]
            info = svc.status()
        assert info == ServiceInfo(running=False, label="archon", pid=None, uptime=None)

    def test_main_pid_zero(self, svc: SystemdService) -> None:
        self._mock_runtime()
        with patch.object(svc, "_run") as mock_run:
            mock_run.side_effect = [
                subprocess.CompletedProcess([], 0, stdout="active\n", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="MainPID=0\n", stderr=""),
            ]
            info = svc.status()
        assert info == ServiceInfo(running=True, label="archon", pid=None, uptime=None)

    def test_malformed_pid(self, svc: SystemdService) -> None:
        self._mock_runtime()
        with patch.object(svc, "_run") as mock_run:
            mock_run.side_effect = [
                subprocess.CompletedProcess([], 0, stdout="active\n", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="garbage\n", stderr=""),
            ]
            info = svc.status()
        assert info == ServiceInfo(running=True, label="archon", pid=None, uptime=None)

    def test_empty_stdout(self, svc: SystemdService) -> None:
        self._mock_runtime()
        with patch.object(svc, "_run") as mock_run:
            mock_run.side_effect = [
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            ]
            info = svc.status()
        assert info == ServiceInfo(running=False, label="archon", pid=None, uptime=None)

    def test_non_numeric_pid(self, svc: SystemdService) -> None:
        self._mock_runtime()
        with patch.object(svc, "_run") as mock_run:
            mock_run.side_effect = [
                subprocess.CompletedProcess([], 0, stdout="active\n", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="MainPID=abc\n", stderr=""),
            ]
            info = svc.status()
        assert info == ServiceInfo(running=True, label="archon", pid=None, uptime=None)

    def test_subprocess_fails(self, svc: SystemdService) -> None:
        with patch.object(svc, "_run", side_effect=FileNotFoundError):
            info = svc.status()
        assert info == ServiceInfo(running=False, label="archon")


# ── T24 — remediation_hint + pre_activate_cleanup ──


class TestRemediationHint:
    def test_contains_systemctl(self, svc: SystemdService) -> None:
        hint = svc.remediation_hint()
        assert "systemctl" in hint


class TestPreActivateCleanup:
    def test_calls_stop(self, svc: SystemdService) -> None:
        with patch.object(svc, "stop", return_value=0) as mock_stop:
            rc = svc.pre_activate_cleanup()
            assert rc == 0
            mock_stop.assert_called_once_with(dry_run=False)

    def test_ignores_stop_error(self, svc: SystemdService) -> None:
        with patch.object(svc, "stop", return_value=1):
            rc = svc.pre_activate_cleanup()
            assert rc == 0

    def test_dry_run(self, svc: SystemdService) -> None:
        with patch.object(svc, "stop", return_value=0) as mock_stop:
            rc = svc.pre_activate_cleanup(dry_run=True)
            assert rc == 0
            mock_stop.assert_called_once_with(dry_run=True)


# ── T25 — restart ──


class TestRestart:
    def test_success(self, svc: SystemdService) -> None:
        with patch.object(svc, "_run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            assert svc.restart() == 0
            mock_run.assert_called_once_with(
                ["systemctl", "--user", "restart", "archon"], dry_run=False
            )

    def test_failure(self, svc: SystemdService) -> None:
        with patch.object(svc, "_run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr=""
            )
            assert svc.restart() == 1

    def test_dry_run(self, svc: SystemdService) -> None:
        rc = svc.restart(dry_run=True)
        assert rc == 0
        assert ["systemctl", "--user", "restart", "archon"] in svc.command_log


# ── T26 — register + unregister ──


class TestRegister:
    def test_template_substitution_and_file_write(
        self, svc: SystemdService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        unit_path = tmp_path / "archon.service"
        monkeypatch.setattr("archon.platform.linux.service._UNIT_PATH", unit_path)

        template = (
            "WorkingDirectory=__ARCHON_DIR__\n"
            "ExecStart=__UV_PATH__ run python main.py\n"
            "StandardOutput=append:__LOG_FILE__\n"
        )
        monkeypatch.setattr(
            "archon.platform.linux.service._read_template", lambda: template
        )

        with patch.object(svc, "_run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")
            rc = svc.register()

        assert rc == 0
        content = unit_path.read_text()
        assert "__ARCHON_DIR__" not in content
        assert "__UV_PATH__" not in content
        assert "__LOG_FILE__" not in content

    def test_dry_run_no_file_write(
        self, svc: SystemdService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        unit_path = tmp_path / "archon.service"
        monkeypatch.setattr("archon.platform.linux.service._UNIT_PATH", unit_path)
        monkeypatch.setattr(
            "archon.platform.linux.service._read_template",
            lambda: "WorkingDirectory=__ARCHON_DIR__\n",
        )

        rc = svc.register(dry_run=True)
        assert rc == 0
        assert not unit_path.exists()
        # Should log daemon-reload, enable, start, loginctl
        cmds_flat = [" ".join(c) for c in svc.command_log]
        assert any("daemon-reload" in c for c in cmds_flat)
        assert any("enable" in c for c in cmds_flat)
        assert any("start" in c for c in cmds_flat)
        assert any("loginctl" in c for c in cmds_flat)

    def test_linger_called(
        self, svc: SystemdService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        unit_path = tmp_path / "archon.service"
        monkeypatch.setattr("archon.platform.linux.service._UNIT_PATH", unit_path)
        monkeypatch.setattr(
            "archon.platform.linux.service._read_template",
            lambda: "WorkingDirectory=__ARCHON_DIR__\n",
        )

        rc = svc.register(dry_run=True)
        assert rc == 0
        cmds_flat = [" ".join(c) for c in svc.command_log]
        assert any("enable-linger" in c for c in cmds_flat)

    def test_enable_failure_cleans_up_and_reloads(
        self, svc: SystemdService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        unit_path = tmp_path / "archon.service"
        monkeypatch.setattr("archon.platform.linux.service._UNIT_PATH", unit_path)
        monkeypatch.setattr(
            "archon.platform.linux.service._read_template",
            lambda: "WorkingDirectory=__ARCHON_DIR__\n",
        )

        recorded_cmds: list[list[str]] = []

        def _mock_run(cmd: list[str], dry_run: bool = False, **kw) -> subprocess.CompletedProcess[str]:
            recorded_cmds.append(cmd)
            if "enable" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "enable failed")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with patch.object(svc, "_run", side_effect=_mock_run):
            rc = svc.register()

        assert rc == 1
        # Unit file should be cleaned up
        assert not unit_path.exists()
        # daemon-reload must be called after cleanup
        reload_cmds = [c for c in recorded_cmds if "daemon-reload" in c]
        assert len(reload_cmds) == 2  # one before enable, one after cleanup

    def test_start_failure_still_returns_zero(
        self, svc: SystemdService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """register() returns 0 even if start fails — the unit is enabled for next boot."""
        unit_path = tmp_path / "archon.service"
        monkeypatch.setattr("archon.platform.linux.service._UNIT_PATH", unit_path)
        monkeypatch.setattr(
            "archon.platform.linux.service._read_template",
            lambda: "WorkingDirectory=__ARCHON_DIR__\n",
        )

        def _mock_run(cmd: list[str], dry_run: bool = False, **kw) -> subprocess.CompletedProcess[str]:
            if "start" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "start failed")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with patch.object(svc, "_run", side_effect=_mock_run):
            rc = svc.register()

        assert rc == 0  # enabled for next boot, start failure is non-fatal

    def test_linger_falls_back_to_getpass(
        self, svc: SystemdService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        unit_path = tmp_path / "archon.service"
        monkeypatch.setattr("archon.platform.linux.service._UNIT_PATH", unit_path)
        monkeypatch.setattr(
            "archon.platform.linux.service._read_template",
            lambda: "WorkingDirectory=__ARCHON_DIR__\n",
        )
        monkeypatch.delenv("USER", raising=False)
        monkeypatch.setattr("archon.platform.linux.service.getpass.getuser", lambda: "testuser")

        recorded_cmds: list[list[str]] = []

        def _mock_run(cmd: list[str], dry_run: bool = False, **kw) -> subprocess.CompletedProcess[str]:
            recorded_cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with patch.object(svc, "_run", side_effect=_mock_run):
            rc = svc.register()

        assert rc == 0
        linger_cmds = [c for c in recorded_cmds if "enable-linger" in c]
        assert linger_cmds
        assert linger_cmds[0][-1] == "testuser"

    def test_permission_error(
        self, svc: SystemdService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        unit_path = tmp_path / "noperm" / "archon.service"
        monkeypatch.setattr("archon.platform.linux.service._UNIT_PATH", unit_path)
        monkeypatch.setattr(
            "archon.platform.linux.service._read_template",
            lambda: "WorkingDirectory=__ARCHON_DIR__\n",
        )
        # Parent dir doesn't exist → write will fail
        rc = svc.register()
        assert rc == 1


class TestUnregister:
    def test_unregister_sequence(
        self, svc: SystemdService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        unit_path = tmp_path / "archon.service"
        unit_path.touch()
        monkeypatch.setattr("archon.platform.linux.service._UNIT_PATH", unit_path)

        with patch.object(svc, "_run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")
            rc = svc.unregister()

        assert rc == 0
        assert not unit_path.exists()
        # Should have called stop, disable, daemon-reload
        calls = [c.args[0] for c in mock_run.call_args_list]
        stop_cmd = ["systemctl", "--user", "stop", "archon"]
        disable_cmd = ["systemctl", "--user", "disable", "archon"]
        reload_cmd = ["systemctl", "--user", "daemon-reload"]
        assert stop_cmd in calls
        assert disable_cmd in calls
        assert reload_cmd in calls

    def test_unregister_dry_run(
        self, svc: SystemdService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        unit_path = tmp_path / "archon.service"
        unit_path.touch()
        monkeypatch.setattr("archon.platform.linux.service._UNIT_PATH", unit_path)

        rc = svc.unregister(dry_run=True)
        assert rc == 0
        # File should still exist in dry-run
        assert unit_path.exists()
        cmds_flat = [" ".join(c) for c in svc.command_log]
        assert any("stop" in c for c in cmds_flat)
        assert any("disable" in c for c in cmds_flat)
        assert any("daemon-reload" in c for c in cmds_flat)


# ── T27 — integration (dry-run lifecycle) ──


class TestDryRunLifecycle:
    def test_full_lifecycle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        svc = SystemdService()
        unit_path = tmp_path / "archon.service"
        monkeypatch.setattr("archon.platform.linux.service._UNIT_PATH", unit_path)
        monkeypatch.setattr(
            "archon.platform.linux.service._read_template",
            lambda: "WorkingDirectory=__ARCHON_DIR__\n",
        )

        assert svc.register(dry_run=True) == 0
        assert svc.start(dry_run=True) == 0
        assert svc.restart(dry_run=True) == 0
        assert svc.stop(dry_run=True) == 0
        assert svc.unregister(dry_run=True) == 0

        # All commands reference systemctl or loginctl
        for cmd in svc.command_log:
            assert any(
                tool in cmd[0] for tool in ("systemctl", "loginctl")
            ), f"Unexpected command: {cmd}"

        # register must include daemon-reload → enable → start in order
        cmds_flat = [" ".join(c) for c in svc.command_log]
        reload_idx = next(i for i, c in enumerate(cmds_flat) if "daemon-reload" in c)
        enable_idx = next(i for i, c in enumerate(cmds_flat) if "enable" in c)
        start_indices = [i for i, c in enumerate(cmds_flat) if "start" in c and "restart" not in c]
        first_start_idx = start_indices[0]
        assert reload_idx < enable_idx < first_start_idx
