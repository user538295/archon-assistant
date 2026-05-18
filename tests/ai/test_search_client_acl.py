"""Tests for SearchClient.search() returning SearchQueryResult with acl_filtered (FEAT-044 Task 5.1)."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def _make_client(base_url: str = "http://localhost:8282") -> "SearchClient":
    from archon.ai.search_client import SearchClient

    return SearchClient(base_url=base_url)


def _mock_response(status_code: int = 200, json_data: object = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_data)
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


class TestSearchQueryResult:
    @pytest.mark.asyncio
    async def test_search_client_unwraps_search_response(self) -> None:
        """Server returns dict with results and acl_filtered=false → SearchQueryResult unpacked correctly."""
        from archon.ai.search_client import SearchClient, SearchQueryResult

        client = _make_client()
        mock_resp = _mock_response(200, {"results": [{"text": "hi"}], "acl_filtered": False})

        with patch.object(client._http, "post", new=AsyncMock(return_value=mock_resp)):
            result = await client.search("col", "query")

        assert isinstance(result, SearchQueryResult)
        assert result.results == [{"text": "hi"}]
        assert result.acl_filtered is False

    @pytest.mark.asyncio
    async def test_search_client_bare_list_fallback(self, caplog) -> None:
        """Server returns bare list (old server) → SearchQueryResult with acl_filtered=False + warning logged."""
        from archon.ai.search_client import SearchClient, SearchQueryResult

        client = _make_client()
        mock_resp = _mock_response(200, [{"text": "old"}])

        with patch.object(client._http, "post", new=AsyncMock(return_value=mock_resp)):
            with caplog.at_level(logging.WARNING, logger="archon"):
                result = await client.search("col", "query")

        assert isinstance(result, SearchQueryResult)
        assert result.results == [{"text": "old"}]
        assert result.acl_filtered is False
        assert any("bare JSON array" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_search_client_returns_empty_on_failure(self) -> None:
        """HTTP 500 → SearchQueryResult(results=[], acl_filtered=False)."""
        from archon.ai.search_client import SearchClient, SearchQueryResult

        client = _make_client()
        mock_resp = _mock_response(500, {})

        with patch.object(client._http, "post", new=AsyncMock(return_value=mock_resp)):
            result = await client.search("col", "query")

        assert isinstance(result, SearchQueryResult)
        assert result.results == []
        assert result.acl_filtered is False

    @pytest.mark.asyncio
    async def test_search_client_acl_filtered_true_propagated(self) -> None:
        """Server returns acl_filtered=true → result.acl_filtered is True."""
        from archon.ai.search_client import SearchClient, SearchQueryResult

        client = _make_client()
        mock_resp = _mock_response(200, {"results": [], "acl_filtered": True})

        with patch.object(client._http, "post", new=AsyncMock(return_value=mock_resp)):
            result = await client.search("col", "query")

        assert isinstance(result, SearchQueryResult)
        assert result.results == []
        assert result.acl_filtered is True

    @pytest.mark.asyncio
    async def test_search_client_malformed_dict_response(self) -> None:
        """Server returns dict without 'results' key → SearchQueryResult(results=[], acl_filtered=False)."""
        from archon.ai.search_client import SearchClient, SearchQueryResult

        client = _make_client()
        mock_resp = _mock_response(200, {"foo": "bar"})

        with patch.object(client._http, "post", new=AsyncMock(return_value=mock_resp)):
            result = await client.search("col", "query")

        assert isinstance(result, SearchQueryResult)
        assert result.results == []
        assert result.acl_filtered is False

    @pytest.mark.asyncio
    async def test_search_client_results_key_not_a_list(self) -> None:
        """Server returns results as non-list → SearchQueryResult(results=[], acl_filtered=False) without crashing."""
        from archon.ai.search_client import SearchClient, SearchQueryResult

        client = _make_client()
        mock_resp = _mock_response(200, {"results": "not_a_list", "acl_filtered": True})

        with patch.object(client._http, "post", new=AsyncMock(return_value=mock_resp)):
            result = await client.search("col", "query")

        assert isinstance(result, SearchQueryResult)
        assert result.results == []
        assert result.acl_filtered is False
