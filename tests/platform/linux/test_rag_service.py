"""Tests for Task 6.7 — Linux SystemdRagService."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def svc():  # type: ignore[no-untyped-def]
    from archon.platform.linux.rag_service import SystemdRagService
    return SystemdRagService()


def _ok() -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def test_linux_rag_service_unit_file_contains_service_name(tmp_path: Path) -> None:
    """register() writes a unit file containing archon-rag service name."""
    from archon.platform.linux.rag_service import SystemdRagService

    svc = SystemdRagService()
    unit = tmp_path / "archon-rag.service"
    with (
        patch.object(type(svc), "_unit_path", new_callable=lambda: property(lambda self: unit)),
        patch.object(svc, "_run", return_value=_ok()),
    ):
        rc = svc.register()
    assert rc == 0
    content = unit.read_text()
    assert "archon-rag" in content


def test_linux_rag_service_register_dry_run_no_file(tmp_path: Path) -> None:
    """register(dry_run=True) does not write any file."""
    from archon.platform.linux.rag_service import SystemdRagService

    svc = SystemdRagService()
    unit = tmp_path / "archon-rag.service"
    with patch.object(type(svc), "_unit_path", new_callable=lambda: property(lambda self: unit)):
        rc = svc.register(dry_run=True)
    assert rc == 0
    assert not unit.exists()


def test_linux_rag_service_unit_file_contains_exec_start(tmp_path: Path) -> None:
    """register() writes unit file with ExecStart pointing to sys.executable."""
    import sys
    from archon.platform.linux.rag_service import SystemdRagService

    svc = SystemdRagService()
    unit = tmp_path / "archon-rag.service"
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
    from archon.platform.linux.rag_service import SystemdRagService

    svc = SystemdRagService()
    unit = tmp_path / "archon-rag.service"
    with (
        patch.object(type(svc), "_unit_path", new_callable=lambda: property(lambda self: unit)),
        patch.object(svc, "_run", return_value=_ok()),
    ):
        svc.register()
    content = unit.read_text()
    assert "Restart=always" in content


def test_linux_rag_service_is_installed_true_when_unit_exists(tmp_path: Path) -> None:
    """is_installed() returns True when the unit file exists."""
    from archon.platform.linux.rag_service import SystemdRagService

    svc = SystemdRagService()
    unit = tmp_path / "archon-rag.service"
    unit.write_text("[Unit]\n")
    with patch.object(type(svc), "_unit_path", new_callable=lambda: property(lambda self: unit)):
        assert svc.is_installed() is True


def test_linux_rag_service_is_installed_false_when_no_unit(tmp_path: Path) -> None:
    """is_installed() returns False when unit file is absent."""
    from archon.platform.linux.rag_service import SystemdRagService

    svc = SystemdRagService()
    unit = tmp_path / "archon-rag.service"
    with patch.object(type(svc), "_unit_path", new_callable=lambda: property(lambda self: unit)):
        assert svc.is_installed() is False


def test_linux_rag_service_service_name() -> None:
    """service_name returns 'systemd-rag'."""
    from archon.platform.linux.rag_service import SystemdRagService

    assert SystemdRagService().service_name == "systemd-rag"


def test_linux_rag_service_remediation_hint() -> None:
    """remediation_hint() returns instruction to run archon rag install."""
    from archon.platform.linux.rag_service import SystemdRagService

    assert "archon rag install" in SystemdRagService().remediation_hint()


def test_linux_rag_service_pre_activate_cleanup_returns_zero() -> None:
    """pre_activate_cleanup() is a no-op returning 0."""
    from archon.platform.linux.rag_service import SystemdRagService

    svc = SystemdRagService()
    assert svc.pre_activate_cleanup() == 0
    assert svc.pre_activate_cleanup(dry_run=True) == 0


def test_linux_rag_service_start_calls_systemctl() -> None:
    """start() calls systemctl --user start archon-rag."""
    from archon.platform.linux.rag_service import SystemdRagService

    svc = SystemdRagService()
    mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch.object(svc, "_run", return_value=mock_result) as mock_run:
        rc = svc.start()
    assert rc == 0
    assert mock_run.call_args[0][0] == ["systemctl", "--user", "start", "archon-rag"]


def test_linux_rag_service_stop_calls_systemctl() -> None:
    """stop() calls systemctl --user stop archon-rag."""
    from archon.platform.linux.rag_service import SystemdRagService

    svc = SystemdRagService()
    mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch.object(svc, "_run", return_value=mock_result) as mock_run:
        rc = svc.stop()
    assert rc == 0
    assert mock_run.call_args[0][0] == ["systemctl", "--user", "stop", "archon-rag"]


def test_linux_rag_service_restart_calls_systemctl() -> None:
    """restart() calls systemctl --user restart archon-rag."""
    from archon.platform.linux.rag_service import SystemdRagService

    svc = SystemdRagService()
    mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch.object(svc, "_run", return_value=mock_result) as mock_run:
        rc = svc.restart()
    assert rc == 0
    assert mock_run.call_args[0][0] == ["systemctl", "--user", "restart", "archon-rag"]


def test_linux_rag_service_status_running() -> None:
    """status() returns ServiceInfo(running=True) when service is active."""
    from archon.platform.linux.rag_service import SystemdRagService

    svc = SystemdRagService()
    active_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="active\n", stderr="")
    pid_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="MainPID=12345\n", stderr="")
    with patch.object(svc, "_run", side_effect=[active_result, pid_result]):
        info = svc.status()
    assert info.running is True
    assert info.pid == 12345


def test_linux_rag_service_status_not_running() -> None:
    """status() returns ServiceInfo(running=False) when service is inactive."""
    from archon.platform.linux.rag_service import SystemdRagService

    svc = SystemdRagService()
    active_result = subprocess.CompletedProcess(args=[], returncode=3, stdout="inactive\n", stderr="")
    pid_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="MainPID=0\n", stderr="")
    with patch.object(svc, "_run", side_effect=[active_result, pid_result]):
        info = svc.status()
    assert info.running is False


def test_get_rag_service_linux_returns_systemd_rag() -> None:
    """After Task 6.7 lands, get_rag_service() returns SystemdRagService on Linux."""
    from archon import platform as plat_module
    from archon.platform import reset
    from archon.platform.linux.rag_service import SystemdRagService

    reset()
    try:
        with patch.object(plat_module, "_detect", return_value="linux"):
            from archon.platform import get_rag_service
            result = get_rag_service()
            assert isinstance(result, SystemdRagService)
    finally:
        reset()
