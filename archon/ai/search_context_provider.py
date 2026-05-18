"""SearchContextProvider — multi-collection search retrieval orchestrator (FEAT-038 Task 7.2).

Uses SearchClient for all search calls (Phase A: route(), Phase B: search()).

Call chain in Pipeline.send():
1. pre_context = search_provider.get_pre_context(query)   # Phase A: route() → RouteResponse
2. route_task(prompt, search_pre_context=pre_context)      # decomposer selects collections
3. search_and_prepare(task_output, query)                  # Phase B: search + merge
4. session.inject_context(search_text, ...)                # caller injects result
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from typing import TYPE_CHECKING, Any

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
        cfg: "SearchConfig",
        search_client: "SearchClient | None" = None,
    ) -> None:
        self._cfg = cfg
        if search_client is None:
            from archon.ai.search_client import get_search_client
            search_client = get_search_client()
        self._search_client = search_client
        # State set by get_pre_context(), consumed by search_and_prepare()
        self._route_response: "RouteResponse | None" = None

    async def close(self) -> None:
        """No-op — search calls go through SearchClient which manages its own connection."""
        pass

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
                result = await self._search_client.search(collection, query, cfg.top_k_return)
                if result.acl_filtered:
                    logger.debug("search: acl_filtered=True for collection %s", collection)
                return [SearchResult(**r) for r in result.results]

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
