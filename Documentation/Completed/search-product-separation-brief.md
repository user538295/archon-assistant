---
Purpose: Feature Brief for Search Product Separation (P0 items 1–3)
Status: Draft
Last reviewed: 2026-04-30
---

# Feature Brief: Search Product Separation (P0 Items 1–3)

## Problem

Search is a separate process but not a separate product. It cannot bootstrap without importing Archon's config loader, its service management lives inside `archon/platform/`, and Archon's client-side routing code imports Search internals directly (`Embedder`, `MultiCollectionRouter`). Until these couplings are removed, Search cannot be versioned, released, or operated independently.

The coupling is bidirectional: Archon imports Search internals, and Search imports Archon internals. Both directions must be severed.

## Goal

The Search server starts and runs with zero imports from Archon application modules. Archon integrates with Search exclusively through HTTP/MCP. The service contract (domain types) and metadata schema are defined inside the standalone package, not inherited from Archon conventions.

## Users & Context

Maintainers performing extraction work and, once complete, operators who want to run Search independently of Archon. The user-facing change is minimal — the daemon still runs locally — but the package boundary becomes real rather than nominal.

## Complete Coupling Inventory

Every `archon.search ↔ archon.*` cross-import that must be severed, with the planned resolution for each:

### Search → Archon imports (must be removed from `archon-search`)

| File | Import | Resolution |
|---|---|---|
| `archon/search/description_generator.py` | `from archon.ai.claude_session import _get_env_lock` | Duplicate the small asyncio lock helper inline in `archon-search`; it is too small to warrant a shared package |
| `archon/search/description_generator.py` | `from archon.ai.constants import DEFAULT_FAST_MODEL` | Define `DEFAULT_FAST_MODEL` as a constant in `archon-search`'s own `constants.py` |
| `archon/search/notification_monitor.py` | `from archon.config.loader import NotificationsConfig` | Move `IndexingNotificationMonitor` to Archon's gateway (see Key Decisions — `notification_monitor.py` ownership); no longer needed inside `archon-search` |
| `archon/search/install.py` | `from archon.cli.console import Console` | Replace with minimal `print()`-based UI inside `archon-search` |
| `archon/search/install.py` | `from archon.platform import get_search_service, get_runtime` | `get_runtime()` / `find_binary()` are duplicated with a minimal implementation in `archon-search`; small enough to duplicate cleanly |
| `archon/search/install.py` | `from archon.platform.types import GpuType` | `GpuType` extracted into `archon-search`'s own platform module |

### Archon → Search imports (must be replaced with HTTP calls)

| File | Import | Resolution |
|---|---|---|
| `archon/ai/archon_toolkit_search.py` | 10 MCP tools import `archon.search.store`, `.pipeline`, `.progress`, `.sync` directly | Rewrite all 10 tools as HTTP proxy calls to the Search server REST control plane (see Core Flow step 7) |
| `archon/gateway/gateway.py:620–621` | Imports `IndexingNotificationMonitor`, `IndexingStateStore` from `archon.search.*` | `IndexingNotificationMonitor` moves to gateway and calls `GET /indexing-state` via HTTP; `IndexingStateStore` is never imported by Archon after extraction |
| `archon/cli/doctor.py:17` | Imports `IndexingStateStore`, `IndexingStatus` | `archon doctor` calls `GET /indexing-state` on the Search HTTP API; if Search is not running, doctor reports "Search: not running" without reading internal files |
| `archon/ai/search_context_provider.py` | `Embedder`, `MultiCollectionRouter` direct imports | Replace with HTTP calls to `POST /route` (see Core Flow step 6 and Key Decisions — HTTP routing architecture) |
| `archon/cli/search_cmd.py` | Direct imports of Search internals | Replace with HTTP client calls or delegate to `archon-search` CLI |

## Core Flow

1. Create `packages/archon-search/` with its own `pyproject.toml`, package name `archon-search`, and independent version.
2. Move `archon/search/` source into `packages/archon-search/archon_search/`.
3. Implement standalone config loading from `archon-search.toml`; remove all imports of `archon.config.loader` from the Search server.
4. Extract `LaunchdSearchService`, `SystemdSearchService`, `WindowsSearchService` from `archon/platform/` into `packages/archon-search/`. The extracted classes implement `archon-search`'s own internal service lifecycle interface — not Archon's `PlatformService` ABC.
5. Add a `archon-search` CLI entry point (`start`, `stop`, `status`, `install`, `config show/get/set`) — fully independent of the `archon` CLI.
6. Remove direct imports of `Embedder` and `MultiCollectionRouter` from `archon/ai/search_context_provider.py`; replace with HTTP calls to the new `POST /route` endpoint on the Search server API (see Key Decisions — HTTP routing architecture and HTTP API Surface).
7. Rewrite all 10 MCP tools in `archon/ai/archon_toolkit_search.py` as HTTP proxy calls to the Search server REST control plane. No MCP tool in Archon should import `archon.search.*` after extraction. (This is a distinct deliverable from step 6 — it covers the control-plane tools, not the context-provider routing path.)
8. Remove `archon/cli/search_cmd.py` direct imports of Search internals; replace with HTTP client calls or delegate to the `archon-search` CLI.
9. Define canonical domain types inside `archon-search`: `Query`, `Result`, `Collection`, `Document`, `Chunk`, `Namespace`, `IngestJob`, `ReindexJob`, `DeleteJob`.
10. Add first-class async job model: every long-running operation (ingest, reindex, delete) returns a job ID, supports status polling, and supports cancel (see Job Model API section).
11. Extend chunk storage schema with typed metadata fields (see Metadata Schema section).
12. Archon retains a `[search]` section in its `config.toml` containing only client-facing settings: `url`, optional credentials placeholder, `enabled`. When `enabled=false`: all `search_*` MCP tools are still registered but return a clear error message ("Search is disabled in config"); `archon doctor` shows Search as "disabled" without calling the HTTP API; `IndexingNotificationMonitor` does not start; `archon/ai/search_context_provider.py` returns empty context without calling `/route`.
13. Write a data directory migration guide for existing users: create `~/.archon/archon-search.toml` manually; old `[search]` internal keys in Archon's `config.toml` are ignored with a deprecation warning.

## In Scope

- `packages/archon-search/` monorepo structure with own `pyproject.toml` and version
- `archon-search.toml` standalone config with its own schema and loading logic, at `~/.archon/archon-search.toml`
- `archon-search` CLI entry point: `start`, `stop`, `status`, `install`, `config`
- Service management classes moved from `archon/platform/` into `packages/archon-search/`
- Removal of all direct Search internal imports from Archon core (config loader, embedder, router, sync helpers, MCP tools, gateway, doctor)
- Removal of all Archon internal imports from Search (config loader, CLI console, platform runtime, AI constants)
- Canonical domain type definitions inside `archon-search` (Query, Result, Collection, Document, Chunk, Namespace, Job)
- First-class async job model for ingest, reindex, delete operations (job ID, status, cancel)
- Rich metadata schema: system + filterable + ranking + audit fields
- Archon client adapter (`archon/ai/search_client.py`) that speaks only HTTP/MCP — no Search internals
- Data directory and config migration guide (step 13 — manual config creation, no automated migration command)
- Tests: all existing search tests migrated to `packages/archon-search/tests/`; Archon-side tests updated to mock HTTP boundary
- Static import-boundary lint tests (see In Scope — linting)
- HTTP API Surface endpoints for all current Search functionality (see HTTP API Surface section)

### Import-boundary lint tests (required, enforced in CI)

- `packages/archon-search/tests/test_import_boundary.py`: fails CI if any file under `packages/archon-search/archon_search/` contains `from archon.` or `import archon.` (excluding `archon_search` itself).
- `tests/test_import_boundary.py` (Archon-side): fails CI if any file under `archon/` or `tests/` imports `archon_search.*` directly — **except** `archon/ai/search_client.py`, which is the designated client adapter and is explicitly exempted from this rule. All other files in `archon/` and `tests/` must not import `archon_search.*`. The lint exempts exactly `archon/ai/search_client.py` and no other file.
- The lint is implemented using Python's `ast` module in a pytest test file — no third-party lint tool required. The test walks all `.py` files in the target directory and checks `import` and `from ... import` statements. This is simpler and more portable than `import-linter` or `ruff` rules for this purpose.

## Out of Scope

- Auth and namespace isolation (P0 item 5 — separate brief)
- Evaluation harness (P0 item 4 — separate brief)
- REST API with OpenAPI spec (P0 item 6 — separate brief)
- Metadata filter support at query time (P1 item 7 — depends on metadata schema from this brief)
- Any P1+ retrieval features (HyDE, RAG Fusion, routing improvements)
- PyPI publication — extraction and local monorepo structure only; version scheme is established (see Key Decisions — independent versioning)

## Key Decisions

- **Monorepo**: `packages/archon-search/` with own `pyproject.toml`. Repo split can happen later once the boundary is stable; adding cross-repo coordination cost during extraction would slow it down without benefit.

- **Config path**: `archon-search.toml` lives at `~/.archon/archon-search.toml` — sibling to Archon's data dir, preserving the single-dir UX. Archon's `config.toml` retains only a `[search]` client block (`url`, `enabled`). All current `[search]` section fields (collections, pinned_collections, embedding_model, db_path, etc.) move to `archon-search.toml`. Existing users create `~/.archon/archon-search.toml` manually following the migration guide. Old `[search]` internal keys remaining in Archon's `config.toml` are ignored with a deprecation warning — no automated migration command.

- **HTTP routing architecture**: `search_context_provider.py` currently imports `Embedder` and `MultiCollectionRouter` directly and performs local (in-process) fastembed embedding. After extraction, a new server-side `POST /route` endpoint accepts `{query: str, pinned_collections: list[str], slots: int}` (where `slots` is a per-request override for `routing_shortlist_size` — how many collections to return; if omitted, the server uses its configured `routing_shortlist_size`) and returns **routing metadata only**: an ordered list of collection names (shortlist) and a `routable_names` flag. It does NOT return chunk results. Actual chunk retrieval happens through the existing FastMCP JSON-RPC search endpoint. A full Search request from Archon therefore involves two calls: (1) `POST /route` to get the collection shortlist, (2) FastMCP search on those collections. FastMCP JSON-RPC is the canonical search query interface and is NOT replaced by REST in this brief. The embedding for routing happens server-side. The `MultiCollectionRouter`'s stateful fields (`last_routable_names`, `decomposer_was_invoked`) remain server-side state. Archon's `search_client.py` calls this endpoint with a 10-second timeout. The routing embed round-trip (~20–50ms over localhost) is the target — see Edge Cases for latency measurement requirement.

- **`pinned_collections` ownership**: `pinned_collections` moves to `archon-search.toml` (server-side config). Archon passes `pinned_collections` as a field in every search and route request; the server always-includes pinned collections. This keeps routing decisions server-side. ("Namespaces" is a future concept, addressed in P0 item 5 — this field is `pinned_collections` everywhere: in the config key, the HTTP API request field, and search requests.)

- **Routing config knob ownership**: `routing_shortlist_size`, `routing_confidence_threshold`, `max_parallel_collections`, and `auto_reindex_on_chunk_size_change` move to `archon-search.toml`. They are server-side routing parameters. The `/route` endpoint uses them server-side; Archon does not control them. The `slots` parameter in `POST /route` is a per-request override for `routing_shortlist_size`; if omitted, the server uses its configured `routing_shortlist_size`.

- **HTTP server bind address**: By default, the Search HTTP server binds to `127.0.0.1` only (not `0.0.0.0`). This prevents local network access until auth is implemented (P0 item 5). The bind address is configurable via `archon-search.toml [server] host` but defaults to `127.0.0.1`.

- **FastMCP vs REST split**: FastMCP JSON-RPC is retained as the canonical search query interface (the endpoint that retrieves chunks). It is NOT replaced in this brief. REST HTTP endpoints (`/route`, `/ingest`, `/jobs/*`, `/health`, `/indexing-state`, `/collections/*`) are the control plane. The 10 MCP tools in `archon_toolkit_search.py` are rewritten as HTTP calls to the REST control plane — they do NOT use FastMCP internally. FastMCP is used only for the search-query path.

- **`description_generator.py` LLM dependency**: `archon/search/description_generator.py` moves to `archon-search` with its own dependency on `claude-agent-sdk`. `archon-search` adds `claude-agent-sdk` to its `pyproject.toml` dependencies. The `_get_env_lock` helper is inlined (it is a small asyncio lock utility). If `ANTHROPIC_API_KEY` is not set or description generation fails for any reason, `archon-search` gracefully falls back to using the collection path as the description. Description generation is an enhancement, not a hard requirement — no crash if the key is absent.

- **`notification_monitor.py` ownership**: `IndexingNotificationMonitor` moves to Archon's gateway — the monitor logic stays in Archon and calls `GET /indexing-state` via HTTP instead of reading internal state. The Search server exposes `GET /indexing-state`; Archon's gateway polls it every 10 seconds (matching the current file-poll cadence) and sends Telegram notifications. If the Search server is not running (connection refused), the monitor logs a warning at DEBUG level and retries — no error spam. The stop condition is unchanged: all collections in a terminal state (`DONE` or `FAILED`) triggers a Telegram summary, after which the monitor stops polling for that trigger. `IndexingNotificationMonitor` is removed from `archon-search` entirely.

- **`IndexingStateStore` / doctor post-separation**: `archon doctor` calls `GET /indexing-state` on the Search HTTP API. If Search is not running, doctor reports "Search: not running" without reading internal files. The `IndexingStateStore` JSON file is Search-internal; Archon never reads it directly after extraction.

- **Domain type sharing**: Archon imports `archon-search` as a Python dependency via path reference (`{ path = "../../packages/archon-search" }` in Archon's `pyproject.toml`). Only `archon/ai/search_client.py` (the designated client adapter) may import `archon_search` domain types directly. All other files under `archon/` and `tests/` must not import `archon_search.*` — this is enforced by the import-boundary lint. This is a soft coupling at the type level, acknowledged and accepted during the monorepo phase because types are stable and versioned. If Search ever moves to a separate repo, a dedicated `archon-search-client` PyPI package replaces the path dependency.

- **Platform ABCs**: `archon.platform.PlatformService` and `PlatformRuntime` ABCs are NOT shared. `archon-search` defines its own minimal service lifecycle interface internally. The extracted service classes implement the `archon-search`-internal interface. Archon's `archon/platform/` retains only Archon-specific service management.

- **`install.py` dependencies**: `Console` is replaced with minimal `print()`-based UI inside `archon-search`. `GpuType` is extracted into `archon-search`'s own platform module. `get_runtime()` / `find_binary()` are duplicated with a minimal implementation in `archon-search` — small enough to duplicate cleanly.

- **Logging**: `archon-search` logs to `~/.archon/logs/archon-search.log` using `logging.getLogger("archon-search")`. Daily rotation matches Archon's. Log level configurable via `archon-search.toml [logging] level`. No `print()` in production code.

- **Watch mode**: The filesystem watcher (`watch=true`) stays in `archon-search` (it watches files and triggers ingest — both are Search server responsibilities). The `[search] watch` config key moves to `archon-search.toml`. Archon has no awareness of watch mode. Watch-triggered ingest becomes a job, logged the same way as manual ingest jobs.

- **Independent versioning**: `archon-search` versions independently using CalVer (`YY.M.N`). Archon's `pyproject.toml` pins a minimum version (`archon-search >= X.Y`). PyPI publication is deferred but the version scheme is established now. Archon's `release.sh` does NOT automatically bump `archon-search` — each package releases separately. During the monorepo phase, Archon and `archon-search` deploy in lockstep — the path dependency means Archon always uses the co-located `archon-search` regardless of the `>=` version pin. The version pin becomes operative only after PyPI publication. Users should not expect to mix Archon vX with `archon-search` vY until publication. The purpose of establishing CalVer now is to build the version infrastructure so publication requires no schema change.

- **Migration sequence (not atomic per-step)**: Some steps have circular dependencies — moving source (step 2) breaks importers, and replacing imports (step 6) requires the HTTP endpoint to already exist. Each step's PR must be CI-green, but green-at-every-commit within a step is not required. Steps that have circular dependencies use a strangler-fig pattern: introduce the replacement → migrate callers → remove the old code. This may span multiple commits within a step. Each logical step (1–13) must result in a green CI before the next step begins.

- **Job model breaking change**: MCP tools that previously returned synchronous results now return `IngestJob` (with `job_id` and `status`). This IS a breaking change to the MCP return schema. Callers using the Decomposer receive a new shape. Existing callers that depended on synchronous blocking must be updated. This is acknowledged and is not worked around via backward compat shims.

## HTTP API Surface

Endpoints the Search server must expose for Archon to integrate exclusively via HTTP/MCP.

**Split**: FastMCP JSON-RPC handles the **search query path** (chunk retrieval). REST HTTP handles the **control plane** (routing, ingest jobs, health, state, collections). The 10 MCP tools in `archon_toolkit_search.py` are rewritten as HTTP calls to the REST control plane — they do NOT use FastMCP internally. FastMCP is used only for the search-query path and is NOT replaced by any REST endpoint in this brief.

| Method | Path | Description |
|---|---|---|
| `POST` | `/route` | New. Accepts `{query: str, pinned_collections: list[str], slots: int}`. `slots` is a per-request override for `routing_shortlist_size` (how many collections to return); omit to use the server default. Returns **routing metadata only**: ordered list of collection names (shortlist) and `routable_names: bool`. Does NOT return chunk results — actual retrieval uses FastMCP JSON-RPC. Embedding happens server-side. |
| `POST` | `/ingest` | New async. Accepts document(s) or path. Returns `IngestJob` with `job_id` and `status`. |
| `GET` | `/jobs/{job_id}` | Returns job status + result. |
| `DELETE` | `/jobs/{job_id}` | Cancel job (best-effort). |
| `GET` | `/health` | Returns running/stopped state. |
| `GET` | `/indexing-state` | Returns per-collection indexing state as a JSON object. Each collection entry includes: `status` (one of `PENDING \| IN_PROGRESS \| DONE \| FAILED`), `files_total: int`, `files_indexed: int`, `collection_name: str`, `trigger: str`. This is the same data `IndexingStateStore` currently provides, now served over HTTP. `archon doctor` uses `status` + file counts to replicate its existing nuanced output (e.g., `⏳ partial (N/M files)` for `IN_PROGRESS`, `❌` for `FAILED`). Used by `archon doctor` and `IndexingNotificationMonitor`. |
| FastMCP JSON-RPC | existing | **Search query path** — retrieves chunks. Retained as-is and NOT replaced by REST in this brief. This is the endpoint `archon/ai/search_context_provider.py` calls after getting a collection shortlist from `POST /route`. |
| `GET/POST/DELETE` | `/collections/*` | Collection list, add, remove, info — existing or new endpoints. |

## Job Model API

- **Status enum**: `PENDING | RUNNING | DONE | FAILED | CANCELLED | CANCELLING`
- **Job ID**: UUIDv4. Jobs are persisted to `~/.archon/archon-search-jobs.json` and survive restarts. Old jobs are evicted from the state file after 7 days (eviction runs on startup and on every write, after the atomic write).
- **Persistence**: Writes use an atomic temp-file-then-rename pattern — never an in-place JSON overwrite. This prevents corruption if the process is killed mid-write.
- **Crash recovery on startup**: Any job found in `RUNNING` or `CANCELLING` state at startup is immediately transitioned to `FAILED` with reason `"process_restart"`. No orphaned jobs are left in non-terminal states after a restart.
- **Corrupt file handling**: If the jobs file cannot be parsed on startup, log the error and reset to an empty job store. No crash.
- **Cancel semantics**: Best-effort. Signals stop on the next chunk boundary. Partially written LanceDB rows are NOT rolled back and are immediately visible in search results — partial ingest is acknowledged. After cancel, the `doc_id`s of all chunks ingested before the cancellation point are recorded in the job's `result` field so callers know which documents were partially indexed. Status transitions to `CANCELLING` immediately, then `CANCELLED` when the worker stops.
- **`DELETE /jobs/{id}` idempotency**:
  - Job is `DONE` or `FAILED`: returns 200 (idempotent, no state change).
  - Job is `CANCELLED`: returns 200 (idempotent).
  - Job ID is unknown: returns 404.
  - Job is `RUNNING` or `PENDING`: initiates cancel, returns 202.
- **Watch-triggered ingest**: Also becomes a job, logged the same way as manual ingest jobs.
- **Breaking change**: MCP tools that previously returned synchronous results now return `IngestJob`. This is not backward-compatible. Callers must be updated.

## Metadata Schema

Concrete field definitions for chunk storage:

**System fields** (set by `archon-search` at ingest time, not user-supplied):
- `doc_id: str` — UUIDv4, stable per document
- `chunk_id: str` — UUIDv4, stable per chunk
- `source_path: str` — absolute path of the source file
- `indexed_at: str` — ISO-8601 UTC timestamp of initial ingest
- `file_type: str` — MIME type or extension
- `language: str | null` — detected language code; null if unknown

**Filterable fields** (user-supplied at ingest time):
- `metadata: dict[str, str]` — string keys, string values; max 50 fields per chunk; max 256 chars per key; max 4096 chars per value

**Ranking metadata** (user-supplied at ingest time):
- `custom_score: float | null` — additive ranking signal supplied at ingest time; null means ignored

**Audit fields** (set by `archon-search`):
- `ingested_by: str` — defaults to `"archon-search-cli"` or the MCP tool name; informational only (no auth yet)
- `updated_at: str` — ISO-8601 UTC timestamp of last update

**Schema evolution policy**: additive only. Missing fields in existing chunks default to `null` / empty dict. No forced reindex for schema additions. Breaking schema changes require a major version bump and explicit reindex.

## Definition of Done

All of the following must be true before this work is considered complete:

1. All existing search tests pass under `packages/archon-search/tests/`.
2. `uv run pytest tests/` (Archon-side) passes with Search internals mocked at the HTTP boundary — no test imports `archon_search.*` internals.
3. Import-boundary lint passes: no `from archon.` or `import archon.` in any file under `packages/archon-search/archon_search/`; no `archon_search.*` imports in Archon-side `archon/` or `tests/` except in `archon/ai/search_client.py` (the sole designated client adapter, explicitly exempted by the lint).
4. `archon-search start` succeeds on macOS and Linux with a minimal `archon-search.toml`.
5. `archon doctor` reports Search health via `GET /health` and `GET /indexing-state` HTTP calls — no internal imports.
6. `archon-search status` reports running/stopped state.
7. All Archon-side HTTP failure mode tests pass (see Edge Cases — HTTP failure modes).
8. Routing latency benchmark (p50/p95) measured and documented in the PR; p95 ≤ 150ms or the co-located embedder mode decision is recorded.
9. `description_generator` tests pass under `packages/archon-search/tests/`; Archon no longer imports it.
10. `IndexingNotificationMonitor` in Archon's gateway calls `GET /indexing-state`; existing Telegram notification behavior is preserved (existing tests pass).
11. The migration guide (step 13) is reviewed and accurate for a clean-install user.

## Edge Cases & Constraints

- **Existing users with data directories**: LanceDB tables, manifest files, and indexing state files stay in place. Only the config file path changes. Existing users create `~/.archon/archon-search.toml` manually following the migration guide.
- **Migration sequence**: Each logical step (1–13) must result in green CI before the next step begins. Within a step, multiple commits using a strangler-fig pattern are acceptable. See Key Decisions — migration sequence.
- **Archon `[search]` config section shrinks**: Any Archon config key that was Search-internal (collections, db_path, embedding_model) must be removed from Archon's schema after migration. Users who configured these fields receive deprecation warnings.
- **Test isolation**: Archon-side tests that currently import Search internals must be updated to mock the HTTP boundary. Import-boundary lint tests enforce this in CI.
- **HTTP failure modes**: Archon-side tests for `search_client.py` must cover:
  - Search server not running (connection refused → graceful degrade, no crash, returns empty context)
  - Search server timeout (10s default → logs warning, returns empty context)
  - Search server 5xx (logs error, returns empty context)
  - Malformed JSON response (logs error, returns empty context)
- **Routing latency**: Before the HTTP routing endpoint (`POST /route`) is shipped, measure p50/p95 query-time latency (current in-process baseline vs HTTP round trip). If p95 exceeds 150ms, consider a co-located embedder mode: Archon can optionally embed locally and pass the vector to the server's route endpoint. The 20–50ms estimate is a target, not a measured baseline.
- **`archon doctor` when Search is not running**: Must report "Search: not running" — not a crash or import error.

## Open Questions

*(Previously open questions resolved — see Key Decisions for independent versioning and PyPI publication decisions.)*

## Future Iterations

- Auth, authorization, and namespace isolation (P0 item 5)
- Evaluation harness and retrieval metrics (P0 item 4)
- Stable REST API with OpenAPI spec (P0 item 6)
- Metadata filter support at query time (P1 item 7) — unblocked by the metadata schema defined here
- `archon-search-client` PyPI package (if/when Search moves to a separate repo)

## Recommendation

This is the right work to do first. Without it, every subsequent P0–P5 item lands on top of an Archon subsystem instead of a standalone product — and that debt compounds. The hardest part is the config and client-side import removal: both touch many files and require a clean HTTP boundary where previously there was none. The coupling is more extensive than the original three sites suggested — the complete inventory above reveals 6 Search→Archon imports and 5 Archon→Search import clusters, including 10 MCP tools that bypass HTTP entirely. The metadata schema is the piece most likely to be underspecified at planning time; insist on concrete field definitions with types before implementation begins, or the schema will be revisited during every subsequent feature. Do not compromise on the clean break — no "Archon-privileged client" exceptions, enforced by automated import-boundary lint in CI.
