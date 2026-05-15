# FEAT-039d — SearchClient.telemetry_entries()
**Purpose**: Add `SearchClient.telemetry_entries()` to expose the `GET /telemetry/entries` HTTP endpoint via the established `SearchClient` client convention.
**Audience**: Implementers; reviewers of the `SearchClient` API surface.
**Status**: To Do

---

## Background

FEAT-039c shipped `GET /telemetry/entries` in archon-search and `SearchClient.telemetry_stats()` for the stats endpoint, but left `telemetry_entries` un-mapped. Any code that needs programmatic access to raw telemetry entries — eval tooling, future diagnostic tools — currently has to construct HTTP calls by hand. This task closes that gap with the smallest possible addition: one method, following the existing `telemetry_stats()` pattern exactly.

Source brief: `Documentation/Backlog/FEAT-039d-telemetry-entries-client-brief.md` (fully determined by existing patterns, no open questions).

Existing reference implementation:
- `archon/ai/search_client.py:317` — `SearchClient.telemetry_stats()` (pattern to mirror)
- `tests/ai/test_search_client.py:928` — `TestTelemetryStats` test class (structure to follow)

---

## Goal

`SearchClient.telemetry_entries()` exists, accepts all filter params mirroring the `GET /telemetry/entries` HTTP endpoint, returns the response dict on success (`None` on any failure), omits `None` params from the query string, and passes all unit tests. No other component changes.

---

## Scope

### In Scope
- `SearchClient.telemetry_entries()` method with params: `since`, `until`, `collection`, `endpoint`, `status`, `error_kind`, `offset`, `limit`
- Return type: `dict[str, Any] | None` — identical convention to `telemetry_stats()`
- Unit tests for all scenarios listed in the brief

### Out of Scope
- `telemetry_entries` MCP tool — MCP output size limits make paginated entry lists impractical
- `POST /feedback` relevance capture — deferred to a future feature
- `search_feedback` MCP tool — same reason
- `telemetry_entries_iter()` pagination helper — future iteration
- Export transmission (`export_enabled`) — FEAT-039e
- Any archon-search server changes — the `GET /telemetry/entries` endpoint already exists

---

## Acceptance criteria
- [ ] `SearchClient.telemetry_entries()` callable with zero arguments returns the first page of all entries on success
- [ ] All 14 unit test scenarios pass
- [ ] `None` params are not included in the HTTP query string
- [ ] Integer params (`offset`, `limit`) are serialised correctly in the query string
- [ ] HTTP 200 `{"enabled": false}` is returned as-is (not `None`)
- [ ] Any failure returns `None`; timeout and HTTP errors log at WARNING, connection refused logs at DEBUG
- [ ] `CLAUDE.md` `search_client.py` entry updated to include `telemetry_entries()`
- [ ] All existing tests continue to pass

---

## What does NOT change
- `SearchClient.telemetry_stats()` — untouched
- `archon/ai/archon_toolkit_search.py` — no new MCP tool registered
- `packages/archon-search/` — server-side endpoint already exists
- Existing `SearchClient` error handling convention — no changes to exception handling behaviour

---

## Known limitations / accepted trade-offs
- No client-side param validation for `endpoint`, `status`, `error_kind` — these are `str | None` pass-throughs; invalid values result in HTTP 422 → `None`. This is intentional: validation is the server's responsibility.
- Pagination is the caller's responsibility — no `telemetry_entries_iter()` helper shipped with this feature.

---

## Architecture

### New method

```python
# archon/ai/search_client.py — appended after telemetry_stats()

async def telemetry_entries(
    self,
    since: str | None = None,
    until: str | None = None,
    collection: str | None = None,
    endpoint: str | None = None,
    status: str | None = None,
    error_kind: str | None = None,
    offset: int | None = None,
    limit: int | None = None,
) -> dict[str, Any] | None:
```

### Behaviour
- Builds `params: dict[str, Any]` including only non-`None` values.
- Integer params (`offset`, `limit`) are included as their native `int` type — `httpx` serialises them to strings automatically.
- Empty string values (e.g. `collection=""`) are NOT filtered — only `None` is omitted. Callers should pass `None` (not `""`) to exclude a param.
- Calls `GET /telemetry/entries` via `self._http`.
- Calls `resp.raise_for_status()` — any non-2xx raises `httpx.HTTPStatusError` → caught → `None`.
- HTTP 200 with any body (including `{"enabled": false}`) → `dict(resp.json())` returned.
- Exception handling mirrors `telemetry_stats()` exactly: `TimeoutException` → `None`, `ConnectError` → `None` (debug log), `HTTPStatusError` → `None` (warning log with status code), `Exception` → `None` (warning log).

### Placement in file
- New `# /telemetry/entries` section immediately after the existing `# /telemetry/stats` section (before `# Context manager / lifecycle`).

### Data flow
```
caller → SearchClient.telemetry_entries(filters) → GET /telemetry/entries?<params> → archon-search
                                                                                      ↓
                                                    dict[str, Any] | None  ←  JSON response
```

---

## Tests

- **test_telemetry_entries_success** (unit): 200 response with full `EntriesResponse` shape; assert mock was called with `/telemetry/entries` path; assert all six keys present: `schema_version`, `enabled`, `entries`, `next_offset`, `total_in_window`, `skipped_lines`
- **test_telemetry_entries_disabled_returned_as_is** (unit): 200 `{"enabled": false}` is returned as-is (not `None`)
- **test_telemetry_entries_returns_none_on_timeout** (unit): `TimeoutException` → `None`
- **test_telemetry_entries_returns_none_on_connect_error** (unit): `ConnectError` → `None`; assert DEBUG log emitted (not WARNING)
- **test_telemetry_entries_returns_none_on_http_500** (unit): HTTP 500 → `None`
- **test_telemetry_entries_returns_none_on_http_400** (unit): HTTP 400 (`since > until`) → `None`
- **test_telemetry_entries_no_params_when_zero_args** (unit): zero-arg call sends no query params
- **test_telemetry_entries_partial_params_omitted** (unit): `collection="docs", limit=10` — only those two appear in query string; `since`, `until`, `status`, `error_kind`, `offset` absent
- **test_telemetry_entries_integer_params_serialised** (unit): call with `offset=10, limit=25`; assert params dict passed to httpx contains `offset=10` (int) and `limit=25` (int) — verifies no premature stringification by the method
- **test_telemetry_entries_returns_none_on_unexpected_exception** (unit): mock raises `RuntimeError("unexpected")` — assert result is `None`
- **test_telemetry_entries_all_params_forwarded** (unit): call with all 8 params (`since="2026-01-01"`, `until="2026-05-01"`, `collection="docs"`, `endpoint="/search"`, `status="ok"`, `error_kind="timeout"`, `offset=5`, `limit=20`); assert all 8 keys appear in the query params dict captured by the mock
- **test_telemetry_entries_returns_none_on_http_422** (unit): mock returns HTTP 422 (invalid `status` value) — assert result is `None`
- **test_telemetry_entries_warning_logged_on_timeout** (unit): `TimeoutException` → assert result is `None`; assert WARNING-level log record emitted (using `caplog`)
- **test_telemetry_entries_empty_string_passes_through** (unit): call with `collection=""`; assert `"collection"` key is present in the params dict passed to httpx with value `""`; verifies that empty string is NOT filtered (only `None` is omitted)

---

## Documentation update
- [ ] `CLAUDE.md`, section `archon/ai/` → `search_client.py` bullet: add `telemetry_entries()` alongside `telemetry_stats()`, path: `CLAUDE.md`

---

## Task breakdown

### Phase 1 — Implement and test
> **Releasable**: after Task 1.1 — the method is callable from any code that holds a `SearchClient` instance.

#### Task 1.1 — SearchClient.telemetry_entries() method + tests
- [x] **File**: `archon/ai/search_client.py`
- [x] **File**: `tests/ai/test_search_client.py`
- **Depends on**: nothing
- **Description**:
  - Add a `# /telemetry/entries` comment block immediately after the `# /telemetry/stats` block (before `# Context manager / lifecycle` at line 349).
  - **Before writing test fixtures**: confirm the server-side `EntriesResponse` field names by checking `packages/archon-search/` or the brief. The mock in `test_telemetry_entries_success` assumes the fields: `schema_version`, `enabled`, `entries`, `next_offset`, `total_in_window`, `skipped_lines` — verify these match the actual model.
  - Implement:
    ```python
    async def telemetry_entries(
        self,
        since: str | None = None,
        until: str | None = None,
        collection: str | None = None,
        endpoint: str | None = None,
        status: str | None = None,
        error_kind: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
    ) -> dict[str, Any] | None:
        """GET /telemetry/entries; returns entries dict or None on failure.

        A 200 response with {"enabled": false} is returned as-is — callers
        check the "enabled" key themselves.

        Only None params are omitted from the query string; empty strings
        (e.g. collection="") are passed through as-is. Pass None (not "")
        to exclude a param.
        """
        params: dict[str, Any] = {}
        if since is not None:
            params["since"] = since
        if until is not None:
            params["until"] = until
        if collection is not None:
            params["collection"] = collection
        if endpoint is not None:
            params["endpoint"] = endpoint
        if status is not None:
            params["status"] = status
        if error_kind is not None:
            params["error_kind"] = error_kind
        if offset is not None:
            params["offset"] = offset
        if limit is not None:
            params["limit"] = limit
        try:
            resp = await self._http.get("/telemetry/entries", params=params)
            resp.raise_for_status()
            return dict(resp.json())
        except httpx.TimeoutException:
            logger.warning("SearchClient.telemetry_entries: timed out")
            return None
        except httpx.ConnectError:
            logger.debug("SearchClient.telemetry_entries: connection refused (%s)", self._base_url)
            return None
        except httpx.HTTPStatusError as exc:
            logger.warning("SearchClient.telemetry_entries: HTTP %s", exc.response.status_code)
            return None
        except Exception as exc:
            logger.warning("SearchClient.telemetry_entries: unexpected error: %s", exc)
            return None
    ```
  - Add test class `TestTelemetryEntries` in `tests/ai/test_search_client.py` immediately after `TestTelemetryStats`, following the same mock-transport pattern used in that class.
  - Test cases:
    - `test_telemetry_entries_success`: mock returns `{"schema_version": 1, "enabled": True, "entries": [{"id": "x"}], "next_offset": 1, "total_in_window": 1, "skipped_lines": 0}`; assert mock was called with `/telemetry/entries` path; assert all six keys present: `schema_version`, `enabled`, `entries`, `next_offset`, `total_in_window`, `skipped_lines`.
    - `test_telemetry_entries_disabled_returned_as_is`: mock returns HTTP 200 `{"enabled": False}`; assert result == `{"enabled": False}`.
    - `test_telemetry_entries_returns_none_on_timeout`: mock raises `httpx.TimeoutException`; assert result is `None`.
    - `test_telemetry_entries_returns_none_on_connect_error`: mock raises `httpx.ConnectError`; assert result is `None`; assert DEBUG log emitted (not WARNING).
    - `test_telemetry_entries_returns_none_on_http_500`: mock returns HTTP 500; assert result is `None`.
    - `test_telemetry_entries_returns_none_on_http_400`: mock returns HTTP 400; assert result is `None`.
    - `test_telemetry_entries_no_params_when_zero_args`: mock captures request; assert `request.url.params` is empty.
    - `test_telemetry_entries_partial_params_omitted`: call with `collection="docs", limit=10`; assert query string contains `collection` and `limit`, and does NOT contain `since`, `until`, `status`, `error_kind`, `offset`.
    - `test_telemetry_entries_integer_params_serialised`: call with `offset=10, limit=25`; assert params dict passed to httpx contains `offset=10` (int) and `limit=25` (int) — verifies no premature stringification by the method.
    - `test_telemetry_entries_returns_none_on_unexpected_exception`: mock raises `RuntimeError("unexpected")`; assert result is `None`.
    - `test_telemetry_entries_all_params_forwarded`: call with all 8 params (`since="2026-01-01"`, `until="2026-05-01"`, `collection="docs"`, `endpoint="/search"`, `status="ok"`, `error_kind="timeout"`, `offset=5`, `limit=20`); assert all 8 keys appear in the query params dict captured by the mock.
    - `test_telemetry_entries_returns_none_on_http_422`: mock returns HTTP 422 (invalid `status` value); assert result is `None`.
    - `test_telemetry_entries_warning_logged_on_timeout`: mock raises `httpx.TimeoutException`; assert result is `None`; assert WARNING-level log record emitted (using `caplog`).
    - `test_telemetry_entries_empty_string_passes_through`: call with `collection=""`; assert `"collection"` key is present in the params dict passed to httpx with value `""`; verifies that empty string is NOT filtered (only `None` is omitted).
- **Releasable**: after this task, any code holding a `SearchClient` instance can call `telemetry_entries()` to retrieve raw telemetry entries.
- **Tests (TDD)** — `tests/ai/test_search_client.py`:
  - Unit: `test_telemetry_entries_success` — `/telemetry/entries` path called; all six keys present in result
  - Unit: `test_telemetry_entries_disabled_returned_as_is` — `{"enabled": false}` returned as-is
  - Unit: `test_telemetry_entries_returns_none_on_timeout` — TimeoutException → None
  - Unit: `test_telemetry_entries_returns_none_on_connect_error` — ConnectError → None; DEBUG log (not WARNING)
  - Unit: `test_telemetry_entries_returns_none_on_http_500` — HTTP 500 → None
  - Unit: `test_telemetry_entries_returns_none_on_http_400` — HTTP 400 → None
  - Unit: `test_telemetry_entries_no_params_when_zero_args` — zero-arg call has empty query string
  - Unit: `test_telemetry_entries_partial_params_omitted` — absent params not in query string
  - Unit: `test_telemetry_entries_integer_params_serialised` — offset and limit passed as int (no premature stringification)
  - Unit: `test_telemetry_entries_returns_none_on_unexpected_exception` — RuntimeError → None
  - Unit: `test_telemetry_entries_all_params_forwarded` — all 8 params present in query params dict
  - Unit: `test_telemetry_entries_returns_none_on_http_422` — HTTP 422 → None
  - Unit: `test_telemetry_entries_warning_logged_on_timeout` — TimeoutException → None; WARNING log emitted (caplog)
  - Unit: `test_telemetry_entries_empty_string_passes_through` — `collection=""` present in params (empty string not filtered)
  - Checkpoint (14 tests): `uv run pytest tests/ai/test_search_client.py -k "TelemetryEntries" -v`

#### Task 1.2 — CLAUDE.md documentation update
- [ ] **File**: `CLAUDE.md`
- **Depends on**: Task 1.1
- **Description**:
  - In the `archon/ai/` → `search_client.py` bullet in `CLAUDE.md`, add `telemetry_entries()` to the list of methods alongside `telemetry_stats()`.
  - Current text (search for): `GET /telemetry/stats` (aggregated query statistics), `GET /telemetry/entries` (raw JSONL log entries with optional filters)
  - The `search_client.py` bullet currently reads: `SearchClient` — async HTTP client adapter … `get_search_client()` / `reset_search_client()` singletons; all methods return `None`/`[]`/status code on failure, never raise
  - No structural change needed — the existing CLAUDE.md description of the HTTP endpoints already references both `/telemetry/stats` and `/telemetry/entries`; confirm the `telemetry_stats()` and `telemetry_entries()` methods are both listed in the `search_client.py` bullet.
- **Releasable**: after this task, CLAUDE.md accurately documents both telemetry client methods.
- **Tests (TDD)** — N/A (documentation task; verify by reading the updated section)
  - Checkpoint: `grep -n "telemetry_entries" CLAUDE.md`
