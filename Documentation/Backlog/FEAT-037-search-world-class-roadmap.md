---
Purpose: Priority-ordered roadmap for turning Archon's current search subsystem into a standalone world-class search product
Audience: Maintainers planning extraction, architecture, and feature roadmap for Search
Status: Draft
Last reviewed: 2026-04-30
Source of truth: Current code under archon/search/ verified against comparison docs dated 2026-04-29
---

# Standalone Search Roadmap

## Scope

The standalone package name must be decided before item 1 implementation begins. Options: (a) same monorepo as Archon with separate `pyproject.toml` under `packages/archon-search/`; (b) separate repository with independent releases. This document does not mandate the choice but requires it to be made and documented in an ADR before extraction work starts.

This document converts these two comparison documents into a precise implementation roadmap:

- `Documentation/Backlog/FEAT-037-search-competitive-analysis-field.md`
- `Documentation/Backlog/FEAT-037-search-competitive-analysis-marveen.md`

Because those documents may lag the implementation, **the code is the source of truth**. The roadmap below was verified against:

- `archon/search/`
- `archon/cli/search_cmd.py`
- `archon/search/server.py`
- `archon/search/store.py`
- `archon/search/pipeline.py`
- `Documentation/Architecture/180_search_architecture.md`

## Verified Current State

These points are confirmed from the current code, not assumed:

1. Search is already a **separate process with partially independent lifecycle control**, but it is **not a separate product**.
   - Verified in [Documentation/Architecture/180_search_architecture.md](Documentation/Architecture/180_search_architecture.md) and [archon/search/server.py](/Users/manczg/Documents/development/archon/archon/search/server.py).
   - It is still packaged, versioned, configured, and operated as part of Archon, and the Archon gateway can auto-start it when Search is enabled.

2. Search currently exposes an **MCP-first server**, not a general-purpose external API.
   - Verified in [archon/search/server.py](/Users/manczg/Documents/development/archon/archon/search/server.py).
   - It has MCP tools plus `GET /health`; it does not currently provide a first-class REST search API.

3. The core retrieval stack is already strong.
   - Hybrid retrieval exists: vector search + FTS + RRF in [archon/search/store.py](/Users/manczg/Documents/development/archon/archon/search/store.py).
   - Cross-encoder reranking exists in [archon/search/reranker.py](/Users/manczg/Documents/development/archon/archon/search/reranker.py).
   - Context-window expansion exists in [archon/search/pipeline.py](/Users/manczg/Documents/development/archon/archon/search/pipeline.py).
   - Multi-collection routing exists in [archon/search/router.py](/Users/manczg/Documents/development/archon/archon/search/router.py).

4. The ingestion stack is already substantial.
   - File parsing, chunking, embedding, sync, watcher support, crash recovery, collection metadata, and startup sync are all present across `parser.py`, `chunker.py`, `pipeline.py`, `sync.py`, `watcher.py`, and `progress.py`.

5. The main confirmed product gaps are not “basic RAG missing pieces”. They are:
   - product separation
   - canonical service contract and job model
   - public API surface
   - auth and isolation
   - metadata model and filters
   - retrieval quality extensions
   - query-path and sync-path performance architecture
   - operability for standalone use
   - evaluation discipline

## Priority Logic

Items are ordered by this rule set:

1. **Separate the product boundary first.** A search system cannot be “separate from Archon” if packaging, config, ownership, and API are still Archon-specific.
2. **Define the service contract before freezing public protocols.** Metadata, jobs, explainability, and security shape the real API.
3. **Fix retrieval correctness before adding exotic features.** World-class search is defined first by relevance and debuggability, not by feature count.
4. **Fix first-order performance bottlenecks before multiplying query work.** Stronger routing and shared query execution should come before HyDE and RAG Fusion.
5. **Add operability before scale theatrics.** Auth, export, health, and evaluation matter earlier than GraphRAG or distributed scaling.
6. **Do not optimize around current single-user assumptions.** The current Archon coupling is acceptable for a daemon, but it is the main constraint on a standalone search product.

## Priority 0: Product Separation

These are the highest-priority items. Without them, Search remains an Archon subsystem.

### 1. Extract Search into a standalone package and service

**Why this is first**

- Current code lives under `archon/search/`, uses Archon config loading, and is managed by `archon search ...`.
- That is separate process separation, not product separation.

**What to do**

- Create a standalone package with its own name, version, CLI, config schema, and release process.
- Make Archon a client of that service instead of the owner of that code.
- Remove direct dependency on `archon.config.loader` for the standalone server bootstrap.
- Remove direct client-side imports of Search internals from Archon where a network/service contract should exist.
- Define ownership and migration for:
  - search config
  - service installation
  - data directory layout
  - manifests and indexing state files
  - collection naming rules

**Minimum acceptance criteria**

- Search server can start without importing Archon application modules.
- Search has its own CLI entry point.
- Search has its own config file and example config.
- Archon integrates with it only through network/API boundaries.
- Archon no longer needs direct imports of Search routing/search helpers for normal operation.

### 2. Define the canonical service contract and indexing job model

**Why this is second**

- A standalone search product needs its data model and long-running operation model defined before protocol stability.
- Search lifecycle operations are not just request/response calls; they are jobs.

**What to do**

- Define canonical internal service concepts first:
  - query
  - result
  - collection
  - document
  - chunk metadata
  - namespace
  - security scope
  - ingest/reindex/export/migration job
- Add a first-class async job/control plane for:
  - ingest
  - reindex
  - delete
  - export
  - import
  - migration
- Every long-running operation should support:
  - job ID
  - idempotency
  - status/progress
  - retry semantics
  - cancel/resume where feasible
  - backpressure/failure isolation

**Minimum acceptance criteria**

- The domain/service contract is documented independently of protocol.
- Long-running indexing and migration operations are modeled as jobs, not ad hoc endpoints.
- REST and MCP can be layered on top of the same contract later without redesigning the core model.

### 3. Introduce a real metadata schema

**Why this is third**

- The service contract cannot be correct until metadata shape, filterability, and audit fields are defined.
- Current chunk schema is fixed to `doc_id`, `chunk_id`, `text`, `vector`, `source_path`, `indexed_at`.
- World-class search requires structured filtering, ranking, audit, and governance.

**What to do**

- Add typed per-document and per-chunk metadata.
- Distinguish:
  - system fields
  - filterable metadata
  - ranking metadata
  - audit metadata
- Define indexing/update semantics for metadata explicitly.
- Define which metadata is high-cardinality, indexed, filterable, or informational only.

**Minimum acceptance criteria**

- Search records can carry validated metadata without turning the product into a generic blob store.
- Filters are stored and queried without abusing free-text fields.
- Metadata is included in API responses and explain output.

### 4. Build an evaluation harness and data-collection loop

**Why this is fourth**

- The repo has strong software tests.
- It does not currently expose a first-class retrieval evaluation framework in the verified Search code.
- “World class” requires measurable retrieval quality, not intuition.

**What to do**

- Add offline evaluation datasets and metrics.
- Add the instrumentation needed to build those datasets from real usage safely.
- Track at least:
  - recall@k
  - MRR
  - nDCG@k
  - reranker lift
  - routing accuracy
  - latency percentiles
- Add online analytics and feedback loops for:
  - query logging policy
  - judgment capture
  - relevance feedback
  - safe controlled experiments

**Minimum acceptance criteria**

- Every ranking change can be compared against a fixed benchmark set.
- CI or release checks report evaluation deltas.
- Query collection, labeling, and privacy boundaries are explicitly defined.

### 5. Add authentication, authorization, namespace isolation, and security trimming

**Why this is fifth**

- Current Search server has no verified server-side auth path.
- Current architecture explicitly states shared URL and no per-user routing.
- In a standalone product, isolation is not only auth middleware; it is a storage and contract design issue.

**What to do**

- Add API key or bearer-token auth first.
- Add namespace/project isolation at the storage and query layers.
- Design collections to belong to a namespace, not to the whole server.
- Add document-level or chunk-level security trimming to the design where source ACL inheritance is required.

**Minimum acceptance criteria**

- Every mutating and read API call is authenticated.
- A request cannot read or mutate collections outside its namespace.
- The authorization model is represented in the data model and query contract, not bolted on later.

### 6. Define stable external APIs: REST + MCP

**Why this is sixth**

- Current server is MCP-first and suitable for Claude integration, but not for broad external use.
- External protocol stability should follow the canonical service contract, metadata model, job model, and security model.

**What to do**

- Keep MCP as a first-class integration surface.
- Add a stable REST API for:
  - search
  - search with context
  - collection lifecycle
  - document lifecycle
  - job control
  - health/readiness
  - diagnostics
  - explain/debug

**Minimum acceptance criteria**

- OpenAPI spec exists.
- REST and MCP call the same internal application service layer.
- Protocol differences are intentional and documented rather than accidental.

## Priority 1: Retrieval Quality

These items directly improve relevance and should come before advanced “flashy” features.

### 7. Add metadata filters at search time

**Why it is urgent**

- Both comparison documents identify this as a high-value missing feature.
- The current schema already supports at least path/date filtering via `source_path` and `indexed_at`, but the search/store/API path does not expose filtering yet.
- A richer metadata model is still needed for broad filterability.

**Examples**

- source path prefix / glob
- file type
- indexed-after / indexed-before
- collection tags
- language
- user-defined labels

### 8. Add a server-side multi-collection search primitive

**Why it is urgent**

- The current client path fans out one request per collection.
- Each server-side `search()` call currently re-embeds the same query and reruns reranking independently.
- This is a first-order latency and throughput bottleneck before HyDE or RAG Fusion are added.

**What to do**

- Embed the query once per request.
- Run multi-collection retrieval on the server side.
- Share merge/rerank orchestration across collections instead of duplicating it per collection call.
- Design this together with the external API and explain/debug model.

### 9. Replace centroid-only collection routing with stronger collection representations

**Why it is urgent**

- Current routing uses one centroid vector per collection.
- That is both a relevance weakness and a performance control point, because routing determines downstream query fan-out.
- Stronger routing should come before features that multiply query count.

**What to do**

- Keep centroid routing as baseline.
- Add richer alternatives:
  - summary embedding
  - multi-centroid / clustered centroids
  - representative chunk prototypes
  - description + centroid hybrid routing
- Reduce routing transport overhead by moving shortlist/routing work server-side or adding metadata cache/invalidation.

### 10. Add HyDE / query expansion

**Why it is urgent**

- Explicitly identified in the field comparison as a major competitive gap versus R2R.
- Especially valuable for short, vague, underspecified user queries.

**Requirement**

- Make it optional and measurable.
- Do not enable by default until it shows benchmark gains on the evaluation harness.

### 11. Add RAG Fusion / multi-query decomposition

**Why it is urgent**

- Also explicitly identified in the field comparison as a high-value gap.
- Improves recall for multi-faceted questions where one query wording misses relevant chunks.

**Requirement**

- Parallel sub-query search.
- Fusion must be benchmarked, not enabled by assumption.

### 12. Add result explainability and a debug/explain endpoint

**Why it is urgent**

- Current server does not expose a verified explain API.
- Once search becomes standalone, operators need to know why a result ranked highly.

**Explain output should include**

- vector rank
- FTS rank
- fused score
- reranker score
- matched metadata filters
- collection selection path
- whether routing/HyDE/RAG Fusion were used

### 13. Add per-collection embedding model selection

**Why it is important**

- `CollectionMeta.embedding_model` exists, but current ingest/search flow still operates around a global configured model.
- This is a direct gap called out in both comparisons.
- It is also a routing-contract change because mismatched collection models are currently treated as unscored by the router.

**What to do**

- Allow each collection to declare its embedding model.
- Validate query-time compatibility.
- Define cross-model routing/query strategies explicitly.
- Support collection-level reindex workflows.

### 14. Add multilingual retrieval support

**Why it belongs in Priority 1**

- Current default model path is English-oriented.
- The field comparison explicitly calls out lack of multilingual support out of the box.
- A standalone world-class system should not be English-only by default assumption.

**What to do**

- Support multilingual embedding models.
- Add language metadata.
- Add language-aware FTS/tokenization strategy where backend allows it.

## Priority 2: Ingestion and Storage Correctness

These items improve indexing quality and long-term maintainability.

### 15. Add connector and federation architecture

**Why it matters**

- A world-class standalone search product needs more than local filesystem indexing.
- It needs source connectors, sync checkpoints, scheduler/orchestrator behavior, ACL propagation, and source-specific change detection.

### 16. Remove full-collection FTS rebuild as the default update path

**Why it matters**

- Confirmed in current code: `ingest_directory()` rebuilds FTS once at the end with `replace=True`.
- Incremental sync also rebuilds FTS after changed/new/deleted files.
- This is not only a bulk-ingest issue; it affects watch-mode and incremental sync scalability.

**Goal**

- Support incremental or additive FTS maintenance for changed documents only.

### 17. Remove full metadata rescans from incremental sync

**Why it matters**

- After incremental sync, the current implementation recomputes collection metadata by reloading all vectors and recounting docs.
- Fixing FTS rebuilds alone still leaves large watched collections exposed to O(collection size) rescans.

### 18. Add streaming / incremental chunking for very large files

**Why it matters**

- Both comparison docs call out the absence of streaming/incremental chunking.
- Large-file ingest should not require full-document materialization when avoidable.

### 19. Add chunk-level enrichment at ingest time

**Why it matters**

- Current chunk records are minimal.
- Better retrieval quality often comes from enriched chunks, not just better rerankers.

**Candidate enrichments**

- local title / section path
- heading ancestry
- file type
- page number
- source subtype
- code symbol context for source code files

### 20. Add export, import, backup, and restore APIs

**Why it matters**

- Current comparisons explicitly note missing backup/export API.
- A standalone service must support data portability and disaster recovery.

**Minimum scope**

- collection export
- collection import
- metadata export
- manifest/state export
- backup compatibility statement per storage backend

### 21. Add schema migration strategy that does not rely on full manual re-ingest

**Why it matters**

- Current storage design is fast, but schema evolution is operationally expensive.
- A standalone product needs explicit migration policy, tooling, and rollback rules.

## Priority 3: Standalone Operability

These items make the system serious to run outside the Archon daemon context.

### 22. Add first-class health, readiness, and diagnostics APIs

**Why it matters**

- `GET /health` exists, but standalone operation needs deeper readiness and diagnostics.
- Current lazy-loading model means the first real query can absorb embedder/reranker startup cost while health still reports `ok`.

**Should cover**

- storage connectivity
- model load status
- warm/preloaded status
- index build state
- collection staleness
- model mismatch
- queue/backfill state
- watcher state

### 23. Add install-time or background model/provider validation for both embedder and reranker

**Why it matters**

- Current docs note that provider validation is stronger for embeddings than for reranking.
- Standalone production software must validate acceleration config without accidentally regressing the current lazy-load startup contract.

### 24. Add observability, tracing, and latency breakdowns

**Why it matters**

- Once routing, filters, shared multi-collection search, HyDE, and RAG Fusion exist, latency becomes multi-stage.
- This should exist before the most expensive retrieval features are rolled out broadly.

**Track at minimum**

- parse time
- embed time
- routing time
- vector search time
- FTS time
- fusion time
- rerank time
- end-to-end latency

### 25. Add log rotation and structured operational logs

**Why it matters**

- The comparison calls out no dedicated search log rotation.
- Standalone services need stable machine-readable logs.

### 26. Add background jobs and maintenance policies

**Why it matters**

- World-class search systems need explicit maintenance behavior.

**Examples**

- stale collection detection
- scheduled compaction/vacuum if backend requires it
- orphan cleanup
- failed-ingest retry policy
- periodic integrity checks

## Priority 4: Product Surface and UX

These matter, but they should not outrank separation or retrieval quality.

### 27. Add streaming search results

**Why it matters**

- Called out as missing in both comparisons.
- Particularly useful when reranking large candidate sets.

### 28. Add SDKs for external consumers

**Why it matters**

- A standalone service should not force all users through raw HTTP or MCP.

**Minimum scope**

- Python SDK
- TypeScript SDK

### 29. Add admin/debug UI only after API and explainability stabilize

**Why this is later**

- A UI before stable APIs usually hardens the wrong abstractions.
- The right sequence is API -> explainability -> operator UI.

### 30. Add access control policies per collection

**Why it matters**

- This is the standalone form of the “security profiles” idea surfaced by the Marveen comparison.
- It becomes important after namespaces and auth exist.

## Priority 5: Advanced Features for “World-Class” Positioning

These are valuable, but they should not displace the earlier priorities.

### 31. Add salience and temporal weighting

**Source**

- Strongly motivated by the Marveen comparison.

**Use carefully**

- Make this an explicit scoring component, not hidden magic.
- It must be explainable and disableable.

### 32. Add semantic memory tiers

**Source**

- Also motivated by the Marveen comparison.

**Examples**

- recent
- durable
- pinned
- archival

This is useful only after metadata, filters, and explainability are in place.

### 33. Add GraphRAG / entity-relationship retrieval

**Source**

- Identified as a gap versus Kotaemon, mem0, and R2R.

**Why later**

- It is expensive and easy to overbuild.
- It should follow a stable core retrieval system and an evaluation harness.

### 34. Add richer multimodal retrieval and document understanding

**Source**

- Identified in the field comparison as a gap versus Kotaemon and R2R.

**Why later**

- High cost.
- Complex evaluation.
- Not needed for the standalone product boundary or the core retrieval leap.

### 35. Add horizontal scaling only after the storage contract stabilizes

**Why later**

- Current LanceDB-based design is fast locally but not a multi-writer distributed system.
- Scaling decisions should follow product boundaries, APIs, auth, and workload evidence.

### 36. Add pluggable storage backends only after the application contract stabilizes

**Why later**

- Multi-backend support is valuable.
- Premature backend abstraction will slow the extraction effort.
- First stabilize the service contract, metadata model, and evaluation harness.

## Items That Are Important But Should Not Be Mistaken for Core Priorities

These are worthwhile, but they are not the first moves:

- binary quantization
- embedded widget
- web dashboard
- OpenAI-compatible API shim
- citation-rich streaming UI
- multi-region/distributed deployment

They become sensible only after the standalone system has:

- stable APIs
- auth
- metadata
- explainability
- evaluation
- predictable ingestion behavior

## Final Ordered Backlog

If only one list is used for planning, use this order:

1. Extract Search into a standalone package and service.
2. Define the canonical service contract and indexing job model.
3. Add a real metadata schema.
4. Build an evaluation harness and data-collection loop.
5. Add auth, authorization, namespace isolation, and security trimming.
6. Define stable external APIs: REST + MCP.
7. Add metadata filters at search time.
8. Add a server-side multi-collection search primitive.
9. Improve collection routing beyond single centroids.
10. Add HyDE / query expansion.
11. Add RAG Fusion / multi-query retrieval.
12. Add explain/debug APIs and score breakdowns.
13. Add per-collection embedding models.
14. Add multilingual retrieval support.
15. Add connector and federation architecture.
16. Remove full-collection FTS rebuild as the default update path.
17. Remove full metadata rescans from incremental sync.
18. Add streaming/incremental chunking for very large files.
19. Add chunk-level enrichment.
20. Add export/import/backup/restore.
21. Add schema migration tooling.
22. Add deeper health/readiness/diagnostics.
23. Add install-time or background validation for embedder/reranker providers.
24. Add observability, tracing, and stage-level latency metrics.
25. Add structured logs and log rotation.
26. Add maintenance jobs and retry policies.
27. Add streaming result delivery.
28. Add Python and TypeScript SDKs.
29. Add admin/debug UI.
30. Add collection-level access policies.
31. Add salience and temporal weighting.
32. Add semantic memory tiers.
33. Add GraphRAG.
34. Add richer multimodal retrieval.
35. Reassess horizontal scaling.
36. Reassess backend pluggability.

## Recommendation

If the goal is specifically **“a full-featured world-class search system separated from Archon”**, the correct sequence is:

1. **Separate the product boundary.**
2. **Define metadata, jobs, and security as the real service contract.**
3. **Make relevance measurable.**
4. **Fix first-order query and sync bottlenecks.**
5. **Then add advanced retrieval features.**

The biggest mistake would be to start with GraphRAG, richer multimodal retrieval, or distributed scale while Search is still packaged and operated as an Archon subsystem with no standalone contract for metadata/jobs/security, no retrieval evaluation framework, and unresolved first-order performance bottlenecks in query fan-out and incremental sync.
