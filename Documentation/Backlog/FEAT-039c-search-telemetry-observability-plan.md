# FEAT-039c — Search Telemetry Observability
**Purpose**: Implementation plan for read-back of locally-written search telemetry data: two HTTP endpoints, a `TelemetryReader` core module, a `SearchClient` extension, and an MCP `telemetry_stats` tool in `ArchonToolkit`.
**Audience**: Implementers of FEAT-039c; reviewers of the API surface.
**Status**: Draft

---

## Background

FEAT-039b shipped opt-in local JSONL telemetry: a `TelemetryWriter` that appends one entry per search/route call to `~/.archon/search-logs/YYYY-MM-DD.jsonl`, a `Pruner` for retention, and hooks in all three endpoint types. The data accumulates but is completely opaque — no consumer exists.

FEAT-039c adds the read path: two FastAPI endpoints (`GET /telemetry/stats` and `GET /telemetry/entries`) that parse the JSONL files on demand and return aggregated metrics or paginated raw entries. A `SearchClient` extension and an `ArchonToolkit` MCP tool bring the stats into Claude sessions for self-diagnostics.

The `export_enabled` flag, previously a hard `ConfigError` (placeholder guard), is softened to a WARNING + forced-False so operators no longer crash on start.

Source brief: `Documentation/Backlog/FEAT-039c-telemetry-observability-brief.md` (three-cycle iterative DA review resolved: response schemas, pagination semantics, SearchClient convention, `by_collection` fan-out, `asyncio.to_thread()` mandate, `success_rate` formula, filter AND semantics).

Repository structure (verified):
- `packages/archon-search/archon_search/config.py` — `TelemetryConfig` dataclass; `export_enabled` ConfigError at lines 186–195.
- `packages/archon-search/archon_search/telemetry/` — `entry.py` (`TelemetryEntry`, `EndpointKind`, `Status`, `ErrorKind`, `DOCUMENTED_SCHEMA_FIELDS`), `writer.py` (`TelemetryWriter`), `pruner.py` (`Pruner`).
- `packages/archon-search/archon_search/server/` — `app.py` (lifespan, router registration), `routes_route.py` (pattern for new routes), no `routes_telemetry.py` yet.
- `archon/ai/search_client.py` — `SearchClient` (httpx-based, returns `None` on any failure, 10s timeout).
- `archon/ai/archon_toolkit_search.py` — `_register_search_tools()` with schema + handler pattern for 10 existing tools.

---

## Goal

After FEAT-039c, any caller (HTTP, MCP, `SearchClient`) can query aggregated search performance stats from the running archon-search service. Raw telemetry entries are browseable via HTTP (`GET /telemetry/entries`) directly. `export_enabled = true` in config no longer crashes the service. The data FEAT-039b writes becomes visible without leaving the session.

---

## Scope

### In Scope
- `export_enabled` ConfigError → logged WARNING + field forced to `False` in memory
- `packages/archon-search/archon_search/telemetry/reader.py` — `TelemetryReader` class (file discovery, JSONL parsing, stats aggregation, filtering, pagination)
- `packages/archon-search/archon_search/server/schemas_telemetry.py` — Pydantic response models
- `packages/archon-search/archon_search/server/routes_telemetry.py` — `GET /telemetry/stats` and `GET /telemetry/entries`
- `app.py` router registration for `routes_telemetry.router`
- `archon/ai/search_client.py` — `telemetry_stats()` method
- `archon/ai/archon_toolkit_search.py` — `telemetry_stats` MCP tool registration
- CLAUDE.md and archon-search README documentation updates

### Out of Scope
- External transmission (`export_enabled` implementation) — FEAT-039d
- `POST /feedback` relevance capture — FEAT-039d or later
- `telemetry_entries` MCP tool — entries are too large for MCP output size limits
- Indexed analytics (SQLite/DuckDB)
- Optional query-text/hash logging
- Authentication on telemetry endpoints

---

## Acceptance criteria
- [ ] `export_enabled = true` in TOML no longer raises `ConfigError`; service starts; WARNING logged; `config.telemetry.export_enabled` is `False` in memory.
- [ ] `GET /telemetry/stats` returns the documented JSON shape with `schema_version: 1` when telemetry is enabled and JSONL files exist.
- [ ] `GET /telemetry/stats` returns `{"enabled": false}` (HTTP 200) when `telemetry.enabled = false`.
- [ ] `GET /telemetry/entries` returns paginated entries with `next_offset`, `total_in_window`, `skipped_lines`.
- [ ] All entry filters are AND'd; `since > until` returns HTTP 400.
- [ ] `endpoint`, `status`, `error_kind` values not in their respective enums return HTTP 422.
- [ ] Corrupted JSONL lines are skipped; `skipped_lines` in the response counts them; the request succeeds.
- [ ] `by_collection` fans out for routing entries: each element of `collections` list gets +1.
- [ ] `success_rate` is `null` when `total_queries == 0`; `count(status=="ok") / total_queries` otherwise.
- [ ] `latency_ms: {"p50": null, "p95": null}` when fewer than 1 entry (zero entries); nearest-rank values otherwise.
- [ ] Invariant holds: `sum(error_breakdown.values()) == sum(by_endpoint[e]["error"] for e in by_endpoint)`.
- [ ] `GET /telemetry/stats?since=2026-05-15&until=2026-05-14` returns HTTP 400.
- [ ] All JSONL reads run in `asyncio.to_thread()`; the FastAPI event loop is not blocked.
- [ ] `SearchClient.telemetry_stats()` returns `None` when service is unreachable or on any HTTP/network error; returns `{"enabled": false}` dict when telemetry is disabled (caller checks `enabled` key).
- [ ] `telemetry_stats` MCP tool returns a human-readable hint when result is `None`.
- [ ] Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/ -q` — all pass.
- [ ] Checkpoint: `uv run pytest --no-cov tests/ai/test_search_client.py tests/ai/test_archon_toolkit_search.py -q` — all pass (archon side).

---

## What does NOT change
- `TelemetryEntry` schema, `TelemetryWriter`, `Pruner` — no changes to the write path.
- `DOCUMENTED_SCHEMA_FIELDS` constant — entries are read back verbatim.
- Existing `SearchClient` methods and their `None`-on-failure convention.
- Existing MCP tools in `archon_toolkit_search.py` — new tool is additive.
- `app.state.telemetry_writer` — lifespan setup is unchanged; reader derives `log_dir` from config.

---

## Known limitations / accepted trade-offs
- **O(files) file reads, O(entries) parsing per request** — no caching or indexing. Adequate at local 30-day / <10K entry scale. Re-evaluate beyond 90 days or 100K entries.
- **`SearchClient` timeout (10s default) applies** — large date ranges may timeout; caller sees `None` as "unreachable". Acceptable for v1.
- **Offset pagination is stable only for oldest-first ordering** — entries are always returned oldest-first (write order). Entries appended after page 1 do not shift prior offsets.
- **`by_collection` `total` can exceed `total_queries`** — routing fan-out is intentional; documented in response.
- **`by_collection` has no `error` count** — error entries carry no collection field by construction.
- **POSIX concurrent write/read** — may miss the in-flight line; near-real-time is acceptable.

---

## Architecture

### New module: `packages/archon-search/archon_search/telemetry/reader.py`

`TelemetryReader` is a pure, synchronous, testable class. It is never imported by the write path (`writer.py`, `pruner.py`). HTTP handlers call it via `asyncio.to_thread()`.

```python
class TelemetryReader:
    def __init__(self, log_dir: Path, retention_days: int) -> None

    def resolve_dates(
        self, since: date | None, until: date | None
    ) -> tuple[date, date]
    # Applies defaults: until=today UTC, since=until-retention_days.
    # Clamps since to until-timedelta(days=retention_days) if earlier.
    # Raises ValueError if since > until after clamping.

    def files_in_range(self, since: date, until: date) -> list[Path]
    # Returns sorted list of log_dir/YYYY-MM-DD.jsonl files
    # whose stem date is in [since, until] inclusive.
    # Non-matching filenames silently skipped.
    # Returns [] if log_dir does not exist.

    def read_entries(
        self, since: date, until: date
    ) -> tuple[list[TelemetryEntry], int]
    # Reads all files from files_in_range(since, until).
    # FileNotFoundError per file → DEBUG log, skip.
    # OSError per file → WARNING log, skip.
    # Malformed JSON line → WARNING log, skipped_lines += 1.
    # Returns (entries_oldest_first, skipped_lines).

    def compute_stats(
        self,
        entries: list[TelemetryEntry],
        since: date,
        until: date,
        skipped_lines: int,
    ) -> dict[str, Any]
    # Returns the full stats response dict (schema_version=1, enabled=True).
    # success_rate: None when total==0, else ok_count/total.
    # latency_ms: {"p50": None, "p95": None} when len(entries) < 1 (zero entries).
    # Nearest-rank: idx = ceil(p/100 * n) - 1.
    # by_collection fans out routing entries.
    # error_breakdown: all 6 ErrorKind keys pre-populated (0 if absent).
    # Invariant: sum(error_breakdown.values()) == sum(ep["error"] for ep in by_endpoint.values()).

    def filter_entries(
        self,
        entries: list[TelemetryEntry],
        *,
        collection: str | None = None,
        endpoint: EndpointKind | None = None,
        status: Status | None = None,
        error_kind: ErrorKind | None = None,
    ) -> list[TelemetryEntry]
    # All conditions AND'd.
    # collection: matches e.collection==X OR (e.collections and X in e.collections).

    def paginate(
        self, entries: list[TelemetryEntry], offset: int, limit: int
    ) -> tuple[list[TelemetryEntry], int]
    # Returns (entries[offset:offset+limit], len(entries)).
    # total_in_window is before slicing.
```

### New module: `packages/archon-search/archon_search/server/schemas_telemetry.py`

Pydantic response models used by route handlers as return type annotations.

```python
class LatencyPercentiles(BaseModel):
    p50: float | None
    p95: float | None

class EndpointStats(BaseModel):
    total: int
    ok: int
    error: int

class CollectionStats(BaseModel):
    # Note: `total` counts can exceed `total_queries` at the response level
    # because routing entries fan out to multiple collections.
    total: int
    ok: int

class ErrorBreakdown(BaseModel):
    empty_query: int = 0
    slot_out_of_range: int = 0
    timeout: int = 0
    internal_error: int = 0
    validation_error: int = 0
    other: int = 0

class StatsResponse(BaseModel):
    schema_version: int = 1
    enabled: bool
    since: str | None = None
    until: str | None = None
    total_queries: int = 0
    success_rate: float | None = None
    skipped_lines: int = 0
    latency_ms: LatencyPercentiles = LatencyPercentiles(p50=None, p95=None)
    by_endpoint: dict[str, EndpointStats] = {}
    by_collection: dict[str, CollectionStats] = {}
    error_breakdown: ErrorBreakdown = ErrorBreakdown()

class EntriesResponse(BaseModel):
    schema_version: int = 1
    enabled: bool
    entries: list[dict[str, Any]]
    next_offset: int
    total_in_window: int
    skipped_lines: int = 0

class DisabledResponse(BaseModel):
    enabled: bool = False
```

### New module: `packages/archon-search/archon_search/server/routes_telemetry.py`

```python
router = APIRouter()

@router.get("/telemetry/stats")
async def get_telemetry_stats(
    request: Request,
    since: Annotated[date | None, Query()] = None,
    until: Annotated[date | None, Query()] = None,
) -> StatsResponse | DisabledResponse:
    # Returns DisabledResponse() if not config.telemetry.enabled.
    # Wraps reader.resolve_dates() in try/except ValueError → HTTPException(400).
    # Calls asyncio.to_thread(reader.read_entries, since_d, until_d).
    # Returns reader.compute_stats(...).

@router.get("/telemetry/entries")
async def get_telemetry_entries(
    request: Request,
    since: Annotated[date | None, Query()] = None,
    until: Annotated[date | None, Query()] = None,
    collection: Annotated[str | None, Query()] = None,
    endpoint: Annotated[EndpointKind | None, Query()] = None,
    status: Annotated[Status | None, Query()] = None,
    error_kind: Annotated[ErrorKind | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> EntriesResponse | DisabledResponse:
    # Returns DisabledResponse() if not enabled.
    # FastAPI validates endpoint/status/error_kind via Literal type coercion;
    # invalid string values yield HTTP 422 (unprocessable entity).
    # since > until → HTTP 400 via ValueError from resolve_dates (caught → HTTPException(400)).
    # Calls asyncio.to_thread(reader.read_entries, since_d, until_d).
    # Returns EntriesResponse with entries as model_dump() dicts.
```

### `app.py` change — router registration

```python
from archon_search.server.routes_telemetry import router as telemetry_router
app.include_router(telemetry_router)
```

### `archon/ai/search_client.py` additions

```python
async def telemetry_stats(
    self,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any] | None:
    # GET /telemetry/stats with optional since/until query params.
    # Returns None on any failure (timeout, connect error, HTTP error).
    # {"enabled": false} is parsed and returned as-is (caller checks enabled key).

# DEFERRED — FEAT-039d: no caller in this feature
async def telemetry_entries(
    self,
    since: str | None = None,
    until: str | None = None,
    collection: str | None = None,
    endpoint: str | None = None,
    status: str | None = None,
    error_kind: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any] | None:
    # GET /telemetry/entries with optional query params (omit None values from params).
    # Returns None on any failure.
```

### `archon/ai/archon_toolkit_search.py` addition

New schema `_TELEMETRY_STATS_SCHEMA` and handler `_handle_telemetry_stats` registered via `toolkit.register_tool("telemetry_stats", ...)`.

Handler returns:
- If `result is None`: `{"error": "no data available", "hint": "telemetry may be disabled — set [telemetry] enabled = true in archon-search.toml; or the service may be unreachable or the request timed out"}`
- If `result.get("enabled") is False`: `{"error": "telemetry is disabled", "hint": "set [telemetry] enabled = true in archon-search.toml"}`
- Otherwise: `json.dumps(result)`

---

## Tests

### Phase 1 — Config fix
- **`test_export_enabled_true_does_not_raise`** (unit): `load_config()` with `export_enabled = true` does not raise `ConfigError`.
- **`test_export_enabled_true_logs_warning`** (unit): `caplog` captures WARNING from `archon.search` logger.
- **`test_export_enabled_forced_false_in_memory`** (unit): `config.telemetry.export_enabled` is `False` after parsing `export_enabled = true`.
- **`test_export_enabled_false_silent`** (unit): `export_enabled = false` — no warning, no error.

### Phase 2 — TelemetryReader
- **`test_resolve_dates_defaults`** (unit): `since=None, until=None` → today UTC and `today - retention_days`.
- **`test_resolve_dates_clamps_since_to_retention`** (unit): `since` older than retention window is clamped.
- **`test_resolve_dates_raises_if_since_after_until`** (unit): `since > until` → `ValueError`.
- **`test_files_in_range_empty_dir`** (unit): no log_dir → returns `[]`.
- **`test_files_in_range_selects_correct_dates`** (unit): files outside [since, until] excluded.
- **`test_files_in_range_skips_non_matching_filenames`** (unit): `other.jsonl`, `foo.txt` skipped.
- **`test_read_entries_parses_valid_jsonl`** (unit): returns correct `TelemetryEntry` list and `skipped_lines=0`.
- **`test_read_entries_skips_malformed_lines`** (unit): bad JSON lines increment `skipped_lines`; valid lines still returned.
- **`test_read_entries_skips_missing_file`** (unit): `FileNotFoundError` → DEBUG logged, entry skipped.
- **`test_read_entries_empty_dir`** (unit): returns `([], 0)`.
- **`test_compute_stats_total_queries`** (unit): correct count.
- **`test_compute_stats_success_rate_zero_queries`** (unit): `success_rate` is `None`.
- **`test_compute_stats_success_rate_formula`** (unit): `ok_count / total`.
- **`test_compute_stats_latency_nearest_rank`** (unit): P50/P95 correct for known dataset.
- **`test_compute_stats_latency_null_when_no_entries`** (unit): `{"p50": None, "p95": None}` with 0 entries only (1 entry is sufficient for non-null percentiles).
- **`test_compute_stats_by_endpoint_counts`** (unit): total/ok/error per endpoint.
- **`test_compute_stats_by_collection_fan_out`** (unit): routing entry with `collections=["A","B"]` increments both.
- **`test_compute_stats_by_collection_excludes_error_entries`** (unit): error entries (no collection) not in by_collection.
- **`test_compute_stats_error_breakdown_all_keys_present`** (unit): all 6 ErrorKind keys present even with zero values.
- **`test_compute_stats_invariant_error_breakdown_vs_by_endpoint`** (unit): sum equality holds.
- **`test_filter_entries_by_endpoint`** (unit): filters correctly.
- **`test_filter_entries_by_status`** (unit): filters correctly.
- **`test_filter_entries_by_error_kind`** (unit): filters correctly.
- **`test_filter_entries_by_collection_singular`** (unit): matches `e.collection == X`.
- **`test_filter_entries_by_collection_routing_plural`** (unit): matches `X in e.collections`.
- **`test_filter_entries_and_semantics`** (unit): `status=ok + error_kind=timeout` → empty (contradictory).
- **`test_filter_entries_no_filter`** (unit): returns all entries unchanged.
- **`test_paginate_basic`** (unit): offset/limit slicing; total_in_window = unsliced count.
- **`test_paginate_last_page`** (unit): partial page returned; total_in_window unchanged.
- **`test_paginate_offset_beyond_end`** (unit): returns empty list; total_in_window still correct.

### Phase 3 — HTTP Endpoints
- **`test_stats_disabled_returns_enabled_false`** (integration): `config.telemetry.enabled = False` → `{"enabled": false}` HTTP 200.
- **`test_stats_no_files_returns_zeros`** (integration): enabled but empty log_dir.
- **`test_stats_returns_correct_values`** (integration): write 3 known JSONL lines (2 ok, 1 timeout error) spread across two files (`2026-05-13.jsonl` and `2026-05-14.jsonl`) to verify multi-file aggregation → assert `total_queries=3`, `success_rate≈0.667`, `latency_ms.p50` is the median value, `by_endpoint` has correct counts, `error_breakdown.timeout=1`.
- **`test_stats_since_after_until_returns_400`** (integration): `?since=2026-05-15&until=2026-05-14` → HTTP 400.
- **`test_stats_date_range_selects_files`** (integration): only files in range are read.
- **`test_entries_disabled_returns_enabled_false`** (integration): HTTP 200, `{"enabled": false}`.
- **`test_entries_pagination_offset_limit`** (integration): `offset=0&limit=2` → 2 entries, `next_offset=2`, correct `total_in_window`.
- **`test_entries_filter_by_status`** (integration): `?status=ok` filters correctly.
- **`test_entries_filter_by_endpoint`** (integration): filters correctly.
- **`test_entries_filter_by_error_kind`** (integration): filters correctly.
- **`test_entries_filter_by_collection`** (integration): matches both singular and plural fields.
- **`test_entries_invalid_status_returns_422`** (integration): `?status=error` (not a valid literal) → HTTP 422.
- **`test_entries_invalid_endpoint_returns_422`** (integration): `?endpoint=foo` → HTTP 422.
- **`test_entries_invalid_error_kind_returns_422`** (integration): `?error_kind=bad` → HTTP 422.
- **`test_entries_since_after_until_returns_400`** (integration): HTTP 400.
- **`test_entries_schema_version_present`** (integration): `schema_version == 1`.
- **`test_entries_only_documented_fields`** (integration): each returned entry (a) contains all required non-optional fields (`query_id`, `timestamp`, `endpoint`, `latency_ms`, `status`), and (b) has no keys outside `DOCUMENTED_SCHEMA_FIELDS`.
- **`test_stats_skipped_lines_counted`** (integration): JSONL file with one bad line → `skipped_lines == 1`.

### Phase 4 — SearchClient + MCP tool
- **`test_search_client_telemetry_stats_success`** (unit): mocked HTTP GET `/telemetry/stats` → returns parsed dict.
- **`test_search_client_telemetry_stats_returns_none_on_timeout`** (unit): `httpx.TimeoutException` → `None`.
- **`test_search_client_telemetry_stats_returns_none_on_connect_error`** (unit): `httpx.ConnectError` → `None`.
- **`test_search_client_telemetry_stats_passes_params`** (unit): `since="2026-05-01"` appears in GET params.
- **`test_search_client_telemetry_stats_omits_none_params`** (unit): `since=None` not in request params.
- **`test_search_client_telemetry_stats_returns_none_on_http_error`** (unit): mock server returns HTTP 500 → `raise_for_status()` raises `HTTPStatusError` → method returns `None`.
- **`test_telemetry_stats_tool_returns_stats_json`** (unit): mock SearchClient returns stats dict → MCP handler returns JSON string.
- **`test_telemetry_stats_tool_hints_when_none`** (unit): `SearchClient.telemetry_stats` returns `None` → handler returns hint JSON.
- **`test_telemetry_stats_tool_hints_when_disabled`** (unit): returns `{"enabled": false}` → handler returns disabled hint.
- **`test_telemetry_stats_tool_registered`** (unit): `"telemetry_stats"` in `ArchonToolkit` tool registry.
- **`test_telemetry_stats_tool_no_args`** (unit): call handler with `arguments={}` (no `since`/`until`) → `SearchClient.telemetry_stats(since=None, until=None)` is called → returns normal stats JSON.

---

## Documentation update
- [ ] `CLAUDE.md`, section: `[telemetry]` config description — update `export_enabled` line from "raises ConfigError (reserved for FEAT-039c)" to "logs WARNING and is ignored (reserved for FEAT-039d)"; add `GET /telemetry/stats` and `GET /telemetry/entries` to the archon-search HTTP surface, path: `CLAUDE.md`
- [ ] `packages/archon-search/README.md`, section: add `## Telemetry Read-Back API` subsection documenting the two new endpoints, their parameters, and response shapes, path: `packages/archon-search/README.md`

---

## Task breakdown

### Phase 1 — Config fix
> **Releasable**: after Task 1.1 — `export_enabled = true` in TOML no longer crashes the service.

#### Task 1.1 — `export_enabled` ConfigError → WARNING + forced False
- [x] **File**: `packages/archon-search/archon_search/config.py`
- **Depends on**: nothing
- **Description**:
  - In `load_config()`, find the block at lines 186–195 that raises `ConfigError` when `export_enabled` is `True`.
  - Replace the `raise ConfigError(...)` with: log WARNING via `_logger.warning("telemetry: export_enabled is reserved for FEAT-039d and will be ignored")`, then `telemetry.export_enabled = False` (force to `False` in memory).
  - The WARNING must be emitted before the assignment, so `caplog` captures it in tests.
  - `export_enabled = false` path: no warning, no error (already handled by existing `telemetry.export_enabled = export_enabled`).
  - Absent `export_enabled` key: no change (existing path).
  - No other changes to `config.py`.
- **Releasable**: operators with `export_enabled = true` in their TOML no longer crash on service start.
- **Tests (TDD)** — `packages/archon-search/tests/config/test_telemetry_config.py` (extend existing file):
  - Unit: `test_export_enabled_true_does_not_raise` — `load_config()` with `[telemetry] export_enabled = true` does not raise any exception.
  - Unit: `test_export_enabled_true_logs_warning` — `caplog` captures a WARNING record from logger `archon.search` containing `"export_enabled"`.
  - Unit: `test_export_enabled_forced_false_in_memory` — `config.telemetry.export_enabled` is `False` after parsing `export_enabled = true`.
  - Unit: `test_export_enabled_false_silent` — `export_enabled = false` emits no warning.
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/config/test_telemetry_config.py -k export -v`

---

### Phase 2 — JSONL Reader
> **Releasable**: after Task 2.3 — `TelemetryReader` is fully testable in isolation; HTTP handlers can be built on top of it.

#### Task 2.1 — `TelemetryReader` — file discovery and entry parsing
- [x] **File**: `packages/archon-search/archon_search/telemetry/reader.py` (new)
- **Depends on**: nothing
- **Description**:
  - `class TelemetryReader` with `__init__(self, log_dir: Path, retention_days: int) -> None`.
  - `resolve_dates(self, since: date | None, until: date | None) -> tuple[date, date]`:
    - `until` defaults to `datetime.now(UTC).date()` if `None`.
    - `since` defaults to `until - timedelta(days=self._retention_days)` if `None`.
    - Clamp `since` to `until - timedelta(days=self._retention_days)` if earlier.
    - Raise `ValueError("since must be before until")` if `since > until` after clamping.
  - `files_in_range(self, since: date, until: date) -> list[Path]`:
    - If `self._log_dir` does not exist, return `[]`.
    - Glob `self._log_dir.glob("*.jsonl")`, sort ascending.
    - Include only files where `date.fromisoformat(path.stem)` is in `[since, until]`.
    - Skip files where `path.stem` does not parse as a date (silent, no log).
    - Return sorted list.
  - `read_entries(self, since: date, until: date) -> tuple[list[TelemetryEntry], int]`:
    - Iterate `self.files_in_range(since, until)`.
    - Per file: catch `FileNotFoundError` → `logger.debug(...)`, skip; catch `OSError` → `logger.warning(...)`, skip.
    - Per line: strip, skip empty; `json.loads(line)` + `TelemetryEntry.model_validate(data)` — catch `Exception` → `logger.warning("telemetry: skipping malformed line in %s", path)`, `skipped_lines += 1`.
    - Return `(entries, skipped_lines)` where entries are in file-date ascending, then line-position ascending order.
  - Add `TelemetryReader` to `packages/archon-search/archon_search/telemetry/__init__.py` exports.
  - Logger: `logging.getLogger("archon.search")`.
- **Releasable**: `TelemetryReader` can read and parse JSONL files; usable in tests and HTTP handlers.
- **Tests (TDD)** — `packages/archon-search/tests/telemetry/test_reader.py` (new):
  - Unit: `test_resolve_dates_defaults` — `since=None, until=None` → today UTC and `today - retention_days`.
  - Unit: `test_resolve_dates_clamps_since_to_retention` — `since` older than window is clamped.
  - Unit: `test_resolve_dates_raises_if_since_after_until` — `since > until` → `ValueError`.
  - Unit: `test_files_in_range_empty_dir` — non-existent `log_dir` → `[]`.
  - Unit: `test_files_in_range_selects_correct_dates` — files outside `[since, until]` excluded; files inside included.
  - Unit: `test_files_in_range_skips_non_matching_filenames` — `other.jsonl`, `foo-bar.jsonl`, `notes.txt` skipped.
  - Unit: `test_read_entries_parses_valid_jsonl` — two valid JSONL lines → two `TelemetryEntry` objects, `skipped=0`.
  - Unit: `test_read_entries_skips_malformed_lines` — one bad JSON line → `skipped=1`, valid entry still returned.
  - Unit: `test_read_entries_skips_missing_file` — `FileNotFoundError` logged at DEBUG, not raised.
  - Unit: `test_read_entries_empty_dir` — returns `([], 0)`.
  - Unit: `test_read_entries_skips_oserror_file` — mock a `PermissionError` (which is `OSError`) on one file; assert it is logged at WARNING level (not DEBUG); assert other files' entries are still returned.
  - Unit: `test_resolve_dates_historical_until_uses_relative_window` — `resolve_dates(since=None, until=date(2020, 1, 1))` with `retention_days=30` returns `(date(2019, 12, 2), date(2020, 1, 1))` without error (since is clamped relative to `until`, not `today`).
  - Unit: `test_files_in_range_single_day_boundary` — `since == until == date(2026, 5, 14)` with file `2026-05-14.jsonl` present → result contains exactly that file.
  - Unit: `test_files_in_range_existing_empty_dir` — `log_dir.mkdir()` (exists but empty) → `files_in_range(...)` returns `[]`.
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/telemetry/test_reader.py -k "resolve_dates or files_in_range or read_entries" -v`

#### Task 2.2 — `TelemetryReader.compute_stats`
- [x] **File**: `packages/archon-search/archon_search/telemetry/reader.py`
- **Depends on**: Task 2.1
- **Description**:
  - `compute_stats(self, entries: list[TelemetryEntry], since: date, until: date, skipped_lines: int) -> dict[str, Any]`:
    - `total_queries = len(entries)`.
    - `success_rate = count(e.status == "ok") / total_queries` if `total_queries > 0` else `None`.
    - `latency_ms`: sort `[e.latency_ms for e in entries]`; nearest-rank `idx = math.ceil(p / 100 * n) - 1`; return `{"p50": ..., "p95": ...}`; if `len(entries) < 1` (zero entries), return `{"p50": None, "p95": None}`.
    - The `since` and `until` keys in the returned dict are serialized as `date.isoformat()` (YYYY-MM-DD strings).
    - `by_endpoint`: dict keyed by `e.endpoint`; each value `{"total": int, "ok": int, "error": int}`.
    - `by_collection`:
      - For each entry: if `e.collection` is not `None`, use `[e.collection]`; elif `e.collections` is not `None`, use `e.collections`; else skip (error entries).
      - Each collection in the list gets `+1 total`, `+1 ok` if `e.status == "ok"`.
    - `error_breakdown`: dict with all 6 `ErrorKind` literal values pre-populated at `0`; increment by `e.error_kind` for entries where `e.error_kind is not None`.
    - Invariant (enforced by correct counting, not asserted at runtime): `sum(error_breakdown.values()) == sum(ep["error"] for ep in by_endpoint.values())`.
    - Return dict matching the `StatsResponse` JSON schema (keys: `schema_version=1`, `enabled=True`, `since`, `until`, `total_queries`, `success_rate`, `skipped_lines`, `latency_ms`, `by_endpoint`, `by_collection`, `error_breakdown`).
- **Releasable**: `compute_stats` produces the full stats dict from an in-memory entry list.
- **Tests (TDD)** — `packages/archon-search/tests/telemetry/test_reader.py`:
  - Unit: `test_compute_stats_total_queries` — correct count.
  - Unit: `test_compute_stats_success_rate_zero_queries` — `success_rate is None`.
  - Unit: `test_compute_stats_success_rate_formula` — 3 ok / 4 total = 0.75.
  - Unit: `test_compute_stats_latency_nearest_rank` — known dataset, verified P50/P95 values.
  - Unit: `test_compute_stats_latency_null_when_no_entries` — 0 entries only → `{"p50": None, "p95": None}` (1 entry returns valid percentiles).
  - Unit: `test_compute_stats_by_endpoint_counts` — search/route entries counted correctly.
  - Unit: `test_compute_stats_by_collection_fan_out` — routing entry with `collections=["A","B"]` → both A and B incremented.
  - Unit: `test_compute_stats_by_collection_excludes_error_entries` — error entries (no `collection`/`collections`) not in `by_collection`.
  - Unit: `test_compute_stats_error_breakdown_all_keys_present` — all 6 keys present; zero-filled when no errors.
  - Unit: `test_compute_stats_invariant_error_breakdown_vs_by_endpoint` — `sum(error_breakdown.values()) == sum(ep["error"] for ep in by_endpoint.values())`.
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/telemetry/test_reader.py -k "compute_stats" -v`

#### Task 2.3 — `TelemetryReader.filter_entries` and `TelemetryReader.paginate`
- [x] **File**: `packages/archon-search/archon_search/telemetry/reader.py`
- **Depends on**: Task 2.1
- **Description**:
  - `filter_entries(self, entries: list[TelemetryEntry], *, collection: str | None = None, endpoint: EndpointKind | None = None, status: Status | None = None, error_kind: ErrorKind | None = None) -> list[TelemetryEntry]`:
    - All conditions are AND'd.
    - `endpoint` filter: `e.endpoint == endpoint`.
    - `status` filter: `e.status == status`.
    - `error_kind` filter: `e.error_kind == error_kind`.
    - `collection` filter: `e.collection == collection` OR (`e.collections is not None` AND `collection in e.collections`).
    - All `None` filters are no-ops. Return filtered list in original order.
  - `paginate(self, entries: list[TelemetryEntry], offset: int, limit: int) -> tuple[list[TelemetryEntry], int]`:
    - `total_in_window = len(entries)`.
    - Return `(entries[offset:offset + limit], total_in_window)`.
    - `offset` beyond end: returns `([], total_in_window)` — not an error.
- **Releasable**: filtering and pagination are available for the entries HTTP endpoint.
- **Tests (TDD)** — `packages/archon-search/tests/telemetry/test_reader.py`:
  - Unit: `test_filter_entries_by_endpoint` — correct entries returned.
  - Unit: `test_filter_entries_by_status` — correct entries returned.
  - Unit: `test_filter_entries_by_error_kind` — correct entries returned.
  - Unit: `test_filter_entries_by_collection_singular` — matches `e.collection == X`.
  - Unit: `test_filter_entries_by_collection_routing_plural` — matches `X in e.collections`.
  - Unit: `test_filter_entries_and_semantics` — `status=ok` + `error_kind=timeout` → empty list (contradictory filters).
  - Unit: `test_filter_entries_and_semantics_valid_intersection` — `endpoint=search` + `status=ok` on a mixed dataset → returns only entries matching both conditions (non-empty list).
  - Unit: `test_filter_entries_no_filter` — all `None` → full list unchanged.
  - Unit: `test_paginate_basic` — `offset=0, limit=2` → 2 entries, `total_in_window = len(all)`.
  - Unit: `test_paginate_last_page` — partial last page returned, `total_in_window` unchanged.
  - Unit: `test_paginate_offset_beyond_end` — `offset=999` → `[]`, correct `total_in_window`.
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/telemetry/test_reader.py -v`

---

### Phase 3 — HTTP Endpoints
> **Releasable**: after Task 3.4 — `GET /telemetry/stats` and `GET /telemetry/entries` are live in the running service.

#### Task 3.1 — Pydantic response models
- [x] **File**: `packages/archon-search/archon_search/server/schemas_telemetry.py` (new)
- **Depends on**: nothing
- **Description**:
  - Define the following Pydantic v2 `BaseModel` subclasses:
    - `LatencyPercentiles(BaseModel)`: `p50: float | None`, `p95: float | None`
    - `EndpointStats(BaseModel)`: `total: int`, `ok: int`, `error: int`
    - `CollectionStats(BaseModel)`: `total: int`, `ok: int`
    - `ErrorBreakdown(BaseModel)`: all 6 `ErrorKind` fields as `int = 0`
    - `StatsResponse(BaseModel)`: `schema_version: int = 1`, `enabled: bool`, `since: str | None = None`, `until: str | None = None`, `total_queries: int = 0`, `success_rate: float | None = None`, `skipped_lines: int = 0`, `latency_ms: LatencyPercentiles = LatencyPercentiles(p50=None, p95=None)`, `by_endpoint: dict[str, EndpointStats] = {}`, `by_collection: dict[str, CollectionStats] = {}`, `error_breakdown: ErrorBreakdown = ErrorBreakdown()`
    - `EntriesResponse(BaseModel)`: `schema_version: int = 1`, `enabled: bool`, `entries: list[dict[str, Any]]`, `next_offset: int`, `total_in_window: int`, `skipped_lines: int = 0`
    - `DisabledResponse(BaseModel)`: `enabled: bool = False`
  - No business logic — pure data models.
- **Releasable**: models importable by route handlers.
- **Tests (TDD)** — `packages/archon-search/tests/server/test_schemas_telemetry.py` (new):
  - Unit: `test_stats_response_defaults` — `StatsResponse(enabled=True)` has correct defaults: `latency_ms` is a `LatencyPercentiles(p50=None, p95=None)` instance (not `None`), `error_breakdown` is an `ErrorBreakdown()` instance (not `None`).
  - Unit: `test_error_breakdown_all_keys_default_zero` — all 6 fields are `0`.
  - Unit: `test_disabled_response_enabled_false` — `DisabledResponse().enabled is False`.
  - Unit: `test_entries_response_fields` — round-trip construction.
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/server/test_schemas_telemetry.py -v`

#### Task 3.2 — `GET /telemetry/stats` route handler
- [x] **File**: `packages/archon-search/archon_search/server/routes_telemetry.py` (new)
- **Depends on**: Task 2.2, Task 3.1
- **Description**:
  - `router = APIRouter()`
  - `@router.get("/telemetry/stats") async def get_telemetry_stats(request: Request, since: Annotated[date | None, Query()] = None, until: Annotated[date | None, Query()] = None) -> StatsResponse | DisabledResponse:`
    - `config: SearchConfig = request.app.state.config`
    - If `not config.telemetry.enabled`: return `DisabledResponse()`.
    - `log_dir = Path(config.telemetry.log_dir).expanduser()`
    - `reader = TelemetryReader(log_dir, config.telemetry.retention_days)`
    - Wrap `reader.resolve_dates(since, until)` in try/except: catch `ValueError` → raise `HTTPException(400, detail=str(e))`.
    - `entries, skipped = await asyncio.to_thread(reader.read_entries, since_d, until_d)`
    - Return `reader.compute_stats(entries, since_d, until_d, skipped)`.
  - FastAPI infers response model from return type annotation `StatsResponse | DisabledResponse`; the disabled path returns `DisabledResponse()`.
  - Import: `from archon_search.telemetry.reader import TelemetryReader` and standard library imports.
  - Logger: `logging.getLogger("archon.search")`.
- **Releasable**: stats endpoint callable via HTTP and `TestClient`.
- **Tests (TDD)** — `packages/archon-search/tests/server/test_routes_telemetry.py` (new):
  - Use `TestClient` with a minimal `create_app()` configured with a temp `log_dir`.
  - Integration: `test_stats_disabled_returns_enabled_false` — `config.telemetry.enabled=False` → `{"enabled": false}` HTTP 200.
  - Integration: `test_stats_no_files_returns_zeros` — enabled, empty log_dir → `total_queries=0`, HTTP 200.
  - Integration: `test_stats_returns_correct_values` — write 3 known JSONL lines (2 ok, 1 timeout error) spread across two files → assert `total_queries=3`, `success_rate≈0.667`, `latency_ms.p50` is the median value, `by_endpoint` has correct counts, `error_breakdown.timeout=1`.
  - Integration: `test_stats_since_after_until_returns_400` — `?since=2026-05-15&until=2026-05-14` → HTTP 400.
  - Integration: `test_stats_single_future_since_returns_400` — `?since=2099-01-01` (no until) → HTTP 400 (resolve_dates raises ValueError because since=2099 exceeds until=today directly — clamping does not apply since future date exceeds the clamp anchor).
  - Integration: `test_stats_date_range_selects_files` — two JSONL files in temp dir, only one in range.
  - Integration: `test_stats_skipped_lines_counted` — one bad JSON line → `skipped_lines=1` in response.
  - Integration: `test_stats_schema_version_is_1` — `schema_version == 1`.
  - Integration: `test_stats_uses_asyncio_to_thread` — patch `asyncio.to_thread` and assert it is called with `reader.read_entries` as the first argument when the stats endpoint is invoked.
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/server/test_routes_telemetry.py -k stats -v`

#### Task 3.3 — `GET /telemetry/entries` route handler
- [x] **File**: `packages/archon-search/archon_search/server/routes_telemetry.py`
- **Depends on**: Task 2.3, Task 3.1, Task 3.2
- **Description**:
  - Add to `routes_telemetry.py`:
  - `@router.get("/telemetry/entries") async def get_telemetry_entries(request, since, until, collection, endpoint: Annotated[EndpointKind | None, Query()] = None, status: Annotated[Status | None, Query()] = None, error_kind: Annotated[ErrorKind | None, Query()] = None, offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=200)] = 50) -> EntriesResponse | DisabledResponse:`
    - If `not config.telemetry.enabled`: return `DisabledResponse()`.
    - Wrap `reader.resolve_dates(since, until)` in try/except: catch `ValueError` → raise `HTTPException(400, detail=str(e))`.
    - `entries, skipped = await asyncio.to_thread(reader.read_entries, since_d, until_d)`
    - `filtered = reader.filter_entries(entries, collection=collection, endpoint=endpoint, status=status, error_kind=error_kind)`
    - `page, total = reader.paginate(filtered, offset, limit)`
    - Return `{"schema_version": 1, "enabled": True, "entries": [e.model_dump() for e in page], "next_offset": offset + len(page), "total_in_window": total, "skipped_lines": skipped}`.
  - FastAPI validates `endpoint`, `status`, `error_kind` via `Literal` type coercion — invalid string values yield HTTP 422 automatically.
  - FastAPI infers response model from return type annotation `EntriesResponse | DisabledResponse`; the disabled path returns `DisabledResponse()`.
- **Releasable**: entries endpoint callable via HTTP.
- **Tests (TDD)** — `packages/archon-search/tests/server/test_routes_telemetry.py`:
  - Integration: `test_entries_disabled_returns_enabled_false`.
  - Integration: `test_entries_pagination_offset_limit` — 5 entries in JSONL, `limit=2`, verify `next_offset=2`, `total_in_window=5`.
  - Integration: `test_entries_filter_by_status` — mix of ok/error, `?status=ok` returns only ok.
  - Integration: `test_entries_filter_by_endpoint`.
  - Integration: `test_entries_filter_by_error_kind`.
  - Integration: `test_entries_filter_by_collection`.
  - Integration: `test_entries_invalid_status_returns_422` — `?status=error` (not a valid literal) → HTTP 422.
  - Integration: `test_entries_invalid_endpoint_returns_422`.
  - Integration: `test_entries_invalid_error_kind_returns_422`.
  - Integration: `test_entries_since_after_until_returns_400`.
  - Integration: `test_entries_schema_version_is_1`.
  - Integration: `test_entries_only_documented_fields` — each returned entry (a) contains all required non-optional fields (`query_id`, `timestamp`, `endpoint`, `latency_ms`, `status`), and (b) has no keys outside `DOCUMENTED_SCHEMA_FIELDS`.
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/server/test_routes_telemetry.py -v`

#### Task 3.4 — Register telemetry router in `app.py`
- [x] **File**: `packages/archon-search/archon_search/server/app.py`
- **Depends on**: Task 3.3
- **Description**:
  - Add import: `from archon_search.server.routes_telemetry import router as telemetry_router`
  - Add `app.include_router(telemetry_router)` in `create_app()` alongside the existing router registrations.
  - No other changes to `app.py`.
- **Releasable**: endpoints live in the production FastAPI app.
- **Tests (TDD)** — `packages/archon-search/tests/server/test_app.py` (extend existing):
  - Integration: `test_telemetry_stats_route_registered` — `TestClient(create_app(...)).get("/telemetry/stats")` returns 200 (not 404).
  - Integration: `test_telemetry_entries_route_registered` — `TestClient(create_app(...)).get("/telemetry/entries")` returns 200 (not 404).
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/server/ -v`

---

### Phase 4 — SearchClient and MCP tool
> **Releasable**: after Task 4.3 — Claude sessions can call `telemetry_stats` MCP tool to get stats without leaving the session.

#### Task 4.1 — `SearchClient.telemetry_stats()`
- [x] **File**: `archon/ai/search_client.py`
- **Depends on**: Task 3.4
- **Description**:
  - Add method to `SearchClient`:
  ```python
  async def telemetry_stats(
      self,
      since: str | None = None,
      until: str | None = None,
  ) -> dict[str, Any] | None:
  ```
  - Build `params: dict[str, str] = {}`. Add `since` and `until` only if not `None`.
  - `await self._http.get("/telemetry/stats", params=params)` + `resp.raise_for_status()` + `return resp.json()`.
  - Catch `httpx.TimeoutException` → `logger.warning(...)`, return `None`.
  - Catch `httpx.ConnectError` → `logger.debug(...)`, return `None`.
  - Catch `httpx.HTTPStatusError` → `logger.warning(...)`, return `None`.
  - Catch bare `Exception` → `logger.warning(...)`, return `None`.
  - Note: `{"enabled": false}` responses are returned as-is (not mapped to `None`). Callers check the `enabled` key.
- **Releasable**: archon can query telemetry stats from archon-search over HTTP.
- **Tests (TDD)** — `tests/ai/test_search_client.py` (extend existing):
  - Unit: `test_telemetry_stats_success` — mock `httpx` GET `/telemetry/stats` → 200 + JSON → returns parsed dict.
  - Unit: `test_telemetry_stats_returns_none_on_timeout` — `httpx.TimeoutException` → `None`.
  - Unit: `test_telemetry_stats_returns_none_on_connect_error` — `httpx.ConnectError` → `None`.
  - Unit: `test_telemetry_stats_passes_since_param` — `since="2026-05-01"` present in GET params.
  - Unit: `test_telemetry_stats_omits_none_params` — `since=None` not in request query string.
  - Unit: `test_telemetry_stats_returns_none_on_http_error` — mock server returns HTTP 500 → `raise_for_status()` raises `HTTPStatusError` → method returns `None`.
  - Checkpoint: `uv run pytest --no-cov tests/ai/test_search_client.py -k telemetry_stats -v`

#### Task 4.2 — `SearchClient.telemetry_entries()` — DEFERRED to FEAT-039d
> **Out of scope for FEAT-039c.** `telemetry_entries()` has no caller in this feature (`telemetry_entries` MCP tool is explicitly out of scope due to MCP output size limits). Adding the method now violates YAGNI. Deferred to FEAT-039d when a concrete caller exists.

#### Task 4.3 — `telemetry_stats` MCP tool in `ArchonToolkit`
- [ ] **File**: `archon/ai/archon_toolkit_search.py`
- **Depends on**: Task 4.1
- **Description**:
  - Add `_TELEMETRY_STATS_SCHEMA: dict[str, Any]`:
    ```python
    {
        "name": "telemetry_stats",
        "description": "Retrieve aggregated search performance statistics from the local telemetry store. Returns total queries, success rate, P50/P95 latency, per-endpoint and per-collection breakdowns, and error breakdown by error kind. Requires [telemetry] enabled = true in archon-search.toml.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "since": {"type": "string", "description": "Start date YYYY-MM-DD (UTC). Defaults to today minus retention_days."},
                "until": {"type": "string", "description": "End date YYYY-MM-DD (UTC), inclusive. Defaults to today."},
            },
        },
    }
    ```
  - Add handler:
    ```python
    async def _handle_telemetry_stats(
        toolkit: "ArchonToolkit",
        arguments: dict[str, Any],
        *,
        user_id: int | None = None,
    ) -> str:
    ```
    - `client = get_search_client()`
    - `result = await client.telemetry_stats(since=arguments.get("since"), until=arguments.get("until"))`
    - If `result is None`: return `json.dumps({"error": "no data available", "hint": "telemetry may be disabled — set [telemetry] enabled = true in archon-search.toml; or the service may be unreachable or the request timed out"})`.
    - If `not result.get("enabled", True)`: return `json.dumps({"error": "telemetry is disabled", "hint": "set [telemetry] enabled = true in archon-search.toml"})`.
    - Otherwise: return `json.dumps(result)`.
  - Register in `_register_search_tools()`: `toolkit.register_tool("telemetry_stats", _TELEMETRY_STATS_SCHEMA, functools.partial(_handle_telemetry_stats, toolkit))`.
- **Releasable**: Claude sessions can call `telemetry_stats` MCP tool to retrieve performance stats.
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_search.py` (extend existing):
  - Unit: `test_telemetry_stats_tool_registered` — `"telemetry_stats"` present in `ArchonToolkit` tool registry after `_register_search_tools()`.
  - Unit: `test_telemetry_stats_tool_returns_stats_json` — mock `SearchClient.telemetry_stats` returns a stats dict → handler returns JSON string of that dict.
  - Unit: `test_telemetry_stats_tool_hints_when_none` — mock returns `None` → handler JSON contains `"hint"` key.
  - Unit: `test_telemetry_stats_tool_hints_when_disabled` — mock returns `{"enabled": false}` → handler JSON contains `"telemetry is disabled"`.
  - Unit: `test_telemetry_stats_tool_passes_since_until_args` — `arguments={"since": "2026-05-01"}` passed through to `client.telemetry_stats`.
  - Unit: `test_telemetry_stats_tool_no_args` — call handler with `arguments={}` (no `since`/`until`) → `SearchClient.telemetry_stats(since=None, until=None)` is called → returns normal stats JSON.
  - Checkpoint: `uv run pytest --no-cov tests/ai/test_archon_toolkit_search.py -k telemetry -v`

---

### Phase 5 — Documentation
> **Releasable**: after Task 5.2 — documentation matches the shipped implementation.

#### Task 5.1 — Update CLAUDE.md
- [ ] **File**: `CLAUDE.md`
- **Depends on**: Task 1.1
- **Description**:
  - In the `[telemetry]` config section description, change: "`export_enabled = true` raises `ConfigError` (reserved for FEAT-039c)" to "`export_enabled = true` logs a WARNING and is ignored (reserved for FEAT-039d)".
  - In the archon-search HTTP surface description (or config description), add the two new endpoints: `GET /telemetry/stats` and `GET /telemetry/entries` with a one-line description each.
  - No other changes.
- **Releasable**: CLAUDE.md reflects the new behavior.
- **Tests (TDD)**: N/A — documentation task.
  - Checkpoint: `grep -n "export_enabled" CLAUDE.md` — confirm no mention of "FEAT-039c" in that context.

#### Task 5.2 — archon-search README telemetry read-back section
- [ ] **File**: `packages/archon-search/README.md`
- **Depends on**: Task 3.4
- **Description**:
  - Add a `## Telemetry Read-Back API` section (after the existing `## Privacy & Telemetry` section).
  - Document `GET /telemetry/stats`: parameters (`since`, `until`), response shape summary, note that `success_rate` is `null` when no queries.
  - Document `GET /telemetry/entries`: parameters (`since`, `until`, `collection`, `endpoint`, `status`, `error_kind`, `offset`, `limit`), pagination via `next_offset`/`total_in_window`. Note pagination stop condition: clients should continue calling with the returned `next_offset` until `entries` is empty (equivalently, until `next_offset >= total_in_window`).
  - Note: both endpoints return `{"enabled": false}` when telemetry is disabled.
  - Keep under 40 lines total.
- **Releasable**: README documents the new API surface.
- **Tests (TDD)**: N/A — documentation task.
  - Checkpoint: `grep -n "telemetry/stats" packages/archon-search/README.md` — confirms section exists.
