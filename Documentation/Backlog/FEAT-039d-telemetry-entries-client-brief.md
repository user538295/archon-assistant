---
Purpose: Feature brief for FEAT-039d — SearchClient.telemetry_entries() programmatic access
Audience: Implementers; reviewers of the SearchClient API surface
Status: Draft
Last reviewed: 2026-05-15
---

# Feature Brief: FEAT-039d — Telemetry Entries Client Access

## Problem
`GET /telemetry/entries` exists in archon-search since FEAT-039c, but `SearchClient` has no method to call it. Any code that needs programmatic access to raw telemetry entries (eval tooling, future diagnostic tools) must construct HTTP calls manually.

## Goal
`SearchClient.telemetry_entries()` exists, follows the established `SearchClient` convention (returns `None` on any failure, never raises), and passes all tests.

## Users & Context
Archon internal code and eval tooling that need to query raw telemetry entries programmatically — not end users. The HTTP endpoint remains the primary interface for direct human use (curl, scripts).

## Core Flow
1. Caller invokes `search_client.telemetry_entries(since=..., until=..., collection=..., ...)`.
2. `SearchClient` builds query params (omitting `None` values), calls `GET /telemetry/entries`.
3. On success: returns the response dict. Full `EntriesResponse` shape: `schema_version: int`, `enabled: bool`, `entries: list[dict]`, `next_offset: int`, `total_in_window: int`, `skipped_lines: int`.
4. On telemetry disabled: server returns HTTP 200 `{"enabled": false}`; `SearchClient` returns this dict as-is (not `None`). Callers check the `enabled` key, same as `telemetry_stats()`.
5. On any failure (network error, non-2xx, timeout): returns `None`.

## In Scope
- `SearchClient.telemetry_entries()` method with all filter params mirroring the HTTP endpoint (`since`, `until`, `collection`, `endpoint`, `status`, `error_kind`, `offset`, `limit`)
- `telemetry_entries()` return type: `dict[str, Any] | None` — matching `telemetry_stats()`
- Unit tests covering:
  - Successful response: mock returns full `EntriesResponse` shape; assert `entries`, `next_offset`, `total_in_window`, `skipped_lines` present in returned dict
  - Telemetry disabled: server returns HTTP 200 `{"enabled": false}`; assert method returns `{"enabled": false}` as-is (not `None`)
  - `None` on network error
  - `None` on non-2xx (e.g., HTTP 500)
  - `None` on HTTP 400 (`since > until`)
  - Param omission — zero args: no params in query string
  - Param omission — partial args: `collection="docs", limit=10` supplied; assert `since`, `until`, `status`, `error_kind`, `offset` absent from query string
  - Integer params: `offset=10, limit=25` appears correctly in query string

## Out of Scope
- `telemetry_entries` MCP tool — entries responses are too large for MCP output limits; HTTP endpoint is sufficient
- `POST /feedback` relevance capture — deferred to a future feature when a concrete quality problem is identified
- `search_feedback` MCP tool — same reason
- Fixture promotion tooling — no feedback data to promote
- Export transmission (`export_enabled` implementation) — FEAT-039e

## Key Decisions
- **No MCP tool**: MCP output size limits make paginated entry lists impractical; the HTTP endpoint already covers direct access needs.
- **Feedback descoped**: relevance feedback is search engineering infrastructure with no current quality problem driving it — build it when the eval harness surfaces a gap, not speculatively.
- **Follows `SearchClient` convention exactly**: returns `None` on any failure, never raises, omits `None` params from query string — same as `telemetry_stats()`. HTTP 200 `{"enabled": false}` is not a failure; it is returned as-is.
- **No client-side param validation**: `endpoint`, `status`, `error_kind` are `str | None` pass-through — no `StrEnum` imports needed. Invalid values result in HTTP 422 → `None`.

## Edge Cases & Constraints
- **All params optional**: method must work with zero arguments (returns first page of all entries).
- **`None` param omission**: params with `None` value must not appear in the query string (server treats absent param differently from empty string).
- **`since`/`until` format**: `YYYY-MM-DD` strings. `since > until` causes HTTP 400; `SearchClient` returns `None` (non-2xx → `None` convention handles this).
- **`limit` range**: server enforces 1–200 (default 50). Values outside this range return HTTP 422 → `None`.
- **`offset` range**: must be `>= 0` (default 0). Negative values return HTTP 422 → `None`.
- **Telemetry disabled**: server returns HTTP 200 `{"enabled": false}` — a different shape from the normal entries response. `SearchClient` returns this as-is; callers must check `enabled` before accessing `entries`. The disabled dict contains ONLY `enabled: false` — no `entries`, `next_offset`, `skipped_lines`, or other fields. Accessing any other key will raise `KeyError`.
- **`endpoint`, `status`, `error_kind` types**: passed as `str | None` with no client-side enum validation — implementers must not import `StrEnum` types for these params. Validation is the server's responsibility.
- **Pagination termination**: callers iterating all pages must stop when `next_offset >= total_in_window`. Alternatively: stop when `entries` is empty — simpler and handles cases where data changes between page requests.
- **Service unreachable**: returns `None`, same as all other `SearchClient` methods.

## Open Questions
None — scope is narrow and fully determined by existing patterns.

## Future Iterations
- `telemetry_entries` MCP tool if MCP size limits increase or a streaming MCP protocol is available
- `telemetry_entries_iter()` async generator helper for callers that need to paginate all entries without managing `offset`/`total_in_window` manually
- `POST /feedback` + `search_feedback` MCP tool when eval harness identifies a retrieval quality gap
- Fixture promotion tooling once feedback data exists

## Recommendation
This is a small, well-bounded plumbing task — one method, following an existing pattern, with an anticipated future caller (eval tooling). The right call was to descope feedback: no quality problem has been identified that needs a feedback loop yet. Ship this in an hour, keep the door open for FEAT-039e (export) and a future feedback feature when there's a reason to build it.
