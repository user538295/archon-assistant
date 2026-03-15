"""Unit tests for _ensure_qmd_daemon — FR.002 QMD integration.

All external side-effects (find_binary, os.kill, asyncio subprocess,
asyncio.sleep, PID file I/O) are mocked so the suite runs in-process
without touching the filesystem or spawning real processes.
"""
import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.gateway.gateway import _ensure_qmd_daemon

# AsyncMock internals emit a "coroutine ... was never awaited" RuntimeWarning
# when the mock object is garbage-collected in some Python/pytest-asyncio
# combinations.  The warning is a mock library artefact — all tests pass
# correctly — so suppress it at file scope.
pytestmark = pytest.mark.filterwarnings(
    "ignore:coroutine.*was never awaited:RuntimeWarning"
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _pid_path() -> Path:
    return Path.home() / ".cache" / "qmd" / "mcp.pid"


def _no_http_probe():
    """Mock the HTTP probe to simulate an unreachable daemon."""
    return patch("urllib.request.urlopen", side_effect=ConnectionRefusedError)


def _mock_find_binary(result: Path | None = Path("/usr/bin/qmd")):
    """Mock get_runtime().find_binary() to return *result*."""
    mock_runtime = MagicMock()
    mock_runtime.find_binary.return_value = result
    return patch("archon.platform.get_runtime", return_value=mock_runtime)


# ── remote host branch ───────────────────────────────────────────────────────


async def test_remote_host_returns_true_immediately() -> None:
    """Non-localhost host must return True without touching PATH or subprocess."""
    with _mock_find_binary() as mock_rt:
        result = await _ensure_qmd_daemon("remote.host", 8181)

    assert result is True
    mock_rt.return_value.find_binary.assert_not_called()


async def test_remote_host_192_returns_true() -> None:
    result = await _ensure_qmd_daemon("192.168.1.100", 8181)
    assert result is True


async def test_remote_host_qmd_internal_returns_true() -> None:
    result = await _ensure_qmd_daemon("qmd.internal", 8181)
    assert result is True


# ── qmd not in PATH ───────────────────────────────────────────────────────────


async def test_qmd_not_in_path_returns_false() -> None:
    with _mock_find_binary(None):
        result = await _ensure_qmd_daemon("localhost", 8181)

    assert result is False


async def test_qmd_not_in_path_for_127_0_0_1_returns_false() -> None:
    with _mock_find_binary(None):
        result = await _ensure_qmd_daemon("127.0.0.1", 8181)

    assert result is False


# ── PID file present, daemon alive ───────────────────────────────────────────


async def test_alive_pid_returns_true_without_starting_daemon(
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "mcp.pid"
    pid_file.write_text("12345")
    fake_pid_path = pid_file

    with (
        _mock_find_binary(),
        patch.object(Path, "exists", lambda self: self == _pid_path() or fake_pid_path.exists()),
        patch.object(Path, "read_text", lambda self: "12345"),
        patch("os.kill") as mock_kill,
        patch("asyncio.create_subprocess_exec") as mock_proc,
    ):
        mock_kill.return_value = None  # process is alive
        result = await _ensure_qmd_daemon("localhost", 8181)

    assert result is True
    mock_proc.assert_not_called()


# ── PID file present but stale (os.kill raises OSError) ──────────────────────


async def test_stale_pid_triggers_restart() -> None:
    """Stale PID file (dead process) should attempt daemon restart."""
    completed_proc = AsyncMock()
    completed_proc.returncode = 0
    completed_proc.communicate = AsyncMock(return_value=(b"", b""))

    call_count = {"n": 0}

    def _fake_exists(self: Path) -> bool:
        # First two calls: PID file exists (initial check + after startup wait).
        # We make it return True both times to exercise the success path.
        return str(self).endswith("mcp.pid")

    def _fake_read_text(self: Path) -> str:
        call_count["n"] += 1
        return "99999"  # always returns a PID

    def _fake_kill(pid: int, sig: int) -> None:
        if call_count["n"] <= 1:
            # First call (alive check for stale PID) — raise to simulate dead process.
            raise OSError("no such process")
        # After restart the process is alive.

    with (
        _mock_find_binary(),
        patch.object(Path, "exists", _fake_exists),
        patch.object(Path, "read_text", _fake_read_text),
        patch("os.kill", side_effect=_fake_kill),
        patch("asyncio.create_subprocess_exec", return_value=completed_proc),
        patch("asyncio.sleep", new_callable=AsyncMock),
        _no_http_probe(),
    ):
        result = await _ensure_qmd_daemon("localhost", 8181)

    assert result is True
    completed_proc.communicate.assert_awaited_once()


# ── subprocess returns non-zero ───────────────────────────────────────────────


async def test_daemon_start_nonzero_exit_returns_false() -> None:
    failed_proc = AsyncMock()
    failed_proc.returncode = 1
    failed_proc.communicate = AsyncMock(return_value=(b"", b"startup error"))

    with (
        _mock_find_binary(),
        patch.object(Path, "exists", return_value=False),
        patch("asyncio.create_subprocess_exec", return_value=failed_proc),
        patch("asyncio.sleep", new_callable=AsyncMock),
        _no_http_probe(),
    ):
        result = await _ensure_qmd_daemon("localhost", 8181)

    assert result is False


async def test_daemon_start_nonzero_uses_stderr_in_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    failed_proc = AsyncMock()
    failed_proc.returncode = 2
    failed_proc.communicate = AsyncMock(return_value=(b"", b"some error message"))

    with (
        _mock_find_binary(),
        patch.object(Path, "exists", return_value=False),
        patch("asyncio.create_subprocess_exec", return_value=failed_proc),
        patch("asyncio.sleep", new_callable=AsyncMock),
        caplog.at_level(logging.WARNING, logger="archon"),
        _no_http_probe(),
    ):
        await _ensure_qmd_daemon("localhost", 8181)

    assert any("some error message" in r.message for r in caplog.records)


# ── subprocess times out ──────────────────────────────────────────────────────


async def test_daemon_start_timeout_returns_false() -> None:
    slow_proc = MagicMock()
    # communicate is never actually called because wait_for raises immediately
    slow_proc.communicate = AsyncMock()

    with (
        _mock_find_binary(),
        patch.object(Path, "exists", return_value=False),
        patch(
            "asyncio.create_subprocess_exec",
            return_value=slow_proc,
        ),
        patch(
            "asyncio.wait_for",
            side_effect=asyncio.TimeoutError,
        ),
        _no_http_probe(),
    ):
        result = await _ensure_qmd_daemon("localhost", 8181)

    assert result is False


# ── subprocess succeeds but PID file not written ─────────────────────────────


async def test_daemon_started_but_no_pid_file_returns_false() -> None:
    ok_proc = AsyncMock()
    ok_proc.returncode = 0
    ok_proc.communicate = AsyncMock(return_value=(b"", b""))

    with (
        _mock_find_binary(),
        patch.object(Path, "exists", return_value=False),  # PID file never appears
        patch("asyncio.create_subprocess_exec", return_value=ok_proc),
        patch("asyncio.sleep", new_callable=AsyncMock),
        _no_http_probe(),
    ):
        result = await _ensure_qmd_daemon("localhost", 8181)

    assert result is False


# ── exception during subprocess ───────────────────────────────────────────────


async def test_daemon_start_generic_exception_returns_false() -> None:
    with (
        _mock_find_binary(),
        patch.object(Path, "exists", return_value=False),
        patch(
            "asyncio.create_subprocess_exec",
            side_effect=OSError("Permission denied"),
        ),
        _no_http_probe(),
    ):
        result = await _ensure_qmd_daemon("localhost", 8181)

    assert result is False


# ── correct subprocess arguments ─────────────────────────────────────────────


async def test_daemon_start_uses_correct_subprocess_args() -> None:
    """Daemon must be started with 'qmd mcp --http --port N --daemon'."""
    ok_proc = AsyncMock()
    ok_proc.returncode = 0
    ok_proc.communicate = AsyncMock(return_value=(b"", b""))

    captured_args: list = []

    async def _fake_create(*args, **kwargs):
        captured_args.extend(args)
        return ok_proc

    with (
        _mock_find_binary(),
        patch.object(Path, "exists", return_value=False),
        patch("asyncio.create_subprocess_exec", side_effect=_fake_create),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        await _ensure_qmd_daemon("localhost", 9090)

    assert captured_args[:6] == ["/usr/bin/qmd", "mcp", "--http", "--port", "9090", "--daemon"]


# ── PID file read error (malformed content) ───────────────────────────────────


async def test_malformed_pid_file_triggers_restart() -> None:
    """Non-integer PID file content is treated as stale → restart attempt."""
    ok_proc = AsyncMock()
    ok_proc.returncode = 0
    ok_proc.communicate = AsyncMock(return_value=(b"", b""))

    read_call = {"count": 0}

    def _fake_read_text(self: Path) -> str:
        read_call["count"] += 1
        if read_call["count"] == 1:
            return "not-a-number"  # first read: malformed
        return "22222"  # after restart: valid PID

    with (
        _mock_find_binary(),
        patch.object(Path, "exists", return_value=True),
        patch.object(Path, "read_text", _fake_read_text),
        patch("os.kill", return_value=None),
        patch("asyncio.create_subprocess_exec", return_value=ok_proc),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await _ensure_qmd_daemon("localhost", 8181)

    # After restart succeeds with a good PID + os.kill(22222, 0) succeeds → True
    assert result is True
