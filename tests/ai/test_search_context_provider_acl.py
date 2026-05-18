"""Tests for SearchContextProvider ACL filtering log behavior (FEAT-044 Task 5.2)."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from archon.ai.decomposer import TaskOutput
from archon.ai.search_client import SearchQueryResult
from archon_search.types import RouteResponse


def _make_search_config(
    *,
    enabled: bool = True,
    max_parallel_collections: int = 3,
    top_k_return: int = 5,
) -> MagicMock:
    cfg = MagicMock()
    cfg.enabled = enabled
    cfg.max_parallel_collections = max_parallel_collections
    cfg.top_k_return = top_k_return
    return cfg


def _make_task_output(selected_collections: list[str] | None = None) -> TaskOutput:
    t = TaskOutput(scope="small", prompt="q")
    t.selected_collections = selected_collections
    return t


def _make_provider(cfg=None, search_client=None):
    from archon.ai.search_context_provider import SearchContextProvider

    if cfg is None:
        cfg = _make_search_config()
    return SearchContextProvider(cfg=cfg, search_client=search_client or MagicMock())


def _route_response(
    *,
    pre_context: str | None = None,
    pinned_names: list[str] | None = None,
    routable_names: list[str] | None = None,
    decomposer_invoked: bool = False,
) -> RouteResponse:
    return RouteResponse(
        pre_context=pre_context,
        pinned_names=pinned_names or [],
        routable_names=routable_names or [],
        decomposer_invoked=decomposer_invoked,
    )


def _search_result_dict(text: str, score: float, collection: str = "col") -> dict:
    return {
        "doc_id": f"{collection}-doc",
        "chunk_id": f"{collection}-chunk",
        "text": text,
        "score": score,
        "source_path": f"/path/{collection}",
    }


@pytest.mark.asyncio
async def test_context_provider_logs_acl_filtered_at_debug(caplog) -> None:
    """When search returns acl_filtered=True, a DEBUG log is emitted for that collection."""
    mock_client = MagicMock()
    mock_client.route = AsyncMock(
        return_value=_route_response(routable_names=["col1"], decomposer_invoked=False)
    )
    mock_client.search = AsyncMock(
        return_value=SearchQueryResult(
            results=[_search_result_dict("text", 0.9, "col1")],
            acl_filtered=True,
        )
    )

    provider = _make_provider(search_client=mock_client)
    await provider.get_pre_context("query")
    task_output = _make_task_output(selected_collections=None)

    with caplog.at_level(logging.DEBUG, logger="archon"):
        await provider.search_and_prepare(task_output, "query")

    debug_records = [
        r for r in caplog.records
        if r.levelno == logging.DEBUG and "acl_filtered" in r.message
    ]
    assert debug_records, "Expected a DEBUG log about acl_filtered=True"
    assert "col1" in debug_records[0].message


@pytest.mark.asyncio
async def test_context_provider_acl_filtered_false_no_log(caplog) -> None:
    """When search returns acl_filtered=False, no acl_filtered DEBUG log is emitted."""
    mock_client = MagicMock()
    mock_client.route = AsyncMock(
        return_value=_route_response(routable_names=["col1"], decomposer_invoked=False)
    )
    mock_client.search = AsyncMock(
        return_value=SearchQueryResult(
            results=[_search_result_dict("text", 0.9, "col1")],
            acl_filtered=False,
        )
    )

    provider = _make_provider(search_client=mock_client)
    await provider.get_pre_context("query")
    task_output = _make_task_output(selected_collections=None)

    with caplog.at_level(logging.DEBUG, logger="archon"):
        await provider.search_and_prepare(task_output, "query")

    acl_debug_records = [
        r for r in caplog.records
        if r.levelno == logging.DEBUG and "acl_filtered" in r.message
    ]
    assert not acl_debug_records, f"Unexpected acl_filtered DEBUG log: {acl_debug_records}"


@pytest.mark.asyncio
async def test_context_provider_passes_results_through_unchanged(caplog) -> None:
    """Results from SearchQueryResult are returned correctly regardless of acl_filtered flag."""
    mock_client = MagicMock()
    mock_client.route = AsyncMock(
        return_value=_route_response(routable_names=["col1"], decomposer_invoked=False)
    )
    mock_client.search = AsyncMock(
        return_value=SearchQueryResult(
            results=[_search_result_dict("important text", 0.95, "col1")],
            acl_filtered=True,
        )
    )

    provider = _make_provider(search_client=mock_client)
    await provider.get_pre_context("query")
    task_output = _make_task_output(selected_collections=None)

    result = await provider.search_and_prepare(task_output, "query")

    assert result is not None
    rag_text, chunk_count, searched = result
    assert chunk_count == 1
    assert "col1" in searched
    assert "important text" in rag_text
