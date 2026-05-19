"""Suite 6: ArchonToolkitSearch E2E tests (FEAT-038 Task 12.1).

Tests use the real ArchonToolkit with _register_search_tools registered.
SearchClient HTTP layer is mocked via patch('archon.ai.archon_toolkit_search.get_search_client').

H6.1–H6.3:  search_status tool
H6.4, E6.1: search_ingest tool
H6.5–H6.9:  collection management (add, remove, list, info)
E6.2–E6.3:  collection management error paths
H6.10–H6.12: CLI redirect tools (start, stop, sync)
H6.13, E6.4: search_collection_reindex
E6.5:        search_collection_info server-down path
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from archon.ai.archon_toolkit import ArchonToolkit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_toolkit(config=None) -> ArchonToolkit:
    """Create a real ArchonToolkit with search tools registered."""
    toolkit = ArchonToolkit(config=config)
    return toolkit


def _make_mock_client() -> AsyncMock:
    """Return a mock SearchClient with all async methods stubbed."""
    client = AsyncMock()
    client.health = AsyncMock(return_value={"status": "running"})
    client.status = AsyncMock(return_value={"running": True, "pid": 1234, "collections": []})
    client.ingest = AsyncMock(return_value=_make_ingest_job())
    client.list_collections = AsyncMock(return_value=[])
    client.add_collection = AsyncMock(return_value={"name": "col", "path": "/tmp/col"})
    client.remove_collection = AsyncMock(return_value={"deleted": True, "name": "col"})
    client.collection_info = AsyncMock(return_value={"name": "col", "path": "/tmp/col"})
    client.reindex_collection = AsyncMock(return_value=_make_ingest_job("xyz"))
    return client


def _make_ingest_job(job_id: str = "abc") -> MagicMock:
    """Return a mock IngestJob-like object."""
    job = MagicMock()
    job.job_id = job_id
    job.status = MagicMock()
    job.status.__str__ = lambda self: "PENDING"
    return job


def _make_config(*, search_enabled: bool = True) -> MagicMock:
    """Return a minimal mock config with search and history sub-configs."""
    cfg = MagicMock()
    cfg.search.enabled = search_enabled
    cfg.search.url = "http://127.0.0.1:8765"
    cfg.history.directory = "/tmp/history"
    return cfg


# ---------------------------------------------------------------------------
# Tool registration smoke test
# ---------------------------------------------------------------------------


class TestToolRegistration:
    """Verify all search tools are registered in ArchonToolkit.__init__."""

    def test_all_search_tools_registered(self) -> None:
        toolkit = ArchonToolkit()
        expected = {
            "search_status", "search_start", "search_stop",
            "search_ingest", "search_collection_list", "search_collection_add",
            "search_collection_remove", "search_collection_info",
            "search_collection_reindex",
        }
        # ArchonToolkit stores handlers in _handlers dict (tool_name -> callable)
        registered = set(toolkit._handlers.keys())
        assert expected.issubset(registered), f"Missing search tools: {expected - registered}"


# ---------------------------------------------------------------------------
# H6.1: search_status — enabled + server running
# ---------------------------------------------------------------------------


class TestH61SearchStatusEnabledRunning:
    """H6.1: search_status when search enabled and server running."""

    async def test_call_tool_search_status_running(self) -> None:
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        mock_client.status = AsyncMock(return_value={"running": True, "collections": []})

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await toolkit.call_tool("search_status", {})

        data = json.loads(result)
        assert data["running"] is True

    async def test_search_status_running_collections_field_present(self) -> None:
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        mock_client.status = AsyncMock(return_value={"running": True, "collections": [{"name": "docs"}]})

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await toolkit.call_tool("search_status", {})

        data = json.loads(result)
        assert "collections" in data
        assert data["collections"][0]["name"] == "docs"


# ---------------------------------------------------------------------------
# H6.2: search_status — enabled + server down
# ---------------------------------------------------------------------------


class TestH62SearchStatusServerDown:
    """H6.2: search_status when enabled but server unreachable."""

    async def test_call_tool_search_status_server_down(self) -> None:
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        mock_client.status = AsyncMock(return_value=None)

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await toolkit.call_tool("search_status", {})

        data = json.loads(result)
        assert data["running"] is False
        assert "error" in data


# ---------------------------------------------------------------------------
# H6.3: search_status — search disabled in config
# ---------------------------------------------------------------------------


class TestH63SearchStatusDisabled:
    """H6.3: search_status when search config is disabled.

    The production handler (_handle_rag_status) has NO guard on config.search.enabled.
    It always calls get_search_client().status() unconditionally. When search is
    disabled the server is not started, so the client returns None — which triggers
    the same service-unavailable JSON path as H6.2. This test documents that
    no enabled-guard exists and exercises the resulting code path.
    """

    async def test_search_status_when_disabled_client_returns_none(self) -> None:
        cfg = _make_config(search_enabled=False)
        toolkit = _make_toolkit(config=cfg)
        mock_client = _make_mock_client()
        mock_client.status = AsyncMock(return_value=None)

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await toolkit.call_tool("search_status", {})

        # Service is down (not started because disabled) → {"running": False, "error": ...}
        data = json.loads(result)
        assert data["running"] is False


# ---------------------------------------------------------------------------
# H6.4: search_ingest — success
# ---------------------------------------------------------------------------


class TestH64SearchIngestSuccess:
    """H6.4: search_ingest returns job_id on success."""

    async def test_search_ingest_returns_job_id(self) -> None:
        cfg = _make_config()
        toolkit = _make_toolkit(config=cfg)
        mock_client = _make_mock_client()
        mock_client.ingest = AsyncMock(return_value=_make_ingest_job("abc"))

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await toolkit.call_tool("search_ingest", {"collection": "mycol", "path": "/tmp/docs"})

        data = json.loads(result)
        assert data["job_id"] == "abc"

    async def test_search_ingest_success_includes_status(self) -> None:
        cfg = _make_config()
        toolkit = _make_toolkit(config=cfg)
        mock_client = _make_mock_client()

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await toolkit.call_tool("search_ingest", {"collection": "mycol", "path": "/tmp/docs"})

        data = json.loads(result)
        assert "status" in data
        assert "collection" in data


# ---------------------------------------------------------------------------
# E6.1: search_ingest — server down
# ---------------------------------------------------------------------------


class TestE61SearchIngestServerDown:
    """E6.1: search_ingest when server is down returns error message."""

    async def test_search_ingest_server_down_returns_error(self) -> None:
        cfg = _make_config()
        toolkit = _make_toolkit(config=cfg)
        mock_client = _make_mock_client()
        mock_client.ingest = AsyncMock(return_value=None)

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await toolkit.call_tool("search_ingest", {"collection": "mycol", "path": "/tmp/docs"})

        assert "failed" in result.lower() or "unavailable" in result.lower()

    async def test_search_ingest_server_down_not_json(self) -> None:
        """Error response is a plain string, not JSON."""
        cfg = _make_config()
        toolkit = _make_toolkit(config=cfg)
        mock_client = _make_mock_client()
        mock_client.ingest = AsyncMock(return_value=None)

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await toolkit.call_tool("search_ingest", {"collection": "col", "path": "/p"})

        with pytest.raises(json.JSONDecodeError):
            json.loads(result)


# ---------------------------------------------------------------------------
# H6.5: search_collection_add — success
# ---------------------------------------------------------------------------


class TestH65SearchCollectionAddSuccess:
    """H6.5: search_collection_add returns collection info on success."""

    async def test_collection_add_success(self) -> None:
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        mock_client.add_collection = AsyncMock(return_value={"name": "col", "path": "/tmp/col"})

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await toolkit.call_tool("search_collection_add", {"path": "/tmp/col"})

        data = json.loads(result)
        assert data["name"] == "col"

    async def test_collection_add_calls_client_with_path(self) -> None:
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            await toolkit.call_tool("search_collection_add", {"path": "/tmp/my/docs"})

        mock_client.add_collection.assert_awaited_once_with("/tmp/my/docs")


# ---------------------------------------------------------------------------
# H6.6: search_collection_remove — success
# ---------------------------------------------------------------------------


class TestH66SearchCollectionRemoveSuccess:
    """H6.6: search_collection_remove returns deletion info on success."""

    async def test_collection_remove_success(self) -> None:
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        mock_client.remove_collection = AsyncMock(return_value={"deleted": True})

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await toolkit.call_tool("search_collection_remove", {"name": "col"})

        data = json.loads(result)
        assert data["deleted"] is True

    async def test_collection_remove_calls_client_with_name(self) -> None:
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            await toolkit.call_tool("search_collection_remove", {"name": "my-col"})

        mock_client.remove_collection.assert_awaited_once_with("my-col")


# ---------------------------------------------------------------------------
# H6.7: search_collection_list — success (non-empty)
# ---------------------------------------------------------------------------


class TestH67SearchCollectionListSuccess:
    """H6.7: search_collection_list returns list on success."""

    async def test_collection_list_returns_collections(self) -> None:
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        mock_client.list_collections = AsyncMock(return_value=[{"name": "col"}])

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await toolkit.call_tool("search_collection_list", {})

        data = json.loads(result)
        assert isinstance(data, list)
        assert data[0]["name"] == "col"


# ---------------------------------------------------------------------------
# H6.8: search_collection_info — success
# ---------------------------------------------------------------------------


class TestH68SearchCollectionInfoSuccess:
    """H6.8: search_collection_info returns metadata dict on success."""

    async def test_collection_info_success(self) -> None:
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        mock_client.collection_info = AsyncMock(return_value={"name": "col", "path": "/tmp/col"})

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await toolkit.call_tool("search_collection_info", {"collection_name": "col"})

        data = json.loads(result)
        assert data["name"] == "col"
        assert data["path"] == "/tmp/col"

    async def test_collection_info_calls_client_with_name(self) -> None:
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            await toolkit.call_tool("search_collection_info", {"collection_name": "docs"})

        mock_client.collection_info.assert_awaited_once_with("docs")


# ---------------------------------------------------------------------------
# H6.9: search_collection_list — empty
# ---------------------------------------------------------------------------


class TestH69SearchCollectionListEmpty:
    """H6.9: search_collection_list returns empty list when no collections."""

    async def test_collection_list_empty(self) -> None:
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        mock_client.list_collections = AsyncMock(return_value=[])

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await toolkit.call_tool("search_collection_list", {})

        data = json.loads(result)
        assert data == []


# ---------------------------------------------------------------------------
# E6.2: search_collection_add — server down
# ---------------------------------------------------------------------------


class TestE62SearchCollectionAddServerDown:
    """E6.2: search_collection_add when server is down returns error string."""

    async def test_collection_add_server_down(self) -> None:
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        mock_client.add_collection = AsyncMock(return_value=None)

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await toolkit.call_tool("search_collection_add", {"path": "/tmp/col"})

        assert "failed" in result.lower()

    async def test_collection_add_server_down_mentions_path(self) -> None:
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        mock_client.add_collection = AsyncMock(return_value=None)

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await toolkit.call_tool("search_collection_add", {"path": "/tmp/col"})

        assert "/tmp/col" in result


# ---------------------------------------------------------------------------
# E6.3: search_collection_remove — server down
# ---------------------------------------------------------------------------


class TestE63SearchCollectionRemoveServerDown:
    """E6.3: search_collection_remove when server is down returns error string."""

    async def test_collection_remove_server_down(self) -> None:
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        mock_client.remove_collection = AsyncMock(return_value=None)

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await toolkit.call_tool("search_collection_remove", {"name": "col"})

        assert "failed" in result.lower()

    async def test_collection_remove_server_down_mentions_name(self) -> None:
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        mock_client.remove_collection = AsyncMock(return_value=None)

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await toolkit.call_tool("search_collection_remove", {"name": "my-col"})

        assert "my-col" in result


# ---------------------------------------------------------------------------
# H6.10: search_start — CLI redirect
# ---------------------------------------------------------------------------


class TestH610SearchStart:
    """H6.10: search_start returns CLI guidance message."""

    async def test_search_start_returns_cli_redirect(self) -> None:
        toolkit = _make_toolkit()

        result = await toolkit.call_tool("search_start", {})

        assert "archon search start" in result


# ---------------------------------------------------------------------------
# H6.11: search_stop — CLI redirect
# ---------------------------------------------------------------------------


class TestH611SearchStop:
    """H6.11: search_stop returns CLI guidance message."""

    async def test_search_stop_returns_cli_redirect(self) -> None:
        toolkit = _make_toolkit()

        result = await toolkit.call_tool("search_stop", {})

        assert "archon search stop" in result


# ---------------------------------------------------------------------------
# H6.13: search_collection_reindex — success
# ---------------------------------------------------------------------------


class TestH613SearchCollectionReindexSuccess:
    """H6.13: search_collection_reindex returns job info on success."""

    async def test_reindex_success_returns_job_id(self) -> None:
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        mock_client.reindex_collection = AsyncMock(return_value=_make_ingest_job("xyz"))

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await toolkit.call_tool("search_collection_reindex", {"collection_name": "col"})

        data = json.loads(result)
        assert data["job_id"] == "xyz"

    async def test_reindex_success_includes_collection_name(self) -> None:
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await toolkit.call_tool("search_collection_reindex", {"collection_name": "docs"})

        data = json.loads(result)
        assert data["collection"] == "docs"
        assert "status" in data

    async def test_reindex_calls_client_with_collection_name(self) -> None:
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            await toolkit.call_tool("search_collection_reindex", {"collection_name": "mycol"})

        mock_client.reindex_collection.assert_awaited_once_with("mycol")


# ---------------------------------------------------------------------------
# E6.4: search_collection_reindex — server down
# ---------------------------------------------------------------------------


class TestE64SearchCollectionReindexServerDown:
    """E6.4: search_collection_reindex when server is down returns error string."""

    async def test_reindex_server_down_returns_error(self) -> None:
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        mock_client.reindex_collection = AsyncMock(return_value=None)

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await toolkit.call_tool("search_collection_reindex", {"collection_name": "col"})

        assert "failed" in result.lower() or "error" in result.lower()

    async def test_reindex_server_down_mentions_collection(self) -> None:
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        mock_client.reindex_collection = AsyncMock(return_value=None)

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await toolkit.call_tool("search_collection_reindex", {"collection_name": "my-col"})

        assert "my-col" in result


# ---------------------------------------------------------------------------
# E6.5: search_collection_info — server down
# ---------------------------------------------------------------------------


class TestE65SearchCollectionInfoServerDown:
    """E6.5 (gap): search_collection_info when server is down returns error string.

    The handler returns f"Error: collection {col_name!r} not found or service unavailable"
    when collection_info() returns None (server down or collection absent).
    """

    async def test_collection_info_server_down_returns_error(self) -> None:
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        mock_client.collection_info = AsyncMock(return_value=None)

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await toolkit.call_tool("search_collection_info", {"collection_name": "col"})

        assert "Error" in result or "not found" in result.lower()

    async def test_collection_info_server_down_mentions_collection_name(self) -> None:
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        mock_client.collection_info = AsyncMock(return_value=None)

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await toolkit.call_tool("search_collection_info", {"collection_name": "my-docs"})

        assert "my-docs" in result
