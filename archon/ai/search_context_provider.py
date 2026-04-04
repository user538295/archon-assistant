"""SearchContextProvider — multi-collection search retrieval orchestrator (FEAT-022 Task 3.1).

Standalone orchestrator called from Pipeline.send(). NOT a ContextProvider implementor.

Call chain in Pipeline.send():
1. pre_context = search_provider.get_pre_context(query)   # Phase A: routing
2. route_task(prompt, search_pre_context=pre_context)      # decomposer selects collections
3. search_and_prepare(task_output, query)                  # Phase B: search + merge
4. session.inject_context(search_text, ...)                # caller injects result
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from typing import TYPE_CHECKING, Any

import httpx

from archon.search._types import SearchResult
from archon.search.embedder import Embedder, make_embedder
from archon.search.router import MultiCollectionRouter
from archon.search.sync import path_to_collection_name

if TYPE_CHECKING:
    from archon.ai.decomposer import TaskOutput
    from archon.config.loader import SearchConfig

logger = logging.getLogger("archon")

_SEARCH_TIMEOUT = 10.0


async def _search_collection(
    search_url: str, collection: str, query: str, top_k: int
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
    async with httpx.AsyncClient(timeout=_SEARCH_TIMEOUT) as client:
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

    Creates ONE Embedder instance shared across MultiCollectionRouter instances.
    Call get_pre_context() before route_task(), then search_and_prepare() after.
    """

    def __init__(self, search_url: str, cfg: "SearchConfig") -> None:
        self._search_url = search_url
        self._cfg = cfg
        self._embedder: Embedder = make_embedder(
            cfg.embedding_model,
            providers=list(cfg.providers) if cfg.providers else None,
        )
        # State set by get_pre_context(), consumed by search_and_prepare()
        self._router: MultiCollectionRouter | None = None
        self._pinned_names: list[str] = []

    async def get_pre_context(self, query: str) -> str | None:
        """Phase A: resolve pinned names, apply tier logic, return <rag_collections> block.

        Side effects:
        - Sets self._router (new MultiCollectionRouter for this query)
        - Sets self._pinned_names (resolved and filtered)

        Returns the <rag_collections> block string, or None when decomposer should not be
        involved (Tier 1, slot exhaustion, no metadata, confidence gate failed).
        """
        router = MultiCollectionRouter(
            search_url=self._search_url,
            embedder=self._embedder,
            shortlist_size=self._cfg.routing_shortlist_size,
            confidence_threshold=self._cfg.routing_confidence_threshold,
            embedding_model=self._cfg.embedding_model,
        )
        self._router = router

        # Fetch metadata for pinned name resolution
        all_meta = await router.fetch_metadata()
        all_names = {m.name for m in all_meta}

        # Resolve pinned paths → names; silently skip those not in metadata
        resolved_pinned: list[str] = []
        for path in self._cfg.pinned_collections:
            name = path_to_collection_name(path)
            if name in all_names:
                resolved_pinned.append(name)
            else:
                logger.debug(
                    "get_pre_context: pinned path %r resolved to %r but not in metadata — skipping",
                    path,
                    name,
                )
        self._pinned_names = resolved_pinned

        # Compute available slots for decomposer (routable collections)
        available_slots = self._cfg.max_parallel_collections - len(resolved_pinned)

        return await router.get_pre_context(
            query,
            pinned_names=resolved_pinned,
            available_slots=available_slots,
        )

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
        if self._router is None:
            return None

        router = self._router
        cfg = self._cfg

        # Tier-aware remapping of selected_collections
        if router.decomposer_was_invoked and task_output.selected_collections is None:
            task_output_selected: list[str] | None = []
        else:
            task_output_selected = task_output.selected_collections

        # Determine routable collections to search
        if task_output_selected is None:
            # Tier 1: decomposer was NOT invoked; search all routable (capped) + all pinned
            routable_to_search = router.last_routable_names[: cfg.max_parallel_collections]
            to_search = self._pinned_names + routable_to_search
        elif task_output_selected == []:
            # Decomposer ran but selected nothing (or tag absent after decomposer invoked)
            # → search pinned only
            if not self._pinned_names:
                return None
            to_search = list(self._pinned_names)
        else:
            # Tier 2/3: filter selected against last_routable_names (discard hallucinated)
            routable_set = set(router.last_routable_names)
            valid_selected = [n for n in task_output_selected if n in routable_set]
            # Cap at max_parallel - len(pinned), minimum 0
            routable_cap = max(0, cfg.max_parallel_collections - len(self._pinned_names))
            capped = valid_selected[:routable_cap]
            to_search = self._pinned_names + capped

        if not to_search:
            return None

        # Parallel search with semaphore
        semaphore = asyncio.Semaphore(cfg.max_parallel_collections)

        async def _bounded_search(collection: str) -> list[SearchResult]:
            async with semaphore:
                return await _search_collection(
                    self._search_url, collection, query, cfg.top_k_return
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
