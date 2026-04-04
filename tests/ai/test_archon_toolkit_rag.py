"""Tests for archon_toolkit_rag — rag_status MCP tool (Task 1.1)."""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from archon.ai.archon_toolkit import ArchonToolkit
import archon.ai.archon_toolkit_rag as rag_module
from archon.ai.archon_toolkit_rag import _handle_rag_status
from archon.platform.types import ServiceInfo
from archon.search._types import CollectionInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_toolkit(config=None) -> ArchonToolkit:
    return ArchonToolkit(config=config)


def _stopped_service_info() -> ServiceInfo:
    return ServiceInfo(running=False, service_name="archon-rag", pid=None)


def _running_service_info(pid: int = 1234) -> ServiceInfo:
    return ServiceInfo(running=True, service_name="archon-rag", pid=pid)


# ---------------------------------------------------------------------------
# test_rag_status_stopped
# ---------------------------------------------------------------------------


class TestRagStatusStopped:
    async def test_rag_status_stopped(self) -> None:
        """When the RAG service is stopped, return JSON with running=False."""
        toolkit = _make_toolkit()

        mock_service = MagicMock()
        mock_service.status = MagicMock(return_value=_stopped_service_info())

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_stopped_service_info())

            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=mock_service):
                result = await _handle_rag_status(toolkit, {})

        data = json.loads(result)
        assert data == {"running": False, "pid": None, "collections": []}


# ---------------------------------------------------------------------------
# test_rag_status_running_with_collections
# ---------------------------------------------------------------------------


class TestRagStatusRunningWithCollections:
    async def test_rag_status_running_with_collections(self) -> None:
        """When RAG service runs, return JSON with pid and collection list."""
        mock_cfg = MagicMock()
        mock_cfg.search.db_path = "/tmp/test_rag_db"
        toolkit = _make_toolkit(config=mock_cfg)

        collections = [
            CollectionInfo(name="docs", doc_count=10, chunk_count=100),
            CollectionInfo(name="notes", doc_count=5, chunk_count=42),
        ]

        mock_store = AsyncMock()
        mock_store.list_collections = AsyncMock(return_value=collections)

        mock_service = MagicMock()
        mock_service.status = MagicMock(return_value=_running_service_info(pid=1234))

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_running_service_info(pid=1234))

            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=mock_service):
                with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                    result = await _handle_rag_status(toolkit, {})

        data = json.loads(result)
        assert data["running"] is True
        assert data["pid"] == 1234
        assert len(data["collections"]) == 2
        col0 = data["collections"][0]
        assert col0["name"] == "docs"
        assert col0["doc_count"] == 10
        assert col0["chunk_count"] == 100
        col1 = data["collections"][1]
        assert col1["name"] == "notes"
        assert col1["doc_count"] == 5
        assert col1["chunk_count"] == 42
        mock_store.connect.assert_called_once()
        mock_store.disconnect.assert_called_once()


# ---------------------------------------------------------------------------
# test_rag_status_rag_unavailable
# ---------------------------------------------------------------------------


class TestRagStatusUnavailable:
    async def test_rag_status_rag_unavailable(self) -> None:
        """When _RAG_AVAILABLE is False, return 'RAG not available'."""
        toolkit = _make_toolkit()

        with patch.object(rag_module, "_RAG_AVAILABLE", False):
            result = await _handle_rag_status(toolkit, {})

        assert result == "RAG not available"


# ---------------------------------------------------------------------------
# test_rag_status_running_no_config
# ---------------------------------------------------------------------------


class TestRagStatusRunningNoConfig:
    async def test_rag_status_running_no_config(self) -> None:
        """When service is running but config=None, return running=True with empty collections."""
        toolkit = _make_toolkit()  # no config arg → config=None

        mock_service = MagicMock()
        mock_service.status = MagicMock(return_value=_running_service_info(pid=1234))

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_running_service_info(pid=1234))

            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=mock_service):
                with patch("archon.ai.archon_toolkit_rag.SearchStore") as mock_rag_store_cls:
                    result = await _handle_rag_status(toolkit, {})

        data = json.loads(result)
        assert data == {"running": True, "pid": 1234, "collections": []}
        mock_rag_store_cls.assert_not_called()


# ---------------------------------------------------------------------------
# test_rag_status_store_error  (list_collections raises)
# ---------------------------------------------------------------------------


class TestRagStatusStoreError:
    async def test_rag_status_store_error(self) -> None:
        """When store.list_collections() raises, return JSON with running=True and empty collections."""
        mock_cfg = MagicMock()
        mock_cfg.search.db_path = "/tmp/test_rag_db"
        toolkit = _make_toolkit(config=mock_cfg)

        mock_store = AsyncMock()
        mock_store.list_collections = AsyncMock(side_effect=RuntimeError("DB error"))

        mock_service = MagicMock()
        mock_service.status = MagicMock(return_value=_running_service_info(pid=5678))

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_running_service_info(pid=5678))

            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=mock_service):
                with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                    result = await _handle_rag_status(toolkit, {})

        data = json.loads(result)
        assert data["running"] is True
        assert data["pid"] == 5678
        assert data["collections"] == []
        mock_store.connect.assert_called_once()
        mock_store.disconnect.assert_called_once()


# ---------------------------------------------------------------------------
# test_rag_status_disconnect_on_error  (connect raises)
# ---------------------------------------------------------------------------


class TestRagStatusDisconnectOnError:
    async def test_rag_status_disconnect_on_error(self) -> None:
        """store.disconnect() must be called even when connect() raises."""
        mock_cfg = MagicMock()
        mock_cfg.search.db_path = "/tmp/test_rag_db"
        toolkit = _make_toolkit(config=mock_cfg)

        mock_store = AsyncMock()
        mock_store.connect = AsyncMock(side_effect=RuntimeError("Connection refused"))

        mock_service = MagicMock()
        mock_service.status = MagicMock(return_value=_running_service_info(pid=9999))

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_running_service_info(pid=9999))

            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=mock_service):
                with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                    result = await _handle_rag_status(toolkit, {})

        mock_store.disconnect.assert_called_once()
        data = json.loads(result)
        assert data["running"] is True
        assert data["collections"] == []


# ---------------------------------------------------------------------------
# rag_start tests (Task 1.2)
# ---------------------------------------------------------------------------


from archon.ai.archon_toolkit_rag import _handle_rag_start  # noqa: E402


class TestRagStartSuccess:
    async def test_rag_start_success(self) -> None:
        """When start() returns 0, return 'RAG service started.'"""
        toolkit = _make_toolkit()

        mock_service = MagicMock()
        mock_service.start = MagicMock(return_value=0)

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=0)

            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=mock_service):
                result = await _handle_rag_start(toolkit, {})

        assert result == "RAG service started."
        mock_asyncio.to_thread.assert_called_once()
        assert mock_asyncio.to_thread.call_args[0][0] == mock_service.start


class TestRagStartFailure:
    async def test_rag_start_failure(self) -> None:
        """When start() returns non-zero, return failure message."""
        toolkit = _make_toolkit()

        mock_service = MagicMock()
        mock_service.start = MagicMock(return_value=1)

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=1)

            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=mock_service):
                result = await _handle_rag_start(toolkit, {})

        assert result == "RAG service start failed (exit code 1)."
        mock_asyncio.to_thread.assert_called_once()
        assert mock_asyncio.to_thread.call_args[0][0] == mock_service.start


class TestRagStartRaises:
    async def test_rag_start_raises(self) -> None:
        """When start() raises, handler returns error string (no uncaught exception)."""
        toolkit = _make_toolkit()

        mock_service = MagicMock()
        mock_service.start = MagicMock(side_effect=RuntimeError("process error"))

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(side_effect=RuntimeError("process error"))

            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=mock_service):
                result = await _handle_rag_start(toolkit, {})

        assert isinstance(result, str)
        assert "failed" in result


class TestRagStartUnavailable:
    async def test_rag_start_rag_unavailable(self) -> None:
        """When _RAG_AVAILABLE is False, return 'RAG not available'."""
        toolkit = _make_toolkit()

        with patch.object(rag_module, "_RAG_AVAILABLE", False):
            result = await _handle_rag_start(toolkit, {})

        assert result == "RAG not available"


# ---------------------------------------------------------------------------
# rag_stop tests (Task 1.3)
# ---------------------------------------------------------------------------


from archon.ai.archon_toolkit_rag import _handle_rag_stop  # noqa: E402


class TestRagStopSuccess:
    async def test_rag_stop_success(self) -> None:
        """When stop() returns 0, return 'RAG service stopped.'"""
        toolkit = _make_toolkit()

        mock_service = MagicMock()
        mock_service.stop = MagicMock(return_value=0)

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=0)

            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=mock_service):
                result = await _handle_rag_stop(toolkit, {})

        assert result == "RAG service stopped."
        mock_asyncio.to_thread.assert_called_once()
        assert mock_asyncio.to_thread.call_args[0][0] == mock_service.stop


class TestRagStopFailure:
    async def test_rag_stop_failure(self) -> None:
        """When stop() returns non-zero, return failure message with exit code."""
        toolkit = _make_toolkit()

        mock_service = MagicMock()
        mock_service.stop = MagicMock(return_value=2)

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=2)

            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=mock_service):
                result = await _handle_rag_stop(toolkit, {})

        assert result == "RAG service stop failed (exit code 2)."
        mock_asyncio.to_thread.assert_called_once()
        assert mock_asyncio.to_thread.call_args[0][0] == mock_service.stop


class TestRagStopRaises:
    async def test_rag_stop_raises(self) -> None:
        """When stop() raises, handler returns error string containing 'failed'."""
        toolkit = _make_toolkit()

        mock_service = MagicMock()
        mock_service.stop = MagicMock(side_effect=RuntimeError("process error"))

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(side_effect=RuntimeError("process error"))

            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=mock_service):
                result = await _handle_rag_stop(toolkit, {})

        assert result == "RAG service stop failed: process error"
        mock_asyncio.to_thread.assert_called_once()
        assert mock_asyncio.to_thread.call_args[0][0] == mock_service.stop


class TestRagStopUnavailable:
    async def test_rag_stop_rag_unavailable(self) -> None:
        """When _RAG_AVAILABLE is False, return 'RAG not available'."""
        toolkit = _make_toolkit()

        with patch.object(rag_module, "_RAG_AVAILABLE", False):
            result = await _handle_rag_stop(toolkit, {})

        assert result == "RAG not available"


# ---------------------------------------------------------------------------
# rag_ingest tests (Task 2.1)
# ---------------------------------------------------------------------------


from archon.ai.archon_toolkit_rag import _handle_rag_ingest  # noqa: E402
from archon.search._types import IngestResult  # noqa: E402


def _make_ingest_results(ok_count: int, error_count: int) -> list[IngestResult]:
    results = []
    for i in range(ok_count):
        results.append(IngestResult(doc_id=f"doc_{i}", chunks_created=5, status="ok"))
    for i in range(error_count):
        results.append(IngestResult(doc_id=f"err_{i}", chunks_created=0, status="error", error="parse error"))
    return results


def _make_config_with_history(history_dir: str = "/tmp/archon/history") -> MagicMock:
    mock_cfg = MagicMock()
    mock_cfg.history.directory = history_dir
    return mock_cfg


class TestRagIngestServiceRunningBlocked:
    async def test_rag_ingest_service_running_blocked(self) -> None:
        """When RAG service is running, return error asking to stop first."""
        toolkit = _make_toolkit(config=_make_config_with_history())

        mock_service = MagicMock()

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_running_service_info())

            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=mock_service):
                result = await _handle_rag_ingest(toolkit, {})

        assert "Error:" in result
        assert "running" in result.lower()
        assert "rag_stop" in result


class TestRagIngestSuccess:
    async def test_rag_ingest_success(self) -> None:
        """Mock service stopped, 3 ok + 1 error result; assert JSON {ok:3, errors:1}."""
        mock_cfg = _make_config_with_history()
        toolkit = _make_toolkit(config=mock_cfg)

        ingest_results = _make_ingest_results(ok_count=3, error_count=1)

        mock_pipeline = AsyncMock()
        mock_pipeline.store = AsyncMock()
        mock_pipeline.ingest_directory = AsyncMock(return_value=ingest_results)

        mock_service = MagicMock()

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_stopped_service_info())

            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=mock_service):
                with patch("archon.ai.archon_toolkit_rag.create_pipeline", return_value=mock_pipeline):
                    with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", return_value="sessions"):
                        result = await _handle_rag_ingest(toolkit, {"path": "/tmp/some/path"})

        data = json.loads(result)
        assert data["ok"] == 3
        assert data["errors"] == 1
        assert "collection" in data
        assert isinstance(data["collection"], str)
        mock_pipeline.store.connect.assert_called_once()
        mock_pipeline.store.disconnect.assert_called_once()


class TestRagIngestDefaultPath:
    async def test_rag_ingest_default_path(self) -> None:
        """No 'path' arg — path passed to ingest_directory should end with 'sessions'."""
        mock_cfg = _make_config_with_history("/tmp/archon/history")
        toolkit = _make_toolkit(config=mock_cfg)

        captured: list = []

        async def fake_ingest(path, collection, **kwargs):
            captured.append(path)
            return []

        mock_pipeline = AsyncMock()
        mock_pipeline.store = AsyncMock()
        mock_pipeline.ingest_directory = fake_ingest

        mock_service = MagicMock()

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_stopped_service_info())

            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=mock_service):
                with patch("archon.ai.archon_toolkit_rag.create_pipeline", return_value=mock_pipeline):
                    with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", return_value="sessions"):
                        await _handle_rag_ingest(toolkit, {})

        assert len(captured) == 1
        called_path = captured[0]
        assert str(called_path).endswith("/sessions") or str(called_path).endswith("\\sessions")


class TestRagIngestCustomCollection:
    async def test_rag_ingest_custom_collection(self) -> None:
        """collection='my_col' arg is passed through in the result JSON."""
        mock_cfg = _make_config_with_history()
        toolkit = _make_toolkit(config=mock_cfg)

        mock_pipeline = AsyncMock()
        mock_pipeline.store = AsyncMock()
        mock_pipeline.ingest_directory = AsyncMock(return_value=[])

        mock_service = MagicMock()

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_stopped_service_info())

            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=mock_service):
                with patch("archon.ai.archon_toolkit_rag.create_pipeline", return_value=mock_pipeline):
                    with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", return_value="default"):
                        result = await _handle_rag_ingest(toolkit, {
                            "path": "/tmp/some/path",
                            "collection": "my_col",
                        })

        data = json.loads(result)
        assert data["collection"] == "my_col"
        mock_pipeline.ingest_directory.assert_called_once()
        call_args = mock_pipeline.ingest_directory.call_args
        assert call_args[0][1] == "my_col"


class TestRagIngestNoConfig:
    async def test_rag_ingest_no_config(self) -> None:
        """When _config is None, return 'Configuration not available.'"""
        toolkit = _make_toolkit()  # no config

        mock_service = MagicMock()

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_stopped_service_info())

            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=mock_service):
                result = await _handle_rag_ingest(toolkit, {})

        assert result == "Configuration not available."


class TestRagIngestException:
    async def test_rag_ingest_exception(self) -> None:
        """When pipeline raises, response contains 'Ingest failed:'."""
        mock_cfg = _make_config_with_history()
        toolkit = _make_toolkit(config=mock_cfg)

        mock_pipeline = AsyncMock()
        mock_pipeline.store = AsyncMock()
        mock_pipeline.ingest_directory = AsyncMock(side_effect=RuntimeError("disk full"))

        mock_service = MagicMock()

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_stopped_service_info())

            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=mock_service):
                with patch("archon.ai.archon_toolkit_rag.create_pipeline", return_value=mock_pipeline):
                    with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", return_value="sessions"):
                        result = await _handle_rag_ingest(toolkit, {"path": "/tmp/some/path"})

        assert "Ingest failed:" in result


class TestRagIngestDisconnectOnError:
    async def test_rag_ingest_disconnect_on_error(self) -> None:
        """pipeline.store.disconnect() is called even when ingest_directory raises."""
        mock_cfg = _make_config_with_history()
        toolkit = _make_toolkit(config=mock_cfg)

        mock_pipeline = AsyncMock()
        mock_pipeline.store = AsyncMock()
        mock_pipeline.ingest_directory = AsyncMock(side_effect=RuntimeError("failure"))

        mock_service = MagicMock()

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_stopped_service_info())

            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=mock_service):
                with patch("archon.ai.archon_toolkit_rag.create_pipeline", return_value=mock_pipeline):
                    with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", return_value="sessions"):
                        await _handle_rag_ingest(toolkit, {"path": "/tmp/some/path"})

        mock_pipeline.store.connect.assert_called_once()
        mock_pipeline.store.disconnect.assert_called_once()


class TestRagIngestUnavailable:
    async def test_rag_ingest_rag_unavailable(self) -> None:
        """When _RAG_AVAILABLE is False, return 'RAG not available'."""
        toolkit = _make_toolkit()

        with patch.object(rag_module, "_RAG_AVAILABLE", False):
            result = await _handle_rag_ingest(toolkit, {})

        assert result == "RAG not available"


# ---------------------------------------------------------------------------
# rag_sync tests (Task 2.2)
# ---------------------------------------------------------------------------


from archon.ai.archon_toolkit_rag import _handle_rag_sync  # noqa: E402
from archon.search.sync import SyncResult  # noqa: E402


def _make_sync_result(
    added=(), removed=(), unchanged=(), errors=(), skipped=(), updated=()
) -> SyncResult:
    return SyncResult(
        added=list(added),
        removed=list(removed),
        unchanged=list(unchanged),
        errors=list(errors),
        skipped=list(skipped),
        updated=list(updated),
    )


class TestRagSyncSuccess:
    async def test_rag_sync_success(self) -> None:
        """sync() returns SyncResult; JSON has correct added/removed/unchanged/errors."""
        mock_cfg = MagicMock()
        mock_cfg.search.collections = ["/some/path"]
        toolkit = _make_toolkit(config=mock_cfg)

        sync_result = _make_sync_result(
            added=["col_a", "col_b"],
            removed=["col_old"],
            unchanged=["x1", "x2", "x3", "x4", "x5"],
            errors=[],
        )

        mock_pipeline = AsyncMock()
        mock_pipeline.store = AsyncMock()

        mock_sync_instance = AsyncMock()
        mock_sync_instance.sync = AsyncMock(return_value=sync_result)

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_stopped_service_info())

            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                with patch("archon.ai.archon_toolkit_rag.create_pipeline", return_value=mock_pipeline):
                    with patch("archon.ai.archon_toolkit_rag.RagCollectionSync", return_value=mock_sync_instance):
                        result = await _handle_rag_sync(toolkit, {})

        data = json.loads(result)
        assert sorted(data["added"]) == ["col_a", "col_b"]
        assert data["removed"] == ["col_old"]
        assert data["unchanged"] == 5
        assert data["errors"] == []
        assert data["updated"] == []
        assert "warning" not in data


class TestRagSyncNoConfig:
    async def test_rag_sync_no_config(self) -> None:
        """When _config is None, return 'Configuration not available.'"""
        toolkit = _make_toolkit()  # no config → _config=None

        result = await _handle_rag_sync(toolkit, {})

        assert result == "Configuration not available."


class TestRagSyncWithErrors:
    async def test_rag_sync_with_errors(self) -> None:
        """When sync returns errors, JSON errors list is non-empty."""
        mock_cfg = MagicMock()
        mock_cfg.search.collections = []
        toolkit = _make_toolkit(config=mock_cfg)

        sync_result = _make_sync_result(errors=["path does not exist: /bad/path"])

        mock_pipeline = AsyncMock()
        mock_pipeline.store = AsyncMock()

        mock_sync_instance = AsyncMock()
        mock_sync_instance.sync = AsyncMock(return_value=sync_result)

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_stopped_service_info())

            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                with patch("archon.ai.archon_toolkit_rag.create_pipeline", return_value=mock_pipeline):
                    with patch("archon.ai.archon_toolkit_rag.RagCollectionSync", return_value=mock_sync_instance):
                        result = await _handle_rag_sync(toolkit, {})

        data = json.loads(result)
        assert len(data["errors"]) > 0
        assert data["errors"] == ["path does not exist: /bad/path"]


class TestRagSyncServiceRunningIncludesWarning:
    async def test_rag_sync_service_running_includes_warning(self) -> None:
        """When service is running, returned JSON contains 'warning' key."""
        mock_cfg = MagicMock()
        mock_cfg.search.collections = []
        toolkit = _make_toolkit(config=mock_cfg)

        sync_result = _make_sync_result()

        mock_pipeline = AsyncMock()
        mock_pipeline.store = AsyncMock()

        mock_sync_instance = AsyncMock()
        mock_sync_instance.sync = AsyncMock(return_value=sync_result)

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_running_service_info())

            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                with patch("archon.ai.archon_toolkit_rag.create_pipeline", return_value=mock_pipeline):
                    with patch("archon.ai.archon_toolkit_rag.RagCollectionSync", return_value=mock_sync_instance):
                        result = await _handle_rag_sync(toolkit, {})

        data = json.loads(result)
        assert "warning" in data
        assert data["warning"] == "RAG service is running \u2014 write conflicts are possible"


class TestRagSyncDisconnectOnError:
    async def test_rag_sync_disconnect_on_error(self) -> None:
        """pipeline.store.disconnect() is called even when sync() raises."""
        mock_cfg = MagicMock()
        mock_cfg.search.collections = []
        toolkit = _make_toolkit(config=mock_cfg)

        mock_pipeline = AsyncMock()
        mock_pipeline.store = AsyncMock()

        mock_sync_instance = AsyncMock()
        mock_sync_instance.sync = AsyncMock(side_effect=RuntimeError("disk full"))

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_stopped_service_info())

            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                with patch("archon.ai.archon_toolkit_rag.create_pipeline", return_value=mock_pipeline):
                    with patch("archon.ai.archon_toolkit_rag.RagCollectionSync", return_value=mock_sync_instance):
                        result = await _handle_rag_sync(toolkit, {})

        mock_pipeline.store.connect.assert_called_once()
        mock_pipeline.store.disconnect.assert_called_once()
        assert "Sync failed:" in result


class TestRagSyncUnavailable:
    async def test_rag_sync_rag_unavailable(self) -> None:
        """When _RAG_AVAILABLE is False, return 'RAG not available'."""
        toolkit = _make_toolkit()

        with patch.object(rag_module, "_RAG_AVAILABLE", False):
            result = await _handle_rag_sync(toolkit, {})

        assert result == "RAG not available"


class TestRagSyncResponseIncludesUpdated:
    async def test_handle_rag_sync_response_includes_updated(self) -> None:
        """JSON response from _handle_rag_sync includes 'updated' field (Task 4.9)."""
        mock_cfg = MagicMock()
        mock_cfg.search.collections = ["/docs"]
        toolkit = _make_toolkit(config=mock_cfg)

        sync_result = _make_sync_result(updated=["docs"])

        mock_pipeline = AsyncMock()
        mock_pipeline.store = AsyncMock()

        mock_sync_instance = AsyncMock()
        mock_sync_instance.sync = AsyncMock(return_value=sync_result)

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_stopped_service_info())

            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                with patch("archon.ai.archon_toolkit_rag.create_pipeline", return_value=mock_pipeline):
                    with patch("archon.ai.archon_toolkit_rag.RagCollectionSync", return_value=mock_sync_instance):
                        result = await _handle_rag_sync(toolkit, {})

        data = json.loads(result)
        assert data["updated"] == ["docs"]


# ---------------------------------------------------------------------------
# rag_collection_list tests (Task 3.1)
# ---------------------------------------------------------------------------


from archon.ai.archon_toolkit_rag import _handle_rag_collection_list  # noqa: E402


def _make_rag_config(
    db_path: str = "/tmp/test_rag_db",
    collections: list[str] | None = None,
) -> MagicMock:
    mock_cfg = MagicMock()
    mock_cfg.search.db_path = db_path
    mock_cfg.search.collections = collections or []
    return mock_cfg


class TestRagCollectionListEmpty:
    async def test_rag_collection_list_empty(self) -> None:
        """No collections in store or config → returns JSON []."""
        mock_cfg = _make_rag_config(collections=[])
        toolkit = _make_toolkit()

        mock_store = AsyncMock()
        mock_store.list_collections = AsyncMock(return_value=[])

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                    with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", side_effect=lambda p: p):
                        with patch("pathlib.Path.exists", return_value=False):
                            result = await _handle_rag_collection_list(toolkit, {})

        data = json.loads(result)
        assert data == []
        mock_store.connect.assert_called_once()
        mock_store.disconnect.assert_called_once()


class TestRagCollectionListIndexed:
    async def test_rag_collection_list_indexed(self) -> None:
        """Collection in both store and config (with manifest entry) → status='indexed'."""
        mock_cfg = _make_rag_config(
            db_path="/tmp/test_rag_db",
            collections=["/some/docs"],
        )
        toolkit = _make_toolkit()

        col = CollectionInfo(name="docs", doc_count=10, chunk_count=100)

        mock_store = AsyncMock()
        mock_store.list_collections = AsyncMock(return_value=[col])

        manifest_data = json.dumps({"docs": "/some/docs"})

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                    with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", return_value="docs"):
                        with patch("pathlib.Path.exists", return_value=True):
                            with patch("pathlib.Path.read_text", return_value=manifest_data):
                                result = await _handle_rag_collection_list(toolkit, {})

        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["name"] == "docs"
        assert data[0]["status"] == "indexed"
        assert data[0]["doc_count"] == 10
        assert data[0]["chunk_count"] == 100
        assert data[0]["path"] == "/some/docs"
        mock_store.connect.assert_called_once()
        mock_store.disconnect.assert_called_once()


class TestRagCollectionListOrphan:
    async def test_rag_collection_list_orphan(self) -> None:
        """Collection in manifest but not config → status='orphan (managed)'."""
        mock_cfg = _make_rag_config(collections=[])
        toolkit = _make_toolkit()

        col = CollectionInfo(name="old_docs", doc_count=5, chunk_count=50)

        mock_store = AsyncMock()
        mock_store.list_collections = AsyncMock(return_value=[col])

        manifest_data = json.dumps({"old_docs": "/old/path"})

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                    with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", side_effect=lambda p: p):
                        with patch("pathlib.Path.exists", return_value=True):
                            with patch("pathlib.Path.read_text", return_value=manifest_data):
                                result = await _handle_rag_collection_list(toolkit, {})

        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["name"] == "old_docs"
        assert data[0]["status"] == "orphan (managed)"
        mock_store.connect.assert_called_once()
        mock_store.disconnect.assert_called_once()


class TestRagCollectionListNotYetIndexed:
    async def test_rag_collection_list_not_yet_indexed(self) -> None:
        """Collection in config but not store → status='not yet indexed'."""
        mock_cfg = _make_rag_config(collections=["/new/docs"])
        toolkit = _make_toolkit()

        mock_store = AsyncMock()
        mock_store.list_collections = AsyncMock(return_value=[])

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                    with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", return_value="docs"):
                        with patch("pathlib.Path.exists", return_value=False):
                            result = await _handle_rag_collection_list(toolkit, {})

        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["name"] == "docs"
        assert data[0]["status"] == "not yet indexed"
        mock_store.connect.assert_called_once()
        mock_store.disconnect.assert_called_once()


class TestRagCollectionListUnmanaged:
    async def test_rag_collection_list_unmanaged(self) -> None:
        """Collection in LanceDB but in neither manifest nor config → status='unmanaged'."""
        mock_cfg = _make_rag_config(collections=[])
        toolkit = _make_toolkit()

        col = CollectionInfo(name="mystery", doc_count=3, chunk_count=30)

        mock_store = AsyncMock()
        mock_store.list_collections = AsyncMock(return_value=[col])

        manifest_data = json.dumps({})  # empty manifest

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                    with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", side_effect=lambda p: p):
                        with patch("pathlib.Path.exists", return_value=True):
                            with patch("pathlib.Path.read_text", return_value=manifest_data):
                                result = await _handle_rag_collection_list(toolkit, {})

        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["name"] == "mystery"
        assert data[0]["status"] == "unmanaged"
        mock_store.connect.assert_called_once()
        mock_store.disconnect.assert_called_once()


class TestRagCollectionListDisconnectOnError:
    async def test_rag_collection_list_disconnect_on_error(self) -> None:
        """store.disconnect() is called even when list_collections() raises."""
        mock_cfg = _make_rag_config(collections=[])
        toolkit = _make_toolkit()

        mock_store = AsyncMock()
        mock_store.list_collections = AsyncMock(side_effect=RuntimeError("DB exploded"))

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                    with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", side_effect=lambda p: p):
                        with patch("pathlib.Path.exists", return_value=False):
                            result = await _handle_rag_collection_list(toolkit, {})

        mock_store.connect.assert_called_once()
        mock_store.disconnect.assert_called_once()
        assert "Error:" in result
        assert "DB exploded" in result


class TestRagCollectionListRagUnavailable:
    async def test_rag_collection_list_rag_unavailable(self) -> None:
        """When _RAG_AVAILABLE is False, return 'RAG not available'."""
        toolkit = _make_toolkit()

        with patch.object(rag_module, "_RAG_AVAILABLE", False):
            result = await _handle_rag_collection_list(toolkit, {})

        assert result == "RAG not available"


class TestRagCollectionListMixed:
    async def test_rag_collection_list_mixed(self) -> None:
        """Mixed statuses: indexed, orphan (managed), not yet indexed — all 3 present."""
        mock_cfg = _make_rag_config(
            db_path="/tmp/test_rag_db",
            collections=["/some/active", "/some/docs"],
        )
        toolkit = _make_toolkit()

        # "active" is in store + config → indexed
        # "old" is in store + manifest but not config → orphan (managed)
        active_col = CollectionInfo(name="active", doc_count=5, chunk_count=50)
        old_col = CollectionInfo(name="old", doc_count=2, chunk_count=20)

        mock_store = AsyncMock()
        mock_store.list_collections = AsyncMock(return_value=[active_col, old_col])

        manifest_data = json.dumps({"active": "/some/active", "old": "/old/removed"})

        def _col_name(p):
            return p.split("/")[-1]

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                    with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", side_effect=_col_name):
                        with patch("pathlib.Path.exists", return_value=True):
                            with patch("pathlib.Path.read_text", return_value=manifest_data):
                                result = await _handle_rag_collection_list(toolkit, {})

        data = json.loads(result)
        by_name = {d["name"]: d for d in data}

        assert "active" in by_name
        assert by_name["active"]["status"] == "indexed"

        assert "old" in by_name
        assert by_name["old"]["status"] == "orphan (managed)"

        # "docs" = last segment of "/new/docs" via _col_name
        assert "docs" in by_name
        assert by_name["docs"]["status"] == "not yet indexed"

        assert len(data) == 3
        mock_store.connect.assert_called_once()
        mock_store.disconnect.assert_called_once()


class TestRagCollectionListNoConfig:
    async def test_rag_collection_list_no_config(self) -> None:
        """When load_config() returns config where cfg.rag is None, return 'Configuration not available.'"""
        toolkit = _make_toolkit()

        mock_cfg = MagicMock()
        mock_cfg.search = None

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                result = await _handle_rag_collection_list(toolkit, {})

        assert result == "Configuration not available."


# ---------------------------------------------------------------------------
# TestRagCollectionListConnectFailure (C2-I-2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRagCollectionListConnectFailure:
    async def test_rag_collection_list_connect_failure(self) -> None:
        """store.connect() raises → returns error string, disconnect still called."""
        mock_cfg = _make_rag_config(collections=[])
        toolkit = _make_toolkit()
        mock_store = AsyncMock()
        mock_store.connect = AsyncMock(side_effect=RuntimeError("connection refused"))

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                    with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", side_effect=lambda p: p):
                        with patch("pathlib.Path.exists", return_value=False):
                            result = await _handle_rag_collection_list(toolkit, {})

        mock_store.disconnect.assert_called_once()
        assert "Error:" in result
        assert "connection refused" in result


# ---------------------------------------------------------------------------
# TestRagCollectionListConfigError (C2-T-3.1-04)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRagCollectionListConfigError:
    async def test_rag_collection_list_config_error(self) -> None:
        """load_config() raising → returns 'Configuration error: ...'."""
        toolkit = _make_toolkit()

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", side_effect=RuntimeError("corrupt config")):
                result = await _handle_rag_collection_list(toolkit, {})

        assert "Configuration error:" in result
        assert "corrupt config" in result


# ---------------------------------------------------------------------------
# rag_collection_add tests (Task 3.2)
# ---------------------------------------------------------------------------


from archon.ai.archon_toolkit_rag import _handle_rag_collection_add  # noqa: E402


def _make_rag_cfg_with_collections(
    db_path: str = "/tmp/test_rag_db",
    collections: list[str] | None = None,
    config_file: str | None = None,
) -> MagicMock:
    """Build a MagicMock config with rag.collections and rag.db_path set."""
    mock_cfg = MagicMock()
    mock_cfg.search.db_path = db_path
    mock_cfg.search.collections = collections or []
    return mock_cfg


def _make_toolkit_with_config_file(config_file: str | None = None) -> ArchonToolkit:
    tk = _make_toolkit()
    tk._config_file = config_file
    return tk


class TestRagCollectionAddRagUnavailable:
    async def test_rag_collection_add_rag_unavailable(self) -> None:
        """_RAG_AVAILABLE=False → 'RAG not available'."""
        toolkit = _make_toolkit()
        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", False):
            result = await _handle_rag_collection_add(toolkit, {"path": "/some/docs"})
        assert result == "RAG not available"


class TestRagCollectionAddAlreadyRegistered:
    async def test_rag_collection_add_already_registered(self) -> None:
        """Path already normalises to one in cfg.rag.collections → 'Already registered:'."""
        existing_path = "/some/docs"
        mock_cfg = _make_rag_cfg_with_collections(collections=[existing_path])
        toolkit = _make_toolkit()

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", side_effect=lambda p: "docs"):
                    result = await _handle_rag_collection_add(toolkit, {"path": existing_path})

        assert "Already registered:" in result


class TestRagCollectionAddSuccess:
    async def test_rag_collection_add_success(self) -> None:
        """Valid new path → config_collections_append called, ingest called, success message."""
        mock_cfg = _make_rag_cfg_with_collections(collections=[])
        toolkit = _make_toolkit_with_config_file("/home/user/.archon/config.toml")

        mock_pipeline = AsyncMock()
        mock_pipeline.store = AsyncMock()
        mock_pipeline.ingest_directory = AsyncMock(return_value=[MagicMock(ok=True)])

        stopped = _stopped_service_info()

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
                    mock_asyncio.to_thread = AsyncMock(return_value=stopped)
                    with patch("archon.ai.archon_toolkit_rag.create_pipeline", return_value=mock_pipeline):
                        with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", return_value="docs"):
                            with patch("archon.ai.archon_toolkit_rag.manifest_lookup_by_path", return_value=None):
                                with patch("archon.ai.archon_toolkit_rag.config_collections_append") as mock_append:
                                    with patch("pathlib.Path.exists", return_value=False):
                                        result = await _handle_rag_collection_add(toolkit, {"path": "/new/docs"})

        assert "added and indexed" in result
        mock_append.assert_called_once()
        mock_pipeline.ingest_directory.assert_called_once()
        mock_pipeline.store.connect.assert_called_once()
        mock_pipeline.store.disconnect.assert_called_once()


class TestRagCollectionAddServiceRunningWarns:
    async def test_rag_collection_add_service_running_warns(self) -> None:
        """Service running → Warning in result, but config and ingest still called."""
        mock_cfg = _make_rag_cfg_with_collections(collections=[])
        toolkit = _make_toolkit()

        mock_pipeline = AsyncMock()
        mock_pipeline.store = AsyncMock()
        mock_pipeline.ingest_directory = AsyncMock(return_value=[MagicMock(ok=True)])

        running = _running_service_info()

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
                    mock_asyncio.to_thread = AsyncMock(return_value=running)
                    with patch("archon.ai.archon_toolkit_rag.create_pipeline", return_value=mock_pipeline):
                        with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", return_value="docs"):
                            with patch("archon.ai.archon_toolkit_rag.manifest_lookup_by_path", return_value=None):
                                with patch("archon.ai.archon_toolkit_rag.config_collections_append") as mock_append:
                                    with patch("pathlib.Path.exists", return_value=False):
                                        result = await _handle_rag_collection_add(toolkit, {"path": "/new/docs"})

        assert "Warning" in result
        mock_append.assert_called_once()
        mock_pipeline.ingest_directory.assert_called_once()


class TestRagCollectionAddIngestError:
    async def test_rag_collection_add_ingest_error(self) -> None:
        """Ingest raises → response contains 'Ingest error:' and disconnect still called."""
        mock_cfg = _make_rag_cfg_with_collections(collections=[])
        toolkit = _make_toolkit()

        mock_pipeline = AsyncMock()
        mock_pipeline.store = AsyncMock()
        mock_pipeline.ingest_directory = AsyncMock(side_effect=RuntimeError("disk full"))

        stopped = _stopped_service_info()

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
                    mock_asyncio.to_thread = AsyncMock(return_value=stopped)
                    with patch("archon.ai.archon_toolkit_rag.create_pipeline", return_value=mock_pipeline):
                        with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", return_value="docs"):
                            with patch("archon.ai.archon_toolkit_rag.manifest_lookup_by_path", return_value=None):
                                with patch("archon.ai.archon_toolkit_rag.config_collections_append"):
                                    with patch("pathlib.Path.exists", return_value=False):
                                        result = await _handle_rag_collection_add(toolkit, {"path": "/new/docs"})

        assert "Ingest error:" in result
        assert "disk full" in result
        mock_pipeline.store.disconnect.assert_called_once()


class TestRagCollectionAddDisconnectOnError:
    async def test_rag_collection_add_disconnect_on_error(self) -> None:
        """pipeline.ingest_directory raises → pipeline.store.disconnect() still called."""
        mock_cfg = _make_rag_cfg_with_collections(collections=[])
        toolkit = _make_toolkit()

        mock_pipeline = AsyncMock()
        mock_pipeline.store = AsyncMock()
        mock_pipeline.ingest_directory = AsyncMock(side_effect=RuntimeError("boom"))

        stopped = _stopped_service_info()

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
                    mock_asyncio.to_thread = AsyncMock(return_value=stopped)
                    with patch("archon.ai.archon_toolkit_rag.create_pipeline", return_value=mock_pipeline):
                        with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", return_value="docs"):
                            with patch("archon.ai.archon_toolkit_rag.manifest_lookup_by_path", return_value=None):
                                with patch("archon.ai.archon_toolkit_rag.config_collections_append"):
                                    with patch("pathlib.Path.exists", return_value=False):
                                        await _handle_rag_collection_add(toolkit, {"path": "/new/docs"})

        mock_pipeline.store.disconnect.assert_called_once()


class TestRagCollectionAddNoConfig:
    async def test_rag_collection_add_no_rag_config(self) -> None:
        """cfg.rag is None → 'Configuration not available.'"""
        mock_cfg = MagicMock()
        mock_cfg.search = None
        toolkit = _make_toolkit()

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                result = await _handle_rag_collection_add(toolkit, {"path": "/some/docs"})

        assert result == "Configuration not available."

    async def test_rag_collection_add_config_error(self) -> None:
        """load_config() raises → 'Configuration error: ...'"""
        toolkit = _make_toolkit()

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", side_effect=RuntimeError("corrupt")):
                result = await _handle_rag_collection_add(toolkit, {"path": "/some/docs"})

        assert "Configuration error:" in result
        assert "corrupt" in result


# ---------------------------------------------------------------------------
# rag_collection_remove tests (Task 3.3)
# ---------------------------------------------------------------------------


from archon.ai.archon_toolkit_rag import _handle_rag_collection_remove  # noqa: E402


class TestRagCollectionRemoveRagUnavailable:
    async def test_rag_collection_remove_rag_unavailable(self) -> None:
        """_RAG_AVAILABLE=False → 'RAG not available'."""
        toolkit = _make_toolkit()
        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", False):
            result = await _handle_rag_collection_remove(toolkit, {"path": "/some/docs"})
        assert result == "RAG not available"


class TestRagCollectionRemoveNotRegistered:
    async def test_rag_collection_remove_not_registered(self) -> None:
        """Path not in cfg.rag.collections → error message."""
        mock_cfg = _make_rag_cfg_with_collections(collections=["/other/path"])
        toolkit = _make_toolkit()

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", side_effect=lambda p: "docs"):
                    result = await _handle_rag_collection_remove(toolkit, {"path": "/some/docs"})

        assert "not in collections" in result.lower() or "Error:" in result


class TestRagCollectionRemoveServiceRunningNoForce:
    async def test_rag_collection_remove_service_running_no_force(self) -> None:
        """Service running, force=false → error message."""
        mock_cfg = _make_rag_cfg_with_collections(collections=["/some/docs"])
        toolkit = _make_toolkit()

        running = _running_service_info()

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", return_value="docs"):
                    with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
                        mock_asyncio.to_thread = AsyncMock(return_value=running)
                        with patch("pathlib.Path.exists", return_value=False):
                            result = await _handle_rag_collection_remove(toolkit, {"path": "/some/docs", "force": False})

        assert "running" in result.lower() or "Error:" in result


class TestRagCollectionRemoveServiceRunningForce:
    async def test_rag_collection_remove_service_running_force(self) -> None:
        """Service running, force=true → proceeds to drop."""
        mock_cfg = _make_rag_cfg_with_collections(collections=["/some/docs"])
        toolkit = _make_toolkit()

        running = _running_service_info()
        mock_store = AsyncMock()
        mock_store.drop_collection = AsyncMock()

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", return_value="docs"):
                    with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
                        mock_asyncio.to_thread = AsyncMock(return_value=running)
                        with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                            with patch("archon.ai.archon_toolkit_rag.manifest_lookup_by_path", return_value=None):
                                with patch("archon.ai.archon_toolkit_rag.config_collections_remove"):
                                    with patch("archon.ai.archon_toolkit_rag.manifest_remove_entry"):
                                        with patch("pathlib.Path.exists", return_value=False):
                                            result = await _handle_rag_collection_remove(
                                                toolkit, {"path": "/some/docs", "force": True}
                                            )

        mock_store.drop_collection.assert_called_once_with("docs")
        assert "removed" in result.lower() or "Collection removed" in result


class TestRagCollectionRemoveSuccess:
    async def test_rag_collection_remove_success(self) -> None:
        """Drop succeeds → config and manifest updated, success message returned."""
        mock_cfg = _make_rag_cfg_with_collections(collections=["/some/docs"])
        toolkit = _make_toolkit()

        stopped = _stopped_service_info()
        mock_store = AsyncMock()
        mock_store.drop_collection = AsyncMock()

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", return_value="docs"):
                    with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
                        mock_asyncio.to_thread = AsyncMock(return_value=stopped)
                        with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                            with patch("archon.ai.archon_toolkit_rag.manifest_lookup_by_path", return_value=None):
                                with patch("archon.ai.archon_toolkit_rag.config_collections_remove") as mock_cfg_rm:
                                    with patch("archon.ai.archon_toolkit_rag.manifest_remove_entry") as mock_mfst_rm:
                                        with patch("pathlib.Path.exists", return_value=False):
                                            result = await _handle_rag_collection_remove(
                                                toolkit, {"path": "/some/docs"}
                                            )

        assert "Collection removed" in result
        mock_cfg_rm.assert_called_once()
        mock_mfst_rm.assert_called_once()
        mock_store.connect.assert_called_once()
        mock_store.disconnect.assert_called_once()


class TestRagCollectionRemoveDropFailsAndDisconnects:
    async def test_rag_collection_remove_drop_fails_and_disconnects(self) -> None:
        """store.drop_collection raises → response contains 'Drop failed:' AND disconnect still called."""
        mock_cfg = _make_rag_cfg_with_collections(collections=["/some/docs"])
        toolkit = _make_toolkit()

        stopped = _stopped_service_info()
        mock_store = AsyncMock()
        mock_store.drop_collection = AsyncMock(side_effect=RuntimeError("table locked"))

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", return_value="docs"):
                    with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
                        mock_asyncio.to_thread = AsyncMock(return_value=stopped)
                        with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                            with patch("archon.ai.archon_toolkit_rag.manifest_lookup_by_path", return_value=None):
                                with patch("pathlib.Path.exists", return_value=False):
                                    result = await _handle_rag_collection_remove(toolkit, {"path": "/some/docs"})

        assert "Drop failed:" in result
        assert "table locked" in result
        mock_store.disconnect.assert_called_once()


class TestRagCollectionRemoveManifestLookup:
    async def test_rag_collection_remove_uses_manifest_name(self) -> None:
        """manifest_lookup_by_path returns a name → drop_collection uses manifest name, not fallback."""
        mock_cfg = _make_rag_cfg_with_collections(collections=["/some/docs"])
        toolkit = _make_toolkit()

        stopped = _stopped_service_info()
        mock_store = AsyncMock()
        mock_store.drop_collection = AsyncMock()

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", return_value="fallback_name"):
                    with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
                        mock_asyncio.to_thread = AsyncMock(return_value=stopped)
                        with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                            # manifest_lookup_by_path returns "manifest_name" — should take precedence
                            with patch("archon.ai.archon_toolkit_rag.manifest_lookup_by_path", return_value="manifest_name"):
                                with patch("archon.ai.archon_toolkit_rag.config_collections_remove"):
                                    with patch("archon.ai.archon_toolkit_rag.manifest_remove_entry") as mock_mfst_rm:
                                        with patch("pathlib.Path.exists", return_value=False):
                                            result = await _handle_rag_collection_remove(
                                                toolkit, {"path": "/some/docs"}
                                            )

        # Must use manifest-derived name for all operations
        mock_store.drop_collection.assert_called_once_with("manifest_name")
        mock_mfst_rm.assert_called_once()
        call_args = mock_mfst_rm.call_args
        assert call_args[0][1] == "manifest_name"
        assert "Collection removed" in result


class TestRagCollectionRemoveStatusCheckFails:
    async def test_rag_collection_remove_status_check_fails(self) -> None:
        """Service status check raises → returns error (fail-safe for destructive op)."""
        mock_cfg = _make_rag_cfg_with_collections(collections=["/some/docs"])
        toolkit = _make_toolkit()

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", return_value="docs"):
                    with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
                        mock_asyncio.to_thread = AsyncMock(side_effect=RuntimeError("launchd error"))
                        with patch("pathlib.Path.exists", return_value=False):
                            result = await _handle_rag_collection_remove(toolkit, {"path": "/some/docs"})

        assert "Error:" in result
        assert "could not check" in result.lower() or "launchd error" in result


# ---------------------------------------------------------------------------
# rag_collection_info tests (Task 3.4)
# ---------------------------------------------------------------------------


from archon.ai.archon_toolkit_rag import _handle_rag_collection_info  # noqa: E402
from archon.search.collection_meta import CollectionMeta  # noqa: E402
from datetime import datetime, timezone  # noqa: E402


def _make_collection_meta(name: str = "docs") -> CollectionMeta:
    return CollectionMeta(
        name=name,
        description="A test collection",
        centroid=[0.1, 0.2],
        doc_count=10,
        chunk_count=100,
        embedding_model="text-embedding-3-small",
        last_indexed=datetime(2026, 1, 15, tzinfo=timezone.utc),
    )


class TestRagCollectionInfoRagUnavailable:
    async def test_rag_collection_info_rag_unavailable(self) -> None:
        """_RAG_AVAILABLE=False → 'RAG not available'."""
        toolkit = _make_toolkit()
        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", False):
            result = await _handle_rag_collection_info(toolkit, {"collection_name": "docs"})
        assert result == "RAG not available"


class TestRagCollectionInfoFound:
    async def test_rag_collection_info_found(self) -> None:
        """Collection exists → JSON with all meta fields."""
        mock_cfg = _make_rag_config()
        toolkit = _make_toolkit()
        meta = _make_collection_meta("docs")

        mock_pipeline = AsyncMock()
        mock_pipeline.store = AsyncMock()
        mock_pipeline.get_collection_meta = AsyncMock(return_value=meta)

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.create_pipeline", return_value=mock_pipeline):
                    result = await _handle_rag_collection_info(toolkit, {"collection_name": "docs"})

        data = json.loads(result)
        assert data["name"] == "docs"
        assert data["description"] == "A test collection"
        assert data["doc_count"] == 10
        assert data["chunk_count"] == 100
        assert data["embedding_model"] == "text-embedding-3-small"
        assert data["centroid"] is True  # centroid is not None
        assert data["last_indexed"] == "2026-01-15T00:00:00+00:00"
        mock_pipeline.store.connect.assert_called_once()
        mock_pipeline.store.disconnect.assert_called_once()


class TestRagCollectionInfoNotFound:
    async def test_rag_collection_info_not_found(self) -> None:
        """get_collection_meta returns None → error message."""
        mock_cfg = _make_rag_config()
        toolkit = _make_toolkit()

        mock_pipeline = AsyncMock()
        mock_pipeline.store = AsyncMock()
        mock_pipeline.get_collection_meta = AsyncMock(return_value=None)

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.create_pipeline", return_value=mock_pipeline):
                    result = await _handle_rag_collection_info(toolkit, {"collection_name": "missing"})

        assert "missing" in result
        assert "not found" in result.lower()
        mock_pipeline.store.disconnect.assert_called_once()


class TestRagCollectionInfoStoreError:
    async def test_rag_collection_info_store_error(self) -> None:
        """Pipeline raises → exception message returned, disconnect called."""
        mock_cfg = _make_rag_config()
        toolkit = _make_toolkit()

        mock_pipeline = AsyncMock()
        mock_pipeline.store = AsyncMock()
        mock_pipeline.get_collection_meta = AsyncMock(side_effect=RuntimeError("store exploded"))

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.create_pipeline", return_value=mock_pipeline):
                    result = await _handle_rag_collection_info(toolkit, {"collection_name": "docs"})

        assert "Error: store exploded" == result
        mock_pipeline.store.disconnect.assert_called_once()


class TestRagCollectionInfoConnectError:
    async def test_rag_collection_info_connect_failure(self) -> None:
        """store.connect() raises → error returned, disconnect still called."""
        mock_cfg = _make_rag_config()
        toolkit = _make_toolkit()

        mock_pipeline = AsyncMock()
        mock_pipeline.store = AsyncMock()
        mock_pipeline.store.connect = AsyncMock(side_effect=RuntimeError("conn failed"))

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.create_pipeline", return_value=mock_pipeline):
                    result = await _handle_rag_collection_info(toolkit, {"collection_name": "docs"})

        assert "Error: conn failed" == result
        mock_pipeline.store.disconnect.assert_called_once()


class TestRagCollectionInfoConfigErrors:
    async def test_rag_collection_info_load_config_raises(self) -> None:
        """load_config() raises → 'Configuration error: ...'."""
        toolkit = _make_toolkit()
        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch(
                "archon.ai.archon_toolkit_rag.load_config",
                side_effect=RuntimeError("cfg boom"),
            ):
                result = await _handle_rag_collection_info(toolkit, {"collection_name": "docs"})
        assert "Configuration error: cfg boom" in result

    async def test_rag_collection_info_no_rag_config(self) -> None:
        """cfg.rag is None → 'Configuration not available.'."""
        mock_cfg = MagicMock()
        mock_cfg.search = None
        toolkit = _make_toolkit()
        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                result = await _handle_rag_collection_info(toolkit, {"collection_name": "docs"})
        assert result == "Configuration not available."


class TestRagCollectionInfoNullFields:
    async def test_rag_collection_info_null_centroid_and_last_indexed(self) -> None:
        """centroid=None → centroid=false; last_indexed=None → last_indexed=null in JSON."""
        mock_cfg = _make_rag_config()
        toolkit = _make_toolkit()

        meta = CollectionMeta(
            name="docs",
            description="A test collection",
            centroid=None,
            doc_count=5,
            chunk_count=50,
            embedding_model="text-embedding-3-small",
            last_indexed=None,
        )
        mock_pipeline = AsyncMock()
        mock_pipeline.store = AsyncMock()
        mock_pipeline.get_collection_meta = AsyncMock(return_value=meta)

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.create_pipeline", return_value=mock_pipeline):
                    result = await _handle_rag_collection_info(toolkit, {"collection_name": "docs"})

        data = json.loads(result)
        assert data["centroid"] is False
        assert data["last_indexed"] is None


# ---------------------------------------------------------------------------
# rag_collection_reindex tests (Task 3.5)
# ---------------------------------------------------------------------------


from pathlib import Path  # noqa: E402
from archon.ai.archon_toolkit_rag import _handle_rag_collection_reindex  # noqa: E402


class TestRagCollectionReindexServiceRunning:
    async def test_rag_collection_reindex_service_running(self) -> None:
        """When RAG service is running, return error asking to stop first."""
        mock_cfg = _make_rag_config(collections=["/some/docs"])
        toolkit = _make_toolkit()

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", return_value="docs"):
                    with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
                        mock_asyncio.to_thread = AsyncMock(return_value=_running_service_info())
                        with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                            result = await _handle_rag_collection_reindex(
                                toolkit, {"collection_name": "docs"}
                            )

        assert "Error:" in result
        assert "running" in result.lower()
        assert "rag_stop" in result


class TestRagCollectionReindexNotInConfig:
    async def test_rag_collection_reindex_not_in_config(self) -> None:
        """collection_name not in cfg.rag.collections → error."""
        mock_cfg = _make_rag_config(collections=["/other/path"])
        toolkit = _make_toolkit()

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", return_value="other"):
                    with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
                        mock_asyncio.to_thread = AsyncMock(return_value=_stopped_service_info())
                        with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                            result = await _handle_rag_collection_reindex(
                                toolkit, {"collection_name": "docs"}
                            )

        assert "Error:" in result
        assert "docs" in result
        assert "not found" in result.lower()


class TestRagCollectionReindexSuccess:
    async def test_rag_collection_reindex_success(self) -> None:
        """Valid collection; ingest_directory called with force_regenerate_description=True; JSON returned."""
        mock_cfg = _make_rag_config(collections=["/some/docs"])
        toolkit = _make_toolkit()

        ingest_results = _make_ingest_results(ok_count=5, error_count=1)

        mock_pipeline = AsyncMock()
        mock_pipeline.store = AsyncMock()
        mock_pipeline.ingest_directory = AsyncMock(return_value=ingest_results)

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", return_value="docs"):
                    with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
                        mock_asyncio.to_thread = AsyncMock(return_value=_stopped_service_info())
                        with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                            with patch("archon.ai.archon_toolkit_rag.create_pipeline", return_value=mock_pipeline):
                                result = await _handle_rag_collection_reindex(
                                    toolkit, {"collection_name": "docs"}
                                )

        data = json.loads(result)
        assert data["ok"] == 5
        assert data["errors"] == 1
        mock_pipeline.store.connect.assert_called_once()
        mock_pipeline.store.disconnect.assert_called_once()
        mock_pipeline.ingest_directory.assert_called_once()
        call_kwargs = mock_pipeline.ingest_directory.call_args
        assert call_kwargs[1].get("force_regenerate_description") is True
        expected_path = Path("/some/docs").expanduser().resolve()
        assert call_kwargs[0][0] == expected_path
        assert call_kwargs[0][1] == "docs"


class TestRagCollectionReindexDisconnectOnError:
    async def test_rag_collection_reindex_disconnect_on_error(self) -> None:
        """pipeline.store.disconnect() is called even when ingest_directory raises."""
        mock_cfg = _make_rag_config(collections=["/some/docs"])
        toolkit = _make_toolkit()

        mock_pipeline = AsyncMock()
        mock_pipeline.store = AsyncMock()
        mock_pipeline.ingest_directory = AsyncMock(side_effect=RuntimeError("disk full"))

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", return_value="docs"):
                    with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
                        mock_asyncio.to_thread = AsyncMock(return_value=_stopped_service_info())
                        with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                            with patch("archon.ai.archon_toolkit_rag.create_pipeline", return_value=mock_pipeline):
                                result = await _handle_rag_collection_reindex(
                                    toolkit, {"collection_name": "docs"}
                                )

        mock_pipeline.store.connect.assert_called_once()
        mock_pipeline.store.disconnect.assert_called_once()
        assert result.startswith("Error:")
        assert "disk full" in result


class TestRagCollectionReindexRagUnavailable:
    async def test_rag_collection_reindex_rag_unavailable(self) -> None:
        """_RAG_AVAILABLE=False → 'RAG not available'."""
        toolkit = _make_toolkit()
        with patch.object(rag_module, "_RAG_AVAILABLE", False):
            result = await _handle_rag_collection_reindex(toolkit, {"collection_name": "docs"})
        assert result == "RAG not available"


class TestRagCollectionReindexLoadConfigRaises:
    async def test_rag_collection_reindex_load_config_raises(self) -> None:
        """load_config() raises → 'Configuration error: ...'."""
        toolkit = _make_toolkit()
        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch(
                "archon.ai.archon_toolkit_rag.load_config",
                side_effect=RuntimeError("cfg boom"),
            ):
                result = await _handle_rag_collection_reindex(toolkit, {"collection_name": "docs"})
        assert "Configuration error: cfg boom" in result


class TestRagCollectionReindexNoSearchConfig:
    async def test_rag_collection_reindex_no_rag_config(self) -> None:
        """cfg.rag is None → 'Configuration not available.'."""
        mock_cfg = MagicMock()
        mock_cfg.search = None
        toolkit = _make_toolkit()
        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                result = await _handle_rag_collection_reindex(toolkit, {"collection_name": "docs"})
        assert result == "Configuration not available."


class TestRagCollectionReindexConnectError:
    async def test_rag_collection_reindex_connect_failure(self) -> None:
        """pipeline.store.connect() raises → disconnect still called, result starts with 'Error:'."""
        mock_cfg = _make_rag_config(collections=["/some/docs"])
        toolkit = _make_toolkit()

        mock_pipeline = AsyncMock()
        mock_pipeline.store = AsyncMock()
        mock_pipeline.store.connect = AsyncMock(side_effect=RuntimeError("connect failed"))

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", return_value="docs"):
                    with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
                        mock_asyncio.to_thread = AsyncMock(return_value=_stopped_service_info())
                        with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                            with patch("archon.ai.archon_toolkit_rag.create_pipeline", return_value=mock_pipeline):
                                result = await _handle_rag_collection_reindex(
                                    toolkit, {"collection_name": "docs"}
                                )

        mock_pipeline.store.disconnect.assert_called_once()
        assert result.startswith("Error:")
        assert "connect failed" in result


class TestRagCollectionReindexStatusCheckFailure:
    async def test_rag_collection_reindex_status_check_failure(self) -> None:
        """asyncio.to_thread raises → result equals 'Error: could not check RAG service status: ...'."""
        mock_cfg = _make_rag_config(collections=["/some/docs"])
        toolkit = _make_toolkit()

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", return_value="docs"):
                    with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
                        mock_asyncio.to_thread = AsyncMock(side_effect=OSError("service unreachable"))
                        with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                            result = await _handle_rag_collection_reindex(
                                toolkit, {"collection_name": "docs"}
                            )

        assert result == "Error: could not check RAG service status: service unreachable"


# ---------------------------------------------------------------------------
# Task 3.5 — Clear collection state on reindex (MCP)
# ---------------------------------------------------------------------------


class TestRagCollectionReindexClearsState:
    async def test_handle_rag_collection_reindex_clears_state(self) -> None:
        """remove_collection called on state store before ingest."""
        mock_cfg = _make_rag_config(collections=["/some/docs"])
        toolkit = _make_toolkit()

        ingest_results = _make_ingest_results(ok_count=3, error_count=0)

        mock_pipeline = AsyncMock()
        mock_pipeline.store = AsyncMock()
        mock_pipeline.ingest_directory = AsyncMock(return_value=ingest_results)

        mock_state_store = MagicMock()
        call_order: list[str] = []

        def track_remove(name):
            call_order.append(f"remove:{name}")

        mock_state_store.remove_collection = MagicMock(side_effect=track_remove)

        orig_ingest = mock_pipeline.ingest_directory

        async def track_ingest(*args, **kwargs):
            call_order.append("ingest")
            return await orig_ingest(*args, **kwargs)

        mock_pipeline.ingest_directory = AsyncMock(side_effect=track_ingest)

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", return_value="docs"):
                    with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
                        mock_asyncio.to_thread = AsyncMock(return_value=_stopped_service_info())
                        with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                            with patch("archon.ai.archon_toolkit_rag.create_pipeline", return_value=mock_pipeline):
                                with patch("archon.ai.archon_toolkit_rag.IndexingStateStore", return_value=mock_state_store):
                                    result = await _handle_rag_collection_reindex(
                                        toolkit, {"collection_name": "docs"}
                                    )

        data = json.loads(result)
        assert data["ok"] == 3
        assert call_order == ["remove:docs", "ingest"]

    async def test_handle_rag_collection_reindex_state_clear_failure_non_fatal(self) -> None:
        """remove_collection raises → ingest proceeds normally."""
        mock_cfg = _make_rag_config(collections=["/some/docs"])
        toolkit = _make_toolkit()

        ingest_results = _make_ingest_results(ok_count=2, error_count=0)

        mock_pipeline = AsyncMock()
        mock_pipeline.store = AsyncMock()
        mock_pipeline.ingest_directory = AsyncMock(return_value=ingest_results)

        mock_state_store = MagicMock()
        mock_state_store.remove_collection = MagicMock(side_effect=OSError("disk full"))

        with patch("archon.ai.archon_toolkit_rag._RAG_AVAILABLE", True):
            with patch("archon.ai.archon_toolkit_rag.load_config", return_value=mock_cfg):
                with patch("archon.ai.archon_toolkit_rag.path_to_collection_name", return_value="docs"):
                    with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
                        mock_asyncio.to_thread = AsyncMock(return_value=_stopped_service_info())
                        with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                            with patch("archon.ai.archon_toolkit_rag.create_pipeline", return_value=mock_pipeline):
                                with patch("archon.ai.archon_toolkit_rag.IndexingStateStore", return_value=mock_state_store):
                                    result = await _handle_rag_collection_reindex(
                                        toolkit, {"collection_name": "docs"}
                                    )

        data = json.loads(result)
        assert data["ok"] == 2
        mock_pipeline.ingest_directory.assert_called_once()


# ---------------------------------------------------------------------------
# TestRagStatusProgress — Task 1.7: rag_status MCP tool progress fields
# ---------------------------------------------------------------------------


class TestRagStatusProgress:
    """Tests for progress fields merged into rag_status response."""

    async def test_rag_status_includes_progress_fields(self) -> None:
        """When state file present, each collection dict includes progress fields."""
        from archon.search.progress import CollectionProgress, IndexingState, IndexingStatus

        mock_cfg = MagicMock()
        mock_cfg.search.db_path = "/tmp/test_rag_db"
        toolkit = _make_toolkit(config=mock_cfg)

        collections = [
            CollectionInfo(name="docs", doc_count=10, chunk_count=100),
        ]
        mock_store = AsyncMock()
        mock_store.list_collections = AsyncMock(return_value=collections)

        state = IndexingState(collections={
            "docs": CollectionProgress(
                status=IndexingStatus.IN_PROGRESS,
                total_files=20,
                processed_files=15,
                error=None,
                error_count=0,
            ),
        })

        mock_state_store = MagicMock()
        mock_state_store.read.return_value = state

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_running_service_info(pid=1234))
            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                    with patch("archon.ai.archon_toolkit_rag.IndexingStateStore", return_value=mock_state_store):
                        result = await _handle_rag_status(toolkit, {})

        data = json.loads(result)
        col = data["collections"][0]
        assert col["name"] == "docs"
        assert col["doc_count"] == 10
        assert col["chunk_count"] == 100
        assert col["status"] == "partial"
        assert col["processed_files"] == 15
        assert col["total_files"] == 20

    async def test_rag_status_without_state_file(self) -> None:
        """When no state file exists, collections have no progress fields."""
        mock_cfg = MagicMock()
        mock_cfg.search.db_path = "/tmp/test_rag_db"
        toolkit = _make_toolkit(config=mock_cfg)

        collections = [
            CollectionInfo(name="docs", doc_count=10, chunk_count=100),
        ]
        mock_store = AsyncMock()
        mock_store.list_collections = AsyncMock(return_value=collections)

        mock_state_store = MagicMock()
        mock_state_store.read.return_value = None  # no state file

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_running_service_info(pid=1234))
            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                    with patch("archon.ai.archon_toolkit_rag.IndexingStateStore", return_value=mock_state_store):
                        result = await _handle_rag_status(toolkit, {})

        data = json.loads(result)
        col = data["collections"][0]
        assert col["name"] == "docs"
        assert col["doc_count"] == 10
        assert col["chunk_count"] == 100
        assert "status" not in col
        assert "processed_files" not in col

    async def test_rag_status_merges_new_collections(self) -> None:
        """Collections in state but not in LanceDB are included in the response."""
        from archon.search.progress import CollectionProgress, IndexingState, IndexingStatus

        mock_cfg = MagicMock()
        mock_cfg.search.db_path = "/tmp/test_rag_db"
        toolkit = _make_toolkit(config=mock_cfg)

        # LanceDB has "docs", state has "docs" + "new_col" (being indexed)
        collections = [
            CollectionInfo(name="docs", doc_count=10, chunk_count=100),
        ]
        mock_store = AsyncMock()
        mock_store.list_collections = AsyncMock(return_value=collections)

        state = IndexingState(collections={
            "docs": CollectionProgress(
                status=IndexingStatus.DONE,
                total_files=10,
                processed_files=10,
            ),
            "new_col": CollectionProgress(
                status=IndexingStatus.IN_PROGRESS,
                total_files=5,
                processed_files=2,
            ),
        })

        mock_state_store = MagicMock()
        mock_state_store.read.return_value = state

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_running_service_info(pid=1234))
            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                    with patch("archon.ai.archon_toolkit_rag.IndexingStateStore", return_value=mock_state_store):
                        result = await _handle_rag_status(toolkit, {})

        data = json.loads(result)
        names = [c["name"] for c in data["collections"]]
        assert "new_col" in names
        new_col = next(c for c in data["collections"] if c["name"] == "new_col")
        assert new_col["status"] == "partial"
        assert new_col["processed_files"] == 2
        assert new_col["total_files"] == 5
        assert new_col["doc_count"] == 0
        assert new_col["chunk_count"] == 0

    async def test_rag_status_error_fields(self) -> None:
        """Failed collection includes error and error_count fields."""
        from archon.search.progress import CollectionProgress, IndexingState, IndexingStatus

        mock_cfg = MagicMock()
        mock_cfg.search.db_path = "/tmp/test_rag_db"
        toolkit = _make_toolkit(config=mock_cfg)

        collections = [
            CollectionInfo(name="broken", doc_count=3, chunk_count=20),
        ]
        mock_store = AsyncMock()
        mock_store.list_collections = AsyncMock(return_value=collections)

        state = IndexingState(collections={
            "broken": CollectionProgress(
                status=IndexingStatus.FAILED,
                total_files=10,
                processed_files=3,
                error="Embedding API timeout",
                error_count=7,
            ),
        })

        mock_state_store = MagicMock()
        mock_state_store.read.return_value = state

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_running_service_info(pid=1234))
            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                    with patch("archon.ai.archon_toolkit_rag.IndexingStateStore", return_value=mock_state_store):
                        result = await _handle_rag_status(toolkit, {})

        data = json.loads(result)
        col = data["collections"][0]
        assert col["error"] == "Embedding API timeout"
        assert col["error_count"] == 7
        assert col["status"] == "failed"

    async def test_mcp_status_partial(self) -> None:
        """IN_PROGRESS collection with processed_files > 0 → status 'partial'."""
        from archon.search.progress import CollectionProgress, IndexingState, IndexingStatus

        mock_cfg = MagicMock()
        mock_cfg.search.db_path = "/tmp/test_rag_db"
        toolkit = _make_toolkit(config=mock_cfg)

        collections = [
            CollectionInfo(name="docs", doc_count=30, chunk_count=300),
        ]
        mock_store = AsyncMock()
        mock_store.list_collections = AsyncMock(return_value=collections)

        state = IndexingState(collections={
            "docs": CollectionProgress(
                status=IndexingStatus.IN_PROGRESS,
                total_files=100,
                processed_files=50,
                error=None,
                error_count=0,
            ),
        })

        mock_state_store = MagicMock()
        mock_state_store.read.return_value = state

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_running_service_info(pid=1234))
            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                    with patch("archon.ai.archon_toolkit_rag.IndexingStateStore", return_value=mock_state_store):
                        result = await _handle_rag_status(toolkit, {})

        data = json.loads(result)
        col = data["collections"][0]
        assert col["status"] == "partial"

    async def test_mcp_status_in_progress_zero(self) -> None:
        """IN_PROGRESS collection with processed_files=0 → status remains 'in_progress'."""
        from archon.search.progress import CollectionProgress, IndexingState, IndexingStatus

        mock_cfg = MagicMock()
        mock_cfg.search.db_path = "/tmp/test_rag_db"
        toolkit = _make_toolkit(config=mock_cfg)

        collections = [
            CollectionInfo(name="docs", doc_count=0, chunk_count=0),
        ]
        mock_store = AsyncMock()
        mock_store.list_collections = AsyncMock(return_value=collections)

        state = IndexingState(collections={
            "docs": CollectionProgress(
                status=IndexingStatus.IN_PROGRESS,
                total_files=100,
                processed_files=0,
                error=None,
                error_count=0,
            ),
        })

        mock_state_store = MagicMock()
        mock_state_store.read.return_value = state

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_running_service_info(pid=1234))
            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                    with patch("archon.ai.archon_toolkit_rag.IndexingStateStore", return_value=mock_state_store):
                        result = await _handle_rag_status(toolkit, {})

        data = json.loads(result)
        col = data["collections"][0]
        assert col["status"] == "in_progress"

    async def test_mcp_status_state_only_in_progress_zero(self) -> None:
        """State-only path: IN_PROGRESS + processed_files=0 → status stays 'in_progress'."""
        from archon.search.progress import CollectionProgress, IndexingState, IndexingStatus

        mock_cfg = MagicMock()
        mock_cfg.search.db_path = "/tmp/test_rag_db"
        toolkit = _make_toolkit(config=mock_cfg)

        mock_store = AsyncMock()
        mock_store.list_collections = AsyncMock(return_value=[])  # nothing in LanceDB yet

        state = IndexingState(collections={
            "pending_col": CollectionProgress(
                status=IndexingStatus.IN_PROGRESS,
                total_files=50,
                processed_files=0,
                error=None,
                error_count=0,
            ),
        })

        mock_state_store = MagicMock()
        mock_state_store.read.return_value = state

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_running_service_info(pid=1234))
            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                    with patch("archon.ai.archon_toolkit_rag.IndexingStateStore", return_value=mock_state_store):
                        result = await _handle_rag_status(toolkit, {})

        data = json.loads(result)
        col = data["collections"][0]
        assert col["name"] == "pending_col"
        assert col["status"] == "in_progress"


# ---------------------------------------------------------------------------
# Task 4.7 — config params wired through RagCollectionSync constructors
# ---------------------------------------------------------------------------


class TestRagSyncPassesConfigParams:
    async def test_mcp_sync_passes_config_params(self) -> None:
        """_handle_rag_sync passes embedding_model, chunk_size, auto_reindex_on_chunk_size_change to RagCollectionSync."""
        mock_cfg = MagicMock()
        mock_cfg.search.collections = ["/some/path"]
        mock_cfg.search.db_path = "/tmp/test_rag_db"
        mock_cfg.search.pinned_collections = []
        mock_cfg.search.embedding_model = "my-embed-model"
        mock_cfg.search.chunk_size = 256
        mock_cfg.search.auto_reindex_on_chunk_size_change = True
        toolkit = _make_toolkit(config=mock_cfg)

        sync_result = _make_sync_result()

        mock_pipeline = AsyncMock()
        mock_pipeline.store = AsyncMock()

        mock_sync_instance = AsyncMock()
        mock_sync_instance.sync = AsyncMock(return_value=sync_result)

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_stopped_service_info())

            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                with patch("archon.ai.archon_toolkit_rag.create_pipeline", return_value=mock_pipeline):
                    with patch("archon.ai.archon_toolkit_rag.RagCollectionSync") as MockSync:
                        MockSync.return_value = mock_sync_instance
                        await _handle_rag_sync(toolkit, {})

        call_kwargs = MockSync.call_args[1]
        assert call_kwargs["embedding_model"] == "my-embed-model"
        assert call_kwargs["chunk_size"] == 256
        assert call_kwargs["auto_reindex_on_chunk_size_change"] is True


# ---------------------------------------------------------------------------
# Task 5.3 — _handle_rag_sync sets manual trigger before sync
# ---------------------------------------------------------------------------


class TestRagSyncManualTrigger:
    async def test_rag_sync_tool_sets_manual_trigger(self) -> None:
        """_handle_rag_sync calls state_store.set_trigger('manual') before sync.sync()."""
        mock_cfg = MagicMock()
        mock_cfg.search.collections = ["/some/path"]
        toolkit = _make_toolkit(config=mock_cfg)

        sync_result = _make_sync_result()
        call_order: list[str] = []

        mock_state_store = MagicMock()
        mock_state_store.set_trigger.side_effect = lambda t: call_order.append(f"set_trigger:{t}")

        mock_pipeline = AsyncMock()
        mock_pipeline.store = AsyncMock()

        mock_sync_instance = AsyncMock()
        mock_sync_instance.sync = AsyncMock(
            side_effect=lambda cols: (call_order.append("sync"), sync_result)[1]
        )

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_stopped_service_info())

            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                with patch("archon.ai.archon_toolkit_rag.create_pipeline", return_value=mock_pipeline):
                    with patch("archon.ai.archon_toolkit_rag.RagCollectionSync", return_value=mock_sync_instance):
                        with patch("archon.ai.archon_toolkit_rag.IndexingStateStore", return_value=mock_state_store):
                            await _handle_rag_sync(toolkit, {})

        mock_state_store.set_trigger.assert_called_once_with("manual")
        assert call_order.index("set_trigger:manual") < call_order.index("sync")

    async def test_rag_sync_tool_set_trigger_failure_does_not_prevent_sync(self) -> None:
        """If set_trigger('manual') raises, sync still runs and result is returned."""
        mock_cfg = MagicMock()
        mock_cfg.search.collections = ["/some/path"]
        toolkit = _make_toolkit(config=mock_cfg)

        sync_result = _make_sync_result(added=["col_a"])

        mock_state_store = MagicMock()
        mock_state_store.set_trigger.side_effect = OSError("disk full")

        mock_pipeline = AsyncMock()
        mock_pipeline.store = AsyncMock()

        mock_sync_instance = AsyncMock()
        mock_sync_instance.sync = AsyncMock(return_value=sync_result)

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_stopped_service_info())

            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                with patch("archon.ai.archon_toolkit_rag.create_pipeline", return_value=mock_pipeline):
                    with patch("archon.ai.archon_toolkit_rag.RagCollectionSync", return_value=mock_sync_instance):
                        with patch("archon.ai.archon_toolkit_rag.IndexingStateStore", return_value=mock_state_store):
                            result = await _handle_rag_sync(toolkit, {})

        # Sync must still run despite trigger failure
        mock_sync_instance.sync.assert_awaited_once()
        data = json.loads(result)
        assert data["added"] == ["col_a"]


# ---------------------------------------------------------------------------
# FEAT-027-P7 Task 7.3 — eta_seconds in rag_status MCP response
# ---------------------------------------------------------------------------


class TestRagStatusEta:
    """Tests for eta_seconds field in _handle_rag_status JSON response."""

    @staticmethod
    def _make_in_progress_state(processed: int = 20, total: int = 100) -> "IndexingState":
        from archon.search.progress import CollectionProgress, IndexingState, IndexingStatus
        return IndexingState(collections={
            "docs": CollectionProgress(
                status=IndexingStatus.IN_PROGRESS,
                total_files=total,
                processed_files=processed,
                started_at="2026-04-04T09:00:00+00:00",
            ),
        })

    @staticmethod
    def _make_toolkit_with_store(state, collections=None):
        from archon.search._types import CollectionInfo
        mock_cfg = MagicMock()
        mock_cfg.search.db_path = "/tmp/test_rag_db"
        toolkit = _make_toolkit(config=mock_cfg)

        if collections is None:
            collections = [CollectionInfo(name="docs", doc_count=10, chunk_count=100)]

        mock_store = AsyncMock()
        mock_store.list_collections = AsyncMock(return_value=collections)

        mock_state_store = MagicMock()
        mock_state_store.read.return_value = state

        return toolkit, mock_store, mock_state_store

    async def test_rag_status_mcp_includes_eta_seconds(self) -> None:
        """compute_eta_seconds returns 300 → collection dict contains 'eta_seconds': 300."""
        from archon.search.progress import CollectionProgress, IndexingStatus
        state = self._make_in_progress_state()
        toolkit, mock_store, mock_state_store = self._make_toolkit_with_store(state)

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_running_service_info(pid=1234))
            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                    with patch("archon.ai.archon_toolkit_rag.IndexingStateStore", return_value=mock_state_store):
                        with patch("archon.ai.archon_toolkit_rag.compute_eta_seconds", return_value=300) as mock_eta:
                            result = await _handle_rag_status(toolkit, {})

        data = json.loads(result)
        col = data["collections"][0]
        assert col["eta_seconds"] == 300
        # Verify compute_eta_seconds was called with the correct CollectionProgress
        mock_eta.assert_called_once()
        call_arg = mock_eta.call_args[0][0]
        assert isinstance(call_arg, CollectionProgress)
        assert call_arg.status == IndexingStatus.IN_PROGRESS

    async def test_rag_status_mcp_omits_eta_seconds_when_too_few(self) -> None:
        """compute_eta_seconds returns None → 'eta_seconds' key absent from collection dict."""
        state = self._make_in_progress_state()
        toolkit, mock_store, mock_state_store = self._make_toolkit_with_store(state)

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_running_service_info(pid=1234))
            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                    with patch("archon.ai.archon_toolkit_rag.IndexingStateStore", return_value=mock_state_store):
                        with patch("archon.ai.archon_toolkit_rag.compute_eta_seconds", return_value=None):
                            result = await _handle_rag_status(toolkit, {})

        data = json.loads(result)
        col = data["collections"][0]
        assert "eta_seconds" not in col

    @pytest.mark.parametrize("status_str", ["done", "failed", "pending"])
    async def test_rag_status_mcp_omits_eta_seconds_for_non_in_progress(
        self, status_str: str
    ) -> None:
        """DONE and FAILED collections: compute_eta_seconds returns None → no eta_seconds key."""
        from archon.search.progress import CollectionProgress, IndexingState, IndexingStatus
        state = IndexingState(collections={
            "docs": CollectionProgress(
                status=IndexingStatus(status_str),
                total_files=100,
                processed_files=80,
                started_at="2026-04-04T09:00:00+00:00",
            ),
        })
        toolkit, mock_store, mock_state_store = self._make_toolkit_with_store(state)

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_running_service_info(pid=1234))
            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                    with patch("archon.ai.archon_toolkit_rag.IndexingStateStore", return_value=mock_state_store):
                        with patch("archon.ai.archon_toolkit_rag.compute_eta_seconds", return_value=None):
                            result = await _handle_rag_status(toolkit, {})

        data = json.loads(result)
        col = data["collections"][0]
        assert "eta_seconds" not in col

    async def test_rag_status_mcp_no_eta_when_no_state_for_collection(self) -> None:
        """LanceDB collection with no matching state entry → no eta_seconds, no status fields."""
        from archon.search.progress import IndexingState
        # State file is empty — no matching entry for the LanceDB collection
        state = IndexingState(collections={})
        toolkit, mock_store, mock_state_store = self._make_toolkit_with_store(state)

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_running_service_info(pid=1234))
            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                    with patch("archon.ai.archon_toolkit_rag.IndexingStateStore", return_value=mock_state_store):
                        with patch("archon.ai.archon_toolkit_rag.compute_eta_seconds") as mock_eta:
                            result = await _handle_rag_status(toolkit, {})

        data = json.loads(result)
        col = data["collections"][0]
        assert "eta_seconds" not in col
        assert "status" not in col
        # compute_eta_seconds is not called when no state entry for the collection
        mock_eta.assert_not_called()

    async def test_rag_status_mcp_includes_eta_seconds_state_only(self) -> None:
        """IN_PROGRESS collection in state-only block (not in LanceDB) → eta_seconds included."""
        from archon.search.progress import CollectionProgress, IndexingState, IndexingStatus
        # State has "new_col" not in LanceDB
        state = IndexingState(collections={
            "new_col": CollectionProgress(
                status=IndexingStatus.IN_PROGRESS,
                total_files=100,
                processed_files=20,
                started_at="2026-04-04T09:00:00+00:00",
            ),
        })
        # LanceDB has no collections
        mock_cfg = MagicMock()
        mock_cfg.search.db_path = "/tmp/test_rag_db"
        toolkit = _make_toolkit(config=mock_cfg)

        mock_store = AsyncMock()
        mock_store.list_collections = AsyncMock(return_value=[])

        mock_state_store = MagicMock()
        mock_state_store.read.return_value = state

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_running_service_info(pid=1234))
            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                    with patch("archon.ai.archon_toolkit_rag.IndexingStateStore", return_value=mock_state_store):
                        with patch("archon.ai.archon_toolkit_rag.compute_eta_seconds", return_value=300) as mock_eta:
                            result = await _handle_rag_status(toolkit, {})

        data = json.loads(result)
        col = data["collections"][0]
        assert col["name"] == "new_col"
        assert col["eta_seconds"] == 300
        # Verify compute_eta_seconds was called with the correct CollectionProgress
        mock_eta.assert_called_once()
        from archon.search.progress import CollectionProgress, IndexingStatus
        call_arg = mock_eta.call_args[0][0]
        assert isinstance(call_arg, CollectionProgress)
        assert call_arg.status == IndexingStatus.IN_PROGRESS


def test_rag_status_schema_description_mentions_eta_seconds() -> None:
    """AC: _RAG_STATUS_SCHEMA description mentions eta_seconds for tool discoverability."""
    from archon.ai.archon_toolkit_rag import _RAG_STATUS_SCHEMA
    assert "eta_seconds" in _RAG_STATUS_SCHEMA["description"]


# ---------------------------------------------------------------------------
# FEAT-027-P8 Task 8.6 — watching field in rag_status MCP response
# ---------------------------------------------------------------------------


class TestRagStatusWatching:
    """Tests for watching field in rag_status MCP response (FEAT-027-P8 Task 8.6)."""

    def _make_collection_info(self, name: str = "my-docs") -> "CollectionInfo":
        from archon.search._types import CollectionInfo
        return CollectionInfo(name=name, doc_count=5, chunk_count=20)

    async def test_rag_status_mcp_includes_watching_true(self) -> None:
        """cfg.rag.watch=True → each collection dict contains watching=True."""
        mock_cfg = MagicMock()
        mock_cfg.search.db_path = "/tmp/test_rag_db"
        mock_cfg.search.watch = True
        toolkit = _make_toolkit(config=mock_cfg)

        col_info = self._make_collection_info()
        mock_store = AsyncMock()
        mock_store.list_collections = AsyncMock(return_value=[col_info])

        mock_state_store = MagicMock()
        mock_state_store.read.return_value = None

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_running_service_info(pid=1234))
            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                    with patch("archon.ai.archon_toolkit_rag.IndexingStateStore", return_value=mock_state_store):
                        result = await _handle_rag_status(toolkit, {})

        data = json.loads(result)
        assert len(data["collections"]) == 1
        assert data["collections"][0]["watching"] is True

    async def test_rag_status_mcp_includes_watching_false(self) -> None:
        """cfg.rag.watch=False → each collection dict contains watching=False."""
        mock_cfg = MagicMock()
        mock_cfg.search.db_path = "/tmp/test_rag_db"
        mock_cfg.search.watch = False
        toolkit = _make_toolkit(config=mock_cfg)

        col_info = self._make_collection_info()
        mock_store = AsyncMock()
        mock_store.list_collections = AsyncMock(return_value=[col_info])

        mock_state_store = MagicMock()
        mock_state_store.read.return_value = None

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_running_service_info(pid=1234))
            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                    with patch("archon.ai.archon_toolkit_rag.IndexingStateStore", return_value=mock_state_store):
                        result = await _handle_rag_status(toolkit, {})

        data = json.loads(result)
        assert len(data["collections"]) == 1
        assert data["collections"][0]["watching"] is False

    def test_rag_status_schema_description_mentions_watching(self) -> None:
        """_RAG_STATUS_SCHEMA description mentions watching for tool discoverability."""
        from archon.ai.archon_toolkit_rag import _RAG_STATUS_SCHEMA
        assert "watching" in _RAG_STATUS_SCHEMA["description"]

    async def test_rag_status_mcp_includes_watching_state_only(self) -> None:
        """cfg.rag.watch=True + state-only collection (not in LanceDB) → watching=True in entry."""
        from archon.search.progress import CollectionProgress, IndexingState, IndexingStatus

        mock_cfg = MagicMock()
        mock_cfg.search.db_path = "/tmp/test_rag_db"
        mock_cfg.search.watch = True
        toolkit = _make_toolkit(config=mock_cfg)

        mock_store = AsyncMock()
        mock_store.list_collections = AsyncMock(return_value=[])

        state = IndexingState(collections={
            "state-only-col": CollectionProgress(
                status=IndexingStatus.DONE,
                total_files=10,
                processed_files=10,
            ),
        })
        mock_state_store = MagicMock()
        mock_state_store.read.return_value = state

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_running_service_info(pid=42))
            with patch("archon.ai.archon_toolkit_rag.get_search_service", return_value=MagicMock()):
                with patch("archon.ai.archon_toolkit_rag.SearchStore", return_value=mock_store):
                    with patch("archon.ai.archon_toolkit_rag.IndexingStateStore", return_value=mock_state_store):
                        result = await _handle_rag_status(toolkit, {})

        data = json.loads(result)
        assert len(data["collections"]) == 1
        col = data["collections"][0]
        assert col["name"] == "state-only-col"
        assert col["watching"] is True
