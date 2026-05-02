"""Tests for archon_toolkit_search — HTTP client edition (FEAT-038 Task 7.3).

All tools communicate with the archon-search service exclusively via SearchClient.
No direct imports of archon.search.* or archon_search.* internal symbols.
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from archon.ai.archon_toolkit import ArchonToolkit
import archon.ai.archon_toolkit_search as search_module
from archon.ai.archon_toolkit_search import (
    _handle_rag_status,
    _handle_rag_start,
    _handle_rag_stop,
    _handle_rag_ingest,
    _handle_rag_sync,
    _handle_rag_collection_list,
    _handle_rag_collection_add,
    _handle_rag_collection_remove,
    _handle_rag_collection_info,
    _handle_rag_collection_reindex,
)
from archon.platform.types import ServiceInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_toolkit(config=None) -> ArchonToolkit:
    return ArchonToolkit(config=config)


def _stopped_service_info() -> ServiceInfo:
    return ServiceInfo(running=False, service_name="archon-search", pid=None)


def _running_service_info(pid: int = 1234) -> ServiceInfo:
    return ServiceInfo(running=True, service_name="archon-search", pid=pid)


def _make_mock_client() -> AsyncMock:
    """Return a mock SearchClient with all async methods stubbed."""
    client = AsyncMock()
    client.status = AsyncMock(return_value={"running": True, "pid": 1234, "collections": []})
    client.ingest = AsyncMock(return_value=_make_ingest_job())
    client.list_collections = AsyncMock(return_value=[])
    client.add_collection = AsyncMock(return_value={"status": "ok", "path": "/some/docs"})
    client.remove_collection = AsyncMock(return_value={"status": "ok", "name": "docs"})
    client.collection_info = AsyncMock(return_value={"name": "docs", "doc_count": 10})
    client.reindex_collection = AsyncMock(return_value=_make_ingest_job("reindex-job-id"))
    return client


def _make_ingest_job(job_id: str = "test-job-id"):
    """Return a mock IngestJob-like object."""
    job = MagicMock()
    job.job_id = job_id
    job.status = MagicMock()
    job.status.__str__ = lambda self: "pending"
    return job


# ---------------------------------------------------------------------------
# 1. test_search_status_calls_status_endpoint
# ---------------------------------------------------------------------------


class TestSearchStatusCallsStatusEndpoint:
    async def test_search_status_calls_status_endpoint(self) -> None:
        """search_status calls SearchClient.status() and returns its JSON output."""
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        status_data = {"running": True, "pid": 42, "collections": [{"name": "docs"}]}
        mock_client.status = AsyncMock(return_value=status_data)

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await _handle_rag_status(toolkit, {})

        mock_client.status.assert_awaited_once()
        data = json.loads(result)
        assert data["running"] is True
        assert data["pid"] == 42
        assert data["collections"][0]["name"] == "docs"

    async def test_search_status_service_unavailable(self) -> None:
        """When SearchClient.status() returns None, return JSON with error."""
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        mock_client.status = AsyncMock(return_value=None)

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await _handle_rag_status(toolkit, {})

        data = json.loads(result)
        assert data["running"] is False
        assert "error" in data


# ---------------------------------------------------------------------------
# 2. test_search_start_invokes_local_cli_wrapper
# ---------------------------------------------------------------------------


class TestSearchStartInvokesLocalCliWrapper:
    async def test_search_start_invokes_local_cli_wrapper(self) -> None:
        """search_start uses platform service start(), not archon.search.* imports."""
        toolkit = _make_toolkit()

        mock_service = MagicMock()

        with patch("archon.ai.archon_toolkit_search.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=0)
            with patch("archon.ai.archon_toolkit_search.get_search_service", return_value=mock_service):
                result = await _handle_rag_start(toolkit, {})

        assert result == "RAG service started."
        mock_asyncio.to_thread.assert_called_once()
        # Must call start, not any search internal method
        assert mock_asyncio.to_thread.call_args[0][0] == mock_service.start

    async def test_search_start_no_archon_search_imports(self) -> None:
        """search_start handler: verify the module source has no direct archon.search.* import statements."""
        import inspect
        src = inspect.getsource(search_module)
        # Only check actual import statement lines
        forbidden = [
            line.strip() for line in src.splitlines()
            if (line.strip().startswith("from archon.search") or line.strip().startswith("import archon.search"))
        ]
        assert len(forbidden) == 0, f"Forbidden archon.search imports in module: {forbidden}"

    async def test_search_start_failure_returns_error(self) -> None:
        """start() returns non-zero exit code → failure message."""
        toolkit = _make_toolkit()
        mock_service = MagicMock()

        with patch("archon.ai.archon_toolkit_search.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=1)
            with patch("archon.ai.archon_toolkit_search.get_search_service", return_value=mock_service):
                result = await _handle_rag_start(toolkit, {})

        assert "start failed" in result
        assert "1" in result

    async def test_search_start_exception_returns_error(self) -> None:
        """start() raises → error string returned."""
        toolkit = _make_toolkit()
        mock_service = MagicMock()

        with patch("archon.ai.archon_toolkit_search.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(side_effect=RuntimeError("process error"))
            with patch("archon.ai.archon_toolkit_search.get_search_service", return_value=mock_service):
                result = await _handle_rag_start(toolkit, {})

        assert "failed" in result


# ---------------------------------------------------------------------------
# 3. test_search_stop_invokes_local_cli_wrapper
# ---------------------------------------------------------------------------


class TestSearchStopInvokesLocalCliWrapper:
    async def test_search_stop_invokes_local_cli_wrapper(self) -> None:
        """search_stop uses platform service stop(), not archon.search.* imports."""
        toolkit = _make_toolkit()
        mock_service = MagicMock()

        with patch("archon.ai.archon_toolkit_search.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=0)
            with patch("archon.ai.archon_toolkit_search.get_search_service", return_value=mock_service):
                result = await _handle_rag_stop(toolkit, {})

        assert result == "RAG service stopped."
        mock_asyncio.to_thread.assert_called_once()
        assert mock_asyncio.to_thread.call_args[0][0] == mock_service.stop

    async def test_search_stop_failure_returns_error(self) -> None:
        """stop() returns non-zero → failure message."""
        toolkit = _make_toolkit()
        mock_service = MagicMock()

        with patch("archon.ai.archon_toolkit_search.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=2)
            with patch("archon.ai.archon_toolkit_search.get_search_service", return_value=mock_service):
                result = await _handle_rag_stop(toolkit, {})

        assert "stop failed" in result
        assert "2" in result

    async def test_search_stop_exception_returns_error(self) -> None:
        """stop() raises → error string."""
        toolkit = _make_toolkit()
        mock_service = MagicMock()

        with patch("archon.ai.archon_toolkit_search.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(side_effect=RuntimeError("process error"))
            with patch("archon.ai.archon_toolkit_search.get_search_service", return_value=mock_service):
                result = await _handle_rag_stop(toolkit, {})

        assert "failed" in result


# ---------------------------------------------------------------------------
# 4. test_search_ingest_calls_client_ingest
# ---------------------------------------------------------------------------


class TestSearchIngestCallsClientIngest:
    async def test_search_ingest_calls_client_ingest(self) -> None:
        """search_ingest calls SearchClient.ingest() with collection and path."""
        mock_cfg = MagicMock()
        mock_cfg.history.directory = "/tmp/history"
        toolkit = _make_toolkit(config=mock_cfg)

        mock_client = _make_mock_client()

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await _handle_rag_ingest(toolkit, {"path": "/docs", "collection": "mydocs"})

        mock_client.ingest.assert_called_once_with(collection="mydocs", path="/docs")

    async def test_search_ingest_derives_collection_from_path(self) -> None:
        """When collection not given, derives name from path basename."""
        mock_cfg = MagicMock()
        mock_cfg.history.directory = "/tmp/history"
        toolkit = _make_toolkit(config=mock_cfg)
        mock_client = _make_mock_client()

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            await _handle_rag_ingest(toolkit, {"path": "/some/mydocs"})

        call_kwargs = mock_client.ingest.call_args
        col = call_kwargs[1].get("collection") or call_kwargs[0][0]
        assert col == "mydocs"

    async def test_search_ingest_no_config_returns_error(self) -> None:
        """When config is None, returns 'Configuration not available.'"""
        toolkit = _make_toolkit()  # no config
        result = await _handle_rag_ingest(toolkit, {})
        assert result == "Configuration not available."

    async def test_search_ingest_client_failure_returns_error(self) -> None:
        """When SearchClient.ingest() returns None, return error string."""
        mock_cfg = MagicMock()
        mock_cfg.history.directory = "/tmp/history"
        toolkit = _make_toolkit(config=mock_cfg)
        mock_client = _make_mock_client()
        mock_client.ingest = AsyncMock(return_value=None)

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await _handle_rag_ingest(toolkit, {"path": "/docs", "collection": "docs"})

        assert "failed" in result.lower() or "unavailable" in result.lower()


# ---------------------------------------------------------------------------
# 5. test_search_ingest_returns_job_info
# ---------------------------------------------------------------------------


class TestSearchIngestReturnsJobInfo:
    async def test_search_ingest_returns_job_info(self) -> None:
        """search_ingest returns JSON with job_id — breaking change from old sync behavior."""
        mock_cfg = MagicMock()
        mock_cfg.history.directory = "/tmp/history"
        toolkit = _make_toolkit(config=mock_cfg)
        mock_client = _make_mock_client()

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await _handle_rag_ingest(toolkit, {"path": "/docs", "collection": "docs"})

        data = json.loads(result)
        assert "job_id" in data
        assert data["job_id"] == "test-job-id"

    async def test_search_ingest_job_info_includes_status(self) -> None:
        """Returned job info includes status field."""
        mock_cfg = MagicMock()
        mock_cfg.history.directory = "/tmp/history"
        toolkit = _make_toolkit(config=mock_cfg)
        mock_client = _make_mock_client()

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await _handle_rag_ingest(toolkit, {"path": "/docs", "collection": "docs"})

        data = json.loads(result)
        assert "status" in data
        assert "collection" in data


# ---------------------------------------------------------------------------
# 6. test_search_sync_invokes_sync_wrapper
# ---------------------------------------------------------------------------


class TestSearchSyncInvokesSyncWrapper:
    async def test_search_sync_invokes_sync_wrapper(self) -> None:
        """search_sync returns a 'not supported' message (does not import SearchCollectionSync)."""
        toolkit = _make_toolkit()
        result = await _handle_rag_sync(toolkit, {})
        assert "not supported" in result.lower() or "sync" in result.lower()

    async def test_search_sync_does_not_import_search_collection_sync(self) -> None:
        """The module does not reference SearchCollectionSync at all."""
        import inspect
        src = inspect.getsource(search_module)
        assert "SearchCollectionSync" not in src

    async def test_search_sync_does_not_import_archon_search_internals(self) -> None:
        """The module has no 'from archon.search.' import statements (except via search_client)."""
        import inspect
        src = inspect.getsource(search_module)
        # Only check actual import statement lines (starting with 'from' or 'import')
        forbidden_import_lines = [
            line.strip() for line in src.splitlines()
            if (line.strip().startswith("from archon.search") or line.strip().startswith("import archon.search"))
        ]
        assert len(forbidden_import_lines) == 0, \
            f"Forbidden archon.search imports found: {forbidden_import_lines}"


# ---------------------------------------------------------------------------
# 7. test_search_collection_list_calls_client
# ---------------------------------------------------------------------------


class TestSearchCollectionListCallsClient:
    async def test_search_collection_list_calls_client(self) -> None:
        """search_collection_list calls SearchClient.list_collections()."""
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        collections_data = [{"name": "docs", "doc_count": 10}, {"name": "notes", "doc_count": 5}]
        mock_client.list_collections = AsyncMock(return_value=collections_data)

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await _handle_rag_collection_list(toolkit, {})

        mock_client.list_collections.assert_awaited_once()
        data = json.loads(result)
        assert len(data) == 2
        assert data[0]["name"] == "docs"

    async def test_search_collection_list_returns_empty_on_empty(self) -> None:
        """Returns JSON [] when no collections."""
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        mock_client.list_collections = AsyncMock(return_value=[])

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await _handle_rag_collection_list(toolkit, {})

        data = json.loads(result)
        assert data == []


# ---------------------------------------------------------------------------
# 8. test_search_collection_add_calls_client
# ---------------------------------------------------------------------------


class TestSearchCollectionAddCallsClient:
    async def test_search_collection_add_calls_client(self) -> None:
        """search_collection_add calls SearchClient.add_collection(path)."""
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        mock_client.add_collection = AsyncMock(return_value={"status": "ok", "path": "/new/docs"})

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await _handle_rag_collection_add(toolkit, {"path": "/new/docs"})

        mock_client.add_collection.assert_awaited_once_with("/new/docs")
        data = json.loads(result)
        assert data["status"] == "ok"

    async def test_search_collection_add_client_failure_returns_error(self) -> None:
        """When add_collection() returns None, return error string."""
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        mock_client.add_collection = AsyncMock(return_value=None)

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await _handle_rag_collection_add(toolkit, {"path": "/new/docs"})

        assert "Failed" in result or "failed" in result


# ---------------------------------------------------------------------------
# 9. test_search_collection_remove_calls_client
# ---------------------------------------------------------------------------


class TestSearchCollectionRemoveCallsClient:
    async def test_search_collection_remove_calls_client(self) -> None:
        """search_collection_remove calls SearchClient.remove_collection(name)."""
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        mock_client.remove_collection = AsyncMock(return_value={"status": "ok", "name": "docs"})

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await _handle_rag_collection_remove(toolkit, {"name": "docs"})

        mock_client.remove_collection.assert_awaited_once_with("docs")
        data = json.loads(result)
        assert data["status"] == "ok"

    async def test_search_collection_remove_client_failure_returns_error(self) -> None:
        """When remove_collection() returns None, return error string."""
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        mock_client.remove_collection = AsyncMock(return_value=None)

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await _handle_rag_collection_remove(toolkit, {"name": "docs"})

        assert "failed" in result.lower() or "Failed" in result


# ---------------------------------------------------------------------------
# 9b. test_search_collection_info_calls_client
# ---------------------------------------------------------------------------


class TestSearchCollectionInfoCallsClient:
    async def test_search_collection_info_calls_client(self) -> None:
        """search_collection_info calls SearchClient.collection_info(name)."""
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        info = {"name": "docs", "doc_count": 10, "chunk_count": 100}
        mock_client.collection_info = AsyncMock(return_value=info)

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await _handle_rag_collection_info(toolkit, {"collection_name": "docs"})

        mock_client.collection_info.assert_awaited_once_with("docs")
        data = json.loads(result)
        assert data["name"] == "docs"
        assert data["doc_count"] == 10

    async def test_search_collection_info_not_found_returns_error(self) -> None:
        """When collection_info() returns None, return error string."""
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        mock_client.collection_info = AsyncMock(return_value=None)

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await _handle_rag_collection_info(toolkit, {"collection_name": "missing"})

        assert "Error" in result
        assert "missing" in result


# ---------------------------------------------------------------------------
# 10. test_search_collection_reindex_calls_client
# ---------------------------------------------------------------------------


class TestSearchCollectionReindexCallsClient:
    async def test_search_collection_reindex_calls_client(self) -> None:
        """search_collection_reindex calls SearchClient.reindex_collection(name)."""
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await _handle_rag_collection_reindex(toolkit, {"collection_name": "docs"})

        mock_client.reindex_collection.assert_awaited_once_with("docs")
        data = json.loads(result)
        assert "job_id" in data
        assert data["job_id"] == "reindex-job-id"

    async def test_search_collection_reindex_returns_job_info(self) -> None:
        """Returned result includes job_id — breaking change from old sync behavior."""
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await _handle_rag_collection_reindex(toolkit, {"collection_name": "docs"})

        data = json.loads(result)
        assert "job_id" in data
        assert "status" in data
        assert data["collection"] == "docs"

    async def test_search_collection_reindex_client_failure_returns_error(self) -> None:
        """When reindex_collection() returns None, return error string."""
        toolkit = _make_toolkit()
        mock_client = _make_mock_client()
        mock_client.reindex_collection = AsyncMock(return_value=None)

        with patch("archon.ai.archon_toolkit_search.get_search_client", return_value=mock_client):
            result = await _handle_rag_collection_reindex(toolkit, {"collection_name": "docs"})

        assert "Error" in result or "failed" in result.lower()


# ---------------------------------------------------------------------------
# Module-level import verification
# ---------------------------------------------------------------------------


class TestNoInternalSearchImports:
    """Verify the rewritten module has no direct archon.search.* or archon_search.* imports."""

    def test_no_archon_search_star_imports_in_module(self) -> None:
        """The module source does not contain 'from archon.search' import statements."""
        import inspect
        src = inspect.getsource(search_module)
        forbidden = [
            line.strip() for line in src.splitlines()
            if (line.strip().startswith("from archon.search") or line.strip().startswith("from archon_search"))
        ]
        assert len(forbidden) == 0, f"Forbidden imports found: {forbidden}"

    def test_no_search_store_symbol(self) -> None:
        """SearchStore is not imported into the module namespace."""
        assert not hasattr(search_module, "SearchStore")

    def test_no_create_pipeline_symbol(self) -> None:
        """create_pipeline is not imported into the module namespace."""
        assert not hasattr(search_module, "create_pipeline")

    def test_no_search_collection_sync_symbol(self) -> None:
        """SearchCollectionSync is not imported into the module namespace."""
        assert not hasattr(search_module, "SearchCollectionSync")


# ---------------------------------------------------------------------------
# _register_search_tools — registration completeness
# ---------------------------------------------------------------------------


class TestRegisterSearchTools:
    def test_all_10_tools_registered(self) -> None:
        """_register_search_tools registers exactly the 10 expected tool names."""
        from archon.ai.archon_toolkit_search import _register_search_tools
        from unittest.mock import MagicMock

        toolkit = MagicMock(spec=ArchonToolkit)
        toolkit._tools = {}

        registered_names: list[str] = []

        def capture_register(name: str, schema: dict, handler: object) -> None:
            registered_names.append(name)

        toolkit.register_tool = capture_register

        _register_search_tools(toolkit)

        expected = {
            "search_status",
            "search_start",
            "search_stop",
            "search_ingest",
            "search_sync",
            "search_collection_list",
            "search_collection_add",
            "search_collection_remove",
            "search_collection_info",
            "search_collection_reindex",
        }
        assert set(registered_names) == expected
