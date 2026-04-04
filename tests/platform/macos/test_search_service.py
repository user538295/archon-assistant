"""Tests for Task 6.6 — macOS LaunchdSearchService and get_search_service() singleton."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.macos


# ── LaunchdSearchService ──────────────────────────────────────────────────────


@pytest.fixture
def svc():  # type: ignore[no-untyped-def]
    from archon.platform.macos.search_service import LaunchdSearchService
    return LaunchdSearchService()


@pytest.fixture
def tmp_plist(tmp_path: Path, svc, monkeypatch: pytest.MonkeyPatch) -> Path:  # type: ignore[no-untyped-def]
    plist = tmp_path / "com.archon.search.plist"
    monkeypatch.setattr(svc, "_plist_path", plist)
    return plist


def test_rag_service_plist_contains_label(tmp_path: Path) -> None:
    """register() writes a plist file containing com.archon.search label."""
    from archon.platform.macos.search_service import LaunchdSearchService

    svc = LaunchdSearchService()
    plist = tmp_path / "com.archon.search.plist"
    with patch.object(type(svc), "_plist_path", new_callable=lambda: property(lambda self: plist)):
        rc = svc.register()
    assert rc == 0
    content = plist.read_text()
    assert "com.archon.search" in content


def test_rag_service_plist_contains_python_executable(tmp_path: Path) -> None:
    """register() writes a plist with sys.executable in ProgramArguments."""
    from archon.platform.macos.search_service import LaunchdSearchService

    svc = LaunchdSearchService()
    plist = tmp_path / "com.archon.search.plist"
    with patch.object(type(svc), "_plist_path", new_callable=lambda: property(lambda self: plist)):
        rc = svc.register()
    assert rc == 0
    content = plist.read_text()
    assert sys.executable in content


def test_rag_service_register_dry_run_no_file(tmp_path: Path) -> None:
    """register(dry_run=True) does not write any file."""
    from archon.platform.macos.search_service import LaunchdSearchService

    svc = LaunchdSearchService()
    plist = tmp_path / "com.archon.search.plist"
    with patch.object(type(svc), "_plist_path", new_callable=lambda: property(lambda self: plist)):
        rc = svc.register(dry_run=True)
    assert rc == 0
    assert not plist.exists()


def test_rag_service_is_installed_true_when_plist_exists(tmp_path: Path) -> None:
    """is_installed() returns True when the plist file exists."""
    from archon.platform.macos.search_service import LaunchdSearchService

    svc = LaunchdSearchService()
    plist = tmp_path / "com.archon.search.plist"
    plist.write_text("<plist/>")
    with patch.object(type(svc), "_plist_path", new_callable=lambda: property(lambda self: plist)):
        assert svc.is_installed() is True


def test_rag_service_is_installed_false_when_no_plist(tmp_path: Path) -> None:
    """is_installed() returns False when plist is absent."""
    from archon.platform.macos.search_service import LaunchdSearchService

    svc = LaunchdSearchService()
    plist = tmp_path / "com.archon.search.plist"
    with patch.object(type(svc), "_plist_path", new_callable=lambda: property(lambda self: plist)):
        assert svc.is_installed() is False


def test_rag_service_service_name() -> None:
    """service_name property returns 'launchd-search'."""
    from archon.platform.macos.search_service import LaunchdSearchService

    svc = LaunchdSearchService()
    assert svc.service_name == "launchd-search"


def test_rag_service_remediation_hint() -> None:
    """remediation_hint returns instruction to run archon search install."""
    from archon.platform.macos.search_service import LaunchdSearchService

    svc = LaunchdSearchService()
    hint = svc.remediation_hint()
    assert "archon search install" in hint


def test_rag_service_pre_activate_cleanup_returns_zero() -> None:
    """pre_activate_cleanup() is a no-op that returns 0."""
    from archon.platform.macos.search_service import LaunchdSearchService

    svc = LaunchdSearchService()
    assert svc.pre_activate_cleanup() == 0
    assert svc.pre_activate_cleanup(dry_run=True) == 0


# ── get_search_service() singleton ────────────────────────────────────────────


def test_get_search_service_override_in_tests() -> None:
    """override(search_service=mock) makes get_search_service() return the mock."""
    from archon.platform import get_search_service, override, reset

    mock = MagicMock()
    try:
        override(search_service=mock)
        assert get_search_service() is mock
    finally:
        reset()


def test_reset_clears_rag_service_singleton() -> None:
    """After override + reset, get_search_service() returns fresh LaunchdSearchService."""
    from archon.platform import get_search_service, override, reset
    from archon.platform.macos.search_service import LaunchdSearchService

    mock = MagicMock()
    override(search_service=mock)
    reset()
    result = get_search_service()
    assert result is not mock
    assert isinstance(result, LaunchdSearchService)
    reset()  # clean up singleton


def test_get_search_service_windows_returns_stub() -> None:
    """On Windows platform, get_search_service() returns a WindowsSearchService instance."""
    from archon import platform as plat_module
    from archon.platform import get_search_service, reset
    from archon.platform.windows.search_service import WindowsSearchService

    reset()
    try:
        with patch.object(plat_module, "_detect", return_value="win32"):
            result = get_search_service()
            assert isinstance(result, WindowsSearchService)
    finally:
        reset()


# ── Lifecycle method tests ─────────────────────────────────────────────────


def test_rag_service_start_not_installed_returns_one(tmp_path: Path) -> None:
    """start() returns 1 when plist is not installed."""
    from archon.platform.macos.search_service import LaunchdSearchService
    svc = LaunchdSearchService()
    plist = tmp_path / "com.archon.search.plist"  # does not exist
    with patch.object(type(svc), "_plist_path", new_callable=lambda: property(lambda self: plist)):
        assert svc.start() == 1


def test_rag_service_stop_when_not_loaded_returns_zero() -> None:
    """stop() is idempotent — returns 0 when service is not loaded."""
    from archon.platform.macos.search_service import LaunchdSearchService
    svc = LaunchdSearchService()
    with patch.object(svc, "_is_loaded", return_value=False):
        assert svc.stop() == 0


def test_rag_service_stop_calls_unload_when_loaded(tmp_path: Path) -> None:
    """stop() calls launchctl unload when service is loaded."""
    from archon.platform.macos.search_service import LaunchdSearchService
    svc = LaunchdSearchService()
    plist = tmp_path / "com.archon.search.plist"
    mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with (
        patch.object(type(svc), "_plist_path", new_callable=lambda: property(lambda self: plist)),
        patch.object(svc, "_is_loaded", return_value=True),
        patch.object(svc, "_run", return_value=mock_result) as mock_run,
    ):
        rc = svc.stop()
    assert rc == 0
    assert mock_run.call_args[0][0] == ["launchctl", "unload", str(plist)]


def test_rag_service_status_running(tmp_path: Path) -> None:
    """status() returns ServiceInfo(running=True) when launchctl reports a PID."""
    from archon.platform.macos.search_service import LaunchdSearchService
    svc = LaunchdSearchService()
    mock_result = subprocess.CompletedProcess(
        args=[], returncode=0, stdout='"PID" = 12345;', stderr=""
    )
    with patch.object(svc, "_run", return_value=mock_result):
        info = svc.status()
    assert info.running is True
    assert info.pid == 12345


def test_rag_service_status_not_running() -> None:
    """status() returns ServiceInfo(running=False) when launchctl returns non-zero."""
    from archon.platform.macos.search_service import LaunchdSearchService
    svc = LaunchdSearchService()
    mock_result = subprocess.CompletedProcess(args=[], returncode=3, stdout="", stderr="")
    with patch.object(svc, "_run", return_value=mock_result):
        info = svc.status()
    assert info.running is False


def test_rag_service_restart_composes_stop_and_start() -> None:
    """restart() returns 0 when both stop and start succeed."""
    from archon.platform.macos.search_service import LaunchdSearchService
    svc = LaunchdSearchService()
    with (
        patch.object(svc, "stop", return_value=0),
        patch.object(svc, "start", return_value=0),
    ):
        assert svc.restart() == 0


def test_rag_service_restart_returns_one_if_stop_fails() -> None:
    """restart() returns 1 if stop fails."""
    from archon.platform.macos.search_service import LaunchdSearchService
    svc = LaunchdSearchService()
    with (
        patch.object(svc, "stop", return_value=1),
        patch.object(svc, "start", return_value=0),
    ):
        assert svc.restart() == 1


def test_rag_service_unregister_unloads_before_deleting(tmp_path: Path) -> None:
    """unregister() calls launchctl unload before deleting the plist."""
    from archon.platform.macos.search_service import LaunchdSearchService
    svc = LaunchdSearchService()
    plist = tmp_path / "com.archon.search.plist"
    plist.write_text("<plist/>")
    mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with (
        patch.object(type(svc), "_plist_path", new_callable=lambda: property(lambda self: plist)),
        patch.object(svc, "_is_loaded", return_value=True),
        patch.object(svc, "_run", return_value=mock_result) as mock_run,
    ):
        rc = svc.unregister()
    assert rc == 0
    assert mock_run.call_args[0][0] == ["launchctl", "unload", str(plist)]
    assert not plist.exists()


