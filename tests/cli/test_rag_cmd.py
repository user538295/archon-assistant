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


# ---------------------------------------------------------------------------
# Task 4.1 — archon rag sync
# ---------------------------------------------------------------------------


def test_sync_cli_command_prints_result(capsys: pytest.CaptureFixture[str]) -> None:
    """archon rag sync prints added/removed/unchanged/errors counts."""
    from archon.cli.rag_cmd import _run_sync
    from archon.rag.sync import SyncResult

    mock_sync_result = SyncResult(
        added=["docs"], removed=["old_col"], unchanged=["sessions"], errors=[], skipped=[]
    )
    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.store.disconnect = AsyncMock()
    mock_cfg = MagicMock()
    mock_cfg.rag.collections = ["~/.archon/history/sessions"]

    with (
        patch("archon.cli.rag_cmd.get_rag_service") as mock_svc,
        patch("archon.cli.rag_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.rag_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.rag_cmd.RagCollectionSync") as MockSync,
    ):
        mock_svc.return_value.status.return_value.running = False
        MockSync.return_value.sync = AsyncMock(return_value=mock_sync_result)
        result = _run_sync(_make_args(rag_command="sync"))

    out = capsys.readouterr().out
    assert "1 added" in out
    assert "1 removed" in out
    assert "1 unchanged" in out
    assert "0 errors" in out
    assert result == 0


def test_sync_cli_returns_1_on_errors(capsys: pytest.CaptureFixture[str]) -> None:
    """archon rag sync returns exit code 1 when there are sync errors."""
    from archon.cli.rag_cmd import _run_sync
    from archon.rag.sync import SyncResult

    mock_sync_result = SyncResult(
        added=[], removed=[], unchanged=[], errors=["path does not exist: /bad"], skipped=[]
    )
    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.store.disconnect = AsyncMock()
    mock_cfg = MagicMock()
    mock_cfg.rag.collections = []

    with (
        patch("archon.cli.rag_cmd.get_rag_service") as mock_svc,
        patch("archon.cli.rag_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.rag_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.rag_cmd.RagCollectionSync") as MockSync,
    ):
        mock_svc.return_value.status.return_value.running = False
        MockSync.return_value.sync = AsyncMock(return_value=mock_sync_result)
        result = _run_sync(_make_args(rag_command="sync"))

    assert result == 1


def test_sync_cli_warns_if_service_running(capsys: pytest.CaptureFixture[str]) -> None:
    """archon rag sync prints a warning (but proceeds) if the RAG service is running."""
    from archon.cli.rag_cmd import _run_sync
    from archon.rag.sync import SyncResult

    mock_sync_result = SyncResult(
        added=[], removed=[], unchanged=[], errors=[], skipped=[]
    )
    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.store.disconnect = AsyncMock()
    mock_cfg = MagicMock()
    mock_cfg.rag.collections = []

    with (
        patch("archon.cli.rag_cmd.get_rag_service") as mock_svc,
        patch("archon.cli.rag_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.rag_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.rag_cmd.RagCollectionSync") as MockSync,
    ):
        mock_svc.return_value.status.return_value.running = True
        MockSync.return_value.sync = AsyncMock(return_value=mock_sync_result)
        result = _run_sync(_make_args(rag_command="sync"))

    out = capsys.readouterr().out
    assert "warning" in out.lower() or "running" in out.lower()
    assert result == 0  # proceeds despite warning


# ---------------------------------------------------------------------------
# Task 4.2 — archon rag collection list
# ---------------------------------------------------------------------------


def _make_collection_list_args(**kwargs) -> argparse.Namespace:
    defaults = dict(
        rag_command="collection",
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
    from archon.cli.rag_cmd import _run_collection_list

    mock_store = MagicMock()
    mock_store.connect = AsyncMock()
    mock_store.disconnect = AsyncMock()
    mock_store.list_collections = AsyncMock(
        return_value=[_make_collection_info("sessions", doc_count=3, chunk_count=12)]
    )

    manifest_data = '{"sessions": "/home/user/.archon/history/sessions"}'

    mock_cfg = MagicMock()
    mock_cfg.rag.db_path = "/tmp/rag"
    mock_cfg.rag.collections = ["/home/user/.archon/history/sessions"]

    with (
        patch("archon.cli.rag_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.rag_cmd.RagStore", return_value=mock_store),
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
    from archon.cli.rag_cmd import _run_collection_list

    mock_store = MagicMock()
    mock_store.connect = AsyncMock()
    mock_store.disconnect = AsyncMock()
    mock_store.list_collections = AsyncMock(
        return_value=[_make_collection_info("old_col", doc_count=1, chunk_count=5)]
    )

    # old_col is in manifest but config has no collections
    manifest_data = '{"old_col": "/tmp/old_col"}'

    mock_cfg = MagicMock()
    mock_cfg.rag.db_path = "/tmp/rag"
    mock_cfg.rag.collections = []

    with (
        patch("archon.cli.rag_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.rag_cmd.RagStore", return_value=mock_store),
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
    from archon.cli.rag_cmd import _run_collection_list

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
    mock_cfg.rag.db_path = "/tmp/rag"
    mock_cfg.rag.collections = []

    with (
        patch("archon.cli.rag_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.rag_cmd.RagStore", return_value=mock_store),
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
    from archon.cli.rag_cmd import _run_collection_list

    mock_store = MagicMock()
    mock_store.connect = AsyncMock()
    mock_store.disconnect = AsyncMock()
    mock_store.list_collections = AsyncMock(return_value=[])  # nothing in LanceDB

    manifest_data = "{}"

    mock_cfg = MagicMock()
    mock_cfg.rag.db_path = "/tmp/rag"
    mock_cfg.rag.collections = ["/home/user/docs"]

    with (
        patch("archon.cli.rag_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.rag_cmd.RagStore", return_value=mock_store),
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
    from archon.cli.rag_cmd import _run_collection_list

    mock_store = MagicMock()
    mock_store.connect = AsyncMock()
    mock_store.disconnect = AsyncMock()
    mock_store.list_collections = AsyncMock(return_value=[])

    mock_cfg = MagicMock()
    mock_cfg.rag.db_path = "/tmp/rag"
    mock_cfg.rag.collections = []

    with (
        patch("archon.cli.rag_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.rag_cmd.RagStore", return_value=mock_store),
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
        rag_command="collection",
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
    from archon.cli.rag_cmd import _run_collection_add

    path = str(tmp_path / "docs")

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.ingest_directory = AsyncMock(return_value=[])
    mock_pipeline.store.disconnect = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.rag.db_path = str(tmp_path / "rag")
    mock_cfg.rag.collections = []

    config_file = tmp_path / "config.toml"
    config_file.write_text('[rag]\ncollections = []\n')

    with (
        patch("archon.cli.rag_cmd.get_rag_service") as mock_svc,
        patch("archon.cli.rag_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.rag_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.rag_cmd._config_collections_append") as mock_append,
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_add(_make_collection_add_args(path=path))

    mock_append.assert_called_once()
    mock_pipeline.ingest_directory.assert_awaited_once()
    out = capsys.readouterr().out
    assert "Collection added and indexed" in out
    assert result == 0


def test_collection_add_already_registered_exits_0(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """If path is already in config (after normalisation), print message and exit 0."""
    from archon.cli.rag_cmd import _run_collection_add

    path = str(tmp_path / "docs")

    mock_cfg = MagicMock()
    mock_cfg.rag.db_path = str(tmp_path / "rag")
    # Same path already in collections
    mock_cfg.rag.collections = [path]

    with (
        patch("archon.cli.rag_cmd.get_rag_service") as mock_svc,
        patch("archon.cli.rag_cmd.load_config", return_value=mock_cfg),
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
    from archon.cli.rag_cmd import _run_collection_add
    from pathlib import Path

    home = Path.home()
    # Use a subdirectory under home for tilde expansion
    rel = "archon_test_docs_4321"
    tilde_path = f"~/{rel}"
    abs_path = str(home / rel)

    mock_cfg = MagicMock()
    mock_cfg.rag.db_path = str(tmp_path / "rag")
    # Store the absolute path in config — should still be detected as duplicate
    mock_cfg.rag.collections = [abs_path]

    with (
        patch("archon.cli.rag_cmd.get_rag_service") as mock_svc,
        patch("archon.cli.rag_cmd.load_config", return_value=mock_cfg),
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
    from archon.cli.rag_cmd import _run_collection_add

    path = str(tmp_path / "docs")

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.ingest_directory = AsyncMock(return_value=[])
    mock_pipeline.store.disconnect = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.rag.db_path = str(tmp_path / "rag")
    mock_cfg.rag.collections = []

    with (
        patch("archon.cli.rag_cmd.get_rag_service") as mock_svc,
        patch("archon.cli.rag_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.rag_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.rag_cmd._config_collections_append"),
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
    from archon.cli.rag_cmd import _run_collection_add
    from archon.rag.sync import path_to_collection_name

    path = str(tmp_path / "my_project")
    expected_name = path_to_collection_name(path)

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.ingest_directory = AsyncMock(return_value=[])
    mock_pipeline.store.disconnect = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.rag.db_path = str(tmp_path / "rag")
    mock_cfg.rag.collections = []

    with (
        patch("archon.cli.rag_cmd.get_rag_service") as mock_svc,
        patch("archon.cli.rag_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.rag_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.rag_cmd._config_collections_append"),
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
    from archon.cli.rag_cmd import _run_collection_add

    path = str(tmp_path / "docs")

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.ingest_directory = AsyncMock(side_effect=RuntimeError("disk full"))
    mock_pipeline.store.disconnect = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.rag.db_path = str(tmp_path / "rag")
    mock_cfg.rag.collections = []

    mock_append = MagicMock()

    with (
        patch("archon.cli.rag_cmd.get_rag_service") as mock_svc,
        patch("archon.cli.rag_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.rag_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.rag_cmd._config_collections_append", mock_append),
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_add(_make_collection_add_args(path=path))

    # Config append happens before ingest attempt
    mock_append.assert_called_once()
    out = capsys.readouterr().out
    assert "disk full" in out or "error" in out.lower()
    assert result == 1


def test_config_collections_append_writes_tomlkit(tmp_path) -> None:
    """_config_collections_append appends path to [rag] collections array."""
    import tomlkit
    from archon.cli.rag_cmd import _config_collections_append

    config_file = tmp_path / "config.toml"
    config_file.write_text('[rag]\ncollections = ["/existing/path"]\n')

    _config_collections_append(config_file, "/new/path")

    doc = tomlkit.parse(config_file.read_text())
    assert "/new/path" in doc["rag"]["collections"]
    assert "/existing/path" in doc["rag"]["collections"]


def test_config_collections_append_preserves_existing_comments(tmp_path) -> None:
    """_config_collections_append preserves TOML comments and formatting."""
    import tomlkit
    from archon.cli.rag_cmd import _config_collections_append

    config_file = tmp_path / "config.toml"
    config_file.write_text(
        '# Archon config\n[rag]\n# list of paths\ncollections = ["/a"]\n'
    )

    _config_collections_append(config_file, "/b")

    content = config_file.read_text()
    assert "# Archon config" in content
    assert "# list of paths" in content
    doc = tomlkit.parse(content)
    assert "/b" in doc["rag"]["collections"]


def test_collection_add_integration(tmp_path) -> None:
    """Integration test: full _run_collection_add with real tomlkit config write."""
    from archon.cli.rag_cmd import _run_collection_add
    from archon.rag.sync import path_to_collection_name
    import tomlkit

    path = str(tmp_path / "some_docs")

    config_file = tmp_path / "config.toml"
    config_file.write_text('[rag]\ncollections = []\n')

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.ingest_directory = AsyncMock(return_value=[])
    mock_pipeline.store.disconnect = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.rag.db_path = str(tmp_path / "rag")
    mock_cfg.rag.collections = []

    with (
        patch("archon.cli.rag_cmd.get_rag_service") as mock_svc,
        patch("archon.cli.rag_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.rag_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.rag_cmd._CONFIG_PATH", config_file),
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_add(_make_collection_add_args(path=path))

    assert result == 0
    doc = tomlkit.parse(config_file.read_text())
    assert path in doc["rag"]["collections"]
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
    from archon.cli.rag_cmd import _run_collection_add
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
    mock_cfg.rag.db_path = str(rag_dir)
    mock_cfg.rag.collections = []

    with (
        patch("archon.cli.rag_cmd.get_rag_service") as mock_svc,
        patch("archon.cli.rag_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.rag_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.rag_cmd._config_collections_append"),
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
    from archon.cli.rag_cmd import _run_collection_add
    from archon.rag.sync import path_to_collection_name
    from pathlib import Path

    path = str(tmp_path / "docs")
    resolved_path = Path(path).expanduser().resolve()
    expected_col_name = path_to_collection_name(path)

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.ingest_directory = AsyncMock(return_value=[])
    mock_pipeline.store.disconnect = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.rag.db_path = str(tmp_path / "rag")
    mock_cfg.rag.collections = []

    with (
        patch("archon.cli.rag_cmd.get_rag_service") as mock_svc,
        patch("archon.cli.rag_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.rag_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.rag_cmd._config_collections_append"),
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_add(_make_collection_add_args(path=path))

    assert result == 0
    call_args = mock_pipeline.ingest_directory.call_args
    assert call_args[0][0] == resolved_path
    assert call_args[0][1] == expected_col_name


# ---------------------------------------------------------------------------
# C1-T-3: _config_collections_append creates missing [rag] section
# ---------------------------------------------------------------------------


def test_config_collections_append_creates_missing_rag_section(tmp_path) -> None:
    """_config_collections_append creates [rag] section if not present."""
    import tomlkit
    from archon.cli.rag_cmd import _config_collections_append

    config_file = tmp_path / "config.toml"
    config_file.write_text('[logging]\nlevel = "info"\n')

    _config_collections_append(config_file, "/new/path")

    doc = tomlkit.parse(config_file.read_text())
    assert "/new/path" in doc["rag"]["collections"]


# ---------------------------------------------------------------------------
# C1-T-4: non-existent directory — ingest fails, path stays in config
# ---------------------------------------------------------------------------


def test_collection_add_nonexistent_directory_ingest_fails(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """Ingest failure for non-existent dir: path stays in config, exit 1."""
    from archon.cli.rag_cmd import _run_collection_add

    path = str(tmp_path / "does_not_exist")

    mock_pipeline = MagicMock()
    mock_pipeline.store.connect = AsyncMock()
    mock_pipeline.ingest_directory = AsyncMock(
        side_effect=FileNotFoundError("no such directory")
    )
    mock_pipeline.store.disconnect = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.rag.db_path = str(tmp_path / "rag")
    mock_cfg.rag.collections = []

    mock_append = MagicMock()

    with (
        patch("archon.cli.rag_cmd.get_rag_service") as mock_svc,
        patch("archon.cli.rag_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.rag_cmd.create_pipeline", return_value=mock_pipeline),
        patch("archon.cli.rag_cmd._config_collections_append", mock_append),
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


def _make_collection_remove_args(path: str = "/tmp/my_docs", force: bool = False, **kwargs) -> argparse.Namespace:
    defaults = dict(
        rag_command="collection",
        collection_command="remove",
        path=path,
        force=force,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_collection_remove_removes_from_config_and_drops(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """Happy path: path in config, service stopped, config remove called, store.drop_collection called, prints 'Collection removed', returns 0."""
    from archon.cli.rag_cmd import _run_collection_remove

    path = str(tmp_path / "docs")

    mock_store = MagicMock()
    mock_store.connect = AsyncMock()
    mock_store.disconnect = AsyncMock()
    mock_store.drop_collection = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.rag.db_path = str(tmp_path / "rag")
    mock_cfg.rag.collections = [path]

    with (
        patch("archon.cli.rag_cmd.get_rag_service") as mock_svc,
        patch("archon.cli.rag_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.rag_cmd.RagStore", return_value=mock_store),
        patch("archon.cli.rag_cmd._config_collections_remove") as mock_remove,
        patch("archon.cli.rag_cmd.manifest_lookup_by_path", return_value=None),
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_remove(_make_collection_remove_args(path=path))

    mock_remove.assert_called_once()
    mock_store.drop_collection.assert_awaited_once()
    # C1-T-2: verify col_name passed to drop_collection
    from archon.rag.sync import path_to_collection_name
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
    from archon.cli.rag_cmd import _run_collection_remove

    path = str(tmp_path / "docs")

    mock_cfg = MagicMock()
    mock_cfg.rag.db_path = str(tmp_path / "rag")
    mock_cfg.rag.collections = []  # path not in config

    with (
        patch("archon.cli.rag_cmd.get_rag_service") as mock_svc,
        patch("archon.cli.rag_cmd.load_config", return_value=mock_cfg),
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
    from archon.cli.rag_cmd import _run_collection_remove

    path = str(tmp_path / "docs")

    mock_cfg = MagicMock()
    mock_cfg.rag.db_path = str(tmp_path / "rag")
    mock_cfg.rag.collections = [path]

    mock_config_remove = MagicMock()
    mock_store = MagicMock()
    mock_store.drop_collection = AsyncMock()

    with (
        patch("archon.cli.rag_cmd.get_rag_service") as mock_svc,
        patch("archon.cli.rag_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.rag_cmd._config_collections_remove", mock_config_remove),
        patch("archon.cli.rag_cmd.RagStore", return_value=mock_store),
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
    from archon.cli.rag_cmd import _run_collection_remove

    path = str(tmp_path / "docs")

    mock_store = MagicMock()
    mock_store.connect = AsyncMock()
    mock_store.disconnect = AsyncMock()
    mock_store.drop_collection = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.rag.db_path = str(tmp_path / "rag")
    mock_cfg.rag.collections = [path]

    with (
        patch("archon.cli.rag_cmd.get_rag_service") as mock_svc,
        patch("archon.cli.rag_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.rag_cmd.RagStore", return_value=mock_store),
        patch("archon.cli.rag_cmd._config_collections_remove"),
        patch("archon.cli.rag_cmd.manifest_lookup_by_path", return_value=None),
    ):
        mock_svc.return_value.status.return_value.running = True
        result = _run_collection_remove(_make_collection_remove_args(path=path, force=True))

    out = capsys.readouterr().out
    assert "warning" in out.lower() or "Warning" in out
    assert result == 0


def test_config_collections_remove_normalizes_tilde(tmp_path) -> None:
    """Stores ~/docs in config, remove called with expanded path — entry is removed."""
    import tomlkit
    from archon.cli.rag_cmd import _config_collections_remove
    from pathlib import Path

    tilde_path = "~/archon_test_remove_docs_8765"
    abs_path = str(Path(tilde_path).expanduser())

    config_file = tmp_path / "config.toml"
    config_file.write_text(f'[rag]\ncollections = ["{tilde_path}"]\n')

    _config_collections_remove(config_file, abs_path)

    doc = tomlkit.parse(config_file.read_text())
    assert tilde_path not in doc["rag"]["collections"]
    assert abs_path not in doc["rag"]["collections"]


def test_collection_remove_integration(tmp_path) -> None:
    """Integration: real tomlkit write — path removed from config file after remove call."""
    import tomlkit
    from archon.cli.rag_cmd import _run_collection_remove
    from pathlib import Path

    path = str(tmp_path / "some_docs")

    config_file = tmp_path / "config.toml"
    config_file.write_text(f'[rag]\ncollections = ["{path}"]\n')

    mock_store = MagicMock()
    mock_store.connect = AsyncMock()
    mock_store.disconnect = AsyncMock()
    mock_store.drop_collection = AsyncMock()

    mock_cfg = MagicMock()
    mock_cfg.rag.db_path = str(tmp_path / "rag")
    mock_cfg.rag.collections = [path]

    with (
        patch("archon.cli.rag_cmd.get_rag_service") as mock_svc,
        patch("archon.cli.rag_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.rag_cmd.RagStore", return_value=mock_store),
        patch("archon.cli.rag_cmd._CONFIG_PATH", config_file),
        patch("archon.cli.rag_cmd.manifest_lookup_by_path", return_value=None),
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_remove(_make_collection_remove_args(path=path))

    assert result == 0
    doc = tomlkit.parse(config_file.read_text())
    assert path not in doc["rag"]["collections"]


# ---------------------------------------------------------------------------
# C1-T-1: non-KeyError drop exception leaves config intact
# ---------------------------------------------------------------------------


def test_collection_remove_drop_failure_leaves_config_intact(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """If drop_collection raises a non-KeyError exception, config is NOT touched and exit code is 1."""
    from archon.cli.rag_cmd import _run_collection_remove

    path = str(tmp_path / "docs")

    mock_store = MagicMock()
    mock_store.connect = AsyncMock()
    mock_store.disconnect = AsyncMock()
    mock_store.drop_collection = AsyncMock(side_effect=RuntimeError("LanceDB error"))

    mock_cfg = MagicMock()
    mock_cfg.rag.db_path = str(tmp_path / "rag")
    mock_cfg.rag.collections = [path]

    with (
        patch("archon.cli.rag_cmd.get_rag_service") as mock_svc,
        patch("archon.cli.rag_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.rag_cmd.RagStore", return_value=mock_store),
        patch("archon.cli.rag_cmd._config_collections_remove") as mock_remove,
        patch("archon.cli.rag_cmd.manifest_lookup_by_path", return_value=None),
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
    from archon.cli.rag_cmd import _run_collection_remove

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
    mock_cfg.rag.db_path = str(rag_dir)
    mock_cfg.rag.collections = [path]

    with (
        patch("archon.cli.rag_cmd.get_rag_service") as mock_svc,
        patch("archon.cli.rag_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.rag_cmd.RagStore", return_value=mock_store),
        patch("archon.cli.rag_cmd._config_collections_remove"),
    ):
        mock_svc.return_value.status.return_value.running = False
        result = _run_collection_remove(_make_collection_remove_args(path=path))

    assert result == 0
    call_args = mock_store.drop_collection.call_args
    assert call_args[0][0] == special_name


# ---------------------------------------------------------------------------
# Task 4.5 — help subcommands and argparser registration
# ---------------------------------------------------------------------------


def test_rag_help_prints_usage(capsys: pytest.CaptureFixture[str]) -> None:
    """run_rag with rag_command='help' calls print_help and returns 0."""
    import argparse
    from archon.cli.rag_cmd import run_rag

    p = argparse.ArgumentParser(prog="archon rag")
    p.add_argument("--install", help="install rag")
    args = argparse.Namespace(rag_command="help")
    result = run_rag(args, rag_parser=p)

    assert result == 0
    out = capsys.readouterr().out
    assert "archon rag" in out or "usage" in out.lower()
    assert "install" in out or "collection" in out or "usage" in out.lower()


def test_rag_no_subcommand_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    """run_rag with rag_command=None prints help and returns 0."""
    import argparse
    from archon.cli.rag_cmd import run_rag

    p = argparse.ArgumentParser(prog="archon rag")
    p.add_argument("--install", help="install rag")
    args = argparse.Namespace(rag_command=None)
    result = run_rag(args, rag_parser=p)

    assert result == 0
    out = capsys.readouterr().out
    assert "archon rag" in out or "usage" in out.lower()
    assert "install" in out or "collection" in out or "usage" in out.lower()


def test_collection_help_prints_usage(capsys: pytest.CaptureFixture[str]) -> None:
    """_run_collection with collection_command='help' prints help and returns 0."""
    import argparse
    from archon.cli.rag_cmd import _run_collection

    p = argparse.ArgumentParser(prog="archon rag collection")
    p.add_argument("--add", help="add collection")
    args = argparse.Namespace(rag_command="collection", collection_command="help")
    result = _run_collection(args, collection_parser=p)

    assert result == 0
    out = capsys.readouterr().out
    assert "archon rag collection" in out or "usage" in out.lower()
    assert "add" in out or "remove" in out or "usage" in out.lower()


def test_collection_no_subcommand_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    """_run_collection with collection_command=None prints help and returns 0."""
    import argparse
    from archon.cli.rag_cmd import _run_collection

    p = argparse.ArgumentParser(prog="archon rag collection")
    p.add_argument("--add", help="add collection")
    args = argparse.Namespace(rag_command="collection", collection_command=None)
    result = _run_collection(args, collection_parser=p)

    assert result == 0
    out = capsys.readouterr().out
    assert "archon rag collection" in out or "usage" in out.lower()
    assert "add" in out or "remove" in out or "usage" in out.lower()


def test_rag_help_no_parser_prints_fallback(capsys: pytest.CaptureFixture[str]) -> None:
    """run_rag with rag_command='help' and rag_parser=None prints fallback and returns 0."""
    import argparse
    from archon.cli.rag_cmd import run_rag

    args = argparse.Namespace(rag_command="help")
    result = run_rag(args, rag_parser=None)

    assert result == 0
    out = capsys.readouterr().out
    assert "archon rag" in out.lower() or "usage" in out.lower()


def test_rag_no_subcommand_no_parser_prints_fallback(capsys: pytest.CaptureFixture[str]) -> None:
    """run_rag with rag_command=None and rag_parser=None prints fallback and returns 0."""
    import argparse
    from archon.cli.rag_cmd import run_rag

    args = argparse.Namespace(rag_command=None)
    result = run_rag(args, rag_parser=None)

    assert result == 0
    out = capsys.readouterr().out
    assert "install" in out or "usage" in out.lower()


def test_collection_help_no_parser_prints_fallback(capsys: pytest.CaptureFixture[str]) -> None:
    """_run_collection with collection_command='help' and collection_parser=None prints fallback and returns 0."""
    import argparse
    from archon.cli.rag_cmd import _run_collection

    args = argparse.Namespace(rag_command="collection", collection_command="help")
    result = _run_collection(args, collection_parser=None)

    assert result == 0
    out = capsys.readouterr().out
    assert "list" in out or "usage" in out.lower()
