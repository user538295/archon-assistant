**Purpose**: Documents the RAG (Retrieval-Augmented Generation) subsystem — components, data flow, interfaces, and integration with the Archon gateway.
**Audience**: Backend engineers extending, maintaining, or operating the RAG integration
**Status**: Stable
**Last reviewed**: 2026-03-26
**Next review**: 2026-06-26

# RAG Architecture

## Principles

1. **Fully optional, zero-crash degradation.** RAG is disabled by default. When the server is unreachable, Archon logs a warning and continues normally — no exceptions propagate to users.
2. **Python-native, offline-first.** No Node.js, no cloud services. The entire stack (LanceDB + fastembed + Chonkie + FastMCP) runs locally via Python.
3. **Lazy model loading.** Embedding and reranking models are loaded on first use, not at server startup. This keeps startup fast and avoids loading models that may never be called.
4. **Thread-safe ML backends.** fastembed models are not async-safe; all encoding and prediction runs via `asyncio.to_thread()` behind a double-checked lock.
5. **Separate process, separate lifecycle.** The RAG server is a user-managed process (`archon rag start/stop`), not owned by the Archon daemon. Archon probes it at startup and wires it in if available; it is never stopped at Archon shutdown.
6. **Shared URL, no per-user routing.** All users share the same RAG server URL. Unlike the Archon MCP Server (which uses per-user paths), RAG has no user isolation at the server layer.

---

## Overview

```
archon rag install → RagInstaller
  ├── uv pip install -e ".[rag]"
  ├── Download ONNX models (fastembed, lazy)
  ├── Create ~/.archon/rag/ data dir
  └── Register + start com.archon.rag (macOS) / archon-rag (Linux)

[rag] enabled = true
       │
       ▼
Gateway._ensure_rag_server(host, port) ← TCP probe, 2s timeout
       │
       ├── reachable → rag_url = "http://{host}:{port}/mcp"
       │                     passed to SessionManager + BackgroundAgentManager
       │
       └── unreachable → log warning → rag_url = None → no RAG this session

ClaudeSession(rag_url)
  └── _build_mcp_servers()
        └── mcp_servers["rag"] = {"type": "http", "url": rag_url}
              → Claude SDK registers RAG as MCP server for the session

claude/            archon.rag.server (FastMCP HTTP)
  └── search() ──→ RagPipeline.search()
                     ├── Embedder.embed_one(query)          [fastembed → thread pool]
                     ├── RagStore.hybrid_search()           [RRF: vector + BM25]
                     └── Reranker.rerank()                  [cross-encoder → thread pool]
```

---

## Module breakdown

### `archon/rag/_types.py` — Shared dataclasses

Leaf module with no imports from other `archon/rag/` files. All other modules import from here.

| Dataclass | Fields |
|---|---|
| `ChunkRecord` | `doc_id: str`, `chunk_id: str`, `text: str`, `vector: list[float]`, `source_path: str`, `indexed_at: str` |
| `SearchResult` | `doc_id: str`, `chunk_id: str`, `text: str`, `score: float`, `source_path: str` |
| `DocumentInfo` | `doc_id: str`, `source_path: str`, `chunk_count: int`, `indexed_at: str` |
| `CollectionInfo` | `name: str`, `doc_count: int`, `chunk_count: int` |
| `IngestResult` | `doc_id: str`, `chunks_created: int`, `status: str` (`"ok"` \| `"error"`), `error: str \| None = None` |

`ChunkRecord.chunk_id` is empty string `""` when produced by `DocumentChunker`; the pipeline assigns the final `"{doc_id}-{idx:06d}"` format before writing to the store.

---

### `archon/rag/store.py` — LanceDB vector store

`RagStore` manages all LanceDB operations: creating collections, ingesting chunks, hybrid search, and document lifecycle.

#### LanceDB schema (per collection table)

| Column | Arrow type | Notes |
|---|---|---|
| `doc_id` | `pa.utf8()` | SHA-256 hex digest of the resolved file path |
| `chunk_id` | `pa.utf8()` | `"{doc_id}-{idx:06d}"` |
| `text` | `pa.utf8()` | Chunk content |
| `vector` | `pa.list_(pa.float32(), embedding_dim)` | Dense embedding |
| `source_path` | `pa.utf8()` | Original file path string |
| `indexed_at` | `pa.utf8()` | ISO-8601 UTC timestamp |

#### Validation constants

| Regex | Variable | Validates |
|---|---|---|
| `^[a-f0-9]{64}$` | `_DOC_ID_RE` | doc_id (SHA-256 output) |
| `^[a-f0-9]{64}-\d{6}$` | `_CHUNK_ID_RE` | chunk_id format |
| `^[a-zA-Z0-9][a-zA-Z0-9_-]{0,64}$` | `_COLLECTION_RE` | Collection names (alphanumeric start, max 65 chars) |

Every method that accepts `collection: str` or `doc_id: str` validates these patterns and raises `ValueError` on mismatch before touching the database.

#### Hybrid search — Reciprocal Rank Fusion (RRF)

```
k = 60  (RRF constant)

1. Vector search → top (max(top_k * 3, 20)) candidates
2. BM25 FTS search → top (max(top_k * 3, 20)) candidates
   (gracefully degrades to vector-only if no FTS index)
3. For each chunk in union of results:
     score = Σ 1.0 / (k + rank + 1)  across both ranked lists
4. Sort descending, return top_k
```

FTS index is built by `rebuild_fts_index(collection)` using `table.create_index("text", config=FTS(), replace=True)`. It is called once after a batch ingest — not after every individual chunk write.

#### `RagStore` public interface

```python
class RagStore:
    def __init__(self, db_path: str | Path) -> None: ...
    async def connect(self) -> None: ...           # lancedb.connect_async(db_path)
    async def disconnect(self) -> None: ...        # clears reference; LanceDB has no explicit close

    async def ensure_collection(self, collection: str, embedding_dim: int) -> None: ...
    async def list_collections(self) -> list[CollectionInfo]: ...
    async def ingest_chunks(self, collection: str, chunks: list[ChunkRecord]) -> int: ...
    async def rebuild_fts_index(self, collection: str) -> None: ...
    async def hybrid_search(
        self, collection: str, query_vector: list[float],
        query_text: str, top_k: int,
    ) -> list[SearchResult]: ...
    async def delete_document(self, collection: str, doc_id: str) -> int: ...
    async def list_documents(self, collection: str, limit: int = 100) -> list[DocumentInfo]: ...
    async def fetch_adjacent_chunks(
        self, collection: str, doc_id: str, center_idx: int, window: int,
    ) -> list[ChunkRecord]: ...
```

---

### `archon/rag/embedder.py` — Embedding backend

#### Thread-safety pattern

fastembed's `TextEmbedding` is not async-safe. `ModelEmbedder` uses a **double-checked lock** for one-time lazy model initialisation, then dispatches encoding to a thread pool via `asyncio.to_thread()`.

```python
class ModelEmbedder:
    def encode(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            with self._lock:
                if self._model is None:          # double-checked lock
                    from fastembed import TextEmbedding
                    self._model = TextEmbedding(self._model_name, providers=self._providers)
        return [e.tolist() for e in self._model.embed(texts)]
```

#### `Embedder` async wrapper

```python
class Embedder:
    @property
    def embedding_dim(self) -> int: ...       # cached after first embed(); raises RuntimeError before

    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_one(self, text: str) -> list[float]: ...   # convenience: embed([text])[0]
```

`embed()` runs `backend.encode()` via `asyncio.to_thread()` and caches the vector dimension on the first call.

#### Factory

```python
def make_embedder(model_name: str, providers: list[str] | None = None) -> Embedder:
    return Embedder(ModelEmbedder(model_name, providers=providers))
```

#### `EmbedderBackend` protocol

```python
@runtime_checkable
class EmbedderBackend(Protocol):
    def encode(self, texts: list[str]) -> list[list[float]]: ...
```

Tests inject a `MockEmbedder` that returns deterministic fixed-dimension vectors — no real model download required in tests.

---

### `archon/rag/reranker.py` — Reranking backend

Same thread-safety pattern as the embedder: double-checked lock + `asyncio.to_thread()`.

```python
class ModelReranker:
    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        # pairs: [(query, doc1), (query, doc2), ...]
        if not pairs:
            return []
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from fastembed import TextCrossEncoder
                    self._model = TextCrossEncoder(self._model_name, providers=self._providers)
        query = pairs[0][0]
        documents = [p[1] for p in pairs]
        return list(self._model.rerank(query, documents))

class Reranker:
    async def rerank(
        self, query: str, candidates: list[SearchResult], top_k: int,
    ) -> list[SearchResult]: ...   # mutates .score in-place; returns top_k sorted descending
```

#### `RerankerBackend` protocol

```python
@runtime_checkable
class RerankerBackend(Protocol):
    def predict(self, pairs: list[tuple[str, str]]) -> list[float]: ...
```

---

### `archon/rag/parser.py` — Document parser

`DocumentParser.parse(path: Path) -> str` routes by file extension:

| Extensions | Library | Method |
|---|---|---|
| `.md`, `.txt`, `.py`, `.js`, `.ts`, `.go`, `.rs`, `.java`, `.sh`, `.yaml`, `.yml`, `.json`, `.toml`, `.csv` + unknown | `Path.read_text(errors="replace")` | `_parse_plain` |
| `.html`, `.htm` | `trafilatura.extract(include_tables=True, include_links=False)` | `_parse_html` |
| `.pdf` | `docling.DocumentConverter().convert(path).document.export_to_markdown()` | `_parse_pdf` |
| `.docx`, `.pptx`, `.xlsx` | `markitdown.MarkItDown().convert(path).text_content` | `_parse_office` |

All handlers run in a thread pool via `asyncio.to_thread()`.

`ParseError` wraps any exception from the underlying library:

```python
class ParseError(Exception):
    def __init__(self, path: Path, cause: Exception) -> None:
        super().__init__(f"Failed to parse {path}: {cause}")
        self.path = path
        self.cause = cause
```

---

### `archon/rag/chunker.py` — Text chunker

`DocumentChunker` wraps Chonkie's `RecursiveChunker` with a GPT-2 tokenizer.

```python
class DocumentChunker:
    def __init__(self, chunk_size: int = 512) -> None:
        from chonkie import RecursiveChunker
        self._chunker = RecursiveChunker(tokenizer="gpt2", chunk_size=chunk_size)

    def chunk(self, text: str, doc_id: str, source_path: str) -> list[ChunkRecord]:
        ...
```

- **Chunk ID**: empty string `""` in the returned records — the pipeline assigns sequential `"{doc_id}-{idx:06d}"` IDs before calling `ingest_chunks()`.
- **Timestamp**: `datetime.now(timezone.utc).isoformat()` per chunk.
- Empty text input returns an empty list (no crash).

> `chonkie` is installed bare (no `[all]` extras) to avoid pulling in `sentence-transformers` and PyTorch.

---

### `archon/rag/pipeline.py` — Orchestration

`RagPipeline` is the single entry point for all ingest and search operations. It wires store, embedder, reranker, chunker, and parser together.

#### Document identity

```python
doc_id = hashlib.sha256(str(path.resolve()).encode()).hexdigest()
```

Re-ingesting the same file replaces its existing chunks (idempotent by path hash).

#### Binary extension filter

Frozenset of extensions that are skipped during `ingest_directory`: `.pyc`, `.dll`, `.so`, `.png`, `.jpg`, `.mp3`, `.mp4`, `.zip`, `.db`, `.parquet`, `.wasm`, etc. Skipped files are not counted as errors.

#### `RagPipeline` public interface

```python
class RagPipeline:
    async def ingest_file(
        self, path: Path, collection: str, rebuild_fts: bool = True,
    ) -> IngestResult: ...

    async def ingest_directory(
        self,
        path: Path,
        collection: str,
        glob_pattern: str = "**/*",
        progress_cb: Callable[[int, int], Awaitable[None]] | None = None,
    ) -> list[IngestResult]: ...   # rebuilds FTS once at the end

    async def search(self, query: str, collection: str) -> list[SearchResult]: ...

    async def search_with_context(
        self, query: str, collection: str, context_window: int = 1,
    ) -> list[dict[str, Any]]: ...  # adds context_before / context_after keys

    async def delete_document(self, doc_id: str, collection: str) -> int: ...
    async def list_collections(self) -> list[CollectionInfo]: ...
    async def list_documents(self, collection: str, limit: int = 100) -> list[DocumentInfo]: ...
```

#### Factory function

```python
def create_pipeline(
    cfg: RagConfig,
    embedder_backend: EmbedderBackend | None = None,
    reranker_backend: RerankerBackend | None = None,
) -> RagPipeline:
    """Build a RagPipeline. Does NOT call store.connect()."""
```

The factory accepts optional backend overrides for testing. Tests pass `MockEmbedder` / `MockReranker` to bypass model downloads entirely.

---

### `archon/rag/server.py` — FastMCP HTTP server

`create_app(pipeline, default_collection)` registers 7 MCP tools on a FastMCP instance.

#### Tool signatures

| Tool | Parameters | Returns | Error handling |
|---|---|---|---|
| `search` | `query: str`, `collection: str \| None = None` | `list[dict]` | `[{"error": str}]` |
| `search_with_context` | `query: str`, `collection: str \| None = None`, `context_window: int = 1` | `list[dict]` | `[{"error": str}]` |
| `ingest_file` | `path: str`, `collection: str \| None = None` | `dict` | `{"error": str}` |
| `ingest_directory` | `path: str`, `glob_pattern: str = "**/*"`, `collection: str \| None = None`, `ctx: Context \| None = None` | `list[dict]` | `[{"error": str}]` |
| `list_collections` | — | `list[dict]` | `[{"error": str}]` |
| `list_documents` | `collection: str \| None = None`, `limit: int = 100` | `list[dict]` | `[{"error": str}]` |
| `delete_document` | `doc_id: str`, `collection: str \| None = None` | `dict` | `{"error": str}` |

When `collection` is omitted, all tools fall back to `default_collection` (the `history_collection` from config). Tools never raise exceptions — all errors are returned as `{"error": message}` dicts.

`ingest_directory` uses `ctx.report_progress()` (FastMCP's progress API) to stream per-file progress to the caller.

#### Entry point

```python
# python -m archon.rag.server
async def main() -> None:
    cfg = load_config()
    pipeline = create_pipeline(cfg.rag)
    await pipeline.store.connect()
    app = create_app(pipeline, cfg.rag.history_collection)
    try:
        await app.run_http_async(host=cfg.rag.host, port=cfg.rag.port)
    finally:
        await pipeline.store.disconnect()
```

The HTTP endpoint served is `/mcp` (FastMCP standard). The gateway constructs `http://{host}:{port}/mcp` as the MCP server URL.

---

### `archon/rag/install.py` — Installer

`RagInstaller` implements the full installation workflow as discrete, independently testable methods.

#### `RagInstaller` methods

| Method | Description |
|---|---|
| `check_deps()` | Returns list of missing packages from `_RAG_PACKAGES` |
| `detect_gpu()` | Runs `nvidia-smi`; returns `returncode == 0` |
| `install_deps(gpu: bool)` | Installs `fastembed-gpu` + `onnxruntime-gpu` if GPU; else `fastembed`. Always installs LanceDB, docling, markitdown, trafilatura, chonkie, fastmcp |
| `configure_providers(gpu: bool)` | Writes `providers = ["CUDAExecutionProvider"]` to `[rag]` section via tomlkit if GPU |
| `create_data_dir()` | Creates the `db_path` directory |
| `write_service_file()` | Delegates to `get_rag_service().register(dry_run=...)` |
| `load_service()` | Delegates to `get_rag_service().start(dry_run=...)` |
| `_wait_for_service(timeout=30)` | Polls `http://{host}:{port}/health` until ready (1s intervals) |
| `create_history_collection()` | Ingests `{history_dir}/sessions/` into `history_collection` |
| `run(non_interactive=False)` | Full install workflow |
| `run_uninstall(delete_db=False)` | Stop, unregister, optionally delete `~/.archon/rag/db` |

**GPU detection** uses a subprocess call to `nvidia-smi`. No heuristics, no environment variables — if the command exits 0, GPU packages are installed. `providers` is then written to `config.toml` via tomlkit to preserve comments and formatting.

**Service readiness** (post-start): the installer polls `http://{host}:{port}/health` via `urllib.request.urlopen` with a 30-second timeout. This is separate from the gateway probe (which uses TCP).

---

### Platform services

#### Platform detection

`get_rag_service()` in `archon/platform/__init__.py` selects the implementation using `sys.platform`:

| Platform | Class | Service name | Service file |
|---|---|---|---|
| macOS (`darwin`) | `LaunchdRagService` | `com.archon.rag` | `~/Library/LaunchAgents/com.archon.rag.plist` |
| Linux | `SystemdRagService` | `archon-rag` | `~/.config/systemd/user/archon-rag.service` |
| Windows | `WindowsRagService` | `windows-rag` | Stub only — logs warning |

All three implement the `PlatformService` ABC from `archon/platform/`.

#### macOS: `LaunchdRagService`

- Plist label: `com.archon.rag`
- Command: `{python} -m archon.rag.server`
- Environment: `ARCHON_CONFIG={config_path}`
- Log file: `~/.archon/rag/archon-rag.log`
- `KeepAlive = true`, `RunAtLoad = true`

#### Linux: `SystemdRagService`

- Service unit: `archon-rag.service`
- Command: `{python} -m archon.rag.server`
- Environment: `ARCHON_CONFIG={config_path}`
- `Restart=always`, `RestartSec=5`
- `WantedBy=default.target` (user-level; `enable-linger` is set)

#### Windows: `WindowsRagService`

All lifecycle methods return 1 and log: `"RAG service management not supported on Windows; run python -m archon.rag.server manually"`. The server itself runs fine on Windows — only the OS-level service integration is stubbed.

---

## Gateway integration

The gateway (`archon/gateway/gateway.py`) wires RAG in at startup:

```
cfg.rag.enabled = true
         │
         ▼
_ensure_rag_server(host, port)
  ├── host is not localhost/127.0.0.1 → skip probe, return True
  └── localhost → asyncio.open_connection(host, port, timeout=2.0)
                   ├── success → return True
                   └── OSError / TimeoutError → log warning, return False

rag_url = "http://{host}:{port}/mcp"   if reachable
rag_url = None                          if unreachable or disabled

SessionManager(rag_url=rag_url)
BackgroundAgentManager(rag_url=rag_url)
```

Passed `rag_url` flows into every new `ClaudeSession` via `SessionManager._create_session()`:

```python
if self._rag_url:
    mcp_servers["rag"] = {"type": "http", "url": self._rag_url}
```

All users share the same RAG server; there is no per-user isolation at the server layer.

The RAG server is **not stopped at Archon shutdown** — it is a user-owned process managed independently.

---

## Context injection

`ContextProvider.startup_context_prompt(rag_enabled: bool)` is called at session startup:

```python
# HistoryCompactor (ContextProvider implementation)
rag_section = (
    "\n\nA local RAG search tool is available as the `search` MCP tool. "
    "Use it to find specific topics, conversations, or documents by meaning "
    "instead of reading individual files. Call `search` with a natural-language "
    "query; it returns the most relevant chunks with source paths."
    if rag_enabled
    else ""
)
```

`rag_enabled = rag_url is not None` — set by `SessionManager` and `Decomposer` from their stored `_rag_url`.

---

## Configuration schema

`RagConfig` dataclass in `archon/config/loader.py`:

```python
@dataclass
class RagConfig:
    enabled: bool = False
    host: str = "localhost"
    port: int = 8282
    db_path: str = "~/.archon/rag"
    history_collection: str = "archon-history"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    providers: list[str] = field(default_factory=list)
    top_k_retrieve: int = 20
    top_k_return: int = 5
    chunk_size: int = 512
```

**Validation** (raises `ConfigError`):

- `port` must be 1–65535
- `top_k_return > 0`
- `top_k_retrieve > top_k_return`
- `chunk_size > 0`

`providers = []` → CPU (ONNX default). `providers = ["CUDAExecutionProvider"]` → NVIDIA GPU. Written automatically by `RagInstaller.configure_providers()` when GPU is detected.

---

## Data flow: ingest

```
archon rag ingest /docs --collection my-docs
         │
         ▼
RagPipeline.ingest_directory(path, collection)
  ├── glob all files, filter binary extensions
  ├── for each file:
  │     DocumentParser.parse(path) → Markdown string
  │     DocumentChunker.chunk(text, doc_id, source_path) → list[ChunkRecord]
  │     Embedder.embed([chunk.text for chunk in chunks]) → list[list[float]]
  │     assign chunk_ids: "{doc_id}-{idx:06d}"
  │     assign vectors to chunks
  │     RagStore.ingest_chunks(collection, chunks)
  │     → progress_cb(done, total)
  └── RagStore.rebuild_fts_index(collection)   ← once at end
```

`doc_id = sha256(str(path.resolve()).encode()).hexdigest()`

---

## Data flow: search

```
Claude calls search("archon session management", collection="archon-history")
         │
         ▼
RagPipeline.search(query, collection)
  ├── Embedder.embed_one(query) → query_vector [asyncio.to_thread]
  ├── RagStore.hybrid_search(collection, query_vector, query, top_k_retrieve)
  │     ├── vector_search(query_vector, fetch=max(top_k*3, 20))
  │     ├── fts_search(query, fetch=max(top_k*3, 20))
  │     └── RRF merge → top top_k_retrieve candidates
  └── Reranker.rerank(query, candidates, top_k_return) [asyncio.to_thread]
        └── returns top top_k_return SearchResult objects
```

Default pipeline: retrieve 20 → rerank → return 5.

---

## Testing

Tests live in `tests/rag/` with one file per module. All tests use mock backends — no real model downloads:

```python
class MockEmbedder:
    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 384 for _ in texts]   # fixed dimension

class MockReranker:
    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        return [1.0 / (i + 1) for i in range(len(pairs))]  # deterministic ranking
```

Store tests use a `tmp_path` LanceDB database (real LanceDB, in-memory-equivalent via temp dir).

**Coverage target:** ≥ 85% for `archon/rag/`.

---

## See also

- [Services and Integration Architecture](120_services_and_integration_architecture.md) — RAG MCP integration section
- [Data Architecture and Persistence](130_data_architecture_and_persistence.md) — LanceDB storage paths
- [ADR 09 — RAG history format](../ADRs/09_rag_history_format.md) — decision record
- [RAG User Guide](../UserManual/rag_guide.md) — operator installation and usage
- [FEAT-019 Research](../Completed/26_rag_integration_research.md) — technology selection rationale
