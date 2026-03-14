"""T50 — WindowsRuntime stub tests."""
from __future__ import annotations

import signal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from archon.platform.windows.runtime import WindowsRuntime

_HAS_SIGBREAK = hasattr(signal, "SIGBREAK")


@pytest.fixture
def rt() -> WindowsRuntime:
    return WindowsRuntime()


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


class TestWindowsRuntime:
    def test_register_signals_registers_sigint(self, rt: WindowsRuntime) -> None:
        """SIGINT is always registered."""
        loop = MagicMock()

        async def shutdown():
            pass

        with patch("archon.platform.windows.runtime.signal.signal") as mock_signal:
            rt.register_signals(loop, shutdown)

        signal_nums = {call.args[0] for call in mock_signal.call_args_list}
        assert signal.SIGINT in signal_nums
        if _HAS_SIGBREAK:
            assert signal.SIGBREAK in signal_nums  # type: ignore[attr-defined]
            assert mock_signal.call_count == 2
        else:
            # On non-Windows, only SIGINT
            assert mock_signal.call_count == 1

    def test_register_signals_registers_sigbreak_when_available(
        self, rt: WindowsRuntime
    ) -> None:
        """When SIGBREAK is available, it gets registered instead of SIGTERM."""
        loop = MagicMock()

        async def shutdown():
            pass

        # Temporarily add SIGBREAK to signal module
        fake_sigbreak = 21
        with patch.object(
            __import__("archon.platform.windows.runtime", fromlist=["signal"]),
            "signal",
        ) as mock_sig_mod:
            mock_sig_mod.SIGINT = signal.SIGINT
            mock_sig_mod.SIGBREAK = fake_sigbreak
            rt.register_signals(loop, shutdown)

        assert mock_sig_mod.signal.call_count == 2
        signal_nums = {call.args[0] for call in mock_sig_mod.signal.call_args_list}
        assert signal.SIGINT in signal_nums
        assert fake_sigbreak in signal_nums

    def test_no_sigterm_registered(self, rt: WindowsRuntime) -> None:
        """SIGTERM must never be registered (not deliverable on Windows)."""
        loop = MagicMock()

        async def shutdown():
            pass

        with patch("archon.platform.windows.runtime.signal.signal") as mock_signal:
            rt.register_signals(loop, shutdown)

        signal_nums = {call.args[0] for call in mock_signal.call_args_list}
        assert signal.SIGTERM not in signal_nums

    def test_signal_callback_bridges_to_asyncio(self, rt: WindowsRuntime) -> None:
        loop = _mock_loop()

        async def shutdown():
            pass

        with patch("archon.platform.windows.runtime.signal.signal") as mock_signal:
            rt.register_signals(loop, shutdown)

        # Get the installed handler and invoke it
        handler = mock_signal.call_args_list[0].args[1]
        handler(signal.SIGINT, None)

        loop.call_soon_threadsafe.assert_called_once()
        # Execute the scheduled closure to verify it creates a task
        scheduled_fn = loop.call_soon_threadsafe.call_args.args[0]
        scheduled_fn()
        loop.create_task.assert_called_once()

    def test_signal_idempotency_guard(self, rt: WindowsRuntime) -> None:
        """Second signal while shutdown is in progress is ignored."""
        loop = _mock_loop(task_done=False)

        async def shutdown():
            pass

        with patch("archon.platform.windows.runtime.signal.signal") as mock_signal:
            rt.register_signals(loop, shutdown)

        handler = mock_signal.call_args_list[0].args[1]

        # First signal — schedules shutdown
        handler(signal.SIGINT, None)
        assert loop.call_soon_threadsafe.call_count == 1
        # Execute the closure so _shutdown_task gets assigned
        scheduled_fn = loop.call_soon_threadsafe.call_args.args[0]
        scheduled_fn()

        # Second signal — should be ignored (task not done)
        handler(signal.SIGINT, None)
        assert loop.call_soon_threadsafe.call_count == 1  # Still 1

    def test_signal_retriggers_after_completed_task(self, rt: WindowsRuntime) -> None:
        """If the first shutdown task completed, a second signal re-triggers."""
        loop = _mock_loop(task_done=True)

        async def shutdown():
            pass

        with patch("archon.platform.windows.runtime.signal.signal") as mock_signal:
            rt.register_signals(loop, shutdown)

        handler = mock_signal.call_args_list[0].args[1]

        # First signal
        handler(signal.SIGINT, None)
        assert loop.call_soon_threadsafe.call_count == 1
        scheduled_fn = loop.call_soon_threadsafe.call_args.args[0]
        scheduled_fn()

        # Second signal — task.done() is True, so it re-triggers
        handler(signal.SIGINT, None)
        assert loop.call_soon_threadsafe.call_count == 2

    def test_find_binary_delegates_to_which(self, rt: WindowsRuntime) -> None:
        with patch("shutil.which", return_value="/usr/bin/whisper"):
            result = rt.find_binary("whisper")
        assert result == Path("/usr/bin/whisper")

    def test_find_binary_returns_none(self, rt: WindowsRuntime) -> None:
        with patch("shutil.which", return_value=None):
            result = rt.find_binary("nonexistent_xyz")
        assert result is None

    def test_process_uptime_returns_none(self, rt: WindowsRuntime) -> None:
        assert rt.process_uptime(1234) is None

    def test_restart_process_calls_execv(self, rt: WindowsRuntime) -> None:
        with patch("os.execv") as mock_execv:
            rt.restart_process()
        mock_execv.assert_called_once()
