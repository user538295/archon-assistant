from __future__ import annotations
import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import archon.cli.update as update_mod


class _UpdateArgs:
    def __init__(self, tag: str | None = None) -> None:
        self.tag = tag


class _VersionArgs:
    pass


def test_run_update_calls_installer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    install_py = app_dir / "install.py"
    install_py.write_text("# fake installer")
    monkeypatch.setattr(update_mod, "_ARCHON_HOME", tmp_path)
    with patch("archon.cli.update.subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        result = update_mod.run_update(_UpdateArgs())
    assert result == 0
    cmd = mock_run.call_args[0][0]
    assert "uv" in cmd
    assert "--update" in cmd
    assert str(install_py) in cmd


def test_run_update_with_tag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "install.py").write_text("# fake")
    monkeypatch.setattr(update_mod, "_ARCHON_HOME", tmp_path)
    with patch("archon.cli.update.subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        update_mod.run_update(_UpdateArgs(tag="26.4.0"))
    cmd = mock_run.call_args[0][0]
    assert "--tag" in cmd
    assert "26.4.0" in cmd


def test_run_update_installer_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(update_mod, "_ARCHON_HOME", tmp_path)
    result = update_mod.run_update(_UpdateArgs())
    assert result == 1
    assert "not found" in capsys.readouterr().out


def test_run_update_propagates_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "install.py").write_text("# fake")
    monkeypatch.setattr(update_mod, "_ARCHON_HOME", tmp_path)
    with patch("archon.cli.update.subprocess.run", return_value=MagicMock(returncode=2)):
        result = update_mod.run_update(_UpdateArgs())
    assert result == 2


def test_run_version_prints_current(capsys: pytest.CaptureFixture) -> None:
    with patch("archon.cli.update.urllib.request.urlopen", side_effect=Exception("offline")):
        with patch("archon.version.get_version", return_value="26.3.5"):
            result = update_mod.run_version(_VersionArgs())
    out = capsys.readouterr().out
    assert "26.3.5" in out
    assert result == 0


def test_run_version_shows_newer_available(capsys: pytest.CaptureFixture) -> None:
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"tag_name": "v26.5.0"}).encode()
    with patch("archon.cli.update.urllib.request.urlopen", return_value=mock_resp):
        with patch("archon.version.get_version", return_value="26.3.5"):
            update_mod.run_version(_VersionArgs())
    out = capsys.readouterr().out
    assert "26.5.0" in out
    assert "archon update" in out


def test_run_version_offline_no_crash(capsys: pytest.CaptureFixture) -> None:
    with patch("archon.cli.update.urllib.request.urlopen", side_effect=ConnectionError("offline")):
        with patch("archon.version.get_version", return_value="26.3.5"):
            result = update_mod.run_version(_VersionArgs())
    assert result == 0


def test_run_version_up_to_date(capsys: pytest.CaptureFixture) -> None:
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"tag_name": "v26.3.5"}).encode()
    with patch("archon.cli.update.urllib.request.urlopen", return_value=mock_resp):
        with patch("archon.version.get_version", return_value="26.3.5"):
            update_mod.run_version(_VersionArgs())
    out = capsys.readouterr().out
    assert "Up to date" in out


def test_run_version_handles_import_error(capsys: pytest.CaptureFixture) -> None:
    with patch("archon.cli.update.urllib.request.urlopen", side_effect=Exception("offline")):
        with patch("archon.version.get_version", side_effect=ImportError("no module")):
            result = update_mod.run_version(_VersionArgs())
    out = capsys.readouterr().out
    assert "unknown" in out
    assert result == 0
