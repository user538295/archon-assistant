"""Tests for cli/service.py — service lifecycle commands via platform delegation."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from archon.cli import service as svc
from archon.platform import override, reset


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    reset()


def _mock_service(**status_overrides: object) -> MagicMock:
    """Create a mock PlatformService for override()."""
    mock = MagicMock()
    mock.start.return_value = 0
    mock.stop.return_value = 0
    mock.restart.return_value = 0
    for k, v in status_overrides.items():
        getattr(mock, k).return_value = v
    return mock


# ── run_start ─────────────────────────────────────────────────────────────────


def test_start_success(capsys: pytest.CaptureFixture) -> None:
    mock = _mock_service(start=0)
    override(service=mock)
    assert svc.run_start() == 0
    mock.start.assert_called_once()
    assert "Archon started" in capsys.readouterr().out


def test_start_failure(capsys: pytest.CaptureFixture) -> None:
    mock = _mock_service(start=1)
    override(service=mock)
    assert svc.run_start() == 1
    assert "Failed to start" in capsys.readouterr().out


def test_start_already_running_returns_0(capsys: pytest.CaptureFixture) -> None:
    """Platform service returns 0 for already-running — CLI should also return 0."""
    mock = _mock_service(start=0)
    override(service=mock)
    assert svc.run_start() == 0
    assert "Archon started" in capsys.readouterr().out


# ── run_stop ──────────────────────────────────────────────────────────────────


def test_stop_success(capsys: pytest.CaptureFixture) -> None:
    mock = _mock_service(stop=0)
    override(service=mock)
    assert svc.run_stop() == 0
    mock.stop.assert_called_once()
    assert "Archon stopped" in capsys.readouterr().out


def test_stop_failure(capsys: pytest.CaptureFixture) -> None:
    mock = _mock_service(stop=1)
    override(service=mock)
    assert svc.run_stop() == 1
    assert "Failed to stop" in capsys.readouterr().out


def test_stop_already_stopped_returns_0(capsys: pytest.CaptureFixture) -> None:
    """Platform service returns 0 for already-stopped — CLI should also return 0."""
    mock = _mock_service(stop=0)
    override(service=mock)
    assert svc.run_stop() == 0
    assert "Archon stopped" in capsys.readouterr().out


# ── run_restart ───────────────────────────────────────────────────────────────


def test_restart_success(capsys: pytest.CaptureFixture) -> None:
    mock = _mock_service(restart=0)
    override(service=mock)
    assert svc.run_restart() == 0
    mock.restart.assert_called_once()
    assert "Archon restarted" in capsys.readouterr().out


def test_restart_failure(capsys: pytest.CaptureFixture) -> None:
    mock = _mock_service(restart=1)
    override(service=mock)
    assert svc.run_restart() == 1
    assert "Failed to restart" in capsys.readouterr().out


def test_restart_prints_restarted_not_started(capsys: pytest.CaptureFixture) -> None:
    """Restart must print 'Archon restarted', not 'Archon started'."""
    mock = _mock_service(restart=0)
    override(service=mock)
    svc.run_restart()
    out = capsys.readouterr().out
    assert "restarted" in out
    assert "Archon started" not in out


# ── error handling ───────────────────────────────────────────────────────────


def _raising_service(error: Exception) -> MagicMock:
    """Create a mock service where get_service() itself raises."""
    mock = MagicMock()
    mock.start.side_effect = error
    mock.stop.side_effect = error
    mock.restart.side_effect = error
    mock.remediation_hint.return_value = "Run manually instead"
    return mock


def test_start_notimplemented_returns_1(capsys: pytest.CaptureFixture) -> None:
    mock = _raising_service(NotImplementedError("unsupported"))
    override(service=mock)
    assert svc.run_start() == 1
    out = capsys.readouterr().out
    assert "unsupported" in out
    assert "Run manually instead" in out


def test_stop_notimplemented_returns_1(capsys: pytest.CaptureFixture) -> None:
    mock = _raising_service(NotImplementedError("unsupported"))
    override(service=mock)
    assert svc.run_stop() == 1
    out = capsys.readouterr().out
    assert "unsupported" in out


def test_restart_notimplemented_returns_1(capsys: pytest.CaptureFixture) -> None:
    mock = _raising_service(NotImplementedError("unsupported"))
    override(service=mock)
    assert svc.run_restart() == 1
    out = capsys.readouterr().out
    assert "unsupported" in out


def test_start_generic_exception_returns_1(capsys: pytest.CaptureFixture) -> None:
    mock = _raising_service(RuntimeError("something broke"))
    override(service=mock)
    assert svc.run_start() == 1
    out = capsys.readouterr().out
    assert "something broke" in out


def test_get_service_raises_notimplemented(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    """When get_service() itself raises (unsupported platform), handle gracefully."""
    reset()
    monkeypatch.setattr(svc, "get_service", lambda: (_ for _ in ()).throw(NotImplementedError("Unsupported platform: freebsd")))
    assert svc.run_start() == 1
    out = capsys.readouterr().out
    assert "Unsupported platform" in out
