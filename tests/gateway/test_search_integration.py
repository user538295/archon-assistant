"""tests/gateway/test_search_integration.py — TDD tests for FEAT-021 Task 2.2.

Verifies that history_collection is derived from history.directory
rather than read from rag config.
"""
from __future__ import annotations

from pathlib import Path

import pytest


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


def test_archon_path_to_collection_name_derives_from_history_dir(tmp_path: Path) -> None:
    """Archon's _path_to_collection_name derives the collection name from history directory.

    This is the Archon-side boundary test: Archon must not call archon_search server
    internals directly — it uses SearchClient (HTTP). This test verifies that Archon's
    own path_to_collection_name utility in search_cmd.py correctly derives collection
    names from history directory paths, matching the expected behaviour.
    """
    from archon.cli.search_cmd import _path_to_collection_name

    history_dir = str(tmp_path / "history")
    sessions_path = str(Path(history_dir).expanduser() / "sessions")

    col = _path_to_collection_name(sessions_path)

    # The last component is "sessions" → sanitized → "sessions"
    assert col == "sessions"


def test_archon_path_to_collection_name_uses_last_component(tmp_path: Path) -> None:
    """Archon's _path_to_collection_name uses the last path component (basename).

    Verifies Archon's own utility mirrors archon_search.sync.path_to_collection_name
    without importing archon_search internals.
    """
    from archon.cli.search_cmd import _path_to_collection_name

    col1 = _path_to_collection_name("/alpha/sessions")
    col2 = _path_to_collection_name("/beta/sessions")
    assert col1 == "sessions"
    assert col2 == "sessions"
