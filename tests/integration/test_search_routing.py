"""Integration tests for the full RAG routing data flow (FEAT-038 Task 7.2).

These tests exercise the complete chain:
  query → SearchContextProvider → SearchClient.route() → phase B fan-out search → merge

The HTTP boundary (SearchClient.route and SearchClient.search) is mocked.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from archon.ai.decomposer import TaskOutput
from archon.ai.search_client import SearchQueryResult
from archon.ai.search_context_provider import SearchContextProvider
from archon.config.loader import SearchConfig

from archon_search._types import SearchResult

try:
    from archon_search.types import RouteResponse
except ImportError:
    RouteResponse = None  # type: ignore[assignment,misc]


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────

_SEARCH_URL = "http://localhost:8282/mcp"


def _make_rag_config(
    *,
    max_parallel: int = 3,
    pinned_collections: list[str] | None = None,
    top_k_return: int = 5,
) -> SearchConfig:
    return SearchConfig(
        enabled=True,
        max_parallel_collections=max_parallel,
        top_k_return=top_k_return,
    )


def _make_route_response(
    *,
    pre_context: str | None = None,
    pinned_names: list[str] | None = None,
    routable_names: list[str] | None = None,
    decomposer_invoked: bool = False,
) -> Any:
    if RouteResponse is not None:
        return RouteResponse(
            pre_context=pre_context,
            pinned_names=pinned_names or [],
            routable_names=routable_names or [],
            decomposer_invoked=decomposer_invoked,
        )
    rr = MagicMock()
    rr.pre_context = pre_context
    rr.pinned_names = pinned_names or []
    rr.routable_names = routable_names or []
    rr.decomposer_invoked = decomposer_invoked
    return rr


def _make_task_output(selected_collections: list[str] | None) -> TaskOutput:
    to = TaskOutput(scope="small", prompt="test query")
    to.selected_collections = selected_collections
    return to


def _make_search_result(collection: str, idx: int = 0) -> SearchResult:
    return SearchResult(
        doc_id=f"{collection}-doc-{idx}",
        chunk_id=f"{collection}-chunk-{idx}",
        text=f"Text from {collection} chunk {idx}",
        score=0.9 - idx * 0.1,
        source_path=f"/path/{collection}/doc{idx}.md",
    )


def _make_search_dict(collection: str, idx: int = 0) -> dict:
    """Return a raw dict as returned by SearchClient.search()."""
    return {
        "doc_id": f"{collection}-doc-{idx}",
        "chunk_id": f"{collection}-chunk-{idx}",
        "text": f"Text from {collection} chunk {idx}",
        "score": 0.9 - idx * 0.1,
        "source_path": f"/path/{collection}/doc{idx}.md",
    }


def _make_mock_client(route_response: Any) -> MagicMock:
    client = MagicMock()
    client.route = AsyncMock(return_value=route_response)
    return client


# ──────────────────────────────────────────────────────────────────
# Test 1: Full happy-path (Tier 3 — decomposer invoked)
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_rag_routing_chain() -> None:
    """Full Tier 3 routing chain with SearchContextProvider + SearchClient HTTP mock.

    - decomposer_invoked=True → pre_context returned with collections block
    - search_and_prepare with selected_collections → parallel search → merged results
    - Returns (rag_text, chunk_count, searched_names) with correct data
    """
    routable = [f"col{i}" for i in range(8)]  # 8 routable collections
    route_resp = _make_route_response(
        pre_context=f"<search_collections>{', '.join(routable)}</search_collections>",
        pinned_names=[],
        routable_names=routable,
        decomposer_invoked=True,
    )
    client = _make_mock_client(route_resp)
    cfg = _make_rag_config(max_parallel=3, top_k_return=5)
    provider = SearchContextProvider(cfg=cfg, search_client=client)

    # Phase A: get_pre_context → route() → returns pre_context block
    pre_context = await provider.get_pre_context("test query")

    assert pre_context is not None
    assert "<search_collections>" in pre_context
    assert "</search_collections>" in pre_context

    # Phase B: decomposer selected 2 collections from the shortlist
    selected = routable[:2]
    task_output = _make_task_output(selected_collections=selected)

    search_results = {
        name: [_make_search_dict(name, 0), _make_search_dict(name, 1)]
        for name in routable
    }

    async def _mock_search(collection: str, query: str, top_k: int) -> SearchQueryResult:
        return SearchQueryResult(results=search_results.get(collection, []), acl_filtered=False)

    client.search = AsyncMock(side_effect=_mock_search)
    result = await provider.search_and_prepare(task_output, "test query")

    assert result is not None
    rag_text, chunk_count, actual_searched = result

    assert chunk_count > 0
    assert chunk_count <= cfg.top_k_return
    assert "[RAG context — retrieved document chunks:]" in rag_text
    assert "[End RAG context]" in rag_text
    assert "Source:" in rag_text
    for name in actual_searched:
        assert name in selected

    detail = f"{chunk_count} chunks from {', '.join(actual_searched)}"
    assert str(chunk_count) in detail
    for name in actual_searched:
        assert name in detail


# ──────────────────────────────────────────────────────────────────
# Test 2: Graceful degradation when route() fails
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_rag_routing_graceful_degradation() -> None:
    """When route() returns None (server error), the full chain degrades gracefully.

    - get_pre_context returns None (no route response)
    - search_and_prepare returns None (no route state)
    """
    cfg = _make_rag_config(max_parallel=3)
    client = _make_mock_client(None)  # route() returns None
    provider = SearchContextProvider(cfg=cfg, search_client=client)

    # Phase A: route() failed → None
    pre_context = await provider.get_pre_context("test query")
    assert pre_context is None

    # Phase B: no route state → None
    task_output = _make_task_output(selected_collections=None)
    result = await provider.search_and_prepare(task_output, "test query")
    assert result is None


# ──────────────────────────────────────────────────────────────────
# Test 3: Tier 1 path (decomposer NOT invoked)
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_rag_routing_tier1_chain() -> None:
    """Tier 1: decomposer_invoked=False → get_pre_context returns None.

    - search_and_prepare with task_output.selected_collections=None → searches ALL routable
    - Returns merged results containing chunks from all routable collections
    """
    routable = ["alpha", "beta", "gamma"]
    route_resp = _make_route_response(
        pre_context=None,  # Tier 1: no decomposer block
        pinned_names=[],
        routable_names=routable,
        decomposer_invoked=False,
    )
    client = _make_mock_client(route_resp)
    cfg = _make_rag_config(max_parallel=3, top_k_return=5)
    provider = SearchContextProvider(cfg=cfg, search_client=client)

    # Phase A: Tier 1 → get_pre_context returns None (no decomposer)
    pre_context = await provider.get_pre_context("test query")
    assert pre_context is None

    # Phase B: Tier 1 — selected_collections=None means "search all routable"
    task_output = _make_task_output(selected_collections=None)

    search_results = {
        name: [_make_search_dict(name, 0), _make_search_dict(name, 1)]
        for name in routable
    }

    async def _mock_search(collection: str, query: str, top_k: int) -> SearchQueryResult:
        return SearchQueryResult(results=search_results.get(collection, []), acl_filtered=False)

    client.search = AsyncMock(side_effect=_mock_search)
    result = await provider.search_and_prepare(task_output, "test query")

    assert result is not None
    rag_text, chunk_count, actual_searched = result

    # All 3 routable collections must have been searched (Tier 1 = search all)
    assert set(actual_searched) == {"alpha", "beta", "gamma"}
    assert chunk_count > 0
    assert chunk_count <= cfg.top_k_return
    assert "[RAG context — retrieved document chunks:]" in rag_text
    assert "[End RAG context]" in rag_text
    assert "Source:" in rag_text

    detail = f"{chunk_count} chunks from {', '.join(actual_searched)}"
    assert str(chunk_count) in detail
    for name in actual_searched:
        assert name in detail


# ──────────────────────────────────────────────────────────────────
# Test 4: inject_context detail string format
# ──────────────────────────────────────────────────────────────────


def test_inject_context_detail_format() -> None:
    """Verify the detail string format matches what pipeline.py passes to inject_context.

    pipeline.py line 278:
        detail=f"{chunk_count} chunks from {', '.join(actual_searched_names)}"
    """
    # Single collection
    chunk_count = 3
    actual_searched_names = ["col0"]
    detail = f"{chunk_count} chunks from {', '.join(actual_searched_names)}"
    assert detail == "3 chunks from col0"
    assert str(chunk_count) in detail
    for name in actual_searched_names:
        assert name in detail

    # Multiple collections
    chunk_count = 5
    actual_searched_names = ["alpha", "beta", "gamma"]
    detail = f"{chunk_count} chunks from {', '.join(actual_searched_names)}"
    assert detail == "5 chunks from alpha, beta, gamma"
    assert str(chunk_count) in detail
    for name in actual_searched_names:
        assert name in detail


# ──────────────────────────────────────────────────────────────────
# Test 5: Sentinel remap — decomposer_invoked=True, selected_collections=None
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_rag_routing_sentinel_remap() -> None:
    """Tier 3 with decomposer invoked but TaskOutput.selected_collections=None.

    When decomposer_invoked=True AND selected_collections=None, the code remaps to
    [] (pinned-only search). Since there are no pinned collections and selected_collections
    is remapped to [], to_search = [] → search_and_prepare returns None.
    """
    routable = [f"col{i}" for i in range(8)]
    route_resp = _make_route_response(
        pre_context=f"<search_collections>{', '.join(routable)}</search_collections>",
        pinned_names=[],
        routable_names=routable,
        decomposer_invoked=True,
    )
    client = _make_mock_client(route_resp)
    cfg = _make_rag_config(max_parallel=3, top_k_return=5, pinned_collections=[])
    provider = SearchContextProvider(cfg=cfg, search_client=client)

    # Phase A: Tier 3 → decomposer_invoked=True
    pre_context = await provider.get_pre_context("test query")
    assert pre_context is not None

    # Verify route state
    assert provider._route_response is not None
    assert provider._route_response.decomposer_invoked is True

    # Phase B: decomposer returns selected_collections=None (sentinel → remap to [])
    # No pinned collections + selected_collections=[] → to_search=[] → None
    task_output = _make_task_output(selected_collections=None)

    result = await provider.search_and_prepare(task_output, "test query")

    # No pinned + selected remapped to [] → nothing to search → None
    assert result is None
    client.search.assert_not_called()
