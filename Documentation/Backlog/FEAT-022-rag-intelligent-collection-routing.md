# FEAT-022 — RAG Intelligent Collection Routing

**Purpose**: Replace the single-collection RAG search with decomposer-driven automatic multi-collection routing — the decomposer selects which collections to query based on the user's request, searches them in resource-bounded parallel batches, and injects the merged results as context, all without any user instruction.
**Audience**: Archon users with multiple indexed collections who want Claude to automatically draw on the right knowledge sources without being told which to use.
**Status**: To Do
**Depends on**: FEAT-021 (multi-collection infrastructure, `RagCollectionSync`, `archon rag collection` CLI)

---

## Background

After FEAT-021, Archon can manage multiple LanceDB collections declared in `config.toml`. However, the RAG server still searches a single `default_collection` per request. Every query hits the same collection regardless of relevance.

The desired behaviour: the decomposer receives a ranked shortlist of available collections and their auto-generated descriptions, selects the 2–3 most relevant ones, searches them in parallel via the RAG server, merges the results, and injects the top-K chunks — all before the main LLM query executes. No user instruction is needed; the right collections are chosen automatically based on the query.

The main resource concern is scale: with 20+ collections, naive parallel search across all of them would saturate the RAG server and local disk I/O. The solution is a two-stage approach: fast embedding pre-ranking (milliseconds, no LLM) narrows candidates to a small shortlist; the decomposer reasons only over that shortlist; and actual search is batched to at most `rag.max_parallel_collections` (default 3) concurrent requests.

---

## Goal

1. Every task-intent query automatically identifies and searches the 2–3 most relevant collections.
2. Resource usage is bounded: at most `max_parallel_collections` concurrent RAG searches at any time; a confidence gate skips RAG entirely when no collection is a strong match.
3. Two default collections (`~/.archon/history/sessions` and `~/.archon/workspace`) are always searched via `rag.pinned_collections`, bypassing the confidence gate and decomposer selection — they contribute to the total parallel search budget.
4. Each collection has an auto-generated description (produced by Haiku during ingest) that the decomposer uses for selection reasoning.
4. Each collection stores an embedding centroid used for the fast pre-ranking step before the decomposer is involved.
5. The feature degrades gracefully: if RAG is disabled or no collection matches, the session continues without RAG context.

---

## Scope

### In Scope
- `CollectionMeta` dataclass — name, description, centroid, doc_count, chunk_count, embedding_model, described_at_doc_count
- `list_collections()` MCP tool (already exists) — returns all `CollectionMeta` entries (without centroid vectors, for brevity)
- `get_collections_meta()` MCP tool (new) — bulk tool returning all `CollectionMeta` entries WITH centroids included (used by `MultiCollectionRouter.fetch_metadata()`)
- `get_collection_meta(name: str)` MCP tool (new) — returns full `CollectionMeta` for one collection including centroid; raises error on unknown name
- Centroid computation during ingest: mean of all chunk embeddings in the current ingest batch, stored in collection metadata
- Haiku-generated collection description: 2–3 sentence summary from a sample of chunks, generated async during ingest
- Description re-generation trigger: doc_count changes by ≥20% since last generation
- Centroid recomputation: full mean of all chunk embeddings on each ingest (replaced on every ingest)
- `MultiCollectionRouter` — pre-ranks collections by cosine similarity between query embedding and collection centroids, returns shortlist
- `archon/ai/rag_context_provider.py` (new): standalone `RagContextProvider` orchestrator — calls `MultiCollectionRouter`, passes shortlist descriptions to router session prompt, executes batch-parallel searches, injects results via `inject_context()`; `archon/ai/pipeline.py` modified to call `RagContextProvider` between `route_task()` and `session.send()`
- `rag.max_parallel_collections` config key (default `3`)
- `rag.routing_confidence_threshold` config key (default `0.30`): if best centroid similarity < threshold, skip RAG
- `rag.routing_shortlist_size` config key (default `8`): max collections passed to decomposer for reasoning
- `rag.pinned_collections` config key (default `["~/.archon/history/sessions", "~/.archon/workspace"]`): list of collection paths always searched regardless of routing confidence or decomposer selection; `[]` disables pinned behaviour
- `archon rag collection info <name>` CLI command — shows description, centroid status, doc/chunk counts, embedding model
- Ingest progress feedback: file count, chunk count printed to stdout during `archon rag collection add` and `archon rag sync`
- `archon rag collection reindex <name>` CLI command — re-embeds all documents, regenerates centroid and description
- Dry-run support for `archon rag collection remove --dry-run` (extends FEAT-021's `--force` flag with a preview mode)
- `archon doctor` collection health checks: stale index (>7 days), embedding model mismatch, zero-document collections, missing centroid
- RAG injection visibility: `ContextInjectedEvent` with `injection_type="rag_retrieval"` and `detail=f"{chunk_count} chunks from {', '.join(actual_searched_names)}"` where `actual_searched_names` are the post-filter names actually searched (reuses FEAT-018 pipeline); in verbose/debug mode Telegram shows `🔍 RAG: {chunk_count} chunks from {col1}, {col2}` where `chunk_count` comes from the `detail` field prefix; silent in quiet/normal

### Out of Scope
- Cross-collection query result reranking with a cross-encoder model (future — reranker already exists per-collection)
- Changing how results are ranked within a single collection (existing `bge-reranker-v2-m3` unchanged)
- Exposing collection selection to the user (user never needs to specify a collection)
- Per-collection embedding model configuration
- Automatic re-index on file-system change (file-watch)

---

## Acceptance Criteria

### Collection metadata
- [ ] `CollectionMeta` dataclass: `name`, `description`, `centroid: list[float] | None`, `doc_count`, `chunk_count`, `embedding_model`, `last_indexed: datetime | None`, `last_described: datetime | None`, `described_at_doc_count: int | None`
- [ ] During ingest, the chunk centroid (mean of all chunk embeddings in the current ingest batch, as `list[float]`) is computed fresh and stored in LanceDB collection metadata (replaces previous centroid on every ingest)
- [ ] During ingest, a Haiku call samples up to 20 representative chunks and generates a 2–3 sentence description stored alongside the centroid
- [ ] Description regenerates automatically when `abs(current_doc_count - described_at_doc_count) / described_at_doc_count >= 0.20`; if `described_at_doc_count` is `None` or `0`, always generate description
- [ ] If Haiku call fails during ingest, description is `None`; ingest completes without error

### RAG server MCP tools
- [ ] `list_collections()` MCP tool returns `list[CollectionMeta]` (centroid omitted for size); already exists, extended with `CollectionMeta` fields
- [ ] `get_collections_meta()` MCP tool (new) returns `list[CollectionMeta]` WITH centroids included — this is the tool called by `MultiCollectionRouter.fetch_metadata()` via JSON-RPC POST to the RAG server URL
- [ ] `get_collection_meta(name: str)` MCP tool (new) returns full `CollectionMeta` for a single collection including centroid; raises `ValueError` on unknown name
- [ ] All list tools return an empty list when no collections exist
- [ ] Existing `search()` and `ingest()` MCP tools unchanged
- [ ] `MultiCollectionRouter` calls `get_collections_meta()` via a direct JSON-RPC POST (`httpx.AsyncClient`) to the RAG server URL; `RagContextProvider` similarly uses `httpx.AsyncClient` to call `search()` per selected collection

### Multi-collection routing
- [ ] `MultiCollectionRouter` is initialised with an `Embedder` instance (provided by `RagContextProvider`); fetches full metadata (with centroids) via `get_collections_meta()` MCP tool call once per instance (cached in `_cached_metadata`); has a 10-second timeout — if exceeded, returns `[]` and logs debug-level warning, causing `select()` to return `[]` and RAG to be skipped; `MultiCollectionRouter` is instantiated fresh per `route_task()` call, so metadata is fetched once per user query
- [ ] `MultiCollectionRouter.rank(query_embedding) -> list[CollectionMeta]`: returns collections sorted descending by cosine similarity between query embedding and centroid; collections with `centroid=None` are placed after centroid-ranked results
- [ ] None-centroid handling in confidence gate: if ALL collections have `centroid=None`, bypass the confidence gate entirely and pass all collections (up to `routing_shortlist_size`) to the decomposer; if SOME collections have valid centroids, apply confidence gate to the best centroid similarity — if gate fails, return empty list (None-centroid collections are not included when gate fails)
- [ ] If `max(similarity_scores) < rag.routing_confidence_threshold` (and at least one centroid is non-None), routing returns empty list (RAG skipped)
- [ ] The confidence gate applies only to `routable_meta` collections; `pinned_meta` collections are unaffected by the gate
- [ ] Three-tier shortlist logic applied to `routable_meta` count only (pinned collections are excluded from the tier calculation):
  - **Tier 1** (`routable_meta` count ≤ 3): skip BOTH centroid pre-ranking AND decomposer; `RagContextProvider` searches all routable collections directly in Phase B alongside pinned; no `<rag_collections>` block is built; confidence gate does NOT apply — with very few collections, searching all is negligible and centroid-based filtering is unreliable for new collections
  - **Tier 2** (4 ≤ `routable_meta` count ≤ `rag.routing_shortlist_size`): skip centroid pre-ranking; pass all routable collections to decomposer via `<rag_collections>` block; decomposer selects which to search
  - **Tier 3** (`routable_meta` count > `rag.routing_shortlist_size`): centroid pre-rank to get shortlist (confidence gate applies here); pass shortlist to decomposer via `<rag_collections>` block; decomposer selects which to search
- [ ] At most `rag.routing_shortlist_size` routable collections are passed to the decomposer for reasoning (Tier 3 cap)

### Pinned collections
- [ ] `RagConfig` has `pinned_collections: list[str]` with default `["~/.archon/history/sessions", "~/.archon/workspace"]`
- [ ] `pinned_collections = []` disables pinned behaviour entirely; all collections enter routing normally
- [ ] `RagContextProvider.get_pre_context()` resolves `pinned_collections` paths to names via `path_to_collection_name()`; paths that resolve to an unknown collection (not present in fetched metadata) are silently skipped with a debug-level log entry
- [ ] Fetched collection metadata is split into `pinned_meta` (names matching resolved pinned paths) and `routable_meta` (all others); centroid pre-ranking and the confidence gate are applied only to `routable_meta`
- [ ] The `<rag_collections>` block passed to the decomposer contains only `routable_meta` entries; pinned collections are excluded — the decomposer has no awareness of them
- [ ] Pinned collections bypass the confidence gate: they are always added to `to_search` in Phase B regardless of routing outcome
- [ ] If the decomposer selects zero routable collections (or the confidence gate fails for all routable collections), Phase B still searches the pinned collections and RAG injection proceeds with pinned-only results
- [ ] Decomposer-selected collections are capped at `max_parallel_collections - len(resolved_pinned_names)` (minimum 0) to keep total searches within the resource bound
- [ ] Pinned collection results are merged with router-selected results via the same `_normalize_and_merge()` function; no special score weighting
- [ ] `actual_searched_names` passed to `inject_context()` includes both pinned and router-selected collection names
- [ ] `archon doctor` warns for each path in `rag.pinned_collections` that is not also present in `rag.collections`: `⚠ Pinned collection '<path>' is not declared in rag.collections — it will be skipped at runtime`; this check runs regardless of RAG server availability (config-only, no HTTP call)

### Decomposer integration — two-phase protocol

**Phase A — Collection selection (router session)**:
- [ ] `RagContextProvider` runs BEFORE `route_task()` is called; `get_pre_context()` resolves `pinned_collections` to `_pinned_names`, splits metadata into `pinned_meta` and `routable_meta`, and applies tier logic to `routable_meta` count: Tier 1 returns `None` (no decomposer call); Tiers 2 and 3 embed the query locally and build a `<rag_collections>` block from `routable_meta` (with centroid pre-ranking for Tier 3 only)
- [ ] Before building the shortlist, `get_pre_context()` resolves `pinned_collections` to `_pinned_names` and splits metadata into `pinned_meta` and `routable_meta`; the shortlist and `<rag_collections>` block are built from `routable_meta` only
- [ ] The shortlist + descriptions are passed to `route_task()` via a new `rag_pre_context: str | None = None` parameter; this block is appended to the router session's instruction prompt AFTER `load_prompt("route_task")` content and BEFORE the final instruction is sent to the router session — positioned at the END of the instruction block so the LLM reasons about routing first, then outputs collection selection
- [ ] The router session prompt instructs the LLM: "Output your selected collections as `<rag_selected_collections>name1, name2</rag_selected_collections>` at the end of your routing decision"
- [ ] `route_task()` itself is responsible for parsing `<rag_selected_collections>` from the router's Response content (in `_parse_task_output()`); `_parse_task_output()` extracts collection names from `<rag_selected_collections>` tags using a simple XML extract + comma-split + strip-whitespace — NO filtering against the shortlist; it stores the raw parsed names in `task_output.selected_collections`; `RagContextProvider.search_and_prepare(task_output, query)` reads `task_output.selected_collections` directly — no need to observe yielded events; if `task_output.selected_collections` is `None`, `search_and_prepare()` returns `None` immediately
- [ ] `route_task()` signature: `route_task(prompt: str, rag_pre_context: str | None = None)`

**Phase B — RAG injection (main session)**:
- [ ] Phase B prepends resolved pinned names to `to_search` before adding decomposer-selected names; decomposer-selected names are capped at `max_parallel_collections - len(_pinned_names)` (minimum 0)
- [ ] After Phase A, `RagContextProvider` searches the selected collections in parallel using `asyncio.Semaphore(cfg.rag.max_parallel_collections)`; `asyncio.gather(*..., return_exceptions=True)` bounds concurrency to at most `max_parallel_collections` concurrent requests; per-collection exceptions are caught and logged at debug level — results from successful collections are still merged and returned; if ALL collections fail, `search_and_prepare()` returns `None` (RAG skipped silently)
- [ ] Results are merged and re-ranked: normalise scores per-collection (`(score - min_score) / (max_score - min_score)`; if `max_score == min_score`, assign `normalized = 0.5` — mid-range neutral — to prevent single-result or uniform-score collections from dominating the merged ranking); merge all, sort descending, take top `rag.top_k_return`
- [ ] `search_and_prepare(task_output, query) -> tuple[str, int] | None` returns `(merged_text, chunk_count)` where `chunk_count` is the number of individual chunks in the merged result, or `None` if no results; it does NOT call `inject_context()` internally
- [ ] `Pipeline.send()` calls `route_task()` (Phase A), then when `search_and_prepare()` returns a non-None value calls `rag_text, chunk_count = result` and then `self._session.inject_context(rag_text, injection_type="rag_retrieval", detail=f"{chunk_count} chunks from {', '.join(actual_searched_names)}")` where `actual_searched_names` are the post-filter names actually searched, then calls the main `session.send()` — the injected RAG context is prepended to the main send so the decomposer answers WITH the RAG results

**Parsing rules**:
- [ ] `_parse_task_output()` extracts content between `<rag_selected_collections>` tags; splits by comma and strips whitespace; stores raw parsed names in `task_output.selected_collections` — NO filtering against the shortlist at this stage; `search_and_prepare()` filters `task_output.selected_collections` against `self._last_shortlist_names` (cached by `get_pre_context()`) to discard hallucinated/typo names, then caps at 3 in shortlist-rank order; if zero valid names remain after filtering, RAG is skipped
- [ ] When `<rag_selected_collections>` appears multiple times, use only the first occurrence
- [ ] Missing closing tag → zero selections → RAG skipped
- [ ] Empty tag (`<rag_selected_collections></rag_selected_collections>`): splitting empty string by comma produces a list with one empty string; the empty string is discarded during the name-validation step (empty strings cannot match any collection name), resulting in zero valid names → RAG skipped
- [ ] Names may be separated by commas with arbitrary whitespace including newlines; `.strip()` (not `.strip(' ')`) is used on each name to handle all whitespace variants
- [ ] Collection names must not contain commas — this constraint is enforced by FEAT-021's `path_to_collection_name()` sanitization

**Error handling**:
- [ ] If decomposer selects zero collections (or all have low confidence), only pinned collections are searched; if `pinned_collections = []` and decomposer selects zero, nothing is injected
- [ ] If RAG server is unreachable, routing is skipped silently with a debug-level log entry; session continues

### CLI additions
- [ ] `archon rag collection info <name>` prints: description, centroid status (present/missing), doc count, chunk count, embedding model, last indexed date, last described date; output format is key-value pairs, one per line:
  ```
  Description:     <text or "(none)">
  Centroid:        present / missing
  Doc count:       42
  Chunk count:     318
  Embedding model: BAAI/bge-small-en-v1.5
  Last indexed:    2026-03-21 (3 days ago)
  Last described:  2026-03-21 (3 days ago) / (never)
  ```
  Dates shown as `YYYY-MM-DD (N days ago)` relative format; if `None`, show `(never)`; `description=None` shown as `(none)`
- [ ] `archon rag collection reindex <name>` re-ingests all documents, recomputes centroid, regenerates Haiku description; prints progress (files, chunks)
- [ ] `archon rag collection add <path>` prints progress during ingest: `Indexing: N/M files (K chunks)...`
- [ ] `archon rag sync` prints per-collection progress during ingest
- [ ] `archon rag collection remove <name> --dry-run` prints what would be removed without executing

### Doctor
- [ ] `archon doctor` warns for collections not re-indexed in >7 days: `⚠ Collection '<name>' last indexed N days ago`; staleness is `(datetime.now(UTC) - last_indexed).days > 7` (integer days, floor division); N is `(datetime.now(UTC) - last_indexed).days`
- [ ] `archon doctor` warns for collections whose `embedding_model` differs from `cfg.rag.embedding_model`: `⚠ Collection '<name>' indexed with '<old_model>', current model is '<new_model>' — reindex required`
- [ ] `archon doctor` warns for collections with `doc_count == 0`: `⚠ Collection '<name>' is empty`
- [ ] `archon doctor` warns for collections with `centroid=None`: `⚠ Collection '<name>' has no centroid — routing disabled for this collection`
- [ ] `archon doctor` warns for each path in `rag.pinned_collections` not also in `rag.collections`: `⚠ Pinned collection '<path>' is not declared in rag.collections — it will be skipped at runtime` (config-level check, no RAG server required)

### Tests
- [ ] All existing tests pass
- [ ] New tests achieve ≥85% coverage for all new and modified modules
- [ ] `MultiCollectionRouter.rank()` tested with mocked centroid data for: normal ranking, confidence gate skip, `None` centroid handling, shortlist size cap, all-None-centroid bypass; `test_router_fetch_metadata_timeout_returns_empty`; `test_rank_skips_collections_with_mismatched_embedding_model` — asserts that collections with a different `embedding_model` than `cfg.rag.embedding_model` are treated as `centroid=None` and placed last in the ranking, not used for similarity scoring, providing protection against stale centroids in different vector spaces
- [ ] Decomposer integration tested with mocked router and RAG server (in `tests/ai/test_rag_context_provider.py`): assert `inject_context` called with correct type and `detail=", ".join(selected_names)` format; `test_rag_parsing_filters_hallucinated_names`, `test_rag_parsing_handles_extra_whitespace`, `test_rag_skips_when_parsing_yields_zero_collections`, `test_rag_parsing_multiple_tags_uses_first`, `test_rag_parsing_unclosed_tag_skips_rag`
- [ ] Score normalization tested via `rag_context_provider._normalize_and_merge()` directly: `test_score_normalization_single_result` (asserts `normalized_score = 0.5` for a single-result collection), `test_score_normalization_identical_scores` (asserts `normalized_score = 0.5` when all scores are equal)
- [ ] RAG server MCP tool tests: `list_collections()` (centroid omitted), `get_collections_meta()` (bulk with centroids), `get_collection_meta(name)` (single with centroid, error on unknown); `test_collections_meta_bulk_endpoint_returns_centroids`
- [ ] Centroid computation tested: mean of known embeddings from current ingest batch, regeneration trigger; `test_ingest_computes_centroid_from_all_chunks`; `test_regeneration_trigger_when_described_at_doc_count_is_none`; `test_collection_meta_upsert_includes_described_at_doc_count`; `test_list_collections_excludes_archon_prefix`
- [ ] Description generation tested: `test_generate_description_timeout_returns_none`, `test_generate_description_returns_none_on_empty_chunks`
- [ ] Doctor tests: assert each warning condition triggers the correct message; `test_doctor_skips_rag_checks_when_server_down` — asserts that when the RAG server HTTP call fails, the doctor emits the "RAG server is not running" warning and skips all collection checks
- [ ] `test_pinned_collections_bypass_confidence_gate` — mocked router returns low similarity for all routable collections; assert pinned collections are searched regardless
- [ ] `test_pinned_collections_excluded_from_decomposer_block` — assert the `<rag_collections>` context block text does not contain pinned collection names
- [ ] `test_pinned_only_search_when_router_selects_zero` — decomposer selects no routable collections; assert only pinned collections are searched and `inject_context` is still called
- [ ] `test_pinned_and_selected_merged` — 2 pinned + 1 decomposer-selected; assert `_normalize_and_merge()` receives results from all 3 collections
- [ ] `test_pinned_counts_toward_max_parallel` — `max_parallel=3`, 2 pinned; assert decomposer-selected collections capped at 1
- [ ] `test_pinned_empty_list_routes_normally` — `pinned_collections=[]`; assert all collections enter routing and confidence gate normally
- [ ] `test_pinned_unknown_path_silently_skipped` — pinned path with no matching collection in fetched metadata; assert debug log emitted, no error, remaining pinned collections still searched
- [ ] `test_doctor_warns_pinned_not_in_collections` — `pinned_collections` contains a path not present in `collections`; assert warning message matches `⚠ Pinned collection '<path>' is not declared in rag.collections — it will be skipped at runtime`
- [ ] `test_doctor_pinned_check_runs_when_server_down` — RAG server unreachable (HTTP fails); assert pinned-not-in-collections warning still fires (config-only check)
- [ ] `test_actual_searched_names_includes_pinned` — assert `inject_context()` detail string contains both pinned and router-selected collection names

---

## What Does NOT Change

- `RagPipeline.search()` — still takes a single `collection` parameter; multi-collection orchestration is above this layer
- `search()` MCP tool — unchanged; `MultiCollectionRouter` calls it once per selected collection via JSON-RPC
- `BAAI/bge-reranker-v2-m3` reranker — still used per-collection; cross-collection merge uses score normalisation only
- `RagCollectionSync` — unchanged; FEAT-022 only adds metadata fields computed during ingest
- Classifier — unmodified; collection routing is entirely the decomposer's concern
- `ContextReminder` injection path — RAG uses the existing `inject_context()` queue
- `_normalize_and_merge()` — unchanged; pinned collection results enter the same merging function as router-selected results with no special treatment

---

## Known Limitations / Accepted Trade-offs

- **`archon rag sync` vs `archon rag collection reindex <name>`**: `archon rag sync` reconciles collection membership (adds/removes collections per config) and re-ingests files that have changed. It calls `ingest_directory()` per collection, which recomputes the centroid and regenerates descriptions if the 20% threshold is met. So `sync` will naturally refresh centroids and descriptions over time. `archon rag collection reindex <name>` is an explicit force-refresh: it re-ingests ALL documents unconditionally, sets `force_regenerate_description=True` to bypass the 20% threshold, and guarantees the centroid and description reflect the current state of the collection. Use `reindex` after: (a) changing `cfg.rag.embedding_model`, (b) suspecting a corrupted centroid, or (c) wanting a fresh description without waiting for the 20% threshold.
- Centroid pre-ranking degrades for heterogeneous collections (mixed-topic content averages to a noisy centroid). Mitigated by the confidence gate: low-confidence centroids cause RAG to be skipped. Collections with `centroid=None` are placed after centroid-ranked results in the shortlist, but only when the confidence gate passes; when the gate fails, None-centroid collections are not included.
- Centroid is always computed as the mean of all chunk embeddings in the current ingest batch. For `archon rag collection reindex <name>`, all documents are re-ingested so the centroid reflects the full collection. For `archon rag collection add <path>`, only the current batch's embeddings contribute to the centroid; use `reindex` for a full centroid recomputation.
- Decomposer collection selection adds one LLM reasoning step per query for Tiers 2 and 3 (Haiku-class model preferred for speed). Tier 1 (≤ 3 routable collections) skips the decomposer entirely — all routable collections are searched directly alongside pinned. This keeps typical small installs (default 2 pinned + 1–3 domain collections) fast with no LLM overhead on routing.
- Cross-collection score normalisation is approximate (per-collection min/max normalisation). A cross-encoder reranker would be more accurate but is deferred (FEAT-020 scope).
- Description generation costs one Haiku call per collection at ingest time. For a fresh install with 2 default collections, this is ~2 API calls. Acceptable.
- `MultiCollectionRouter` caches collection metadata per instance (populated on first `select()` call). Since a new instance is created per `route_task()` call, metadata is fetched once per user query. If a collection is added mid-query, it will not appear until the next query. Accepted: collections change rarely.
- Embedding model change invalidates all existing centroids. `archon doctor` will warn. Use `archon rag sync` to re-ingest all collections, then `archon rag collection reindex <name>` for each affected collection. A bulk `--all` reindex flag is out of scope here.
- RAG routing applies only to task-intent queries (those going through `route_task()`). Chat-classified queries (`intent='chat'`) bypass `route_task()` and receive no RAG context. This is intentional: chat responses are conversational and generally do not require document retrieval.
- The default `routing_confidence_threshold = 0.30` is calibrated for `BAAI/bge-small-en-v1.5` cosine similarity against collection centroids. For different embedding models, this value may need tuning: centroids of heterogeneous collections tend to produce lower similarities (~0.1–0.3 for unrelated queries); focused collections can produce higher similarities (0.5+). If RAG is consistently being skipped even for relevant queries, lower this threshold. If irrelevant collections are being searched, raise it.
- Pinned collections and `max_parallel_collections`: with the default `pinned_collections` (2 paths) and `max_parallel_collections = 3`, the decomposer can select at most 1 additional collection per query. Users with many domain-specific collections who want the decomposer to select 2–3 should raise `max_parallel_collections` to 4–5.
- Pinned collections must also be declared in `rag.collections`: a path in `pinned_collections` that is not in `rag.collections` will never be indexed (FEAT-021's sync ignores undeclared paths). At runtime the path resolves to a non-existent collection; the search silently returns no results. `archon doctor` warns about this misconfiguration. The defaults satisfy this automatically.

---

## Architecture

### New components

**`archon/rag/collection_meta.py`** (new file)
```python
@dataclass
class CollectionMeta:
    name: str
    description: str | None
    centroid: list[float] | None    # omitted in /collections list response
    doc_count: int
    chunk_count: int
    embedding_model: str
    last_indexed: datetime | None
    last_described: datetime | None
    described_at_doc_count: int | None  # doc_count at time of last description generation; used for ≥20% trigger; defaults to None for a new collection that has never been ingested; get_collection_meta() returns None for this field until the first successful description generation
```

**`archon/rag/router.py`** (new file)
```python
class MultiCollectionRouter:
    def __init__(self, rag_url: str, embedder: Embedder, shortlist_size: int, confidence_threshold: float) -> None: ...
    # Does NOT create its own Embedder — receives one from RagContextProvider
    # Uses httpx.AsyncClient to call the RAG MCP server JSON-RPC endpoint directly:
    # POST {rag_url}  Content-Type: application/json
    # {"jsonrpc":"2.0","method":"tools/call","params":{"name":"get_collections_meta","arguments":{}},"id":1}

    async def fetch_metadata(self) -> list[CollectionMeta]:
        """Fetch full metadata (with centroids) via get_collections_meta() JSON-RPC call to the RAG server URL
        (httpx.AsyncClient POST). Result cached in _cached_metadata for the instance lifetime.
        Populated on first select() call. 10-second timeout; returns [] on timeout
        and logs a debug-level warning. When [] is returned, select() returns [] and RAG is skipped."""

    def rank(self, query_embedding: list[float], collections: list[CollectionMeta]) -> list[CollectionMeta]:
        """Return shortlist sorted by cosine similarity. Returns [] if max_sim < threshold (and at least one
        centroid is non-None). If ALL centroids are None, bypass confidence gate and return all (up to shortlist_size).

        Before computing cosine similarity, filters out any collection whose CollectionMeta.embedding_model
        differs from cfg.rag.embedding_model. Mismatched collections are treated as if centroid=None
        (placed last in ranking, subject to confidence gate behavior). This provides query-time protection
        against stale centroids computed with a different embedding model. archon doctor still warns about
        mismatches so the user can run `reindex` to fix them permanently."""

    async def select(self, query: str) -> list[CollectionMeta]:
        """Embed query locally via provided Embedder instance, rank, return shortlist.
        Entry point for RagContextProvider. MultiCollectionRouter is instantiated fresh per route_task() call."""
```

`MultiCollectionRouter` receives a `rag_url: str` and an `Embedder` instance from `RagContextProvider` — it does NOT instantiate its own embedder. `RagContextProvider` creates ONE `Embedder` at its own instantiation time and passes it to each `MultiCollectionRouter` it creates, so the fastembed model is loaded once per process. `MultiCollectionRouter` uses `httpx.AsyncClient` to POST JSON-RPC requests directly to the RAG server URL — the same URL used by other modules to communicate with the RAG server. `RagContextProvider` similarly calls `search()` via JSON-RPC POST (`httpx.AsyncClient`) for each selected collection.

**`archon/rag/description_generator.py`** (new file)
```python
async def generate_description(chunks: list[str], collection_name: str) -> str | None:
    """Sample up to 20 chunks, call Haiku via ClaudeSDKClient, return 2-3 sentence description.

    Session lifecycle: creates a new ClaudeSDKClient with DEFAULT_FAST_MODEL (Haiku),
    permission_mode="bypassPermissions", and the project's working directory.
    Sends a single query, reads response, then disconnects.
    A 30-second timeout wraps the entire connect/query/receive/disconnect lifecycle;
    if exceeded, returns None. On any error, returns None without raising.
    """
```

### Modified components

**`archon/rag/store.py`**
- `get_collection_meta(name: str) -> CollectionMeta` — reads stored centroid + description from the `_archon_collection_meta` table
- `update_collection_meta(name: str, meta: CollectionMeta) -> None` — writes centroid + description after ingest
- `list_collections()` must filter out tables matching the `^_archon_` prefix so `_archon_collection_meta` does not appear in results as a spurious collection

**`archon/rag/pipeline.py`**
- `ingest_directory()` — after embedding all chunks: compute centroid, check description regeneration trigger, call `generate_description()` async, call `store.update_collection_meta()`
- Progress callback: `on_progress(files_done: int, files_total: int, chunks_done: int)` — called per file during ingest; CLI passes a printing callback

**`archon/rag/server.py`**
```python
@app.tool()
async def get_collections_meta() -> list[CollectionMeta]:
    """Returns all CollectionMeta WITH centroids included — bulk endpoint used by MultiCollectionRouter.fetch_metadata()."""

@app.tool()
async def get_collection_meta(name: str) -> CollectionMeta:
    """Returns full CollectionMeta including centroid for a single collection. Raises ValueError on unknown name (for CLI info command)."""
```
Existing `list_collections()` MCP tool is extended to return `list[CollectionMeta]` with centroid omitted (for listing/display). The RAG server uses FastMCP (`@app.tool()` decorators, JSON-RPC 2.0) — not FastAPI REST endpoints.

**`archon/ai/decomposer.py`**
- `TaskOutput` gains a new field: `selected_collections: list[str] | None = None`
- `route_task()` is responsible for parsing `<rag_selected_collections>` from the router's Response content in `_parse_task_output()`; `_parse_task_output()` performs a simple XML extract + comma-split + strip-whitespace — NO filtering against the shortlist; raw parsed names are stored in `task_output.selected_collections`
- `route_task()` signature: `route_task(prompt: str, rag_pre_context: str | None = None)`
- `rag_pre_context` is appended to the instruction string AFTER `load_prompt("route_task")` content and BEFORE the final instruction is sent to the router session (positioned at the END of the instruction block)

**`archon/ai/rag_context_provider.py`** (new file)
- The existing `ContextProvider` Protocol in `archon/ai/context_provider.py` is NOT modified. A new **`RagContextProvider`** class is created in `archon/ai/rag_context_provider.py`. This is NOT a `ContextProvider` implementor — it is a standalone orchestrator called from `Pipeline.send()`.
- `RagContextProvider.__init__` creates ONE `Embedder` instance; `Pipeline` creates one `RagContextProvider` instance and reuses it across calls (fastembed model loaded once per process). Instance variables: `_last_shortlist_names: list[str] | None = None` — populated by `get_pre_context()` with the shortlist names from the current query's centroid pre-ranking; consumed by `search_and_prepare()` to filter out hallucinated collection names. `_pinned_names: list[str] = []` — populated by `get_pre_context()` via `path_to_collection_name()` on each path in `cfg.rag.pinned_collections`; excludes paths with no matching entry in fetched metadata.
- Two-phase protocol:

  **Phase A — Collection selection (router session)**: `get_pre_context(query)` embeds query locally, resolves `pinned_collections` to `_pinned_names`, splits fetched metadata into `pinned_meta` and `routable_meta`, and calls `MultiCollectionRouter.rank()` on `routable_meta` only to get the centroid pre-ranked shortlist; returns a `<rag_collections>` block (built from `routable_meta` shortlist only) passed to `route_task(prompt, rag_pre_context=...)`. This block goes into the ROUTER session prompt (not the main session). The router LLM outputs `<rag_selected_collections>` tags as part of its routing decision; `route_task()` parses these tags in `_parse_task_output()` and stores the result in `task_output.selected_collections` — these tags are never visible to users (router Response content is already suppressed from Telegram).

  **Phase B — RAG injection (main session)**: after `route_task()` returns, `search_and_prepare(task_output, query) -> tuple[str, int] | None` prepends `_pinned_names` to `to_search`, caps decomposer-selected additions at `max_parallel_collections - len(_pinned_names)` (minimum 0), filters `task_output.selected_collections` against `self._last_shortlist_names` to discard hallucinations, and searches all collections in parallel using `asyncio.gather(*..., return_exceptions=True)` (per-collection exceptions caught and logged at debug; if all fail, returns `None`), and returns `(merged_text, chunk_count)` (or `None` if no results). It does NOT call `inject_context()` internally. `Pipeline.send()` unpacks `rag_text, chunk_count = result` and calls `self._session.inject_context(rag_text, injection_type="rag_retrieval", detail=f"{chunk_count} chunks from {', '.join(actual_searched_names)}")` where `actual_searched_names` are the post-filter names actually searched (includes both pinned and router-selected).

- Router context block format (sent to ROUTER session, includes output instruction):
  ```
  <rag_collections>
  Available collections (select 1–3 most relevant for this query, output their names in
  <rag_selected_collections>name1, name2</rag_selected_collections> tags at the end of your routing decision):
  - sessions: Daily conversation history and session logs
  - workspace: Current project files and documentation
  - contracts: Legal agreements and employment documents
  </rag_collections>
  ```
- Parallel RAG searches use `asyncio.Semaphore(cfg.rag.max_parallel_collections)` to bound concurrency:
  ```python
  semaphore = asyncio.Semaphore(max_parallel)
  async def bounded_search(col):
      async with semaphore:
          return await rag_search(query, col)
  results = await asyncio.gather(*[bounded_search(c) for c in selected])
  ```
- Score normalisation via module-level `_normalize_and_merge(per_collection_results: dict[str, list[SearchResult]]) -> list[SearchResult]`: `normalized = (score - min_score) / (max_score - min_score)`; if `max_score == min_score` (collection returns exactly 1 result or all results have identical scores), assign `normalized = 0.5` (mid-range neutral) for all results from that collection — this prevents single-result collections from automatically dominating the merged ranking; merge all, sort descending, take top `cfg.rag.top_k_return`
- Response parsing (performed by `route_task()` in `_parse_task_output()`, stored in `task_output.selected_collections`): extract content between `<rag_selected_collections>` tags; when tag appears multiple times, use only the first; missing closing tag → zero selections → `selected_collections=None`; split by comma and strip whitespace (`.strip()` to handle all whitespace variants including newlines); store raw names — NO filtering against the shortlist at this stage. `RagContextProvider.search_and_prepare()` reads `task_output.selected_collections` directly; if `None`, returns `None` immediately; filters the raw names against `self._last_shortlist_names` to discard hallucinated/typo names; empty strings are discarded (cannot match any collection name); if zero valid names remain, returns `None` (RAG skipped); caps at 3 in shortlist-rank order (maximum of 3 collections searched, even if more valid matches exist). Collection names must not contain commas — enforced by FEAT-021's `path_to_collection_name()` sanitization.
- `search_and_prepare(task_output, query) -> tuple[str, int] | None`: returns `(merged_text, chunk_count)` where `chunk_count` is the number of individual chunks in the merged result, or `None` if no results; does NOT call `inject_context()` internally. `Pipeline.send()` unpacks the result as `rag_text, chunk_count = result` and calls `self._session.inject_context(rag_text, injection_type="rag_retrieval", detail=f"{chunk_count} chunks from {', '.join(actual_searched_names)}")` where `actual_searched_names` are the post-filter names actually searched (not the raw `task_output.selected_collections`).

**`archon/config/loader.py`**
- `RagConfig` gains: `max_parallel_collections: int = 3`, `routing_confidence_threshold: float = 0.30`, `routing_shortlist_size: int = 8`
- `pinned_collections: list[str] = ['~/.archon/history/sessions', '~/.archon/workspace']` — list of collection paths always searched, bypassing confidence gate and decomposer selection
- `routing_confidence_threshold`: the default `0.30` is calibrated for `BAAI/bge-small-en-v1.5` cosine similarity against collection centroids. For different embedding models, this value may need tuning. Centroids of heterogeneous collections tend to produce lower similarities (~0.1–0.3 for unrelated queries); focused collections can produce higher similarities (0.5+). If RAG is consistently being skipped for relevant queries, lower this threshold. If irrelevant collections are being searched, raise it. This note should also be reflected in the `routing_confidence_threshold` config example in Task 6.1 (`examples/config.toml.example`).

**`archon/cli/rag_cmd.py`**
- `_run_collection_info(args)` — fetches and prints `CollectionMeta` for one collection
- `_run_collection_reindex(args)` — calls `pipeline.ingest_directory()` with forced description regeneration; prints progress (files, chunks)
- `_run_collection_add()` — adds progress callback to existing ingest call
- `_run_sync()` — adds per-collection progress output

**`archon/cli/doctor.py`**
- Adds RAG health check section: calls `get_collections_meta()` via JSON-RPC POST (`httpx.AsyncClient`) to the RAG server URL; if the HTTP call fails (connection refused, timeout), emits `"RAG server is not running — RAG health checks skipped"` and returns without performing collection checks (no direct LanceDB access); when reachable, checks staleness, model mismatch, empty collections, missing centroids; also checks `rag.pinned_collections` against `rag.collections`; warns for paths present in pinned but absent from collections (config-only, no RAG server required)

### Data flow

```
User message → Pipeline.send(user_message)
  │
  ├─ Phase A: RagContextProvider.get_pre_context(query)
  │    ├─ MultiCollectionRouter.fetch_metadata()             ← get_collections_meta() MCP tool call (10s timeout)
  │    ├─ Split metadata: pinned_meta vs routable_meta
  │    ├─ Tier 1 (routable ≤ 3) → return None               ← skip decomposer; Phase B searches all routable + pinned
  │    ├─ Tier 2/3 (routable ≥ 4):
  │    │    ├─ embed_query locally via shared Embedder       ← fastembed, no HTTP call
  │    │    ├─ Tier 3 only: rank(query_embedding, routable)  ← cosine sim, confidence gate, shortlist cap
  │    │    └─ return <rag_collections> block (routable shortlist only)
  │    └─ if shortlist empty (gate failed) → return None     ← pinned still searched in Phase B
  │
  ├─ (Tier 2/3 only) async for item in Decomposer.route_task(prompt, rag_pre_context=<rag_collections block>):
  │    ├─ Router session prompt includes collection shortlist + instruction to output
  │    │   <rag_selected_collections> tags at end of routing decision
  │    ├─ route_task() parses <rag_selected_collections> in _parse_task_output()
  │    │   → stores in task_output.selected_collections (raw names, no filtering yet)
  │    └─ router Response content suppressed from Telegram (shown as "Routing decision")
  │
  ├─ Phase B: rag_text = await RagContextProvider.search_and_prepare(task_output, query)
  │    ├─ Tier 1: to_search = _pinned_names + all routable_meta names (capped at max_parallel)
  │    ├─ Tier 2/3: to_search = _pinned_names + decomposer_selected[:max_parallel - len(_pinned_names)]
  │    ├─ Tier 2/3: Filter task_output.selected_collections against _last_shortlist_names → top 3 (capped)
  │    ├─ asyncio.Semaphore(max_parallel_collections) + asyncio.gather(
  │    │       *[search(collection=name) MCP tool for name in to_search])
  │    │    └─ at most max_parallel_collections concurrent requests
  │    ├─ _normalize_and_merge(per_collection_results)       ← normalise scores per-collection, sort, top-K
  │    └─ returns merged chunk text as str | None  (does NOT call inject_context internally)
  │
  ├─ if result is not None (rag_text, chunk_count = result):
  │    └─ self._session.inject_context(rag_text, "rag_retrieval", detail=f"{chunk_count} chunks from {', '.join(actual_searched_names)}")
  │         └─ → ContextInjectedEvent (FEAT-018 pipeline)
  │
  └─ session.send(task_output.prompt)   ← main session receives injected RAG context
```

### Centroid storage

Stored in a LanceDB `_archon_collection_meta` table (one row per collection). The `^_archon_` prefix prevents this table from appearing in `list_collections()` results as a spurious collection.

| column | type | notes |
|--------|------|-------|
| `name` | `str` | collection name (PK) |
| `description` | `str \| None` | Haiku-generated |
| `centroid` | `list[float]` | full mean of all chunk embeddings from current ingest batch |
| `doc_count` | `int` | at last ingest |
| `chunk_count` | `int` | at last ingest |
| `embedding_model` | `str` | model used for centroid |
| `last_indexed` | `str \| None` | ISO-8601; deserialized to `datetime \| None` when read |
| `last_described` | `str \| None` | ISO-8601; deserialized to `datetime \| None` when read |
| `described_at_doc_count` | `int \| None` | doc_count at time of last description generation; used for ≥20% change trigger |

> **Note**: The store serializes `datetime` fields to/from ISO-8601 strings when reading/writing the `_archon_collection_meta` table. The `CollectionMeta` dataclass uses `datetime | None` for both `last_indexed` and `last_described`.

---

## Task Breakdown

### Phase 1 — Collection metadata infrastructure
> **Releasable**: after Task 1.3; ingest stores centroid + description; server exposes metadata endpoints

#### Task 1.1 — `CollectionMeta` dataclass and `_archon_collection_meta` table in `RagStore`
- [ ] **Files**: `archon/rag/collection_meta.py` (new), `archon/rag/store.py`
- **Depends on**: nothing
- **Requires FEAT-021 complete**: multi-collection `RagStore`, `ensure_collection()`, `list_collections()` must exist
- **Description**: `CollectionMeta` dataclass (including `described_at_doc_count`, which defaults to `None` for a new collection that has never been ingested — `get_collection_meta()` returns `None` for this field until the first successful description generation); `RagStore.get_collection_meta()`, `update_collection_meta()`, `_archon_collection_meta` LanceDB table (upsert by name); `list_collections()` filters out `^_archon_` prefixed tables
- **Tests** — `tests/rag/test_store.py`: `test_collection_meta_upsert`, `test_collection_meta_get_missing_returns_none`, `test_collection_meta_upsert_includes_described_at_doc_count`, `test_list_collections_excludes_archon_prefix`

#### Task 1.2 — Centroid computation in `RagPipeline.ingest_directory()`
- [ ] **File**: `archon/rag/pipeline.py`
- **Depends on**: Task 1.1
- **Requires FEAT-021 complete**: multi-collection `RagStore`, `ensure_collection()`, `list_collections()` must exist
- **Description**: after embedding all chunks in the current ingest batch, compute `mean(embeddings)` as centroid (fresh computation from the current batch only, not incremental); call `store.update_collection_meta()` with updated centroid, doc_count, chunk_count, embedding_model, last_indexed. Note: for `archon rag collection reindex <name>`, all documents are re-ingested so the centroid reflects the full collection. For `archon rag collection add <path>`, only the current batch's embeddings are included; use `reindex` for a full centroid recomputation.
- **Tests** — `tests/rag/test_pipeline.py`: `test_ingest_computes_centroid_from_all_chunks`, `test_ingest_centroid_replaced_on_reingest`

#### Task 1.3 — Haiku description generation on ingest
- [ ] **Files**: `archon/rag/description_generator.py` (new), `archon/rag/pipeline.py`
- **Depends on**: Task 1.1
- **Requires FEAT-021 complete**: multi-collection `RagStore`, `ensure_collection()`, `list_collections()` must exist
- **Description**: `generate_description(chunks, name)` samples up to 20 chunks, creates a new `ClaudeSDKClient` session (DEFAULT_FAST_MODEL / Haiku, `permission_mode="bypassPermissions"`), sends a single query, reads response, disconnects; wrapped in a 30-second timeout — if exceeded, returns `None`; if `chunk_count == 0` or `chunks` list is empty, skip description generation immediately and return `None` without any Haiku call; `ingest_directory()` calls it async; regenerates when `abs(current_doc_count - described_at_doc_count) / described_at_doc_count >= 0.20`; if `described_at_doc_count` is `None` or `0`, always regenerate — EXCEPT when `chunk_count == 0`; failure → description stays `None`, no error
- **Acceptance criteria**: description generation has a 30-second timeout; after generation, `described_at_doc_count` is set to the current `doc_count`; if `chunk_count == 0`, description generation is skipped without error
- **Tests** — `tests/rag/test_description_generator.py`: `test_generate_description_calls_haiku`, `test_generate_description_on_failure_returns_none`, `test_generate_description_timeout_returns_none`, `test_regeneration_trigger_at_20pct_change`, `test_no_regeneration_below_threshold`, `test_regeneration_trigger_when_described_at_doc_count_is_none`, `test_generate_description_returns_none_on_empty_chunks`

#### Task 1.4 — `get_collections_meta()` and `get_collection_meta(name)` MCP tools
- [ ] **File**: `archon/rag/server.py`
- **Depends on**: Task 1.1
- **Requires FEAT-021 complete**: multi-collection `RagStore`, `ensure_collection()`, `list_collections()` must exist
- **Description**: two new `@app.tool()` decorated MCP tools (JSON-RPC 2.0 via FastMCP); `get_collections_meta()` returns all collections WITH centroids (bulk, used by `MultiCollectionRouter.fetch_metadata()`); `get_collection_meta(name: str)` returns full `CollectionMeta` including centroid for one collection, raises `ValueError` on unknown name; existing `list_collections()` MCP tool extended to return `CollectionMeta` with centroid omitted
- **Tests** — `tests/rag/test_server.py`: `test_collections_endpoint_returns_list`, `test_collections_endpoint_omits_centroid`, `test_collections_meta_bulk_endpoint_returns_centroids`, `test_collection_meta_endpoint_includes_centroid`, `test_collection_meta_unknown_name_raises_error`

### Phase 2 — Multi-collection router
> **Releasable**: after Task 2.2; routing logic available for context_provider integration

#### Task 2.1 — `MultiCollectionRouter` with centroid pre-ranking
- [ ] **File**: `archon/rag/router.py` (new)
- **Depends on**: Task 1.4
- **Description**: `fetch_metadata()` (cached) — has a 10-second timeout; if exceeded, returns `[]` and logs a debug-level warning; when `fetch_metadata()` returns `[]`, `select()` returns `[]` and RAG is skipped for this query; `rank(query_embedding, collections)` with cosine similarity + confidence gate + shortlist cap; `select(query)` entry point
- **Tests** — `tests/rag/test_router.py`: `test_rank_returns_sorted_by_similarity`, `test_rank_confidence_gate_returns_empty`, `test_rank_none_centroid_placed_last`, `test_rank_shortlist_size_cap`, `test_tier2_skips_centroid_preranking` — 4–8 routable collections: assert centroid pre-ranking not called, all passed to block; `test_tier1_skips_decomposer_searches_all` — ≤ 3 routable collections: assert `get_pre_context()` returns `None` (no block built); `test_router_fetch_metadata_timeout_returns_empty`, `test_rank_skips_collections_with_mismatched_embedding_model` — asserts that collections whose `embedding_model` differs from the configured model are treated as `centroid=None` and placed last in the ranking (not used for similarity scoring)

#### Task 2.2 — Config additions for routing parameters
- [ ] **File**: `archon/config/loader.py`
- **Depends on**: nothing
- **Description**: add `max_parallel_collections: int = 3`, `routing_confidence_threshold: float = 0.30`, `routing_shortlist_size: int = 8`, `pinned_collections: list[str] = ["~/.archon/history/sessions", "~/.archon/workspace"]` to `RagConfig`
- **Tests** — `tests/config/test_rag_config.py`: `test_routing_defaults`, `test_routing_config_parsed_from_toml`, `test_pinned_collections_default`, `test_pinned_collections_parsed_from_toml`

### Phase 3 — Decomposer integration
> **Releasable**: after Task 3.1; Archon automatically selects and searches collections on every query

#### Task 3.1 — `RagContextProvider` multi-collection retrieval
- [ ] **Files**: `archon/ai/rag_context_provider.py` (new), `archon/ai/pipeline.py` (modified)
- **Depends on**: Task 2.1, Task 2.2
- **Description**: new `RagContextProvider` class in `archon/ai/rag_context_provider.py`; NOT a `ContextProvider` implementor — it is a standalone orchestrator called from `Pipeline.send()`. `RagContextProvider.__init__` creates ONE `Embedder` instance and passes it to each `MultiCollectionRouter` it creates (the embedder model is loaded once per process). `MultiCollectionRouter.__init__` takes an `embedder: Embedder` parameter — it does NOT create its own `Embedder`. `Pipeline` creates one `RagContextProvider` instance and reuses it across calls. Resolves `pinned_collections` paths to `_pinned_names`; splits metadata into pinned/routable before building shortlist; prepends pinned to `to_search` in `search_and_prepare()` with slot cap.

  Call chain in `Pipeline.send(user_message)`:
  ```python
  # Pipeline.send() updated pattern:
  pre_context = rag_provider.get_pre_context(query) if rag_provider else None
  task_output = None
  async for item in self._decomposer.route_task(prompt, rag_pre_context=pre_context):
      if isinstance(item, TaskOutput):
          task_output = item
      else:
          yield dataclasses.replace(item, source="router")
  # After the loop, task_output is available:
  if rag_provider and task_output:
      result = await rag_provider.search_and_prepare(task_output, query)  # -> tuple[str, int] | None
      if result is not None:
          rag_text, chunk_count = result
          actual_searched_names = ...  # post-filter names from search_and_prepare
          self._session.inject_context(rag_text, injection_type="rag_retrieval",
                                       detail=f"{chunk_count} chunks from {', '.join(actual_searched_names)}")
  async for event in self._session.send(task_output.prompt):
      yield event
  ```

  Score normalization uses module-level function `_normalize_and_merge(per_collection_results: dict[str, list[SearchResult]]) -> list[SearchResult]`: for each collection, compute `normalized = (score - min_score) / (max_score - min_score)` with fallback `normalized = 0.5` (mid-range neutral) if `max_score == min_score` (i.e., collection returns exactly 1 result or all results have identical scores) — this prevents single-result collections from automatically dominating the merged ranking; merge all normalized results, sort descending by normalized score, take top `cfg.rag.top_k_return`.

  Parsing rules: `_parse_task_output()` extracts content between `<rag_selected_collections>` tags; splits by comma and strips whitespace (`.strip()` to handle all whitespace variants including newlines); stores raw names in `task_output.selected_collections` — NO filtering at this stage; empty strings are discarded (cannot match any collection name); when the tag appears multiple times, uses only the first occurrence; missing closing tag → `selected_collections=None` → RAG skipped; empty tag → zero valid names → RAG skipped. `search_and_prepare()` then filters raw names against `self._last_shortlist_names`, discards hallucinated/typo names, caps at 3 in shortlist-rank order (maximum of 3 collections, enforced even if more valid matches exist), and uses post-filter names as `actual_searched_names` for the `inject_context()` detail. Collection names must not contain commas — this constraint is enforced by FEAT-021's `path_to_collection_name()` sanitization.

  If router returns empty or RAG server unreachable → skip silently with debug-level log.
- **Tests** — `tests/ai/test_rag_context_provider.py`:
  - `test_rag_selects_correct_collections`
  - `test_rag_skips_on_empty_shortlist`
  - `test_rag_skips_on_server_error`
  - `test_rag_parallel_search_bounded` — assert that with 5 selected collections and `max_parallel=2`, the semaphore ensures at most 2 concurrent searches
  - `test_rag_inject_context_called`
  - `test_rag_parsing_filters_hallucinated_names`
  - `test_rag_parsing_handles_extra_whitespace` — explicitly covers newline-separated names (`.strip()` handles all whitespace variants including newlines)
  - `test_rag_skips_when_parsing_yields_zero_collections`
  - `test_rag_parsing_multiple_tags_uses_first` — when `<rag_selected_collections>` appears multiple times, use only the first
  - `test_rag_parsing_unclosed_tag_skips_rag` — missing closing tag → zero selections → RAG skipped
  - `test_rag_parsing_empty_tag_skips_rag` — empty tag produces one empty string after comma-split; empty string is discarded during name-validation; zero valid names → RAG skipped
  - `test_rag_partial_search_failure_uses_remaining_results` — with one collection raising an exception and one succeeding, assert that `search_and_prepare()` returns results from the successful collection only
  - `test_tier1_skips_decomposer_searches_all_routable` — when `routable_meta` count ≤ 3, `RagContextProvider` skips the decomposer and passes all routable collections directly to `search_and_prepare()` alongside pinned
  - `test_search_and_prepare_caps_at_3_collections` — even if `_last_shortlist_names` contains more than 3 valid matches, `search_and_prepare()` caps at 3 collections
  - `test_score_normalization_single_result` — calls `rag_context_provider._normalize_and_merge()` directly; asserts that a collection with exactly 1 result gets `normalized_score = 0.5`
  - `test_score_normalization_identical_scores` — calls `rag_context_provider._normalize_and_merge()` directly; asserts that a collection where all results have identical scores gets `normalized_score = 0.5`
  - `test_pinned_collections_bypass_confidence_gate`
  - `test_pinned_collections_excluded_from_decomposer_block`
  - `test_pinned_only_search_when_router_selects_zero`
  - `test_pinned_and_selected_merged`
  - `test_pinned_counts_toward_max_parallel`
  - `test_pinned_empty_list_routes_normally`
  - `test_pinned_unknown_path_silently_skipped`
  - `test_actual_searched_names_includes_pinned`

#### Task 3.2 — Telegram visibility for RAG injection
- [ ] **Files**: `archon/chat/handler.py`, `archon/ai/event_mapper.py` (FEAT-018 constants)
- **Depends on**: Task 3.1
- **Description**: `ContextInjectedEvent` with `injection_type="rag_retrieval"` and `detail=f"{chunk_count} chunks from {', '.join(actual_searched_names)}"` where `actual_searched_names` are the post-filter names actually searched; format in `handler.py`: `🔍 RAG: {chunk_count} chunks from {col1}, {col2}` in verbose/debug (chunk_count and collection names parsed from the detail field), silent otherwise; history entry via existing `event_renderer.py`
- **Tests** — `tests/chat/test_handler.py`: `test_rag_injection_visible_in_verbose` (asserts the full `"RAG: N chunks from <col1>, <col2>"` format), `test_rag_injection_silent_in_quiet`

#### Task 3.3 — Integration test: full RAG routing data flow
- [ ] **File**: `tests/integration/test_rag_routing.py` (new)
- **Depends on**: Task 3.1, Task 3.2
- **Description**: end-to-end test that mocks only the HTTP boundary (RAG server). Creates a real `RagContextProvider` with a real `MultiCollectionRouter`; verifies the full chain: query → embed locally → rank → decomposer context block construction → parse selection → parallel search (mocked HTTP responses) → merge scores → `inject_context` called with correct `injection_type` and `detail`
- **Tests**: `test_full_rag_routing_chain`, `test_full_rag_routing_graceful_degradation`

### Phase 4 — CLI additions
> **Releasable**: after Task 4.3; full CLI support for metadata inspection, reindex, progress, dry-run

#### Task 4.1 — `archon rag collection info` and `archon rag collection reindex`
- [ ] **File**: `archon/cli/rag_cmd.py`
- **Depends on**: Task 1.4
- **Description**: `info` fetches and prints `CollectionMeta`; `reindex` calls `ingest_directory()` with `force_regenerate_description=True`, re-ingests ALL documents unconditionally, recomputes centroid, and guarantees centroid and description reflect the full current state of the collection. Distinction from `sync`: `archon rag sync` only re-ingests changed files and regenerates descriptions only when the 20% change threshold is met; `reindex` bypasses all thresholds and always performs a full rebuild. Use `reindex` after changing `cfg.rag.embedding_model`, suspecting a corrupted centroid, or forcing a fresh description.
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
- **Tests** — `tests/cli/test_rag_cmd.py`: `test_collection_remove_dry_run_prints_without_executing`, `test_collection_remove_dry_run_and_force_flags_are_mutually_exclusive`

### Phase 5 — Doctor integration
> **Releasable**: after Task 5.1

#### Task 5.1 — RAG collection health checks in `archon doctor`
- [ ] **File**: `archon/cli/doctor.py`
- **Depends on**: Task 1.4
- **Description**: calls `get_collections_meta()` via JSON-RPC POST (`httpx.AsyncClient`) to the RAG server URL; if the HTTP call fails (connection refused, timeout), the doctor emits: `"RAG server is not running — RAG health checks skipped"` and returns without performing any collection checks (no direct LanceDB access); when the server is reachable, checks staleness (>7 days), embedding model mismatch, empty collections, and missing centroid; prints one warning line per issue; also checks `rag.pinned_collections` against `rag.collections`; warns for paths present in pinned but absent from collections (config-only, no RAG server required)
- **Tests** — `tests/cli/test_doctor.py`: `test_doctor_warns_stale_collection`, `test_doctor_warns_model_mismatch`, `test_doctor_warns_empty_collection`, `test_doctor_warns_missing_centroid`, `test_doctor_no_warnings_on_healthy_collections`, `test_doctor_skips_rag_checks_when_server_down`, `test_doctor_warns_pinned_not_in_collections`, `test_doctor_pinned_check_runs_when_server_down`

### Phase 6 — Documentation
#### Task 6.1 — Update docs
- [ ] **Files**: `Documentation/UserManual/rag_guide.md`, `examples/config.toml.example`, `Documentation/Architecture/180_rag_architecture.md`, `CLAUDE.md`
- **Depends on**: Task 4.3
- **Description**: document routing config keys, decomposer context block format, `info`/`reindex` commands, doctor checks; add `max_parallel_collections`, `routing_confidence_threshold`, `routing_shortlist_size` to config example; `examples/config.toml.example` — add annotated `pinned_collections` entry under `[rag]`
