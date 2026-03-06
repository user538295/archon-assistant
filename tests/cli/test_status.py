from __future__ import annotations
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import archon.cli.status as status_mod
from archon.cli.status import ServiceInfo, HealthInfo


def test_run_status_running_returns_0(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(status_mod, "_get_service_info",
                        lambda: ServiceInfo(running=True, pid=1234, uptime="1h"))
    monkeypatch.setattr(status_mod, "_check_health",
                        lambda h, p: HealthInfo(reachable=True, latency_ms=10))
    monkeypatch.setattr(status_mod, "_load_config_raw", lambda: {})
    result = status_mod.run_status(None)
    assert result == 0


def test_run_status_stopped_shows_stopped(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(status_mod, "_get_service_info",
                        lambda: ServiceInfo(running=False, pid=None, uptime=None))
    monkeypatch.setattr(status_mod, "_check_health",
                        lambda h, p: HealthInfo(reachable=False, latency_ms=None))
    monkeypatch.setattr(status_mod, "_load_config_raw", lambda: {})
    status_mod.run_status(None)
    out = capsys.readouterr().out
    assert "stopped" in out


def test_run_status_running_shows_pid(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(status_mod, "_get_service_info",
                        lambda: ServiceInfo(running=True, pid=5678, uptime="2h"))
    monkeypatch.setattr(status_mod, "_check_health",
                        lambda h, p: HealthInfo(reachable=True, latency_ms=5))
    monkeypatch.setattr(status_mod, "_load_config_raw", lambda: {})
    status_mod.run_status(None)
    out = capsys.readouterr().out
    assert "5678" in out


def test_run_status_health_shown(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(status_mod, "_get_service_info",
                        lambda: ServiceInfo(running=True, pid=1, uptime="1m"))
    monkeypatch.setattr(status_mod, "_check_health",
                        lambda h, p: HealthInfo(reachable=True, latency_ms=15))
    monkeypatch.setattr(status_mod, "_load_config_raw", lambda: {})
    status_mod.run_status(None)
    out = capsys.readouterr().out
    assert "✔" in out
    assert "15ms" in out


def test_run_status_health_unreachable(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(status_mod, "_get_service_info",
                        lambda: ServiceInfo(running=True, pid=1, uptime=None))
    monkeypatch.setattr(status_mod, "_check_health",
                        lambda h, p: HealthInfo(reachable=False, latency_ms=None))
    monkeypatch.setattr(status_mod, "_load_config_raw", lambda: {})
    status_mod.run_status(None)
    out = capsys.readouterr().out
    assert "✗" in out


def test_get_service_info_macos_running() -> None:
    launchctl_output = '"PID" = 12345;\n"Label" = "com.archon.assistant";'
    with patch("archon.cli.status.platform.system", return_value="Darwin"), \
         patch("archon.cli.status.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=launchctl_output)
        result = status_mod._get_service_info()
    assert result.running is True
    assert result.pid == 12345


def test_get_service_info_macos_stopped() -> None:
    with patch("archon.cli.status.platform.system", return_value="Darwin"), \
         patch("archon.cli.status.subprocess.run",
               return_value=MagicMock(returncode=1, stdout="")):
        result = status_mod._get_service_info()
    assert result.running is False


def test_get_service_info_linux_running() -> None:
    def fake_run(cmd, **kw):
        if "is-active" in cmd:
            return MagicMock(returncode=0, stdout="active\n")
        if "show" in cmd:
            return MagicMock(returncode=0, stdout="MainPID=9999\n")
        return MagicMock(returncode=0, stdout="")
    with patch("archon.cli.status.platform.system", return_value="Linux"), \
         patch("archon.cli.status.subprocess.run", side_effect=fake_run):
        result = status_mod._get_service_info()
    assert result.running is True


def test_get_service_info_linux_stopped() -> None:
    with patch("archon.cli.status.platform.system", return_value="Linux"), \
         patch("archon.cli.status.subprocess.run",
               return_value=MagicMock(returncode=1, stdout="inactive\n")):
        result = status_mod._get_service_info()
    assert result.running is False


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


def test_count_plugins_counts_dirs(tmp_path: Path) -> None:
    for name in ("plugin_a", "plugin_b", "plugin_c"):
        (tmp_path / name).mkdir()
    (tmp_path / "not_a_dir.txt").write_text("x")
    count = status_mod._count_plugins(str(tmp_path))
    assert count == 3


def test_count_plugins_missing_dir() -> None:
    count = status_mod._count_plugins("/nonexistent/path/that/does/not/exist")
    assert count == 0


def test_load_config_raw_returns_empty_on_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(status_mod, "_CONFIG_PATH", tmp_path / "nonexistent.toml")
    result = status_mod._load_config_raw()
    assert result == {}
