# FEAT-042 — Search Namespace Data Model (5b)
**Purpose**: Add `namespace: str` field to every collection record so that namespace isolation (5c) has a ready data layer — no enforcement yet, purely additive.
**Audience**: Implementers; reviewers preparing 5c.
**Status**: To Do

---

## Background

archon-search has no concept of namespace — every collection is globally visible and accessible to any authenticated caller. Before namespace isolation (5c) can be enforced, the data model must carry the `namespace` field everywhere a collection is identified or returned.

Source brief: `Documentation/Backlog/search-namespace-5b-data-model-brief.md`

---

## Goal

Every collection record carries a `namespace: str` field defaulted to `"default"`. Existing rows in `_archon_collection_meta` are migrated on startup — idempotently — by adding the column and backfilling with `"default"`. REST responses for `GET /collections/` and `GET /collections/{name}` include `"namespace": "default"`. No enforcement, no filtering, no API parameter for namespace. Existing callers see no breaking changes.

---

## Scope

### In Scope
> Note: All `"default"` string literals below refer to `DEFAULT_NAMESPACE` from `constants.py` in implementation. Task descriptions are authoritative for exact code form.

- Add `namespace: str = "default"` to `CollectionMeta` dataclass (`collection_meta.py`)
- Add `namespace: str = "default"` to `CollectionInfo` dataclass (`_types.py`)
- Add `namespace` column (PyArrow `utf8`) to `_meta_schema()` in `store.py` — ensures fresh installs create it correctly
- `_row_to_meta()` reads `namespace` via `row.get("namespace", "default")` — safe against mid-migration race
- `update_collection_meta()` includes `"namespace": meta.namespace` in its row dict construction
- `list_collections()` passes `namespace="default"` to `CollectionInfo` constructor (hardcoded — no metadata lookup)
- `SearchStore.migrate_namespace()` — new async method: opens `_archon_collection_meta` if it exists, checks schema, calls `add_columns({'namespace': "'default'"})` if absent, catches column-already-exists error
- Wire migration into `app.py` lifespan: `await app.state.search_store.migrate_namespace()` immediately after `connect()`
- Add `"namespace": "default"` to `GET /collections/` response entries (config-path based)
- Add `"namespace": "default"` to `GET /collections/{name}` response dict

### Out of Scope
- Namespace enforcement / filtering (5c)
- `namespace` field in `/status` endpoint
- Key → namespace mapping (5c)
- `namespace` parameter on any API endpoint
- Per-chunk namespace field
- `[namespace]` config section
- Multiple named namespaces
- `Collection` / `CollectionDetail` types in `types.py` (public API types)

---

## Acceptance criteria
- [ ] `CollectionMeta.namespace` field exists with default `"default"`
- [ ] `CollectionInfo.namespace` field exists with default `"default"`
- [ ] `_meta_schema()` includes `namespace` column — fresh install creates table with column
- [ ] `update_collection_meta()` writes `namespace` value to the row
- [ ] `_row_to_meta()` returns `namespace="default"` for rows missing the key
- [ ] `list_collections()` returns `CollectionInfo` with `namespace="default"`
- [ ] `migrate_namespace()` is idempotent: calling it twice does not raise
- [ ] `migrate_namespace()` skips tables when `_archon_collection_meta` does not exist
- [ ] `migrate_namespace()` is a no-op when `namespace` column already exists
- [ ] Migration wired into lifespan before first request is handled
- [ ] `GET /collections/` response includes `"namespace": "default"` per entry
- [ ] `GET /collections/{name}` response includes `"namespace": "default"`
- [ ] All existing tests pass unchanged

---

## What does NOT change
- `/status` endpoint responses — collection entries there are operational snapshots, not domain records
- `types.py` public `Collection` / `CollectionDetail` types consumed by `archon/ai/search_client.py`
- `GET /collections/` data source (config paths, not `SearchStore.list_collections()`) — field is added inline
- `SearchStore.list_collections()` enumeration logic — only the `CollectionInfo` constructor call gains the field
- Upsert key in `update_collection_meta()` — remains `name`-only (5c concern, explicitly deferred)

---

## Known limitations / accepted trade-offs
- Only `"default"` is ever written — no multi-namespace support until 5c.
- `list_collections()` hardcodes `"default"` without meta-table lookup — correct for this increment; 5c must fix this.
- Concurrent startup TOCTOU: schema check and `add_columns` are not atomic. Handled by catching the column-already-exists error from LanceDB.

---

## Architecture

### New/modified modules

| File | Change |
|---|---|
| `archon_search/collection_meta.py` | Add `namespace: str = "default"` field |
| `archon_search/_types.py` | Add `namespace: str = "default"` to `CollectionInfo` |
| `archon_search/store.py` | `_meta_schema()` + `_row_to_meta()` + `update_collection_meta()` + `list_collections()` + new `migrate_namespace()` |
| `archon_search/server/app.py` | Call `migrate_namespace()` in lifespan after `connect()` |
| `archon_search/server/routes_collections.py` | Add `"namespace"` key to `GET /collections/` and `GET /collections/{name}` inline dicts |

### Data flow
1. Server starts → `connect()` → `migrate_namespace()` (adds column if absent, backfills "default").
2. All subsequent `update_collection_meta()` calls write `namespace=meta.namespace`.
3. All `_row_to_meta()` reads use `.get("namespace", "default")` — safe for rows predating migration.
4. REST routes build their response dicts independently from the store; both explicitly set `"namespace": "default"`.

### Key method signature

```python
async def migrate_namespace(self) -> None:
    """Idempotent: adds namespace column to _archon_collection_meta if absent."""
```

---

## Tests

1. **test_collection_meta_namespace_field** (unit) — `CollectionMeta` has `namespace` attribute defaulting to `"default"` — from Task 1.1
2. **test_collection_meta_namespace_custom** (unit) — `CollectionMeta(name="x", namespace="foo")` stores the custom value — from Task 1.1
3. **test_collection_info_namespace_field** (unit) — `CollectionInfo` has `namespace` attribute defaulting to `"default"` — from Task 1.2
4. **test_collection_info_namespace_custom** (unit) — explicit `namespace="foo"` is stored on `CollectionInfo` — from Task 1.2
5. **test_meta_schema_includes_namespace** (unit) — `_meta_schema()` field names contain `"namespace"` — from Task 2.1
6. **test_row_to_meta_reads_namespace** (unit) — `_row_to_meta` with explicit `namespace` key returns that value — from Task 2.2
7. **test_row_to_meta_missing_namespace_defaults** (unit) — `_row_to_meta` with row dict missing `namespace` key returns `"default"` — from Task 2.2
8. **test_update_collection_meta_writes_namespace** (integration) — write `namespace="tenant-x"`, read back raw row dict directly (not through `get_collection_meta()`), assert raw dict `"namespace"` key equals `"tenant-x"` — from Task 2.3
9. **test_update_collection_meta_round_trip_namespace_preserved** (integration) — write `namespace="foo"`, read back, assert preserved — from Task 2.3
10. **test_get_all_collections_meta_returns_namespace** (integration) — write two meta rows, call `get_all_collections_meta()`, assert all returned `CollectionMeta` objects have `.namespace == DEFAULT_NAMESPACE` — from Task 2.3 (NEW)
11. **test_list_collections_includes_namespace** (integration) — `list_collections()` returns `CollectionInfo` with `namespace="default"` — from Task 2.4 (store-level)
12. **test_migrate_namespace_no_meta_table** (integration) — migration when `_archon_collection_meta` does not exist is a no-op (no error) — from Task 3.1
13. **test_migrate_namespace_empty_table** (integration) — migration on empty `_archon_collection_meta` (old schema) adds column without error — from Task 3.1
14. **test_migrate_namespace_existing_rows** (integration) — table with existing rows but no `namespace` column gains column after migration — from Task 3.1
15. **test_migrate_namespace_already_migrated** (integration) — calling `migrate_namespace()` twice does not raise — from Task 3.1
16. **test_migrate_namespace_rows_backfilled** (integration) — after migration on a table with pre-existing rows, raw PyArrow read via `.column("namespace").to_pylist()` confirms all values equal `DEFAULT_NAMESPACE` — from Task 3.1
17. **test_migrate_namespace_concurrent_race** (integration) — `add_columns` raises `RuntimeError("Column namespace already exists in the dataset")` (patched via `patch.object` on the specific `AsyncTable` instance), `migrate_namespace()` handles it gracefully without re-raising — from Task 3.1 (NEW)
18. **test_lifespan_calls_migrate_namespace** (integration) — `migrate_namespace` called exactly once during lifespan, after `connect` — from Task 3.2
19. **test_routes_list_collections_namespace** (unit) — `GET /collections/` response entries include `"namespace": "default"` — from Task 4.1
20. **test_routes_get_collection_namespace** (unit) — `GET /collections/{name}` response includes `"namespace": "default"` — from Task 4.2
21. **test_collection_meta_namespace_equals_constant** (unit) — `CollectionMeta(name="x").namespace == DEFAULT_NAMESPACE` — couples dataclass default to the constant so changes to `DEFAULT_NAMESPACE` are caught immediately — from Task 1.1

---

## Documentation update
- [ ] `Documentation/Backlog/search-namespace-5b-data-model-brief.md` — no change needed (brief is read-only reference)

---

## Task breakdown

### Phase 1 — Domain types
> **Releasable**: after Task 1.2 — `CollectionMeta` and `CollectionInfo` both carry the field; all dependent layers can be updated safely. Includes Task 0.1 (constant extraction).

#### Task 0.1 — Extract `DEFAULT_NAMESPACE` constant
- [x] **File**: `packages/archon-search/archon_search/constants.py`
- **Depends on**: nothing
- **Description**:
  - Add `DEFAULT_NAMESPACE: str = "default"` to `constants.py`
  - All 5b code that uses the `"default"` string literal for namespace must import and reference this constant instead of hardcoding the string. This includes: `CollectionMeta` default value, `CollectionInfo` default value, `list_collections()` constructor call, `migrate_namespace()` `add_columns` value, `GET /collections/` inline dict, `GET /collections/{name}` inline dict.
  - Single definition ensures `grep DEFAULT_NAMESPACE` finds every namespace site for 5c
- **Releasable**: constant is available to all subsequent tasks
- **Tests (TDD)** — N/A (a constant needs no test):
  - Checkpoint: N/A

#### Task 1.1 — Add `namespace` to `CollectionMeta`
- [x] **File**: `packages/archon-search/archon_search/collection_meta.py`
- **Depends on**: nothing
- **Description**:
  - Add `namespace: str = DEFAULT_NAMESPACE` as a field on the `CollectionMeta` dataclass, after `described_at_doc_count` — import `DEFAULT_NAMESPACE` from `archon_search.constants`
  - Default value references `DEFAULT_NAMESPACE` — no callers need to change their constructor calls
- **Releasable**: `CollectionMeta` carries the field; store serialization can reference it
- **Tests (TDD)** — `packages/archon-search/tests/test_store.py`:
  - Unit: `test_collection_meta_namespace_field` — `CollectionMeta(name="x")` has `.namespace == "default"`
  - Unit: `test_collection_meta_namespace_custom` — `CollectionMeta(name="x", namespace="foo")` has `.namespace == "foo"`
  - Unit: `test_collection_meta_namespace_equals_constant` — import `DEFAULT_NAMESPACE` from `archon_search.constants`; assert `CollectionMeta(name="x").namespace == DEFAULT_NAMESPACE` — this couples the dataclass default to the constant so changes to `DEFAULT_NAMESPACE` are caught immediately
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_store.py -k "test_collection_meta_namespace" -v`

#### Task 1.2 — Add `namespace` to `CollectionInfo`
- [x] **File**: `packages/archon-search/archon_search/_types.py`
- **Depends on**: nothing (independent of Task 1.1)
- **Description**:
  - Add `namespace: str = DEFAULT_NAMESPACE` as a field on the `CollectionInfo` dataclass, after `chunk_count` — import `DEFAULT_NAMESPACE` from `archon_search.constants`
  - Default value references `DEFAULT_NAMESPACE` — existing `CollectionInfo(name=..., doc_count=..., chunk_count=...)` calls compile without change
- **Releasable**: `CollectionInfo` carries the field; `list_collections()` can pass it
- **Tests (TDD)** — `packages/archon-search/tests/test_store.py`:
  - Note: `CollectionInfo` is an internal type from `_types.py`; tests live in `test_store.py` to follow the convention for internal types.
  - Unit: `test_collection_info_namespace_field` — `CollectionInfo(name="x", doc_count=0, chunk_count=0)` has `.namespace == "default"`
  - Unit: `test_collection_info_namespace_custom` — explicit `namespace="foo"` is stored
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_store.py -k "test_collection_info_namespace" -v`

---

### Phase 2 — Store serialization layer
> **Releasable**: after Task 2.3 — fresh installs get the column; existing read/write paths are namespace-aware.

#### Task 2.1 — `_meta_schema()` adds `namespace` column
- [x] **File**: `packages/archon-search/archon_search/store.py`
- **Depends on**: Task 1.1
- **Description**:
  - Add `pa.field("namespace", pa.utf8())` to the list of fields in `_meta_schema()` (after `described_at_doc_count`)
  - This ensures fresh installs create `_archon_collection_meta` with the column from day one
  - No other change in this task
- **Releasable**: fresh installs create the table with `namespace` column present
- **Tests (TDD)** — `packages/archon-search/tests/test_store.py`:
  - Unit: `test_meta_schema_includes_namespace` — `SearchStore._meta_schema().names` contains `"namespace"`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_store.py -k "test_meta_schema_includes_namespace" -v`

#### Task 2.2 — `_row_to_meta()` reads `namespace` safely
- [x] **File**: `packages/archon-search/archon_search/store.py`
- **Depends on**: Task 1.1
- **Description**:
  - Change the `_row_to_meta()` static method to read `namespace` from the row dict using `row.get("namespace", DEFAULT_NAMESPACE)` — not `row["namespace"]`
  - Pass `namespace=row.get("namespace", DEFAULT_NAMESPACE)` to the `CollectionMeta(...)` constructor
  - This makes deserialization safe for rows that predate the migration (missing key → default)
- **Releasable**: `get_collection_meta()` and `get_all_collections_meta()` return namespace; safe against pre-migration rows
- **Tests (TDD)** — `packages/archon-search/tests/test_store.py`:
  - Unit: `test_row_to_meta_reads_namespace` — row dict with `"namespace": "custom"` → `meta.namespace == "custom"`
  - Unit: `test_row_to_meta_missing_namespace_defaults` — row dict without `"namespace"` key → `meta.namespace == "default"`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_store.py -k "test_row_to_meta" -v`

#### Task 2.3 — `update_collection_meta()` writes `namespace`
- [x] **File**: `packages/archon-search/archon_search/store.py`
- **Depends on**: Task 2.1, Task 2.2
- **Description**:
  - In `update_collection_meta()`, add `"namespace": meta.namespace` to the dict passed to `table.add()`
  - Placement: alongside `"name"`, `"description"`, etc. — after `"described_at_doc_count"`
  - No other logic change
- **Releasable**: round-trip write → read via `get_collection_meta()` preserves `namespace`
- **Tests (TDD)** — `packages/archon-search/tests/test_store.py`:
  - Integration: `test_update_collection_meta_writes_namespace` — create meta with `namespace="tenant-x"`, write via `update_collection_meta()`, read back the RAW row dict directly (via `table.query().to_list()`) and assert the `"namespace"` key in the raw dict equals `"tenant-x"` — do NOT rely solely on `get_collection_meta()` which uses `_row_to_meta()` with a fallback default
  - Integration: `test_update_collection_meta_round_trip_namespace_preserved` — write `namespace="foo"`, read back, assert preserved
  - Integration: `test_get_all_collections_meta_returns_namespace` — write two meta rows via `update_collection_meta()`, call `get_all_collections_meta()`, assert all returned `CollectionMeta` objects have `.namespace == DEFAULT_NAMESPACE`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_store.py -k "test_update_collection_meta or test_get_all_collections_meta" -v`

#### Task 2.4 — `list_collections()` passes `namespace` to `CollectionInfo`
- [ ] **File**: `packages/archon-search/archon_search/store.py`
- **Depends on**: Task 1.2
- **Description**:
  - In `list_collections()`, change `CollectionInfo(name=name, doc_count=doc_count, chunk_count=chunk_count)` to `CollectionInfo(name=name, doc_count=doc_count, chunk_count=chunk_count, namespace=DEFAULT_NAMESPACE)` — import `DEFAULT_NAMESPACE` from `constants.py`
  - Value references `DEFAULT_NAMESPACE` — no metadata lookup; per brief this is correct and intentional for 5b
- **Releasable**: `list_collections()` returns `CollectionInfo` objects with `namespace="default"`
- **Tests (TDD)** — `packages/archon-search/tests/test_store.py`:
  - Integration: `test_list_collections_includes_namespace` — after ensuring a collection exists, `list_collections()` returns items where `.namespace == "default"`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_store.py -k "test_list_collections_includes_namespace" -v`

---

### Phase 3 — Startup migration
> **Releasable**: after Task 3.1 — existing databases are migrated on startup; migration is wired before any request can observe pre-migration state.

#### Task 3.1 — `SearchStore.migrate_namespace()` method
- [ ] **File**: `packages/archon-search/archon_search/store.py`
- **Depends on**: Task 2.1 (schema context), Task 2.2 (safe deserialization)
- **Description**:
  - Add `async def migrate_namespace(self) -> None` method to `SearchStore`
  - Algorithm:
    1. `db = self._require_connected()`
    2. `all_names = (await db.list_tables()).tables`
    3. If `_META_TABLE not in all_names`: return (no-op — table doesn't exist yet)
    4. `table = await db.open_table(_META_TABLE)`
    5. `schema_names = (await table.schema()).names`
    6. If `"namespace" in schema_names`: return (already migrated — idempotent)
    7. Wrap `add_columns` in a try/except block as follows — SQL string literal; inner single quotes required:
```python
try:
    await table.add_columns({"namespace": f"'{DEFAULT_NAMESPACE}'"})
    logger.info("namespace migration: added namespace column to %s", _META_TABLE)
except Exception as exc:
    if "already exists" in str(exc).lower():
        logger.warning("Concurrent migration: namespace column already added — %s", exc)
        return
    raise
```
  - Place `logger.info("namespace migration: added namespace column to %s", _META_TABLE)` INSIDE the try block, immediately after the `add_columns()` call and before the `except` clause — it should only execute when `add_columns` succeeds without exception.
  - In the `except` clause, inspect `str(exc).lower()` for the substring `"already exists"` — the verified Lance error message is `"Column {name} already exists in the dataset"`. Only this substring check is reliable; do NOT check for `"duplicate"` or standalone `"column"` which would false-positive on unrelated errors.
- **Releasable**: `migrate_namespace()` is callable; handles all edge cases
- **Tests (TDD)** — `packages/archon-search/tests/test_store.py`:
  - Integration: `test_migrate_namespace_no_meta_table` — freshly connected store with no `_archon_collection_meta` table → `migrate_namespace()` returns without error
  - Integration: `test_migrate_namespace_empty_table` — construct the OLD schema manually (without `namespace`), create empty `_archon_collection_meta` via `db.create_table()` directly, call `migrate_namespace()`, assert `"namespace"` in schema names — no error raised
  - Integration: `test_migrate_namespace_existing_rows` — construct the OLD `_archon_collection_meta` PyArrow schema manually (identical to current `_meta_schema()` but without the `namespace` field), create the table directly via `db.create_table(_META_TABLE, schema=old_schema)`, insert a row with all existing fields, call `migrate_namespace()`, assert `"namespace"` appears in `(await table.schema()).names`
  - Integration: `test_migrate_namespace_already_migrated` — call `migrate_namespace()` twice → second call is a no-op, no exception
  - Integration: `test_migrate_namespace_rows_backfilled` — set up an old-schema table with a pre-existing row (same approach as `test_migrate_namespace_existing_rows`: construct old PyArrow schema manually, insert a row), call `migrate_namespace()`, then verify the backfill by reading the raw PyArrow table directly (`await table.query().to_arrow()`), extracting values via `.column("namespace").to_pylist()`, and asserting all values equal `DEFAULT_NAMESPACE` — use `.to_pylist()` to get native Python strings for comparison rather than PyArrow scalars. Do NOT use `_row_to_meta()` for this assertion, as its `row.get()` fallback would mask a backfill failure
  - Integration: `test_migrate_namespace_concurrent_race` — set up a table with old schema (no namespace column), then patch `lancedb.table.AsyncTable.add_columns` at the CLASS level (via `unittest.mock.patch.object(lancedb.table.AsyncTable, "add_columns", new=AsyncMock(side_effect=RuntimeError("Column namespace already exists in the dataset")))`); call `migrate_namespace()`, assert no exception is raised. Class-level patching is required because the `AsyncTable` instance is created INSIDE `migrate_namespace()` via `db.open_table()` and cannot be intercepted by instance-level `patch.object`. Note: at implementation time, verify the actual LanceDB exception type by running `add_columns` on a table that already has the column — update both the migration handler and this test to match the real exception type if it differs from `RuntimeError`.
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_store.py -k "test_migrate_namespace" -v`

#### Task 3.2 — Wire migration into lifespan (`app.py`)
- [ ] **File**: `packages/archon-search/archon_search/server/app.py`
- **Depends on**: Task 3.1
- **Description**:
  - In the `lifespan` async context manager, immediately after `await app.state.search_store.connect()` and before the telemetry block, add: `await app.state.search_store.migrate_namespace()`
  - This ensures no incoming request can observe pre-migration state
  - No other changes
  - Specifically: in `create_app()` → `lifespan()`, insert `await app.state.search_store.migrate_namespace()` on the line immediately after `await app.state.search_store.connect()`. This is the code change; the test below verifies it.
- **Releasable**: server startup includes idempotent migration — existing and fresh deployments are namespace-ready
- **Tests (TDD)** — `packages/archon-search/tests/test_app.py`:
  - Integration: `test_lifespan_calls_migrate_namespace` — using `AsyncMock` patches for `SearchStore.connect`, `SearchStore.disconnect`, and `SearchStore.migrate_namespace`; create the app via `create_app()`; run the lifespan (via `AsyncClient` as context manager or equivalent); assert `migrate_namespace` was called exactly once and `connect` was called before it. Note: `connect()` and `disconnect()` must also be patched to avoid a real LanceDB connection attempt. Note: `create_app()` requires a `SearchConfig` and a `JobStore` instance — follow the same construction pattern as existing `test_app.py` tests (e.g., `create_app(config, job_store=InMemoryJobStore())` or equivalent).
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_app.py -k "test_lifespan_calls_migrate_namespace" -v`

---

### Phase 4 — REST response fields
> **Releasable**: after Task 4.2 — both collection list and detail endpoints return `"namespace": "default"`; 5c can rely on the field being present in all responses.

#### Task 4.1 — `GET /collections/` response includes `namespace`
- [ ] **File**: `packages/archon-search/archon_search/server/routes_collections.py`
- **Depends on**: nothing (independent of store tasks — response dict is built inline)
- **Description**:
  - In `list_collections()` handler, add `"namespace": DEFAULT_NAMESPACE` to the `entry` dict constructed for each collection — import `DEFAULT_NAMESPACE` from `constants.py`
  - Placement: after `"chunk_count"`, before `"status"` — for readability
  - Data source for this endpoint is config paths (independent of `SearchStore.list_collections()`), so the field references `DEFAULT_NAMESPACE`
- **Releasable**: `GET /collections/` responses include `"namespace"` field
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_collections.py`:
  - Unit: `test_routes_list_collections_namespace` — using `fastapi.testclient.TestClient` (same pattern as existing `test_routes_collections.py` tests), with mocked config containing at least one collection path; call `GET /collections/`, parse response JSON, assert each entry includes `"namespace": "default"`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_routes_collections.py -k "test_routes_list_collections_namespace" -v`

#### Task 4.2 — `GET /collections/{name}` response includes `namespace`
- [ ] **File**: `packages/archon-search/archon_search/server/routes_collections.py`
- **Depends on**: nothing (independent)
- **Description**:
  - In `get_collection_info()` handler, add `"namespace": DEFAULT_NAMESPACE` to the `data` dict — import `DEFAULT_NAMESPACE` from `constants.py`
  - Placement: after `"last_indexed"` at the end of the dict
  - Value references `DEFAULT_NAMESPACE` — matches the collection-level contract for 5b
- **Releasable**: `GET /collections/{name}` responses include `"namespace"` field; 5b feature complete
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_collections.py`:
  - Unit: `test_routes_get_collection_namespace` — using `fastapi.testclient.TestClient` (same pattern as existing tests), with mocked config containing a registered collection; call `GET /collections/{name}`, assert response JSON includes `"namespace": "default"`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_routes_collections.py -k "test_routes_get_collection_namespace" -v`

---

### Phase 5 — Full test run
> **Releasable**: after this phase — all tests pass, feature is shippable.

#### Task 5.1 — Full test suite
- [ ] **File**: N/A (verification only)
- **Depends on**: Tasks 0.1, 1.1, 1.2, 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 4.1, 4.2
- **Description**:
  - Run full test suite; resolve any regressions
  - All 21 new test cases must pass
  - No existing tests may be broken
- **Releasable**: FEAT-042 is complete and shippable
- **Tests (TDD)** — N/A:
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov -q`
