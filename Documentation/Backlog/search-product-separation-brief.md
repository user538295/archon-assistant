---
Purpose: Feature Brief for Search Product Separation (P0 items 1–3)
Status: Draft
Last reviewed: 2026-04-30
---

# Feature Brief: Search Product Separation (P0 Items 1–3)

## Problem

Search is a separate process but not a separate product. It cannot bootstrap without importing Archon's config loader, its service management lives inside `archon/platform/`, and Archon's client-side routing code imports Search internals directly (`Embedder`, `MultiCollectionRouter`). Until these couplings are removed, Search cannot be versioned, released, or operated independently.

## Goal

The Search server starts and runs with zero imports from Archon application modules. Archon integrates with Search exclusively through HTTP/MCP. The service contract (domain types) and metadata schema are defined inside the standalone package, not inherited from Archon conventions.

## Users & Context

Maintainers performing extraction work and, once complete, operators who want to run Search independently of Archon. The user-facing change is minimal — the daemon still runs locally — but the package boundary becomes real rather than nominal.

## Core Flow

1. Create `packages/archon-search/` with its own `pyproject.toml`, package name `archon-search`, and independent version.
2. Move `archon/search/` source into `packages/archon-search/archon_search/`.
3. Implement standalone config loading from `archon-search.toml`; remove all imports of `archon.config.loader` from the Search server.
4. Extract `LaunchdSearchService`, `SystemdSearchService`, `WindowsSearchService` from `archon/platform/` into `packages/archon-search/`.
5. Add a `archon-search` CLI entry point (`start`, `stop`, `status`, `install`, `config show/get/set`) — fully independent of the `archon` CLI.
6. Remove direct imports of `Embedder` and `MultiCollectionRouter` from `archon/ai/search_context_provider.py`; replace with HTTP calls to the Search server API.
7. Remove `archon/cli/search_cmd.py` direct imports of Search internals; replace with HTTP client calls or delegate to the `archon-search` CLI.
8. Define canonical domain types inside `archon-search`: `Query`, `Result`, `Collection`, `Document`, `Chunk`, `Namespace`, `IngestJob`, `ReindexJob`, `DeleteJob`.
9. Add first-class async job model: every long-running operation (ingest, reindex, delete) returns a job ID, supports status polling, and supports cancel.
10. Extend chunk storage schema with typed metadata fields: system fields (doc_id, chunk_id, source_path, indexed_at, file_type, language), filterable fields (user-defined key/value), ranking metadata (custom score signals), audit fields (ingested_by, updated_at).
11. Archon retains a `[search]` section in its `config.toml` containing only client-facing settings: `url`, optional credentials placeholder, `enabled`.
12. Write a data directory migration guide for existing users (manifest and state files stay in place; config path changes).

## In Scope

- `packages/archon-search/` monorepo structure with own `pyproject.toml` and version
- `archon-search.toml` standalone config with its own schema and loading logic
- `archon-search` CLI entry point: `start`, `stop`, `status`, `install`, `config`
- Service management classes moved from `archon/platform/` into `packages/archon-search/`
- Removal of all direct Search internal imports from Archon core (config loader, embedder, router, sync helpers)
- Canonical domain type definitions inside `archon-search` (Query, Result, Collection, Document, Chunk, Namespace, Job)
- First-class async job model for ingest, reindex, delete operations (job ID, status, cancel)
- Rich metadata schema: system + filterable + ranking + audit fields
- Archon client adapter (`archon/ai/search_client.py` or similar) that speaks only HTTP/MCP — no Search internals
- Data directory and manifest migration guide
- Tests: all existing search tests migrated to `packages/archon-search/tests/`; Archon-side tests updated to mock HTTP boundary

## Out of Scope

- Auth and namespace isolation (P0 item 5 — separate brief)
- Evaluation harness (P0 item 4 — separate brief)
- REST API with OpenAPI spec (P0 item 6 — separate brief)
- Metadata filter support at query time (P1 item 7 — depends on metadata schema from this brief)
- Any P1+ retrieval features (HyDE, RAG Fusion, routing improvements)
- PyPI publication — extraction and local monorepo structure only

## Key Decisions

- **Monorepo**: `packages/archon-search/` with own `pyproject.toml`. Repo split can happen later once the boundary is stable; adding cross-repo coordination cost during extraction would slow it down without benefit.
- **Config**: `archon-search.toml` is the standalone Search config. Archon's `config.toml` retains only a `[search]` client block (url, enabled). The current `[search]` section fields (collections, pinned_collections, embedding_model, etc.) migrate to `archon-search.toml`.
- **Client-side routing imports removed**: `search_context_provider.py` currently imports `Embedder` and `MultiCollectionRouter` directly. These become HTTP calls. The routing embed round-trip (~20–50ms over localhost) is acceptable for a local daemon.
- **Service management extracted**: `archon/platform/` Search service classes move into `packages/archon-search/`. Archon's platform layer retains only Archon-specific service management. No stubs left behind — clean removal.

## Edge Cases & Constraints

- **Existing users with data directories**: LanceDB tables, manifest files, and indexing state files stay in place. Only the config file path changes. The migration guide must document exact steps.
- **Transition period**: During extraction, `archon/search/` still exists in the repo. The move must be a single atomic commit per logical step, not a partial state. CI must stay green at every step.
- **Archon `[search]` config section shrinks**: Any Archon config key that was Search-internal (collections, db_path, embedding_model) must be removed from Archon's schema. Users who configured these fields need a migration note.
- **Test isolation**: Archon-side tests that currently import Search internals must be updated to mock the HTTP boundary. No test should import `archon_search` internals after extraction.
- **Job model is additive**: Long-running operations currently return synchronously (ingest blocks until complete). Adding a job model must not break existing callers — the MCP tools can return job ID and also poll to completion for backward compatibility.

## Open Questions

- **Package name on PyPI**: `archon-search` is the working name. Should it be published during this work, or is local monorepo structure the only goal for now?
- **Independent versioning**: Does `archon-search` version independently from `archon`, or do they share a release cadence? The answer affects how `pyproject.toml` dependency pins are written in `archon`'s own package.

## Future Iterations

- Auth, authorization, and namespace isolation (P0 item 5)
- Evaluation harness and retrieval metrics (P0 item 4)
- Stable REST API with OpenAPI spec (P0 item 6)
- Metadata filter support at query time (P1 item 7) — unblocked by the metadata schema defined here

## Recommendation

This is the right work to do first. Without it, every subsequent P0–P5 item lands on top of an Archon subsystem instead of a standalone product — and that debt compounds. The hardest part is the config and client-side import removal: both touch many files and require a clean HTTP boundary where previously there was none. The metadata schema is the piece most likely to be underspecified at planning time; insist on concrete field definitions with types before implementation begins, or the schema will be revisited during every subsequent feature. Do not compromise on the clean break — no "Archon-privileged client" exceptions.
