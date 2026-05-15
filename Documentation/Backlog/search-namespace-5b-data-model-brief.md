# Feature Brief: Namespace Data Model (5b)

## Problem
archon-search has no concept of namespace — every collection is globally visible and accessible to any authenticated caller. Before namespace isolation (5c) can be enforced, the data model must carry the `namespace` field everywhere a collection is identified or returned.

## Goal
Every collection record carries a `namespace: str` field. All existing collections are automatically migrated to `"default"` on startup. REST responses include the field. No behaviour changes for existing callers.

## Users & Context
Local and multi-tenant deployments of archon-search. Users are not directly aware of this change — it is infrastructure for 5c. The one observable effect: `GET /collections` and `GET /collections/{name}` start returning a `namespace` field.

## Core Flow
1. archon-search starts up.
2. Startup migration runs: for each collection in `_archon_collection_meta`, if `namespace` is absent, set it to `"default"`. Idempotent — safe to run on every startup.
3. New collections created after migration are written with `namespace="default"` (hardcoded — no config, no API parameter yet).
4. All collection REST responses include `"namespace": "default"` in the payload.
5. No filtering or enforcement — all callers see all collections as before.

## In Scope
- Add `namespace: str` field to `CollectionMeta` dataclass (`collection_meta.py`)
- Add `namespace` column to `_archon_collection_meta` LanceDB table schema (`store.py`)
- Add `namespace` field to `Collection` and `CollectionDetail` domain types (`types.py`)
- Add `namespace` to all collection REST responses (`routes_collections.py`)
- Idempotent startup migration: existing collections → `namespace="default"`
- `SearchStore.ensure_collection()` writes `namespace="default"` on creation
- `SearchStore.get_collection_meta()` / `get_all_collections_meta()` return namespace
- `SearchStore.list_collections()` includes namespace in `CollectionInfo`
- Unit tests: schema creation, round-trip read/write, migration (existing → "default"), new collection carries field

## Out of Scope
- Namespace enforcement / filtering — that is 5c
- Key → namespace mapping — that is 5c
- `namespace` parameter on any API endpoint — no callers can set it yet
- Per-chunk namespace field — collection-level is sufficient; 5d can revisit if needed
- `[namespace]` config section — no config until 5c needs it
- Multiple named namespaces — only `"default"` is written in this increment

## Key Decisions
- **Collection-level only, not chunk-level**: 5c enforces isolation at collection query time; adding namespace to every chunk doubles migration scope for zero benefit until 5d (explicitly deferred).
- **Hardcoded `"default"` string**: no config or API parameter in this increment — the value has no effect until 5c enforces it, so no config burden.
- **Idempotent startup migration, not a one-time flag**: eliminates "did the migration run?" ambiguity forever; cost is one lightweight metadata table scan per startup.

## Edge Cases & Constraints
- **LanceDB schema evolution**: LanceDB does not support `ALTER TABLE`. The `_archon_collection_meta` table must be handled by reading all rows, adding the field, dropping and recreating the table with the new schema, and reinserting. Alternatively, write a fallback: if `namespace` column is absent on read, return `"default"` and write it back lazily. **Recommendation: eager migration on startup** — cleaner than lazy backfill scattered across reads.
- **Concurrent startup**: two processes starting simultaneously could both attempt migration. Migration must be idempotent (check-then-write with graceful handling of duplicate writes).
- **Empty `_archon_collection_meta` table**: no rows to migrate — migration is a no-op. New collections created after this point will carry the field.
- **`list_collections()` returns LanceDB table names, not metadata rows**: `namespace` comes from `_archon_collection_meta`; if a collection table exists but has no metadata row, return `namespace="default"` and write the row.

## Open Questions
- LanceDB schema migration strategy needs to be verified against the LanceDB version in use — confirm whether column addition is supported or whether a drop-and-recreate pattern is required.

## Future Iterations
- **5c — Namespace isolation**: middleware extracts namespace from API key, all collection operations filter by caller's namespace.
- **5d — Document/chunk-level trimming**: if per-document access control is needed, chunk table gets its own namespace (or ACL) field at that point.
- **Named namespaces via config or API**: once 5c exists, operators may want to provision namespaces explicitly rather than relying solely on `"default"`.

## Recommendation
This is the right increment to build next — it is purely additive, has no user-visible behaviour change, and unblocks 5c entirely. The hardest part is the LanceDB schema migration for the metadata table; verify the drop-and-recreate pattern works cleanly before starting. Do not skip the migration in favour of lazy backfill — clean foundation now saves pain at every subsequent read.
