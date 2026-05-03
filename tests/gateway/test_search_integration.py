"""tests/gateway/test_rag_integration.py — TDD tests for FEAT-021 Task 2.2.

Verifies that history_collection is derived from history.directory
rather than read from rag config.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# test_history_collection_derived_from_history_dir
# ---------------------------------------------------------------------------


def test_history_collection_derived_from_history_dir() -> None:
    """history_col is derived via path_to_collection_name from history.directory/sessions."""
    from archon_search.sync import path_to_collection_name

    history_dir = "/home/user/.archon/history"
    sessions_path = str(Path(history_dir).expanduser() / "sessions")
    col = path_to_collection_name(sessions_path)

    # The last component is "sessions" → sanitized → "sessions"
    assert col == "sessions"


def test_history_collection_derived_uses_last_component() -> None:
    """path_to_collection_name uses the last path component (basename)."""
    from archon_search.sync import path_to_collection_name

    # Different base dirs, same last component → same collection name
    col1 = path_to_collection_name("/alpha/sessions")
    col2 = path_to_collection_name("/beta/sessions")
    assert col1 == "sessions"
    assert col2 == "sessions"


# ---------------------------------------------------------------------------
# test_server_uses_derived_history_collection
# ---------------------------------------------------------------------------


def test_server_uses_derived_history_collection() -> None:
    """server.main() derives history_col from config instead of cfg.rag.history_collection."""
    # SearchConfig must NOT have a history_collection attribute
    from archon.config.loader import SearchConfig

    cfg = SearchConfig()
    assert not hasattr(cfg, "history_collection"), (
        "SearchConfig still has history_collection attribute — Task 2.1 not complete"
    )


def test_server_main_derives_collection_from_history_dir(tmp_path: Path) -> None:
    """main() in server.py must pass derived collection name, not cfg.rag.history_collection."""
    import asyncio

    from archon_search.sync import path_to_collection_name

    history_dir = str(tmp_path / "history")
    expected_col = path_to_collection_name(str(Path(history_dir).expanduser() / "sessions"))

    mock_store = MagicMock()
    mock_store.connect = AsyncMock()
    mock_store.disconnect = AsyncMock()
    mock_store.list_collections = AsyncMock(return_value=[])
    mock_store._db_path = tmp_path / "rag"

    mock_pipeline = MagicMock()
    mock_pipeline.store = mock_store

    captured_default_collection: list[str] = []

    fake_cfg = MagicMock()
    fake_cfg.search.url = "http://localhost:8282"
    fake_cfg.search.enabled = True
    fake_cfg.search.max_parallel_collections = 3
    fake_cfg.search.top_k_return = 5
    # sync_timeout_seconds used by server.main() via _search_cfg_get; must be int
    fake_cfg.search.sync_timeout_seconds = 5
    fake_cfg.history = MagicMock()
    fake_cfg.history.directory = history_dir

    async def patched_run_http_async(*a: object, **kw: object) -> None:
        pass  # don't block

    app_mock = MagicMock()
    app_mock.run_http_async = patched_run_http_async

    def fake_create_app(pipeline: object, default_collection: str) -> MagicMock:
        captured_default_collection.append(default_collection)
        return app_mock

    async def run() -> None:
        from archon_search.sync import SyncResult

        mock_sync_result = SyncResult(added=[], removed=[], unchanged=[], errors=[], skipped=[])
        # Patch lazy imports at their source modules
        with (
            patch("archon.config.loader.load_config", return_value=fake_cfg),
            patch("archon_search.pipeline.create_pipeline", return_value=mock_pipeline),
            patch("archon_search.server.mcp.create_app", side_effect=fake_create_app),
            patch("archon_search.server.mcp.create_pipeline", return_value=mock_pipeline),
            patch("archon_search.server.mcp.SearchCollectionSync") as MockSync,
            patch("archon_search.server.mcp.IndexingStateStore"),
        ):
            MockSync.return_value.sync = AsyncMock(return_value=mock_sync_result)
            import archon.config.loader as cfg_mod
            orig_load = cfg_mod.load_config
            cfg_mod.load_config = lambda *a, **kw: fake_cfg  # type: ignore[assignment]
            try:
                from archon_search.server.mcp import main
                await main()
            finally:
                cfg_mod.load_config = orig_load  # type: ignore[assignment]

    asyncio.run(run())

    # The default collection passed to create_app must be the derived one
    assert len(captured_default_collection) > 0
    assert captured_default_collection[-1] == expected_col
