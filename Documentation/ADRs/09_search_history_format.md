# ADR 09 — Search Integration and History Format

**Purpose**: Architecture decision record for the Search (Retrieval-Augmented Generation) integration replacing QMD, and the Markdown history format that supports semantic search
**Audience**: Backend engineers
**Status**: Accepted
**Last reviewed**: 2026-03-25
**Next review**: 2026-06-25

---

## Status

Accepted

## Date

2026-03-25

## Context

Archon previously integrated optionally with QMD (`@tobilu/qmd`), a Node.js tool providing vector search over conversation history. QMD required Node.js ≥ 22 or Bun ≥ 1.0, downloaded ~3 GB of GGUF models at first run, and ran as an HTTP MCP daemon. These dependencies created friction for operators wanting semantic search without a Node.js toolchain.

Archon persists every conversation turn to daily Markdown files at `~/.archon/history/sessions/YYYY-MM-DD.md`. These files serve two purposes:

1. **Audit log** — human-readable record of all interactions.
2. **Searchable memory** — Claude can query its own past conversations via an MCP server that indexes these files and exposes full-text and semantic search tools.

The history format must be compatible with how a document retrieval system structures and retrieves chunks. The H2/H3 Markdown structure and Contextual Retrieval blockquote established in the QMD era carry forward unchanged because the format is sound for any chunked retrieval system.

Full technology selection rationale, trade-off analysis, and architecture details are in [`Documentation/Completed/26_search_integration_research.md`](../Completed/26_search_integration_research.md).

## Decision

### Replace QMD with a Python-native search stack (`archon/search/`)

Replace QMD with a fully Python-native, local-first search stack:

| Component | Technology | Rationale |
|---|---|---|
| Vector store | LanceDB | Embedded, no separate process; hybrid BM25 + vector search |
| Embeddings | fastembed (ONNX) | No PyTorch; no HuggingFace tokenizer process explosion; ~33–130 MB models |
| Reranking | fastembed TextCrossEncoder | Cross-encoder reranking; ~85 MB model; ~160 ms CPU latency acceptable |
| Parsing | Docling | Native PDF/DOCX/XLSX/PPTX/HTML parsing without external binaries |
| Chunking | Chonkie RecursiveChunker | Token-aware recursive chunking |
| MCP server | FastMCP (Python) | Eliminates Node.js; same HTTP JSON-RPC protocol as Archon MCP Server |

### Config rename: `[qmd]` → `[search]`

The `[qmd]` config section is replaced by `[search]` with no migration path (clean break):

```toml
[search]
enabled = true                      # on by default since FEAT-046; the installer starts the service automatically
host = "localhost"
port = 8282
history_collection = "archon-history"
```

### Symbol renames

All `qmd_*` symbols are renamed to `search_*` throughout the codebase:

| Old | New |
|---|---|
| `search_url` | `search_url` |
| `search_enabled` | `search_enabled` |
| `mcp_servers["qmd"]` | `mcp_servers["search"]` |
| `_ensure_qmd_daemon()` | `_ensure_search_server()` |

### Gateway probe pattern

The gateway no longer starts a QMD subprocess. Instead it probes the Search server's `/health` endpoint at startup. The Search server is managed as a separate user-owned process via `archon search start/stop`:

```
GET http://{host}:{port}/health
→ 200: search_url set; injected into all sessions
→ error: log warning; Archon continues without Search
```

### History format (unchanged)

The Markdown history format established for QMD compatibility is retained unchanged. The H2/H3 hierarchy and Contextual Retrieval blockquote are equally suitable for LanceDB chunking:

```markdown
# YYYY-MM-DD — Archon Conversations

## HH:MM:SS UTC · User {user_id} · {cwd}

{user_message_text}

### 💭 Thinking · HH:MM:SS UTC

### 🔧 Tool: {name} [{id}] · HH:MM:SS UTC

### 📤 Result [{id}] · HH:MM:SS UTC

### ✅ Response · HH:MM:SS UTC

> User: "{first_120_chars_of_question}..."

{response_content}
```

### 7 MCP tools exposed

| Tool | Description |
|---|---|
| `search` | Hybrid BM25 + vector search across all collections |
| `search_with_context` | Search with surrounding chunk context |
| `ingest_file` | Parse, chunk, embed, and store a single file |
| `ingest_directory` | Ingest all supported files under a directory path |
| `list_collections` | List all indexed collections and document counts |
| `list_documents` | List documents in a collection |
| `delete_document` | Remove a document and its chunks from the store |

## Consequences

### Positive

- Eliminates Node.js / Bun dependency entirely.
- ~150 MB model download at first install (vs. ~3 GB for QMD).
- Supports PDF, DOCX, XLSX, PPTX, HTML, MD, TXT, and code files natively via Docling.
- Hybrid BM25 + vector search with cross-encoder reranking delivers higher precision than QMD's vector-only approach.
- `fastembed` uses ONNX Runtime — no macOS process explosion from HuggingFace tokenizers.
- The gateway probe pattern is simpler than subprocess management: one HTTP request, no PID files.

### Negative

- No migration path from existing QMD collections — operators must re-ingest from source files.
- No auto re-indexing in v1 — `archon search ingest` must be run manually (tracked as `Search-watcher` in tech debt).
- Windows service registration deferred (tracked as `Search-windows` in tech debt) — manual run via `python -m archon.search.server`.
- Reranker adds ~160 ms latency on CPU — acceptable for a personal knowledge base.

## Alternatives Considered

### Keep QMD, wrap it in a Python subprocess manager

Rejected: still requires Node.js / Bun on the operator's machine. Adds a subprocess management layer without eliminating the dependency.

### sentence-transformers instead of fastembed

Rejected: requires PyTorch (2–5 GB), triggers HuggingFace tokenizer process explosion (~100+ worker processes) on macOS. fastembed's ONNX backend is leaner and sufficient for embedding/reranking.

### Qdrant as vector store

Rejected: requires a separate Qdrant server process (Docker or binary). LanceDB is embedded and needs no additional infrastructure.

## Related Documents

- [`Documentation/Completed/26_search_integration_research.md`](../Completed/26_search_integration_research.md) — full technology selection rationale, trade-off analysis, and implementation plan
- [`Documentation/Completed/09_qmd_compatible_history_format.md`](../Completed/09_qmd_compatible_history_format.md) — superseded ADR for QMD-compatible history format (archived)
- `archon/ai/history_manager.py` — implementation of history persistence
- `archon/ai/event_renderer.py` — renders SDK events to Markdown
- [`Documentation/Architecture/120_services_and_integration_architecture.md`](../Architecture/120_services_and_integration_architecture.md) — Search MCP integration section
