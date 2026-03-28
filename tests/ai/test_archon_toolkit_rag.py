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
