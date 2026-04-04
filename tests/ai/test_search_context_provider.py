"""Tests for SearchContextProvider — multi-collection search retrieval orchestrator (FEAT-022 Task 3.1)."""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.decomposer import TaskOutput
from archon.search._types import SearchResult
from archon.search.collection_meta import CollectionMeta


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_meta(name: str, description: str = "desc") -> CollectionMeta:
    return CollectionMeta(name=name, description=description)


def _make_search_result(text: str, score: float, collection: str = "col") -> SearchResult:
    return SearchResult(
        doc_id=f"{collection}-doc",
        chunk_id=f"{collection}-chunk",
        text=text,
        score=score,
        source_path=f"/path/{collection}.md",
    )


def _make_rag_config(
    *,
    max_parallel: int = 3,
    shortlist_size: int = 8,
    confidence_threshold: float = 0.30,
    pinned_collections: list[str] | None = None,
    top_k_return: int = 5,
    embedding_model: str = "BAAI/bge-small-en-v1.5",
    providers: list[str] | None = None,
) -> MagicMock:
    cfg = MagicMock()
    cfg.max_parallel_collections = max_parallel
    cfg.routing_shortlist_size = shortlist_size
    cfg.routing_confidence_threshold = confidence_threshold
    cfg.pinned_collections = pinned_collections if pinned_collections is not None else []
    cfg.top_k_return = top_k_return
    cfg.embedding_model = embedding_model
    cfg.providers = providers or []
    cfg.host = "localhost"
    cfg.port = 8282
    return cfg


def _make_task_output(
    selected_collections: list[str] | None,
    scope: str = "small",
    prompt: str = "do it",
) -> TaskOutput:
    to = TaskOutput(scope=scope, prompt=prompt)
    to.selected_collections = selected_collections
    return to


def _mock_router(
    *,
    metadata: list[CollectionMeta] | None = None,
    pre_context: str | None = "some context",
    last_routable_names: list[str] | None = None,
    decomposer_was_invoked: bool = True,
) -> MagicMock:
    """Build a mock MultiCollectionRouter."""
    router = MagicMock()
    _meta = metadata or []
    router.fetch_metadata = AsyncMock(return_value=_meta)
    router.get_pre_context = AsyncMock(return_value=pre_context)
    router.last_routable_names = last_routable_names or []
    router.decomposer_was_invoked = decomposer_was_invoked
    return router


def _make_search_response(results: list[SearchResult]) -> dict[str, Any]:
    """Build a JSON-RPC response for a search call."""
    return {
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps([
                        {
                            "doc_id": r.doc_id,
                            "chunk_id": r.chunk_id,
                            "text": r.text,
                            "score": r.score,
                            "source_path": r.source_path,
                        }
                        for r in results
                    ]),
                }
            ]
        }
    }


# ──────────────────────────────────────────────────────────────────
# Import & basic instantiation
# ──────────────────────────────────────────────────────────────────


def test_search_context_provider_import() -> None:
    from archon.ai.search_context_provider import SearchContextProvider  # noqa: F401


def test_search_context_provider_instantiates() -> None:
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config()
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)
    assert provider is not None


# ──────────────────────────────────────────────────────────────────
# get_pre_context() — calls router and returns block
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_pre_context_empty_metadata() -> None:
    """When metadata fetch returns empty list, get_pre_context returns None."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config()
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    router = _mock_router(metadata=[], pre_context=None, last_routable_names=[])
    with patch("archon.ai.search_context_provider.MultiCollectionRouter", return_value=router):
        result = await provider.get_pre_context("query")

    assert result is None


@pytest.mark.asyncio
async def test_get_pre_context_returns_none_when_no_slots_for_decomposer() -> None:
    """When max_parallel <= len(pinned), get_pre_context returns None (slot exhaustion)."""
    from archon.ai.search_context_provider import SearchContextProvider

    # 2 pinned paths, max_parallel=2 → 0 available slots
    cfg = _make_rag_config(
        max_parallel=2,
        pinned_collections=["/path/a", "/path/b"],
    )
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    meta = [
        _make_meta("a"),
        _make_meta("b"),
        _make_meta("routable"),
    ]
    router = _mock_router(metadata=meta, pre_context=None, last_routable_names=[])
    router.decomposer_was_invoked = False

    with patch("archon.ai.search_context_provider.MultiCollectionRouter", return_value=router):
        with patch("archon.ai.search_context_provider.path_to_collection_name", side_effect=lambda p: p.split("/")[-1]):
            result = await provider.get_pre_context("query")

    # Slot exhaustion → None returned (router's get_pre_context was called with available_slots=0)
    assert result is None


@pytest.mark.asyncio
async def test_get_pre_context_passes_resolved_pinned_and_slots_to_router() -> None:
    """get_pre_context() resolves pinned paths and computes available_slots correctly."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(
        max_parallel=3,
        pinned_collections=["/path/pinned1"],
    )
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    meta = [_make_meta("pinned1"), _make_meta("col1"), _make_meta("col2")]
    router = _mock_router(metadata=meta, pre_context="<search_collections>...</search_collections>")
    router.decomposer_was_invoked = True
    router.last_routable_names = ["col1", "col2"]

    with patch("archon.ai.search_context_provider.MultiCollectionRouter", return_value=router):
        with patch("archon.ai.search_context_provider.path_to_collection_name", return_value="pinned1"):
            result = await provider.get_pre_context("query")

    router.get_pre_context.assert_called_once_with(
        "query", pinned_names=["pinned1"], available_slots=2
    )
    assert result is not None


# ──────────────────────────────────────────────────────────────────
# search_and_prepare() — three-way branch
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tier1_skips_decomposer_searches_all_routable() -> None:
    """Tier 1 path: selected_collections=None and decomposer NOT invoked → search all routable."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    # Simulate get_pre_context having been called (Tier 1 state)
    provider._router = _mock_router(decomposer_was_invoked=False)
    provider._router.last_routable_names = ["col1", "col2"]
    provider._router.decomposer_was_invoked = False
    provider._pinned_names = []

    task_output = _make_task_output(selected_collections=None)

    results_col1 = [_make_search_result("chunk from col1", 0.9, "col1")]
    results_col2 = [_make_search_result("chunk from col2", 0.7, "col2")]

    async def _mock_search(url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        if collection == "col1":
            return results_col1
        return results_col2

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        result = await provider.search_and_prepare(task_output, "my query")

    assert result is not None
    rag_text, chunk_count, searched_names = result
    assert "col1" in searched_names or "col2" in searched_names
    assert chunk_count > 0


@pytest.mark.asyncio
async def test_tier1_cap_applies_to_routable_not_total() -> None:
    """Tier 1: routable is capped at max_parallel; pinned always included in full."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(max_parallel=2)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    provider._router = MagicMock()
    provider._router.last_routable_names = ["col1", "col2", "col3"]  # 3 routable
    provider._router.decomposer_was_invoked = False
    provider._pinned_names = ["pinned1"]  # 1 pinned

    task_output = _make_task_output(selected_collections=None)

    searched: list[str] = []

    async def _mock_search(url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        searched.append(collection)
        return [_make_search_result("text", 0.8, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        result = await provider.search_and_prepare(task_output, "query")

    # Tier 1: pinned1 + up to max_parallel=2 routable → pinned1 + col1, col2
    assert "pinned1" in searched
    # Routable capped at max_parallel=2
    routable_searched = [s for s in searched if s != "pinned1"]
    assert len(routable_searched) <= 2


@pytest.mark.asyncio
async def test_search_and_prepare_caps_at_3_collections() -> None:
    """Non-pinned selected collections are capped at max_parallel - len(pinned)."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    provider._router = MagicMock()
    provider._router.last_routable_names = ["col1", "col2", "col3", "col4", "col5"]
    provider._router.decomposer_was_invoked = True
    provider._pinned_names = []

    task_output = _make_task_output(selected_collections=["col1", "col2", "col3", "col4"])

    searched: list[str] = []

    async def _mock_search(url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        searched.append(collection)
        return [_make_search_result("text", 0.8, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        await provider.search_and_prepare(task_output, "query")

    # Cap = max_parallel(3) - len(pinned)(0) = 3
    assert len(searched) <= 3


@pytest.mark.asyncio
async def test_search_and_prepare_returns_none_when_no_routable_state() -> None:
    """When router was not called (no get_pre_context call), returns None."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config()
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)
    # No _router set — fresh provider
    task_output = _make_task_output(selected_collections=None)

    result = await provider.search_and_prepare(task_output, "query")
    assert result is None


@pytest.mark.asyncio
async def test_search_and_prepare_selected_empty_list_searches_pinned_only() -> None:
    """Empty selected_collections → search pinned only."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    provider._router = MagicMock()
    provider._router.last_routable_names = ["col1", "col2"]
    provider._router.decomposer_was_invoked = True
    provider._pinned_names = ["pinned1"]

    task_output = _make_task_output(selected_collections=[])

    searched: list[str] = []

    async def _mock_search(url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        searched.append(collection)
        return [_make_search_result("text", 0.8, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        result = await provider.search_and_prepare(task_output, "query")

    assert searched == ["pinned1"]
    assert result is not None


@pytest.mark.asyncio
async def test_search_and_prepare_empty_selected_no_pinned_returns_none() -> None:
    """Empty selected and no pinned → nothing to search → return None."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    provider._router = MagicMock()
    provider._router.last_routable_names = ["col1"]
    provider._router.decomposer_was_invoked = True
    provider._pinned_names = []

    task_output = _make_task_output(selected_collections=[])

    with patch("archon.ai.search_context_provider._search_collection"):
        result = await provider.search_and_prepare(task_output, "query")

    assert result is None


@pytest.mark.asyncio
async def test_search_and_prepare_remaps_none_to_empty_list_when_decomposer_was_invoked() -> None:
    """When decomposer was invoked but selected_collections is None, remap to [] → pinned only."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    provider._router = MagicMock()
    provider._router.last_routable_names = ["col1"]
    provider._router.decomposer_was_invoked = True
    provider._pinned_names = ["pinned1"]

    # selected_collections=None, but decomposer WAS invoked → remap to []
    task_output = _make_task_output(selected_collections=None)

    searched: list[str] = []

    async def _mock_search(url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        searched.append(collection)
        return [_make_search_result("text", 0.8, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        result = await provider.search_and_prepare(task_output, "query")

    # Remapped to [] → pinned only
    assert searched == ["pinned1"]
    assert result is not None


# ──────────────────────────────────────────────────────────────────
# Pinned collection behaviour
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pinned_collections_bypass_confidence_gate() -> None:
    """Pinned collections are searched even when confidence gate fails for routable."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(max_parallel=3, pinned_collections=["/path/pinned1"])
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    meta = [_make_meta("pinned1"), _make_meta("routable1"), _make_meta("routable2")]
    router = _mock_router(
        metadata=meta,
        pre_context=None,  # confidence gate failed
        decomposer_was_invoked=False,
    )
    router.last_routable_names = []  # gate failed

    with patch("archon.ai.search_context_provider.MultiCollectionRouter", return_value=router):
        with patch("archon.ai.search_context_provider.path_to_collection_name", return_value="pinned1"):
            await provider.get_pre_context("query")

    # After get_pre_context, pinned names should be resolved
    assert "pinned1" in provider._pinned_names

    task_output = _make_task_output(selected_collections=None)
    searched: list[str] = []

    async def _mock_search(url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        searched.append(collection)
        return [_make_search_result("text", 0.9, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        result = await provider.search_and_prepare(task_output, "query")

    assert "pinned1" in searched
    assert result is not None


@pytest.mark.asyncio
async def test_pinned_collections_excluded_from_decomposer_block() -> None:
    """Pinned collections must NOT appear in the <search_collections> block."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(max_parallel=3, pinned_collections=["/path/pinned1"])
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    meta = [_make_meta("pinned1"), _make_meta("col1"), _make_meta("col2"), _make_meta("col3"), _make_meta("col4")]
    router = _mock_router(
        metadata=meta,
        pre_context="<search_collections>col1, col2, col3, col4</search_collections>",
        last_routable_names=["col1", "col2", "col3", "col4"],
    )
    router.decomposer_was_invoked = True

    called_with: dict[str, Any] = {}

    original_get_pre_context = router.get_pre_context

    async def _tracking_get_pre_context(query: str, pinned_names: list[str], available_slots: int) -> str | None:
        called_with["pinned_names"] = pinned_names
        called_with["available_slots"] = available_slots
        return await original_get_pre_context(query, pinned_names=pinned_names, available_slots=available_slots)

    router.get_pre_context = _tracking_get_pre_context

    with patch("archon.ai.search_context_provider.MultiCollectionRouter", return_value=router):
        with patch("archon.ai.search_context_provider.path_to_collection_name", return_value="pinned1"):
            result = await provider.get_pre_context("query")

    # pinned1 is resolved and passed to router; the block from router shouldn't include it
    assert "pinned1" in called_with.get("pinned_names", [])
    # The block returned by router does not include pinned1
    if result:
        assert "pinned1" not in result


@pytest.mark.asyncio
async def test_pinned_only_search_when_router_selects_zero() -> None:
    """Decomposer selects zero routable → only pinned collections are searched."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    provider._router = MagicMock()
    provider._router.last_routable_names = ["col1", "col2"]
    provider._router.decomposer_was_invoked = True
    provider._pinned_names = ["pinned1"]

    task_output = _make_task_output(selected_collections=[])

    searched: list[str] = []

    async def _mock_search(url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        searched.append(collection)
        return [_make_search_result("text", 0.8, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        result = await provider.search_and_prepare(task_output, "query")

    assert searched == ["pinned1"]
    assert result is not None


@pytest.mark.asyncio
async def test_pinned_and_selected_merged() -> None:
    """2 pinned + 1 decomposer-selected → results from all 3 are merged."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    provider._router = MagicMock()
    provider._router.last_routable_names = ["col1"]
    provider._router.decomposer_was_invoked = True
    provider._pinned_names = ["pinned1", "pinned2"]

    task_output = _make_task_output(selected_collections=["col1"])

    searched: list[str] = []

    async def _mock_search(url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        searched.append(collection)
        return [_make_search_result(f"text from {collection}", 0.8, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        result = await provider.search_and_prepare(task_output, "query")

    assert set(searched) == {"pinned1", "pinned2", "col1"}
    assert result is not None


@pytest.mark.asyncio
async def test_pinned_counts_toward_max_parallel() -> None:
    """With 2 pinned and max_parallel=3, only 1 routable slot remains."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    provider._router = MagicMock()
    provider._router.last_routable_names = ["col1", "col2", "col3"]
    provider._router.decomposer_was_invoked = True
    provider._pinned_names = ["pinned1", "pinned2"]

    # Decomposer selected 3, but only 1 routable slot
    task_output = _make_task_output(selected_collections=["col1", "col2", "col3"])

    searched: list[str] = []

    async def _mock_search(url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        searched.append(collection)
        return [_make_search_result("text", 0.8, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        await provider.search_and_prepare(task_output, "query")

    routable_searched = [s for s in searched if not s.startswith("pinned")]
    assert len(routable_searched) <= 1


@pytest.mark.asyncio
async def test_pinned_empty_list_routes_normally() -> None:
    """pinned_collections=[] → all collections go through routing normally."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(max_parallel=3, pinned_collections=[])
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    meta = [_make_meta("col1"), _make_meta("col2")]
    router = _mock_router(
        metadata=meta,
        pre_context=None,
        last_routable_names=["col1", "col2"],
        decomposer_was_invoked=False,
    )

    with patch("archon.ai.search_context_provider.MultiCollectionRouter", return_value=router):
        await provider.get_pre_context("query")

    assert provider._pinned_names == []
    # No pinned → all routable
    assert set(provider._router.last_routable_names) == {"col1", "col2"}


@pytest.mark.asyncio
async def test_pinned_unknown_path_silently_skipped() -> None:
    """Pinned path not in metadata is silently skipped; no error raised."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(max_parallel=3, pinned_collections=["/unknown/path"])
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    meta = [_make_meta("col1"), _make_meta("col2")]  # "path" not in metadata
    router = _mock_router(metadata=meta, pre_context=None, last_routable_names=["col1", "col2"])

    with patch("archon.ai.search_context_provider.MultiCollectionRouter", return_value=router):
        with patch("archon.ai.search_context_provider.path_to_collection_name", return_value="path"):
            await provider.get_pre_context("query")

    # "path" resolved but not in metadata → silently skipped
    assert "path" not in provider._pinned_names


@pytest.mark.asyncio
async def test_actual_searched_names_includes_pinned() -> None:
    """actual_searched_names in the result includes both pinned and router-selected names."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    provider._router = MagicMock()
    provider._router.last_routable_names = ["col1"]
    provider._router.decomposer_was_invoked = True
    provider._pinned_names = ["pinned1"]

    task_output = _make_task_output(selected_collections=["col1"])

    async def _mock_search(url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        return [_make_search_result("text", 0.8, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        result = await provider.search_and_prepare(task_output, "query")

    assert result is not None
    _, _, actual_searched_names = result
    assert "pinned1" in actual_searched_names
    assert "col1" in actual_searched_names


@pytest.mark.asyncio
async def test_pinned_exhausts_max_parallel_cap() -> None:
    """When pinned fills max_parallel slots, selected routable gets 0 slots (capped at 0)."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(max_parallel=2)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    provider._router = MagicMock()
    provider._router.last_routable_names = ["col1", "col2"]
    provider._router.decomposer_was_invoked = True
    provider._pinned_names = ["pinned1", "pinned2"]  # fills all slots

    task_output = _make_task_output(selected_collections=["col1", "col2"])

    searched: list[str] = []

    async def _mock_search(url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        searched.append(collection)
        return [_make_search_result("text", 0.8, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        await provider.search_and_prepare(task_output, "query")

    routable_searched = [s for s in searched if not s.startswith("pinned")]
    assert len(routable_searched) == 0
    # Only pinned are searched
    assert set(searched) == {"pinned1", "pinned2"}


# ──────────────────────────────────────────────────────────────────
# Score normalization
# ──────────────────────────────────────────────────────────────────


def test_score_normalization_single_result() -> None:
    """Single result in a collection gets normalized score 0.5."""
    from archon.ai.search_context_provider import _normalize_and_merge

    per_collection = {
        "col1": [_make_search_result("text", 0.9, "col1")],
    }
    merged = _normalize_and_merge(per_collection, top_k=5)
    assert len(merged) == 1
    # Single result → normalized to 0.5 (fallback for max==min)
    assert merged[0].score == pytest.approx(0.5)


def test_score_normalization_identical_scores() -> None:
    """Multiple results with identical scores all get normalized to 0.5."""
    from archon.ai.search_context_provider import _normalize_and_merge

    per_collection = {
        "col1": [
            _make_search_result("text1", 0.8, "col1"),
            _make_search_result("text2", 0.8, "col1"),
        ],
    }
    merged = _normalize_and_merge(per_collection, top_k=5)
    for r in merged:
        assert r.score == pytest.approx(0.5)


def test_score_normalization_multi_result_spread() -> None:
    """Multi-result collection: max gets 1.0, min gets 0.0."""
    from archon.ai.search_context_provider import _normalize_and_merge

    per_collection = {
        "col1": [
            _make_search_result("top", 1.0, "col1"),
            _make_search_result("mid", 0.5, "col1"),
            _make_search_result("bot", 0.0, "col1"),
        ],
    }
    merged = _normalize_and_merge(per_collection, top_k=5)
    scores = {r.text: r.score for r in merged}
    assert scores["top"] == pytest.approx(1.0)
    assert scores["mid"] == pytest.approx(0.5)
    assert scores["bot"] == pytest.approx(0.0)


def test_score_normalization_top_k_applied() -> None:
    """_normalize_and_merge returns at most top_k results."""
    from archon.ai.search_context_provider import _normalize_and_merge

    per_collection = {
        "col1": [_make_search_result(f"t{i}", float(i) / 10, "col1") for i in range(10)],
    }
    merged = _normalize_and_merge(per_collection, top_k=3)
    assert len(merged) <= 3


# ──────────────────────────────────────────────────────────────────
# Tier boundary tests
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tier_boundary_3_vs_4_routable() -> None:
    """3 routable → Tier 1 (decomposer not invoked); 4 routable → Tier 2 (decomposer invoked)."""
    from archon.ai.search_context_provider import SearchContextProvider

    # Tier 1: 3 routable
    cfg = _make_rag_config(max_parallel=5, shortlist_size=8)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    meta3 = [_make_meta(f"col{i}") for i in range(3)]
    router3 = _mock_router(metadata=meta3, pre_context=None, last_routable_names=[f"col{i}" for i in range(3)])
    router3.decomposer_was_invoked = False

    with patch("archon.ai.search_context_provider.MultiCollectionRouter", return_value=router3):
        result3 = await provider.get_pre_context("query")

    # Tier 1 → None (no decomposer block)
    assert result3 is None
    assert not provider._router.decomposer_was_invoked

    # Tier 2: 4 routable
    provider2 = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)
    meta4 = [_make_meta(f"col{i}") for i in range(4)]
    router4 = _mock_router(
        metadata=meta4,
        pre_context="<search_collections>...</search_collections>",
        last_routable_names=[f"col{i}" for i in range(4)],
    )
    router4.decomposer_was_invoked = True

    with patch("archon.ai.search_context_provider.MultiCollectionRouter", return_value=router4):
        result4 = await provider2.get_pre_context("query")

    assert result4 is not None
    assert provider2._router.decomposer_was_invoked


# ──────────────────────────────────────────────────────────────────
# Parsing filters hallucinated names
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rag_parsing_filters_hallucinated_names() -> None:
    """Names not in _last_routable_names are discarded (hallucinated names)."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    provider._router = MagicMock()
    provider._router.last_routable_names = ["col1", "col2"]
    provider._router.decomposer_was_invoked = True
    provider._pinned_names = []

    # Decomposer returned "hallucinated" which is NOT in _last_routable_names
    task_output = _make_task_output(selected_collections=["col1", "hallucinated"])

    searched: list[str] = []

    async def _mock_search(url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        searched.append(collection)
        return [_make_search_result("text", 0.8, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        await provider.search_and_prepare(task_output, "query")

    assert "hallucinated" not in searched
    assert "col1" in searched


@pytest.mark.asyncio
async def test_rag_parsing_handles_extra_whitespace() -> None:
    """Names with leading/trailing whitespace (including newlines) are stripped."""
    from archon.ai.decomposer import Decomposer

    with patch("archon.ai.decomposer.ClaudeSession"):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock"):
            d = Decomposer()

    raw = '{"scope":"small","prompt":"p"}\n<search_selected_collections>  foo  \n  bar  </search_selected_collections>'
    result = d._parse_task_output(raw, "original")
    assert result.selected_collections == ["foo", "bar"]


@pytest.mark.asyncio
async def test_rag_skips_when_parsing_yields_zero_collections() -> None:
    """selected_collections=[] with no pinned → search_and_prepare returns None."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    provider._router = MagicMock()
    provider._router.last_routable_names = ["col1"]
    provider._router.decomposer_was_invoked = True
    provider._pinned_names = []

    task_output = _make_task_output(selected_collections=[])

    with patch("archon.ai.search_context_provider._search_collection"):
        result = await provider.search_and_prepare(task_output, "query")

    assert result is None


@pytest.mark.asyncio
async def test_rag_parsing_multiple_tags_uses_first() -> None:
    """When <search_selected_collections> appears multiple times, use only the first."""
    from archon.ai.decomposer import Decomposer

    with patch("archon.ai.decomposer.ClaudeSession"):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock"):
            d = Decomposer()

    raw = (
        '{"scope":"small","prompt":"p"}'
        "<search_selected_collections>first</search_selected_collections>"
        "<search_selected_collections>second</search_selected_collections>"
    )
    result = d._parse_task_output(raw, "original")
    assert result.selected_collections == ["first"]


@pytest.mark.asyncio
async def test_rag_parsing_unclosed_tag_skips_rag() -> None:
    """Unclosed tag → selected_collections=[] → empty list (not None)."""
    from archon.ai.decomposer import Decomposer

    with patch("archon.ai.decomposer.ClaudeSession"):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock"):
            d = Decomposer()

    raw = '{"scope":"small","prompt":"p"}<search_selected_collections>col1'
    result = d._parse_task_output(raw, "original")
    assert result.selected_collections == []


@pytest.mark.asyncio
async def test_rag_parsing_empty_tag_skips_rag() -> None:
    """Empty tag → selected_collections=[] (not None)."""
    from archon.ai.decomposer import Decomposer

    with patch("archon.ai.decomposer.ClaudeSession"):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock"):
            d = Decomposer()

    raw = '{"scope":"small","prompt":"p"}<search_selected_collections></search_selected_collections>'
    result = d._parse_task_output(raw, "original")
    assert result.selected_collections == []


# ──────────────────────────────────────────────────────────────────
# Error handling
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rag_skips_on_server_error() -> None:
    """HTTP error during search → result excluded; if all fail, returns None."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    provider._router = MagicMock()
    provider._router.last_routable_names = ["col1"]
    provider._router.decomposer_was_invoked = True
    provider._pinned_names = []

    task_output = _make_task_output(selected_collections=["col1"])

    async def _failing_search(url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        raise ConnectionError("server down")

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_failing_search):
        result = await provider.search_and_prepare(task_output, "query")

    assert result is None


@pytest.mark.asyncio
async def test_rag_partial_search_failure_uses_remaining_results() -> None:
    """Partial search failure: successful collections are still merged."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    provider._router = MagicMock()
    provider._router.last_routable_names = ["col1", "col2"]
    provider._router.decomposer_was_invoked = True
    provider._pinned_names = []

    task_output = _make_task_output(selected_collections=["col1", "col2"])

    async def _mixed_search(url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        if collection == "col1":
            raise ConnectionError("col1 down")
        return [_make_search_result("from col2", 0.8, "col2")]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mixed_search):
        result = await provider.search_and_prepare(task_output, "query")

    assert result is not None
    rag_text, chunk_count, searched_names = result
    assert chunk_count == 1
    assert "from col2" in rag_text


@pytest.mark.asyncio
async def test_search_and_prepare_all_collections_fail_returns_none() -> None:
    """All searches fail → return None."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    provider._router = MagicMock()
    provider._router.last_routable_names = ["col1", "col2"]
    provider._router.decomposer_was_invoked = True
    provider._pinned_names = []

    task_output = _make_task_output(selected_collections=["col1", "col2"])

    async def _all_fail(url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        raise RuntimeError("all down")

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_all_fail):
        result = await provider.search_and_prepare(task_output, "query")

    assert result is None


@pytest.mark.asyncio
async def test_rag_skips_on_empty_shortlist() -> None:
    """When confidence gate fails (empty shortlist), get_pre_context returns None."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(max_parallel=5)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    meta = [_make_meta(f"col{i}") for i in range(10)]
    router = _mock_router(
        metadata=meta,
        pre_context=None,  # gate failed, empty shortlist
        last_routable_names=[],
        decomposer_was_invoked=False,
    )

    with patch("archon.ai.search_context_provider.MultiCollectionRouter", return_value=router):
        result = await provider.get_pre_context("query")

    assert result is None


# ──────────────────────────────────────────────────────────────────
# Parallel search bounds
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rag_parallel_search_bounded() -> None:
    """With 5 collections and max_parallel=2, at most 2 concurrent searches run."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(max_parallel=2)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    provider._router = MagicMock()
    provider._router.last_routable_names = ["c1", "c2", "c3", "c4", "c5"]
    provider._router.decomposer_was_invoked = True
    provider._pinned_names = []

    task_output = _make_task_output(selected_collections=["c1", "c2"])  # capped at 2

    concurrent_peak = 0
    current_concurrent = 0
    lock = asyncio.Lock()

    async def _bounded_search(url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        nonlocal concurrent_peak, current_concurrent
        async with lock:
            current_concurrent += 1
            if current_concurrent > concurrent_peak:
                concurrent_peak = current_concurrent
        await asyncio.sleep(0.01)
        async with lock:
            current_concurrent -= 1
        return [_make_search_result("text", 0.8, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_bounded_search):
        await provider.search_and_prepare(task_output, "query")

    assert concurrent_peak <= 2


# ──────────────────────────────────────────────────────────────────
# Pipeline integration: get_pre_context flow
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tier1_pipeline_does_not_call_route_task_with_rag_context() -> None:
    """In Tier 1, get_pre_context returns None → route_task called without rag context."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    meta = [_make_meta("col1"), _make_meta("col2")]  # only 2 → Tier 1
    router = _mock_router(
        metadata=meta,
        pre_context=None,
        last_routable_names=["col1", "col2"],
        decomposer_was_invoked=False,
    )

    with patch("archon.ai.search_context_provider.MultiCollectionRouter", return_value=router):
        pre_context = await provider.get_pre_context("query")

    assert pre_context is None


@pytest.mark.asyncio
async def test_rag_inject_context_called() -> None:
    """After search_and_prepare returns results, inject_context is called by caller."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    provider._router = MagicMock()
    provider._router.last_routable_names = ["col1"]
    provider._router.decomposer_was_invoked = False
    provider._pinned_names = []

    task_output = _make_task_output(selected_collections=None)

    async def _mock_search(url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        return [_make_search_result("useful text", 0.9, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        result = await provider.search_and_prepare(task_output, "query")

    # The caller (Pipeline) would call inject_context with the returned data
    assert result is not None
    rag_text, chunk_count, actual_searched = result
    assert chunk_count >= 1
    assert len(actual_searched) >= 1


@pytest.mark.asyncio
async def test_rag_selects_correct_collections() -> None:
    """Tier 2/3 path: only selected collections matching _last_routable_names are searched."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    provider._router = MagicMock()
    provider._router.last_routable_names = ["alpha", "beta", "gamma"]
    provider._router.decomposer_was_invoked = True
    provider._pinned_names = []

    task_output = _make_task_output(selected_collections=["alpha", "gamma"])

    searched: list[str] = []

    async def _mock_search(url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        searched.append(collection)
        return [_make_search_result("text", 0.8, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        await provider.search_and_prepare(task_output, "query")

    assert set(searched) == {"alpha", "gamma"}
    assert "beta" not in searched


@pytest.mark.asyncio
async def test_per_collection_search_timeout_excluded_from_results() -> None:
    """Individual collection timeout (asyncio.TimeoutError) is excluded from results."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    provider._router = MagicMock()
    provider._router.last_routable_names = ["fast", "slow"]
    provider._router.decomposer_was_invoked = True
    provider._pinned_names = []

    task_output = _make_task_output(selected_collections=["fast", "slow"])

    async def _mock_search(url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        if collection == "slow":
            raise asyncio.TimeoutError()
        return [_make_search_result("fast result", 0.9, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        result = await provider.search_and_prepare(task_output, "query")

    assert result is not None
    rag_text, chunk_count, searched_names = result
    assert "fast result" in rag_text
    assert chunk_count == 1


@pytest.mark.asyncio
async def test_tier1_cap_on_routable_total_searches() -> None:
    """Tier 1 with max_parallel=3: cap routable to 3 even if more exist."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    provider._router = MagicMock()
    # 5 routable in Tier 1 (decomposer not invoked)
    provider._router.last_routable_names = ["c1", "c2", "c3", "c4", "c5"]
    provider._router.decomposer_was_invoked = False
    provider._pinned_names = []

    task_output = _make_task_output(selected_collections=None)

    searched: list[str] = []

    async def _mock_search(url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        searched.append(collection)
        return [_make_search_result("text", 0.8, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        await provider.search_and_prepare(task_output, "query")

    # Tier 1: routable capped at max_parallel=3
    assert len(searched) <= 3


# ──────────────────────────────────────────────────────────────────
# _search_collection() unit tests
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_collection_happy_path() -> None:
    """_search_collection returns SearchResult list on successful response."""
    from archon.ai.search_context_provider import _search_collection
    import httpx
    from unittest.mock import AsyncMock, patch, MagicMock

    results_data = [
        {"doc_id": "d1", "chunk_id": "c1", "text": "hello", "score": 0.9, "source_path": "/f.md"}
    ]
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={
        "result": {"content": [{"type": "text", "text": json.dumps(results_data)}]}
    })

    with patch("archon.ai.search_context_provider.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=response)
        results = await _search_collection("http://localhost:9999", "col", "query", top_k=5)

    assert len(results) == 1
    assert results[0].text == "hello"
    assert results[0].score == 0.9


@pytest.mark.asyncio
async def test_search_collection_jsonrpc_error_returns_empty() -> None:
    """_search_collection returns [] on JSON-RPC error key."""
    from archon.ai.search_context_provider import _search_collection
    from unittest.mock import AsyncMock, patch, MagicMock

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={"error": {"code": -32600, "message": "bad"}})

    with patch("archon.ai.search_context_provider.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=response)
        results = await _search_collection("http://localhost:9999", "col", "query", top_k=5)

    assert results == []


@pytest.mark.asyncio
async def test_search_collection_empty_content_returns_empty() -> None:
    """_search_collection returns [] when content blocks are empty."""
    from archon.ai.search_context_provider import _search_collection
    from unittest.mock import AsyncMock, patch, MagicMock

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={"result": {"content": []}})

    with patch("archon.ai.search_context_provider.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=response)
        results = await _search_collection("http://localhost:9999", "col", "query", top_k=5)

    assert results == []


@pytest.mark.asyncio
async def test_search_collection_malformed_json_text_returns_empty() -> None:
    """_search_collection returns [] when text block contains invalid JSON."""
    from archon.ai.search_context_provider import _search_collection
    from unittest.mock import AsyncMock, patch, MagicMock

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={
        "result": {"content": [{"type": "text", "text": "not json at all"}]}
    })

    with patch("archon.ai.search_context_provider.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=response)
        results = await _search_collection("http://localhost:9999", "col", "query", top_k=5)

    assert results == []


@pytest.mark.asyncio
async def test_search_collection_error_entries_skipped() -> None:
    """_search_collection skips individual result entries that have an 'error' key."""
    from archon.ai.search_context_provider import _search_collection
    from unittest.mock import AsyncMock, patch, MagicMock

    results_data = [
        {"error": "not found"},
        {"doc_id": "d1", "chunk_id": "c1", "text": "good", "score": 0.8, "source_path": "/f.md"},
    ]
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={
        "result": {"content": [{"type": "text", "text": json.dumps(results_data)}]}
    })

    with patch("archon.ai.search_context_provider.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=response)
        results = await _search_collection("http://localhost:9999", "col", "query", top_k=5)

    assert len(results) == 1
    assert results[0].text == "good"


@pytest.mark.asyncio
async def test_search_collection_top_k_slices_results() -> None:
    """_search_collection respects the top_k limit."""
    from archon.ai.search_context_provider import _search_collection
    from unittest.mock import AsyncMock, patch, MagicMock

    results_data = [
        {"doc_id": f"d{i}", "chunk_id": f"c{i}", "text": f"t{i}", "score": 0.5, "source_path": "/f.md"}
        for i in range(10)
    ]
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={
        "result": {"content": [{"type": "text", "text": json.dumps(results_data)}]}
    })

    with patch("archon.ai.search_context_provider.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=response)
        results = await _search_collection("http://localhost:9999", "col", "query", top_k=3)

    assert len(results) == 3


# ──────────────────────────────────────────────────────────────────
# _format_results() unit tests
# ──────────────────────────────────────────────────────────────────


def test_format_results_empty_returns_empty_string() -> None:
    """_format_results returns '' for empty input."""
    from archon.ai.search_context_provider import _format_results

    assert _format_results([]) == ""


def test_format_results_single_result_format() -> None:
    """_format_results produces correct format with header and footer markers."""
    from archon.ai.search_context_provider import _format_results

    result = _make_search_result("chunk text here", 0.9, "col1")
    output = _format_results([result])

    assert "[RAG context" in output
    assert "[End RAG context]" in output
    assert "chunk text here" in output
    assert "/path/col1.md" in output


def test_format_results_numbers_results() -> None:
    """_format_results numbers results starting from 1."""
    from archon.ai.search_context_provider import _format_results

    results = [
        _make_search_result("first", 0.9),
        _make_search_result("second", 0.8),
    ]
    output = _format_results(results)

    assert "[1]" in output
    assert "[2]" in output
