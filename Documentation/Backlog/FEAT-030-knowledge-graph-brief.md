# Feature Brief: Knowledge Graph + Fact Validity Windows (FEAT-030)

## Problem
Archon has no way to record facts that change over time. When an architectural decision changes — embedding model switched, config policy updated, a component renamed — the old fact is either lost or buried in RAG chunks mixed with new content. Users cannot ask "what was the policy in January?" and get a reliable, authoritative answer.

## Goal
A queryable SQLite-backed fact store of entity-relationship triples with temporal validity windows, exposed as MCP tools, so Claude can record and recall time-bound facts accurately across any session.

## Users & Context
The user (acting through Claude via Telegram or a session) captures architectural decisions, config changes, and policy updates as they happen. Future sessions — days or months later — query these facts with full temporal awareness, without re-reading history files or relying on compacted summaries that may have lost the detail.

## Core Flow

**Layer 1 — Deterministic extraction (always runs, zero cost):**
1. `HistoryCompactor` runs its daily compaction as usual, producing a 3000-word summary
2. `DeterministicExtractor` runs immediately after: locates the `## Key Decisions and Outcomes` section (and sub-sections) and parses its bullet points into triple candidates — no API call, regex-only. Uses fuzzy heading match (case-insensitive prefix `## key decisions`).
3. Each bullet is mapped to `(subject, predicate, object)` using simple pattern matching; unparseable bullets are stored as `(session_<YYYY-MM-DD>, note, <bullet text>)` (compaction date) so nothing is dropped
4. When a new triple matches `(subject, predicate)` of an existing active triple, the old one's `valid_to` is set automatically (see Conflict Resolution Matrix)

**Layer 2 — LLM extraction (optional, runs after Layer 1 if `auto_extract = true`):**
5. `HaikuExtractor` runs a single Haiku call on the full 3000-word summary
6. Extracts additional triples not captured by bullet parsing (implicit decisions, context, relationships)
7. Results are merged with Layer 1 output; duplicates deduplicated by `(subject, predicate, object)` normalisation
8. Triples carry `source="auto_llm"` and a confidence score; those below threshold are logged at DEBUG level via `logging.getLogger('archon.kg')` with a structured message containing `source`, `confidence`, and triple content — no separate log file

**Layer 3 — Explicit user input (anytime, deterministic):**
9. User sends `/fact auth-system uses OAuth2` via Telegram — parsed into a triple using the rule: first whitespace-delimited token = subject, second = predicate, remainder = object (may contain spaces). Minimum 3 tokens required for a structured triple; fewer tokens fall back to free-text `(session_<YYYY-MM-DD>, note, <text>)` where the date is today's date. Entity names that contain spaces must use hyphens or underscores instead (e.g., `auth-system` not `auth system`) — the parser splits on whitespace, so a space-delimited entity name would be parsed incorrectly. Normalisation converts hyphens to underscores post-parse. Written immediately with `source="manual"`.
10. Free-text fallback: `/fact we agreed to keep the chunk size at 512` → stored as `(session_<YYYY-MM-DD>, note, <text>)` (today's date) if structure cannot be parsed
11. `kg_invalidate <id>` is available via MCP tools and via Telegram for corrections

**Recall (MCP, Claude-initiated):**
12. `kg_query entity="auth_system"` returns all active facts, optionally filtered by `as_of` date
13. `kg_timeline entity="auth_system"` shows the full history of changes for an entity
14. Facts surfaced on demand — not injected automatically into every session

## In Scope
- New `archon/ai/fact_store.py` — SQLite-backed store with atomic writes (WAL mode)
- Triple model: `(id, subject, predicate, object, valid_from, valid_to, confidence, source, created_at)` — `valid_from`: for auto-extracted triples, set to the date of the compacted session file (not the extraction run date); for manual `/fact` and `kg_add` triples, set to today's date by default; `kg_add` accepts an optional `valid_from` ISO-date parameter (e.g., `2026-01-15`). If omitted, defaults to today's date. Dates in the future are rejected with an error. A backdated `valid_from` does not change conflict resolution order — triples are compared by `created_at` for ordering, not `valid_from`. Indexes: `(subject, predicate, valid_to)` composite index for temporal lookups; `(subject)` index for `kg_query` and `kg_timeline`.
- SQLite `meta` table with `schema_version` integer — enables forward-compatible migrations without requiring a fresh database.
- New `archon/ai/fact_extractor.py` — two classes:
  - `DeterministicExtractor`: regex parser for `## Key Decisions and Outcomes` section bullets AND its sub-sections (e.g., `### Completed Tasks`, `### Incomplete Tasks`); uses fuzzy heading match (case-insensitive prefix `## key decisions`); zero API cost; always runs. Section boundary: captures all content from the `## Key Decisions` prefix match to the next `##`-level heading (or end of document), then parses all bullet points within that range regardless of `###` sub-headings.
  - `HaikuExtractor`: optional LLM pass on full summary; runs only if `auto_extract = true`
- `HistoryCompactor._compact_day()` calls `DeterministicExtractor` inline after writing the summary file (synchronous, fast). `HaikuExtractor` is run sequentially inside `_compact_day()` after DeterministicExtractor, with its own try/except so failures never propagate. It does NOT use `asyncio.create_task()` — sequential execution avoids fire-and-forget lifecycle gaps and is acceptable because `_compact_day()` already runs in a background task and Haiku latency (~1–2s per call) is negligible in a nightly batch job. Extraction is skipped when `_compact_day()` writes no file (e.g., empty content, no response sections from LLM) — both extractors are only called after a summary file is successfully written.
- `/fact` Telegram command in `archon/chat/commands.py`: parses `<subject> <predicate> <object>` or stores as free-text note; writes `source="manual"` triple immediately
- Five MCP tools in `ArchonToolkit`: `kg_add`, `kg_query`, `kg_invalidate`, `kg_timeline`, `kg_stats`. Plus a sixth tool: `kg_search predicate=<p> object=<o> [as_of=<date>]` — returns active triples (or as_of-filtered triples if `as_of` is provided) matching any combination of predicate and/or object filters (entity name not required). At least one filter (predicate or object) must be provided; returns an error if both are omitted. Return schema is identical to `kg_query`. `kg_add`: adds a triple with optional `source` (default `"manual"`) and optional `valid_from` (default today). KG tools follow the separate-module pattern: `archon/ai/archon_toolkit_kg.py` registers all 6 tools via `_register_kg_tools()` called from `ArchonToolkit.__init__()`.
- `kg_stats` returns: `total_triples` (int), `active_triples` (int), `expired_triples` (int), `triples_by_source` (dict mapping source string to count), `oldest_valid_from` (ISO date or null), `newest_created_at` (ISO date or null).
- New `[knowledge_graph]` config section: `db_path` (default: `~/.archon/kg/facts.db`), `auto_extract` bool (default: `false` — opt-in to Haiku cost), `confidence_threshold` (default: `0.7`)
- Gateway wiring: `FactStore` is instantiated at startup by `Gateway` and injected into `HistoryCompactor` (via constructor) and `ArchonToolkit` (for MCP tools). `HistoryCompactor` receives `DeterministicExtractor` and `HaikuExtractor` via constructor injection. Note: `HistoryCompactor.__init__` gains optional parameters `fact_store: FactStore | None = None`, `deterministic_extractor: DeterministicExtractor | None = None`, `haiku_extractor: HaikuExtractor | None = None` — all default to `None` so the existing instantiation and all existing tests remain valid without modification. When `fact_store` is `None`, extraction is skipped silently inside `_compact_day()`. `ArchonToolkit` gains an optional `fact_store: FactStore | None = None` constructor parameter, matching the existing pattern for `job_scheduler`, `attachment_store`, etc. `_register_kg_tools()` reads `self._fact_store`. When `fact_store` is `None` (KG not configured), the 6 KG tools return an error message.
- Full TDD coverage: `tests/ai/test_fact_store.py`, `tests/ai/test_fact_extractor.py`, `tests/chat/test_commands.py` (fact command), `tests/ai/test_fact_extractor_integration.py` — end-to-end test: mock compacted summary → `DeterministicExtractor` → `FactStore` → `kg_query` returns expected triples. `tests/config/test_kg_config.py` — verifies `[knowledge_graph]` section loads with correct defaults (`db_path`, `auto_extract=false`, `confidence_threshold=0.7`) and raises `ConfigError` on invalid types.
- `test_fact_store.py` includes: auto-new vs manual-existing (no invalidation), manual-new vs auto-existing (auto invalidated), manual-new vs manual-existing (warning, no invalidation).
- `test_fact_extractor.py` includes: confidence=0.0, confidence=0.7 (boundary — included), confidence=1.0, confidence field absent (defaults to 0.0), confidence > 1.0 (clamped to 1.0).

## Out of Scope
- Automatic fact extraction from documents or RAG chunks — error rate too high for a memory system that must be authoritative
- Graph traversal across entities (e.g., "find all facts related to entities connected to auth_system")
- KG-informed RAG routing (using facts to bias collection selection)
- Import/export of triples in standard KG formats (RDF, JSON-LD)
- UI for browsing the KG (Telegram commands only for now)

## Key Decisions
- **`fact_store.py` lives in `archon/ai/`** alongside `history_compactor.py` and `fact_extractor.py` — the KG is part of the AI/memory layer, not the RAG/search layer.
- **SQLite over JSON**: triples need efficient temporal queries (`WHERE valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)`) that JSON cannot support without loading everything into memory. SQLite is a single embedded file — no daemon, no connection pooling needed.
- **No "Claude mid-session" path**: relying on Claude to spontaneously call `kg_add` when it detects a decision is unreliable — Claude has no persistent obligation across turns and will forget. All automatic extraction is post-session (after compaction), not in-flight.
- **Deterministic first, LLM optional**: `DeterministicExtractor` runs unconditionally on the structured `## Key Decisions` section — free, fast, no API dependency. `HaikuExtractor` is opt-in (`auto_extract = false` by default) for users who want broader coverage and accept the marginal cost.
- **`auto_extract` defaults to `false`**: Haiku extraction adds ~$0.001/day but it's still a cost and a dependency. Users who want it enable it explicitly. The deterministic layer alone provides meaningful value.
- **`/fact` as the reliable explicit path**: deterministic, immediate, user-controlled. No assumption that Claude will remember to record anything. If a decision matters, the user or Claude can record it in one command.
- **Manual triples are never invalidated by auto-extraction. Auto triples are superseded when a manual triple for the same `(subject, predicate)` is added.** See Conflict Resolution Matrix for full rules.
- **Simple triple model, not a full ontology**: subject/predicate/object is sufficient. RDF/OWL complexity would violate KISS without proportional benefit at this scale.
- **Normalisation**: subject and predicate strings are normalised before storage and lookup: lowercase, replace spaces and hyphens with underscores, strip leading/trailing whitespace. Object strings are stored verbatim (preserving case, version numbers, file paths, etc.) but normalised only for deduplication comparisons. Example: subject `auth-system` → stored as `auth_system`; object `OAuth2.0` → stored as `OAuth2.0`, deduplicated as `oauth2.0`.
- **Schema version tracking**: a `meta` table stores `schema_version`. Future column additions use `ALTER TABLE` with `DEFAULT NULL`; breaking changes increment the version and require a migration.
- **SDK rule**: `HaikuExtractor` uses `ClaudeSDKClient` (`claude-agent-sdk`) following the same `connect/query/receive_response/disconnect` lifecycle as `HistoryCompactor`. Direct use of `anthropic.AsyncAnthropic()` is prohibited.
- **Extraction inside `_compact_day()`**: extraction runs inside `_compact_day()` rather than via a separate event/hook to avoid introducing a publish-subscribe mechanism for a single subscriber. This is a deliberate KISS trade-off against SRP purity.
- **HaikuExtractor runs sequentially, not as a fire-and-forget task**: avoids dangling tasks during shutdown (Gateway's `stop_all()` must complete in 5 seconds). `_compact_day()` already runs in a background asyncio task; running HaikuExtractor synchronously within it adds minimal latency with no lifecycle complexity.

## Edge Cases & Constraints

### Conflict Resolution Matrix

| New triple | Existing active triple | Outcome |
|---|---|---|
| auto-new | auto-existing (same `subject, predicate`) | auto-existing `valid_to` is set; new triple written |
| auto-new | manual-existing (same `subject, predicate`) | NO invalidation; auto triple written alongside manual triple; both remain active |
| manual-new | auto-existing (same `subject, predicate`) | auto-existing `valid_to` is set; manual triple takes over |
| manual-new | manual-existing (same `subject, predicate`) | warn user, do NOT auto-invalidate; user calls `kg_invalidate` explicitly |

### Other Edge Cases
- **Unparseable bullets in `DeterministicExtractor`**: stored as `(session_<YYYY-MM-DD>, note, <bullet text>)` where the date is the compaction date — nothing is silently dropped; user can later promote to a structured triple via `/fact` or `kg_add`.
- **`DeterministicExtractor` heading absent or variant**: if neither `## Key Decisions and Outcomes` nor `## Key Decisions` is found in the compacted summary, Layer 1 yields zero triples and logs a warning — it does NOT raise. The compaction still succeeds.
- **Low-confidence Haiku triples** (below `confidence_threshold`): logged at DEBUG level via `logging.getLogger('archon.kg')` with a structured message containing `source`, `confidence`, and triple content — no separate log file. User reviews and promotes via `/fact` or `kg_add`.
- **`HaikuExtractor` failure** (Haiku call fails or times out): logged and swallowed — compaction still succeeds and deterministic results are still written. KG extraction is never a blocker.
- **`/fact` with ambiguous text** (can't parse subject/predicate/object): stored as `(session_<YYYY-MM-DD>, note, <full text>)` (today's date) with `source="manual"`. User is informed of the fallback in the Telegram reply.
- **NULL `valid_to`** = "currently true" — never a zero-date or sentinel.
- **`as_of` boundary semantics**: `valid_from <= as_of AND (valid_to IS NULL OR valid_to > as_of)` — `valid_from` is inclusive, `valid_to` is exclusive (a triple expires at `valid_to`, not after it).
- **`kg_query` with no `as_of`** returns all active facts (valid_to IS NULL).
- **Concurrent writes**: SQLite WAL mode, `BEGIN IMMEDIATE` transaction. Single-process daemon; contention is minimal.
- **`kg_invalidate` on already-expired triple**: no-op, returns informational message.
- **SQLite corruption**: on `OperationalError` during open or read, the store logs the error and raises — it does NOT silently create a new empty database. Users should back up `~/.archon/kg/facts.db` alongside their config. SQLite WAL mode allows safe file-level backups while the daemon is running.
- **`compact_today()` partial summaries**: extraction only runs on completed-day compaction via `_compact_day()`, not on partial/in-progress summaries from `compact_today()`. Facts from the current day's session are not extracted until the midnight compaction completes for that day.
- **Exact duplicate from Layer 1+2 merge**: if Layer 1 and Layer 2 produce a triple with identical `(subject, predicate, object)` after normalisation, only one is written — the existing active triple is kept unchanged (no new insertion, no `valid_to` update). The conflict resolution matrix's auto-vs-auto supersession only applies when objects differ.
- **Fallback triple subject uniqueness**: fallback triples use `session_<YYYY-MM-DD>` as the subject (not bare `session`) to prevent conflict-matrix collisions between fallback triples from different days.

## Open Questions
- Should the KG db live alongside LanceDB (same `db_path` directory) or in its own path? Recommendation: separate `~/.archon/kg/` so it can be backed up, inspected, or migrated independently.

**Resolved**: `kg_add` accepts optional `source` field (default `"manual"`).

## Future Iterations
- Auto-extraction: Claude proposes facts from session content; user confirms before writing
- KG-informed RAG routing: facts about a project inform which collections to search
- Graph traversal: "show everything related to entities connected to auth_system"
- Export to standard formats (JSON-LD, Turtle/RDF) for interoperability

## Recommendation
Build this first. The SQLite store is fully independent of LanceDB, the MCP tools are additive, and nothing breaks for users who don't use it. The extraction layers are deliberately incremental: deterministic bullet parsing ships first and provides immediate value at zero cost; Haiku extraction is a one-line config opt-in when the user wants broader coverage; `/fact` is a safety valve that doesn't require trusting Claude to remember anything.

**Before shipping**: run the `DeterministicExtractor` against a sample of real compacted summaries to calibrate false-negative rate. The `## Key Decisions` section format must be robust across the variety of Haiku outputs that `HistoryCompactor` produces. The temporal query logic (`as_of` filtering) must be tested exhaustively — it is the core value of the entire feature.
