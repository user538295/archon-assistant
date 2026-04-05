**Purpose**: Documents the Search (Retrieval-Augmented Generation) subsystem — components, data flow, interfaces, and integration with the Archon gateway.
**Audience**: Backend engineers extending, maintaining, or operating the Search integration
**Status**: Stable
**Last reviewed**: 2026-03-26
**Next review**: 2026-06-26

# Search Architecture

## Principles

1. **Fully optional, zero-crash degradation.** Search is disabled by default. When the server is unreachable, Archon logs a warning and continues normally — no exceptions propagate to users.
2. **Python-native, offline-first.** No Node.js, no cloud services. The entire stack (LanceDB + fastembed + Chonkie + FastMCP) runs locally via Python.
3. **Lazy model loading.** Embedding and reranking models are loaded on first use, not at server startup. This keeps startup fast and avoids loading models that may never be called.
4. **Thread-safe ML backends.** fastembed models are not async-safe; all encoding and prediction runs via `asyncio.to_thread()` behind a double-checked lock.
5. **Separate process, separate lifecycle.** The Search server is a user-managed process (`archon search start/stop`), not owned by the Archon daemon. Archon probes it at startup and wires it in if available; it is never stopped at Archon shutdown.
6. **Shared URL, no per-user routing.** All users share the same Search server URL. Unlike the Archon MCP Server (which uses per-user paths), Search has no user isolation at the server layer.

---

## Overview

```
archon search install → SearchInstaller
  ├── uv pip install -e ".[search]"
  ├── Download ONNX models (fastembed, lazy)
  ├── Create ~/.archon/search/ data dir
  └── Register + start com.archon.search (macOS) / archon-search (Linux)

[search] enabled = true
[search] collections = ["~/.archon/history/sessions", "~/.archon/workspace"]
       │
       ▼
Gateway._ensure_search_server(host, port) ← TCP probe, 2s timeout
       │
       ▼ server.py:main() (on startup: deferred by default; up to sync_timeout_seconds if > 0)
SearchCollectionSync.sync(collections)
  ├── migrate archon-history → sessions (if needed)
  ├── drop orphaned managed collections
  ├── ingest new paths
  └── write sync_manifest.json
       │
       ├── reachable → search_url = "http://{host}:{port}/mcp"
       │                     passed to SessionManager + BackgroundAgentManager
       │
       └── unreachable → log warning → search_url = None → no Search this session

ClaudeSession(search_url)
  └── _build_mcp_servers()
        └── mcp_servers["search"] = {"type": "http", "url": search_url}
              → Claude SDK registers Search as MCP server for the session

claude/            archon.search.server (FastMCP HTTP)
  └── search() ──→ SearchPipeline.search()
                     ├── Embedder.embed_one(query)          [fastembed → thread pool]
                     ├── SearchStore.hybrid_search()        [RRF: vector + BM25]
                     └── Reranker.rerank()                  [cross-encoder → thread pool]
```

---

## Module breakdown

### `archon/search/_types.py` — Shared dataclasses

Leaf module with no imports from other `archon/search/` files. All other modules import from here.

| Dataclass | Fields |
|---|---|
| `ChunkRecord` | `doc_id: str`, `chunk_id: str`, `text: str`, `vector: list[float]`, `source_path: str`, `indexed_at: str` |
| `SearchResult` | `doc_id: str`, `chunk_id: str`, `text: str`, `score: float`, `source_path: str` |
| `DocumentInfo` | `doc_id: str`, `source_path: str`, `chunk_count: int`, `indexed_at: str` |
| `CollectionInfo` | `name: str`, `doc_count: int`, `chunk_count: int` |
| `IngestResult` | `doc_id: str`, `chunks_created: int`, `status: str` (`"ok"` \| `"error"`), `error: str \| None = None` |

`ChunkRecord.chunk_id` is empty string `""` when produced by `DocumentChunker`; the pipeline assigns the final `"{doc_id}-{idx:06d}"` format before writing to the store.

---

### `archon/search/store.py` — LanceDB vector store

`SearchStore` manages all LanceDB operations: creating collections, ingesting chunks, hybrid search, and document lifecycle.

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

#### `SearchStore` public interface

```python
class SearchStore:
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

### `archon/search/embedder.py` — Embedding backend

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

### `archon/search/reranker.py` — Reranking backend

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

### `archon/search/parser.py` — Document parser

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

### `archon/search/chunker.py` — Text chunker

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

### `archon/search/pipeline.py` — Orchestration

`SearchPipeline` is the single entry point for all ingest and search operations. It wires store, embedder, reranker, chunker, and parser together.

#### Document identity

```python
doc_id = hashlib.sha256(str(path.resolve()).encode()).hexdigest()
```

Re-ingesting the same file replaces its existing chunks (idempotent by path hash).

#### Binary extension filter

Frozenset of extensions that are skipped during `ingest_directory`: `.pyc`, `.dll`, `.so`, `.png`, `.jpg`, `.mp3`, `.mp4`, `.zip`, `.db`, `.parquet`, `.wasm`, etc. Skipped files are not counted as errors.

#### `SearchPipeline` public interface

```python
class SearchPipeline:
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
    cfg: SearchConfig,
    embedder_backend: EmbedderBackend | None = None,
    reranker_backend: RerankerBackend | None = None,
) -> SearchPipeline:
    """Build a SearchPipeline. Does NOT call store.connect()."""
```

The factory accepts optional backend overrides for testing. Tests pass `MockEmbedder` / `MockReranker` to bypass model downloads entirely.

---

### `archon/search/server.py` — FastMCP HTTP server

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

When `collection` is omitted, all tools fall back to `default_collection`. Tools never raise exceptions — all errors are returned as `{"error": message}` dicts.

`ingest_directory` uses `ctx.report_progress()` (FastMCP's progress API) to stream per-file progress to the caller.

#### Entry point

```python
# python -m archon.search.server
async def main() -> None:
    cfg = load_config()
    pipeline = create_pipeline(cfg.search)
    await pipeline.store.connect()
    app = create_app(pipeline)
    try:
        await app.run_http_async(host=cfg.search.host, port=cfg.search.port)
    finally:
        await pipeline.store.disconnect()
```

The HTTP endpoint served is `/mcp` (FastMCP standard). The gateway constructs `http://{host}:{port}/mcp` as the MCP server URL.

---

### `archon/search/install.py` — Installer

`SearchInstaller` implements the full installation workflow as discrete, independently testable methods.

#### `SearchInstaller` methods

| Method | Description |
|---|---|
| `check_deps()` | Returns list of missing packages from `_RAG_PACKAGES` |
| `detect_gpu()` | Runs `nvidia-smi`; returns `returncode == 0` |
| `install_deps(gpu: bool)` | Installs `fastembed-gpu` + `onnxruntime-gpu` if GPU; else `fastembed`. Always installs LanceDB, docling, markitdown, trafilatura, chonkie, fastmcp |
| `configure_providers(gpu: bool)` | Writes `providers = ["CUDAExecutionProvider"]` to `[search]` section via tomlkit if GPU |
| `create_data_dir()` | Creates the `db_path` directory |
| `write_service_file()` | Delegates to `get_search_service().register(dry_run=...)` |
| `load_service()` | Delegates to `get_search_service().start(dry_run=...)` |
| `_wait_for_service(timeout=60)` | Polls `http://{host}:{port}/health` until ready (1s intervals), printing progress dots |
| `create_history_collection()` | Ingests `{history_dir}/sessions/` into the default `sessions` collection |
| `run(non_interactive=False)` | Full install workflow |
| `run_uninstall(delete_db=False)` | Stop, unregister, optionally delete `~/.archon/search/db` |

**GPU detection** uses a subprocess call to `nvidia-smi`. No heuristics, no environment variables — if the command exits 0, GPU packages are installed. `providers` is then written to `config.toml` via tomlkit to preserve comments and formatting.

**Service readiness** (post-start): the installer polls `http://{host}:{port}/health` via `urllib.request.urlopen` with a 60-second timeout. This is separate from the gateway probe (which uses TCP).

---

### Platform services

#### Platform detection

`get_search_service()` in `archon/platform/__init__.py` selects the implementation using `sys.platform`:

| Platform | Class | Service name | Service file |
|---|---|---|---|
| macOS (`darwin`) | `LaunchdSearchService` | `com.archon.search` | `~/Library/LaunchAgents/com.archon.search.plist` |
| Linux | `SystemdSearchService` | `archon-search` | `~/.config/systemd/user/archon-search.service` |
| Windows | `WindowsSearchService` | `windows-search` | Stub only — logs warning |

All three implement the `PlatformService` ABC from `archon/platform/`.

#### macOS: `LaunchdSearchService`

- Plist label: `com.archon.search`
- Command: `{python} -m archon.search.server`
- Environment: `ARCHON_CONFIG={config_path}`
- Log file: `~/.archon/search/archon-search.log`
- `KeepAlive = true`, `RunAtLoad = true`

#### Linux: `SystemdSearchService`

- Service unit: `archon-search.service`
- Command: `{python} -m archon.search.server`
- Environment: `ARCHON_CONFIG={config_path}`
- `Restart=always`, `RestartSec=5`
- `WantedBy=default.target` (user-level; `enable-linger` is set)

#### Windows: `WindowsSearchService`

All lifecycle methods return 1 and log: `"Search service management not supported on Windows; run python -m archon.search.server manually"`. The server itself runs fine on Windows — only the OS-level service integration is stubbed.

---

## Gateway integration

The gateway (`archon/gateway/gateway.py`) wires Search in at startup:

```
cfg.search.enabled = true
         │
         ▼
_ensure_search_server(host, port)
  ├── host is not localhost/127.0.0.1 → skip probe, return True
  └── localhost → asyncio.open_connection(host, port, timeout=2.0)
                   ├── success → return True
                   └── OSError / TimeoutError → log warning, return False

search_url = "http://{host}:{port}/mcp"   if reachable
search_url = None                          if unreachable or disabled

SessionManager(search_url=search_url)
BackgroundAgentManager(search_url=search_url)
```

Passed `search_url` flows into every new `ClaudeSession` via `SessionManager._create_session()`:

```python
if self._search_url:
    mcp_servers["search"] = {"type": "http", "url": self._search_url}
```

All users share the same Search server; there is no per-user isolation at the server layer.

The Search server is **not stopped at Archon shutdown** — it is a user-owned process managed independently.

---

## Context injection

`ContextProvider.startup_context_prompt(search_enabled: bool)` is called at session startup:

```python
# HistoryCompactor (ContextProvider implementation)
search_section = (
    "\n\nA local Search tool is available as the `search` MCP tool. "
    "Use it to find specific topics, conversations, or documents by meaning "
    "instead of reading individual files. Call `search` with a natural-language "
    "query; it returns the most relevant chunks with source paths."
    if search_enabled
    else ""
)
```

`search_enabled = search_url is not None` — set by `SessionManager` and `Decomposer` from their stored `_search_url`.

---

## Multi-collection routing

When multiple collections are configured, `SearchContextProvider` (in `archon/ai/search_context_provider.py`) orchestrates a two-phase retrieval pipeline.

### Phase A — Routing

Called before `route_task()` via `SearchContextProvider.get_pre_context(query)`:

1. Fetch `CollectionMeta` records from the Search server via `MultiCollectionRouter.fetch_metadata()` (cached per `SearchContextProvider` instance; one HTTP call per session).
2. Resolve `pinned_collections` paths → collection names. Pinned collections bypass routing and always consume slots.
3. Compute `available_slots = max_parallel_collections - len(pinned_names)`.
4. Apply three-tier logic over the routable (non-pinned) collections:

| Tier | Condition | Behaviour |
|---|---|---|
| 1 | `n_routable ≤ 3` | Return `None`; skip decomposer; search all routable |
| 2 | `4 ≤ n_routable ≤ routing_shortlist_size` | Build `<search_collections>` block; decomposer selects |
| 3 | `n_routable > routing_shortlist_size` | Centroid pre-ranking → shortlist → decomposer selects |

**Centroid pre-ranking (Tier 3):** `MultiCollectionRouter.rank()` computes cosine similarity between the query embedding and each collection's centroid vector. Collections with a mismatched `embedding_model` are treated as `centroid=None` and placed after scored ones. If the top similarity is below `routing_confidence_threshold`, the entire shortlist is dropped and `[]` is returned.

**Confidence gate bypass:** If all collections have `centroid=None` (e.g., legacy collections before centroid support), the confidence gate is skipped and up to `routing_shortlist_size` collections are returned as-is.

### Decomposer context block format

When the decomposer is invoked (Tiers 2 and 3), a `<search_collections>` block is appended to the routing prompt:

```
<search_collections>
Available collections (select 1–N most relevant for this query, output their names in
<search_selected_collections>name1, name2</search_selected_collections> tags at the end of your routing decision):
- sessions: (no description)
- docs: Technical reference documents for the API
</search_collections>
```

`N` = `available_slots` (capped at 1 minimum). The decomposer outputs selected collection names in `<search_selected_collections>` tags.

### Phase B — Search and merge

Called after `route_task()` via `SearchContextProvider.search_and_prepare(task_output, query)`:

1. Determine collections to search based on tier:
   - Tier 1 (decomposer not invoked): all routable names (capped at `max_parallel_collections`) + pinned
   - Decomposer returned empty: pinned only
   - Tier 2/3: valid selected (filtered against `last_routable_names`, capped at `available_slots`) + pinned
2. Run parallel searches with `asyncio.Semaphore(max_parallel_collections)`.
3. Normalize scores per-collection: `(score - min) / (max - min)`; fallback to 0.5 when `max == min`.
4. Merge all results, sort descending, return top `top_k_return`.

---

### `MultiCollectionRouter`

`archon/search/router.py` — centroid-based pre-ranker. Key methods:

| Method | Description |
|---|---|
| `fetch_metadata()` | JSON-RPC call to `get_collections_meta`; cached after first call; returns `[]` on timeout |
| `rank(query_embedding, collections)` | Cosine similarity rank; applies confidence gate; returns up to `shortlist_size` |
| `select(query)` | Embed query + fetch metadata + rank (convenience wrapper) |
| `get_pre_context(query, pinned_names, available_slots)` | Full tier logic; returns `<search_collections>` block or `None` |

`last_routable_names` and `decomposer_was_invoked` are set as side-effects by `get_pre_context()` and consumed by `search_and_prepare()`.

---

### `SearchContextProvider`

`archon/ai/search_context_provider.py` — orchestrator for Pipeline. One instance per `Pipeline`, shared across all `send()` calls.

- Creates one `Embedder` instance (shared across router invocations to avoid repeated model loads).
- Creates a fresh `MultiCollectionRouter` per `send()` call (each call fetches metadata fresh from the server).
- `get_pre_context()` sets `_router` and `_pinned_names` as state consumed by `search_and_prepare()`.

---

## Configuration schema

`SearchConfig` dataclass in `archon/config/loader.py`:

```python
@dataclass
class SearchConfig:
    enabled: bool = False
    host: str = "localhost"
    port: int = 8282
    db_path: str = "~/.archon/search"
    collections: list[str] = field(default_factory=lambda: [
        "~/.archon/history/sessions",
        "~/.archon/workspace",
    ])
    sync_timeout_seconds: int = 0
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    providers: list[str] = field(default_factory=list)
    top_k_retrieve: int = 20
    top_k_return: int = 5
    chunk_size: int = 512
    max_parallel_collections: int = 3
    routing_confidence_threshold: float = 0.30
    routing_shortlist_size: int = 8
    pinned_collections: list[str] = field(default_factory=lambda: list(_DEFAULT_SEARCH_COLLECTIONS))
    auto_reindex_on_chunk_size_change: bool = True
    watch: bool = False
```

**Validation** (raises `ConfigError`):

- `port` must be 1–65535
- `sync_timeout_seconds` must be >= 0
- `top_k_return > 0`
- `top_k_retrieve > top_k_return`
- `chunk_size > 0`
- `max_parallel_collections >= 1`
- `routing_confidence_threshold` in `[0.0, 1.0]`
- `routing_shortlist_size >= 1`

`providers = []` → CPU (ONNX default). `providers = ["CUDAExecutionProvider"]` → NVIDIA GPU. Written automatically by `SearchInstaller.configure_providers()` when GPU is detected.

If `history_collection` is present in config, the loader emits a deprecation warning and ignores the value. See migration notes below.

---

## Collection management

### Declarative sync model

`[search] collections` in config declares the *desired state* — a list of filesystem paths that should be indexed. `SearchCollectionSync.sync()` reconciles this list with the actual LanceDB tables on every service startup.

**Sync algorithm (`archon/search/sync.py`):**

1. Run migration: rename `archon-history` → `sessions` if only the legacy table exists.
2. Build desired mapping `{collection_name: resolved_path}` using `_build_desired()`.
3. Load manifest → set of managed collection names.
4. Drop `(existing ∩ managed) − desired` (orphaned managed collections).
5. Record skipped = `existing − managed − desired` (unmanaged collections are never touched).
6. Ingest new collections = `desired − existing`.
7. Unchanged = `existing ∩ desired`.
8. Write manifest atomically.

### Manifest file

`{db_path}/sync_manifest.json` tracks which collections Archon created. Default path: `~/.archon/search/sync_manifest.json`.

Structure: `{"collection_name": "resolved_source_path", ...}`

Collections absent from the manifest are "unmanaged" — sync never drops them. This allows LanceDB tables created outside Archon (e.g., by direct MCP `ingest_directory` calls) to coexist without interference.

### Collection name derivation

`path_to_collection_name(path)` produces the collection name:

1. Expand `~` and resolve to absolute path.
2. Take the last path component and lowercase it.
3. Replace non-alphanumeric runs with `_`, strip leading/trailing `_`.
4. Fall back to `"collection"` if the result is empty.

**Collision resolution** (`SearchCollectionSync._build_desired()`): if two configured paths share the same basename, `_name_at_depth()` walks up the tree including progressively more parent components until all names are unique. If still colliding after exhausting the path depth, a 6-character SHA-1 hash suffix is appended (`{base}_{sha1[:6]}`).

### Startup sync behavior

The Search server calls `SearchCollectionSync.sync()` at startup in `server.py:main()`. `sync_timeout_seconds` controls how long the gateway waits:

- `> 0` → gateway awaits sync for up to that many seconds; if it times out, sync continues in background.
- `= 0` → sync runs entirely in background; gateway proceeds immediately.

### CLI management

`archon/cli/search_cmd.py` implements imperative management for individual collections:

| Command | Behaviour |
|---|---|
| `archon search sync` | Run `SearchCollectionSync.sync()` immediately; prints added/removed/unchanged counts. Warns if service is running (write conflicts possible). |
| `archon search collection list` | Lists LanceDB collections with path, doc/chunk counts, and status (`indexed` / `orphan (managed)` / `unmanaged`). |
| `archon search collection add <path>` | Appends path to `[search] collections` in config, then ingests the directory. Config is updated first — ingest failure is recoverable with `archon search sync`. |
| `archon search collection remove <path>` | Drops LanceDB collection, removes path from config, cleans up manifest. Requires service to be stopped; `--force` bypasses the check; `--dry-run` prints planned changes without executing (`--dry-run` and `--force` are mutually exclusive). |
| `archon search collection info <name>` | Prints `CollectionMeta` fields: name, description, doc_count, chunk_count, embedding_model, centroid presence, last_indexed. Calls `SearchPipeline.get_collection_meta(name)`. |
| `archon search collection reindex <name>` | Resolves source path from config, calls `SearchPipeline.ingest_directory(..., force_regenerate_description=True)`. Requires service to be stopped. Regenerates embeddings, centroid, and description. |

### Doctor health checks

`archon/cli/doctor.py:_check_search_health()` runs two categories of checks:

**Config-only (always runs, no server required):**
- Each path in `pinned_collections` is checked against `collections`. A pinned path not in `collections` is not a managed collection and will be silently skipped at runtime. A warning is printed.

**Live checks (skipped if Search server is unreachable):**
- Calls `get_collections_meta` via JSON-RPC on `http://{host}:{port}`.
- Per collection: staleness (last indexed > 7 days), embedding model mismatch vs. `search.embedding_model`, empty (`doc_count == 0`), missing centroid (routing disabled for that collection).

---

### Migration from `history_collection`

Legacy configs used `history_collection = "archon-history"`. The migration path:

- Config loader: if `history_collection` is present, logs a deprecation warning (`[search] history_collection is no longer supported`) and ignores it.
- `_maybe_migrate()` in `SearchCollectionSync`: on first sync, if `archon-history` LanceDB table exists and `sessions` does not, renames the table to `sessions` and updates the manifest. If both exist, logs a warning and skips — manual cleanup required.

---

## Watch mode (`[search] watch`)

When `watch = true` in `config.toml`, the Search server automatically re-indexes collections when files change on disk — no manual `archon search sync` required.

**Config key**: `[search] watch = false` (default). Set `true` to enable. Requires `watchdog>=3.0` (`uv sync --extra search`).

**Components** (all in `archon/search/watcher.py`):

- `_DebounceHandler` (watchdog `FileSystemEventHandler`): receives raw filesystem events; debounces rapid writes with a per-collection 5-second `threading.Timer`. When the timer fires it submits `sync_collection()` to the asyncio event loop via `asyncio.run_coroutine_threadsafe`.
- `CollectionWatcher`: owns one watchdog `Observer` per collection directory. `start()` logs a warning and returns without raising if `watchdog` is not installed. `stop()` (called from `asyncio.to_thread`) stops and joins the observer thread.
- `WatcherManager`: manages all per-collection `CollectionWatcher` instances. `add(name, path)` creates and starts a watcher. `stop_all()` stops all watchers concurrently, then waits up to 10 seconds for in-flight `sync_collection` coroutines to drain before returning.

**Sync callback** (`SearchCollectionSync.sync_collection()`):

Called by the watcher callback. Reads current indexing state, detects new/changed/deleted files via `_check_collection_changes`, then calls `_apply_collection_changes` if any delta is found. A no-op when `_state_store is None`. Uses the same per-collection `asyncio.Lock` as `sync()` — manual `archon search sync` and watch-triggered syncs are serialised and cannot conflict.

**Server lifecycle** (`archon/search/server.py`):

```python
if cfg.search.watch:
    watcher_manager = WatcherManager(on_change=_on_change, loop=loop)
    for col_name, path in desired.items():
        watcher_manager.add(col_name, path)
# ...
finally:
    if watcher_manager is not None:
        await watcher_manager.stop_all()   # drains in-flight syncs
    await pipeline.store.disconnect()
```

`watcher_manager.stop_all()` is wrapped in `try/except` so a watcher shutdown error never prevents `pipeline.store.disconnect()` from running.

**Status visibility**:

- `archon search status` appends `(watch)` to the status column for `DONE` and `IN_PROGRESS` (rendered as `partial`) collections when watch mode is active.
- `search_status` MCP response includes `"watching": true/false` on each collection dict (global config value, same for all collections).

---

## Data flow: ingest

```
archon search ingest /docs --collection my-docs
         │
         ▼
SearchPipeline.ingest_directory(path, collection)
  ├── glob all files, filter binary extensions
  ├── for each file:
  │     DocumentParser.parse(path) → Markdown string
  │     DocumentChunker.chunk(text, doc_id, source_path) → list[ChunkRecord]
  │     Embedder.embed([chunk.text for chunk in chunks]) → list[list[float]]
  │     assign chunk_ids: "{doc_id}-{idx:06d}"
  │     assign vectors to chunks
  │     SearchStore.ingest_chunks(collection, chunks)
  │     → progress_cb(done, total)
  └── SearchStore.rebuild_fts_index(collection)   ← once at end
```

`doc_id = sha256(str(path.resolve()).encode()).hexdigest()`

---

## Data flow: search

```
Claude calls search("archon session management", collection="archon-history")
         │
         ▼
SearchPipeline.search(query, collection)
  ├── Embedder.embed_one(query) → query_vector [asyncio.to_thread]
  ├── SearchStore.hybrid_search(collection, query_vector, query, top_k_retrieve)
  │     ├── vector_search(query_vector, fetch=max(top_k*3, 20))
  │     ├── fts_search(query, fetch=max(top_k*3, 20))
  │     └── RRF merge → top top_k_retrieve candidates
  └── Reranker.rerank(query, candidates, top_k_return) [asyncio.to_thread]
        └── returns top top_k_return SearchResult objects
```

Default pipeline: retrieve 20 → rerank → return 5.

---

## Testing

Tests live in `tests/search/` with one file per module. All tests use mock backends — no real model downloads:

```python
class MockEmbedder:
    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 384 for _ in texts]   # fixed dimension

class MockReranker:
    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        return [1.0 / (i + 1) for i in range(len(pairs))]  # deterministic ranking
```

Store tests use a `tmp_path` LanceDB database (real LanceDB, in-memory-equivalent via temp dir).

**Coverage target:** ≥ 85% for `archon/search/`.

---

## See also

- [Services and Integration Architecture](120_services_and_integration_architecture.md) — Search MCP integration section
- [Data Architecture and Persistence](130_data_architecture_and_persistence.md) — LanceDB storage paths
- [ADR 09 — Search history format](../ADRs/09_search_history_format.md) — decision record
- [Search User Guide](../UserManual/search_guide.md) — operator installation and usage
- [FEAT-019 Research](../Completed/26_search_integration_research.md) — technology selection rationale
