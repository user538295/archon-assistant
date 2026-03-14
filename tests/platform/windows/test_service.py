"""T49 — WindowsService stub tests."""
from __future__ import annotations

import logging

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

    def test_start_returns_failure_and_warns(self, svc: WindowsService, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="archon"):
            assert svc.start() == 1
        assert "Windows service management not yet supported" in caplog.text

    def test_stop_returns_failure_and_warns(self, svc: WindowsService, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="archon"):
            assert svc.stop() == 1
        assert "Windows service management not yet supported" in caplog.text

    def test_restart_returns_failure_and_warns(self, svc: WindowsService, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="archon"):
            assert svc.restart() == 1
        assert "Windows service management not yet supported" in caplog.text

    def test_register_returns_failure_and_warns(self, svc: WindowsService, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="archon"):
            assert svc.register() == 1
        assert "Windows service management not yet supported" in caplog.text

    def test_unregister_returns_failure_and_warns(self, svc: WindowsService, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="archon"):
            assert svc.unregister() == 1
        assert "Windows service management not yet supported" in caplog.text

    def test_status_returns_stopped(self, svc: WindowsService) -> None:
        info = svc.status()
        assert info.running is False
        assert info.service_name == "archon"

    def test_remediation_hint(self, svc: WindowsService) -> None:
        assert "uv run python main.py" in svc.remediation_hint()

    def test_pre_activate_cleanup_succeeds(self, svc: WindowsService) -> None:
        assert svc.pre_activate_cleanup() == 0
