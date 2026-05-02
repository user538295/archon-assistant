"""Tests for SearchClient — HTTP client adapter for archon-search service (FEAT-038 Task 7.1)."""
from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from archon_search.types import IngestJob, JobStatus, RouteResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(base_url: str = "http://localhost:8282") -> "SearchClient":
    from archon.ai.search_client import SearchClient

    return SearchClient(base_url=base_url)


def _mock_response(status_code: int = 200, json_data: object = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_data or {})
    if status_code >= 400:
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                message=f"HTTP {status_code}",
                request=MagicMock(),
                response=resp,
            )
        )
    else:
        resp.raise_for_status = MagicMock()
    return resp


# Shared IngestJob JSON for reuse in multiple tests
_INGEST_JOB_DATA = {
    "job_id": "abc123",
    "status": "PENDING",
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
    "result": None,
    "error": None,
}


# ---------------------------------------------------------------------------
# route()
# ---------------------------------------------------------------------------


class TestRoute:
    @pytest.mark.asyncio
    async def test_route_calls_post_route(self) -> None:
        """route() POSTs to /route with query and optional slots."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        route_data = {
            "pre_context": "some context",
            "pinned_names": ["col1"],
            "routable_names": ["col2"],
            "decomposer_invoked": True,
        }
        mock_resp = _mock_response(200, route_data)

        with patch.object(client._http, "post", new=AsyncMock(return_value=mock_resp)) as mock_post:
            result = await client.route("find me something", slots=3)

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "/route" in call_kwargs[0][0] or "/route" in str(call_kwargs)
        assert result is not None

    @pytest.mark.asyncio
    async def test_route_parses_pre_context_and_router_state(self) -> None:
        """route() returns RouteResponse with pre_context, pinned_names, routable_names, decomposer_invoked."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        route_data = {
            "pre_context": "context text",
            "pinned_names": ["pinned"],
            "routable_names": ["route1", "route2"],
            "decomposer_invoked": False,
        }
        mock_resp = _mock_response(200, route_data)

        with patch.object(client._http, "post", new=AsyncMock(return_value=mock_resp)):
            result = await client.route("my query")

        assert isinstance(result, RouteResponse)
        assert result.pre_context == "context text"
        assert result.pinned_names == ["pinned"]
        assert result.routable_names == ["route1", "route2"]
        assert result.decomposer_invoked is False

    @pytest.mark.asyncio
    async def test_route_connection_refused_returns_none(self) -> None:
        """route() returns None on ConnectError (connection refused)."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")

        with patch.object(
            client._http,
            "post",
            new=AsyncMock(side_effect=httpx.ConnectError("Connection refused")),
        ):
            result = await client.route("query")

        assert result is None

    @pytest.mark.asyncio
    async def test_route_timeout_returns_none(self, caplog: pytest.LogCaptureFixture) -> None:
        """route() returns None and logs WARNING on timeout."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")

        with patch.object(
            client._http,
            "post",
            new=AsyncMock(side_effect=httpx.TimeoutException("timeout")),
        ):
            with caplog.at_level(logging.WARNING, logger="archon"):
                result = await client.route("query")

        assert result is None
        assert any("timeout" in r.message.lower() or "timed out" in r.message.lower() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_route_5xx_returns_none(self, caplog: pytest.LogCaptureFixture) -> None:
        """route() returns None and logs WARNING on 5xx response."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        mock_resp = _mock_response(500, {})

        with patch.object(client._http, "post", new=AsyncMock(return_value=mock_resp)):
            with caplog.at_level(logging.WARNING, logger="archon"):
                result = await client.route("query")

        assert result is None
        assert any("500" in r.message or "HTTP" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_route_malformed_json_returns_none(self, caplog: pytest.LogCaptureFixture) -> None:
        """route() returns None on malformed/unexpected JSON."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(side_effect=json.JSONDecodeError("invalid json", "", 0))

        with patch.object(client._http, "post", new=AsyncMock(return_value=mock_resp)):
            with caplog.at_level(logging.WARNING, logger="archon"):
                result = await client.route("query")

        assert result is None
        assert any(r.levelno >= logging.WARNING for r in caplog.records)


# ---------------------------------------------------------------------------
# health()
# ---------------------------------------------------------------------------


class TestHealth:
    @pytest.mark.asyncio
    async def test_health_returns_dict_on_success(self) -> None:
        """health() returns a dict on 200 response."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        health_data = {"status": "ok", "version": "1.0.0"}
        mock_resp = _mock_response(200, health_data)

        with patch.object(client._http, "get", new=AsyncMock(return_value=mock_resp)):
            result = await client.health()

        assert isinstance(result, dict)
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_health_not_running_returns_none(self) -> None:
        """health() returns None when service is not running (connection refused)."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")

        with patch.object(
            client._http,
            "get",
            new=AsyncMock(side_effect=httpx.ConnectError("Connection refused")),
        ):
            result = await client.health()

        assert result is None


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------


class TestStatus:
    @pytest.mark.asyncio
    async def test_status_returns_rich_status_on_success(self) -> None:
        """status() returns dict with service and collection state fields."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        status_data = {
            "running": True,
            "collections": [{"name": "docs", "doc_count": 10}],
            "version": "1.0.0",
        }
        mock_resp = _mock_response(200, status_data)

        with patch.object(client._http, "get", new=AsyncMock(return_value=mock_resp)):
            result = await client.status()

        assert isinstance(result, dict)
        assert "running" in result
        assert "collections" in result


# ---------------------------------------------------------------------------
# indexing_state()
# ---------------------------------------------------------------------------


class TestIndexingState:
    @pytest.mark.asyncio
    async def test_indexing_state_returns_dict(self) -> None:
        """indexing_state() returns dict on 200 response."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        state_data = {"collections": {"docs": "DONE"}}
        mock_resp = _mock_response(200, state_data)

        with patch.object(client._http, "get", new=AsyncMock(return_value=mock_resp)):
            result = await client.indexing_state()

        assert isinstance(result, dict)
        assert "collections" in result


# ---------------------------------------------------------------------------
# ingest()
# ---------------------------------------------------------------------------


class TestIngest:
    @pytest.mark.asyncio
    async def test_ingest_returns_ingest_job(self) -> None:
        """ingest() returns IngestJob on 202 response."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        mock_resp = _mock_response(202, _INGEST_JOB_DATA)

        with patch.object(client._http, "post", new=AsyncMock(return_value=mock_resp)):
            result = await client.ingest(collection="docs", path="/some/path")

        assert isinstance(result, IngestJob)
        assert result.job_id == "abc123"
        assert result.status == JobStatus.PENDING


# ---------------------------------------------------------------------------
# list_collections()
# ---------------------------------------------------------------------------


class TestListCollections:
    @pytest.mark.asyncio
    async def test_list_collections_returns_list(self) -> None:
        """list_collections() returns list of dicts on 200 response."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        collections_data = [{"name": "docs", "doc_count": 5}, {"name": "code", "doc_count": 10}]
        mock_resp = _mock_response(200, collections_data)

        with patch.object(client._http, "get", new=AsyncMock(return_value=mock_resp)):
            result = await client.list_collections()

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["name"] == "docs"


# ---------------------------------------------------------------------------
# add_collection()
# ---------------------------------------------------------------------------


class TestAddCollection:
    @pytest.mark.asyncio
    async def test_add_collection_returns_dict(self) -> None:
        """add_collection() returns dict on 200 response."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        response_data = {"name": "docs", "path": "/some/path"}
        mock_resp = _mock_response(200, response_data)

        with patch.object(client._http, "post", new=AsyncMock(return_value=mock_resp)):
            result = await client.add_collection(path="/some/path")

        assert isinstance(result, dict)
        assert result["path"] == "/some/path"


# ---------------------------------------------------------------------------
# remove_collection()
# ---------------------------------------------------------------------------


class TestRemoveCollection:
    @pytest.mark.asyncio
    async def test_remove_collection_returns_dict(self) -> None:
        """remove_collection() returns dict on 200 response."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        response_data = {"name": "docs", "removed": True}
        mock_resp = _mock_response(200, response_data)

        with patch.object(client._http, "delete", new=AsyncMock(return_value=mock_resp)):
            result = await client.remove_collection("docs")

        assert isinstance(result, dict)
        assert result["name"] == "docs"


# ---------------------------------------------------------------------------
# collection_info()
# ---------------------------------------------------------------------------


class TestCollectionInfo:
    @pytest.mark.asyncio
    async def test_collection_info_returns_dict(self) -> None:
        """collection_info() returns dict on 200 response."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        info_data = {"name": "docs", "doc_count": 42, "path": "/some/path"}
        mock_resp = _mock_response(200, info_data)

        with patch.object(client._http, "get", new=AsyncMock(return_value=mock_resp)):
            result = await client.collection_info("docs")

        assert isinstance(result, dict)
        assert result["name"] == "docs"
        assert result["doc_count"] == 42


# ---------------------------------------------------------------------------
# reindex_collection()
# ---------------------------------------------------------------------------


class TestReindexCollection:
    @pytest.mark.asyncio
    async def test_reindex_collection_returns_ingest_job(self) -> None:
        """reindex_collection() returns IngestJob on 200 response."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        mock_resp = _mock_response(200, _INGEST_JOB_DATA)

        with patch.object(client._http, "post", new=AsyncMock(return_value=mock_resp)):
            result = await client.reindex_collection("docs")

        assert isinstance(result, IngestJob)
        assert result.job_id == "abc123"
        assert result.status == JobStatus.PENDING


# ---------------------------------------------------------------------------
# get_job()
# ---------------------------------------------------------------------------


class TestGetJob:
    @pytest.mark.asyncio
    async def test_get_job_returns_ingest_job(self) -> None:
        """get_job() returns IngestJob on 200 response."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        mock_resp = _mock_response(200, _INGEST_JOB_DATA)

        with patch.object(client._http, "get", new=AsyncMock(return_value=mock_resp)):
            result = await client.get_job("abc123")

        assert isinstance(result, IngestJob)
        assert result.job_id == "abc123"
        assert result.status == JobStatus.PENDING


# ---------------------------------------------------------------------------
# cancel_job()
# ---------------------------------------------------------------------------


class TestCancelJob:
    @pytest.mark.asyncio
    async def test_cancel_job_returns_status_code(self) -> None:
        """cancel_job() returns the HTTP status code."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        mock_resp = _mock_response(204, None)
        mock_resp.raise_for_status = MagicMock()  # override — 204 is success

        with patch.object(client._http, "delete", new=AsyncMock(return_value=mock_resp)):
            code = await client.cancel_job("abc123")

        assert code == 204

    @pytest.mark.asyncio
    async def test_cancel_job_timeout_returns_408(self) -> None:
        """cancel_job() returns 408 on timeout."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")

        with patch.object(
            client._http,
            "delete",
            new=AsyncMock(side_effect=httpx.TimeoutException("timed out")),
        ):
            code = await client.cancel_job("abc123")

        assert code == 408


# ---------------------------------------------------------------------------
# get_search_client() singleton factory
# ---------------------------------------------------------------------------


class TestGetSearchClient:
    def test_get_search_client_returns_search_client(self) -> None:
        """get_search_client() returns a SearchClient instance."""
        from archon.ai import search_client as sc_module
        from archon.ai.search_client import SearchClient, get_search_client

        # Reset singleton before test
        sc_module._search_client = None

        mock_cfg = MagicMock()
        mock_cfg.search.host = "localhost"
        mock_cfg.search.port = 8282

        with patch("archon.ai.search_client.config", mock_cfg):
            client = get_search_client()

        assert isinstance(client, SearchClient)

        # Cleanup
        sc_module._search_client = None

    def test_get_search_client_singleton(self) -> None:
        """get_search_client() returns the same instance on repeated calls."""
        from archon.ai import search_client as sc_module

        mock_cfg = MagicMock()
        mock_cfg.search.host = "localhost"
        mock_cfg.search.port = 8282

        # Reset the singleton
        sc_module._search_client = None

        with patch("archon.ai.search_client.config", mock_cfg):
            client1 = sc_module.get_search_client()
            client2 = sc_module.get_search_client()

        assert client1 is client2

        # Cleanup
        sc_module._search_client = None
