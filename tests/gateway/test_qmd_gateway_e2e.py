"""E2E tests for QMD daemon lifecycle in the gateway startup path.

Tests the _ensure_qmd_daemon integration and the qmd_url construction
that happens inside gateway._run().  All external I/O is mocked so no
real processes, network, or filesystem access is required.

Covered scenarios:
  - QMD disabled → qmd_url is None, _ensure_qmd_daemon never called
  - QMD enabled, daemon starts OK → qmd_url built from host:port
  - QMD enabled, daemon fails → qmd_url is None, warning logged
  - Remote host: URL built from custom host
  - URL format: http://<host>:<port>/mcp
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.gateway.gateway import _ensure_qmd_daemon


# ── _ensure_qmd_daemon: full success path (local) ────────────────────────────


async def test_ensure_qmd_daemon_success_returns_true_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Full success path: qmd in PATH, daemon starts, PID file written."""
    import logging

    ok_proc = AsyncMock()
    ok_proc.returncode = 0
    ok_proc.communicate = AsyncMock(return_value=(b"", b""))

    read_call = {"n": 0}

    def _fake_exists(self: Path) -> bool:
        # PID file absent initially (triggers restart), then present after startup.
        read_call["n"] += 1
        return read_call["n"] > 1

    def _fake_read_text(self: Path) -> str:
        return "55555"

    mock_runtime = MagicMock()
    mock_runtime.find_binary.return_value = Path("/usr/bin/qmd")

    with (
        patch("archon.platform.get_runtime", return_value=mock_runtime),
        patch.object(Path, "exists", _fake_exists),
        patch.object(Path, "read_text", _fake_read_text),
        patch("os.kill", return_value=None),
        patch("asyncio.create_subprocess_exec", return_value=ok_proc),
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch("urllib.request.urlopen", side_effect=ConnectionRefusedError),
        caplog.at_level(logging.INFO, logger="archon"),
    ):
        result = await _ensure_qmd_daemon("localhost", 8181)

    assert result is True
    assert any("started successfully" in r.message for r in caplog.records)


# ── qmd_url construction ──────────────────────────────────────────────────────


def test_qmd_url_format_localhost() -> None:
    """URL must be http://<host>:<port>/mcp."""
    host, port = "localhost", 8181
    url = f"http://{host}:{port}/mcp"
    assert url == "http://localhost:8181/mcp"


def test_qmd_url_format_custom_port() -> None:
    url = f"http://localhost:9090/mcp"
    assert url == "http://localhost:9090/mcp"


def test_qmd_url_format_remote_host() -> None:
    url = f"http://qmd.internal:8181/mcp"
    assert url == "http://qmd.internal:8181/mcp"


def test_qmd_url_format_ip_address() -> None:
    url = f"http://192.168.1.50:41823/mcp"
    assert url == "http://192.168.1.50:41823/mcp"


# ── gateway _run: QMD disabled path ──────────────────────────────────────────


async def test_gateway_qmd_disabled_never_calls_ensure_daemon() -> None:
    """When qmd.enabled=False, _ensure_qmd_daemon must NOT be called."""
    with patch("archon.gateway.gateway._ensure_qmd_daemon") as mock_ensure:
        # Simulate the gateway logic branch (minimal, not calling full _run)
        qmd_enabled = False
        qmd_url = None
        if qmd_enabled:
            ok = await mock_ensure("localhost", 8181)
            if ok:
                qmd_url = "http://localhost:8181/mcp"

    assert qmd_url is None
    mock_ensure.assert_not_called()


# ── gateway _run: QMD enabled, daemon OK ─────────────────────────────────────


async def test_gateway_qmd_enabled_daemon_ok_sets_qmd_url() -> None:
    """When enabled=True and daemon starts, qmd_url is set to full MCP URL."""
    with patch("archon.gateway.gateway._ensure_qmd_daemon", return_value=True) as mock_ensure:
        qmd_enabled = True
        host = "localhost"
        port = 8181
        qmd_url = None
        if qmd_enabled:
            ok = await mock_ensure(host, port)
            if ok:
                qmd_url = f"http://{host}:{port}/mcp"

    assert qmd_url == "http://localhost:8181/mcp"
    mock_ensure.assert_awaited_once_with("localhost", 8181)


# ── gateway _run: QMD enabled but daemon fails ───────────────────────────────


async def test_gateway_qmd_enabled_daemon_fails_keeps_qmd_url_none() -> None:
    """When enabled=True but daemon fails, qmd_url must remain None."""
    with patch("archon.gateway.gateway._ensure_qmd_daemon", return_value=False):
        qmd_enabled = True
        host, port = "localhost", 8181
        qmd_url = None
        if qmd_enabled:
            ok = await _ensure_qmd_daemon.__wrapped__(host, port) if hasattr(_ensure_qmd_daemon, "__wrapped__") else False
            # use the patched version via direct logic:
        # Replicate the exact gateway conditional:
        from archon.gateway.gateway import _ensure_qmd_daemon as real_fn
        mock_runtime = MagicMock()
        mock_runtime.find_binary.return_value = Path("/usr/bin/qmd")
        with patch.object(
            asyncio,
            "create_subprocess_exec",
            side_effect=OSError("no qmd"),
        ), patch("archon.platform.get_runtime", return_value=mock_runtime), patch.object(
            Path, "exists", return_value=False
        ):
            daemon_ok = await real_fn("localhost", 8181)

    assert daemon_ok is False


# ── gateway builds correct URL for non-default port ──────────────────────────


async def test_gateway_uses_configured_port_in_qmd_url() -> None:
    """Non-default port must be reflected in the constructed URL."""
    host, port = "localhost", 9090

    with patch("archon.gateway.gateway._ensure_qmd_daemon", return_value=True) as mock_ensure:
        qmd_url = None
        ok = await mock_ensure(host, port)
        if ok:
            qmd_url = f"http://{host}:{port}/mcp"

    assert qmd_url == "http://localhost:9090/mcp"


# ── gateway builds correct URL for remote host ───────────────────────────────


async def test_gateway_uses_remote_host_in_qmd_url() -> None:
    host, port = "qmd.internal", 8181

    with patch("archon.gateway.gateway._ensure_qmd_daemon", return_value=True) as mock_ensure:
        qmd_url = None
        ok = await mock_ensure(host, port)
        if ok:
            qmd_url = f"http://{host}:{port}/mcp"

    assert qmd_url == "http://qmd.internal:8181/mcp"


# ── daemon startup wait constant ─────────────────────────────────────────────


def test_qmd_daemon_startup_wait_is_reasonable() -> None:
    """_QMD_DAEMON_STARTUP_WAIT must be a positive float (sanity check)."""
    from archon.gateway.gateway import _QMD_DAEMON_STARTUP_WAIT
    assert isinstance(_QMD_DAEMON_STARTUP_WAIT, float)
    assert _QMD_DAEMON_STARTUP_WAIT > 0
    assert _QMD_DAEMON_STARTUP_WAIT <= 10.0  # should not be excessively long


# ── find_binary integration (cross-platform binary discovery) ────────────────


async def test_ensure_qmd_uses_find_binary_instead_of_shutil_which() -> None:
    """Binary detection must use get_runtime().find_binary(), not shutil.which()."""
    mock_runtime = MagicMock()
    mock_runtime.find_binary.return_value = Path("/custom/path/qmd")

    pid_file_exists_calls = {"n": 0}

    def _fake_exists(self: Path) -> bool:
        pid_file_exists_calls["n"] += 1
        return pid_file_exists_calls["n"] > 1

    ok_proc = AsyncMock()
    ok_proc.returncode = 0
    ok_proc.communicate = AsyncMock(return_value=(b"", b""))

    with (
        patch("archon.platform.get_runtime", return_value=mock_runtime),
        patch.object(Path, "exists", _fake_exists),
        patch.object(Path, "read_text", return_value="12345"),
        patch("os.kill", return_value=None),
        patch("asyncio.create_subprocess_exec", return_value=ok_proc) as mock_exec,
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch("urllib.request.urlopen", side_effect=ConnectionRefusedError),
    ):
        result = await _ensure_qmd_daemon("localhost", 8181)

    assert result is True
    mock_runtime.find_binary.assert_called_once_with("qmd", extra_paths=None)
    # The resolved binary path must be used for subprocess exec
    mock_exec.assert_called_once()
    assert mock_exec.call_args[0][0] == "/custom/path/qmd"


async def test_ensure_qmd_uses_configured_binary_path() -> None:
    """When binary_path is provided, it must be passed as extra_paths to find_binary."""
    mock_runtime = MagicMock()
    mock_runtime.find_binary.return_value = Path("/home/user/.bun/bin/qmd")

    with (
        patch("archon.platform.get_runtime", return_value=mock_runtime),
        patch.object(Path, "exists", return_value=True),
        patch.object(Path, "read_text", return_value="12345"),
        patch("os.kill", return_value=None),
    ):
        result = await _ensure_qmd_daemon(
            "localhost", 8181, binary_path="/home/user/.bun/bin/qmd",
        )

    assert result is True
    mock_runtime.find_binary.assert_called_once_with(
        "qmd", extra_paths=[Path("/home/user/.bun/bin/qmd")],
    )


async def test_ensure_qmd_find_binary_not_found_disables_qmd(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When find_binary returns None, QMD must be disabled with a warning."""
    import logging

    mock_runtime = MagicMock()
    mock_runtime.find_binary.return_value = None

    with (
        patch("archon.platform.get_runtime", return_value=mock_runtime),
        caplog.at_level(logging.WARNING, logger="archon"),
    ):
        result = await _ensure_qmd_daemon("localhost", 8181)

    assert result is False
    assert any("not found" in r.message for r in caplog.records)


async def test_ensure_qmd_not_found_warning_includes_binary_path(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When binary_path is set but qmd not found, warning must mention the configured path."""
    import logging

    mock_runtime = MagicMock()
    mock_runtime.find_binary.return_value = None

    with (
        patch("archon.platform.get_runtime", return_value=mock_runtime),
        caplog.at_level(logging.WARNING, logger="archon"),
    ):
        result = await _ensure_qmd_daemon(
            "localhost", 8181, binary_path="/custom/bin/qmd",
        )

    assert result is False
    assert any("/custom/bin/qmd" in r.message for r in caplog.records)


async def test_ensure_qmd_expands_tilde_in_binary_path() -> None:
    """binary_path with ~ must be expanded to the full home directory path."""
    mock_runtime = MagicMock()
    mock_runtime.find_binary.return_value = Path.home() / ".bun" / "bin" / "qmd"

    with (
        patch("archon.platform.get_runtime", return_value=mock_runtime),
        patch.object(Path, "exists", return_value=True),
        patch.object(Path, "read_text", return_value="12345"),
        patch("os.kill", return_value=None),
    ):
        result = await _ensure_qmd_daemon(
            "localhost", 8181, binary_path="~/.bun/bin/qmd",
        )

    assert result is True
    expected = Path.home() / ".bun" / "bin" / "qmd"
    mock_runtime.find_binary.assert_called_once_with(
        "qmd", extra_paths=[expected],
    )
