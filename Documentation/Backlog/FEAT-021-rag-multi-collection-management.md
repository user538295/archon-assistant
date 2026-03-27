# FEAT-021 — RAG Multi-Collection Management

**Purpose**: Add a local vector-search layer to Archon so the decomposer can automatically retrieve relevant context from user-managed document collections without any explicit instruction, while keeping resource usage bounded via priority-ordered batch search.
**Audience**: Archon users who maintain local document libraries (notes, contracts, code, docs) and want Claude to draw on them automatically without copy-pasting context manually.
**Status**: To Do

---

## Background

Archon sessions currently rely entirely on injected history and REMINDER.md for persistent context. Users with large local document libraries must manually paste relevant excerpts or live with Claude not knowing about them.

A RAG (Retrieval-Augmented Generation) layer would let Archon automatically retrieve the most relevant chunks from local collections and inject them into the decomposer session before it begins reasoning — invisibly, without the user having to ask.

The central challenge is **collection selection**: with many collections, naïve parallel search across all of them exhausts system resources. The solution is a two-stage approach: fast embedding-based pre-ranking (no LLM, milliseconds), then decomposer reasoning over a small pre-filtered shortlist, then batch-parallel search of the top 2–3 collections.

---

## Goal

1. Users manage named document collections via `archon collection` CLI commands.
2. Collections are indexed locally (chunked, embedded, stored in a vector DB).
3. On every decomposer query, Archon automatically selects and searches the most relevant collections, injecting retrieved chunks as context — no user instruction needed.
4. Resource usage is bounded: at most 2–3 collections are searched in parallel per batch; a confidence gate skips RAG entirely when no collection is a strong match.

---

## Scope

### In Scope

- `archon collection` CLI subcommands: `create`, `delete`, `list`, `ingest`, `reindex`, `info`
- Dry-run flag for destructive operations (`--dry-run`)
- CLI RAG debug tool: `archon rag search <query> [--collection <name>] [--top-k N]`
- `archon doctor` collection health checks
- Auto-generated collection metadata: embedding centroid + Haiku-generated description
- `VectorStore` abstraction (ChromaDB as default backend)
- `CollectionRegistry` — persists collection configs in `~/.archon/collections/`
- `RAGRetriever` — query embedding + centroid pre-ranking + batch parallel search
- `EmbeddingProvider` abstraction (OpenAI `text-embedding-3-small` default; local fallback via `sentence-transformers`)
- Decomposer integration: RAG context injected via `inject_context()` before session query
- Ingest progress feedback (file count, chunk count, timing)
- `[rag]` config section in `config.toml`

### Out of Scope

- Telegram commands for collection management (future)
- Remote / cloud vector stores (S3, Pinecone, Weaviate)
- Re-ranking models (cross-encoders)
- Streaming ingest for very large corpora (>100 k documents)
- Web scraping / URL ingestion (local files only)
- Multi-user collection namespacing (single-user daemon)

---

## Acceptance Criteria

### CLI
- [ ] `archon collection create <name> [--description <text>]` creates a named, empty collection
- [ ] `archon collection delete <name> [--dry-run]` removes collection and all indexed data; dry-run prints what would be deleted
- [ ] `archon collection list` prints name, document count, last indexed date, and auto-generated description for each collection
- [ ] `archon collection ingest <name> <path> [--glob <pattern>]` indexes files; prints progress (N files, N chunks, elapsed)
- [ ] `archon collection reindex <name>` re-embeds all documents; detects and removes deleted files
- [ ] `archon collection info <name>` shows document count, chunk count, embedding model, last indexed, description
- [ ] `archon rag search <query> [--collection <name>] [--top-k N]` returns top-N chunks with scores; searches all collections if `--collection` omitted
- [ ] All destructive CLI operations prompt for confirmation unless `--dry-run` or `--yes` is passed

### Auto-metadata generation
- [ ] During `ingest` / `reindex`, an embedding centroid is computed from all chunk embeddings and stored
- [ ] During `ingest` / `reindex`, a Haiku-generated description (2–3 sentences) is generated asynchronously from a sample of chunks and stored
- [ ] Description regenerates automatically when document count changes by ≥20% since last generation
- [ ] Centroid updates incrementally on each ingest (running mean); no full recompute required

### Decomposer integration
- [ ] `RAGRetriever.retrieve(query, session)` is called at the start of every decomposer `route_task()` before the LLM query
- [ ] Pre-ranking: query embedding is compared against all collection centroids; top 5–10 collections selected (O(ms), no LLM)
- [ ] Decomposer receives the pre-filtered shortlist + descriptions in context and selects top 2–3 for actual search
- [ ] Selected collections are searched in parallel (asyncio); at most `rag.max_parallel` (default: 3) concurrent searches
- [ ] If top centroid similarity < `rag.confidence_threshold` (default: 0.30), RAG is skipped entirely and a debug-level log entry is written
- [ ] Retrieved chunks are injected via `inject_context(text, injection_type="rag_retrieval", detail=collection_name)`
- [ ] If no relevant chunks are found after search, nothing is injected (no empty context block)
- [ ] RAG injection appears in session history via `ContextInjectedEvent` (injection_type="rag_retrieval")
- [ ] In verbose/debug mode: Telegram shows `🔍 RAG: retrieved N chunks from <collection>`
- [ ] In quiet/normal mode: RAG injection is silent (no Telegram message)

### Doctor integration
- [ ] `archon doctor` reports collections with stale indexes (last indexed > 7 days)
- [ ] `archon doctor` reports collections whose embedding model differs from the currently configured model
- [ ] `archon doctor` reports collections with zero documents

### Config
- [ ] `[rag]` section in `config.toml` supports: `enabled` (default `true`), `collections_dir`, `embedding_provider`, `embedding_model`, `max_parallel` (default `3`), `confidence_threshold` (default `0.30`), `top_k` (default `5`)
- [ ] RAG is fully disabled when `[rag] enabled = false`

### Tests
- [ ] All existing tests pass
- [ ] New tests achieve ≥85% coverage for all new modules
- [ ] `RAGRetriever` is tested with mocked `VectorStore` and `EmbeddingProvider`
- [ ] Decomposer integration tests mock `RAGRetriever.retrieve()` and assert `inject_context` called with correct args
- [ ] `archon doctor` tests assert stale/model-mismatch/empty-collection warnings

---

## What Does NOT Change

- Classifier — RAG selection is entirely the decomposer's concern; classifier is unmodified
- `ContextReminder` injection path — RAG is a separate injection type
- `ClaudeSession.send()` event pipeline — RAG injects via the existing `inject_context()` / `_pending_context` queue
- Session history format — `ContextInjectedEvent` with `injection_type="rag_retrieval"` is already handled by the existing event pipeline (see FEAT-018)

---

## Known Limitations / Accepted Trade-offs

- Centroid pre-ranking is approximate: heterogeneous collections (mixed topics) produce noisy centroids. Mitigated by the confidence gate — low-confidence matches are skipped rather than searched.
- Embedding model change invalidates all existing centroids and indexes; `archon collection reindex --all` is required after changing `rag.embedding_model`. Doctor warns when model mismatch is detected.
- Haiku description generation adds ~1–2 s to the first ingest. Subsequent ingests only regenerate when the ≥20% document-count change threshold is crossed.
- `max_parallel = 3` is a config default, not a hard cap. Users with fast SSDs and small collections can raise it; users on constrained hardware should lower it.
- RAG injection content is NOT shown in Telegram (only chunk count and collection name) to avoid flooding. Full chunks are available in session history.
- The decomposer's collection selection step is an LLM reasoning call over the shortlist (5–10 items). For users with ≤3 collections, the centroid pre-ranking step is skipped and all collections are passed directly.

---

## Architecture

### New modules

**`archon/rag/`**
- `collection_registry.py`: `CollectionRegistry` — CRUD for collection configs persisted in `~/.archon/collections/<name>/meta.toml`; each entry: `name`, `description`, `embedding_model`, `last_indexed`, `doc_count`, `chunk_count`, `centroid` (serialized numpy array)
- `vector_store.py`: `VectorStore` ABC + `ChromaVectorStore` (default); methods: `add(chunks)`, `search(query_embedding, top_k) -> list[Chunk]`, `delete_collection()`, `count()`
- `embedding_provider.py`: `EmbeddingProvider` ABC + `OpenAIEmbeddingProvider` + `LocalEmbeddingProvider` (sentence-transformers); method: `embed(texts: list[str]) -> list[list[float]]`
- `ingester.py`: `Ingester` — file loading (text, markdown, PDF via `pypdf`), chunking (sliding window, 512 tokens, 64-token overlap), embedding, upsert to `VectorStore`, centroid computation, Haiku description generation
- `retriever.py`: `RAGRetriever` — `retrieve(query: str) -> list[Chunk] | None`; centroid pre-rank → decomposer shortlist selection → batch-parallel `VectorStore.search()` → merge + rank by score; confidence gate

**`archon/cli/collection_cmd.py`** — `archon collection` subcommand handler
**`archon/cli/rag_cmd.py`** — `archon rag search` subcommand handler

### Modified

- `archon/cli/main.py` — register `collection` and `rag` subcommands
- `archon/ai/decomposer.py` — call `RAGRetriever.retrieve(query)` in `route_task()`, inject result via `inject_context(..., injection_type="rag_retrieval")`
- `archon/config/loader.py` — add `RagConfig` dataclass and `[rag]` section parsing
- `archon/cli/doctor.py` — add collection health checks
- `examples/config.toml.example` — add `[rag]` section with all defaults documented

### Data flow

```
User message → Decomposer.route_task(query)
  │
  ├─ RAGRetriever.retrieve(query)
  │    ├─ embed(query)                          ← EmbeddingProvider
  │    ├─ cosine_sim(query_emb, all_centroids)  ← O(ms), no LLM
  │    ├─ if max_sim < confidence_threshold → return None (skip RAG)
  │    ├─ shortlist = top min(10, N) collections
  │    ├─ Decomposer selects top 2–3 from shortlist via LLM reasoning
  │    ├─ asyncio.gather(*[store.search(q, top_k) for store in selected])
  │    ├─ merge + rank by score
  │    └─ return top_k chunks
  │
  ├─ inject_context(chunks_text, "rag_retrieval", collection_name)
  │
  └─ LLM query (with RAG context already in prompt)
```

### Collection metadata storage

`~/.archon/collections/<name>/meta.toml`:
```toml
name = "work-docs"
embedding_model = "text-embedding-3-small"
last_indexed = "2026-03-27T14:30:00"
doc_count = 142
chunk_count = 891
description = "Work contracts and project documentation from 2023–2026, covering employment terms, NDA templates, and delivery specifications."
centroid = "base64:<serialized_float32_array>"
```

ChromaDB data stored in `~/.archon/collections/<name>/chroma/`.

---

## Dependencies

New Python packages (to add to `pyproject.toml`):
- `chromadb` — local vector store
- `pypdf` — PDF text extraction
- `numpy` — centroid arithmetic
- `sentence-transformers` — optional local embedding fallback (soft dependency; imported only when `embedding_provider = "local"`)
