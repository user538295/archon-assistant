# Feature Brief: FEAT-039b — Search query telemetry and privacy policy

## Problem
The Search server has no record of what real users actually query, so we cannot diagnose retrieval regressions in production, calibrate the offline eval baseline against real usage, or build the relevance-feedback loop that FEAT-037 calls for. This blocks every data-driven improvement after FEAT-039.

## Goal
Ship an opt-in, local-only, structured query log with a published privacy contract — so that operators can inspect their own search behavior, and FEAT-039c has clean input data to build on. Success is observable: when `[telemetry].enabled = true`, every search and route request produces a JSONL line at `~/.archon/search-logs/YYYY-MM-DD.jsonl`, and the documented privacy boundary makes it auditable.

## Users & Context
**Primary**: the operator running their own Archon instance, who wants to understand what they're searching for over time and feed real queries into eval calibration.
**Secondary**: future contributors building FEAT-039c (feedback capture, online loop) who need a documented, stable log schema to consume.
At the time of use, the operator has already installed and is actively searching via Archon. Telemetry collection is invisible to the chat-side UX — it's a quiet background record.

## Core Flow
1. Operator enables telemetry by setting `[telemetry].enabled = true` in `~/.archon/archon-search.toml` and restarts the Search server.
2. Each `POST /search` and `POST /route` request runs normally; after the response is sent, the handler dispatches a fire-and-forget async write to today's JSONL file.
3. Every entry contains: `query_id` (UUID), `timestamp` (ISO 8601 UTC, server-side), `endpoint` (`search` | `route`), `collection`, `result_count`, `result_doc_ids` (ordered), `latency_ms`.
4. A daily cleanup job (sharing the existing attachment-TTL scheduler) deletes files older than `[telemetry].retention_days` (default 30).
5. Operator inspects logs directly with `cat`, `jq`, or other plain-text tools. No HTTP read-back endpoint.

## In Scope
- New `[telemetry]` section in `~/.archon/archon-search.toml` with `enabled` (default `false`), `retention_days` (default `30`), `export_enabled` (reserved; rejected with a clear error in v1).
- JSONL log writer in `archon_search/telemetry/` (new module) with one public function: `log_query_async(entry: TelemetryEntry) -> None`.
- Log-write hooks at the end of `routes_search.py` and `routes_route.py` handlers, guarded by `if config.telemetry.enabled`.
- Daily retention pruner registered in the existing scheduler.
- Privacy policy section in `packages/archon-search/README.md` and a new ADR (or amendment to ADR 09) documenting: what is logged, what is not, opt-in default, retention, and the explicit prohibition on external transmission in v1.
- Updates to FEAT-037 roadmap item 4 and FEAT-039 follow-up notes to mark 039b/039c boundaries.

## Out of Scope
- **Relevance feedback capture (thumbs up/down, judgment labeling)** — deferred to FEAT-039c; needs UX design and Telegram-side flow work.
- **Online data-collection loop / external transmission** — deferred to FEAT-039c; requires the consent + transport model 039b's policy document only sets the stage for.
- **Promotion of logged queries into FEAT-039 eval fixtures** — deferred to FEAT-039c; without feedback labels the queries cannot enter the deterministic baseline.
- **Indexed analytics (SQLite/DuckDB)** — deferred to FEAT-039c; JSONL is sufficient until real analysis demand exists.
- **Raw query text / query-hash logging** — deferred to FEAT-039c; v1 logs no query content to minimize the v1 privacy surface.
- **Read-back HTTP endpoint** (`GET /telemetry/queries`) — deferred to FEAT-039c.
- **User-identity correlation** — never; even when 039c adds raw query text, telemetry remains user-agnostic at the Search layer.

## Key Decisions
- **JSONL on disk, not SQLite / LanceDB**: append-only, crash-safe, single-writer-friendly, zero new dependencies. SQLite is the obvious upgrade path when 039c needs indexed reads.
- **Opt-in default (`enabled = false`)**: conservative privacy posture; no data collected without an explicit configuration change.
- **Two separate flags (`enabled` for local logs, `export_enabled` reserved + rejected in v1)**: forces 039c to revisit the export decision deliberately; prevents "enabled" from quietly meaning "transmitted" in some future config drift.
- **Log fields = A + D from refinement (query_id, timestamp, collection, result_count, doc_ids, latency)**: doc_ids are not sensitive (corpus is local), enable offline relevance recomputation, and give 039c real data to label without recording raw query text yet.
- **Write hook at HTTP handler, not in `Pipeline.search()`**: keeps telemetry out of business logic; one obvious place to disable; matches FastAPI conventions.
- **Fire-and-forget async writes, errors swallowed**: telemetry MUST NOT fail or slow a search request. Logger errors go to stderr only.
- **Retention pruner reuses existing scheduler**: no new infra; same code path as attachment TTL cleanup.

## Edge Cases & Constraints
- **Disk full / log write fails**: caught and logged to stderr; the search response is unaffected. Telemetry is best-effort.
- **Concurrent writers (multiple Search processes)**: JSONL appends under POSIX `O_APPEND` are line-atomic for writes under PIPE_BUF (~4 KB). Each entry must stay under that bound — enforce a serializer length check before write.
- **Server restart mid-day**: today's `.jsonl` file is reopened in append mode; no data loss.
- **Clock skew**: timestamps are always server-side UTC; client clocks are never trusted.
- **Retention worker missed run (server down for days)**: next startup runs cleanup before accepting requests; multi-day backlog is acceptable.
- **`export_enabled = true` in v1 config**: server fails startup with an explicit error message pointing at FEAT-039c — defense against config drift.
- **Sensitive collection names**: collections are opt-in to be searched at all; logging the collection name carries the same privacy weight as searching it. No additional redaction in v1.
- **MCP / Archon client never sees telemetry**: log files are local to the Search server's `~/.archon/search-logs/` and never traverse the HTTP boundary.

## Open Questions
- Should `query_id` be deterministic from the request (e.g., hash of inputs + timestamp bucket) or a random UUID? Random UUID is the safer default; planning can revisit if deduplication ever matters.
- Does the existing scheduler have a stable hook for "daily at midnight UTC", or does FEAT-039b need to add one? Planning should confirm and either reuse or extend.
- Should logs be written via a queue (`asyncio.Queue`) and a single background writer task, or via per-request fire-and-forget tasks? Queue is cleaner but adds a lifecycle dependency. Planning to decide.

## Future Iterations (handed to FEAT-039c)
- Relevance feedback API (`POST /feedback`) and Telegram-side capture UX (thumbs / inline keyboard).
- Promotion tool: `archon-search telemetry export-fixture --since YYYY-MM-DD` producing a candidate eval fixture for human review.
- Optional raw-query-text logging behind a second-level opt-in flag (`[telemetry].log_query_text`), with hashing as the safer middle ground.
- Indexed analytics layer (SQLite or DuckDB), populated from JSONL on demand.
- Read-back HTTP endpoint with auth, scoped to local UNIX socket if exposed at all.
- External transmission model (`export_enabled = true`) with explicit destination, transport security, and consent UI.

## Recommendation
Build this now, exactly at the scope above. The hardest part is **not** the JSONL writer — it's the privacy contract: getting the policy doc precise enough that FEAT-039c inherits a clear "what changes, and why" envelope. Do not compromise on the opt-in default, the `export_enabled` reservation, or the no-query-text rule — every shortcut here becomes a privacy liability later. The implementation itself should be one small module, two handler edits, and one scheduler entry. If the work expands beyond that, scope has drifted into 039c.
