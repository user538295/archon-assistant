from __future__ import annotations
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import archon.cli.logs as logs_mod


class _Args:
    def __init__(self, lines: int = 50, follow: bool = False, date: str | None = None) -> None:
        self.lines = lines
        self.follow = follow
        self.date = date


def test_run_logs_tails_50_lines_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_file = tmp_path / "archon.log"
    log_file.write_text("line1\nline2\n")
    monkeypatch.setattr(logs_mod, "_log_path", lambda: log_file)
    with patch("archon.cli.logs.subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        result = logs_mod.run_logs(_Args())
    assert result == 0
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "tail"
    assert "-50" in cmd
    assert str(log_file) in cmd


def test_run_logs_custom_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_file = tmp_path / "archon.log"
    log_file.write_text("x\n")
    monkeypatch.setattr(logs_mod, "_log_path", lambda: log_file)
    with patch("archon.cli.logs.subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        logs_mod.run_logs(_Args(lines=200))
    cmd = mock_run.call_args[0][0]
    assert "-200" in cmd


def test_run_logs_file_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(logs_mod, "_log_path", lambda: tmp_path / "missing.log")
    result = logs_mod.run_logs(_Args())
    assert result == 1
    assert "not found" in capsys.readouterr().out


def test_run_logs_with_date(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base_log = tmp_path / "archon.log"
    dated_log = tmp_path / "archon.2026-03-01.log"
    dated_log.write_text("old logs\n")
    monkeypatch.setattr(logs_mod, "_log_path", lambda: base_log)
    with patch("archon.cli.logs.subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        result = logs_mod.run_logs(_Args(date="2026-03-01"))
    assert result == 0
    cmd = mock_run.call_args[0][0]
    assert "archon.2026-03-01.log" in cmd[-1]


def test_run_logs_date_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(logs_mod, "_log_path", lambda: tmp_path / "archon.log")
    result = logs_mod.run_logs(_Args(date="2026-01-01"))
    assert result == 1
    assert "not found" in capsys.readouterr().out


def test_run_logs_follow_uses_tail_f(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_file = tmp_path / "archon.log"
    log_file.write_text("x\n")
    monkeypatch.setattr(logs_mod, "_log_path", lambda: log_file)
    with patch("archon.cli.logs.subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        result = logs_mod.run_logs(_Args(follow=True))
    assert result == 0
    cmd = mock_run.call_args[0][0]
    assert "-f" in cmd


def test_log_path_reads_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    config_dir = tmp_path / ".archon"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text('[logging]\nlog_file = "/custom/path.log"\n')
    result = logs_mod._log_path()
    assert result == Path("/custom/path.log")


def test_log_path_default_when_no_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # No config exists
    result = logs_mod._log_path()
    assert result == tmp_path / ".archon" / "logs" / "archon.log"


def test_log_path_falls_back_on_bad_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    config_dir = tmp_path / ".archon"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text("NOT VALID TOML @@@")
    result = logs_mod._log_path()
    assert result == tmp_path / ".archon" / "logs" / "archon.log"


def test_run_logs_zero_lines_returns_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(logs_mod, "_log_path", lambda: tmp_path / "archon.log")
    assert logs_mod.run_logs(_Args(lines=0)) == 1
    assert "positive" in capsys.readouterr().out


def test_run_logs_negative_lines_returns_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(logs_mod, "_log_path", lambda: tmp_path / "archon.log")
    assert logs_mod.run_logs(_Args(lines=-10)) == 1
    assert "positive" in capsys.readouterr().out


def test_run_logs_tail_not_found_returns_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """tail binary not on PATH must return 1 with a clean error message."""
    log_file = tmp_path / "archon.log"
    log_file.write_text("x\n")
    monkeypatch.setattr(logs_mod, "_log_path", lambda: log_file)
    with patch("archon.cli.logs.subprocess.run", side_effect=FileNotFoundError):
        result = logs_mod.run_logs(_Args())
    assert result == 1
    assert "tail not found" in capsys.readouterr().out


def test_run_logs_follow_tail_not_found_returns_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """tail not found in follow mode must return 1 with clean message."""
    log_file = tmp_path / "archon.log"
    log_file.write_text("x\n")
    monkeypatch.setattr(logs_mod, "_log_path", lambda: log_file)
    with patch("archon.cli.logs.subprocess.run", side_effect=FileNotFoundError):
        result = logs_mod.run_logs(_Args(follow=True))
    assert result == 1
    assert "tail not found" in capsys.readouterr().out
