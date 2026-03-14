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


def test_run_with_timeout_removed():
    """_run_with_timeout was removed as dead code (YAGNI)."""
    obj = _Concrete()
    assert not hasattr(obj, "_run_with_timeout")


def test_command_log_stores_copies_not_references():
    """Mutating the original cmd list must not affect the logged entry."""
    obj = _Concrete()
    cmd = ["echo", "hello"]
    obj._run(cmd, dry_run=True)
    cmd.append("extra")
    assert obj.command_log[0] == ["echo", "hello"]
