# FEAT-041 — Search API Key Authentication
**Purpose**: Add Bearer token authentication to every archon-search endpoint except `GET /health`, with zero-config key generation for local users and automatic injection by `SearchClient`.
**Audience**: Implementers; security reviewers.
**Status**: To Do

---

## Background

archon-search exposes all endpoints to any local process with no authentication. This blocks namespace isolation (5b–5d) and makes the service unsafe to expose beyond localhost without a breaking change. FEAT-041 is the prerequisite for all 5b–5d increments.

Source brief: `Documentation/Backlog/search-auth-5a-api-key-brief.md`

Two raw `httpx.AsyncClient` callers bypass `SearchClient` and will silently get 401 after auth lands:
- `doctor.py` `_check_search_health()` — JSON-RPC call to `search_url`; must migrate to REST
- `search_context_provider.py` `_search_collection()` — JSON-RPC search call; must migrate to new `POST /search`

Both migrations are required before FEAT-041 is releasable.

---

## Goal

Every archon-search API call except `GET /health` requires a valid `ARCHON_SEARCH_API_KEY`. The key is auto-generated on first run, written to `~/.archon/.search.env`, and injected automatically by `SearchClient` via an `httpx.Auth` subclass. Existing local users need zero manual configuration. Both raw-httpx callers are migrated to `SearchClient`.

---

## Scope

### In Scope
- `key_manager.py` in archon-search: load from env → file → auto-generate (atomic write, chmod 600, concurrent safety)
- `APIKeyMiddleware` FastAPI middleware: validates `Authorization: Bearer {key}` on all routes except `GET /health` exact path; `secrets.compare_digest()`; startup log naming auth source
- `POST /search` REST endpoint in archon-search: accepts `{collection, query, top_k}`, returns list of `SearchResult` objects
- Extended `GET /collections` and `GET /collections/{name}` to return real `doc_count` and `centroid_present` fields (needed for doctor migration)
- `SearchApiKeyAuth` httpx.Auth subclass in `search_client.py`: lazy load, success caching, 401 retry, ERROR log on second 401
- `SearchClient.search()` method: wraps `POST /search`
- Fix `doctor.py` `_check_search_health()`: remove raw JSON-RPC, use `SearchClient.list_collections()` + `collection_info()`, adapt centroid check
- Fix `search_context_provider.py`: replace `_search_collection()` + `self._http` with `SearchClient.search()`
- `archon doctor` key file checks: existence, permissions 600 (POSIX only), authenticated `GET /status` via `SearchClient`
- All unit tests listed in Test Coverage section of brief; integration tests for key loading chain

### Out of Scope
- Key rotation (`archon search rotate-key`)
- Multiple named keys or per-key scopes (5c)
- HTTPS between Archon and archon-search
- `/status` and `/indexing-state` staying public

---

## Acceptance criteria
- [ ] `POST /health` → 200 (no auth required); `GET /health` → 200 (no auth required); `GET /health/` → 401
- [ ] All other routes without valid Bearer token → 401 with `WWW-Authenticate: Bearer`
- [ ] Valid Bearer token → request proceeds normally
- [ ] archon-search auto-generates `~/.archon/.search.env` with 600 permissions on first start (no pre-existing key)
- [ ] `ARCHON_SEARCH_API_KEY` env var overrides file; empty string treated as absent
- [ ] `SearchClient` injects key automatically on every request; lazy load on first use
- [ ] `SearchClient` self-heals 401 (clears cache, re-reads key file, retries); second 401 → ERROR log → `None`
- [x] `search_context_provider.py` uses `SearchClient.search()` with no raw httpx client
- [ ] `doctor.py` uses `SearchClient.list_collections()` + `collection_info()` with no raw httpx client
- [ ] `archon doctor` reports key file missing/wrong-permissions as failure
- [ ] `archon doctor` reports 401 on `GET /status` as actionable failure
- [ ] All new and existing tests pass
- [ ] No key value appears in any log

---

## What does NOT change
- `GET /health` endpoint semantics — stays public, still returns `{"status": "ok"}`
- `SearchClient` method signatures for all pre-existing methods
- `SearchContextProvider` public API (`get_pre_context`, `search_and_prepare`, `close`)
- archon config schema — no new `config.toml` keys; key lives in `~/.archon/.search.env`
- `[search] url` config key — unchanged

---

## Known limitations / accepted trade-offs
- `doc_count` in `GET /collections` is still hardcoded to 0 (only `GET /collections/{name}` gets the real count from the store); full list-level doc counts require iterating all collections and are expensive — deferred to a future task
- Windows: `chmod 600` is silently skipped in both archon-search key generation and `archon doctor`; no functional equivalent on Windows
- Startup race: `SearchClient` does not cache load failures, so the race self-heals without restart

---

## Architecture

### New module: `archon_search/key_manager.py`
```python
KEY_FILE = Path("~/.archon/.search.env").expanduser()
ENV_VAR = "ARCHON_SEARCH_API_KEY"

def load_or_generate_key() -> tuple[str, str]:
    """Return (key, source) where source is 'env var' | 'file: ...' | 'auto-generated'."""

def _load_from_env() -> str | None: ...
def _load_from_file() -> str | None: ...
def _generate_and_write() -> str: ...       # atomic write via O_EXCL + os.replace
def _validate_key(value: str) -> bool: ...  # non-empty hex string
```

### New middleware: `archon_search/server/middleware_auth.py`
```python
class APIKeyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, api_key: str) -> None: ...
    async def dispatch(self, request: Request, call_next) -> Response:
        # Exempt only: method == "GET" and path == "/health" (exact match)
        # Extract Bearer token; secrets.compare_digest comparison
        # Return 401 + WWW-Authenticate: Bearer on failure
```

### New route file: `archon_search/server/routes_search.py`
```python
class SearchRequest(BaseModel):
    collection: str
    query: str
    top_k: int = 5

class SearchResultSchema(BaseModel):
    doc_id: str
    chunk_id: str
    text: str
    score: float
    source_path: str

@router.post("/search", response_model=list[SearchResultSchema])
async def search(body: SearchRequest, request: Request) -> Any:
    # Uses request.app.state.embedder + SearchStore(config.db_path) + ModelReranker
```

### Extended collection endpoints
`GET /collections/{name}` — `get_collection_info()` in `routes_collections.py`:
- Add real `doc_count` from `SearchStore(config.db_path).count_documents(name)` (async, lazy init per-request)
- Rename response field `centroid_present` → keep as-is; doctor.py adapts to use it

### New `SearchApiKeyAuth` in `archon/ai/search_client.py`
```python
class SearchApiKeyAuth(httpx.Auth):
    _KEY_FILE = Path("~/.archon/.search.env").expanduser()
    _ENV_VAR = "ARCHON_SEARCH_API_KEY"

    async def async_auth_flow(self, request: Request) -> AsyncGenerator[Request, Response]:
        key = await self._get_key()
        if key is None:
            logger.warning("No ARCHON_SEARCH_API_KEY found — all search requests will fail with 401")
        if key is not None:
            request.headers["Authorization"] = f"Bearer {key}"
        response = yield request
        if response.status_code == 401:
            self._cached_key = None
            key = await self._get_key(force_reload=True)
            if key is not None:
                request.headers["Authorization"] = f"Bearer {key}"
            response = yield request
            if response.status_code == 401:
                logger.error("Search authentication failed — check ARCHON_SEARCH_API_KEY or ~/.archon/.search.env")
```

### `SearchClient.search()` new method
```python
async def search(
    self,
    collection: str,
    query: str,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """POST /search; returns list of result dicts or [] on any failure."""
```

### Doctor key file check in `archon/diagnostics.py`
```python
def _check_search_key_file() -> CheckResult:
    """Check ~/.archon/.search.env exists with permissions 600 (POSIX only)."""
```

### Data flow after FEAT-041
```
Telegram → Pipeline → SearchContextProvider.search_and_prepare()
                          → SearchClient.search(collection, query, top_k)  [SearchApiKeyAuth injects key]
                          → POST /search  →  archon-search  →  pipeline.search()
                          
archon doctor → SearchClient.list_collections() + collection_info()  [auth injected]
              → GET /collections, GET /collections/{name}
              
archon doctor → SearchClient.status()  [auth injected]
              → GET /status  → 200 or 401
```

---

## Tests

### archon-search side (`packages/archon-search/tests/`)
- **test_key_load_from_env** (unit): env var present → used; no file read
- **test_key_load_from_file** (unit): env absent + file present → file used
- **test_key_auto_generate** (unit): both absent → generates 64-char hex, writes file with 600 perms
- **test_key_env_priority_over_file** (unit): both present → env var wins
- **test_key_empty_env_falls_back_to_file** (unit): env set to empty string → treated as absent
- **test_key_malformed_file** (unit): non-hex value → ERROR log, treat as not found
- **test_key_atomic_write** (unit): temp file → replace (no partial-write window)
- **test_key_concurrent_race_loses** (unit): FileExistsError on O_EXCL → reads existing `.search.env`
- **test_key_orphaned_tmp** (unit): `.search.env.tmp` exists but `.search.env` absent → delete tmp, retry
- **test_key_file_permissions** (unit): auto-generated file created with 600; wider perms → chmod attempted
- **test_middleware_valid_key** (unit): valid Bearer → 200
- **test_middleware_missing_header** (unit): no Authorization → 401 + WWW-Authenticate
- **test_middleware_wrong_key** (unit): wrong key → 401
- **test_middleware_malformed_header** (unit): `Basic ...` → 401
- **test_middleware_empty_bearer** (unit): empty value → 401
- **test_middleware_get_health_exempt** (unit): `GET /health` → no auth check
- **test_middleware_post_health_requires_auth** (unit): `POST /health` → 401 (not exempt)
- **test_middleware_health_trailing_slash** (unit): `GET /health/` → 401 (not exempt)
- **test_middleware_compare_digest** (unit): verify `secrets.compare_digest` used, not `==`
- **test_search_endpoint_returns_results** (integration): `POST /search` with valid collection + query → list of result objects
- **test_search_endpoint_unknown_collection** (integration): unknown collection name → HTTP 200 with `[]`
- **test_collection_info_real_doc_count** (integration): `GET /collections/{name}` returns actual `doc_count` (not 0) after ingest
- **test_startup_log_auth_source** (unit): startup log includes source string without key value
- **test_startup_log_key_absent** (unit): `create_app()` logs INFO containing "auth"; assert the actual key value string is NOT present in the log output
- **test_key_roundtrip_generate_then_auth** (integration): Start with no key file and no env var; call `load_or_generate_key()` to generate and write the key to `KEY_FILE` (from `key_manager.KEY_FILE`); construct `SearchApiKeyAuth()` and verify its `_KEY_FILE` attribute resolves to the SAME path as `key_manager.KEY_FILE` (explicit assertion to catch path-coupling bugs); make a request to a protected endpoint using a test httpx.AsyncClient with `SearchApiKeyAuth()`; assert 200.
- **test_malformed_header_no_space** (unit): `Authorization: BearerSOMEVALUE` (no space between Bearer and token) → 401
- **test_delete_health_requires_auth** (unit): `DELETE /health` → 401 (not exempt)

### archon side (`tests/ai/test_search_client.py`, `tests/cli/`)
- **test_auth_lazy_load** (unit): no key at construction; key loaded on first request
- **test_auth_cached_on_success** (unit): second request uses cached key without re-reading file
- **test_auth_retry_not_cached** (unit): failed load → retry on next request (self-heals)
- **test_auth_401_clears_cache** (unit): 401 response → cache cleared, key re-read, request retried
- **test_auth_second_401_error_log** (unit): 401 after retry → ERROR logged exactly once
- **test_auth_no_key_warning** (unit): no key from any source → WARNING logged
- **test_search_client_search_success** (unit): `POST /search` returns list of result dicts
- **test_search_client_search_empty** (unit): empty collection → returns `[]`
- **test_search_client_search_failure** (unit): HTTP 500 → `[]`
- **test_context_provider_uses_search_client** (unit): `_search_collection` gone; search via `SearchClient.search()`
- **test_context_provider_no_raw_http** (unit): `SearchContextProvider` has no `_http` attribute
- **test_doctor_no_jsonrpc** (unit): `_SEARCH_JSONRPC_PAYLOAD` constant removed; no raw httpx call
- **test_doctor_collection_staleness** (unit): staleness check uses `collection_info()` response
- **test_doctor_centroid_check** (unit): centroid check uses `centroid_present` bool field
- **test_doctor_401_actionable** (unit): authenticated status check returns 401 → failure with message
- **test_check_search_key_file_missing** (unit): no file → CheckResult failure
- **test_check_search_key_file_wrong_perms** (unit): permissions != 600 → CheckResult failure
- **test_check_search_key_file_ok** (unit): file exists, 600 → CheckResult success
- **test_check_search_key_file_windows_skip** (unit): on Windows, permission check returns INFO skip

---

## Documentation update
- [ ] `CLAUDE.md`, `archon/ai/` → `search_client.py` bullet: add `SearchApiKeyAuth`, `search()`, auth lazy-load behaviour, key file path
- [ ] `CLAUDE.md`, `archon/cli/doctor.py` bullet: update to reflect removal of JSON-RPC, new key file check
- [ ] `packages/archon-search/README.md` (if exists): note `ARCHON_SEARCH_API_KEY` env var and `.search.env` file

---

## Task breakdown

### Phase 1 — Key management and auth middleware (archon-search)
> **Releasable**: after Task 1.2 — archon-search enforces auth on all routes except `GET /health`. All existing REST callers that go through `SearchClient` will fail with 401 until Phase 4 lands. The two raw-httpx callers (`search_context_provider.py` and `doctor.py`) will also fail until Phase 5 lands. **Do not deploy Phase 1 without Phases 4 AND 5 deployed in the same release.**

#### Task 1.1 — `key_manager.py`: key loading and auto-generation
- [x] **File**: `packages/archon-search/archon_search/key_manager.py`
- [x] **File**: `packages/archon-search/tests/test_key_manager.py`
- **Depends on**: nothing
- **Description**:
  - `KEY_FILE: Path = Path("~/.archon/.search.env").expanduser()`
  - `ENV_VAR: str = "ARCHON_SEARCH_API_KEY"`
  - `load_or_generate_key() -> tuple[str, str]`: returns `(key, source)` where source is one of `"env var"`, `"file: ~/.archon/.search.env"`, `"auto-generated"`. Calls `_load_from_env()` first, then `_load_from_file()`, then `_generate_and_write()`.
  - `_load_from_env() -> str | None`: reads `os.environ.get(ENV_VAR, "")` — empty string treated as absent (returns `None`).
  - `_load_from_file() -> str | None`: reads `KEY_FILE` if it exists; parses `ARCHON_SEARCH_API_KEY=<value>` line; calls `_validate_key(value)` — returns `None` with ERROR log on malformed/empty; strips whitespace before validation.
  - `_validate_key(value: str) -> bool`: returns `True` if value is non-empty and matches `^[0-9a-f]+$`.
  - `_generate_and_write() -> str`: sequence — (a) `os.makedirs(KEY_FILE.parent, exist_ok=True)`; (b) `tmp = KEY_FILE.parent / ".search.env.tmp"`; (c) attempt `fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)` — on `FileExistsError` (first attempt): sleep 100ms, attempt `_load_from_file()` — if key found return it; else delete `tmp` and retry step (c) once; if the second `O_EXCL` attempt also raises `FileExistsError`, attempt `_load_from_file()` once more — if found, return the key; otherwise raise `RuntimeError('key generation failed: concurrent write conflict')` with a clear message; (d) after acquiring `fd`, wrap steps (d) and (e) in a `try/finally` block that calls `os.close(fd)` in the `finally` clause — even if `os.replace()` raises: write `ARCHON_SEARCH_API_KEY={key}\n` to fd; (e) `os.replace(str(tmp), str(KEY_FILE))`; (f) return key. If `os.replace(tmp, KEY_FILE)` raises an exception: (a) log at ERROR: `f'key generation failed: {exc}'` (exception type only, not key value), (b) attempt `os.unlink(str(tmp))` in an `except OSError: pass` block as best-effort cleanup, then re-raise the original exception. This prevents orphaned tmp files from silently blocking future starts.
  - `_chmod_600(path: Path) -> None`: on non-Windows, attempt `path.chmod(0o600)` — `PermissionError` logs WARNING and continues. On Windows (`sys.platform == "win32"`), log INFO "permission check skipped on Windows" and return.
  - On file load: if existing file permissions are wider than 600, call `_chmod_600(KEY_FILE)`.
  - Key value must never appear in any log message.
- **Releasable**: after this task, `load_or_generate_key()` is callable from startup code.
- **Tests (TDD)** — `packages/archon-search/tests/test_key_manager.py`:
  - Unit: `test_load_from_env` — env var set → returned; no file read
  - Unit: `test_load_from_file` — env absent, file present with valid hex → file key returned
  - Unit: `test_auto_generate` — both absent → 64-char hex key generated; verify file exists with content exactly `ARCHON_SEARCH_API_KEY=<64-char-hex>\n` (no quotes, no trailing space, newline-terminated); permissions 600; source is `"auto-generated"`
  - Unit: `test_env_priority_over_file` — both set → env var returned
  - Unit: `test_empty_env_falls_back_to_file` — env `""` → treated as absent → file used
  - Unit: `test_malformed_file_non_hex` — file contains `ARCHON_SEARCH_API_KEY=not-hex` → None, ERROR logged
  - Unit: `test_malformed_file_empty_value` — file contains `ARCHON_SEARCH_API_KEY=` → None, ERROR logged
  - Unit: `test_key_with_whitespace_stripped` — file contains `ARCHON_SEARCH_API_KEY=  <64-char-hex>  ` (leading/trailing spaces); assert stripped value passes validation and is returned correctly; also test with `\r\n` Windows line endings
  - Unit: `test_atomic_write` — temp file is renamed to final (os.replace called)
  - Unit: `test_concurrent_race_loses` — FileExistsError on O_EXCL + `.search.env` present → reads existing file
  - Unit: `test_orphaned_tmp` — `.search.env.tmp` exists, `.search.env` absent → deletes tmp, retries, succeeds
  - Unit: `test_generated_file_permissions_600` — generated file has mode 0o600
  - Unit: `test_chmod_on_wide_perms` — file with 0o644 → chmod 600 called
  - Unit: `test_key_file_missing_prefix` — file contains content like `SOME_OTHER_VAR=abc123` with no `ARCHON_SEARCH_API_KEY=` line → `_load_from_file()` returns `None`; no crash
  - Unit: `test_generate_and_write_exhausted_raises_runtime_error` — mock `os.open` to always raise `FileExistsError` AND `_load_from_file()` to always return `None`; assert `RuntimeError` is raised with "key generation failed" message
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_key_manager.py -v`

#### Task 1.2 — `APIKeyMiddleware` and startup wire-up
- [x] **File**: `packages/archon-search/archon_search/server/middleware_auth.py`
- [x] **File**: `packages/archon-search/archon_search/server/app.py` (add middleware + startup log)
- [x] **File**: `packages/archon-search/tests/server/test_middleware_auth.py`
- **Depends on**: Task 1.1
- **Description**:
  - `class APIKeyMiddleware(BaseHTTPMiddleware)` in `middleware_auth.py`:
    - `__init__(self, app, api_key: str) -> None`: stores `_api_key = api_key`.
    - `dispatch(self, request: Request, call_next: Callable) -> Response`:
      - Exempt check: `request.method == "GET" and request.url.path == "/health"` → call `call_next(request)` directly.
      - All other requests: extract `Authorization` header; split on `" "` expecting two parts; first part must be `"Bearer"` (case-sensitive); compare second part with `secrets.compare_digest(token, self._api_key)` — both missing header and wrong key return `Response(status_code=401, headers={"WWW-Authenticate": "Bearer"})`.
      - Key must not appear in any log; log at DEBUG on auth success with only path + method.
  - Wire into `create_app()` in `app.py`:
    - Call `load_or_generate_key()` before creating FastAPI instance; store the returned `(key, source)`.
    - After creating FastAPI, call `app.add_middleware(APIKeyMiddleware, api_key=key)`.
    - Log at INFO: `f"API key authentication enabled (source: {source})"` — key value must NOT appear.
  - Startup log must come AFTER the app is created (so the app can start even if the log call fails).
- **Releasable**: after this task, archon-search enforces auth on all routes except `GET /health`.
- **Tests (TDD)** — `packages/archon-search/tests/server/test_middleware_auth.py`:
  - Unit: `test_valid_key_passes` — valid Bearer token → 200
  - Unit: `test_missing_header_401` — no Authorization header → 401 + `WWW-Authenticate: Bearer`
  - Unit: `test_wrong_key_401` — wrong key → 401
  - Unit: `test_malformed_header_basic` — `Authorization: Basic xxx` → 401
  - Unit: `test_empty_bearer_value` — `Authorization: Bearer ` (empty after Bearer) → 401
  - Unit: `test_get_health_exempt` — `GET /health` → 200 without token
  - Unit: `test_post_health_requires_auth` — `POST /health` → 401
  - Unit: `test_get_health_trailing_slash_not_exempt` — `GET /health/` → 401
  - Unit: `test_compare_digest_not_equality` — verify `secrets.compare_digest` is called (mock or inspect bytecode)
  - Unit: `test_malformed_header_no_space` — `Authorization: BearerSOMEVALUE` (no space between Bearer and token) → 401
  - Unit: `test_delete_health_requires_auth` — `DELETE /health` → 401 (not exempt)
  - Unit: `test_startup_log_contains_source` — `create_app()` logs INFO mentioning source; key value absent from log
  - Unit: `test_startup_log_key_absent` — `create_app()` logs INFO containing "auth"; assert the actual key value string is NOT present in the log output
  - Integration: `test_key_roundtrip_generate_then_auth` — Start with no key file and no env var; call `load_or_generate_key()` to generate and write the key to `KEY_FILE` (from `key_manager.KEY_FILE`); construct `SearchApiKeyAuth()` and verify its `_KEY_FILE` attribute resolves to the SAME path as `key_manager.KEY_FILE` (explicit assertion to catch path-coupling bugs); make a request to a protected endpoint using a test httpx.AsyncClient with `SearchApiKeyAuth()`; assert 200.
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/server/test_middleware_auth.py -v`

---

### Phase 2 — POST /search endpoint (archon-search)
> **Releasable**: after Task 2.1 — `SearchClient.search()` (Phase 4, Task 4.3) can call this endpoint.

#### Task 2.1 — `POST /search` route
- [x] **File**: `packages/archon-search/archon_search/server/routes_search.py`
- [x] **File**: `packages/archon-search/archon_search/server/app.py` (include search router)
- [x] **File**: `packages/archon-search/tests/server/test_routes_search.py`
- **Depends on**: nothing (independent of Phase 1; can be done in parallel)
- **Description**:
  - `SearchRequest(BaseModel)`: `collection: str`, `query: str`, `top_k: int = 5`. Validate: `collection` must be non-empty, `query` must be non-empty, `top_k >= 1`.
  - `SearchResultSchema(BaseModel)`: `doc_id: str`, `chunk_id: str`, `text: str`, `score: float`, `source_path: str`.
  - `@router.post("/search", response_model=list[SearchResultSchema])`:
    - Build `Embedder` from `request.app.state.embedder` (already in `app.state`).
    - Instantiate `SearchStore(request.app.state.config.db_path)` — lazy construction per-request is acceptable for now.
    - Instantiate `ModelReranker` from config's reranker settings (or use `PassthroughReranker` if no reranker configured).
    - Call: `vector = await app.state.embedder.embed_one(body.query)`, then `candidates = await store.hybrid_search(body.collection, vector, body.query, top_k=body.top_k * 3)`, then `reranked = await reranker.rerank(body.query, candidates, top_k=body.top_k)`.
    - If collection not found in store: return HTTP 200 with `[]`. Do NOT return 404. The caller (`SearchClient.search()`) treats both 200+empty and connection errors as 'no results' and returns `[]` in both cases. Returning 200+[] is consistent: it distinguishes 'collection exists but has no matches' from 'server error' cleanly via HTTP status codes, without requiring callers to handle 404.
    - Any exception → log WARNING + return `[]`.
    - Convert `SearchResult` objects to `SearchResultSchema` dicts for response.
  - Register router in `create_app()`: `app.include_router(search_router)`.
  - Note: the `search_store` in `app.state` is set to `None` in `create_app()` — use `SearchStore(config.db_path)` constructed directly in the handler (consistent with how `routes_route.py` builds `MultiCollectionRouter` per-request).
- **Releasable**: after this task, `POST /search` is available; callers can be migrated.
- **Tests (TDD)** — `packages/archon-search/tests/server/test_routes_search.py`:
  - Unit: `test_search_returns_results` — valid request → list of result dicts with correct fields
  - Unit: `test_search_empty_collection_returns_empty` — collection not found → `[]`, no exception
  - Unit: `test_search_invalid_top_k` — `top_k=0` → 422 validation error
  - Unit: `test_search_empty_query` — `query=""` → 422 (Pydantic validation)
  - Integration: `test_search_end_to_end` — ingest a doc, search, verify result appears
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/server/test_routes_search.py -v`

---

### Phase 3 — Extend collection detail (archon-search)
> **Releasable**: after Task 3.1 — `GET /collections/{name}` returns real `doc_count`, needed for doctor migration.

#### Task 3.1 — Real `doc_count` in `GET /collections/{name}`
- [x] **File**: `packages/archon-search/archon_search/server/routes_collections.py`
- [x] **File**: `packages/archon-search/tests/server/test_routes_collections.py` (update/add tests)
- **Depends on**: nothing
- **Description**:
  - **Verification step**: confirm field mismatch between JSON-RPC `get_collections_meta` and `GET /collections/{name}`:
    - JSON-RPC returns: `last_indexed`, `doc_count` (real count), `centroid` (vector or None).
    - REST `GET /collections/{name}` returns: `last_indexed` ✓, `doc_count: 0` (hardcoded) ✗, `centroid_present: bool` (different field name and type).
    - Conclusion: two fixes needed — real `doc_count` and doctor must adapt to `centroid_present`.
  - In `get_collection_info()` handler (line ~181):
    - After resolving `resolved = path_to_name[name]`, add:
      ```python
      try:
          store = SearchStore(config.db_path)
          doc_count = await store.count_documents(name)
      except Exception:
          doc_count = 0
      ```
    - Replace hardcoded `"doc_count": 0` with `"doc_count": doc_count`.
  - Also populate `centroid_present` with real data from the LanceDB collection metadata. The centroid is stored in the `_archon_collection_meta` table managed by `CollectionMeta` in `archon_search/collection_meta.py`. To check if a centroid exists, use `SearchStore(config.db_path).get_collection_meta(name)` — if the returned `CollectionMeta` object has a non-None/non-empty `centroid` field, `centroid_present = True`. To avoid recreating `SearchStore` on every `collection_info()` call, add `SearchStore` as a lazy singleton on `app.state` (initialized in `create_app()` with `app.state.search_store = SearchStore(config.db_path)`) and reuse it in both the centroid check and the doc_count query. NOTE: `IndexingStateStore` (the `state_store` already used in the handler) does NOT contain centroid data — do not look for `cp.centroid` there. A hardcoded `False` value is NOT acceptable — it causes `archon doctor` to permanently warn that every collection has no centroid.
  - `GET /collections` (list endpoint) keeps `doc_count: 0` — iterating all collections is expensive; deferred.
- **Releasable**: after this task, `GET /collections/{name}` returns real doc count and real centroid_present status.
- **Tests (TDD)** — `packages/archon-search/tests/server/test_routes_collections.py`:
  - Unit: `test_collection_info_doc_count_real` — collection with 3 docs → `doc_count: 3` in response
  - Unit: `test_collection_info_doc_count_zero_on_store_error` — store raises → `doc_count: 0`, no exception
  - Unit: `test_collection_info_centroid_present_true` — collection with centroid → `centroid_present: true` in response
  - Unit: `test_collection_info_centroid_present_false` — collection without centroid → `centroid_present: false` in response
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/server/test_routes_collections.py -v`

---

### Phase 4 — SearchClient auth and search method (archon)
> **Releasable**: after Task 4.2 — all `SearchClient` calls automatically include auth. After Task 4.3 — `SearchClient.search()` is callable.

#### Task 4.1 — `SearchApiKeyAuth` httpx.Auth subclass
- [x] **File**: `archon/ai/search_client.py` (new class, before `SearchClient`)
- [x] **File**: `tests/ai/test_search_client.py` (new test class `TestSearchApiKeyAuth`)
- **Depends on**: nothing (independent of Phase 1)
- **Description**:
  - `class SearchApiKeyAuth(httpx.Auth)`:
    - Class-level constants: `_KEY_FILE: Path = Path("~/.archon/.search.env").expanduser()`, `_ENV_VAR: str = "ARCHON_SEARCH_API_KEY"`.
    - `__init__(self) -> None`: `self._cached_key: str | None = None`.
    - `async def async_auth_flow(self, request: httpx.Request) -> AsyncGenerator[httpx.Request, httpx.Response]`:
      1. `key = await self._resolve_key()`.
      2. If key is None: log WARNING `"No ARCHON_SEARCH_API_KEY found — all search requests will fail with 401"`.
      3. If key is not None: set `request.headers["Authorization"] = f"Bearer {key}"`. If key is None: do NOT set the Authorization header. Let the request proceed without auth — the server will return 401, which is the expected signal that triggers the 401 retry path (step 5). Setting `Bearer ` (empty) is semantically incorrect and would cause two spurious requests instead of the standard auth flow.
      4. `response = yield request`.
      5. If `response.status_code == 401`:
         - `self._cached_key = None`.
         - `key = await self._resolve_key(force_reload=True)`.
         - **Short-circuit when key is None AND the reload also returns None**: if `_resolve_key(force_reload=True)` returns `None`, do NOT yield a second request. The generator ends. The first 401 response propagates to the caller (which calls `raise_for_status()` → `HTTPStatusError`). The ERROR log ('Search authentication failed') must NOT fire in this case — that ERROR is reserved for the scenario where a key EXISTS but is rejected. A 'no key' scenario only produces the WARNING.
         - If key is not None: set `request.headers["Authorization"] = f"Bearer {key}"`. Yield the second request.
         - `response = yield request`.
         - If `response.status_code == 401`: log ERROR `"Search authentication failed — check ARCHON_SEARCH_API_KEY or ~/.archon/.search.env"` ONLY when the second request (retry with a freshly-loaded key) also returns 401 — not when no key could be loaded at all. Generator ends (no more yields — 401 propagates to caller).
    - `async def _resolve_key(self, force_reload: bool = False) -> str | None`:
      - If `self._cached_key and not force_reload`: return `self._cached_key`.
      - When `force_reload=True`: clear `self._cached_key` to `None`, then resolve from all sources using the SAME priority (env var first, then file) — do NOT skip the env var. The 'reload' means clearing the local cache, not changing source priority. Re-reading the env var is useful if a proxy returned a spurious 401 (env var is still valid); re-reading the file handles the key-rotation case. Skipping the env var would break env-var-only Docker/CI deployments on any transient 401.
      - Try env var first: `val = os.environ.get(self._ENV_VAR, "")` — non-empty → cache + return.
      - Try file: `asyncio.to_thread(self._KEY_FILE.read_text)` in try/except — parse `ARCHON_SEARCH_API_KEY=<val>` line; validate hex; cache + return on success.
      - Return None (do not cache None — retry on next call).
    - Do NOT override `sync_auth_flow()` for normal async operation. Add a `sync_auth_flow()` override that raises `NotImplementedError('SearchApiKeyAuth does not support synchronous httpx clients')`. This prevents silent auth bypass if anyone accidentally uses the Auth subclass with `httpx.Client` (sync). Production code uses `httpx.AsyncClient` exclusively — but an explicit error is better than silently sending unauthenticated requests.
- **Releasable**: after this task, `SearchApiKeyAuth` is instantiable and injectable.
- **Tests (TDD)** — `tests/ai/test_search_client.py` (new class `TestSearchApiKeyAuth`):
  - Unit: `test_lazy_no_key_at_init` — `SearchApiKeyAuth()` doesn't read file or env at construction
  - Unit: `test_key_loaded_on_first_request` — key injected on first `async_auth_flow` call
  - Unit: `test_key_cached_on_success` — second call uses cached key without re-reading
  - Unit: `test_failure_not_cached` — resolver returns None → next call re-reads (no persistent None cache)
  - Unit: `test_401_clears_cache_and_retries` — first 401 → cache cleared, key re-read, second request sent
  - Unit: `test_second_401_error_log` — both requests get 401 → ERROR log emitted exactly once; generator ends
  - Unit: `test_no_key_warning` — env absent + file absent → WARNING logged with exact message
  - Unit: `test_env_priority` — env var set → used without reading file
  - Unit: `test_sync_auth_flow_raises` — calling `sync_auth_flow` raises `NotImplementedError`
  - Unit: `test_auth_401_then_reload_succeeds` — mock key file to return key_A initially (cached), then key_B after reload; mock server to reject key_A (401) and accept key_B (200); assert: first request gets 401, second request (with key_B) gets 200, result is the 200 response, no ERROR log
  - Unit: `test_none_key_then_401_no_error_log` — env absent + file absent (key = None), request sent without auth header, server returns 401, retry: key still None after reload → generator ends without second request; assert: WARNING was logged, ERROR was NOT logged, generator produced exactly 1 request yield
  - Checkpoint: `uv run pytest tests/ai/test_search_client.py -k "TestSearchApiKeyAuth" -v`

#### Task 4.2 — Wire `SearchApiKeyAuth` into `SearchClient`
- [x] **File**: `archon/ai/search_client.py` (`SearchClient.__init__`)
- [x] **File**: `tests/ai/test_search_client.py` (update `TestSearchClientInit` or add fixture)
- **Depends on**: Task 4.1
- **Description**:
  - Modify `SearchClient.__init__`:
    - Add `auth: httpx.Auth | None = None` parameter (default: `None`).
    - If `auth is None`: instantiate `auth = SearchApiKeyAuth()`.
    - Pass `auth=auth` to `httpx.AsyncClient(...)` constructor.
    - Remove any `headers={"Authorization": ...}` if accidentally added.
  - For tests that need to bypass auth: pass `auth=httpx.Auth()` (no-op) or mock transport.
  - The `transport` parameter already exists — no change needed there.
- **Releasable**: after this task, all `SearchClient` instances automatically inject auth.
- **Tests (TDD)** — `tests/ai/test_search_client.py`:
  - Unit: `test_search_client_uses_auth_subclass` — `SearchClient()` constructs with `SearchApiKeyAuth` instance
  - Unit: `test_search_client_accepts_custom_auth` — custom auth object passed via `auth=` parameter is used
  - Checkpoint: `uv run pytest tests/ai/test_search_client.py -k "TestSearchClient" -v`

#### Task 4.3 — `SearchClient.search()` method
- [x] **File**: `archon/ai/search_client.py`
- [x] **File**: `tests/ai/test_search_client.py` (new class `TestSearchClientSearch`)
- **Depends on**: Task 4.2, Task 2.1 (for contract alignment)
- **Description**:
  - Add `search()` method after the route-related methods:
    ```python
    async def search(
        self,
        collection: str,
        query: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """POST /search; returns list of result dicts or [] on any failure."""
    ```
  - Request body: `{"collection": collection, "query": query, "top_k": top_k}`.
  - 200 response: `list(resp.json())` — each dict has `doc_id`, `chunk_id`, `text`, `score`, `source_path`.
  - Error handling: `TimeoutException` → `[]` + WARNING; `ConnectError` → `[]` + DEBUG; `HTTPStatusError` where `status_code in (401, 403)` → `[]`, no extra log (Auth subclass already logged); other `HTTPStatusError` → `[]` + WARNING with status code; `Exception` → `[]` + WARNING.
  - Note the 401/403 silent-skip: the Auth subclass logs the actionable ERROR; the method must NOT add a redundant generic warning for 401/403.
- **Releasable**: after this task, `SearchClient.search()` is callable.
- **Tests (TDD)** — `tests/ai/test_search_client.py`:
  - Unit: `test_search_success` — 200 with result list → list of dicts
  - Unit: `test_search_empty_result` — 200 with `[]` → `[]`
  - Unit: `test_search_timeout` — TimeoutException → `[]`, WARNING logged
  - Unit: `test_search_connect_error` — ConnectError → `[]`, DEBUG (not WARNING)
  - Unit: `test_search_http_500` — HTTP 500 → `[]`
  - Unit: `test_search_http_401_no_double_log` — HTTP 401 → `[]`; assert NO extra WARNING beyond Auth subclass
  - Unit: `test_search_unexpected_exception` — RuntimeError → `[]`
  - Checkpoint: `uv run pytest tests/ai/test_search_client.py -k "TestSearchClientSearch" -v`

---

### Phase 5 — Fix raw httpx callers (archon)
> **Releasable**: after both tasks — all search calls go through `SearchClient`; 401s are handled transparently.

#### Task 5.1 — Fix `search_context_provider.py`
- [x] **File**: `archon/ai/search_context_provider.py`
- [x] **File**: `tests/ai/test_search_context_provider.py`
- **Depends on**: Task 4.3
- **Description**:
  - **Remove**: `_search_collection()` module-level function (entire function, lines ~43–94).
  - **Remove**: `self._http = httpx.AsyncClient(...)` from `SearchContextProvider.__init__`.
  - **Remove**: `self._search_url` field (no longer needed after migration).
  - **Remove**: `close()` call to `self._http.aclose()` — body becomes empty; keep the `close()` method signature for protocol compatibility but make it a no-op: `async def close(self) -> None: pass`.
  - **Update** `search_and_prepare()` inner `_bounded_search()`:
    ```python
    async def _bounded_search(collection: str) -> list[SearchResult]:
        async with semaphore:
            raw = await self._search_client.search(collection, query, cfg.top_k_return)
            return [SearchResult(**r) for r in raw]
    ```
  - **search_url propagation chain — what to change vs. what to leave alone:**
    - `SearchContextProvider.__init__` receives `search_url` which is the MCP endpoint URL (e.g., `cfg.search.url + '/mcp'`). After this migration, `SearchContextProvider` no longer needs this URL (REST search goes through `SearchClient` which uses the REST base URL). Remove the `search_url` parameter from `SearchContextProvider.__init__` and all usages within the class.
    - `Pipeline.__init__` currently passes `search_url=search_url` to `SearchContextProvider(...)` — remove only this argument from the `SearchContextProvider(...)` call in `pipeline.py`. Do NOT remove `search_url` from `Pipeline.__init__` itself — `Pipeline` still passes `search_url` to `Decomposer.__init__` and possibly to background agent MCP configuration; removing it from `Pipeline` would break those uses.
    - `Decomposer.__init__` stores `search_url` as `self._search_url` — this field is for the SDK MCP server integration and must NOT be removed.
    - `SessionManager`, `Gateway`, `BackgroundAgentManager` all pass `search_url` for SDK MCP integration — do NOT change these callers.
    - Summary: only change is removing `search_url` from `SearchContextProvider.__init__` signature and from the one call site in `Pipeline.__init__` that passes it to `SearchContextProvider()`.
  - Remove `import httpx` if no longer used in this file.
- **Releasable**: after this task, search queries go through `SearchClient` and inherit auth.
- **Tests (TDD)** — `tests/ai/test_search_context_provider.py`:
  - Unit: `test_no_raw_http_client` — `SearchContextProvider` instance has no `_http` attribute
  - Unit: `test_bounded_search_uses_search_client` — `search_and_prepare()` calls `mock_search_client.search()`
  - Unit: `test_close_is_noop` — `await provider.close()` does not raise
  - Unit: `test_pipeline_search_url_still_passed_to_decomposer` — mock `Pipeline.__init__` and verify `Decomposer` still receives `search_url`; verify `SearchContextProvider` does NOT receive it
  - Unit: `test_search_and_prepare_mixed_results` — one collection returns `[]`, another returns results; assert merged output contains only results from the non-empty collection; verify no exception
  - Integration: `test_search_and_prepare_full_flow` — mock `SearchClient.search()` returns results; assert `_normalize_and_merge` called with correct data
  - Checkpoint: `uv run pytest tests/ai/test_search_context_provider.py -v`

#### Task 5.2 — Fix `doctor.py` `_check_search_health()`
- [x] **File**: `archon/cli/doctor.py`
- [x] **File**: `tests/cli/test_doctor.py`
- **Depends on**: Task 4.2, Task 3.1
- **Description**:
  - **Remove**: `_SEARCH_JSONRPC_PAYLOAD` constant.
  - **Remove**: raw `httpx.AsyncClient` block (lines ~91–97, the `async with httpx.AsyncClient(...)` block).
  - **Remove**: `import httpx` from `doctor.py` if no longer used.
  - **Replace** the raw httpx block with:
    ```python
    collection_names = await client.list_collections()  # returns list[dict]
    # Iterate and call collection_info per collection for doc_count + centroid_present
    ```
  - Fetch metadata per-collection in parallel using `asyncio.gather()`: `col_details = await asyncio.gather(*[client.collection_info(name) for name in col_names], return_exceptions=False)`. Pair results with names using `zip(col_names, col_details)`. This prevents N sequential HTTP round-trips for N collections.
  - NOTE: `list_collections()` returns `doc_count: 0` for all collections (hardcoded; Task 3.1 only fixes `collection_info()`). Do NOT use `doc_count` from `list_collections()` for doctor checks. The pattern is: use `list_collections()` only to get collection NAMES, then call `collection_info(name)` per-collection for all metadata fields including `doc_count`, `last_indexed`, and `centroid_present`.
  - **Adapt** staleness check: use `col_detail.get("last_indexed")` — same field, compatible.
  - **Adapt** centroid check: use `not col_detail.get("centroid_present", True)` instead of `col.get("centroid") is None`.
  - **Adapt** empty check: use `col_detail.get("doc_count", 0) == 0` — same field name, now real count.
  - If `collection_info()` returns None for a collection → skip silently (server-side error).
  - The existing flow logic (`col_state`, `status`, `in_progress`/`pending`/`failed`/`done`) remains unchanged; only the source of `last_indexed`, `doc_count`, `centroid` changes.
- **Releasable**: after this task, `archon doctor` search health check uses only authenticated REST calls.
- **Tests (TDD)** — `tests/cli/test_doctor.py`:
  - Unit: `test_no_jsonrpc_payload` — `_SEARCH_JSONRPC_PAYLOAD` constant removed; no raw httpx call
  - Unit: `test_staleness_check_uses_collection_info` — `collection_info()` response with `last_indexed` → staleness warning printed
  - Unit: `test_centroid_check_uses_centroid_present` — `centroid_present: false` → warning printed
  - Unit: `test_doc_count_zero` — `doc_count: 0` → empty warning printed
  - Unit: `test_collection_info_none_skipped` — `collection_info()` returns None → no exception, collection skipped
  - Unit: `test_doctor_collection_checks_parallel` — mock `list_collections()` to return 3 collections; mock each `collection_info()` call with artificial delay (asyncio.sleep(0.01)); assert all 3 `collection_info` calls are started concurrently (total elapsed time is closer to 1 delay than 3 delays); verify all 3 results are processed
  - Unit: `test_centroid_check_no_warning_when_present` — `centroid_present: true` in `collection_info()` response → no centroid warning in doctor output
  - Checkpoint: `uv run pytest tests/cli/test_doctor.py -v`

---

### Phase 6 — Doctor key file checks
> **Releasable**: after Task 6.1 — `archon doctor` verifies key file and authenticated status.

#### Task 6.1 — `_check_search_key_file()` in `diagnostics.py`
- [x] **File**: `archon/diagnostics.py`
- [x] **File**: `tests/cli/test_doctor.py` (add key file check tests)
- **Depends on**: nothing (independent; can be done in parallel with Phase 4)
- **Description**:
  - Add `_check_search_key_file() -> CheckResult`:
    - Follow the `_check_context_windows()` pattern in `diagnostics.py`: self-load config via `load_config()` inside the function and early-return `CheckResult('search key file', True, 'search disabled')` when `cfg.search.enabled is False`. This keeps `run_checks()` signature unchanged and ensures the MCP toolkit's `archon_doctor` tool also includes this check.
    - Before checking the file: read `val = os.environ.get('ARCHON_SEARCH_API_KEY', '').strip()`. If `val` is non-empty AND matches `^[0-9a-f]+$` (valid hex), return `CheckResult('search key file', True, 'key provided via env var')`. If `val` is non-empty but NOT valid hex, return `CheckResult('search key file', False, 'ARCHON_SEARCH_API_KEY env var is set but contains invalid value (expected hex string)')`. Only proceed to file check when env var is absent or empty. The file is optional when the env var is set (Docker/CI scenario). Only report the file as missing when BOTH the env var is absent AND the file is absent.
    - Checks `Path("~/.archon/.search.env").expanduser()` exists → `CheckResult("search key file", False, "not found — run: archon search start to generate key")` on missing.
    - On Windows (`sys.platform == "win32"`): return `CheckResult("search key file", True, "permissions check skipped on Windows")`.
    - On POSIX: check `oct(stat.S_IMODE(file.stat().st_mode))` == `0o600` → `CheckResult("search key file", False, "permissions too wide — expected 600")` if not.
    - Pass: `CheckResult("search key file", True, "ok")`.
  - Wire into `run_doctor()` in `doctor.py`: add `_check_search_key_file` to the checks list (only when `cfg.search.enabled`).
- **Releasable**: after this task, `archon doctor` reports key file issues.
- **Tests (TDD)** — `tests/cli/test_doctor.py`:
  - Unit: `test_check_search_key_file_missing` — no file → CheckResult failure
  - Unit: `test_check_search_key_file_wrong_perms` — 0o644 → CheckResult failure
  - Unit: `test_check_search_key_file_ok` — file exists, 0o600 → CheckResult pass
  - Unit: `test_check_search_key_file_windows_skip` — `sys.platform == "win32"` → INFO skip (use `monkeypatch.setattr(sys, "platform", "win32")`)
  - Unit: `test_check_search_key_file_env_var_set_skips_file_check` — `ARCHON_SEARCH_API_KEY` env var set to valid hex → CheckResult pass even if file absent; also test `ARCHON_SEARCH_API_KEY=not-hex` → CheckResult failure with message about invalid value
  - Unit: `test_check_search_key_file_search_disabled` — `cfg.search.enabled = False` → CheckResult with "disabled" message; no file system access
  - Checkpoint: `uv run pytest tests/cli/test_doctor.py -k "key_file" -v`

#### Task 6.2 — Authenticated `GET /status` check in doctor
- [x] **File**: `archon/cli/doctor.py`
- [x] **File**: `tests/cli/test_doctor.py`
- **Depends on**: Task 4.2
- **Description**:
  - Modify `_check_search_health()` to return a list of `CheckResult` objects instead of `None`. Within the existing `async with SearchClient(search_url) as client:` block, after the health + indexing_state calls, add an auth check:
    - Call `result = await client.health()` — if it returns a dict and HTTP status was 200, that confirms authentication worked; OR if the result dict's HTTP context was 401, report failure.
    - Cleaner: attempt `await client.status()` if that method exists, or check `await client.health()` which is already called. If the existing `health()` call (line 80) returns non-None, auth succeeded (because without a valid key the server would have returned 401 → health() returns None). Report: `CheckResult('search auth', True, 'authenticated')`.
    - If `health()` at line 80 returned None (server unreachable or 401): report `CheckResult('search auth', False, '401 Unauthorized or unreachable — check ARCHON_SEARCH_API_KEY or ~/.archon/.search.env')`.
    - Note: this piggybacks on the existing `health()` call at the top of `_check_search_health()` — no second HTTP round-trip needed.
    - `_check_search_health()` returns `list[CheckResult]`. The caller in `run_doctor()` extends the results list and prints them with the standard formatting.
    - Remove the separate `_check_search_auth(cfg)` function — the logic is inlined into `_check_search_health()`.
- **Releasable**: after this task, `archon doctor` validates the full auth chain.
- **Tests (TDD)** — `tests/cli/test_doctor.py`:
  - Unit: `test_check_search_auth_success` — `SearchClient.health()` returns a dict → CheckResult with pass included in return list
  - Unit: `test_check_search_auth_401_actionable` — `SearchClient.health()` returns None → CheckResult failure with actionable message included in return list
  - Unit: `test_check_search_auth_disabled` — search disabled → auth check not run
  - Checkpoint: `uv run pytest tests/cli/test_doctor.py -k "search_auth" -v`

---

### Phase 7 — Documentation
> **Releasable**: after Task 7.1 — CLAUDE.md reflects the new auth architecture.

#### Task 7.1 — CLAUDE.md update
- [x] **File**: `CLAUDE.md`
- **Depends on**: all previous tasks
- **Description**:
  - In `archon/ai/` → `search_client.py` bullet: add `SearchApiKeyAuth` (lazy load, caches on success, 401 retry, distinct ERROR log); add `search()` method (`POST /search`, returns `list[dict]`); note key file `~/.archon/.search.env`.
  - In `archon/cli/doctor.py` bullet (or `diagnostics.py`): note `_check_search_key_file()` (existence + 600 perms) and authenticated status check.
  - In `[search]` config section: add note that auth key lives in `~/.archon/.search.env` (not config.toml); `ARCHON_SEARCH_API_KEY` env var override.
  - Remove mention of `_SEARCH_JSONRPC_PAYLOAD` if present anywhere.
- **Releasable**: after this task, CLAUDE.md is accurate.
- **Tests (TDD)** — N/A (verify by reading the updated section)
  - Checkpoint: `grep -n "SearchApiKeyAuth\|search_client\|search()" CLAUDE.md`
