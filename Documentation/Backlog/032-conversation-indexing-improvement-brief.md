# Feature Brief: Improved Conversation Indexing — Paired Exchange Units (FEAT-032)

## Problem
Archon indexes session history files as raw Markdown chunks with no awareness of conversation structure. The actual history format places the full user message and the assistant response in the same H2 section — they are not physically separated. However, the `✅ Response` section only embeds a 120-character truncated quote of the user's question (`> User: "..."`). For complex sessions with many tool calls, the H2 section spans thousands of tokens and splits across multiple chunks: the full user message lands in the first chunk; the response chunk only has the 120-char truncation. Searching "what did we decide about X?" retrieves the response chunk, which is missing the full question context — making it semantically incomplete for retrieval.

## Goal
Index Archon's own session history (and any future imported conversations via FEAT-031) as semantically paired exchange units — one unit per `(full user message + assistant response)`, tool calls stripped — so every chunk carries the complete semantic context of the exchange regardless of its length.

## Users & Context
A user referencing past work in a new session ("what was our decision on auth?", "did we discuss this before?"). They expect the search result to show the full exchange — the question they asked and the answer they received — not a fragment. They should not need to read surrounding context to understand what was found.

## Core Flow
1. Session history `.md` files continue to be written by `HistoryManager` — unchanged
2. A new `ConversationChunker` is introduced alongside the existing `DocumentChunker`
3. When `SearchCollectionSync` ingests the `sessions` collection, it detects the history path and routes to `ConversationChunker` instead of `DocumentChunker`
4. `ConversationChunker` parses H2 section boundaries in the history format; for each exchange it extracts:
   - The full user message text (from the H2 section opening)
   - The response content only (from the `✅ Response` H3 section)
   - Tool calls, thinking, routing events are discarded — not indexed
5. The combined text `"User: {full message}\n\nResponse: {response content}"` is passed to the existing `RecursiveChunker` for token-aware splitting
6. If the combined text fits in one chunk: no schema changes needed, stored as a normal chunk
7. If the combined text spans multiple chunks: all sub-chunks share an `exchange_id` (deterministic hash of exchange timestamp + source path)
8. `search_with_context` is updated to fetch all chunks sharing the same `exchange_id` when one matches
9. Existing `sessions` collection is automatically rebuilt on next sync (reindex trigger)
10. `ConversationChunker` is reused by FEAT-031's import pipeline for imported conversation pairs

## In Scope
- New `archon/search/conversation_chunker.py` — `ConversationChunker` class; parses H2/H3 session markdown, extracts user+response pairs, discards tool calls
- Extended `ChunkRecord` in `_types.py`: add `exchange_id: str | None` (only populated when an exchange spans multiple chunks)
- LanceDB schema extension: one nullable `exchange_id` column (minimal change, NULL for all document collection chunks)
- Auto-reindex of `sessions` collection on next sync (existing reindex mechanism)
- Updated `search_with_context`: when a result has a non-null `exchange_id`, fetch all sibling chunks by that ID
- Applied to: `sessions` collection and FEAT-031 import collections
- Full TDD test coverage in `tests/search/test_conversation_chunker.py`

## Out of Scope
- Changes to `HistoryManager` or how history files are written
- Changes to `HistoryCompactor` — daily summaries are unaffected
- Applying `ConversationChunker` to document collections (code, docs, PDFs) — structure is meaningless for documents
- Indexing tool calls, thinking blocks, or routing events — still excluded, same as today
- Exchange-type filtering in search (e.g., search only in responses) — deferred
- Schema migration for collections other than `sessions` — other collections use `DocumentChunker`, unaffected

## Key Decisions
- **One merged chunk per exchange, not two separate role chunks**: the real problem is the 120-char truncation of the user question in the response section. Merging `(full user message + response)` into one unit solves this directly. Two separate role chunks would require a join on every search result and add schema complexity for no retrieval benefit.
- **Discard tool calls from the index unit**: tool calls between user message and response are noise for semantic retrieval. The compactor already proved this — `_extract_responses()` strips them. The indexed unit should mirror what the compactor captures: intent + outcome.
- **`exchange_id` only when the merged text exceeds chunk size**: for short exchanges (the common case), no new schema column is populated. The column is nullable and only used for large exchanges that split. This minimises schema change blast radius.
- **New `ConversationChunker` class, not a mode flag on `DocumentChunker`**: structurally different parsing logic; mixing via a flag violates single responsibility.
- **Fallback to `DocumentChunker` on parse failure**: if a history file has no H2 headers (malformed or legacy format), `ConversationChunker` degrades gracefully. No data loss.

## Edge Cases & Constraints
- **Exchange with no assistant response** (interrupted session, user-only message): indexed as a single chunk with `exchange_role="user_only"`, `exchange_id` set. Not dropped.
- **Very long assistant responses** (multi-tool chains, long code blocks): the response chunk may exceed token limit and be split by the underlying tokenizer. All sub-chunks share the same `exchange_id` and `exchange_role="assistant_response"`. `search_with_context` fetches all sub-chunks by `exchange_id`.
- **Agent log files** (`YYYY-MM-DD-HH-MM-agent.md`): same format, same `ConversationChunker` applies.
- **LanceDB schema migration**: existing `sessions` collection is dropped and rebuilt. User is notified via Telegram on reindex completion (existing `IndexingNotificationMonitor`). No data loss — raw `.md` files are the source of truth.
- **Other collections** (document collections): `exchange_id` and `exchange_role` columns are added to their schema too (same LanceDB table structure), but will always be NULL. No behavioral change for document search.

## Open Questions
- Should the `exchange_id` be a UUID generated at chunk time, or a deterministic hash of the exchange content? Recommendation: deterministic hash (SHA256 of exchange timestamp + source path) — enables stable IDs across reindexes without a UUID registry.

## Future Iterations
- Exchange-type filtering: `search(query, exchange_role="user")` — search only in questions or only in responses
- Tool call indexing as a separate optional collection (for debugging sessions)
- Automatic reuse of `ConversationChunker` for FEAT-031 import collections (natural follow-on, not blocking)

## Recommendation
Build this third, after the KG (FEAT-030) and export mining (FEAT-031) are stable. The LanceDB schema migration forces a full rebuild of the `sessions` collection, which is the most disruptive change. Doing it last means the schema change happens only once, and the `ConversationChunker` is immediately reusable by FEAT-031 imports. The approach is materially simpler than the original brief described — one merged chunk per exchange, one nullable column, fallback for edge cases. The hardest part is correctly parsing the H2/H3 markdown boundaries across the variety of real session files. Test against a large corpus of real history files before shipping.
