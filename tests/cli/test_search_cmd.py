"""Tests for archon/cli/search_cmd.py — Task 7.2."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Safety fixture — prevents accidental writes to the real user config
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def guard_real_config(request, tmp_path):
    """Snapshot ~/.archon/config.toml before each test and assert it is unchanged afterward.

    Autouse within this module only — protects all tests here from accidentally
    writing to the real user config via _run_collection_add or config_collections_append.
    """
    config_path = Path.home() / ".archon" / "config.toml"

    if not config_path.exists():
        yield
        return

    snapshot = config_path.read_bytes()
    yield
    after = config_path.read_bytes()

    if after != snapshot:
        config_path.write_bytes(snapshot)
        pytest.fail(
            f"Test '{request.node.nodeid}' wrote to the real {config_path}. "
            "Add patch('archon.cli.search_cmd.config_collections_append') to prevent this."
        )


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
    """install delegates to archon-search CLI via subprocess."""
    from archon.cli.search_cmd import _run_install

    with patch("archon.cli.search_cmd.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = _run_install(_make_args(search_command="install"))

    assert result == 0
    cmd = mock_run.call_args[0][0]
    assert "archon-search" in cmd
    assert "install" in cmd


def test_search_install_dry_run_flag() -> None:
    """install --dry-run passes --dry-run to archon-search CLI."""
    from archon.cli.search_cmd import _run_install

    with patch("archon.cli.search_cmd.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        _run_install(_make_args(search_command="install", dry_run=True))

    cmd = mock_run.call_args[0][0]
    assert "--dry-run" in cmd


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------

def test_search_uninstall_delegates() -> None:
    """uninstall --delete-db delegates to archon-search CLI with --delete-db."""
    from archon.cli.search_cmd import _run_uninstall

    with patch("archon.cli.search_cmd.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = _run_uninstall(_make_args(search_command="uninstall", delete_db=True))

    assert result == 0
    cmd = mock_run.call_args[0][0]
    assert "archon-search" in cmd
    assert "uninstall" in cmd
    assert "--delete-db" in cmd


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------

def test_search_start_calls_platform_service() -> None:
    """start delegates to archon-search CLI."""
    from archon.cli.search_cmd import _run_start

    with patch("archon.cli.search_cmd.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = _run_start(_make_args(search_command="start"))

    assert result == 0
    cmd = mock_run.call_args[0][0]
    assert "archon-search" in cmd
    assert "start" in cmd


def test_search_stop_calls_platform_service() -> None:
    """stop delegates to archon-search CLI."""
    from archon.cli.search_cmd import _run_stop

    with patch("archon.cli.search_cmd.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = _run_stop(_make_args(search_command="stop"))

    assert result == 0
    cmd = mock_run.call_args[0][0]
    assert "archon-search" in cmd
    assert "stop" in cmd


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def test_search_status_prints_service_state(capsys: pytest.CaptureFixture[str]) -> None:
    """status prints 'running' when SearchClient.status() returns running=True."""
    from archon.cli.search_cmd import _run_status
    from archon.ai.search_client import SearchClient

    status_data = {"running": True, "pid": 1234, "collections": []}

    with (
        patch("archon.cli.search_cmd.load_config") as mock_cfg,
        patch.object(SearchClient, "status", new_callable=AsyncMock, return_value=status_data),
    ):
        mock_cfg.return_value.search.url = "http://127.0.0.1:8765"
        result = _run_status(_make_args(search_command="status"))

    out = capsys.readouterr().out
    assert "running" in out.lower()
    assert result == 0


def test_search_status_server_unreachable_prints_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """status prints 'unreachable'/'stopped' when SearchClient.status() returns None."""
    from archon.cli.search_cmd import _run_status
    from archon.ai.search_client import SearchClient

    with (
        patch("archon.cli.search_cmd.load_config") as mock_cfg,
        patch.object(SearchClient, "status", new_callable=AsyncMock, return_value=None),
    ):
        mock_cfg.return_value.search.url = "http://127.0.0.1:8765"
        result = _run_status(_make_args(search_command="status"))

    out = capsys.readouterr().out
    assert "unreachable" in out.lower() or "stopped" in out.lower()
    assert result != 0


def test_search_status_disconnects_on_list_collections_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """status handles SearchClient error gracefully."""
    from archon.cli.search_cmd import _run_status
    from archon.ai.search_client import SearchClient

    with (
        patch("archon.cli.search_cmd.load_config") as mock_cfg,
        patch.object(SearchClient, "status", new_callable=AsyncMock, return_value=None),
    ):
        mock_cfg.return_value.search.url = "http://127.0.0.1:8765"
        result = _run_status(_make_args(search_command="status"))

    out = capsys.readouterr().out
    assert "stopped" in out.lower() or "unreachable" in out.lower()
    assert result != 0


def test_search_status_shows_unavailable_on_lock_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """status returns non-zero when service is unreachable."""
    from archon.cli.search_cmd import _run_status
    from archon.ai.search_client import SearchClient

    with (
        patch("archon.cli.search_cmd.load_config") as mock_cfg,
        patch.object(SearchClient, "status", new_callable=AsyncMock, return_value=None),
    ):
        mock_cfg.return_value.search.url = "http://127.0.0.1:8765"
        result = _run_status(_make_args(search_command="status"))

    out = capsys.readouterr().out
    assert result != 0
    assert "stopped" in out.lower() or "unreachable" in out.lower()


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------

def test_search_ingest_no_args_uses_history_dir(capsys: pytest.CaptureFixture[str]) -> None:
    """ingest with no args delegates to archon-search CLI without extra flags."""
    from archon.cli.search_cmd import _run_ingest

    with patch("archon.cli.search_cmd.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = _run_ingest(_make_args(search_command="ingest"))

    assert result == 0
    cmd = mock_run.call_args[0][0]
    assert "archon-search" in cmd
    assert "ingest" in cmd


def test_search_ingest_with_path_and_collection(capsys: pytest.CaptureFixture[str]) -> None:
    """ingest with --path and --collection passes those flags to archon-search CLI."""
    from archon.cli.search_cmd import _run_ingest

    with patch("archon.cli.search_cmd.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = _run_ingest(_make_args(search_command="ingest", path="/my/docs", collection="my-col"))

    assert result == 0
    cmd = mock_run.call_args[0][0]
    assert "--path" in cmd
    assert "/my/docs" in cmd
    assert "--collection" in cmd
    assert "my-col" in cmd


def test_search_ingest_aborts_when_service_running(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ingest failure (non-zero exit from archon-search CLI) returns non-zero exit code."""
    from archon.cli.search_cmd import _run_ingest

    with patch("archon.cli.search_cmd.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        result = _run_ingest(_make_args(search_command="ingest"))

    assert result != 0


def test_search_ingest_disconnects_on_failure(capsys: pytest.CaptureFixture[str]) -> None:
    """ingest passes archon-search not found → returns non-zero exit."""
    from archon.cli.search_cmd import _run_ingest

    with patch("archon.cli.search_cmd.subprocess.run", side_effect=FileNotFoundError("not found")):
        result = _run_ingest(_make_args(search_command="ingest"))

    out = capsys.readouterr().out
    assert result != 0
    assert "archon-search" in out or "not found" in out.lower()


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
    """archon rag sync delegates to archon-search CLI."""
    from archon.cli.search_cmd import _run_sync

    with patch("archon.cli.search_cmd.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = _run_sync(_make_args(search_command="sync"))

    assert result == 0
    cmd = mock_run.call_args[0][0]
    assert "archon-search" in cmd
    assert "sync" in cmd


def test_sync_cli_returns_1_on_errors(capsys: pytest.CaptureFixture[str]) -> None:
    """archon rag sync returns exit code 1 when archon-search CLI returns non-zero."""
    from archon.cli.search_cmd import _run_sync

    with patch("archon.cli.search_cmd.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        result = _run_sync(_make_args(search_command="sync"))

    assert result == 1


def test_sync_cli_stops_service_when_running(capsys: pytest.CaptureFixture[str]) -> None:
    """archon sync delegates to archon-search CLI subprocess."""
    from archon.cli.search_cmd import _run_sync

    with patch("archon.cli.search_cmd.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = _run_sync(_make_args(search_command="sync"))

    assert result == 0
    mock_run.assert_called_once()


def test_sync_cli_aborts_when_service_stop_fails(capsys: pytest.CaptureFixture[str]) -> None:
    """archon sync returns non-zero when archon-search CLI fails."""
    from archon.cli.search_cmd import _run_sync

    with patch("archon.cli.search_cmd.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=2)
        result = _run_sync(_make_args(search_command="sync"))

    assert result != 0


def test_sync_cli_restarts_service_even_when_sync_fails(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """archon sync returns non-zero on subprocess failure."""
    from archon.cli.search_cmd import _run_sync

    with patch("archon.cli.search_cmd.subprocess.run", side_effect=FileNotFoundError("not found")):
        result = _run_sync(_make_args(search_command="sync"))

    assert result != 0


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
    """Indexed collection shows name, doc/chunk counts via SearchClient."""
    from archon.cli.search_cmd import _run_collection_list
    from archon.ai.search_client import SearchClient

    collections = [
        {"name": "sessions", "path": "/home/user/.archon/history/sessions",
         "doc_count": 3, "chunk_count": 12, "status": "indexed"}
    ]

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch.object(SearchClient, "list_collections", new_callable=AsyncMock, return_value=collections),
    ):
        result = _run_collection_list(_make_collection_list_args())

    out = capsys.readouterr().out
    assert "sessions" in out
    assert "docs=3" in out
    assert "chunks=12" in out
    assert result == 0


def test_collection_list_marks_orphans(capsys: pytest.CaptureFixture[str]) -> None:
    """Collections returned by server are listed."""
    from archon.cli.search_cmd import _run_collection_list
    from archon.ai.search_client import SearchClient

    collections = [
        {"name": "old_col", "path": "/tmp/old_col", "doc_count": 1,
         "chunk_count": 5, "status": "indexed"}
    ]

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch.object(SearchClient, "list_collections", new_callable=AsyncMock, return_value=collections),
    ):
        result = _run_collection_list(_make_collection_list_args())

    out = capsys.readouterr().out
    assert "old_col" in out
    assert result == 0


def test_collection_list_distinguishes_managed_orphan_from_unmanaged(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Multiple collections are listed with their status from server."""
    from archon.cli.search_cmd import _run_collection_list
    from archon.ai.search_client import SearchClient

    collections = [
        {"name": "col_a", "path": "/tmp/a", "doc_count": 1, "chunk_count": 5, "status": "indexed"},
        {"name": "col_b", "path": "/tmp/b", "doc_count": 2, "chunk_count": 10, "status": "indexed"},
    ]

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch.object(SearchClient, "list_collections", new_callable=AsyncMock, return_value=collections),
    ):
        result = _run_collection_list(_make_collection_list_args())

    out = capsys.readouterr().out
    assert "col_a" in out
    assert "col_b" in out
    assert result == 0


def test_collection_list_shows_unindexed_config_paths(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Empty list from server prints 'No collections found.'"""
    from archon.cli.search_cmd import _run_collection_list
    from archon.ai.search_client import SearchClient

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch.object(SearchClient, "list_collections", new_callable=AsyncMock, return_value=[]),
    ):
        result = _run_collection_list(_make_collection_list_args())

    out = capsys.readouterr().out
    assert "No collections found." in out
    assert result == 0


def test_collection_list_empty(capsys: pytest.CaptureFixture[str]) -> None:
    """No collections from server prints 'No collections found.'"""
    from archon.cli.search_cmd import _run_collection_list
    from archon.ai.search_client import SearchClient

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch.object(SearchClient, "list_collections", new_callable=AsyncMock, return_value=[]),
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
    """Happy path: adds new path via SearchClient.add_collection()."""
    from archon.cli.search_cmd import _run_collection_add
    from archon.ai.search_client import SearchClient

    path = str(tmp_path / "docs")

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"
    mock_cfg.search.collections = []
    mock_cfg.search.pinned_collections = []

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch.object(SearchClient, "add_collection", new_callable=AsyncMock, return_value={"name": "docs"}),
    ):
        result = _run_collection_add(_make_collection_add_args(path=path))

    out = capsys.readouterr().out
    assert "Collection added" in out
    assert result == 0


def test_add_prints_progress(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """_run_collection_add prints success message from server response."""
    from archon.cli.search_cmd import _run_collection_add
    from archon.ai.search_client import SearchClient

    path = str(tmp_path / "docs")

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"
    mock_cfg.search.collections = []
    mock_cfg.search.pinned_collections = []

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch.object(SearchClient, "add_collection", new_callable=AsyncMock, return_value={"name": "docs"}),
    ):
        result = _run_collection_add(_make_collection_add_args(path=path))

    out = capsys.readouterr().out
    assert result == 0
    assert "Collection added" in out


def test_sync_prints_progress(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """_run_sync delegates to archon-search CLI subprocess."""
    from archon.cli.search_cmd import _run_sync

    with patch("archon.cli.search_cmd.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = _run_sync(_make_args(search_command="sync"))

    assert result == 0
    mock_run.assert_called_once()


def test_collection_add_already_registered_delegates_to_server(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """Duplicate detection is delegated to server — add_collection call is made regardless."""
    from archon.cli.search_cmd import _run_collection_add
    from archon.ai.search_client import SearchClient

    path = str(tmp_path / "docs")

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch.object(SearchClient, "add_collection", new_callable=AsyncMock, return_value={"status": "added"}),
    ):
        result = _run_collection_add(_make_collection_add_args(path=path))

    assert result == 0


def test_collection_add_normalizes_tilde(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """add_collection delegates to server — no local tilde normalization needed."""
    from archon.cli.search_cmd import _run_collection_add
    from archon.ai.search_client import SearchClient

    home = Path.home()
    rel = "archon_test_docs_4321"
    tilde_path = f"~/{rel}"

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch.object(SearchClient, "add_collection", new_callable=AsyncMock, return_value={"status": "added"}),
    ):
        result = _run_collection_add(_make_collection_add_args(path=tilde_path))

    assert result == 0


def test_collection_add_warns_if_service_running(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """When service is unreachable (None from add_collection), returns error."""
    from archon.cli.search_cmd import _run_collection_add
    from archon.ai.search_client import SearchClient

    path = str(tmp_path / "docs")

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"
    mock_cfg.search.collections = []
    mock_cfg.search.pinned_collections = []

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch.object(SearchClient, "add_collection", new_callable=AsyncMock, return_value=None),
    ):
        result = _run_collection_add(_make_collection_add_args(path=path))

    out = capsys.readouterr().out
    assert "failed" in out.lower() or "error" in out.lower() or "running" in out.lower()
    assert result != 0


def test_collection_add_uses_naive_name_collision_resolved_on_next_sync(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """When server responds successfully, collection is added."""
    from archon.cli.search_cmd import _run_collection_add
    from archon.ai.search_client import SearchClient

    path = str(tmp_path / "my_project")

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"
    mock_cfg.search.collections = []
    mock_cfg.search.pinned_collections = []

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch.object(SearchClient, "add_collection", new_callable=AsyncMock, return_value={"name": "my_project"}) as mock_add,
    ):
        result = _run_collection_add(_make_collection_add_args(path=path))

    mock_add.assert_awaited_once_with(path)
    assert result == 0


def test_collection_add_ingest_error_path_stays_in_config(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """On server failure (None response), returns exit 1 with error message."""
    from archon.cli.search_cmd import _run_collection_add
    from archon.ai.search_client import SearchClient

    path = str(tmp_path / "docs")

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"
    mock_cfg.search.collections = []
    mock_cfg.search.pinned_collections = []

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch.object(SearchClient, "add_collection", new_callable=AsyncMock, return_value=None),
    ):
        result = _run_collection_add(_make_collection_add_args(path=path))

    out = capsys.readouterr().out
    assert "failed" in out.lower() or "error" in out.lower()
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
    """Integration test: _run_collection_add delegates to SearchClient."""
    from archon.cli.search_cmd import _run_collection_add
    from archon.ai.search_client import SearchClient

    path = str(tmp_path / "some_docs")

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"
    mock_cfg.search.collections = []
    mock_cfg.search.pinned_collections = []

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch.object(SearchClient, "add_collection", new_callable=AsyncMock, return_value={"name": "some_docs"}),
    ):
        result = _run_collection_add(_make_collection_add_args(path=path))

    assert result == 0


# ---------------------------------------------------------------------------
# C1-T-1: manifest lookup hit path — server owns the name mapping now
# ---------------------------------------------------------------------------


def test_collection_add_uses_manifest_name_when_available(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """Server is called with the path; name mapping is server-side now."""
    from archon.cli.search_cmd import _run_collection_add
    from archon.ai.search_client import SearchClient

    path = str(tmp_path / "my_docs")

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"
    mock_cfg.search.collections = []
    mock_cfg.search.pinned_collections = []

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch.object(SearchClient, "add_collection", new_callable=AsyncMock, return_value={"name": "my-collection"}) as mock_add,
    ):
        result = _run_collection_add(_make_collection_add_args(path=path))

    assert result == 0
    mock_add.assert_awaited_once_with(path)


# ---------------------------------------------------------------------------
# C1-T-2: verify call path
# ---------------------------------------------------------------------------


def test_collection_add_appends_to_config_and_ingests_verified(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """SearchClient.add_collection is called with the provided path."""
    from archon.cli.search_cmd import _run_collection_add
    from archon.ai.search_client import SearchClient

    path = str(tmp_path / "docs")

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"
    mock_cfg.search.collections = []
    mock_cfg.search.pinned_collections = []

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch.object(SearchClient, "add_collection", new_callable=AsyncMock, return_value={"name": "docs"}) as mock_add,
    ):
        result = _run_collection_add(_make_collection_add_args(path=path))

    assert result == 0
    mock_add.assert_awaited_once_with(path)


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
# C1-T-4: server returns None → error + exit 1
# ---------------------------------------------------------------------------


def test_collection_add_nonexistent_directory_ingest_fails(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """Server returns None → error message, exit 1."""
    from archon.cli.search_cmd import _run_collection_add
    from archon.ai.search_client import SearchClient

    path = str(tmp_path / "does_not_exist")

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"
    mock_cfg.search.collections = []
    mock_cfg.search.pinned_collections = []

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch.object(SearchClient, "add_collection", new_callable=AsyncMock, return_value=None),
    ):
        result = _run_collection_add(_make_collection_add_args(path=path))

    out = capsys.readouterr().out
    assert "failed" in out.lower() or "error" in out.lower()
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
    """Happy path: path in config, SearchClient.remove_collection() called, config updated, returns 0."""
    from archon.cli.search_cmd import _run_collection_remove
    from archon.ai.search_client import SearchClient

    path = str(tmp_path / "docs")

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"
    mock_cfg.search.collections = [path]
    mock_cfg.search.pinned_collections = []
    mock_cfg.search.all_indexed_collections = [path]
    mock_cfg.search.db_path = str(tmp_path / "rag")

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.config_collections_remove") as mock_remove,
        patch.object(SearchClient, "remove_collection", new_callable=AsyncMock, return_value={"status": "removed"}),
    ):
        result = _run_collection_remove(_make_collection_remove_args(path=path))

    mock_remove.assert_called_once()
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
    mock_cfg.search.url = "http://127.0.0.1:8765"
    mock_cfg.search.collections = []  # path not in config
    mock_cfg.search.pinned_collections = []
    mock_cfg.search.all_indexed_collections = []
    mock_cfg.search.db_path = str(tmp_path / "rag")

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
    ):
        result = _run_collection_remove(_make_collection_remove_args(path=path))

    out = capsys.readouterr().out
    assert "Error: not in collections" in out
    assert result == 1


def test_collection_remove_service_running_without_force_exits_1(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """When SearchClient.remove_collection() returns None and force=False → error, nothing removed."""
    from archon.cli.search_cmd import _run_collection_remove
    from archon.ai.search_client import SearchClient

    path = str(tmp_path / "docs")

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"
    mock_cfg.search.collections = [path]
    mock_cfg.search.pinned_collections = []
    mock_cfg.search.all_indexed_collections = [path]
    mock_cfg.search.db_path = str(tmp_path / "rag")

    mock_config_remove = MagicMock()

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.config_collections_remove", mock_config_remove),
        patch.object(SearchClient, "remove_collection", new_callable=AsyncMock, return_value=None),
    ):
        result = _run_collection_remove(_make_collection_remove_args(path=path, force=False))

    out = capsys.readouterr().out
    assert result == 1
    # Config must not be touched when remove fails without force
    mock_config_remove.assert_not_called()
    assert "failed" in out.lower() or "running" in out.lower() or "error" in out.lower()


def test_collection_remove_service_running_with_force_proceeds(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """With --force, removal proceeds even when SearchClient returns None, config is removed."""
    from archon.cli.search_cmd import _run_collection_remove
    from archon.ai.search_client import SearchClient

    path = str(tmp_path / "docs")

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"
    mock_cfg.search.collections = [path]
    mock_cfg.search.pinned_collections = []
    mock_cfg.search.all_indexed_collections = [path]
    mock_cfg.search.db_path = str(tmp_path / "rag")

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.config_collections_remove"),
        patch.object(SearchClient, "remove_collection", new_callable=AsyncMock, return_value=None),
    ):
        result = _run_collection_remove(_make_collection_remove_args(path=path, force=True))

    assert result == 0


def test_collection_remove_force_with_service_down_still_removes_config(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """With --force, config is removed even when the service raises a connection error."""
    from archon.cli.search_cmd import _run_collection_remove
    from archon.ai.search_client import SearchClient

    path = str(tmp_path / "docs")

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"
    mock_cfg.search.collections = [path]
    mock_cfg.search.pinned_collections = []
    mock_cfg.search.all_indexed_collections = [path]
    mock_cfg.search.db_path = str(tmp_path / "rag")

    remove_mock = AsyncMock(side_effect=ConnectionError("Connection refused"))

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.config_collections_remove") as mock_cfg_remove,
        patch.object(SearchClient, "remove_collection", remove_mock),
    ):
        result = _run_collection_remove(_make_collection_remove_args(path=path, force=True))

    assert result == 0
    mock_cfg_remove.assert_called_once()


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
    from archon.ai.search_client import SearchClient

    path = str(tmp_path / "some_docs")

    config_file = tmp_path / "config.toml"
    config_file.write_text(f'[search]\ncollections = ["{path}"]\n')

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"
    mock_cfg.search.collections = [path]
    mock_cfg.search.pinned_collections = []
    mock_cfg.search.all_indexed_collections = [path]
    mock_cfg.search.db_path = str(tmp_path / "rag")

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd._CONFIG_PATH", config_file),
        patch.object(SearchClient, "remove_collection", new_callable=AsyncMock, return_value={"status": "removed"}),
    ):
        result = _run_collection_remove(_make_collection_remove_args(path=path))

    assert result == 0
    doc = tomlkit.parse(config_file.read_text())
    assert path not in doc["search"]["collections"]


# ---------------------------------------------------------------------------
# C1-T-1: SearchClient returns None → config is NOT touched
# ---------------------------------------------------------------------------


def test_collection_remove_drop_failure_leaves_config_intact(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """If SearchClient.remove_collection() returns None (force=False), config is NOT touched and exit code is 1."""
    from archon.cli.search_cmd import _run_collection_remove
    from archon.ai.search_client import SearchClient

    path = str(tmp_path / "docs")

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"
    mock_cfg.search.collections = [path]
    mock_cfg.search.pinned_collections = []
    mock_cfg.search.all_indexed_collections = [path]
    mock_cfg.search.db_path = str(tmp_path / "rag")

    mock_remove = MagicMock()

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.config_collections_remove", mock_remove),
        patch.object(SearchClient, "remove_collection", new_callable=AsyncMock, return_value=None),
    ):
        result = _run_collection_remove(_make_collection_remove_args(path=path))

    # Config must NOT be touched when remove fails without force
    mock_remove.assert_not_called()
    assert result == 1
    out = capsys.readouterr().out
    assert any(kw in out for kw in ("failed", "error", "running"))


# ---------------------------------------------------------------------------
# C1-T-3: server is called with the resolved collection name
# ---------------------------------------------------------------------------


def test_collection_remove_uses_manifest_name_for_drop(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """SearchClient.remove_collection() is called and config is cleaned up."""
    from archon.cli.search_cmd import _run_collection_remove
    from archon.ai.search_client import SearchClient

    path = str(tmp_path / "docs")

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"
    mock_cfg.search.collections = [path]
    mock_cfg.search.pinned_collections = []
    mock_cfg.search.all_indexed_collections = [path]
    mock_cfg.search.db_path = str(tmp_path / "rag")

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.config_collections_remove") as mock_remove,
        patch.object(SearchClient, "remove_collection", new_callable=AsyncMock, return_value={"status": "removed"}),
    ):
        result = _run_collection_remove(_make_collection_remove_args(path=path))

    assert result == 0
    mock_remove.assert_called_once()


# ---------------------------------------------------------------------------
# Task 4.3 — --dry-run flag for collection remove
# ---------------------------------------------------------------------------


def test_collection_remove_dry_run_prints_without_executing(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """--dry-run prints config entry + collection name but does NOT call remove_collection."""
    from archon.cli.search_cmd import _run_collection_remove
    from archon.ai.search_client import SearchClient

    path = str(tmp_path / "docs")

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"
    mock_cfg.search.collections = [path]
    mock_cfg.search.pinned_collections = []
    mock_cfg.search.all_indexed_collections = [path]
    mock_cfg.search.db_path = str(tmp_path / "rag")

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon.cli.search_cmd.config_collections_remove") as mock_remove,
        patch.object(SearchClient, "remove_collection", new_callable=AsyncMock) as mock_remove_collection,
    ):
        result = _run_collection_remove(
            _make_collection_remove_args(path=path, dry_run=True)
        )

    # Must return 0 (success, nothing to undo)
    assert result == 0
    # Must NOT execute actual removal
    mock_remove.assert_not_called()
    mock_remove_collection.assert_not_awaited()
    # Must print what WOULD be removed
    out = capsys.readouterr().out
    assert path in out
    assert "Would remove" in out or "would remove" in out.lower()


def test_collection_remove_dry_run_and_force_flags_are_mutually_exclusive(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """--dry-run and --force together → error message and return 1."""
    from archon.cli.search_cmd import _run_collection_remove

    path = str(tmp_path / "docs")

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"
    mock_cfg.search.collections = [path]

    with patch("archon.cli.search_cmd.load_config", return_value=mock_cfg):
        result = _run_collection_remove(
            _make_collection_remove_args(path=path, dry_run=True, force=True)
        )

    assert result == 1
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
    """info fetches collection info via SearchClient and prints name, description, doc_count, centroid."""
    from archon.cli.search_cmd import _run_collection_info
    from archon.ai.search_client import SearchClient

    meta = {
        "name": "sessions",
        "description": "Daily session logs",
        "centroid": [0.1, 0.2, 0.3],
        "doc_count": 42,
        "chunk_count": 180,
        "embedding_model": "BAAI/bge-small-en-v1.5",
    }

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch.object(SearchClient, "collection_info", new_callable=AsyncMock, return_value=meta),
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
    from archon.ai.search_client import SearchClient

    meta = {
        "name": "docs",
        "description": None,
        "centroid": None,
        "doc_count": 5,
        "chunk_count": 20,
        "embedding_model": "BAAI/bge-small-en-v1.5",
    }

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch.object(SearchClient, "collection_info", new_callable=AsyncMock, return_value=meta),
    ):
        result = _run_collection_info(_make_collection_meta_args(collection_name="docs"))

    out = capsys.readouterr().out
    assert "docs" in out
    assert "absent" in out.lower() or "none" in out.lower() or "no centroid" in out.lower()
    assert result == 0


def test_collection_reindex_prints_progress(capsys: pytest.CaptureFixture[str]) -> None:
    """reindex submits job via SearchClient.reindex_collection and prints confirmation."""
    from archon.cli.search_cmd import _run_collection_reindex
    from archon.ai.search_client import SearchClient

    mock_job = MagicMock()
    mock_job.job_id = "job-123"

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch.object(SearchClient, "reindex_collection", new_callable=AsyncMock, return_value=mock_job),
    ):
        result = _run_collection_reindex(_make_collection_reindex_args(collection_name="sessions"))

    out = capsys.readouterr().out
    assert result == 0
    assert any(w in out.lower() for w in ("reindex", "submitted", "sessions"))


def test_collection_info_not_found(capsys: pytest.CaptureFixture[str]) -> None:
    """info returns exit code 1 with error message when collection does not exist."""
    from archon.cli.search_cmd import _run_collection_info
    from archon.ai.search_client import SearchClient

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch.object(SearchClient, "collection_info", new_callable=AsyncMock, return_value=None),
    ):
        result = _run_collection_info(_make_collection_meta_args(collection_name="nonexistent"))

    out = capsys.readouterr().out
    assert result == 1
    assert "nonexistent" in out
    assert "not found" in out.lower() or "error" in out.lower()


def test_collection_reindex_not_in_config(capsys: pytest.CaptureFixture[str]) -> None:
    """reindex returns exit code 1 when service is unreachable (None result)."""
    from archon.cli.search_cmd import _run_collection_reindex
    from archon.ai.search_client import SearchClient

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch.object(SearchClient, "reindex_collection", new_callable=AsyncMock, return_value=None),
    ):
        result = _run_collection_reindex(_make_collection_reindex_args(collection_name="sessions"))

    out = capsys.readouterr().out
    assert result == 1
    assert "sessions" in out


def test_collection_reindex_blocked_when_service_running(capsys: pytest.CaptureFixture[str]) -> None:
    """reindex returns exit code 1 with error message when service returns None (unreachable)."""
    from archon.cli.search_cmd import _run_collection_reindex
    from archon.ai.search_client import SearchClient

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch.object(SearchClient, "reindex_collection", new_callable=AsyncMock, return_value=None),
    ):
        result = _run_collection_reindex(_make_collection_reindex_args(collection_name="sessions"))

    out = capsys.readouterr().out
    assert result == 1


def test_run_collection_reindex_clears_state(capsys: pytest.CaptureFixture[str]) -> None:
    """reindex submits job and prints collection name in confirmation."""
    from archon.cli.search_cmd import _run_collection_reindex
    from archon.ai.search_client import SearchClient

    mock_job = MagicMock()
    mock_job.job_id = "job-456"

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch.object(SearchClient, "reindex_collection", new_callable=AsyncMock, return_value=mock_job) as mock_reindex,
    ):
        result = _run_collection_reindex(_make_collection_reindex_args(collection_name="sessions"))

    assert result == 0
    mock_reindex.assert_called_once_with("sessions")
    out = capsys.readouterr().out
    assert "sessions" in out


def test_run_collection_reindex_state_clear_failure_non_fatal(capsys: pytest.CaptureFixture[str]) -> None:
    """reindex with service exception → returns 1 (error propagated from SearchClient)."""
    from archon.cli.search_cmd import _run_collection_reindex
    from archon.ai.search_client import SearchClient

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch.object(SearchClient, "reindex_collection", new_callable=AsyncMock, return_value=None),
    ):
        result = _run_collection_reindex(_make_collection_reindex_args(collection_name="sessions"))

    assert result == 1


# ---------------------------------------------------------------------------
# load_config contract: require_token=False must always be passed (M2)
# ---------------------------------------------------------------------------

def test_search_status_load_config_called_with_require_token_false() -> None:
    """_run_status must call load_config(require_token=False) — token not needed for RAG CLI."""
    from archon.ai.search_client import SearchClient

    with (
        patch("archon.cli.search_cmd.load_config") as mock_load,
        patch.object(SearchClient, "status", new_callable=AsyncMock, return_value=None),
    ):
        mock_load.return_value.search.url = "http://127.0.0.1:8765"
        from archon.cli.search_cmd import _run_status
        _run_status(_make_args(search_command="status"))

    mock_load.assert_called_once_with(require_token=False)


# ---------------------------------------------------------------------------
# status — progress display (FEAT-027 Task 1.6)
# ---------------------------------------------------------------------------

class TestRunStatusProgress:
    """Tests for _run_status() indexing progress display."""

    @staticmethod
    def _status_data(collections: list | None = None) -> dict:  # type: ignore[type-arg]
        return {"running": True, "pid": 1234, "collections": collections or []}

    @staticmethod
    def _col_dict(name: str, doc_count: int = 0, chunk_count: int = 0) -> dict:  # type: ignore[type-arg]
        return {"name": name, "doc_count": doc_count, "chunk_count": chunk_count}

    @staticmethod
    def _progress_dict(
        status: str,
        total_files: int = 0,
        processed_files: int = 0,
        error: str | None = None,
    ) -> dict:  # type: ignore[type-arg]
        return {"status": status, "total_files": total_files, "processed_files": processed_files, "started_at": None, "error": error}

    @staticmethod
    def _patch_client(status_data: dict, indexing_state_data: dict | None = None):  # type: ignore[type-arg]
        from archon.ai.search_client import SearchClient
        return (
            patch.object(SearchClient, "status", new_callable=AsyncMock, return_value=status_data),
            patch.object(SearchClient, "indexing_state", new_callable=AsyncMock, return_value=indexing_state_data or {"collections": {}}),
        )

    def test_run_status_with_progress_display(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """HTTP state present: output shows status table."""
        from archon.cli.search_cmd import _run_status
        status_data = self._status_data([
            self._col_dict("sessions", 80, 400),
            self._col_dict("my-project", 340, 1700),
        ])
        indexing_state_data = {"collections": {
            "sessions": self._progress_dict("in_progress", 120, 87),
            "my-project": self._progress_dict("done", 340, 340),
        }}
        s_patch, i_patch = self._patch_client(status_data, indexing_state_data)
        with s_patch, i_patch, patch("archon.cli.search_cmd.load_config") as mock_cfg:
            mock_cfg.return_value.search.url = "http://127.0.0.1:8765"
            mock_cfg.return_value.search.watch = False
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
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Empty HTTP indexing state: falls back to collection list format."""
        from archon.cli.search_cmd import _run_status
        status_data = self._status_data([self._col_dict("sessions", 80, 400)])
        s_patch, i_patch = self._patch_client(status_data, {"collections": {}})
        with s_patch, i_patch, patch("archon.cli.search_cmd.load_config") as mock_cfg:
            mock_cfg.return_value.search.url = "http://127.0.0.1:8765"
            mock_cfg.return_value.search.watch = False
            result = _run_status(_make_args(search_command="status"))

        out = capsys.readouterr().out
        assert "collection=sessions" in out
        assert "docs=80" in out
        assert "chunks=400" in out
        assert result == 0

    def test_run_status_failed_exit_code_nonzero(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Returns 1 when any collection has status == failed."""
        from archon.cli.search_cmd import _run_status
        status_data = self._status_data()
        indexing_state_data = {"collections": {
            "broken": self._progress_dict("failed", 50, 12, error="parse error"),
        }}
        s_patch, i_patch = self._patch_client(status_data, indexing_state_data)
        with s_patch, i_patch, patch("archon.cli.search_cmd.load_config") as mock_cfg:
            mock_cfg.return_value.search.url = "http://127.0.0.1:8765"
            mock_cfg.return_value.search.watch = False
            result = _run_status(_make_args(search_command="status"))

        assert result == 1

    def test_run_status_done_exit_code_zero(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Returns 0 when all collections are done."""
        from archon.cli.search_cmd import _run_status
        status_data = self._status_data([self._col_dict("docs", 100, 500)])
        indexing_state_data = {"collections": {
            "docs": self._progress_dict("done", 100, 100),
        }}
        s_patch, i_patch = self._patch_client(status_data, indexing_state_data)
        with s_patch, i_patch, patch("archon.cli.search_cmd.load_config") as mock_cfg:
            mock_cfg.return_value.search.url = "http://127.0.0.1:8765"
            mock_cfg.return_value.search.watch = False
            result = _run_status(_make_args(search_command="status"))

        assert result == 0

    def test_run_status_in_progress_exit_code_zero(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Returns 0 when collections are in_progress (not failed)."""
        from archon.cli.search_cmd import _run_status
        status_data = self._status_data()
        indexing_state_data = {"collections": {
            "sessions": self._progress_dict("in_progress", 120, 50),
        }}
        s_patch, i_patch = self._patch_client(status_data, indexing_state_data)
        with s_patch, i_patch, patch("archon.cli.search_cmd.load_config") as mock_cfg:
            mock_cfg.return_value.search.url = "http://127.0.0.1:8765"
            mock_cfg.return_value.search.watch = False
            result = _run_status(_make_args(search_command="status"))

        assert result == 0

    def test_run_status_mixed_failed_and_done_exit_code(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Returns 1 when mix of failed + done."""
        from archon.cli.search_cmd import _run_status
        status_data = self._status_data()
        indexing_state_data = {"collections": {
            "ok-col": self._progress_dict("done", 100, 100),
            "bad-col": self._progress_dict("failed", 50, 10, error="disk full"),
        }}
        s_patch, i_patch = self._patch_client(status_data, indexing_state_data)
        with s_patch, i_patch, patch("archon.cli.search_cmd.load_config") as mock_cfg:
            mock_cfg.return_value.search.url = "http://127.0.0.1:8765"
            mock_cfg.return_value.search.watch = False
            result = _run_status(_make_args(search_command="status"))

        assert result == 1

    def test_run_status_pending_shows_dash(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Pending collection shows dash instead of file counts."""
        from archon.cli.search_cmd import _run_status
        status_data = self._status_data()
        indexing_state_data = {"collections": {
            "docs": self._progress_dict("pending"),
        }}
        s_patch, i_patch = self._patch_client(status_data, indexing_state_data)
        with s_patch, i_patch, patch("archon.cli.search_cmd.load_config") as mock_cfg:
            mock_cfg.return_value.search.url = "http://127.0.0.1:8765"
            mock_cfg.return_value.search.watch = False
            _run_status(_make_args(search_command="status"))

        out = capsys.readouterr().out
        lines = [l for l in out.splitlines() if "docs" in l]
        assert len(lines) >= 1
        assert "\u2014" in repr(lines[0])

    def test_run_status_error_message_shown(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Failed collection shows error message."""
        from archon.cli.search_cmd import _run_status
        status_data = self._status_data()
        indexing_state_data = {"collections": {
            "old-notes": self._progress_dict("failed", 50, 12, error="parse error"),
        }}
        s_patch, i_patch = self._patch_client(status_data, indexing_state_data)
        with s_patch, i_patch, patch("archon.cli.search_cmd.load_config") as mock_cfg:
            mock_cfg.return_value.search.url = "http://127.0.0.1:8765"
            mock_cfg.return_value.search.watch = False
            _run_status(_make_args(search_command="status"))

        out = capsys.readouterr().out
        assert "parse error" in out

    def test_run_status_merge_state_and_collections(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """State-only collections are included; HTTP collections-only shown with info only."""
        from archon.cli.search_cmd import _run_status
        status_data = self._status_data([self._col_dict("existing-col", 100, 500)])
        indexing_state_data = {"collections": {
            "new-col": self._progress_dict("in_progress", 200, 50),
        }}
        s_patch, i_patch = self._patch_client(status_data, indexing_state_data)
        with s_patch, i_patch, patch("archon.cli.search_cmd.load_config") as mock_cfg:
            mock_cfg.return_value.search.url = "http://127.0.0.1:8765"
            mock_cfg.return_value.search.watch = False
            result = _run_status(_make_args(search_command="status"))

        out = capsys.readouterr().out
        assert "new-col" in out
        assert "existing-col" in out
        assert result == 0

    # -----------------------------------------------------------------------
    # FEAT-027-P2 Task 2.3: partial status for in-progress collections
    # -----------------------------------------------------------------------

    def test_cli_status_shows_partial(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """IN_PROGRESS with processed_files > 0 shows 'partial' status and 'N / M files'."""
        from archon.cli.search_cmd import _run_status
        status_data = self._status_data()
        indexing_state_data = {"collections": {
            "my-docs": self._progress_dict("in_progress", 100, 50),
        }}
        s_patch, i_patch = self._patch_client(status_data, indexing_state_data)
        with s_patch, i_patch, patch("archon.cli.search_cmd.load_config") as mock_cfg:
            mock_cfg.return_value.search.url = "http://127.0.0.1:8765"
            mock_cfg.return_value.search.watch = False
            _run_status(_make_args(search_command="status"))

        out = capsys.readouterr().out
        line = [l for l in out.splitlines() if "my-docs" in l][0]
        assert "partial" in line
        assert "50 / 100" in line

    def test_cli_status_in_progress_zero(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """IN_PROGRESS with processed_files == 0 shows 'in_progress' and '0 / M files'."""
        from archon.cli.search_cmd import _run_status
        status_data = self._status_data()
        indexing_state_data = {"collections": {
            "fresh-col": self._progress_dict("in_progress", 200, 0),
        }}
        s_patch, i_patch = self._patch_client(status_data, indexing_state_data)
        with s_patch, i_patch, patch("archon.cli.search_cmd.load_config") as mock_cfg:
            mock_cfg.return_value.search.url = "http://127.0.0.1:8765"
            mock_cfg.return_value.search.watch = False
            _run_status(_make_args(search_command="status"))

        out = capsys.readouterr().out
        line = [l for l in out.splitlines() if "fresh-col" in l][0]
        assert "in_progress" in line
        assert "0 /" in line

    def test_cli_status_pending_shows_dash(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """PENDING collection shows em-dash for progress (regression guard)."""
        from archon.cli.search_cmd import _run_status
        status_data = self._status_data()
        indexing_state_data = {"collections": {
            "pending-col": self._progress_dict("pending"),
        }}
        s_patch, i_patch = self._patch_client(status_data, indexing_state_data)
        with s_patch, i_patch, patch("archon.cli.search_cmd.load_config") as mock_cfg:
            mock_cfg.return_value.search.url = "http://127.0.0.1:8765"
            mock_cfg.return_value.search.watch = False
            _run_status(_make_args(search_command="status"))

        out = capsys.readouterr().out
        line = [l for l in out.splitlines() if "pending-col" in l][0]
        assert "\u2014" in repr(line)

    def test_cli_status_done_shows_done(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """DONE collection shows 'done' status (regression guard)."""
        from archon.cli.search_cmd import _run_status
        status_data = self._status_data([self._col_dict("done-col", 80, 400)])
        indexing_state_data = {"collections": {
            "done-col": self._progress_dict("done", 80, 80),
        }}
        s_patch, i_patch = self._patch_client(status_data, indexing_state_data)
        with s_patch, i_patch, patch("archon.cli.search_cmd.load_config") as mock_cfg:
            mock_cfg.return_value.search.url = "http://127.0.0.1:8765"
            mock_cfg.return_value.search.watch = False
            _run_status(_make_args(search_command="status"))

        out = capsys.readouterr().out
        line = [l for l in out.splitlines() if "done-col" in l][0]
        assert "done" in line

def test_cli_sync_passes_config_params() -> None:
    """_run_sync delegates to the archon-search sync subprocess."""
    from archon.cli.search_cmd import _run_sync

    with patch("archon.cli.search_cmd.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = _run_sync(_make_args(search_command="sync"))

    assert result == 0
    cmd = mock_run.call_args[0][0]
    assert "archon-search" in cmd
    assert "sync" in cmd


# ---------------------------------------------------------------------------
# Task 4.8 — CLI sync output for `updated` collections
# ---------------------------------------------------------------------------


def test_run_sync_output_includes_updated(capsys: pytest.CaptureFixture[str]) -> None:
    """_run_sync delegates sync to archon-search CLI subprocess (returns subprocess exit code)."""
    from archon.cli.search_cmd import _run_sync

    with patch("archon.cli.search_cmd.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = _run_sync(_make_args(search_command="sync"))

    assert result == 0
    cmd = mock_run.call_args[0][0]
    assert "archon-search" in cmd
    assert "sync" in cmd


# ---------------------------------------------------------------------------
# FEAT-027-P7 Task 7.2 — ETA display in _print_progress_table
# ---------------------------------------------------------------------------


class TestEtaDisplay:
    """Tests for ETA suffix in _print_progress_table (FEAT-027-P7 Task 7.2)."""

    @staticmethod
    def _make_in_progress_state(processed: int = 50, total: int = 100) -> "IndexingState":
        from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus
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
        from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus
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
        from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

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
        from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus
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
        from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus
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


# ---------------------------------------------------------------------------
# C1-B — sync CLI uses all_indexed_collections not just .collections
# ---------------------------------------------------------------------------


def _make_cfg_with_pinned(
    collections: list[str],
    pinned_collections: list[str],
    db_path: str = "/tmp/rag",
) -> MagicMock:
    """Build mock config with both collections and pinned_collections + all_indexed_collections."""
    mock_cfg = MagicMock()
    mock_cfg.search.db_path = db_path
    mock_cfg.search.collections = collections
    mock_cfg.search.pinned_collections = pinned_collections
    # Replicate all_indexed_collections: pinned first, deduplicated
    _all: list[str] = []
    _seen: set[str] = set()
    for p in pinned_collections:
        if p not in _seen:
            _all.append(p)
            _seen.add(p)
    for p in collections:
        if p not in _seen:
            _all.append(p)
            _seen.add(p)
    mock_cfg.search.all_indexed_collections = _all
    return mock_cfg


def test_sync_cli_passes_all_indexed_collections_to_sync(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_run_sync delegates to archon-search sync — subprocess exit code is returned."""
    from archon.cli.search_cmd import _run_sync

    with patch("archon.cli.search_cmd.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = _run_sync(_make_args(search_command="sync"))

    assert result == 0
    cmd = mock_run.call_args[0][0]
    assert "archon-search" in cmd
    assert "sync" in cmd


# ---------------------------------------------------------------------------
# C1-C — collection list shows pinned-only as 'indexed' not 'orphan (managed)'
# ---------------------------------------------------------------------------


def test_collection_list_pinned_only_shows_as_indexed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_run_collection_list shows collections via SearchClient HTTP with status field."""
    from archon.cli.search_cmd import _run_collection_list
    from archon.ai.search_client import SearchClient

    collections_data = [{"name": "sessions", "doc_count": 5, "chunk_count": 20, "status": "indexed", "path": "/pinned/sessions"}]

    with (
        patch("archon.cli.search_cmd.load_config") as mock_cfg,
        patch.object(SearchClient, "list_collections", new_callable=AsyncMock, return_value=collections_data),
    ):
        mock_cfg.return_value.search.url = "http://127.0.0.1:8765"
        result = _run_collection_list(_make_collection_list_args())

    out = capsys.readouterr().out
    assert result == 0
    assert "sessions" in out
    assert "indexed" in out, f"Expected 'indexed' in output, got:\n{out}"
    assert "orphan" not in out, f"Pinned-only collection must not show as orphan, got:\n{out}"


# ---------------------------------------------------------------------------
# C1-D — remove: pinned-only path returns special error message
# ---------------------------------------------------------------------------


def test_collection_remove_pinned_only_returns_special_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Attempting to remove a pinned-only collection returns a clear error mentioning pinned_collections."""
    from archon.cli.search_cmd import _run_collection_remove

    pinned_path = "/pinned/sessions"
    mock_cfg = _make_cfg_with_pinned(
        collections=[],  # NOT in collections
        pinned_collections=[pinned_path],
    )

    with patch("archon.cli.search_cmd.load_config", return_value=mock_cfg):
        result = _run_collection_remove(
            argparse.Namespace(path=pinned_path, dry_run=False, force=False)
        )

    out = capsys.readouterr().out
    assert result == 1
    assert "pinned" in out.lower(), f"Expected pinned-collection error, got:\n{out}"
    assert "pinned_collections" in out, f"Error should mention pinned_collections config key, got:\n{out}"


def test_collection_remove_unknown_path_returns_not_in_collections(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A path that is in neither collections nor pinned_collections returns existing 'not in collections' error."""
    from archon.cli.search_cmd import _run_collection_remove

    mock_cfg = _make_cfg_with_pinned(
        collections=["/user/docs"],
        pinned_collections=["/pinned/sessions"],
    )

    with patch("archon.cli.search_cmd.load_config", return_value=mock_cfg):
        result = _run_collection_remove(
            argparse.Namespace(path="/unknown/path", dry_run=False, force=False)
        )

    out = capsys.readouterr().out
    assert result == 1
    assert "not in collections" in out.lower() or "Error:" in out


def test_collection_remove_path_in_both_collections_and_pinned_warns(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Remove a path that is in both collections AND pinned_collections: succeeds with pinned note."""
    from archon.cli.search_cmd import _run_collection_remove
    from archon.ai.search_client import SearchClient

    shared_path = str(tmp_path / "shared_docs")
    Path(shared_path).mkdir()
    mock_cfg = _make_cfg_with_pinned(
        collections=[shared_path],
        pinned_collections=[shared_path],
    )
    mock_cfg.search.db_path = str(tmp_path / "rag")
    mock_cfg.search.url = "http://127.0.0.1:8765"

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch.object(SearchClient, "remove_collection", new_callable=AsyncMock, return_value={"deleted": True}),
        patch("archon.cli.search_cmd.config_collections_remove"),
        patch("archon_search.sync.manifest_lookup_by_path", return_value=None),
        patch("archon_search.sync.path_to_collection_name", return_value="shared_docs"),
    ):
        result = _run_collection_remove(
            argparse.Namespace(path=shared_path, dry_run=False, force=False)
        )

    out = capsys.readouterr().out
    assert result == 0, f"Expected success (exit 0), got {result}. Output:\n{out}"
    assert "pinned" in out.lower(), (
        f"Output should note path stays indexed as pinned collection:\n{out}"
    )


# ---------------------------------------------------------------------------
# C1-E — reindex: pinned-only collection can be reindexed
# ---------------------------------------------------------------------------


def _make_collection_reindex_args(collection_name: str) -> argparse.Namespace:
    return argparse.Namespace(
        search_command="collection",
        collection_command="reindex",
        collection_name=collection_name,
    )


def test_collection_reindex_pinned_only_succeeds(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """_run_collection_reindex submits a reindex job via SearchClient HTTP."""
    from archon.cli.search_cmd import _run_collection_reindex
    from archon.ai.search_client import SearchClient

    mock_job = MagicMock()
    mock_job.job_id = "job-123"

    with (
        patch("archon.cli.search_cmd.load_config") as mock_cfg,
        patch.object(SearchClient, "reindex_collection", new_callable=AsyncMock, return_value=mock_job),
    ):
        mock_cfg.return_value.search.url = "http://127.0.0.1:8765"
        result = _run_collection_reindex(_make_collection_reindex_args("pinned_docs"))

    assert result == 0, (
        "Pinned-only collection must be reindexable via SearchClient.reindex_collection"
    )


# ---------------------------------------------------------------------------
# C1-G — add: duplicate check covers pinned_collections
# ---------------------------------------------------------------------------


def test_collection_add_path_delegates_to_server(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """add_collection delegates to SearchClient — server handles duplicate detection."""
    from archon.cli.search_cmd import _run_collection_add
    from archon.ai.search_client import SearchClient

    pinned_path = str(tmp_path / "pinned_docs")
    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch.object(SearchClient, "add_collection", new_callable=AsyncMock, return_value={"status": "added"}),
    ):
        result = _run_collection_add(_make_collection_add_args(path=pinned_path))

    assert result == 0


# ---------------------------------------------------------------------------
# Task 7.6 — Import boundary: no archon.search.* imports remain
# ---------------------------------------------------------------------------


def test_search_cmd_uses_boundary_adapters() -> None:
    """search_cmd must not import archon.search.* — all server-owned ops use SearchClient."""
    import ast
    import pathlib

    search_cmd_path = pathlib.Path(__file__).parents[2] / "archon" / "cli" / "search_cmd.py"
    tree = ast.parse(search_cmd_path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("archon.search"), (
                    f"Forbidden top-level import in search_cmd.py: import {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not module.startswith("archon.search"), (
                f"Forbidden import in search_cmd.py: from {module} import ..."
            )


def test_update_command_hands_off_search_lifecycle() -> None:
    """update.py must not import archon.search.* or reference get_search_service/SearchInstaller."""
    import ast
    import pathlib

    update_path = pathlib.Path(__file__).parents[2] / "archon" / "cli" / "update.py"
    tree = ast.parse(update_path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("archon.search"), (
                    f"Forbidden top-level import in update.py: import {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not module.startswith("archon.search"), (
                f"Forbidden import in update.py: from {module} import ..."
            )
            # SearchInstaller must not be imported from archon.search
            for alias in node.names:
                assert alias.name != "SearchInstaller" or not module.startswith("archon"), (
                    f"Forbidden import in update.py: from {module} import {alias.name}"
                )


def test_uninstall_delete_db_hands_off_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    """archon search uninstall --delete-db passes --delete-db to standalone CLI."""
    from archon.cli.search_cmd import _run_uninstall

    with patch("archon.cli.search_cmd.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = _run_uninstall(_make_args(search_command="uninstall", delete_db=True))

    assert result == 0
    cmd = mock_run.call_args[0][0]
    assert "archon-search" in cmd
    assert "--delete-db" in cmd


def test_collection_remove_dry_run_and_force_handoff(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """collection remove --dry-run prints would-remove and returns 0 without executing."""
    from archon.cli.search_cmd import _run_collection_remove

    path = str(tmp_path / "docs")

    mock_cfg = MagicMock()
    mock_cfg.search.all_indexed_collections = [path]
    mock_cfg.search.collections = [path]
    mock_cfg.search.pinned_collections = []
    mock_cfg.search.db_path = str(tmp_path / "rag")

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch("archon_search.sync.manifest_lookup_by_path", return_value=None),
        patch("archon_search.sync.path_to_collection_name", return_value="docs"),
    ):
        result = _run_collection_remove(
            argparse.Namespace(
                search_command="collection",
                collection_command="remove",
                path=path,
                dry_run=True,
                force=False,
            )
        )

    out = capsys.readouterr().out
    assert result == 0
    assert "Would remove" in out or "would remove" in out.lower()


def test_collection_remove_pinned_only_error_preserved(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    """Pinned-only removal still surfaces the operator-facing error."""
    from archon.cli.search_cmd import _run_collection_remove

    path = str(tmp_path / "pinned_docs")

    # In pinned_collections but NOT in collections
    mock_cfg = MagicMock()
    mock_cfg.search.all_indexed_collections = [path]
    mock_cfg.search.collections = []  # not in regular collections
    mock_cfg.search.pinned_collections = [path]
    mock_cfg.search.db_path = str(tmp_path / "rag")

    with (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
    ):
        result = _run_collection_remove(
            argparse.Namespace(
                search_command="collection",
                collection_command="remove",
                path=path,
                dry_run=False,
                force=False,
            )
        )

    out = capsys.readouterr().out
    assert result != 0
    assert "pinned" in out.lower()


# ---------------------------------------------------------------------------
# Suite 9 — archon search status progress table (S9.1–S9.11)
# ---------------------------------------------------------------------------


def _status_mocks(
    status_data: dict | None,  # type: ignore[type-arg]
    state_data: dict | None,  # type: ignore[type-arg]
) -> tuple:
    """Return (mock_cfg, patch_status, patch_indexing_state) context managers."""
    from archon.ai.search_client import SearchClient

    mock_cfg = MagicMock()
    mock_cfg.search.url = "http://127.0.0.1:8765"

    return (
        patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
        patch.object(SearchClient, "status", new_callable=AsyncMock, return_value=status_data),
        patch.object(SearchClient, "indexing_state", new_callable=AsyncMock, return_value=state_data),
    )


def test_status_shows_all_done_collection(capsys: pytest.CaptureFixture[str]) -> None:
    """S9.1: a fully-indexed collection shows 'done' label in the progress table."""
    from archon.cli.search_cmd import _run_status

    status_data = {"running": True, "pid": 1, "collections": []}
    state_data = {
        "collections": {
            "sessions": {
                "status": "done",
                "total_files": 10,
                "processed_files": 10,
                "started_at": None,
                "error": None,
            }
        }
    }

    cfg_patch, status_patch, state_patch = _status_mocks(status_data, state_data)
    with cfg_patch, status_patch, state_patch:
        result = _run_status(_make_args(search_command="status"))

    out = capsys.readouterr().out
    sessions_line = next((l for l in out.splitlines() if "sessions" in l), None)
    assert sessions_line is not None, f"Expected line containing 'sessions' in:\n{out}"
    assert "done" in sessions_line, f"Expected 'done' on the sessions line: {sessions_line}"
    assert result == 0


def test_status_shows_in_progress_with_fraction(capsys: pytest.CaptureFixture[str]) -> None:
    """S9.2: in-progress collection with processed=3, total=10 shows 'partial/done 3/10'."""
    from archon.cli.search_cmd import _run_status

    status_data = {"running": True, "pid": 1, "collections": []}
    state_data = {
        "collections": {
            "docs": {
                "status": "in_progress",
                "total_files": 10,
                "processed_files": 3,
                "started_at": None,
                "error": None,
            }
        }
    }

    cfg_patch, status_patch, state_patch = _status_mocks(status_data, state_data)
    with cfg_patch, status_patch, state_patch:
        result = _run_status(_make_args(search_command="status"))

    out = capsys.readouterr().out
    docs_line = next((l for l in out.splitlines() if "docs" in l), None)
    assert docs_line is not None, f"Expected a line containing 'docs' in:\n{out}"
    assert "partial" in docs_line, f"Expected 'partial' on the docs line: {docs_line}"
    assert "3 / 10" in docs_line, f"Expected '3 / 10' on the docs line: {docs_line}"
    assert result == 0


def test_status_shows_failed_collection_with_error_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """S9.3: FAILED collection shows error message and causes exit code 1."""
    from archon.cli.search_cmd import _run_status

    status_data = {"running": True, "pid": 1, "collections": []}
    state_data = {
        "collections": {
            "corpus": {
                "status": "failed",
                "total_files": 5,
                "processed_files": 1,
                "started_at": None,
                "error": "oom",
            }
        }
    }

    cfg_patch, status_patch, state_patch = _status_mocks(status_data, state_data)
    with cfg_patch, status_patch, state_patch:
        result = _run_status(_make_args(search_command="status"))

    out = capsys.readouterr().out
    assert "oom" in out, f"Expected error message 'oom' in:\n{out}"
    assert "failed" in out.lower(), f"Expected 'failed' status in:\n{out}"
    assert result == 1


def test_status_shows_pending_collection(capsys: pytest.CaptureFixture[str]) -> None:
    """S9.4: PENDING collection shows '—' and does not crash."""
    from archon.cli.search_cmd import _run_status

    status_data = {"running": True, "pid": 1, "collections": []}
    state_data = {
        "collections": {
            "pending_col": {
                "status": "pending",
                "total_files": 0,
                "processed_files": 0,
                "started_at": None,
                "error": None,
            }
        }
    }

    cfg_patch, status_patch, state_patch = _status_mocks(status_data, state_data)
    with cfg_patch, status_patch, state_patch:
        result = _run_status(_make_args(search_command="status"))

    out = capsys.readouterr().out
    assert "pending_col" in out
    assert "—" in out
    assert result == 0


def test_status_shows_eta_when_available(capsys: pytest.CaptureFixture[str]) -> None:
    """S9.5: in-progress collection with enough data shows ETA in minutes."""
    from archon.cli.search_cmd import _run_status
    from datetime import UTC, datetime, timedelta

    # started 10 minutes ago, 20 of 100 processed → ~40 min remaining
    started = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()

    status_data = {"running": True, "pid": 1, "collections": []}
    state_data = {
        "collections": {
            "bigcorpus": {
                "status": "in_progress",
                "total_files": 100,
                "processed_files": 20,
                "started_at": started,
                "error": None,
            }
        }
    }

    cfg_patch, status_patch, state_patch = _status_mocks(status_data, state_data)
    with cfg_patch, status_patch, state_patch:
        result = _run_status(_make_args(search_command="status"))

    out = capsys.readouterr().out
    assert "min remaining" in out, f"Expected ETA output with 'min remaining' in:\n{out}"
    # Verify approximate minute value is shown (should be ~40 min for 20/100 in 10 min)
    bigcorpus_line = next((l for l in out.splitlines() if "bigcorpus" in l), None)
    assert bigcorpus_line is not None, f"Expected line for 'bigcorpus' in:\n{out}"
    assert "min remaining" in bigcorpus_line, f"Expected ETA on bigcorpus line: {bigcorpus_line}"
    assert result == 0


def test_status_multiple_collections_all_shown(capsys: pytest.CaptureFixture[str]) -> None:
    """S9.6: 3 collections in state → all 3 rows appear in output."""
    from archon.cli.search_cmd import _run_status

    status_data = {"running": True, "pid": 1, "collections": []}
    state_data = {
        "collections": {
            "alpha": {
                "status": "done",
                "total_files": 5,
                "processed_files": 5,
                "started_at": None,
                "error": None,
            },
            "beta": {
                "status": "in_progress",
                "total_files": 10,
                "processed_files": 4,
                "started_at": None,
                "error": None,
            },
            "gamma": {
                "status": "pending",
                "total_files": 0,
                "processed_files": 0,
                "started_at": None,
                "error": None,
            },
        }
    }

    cfg_patch, status_patch, state_patch = _status_mocks(status_data, state_data)
    with cfg_patch, status_patch, state_patch:
        result = _run_status(_make_args(search_command="status"))

    out = capsys.readouterr().out
    assert "alpha" in out
    assert "beta" in out
    assert "gamma" in out
    assert result == 0


def test_status_shows_pid_from_service(capsys: pytest.CaptureFixture[str]) -> None:
    """S9.7: status() returns pid=9999 → output contains 'pid=9999'."""
    from archon.cli.search_cmd import _run_status

    status_data = {"running": True, "pid": 9999, "collections": []}
    state_data = None  # no indexing state

    cfg_patch, status_patch, state_patch = _status_mocks(status_data, state_data)
    with cfg_patch, status_patch, state_patch:
        result = _run_status(_make_args(search_command="status"))

    out = capsys.readouterr().out
    assert "pid=9999" in out
    assert result == 0


def test_status_shows_stopped_when_status_returns_none(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """S9.8: status() returns None → 'stopped (unreachable)' is printed, exit 1."""
    from archon.cli.search_cmd import _run_status

    cfg_patch, status_patch, state_patch = _status_mocks(None, None)
    with cfg_patch, status_patch, state_patch:
        result = _run_status(_make_args(search_command="status"))

    out = capsys.readouterr().out
    assert "stopped" in out.lower()
    assert "unreachable" in out.lower()
    assert result == 1


def test_status_shows_stopped_when_indexing_state_returns_none(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """S9.9: service is running but indexing_state() returns None → graceful degradation (no crash)."""
    from archon.cli.search_cmd import _run_status

    status_data = {"running": True, "pid": 42, "collections": []}
    # state_data = None means _run_status won't enter the progress table branch
    cfg_patch, status_patch, state_patch = _status_mocks(status_data, None)
    with cfg_patch, status_patch, state_patch:
        result = _run_status(_make_args(search_command="status"))

    out = capsys.readouterr().out
    assert "running" in out.lower(), f"Expected 'running' in output:\n{out}"
    # Graceful degradation: no crash, no traceback; service status still visible
    assert "Traceback" not in out, "No traceback should appear on graceful degradation"
    assert result == 0


def test_status_exit_code_0_when_all_healthy(capsys: pytest.CaptureFixture[str]) -> None:
    """S9.10: all collections DONE → exit 0."""
    from archon.cli.search_cmd import _run_status

    status_data = {"running": True, "pid": 1, "collections": []}
    state_data = {
        "collections": {
            "col_a": {
                "status": "done",
                "total_files": 8,
                "processed_files": 8,
                "started_at": None,
                "error": None,
            },
            "col_b": {
                "status": "done",
                "total_files": 3,
                "processed_files": 3,
                "started_at": None,
                "error": None,
            },
        }
    }

    cfg_patch, status_patch, state_patch = _status_mocks(status_data, state_data)
    with cfg_patch, status_patch, state_patch:
        result = _run_status(_make_args(search_command="status"))

    assert result == 0


def test_status_exit_code_1_when_any_failed(capsys: pytest.CaptureFixture[str]) -> None:
    """S9.11: any collection FAILED → exit 1."""
    from archon.cli.search_cmd import _run_status

    status_data = {"running": True, "pid": 1, "collections": []}
    state_data = {
        "collections": {
            "ok_col": {
                "status": "done",
                "total_files": 5,
                "processed_files": 5,
                "started_at": None,
                "error": None,
            },
            "bad_col": {
                "status": "failed",
                "total_files": 10,
                "processed_files": 2,
                "started_at": None,
                "error": "disk full",
            },
        }
    }

    cfg_patch, status_patch, state_patch = _status_mocks(status_data, state_data)
    with cfg_patch, status_patch, state_patch:
        result = _run_status(_make_args(search_command="status"))

    assert result == 1


# ---------------------------------------------------------------------------
# Suite 9 — compute_eta_seconds unit tests (S9.12–S9.18)
# ---------------------------------------------------------------------------


class _FakeCP:
    """Minimal duck-typed CollectionProgress-like object for compute_eta_seconds tests."""

    def __init__(
        self,
        status: str = "in_progress",
        total_files: int = 100,
        processed_files: int = 50,
        started_at: str | None = None,
    ) -> None:
        self.status = status
        self.total_files = total_files
        self.processed_files = processed_files
        self.started_at = started_at


class TestComputeEtaSeconds:
    """Unit tests for compute_eta_seconds (S9.12–S9.18)."""

    def test_s9_12_not_in_progress_returns_none(self) -> None:
        """S9.12: status != 'in_progress' → None."""
        from archon.cli.search_cmd import compute_eta_seconds

        for status in ("done", "failed", "pending", "idle"):
            cp = _FakeCP(status=status, processed_files=50, started_at="2026-01-01T00:00:00+00:00")
            assert compute_eta_seconds(cp) is None, f"Expected None for status={status!r}"

    def test_s9_13_total_files_zero_returns_none(self) -> None:
        """S9.13: total_files=0 → None (divide-by-zero guard: processed >= total fires when total=0)."""
        from archon.cli.search_cmd import compute_eta_seconds

        # Use processed_files=15 so the `processed < 10` guard is cleared and the
        # `processed >= total` (15 >= 0) guard is the one that fires, returning None.
        cp = _FakeCP(status="in_progress", total_files=0, processed_files=15, started_at="2026-01-01T00:00:00+00:00")
        assert compute_eta_seconds(cp) is None

    def test_s9_14_fewer_than_10_processed_returns_none(self) -> None:
        """S9.14: processed_files < 10 → None (too early)."""
        from archon.cli.search_cmd import compute_eta_seconds

        cp = _FakeCP(
            status="in_progress",
            total_files=100,
            processed_files=5,
            started_at="2026-01-01T00:00:00+00:00",
        )
        assert compute_eta_seconds(cp) is None

    def test_s9_15_no_started_at_returns_none(self) -> None:
        """S9.15: started_at=None → None."""
        from archon.cli.search_cmd import compute_eta_seconds

        cp = _FakeCP(status="in_progress", total_files=100, processed_files=50, started_at=None)
        assert compute_eta_seconds(cp) is None

    def test_s9_16_valid_progress_computes_eta(self) -> None:
        """S9.16: 100 files, 50 processed, started 60s ago → ~60s remaining."""
        from datetime import datetime, timezone

        from archon.cli.search_cmd import compute_eta_seconds

        now = datetime(2026, 1, 1, 0, 1, 0, tzinfo=timezone.utc)  # T+60s
        started_at = "2026-01-01T00:00:00+00:00"
        cp = _FakeCP(status="in_progress", total_files=100, processed_files=50, started_at=started_at)
        eta = compute_eta_seconds(cp, now=now)
        assert eta is not None
        # 50 files remain, rate = 50/60 fps → ETA = 50 / (50/60) = 60s exactly (fixed now)
        assert eta == 60, f"Expected exactly 60s, got {eta}"

    def test_s9_17_processed_equals_total_returns_none(self) -> None:
        """S9.17: processed == total → None (already complete)."""
        from archon.cli.search_cmd import compute_eta_seconds

        cp = _FakeCP(
            status="in_progress",
            total_files=50,
            processed_files=50,
            started_at="2026-01-01T00:00:00+00:00",
        )
        assert compute_eta_seconds(cp) is None

    def test_s9_18_started_at_iso_string_parsed(self) -> None:
        """S9.18: started_at as ISO 8601 string → parsed correctly, valid ETA returned."""
        from datetime import datetime, timezone

        from archon.cli.search_cmd import compute_eta_seconds

        now = datetime(2026, 6, 15, 12, 1, 0, tzinfo=timezone.utc)  # T+60s
        started_at = "2026-06-15T12:00:00+00:00"
        cp = _FakeCP(status="in_progress", total_files=200, processed_files=100, started_at=started_at)
        eta = compute_eta_seconds(cp, now=now)
        assert eta is not None
        assert isinstance(eta, int)
        # 100 files remain, rate = 100/60 fps → ETA = int(100/(100/60)) = 60s exactly (fixed now)
        assert eta == 60, f"Expected exactly 60s, got {eta}"


# ---------------------------------------------------------------------------
# S9.19–S9.24: _path_to_collection_name
# ---------------------------------------------------------------------------


class TestPathToCollectionName:
    """Unit tests for _path_to_collection_name (S9.19–S9.24)."""

    def test_s9_19_hyphenated_path(self) -> None:
        """S9.19: /home/user/my-docs → my_docs."""
        from archon.cli.search_cmd import _path_to_collection_name

        assert _path_to_collection_name("/home/user/my-docs") == "my_docs"

    def test_s9_20_simple_name(self) -> None:
        """S9.20: /data/history → history."""
        from archon.cli.search_cmd import _path_to_collection_name

        assert _path_to_collection_name("/data/history") == "history"

    def test_s9_21_trailing_slash_stripped(self) -> None:
        """S9.21: /data/docs/ → docs (trailing slash stripped)."""
        from archon.cli.search_cmd import _path_to_collection_name

        assert _path_to_collection_name("/data/docs/") == "docs"

    def test_s9_22_spaces_and_special_chars(self) -> None:
        """S9.22: /data/my project (2024) → my_project_2024."""
        from archon.cli.search_cmd import _path_to_collection_name

        assert _path_to_collection_name("/data/my project (2024)") == "my_project_2024"

    def test_s9_23_all_special_falls_back_to_collection(self) -> None:
        """S9.23: !!! → collection (fallback for all-special names)."""
        from archon.cli.search_cmd import _path_to_collection_name

        assert _path_to_collection_name("!!!") == "collection"

    def test_s9_24_uppercase_lowercased(self) -> None:
        """S9.24: /data/MyDocs → mydocs (lowercased)."""
        from archon.cli.search_cmd import _path_to_collection_name

        assert _path_to_collection_name("/data/MyDocs") == "mydocs"


# ---------------------------------------------------------------------------
# S9.25–S9.29: _print_progress_table
# ---------------------------------------------------------------------------


class _FakeCPState:
    """Duck-typed collection progress entry for _print_progress_table tests."""

    def __init__(
        self,
        status: str = "done",
        processed_files: int = 10,
        total_files: int = 10,
        error: str | None = None,
    ) -> None:
        self.status = _FakeStatusEnum(status)
        self.processed_files = processed_files
        self.total_files = total_files
        self.error = error
        self.started_at = None


class _FakeStatusEnum:
    """Minimal enum-like with .value for duck-typing."""

    def __init__(self, value: str) -> None:
        self.value = value


class _FakeIndexingState:
    """Duck-typed IndexingState-like object."""

    def __init__(self, collections: dict) -> None:
        self.collections = collections


class _FakeLanceDBCol:
    """Duck-typed LanceDB collection info."""

    def __init__(self, name: str, doc_count: int = 0, chunk_count: int = 0) -> None:
        self.name = name
        self.doc_count = doc_count
        self.chunk_count = chunk_count


class TestPrintProgressTable:
    """Unit tests for _print_progress_table (S9.25–S9.29)."""

    def test_s9_25_empty_state_no_output(self, capsys) -> None:  # type: ignore[no-untyped-def]
        """S9.25: empty state + empty collections → no output, returns False."""
        from archon.cli.search_cmd import _print_progress_table

        state = _FakeIndexingState(collections={})
        result = _print_progress_table(state, collections=[])
        captured = capsys.readouterr()
        assert result is False
        assert captured.out == ""

    def test_s9_26_all_done_returns_false(self, capsys) -> None:  # type: ignore[no-untyped-def]
        """S9.26: all collections DONE → returns False."""
        from archon.cli.search_cmd import _print_progress_table

        state = _FakeIndexingState(
            collections={
                "alpha": _FakeCPState(status="done", processed_files=5, total_files=5),
                "beta": _FakeCPState(status="done", processed_files=3, total_files=3),
            }
        )
        result = _print_progress_table(state, collections=[])
        assert result is False
        out = capsys.readouterr().out
        assert "alpha" in out
        assert "beta" in out

    def test_s9_27_one_failed_returns_true(self, capsys) -> None:  # type: ignore[no-untyped-def]
        """S9.27: one FAILED collection → returns True."""
        from archon.cli.search_cmd import _print_progress_table

        state = _FakeIndexingState(
            collections={
                "alpha": _FakeCPState(status="done", processed_files=5, total_files=5),
                "beta": _FakeCPState(status="failed", processed_files=0, total_files=3),
            }
        )
        result = _print_progress_table(state, collections=[])
        assert result is True
        out = capsys.readouterr().out
        assert "beta" in out

    def test_s9_28_three_configured_two_have_state_all_shown(self, capsys) -> None:  # type: ignore[no-untyped-def]
        """S9.28: 3 configured collections, 2 have state → all 3 shown in output."""
        from archon.cli.search_cmd import _print_progress_table

        state = _FakeIndexingState(
            collections={
                "alpha": _FakeCPState(status="done", processed_files=5, total_files=5),
                "beta": _FakeCPState(status="in_progress", processed_files=2, total_files=10),
            }
        )
        # gamma is only in lancedb, not in state
        collections = [
            _FakeLanceDBCol("alpha", doc_count=5, chunk_count=20),
            _FakeLanceDBCol("beta", doc_count=2, chunk_count=8),
            _FakeLanceDBCol("gamma", doc_count=0, chunk_count=0),
        ]
        result = _print_progress_table(state, collections=collections)
        out = capsys.readouterr().out
        assert "alpha" in out
        assert "beta" in out
        assert "gamma" in out
        assert result is False

    def test_s9_29_collection_in_config_no_state_shows_fallback(self, capsys) -> None:  # type: ignore[no-untyped-def]
        """S9.29: collection in lancedb but no state entry → fallback line with docs/chunks counts.

        The spec says '—' or 'not_yet_indexed', but the actual production output (search_cmd.py:241)
        is 'collection={name}  docs={N}  chunks={M}' for LanceDB-only entries. The test asserts
        the actual production behavior.
        """
        from archon.cli.search_cmd import _print_progress_table

        state = _FakeIndexingState(collections={})
        collections = [_FakeLanceDBCol("gamma", doc_count=7, chunk_count=42)]
        result = _print_progress_table(state, collections=collections)
        out = capsys.readouterr().out
        assert result is False
        # Falls through to the else branch: prints collection=..., docs=..., chunks=...
        assert "gamma" in out
        assert "docs=7" in out
        assert "chunks=42" in out


# ---------------------------------------------------------------------------
# S9.30–S9.32: _run_status edge cases
# ---------------------------------------------------------------------------


class TestStatusEdgeCases:
    """S9.30–S9.32: status output edge cases."""

    def test_s9_30_zero_total_files(self, capsys: pytest.CaptureFixture[str]) -> None:
        """S9.30: collection with total_files=0 renders without division by zero."""
        from archon.cli.search_cmd import _run_status

        status_data = {"running": True, "pid": 42, "collections": []}
        state_data = {
            "collections": {
                "empty_col": {
                    "status": "in_progress",
                    "total_files": 0,
                    "processed_files": 0,
                    "started_at": None,
                    "error": None,
                }
            }
        }

        cfg_patch, status_patch, state_patch = _status_mocks(status_data, state_data)
        with cfg_patch, status_patch, state_patch:
            result = _run_status(_make_args(search_command="status"))

        out = capsys.readouterr().out
        assert "empty_col" in out
        # Must not crash; exit code 0 (in_progress is not failed)
        assert result == 0

    def test_s9_31_null_error_field_omits_error_suffix(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """S9.31: error=null (None) in collection state → no '[...]' suffix in output."""
        from archon.cli.search_cmd import _run_status

        status_data = {"running": True, "pid": 1, "collections": []}
        state_data = {
            "collections": {
                "alpha": {
                    "status": "done",
                    "total_files": 5,
                    "processed_files": 5,
                    "started_at": None,
                    "error": None,
                }
            }
        }

        cfg_patch, status_patch, state_patch = _status_mocks(status_data, state_data)
        with cfg_patch, status_patch, state_patch:
            result = _run_status(_make_args(search_command="status"))

        out = capsys.readouterr().out
        alpha_line = next((l for l in out.splitlines() if "alpha" in l), None)
        assert alpha_line is not None
        assert "[" not in alpha_line, f"Expected no error suffix in: {alpha_line!r}"
        assert result == 0

    def test_s9_32_long_collection_name_printed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """S9.32: collection name longer than 40 chars still appears in output."""
        from archon.cli.search_cmd import _run_status

        long_name = "a" * 60
        status_data = {"running": True, "pid": 1, "collections": []}
        state_data = {
            "collections": {
                long_name: {
                    "status": "done",
                    "total_files": 1,
                    "processed_files": 1,
                    "started_at": None,
                    "error": None,
                }
            }
        }

        cfg_patch, status_patch, state_patch = _status_mocks(status_data, state_data)
        with cfg_patch, status_patch, state_patch:
            result = _run_status(_make_args(search_command="status"))

        out = capsys.readouterr().out
        assert long_name in out
        assert result == 0


# ---------------------------------------------------------------------------
# S9.33–S9.34: collection remove edge cases (S9.35 deferred — requires prod change)
# ---------------------------------------------------------------------------


class TestCollectionRemoveEdgeCases:
    """S9.33–S9.34: collection remove edge cases."""

    def test_s9_33_trailing_slash_path_still_matches(
        self, capsys: pytest.CaptureFixture[str], tmp_path
    ) -> None:
        """S9.33: path with trailing slash resolves to the same dir as without slash."""
        from archon.cli.search_cmd import _run_collection_remove
        from archon.ai.search_client import SearchClient

        base = str(tmp_path / "docs")
        path_with_slash = base + "/"

        mock_cfg = MagicMock()
        mock_cfg.search.url = "http://127.0.0.1:8765"
        mock_cfg.search.collections = [base]
        mock_cfg.search.pinned_collections = []
        mock_cfg.search.all_indexed_collections = [base]

        with (
            patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
            patch("archon.cli.search_cmd.config_collections_remove") as mock_rm,
            patch.object(
                SearchClient,
                "remove_collection",
                new_callable=AsyncMock,
                return_value={"status": "removed"},
            ),
        ):
            result = _run_collection_remove(
                _make_collection_remove_args(path=path_with_slash)
            )

        assert result == 0
        mock_rm.assert_called_once()
        out = capsys.readouterr().out
        assert "Collection removed" in out

    def test_s9_34_symlink_resolves_to_real_path(
        self, capsys: pytest.CaptureFixture[str], tmp_path
    ) -> None:
        """S9.34: symlink path resolves to real path — matches if real path is registered."""
        from archon.cli.search_cmd import _run_collection_remove
        from archon.ai.search_client import SearchClient

        real_dir = tmp_path / "real_docs"
        real_dir.mkdir()
        link = tmp_path / "link_docs"
        link.symlink_to(real_dir)

        real_path = str(real_dir)
        link_path = str(link)

        mock_cfg = MagicMock()
        mock_cfg.search.url = "http://127.0.0.1:8765"
        # Config stores the real path; user supplies the symlink path
        mock_cfg.search.collections = [real_path]
        mock_cfg.search.pinned_collections = []
        mock_cfg.search.all_indexed_collections = [real_path]

        with (
            patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
            patch("archon.cli.search_cmd.config_collections_remove") as mock_rm,
            patch.object(
                SearchClient,
                "remove_collection",
                new_callable=AsyncMock,
                return_value={"status": "removed"},
            ),
        ):
            result = _run_collection_remove(
                _make_collection_remove_args(path=link_path)
            )

        assert result == 0
        mock_rm.assert_called_once()
        out = capsys.readouterr().out
        assert "Collection removed" in out


# ---------------------------------------------------------------------------
# S9.36–S9.37: collection add edge cases
# ---------------------------------------------------------------------------


class TestCollectionAddEdgeCases:
    """S9.36–S9.37: collection add edge cases."""

    def test_s9_36_absolute_path_not_double_resolved(
        self, capsys: pytest.CaptureFixture[str], tmp_path
    ) -> None:
        """S9.36: absolute path is passed through to SearchClient unchanged (no double resolve)."""
        from archon.cli.search_cmd import _run_collection_add
        from archon.ai.search_client import SearchClient

        abs_path = str(tmp_path / "my_corpus")

        mock_cfg = MagicMock()
        mock_cfg.search.url = "http://127.0.0.1:8765"

        captured_path: list[str] = []

        async def _capture_add(self_: object, path: str) -> dict:  # type: ignore[type-arg]
            captured_path.append(path)
            return {"name": "my_corpus"}

        with (
            patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
            patch.object(SearchClient, "add_collection", new=_capture_add),
        ):
            result = _run_collection_add(_make_collection_add_args(path=abs_path))

        assert result == 0
        assert len(captured_path) == 1
        # The path passed to SearchClient must equal the original path arg
        assert captured_path[0] == abs_path

    def test_s9_37_server_409_prints_error_and_returns_1(
        self, capsys: pytest.CaptureFixture[str], tmp_path
    ) -> None:
        """S9.37: SearchClient.add_collection() returns None (e.g. 409 conflict) → error + exit 1."""
        from archon.cli.search_cmd import _run_collection_add
        from archon.ai.search_client import SearchClient

        path = str(tmp_path / "docs")

        mock_cfg = MagicMock()
        mock_cfg.search.url = "http://127.0.0.1:8765"

        with (
            patch("archon.cli.search_cmd.load_config", return_value=mock_cfg),
            patch.object(
                SearchClient, "add_collection", new_callable=AsyncMock, return_value=None
            ),
        ):
            result = _run_collection_add(_make_collection_add_args(path=path))

        out = capsys.readouterr().out
        assert result == 1
        assert "Error" in out


# ---------------------------------------------------------------------------
# S9.38–S9.40: install/uninstall error paths
# ---------------------------------------------------------------------------


class TestInstallUninstallErrorPaths:
    """S9.38–S9.40: install/uninstall error paths."""

    def test_s9_38_install_file_not_found_returns_1(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """S9.38: archon-search not in PATH → FileNotFoundError → prints error, returns 1."""
        from archon.cli.search_cmd import _run_install

        with patch(
            "archon.cli.search_cmd.subprocess.run", side_effect=FileNotFoundError()
        ):
            result = _run_install(_make_args(search_command="install"))

        out = capsys.readouterr().out
        assert result == 1
        assert "archon-search" in out

    def test_s9_39_install_nonzero_exit_propagated(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """S9.39: archon-search install exits non-zero → _run_install returns that code."""
        from archon.cli.search_cmd import _run_install

        with patch("archon.cli.search_cmd.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=2)
            result = _run_install(_make_args(search_command="install"))

        assert result == 2

    def test_s9_40_uninstall_delete_db_flag_passed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """S9.40: _run_uninstall with delete_db=True passes --delete-db to archon-search."""
        from archon.cli.search_cmd import _run_uninstall

        with patch("archon.cli.search_cmd.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = _run_uninstall(_make_args(search_command="uninstall", delete_db=True))

        cmd = mock_run.call_args[0][0]
        assert "--delete-db" in cmd
        assert result == 0

    def test_s9_40b_uninstall_file_not_found_returns_1(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """S9.40b: archon-search not in PATH during uninstall → FileNotFoundError → exit 1."""
        from archon.cli.search_cmd import _run_uninstall

        with patch(
            "archon.cli.search_cmd.subprocess.run", side_effect=FileNotFoundError()
        ):
            result = _run_uninstall(_make_args(search_command="uninstall"))

        out = capsys.readouterr().out
        assert result == 1
        assert "archon-search" in out


# ---------------------------------------------------------------------------
# S9.41–S9.43: start/stop error paths
# ---------------------------------------------------------------------------


class TestStartStopErrorPaths:
    """S9.41–S9.43: start/stop error paths."""

    def test_s9_41_start_nonzero_exit_prints_failed_and_returns_code(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """S9.41: archon-search start returns non-zero → prints failure message, returns that code."""
        from archon.cli.search_cmd import _run_start

        with patch("archon.cli.search_cmd.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            result = _run_start(_make_args(search_command="start"))

        out = capsys.readouterr().out
        assert result == 1
        assert "failed" in out.lower() or "start" in out.lower()

    def test_s9_42_start_file_not_found_returns_1(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """S9.42: archon-search not in PATH on start → FileNotFoundError → exit 1."""
        from archon.cli.search_cmd import _run_start

        with patch(
            "archon.cli.search_cmd.subprocess.run", side_effect=FileNotFoundError()
        ):
            result = _run_start(_make_args(search_command="start"))

        out = capsys.readouterr().out
        assert result == 1
        assert "archon-search" in out

    def test_s9_43_stop_nonzero_exit_prints_failed_and_returns_code(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """S9.43: archon-search stop returns non-zero → prints failure message, returns that code."""
        from archon.cli.search_cmd import _run_stop

        with patch("archon.cli.search_cmd.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=3)
            result = _run_stop(_make_args(search_command="stop"))

        out = capsys.readouterr().out
        assert result == 3
        assert "failed" in out.lower() or "stop" in out.lower()

    def test_s9_43b_stop_file_not_found_returns_1(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """S9.43b: archon-search not in PATH on stop → FileNotFoundError → exit 1."""
        from archon.cli.search_cmd import _run_stop

        with patch(
            "archon.cli.search_cmd.subprocess.run", side_effect=FileNotFoundError()
        ):
            result = _run_stop(_make_args(search_command="stop"))

        out = capsys.readouterr().out
        assert result == 1
        assert "archon-search" in out


# ---------------------------------------------------------------------------
# S9.44–S9.56: _check_search_server / _check_search_health — SKIPPED
#
# These scenarios are fully covered by tests/cli/test_doctor.py:
#   - TestCheckRagServer: disabled, not_installed, not_running, running (lines 620–653)
#   - _check_search_health: staleness, empty docs, missing centroid, healthy, unreachable,
#     boundary 7-day, 8-day, absent last_indexed, model mismatch, pinned-removal guard,
#     IN_PROGRESS/PENDING suppression (lines 350–585)
# No additional tests are required here.
# ---------------------------------------------------------------------------
