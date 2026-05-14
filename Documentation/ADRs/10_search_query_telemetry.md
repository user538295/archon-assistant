**Purpose**: Documents the decision to add opt-in local query telemetry to `archon-search`, including privacy constraints, on-disk format, and boundaries.
**Audience**: Backend engineers, security reviewers
**Status**: Accepted
**Last reviewed**: 2026-05-14
**Next review**: 2026-08-14

---

# ADR 10 — Search Query Telemetry

**Status**: Accepted
**Date**: 2026-05-14
**Deciders**: Project maintainer

## Status

Accepted

## Context

`archon-search` handles semantic search over the user's local knowledge base. To allow operators to
understand query patterns, diagnose retrieval quality, and tune configuration, a lightweight
observability layer is useful. At the same time, Archon is a local-first, privacy-respecting tool:
the user's documents, queries, and conversation history must never leave the machine without
explicit consent.

Key constraints driving this decision:

- Telemetry must be **opt-in** — disabled by default; no data is written unless the operator sets
  `[telemetry] enabled = true` in `archon-search.toml`.
- Query text is sensitive personal data. Logging raw query strings in v1 is not acceptable without
  a separate, explicit consent mechanism that does not yet exist.
- The codebase is the security boundary. Operators cannot accidentally exfiltrate data through
  misconfiguration — the export path must simply not exist in this version.
- A single-process, single-writer design is required because `archon-search` runs as a single
  Python process. Multiple concurrent writers to the same JSONL file would require locking logic
  that is not warranted at this scale.

## Decision

### Opt-in by default

Telemetry is controlled by `[telemetry] enabled` in `archon-search.toml`, defaulting to `false`.
No JSONL file is created and no entries are enqueued unless `enabled = true`.

### JSONL on disk, local-only

Each telemetry entry is serialised as a single JSON object followed by a newline (`\n`) and
appended to a daily log file:

```
~/.archon/search-logs/YYYY-MM-DD.jsonl
```

The date in the filename determines the file's logical age for retention purposes — no `mtime`
stat is required, avoiding race conditions on file rotation.

### No raw query text in v1

`TelemetryEntry` fields intentionally **omit** the raw query string. Recorded fields are:

| Field | Type | Description |
|---|---|---|
| `query_id` | str | UUID hex, unique per entry |
| `timestamp` | ISO-8601 string | UTC timestamp of the event |
| `endpoint` | EndpointKind Literal | One of `"search"` \| `"search_with_context"` \| `"route"` |
| `collection` | string or null | Collection name (for single-collection calls) |
| `collections` | list[str] or null | Collection list (for routing calls) |
| `result_doc_ids` | list[str] or null | Doc IDs returned (path-derived — see Privacy section) |
| `truncated` | bool or null | `true` when `result_doc_ids` was truncated to fit 8 KiB limit |
| `result_count` | int or null | Number of results returned |
| `latency_ms` | float | End-to-end latency in milliseconds |
| `decomposer_invoked` | bool or null | Whether the LLM decomposer was called (routing only) |
| `status` | Status Literal | Status of the call (all entries): `"ok"` \| `"validation_error"` \| `"timeout"` \| `"internal_error"` |
| `error_kind` | string or null | Error category (error entries only) |

This schema provides sufficient signal for latency analysis, collection usage patterns, and error
rates without capturing any user-supplied text.

### Factories enforce structural privacy

All `TelemetryEntry` instances are created through named classmethods:

- `TelemetryEntry.from_search_tool_result(*, endpoint, collection, result_doc_ids, latency_ms)`
- `TelemetryEntry.from_route_response(*, collections, decomposer_invoked, latency_ms)`
- `TelemetryEntry.from_error(*, endpoint, status, error_kind, latency_ms)`

No factory accepts a query string parameter. This is a structural guarantee, not a documentation
convention — a caller cannot accidentally log a query by misusing the API.

### Single drain task, single-writer-per-process

An `asyncio.Queue` buffers entries written by request handlers. A single background drain task
consumes the queue and writes to the JSONL file. This design:

- Eliminates file-level locking entirely (one writer at a time by construction).
- Decouples request handling latency from disk I/O.
- Simplifies shutdown: drain the queue then close the file handle.

Because `archon-search` runs as a single OS process, one drain task is sufficient.

### File-age from filename

Log rotation and retention pruning use the date embedded in the filename (`YYYY-MM-DD.jsonl`)
rather than filesystem `mtime`. This avoids ambiguity when files are moved, backed up, or copied,
and makes the pruning logic deterministic regardless of filesystem behaviour.

### Retention default: 30 days

The default `[telemetry] retention_days = 30` is applied by the `Pruner`. Files older
than `retention_days` are deleted at startup (and optionally on a schedule). Operators may increase
or decrease this value; minimum is `1` day (enforced at config load with a `ConfigError`).

## Consequences

### Positive

- Operators who opt in gain latency, error-rate, and collection-usage data without exposing query
  content.
- The single-writer design requires no file locking and adds minimal overhead to request handling.
- Structural privacy through factories makes accidental query logging impossible — it requires a
  deliberate API change, not just a config mistake.
- Filename-based age calculation is deterministic and portable across OS and filesystem types.

### Negative

- No query text means operators cannot diagnose *what* was searched, only *that* a search happened
  and how fast it was.
- JSONL files must be manually shipped to any external analysis tool — there is no built-in
  aggregation or dashboard in v1.
- `result_doc_ids` contain path-derived identifiers (see Privacy section below), which may carry
  indirect path-PII.

## Privacy

### Path-derived `doc_id` risk

`result_doc_ids` are derived from the file path of the indexed document, typically by normalising
the absolute path into a collection-relative identifier (e.g.,
`docs/2026-05-14.md` → `archon-history/docs/2026-05-14.md`). This means:

- **Directory structure is visible**: a `doc_id` like `personal/finances/receipts/2025.pdf` reveals
  that such a file exists and was retrieved.
- **User home path may appear**: if the collection root is `~` and the normalisation is naive, the
  username portion of the path could be embedded.

In v1 this is accepted because the JSONL files are stored locally under `~/.archon/search-logs/`
and are not transmitted anywhere. Operators who share or export telemetry logs manually must be
aware that `doc_id` values may reveal directory structure.

A future version may hash or truncate `doc_id` values before recording them. Until then, the
operator's responsibility is to treat telemetry files with the same sensitivity as the indexed
documents themselves.

## Why `export_enabled` is not a security boundary

The config field `export_enabled` is reserved for FEAT-039c (remote telemetry export). Setting
`export_enabled = true` in `archon-search.toml` currently raises a `ConfigError` at startup — it
is explicitly blocked, not silently ignored.

The real defence against telemetry data leaving the machine in v1 is the **absence of export code**:
there is no HTTP client, no remote endpoint, and no serialisation path that transmits data off
the local filesystem. `export_enabled = false` (the enforced default) is a documentation signal,
not a firewall. An operator who somehow bypassed the config guard would find no code to execute.

This design is intentional: relying on a runtime flag to prevent data exfiltration is fragile.
The absence of the capability itself is the only trustworthy guarantee at this stage.

## Open questions / FEAT-039c hooks

The following questions are deferred to FEAT-039c (remote telemetry export):

1. **Consent flow**: What UI/confirmation is required before enabling remote export? A one-time
   interactive prompt? A separate signed config field?
2. **Transport security**: mTLS, bearer token, or something else for the upload endpoint?
3. **`doc_id` sanitisation**: Should `doc_id` values be hashed before remote transmission even if
   they are acceptable in local JSONL files?
4. **Sampling**: Should high-volume deployments sample entries before export to limit bandwidth?
5. **Schema versioning**: The JSONL schema must be versioned (`"schema_version": 1`) before any
   remote consumer is built against it.

Until FEAT-039c is scoped and accepted, `export_enabled = true` remains a hard error.

## Related Documents

- `packages/archon-search/archon_search/telemetry/entry.py` — `TelemetryEntry`, factories, `DOCUMENTED_SCHEMA_FIELDS`
- `packages/archon-search/archon_search/telemetry/writer.py` — `TelemetryWriter`
- `packages/archon-search/archon_search/telemetry/pruner.py` — `Pruner` retention logic
- `packages/archon-search/tests/telemetry/` — unit tests for all telemetry components
- [`Documentation/ADRs/09_search_history_format.md`](09_search_history_format.md) — search integration context
