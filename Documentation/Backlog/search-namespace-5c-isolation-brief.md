# Feature Brief: Namespace Isolation at Storage and Query Layers (5c)

## Problem

After 5b, every collection record carries a `namespace` field and existing data has been migrated to `"default"`. However, namespace isolation is not enforced anywhere. The following concrete failures remain:

1. `APIKeyMiddleware` (`middleware_auth.py`) validates the key with `secrets.compare_digest` against a single global key but never resolves it to a namespace or injects `request.state.namespace`. No namespace context reaches any route handler.

2. `GET /collections/` (`routes_collections.py` line 73) reads from `config.collections + config.pinned_collections` — filesystem paths with no namespace concept. It hardcodes `"namespace": DEFAULT_NAMESPACE` in every response (line 88). A key bound to namespace `"tenantA"` sees all of namespace `"default"`'s collections.

3. `POST /search` (`routes_search.py` lines 64–65) creates a fresh `SearchStore(config.db_path)` per request — bypassing `app.state.search_store` and any namespace context — then calls `hybrid_search()` with no namespace check. Any key can search any collection.

4. `update_collection_meta()` (`store.py` line 315) deletes by `name` alone: `await table.delete(f"name = '{meta.name}'")`). If two namespaces register a collection with the same name, an upsert in one namespace deletes the other namespace's metadata row.

5. `_default_ingest_task()` (`routes_jobs.py` lines 54–82) runs as a detached `asyncio.create_task`. It receives `IngestRequest` which has no `namespace` field. The namespace of the registering caller is not captured or stored on the job.

6. `SearchStore.list_collections()` (`store.py` line 225) hardcodes `namespace=DEFAULT_NAMESPACE` in the `CollectionInfo` constructor. Non-default namespace collections return the wrong namespace.

7. `get_collection_meta(name)` (`store.py` lines 260–272) fetches by `name` alone. When two namespaces own same-named collections this returns the first match — a data corruption vector.

8. `POST /route` (`routes_route.py` line 85) uses `config.pinned_collections` directly with no namespace filtering. A key bound to namespace `"tenantA"` routes against `"default"` namespace pinned collections.

9. `GET /status` (`routes_status.py`) and `GET /indexing-state` (`routes_state.py`) return all collections with no per-namespace filtering.

10. `GET /jobs/{job_id}` and `DELETE /jobs/{job_id}` (`routes_jobs.py` lines 100–132) have no namespace check — any key can read or cancel any job.

11. CLI commands (`archon_search/cli/collection.py`) call `SearchStore` directly, bypassing all HTTP auth and namespace enforcement.

12. There is no key→namespace mapping in config, middleware, or any data store.

## Goal

After 5c:

- Every HTTP request carries a resolved namespace, derived server-side from the authenticated API key.
- A key bound to namespace A cannot list, read, write, delete, or search collections registered to namespace B.
- A key bound to namespace A cannot see or cancel jobs created by namespace B.
- All collection CRUD, search, ingest, routing, status, and job operations enforce caller namespace.
- The existing single-key, single-namespace (`"default"`) deployment works with zero config change.
- Telemetry endpoints are excluded from namespace filtering (explicitly deferred with documented rationale).

## Users & Context

**Operator / service administrator**: configures namespaces in `archon-search.toml` under `[namespaces]`. Issues API keys to tenants or subsystems. For single-operator deployments (current common case), no `[namespaces]` section is needed — the single key falls back to `"default"`.

**Tenant / API consumer**: holds an API key that maps to exactly one namespace. Unaware of other namespaces. Receives 404 (not 403) when accessing resources from another namespace.

**Archon parent process**: uses `SearchApiKeyAuth` in `archon/ai/search_client.py`, loads the single key from `~/.archon/.search.env` with no namespace awareness. After 5c, this key falls back to `"default"` — no changes required to `SearchClient`.

**CLI operator**: uses `archon-search collection add/remove/list/info/reindex` and `archon-search ingest`. These call `SearchStore` directly and have admin-level access to all namespaces. This is an explicit security boundary, not a gap.

## Core Flow

The end-to-end namespace enforcement path after 5c:

1. **Config startup**: `load_config()` reads `[namespaces]` section from `archon-search.toml` (if present) and populates `SearchConfig.namespaces: dict[str, str]` — a mapping of `key_hex → namespace_name`. If the section is absent or empty, `SearchConfig.namespaces` is `{}`.

2. **App startup**: `create_app()` passes the namespace map to `APIKeyMiddleware`. The middleware constructor becomes `APIKeyMiddleware(app, api_key: str, namespaces: dict[str, str])`.

3. **Request arrives**: `APIKeyMiddleware.dispatch()` extracts the Bearer token. It iterates the `namespaces` dict using `secrets.compare_digest` for each key (constant-time, no early exit). The first match gives the namespace. If no match is found but the token matches the server's own `api_key` (the auto-generated single key), namespace resolves to `DEFAULT_NAMESPACE`. If no match at all, return 401. On success, set `request.state.namespace = resolved_namespace`.

4. **Namespace never comes from the client**: namespace is derived solely from the server-side key mapping. Request headers, query parameters, and request body fields are never consulted for namespace.

5. **Route handler reads `request.state.namespace`**: every handler that touches collection data uses this value.

6. **Collection visibility check**: `GET /collections/` looks up each config-path collection name in `_archon_collection_meta` and returns only those whose `namespace` column matches `request.state.namespace`. Collections with no meta row are treated as `DEFAULT_NAMESPACE`.

7. **Collection access check** (single-collection endpoints): before any read/write/search operation on a named collection, the handler calls `get_collection_meta(name, namespace=request.state.namespace)`. If the result is `None` (either the collection doesn't exist or belongs to another namespace), return 404. Never 403.

8. **Search**: `POST /search` uses `request.app.state.search_store` (not a fresh `SearchStore`). Before calling `hybrid_search()`, it calls `get_collection_meta(body.collection, namespace=request.state.namespace)`. If `None`, return 404.

9. **Ingest**: `POST /collections/` and `POST /collections/{name}/reindex` pass `request.state.namespace` as an explicit argument to `_default_ingest_task()`. Namespace is never written onto `IngestRequest` — namespace is server-side only.

10. **Job visibility**: `GET /jobs/{job_id}` and `DELETE /jobs/{job_id}` check that `job.namespace == request.state.namespace`. If not, return 404.

## In Scope

### Config layer (`archon_search/config.py`)

- Add `namespaces: dict[str, str] = field(default_factory=dict)` to `SearchConfig`. This is the key_hex→namespace mapping.
- Add `[namespaces]` section parsing to `load_config()`: read `doc.get("namespaces", {})` and populate `config.namespaces`. Each entry is `"<key_hex>" = "<namespace_name>"`. If the section is absent, `config.namespaces` stays `{}`.
- Add `_validate_namespace(name: str) -> None` function in `constants.py` (alongside `DEFAULT_NAMESPACE`): namespace names must match `^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$`. Raises `ValueError` on invalid input. Called in `APIKeyMiddleware` on resolved namespace values and in `update_collection_meta()`.
- `save_config()` must preserve `[namespaces]` section on round-trip. Confirmed: tomlkit preserves all unmodified sections (including `[namespaces]`) on round-trip. `save_config()` only rewrites `[collections]` arrays; any `[namespaces]` entries written before calling `save_config()` will survive unchanged. No code change needed.

### Middleware (`archon_search/server/middleware_auth.py`)

- `APIKeyMiddleware.__init__` gains `namespaces: dict[str, str]` parameter (default `{}`). Store as `self._namespaces`.
- `dispatch()` key resolution:
  1. Extract Bearer token as before.
  2. Iterate all `self._namespaces.items()` — use a `resolved_namespace: str | None = None` flag variable; never break early (avoid timing leakage). On match, set `resolved_namespace = ns`.
  3. If `resolved_namespace is None`, try `secrets.compare_digest(token, self._api_key)`. Match → `resolved_namespace = DEFAULT_NAMESPACE`.
  4. If still `None` → return 401.
  5. Import `_validate_namespace` from `archon_search.constants`. Call `_validate_namespace(resolved_namespace)`. If invalid (operator misconfiguration), log ERROR and return 500.
  6. Set `request.state.namespace = resolved_namespace`.
- **Performance note**: linear scan over N keys with `compare_digest` is acceptable for operator-scale deployments (< 100 keys). Document as a known limitation.

### App factory (`archon_search/server/app.py`)

- Pass `namespaces=config.namespaces` to `APIKeyMiddleware` in `create_app()`.

### Store (`archon_search/store.py`)

**`update_collection_meta()` — upsert key**:
- Line 314: keep `if any(r["name"] == meta.name for r in rows):` — filter by `name` alone (revert composite key).
- Line 315: keep `await table.delete(f"name = '{meta.name}'")`  — delete by `name` alone.
- Under Decision 6 (globally unique collection names), only one namespace can own a given collection name, so delete-by-name-alone is safe. The composite key `(name, namespace)` is deferred to when namespace-prefixed table names are introduced (Future Iterations).
- Import `_validate_namespace` from `archon_search.constants`. Add a `_validate_namespace(meta.namespace)` call at the top of `update_collection_meta()` — this validates the namespace field for correctness, not SQL safety (namespace is not in the filter expression).
- Both `meta.name` and `meta.namespace` are validated before any SQL is executed (via `_validate_collection` and `_validate_namespace`), making interpolation safe.

**`get_collection_meta()` — add namespace parameter**:
- Signature: `get_collection_meta(self, name: str, namespace: str = DEFAULT_NAMESPACE) -> CollectionMeta | None`
- Filter (line 269): `matching = [r for r in rows if r["name"] == name and (r.get("namespace") or DEFAULT_NAMESPACE) == namespace]`
- All route handlers enforcing namespace isolation must pass `namespace=request.state.namespace`. CLI and startup code may omit (defaults to `DEFAULT_NAMESPACE`) or pass an explicit value.

**`list_collections()` — read namespace from meta**:
- Remove the hardcoded `namespace=DEFAULT_NAMESPACE` in `CollectionInfo(...)` (line 225).
- After computing `doc_count` and `chunk_count` for each table name, call `meta = await self.get_collection_meta(name)` to get the actual namespace. If `meta is None` (orphan table with no meta row), use `DEFAULT_NAMESPACE` as fallback.
- **Callers after 5c**: `list_collections()` is called by CLI commands (which bypass HTTP). Route handlers do NOT call `list_collections()` — they use `get_all_collections_meta()` + in-memory set for all list/filter operations (see route handler specs). This avoids N+1 meta scans at the HTTP layer; the per-name lookup in `list_collections()` is acceptable for CLI use.

**New method `delete_collection_meta()`**:
- Signature: `async def delete_collection_meta(self, name: str, namespace: str) -> None`
- Validates `name` with `_validate_collection` and `namespace` with `_validate_namespace`.
- Opens `_META_TABLE` if it exists.
- Executes `await table.delete(f"name = '{name}' AND namespace = '{namespace}'")`
- No-op if `_META_TABLE` does not exist.

**`get_all_collections_meta()` — no change**:
- Remains unfiltered. CLI and admin operations use this. Route handlers needing namespace-filtered metadata build an in-memory set from this call rather than issuing N per-name calls (see route handler specs below).

### Routes — collections (`archon_search/server/routes_collections.py`)

**`list_collections` (GET /collections/)**:
- Convert handler from `def list_collections` to `async def list_collections` — required because `get_all_collections_meta()` is an async method.
- After building `path_to_name`, call `all_meta = await search_store.get_all_collections_meta()` once (where `search_store = request.app.state.search_store`). Build an in-memory set: `ns_names = {m.name for m in all_meta if m.namespace == request.state.namespace}`. Do not issue a per-name `get_collection_meta()` call inside a loop — that is an N+1 scan.
- Include a collection in the result only if `name in ns_names`. Use the namespace from the matching meta entry in the response.
- Collections with no meta row (not in `all_meta`): treat as `DEFAULT_NAMESPACE` — include only if `request.state.namespace == DEFAULT_NAMESPACE`.

**`add_collection` (POST /collections/)**:
- After the existing dedup check (resolved paths in config), additionally call `get_all_collections_meta()` and check if any row has `name == path_to_collection_name(resolved)`. If yes, return 409 with `"collection name already registered in another namespace"` (globally unique name enforcement — CRIT-7).
- Immediately write a stub `CollectionMeta` row: `await search_store.update_collection_meta(CollectionMeta(name=collection_name, namespace=request.state.namespace))`. This registers namespace ownership before the background task completes.
- If `update_collection_meta()` raises an exception, the config append (step 3) must be rolled back before re-raising. Roll back by removing the appended path from `config.collections` and calling `_maybe_save_config()` again.
- Pass `request.state.namespace` as an explicit argument to `_default_ingest_task()` — do not set it on `IngestRequest` (see Routes — jobs below).

**`remove_collection` (DELETE /collections/{name})**:
- Before proceeding, call `await search_store.get_collection_meta(name, namespace=request.state.namespace)`. If `None`, return 404.
- After `search_store.drop_collection(name)`, call `await search_store.delete_collection_meta(name, request.state.namespace)`.

**`get_collection_info` (GET /collections/{name})**:
- After config path lookup, call `await search_store.get_collection_meta(name, namespace=request.state.namespace)`. If `None`, return 404.
- Use actual namespace from meta row in response.

**`reindex_collection` (POST /collections/{name}/reindex)**:
- After config path check, call `await search_store.get_collection_meta(name, namespace=request.state.namespace)`. If `None`, return 404.
- Pass `request.state.namespace` as an explicit argument to `_default_ingest_task()` — do not set it on `IngestRequest`.

### Routes — search (`archon_search/server/routes_search.py`)

- Remove the fresh `SearchStore` creation (lines 64–65: `store = SearchStore(config.db_path); await store.connect()`).
- Use `store = request.app.state.search_store`.
- Remove the `try/finally` block that disconnects the store.
- Before `store.hybrid_search()`, call `meta = await store.get_collection_meta(body.collection, namespace=request.state.namespace)`. If `meta is None`, return HTTP 404 with `"collection not found"`.

### Routes — jobs (`archon_search/server/routes_jobs.py`)

**`IngestRequest`**:
- Do NOT add a `namespace` field. Namespace comes from `request.state.namespace` (server-side), never from the request body — a client must not be able to supply or override namespace.

**`IngestJob`** (dataclass in `archon_search/types.py`):
- Add `namespace: str = DEFAULT_NAMESPACE` field.
- Update `job_to_dict()` in `archon_search/jobs/model.py` to include `'namespace': job.namespace`.
- `JobStore._load()` must handle pre-5c JSON: since `IngestJob` has `namespace` with a default, `IngestJob(**item)` will work for pre-5c JSON (missing key uses default). Post-5c JSON read by pre-5c code will fail (`TypeError: unexpected keyword argument`). This is a one-way migration — downgrading from 5c to pre-5c is a breaking change. Document this in deployment notes.
- `JobStore.create()` gains `namespace: str = DEFAULT_NAMESPACE` parameter, passed to `IngestJob(...)`.

**`_default_ingest_task()`**:
- Add `namespace: str` parameter. The namespace is passed explicitly by the route handler, not derived from `IngestRequest`.
- Inside the task, pass `namespace` to `update_collection_meta()` explicitly. Set `IngestJob.namespace` from this parameter, not from `ingest_body`.

**`ingest` route (POST /ingest)**:
- Do not mutate `body.namespace`. Instead, pass `request.state.namespace` as an explicit argument: `asyncio.create_task(_default_ingest_task(job_id, store, ingest_body, request.state.namespace))`.
- Pass `namespace=request.state.namespace` to `store.create()`.

**`get_job` route (GET /jobs/{job_id})**:
- After retrieving the job, check `job.namespace == request.state.namespace`. If not, return 404.

**`delete_job` route (DELETE /jobs/{job_id})**:
- Same namespace check → 404 if mismatch.

### Routes — route (`archon_search/server/routes_route.py`)

- After building `pinned_names`, call `all_meta = await store.get_all_collections_meta()` once. Build `ns_names = {m.name for m in all_meta if m.namespace == request.state.namespace}`. Filter `pinned_names` to only those `in ns_names`. Do not use `asyncio.gather()` of N `get_collection_meta()` calls — that is an N+1 scan.
- Obtain `store` from `request.app.state.search_store`.

### Routes — status and indexing-state

**`GET /status` (`routes_status.py`)**:
- Convert handler from `def status` to `async def status` (needed for async store calls).
- `search_store = request.app.state.search_store`.
- Call `all_meta = await search_store.get_all_collections_meta()` once. Build `ns_names = {m.name for m in all_meta if m.namespace == request.state.namespace}`. Filter `all_names` to only include names `in ns_names`. Do not issue per-name `get_collection_meta()` calls in a loop.

**`GET /indexing-state` (`routes_state.py`)**:
- Convert to `async def indexing_state`.
- Build `ns_names` from `await search_store.get_all_collections_meta()` (same pattern as `/status`). Filter `state.collections.items()` to only include entries where `name in ns_names`. Return the filtered dict.

### Unit tests

- `APIKeyMiddleware` with `namespaces={}`: existing single-key behavior preserved; token matches `api_key` → `request.state.namespace == DEFAULT_NAMESPACE`.
- `APIKeyMiddleware` with `namespaces={"keyA": "tenantA", "keyB": "tenantB"}`: key A → `"tenantA"`; key B → `"tenantB"`; unknown token → 401; server's own key (not in dict) → `DEFAULT_NAMESPACE`.
- `request.state.namespace` is correctly set (ASGI test client).
- `get_collection_meta(name, namespace="tenantA")` returns `None` when the collection's meta row has `namespace="tenantB"`.
- `update_collection_meta()` deletes by `name` alone (Decision 6: names are globally unique): calling `update_collection_meta()` for `name="foo", namespace="tenantA"` deletes any existing row where `name="foo"` and inserts a new row with `namespace="tenantA"`. Verify that a row for the same name (which can only belong to the same namespace under Decision 6) is correctly replaced without affecting other rows.
- `GET /collections/` returns only collections in caller's namespace.
- `GET /collections/{name}` returns 404 for cross-namespace access.
- `POST /search` returns 404 for a collection belonging to another namespace.
- `GET /jobs/{job_id}` returns 404 when job belongs to different namespace.
- `DELETE /jobs/{job_id}` returns 404 when job belongs to different namespace.
- `POST /collections/` returns 409 if collection name already registered in any namespace.
- `POST /route` excludes pinned collections belonging to other namespaces.
- `load_config()` with `[namespaces]` section: `config.namespaces` populated correctly.
- `load_config()` without `[namespaces]` section: `config.namespaces == {}`.
- `save_config()` preserves existing `[namespaces]` section.
- `JobStore` loading a JSON file with jobs that have no `namespace` field deserializes to `DEFAULT_NAMESPACE`.
- Integration test: two namespaces (`"tenantA"`, `"tenantB"`), two keys, two collections — each key sees only its own collection in `GET /collections/`, `POST /search`, `GET /status`.

## Out of Scope

- **Telemetry namespace filtering** (`GET /telemetry/entries`, `GET /telemetry/stats`): telemetry entries are stored in raw JSONL files. Filtering by collection→namespace requires per-entry parsing and meta-table lookups disproportionate to the security value in a single-operator deployment. Any authenticated key can read telemetry regardless of namespace. Deferred to a follow-up.

- **Namespace-prefixed LanceDB table names** (`{namespace}__{collection}`): collection names remain globally unique in 5c. Table name prefixing (enabling same-named collections across namespaces) is deferred until a concrete multi-tenant use case exists.

- **CLI `--namespace` flag**: CLI commands bypass HTTP auth and have admin-level namespace access. Adding per-namespace CLI scoping is a UX nicety, not a security requirement. Deferred.

- **Namespace provisioning API** (`POST /namespaces`): namespaces are declared only in config. A management API can be added when self-service provisioning is needed.

- **Multiple namespaces per key**: one key maps to exactly one namespace in 5c.

- **`Collection` and `CollectionDetail` types in `archon_search/types.py`**: these public API types don't include `namespace`. Route handlers construct raw dicts that already include the field. Types.py update can happen in 5d.

- **Per-chunk namespace field**: namespace enforcement is at collection granularity. Chunk-level ACLs are 5d.

- **Cross-namespace admin key**: no super-key in this increment.

- **`pinned_collections` config restructure**: the config list remains a flat list of paths. Namespace membership is determined entirely by `_archon_collection_meta`, not by config position. Pinned collections are filtered at request time via meta-table lookup.

## Key Decisions

**Decision 1: Key→namespace mapping lives in `[namespaces]` section of `archon-search.toml`.**
Format: `"<key_hex>" = "<namespace_name>"` — a flat TOML table. Loaded into `SearchConfig.namespaces: dict[str, str]`. Alternative (LanceDB table) rejected — operators need to edit config, not a database.

**Decision 2: Multiple keys can map to the same namespace (key rotation support).**
Two entries `"keyA" = "tenantA"` and `"keyB" = "tenantA"` are valid. Middleware iterates all entries.

**Decision 3: One key maps to exactly one namespace.**
A key cannot grant access to multiple namespaces simultaneously. Cross-namespace access is not supported in 5c.

**Decision 4: Keys not in `[namespaces]` fall back to `"default"` namespace.**
The server's auto-generated key (from `key_manager.py`) is not in `[namespaces]` by default. When a token matches `self._api_key` but is not in `self._namespaces`, namespace resolves to `DEFAULT_NAMESPACE`. This is the zero-config fallback preserving existing single-key deployments with no config change.

**Decision 5: `key_manager.py` is unchanged.**
`~/.archon/.search.env` continues to hold the single auto-generated key. The namespace mapping in config is orthogonal. Operators who want named namespaces add entries to `[namespaces]` manually.

**Decision 6: Collection names are globally unique across all namespaces in 5c.**
`path_to_collection_name()` produces the LanceDB table name. Two namespaces cannot have a collection with the same name. `POST /collections/` checks `get_all_collections_meta()` for name collisions and returns 409. Namespace-prefixed table names are deferred.

**Decision 7: Cross-namespace access returns 404, not 403.**
A 403 would confirm the resource exists in another namespace. A 404 is indistinguishable from "not found," preventing namespace enumeration.

**Decision 8: CLI has admin-level access to all namespaces.**
CLI commands call `SearchStore` directly without HTTP auth. This is an operator-level channel — same security model as filesystem access. Documented explicitly. Appropriate for the current single-operator deployment model.

**Decision 9: `pinned_collections` in config belong to `"default"` namespace by default.**
A key bound to a non-default namespace does not see pinned collections in routing or collection lists unless those collections are also registered (via `POST /collections/`) in that namespace. Namespace membership is determined entirely by `_archon_collection_meta`, not by config position.

**Decision 10: Telemetry endpoints are not namespace-filtered in 5c.**
`GET /telemetry/entries` and `GET /telemetry/stats` are accessible to any authenticated key regardless of namespace. Known limitation. Deferred — disproportionate engineering effort for the current deployment model.

**Decision 11: Archon `SearchClient` requires no changes for 5c.**
`SearchApiKeyAuth` loads the single key from `~/.archon/.search.env`. After 5c, this key resolves to `DEFAULT_NAMESPACE` via the fallback rule. All Archon operations on the search service continue without parent-repo changes.

## Edge Cases & Constraints

**Namespace validation regex**: must match `^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$` — same as `_COLLECTION_RE`. `_validate_namespace(name: str) -> None` (defined in `constants.py` alongside `DEFAULT_NAMESPACE`) raises `ValueError` on mismatch. Both middleware and `update_collection_meta()` import from `archon_search.constants`. Middleware calls this on every resolved namespace; if validation fails (operator misconfiguration), middleware logs ERROR and returns 500 (not 401 — issue is server-side).

**Middleware timing consideration**: iterating N keys with `secrets.compare_digest` is not perfectly constant-time because loop length is observable. Use a flag variable (`resolved_namespace = None`, iterate all, no `break`) to avoid early exit. Acceptable for the current threat model (operator-issued keys). Document as a known limitation.

**Meta table lookup failure during namespace check**: if `get_collection_meta()` raises an unexpected exception (LanceDB error, schema mismatch), the safe fallback is to catch, log WARNING, and treat the result as `None` → 404. Never allow an exception to bypass the namespace check.

**Concurrent collection registration**: two requests for the same path from different namespaces arriving simultaneously may both pass the name-collision check before either writes the stub meta row. The second `update_collection_meta()` upsert wins and overwrites the first. This race is acceptable for the current single-operator model. A LanceDB-level transaction would be needed to eliminate it — out of scope for 5c.

**Job namespace field in existing persistent JSON**: `JobStore._load()` must use `.get("namespace", DEFAULT_NAMESPACE)` (not `item["namespace"]`) when deserializing jobs. `IngestJob` must have `namespace: str = DEFAULT_NAMESPACE` as a field with a default value.

**`GET /status` and `GET /indexing-state` — sync→async conversion**: both handlers are currently synchronous. Adding `get_collection_meta()` async calls requires converting to `async def`. Non-breaking change under FastAPI.

**`SearchStore.list_collections()` — performance**: adding a `get_collection_meta()` call per collection increases meta table scans. Acceptable for 5c. A batched meta lookup (fetch all rows once, index by name) is a performance optimization for a follow-up.

**Stub meta write failure in `POST /collections/`**: if `update_collection_meta()` raises after the config append and `save_config()` have already committed (e.g., LanceDB disk error), the collection path is registered in config but has no namespace ownership record. The route handler must catch this exception, remove the appended path from `config.collections`, call `_maybe_save_config()` to revert the config, then re-raise as a 500 error. Without rollback, the path remains in config but is invisible to namespace-filtered list responses, and a subsequent `POST /collections/` for the same path returns 409 (config-level dedup).

**`pinned_collections` filtering in `/route`**: call `get_all_collections_meta()` once and filter in-memory. Do not use `asyncio.gather()` of N `get_collection_meta()` calls — that is an N+1 scan.

**`save_config()` and `[namespaces]` preservation**: `save_config()` currently only rewrites `[collections]` arrays. Confirmed: tomlkit preserves all unmodified sections (including `[namespaces]`) on round-trip. Any `[namespaces]` entries written before calling `save_config()` will survive unchanged. No code change needed.

## Future Iterations

**5d — Document/chunk-level security trimming**: add optional `acl: list[str]` to chunk records. At query time, filter retrieved chunks to those the caller's identity can access.

**Namespace-prefixed LanceDB table names**: when multiple tenants legitimately need same-named collections (e.g., both want a `"documentation"` collection), table naming must include the namespace prefix. Requires data migration and changes to `path_to_collection_name()`.

**Namespace provisioning API**: `POST /namespaces` and `DELETE /namespaces/{name}` for self-service management without editing config.

**CLI `--namespace` flag**: `archon-search collection list --namespace tenantA` for operator-level scoped CLI operations.

**Telemetry namespace filtering**: parse JSONL telemetry entries by collection name, look up namespace in meta table, filter by caller namespace.

**Admin/super-key**: a key that maps to all namespaces simultaneously, useful for monitoring and backup.

**Namespace-aware routing**: router's centroid database scoped to the caller's namespace (current router uses global config paths).

**`list_collections()` store method — N+1 addressed**: route handlers now call `get_all_collections_meta()` once and filter in-memory rather than issuing N individual `get_collection_meta()` calls. The store method itself still scans all meta rows on each call; a further optimization (indexed lookup) can be deferred until there is a measured performance problem.

## Recommendation

This is the correct increment to build next. The 5b brief documented the specific 5c debt items (`update_collection_meta()` upsert key, `list_collections()` hardcoding), and those items are now fully specified here. The scope is self-contained: all changes are within the `archon-search` package — no changes required in the parent Archon repo or in `SearchClient`. The fallback rule (unmapped keys resolve to `DEFAULT_NAMESPACE`) makes the migration zero-risk for existing single-key deployments: operators who do not add a `[namespaces]` section observe no behavioral change whatsoever. The only behavioral difference is that endpoints now filter by namespace — and since all existing collections are `"default"` and the single key falls back to `"default"`, the filter returns the same set as before.

5c is prerequisite for 5d (chunk-level ACLs), item 30 (collection-level access policies), and any future multi-tenant or SaaS deployment of archon-search.
