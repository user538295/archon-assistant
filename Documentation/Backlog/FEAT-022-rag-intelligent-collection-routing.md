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

1. Every decomposer query automatically identifies and searches the 2–3 most relevant collections.
2. Resource usage is bounded: at most `max_parallel_collections` concurrent RAG searches at any time; a confidence gate skips RAG entirely when no collection is a strong match.
3. Each collection has an auto-generated description (produced by Haiku during ingest) that the decomposer uses for selection reasoning.
4. Each collection stores an embedding centroid used for the fast pre-ranking step before the decomposer is involved.
5. The feature degrades gracefully: if RAG is disabled or no collection matches, the session continues without RAG context.

---

## Scope

### In Scope
- `CollectionMeta` dataclass — name, path, description, centroid, doc_count, chunk_count, embedding_model
- `GET /collections` RAG server endpoint — returns all `CollectionMeta` entries (without centroid vectors, for brevity)
- `GET /collections/<name>/meta` RAG server endpoint — returns full metadata including centroid
- Centroid computation during ingest: mean of all chunk embeddings, stored in collection metadata
- Haiku-generated collection description: 2–3 sentence summary from a sample of chunks, generated async during ingest
- Description re-generation trigger: doc_count changes by ≥20% since last generation
- Centroid incremental update: running mean on each ingest (no full recompute)
- `MultiCollectionRouter` — pre-ranks collections by cosine similarity between query embedding and collection centroids, returns shortlist
- `context_provider.py` updated: calls `MultiCollectionRouter`, passes shortlist descriptions to decomposer context, executes batch-parallel searches, injects results via `inject_context()`
- `rag.max_parallel_collections` config key (default `3`)
- `rag.routing_confidence_threshold` config key (default `0.30`): if best centroid similarity < threshold, skip RAG
- `rag.routing_shortlist_size` config key (default `8`): max collections passed to decomposer for reasoning
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
- [ ] `CollectionMeta` dataclass: `name`, `description`, `centroid: list[float] | None`, `doc_count`, `chunk_count`, `embedding_model`, `last_indexed`, `last_described`
- [ ] During ingest, the chunk centroid (mean of all chunk embeddings as `list[float]`) is computed and stored in LanceDB collection metadata
- [ ] Centroid updates incrementally: each new ingest batch updates the running mean without full recompute
- [ ] During ingest, a Haiku call samples up to 20 representative chunks and generates a 2–3 sentence description stored alongside the centroid
- [ ] Description regenerates automatically when `doc_count` changes by ≥20% since `last_described`
- [ ] If Haiku call fails during ingest, description is `None`; ingest completes without error

### RAG server endpoints
- [ ] `GET /collections` returns `list[CollectionMeta]` (centroid omitted for size)
- [ ] `GET /collections/<name>/meta` returns full `CollectionMeta` including centroid
- [ ] Both endpoints return 200 with empty list when no collections exist
- [ ] Existing `POST /search` and `POST /ingest` endpoints unchanged

### Multi-collection routing
- [ ] `MultiCollectionRouter` is initialised with the RAG server URL; fetches full metadata (with centroids) once per decomposer session and caches for that session
- [ ] `MultiCollectionRouter.rank(query_embedding) -> list[CollectionMeta]`: returns collections sorted descending by cosine similarity between query embedding and centroid; collections with `centroid=None` are placed last
- [ ] If `max(similarity_scores) < rag.routing_confidence_threshold`, routing returns empty list (RAG skipped)
- [ ] At most `rag.routing_shortlist_size` collections are passed to the decomposer for reasoning
- [ ] If total collections ≤ `rag.routing_shortlist_size`, centroid pre-ranking is skipped and all are passed directly

### Decomposer integration
- [ ] `context_provider.py` fetches collection shortlist + descriptions before each decomposer `route_task()` call
- [ ] Decomposer context includes a structured block: collection names + descriptions for the shortlist, with instruction to select the 1–3 most relevant
- [ ] Decomposer's selected collection names are parsed from its response and used for actual search
- [ ] Selected collections are searched in parallel via `asyncio.gather`; at most `rag.max_parallel_collections` concurrent requests
- [ ] Results from each collection are merged and re-ranked by score (normalised within each collection before merging)
- [ ] Top `rag.top_k_return` merged results are injected via `inject_context(text, injection_type="rag_retrieval", detail=collection_name)`
- [ ] If decomposer selects zero collections (or all have low confidence), nothing is injected
- [ ] If RAG server is unreachable, routing is skipped silently with a debug-level log entry; session continues

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
- [ ] Decomposer integration tested with mocked router and RAG server: assert `inject_context` called with correct type and detail
- [ ] RAG server endpoint tests: `GET /collections`, `GET /collections/<name>/meta`
- [ ] Centroid computation tested: mean of known embeddings, incremental update, regeneration trigger
- [ ] Doctor tests: assert each warning condition triggers the correct message

---

## What Does NOT Change

- `RagPipeline.search()` — still takes a single `collection` parameter; multi-collection orchestration is above this layer
- `POST /search` endpoint — unchanged; `MultiCollectionRouter` calls it once per selected collection
- `BAAI/bge-reranker-v2-m3` reranker — still used per-collection; cross-collection merge uses score normalisation only
- `RagCollectionSync` — unchanged; FEAT-022 only adds metadata fields computed during ingest
- Classifier — unmodified; collection routing is entirely the decomposer's concern
- `ContextReminder` injection path — RAG uses the existing `inject_context()` queue

---

## Known Limitations / Accepted Trade-offs

- Centroid pre-ranking degrades for heterogeneous collections (mixed-topic content averages to a noisy centroid). Mitigated by the confidence gate: low-confidence centroids are deprioritised but collections with `centroid=None` are always included in the shortlist as fallback candidates.
- Decomposer collection selection adds one LLM reasoning step per query (Haiku-class model preferred for speed). For ≤3 collections, this step is skipped and all are searched directly.
- Cross-collection score normalisation is approximate (per-collection min/max normalisation). A cross-encoder reranker would be more accurate but is deferred (FEAT-020 scope).
- Description generation costs one Haiku call per collection at ingest time. For a fresh install with 2 default collections, this is ~2 API calls. Acceptable.
- `MultiCollectionRouter` caches collection metadata per decomposer session. If a collection is added mid-session, it will not appear until the next session. Accepted: collections change rarely relative to session lifetime.
- Embedding model change invalidates all existing centroids. `archon doctor` will warn; `archon rag collection reindex --all` is required. A future migration command is out of scope here.

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
```

**`archon/rag/router.py`** (new file)
```python
class MultiCollectionRouter:
    def __init__(self, rag_base_url: str, shortlist_size: int, confidence_threshold: float) -> None: ...

    async def fetch_metadata(self) -> list[CollectionMeta]:
        """Fetch full metadata (with centroids) from GET /collections/meta. Cached per instance."""

    def rank(self, query_embedding: list[float], collections: list[CollectionMeta]) -> list[CollectionMeta]:
        """Return shortlist sorted by cosine similarity. Returns [] if max_sim < threshold."""

    async def select(self, query: str) -> list[CollectionMeta]:
        """Embed query, rank, return shortlist. Entry point for context_provider."""
```

**`archon/rag/description_generator.py`** (new file)
```python
async def generate_description(chunks: list[str], collection_name: str) -> str | None:
    """Sample up to 20 chunks, call Haiku via ClaudeSDKClient, return 2-3 sentence description."""
```

### Modified components

**`archon/rag/store.py`**
- `get_collection_meta(name: str) -> CollectionMeta` — reads stored centroid + description from a metadata table alongside each collection
- `update_collection_meta(name: str, meta: CollectionMeta) -> None` — writes centroid + description after ingest

**`archon/rag/pipeline.py`**
- `ingest_directory()` — after embedding all chunks: compute centroid, check description regeneration trigger, call `generate_description()` async, call `store.update_collection_meta()`
- Progress callback: `on_progress(files_done: int, files_total: int, chunks_done: int)` — called per file during ingest; CLI passes a printing callback

**`archon/rag/server.py`**
- `GET /collections` → returns `list[CollectionMeta]` (centroid omitted)
- `GET /collections/{name}/meta` → returns full `CollectionMeta` including centroid

**`archon/ai/context_provider.py`**
- `RagContextProvider` implementation updated: call `MultiCollectionRouter.select(query)` → decomposer reasoning → `asyncio.gather(*[rag_search(q, col) for col in selected])` → merge → `inject_context()`
- Decomposer context block format:
  ```
  <rag_collections>
  Available collections (select 1–3 most relevant for this query):
  - sessions: Daily conversation history and session logs
  - workspace: Current project files and documentation
  - contracts: Legal agreements and employment documents
  </rag_collections>
  ```

**`archon/config/loader.py`**
- `RagConfig` gains: `max_parallel_collections: int = 3`, `routing_confidence_threshold: float = 0.30`, `routing_shortlist_size: int = 8`

**`archon/cli/rag_cmd.py`**
- `_run_collection_info(args)` — fetches and prints `CollectionMeta` for one collection
- `_run_collection_reindex(args)` — calls `pipeline.ingest_directory()` with forced description regeneration; prints progress
- `_run_collection_add()` — adds progress callback to existing ingest call
- `_run_sync()` — adds per-collection progress output

**`archon/cli/doctor.py`**
- Adds RAG health check section: calls `GET /collections`, checks staleness, model mismatch, empty collections, missing centroids

### Data flow

```
User message → Decomposer.route_task(query)
  │
  ├─ MultiCollectionRouter.select(query)
  │    ├─ embed(query)                              ← RAG server POST /embed (or local)
  │    ├─ fetch_metadata() → list[CollectionMeta]  ← GET /collections (with centroids)
  │    ├─ rank(query_embedding, collections)        ← cosine sim, confidence gate
  │    ├─ if shortlist empty → return None          ← RAG skipped
  │    └─ return shortlist (≤routing_shortlist_size)
  │
  ├─ Decomposer receives shortlist descriptions in context block
  │    └─ Selects 1–3 collection names
  │
  ├─ asyncio.gather(*[POST /search?collection=name for name in selected])
  │    └─ max_parallel_collections concurrent requests
  │
  ├─ merge_results(per_collection_results)          ← normalise scores, deduplicate, top-K
  │
  └─ inject_context(chunks_text, "rag_retrieval", collection_name)
       └─ → ContextInjectedEvent (FEAT-018 pipeline)
```

### Centroid storage

Stored in a LanceDB `_meta` table alongside each collection (one row per collection):

| column | type | notes |
|--------|------|-------|
| `name` | `str` | collection name (PK) |
| `description` | `str \| None` | Haiku-generated |
| `centroid` | `list[float]` | mean of chunk embeddings |
| `doc_count` | `int` | at last ingest |
| `chunk_count` | `int` | at last ingest |
| `embedding_model` | `str` | model used for centroid |
| `last_indexed` | `str` | ISO-8601 |
| `last_described` | `str \| None` | ISO-8601 |

---

## Task Breakdown

### Phase 1 — Collection metadata infrastructure
> **Releasable**: after Task 1.3; ingest stores centroid + description; server exposes metadata endpoints

#### Task 1.1 — `CollectionMeta` dataclass and `_meta` table in `RagStore`
- [ ] **Files**: `archon/rag/collection_meta.py` (new), `archon/rag/store.py`
- **Depends on**: nothing
- **Description**: `CollectionMeta` dataclass; `RagStore.get_collection_meta()`, `update_collection_meta()`, `_meta` LanceDB table (upsert by name)
- **Tests** — `tests/rag/test_store.py`: `test_collection_meta_upsert`, `test_collection_meta_get_missing_returns_none`

#### Task 1.2 — Centroid computation and incremental update in `RagPipeline.ingest_directory()`
- [ ] **File**: `archon/rag/pipeline.py`
- **Depends on**: Task 1.1
- **Description**: after embedding all chunks, compute `mean(embeddings)` as centroid; call `store.update_collection_meta()` with updated centroid, doc_count, chunk_count, embedding_model, last_indexed; running mean update on incremental ingest
- **Tests** — `tests/rag/test_pipeline.py`: `test_ingest_computes_centroid`, `test_ingest_updates_centroid_incrementally`

#### Task 1.3 — Haiku description generation on ingest
- [ ] **Files**: `archon/rag/description_generator.py` (new), `archon/rag/pipeline.py`
- **Depends on**: Task 1.1
- **Description**: `generate_description(chunks, name)` samples up to 20 chunks, calls Haiku via `ClaudeSDKClient`, returns description string; `ingest_directory()` calls it async; regenerates when doc_count change ≥20%; failure → description stays `None`, no error
- **Tests** — `tests/rag/test_description_generator.py`: `test_generate_description_calls_haiku`, `test_generate_description_on_failure_returns_none`, `test_regeneration_trigger_at_20pct_change`, `test_no_regeneration_below_threshold`

#### Task 1.4 — `GET /collections` and `GET /collections/{name}/meta` server endpoints
- [ ] **File**: `archon/rag/server.py`
- **Depends on**: Task 1.1
- **Description**: two new FastAPI routes returning `CollectionMeta`; `/collections` omits centroid; `/{name}/meta` includes centroid; 404 on unknown name
- **Tests** — `tests/rag/test_server.py`: `test_collections_endpoint_returns_list`, `test_collections_endpoint_omits_centroid`, `test_collection_meta_endpoint_includes_centroid`, `test_collection_meta_endpoint_404`

### Phase 2 — Multi-collection router
> **Releasable**: after Task 2.2; routing logic available for context_provider integration

#### Task 2.1 — `MultiCollectionRouter` with centroid pre-ranking
- [ ] **File**: `archon/rag/router.py` (new)
- **Depends on**: Task 1.4
- **Description**: `fetch_metadata()` (cached), `rank(query_embedding, collections)` with cosine similarity + confidence gate + shortlist cap, `select(query)` entry point
- **Tests** — `tests/rag/test_router.py`: `test_rank_returns_sorted_by_similarity`, `test_rank_confidence_gate_returns_empty`, `test_rank_none_centroid_placed_last`, `test_rank_shortlist_size_cap`, `test_small_collection_set_skips_preranking`

#### Task 2.2 — Config additions for routing parameters
- [ ] **File**: `archon/config/loader.py`
- **Depends on**: nothing
- **Description**: add `max_parallel_collections: int = 3`, `routing_confidence_threshold: float = 0.30`, `routing_shortlist_size: int = 8` to `RagConfig`
- **Tests** — `tests/config/test_rag_config.py`: `test_routing_defaults`, `test_routing_config_parsed_from_toml`

### Phase 3 — Decomposer integration
> **Releasable**: after Task 3.1; Archon automatically selects and searches collections on every query

#### Task 3.1 — `context_provider.py` multi-collection retrieval
- [ ] **File**: `archon/ai/context_provider.py`
- **Depends on**: Task 2.1, Task 2.2
- **Description**: call `MultiCollectionRouter.select(query)` → build decomposer context block with descriptions → parse selected collection names from decomposer context → `asyncio.gather` searches (bounded by `max_parallel_collections`) → merge + normalise scores → inject top-K via `inject_context("rag_retrieval")`; if router returns empty or RAG server unreachable → skip silently
- **Tests** — `tests/ai/test_context_provider.py`: `test_rag_selects_correct_collections`, `test_rag_skips_on_empty_shortlist`, `test_rag_skips_on_server_error`, `test_rag_parallel_search_bounded`, `test_rag_inject_context_called`

#### Task 3.2 — Telegram visibility for RAG injection
- [ ] **Files**: `archon/chat/handler.py`, `archon/ai/event_mapper.py` (FEAT-018 constants)
- **Depends on**: Task 3.1
- **Description**: `ContextInjectedEvent` with `injection_type="rag_retrieval"` and `detail=collection_name`; format in `handler.py`: `🔍 RAG: N chunks from <collection>` in verbose/debug, silent otherwise; history entry via existing `event_renderer.py`
- **Tests** — `tests/chat/test_handler.py`: `test_rag_injection_visible_in_verbose`, `test_rag_injection_silent_in_quiet`

### Phase 4 — CLI additions
> **Releasable**: after Task 4.3; full CLI support for metadata inspection, reindex, progress, dry-run

#### Task 4.1 — `archon rag collection info` and `archon rag collection reindex`
- [ ] **File**: `archon/cli/rag_cmd.py`
- **Depends on**: Task 1.4
- **Description**: `info` fetches and prints `CollectionMeta`; `reindex` calls `ingest_directory()` with force-regenerate flag, prints progress
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
- **Description**: calls `GET /collections` (or opens store directly if RAG server is stopped); checks staleness, model mismatch, empty collections, missing centroid; prints one warning line per issue
- **Tests** — `tests/cli/test_doctor.py`: `test_doctor_warns_stale_collection`, `test_doctor_warns_model_mismatch`, `test_doctor_warns_empty_collection`, `test_doctor_warns_missing_centroid`, `test_doctor_no_warnings_on_healthy_collections`

### Phase 6 — Documentation
#### Task 6.1 — Update docs
- [ ] **Files**: `Documentation/UserManual/rag_guide.md`, `examples/config.toml.example`, `Documentation/Architecture/180_rag_architecture.md`, `CLAUDE.md`
- **Depends on**: Task 4.3
- **Description**: document routing config keys, decomposer context block format, `info`/`reindex` commands, doctor checks; add `max_parallel_collections`, `routing_confidence_threshold`, `routing_shortlist_size` to config example
