"""Tests for platform detection, singletons, override, and reset."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from archon.platform import get_runtime, get_service, override, reset


def test_darwin_returns_launchd_service():
    with patch("archon.platform.sys") as mock_sys:
        mock_sys.platform = "darwin"
        svc = get_service()
    from archon.platform.macos.service import LaunchdService
    assert isinstance(svc, LaunchdService)


def test_linux_returns_systemd_service():
    with patch("archon.platform.sys") as mock_sys:
        mock_sys.platform = "linux"
        svc = get_service()
    from archon.platform.linux.service import SystemdService
    assert isinstance(svc, SystemdService)


def test_unsupported_platform_raises():
    with patch("archon.platform.sys") as mock_sys:
        mock_sys.platform = "freebsd"
        with pytest.raises(NotImplementedError, match="freebsd"):
            get_service()


def test_service_singleton():
    with patch("archon.platform.sys") as mock_sys:
        mock_sys.platform = "darwin"
        a = get_service()
        b = get_service()
    assert a is b


def test_runtime_singleton():
    with patch("archon.platform.sys") as mock_sys:
        mock_sys.platform = "darwin"
        a = get_runtime()
        b = get_runtime()
    assert a is b


def test_override_service():
    mock_svc = MagicMock()
    override(service=mock_svc)
    assert get_service() is mock_svc


def test_override_runtime():
    mock_rt = MagicMock()
    override(runtime=mock_rt)
    assert get_runtime() is mock_rt


def test_reset_clears_singletons():
    override(service=MagicMock())
    reset()
    with patch("archon.platform.sys") as mock_sys:
        mock_sys.platform = "darwin"
        svc = get_service()
    from archon.platform.macos.service import LaunchdService
    assert isinstance(svc, LaunchdService)


def test_override_service_does_not_affect_runtime():
    mock_svc = MagicMock()
    override(service=mock_svc)
    with patch("archon.platform.sys") as mock_sys:
        mock_sys.platform = "darwin"
        rt = get_runtime()
    from archon.platform.macos.runtime import MacRuntime
    assert isinstance(rt, MacRuntime)


def test_no_leakage_between_tests():
    """After reset(), get_service() re-detects from platform."""
    with patch("archon.platform.sys") as mock_sys:
        mock_sys.platform = "darwin"
        svc = get_service()
    from archon.platform.macos.service import LaunchdService
    assert isinstance(svc, LaunchdService)


# ── T51: Windows detection ────────────────────────────────────────────


def test_win32_returns_windows_service():
    with patch("archon.platform.sys") as mock_sys:
        mock_sys.platform = "win32"
        svc = get_service()
    from archon.platform.windows.service import WindowsService
    assert isinstance(svc, WindowsService)


def test_win32_returns_windows_runtime():
    with patch("archon.platform.sys") as mock_sys:
        mock_sys.platform = "win32"
        rt = get_runtime()
    from archon.platform.windows.runtime import WindowsRuntime
    assert isinstance(rt, WindowsRuntime)


# ── Linux runtime detection ──────────────────────────────────────────


def test_linux_returns_linux_runtime():
    with patch("archon.platform.sys") as mock_sys:
        mock_sys.platform = "linux"
        rt = get_runtime()
    from archon.platform.linux.runtime import LinuxRuntime
    assert isinstance(rt, LinuxRuntime)


def test_unsupported_platform_raises_for_runtime():
    with patch("archon.platform.sys") as mock_sys:
        mock_sys.platform = "freebsd"
        with pytest.raises(NotImplementedError, match="freebsd"):
            get_runtime()


# ── Sentinel-based override ──────────────────────────────────────────


def test_override_none_clears_service():
    """override(service=None) should clear the service singleton."""
    mock_svc = MagicMock()
    override(service=mock_svc)
    assert get_service() is mock_svc

    override(service=None)
    with patch("archon.platform.sys") as mock_sys:
        mock_sys.platform = "darwin"
        svc = get_service()
    from archon.platform.macos.service import LaunchdService
    assert isinstance(svc, LaunchdService)


def test_override_none_clears_runtime():
    """override(runtime=None) should clear the runtime singleton."""
    mock_rt = MagicMock()
    override(runtime=mock_rt)
    assert get_runtime() is mock_rt

    override(runtime=None)
    with patch("archon.platform.sys") as mock_sys:
        mock_sys.platform = "darwin"
        rt = get_runtime()
    from archon.platform.macos.runtime import MacRuntime
    assert isinstance(rt, MacRuntime)
