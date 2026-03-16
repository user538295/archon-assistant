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


def test_run_update_resolves_latest_tag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    install_py = app_dir / "install.py"
    install_py.write_text("# fake installer")
    monkeypatch.setattr(update_mod, "_ARCHON_HOME", tmp_path)
    with patch("archon.cli.update._fetch_latest_tag", return_value="26.4.100"):
        with patch("archon.version.get_version", return_value="26.3.0"):
            with patch("archon.cli.update.subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
                result = update_mod.run_update(_UpdateArgs())
    assert result == 0
    cmd = mock_run.call_args[0][0]
    assert "uv" in cmd
    assert "--update" in cmd
    assert "--tag" in cmd
    assert "26.4.100" in cmd
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
    with patch("archon.cli.update._fetch_latest_tag", return_value="26.4.0"):
        with patch("archon.version.get_version", return_value="26.3.0"):
            with patch("archon.cli.update.subprocess.run", return_value=MagicMock(returncode=2)):
                result = update_mod.run_update(_UpdateArgs())
    assert result == 2


def test_run_update_uv_not_in_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "install.py").write_text("# fake")
    monkeypatch.setattr(update_mod, "_ARCHON_HOME", tmp_path)
    with patch("archon.cli.update._fetch_latest_tag", return_value="26.4.0"):
        with patch("archon.version.get_version", return_value="26.3.0"):
            with patch("archon.cli.update.subprocess.run", side_effect=FileNotFoundError("uv not found")):
                result = update_mod.run_update(_UpdateArgs())
    assert result == 1
    out = capsys.readouterr().out
    assert "uv" in out
    assert "PATH" in out
    assert "https://docs.astral.sh/uv/" in out


def test_run_update_fetch_failure_returns_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "install.py").write_text("# fake")
    monkeypatch.setattr(update_mod, "_ARCHON_HOME", tmp_path)
    with patch("archon.cli.update._fetch_latest_tag", return_value=None):
        result = update_mod.run_update(_UpdateArgs())
    assert result == 1
    out = capsys.readouterr().out
    assert "could not fetch" in out


def test_run_update_explicit_tag_skips_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "install.py").write_text("# fake")
    monkeypatch.setattr(update_mod, "_ARCHON_HOME", tmp_path)
    with patch("archon.cli.update._fetch_latest_tag") as mock_fetch:
        with patch("archon.cli.update.subprocess.run", return_value=MagicMock(returncode=0)):
            update_mod.run_update(_UpdateArgs(tag="26.5.0"))
    mock_fetch.assert_not_called()


def test_parse_version_numeric_comparison() -> None:
    """Verify that version comparison is numeric, not lexicographic."""
    assert update_mod._parse_version("26.3.330") > update_mod._parse_version("26.3.300")
    assert update_mod._parse_version("26.3.9") < update_mod._parse_version("26.3.10")
    assert update_mod._parse_version("26.4.0") > update_mod._parse_version("26.3.999")
    assert update_mod._parse_version("unknown") == (0,)


def test_run_update_skips_when_already_up_to_date(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """Downgrade protection: don't update if current >= latest."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "install.py").write_text("# fake")
    monkeypatch.setattr(update_mod, "_ARCHON_HOME", tmp_path)
    with patch("archon.cli.update._fetch_latest_tag", return_value="26.3.300"):
        with patch("archon.version.get_version", return_value="26.3.330"):
            with patch("archon.cli.update.subprocess.run") as mock_run:
                result = update_mod.run_update(_UpdateArgs())
    assert result == 0
    mock_run.assert_not_called()
    assert "Already up to date" in capsys.readouterr().out


def test_run_update_explicit_tag_bypasses_downgrade_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit --tag should always proceed, even if it's older."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "install.py").write_text("# fake")
    monkeypatch.setattr(update_mod, "_ARCHON_HOME", tmp_path)
    with patch("archon.cli.update.subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        update_mod.run_update(_UpdateArgs(tag="26.3.100"))
    mock_run.assert_called_once()


def test_fetch_latest_tag_from_releases() -> None:
    """When releases endpoint returns a tag, use it."""
    with patch.object(update_mod, "_fetch_from_releases", return_value="26.4.5"):
        result = update_mod._fetch_latest_tag()
    assert result == "26.4.5"


def test_fetch_latest_tag_falls_back_to_tags_api() -> None:
    """When releases endpoint fails, fall back to tags API."""
    with patch.object(update_mod, "_fetch_from_releases", return_value=None):
        with patch.object(update_mod, "_fetch_from_tags", return_value="26.3.368"):
            result = update_mod._fetch_latest_tag()
    assert result == "26.3.368"


def test_fetch_latest_tag_returns_none_when_both_fail() -> None:
    """When both endpoints fail, return None."""
    with patch.object(update_mod, "_fetch_from_releases", return_value=None):
        with patch.object(update_mod, "_fetch_from_tags", return_value=None):
            assert update_mod._fetch_latest_tag() is None


def test_fetch_from_releases_parses_response() -> None:
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"tag_name": "v26.4.5"}).encode()
    with patch("archon.cli.update.urllib.request.urlopen", return_value=mock_resp):
        assert update_mod._fetch_from_releases() == "26.4.5"


def test_fetch_from_releases_returns_none_on_error() -> None:
    with patch("archon.cli.update.urllib.request.urlopen", side_effect=ConnectionError("offline")):
        assert update_mod._fetch_from_releases() is None


def test_fetch_from_tags_parses_response() -> None:
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps([{"name": "v26.3.368"}]).encode()
    with patch("archon.cli.update.urllib.request.urlopen", return_value=mock_resp):
        assert update_mod._fetch_from_tags() == "26.3.368"


def test_fetch_from_tags_picks_highest_version() -> None:
    """Tags API sorts by commit date; we must pick the highest version."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps([
        {"name": "v26.2.100"},  # hotfix on older commit, listed first
        {"name": "v26.3.368"},  # actual latest
        {"name": "v26.3.350"},
    ]).encode()
    with patch("archon.cli.update.urllib.request.urlopen", return_value=mock_resp):
        assert update_mod._fetch_from_tags() == "26.3.368"


def test_fetch_from_tags_skips_non_version_tags() -> None:
    """Non-version tags (e.g. 'stable') parse to (0,) and are ignored."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps([
        {"name": "stable"},
        {"name": "v26.3.100"},
    ]).encode()
    with patch("archon.cli.update.urllib.request.urlopen", return_value=mock_resp):
        assert update_mod._fetch_from_tags() == "26.3.100"


def test_fetch_from_tags_returns_none_on_empty_list() -> None:
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps([]).encode()
    with patch("archon.cli.update.urllib.request.urlopen", return_value=mock_resp):
        assert update_mod._fetch_from_tags() is None


def test_fetch_from_tags_returns_none_on_only_non_version_tags() -> None:
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps([{"name": "stable"}, {"name": "nightly"}]).encode()
    with patch("archon.cli.update.urllib.request.urlopen", return_value=mock_resp):
        assert update_mod._fetch_from_tags() is None


def test_fetch_from_tags_returns_none_on_error() -> None:
    with patch("archon.cli.update.urllib.request.urlopen", side_effect=ConnectionError("offline")):
        assert update_mod._fetch_from_tags() is None


def test_parse_version_edge_cases() -> None:
    assert update_mod._parse_version("v26.3.5") == (26, 3, 5)  # v-prefix handled by regex
    assert update_mod._parse_version("26.3") == (26, 3)  # two segments
    assert update_mod._parse_version("") == (0,)  # empty
    assert update_mod._parse_version("26.3.342-rc1") == (26, 3, 342)  # pre-release suffix dropped


def test_run_version_prints_current(capsys: pytest.CaptureFixture) -> None:
    with patch("archon.cli.update._fetch_latest_tag", return_value=None):
        with patch("archon.version.get_version", return_value="26.3.5"):
            result = update_mod.run_version(_VersionArgs())
    out = capsys.readouterr().out
    assert "26.3.5" in out
    assert result == 0


def test_run_version_shows_newer_available(capsys: pytest.CaptureFixture) -> None:
    with patch("archon.cli.update._fetch_latest_tag", return_value="26.5.0"):
        with patch("archon.version.get_version", return_value="26.3.5"):
            update_mod.run_version(_VersionArgs())
    out = capsys.readouterr().out
    assert "26.5.0" in out
    assert "archon update" in out


def test_run_version_offline_no_crash(capsys: pytest.CaptureFixture) -> None:
    with patch("archon.cli.update._fetch_latest_tag", return_value=None):
        with patch("archon.version.get_version", return_value="26.3.5"):
            result = update_mod.run_version(_VersionArgs())
    assert result == 0


def test_run_version_up_to_date(capsys: pytest.CaptureFixture) -> None:
    with patch("archon.cli.update._fetch_latest_tag", return_value="26.3.5"):
        with patch("archon.version.get_version", return_value="26.3.5"):
            update_mod.run_version(_VersionArgs())
    out = capsys.readouterr().out
    assert "Up to date" in out


def test_run_version_no_downgrade_prompt(capsys: pytest.CaptureFixture) -> None:
    """When current version is newer than latest release, show 'Up to date'."""
    with patch("archon.cli.update._fetch_latest_tag", return_value="26.3.300"):
        with patch("archon.version.get_version", return_value="26.3.330"):
            update_mod.run_version(_VersionArgs())
    out = capsys.readouterr().out
    assert "Up to date" in out
    assert "archon update" not in out


def test_run_version_handles_import_error(capsys: pytest.CaptureFixture) -> None:
    with patch("archon.cli.update._fetch_latest_tag", return_value=None):
        with patch("archon.version.get_version", side_effect=ImportError("no module")):
            result = update_mod.run_version(_VersionArgs())
    out = capsys.readouterr().out
    assert "unknown" in out
    assert result == 0


class _UninstallArgs:
    pass


def test_run_uninstall_calls_installer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    install_py = app_dir / "install.py"
    install_py.write_text("# fake installer")
    monkeypatch.setattr(update_mod, "_ARCHON_HOME", tmp_path)
    with patch("archon.cli.update.subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        result = update_mod.run_uninstall(_UninstallArgs())
    assert result == 0
    cmd = mock_run.call_args[0][0]
    assert "uv" in cmd
    assert str(install_py) in cmd
    assert "--uninstall" in cmd


def test_run_uninstall_installer_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(update_mod, "_ARCHON_HOME", tmp_path)
    result = update_mod.run_uninstall(_UninstallArgs())
    assert result == 1
    assert "not found" in capsys.readouterr().out


def test_run_uninstall_propagates_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "install.py").write_text("# fake")
    monkeypatch.setattr(update_mod, "_ARCHON_HOME", tmp_path)
    with patch("archon.cli.update.subprocess.run", return_value=MagicMock(returncode=2)):
        result = update_mod.run_uninstall(_UninstallArgs())
    assert result == 2


def test_run_uninstall_uv_not_in_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "install.py").write_text("# fake")
    monkeypatch.setattr(update_mod, "_ARCHON_HOME", tmp_path)
    with patch("archon.cli.update.subprocess.run", side_effect=FileNotFoundError("uv not found")):
        result = update_mod.run_uninstall(_UninstallArgs())
    assert result == 1
    out = capsys.readouterr().out
    assert "uv" in out
