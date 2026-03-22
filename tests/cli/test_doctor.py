from __future__ import annotations
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import archon.cli.doctor as doctor_mod
from archon.cli.doctor import CheckResult


def _all_ok() -> list[CheckResult]:
    return [CheckResult(f"check{i}", True, "OK") for i in range(9)]


def test_all_pass_returns_0(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    checks = [doctor_mod._check_git, doctor_mod._check_uv, doctor_mod._check_python,
              doctor_mod._check_claude, doctor_mod._check_env_file,
              doctor_mod._check_config_file, doctor_mod._check_logs_dir,
              doctor_mod._check_health, doctor_mod._check_app_dir]
    ok = CheckResult("x", True, "OK")
    for fn in checks:
        monkeypatch.setattr(doctor_mod, fn.__name__, lambda _ok=ok: _ok)
    result = doctor_mod.run_doctor()
    assert result == 0
    assert "All checks passed" in capsys.readouterr().out


def test_one_fail_returns_1(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    ok = CheckResult("x", True, "OK")
    fail = CheckResult("env file", False, "not found")
    checks = [doctor_mod._check_git, doctor_mod._check_uv, doctor_mod._check_python,
              doctor_mod._check_claude, doctor_mod._check_env_file,
              doctor_mod._check_config_file, doctor_mod._check_logs_dir,
              doctor_mod._check_health, doctor_mod._check_app_dir]
    for fn in checks:
        if fn.__name__ == "_check_env_file":
            monkeypatch.setattr(doctor_mod, fn.__name__, lambda: fail)
        else:
            monkeypatch.setattr(doctor_mod, fn.__name__, lambda _ok=ok: _ok)
    result = doctor_mod.run_doctor()
    assert result == 1
    assert "issue" in capsys.readouterr().out


def test_check_git_found() -> None:
    with patch("archon.cli.doctor.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="git version 2.39")):
        result = doctor_mod._check_git()
    assert result.ok is True


def test_check_git_not_found() -> None:
    with patch("archon.cli.doctor.subprocess.run", side_effect=FileNotFoundError):
        result = doctor_mod._check_git()
    assert result.ok is False


def test_check_uv_found() -> None:
    with patch("archon.cli.doctor.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="uv 0.4.10")):
        result = doctor_mod._check_uv()
    assert result.ok is True


def test_check_python_312() -> None:
    with patch("archon.cli.doctor.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="Python 3.12.3", stderr="")):
        result = doctor_mod._check_python()
    assert result.ok is True


def test_check_python_below_312() -> None:
    with patch("archon.cli.doctor.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="Python 3.11.0", stderr="")):
        result = doctor_mod._check_python()
    assert result.ok is False


def test_check_env_file_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=abc123\n")
    result = doctor_mod._check_env_file()
    assert result.ok is True


def test_check_env_file_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    result = doctor_mod._check_env_file()
    assert result.ok is False


def test_check_config_valid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    (tmp_path / "config.toml").write_text('[access]\nallowed_user_ids = [1]\n')
    result = doctor_mod._check_config_file()
    assert result.ok is True


def test_check_config_invalid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    (tmp_path / "config.toml").write_text("NOT VALID TOML @@@")
    result = doctor_mod._check_config_file()
    assert result.ok is False


def test_check_logs_dir_writable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    result = doctor_mod._check_logs_dir()
    assert result.ok is True


def test_check_logs_dir_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    result = doctor_mod._check_logs_dir()
    assert result.ok is False


def test_check_health_ok() -> None:
    with patch("archon.cli.doctor.urllib.request.urlopen", return_value=MagicMock()):
        result = doctor_mod._check_health()
    assert result.ok is True


def test_check_health_fail() -> None:
    with patch("archon.cli.doctor.urllib.request.urlopen", side_effect=Exception("refused")):
        result = doctor_mod._check_health()
    assert result.ok is False


def test_check_app_dir_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    (tmp_path / "app").mkdir()
    result = doctor_mod._check_app_dir()
    assert result.ok is True


def test_check_app_dir_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    result = doctor_mod._check_app_dir()
    assert result.ok is False


def test_check_env_file_commented_token_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A commented-out token line must not pass as healthy."""
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    env = tmp_path / ".env"
    env.write_text("# TELEGRAM_BOT_TOKEN=abc123\n")
    result = doctor_mod._check_env_file()
    assert result.ok is False


def test_check_env_file_empty_value_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty token value (TELEGRAM_BOT_TOKEN=) must not pass as healthy."""
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=\n")
    result = doctor_mod._check_env_file()
    assert result.ok is False


def test_check_env_file_token_keyword_only_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Presence of the keyword in an unrelated string must not pass."""
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    env = tmp_path / ".env"
    env.write_text("# export TELEGRAM_BOT_TOKEN\n")
    result = doctor_mod._check_env_file()
    assert result.ok is False


def test_check_health_reads_port_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_check_health() must use the port from config.toml when available."""
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    (tmp_path / "config.toml").write_text(
        "[background_agents]\nport = 19999\n"
    )
    captured_urls: list[str] = []

    def fake_urlopen(url: str, timeout: int) -> MagicMock:
        captured_urls.append(url)
        return MagicMock()

    with patch("archon.cli.doctor.urllib.request.urlopen", side_effect=fake_urlopen):
        doctor_mod._check_health()

    assert len(captured_urls) == 1
    assert ":19999/" in captured_urls[0]


def test_check_health_uses_default_port_when_config_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_check_health() must fall back to port 18182 when config is absent."""
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    # No config.toml in tmp_path
    captured_urls: list[str] = []

    def fake_urlopen(url: str, timeout: int) -> MagicMock:
        captured_urls.append(url)
        raise Exception("refused")

    with patch("archon.cli.doctor.urllib.request.urlopen", side_effect=fake_urlopen):
        doctor_mod._check_health()

    assert len(captured_urls) == 1
    assert ":18182/" in captured_urls[0]
