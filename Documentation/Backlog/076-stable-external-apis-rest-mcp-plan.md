# FEAT-045 — Stable External APIs: REST + MCP
**Purpose**: Publish a versioned, fully-documented OpenAPI specification for the archon-search REST API; modernize the MCP server to share the same service layer as REST routes; and enforce auth parity across both surfaces.
**Audience**: Power users and self-hosters integrating archon-search into their own tooling (scripts, n8n flows, agents, Claude Desktop).
**Status**: To Do

---

## Background
archon-search has a working REST API and an MCP server, but neither is formally contracted for external consumption: REST routes return bare `dict`/`JSONResponse` objects that bypass FastAPI's OpenAPI schema generation, the spec endpoints are behind auth, there is no SecurityScheme annotation, the MCP server has no auth and uses a completely different internal abstraction (`SearchPipeline`) than the REST routes (which do inline orchestration against `app.state`), and response shapes diverge. The result is two separate unmaintained surfaces that drift over time and cannot be depended on by external callers.

## Goal
After this feature, any external caller can: (1) fetch the OpenAPI spec without a token, see accurate schemas for every endpoint, and understand the Bearer auth requirement; (2) call all REST endpoints knowing the response shapes are stable and validated; (3) use MCP tools that return the same response shapes as REST, are authenticated for HTTP transport, and call the same internal service layer — guaranteeing parity by construction. The `SearchPipeline` is extended to be the single shared service layer for both surfaces. A BREAKING.md policy governs future compatibility commitments.

---

## Scope

### In Scope
- Exempt `/docs`, `/openapi.json`, `/redoc` from auth middleware (in addition to `/health` which is already exempt)
- Add custom OpenAPI schema with SecurityScheme (BearerAuth) and per-path security annotations for all non-health endpoints
- Add CORS middleware (always-on, `allow_origins=["*"]`)
- Shared Pydantic response schemas for all routes that currently return bare `dict` or `JSONResponse`; `ErrorDetail` schema for 401/404/409/503 error responses documented in OpenAPI `responses=`
- Type all untyped REST endpoints: `/health`, `/status`, `/indexing-state`, all `/collections/*`, `/ingest`, `/jobs/{id}` GET and DELETE
- Add `SearchPipelineResult` to pipeline return type for search (exposes `acl_filtered` flag)
- Add `namespace` parameter to `SearchPipeline.get_all_collections_meta()`, `list_documents()`, `delete_document()`
- Wire `SearchPipeline` into `create_app()` as `app.state.pipeline`, reusing existing store and embedder to avoid duplicate model loading
- Refactor `routes_search.py` to delegate to `pipeline.search()` instead of inline orchestration
- Wire real pipeline ingest into async job execution via `app.state.ingest_pipeline`
- Standardize MCP error responses from `{"error": str}` with HTTP 200 to structured `McpErrorResponse`
- Align MCP `search` tool response to `{"results": [...], "acl_filtered": bool}` (matches REST `SearchResponse`)
- Add `APIKeyMiddleware` to the FastMCP HTTP transport in `mcp.py`
- Remove `search_sync` dead stub from `archon_toolkit_search.py`
- Add `BREAKING.md` with CalVer compatibility policy
- Add OpenAPI spec snapshot test to CI

### Out of Scope
- URL versioning (`/v1/` prefix)
- Explain/debug endpoint — separate backlog item
- SDK generation from OpenAPI spec
- Admin/debug UI
- Adding MCP equivalents for REST-only endpoints (`/route`, `/indexing-state`, `/jobs/*`, `/telemetry/entries`) — these are intentional protocol differences for this iteration
- Adding REST endpoints for MCP-only tools (`search_with_context`, `get_collections_meta`, `ingest_file`, `ingest_directory`, `list_documents`, `delete_document`) — separate gap-closing iteration

---

## Acceptance criteria

> Acceptance criteria are verified in the final task. See Task 6.1 — Final verification & documentation update.

---

## What does NOT change
- Existing REST route paths and HTTP methods — no URL changes
- `SearchClient` in `archon/ai/search_client.py` — must pass all existing integration tests without modification
- MCP tool names and parameter signatures — only response shapes and internal plumbing change
- `SearchPipeline.search()` and `search_with_context()` already have `namespace` params — not changed, only the return type of `search()` is extended

---

## Known limitations / accepted trade-offs
- MCP `search` response shape is a breaking change for existing MCP consumers (raw list → `{results, acl_filtered}` dict). This is acknowledged; a `BREAKING.md` entry covers it.
- MCP auth applies only to HTTP transport. Stdio transport (Claude Desktop) is OS-process-scoped; no additional auth is needed.
- `ingest_directory`, `ingest_file`, `list_documents`, `delete_document` remain MCP-native only (no REST equivalents) in this iteration. The `/ingest` REST route is wired to `pipeline.ingest_file()` for path-based ingestion only; `documents` list ingestion stays a stub.
- CORS is always-on with `["*"]` — appropriate for a self-hosted local daemon. Not configurable in this iteration.
- REST and MCP handle "collection not found" differently: REST returns HTTP 404 with `{"detail": "..."}`, while MCP returns HTTP 200 with `{"error": "...", "code": "not_found"}`. This is intentional (MCP tools return data, not HTTP errors), but callers unifying both surfaces must handle two different error shapes and status codes.
- Error response schemas (401, 404, 409, 503) are typed via `ErrorDetail` and added to each route's `responses=` per the Phase 2 error code table. The error shapes are documented in the OpenAPI spec. Error responses are still returned as bare `JSONResponse({"detail": "..."})` at runtime — `response_model` validation is intentionally bypassed for error paths (FastAPI standard practice).
- FastAPI's 422 validation error uses `{"detail": [{...}]}` (a list), which is incompatible with `ErrorDetail(detail: str)`. 422 is intentionally excluded from the `responses=` declarations and uses FastAPI's built-in `RequestValidationError` schema instead.
- MCP HTTP auth uses `namespaces={}` — namespace-specific API tokens configured in `config.namespaces` for the REST server will silently resolve to `DEFAULT_NAMESPACE` on the MCP surface. Multi-namespace MCP auth is a future item.

---

## Architecture

### New modules / files
- `packages/archon-search/archon_search/server/schemas.py` — shared Pydantic response models for all typed REST routes (`HealthResponse`, `StatusResponse`, `StatusCollectionEntry`, `IndexingStateCollectionEntry`, `IndexingStateResponse`, `CollectionSummary`, `CollectionDetail`, `JobResponse`, `DeleteResponse`)

### Modified modules
- `archon_search/server/middleware_auth.py` — exempt path set extended to `{"/health", "/docs", "/openapi.json", "/redoc"}`; `_EXEMPT_METHOD`/`_EXEMPT_PATH` replaced with `_EXEMPT_PATHS: frozenset[str]`; `dispatch()` updated to use set membership check
- `archon_search/server/app.py` — CORS middleware, custom OpenAPI (SecurityScheme), `SearchPipeline` instantiation reusing existing store/embedder, `app.state.ingest_pipeline` set to real pipeline wrapper
- `archon_search/pipeline.py` — `SearchPipelineResult(results: list[SearchResult], acl_filtered: bool)` dataclass added; `search()` return type changed to `SearchPipelineResult` (was `list[SearchResult]`); `get_all_collections_meta(namespace)`, `list_documents(namespace)`, `delete_document(namespace)` — namespace guard added
- `archon_search/server/routes_*.py` — all untyped routes get Pydantic `response_model`; `routes_search.py` delegates to `pipeline.search()` instead of inline logic
- `archon_search/server/mcp.py` — `McpErrorResponse` used for all tool errors; `search` tool returns `SearchResponse`-compatible dict; `APIKeyMiddleware` applied to FastMCP app for HTTP transport
- `archon/ai/archon_toolkit_search.py` — `search_sync` tool and its registration removed

### New files
- `BREAKING.md` (`packages/archon-search/`) — CalVer compatibility policy and breaking-change log
- `packages/archon-search/tests/server/test_openapi_schema.py` — snapshot test for OpenAPI spec

### Data flow (after this feature)
```
External REST caller
  → auth middleware (exempt: /health, /docs, /openapi.json, /redoc)
  → typed route handler (Pydantic response model)
  → pipeline.search(namespace) | pipeline.ingest_file() | store.*
  → SearchResponse / JobResponse / CollectionDetail / etc.

MCP caller (HTTP transport)
  → APIKeyMiddleware on FastMCP app
  → mcp tool (search, ingest_file, etc.)
  → pipeline.search(namespace) | pipeline.ingest_file()
  → {"results": [...], "acl_filtered": bool} | {"error": ..., "code": ...}
```

### Key type signatures

```python
# pipeline.py
@dataclass
class SearchPipelineResult:
    results: list[SearchResult]
    acl_filtered: bool

class SearchPipeline:
    async def search(
        self, query: str, collection: str, namespace: str = DEFAULT_NAMESPACE
    ) -> SearchPipelineResult: ...  # was list[SearchResult]

    async def get_all_collections_meta(
        self, namespace: str = DEFAULT_NAMESPACE
    ) -> list[CollectionMeta]: ...  # was no namespace param; now filters by namespace

    async def list_documents(
        self, collection: str, limit: int = 100, namespace: str = DEFAULT_NAMESPACE
    ) -> list[DocumentInfo]: ...  # guards that collection belongs to namespace

    async def delete_document(
        self, doc_id: str, collection: str, namespace: str = DEFAULT_NAMESPACE
    ) -> int: ...  # guards that collection belongs to namespace

# schemas.py
class HealthResponse(BaseModel):
    status: str
    version: str

class StatusCollectionEntry(BaseModel):
    name: str; status: str; watching: bool; eta_seconds: float | None
    processed_files: int; total_files: int; error: str | None; error_count: int

class StatusResponse(BaseModel):
    running: bool; pid: int; version: str
    collections: list[StatusCollectionEntry]

class IndexingStateCollectionEntry(BaseModel):
    status: str; processed_files: int; total_files: int
    error: str | None; error_count: int
    started_at: str | None; completed_at: str | None

class IndexingStateResponse(BaseModel):
    collections: dict[str, IndexingStateCollectionEntry]
    last_updated: str | None = None
    trigger: str | None = None

class CollectionSummary(BaseModel):
    name: str; path: str; description: str
    doc_count: int; chunk_count: int; namespace: str; status: str

class CollectionDetail(CollectionSummary):
    embedding_model: str; centroid_present: bool
    last_indexed: str | None; acl_protected_count: int; acl_open_count: int

class JobResponse(BaseModel):
    job_id: str; status: str; created_at: str; updated_at: str
    result: str | None; error: str | None; namespace: str

class DeleteResponse(BaseModel):
    name: str; deleted: bool

class ErrorDetail(BaseModel):
    detail: str

# mcp.py (error helper)
class McpErrorResponse(TypedDict):
    error: str
    code: str  # "not_found" | "internal_error"
```

---

## Task breakdown

### Phase 1 — OpenAPI Foundations
> **Releasable**: after Task 1.4, the OpenAPI spec is publicly accessible, accurately annotates auth requirements on all endpoints, and all response schemas are registered. The spec is a first-class deliverable after this phase.

#### Task 1.1 — Auth middleware: exempt spec endpoints from token requirement
- [x] **File**: `packages/archon-search/archon_search/server/middleware_auth.py`
- **Depends on**: nothing
- **Description**:
  - Replace `_EXEMPT_METHOD = "GET"` and `_EXEMPT_PATH = "/health"` with `_EXEMPT_PATHS: frozenset[str] = frozenset({"/health", "/docs", "/openapi.json", "/redoc"})`
  - Update `dispatch()`: change the single-path check `if request.method == _EXEMPT_METHOD and request.url.path == _EXEMPT_PATH:` to `if request.url.path in _EXEMPT_PATHS:`
  - Remove the method restriction — health check and spec endpoints should be exempt regardless of HTTP method to avoid surprising 401s on non-GET requests to exempted paths (e.g. CORS preflight OPTIONS)
  - All other dispatch logic unchanged
- **Releasable**: after this task, unauthenticated clients can fetch `/docs` and `/openapi.json`
- **Tests (TDD)** — `packages/archon-search/tests/server/test_middleware_auth.py`:
  - Unit: `test_docs_path_exempt_without_token` — GET /docs returns 200 (not 401) when no Authorization header
  - Unit: `test_openapi_json_exempt_without_token` — GET /openapi.json returns 200 without token
  - Unit: `test_redoc_exempt_without_token` — GET /redoc returns 200 without token
  - Unit: `test_health_still_exempt` — existing test for GET /health remains green
  - Unit: `test_search_still_requires_token` — GET /search (any data endpoint) still returns 401 without token
  - Unit: `test_options_on_exempt_path_allowed` — OPTIONS /health returns not-401 (CORS preflight)
  - Unit: `test_invalid_token_returns_401` — request with `Authorization: Bearer wrong-key` returns 401
  - Unit: `test_wrong_scheme_returns_401` — request with `Authorization: Basic xxx` returns 401
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/server/test_middleware_auth.py -v`

#### Task 1.2 — Custom OpenAPI schema: SecurityScheme + per-path security annotations
- [x] **File**: `packages/archon-search/archon_search/server/app.py`
- **Depends on**: Task 1.1
- **Description**:
  - Add a `_configure_openapi(app: FastAPI) -> None` function that overrides `app.openapi` with a closure that:
    1. Calls `get_openapi(title="archon-search", version=_VERSION, description="REST API for archon-search document search and collection management", routes=app.routes)` (import `_VERSION` from `routes_health.py` pattern or via `importlib.metadata`)
    2. Adds `components.securitySchemes.BearerAuth = {"type": "http", "scheme": "bearer"}`
    3. Iterates all paths in the generated schema; for any path not in `{"/health", "/docs", "/openapi.json", "/redoc"}`, adds `security: [{"BearerAuth": []}]` to every operation
    4. Caches the result in `app.openapi_schema`
  - Call `_configure_openapi(app)` at the end of `create_app()` before returning
  - Import `get_openapi` from `fastapi.openapi.utils`
- **Releasable**: after this task, the OpenAPI spec includes SecurityScheme and all data endpoints show Bearer auth requirement
- **Tests (TDD)** — `packages/archon-search/tests/server/test_openapi_schema.py`:
  - Unit: `test_security_scheme_present` — `app.openapi()["components"]["securitySchemes"]["BearerAuth"]` equals `{"type": "http", "scheme": "bearer"}`
  - Unit: `test_health_has_no_security` — `/health` path has no `security` key in any operation
  - Unit: `test_search_has_bearer_security` — `/search` path POST operation has `security: [{"BearerAuth": []}]`
  - Unit: `test_docs_path_has_no_security` — if `/docs` appears in paths, it has no security annotation
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/server/test_openapi_schema.py -v`

#### Task 1.3 — CORS middleware
- [x] **File**: `packages/archon-search/archon_search/server/app.py`
- **Depends on**: nothing (independent of 1.1 and 1.2)
- **Description**:
  - Add `from fastapi.middleware.cors import CORSMiddleware`
  - In `create_app()`, add `app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])` after `APIKeyMiddleware`, at the end of the middleware setup in `create_app()`
  - Middleware order matters: CORS must be outer (last added = outermost in Starlette's LIFO middleware stack) so preflight OPTIONS requests return CORS headers before the auth check runs. Add CORS **after** `APIKeyMiddleware`, not before.
  - No config key — always-on for self-hosted deployments
- **Releasable**: after this task, browser-based consumers (n8n web UI, etc.) can make cross-origin requests
- **Tests (TDD)** — `packages/archon-search/tests/server/test_openapi_schema.py` (extend existing):
  - Unit: `test_cors_options_preflight_returns_headers` — OPTIONS /search returns `Access-Control-Allow-Origin: *` without requiring auth
  - Unit: `test_cors_get_health_returns_headers` — GET /health response includes `Access-Control-Allow-Origin`
  - Unit: `test_cors_preflight_to_protected_endpoint_not_blocked` — OPTIONS /search returns CORS headers (Access-Control-Allow-Origin) and NOT a 401 (auth middleware does not block preflight)
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/server/test_openapi_schema.py -v`

#### Task 1.4 — Shared REST response schemas module
- [x] **File**: `packages/archon-search/archon_search/server/schemas.py` (new)
- **Depends on**: nothing
- **Description**:
  - Define all Pydantic models that will be used as `response_model` in Phase 2 tasks:
  ```python
  from pydantic import BaseModel

  class HealthResponse(BaseModel):
      status: str      # "running"
      version: str

  class StatusCollectionEntry(BaseModel):
      name: str
      status: str
      watching: bool
      eta_seconds: float | None = None
      processed_files: int = 0
      total_files: int = 0
      error: str | None = None
      error_count: int = 0

  class StatusResponse(BaseModel):
      running: bool
      pid: int
      version: str
      collections: list[StatusCollectionEntry]

  class IndexingStateCollectionEntry(BaseModel):
      status: str
      processed_files: int = 0
      total_files: int = 0
      error: str | None = None
      error_count: int = 0
      started_at: str | None = None
      completed_at: str | None = None

  class IndexingStateResponse(BaseModel):
      collections: dict[str, IndexingStateCollectionEntry]
      last_updated: str | None = None
      trigger: str | None = None

  class CollectionSummary(BaseModel):
      name: str
      path: str
      description: str = ""
      doc_count: int = 0
      chunk_count: int = 0
      namespace: str
      status: str

  class CollectionDetail(CollectionSummary):
      embedding_model: str
      centroid_present: bool = False
      last_indexed: str | None = None
      acl_protected_count: int = 0
      acl_open_count: int = 0

  class JobResponse(BaseModel):
      job_id: str
      status: str
      created_at: str
      updated_at: str
      result: str | None = None
      error: str | None = None
      namespace: str

  class DeleteResponse(BaseModel):
      name: str
      deleted: bool

  class ErrorDetail(BaseModel):
      detail: str
  ```
  - No logic in this file — data models only
- **Releasable**: after this task, all Phase 2 tasks can import from `schemas.py`
- **Tests (TDD)** — `packages/archon-search/tests/server/test_schemas.py` (new):
  - Unit: `test_job_response_from_job_to_dict` — `JobResponse(**job_to_dict(job))` succeeds for a valid IngestJob
  - Unit: `test_collection_detail_inherits_summary_fields` — CollectionDetail includes all CollectionSummary fields
  - Unit: `test_indexing_state_response_empty_collections` — `IndexingStateResponse(collections={})` is valid
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/server/test_schemas.py -v`

---

### Phase 2 — REST Route Response Models
> **Releasable**: after this phase, every public endpoint has an accurate OpenAPI schema — no `{}` response schemas remain. The OpenAPI spec is a complete and trustworthy contract.

#### Error response schema coverage (applies to all Phase 2 tasks)
Each task below adds `response_model` to its routes. **Each task must also add `responses=` for the error codes that route can actually return**, using `ErrorDetail` from `schemas.py`. Do NOT add codes a route cannot return (e.g. `/health` never returns any error code — it is exempt from auth and has no 404 path).

| Route | Applicable error codes |
|---|---|
| `GET /health` | _(none — auth-exempt, always 200)_ |
| `GET /status` | 401 |
| `GET /indexing-state` | 401 |
| `GET /collections/` | 401 |
| `GET /collections/{name}` | 401, 404 |
| `POST /collections/` | 401, 409 |
| `POST /collections/{name}/reindex` | 401, 404 |
| `DELETE /collections/{name}` | 401, 404 |
| `POST /search` | 401, 404, 503 |
| `POST /ingest` | 401 |
| `GET /jobs/{job_id}` | 401, 404 |
| `DELETE /jobs/{job_id}` | 401, 404 |

Define a per-file constant for the applicable subset:
```python
from archon_search.server.schemas import ErrorDetail
_ERROR_401 = {401: {"model": ErrorDetail}}
_ERROR_401_404 = {401: {"model": ErrorDetail}, 404: {"model": ErrorDetail}}
# etc. — use the minimal set for each route
```

#### Task 2.1 — Type `/health` and `/status` endpoints
- [x] **File**: `packages/archon-search/archon_search/server/routes_health.py`, `packages/archon-search/archon_search/server/routes_status.py`
- **Depends on**: Task 1.4
- **Description**:
  - `routes_health.py`: change `async def health() -> dict:` to `async def health() -> HealthResponse:` with `response_model=HealthResponse`; return `HealthResponse(status="running", version=_VERSION)` instead of bare dict
  - `routes_status.py`: change `async def status(request: Request) -> dict:` to `async def status(...) -> StatusResponse:` with `response_model=StatusResponse`; construct and return `StatusResponse(running=True, pid=pid, version=_VERSION, collections=[StatusCollectionEntry(**entry) for entry in collection_entries])` instead of building a raw dict. Ensure the collection entry dict fields map exactly to `StatusCollectionEntry` fields.
  - Both routes already return correct data — only the return type and response_model annotation change
- **Releasable**: after this task, `/health` and `/status` have accurate schemas in the OpenAPI spec
- **Tests (TDD)** — `packages/archon-search/tests/server/test_openapi_schema.py`:
  - Unit: `test_health_response_schema_in_spec` — `/health` GET operation 200 response schema matches `HealthResponse` shape (has `status` and `version` string properties)
  - Unit: `test_status_response_schema_in_spec` — `/status` GET 200 response schema has `running`, `pid`, `version`, `collections` array
  - Integration: `test_health_endpoint_returns_typed_response` — GET /health returns JSON with `status` and `version` keys matching `HealthResponse` schema
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/server/test_openapi_schema.py -v`

#### Task 2.2 — Type `/indexing-state` endpoint
- [ ] **File**: `packages/archon-search/archon_search/server/routes_state.py`
- **Depends on**: Task 1.4
- **Description**:
  - Change `async def indexing_state(request: Request) -> dict:` to return `IndexingStateResponse`
  - When `state is None`, return `IndexingStateResponse(collections={}, last_updated=None, trigger=None)`
  - Build `IndexingStateResponse(collections={name: IndexingStateCollectionEntry(**{k: v for k,v in col.items() if k in _COLLECTION_API_FIELDS}) for name, col in raw["collections"].items() if name in ns_names}, last_updated=raw.get("last_updated"), trigger=raw.get("trigger"))`
  - Add `response_model=IndexingStateResponse` to the route decorator
  - `_COLLECTION_API_FIELDS` already restricts to the correct field subset — use it to filter before constructing `IndexingStateCollectionEntry`
- **Releasable**: after this task, `/indexing-state` has an accurate schema
- **Tests (TDD)** — `packages/archon-search/tests/server/test_openapi_schema.py`:
  - Unit: `test_indexing_state_schema_in_spec` — `/indexing-state` GET 200 response schema has `collections` object with `IndexingStateCollectionEntry`-shaped additionalProperties
  - Unit: `test_indexing_state_empty_when_no_state_file` — endpoint returns `{"collections": {}}` when state store has no data
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/server/test_openapi_schema.py -v`

#### Task 2.3 — Type `/collections/` GET (list) endpoint
- [ ] **File**: `packages/archon-search/archon_search/server/routes_collections.py`
- **Depends on**: Task 1.4
- **Description**:
  - Change `list_collections` to `response_model=list[CollectionSummary]`
  - Build and return `[CollectionSummary(**entry) for entry in result]` instead of `JSONResponse(content=result)`
  - Ensure `CollectionSummary` field names match the dict keys built in the existing handler (they do: `name`, `path`, `description`, `doc_count`, `chunk_count`, `namespace`, `status`)
- **Releasable**: after this task, `GET /collections/` has an accurate schema
- **Tests (TDD)** — `packages/archon-search/tests/server/test_openapi_schema.py`:
  - Unit: `test_collections_list_schema_in_spec` — `/collections/` GET 200 schema is an array of `CollectionSummary`-shaped objects
  - Integration: `test_list_collections_returns_typed_list` — GET /collections/ returns a JSON array where each item has all CollectionSummary fields
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/server/test_openapi_schema.py -v`

#### Task 2.4 — Type `/collections/{name}` GET (detail) endpoint
- [ ] **File**: `packages/archon-search/archon_search/server/routes_collections.py`
- **Depends on**: Task 1.4
- **Description**:
  - Change `get_collection_info` to `response_model=CollectionDetail`
  - Build and return `CollectionDetail(**data)` instead of `JSONResponse(content=data)`
  - Ensure all keys in `data` dict match `CollectionDetail` field names (they do: adds `embedding_model`, `centroid_present`, `last_indexed`, `acl_protected_count`, `acl_open_count` to the base summary fields)
- **Releasable**: after this task, `GET /collections/{name}` has an accurate schema
- **Tests (TDD)** — `packages/archon-search/tests/server/test_openapi_schema.py`:
  - Unit: `test_collection_detail_schema_in_spec` — `/collections/{name}` GET 200 schema includes `acl_protected_count` and `embedding_model` fields
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/server/test_openapi_schema.py -v`

#### Task 2.5 — Type `/collections/` POST, `/collections/{name}/reindex`, `/ingest` (job-returning routes)
- [ ] **File**: `packages/archon-search/archon_search/server/routes_collections.py`, `packages/archon-search/archon_search/server/routes_jobs.py`
- **Depends on**: Task 1.4
- **Description**:
  - All three endpoints return `job_to_dict(job)` — change each to return `JobResponse(**job_to_dict(job))` with `response_model=JobResponse, status_code=202`
  - `add_collection` in `routes_collections.py`: change `return JSONResponse(content=job_to_dict(job), status_code=202)` → `return JobResponse(**job_to_dict(job))`; add `response_model=JobResponse, status_code=202` to decorator
  - `reindex_collection`: same change
  - `ingest` in `routes_jobs.py`: same change
  - The 409/500 error returns in `add_collection` that use `JSONResponse({"detail": ...})` stay as-is (FastAPI handles these as non-schema HTTP errors)
- **Releasable**: after this task, all job-returning endpoints have accurate schemas and 202 status is correctly documented
- **Tests (TDD)** — `packages/archon-search/tests/server/test_openapi_schema.py`:
  - Unit: `test_add_collection_202_schema_in_spec` — `POST /collections/` 202 response schema matches `JobResponse` shape
  - Unit: `test_ingest_202_schema_in_spec` — `POST /ingest` 202 response schema matches `JobResponse`
  - Unit: `test_reindex_202_schema_in_spec` — `POST /collections/{name}/reindex` 202 response schema matches `JobResponse`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/server/test_openapi_schema.py -v`

#### Task 2.6 — Type `/collections/{name}` DELETE and `/jobs/{id}` GET/DELETE
- [ ] **File**: `packages/archon-search/archon_search/server/routes_collections.py`, `packages/archon-search/archon_search/server/routes_jobs.py`
- **Depends on**: Task 1.4
- **Description**:
  - `remove_collection` in `routes_collections.py`: change `return JSONResponse(content={"name": name, "deleted": True})` → `return DeleteResponse(name=name, deleted=True)`; add `response_model=DeleteResponse`
  - `get_job` in `routes_jobs.py`: change `return JSONResponse(content=job_to_dict(job))` → `return JobResponse(**job_to_dict(job))`; add `response_model=JobResponse`
  - `delete_job` in `routes_jobs.py`: change all `return JSONResponse(content=job_to_dict(job), status_code=...)` → `return JobResponse(**job_to_dict(job))`; add `response_model=JobResponse`
  - `delete_job` has multiple return paths with different status codes (200 for terminal jobs, 202 for in-progress jobs being cancelled). Use `responses={200: {"model": JobResponse}, 202: {"model": JobResponse}}` in the route decorator and return `JobResponse(**job_to_dict(job))` with an explicit `status_code` parameter where needed (e.g. `Response(content=..., status_code=202)` or FastAPI's `JSONResponse`). This makes both status codes visible in the OpenAPI spec.
  - **Error response schemas**: for the routes in this task, apply the `responses=` subsets per the Phase 2 error code table above: `remove_collection` → 401/404; `get_job` → 401/404; `delete_job` → 401/404. Tasks 2.1–2.5 apply `responses=` to their own routes per the same table.
- **Releasable**: after this task, all remaining untyped REST endpoints have accurate OpenAPI schemas and all error shapes are documented — Phase 2 complete
- **Tests (TDD)** — `packages/archon-search/tests/server/test_openapi_schema.py`:
  - Unit: `test_delete_collection_schema_in_spec` — `DELETE /collections/{name}` 200 schema has `name` and `deleted` fields
  - Unit: `test_get_job_schema_in_spec` — `GET /jobs/{job_id}` 200 schema matches `JobResponse`
  - Unit: `test_no_empty_schemas_remain` — iterate all paths/operations in `app.openapi()`; assert no `200` response schema is `{}` or missing
  - Unit: `test_error_schemas_documented` — for endpoints that can 404 (e.g. `/collections/{name}` GET), the OpenAPI spec includes a `404` response entry with `ErrorDetail`-shaped schema (`detail: string`)
  - Integration: `test_404_runtime_response_matches_error_detail` — GET /collections/nonexistent returns HTTP 404 with body `{"detail": "<string>"}` (not a list, not `{"error": ...}`)
  - Unit: `test_delete_active_job_returns_202` — DELETE of an in-progress job returns 202 with `JobResponse` body
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/server/test_openapi_schema.py -v`

---

### Phase 3 — SearchPipeline Service Layer
> **Releasable**: after Task 3.4, REST search delegates to the shared pipeline — the search surface is unified. After Task 3.5, real ingest is wired. Full releasability after Task 3.5.

#### Task 3.1 — Add `SearchPipelineResult` and update `search()` return type
- [ ] **File**: `packages/archon-search/archon_search/pipeline.py`
- **Depends on**: nothing
- **Description**:
  - Add at module level (after imports, before `SearchPipeline` class):
    ```python
    from dataclasses import dataclass

    @dataclass
    class SearchPipelineResult:
        results: list[SearchResult]
        acl_filtered: bool
    ```
  - Change `search()` return type from `list[SearchResult]` to `SearchPipelineResult`
  - Change the last line of `search()` from:
    ```python
    candidates, _ = apply_acl_filter(candidates, lambda r: r.acl, namespace)
    return await self._reranker.rerank(query, candidates, top_k=self._top_k_return)
    ```
    to:
    ```python
    candidates, acl_filtered = apply_acl_filter(candidates, lambda r: r.acl, namespace)
    results = await self._reranker.rerank(query, candidates, top_k=self._top_k_return)
    return SearchPipelineResult(results=results, acl_filtered=acl_filtered)
    ```
  - Export `SearchPipelineResult` from `archon_search/_types.py` or keep in `pipeline.py` (keep in `pipeline.py` — it's a pipeline implementation detail)
  - `search_with_context()` calls `self.search()` internally — update it to use `result.results` instead of treating return as a list:
    ```python
    # In pipeline.py search_with_context():
    # Change:  for result in results:
    # To:      result_obj = await self.search(query, collection, namespace=namespace)
    #          for result in result_obj.results:
    ```
- **Releasable**: after this task, callers that need `acl_filtered` can get it from the pipeline
- **Tests (TDD)** — `packages/archon-search/tests/test_pipeline.py` (extend existing or new):
  - Unit: `test_search_returns_pipeline_result` — `pipeline.search()` returns `SearchPipelineResult` instance
  - Unit: `test_search_acl_filtered_true_when_chunks_filtered` — when ACL filter removes candidates, `acl_filtered=True`
  - Unit: `test_search_acl_filtered_false_when_all_pass` — when no ACL filtering occurs, `acl_filtered=False`
  - Unit: `test_search_with_context_still_works_after_type_change` — `search_with_context()` returns list of dicts with `result`, `context_before`, `context_after` keys
  - Unit: `test_mcp_search_with_context_tool_still_works` — MCP `search_with_context` tool (in `mcp.py`) returns list of context dicts after pipeline type change
  - **Existing tests to update**: `packages/archon-search/tests/server/test_mcp_telemetry.py` — all `_make_pipeline()` mock factories return `list[SearchResult]`; update to return `SearchPipelineResult(results=..., acl_filtered=False)`. Also `test_search_tool_does_not_log_when_writer_none` asserts `isinstance(output, list)` — update assertion to check `isinstance(output, dict)` with `"results"` key.
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_pipeline.py -v`

#### Task 3.2 — Add `namespace` guard to `get_all_collections_meta`, `list_documents`, `delete_document`
- [ ] **File**: `packages/archon-search/archon_search/pipeline.py`
- **Depends on**: nothing (independent of 3.1)
- **Description**:
  - `get_all_collections_meta(namespace: str = DEFAULT_NAMESPACE) -> list[CollectionMeta]`:
    - Call `await self.store.get_all_collections_meta()` then filter: `return [m for m in all_meta if m.namespace == namespace]`
  - `list_documents(collection: str, limit: int = 100, namespace: str = DEFAULT_NAMESPACE) -> list[DocumentInfo]`:
    - Before delegating to store, verify collection belongs to namespace: `meta = await self.store.get_collection_meta(collection, namespace=namespace); if meta is None: return []`
    - Then `return await self.store.list_documents(collection, limit)`
  - `delete_document(doc_id: str, collection: str, namespace: str = DEFAULT_NAMESPACE) -> int`:
    - Verify collection namespace: `meta = await self.store.get_collection_meta(collection, namespace=namespace); if meta is None: raise ValueError(f"collection {collection!r} not found in namespace {namespace!r}")`
    - Then `return await self.store.delete_document(collection, doc_id)`
  - MCP callers currently pass no namespace → they get `DEFAULT_NAMESPACE` — backward compatible
- **Releasable**: after this task, MCP and REST can safely use these methods with namespace isolation
- **Tests (TDD)** — `packages/archon-search/tests/test_pipeline.py`:
  - Unit: `test_get_all_collections_meta_filters_by_namespace` — with two collections in different namespaces, only the matching namespace's collections returned
  - Unit: `test_list_documents_wrong_namespace_returns_empty` — listing documents for a collection in a different namespace returns `[]`
  - Unit: `test_delete_document_wrong_namespace_raises` — deleting from a collection in a wrong namespace raises `ValueError`
  - Unit: `test_delete_document_correct_namespace_succeeds` — deleting from correct namespace delegates to store
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_pipeline.py -v`

#### Task 3.3 — Wire `SearchPipeline` into `create_app()`
- [x] **File**: `packages/archon-search/archon_search/server/app.py`
- **Depends on**: Task 3.1, Task 3.2
- **Description**:
  - Import: `from archon_search.pipeline import SearchPipeline`
  - After `app.state.embedder` is set, construct and store the pipeline — reusing the same store and embedder (no duplicate ML model loading):
    ```python
    from archon_search.pipeline import SearchPipeline
    from archon_search.reranker import ModelReranker, Reranker
    from archon_search.chunker import DocumentChunker
    from archon_search.parser import DocumentParser

    reranker = Reranker(ModelReranker(config.reranker_model, providers=config.providers or None))
    chunker = DocumentChunker(config.chunk_size)
    parser = DocumentParser()
    app.state.pipeline = SearchPipeline(
        store=app.state.search_store,
        embedder=app.state.embedder,
        reranker=reranker,
        chunker=chunker,
        parser=parser,
        top_k_retrieve=config.top_k_retrieve,
        top_k_return=config.top_k_return,
    )
    ```
  - Note: we do NOT use `create_pipeline()` here because that factory always creates a new `SearchStore` internally, which would defeat the goal of sharing the same store and embedder instances with `app.state`. `SearchPipeline` is constructed directly to allow dependency injection of the existing `app.state.search_store` and `app.state.embedder`.
  - The pipeline shares the same `SearchStore` instance — `store.connect()` in the lifespan is called on the shared store, so the pipeline's store is already connected at startup
- **Releasable**: after this task, `app.state.pipeline` is available for all route handlers
- **Tests (TDD)** — `packages/archon-search/tests/server/test_lifespan_telemetry.py` (extend) or new `tests/server/test_app_factory.py`:
  - Unit: `test_create_app_has_pipeline_in_state` — `create_app(config, job_store)` results in `app.state.pipeline` being a `SearchPipeline` instance
  - Unit: `test_pipeline_shares_store_with_app_state` — `app.state.pipeline.store is app.state.search_store`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/server/test_app_factory.py -v`

#### Task 3.4 — Refactor `routes_search.py` to delegate to `pipeline.search()`
- [ ] **File**: `packages/archon-search/archon_search/server/routes_search.py`
- **Depends on**: Task 3.1, Task 3.3
- **Description**:
  - Remove imports: `from archon_search.reranker import ModelReranker, Reranker`, `from archon_search.store import SearchStore`
  - Replace the inline search logic in `search()` handler with:
    ```python
    pipeline = request.app.state.pipeline
    ns = request.state.namespace
    try:
        meta = await pipeline.get_collection_meta(body.collection, namespace=ns)
    except Exception as exc:
        logger.error("search: meta lookup failed for collection %r: %s", body.collection, exc, exc_info=True)
        return JSONResponse({"detail": "service unavailable"}, status_code=503)
    if meta is None:
        return JSONResponse({"detail": "collection not found"}, status_code=404)
    try:
        result = await pipeline.search(body.query, body.collection, namespace=ns)
        return SearchResponse(
            results=[SearchResultSchema.from_result(r) for r in result.results],
            acl_filtered=result.acl_filtered,
        )
    except Exception as exc:
        logger.warning("search failed for collection %r: %s", body.collection, exc, exc_info=True)
        return SearchResponse(results=[], acl_filtered=False)
    ```
  - **Note on `top_k` regression**: the current `routes_search.py` uses `body.top_k` for per-request `top_k` control. Delegating to `pipeline.search()` loses this — `pipeline._top_k_return` (config-level) replaces it. This is a behavioral change that must be documented in `BREAKING.md`. The existing test `test_search_top_k_forwarded` in `tests/server/test_routes_search.py` must be updated or removed to reflect the new behavior. **Keep the `top_k` field in `SearchRequest`** — removing it would be a second breaking change for clients that currently send it. The field is accepted but silently ignored; the BREAKING.md entry documents this.
  - `SearchRequest`, `SearchResultSchema`, `SearchResponse` classes are unchanged
- **Releasable**: after this task, REST search goes through the shared pipeline — inline logic eliminated
- **Tests (TDD)** — `packages/archon-search/tests/server/test_routes_search.py`:
  - Unit: `test_search_uses_pipeline_not_inline_logic` — mock `app.state.pipeline.search` and verify it's called (not `app.state.embedder.embed_one` directly)
  - Unit: `test_search_passes_namespace_to_pipeline` — verify `pipeline.search(namespace=ns)` is called with the request namespace
  - Unit: `test_search_returns_acl_filtered_flag` — when pipeline returns `acl_filtered=True`, response has `acl_filtered: true`
  - Unit: `test_search_collection_not_found_returns_404` — when `get_collection_meta` returns None, 404 is returned
  - Unit: `test_search_pipeline_error_returns_empty` — when `pipeline.search()` raises, returns `SearchResponse(results=[], acl_filtered=False)`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/server/test_routes_search.py -v`

#### Task 3.5 — Wire real pipeline ingest into async job execution
- [x] **File**: `packages/archon-search/archon_search/server/app.py`, `packages/archon-search/archon_search/server/routes_jobs.py`
- **Depends on**: Task 3.3
- **Description**:
  - In `app.py`, add a factory that creates the `ingest_pipeline` callable for the job executor:
    ```python
    def _make_ingest_pipeline_fn(pipeline: SearchPipeline):
        async def _ingest_fn(job_id: str, store: JobStore, body: IngestRequest, namespace: str = DEFAULT_NAMESPACE) -> None:
            if body.path:
                path = Path(body.path).expanduser().resolve()
                # Namespace guard: verify collection belongs to namespace before ingesting.
                # Consistent with routes_search.py pattern (get_collection_meta guard).
                meta = await pipeline.get_collection_meta(body.collection, namespace=namespace)
                if meta is None:
                    raise ValueError(f"collection {body.collection!r} not found in namespace {namespace!r}")
                if path.is_dir():
                    await pipeline.ingest_directory(path, body.collection, namespace=namespace)
                else:
                    await pipeline.ingest_file(path, body.collection)
            # body.documents list ingestion stays a stub for this iteration
        return _ingest_fn
    ```
  - **Why the guard is in `_ingest_fn`, not in `pipeline.ingest_file()`**: `ingest_file()` is a low-level operation — namespace is a collection-metadata attribute, not stored per-chunk. Adding a namespace parameter to `ingest_file()` would be inert (no store operation uses it at the chunk level). The guard pattern (verify collection belongs to namespace, then proceed) is applied at the service boundary, same as in `routes_search.py`.
  - After `app.state.pipeline` is set in `create_app()`, add: `app.state.ingest_pipeline = _make_ingest_pipeline_fn(app.state.pipeline)`
  - The existing `_default_ingest_task` in `routes_jobs.py` already reads `getattr(request.app.state, "ingest_pipeline", None)` — no change needed there; the pipeline function is now set rather than None
  - **Important**: `add_collection` and `reindex_collection` in `routes_collections.py` also create ingest tasks but do not pass `app.state.ingest_pipeline` as the `pipeline_fn`. Add these two lines before each `asyncio.create_task(...)` call in those handlers (same pattern as `routes_jobs.py` line 90-91):
    ```python
    pipeline_fn = getattr(request.app.state, "ingest_pipeline", None)
    task = asyncio.create_task(
        _default_ingest_task(job.job_id, store, ingest_body, namespace=ns, pipeline_fn=pipeline_fn)
    )
    ```
    Add the following test:
    - Unit: `test_add_collection_ingest_calls_pipeline` — after `add_collection` creates a job, the ingest task calls `pipeline.ingest_directory()` (not a stub)
- **Releasable**: after this task, `POST /ingest` with a path triggers real indexing via the pipeline, not a no-op stub
- **Tests (TDD)** — `packages/archon-search/tests/server/test_routes_jobs.py` (new or extend):
  - Unit: `test_ingest_pipeline_fn_set_on_app_state` — `create_app()` results in `app.state.ingest_pipeline` being callable
  - Unit: `test_ingest_job_calls_pipeline_for_path` — POST /ingest with `path` results in `pipeline.ingest_file()` being called
  - Unit: `test_ingest_job_calls_ingest_directory_for_dir_path` — POST /ingest with a directory path calls `pipeline.ingest_directory()`
  - Unit: `test_ingest_job_with_no_path_is_noop` — POST /ingest with `documents` only still returns 202 (stub path, no exception)
  - Unit: `test_ingest_fn_rejects_wrong_namespace` — `_ingest_fn` raises `ValueError` when `pipeline.get_collection_meta` returns `None` (collection not in namespace)
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/server/test_routes_jobs.py -v`

---

### Phase 4 — MCP Modernization
> **Releasable**: after Task 4.3, the MCP server is auth-enforced for HTTP transport, errors are structured, and search response shape matches REST. Full MCP parity for the primary surfaces delivered.

#### Task 4.1 — Standardize MCP error responses to structured shape
- [ ] **File**: `packages/archon-search/archon_search/server/mcp.py`
- **Depends on**: nothing
- **Description**:
  - Add a `TypedDict` for structured error responses:
    ```python
    from typing import TypedDict

    class McpErrorResponse(TypedDict):
        error: str
        code: str
    ```
  - Replace all `return {"error": str(exc)}` occurrences with `return McpErrorResponse(error=str(exc), code="internal_error")`
  - Replace `return {"error": f"Collection {name!r} not found"}` with `return McpErrorResponse(error=f"Collection {name!r} not found", code="not_found")`
  - Return type of each tool changes from `list[dict[str, Any]] | dict[str, Any]` — keep as-is (Python dicts are compatible with TypedDict return)
  - All 9 tools updated: `search`, `search_with_context`, `ingest_file`, `ingest_directory`, `list_collections`, `get_collections_meta`, `get_collection_meta`, `list_documents`, `delete_document`
- **Releasable**: after this task, MCP error responses have a consistent `{"error": str, "code": str}` shape that callers can type-check
- **Tests (TDD)** — `packages/archon-search/tests/server/test_mcp_error_responses.py` (new):
  - Unit: `test_search_error_returns_structured_error` — when `pipeline.search()` raises, tool returns dict with `error` and `code` keys
  - Unit: `test_get_collection_meta_not_found_has_not_found_code` — returns `code="not_found"` when collection missing
  - Unit: `test_ingest_file_error_has_internal_error_code` — returns `code="internal_error"` on exception
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/server/test_mcp_error_responses.py -v`

#### Task 4.2 — Align MCP `search` tool response to REST `SearchResponse` shape
- [ ] **File**: `packages/archon-search/archon_search/server/mcp.py`
- **Depends on**: Task 3.1 (SearchPipelineResult), Task 4.1
- **Description**:
  - Change `search` tool return type from `list[dict[str, Any]]` to `dict[str, Any]` (matches `SearchResponse` JSON shape)
  - Update the success return from `return [asdict(r) for r in results]` to:
    ```python
    result = await pipeline.search(query, collection or default_collection)
    return {
        "results": [asdict(r) for r in result.results],
        "acl_filtered": result.acl_filtered,
    }
    ```
  - Update the error return from `return [{"error": str(exc)}]` (list) to `return McpErrorResponse(error=str(exc), code="internal_error")` (dict)
  - **This is a breaking change** for existing MCP consumers who iterate the return value as a list. The `BREAKING.md` entry in Task 5.2 covers this.
  - Telemetry code uses `r.doc_id` — update to `result.results[i].doc_id` pattern
- **Releasable**: after this task, MCP `search` returns `{"results": [...], "acl_filtered": bool}` identical to REST
- **Tests (TDD)** — `packages/archon-search/tests/server/test_mcp_telemetry.py` (extend):
  - Unit: `test_mcp_search_returns_results_and_acl_filtered` — tool returns dict with `results` list and `acl_filtered` bool
  - Unit: `test_mcp_search_acl_filtered_propagated` — when pipeline returns `acl_filtered=True`, MCP response has `acl_filtered: true`
  - Unit: `test_mcp_search_error_returns_dict_not_list` — error response is a dict, not a list
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/server/test_mcp_telemetry.py -v`

#### Task 4.3 — Add `APIKeyMiddleware` to FastMCP HTTP transport
- [ ] **File**: `packages/archon-search/archon_search/server/mcp.py`
- **Depends on**: Task 1.1 (exempt paths pattern)
- **Description**:
  - In `create_app()`, add auth middleware to the FastMCP app after construction:
    ```python
    from archon_search.key_manager import load_or_generate_key
    from archon_search.server.middleware_auth import APIKeyMiddleware

    api_key, _ = load_or_generate_key()
    app.add_middleware(APIKeyMiddleware, api_key=api_key, namespaces={})
    ```
  - The FastMCP app is a Starlette app — `add_middleware` works the same way
  - The `/health` custom route is already exempt per `_EXEMPT_PATHS` in the middleware
  - MCP tool endpoints are at `/mcp` (FastMCP default) — these will require auth
  - `namespaces={}` — the MCP server operates under `DEFAULT_NAMESPACE` for this iteration; multi-namespace MCP is a future item
  - Stdio transport: `APIKeyMiddleware` is a Starlette middleware on the HTTP layer and has no effect on stdio transport — stdio callers are unaffected
- **Releasable**: after this task, HTTP MCP connections require a valid Bearer token; stdio connections unchanged
- **Tests (TDD)** — `packages/archon-search/tests/server/test_mcp_auth.py` (new):
  - Unit: `test_mcp_http_rejects_unauthenticated_connection` — HTTP request to `/mcp` without Bearer token returns 401
  - Unit: `test_mcp_http_accepts_valid_token` — HTTP request to `/mcp` with valid token proceeds to tool dispatch
  - Unit: `test_mcp_health_exempt_from_auth` — GET /health on MCP app returns 200 without token
  - Unit: `test_mcp_wrong_token_returns_401` — HTTP request to `/mcp` with wrong token returns 401
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/server/test_mcp_auth.py -v`

---

### Phase 5 — Contracts & Housekeeping
> **Releasable**: after this phase, all acceptance criteria are met — `search_sync` removed, BREAKING.md in place, snapshot test in CI.

#### Task 5.1 — Remove `search_sync` dead stub from `archon_toolkit_search.py`
- [ ] **File**: `archon/ai/archon_toolkit_search.py`
- **Depends on**: nothing
- **Description**:
  - Remove the `search_sync` tool registration block entirely — the function definition and its `@toolkit.tool()` or equivalent registration call
  - Verify no other file in `archon/` imports or references `search_sync` (grep: `search_sync`)
  - If `search_sync` appears in any test or CLAUDE.md description, remove those references too
- **Releasable**: after this task, `search_sync` is no longer advertised as an available MCP tool
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_search.py` (new or extend):
  - Unit: `test_search_sync_not_in_registered_tools` — `ArchonToolkit` tool list does not include `search_sync`
  - Unit: `test_remaining_search_tools_still_registered` — `search_status`, `search_ingest`, `search_collection_list`, etc. are still registered
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_search.py -v`

#### Task 5.2 — Add `BREAKING.md` with CalVer compatibility policy
- [ ] **File**: `BREAKING.md` (project root of archon-search package: `packages/archon-search/BREAKING.md`)
- **Depends on**: nothing
- **Description**:
  - Create `BREAKING.md` with the following content structure:
    ```markdown
    # BREAKING CHANGES

    ## Compatibility Policy
    archon-search uses CalVer (`YY.M.<commit-count>`). CalVer segments encode **time only** —
    they do not signal compatibility. This file IS the compatibility contract.

    **Rule**: every release that removes or changes an existing API contract MUST add an entry
    here describing: what changed, the migration path, and from which release the deprecated
    form was announced. Consumers should subscribe to changes in this file, not interpret
    CalVer segments.

    ## Changelog

    ### [next release] — MCP `search` tool response shape
    **Surface**: MCP (`mcp.py` `search` tool)
    **Change**: `search` tool now returns `{"results": [...], "acl_filtered": bool}` instead
    of `[{...}, {...}]` (bare list of result dicts).
    **Migration**: Update consumers to access `response["results"]` instead of iterating the
    response directly. `response["acl_filtered"]` provides the ACL filter flag previously
    unavailable on the MCP surface.
    **Announced in**: this release (no prior deprecation period — the old shape was never
    documented as stable).

    ### [next release] — REST `/search` per-request `top_k` no longer honored
    **Surface**: REST (`/search` POST)
    **Change**: The `top_k` field in `SearchRequest` is now ignored at the route level; the pipeline uses
    `config.top_k_return` instead. Previously, each request could specify its own `top_k`.
    **Migration**: Configure `[search] top_k_return` in `archon-search.toml` to set the desired result count.
    **Announced in**: this release (the behavior was supported but never documented as stable).
    ```
- **Releasable**: after this task, breaking change policy is documented
- **Tests (TDD)**: N/A — documentation file
- **Checkpoint**: manually verify `BREAKING.md` exists at `packages/archon-search/BREAKING.md` and contains the policy section and MCP search entry

#### Task 5.3 — OpenAPI spec snapshot test
- [ ] **File**: `packages/archon-search/tests/server/test_openapi_snapshot.py` (new)
- **Depends on**: Task 1.2, all Phase 2 tasks
- **Description**:
  - Create a snapshot test that: (a) generates the OpenAPI spec from a test app instance, (b) serializes it to JSON, (c) compares against a committed baseline file `tests/server/openapi_snapshot.json`
  - On first run (no baseline): write the snapshot and pass
  - On subsequent runs: fail if the spec differs from the baseline (requiring explicit snapshot update when the contract changes intentionally)
  - Implementation using plain file comparison:
    ```python
    import json
    import pytest
    from pathlib import Path
    from archon_search.server.app import create_app
    from archon_search.jobs.store import JobStore

    SNAPSHOT_PATH = Path(__file__).parent / "openapi_snapshot.json"
    UPDATE_FLAG = "--update-openapi-snapshot"

    def test_openapi_spec_matches_snapshot(app_config, pytestconfig):
        app = create_app(app_config, JobStore())
        spec = app.openapi()
        if not SNAPSHOT_PATH.exists():
            pytest.fail(
                f"OpenAPI snapshot missing. Run with {UPDATE_FLAG} to generate: "
                f"uv run pytest tests/server/test_openapi_snapshot.py {UPDATE_FLAG}"
            )
        if getattr(pytestconfig, "getoption", lambda x, default=None: default)(UPDATE_FLAG, default=False):
            SNAPSHOT_PATH.write_text(json.dumps(spec, indent=2, sort_keys=True))
            return
        baseline = json.loads(SNAPSHOT_PATH.read_text())
        assert spec == baseline, (
            f"OpenAPI spec changed. If intentional, regenerate with: "
            f"uv run pytest tests/server/test_openapi_snapshot.py {UPDATE_FLAG}"
        )
    ```
  - Commit `tests/server/openapi_snapshot.json` as part of this task (generated from the completed Phase 1–2 state). The snapshot file is required for CI to pass.
  - **conftest.py requirement**: The `--update-openapi-snapshot` flag must be registered in `packages/archon-search/tests/conftest.py` (or the test directory's conftest):
    ```python
    def pytest_addoption(parser):
        parser.addoption("--update-openapi-snapshot", action="store_true", default=False,
                         help="Regenerate the OpenAPI spec snapshot baseline")
    ```
    Add this to the existing `conftest.py` if it exists, or create it.
- **Releasable**: after this task, unintended contract changes are caught at CI time
- **Tests (TDD)** — self-describing (the test IS the snapshot test):
  - Unit: `test_openapi_spec_matches_snapshot` — spec equals committed baseline
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/server/test_openapi_snapshot.py -v`

---

### Phase 6 — Verification & Documentation

#### Task 6.1 — Final verification & documentation update
- [ ] **File**: N/A (agent task)
- **Depends on**: all prior tasks
- **Description**:
  - Spawn an agent to discover all documentation in the project (CLAUDE.md, README.md, ADRs, Architecture docs, UserManual, archon-search README, `Documentation/Backlog/stable-external-apis-rest-mcp-brief.md`) and update every file whose content is affected by this feature. Specifically:
    - CLAUDE.md `archon/ai/archon_toolkit_search.py` section: remove `search_sync` from the tool list
    - CLAUDE.md `search_client.py` section: note that the OpenAPI spec is now the authoritative contract
    - archon-search `README.md` (if exists): add API documentation section pointing to `/docs`
    - Move `Documentation/Backlog/stable-external-apis-rest-mcp-brief.md` to `Documentation/Completed/`
    - The agent must not touch unrelated files
  - Verify all acceptance criteria below are met before marking this task complete
- **Releasable**: after this task, the feature is fully verified and all documentation reflects the delivered implementation
- **Acceptance criteria** (must all pass):
  - `GET /docs`, `GET /openapi.json`, and `GET /redoc` return HTTP 200 without an Authorization header
  - `GET /search` without a Bearer token returns HTTP 401
  - The OpenAPI spec includes `components.securitySchemes.BearerAuth` with `type: http, scheme: bearer`
  - Every endpoint except `/health`, `/docs`, `/openapi.json`, `/redoc` has `security: [{BearerAuth: []}]` in the spec
  - No endpoint in the spec has an empty `{}` response schema for its 200 response
  - `GET /health` returns `{"status": "running", "version": "<version>"}` (matches `HealthResponse`)
  - `POST /search` response includes `acl_filtered: bool` field
  - MCP `search` tool returns `{"results": [...], "acl_filtered": bool}` (not a bare list)
  - MCP `search` tool errors return `{"error": str, "code": str}` (not `{"error": str}` alone)
  - HTTP MCP connections without Bearer token receive 401
  - `search_sync` is not in the list of tools returned by `ArchonToolkit`
  - `BREAKING.md` exists at `packages/archon-search/BREAKING.md` with policy section, MCP `search` tool response shape entry, and REST `/search` per-request `top_k` regression entry
  - `packages/archon-search/tests/server/openapi_snapshot.json` exists and `test_openapi_spec_matches_snapshot` passes
  - `uv run pytest` in `archon/` passes with no regressions
  - `cd packages/archon-search && uv run pytest` passes with no regressions
- **Tests (TDD)**: N/A — verification and documentation task
- **Checkpoint**: manually confirm every acceptance criterion above is checked
