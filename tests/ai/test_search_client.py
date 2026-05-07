"""Tests for SearchClient — HTTP client adapter for archon-search service (FEAT-038 Task 7.1)."""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Generator
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
        mock_cfg.search.url = "http://localhost:8282"

        with patch("archon.ai.search_client.config", mock_cfg):
            client = get_search_client()

        assert isinstance(client, SearchClient)

        # Cleanup
        sc_module._search_client = None

    def test_get_search_client_singleton(self) -> None:
        """get_search_client() returns the same instance on repeated calls."""
        from archon.ai import search_client as sc_module

        mock_cfg = MagicMock()
        mock_cfg.search.url = "http://localhost:8282"

        # Reset the singleton
        sc_module._search_client = None

        with patch("archon.ai.search_client.config", mock_cfg):
            client1 = sc_module.get_search_client()
            client2 = sc_module.get_search_client()

        assert client1 is client2

        # Cleanup
        sc_module._search_client = None


class TestGetSearchClientAsync:
    """A10.40–A10.42: async get_search_client / reset_search_client behaviour."""

    @pytest.fixture(autouse=True)
    async def reset_singleton(self) -> AsyncGenerator[None, None]:
        from archon.ai.search_client import reset_search_client

        yield
        await reset_search_client()

    @pytest.mark.asyncio
    async def test_get_search_client_uses_config_url(self) -> None:
        """A10.40: config with search.url + singleton is None → returns SearchClient with correct base_url."""
        from archon.ai import search_client as sc_module
        from archon.ai.search_client import SearchClient, get_search_client

        sc_module._search_client = None

        mock_cfg = MagicMock()
        mock_cfg.search.url = "http://test-host:9999"

        with patch("archon.ai.search_client.config", mock_cfg):
            client = get_search_client()

        assert isinstance(client, SearchClient)
        assert client._base_url == "http://test-host:9999"

    @pytest.mark.asyncio
    async def test_get_search_client_returns_same_instance(self) -> None:
        """A10.41: calling get_search_client() twice without reset returns same object."""
        from archon.ai import search_client as sc_module
        from archon.ai.search_client import get_search_client

        sc_module._search_client = None

        mock_cfg = MagicMock()
        mock_cfg.search.url = "http://localhost:8765"

        with patch("archon.ai.search_client.config", mock_cfg):
            client1 = get_search_client()
            client2 = get_search_client()

        assert client1 is client2

    @pytest.mark.asyncio
    async def test_reset_search_client_creates_new_instance(self) -> None:
        """A10.42: call get_search_client(), reset, call again → new (different) object."""
        from archon.ai import search_client as sc_module
        from archon.ai.search_client import get_search_client, reset_search_client

        sc_module._search_client = None

        mock_cfg = MagicMock()
        mock_cfg.search.url = "http://localhost:8765"

        with patch("archon.ai.search_client.config", mock_cfg):
            client1 = get_search_client()
            await reset_search_client()
            client2 = get_search_client()

        assert id(client1) != id(client2)


# ---------------------------------------------------------------------------
# transport parameter
# ---------------------------------------------------------------------------


class TestTransportParam:
    def test_transport_param_forwarded_to_http_client(self) -> None:
        """SearchClient forwards the transport param to the underlying httpx.AsyncClient."""
        from archon.ai.search_client import SearchClient

        class _MinimalTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                return httpx.Response(200)

        transport = _MinimalTransport()
        client = SearchClient(base_url="http://localhost:8282", transport=transport)
        assert client._http._transport is transport


# ---------------------------------------------------------------------------
# Suite 10 — SearchClient Error Branches (A10.1–A10.27b)
# ---------------------------------------------------------------------------


class TestHealthErrorBranches:
    """A10.1–A10.3: health() error paths."""

    @pytest.mark.asyncio
    async def test_health_timeout_returns_none(self) -> None:
        """A10.1: health() returns None on TimeoutException."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        with patch.object(client._http, "get", new=AsyncMock(side_effect=httpx.TimeoutException("timed out"))):
            result = await client.health()
        assert result is None

    @pytest.mark.asyncio
    async def test_health_connect_error_returns_none(self) -> None:
        """A10.2: health() returns None on ConnectError."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        with patch.object(client._http, "get", new=AsyncMock(side_effect=httpx.ConnectError("refused"))):
            result = await client.health()
        assert result is None

    @pytest.mark.asyncio
    async def test_health_5xx_returns_none(self) -> None:
        """A10.3: health() returns None on 5xx response."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        mock_resp = _mock_response(500, {})
        with patch.object(client._http, "get", new=AsyncMock(return_value=mock_resp)):
            result = await client.health()
        assert result is None


class TestStatusErrorBranches:
    """A10.4–A10.7: status() and indexing_state() error paths."""

    @pytest.mark.asyncio
    async def test_status_timeout_returns_none(self) -> None:
        """A10.4: status() returns None on TimeoutException."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        with patch.object(client._http, "get", new=AsyncMock(side_effect=httpx.TimeoutException("timed out"))):
            result = await client.status()
        assert result is None

    @pytest.mark.asyncio
    async def test_status_connect_error_returns_none(self) -> None:
        """A10.5: status() returns None on ConnectError."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        with patch.object(client._http, "get", new=AsyncMock(side_effect=httpx.ConnectError("refused"))):
            result = await client.status()
        assert result is None

    @pytest.mark.asyncio
    async def test_indexing_state_timeout_returns_none(self) -> None:
        """A10.6: indexing_state() returns None on TimeoutException."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        with patch.object(client._http, "get", new=AsyncMock(side_effect=httpx.TimeoutException("timed out"))):
            result = await client.indexing_state()
        assert result is None

    @pytest.mark.asyncio
    async def test_indexing_state_connect_error_returns_none(self) -> None:
        """A10.7: indexing_state() returns None on ConnectError (not {})."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        with patch.object(client._http, "get", new=AsyncMock(side_effect=httpx.ConnectError("refused"))):
            result = await client.indexing_state()
        # ConnectError → returns None (the method's except clause returns None)
        assert result is None


class TestIngestErrorBranches:
    """A10.8–A10.11: ingest() error and edge-case paths."""

    @pytest.mark.asyncio
    async def test_ingest_timeout_returns_none(self) -> None:
        """A10.8: ingest() returns None on TimeoutException."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        with patch.object(client._http, "post", new=AsyncMock(side_effect=httpx.TimeoutException("timed out"))):
            result = await client.ingest(collection="docs", path="/some/path")
        assert result is None

    @pytest.mark.asyncio
    async def test_ingest_connect_error_returns_none(self) -> None:
        """A10.9: ingest() returns None on ConnectError."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        with patch.object(client._http, "post", new=AsyncMock(side_effect=httpx.ConnectError("refused"))):
            result = await client.ingest(collection="docs", path="/some/path")
        assert result is None

    @pytest.mark.asyncio
    async def test_ingest_without_path_returns_none_on_error(self) -> None:
        """A10.10: ingest(path=None) returns None on failure (path omitted from payload)."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        with patch.object(client._http, "post", new=AsyncMock(side_effect=httpx.ConnectError("refused"))) as mock_post:
            result = await client.ingest(collection="docs", path=None)
        assert result is None
        # Verify path was NOT included in the payload
        posted_json = mock_post.call_args.kwargs["json"]
        assert "path" not in posted_json

    @pytest.mark.asyncio
    async def test_ingest_payload_has_no_ingested_by(self) -> None:
        """A10.11: ingest() POST payload does not include an 'ingested_by' key."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        mock_resp = _mock_response(202, _INGEST_JOB_DATA)
        with patch.object(client._http, "post", new=AsyncMock(return_value=mock_resp)) as mock_post:
            await client.ingest(collection="docs", path="/some/path")
        posted_json = mock_post.call_args.kwargs["json"]
        assert "ingested_by" not in posted_json


class TestGetJobAndCancelJobErrorBranches:
    """A10.12–A10.14: get_job() and cancel_job() error paths."""

    @pytest.mark.asyncio
    async def test_get_job_timeout_returns_none(self) -> None:
        """A10.12: get_job() returns None on TimeoutException."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        with patch.object(client._http, "get", new=AsyncMock(side_effect=httpx.TimeoutException("timed out"))):
            result = await client.get_job("abc123")
        assert result is None

    @pytest.mark.asyncio
    async def test_cancel_job_connect_error_returns_503(self) -> None:
        """A10.13: cancel_job() returns 503 on ConnectError."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        with patch.object(client._http, "delete", new=AsyncMock(side_effect=httpx.ConnectError("refused"))):
            code = await client.cancel_job("abc123")
        assert code == 503

    @pytest.mark.asyncio
    async def test_cancel_job_4xx_no_warning_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """A10.14: cancel_job() 4xx does NOT log a WARNING (only 5xx does)."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        mock_resp = _mock_response(404, {})
        mock_resp.raise_for_status = MagicMock()  # 404 but no raise_for_status called by cancel_job
        mock_resp.status_code = 404
        with patch.object(client._http, "delete", new=AsyncMock(return_value=mock_resp)):
            with caplog.at_level(logging.WARNING, logger="archon"):
                code = await client.cancel_job("abc123")
        assert code == 404
        # No WARNING should have been logged for a 4xx response
        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warning_records) == 0


class TestListCollectionsErrorBranches:
    """A10.15–A10.16: list_collections() error paths."""

    @pytest.mark.asyncio
    async def test_list_collections_timeout_returns_empty_list(self) -> None:
        """A10.15: list_collections() returns [] on TimeoutException."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        with patch.object(client._http, "get", new=AsyncMock(side_effect=httpx.TimeoutException("timed out"))):
            result = await client.list_collections()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_collections_connect_error_returns_empty_list(self) -> None:
        """A10.16: list_collections() returns [] on ConnectError."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        with patch.object(client._http, "get", new=AsyncMock(side_effect=httpx.ConnectError("refused"))):
            result = await client.list_collections()
        assert result == []


class TestCollectionMethodsErrorBranches:
    """A10.17–A10.20: add/remove/info/reindex collection timeout paths."""

    @pytest.mark.asyncio
    async def test_add_collection_timeout_returns_none(self) -> None:
        """A10.17: add_collection() returns None on TimeoutException."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        with patch.object(client._http, "post", new=AsyncMock(side_effect=httpx.TimeoutException("timed out"))):
            result = await client.add_collection(path="/some/path")
        assert result is None

    @pytest.mark.asyncio
    async def test_remove_collection_timeout_returns_none(self) -> None:
        """A10.18: remove_collection() returns None on TimeoutException."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        with patch.object(client._http, "delete", new=AsyncMock(side_effect=httpx.TimeoutException("timed out"))):
            result = await client.remove_collection("docs")
        assert result is None

    @pytest.mark.asyncio
    async def test_collection_info_timeout_returns_none(self) -> None:
        """A10.19: collection_info() returns None on TimeoutException."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        with patch.object(client._http, "get", new=AsyncMock(side_effect=httpx.TimeoutException("timed out"))):
            result = await client.collection_info("docs")
        assert result is None

    @pytest.mark.asyncio
    async def test_reindex_collection_timeout_returns_none(self) -> None:
        """A10.20: reindex_collection() returns None on TimeoutException."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        with patch.object(client._http, "post", new=AsyncMock(side_effect=httpx.TimeoutException("timed out"))):
            result = await client.reindex_collection("docs")
        assert result is None


class TestRoutePayloadAndASGI:
    """A10.21 and A10.21b: route() payload shape and real ASGI integration."""

    @pytest.mark.asyncio
    async def test_route_no_slots_omits_slots_key_from_payload(self) -> None:
        """A10.21: route(query, slots=None) does NOT include 'slots' key in POST payload."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        route_data = {
            "pre_context": "ctx",
            "pinned_names": [],
            "routable_names": [],
            "decomposer_invoked": False,
        }
        mock_resp = _mock_response(200, route_data)

        with patch.object(client._http, "post", new=AsyncMock(return_value=mock_resp)) as mock_post:
            await client.route("my query", slots=None)

        call_kwargs = mock_post.call_args
        posted_json = call_kwargs[1]["json"]
        assert "slots" not in posted_json
        assert posted_json["query"] == "my query"

    @pytest.mark.asyncio
    async def test_route_asgi_all_fields_populated(self, tmp_path) -> None:
        """A10.21b: real in-process FastAPI — RouteResponse fields correctly populated."""
        from archon_search.server.app import create_app
        from archon_search.config import SearchConfig
        from archon_search.jobs.store import JobStore
        from archon.ai.search_client import SearchClient

        config = SearchConfig(db_path=str(tmp_path / "search_db"))
        job_store = JobStore(tmp_path / "jobs.json")
        app = create_app(config, job_store, config_path=tmp_path / "config.toml")

        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            client = SearchClient("http://test", transport=transport)
            client._http.follow_redirects = True
            try:
                result = await client.route("find something useful")
            finally:
                await client.close()

        # With empty collections, route() still returns a valid RouteResponse (empty fields)
        assert result is not None
        assert isinstance(result, RouteResponse)
        assert hasattr(result, "pre_context")
        assert hasattr(result, "routable_names")
        assert hasattr(result, "pinned_names")
        assert hasattr(result, "decomposer_invoked")


class TestBaseUrlNormalization:
    """A10.22 and A10.27b: base_url trailing slash and path prefix handling."""

    def test_trailing_slash_stripped_from_base_url(self) -> None:
        """A10.22: SearchClient('http://test/') strips trailing slash."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://test/")
        # The stored _base_url should have no trailing slash
        assert not client._base_url.endswith("/")

    def test_path_prefix_preserved(self) -> None:
        """A10.27b: SearchClient('http://test/api/v1') preserves the path prefix."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://test/api/v1")
        assert "/api/v1" in client._base_url


class TestLifecycle:
    """A10.23–A10.24: close() and async context manager."""

    @pytest.mark.asyncio
    async def test_close_closes_http_client(self) -> None:
        """A10.23: close() calls aclose() on the underlying httpx client."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        with patch.object(client._http, "aclose", new=AsyncMock()) as mock_aclose:
            await client.close()
        mock_aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager_usable_and_closed_on_exit(self) -> None:
        """A10.24: async with SearchClient(...) as c — c is usable, closed on exit."""
        from archon.ai.search_client import SearchClient

        health_data = {"status": "ok"}
        mock_resp = _mock_response(200, health_data)

        async with SearchClient(base_url="http://localhost:8282") as client:
            with patch.object(client._http, "get", new=AsyncMock(return_value=mock_resp)):
                result = await client.health()
            assert result == {"status": "ok"}

        # After exiting, the httpx client should be closed (isClosed attribute or similar)
        assert client._http.is_closed


class TestResetSearchClient:
    """A10.25–A10.26: reset_search_client() singleton management."""

    @pytest.fixture(autouse=True)
    def restore_singleton(self) -> Generator[None, None, None]:
        from archon.ai import search_client as sc_module
        original = sc_module._search_client
        try:
            yield
        finally:
            sc_module._search_client = original

    @pytest.mark.asyncio
    async def test_reset_closes_existing_singleton(self) -> None:
        """A10.25: reset_search_client() closes the existing singleton and sets it to None."""
        from archon.ai import search_client as sc_module
        from archon.ai.search_client import reset_search_client, SearchClient

        # Set up a real singleton
        sc_module._search_client = SearchClient(base_url="http://localhost:8282")
        original_http = sc_module._search_client._http

        with patch.object(original_http, "aclose", new=AsyncMock()) as mock_aclose:
            await reset_search_client()

        mock_aclose.assert_called_once()
        assert sc_module._search_client is None

    @pytest.mark.asyncio
    async def test_reset_when_none_is_noop(self) -> None:
        """A10.26: reset_search_client() when singleton is None is a no-op (no exception)."""
        from archon.ai import search_client as sc_module
        from archon.ai.search_client import reset_search_client

        sc_module._search_client = None
        # Should not raise
        await reset_search_client()
        assert sc_module._search_client is None
