# FEAT-044 — Document/Chunk-Level Security Trimming (5d)
**Purpose**: Add per-document ACL enforcement to archon-search so operators can mark individual documents as restricted at ingest time and have ACL-denied chunks silently dropped before reranking.
**Audience**: Implementers; security reviewers.
**Status**: Draft

---

## Background

FEAT-041 (5a) added API key authentication. FEAT-042 (5b) added a `namespace` column to collection metadata. FEAT-043 (5c) enforced per-namespace isolation at every HTTP endpoint — a key bound to namespace A cannot read or write resources belonging to namespace B.

Within a namespace, however, every authenticated caller can retrieve any indexed chunk. There is no way to mark individual documents as restricted (drafts, confidential policy docs, archived material) or to prepare per-document access policies before shared collections become available in a future increment.

This feature (5d) adds chunk-level ACL enforcement. The identity model stays the same (namespace names as ACL identifiers, consistent with 5c). The mechanism is a nullable `acl` column on every chunk row: `null` = default-open; a list of namespace names = allow-listed; `[]` = deny-all. ACL filtering happens after `hybrid_search()` produces candidates and before the cross-encoder reranker scores them, so denied chunks never consume reranker budget.

The `POST /search` response is wrapped in a `{ results, acl_filtered }` envelope — a deliberate breaking change made before item 6 freezes the public API. This breaks every caller of `POST /search`, including `SearchClient.search()` in the Archon parent.

---

## Goal

After 5d, an operator can annotate any document with an ACL at ingest time (YAML front matter `_acl:` key for markdown/text files; `.acl` sidecar file for binary files). At query time, chunks whose ACL excludes the caller's namespace are silently dropped before reranking. The `POST /search` response signals when filtering occurred via an `acl_filtered: bool` flag. Operators can audit ACL coverage via `GET /collections/{name}` (`acl_protected_count`, `acl_open_count`). Pre-existing chunks and `acl: null` chunks behave exactly as before — zero behavior change for existing deployments that do not set ACLs.

---

## Scope

### In Scope

- `acl: list[str] | None` column (PyArrow `pa.list_(pa.utf8())`, nullable) in every LanceDB collection chunk table. Startup migration adds the column to pre-existing tables; pre-existing chunks default to `null` (open).
- ACL source: YAML front matter (`_acl` key) for markdown/text files; sidecar `{filename}.acl` for binary files. Front matter takes precedence when both exist.
- Document-level ACL: resolved value is copied to all chunks sharing the same `doc_id`. No per-chunk override.
- `_acl` is parsed from `str`, `list[str]`, or `None`; other YAML types default to open with one WARNING per document.
- Invalid namespace names (failing `^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$`) are dropped; `deny-all` is a reserved word and is handled specially — see `parse_acl_value()` special case in the Architecture section. All-invalid (where no entry is the reserved word `deny-all`) defaults to open (fail-open). One WARNING per document including drop count.
- `.acl` sidecar files are excluded from content indexing. A sidecar containing only `deny-all` (case-insensitive, first non-blank line) → `acl: []` (deny-all). Empty sidecar → `acl: null` (open). Sidecar > 64 KB rejected with WARNING and treated as absent.
- ACL filter placement: after `hybrid_search()`, before cross-encoder reranking.
- `fetch_adjacent_chunks()` (context expansion) results ACL-filtered before inclusion in response. `ChunkRecord` gains `acl: list[str] | None` field.
- `SearchResult` (internal dataclass) gains `acl: list[str] | None` field. Field is NOT exposed in `SearchResultSchema` (HTTP response type). The two types remain separate.
- `POST /search` response wrapped in `SearchResponse { results: list[SearchResultSchema], acl_filtered: bool }`.
- `SearchClient.search()` returns `SearchQueryResult(results: list[dict], acl_filtered: bool)` instead of bare `list[dict]`. Defensive fallback: if response is a bare JSON array (old server), return it with `acl_filtered=False`.
- `SearchContextProvider` receives `acl_filtered`; logs at DEBUG level only; does not surface to user.
- `GET /collections/{name}` gains `acl_protected_count: int` (chunks with non-null ACL) and `acl_open_count: int` (chunks with null ACL).
- `deny-all` reserved as a forbidden namespace name — `_validate_namespace()` must reject it.
- YAML `_acl` key and other standard front matter fields are stripped from document text before chunking; ACL values must not appear in indexed chunk text.
- Sidecar files: same directory as source (no path traversal), symlinks not followed, UTF-8 with BOM stripped.

### Out of Scope

- Per-key ACLs, per-chunk ACL override, ACL revocation endpoint, per-document ACL inspection API
- Cross-namespace shared collections, role/tag-level ACLs, adaptive over-fetch
- Watcher support for sidecar changes
- Telemetry ACL filtering
- CLI ACL support
- `acl_filtered_count` (only boolean)

---

## Acceptance criteria

> Acceptance criteria are verified in the final task. See [Task 6.1 — Final verification & documentation update].

---

## What does NOT change

- `SearchResultSchema` (HTTP response type): no `acl` field added.
- Existing collection namespace isolation (5c): namespace check on `GET/POST /collections` is unchanged.
- `SearchContextProvider` does not perform its own ACL filtering — the `acl_filtered` flag received from `SearchQueryResult` is logged at DEBUG level only and does not trigger additional behavior. However, ACL filtering has already occurred upstream (in `POST /search` via the server, and in `SearchPipeline.search()` for pipeline callers) — `SearchContextProvider` receives only chunks that are permitted for the caller's namespace.
- `SearchClient` method signatures other than `search()` return type.
- Telemetry endpoints (`GET /telemetry/stats`, `GET /telemetry/entries`): unfiltered, unchanged.
- CLI commands: no `--namespace` or ACL filtering.
- `doc_count` in `GET /collections/{name}`: counts all documents regardless of ACL.

---

## Known limitations / accepted trade-offs

- **At-most `top_k` semantics**: filtered results may be fewer than `top_k`. No adaptive re-fetch. `acl_filtered: true` is the signal.
- **FTS index includes denied chunks**: BM25 ranking signals are computed over all chunks; ACL-restricted chunks influence ranking order but are never returned.
- **Over-fetch under heavy restriction**: if >66% of chunks are ACL-denied for the caller, results will consistently be fewer than `top_k`. Push-to-store optimization (`WHERE acl IS NULL OR list_contains(acl, :namespace)`) is deferred.
- **Sidecar `deny-all` sentinel**: `deny-all` is reserved and must not be used as a namespace name. Operators who do so lose the ability to grant sidecar access to that namespace.
- **Multi-namespace ACL values are inert in 5d**: If `_acl: tenantA,tenantB` is set on a `tenantA`-owned collection, `tenantB` callers are blocked by 5c and never reach the ACL check. Stored correctly; will take effect when cross-namespace shared collections are implemented.
- **ACL immutability**: changing an ACL requires re-ingest. Accepted as correct for 5d.
- **Breaking API change**: `POST /search` response shape changes from `list[SearchResultSchema]` to `SearchResponse`. All callers (including `SearchClient`) must be updated in the same release.
- **`deny-all` as a namespace name in `_acl`**: Using `deny-all` as a namespace name in `_acl` (e.g., `_acl: deny-all`) is interpreted as deny-all intent, not as granting access to a namespace literally named `deny-all`. This is consistent with the reservation of `deny-all` as a forbidden namespace name.
- **`_acl: null` vs absent `_acl`**: YAML `_acl: null` and absent `_acl` are indistinguishable after `front_matter.pop('_acl', None)` — both return `None`. If a document has `_acl: null` in front matter AND a sidecar, the sidecar will be used (because `resolve_acl` cannot distinguish `None` from absent). Operators who want to explicitly override a sidecar with no restriction should remove the sidecar instead. This is an accepted limitation for 5d.

---

## Architecture

### New module

**`packages/archon-search/archon_search/acl.py`** — standalone ACL utilities (no I/O, pure functions + one file reader):
- `is_acl_namespace_valid(name: str) -> bool` — wraps `_NAMESPACE_RE.fullmatch(name)` and rejects `"deny-all"`
- `parse_acl_value(raw: Any, doc_path: str) -> list[str] | None` — normalizes YAML value (`str` → split on commas AND newlines using `re.split(r'[,\n]', raw.strip())`, strip whitespace from each token; `list[str]` → direct, non-str elements count as invalid; other types → `None` + WARNING); validates each entry; returns `None` on all-invalid or empty/absent. **Special case — `deny-all` as the sole namespace**: if the input `_acl` value (after splitting and stripping) consists ONLY of entries equal to `deny-all` (and no valid namespace names), return `[]` (deny-all) rather than `None` (fail-open). Rationale: an operator who writes `_acl: deny-all` almost certainly intends deny-all semantics — silently making this fail-open is a security inversion. Log a WARNING: `_acl in {doc_path} contains the reserved word 'deny-all' as a namespace name; interpreting as deny-all (acl: [])`. If mixed with valid namespace names (`_acl: deny-all,tenantA`), the `deny-all` entry is dropped with a warning and the valid names are used. **Mixed `deny-all` + invalid entries**: if `deny-all` appears together with other invalid names and no valid namespace names (e.g., `_acl: 'deny-all,!!!bad!!!'`), the behavior is fail-open (`None`), not deny-all. Rationale: the deny-all special case only applies when `deny-all` is the SOLE non-valid entry, making the operator intent unambiguous. Mixed cases are treated as all-invalid (fail-open), with a WARNING.
- `read_acl_sidecar(doc_path: Path) -> list[str] | None` — reads `{doc_path}.acl`; detects `deny-all` sentinel; validates lines; size limit 64 KB; returns `None` on empty/absent/invalid
- `resolve_acl(doc_path: Path, front_matter_acl: Any) -> list[str] | None` — precedence: front matter → sidecar; logs WARNING when both exist. The caller (`ingest_file()`) is responsible for extracting the `_acl` value from the front matter dict before calling `resolve_acl()`.
- `is_acl_allowed(acl: list[str] | None, namespace: str) -> bool` — `None` → `True`; `[]` → `False`; `namespace in acl` → result; falls back to `False` if `namespace` is empty
- `apply_acl_filter(items: list[_T], get_acl: Callable[[_T], list[str] | None], namespace: str) -> tuple[list[_T], bool]` — generic filter over any list of ACL-bearing objects; returns `(filtered_items, any_dropped)`

### Modified types (`_types.py`)

- `ChunkRecord`: add `acl: list[str] | None = None`
- `SearchResult`: add `acl: list[str] | None = None`

### Modified store (`store.py`)

- `_schema(embedding_dim)`: add `pa.field("acl", pa.list_(pa.utf8()), nullable=True)`
- `migrate_acl() -> None`: iterate all collection chunk tables (via `_archon_collection_meta`), add `acl` column if absent; catch `RuntimeError` for already-exists case; idempotent
- `hybrid_search()`: include `acl` column in LanceDB scan; populate `SearchResult.acl` from each row
- `fetch_adjacent_chunks()`: include `acl` column in LanceDB scan; populate `ChunkRecord.acl` from each row
- `get_acl_stats(collection: str) -> tuple[int, int]`: scan chunk table, return `(acl_protected_count, acl_open_count)` — protected = rows where `acl IS NOT NULL`, open = rows where `acl IS NULL`

### Modified pipeline (`pipeline.py`)

- `ingest_file()`: resolve ACL via `acl.resolve_acl()`; propagate resolved ACL to all `ChunkRecord` objects produced; strip `_acl` from front matter dict before building chunk text
- Directory/file collection logic: skip files matching `*.acl` pattern (sidecar files must not be indexed as content)
- `search(query, collection, namespace)` and `search_with_context(query, collection, context_window, namespace)`: both gain a required `namespace: str` parameter. `search()` applies `apply_acl_filter(candidates, lambda r: r.acl, namespace)` after `hybrid_search()` and before reranking. `search_with_context()` passes `namespace` to `self.search()` and additionally filters `fetch_adjacent_chunks()` results: `apply_acl_filter(adjacent, lambda c: c.acl, namespace)`.

### Modified routes (`server/routes_search.py`)

- `SearchResponse(BaseModel)`: new Pydantic schema `{ results: list[SearchResultSchema], acl_filtered: bool }`
- `search()` route: after `hybrid_search()` and before reranking, call `apply_acl_filter(candidates, lambda r: r.acl, namespace)`; wrap final response in `SearchResponse`; update `response_model=SearchResponse`

> **RC-02 NOTE — context expansion code path**: `fetch_adjacent_chunks()` for context expansion does NOT currently exist in the `POST /search` HTTP route — it is only called from `SearchPipeline.search_with_context()` in `pipeline.py`. For 5d, adjacent chunk ACL filtering applies ONLY to the `SearchPipeline.search_with_context()` code path (see Task 3.4b). If `fetch_adjacent_chunks()` is ever added to the HTTP route in a future increment, ACL filtering must be applied there too.

### Modified routes (`server/routes_collections.py`)

- `get_collection_info()`: call `store.get_acl_stats(name)`, add `acl_protected_count` and `acl_open_count` to the JSON response

### Modified constants (`constants.py`)

- `_validate_namespace()`: add check `if name == "deny-all": raise ValueError(...)`

### Modified lifespan (`server/app.py`)

- After `migrate_namespace()`: add `await app.state.search_store.migrate_acl()`

### Modified SearchClient (`archon/ai/search_client.py`)

- New `SearchQueryResult(NamedTuple)`: `results: list[dict[str, Any]]`, `acl_filtered: bool`
- `search()` return type: `SearchQueryResult` (was `list[dict[str, Any]]`)
- Response handling: if `isinstance(data, dict)` → unwrap `data["results"]`, `data["acl_filtered"]`; if `isinstance(data, list)` → bare-list fallback, `acl_filtered=False`, log WARNING

### Modified SearchContextProvider (`archon/ai/search_context_provider.py`)

- Update call site of `search_client.search()` to unpack `SearchQueryResult`; log `acl_filtered=True` at DEBUG level

---

## Task breakdown

### Phase 1 — Foundations (data model, constants, schema)
> **Releasable**: after Task 1.5 — startup migration runs cleanly against pre-existing databases; no behavior change yet

#### Task 1.1 — Reserve `deny-all` as a forbidden namespace name
- [x] **File**: `packages/archon-search/archon_search/constants.py`
- **Depends on**: nothing
- **Description**:
  - In `_validate_namespace(name: str) -> None`, add: `if name == "deny-all": raise ValueError("Namespace name 'deny-all' is reserved and cannot be used.")`
  - Must be checked before the regex test (fail fast on known sentinel)
  - Does not affect existing data (no stored namespace is named `deny-all`)
- **Releasable**: after this task, `_validate_namespace` rejects `deny-all` at API creation time
- **Tests (TDD)** — `packages/archon-search/tests/test_constants_acl.py`:
  - Unit: `test_validate_namespace_rejects_deny_all` — `_validate_namespace("deny-all")` raises `ValueError` with message
  - Unit: `test_validate_namespace_allows_valid_names` — normal names still pass (regression)
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_constants_acl.py -v`

#### Task 1.2 — Add `acl` field to `ChunkRecord` and `SearchResult`
- [x] **File**: `packages/archon-search/archon_search/_types.py`
- **Depends on**: nothing
- **Description**:
  - `ChunkRecord`: add `acl: list[str] | None = None` as the last field (default preserves all existing construction sites)
  - `SearchResult`: add `acl: list[str] | None = None` as the last field
  - Both fields default to `None` — no call-site changes required yet; callers that explicitly pass positional args will need updating in later tasks
- **Releasable**: after this task, both dataclasses carry the `acl` field; no behavioral change
- **Tests (TDD)** — `packages/archon-search/tests/test_types_acl.py`:
  - Unit: `test_chunk_record_default_acl_is_none` — `ChunkRecord(...)` without `acl` kwarg → `acl is None`
  - Unit: `test_search_result_default_acl_is_none` — same for `SearchResult`
  - Unit: `test_chunk_record_acl_deny_all` — `ChunkRecord(..., acl=[])` round-trips correctly
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_types_acl.py -v`

#### Task 1.3 — Add `acl` column to LanceDB chunk table schema
- [x] **File**: `packages/archon-search/archon_search/store.py`
- **Depends on**: Task 1.2
- **Description**:
  - In `SearchStore._schema(embedding_dim: int) -> pa.Schema`, append `pa.field("acl", pa.list_(pa.utf8()), nullable=True)` before returning
  - Verify LanceDB accepts `nullable=True` for `pa.list_()` — if not, consult LanceDB docs and use the correct nullability annotation
  - New collections created after this change will have the `acl` column; existing tables are handled by `migrate_acl()` in Task 1.4
  - **Implementer note**: Between Task 1.3 (schema update) and Task 2.4 (ingest path update), `ingest_chunks()` will not include `acl` in row dicts. Verify that the installed LanceDB version inserts `null` for omitted nullable columns — if not, Tasks 1.3 and 2.4 must be applied in the same atomic commit.
- **Releasable**: after this task, newly created collections have the `acl` column in their schema
- **Tests (TDD)** — `packages/archon-search/tests/test_store_acl.py`:
  - Integration: `test_new_collection_has_acl_column` — `ensure_collection()` creates table; LanceDB schema contains `acl` field of correct type
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_store_acl.py::test_new_collection_has_acl_column -v`

#### Task 1.4 — Startup migration: `migrate_acl()`
- [x] **File**: `packages/archon-search/archon_search/store.py`
- **Depends on**: Task 1.3
- **Description**:
  - Add `async def migrate_acl(self) -> None` to `SearchStore`
  - Enumerate all collection names by scanning `_archon_collection_meta` (the same table used by `migrate_namespace()`)
  - For each collection, open its LanceDB chunk table and check if `acl` column exists
  - If absent: add the `acl` column with `pa.list_(pa.utf8())` type; all existing rows get `null`
  - **Implementer note**: Before writing `migrate_acl()`, verify the correct DuckDB/LanceDB SQL type name for a nullable list-of-strings column. Try `add_columns({'acl': 'cast(NULL as VARCHAR[])'})` on the installed LanceDB version. If this fails, consult LanceDB docs for the `add_columns` API with nullable list types. The LanceDB version in use is pinned in `packages/archon-search/pyproject.toml` — check the installed version's API first.
  - Catch `RuntimeError` (already exists, concurrent startup); log WARNING and continue
  - Idempotent: safe to call multiple times
  - In `server/app.py` lifespan: call `await app.state.search_store.migrate_acl()` immediately after `migrate_namespace()`
- **Releasable**: after this task, pre-existing databases are migrated on startup; pre-existing chunks default to `acl=null` (open)
- **Tests (TDD)** — `packages/archon-search/tests/test_store_acl.py`:
  - Integration: `test_migrate_acl_adds_column_to_existing_tables` — create collection without `acl` column, call `migrate_acl()`, verify column added and existing rows have `null`
  - Integration: `test_migrate_acl_idempotent` — call `migrate_acl()` twice without error
  - Integration: `test_migrate_acl_skips_nonexistent_meta_table` — fresh store with no collections runs cleanly
  - Integration: `test_app_lifespan_calls_migrate_acl` — start the app (or mock the lifespan) — verify `store.migrate_acl()` is called after `migrate_namespace()` during startup
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_store_acl.py -v`

---

### Phase 2 — Ingest path
> **Releasable**: after Task 2.4 — documents with `_acl` annotations are ingested with ACL metadata; sidecar files are excluded from content indexing

#### Task 2.1 — ACL parsing utilities: `parse_acl_value()` and `is_acl_namespace_valid()`
- [x] **File**: `packages/archon-search/archon_search/acl.py` (new file)
- **Depends on**: Task 1.1
- **Description**:
  - `is_acl_namespace_valid(name: str) -> bool`: returns `_NAMESPACE_RE.fullmatch(name) is not None and name != "deny-all"` (import `_NAMESPACE_RE` from `constants`)
  - `parse_acl_value(raw: Any, doc_path: str) -> list[str] | None`:
    - `None` → return `None`
    - `str` → split on commas AND newlines using `re.split(r'[,\n]', raw.strip())` (handles YAML block scalars that produce `\n`-separated strings), strip whitespace from each resulting token
    - `list` → validate each element is a `str`; non-str elements (e.g., `int`, `None`, `bool`) count as invalid and are dropped with WARNING
    - Any other type (including `bool` from YAML `true`/`false`) → log `WARNING: _acl in {doc_path} has invalid type {type(raw).__name__} (ignored); chunk defaults to open` → return `None`
    - After building the candidate list, filter with `is_acl_namespace_valid()`; collect drop count
    - If any entries were dropped: log `WARNING: _acl in {doc_path} has {drop_count} invalid namespace names (dropped); chunk defaults to open` (one WARNING per document regardless of drop count)
    - **Special case — `deny-all` as the sole namespace**: if ALL entries are the reserved word `deny-all` (after filtering invalid names, only `deny-all` entries remain, no valid namespace names), return `[]` (deny-all) and log `WARNING: _acl in {doc_path} contains the reserved word 'deny-all' as a namespace name; interpreting as deny-all (acl: [])`. If `deny-all` is mixed with valid names, drop `deny-all` with a warning and use the valid names. **Mixed `deny-all` + invalid entries**: if `deny-all` appears together with other invalid names and no valid namespace names (e.g., `_acl: 'deny-all,!!!bad!!!'`), the behavior is fail-open (`None`), not deny-all — mixed cases are treated as all-invalid (fail-open), with a WARNING.
    - If result list is empty after filtering (including originally empty `[]`): return `[]` for empty input (deny-all), `None` for all-invalid (fail-open)
    - Note: `[]` from YAML (`_acl: []`) → already empty after parsing → return `[]` (deny-all intentional)
- **Releasable**: after this task, `parse_acl_value()` is callable by ingest pipeline
- **Tests (TDD)** — `packages/archon-search/tests/test_acl.py`:
  - Unit: `test_parse_acl_value_string_comma_separated`
  - Unit: `test_parse_acl_value_list`
  - Unit: `test_parse_acl_value_none`
  - Unit: `test_parse_acl_value_int_defaults_open_with_warning`
  - Unit: `test_parse_acl_value_invalid_names_dropped_with_warning`
  - Unit: `test_parse_acl_value_all_invalid_defaults_open`
  - Unit: `test_parse_acl_value_empty_list_returns_deny_all`
  - Unit: `test_parse_acl_value_strips_whitespace`
  - Unit: `test_parse_acl_value_deny_all_name_rejected`
  - Unit: `test_parse_acl_value_deny_all_sole_entry_returns_deny_all` — `"deny-all"` as the sole value → `[]` + WARNING
  - Unit: `test_parse_acl_value_newline_separated` — `"tenantA\ntenantB"` → `["tenantA", "tenantB"]`
  - Unit: `test_parse_acl_value_mixed_valid_and_invalid` — `["tenantA", "!!!bad!!!", "tenantB"]` → `["tenantA", "tenantB"]` + WARNING
  - Unit: `test_parse_acl_value_list_with_nonstring_elements` — `[42, "tenantA", None]` → `["tenantA"]` + WARNING
  - Unit: `test_parse_acl_value_bool_defaults_open_with_warning` — `True` → `None` + WARNING
  - Unit: `test_parse_acl_value_deny_all_mixed_with_invalid_fails_open` — `"deny-all,!!!bad!!!"` → `None` (fail-open) + WARNING (not deny-all, because mixed with other invalid)
  - Unit: `test_is_acl_namespace_valid_blocks_deny_all`
  - Unit: `test_is_acl_namespace_valid_blocks_invalid_chars`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_acl.py::test_parse_acl -v && uv run pytest tests/test_acl.py::test_is_acl_namespace_valid -v`

#### Task 2.2 — Sidecar reader: `read_acl_sidecar()`
- [x] **File**: `packages/archon-search/archon_search/acl.py`
- **Depends on**: Task 2.1
- **Description**:
  - `read_acl_sidecar(doc_path: Path) -> list[str] | None`:
    - Sidecar path: `doc_path.parent / (doc_path.name + ".acl")`
    - If not exists → return `None`
    - Check `sidecar_path.is_symlink()` → if symlink: log WARNING, return `None` (no symlink follow)
    - Read as bytes; if size > 65536: log `WARNING: .acl sidecar for {doc_path} exceeds 64 KB limit; ignored`, return `None`
    - Decode as UTF-8, strip BOM (`﻿`)
    - Split on newlines; strip whitespace from each; filter empty lines
    - If no non-empty lines → return `None` (empty sidecar = open)
    - If first non-empty line (case-insensitive) == `"deny-all"` → return `[]`; if additional non-empty lines follow, log `WARNING: .acl sidecar for {doc_path} has content after 'deny-all' sentinel; additional lines ignored`
    - Otherwise: validate each line with `is_acl_namespace_valid()`; collect valid names; drop invalid names
    - If any invalid lines: log WARNING with count
    - Return valid names or `None` if all invalid
  - `resolve_acl(doc_path: Path, front_matter_acl: Any) -> list[str] | None`:
    - If `front_matter_acl` is not `None` (key was present in front matter): call `parse_acl_value(front_matter_acl, str(doc_path))`; also check if sidecar exists — if it does, log `WARNING: both front matter _acl and sidecar {sidecar_path} found for {doc_path}; using front matter`
    - Else: call `read_acl_sidecar(doc_path)`
    - Return result
- **Releasable**: after this task, all ACL parsing utilities are ready
- **Tests (TDD)** — `packages/archon-search/tests/test_acl.py`:
  - Unit: `test_read_acl_sidecar_namespace_list`
  - Unit: `test_read_acl_sidecar_deny_all_sentinel`
  - Unit: `test_read_acl_sidecar_deny_all_case_insensitive`
  - Unit: `test_read_acl_sidecar_empty_returns_none`
  - Unit: `test_read_acl_sidecar_absent_returns_none`
  - Unit: `test_read_acl_sidecar_size_limit`
  - Unit: `test_read_acl_sidecar_bom_stripped`
  - Unit: `test_read_acl_sidecar_invalid_lines_dropped`
  - Unit: `test_read_acl_sidecar_symlink_returns_none`
  - Unit: `test_read_acl_sidecar_deny_all_with_trailing_lines` — sidecar with `deny-all\nns1\nns2` → `[]` + WARNING about trailing content
  - Unit: `test_read_acl_sidecar_invalid_utf8_returns_none` — sidecar with binary content (invalid UTF-8 bytes) → `None` + WARNING (no `UnicodeDecodeError` propagation)
  - Unit: `test_resolve_acl_front_matter_takes_precedence`
  - Unit: `test_resolve_acl_sidecar_used_when_no_front_matter`
  - Unit: `test_resolve_acl_explicit_null_front_matter_falls_through_to_sidecar` — `resolve_acl(path, None)` with a sidecar present → sidecar value is used (because `None` and absent `_acl` are indistinguishable); documents the accepted limitation from Known Limitations section
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_acl.py -v`

#### Task 2.3 — ACL filter utilities: `is_acl_allowed()` and `apply_acl_filter()`
- [x] **File**: `packages/archon-search/archon_search/acl.py`
- **Depends on**: Task 2.2 (same file, sequential)
- **Description**:
  - `is_acl_allowed(acl: list[str] | None, namespace: str) -> bool`:
    - `acl is None` → `True` (default-open)
    - `acl == []` → `False` (deny-all)
    - `not namespace` → `False` (empty namespace = authorization failure → fail-closed for protected chunks)
    - `namespace in acl` → `True`; else `False`
    - Comparison is case-sensitive (no `.lower()`)
  - `_T = TypeVar("_T")` at module level
  - `apply_acl_filter(items: list[_T], get_acl: Callable[[_T], list[str] | None], namespace: str) -> tuple[list[_T], bool]`:
    - Filter `items` by `is_acl_allowed(get_acl(item), namespace)`
    - Return `(passing_items, any(not is_acl_allowed(get_acl(i), namespace) for i in items))`
- **Releasable**: after this task, filter utilities are available for the query path
- **Tests (TDD)** — `packages/archon-search/tests/test_acl.py`:
  - Unit: `test_is_acl_allowed_null_open`
  - Unit: `test_is_acl_allowed_deny_all`
  - Unit: `test_is_acl_allowed_match`
  - Unit: `test_is_acl_allowed_no_match`
  - Unit: `test_is_acl_allowed_case_sensitive`
  - Unit: `test_is_acl_allowed_empty_namespace_denies_protected`
  - Unit: `test_is_acl_allowed_none_namespace` — `namespace=None` passed as str → treated as falsy, returns `False` for protected chunks
  - Unit: `test_apply_acl_filter_removes_denied`
  - Unit: `test_apply_acl_filter_all_open`
  - Unit: `test_apply_acl_filter_deny_all`
  - Unit: `test_apply_acl_filter_empty_list` — `apply_acl_filter([], ...)` → `([], False)` (boundary condition)
  - Unit: `test_apply_acl_filter_all_denied` — all items have non-matching ACL → `([], True)`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_acl.py -v`

#### Task 2.4 — Integrate ACL into `ingest_file()` + filter `.acl` sidecar files
- [x] **File**: `packages/archon-search/archon_search/pipeline.py`
- **Depends on**: Tasks 1.2, 2.2
- **Description**:
  - **Skip `.acl` sidecar files**: wherever the ingest pipeline collects files for indexing (directory scan in `_default_ingest_task` or `ingest_directory()`), add a guard: `if path.suffix == ".acl" or path.name.endswith(".acl"): continue` (skip, do not log — sidecar files are expected, not errors)
  - **IMPORTANT — front matter extraction**: The current `ingest_file()` pipeline does not extract YAML front matter — the raw text including any `---` block is passed directly to the chunker. This task must first add front matter extraction: (1) detect the `---...---` front matter block at the top of the file, (2) parse it as YAML to produce a `dict`, (3) strip the front matter block from the text before chunking. This is new functionality, not a modification of existing code.
  - **File type scoping**: restrict front matter extraction to known text file types (e.g., `.md`, `.txt`, `.rst`, `.html`). For binary files (PDF, images, etc.), the parser returns extracted text which may begin with `---` by coincidence — these must NOT be subjected to front matter detection. Check `path.suffix.lower()` before attempting YAML front matter extraction.
  - **In `ingest_file()`**:
    1. After extracting YAML front matter (as described above), call `_acl = front_matter.pop("_acl", None)` — `pop` removes it from the dict so it is not included in chunk text
    2. If the document type does not support front matter (binary files), set `_acl = None`
    3. Call `resolved_acl = resolve_acl(path, _acl)` from `archon_search.acl`
    4. When constructing each `ChunkRecord` for this document, pass `acl=resolved_acl`
  - **In `ingest_chunks()` in `store.py`**: add `"acl": c.acl` to the row dict for each `ChunkRecord` so the ACL value is persisted to LanceDB. Without this step, the `acl` field on `ChunkRecord` is set but never written to the database.
  - Ensure that existing front matter stripping logic strips the entire front matter block from chunk text (not just `_acl`). If front matter stripping is already in place, verify `_acl` does not appear in chunk text via the test below.
- **Releasable**: after this task, documents with `_acl` are ingested with ACL metadata propagated to all their chunks; `.acl` files are excluded from content indexing
- **Tests (TDD)** — `packages/archon-search/tests/test_pipeline_acl.py` and `packages/archon-search/tests/test_store_acl.py`:
  - Integration: `test_ingest_file_front_matter_acl_propagated_to_chunks`
  - Integration: `test_ingest_file_sidecar_acl_propagated_to_chunks`
  - Integration: `test_ingest_file_strips_acl_from_chunk_text`
  - Integration: `test_ingest_file_skips_acl_sidecar_files`
  - Integration: `test_ingest_file_front_matter_precedence_over_sidecar`
  - Integration: `test_ingest_file_empty_sidecar_defaults_open`
  - Integration: `test_ingest_file_deny_all_sidecar`
  - Integration: `test_ingest_file_all_invalid_acl_defaults_open_with_warning`
  - Integration: `test_reingest_updates_acl_for_all_chunks`
  - Integration: `test_ingest_binary_file_no_front_matter_parsing` — PDF or binary file whose extracted text begins with `---` — `_acl` field is `None` (not misdetected as front matter); sidecar is checked for ACL
  - Integration: `test_ingest_file_front_matter_block_stripped_from_chunk_text` — document with `---` YAML front matter block; `---` delimiters and their content do not appear in any indexed chunk text
  - Integration: `test_ingest_chunks_serializes_acl_field` (in `test_store_acl.py`) — `ingest_chunks()` with a `ChunkRecord` carrying `acl=["ns1"]` persists the ACL value to LanceDB
  - Integration: `test_ingest_chunks_serializes_deny_all_acl` (in `test_store_acl.py`) — `ingest_chunks()` with a `ChunkRecord` carrying `acl=[]` (deny-all) persists the value correctly; reading back the row returns `acl==[]`, not `None`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_pipeline_acl.py tests/test_store_acl.py -v`

---

### Phase 3 — Query path
> **Releasable**: after Task 3.4b — `POST /search` enforces ACL filtering and returns the `SearchResponse` envelope; pipeline-level ACL filtering is also live

#### Task 3.1 — Update `hybrid_search()` to return `acl` field in `SearchResult`
- [x] **File**: `packages/archon-search/archon_search/store.py`
- **Depends on**: Tasks 1.2, 1.3
- **Description**:
  - In `hybrid_search()`, update the LanceDB scan/query to also select the `acl` column (add `"acl"` to the column selection list, if any; or verify it is included by default in the full-row scan)
  - When mapping each LanceDB row to a `SearchResult`, extract `acl` value: convert `None` to `None`; convert a LanceDB `list` value to `list[str]`; handle the case where the column may not exist (pre-migration row) by defaulting to `None`
  - Pass `acl=row_acl` to `SearchResult(...)` constructor
- **Releasable**: after this task, `hybrid_search()` results carry `acl` metadata
- **Tests (TDD)** — `packages/archon-search/tests/test_store_acl.py`:
  - Integration: `test_ingest_and_hybrid_search_returns_acl_field` — ingest chunk with `acl=["ns1"]`; `hybrid_search()` result carries `acl=["ns1"]`
  - Integration: `test_hybrid_search_null_acl_chunk` — open chunk → `acl=None` in result
  - Integration: `test_hybrid_search_row_missing_acl_column_defaults_none` — if a LanceDB row has no `acl` key (pre-migration scenario), `hybrid_search()` returns `SearchResult` with `acl=None` — verifies `.get("acl")` is used, not `["acl"]`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_store_acl.py::test_ingest_and_hybrid_search -v`

#### Task 3.2 — Update `fetch_adjacent_chunks()` to return `acl` field in `ChunkRecord`
- [x] **File**: `packages/archon-search/archon_search/store.py`
- **Depends on**: Tasks 1.2, 1.3
- **Description**:
  - In `fetch_adjacent_chunks()`, update the LanceDB scan to include the `acl` column
  - When mapping each row to `ChunkRecord`, extract `acl` with the same null-safety logic as Task 3.1
  - Pass `acl=row_acl` to `ChunkRecord(...)` constructor
- **Releasable**: after this task, `fetch_adjacent_chunks()` results carry `acl` metadata, ready for filtering in the route handler
- **Tests (TDD)** — `packages/archon-search/tests/test_store_acl.py`:
  - Integration: `test_fetch_adjacent_chunks_returns_acl_field`
  - Integration: `test_fetch_adjacent_chunks_missing_acl_defaults_none` — row without `acl` column produces `ChunkRecord.acl = None`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_store_acl.py::test_fetch_adjacent -v`

#### Task 3.3 — `SearchResponse` Pydantic schema
- [x] **File**: `packages/archon-search/archon_search/server/routes_search.py`
- **Depends on**: nothing (pure schema definition)
- **Description**:
  - Add `class SearchResponse(BaseModel): results: list[SearchResultSchema]; acl_filtered: bool` to `routes_search.py`
  - Do not yet use it — that happens in Task 3.4
  - Confirm `SearchResultSchema` does NOT gain an `acl` field (keep types separate)
- **Releasable**: after this task, the schema is defined and importable
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_search_acl.py`:
  - Unit: `test_search_response_schema_fields` — `SearchResponse(results=[], acl_filtered=False)` serializes to `{"results": [], "acl_filtered": false}`
  - Unit: `test_search_result_schema_no_acl_field` — `SearchResultSchema` does not have an `acl` attribute
  - Unit: `test_search_response_is_never_bare_array` — `POST /search` response body is always a JSON object with `results` and `acl_filtered` keys, never a JSON array
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_routes_search_acl.py::test_search_response_schema -v`

#### Task 3.4 — Apply ACL filter and `SearchResponse` envelope in POST /search
- [x] **File**: `packages/archon-search/archon_search/server/routes_search.py`
- **Depends on**: Tasks 2.3, 3.1, 3.3
- **Description**:
  - Update `search()` route: `response_model=SearchResponse`; return type annotation `-> SearchResponse | JSONResponse`
  - After `hybrid_search()` returns candidates (before reranking):
    - `namespace = request.state.namespace` (set by `APIKeyMiddleware`; empty string if absent)
    - `candidates, acl_filtered = apply_acl_filter(candidates, lambda r: r.acl, namespace)` from `archon_search.acl`
  - Pass the filtered candidates to the cross-encoder reranker (no change to reranker call)
  - Return `SearchResponse(results=[SearchResultSchema.from_result(r) for r in final_results], acl_filtered=acl_filtered)`
  - `SearchResultSchema.from_result()` does not include `acl` field (existing implementation, unchanged)
  - **NOTE**: `fetch_adjacent_chunks()` is NOT called from the HTTP route handler — it is only called from `SearchPipeline.search_with_context()`. Do NOT add adjacent chunk ACL filtering here; that is handled in Task 3.4b.
  - **Update the error handler**: The existing `except Exception` clause returns a bare `[]` (list). After changing `response_model=SearchResponse`, FastAPI will reject a bare `[]` with a Pydantic validation error. Change the error path to return `SearchResponse(results=[], acl_filtered=False)` or a `JSONResponse` with the same shape.
  - **DEPLOYMENT NOTE**: Task 3.4 (server) and Task 5.1 (client) MUST be deployed in the same release. Do not deploy the server alone — until Task 5.1 is also shipped, `SearchClient.search()` will silently return `["results", "acl_filtered"]` instead of result dicts.
- **Releasable**: after this task, `POST /search` enforces chunk-level ACL and returns the envelope
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_search_acl.py`:
  - Integration: `test_search_returns_search_response_envelope`
  - Integration: `test_search_acl_null_always_returned`
  - Integration: `test_search_acl_match_returned`
  - Integration: `test_search_acl_no_match_excluded`
  - Integration: `test_search_acl_deny_all_excluded`
  - Integration: `test_search_acl_filtered_false_when_no_drops`
  - Integration: `test_search_missing_namespace_denies_protected`
  - Integration: `test_search_result_schema_no_acl_field`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_routes_search_acl.py -v`

#### Task 3.4b — Apply ACL filter in `SearchPipeline.search_with_context()` and `SearchPipeline.search()`
- [x] **File**: `packages/archon-search/archon_search/pipeline.py`
- **Depends on**: Tasks 2.3, 3.1, 3.2
- **Description**:
  - **Add `namespace: str` parameter** to both `SearchPipeline.search(query: str, collection: str)` and `SearchPipeline.search_with_context(query: str, collection: str, context_window: int = 1)`. All callers of these methods must be updated to pass namespace.
  - `search_with_context()` calls `self.search()` internally. Because `search()` already applies `apply_acl_filter` on main results, `search_with_context()` does NOT need a second filter on main results — it only needs to filter the `fetch_adjacent_chunks()` results. Do not add `apply_acl_filter` to `search_with_context()` for the main candidates — that would cause double-filtering.
  - Note: this code path is used by internal pipeline callers (e.g., MCP tools), not by the HTTP route handler
  - **CRITICAL**: Update `SearchPipeline.search(query, collection, namespace)` to apply `apply_acl_filter(candidates, lambda r: r.acl, namespace)` after `hybrid_search()` and before reranking. Update `SearchPipeline.search_with_context(query, collection, context_window, namespace)` to pass `namespace` to `self.search()` and apply `apply_acl_filter(adjacent, lambda c: c.acl, namespace)` on `fetch_adjacent_chunks()` results only.
- **Releasable**: after this task, the pipeline-level search paths also enforce ACL filtering
- **Tests (TDD)** — `packages/archon-search/tests/test_pipeline_acl.py` and `packages/archon-search/tests/test_routes_search_acl.py`:
  - Integration: `test_search_with_context_acl_filter_applied` — call `search_with_context()` with a namespace; chunks restricted to other namespaces are excluded from results and adjacent chunks
  - Integration: `test_search_pipeline_search_acl_filter_applied` — call `SearchPipeline.search()` with a namespace; chunks restricted to other namespaces are excluded from results
  - Integration: `test_search_pipeline_search_default_namespace_denies_protected` — call `SearchPipeline.search()` without a namespace (empty string); verify protected chunks are denied (confirming the default is fail-closed, consistent with `is_acl_allowed` behavior for empty namespace)
  - Integration: `test_e2e_ingest_and_search_acl_enforcement` — full pipeline — ingest a file with `_acl: tenantA`; search as `tenantA` namespace (gets the chunk); search as `tenantB` namespace (gets nothing); search with empty namespace (gets nothing for protected, gets open chunks)
  - Integration: `test_search_context_expansion_acl_filtered` — adjacent chunks with ACL-denied ACL excluded from response — NOTE: context expansion (`fetch_adjacent_chunks`) is in `SearchPipeline.search_with_context()`, not the HTTP route; this test covers the pipeline code path

---

### Phase 4 — Collection stats
> **Releasable**: after Task 4.2 — `GET /collections/{name}` exposes ACL coverage statistics

#### Task 4.1 — `get_acl_stats()` in `SearchStore`
- [ ] **File**: `packages/archon-search/archon_search/store.py`
- **Depends on**: Task 1.4
- **Description**:
  - `async def get_acl_stats(self, collection: str) -> tuple[int, int]`:
    - Returns `(acl_protected_count, acl_open_count)` where:
      - `acl_protected_count` = count of rows where `acl IS NOT NULL`
      - `acl_open_count` = count of rows where `acl IS NULL`
    - Open the collection's chunk LanceDB table; scan all rows selecting only the `acl` column; count nulls vs non-nulls using PyArrow
    - If table does not exist (collection not found): return `(0, 0)`
    - Do not filter by namespace — this is an aggregate-only operator endpoint
- **Releasable**: after this task, `get_acl_stats()` is callable from the route handler
- **Tests (TDD)** — `packages/archon-search/tests/test_store_acl.py`:
  - Integration: `test_get_acl_stats_counts_protected_and_open`
  - Integration: `test_get_acl_stats_empty_collection`
  - Integration: `test_get_acl_stats_all_open`
  - Integration: `test_get_acl_stats_all_protected`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_store_acl.py::test_get_acl_stats -v`

#### Task 4.2 — Expose ACL stats in `GET /collections/{name}`
- [ ] **File**: `packages/archon-search/archon_search/server/routes_collections.py`
- **Depends on**: Task 4.1
- **Description**:
  - In `get_collection_info()`, after existing collection meta fetch, call `acl_protected, acl_open = await store.get_acl_stats(name)`
  - Add `"acl_protected_count": acl_protected, "acl_open_count": acl_open` to the JSON response dict
  - Fields are present regardless of whether any ACL is set (both will be `0` for new collections with no ACL annotations)
  - No change to response_model annotation — the endpoint already returns raw `JSONResponse`
- **Releasable**: after this task, operators can audit ACL coverage via `GET /collections/{name}`
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_collections_acl.py`:
  - Integration: `test_get_collection_info_includes_acl_stats`
  - Integration: `test_acl_stats_sum_to_total`
  - Checkpoint: `cd packages/archon-search && uv run pytest tests/test_routes_collections_acl.py -v`

---

### Phase 5 — SearchClient and SearchContextProvider
> **Releasable**: after Task 5.2 — the Archon parent handles the new `SearchResponse` envelope correctly; `acl_filtered` is logged at DEBUG

#### Task 5.1 — Update `SearchClient.search()` for `SearchResponse` envelope
- [ ] **File**: `archon/ai/search_client.py`
- **Depends on**: Task 3.4 — **DEPLOYMENT NOTE**: These two changes MUST be deployed atomically (in the same release). If the server is updated before the client, `SearchClient.search()` will return `["results", "acl_filtered"]` as search results (dict key corruption). The bare-list fallback in Task 5.1 provides client-side protection ONLY when the old server is still running; it does NOT protect against the new server when the client hasn't been updated. Ensure `search_client.py` (Task 5.1) is included in the same release as the archon-search server update (Task 3.4).
- **Description**:
  - Add `class SearchQueryResult(NamedTuple): results: list[dict[str, Any]]; acl_filtered: bool` near the top of the file (or use `@dataclass` if NamedTuple doesn't fit the module style)
  - Update `search()` signature: `async def search(...) -> SearchQueryResult` (was `list[dict[str, Any]]`)
  - Update response handling:
    ```python
    data = resp.json()
    if isinstance(data, dict):
        return SearchQueryResult(results=data.get("results", []), acl_filtered=data.get("acl_filtered", False))
    elif isinstance(data, list):
        logger.warning("search: received bare JSON array from server (old server version?); acl_filtered defaults to False")
        return SearchQueryResult(results=data, acl_filtered=False)
    else:
        return SearchQueryResult(results=[], acl_filtered=False)
    ```
  - On HTTP error (non-2xx, exception): return `SearchQueryResult(results=[], acl_filtered=False)` (same failure behavior as before; never raise)
- **Releasable**: after this task, `SearchClient.search()` correctly handles the new envelope
- **Tests (TDD)** — `tests/ai/test_search_client_acl.py`:
  - Unit: `test_search_client_unwraps_search_response`
  - Unit: `test_search_client_bare_list_fallback`
  - Unit: `test_search_client_returns_empty_on_failure`
  - Unit: `test_search_client_acl_filtered_true_propagated`
  - Unit: `test_search_client_malformed_dict_response` — server returns `{"foo": "bar"}` → `SearchQueryResult(results=[], acl_filtered=False)`, no crash
  - Unit: `test_search_client_results_key_not_a_list` — server returns `{"results": "not_a_list", "acl_filtered": true}` → `SearchQueryResult(results=[], acl_filtered=False)` without crashing
  - Checkpoint: `uv run pytest tests/ai/test_search_client_acl.py -v`

#### Task 5.2 — Update `SearchContextProvider` to handle `acl_filtered`
- [ ] **File**: `archon/ai/search_context_provider.py`
- **Depends on**: Task 5.1
- **Description**:
  - Locate the call site of `search_client.search(...)` in `SearchContextProvider`
  - Unpack the `SearchQueryResult`: `result = await self._search_client.search(...); chunks = result.results; acl_filtered = result.acl_filtered`
  - **CRITICAL**: The `_bounded_search` inner closure (inside `search_and_prepare()`) currently does:
    ```python
    raw = await self._search_client.search(collection, query, cfg.top_k_return)
    return [SearchResult(**r) for r in raw]
    ```
    After Task 5.1, `raw` is a `SearchQueryResult(NamedTuple)`. Iterating a NamedTuple with `for r in raw` yields the tuple's fields (`results_list`, then `acl_filtered_bool`), not the result dicts — `SearchResult(**results_list)` and `SearchResult(**True)` both raise `TypeError`. Change to:
    ```python
    result = await self._search_client.search(collection, query, cfg.top_k_return)
    return [SearchResult(**r) for r in result.results]
    ```
    Also extract `acl_filtered` from `result.acl_filtered` for the DEBUG log. Verify no other call sites iterate the return value directly.
  - If `acl_filtered`: `logger.debug("search: acl_filtered=True for collection %s (namespace %s)", collection, namespace)`
  - The `acl_filtered` flag does NOT change context preparation behavior — same chunks are returned to the caller
  - Update any type annotations that reference the old `list[dict]` return type
- **Releasable**: after this task, the full 5d feature is live end-to-end
- **Tests (TDD)** — `tests/ai/test_search_context_provider_acl.py`:
  - Unit: `test_context_provider_logs_acl_filtered_at_debug`
  - Unit: `test_context_provider_acl_filtered_false_no_log`
  - Unit: `test_context_provider_passes_results_through_unchanged`
  - Checkpoint: `uv run pytest tests/ai/test_search_context_provider_acl.py -v`

---

### Final Phase — Verification & Documentation

#### Task 6.1 — Final verification & documentation update
- [ ] **File**: N/A (agent task)
- **Depends on**: all prior tasks
- **Description**:
  - Spawn an agent to discover all documentation in the project (READMEs, ADRs, API docs, architecture docs, user guides, CLAUDE.md) and update every file whose content is affected by the changes delivered in this plan. The agent must not update docs that are unrelated.
  - Files known to require updates: `Documentation/Backlog/FEAT-037-search-world-class-roadmap.md` (mark item 5d implemented); `CLAUDE.md` section `search_client.py` (update `search()` return type to `SearchQueryResult`).
  - Verify all acceptance criteria below are met before marking this task complete.
- **Releasable**: after this task, the feature is fully verified and all documentation reflects the delivered implementation.
- **Acceptance criteria** (must all pass):
  - Pre-existing chunks are treated as `acl: null` (open) after startup migration; existing deployments with no ACL annotations see zero behavior change.
  - An operator can set `_acl: tenantA,tenantB` in YAML front matter; only callers with namespace `tenantA` or `tenantB` receive those chunks.
  - An operator can set `acl: []` (deny-all) via front matter `_acl: []` or via a sidecar containing only `deny-all`; no caller receives those chunks.
  - `.acl` sidecar files never appear as search results.
  - `_acl` front matter values do not appear in indexed chunk text.
  - `acl_filtered: true` is set on the response when any candidate was dropped; `false` when no candidates were dropped.
  - Adjacent chunks (`fetch_adjacent_chunks`) are ACL-filtered before inclusion in `SearchPipeline.search_with_context()` output (see Task 3.4b).
  - `acl_protected_count + acl_open_count` equals the actual total number of chunks in any collection (verified via `GET /collections/{name}`, NOT via the `chunk_count` field which is hardcoded to 0 in the current implementation).
  - `SearchClient.search()` correctly unwraps `results` from the `SearchResponse` envelope; does not return dict keys on error.
  - `deny-all` is rejected as a namespace name at validation time.
  - ACL matching is case-sensitive: `"TenantA"` does not match `"tenanta"`.
  - Re-ingesting a document with a changed `_acl` value updates all existing chunks for that `doc_id` to the new ACL.
  - Missing `request.state.namespace` (middleware bug) → all ACL-protected chunks denied; only `acl: null` chunks returned.
  - All tests pass: `uv run pytest` exits 0.
  - Test coverage ≥ 85%: `uv run pytest --cov=archon_search --cov-fail-under=85`.
- **Tests (TDD)**: N/A — this is a verification and documentation task.
- **Checkpoint**: manually confirm every acceptance criterion above is checked.
