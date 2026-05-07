"""Tests for SearchContextProvider — HTTP-based multi-collection search retrieval (FEAT-038 Task 7.2)."""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.decomposer import TaskOutput

from archon_search._types import SearchResult

try:
    from archon_search.types import RouteResponse
except ImportError:
    RouteResponse = None  # type: ignore[assignment,misc]


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


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
    enabled: bool = True,
    max_parallel: int = 3,
    pinned_collections: list[str] | None = None,
    top_k_return: int = 5,
    host: str = "localhost",
    port: int = 8282,
) -> MagicMock:
    cfg = MagicMock()
    cfg.enabled = enabled
    cfg.max_parallel_collections = max_parallel
    cfg.pinned_collections = pinned_collections if pinned_collections is not None else []
    cfg.top_k_return = top_k_return
    cfg.host = host
    cfg.port = port
    return cfg


def _make_task_output(
    selected_collections: list[str] | None,
    scope: str = "small",
    prompt: str = "do it",
) -> TaskOutput:
    to = TaskOutput(scope=scope, prompt=prompt)
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
    # Fallback mock if archon_search not installed
    rr = MagicMock()
    rr.pre_context = pre_context
    rr.pinned_names = pinned_names or []
    rr.routable_names = routable_names or []
    rr.decomposer_invoked = decomposer_invoked
    return rr


def _make_mock_client(route_response: Any = None) -> MagicMock:
    """Build a mock SearchClient."""
    client = MagicMock()
    client.route = AsyncMock(return_value=route_response)
    return client


# ──────────────────────────────────────────────────────────────────
# Import & basic instantiation
# ──────────────────────────────────────────────────────────────────


def test_search_context_provider_import() -> None:
    from archon.ai.search_context_provider import SearchContextProvider  # noqa: F401


def test_search_context_provider_instantiates() -> None:
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config()
    client = _make_mock_client()
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)
    assert provider is not None


# ──────────────────────────────────────────────────────────────────
# Task 7.2 — new tests (TDD first)
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_context_calls_route_then_fastmcp() -> None:
    """route() called first; FastMCP search called second for each collection."""
    from archon.ai.search_context_provider import SearchContextProvider

    route_resp = _make_route_response(
        pre_context="<rag_collections>col1</rag_collections>",
        pinned_names=[],
        routable_names=["col1"],
        decomposer_invoked=False,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config()
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    call_order: list[str] = []

    async def _track_route(query: str, **_: Any) -> Any:
        call_order.append("route")
        return route_resp

    client.route = AsyncMock(side_effect=_track_route)

    async def _track_search(client: Any, url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        call_order.append(f"search:{collection}")
        return [_make_search_result("text", 0.9, collection)]

    # Phase A
    pre = await provider.get_pre_context("query")
    assert pre is not None

    task_output = _make_task_output(selected_collections=None)
    with patch("archon.ai.search_context_provider._search_collection", side_effect=_track_search):
        await provider.search_and_prepare(task_output, "query")

    assert call_order[0] == "route"
    assert any(o.startswith("search:") for o in call_order)
    assert client.route.call_count == 1


@pytest.mark.asyncio
async def test_get_context_preserves_pinned_names_from_route_state() -> None:
    """pinned_names from RouteResponse are always prepended in phase B."""
    from archon.ai.search_context_provider import SearchContextProvider

    route_resp = _make_route_response(
        pre_context=None,
        pinned_names=["pinned1"],
        routable_names=["col1"],
        decomposer_invoked=False,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config()
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    await provider.get_pre_context("query")

    task_output = _make_task_output(selected_collections=None)
    searched: list[str] = []

    async def _mock_search(client: Any, url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        searched.append(collection)
        return [_make_search_result("text", 0.8, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        result = await provider.search_and_prepare(task_output, "query")

    assert "pinned1" in searched
    assert result is not None


@pytest.mark.asyncio
async def test_get_context_preserves_tier1_behavior_from_route_state() -> None:
    """decomposer_invoked=False → Tier 1: search all routable, no selected_collections filter."""
    from archon.ai.search_context_provider import SearchContextProvider

    route_resp = _make_route_response(
        pre_context=None,
        pinned_names=[],
        routable_names=["col1", "col2"],
        decomposer_invoked=False,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    await provider.get_pre_context("query")

    # selected_collections=None, decomposer NOT invoked → Tier 1
    task_output = _make_task_output(selected_collections=None)
    searched: list[str] = []

    async def _mock_search(client: Any, url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        searched.append(collection)
        return [_make_search_result("text", 0.8, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        result = await provider.search_and_prepare(task_output, "query")

    # Tier 1: all routable searched
    assert "col1" in searched
    assert "col2" in searched
    assert result is not None


@pytest.mark.asyncio
async def test_get_context_discards_hallucinated_collections_from_route_state() -> None:
    """Collection names not in routable_names are discarded (hallucinated)."""
    from archon.ai.search_context_provider import SearchContextProvider

    route_resp = _make_route_response(
        pre_context="<rag_collections>col1, hallucinated</rag_collections>",
        pinned_names=[],
        routable_names=["col1"],
        decomposer_invoked=True,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config()
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    await provider.get_pre_context("query")

    task_output = _make_task_output(selected_collections=["col1", "hallucinated"])
    searched: list[str] = []

    async def _mock_search(client: Any, url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        searched.append(collection)
        return [_make_search_result("text", 0.8, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        await provider.search_and_prepare(task_output, "query")

    assert "hallucinated" not in searched
    assert "col1" in searched


@pytest.mark.asyncio
async def test_get_context_respects_max_parallel_and_top_k_return() -> None:
    """max_parallel_collections and top_k_return are honored by the fan-out/merge logic."""
    from archon.ai.search_context_provider import SearchContextProvider

    route_resp = _make_route_response(
        pre_context="ctx",
        pinned_names=[],
        routable_names=["c1", "c2", "c3", "c4", "c5"],
        decomposer_invoked=True,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config(max_parallel=2, top_k_return=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    await provider.get_pre_context("query")

    # Decomposer selected 5 collections; cap = max_parallel=2
    task_output = _make_task_output(selected_collections=["c1", "c2", "c3", "c4", "c5"])
    searched: list[str] = []

    async def _mock_search(client: Any, url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        searched.append(collection)
        assert top_k == 3  # top_k_return respected
        return [_make_search_result(f"text-{collection}", 0.8, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        result = await provider.search_and_prepare(task_output, "query")

    # max_parallel_collections=2 caps selected routable
    assert len(searched) <= 2
    assert result is not None
    _, chunk_count, _ = result
    assert chunk_count <= 3  # top_k_return applied


@pytest.mark.asyncio
async def test_get_context_search_disabled_returns_empty() -> None:
    """cfg.search.enabled=False → empty context; no HTTP call made."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config(enabled=False)
    client = _make_mock_client()
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    pre = await provider.get_pre_context("query")
    assert pre is None

    # route() must NOT have been called
    client.route.assert_not_called()

    task_output = _make_task_output(selected_collections=None)
    result = await provider.search_and_prepare(task_output, "query")
    assert result is None


@pytest.mark.asyncio
async def test_get_context_route_returns_none_returns_empty() -> None:
    """route() returns None → get_pre_context returns None; search_and_prepare returns None."""
    from archon.ai.search_context_provider import SearchContextProvider

    client = _make_mock_client(route_response=None)
    cfg = _make_rag_config()
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    pre = await provider.get_pre_context("query")
    assert pre is None

    task_output = _make_task_output(selected_collections=None)
    result = await provider.search_and_prepare(task_output, "query")
    assert result is None


@pytest.mark.asyncio
async def test_get_context_empty_routable_names_returns_empty() -> None:
    """No routable collections → search_and_prepare returns None; FastMCP not called."""
    from archon.ai.search_context_provider import SearchContextProvider

    route_resp = _make_route_response(
        pre_context=None,
        pinned_names=[],
        routable_names=[],
        decomposer_invoked=False,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config()
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    await provider.get_pre_context("query")

    task_output = _make_task_output(selected_collections=None)

    with patch("archon.ai.search_context_provider._search_collection") as mock_search:
        result = await provider.search_and_prepare(task_output, "query")

    assert result is None
    mock_search.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# Existing behavior — updated to use SearchClient
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_pre_context_returns_pre_context_from_route() -> None:
    """get_pre_context returns route_response.pre_context."""
    from archon.ai.search_context_provider import SearchContextProvider

    route_resp = _make_route_response(
        pre_context="<rag_collections>col1</rag_collections>",
        pinned_names=[],
        routable_names=["col1"],
        decomposer_invoked=True,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config()
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    result = await provider.get_pre_context("query")
    assert result == "<rag_collections>col1</rag_collections>"


@pytest.mark.asyncio
async def test_tier1_skips_decomposer_searches_all_routable() -> None:
    """Tier 1 path: selected_collections=None and decomposer NOT invoked → search all routable."""
    from archon.ai.search_context_provider import SearchContextProvider

    route_resp = _make_route_response(
        pinned_names=[],
        routable_names=["col1", "col2"],
        decomposer_invoked=False,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    await provider.get_pre_context("query")

    task_output = _make_task_output(selected_collections=None)
    results_col1 = [_make_search_result("chunk from col1", 0.9, "col1")]
    results_col2 = [_make_search_result("chunk from col2", 0.7, "col2")]

    async def _mock_search(client: Any, url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        if collection == "col1":
            return results_col1
        return results_col2

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        result = await provider.search_and_prepare(task_output, "query")

    assert result is not None
    rag_text, chunk_count, searched_names = result
    assert "col1" in searched_names or "col2" in searched_names
    assert chunk_count > 0


@pytest.mark.asyncio
async def test_tier1_cap_applies_to_routable_not_total() -> None:
    """Tier 1: routable is capped at max_parallel; pinned always included in full."""
    from archon.ai.search_context_provider import SearchContextProvider

    route_resp = _make_route_response(
        pinned_names=["pinned1"],
        routable_names=["col1", "col2", "col3"],
        decomposer_invoked=False,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config(max_parallel=2)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    await provider.get_pre_context("query")

    task_output = _make_task_output(selected_collections=None)
    searched: list[str] = []

    async def _mock_search(client: Any, url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        searched.append(collection)
        return [_make_search_result("text", 0.8, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        result = await provider.search_and_prepare(task_output, "query")

    assert "pinned1" in searched
    routable_searched = [s for s in searched if s != "pinned1"]
    assert len(routable_searched) <= 2


@pytest.mark.asyncio
async def test_search_and_prepare_caps_at_3_collections() -> None:
    """Non-pinned selected collections are capped at max_parallel - len(pinned)."""
    from archon.ai.search_context_provider import SearchContextProvider

    route_resp = _make_route_response(
        pinned_names=[],
        routable_names=["col1", "col2", "col3", "col4", "col5"],
        decomposer_invoked=True,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    await provider.get_pre_context("query")

    task_output = _make_task_output(selected_collections=["col1", "col2", "col3", "col4"])
    searched: list[str] = []

    async def _mock_search(client: Any, url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        searched.append(collection)
        return [_make_search_result("text", 0.8, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        await provider.search_and_prepare(task_output, "query")

    assert len(searched) <= 3


@pytest.mark.asyncio
async def test_search_and_prepare_returns_none_when_no_route_state() -> None:
    """When get_pre_context was not called (no route state), returns None."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config()
    client = _make_mock_client()
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)
    task_output = _make_task_output(selected_collections=None)

    result = await provider.search_and_prepare(task_output, "query")
    assert result is None


@pytest.mark.asyncio
async def test_search_and_prepare_selected_empty_list_searches_pinned_only() -> None:
    """Empty selected_collections → search pinned only."""
    from archon.ai.search_context_provider import SearchContextProvider

    route_resp = _make_route_response(
        pinned_names=["pinned1"],
        routable_names=["col1", "col2"],
        decomposer_invoked=True,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    await provider.get_pre_context("query")

    task_output = _make_task_output(selected_collections=[])
    searched: list[str] = []

    async def _mock_search(client: Any, url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
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

    route_resp = _make_route_response(
        pinned_names=[],
        routable_names=["col1"],
        decomposer_invoked=True,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    await provider.get_pre_context("query")

    task_output = _make_task_output(selected_collections=[])

    with patch("archon.ai.search_context_provider._search_collection"):
        result = await provider.search_and_prepare(task_output, "query")

    assert result is None


@pytest.mark.asyncio
async def test_search_and_prepare_remaps_none_to_empty_list_when_decomposer_was_invoked() -> None:
    """When decomposer was invoked but selected_collections is None, remap to [] → pinned only."""
    from archon.ai.search_context_provider import SearchContextProvider

    route_resp = _make_route_response(
        pinned_names=["pinned1"],
        routable_names=["col1"],
        decomposer_invoked=True,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    await provider.get_pre_context("query")

    task_output = _make_task_output(selected_collections=None)
    searched: list[str] = []

    async def _mock_search(client: Any, url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        searched.append(collection)
        return [_make_search_result("text", 0.8, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        result = await provider.search_and_prepare(task_output, "query")

    assert searched == ["pinned1"]
    assert result is not None


@pytest.mark.asyncio
async def test_pinned_collections_bypass_confidence_gate() -> None:
    """Pinned collections are searched even when no routable collections are returned."""
    from archon.ai.search_context_provider import SearchContextProvider

    route_resp = _make_route_response(
        pinned_names=["pinned1"],
        routable_names=[],
        decomposer_invoked=False,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    await provider.get_pre_context("query")

    task_output = _make_task_output(selected_collections=None)
    searched: list[str] = []

    async def _mock_search(client: Any, url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        searched.append(collection)
        return [_make_search_result("text", 0.9, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        result = await provider.search_and_prepare(task_output, "query")

    assert "pinned1" in searched
    assert result is not None


@pytest.mark.asyncio
async def test_pinned_only_search_when_router_selects_zero() -> None:
    """Decomposer selects zero routable → only pinned collections are searched."""
    from archon.ai.search_context_provider import SearchContextProvider

    route_resp = _make_route_response(
        pinned_names=["pinned1"],
        routable_names=["col1", "col2"],
        decomposer_invoked=True,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    await provider.get_pre_context("query")

    task_output = _make_task_output(selected_collections=[])
    searched: list[str] = []

    async def _mock_search(client: Any, url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
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

    route_resp = _make_route_response(
        pinned_names=["pinned1", "pinned2"],
        routable_names=["col1"],
        decomposer_invoked=True,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    await provider.get_pre_context("query")

    task_output = _make_task_output(selected_collections=["col1"])
    searched: list[str] = []

    async def _mock_search(client: Any, url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
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

    route_resp = _make_route_response(
        pinned_names=["pinned1", "pinned2"],
        routable_names=["col1", "col2", "col3"],
        decomposer_invoked=True,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    await provider.get_pre_context("query")

    task_output = _make_task_output(selected_collections=["col1", "col2", "col3"])
    searched: list[str] = []

    async def _mock_search(client: Any, url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        searched.append(collection)
        return [_make_search_result("text", 0.8, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        await provider.search_and_prepare(task_output, "query")

    routable_searched = [s for s in searched if not s.startswith("pinned")]
    assert len(routable_searched) <= 1


@pytest.mark.asyncio
async def test_actual_searched_names_includes_pinned() -> None:
    """actual_searched_names in the result includes both pinned and router-selected names."""
    from archon.ai.search_context_provider import SearchContextProvider

    route_resp = _make_route_response(
        pinned_names=["pinned1"],
        routable_names=["col1"],
        decomposer_invoked=True,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    await provider.get_pre_context("query")

    task_output = _make_task_output(selected_collections=["col1"])

    async def _mock_search(client: Any, url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        return [_make_search_result("text", 0.8, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        result = await provider.search_and_prepare(task_output, "query")

    assert result is not None
    _, _, actual_searched_names = result
    assert "pinned1" in actual_searched_names
    assert "col1" in actual_searched_names


@pytest.mark.asyncio
async def test_pinned_exhausts_max_parallel_cap() -> None:
    """When pinned fills max_parallel slots, selected routable gets 0 slots."""
    from archon.ai.search_context_provider import SearchContextProvider

    route_resp = _make_route_response(
        pinned_names=["pinned1", "pinned2"],
        routable_names=["col1", "col2"],
        decomposer_invoked=True,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config(max_parallel=2)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    await provider.get_pre_context("query")

    task_output = _make_task_output(selected_collections=["col1", "col2"])
    searched: list[str] = []

    async def _mock_search(client: Any, url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        searched.append(collection)
        return [_make_search_result("text", 0.8, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        await provider.search_and_prepare(task_output, "query")

    routable_searched = [s for s in searched if not s.startswith("pinned")]
    assert len(routable_searched) == 0
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
# Hallucination filter
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rag_parsing_filters_hallucinated_names() -> None:
    """Names not in routable_names are discarded (hallucinated names)."""
    from archon.ai.search_context_provider import SearchContextProvider

    route_resp = _make_route_response(
        pinned_names=[],
        routable_names=["col1", "col2"],
        decomposer_invoked=True,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    await provider.get_pre_context("query")

    task_output = _make_task_output(selected_collections=["col1", "hallucinated"])
    searched: list[str] = []

    async def _mock_search(client: Any, url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
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

    route_resp = _make_route_response(
        pinned_names=[],
        routable_names=["col1"],
        decomposer_invoked=True,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    await provider.get_pre_context("query")

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

    route_resp = _make_route_response(
        pinned_names=[],
        routable_names=["col1"],
        decomposer_invoked=True,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    await provider.get_pre_context("query")

    task_output = _make_task_output(selected_collections=["col1"])

    async def _failing_search(client: Any, url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        raise ConnectionError("server down")

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_failing_search):
        result = await provider.search_and_prepare(task_output, "query")

    assert result is None


@pytest.mark.asyncio
async def test_rag_partial_search_failure_uses_remaining_results() -> None:
    """Partial search failure: successful collections are still merged."""
    from archon.ai.search_context_provider import SearchContextProvider

    route_resp = _make_route_response(
        pinned_names=[],
        routable_names=["col1", "col2"],
        decomposer_invoked=True,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    await provider.get_pre_context("query")

    task_output = _make_task_output(selected_collections=["col1", "col2"])

    async def _mixed_search(client: Any, url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
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

    route_resp = _make_route_response(
        pinned_names=[],
        routable_names=["col1", "col2"],
        decomposer_invoked=True,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    await provider.get_pre_context("query")

    task_output = _make_task_output(selected_collections=["col1", "col2"])

    async def _all_fail(client: Any, url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        raise RuntimeError("all down")

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_all_fail):
        result = await provider.search_and_prepare(task_output, "query")

    assert result is None


# ──────────────────────────────────────────────────────────────────
# Parallel search bounds
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rag_parallel_search_bounded() -> None:
    """With 5 collections and max_parallel=2, at most 2 concurrent searches run."""
    from archon.ai.search_context_provider import SearchContextProvider

    route_resp = _make_route_response(
        pinned_names=[],
        routable_names=["c1", "c2", "c3", "c4", "c5"],
        decomposer_invoked=True,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config(max_parallel=2)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    await provider.get_pre_context("query")

    task_output = _make_task_output(selected_collections=["c1", "c2"])

    concurrent_peak = 0
    current_concurrent = 0
    lock = asyncio.Lock()

    async def _bounded_search(client: Any, url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
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
# _search_collection() unit tests
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_collection_happy_path() -> None:
    """_search_collection returns SearchResult list on successful response."""
    from archon.ai.search_context_provider import _search_collection
    from unittest.mock import AsyncMock, MagicMock

    results_data = [
        {"doc_id": "d1", "chunk_id": "c1", "text": "hello", "score": 0.9, "source_path": "/f.md"}
    ]
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={
        "result": {"content": [{"type": "text", "text": json.dumps(results_data)}]}
    })

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=response)
    results = await _search_collection(mock_client, "http://localhost:9999", "col", "query", top_k=5)

    assert len(results) == 1
    assert results[0].text == "hello"
    assert results[0].score == 0.9


@pytest.mark.asyncio
async def test_search_collection_jsonrpc_error_returns_empty() -> None:
    """_search_collection returns [] on JSON-RPC error key."""
    from archon.ai.search_context_provider import _search_collection
    from unittest.mock import AsyncMock, MagicMock

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={"error": {"code": -32600, "message": "bad"}})

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=response)
    results = await _search_collection(mock_client, "http://localhost:9999", "col", "query", top_k=5)

    assert results == []


@pytest.mark.asyncio
async def test_search_collection_empty_content_returns_empty() -> None:
    """_search_collection returns [] when content blocks are empty."""
    from archon.ai.search_context_provider import _search_collection
    from unittest.mock import AsyncMock, MagicMock

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={"result": {"content": []}})

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=response)
    results = await _search_collection(mock_client, "http://localhost:9999", "col", "query", top_k=5)

    assert results == []


@pytest.mark.asyncio
async def test_search_collection_malformed_json_text_returns_empty() -> None:
    """_search_collection returns [] when text block contains invalid JSON."""
    from archon.ai.search_context_provider import _search_collection
    from unittest.mock import AsyncMock, MagicMock

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={
        "result": {"content": [{"type": "text", "text": "not json at all"}]}
    })

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=response)
    results = await _search_collection(mock_client, "http://localhost:9999", "col", "query", top_k=5)

    assert results == []


@pytest.mark.asyncio
async def test_search_collection_error_entries_skipped() -> None:
    """_search_collection skips individual result entries that have an 'error' key."""
    from archon.ai.search_context_provider import _search_collection
    from unittest.mock import AsyncMock, MagicMock

    results_data = [
        {"error": "not found"},
        {"doc_id": "d1", "chunk_id": "c1", "text": "good", "score": 0.8, "source_path": "/f.md"},
    ]
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={
        "result": {"content": [{"type": "text", "text": json.dumps(results_data)}]}
    })

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=response)
    results = await _search_collection(mock_client, "http://localhost:9999", "col", "query", top_k=5)

    assert len(results) == 1
    assert results[0].text == "good"


@pytest.mark.asyncio
async def test_search_collection_top_k_slices_results() -> None:
    """_search_collection respects the top_k limit."""
    from archon.ai.search_context_provider import _search_collection
    from unittest.mock import AsyncMock, MagicMock

    results_data = [
        {"doc_id": f"d{i}", "chunk_id": f"c{i}", "text": f"t{i}", "score": 0.5, "source_path": "/f.md"}
        for i in range(10)
    ]
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={
        "result": {"content": [{"type": "text", "text": json.dumps(results_data)}]}
    })

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=response)
    results = await _search_collection(mock_client, "http://localhost:9999", "col", "query", top_k=3)

    assert len(results) == 3


@pytest.mark.asyncio
async def test_search_collection_happy_path_asserts_jsonrpc_payload() -> None:
    """_search_collection sends the correct JSON-RPC payload."""
    from archon.ai.search_context_provider import _search_collection
    from unittest.mock import AsyncMock, MagicMock

    url = "http://localhost:8282/mcp"
    collection = "docs"
    query = "how to configure"
    top_k = 5

    results_data = [
        {"doc_id": "d1", "chunk_id": "c1", "text": "hello", "score": 0.9, "source_path": "/f.md"}
    ]
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={
        "result": {"content": [{"type": "text", "text": json.dumps(results_data)}]}
    })

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=response)
    await _search_collection(mock_client, url, collection, query, top_k)

    call_args = mock_client.post.call_args
    # post(url, json=payload) — keyword arg
    posted_payload = call_args[1].get("json") or (call_args[0][1] if len(call_args[0]) > 1 else call_args[1]["json"])
    assert posted_payload["jsonrpc"] == "2.0"
    assert posted_payload["method"] == "tools/call"
    assert posted_payload["params"]["name"] == "search"
    assert posted_payload["params"]["arguments"]["query"] == query
    assert posted_payload["params"]["arguments"]["collection"] == collection


@pytest.mark.asyncio
async def test_search_collection_http_error_propagates() -> None:
    """_search_collection propagates HTTPStatusError from raise_for_status."""
    from archon.ai.search_context_provider import _search_collection
    import httpx

    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "HTTP 500", request=MagicMock(), response=MagicMock()
        )
    )
    mock_client.post = AsyncMock(return_value=mock_resp)

    with pytest.raises(httpx.HTTPStatusError):
        await _search_collection(mock_client, "http://localhost:8282/mcp", "docs", "query", 5)


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


# ──────────────────────────────────────────────────────────────────
# get_search_client singleton injection
# ──────────────────────────────────────────────────────────────────


def test_search_context_provider_uses_singleton_when_no_client_passed() -> None:
    """When search_client is not passed, get_search_client() is used."""
    from archon.ai.search_context_provider import SearchContextProvider
    from archon.ai import search_client as sc_module

    mock_client = _make_mock_client()
    cfg = _make_rag_config()

    with patch.object(sc_module, "get_search_client", return_value=mock_client):
        provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    assert provider is not None


@pytest.mark.asyncio
async def test_per_collection_search_timeout_excluded_from_results() -> None:
    """Individual collection timeout (asyncio.TimeoutError) is excluded from results."""
    from archon.ai.search_context_provider import SearchContextProvider

    route_resp = _make_route_response(
        pinned_names=[],
        routable_names=["fast", "slow"],
        decomposer_invoked=True,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    await provider.get_pre_context("query")

    task_output = _make_task_output(selected_collections=["fast", "slow"])

    async def _mock_search(client: Any, url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        if collection == "slow":
            raise asyncio.TimeoutError()
        return [_make_search_result("fast result", 0.9, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        result = await provider.search_and_prepare(task_output, "query")

    assert result is not None
    rag_text, chunk_count, searched_names = result
    assert "fast result" in rag_text
    assert chunk_count == 1


# ──────────────────────────────────────────────────────────────────
# Suite 10 — SearchContextProvider Branches (A10.27–A10.39)
# ──────────────────────────────────────────────────────────────────


def test_a10_27_no_client_calls_get_search_client() -> None:
    """A10.27: When search_client=None, get_search_client() is called internally."""
    from archon.ai.search_context_provider import SearchContextProvider
    from archon.ai import search_client as sc_module

    mock_client = _make_mock_client()
    cfg = _make_rag_config()

    with patch.object(sc_module, "get_search_client", return_value=mock_client) as mock_get:
        provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg)

    mock_get.assert_called_once()
    assert provider._search_client is mock_client


@pytest.mark.asyncio
async def test_a10_28_context_manager_calls_aclose() -> None:
    """A10.28: Using SearchContextProvider as async context manager calls _http.aclose() on exit."""
    from archon.ai.search_context_provider import SearchContextProvider

    cfg = _make_rag_config()
    client = _make_mock_client()
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    with patch.object(provider._http, "aclose", new_callable=AsyncMock) as mock_close:
        async with provider:
            pass
        mock_close.assert_called_once()


@pytest.mark.asyncio
async def test_a10_29_successful_route_logs_elapsed_ms(caplog: Any) -> None:
    """A10.29: Successful route() logs elapsed ms at DEBUG level."""
    import logging
    from archon.ai.search_context_provider import SearchContextProvider

    route_resp = _make_route_response(
        pre_context="ctx",
        pinned_names=[],
        routable_names=["col1"],
        decomposer_invoked=False,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config()
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    with caplog.at_level(logging.DEBUG, logger="archon"):
        await provider.get_pre_context("my query")

    assert any(
        rec.levelno == logging.DEBUG and "ms" in rec.message
        for rec in caplog.records
        if "route" in rec.message.lower()
    )


@pytest.mark.asyncio
async def test_a10_30_decomposer_invoked_none_selected_searches_pinned_only() -> None:
    """A10.30: decomposer_invoked=True + selected_collections=None → treated as [], pinned only."""
    from archon.ai.search_context_provider import SearchContextProvider

    route_resp = _make_route_response(
        pinned_names=["pinned1"],
        routable_names=["col1", "col2"],
        decomposer_invoked=True,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    await provider.get_pre_context("query")

    # selected_collections=None but decomposer WAS invoked → remapped to [] → pinned only
    task_output = _make_task_output(selected_collections=None)
    searched: list[str] = []

    async def _mock_search(client: Any, url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        searched.append(collection)
        return [_make_search_result("text", 0.8, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        result = await provider.search_and_prepare(task_output, "query")

    assert searched == ["pinned1"]
    assert result is not None


@pytest.mark.asyncio
async def test_a10_31_five_pinned_max_parallel_3_all_searched() -> None:
    """A10.31: 5 pinned, max_parallel=3 → all 5 are searched (pinned count is not capped by max_parallel)."""
    from archon.ai.search_context_provider import SearchContextProvider

    route_resp = _make_route_response(
        pinned_names=["p1", "p2", "p3", "p4", "p5"],
        routable_names=[],
        decomposer_invoked=False,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    await provider.get_pre_context("query")

    task_output = _make_task_output(selected_collections=None)
    searched: list[str] = []

    async def _mock_search(client: Any, url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        searched.append(collection)
        return [_make_search_result("text", 0.8, collection)]

    with patch("archon.ai.search_context_provider._search_collection", side_effect=_mock_search):
        result = await provider.search_and_prepare(task_output, "query")

    # All 5 pinned must be searched — semaphore throttles concurrency, not total count
    assert set(searched) == {"p1", "p2", "p3", "p4", "p5"}
    assert result is not None


@pytest.mark.asyncio
async def test_a10_32_non_text_content_block_returns_empty() -> None:
    """A10.32: Non-text content block in response → _search_collection returns []."""
    from archon.ai.search_context_provider import _search_collection
    from unittest.mock import AsyncMock, MagicMock

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={
        "result": {"content": [{"type": "image", "data": "base64stuff"}]}
    })

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=response)
    results = await _search_collection(mock_client, "http://localhost:9999", "col", "query", top_k=5)

    assert results == []


def test_a10_33_top_k_truncates_merged_results() -> None:
    """A10.33: _normalize_and_merge truncates to top_k results."""
    from archon.ai.search_context_provider import _normalize_and_merge

    per_collection = {
        "col1": [_make_search_result(f"t{i}", float(i) / 10, "col1") for i in range(8)],
    }
    merged = _normalize_and_merge(per_collection, top_k=4)
    assert len(merged) == 4


def test_a10_34_over_fetch_returns_all_within_top_k() -> None:
    """A10.34: When fewer results than top_k exist, all are returned."""
    from archon.ai.search_context_provider import _normalize_and_merge

    per_collection = {
        "col1": [_make_search_result("a", 0.9, "col1"), _make_search_result("b", 0.7, "col1")],
    }
    merged = _normalize_and_merge(per_collection, top_k=10)
    assert len(merged) == 2


def test_a10_35_normalize_and_merge_empty_input_returns_empty() -> None:
    """A10.35: _normalize_and_merge with empty dict returns []."""
    from archon.ai.search_context_provider import _normalize_and_merge

    merged = _normalize_and_merge({}, top_k=5)
    assert merged == []


def test_a10_36_normalize_and_merge_collection_with_empty_list_skipped() -> None:
    """A10.36: Collections with empty result lists are skipped in merge."""
    from archon.ai.search_context_provider import _normalize_and_merge

    per_collection = {
        "empty_col": [],
        "good_col": [_make_search_result("good", 0.8, "good_col")],
    }
    merged = _normalize_and_merge(per_collection, top_k=5)
    assert len(merged) == 1
    assert merged[0].text == "good"


def test_a10_37_format_results_includes_source_path() -> None:
    """A10.37: _format_results output includes source path for each result."""
    from archon.ai.search_context_provider import _format_results

    results = [_make_search_result("chunk content", 0.9, "mycol")]
    output = _format_results(results)

    assert "Source:" in output
    assert "/path/mycol.md" in output
    assert "chunk content" in output


def test_a10_38_format_results_wraps_in_rag_context_markers() -> None:
    """A10.38: _format_results output is wrapped in [RAG context...] and [End RAG context]."""
    from archon.ai.search_context_provider import _format_results

    results = [
        _make_search_result("first chunk", 0.9),
        _make_search_result("second chunk", 0.7),
    ]
    output = _format_results(results)

    assert output.startswith("[RAG context")
    assert output.endswith("[End RAG context]")


@pytest.mark.asyncio
async def test_a10_39_all_tasks_raise_value_error_returns_none(caplog: Any) -> None:
    """A10.39: All search tasks raise ValueError → returns None; each failure logged at DEBUG."""
    import logging
    from archon.ai.search_context_provider import SearchContextProvider

    route_resp = _make_route_response(
        pinned_names=[],
        routable_names=["col1", "col2"],
        decomposer_invoked=True,
    )
    client = _make_mock_client(route_response=route_resp)
    cfg = _make_rag_config(max_parallel=3)
    provider = SearchContextProvider(search_url="http://localhost:8282/mcp", cfg=cfg, search_client=client)

    await provider.get_pre_context("query")

    task_output = _make_task_output(selected_collections=["col1", "col2"])

    async def _raise_value_error(client: Any, url: str, collection: str, query: str, top_k: int) -> list[SearchResult]:
        raise ValueError(f"bad collection: {collection}")

    with caplog.at_level(logging.DEBUG, logger="archon"):
        with patch("archon.ai.search_context_provider._search_collection", side_effect=_raise_value_error):
            result = await provider.search_and_prepare(task_output, "query")

    assert result is None
    # Each failing collection should produce a DEBUG log entry
    debug_messages = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
    col1_entries = [m for m in debug_messages if "col1" in m]
    col2_entries = [m for m in debug_messages if "col2" in m]
    assert len(col1_entries) >= 1, f"No DEBUG log mentioning 'col1'. All debug: {debug_messages}"
    assert len(col2_entries) >= 1, f"No DEBUG log mentioning 'col2'. All debug: {debug_messages}"
