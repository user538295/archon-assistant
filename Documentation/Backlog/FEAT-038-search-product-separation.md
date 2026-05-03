# FEAT-038 — Search Product Separation (P0 Items 1–3)
**Purpose**: Extract `archon/search/` into a standalone Search package with its own config, CLI, and zero cross-imports with Archon. This backlog item assumes a prior ADR ratifies the monorepo layout at `packages/archon-search/`.
**Audience**: Maintainers performing extraction; operators wanting to run Search independently.
**Status**: To Do

---

## Background

Search is a separate process but not a separate product. It cannot bootstrap without Archon's config loader; service management lives inside `archon/platform/`; and Archon's client-side routing imports Search internals directly (`Embedder`, `MultiCollectionRouter`). The coupling is bidirectional (6 Search→Archon imports, 5 Archon→Search import clusters including 10 MCP tools). Neither side can version, release, or operate independently until all couplings are severed.

See: `Documentation/Backlog/search-product-separation-brief.md` for the authoritative coupling inventory and baseline decisions. This backlog item turns that brief into an execution plan and makes any runtime/CLI clarifications explicit.

## Goal

`archon-search start` runs with zero imports from `archon.*`. Archon's runtime search integration uses HTTP/MCP only; local operator/lifecycle wrappers may call the standalone `archon-search` CLI without importing Search internals. Import-boundary lint tests enforce this in CI permanently. All 13 steps from the brief result in green CI.

## Preconditions

- Before Phase 1 starts, record the packaging decision in an ADR. This document assumes the ADR chooses the monorepo path `packages/archon-search/`.
- The HTTP routes in this document are an internal Archon↔Search integration boundary for the extraction phase, not a public stable API. Auth, namespace isolation, and public API stabilization remain later backlog items.

---

## Scope

### In Scope
- `packages/archon-search/` with own `pyproject.toml`, package name `archon-search`, CalVer versioning (after the ADR records the monorepo choice)
- Move `archon/search/` → `packages/archon-search/archon_search/`
- `archon-search.toml` at `~/.archon/archon-search.toml` — standalone config with own schema/loader
- `archon-search` CLI preserves the current operator-facing Search verbs and safety flags: `install`, `uninstall`, `start`, `stop`, `status`, `ingest`, `sync`, `collection ...`, `config show/get/set`
- Extract `LaunchdSearchService`, `SystemdSearchService`, `WindowsSearchService` from `archon/platform/` into `archon-search` — implementing `archon-search`-internal lifecycle interface (NOT Archon's `PlatformService` ABC)
- Canonical domain types in `archon-search`: `Query`, `Result`, `Collection`, `Document`, `Chunk`, `Namespace`, `IngestJob`, `ReindexJob`, `DeleteJob`
- First-class async job model: `IngestJob` with `job_id`, `status`, persistence, cancel support
- Internal REST HTTP control plane: `POST /route`, `POST /ingest`, `GET /jobs/{id}`, `DELETE /jobs/{id}`, `GET /health`, `GET /status`, `GET /indexing-state`, `GET/POST/DELETE /collections/*`
- `POST /route` returns the client-visible routing state needed to preserve the current two-phase provider flow (`pre_context`, `pinned_names`, `routable_names`, `decomposer_invoked`); embedding remains server-side; it does NOT return chunks or constitute a stable external API
- `archon/ai/search_client.py` — sole designated HTTP client adapter (only file in `archon/` allowed to import `archon_search.*`)
- Rewrite `search_context_provider.py` to call `POST /route` for pre-context plus routing state, then FastMCP for chunks after `route_task()`
- Rewrite all 10 MCP tools in `archon_toolkit_search.py` to cross the package boundary without importing Search internals: REST for health/jobs/collections/routing-related operations, standalone `archon-search` CLI or service wrappers for local lifecycle operations
- Move `IndexingNotificationMonitor` to `archon/gateway/`; it calls `GET /indexing-state` every 30s
- Update `archon/cli/doctor.py` to call `GET /health` + `GET /indexing-state` — no Search internals
- Update `archon/cli/search_cmd.py` to use `SearchClient` and/or delegate to `archon-search` CLI — no Search internals
- Shrink Archon's `[search]` config to client-side fields only: `url`, `enabled`, `max_parallel_collections`, `top_k_return` (+ optional credentials placeholder)
- Metadata schema additions: system fields, filterable `metadata: dict[str, str]`, `custom_score`, audit fields
- Import-boundary lint tests enforced in CI (both sides)
- All existing search tests migrated to `packages/archon-search/tests/`; Archon-side tests mock HTTP boundary
- Data directory migration guide (step 13)
- HTTP server defaults to `127.0.0.1` (not `0.0.0.0`)
- `description_generator.py` moves to `archon-search` with own `claude-agent-sdk` dependency; `_get_env_lock` inlined
- Routing latency benchmark (p50/p95) measured and documented in PR; p95 ≤ 150ms target
- Job persistence: `~/.archon/archon-search-jobs.json`, atomic write, 7-day eviction, crash recovery
- Search remains optional and disabled by default in Archon; disabled mode still degrades gracefully

### Out of Scope
- Auth and namespace isolation (P0 item 5)
- Evaluation harness (P0 item 4)
- REST API with OpenAPI spec (P0 item 6)
- Metadata filter support at query time (P1 item 7)
- Any P1+ retrieval features (HyDE, RAG Fusion, routing improvements)
- PyPI publication (version scheme established; publication deferred)

---

## Acceptance criteria
- [ ] `archon-search start` succeeds on macOS and Linux with a minimal `archon-search.toml`
- [ ] `archon-search` preserves the current operator-facing lifecycle and collection-management verbs without importing `archon.*`
- [ ] `uv run pytest tests/` (Archon-side) passes with Search internals mocked at HTTP boundary
- [ ] `uv run pytest packages/archon-search/tests/` passes (all existing search tests migrated)
- [ ] Import boundary lint passes: no `from archon.` in `packages/archon-search/archon_search/`; no `archon.search.*` anywhere in `archon/` or `tests/`; no `archon_search.*` in `archon/` or `tests/` except `archon/ai/search_client.py`
- [ ] `archon doctor` reports Search health via HTTP — no internal imports
- [ ] All 10 MCP tools in `archon_toolkit_search.py` cross the package boundary without `archon.search.*` imports; REST-backed tools and local lifecycle wrappers are explicitly separated and tested
- [ ] `search_context_provider.py` preserves the current two-phase routing flow via `POST /route` plus FastMCP — no direct `Embedder`/`MultiCollectionRouter` imports
- [ ] `IndexingNotificationMonitor` in `archon/gateway/` calls `GET /indexing-state`; existing Telegram notification behavior preserved
- [ ] HTTP failure mode tests pass: connection refused, timeout, 5xx, malformed JSON → graceful degrade
- [ ] `search.enabled = false` preserves current graceful degradation: Search tools stay registered with a clear disabled response; `archon doctor` shows disabled without HTTP calls; `IndexingNotificationMonitor` does not start; `search_context_provider.py` returns empty context
- [ ] Gateway/Search startup semantics are explicit and tested: Archon treats Search as an optional dependency at `search.url` and does not rely on `archon.search.*` probes or service-state imports
- [ ] A benchmark harness exists for routing latency, and the implementation records p50/p95 before merge
- [ ] Migration guide (step 13) includes a deterministic smoke-test checklist for a clean-install user
- [ ] `description_generator` tests pass under `packages/archon-search/tests/`

---

## What does NOT change
- FastMCP JSON-RPC search-query path (chunk retrieval) — retained as-is, not replaced by REST
- LanceDB tables, manifest files, indexing state files in existing user data directories
- Existing document/chunk identity semantics: `doc_id` remains the current SHA-256 path hash and `chunk_id` remains `"{doc_id}-{idx:06d}"`
- Telegram notification behavior for indexing state (same trigger logic, now via HTTP poll)
- `archon/platform/` ABCs (`PlatformService`, `PlatformRuntime`) — remain Archon-only
- Archon's `config.toml` retains a `[search]` section for client-side behavior (`url`, `enabled`, `max_parallel_collections`, `top_k_return`), and Search remains disabled by default

## Deliberate behavior changes

- Gateway lifecycle ownership changes: after extraction, Archon treats Search as an optional HTTP dependency at `search.url` rather than auto-starting it via `archon.search.*` internals. Local start/stop remains available through the standalone `archon-search` CLI and any thin wrappers that call it.
- `archon doctor` keeps coarse Search availability and indexing-state checks only. Detailed Search-internal diagnostics that depended on local config and LanceDB internals move to `archon-search` operator commands rather than staying in Archon.

---

## Known limitations / accepted trade-offs
- **Breaking change**: MCP tools now return `IngestJob` instead of synchronous results. Callers must be updated.
- **Partial ingest on cancel**: `CANCELLED` jobs record which `doc_id`s were indexed before cancellation; partially-written LanceDB rows are NOT rolled back and are immediately visible in search.
- **Soft type coupling**: `archon/ai/search_client.py` may import `archon_search.*` domain types (path dependency in monorepo). Accepted during monorepo phase; removed if Search moves to separate repo.
- **Migration is manual**: Existing users create `~/.archon/archon-search.toml` manually; old `[search]` internal keys in Archon's `config.toml` are ignored with a deprecation warning. No automated migration command.
- **Strangler-fig within steps**: Steps with circular dependencies (source move breaks importers; HTTP endpoint must exist before callers migrate) use introduce→migrate→remove across multiple commits within a step. CI must be green at step completion, not at every intermediate commit.

---

## Architecture

### New package: `packages/archon-search/`
```
packages/archon-search/
├── pyproject.toml              # package: archon-search, CalVer YY.M.N
├── archon_search/
│   __init__.py
│   types.py                   # Canonical domain types
│   constants.py               # DEFAULT_FAST_MODEL, DEFAULT_MODEL
│   config.py                  # SearchConfig + load_config(path) from archon-search.toml
│   platform/
│   │   __init__.py
│   │   types.py               # GpuType (extracted from archon.platform.types)
│   │   runtime.py             # find_binary(), get_runtime() — minimal dup
│   │   service.py             # SearchServiceLifecycle ABC (archon-search-internal)
│   │   macos.py               # LaunchdSearchService
│   │   linux.py               # SystemdSearchService
│   │   windows.py             # WindowsSearchService (stubs)
│   server/
│   │   __init__.py
│   │   app.py                 # FastAPI app factory
│   │   routes_health.py       # GET /health
│   │   routes_state.py        # GET /indexing-state
│   │   routes_route.py        # POST /route
│   │   routes_jobs.py         # POST /ingest, GET/DELETE /jobs/{id}
│   │   routes_collections.py  # GET/POST/DELETE /collections/*
│   jobs/
│   │   __init__.py
│   │   model.py               # IngestJob, JobStatus enum, job dataclasses
│   │   store.py               # JobStore — persist/load/evict/update
│   cli/
│       __init__.py
│       main.py                # archon-search entry point
│       start.py               # start subcommand
│       stop.py                # stop subcommand
│       status.py              # status subcommand
│       install_cmd.py         # install subcommand
│       config_cmd.py          # config show/get/set subcommands
│   (plus moved: chunker, embedder, pipeline, progress, reranker, router, store, sync,
│                watcher, parser, collection_meta, description_generator, _types)
├── tests/
│   __init__.py
│   test_import_boundary.py    # lint: no from archon. in archon_search/
│   test_config.py
│   test_types.py
│   test_job_store.py
│   test_routes_health.py
│   test_routes_state.py
│   test_routes_route.py
│   test_routes_jobs.py
│   test_routes_collections.py
│   (plus migrated search tests from archon/tests/)
```

### Modified Archon files
- `archon/ai/search_client.py` — **new** HTTP client adapter (only file allowed to import `archon_search.*`)
- `archon/ai/search_context_provider.py` — `POST /route` + FastMCP (no Embedder/MultiCollectionRouter)
- `archon/ai/archon_toolkit_search.py` — 10 tools as HTTP proxy calls
- `archon/gateway/gateway.py` — `IndexingNotificationMonitor` (HTTP-based, moved from search)
- `archon/gateway/notification_monitor.py` — **new** (moved + adapted from `archon/search/notification_monitor.py`)
- `archon/cli/doctor.py` — calls HTTP endpoints
- `archon/cli/search_cmd.py` — uses HTTP client
- `archon/cli/update.py` — update/uninstall flows hand off Search lifecycle to the standalone `archon-search` CLI and clean up legacy service definitions
- `archon/config/` — `[search]` schema trimmed to client-side fields only: `url`, `enabled`, `max_parallel_collections`, `top_k_return`
- `pyproject.toml` — add path dependency: `archon-search = { path = "packages/archon-search" }`
- `tests/test_import_boundary.py` — **new** Archon-side lint

### Key interfaces

```python
# archon_search/types.py
@dataclass
class Query: text: str; slots: int | None = None

@dataclass
class RouteResponse:
    pre_context: str | None
    pinned_names: list[str]
    routable_names: list[str]
    decomposer_invoked: bool

@dataclass
class IngestJob:
    job_id: str          # UUIDv4
    status: JobStatus    # PENDING | RUNNING | DONE | FAILED | CANCELLED | CANCELLING
    created_at: str      # ISO-8601 UTC
    result: dict | None = None

class JobStatus(str, Enum):
    PENDING = "PENDING"; RUNNING = "RUNNING"; DONE = "DONE"
    FAILED = "FAILED"; CANCELLED = "CANCELLED"; CANCELLING = "CANCELLING"

# archon_search/config.py
@dataclass
class SearchConfig:
    host: str = "127.0.0.1"; port: int = 8765
    db_path: str = "~/.archon/search"
    embedding_model: str = "..."; reranker_model: str = "..."
    log_level: str = "INFO"; log_file: str = "~/.archon/logs/archon-search.log"
    ...

def load_config(path: Path | None = None) -> SearchConfig: ...

# archon/ai/search_client.py
class SearchClient:
    def __init__(self, base_url: str, timeout: float = 10.0): ...
    async def route(self, query: str, slots: int | None = None) -> RouteResponse: ...
    async def status(self) -> dict: ...
    async def health(self) -> dict: ...
    async def indexing_state(self) -> dict: ...
    async def ingest(self, ...) -> IngestJob: ...
    async def get_job(self, job_id: str) -> IngestJob: ...
    async def cancel_job(self, job_id: str) -> IngestJob: ...
    async def list_collections(self) -> list[dict]: ...  # rich collection summaries
    async def add_collection(self, path: str) -> dict: ...  # collection identity + initial ingest job
    async def remove_collection(self, name: str) -> dict: ...  # removal result
    async def collection_info(self, name: str) -> dict: ...  # rich collection detail
    async def reindex_collection(self, name: str) -> IngestJob: ...
```

### Config
- `archon-search.toml` at `~/.archon/archon-search.toml` — all server-side fields
- Archon's `config.toml` `[search]` retains client-side fields only: `url` (str, default `"http://127.0.0.1:8765"`), `enabled` (bool, default `false`), `max_parallel_collections` (int, default `3`), `top_k_return` (int, default `5`)
- Old `[search]` internal keys (collections, db_path, embedding_model, etc.) emit deprecation warning if present

---

## Tests

- **test_package_scaffold** (unit): `archon-search` pyproject.toml has correct name, version, entry points
- **test_domain_types** (unit): all domain type dataclasses instantiate and serialize correctly
- **test_job_status_enum** (unit): all 6 status values present
- **test_config_load_defaults** (unit): `load_config()` returns defaults when no file exists
- **test_config_load_file** (unit): values from `archon-search.toml` override defaults
- **test_config_invalid_value_raises** (unit): invalid config values raise `ConfigError`
- **test_gpu_type_extracted** (unit): `GpuType` importable from `archon_search.platform.types`
- **test_find_binary** (unit): `find_binary()` returns path when binary exists
- **test_service_lifecycle_abc** (unit): `SearchServiceLifecycle` ABC has start/stop/status methods
- **test_launchd_search_service_start** (unit): macOS service start emits correct launchctl command
- **test_launchd_search_service_stop** (unit): macOS service stop emits correct command
- **test_launchd_search_service_status** (unit): status returns running/stopped
- **test_systemd_search_service** (unit): Linux service lifecycle analogues
- **test_job_store_create** (unit): `JobStore.create()` returns `IngestJob` with UUIDv4 job_id
- **test_job_store_update** (unit): status transitions update correctly
- **test_job_store_persist** (unit): jobs written atomically via temp-file rename
- **test_job_store_load_after_restart** (unit): jobs survive write→load cycle
- **test_job_store_crash_recovery** (unit): RUNNING/CANCELLING jobs transition to FAILED on load
- **test_job_store_corrupt_file** (unit): corrupt JSON resets to empty store without crash
- **test_job_store_eviction** (unit): jobs older than 7 days are evicted on startup
- **test_job_store_cancel_idempotency** (unit): cancel on DONE/FAILED returns 200; RUNNING → 202; unknown → 404
- **test_health_endpoint** (unit/integration): `GET /health` returns 200 with `{"status": "running"}`
- **test_status_endpoint** (unit/integration): `GET /status` returns rich service + collection status
- **test_indexing_state_endpoint** (unit/integration): `GET /indexing-state` returns per-collection state
- **test_route_endpoint** (unit/integration): `POST /route` with query returns `pre_context`, `pinned_names`, `routable_names`, and `decomposer_invoked`
- **test_route_endpoint_slots_override** (unit): `slots` param overrides server `routing_shortlist_size`
- **test_route_endpoint_pinned_included** (unit): server-configured pinned collections are always represented in the returned routing state
- **test_route_endpoint_confidence_gate_filters_low_similarity** (unit): low-confidence collections are excluded unless pinned
- **test_route_endpoint_centroid_none_bypasses_confidence_gate** (unit): legacy collections with no centroids still route
- **test_route_endpoint_preserves_router_ordering** (unit): returned routable-name order matches router ranking
- **test_ingest_endpoint_returns_job** (unit/integration): `POST /ingest` returns `IngestJob`
- **test_get_job_endpoint** (unit): `GET /jobs/{id}` returns current status
- **test_delete_job_done_idempotent** (unit): `DELETE /jobs/{id}` on DONE → 200
- **test_delete_job_running_initiates_cancel** (unit): `DELETE /jobs/{id}` on RUNNING → 202
- **test_delete_job_unknown** (unit): `DELETE /jobs/{id}` on unknown → 404
- **test_collections_list** (unit/integration): `GET /collections/` returns list
- **test_collections_add** (unit/integration): `POST /collections/` persists config and starts initial ingest
- **test_collections_remove** (unit/integration): `DELETE /collections/{name}` removes config and LanceDB data safely
- **test_collection_detail_endpoint** (unit/integration): `GET /collections/{name}` returns collection detail fields used by current operator workflows
- **test_description_generator_no_api_key** (unit): falls back to collection path as description
- **test_description_generator_api_failure** (unit): graceful fallback on API error
- **test_search_client_route** (unit): `SearchClient.route()` calls `POST /route` with correct payload and parses routing-state fields
- **test_search_client_status** (unit): `SearchClient.status()` calls `GET /status`
- **test_search_client_health** (unit): `SearchClient.health()` calls `GET /health`
- **test_search_client_connection_refused** (unit): raises gracefully → returns empty/None
- **test_search_client_timeout** (unit): 10s timeout → logs warning, returns empty context
- **test_search_client_5xx** (unit): logs error, returns empty context
- **test_search_client_malformed_json** (unit): logs error, returns empty context
- **test_search_context_provider_calls_route_then_fastmcp** (unit): two-call sequence verified
- **test_search_context_provider_preserves_pinned_names** (unit): returned `pinned_names` are always searched in phase B
- **test_search_context_provider_preserves_tier_state** (unit): returned routing state preserves Tier 1 vs Tier 2/3 behavior
- **test_search_context_provider_discards_hallucinated_names** (unit): decomposer-selected names filtered against returned `routable_names`
- **test_search_context_provider_respects_client_parallelism** (unit): `max_parallel_collections` and `top_k_return` still govern client-side fan-out/merge
- **test_search_context_provider_search_disabled** (unit): returns empty when `search.enabled=false`
- **test_toolkit_search_status_http** (unit): `search_status` tool calls `GET /status`
- **test_toolkit_search_ingest_http** (unit): `search_ingest` tool calls `POST /ingest`, returns `IngestJob`
- **test_toolkit_search_collection_list_http** (unit): calls `GET /collections/`
- **test_toolkit_search_collection_add_http** (unit): calls `POST /collections/`
- **test_toolkit_search_collection_remove_http** (unit): calls `DELETE /collections/{name}`
- **test_toolkit_search_collection_info_http** (unit): calls `GET /collections/{name}`
- **test_toolkit_search_collection_reindex_http** (unit): calls appropriate reindex endpoint
- **test_toolkit_search_start_wrapper** (unit): calls the standalone lifecycle wrapper, not `archon.search.*`
- **test_toolkit_search_stop_wrapper** (unit): calls the standalone lifecycle wrapper, not `archon.search.*`
- **test_toolkit_search_sync_wrapper** (unit): calls the standalone sync wrapper, not `archon.search.*`
- **test_notification_monitor_polls_http** (unit): `IndexingNotificationMonitor` calls `GET /indexing-state`
- **test_notification_monitor_server_not_running** (unit): connection refused → DEBUG log, no error spam
- **test_notification_monitor_terminal_state** (unit): all DONE/FAILED → Telegram summary sent
- **test_doctor_search_running** (unit): `archon doctor` calls `GET /health`, shows healthy
- **test_doctor_search_not_running** (unit): connection refused → "Search: not running", no crash
- **test_doctor_indexing_state_partial** (unit): IN_PROGRESS shows `⏳ partial (N/M files)`
- **test_doctor_indexing_state_failed** (unit): FAILED shows `❌`
- **test_collection_remove_safety_flags_preserved** (unit): `--dry-run`, `--force`, and pinned-only remove error semantics are preserved
- **test_import_boundary_archon_search_package** (unit): no `from archon.` in `archon_search/`
- **test_import_boundary_archon_package** (unit): no `archon.search.*` in `archon/` or `tests/`; no `archon_search.*` outside `search_client.py`
- **test_archon_search_cli_start** (integration): `archon-search start` spawns server process
- **test_archon_search_cli_status** (integration): `archon-search status` reports state
- **test_archon_config_deprecation_warning** (unit): old `[search]` keys emit deprecation warning
- **test_metadata_schema_system_fields** (unit): ingested chunk has `doc_id`, `chunk_id`, `source_path`, `indexed_at`, `file_type`, `language`
- **test_metadata_schema_filterable** (unit): `metadata: dict[str, str]` stored per chunk
- **test_metadata_schema_custom_score** (unit): `custom_score: float | None` stored
- **test_metadata_schema_audit_fields** (unit): `ingested_by`, `updated_at` set at ingest
- **test_watch_triggered_ingest_creates_job** (unit): watch-triggered ingest returns `IngestJob`

---

## Documentation update
- [ ] `Documentation/Architecture/180_search_architecture.md`, section: Architecture overview, path: `Documentation/Architecture/180_search_architecture.md` — update to reflect HTTP boundary, `packages/archon-search/` structure, new config path
- [ ] `Documentation/Architecture/110_component_catalog_and_layer_breakdown.md`, section: Search components, path: `Documentation/Architecture/110_component_catalog_and_layer_breakdown.md` — update component locations post-extraction
- [ ] `Documentation/UserManual/search_guide.md`, section: Search configuration and collection workflows, path: `Documentation/UserManual/search_guide.md` — update operator-facing config keys, flags, and collection semantics
- [ ] Migration guide, path: `Documentation/UserManual/search-migration-guide.md` — new file, step 13 from brief

---

## Task breakdown

### Phase 1 — Package scaffold + domain types
> **Releasable**: after Task 1.3 — `archon-search` installs as a Python package; domain types importable

#### Task 1.1 — Create `packages/archon-search/pyproject.toml`
- [x] **File**: `packages/archon-search/pyproject.toml`
- **Depends on**: nothing
- **Description**:
  - Package name: `archon-search`, version: `26.4.0` (CalVer `YY.M.N`), description: "Standalone Search server for Archon"
  - Entry point: `archon-search = "archon_search.cli.main:main"`
  - Dependencies: `fastapi`, `uvicorn[standard]`, `lancedb`, `fastembed`, `tomlkit`, `click`, `claude-agent-sdk`, `httpx`
  - Dev/test dependencies: `pytest`, `pytest-asyncio`, `httpx` (for TestClient), `pytest-cov`
  - Python ≥ 3.12
  - Create `packages/archon-search/archon_search/__init__.py` (empty)
  - Add path dependency to root `pyproject.toml`: `archon-search = { path = "packages/archon-search" }`
- **Releasable**: after this task, `uv sync` installs `archon-search` package
- **Tests (TDD)** — `packages/archon-search/tests/test_package_scaffold.py`:
  - Unit: `test_package_importable` — `import archon_search` succeeds after `uv sync`
  - Unit: `test_entry_point_defined` — pyproject.toml contains correct entry point
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_package_scaffold.py -v`

#### Task 1.2 — Canonical domain types
- [x] **File**: `packages/archon-search/archon_search/types.py`
- **Depends on**: Task 1.1
- **Description**:
  - `JobStatus(str, Enum)`: `PENDING`, `RUNNING`, `DONE`, `FAILED`, `CANCELLED`, `CANCELLING`
  - `@dataclass IngestJob`: `job_id: str`, `status: JobStatus`, `created_at: str`, `updated_at: str`, `result: dict | None = None`, `error: str | None = None`
  - `@dataclass ReindexJob(IngestJob)`: no additional fields (same lifecycle)
  - `@dataclass DeleteJob(IngestJob)`: `deleted_ids: list[str] = field(default_factory=list)`
  - `@dataclass Query`: `text: str`, `slots: int | None = None`
  - `@dataclass RouteResponse`: `pre_context: str | None`, `pinned_names: list[str]`, `routable_names: list[str]`, `decomposer_invoked: bool`
  - `@dataclass Collection`: `name: str`, `path: str`, `description: str`, `doc_count: int`, `chunk_count: int`, `status: str`, `watching: bool = False`
  - `@dataclass CollectionDetail(Collection)`: `embedding_model: str`, `centroid_present: bool`, `last_indexed: str | None = None`
  - `@dataclass Chunk`: `chunk_id: str`, `doc_id: str`, `text: str`, `source_path: str`, `collection: str`, `indexed_at: str`, `file_type: str`, `language: str | None`, `metadata: dict[str, str]`, `custom_score: float | None = None`, `ingested_by: str = "archon-search-cli"`, `updated_at: str = ""`
  - Preserve current identity semantics during extraction: `doc_id` remains the existing SHA-256 path hash and `chunk_id` remains `"{doc_id}-{idx:06d}"`; this work does not introduce UUID-based document identities
  - All dataclasses use `@dataclass(frozen=False)` and are JSON-serializable via `dataclasses.asdict()`
- **Releasable**: after this task, domain types are importable from `archon_search.types`
- **Tests (TDD)** — `packages/archon-search/tests/test_types.py`:
  - Unit: `test_job_status_all_values` — all 6 status values present
  - Unit: `test_ingest_job_instantiation` — default fields correct
  - Unit: `test_route_response_instantiation` — `pre_context`, `pinned_names`, `routable_names`, and `decomposer_invoked` fields round-trip
  - Unit: `test_chunk_metadata_default_empty_dict` — metadata defaults to `{}`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_types.py -v`

#### Task 1.3 — Standalone config loader
- [x] **File**: `packages/archon-search/archon_search/config.py`
- **Depends on**: Task 1.1
- **Description**:
  - `@dataclass SearchConfig` with sections (all fields have defaults):
    - `[server]`: `host: str = "127.0.0.1"`, `port: int = 8765`
    - `[database]`: `db_path: str = "~/.archon/search"`, `embedding_model: str = "BAAI/bge-small-en-v1.5"`, `reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"`, `chunk_size: int = 512`, `auto_reindex_on_chunk_size_change: bool = True`
    - `[routing]`: `routing_shortlist_size: int = 8`, `routing_confidence_threshold: float = 0.30`, `max_parallel_collections: int = 3`
    - `[collections]`: `pinned_collections: list[str] = field(default_factory=list)`, `watch: bool = False`
    - `[logging]`: `level: str = "INFO"`, `log_file: str = "~/.archon/logs/archon-search.log"`
  - `class ConfigError(Exception)`: raised on invalid values or type mismatches
  - `def load_config(path: Path | None = None) -> SearchConfig`: reads `~/.archon/archon-search.toml` by default; returns `SearchConfig` with defaults merged with file values; `path=None` → use default path; file missing → all defaults (no error)
  - Uses `tomlkit` for parsing (preserves comments on round-trips, consistent with Archon)
  - `def get_default_config_path() -> Path`: returns `Path.home() / ".archon" / "archon-search.toml"`
- **Releasable**: after this task, `load_config()` works standalone with no Archon imports
- **Tests (TDD)** — `packages/archon-search/tests/test_config.py`:
  - [x] Unit: `test_load_config_defaults_when_no_file` — returns defaults without error
  - [x] Unit: `test_load_config_from_file` — TOML overrides applied correctly
  - [x] Unit: `test_load_config_custom_path` — explicit `path=` argument honored
  - [x] Unit: `test_host_default_is_loopback` — `host == "127.0.0.1"`
  - [x] Unit: `test_db_path_tilde_preserved` — `db_path` stored as string, expansion is caller's responsibility
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_config.py -v`

---

### Phase 2 — Source move + Search→Archon import removal
> **Releasable**: after Task 2.4 — `archon-search` package contains all Search source; no `from archon.` imports remain in `archon_search/`

#### Task 2.1 — Move `archon/search/` source into `archon_search/`
- [x] **Files**: `packages/archon-search/archon_search/` (bulk move)
- **Depends on**: Task 1.1
- **Description**:
  - Copy (not delete yet) all `.py` files from `archon/search/` → `packages/archon-search/archon_search/`:
    `chunker.py`, `collection_meta.py`, `description_generator.py`, `embedder.py`, `install.py`, `notification_monitor.py`, `parser.py`, `pipeline.py`, `progress.py`, `reranker.py`, `router.py`, `server.py`, `store.py`, `sync.py`, `watcher.py`, `_types.py`
  - Update all intra-package imports (`from archon.search.X` → `from archon_search.X`) within the moved files
  - Do NOT yet remove from `archon/search/` — the old location stays until importers are updated (strangler-fig)
  - Do NOT yet fix cross-package imports (`from archon.ai...`, `from archon.config...`, etc.) — those are Tasks 2.2–2.4
  - CI may still fail at this step due to remaining cross imports — that is expected per the brief's migration sequence
- **Releasable**: N/A — intermediate step; CI will be partial until Task 2.4
- **Tests (TDD)** — `packages/archon-search/tests/test_module_move.py`:
  - [x] Unit: `test_chunker_importable_from_archon_search` — `from archon_search.chunker import Chunker` succeeds
  - [x] Unit: `test_store_importable_from_archon_search` — `from archon_search.store import SearchStore` succeeds
  - [x] Checkpoint: `cd packages/archon-search && uv run pytest tests/test_module_move.py -v`

#### Task 2.2 — Inline `_get_env_lock` and define `DEFAULT_FAST_MODEL` in `archon-search`
- [x] **Files**: `packages/archon-search/archon_search/constants.py`, `packages/archon-search/archon_search/description_generator.py`
- **Depends on**: Task 2.1
- **Description**:
  - `constants.py`:
    - `DEFAULT_FAST_MODEL: str = "claude-haiku-4-5-20251001"` — mirrors Archon's constant; defined independently
    - `DEFAULT_MODEL: str = "claude-sonnet-4-6"` — for description generation
  - `description_generator.py`:
    - Remove `from archon.ai.claude_session import _get_env_lock` — inline the helper:
      ```python
      _env_lock = asyncio.Lock()
      ```
    - Remove `from archon.ai.constants import DEFAULT_FAST_MODEL` — replace with `from archon_search.constants import DEFAULT_FAST_MODEL`
    - If `ANTHROPIC_API_KEY` is not set or generation fails: catch exception, log at DEBUG, return collection path as description — no crash
- **Releasable**: after this task, `description_generator.py` has zero `archon.*` imports
- **Tests (TDD)** — `packages/archon-search/tests/test_description_generator.py`:
  - [x] Unit: `test_no_api_key_falls_back_to_path` — `ANTHROPIC_API_KEY` unset → returns path string
  - [x] Unit: `test_api_failure_falls_back_to_path` — SDK raises exception → returns path string
  - [x] Unit: `test_constants_independent` — `from archon_search.constants import DEFAULT_FAST_MODEL` succeeds
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_description_generator.py -v`

#### Task 2.3 — Replace Console + extract GpuType + duplicate `get_runtime()`/`find_binary()`
- [x] **Files**: `packages/archon-search/archon_search/platform/types.py`, `packages/archon-search/archon_search/platform/runtime.py`, `packages/archon-search/archon_search/install.py`
- **Depends on**: Task 2.1
- **Description**:
  - `platform/types.py`:
    - `class GpuType(str, Enum): NONE = "none"; CUDA = "cuda"; METAL = "metal"` — extracted from `archon/platform/types.py`
  - `platform/runtime.py`:
    - `def find_binary(name: str, extra_paths: list[str] | None = None) -> Path | None`: minimal implementation — checks `PATH` + `extra_paths`; returns `None` if not found
    - `def get_runtime() -> SearchRuntime`: returns singleton `SearchRuntime` instance
    - `class SearchRuntime`: has `find_binary()` method delegating to module-level function
    - No imports from `archon.platform`
  - `install.py` (in `archon_search/`):
    - Remove `from archon.cli.console import Console` — replace all `Console.*` calls with `print()`
    - Remove `from archon.platform import get_search_service, get_runtime` — replace with `from archon_search.platform.runtime import get_runtime`
    - Remove `from archon.platform.types import GpuType` — replace with `from archon_search.platform.types import GpuType`
- **Releasable**: after this task, `install.py` has zero `archon.*` imports
- **Tests (TDD)** — `packages/archon-search/tests/test_platform.py`:
  - [x] Unit: `test_gpu_type_values` — `GpuType.NONE`, `GpuType.CUDA`, `GpuType.METAL` exist
  - [x] Unit: `test_find_binary_not_found` — unknown binary → returns `None`
  - [x] Unit: `test_find_binary_found` — existing binary (e.g. `python3`) → returns `Path`
  - [x] Checkpoint: `cd packages/archon-search && uv run pytest tests/test_platform.py -v`

#### Task 2.4 — Remove `notification_monitor.py` from `archon-search` scope
- [x] **File**: `packages/archon-search/archon_search/notification_monitor.py` (delete)
- **Depends on**: Task 2.1, Task 7.4 (the Archon-side replacement must exist before the moved copy is deleted)
- **Description**:
  - Delete `packages/archon-search/archon_search/notification_monitor.py` (the one moved from `archon/search/`)
  - The file imported `from archon.config.loader import NotificationsConfig` — removing it from `archon-search` severs this coupling
  - The replacement (`archon/gateway/notification_monitor.py`) is created in Task 7.4
  - After deletion, no file in `archon_search/` imports `archon.config.loader`
  - Update any `archon_search/__init__.py` exports that referenced `notification_monitor`
- **Releasable**: after this task, `archon-search` has zero imports of `archon.config.loader`
- **Tests (TDD)** — `packages/archon-search/tests/test_no_archon_imports.py` (partial — more added in Phase 8):
  - [x] Unit: `test_no_archon_config_imports` — `archon.config.loader` not imported anywhere in `archon_search/`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_no_archon_imports.py -v`

---

### Phase 3 — `archon-search`-internal service lifecycle + platform extraction
> **Releasable**: after Task 3.4 — `archon-search` manages its own service lifecycle without touching `archon/platform/`

#### Task 3.1 — Define `SearchServiceLifecycle` ABC
- [x] **File**: `packages/archon-search/archon_search/platform/service.py`
- **Depends on**: Task 1.1
- **Description**:
  - `class SearchServiceLifecycle(ABC)` — `archon-search`-internal interface; does NOT extend `archon.platform.PlatformService`
  - Abstract methods:
    - `def start(self) -> None`
    - `def stop(self) -> None`
    - `def restart(self) -> None`: default implementation calls `stop()` then `start()`
    - `def status(self) -> ServiceStatus`
    - `def register(self) -> None`
    - `def unregister(self) -> None`
  - `@dataclass ServiceStatus`: `running: bool`, `pid: int | None`, `uptime_seconds: float | None`
- **Releasable**: after this task, `SearchServiceLifecycle` ABC is importable
- **Tests (TDD)** — `packages/archon-search/tests/test_service_lifecycle.py`:
  - Unit: `test_abc_cannot_instantiate` — `SearchServiceLifecycle()` raises `TypeError`
  - Unit: `test_restart_default_calls_stop_then_start` — concrete subclass with mocked stop/start; `restart()` calls both
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_service_lifecycle.py -v`

#### Task 3.2 — Extract `LaunchdSearchService` (macOS)
- [x] **File**: `packages/archon-search/archon_search/platform/macos.py`
- **Depends on**: Task 3.1, Task 2.3
- **Description**:
  - `class LaunchdSearchService(SearchServiceLifecycle)` — extracted from `archon/platform/macos/search_service.py`
  - All logic moved verbatim; update imports to remove `archon.platform.*` references
  - Uses `archon_search.platform.runtime.get_runtime()` for binary discovery
  - `start()`: creates launchd plist, runs `launchctl load`
  - `stop()`: runs `launchctl unload`
  - `status()`: runs `launchctl list` to detect running state
  - `register()` / `unregister()`: install/remove plist file
  - Old `archon/platform/macos/search_service.py` is NOT deleted yet (consumers still use it until Phase 5)
- **Releasable**: after this task, macOS service management works via `archon-search` package
- **Tests (TDD)** — `packages/archon-search/tests/test_service_macos.py`:
  - [x] Unit: `test_start_calls_launchctl_load` — subprocess mocked; `start()` calls correct command
  - [x] Unit: `test_stop_calls_launchctl_unload` — subprocess mocked
  - [x] Unit: `test_status_returns_running_when_listed` — mocked `launchctl list` output
  - [x] Unit: `test_status_returns_stopped_when_not_listed` — empty `launchctl list`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_service_macos.py -v`

#### Task 3.3 — Extract `SystemdSearchService` (Linux)
- [x] **File**: `packages/archon-search/archon_search/platform/linux.py`
- **Depends on**: Task 3.1, Task 2.3
- **Description**:
  - `class SystemdSearchService(SearchServiceLifecycle)` — extracted from `archon/platform/linux/search_service.py`
  - All logic moved; update imports to remove `archon.platform.*` references
  - `start()`: runs `systemctl --user start archon-search`
  - `stop()`: runs `systemctl --user stop archon-search`
  - `status()`: runs `systemctl --user is-active archon-search`
  - `register()` / `unregister()`: install/remove systemd unit file
- **Releasable**: after this task, Linux service management works via `archon-search` package
- **Tests (TDD)** — `packages/archon-search/tests/test_service_linux.py`:
  - [x] Unit: `test_start_calls_systemctl_start` — subprocess mocked
  - [x] Unit: `test_stop_calls_systemctl_stop` — subprocess mocked
  - [x] Unit: `test_status_running` — `is-active` returns `active`
  - [x] Unit: `test_status_stopped` — `is-active` returns `inactive`
  - [x] Checkpoint: `cd packages/archon-search && uv run pytest tests/test_service_linux.py -v`

#### Task 3.4 — Extract `WindowsSearchService` (stubs)
- [x] **File**: `packages/archon-search/archon_search/platform/windows.py`
- **Depends on**: Task 3.1
- **Description**:
  - `class WindowsSearchService(SearchServiceLifecycle)` — stub implementations
  - All methods raise `NotImplementedError("Windows service management not yet supported — run archon-search start manually")`
  - `status()`: returns `ServiceStatus(running=False, pid=None, uptime_seconds=None)`
- **Releasable**: after this task, Windows import chain is satisfied
- **Tests (TDD)** — `packages/archon-search/tests/test_service_windows.py`:
  - [x] Unit: `test_start_raises_not_implemented` — `start()` raises `NotImplementedError`
  - [x] Unit: `test_status_returns_stopped` — `status()` returns `ServiceStatus(running=False, ...)`
  - [x] Checkpoint: `cd packages/archon-search && uv run pytest tests/test_service_windows.py -v`

---

### Phase 4 — `archon-search` CLI
> **Releasable**: after Task 4.4 — `archon-search` preserves the current operator-facing Search CLI surface without any `archon.*` imports

#### Task 4.1 — CLI entry point scaffold
- [x] **File**: `packages/archon-search/archon_search/cli/main.py`
- **Depends on**: Task 1.1
- **Description**:
  - Uses `click` for CLI
  - `@click.group() def main()`: top-level group, `archon-search` command
  - Imports and registers subcommand groups: `install`, `uninstall`, `start`, `stop`, `status`, `ingest`, `sync`, `collection`, `config`
  - `if __name__ == "__main__": main()` guard
  - Entry point wired in `pyproject.toml`: `archon-search = "archon_search.cli.main:main"`
- **Releasable**: after this task, `archon-search --help` works
- **Tests (TDD)** — `packages/archon-search/tests/cli/test_main.py`:
  - Unit: `test_help_exits_zero` — `CliRunner().invoke(main, ["--help"])` exits 0
  - Unit: `test_unknown_command_exits_nonzero` — `CliRunner().invoke(main, ["bogus"])` exits non-zero
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/cli/test_main.py -v`

#### Task 4.2 — `start` and `stop` subcommands
- [x] **File**: `packages/archon-search/archon_search/cli/start.py`, `stop.py`
- **Depends on**: Task 4.1, Task 3.2, Task 3.3, Task 1.3
- **Description**:
  - `start.py`:
    - `@click.command() def start(config: Path | None)`: loads `SearchConfig` via `load_config(config)`, selects platform service via `sys.platform`, calls `service.start()`, prints "archon-search started"
    - `--config PATH` option (optional; defaults to `~/.archon/archon-search.toml`)
  - `stop.py`:
    - `@click.command() def stop()`: selects platform service, calls `service.stop()`, prints "archon-search stopped"
  - Platform service selection: `sys.platform == "darwin"` → `LaunchdSearchService`; `"linux"` → `SystemdSearchService`; `"win32"` → `WindowsSearchService`
  - All platform selection logic in one `_get_service() -> SearchServiceLifecycle` helper in `cli/`
- **Releasable**: after this task, `archon-search start` and `archon-search stop` work on macOS/Linux
- **Tests (TDD)** — `packages/archon-search/tests/cli/test_start_stop.py`:
  - Unit: `test_start_calls_service_start` — service mocked; `start` command calls `service.start()`
  - Unit: `test_stop_calls_service_stop` — service mocked
  - Unit: `test_start_with_custom_config_path` — `--config` option honored
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/cli/test_start_stop.py -v`

#### Task 4.3 — `status` subcommand
- [x] **File**: `packages/archon-search/archon_search/cli/status.py`
- **Depends on**: Task 4.1, Task 3.1
- **Description**:
  - `@click.command() def status()`: reports both process status and collection/indexing status
  - Prints "running (PID N, uptime Xs)" or "stopped", plus the same collection-level progress/watch visibility that current operators rely on (`doc_count`, `chunk_count`, indexing status, `eta_seconds` when available, `watching`)
  - Uses the standalone Search-side status contract (`GET /status` when the server is up, local service state otherwise)
  - If service raises, prints error and exits 1
- **Releasable**: after this task, `archon-search status` works
- **Tests (TDD)** — `packages/archon-search/tests/cli/test_status.py`:
  - Unit: `test_status_running_output` — mocked `ServiceStatus(running=True, pid=123, uptime_seconds=42.0)` → output contains "running"
  - Unit: `test_status_stopped_output` — `ServiceStatus(running=False, ...)` → output contains "stopped"
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/cli/test_status.py -v`

#### Task 4.4 — `install`, `uninstall`, `ingest`, `sync`, `collection`, and `config` subcommands
- [x] **Files**: `packages/archon-search/archon_search/cli/install_cmd.py`, `packages/archon-search/archon_search/cli/config_cmd.py`
- **Depends on**: Task 4.1, Task 1.3, Task 3.2, Task 3.3
- **Description**:
  - `install_cmd.py`:
    - `@click.command() def install()`: preserves the current end-to-end bootstrap workflow: create default `archon-search.toml` if absent, ensure data/log directories exist, register the service, start it, wait for `GET /health`, and trigger initial sync/ingest
    - On upgrade, detect legacy Archon-managed Search launchd/systemd definitions and rewrite or re-register them so they point at the standalone `archon-search` service entrypoint before Phase 8 deletes `archon.search.server`
    - Preserve current safety/automation flags where they exist today (`--dry-run`, `--non-interactive`)
    - `@click.command() def uninstall()`: unregisters the Search service; preserves the current `--delete-db` destructive cleanup path as an explicit flag
  - `ingest.py` / `sync.py` / `collection.py`:
    - Preserve the current operator-facing verbs: `ingest`, `sync`, and `collection list/add/remove/info/reindex`
    - During the extraction phase these commands may call package-internal Search APIs directly; after the REST control plane exists they may delegate to that boundary, but they must not depend on `archon.*`
    - Preserve current operator-facing safety semantics such as `collection remove --dry-run/--force` and the pinned-only removal error path
  - `config_cmd.py`:
    - `@click.group() def config()`: subgroup
    - `@config.command() def show()`: loads config, prints as TOML
    - `@config.command() def get(key: str)`: looks up dotted key (e.g. `server.port`), prints value
    - `@config.command() def set(key: str, value: str)`: writes updated value to TOML file using `tomlkit`
- **Releasable**: after this task, `archon-search` covers the current lifecycle, ingest, sync, collection, and config verbs
- **Tests (TDD)** — `packages/archon-search/tests/cli/test_config_cmd.py`:
  - Unit: `test_config_show_prints_toml` — output contains `[server]`
  - Unit: `test_config_get_port` — `config get server.port` prints default port
  - Unit: `test_config_set_updates_file` — `config set server.port 9000` writes to file
  - Unit: `test_install_registers_service` — `service.register()` called
  - Unit: `test_install_waits_for_health_and_triggers_initial_sync` — install does not exit before Search is usable
  - Unit: `test_install_migrates_legacy_service_definition` — existing Archon-managed plist/unit is rewritten or replaced
  - Unit: `test_uninstall_unregisters_service` — `service.unregister()` called
  - Unit: `test_uninstall_delete_db_flag_preserved` — destructive database cleanup stays explicit and tested
  - Unit: `test_sync_command_available` — `archon-search sync --help` exits 0
  - Unit: `test_collection_group_available` — `archon-search collection --help` exits 0
  - Unit: `test_collection_remove_dry_run_and_force_semantics_preserved` — standalone CLI preserves current safety flags
  - Unit: `test_collection_remove_pinned_only_error_preserved` — pinned-only removal still fails clearly
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/cli/ -v`

---

### Phase 5 — HTTP control plane
> **Releasable**: after Task 5.7 — all REST endpoints up; `archon-search start` serves HTTP

#### Task 5.1 — Job store (persistence + lifecycle)
- [x] **Files**: `packages/archon-search/archon_search/jobs/model.py`, `packages/archon-search/archon_search/jobs/store.py`
- **Depends on**: Task 1.2
- **Description**:
  - `model.py`: re-exports `IngestJob`, `JobStatus` from `archon_search.types` (no duplication); adds `JOBS_FILE: Path = Path.home() / ".archon" / "archon-search-jobs.json"`
  - `store.py`:
    - `class JobStore`:
      - `def __init__(self, path: Path = JOBS_FILE)`: loads existing jobs from `path`; on load, transitions RUNNING/CANCELLING → FAILED with `error="process_restart"`; corrupt JSON → empty store, log error
      - `def create(self, **kwargs) -> IngestJob`: generates UUIDv4 `job_id`, `status=PENDING`, timestamps `created_at` + `updated_at` (ISO-8601 UTC); writes atomically
      - `def update(self, job_id: str, **kwargs) -> IngestJob`: updates fields, sets `updated_at`; writes atomically; raises `KeyError` if unknown
      - `def get(self, job_id: str) -> IngestJob | None`
      - `def list(self) -> list[IngestJob]`
      - `def _write_atomic(self)`: write to `path.with_suffix(".tmp")`, then `rename()` to `path`
      - `def _evict_old(self)`: remove jobs where `updated_at` is >7 days ago; called on startup and every write
- **Releasable**: after this task, `JobStore` is usable for job tracking
- **Tests (TDD)** — `packages/archon-search/tests/test_job_store.py`:
  - Unit: `test_create_returns_job_with_uuid` — `job_id` matches UUIDv4 pattern
  - Unit: `test_create_status_is_pending` — `status == JobStatus.PENDING`
  - Unit: `test_update_changes_status` — `PENDING → RUNNING` transition
  - Unit: `test_atomic_write` — temp file renamed; no partial-write artifact
  - Unit: `test_crash_recovery_running_to_failed` — RUNNING job on load → FAILED
  - Unit: `test_crash_recovery_cancelling_to_failed` — CANCELLING → FAILED
  - Unit: `test_corrupt_file_resets` — unparseable JSON → empty store, no exception
  - Unit: `test_eviction_removes_old_jobs` — job updated 8 days ago → evicted
  - Unit: `test_eviction_keeps_recent_jobs` — job updated 1 day ago → retained
  - Unit: `test_get_unknown_returns_none` — unknown `job_id` → `None`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_job_store.py -v`

#### Task 5.2 — FastAPI app factory
- [x] **File**: `packages/archon-search/archon_search/server/app.py`
- **Depends on**: Task 1.3, Task 5.1
- **Description**:
  - `def create_app(config: SearchConfig, job_store: JobStore) -> FastAPI`:
    - Creates `FastAPI(title="archon-search", version=...)` instance
    - Stores `config` and `job_store` in `app.state`
    - Registers all route modules (health, state, route, jobs, collections)
    - Configures logging via `logging.getLogger("archon-search")`
    - Binds to `config.server.host` + `config.server.port`
  - `def run_server(config: SearchConfig)`: creates `JobStore`, calls `create_app()`, runs via `uvicorn.run()`
  - No imports from `archon.*`
- **Releasable**: after this task, `run_server()` starts a working (but route-less) HTTP server
- **Tests (TDD)** — `packages/archon-search/tests/test_app.py`:
  - Unit: `test_create_app_returns_fastapi_instance` — `isinstance(create_app(...), FastAPI)`
  - Unit: `test_app_state_has_config` — `app.state.config` is `SearchConfig`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_app.py -v`

#### Task 5.3 — `GET /health` endpoint
- [x] **File**: `packages/archon-search/archon_search/server/routes_health.py`
- **Depends on**: Task 5.2
- **Description**:
  - `router = APIRouter()`
  - `@router.get("/health") async def health() -> dict`: returns `{"status": "running", "version": <package version>}`
  - Registered in `app.py` with `app.include_router(health_router)`
- **Releasable**: after this task, `GET /health` responds with 200
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_health.py`:
  - [x] Integration: `test_health_returns_200` — `TestClient(app).get("/health")` → 200
  - [x] Integration: `test_health_has_status_running` — response JSON has `"status": "running"`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_routes_health.py -v`

#### Task 5.3a — `GET /status` endpoint
- [x] **File**: `packages/archon-search/archon_search/server/routes_status.py`
- **Depends on**: Task 5.2, Task 5.4, Task 5.7
- **Description**:
  - `@router.get("/status") async def status(request: Request) -> dict`:
    - Returns the rich operator-facing status that current `search_status` and `archon search status` workflows depend on
    - Includes service/process fields (`running`, `pid`, `version`)
    - Includes per-collection summary fields (`name`, `path`, `doc_count`, `chunk_count`, `status`, `watching`, optional `eta_seconds`, `processed_files`, `total_files`, `error`, `error_count`, and manifest-derived state such as `indexed`, `orphan_managed`, `unmanaged`, `not_yet_indexed`)
  - This endpoint is the source for `search_status`; `GET /health` remains a coarse liveness probe only
- **Releasable**: after this task, Search exposes a rich status contract instead of reducing status to a health ping
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_status.py`:
  - Integration: `test_status_returns_running_and_collections` — response includes service + collections
  - Unit: `test_status_includes_eta_when_progress_known` — in-progress collection includes `eta_seconds`
  - Unit: `test_status_includes_watching_flag` — watch-mode visibility preserved
  - Unit: `test_status_includes_progress_and_error_fields` — `processed_files`, `total_files`, `error`, and `error_count` preserved
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_routes_status.py -v`

#### Task 5.4 — `GET /indexing-state` endpoint
- [x] **File**: `packages/archon-search/archon_search/server/routes_state.py`
- **Depends on**: Task 5.2
- **Description**:
  - `@router.get("/indexing-state") async def indexing_state(request: Request) -> dict`:
    - Reads `IndexingStateStore` from `request.app.state.config.database.db_path`
    - Returns the exact shared state shape used by both doctor and the notification monitor:
      - `{last_updated: str | null, trigger: str | null, collections: {<name>: {status, processed_files, total_files, error, error_count, ...}}}`
    - `status` values remain the current persisted lowercase values: `pending | in_progress | done | failed`
    - If state file doesn't exist: returns `{}`
  - Response model matches the contract specified in the brief's HTTP API Surface
- **Releasable**: after this task, `GET /indexing-state` returns collection state
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_state.py`:
  - Integration: `test_indexing_state_empty_when_no_file` — state file absent → `{}`
  - Integration: `test_indexing_state_returns_collections` — state file with two collections → both in response
  - Integration: `test_indexing_state_exact_shared_shape` — response includes top-level `last_updated`, `trigger`, `collections`
  - Integration: `test_indexing_state_fields_present` — per-collection entries include progress/error fields
  - Integration: `test_indexing_state_status_values_match_persisted_schema` — status strings remain `pending | in_progress | done | failed`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_routes_state.py -v`

#### Task 5.5 — `POST /route` endpoint
- [x] **File**: `packages/archon-search/archon_search/server/routes_route.py`
- **Depends on**: Task 5.2, Task 1.2
- **Description**:
  - Request: `{query: str, slots: int | None = None}`
  - `slots`: optional manual override for `routing_shortlist_size`; normal Archon calls omit it so server routing breadth remains server-owned
  - Response: `{pre_context: str | None, pinned_names: list[str], routable_names: list[str], decomposer_invoked: bool}`
  - Server-side embedding via `Embedder`; uses `MultiCollectionRouter` server-side state
  - `archon-search.toml [collections].pinned_collections` is the canonical source of truth for pinned collections
  - Returned routing state is sufficient to preserve the current provider behavior:
    - `pre_context` is the `<search_collections>` block injected before `route_task()` when needed
    - `pinned_names` is the exact resolved pinned-collection list that must always be prepended during phase B
    - `routable_names` is the exact routable-name list later used to validate decomposer selections
    - `decomposer_invoked` distinguishes Tier 1 from Tier 2/3 behavior
  - Does NOT return chunks — routing metadata only
  - Timeout: 30s internal processing; callers (Archon) use 10s timeout
  - Returns 400 if `query` is empty string
- **Releasable**: after this task, `POST /route` returns the routing state needed to preserve the current provider flow
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_route.py`:
  - Integration: `test_route_returns_pre_context_when_decomposer_needed` — response includes `<search_collections>` block
  - Integration: `test_route_returns_pinned_names` — response includes the resolved pinned collection names
  - Integration: `test_route_returns_routable_names_list` — response has the exact routable-name list
  - Integration: `test_route_returns_decomposer_invoked_flag` — response distinguishes Tier 1 vs Tier 2/3
  - Unit: `test_route_empty_query_returns_400` — empty `query` → 400
  - Unit: `test_slots_overrides_shortlist_size` — `slots=2` limits the returned `routable_names` to at most 2 collections
  - Unit: `test_pinned_always_included` — pinned collection always represented in routing output
  - Unit: `test_confidence_gate_filters_low_similarity` — below-threshold collections excluded
  - Unit: `test_centroid_none_bypasses_confidence_gate` — legacy collections with `centroid=None` still route
  - Unit: `test_router_order_is_preserved` — `routable_names` order matches router ranking
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_routes_route.py -v`

#### Task 5.6 — `POST /ingest`, `GET /jobs/{id}`, `DELETE /jobs/{id}`
- [x] **File**: `packages/archon-search/archon_search/server/routes_jobs.py`
- **Depends on**: Task 5.2, Task 5.1
- **Description**:
  - `POST /ingest`: body `{path: str | None, documents: list[dict] | None, collection: str}`:
    - Creates job via `JobStore.create()`; spawns background asyncio task to run ingest pipeline; returns `IngestJob` (202)
    - Background task: updates job `PENDING → RUNNING`, runs ingest, updates `RUNNING → DONE` or `FAILED`
    - Watch-triggered ingest uses same code path
  - `GET /jobs/{job_id}`:
    - Returns `IngestJob` (200); 404 if unknown
  - `DELETE /jobs/{job_id}` (cancel):
    - DONE/FAILED/CANCELLED: 200 (idempotent)
    - RUNNING/PENDING: sets `CANCELLING`, signals worker on next chunk boundary; returns 202
    - Unknown: 404
    - After cancel: `doc_id`s of ingested chunks recorded in `job.result`
- **Releasable**: after this task, async ingest with job tracking works end-to-end
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_jobs.py`:
  - Integration: `test_ingest_returns_202_with_job_id` — POST /ingest → 202, body has `job_id`
  - Integration: `test_get_job_returns_current_status` — GET /jobs/{id} → 200 with `IngestJob`
  - Unit: `test_get_job_unknown_returns_404` — unknown id → 404
  - Unit: `test_delete_job_done_returns_200` — DONE job → 200 idempotent
  - Unit: `test_delete_job_running_returns_202` — RUNNING job → 202, status → CANCELLING
  - Unit: `test_delete_job_unknown_returns_404` — unknown id → 404
  - Unit: `test_delete_job_cancelled_returns_200` — CANCELLED job → 200 idempotent
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_routes_jobs.py -v`

#### Task 5.7 — `GET/POST/DELETE /collections/*`
- [x] **File**: `packages/archon-search/archon_search/server/routes_collections.py`
- **Depends on**: Task 5.2
- **Description**:
  - `GET /collections/`: returns `list[Collection]` — all registered collections with `name`, `path`, `description`, `doc_count`, `chunk_count`, and collection lifecycle status
  - `POST /collections/`: body `{path: str, name: str | None}`; persists the collection config entry and immediately enqueues initial ingest so the new collection becomes searchable as part of the same workflow; returns `IngestJob` (202) plus collection identity
  - `DELETE /collections/{name}`: removes the collection config entry and deletes its LanceDB data under server-side serialization; rejects pinned-only collections with a clear error; CLI `--dry-run` remains a wrapper concern, not an HTTP concern
  - `GET /collections/{name}`: returns `CollectionDetail` including `embedding_model`, `centroid_present`, and `last_indexed`; 404 if not found
  - `POST /collections/{name}/reindex`: starts reindex job; returns `IngestJob` (202)
- **Releasable**: after this task, full collections control plane is up
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_collections.py`:
  - Integration: `test_list_collections_empty` — no collections → `[]`
  - Integration: `test_add_collection_persists_and_starts_ingest` — POST persists config then returns an ingest job
  - Integration: `test_remove_collection_deletes_config_and_data` — DELETE removes both registration and LanceDB data
  - Unit: `test_remove_unknown_collection_returns_404` — unknown name → 404
  - Unit: `test_remove_pinned_only_collection_rejected` — pinned-only remove gets clear error
  - Integration: `test_get_collection_info` — GET `/collections/{name}` → `CollectionDetail`
  - Integration: `test_reindex_returns_ingest_job` — POST reindex → 202 with `job_id`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_routes_collections.py -v`

---

### Phase 6 — Metadata schema
> **Releasable**: after Task 6.1 — new chunk fields present on all ingested chunks

#### Task 6.1 — Extend chunk storage with typed metadata fields
- [x] **Files**: `packages/archon-search/archon_search/store.py`, `packages/archon-search/archon_search/_types.py` (or `archon_search/types.py`)
- **Depends on**: Task 2.1, Task 1.2
- **Description**:
  - Update LanceDB schema (via LanceDB `pa.schema`) to include all fields from the brief's Metadata Schema section:
    - System fields (set by `archon-search` at ingest): `doc_id: str` (retain current SHA-256 path hash), `chunk_id: str` (retain current `"{doc_id}-{idx:06d}"` format), `source_path: str`, `indexed_at: str`, `file_type: str`, `language: str | null`
    - Filterable fields: `metadata: dict[str, str]` (stored as JSON string in LanceDB, parsed on read)
    - Ranking: `custom_score: float | null`
    - Audit: `ingested_by: str` (default `"archon-search-cli"`), `updated_at: str`
  - Schema evolution: additive only; existing chunks missing new fields default to `null`/empty dict on read — no forced reindex
  - Validate `metadata` at ingest: max 50 fields, max 256-char keys, max 4096-char values; raise `ValueError` on violation
  - `ingested_by` populated from HTTP header `X-Ingested-By` if present; else default
- **Releasable**: after this task, ingested chunks carry full metadata schema
- **Tests (TDD)** — `packages/archon-search/tests/test_metadata_schema.py`:
  - Unit: `test_ingested_chunk_has_doc_id` — ingested chunk preserves current SHA-256 path-hash format
  - Unit: `test_ingested_chunk_has_chunk_id` — ingested chunk preserves current `"{doc_id}-{idx:06d}"` format
  - Unit: `test_ingested_chunk_has_indexed_at` — ISO-8601 UTC timestamp
  - Unit: `test_metadata_dict_stored_and_retrieved` — `{"key": "val"}` round-trips correctly
  - Unit: `test_metadata_max_fields_validation` — 51 fields → `ValueError`
  - Unit: `test_metadata_key_too_long_validation` — key > 256 chars → `ValueError`
  - Unit: `test_metadata_value_too_long_validation` — value > 4096 chars → `ValueError`
  - Unit: `test_custom_score_stored` — `custom_score=0.9` round-trips
  - Unit: `test_existing_chunk_missing_new_field_defaults_null` — old-schema chunk read → `language=None`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_metadata_schema.py -v`

---

### Phase 7 — Archon client adapter + Archon-side migration
> **Releasable**: after Task 7.7 — Archon has zero direct `archon.search.*` or `archon_search.*` imports outside `search_client.py`

#### Task 7.1 — `archon/ai/search_client.py` — HTTP client adapter
- [x] **File**: `archon/ai/search_client.py`
- **Depends on**: Task 5.3, Task 5.4, Task 5.5, Task 5.6, Task 5.7
- **Description**:
  - `class SearchClient`:
    - `def __init__(self, base_url: str, timeout: float = 10.0)`: stores `base_url`; creates `httpx.AsyncClient`
    - `async def route(self, query: str, slots: int | None = None) -> RouteResponse | None`: POST `/route`; returns `None` on any failure (connection refused, timeout, 5xx, malformed JSON) — logs at appropriate level
    - `async def health(self) -> dict | None`: GET `/health`; returns `None` on failure
    - `async def status(self) -> dict | None`: GET `/status`; returns rich service + collection state
    - `async def indexing_state(self) -> dict | None`: GET `/indexing-state`; returns `None` on failure
    - `async def ingest(self, path: str | None = None, documents: list[dict] | None = None, collection: str = "") -> IngestJob | None`
    - `async def get_job(self, job_id: str) -> IngestJob | None`
    - `async def cancel_job(self, job_id: str) -> int`: returns HTTP status code
    - `async def list_collections(self) -> list[dict]`: returns rich collection summaries; returns `[]` on failure
    - `async def add_collection(self, path: str, name: str | None = None) -> dict | None`: returns collection identity plus initial ingest job
    - `async def remove_collection(self, name: str) -> dict | None`: returns structured removal result or clear error
    - `async def collection_info(self, name: str) -> dict | None`: returns rich collection detail
    - `async def reindex_collection(self, name: str) -> IngestJob | None`
  - May import `from archon_search.types import IngestJob, RouteResponse, JobStatus` (sole exemption)
  - All HTTP failures: log at WARNING (timeout/5xx) or DEBUG (connection refused); never raise — return `None`/empty
  - `def get_search_client() -> SearchClient`: singleton factory using Archon config `search.url`
- **Releasable**: after this task, all Archon HTTP→Search calls go through `search_client.py`
- **Tests (TDD)** — `tests/ai/test_search_client.py`:
  - Unit: `test_route_calls_post_route` — httpx mocked; correct endpoint called with payload
  - Unit: `test_route_parses_pre_context_and_router_state` — `pre_context`, `pinned_names`, `routable_names`, and `decomposer_invoked` parsed correctly
  - Unit: `test_route_connection_refused_returns_none` — `httpx.ConnectError` → `None`
  - Unit: `test_route_timeout_returns_none` — `httpx.TimeoutException` → `None`, logs WARNING
  - Unit: `test_route_5xx_returns_none` — status 500 → `None`, logs WARNING
  - Unit: `test_route_malformed_json_returns_none` — invalid JSON → `None`, logs WARNING
  - Unit: `test_health_returns_dict_on_success` — 200 with JSON → `dict`
  - Unit: `test_status_returns_rich_status_on_success` — 200 with JSON → service + collection fields present
  - Unit: `test_health_not_running_returns_none` — connection refused → `None`
  - Unit: `test_ingest_returns_ingest_job` — 202 with `IngestJob` shape → `IngestJob`
  - Unit: `test_cancel_job_returns_status_code` — returns int HTTP status
  - Checkpoint: `uv run pytest tests/ai/test_search_client.py -v`

#### Task 7.2 — Rewrite `search_context_provider.py` to use HTTP
- [x] **File**: `archon/ai/search_context_provider.py`
- **Depends on**: Task 7.1
- **Description**:
  - Remove imports: `from archon.search.embedder import Embedder`, `from archon.search.router import MultiCollectionRouter`
  - New flow preserves the current two-phase provider behavior:
    1. Before `route_task()`, call `await search_client.route(query)` → `RouteResponse`
    2. Inject `route_response.pre_context` into the routing prompt when present
    3. After `route_task()`, use `route_response.pinned_names`, `route_response.routable_names`, and `route_response.decomposer_invoked` to reproduce the current Tier 1 / Tier 2 / Tier 3 selection logic and discard hallucinated collection names
    4. Call FastMCP JSON-RPC search endpoint for the resulting collection list and merge results as before, still honoring `cfg.search.max_parallel_collections` and `cfg.search.top_k_return`
  - `search_client` injected via constructor or `get_search_client()` singleton
  - If `cfg.search.enabled is False`: return empty context immediately (no HTTP call)
  - Server-side routing semantics stay protected in `/route` tests; client-side merge/fan-out semantics remain protected in `SearchContextProvider` tests because `max_parallel_collections` and `top_k_return` stay Archon-owned
  - Latency: log timing of `route()` call at DEBUG level for benchmark measurement
- **Releasable**: after this task, `search_context_provider.py` has no Search internal imports
- **Tests (TDD)** — `tests/ai/test_search_context_provider.py`:
  - Unit: `test_get_context_calls_route_then_fastmcp` — mocked client; `route()` called first, FastMCP second
  - Unit: `test_get_context_preserves_pinned_names_from_route_state` — returned `pinned_names` are always prepended during phase B
  - Unit: `test_get_context_preserves_tier1_behavior_from_route_state` — `decomposer_invoked=False` keeps Tier 1 semantics
  - Unit: `test_get_context_discards_hallucinated_collections_from_route_state` — selected names filtered against returned `routable_names`
  - Unit: `test_get_context_respects_max_parallel_and_top_k_return` — client-side fan-out/merge semantics unchanged
  - Unit: `test_get_context_search_disabled_returns_empty` — `cfg.search.enabled=False` → empty, no HTTP call
  - Unit: `test_get_context_route_returns_none_returns_empty` — `route()` returns `None` → empty context
  - Unit: `test_get_context_empty_routable_names_returns_empty` — no routable collections → empty context, no FastMCP call
  - Checkpoint: `uv run pytest tests/ai/test_search_context_provider.py -v`

#### Task 7.3 — Rewrite all 10 MCP tools in `archon_toolkit_search.py` as HTTP calls
- [x] **File**: `archon/ai/archon_toolkit_search.py`
- **Depends on**: Task 7.1
- **Description**:
  - Remove all imports of `archon.search.*` and `archon_search.*` (except via `search_client`)
  - Rewrite each tool to call the appropriate `SearchClient` method:
    - `search_status` → `search_client.status()`
    - `search_start` → local lifecycle wrapper (`archon-search start` or platform service adapter), not Search internals
    - `search_stop` → local lifecycle wrapper (`archon-search stop` or platform service adapter)
    - `search_ingest` → `search_client.ingest()` → returns `IngestJob` (breaking change: was sync)
    - `search_sync` → `archon-search sync` wrapper until or unless a dedicated sync endpoint exists
    - `search_collection_list` → `search_client.list_collections()`
    - `search_collection_add` → `search_client.add_collection()`
    - `search_collection_remove` → `search_client.remove_collection()`
    - `search_collection_info` → `search_client.collection_info()`
    - `search_collection_reindex` → `search_client.reindex_collection()` → returns `IngestJob`
  - Each tool returns an appropriate string representation of the response
  - Breaking: `search_ingest` and `search_collection_reindex` now return job info, not blocking result
- **Releasable**: after this task, all 10 MCP tools cross the package boundary cleanly — REST where the server owns the operation, local CLI/service wrappers where the standalone process lifecycle is involved
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_search.py`:
  - Unit: `test_search_status_calls_status_endpoint` — `SearchClient.status()` called, returns rich status string
  - Unit: `test_search_start_invokes_local_cli_wrapper` — no `archon.search.*` imports used
  - Unit: `test_search_stop_invokes_local_cli_wrapper` — no `archon.search.*` imports used
  - Unit: `test_search_ingest_calls_client_ingest` — `SearchClient.ingest()` called, returns job_id string
  - Unit: `test_search_ingest_returns_job_info` — response includes `job_id` (breaking change verified)
  - Unit: `test_search_sync_invokes_sync_wrapper` — sync does not import `SearchCollectionSync`
  - Unit: `test_search_collection_list_calls_client` — `SearchClient.list_collections()` called
  - Unit: `test_search_collection_add_calls_client` — `SearchClient.add_collection()` called
  - Unit: `test_search_collection_remove_calls_client` — `SearchClient.remove_collection()` called
  - Unit: `test_search_collection_reindex_calls_client` — `SearchClient.reindex_collection()` called
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_search.py -v`

#### Task 7.4 — Move `IndexingNotificationMonitor` to `archon/gateway/`
- [x] **File**: `archon/gateway/notification_monitor.py` (new), `archon/gateway/gateway.py` (update)
- **Depends on**: Task 5.4, Task 7.1
- **Description**:
  - `archon/gateway/notification_monitor.py`:
    - `class IndexingNotificationMonitor` — asyncio background task
    - Polls `GET /indexing-state` via `SearchClient.indexing_state()` every 30 seconds (matching current cadence)
    - Stop condition: all collections in terminal state (`DONE` or `FAILED`) after `"install"` or `"update"` trigger → send Telegram summary
    - `"manual"` trigger: suppressed (no Telegram notification)
    - Connection refused: log DEBUG, retry next cycle — no error spam
    - Quiet mode: suppressed
    - Imports: `from archon.ai.search_client import SearchClient` (via gateway config); `from archon.config import config`; NO imports from `archon.search.*`
  - `archon/gateway/gateway.py`:
    - Remove imports of `IndexingNotificationMonitor`, `IndexingStateStore`, and Search reachability helpers from `archon.search.*`
    - Treat Search as an optional dependency at `cfg.search.url`; do not auto-start it from gateway startup
    - Import `IndexingNotificationMonitor` from `archon.gateway.notification_monitor`
- **Releasable**: after this task, `IndexingNotificationMonitor` has no Search internal imports; Telegram notifications preserved
- **Tests (TDD)** — `tests/gateway/test_notification_monitor.py`:
  - Unit: `test_monitor_polls_http_endpoint` — `SearchClient.indexing_state()` called each cycle
  - Unit: `test_monitor_sends_notification_on_all_done` — all DONE → `send_message()` called
  - Unit: `test_monitor_sends_notification_on_all_failed` — all FAILED → `send_message()` called
  - Unit: `test_monitor_no_notification_on_manual_trigger` — `"manual"` trigger → no notification
  - Unit: `test_monitor_connection_refused_logs_debug_not_error` — `None` response → log DEBUG, continue
  - Unit: `test_monitor_suppressed_in_quiet_mode` — quiet mode → no notification
  - Unit: `test_gateway_does_not_auto_start_search_after_extraction` — Search unavailability logs warning and Archon continues
  - Checkpoint: `uv run pytest tests/gateway/test_notification_monitor.py -v`

#### Task 7.5 — Update `archon/cli/doctor.py` to call HTTP
- [x] **File**: `archon/cli/doctor.py`
- **Depends on**: Task 7.1
- **Description**:
  - Remove imports: `from archon.search.progress import IndexingStateStore`, `from archon.search.progress import IndexingStatus`
  - Replace with HTTP calls:
    - `await search_client.health()` → if `None`: show "Search: not running" (no crash)
    - `await search_client.indexing_state()` → per-collection status display
    - `IN_PROGRESS`: `⏳ partial (N/M files)` (same as before)
    - `FAILED`: `❌` (same as before)
  - If `cfg.search.enabled is False`: show "Search: disabled" — no HTTP call
  - Explicitly narrow Archon's responsibility: detailed Search-internal diagnostics (for example collection/config mismatches that require Search-local config access) move to `archon-search` operator commands instead of being retained in Archon
  - Note: `doctor` is a CLI command so it can run synchronously wrapping async calls with `asyncio.run()`
- **Releasable**: after this task, `archon doctor` uses HTTP for Search status
- **Tests (TDD)** — `tests/cli/test_doctor.py` (update existing):
  - Unit: `test_doctor_search_running_calls_health` — `SearchClient.health()` called, output shows healthy
  - Unit: `test_doctor_search_not_running_shows_not_running` — `None` → "not running", no crash
  - Unit: `test_doctor_search_disabled_shows_disabled` — `enabled=False` → "disabled", no HTTP call
  - Unit: `test_doctor_in_progress_shows_partial` — `IN_PROGRESS` state → `⏳ partial (N/M files)`
  - Unit: `test_doctor_failed_shows_error` — `FAILED` state → `❌`
  - Checkpoint: `uv run pytest tests/cli/test_doctor.py -v`

#### Task 7.6 — Update `archon/cli/search_cmd.py` and `archon/cli/update.py`
- [x] **Files**: `archon/cli/search_cmd.py`, `archon/cli/update.py`
- **Depends on**: Task 7.1
- **Description**:
  - [x] Remove all direct imports of `archon.search.*` internals
  - [x] Replace with calls to `SearchClient` for server-owned operations and delegation to `archon-search` CLI for local lifecycle/reconcile commands
  - [x] Preserve the current Archon CLI surface during migration even if the implementation becomes a thin wrapper around the standalone package
  - [x] Update `archon/cli/update.py` and uninstall flows so they no longer depend on `get_search_service` or `SearchInstaller`; if a legacy Archon-managed Search service is detected, stop/unregister it and hand off to the standalone `archon-search` install/uninstall workflow
- **Releasable**: after this task, Archon's search/update CLI paths have no Search internal imports
- **Tests (TDD)** — `tests/cli/test_search_cmd.py` (update existing):
  - [x] Unit: `test_search_cmd_uses_boundary_adapters` — server-owned operations use `SearchClient`; lifecycle/reconcile operations use the standalone CLI wrapper; no `archon.search.*` imports remain
  - [x] Unit: `test_update_command_hands_off_search_lifecycle` — Archon update/uninstall paths no longer import Search internals and correctly invoke standalone lifecycle handoff
  - [x] Unit: `test_uninstall_delete_db_hands_off_cleanly` — `archon search uninstall --delete-db` preserves explicit destructive semantics through the standalone CLI handoff
  - [x] Unit: `test_collection_remove_dry_run_and_force_handoff` — `archon search collection remove --dry-run/--force` preserves current safety behavior through the standalone CLI handoff
  - [x] Unit: `test_collection_remove_pinned_only_error_preserved` — pinned-only removal still surfaces the current operator-facing error
  - Checkpoint: `uv run pytest tests/cli/test_search_cmd.py tests/cli/test_update.py -v`

#### Task 7.7 — Shrink Archon `[search]` config to client-only fields
- [x] **Files**: `archon/config/loader.py` (or schema file), `examples/config.toml.example`
- **Depends on**: Task 7.1
- **Description**:
  - `SearchConfig` in Archon's config retains client-side fields only:
    - `url: str = "http://127.0.0.1:8765"`
    - `enabled: bool = False`
    - `max_parallel_collections: int = 3`
    - `top_k_return: int = 5`
  - All removed fields (`collections`, `db_path`, `embedding_model`, `reranker_model`, `chunk_size`, `pinned_collections`, `routing_*`, `auto_reindex_on_chunk_size_change`, `watch`) emit deprecation warning if present in user's `config.toml`
  - Warning: `"[search] key '{key}' is no longer read by Archon — move it to archon-search.toml"` logged at WARNING
  - Update `examples/config.toml.example` to show client-only `[search]` section with comment pointing to `archon-search.toml`
  - Update `CLAUDE.md` `[search]` config documentation section
- **Releasable**: after this task, Archon's config schema is clean
- **Tests (TDD)** — `tests/config/test_search_config.py` (update existing):
  - Unit: `test_search_config_has_url_field` — `cfg.search.url` accessible
  - Unit: `test_search_config_has_enabled_field` — `cfg.search.enabled` accessible
  - Unit: `test_search_config_has_max_parallel_collections_field` — `cfg.search.max_parallel_collections` accessible
  - Unit: `test_search_config_has_top_k_return_field` — `cfg.search.top_k_return` accessible
  - Unit: `test_search_config_deprecated_key_logs_warning` — old `[search] db_path` in TOML → WARNING logged
  - Unit: `test_search_config_no_longer_has_db_path` — `cfg.search.db_path` raises `AttributeError`
  - Checkpoint: `uv run pytest tests/config/test_search_config.py -v`

---

### Phase 8 — Delete old `archon/search/` source + `archon/platform/search_service.py`
> **Releasable**: after Task 8.2 — `archon/search/` and platform search services fully removed

#### Task 8.1 — Delete `archon/search/` source directory
- [x] **File**: `archon/search/` (delete entire directory)
- **Depends on**: All Phase 7 tasks, Task 2.1 through 2.4 (all Archon consumers migrated)
- **Description**:
  - Verify no file in `archon/` or `tests/` imports `archon.search.*` before deletion:
    `grep -r "from archon.search\\|import archon.search" archon/ tests/ --include="*.py"` → must return empty
  - Delete `archon/search/` directory entirely
  - Update `archon/__init__.py` if it exported search symbols
  - Remove `archon/search/` from test discovery in `pytest.ini` / `pyproject.toml` if listed
- **Releasable**: after this task, `archon.search` module is gone
- **Tests (TDD)** — verifies via import boundary lint in Task 9.1

#### Task 8.2 — Delete platform search service classes from `archon/platform/`
- [x] **Files**: `archon/platform/macos/search_service.py`, `archon/platform/linux/search_service.py`, `archon/platform/windows/search_service.py`
- **Depends on**: Task 8.1, Phase 3 (extracted equivalents exist in `archon-search`)
- **Description**:
  - Delete all three `search_service.py` files from `archon/platform/`
  - Update `archon/platform/__init__.py` to remove any `get_search_service` export
  - Verify no remaining callers: `grep -r "get_search_service\|search_service" archon/ --include="*.py"` → empty
- **Releasable**: after this task, `archon/platform/` contains only Archon-specific service management
- **Tests (TDD)** — existing platform tests continue passing; search-service-specific tests moved to `archon-search`

---

### Phase 9 — Import boundary lint tests
> **Releasable**: after Task 9.2 — CI enforces the import boundary permanently

#### Task 9.1 — Import boundary lint: `archon-search` side
- [x] **File**: `packages/archon-search/tests/test_import_boundary.py`
- **Depends on**: Task 8.1
- **Description**:
  - Uses Python `ast` module to walk all `.py` files under `packages/archon-search/archon_search/`
  - Fails if any `import` or `from ... import` statement references `archon.` (not `archon_search.`)
  - Error message includes the offending file and import statement
  - No third-party lint tool required — pure `ast` + `pathlib`
  ```python
  def test_no_archon_imports_in_archon_search():
      root = Path(__file__).parents[2] / "archon_search"
      for py_file in root.rglob("*.py"):
          tree = ast.parse(py_file.read_text())
          for node in ast.walk(tree):
              if isinstance(node, (ast.Import, ast.ImportFrom)):
                  # check module name doesn't start with 'archon.'
                  ...
  ```
- **Releasable**: after this task, CI catches any re-introduced `archon.` imports in `archon-search`
- **Tests (TDD)** — `packages/archon-search/tests/test_import_boundary.py`:
  - Unit: `test_no_archon_imports_in_archon_search` — walks all files, fails on any `from archon.` hit
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_import_boundary.py -v`

#### Task 9.2 — Import boundary lint: Archon side
- [x] **File**: `tests/test_import_boundary.py` (new, Archon root)
- **Depends on**: Task 8.1, Task 7.1
- **Description**:
  - Uses `ast` module to walk all `.py` files under `archon/` and `tests/`
  - Fails if any file imports `archon.search.*`
  - Fails if any file imports `archon_search.*` **except** `archon/ai/search_client.py`
  - Exemption is explicit: lint skips `search_client.py` by path
  ```python
  EXEMPT = {"archon/ai/search_client.py"}
  
  def test_no_archon_search_imports_outside_client():
      root = Path(__file__).parent.parent
      for py_file in (root / "archon").rglob("*.py"):
          if str(py_file.relative_to(root)) in EXEMPT:
              continue
          # check no import of archon_search.*
          ...
  ```
  - **Releasable**: after this task, CI catches any Archon/test file importing `archon.search.*` and any non-adapter file importing `archon_search.*`
- **Tests (TDD)** — `tests/test_import_boundary.py`:
  - [x] Unit: `test_no_archon_search_imports_in_archon_or_tests` — fails on `archon.search.*`
  - [x] Unit: `test_no_archon_search_imports_outside_search_client` — walks all files, fails on violation
  - [x] Unit: `test_search_client_is_exempt` — `search_client.py` itself is not flagged
  - [x] Checkpoint: `uv run pytest tests/test_import_boundary.py -v`

---

### Phase 10 — Routing latency benchmark
> **Releasable**: after Task 10.1 — latency measured; p95 ≤ 150ms or co-located embedder decision recorded

#### Task 10.1 — Measure and document routing latency
- [x] **File**: `packages/archon-search/tests/benchmark_routing_latency.py`
- **Depends on**: Task 5.5, Task 7.2
- **Description**:
  - Script (not a normal pytest test — run manually with `pytest --benchmark` or as a standalone script):
    - Establishes baseline: current in-process `MultiCollectionRouter` embed time (pre-extraction)
    - Measures HTTP round-trip: `POST /route` with a realistic query against a running `archon-search` server
    - Reports p50/p95 over 100 iterations
  - Results documented in the PR description or an ADR/update note referenced from the implementation
  - If p95 > 150ms: record decision in brief's Key Decisions — co-located embedder mode (Archon embeds locally and passes vector to server)
  - Target: p50 ≤ 30ms, p95 ≤ 150ms (20–50ms over localhost)
- **Releasable**: benchmark harness exists and the rollout records p50/p95 or an explicit exception
- **Tests (TDD)** — `packages/archon-search/tests/benchmark_routing_latency.py`:
  - Live E2E: `test_routing_latency_harness_runs` — requires running server; prints p50/p95 for rollout sign-off
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/benchmark_routing_latency.py -v -s`

---

### Phase 11 — Test migration + Archon-side test updates
> **Releasable**: after Task 11.2 — all tests pass; no Archon test imports `archon_search.*` internals

#### Task 11.1 — Migrate existing search tests to `packages/archon-search/tests/`
- [ ] **Files**: `packages/archon-search/tests/` (migrated from `tests/search/` or wherever current search tests live)
- **Depends on**: Phase 8, Phase 5
- **Description**:
  - Move all tests that import `archon.search.*` to `packages/archon-search/tests/`
  - Update imports: `from archon.search.X` → `from archon_search.X`
  - Tests that tested Search internals now test `archon_search` internals — same logic, new import path
  - Any test that mocked Archon config for Search setup: update to use `load_config()` with a test TOML
- **Releasable**: all `packages/archon-search/tests/` pass
- **Tests (TDD)** — run migrated suite:
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/ -v`

#### Task 11.2 — Update Archon-side tests to mock HTTP boundary
- [ ] **Files**: `tests/` (Archon side) — any test currently importing `archon.search.*`
- **Depends on**: Task 11.1, Phase 7
- **Description**:
  - Find all Archon-side test files importing `archon.search.*`: `grep -r "from archon.search" tests/ --include="*.py" -l`
  - For each: replace Search internal mocks with `SearchClient` mocks
  - Pattern: `mocker.patch("archon.ai.search_client.SearchClient.route", return_value=RouteResponse(...))`
  - Gateway tests that test `IndexingNotificationMonitor`: update to mock `SearchClient.indexing_state()`
  - Doctor tests: already covered in Task 7.5
  - Add a guard test that no Archon-side test imports `archon.search.*` after the migration
- **Releasable**: after this task, `uv run pytest tests/` passes with zero `archon.search.*` imports in test files
- **Tests (TDD)**:
  - Checkpoint: `uv run pytest tests/ -v`

---

### Phase 12 — Migration guide + documentation
> **Releasable**: after Task 12.2 — documentation accurate for a clean-install user

#### Task 12.1 — Migration guide for existing users
- [ ] **File**: `Documentation/UserManual/search-migration-guide.md`
- **Depends on**: Task 7.7
- **Description**:
  - Step-by-step guide:
    1. Create `~/.archon/archon-search.toml` manually (template provided)
    2. Move all Search-internal config keys from Archon's `config.toml [search]` to `archon-search.toml`
    3. Verify Archon's `[search]` retains only client-side fields: `url`, `enabled`, `max_parallel_collections`, `top_k_return`
    4. Run `archon-search install` (or an explicit migration command) so any legacy Archon-managed launchd/systemd service definition is rewritten to the standalone `archon-search` entrypoint
    5. Run `archon-search start` to verify the standalone service boots
    6. Run `archon doctor` to confirm Search health shown via HTTP
  - Template `archon-search.toml` provided with all defaults
  - Deprecation warning behavior documented
  - Data directory note: LanceDB tables unchanged — no migration needed
  - Include a smoke-test checklist: start Search, verify `GET /health`, run `archon doctor`, confirm disabled-mode behavior when `search.enabled=false`
- **Releasable**: migration guide is actionable by an existing user
- **Validation**:
  - Manual: run the smoke-test checklist on a clean-install environment and record the outcome in the implementation notes

#### Task 12.2 — Update Architecture documentation
- [ ] **Files**: `Documentation/Architecture/180_search_architecture.md`, `Documentation/Architecture/110_component_catalog_and_layer_breakdown.md`, `CLAUDE.md`
- **Depends on**: Task 12.1
- **Description**:
  - `180_search_architecture.md`: update to reflect HTTP boundary, `packages/archon-search/` structure, new config path, HTTP API Surface table
  - `110_component_catalog_and_layer_breakdown.md`: update Search component locations post-extraction
  - `CLAUDE.md`: update `[search]` config section to show client-only fields; update search-related module descriptions to reflect HTTP client adapter; remove references to `archon/search/`
- **Releasable**: documentation accurate post-extraction
- **Tests (TDD)** — N/A (documentation)
