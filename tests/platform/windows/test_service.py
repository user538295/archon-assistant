"""T49 — WindowsService stub tests."""
from __future__ import annotations

import pytest

from archon.platform.windows.service import WindowsService


@pytest.fixture
def svc() -> WindowsService:
    return WindowsService()


class TestWindowsService:
    def test_service_name(self, svc: WindowsService) -> None:
        assert svc.service_name == "windows"

    def test_is_installed_always_false(self, svc: WindowsService) -> None:
        assert svc.is_installed() is False

    def test_start_raises(self, svc: WindowsService) -> None:
        with pytest.raises(NotImplementedError, match="Windows service management"):
            svc.start()

    def test_stop_raises(self, svc: WindowsService) -> None:
        with pytest.raises(NotImplementedError, match="Windows service management"):
            svc.stop()

    def test_restart_raises(self, svc: WindowsService) -> None:
        with pytest.raises(NotImplementedError, match="Windows service management"):
            svc.restart()

    def test_register_raises(self, svc: WindowsService) -> None:
        with pytest.raises(NotImplementedError, match="Windows service management"):
            svc.register()

    def test_unregister_raises(self, svc: WindowsService) -> None:
        with pytest.raises(NotImplementedError, match="Windows service management"):
            svc.unregister()

    def test_status_returns_stopped(self, svc: WindowsService) -> None:
        info = svc.status()
        assert info.running is False
        assert info.label == "archon"

    def test_remediation_hint(self, svc: WindowsService) -> None:
        assert "uv run python main.py" in svc.remediation_hint()

    def test_pre_activate_cleanup_succeeds(self, svc: WindowsService) -> None:
        assert svc.pre_activate_cleanup() == 0
