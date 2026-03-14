"""T38 — Live E2E test exercising real OS service management.

Marked @pytest.mark.live — excluded from default runs.
Run with: uv run pytest -m live
"""
from __future__ import annotations

import sys

import pytest

from archon.platform import get_service, reset


pytestmark = [pytest.mark.live]


@pytest.fixture(autouse=True)
def _reset():
    yield
    reset()


@pytest.fixture
def service():
    """Get the platform service for the current OS and ensure clean state."""
    svc = get_service()
    # Pre-test cleanup — clear stale state from prior interrupted runs
    try:
        svc.stop()
    except Exception:
        pass
    try:
        svc.unregister()
    except Exception:
        pass
    yield svc
    # Post-test cleanup
    try:
        svc.stop()
    except Exception:
        pass
    try:
        svc.unregister()
    except Exception:
        pass


class TestLiveServiceLifecycle:
    """Full lifecycle: register → start → status → stop → unregister."""

    def test_register_creates_service_file(self, service) -> None:
        rc = service.register()
        assert rc == 0
        assert service.is_installed()

    def test_start_and_status(self, service) -> None:
        service.register()
        rc = service.start()
        assert rc == 0

        info = service.status()
        assert info.running is True
        assert info.pid is not None
        assert info.pid > 0

    def test_stop(self, service) -> None:
        service.register()
        service.start()
        rc = service.stop()
        assert rc == 0

        info = service.status()
        assert info.running is False

    def test_unregister_removes_service_file(self, service) -> None:
        service.register()
        service.start()
        service.stop()
        rc = service.unregister()
        assert rc == 0
        assert not service.is_installed()
