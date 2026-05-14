# Feature Brief: FEAT-039c — Search Telemetry Observability

## Problem
Telemetry data written by FEAT-039b accumulates locally as JSONL files but is completely opaque: no one can see success rates, latency trends, or error patterns without manually grepping raw files. The system produces data but offers no insight from it.

## Goal
Any consumer (MCP tool, HTTP client, CLI) can retrieve aggregated search performance stats and paginated raw telemetry entries from the running archon-search service, using data already on disk — no new collection, no external transmission.

## Users & Context
Operators and developers running archon-search locally, wanting to understand whether search is working well and which collections or endpoints are struggling. Accessed ad-hoc during tuning or after incidents. Also accessible to Claude sessions via MCP, enabling self-diagnostics.

## Core Flow

1. Operator enables telemetry in `archon-search.toml` (`[telemetry] enabled = true`). FEAT-039b already handles writing.
2. After some usage has accumulated, operator calls `GET /telemetry/stats?since=YYYY-MM-DD` (or via MCP tool `telemetry_stats`).
3. Service reads the relevant JSONL files from `log_dir`, parses entries in-memory, and returns aggregated metrics: total queries, success rate, error breakdown, latency percentiles (P50/P95), and per-collection and per-endpoint breakdowns.
4. Optionally, operator calls `GET /telemetry/entries?since=YYYY-MM-DD&collection=X&status=ok` to browse raw entries with pagination.
5. MCP tool `telemetry_stats` wraps step 2–3 so Claude sessions can query performance without leaving the session.

## In Scope

- **`export_enabled` fix**: change `ConfigError` to a logged WARNING (no-op) and force `export_enabled` to `False` in memory — operators who had `export_enabled = true` in config no longer crash; the flag is inert and cannot accidentally activate export logic until FEAT-039d
- **`GET /telemetry/stats`**: aggregated metrics over a `since` / `until` date range — total queries, success rate, P50/P95 latency, error breakdown by `error_kind`, per-endpoint counts, per-collection counts
- **`GET /telemetry/entries`**: paginated raw entries with filters (`since`, `until`, `collection`, `endpoint`, `status`); `offset`/`limit` pagination (limit capped at 200)
- **MCP tool `telemetry_stats`**: thin wrapper over the stats endpoint, registered in `ArchonToolkit` (archon-side, calls archon-search via `SearchClient.telemetry_stats()` over HTTP); returns the same payload as the HTTP endpoint
- **`SearchClient` new methods**: `telemetry_stats(since, until)` and `telemetry_entries(since, until, collection, endpoint, status, offset, limit)`; both return `None` on network failure AND when telemetry is disabled (consistent with existing `SearchClient` convention — callers treat both as "no data available"). The MCP tool `telemetry_stats` returns a human-readable "telemetry is disabled — enable it in archon-search.toml" message when it receives None, using context from the error.
- **Read JSONL directly**: no indexing layer — parse files on request (adequate for 30-day retention at local traffic volumes)
- **Telemetry disabled guard**: both endpoints return `{"enabled": false}` with HTTP 200 when `telemetry.enabled = false`, rather than 404 or 503
- **`schema_version: 1`** (integer) included in all stats and entries responses — future-proofing the API contract
- **Update ADR 10** to reference FEAT-039d (not FEAT-039c) for remote export
- **Update ADR 10's `export_enabled` behavior description** to reflect the ConfigError removal (now a WARNING + forced to `False` in memory, service starts normally)

## Out of Scope

- External transmission (`export_enabled = true` implementation) — privacy and transport complexity; deferred to FEAT-039d
- Relevance feedback capture (`POST /feedback`) — separate data model and collection concern; FEAT-039d or later
- Fixture promotion CLI — depends on feedback data that doesn't exist yet
- Indexed analytics (SQLite/DuckDB) — premature; JSONL reads are sufficient at local scale
- Telegram-side UX — not archon-search's concern; any Telegram integration calls these endpoints from `archon/chat/`
- Authentication — service is localhost-only; same policy as all existing endpoints
- Optional query-text/hash logging — separate privacy decision; FEAT-039d
- **`telemetry_entries` MCP tool** — entries responses can be large (up to 200 items per page); the MCP tool output size limit makes this impractical. Operators needing to browse entries should use the HTTP endpoint directly. A future iteration may add a summary or filtered view.

## Response Schemas

When telemetry is disabled, both endpoints return:
```json
{"enabled": false}
```

`GET /telemetry/stats` success response:
```json
{
  "schema_version": 1,
  "enabled": true,
  "since": "YYYY-MM-DD",
  "until": "YYYY-MM-DD",
  "total_queries": 1234,
  "success_rate": 0.97,  /* null when total_queries == 0 */
  "skipped_lines": 0,
  "latency_ms": {
    "p50": 45.2,
    "p95": 210.8
  },
  "by_endpoint": {
    "search": {"total": 900, "ok": 880, "error": 20},
    "search_with_context": {"total": 300, "ok": 290, "error": 10},
    "route": {"total": 34, "ok": 34, "error": 0}
  },
  "by_collection": {
    "my-docs": {"total": 600, "ok": 590}
  },
  "error_breakdown": {
    "empty_query": 5,
    "timeout": 12,
    "internal_error": 8,
    "validation_error": 5,
    "slot_out_of_range": 0,
    "other": 0
  }
}
```

`GET /telemetry/entries` success response:
```json
{
  "schema_version": 1,
  "enabled": true,
  "entries": [/* verbatim TelemetryEntry objects as dicts */],
  "next_offset": 50,
  "total_in_window": 1234,
  "skipped_lines": 0
}
```

`skipped_lines` is always present, defaulting to 0 when all lines parse successfully. For the stats endpoint, `total_queries` counts only successfully parsed entries. `total_in_window` is the total number of entries matching the applied filters across all files in the date window, before `offset`/`limit` are applied — callers know they have reached the end when `next_offset >= total_in_window`. If cursor pagination is added later, `schema_version` bumps.

## API Parameters

`GET /telemetry/stats`:
- `since`: optional, default = today UTC − `retention_days`
- `until`: optional, default = today UTC (inclusive)

`GET /telemetry/entries`:
- `since`, `until`: same defaults as stats
- `collection`: optional, default = no filter
- `endpoint`: optional, must be one of `search | search_with_context | route`; default = no filter
- `status`: optional, must be one of `ok | validation_error | timeout | internal_error`; default = no filter
- `error_kind`: optional, must be one of `empty_query | slot_out_of_range | timeout | internal_error | validation_error | other`; default = no filter
- `offset`: integer ≥ 0, default = 0
- `limit`: integer 1–200, default = 50

Entries are returned in timestamp-ascending order (matching JSONL file order). To fetch the next page, pass `offset + limit`.

All filters are AND'd. Contradictory combinations (e.g., `status=ok` with any `error_kind`) return empty results — `ok`-status entries never have `error_kind` set.

## Key Decisions

- **Read JSONL directly, no index**: adding a database for a local tool with 30-day retention is over-engineering. A request that aggregates 30 files is fast enough. Re-evaluate if retention grows beyond 90 days.
- **`export_enabled` becomes a warning, not an error**: the ConfigError was a placeholder guard. Making it a no-op warning is the right long-term behavior — the absence of export code is the real safety boundary (per ADR 10). Forcing `export_enabled` to `False` in memory after the warning ensures FEAT-039d cannot be accidentally triggered by stale config, even if a future code path checks the flag.
- **MCP tool in `ArchonToolkit`, archon-side**: `telemetry_stats` is registered in `ArchonToolkit`, calling archon-search via `SearchClient.telemetry_stats()` over HTTP — consistent with all existing search tools (`search_start`, `search_status`, etc.). No changes to the archon-search MCP server.
- **Offset pagination over cursor-based**: for a local read-only file system with a single writer, "new entries arriving mid-session" is negligible for analytics use cases. Both offset and cursor pagination require reading from the start of the date-range files; offset is simpler and correct for oldest-first ordering. Entries are returned in **oldest-first order** (ascending by file date, then by line order within the file) — the natural JSONL write order. With append-only files and oldest-first ordering, offset-based pagination is stable: new entries appended after a page-1 request do not shift earlier offsets.
- **`SearchClient` returns `None` for both network failure and disabled state**: consistent with existing `SearchClient` convention — callers treat both as "no data available". The archon-search HTTP endpoint returns `{"enabled": false}` for its direct callers. The MCP tool provides a helpful message when it receives `None`.
- **`schema_version` in HTTP response only**: JSONL entries are treated as v1 implicitly. A future migration can detect schema version from the response rather than stored data, avoiding the need to rewrite JSONL files.
- **`SearchClient` timeout applies to telemetry endpoints**: the `SearchClient` default HTTP timeout applies. Operators querying large date ranges should be aware that stats aggregation can take several seconds. If `SearchClient` times out, it returns `None` — callers see this as "unreachable" rather than "slow". This is acceptable for v1; a per-method timeout override is a future-iteration concern.
- **Date window is silently clamped to `retention_days`**: there can never be data beyond the retention window anyway, so a 400 error for out-of-range dates would be misleading. Silently clamping is correct and simpler.
- **`success_rate` formula**: `count(status == 'ok') / total_queries` across all endpoints and entry types. This is a global aggregate — an "ok" routing response and an "ok" search response both contribute equally. When `total_queries == 0`, `success_rate` is `null` (not 0.0).
- **P50/P95 via nearest-rank method**: sort entries by `latency_ms`, take index `math.ceil(p/100 * n) - 1`. When fewer than 2 entries exist in the window, `latency_ms` is present but its values are `null`: `{"p50": null, "p95": null}`. No external statistics library required.
- **All JSONL file I/O uses `asyncio.to_thread()`**: this is required for FastAPI async handlers — synchronous file I/O blocks the event loop. The read path must use `asyncio.to_thread(reader_fn)` where `reader_fn` does all file opens, iterations, and JSON parsing synchronously inside the thread.

## Edge Cases & Constraints

- **No JSONL files yet** (telemetry just enabled): stats returns zeros, entries returns empty list — not an error.
- **Corrupted JSONL line**: skip and log WARNING at `archon.search` logger; do not abort the request. `skipped_lines` in the response counts skipped lines.
- **`since` > `until`**: return HTTP 400 `{"detail": "since must be before until"}`.
- **`since` beyond retention window**: the date window is silently clamped to `retention_days`. Whatever files exist are read; missing data is not synthesized.
- **`status` filter values**: `status` must be one of the `Status` literals: `ok`, `validation_error`, `timeout`, `internal_error`. Invalid values return HTTP 400.
- **`error_kind` filter values**: `error_kind` must be one of the closed `ErrorKind` literals: `empty_query`, `slot_out_of_range`, `timeout`, `internal_error`, `validation_error`, `other`. Invalid `error_kind` filter values return HTTP 400.
- **`collection` filter for routing entries**: matches entries where either `collection == X` (retrieval entries) or `X in collections` (routing entries). This ensures routing entries appear in collection-scoped views.
- **`query_id` field**: an existing field on every `TelemetryEntry` (32 hex chars, generated per factory call, unique per entry, already written to JSONL by FEAT-039b). No schema changes to FEAT-039b's entry format are required.
- **JSONL file naming**: files are named `YYYY-MM-DD.jsonl` (UTC date, zero-padded). The stats/entries reader selects files by parsing filenames in the date range [since, until] inclusive. Files that do not match the `YYYY-MM-DD.jsonl` pattern are silently skipped. This is the existing FEAT-039b convention.
- **Date parameter timezone semantics**: `since` and `until` are UTC calendar dates in `YYYY-MM-DD` format. Both are inclusive. When `until` is omitted, it defaults to today's UTC date. When `since` is omitted, it defaults to `today − retention_days`. Entries are matched by the filename date of the JSONL file they appear in, not by their `timestamp` field — this makes date filtering O(file-count) rather than O(entry-count).
- **FileNotFoundError during concurrent prune**: if a JSONL file is deleted by the Pruner while the handler is iterating the directory, the `FileNotFoundError` is caught per-file, logged at DEBUG level, and the file is silently skipped. The pruner is doing its job.
- **Concurrent write during read**: JSONL append is atomic at the line level on POSIX; reading while writing may miss the in-flight line. Acceptable — stats are near-real-time, not exact.
- **`truncated: true` entries in latency percentiles**: include them — latency is still valid even if `result_doc_ids` was trimmed.
- **HTTP 400 error format**: uses FastAPI's default `{"detail": "..."}` format.
- **`by_collection` does not include error counts**: error entries (produced via `TelemetryEntry.from_error()`) have no `collection` or `collections` field — they are endpoint-level, not collection-level. Therefore `by_collection` cannot include error counts and shows only `{total, ok}`. The full error breakdown is in `error_breakdown` keyed by `error_kind`.
- **`by_collection` counting for routing entries**: routing entries (endpoint=`route`) have `collections: list[str]`, not a single `collection`. In `by_collection`, routing entries fan out — each collection in the list gets +1 to its totals. A routing entry with `collections: ['A', 'B']` counts once in each of A and B. This means `sum(by_collection[*].total)` can exceed `total_queries` (it counts collection-touches, not unique queries).
- **Invariant: `error_breakdown` vs `by_endpoint`**: `sum(error_breakdown.values()) == sum(by_endpoint[e]['error'] for e in by_endpoint)`. Both counts are derived from the same set of error-status entries. Implementations must ensure they use the same counting logic for both to avoid inconsistency.

## Future Iterations

- Indexed analytics (SQLite/DuckDB) if JSONL read performance becomes a bottleneck at scale
- `POST /feedback` relevance signal capture, feeding into eval fixture promotion
- `archon-search telemetry promote` CLI for exporting entries to YAML eval fixtures
- External transmission (`export_enabled = true`) with destination config, transport security, and consent — FEAT-039d
- Optional hashed query-text logging behind `[telemetry].log_query_text = true`

## Recommendation

This is the right feature to build now. FEAT-039b created a data pipeline with no consumer — 039c closes that gap with minimal new complexity by reading what's already there. The hardest part is not the implementation (JSONL parsing + a few aggregations is straightforward) but getting the API shape right so future analytics layers can build on it without breaking changes. Schema versioning in the response and clear, simple pagination are the two things not to cut.
