# Feature Brief: Conversation Export Mining (FEAT-031)

## Problem
Archon only indexes its own session history. Users who interact with other AI platforms — ChatGPT, Claude.ai, Gemini — have no way to bring those conversations into Archon's unified search. Valuable context, decisions, and knowledge from other platforms is invisible to Archon, undermining its role as a unified memory system.

## Goal
A pluggable import pipeline that ingests conversation exports from external AI platforms into Archon's existing LanceDB search infrastructure, so all past AI interactions across all platforms are searchable from a single interface via the existing `search` tools.

## Users & Context
A user who uses multiple AI platforms exports their conversation history (e.g., ChatGPT's `conversations.json`, Claude.ai's export archive), places the file in a location Archon can reach, triggers an import via MCP tool or Telegram command, and immediately searches across all AI conversations as if they were Archon's own history. Re-import when a new export is available updates only changed conversations.

## Core Flow
1. User exports conversations from an external platform (e.g., downloads ChatGPT export ZIP)
2. User runs: `search_import path="/path/to/conversations.json" format="chatgpt" collection="chatgpt-2026"` via MCP tool or Telegram
3. Archon detects format (explicit or auto-detected), routes to the matching importer adapter
4. Importer extracts conversation exchanges as paired units: `(user_message, assistant_response)` — one unit per turn
5. Pairs are chunked using the existing `DocumentChunker` and embedded into a named collection
6. Progress is tracked via existing `IndexingStateStore` (PENDING → IN_PROGRESS → DONE/FAILED)
7. Collection is immediately searchable via existing `search` and `search_with_context` tools
8. On re-import: content hashes detect unchanged exchanges; only new/changed pairs are re-indexed

## In Scope
- New `archon/search/importers/` module with a pluggable `ConversationImporter` ABC
- **Claude.ai** export adapter (JSON archive format)
- **ChatGPT** export adapter (`conversations.json`)
- Conversation exchanges indexed as paired `(user, assistant)` units — not raw JSON, not individual messages
- New MCP tool: `search_import(path, format, collection_name)` registered in `ArchonToolkit`
- Collection naming convention: `import-{platform}-{label}` (e.g., `import-chatgpt-2026`)
- Import progress tracked via existing `IndexingStateStore`
- Content-hash deduplication per exchange to support incremental re-import
- Auto-format detection from file extension and content sniffing when `format` is omitted
- Telegram delivery of import progress notification on completion (via existing `IndexingNotificationMonitor`)

## Out of Scope
- Slack, Discord, Gemini adapters — deferred (follow-on adapters, not blocking)
- Real-time sync with external platform APIs — Archon is local-first; no API credentials
- Automatic scheduling of re-imports — user triggers manually
- Deduplication across two different platform imports (e.g., same conversation exported from both ChatGPT and Claude.ai)
- Import via Telegram file attachment — deferred (file handling path adds complexity)
- Modification or deletion of imported conversations from within Archon

## Key Decisions
- **Exchange pairs as indexing units, not raw JSON or individual messages**: a user question without its answer is not useful in search results. The pair is the smallest semantically complete unit. Individual messages would double the index size and halve result quality.
- **New `archon/search/importers/` module, not an extension of `DocumentParser`**: conversation exports are structurally different from documents. They are not files to be chunked — they are structured data to be parsed into exchange pairs. Mixing this into `DocumentParser` would violate single responsibility.
- **File-based import only, no API access**: maintains Archon's local-first, no-credential principle.
- **Use existing `SearchCollectionSync` infrastructure for progress and state**: avoid a parallel ingest path. Imported collections appear identical to native collections after import — same search tools, same routing, same manifest.
- **Explicit `format` parameter with auto-detect fallback**: auto-detect is convenient but unreliable across format versions. Explicit wins; auto-detect is best-effort.

## Edge Cases & Constraints
- **Malformed or truncated export files**: skip bad exchanges individually, log as warnings, continue with valid exchanges. Report error count in result.
- **Export format version changes** (e.g., ChatGPT v1 vs v2 schema): version detection per adapter; fail fast with a clear error if format is unrecognised, rather than silently importing garbage.
- **Very large exports** (100k+ conversations): streaming JSON parser, not load-all-into-memory. Progress reported every 50 exchanges via `IndexingStateStore`.
- **Re-import of unchanged data**: content hash per exchange pair (SHA256 of user+assistant text). Skip if hash matches stored value. Only re-embed changed or new exchanges.
- **Collection name collision**: if `collection_name` already exists and is not an import collection (i.e., it's a native path-based collection), reject with a clear error.
- **Encoding**: all exports read as UTF-8; skip exchanges with undecodable bytes, log path.

## Open Questions
- Should imported collections appear in `archon doctor` health checks the same way native collections do? Recommendation: yes, with a `[imported]` tag in the output to distinguish them.
- Should re-import replace the collection entirely or merge? Recommendation: merge via content hashing — safer for large collections, and consistent with how native file sync works.

## Future Iterations
- Slack, Discord, Gemini, Notion, Obsidian adapters
- Import via Telegram file attachment (drop export JSON directly into chat)
- Automatic re-import reminders when export file is newer than last import date
- Cross-platform deduplication (detect the same conversation exported from two platforms)
- `ConversationChunker` (from FEAT-032) applied to imported pairs for richer metadata

## Recommendation
This is the strategic feature — the one that makes Archon genuinely useful as a universal memory layer rather than just a Claude Code companion. Build it second, after the KG (FEAT-030) is stable and independent. The hardest part is not the indexing — the existing pipeline handles that cleanly — it is the format adapters: every platform changes its export schema and the adapters will need maintenance. Design the `ConversationImporter` ABC to be strict about schema validation, fail fast on format mismatches, and make adding a new adapter a 30-minute task. Do not compromise on the exchange-pair unit: individual message indexing is a false shortcut that degrades every future search result.
