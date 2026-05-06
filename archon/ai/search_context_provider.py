"""SearchContextProvider — multi-collection search retrieval orchestrator (FEAT-038 Task 7.2).

HTTP-based implementation. Uses SearchClient.route() for phase A and FastMCP JSON-RPC for phase B.

Call chain in Pipeline.send():
1. pre_context = search_provider.get_pre_context(query)   # Phase A: route() → RouteResponse
2. route_task(prompt, search_pre_context=pre_context)      # decomposer selects collections
3. search_and_prepare(task_output, query)                  # Phase B: search + merge
4. session.inject_context(search_text, ...)                # caller injects result
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import time
from typing import TYPE_CHECKING, Any

import httpx

from dataclasses import dataclass


@dataclass
class SearchResult:
    doc_id: str
    chunk_id: str
    text: str
    score: float
    source_path: str

if TYPE_CHECKING:
    from archon.ai.decomposer import TaskOutput
    from archon.ai.search_client import RouteResponse, SearchClient
    from archon.config.loader import SearchConfig

logger = logging.getLogger("archon")

_SEARCH_TIMEOUT = 10.0


async def _search_collection(
    client: httpx.AsyncClient, search_url: str, collection: str, query: str, top_k: int
) -> list[SearchResult]:
    """Call the RAG server's search tool via JSON-RPC for one collection."""
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "search",
            "arguments": {"query": query, "collection": collection},
        },
        "id": 1,
    }
    response = await client.post(search_url, json=payload)
    response.raise_for_status()
    data: dict[str, Any] = response.json()

    if "error" in data:
        logger.debug("_search_collection: JSON-RPC error for %s: %s", collection, data["error"])
        return []

    content_blocks: list[dict[str, Any]] = data.get("result", {}).get("content", [])
    if not content_blocks:
        return []

    first_block = content_blocks[0]
    if first_block.get("type") != "text":
        return []

    try:
        raw_results: list[dict[str, Any]] = json.loads(first_block["text"])
    except (json.JSONDecodeError, KeyError):
        return []

    results = []
    for r in raw_results:
        if "error" in r:
            continue
        try:
            results.append(
                SearchResult(
                    doc_id=r["doc_id"],
                    chunk_id=r["chunk_id"],
                    text=r["text"],
                    score=float(r["score"]),
                    source_path=r["source_path"],
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    return results[:top_k]


def _normalize_and_merge(
    per_collection: dict[str, list[SearchResult]], top_k: int
) -> list[SearchResult]:
    """Normalize scores per-collection then merge and return top_k.

    For each collection: normalized = (score - min) / (max - min).
    Fallback to 0.5 when max == min (prevents single-result collections dominating).
    """
    merged: list[SearchResult] = []
    for results in per_collection.values():
        if not results:
            continue
        scores = [r.score for r in results]
        min_s = min(scores)
        max_s = max(scores)
        spread = max_s - min_s
        for r in results:
            if spread == 0.0:
                normalized = 0.5
            else:
                normalized = (r.score - min_s) / spread
            merged.append(dataclasses.replace(r, score=normalized))

    merged.sort(key=lambda r: r.score, reverse=True)
    return merged[:top_k]


def _format_results(results: list[SearchResult]) -> str:
    """Format merged search results as a text block for context injection."""
    if not results:
        return ""
    lines = ["[RAG context — retrieved document chunks:]"]
    for i, r in enumerate(results, 1):
        lines.append(f"\n[{i}] Source: {r.source_path}")
        lines.append(r.text)
    lines.append("\n[End RAG context]")
    return "\n".join(lines)


class SearchContextProvider:
    """Orchestrates multi-collection search retrieval for Pipeline.send().

    Phase A: get_pre_context() → calls search_client.route() → stores RouteResponse
    Phase B: search_and_prepare() → uses stored RouteResponse for tier logic + fan-out search
    """

    def __init__(
        self,
        search_url: str,
        cfg: "SearchConfig",
        search_client: "SearchClient | None" = None,
    ) -> None:
        self._search_url = search_url
        self._cfg = cfg
        if search_client is None:
            from archon.ai.search_client import get_search_client
            search_client = get_search_client()
        self._search_client = search_client
        # Shared HTTP client — avoids creating N connection pools during fan-out
        self._http = httpx.AsyncClient(timeout=_SEARCH_TIMEOUT)
        # State set by get_pre_context(), consumed by search_and_prepare()
        self._route_response: "RouteResponse | None" = None

    async def close(self) -> None:
        """Close the shared HTTP client."""
        await self._http.aclose()

    async def __aenter__(self) -> "SearchContextProvider":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def get_pre_context(self, query: str) -> str | None:
        """Phase A: call route() → store RouteResponse → return pre_context.

        Returns route_response.pre_context or None when routing unavailable.
        Logs timing at DEBUG level for benchmark measurement.
        """
        self._route_response = None
        if not self._cfg.enabled:
            return None

        t0 = time.monotonic()
        route_response = await self._search_client.route(query)
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.debug("get_pre_context: route() took %.1f ms", elapsed_ms)

        if route_response is None:
            return None

        self._route_response = route_response
        return route_response.pre_context

    async def search_and_prepare(
        self, task_output: "TaskOutput", query: str
    ) -> tuple[str, int, list[str]] | None:
        """Phase B: determine collections to search, run parallel search, merge results.

        Args:
            task_output: Result from route_task() with optional selected_collections.
            query: The original user query (for search).

        Returns:
            (rag_text, chunk_count, actual_searched_names) or None if nothing to inject.
        """
        if self._route_response is None:
            return None

        route_response = self._route_response
        cfg = self._cfg
        pinned_names: list[str] = list(route_response.pinned_names)
        routable_names: list[str] = list(route_response.routable_names)
        decomposer_invoked: bool = route_response.decomposer_invoked

        # Tier-aware remapping of selected_collections
        if decomposer_invoked and task_output.selected_collections is None:
            task_output_selected: list[str] | None = []
        else:
            task_output_selected = task_output.selected_collections

        # Determine routable collections to search
        if task_output_selected is None:
            # Tier 1: decomposer NOT invoked; search all routable (capped) + all pinned
            routable_to_search = routable_names[: cfg.max_parallel_collections]
            to_search = pinned_names + routable_to_search
        elif task_output_selected == []:
            # Decomposer ran but selected nothing → search pinned only
            if not pinned_names:
                return None
            to_search = list(pinned_names)
        else:
            # Tier 2/3: filter selected against routable_names (discard hallucinated)
            routable_set = set(routable_names)
            valid_selected = [n for n in task_output_selected if n in routable_set]
            # Cap at max_parallel - len(pinned), minimum 0
            routable_cap = max(0, cfg.max_parallel_collections - len(pinned_names))
            capped = valid_selected[:routable_cap]
            to_search = pinned_names + capped

        if not to_search:
            return None

        # Parallel search with semaphore
        semaphore = asyncio.Semaphore(cfg.max_parallel_collections)

        async def _bounded_search(collection: str) -> list[SearchResult]:
            async with semaphore:
                return await _search_collection(
                    self._http, self._search_url, collection, query, cfg.top_k_return
                )

        tasks = [_bounded_search(col) for col in to_search]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        per_collection: dict[str, list[SearchResult]] = {}
        actual_searched: list[str] = []
        for collection, result in zip(to_search, raw_results):
            if isinstance(result, BaseException):
                logger.debug(
                    "search_and_prepare: search failed for %r: %s", collection, result
                )
                continue
            per_collection[collection] = result
            actual_searched.append(collection)

        if not per_collection:
            return None

        merged = _normalize_and_merge(per_collection, cfg.top_k_return)
        if not merged:
            return None

        rag_text = _format_results(merged)
        return rag_text, len(merged), actual_searched
