"""Tests for archon_toolkit_rag — rag_status MCP tool (Task 1.1)."""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from archon.ai.archon_toolkit import ArchonToolkit
import archon.ai.archon_toolkit_rag as rag_module
from archon.ai.archon_toolkit_rag import _handle_rag_status
from archon.platform.types import ServiceInfo
from archon.rag._types import CollectionInfo


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

            with patch("archon.ai.archon_toolkit_rag.get_rag_service", return_value=mock_service):
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
        mock_cfg.rag.db_path = "/tmp/test_rag_db"
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

            with patch("archon.ai.archon_toolkit_rag.get_rag_service", return_value=mock_service):
                with patch("archon.ai.archon_toolkit_rag.RagStore", return_value=mock_store):
                    result = await _handle_rag_status(toolkit, {})

        data = json.loads(result)
        assert data["running"] is True
        assert data["pid"] == 1234
        assert len(data["collections"]) == 2
        assert data["collections"][0] == {"name": "docs", "doc_count": 10, "chunk_count": 100}
        assert data["collections"][1] == {"name": "notes", "doc_count": 5, "chunk_count": 42}
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

            with patch("archon.ai.archon_toolkit_rag.get_rag_service", return_value=mock_service):
                with patch("archon.ai.archon_toolkit_rag.RagStore") as mock_rag_store_cls:
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
        mock_cfg.rag.db_path = "/tmp/test_rag_db"
        toolkit = _make_toolkit(config=mock_cfg)

        mock_store = AsyncMock()
        mock_store.list_collections = AsyncMock(side_effect=RuntimeError("DB error"))

        mock_service = MagicMock()
        mock_service.status = MagicMock(return_value=_running_service_info(pid=5678))

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_running_service_info(pid=5678))

            with patch("archon.ai.archon_toolkit_rag.get_rag_service", return_value=mock_service):
                with patch("archon.ai.archon_toolkit_rag.RagStore", return_value=mock_store):
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
        mock_cfg.rag.db_path = "/tmp/test_rag_db"
        toolkit = _make_toolkit(config=mock_cfg)

        mock_store = AsyncMock()
        mock_store.connect = AsyncMock(side_effect=RuntimeError("Connection refused"))

        mock_service = MagicMock()
        mock_service.status = MagicMock(return_value=_running_service_info(pid=9999))

        with patch("archon.ai.archon_toolkit_rag.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=_running_service_info(pid=9999))

            with patch("archon.ai.archon_toolkit_rag.get_rag_service", return_value=mock_service):
                with patch("archon.ai.archon_toolkit_rag.RagStore", return_value=mock_store):
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

            with patch("archon.ai.archon_toolkit_rag.get_rag_service", return_value=mock_service):
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

            with patch("archon.ai.archon_toolkit_rag.get_rag_service", return_value=mock_service):
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

            with patch("archon.ai.archon_toolkit_rag.get_rag_service", return_value=mock_service):
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

            with patch("archon.ai.archon_toolkit_rag.get_rag_service", return_value=mock_service):
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

            with patch("archon.ai.archon_toolkit_rag.get_rag_service", return_value=mock_service):
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

            with patch("archon.ai.archon_toolkit_rag.get_rag_service", return_value=mock_service):
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
from archon.rag._types import IngestResult  # noqa: E402


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

            with patch("archon.ai.archon_toolkit_rag.get_rag_service", return_value=mock_service):
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

            with patch("archon.ai.archon_toolkit_rag.get_rag_service", return_value=mock_service):
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

            with patch("archon.ai.archon_toolkit_rag.get_rag_service", return_value=mock_service):
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

            with patch("archon.ai.archon_toolkit_rag.get_rag_service", return_value=mock_service):
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

            with patch("archon.ai.archon_toolkit_rag.get_rag_service", return_value=mock_service):
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

            with patch("archon.ai.archon_toolkit_rag.get_rag_service", return_value=mock_service):
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

            with patch("archon.ai.archon_toolkit_rag.get_rag_service", return_value=mock_service):
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
