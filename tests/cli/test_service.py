from __future__ import annotations
import pytest
from unittest.mock import patch, MagicMock, call
from pathlib import Path
import archon.cli.service as svc


@pytest.fixture
def plist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "com.archon.assistant.plist"
    p.write_text("<plist/>")
    monkeypatch.setattr(svc, "_PLIST_PATH", p)
    return p


# ── run_start ─────────────────────────────────────────────────────────────────

def test_start_macos_loads_plist(plist: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_macos_is_loaded", lambda: False)
    with patch("archon.cli.service.platform.system", return_value="Darwin"), \
         patch("archon.cli.service.subprocess.run", return_value=MagicMock(returncode=0)):
        assert svc.run_start() == 0


def test_start_macos_missing_plist_returns_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_PLIST_PATH", tmp_path / "missing.plist")
    with patch("archon.cli.service.platform.system", return_value="Darwin"), \
         patch("archon.cli.service.subprocess.run") as mock_run:
        assert svc.run_start() == 1
    mock_run.assert_not_called()


def test_start_already_loaded_returns_0(plist: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(svc, "_macos_is_loaded", lambda: True)
    with patch("archon.cli.service.platform.system", return_value="Darwin"), \
         patch("archon.cli.service.subprocess.run") as mock_run:
        assert svc.run_start() == 0
    # launchctl load must NOT be called
    mock_run.assert_not_called()
    assert "already loaded" in capsys.readouterr().out


def test_start_linux_calls_systemctl(monkeypatch: pytest.MonkeyPatch) -> None:
    with patch("archon.cli.service.platform.system", return_value="Linux"), \
         patch("archon.cli.service.subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        assert svc.run_start() == 0
    cmd = mock_run.call_args[0][0]
    assert "systemctl" in cmd and "start" in cmd


def test_start_subprocess_failure_returns_1(plist: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_macos_is_loaded", lambda: False)
    with patch("archon.cli.service.platform.system", return_value="Darwin"), \
         patch("archon.cli.service.subprocess.run", return_value=MagicMock(returncode=1)):
        assert svc.run_start() == 1


def test_start_launchctl_not_found_returns_1(plist: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_macos_is_loaded", lambda: False)
    with patch("archon.cli.service.platform.system", return_value="Darwin"), \
         patch("archon.cli.service.subprocess.run", side_effect=FileNotFoundError):
        assert svc.run_start() == 1


def test_start_systemctl_not_found_returns_1(monkeypatch: pytest.MonkeyPatch) -> None:
    with patch("archon.cli.service.platform.system", return_value="Linux"), \
         patch("archon.cli.service.subprocess.run", side_effect=FileNotFoundError):
        assert svc.run_start() == 1


# ── run_stop ──────────────────────────────────────────────────────────────────

def test_stop_macos_calls_launchctl_unload(plist: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_macos_is_loaded", lambda: True)
    with patch("archon.cli.service.platform.system", return_value="Darwin"), \
         patch("archon.cli.service.subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        assert svc.run_stop() == 0
    cmd = mock_run.call_args[0][0]
    assert "launchctl" in cmd and "unload" in cmd


def test_stop_macos_missing_plist_but_loaded_uses_bootout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_PLIST_PATH", tmp_path / "missing.plist")
    monkeypatch.setattr(svc, "_macos_is_loaded", lambda: True)
    with patch("archon.cli.service.platform.system", return_value="Darwin"), \
         patch("archon.cli.service.subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        assert svc.run_stop() == 0
    cmd = mock_run.call_args[0][0]
    assert "launchctl" in cmd and "bootout" in cmd


def test_stop_already_stopped_returns_0(plist: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(svc, "_macos_is_loaded", lambda: False)
    with patch("archon.cli.service.platform.system", return_value="Darwin"), \
         patch("archon.cli.service.subprocess.run") as mock_run:
        assert svc.run_stop() == 0
    mock_run.assert_not_called()
    assert "not loaded" in capsys.readouterr().out


def test_stop_linux_calls_systemctl_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    with patch("archon.cli.service.platform.system", return_value="Linux"), \
         patch("archon.cli.service.subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        assert svc.run_stop() == 0
    cmd = mock_run.call_args[0][0]
    assert "stop" in cmd


def test_stop_launchctl_not_found_returns_1(plist: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_macos_is_loaded", lambda: True)
    with patch("archon.cli.service.platform.system", return_value="Darwin"), \
         patch("archon.cli.service.subprocess.run", side_effect=FileNotFoundError):
        assert svc.run_stop() == 1


# ── run_restart ───────────────────────────────────────────────────────────────

def test_restart_success(plist: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_macos_is_loaded", lambda: True)
    with patch("archon.cli.service.platform.system", return_value="Darwin"), \
         patch("archon.cli.service.subprocess.run", return_value=MagicMock(returncode=0)):
        assert svc.run_restart() == 0


def test_restart_when_service_stopped_still_succeeds(plist: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """Restart should work even when service is already stopped (no unload needed)."""
    monkeypatch.setattr(svc, "_macos_is_loaded", lambda: False)
    with patch("archon.cli.service.platform.system", return_value="Darwin"), \
         patch("archon.cli.service.subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        assert svc.run_restart() == 0
    # Only one subprocess call: launchctl load (no unload since not running)
    assert mock_run.call_count == 1
    assert "load" in mock_run.call_args[0][0]
    assert "restarted" in capsys.readouterr().out


def test_restart_prints_only_restarted_not_started(plist: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """Restart must NOT print 'Archon started' — only 'Archon restarted'."""
    monkeypatch.setattr(svc, "_macos_is_loaded", lambda: True)
    with patch("archon.cli.service.platform.system", return_value="Darwin"), \
         patch("archon.cli.service.subprocess.run", return_value=MagicMock(returncode=0)):
        svc.run_restart()
    out = capsys.readouterr().out
    assert "restarted" in out
    assert "Archon started" not in out


def test_restart_missing_plist_returns_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_PLIST_PATH", tmp_path / "missing.plist")
    with patch("archon.cli.service.platform.system", return_value="Darwin"):
        assert svc.run_restart() == 1


def test_restart_unload_failure_returns_1(plist: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_macos_is_loaded", lambda: True)
    with patch("archon.cli.service.platform.system", return_value="Darwin"), \
         patch("archon.cli.service.subprocess.run", return_value=MagicMock(returncode=1)):
        assert svc.run_restart() == 1


def test_restart_linux_success(monkeypatch: pytest.MonkeyPatch) -> None:
    with patch("archon.cli.service.platform.system", return_value="Linux"), \
         patch("archon.cli.service.subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        assert svc.run_restart() == 0
    cmd = mock_run.call_args[0][0]
    assert "systemctl" in cmd and "restart" in cmd


def test_restart_linux_failure_returns_1(monkeypatch: pytest.MonkeyPatch) -> None:
    with patch("archon.cli.service.platform.system", return_value="Linux"), \
         patch("archon.cli.service.subprocess.run", return_value=MagicMock(returncode=1)):
        assert svc.run_restart() == 1


def test_restart_linux_not_found_returns_1(monkeypatch: pytest.MonkeyPatch) -> None:
    with patch("archon.cli.service.platform.system", return_value="Linux"), \
         patch("archon.cli.service.subprocess.run", side_effect=FileNotFoundError):
        assert svc.run_restart() == 1


def test_macos_is_loaded_returns_false_when_launchctl_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """_macos_is_loaded() must return False (not crash) when launchctl is not found."""
    with patch("archon.cli.service.subprocess.run", side_effect=FileNotFoundError):
        assert svc._macos_is_loaded() is False


def test_macos_is_loaded_returns_true_when_loaded() -> None:
    with patch("archon.cli.service.subprocess.run", return_value=MagicMock(returncode=0)):
        assert svc._macos_is_loaded() is True


def test_macos_is_loaded_returns_false_when_not_loaded() -> None:
    with patch("archon.cli.service.subprocess.run", return_value=MagicMock(returncode=1)):
        assert svc._macos_is_loaded() is False


def test_stop_systemctl_not_found_returns_1(monkeypatch: pytest.MonkeyPatch) -> None:
    with patch("archon.cli.service.platform.system", return_value="Linux"), \
         patch("archon.cli.service.subprocess.run", side_effect=FileNotFoundError):
        assert svc.run_stop() == 1


def test_stop_subprocess_failure_returns_1(plist: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_macos_is_loaded", lambda: True)
    with patch("archon.cli.service.platform.system", return_value="Darwin"), \
         patch("archon.cli.service.subprocess.run", return_value=MagicMock(returncode=1)):
        assert svc.run_stop() == 1


def test_restart_unload_launchctl_not_found_returns_1(plist: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_macos_is_loaded", lambda: True)
    with patch("archon.cli.service.platform.system", return_value="Darwin"), \
         patch("archon.cli.service.subprocess.run", side_effect=FileNotFoundError):
        assert svc.run_restart() == 1


def test_restart_load_launchctl_not_found_returns_1(plist: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unload succeeds but load raises FileNotFoundError."""
    monkeypatch.setattr(svc, "_macos_is_loaded", lambda: True)
    responses = [MagicMock(returncode=0), FileNotFoundError()]
    with patch("archon.cli.service.platform.system", return_value="Darwin"), \
         patch("archon.cli.service.subprocess.run", side_effect=responses):
        assert svc.run_restart() == 1


def test_restart_load_failure_returns_1(plist: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unload succeeds (rc=0) but load returns rc=1."""
    monkeypatch.setattr(svc, "_macos_is_loaded", lambda: True)
    responses = [MagicMock(returncode=0), MagicMock(returncode=1)]
    with patch("archon.cli.service.platform.system", return_value="Darwin"), \
         patch("archon.cli.service.subprocess.run", side_effect=responses):
        assert svc.run_restart() == 1


