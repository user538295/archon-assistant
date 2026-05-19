# Feature Brief: Stable External APIs — REST + MCP

## Problem
Power users and self-hosters who deploy archon-search standalone have no formal API contract to build against. Routes exist but are undocumented, unversioned, and explicitly designated as internal integration boundaries — making any external integration fragile by design.

## Goal
Publish a versioned OpenAPI specification for the existing REST API and modernize the MCP server so that both surfaces share the same internal service layer. External callers (scripts, n8n flows, other agents) can integrate with confidence that the contract is stable and intentional.

## Users & Context
Power users and self-hosters who deploy archon-search as a standalone service and want to integrate it into their own tooling — automation pipelines, custom agents, or CLI scripts. They arrive after the service is running and authenticated, looking for a reliable, documented interface to build against.

## Core Flow
1. User deploys archon-search and obtains an API key.
2. User fetches the OpenAPI spec from `/openapi.json` (or Swagger UI at `/docs`, if enabled) to understand available endpoints, schemas, and authentication requirements.
3. User calls search, collection, job, or health endpoints from their external tool using the published contract.
4. User integrates MCP tools (via Claude Desktop or another MCP client) for Claude-native workflows; tools behave identically to their REST counterparts.
5. When archon-search updates, the CalVer version bump signals whether the change is breaking — the spec documents what changed.

## In Scope
- Generate and serve the OpenAPI spec (`/openapi.json`, Swagger UI at `/docs` if enabled per Key Decisions)
- Formally document all existing REST endpoints as the public API surface (search, search-with-context, collection lifecycle, document lifecycle, job control, health/readiness, diagnostics)
- **Extract a shared service layer** from the inline orchestration logic currently embedded in REST route handlers (which access `app.state` directly), then wire both REST routes and MCP tools to call that service layer — this is the core modernization work, not simply rewiring `mcp.py`
- Audit and enumerate all capability gaps between REST and MCP surfaces (see Known Gaps to Resolve); close gaps or explicitly document intentional protocol differences
- Document intentional protocol differences between REST and MCP (e.g. streaming, pagination, context-window helpers that are MCP-specific)
- Validate that the CalVer versioning strategy is sufficient as the stability signal; add a concrete versioning policy and changelog entry policy
- **Standardize error response shapes** across REST and MCP — REST currently returns a mix of `JSONResponse({'detail': ...})`, `HTTPException`, and Pydantic models; MCP returns `{'error': str}` with HTTP 200. Stabilize both to consistent schemas documented in the OpenAPI spec.
- Add typed response models to all REST routes (replacing bare `JSONResponse` returns) and use FastAPI's `Depends()` for auth, so the OpenAPI spec generates accurate schemas for all endpoints

## Out of Scope
- URL versioning (`/v1/` prefix) — no demonstrated need to run two API versions simultaneously; CalVer + OpenAPI spec is the right commitment level at this stage
- Explain/debug endpoint (`/explain` with full score breakdowns) — already a separate backlog item; deferred to avoid blocking API stabilization on new endpoint implementation
- SDK generation from the OpenAPI spec — useful future iteration, not needed for the stability contract
- Admin or debug UI — roadmap explicitly sequences this after API stabilization

## Key Decisions
- **CalVer + OpenAPI over URL versioning**: A published spec plus CalVer version bumps communicates the stability contract without the operational overhead of running multiple URL namespaces simultaneously. This decision can be revisited if breaking changes become frequent.
- **Extract service layer, then wire both surfaces**: REST routes today contain inline orchestration against `app.state` (search_store, embedder, job_store, etc.); MCP uses `SearchPipeline` — a separate abstraction. The work is: (a) extract a proper service layer from REST route handlers, (b) rewrite REST routes to call it, (c) rewrite MCP tools to call the same layer. This eliminates drift by construction.
- **CalVer breaking-change semantics**: CalVer segments encode time only, not compatibility. The `BREAKING.md` entry (or a dedicated changelog section) is the sole compatibility signal — every release that removes or changes an existing contract MUST include a `BREAKING.md` entry describing what changed, what the migration path is, and from which release the deprecated form was announced. Consumers should subscribe to `BREAKING.md` updates, not interpret CalVer segments. Without this, the OpenAPI spec alone is not a sufficient stability contract.
- **Backward-compatibility scope**: The no-breaking-changes constraint applies to **REST routes only**. MCP consumers will see breaking changes as response shapes are normalized to match REST (e.g., search currently returns raw dataclass dicts; after parity it must match REST's `SearchResponse` with `acl_filtered`). A migration path is required: document changes in the changelog, give consumers one CalVer month-increment of warning before removal.
- **Auth for `/docs` and `/openapi.json`**: Auth middleware currently exempts only `/health`. `/docs` and `/openapi.json` must be explicitly exempted so that users can discover the API contract without needing a token first. Exempting these endpoints is the correct choice — they expose schema, not data — and the decision must be recorded in the spec's security section.
- **MCP auth model**: MCP currently accepts a `SearchPipeline` with no key validation and no namespace scoping. This is a security gap. The modernization must define and implement an auth model for MCP connections equivalent to REST's Bearer token middleware. The specific mechanism (token passed at connection time, env var, config) is a design choice for implementation, but the requirement is non-negotiable.
- **Swagger UI in production**: Swagger UI is enabled in production and served behind auth middleware (exempt from auth by the `/docs` exemption, but not serving data). Self-hosters benefit from discoverability. If a deployment needs to disable it, that is a config option — the default is enabled.
- **Deprecation policy**: Deprecated endpoints are annotated in the OpenAPI spec with a `deprecated: true` flag and a sunset note. Deprecated routes are supported for at least one CalVer month-increment before removal. The `search_sync` stub in `archon_toolkit_search.py` (currently hardcoded as "not supported via HTTP") must be removed from `ArchonToolkit` in the following release — the OpenAPI spec's `deprecated: true` mechanism does not apply here, as this tool lives in Archon's own MCP proxy layer, not in the archon-search OpenAPI spec.
- **Power users as primary consumer**: The API is designed for self-hosters building integrations, not abstract third-party frameworks. This keeps the surface area grounded in the use cases we already serve.

## Known Gaps to Resolve
There are three distinct surfaces to audit: (1) the archon-search REST server, (2) the archon-search native MCP server (`mcp.py`, calls `SearchPipeline` directly), and (3) Archon's MCP proxy layer (`archon_toolkit_search.py`, calls REST via `SearchClient`). Items marked "decision required" need an explicit scope decision before implementation.

**REST-only (no native MCP equivalent — native `mcp.py` has no tool for these):**
- `POST /route` — query routing (no equivalent in either MCP layer)
- `GET /indexing-state` — indexing progress (no equivalent in either MCP layer)
- `GET /jobs/{id}`, `DELETE /jobs/{id}` — job control (no equivalent in either MCP layer)
- `GET /telemetry/entries` — telemetry log read-back (no equivalent in either MCP layer)

Note: `GET /status`, `POST /ingest`, `GET /telemetry/stats`, `POST /collections`, `DELETE /collections/{name}`, and `POST /collections/{name}/reindex` have proxy equivalents in `archon_toolkit_search.py` but no native `mcp.py` tool. These require the parity audit to decide: add native MCP tools (so the archon-search MCP server is self-contained), or document as Archon-proxy-only.

**Native MCP-only (no REST equivalent — archon-search `mcp.py` has these; REST does not):**
- `search_with_context` — search with explicit context window parameter. Decision required: add REST endpoint or document as intentional MCP-specific.
- `get_collections_meta` — collection metadata including centroid vectors. Decision required: add REST endpoint or document as intentional MCP-specific.
- `ingest_file` — synchronous inline ingestion via `pipeline.ingest_file()`; REST's `POST /ingest` enqueues a background job (async). Fundamentally different execution models. Decision required: expose a synchronous REST variant, or document as intentional protocol difference.
- `ingest_directory` — same execution model divergence as `ingest_file`.
- `list_documents` — no `GET /collections/{name}/documents` REST endpoint. Decision required: add REST endpoint or document as MCP-only.
- `delete_document` — no `DELETE /documents/{doc_id}` REST endpoint. Decision required: add REST endpoint or document as MCP-only.
- `get_collection_meta` (single collection) — returns raw dict including centroid; REST's `GET /collections/{name}` has a different shape (no centroid, adds `acl_protected_count`, `acl_open_count`, `last_indexed`, `embedding_model`). Must be reconciled.

**Known behavioral divergence (must be reconciled):**
- MCP search returns raw dataclass dicts; REST returns `SearchResponse` with `acl_filtered`. After parity, native MCP must return the same shape.
- MCP errors return `{"error": str}` with HTTP 200; REST returns proper HTTP status codes. Both must be normalized.
- `list_collections`: native MCP's `list_collections` returns `CollectionMeta` dicts (centroid omitted); REST's `GET /collections/` returns a different shape (`{name, path, description, doc_count, chunk_count, namespace, status}`). Shapes must be reconciled or divergence explicitly documented.
- `search_sync` is a dead stub in `archon_toolkit_search.py` (Archon's MCP proxy layer) — hardcoded to return "not supported via HTTP". Will be removed from `ArchonToolkit`; `archon_toolkit_search.py` updated accordingly.

## Edge Cases & Constraints
- **Existing `SearchClient` must keep working**: Archon's internal HTTP client is the primary consumer today. The OpenAPI spec formalizes the contract it already uses; no breaking changes to existing REST routes are permitted as part of this work.
- **Third MCP surface — `archon_toolkit_search.py`**: Archon's MCP proxy calls archon-search over HTTP via `SearchClient`. When REST endpoints change, this surface must also be kept in sync; it is a consumer of the public API and must be updated alongside any contract changes.
- **Auth bootstrap**: `/docs` and `/openapi.json` must be explicitly exempted from auth middleware so users can discover the API contract. See Key Decisions.
- **MCP has no auth**: See Key Decisions — this is a required deliverable, not a post-launch concern.
- **FastAPI spec quality requires typed response models**: Routes returning `JSONResponse` directly or with `response_model=None` produce empty `{}` schemas in the auto-generated spec. Curation means rewriting route handlers to use typed Pydantic response models and FastAPI's `Depends()` for auth — this is a partial REST layer rewrite, not a free byproduct of FastAPI.
- **CalVer as stability signal requires explicit policy**: Without defined breaking-change semantics, CalVer alone is ambiguous. See Key Decisions for the concrete policy.
- **OpenAPI spec snapshot test**: A snapshot test of the OpenAPI spec (or contract test suite) must be added as part of this work, so future changes that break the published contract are caught at CI time rather than discovered by consumers.
- **CORS policy**: Browser-based consumers (e.g., n8n web UI) require CORS headers. Define whether CORS is enabled by default, restricted by origin, or opt-in via config — and document the policy in the spec.

## Definition of Done
- All public endpoints have descriptions, parameter docs, and example schemas in the OpenAPI spec — no `{}` response schemas remain.
- Auth requirements are correctly annotated on all endpoints (SecurityScheme defined in spec; Bearer token requirement shown per-endpoint).
- `/docs` and `/openapi.json` are accessible without a token and return the current spec.
- MCP and REST surfaces produce identical results for all equivalent operations (verified against the enumerated parity list in Known Gaps to Resolve).
- MCP auth model is implemented and enforced — unauthenticated MCP connections are rejected.
- CalVer breaking-change policy is documented in the spec and in `BREAKING.md` or equivalent changelog.
- Error response shapes are consistent and documented: REST returns typed Pydantic error models with correct HTTP status codes; MCP returns the same error shape (not `{"error": str}` with 200).
- `search_sync` is removed from `ArchonToolkit` and `archon_toolkit_search.py` is updated accordingly.
- `SearchClient` integration tests pass with no regressions.
- OpenAPI spec snapshot test is in CI and catches unintended contract changes.

## Future Iterations
- **URL versioning (`/v1/`)**: Introduce if breaking changes become a real operational problem.
- **Explain/debug endpoint**: Full score breakdowns — vector rank, FTS rank, fusion score, reranker score, ACL filters applied. Tracked as a separate backlog item.
- **SDK generation**: Auto-generate Python/TypeScript clients from the OpenAPI spec.
- **Admin/debug UI**: Operator interface, sequenced after API and explainability stabilize.

## Recommendation
This is the right feature to build now — external API stability is the natural next step before retrieval quality work makes the interface harder to formalize. The hardest part is the service layer extraction: REST routes today have inline orchestration against `app.state`, and MCP uses a separate `SearchPipeline` abstraction — there is no shared layer to wire into. The extraction must happen first, or the "parity" work just produces two independently-maintained copies of the same logic. The OpenAPI spec curation is non-trivial because bare `JSONResponse` routes produce empty schemas — expect a partial route rewrite. Do not compromise on MCP parity or MCP auth; shipping REST-only or leaving MCP unauthenticated would contradict the first-class MCP commitment and require a second stabilization pass.
