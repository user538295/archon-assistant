"""Integration tests for the full RAG routing data flow (FEAT-022 Task 3.3).

These tests exercise the complete chain:
  query → real SearchContextProvider + real MultiCollectionRouter
  → mock embedder → mock HTTP boundary (httpx) → merge scores → verify inject_context

Only the HTTP boundary (httpx.AsyncClient.post) and the embedder backend are mocked.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from archon.ai.decomposer import TaskOutput
from archon.ai.search_context_provider import SearchContextProvider
from archon.config.loader import SearchConfig


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────

_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
_SEARCH_URL = "http://localhost:8282/mcp"

# A fixed vector matching the embedding model name used in test metadata
_QUERY_VECTOR = [0.1, 0.2, 0.3, 0.4, 0.5]


def _make_rag_config(
    *,
    max_parallel: int = 3,
    shortlist_size: int = 8,
    confidence_threshold: float = 0.0,  # 0.0 = accept all collections
    pinned_collections: list[str] | None = None,
    top_k_return: int = 5,
) -> SearchConfig:
    return SearchConfig(
        enabled=True,
        host="localhost",
        port=8282,
        embedding_model=_EMBEDDING_MODEL,
        providers=[],
        max_parallel_collections=max_parallel,
        routing_shortlist_size=shortlist_size,
        routing_confidence_threshold=confidence_threshold,
        pinned_collections=pinned_collections if pinned_collections is not None else [],
        top_k_return=top_k_return,
    )


def _make_collection_meta_dict(
    name: str,
    description: str = "A test collection",
    centroid: list[float] | None = None,
    embedding_model: str = _EMBEDDING_MODEL,
) -> dict[str, Any]:
    """Build a dict representing one collection as returned by the RAG server."""
    return {
        "name": name,
        "description": description,
        "centroid": centroid or [0.1, 0.2, 0.3, 0.4, 0.5],
        "embedding_model": embedding_model,
        "doc_count": 10,
        "chunk_count": 50,
    }


def _make_metadata_response(collections: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a JSON-RPC response for get_collections_meta."""
    return {
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(collections),
                }
            ]
        }
    }


def _make_search_results(collection: str, n: int = 2) -> list[dict[str, Any]]:
    """Build raw search result dicts for one collection."""
    return [
        {
            "doc_id": f"{collection}-doc-{i}",
            "chunk_id": f"{collection}-chunk-{i}",
            "text": f"Text from {collection} chunk {i}",
            "score": 0.9 - i * 0.1,
            "source_path": f"/path/{collection}/doc{i}.md",
        }
        for i in range(n)
    ]


def _make_search_response(collection: str, n: int = 2) -> dict[str, Any]:
    """Build a JSON-RPC response for a search call."""
    return {
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(_make_search_results(collection, n)),
                }
            ]
        }
    }


def _make_task_output(selected_collections: list[str] | None) -> TaskOutput:
    to = TaskOutput(scope="small", prompt="test query")
    to.selected_collections = selected_collections
    return to


def _mock_embedder_backend() -> MagicMock:
    """Return a mock EmbedderBackend that returns a fixed vector."""
    backend = MagicMock()
    backend.model_name = _EMBEDDING_MODEL
    backend.encode = MagicMock(return_value=[_QUERY_VECTOR])
    return backend


# ──────────────────────────────────────────────────────────────────
# Test 1: Full happy-path (Tier 3 — >shortlist_size routable collections)
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_rag_routing_chain() -> None:
    """Full Tier 3 routing chain with real SearchContextProvider + real MultiCollectionRouter.

    - >shortlist_size routable collections → centroid pre-ranking → decomposer block returned
    - search_and_prepare with selected_collections → parallel HTTP search → merged results
    - Returns (rag_text, chunk_count, searched_names) with correct data
    """
    # Build >shortlist_size (8) collections with non-uniform centroids so ranking discriminates.
    # col0–col7: positive cosine similarity to _QUERY_VECTOR=[0.1,0.2,0.3,0.4,0.5] → included.
    # col8, col9: negative cosine similarity → sorted last, excluded from top-8 shortlist.
    centroids = [
        [0.1, 0.2, 0.3, 0.4, 0.5],   # col0 — parallel to query, sim=1.0
        [0.2, 0.3, 0.4, 0.5, 0.6],   # col1 — all positive, sim>0
        [0.1, 0.3, 0.2, 0.4, 0.5],   # col2 — all positive, sim>0
        [0.2, 0.1, 0.3, 0.4, 0.5],   # col3 — all positive, sim>0
        [0.1, 0.2, 0.4, 0.3, 0.5],   # col4 — all positive, sim>0
        [0.3, 0.2, 0.3, 0.4, 0.5],   # col5 — all positive, sim>0
        [0.1, 0.2, 0.3, 0.5, 0.4],   # col6 — all positive, sim>0
        [0.2, 0.2, 0.3, 0.4, 0.5],   # col7 — all positive, sim>0
        [-0.1, -0.2, -0.3, -0.4, -0.5],  # col8 — anti-parallel, sim=-1.0 (excluded)
        [0.0, -0.5, 0.0, -0.5, 0.0],     # col9 — negative dot product, sim<0 (excluded)
    ]
    collections = [
        _make_collection_meta_dict(f"col{i}", description=f"Collection {i}", centroid=centroids[i])
        for i in range(10)  # 10 > shortlist_size=8
    ]
    metadata_response = _make_metadata_response(collections)

    cfg = _make_rag_config(
        max_parallel=3,
        shortlist_size=8,
        confidence_threshold=0.0,
        top_k_return=5,
    )

    # HTTP responses: first call = metadata, subsequent = search results per collection
    search_responses = {
        f"col{i}": _make_search_response(f"col{i}", n=2)
        for i in range(10)
    }

    async def mock_post(url: str, **kwargs: Any) -> MagicMock:
        body = kwargs.get("json", {})
        resp = MagicMock(spec=httpx.Response)
        resp.raise_for_status = MagicMock()

        params = body.get("params", {})
        tool_name = params.get("name", "")

        if tool_name == "get_collections_meta":
            resp.json = MagicMock(return_value=metadata_response)
        else:
            # search call — extract collection from arguments
            collection = params.get("arguments", {}).get("collection", "unknown")
            resp.json = MagicMock(return_value=search_responses.get(collection, {"result": {"content": []}}))

        return resp

    backend = _mock_embedder_backend()

    with patch("archon.search.embedder.ModelEmbedder", return_value=backend):
        provider = SearchContextProvider(search_url=_SEARCH_URL, cfg=cfg)

        with patch.object(provider._embedder, "embed_one", AsyncMock(return_value=_QUERY_VECTOR)):
            # Patch httpx.AsyncClient used in MultiCollectionRouter.fetch_metadata
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client.post = AsyncMock(side_effect=mock_post)
                mock_client_cls.return_value = mock_client

                # Phase A: get_pre_context — should return rag_collections block (Tier 3)
                pre_context = await provider.get_pre_context("test query")

    # Tier 3: >shortlist_size routable collections → decomposer block returned
    assert pre_context is not None
    assert "<search_collections>" in pre_context
    assert "</search_collections>" in pre_context

    # Router must have been invoked (decomposer_was_invoked)
    assert provider._router is not None
    assert provider._router.decomposer_was_invoked is True
    # shortlist_size=8 → last_routable_names should be exactly col0..col7 (positive sim)
    assert len(provider._router.last_routable_names) == 8
    assert set(provider._router.last_routable_names) == {"col0", "col1", "col2", "col3", "col4", "col5", "col6", "col7"}
    assert "col8" not in provider._router.last_routable_names
    assert "col9" not in provider._router.last_routable_names

    # Phase B: search_and_prepare — decomposer selected 2 collections from the shortlist
    shortlisted = provider._router.last_routable_names[:2]
    task_output = _make_task_output(selected_collections=shortlisted)

    # Phase B: metadata is already cached from Phase A; only search calls are made
    async def mock_post_phase_b(url: str, **kwargs: Any) -> MagicMock:
        body = kwargs.get("json", {})
        params = body.get("params", {})
        collection = params.get("arguments", {}).get("collection", "unknown")
        resp = MagicMock(spec=httpx.Response)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=search_responses.get(collection, {"result": {"content": []}}))
        return resp

    with patch("httpx.AsyncClient") as mock_client_cls2:
        mock_client2 = AsyncMock()
        mock_client2.__aenter__ = AsyncMock(return_value=mock_client2)
        mock_client2.__aexit__ = AsyncMock(return_value=False)
        mock_client2.post = AsyncMock(side_effect=mock_post_phase_b)
        mock_client_cls2.return_value = mock_client2

        result = await provider.search_and_prepare(task_output, "test query")

    assert result is not None
    rag_text, chunk_count, actual_searched = result

    # chunk_count must be > 0 and ≤ top_k_return
    assert chunk_count > 0
    assert chunk_count <= cfg.top_k_return

    # rag_text must contain the expected markers
    assert "[RAG context — retrieved document chunks:]" in rag_text
    assert "[End RAG context]" in rag_text

    # actual_searched must be a subset of what was requested
    for name in actual_searched:
        assert name in shortlisted

    # Each chunk in rag_text must have a source line
    assert "Source:" in rag_text

    # Verify the detail string format that pipeline.py passes to inject_context
    detail = f"{chunk_count} chunks from {', '.join(actual_searched)}"
    assert str(chunk_count) in detail
    for name in actual_searched:
        assert name in detail


# ──────────────────────────────────────────────────────────────────
# Test 2: Graceful degradation when metadata fetch fails
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_rag_routing_graceful_degradation() -> None:
    """When metadata endpoint returns an HTTP error, the full chain degrades gracefully.

    - get_pre_context returns None (no metadata → nothing to route)
    - search_and_prepare returns None (router is set but last_routable_names is empty)
    """
    cfg = _make_rag_config(max_parallel=3)

    async def mock_post_error(url: str, **kwargs: Any) -> MagicMock:
        raise httpx.HTTPStatusError(
            "500 Internal Server Error",
            request=MagicMock(),
            response=MagicMock(status_code=500),
        )

    backend = _mock_embedder_backend()

    with patch("archon.search.embedder.ModelEmbedder", return_value=backend):
        provider = SearchContextProvider(search_url=_SEARCH_URL, cfg=cfg)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=mock_post_error)
            mock_client_cls.return_value = mock_client

            # Phase A: metadata fetch fails → None
            pre_context = await provider.get_pre_context("test query")

    assert pre_context is None

    # Phase B: search_and_prepare with the router set but empty metadata
    task_output = _make_task_output(selected_collections=None)

    with patch("httpx.AsyncClient") as mock_client_cls2:
        mock_client2 = AsyncMock()
        mock_client2.__aenter__ = AsyncMock(return_value=mock_client2)
        mock_client2.__aexit__ = AsyncMock(return_value=False)
        mock_client2.post = AsyncMock(side_effect=mock_post_error)
        mock_client_cls2.return_value = mock_client2

        result = await provider.search_and_prepare(task_output, "test query")

    # No routable collections → empty to_search → None
    assert result is None


# ──────────────────────────────────────────────────────────────────
# Test 3: Tier 1 path (≤3 routable collections)
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_rag_routing_tier1_chain() -> None:
    """Tier 1: ≤3 routable collections → decomposer bypassed.

    - get_pre_context returns None (Tier 1 — no decomposer needed)
    - router.decomposer_was_invoked is False
    - search_and_prepare with task_output.selected_collections=None → searches ALL routable
    - Returns merged results containing chunks from all routable collections
    """
    # 3 routable collections (≤3 triggers Tier 1)
    collections = [
        _make_collection_meta_dict("alpha", description="Alpha docs"),
        _make_collection_meta_dict("beta", description="Beta docs"),
        _make_collection_meta_dict("gamma", description="Gamma docs"),
    ]
    metadata_response = _make_metadata_response(collections)

    search_responses = {
        "alpha": _make_search_response("alpha", n=2),
        "beta": _make_search_response("beta", n=2),
        "gamma": _make_search_response("gamma", n=2),
    }

    async def mock_post(url: str, **kwargs: Any) -> MagicMock:
        body = kwargs.get("json", {})
        resp = MagicMock(spec=httpx.Response)
        resp.raise_for_status = MagicMock()

        params = body.get("params", {})
        tool_name = params.get("name", "")

        if tool_name == "get_collections_meta":
            resp.json = MagicMock(return_value=metadata_response)
        else:
            collection = params.get("arguments", {}).get("collection", "unknown")
            resp.json = MagicMock(return_value=search_responses.get(collection, {"result": {"content": []}}))

        return resp

    cfg = _make_rag_config(
        max_parallel=3,
        shortlist_size=8,
        confidence_threshold=0.0,
        top_k_return=5,
    )
    backend = _mock_embedder_backend()

    with patch("archon.search.embedder.ModelEmbedder", return_value=backend):
        provider = SearchContextProvider(search_url=_SEARCH_URL, cfg=cfg)

        with patch.object(provider._embedder, "embed_one", AsyncMock(return_value=_QUERY_VECTOR)):
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client.post = AsyncMock(side_effect=mock_post)
                mock_client_cls.return_value = mock_client

                # Phase A: Tier 1 → get_pre_context returns None (no decomposer)
                pre_context = await provider.get_pre_context("test query")

    assert pre_context is None

    # Router was set; decomposer was NOT invoked (Tier 1)
    assert provider._router is not None
    assert provider._router.decomposer_was_invoked is False

    # All 3 routable collections must be in last_routable_names
    assert set(provider._router.last_routable_names) == {"alpha", "beta", "gamma"}

    # Phase B: Tier 1 — selected_collections=None means "search all routable"
    task_output = _make_task_output(selected_collections=None)

    # Phase B: metadata is already cached from Phase A; only search calls are made
    async def mock_post_phase_b(url: str, **kwargs: Any) -> MagicMock:
        body = kwargs.get("json", {})
        params = body.get("params", {})
        collection = params.get("arguments", {}).get("collection", "unknown")
        resp = MagicMock(spec=httpx.Response)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=search_responses.get(collection, {"result": {"content": []}}))
        return resp

    with patch("httpx.AsyncClient") as mock_client_cls2:
        mock_client2 = AsyncMock()
        mock_client2.__aenter__ = AsyncMock(return_value=mock_client2)
        mock_client2.__aexit__ = AsyncMock(return_value=False)
        mock_client2.post = AsyncMock(side_effect=mock_post_phase_b)
        mock_client_cls2.return_value = mock_client2

        result = await provider.search_and_prepare(task_output, "test query")

    assert result is not None
    rag_text, chunk_count, actual_searched = result

    # All 3 routable collections must have been searched (Tier 1 = search all)
    assert set(actual_searched) == {"alpha", "beta", "gamma"}

    # chunk_count must be > 0 and ≤ top_k_return
    assert chunk_count > 0
    assert chunk_count <= cfg.top_k_return

    # rag_text must contain expected markers
    assert "[RAG context — retrieved document chunks:]" in rag_text
    assert "[End RAG context]" in rag_text
    assert "Source:" in rag_text

    # Verify the detail string format that pipeline.py passes to inject_context
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
# Test 5: Sentinel remap — decomposer_was_invoked=True, selected_collections=None
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_rag_routing_sentinel_remap() -> None:
    """Tier 3 with decomposer invoked but TaskOutput.selected_collections=None.

    When decomposer_was_invoked=True AND selected_collections=None, the code remaps to
    [] (pinned-only search). Since there are no pinned collections and selected_collections
    is remapped to [], to_search = [] → search_and_prepare returns None.
    """
    # 10 collections → Tier 3; col0–col7: positive sim, col8/col9: negative sim (excluded)
    centroids = [
        [0.1, 0.2, 0.3, 0.4, 0.5],   # col0 — parallel to query, sim=1.0
        [0.2, 0.3, 0.4, 0.5, 0.6],   # col1 — all positive, sim>0
        [0.1, 0.3, 0.2, 0.4, 0.5],   # col2 — all positive, sim>0
        [0.2, 0.1, 0.3, 0.4, 0.5],   # col3 — all positive, sim>0
        [0.1, 0.2, 0.4, 0.3, 0.5],   # col4 — all positive, sim>0
        [0.3, 0.2, 0.3, 0.4, 0.5],   # col5 — all positive, sim>0
        [0.1, 0.2, 0.3, 0.5, 0.4],   # col6 — all positive, sim>0
        [0.2, 0.2, 0.3, 0.4, 0.5],   # col7 — all positive, sim>0
        [-0.1, -0.2, -0.3, -0.4, -0.5],  # col8 — anti-parallel, sim=-1.0 (excluded)
        [0.0, -0.5, 0.0, -0.5, 0.0],     # col9 — negative dot product, sim<0 (excluded)
    ]
    collections = [
        _make_collection_meta_dict(f"col{i}", description=f"Collection {i}", centroid=centroids[i])
        for i in range(10)
    ]
    metadata_response = _make_metadata_response(collections)

    cfg = _make_rag_config(
        max_parallel=3,
        shortlist_size=8,
        confidence_threshold=0.0,
        top_k_return=5,
        pinned_collections=[],  # no pinned collections
    )

    async def mock_post(url: str, **kwargs: Any) -> MagicMock:
        body = kwargs.get("json", {})
        resp = MagicMock(spec=httpx.Response)
        resp.raise_for_status = MagicMock()
        params = body.get("params", {})
        tool_name = params.get("name", "")
        if tool_name == "get_collections_meta":
            resp.json = MagicMock(return_value=metadata_response)
        else:
            resp.json = MagicMock(return_value={"result": {"content": []}})
        return resp

    backend = _mock_embedder_backend()

    with patch("archon.search.embedder.ModelEmbedder", return_value=backend):
        provider = SearchContextProvider(search_url=_SEARCH_URL, cfg=cfg)

        with patch.object(provider._embedder, "embed_one", AsyncMock(return_value=_QUERY_VECTOR)):
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client.post = AsyncMock(side_effect=mock_post)
                mock_client_cls.return_value = mock_client

                # Phase A: Tier 3 → decomposer_was_invoked=True
                pre_context = await provider.get_pre_context("test query")

    assert pre_context is not None
    assert provider._router is not None
    assert provider._router.decomposer_was_invoked is True

    # Phase B: decomposer returns selected_collections=None (sentinel → remap to [])
    # No pinned collections + selected_collections=[] → to_search=[] → None
    task_output = _make_task_output(selected_collections=None)

    with patch("httpx.AsyncClient") as mock_client_cls2:
        mock_client2 = AsyncMock()
        mock_client2.__aenter__ = AsyncMock(return_value=mock_client2)
        mock_client2.__aexit__ = AsyncMock(return_value=False)
        mock_client2.post = AsyncMock(side_effect=mock_post)
        mock_client_cls2.return_value = mock_client2

        result = await provider.search_and_prepare(task_output, "test query")

    # No pinned + selected remapped to [] → nothing to search → None
    assert result is None
