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

    @pytest.mark.asyncio
    async def test_get_search_client_uses_search_api_key_auth(self) -> None:
        """get_search_client() singleton uses SearchApiKeyAuth by default."""
        from archon.ai.search_client import SearchApiKeyAuth, get_search_client, reset_search_client

        await reset_search_client()

        mock_cfg = MagicMock()
        mock_cfg.search.url = "http://localhost:8765"

        with patch("archon.ai.search_client.config", mock_cfg):
            client = get_search_client()

        assert isinstance(client._http.auth, SearchApiKeyAuth)
        await reset_search_client()


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


# ---------------------------------------------------------------------------
# telemetry_stats()
# ---------------------------------------------------------------------------


class TestTelemetryStats:
    """Task 4.1 — SearchClient.telemetry_stats() tests."""

    @pytest.mark.asyncio
    async def test_telemetry_stats_success(self) -> None:
        """telemetry_stats() returns parsed dict on 200 response."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        stats_data = {"enabled": True, "total_queries": 42, "avg_latency_ms": 12.5}
        mock_resp = _mock_response(200, stats_data)

        with patch.object(client._http, "get", new=AsyncMock(return_value=mock_resp)):
            result = await client.telemetry_stats()

        assert isinstance(result, dict)
        assert result["enabled"] is True
        assert result["total_queries"] == 42

    @pytest.mark.asyncio
    async def test_telemetry_stats_returns_none_on_timeout(self) -> None:
        """telemetry_stats() returns None on TimeoutException."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        with patch.object(client._http, "get", new=AsyncMock(side_effect=httpx.TimeoutException("timed out"))):
            result = await client.telemetry_stats()

        assert result is None

    @pytest.mark.asyncio
    async def test_telemetry_stats_returns_none_on_connect_error(self) -> None:
        """telemetry_stats() returns None on ConnectError."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        with patch.object(client._http, "get", new=AsyncMock(side_effect=httpx.ConnectError("refused"))):
            result = await client.telemetry_stats()

        assert result is None

    @pytest.mark.asyncio
    async def test_telemetry_stats_passes_since_param(self) -> None:
        """telemetry_stats(since='2026-05-01') sends since in query params."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        mock_resp = _mock_response(200, {"enabled": True})

        with patch.object(client._http, "get", new=AsyncMock(return_value=mock_resp)) as mock_get:
            await client.telemetry_stats(since="2026-05-01")

        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert params.get("since") == "2026-05-01"

    @pytest.mark.asyncio
    async def test_telemetry_stats_omits_none_params(self) -> None:
        """telemetry_stats(since=None, until=None) does not include those keys in params."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        mock_resp = _mock_response(200, {"enabled": False})

        with patch.object(client._http, "get", new=AsyncMock(return_value=mock_resp)) as mock_get:
            await client.telemetry_stats()

        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert "since" not in params
        assert "until" not in params

    @pytest.mark.asyncio
    async def test_telemetry_stats_returns_none_on_http_error(self) -> None:
        """telemetry_stats() returns None when server returns HTTP 500."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        mock_resp = _mock_response(500, {})

        with patch.object(client._http, "get", new=AsyncMock(return_value=mock_resp)):
            result = await client.telemetry_stats()

        assert result is None

    @pytest.mark.asyncio
    async def test_telemetry_stats_disabled_response_returned_as_is(self) -> None:
        """telemetry_stats() returns {'enabled': False} dict as-is (not None)."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        mock_resp = _mock_response(200, {"enabled": False})

        with patch.object(client._http, "get", new=AsyncMock(return_value=mock_resp)):
            result = await client.telemetry_stats()

        assert result == {"enabled": False}


# ---------------------------------------------------------------------------
# telemetry_entries()
# ---------------------------------------------------------------------------


class TestTelemetryEntries:
    """FEAT-039d — SearchClient.telemetry_entries() tests."""

    @pytest.mark.asyncio
    async def test_telemetry_entries_success(self) -> None:
        """telemetry_entries() returns parsed dict on 200 response with all six keys."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        entries_data = {
            "schema_version": 1,
            "enabled": True,
            "entries": [{"id": "x"}],
            "next_offset": 1,
            "total_in_window": 1,
            "skipped_lines": 0,
        }
        mock_resp = _mock_response(200, entries_data)

        with patch.object(client._http, "get", new=AsyncMock(return_value=mock_resp)) as mock_get:
            result = await client.telemetry_entries()

        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert call_args.args[0] == "/telemetry/entries"
        assert result is not None
        for key in ("schema_version", "enabled", "entries", "next_offset", "total_in_window", "skipped_lines"):
            assert key in result

    @pytest.mark.asyncio
    async def test_telemetry_entries_disabled_returned_as_is(self) -> None:
        """telemetry_entries() returns {'enabled': False} dict as-is (not None)."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        mock_resp = _mock_response(200, {"enabled": False})

        with patch.object(client._http, "get", new=AsyncMock(return_value=mock_resp)):
            result = await client.telemetry_entries()

        assert result == {"enabled": False}

    @pytest.mark.asyncio
    async def test_telemetry_entries_returns_none_on_timeout(self) -> None:
        """telemetry_entries() returns None on TimeoutException."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        with patch.object(client._http, "get", new=AsyncMock(side_effect=httpx.TimeoutException("timed out"))):
            result = await client.telemetry_entries()

        assert result is None

    @pytest.mark.asyncio
    async def test_telemetry_entries_returns_none_on_connect_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """telemetry_entries() returns None on ConnectError; DEBUG log emitted (not WARNING)."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        with caplog.at_level(logging.DEBUG, logger="archon"):
            with patch.object(client._http, "get", new=AsyncMock(side_effect=httpx.ConnectError("refused"))):
                result = await client.telemetry_entries()

        assert result is None
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG and "telemetry_entries" in r.message]
        assert debug_records, "Expected a DEBUG log record for ConnectError"
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING and "telemetry_entries" in r.message]
        assert not warning_records, "ConnectError should not emit a WARNING log"

    @pytest.mark.asyncio
    async def test_telemetry_entries_returns_none_on_http_500(self, caplog: pytest.LogCaptureFixture) -> None:
        """telemetry_entries() returns None when server returns HTTP 500; WARNING log emitted."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        mock_resp = _mock_response(500, {})

        with caplog.at_level(logging.WARNING, logger="archon"):
            with patch.object(client._http, "get", new=AsyncMock(return_value=mock_resp)):
                result = await client.telemetry_entries()

        assert result is None
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING and "telemetry_entries" in r.message]
        assert warning_records, "Expected a WARNING log record for HTTP 500"

    @pytest.mark.asyncio
    async def test_telemetry_entries_returns_none_on_http_400(self) -> None:
        """telemetry_entries() returns None when server returns HTTP 400."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        mock_resp = _mock_response(400, {})

        with patch.object(client._http, "get", new=AsyncMock(return_value=mock_resp)):
            result = await client.telemetry_entries()

        assert result is None

    @pytest.mark.asyncio
    async def test_telemetry_entries_no_params_when_zero_args(self) -> None:
        """Zero-arg call sends no query params."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        mock_resp = _mock_response(200, {"enabled": True, "entries": [], "next_offset": 0, "total_in_window": 0, "skipped_lines": 0})

        with patch.object(client._http, "get", new=AsyncMock(return_value=mock_resp)) as mock_get:
            await client.telemetry_entries()

        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs["params"]
        assert params == {}, f"Expected empty params dict, got: {params}"

    @pytest.mark.asyncio
    async def test_telemetry_entries_partial_params_omitted(self) -> None:
        """collection='docs', limit=10 — only those two appear; others absent."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        mock_resp = _mock_response(200, {"enabled": True, "entries": [], "next_offset": 0, "total_in_window": 0, "skipped_lines": 0})

        with patch.object(client._http, "get", new=AsyncMock(return_value=mock_resp)) as mock_get:
            await client.telemetry_entries(collection="docs", limit=10)

        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert "collection" in params
        assert "limit" in params
        for absent in ("since", "until", "status", "error_kind", "offset"):
            assert absent not in params, f"Unexpected param: {absent}"

    @pytest.mark.asyncio
    async def test_telemetry_entries_integer_params_serialised(self) -> None:
        """offset and limit are passed as int (no premature stringification)."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        mock_resp = _mock_response(200, {"enabled": True, "entries": [], "next_offset": 35, "total_in_window": 35, "skipped_lines": 0})

        with patch.object(client._http, "get", new=AsyncMock(return_value=mock_resp)) as mock_get:
            await client.telemetry_entries(offset=10, limit=25)

        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert params["offset"] == 10
        assert params["limit"] == 25
        assert isinstance(params["offset"], int)
        assert isinstance(params["limit"], int)

    @pytest.mark.asyncio
    async def test_telemetry_entries_returns_none_on_unexpected_exception(self, caplog: pytest.LogCaptureFixture) -> None:
        """RuntimeError → None; WARNING log emitted."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        with caplog.at_level(logging.WARNING, logger="archon"):
            with patch.object(client._http, "get", new=AsyncMock(side_effect=RuntimeError("unexpected"))):
                result = await client.telemetry_entries()

        assert result is None
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING and "telemetry_entries" in r.message]
        assert warning_records, "Expected a WARNING log record for unexpected exception"

    @pytest.mark.asyncio
    async def test_telemetry_entries_all_params_forwarded(self) -> None:
        """All 8 params appear in the query params dict passed to httpx."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        mock_resp = _mock_response(200, {"enabled": True, "entries": [], "next_offset": 25, "total_in_window": 25, "skipped_lines": 0})

        with patch.object(client._http, "get", new=AsyncMock(return_value=mock_resp)) as mock_get:
            await client.telemetry_entries(
                since="2026-01-01",
                until="2026-05-01",
                collection="docs",
                endpoint="/search",
                status="ok",
                error_kind="timeout",
                offset=5,
                limit=20,
            )

        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert params["since"] == "2026-01-01"
        assert params["until"] == "2026-05-01"
        assert params["collection"] == "docs"
        assert params["endpoint"] == "/search"
        assert params["status"] == "ok"
        assert params["error_kind"] == "timeout"
        assert params["offset"] == 5
        assert params["limit"] == 20

    @pytest.mark.asyncio
    async def test_telemetry_entries_returns_none_on_http_422(self) -> None:
        """HTTP 422 (invalid status value) → None."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        mock_resp = _mock_response(422, {})

        with patch.object(client._http, "get", new=AsyncMock(return_value=mock_resp)):
            result = await client.telemetry_entries()

        assert result is None

    @pytest.mark.asyncio
    async def test_telemetry_entries_warning_logged_on_timeout(self, caplog: pytest.LogCaptureFixture) -> None:
        """TimeoutException → None; WARNING-level log record emitted."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        with caplog.at_level(logging.WARNING, logger="archon"):
            with patch.object(client._http, "get", new=AsyncMock(side_effect=httpx.TimeoutException("timed out"))):
                result = await client.telemetry_entries()

        assert result is None
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING and "telemetry_entries" in r.message]
        assert warning_records, "Expected a WARNING log record for TimeoutException"

    @pytest.mark.asyncio
    async def test_telemetry_entries_empty_string_passes_through(self) -> None:
        """collection='' is not filtered — only None is omitted."""
        from archon.ai.search_client import SearchClient

        client = SearchClient(base_url="http://localhost:8282")
        mock_resp = _mock_response(200, {"enabled": True, "entries": [], "next_offset": 0, "total_in_window": 0, "skipped_lines": 0})

        with patch.object(client._http, "get", new=AsyncMock(return_value=mock_resp)) as mock_get:
            await client.telemetry_entries(collection="")

        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert "collection" in params
        assert params["collection"] == ""


# ---------------------------------------------------------------------------
# SearchApiKeyAuth
# ---------------------------------------------------------------------------


class TestSearchApiKeyAuth:
    """Task 4.1 — SearchApiKeyAuth httpx.Auth subclass."""

    def test_lazy_no_key_at_init(self, monkeypatch) -> None:
        """SearchApiKeyAuth() does not read file or env at construction."""
        from archon.ai.search_client import SearchApiKeyAuth

        # Patch env to ensure it's not accessed silently
        monkeypatch.delenv("ARCHON_SEARCH_API_KEY", raising=False)
        auth = SearchApiKeyAuth()
        # _cached_key must be None — no resolution happened yet
        assert auth._cached_key is None

    @pytest.mark.asyncio
    async def test_key_loaded_on_first_request(self, monkeypatch) -> None:
        """Key is injected on first async_auth_flow call via env var."""
        from archon.ai.search_client import SearchApiKeyAuth

        monkeypatch.setenv("ARCHON_SEARCH_API_KEY", "deadbeef")
        auth = SearchApiKeyAuth()

        mock_request = MagicMock()
        mock_request.headers = {}
        mock_response = MagicMock()
        mock_response.status_code = 200

        gen = auth.async_auth_flow(mock_request)
        # First yield sends the (modified) request
        sent_request = await gen.__anext__()
        assert sent_request.headers.get("Authorization") == "Bearer deadbeef"
        # Feed a 200 response; generator should finish
        try:
            await gen.asend(mock_response)
        except StopAsyncIteration:
            pass

    @pytest.mark.asyncio
    async def test_key_cached_on_success(self, monkeypatch) -> None:
        """Second call uses cached key without re-reading env/file."""
        from archon.ai.search_client import SearchApiKeyAuth

        monkeypatch.setenv("ARCHON_SEARCH_API_KEY", "cafebabe")
        auth = SearchApiKeyAuth()

        mock_request = MagicMock()
        mock_request.headers = {}
        mock_response = MagicMock()
        mock_response.status_code = 200

        # First call
        gen = auth.async_auth_flow(mock_request)
        await gen.__anext__()
        try:
            await gen.asend(mock_response)
        except StopAsyncIteration:
            pass

        # After first call, _cached_key must be set
        assert auth._cached_key == "cafebabe"

        # Remove env var — second call must use cache, not re-read
        monkeypatch.delenv("ARCHON_SEARCH_API_KEY", raising=False)
        mock_request2 = MagicMock()
        mock_request2.headers = {}

        gen2 = auth.async_auth_flow(mock_request2)
        sent_request2 = await gen2.__anext__()
        assert sent_request2.headers.get("Authorization") == "Bearer cafebabe"
        try:
            await gen2.asend(mock_response)
        except StopAsyncIteration:
            pass

    @pytest.mark.asyncio
    async def test_failure_not_cached(self, monkeypatch) -> None:
        """Resolver returns None → _cached_key stays None; next call re-reads."""
        from archon.ai.search_client import SearchApiKeyAuth

        monkeypatch.delenv("ARCHON_SEARCH_API_KEY", raising=False)
        auth = SearchApiKeyAuth()

        # Patch _KEY_FILE so it raises FileNotFoundError
        with patch.object(type(auth), "_KEY_FILE", new_callable=lambda: property(lambda self: MagicMock(read_text=MagicMock(side_effect=FileNotFoundError())))):
            pass  # just proving setup

        # Directly patch _resolve_key to return None on first call, then a key
        call_count = 0
        original_resolve = auth._resolve_key

        async def _fake_resolve(force_reload: bool = False) -> str | None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None
            return None  # still None on second call too

        auth._resolve_key = _fake_resolve  # type: ignore[method-assign]

        mock_request = MagicMock()
        mock_request.headers = {}
        mock_response = MagicMock()
        mock_response.status_code = 200

        gen = auth.async_auth_flow(mock_request)
        await gen.__anext__()
        try:
            await gen.asend(mock_response)
        except StopAsyncIteration:
            pass

        # None must never be cached
        assert auth._cached_key is None

    @pytest.mark.asyncio
    async def test_401_clears_cache_and_retries(self, monkeypatch) -> None:
        """First 401 → cache cleared, key re-read, second request sent with new key."""
        from archon.ai.search_client import SearchApiKeyAuth

        auth = SearchApiKeyAuth()
        auth._cached_key = "oldkey"

        call_count = 0

        async def _fake_resolve(force_reload: bool = False) -> str | None:
            nonlocal call_count
            call_count += 1
            if force_reload:
                return "newkey"
            return "oldkey"

        auth._resolve_key = _fake_resolve  # type: ignore[method-assign]

        mock_request = MagicMock()
        mock_request.headers = {}

        resp_401 = MagicMock()
        resp_401.status_code = 401
        resp_200 = MagicMock()
        resp_200.status_code = 200

        gen = auth.async_auth_flow(mock_request)
        # First yield: request with oldkey
        first_req = await gen.__anext__()
        assert first_req.headers.get("Authorization") == "Bearer oldkey"

        # Feed 401 → auth should retry with newkey
        second_req = await gen.asend(resp_401)
        assert second_req.headers.get("Authorization") == "Bearer newkey"

        # Feed 200 → generator ends
        try:
            await gen.asend(resp_200)
        except StopAsyncIteration:
            pass

    @pytest.mark.asyncio
    async def test_second_401_error_log(self, monkeypatch, caplog) -> None:
        """Both requests get 401 → ERROR log emitted exactly once; generator ends."""
        from archon.ai.search_client import SearchApiKeyAuth

        auth = SearchApiKeyAuth()

        async def _fake_resolve(force_reload: bool = False) -> str | None:
            return "somekey"

        auth._resolve_key = _fake_resolve  # type: ignore[method-assign]

        mock_request = MagicMock()
        mock_request.headers = {}

        resp_401 = MagicMock()
        resp_401.status_code = 401

        gen = auth.async_auth_flow(mock_request)
        # First yield
        await gen.__anext__()

        # Feed first 401 → retry
        with caplog.at_level(logging.ERROR, logger="archon"):
            second_req = await gen.asend(resp_401)

        # Feed second 401 → ERROR logged, generator ends
        with caplog.at_level(logging.ERROR, logger="archon"):
            try:
                await gen.asend(resp_401)
            except StopAsyncIteration:
                pass

        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_records) == 1
        assert "Search authentication failed" in error_records[0].message

    @pytest.mark.asyncio
    async def test_no_key_warning(self, monkeypatch, caplog) -> None:
        """No env, no file → WARNING logged with exact message."""
        from archon.ai.search_client import SearchApiKeyAuth

        monkeypatch.delenv("ARCHON_SEARCH_API_KEY", raising=False)
        auth = SearchApiKeyAuth()

        # Patch _KEY_FILE to a non-existent path
        with patch.object(SearchApiKeyAuth, "_KEY_FILE", new=MagicMock(read_text=MagicMock(side_effect=FileNotFoundError()))):
            mock_request = MagicMock()
            mock_request.headers = {}
            mock_response = MagicMock()
            mock_response.status_code = 200

            gen = auth.async_auth_flow(mock_request)
            with caplog.at_level(logging.WARNING, logger="archon"):
                sent = await gen.__anext__()

            try:
                await gen.asend(mock_response)
            except StopAsyncIteration:
                pass

        # No Authorization header set when no key
        assert "Authorization" not in sent.headers
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "No ARCHON_SEARCH_API_KEY found — all search requests will fail with 401" in r.message
            for r in warning_records
        )

    @pytest.mark.asyncio
    async def test_env_priority(self, monkeypatch) -> None:
        """Env var is used without reading file when set."""
        from archon.ai.search_client import SearchApiKeyAuth

        # Use a valid hex key
        monkeypatch.setenv("ARCHON_SEARCH_API_KEY", "deadbeefcafe")
        auth = SearchApiKeyAuth()

        # Track whether file read was attempted
        file_read_called = []
        mock_key_file = MagicMock()
        mock_key_file.read_text = MagicMock(side_effect=lambda: file_read_called.append(1) or "")

        with patch.object(SearchApiKeyAuth, "_KEY_FILE", new=mock_key_file):
            key = await auth._resolve_key()

        assert key == "deadbeefcafe"
        assert len(file_read_called) == 0, "File should not be read when env var is set"

    def test_sync_auth_flow_raises(self) -> None:
        """sync_auth_flow raises NotImplementedError."""
        from archon.ai.search_client import SearchApiKeyAuth

        auth = SearchApiKeyAuth()
        with pytest.raises(NotImplementedError):
            # sync_auth_flow is a generator; calling next() on it should raise
            list(auth.sync_auth_flow(MagicMock()))

    @pytest.mark.asyncio
    async def test_auth_401_then_reload_succeeds(self, monkeypatch, caplog) -> None:
        """key_A cached → 401 → reload returns key_B → 200 → no ERROR log."""
        from archon.ai.search_client import SearchApiKeyAuth

        auth = SearchApiKeyAuth()
        auth._cached_key = "key_a"

        async def _fake_resolve(force_reload: bool = False) -> str | None:
            if force_reload:
                return "key_b"
            return "key_a"

        auth._resolve_key = _fake_resolve  # type: ignore[method-assign]

        mock_request = MagicMock()
        mock_request.headers = {}

        resp_401 = MagicMock()
        resp_401.status_code = 401
        resp_200 = MagicMock()
        resp_200.status_code = 200

        gen = auth.async_auth_flow(mock_request)
        first_req = await gen.__anext__()
        assert first_req.headers.get("Authorization") == "Bearer key_a"

        with caplog.at_level(logging.ERROR, logger="archon"):
            second_req = await gen.asend(resp_401)
        assert second_req.headers.get("Authorization") == "Bearer key_b"

        with caplog.at_level(logging.ERROR, logger="archon"):
            try:
                await gen.asend(resp_200)
            except StopAsyncIteration:
                pass

        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_records) == 0

    @pytest.mark.asyncio
    async def test_none_key_then_401_no_error_log(self, monkeypatch, caplog) -> None:
        """No key → request without auth → 401 → retry returns None → generator ends with WARNING, no ERROR."""
        from archon.ai.search_client import SearchApiKeyAuth

        monkeypatch.delenv("ARCHON_SEARCH_API_KEY", raising=False)
        auth = SearchApiKeyAuth()

        with patch.object(SearchApiKeyAuth, "_KEY_FILE", new=MagicMock(read_text=MagicMock(side_effect=FileNotFoundError()))):
            mock_request = MagicMock()
            mock_request.headers = {}

            resp_401 = MagicMock()
            resp_401.status_code = 401

            gen = auth.async_auth_flow(mock_request)
            with caplog.at_level(logging.WARNING, logger="archon"):
                first_req = await gen.__anext__()

            # No Authorization header when key is None
            assert "Authorization" not in first_req.headers

            # Feed 401 — should try force reload, get None again, then end without ERROR
            with caplog.at_level(logging.ERROR, logger="archon"):
                try:
                    await gen.asend(resp_401)
                except StopAsyncIteration:
                    pass

        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_records) == 0

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_records) >= 1

    @pytest.mark.asyncio
    async def test_file_key_resolution(self, monkeypatch, tmp_path) -> None:
        """Key written to _KEY_FILE is read and returned when env var is absent."""
        from archon.ai.search_client import SearchApiKeyAuth

        hex_key = "a" * 64
        key_file = tmp_path / ".search.env"
        key_file.write_text(f"ARCHON_SEARCH_API_KEY={hex_key}\n")

        monkeypatch.delenv("ARCHON_SEARCH_API_KEY", raising=False)
        auth = SearchApiKeyAuth()

        with patch.object(SearchApiKeyAuth, "_KEY_FILE", new=key_file):
            key = await auth._resolve_key()

        assert key == hex_key


class TestSearchClient:
    """Task 4.2 — SearchClient.__init__ wires SearchApiKeyAuth by default."""

    def test_search_client_uses_auth_subclass(self) -> None:
        """SearchClient() (no auth arg) uses a SearchApiKeyAuth instance."""
        from archon.ai.search_client import SearchClient, SearchApiKeyAuth

        client = SearchClient(base_url="http://localhost:8282")
        assert isinstance(client._http.auth, SearchApiKeyAuth)

    def test_search_client_accepts_custom_auth(self) -> None:
        """SearchClient(auth=custom) uses the provided auth object, not SearchApiKeyAuth."""
        from archon.ai.search_client import SearchClient, SearchApiKeyAuth

        custom_auth = MagicMock(spec=httpx.Auth)
        client = SearchClient(base_url="http://localhost:8282", auth=custom_auth)
        assert client._http.auth is custom_auth
        assert not isinstance(client._http.auth, SearchApiKeyAuth)


# ---------------------------------------------------------------------------
# search()
# ---------------------------------------------------------------------------


class TestSearchClientSearch:
    """Task 4.3 — SearchClient.search() wrapping POST /search."""

    @pytest.mark.asyncio
    async def test_search_success(self) -> None:
        """search() returns SearchQueryResult with result dicts on 200 response."""
        from archon.ai.search_client import SearchClient, SearchQueryResult

        client = SearchClient(base_url="http://localhost:8282")
        results_data = [
            {"doc_id": "d1", "chunk_id": "c1", "text": "hello", "score": 0.9, "source_path": "/f"}
        ]
        mock_resp = _mock_response(200, results_data)

        with patch.object(client._http, "post", new=AsyncMock(return_value=mock_resp)):
            result = await client.search("col1", "hello")

        assert isinstance(result, SearchQueryResult)
        assert len(result.results) == 1
        assert result.results[0]["doc_id"] == "d1"
        assert result.results[0]["score"] == 0.9

    @pytest.mark.asyncio
    async def test_search_empty_result(self) -> None:
        """search() returns SearchQueryResult with empty list on 200 response with empty list."""
        from archon.ai.search_client import SearchClient, SearchQueryResult

        client = SearchClient(base_url="http://localhost:8282")
        mock_resp = _mock_response(200, [])

        with patch.object(client._http, "post", new=AsyncMock(return_value=mock_resp)):
            result = await client.search("col1", "query")

        assert isinstance(result, SearchQueryResult)
        assert result.results == []

    @pytest.mark.asyncio
    async def test_search_timeout(self, caplog: pytest.LogCaptureFixture) -> None:
        """search() returns empty SearchQueryResult and logs WARNING on TimeoutException."""
        from archon.ai.search_client import SearchClient, SearchQueryResult

        client = SearchClient(base_url="http://localhost:8282")

        with patch.object(client._http, "post", new=AsyncMock(side_effect=httpx.TimeoutException("timed out"))):
            with caplog.at_level(logging.WARNING, logger="archon"):
                result = await client.search("col1", "query")

        assert isinstance(result, SearchQueryResult)
        assert result.results == []
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("col1" in r.message for r in warning_records)

    @pytest.mark.asyncio
    async def test_search_connect_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """search() returns empty SearchQueryResult and logs DEBUG (not WARNING) on ConnectError."""
        from archon.ai.search_client import SearchClient, SearchQueryResult

        client = SearchClient(base_url="http://localhost:8282")

        with patch.object(client._http, "post", new=AsyncMock(side_effect=httpx.ConnectError("refused"))):
            with caplog.at_level(logging.DEBUG, logger="archon"):
                result = await client.search("col1", "query")

        assert isinstance(result, SearchQueryResult)
        assert result.results == []
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG and "col1" in r.message]
        assert debug_records, "Expected a DEBUG log record for ConnectError"
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING and "col1" in r.message]
        assert not warning_records, "ConnectError should not emit a WARNING log"

    @pytest.mark.asyncio
    async def test_search_http_500(self) -> None:
        """search() returns empty SearchQueryResult on HTTP 500."""
        from archon.ai.search_client import SearchClient, SearchQueryResult

        client = SearchClient(base_url="http://localhost:8282")
        mock_resp = _mock_response(500, {})

        with patch.object(client._http, "post", new=AsyncMock(return_value=mock_resp)):
            result = await client.search("col1", "query")

        assert isinstance(result, SearchQueryResult)
        assert result.results == []

    @pytest.mark.asyncio
    async def test_search_http_401_no_double_log(self, caplog: pytest.LogCaptureFixture) -> None:
        """search() returns empty SearchQueryResult on HTTP 401; no extra WARNING beyond auth subclass."""
        from archon.ai.search_client import SearchClient, SearchQueryResult

        client = SearchClient(base_url="http://localhost:8282")
        mock_resp = _mock_response(401, {})

        with patch.object(client._http, "post", new=AsyncMock(return_value=mock_resp)):
            with caplog.at_level(logging.WARNING, logger="archon"):
                result = await client.search("col1", "query")

        assert isinstance(result, SearchQueryResult)
        assert result.results == []
        # The search() method itself must NOT log an extra WARNING for 401/403
        extra_warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "col1" in r.message
        ]
        assert len(extra_warnings) == 0, f"search() must not log a WARNING for 401: {extra_warnings}"

    @pytest.mark.asyncio
    async def test_search_unexpected_exception(self, caplog: pytest.LogCaptureFixture) -> None:
        """search() returns empty SearchQueryResult on unexpected RuntimeError; WARNING logged."""
        from archon.ai.search_client import SearchClient, SearchQueryResult

        client = SearchClient(base_url="http://localhost:8282")

        with patch.object(client._http, "post", new=AsyncMock(side_effect=RuntimeError("boom"))):
            with caplog.at_level(logging.WARNING, logger="archon"):
                result = await client.search("col1", "query")

        assert isinstance(result, SearchQueryResult)
        assert result.results == []
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warning_records, "Expected a WARNING log for unexpected exception"


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
