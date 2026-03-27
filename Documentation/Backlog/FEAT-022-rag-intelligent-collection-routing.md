# FEAT-022 — RAG Intelligent Collection Routing

**Purpose**: Replace the single-collection RAG search with decomposer-driven automatic multi-collection routing — the decomposer selects which collections to query based on the user's request, searches them in resource-bounded parallel batches, and injects the merged results as context, all without any user instruction.
**Audience**: Archon users with multiple indexed collections who want Claude to automatically draw on the right knowledge sources without being told which to use.
**Status**: To Do
**Depends on**: FEAT-021 (multi-collection infrastructure, `RagCollectionSync`, `archon rag collection` CLI)

---

## Background

After FEAT-021, Archon can manage multiple LanceDB collections declared in `config.toml`. However, the RAG server still searches a single `default_collection` per request. Every query hits the same collection regardless of relevance.

The desired behaviour: the decomposer receives a ranked shortlist of available collections and their auto-generated descriptions, selects the 2–3 most relevant ones, searches them in parallel via the RAG pipeline, merges the results, and injects the top-K chunks — all before the main LLM query executes. No user instruction is needed; the right collections are chosen automatically based on the query.

The main resource concern is scale: with 20+ collections, naive parallel search across all of them would saturate the RAG server and local disk I/O. The solution is a two-stage approach: fast embedding pre-ranking (milliseconds, no LLM) narrows candidates to a small shortlist; collection selection reasoning is embedded in the existing `route_task()` router call (no separate subprocess per query); and actual search is batched to at most `rag.max_parallel_collections` (default 3) concurrent requests.

---

## Goal

1. Every decomposer query automatically identifies and searches the 2–3 most relevant collections.
2. Resource usage is bounded: at most `max_parallel_collections` concurrent RAG searches at any time; a confidence gate skips RAG entirely when no collection is a strong match.
3. Each collection has an auto-generated description (produced by Haiku during ingest) that is used for selection reasoning.
4. Each collection stores an embedding centroid used for the fast pre-ranking step before the selection reasoning is involved.
5. The feature degrades gracefully: if RAG is disabled or no collection matches, the session continues without RAG context.

---

## Scope

### In Scope
- `CollectionMeta` dataclass — name, path, description, centroid, doc_count, chunk_count, embedding_model, described_at_doc_count
- MCP tool `list_collections_meta(include_centroids: bool = False)` — returns all `CollectionMeta` entries; centroids included only when `include_centroids=True`
- Centroid computation during ingest: mean of all chunk embeddings, stored in collection metadata
- Haiku-generated collection description: 2–3 sentence summary from a sample of chunks, generated async during ingest (one-shot Haiku call via `ClaudeSDKClient`; acceptable cost because ingest is infrequent)
- Description re-generation trigger: doc_count changes by ≥20% since last generation (measured against `described_at_doc_count`)
- Centroid incremental update: running mean on each ingest (new documents only); full recompute after any delete
- `MultiCollectionRouter` — runs in-process using a public `embed_query()` method on `RagPipeline` that delegates to the internal `Embedder`; pre-ranks collections by cosine similarity between query embedding and collection centroids, returns shortlist
- `archon/ai/rag_context_provider.py` (new): `RagContextProvider` class — a separate collaborator (NOT implementing `ContextProvider`) with two methods: `async def prepare_pre_route(query: str) -> tuple[str | None, list[CollectionMeta]]` and `async def prepare_post_route(query: str, candidates: list[CollectionMeta], selected_collections: list[str]) -> str | None`; `prepare_pre_route` runs `MultiCollectionRouter.select()` and returns the `<rag_collections>` context block (or `None` for Tier 1) alongside the candidate list; `Pipeline.send()` passes the block directly to `Decomposer.route_task(rag_context=block)` (not via `inject_context()`); after `route_task()` returns, `prepare_post_route` searches selected collections in parallel, merges results, and returns the merged RAG text; `Pipeline.send()` calls `decomposer.inject_context(rag_text, injection_type="rag_retrieval")` with the result (consumed by `answer()`)
- `rag.max_parallel_collections` config key (default `3`)
- `rag.routing_confidence_threshold` config key (default `0.30`): if best centroid similarity < threshold, skip RAG
- `rag.routing_shortlist_size` config key (default `8`): max collections passed to `route_task()` context block
- `archon rag collection info <name>` CLI command — shows description, centroid status, doc/chunk counts, embedding model
- Ingest progress feedback: file count, chunk count, elapsed time printed to stdout during `archon rag collection add` and `archon rag sync`
- `archon rag collection reindex <name>` CLI command — re-embeds all documents, regenerates centroid and description
- Dry-run support for `archon rag collection remove --dry-run` (extends FEAT-021's `--force` flag with a preview mode)
- `archon doctor` collection health checks: stale index (>7 days), embedding model mismatch, zero-document collections, missing centroid
- RAG injection visibility: `ContextInjectedEvent` with `injection_type="rag_retrieval"` (reuses FEAT-018 pipeline); in verbose/debug mode Telegram shows `🔍 RAG: N chunks from <collection>`; silent in quiet/normal

### Out of Scope
- Cross-collection query result reranking with a cross-encoder model (future — reranker already exists per-collection)
- Changing how results are ranked within a single collection (existing `bge-reranker-v2-m3` unchanged)
- Exposing collection selection to the user (user never needs to specify a collection)
- Per-collection embedding model configuration
- Automatic re-index on file-system change (file-watch)

---

## Acceptance Criteria

### Collection metadata
- [ ] `CollectionMeta` dataclass: `name`, `description`, `centroid: list[float] | None`, `doc_count`, `chunk_count`, `embedding_model`, `last_indexed`, `last_described`, `described_at_doc_count: int | None`
- [ ] During ingest, the chunk centroid (mean of all chunk embeddings as `list[float]`) is computed and stored in LanceDB collection metadata
- [ ] Centroid updates incrementally (running mean) for pure additions; after any delete operation, centroid is recomputed from scratch (full mean of remaining embeddings)
- [ ] During ingest, a Haiku call samples up to 20 representative chunks and generates a 2–3 sentence description stored alongside the centroid (one-shot `ClaudeSDKClient` call; acceptable at ingest time because ingest is infrequent)
- [ ] Description regenerates automatically when `abs(current_doc_count - described_at_doc_count) / described_at_doc_count >= 0.20`; `described_at_doc_count` is updated together with `last_described` when a description is (re-)generated; if `described_at_doc_count` is `None` or `0`, description is always regenerated (guard against division-by-zero)
- [ ] If Haiku call fails during ingest, description is `None`; ingest completes without error

### RAG server MCP tools
- [ ] MCP tool `list_collections_meta(include_centroids: bool = False)` returns `list[CollectionMeta]`; centroids included only when `include_centroids=True`
- [ ] `list_collections_meta` returns an empty list when no collections exist
- [ ] `list_collections_meta` returns an error dict on unknown or inaccessible store
- [ ] `archon_collection_meta` internal table is excluded from `list_collections()` output and from `list_collections_meta` results (it is an internal table, not a user collection)
- [ ] Existing MCP search and ingest tools unchanged

### Multi-collection routing
- [ ] `MultiCollectionRouter` runs in-process using `rag_pipeline.embed_query(text)` (a public method on `RagPipeline` that delegates to the internal `_embedder.embed_one(text)`); fetches collection metadata via `rag_pipeline.store.list_collection_meta()` directly (in-process, no MCP/HTTP call) once per instance and caches it
- [ ] `MultiCollectionRouter.rank(query_embedding) -> list[CollectionMeta]`: returns collections sorted descending by cosine similarity between query embedding and centroid; collections with `centroid=None` are placed last
- [ ] Three-tier shortlist logic:
  - If `total_collections ≤ 3`: skip both centroid pre-ranking AND `route_task()` collection selection; `RagContextProvider` searches all collections directly, injects results into the main session only (no `<rag_collections>` block injected into router). **The confidence gate does NOT apply for this tier** — with very few collections, the cost of searching all is negligible and centroid-based filtering would be unreliable (centroids may not yet be computed for new collections).
  - If `3 < total_collections ≤ rag.routing_shortlist_size`: skip centroid pre-ranking; pass all collections to `route_task()` via a `<rag_collections>` context block; after `route_task()` returns, read `selected_collections` from `TaskOutput` to determine which to search
  - If `total_collections > rag.routing_shortlist_size`: centroid pre-rank to get shortlist (confidence gate applies here); pass shortlist to `route_task()` via `<rag_collections>` context block; after `route_task()` returns, read `selected_collections` from `TaskOutput`
- [ ] For Tiers 2 and 3, if `route_task()` returns `selected_collections = []`, RAG is skipped (no injection into main session)

### Decomposer integration
- [ ] `RagContextProvider` (in `archon/ai/rag_context_provider.py`) is a standalone class with two methods: `async def prepare_pre_route(query: str) -> tuple[str | None, list[CollectionMeta]]` and `async def prepare_post_route(query: str, candidates: list[CollectionMeta], selected_collections: list[str]) -> str | None`; it does NOT implement the `ContextProvider` protocol (which has `get_recent_context()` / `get_context_files()` / `startup_context_prompt()` — static session-startup methods); the `context_provider.py` Protocol is NOT modified
- [ ] `RagContextProvider` is injected into `Pipeline.__init__()` as an optional dependency: `rag_context_provider: RagContextProvider | None = None`
- [ ] `RagContextProvider.__init__()` receives a `RagPipeline` instance as dependency; all collection searches are done by calling `await rag_pipeline.search(query, collection_name)` directly (in-process Python import, no MCP/HTTP); `MultiCollectionRouter.fetch_metadata()` calls `await rag_pipeline.store.list_collection_meta()` directly
- [ ] `RagContextProvider` and `MultiCollectionRouter` are only instantiated when `cfg.rag.enabled = True`; they hold a reference to the shared `RagPipeline` instance
- [ ] For Tiers 2 and 3 (> 3 collections): `RagContextProvider.prepare_pre_route()` returns a `<rag_collections>` context block (alongside the candidate list). `Pipeline.send()` passes this block directly to `Decomposer.route_task(rag_context=block)` as a parameter — it is appended to the instruction string inside `route_task()` before the routing prompt (NOT injected via `inject_context()`). This ensures the block is visible to the router session. The block lists the shortlisted collections with descriptions and instructs `route_task()` to include a `selected_collections` JSON array in its response.
- [ ] `route_task()` response is extended to include an optional `selected_collections: list[str]` field in its JSON output (alongside `scope`, `summary`, `prompt`/`agents`); the existing parser reads this field if present; on parse failure or missing field, `selected_collections` defaults to `[]`
- [ ] `TaskOutput` dataclass gains `selected_collections: list[str] = field(default_factory=list)` — populated from the `route_task()` JSON response
- [ ] After `route_task()` returns, `Pipeline.send()` reads `task_output.selected_collections` and calls `rag_text = await self._rag_context_provider.prepare_post_route(query, candidates, task_output.selected_collections)` if `_rag_context_provider` is set; if `rag_text is not None`, calls `self._decomposer.inject_context(rag_text, injection_type="rag_retrieval")` (consumed during the subsequent `answer()` call)
- [ ] `RagContextProvider.prepare_pre_route(query)` returns `tuple[str | None, list[CollectionMeta]]`: the first element is the `<rag_collections>` block to pass to `route_task()` (or `None` for Tier 1), the second element is the candidate list to pass to `prepare_post_route()`. It has NO side effects — no `inject_context()` calls.
- [ ] `RagContextProvider.prepare_post_route(query, candidates, selected_collections)` returns the merged RAG text as `str`, or `None` if RAG was skipped. `Pipeline.send()` calls `self._decomposer.inject_context(rag_text, injection_type="rag_retrieval")` with any non-None result.
- [ ] Decomposer context block format for collection selection (passed as `rag_context` to `route_task()`):
  ```
  <rag_collections>
  Available collections (select 1–3 most relevant for this query):
  - sessions: Daily conversation history and session logs
  - workspace: Current project files and documentation
  - contracts: Legal agreements and employment documents
  </rag_collections>
  ```
- [ ] `route_task()` router prompt is updated to: when a `<rag_collections>` block is present in the instruction (appended by the caller), include `"selected_collections": ["name1", "name2"]` in the JSON response (alongside `scope`/`summary`/`prompt`); when no `<rag_collections>` block is present, omit the field (defaults to `[]`)
- [ ] Selected collections are searched in parallel using `asyncio.Semaphore(max_parallel_collections)` to bound concurrency; all coroutines are submitted together but at most `max_parallel_collections` execute concurrently
- [ ] Results from each collection are normalised with min-max normalisation: if `max_score == min_score` within a collection (single-result collections and all-equal-score collections), all normalised scores for that collection are set to `1.0`; otherwise `score_norm = (score - min_score) / (max_score - min_score)` is used; results from all collections are merged and sorted by normalised score descending
- [ ] Top `rag.top_k_return` merged results are assembled into `rag_text`; `Pipeline.send()` calls `self._decomposer.inject_context(rag_text, injection_type="rag_retrieval")` with the result from `RagContextProvider.prepare_post_route()`; the injected context is consumed (prepended to the prompt) at the start of the next main-session `send()` call, which occurs during `Decomposer.answer()`
- [ ] If `selected_collections` is empty (or all have low confidence via the Tier 3 confidence gate), nothing is injected into the main session
- [ ] On the direct-chat path (intent == "chat" above confidence threshold): `Pipeline.send()` still calls `prepare_pre_route(query)` to obtain `(_, candidates)` — the returned block is ignored (no router call on this path); then calls `prepare_post_route(query, candidates, selected_collections=[])` so Tier 1 collections are still searched; this ensures chat queries also benefit from RAG when ≤ 3 collections exist
- [ ] When `route_task()` returns a fallback `TaskOutput` (`is_fallback=True`), `Pipeline.send()` applies Tier 1 behaviour: if there are ≤ 3 candidates (from `prepare_pre_route`), search all of them; otherwise skip RAG entirely (to avoid overwhelming a system that is already struggling with timeouts). This is documented as an accepted degradation.
- [ ] If RAG pipeline is unavailable, routing is skipped silently with a debug-level log entry; session continues

### CLI additions
- [ ] `archon rag collection info <name>` prints: description, centroid status (present/missing), doc count, chunk count, embedding model, last indexed date, last described date
- [ ] `archon rag collection reindex <name>` re-ingests all documents, recomputes centroid, regenerates Haiku description; prints progress (files, chunks, elapsed)
- [ ] `archon rag collection add <path>` prints progress during ingest: `Indexing: N/M files (K chunks)...`
- [ ] `archon rag sync` prints per-collection progress during ingest
- [ ] `archon rag collection remove <path> --dry-run` prints what would be removed without executing

### Doctor
- [ ] `archon doctor` warns for collections not re-indexed in >7 days: `⚠ Collection '<name>' last indexed N days ago`
- [ ] `archon doctor` warns for collections whose `embedding_model` differs from `cfg.rag.embedding_model`: `⚠ Collection '<name>' indexed with '<old_model>', current model is '<new_model>' — reindex required`
- [ ] `archon doctor` warns for collections with `doc_count == 0`: `⚠ Collection '<name>' is empty`
- [ ] `archon doctor` warns for collections with `centroid=None`: `⚠ Collection '<name>' has no centroid — routing disabled for this collection`

### Tests
- [ ] All existing tests pass
- [ ] New tests achieve ≥85% coverage for all new and modified modules
- [ ] `MultiCollectionRouter.rank()` tested with mocked centroid data for: normal ranking, confidence gate skip, `None` centroid handling, shortlist size cap
- [ ] `test_fetch_metadata_cached_across_calls` — call `select()` twice, assert store method called only once
- [ ] `test_medium_collection_set_passes_all_to_route_task_without_preranking` — 3 < N ≤ shortlist_size: assert centroid pre-ranking skipped, all collections included in the returned `<rag_collections>` block (passed to `route_task()` by the caller)
- [ ] `test_embed_query_delegates_to_embedder` — assert `RagPipeline.embed_query(text)` returns same result as `_embedder.embed_one(text)`
- [ ] Semaphore concurrency test: assert that `asyncio.Semaphore(max_parallel_collections)` limits concurrent search coroutines (not just that they are submitted together)
- [ ] Pipeline integration: `test_pipeline_calls_inject_context_with_rag_text` — assert `Pipeline.send()` calls `decomposer.inject_context(rag_text, injection_type="rag_retrieval")` with the non-None return value from `RagContextProvider.prepare_post_route()`
- [ ] `test_pipeline_skips_inject_context_when_prepare_returns_none` — assert `inject_context` is NOT called when `prepare_post_route()` returns `None`
- [ ] MCP tool tests: `list_collections_meta` (with and without centroids, empty store, error case)
- [ ] Centroid computation tested: mean of known embeddings, incremental update for additions, full recompute after delete (`test_ingest_recomputes_centroid_after_delete`), regeneration trigger
- [ ] `test_ensure_meta_table_created_on_connect` — assert `archon_collection_meta` table exists after `RagStore.connect()`
- [ ] Score normalisation tested: `test_merge_results_normalizes_scores` covering single-result collection (normalised score = 1.0), empty collection result (no results contributed), all-equal-score collection (all normalised scores = 1.0 because `max_score == min_score`), and normal multi-result collection (scores spread across [0.0, 1.0])
- [ ] Parse tests: `test_parse_collection_names_from_json`, `test_parse_collection_names_fallback_on_invalid`, `test_parse_collection_names_ignores_unknown_names`
- [ ] `test_list_collections_excludes_meta_table` — assert `archon_collection_meta` does not appear in `list_collections()` output
- [ ] Doctor tests: assert each warning condition triggers the correct message
- [ ] `test_small_collection_set_skips_rag_collections_block` — for ≤ 3 collections, assert `prepare_pre_route()` returns `(None, candidates)` (no block) and `route_task()` is called with `rag_context=None`
- [ ] `test_task_output_selected_collections_parsed` — assert `TaskOutput.selected_collections` is populated from the `route_task()` JSON response when present

---

## What Does NOT Change

- `RagPipeline.search()` — still takes a single `collection` parameter; multi-collection orchestration is above this layer
- MCP search tool — unchanged; `RagContextProvider` calls `rag_pipeline.search()` directly (in-process) once per selected collection
- `BAAI/bge-reranker-v2-m3` reranker — still used per-collection; cross-collection merge uses score normalisation only
- `RagCollectionSync` — unchanged; FEAT-022 only adds metadata fields computed during ingest
- Classifier — unmodified; collection routing is entirely above the classifier
- `ContextProvider` protocol in `context_provider.py` — NOT modified; `RagContextProvider` is a separate, unrelated class
- `ContextReminder` injection path — unchanged; RAG injection uses the existing `inject_context()` queue mechanism and reuses `ContextInjectedEvent` from FEAT-018

---

## Known Limitations / Accepted Trade-offs

- Centroid pre-ranking degrades for heterogeneous collections (mixed-topic content averages to a noisy centroid). Mitigated by the confidence gate: low-confidence centroids are deprioritised but collections with `centroid=None` are always included in the shortlist as fallback candidates.
- For ≤ 3 collections (Tier 1), the confidence gate does NOT apply — all collections are always searched. This is an intentional design choice: with very few collections, the cost of searching all is negligible and skipping RAG entirely based on centroid similarity would be unreliable (centroids may not yet be computed for new collections). The confidence gate applies only for Tiers 2 and 3, after `route_task()` returns `selected_collections`.
- Collection selection reasoning is embedded in `route_task()` via the `rag_context` parameter — the `<rag_collections>` block is appended directly to the instruction string inside `route_task()`. This avoids both a per-query subprocess spawn and the session injection race (injecting via `inject_context()` would target the wrong session). The block is present only when collections are available and is ignored on parse failure.
- When `route_task()` returns a fallback `TaskOutput` (`is_fallback=True`, e.g. router timeout or session init failure), `Pipeline.send()` applies Tier 1 degradation: if there are ≤ 3 candidates from `prepare_pre_route`, it searches all of them; otherwise RAG is skipped entirely. This avoids overwhelming a system that is already under stress. Accepted degradation.
- All-equal-score results within a collection (including single-result collections) receive a normalised score of 1.0. This is intentional: min-max normalisation is undefined when `max == min`; setting 1.0 ensures these results are not silently discarded from the merge.
- Cross-collection score normalisation is approximate (per-collection min/max normalisation). A cross-encoder reranker would be more accurate but is deferred (FEAT-020 scope).
- Description generation at ingest time costs one Haiku call per collection (one-shot `ClaudeSDKClient`). For a fresh install with 2 default collections, this is ~2 API calls. Acceptable because ingest is infrequent.
- `MultiCollectionRouter` caches collection metadata per instance. If a collection is added mid-session, it will not appear until the next session. Accepted: collections change rarely relative to session lifetime.
- Embedding model change invalidates all existing centroids. `archon doctor` will warn; `archon rag collection reindex --all` is required. A future migration command is out of scope here.
- `RagContextProvider` and `MultiCollectionRouter` are in-process components — they require the RAG pipeline to be available in the same process. This is true for the main Archon daemon but not for standalone CLI commands (which use MCP or direct store access instead).

---

## Architecture

### New components

**`archon/rag/collection_meta.py`** (new file)
```python
@dataclass
class CollectionMeta:
    name: str
    description: str | None
    centroid: list[float] | None    # omitted when list_collections_meta(include_centroids=False)
    doc_count: int
    chunk_count: int
    embedding_model: str
    last_indexed: datetime | None
    last_described: datetime | None
    described_at_doc_count: int | None  # doc_count at the time description was last generated
```

**`archon/rag/router.py`** (new file)
```python
class MultiCollectionRouter:
    def __init__(self, rag_pipeline: RagPipeline, shortlist_size: int, confidence_threshold: float) -> None:
        # Uses rag_pipeline.embed_query() — no separate model load
        ...

    async def fetch_metadata(self) -> list[CollectionMeta]:
        """Fetch full metadata (with centroids) via rag_pipeline.store.list_collection_meta() directly. Cached per instance."""

    def rank(self, query_embedding: list[float], collections: list[CollectionMeta]) -> list[CollectionMeta]:
        """Return shortlist sorted by cosine similarity. Returns [] if max_sim < threshold."""

    async def select(self, query: str) -> list[CollectionMeta]:
        """Embed query via rag_pipeline.embed_query(), apply three-tier shortlist logic, return shortlist.
        Entry point for RagContextProvider."""
```

**`archon/rag/description_generator.py`** (new file)
```python
async def generate_description(chunks: list[str], collection_name: str) -> str | None:
    """Sample up to 20 chunks, call Haiku via ClaudeSDKClient, return 2-3 sentence description.
    Called at ingest time only — acceptable cost because ingest is infrequent."""
```

**`archon/ai/rag_context_provider.py`** (new file)
```python
class RagContextProvider:
    """Standalone collaborator that prepares RAG context around route_task().

    NOT an implementation of the ContextProvider protocol. Injected into Pipeline
    as an optional dependency: rag_context_provider: RagContextProvider | None = None.

    Two-phase operation:
      Phase 1 (before route_task): return <rag_collections> block + candidate list (no side effects).
        - Tier 1 (≤3 collections): block is None; returns (None, all_candidates).
        - Tiers 2/3: block is the formatted <rag_collections> string; returns (block, shortlist).
        Pipeline.send() passes the block directly to route_task(rag_context=block).
      Phase 2 (after route_task): search selected_collections, merge results, return merged text.

    Pipeline.send() calls decomposer.inject_context(rag_text, injection_type="rag_retrieval")
    with the non-None return value from prepare_post_route(). The injected context is consumed
    (prepended to the prompt) at the start of the next main-session send() call, which occurs
    during Decomposer.answer().
    """

    def __init__(self, rag_pipeline: RagPipeline, cfg: RagConfig) -> None:
        # Holds reference to shared RagPipeline; instantiated only when cfg.rag.enabled = True
        ...

    async def prepare_pre_route(self, query: str) -> tuple[str | None, list[CollectionMeta]]:
        """Phase 1: run MultiCollectionRouter, return (<rag_collections> block, candidates).

        For Tier 1 (≤3 collections): returns (None, all_collections) — no block, no side effects.
        For Tiers 2/3: returns (block_str, shortlist) — block_str is passed to route_task().
        Has NO side effects; does NOT call pipeline.inject_context().
        Returns (None, []) if RAG is unavailable or no candidates.
        """

    async def prepare_post_route(
        self, query: str, candidates: list[CollectionMeta], selected_collections: list[str]
    ) -> str | None:
        """Phase 2: search selected collections, merge results, return merged RAG text.

        For Tier 1: ignores selected_collections; searches all candidates directly.
        For Tiers 2/3: searches only the names in selected_collections (filtered against candidates).
        Returns merged RAG text, or None if RAG was skipped or no results found.
        """
```

### Modified components

**`archon/rag/store.py`**
- `RagStore.ensure_meta_table()` — creates the `archon_collection_meta` table if it doesn't exist. Called during `RagStore.connect()`. The centroid column is stored as a JSON-encoded `str` (not a fixed-dimension LanceDB vector column), since centroid dimension varies by embedding model. The `list_collection_meta()` method deserialises the JSON string back to `list[float]` on read.
- `get_collection_meta(name: str) -> CollectionMeta | None` — reads stored centroid + description from `archon_collection_meta` table; returns `None` if not found
- `update_collection_meta(name: str, meta: CollectionMeta) -> None` — writes centroid + description after ingest (upsert by name); serialises centroid as JSON string
- `list_collection_meta(include_centroids: bool = False) -> list[CollectionMeta]` — returns all rows from `archon_collection_meta`; omits `centroid` field (sets to `None`) if `include_centroids=False`; used by `MultiCollectionRouter` directly (called with `include_centroids=True`)
- `list_collections()` — **filters out `archon_collection_meta`** from the results; it is an internal table, not a user collection

**`archon/rag/pipeline.py`**
- `embed_query(text: str) -> list[float]` — public method that delegates to `await self._embedder.embed_one(text)`; used by `MultiCollectionRouter.select()` to embed the query without accessing the private `_embedder` attribute
- `ingest_directory()` — after embedding all chunks: compute centroid, check description regeneration trigger (using `described_at_doc_count`), call `generate_description()` async, call `store.update_collection_meta()` with updated `described_at_doc_count`
- Progress callback: `on_progress(files_done: int, files_total: int, chunks_done: int)` — called per file during ingest; CLI passes a printing callback

**`archon/rag/server.py`**
- New MCP tool `list_collections_meta(include_centroids: bool = False) -> list[CollectionMeta]` — returns all collection metadata; centroids included only when `include_centroids=True`; used by external callers (e.g., `archon doctor`, `archon rag collection info`); the router uses direct store access instead
- No per-collection endpoint is needed; callers that need a single collection's metadata filter the returned list by name

**`archon/ai/pipeline.py`**
- `Pipeline.__init__()` gains optional parameter: `rag_context_provider: RagContextProvider | None = None`
- `Pipeline.send()` flow (when `_rag_context_provider` is set):
  1. After classification: call `(rag_block, candidates) = await self._rag_context_provider.prepare_pre_route(query)` — `rag_block` is `None` for Tier 1, a formatted string for Tiers 2/3; no side effects
  2. Routing path: call `route_task(query, rag_context=rag_block)` — `Decomposer.route_task()` appends `rag_block` to the instruction string before the routing prompt; its JSON response includes `selected_collections`
  3. After `route_task()` returns: call `rag_text = await self._rag_context_provider.prepare_post_route(query, candidates, task_output.selected_collections)`; if fallback (`is_fallback=True`), pass `selected_collections=[]` (Tier 1 degradation applies — search all if ≤3 candidates, else skip)
  4. If `rag_text is not None`: call `self._decomposer.inject_context(rag_text, injection_type="rag_retrieval")` — this context is consumed during the subsequent `answer()` call
- On the direct-chat path (intent == "chat" above confidence threshold): `prepare_pre_route(query)` is still called to get `(_, candidates)`; the returned block is ignored; then call `prepare_post_route(query, candidates, selected_collections=[])` so Tier 1 collections are still searched; `route_task()` is not called on this path

**`archon/ai/decomposer.py`**
- `TaskOutput` dataclass gains `selected_collections: list[str] = field(default_factory=list)` — populated from the `route_task()` JSON response `"selected_collections"` field; defaults to `[]` on parse failure or missing field
- `_parse_task_output()` reads optional `"selected_collections"` field from the parsed JSON dict
- `route_task()` gains optional `rag_context: str | None = None` parameter: `async def route_task(self, prompt: str, rag_context: str | None = None) -> AsyncGenerator[...]`; if `rag_context` is not None, it is appended to the instruction string after the existing context blocks but before the route prompt: `instruction = f"{existing_blocks}\n{rag_context}\n{route_prompt}\n\nUser request: {prompt}"`

**`archon/config/loader.py`**
- `RagConfig` gains: `max_parallel_collections: int = 3`, `routing_confidence_threshold: float = 0.30`, `routing_shortlist_size: int = 8`

**`archon/cli/rag_cmd.py`**
- `_run_collection_info(args)` — fetches and prints `CollectionMeta` for one collection via `list_collections_meta` MCP tool
- `_run_collection_reindex(args)` — calls `pipeline.ingest_directory()` with forced description regeneration; prints progress
- `_run_collection_add()` — adds progress callback to existing ingest call
- `_run_sync()` — adds per-collection progress output

**`archon/cli/doctor.py`**
- Adds RAG health check section: calls `list_collections_meta` MCP tool (or opens store directly if server is stopped), checks staleness, model mismatch, empty collections, missing centroids

### Data flow

```
User message → Pipeline.send(query)
  │
  ├─ [Step 1: Classify] — unchanged
  │
  ├─ RagContextProvider.prepare_pre_route(query)             ← ALWAYS called (both paths); no side effects
  │    │   returns (rag_block: str | None, candidates: list[CollectionMeta])
  │    │
  │    ├─ MultiCollectionRouter.select(query)
  │    │    ├─ rag_pipeline.embed_query(query)               ← public method → _embedder.embed_one()
  │    │    ├─ fetch_metadata()                              ← rag_pipeline.store.list_collection_meta() directly
  │    │    ├─ three-tier logic (≤3 / ≤shortlist / >shortlist)
  │    │    │    Tier 1 (≤3): return all collections; confidence gate NOT applied
  │    │    │    Tier 2/3: rank() with cosine sim + confidence gate; return shortlist
  │    │    └─ return candidate list (may be empty)
  │    │
  │    ├─ Tier 1: rag_block = None
  │    └─ Tier 2/3: rag_block = formatted <rag_collections> string  (NOT injected; returned directly)
  │
  ├─ [ROUTING PATH] Decomposer.route_task(query, rag_context=rag_block)
  │    │   rag_block appended to instruction string before routing prompt (inside route_task())
  │    └─ returns TaskOutput with selected_collections: list[str]
  │         (parsed from "selected_collections" JSON field; defaults to [] on parse failure)
  │         (is_fallback=True → Pipeline applies Tier 1 degradation: search all if ≤3 candidates, else skip)
  │
  ├─ [CHAT PATH] route_task() skipped; rag_block ignored; selected_collections=[] passed to post_route
  │
  ├─ RagContextProvider.prepare_post_route(query, candidates, selected_collections)
  │    │
  │    ├─ Tier 1 (candidates ≤3 or selected_collections=[]): search all candidates directly
  │    ├─ Tier 2/3: search only names in selected_collections (filtered against candidates)
  │    │
  │    ├─ asyncio.Semaphore(max_parallel_collections) bounded parallel searches
  │    │    └─ await rag_pipeline.search(query, collection_name) per selected collection (in-process)
  │    │
  │    ├─ merge_results(per_collection_results)
  │    │    └─ per-collection min-max normalise:
  │    │         if max_score == min_score → all scores = 1.0
  │    │         else score_norm = (score - min_score) / (max_score - min_score)
  │    │       deduplicate, sort descending, top-K
  │    │
  │    └─ return merged_rag_text (str) or None if no results
  │
  ├─ if rag_text is not None:
  │    └─ Pipeline.send() calls decomposer.inject_context(rag_text, injection_type="rag_retrieval")
  │         └─ queued for next main-session send() → ContextInjectedEvent (FEAT-018 pipeline)
  │
  └─ Decomposer.answer(resolved_prompt)                      ← main session consumes injected RAG context
```

### Centroid storage

Stored in a LanceDB table named `archon_collection_meta` (one row per collection). This is an internal table, not a user-facing collection — it bypasses the `_validate_collection` name check in `RagStore` and is never returned by `list_collections()` or exposed as a searchable collection.

`RagStore.ensure_meta_table()` is called during `RagStore.connect()` to create the table if it doesn't exist. The `centroid` column is stored as a JSON-encoded string (not a fixed LanceDB vector column) because centroid dimension depends on the embedding model used at ingest time and may vary across collections.

| column | type | notes |
|--------|------|-------|
| `name` | `str` | collection name (PK) |
| `description` | `str \| None` | Haiku-generated |
| `centroid` | `str \| None` | JSON-encoded `list[float]`; deserialised to `list[float]` on read |
| `doc_count` | `int` | at last ingest |
| `chunk_count` | `int` | at last ingest |
| `embedding_model` | `str` | model used for centroid |
| `last_indexed` | `str` | ISO-8601 |
| `last_described` | `str \| None` | ISO-8601 |
| `described_at_doc_count` | `int \| None` | doc_count when description was last generated; used to compute 20% regeneration trigger |

---

## Task Breakdown

### Phase 1 — Collection metadata infrastructure
> **Releasable**: after Task 1.3; ingest stores centroid + description; server exposes metadata endpoints

#### Task 1.1 — `CollectionMeta` dataclass and `archon_collection_meta` table in `RagStore`
- [ ] **Files**: `archon/rag/collection_meta.py` (new), `archon/rag/store.py`
- **Depends on**: nothing
- **Description**: `CollectionMeta` dataclass (including `described_at_doc_count: int | None`); `RagStore.ensure_meta_table()` creates the `archon_collection_meta` table if it doesn't exist (called from `RagStore.connect()`); centroid stored as JSON-encoded string (not fixed LanceDB vector) since dimension varies by embedding model; `RagStore.get_collection_meta()`, `update_collection_meta()`, `list_collection_meta(include_centroids: bool = False)`, `archon_collection_meta` LanceDB table (upsert by name); table bypasses `_validate_collection` name check (internal table); `list_collections()` filters out `archon_collection_meta` from results
- **Tests** — `tests/rag/test_store.py`: `test_collection_meta_upsert`, `test_collection_meta_get_missing_returns_none`, `test_list_collections_excludes_meta_table`, `test_ensure_meta_table_created_on_connect`

#### Task 1.2 — Centroid computation and incremental update in `RagPipeline.ingest_directory()`
- [ ] **File**: `archon/rag/pipeline.py`
- **Depends on**: Task 1.1
- **Description**: after embedding all chunks, compute `mean(embeddings)` as centroid; call `store.update_collection_meta()` with updated centroid, doc_count, chunk_count, embedding_model, last_indexed; running mean update for pure additions; full recompute from remaining embeddings after any delete; add public `embed_query(text: str) -> list[float]` method that delegates to `await self._embedder.embed_one(text)` (used by `MultiCollectionRouter` to avoid accessing private `_embedder`)
- **Tests** — `tests/rag/test_pipeline.py`: `test_ingest_computes_centroid`, `test_ingest_updates_centroid_incrementally`, `test_ingest_recomputes_centroid_after_delete`, `test_embed_query_delegates_to_embedder`

#### Task 1.3 — Haiku description generation on ingest
- [ ] **Files**: `archon/rag/description_generator.py` (new), `archon/rag/pipeline.py`
- **Depends on**: Task 1.1
- **Description**: `generate_description(chunks, name)` samples up to 20 chunks, calls Haiku via `ClaudeSDKClient` (NOT `anthropic.AsyncAnthropic()`), returns description string; this is a one-shot call acceptable at ingest time because ingest is infrequent; `ingest_directory()` calls it async; regenerates when `abs(current_doc_count - described_at_doc_count) / described_at_doc_count >= 0.20`; stores updated `described_at_doc_count` alongside `last_described`; failure → description stays `None`, no error
- **Tests** — `tests/rag/test_description_generator.py`: `test_generate_description_calls_haiku`, `test_generate_description_on_failure_returns_none`, `test_regeneration_trigger_at_20pct_change`, `test_no_regeneration_below_threshold`

#### Task 1.4 — `list_collections_meta` MCP tool in `server.py`
- [ ] **File**: `archon/rag/server.py`
- **Depends on**: Task 1.1
- **Description**: new MCP tool `list_collections_meta(include_centroids: bool = False)` returning `list[CollectionMeta]`; centroids included only when `include_centroids=True`; returns empty list when no collections exist; returns error dict on inaccessible store; used by external callers (CLI, doctor); the router uses direct store access
- **Tests** — `tests/rag/test_server.py`: `test_list_collections_meta_returns_list`, `test_list_collections_meta_omits_centroids_by_default`, `test_list_collections_meta_includes_centroids_when_requested`, `test_list_collections_meta_empty_store`

### Phase 2 — Multi-collection router
> **Releasable**: after Task 2.2; routing logic available for context_provider integration

#### Task 2.1 — `MultiCollectionRouter` with centroid pre-ranking
- [ ] **File**: `archon/rag/router.py` (new)
- **Depends on**: Task 1.1, Task 1.2
- **Description**: `MultiCollectionRouter(rag_pipeline: RagPipeline, shortlist_size: int, confidence_threshold: float)` — calls `rag_pipeline.embed_query(text)` (public method added in Task 1.2), no direct `_embedder` access; `fetch_metadata()` calls `await rag_pipeline.store.list_collection_meta(include_centroids=True)` directly (cached per instance); `rank(query_embedding, collections)` with cosine similarity + confidence gate + shortlist cap; `select(query)` entry point applying three-tier logic — Tier 1 (≤3): return all, skip confidence gate; Tier 2/3: rank + gate
- **Tests** — `tests/rag/test_router.py`: `test_rank_returns_sorted_by_similarity`, `test_rank_confidence_gate_returns_empty`, `test_rank_none_centroid_placed_last`, `test_rank_shortlist_size_cap`, `test_small_collection_set_skips_preranking_and_confidence_gate`, `test_fetch_metadata_cached_across_calls`, `test_medium_collection_set_passes_all_to_route_task_without_preranking`

#### Task 2.2 — Config additions for routing parameters
- [ ] **File**: `archon/config/loader.py`
- **Depends on**: nothing
- **Description**: add `max_parallel_collections: int = 3`, `routing_confidence_threshold: float = 0.30`, `routing_shortlist_size: int = 8` to `RagConfig`
- **Tests** — `tests/config/test_rag_config.py`: `test_routing_defaults`, `test_routing_config_parsed_from_toml`

### Phase 3 — Decomposer integration
> **Releasable**: after Task 3.2; Archon automatically selects and searches collections on every query

#### Task 3.1 — `TaskOutput` extended with `selected_collections` and `route_task()` signature update
- [ ] **Files**: `archon/ai/decomposer.py`, `archon/ai/prompts/route_task.md`
- **Depends on**: nothing
- **Description**: add `selected_collections: list[str] = field(default_factory=list)` to `TaskOutput` dataclass; update `_parse_task_output()` to read optional `"selected_collections"` field from JSON (defaults to `[]` on parse failure or missing field); add optional `rag_context: str | None = None` parameter to `route_task()` — if not None, appended to the instruction string after existing context blocks but before the route prompt; update `route_task.md` prompt to instruct: when a `<rag_collections>` block is present in the instruction, include `"selected_collections": ["name1", "name2"]` in the JSON response alongside `scope`/`summary`/`prompt`; when no block is present, omit the field
- **Tests** — `tests/ai/test_decomposer.py`: `test_task_output_selected_collections_parsed`, `test_task_output_selected_collections_defaults_to_empty`, `test_route_task_appends_rag_context_to_instruction`

#### Task 3.2 — `RagContextProvider` multi-collection retrieval
- [ ] **File**: `archon/ai/rag_context_provider.py` (new)
- **Depends on**: Task 2.1, Task 2.2, Task 3.1
- **Description**: `RagContextProvider(rag_pipeline: RagPipeline, cfg: RagConfig)` — standalone class, NOT implementing `ContextProvider` protocol; two methods: `prepare_pre_route(query: str) -> tuple[str | None, list[CollectionMeta]]` and `prepare_post_route(query: str, candidates: list[CollectionMeta], selected_collections: list[str]) -> str | None`; pre-route: calls `MultiCollectionRouter.select(query)` → for Tier 1 returns `(None, candidates)`; for Tiers 2/3 returns `(block_str, shortlist)` — NO calls to `inject_context()`; post-route: searches selected (or all Tier-1) collections via `asyncio.Semaphore(max_parallel_collections)` bounded parallel `await rag_pipeline.search(query, name)` → merge + min-max normalise (if `max==min` → score 1.0; else standard formula) → top-K selection → returns merged `rag_text` or `None`; if router returns empty list or RAG pipeline unavailable → return `None` with debug log
- **Tests** — `tests/ai/test_rag_context_provider.py`: `test_rag_pre_route_returns_block_for_tier2`, `test_rag_pre_route_returns_none_block_for_tier1`, `test_rag_post_route_searches_selected_collections`, `test_rag_post_route_returns_none_on_empty_selected`, `test_rag_post_route_searches_all_for_tier1`, `test_rag_skips_on_pipeline_error`, `test_rag_parallel_search_bounded_by_semaphore`, `test_rag_returns_merged_text`, `test_parse_collection_names_from_json`, `test_parse_collection_names_fallback_on_invalid`, `test_parse_collection_names_ignores_unknown_names`, `test_merge_results_normalizes_scores`

#### Task 3.3 — Wire `RagContextProvider` into `Pipeline`
- [ ] **File**: `archon/ai/pipeline.py`
- **Depends on**: Task 3.2
- **Description**: add `rag_context_provider: RagContextProvider | None = None` parameter to `Pipeline.__init__()`; in `Pipeline.send()`: (a) after classification and on BOTH paths, call `(rag_block, candidates) = await self._rag_context_provider.prepare_pre_route(query)` if set — no side effects; (b) routing path: pass `rag_context=rag_block` to `route_task()`; (c) after `route_task()` (or directly on chat path), call `rag_text = await self._rag_context_provider.prepare_post_route(query, candidates, selected_collections)` — on fallback path with `is_fallback=True`, pass `selected_collections=[]` so Tier 1 degradation applies; (d) if `rag_text is not None`, call `self._decomposer.inject_context(rag_text, injection_type="rag_retrieval")`; on the direct-chat path: `prepare_pre_route` is still called; `rag_block` is ignored; `prepare_post_route` is called with `selected_collections=[]`; gateway instantiates `RagContextProvider` when `cfg.rag.enabled = True` and passes it to `Pipeline`
- **Tests** — `tests/ai/test_pipeline.py`: `test_pipeline_calls_inject_context_with_rag_text`, `test_pipeline_skips_inject_context_when_prepare_returns_none`, `test_pipeline_skips_rag_when_provider_is_none`, `test_pipeline_calls_pre_route_on_both_paths`, `test_pipeline_passes_rag_block_to_route_task`, `test_pipeline_calls_post_route_after_route_task`, `test_pipeline_fallback_applies_tier1_degradation`

#### Task 3.4 — Telegram visibility for RAG injection
- [ ] **Files**: `archon/chat/handler.py`, `archon/ai/event_mapper.py` (FEAT-018 constants)
- **Depends on**: Task 3.2
- **Description**: `ContextInjectedEvent` with `injection_type="rag_retrieval"` and `detail=collection_name`; format in `handler.py`: `🔍 RAG: N chunks from <collection>` in verbose/debug, silent otherwise; history entry via existing `event_renderer.py`
- **Tests** — `tests/chat/test_handler.py`: `test_rag_injection_visible_in_verbose`, `test_rag_injection_silent_in_quiet`

### Phase 4 — CLI additions
> **Releasable**: after Task 4.3; full CLI support for metadata inspection, reindex, progress, dry-run

#### Task 4.1 — `archon rag collection info` and `archon rag collection reindex`
- [ ] **File**: `archon/cli/rag_cmd.py`
- **Depends on**: Task 1.4
- **Description**: `info` calls `list_collections_meta` MCP tool and prints the matching `CollectionMeta`; `reindex` calls `ingest_directory()` with force-regenerate flag, prints progress
- **Tests** — `tests/cli/test_rag_cmd.py`: `test_collection_info_output`, `test_collection_info_no_centroid`, `test_collection_reindex_prints_progress`

#### Task 4.2 — Ingest progress feedback
- [ ] **File**: `archon/rag/pipeline.py`, `archon/cli/rag_cmd.py`
- **Depends on**: Task 1.2
- **Description**: `on_progress` callback in `ingest_directory()`; CLI passes a stdout-printing callback to `add` and `sync`
- **Tests** — `tests/rag/test_pipeline.py`: `test_ingest_calls_progress_callback`, `tests/cli/test_rag_cmd.py`: `test_add_prints_progress`

#### Task 4.3 — `archon rag collection remove --dry-run`
- [ ] **File**: `archon/cli/rag_cmd.py`
- **Depends on**: nothing (extends FEAT-021 Task 4.4)
- **Description**: `--dry-run` flag prints what would be removed (config entry + LanceDB table name) without executing; mutually exclusive with `--force`
- **Tests** — `tests/cli/test_rag_cmd.py`: `test_collection_remove_dry_run_prints_without_executing`

### Phase 5 — Doctor integration
> **Releasable**: after Task 5.1

#### Task 5.1 — RAG collection health checks in `archon doctor`
- [ ] **File**: `archon/cli/doctor.py`
- **Depends on**: Task 1.4
- **Description**: calls `list_collections_meta` MCP tool (or opens store directly if RAG server is stopped); checks staleness, model mismatch, empty collections, missing centroid; prints one warning line per issue
- **Tests** — `tests/cli/test_doctor.py`: `test_doctor_warns_stale_collection`, `test_doctor_warns_model_mismatch`, `test_doctor_warns_empty_collection`, `test_doctor_warns_missing_centroid`, `test_doctor_no_warnings_on_healthy_collections`

### Phase 6 — Documentation
#### Task 6.1 — Update docs
- [ ] **Files**: `Documentation/UserManual/rag_guide.md`, `examples/config.toml.example`, `Documentation/Architecture/180_rag_architecture.md`, `CLAUDE.md`
- **Depends on**: Task 4.3
- **Description**: document routing config keys, collection selection context block format, `info`/`reindex` commands, doctor checks; add `max_parallel_collections`, `routing_confidence_threshold`, `routing_shortlist_size` to config example; note that `RagContextProvider` is a standalone collaborator injected into `Pipeline`, not a `ContextProvider` implementation; document the two-phase prepare_pre_route / prepare_post_route flow and that `Pipeline.send()` calls `decomposer.inject_context()` with the result
