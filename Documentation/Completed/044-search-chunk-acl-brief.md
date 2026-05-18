# Feature Brief: Document/Chunk-Level Security Trimming (5d)

## Problem

Within a namespace, all authenticated callers can retrieve any indexed chunk — operators have no way to mark individual documents as restricted (draft, confidential, archived) or to prepare per-document access policies before shared collections become available in a future increment.

## Goal

After 5d, an operator can annotate any document with an ACL at ingest time. At query time, chunks whose ACL excludes the caller's namespace are silently dropped before reranking. The `POST /search` response signals when filtering occurred. Operators can audit ACL coverage via `GET /collections/{name}`.

## Users & Context

**Operator / service administrator**: indexes a corpus that contains a mix of universally accessible and restricted content (drafts, confidential policy docs, archived material). Sets ACLs in document front matter or sidecar files before ingesting. Wants confirmation that ACLs are applied without re-reading every source file.

**API consumer / tenant**: holds a Bearer token that maps to a namespace. Unaware that ACL-restricted chunks exist. Receives fewer than `top_k` results when filtering occurs, with a boolean flag on the response indicating that happened.

**Archon parent process**: uses `SearchClient` with the default key (namespace `"default"`). After 5d, chunks with `acl: null` remain accessible as before — zero behavior change for existing deployments.

## Core Flow

**Ingest path:**

1. Operator adds `_acl` metadata to source documents: YAML front matter for markdown/text files (`_acl: tenantA,tenantB` or `_acl: [tenantA, tenantB]`), or a sidecar file (`report.pdf.acl`) containing namespace names one per line for binary files. Front matter takes precedence when both exist.
2. Operator triggers ingest (via `POST /collections/` or `POST /ingest`).
3. Pipeline reads `_acl` from the document's front matter or sidecar. For YAML front matter, `_acl` may be parsed as a `str`, `list[str]`, `int`, or `None` depending on YAML syntax used:
   - `str` values (e.g., `_acl: tenantA,tenantB`): split on commas; strip whitespace from each token. (Newline splitting applies only if the YAML value uses block scalar syntax, e.g., `_acl: |\n  tenantA\n  tenantB` — parsed by YAML as a `str` with embedded `\n`.)
   - `list[str]` values (e.g., `_acl: [tenantA, tenantB]` or block-style lists): use directly.
   - Any other type (e.g., `int`): treated as absent — ACL defaults to open, one WARNING logged per document.
   - Invalid namespace names (failing the `^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$` regex) are dropped. One WARNING is logged per document (not per invalid entry), including the count of dropped entries and the document path (e.g., `WARNING: _acl in report.pdf has 3 invalid namespace names (dropped); chunk defaults to open`). If all entries are invalid or the field is absent, the chunk defaults to open (`null`).
4. The resolved `list[str] | None` is copied to **every chunk** produced from that document (all chunks share the document-level ACL — there is no per-chunk override in 5d).
5. Chunks are stored in LanceDB with the `acl` column populated.

**Query path:**

1. `POST /search` arrives; middleware sets `request.state.namespace` from the Bearer token.
2. Route handler validates collection exists in caller's namespace (5c check, unchanged).
3. Query is embedded. `hybrid_search()` over-fetches `top_k * 3` candidates via vector + FTS + RRF. Each returned `SearchResult` now carries an `acl: list[str] | None` field (internal only, not in the HTTP response).
4. ACL filter (in route handler, after `hybrid_search`, before reranking):
   - `acl is None` → include (default-open).
   - caller's namespace in `acl` → include.
   - otherwise → exclude.
   - Track whether any candidate was dropped: `acl_filtered = True`.
5. Passing candidates are passed to the cross-encoder reranker, which returns the top `top_k`.
6. Context-expanded chunks (`fetch_adjacent_chunks`) are ACL-checked by the same rule before inclusion in the response. `ChunkRecord` must be extended with an `acl: list[str] | None` field so that returned records carry ACL data for this filtering step.
7. Response: `SearchResponse { results: list[SearchResultSchema], acl_filtered: bool }`.

**Operator audit:**

`GET /collections/{name}` response gains two integer fields: `acl_protected_count` (chunks with a non-null ACL) and `acl_open_count` (chunks with `null` ACL). Operators can verify ACL coverage without re-reading source files.

## In Scope

- `acl: list[str] | None` column in every LanceDB collection table. Startup migration adds the column to pre-existing tables; pre-existing chunks default to `null` (open).
- ACL source: YAML front matter (`_acl` key) for markdown/text files; sidecar `{filename}.acl` for binary files. Front matter takes precedence when both exist.
- Document-level ACL: the resolved value is copied to all chunks with the same `doc_id`. No per-chunk override.
- ACL identifiers are namespace names only (consistent with 5c identity model).
- Null/absent = default-open. Empty list (`[]`) = deny-all (no caller can retrieve these chunks).
- Filter placement: after `hybrid_search()`, before cross-encoder reranking. At-most `top_k` semantics (not exactly `top_k`).
- `fetch_adjacent_chunks()` (context expansion): ACL-checked before inclusion. `ChunkRecord` must be extended with an `acl: list[str] | None` field to carry ACL data for this filtering step.
- `SearchResult` (the archon-search server's internal dataclass, distinct from `SearchContextProvider.SearchResult` in the Archon parent) gains an internal `acl: list[str] | None` field; field is NOT exposed in the HTTP response schema. `SearchResultSchema` (the HTTP response type) must remain a separate type — do not reuse `SearchResult` as the HTTP schema. All code that constructs `SearchResult` (in `hybrid_search()`, tests, and any utility code) must pass the new field.
- `POST /search` response wrapped in `SearchResponse { results, acl_filtered: bool }` envelope. `SearchClient.search()` returns a new typed result `(results: list[dict], acl_filtered: bool)` instead of a bare list. If `SearchClient.search()` receives a bare JSON array (old server not yet updated), it must detect this case (`isinstance(data, list)`) and return it unchanged with `acl_filtered=False` as a defensive fallback — this is not a supported mode but prevents silent corruption in partial-upgrade scenarios. `SearchContextProvider` receives `acl_filtered` and MAY log it at DEBUG level; it does NOT surface it to the user as a notification. The `acl_filtered` flag in the Archon parent is informational only for 5d — it does not change context preparation behavior. Deployment must be coordinated: archon-search and the Archon parent must be updated in the same release — no mixed-version operation is safe (the current `list(resp.json())` on a dict response returns dict keys, silently corrupting results).
- `GET /collections/{name}` response gains `acl_protected_count: int` and `acl_open_count: int`.
- Invalid `_acl` entries (fail namespace regex) dropped with WARNING at ingest; one WARNING per document (not per entry) including the count of dropped entries; all-invalid defaults to open.
- `.acl` sidecar files are excluded from content indexing. The ingest pipeline must filter out any file whose name matches the `*.acl` sidecar pattern.
- YAML front matter containing `_acl` (and other standard front matter fields) is stripped from document text before chunking. ACL values must not appear in indexed chunk text.
- Startup migration: add the `acl` column (PyArrow type `pa.list_(pa.utf8())`, nullable) to all existing LanceDB collection chunk tables. Unlike the 5b namespace migration (which added a column to one metadata table), this migration must iterate every collection's chunk table — one per collection. The SQL expression for a null list column must be verified against LanceDB docs before implementation; `add_columns({'acl': None})` may need to be replaced with a PyArrow schema alteration approach if LanceDB does not accept a NULL default for list-typed columns.
- Sidecar files must be in the same directory as their source document (no path traversal). Symlinks are not followed when reading sidecar files. Sidecar files larger than 64 KB are rejected with a WARNING and treated as absent. Sidecar content is read as UTF-8 (BOM stripped if present).

## Out of Scope

- **Key-level ACLs**: a key cannot have finer identity than its namespace. All keys in a namespace are treated equally. Per-key restrictions require a new identity model increment.
- **Revocation without re-ingest**: there is no `PATCH /chunks/{id}/acl` or document-level ACL update endpoint. Changing an ACL requires re-ingesting the document. Documented as a known operational constraint.
- **Per-chunk ACL override**: all chunks from a document share the same ACL. Intra-document chunk-level granularity is deferred.
- **ACL inspection API** (`GET /collections/{name}/documents`): no per-document ACL read endpoint. Aggregate stats in `GET /collections/{name}` provide audit coverage; per-document inspection is a future increment.
- **Cross-namespace ACL grants**: even if `acl: ["tenantB"]` is set on a chunk owned by a `"tenantA"` collection, `"tenantB"` callers are blocked from accessing the collection by 5c middleware. Cross-namespace shared collections are a future item. **Warning**: operators who write multi-namespace ACL values (e.g., `_acl: tenantA,tenantB` on a `tenantA`-owned collection) should be aware that `tenantB` cannot access the collection at all under 5c namespace isolation. The ACL value is stored correctly and will take effect when cross-namespace shared collections are implemented, but it has no effect in 5d.
- **Telemetry ACL filtering**: `GET /telemetry/entries` and `GET /telemetry/stats` remain unfiltered. Telemetry may reference collection names that include ACL-restricted chunks.
- **CLI ACL support**: CLI commands bypass HTTP auth and have admin-level access; no `--namespace` or ACL filtering is added to CLI in 5d.
- **`acl_filtered_count`** (number of dropped candidates): only a boolean flag is returned. Revealing the count leaks information about denied chunks.
- **Sidecar file change detection by watcher**: The file watcher (`watcher.py`) monitors source files for changes. Modifying a `.acl` sidecar file does NOT trigger re-ingest of the source document. Operators must either also touch the source file or trigger a manual re-ingest. Watcher support for sidecar changes is deferred.
- **MCP tool search paths**: If the search server exposes MCP tools that invoke search directly, those tools must also apply ACL filtering using the caller's namespace. In 5d, ACL filtering is implemented in the HTTP route handler. If MCP tools call `SearchPipeline.search()` or `SearchStore.hybrid_search()` directly, they must apply the same filter step. ACL bypass via MCP is explicitly not acceptable; this is a known implementation checklist item.

## Key Decisions

- **Namespace names as ACL identifiers**: consistent with 5c; ACLs survive key rotation because namespace names (not key hex values) are stored. Operator-familiar identity unit.
- **Fail-open on invalid `_acl` entries**: All-invalid `_acl` defaults to `null` (open), not `[]` (deny-all). This matches the behavior of absent `_acl` and avoids silently locking out data on typos. The tradeoff: an operator who fat-fingers a namespace name gets open chunks rather than denied chunks. The WARNING log is the primary operator signal. Operators with strict security requirements should validate ACL entries against their namespace list before ingesting.
- **Document-level ACL, copied to all chunks**: no per-chunk granularity in 5d. Simpler ingest, predictable behavior — a document is either accessible or not as a whole.
- **Filter before reranking**: ACL filtering happens after `hybrid_search()` produces candidates and before the cross-encoder reranker scores them. Avoids burning reranker budget on denied chunks.
- **At-most semantics**: filtered results may be fewer than `top_k`. No retry/adaptive re-fetch. The `acl_filtered: bool` flag tells the caller filtering occurred.
- **`SearchResponse` envelope**: `POST /search` now returns `{ results, acl_filtered }` instead of a bare array. Breaking change, but made deliberately before item 6 freezes the public API. The envelope provides a natural home for future metadata fields (`query_time_ms`, `total_candidates`).
- **Front matter + sidecar (Option C)**: YAML front matter for markdown/text (zero friction); `.acl` sidecar for binary files (PDFs, Word docs). Front matter takes precedence when both exist. Covers all file types without forcing sidecars on markdown users.
- **ACL immutability**: changing an ACL requires re-ingest. Accepted as the correct tradeoff for 5d — the ingest pipeline already does full document replace; a separate ACL update path would duplicate mutation logic.
- **Deny-all semantics**: `acl: []` explicitly denies all callers. This is distinct from `acl: null` (default-open). Operators can use deny-all to index content (preserve it in the store) without making it retrievable.

## Edge Cases & Constraints

- **Pre-existing chunks**: treated as `acl: null` (default-open). The startup migration adds the nullable `pa.list_(pa.utf8())` column to every collection's chunk table; no re-ingest required.
- **All-invalid `_acl` entries**: **Design choice: fail-open on all-invalid `_acl`**: If every namespace name in the document's `_acl` fails validation, the chunk defaults to `acl: null` (open). This is a deliberate fail-open choice — the same as absent `_acl`. The alternative (fail-closed, defaulting to `acl: []` deny-all) would prevent data loss but silently deny access to documents the operator may have intended to be open. Operators who intend restriction must use valid namespace names. One WARNING is logged per document (not per invalid entry), including the count of dropped entries and the document path: e.g., `WARNING: _acl in report.pdf has 3 invalid namespace names (dropped); chunk defaults to open.` This avoids per-entry log flooding.
- **Empty sidecar file**: treated as `acl: null` (default-open), not deny-all. A sidecar with no valid lines is indistinguishable from absent.
- **Sidecar + front matter both present**: front matter takes precedence. The sidecar is ignored. WARNING logged to alert the operator of the ambiguity.
- **Over-fetch heuristic under heavy restriction**: if >66% of chunks in a collection are ACL-denied for the caller, returned results will consistently be fewer than `top_k`. The `acl_filtered: true` flag is the signal. Adaptive re-fetch is deferred. For deployments where a high proportion of chunks are ACL-denied, implementers may consider pushing the ACL filter into the `hybrid_search()` query as a LanceDB WHERE clause (`WHERE acl IS NULL OR list_contains(acl, :namespace)`), which avoids over-fetching denied chunks entirely. The over-fetch approach described here is the default for 5d simplicity; push-to-store is a known optimization path. Implementations MAY use a higher multiplier (e.g., `top_k * 5` or `top_k * 10`) at their discretion to improve recall under moderate ACL restriction without the full WHERE clause change.
- **Context expansion sparsity**: if many neighboring chunks are ACL-denied, expanded context may be sparse or empty for a given result. Accepted — ACL enforcement takes precedence over context completeness.
- **`hybrid_search()` return type change**: `SearchResult` (the archon-search server's internal dataclass) gains an internal `acl: list[str] | None` field. This field must NOT appear in `SearchResultSchema` (the HTTP response type). Keep the two types separate.
- **FTS index includes denied chunks**: BM25 ranking signals are computed over all chunks regardless of ACL. ACL-restricted chunks influence ranking order but are never returned. This is a known, accepted information-theoretic limitation.
- **`acl_protected_count` accuracy**: counts chunks with non-null ACL at the time of the API call. Does not distinguish deny-all (`[]`) from namespace-restricted (`["tenantA"]`). Sufficient for coverage auditing.
- **LanceDB column migration concurrency**: catch `RuntimeError` if column already exists (concurrent startup); log WARNING, continue. Idempotent. Note: unlike the 5b migration (one metadata table), this migration must iterate every collection's chunk table — verify LanceDB behaviour for `pa.list_(pa.utf8())` columns before implementation.
- **Multi-namespace ACL values are inert in 5d**: If a document's `_acl` includes a namespace other than the owning collection's namespace, those entries are stored but have no effect — the 5c collection-level isolation prevents callers from other namespaces from reaching the search route. This is not an error; no warning is logged.
- **Sidecar deny-all**: The sidecar format (one namespace per line) has no native representation for deny-all (`acl: []`). An empty sidecar is treated as `acl: null` (open). Operators who need deny-all semantics for binary files must use a special sentinel line: a sidecar containing only the word `deny-all` (case-insensitive) on the first non-blank line is parsed as `acl: []`. Any other content is parsed as the namespace list. **Warning**: `deny-all` is a valid namespace name per the regex `^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$` but is reserved as the sidecar sentinel. Operators must not name a namespace `deny-all`; doing so makes it impossible to grant sidecar access to that namespace. The namespace validation layer must reject `deny-all` as a namespace name.
- **`doc_count` includes ACL-denied documents**: The `doc_count` field in `GET /collections/{name}` counts all documents regardless of ACL. A caller can infer that restricted content exists by comparing `doc_count` to `acl_open_count + (acl_protected_count visible to them)`. This is a known, accepted information disclosure — resolving it would require per-caller document counting, which is deferred. Operators should be aware that `doc_count` is an aggregate, not a per-caller count.
- **Missing or empty namespace in request context**: If `request.state.namespace` is absent or empty (e.g., due to a middleware bug), the ACL filter treats it as matching nothing — all ACL-protected chunks are denied. Only `acl: null` (open) chunks are included in results. This is the fail-closed default for authorization failures.

## Open Questions

- None. All design decisions are resolved.

## Future Iterations

- **Per-chunk ACL override**: allow individual chunks within a document to have different ACLs (e.g., a document where the executive summary is open but the financial appendix is restricted).
- **ACL revocation endpoint**: `PATCH /collections/{name}/documents/{doc_id}/acl` — update all chunks for a document atomically without re-ingest.
- **Per-document ACL inspection**: `GET /collections/{name}/documents` listing with ACL field, so operators can audit without re-reading source files.
- **Cross-namespace shared collections**: when a collection can be accessed by multiple namespaces (future item), 5d's ACL infrastructure is already in place to control which namespaces see which chunks.
- **Role/tag-level ACLs**: sub-namespace identity (a caller carries tags set in config); chunk ACL checked against tags rather than namespace name. Requires a new identity model.
- **Adaptive over-fetch**: if filtered candidates < `top_k`, retry `hybrid_search` with a larger fetch multiplier before giving up.
- **`acl_filtered_count`**: expose the number of dropped candidates alongside the boolean — once the information-leak tradeoff is re-evaluated.
- **Deny-all breakdown in collection stats**: distinguish `acl: []` (deny-all) from `acl: ["ns"]` (namespace-restricted) in `acl_protected_count`.

## Test Requirements

The following test cases are mandatory for a security feature of this kind:

- ACL filter allows access when `acl is None` (open), when the caller's namespace is in `acl`, and denies when it is not.
- ACL filter denies all callers when `acl = []` (deny-all).
- `_acl` values failing the namespace regex are dropped; all-invalid defaults to open (with one WARNING logged per document).
- `acl_filtered: true` is set when any candidate is dropped; `false` when no candidates are dropped.
- Adjacent chunks (`fetch_adjacent_chunks`) are ACL-filtered before inclusion in the response.
- `SearchClient.search()` correctly unwraps the `results` key from the new `SearchResponse` envelope and does not return dict keys.
- Ingest correctly strips `_acl` front matter from indexed chunk text (ACL values must not appear in search results).
- `.acl` sidecar files are not indexed as content documents.
- Sidecar file is ignored when front matter is also present; one WARNING is logged to alert the operator of the ambiguity.
- Empty sidecar produces `acl: null` (open), not deny-all.
- A sidecar containing only `deny-all` (case-insensitive) produces `acl: []` (deny-all).
- Pre-existing chunks (no `acl` column, after migration) are treated as `acl: null` (open).
- `acl_protected_count` and `acl_open_count` sum to the total chunk count for a collection.
- ACL matching is case-sensitive: `"TenantA"` does not match `"tenanta"`.
- A namespace name containing SQL injection characters is blocked by the namespace regex at ingest time.
- Re-ingesting a document with a changed `_acl` value updates all existing chunks for that `doc_id` to the new ACL. No stale ACL values remain after re-ingest.

## Recommendation

This is the right increment to build before item 6 (stable external APIs). The `SearchResponse` envelope and the `acl` column are schema decisions that, if deferred past item 6, force a breaking API change. The implementation is well-scoped: one LanceDB column, one filter step in the query path, one sidecar parsing extension at ingest. The hardest part is the `SearchResponse` wrapper — every caller of `POST /search` (including `SearchClient` in the Archon parent repo and any existing tests) must be updated. That refactor must not be rushed; it is the most likely source of regressions. What must not be compromised: the filter must happen before reranking, and `acl: []` must be a genuine deny-all with no bypass path.
