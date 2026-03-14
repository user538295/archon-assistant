from __future__ import annotations

import pytest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import archon.platform as platform_mod
from archon.platform.service import PlatformService
from archon.platform.types import ServiceInfo
from archon.cli.status import HealthInfo
import archon.cli.status as status_mod


@dataclass
class _StubService:
    """Minimal stub implementing the two methods status.py needs."""

    _info: ServiceInfo
    _name: str = "launchd"

    @property
    def service_name(self) -> str:
        return self._name

    def status(self) -> ServiceInfo:
        return self._info


@pytest.fixture(autouse=True)
def _reset_platform() -> None:  # type: ignore[misc]
    """Ensure a clean platform singleton for every test."""
    platform_mod.reset()
    yield  # type: ignore[misc]
    platform_mod.reset()


# ── run_status integration ──────────────────────────────────────────


def test_run_status_shows_service_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    info = ServiceInfo(running=True, service_name="com.archon.assistant", pid=100, uptime="5m")
    platform_mod.override(service=_StubService(_info=info, _name="launchd"))  # type: ignore[arg-type]
    monkeypatch.setattr(status_mod, "_check_health",
                        lambda h, p: HealthInfo(reachable=True, latency_ms=1))
    monkeypatch.setattr(status_mod, "_load_config_raw", lambda: {})

    status_mod.run_status(None)
    out = capsys.readouterr().out
    assert "launchd" in out


def test_run_status_running_shows_pid_and_uptime(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    info = ServiceInfo(running=True, service_name="com.archon.assistant", pid=5678, uptime="2h")
    platform_mod.override(service=_StubService(_info=info))  # type: ignore[arg-type]
    monkeypatch.setattr(status_mod, "_check_health",
                        lambda h, p: HealthInfo(reachable=True, latency_ms=5))
    monkeypatch.setattr(status_mod, "_load_config_raw", lambda: {})

    status_mod.run_status(None)
    out = capsys.readouterr().out
    assert "5678" in out
    assert "2h" in out


def test_run_status_stopped_shows_stopped(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    info = ServiceInfo(running=False, service_name="com.archon.assistant")
    platform_mod.override(service=_StubService(_info=info))  # type: ignore[arg-type]
    monkeypatch.setattr(status_mod, "_check_health",
                        lambda h, p: HealthInfo(reachable=False, latency_ms=None))
    monkeypatch.setattr(status_mod, "_load_config_raw", lambda: {})

    status_mod.run_status(None)
    out = capsys.readouterr().out
    assert "stopped" in out
    # Service line should NOT appear when stopped
    assert "Service" not in out


def test_run_status_output_format(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    info = ServiceInfo(running=True, service_name="archon", pid=42, uptime="10m")
    platform_mod.override(service=_StubService(_info=info, _name="systemd"))  # type: ignore[arg-type]
    monkeypatch.setattr(status_mod, "_check_health",
                        lambda h, p: HealthInfo(reachable=True, latency_ms=15))
    monkeypatch.setattr(status_mod, "_load_config_raw", lambda: {})

    result = status_mod.run_status(None)
    out = capsys.readouterr().out

    assert result == 0
    assert "●" in out
    assert "running" in out
    assert "systemd" in out
    assert "PID 42" in out
    assert "uptime 10m" in out
    assert "✔" in out
    assert "15ms" in out


def test_run_status_health_unreachable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    info = ServiceInfo(running=True, service_name="com.archon.assistant", pid=1)
    platform_mod.override(service=_StubService(_info=info))  # type: ignore[arg-type]
    monkeypatch.setattr(status_mod, "_check_health",
                        lambda h, p: HealthInfo(reachable=False, latency_ms=None))
    monkeypatch.setattr(status_mod, "_load_config_raw", lambda: {})

    status_mod.run_status(None)
    out = capsys.readouterr().out
    assert "✗" in out
    assert "unreachable" in out


# ── _check_health ───────────────────────────────────────────────────


def test_check_health_success() -> None:
    mock_resp = MagicMock()
    mock_resp.status = 200
    with patch("archon.cli.status.urllib.request.urlopen", return_value=mock_resp):
        result = status_mod._check_health("localhost", 18182)
    assert result.reachable is True
    assert result.latency_ms is not None
    assert isinstance(result.latency_ms, int)


def test_check_health_failure() -> None:
    with patch("archon.cli.status.urllib.request.urlopen", side_effect=Exception("refused")):
        result = status_mod._check_health("localhost", 18182)
    assert result.reachable is False
    assert result.latency_ms is None


# ── _count_plugins ──────────────────────────────────────────────────


def test_count_plugins_counts_dirs(tmp_path: Path) -> None:
    for name in ("plugin_a", "plugin_b", "plugin_c"):
        (tmp_path / name).mkdir()
    (tmp_path / "not_a_dir.txt").write_text("x")
    count = status_mod._count_plugins(str(tmp_path))
    assert count == 3


def test_count_plugins_missing_dir() -> None:
    count = status_mod._count_plugins("/nonexistent/path/that/does/not/exist")
    assert count == 0


# ── _load_config_raw ────────────────────────────────────────────────


def test_load_config_raw_returns_empty_on_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(status_mod, "_CONFIG_PATH", tmp_path / "nonexistent.toml")
    result = status_mod._load_config_raw()
    assert result == {}


# ── error handling ─────────────────────────────────────────────────


def test_run_status_get_service_raises_notimplemented(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """When get_service() raises NotImplementedError, print error and return 1."""
    platform_mod.reset()

    def _raise() -> None:
        raise NotImplementedError("Unsupported platform: freebsd")

    monkeypatch.setattr(status_mod, "get_service", _raise)
    result = status_mod.run_status(None)
    assert result == 1
    out = capsys.readouterr().out
    assert "Unsupported platform" in out


def test_run_status_service_exception_with_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """When get_service() returns but status() raises, show hint if available."""
    mock = MagicMock()
    mock.status.side_effect = NotImplementedError("not supported")
    mock.remediation_hint.return_value = "Run manually"
    platform_mod.override(service=mock)  # type: ignore[arg-type]

    monkeypatch.setattr(status_mod, "_load_config_raw", lambda: {})

    result = status_mod.run_status(None)
    assert result == 1
    out = capsys.readouterr().out
    assert "not supported" in out
    assert "Run manually" in out
