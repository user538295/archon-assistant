# Feature Brief: FEAT-039b — Search query telemetry and privacy policy

## Problem
The Search server has no record of what real users actually query, so we cannot diagnose retrieval regressions in production, calibrate the offline eval baseline against real usage, or build the relevance-feedback loop that FEAT-037 calls for. This blocks every data-driven improvement after FEAT-039.

## Goal
Ship an opt-in, local-only, structured query log with a published privacy contract — so that operators can inspect their own search behavior, and FEAT-039c has clean input data to build on. Success is observable: when `[telemetry].enabled = true`, every `search` and `search_with_context` MCP tool invocation, and every `POST /route` REST call, appends one JSONL line to today's `~/.archon/search-logs/YYYY-MM-DD.jsonl`. The documented privacy boundary is auditable, and a CI test asserts the log entry schema is exhaustive (no accidental query-text leakage).

## Users & Context
**Primary**: the operator running their own Archon instance, who wants to understand what they're searching for over time and feed real queries into eval calibration.
**Secondary**: future contributors building FEAT-039c (feedback capture, online loop) who need a documented, stable log schema to consume.
At the time of use, the operator has already installed and is actively searching via Archon. Telemetry collection is invisible to the chat-side UX — it's a quiet background record.

## Architecture context (established by investigation)

Two findings shape the hook design and must be reflected in any plan:

- **There is no `routes_search.py` REST endpoint.** Search is exposed as FastMCP tools in `packages/archon-search/archon_search/server/mcp.py` (`search`, `search_with_context`). The hook target for retrieval telemetry is the MCP tool wrapper, not a FastAPI route. Routing telemetry hooks into `routes_route.py` (the only REST entry point that receives a user query).
- **There is no scheduler inside `archon-search`.** Archon's `JobScheduler` lives on the other side of the HTTP boundary. The retention pruner must be a new in-process periodic task managed by the FastAPI `lifespan` (registered into `app.state._background_tasks` so it is cancelled cleanly on shutdown).

## Core Flow
1. Operator enables telemetry by setting `[telemetry].enabled = true` in `~/.archon/archon-search.toml` and restarts the Search server.
2. Each MCP `search` / `search_with_context` tool call and each `POST /route` REST call runs normally. After the response is computed (success **or** validation/timeout error), the handler enqueues a `TelemetryEntry` onto an `asyncio.Queue` and returns — the request never waits on disk I/O.
3. A single background writer task drains the queue, appends one JSON line per entry to today's `~/.archon/search-logs/YYYY-MM-DD.jsonl`, and rotates to the next day at server-side UTC midnight.
4. A daily retention pruner (also a `lifespan`-managed task) deletes files whose filename date is older than `[telemetry].retention_days` (default 30).
5. Operator inspects logs directly with `cat`, `jq`, or other plain-text tools. No HTTP read-back endpoint.

## Log entry schema

Two entry variants, both serialized as one JSON object per line. The `TelemetryEntry` Pydantic model uses `model_config = ConfigDict(extra="forbid")` so that any future field added must be deliberate — this is the structural privacy guarantee. Construction goes through explicit factory methods (`from_search_tool_result(...)`, `from_route_response(...)`) that accept ONLY the safe fields; the factories never receive the raw request body, so query text cannot reach the entry by accident.

**Common fields (all entries)**:
- `query_id: str` — random UUIDv4
- `timestamp: str` — server-side UTC, ISO 8601 with `Z` suffix
- `endpoint: Literal["search", "search_with_context", "route"]`
- `latency_ms: float`
- `status: Literal["ok", "validation_error", "timeout", "internal_error"]`

**Retrieval entries** (`endpoint = "search" | "search_with_context"`, `status = "ok"`):
- `collection: str` — the exact collection name searched (never derived from labels)
- `result_count: int` — number of results returned
- `result_doc_ids: list[str]` — ordered, post-rerank doc_ids (see Privacy section for the path-derived doc_id discussion)

**Routing entries** (`endpoint = "route"`, `status = "ok"`):
- `collections: list[str]` — `pinned_names + routable_names` from the route response (singular `collection` does not apply; routing emits a shortlist)
- `decomposer_invoked: bool`

**Error entries** (`status != "ok"`):
- Only the common fields plus a redacted `error_kind: str` (e.g., `"empty_query"`, `"slot_out_of_range"`, `"timeout"`, `"internal_error"`). Never the exception message — exception text can echo user input.

## In Scope
- New `[telemetry]` section in `~/.archon/archon-search.toml`: `enabled: bool = false`, `retention_days: int = 30`, `export_enabled: bool = false` (reserved; see export rejection below).
- New module `packages/archon-search/archon_search/telemetry/` containing:
  - `entry.py` — Pydantic `TelemetryEntry` model with `extra="forbid"`, factories `from_search_tool_result()` and `from_route_response()` and `from_error()`.
  - `writer.py` — `TelemetryWriter` class managing the `asyncio.Queue`, the background drain task, the today-file handle, and UTC rotation.
  - `pruner.py` — periodic task that deletes `YYYY-MM-DD.jsonl` files where the filename date is older than `retention_days` (file age is determined from the filename, never from `mtime` / `ctime`; the pruner accepts an injectable `now: date | None = None` for deterministic tests).
- Hook points:
  - In `server/mcp.py`: wrap the existing `search` and `search_with_context` tool bodies so the telemetry enqueue runs in a `try/finally` after the pipeline call. The wrapping must construct the entry via the factory — `body.query` (raw query text) must never be passed to the factory.
  - In `routes_route.py`: same pattern at the end of the handler.
  - Both hook sites are guarded by `if writer is not None` (writer is `None` when telemetry is disabled, so the hot path is one attribute load).
- Lifespan registration in `server/app.py`: when `config.telemetry.enabled`, construct the `TelemetryWriter` and `Pruner`, start them as tracked tasks in `app.state._background_tasks`, and drain the queue (with a bounded shutdown timeout, default 2s) before lifespan exit.
- `export_enabled = true` rejection: implemented as a Pydantic validator on the `[telemetry]` config section. When `export_enabled=True`, validation raises `ConfigError("[telemetry].export_enabled is reserved for FEAT-039c and must be false in v1")`. The error surfaces at config-load time (before `create_app()`), and on startup the server also emits an `archon.search` logger warning `"telemetry: export attempt rejected"` so the attempt is observable in logs.
- Privacy policy section in `packages/archon-search/README.md` and a new ADR (`Documentation/ADRs/10_search_query_telemetry.md`) documenting: what is logged, what is not, opt-in default, retention, path-derived doc_id risk, the explicit prohibition on external transmission in v1, and the honest statement that the v1 defense against external transmission is the **absence of export code**, not the config flag.
- Roadmap and FEAT-039 plan updates (already landed in commit `abaf6d6`).

## Out of Scope
- **Relevance feedback capture (thumbs up/down, judgment labeling)** — deferred to FEAT-039c; needs UX design and Telegram-side flow work.
- **Online data-collection loop / external transmission** — deferred to FEAT-039c; requires the consent + transport model 039b's policy document only sets the stage for.
- **Promotion of logged queries into FEAT-039 eval fixtures** — deferred to FEAT-039c; without feedback labels the queries cannot enter the deterministic baseline.
- **Indexed analytics (SQLite/DuckDB)** — deferred to FEAT-039c; JSONL is sufficient until real analysis demand exists.
- **Raw query text / query-hash logging** — deferred to FEAT-039c; v1 logs no query content to minimize the v1 privacy surface.
- **Read-back HTTP endpoint** (`GET /telemetry/queries`) — deferred to FEAT-039c.
- **User-identity correlation** — never; even when 039c adds raw query text, telemetry remains user-agnostic at the Search layer.
- **A new REST `POST /search` endpoint** — out of scope; FEAT-039b hooks the existing MCP tools. If a future feature adds a REST search endpoint, it will need to call the same `TelemetryWriter`.

## Key Decisions
- **JSONL on disk, not SQLite / LanceDB**: append-only, crash-safe, zero new dependencies. SQLite is the obvious upgrade path when 039c needs indexed reads.
- **Opt-in default (`enabled = false`)**: conservative privacy posture; no data collected without an explicit configuration change.
- **Two separate flags (`enabled` for local logs, `export_enabled` reserved + rejected in v1)**: prevents "enabled" from quietly meaning "transmitted" in some future config drift. We are honest in the ADR that the actual v1 enforcement is the absence of export code; the flag rejection is an early warning, not the security boundary.
- **Hook at MCP tool wrapper for retrieval, REST handler for routing**: matches the actual architecture. Keeps telemetry out of `Pipeline.search()` so business logic stays clean.
- **Queue + single writer task, not per-request `asyncio.create_task`**: resolves the fire-and-forget vs clean-shutdown contradiction. The single drain task is tracked in `app.state._background_tasks`, can be cancelled, and offers a bounded drain on shutdown. Per-request `create_task` would leak orphaned tasks at shutdown and produce "Task was destroyed but it is pending" warnings.
- **Construction via factory methods, never from request body**: `TelemetryEntry.from_search_tool_result(...)` accepts only the safe fields. The raw query string never enters the telemetry layer. Combined with `extra="forbid"` on the Pydantic model, accidental query-text logging requires deliberate code, not a slip.
- **File age from filename, not `mtime`**: the pruner parses `YYYY-MM-DD.jsonl` and compares against `today (UTC)`. Avoids cross-platform `mtime` differences and lets tests inject a fake `now`.
- **Errors swallowed at one specific layer with one specific behavior**: see Error handling section below.

## Error handling (testable specification)
- **Writer task** catches `OSError` and `ValueError` only (disk failures and serialization failures). Any other exception propagates and crashes the writer task — the lifespan logs the crash via `archon.search` logger at ERROR level and the server continues running with telemetry disabled for the rest of the process lifetime. This is intentional: a bug in the writer should be loud, not silent.
- **Caught `OSError` / `ValueError`** is logged via `archon.search` logger at WARNING level with a rate limit of one warning per file per minute (use `logging.Filter` or a small counter). The search response is unaffected.
- **Queue full** (writer fell behind): drop the oldest entry, increment a `telemetry_dropped_total` counter logged at WARNING once per minute. Default queue size 1024 — bounded.
- **Entry serialization too large**: if `json.dumps(entry).encode("utf-8")` exceeds 8 KiB, truncate `result_doc_ids` to the first N that fit and append a `truncated: true` field. The 8 KiB limit reflects realistic JSONL line-size limits on common filesystems; `O_APPEND` on regular POSIX files is atomic regardless of size (the PIPE_BUF concern in earlier drafts conflated pipe and file semantics — single-writer is safe for any size, and we have one writer task).
- **Single-writer guarantee**: because there is one queue drainer per Search server process, append interleaving from multiple writers does not occur. Multi-process deployments (rare for archon-search; the server is single-process by design) would each have their own writer task; if operators deploy multiple processes against the same `~/.archon/search-logs/` directory, they each write to a process-distinct file (`YYYY-MM-DD.<pid>.jsonl`) — but this is documented as a v1 limitation, not a v1 feature.

## Edge Cases & Constraints
- **Disk full / log write fails**: caught at the writer task per Error handling above; the search response is unaffected.
- **Server restart mid-day**: today's `.jsonl` file is reopened in append mode by the new writer task; no data loss for committed lines.
- **Clock skew**: timestamps are always server-side UTC via `datetime.now(UTC)`; client clocks are never trusted.
- **UTC day rollover**: writer detects the boundary by comparing `datetime.now(UTC).date()` against the current file's date on each entry; rotates atomically (close, open new file).
- **Retention worker missed run (server down for days)**: the pruner runs once at lifespan startup before accepting requests, then daily. Multi-day backlog is acceptable.
- **Pruner / writer race on day-boundary file**: the pruner only deletes files where `filename_date <= today - retention_days`. Today's file is never a deletion candidate; the writer task is the only process writing.
- **`export_enabled = true` in v1 config**: config-load `ConfigError`; server logs the rejection at WARNING and fails to start. Operators see a clear pointer to FEAT-039c.
- **Failed / rejected queries**: explicitly logged as error entries with `status` set to the failure kind and a redacted `error_kind`. The diagnostic value is the whole point of the brief — silent success-only logging would defeat the goal.
- **Path-derived `doc_id` privacy risk**: doc_ids are derived from `source_path` in archon-search. They may contain usernames, project names, or other path-based PII. The ADR must state this explicitly: enabling telemetry means accepting that the operator's local filesystem layout enters the log. A future hashed-doc-id mode is listed as a FEAT-039c follow-up.
- **MCP / Archon client never sees telemetry**: log files are local to the Search server's `~/.archon/search-logs/` and never traverse the HTTP boundary.
- **Windows**: archon-search is cross-platform per CLAUDE.md. `~/.archon/search-logs/` resolves via `Path.home()`; append mode works on Windows but lacks POSIX append atomicity. The single-writer queue design avoids this entirely — there is one writer per process.

## Open Questions
- **Should the writer flush after every entry or batch (e.g., flush every 1s or every 64 entries)?** Flush-per-entry is the safe default for crash safety; batching is a v1.1 optimization if telemetry hurts latency. Recommendation: flush per entry; revisit only if benchmarks demand it.
- **Should `query_id` flow back to the caller in the response?** Brief default: no. If FEAT-039c needs end-to-end correlation, it can add an opt-in response header.
- **Should the ADR live as `Documentation/ADRs/10_search_query_telemetry.md` or as an amendment to ADR 09 (search history format)?** Recommendation: standalone ADR 10 — telemetry has a different lifecycle and consent model than session history.

## Verifiability checklist (handed to /plan-maker)
The plan must include tests that observe these behaviors:
- `test_telemetry_disabled_writes_nothing` — with `enabled=false`, no files appear under `~/.archon/search-logs/` after queries.
- `test_telemetry_entry_schema_is_exhaustive` — parse a logged JSONL line, assert key set is EXACTLY the documented schema; any extra key fails CI. This is the privacy regression guard.
- `test_no_query_text_in_log_under_sentinel` — issue a search with a sentinel UUID inside the query string; assert the sentinel never appears in the JSONL file.
- `test_export_enabled_true_raises_config_error` — assert `ConfigError` with the documented message substring; assert the WARNING log line is emitted.
- `test_pruner_deletes_old_files_with_injected_now` — create files dated 1–60 days ago, run pruner with `now=...`, assert exactly the files older than `retention_days` were deleted.
- `test_pruner_never_deletes_today_or_future` — files dated today or tomorrow are never deleted regardless of mtime.
- `test_writer_drains_on_lifespan_shutdown` — enqueue entries, shut down, assert all are flushed (within the bounded drain timeout).
- `test_failed_query_logs_error_entry` — invalid `/route` request produces an error entry with redacted `error_kind`.
- `test_oversized_entry_truncates_doc_ids` — synthetic large doc_id list; resulting JSONL line ≤ 8 KiB and contains `truncated: true`.
- `test_path_derived_doc_id_warning_in_readme` — `packages/archon-search/README.md` contains an explicit warning that doc_ids may reveal filesystem paths.

## Future Iterations (handed to FEAT-039c)
- Relevance feedback API (`POST /feedback`) and Telegram-side capture UX (thumbs / inline keyboard).
- Promotion tool: `archon-search telemetry export-fixture --since YYYY-MM-DD` producing a candidate eval fixture for human review.
- Optional raw-query-text logging behind a second-level opt-in flag (`[telemetry].log_query_text`), with hashing as the safer middle ground.
- Optional hashed-doc-id mode (`[telemetry].hash_doc_ids = true`) for operators who want telemetry without filesystem-path exposure.
- Indexed analytics layer (SQLite or DuckDB), populated from JSONL on demand.
- Read-back HTTP endpoint with auth, scoped to local UNIX socket if exposed at all.
- External transmission model (`export_enabled = true`) with explicit destination, transport security, and consent UI.

## Recommendation
Build this now, exactly at the scope above. The hardest part is **not** the JSONL writer — it's the privacy contract: getting the policy doc precise enough that FEAT-039c inherits a clear "what changes, and why" envelope. Do not compromise on the opt-in default, the `export_enabled` reservation, the `extra="forbid"` Pydantic guarantee, or the no-query-text rule — every shortcut here becomes a privacy liability later. The implementation itself is one new `telemetry/` module, two MCP tool wrappers, one REST handler edit, one lifespan registration, and one ADR. If the work expands beyond that, scope has drifted into 039c.
