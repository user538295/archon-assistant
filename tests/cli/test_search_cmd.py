"""Tests for archon/cli/search_cmd.py — Task 7.2."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_args(**kwargs) -> argparse.Namespace:
    defaults = dict(
        search_command=None,
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
    info.service_name = "archon-search"
    info.pid = 1234 if running else None
    return info


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------

def test_search_install_delegates_to_installer() -> None:
    mock_installer = MagicMock()
    mock_installer.return_value.run.return_value = 0

    with patch("archon.cli.search_cmd.SearchInstaller",mock_installer):
        from archon.cli.search_cmd import _run_install
        result = _run_install(_make_args(search_command="install"))

    assert result == 0
    mock_installer.return_value.run.assert_called_once_with(non_interactive=False)


def test_search_install_dry_run_flag() -> None:
    mock_installer = MagicMock()
    mock_installer.return_value.run.return_value = 0

    with patch("archon.cli.search_cmd.SearchInstaller",mock_installer):
        from archon.cli.search_cmd import _run_install
        _run_install(_make_args(search_command="install", dry_run=True))

    mock_installer.assert_called_once_with(dry_run=True)


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------

def test_search_uninstall_delegates() -> None:
    mock_installer = MagicMock()
    mock_installer.return_value.run_uninstall.return_value = 0

    with patch("archon.cli.search_cmd.SearchInstaller",mock_installer):
        from archon.cli.search_cmd import _run_uninstall
        result = _run_uninstall(_make_args(search_command="uninstall", delete_db=True))

    assert result == 0
    mock_installer.return_value.run_uninstall.assert_called_once_with(delete_db=True)


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------

def test_search_start_calls_platform_service() -> None:
    mock_svc = MagicMock()
    mock_svc.start.return_value = 0

    with patch("archon.cli.search_cmd.get_search_service", return_value=mock_svc):
        from archon.cli.search_cmd import _run_start
        result = _run_start(_make_args(search_command="start"))

    assert result == 0
    mock_svc.start.assert_called_once()


def test_search_stop_calls_platform_service() -> None:
    mock_svc = MagicMock()
    mock_svc.stop.return_value = 0

    with patch("archon.cli.search_cmd.get_search_service", return_value=mock_svc):
        from archon.cli.search_cmd import _run_stop
        result = _run_stop(_make_args(search_command="stop"))

    assert result == 0
    mock_svc.stop.assert_called_once()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def test_search_status_prints_service_state(capsys: pytest.CaptureFixture[str]) -> None:
    mock_svc = MagicMock()
    mock_svc.status.return_value = _make_service_info(running=True)

    mock_store = MagicMock()
    mock_store.connect = AsyncMock()
    mock_store.list_collections = AsyncMock(return_value=[])
    mock_store.disconnect = AsyncMock()

    with (
        patch("archon.cli.search_cmd.get_search_service", return_value=mock_svc),
        patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
        patch("archon.cli.search_cmd.load_config") as mock_cfg,
    ):
        mock_cfg.return_value.search.db_path = "/tmp/rag"
        mock_cfg.return_value.search.watch = False
        from archon.cli.search_cmd import _run_status
        result = _run_status(_make_args(search_command="status"))

    out = capsys.readouterr().out
    assert "running" in out.lower()
    assert result == 0


def test_search_status_server_unreachable_prints_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    mock_svc = MagicMock()
    mock_svc.status.return_value = _make_service_info(running=False)

    with patch("archon.cli.search_cmd.get_search_service", return_value=mock_svc):
        from archon.cli.search_cmd import _run_status
        result = _run_status(_make_args(search_command="status"))

    out = capsys.readouterr().out
    assert "unreachable" in out.lower() or "stopped" in out.lower()
    assert result != 0


def test_search_status_disconnects_on_list_collections_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    mock_svc = MagicMock()
    mock_svc.status.return_value = _make_service_info(running=True)

    mock_store = MagicMock()
    mock_store.connect = AsyncMock()
    mock_store.list_collections = AsyncMock(side_effect=RuntimeError("lock"))
    mock_store.disconnect = AsyncMock()

    with (
        patch("archon.cli.search_cmd.get_search_service", return_value=mock_svc),
        patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
        patch("archon.cli.search_cmd.load_config") as mock_cfg,
    ):
        mock_cfg.return_value.search.db_path = "/tmp/rag"
        mock_cfg.return_value.search.watch = False
        from archon.cli.search_cmd import _run_status
        result = _run_status(_make_args(search_command="status"))

    mock_store.disconnect.assert_awaited_once()
    out = capsys.readouterr().out
    assert "Stats unavailable" in out
    assert result == 0


def test_search_status_shows_unavailable_on_lock_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    mock_svc = MagicMock()
    mock_svc.status.return_value = _make_service_info(running=True)

    mock_store = MagicMock()
    mock_store.connect = AsyncMock()
    mock_store.list_collections = AsyncMock(side_effect=OSError("LanceDB lock"))
    mock_store.disconnect = AsyncMock()

    with (
        patch("archon.cli.search_cmd.get_search_service", return_value=mock_svc),
        patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
        patch("archon.cli.search_cmd.load_config") as mock_cfg,
    ):
        mock_cfg.return_value.search.db_path = "/tmp/rag"
        mock_cfg.return_value.search.watch = False
        from archon.cli.search_cmd import _run_status
        result = _run_status(_make_args(search_command="status"))

    out = capsys.readouterr().out
    assert "Stats unavailable" in out


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------

def test_search_ingest_no_args_uses_history_dir(capsys: pytest.CaptureFixture[str]) -> None:
    mock_svc = MagicMock()
    mock_svc.status.return_value = _make_service_info(running=False)

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.ingest_directory = AsyncMock(return_value=MagicMock(ingested=3, skipped=0, errors=0))
    mock_pipeline.store.disconnect = AsyncMock()

    with (
        patch("archon.cli.search_cmd.get_search_service", return_value=mock_svc),
        patch("archon.cli.search_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.search_cmd.load_config") as mock_cfg,
    ):
        mock_cfg.return_value.search.db_path = "/tmp/rag"

        mock_cfg.return_value.history.directory = "/tmp/history"
        from archon.cli.search_cmd import _run_ingest
        result = _run_ingest(_make_args(search_command="ingest"))

    assert result == 0
    call_args = mock_pipeline.ingest_directory.call_args
    # path should be history sessions dir
    assert "sessions" in str(call_args[0][0])
    # collection should be derived from history directory path (basename = "sessions")
    assert call_args[0][1] == "sessions"


def test_search_ingest_with_path_and_collection(capsys: pytest.CaptureFixture[str]) -> None:
    mock_svc = MagicMock()
    mock_svc.status.return_value = _make_service_info(running=False)

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.ingest_directory = AsyncMock(return_value=MagicMock(ingested=1, skipped=0, errors=0))
    mock_pipeline.store.disconnect = AsyncMock()

    with (
        patch("archon.cli.search_cmd.get_search_service", return_value=mock_svc),
        patch("archon.cli.search_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.search_cmd.load_config") as mock_cfg,
    ):
        mock_cfg.return_value.search.db_path = "/tmp/rag"

        mock_cfg.return_value.history.directory = "/tmp/history"
        from archon.cli.search_cmd import _run_ingest
        result = _run_ingest(_make_args(search_command="ingest", path="/my/docs", collection="my-col"))

    assert result == 0
    call_args = mock_pipeline.ingest_directory.call_args
    assert str(call_args[0][0]) == "/my/docs"
    assert call_args[0][1] == "my-col"


def test_search_ingest_aborts_when_service_running(
    capsys: pytest.CaptureFixture[str],
) -> None:
    mock_svc = MagicMock()
    mock_svc.status.return_value = _make_service_info(running=True)

    mock_pipeline = MagicMock()
    mock_pipeline.ingest_directory = AsyncMock()

    with (
        patch("archon.cli.search_cmd.get_search_service", return_value=mock_svc),
        patch("archon.cli.search_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.search_cmd.load_config") as mock_cfg,
    ):
        mock_cfg.return_value.search.db_path = "/tmp/rag"

        mock_cfg.return_value.history.directory = "/tmp/history"
        from archon.cli.search_cmd import _run_ingest
        result = _run_ingest(_make_args(search_command="ingest"))

    out = capsys.readouterr().out
    assert result != 0
    mock_pipeline.ingest_directory.assert_not_awaited()


def test_search_ingest_disconnects_on_failure(capsys: pytest.CaptureFixture[str]) -> None:
    mock_svc = MagicMock()
    mock_svc.status.return_value = _make_service_info(running=False)

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.ingest_directory = AsyncMock(side_effect=RuntimeError("ingest boom"))
    mock_pipeline.store.disconnect = AsyncMock()

    with (
        patch("archon.cli.search_cmd.get_search_service", return_value=mock_svc),
        patch("archon.cli.search_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.search_cmd.load_config") as mock_cfg,
    ):
        mock_cfg.return_value.search.db_path = "/tmp/rag"

        mock_cfg.return_value.history.directory = "/tmp/history"
        from archon.cli.search_cmd import _run_ingest
        result = _run_ingest(_make_args(search_command="ingest"))

    mock_pipeline.store.disconnect.assert_awaited_once()
    assert result != 0


# ---------------------------------------------------------------------------
# main.py integration — archon rag --help exits 0
# ---------------------------------------------------------------------------

def test_main_search_command_registered(capsys: pytest.CaptureFixture[str]) -> None:
    from archon.cli.main import main
    result = main(["search", "--help"])
    # argparse prints help and exits 0 (or SystemExit(0)); our main converts to 0
    # The test passes if result == 0 OR SystemExit(0) is raised
    assert result == 0
    out = capsys.readouterr().out
    assert "search" in out.lower()


# ---------------------------------------------------------------------------
# Task 4.1 — archon rag sync
# ---------------------------------------------------------------------------


def test_sync_cli_command_prints_result(capsys: pytest.CaptureFixture[str]) -> None:
    """archon rag sync prints added/updated/removed/unchanged/errors counts."""
    from archon.cli.search_cmd import _run_sync
    from archon.search.sync import SyncResult

    mock_sync_result = SyncResult(
        added=["docs"], removed=["old_col"], unchanged=["sessions"], errors=[], skipped=[], updated=[]
    )
    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.store.disconnect = AsyncMock()
    mock_cfg = MagicMock()
    mock_cfg.search.collections = ["~/.archon/history/sessions"]

    with (
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.search_cmd.SearchCollectionSync") as MockSync,
    ):
        mock_svc.return_value.status.return_value.running = False
        MockSync.return_value.sync = AsyncMock(return_value=mock_sync_result)
        result = _run_sync(_make_args(search_command="sync"))

    out = capsys.readouterr().out
    assert "1 added" in out
    assert "0 updated" in out
    assert "1 removed" in out
    assert "1 unchanged" in out
    assert "0 errors" in out
    assert "↻" not in out
    assert result == 0


def test_sync_cli_returns_1_on_errors(capsys: pytest.CaptureFixture[str]) -> None:
    """archon rag sync returns exit code 1 when there are sync errors."""
    from archon.cli.search_cmd import _run_sync
    from archon.search.sync import SyncResult

    mock_sync_result = SyncResult(
        added=[], removed=[], unchanged=[], errors=["path does not exist: /bad"], skipped=[]
    )
    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.store.disconnect = AsyncMock()
    mock_cfg = MagicMock()
    mock_cfg.search.collections = []

    with (
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.search_cmd.SearchCollectionSync") as MockSync,
    ):
        mock_svc.return_value.status.return_value.running = False
        MockSync.return_value.sync = AsyncMock(return_value=mock_sync_result)
        result = _run_sync(_make_args(search_command="sync"))

    assert result == 1


def test_sync_cli_warns_if_service_running(capsys: pytest.CaptureFixture[str]) -> None:
    """archon rag sync prints a warning (but proceeds) if the RAG service is running."""
    from archon.cli.search_cmd import _run_sync
    from archon.search.sync import SyncResult

    mock_sync_result = SyncResult(
        added=[], removed=[], unchanged=[], errors=[], skipped=[]
    )
    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.store.disconnect = AsyncMock()
    mock_cfg = MagicMock()
    mock_cfg.search.collections = []

    with (
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.search_cmd.SearchCollectionSync") as MockSync,
    ):
        mock_svc.return_value.status.return_value.running = True
        MockSync.return_value.sync = AsyncMock(return_value=mock_sync_result)
        result = _run_sync(_make_args(search_command="sync"))

    out = capsys.readouterr().out
    assert "warning" in out.lower() or "running" in out.lower()
    assert result == 0  # proceeds despite warning


# ---------------------------------------------------------------------------
# Task 4.2 — archon rag collection list
# ---------------------------------------------------------------------------


def _make_collection_list_args(**kwargs) -> argparse.Namespace:
    defaults = dict(
        search_command="collection",
        collection_command="list",
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _make_collection_info(name: str, doc_count: int = 5, chunk_count: int = 20) -> MagicMock:
    info = MagicMock()
    info.name = name
    info.doc_count = doc_count
    info.chunk_count = chunk_count
    return info


def test_collection_list_shows_path_and_counts(capsys: pytest.CaptureFixture[str]) -> None:
    """Indexed collection shows path from manifest + doc/chunk counts with 'indexed' status."""
    from archon.cli.search_cmd import _run_collection_list

    mock_store = MagicMock()
    mock_store.connect = AsyncMock()
    mock_store.disconnect = AsyncMock()
    mock_store.list_collections = AsyncMock(
        return_value=[_make_collection_info("sessions", doc_count=3, chunk_count=12)]
    )

    manifest_data = '{"sessions": "/home/user/.archon/history/sessions"}'

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = "/tmp/rag"
    mock_cfg.search.collections = ["/home/user/.archon/history/sessions"]

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
        patch("pathlib.Path.exists", return_value=True),
        patch("pathlib.Path.read_text", return_value=manifest_data),
    ):
        result = _run_collection_list(_make_collection_list_args())

    out = capsys.readouterr().out
    assert "sessions" in out
    assert "docs=3" in out
    assert "chunks=12" in out
    assert "indexed" in out
    assert result == 0


def test_collection_list_marks_orphans(capsys: pytest.CaptureFixture[str]) -> None:
    """Collection in manifest but not in config is marked as 'orphan (managed)'."""
    from archon.cli.search_cmd import _run_collection_list

    mock_store = MagicMock()
    mock_store.connect = AsyncMock()
    mock_store.disconnect = AsyncMock()
    mock_store.list_collections = AsyncMock(
        return_value=[_make_collection_info("old_col", doc_count=1, chunk_count=5)]
    )

    # old_col is in manifest but config has no collections
    manifest_data = '{"old_col": "/tmp/old_col"}'

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = "/tmp/rag"
    mock_cfg.search.collections = []

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
        patch("pathlib.Path.exists", return_value=True),
        patch("pathlib.Path.read_text", return_value=manifest_data),
    ):
        result = _run_collection_list(_make_collection_list_args())

    out = capsys.readouterr().out
    assert "old_col" in out
    assert "orphan (managed)" in out
    assert result == 0


def test_collection_list_distinguishes_managed_orphan_from_unmanaged(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Collections not in manifest AND not in config are marked 'unmanaged'."""
    from archon.cli.search_cmd import _run_collection_list

    mock_store = MagicMock()
    mock_store.connect = AsyncMock()
    mock_store.disconnect = AsyncMock()
    mock_store.list_collections = AsyncMock(
        return_value=[
            _make_collection_info("orphan_col"),
            _make_collection_info("unmanaged_col"),
        ]
    )

    # orphan_col in manifest, unmanaged_col NOT in manifest
    manifest_data = '{"orphan_col": "/tmp/orphan_col"}'

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = "/tmp/rag"
    mock_cfg.search.collections = []

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
        patch("pathlib.Path.exists", return_value=True),
        patch("pathlib.Path.read_text", return_value=manifest_data),
    ):
        result = _run_collection_list(_make_collection_list_args())

    out = capsys.readouterr().out
    assert "orphan_col" in out
    assert "orphan (managed)" in out
    assert "unmanaged_col" in out
    assert "unmanaged" in out
    # unmanaged_col should NOT be labeled as orphan (managed)
    lines = out.splitlines()
    unmanaged_line = next((l for l in lines if "unmanaged_col" in l), "")
    assert "orphan" not in unmanaged_line
    assert result == 0


def test_collection_list_shows_unindexed_config_paths(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Paths in config but not yet in LanceDB are printed as '(not yet indexed)'."""
    from archon.cli.search_cmd import _run_collection_list

    mock_store = MagicMock()
    mock_store.connect = AsyncMock()
    mock_store.disconnect = AsyncMock()
    mock_store.list_collections = AsyncMock(return_value=[])  # nothing in LanceDB

    manifest_data = "{}"

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = "/tmp/rag"
    mock_cfg.search.collections = ["/home/user/docs"]

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
        patch("pathlib.Path.exists", return_value=True),
        patch("pathlib.Path.read_text", return_value=manifest_data),
    ):
        result = _run_collection_list(_make_collection_list_args())

    out = capsys.readouterr().out
    assert "not yet indexed" in out
    assert "/home/user/docs" in out
    assert result == 0


def test_collection_list_empty(capsys: pytest.CaptureFixture[str]) -> None:
    """No collections and no config paths prints 'No collections found.'"""
    from archon.cli.search_cmd import _run_collection_list

    mock_store = MagicMock()
    mock_store.connect = AsyncMock()
    mock_store.disconnect = AsyncMock()
    mock_store.list_collections = AsyncMock(return_value=[])

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = "/tmp/rag"
    mock_cfg.search.collections = []

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
        patch("pathlib.Path.exists", return_value=False),
    ):
        result = _run_collection_list(_make_collection_list_args())

    out = capsys.readouterr().out
    assert "No collections found." in out
    assert result == 0


# ---------------------------------------------------------------------------
# Task 4.3 — archon rag collection add <path>
# ---------------------------------------------------------------------------


def _make_collection_add_args(path: str = "/tmp/my_docs", **kwargs) -> argparse.Namespace:
    defaults = dict(
        search_command="collection",
        collection_command="add",
        path=path,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_collection_add_appends_to_config_and_ingests(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """Happy path: adds new path to config and ingests it."""
    from archon.cli.search_cmd import _run_collection_add

    path = str(tmp_path / "docs")

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.ingest_directory = AsyncMock(return_value=[])
    mock_pipeline.store.disconnect = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = str(tmp_path / "rag")
    mock_cfg.search.collections = []

    config_file = tmp_path / "config.toml"
    config_file.write_text('[search]\ncollections = []\n')

    with (
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.search_cmd.config_collections_append") as mock_append,
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_add(_make_collection_add_args(path=path))

    mock_append.assert_called_once()
    mock_pipeline.ingest_directory.assert_awaited_once()
    out = capsys.readouterr().out
    assert "Collection added and indexed" in out
    assert result == 0


def test_add_prints_progress(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """_run_collection_add passes a progress callback that prints progress lines."""
    from archon.cli.search_cmd import _run_collection_add

    path = str(tmp_path / "docs")

    # ingest_directory will call the progress_cb with (1, 1)
    async def _fake_ingest(resolved_path, col, progress_cb=None, **kwargs):
        if progress_cb is not None:
            progress_cb(1, 1)
        return []

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.ingest_directory = _fake_ingest
    mock_pipeline.store.disconnect = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = str(tmp_path / "rag")
    mock_cfg.search.collections = []

    with (
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.search_cmd.config_collections_append"),
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_add(_make_collection_add_args(path=path))

    out = capsys.readouterr().out
    assert result == 0
    # Progress output contains [done/total] or similar indicator
    assert "[1/1]" in out or "1/1" in out


def test_sync_prints_progress(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """_run_sync passes a progress callback that prints progress lines during ingest."""
    from archon.cli.search_cmd import _run_sync

    # ingest_directory will call the progress_cb with (1, 1)
    async def _fake_ingest(resolved_path, col, progress_cb=None, **kwargs):
        if progress_cb is not None:
            progress_cb(1, 1)
        return []

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.ingest_directory = _fake_ingest
    mock_pipeline.store.disconnect = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = str(tmp_path / "rag")
    mock_cfg.search.collections = [str(tmp_path / "docs")]

    # Use real SearchCollectionSync so the progress_cb flows through
    from archon.search.sync import SearchCollectionSync, SyncResult

    async def _fake_sync(collections, progress_cb=None):
        # Simulate one path being added, calling progress_cb as ingest_directory would
        if progress_cb is not None:
            progress_cb(1, 1)
        return SyncResult(added=["docs"], removed=[], unchanged=[], errors=[], skipped=[])

    with (
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.search_cmd.SearchCollectionSync") as MockSync,
    ):
        mock_svc.return_value.status.return_value.running = False
        MockSync.return_value.sync = _fake_sync
        result = _run_sync(_make_args(search_command="sync"))

    out = capsys.readouterr().out
    assert result == 0
    assert "[1/1]" in out or "1/1" in out


def test_collection_add_already_registered_exits_0(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """If path is already in config (after normalisation), print message and exit 0."""
    from archon.cli.search_cmd import _run_collection_add

    path = str(tmp_path / "docs")

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = str(tmp_path / "rag")
    # Same path already in collections
    mock_cfg.search.collections = [path]

    with (
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_add(_make_collection_add_args(path=path))

    out = capsys.readouterr().out
    assert "Already registered" in out
    assert result == 0


def test_collection_add_normalizes_tilde(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """Tilde paths are normalised for duplicate detection."""
    from archon.cli.search_cmd import _run_collection_add
    from pathlib import Path

    home = Path.home()
    # Use a subdirectory under home for tilde expansion
    rel = "archon_test_docs_4321"
    tilde_path = f"~/{rel}"
    abs_path = str(home / rel)

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = str(tmp_path / "rag")
    # Store the absolute path in config — should still be detected as duplicate
    mock_cfg.search.collections = [abs_path]

    with (
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_add(_make_collection_add_args(path=tilde_path))

    out = capsys.readouterr().out
    assert "Already registered" in out
    assert result == 0


def test_collection_add_warns_if_service_running(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """Warns about write conflicts when service is running, but does not block."""
    from archon.cli.search_cmd import _run_collection_add

    path = str(tmp_path / "docs")

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.ingest_directory = AsyncMock(return_value=[])
    mock_pipeline.store.disconnect = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = str(tmp_path / "rag")
    mock_cfg.search.collections = []

    with (
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.search_cmd.config_collections_append"),
    ):
        mock_svc.return_value.status.return_value.running = True
        result = _run_collection_add(_make_collection_add_args(path=path))

    out = capsys.readouterr().out
    assert "warning" in out.lower() or "conflict" in out.lower() or "running" in out.lower()
    # Should proceed (no block)
    mock_pipeline.ingest_directory.assert_awaited_once()
    assert result == 0


def test_collection_add_uses_naive_name_collision_resolved_on_next_sync(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """When no manifest entry for path, derives name via path_to_collection_name."""
    from archon.cli.search_cmd import _run_collection_add
    from archon.search.sync import path_to_collection_name

    path = str(tmp_path / "my_project")
    expected_name = path_to_collection_name(path)

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.ingest_directory = AsyncMock(return_value=[])
    mock_pipeline.store.disconnect = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = str(tmp_path / "rag")
    mock_cfg.search.collections = []

    with (
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.search_cmd.config_collections_append"),
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_add(_make_collection_add_args(path=path))

    call_args = mock_pipeline.ingest_directory.call_args
    actual_name = call_args[0][1]
    assert actual_name == expected_name
    assert result == 0


def test_collection_add_ingest_error_path_stays_in_config(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """On ingest failure, path stays in config (already appended) and returns exit 1."""
    from archon.cli.search_cmd import _run_collection_add

    path = str(tmp_path / "docs")

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.ingest_directory = AsyncMock(side_effect=RuntimeError("disk full"))
    mock_pipeline.store.disconnect = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = str(tmp_path / "rag")
    mock_cfg.search.collections = []

    mock_append = MagicMock()

    with (
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.search_cmd.config_collections_append", mock_append),
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_add(_make_collection_add_args(path=path))

    # Config append happens before ingest attempt
    mock_append.assert_called_once()
    out = capsys.readouterr().out
    assert "disk full" in out or "error" in out.lower()
    assert result == 1


def test_config_collections_append_writes_tomlkit(tmp_path) -> None:
    """_config_collections_append appends path to [search] collections array."""
    import tomlkit
    from archon.config.config_rw import config_collections_append

    config_file = tmp_path / "config.toml"
    config_file.write_text('[search]\ncollections = ["/existing/path"]\n')

    config_collections_append(config_file, "/new/path")

    doc = tomlkit.parse(config_file.read_text())
    assert "/new/path" in doc["search"]["collections"]
    assert "/existing/path" in doc["search"]["collections"]


def test_config_collections_append_preserves_existing_comments(tmp_path) -> None:
    """_config_collections_append preserves TOML comments and formatting."""
    import tomlkit
    from archon.config.config_rw import config_collections_append

    config_file = tmp_path / "config.toml"
    config_file.write_text(
        '# Archon config\n[search]\n# list of paths\ncollections = ["/a"]\n'
    )

    config_collections_append(config_file, "/b")

    content = config_file.read_text()
    assert "# Archon config" in content
    assert "# list of paths" in content
    doc = tomlkit.parse(content)
    assert "/b" in doc["search"]["collections"]


def test_collection_add_integration(tmp_path) -> None:
    """Integration test: full _run_collection_add with real tomlkit config write."""
    from archon.cli.search_cmd import _run_collection_add
    from archon.search.sync import path_to_collection_name
    import tomlkit

    path = str(tmp_path / "some_docs")

    config_file = tmp_path / "config.toml"
    config_file.write_text('[search]\ncollections = []\n')

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.ingest_directory = AsyncMock(return_value=[])
    mock_pipeline.store.disconnect = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = str(tmp_path / "rag")
    mock_cfg.search.collections = []

    with (
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.search_cmd._CONFIG_PATH", config_file),
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_add(_make_collection_add_args(path=path))

    assert result == 0
    doc = tomlkit.parse(config_file.read_text())
    assert path in doc["search"]["collections"]
    # C1-T-5: verify col_name passed to ingest_directory matches path_to_collection_name
    assert mock_pipeline.ingest_directory.call_args[0][1] == path_to_collection_name(path)


# ---------------------------------------------------------------------------
# C1-T-1: manifest lookup hit path
# ---------------------------------------------------------------------------


def test_collection_add_uses_manifest_name_when_available(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """When the path is already tracked in the manifest, use its collection name."""
    import json
    from archon.cli.search_cmd import _run_collection_add
    from pathlib import Path

    path = str(tmp_path / "my_docs")
    resolved_path = str(Path(path).expanduser().resolve())

    # Create manifest: collection_name → source_path (reverse: path → name lookup)
    rag_dir = tmp_path / "rag"
    rag_dir.mkdir()
    manifest = {"my-collection": resolved_path}
    (rag_dir / "sync_manifest.json").write_text(json.dumps(manifest))

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.ingest_directory = AsyncMock(return_value=[])
    mock_pipeline.store.disconnect = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = str(rag_dir)
    mock_cfg.search.collections = []

    with (
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.search_cmd.config_collections_append"),
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_add(_make_collection_add_args(path=path))

    assert result == 0
    call_args = mock_pipeline.ingest_directory.call_args
    assert call_args[0][1] == "my-collection"


# ---------------------------------------------------------------------------
# C1-T-2: verify col_name and resolved path in happy path test
# ---------------------------------------------------------------------------


def test_collection_add_appends_to_config_and_ingests_verified(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """Happy path with explicit assertions on ingest_directory call arguments."""
    from archon.cli.search_cmd import _run_collection_add
    from archon.search.sync import path_to_collection_name
    from pathlib import Path

    path = str(tmp_path / "docs")
    resolved_path = Path(path).expanduser().resolve()
    expected_col_name = path_to_collection_name(path)

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.ingest_directory = AsyncMock(return_value=[])
    mock_pipeline.store.disconnect = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = str(tmp_path / "rag")
    mock_cfg.search.collections = []

    with (
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.search_cmd.config_collections_append"),
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_add(_make_collection_add_args(path=path))

    assert result == 0
    call_args = mock_pipeline.ingest_directory.call_args
    assert call_args[0][0] == resolved_path
    assert call_args[0][1] == expected_col_name


# ---------------------------------------------------------------------------
# C1-T-3: _config_collections_append creates missing [search] section
# ---------------------------------------------------------------------------


def test_config_collections_append_creates_missing_rag_section(tmp_path) -> None:
    """_config_collections_append creates [search] section if not present."""
    import tomlkit
    from archon.config.config_rw import config_collections_append

    config_file = tmp_path / "config.toml"
    config_file.write_text('[logging]\nlevel = "info"\n')

    config_collections_append(config_file, "/new/path")

    doc = tomlkit.parse(config_file.read_text())
    assert "/new/path" in doc["search"]["collections"]


# ---------------------------------------------------------------------------
# C1-T-4: non-existent directory — ingest fails, path stays in config
# ---------------------------------------------------------------------------


def test_collection_add_nonexistent_directory_ingest_fails(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """Ingest failure for non-existent dir: path stays in config, exit 1."""
    from archon.cli.search_cmd import _run_collection_add

    path = str(tmp_path / "does_not_exist")

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.ingest_directory = AsyncMock(
        side_effect=FileNotFoundError("no such directory")
    )
    mock_pipeline.store.disconnect = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = str(tmp_path / "rag")
    mock_cfg.search.collections = []

    mock_append = MagicMock()

    with (
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.search_cmd.config_collections_append", mock_append),
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_add(_make_collection_add_args(path=path))

    # Path stays in config even though ingest failed
    mock_append.assert_called_once()
    out = capsys.readouterr().out
    assert "no such directory" in out or "error" in out.lower()
    assert result == 1


# ---------------------------------------------------------------------------
# Task 4.4 — archon rag collection remove <path>
# ---------------------------------------------------------------------------


def _make_collection_remove_args(
    path: str = "/tmp/my_docs",
    force: bool = False,
    dry_run: bool = False,
    **kwargs,
) -> argparse.Namespace:
    defaults = dict(
        search_command="collection",
        collection_command="remove",
        path=path,
        force=force,
        dry_run=dry_run,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_collection_remove_removes_from_config_and_drops(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """Happy path: path in config, service stopped, config remove called, store.drop_collection called, prints 'Collection removed', returns 0."""
    from archon.cli.search_cmd import _run_collection_remove

    path = str(tmp_path / "docs")

    mock_store = MagicMock()
    mock_store.connect = AsyncMock()
    mock_store.disconnect = AsyncMock()
    mock_store.drop_collection = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = str(tmp_path / "rag")
    mock_cfg.search.collections = [path]

    with (
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
        patch("archon.cli.search_cmd.config_collections_remove") as mock_remove,
        patch("archon.cli.search_cmd.manifest_lookup_by_path", return_value=None),
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_remove(_make_collection_remove_args(path=path))

    mock_remove.assert_called_once()
    mock_store.drop_collection.assert_awaited_once()
    # C1-T-2: verify col_name passed to drop_collection
    from archon.search.sync import path_to_collection_name
    call_args = mock_store.drop_collection.call_args
    assert call_args[0][0] == path_to_collection_name(path)
    out = capsys.readouterr().out
    assert "Collection removed" in out
    assert result == 0


def test_collection_remove_path_not_in_config_exits_1(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """Path not in config → 'Error: not in collections', exit 1."""
    from archon.cli.search_cmd import _run_collection_remove

    path = str(tmp_path / "docs")

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = str(tmp_path / "rag")
    mock_cfg.search.collections = []  # path not in config

    with (
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_remove(_make_collection_remove_args(path=path))

    out = capsys.readouterr().out
    assert "Error: not in collections" in out
    assert result == 1


def test_collection_remove_service_running_without_force_exits_1(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """Service running, force=False → error with stop instructions, exit 1, nothing touched."""
    from archon.cli.search_cmd import _run_collection_remove

    path = str(tmp_path / "docs")

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = str(tmp_path / "rag")
    mock_cfg.search.collections = [path]

    mock_config_remove = MagicMock()
    mock_store = MagicMock()
    mock_store.drop_collection = AsyncMock()

    with (
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.config_collections_remove", mock_config_remove),
        patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
    ):
        mock_svc.return_value.status.return_value.running = True
        result = _run_collection_remove(_make_collection_remove_args(path=path, force=False))

    out = capsys.readouterr().out
    assert result == 1
    # Neither config nor store should be touched
    mock_config_remove.assert_not_called()
    mock_store.drop_collection.assert_not_awaited()
    # Should mention stop instructions
    assert "stop" in out.lower() or "running" in out.lower()


def test_collection_remove_service_running_with_force_proceeds(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """Service running, force=True → warning printed, proceeds to remove, returns 0."""
    from archon.cli.search_cmd import _run_collection_remove

    path = str(tmp_path / "docs")

    mock_store = MagicMock()
    mock_store.connect = AsyncMock()
    mock_store.disconnect = AsyncMock()
    mock_store.drop_collection = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = str(tmp_path / "rag")
    mock_cfg.search.collections = [path]

    with (
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
        patch("archon.cli.search_cmd.config_collections_remove"),
        patch("archon.cli.search_cmd.manifest_lookup_by_path", return_value=None),
    ):
        mock_svc.return_value.status.return_value.running = True
        result = _run_collection_remove(_make_collection_remove_args(path=path, force=True))

    out = capsys.readouterr().out
    assert "warning" in out.lower() or "Warning" in out
    assert result == 0


def test_config_collections_remove_normalizes_tilde(tmp_path) -> None:
    """Stores ~/docs in config, remove called with expanded path — entry is removed."""
    import tomlkit
    from archon.config.config_rw import config_collections_remove
    from pathlib import Path

    tilde_path = "~/archon_test_remove_docs_8765"
    abs_path = str(Path(tilde_path).expanduser())

    config_file = tmp_path / "config.toml"
    config_file.write_text(f'[search]\ncollections = ["{tilde_path}"]\n')

    config_collections_remove(config_file, abs_path)

    doc = tomlkit.parse(config_file.read_text())
    assert tilde_path not in doc["search"]["collections"]
    assert abs_path not in doc["search"]["collections"]


def test_collection_remove_integration(tmp_path) -> None:
    """Integration: real tomlkit write — path removed from config file after remove call."""
    import tomlkit
    from archon.cli.search_cmd import _run_collection_remove
    from pathlib import Path

    path = str(tmp_path / "some_docs")

    config_file = tmp_path / "config.toml"
    config_file.write_text(f'[search]\ncollections = ["{path}"]\n')

    mock_store = MagicMock()
    mock_store.connect = AsyncMock()
    mock_store.disconnect = AsyncMock()
    mock_store.drop_collection = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = str(tmp_path / "rag")
    mock_cfg.search.collections = [path]

    with (
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
        patch("archon.cli.search_cmd._CONFIG_PATH", config_file),
        patch("archon.cli.search_cmd.manifest_lookup_by_path", return_value=None),
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_remove(_make_collection_remove_args(path=path))

    assert result == 0
    doc = tomlkit.parse(config_file.read_text())
    assert path not in doc["search"]["collections"]


# ---------------------------------------------------------------------------
# C1-T-1: non-KeyError drop exception leaves config intact
# ---------------------------------------------------------------------------


def test_collection_remove_drop_failure_leaves_config_intact(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """If drop_collection raises a non-KeyError exception, config is NOT touched and exit code is 1."""
    from archon.cli.search_cmd import _run_collection_remove

    path = str(tmp_path / "docs")

    mock_store = MagicMock()
    mock_store.connect = AsyncMock()
    mock_store.disconnect = AsyncMock()
    mock_store.drop_collection = AsyncMock(side_effect=RuntimeError("LanceDB error"))

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = str(tmp_path / "rag")
    mock_cfg.search.collections = [path]

    with (
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
        patch("archon.cli.search_cmd.config_collections_remove") as mock_remove,
        patch("archon.cli.search_cmd.manifest_lookup_by_path", return_value=None),
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_remove(_make_collection_remove_args(path=path))

    # Config must NOT be touched when drop fails
    mock_remove.assert_not_called()
    assert result == 1
    out = capsys.readouterr().out
    assert any(kw in out for kw in ("Drop failed", "LanceDB error", "error"))


# ---------------------------------------------------------------------------
# C1-T-3: manifest-hit path — col_name from manifest passed to drop_collection
# ---------------------------------------------------------------------------


def test_collection_remove_uses_manifest_name_for_drop(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """When a manifest entry exists for the path, that collection name is passed to drop_collection."""
    import json as json_mod
    from pathlib import Path as _Path
    from archon.cli.search_cmd import _run_collection_remove

    path = str(tmp_path / "docs")
    resolved = str(_Path(path).expanduser().resolve())
    special_name = "my-special-collection"

    # Create the manifest file so manifest_lookup_by_path (real function) returns the special name
    rag_dir = tmp_path / "rag"
    rag_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = rag_dir / "sync_manifest.json"
    manifest_path.write_text(json_mod.dumps({special_name: resolved}))

    mock_store = MagicMock()
    mock_store.connect = AsyncMock()
    mock_store.disconnect = AsyncMock()
    mock_store.drop_collection = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = str(rag_dir)
    mock_cfg.search.collections = [path]

    with (
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
        patch("archon.cli.search_cmd.config_collections_remove"),
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_remove(_make_collection_remove_args(path=path))

    assert result == 0
    call_args = mock_store.drop_collection.call_args
    assert call_args[0][0] == special_name


# ---------------------------------------------------------------------------
# Task 4.3 — --dry-run flag for collection remove
# ---------------------------------------------------------------------------


def test_collection_remove_dry_run_prints_without_executing(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """--dry-run prints config entry + LanceDB table name but does NOT call drop/remove."""
    from archon.cli.search_cmd import _run_collection_remove

    path = str(tmp_path / "docs")

    mock_store = MagicMock()
    mock_store.connect = AsyncMock()
    mock_store.disconnect = AsyncMock()
    mock_store.drop_collection = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = str(tmp_path / "rag")
    mock_cfg.search.collections = [path]

    with (
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.SearchStore") as mock_rag_store_cls,
        patch("archon.cli.search_cmd.config_collections_remove") as mock_remove,
        patch("archon.cli.search_cmd.manifest_lookup_by_path", return_value=None),
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_remove(
            _make_collection_remove_args(path=path, dry_run=True)
        )

    # Must return 0 (success, nothing to undo)
    assert result == 0
    # Must NOT execute actual removal
    mock_remove.assert_not_called()
    mock_store.drop_collection.assert_not_awaited()
    # Dry-run must not call the RAG service or instantiate SearchStore
    mock_svc.assert_not_called()
    mock_rag_store_cls.assert_not_called()
    # Must print what WOULD be removed
    out = capsys.readouterr().out
    assert path in out
    from archon.search.sync import path_to_collection_name
    expected_col_name = path_to_collection_name(path)
    assert expected_col_name in out


def test_collection_remove_dry_run_and_force_flags_are_mutually_exclusive(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """--dry-run and --force together → error message and return 1."""
    from archon.cli.search_cmd import _run_collection_remove

    path = str(tmp_path / "docs")

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = str(tmp_path / "rag")
    mock_cfg.search.collections = [path]

    mock_store = MagicMock()
    mock_store.drop_collection = AsyncMock()

    with (
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
        patch("archon.cli.search_cmd.config_collections_remove") as mock_remove,
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_remove(
            _make_collection_remove_args(path=path, dry_run=True, force=True)
        )

    assert result == 1
    mock_remove.assert_not_called()
    mock_store.drop_collection.assert_not_awaited()
    out = capsys.readouterr().out
    assert "mutually exclusive" in out.lower() or "cannot" in out.lower() or "error" in out.lower()


# ---------------------------------------------------------------------------
# Task 4.5 — help subcommands and argparser registration
# ---------------------------------------------------------------------------


def test_search_help_prints_usage(capsys: pytest.CaptureFixture[str]) -> None:
    """run_rag with search_command='help' calls print_help and returns 0."""
    import argparse
    from archon.cli.search_cmd import run_search

    p = argparse.ArgumentParser(prog="archon rag")
    p.add_argument("--install", help="install rag")
    args = argparse.Namespace(search_command="help")
    result = run_search(args, search_parser=p)

    assert result == 0
    out = capsys.readouterr().out
    assert "archon rag" in out or "usage" in out.lower()
    assert "install" in out or "collection" in out or "usage" in out.lower()


def test_search_no_subcommand_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    """run_rag with search_command=None prints help and returns 0."""
    import argparse
    from archon.cli.search_cmd import run_search

    p = argparse.ArgumentParser(prog="archon rag")
    p.add_argument("--install", help="install rag")
    args = argparse.Namespace(search_command=None)
    result = run_search(args, search_parser=p)

    assert result == 0
    out = capsys.readouterr().out
    assert "archon rag" in out or "usage" in out.lower()
    assert "install" in out or "collection" in out or "usage" in out.lower()


def test_collection_help_prints_usage(capsys: pytest.CaptureFixture[str]) -> None:
    """_run_collection with collection_command='help' prints help and returns 0."""
    import argparse
    from archon.cli.search_cmd import _run_collection

    p = argparse.ArgumentParser(prog="archon rag collection")
    p.add_argument("--add", help="add collection")
    args = argparse.Namespace(search_command="collection", collection_command="help")
    result = _run_collection(args, collection_parser=p)

    assert result == 0
    out = capsys.readouterr().out
    assert "archon rag collection" in out or "usage" in out.lower()
    assert "add" in out or "remove" in out or "usage" in out.lower()


def test_collection_no_subcommand_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    """_run_collection with collection_command=None prints help and returns 0."""
    import argparse
    from archon.cli.search_cmd import _run_collection

    p = argparse.ArgumentParser(prog="archon rag collection")
    p.add_argument("--add", help="add collection")
    args = argparse.Namespace(search_command="collection", collection_command=None)
    result = _run_collection(args, collection_parser=p)

    assert result == 0
    out = capsys.readouterr().out
    assert "archon rag collection" in out or "usage" in out.lower()
    assert "add" in out or "remove" in out or "usage" in out.lower()


def test_search_help_no_parser_prints_fallback(capsys: pytest.CaptureFixture[str]) -> None:
    """run_rag with search_command='help' and search_parser=None prints fallback and returns 0."""
    import argparse
    from archon.cli.search_cmd import run_search

    args = argparse.Namespace(search_command="help")
    result = run_search(args, search_parser=None)

    assert result == 0
    out = capsys.readouterr().out
    assert "archon rag" in out.lower() or "usage" in out.lower()


def test_search_no_subcommand_no_parser_prints_fallback(capsys: pytest.CaptureFixture[str]) -> None:
    """run_rag with search_command=None and search_parser=None prints fallback and returns 0."""
    import argparse
    from archon.cli.search_cmd import run_search

    args = argparse.Namespace(search_command=None)
    result = run_search(args, search_parser=None)

    assert result == 0
    out = capsys.readouterr().out
    assert "install" in out or "usage" in out.lower()


def test_collection_help_no_parser_prints_fallback(capsys: pytest.CaptureFixture[str]) -> None:
    """_run_collection with collection_command='help' and collection_parser=None prints fallback and returns 0."""
    import argparse
    from archon.cli.search_cmd import _run_collection

    args = argparse.Namespace(search_command="collection", collection_command="help")
    result = _run_collection(args, collection_parser=None)

    assert result == 0
    out = capsys.readouterr().out
    assert "list" in out or "usage" in out.lower()


# ---------------------------------------------------------------------------
# Task 4.1 (FEAT-022) — archon rag collection info / reindex
# ---------------------------------------------------------------------------


def _make_collection_meta_args(collection_name: str = "sessions", **kwargs) -> argparse.Namespace:
    defaults = dict(
        search_command="collection",
        collection_command="info",
        collection_name=collection_name,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _make_collection_reindex_args(collection_name: str = "sessions", **kwargs) -> argparse.Namespace:
    defaults = dict(
        search_command="collection",
        collection_command="reindex",
        collection_name=collection_name,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_collection_info_output(capsys: pytest.CaptureFixture[str]) -> None:
    """info fetches CollectionMeta and prints name, description, doc_count, centroid present."""
    from archon.cli.search_cmd import _run_collection_info
    from archon.search.collection_meta import CollectionMeta

    meta = CollectionMeta(
        name="sessions",
        description="Daily session logs",
        centroid=[0.1, 0.2, 0.3],
        doc_count=42,
        chunk_count=180,
        embedding_model="BAAI/bge-small-en-v1.5",
    )

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.get_collection_meta = AsyncMock(return_value=meta)
    mock_pipeline.store.disconnect = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = "/tmp/rag"

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.create_pipeline", return_value=mock_pipeline),
    ):
        result = _run_collection_info(_make_collection_meta_args(collection_name="sessions"))

    out = capsys.readouterr().out
    assert "sessions" in out
    assert "Daily session logs" in out
    assert "42" in out
    assert "present" in out.lower() or "centroid" in out.lower()
    assert result == 0


def test_collection_info_no_centroid(capsys: pytest.CaptureFixture[str]) -> None:
    """info handles centroid=None gracefully — prints 'absent' or equivalent."""
    from archon.cli.search_cmd import _run_collection_info
    from archon.search.collection_meta import CollectionMeta

    meta = CollectionMeta(
        name="docs",
        description=None,
        centroid=None,
        doc_count=5,
        chunk_count=20,
        embedding_model="BAAI/bge-small-en-v1.5",
    )

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.get_collection_meta = AsyncMock(return_value=meta)
    mock_pipeline.store.disconnect = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = "/tmp/rag"

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.create_pipeline", return_value=mock_pipeline),
    ):
        result = _run_collection_info(_make_collection_meta_args(collection_name="docs"))

    out = capsys.readouterr().out
    assert "docs" in out
    assert "absent" in out.lower() or "none" in out.lower() or "no centroid" in out.lower()
    assert result == 0


def test_collection_reindex_prints_progress(capsys: pytest.CaptureFixture[str]) -> None:
    """reindex calls ingest_directory with force_regenerate_description=True and prints progress."""
    from archon.cli.search_cmd import _run_collection_reindex
    from archon.search.sync import path_to_collection_name

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.ingest_directory = AsyncMock(return_value=[MagicMock(status="ok", chunks_created=5)] * 3)
    mock_pipeline.store.disconnect = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = "/tmp/rag"
    mock_cfg.search.collections = ["/tmp/sessions"]

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.search_cmd.path_to_collection_name", return_value="sessions"),
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_reindex(_make_collection_reindex_args(collection_name="sessions"))

    # Must be called with force_regenerate_description=True
    call_kwargs = mock_pipeline.ingest_directory.call_args[1]
    assert call_kwargs.get("force_regenerate_description") is True

    out = capsys.readouterr().out
    assert result == 0
    # Some progress/completion message printed
    assert any(w in out.lower() for w in ("reindex", "complete", "ok", "ingested"))


def test_collection_info_not_found(capsys: pytest.CaptureFixture[str]) -> None:
    """info returns exit code 1 with error message when collection does not exist."""
    from archon.cli.search_cmd import _run_collection_info

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.get_collection_meta = AsyncMock(return_value=None)
    mock_pipeline.store.disconnect = AsyncMock()

    mock_cfg = MagicMock()

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.create_pipeline", return_value=mock_pipeline),
    ):
        result = _run_collection_info(_make_collection_meta_args(collection_name="nonexistent"))

    out = capsys.readouterr().out
    assert result == 1
    assert "nonexistent" in out
    assert "not found" in out.lower() or "error" in out.lower()


def test_collection_reindex_not_in_config(capsys: pytest.CaptureFixture[str]) -> None:
    """reindex returns exit code 1 with error message when collection not in config."""
    from archon.cli.search_cmd import _run_collection_reindex

    mock_cfg = MagicMock()
    mock_cfg.search.collections = ["/tmp/other_path"]

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.path_to_collection_name", return_value="other"),
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_reindex(_make_collection_reindex_args(collection_name="sessions"))

    out = capsys.readouterr().out
    assert result == 1
    assert "sessions" in out
    assert "not found" in out.lower() or "error" in out.lower()


def test_collection_reindex_blocked_when_service_running(capsys: pytest.CaptureFixture[str]) -> None:
    """reindex returns exit code 1 with error message when RAG service is running."""
    from archon.cli.search_cmd import _run_collection_reindex

    with (
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.create_pipeline") as mock_create_pipeline,
    ):
        mock_svc.return_value.status.return_value.running = True
        result = _run_collection_reindex(_make_collection_reindex_args(collection_name="sessions"))

    out = capsys.readouterr().out
    assert result == 1
    assert "running" in out.lower() or "service" in out.lower()
    mock_create_pipeline.assert_not_called()


def test_run_collection_reindex_clears_state(capsys: pytest.CaptureFixture[str]) -> None:
    """reindex calls remove_collection on state store before ingest_directory."""
    from archon.cli.search_cmd import _run_collection_reindex

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.ingest_directory = AsyncMock(return_value=[MagicMock(status="ok", chunks_created=5)] * 3)
    mock_pipeline.store.disconnect = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = "/tmp/rag"
    mock_cfg.search.collections = ["/tmp/sessions"]

    call_order: list[str] = []

    mock_state_store = MagicMock()

    def track_remove(name):
        call_order.append(f"remove:{name}")

    mock_state_store.remove_collection = MagicMock(side_effect=track_remove)

    orig_ingest = mock_pipeline.ingest_directory

    async def track_ingest(*args, **kwargs):
        call_order.append("ingest")
        return await orig_ingest(*args, **kwargs)

    mock_pipeline.ingest_directory = AsyncMock(side_effect=track_ingest)

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.search_cmd.path_to_collection_name", return_value="sessions"),
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.IndexingStateStore", return_value=mock_state_store),
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_reindex(_make_collection_reindex_args(collection_name="sessions"))

    assert result == 0
    # remove_collection must be called before ingest_directory
    assert call_order == ["remove:sessions", "ingest"]
    # ingest_directory must NOT receive exclude_paths (full re-ingest after state clear)
    call_kwargs = mock_pipeline.ingest_directory.call_args[1]
    assert call_kwargs.get("exclude_paths") is None


def test_run_collection_reindex_state_clear_failure_non_fatal(capsys: pytest.CaptureFixture[str]) -> None:
    """remove_collection raises → reindex proceeds; no exception propagated."""
    from archon.cli.search_cmd import _run_collection_reindex

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.ingest_directory = AsyncMock(return_value=[MagicMock(status="ok", chunks_created=5)])
    mock_pipeline.store.disconnect = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = "/tmp/rag"
    mock_cfg.search.collections = ["/tmp/sessions"]

    mock_state_store = MagicMock()
    mock_state_store.remove_collection = MagicMock(side_effect=OSError("disk full"))

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.search_cmd.path_to_collection_name", return_value="sessions"),
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.IndexingStateStore", return_value=mock_state_store),
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_reindex(_make_collection_reindex_args(collection_name="sessions"))

    # Reindex should succeed despite state clear failure
    assert result == 0
    mock_pipeline.ingest_directory.assert_called_once()


# ---------------------------------------------------------------------------
# load_config contract: require_token=False must always be passed (M2)
# ---------------------------------------------------------------------------

def test_search_status_load_config_called_with_require_token_false() -> None:
    """_run_status must call load_config(require_token=False) — token not needed for RAG CLI."""
    mock_svc = MagicMock()
    mock_svc.status.return_value = _make_service_info(running=True)

    mock_store = MagicMock()
    mock_store.connect = AsyncMock()
    mock_store.list_collections = AsyncMock(return_value=[])
    mock_store.disconnect = AsyncMock()

    with (
        patch("archon.cli.search_cmd.get_search_service", return_value=mock_svc),
        patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
        patch("archon.cli.search_cmd.load_config") as mock_load,
    ):
        mock_load.return_value.search.db_path = "/tmp/rag"
        from archon.cli.search_cmd import _run_status
        _run_status(_make_args(search_command="status"))

    mock_load.assert_called_once_with(require_token=False)


# ---------------------------------------------------------------------------
# status — progress display (FEAT-027 Task 1.6)
# ---------------------------------------------------------------------------

class TestRunStatusProgress:
    """Tests for _run_status() indexing progress display."""

    @staticmethod
    def _make_running_service() -> MagicMock:
        mock_svc = MagicMock()
        mock_svc.status.return_value = _make_service_info(running=True)
        return mock_svc

    @staticmethod
    def _make_store_mock(collections: list | None = None) -> MagicMock:
        mock_store = MagicMock()
        mock_store.connect = AsyncMock()
        mock_store.list_collections = AsyncMock(return_value=collections or [])
        mock_store.disconnect = AsyncMock()
        return mock_store

    @staticmethod
    def _make_collection_info(name: str, doc_count: int = 0, chunk_count: int = 0) -> MagicMock:
        col = MagicMock()
        col.name = name
        col.doc_count = doc_count
        col.chunk_count = chunk_count
        return col

    def test_run_status_with_progress_display(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """State file present: output shows status table."""
        from archon.search.progress import CollectionProgress, IndexingState, IndexingStateStore, IndexingStatus

        state = IndexingState(collections={
            "sessions": CollectionProgress(
                status=IndexingStatus.IN_PROGRESS,
                total_files=120,
                processed_files=87,
            ),
            "my-project": CollectionProgress(
                status=IndexingStatus.DONE,
                total_files=340,
                processed_files=340,
            ),
        })
        IndexingStateStore(tmp_path).write(state)

        mock_svc = self._make_running_service()
        mock_store = self._make_store_mock([
            self._make_collection_info("sessions", 80, 400),
            self._make_collection_info("my-project", 340, 1700),
        ])

        with (
            patch("archon.cli.search_cmd.get_search_service", return_value=mock_svc),
            patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
            patch("archon.cli.search_cmd.load_config") as mock_cfg,
        ):
            mock_cfg.return_value.search.db_path = str(tmp_path)
            mock_cfg.return_value.search.watch = False
            from archon.cli.search_cmd import _run_status
            result = _run_status(_make_args(search_command="status"))

        out = capsys.readouterr().out
        assert "sessions" in out
        assert "partial" in out
        assert "87" in out
        assert "120" in out
        assert "my-project" in out
        assert "done" in out
        assert "340" in out
        assert result == 0

    def test_run_status_without_state_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """No state file: falls back to existing collection list format."""
        mock_svc = self._make_running_service()
        mock_store = self._make_store_mock([
            self._make_collection_info("sessions", 80, 400),
        ])

        with (
            patch("archon.cli.search_cmd.get_search_service", return_value=mock_svc),
            patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
            patch("archon.cli.search_cmd.load_config") as mock_cfg,
        ):
            mock_cfg.return_value.search.db_path = str(tmp_path)
            mock_cfg.return_value.search.watch = False
            from archon.cli.search_cmd import _run_status
            result = _run_status(_make_args(search_command="status"))

        out = capsys.readouterr().out
        # Fallback: old format with collection= docs= chunks=
        assert "collection=sessions" in out
        assert "docs=80" in out
        assert "chunks=400" in out
        assert result == 0

    def test_run_status_failed_exit_code_nonzero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Returns 1 when any collection has status == failed."""
        from archon.search.progress import CollectionProgress, IndexingState, IndexingStateStore, IndexingStatus

        state = IndexingState(collections={
            "broken": CollectionProgress(
                status=IndexingStatus.FAILED,
                total_files=50,
                processed_files=12,
                error="parse error",
            ),
        })
        IndexingStateStore(tmp_path).write(state)

        mock_svc = self._make_running_service()
        mock_store = self._make_store_mock()

        with (
            patch("archon.cli.search_cmd.get_search_service", return_value=mock_svc),
            patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
            patch("archon.cli.search_cmd.load_config") as mock_cfg,
        ):
            mock_cfg.return_value.search.db_path = str(tmp_path)
            mock_cfg.return_value.search.watch = False
            from archon.cli.search_cmd import _run_status
            result = _run_status(_make_args(search_command="status"))

        assert result == 1

    def test_run_status_done_exit_code_zero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Returns 0 when all collections are done."""
        from archon.search.progress import CollectionProgress, IndexingState, IndexingStateStore, IndexingStatus

        state = IndexingState(collections={
            "docs": CollectionProgress(
                status=IndexingStatus.DONE,
                total_files=100,
                processed_files=100,
            ),
        })
        IndexingStateStore(tmp_path).write(state)

        mock_svc = self._make_running_service()
        mock_store = self._make_store_mock([
            self._make_collection_info("docs", 100, 500),
        ])

        with (
            patch("archon.cli.search_cmd.get_search_service", return_value=mock_svc),
            patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
            patch("archon.cli.search_cmd.load_config") as mock_cfg,
        ):
            mock_cfg.return_value.search.db_path = str(tmp_path)
            mock_cfg.return_value.search.watch = False
            from archon.cli.search_cmd import _run_status
            result = _run_status(_make_args(search_command="status"))

        assert result == 0

    def test_run_status_in_progress_exit_code_zero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Returns 0 when collections are in_progress (not failed)."""
        from archon.search.progress import CollectionProgress, IndexingState, IndexingStateStore, IndexingStatus

        state = IndexingState(collections={
            "sessions": CollectionProgress(
                status=IndexingStatus.IN_PROGRESS,
                total_files=120,
                processed_files=50,
            ),
        })
        IndexingStateStore(tmp_path).write(state)

        mock_svc = self._make_running_service()
        mock_store = self._make_store_mock()

        with (
            patch("archon.cli.search_cmd.get_search_service", return_value=mock_svc),
            patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
            patch("archon.cli.search_cmd.load_config") as mock_cfg,
        ):
            mock_cfg.return_value.search.db_path = str(tmp_path)
            mock_cfg.return_value.search.watch = False
            from archon.cli.search_cmd import _run_status
            result = _run_status(_make_args(search_command="status"))

        assert result == 0

    def test_run_status_mixed_failed_and_done_exit_code(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Returns 1 when mix of failed + done."""
        from archon.search.progress import CollectionProgress, IndexingState, IndexingStateStore, IndexingStatus

        state = IndexingState(collections={
            "ok-col": CollectionProgress(
                status=IndexingStatus.DONE,
                total_files=100,
                processed_files=100,
            ),
            "bad-col": CollectionProgress(
                status=IndexingStatus.FAILED,
                total_files=50,
                processed_files=10,
                error="disk full",
            ),
        })
        IndexingStateStore(tmp_path).write(state)

        mock_svc = self._make_running_service()
        mock_store = self._make_store_mock()

        with (
            patch("archon.cli.search_cmd.get_search_service", return_value=mock_svc),
            patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
            patch("archon.cli.search_cmd.load_config") as mock_cfg,
        ):
            mock_cfg.return_value.search.db_path = str(tmp_path)
            mock_cfg.return_value.search.watch = False
            from archon.cli.search_cmd import _run_status
            result = _run_status(_make_args(search_command="status"))

        assert result == 1

    def test_run_status_pending_shows_dash(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Pending collection shows dash instead of file counts."""
        from archon.search.progress import CollectionProgress, IndexingState, IndexingStateStore, IndexingStatus

        state = IndexingState(collections={
            "docs": CollectionProgress(
                status=IndexingStatus.PENDING,
            ),
        })
        IndexingStateStore(tmp_path).write(state)

        mock_svc = self._make_running_service()
        mock_store = self._make_store_mock()

        with (
            patch("archon.cli.search_cmd.get_search_service", return_value=mock_svc),
            patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
            patch("archon.cli.search_cmd.load_config") as mock_cfg,
        ):
            mock_cfg.return_value.search.db_path = str(tmp_path)
            mock_cfg.return_value.search.watch = False
            from archon.cli.search_cmd import _run_status
            _run_status(_make_args(search_command="status"))

        out = capsys.readouterr().out
        # Pending should show a dash for progress
        lines = [l for l in out.splitlines() if "docs" in l]
        assert len(lines) >= 1
        # The line with "docs" should contain the em-dash
        assert "\u2014" in lines[0]

    def test_run_status_error_message_shown(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Failed collection shows error message."""
        from archon.search.progress import CollectionProgress, IndexingState, IndexingStateStore, IndexingStatus

        state = IndexingState(collections={
            "old-notes": CollectionProgress(
                status=IndexingStatus.FAILED,
                total_files=50,
                processed_files=12,
                error="parse error",
            ),
        })
        IndexingStateStore(tmp_path).write(state)

        mock_svc = self._make_running_service()
        mock_store = self._make_store_mock()

        with (
            patch("archon.cli.search_cmd.get_search_service", return_value=mock_svc),
            patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
            patch("archon.cli.search_cmd.load_config") as mock_cfg,
        ):
            mock_cfg.return_value.search.db_path = str(tmp_path)
            mock_cfg.return_value.search.watch = False
            from archon.cli.search_cmd import _run_status
            _run_status(_make_args(search_command="status"))

        out = capsys.readouterr().out
        assert "parse error" in out

    def test_run_status_merge_state_and_collections(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """State-only collections are included; LanceDB-only collections shown with info only."""
        from archon.search.progress import CollectionProgress, IndexingState, IndexingStateStore, IndexingStatus

        # State file has "new-col" (being indexed, not yet in LanceDB)
        state = IndexingState(collections={
            "new-col": CollectionProgress(
                status=IndexingStatus.IN_PROGRESS,
                total_files=200,
                processed_files=50,
            ),
        })
        IndexingStateStore(tmp_path).write(state)

        # LanceDB has "existing-col" (not in state file)
        mock_svc = self._make_running_service()
        mock_store = self._make_store_mock([
            self._make_collection_info("existing-col", 100, 500),
        ])

        with (
            patch("archon.cli.search_cmd.get_search_service", return_value=mock_svc),
            patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
            patch("archon.cli.search_cmd.load_config") as mock_cfg,
        ):
            mock_cfg.return_value.search.db_path = str(tmp_path)
            mock_cfg.return_value.search.watch = False
            from archon.cli.search_cmd import _run_status
            result = _run_status(_make_args(search_command="status"))

        out = capsys.readouterr().out
        # Both collections should appear
        assert "new-col" in out
        assert "existing-col" in out
        assert result == 0

    # -----------------------------------------------------------------------
    # FEAT-027-P2 Task 2.3: partial status for in-progress collections
    # -----------------------------------------------------------------------

    def test_cli_status_shows_partial(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """IN_PROGRESS with processed_files > 0 shows 'partial' status and 'N / M files'."""
        from archon.search.progress import CollectionProgress, IndexingState, IndexingStateStore, IndexingStatus

        state = IndexingState(collections={
            "my-docs": CollectionProgress(
                status=IndexingStatus.IN_PROGRESS,
                total_files=100,
                processed_files=50,
            ),
        })
        IndexingStateStore(tmp_path).write(state)

        mock_svc = self._make_running_service()
        mock_store = self._make_store_mock()

        with (
            patch("archon.cli.search_cmd.get_search_service", return_value=mock_svc),
            patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
            patch("archon.cli.search_cmd.load_config") as mock_cfg,
        ):
            mock_cfg.return_value.search.db_path = str(tmp_path)
            mock_cfg.return_value.search.watch = False
            from archon.cli.search_cmd import _run_status
            _run_status(_make_args(search_command="status"))

        out = capsys.readouterr().out
        line = [l for l in out.splitlines() if "my-docs" in l][0]
        assert "partial" in line
        assert "50 / 100" in line

    def test_cli_status_in_progress_zero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """IN_PROGRESS with processed_files == 0 shows 'in_progress' and '0 / M files'."""
        from archon.search.progress import CollectionProgress, IndexingState, IndexingStateStore, IndexingStatus

        state = IndexingState(collections={
            "fresh-col": CollectionProgress(
                status=IndexingStatus.IN_PROGRESS,
                total_files=200,
                processed_files=0,
            ),
        })
        IndexingStateStore(tmp_path).write(state)

        mock_svc = self._make_running_service()
        mock_store = self._make_store_mock()

        with (
            patch("archon.cli.search_cmd.get_search_service", return_value=mock_svc),
            patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
            patch("archon.cli.search_cmd.load_config") as mock_cfg,
        ):
            mock_cfg.return_value.search.db_path = str(tmp_path)
            mock_cfg.return_value.search.watch = False
            from archon.cli.search_cmd import _run_status
            _run_status(_make_args(search_command="status"))

        out = capsys.readouterr().out
        line = [l for l in out.splitlines() if "fresh-col" in l][0]
        assert "in_progress" in line
        assert "0 /" in line

    def test_cli_status_pending_shows_dash(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """PENDING collection shows em-dash for progress (regression guard)."""
        from archon.search.progress import CollectionProgress, IndexingState, IndexingStateStore, IndexingStatus

        state = IndexingState(collections={
            "pending-col": CollectionProgress(
                status=IndexingStatus.PENDING,
            ),
        })
        IndexingStateStore(tmp_path).write(state)

        mock_svc = self._make_running_service()
        mock_store = self._make_store_mock()

        with (
            patch("archon.cli.search_cmd.get_search_service", return_value=mock_svc),
            patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
            patch("archon.cli.search_cmd.load_config") as mock_cfg,
        ):
            mock_cfg.return_value.search.db_path = str(tmp_path)
            mock_cfg.return_value.search.watch = False
            from archon.cli.search_cmd import _run_status
            _run_status(_make_args(search_command="status"))

        out = capsys.readouterr().out
        line = [l for l in out.splitlines() if "pending-col" in l][0]
        assert "\u2014" in line

    def test_cli_status_done_shows_done(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """DONE collection shows 'done' status (regression guard)."""
        from archon.search.progress import CollectionProgress, IndexingState, IndexingStateStore, IndexingStatus

        state = IndexingState(collections={
            "done-col": CollectionProgress(
                status=IndexingStatus.DONE,
                total_files=80,
                processed_files=80,
            ),
        })
        IndexingStateStore(tmp_path).write(state)

        mock_svc = self._make_running_service()
        mock_store = self._make_store_mock([
            self._make_collection_info("done-col", 80, 400),
        ])

        with (
            patch("archon.cli.search_cmd.get_search_service", return_value=mock_svc),
            patch("archon.cli.search_cmd.SearchStore", return_value=mock_store),
            patch("archon.cli.search_cmd.load_config") as mock_cfg,
        ):
            mock_cfg.return_value.search.db_path = str(tmp_path)
            mock_cfg.return_value.search.watch = False
            from archon.cli.search_cmd import _run_status
            _run_status(_make_args(search_command="status"))

        out = capsys.readouterr().out
        line = [l for l in out.splitlines() if "done-col" in l][0]
        assert "done" in line


# ---------------------------------------------------------------------------
# Task 4.7 — config params wired through SearchCollectionSync constructors
# ---------------------------------------------------------------------------


def test_cli_sync_passes_config_params() -> None:
    """_run_sync passes embedding_model, chunk_size, auto_reindex_on_chunk_size_change to SearchCollectionSync."""
    from archon.cli.search_cmd import _run_sync
    from archon.search.sync import SyncResult

    mock_sync_result = SyncResult(added=[], removed=[], unchanged=[], errors=[], skipped=[])

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.store.disconnect = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = "/tmp/rag"
    mock_cfg.search.collections = ["/some/path"]
    mock_cfg.search.pinned_collections = []
    mock_cfg.search.embedding_model = "my-embed-model"
    mock_cfg.search.chunk_size = 256
    mock_cfg.search.auto_reindex_on_chunk_size_change = True

    with (
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.search_cmd.SearchCollectionSync") as MockSync,
        patch("archon.cli.search_cmd.IndexingStateStore"),
    ):
        mock_svc.return_value.status.return_value.running = False
        MockSync.return_value.sync = AsyncMock(return_value=mock_sync_result)
        _run_sync(_make_args(search_command="sync"))

    call_kwargs = MockSync.call_args[1]
    assert call_kwargs["embedding_model"] == "my-embed-model"
    assert call_kwargs["chunk_size"] == 256
    assert call_kwargs["auto_reindex_on_chunk_size_change"] is True


# ---------------------------------------------------------------------------
# Task 4.8 — CLI sync output for `updated` collections
# ---------------------------------------------------------------------------


def test_run_sync_output_includes_updated(capsys: pytest.CaptureFixture[str]) -> None:
    """_run_sync prints updated collections with ↻ indicator and includes updated count in summary."""
    from archon.cli.search_cmd import _run_sync
    from archon.search.sync import SyncResult

    mock_sync_result = SyncResult(
        added=[], removed=[], unchanged=[], errors=[], skipped=[], updated=["sessions"]
    )
    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.store.disconnect = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.search.db_path = "/tmp/rag"
    mock_cfg.search.collections = []
    mock_cfg.search.pinned_collections = []
    mock_cfg.search.embedding_model = "embed"
    mock_cfg.search.chunk_size = 512
    mock_cfg.search.auto_reindex_on_chunk_size_change = False

    with (
        patch("archon.cli.search_cmd.get_search_service") as mock_svc,
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.search_cmd.SearchCollectionSync") as MockSync,
        patch("archon.cli.search_cmd.IndexingStateStore"),
    ):
        mock_svc.return_value.status.return_value.running = False
        MockSync.return_value.sync = AsyncMock(return_value=mock_sync_result)
        result = _run_sync(_make_args(search_command="sync"))

    out = capsys.readouterr().out
    assert result == 0
    # Summary line must include updated count
    assert "1 updated" in out
    # Per-collection line with ↻ indicator (including leading spaces matching the format "  ↻ {name}")
    assert "  ↻ sessions" in out


# ---------------------------------------------------------------------------
# FEAT-027-P7 Task 7.2 — ETA display in _print_progress_table
# ---------------------------------------------------------------------------


class TestEtaDisplay:
    """Tests for ETA suffix in _print_progress_table (FEAT-027-P7 Task 7.2)."""

    @staticmethod
    def _make_in_progress_state(processed: int = 50, total: int = 100) -> "IndexingState":
        from archon.search.progress import CollectionProgress, IndexingState, IndexingStatus
        return IndexingState(collections={
            "my-docs": CollectionProgress(
                status=IndexingStatus.IN_PROGRESS,
                total_files=total,
                processed_files=processed,
                started_at="2026-04-04T09:00:00+00:00",
            ),
        })

    @staticmethod
    def _call_print_progress_table(state, capsys):
        from archon.cli.search_cmd import _print_progress_table
        _print_progress_table(state, [])
        return capsys.readouterr().out

    def test_status_shows_eta_for_in_progress(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Mock compute_eta_seconds returns 300 → output contains '~5 min remaining'."""
        state = self._make_in_progress_state()
        with patch("archon.cli.search_cmd.compute_eta_seconds", return_value=300):
            out = self._call_print_progress_table(state, capsys)
        assert "~5 min remaining" in out

    def test_status_shows_ceil_rounding_for_eta(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Mock returns 150 (2.5 min) → math.ceil gives 3, not banker's round(2.5)=2."""
        state = self._make_in_progress_state()
        with patch("archon.cli.search_cmd.compute_eta_seconds", return_value=150):
            out = self._call_print_progress_table(state, capsys)
        assert "~3 min remaining" in out

    @pytest.mark.parametrize("eta,expected", [
        (59, "< 1 min remaining"),
        (60, "~1 min remaining"),
    ])
    def test_status_boundary_eta(
        self, eta: int, expected: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Boundary: 59s → '< 1 min remaining'; 60s → '~1 min remaining'."""
        state = self._make_in_progress_state()
        with patch("archon.cli.search_cmd.compute_eta_seconds", return_value=eta):
            out = self._call_print_progress_table(state, capsys)
        assert expected in out

    def test_status_suppresses_eta_when_compute_returns_none(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """When compute_eta_seconds returns None, no ETA suffix in output."""
        state = self._make_in_progress_state()
        with patch("archon.cli.search_cmd.compute_eta_seconds", return_value=None):
            out = self._call_print_progress_table(state, capsys)
        assert "remaining" not in out

    @pytest.mark.parametrize("status_str", ["done", "failed", "pending"])
    def test_status_suppresses_eta_for_non_in_progress(
        self, status_str: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Non-IN_PROGRESS collections never show ETA (block gated by status == IN_PROGRESS)."""
        from archon.search.progress import CollectionProgress, IndexingState, IndexingStatus
        state = IndexingState(collections={
            "col": CollectionProgress(
                status=IndexingStatus(status_str),
                total_files=100,
                processed_files=80 if status_str != "pending" else 0,
            ),
        })
        # No mock needed — ETA block is gated by IN_PROGRESS check
        from archon.cli.search_cmd import _print_progress_table
        _print_progress_table(state, [])
        out = capsys.readouterr().out
        assert "remaining" not in out

    def test_status_no_eta_when_processed_zero(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Mock compute_eta_seconds returns None for zero processed files → no ETA in output."""
        state = self._make_in_progress_state(processed=0)
        with patch("archon.cli.search_cmd.compute_eta_seconds", return_value=None):
            out = self._call_print_progress_table(state, capsys)
        assert "remaining" not in out

    def test_status_shows_less_than_1_min_for_eta_zero(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """eta=0 (rounding down to zero seconds) shows '< 1 min remaining'."""
        state = self._make_in_progress_state()
        with patch("archon.cli.search_cmd.compute_eta_seconds", return_value=0):
            out = self._call_print_progress_table(state, capsys)
        assert "< 1 min remaining" in out

    def test_status_integration_eta_with_real_compute(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Integration: _print_progress_table with real compute_eta_seconds — valid started_at produces ETA."""
        from datetime import datetime, timedelta, timezone
        from archon.search.progress import CollectionProgress, IndexingState, IndexingStatus

        now = datetime.now(timezone.utc)
        started = (now - timedelta(seconds=100)).isoformat()
        state = IndexingState(collections={
            "my-docs": CollectionProgress(
                status=IndexingStatus.IN_PROGRESS,
                total_files=100,
                processed_files=20,
                started_at=started,  # 20 files in 100s → fps=0.2, 80 remaining → 400s
            ),
        })
        from archon.cli.search_cmd import _print_progress_table
        _print_progress_table(state, [])
        out = capsys.readouterr().out
        assert "remaining" in out

    @patch("archon.cli.search_cmd.compute_eta_seconds")
    def test_status_shows_eta_alongside_error_suffix(
        self, mock_eta: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """ETA suffix coexists with error suffix when both are present."""
        from archon.search.progress import CollectionProgress, IndexingState, IndexingStatus
        mock_eta.return_value = 300
        state = IndexingState(collections={
            "my_collection": CollectionProgress(
                status=IndexingStatus.IN_PROGRESS,
                processed_files=50,
                total_files=100,
                error="timeout reading file X",
            ),
        })
        from archon.cli.search_cmd import _print_progress_table
        _print_progress_table(state, [])
        out = capsys.readouterr().out
        assert "timeout reading file X" in out
        assert "~5 min remaining" in out


# ---------------------------------------------------------------------------
# FEAT-027-P8 Task 8.6 — watch indicator in _print_progress_table
# ---------------------------------------------------------------------------


class TestWatchIndicator:
    """Tests for (watch) suffix in _print_progress_table (FEAT-027-P8 Task 8.6)."""

    @staticmethod
    def _make_state(status_name: str) -> "IndexingState":
        from archon.search.progress import CollectionProgress, IndexingState, IndexingStatus
        status = IndexingStatus[status_name]
        return IndexingState(collections={
            "my-docs": CollectionProgress(
                status=status,
                total_files=10,
                processed_files=10 if status == IndexingStatus.DONE else 5,
            ),
        })

    def test_status_shows_watch_indicator_for_done(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """DONE collection with watching=True shows (watch) in output."""
        state = self._make_state("DONE")
        from archon.cli.search_cmd import _print_progress_table
        _print_progress_table(state, [], watching=True)
        out = capsys.readouterr().out
        assert "(watch)" in out

    def test_status_no_watch_indicator_when_not_watching(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """DONE collection with watching=False does NOT show (watch)."""
        state = self._make_state("DONE")
        from archon.cli.search_cmd import _print_progress_table
        _print_progress_table(state, [], watching=False)
        out = capsys.readouterr().out
        assert "(watch)" not in out

    @pytest.mark.parametrize("status_name,watching,expect_watch", [
        ("IN_PROGRESS", True, True),
        ("FAILED", True, False),
        ("PENDING", True, False),
    ])
    def test_status_watch_indicator_for_in_progress_not_failed(
        self,
        status_name: str,
        watching: bool,
        expect_watch: bool,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """IN_PROGRESS+watching → (watch); FAILED/PENDING+watching → no (watch)."""
        state = self._make_state(status_name)
        with patch("archon.cli.search_cmd.compute_eta_seconds", return_value=None):
            from archon.cli.search_cmd import _print_progress_table
            _print_progress_table(state, [], watching=watching)
        out = capsys.readouterr().out
        if expect_watch:
            assert "(watch)" in out
        else:
            assert "(watch)" not in out
