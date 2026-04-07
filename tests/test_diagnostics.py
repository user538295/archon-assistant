"""Tests for archon.diagnostics — CheckResult dataclass and run_checks()."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from archon.diagnostics import CheckResult, _check_context_windows, run_checks


# ──────────────────────────────────────────────────────────────────
# CheckResult dataclass
# ──────────────────────────────────────────────────────────────────


def test_check_result_dataclass_fields() -> None:
    result = CheckResult("git", True, "git 2.x")
    assert result.name == "git"
    assert result.ok is True
    assert result.detail == "git 2.x"


# ──────────────────────────────────────────────────────────────────
# run_checks()
# ──────────────────────────────────────────────────────────────────


def _make_subprocess_mock(returncode: int = 0, stdout: str = "ok", stderr: str = "") -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


def _patch_all_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch subprocess.run to succeed for all binary checks."""
    monkeypatch.setattr(
        "archon.diagnostics.subprocess.run",
        lambda *args, **kwargs: _make_subprocess_mock(
            stdout=args[0][0] + " 1.0" if args else "ok"
        ),
    )


def _setup_archon_home(tmp_path: Path) -> None:
    """Create the minimal ~/.archon layout needed by sync checks."""
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=fake:token\n")
    (tmp_path / "config.toml").write_text("[access]\nallowed_user_ids = [1]\n")
    (tmp_path / "logs").mkdir()
    (tmp_path / "app").mkdir()


def test_run_checks_returns_list_of_check_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("archon.diagnostics._ARCHON_HOME", tmp_path)
    _setup_archon_home(tmp_path)
    _patch_all_subprocess(monkeypatch)

    with patch("archon.diagnostics.urllib.request.urlopen", return_value=MagicMock()):
        result = run_checks()

    assert isinstance(result, list)
    assert all(isinstance(r, CheckResult) for r in result)


def test_run_checks_includes_all_check_functions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    EXPECTED_COUNT = 10
    monkeypatch.setattr("archon.diagnostics._ARCHON_HOME", tmp_path)
    _setup_archon_home(tmp_path)
    _patch_all_subprocess(monkeypatch)

    with patch("archon.diagnostics.urllib.request.urlopen", return_value=MagicMock()):
        result = run_checks()

    assert len(result) == EXPECTED_COUNT
    names = [r.name for r in result]
    # Verify _check_health is excluded (it's tautological inside the daemon)
    health_names = {"health check", "_check_health", "health"}
    assert not any(r.name in health_names for r in result)


def test_run_checks_handles_check_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("archon.diagnostics._ARCHON_HOME", tmp_path)
    _setup_archon_home(tmp_path)
    _patch_all_subprocess(monkeypatch)

    exc = RuntimeError("boom")

    import archon.diagnostics as diag_mod

    original_check_git = diag_mod._check_git

    def raise_exc() -> CheckResult:
        raise exc

    monkeypatch.setattr(diag_mod, "_check_git", raise_exc)

    with patch("archon.diagnostics.urllib.request.urlopen", return_value=MagicMock()):
        result = run_checks()

    git_result = next(r for r in result if r.name == "git")
    assert git_result.ok is False
    assert str(exc) in git_result.detail

    # Other checks are still present
    assert len(result) == 10


# ──────────────────────────────────────────────────────────────────
# CheckResult.warn field
# ──────────────────────────────────────────────────────────────────


def test_check_result_warn_field_defaults_false() -> None:
    result = CheckResult("x", True, "ok")
    assert result.warn is False


# ──────────────────────────────────────────────────────────────────
# _check_context_windows()
# ──────────────────────────────────────────────────────────────────


def _make_cfg(available: dict) -> MagicMock:
    cfg = MagicMock()
    cfg.models.available = available
    return cfg


def test_check_context_windows_all_match() -> None:
    cfg = _make_cfg({"claude-sonnet-4-6": 200_000})
    result = _check_context_windows(cfg)
    assert result.ok is True
    assert result.warn is False


def test_check_context_windows_mismatch_returns_warn() -> None:
    # claude-opus-4-6 canonical = 1_000_000, configured = 200_000
    cfg = _make_cfg({"claude-opus-4-6": 200_000})
    result = _check_context_windows(cfg)
    assert result.ok is True
    assert result.warn is True
    assert "1,000,000" in result.detail


def test_check_context_windows_custom_model_skipped() -> None:
    cfg = _make_cfg({"my-proxy": 500_000})
    result = _check_context_windows(cfg)
    assert result.ok is True
    assert result.warn is False


def test_check_context_windows_empty_available() -> None:
    cfg = _make_cfg({})
    result = _check_context_windows(cfg)
    assert result.ok is True
    assert result.warn is False


def test_check_context_windows_multiple_mismatches_reported() -> None:
    cfg = _make_cfg({"claude-opus-4-6": 200_000, "claude-sonnet-4-6": 100_000})
    result = _check_context_windows(cfg)
    assert result.ok is True
    assert result.warn is True
    assert "claude-opus-4-6" in result.detail
    assert "claude-sonnet-4-6" in result.detail


def test_check_context_windows_cfg_none_calls_load_config() -> None:
    from archon.config import ConfigError

    with patch("archon.config.load_config", side_effect=ConfigError("oops")):
        result = _check_context_windows()
    assert result.ok is False
    assert "oops" in result.detail


def test_check_context_windows_cfg_none_load_config_success() -> None:
    mock_cfg = MagicMock()
    mock_cfg.models.available = {"claude-sonnet-4-6": 200_000}

    with patch("archon.config.load_config", return_value=mock_cfg):
        result = _check_context_windows()

    assert result.ok is True
    assert result.warn is False
