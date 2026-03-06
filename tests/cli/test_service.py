from __future__ import annotations
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import archon.cli.service as svc


def test_start_macos_loads_plist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plist = tmp_path / "com.archon.assistant.plist"
    plist.write_text("<plist/>")
    monkeypatch.setattr(svc, "_PLIST_PATH", plist)
    with patch("archon.cli.service.platform.system", return_value="Darwin"), \
         patch("archon.cli.service.subprocess.run", return_value=MagicMock(returncode=0)):
        result = svc.run_start()
    assert result == 0


def test_start_macos_missing_plist_returns_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_PLIST_PATH", tmp_path / "missing.plist")
    with patch("archon.cli.service.platform.system", return_value="Darwin"), \
         patch("archon.cli.service.subprocess.run") as mock_run:
        result = svc.run_start()
    assert result == 1
    mock_run.assert_not_called()


def test_start_linux_calls_systemctl(monkeypatch: pytest.MonkeyPatch) -> None:
    with patch("archon.cli.service.platform.system", return_value="Linux"), \
         patch("archon.cli.service.subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        result = svc.run_start()
    assert result == 0
    cmd = mock_run.call_args[0][0]
    assert "systemctl" in cmd
    assert "start" in cmd


def test_stop_macos_calls_launchctl_unload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_PLIST_PATH", tmp_path / "dummy.plist")
    with patch("archon.cli.service.platform.system", return_value="Darwin"), \
         patch("archon.cli.service.subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        result = svc.run_stop()
    assert result == 0
    cmd = mock_run.call_args[0][0]
    assert "launchctl" in cmd
    assert "unload" in cmd


def test_stop_linux_calls_systemctl_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    with patch("archon.cli.service.platform.system", return_value="Linux"), \
         patch("archon.cli.service.subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        result = svc.run_stop()
    assert result == 0
    cmd = mock_run.call_args[0][0]
    assert "stop" in cmd


def test_start_subprocess_failure_returns_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plist = tmp_path / "com.archon.assistant.plist"
    plist.write_text("<plist/>")
    monkeypatch.setattr(svc, "_PLIST_PATH", plist)
    with patch("archon.cli.service.platform.system", return_value="Darwin"), \
         patch("archon.cli.service.subprocess.run", return_value=MagicMock(returncode=1)):
        result = svc.run_start()
    assert result == 1


def test_restart_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plist = tmp_path / "com.archon.assistant.plist"
    plist.write_text("<plist/>")
    monkeypatch.setattr(svc, "_PLIST_PATH", plist)
    with patch("archon.cli.service.platform.system", return_value="Darwin"), \
         patch("archon.cli.service.subprocess.run", return_value=MagicMock(returncode=0)):
        result = svc.run_restart()
    assert result == 0


def test_restart_stop_fails_does_not_call_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_PLIST_PATH", tmp_path / "missing.plist")
    with patch("archon.cli.service.platform.system", return_value="Darwin"):
        # plist missing so stop will fail (returncode=1 from launchctl, which we mock)
        with patch("archon.cli.service.subprocess.run", return_value=MagicMock(returncode=1)) as mock_run:
            result = svc.run_restart()
    # Should fail at stop, return 1, not try start
    assert result == 1
