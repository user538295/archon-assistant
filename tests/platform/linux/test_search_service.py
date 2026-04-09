"""Tests for Task 6.7 — Linux SystemdSearchService."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def svc():  # type: ignore[no-untyped-def]
    from archon.platform.linux.search_service import SystemdSearchService
    return SystemdSearchService()


def _ok() -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def test_linux_rag_service_unit_file_contains_service_name(tmp_path: Path) -> None:
    """register() writes a unit file containing archon-search service name."""
    from archon.platform.linux.search_service import SystemdSearchService

    svc = SystemdSearchService()
    unit = tmp_path / "archon-search.service"
    with (
        patch.object(type(svc), "_unit_path", new_callable=lambda: property(lambda self: unit)),
        patch.object(svc, "_run", return_value=_ok()),
    ):
        rc = svc.register()
    assert rc == 0
    content = unit.read_text()
    assert "archon-search" in content


def test_linux_rag_service_register_dry_run_no_file(tmp_path: Path) -> None:
    """register(dry_run=True) does not write any file."""
    from archon.platform.linux.search_service import SystemdSearchService

    svc = SystemdSearchService()
    unit = tmp_path / "archon-search.service"
    with patch.object(type(svc), "_unit_path", new_callable=lambda: property(lambda self: unit)):
        rc = svc.register(dry_run=True)
    assert rc == 0
    assert not unit.exists()


def test_linux_rag_service_unit_file_contains_exec_start(tmp_path: Path) -> None:
    """register() writes unit file with ExecStart pointing to sys.executable."""
    import sys
    from archon.platform.linux.search_service import SystemdSearchService

    svc = SystemdSearchService()
    unit = tmp_path / "archon-search.service"
    with (
        patch.object(type(svc), "_unit_path", new_callable=lambda: property(lambda self: unit)),
        patch.object(svc, "_run", return_value=_ok()),
    ):
        svc.register()
    content = unit.read_text()
    assert sys.executable in content
    assert "archon.search.server" in content


def test_linux_rag_service_unit_file_has_restart_always(tmp_path: Path) -> None:
    """register() writes unit with Restart=always."""
    from archon.platform.linux.search_service import SystemdSearchService

    svc = SystemdSearchService()
    unit = tmp_path / "archon-search.service"
    with (
        patch.object(type(svc), "_unit_path", new_callable=lambda: property(lambda self: unit)),
        patch.object(svc, "_run", return_value=_ok()),
    ):
        svc.register()
    content = unit.read_text()
    assert "Restart=always" in content


def test_linux_rag_service_is_installed_true_when_unit_exists(tmp_path: Path) -> None:
    """is_installed() returns True when the unit file exists."""
    from archon.platform.linux.search_service import SystemdSearchService

    svc = SystemdSearchService()
    unit = tmp_path / "archon-search.service"
    unit.write_text("[Unit]\n")
    with patch.object(type(svc), "_unit_path", new_callable=lambda: property(lambda self: unit)):
        assert svc.is_installed() is True


def test_linux_rag_service_is_installed_false_when_no_unit(tmp_path: Path) -> None:
    """is_installed() returns False when unit file is absent."""
    from archon.platform.linux.search_service import SystemdSearchService

    svc = SystemdSearchService()
    unit = tmp_path / "archon-search.service"
    with patch.object(type(svc), "_unit_path", new_callable=lambda: property(lambda self: unit)):
        assert svc.is_installed() is False


def test_linux_rag_service_service_name() -> None:
    """service_name returns 'systemd-search'."""
    from archon.platform.linux.search_service import SystemdSearchService

    assert SystemdSearchService().service_name == "systemd-search"


def test_linux_rag_service_remediation_hint() -> None:
    """remediation_hint() returns instruction to run archon search install."""
    from archon.platform.linux.search_service import SystemdSearchService

    assert "archon search install" in SystemdSearchService().remediation_hint()


def test_linux_rag_service_pre_activate_cleanup_returns_zero_when_not_active() -> None:
    """pre_activate_cleanup() returns 0 when legacy archon-rag is not active."""
    from archon.platform.linux.search_service import SystemdSearchService

    svc = SystemdSearchService()
    not_active = subprocess.CompletedProcess(args=[], returncode=3, stdout="inactive\n", stderr="")
    with patch.object(svc, "_run", return_value=not_active):
        assert svc.pre_activate_cleanup() == 0


def test_linux_rag_service_pre_activate_cleanup_dry_run_returns_zero() -> None:
    """pre_activate_cleanup(dry_run=True) is a no-op that returns 0."""
    from archon.platform.linux.search_service import SystemdSearchService

    svc = SystemdSearchService()
    assert svc.pre_activate_cleanup(dry_run=True) == 0


def test_linux_rag_service_pre_activate_cleanup_stops_legacy_when_active(tmp_path: Path) -> None:
    """pre_activate_cleanup() stops and disables legacy archon-rag when active."""
    from archon.platform.linux.search_service import SystemdSearchService

    svc = SystemdSearchService()
    legacy_unit = tmp_path / "archon-rag.service"
    legacy_unit.write_text("[Unit]\n")
    calls: list[list[str]] = []

    def mock_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        # First call is is-active → returncode 0 means active
        if "is-active" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="active\n", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with (
        patch("archon.platform.linux.search_service._LEGACY_UNIT_PATH", legacy_unit),
        patch.object(svc, "_run", side_effect=mock_run),
    ):
        rc = svc.pre_activate_cleanup()

    assert rc == 0
    stop_calls = [c for c in calls if "stop" in c]
    assert stop_calls, "systemctl stop not called for legacy service"


def test_linux_rag_service_pre_activate_cleanup_removes_legacy_unit(tmp_path: Path) -> None:
    """pre_activate_cleanup() removes the legacy unit file."""
    from archon.platform.linux.search_service import SystemdSearchService

    svc = SystemdSearchService()
    legacy_unit = tmp_path / "archon-rag.service"
    legacy_unit.write_text("[Unit]\n")

    def mock_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if "is-active" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="active\n", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with (
        patch("archon.platform.linux.search_service._LEGACY_UNIT_PATH", legacy_unit),
        patch.object(svc, "_run", side_effect=mock_run),
    ):
        svc.pre_activate_cleanup()

    assert not legacy_unit.exists()


def test_linux_rag_service_start_calls_systemctl() -> None:
    """start() calls systemctl --user start archon-search."""
    from archon.platform.linux.search_service import SystemdSearchService

    svc = SystemdSearchService()
    mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch.object(svc, "_run", return_value=mock_result) as mock_run:
        rc = svc.start()
    assert rc == 0
    assert mock_run.call_args[0][0] == ["systemctl", "--user", "start", "archon-search"]


def test_linux_rag_service_stop_calls_systemctl() -> None:
    """stop() calls systemctl --user stop archon-search."""
    from archon.platform.linux.search_service import SystemdSearchService

    svc = SystemdSearchService()
    mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch.object(svc, "_run", return_value=mock_result) as mock_run:
        rc = svc.stop()
    assert rc == 0
    assert mock_run.call_args[0][0] == ["systemctl", "--user", "stop", "archon-search"]


def test_linux_rag_service_restart_calls_systemctl() -> None:
    """restart() calls systemctl --user restart archon-search."""
    from archon.platform.linux.search_service import SystemdSearchService

    svc = SystemdSearchService()
    mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch.object(svc, "_run", return_value=mock_result) as mock_run:
        rc = svc.restart()
    assert rc == 0
    assert mock_run.call_args[0][0] == ["systemctl", "--user", "restart", "archon-search"]


def test_linux_rag_service_status_running() -> None:
    """status() returns ServiceInfo(running=True) when service is active."""
    from archon.platform.linux.search_service import SystemdSearchService

    svc = SystemdSearchService()
    active_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="active\n", stderr="")
    pid_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="MainPID=12345\n", stderr="")
    with patch.object(svc, "_run", side_effect=[active_result, pid_result]):
        info = svc.status()
    assert info.running is True
    assert info.pid == 12345


def test_linux_rag_service_status_not_running() -> None:
    """status() returns ServiceInfo(running=False) when service is inactive."""
    from archon.platform.linux.search_service import SystemdSearchService

    svc = SystemdSearchService()
    active_result = subprocess.CompletedProcess(args=[], returncode=3, stdout="inactive\n", stderr="")
    pid_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="MainPID=0\n", stderr="")
    with patch.object(svc, "_run", side_effect=[active_result, pid_result]):
        info = svc.status()
    assert info.running is False


# ── CPU throttling — Nice + CPUQuota ──────────────────────────────────────────


def test_linux_unit_file_has_nice_10(tmp_path: Path) -> None:
    """register() writes unit file with Nice=10 for reduced CPU priority."""
    from archon.platform.linux.search_service import SystemdSearchService

    svc = SystemdSearchService()
    unit = tmp_path / "archon-search.service"
    with (
        patch.object(type(svc), "_unit_path", new_callable=lambda: property(lambda self: unit)),
        patch.object(svc, "_run", return_value=_ok()),
    ):
        svc.register()
    content = unit.read_text()
    assert "Nice=10" in content


def test_linux_unit_file_has_cpu_quota(tmp_path: Path) -> None:
    """register() writes unit file with CPUQuota=50% to cap CPU usage."""
    from archon.platform.linux.search_service import SystemdSearchService

    svc = SystemdSearchService()
    unit = tmp_path / "archon-search.service"
    with (
        patch.object(type(svc), "_unit_path", new_callable=lambda: property(lambda self: unit)),
        patch.object(svc, "_run", return_value=_ok()),
    ):
        svc.register()
    content = unit.read_text()
    assert "CPUQuota=50%" in content


def test_linux_unit_file_cpu_throttling_in_service_section(tmp_path: Path) -> None:
    """Nice and CPUQuota must appear inside the [Service] section."""
    from archon.platform.linux.search_service import SystemdSearchService

    svc = SystemdSearchService()
    unit = tmp_path / "archon-search.service"
    with (
        patch.object(type(svc), "_unit_path", new_callable=lambda: property(lambda self: unit)),
        patch.object(svc, "_run", return_value=_ok()),
    ):
        svc.register()
    content = unit.read_text()
    service_pos = content.index("[Service]")
    install_pos = content.index("[Install]")
    nice_pos = content.index("Nice=10")
    quota_pos = content.index("CPUQuota=50%")
    assert service_pos < nice_pos < install_pos
    assert service_pos < quota_pos < install_pos


def test_get_search_service_linux_returns_systemd_rag() -> None:
    """After Task 6.7 lands, get_search_service() returns SystemdSearchService on Linux."""
    from archon import platform as plat_module
    from archon.platform import reset
    from archon.platform.linux.search_service import SystemdSearchService

    reset()
    try:
        with patch.object(plat_module, "_detect", return_value="linux"):
            from archon.platform import get_search_service
            result = get_search_service()
            assert isinstance(result, SystemdSearchService)
    finally:
        reset()
