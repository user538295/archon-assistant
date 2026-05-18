# Feature Brief: Namespace Data Model (5b)

## Problem
archon-search has no concept of namespace — every collection is globally visible and accessible to any authenticated caller. Before namespace isolation (5c) can be enforced, the data model must carry the `namespace` field everywhere a collection is identified or returned.

## Goal
Every collection record carries a `namespace: str` field. All existing collections are automatically migrated to `"default"` on startup. REST responses include the field. No breaking changes for existing callers — the namespace field is an additive addition to REST collection responses.

## Users & Context
Local and multi-tenant deployments of archon-search. Users are not directly aware of this change — it is infrastructure for 5c. The one observable effect: `GET /collections` and `GET /collections/{name}` start returning a `namespace` field.

## Core Flow
1. archon-search starts up.
2. Startup migration is wired into the `app.py` lifespan: `connect()` already exists in the lifespan; the migration call must be added immediately after it, before the `yield`, so no request can observe pre-migration state. For each collection in `_archon_collection_meta`, if `namespace` is absent, set it to `"default"`. Idempotent — safe to run on every startup.
3. New collections created after migration are written with `namespace="default"` (hardcoded — no config, no API parameter yet).
4. All collection REST responses include `"namespace": "default"` in the payload.
5. No filtering or enforcement — all callers see all collections as before.

## In Scope
- Add `namespace: str` field to `CollectionMeta` dataclass (`collection_meta.py`)
- Add `namespace` column to `_archon_collection_meta` LanceDB table schema (`store.py`)
- Add `namespace` field to `CollectionInfo` internal store type (`_types.py`)
- Add `namespace` to all collection REST responses (`routes_collections.py`) — `routes_collections.py` constructs `JSONResponse(content={...})` with inline dicts; `"namespace": "default"` must be added directly to those dict constructions, not via domain type serialization. Note: `GET /collections/` builds its response from config paths (independent of `SearchStore.list_collections()`), so these are two separate scope items with different data sources — both need the field added independently
- Idempotent startup migration: existing collections → `namespace="default"`
- `SearchStore.update_collection_meta()` takes a `CollectionMeta` dataclass (not a raw dict); `CollectionMeta` gets `namespace: str = "default"` as a field with a default value; `update_collection_meta()` includes `"namespace": meta.namespace` in its internal row dict construction alongside all other fields
- `SearchStore.get_collection_meta()` / `get_all_collections_meta()` return namespace
- `SearchStore.list_collections()` includes namespace in `CollectionInfo` — hardcoded `namespace="default"` in the `CollectionInfo` constructor (no meta-table lookup or write; `list_collections()` enumerates tables and counts rows only)
- `_meta_schema()` in `store.py` includes the `namespace` column in the PyArrow schema so fresh installs create the table with the column and never trigger migration
- `_row_to_meta()` in `store.py` reads `namespace` from the row dict during deserialization using `row.get("namespace", "default")` — not `row["namespace"]` — to avoid `KeyError` on rows that haven't been through the migration yet (race window between concurrent processes where one process reads while the other is mid-migration)
- Unit tests: schema creation, round-trip read/write, migration (existing rows → "default"), migration on empty `_archon_collection_meta` table (column added, no rows backfilled), fresh install with no `_archon_collection_meta` table (migration skipped/no-op), already-migrated table (column present — migration is a no-op, idempotency), new collection carries field, `_row_to_meta()` with a row dict missing the `namespace` key returns `namespace="default"`, REST API tests: `GET /collections/` and `GET /collections/{name}` responses include `"namespace": "default"`

## Out of Scope
- Namespace enforcement / filtering — that is 5c
- Namespace field in the `/status` endpoint response — status endpoint collection entries are operational state snapshots, not collection domain records; they may be updated independently in a follow-up
- Key → namespace mapping — that is 5c
- `namespace` parameter on any API endpoint — no callers can set it yet
- Per-chunk namespace field — collection-level is sufficient; 5d can revisit if needed
- `[namespace]` config section — no config until 5c needs it
- Multiple named namespaces — only `"default"` is written in this increment
- `Collection` / `CollectionDetail` types in `types.py` (public API types consumed by `archon/ai/search_client.py`) — `SearchClient` returns raw dicts so the extra field passes through silently; these types will be updated in 5c when enforcement requires namespace-aware client code

## Key Decisions
- **Collection-level only, not chunk-level**: 5c enforces isolation at collection query time; adding namespace to every chunk doubles migration scope for zero benefit until 5d (explicitly deferred).
- **Hardcoded `"default"` string**: no config or API parameter in this increment — the value has no effect until 5c enforces it, so no config burden.
- **Idempotent startup migration, not a one-time flag**: eliminates "did the migration run?" ambiguity forever; cost is one lightweight metadata table scan per startup.

## Edge Cases & Constraints
- **LanceDB schema evolution**: Verified against LanceDB 0.30.2 (installed version). `Table.add_columns({'namespace': "'default'"})` works in-place — it adds the column to the existing table and backfills all existing rows with the SQL expression value in a single operation. No drop-and-recreate required. The migration is: check if `namespace` column absent in `_archon_collection_meta` schema → call `await table.add_columns({'namespace': "'default'"})` → done. The store uses `connect_async()` which returns an `AsyncTable`; on `AsyncTable`, `schema` is an async method: `(await table.schema()).names`. The inner single quotes in `"'default'"` are required — they form a SQL string literal; without them, `'default'` would be interpreted as a column reference, not a string value. Note: `AsyncTable.add_columns()` mirrors the sync `Table` API; verify it accepts the dict signature at implementation time before writing the migration.
- **Concurrent startup**: two processes starting simultaneously could both attempt migration. Both would call `add_columns` on the same table; the second call will fail if the column already exists. This is a TOCTOU race — the schema check is not atomic. The migration must therefore catch the LanceDB exception raised when the column already exists and treat it as idempotent success (not a crash). The guard is: check `'namespace' in (await table.schema()).names`; if absent, call `await table.add_columns({'namespace': "'default'"})` and catch any column-already-exists error.
- **Empty `_archon_collection_meta` table**: no rows to migrate — `add_columns` on an empty table still adds the column to the schema. No-op, no issue.
- **`list_collections()` returns LanceDB table names, not metadata rows**: `namespace` is hardcoded as `"default"` directly in the `CollectionInfo` constructor — no metadata table lookup or write. Orphan-collection reconciliation (writing a stub meta row for collections that exist as tables but have no metadata row) is out of scope for 5b; turning a read-only enumeration method into a write method is bad design and deferred to a dedicated reconciliation step if ever needed.

## Future Iterations
- **5c — Namespace isolation**: middleware extracts namespace from API key, all collection operations filter by caller's namespace.
- **5d — Document/chunk-level trimming**: if per-document access control is needed, chunk table gets its own namespace (or ACL) field at that point.
- **Named namespaces via config or API**: once 5c exists, operators may want to provision namespaces explicitly rather than relying solely on `"default"`.
- **5c concern — upsert key must become `(name, namespace)`**: `update_collection_meta()` currently deletes by `name` alone before inserting. When 5c introduces multiple namespaces, the same collection name could exist in different namespaces; the delete would affect all of them. The upsert key must be changed to `(name, namespace)` in 5c.
- **5c concern — `list_collections()` hardcodes `"default"`**: in 5c, `list_collections()` must be updated to read `namespace` from `_archon_collection_meta` instead of hardcoding; until then, non-default namespaces will not appear correctly in list results.

## Recommendation
This is the right increment to build next — it is purely additive, introduces no breaking changes, and unblocks 5c entirely. The migration path is confirmed clean: LanceDB 0.30.2 `add_columns()` handles in-place column addition with automatic backfill, so no drop-and-recreate is needed. The only implementation care required is the schema-check guard before calling `add_columns` to handle concurrent startup safely.
