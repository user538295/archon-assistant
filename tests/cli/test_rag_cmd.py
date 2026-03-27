"""Tests for archon/cli/rag_cmd.py — Task 7.2."""
from __future__ import annotations

import argparse
import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_args(**kwargs) -> argparse.Namespace:
    defaults = dict(
        rag_command=None,
        dry_run=False,
        non_interactive=False,
        delete_db=False,
        path=None,
        collection=None,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _make_service_info(running: bool) -> MagicMock:
    info = MagicMock()
    info.running = running
    info.service_name = "archon-rag"
    info.pid = 1234 if running else None
    return info


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------

def test_rag_install_delegates_to_installer() -> None:
    mock_installer = MagicMock()
    mock_installer.return_value.run.return_value = 0

    with patch("archon.cli.rag_cmd.RagInstaller", mock_installer):
        from archon.cli.rag_cmd import _run_install
        result = _run_install(_make_args(rag_command="install"))

    assert result == 0
    mock_installer.return_value.run.assert_called_once_with(non_interactive=False)


def test_rag_install_dry_run_flag() -> None:
    mock_installer = MagicMock()
    mock_installer.return_value.run.return_value = 0

    with patch("archon.cli.rag_cmd.RagInstaller", mock_installer):
        from archon.cli.rag_cmd import _run_install
        _run_install(_make_args(rag_command="install", dry_run=True))

    mock_installer.assert_called_once_with(dry_run=True)


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------

def test_rag_uninstall_delegates() -> None:
    mock_installer = MagicMock()
    mock_installer.return_value.run_uninstall.return_value = 0

    with patch("archon.cli.rag_cmd.RagInstaller", mock_installer):
        from archon.cli.rag_cmd import _run_uninstall
        result = _run_uninstall(_make_args(rag_command="uninstall", delete_db=True))

    assert result == 0
    mock_installer.return_value.run_uninstall.assert_called_once_with(delete_db=True)


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------

def test_rag_start_calls_platform_service() -> None:
    mock_svc = MagicMock()
    mock_svc.start.return_value = 0

    with patch("archon.cli.rag_cmd.get_rag_service", return_value=mock_svc):
        from archon.cli.rag_cmd import _run_start
        result = _run_start(_make_args(rag_command="start"))

    assert result == 0
    mock_svc.start.assert_called_once()


def test_rag_stop_calls_platform_service() -> None:
    mock_svc = MagicMock()
    mock_svc.stop.return_value = 0

    with patch("archon.cli.rag_cmd.get_rag_service", return_value=mock_svc):
        from archon.cli.rag_cmd import _run_stop
        result = _run_stop(_make_args(rag_command="stop"))

    assert result == 0
    mock_svc.stop.assert_called_once()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def test_rag_status_prints_service_state(capsys: pytest.CaptureFixture[str]) -> None:
    mock_svc = MagicMock()
    mock_svc.status.return_value = _make_service_info(running=True)

    mock_store = MagicMock()
    mock_store.connect = AsyncMock()
    mock_store.list_collections = AsyncMock(return_value=[])
    mock_store.disconnect = AsyncMock()

    with (
        patch("archon.cli.rag_cmd.get_rag_service", return_value=mock_svc),
        patch("archon.cli.rag_cmd.RagStore", return_value=mock_store),
        patch("archon.cli.rag_cmd.load_config") as mock_cfg,
    ):
        mock_cfg.return_value.rag.db_path = "/tmp/rag"
        from archon.cli.rag_cmd import _run_status
        result = _run_status(_make_args(rag_command="status"))

    out = capsys.readouterr().out
    assert "running" in out.lower()
    assert result == 0


def test_rag_status_server_unreachable_prints_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    mock_svc = MagicMock()
    mock_svc.status.return_value = _make_service_info(running=False)

    with patch("archon.cli.rag_cmd.get_rag_service", return_value=mock_svc):
        from archon.cli.rag_cmd import _run_status
        result = _run_status(_make_args(rag_command="status"))

    out = capsys.readouterr().out
    assert "unreachable" in out.lower() or "stopped" in out.lower()
    assert result != 0


def test_rag_status_disconnects_on_list_collections_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    mock_svc = MagicMock()
    mock_svc.status.return_value = _make_service_info(running=True)

    mock_store = MagicMock()
    mock_store.connect = AsyncMock()
    mock_store.list_collections = AsyncMock(side_effect=RuntimeError("lock"))
    mock_store.disconnect = AsyncMock()

    with (
        patch("archon.cli.rag_cmd.get_rag_service", return_value=mock_svc),
        patch("archon.cli.rag_cmd.RagStore", return_value=mock_store),
        patch("archon.cli.rag_cmd.load_config") as mock_cfg,
    ):
        mock_cfg.return_value.rag.db_path = "/tmp/rag"
        from archon.cli.rag_cmd import _run_status
        result = _run_status(_make_args(rag_command="status"))

    mock_store.disconnect.assert_awaited_once()
    out = capsys.readouterr().out
    assert "Stats unavailable" in out
    assert result == 0


def test_rag_status_shows_unavailable_on_lock_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    mock_svc = MagicMock()
    mock_svc.status.return_value = _make_service_info(running=True)

    mock_store = MagicMock()
    mock_store.connect = AsyncMock()
    mock_store.list_collections = AsyncMock(side_effect=OSError("LanceDB lock"))
    mock_store.disconnect = AsyncMock()

    with (
        patch("archon.cli.rag_cmd.get_rag_service", return_value=mock_svc),
        patch("archon.cli.rag_cmd.RagStore", return_value=mock_store),
        patch("archon.cli.rag_cmd.load_config") as mock_cfg,
    ):
        mock_cfg.return_value.rag.db_path = "/tmp/rag"
        from archon.cli.rag_cmd import _run_status
        result = _run_status(_make_args(rag_command="status"))

    out = capsys.readouterr().out
    assert "Stats unavailable" in out


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------

def test_rag_ingest_no_args_uses_history_dir(capsys: pytest.CaptureFixture[str]) -> None:
    mock_svc = MagicMock()
    mock_svc.status.return_value = _make_service_info(running=False)

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.ingest_directory = AsyncMock(return_value=MagicMock(ingested=3, skipped=0, errors=0))
    mock_pipeline.store.disconnect = AsyncMock()

    with (
        patch("archon.cli.rag_cmd.get_rag_service", return_value=mock_svc),
        patch("archon.cli.rag_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.rag_cmd.load_config") as mock_cfg,
    ):
        mock_cfg.return_value.rag.db_path = "/tmp/rag"

        mock_cfg.return_value.history.directory = "/tmp/history"
        from archon.cli.rag_cmd import _run_ingest
        result = _run_ingest(_make_args(rag_command="ingest"))

    assert result == 0
    call_args = mock_pipeline.ingest_directory.call_args
    # path should be history sessions dir
    assert "sessions" in str(call_args[0][0])
    # collection should be derived from history directory path (basename = "sessions")
    assert call_args[0][1] == "sessions"


def test_rag_ingest_with_path_and_collection(capsys: pytest.CaptureFixture[str]) -> None:
    mock_svc = MagicMock()
    mock_svc.status.return_value = _make_service_info(running=False)

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.ingest_directory = AsyncMock(return_value=MagicMock(ingested=1, skipped=0, errors=0))
    mock_pipeline.store.disconnect = AsyncMock()

    with (
        patch("archon.cli.rag_cmd.get_rag_service", return_value=mock_svc),
        patch("archon.cli.rag_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.rag_cmd.load_config") as mock_cfg,
    ):
        mock_cfg.return_value.rag.db_path = "/tmp/rag"

        mock_cfg.return_value.history.directory = "/tmp/history"
        from archon.cli.rag_cmd import _run_ingest
        result = _run_ingest(_make_args(rag_command="ingest", path="/my/docs", collection="my-col"))

    assert result == 0
    call_args = mock_pipeline.ingest_directory.call_args
    assert str(call_args[0][0]) == "/my/docs"
    assert call_args[0][1] == "my-col"


def test_rag_ingest_aborts_when_service_running(
    capsys: pytest.CaptureFixture[str],
) -> None:
    mock_svc = MagicMock()
    mock_svc.status.return_value = _make_service_info(running=True)

    mock_pipeline = MagicMock()
    mock_pipeline.ingest_directory = AsyncMock()

    with (
        patch("archon.cli.rag_cmd.get_rag_service", return_value=mock_svc),
        patch("archon.cli.rag_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.rag_cmd.load_config") as mock_cfg,
    ):
        mock_cfg.return_value.rag.db_path = "/tmp/rag"

        mock_cfg.return_value.history.directory = "/tmp/history"
        from archon.cli.rag_cmd import _run_ingest
        result = _run_ingest(_make_args(rag_command="ingest"))

    out = capsys.readouterr().out
    assert result != 0
    mock_pipeline.ingest_directory.assert_not_awaited()


def test_rag_ingest_disconnects_on_failure(capsys: pytest.CaptureFixture[str]) -> None:
    mock_svc = MagicMock()
    mock_svc.status.return_value = _make_service_info(running=False)

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.ingest_directory = AsyncMock(side_effect=RuntimeError("ingest boom"))
    mock_pipeline.store.disconnect = AsyncMock()

    with (
        patch("archon.cli.rag_cmd.get_rag_service", return_value=mock_svc),
        patch("archon.cli.rag_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.rag_cmd.load_config") as mock_cfg,
    ):
        mock_cfg.return_value.rag.db_path = "/tmp/rag"

        mock_cfg.return_value.history.directory = "/tmp/history"
        from archon.cli.rag_cmd import _run_ingest
        result = _run_ingest(_make_args(rag_command="ingest"))

    mock_pipeline.store.disconnect.assert_awaited_once()
    assert result != 0


# ---------------------------------------------------------------------------
# main.py integration — archon rag --help exits 0
# ---------------------------------------------------------------------------

def test_main_rag_command_registered(capsys: pytest.CaptureFixture[str]) -> None:
    from archon.cli.main import main
    result = main(["rag", "--help"])
    # argparse prints help and exits 0 (or SystemExit(0)); our main converts to 0
    # The test passes if result == 0 OR SystemExit(0) is raised
    assert result == 0
    out = capsys.readouterr().out
    assert "rag" in out.lower()
