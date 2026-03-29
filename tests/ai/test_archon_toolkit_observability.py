"""Tests for ArchonToolkit observability tools — Task 4.1 (get_logs)."""
import collections
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from archon.ai.archon_toolkit import ArchonToolkit


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_toolkit(*, config: object | None = None) -> ArchonToolkit:
    return ArchonToolkit(config=config)


def _make_config(log_file: Path) -> MagicMock:
    cfg = MagicMock()
    cfg.logging.log_file = log_file
    return cfg


def _write_lines(path: Path, n: int) -> list[str]:
    """Write n numbered lines to path, return the lines."""
    lines = [f"line {i}" for i in range(1, n + 1)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


# ──────────────────────────────────────────────────────────────────
# test_get_logs_default
# ──────────────────────────────────────────────────────────────────


class TestGetLogsDefault:
    async def test_get_logs_default(self, tmp_path: Path) -> None:
        """100-line file, no args → last 50 lines returned."""
        log_file = tmp_path / "archon.log"
        all_lines = _write_lines(log_file, 100)

        toolkit = _make_toolkit(config=_make_config(log_file))
        result = await toolkit.call_tool("get_logs", {})

        returned_lines = result.splitlines()
        assert len(returned_lines) == 50
        assert returned_lines[0] == "line 51"
        assert returned_lines[-1] == "line 100"


# ──────────────────────────────────────────────────────────────────
# test_get_logs_custom_lines
# ──────────────────────────────────────────────────────────────────


class TestGetLogsCustomLines:
    async def test_get_logs_custom_lines(self, tmp_path: Path) -> None:
        """lines=10 → last 10 lines returned."""
        log_file = tmp_path / "archon.log"
        _write_lines(log_file, 50)

        toolkit = _make_toolkit(config=_make_config(log_file))
        result = await toolkit.call_tool("get_logs", {"lines": 10})

        returned_lines = result.splitlines()
        assert len(returned_lines) == 10
        assert returned_lines[0] == "line 41"
        assert returned_lines[-1] == "line 50"


# ──────────────────────────────────────────────────────────────────
# test_get_logs_with_date
# ──────────────────────────────────────────────────────────────────


class TestGetLogsWithDate:
    async def test_get_logs_with_date(self, tmp_path: Path) -> None:
        """date='2026-01-15' → reads archon.2026-01-15.log from same dir."""
        base_log = tmp_path / "archon.log"
        dated_log = tmp_path / "archon.2026-01-15.log"
        _write_lines(base_log, 5)
        dated_log.write_text("dated line 1\ndated line 2\n", encoding="utf-8")

        toolkit = _make_toolkit(config=_make_config(base_log))
        result = await toolkit.call_tool("get_logs", {"date": "2026-01-15"})

        assert "dated line 1" in result
        assert "dated line 2" in result
        assert "line 5" not in result  # base log content must not appear


# ──────────────────────────────────────────────────────────────────
# test_get_logs_file_not_found
# ──────────────────────────────────────────────────────────────────


class TestGetLogsFileNotFound:
    async def test_get_logs_file_not_found(self, tmp_path: Path) -> None:
        """Missing log file → error string contains 'not found'."""
        missing = tmp_path / "no_such_file.log"

        toolkit = _make_toolkit(config=_make_config(missing))
        result = await toolkit.call_tool("get_logs", {})

        assert result.startswith("Log file not found:")
        assert str(missing) in result


# ──────────────────────────────────────────────────────────────────
# test_get_logs_invalid_date
# ──────────────────────────────────────────────────────────────────


class TestGetLogsInvalidDate:
    async def test_get_logs_invalid_date(self, tmp_path: Path) -> None:
        """date='not-a-date' → 'Invalid date format:' returned."""
        log_file = tmp_path / "archon.log"
        _write_lines(log_file, 5)

        toolkit = _make_toolkit(config=_make_config(log_file))
        result = await toolkit.call_tool("get_logs", {"date": "not-a-date"})

        assert result.startswith("Invalid date format:")

    async def test_get_logs_invalid_date_wrong_format(self, tmp_path: Path) -> None:
        """date='01/15/2026' → 'Invalid date format:' returned."""
        log_file = tmp_path / "archon.log"
        _write_lines(log_file, 5)

        toolkit = _make_toolkit(config=_make_config(log_file))
        result = await toolkit.call_tool("get_logs", {"date": "01/15/2026"})

        assert result.startswith("Invalid date format:")


# ──────────────────────────────────────────────────────────────────
# test_get_logs_lines_clamped
# ──────────────────────────────────────────────────────────────────


class TestGetLogsLinesClamped:
    async def test_get_logs_lines_clamped(self, tmp_path: Path) -> None:
        """lines=9999 → clamped to 1000; returns up to 1000 lines."""
        log_file = tmp_path / "archon.log"
        _write_lines(log_file, 1200)

        toolkit = _make_toolkit(config=_make_config(log_file))
        result = await toolkit.call_tool("get_logs", {"lines": 9999})

        returned_lines = result.splitlines()
        assert len(returned_lines) == 1000
        assert returned_lines[0] == "line 201"  # start boundary
        assert returned_lines[-1] == "line 1200"

    async def test_get_logs_lines_clamped_min(self, tmp_path: Path) -> None:
        """lines=0 → clamped to 1."""
        log_file = tmp_path / "archon.log"
        _write_lines(log_file, 10)

        toolkit = _make_toolkit(config=_make_config(log_file))
        result = await toolkit.call_tool("get_logs", {"lines": 0})

        returned_lines = result.splitlines()
        assert len(returned_lines) == 1
        assert returned_lines[0] == "line 10"


# ──────────────────────────────────────────────────────────────────
# test_get_logs_config_none_uses_default_path
# ──────────────────────────────────────────────────────────────────


class TestGetLogsDatedLogNotFound:
    async def test_get_logs_dated_log_not_found(self, tmp_path: Path) -> None:
        """date provided but archived log does not exist → 'not found' error."""
        base_log = tmp_path / "archon.log"
        _write_lines(base_log, 5)
        toolkit = _make_toolkit(config=_make_config(base_log))
        result = await toolkit.call_tool("get_logs", {"date": "2020-01-01"})
        assert result.startswith("Log file not found:")
        assert "2020-01-01" in result


class TestGetLogsConfigNoneUsesDefaultPath:
    async def test_get_logs_config_none_uses_default_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """_config=None → handler falls back to ~/.archon/logs/archon.log."""
        # Monkeypatch Path.home() to point to tmp_path
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        default_log_dir = tmp_path / ".archon" / "logs"
        default_log_dir.mkdir(parents=True)
        default_log = default_log_dir / "archon.log"
        default_log.write_text("fallback line 1\nfallback line 2\n", encoding="utf-8")

        toolkit = _make_toolkit(config=None)
        result = await toolkit.call_tool("get_logs", {})

        assert "fallback line 1" in result
        assert "fallback line 2" in result


# ──────────────────────────────────────────────────────────────────
# get_version tests — Task 4.2
# ──────────────────────────────────────────────────────────────────


class TestGetVersion:
    async def test_get_version_returns_string(self) -> None:
        """get_version MCP tool returns the version string from archon.version."""
        toolkit = _make_toolkit()
        with patch("archon.ai.archon_toolkit.get_version", return_value="26.3.1"):
            result = await toolkit.call_tool("get_version", {})
        assert result == "26.3.1"


# ──────────────────────────────────────────────────────────────────
# archon_doctor tests — Task 4.3
# ──────────────────────────────────────────────────────────────────


class TestArchonDoctorAllPass:
    async def test_archon_doctor_all_pass(self) -> None:
        """mock run_checks returns all-ok list; assert JSON array all ok=true."""
        from archon.diagnostics import CheckResult

        mock_results = [
            CheckResult("git", True, "git version 2.40.0"),
            CheckResult("uv", True, "uv 0.1.24"),
            CheckResult("python", True, "Python 3.12.2"),
        ]
        toolkit = _make_toolkit()
        with patch("archon.ai.archon_toolkit.run_checks", return_value=mock_results):
            result = await toolkit.call_tool("archon_doctor", {})

        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 3
        assert all(item["ok"] is True for item in parsed)
        assert parsed[0] == {"name": "git", "ok": True, "detail": "git version 2.40.0"}


class TestArchonDoctorSomeFail:
    async def test_archon_doctor_some_fail(self) -> None:
        """mock returns 1 failing check; assert JSON contains ok=false entry."""
        from archon.diagnostics import CheckResult

        mock_results = [
            CheckResult("git", True, "git version 2.40.0"),
            CheckResult("claude", False, "not found"),
        ]
        toolkit = _make_toolkit()
        with patch("archon.ai.archon_toolkit.run_checks", return_value=mock_results):
            result = await toolkit.call_tool("archon_doctor", {})

        parsed = json.loads(result)
        failing = [item for item in parsed if not item["ok"]]
        assert len(failing) == 1
        assert failing[0] == {"name": "claude", "ok": False, "detail": "not found"}


class TestArchonDoctorReturnsJsonArray:
    async def test_archon_doctor_returns_json_array(self) -> None:
        """Result is parseable JSON list."""
        from archon.diagnostics import CheckResult

        mock_results = [CheckResult("git", True, "git version 2.40.0")]
        toolkit = _make_toolkit()
        with patch("archon.ai.archon_toolkit.run_checks", return_value=mock_results):
            result = await toolkit.call_tool("archon_doctor", {})

        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 1
        assert set(parsed[0].keys()) == {"name", "ok", "detail"}
