"""Tests for _RunMixin shared helper."""
import subprocess
from unittest.mock import patch

from archon.platform._run_mixin import RunMixin


class _Concrete(RunMixin):
    """Minimal concrete class for testing the mixin."""


def test_dry_run_records_command():
    obj = _Concrete()
    result = obj._run(["echo", "hello"], dry_run=True)
    assert obj.command_log == [["echo", "hello"]]
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_dry_run_custom_stdout():
    obj = _Concrete()
    result = obj._run(["launchctl", "list"], dry_run=True, stdout='"PID" = 1234;')
    assert result.stdout == '"PID" = 1234;'
    assert result.returncode == 0


def test_real_run_calls_subprocess():
    obj = _Concrete()
    fake_result = subprocess.CompletedProcess(args=["ls"], returncode=0, stdout="file.txt", stderr="")
    with patch("subprocess.run", return_value=fake_result) as mock_run:
        result = obj._run(["ls", "-la"])
    mock_run.assert_called_once_with(["ls", "-la"], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "file.txt"
    assert obj.command_log == []


def test_command_log_accumulates():
    obj = _Concrete()
    obj._run(["cmd1"], dry_run=True)
    obj._run(["cmd2", "arg"], dry_run=True)
    obj._run(["cmd3"], dry_run=True)
    assert len(obj.command_log) == 3
    assert obj.command_log[0] == ["cmd1"]
    assert obj.command_log[1] == ["cmd2", "arg"]
    assert obj.command_log[2] == ["cmd3"]


def test_run_with_timeout_raises_on_timeout():
    obj = _Concrete()
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["slow"], timeout=1)):
        try:
            obj._run_with_timeout(["slow"], timeout=1)
            raise AssertionError("Should have raised TimeoutExpired")
        except subprocess.TimeoutExpired:
            pass


def test_run_with_timeout_dry_run_returns_instantly():
    obj = _Concrete()
    result = obj._run_with_timeout(["slow"], timeout=1, dry_run=True)
    assert result.returncode == 0
    assert obj.command_log == [["slow"]]
