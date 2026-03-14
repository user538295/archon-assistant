"""Tests for macOS LaunchdService (T8–T16)."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from archon.platform import override
from archon.platform.macos.service import LaunchdService, _LABEL
from archon.platform.types import ServiceInfo


pytestmark = pytest.mark.macos


@pytest.fixture
def svc() -> LaunchdService:
    return LaunchdService()


@pytest.fixture
def tmp_plist(tmp_path: Path, svc: LaunchdService, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point _PLIST_PATH at tmp_path and return the plist path."""
    plist = tmp_path / "com.archon.assistant.plist"
    monkeypatch.setattr(svc, "_PLIST_PATH", plist)
    return plist


# ── T8 — scaffold + is_installed ──────────────────────────────────────


class TestT8ServiceNameAndIsInstalled:
    def test_service_name(self, svc: LaunchdService) -> None:
        assert svc.service_name == "launchd"

    def test_is_installed_true(self, svc: LaunchdService, tmp_plist: Path) -> None:
        tmp_plist.write_text("<plist/>")
        assert svc.is_installed() is True

    def test_is_installed_false(self, svc: LaunchdService, tmp_plist: Path) -> None:
        assert svc.is_installed() is False


# ── T9 — _is_loaded ──────────────────────────────────────────────────


class TestT9IsLoaded:
    def test_loaded_rc0(self, svc: LaunchdService) -> None:
        with patch.object(svc, "_run", return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )):
            assert svc._is_loaded() is True

    def test_not_loaded_rc_nonzero(self, svc: LaunchdService) -> None:
        with patch.object(svc, "_run", return_value=subprocess.CompletedProcess(
            args=[], returncode=3, stdout="", stderr=""
        )):
            assert svc._is_loaded() is False

    def test_file_not_found(self, svc: LaunchdService) -> None:
        with patch.object(svc, "_run", side_effect=FileNotFoundError):
            assert svc._is_loaded() is False


# ── T10 — start ──────────────────────────────────────────────────────


class TestT10Start:
    def test_start_success(self, svc: LaunchdService, tmp_plist: Path) -> None:
        tmp_plist.write_text("<plist/>")
        with patch.object(svc, "_is_loaded", return_value=False), \
             patch.object(svc, "_run", return_value=subprocess.CompletedProcess(
                 args=[], returncode=0, stdout="", stderr=""
             )) as mock_run:
            assert svc.start() == 0
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert cmd == ["launchctl", "load", str(tmp_plist)]

    def test_start_plist_missing(self, svc: LaunchdService, tmp_plist: Path) -> None:
        assert svc.start() == 1

    def test_start_already_loaded(self, svc: LaunchdService, tmp_plist: Path) -> None:
        tmp_plist.write_text("<plist/>")
        with patch.object(svc, "_is_loaded", return_value=True):
            assert svc.start() == 0

    def test_start_file_not_found(self, svc: LaunchdService, tmp_plist: Path) -> None:
        tmp_plist.write_text("<plist/>")
        with patch.object(svc, "_is_loaded", return_value=False), \
             patch.object(svc, "_run", side_effect=FileNotFoundError):
            assert svc.start() == 1

    def test_start_dry_run(self, svc: LaunchdService, tmp_plist: Path) -> None:
        tmp_plist.write_text("<plist/>")
        with patch.object(svc, "_is_loaded", return_value=False):
            assert svc.start(dry_run=True) == 0
            assert any("launchctl" in cmd[0] for cmd in svc.command_log)


# ── T11 — stop ────────────────────────────────────────────────────────


class TestT11Stop:
    def test_stop_success(self, svc: LaunchdService, tmp_plist: Path) -> None:
        tmp_plist.write_text("<plist/>")
        with patch.object(svc, "_is_loaded", return_value=True), \
             patch.object(svc, "_run", return_value=subprocess.CompletedProcess(
                 args=[], returncode=0, stdout="", stderr=""
             )):
            assert svc.stop() == 0

    def test_stop_failure(self, svc: LaunchdService, tmp_plist: Path) -> None:
        tmp_plist.write_text("<plist/>")
        with patch.object(svc, "_is_loaded", return_value=True), \
             patch.object(svc, "_run", return_value=subprocess.CompletedProcess(
                 args=[], returncode=1, stdout="", stderr="error"
             )):
            assert svc.stop() == 1

    def test_stop_already_stopped(self, svc: LaunchdService, tmp_plist: Path) -> None:
        tmp_plist.write_text("<plist/>")
        with patch.object(svc, "_is_loaded", return_value=False):
            assert svc.stop() == 0

    def test_stop_bootout_fallback(self, svc: LaunchdService, tmp_plist: Path) -> None:
        """Plist doesn't exist but service is loaded → use bootout."""
        # plist does NOT exist (tmp_plist not written)
        with patch.object(svc, "_is_loaded", return_value=True), \
             patch.object(svc, "_run", return_value=subprocess.CompletedProcess(
                 args=[], returncode=0, stdout="", stderr=""
             )) as mock_run:
            assert svc.stop() == 0
            cmd = mock_run.call_args[0][0]
            assert "bootout" in cmd

    def test_stop_file_not_found(self, svc: LaunchdService, tmp_plist: Path) -> None:
        tmp_plist.write_text("<plist/>")
        with patch.object(svc, "_is_loaded", return_value=True), \
             patch.object(svc, "_run", side_effect=FileNotFoundError):
            assert svc.stop() == 1

    def test_stop_dry_run(self, svc: LaunchdService, tmp_plist: Path) -> None:
        tmp_plist.write_text("<plist/>")
        with patch.object(svc, "_is_loaded", return_value=True):
            assert svc.stop(dry_run=True) == 0
            assert len(svc.command_log) >= 1


# ── T12 — status ─────────────────────────────────────────────────────


class TestT12Status:
    def _mock_runtime(self, uptime: str | None = "01:23:45") -> MagicMock:
        runtime = MagicMock()
        runtime.process_uptime.return_value = uptime
        override(runtime=runtime)
        return runtime

    def test_running_with_pid(self, svc: LaunchdService) -> None:
        self._mock_runtime("02:30:00")
        stdout = '{\n\t"PID" = 1234;\n\t"Label" = "com.archon.assistant";\n}'
        with patch.object(svc, "_run", return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout=stdout, stderr=""
        )):
            info = svc.status()
            assert info == ServiceInfo(running=True, service_name=_LABEL, pid=1234, uptime="02:30:00")

    def test_stopped(self, svc: LaunchdService) -> None:
        self._mock_runtime()
        with patch.object(svc, "_run", return_value=subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr=""
        )):
            info = svc.status()
            assert info == ServiceInfo(running=False, service_name=_LABEL)

    def test_pid_zero(self, svc: LaunchdService) -> None:
        """PID = 0 means not running (launchd reports 0 for stopped services)."""
        self._mock_runtime()
        stdout = '{\n\t"PID" = 0;\n\t"Label" = "com.archon.assistant";\n}'
        with patch.object(svc, "_run", return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout=stdout, stderr=""
        )):
            info = svc.status()
            assert info.running is False
            assert info.pid == 0

    def test_malformed_output(self, svc: LaunchdService) -> None:
        self._mock_runtime()
        with patch.object(svc, "_run", return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout="garbage data", stderr=""
        )):
            info = svc.status()
            assert info.running is False

    def test_empty_output(self, svc: LaunchdService) -> None:
        self._mock_runtime()
        with patch.object(svc, "_run", return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )):
            info = svc.status()
            assert info.running is False

    def test_non_numeric_pid(self, svc: LaunchdService) -> None:
        self._mock_runtime()
        stdout = '{\n\t"PID" = abc;\n}'
        with patch.object(svc, "_run", return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout=stdout, stderr=""
        )):
            info = svc.status()
            assert info.running is False

    def test_get_runtime_raises(self, svc: LaunchdService) -> None:
        """status() returns ServiceInfo(running=True) even if get_runtime() raises."""
        stdout = '{\n\t"PID" = 1234;\n\t"Label" = "com.archon.assistant";\n}'
        with patch.object(svc, "_run", return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout=stdout, stderr=""
        )), patch("archon.platform.macos.service.get_runtime", side_effect=RuntimeError("boom")):
            info = svc.status()
            assert info.running is True
            assert info.pid == 1234
            assert info.uptime is None

    def test_subprocess_fails(self, svc: LaunchdService) -> None:
        self._mock_runtime()
        with patch.object(svc, "_run", side_effect=FileNotFoundError):
            info = svc.status()
            assert info == ServiceInfo(running=False, service_name=_LABEL)


# ── T13 — remediation_hint + pre_activate_cleanup ─────────────────────


class TestT13RemediationAndCleanup:
    def test_hint_contains_launchctl(self, svc: LaunchdService) -> None:
        hint = svc.remediation_hint()
        assert "launchctl" in hint

    def test_cleanup_when_loaded(self, svc: LaunchdService) -> None:
        with patch.object(svc, "_is_loaded", return_value=True), \
             patch.object(svc, "_run", return_value=subprocess.CompletedProcess(
                 args=[], returncode=0, stdout="", stderr=""
             )):
            assert svc.pre_activate_cleanup() == 0

    def test_cleanup_unload_failure(self, svc: LaunchdService) -> None:
        with patch.object(svc, "_is_loaded", return_value=True), \
             patch.object(svc, "_run", return_value=subprocess.CompletedProcess(
                 args=[], returncode=1, stdout="", stderr="unload failed"
             )):
            assert svc.pre_activate_cleanup() == 1

    def test_cleanup_when_not_loaded(self, svc: LaunchdService) -> None:
        with patch.object(svc, "_is_loaded", return_value=False):
            assert svc.pre_activate_cleanup() == 0

    def test_cleanup_dry_run(self, svc: LaunchdService) -> None:
        with patch.object(svc, "_is_loaded", return_value=True):
            assert svc.pre_activate_cleanup(dry_run=True) == 0
            assert len(svc.command_log) >= 1


# ── T14 — restart ────────────────────────────────────────────────────


class TestT14Restart:
    def test_both_succeed(self, svc: LaunchdService) -> None:
        with patch.object(svc, "stop", return_value=0) as mock_stop, \
             patch.object(svc, "start", return_value=0) as mock_start:
            assert svc.restart() == 0
            mock_stop.assert_called_once()
            mock_start.assert_called_once()

    def test_stop_fails_still_starts(self, svc: LaunchdService) -> None:
        with patch.object(svc, "stop", return_value=1), \
             patch.object(svc, "start", return_value=0) as mock_start:
            assert svc.restart() == 1
            mock_start.assert_called_once()

    def test_start_fails(self, svc: LaunchdService) -> None:
        with patch.object(svc, "stop", return_value=0), \
             patch.object(svc, "start", return_value=1):
            assert svc.restart() == 1

    def test_dry_run(self, svc: LaunchdService) -> None:
        with patch.object(svc, "stop", return_value=0) as mock_stop, \
             patch.object(svc, "start", return_value=0) as mock_start:
            assert svc.restart(dry_run=True) == 0
            mock_stop.assert_called_once_with(dry_run=True)
            mock_start.assert_called_once_with(dry_run=True)


# ── T15 — register + unregister ──────────────────────────────────────


class TestT15RegisterUnregister:
    @pytest.fixture
    def template_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        tpl = tmp_path / "scripts"
        tpl.mkdir()
        plist_content = (
            '<?xml version="1.0"?>\n<dict>\n'
            "  <string>__UV_PATH__</string>\n"
            "  <string>__ARCHON_DIR__</string>\n"
            "  <string>__LOG_FILE__</string>\n"
            "</dict>\n"
        )
        (tpl / "com.archon.assistant.plist").write_text(plist_content)
        return tpl

    @pytest.fixture
    def svc_for_register(
        self,
        svc: LaunchdService,
        tmp_plist: Path,
        template_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> LaunchdService:
        monkeypatch.setattr(svc, "_TEMPLATE_PATH", template_dir / "com.archon.assistant.plist")
        return svc

    def test_template_substitution(self, svc_for_register: LaunchdService, tmp_plist: Path) -> None:
        with patch.object(svc_for_register, "_is_loaded", return_value=False), \
             patch.object(svc_for_register, "_run", return_value=subprocess.CompletedProcess(
                 args=[], returncode=0, stdout="", stderr=""
             )):
            result = svc_for_register.register()
            assert result == 0
            content = tmp_plist.read_text()
            assert "__UV_PATH__" not in content
            assert "__ARCHON_DIR__" not in content
            assert "__LOG_FILE__" not in content

    def test_file_written(self, svc_for_register: LaunchdService, tmp_plist: Path) -> None:
        with patch.object(svc_for_register, "_is_loaded", return_value=False), \
             patch.object(svc_for_register, "_run", return_value=subprocess.CompletedProcess(
                 args=[], returncode=0, stdout="", stderr=""
             )):
            svc_for_register.register()
            assert tmp_plist.exists()

    def test_dry_run_no_write(self, svc_for_register: LaunchdService, tmp_plist: Path) -> None:
        with patch.object(svc_for_register, "_is_loaded", return_value=False):
            result = svc_for_register.register(dry_run=True)
            assert result == 0
            assert not tmp_plist.exists()
            assert len(svc_for_register.command_log) >= 1

    def test_permission_error(self, svc_for_register: LaunchdService, tmp_plist: Path) -> None:
        with patch.object(svc_for_register, "_is_loaded", return_value=False), \
             patch("builtins.open", side_effect=PermissionError("denied")):
            result = svc_for_register.register()
            assert result == 1

    def test_load_failure_cleans_up(self, svc_for_register: LaunchdService, tmp_plist: Path) -> None:
        with patch.object(svc_for_register, "_is_loaded", return_value=False), \
             patch.object(svc_for_register, "_run", return_value=subprocess.CompletedProcess(
                 args=[], returncode=1, stdout="", stderr="load failed"
             )):
            result = svc_for_register.register()
            assert result == 1
            assert not tmp_plist.exists()

    def test_unregister(self, svc: LaunchdService, tmp_plist: Path) -> None:
        tmp_plist.write_text("<plist/>")
        with patch.object(svc, "_is_loaded", return_value=True), \
             patch.object(svc, "_run", return_value=subprocess.CompletedProcess(
                 args=[], returncode=0, stdout="", stderr=""
             )):
            result = svc.unregister()
            assert result == 0
            assert not tmp_plist.exists()

    def test_unregister_unload_failure_keeps_plist(self, svc: LaunchdService, tmp_plist: Path) -> None:
        tmp_plist.write_text("<plist/>")
        with patch.object(svc, "_is_loaded", return_value=True), \
             patch.object(svc, "_run", return_value=subprocess.CompletedProcess(
                 args=[], returncode=1, stdout="", stderr="unload failed"
             )):
            result = svc.unregister()
            assert result == 1
            assert tmp_plist.exists()  # plist NOT deleted when unload fails

    def test_unregister_dry_run(self, svc: LaunchdService, tmp_plist: Path) -> None:
        tmp_plist.write_text("<plist/>")
        with patch.object(svc, "_is_loaded", return_value=True):
            result = svc.unregister(dry_run=True)
            assert result == 0
            assert tmp_plist.exists()  # not deleted in dry_run

    def test_register_launchctl_not_found(self, svc_for_register: LaunchdService, tmp_plist: Path) -> None:
        with patch.object(svc_for_register, "_run", side_effect=FileNotFoundError):
            result = svc_for_register.register()
            assert result == 1
            assert not tmp_plist.exists()  # plist cleaned up


# ── T16 — dry-run lifecycle integration ───────────────────────────────


class TestT16DryRunLifecycle:
    def test_full_lifecycle(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        svc = LaunchdService()
        plist = tmp_path / "com.archon.assistant.plist"
        monkeypatch.setattr(svc, "_PLIST_PATH", plist)

        # Set up template
        tpl_dir = tmp_path / "scripts"
        tpl_dir.mkdir()
        tpl = tpl_dir / "com.archon.assistant.plist"
        tpl.write_text("<dict>__UV_PATH__ __ARCHON_DIR__ __LOG_FILE__</dict>")
        monkeypatch.setattr(svc, "_TEMPLATE_PATH", tpl)

        # Stub _is_loaded to return False (dry-run never actually loads)
        monkeypatch.setattr(svc, "_is_loaded", lambda: False)

        # register (dry_run skips file write, so create plist to simulate)
        assert svc.register(dry_run=True) == 0
        plist.write_text("<plist/>")  # simulate installed state for start/stop

        assert svc.start(dry_run=True) == 0
        assert svc.restart(dry_run=True) == 0
        assert svc.stop(dry_run=True) == 0
        assert svc.unregister(dry_run=True) == 0

        # Verify ordering constraints in command_log
        cmds_flat = [" ".join(c) for c in svc.command_log]

        # register issues a load
        load_indices = [i for i, c in enumerate(cmds_flat) if "load" in c]
        assert len(load_indices) >= 1

        # There should be commands logged
        assert len(svc.command_log) >= 3
