# FEAT-039b — Search query telemetry and privacy policy
**Purpose**: Implementation plan for opt-in, local-only JSONL query telemetry on the archon-search server, with a published privacy contract that FEAT-039c can extend without redesigning the consent envelope.
**Audience**: Implementers of FEAT-039b; reviewers of the privacy boundary.
**Status**: Draft

---

## Background

FEAT-039 delivered the offline evaluation harness — deterministic backends, gated metrics, baseline calibration, PR/release CI. It cannot, by design, see production traffic. FEAT-037 roadmap item 4 explicitly requires an online data-collection loop with defined query collection, labeling, and privacy boundaries before it can close.

The follow-up is split into two backlog items: FEAT-039b ships the consent envelope and minimum telemetry primitives; FEAT-039c ships relevance feedback capture, online loop, fixture promotion, and any external transmission. FEAT-039b lands first because every downstream piece inherits its privacy contract.

Source brief: `Documentation/Backlog/FEAT-039b-search-telemetry-and-privacy-brief.md` (rewritten after a cycle-1 iterative DA review that corrected the original hook target from a non-existent `routes_search.py` REST endpoint to the actual FastMCP tool layer, removed the assumption of an existing scheduler, and added a verifiable privacy regression test).

Repository structure (verified):
- `packages/archon-search/archon_search/server/mcp.py` — FastMCP tool host; `search`, `search_with_context`, and 7 other tools (ingest/sync/list/delete) registered as `@app.tool()` async wrappers around `SearchPipeline`.
- `packages/archon-search/archon_search/server/routes_route.py` — `POST /route` REST endpoint receiving `RouteRequest{query, slots}`. Existing exception handlers: `except asyncio.TimeoutError → HTTPException(504)`; validation errors raise `HTTPException(400)` directly.
- `packages/archon-search/archon_search/server/app.py` — FastAPI factory; lifespan already manages `app.state._background_tasks: set` with shutdown cancellation. **`mcp.py` is NOT imported from `app.py`**: the FastMCP app exists in the package but is not currently mounted into the production FastAPI server (only `archon_search.server.__main__` is the production entry point and it builds only the FastAPI app). The realistic production telemetry surface today is `POST /route`. The plan still hooks the MCP `search` / `search_with_context` tools so that, the moment FastMCP is mounted into FastAPI (a separate future effort), telemetry is already in place.
- `packages/archon-search/archon_search/config.py` — `SearchConfig` dataclass + `ConfigError` + `load_config()`. No `pydantic` direct dependency today — it arrives transitively via `fastapi`. FEAT-039b adds it as an explicit dependency.
- No scheduler module exists in archon-search. The retention pruner must be a new in-process periodic task.

## Goal

When `[telemetry].enabled = true` in `~/.archon/archon-search.toml`, every invocation of the MCP `search` and `search_with_context` tools and every `POST /route` request appends one JSONL line to today's `~/.archon/search-logs/YYYY-MM-DD.jsonl`. Entries include only the documented schema (no raw query text); failure cases produce explicit error entries. The retention pruner deletes files older than `retention_days` using filename-based age. The privacy boundary is enforceable in CI (an exhaustive-schema regression test fails any future field addition that was not deliberately approved). With `enabled = false` (the default), nothing is logged and nothing is created on disk.

---

## Scope

### In Scope
- New `[telemetry]` section in `SearchConfig` and `archon-search.toml`: `enabled: bool = false`, `retention_days: int = 30`, `export_enabled: bool = false`, `log_dir: str = "~/.archon/search-logs"` (the dir field exists so tests can inject `tmp_path` without monkey-patching `Path.home()`).
- `ConfigError` at config-load when `export_enabled = true`, with a logger WARNING from `archon.search`.
- New module `archon_search/telemetry/` with three files: `entry.py` (Pydantic model + factories + closed `Literal` set for `error_kind`), `writer.py` (queue + drain task + injectable clock + rotation + truncation + `_stopped` flag), `pruner.py` (filename-based daily delete + injectable `now`).
- Add `pydantic>=2.0` as an explicit dependency in `packages/archon-search/pyproject.toml` (today it arrives transitively via FastAPI; this commit makes the direct usage hygienic).
- Hook integration in `server/mcp.py` for `search` and `search_with_context` tools. Writer is passed as a parameter to `create_app(pipeline, default_collection, writer=None)` so the MCP factory takes ownership of the closure variable. The hook is dormant at runtime today (MCP is not mounted) but exercised by the unit tests for those tool functions; it activates the moment the MCP app is wired into the FastAPI server.
- Hook integration in `server/routes_route.py` for `POST /route`. The hook resolves the writer from `request.app.state.telemetry_writer`. The wrapping uses ordering `try / except asyncio.TimeoutError / except HTTPException / except Exception` and converts each branch into the correct entry kind BEFORE re-raising.
- Lifespan registration in `server/app.py` for the writer and pruner; the lifespan also threads the writer through to `create_mcp_app(...)` IF that factory is mounted (no behavior change today; provides the wiring point). Clean shutdown drain with bounded timeout.
- ADR `Documentation/ADRs/10_search_query_telemetry.md`.
- `packages/archon-search/README.md` Privacy section.

### Out of Scope
- Relevance feedback (API + UX) — FEAT-039c.
- External transmission / consent transport — FEAT-039c.
- Promotion of logged queries into FEAT-039 eval fixtures — FEAT-039c.
- Indexed analytics layer (SQLite/DuckDB) — FEAT-039c.
- Raw query text or query-hash logging — FEAT-039c.
- Hashed-doc-id mode — FEAT-039c.
- Read-back HTTP endpoint — FEAT-039c.
- A new REST `POST /search` endpoint — not part of FEAT-039b; the existing MCP tools are the search surface.

---

## Acceptance criteria

- [ ] `SearchConfig` has `telemetry_enabled`, `telemetry_retention_days`, `telemetry_export_enabled` fields with the correct defaults (or a `telemetry: TelemetryConfig` sub-dataclass — see Task 1.1).
- [x] Setting `[telemetry].export_enabled = true` in `archon-search.toml` raises `ConfigError` at `load_config()` time with a message containing the literal substring `"reserved for FEAT-039c"`.
- [ ] `TelemetryEntry` is a Pydantic v2 model with `model_config = ConfigDict(extra="forbid")`; any attempt to construct it with an unknown field raises `ValidationError`.
- [ ] Three factory classmethods exist and accept ONLY the safe fields (never a raw query string): `TelemetryEntry.from_search_tool_result()`, `TelemetryEntry.from_route_response()`, `TelemetryEntry.from_error()`.
- [ ] `TelemetryWriter` exposes `enqueue(entry: TelemetryEntry) -> None` (non-blocking, drops oldest on full queue) and is driven by a single background drain task that appends one JSON line per entry to `~/.archon/search-logs/YYYY-MM-DD.jsonl`.
- [ ] UTC day rollover causes the writer to close the previous file and open the next; rollover is detected per entry, not on a wall-clock timer.
- [ ] An entry whose JSON serialization exceeds 8 KiB has its `result_doc_ids` truncated until the line fits, and gains a `truncated: true` field; never written non-truncated above the limit.
- [ ] Writer errors (`OSError`, `ValueError`) are caught, logged via `archon.search` logger at WARNING level, and rate-limited to one warning per file path per minute. The search/route response is never affected.
- [ ] Pruner deletes only files whose filename date is older than `today (UTC) - retention_days`; today's file is never deleted. Age is parsed from the filename, never from `mtime` / `ctime`. A `now: date | None` kwarg permits deterministic tests.
- [ ] Pruner runs once at lifespan startup before the writer accepts entries, then once every 24h.
- [ ] When `telemetry.enabled = false`, no files appear under `~/.archon/search-logs/` and no writer / pruner tasks are spawned.
- [ ] MCP `search` and `search_with_context` tool calls — success and error paths — produce one entry per call when telemetry is enabled. The raw `query: str` argument never enters the entry factory.
- [ ] `POST /route` — success, validation error, and timeout paths — produces one entry per call. The schema variant is the routing variant (`collections: list[str]`, `decomposer_invoked: bool`).
- [ ] Lifespan shutdown drains the queue within a bounded timeout (default 2s); pending entries written, then writer task cancelled.
- [ ] A single source-of-truth constant `DOCUMENTED_SCHEMA_FIELDS: frozenset[str]` lives in `archon_search/telemetry/entry.py` and is consumed by BOTH the model-level exhaustive test (Task 1.3) AND the e2e key-set test (Task 3.5). The e2e test exercises every entry variant (search ok / search error / search_with_context ok / search_with_context error / route ok / route validation_error / route timeout / oversized → truncated) so that the union of observed keys EQUALS `DOCUMENTED_SCHEMA_FIELDS` — not merely a subset.
- [ ] A CI privacy regression test issues a search and a route call whose `query` argument contains a structurally distinct sentinel like `"PRIVACY-LEAK-SENTINEL-7f3a"` (hyphen-bearing so it cannot collide with a `query_id` or `doc_id` hex value). The test asserts the sentinel does not appear in (a) any file under the test's `log_dir`, AND (b) any captured `archon.search` logger record (via `caplog`). Both assertions are required — exception messages logged via `logger.exception(...)` could echo query text even when JSONL is clean.
- [ ] `error_kind` is typed as a closed `Literal` (`"empty_query" | "slot_out_of_range" | "timeout" | "internal_error" | "validation_error" | "other"`). Hook sites that catch arbitrary exceptions map to `"other"` and log the exception class name only via the `archon.search` logger, never into the JSONL line. A test enforces the closed-allowlist invariant.
- [ ] ADR 10 published; package README has a Privacy section that includes the explicit path-derived-doc_id risk statement.

---

## What does NOT change

- The MCP tool signatures (`search(query, collection)`, `search_with_context(query, collection, ...)`) are not altered. Telemetry wraps them; it does not change their inputs or outputs.
- The `POST /route` request/response shapes are unchanged.
- `SearchPipeline` is not touched. Telemetry construction lives in the server layer.
- Public `SearchResult` payload is unchanged.
- `app.state._background_tasks` shutdown semantics are unchanged (existing cancel-and-gather pattern is reused).
- Default behavior is unchanged: `telemetry.enabled = false`, no telemetry artifacts created.
- The MCP JSON-RPC envelope is unchanged.

---

## Known limitations / accepted trade-offs

- **Path-derived `doc_id` PII**: when telemetry is enabled, doc_ids derived from `source_path` (e.g., `/Users/<name>/Documents/<project>/<file>.md`) are logged. Operators accept this when they opt in. A hashed-doc-id mode is a FEAT-039c follow-up.
- **`export_enabled` flag is feature-gating, not a security boundary**: the v1 defense against external transmission is the absence of export code. The flag exists to force a deliberate revisit in FEAT-039c. The ADR states this explicitly.
- **Single-writer-per-process**: only one Search server process per `~/.archon/search-logs/` directory is supported in v1. Multi-process deployments would each need a process-distinct filename (`YYYY-MM-DD.<pid>.jsonl`) — documented as a v1 limitation, not implemented.
- **No structured failure logging beyond the redacted `error_kind`**: exception messages are not logged into telemetry because they can echo user input. Operational debugging is via the existing server logger.
- **No retention enforcement on file content** — old files are deleted whole; we do not rewrite or compact files mid-retention.
- **`enabled = false` short-circuits everything**: no writer task, no pruner task, no directory creation. Flipping the flag and restarting is required to start collecting.
- **MCP `search` / `search_with_context` hooks are dormant until MCP is mounted into FastAPI**: today only `routes_route.py` produces telemetry in production. The MCP tool hooks are unit-tested directly against the tool factory and ship pre-wired; they activate when the MCP app is mounted (a future, separate effort).
- **Other MCP tools NOT hooked** (`ingest_file`, `ingest_directory`, `list_collections`, `get_collections_meta`, `get_collection_meta`, `list_documents`, `delete_document` — 7 tools): intentionally out of scope. The brief is scoped to retrieval/routing telemetry. Adding ingest/admin tool telemetry would expand the `endpoint` schema and is left to a future feature.
- **Pruner cadence ≤ 24h drift**: if the server runs less than 24h between starts, the pruner runs at every startup. If it runs continuously for 24h+1m, files can exceed retention by up to 24h before the next pass. Acceptable for v1.
- **Drain-then-cancel-then-gather shutdown sequence is harmless**: after `drain_and_stop()` the writer task is `done()`. The lifespan's existing `task.cancel()` loop runs `cancel` on the already-done task (no-op), `asyncio.gather(..., return_exceptions=True)` handles whatever the task's final state is. Documented for reviewer clarity.

---

## Architecture

### New modules

- `packages/archon-search/archon_search/telemetry/__init__.py` — re-exports `TelemetryEntry`, `TelemetryWriter`, `Pruner`.
- `packages/archon-search/archon_search/telemetry/entry.py`:
  ```python
  from datetime import datetime
  from typing import Literal
  from pydantic import BaseModel, ConfigDict, Field

  EndpointKind = Literal["search", "search_with_context", "route"]
  Status = Literal["ok", "validation_error", "timeout", "internal_error"]
  ErrorKind = Literal[
      "empty_query",
      "slot_out_of_range",
      "timeout",
      "internal_error",
      "validation_error",
      "other",
  ]

  # Single source of truth for the privacy regression tests.
  DOCUMENTED_SCHEMA_FIELDS: frozenset[str] = frozenset({
      "query_id", "timestamp", "endpoint", "latency_ms", "status",
      "collection", "result_count", "result_doc_ids", "truncated",
      "collections", "decomposer_invoked",
      "error_kind",
  })

  class TelemetryEntry(BaseModel):
      model_config = ConfigDict(extra="forbid", frozen=True)

      # Common fields (all entries)
      query_id: str  # uuid4 hex
      timestamp: str  # ISO 8601 UTC with Z suffix
      endpoint: EndpointKind
      latency_ms: float
      status: Status

      # Retrieval-only (status="ok", endpoint in {"search","search_with_context"})
      collection: str | None = None
      result_count: int | None = None
      result_doc_ids: list[str] | None = None
      truncated: bool | None = None

      # Routing-only (endpoint="route")
      collections: list[str] | None = None
      decomposer_invoked: bool | None = None

      # Error-only (status != "ok")
      error_kind: ErrorKind | None = None

      @classmethod
      def from_search_tool_result(
          cls,
          *,
          endpoint: Literal["search", "search_with_context"],
          collection: str,
          result_doc_ids: list[str],
          latency_ms: float,
      ) -> "TelemetryEntry": ...

      @classmethod
      def from_route_response(
          cls,
          *,
          collections: list[str],
          decomposer_invoked: bool,
          latency_ms: float,
      ) -> "TelemetryEntry": ...

      @classmethod
      def from_error(
          cls,
          *,
          endpoint: EndpointKind,
          status: Status,
          error_kind: ErrorKind,  # closed Literal — callers must map raw exceptions to "other"
          latency_ms: float,
      ) -> "TelemetryEntry": ...
  ```
  The factories accept ONLY safe fields. They never receive `query: str` or any request body. This is the structural privacy guarantee.

- `packages/archon-search/archon_search/telemetry/writer.py`:
  ```python
  from collections.abc import Callable
  from datetime import datetime, UTC

  class TelemetryWriter:
      def __init__(
          self,
          log_dir: Path,
          *,
          queue_size: int = 1024,
          drain_timeout_s: float = 2.0,
          clock: Callable[[], datetime] = lambda: datetime.now(UTC),
      ): ...
      def enqueue(self, entry: TelemetryEntry) -> None:
          """Synchronous, non-blocking. Drops oldest on full queue (and calls
          task_done() on the dropped slot so queue.join() stays balanced).
          When `self._stopped`, the call is a silent rate-limited WARNING — no
          entry is queued. MUST NOT be awaited; defining it sync prevents future
          maintainers from inserting awaits between get_nowait and put_nowait."""
      async def start(self) -> asyncio.Task: ...
      async def drain_and_stop(self) -> None:
          """Sets self._stopped = True, awaits queue.join() with bounded
          timeout, then cancels the drain task. Idempotent."""
      # private:
      async def _run(self) -> None: ...
      def _serialize(self, entry: TelemetryEntry) -> bytes:
          """json.dumps(entry.model_dump(exclude_none=True), separators=(',',':')).encode('utf-8') + b'\\n'"""
      def _truncate_to_fit(self, entry: TelemetryEntry, limit_bytes: int = 8192) -> TelemetryEntry:
          """Constructs a modified copy via entry.model_copy(update={
              'result_doc_ids': shorter_list, 'truncated': True
          }) — frozen Pydantic models cannot be mutated in place."""
      def _file_for(self, when: datetime) -> Path:  # log_dir / f"{when.date().isoformat()}.jsonl"
      ...
  ```
  Single drain task per process. Internal queue is `asyncio.Queue(maxsize=queue_size)`. On full queue, the writer drops the OLDEST entry (`get_nowait()` then immediately `task_done()` then `put_nowait()`) and logs a rate-limited warning. UTC rollover detected per-entry by comparing `self._clock().date()` to the current open file's date — uses the injected clock callable so tests can drive rollover deterministically.

- `packages/archon-search/archon_search/telemetry/pruner.py`:
  ```python
  class Pruner:
      def __init__(self, log_dir: Path, retention_days: int): ...
      async def start(self) -> asyncio.Task: ...
      def prune_once(self, *, now: date | None = None) -> int:
          """Synchronous. Returns count of files deleted. Filename-based age only."""
      async def _run(self) -> None:  # call prune_once() then sleep 24h, forever, on shutdown CancelledError exits cleanly
  ```

### Config schema

`SearchConfig` gains a `telemetry` field:

```python
@dataclass
class TelemetryConfig:
    enabled: bool = False
    retention_days: int = 30
    export_enabled: bool = False  # rejected if True
    log_dir: str = "~/.archon/search-logs"  # configurable so tests can inject tmp_path

@dataclass
class SearchConfig:
    # ... existing fields ...
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
```

TOML section:
```toml
[telemetry]
enabled = false
retention_days = 30
# export_enabled is reserved for FEAT-039c; setting true raises ConfigError.
```

`load_config()` validates: when `export_enabled` is `True`, raise `ConfigError("[telemetry].export_enabled is reserved for FEAT-039c and must be false in v1")` and emit `logger.getLogger("archon.search").warning("telemetry: export attempt rejected")` before raising.

### Hook integration

**`server/mcp.py`** — wrap `search` and `search_with_context`:
- Read `writer: TelemetryWriter | None` from `app.state.telemetry_writer` (set in lifespan).
- Pattern (illustrative; real wrapping uses `monotonic()` for latency):
  ```python
  @app.tool()
  async def search(query: str, collection: str | None = None) -> ...:
      start = monotonic()
      try:
          results = await pipeline.search(query, collection or default_collection)
          if writer is not None:
              writer.enqueue(TelemetryEntry.from_search_tool_result(
                  endpoint="search",
                  collection=collection or default_collection,
                  result_doc_ids=[r.doc_id for r in results],
                  latency_ms=(monotonic() - start) * 1000.0,
              ))
          return ...
      except Exception as exc:
          if writer is not None:
              writer.enqueue(TelemetryEntry.from_error(
                  endpoint="search", status="internal_error",
                  error_kind="other", latency_ms=(monotonic() - start) * 1000.0,
              ))
          logger.exception("search failed")
          return ...
  ```
- `query` is never passed to any factory.

**`server/routes_route.py`** — same pattern at handler end. Validation errors (`HTTPException`) produce an error entry whose `error_kind` is mapped to the closed `ErrorKind` Literal by `_redact_validation()` (`"empty_query"`, `"slot_out_of_range"`, or fallback `"validation_error"`); the raw exception class name is NEVER passed as `error_kind` — internal exceptions map to `"other"` and the class name goes to the `archon.search` logger only.

**`server/app.py`** — lifespan:
```python
if config.telemetry.enabled:
    log_dir = Path(config.telemetry.log_dir).expanduser()
    log_dir.mkdir(parents=True, exist_ok=True)
    pruner = Pruner(log_dir, retention_days=config.telemetry.retention_days)
    # Wrap synchronous I/O so a large directory does not block the event loop.
    await asyncio.to_thread(pruner.prune_once)
    writer = TelemetryWriter(log_dir)
    app.state.telemetry_writer = writer
    app.state._background_tasks.add(await writer.start())
    app.state._background_tasks.add(await pruner.start())
else:
    app.state.telemetry_writer = None
yield
if config.telemetry.enabled:
    await writer.drain_and_stop()  # bounded; sets _stopped before joining
# existing cancel-and-gather follows; writer task is already done, pruner gets cancelled cleanly
```
The `log_dir` is read from `config.telemetry.log_dir` (defaults to `"~/.archon/search-logs"`). Tests inject a `tmp_path` via config instead of monkey-patching `Path.home()`.

### Data flow

```
MCP search call ─┐
MCP search_with_context call ─┤── handler computes latency_ms, builds TelemetryEntry via factory ──> writer.enqueue() ──> asyncio.Queue ──> single drain task ──> append JSONL line to today's file
POST /route call ─┘                                                                                                                                       │
                                                                                                                                                          └──> on full queue: drop oldest, rate-limited WARNING
                                                                                                                                                          └──> on OSError/ValueError: rate-limited WARNING, response unaffected

Daily 00:00 UTC pass (and lifespan startup):
  pruner.prune_once() ── scan ~/.archon/search-logs/*.jsonl ── parse YYYY-MM-DD from filename ── delete those older than today - retention_days
```

### Config keys introduced

| Key | Type | Default |
|---|---|---|
| `[telemetry].enabled` | bool | `false` |
| `[telemetry].retention_days` | int | `30` |
| `[telemetry].export_enabled` | bool | `false` (rejected if `true`) |

### Logger conventions

- All telemetry-internal warnings: `logging.getLogger("archon.search")` at WARNING level.
- Rate limit: one WARNING per `(filepath, error_kind)` pair per 60 seconds. Implemented as a tiny in-memory counter (no new dependency).

---

## Tests

Each task lists its own. The full set:

- **`test_telemetry_config_defaults`** (unit): `SearchConfig()` has `telemetry.enabled=False`, `retention_days=30`, `export_enabled=False`.
- **`test_telemetry_config_parses_toml`** (unit): `[telemetry] enabled = true / retention_days = 7` loads correctly.
- **`test_telemetry_config_rejects_export_enabled_true`** (unit): `[telemetry] export_enabled = true` raises `ConfigError` containing `"reserved for FEAT-039c"`.
- **`test_telemetry_config_emits_warning_on_export_rejection`** (unit): the rejection path emits the WARNING log line via `archon.search` logger.
- **`test_entry_extra_forbid_blocks_unknown_field`** (unit): `TelemetryEntry(unknown_field=...)` raises `ValidationError`.
- **`test_entry_schema_is_exhaustive`** (unit/contract): the model field set is EXACTLY the documented schema (privacy regression guard).
- **`test_from_search_tool_result_populates_retrieval_fields`** (unit): the factory sets `endpoint`, `collection`, `result_count`, `result_doc_ids`, `status="ok"`; leaves routing-only fields as `None`.
- **`test_from_route_response_populates_routing_fields`** (unit): the factory sets `endpoint="route"`, `collections`, `decomposer_invoked`, `status="ok"`; retrieval-only fields stay `None`.
- **`test_from_error_populates_error_fields`** (unit): `endpoint`, `status`, `error_kind`, `latency_ms`; never a query field.
- **`test_factory_signatures_reject_raw_query_argument`** (unit/contract): inspect the signatures via `inspect.signature` and assert no parameter named `query`, `query_text`, or `body` exists on any factory.
- **`test_writer_enqueues_and_writes_one_line_per_entry`** (integration): three entries → exactly three JSON lines in today's file.
- **`test_writer_appends_to_existing_file`** (integration): existing file gets new lines appended; existing lines preserved.
- **`test_writer_rolls_over_at_utc_midnight`** (integration): with an injected clock, an entry just after rollover lands in the next day's file.
- **`test_writer_drops_oldest_on_full_queue`** (integration): fill the queue, enqueue one more, oldest discarded; rate-limited WARNING emitted once.
- **`test_writer_truncates_oversized_entry`** (integration): entry with 1000 long doc_ids → JSONL line ≤ 8 KiB and contains `"truncated": true`.
- **`test_writer_swallows_oserror_and_continues`** (integration): force `OSError` on append → WARNING logged, writer still alive, next entry written.
- **`test_writer_drain_on_shutdown_flushes_pending`** (integration): enqueue N entries, call `drain_and_stop()`, all N present in file.
- **`test_writer_drain_respects_bounded_timeout`** (integration): wedge the file (read-only); `drain_and_stop()` returns within 2.5s; entries that couldn't flush are logged as lost.
- **`test_pruner_deletes_files_older_than_retention`** (unit): create files dated 0/15/29/30/31/60 days ago; with `retention_days=30`, only the 31 and 60-day files are deleted.
- **`test_pruner_never_deletes_today_or_future`** (unit): files dated today and tomorrow remain, regardless of `mtime`.
- **`test_pruner_uses_filename_not_mtime`** (unit): manipulate `mtime` on a filename-dated-today file to look 60 days old; pruner keeps it.
- **`test_pruner_skips_non_jsonl_files`** (unit): a `README.md` in the directory is untouched.
- **`test_pruner_skips_malformed_filenames`** (unit): a `not-a-date.jsonl` is left alone, no exception raised.
- **`test_pruner_runs_at_startup_before_accepting`** (integration): with seeded old files, app startup deletes them before the writer is started.
- **`test_lifespan_does_not_create_dir_when_disabled`** (integration): `enabled=false` → no `~/.archon/search-logs/`.
- **`test_lifespan_starts_writer_and_pruner_when_enabled`** (integration): both tasks present in `_background_tasks`; `telemetry_writer` set on `app.state`.
- **`test_lifespan_drains_writer_on_shutdown`** (integration): enqueue → shutdown → all entries persisted.
- **`test_search_tool_logs_entry_on_success`** (integration): MCP `search` call with telemetry enabled produces one retrieval entry.
- **`test_search_with_context_tool_logs_entry_on_success`** (integration): same for `search_with_context`.
- **`test_search_tool_logs_error_entry_on_exception`** (integration): pipeline raises → one error entry with `error_kind="other"` (closed Literal; class name goes to `archon.search` logger separately).
- **`test_route_handler_logs_entry_on_success`** (integration): `POST /route` 200 → one routing entry.
- **`test_route_handler_logs_error_entry_on_validation_error`** (integration): empty query → 400 + one error entry `error_kind="empty_query"`.
- **`test_route_handler_logs_error_entry_on_timeout`** (integration): force timeout → one error entry `error_kind="timeout"`.
- **`test_disabled_telemetry_writes_no_files`** (integration): with `enabled=false`, run a search + a route call; no `~/.archon/search-logs/` created.
- **`test_handler_does_not_leak_query_text_into_log`** (integration/privacy): sentinel UUID inside `query` argument → the sentinel never appears anywhere under `~/.archon/search-logs/`.
- **`test_jsonl_key_set_matches_documented_schema`** (e2e/contract): run a representative mix of calls, parse every JSON line, assert the union of keys is a subset of the documented schema.
- **`test_full_telemetry_cycle_with_rotation_and_pruning`** (e2e): inject clock spanning 32 days, simulate calls daily, assert correct rotation + pruning + file count.
- **`test_readme_contains_path_derived_doc_id_warning`** (unit/contract): the `packages/archon-search/README.md` contains the documented path-PII warning substring.
- **`test_adr_10_exists_and_documents_required_sections`** (unit/contract): `Documentation/ADRs/10_search_query_telemetry.md` exists and contains headings for Status, Context, Decision, Consequences, and the privacy/export discussion.

---

## Documentation update

- [ ] `Documentation/ADRs/10_search_query_telemetry.md`, section: full ADR (Status, Context, Decision, Consequences, Privacy & path-PII, Export defense honesty), path: `Documentation/ADRs/10_search_query_telemetry.md`
- [ ] `packages/archon-search/README.md`, section: `## Privacy & Telemetry`, path: `packages/archon-search/README.md`
- [ ] `Documentation/Architecture/180_search_architecture.md`, section: `Evaluation harness (FEAT-039)` → extend with a `Telemetry (FEAT-039b)` subsection, path: `Documentation/Architecture/180_search_architecture.md`
- [ ] `Documentation/990_documentation_index_and_contribution_guide.md`, section: `Backlog` → add the plan file row, path: `Documentation/990_documentation_index_and_contribution_guide.md`

---

## Task breakdown

### Phase 1 — Config and data model
> **Releasable**: after Task 1.4 — the config and the typed entry surface are usable from tests, but nothing is hooked into the running server yet.

#### Task 1.1 — `TelemetryConfig` dataclass and `SearchConfig` integration
- [x] **File**: `packages/archon-search/archon_search/config.py`
- **Depends on**: nothing
- **Description**:
  - Add `@dataclass class TelemetryConfig` with four fields: `enabled: bool = False`, `retention_days: int = 30`, `export_enabled: bool = False`, `log_dir: str = "~/.archon/search-logs"`.
  - Add `telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)` to `SearchConfig`.
  - Add `pydantic>=2.0` to `packages/archon-search/pyproject.toml` `[project] dependencies` (the package already gets Pydantic transitively via FastAPI; this commit makes the direct usage explicit).
  - Extend `load_config()` to parse `[telemetry]` section into `TelemetryConfig`. Use existing `_coerce_int` helper and add a `_coerce_bool` helper if it does not yet exist.
  - Validate `retention_days >= 1`; raise `ConfigError("[telemetry].retention_days must be >= 1")` otherwise.
  - Validate `log_dir` is a non-empty string; raise `ConfigError("[telemetry].log_dir must be a non-empty string")` otherwise.
  - Leave save-back (`save_config`) untouched — telemetry is read-only from TOML, never written back.
- **Releasable**: `SearchConfig` exposes a fully typed `telemetry` block; missing TOML section yields the default config.
- **Tests (TDD)** — `packages/archon-search/tests/config/test_telemetry_config.py`:
  - Unit: `test_telemetry_config_defaults` — `SearchConfig().telemetry` has the documented defaults.
  - Unit: `test_telemetry_config_parses_toml` — `[telemetry] enabled = true / retention_days = 7` loads correctly.
  - Unit: `test_telemetry_config_missing_section_uses_defaults` — TOML without `[telemetry]` yields default `TelemetryConfig`.
  - Unit: `test_telemetry_config_rejects_retention_days_zero` — `retention_days = 0` raises `ConfigError`.
  - Unit: `test_telemetry_config_rejects_non_bool_enabled` — `enabled = "yes"` raises `ConfigError`.
  - Unit: `test_telemetry_config_parses_log_dir_override` — TOML `[telemetry] log_dir = "/custom/path"` is parsed correctly.
  - Unit: `test_telemetry_config_rejects_empty_log_dir` — `log_dir = ""` raises `ConfigError`.
  - Unit: `test_pyproject_has_explicit_pydantic_dependency` — read `pyproject.toml` and assert `pydantic` is in `[project] dependencies`.
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/config/test_telemetry_config.py -v`

#### Task 1.2 — `export_enabled` rejection in `load_config`
- [x] **File**: `packages/archon-search/archon_search/config.py`
- **Depends on**: Task 1.1
- **Description**:
  - In the `[telemetry]` parser, when `export_enabled` is `True`, log a WARNING via `logging.getLogger("archon.search")` with message `"telemetry: export attempt rejected"`, then raise `ConfigError("[telemetry].export_enabled is reserved for FEAT-039c and must be false in v1")`.
  - The WARNING must be emitted BEFORE the exception is raised so log capture works in tests.
  - `False` and absent values are silent (no warning).
- **Releasable**: misconfigured operators get an explicit failure and an audit trail.
- **Tests (TDD)** — `packages/archon-search/tests/config/test_telemetry_config.py`:
  - Unit: `test_telemetry_config_rejects_export_enabled_true` — raises `ConfigError`; message contains `"reserved for FEAT-039c"`.
  - Unit: `test_telemetry_config_emits_warning_on_export_rejection` — `caplog` captures the WARNING from logger `archon.search`.
  - Unit: `test_telemetry_config_export_enabled_false_silent` — no WARNING, no error.
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/config/test_telemetry_config.py -k export -v`

#### Task 1.3 — `TelemetryEntry` Pydantic model + schema constant
- [x] **File**: `packages/archon-search/archon_search/telemetry/entry.py` (new)
- **Depends on**: nothing
- **Description**:
  - Pydantic v2 `BaseModel` subclass `TelemetryEntry` with `model_config = ConfigDict(extra="forbid", frozen=True)`.
  - Fields exactly as listed in Architecture (common + retrieval-only + routing-only + error-only). Use `Literal` types for `endpoint`, `status`, and `error_kind` (the closed `ErrorKind` Literal). All optional fields default to `None`.
  - Export `DOCUMENTED_SCHEMA_FIELDS: frozenset[str]` constant from the same module — single source of truth used by both the unit-level exhaustive test and the e2e key-set test in Task 3.5.
  - No factories yet in this task — those are Task 1.4 to keep tasks small.
  - Create `packages/archon-search/archon_search/telemetry/__init__.py` re-exporting `TelemetryEntry`, `DOCUMENTED_SCHEMA_FIELDS`, `EndpointKind`, `Status`, `ErrorKind` for now.
- **Releasable**: callers can import and construct entries directly with full fields (still verbose; factories arrive in 1.4).
- **Tests (TDD)** — `packages/archon-search/tests/telemetry/test_entry_model.py`:
  - Unit: `test_entry_minimum_construction` — common fields only, optional fields default to `None`.
  - Unit: `test_entry_extra_forbid_blocks_unknown_field` — `TelemetryEntry(query_id="x", ..., unknown_field="leak")` raises `ValidationError`.
  - Unit: `test_entry_schema_is_exhaustive` — `set(TelemetryEntry.model_fields.keys()) == DOCUMENTED_SCHEMA_FIELDS` (equality; uses the shared module constant).
  - Unit: `test_documented_schema_fields_is_subset_of_model_fields` — defends the other direction; ensures the constant cannot reference a field that does not exist on the model.
  - Unit: `test_entry_endpoint_literal_rejects_unknown_value` — `endpoint="weird"` raises `ValidationError`.
  - Unit: `test_entry_status_literal_rejects_unknown_value` — `status="unknown"` raises `ValidationError`.
  - Unit: `test_entry_error_kind_literal_rejects_unknown_value` — `error_kind="LanceDBError"` raises `ValidationError` (closed `ErrorKind` set).
  - Unit: `test_entry_is_frozen` — assigning to a field after construction raises.
  - Unit: `test_entry_model_copy_update_works_on_frozen` — `entry.model_copy(update={"truncated": True})` returns a new instance with the update applied (validates the Task 2.2 mechanism).
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/telemetry/test_entry_model.py -v`

#### Task 1.4 — `TelemetryEntry` factories
- [x] **File**: `packages/archon-search/archon_search/telemetry/entry.py`
- **Depends on**: Task 1.3
- **Description**:
  - Add three `@classmethod` factories on `TelemetryEntry`: `from_search_tool_result`, `from_route_response`, `from_error`. Signatures exactly as in Architecture — keyword-only, no positional `query`/`body` arguments.
  - Each factory generates `query_id = uuid.uuid4().hex` and `timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")`.
  - `from_search_tool_result` computes `result_count = len(result_doc_ids)`.
  - `from_error` accepts any `endpoint` value and any non-"ok" `status`.
  - No `query: str` parameter anywhere — enforced by signature, checked in tests.
- **Releasable**: handler hooks can build entries through the safe construction path only.
- **Tests (TDD)** — `packages/archon-search/tests/telemetry/test_entry_factories.py`:
  - Unit: `test_from_search_tool_result_populates_retrieval_fields`.
  - Unit: `test_from_search_tool_result_computes_result_count_from_doc_ids`.
  - Unit: `test_from_route_response_populates_routing_fields`.
  - Unit: `test_from_error_populates_error_fields`.
  - Unit: `test_from_error_rejects_ok_status` — passing `status="ok"` raises `ValueError`.
  - Unit: `test_factory_signatures_reject_raw_query_argument` — for each factory, `inspect.signature(...)` parameters contain none of `{"query","query_text","body","request"}`.
  - Unit: `test_factories_emit_uuid_query_id` — `query_id` is 32 hex chars and unique across two calls.
  - Unit: `test_factories_emit_utc_z_timestamp` — `timestamp` ends with `"Z"` and parses as a UTC `datetime`.
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/telemetry/test_entry_factories.py -v`

---

### Phase 2 — Writer and pruner
> **Releasable**: after Task 2.4 — the writer and pruner can be driven from tests independently of any FastAPI integration.

#### Task 2.1 — `TelemetryWriter` core enqueue + drain loop
- [x] **File**: `packages/archon-search/archon_search/telemetry/writer.py` (new)
- **Depends on**: Task 1.3
- **Description**:
  - `class TelemetryWriter` with `__init__(self, log_dir: Path, *, queue_size: int = 1024, drain_timeout_s: float = 2.0, clock: Callable[[], datetime] = lambda: datetime.now(UTC))`. The `clock` parameter is required for deterministic rollover tests (Task 3.5 e2e cycle also relies on it).
  - Internal `asyncio.Queue[TelemetryEntry]` of bounded size. Internal `self._stopped: bool = False` flag and a `self._task: asyncio.Task | None = None` reference.
  - `enqueue(entry)` — synchronous (declared `def`, not `async def`; documented in the docstring that this MUST remain synchronous so no `await` can be inserted between the drop/put pair). Behavior:
    1. If `self._stopped`, rate-limited WARNING `"telemetry: enqueue after stop dropped entry"` (one per minute via internal monotonic counter), return.
    2. Try `self._queue.put_nowait(entry)`. On `asyncio.QueueFull`: `self._queue.get_nowait()` to drop oldest, then `self._queue.task_done()` to balance the join counter, then `self._queue.put_nowait(entry)`, then rate-limited WARNING.
  - `async def start()` — creates and returns the drain task; sets `self._task`.
  - `async def drain_and_stop()` — idempotent. Sets `self._stopped = True`, awaits `queue.join()` with `asyncio.wait_for(..., timeout=drain_timeout_s)`; on timeout, logs WARNING with the count of unfinished entries and cancels the drain task; on success, cancels the drain task (which is now idle).
  - `_run()` — loop: `get()` an entry, serialize to bytes via `_serialize`, append to today's file, `task_done()`. UTC rollover detected per entry by comparing `self._clock().date()` to the currently-open file's date. Exceptions of type `OSError`/`ValueError` caught and logged (rate-limited); the entry is `task_done()`d after the catch so `join()` stays balanced. Other exceptions propagate (writer task crashes — caller observes via `self._task.done()` / `self._task.exception()`). After a crash, any subsequent `enqueue()` calls fall into the `_stopped`-equivalent silent-drop path by virtue of the queue having no consumer.
  - `_serialize(entry)` — `(json.dumps(entry.model_dump(exclude_none=True), separators=(",", ":")) + "\n").encode("utf-8")`.
  - `_file_for(when: datetime)` — `log_dir / f"{when.date().isoformat()}.jsonl"`.
  - No truncation here yet — Task 2.2.
  - `mkdir(parents=True, exist_ok=True)` for `log_dir` on first write.
- **Releasable**: telemetry entries can be enqueued and flushed to disk in tests.
- **Tests (TDD)** — `packages/archon-search/tests/telemetry/test_writer.py`:
  - Integration: `test_writer_enqueues_and_writes_one_line_per_entry`.
  - Integration: `test_writer_appends_to_existing_file`.
  - Integration: `test_writer_rolls_over_at_utc_midnight` — construct `TelemetryWriter(..., clock=fake_clock)`; advance `fake_clock` across midnight; entries straddling the boundary land in different files.
  - Integration: `test_writer_drops_oldest_on_full_queue` — `queue_size=2`, enqueue 3, oldest gone, dropped_count = 1.
  - Integration: `test_writer_drop_balances_task_done_so_drain_does_not_hang` — fill queue, force ≥ `queue_size` drops, call `drain_and_stop()`, assert it completes within the bounded timeout (this would hang without the `task_done()` on the drop path).
  - Integration: `test_writer_dropped_count_warning_rate_limited` — 100 drops in 1 second produce one WARNING.
  - Integration: `test_writer_swallows_oserror_and_continues` — `monkeypatch` `open` to raise once; writer logs WARNING; next enqueue succeeds; queue join stays balanced.
  - Integration: `test_writer_crash_on_unexpected_exception_is_observable` — patch `_serialize` to raise `TypeError`; enqueue an entry; the drain task transitions to `done()` with `exception()` returning the `TypeError`; subsequent `enqueue()` calls are silent (no exception leaks to caller).
  - Integration: `test_writer_enqueue_after_stop_is_silent_drop` — call `drain_and_stop()`, then `enqueue()`; no exception, rate-limited WARNING emitted; nothing written.
  - Integration: `test_writer_drain_on_shutdown_flushes_pending` — enqueue 5, call `drain_and_stop()`, all 5 present.
  - Integration: `test_writer_drain_respects_bounded_timeout` — wedge the writer (patch `_run` inner loop to await a never-completing event); `drain_and_stop()` returns ≤ `drain_timeout_s + 0.5s`; WARNING logged with unfinished count.
  - Integration: `test_writer_drain_is_idempotent` — calling `drain_and_stop()` twice does not raise.
  - All tests use `tmp_path` as `log_dir`; none touch `~/.archon/search-logs/`.
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/telemetry/test_writer.py -v`

#### Task 2.2 — `TelemetryWriter` oversized-entry truncation
- [x] **File**: `packages/archon-search/archon_search/telemetry/writer.py`
- **Depends on**: Task 2.1
- **Description**:
  - Add `_truncate_to_fit(entry: TelemetryEntry, limit_bytes: int = 8192) -> TelemetryEntry`.
  - If serialized bytes ≤ `limit_bytes`, return entry unchanged.
  - Otherwise: produce a modified copy via `entry.model_copy(update={"result_doc_ids": shorter_list, "truncated": True})` — `TelemetryEntry` is `frozen=True`, so direct attribute assignment raises; `model_copy(update=...)` is the only correct mutation mechanism. Use binary search over `len(result_doc_ids)` to find the largest prefix that fits (avoids quadratic decrement-and-serialize).
  - If even an empty `result_doc_ids` doesn't fit (common fields alone exceed limit), raise `ValueError("entry exceeds MAX_ENTRY_BYTES even with empty result_doc_ids")` — this should be impossible with realistic schema sizes; the writer's outer `except ValueError` catches it and logs WARNING.
  - Apply truncation in `_run()` before write.
  - Constant `MAX_ENTRY_BYTES = 8192` exposed at module top.
- **Releasable**: writer never emits non-atomic-sized lines.
- **Tests (TDD)** — `packages/archon-search/tests/telemetry/test_writer.py`:
  - [x] Integration: `test_writer_truncates_oversized_entry` — 1000 doc_ids each 50 chars long → line ≤ 8192 bytes, `"truncated":true` field present.
  - [x] Integration: `test_writer_keeps_short_entry_untouched` — small entry has no `truncated` key in serialized form.
  - [x] Unit: `test_truncate_to_fit_binary_search_correctness` — given a known-large entry, result is the largest prefix that fits.
  - [x] Unit: `test_truncate_to_fit_raises_when_even_zero_doc_ids_too_large` — synthetic entry where common fields alone exceed limit; raises `ValueError`.
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/telemetry/test_writer.py -k truncate -v`

#### Task 2.3 — `Pruner.prune_once` (synchronous, filename-based)
- [ ] **File**: `packages/archon-search/archon_search/telemetry/pruner.py` (new)
- **Depends on**: nothing (independent of writer)
- **Description**:
  - `class Pruner(log_dir: Path, retention_days: int)`.
  - `prune_once(*, now: date | None = None) -> int` — synchronous. If `now is None`, `now = datetime.now(UTC).date()`. Scan `log_dir.glob("*.jsonl")`; for each file, parse the filename stem as `YYYY-MM-DD` (use `date.fromisoformat`). Skip files whose stem does not parse — log DEBUG, continue. Delete files where `file_date <= now - timedelta(days=retention_days)` AND `file_date != now`. Return count deleted.
  - Today's file (`file_date == now`) is NEVER deleted — defensive check beyond the `<= now - retention_days` boundary.
  - `OSError` during `unlink()` is caught, logged at WARNING, count not incremented.
- **Releasable**: callable from a CLI or a test to clean up old logs.
- **Tests (TDD)** — `packages/archon-search/tests/telemetry/test_pruner.py`:
  - Unit: `test_pruner_deletes_files_older_than_retention` — 0/15/29/30/31/60 days old, `retention_days=30` → 31 and 60 deleted.
  - Unit: `test_pruner_keeps_files_within_retention` — 0/15/29 untouched.
  - Unit: `test_pruner_never_deletes_today_or_future` — files dated today and tomorrow stay.
  - Unit: `test_pruner_uses_filename_not_mtime` — `os.utime(today_file, (very_old, very_old))` → today's file kept.
  - Unit: `test_pruner_skips_non_jsonl_files` — `README.md` left alone.
  - Unit: `test_pruner_skips_malformed_filenames` — `not-a-date.jsonl` left alone, no exception.
  - Unit: `test_pruner_handles_missing_directory_gracefully` — `log_dir` absent → returns 0, no exception.
  - Unit: `test_pruner_returns_delete_count` — count matches deleted files.
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/telemetry/test_pruner.py -v`

#### Task 2.4 — `Pruner.start` (background 24h loop)
- [ ] **File**: `packages/archon-search/archon_search/telemetry/pruner.py`
- **Depends on**: Task 2.3
- **Description**:
  - `async def start() -> asyncio.Task` — creates a task running `_run()`.
  - `async def _run()` — infinite loop: call `prune_once()` synchronously, then `await asyncio.sleep(86400)`. On `asyncio.CancelledError`, exit cleanly.
  - Note that lifespan startup (Task 3.4) will call `prune_once()` synchronously BEFORE `start()` so that the first prune is observable before the writer accepts entries.
- **Releasable**: pruner runs unattended once daily.
- **Tests (TDD)** — `packages/archon-search/tests/telemetry/test_pruner.py`:
  - Integration: `test_pruner_start_runs_prune_once_immediately` — patch `asyncio.sleep` to a barrier; verify `prune_once` was invoked before the first sleep.
  - Integration: `test_pruner_cancellation_exits_cleanly` — start, cancel, await; no exception leaks.
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/telemetry/test_pruner.py -k start -v`

---

### Phase 3 — Hook integration
> **Releasable**: after Task 3.5 — telemetry is live end-to-end when `enabled=true`; default `enabled=false` is unaffected.

#### Task 3.1 — Lifespan registration in `server/app.py`
- [ ] **File**: `packages/archon-search/archon_search/server/app.py`
- **Depends on**: Task 2.1, Task 2.4, Task 1.1
- **Description**:
  - In the `lifespan` async context manager, before `yield`:
    - If `config.telemetry.enabled`:
      - Resolve `log_dir = Path(config.telemetry.log_dir).expanduser()` (configurable for tests).
      - `log_dir.mkdir(parents=True, exist_ok=True)`.
      - Construct `pruner = Pruner(log_dir, config.telemetry.retention_days)`; `await asyncio.to_thread(pruner.prune_once)` — wrap the synchronous file I/O in a thread to avoid blocking the event loop, especially if `retention_days` is large or the directory is unexpectedly populated.
      - Construct `writer = TelemetryWriter(log_dir)`; `app.state._background_tasks.add(await writer.start())`.
      - `app.state._background_tasks.add(await pruner.start())`.
      - `app.state.telemetry_writer = writer`.
    - Else: `app.state.telemetry_writer = None`.
  - After `yield`, before the existing cancel-and-gather block:
    - If `app.state.telemetry_writer is not None`: `await writer.drain_and_stop()`. The writer task is then `done()`; the existing cancel-and-gather treats it as a no-op (cancel on done task, gather with `return_exceptions=True`).
- **Releasable**: writer + pruner exist on `app.state` when enabled; nothing changes when disabled.
- **Tests (TDD)** — `packages/archon-search/tests/server/test_lifespan_telemetry.py` (new):
  - Integration: `test_lifespan_does_not_create_dir_when_disabled` — `enabled=false` with `log_dir = str(tmp_path / "should-not-exist")`; after startup, the directory does not exist; `app.state.telemetry_writer is None`.
  - Integration: `test_lifespan_starts_writer_and_pruner_when_enabled` — `enabled=true` with `log_dir = str(tmp_path)`; both tasks in `_background_tasks`; writer on `app.state`.
  - Integration: `test_lifespan_runs_initial_prune_before_writer_starts` — seed old files in `tmp_path`; assert they are deleted before any enqueue is possible.
  - Integration: `test_lifespan_prune_runs_in_thread_not_event_loop` — patch `asyncio.to_thread` to record being called; assert it was invoked with `pruner.prune_once`.
  - Integration: `test_lifespan_drains_writer_on_shutdown` — enqueue 3 entries during the request phase, trigger shutdown, all 3 present in file.
  - All tests use `log_dir = str(tmp_path / "search-logs")` via the config field; none touch `~/.archon/`.
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/server/test_lifespan_telemetry.py -v`

#### Task 3.2 — Hook the MCP `search` tool
- [ ] **File**: `packages/archon-search/archon_search/server/mcp.py`
- **Depends on**: Task 3.1, Task 1.4
- **Description**:
  - In the `search` tool definition, capture `start = monotonic()` immediately after entry.
  - On the success path, after `await pipeline.search(...)` returns, if `writer := <resolve from closure or app context>` is not None, call `writer.enqueue(TelemetryEntry.from_search_tool_result(endpoint="search", collection=collection or default_collection, result_doc_ids=[r.doc_id for r in results], latency_ms=(monotonic()-start)*1000.0))`.
  - On the existing `except Exception` branch, if writer is not None, enqueue `TelemetryEntry.from_error(endpoint="search", status="internal_error", error_kind="other", latency_ms=(monotonic()-start)*1000.0)` BEFORE the existing `logger.exception(...)` call. The exception class name is logged ONLY via `logger.exception(...)` — never into the JSONL line — to keep the `ErrorKind` Literal closed.
  - The `query` parameter must never be passed to any factory or any logger call.
  - Writer is resolved via the FastMCP app's owning FastAPI app, which is accessible via the existing `create_mcp_app(pipeline, default_collection)` factory signature — extend the signature with `writer: TelemetryWriter | None = None` parameter; the FastAPI factory passes it through during construction.
- **Releasable**: every `search` MCP call produces a telemetry entry when enabled.
- **Tests (TDD)** — `packages/archon-search/tests/server/test_mcp_telemetry.py` (new):
  - Integration: `test_search_tool_logs_entry_on_success` — invoke tool against a stub pipeline; one retrieval entry produced with correct fields.
  - Integration: `test_search_tool_logs_error_entry_on_exception` — pipeline raises `RuntimeError`; one error entry with `error_kind="other"` (closed Literal; raw class names are NOT logged into the JSONL — they go to the `archon.search` logger only). Test also asserts the `archon.search` logger captured `"RuntimeError"` via `caplog`.
  - Integration: `test_search_tool_query_text_never_in_factory_args` — patch `TelemetryEntry.from_search_tool_result` to record kwargs; assert no kwarg value equals the sentinel query string.
  - Integration: `test_search_tool_does_not_log_when_writer_none` — `writer=None`; no exception, no file activity.
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/server/test_mcp_telemetry.py -k search_tool -v`

#### Task 3.3 — Hook the MCP `search_with_context` tool
- [ ] **File**: `packages/archon-search/archon_search/server/mcp.py`
- **Depends on**: Task 3.2
- **Description**:
  - Same pattern as 3.2 but the success-path entry uses `endpoint="search_with_context"` and the result list is the actual returned chunks (use the same `doc_id` projection).
  - Error path uses `endpoint="search_with_context"`.
- **Releasable**: both retrieval tools are instrumented.
- **Tests (TDD)** — `packages/archon-search/tests/server/test_mcp_telemetry.py`:
  - Integration: `test_search_with_context_tool_logs_entry_on_success`.
  - Integration: `test_search_with_context_tool_logs_error_entry_on_exception`.
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/server/test_mcp_telemetry.py -k search_with_context -v`

#### Task 3.4 — Hook `POST /route`
- [ ] **File**: `packages/archon-search/archon_search/server/routes_route.py`
- **Depends on**: Task 3.1, Task 1.4
- **Description**:
  - At handler entry, capture `start = monotonic()`.
  - Read `writer = request.app.state.telemetry_writer`.
  - Wrap the existing logic with the exception chain ordered as `try / except asyncio.TimeoutError / except HTTPException / except Exception`. The ordering matters: since Python 3.11, `asyncio.TimeoutError` is an alias for `builtins.TimeoutError`, which is a subclass of `OSError` and therefore of `Exception` — placing it AFTER `except Exception` makes the timeout branch unreachable. Also, the existing handler converts `TimeoutError` to `HTTPException(504)` BEFORE re-raising. To avoid mis-classifying timeouts as validation errors, the new wrap must catch `asyncio.TimeoutError` at the OUTERMOST level — BEFORE the existing internal handler runs. Refactor: remove the existing `except asyncio.TimeoutError → HTTPException(504)` block and reimplement it in the new wrapper as: timeout → enqueue error entry → raise `HTTPException(504, "routing timed out")`.
  - Branches:
    - Success: enqueue `TelemetryEntry.from_route_response(collections=resp.pinned_names + resp.routable_names, decomposer_invoked=resp.decomposer_invoked, latency_ms=(monotonic()-start)*1000.0)`.
    - `asyncio.TimeoutError`: enqueue `TelemetryEntry.from_error(endpoint="route", status="timeout", error_kind="timeout", latency_ms=...)`, then raise `HTTPException(504, "routing timed out")`.
    - `HTTPException` with `status_code == 400`: enqueue `TelemetryEntry.from_error(endpoint="route", status="validation_error", error_kind=_redact_validation(detail), latency_ms=...)` then re-raise. (Other `HTTPException` status codes are re-raised without a telemetry entry — we do not invent error kinds for paths that should not exist.)
    - Other `Exception`: enqueue `TelemetryEntry.from_error(endpoint="route", status="internal_error", error_kind="other", latency_ms=...)`. The Python exception class name is logged via `archon.search` logger only (NEVER passed into `error_kind`, since the closed `ErrorKind` Literal does not accept arbitrary class names — this enforces the privacy invariant that no user-input-echoing data reaches the JSONL line). Re-raise.
  - Define a tiny `_redact_validation(detail: str) -> ErrorKind` that maps known detail strings to closed-set codes (`"query must not be empty" -> "empty_query"`, `"slots must be >= 1" -> "slot_out_of_range"`); unknown details map to `"validation_error"`. Detail strings already do NOT contain query text in this handler — defensive only.
  - The `body.query` value never reaches any factory call or log statement; the wrapper has access to `body` but treats it as opaque.
- **Releasable**: routing telemetry is complete.
- **Tests (TDD)** — `packages/archon-search/tests/server/test_routes_route_telemetry.py` (new):
  - Integration: `test_route_handler_logs_entry_on_success` — `POST /route` 200 → one routing entry, `collections` is the union of pinned + routable.
  - Integration: `test_route_handler_logs_error_entry_on_empty_query` — `query=""` → 400 + entry `error_kind="empty_query"`.
  - Integration: `test_route_handler_logs_error_entry_on_invalid_slots` — `slots=0` → 400 + entry `error_kind="slot_out_of_range"`.
  - Integration: `test_route_handler_logs_error_entry_on_timeout` — patch route logic to raise `asyncio.TimeoutError`; 504 + entry `error_kind="timeout"`.
  - Integration: `test_route_handler_logs_error_entry_on_internal_exception` — patch to raise `RuntimeError`; JSONL entry `error_kind="other"`; `archon.search` logger captured `"RuntimeError"` separately.
  - Integration: `test_route_handler_query_text_never_in_factory_args` — sentinel query → recorded factory calls never contain it.
  - Integration: `test_route_handler_no_entries_when_writer_none` — `app.state.telemetry_writer=None`; no exception, no file activity.
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/server/test_routes_route_telemetry.py -v`

#### Task 3.5 — End-to-end privacy and shape regression tests
- [ ] **File**: `packages/archon-search/tests/server/test_telemetry_e2e.py` (new)
- **Depends on**: Task 3.2, Task 3.3, Task 3.4
- **Description**:
  - Spin up the full app with `enabled=true` and `log_dir = str(tmp_path / "search-logs")` via config. Issue a mix that exercises every entry variant: success search, failing search, success search_with_context, failing search_with_context, success route, route validation error, route timeout, oversized retrieval entry (forces truncation).
  - Read all JSONL lines back. Compute `observed_keys = union(line.keys() for line in entries)`.
  - Assert: `observed_keys == DOCUMENTED_SCHEMA_FIELDS` (equality, using the constant imported from `archon_search.telemetry.entry`). Equality — not subset — ensures every documented field appeared in at least one entry; a new always-None field added in the future would fail this test until exercised by a test variant.
  - Use a structurally distinct privacy sentinel: `SENTINEL = "PRIVACY-LEAK-SENTINEL-7f3a-feat-039b"` (hyphen + word characters so it cannot collide with `query_id` hex values or `doc_id` filesystem paths). Pass it as the `query` argument to each tool call.
  - Assert that `SENTINEL` does NOT appear in:
    1. The byte contents of any file under `log_dir` (read all files; substring check).
    2. Any record captured by `caplog.records` whose `name` starts with `archon.search` (string-match against `record.getMessage()` and the `exc_info` repr when present). Exception-message logging via `logger.exception(...)` can echo argument values; this assertion catches it.
  - The `test_full_telemetry_cycle_with_rotation_and_pruning` test uses the writer's injectable `clock` callable to advance 32 days; verifies files rotate per day and the pruner deletes files older than `retention_days` while keeping today + future.
- **Releasable**: privacy contract is enforced by CI; future regressions block PRs.
- **Tests (TDD)** — `packages/archon-search/tests/server/test_telemetry_e2e.py`:
  - E2E: `test_jsonl_key_set_equals_documented_schema` (equality with shared constant; exercises all entry variants).
  - E2E: `test_handler_does_not_leak_query_text_into_log` — sentinel NOT in any log file AND NOT in `caplog` records from `archon.search`.
  - E2E: `test_handler_does_not_leak_query_text_via_exception_message` — force the search pipeline to raise an exception whose message contains the query text (e.g., a downstream `RuntimeError(f"failed for query: {query}")`); assert SENTINEL still absent from JSONL AND from caplog (proves the error-path factory uses `"other"` not `str(exc)`).
  - E2E: `test_disabled_telemetry_writes_no_files` — `enabled=false`, run a mix of calls; `log_dir` either does not exist or is empty.
  - E2E: `test_full_telemetry_cycle_with_rotation_and_pruning` — 32-day injected clock; expected files exist; expected files were pruned.
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/server/test_telemetry_e2e.py -v`

---

### Phase 4 — Documentation
> **Releasable**: after Task 4.3 — operators can opt in safely with accurate docs.

#### Task 4.1 — ADR 10: Search query telemetry
- [ ] **File**: `Documentation/ADRs/10_search_query_telemetry.md` (new)
- **Depends on**: Task 3.5
- **Description**:
  - Sections: `Status`, `Context`, `Decision`, `Consequences`, `Privacy & path-derived doc_id risk`, `Why `export_enabled` is not a security boundary`, `Open questions / FEAT-039c hooks`.
  - State: opt-in default, JSONL on disk, no raw query text in v1, factories enforce structural privacy, single drain task, single-writer-per-process, file-age from filename, retention default 30 days, ADR explicitly acknowledges path-PII in doc_ids and that the real defense against external transmission is the absence of export code.
- **Releasable**: a reviewable, citable decision record.
- **Tests (TDD)** — `packages/archon-search/tests/telemetry/test_docs_contract.py` (new):
  - Unit: `test_adr_10_exists_and_documents_required_sections` — file exists; contains literal headings for Status, Context, Decision, Consequences, Privacy, and the `export_enabled` honesty statement substring (`"absence of export code"`).
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/telemetry/test_docs_contract.py -k adr_10 -v`

#### Task 4.2 — Package README privacy section
- [ ] **File**: `packages/archon-search/README.md`
- **Depends on**: Task 4.1
- **Description**:
  - Add a top-level `## Privacy & Telemetry` section: how to opt in (`[telemetry].enabled = true`), what is logged, what is not (no raw query text in v1), retention default, where files live, and the path-derived doc_id risk in operator-facing language.
  - Link to ADR 10.
- **Releasable**: operators have a single page that documents the privacy contract.
- **Tests (TDD)** — `packages/archon-search/tests/telemetry/test_docs_contract.py`:
  - Unit: `test_readme_contains_path_derived_doc_id_warning` — README contains the documented warning substring (e.g. `"doc_ids may reveal filesystem paths"`).
  - Unit: `test_readme_documents_opt_in_default` — README contains `"enabled = false"` or equivalent default statement.
  - Unit: `test_readme_links_to_adr_10` — README contains `"ADRs/10_search_query_telemetry.md"`.
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/telemetry/test_docs_contract.py -k readme -v`

#### Task 4.3 — Architecture doc + documentation index touches
- [ ] **File**: `Documentation/Architecture/180_search_architecture.md` and `Documentation/990_documentation_index_and_contribution_guide.md`
- **Depends on**: Task 4.1
- **Description**:
  - In `180_search_architecture.md`, under the existing "Evaluation harness (FEAT-039)" section (added by FEAT-039 Task 5.2), add a `Telemetry (FEAT-039b)` subsection naming the module, the hook points, the privacy stance, and a link to ADR 10.
  - In `990_documentation_index_and_contribution_guide.md`, add rows for `Documentation/Backlog/FEAT-039b-search-telemetry-and-privacy-plan.md` and `Documentation/ADRs/10_search_query_telemetry.md`.
- **Releasable**: 039b artifacts are discoverable from the index.
- **Tests (TDD)** — `packages/archon-search/tests/telemetry/test_docs_contract.py`:
  - Unit: `test_arch_doc_mentions_telemetry_section` — `180_search_architecture.md` contains the literal substring `"Telemetry (FEAT-039b)"` and the link to ADR 10.
  - Unit: `test_doc_index_includes_telemetry_plan_and_adr` — `990_documentation_index_and_contribution_guide.md` contains both new rows.
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/telemetry/test_docs_contract.py -k 'arch or doc_index' -v`
