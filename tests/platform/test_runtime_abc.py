"""Tests for PlatformRuntime ABC."""
from __future__ import annotations

import signal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from archon.platform.runtime import PlatformRuntime


def _mock_loop(task_done: bool = False) -> MagicMock:
    """Create a mock loop whose create_task closes the coroutine to avoid warnings."""
    loop = MagicMock()

    def _close_coro(coro):
        coro.close()
        task = MagicMock()
        task.done.return_value = task_done
        return task

    loop.create_task.side_effect = _close_coro
    return loop


def test_cannot_instantiate_directly():
    with pytest.raises(TypeError):
        PlatformRuntime()  # type: ignore[abstract]


class _IncompleteRuntime(PlatformRuntime):
    """Missing abstract methods."""

    def restart_process(self) -> None:
        pass


def test_incomplete_subclass_raises():
    with pytest.raises(TypeError):
        _IncompleteRuntime()  # type: ignore[abstract]


class _ConcreteRuntime(PlatformRuntime):
    """Minimal concrete implementation."""

    def restart_process(self) -> None:
        pass

    def find_binary(self, name: str, extra_paths: list[Path] | None = None) -> Path | None:
        return None


def test_complete_subclass_instantiates():
    rt = _ConcreteRuntime()
    assert rt is not None


def test_register_signals_registers_both():
    rt = _ConcreteRuntime()
    loop = MagicMock()
    callback = MagicMock()
    rt.register_signals(loop, callback)
    assert loop.add_signal_handler.call_count == 2
    signals_registered = {call.args[0] for call in loop.add_signal_handler.call_args_list}
    assert signals_registered == {signal.SIGTERM, signal.SIGINT}


def test_register_signals_first_invocation_creates_task():
    rt = _ConcreteRuntime()
    loop = _mock_loop()

    async def shutdown():
        pass

    rt.register_signals(loop, shutdown)
    handler = loop.add_signal_handler.call_args_list[0].args[1]
    handler()
    loop.create_task.assert_called_once()


def test_register_signals_second_invocation_ignored():
    rt = _ConcreteRuntime()
    loop = _mock_loop()

    async def shutdown():
        pass

    rt.register_signals(loop, shutdown)
    handler = loop.add_signal_handler.call_args_list[0].args[1]
    # First signal
    handler()
    assert loop.create_task.call_count == 1
    # Second signal — should be ignored
    handler()
    assert loop.create_task.call_count == 1  # Still 1, not 2


def test_register_signals_retriggers_after_completed_task():
    """If the first shutdown task has completed, a second signal re-triggers."""
    rt = _ConcreteRuntime()
    loop = _mock_loop(task_done=True)

    async def shutdown():
        pass

    rt.register_signals(loop, shutdown)
    handler = loop.add_signal_handler.call_args_list[0].args[1]
    # First signal
    handler()
    assert loop.create_task.call_count == 1
    # Second signal — task.done() is True, so it re-triggers
    handler()
    assert loop.create_task.call_count == 2


def test_process_uptime_parses_time():
    rt = _ConcreteRuntime()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="01:23:45\n", returncode=0)
        assert rt.process_uptime(1234) == "01:23:45"


def test_process_uptime_parses_days_format():
    rt = _ConcreteRuntime()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="3-02:15:30\n", returncode=0)
        assert rt.process_uptime(1234) == "3-02:15:30"


def test_process_uptime_returns_none_on_failure():
    rt = _ConcreteRuntime()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", returncode=1)
        assert rt.process_uptime(9999) is None


def test_process_uptime_returns_none_for_nonexistent_pid():
    rt = _ConcreteRuntime()
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert rt.process_uptime(99999) is None
