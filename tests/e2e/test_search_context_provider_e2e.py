"""E2E tests for SearchContextProvider — SearchClient.search() path (FEAT-038 Task 7.2 → Task 5.1).

All tests use mock_client.search() — the old JSON-RPC / _http path has been removed.

H2.1–H2.12: happy-path contract tests.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from archon.ai.decomposer import TaskOutput
from archon.ai.search_context_provider import SearchContextProvider

try:
    from archon_search.types import RouteResponse
except ImportError:
    RouteResponse = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cfg(
    *,
    enabled: bool = True,
    max_parallel: int = 3,
    top_k_return: int = 5,
) -> MagicMock:
    cfg = MagicMock()
    cfg.enabled = enabled
    cfg.max_parallel_collections = max_parallel
    cfg.top_k_return = top_k_return
    return cfg


def _make_task_output(selected_collections: list[str] | None) -> TaskOutput:
    to = TaskOutput(scope="small", prompt="test query")
    to.selected_collections = selected_collections
    return to


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


def _make_mock_client(route_response: Any = None) -> MagicMock:
    client = MagicMock()
    client.route = AsyncMock(return_value=route_response)
    return client


def _search_result_dict(
    *,
    doc_id: str = "doc1",
    chunk_id: str = "chunk1",
    text: str = "chunk text",
    score: float = 0.9,
    source_path: str = "/docs/file.md",
) -> dict[str, Any]:
    return {
        "doc_id": doc_id,
        "chunk_id": chunk_id,
        "text": text,
        "score": score,
        "source_path": source_path,
    }


def _make_search_mock(results_by_collection: dict[str, list[dict[str, Any]]]) -> AsyncMock:
    """Return an AsyncMock for client.search() that returns results by collection name."""
    async def _search(collection: str, query: str, top_k: int) -> list[dict[str, Any]]:
        return results_by_collection.get(collection, [])
    return AsyncMock(side_effect=_search)


# ---------------------------------------------------------------------------
# H2.1 — No collections → get_pre_context returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_H2_1_no_collections_get_pre_context_returns_none(patched_search_client):
    """H2.1: When the server has no collections, get_pre_context() returns None."""
    cfg = _make_cfg()
    provider = SearchContextProvider(
        cfg=cfg,
        search_client=patched_search_client,
    )

    result = await provider.get_pre_context("what is archon?")

    # Fresh server has no collections → route returns empty routable_names → pre_context=None
    assert result is None


# ---------------------------------------------------------------------------
# H2.2 — After get_pre_context(), _route_response has pinned/routable names
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_H2_2_route_response_has_pinned_and_routable_names(patched_search_client, tmp_path):
    """H2.2: After get_pre_context(), _route_response carries pinned_names and routable_names."""
    # Add a collection so the server has something routable
    coll_path = str(tmp_path / "docs")
    await patched_search_client.add_collection(coll_path)

    cfg = _make_cfg()
    provider = SearchContextProvider(
        cfg=cfg,
        search_client=patched_search_client,
    )

    await provider.get_pre_context("test query")

    assert provider._route_response is not None
    assert hasattr(provider._route_response, "pinned_names")
    assert hasattr(provider._route_response, "routable_names")


# ---------------------------------------------------------------------------
# H2.3 — search_and_prepare without get_pre_context → returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_H2_3_search_and_prepare_without_pre_context_returns_none(patched_search_client):
    """H2.3: Calling search_and_prepare() without a prior get_pre_context() returns None."""
    cfg = _make_cfg()
    provider = SearchContextProvider(
        cfg=cfg,
        search_client=patched_search_client,
    )

    task_output = _make_task_output(selected_collections=None)
    result = await provider.search_and_prepare(task_output, "query")

    assert result is None


# ---------------------------------------------------------------------------
# H2.4 — Tier 1 (decomposer_invoked=False) → all routable + pinned searched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_H2_4_tier1_searches_all_routable_and_pinned():
    """H2.4: tier1 (decomposer_invoked=False) → searches all routable + pinned."""
    routable = ["col_a", "col_b"]
    results_by_collection = {
        "col_a": [_search_result_dict(doc_id="a1", chunk_id="a1", text="alpha text", score=0.9, source_path="/a.md")],
        "col_b": [_search_result_dict(doc_id="b1", chunk_id="b1", text="beta text", score=0.7, source_path="/b.md")],
    }
    mock_client = _make_mock_client(
        route_response=_make_route_response(
            pre_context=None,
            pinned_names=[],
            routable_names=routable,
            decomposer_invoked=False,
        )
    )
    mock_client.search = _make_search_mock(results_by_collection)

    cfg = _make_cfg(max_parallel=3)
    provider = SearchContextProvider(cfg=cfg, search_client=mock_client)

    await provider.get_pre_context("query")
    task_output = _make_task_output(selected_collections=None)
    result = await provider.search_and_prepare(task_output, "query")

    assert result is not None
    rag_text, chunk_count, actual_searched = result
    assert "col_a" in actual_searched
    assert "col_b" in actual_searched


# ---------------------------------------------------------------------------
# H2.5 — Tier 2 + selected=[] + pinned → only pinned searched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_H2_5_tier2_empty_selected_with_pinned_searches_only_pinned():
    """H2.5: tier2 + selected_collections=[] + pinned → only pinned searched."""
    results_by_collection = {
        "pinned": [_search_result_dict(doc_id="p1", chunk_id="p1", text="pinned chunk", score=0.95, source_path="/pinned.md")],
        "col_a": [_search_result_dict(doc_id="a1", chunk_id="a1", text="routable", score=0.8, source_path="/a.md")],
    }
    mock_client = _make_mock_client(
        route_response=_make_route_response(
            pre_context="<rag>pinned</rag>",
            pinned_names=["pinned"],
            routable_names=["col_a"],
            decomposer_invoked=True,
        )
    )
    mock_client.search = _make_search_mock(results_by_collection)

    cfg = _make_cfg(max_parallel=3)
    provider = SearchContextProvider(cfg=cfg, search_client=mock_client)

    await provider.get_pre_context("query")
    task_output = _make_task_output(selected_collections=[])
    result = await provider.search_and_prepare(task_output, "query")

    assert result is not None
    rag_text, chunk_count, actual_searched = result
    assert actual_searched == ["pinned"]
    assert "pinned chunk" in rag_text


# ---------------------------------------------------------------------------
# H2.6 — Tier 2 + selected=[] + no pinned → returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_H2_6_tier2_empty_selected_no_pinned_returns_none():
    """H2.6: tier2 + selected_collections=[] + no pinned → returns None."""
    mock_client = _make_mock_client(
        route_response=_make_route_response(
            pinned_names=[],
            routable_names=["col_a"],
            decomposer_invoked=True,
        )
    )
    mock_client.search = _make_search_mock({})

    cfg = _make_cfg(max_parallel=3)
    provider = SearchContextProvider(cfg=cfg, search_client=mock_client)

    await provider.get_pre_context("query")
    task_output = _make_task_output(selected_collections=[])
    result = await provider.search_and_prepare(task_output, "query")

    assert result is None


# ---------------------------------------------------------------------------
# H2.7 — Tier 3 + hallucinated collection → only real collection searched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_H2_7_tier3_hallucinated_collection_discarded():
    """H2.7: tier3 + hallucinated collection name → only real collection searched."""
    results_by_collection = {
        "real_col": [_search_result_dict(doc_id="r1", chunk_id="r1", text="real chunk", score=0.88, source_path="/real.md")],
    }
    mock_client = _make_mock_client(
        route_response=_make_route_response(
            pinned_names=[],
            routable_names=["real_col"],
            decomposer_invoked=True,
        )
    )
    mock_client.search = _make_search_mock(results_by_collection)

    cfg = _make_cfg(max_parallel=3)
    provider = SearchContextProvider(cfg=cfg, search_client=mock_client)

    await provider.get_pre_context("query")
    # Decomposer selected real_col + a hallucinated name
    task_output = _make_task_output(selected_collections=["real_col", "hallucinated_col"])
    result = await provider.search_and_prepare(task_output, "query")

    assert result is not None
    rag_text, chunk_count, actual_searched = result
    assert "hallucinated_col" not in actual_searched
    assert "real_col" in actual_searched
    assert "real chunk" in rag_text


# ---------------------------------------------------------------------------
# H2.8 — 10 routable + no pinned + max_parallel=3 → at most 3 routable searched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_H2_8_max_parallel_caps_routable():
    """H2.8: 10 routable + pinned_collections=[] + max_parallel=3 → at most 3 routable searched."""
    routable = [f"col_{i}" for i in range(10)]
    results_by_collection = {
        col: [_search_result_dict(doc_id=f"{col}-d", chunk_id=f"{col}-c", text=f"text from {col}", score=0.5, source_path=f"/{col}.md")]
        for col in routable
    }
    mock_client = _make_mock_client(
        route_response=_make_route_response(
            pinned_names=[],  # mandatory — pinned bypass cap
            routable_names=routable,
            decomposer_invoked=False,  # Tier 1: search all routable, capped by max_parallel
        )
    )
    mock_client.search = _make_search_mock(results_by_collection)

    cfg = _make_cfg(max_parallel=3, top_k_return=5)
    provider = SearchContextProvider(cfg=cfg, search_client=mock_client)

    await provider.get_pre_context("query")
    task_output = _make_task_output(selected_collections=None)
    result = await provider.search_and_prepare(task_output, "query")

    assert result is not None
    rag_text, chunk_count, actual_searched = result
    # Tier 1 caps routable at max_parallel_collections=3
    assert len(actual_searched) <= 3


# ---------------------------------------------------------------------------
# H2.9 — Merged results ranked by score (highest first)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_H2_9_merged_results_ranked_by_score():
    """H2.9: Results from multiple collections are merged and ranked by normalized score."""
    results_by_collection = {
        "col_low": [
            _search_result_dict(doc_id="l1", chunk_id="l1", text="low score chunk", score=0.2, source_path="/low.md"),
            _search_result_dict(doc_id="l2", chunk_id="l2", text="lower chunk", score=0.1, source_path="/low.md"),
        ],
        "col_high": [
            _search_result_dict(doc_id="h1", chunk_id="h1", text="high score chunk", score=0.95, source_path="/high.md"),
            _search_result_dict(doc_id="h2", chunk_id="h2", text="high chunk 2", score=0.85, source_path="/high.md"),
        ],
    }
    mock_client = _make_mock_client(
        route_response=_make_route_response(
            pinned_names=[],
            routable_names=["col_low", "col_high"],
            decomposer_invoked=False,
        )
    )
    mock_client.search = _make_search_mock(results_by_collection)

    cfg = _make_cfg(max_parallel=3, top_k_return=5)
    provider = SearchContextProvider(cfg=cfg, search_client=mock_client)

    await provider.get_pre_context("query")
    task_output = _make_task_output(selected_collections=None)
    result = await provider.search_and_prepare(task_output, "query")

    assert result is not None
    rag_text, chunk_count, actual_searched = result
    # After normalization per-collection, scores within each collection are 0..1;
    # the merged list is sorted by score descending.
    assert chunk_count >= 2
    assert "low score chunk" in rag_text or "lower chunk" in rag_text
    assert "high score chunk" in rag_text or "high chunk 2" in rag_text
    # Ranking: "high score chunk" normalizes to 1.0 (top of col_high);
    # "lower chunk" normalizes to 0.0 (bottom of col_low).
    assert rag_text.index("high score chunk") < rag_text.index("lower chunk")


# ---------------------------------------------------------------------------
# H2.10 — Score normalization per-collection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_H2_10_score_normalization_per_collection():
    """H2.10: Scores are normalized per-collection before merging; single-result collections get 0.5."""
    results_by_collection = {
        # Single result → score normalized to 0.5
        "col_single": [
            _search_result_dict(doc_id="s1", chunk_id="s1", text="single result", score=0.99, source_path="/s.md"),
        ],
        # Two results: max→1.0, min→0.0
        "col_range": [
            _search_result_dict(doc_id="r1", chunk_id="r1", text="top result", score=1.0, source_path="/r.md"),
            _search_result_dict(doc_id="r2", chunk_id="r2", text="bottom result", score=0.0, source_path="/r.md"),
        ],
    }
    mock_client = _make_mock_client(
        route_response=_make_route_response(
            pinned_names=[],
            routable_names=["col_single", "col_range"],
            decomposer_invoked=False,
        )
    )
    mock_client.search = _make_search_mock(results_by_collection)

    cfg = _make_cfg(max_parallel=3, top_k_return=5)
    provider = SearchContextProvider(cfg=cfg, search_client=mock_client)

    await provider.get_pre_context("query")
    task_output = _make_task_output(selected_collections=None)
    result = await provider.search_and_prepare(task_output, "query")

    assert result is not None
    rag_text, chunk_count, actual_searched = result
    assert chunk_count >= 2
    assert set(actual_searched) == {"col_single", "col_range"}
    # Merged order: "top result" (1.0) > "single result" (0.5) > "bottom result" (0.0)
    assert rag_text.index("top result") < rag_text.index("single result")
    assert rag_text.index("single result") < rag_text.index("bottom result")


# ---------------------------------------------------------------------------
# H2.11 — Returned text starts with [RAG context and ends with [End RAG context]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_H2_11_rag_text_has_correct_markers():
    """H2.11: The returned rag_text starts with '[RAG context' and ends with '[End RAG context]'."""
    results_by_collection = {
        "col_a": [_search_result_dict(doc_id="a1", chunk_id="a1", text="some chunk", score=0.8, source_path="/a.md")],
    }
    mock_client = _make_mock_client(
        route_response=_make_route_response(
            pinned_names=[],
            routable_names=["col_a"],
            decomposer_invoked=False,
        )
    )
    mock_client.search = _make_search_mock(results_by_collection)

    cfg = _make_cfg(max_parallel=3, top_k_return=5)
    provider = SearchContextProvider(cfg=cfg, search_client=mock_client)

    await provider.get_pre_context("query")
    task_output = _make_task_output(selected_collections=None)
    result = await provider.search_and_prepare(task_output, "query")

    assert result is not None
    rag_text, chunk_count, actual_searched = result
    assert rag_text.startswith("[RAG context")
    assert rag_text.endswith("[End RAG context]")


# ---------------------------------------------------------------------------
# H2.12 — Second element of tuple == number of merged chunks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_H2_12_chunk_count_matches_merged_results():
    """H2.12: The second element of the returned tuple equals the number of merged chunks."""
    # 2 results per collection × 2 collections = 4 raw; top_k_return=3 → 3 merged
    results_by_collection = {
        "col_a": [
            _search_result_dict(doc_id="a1", chunk_id="a1", text="chunk a1", score=0.9, source_path="/a.md"),
            _search_result_dict(doc_id="a2", chunk_id="a2", text="chunk a2", score=0.6, source_path="/a.md"),
        ],
        "col_b": [
            _search_result_dict(doc_id="b1", chunk_id="b1", text="chunk b1", score=0.8, source_path="/b.md"),
            _search_result_dict(doc_id="b2", chunk_id="b2", text="chunk b2", score=0.4, source_path="/b.md"),
        ],
    }
    mock_client = _make_mock_client(
        route_response=_make_route_response(
            pinned_names=[],
            routable_names=["col_a", "col_b"],
            decomposer_invoked=False,
        )
    )
    mock_client.search = _make_search_mock(results_by_collection)

    cfg = _make_cfg(max_parallel=3, top_k_return=3)
    provider = SearchContextProvider(cfg=cfg, search_client=mock_client)

    await provider.get_pre_context("query")
    task_output = _make_task_output(selected_collections=None)
    result = await provider.search_and_prepare(task_output, "query")

    assert result is not None
    rag_text, chunk_count, actual_searched = result
    # 4 raw results (2 per collection), top_k_return=3 → exactly 3 merged chunks
    assert chunk_count == 3


# ---------------------------------------------------------------------------
# E2.1 — route() returns None → get_pre_context returns None, no exception
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_E2_1_route_returns_none_get_pre_context_returns_none():
    """E2.1: When route() returns None, get_pre_context() returns None without raising."""
    mock_client = _make_mock_client(route_response=None)

    cfg = _make_cfg()
    provider = SearchContextProvider(cfg=cfg, search_client=mock_client)

    result = await provider.get_pre_context("what is archon?")

    assert result is None
    assert provider._route_response is None


# ---------------------------------------------------------------------------
# E2.2 — One collection returns error, others succeed → skipped; remaining returned
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_E2_2_one_collection_error_others_return_results(caplog):
    """E2.2: One collection raises → skipped, logged DEBUG; remaining results returned."""
    async def _search_with_error(collection: str, query: str, top_k: int) -> list[dict]:
        if collection == "col_error":
            raise ConnectionError("server down")
        return [_search_result_dict(doc_id="ok1", chunk_id="ok1", text="ok chunk", score=0.8, source_path="/ok.md")]

    mock_client = _make_mock_client(
        route_response=_make_route_response(
            pinned_names=[],
            routable_names=["col_error", "col_ok"],
            decomposer_invoked=False,
        )
    )
    mock_client.search = AsyncMock(side_effect=_search_with_error)

    cfg = _make_cfg(max_parallel=3, top_k_return=5)
    caplog.set_level(logging.DEBUG, logger="archon")
    provider = SearchContextProvider(cfg=cfg, search_client=mock_client)

    await provider.get_pre_context("query")
    task_output = _make_task_output(selected_collections=None)
    result = await provider.search_and_prepare(task_output, "query")

    assert result is not None
    rag_text, chunk_count, actual_searched = result
    assert "col_error" not in actual_searched
    assert "col_ok" in actual_searched
    assert "ok chunk" in rag_text
    assert chunk_count == 1
    debug_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG and r.name == "archon"]
    assert any("search failed for" in m and "col_error" in m for m in debug_messages)


# ---------------------------------------------------------------------------
# E2.3 — All collections error → search_and_prepare returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_E2_3_all_collections_error_returns_none():
    """E2.3: When all collections raise, search_and_prepare() returns None."""
    async def _all_fail(collection: str, query: str, top_k: int) -> list[dict]:
        raise ConnectionError("server down")

    mock_client = _make_mock_client(
        route_response=_make_route_response(
            pinned_names=[],
            routable_names=["col_a", "col_b"],
            decomposer_invoked=False,
        )
    )
    mock_client.search = AsyncMock(side_effect=_all_fail)

    cfg = _make_cfg(max_parallel=3, top_k_return=5)
    provider = SearchContextProvider(cfg=cfg, search_client=mock_client)

    await provider.get_pre_context("query")
    task_output = _make_task_output(selected_collections=None)
    result = await provider.search_and_prepare(task_output, "query")

    assert result is None


# ---------------------------------------------------------------------------
# E2.4 — Single result per collection → score normalized to 0.5
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_E2_4_single_result_per_collection_score_normalized_to_0_5():
    """E2.4: With only one result per collection, min==max → normalized score is 0.5.

    col_multi has TWO results (scores 1.0 and 0.0) → normalizes to [1.0, 0.0].
    col_a and col_b have ONE result each → both normalize to 0.5.
    Expected order: multi high (1.0) > only chunk a (0.5) > only chunk b (0.5) > multi low (0.0).
    Stable sort preserves col_a before col_b since they share the same normalized score.
    """
    results_by_collection = {
        "col_a": [_search_result_dict(doc_id="a1", chunk_id="a1", text="only chunk a", score=0.99, source_path="/a.md")],
        "col_b": [_search_result_dict(doc_id="b1", chunk_id="b1", text="only chunk b", score=0.11, source_path="/b.md")],
        "col_multi": [
            _search_result_dict(doc_id="m1", chunk_id="m1", text="multi high", score=1.0, source_path="/m.md"),
            _search_result_dict(doc_id="m2", chunk_id="m2", text="multi low", score=0.0, source_path="/m.md"),
        ],
    }
    mock_client = _make_mock_client(
        route_response=_make_route_response(
            pinned_names=[],
            routable_names=["col_a", "col_b", "col_multi"],
            decomposer_invoked=False,
        )
    )
    mock_client.search = _make_search_mock(results_by_collection)

    cfg = _make_cfg(max_parallel=5, top_k_return=5)
    provider = SearchContextProvider(cfg=cfg, search_client=mock_client)

    await provider.get_pre_context("query")
    task_output = _make_task_output(selected_collections=None)
    result = await provider.search_and_prepare(task_output, "query")

    assert result is not None
    rag_text, chunk_count, actual_searched = result
    assert chunk_count == 4
    assert "only chunk a" in rag_text
    assert "only chunk b" in rag_text
    assert "multi high" in rag_text
    assert "multi low" in rag_text
    # Ordering: multi high (1.0) > single-result cols (0.5) > multi low (0.0)
    first_pos = rag_text.index("multi high")
    last_pos = rag_text.index("multi low")
    only_a_pos = rag_text.index("only chunk a")
    only_b_pos = rag_text.index("only chunk b")
    assert first_pos < only_a_pos  # 1.0 > 0.5
    assert only_a_pos < last_pos   # 0.5 > 0.0
    assert only_b_pos < last_pos   # 0.5 > 0.0
    assert only_a_pos < only_b_pos  # stable sort: col_a processed before col_b → appears first


# ---------------------------------------------------------------------------
# E2.5 — All collections return empty lists → returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_E2_5_all_collections_empty_returns_none():
    """E2.5: When all collections return empty result lists, search_and_prepare() returns None."""
    results_by_collection: dict[str, list[dict[str, Any]]] = {"col_a": [], "col_b": []}
    mock_client = _make_mock_client(
        route_response=_make_route_response(
            pinned_names=[],
            routable_names=["col_a", "col_b"],
            decomposer_invoked=False,
        )
    )
    mock_client.search = _make_search_mock(results_by_collection)

    cfg = _make_cfg(max_parallel=3, top_k_return=5)
    provider = SearchContextProvider(cfg=cfg, search_client=mock_client)

    await provider.get_pre_context("query")
    task_output = _make_task_output(selected_collections=None)
    result = await provider.search_and_prepare(task_output, "query")

    assert result is None


# ---------------------------------------------------------------------------
# E2.6 — search returns wrong-shape dicts → results skipped via KeyError, returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_E2_6_wrong_shape_json_returns_empty_no_crash():
    """E2.6: search() returns dicts with wrong keys → SearchResult(**r) raises KeyError → [], no crash."""
    async def _search_bad_shape(collection: str, query: str, top_k: int) -> list[dict]:
        return [
            {"unexpected_key": "some value"},
            {"also_wrong": 42, "no_doc_id": True},
        ]

    mock_client = _make_mock_client(
        route_response=_make_route_response(
            pinned_names=[],
            routable_names=["col_bad"],
            decomposer_invoked=False,
        )
    )
    mock_client.search = AsyncMock(side_effect=_search_bad_shape)

    cfg = _make_cfg(max_parallel=3, top_k_return=5)
    provider = SearchContextProvider(cfg=cfg, search_client=mock_client)

    await provider.get_pre_context("query")
    task_output = _make_task_output(selected_collections=None)
    result = await provider.search_and_prepare(task_output, "query")

    # Wrong-shape dicts raise TypeError during SearchResult(**r) → collection returns [] → None
    assert result is None


# ---------------------------------------------------------------------------
# E2.6b — search() returns empty list from wrong-shape → no crash
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_E2_6b_invalid_json_in_text_block_returns_empty_no_crash():
    """E2.6b: search() returns [] (empty result from any failure) → no crash, returns None."""
    mock_client = _make_mock_client(
        route_response=_make_route_response(
            pinned_names=[],
            routable_names=["col_json_error"],
            decomposer_invoked=False,
        )
    )
    mock_client.search = _make_search_mock({})  # returns [] for all collections

    cfg = _make_cfg(max_parallel=3, top_k_return=5)
    provider = SearchContextProvider(cfg=cfg, search_client=mock_client)

    await provider.get_pre_context("query")
    task_output = _make_task_output(selected_collections=None)
    result = await provider.search_and_prepare(task_output, "query")

    assert result is None


# ---------------------------------------------------------------------------
# C2.1 — SearchConfig(enabled=False) → get_pre_context returns None, no HTTP call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_C2_1_disabled_config_get_pre_context_returns_none_no_http():
    """C2.1: SearchConfig(enabled=False) → get_pre_context() returns None without HTTP call."""
    mock_client = _make_mock_client(
        route_response=_make_route_response(
            pinned_names=["col_a"],
            routable_names=["col_a"],
            decomposer_invoked=False,
        )
    )

    cfg = _make_cfg(enabled=False)
    provider = SearchContextProvider(cfg=cfg, search_client=mock_client)

    result = await provider.get_pre_context("test query")

    assert result is None
    # route() must NOT have been called
    mock_client.route.assert_not_called()
    # _route_response must remain None (guard did not proceed)
    assert provider._route_response is None


# ---------------------------------------------------------------------------
# C2.2 — enabled=False → search_and_prepare returns None at its own guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_C2_2_disabled_config_search_and_prepare_returns_none():
    """C2.2: enabled=False → get_pre_context leaves _route_response=None → search_and_prepare returns None."""
    mock_client = _make_mock_client(
        route_response=_make_route_response(
            pinned_names=["col_a"],
            routable_names=["col_a"],
            decomposer_invoked=False,
        )
    )

    cfg = _make_cfg(enabled=False)
    provider = SearchContextProvider(cfg=cfg, search_client=mock_client)

    # get_pre_context with disabled cfg → _route_response stays None
    await provider.get_pre_context("test query")

    task_output = _make_task_output(selected_collections=None)
    result = await provider.search_and_prepare(task_output, "test query")

    # search_and_prepare returns None because _route_response is None
    assert result is None


# ---------------------------------------------------------------------------
# C2.3 — 20 results, top_k=3 → exactly 3 returned
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_C2_3_top_k_3_returns_exactly_3_results():
    """C2.3: 20 results across 2 collections, top_k_return=3 → exactly 3 merged chunks."""
    results_by_collection = {
        "col_a": [
            _search_result_dict(
                doc_id=f"a{i}", chunk_id=f"a{i}", text=f"chunk a{i}",
                score=1.0 - i * 0.05, source_path="/a.md"
            )
            for i in range(10)
        ],
        "col_b": [
            _search_result_dict(
                doc_id=f"b{i}", chunk_id=f"b{i}", text=f"chunk b{i}",
                score=1.0 - i * 0.05, source_path="/b.md"
            )
            for i in range(10)
        ],
    }
    mock_client = _make_mock_client(
        route_response=_make_route_response(
            pinned_names=[],
            routable_names=["col_a", "col_b"],
            decomposer_invoked=False,
        )
    )
    mock_client.search = _make_search_mock(results_by_collection)

    cfg = _make_cfg(max_parallel=3, top_k_return=3)
    provider = SearchContextProvider(cfg=cfg, search_client=mock_client)

    await provider.get_pre_context("query")
    task_output = _make_task_output(selected_collections=None)
    result = await provider.search_and_prepare(task_output, "query")

    assert result is not None
    rag_text, chunk_count, actual_searched = result
    assert chunk_count == 3


# ---------------------------------------------------------------------------
# C2.4 — max_parallel=1 → serialises searches; all pinned searched despite cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_C2_4_max_parallel_1_sequential_search():
    """C2.4: max_parallel=1 semaphore serialises pinned collections.

    Pinned collections bypass the tier-1 routable cap, so all 3 are added to
    to_search regardless of max_parallel_collections=1.  The semaphore(1) still
    allows only one in-flight request at a time, but all three complete.
    """
    call_order: list[str] = []

    async def _search_ordered(collection: str, query: str, top_k: int) -> list[dict]:
        call_order.append(collection)
        return [_search_result_dict(
            doc_id=f"{collection}-d", chunk_id=f"{collection}-c",
            text=f"text from {collection}", score=0.8, source_path=f"/{collection}.md"
        )]

    mock_client = _make_mock_client(
        route_response=_make_route_response(
            # Pinned collections bypass max_parallel_collections cap → all 3 are searched
            pinned_names=["pin_1", "pin_2", "pin_3"],
            routable_names=["col_1", "col_2", "col_3"],
            decomposer_invoked=False,
        )
    )
    mock_client.search = AsyncMock(side_effect=_search_ordered)

    cfg = _make_cfg(max_parallel=1, top_k_return=10)
    provider = SearchContextProvider(cfg=cfg, search_client=mock_client)

    await provider.get_pre_context("query")
    task_output = _make_task_output(selected_collections=None)
    result = await provider.search_and_prepare(task_output, "query")

    assert result is not None
    rag_text, chunk_count, actual_searched = result

    # All 3 pinned collections must have been searched (semaphore serialises, not blocks)
    assert "pin_1" in actual_searched
    assert "pin_2" in actual_searched
    assert "pin_3" in actual_searched

    # All 3 pinned collections were recorded by the mock
    assert "pin_1" in call_order
    assert "pin_2" in call_order
    assert "pin_3" in call_order

    # Tier-1 routable cap: routable_names[:max_parallel_collections=1] = ["col_1"] only
    assert "col_1" in actual_searched
    assert "col_2" not in actual_searched
    assert "col_3" not in actual_searched
