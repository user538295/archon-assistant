# FEAT-043 — Search Namespace 5C: Isolation at Storage and Query
**Purpose**: Enforce per-namespace isolation at every HTTP endpoint, store method, and background task so that a key bound to namespace A cannot read, write, search, or cancel resources belonging to namespace B.
**Audience**: Implementers; security reviewers.
**Status**: To Do

---

## Background

5b (FEAT-042) added the `namespace` field to `CollectionMeta`, `CollectionInfo`, and the `_archon_collection_meta` LanceDB table, and seeded all existing rows with `"default"`. However, namespace isolation is not enforced anywhere — middleware resolves no namespace, no route handler checks caller namespace, and several store methods hardcode `DEFAULT_NAMESPACE`. A key with any valid token can read, write, and search all collections.

Source brief: `Documentation/Backlog/search-namespace-5c-isolation-brief.md`

---

## Goal

After 5c, every HTTP request carries a resolved namespace derived server-side from the authenticated Bearer token. A key bound to namespace A cannot list, read, write, delete, search, or cancel resources belonging to namespace B. The existing single-key deployment works with zero config change (unmapped keys fall back to `"default"`). All behavioral changes are confined to the `archon-search` package — no changes required to the parent Archon repo or `SearchClient`.

---

## Scope

### In Scope
- `_validate_namespace()` in `constants.py`
- `SearchConfig.namespaces: dict[str, str]` field + `[namespaces]` TOML section parsing in `config.py`
- `APIKeyMiddleware` multi-key namespace resolution with no-early-exit pattern; sets `request.state.namespace`
- `create_app()` passes `namespaces=config.namespaces` to middleware
- `SearchStore.get_collection_meta(name, namespace)` — namespace filter parameter
- `SearchStore.delete_collection_meta(name, namespace)` — new method
- `SearchStore.update_collection_meta()` — add `_validate_namespace()` call + cross-namespace overwrite protection (raises ValueError if an existing row for the same name has a different namespace)
- `SearchStore.list_collections()` — read namespace from meta row (fixes hardcoded `DEFAULT_NAMESPACE`)
- `SearchPipeline.ingest_directory()`, `SearchPipeline.recompute_collection_meta()`, and `SearchPipeline.get_collection_meta()` in `pipeline.py` — add `namespace: str = DEFAULT_NAMESPACE` parameter; pass to `get_collection_meta()` and `CollectionMeta()` constructor
- `_run_pipeline()` in `routes_jobs.py` — add `namespace: str = DEFAULT_NAMESPACE` parameter; forward to `pipeline_fn(job_id, store, body, namespace=namespace)`
- `IngestJob.namespace: str = DEFAULT_NAMESPACE` field in `types.py`
- `job_to_dict()` in `jobs/model.py` — include `namespace` in serialized output
- `JobStore.create(namespace=DEFAULT_NAMESPACE)` — namespace parameter
- `_default_ingest_task(…, namespace: str)` — explicit namespace parameter, not on `IngestRequest`
- All collection route handlers (`list_collections`, `add_collection`, `remove_collection`, `get_collection_info`, `reindex_collection`) — namespace enforcement
- `POST /search` — use `request.app.state.search_store`; add namespace check before `hybrid_search()`
- `POST /ingest`, `GET /jobs/{job_id}`, `DELETE /jobs/{job_id}` — namespace propagation and checks
- `POST /route` — filter pinned collections to caller's namespace
- `GET /status` — filter collections to caller's namespace (sync→async conversion)
- `GET /indexing-state` — filter collections to caller's namespace (sync→async conversion)
- Unit tests for all changed components; integration test for two-namespace isolation

### Out of Scope
- Telemetry endpoint namespace filtering (`GET /telemetry/entries`, `GET /telemetry/stats`)
- Namespace-prefixed LanceDB table names
- CLI `--namespace` flag
- Namespace provisioning API (`POST /namespaces`)
- Multiple namespaces per key
- `Collection` and `CollectionDetail` types in `types.py` (no `namespace` field added; deferred to 5d)
- Changes to `SearchClient` in the parent Archon repo

---

## Acceptance criteria
- [ ] `GET /collections/` with key A returns only collections whose meta row has `namespace == A`; key B sees only its own
- [ ] `GET /collections/{name}` returns 404 when the named collection belongs to a different namespace
- [ ] `POST /collections/` returns 409 if the collection name is already registered in any namespace (global uniqueness)
- [ ] `POST /collections/` writes a stub `CollectionMeta` row with caller's namespace before background ingest starts; rolls back config if meta write fails
- [ ] `DELETE /collections/{name}` returns 404 for cross-namespace access; deletes both LanceDB table and meta row on success
- [ ] `POST /collections/{name}/reindex` returns 404 for cross-namespace access
- [ ] `POST /search` returns 404 when the collection belongs to a different namespace; uses `request.app.state.search_store` (not a fresh instance)
- [ ] `GET /jobs/{job_id}` returns 404 when the job belongs to a different namespace
- [ ] `DELETE /jobs/{job_id}` returns 404 when the job belongs to a different namespace
- [ ] `POST /route` returns only pinned collections belonging to caller's namespace
- [ ] `GET /status` returns only collections belonging to caller's namespace
- [ ] `GET /indexing-state` returns only collections belonging to caller's namespace
- [ ] Single-key deployment with no `[namespaces]` section: all behavior identical to pre-5c (fallback to `"default"`)
- [ ] `load_config()` with `[namespaces]` section: `config.namespaces` populated correctly
- [ ] `load_config()` without `[namespaces]` section: `config.namespaces == {}`
- [ ] `save_config()` preserves existing `[namespaces]` section (tomlkit round-trip)
- [ ] `JobStore` loads pre-5c JSON files without error (missing `namespace` key uses `DEFAULT_NAMESPACE`)
- [ ] `update_collection_meta()` raises ValueError when a collection with the same name exists in a different namespace
- [ ] `POST /collections/` rollback returns 409 (not 500) when the meta write fails due to a cross-namespace name conflict
- [ ] All existing tests continue to pass

---

## What does NOT change
- `GET /health` — stays public; no namespace check
- `GET /telemetry/entries` and `GET /telemetry/stats` — accessible to any authenticated key (middleware still requires a valid Bearer token; these endpoints are NOT public); no namespace filter applied
- `IngestRequest` Pydantic model — no `namespace` field added; namespace is server-side only
- CLI commands (`archon-search collection …`) — continue to call `SearchStore` directly (admin bypass; documented as explicit security boundary)
- `SearchClient` in `archon/ai/search_client.py` — no changes; single key falls back to `"default"` namespace
- `key_manager.py` — unchanged; `[namespaces]` config is orthogonal to auto-generated key
- `get_all_collections_meta()` — remains unfiltered; route handlers build in-memory `ns_names` sets from this call (no N+1 per-name lookups)
- `save_config()` behavior — only rewrites `[collections]` arrays; `[namespaces]` section preserved by tomlkit automatically

---

## Known limitations / accepted trade-offs
- **Linear middleware scan**: iterating N keys with `secrets.compare_digest` is not perfectly constant-time (loop length is observable). Flag variable (no `break`) mitigates early-exit leakage. Acceptable for operator-scale deployments (<100 keys).
- **Concurrent registration race**: two simultaneous `POST /collections/` for the same path from different namespaces may both pass the name-collision check before either writes the stub meta row. The second upsert wins. Acceptable for the current single-operator model.
- **Stub meta rollback on disk error**: config is reverted in-memory and persisted via `_maybe_save_config()`. If the rollback save also fails, config and meta are inconsistent; operator must manually remove the path. Acceptable — LanceDB disk errors are rare.
- **Telemetry not namespace-filtered**: any authenticated key can read all telemetry entries. Deferred — disproportionate engineering effort for the current single-operator deployment model.
- **`list_collections()` store method — batch meta lookup**: each call does one `get_all_collections_meta()` batch read before the table loop. No per-name DB calls. CLI-only; HTTP route handlers use `get_all_collections_meta()` directly.
- **`[namespaces]` config file permissions**: The `archon-search.toml` `[namespaces]` section stores raw API key hex values as TOML keys. Unlike `~/.archon/.search.env` (which has a documented 600-permissions check in `_check_search_key_file()`), `archon-search.toml` has no permissions check. Operators should ensure the config file is not world-readable (chmod 600). A follow-up diagnostic check (`_check_config_file_permissions()` in `diagnostics.py`) should be added in a subsequent task to warn when `[namespaces]` is non-empty and the config file has permissions wider than 600. Additionally, namespace VALUES in the `[namespaces]` section are not validated at config load time — invalid namespace names (e.g., `"has space"`) are only caught at request time when middleware calls `_validate_namespace()`, returning 500. Config-load-time validation (raising `ConfigError` for malformed namespace names) is deferred.
- **`[namespaces]` precedence over default key**: If the auto-generated `api_key` (from `key_manager.py`) is also added to `[namespaces]` with a non-default namespace, the middleware loop will resolve it to that namespace instead of `DEFAULT_NAMESPACE`. This silently changes the Archon parent process's namespace (since `SearchClient` uses the auto-generated key). Operators should NOT add the auto-generated key to `[namespaces]`; the `[namespaces]` section is intended for additional tenant keys only.
- **Orphan stub meta on ingest failure**: if `POST /collections/` writes the stub `CollectionMeta` row successfully but the background `_default_ingest_task` subsequently fails, the stub meta row remains. The collection appears in `GET /collections/` with `doc_count=0` and `centroid_present=False` indefinitely. Callers can remove it via `DELETE /collections/{name}`. Automatic cleanup (delete meta on FAILED job completion) is deferred.

---

## Architecture

### New function: `_validate_namespace()` in `archon_search/constants.py`
```python
import re
_NAMESPACE_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$')

def _validate_namespace(name: str) -> None:
    """Raise ValueError if name is not a valid namespace identifier."""
    if not _NAMESPACE_RE.fullmatch(name):
        raise ValueError(f"Invalid namespace {name!r}: must match ^[a-zA-Z0-9][a-zA-Z0-9_-]{{0,63}}$")
```

### Config change: `SearchConfig.namespaces` in `archon_search/config.py`
```python
@dataclass
class SearchConfig:
    ...
    namespaces: dict[str, str] = field(default_factory=dict)  # key_hex → namespace_name
```
`load_config()` reads `doc.get("namespaces", {})` and assigns `config.namespaces`. No `save_config()` change needed (tomlkit preserves `[namespaces]` on round-trip).

### Middleware change: `APIKeyMiddleware` in `archon_search/server/middleware_auth.py`
```python
class APIKeyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, api_key: str, namespaces: dict[str, str] | None = None) -> None:
        super().__init__(app)
        self._api_key = api_key
        self._namespaces = namespaces or {}

    async def dispatch(self, request: Request, call_next) -> Response:
        # ... exempt GET /health ...
        token = self._extract_bearer(request)
        if token is None:
            return Response(status_code=401, headers={"WWW-Authenticate": "Bearer"})

        resolved_namespace: str | None = None
        for key_hex, ns in self._namespaces.items():
            if secrets.compare_digest(token, key_hex):
                resolved_namespace = ns  # no break — no early exit
        if resolved_namespace is None and secrets.compare_digest(token, self._api_key):
            resolved_namespace = DEFAULT_NAMESPACE
        if resolved_namespace is None:
            return Response(status_code=401, headers={"WWW-Authenticate": "Bearer"})

        try:
            _validate_namespace(resolved_namespace)
        except ValueError:
            logger.error("Middleware: resolved namespace %r is invalid", resolved_namespace)
            return Response(status_code=500)

        request.state.namespace = resolved_namespace
        return await call_next(request)
```

### App factory change: `create_app()` in `archon_search/server/app.py`
```python
app.add_middleware(APIKeyMiddleware, api_key=key, namespaces=config.namespaces)
```

### Store changes: `archon_search/store.py`
- `get_collection_meta(self, name: str, namespace: str = DEFAULT_NAMESPACE) -> CollectionMeta | None` — filter rows by both `name` and resolved namespace
- `delete_collection_meta(self, name: str, namespace: str) -> None` — delete where `name = '{name}' AND namespace = '{namespace}'`; validates both fields; no-op if `_META_TABLE` absent
- `update_collection_meta(self, meta: CollectionMeta) -> None` — add `_validate_namespace(meta.namespace)` call at top; add cross-namespace overwrite protection (raises `ValueError` if an existing row for `meta.name` has a different `namespace`); upsert key remains `name` alone (Decision 6: globally unique names)
- `list_collections(self) -> list[CollectionInfo]` — replace hardcoded `namespace=DEFAULT_NAMESPACE` with actual meta row namespace (fallback `DEFAULT_NAMESPACE` for orphan tables)

### Job model changes
- `IngestJob` in `archon_search/types.py`: add `namespace: str = DEFAULT_NAMESPACE`
- `job_to_dict()` in `archon_search/jobs/model.py`: add `"namespace": job.namespace`
- `JobStore.create(self, namespace: str = DEFAULT_NAMESPACE) -> IngestJob`: pass namespace to `IngestJob(...)`
- `_default_ingest_task(job_id, store, body, namespace: str, pipeline_fn=None)`: takes namespace explicitly; passes to `update_collection_meta()` when updating the meta row

### Route handler namespace enforcement pattern
All handlers that touch collection data follow this pattern:
1. Read `ns = request.state.namespace` (set by middleware)
2. Call `get_collection_meta(name, namespace=ns)` — returns `None` for cross-namespace or missing
3. Return 404 if `None` (never 403)

For list endpoints: call `get_all_collections_meta()` once, build `ns_names = {m.name for m in all_meta if m.namespace == ns}`, filter results by membership in `ns_names`.

---

## Tests

All tests are in `packages/archon-search/tests/` unless noted.

- **test_validate_namespace_valid** (unit): valid names accepted
- **test_validate_namespace_invalid** (unit): names starting with `_`, too long, empty → `ValueError`
- **test_config_namespaces_populated** (unit): `[namespaces]` section in TOML → `config.namespaces` dict
- **test_config_namespaces_absent** (unit): no `[namespaces]` section → `config.namespaces == {}`
- **test_config_save_preserves_namespaces** (unit): `save_config()` round-trip keeps `[namespaces]` entries
- **test_middleware_single_key_fallback** (unit): `namespaces={}`, valid token → `DEFAULT_NAMESPACE`
- **test_middleware_named_key_resolves_namespace** (unit): key in `namespaces` dict → correct namespace set on `request.state`
- **test_middleware_unknown_key_401** (unit): token not in dict and not matching `api_key` → 401
- **test_middleware_no_early_exit** (unit): all keys evaluated even after first match
- **test_middleware_invalid_namespace_500** (unit): operator misconfigured namespace name → 500
- **test_middleware_namespace_set_on_state** (unit): `request.state.namespace` correctly populated
- **test_get_collection_meta_namespace_filter** (unit): returns `None` when meta has different namespace
- **test_delete_collection_meta** (unit): deletes row matching name AND namespace; row in other namespace unaffected
- **test_update_collection_meta_validates_namespace** (unit): invalid namespace raises `ValueError`
- **test_update_collection_meta_cross_namespace_overwrite_raises** (unit): existing row for name in different namespace → `ValueError` before any write
- **test_update_collection_meta_first_insert** (unit): no existing row for name → first registration proceeds normally; row created with correct namespace
- **test_list_collections_reads_namespace_from_meta** (unit): actual namespace from meta row, not hardcoded `DEFAULT_NAMESPACE`; uses batch `get_all_collections_meta()` (not per-name lookup)
- **test_ingest_job_namespace_default** (unit): `IngestJob(**pre_5c_dict)` deserializes to `namespace=DEFAULT_NAMESPACE`
- **test_job_to_dict_includes_namespace** (unit): `job_to_dict()` output has `"namespace"` key
- **test_job_store_create_namespace** (unit): `store.create(namespace="tenantA")` → job has correct namespace
- **test_default_ingest_task_namespace_param** (unit): namespace passed explicitly, not read from `IngestRequest`
- **test_default_ingest_task_takes_namespace_param** (unit): call `_default_ingest_task(…, namespace="tenantA")` — completes without error
- **test_run_pipeline_forwards_namespace** (unit): `_run_pipeline(job_id, store, body, namespace="tenantA", pipeline_fn=mock_fn)` → `mock_fn` called with `namespace="tenantA"`
- **test_ingest_directory_namespace_param** (unit): `SearchPipeline.ingest_directory(job_id, store, body, namespace="tenantA")` → `store.get_collection_meta()` called with `namespace="tenantA"`; `CollectionMeta` constructed with `namespace="tenantA"`
- **test_ingest_directory_default_namespace** (unit): `SearchPipeline.ingest_directory(job_id, store, body)` → `namespace` defaults to `DEFAULT_NAMESPACE`; existing behavior preserved
- **test_default_ingest_task_forwards_namespace_to_run_pipeline** (unit): `_default_ingest_task(job_id, store, body, namespace="tenantA")` → `_run_pipeline` called with `namespace="tenantA"`
- **test_list_collections_filters_by_namespace** (unit): `GET /collections/` returns only caller's collections
- **test_add_collection_global_uniqueness** (unit): name already registered in different namespace → 409
- **test_add_collection_stub_meta_write** (unit): meta row written before task completes
- **test_add_collection_rollback_on_meta_failure** (unit): config appended then reverted if meta write raises non-ValueError exception → 500
- **test_add_collection_cross_namespace_race_returns_409** (unit): `update_collection_meta` raises `ValueError` (TOCTOU race) → 409, config reverted
- **test_remove_collection_cross_namespace_404** (unit): wrong namespace → 404, not deleted
- **test_remove_collection_deletes_meta_row** (unit): `delete_collection_meta()` called on success
- **test_remove_collection_success_drops_table_and_meta** (unit): both `drop_collection()` and `delete_collection_meta()` called on success; meta row absent after completion
- **test_get_collection_info_cross_namespace_404** (unit): wrong namespace → 404
- **test_get_collection_info_centroid_from_namespace_meta** (unit): correct namespace with meta having centroid data → response `centroid_present` is `True` (centroid from namespace-filtered meta, not bare lookup)
- **test_reindex_cross_namespace_404** (unit): wrong namespace → 404
- **test_search_uses_app_state_store** (unit): no fresh `SearchStore` construction per request
- **test_search_same_namespace_proceeds** (unit): `get_collection_meta` returns matching namespace meta → `hybrid_search()` is called
- **test_search_cross_namespace_404** (unit): collection in different namespace → 404
- **test_search_store_exception_returns_503** (unit): LanceDB error on meta lookup → 503 (not 404)
- **test_get_job_cross_namespace_404** (unit): job belongs to different namespace → 404
- **test_delete_job_cross_namespace_404** (unit): same
- **test_ingest_passes_namespace_to_task** (unit): `request.state.namespace` forwarded to `_default_ingest_task()`
- **test_ingest_request_ignores_body_namespace** (unit): body `"namespace": "attacker"` → job namespace is server-resolved value, not `"attacker"`; no 422 error
- **test_health_endpoint_unauthenticated_200** (unit): `GET /health` with no Authorization header → 200 even when `[namespaces]` is non-empty
- **test_middleware_api_key_also_in_namespaces** (unit): `api_key` also in `namespaces` → resolves to mapped namespace (not DEFAULT_NAMESPACE); documents precedence behavior
- **test_add_collection_rollback_save_failure** (unit): `update_collection_meta` raises AND rollback `_maybe_save_config` also raises → 500; config and meta may be inconsistent
- **test_two_namespace_collection_detail_isolation** (integration): key A `GET /collections/{name}` for key B's collection → 404
- **test_two_namespace_delete_isolation** (integration): key A `DELETE /collections/{name}` for key B's collection → 404; key B's collection still exists
- **test_route_filters_pinned_by_namespace** (unit): pinned collections in wrong namespace excluded
- **test_status_filters_by_namespace** (unit): `GET /status` returns only caller's namespace collections
- **test_indexing_state_filters_by_namespace** (unit): `GET /indexing-state` returns only caller's namespace entries
- **test_two_namespace_isolation** (integration): key A and key B each see only their own collection in `GET /collections/`, `POST /search`, `GET /status`
- **test_single_key_backward_compat** (integration): no `[namespaces]` config → all existing behavior unchanged

---

## Documentation update
- [ ] `Documentation/Backlog/FEAT-037-search-world-class-roadmap.md` — mark 5c row as In Progress (plan link added)

---

## Task breakdown

### Phase 1 — Foundation: validation, config, store primitives
> **Releasable**: after this phase, store methods correctly enforce namespace; middleware and route handlers land in Phase 2–4.

#### Task 1.1 — `_validate_namespace()` in `constants.py`
- [x] **File**: `packages/archon-search/archon_search/constants.py`
- **Depends on**: nothing
- **Description**:
  - Add `import re` at top.
  - Add `_NAMESPACE_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$')`.
  - Add `def _validate_namespace(name: str) -> None`: calls `_NAMESPACE_RE.fullmatch(name)`; raises `ValueError(f"Invalid namespace {name!r}: must match …")` if no match.
  - Export in `__all__` if the module defines one (check first — it currently does not, so no change needed).
- **Releasable**: after this task, callers can import and call `_validate_namespace`.
- **Tests (TDD)** — `packages/archon-search/tests/test_constants.py` (create if absent):
  - Unit: `test_validate_namespace_valid_names` — `"default"`, `"tenantA"`, `"tenant-1"`, `"a"`, `"Z"` all pass without raising
  - Unit: `test_validate_namespace_invalid_starts_with_underscore` — `"_bad"` → `ValueError`
  - Unit: `test_validate_namespace_invalid_empty` — `""` → `ValueError`
  - Unit: `test_validate_namespace_too_long` — 65-char name → `ValueError`; 64-char name passes
  - Unit: `test_validate_namespace_invalid_special_chars` — `"has space"`, `"has.dot"` → `ValueError`
  - Unit: `test_validate_namespace_exactly_64_chars` — 64-char name starting with letter → passes
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_constants.py -v`

#### Task 1.2 — `SearchConfig.namespaces` + `[namespaces]` parsing in `config.py`
- [x] **File**: `packages/archon-search/archon_search/config.py`
- **Depends on**: nothing (independent of Task 1.1)
- **Description**:
  - Add `namespaces: dict[str, str] = field(default_factory=dict)` to `SearchConfig` after the existing fields (after `telemetry`).
  - In `load_config()` (or equivalent parsing block): after reading the main sections, add: `config.namespaces = dict(doc.get("namespaces", {}))`. Each entry is `"<key_hex>" = "<namespace_name>"`.
  - `save_config()` requires no change — tomlkit preserves the `[namespaces]` section on round-trip since `save_config()` only mutates `[collections]` arrays.
- **Releasable**: after this task, `SearchConfig.namespaces` is populated from TOML on startup.
- **Tests (TDD)** — `packages/archon-search/tests/test_config.py` (extend existing):
  - Unit: `test_config_namespaces_populated` — TOML with `[namespaces]` section `"keyA" = "tenantA"` → `config.namespaces == {"keyA": "tenantA"}`
  - Unit: `test_config_namespaces_absent` — TOML with no `[namespaces]` section → `config.namespaces == {}`
  - Unit: `test_config_namespaces_empty_section` — `[namespaces]` present but empty → `config.namespaces == {}`
  - Unit: `test_save_config_preserves_namespaces` — write TOML with `[namespaces]`, call `save_config()`, re-read file, assert `[namespaces]` entries survive unchanged
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_config.py -v`

#### Task 1.3 — `get_collection_meta()` namespace filter in `store.py`
- [x] **File**: `packages/archon-search/archon_search/store.py`
- **Depends on**: Task 1.1
- **Description**:
  - Change signature: `async def get_collection_meta(self, name: str, namespace: str = DEFAULT_NAMESPACE) -> CollectionMeta | None`.
  - Update the existing filter expression (currently `[r for r in rows if r["name"] == name]`) to: `matching = [r for r in rows if r["name"] == name and (r.get("namespace") or DEFAULT_NAMESPACE) == namespace]`.
  - All existing callers that omit `namespace=` continue to work (default is `DEFAULT_NAMESPACE`).
  - CLI and startup code may pass explicit namespace or omit.
- **Releasable**: after this task, route handlers can enforce namespace by passing `namespace=request.state.namespace`.
- **Tests (TDD)** — `packages/archon-search/tests/test_store.py` (extend existing):
  - Unit: `test_get_collection_meta_correct_namespace` — row with `namespace="tenantA"` → returned when called with `namespace="tenantA"`
  - Unit: `test_get_collection_meta_wrong_namespace` — same row → `None` when called with `namespace="tenantB"`
  - Unit: `test_get_collection_meta_default_namespace` — row with `namespace="default"` → returned when called with no namespace arg (uses default)
  - Unit: `test_get_collection_meta_missing_namespace_field_fallback` — row without `namespace` column (legacy row) → treated as `DEFAULT_NAMESPACE`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_store.py -k "get_collection_meta" -v`

#### Task 1.4 — `delete_collection_meta()` in `store.py`
- [x] **File**: `packages/archon-search/archon_search/store.py`
- **Depends on**: Task 1.1
- **Description**:
  - Add new method: `async def delete_collection_meta(self, name: str, namespace: str) -> None`.
  - Call `_validate_collection(name)` and `_validate_namespace(namespace)` at the top (raises `ValueError` on invalid input).
  - Open `_META_TABLE` if it exists; if absent, return immediately (no-op).
  - Execute: `await table.delete(f"name = '{name}' AND namespace = '{namespace}'")`
  - Import `_validate_namespace` from `archon_search.constants` (it's already in scope via the existing `DEFAULT_NAMESPACE` import).
- **Releasable**: after this task, `remove_collection` handler can clean up meta rows precisely by namespace.
- **Tests (TDD)** — `packages/archon-search/tests/test_store.py` (extend existing):
  - Unit: `test_delete_collection_meta_removes_correct_row` — two rows (same name, different namespace); delete with `namespace="tenantA"` → only `tenantA` row removed; `tenantB` row intact
  - Unit: `test_delete_collection_meta_noop_when_table_absent` — no `_META_TABLE` → no exception
  - Unit: `test_delete_collection_meta_noop_when_row_missing` — table exists but no matching row → no exception
  - Unit: `test_delete_collection_meta_validates_namespace` — invalid namespace string → `ValueError` before any DB access
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_store.py -k "delete_collection_meta" -v`

#### Task 1.5 — `update_collection_meta()` namespace validation + cross-namespace overwrite protection in `store.py`
- [x] **File**: `packages/archon-search/archon_search/store.py`
- **Depends on**: Task 1.1
- **Description**:
  - At the top of `update_collection_meta()`, add `_validate_namespace(meta.namespace)`. This raises `ValueError` on misconfigured namespace before any SQL is executed.
  - Before executing the upsert, check for cross-namespace collision: if an existing row for `meta.name` exists with a DIFFERENT `namespace` than `meta.namespace`, log `ERROR("update_collection_meta: name %r is registered under namespace %r, refusing to overwrite with namespace %r", meta.name, existing_namespace, meta.namespace)` and raise `ValueError(f"Collection {meta.name!r} belongs to namespace {existing_namespace!r}; cannot reassign to {meta.namespace!r}")`. This makes the global uniqueness invariant self-enforcing at the store layer. The CLI admin bypass is documented as an explicit security boundary — it does not bypass this check.
  - If no existing row exists for `meta.name`, the cross-namespace check is skipped (first registration proceeds normally). The check is only enforced when an existing row is found.
  - The upsert key remains `name` alone (Decision 6: collection names are globally unique across namespaces). The existing `await table.delete(f"name = '{meta.name}'")` line executes only after the cross-namespace check passes.
  - `meta.name` and `meta.namespace` are both validated (via `_validate_collection` and `_validate_namespace` respectively) before interpolation — safe against injection.
- **Releasable**: after this task, `update_collection_meta()` rejects invalid namespace values and refuses to overwrite another namespace's meta row.
- **Tests (TDD)** — `packages/archon-search/tests/test_store.py` (extend existing):
  - Unit: `test_update_collection_meta_invalid_namespace_raises` — `CollectionMeta(name="foo", namespace="has space")` → `ValueError` raised before any DB write
  - Unit: `test_update_collection_meta_valid_namespace_passes` — `CollectionMeta(name="foo", namespace="tenantA")` → completes without error
  - Unit: `test_update_collection_meta_same_namespace_upsert` — insert `(foo, tenantA)`, update `(foo, tenantA)` with new data → only one row exists, updated correctly
  - Unit: `test_update_collection_meta_cross_namespace_overwrite_raises` — existing row `(foo, tenantA)`, call `update_collection_meta(CollectionMeta(name="foo", namespace="tenantB"))` → `ValueError` raised; original row unchanged
  - Unit: `test_update_collection_meta_first_insert` — call `update_collection_meta(CollectionMeta(name="new-name", namespace="tenantA"))` when no row for `"new-name"` exists → completes without error; row is created with `namespace="tenantA"`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_store.py -k "update_collection_meta" -v`

#### Task 1.6 — `list_collections()` reads namespace from meta in `store.py`
- [x] **File**: `packages/archon-search/archon_search/store.py`
- **Depends on**: Task 1.3
- **Description**:
  - In `list_collections()`, remove the hardcoded `namespace=DEFAULT_NAMESPACE` from the `CollectionInfo(...)` constructor (currently line ~225).
  - Before the table loop, call `all_meta = await self.get_all_collections_meta()` once (a single batch read — not per-name calls). Build `meta_by_name = {m.name: m for m in all_meta}`.
  - In the loop, do `meta = meta_by_name.get(name)` — no per-name DB calls.
  - If `meta is None` (orphan table with no meta row): use `DEFAULT_NAMESPACE` as fallback.
  - Pass `namespace=meta.namespace if meta else DEFAULT_NAMESPACE` to `CollectionInfo(...)`.
  - Note: this method is called only by CLI commands. HTTP route handlers use `get_all_collections_meta()` directly and must NOT call `get_collection_meta()` per name inside a loop.
  - **IMPORTANT**: do NOT use `get_collection_meta(name)` without a namespace arg inside this method. After Task 1.3, calling `get_collection_meta(name)` without a namespace arg defaults to `namespace=DEFAULT_NAMESPACE` and returns `None` for any non-default-namespace collection — all non-default collections would be misattributed as `DEFAULT_NAMESPACE`.
- **Releasable**: after this task, CLI `archon-search collection list` shows correct namespace per collection.
- **Tests (TDD)** — `packages/archon-search/tests/test_store.py` (extend existing); tests must use a store instance that has `get_all_collections_meta()` available (not per-name lookup):
  - Unit: `test_list_collections_namespace_from_meta` — collection with `namespace="tenantA"` in meta → `CollectionInfo.namespace == "tenantA"` (not `"default"`); verify `get_all_collections_meta()` is called (not a per-name `get_collection_meta()`)
  - Unit: `test_list_collections_orphan_table_defaults` — table with no meta row → `CollectionInfo.namespace == DEFAULT_NAMESPACE`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_store.py -k "list_collections" -v`

---

### Phase 2 — Middleware namespace resolution + app wire-up
> **Releasable**: after Task 2.2 — every HTTP request has `request.state.namespace` set; route handlers can begin enforcing it.

#### Task 2.1 — `APIKeyMiddleware` multi-key namespace resolution
- [x] **File**: `packages/archon-search/archon_search/server/middleware_auth.py`
- **Depends on**: Task 1.1
- **Description**:
  - Add `namespaces: dict[str, str] | None = None` parameter to `__init__`. Store as `self._namespaces = namespaces or {}`. (Do NOT use a mutable default argument `= {}` — it is shared across all instances.)
  - In `dispatch()`, after extracting the Bearer token:
    1. `resolved_namespace: str | None = None`
    2. Iterate `for key_hex, ns in self._namespaces.items()`: `if secrets.compare_digest(token, key_hex): resolved_namespace = ns` — no `break`, iterate all keys (prevents timing leakage via early exit)
    3. If `resolved_namespace is None`: `if secrets.compare_digest(token, self._api_key): resolved_namespace = DEFAULT_NAMESPACE`
    4. If `resolved_namespace is None`: return `Response(status_code=401, headers={"WWW-Authenticate": "Bearer"})`
    5. Try `_validate_namespace(resolved_namespace)`: on `ValueError`, log `ERROR` and return `Response(status_code=500)`
    6. Set `request.state.namespace = resolved_namespace`
  - Import `DEFAULT_NAMESPACE` and `_validate_namespace` from `archon_search.constants`.
  - Existing behavior (single key, `namespaces={}`) is fully preserved: step 3 falls back to comparing against `self._api_key`, resolving to `DEFAULT_NAMESPACE`.
- **Releasable**: after this task, `request.state.namespace` is correctly set on every authenticated request.
- **Tests (TDD)** — `packages/archon-search/tests/server/test_middleware_auth.py` (extend existing):
  - Unit: `test_middleware_single_key_no_namespaces_fallback` — `namespaces={}`, valid token matches `api_key` → `request.state.namespace == DEFAULT_NAMESPACE`
  - Unit: `test_middleware_named_key_resolves_namespace` — key in `namespaces={"keyA": "tenantA"}` → `request.state.namespace == "tenantA"`
  - Unit: `test_middleware_second_key_resolves_different_namespace` — two keys in `namespaces`; key B resolves to `"tenantB"` (not `"tenantA"`)
  - Unit: `test_middleware_unknown_key_401` — token not in `namespaces` and not matching `api_key` → 401; no `namespace` set
  - Unit: `test_middleware_invalid_namespace_500` — key maps to `"has space"` (invalid) → 500
  - Unit: `test_middleware_no_early_exit` — mock `secrets.compare_digest` to track call count; ensure it is called for all entries even after a match
  - Unit: `test_middleware_namespace_on_request_state` — after successful dispatch, `request.state.namespace` is accessible in handler
  - Unit: `test_middleware_multiple_keys_same_namespace` — `{"keyA": "tenantA", "keyB": "tenantA"}` (key rotation); both resolve to `"tenantA"`
  - Unit: `test_middleware_api_key_also_in_namespaces` — configure `api_key="defaultKey"` and `namespaces={"defaultKey": "tenantA"}`; send request with `"defaultKey"` → resolved namespace is `"tenantA"` (not `DEFAULT_NAMESPACE`); document this precedence: the `[namespaces]` loop runs before the default-key fallback, so if the auto-generated key is mistakenly added to `[namespaces]`, it resolves to the mapped namespace rather than `"default"`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/server/test_middleware_auth.py -v`

#### Task 2.2 — `create_app()` passes `namespaces` to middleware
- [x] **File**: `packages/archon-search/archon_search/server/app.py`
- **Depends on**: Task 2.1, Task 1.2
- **Description**:
  - Change the `app.add_middleware(APIKeyMiddleware, api_key=key)` call to `app.add_middleware(APIKeyMiddleware, api_key=key, namespaces=config.namespaces)`.
  - `config.namespaces` is the dict loaded from `[namespaces]` TOML section. If section absent: `{}` (default) — no behavior change.
- **Releasable**: after this task, the full namespace-resolution pipeline is live end-to-end.
- **Tests (TDD)** — `packages/archon-search/tests/test_app.py` (extend existing):
  - Unit: `test_create_app_passes_namespaces_to_middleware` — `create_app(config_with_namespaces, …)` → `APIKeyMiddleware` receives non-empty `namespaces` dict
  - Unit: `test_create_app_empty_namespaces_no_error` — `config.namespaces == {}` → middleware created without error; existing key still works
  - Unit: `test_health_endpoint_unauthenticated_200` — send `GET /health` with NO Authorization header to the fully-wired app (with non-empty `namespaces` config); assert 200 response (confirms `/health` stays public after all middleware changes)
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_app.py -v`

---

### Phase 3 — Job types and task namespace propagation
> **Releasable**: after Task 3.5 — ingest tasks carry caller namespace through the full pipeline lifecycle. (Tasks 3.1–3.4 are intermediate steps; Task 3.5 completes the namespace threading through `_run_pipeline` and `SearchPipeline`.)

#### Task 3.1 — `IngestJob.namespace` field in `types.py`
- [x] **File**: `packages/archon-search/archon_search/types.py`
- **Depends on**: nothing (independent)
- **Description**:
  - Add `namespace: str = DEFAULT_NAMESPACE` to `IngestJob` dataclass.
  - Import `DEFAULT_NAMESPACE` from `archon_search.constants`.
  - `IngestJob(**item)` in `JobStore._load()` uses `IngestJob(**item)` — since `namespace` has a default, pre-5c JSON files (missing `"namespace"` key) deserialize correctly without code change. Post-5c JSON read by pre-5c code will fail with `TypeError: unexpected keyword argument` — this is a documented one-way migration.
  - `ReindexJob(IngestJob)` and `DeleteJob(IngestJob)` inherit the field; no change needed.
- **Releasable**: after this task, `IngestJob` carries a namespace; downstream callers can set it.
- **Tests (TDD)** — `packages/archon-search/tests/test_types.py` (create or extend):
  - Unit: `test_ingest_job_namespace_default` — `IngestJob(job_id="x", status=…, created_at=…, updated_at=…)` → `job.namespace == "default"`
  - Unit: `test_ingest_job_namespace_explicit` — `IngestJob(…, namespace="tenantA")` → `job.namespace == "tenantA"`
  - Unit: `test_ingest_job_splat_pre_5c_dict` — dict without `"namespace"` key → `IngestJob(**item).namespace == DEFAULT_NAMESPACE`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_types.py -v`

#### Task 3.2 — `job_to_dict()` includes `namespace` in `jobs/model.py`
- [x] **File**: `packages/archon-search/archon_search/jobs/model.py`
- **Depends on**: Task 3.1
- **Description**:
  - Add `"namespace": job.namespace` to the dict returned by `job_to_dict()`.
- **Releasable**: after this task, `GET /jobs/{job_id}` response includes `"namespace"`.
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_jobs.py` (extend existing):
  - Unit: `test_job_to_dict_includes_namespace` — `job_to_dict(IngestJob(…, namespace="tenantA"))` → result dict has `"namespace": "tenantA"`
  - Unit: `test_job_to_dict_default_namespace` — `IngestJob` with no explicit namespace → `"namespace": "default"` in dict
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_routes_jobs.py -k "job_to_dict" -v`

#### Task 3.3 — `JobStore.create(namespace)` parameter
- [x] **File**: `packages/archon-search/archon_search/jobs/store.py`
- **Depends on**: Task 3.1
- **Description**:
  - Change `def create(self) -> IngestJob` to `def create(self, namespace: str = DEFAULT_NAMESPACE) -> IngestJob`.
  - Import `DEFAULT_NAMESPACE` from `archon_search.constants`.
  - Pass `namespace=namespace` to `IngestJob(...)` in the body.
- **Releasable**: after this task, route handlers can create namespace-tagged jobs.
- **Tests (TDD)** — `packages/archon-search/tests/test_job_store.py` (extend existing):
  - Unit: `test_job_store_create_with_namespace` — `store.create(namespace="tenantA")` → returned job has `namespace == "tenantA"`
  - Unit: `test_job_store_create_default_namespace` — `store.create()` → `job.namespace == DEFAULT_NAMESPACE`
  - Unit: `test_job_store_persists_namespace` — create job with namespace, reload store from disk → namespace survives round-trip
  - Unit: `test_job_store_load_pre_5c_json` — JSON file with job entries lacking `"namespace"` key → loaded jobs have `namespace == DEFAULT_NAMESPACE`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_job_store.py -v`

#### Task 3.4 — `_default_ingest_task()` namespace parameter in `routes_jobs.py`
- [x] **File**: `packages/archon-search/archon_search/server/routes_jobs.py`
- **Depends on**: Task 3.1, Task 3.3
- **Description**:
  - Change signature: `_default_ingest_task(job_id, store, body, namespace: str = DEFAULT_NAMESPACE, pipeline_fn=None)`. Namespace is inserted as the 4th positional parameter — before `pipeline_fn`. Giving `namespace` a default of `DEFAULT_NAMESPACE` makes the change safe for incremental deployment: existing 3-arg callers continue to work until they are explicitly wired in Tasks 4.2, 4.5, and 5.3.
  - Namespace is passed explicitly by the route handler — never derived from `IngestRequest` (which has no `namespace` field and must not receive one).
  - Inside `_default_ingest_task`, when calling `update_collection_meta()` directly OR passing `CollectionMeta` to `pipeline_fn`, ensure `meta.namespace = namespace` is set explicitly. The `namespace` parameter received by `_default_ingest_task` must flow through to the `CollectionMeta` object used in all `update_collection_meta()` calls within this task and its pipeline. Add an assertion or comment: `assert meta.namespace == namespace, 'CollectionMeta namespace must match task namespace to avoid cross-namespace overwrite error'`.
  - **Scope boundary**: Task 3.4 changes the `_default_ingest_task` function signature and body, and safely converts any callsite that passes `pipeline_fn` as a positional 4th arg (specifically `routes_jobs.py:94`) to keyword-arg form to prevent a CI-breaking window. All namespace wiring (adding `namespace=ns` to callsites) is done in Tasks 4.2, 4.5, and 5.3.
  - **Complete callsite audit** — all callers of `_default_ingest_task` and their status after Task 3.4:
    - `routes_jobs.py:94`: was `_default_ingest_task(job.job_id, store, body, pipeline_fn)` — convert to `_default_ingest_task(job.job_id, store, body, pipeline_fn=pipeline_fn)` to avoid positional shift (`namespace` will default to `DEFAULT_NAMESPACE` until Task 5.3 wires it)
    - `routes_collections.py:120` (add_collection handler): was `_default_ingest_task(job.job_id, store, ingest_body)` — 3 positional args, no `pipeline_fn`; safe with default (`namespace` defaults)
    - `routes_collections.py:~251` (reindex_collection handler): similar 3-arg call — safe with default (`namespace` defaults)
  - The full `namespace=ns` wiring for all callsites is completed in Tasks 4.2 (add_collection), 4.5 (reindex_collection), and 5.3 (ingest route).
- **Releasable**: after this task, `_default_ingest_task()` signature accepts namespace; callers in Tasks 4.2 and 4.5 can use it.
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_jobs.py` (extend existing):
  - Unit: `test_default_ingest_task_takes_namespace_param` — call `_default_ingest_task(…, namespace="tenantA")` — completes without error
  - Unit: `test_ingest_request_ignores_body_namespace` — POST `/ingest` with JSON body containing `"namespace": "attacker-namespace"`; assert the created job has `namespace == request.state.namespace` (the server-side resolved value), not `"attacker-namespace"`; assert no 422 validation error from Pydantic (the field is unknown and ignored, not rejected)
  - Note: namespace propagation from `_default_ingest_task` through `_run_pipeline` to `SearchPipeline.ingest_directory()` is tested in Task 3.5
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_routes_jobs.py -v`

#### Task 3.5 — `_run_pipeline()` + `SearchPipeline.ingest_directory()` + `SearchPipeline.recompute_collection_meta()` + `SearchPipeline.get_collection_meta()` namespace propagation

- [x] **File**: `packages/archon-search/archon_search/server/routes_jobs.py`, `packages/archon-search/archon_search/pipeline.py`
- **Depends on**: Task 1.3, Task 1.5, Task 3.4
- **Description**:
  - In `_run_pipeline(job_id, store, body, pipeline_fn=None)`: add `namespace: str = DEFAULT_NAMESPACE` parameter. Forward it to `pipeline_fn` as a keyword arg: call `pipeline_fn(job_id, store, body, namespace=namespace)`.
  - In `SearchPipeline.ingest_directory(job_id, store, body)`: add `namespace: str = DEFAULT_NAMESPACE` parameter.
    - At the `get_collection_meta(collection)` call (~line 185): change to `get_collection_meta(collection, namespace=namespace)`. This ensures existing description/centroid state is preserved for non-default-namespace collections (not silently lost as None).
    - At the `CollectionMeta(name=collection, centroid=centroid, ...)` constructor (~line 197): add `namespace=namespace`.
    - Existing callers that pass no `namespace` arg continue to work (default is `DEFAULT_NAMESPACE`).
  - In `SearchPipeline.recompute_collection_meta(self, collection)`: add `namespace: str = DEFAULT_NAMESPACE` parameter.
    - Change the `self.store.get_collection_meta(collection)` call to `self.store.get_collection_meta(collection, namespace=namespace)`. Without this fix, after Task 1.3 the call returns `None` for any non-default-namespace collection, silently discarding existing metadata.
    - Add `namespace=namespace` to the `CollectionMeta(name=collection, ...)` constructor. Without this, the constructed meta defaults to `DEFAULT_NAMESPACE` and the subsequent `self.store.update_collection_meta(meta)` call raises `ValueError` (cross-namespace overwrite protection added in Task 1.5) for any non-default-namespace collection.
    - Existing callers that pass no `namespace` arg continue to work (default is `DEFAULT_NAMESPACE`).
  - In `SearchPipeline.get_collection_meta(self, name)` (the pass-through method at ~line 269–270): add `namespace: str = DEFAULT_NAMESPACE` parameter. Forward it: `return self.store.get_collection_meta(name, namespace=namespace)`. Without this, the pass-through always calls the store with `DEFAULT_NAMESPACE` and returns `None` silently for non-default-namespace collections after Task 1.3.
  - In `_default_ingest_task()`: when calling `_run_pipeline()`, forward `namespace`: `await _run_pipeline(job_id, store, body, namespace=namespace, pipeline_fn=pipeline_fn)`.
  - **Why this is necessary**: Task 1.5 adds cross-namespace overwrite protection to `update_collection_meta()`. Without this fix, both `SearchPipeline.ingest_directory()` and `SearchPipeline.recompute_collection_meta()` construct `CollectionMeta(namespace=DEFAULT_NAMESPACE)` and call `update_collection_meta(meta)`, which raises `ValueError` for any non-default-namespace collection — crashing ingest and recompute tasks for non-default tenants.
- **Releasable**: after this task, background ingest and collection meta recomputation correctly tag collection meta with the caller's namespace end-to-end.
- **Tests (TDD)** — extend relevant test files:
  - Unit: `test_run_pipeline_forwards_namespace` — `_run_pipeline(job_id, store, body, namespace="tenantA", pipeline_fn=mock_fn)` → `mock_fn` called with `namespace="tenantA"`
  - Unit: `test_ingest_directory_namespace_param` — `SearchPipeline.ingest_directory(job_id, store, body, namespace="tenantA")` → `store.get_collection_meta()` called with `namespace="tenantA"`; `CollectionMeta` constructed with `namespace="tenantA"`
  - Unit: `test_ingest_directory_default_namespace` — `SearchPipeline.ingest_directory(job_id, store, body)` → `namespace` defaults to `DEFAULT_NAMESPACE`; existing behavior preserved
  - Unit: `test_default_ingest_task_forwards_namespace_to_run_pipeline` — `_default_ingest_task(job_id, store, body, namespace="tenantA")` → `_run_pipeline` called with `namespace="tenantA"`
  - Unit: `test_recompute_collection_meta_namespace_param` — `SearchPipeline.recompute_collection_meta(job_id, store, collection, namespace="tenantA")` → `store.get_collection_meta()` called with `namespace="tenantA"`; `CollectionMeta` constructed with `namespace="tenantA"`; `store.update_collection_meta()` called with the correctly-namespaced meta (no `ValueError`)
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/ -k "run_pipeline or ingest_directory or recompute_collection_meta" -v`

---

### Phase 4 — Route enforcement: collection endpoints
> **Releasable**: after this phase, all `/collections/*` endpoints enforce namespace isolation.

#### Task 4.1 — `list_collections` namespace filter (`GET /collections/`)
- [x] **File**: `packages/archon-search/archon_search/server/routes_collections.py`
- **Depends on**: Task 2.2, Task 1.3
- **Description**:
  - Convert handler from `def list_collections` to `async def list_collections`.
  - Read `ns = request.state.namespace`.
  - Get `search_store = request.app.state.search_store`.
  - Call `all_meta = await search_store.get_all_collections_meta()` once. Build `ns_names = {m.name for m in all_meta if m.namespace == ns}`.
  - After building `path_to_name` from config, filter the loop: only include collections where `name in ns_names`.
  - For included collections, find the matching meta entry: `meta_by_name = {m.name: m for m in all_meta}`. Use `meta_by_name.get(name).namespace` in the response dict. For collections with no meta row (not in `all_meta`): include only if `ns == DEFAULT_NAMESPACE`; use `DEFAULT_NAMESPACE` as namespace value.
  - Do NOT issue per-name `get_collection_meta()` calls inside the loop.
- **Releasable**: after this task, `GET /collections/` returns only the caller's collections.
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_collections.py` (extend existing):
  - Unit: `test_list_collections_filters_by_namespace` — two collections in meta (one `tenantA`, one `tenantB`); request with `state.namespace="tenantA"` → only `tenantA` collection in response
  - Unit: `test_list_collections_no_meta_default_ns` — collection path in config but no meta row; request with `DEFAULT_NAMESPACE` → included; request with `"tenantA"` → excluded
  - Unit: `test_list_collections_single_key_backward_compat` — `state.namespace=DEFAULT_NAMESPACE`, all existing collections are `DEFAULT_NAMESPACE` → all returned (same as pre-5c)
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_routes_collections.py -k "list_collections" -v`

#### Task 4.2 — `add_collection` global uniqueness + stub meta + rollback (`POST /collections/`)
- [x] **File**: `packages/archon-search/archon_search/server/routes_collections.py`
- **Depends on**: Task 2.2, Task 1.3, Task 1.5, Task 3.3, Task 3.4
- **Description**:
  - Read `ns = request.state.namespace`.
  - After the existing path dedup check, add global-name uniqueness check: call `all_meta = await search_store.get_all_collections_meta()`. If any row has `name == path_to_collection_name(resolved)`, return 409 with `{"detail": "collection name already registered"}`.
  - Append path to `config.collections` and call `_maybe_save_config()` as before.
  - Immediately write stub meta: `await search_store.update_collection_meta(CollectionMeta(name=collection_name, namespace=ns))`. If this raises, distinguish exception type: if `ValueError` (cross-namespace name conflict — TOCTOU race), rollback config AND return 409; for any other exception (LanceDB error, disk error), rollback config AND return 500. To distinguish, catch `ValueError` separately from other exceptions.
  - Pass `namespace=ns` to `store.create()` and pass `namespace=ns` as explicit arg to `_default_ingest_task()`.
  - Import `CollectionMeta` from `archon_search.collection_meta`.
- **Releasable**: after this task, `POST /collections/` enforces global name uniqueness and tags new collections with caller's namespace.
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_collections.py` (extend existing):
  - Unit: `test_add_collection_global_uniqueness_409` — name already in meta (any namespace) → 409
  - Unit: `test_add_collection_writes_stub_meta` — successful add → `get_collection_meta(name, namespace=ns)` returns a row before ingest completes
  - Unit: `test_add_collection_rollback_on_meta_failure` — `update_collection_meta` raises a non-ValueError exception (e.g., LanceDB error) → config path reverted; collection absent from config after handler returns; response is 500
  - Unit: `test_add_collection_cross_namespace_race_returns_409` — `update_collection_meta` raises `ValueError` (simulating TOCTOU race: another request registered the same name after the HTTP-layer uniqueness check) → response is 409 (not 500); config path is reverted
  - Unit: `test_add_collection_job_has_correct_namespace` — created job's `namespace` matches caller's namespace
  - Unit: `test_add_collection_rollback_save_failure` — mock `update_collection_meta` to raise AND mock `_maybe_save_config` (on the rollback call) to also raise → assert response is 500; document that config and meta may be inconsistent in this scenario (operator must manually clean up)
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_routes_collections.py -k "add_collection" -v`

#### Task 4.3 — `remove_collection` namespace check + meta cleanup (`DELETE /collections/{name}`)
- [x] **File**: `packages/archon-search/archon_search/server/routes_collections.py`
- **Depends on**: Task 2.2, Task 1.3, Task 1.4
- **Description**:
  - Read `ns = request.state.namespace`.
  - Before the existing pinned-only check: call `meta = await search_store.get_collection_meta(name, namespace=ns)`. If `None`, raise `HTTPException(status_code=404, detail=f"Collection {name!r} not found")`.
  - Proceed with existing config cleanup (pinned-only rejection, list mutations, `_maybe_save_config()`).
  - After `search_store.drop_collection(name)`, call `await search_store.delete_collection_meta(name, ns)`.
- **Releasable**: after this task, `DELETE /collections/{name}` returns 404 for cross-namespace access and cleans up meta rows.
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_collections.py` (extend existing):
  - Unit: `test_remove_collection_cross_namespace_404` — meta row exists but for different namespace → 404; config unchanged
  - Unit: `test_remove_collection_deletes_meta_row` — successful delete → `delete_collection_meta` called with correct name + namespace
  - Unit: `test_remove_collection_success_drops_table_and_meta` — successful delete with correct namespace; assert BOTH `search_store.drop_collection(name)` AND `search_store.delete_collection_meta(name, ns)` are called; assert meta row is absent after completion (acceptance criterion 5: deletes BOTH LanceDB table AND meta row on success)
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_routes_collections.py -k "remove_collection" -v`

#### Task 4.4 — `get_collection_info` namespace check (`GET /collections/{name}`)
- [x] **File**: `packages/archon-search/archon_search/server/routes_collections.py`
- **Depends on**: Task 2.2, Task 1.3
- **Description**:
  - Read `ns = request.state.namespace`.
  - After the config path lookup, call `meta = await search_store.get_collection_meta(name, namespace=ns)`. If `None`, raise `HTTPException(status_code=404)`.
  - Use `meta.namespace` in the response dict (instead of hardcoded `DEFAULT_NAMESPACE`).
  - **IMPORTANT — eliminate duplicate meta lookup**: The existing handler at approximately line 216 of `routes_collections.py` has a bare `get_collection_meta(name)` call (no namespace arg) used to fetch centroid/doc_count data. After Task 1.3, this call defaults to `namespace=DEFAULT_NAMESPACE` and returns `None` for any non-default-namespace collection, breaking centroid/doc_count for those collections. Reuse the `meta` object already retrieved by the namespace 404 gate — do NOT call `get_collection_meta` a second time. Replace any existing bare `get_collection_meta(name)` call in this handler with the already-fetched `meta`.
- **Releasable**: after this task, `GET /collections/{name}` returns 404 for cross-namespace access.
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_collections.py` (extend existing):
  - Unit: `test_get_collection_info_cross_namespace_404` — meta row belongs to `"tenantB"`, request with `"tenantA"` → 404
  - Unit: `test_get_collection_info_namespace_in_response` — correct namespace → response includes actual namespace from meta
  - Unit: `test_get_collection_info_centroid_from_namespace_meta` — request `GET /collections/{name}` with correct namespace where meta has `centroid` data; assert response `centroid_present` is `True` (verifying centroid came from the namespace-filtered meta object, not a second bare lookup that would return `None` for non-default namespaces)
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_routes_collections.py -k "get_collection_info" -v`

#### Task 4.5 — `reindex_collection` namespace check (`POST /collections/{name}/reindex`)
- [x] **File**: `packages/archon-search/archon_search/server/routes_collections.py`
- **Depends on**: Task 2.2, Task 1.3, Task 3.3, Task 3.4
- **Description**:
  - Read `ns = request.state.namespace`.
  - After the config path check, call `meta = await search_store.get_collection_meta(name, namespace=ns)`. If `None`, raise `HTTPException(status_code=404)`.
  - Pass `namespace=ns` to `store.create()` and as explicit arg to `_default_ingest_task()`.
- **Releasable**: after this task, `POST /collections/{name}/reindex` returns 404 for cross-namespace access.
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_collections.py` (extend existing):
  - Unit: `test_reindex_cross_namespace_404` — meta row belongs to different namespace → 404
  - Unit: `test_reindex_same_namespace_succeeds` — meta row belongs to caller's namespace → 200/202 success status; created job has `namespace == ns`
  - Unit: `test_reindex_job_namespace` — reindex job carries correct namespace
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_routes_collections.py -k "reindex" -v`

---

### Phase 5 — Route enforcement: search, jobs, route, status, state
> **Releasable**: after this phase, all endpoints enforce namespace isolation end-to-end.

#### Task 5.1 — `POST /search` fix: shared store + namespace check
- [x] **File**: `packages/archon-search/archon_search/server/routes_search.py`
- **Depends on**: Task 2.2, Task 1.3
- **Description**:
  - Remove the fresh `SearchStore(config.db_path)` construction (current lines 64–65) and the `try/finally` block that disconnects it.
  - Use `store = request.app.state.search_store`.
  - Read `ns = request.state.namespace`.
  - Before calling `store.hybrid_search()`, call `meta = await store.get_collection_meta(body.collection, namespace=ns)`. If `meta is None`, return `JSONResponse({"detail": "collection not found"}, status_code=404)`.
  - **IMPORTANT — placement of namespace check**: The namespace check (`meta = await store.get_collection_meta(…)` and the 404 return) MUST be placed BEFORE (outside of) the existing `try/except Exception: return []` block that wraps `hybrid_search()`. Placing the namespace check inside that try block means: if the meta lookup raises unexpectedly, the exception is silently caught and returns `[]` instead of propagating the error correctly. Either: (a) place the namespace check before the try block, or (b) add `except HTTPException: raise` before the catch-all `except Exception` to ensure `HTTPException` from the namespace check propagates correctly.
  - **IMPORTANT — correct error response for LanceDB failure**: If the meta check raises a non-HTTPException (LanceDB error, network error), this is a data-layer failure — log `ERROR` (not WARNING) and return `JSONResponse({"detail": "service unavailable"}, status_code=503)`. Do NOT return 404 on DB outage — returning 404 causes clients to treat their cached data as stale and potentially discard valid collection references. A 5xx correctly signals a transient server problem. The namespace check is not bypassed — the request is rejected with 503.
- **Releasable**: after this task, `POST /search` enforces namespace and uses the shared store.
- **Tests (TDD)** — `packages/archon-search/tests/server/test_routes_search.py` (extend existing):
  - Unit: `test_search_uses_app_state_store` — no `SearchStore(…)` call in handler; uses `request.app.state.search_store`
  - Unit: `test_search_same_namespace_proceeds` — `get_collection_meta` returns a meta row with matching namespace → `hybrid_search()` is called (not short-circuited to 404/503)
  - Unit: `test_search_cross_namespace_404` — `get_collection_meta` returns `None` → 404 response
  - Unit: `test_search_store_exception_returns_503` — `get_collection_meta` raises (LanceDB error) → 503 response (not 404, not 200)
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/server/test_routes_search.py -v`

#### Task 5.2 — Job namespace checks (`GET/DELETE /jobs/{job_id}`)
- [x] **File**: `packages/archon-search/archon_search/server/routes_jobs.py`
- **Depends on**: Task 2.2, Task 3.1, Task 3.2
- **Description**:
  - In `get_job`: after `job = store.get(job_id)` (404 if None), add: `if job.namespace != request.state.namespace: raise HTTPException(status_code=404, detail="Job not found")`.
  - In `delete_job`: same check after `job = store.get(job_id)`.
  - The 404 (not 403) response prevents namespace enumeration — consistent with Decision 7.
- **Releasable**: after this task, cross-namespace job access returns 404.
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_jobs.py` (extend existing):
  - Unit: `test_get_job_cross_namespace_404` — job has `namespace="tenantA"`, request with `state.namespace="tenantB"` → 404
  - Unit: `test_get_job_same_namespace_200` — matching namespace → 200 (existing behavior preserved)
  - Unit: `test_delete_job_cross_namespace_404` — same pattern for DELETE
  - Unit: `test_delete_job_same_namespace_proceeds` — job has `namespace="tenantA"`, request with `state.namespace="tenantA"` → response status 202 (or 204, depending on implementation); job status transitions to `CANCELLING` or cancellation task is triggered; assert `store.transition(job_id, ...)` is called (not a no-op)
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_routes_jobs.py -v`

#### Task 5.3 — `POST /ingest` namespace propagation
- [x] **File**: `packages/archon-search/archon_search/server/routes_jobs.py`
- **Depends on**: Task 2.2, Task 3.3, Task 3.4
- **Description**:
  - In the `ingest` route handler, read `ns = request.state.namespace`.
  - This task owns ALL route handler callsite changes for the ingest route:
    - Change `job = store.create()` to `job = store.create(namespace=ns)`.
    - Change the `asyncio.create_task(...)` call to: `asyncio.create_task(_default_ingest_task(job.job_id, store, body, namespace=ns, pipeline_fn=pipeline_fn))`. (Task 3.4 converted the existing callsite to keyword arg form to avoid a CI-breaking positional shift — this task completes the wiring by adding `namespace=ns`.)
  - Do NOT add `namespace` to `IngestRequest` Pydantic model — namespace is server-side only.
- **Releasable**: after this task, `POST /ingest` tags jobs with caller's namespace.
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_jobs.py` (extend existing):
  - Unit: `test_ingest_passes_namespace_to_job` — `POST /ingest` with `state.namespace="tenantA"` → returned job has `"namespace": "tenantA"` in response
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_routes_jobs.py -k "ingest" -v`

#### Task 5.4 — `POST /route` namespace filter
- [ ] **File**: `packages/archon-search/archon_search/server/routes_route.py`
- **Depends on**: Task 2.2, Task 1.3
- **Description**:
  - Read `ns = request.state.namespace`.
  - Get `store = request.app.state.search_store`.
  - Call `all_meta = await store.get_all_collections_meta()` once. Build `ns_names = {m.name for m in all_meta if m.namespace == ns}`.
  - Filter `pinned_names` (derived from `config.pinned_collections`) to only those `in ns_names`.
  - Do NOT issue per-name `get_collection_meta()` calls in a loop (N+1 scan).
  - Route decision logic otherwise unchanged.
- **Releasable**: after this task, `POST /route` only considers pinned collections belonging to caller's namespace.
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_route.py` (extend existing):
  - Unit: `test_route_filters_pinned_by_namespace` — two pinned collections (one `tenantA`, one `tenantB`); `state.namespace="tenantA"` → only `tenantA` pinned collection in routing candidates
  - Unit: `test_route_includes_pinned_for_correct_namespace` — one pinned collection belongs to caller's namespace → that collection IS included in routing candidates
  - Unit: `test_route_no_pinned_for_namespace` — no pinned collections belong to caller's namespace → `pinned_names == []`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_routes_route.py -v`

#### Task 5.5 — `GET /status` namespace filter
- [ ] **File**: `packages/archon-search/archon_search/server/routes_status.py`
- **Depends on**: Task 2.2, Task 1.3
- **Description**:
  - Convert handler from `def status` to `async def status` (required for async store call).
  - Read `ns = request.state.namespace`.
  - Get `search_store = request.app.state.search_store`.
  - Call `all_meta = await search_store.get_all_collections_meta()` once. Build `ns_names = {m.name for m in all_meta if m.namespace == ns}`.
  - Filter the collection names returned in the status response to only those `in ns_names`.
  - Do NOT issue per-name `get_collection_meta()` calls in a loop.
- **Releasable**: after this task, `GET /status` returns only the caller's namespace collections.
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_status.py` (extend existing):
  - Unit: `test_status_filters_by_namespace` — two collections in meta (different namespaces); `state.namespace="tenantA"` → only `tenantA` collection in status response
  - Unit: `test_status_no_collections_for_namespace` — no collections in caller's namespace → status response has empty collection list
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_routes_status.py -v`

#### Task 5.6 — `GET /indexing-state` namespace filter
- [ ] **File**: `packages/archon-search/archon_search/server/routes_state.py`
- **Depends on**: Task 2.2, Task 1.3
- **Description**:
  - Convert handler from `def indexing_state` to `async def indexing_state`.
  - Read `ns = request.state.namespace`.
  - Get `search_store = request.app.state.search_store`.
  - Call `all_meta = await search_store.get_all_collections_meta()` once. Build `ns_names = {m.name for m in all_meta if m.namespace == ns}`.
  - Filter `state.collections.items()` to only include entries where `name in ns_names`.
  - Return the filtered dict.
- **Releasable**: after this task, `GET /indexing-state` returns only the caller's namespace entries.
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_state.py` (extend existing):
  - Unit: `test_indexing_state_filters_by_namespace` — state has two collections (different namespaces); `state.namespace="tenantA"` → filtered result contains only `tenantA` entry; **response body is a JSON object (dict with collection names as keys), not an array**
  - Unit: `test_indexing_state_empty_for_unknown_namespace` — no collections in namespace → `{}` result
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_routes_state.py -v`

---

### Phase 6 — Integration tests
> **Releasable**: after Task 6.1 — full two-namespace isolation verified end-to-end.

#### Task 6.1 — Two-namespace isolation integration test
- [ ] **File**: `packages/archon-search/tests/test_routes_e2e.py` (extend existing)
- **Depends on**: all Phase 4 and Phase 5 tasks
- **Description**:
  - Set up: two API keys (`"keyA"`, `"keyB"`), two namespaces (`"tenantA"`, `"tenantB"`), `config.namespaces = {"keyA": "tenantA", "keyB": "tenantB"}`.
  - Create two collections via `POST /collections/`: one per namespace. Wait for stub meta write (no need to wait for full ingest).
  - Assert `GET /collections/` with key A → only `tenantA` collection; with key B → only `tenantB` collection.
  - Assert `POST /search` with key A on `tenantB` collection → 404; on `tenantA` collection → 200 (or empty list; not 404).
  - Assert `GET /status` with key A → only `tenantA` collection present.
  - Assert `GET /jobs/{job_id}` with key A on a job created by key B → 404.
  - Assert single-key backward compatibility: create test app with `config.namespaces = {}`, single key → resolves to `DEFAULT_NAMESPACE`, all existing-behavior assertions pass.
- **Releasable**: after this task, namespace isolation is verified end-to-end.
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_e2e.py`:
  - Integration: `test_two_namespace_collection_isolation` — key A sees only its collection in `GET /collections/`
  - Integration: `test_two_namespace_search_isolation` — key A cannot search key B's collection (404)
  - Integration: `test_two_namespace_status_isolation` — key A status has only tenantA collections
  - Integration: `test_two_namespace_job_isolation` — key A cannot see key B's job (404)
  - Integration: `test_two_namespace_collection_detail_isolation` — key A requests `GET /collections/{name}` for key B's collection → 404
  - Integration: `test_two_namespace_delete_isolation` — key A attempts `DELETE /collections/{name}` for key B's collection → 404; key B's collection still exists (verify with key B's `GET /collections/`)
  - Integration: `test_single_key_backward_compat` — `namespaces={}`, existing key → all collections visible, all behavior preserved
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_routes_e2e.py -k "namespace" -v`
