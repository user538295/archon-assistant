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
4. Optionally, operator calls `GET /telemetry/entries?since=YYYY-MM-DD&collection=X&status=error` to browse raw entries with pagination.
5. MCP tool `telemetry_stats` wraps step 2–3 so Claude sessions can query performance without leaving the session.

## In Scope

- **`export_enabled` fix**: change `ConfigError` to a logged WARNING (no-op) — operators who had `export_enabled = true` in config no longer crash; the flag remains inert until FEAT-039d
- **`GET /telemetry/stats`**: aggregated metrics over a `since` / `until` date range — total queries, success rate, P50/P95 latency, error breakdown by `error_kind`, per-endpoint counts, per-collection counts
- **`GET /telemetry/entries`**: paginated raw entries with filters (`since`, `until`, `collection`, `endpoint`, `status`); page size capped at 200; cursor-based pagination via `after_query_id`
- **MCP tool `telemetry_stats`**: thin wrapper over the stats endpoint, usable from Claude sessions; returns the same payload as the HTTP endpoint
- **Read JSONL directly**: no indexing layer — parse files on request (adequate for 30-day retention at local traffic volumes)
- **Telemetry disabled guard**: both endpoints return `{"enabled": false}` with HTTP 200 when `telemetry.enabled = false`, rather than 404 or 503

## Out of Scope

- External transmission (`export_enabled = true` implementation) — privacy and transport complexity; deferred to FEAT-039d
- Relevance feedback capture (`POST /feedback`) — separate data model and collection concern; FEAT-039d or later
- Fixture promotion CLI — depends on feedback data that doesn't exist yet
- Indexed analytics (SQLite/DuckDB) — premature; JSONL reads are sufficient at local scale
- Telegram-side UX — not archon-search's concern; any Telegram integration calls these endpoints from `archon/chat/`
- Authentication — service is localhost-only; same policy as all existing endpoints
- Optional query-text/hash logging — separate privacy decision; FEAT-039d

## Key Decisions

- **Read JSONL directly, no index**: adding a database for a local tool with 30-day retention is over-engineering. A request that aggregates 30 files is fast enough. Re-evaluate if retention grows beyond 90 days.
- **`export_enabled` becomes a warning, not an error**: the ConfigError was a placeholder guard. Making it a no-op warning is the right long-term behavior — the absence of export code is the real safety boundary (per ADR 10).
- **MCP tool over new MCP resource**: a tool call fits the existing `ArchonToolkit` pattern and is callable from active Claude sessions without schema changes elsewhere.
- **Cursor-based pagination over offset**: `after_query_id` is stable across writes; offset pagination can skip or duplicate entries if new data arrives mid-session.
- **HTTP 200 with `{"enabled": false}` body when disabled**: 404/503 would cause callers to treat a valid config choice as an error. The service is running; telemetry is just off.

## Edge Cases & Constraints

- **No JSONL files yet** (telemetry just enabled): stats returns zeros, entries returns empty list — not an error.
- **Corrupted JSONL line**: skip and log WARNING at `archon.search` logger; do not abort the request. Count skipped lines in the response metadata.
- **`since` > `until`**: return HTTP 400 with a clear message.
- **`since` beyond retention window**: return data for whatever files exist; do not synthesize missing data.
- **Large date ranges**: if parsing >90 days of files is slow, add a `max_days` cap (default 30) and return HTTP 400 with guidance if exceeded. Document the limit.
- **Concurrent write during read**: JSONL append is atomic at the line level on POSIX; reading while writing may miss the in-flight line. Acceptable — stats are near-real-time, not exact.
- **`truncated: true` entries in latency percentiles**: include them — latency is still valid even if `result_doc_ids` was trimmed.

## Open Questions

- Should `telemetry_stats` MCP tool be added to `ArchonToolkit` (archon-side, calls search over HTTP) or to the archon-search MCP server? Both are valid; archon-side is consistent with existing search tool registration pattern.
- Should the stats response include a `schema_version` field now, to make future migrations easier? Low cost to add; worth deciding at planning time.

## Future Iterations

- Indexed analytics (SQLite/DuckDB) if JSONL read performance becomes a bottleneck at scale
- `POST /feedback` relevance signal capture, feeding into eval fixture promotion
- `archon-search telemetry promote` CLI for exporting entries to YAML eval fixtures
- External transmission (`export_enabled = true`) with destination config, transport security, and consent — FEAT-039d
- Optional hashed query-text logging behind `[telemetry].log_query_text = true`

## Recommendation

This is the right feature to build now. FEAT-039b created a data pipeline with no consumer — 039c closes that gap with minimal new complexity by reading what's already there. The hardest part is not the implementation (JSONL parsing + a few aggregations is straightforward) but getting the API shape right so future analytics layers can build on it without breaking changes. Schema versioning in the response and a clean cursor pagination model are the two things not to cut.
