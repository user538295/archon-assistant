# FEAT-019 — Optional RAG Integration (replaces QMD)
**Purpose**: Replace the Node.js QMD dependency with a fully Python-native, local-first RAG stack (`archon/rag/`) that Claude Code can query via MCP for semantic + keyword search over conversation history and user-specified document collections.
**Audience**: Archon operators who want richer search over their history and documents without Node.js dependencies or external services.
**Status**: To Do

---

## Background

Archon currently optionally integrates with QMD (`@tobilu/qmd`), a Node.js tool that provides vector search over conversation history. QMD requires Node.js ≥ 22 or Bun ≥ 1.0, downloads ~3 GB of GGUF models at first run, and runs as an HTTP MCP daemon. The Python ecosystem now provides a superior local-first stack (LanceDB + Docling + ModernBERT + FastMCP) that: eliminates the Node.js dependency, supports every required document format natively, delivers hybrid BM25+vector search with cross-encoder reranking, and integrates with Archon's existing MCP wiring unchanged. Full technology selection rationale is in [Documentation/Completed/26_rag_integration_research.md](../Completed/26_rag_integration_research.md).

## Goal

An operator runs `archon rag install`, answers a few prompts, and afterward Claude Code has access to a `search` MCP tool backed by a persistent local daemon that: indexes conversation history automatically, accepts arbitrary document collections the user defines, and answers semantic + keyword queries entirely offline. The QMD config section, binary, and installer script are removed; `[rag]` config replaces `[qmd]` with no migration path.

---

## Scope

### In Scope
- New `archon/rag/` sub-package: store, embedder, reranker, parser, chunker, pipeline, FastMCP server, installer
- Optional `[rag]` extras group in `pyproject.toml` (install with `uv pip install -e ".[rag]"`)
- `[rag]` config section replacing `[qmd]` (no migration — clean break)
- `archon rag` CLI subcommand group: `install`, `uninstall`, `start`, `stop`, `status`, `ingest`
- macOS launchd service `com.archon.rag` (Linux systemd in phase 7, Windows stub only)
- 7 MCP tools: `search`, `search_with_context`, `ingest_file`, `ingest_directory`, `list_collections`, `list_documents`, `delete_document`
- `archon rag ingest [path] [--collection name]` — defaults to history dir if no path given
- System prompt update: `rag_enabled=True` text references the `search` tool specifically
- Full rename of all `qmd_url` / `qmd_enabled` symbols → `rag_url` / `rag_enabled`
- Remove `scripts/qmd_installer.sh` and `_ensure_qmd_daemon` from gateway

### Out of Scope
- Windows service registration (stub only — manual run documented)
- Cloud embedding / reranking APIs
- Automatic re-indexing on file change (inotify/FSEvents watcher)
- Web UI for collection management
- Migration of existing QMD collections
- ONNX export / GPU-specific optimization

---

## Acceptance criteria
- [ ] `uv pip install -e ".[rag]"` installs all RAG dependencies without errors
- [ ] `archon rag install` completes on macOS: creates `~/.archon/rag/`, registers `com.archon.rag` launchd service, runs initial ingest of history collection
- [ ] `python -m archon.rag.server` starts a FastMCP HTTP server and exposes all 7 tools
- [ ] `search("archon")` returns ranked results with text, source_path, and score
- [ ] `ingest_file("/path/to/doc.pdf")` parses, chunks, embeds, and stores a PDF
- [ ] `ingest_directory("/path", collection="my-docs")` ingests all supported files with progress
- [ ] Archon gateway connects to the RAG server if `[rag] enabled = true` and server is running
- [ ] Archon gateway logs a warning and continues without RAG if server is unreachable
- [ ] `startup_context_prompt(rag_enabled=True)` references the `search` MCP tool by name
- [ ] `archon rag status` shows service state and collection statistics
- [ ] `archon rag ingest` re-ingests the history collection; `archon rag ingest /path --collection my-docs` ingests a custom path
- [ ] All `qmd` symbols removed from codebase; no references remain
- [ ] Test coverage ≥ 85% for `archon/rag/`; all existing tests continue to pass
- [ ] `uv run mypy archon/` reports zero errors

---

## What does NOT change
- `ClaudeSession` MCP registration mechanism (`mcp_servers["rag"] = {"type": "http", "url": ...}`) — only the key name changes from `"qmd"` to `"rag"`
- `ContextProvider` protocol shape — only the parameter name changes (`qmd_enabled` → `rag_enabled`)
- Gateway's "probe HTTP, warn and continue" pattern for optional integrations
- All other config sections (`[session]`, `[history]`, `[notifications]`, etc.)
- `BackgroundAgentManager` internal logic — only the `qmd_url` parameter name changes
- Existing test structure under `tests/` — only `qmd`-related tests are updated in place

---

## Known limitations / accepted trade-offs
- **~2 GB model download at first install** — ModernBERT (~500 MB) + bge-reranker (~1.2 GB). Displayed as a clear warning before install proceeds.
- **No auto re-index** — ingestion is manual (`archon rag ingest`) or triggered via MCP tool. A future watcher can be added without changing the pipeline.
- **Windows: manual run only** — `archon rag start/stop` stubs out on Windows with a clear message. The server itself (`python -m archon.rag.server`) works on all platforms.
- **No QMD migration** — existing QMD collections are not imported. Users re-ingest from source files.
- **Reranker adds ~160 ms on CPU** — acceptable for a personal knowledge base; documented in user manual.
- **`archon rag ingest` is not incremental v1** — re-ingesting a collection replaces all chunks for documents that have changed (identified by path hash). Full incremental diffing deferred.

---

## Architecture

### New modules

```
archon/rag/
├── __init__.py          # empty marker
├── _types.py            # Shared dataclasses: ChunkRecord, SearchResult, DocumentInfo, CollectionInfo, IngestResult
├── store.py             # RagStore — LanceDB async store (dataclasses in _types.py)
├── embedder.py          # EmbedderBackend protocol + ModelEmbedder (sentence-transformers)
├── reranker.py          # RerankerBackend protocol + ModelReranker (CrossEncoder)
├── parser.py            # DocumentParser: format router → Markdown string
├── chunker.py           # DocumentChunker: Chonkie RecursiveChunker wrapper
├── pipeline.py          # RagPipeline: orchestrates store + embedder + reranker + chunker + parser
├── server.py            # FastMCP HTTP server; entry point: python -m archon.rag.server
└── install.py           # RagInstaller: deps, data dir, model download, service registration, ingest

tests/rag/
├── __init__.py
├── test_store.py
├── test_embedder.py
├── test_reranker.py
├── test_parser.py
├── test_chunker.py
├── test_pipeline.py
├── test_server.py
└── test_install.py

tests/cli/test_rag_cmd.py   # new file alongside existing CLI tests
```

Additional platform files added by this feature:
```
archon/platform/macos/rag_service.py   # RagPlatformService for macOS (launchd)
archon/platform/linux/rag_service.py   # RagPlatformService for Linux (systemd)
```

### Connection to existing components

```
gateway.py
  ├── _ensure_rag_server(host, port) → HTTP probe → bool
  └── cfg.rag.enabled → builds rag_url → SessionManager(rag_url=...) + BackgroundAgentManager(rag_url=...)

SessionManager(rag_url)
  └── _create_session() → ClaudeSession(rag_url=...)
        └── _build_mcp_servers() → mcp_servers["rag"] = {"type": "http", "url": rag_url}

SessionManager(rag_url) → startup_context_prompt(rag_enabled=rag_url is not None)
  └── HistoryCompactor.startup_context_prompt(rag_enabled=True) → includes search tool hint

archon rag install → RagInstaller
  └── writes com.archon.rag.plist → launchctl load → launchctl start
```

### New config keys (`[rag]` section)

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `false` | Connect to the RAG server on startup |
| `host` | str | `"localhost"` | RAG server host |
| `port` | int | `8282` | RAG server HTTP port |
| `db_path` | str | `"~/.archon/rag"` | LanceDB database directory |
| `history_collection` | str | `"archon-history"` | Default collection name for history ingest |
| `embedding_model` | str | `"nomic-ai/modernbert-embed-base"` | HuggingFace model ID |
| `reranker_model` | str | `"BAAI/bge-reranker-v2-m3"` | HuggingFace model ID |
| `top_k_retrieve` | int | `20` | Candidates retrieved before reranking |
| `top_k_return` | int | `5` | Results returned to the LLM after reranking |
| `chunk_size` | int | `512` | Tokens per chunk (when using a token-based tokenizer in RecursiveChunker) |

### Key interfaces

```python
# store.py
@dataclass
class ChunkRecord:
    doc_id: str; chunk_id: str; text: str
    vector: list[float]; source_path: str; indexed_at: str  # ISO-8601

@dataclass
class SearchResult:
    doc_id: str; chunk_id: str; text: str
    score: float; source_path: str

@dataclass
class DocumentInfo:
    doc_id: str; source_path: str; chunk_count: int; indexed_at: str

@dataclass
class CollectionInfo:
    name: str; doc_count: int; chunk_count: int

class RagStore:
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def ensure_collection(self, collection: str, embedding_dim: int) -> None: ...
    async def ingest_chunks(self, collection: str, chunks: list[ChunkRecord]) -> int: ...
    async def hybrid_search(self, collection: str, query_vector: list[float],
                            query_text: str, top_k: int) -> list[SearchResult]: ...
    async def delete_document(self, collection: str, doc_id: str) -> int: ...
    async def list_documents(self, collection: str, limit: int) -> list[DocumentInfo]: ...
    async def list_collections(self) -> list[CollectionInfo]: ...
    async def fetch_adjacent_chunks(self, collection: str, doc_id: str, center_idx: int, window: int) -> list[ChunkRecord]: ...

# embedder.py
class EmbedderBackend(Protocol):
    def encode(self, texts: list[str]) -> list[list[float]]: ...

class Embedder:
    embedding_dim: int
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_one(self, text: str) -> list[float]: ...

# reranker.py
class RerankerBackend(Protocol):
    def predict(self, pairs: list[tuple[str, str]]) -> list[float]: ...

class Reranker:
    async def rerank(self, query: str, candidates: list[SearchResult],
                     top_k: int) -> list[SearchResult]: ...

# parser.py
class ParseError(Exception):
    def __init__(self, path: Path, cause: Exception) -> None: ...

class DocumentParser:
    async def parse(self, path: Path) -> str: ...  # → Markdown string

# chunker.py
class DocumentChunker:
    def __init__(self, chunk_size: int = 512) -> None: ...
    def chunk(self, text: str, doc_id: str, source_path: str) -> list[ChunkRecord]: ...

# _types.py (Task 1.3 — shared dataclasses — imported by all other modules)
# ChunkRecord, SearchResult, DocumentInfo, CollectionInfo, IngestResult — all defined here

# pipeline.py
class RagPipeline:
    async def ingest_file(self, path: Path, collection: str, rebuild_fts: bool = True) -> IngestResult: ...
    async def ingest_directory(self, path: Path, collection: str,
                               glob_pattern: str = "**/*",
                               progress_cb: Callable[[int, int], Awaitable[None]] | None = None
                               ) -> list[IngestResult]: ...
    async def search(self, query: str, collection: str) -> list[SearchResult]: ...
    async def search_with_context(self, query: str, collection: str,
                                  context_window: int = 1) -> list[dict[str, Any]]: ...
    async def delete_document(self, doc_id: str, collection: str) -> int: ...
    async def list_collections(self) -> list[CollectionInfo]: ...
    async def list_documents(self, collection: str, limit: int = 100) -> list[DocumentInfo]: ...
```

---

## Tests

### Phase 1 — Foundation
- **test_rag_config_defaults** (unit): RagConfig fields match documented defaults
- **test_rag_config_custom_values** (unit): all fields parsed from TOML dict
- **test_rag_config_invalid_port** (unit): port out of range raises ConfigError
- **test_rag_config_missing_optional_uses_default** (unit): omitted keys use dataclass defaults
- **test_store_connect_creates_db_dir** (unit): `connect()` creates db_path if missing
- **test_store_ensure_collection_idempotent** (unit): calling twice does not raise
- **test_store_ingest_and_list_documents** (integration): ingest 2 chunks, list_documents returns 1 doc
- **test_store_hybrid_search_returns_results** (integration): search after ingest returns non-empty list
- **test_store_delete_document_removes_all_chunks** (integration): delete clears all chunks for a doc_id
- **test_store_list_collections_includes_created** (integration): created collection appears in list
- **test_store_delete_document_injection_safe** (unit): doc_id with SQL-special chars → no crash or unexpected deletion
- **test_store_delete_document_invalid_doc_id_raises** (unit): doc_id not matching ^[a-f0-9]{64}$ raises ValueError
- **test_store_fetch_adjacent_chunks_returns_neighbors** (integration): 3-chunk doc, center chunk → 2 adjacent chunks returned
- **test_store_fetch_adjacent_chunks_at_boundary** (integration): `center_idx=0`, `window=2` → only right neighbors returned, no negative-index queries
- **test_store_hybrid_search_degrades_gracefully_without_fts_index** (integration): no FTS index built → returns vector-only results instead of raising
- **test_store_list_documents_respects_limit** (integration): 3 docs ingested → list_documents(limit=1) returns exactly 1
- **test_chunk_record_fields** (unit): ChunkRecord instantiation and attribute access
- **test_chunk_record_chunk_id_format** (unit): chunk_id follows `{doc_id}-{idx:06d}` pattern

### Phase 2 — ML Backends
- **test_embedder_mock_backend_returns_vectors** (unit): MockEmbedder.embed returns correct shape
- **test_embedder_embed_one_wraps_embed** (unit): embed_one returns single vector
- **test_embedder_embedding_dim_property** (unit): embedding_dim matches backend output dimension
- **test_reranker_mock_backend_returns_reordered** (unit): higher-score items rank first
- **test_reranker_truncates_to_top_k** (unit): returns at most top_k results
- **test_reranker_empty_candidates_returns_empty** (unit): no crash on empty input

### Phase 3 — Document Processing
- **test_parser_markdown_direct_read** (unit): `.md` file returned as-is
- **test_parser_txt_direct_read** (unit): `.txt` file content returned
- **test_parser_html_uses_trafilatura** (unit): mocked trafilatura called for `.html`
- **test_parser_pdf_uses_docling** (unit): mocked Docling called for `.pdf`
- **test_parser_docx_uses_markitdown** (unit): mocked MarkItDown called for `.docx`
- **test_parser_unknown_extension_falls_back_to_plain** (unit): `.xyz` read as text
- **test_parser_unreadable_file_raises_parse_error** (unit): PermissionError → ParseError
- **test_chunker_produces_chunk_records** (unit): chunk() returns list of ChunkRecord
- **test_chunker_returns_empty_placeholder_chunk_id** (unit): chunk_id field is empty string in chunker output (pipeline assigns sequential IDs)
- **test_chunker_respects_chunk_size** (unit): no chunk exceeds chunk_size * 1.2 tokens
- **test_chunker_all_records_carry_doc_id** (unit): every record has the provided doc_id
- **test_chunker_empty_text_returns_empty** (unit): no crash on empty string

### Phase 4 — Pipeline
- **test_pipeline_ingest_file_happy_path** (integration): markdown file → IngestResult(status="ok", chunks_created>0)
- **test_pipeline_ingest_file_parse_error_returns_error_result** (integration): bad file → IngestResult(status="error")
- **test_pipeline_ingest_directory_counts_files** (integration): 3-file dir → 3 IngestResults
- **test_pipeline_ingest_directory_calls_progress_cb** (integration): progress_cb called for each file
- **test_pipeline_search_returns_ranked_results** (integration): ingest + search → non-empty list
- **test_pipeline_search_with_context_includes_neighbors** (integration): context_window=1 → surrounding chunks attached (context_before/context_after keys)
- **test_pipeline_delete_document_removes_from_store** (integration): delete → search returns empty
- **test_pipeline_list_collections_reflects_ingest** (integration): after ingest, collection appears
- **test_pipeline_list_documents_returns_doc_info** (integration): after ingest, document listed with correct path
- **test_pipeline_ingest_file_idempotent** (integration): ingest same file twice → appears once in list_documents
- **test_pipeline_ingest_directory_empty_dir** (integration): empty directory → returns `[]`, no crash
- **test_pipeline_ingest_directory_partial_failure** (integration): 3 files, 1 unreadable → 2 ok + 1 error results, others unaffected
- **test_create_pipeline_wires_all_components** (unit): factory returns pipeline with all components non-None
- **test_create_pipeline_does_not_auto_connect** (unit): store method raises before `connect()` called
- **test_pipeline_ingest_file_fts_searchable** (integration): single ingest_file → keyword search finds the document

### Phase 5 — MCP Server
- **test_server_search_tool_returns_dict_list** (unit): mocked pipeline.search → tool returns list of dicts
- **test_server_search_tool_missing_collection_uses_default** (unit): omitted collection → uses history_collection from config
- **test_server_search_with_context_tool** (unit): delegates to pipeline.search_with_context
- **test_server_ingest_file_tool_returns_result** (unit): delegates to pipeline.ingest_file
- **test_server_ingest_directory_tool** (unit): delegates to pipeline.ingest_directory
- **test_server_list_collections_tool** (unit): returns serialised CollectionInfo list
- **test_server_list_documents_tool** (unit): returns serialised DocumentInfo list
- **test_server_delete_document_tool** (unit): delegates to pipeline.delete_document
- **test_server_tool_exception_returns_error_dict** (unit): pipeline raises → tool returns {"error": ...}, never re-raises
- **test_server_creates_fastmcp_app** (unit): create_app(config) returns FastMCP instance with 7 tools
- **test_store_ingest_chunks_rejects_malformed_chunk_id** (unit): chunk_id = "" or UUID → raises `ValueError`, no DB write
- **test_server_main_wires_all_components** (unit): main() called with mocked deps → store.connect awaited, app.run called with correct host/port

### Phase 6 — Archon Integration
- **test_rag_config_replaces_qmd_config** (unit): Config.rag exists; Config.qmd does not
- **test_ensure_rag_server_returns_true_when_reachable** (unit): mocked HTTP → True
- **test_ensure_rag_server_returns_false_when_unreachable** (unit): connection refused → False
- **test_ensure_rag_server_remote_host_skips_probe** (unit): non-localhost host → probe skipped, True returned
- **test_claude_session_registers_rag_mcp_server** (unit): rag_url set → mcp_servers["rag"] built
- **test_claude_session_skips_rag_when_none** (unit): rag_url=None → "rag" key absent from mcp_servers
- **test_session_manager_passes_rag_url** (unit): SessionManager(rag_url=...) → ClaudeSession receives it
- **test_startup_prompt_rag_enabled_mentions_search_tool** (unit): rag_enabled=True → text contains "search"
- **test_startup_prompt_rag_disabled_omits_search_tool** (unit): rag_enabled=False → no search tool mention
- **test_background_agent_manager_passes_rag_url** (unit): BAM(rag_url=...) → ClaudeSession receives it
- **test_gateway_rag_lifecycle_integration** (integration): gateway starts → RAG unreachable → logs warning → continues; next session has no rag_url
- **test_no_qmd_symbols_in_codebase** (unit): `grep -ri qmd archon/ tests/` returns zero matches after migration

### Phase 7 — Installer & CLI
- **test_installer_check_deps_returns_missing** (unit): missing package → listed in return value
- **test_installer_check_deps_all_present_returns_empty** (unit): all installed → empty list
- **test_installer_create_data_dir** (unit): creates directory at db_path
- **test_write_service_file_delegates_to_platform** (unit): `get_rag_service().register()` called
- **test_write_service_file_dry_run** (unit): dry_run=True → `register(dry_run=True)` called
- **test_rag_platform_service_macos_plist_contains_label** (unit): plist XML contains `com.archon.rag`
- **test_rag_cmd_install_calls_installer** (unit): `archon rag install` → RagInstaller.run() called
- **test_rag_cmd_start_calls_service_start** (unit): `archon rag start` → platform service start
- **test_rag_cmd_stop_calls_service_stop** (unit): `archon rag stop` → platform service stop
- **test_rag_cmd_status_prints_service_info** (unit): `archon rag status` → output contains "running" or "stopped"
- **test_rag_cmd_ingest_no_args_uses_history_dir** (unit): no path arg → history_dir used
- **test_rag_cmd_ingest_with_path_and_collection** (unit): `--collection my-docs /path` → correct args passed
- **test_installer_register_service_linux_writes_unit_file** (unit): Linux path → systemd unit written at expected path
- **test_rag_cmd_status_server_unreachable** (unit): server unreachable → status command prints "unreachable", returns non-zero
- **test_rag_start_calls_platform_service** (unit): `archon rag start` → delegates to `get_rag_service().start()`
- **test_rag_stop_calls_platform_service** (unit): `archon rag stop` → delegates to `get_rag_service().stop()`
- **test_rag_status_prints_service_state** (unit): output contains "running" or "stopped"
- **test_rag_status_server_unreachable_prints_warning** (unit): server down → "unreachable" in output, non-zero return

---

## Documentation update
- [ ] `examples/config.toml.example`, section `[rag]`: add full annotated `[rag]` block, remove `[qmd]` block, path: `examples/config.toml.example`
- [ ] User manual, section "RAG Search": new section explaining install, configuration, collections, and the 7 MCP tools, path: `Documentation/UserManual/user_manual.md`
- [ ] CLAUDE.md, `[rag]` config fields table: update to reflect new section name and fields, path: `CLAUDE.md`
- [ ] Archive research doc (done): `Documentation/Completed/26_rag_integration_research.md`
- [ ] Remove original backlog research file: `Documentation/Backlog/RAG integration for multi-format document search.md`

---

## Task breakdown

### Phase 1 — Foundation
> **Releasable**: after Task 1.4 — `RagStore` is independently testable; LanceDB ingest and hybrid search work end-to-end with in-process data.

#### Task 1.1 — Optional `[rag]` extras in `pyproject.toml`
- [x] **File**: `pyproject.toml`
- **Depends on**: nothing
- **Description**:
  - Add `[project.optional-dependencies]` entry `rag` with pinned lower bounds:
    `lancedb>=0.30.0`, `sentence-transformers>=3.0.0`, `docling>=2.80.0`,
    `markitdown>=0.1.5`, `trafilatura>=1.8.0`, `chonkie[all]>=0.5.0`, `fastmcp>=3.1.0`
  - Do not add any of these to the base `dependencies` list — they remain optional
  - No code changes; no tests required for this task
- **Releasable**: after this task, `uv pip install -e ".[rag]"` installs the full RAG stack
- **Tests (TDD)** — N/A (config-only change):
  - Checkpoint: `uv pip install -e ".[rag]" --dry-run` (manual verification)

#### Task 1.2 — `RagConfig` dataclass in `config/loader.py`
- [x] **File**: `archon/config/loader.py`
- **Depends on**: nothing
- **Description**:
  - Remove `QmdConfig` dataclass entirely; replace with:
    ```python
    @dataclass
    class RagConfig:
        enabled: bool = False
        host: str = "localhost"
        port: int = 8282
        db_path: str = "~/.archon/rag"
        history_collection: str = "archon-history"
        embedding_model: str = "nomic-ai/modernbert-embed-base"
        reranker_model: str = "BAAI/bge-reranker-v2-m3"
        top_k_retrieve: int = 20
        top_k_return: int = 5
        chunk_size: int = 512
    ```
  - Replace `Config.qmd: QmdConfig` → `Config.rag: RagConfig`
  - Replace `qmd_data = data.get("qmd", {})` block with a `rag_data` block in `load_config()`
  - Validation: port must be 1–65535; `top_k_retrieve` > `top_k_return` > 0; `chunk_size` > 0
  - Raise `ConfigError` with a clear message for each violation
- **Releasable**: after this task, `config.rag` is available throughout the codebase
- **Tests (TDD)** — `tests/config/test_config_loader.py` (update existing `qmd` tests):
  - Unit: `test_rag_config_defaults` — `RagConfig()` fields match documented defaults
  - Unit: `test_rag_config_all_fields_parsed` — full TOML dict parsed correctly
  - Unit: `test_rag_config_invalid_port_raises` — port 0 and 65536 both raise `ConfigError`
  - Unit: `test_rag_config_top_k_validation` — `top_k_return > top_k_retrieve` raises `ConfigError`
  - Unit: `test_config_has_no_qmd_attribute` — `Config` has no `.qmd` attribute
  - Unit: `test_rag_config_chunk_size_zero_raises` — `chunk_size=0` raises `ConfigError`
  - Unit: `test_rag_config_top_k_return_zero_raises` — `top_k_return=0` raises `ConfigError`
  - Unit: `test_rag_config_missing_optional_uses_default` — TOML dict containing only `enabled = true` (all other keys absent); verify `RagConfig` fields all use documented defaults
  - Checkpoint: `uv run pytest tests/config/ -v`

#### Task 1.3 — Shared dataclasses in `archon/rag/_types.py`
- [x] **File**: `archon/rag/_types.py`
- **Depends on**: nothing
- **Description**:
  - Define all shared dataclasses used across the RAG sub-package. No imports from other `archon/rag/` modules — this is a leaf dependency.
  - `@dataclass class ChunkRecord`: `doc_id: str`, `chunk_id: str` (format: `"{doc_id}-{idx:06d}"` when set by the pipeline; empty string `""` in chunker output before pipeline assignment), `text: str`, `vector: list[float]` (empty `[]` until pipeline fills it), `source_path: str`, `indexed_at: str` (ISO-8601 UTC)
  - `@dataclass class SearchResult`: `doc_id: str`, `chunk_id: str`, `text: str`, `score: float`, `source_path: str`
  - `@dataclass class DocumentInfo`: `doc_id: str`, `source_path: str`, `chunk_count: int`, `indexed_at: str`
  - `@dataclass class CollectionInfo`: `name: str`, `doc_count: int`, `chunk_count: int`
  - `@dataclass class IngestResult`: `doc_id: str`, `chunks_created: int`, `status: str` (`"ok"` | `"error"`), `error: str | None = None`
  - All dataclasses use `@dataclass(frozen=False)` (default) to allow `vector` field mutation by pipeline
- **Releasable**: after this task, all dataclasses are importable; all other `archon/rag/` modules depend on this
- **Tests (TDD)** — `tests/rag/test_types.py`:
  - Unit: `test_chunk_record_fields` — instantiate with all fields, verify attribute access
  - Unit: `test_chunk_record_chunk_id_format` — chunk_id follows `{doc_id}-{idx:06d}` pattern
  - Unit: `test_ingest_result_defaults` — `error` defaults to `None`
  - Checkpoint: `uv run pytest tests/rag/test_types.py -v`

#### Task 1.4 — `RagStore` in `archon/rag/store.py`
- [x] **File**: `archon/rag/store.py`
- [x] **File**: `archon/rag/__init__.py` (empty)
- **Depends on**: Task 1.3 (`_types.py` — `store.py` imports `ChunkRecord`, `SearchResult`, `DocumentInfo`, `CollectionInfo` from it)
- **Description**:
  - **Note**: All shared dataclasses (`ChunkRecord`, `SearchResult`, `DocumentInfo`, `CollectionInfo`) are defined in `archon/rag/_types.py` (Task 1.3), not in `store.py`. `store.py` imports from `_types.py`.
  - `class RagStore`:
    - `__init__(self, db_path: str | Path) -> None` — stores path, sets `self._db = None`
    - `async connect(self) -> None` — calls `lancedb.connect_async(str(db_path))`, creates dir if needed
    - `async disconnect(self) -> None` — no-op (LanceDB has no explicit close; clears reference)
    - `async ensure_collection(self, collection: str, embedding_dim: int) -> None` — creates table if absent using PyArrow schema: `doc_id` utf8, `chunk_id` utf8, `text` utf8, `vector` fixed_size_list(float32, embedding_dim), `source_path` utf8, `indexed_at` utf8. FTS index is NOT created here — it is built by `rebuild_fts_index()` after batch ingestion completes.
    - `async ingest_chunks(self, collection: str, chunks: list[ChunkRecord]) -> int` — validates every `ChunkRecord.chunk_id` matches `^[a-f0-9]{64}-\d{6}$` before writing; raises `ValueError` on any malformed ID (catches pipeline bugs early, before corrupt data reaches the DB). Converts to Arrow table, calls `table.add()`. Does NOT rebuild FTS index here (rebuilding per-file is O(n²)). Returns `len(chunks)`.
    - `async rebuild_fts_index(self, collection: str) -> None` — calls `await table.create_index("text", config=lancedb.index.FTS())` (LanceDB AsyncTable API; `create_fts_index` does not exist on AsyncTable). Called once after batch ingestion completes.
    - `async fetch_adjacent_chunks(self, collection: str, doc_id: str, center_idx: int, window: int) -> list[ChunkRecord]` — fetches chunks with chunk_ids in the window around `center_idx`, clamping to non-negative indices: `target_ids = [f"{doc_id}-{i:06d}" for i in range(max(0, center_idx - window), center_idx + window + 1) if i != center_idx]`. Queries store for rows matching those chunk_ids. Returns `[]` if none found. Context before the first chunk in a document is intentionally empty (no negative indices exist).
    - `async hybrid_search(self, collection: str, query_vector: list[float], query_text: str, top_k: int) -> list[SearchResult]` — runs two searches and merges with RRF:
      1. Vector search: `await table.vector_search(query_vector).limit(top_k).to_list()` → assigns reciprocal rank scores. **Implementer note**: Verify async method name against the installed LanceDB version — async tables use `vector_search()` not `search()`. The `vector_column_name` parameter may be needed if the column is not named `"vector"` (e.g., `table.vector_search(query_vector).column("vector").limit(top_k).to_list()`).
      2. FTS search: `await table.search(query_text, query_type="fts").limit(top_k).to_list()` → assigns reciprocal rank scores. **Implementer note**: Verify the async FTS search method signature against installed LanceDB version — this pattern mirrors the sync API but may differ on `AsyncTable`.
      3. Merge: sum RRF scores per `chunk_id`, sort descending, return top `top_k` as `SearchResult` objects
      Returns `[]` if table doesn't exist. If FTS index is not yet built, the FTS sub-query raises an exception — catch it, log a warning, and return vector-only results (degraded hybrid). This prevents crashes during the window between `ingest_chunks` and `rebuild_fts_index`.
      Note: LanceDB's `query_type="hybrid"` requires a registered embedding function on the schema (not used here — we use raw PyArrow schema). The manual two-search + RRF approach avoids this constraint.
    - `async delete_document(self, collection: str, doc_id: str) -> int` — validates `doc_id` matches `^[a-f0-9]{64}$` before use; raises `ValueError` if invalid. Then calls `table.delete(f"doc_id = '{doc_id}'")`; returns count of deleted rows (query before delete to count).
    - `async list_documents(self, collection: str, limit: int = 100) -> list[DocumentInfo]` — fetches a bounded batch of rows selecting only `["doc_id", "source_path", "indexed_at"]` columns via `await table.search().select(["doc_id", "source_path", "indexed_at"]).limit(limit * 50).to_list()` (LanceDB async full-table scan with no vector argument; fetches at most `limit * 50` rows as a practical upper bound, avoiding loading the entire table), then aggregates in Python: group by `doc_id`, count rows per group, pick first `source_path` and `indexed_at` per group. Returns top `limit` `DocumentInfo` results. Returns `[]` if collection absent. **Implementer note**: Use `table.search()` with no arguments for a full-table scan (Python async API). `table.query()` is the JavaScript/TypeScript API and does not exist in Python. Verify the exact async scan API against the installed LanceDB version — alternatives include `table.to_arrow()` with column selection. **Trade-off**: For collections larger than `limit * 50` chunks, `list_documents` may not return the most recently indexed documents; this is acceptable for a personal knowledge base tool.
    - `async list_collections(self) -> list[CollectionInfo]` — calls `db.table_names()`, queries each for counts. Returns `[]` if db not connected.
  - All methods raise `RuntimeError("RagStore not connected")` if called before `connect()`
  - All LanceDB calls are natively async — no `asyncio.to_thread()` needed
- **Releasable**: after this task, data layer is fully testable end-to-end with temp dirs
- **Tests (TDD)** — `tests/rag/test_store.py`:
  - Unit: `test_store_connect_creates_db_dir` — `connect()` on non-existent path creates the directory
  - Unit: `test_store_disconnect_clears_connection` — after `connect()` then `disconnect()`, calling `ingest_chunks` raises `RuntimeError`
  - Unit: `test_store_double_disconnect_safe` — calling `disconnect()` twice does not raise
  - Unit: `test_store_methods_raise_before_connect` — calling any of: `ingest_chunks`, `hybrid_search`, `delete_document`, `list_documents`, `list_collections`, `ensure_collection`, `rebuild_fts_index`, `fetch_adjacent_chunks` before `connect()` raises `RuntimeError`. Parametrize across all 8 methods.
  - Integration: `test_store_ensure_collection_idempotent` — calling twice does not raise
  - Integration: `test_store_ingest_and_list_documents` — 2 chunks, 1 doc → `list_documents` returns 1 `DocumentInfo`
  - Integration: `test_store_hybrid_search_returns_results` — ingest then search returns non-empty list
  - Integration: `test_store_hybrid_search_unknown_collection_returns_empty` — no crash
  - Integration: `test_store_delete_document_removes_chunks` — delete → `list_documents` returns empty
  - Integration: `test_store_list_collections_includes_ingested` — collection appears after ingest
  - Integration: `test_store_delete_document_injection_safe` — doc_id with special chars → no crash, no data loss for other docs
  - Unit: `test_store_delete_document_invalid_doc_id_raises` — doc_id failing regex → raises `ValueError` before any DB call
  - Unit: `test_store_ingest_chunks_rejects_malformed_chunk_id` — chunk_id = "" or UUID → raises `ValueError` before any DB write
  - Integration: `test_store_fetch_adjacent_chunks_returns_neighbors` — ingest 3 sequential chunks, fetch adjacent for center chunk → returns 2 neighbors
  - Integration: `test_store_fetch_adjacent_chunks_at_boundary_returns_partial` — fetch neighbors at `center_idx=0`, `window=2` → only right neighbors (idx 1, 2) returned; no negative-index IDs queried, no crash
  - Integration: `test_store_hybrid_search_degrades_gracefully_without_fts_index` — ingest chunks without calling rebuild_fts_index → search returns vector-only results (non-empty), no exception raised
  - Integration: `test_store_list_documents_respects_limit` — ingest 3 different documents, call `list_documents(collection, limit=1)` → exactly 1 DocumentInfo returned
  - Integration: `test_store_hybrid_search_rrf_ranking_correct` — ingest 2 docs where one matches both vector similarity AND keyword query, another matches only vector similarity; verify the dual-match document has higher score in the merged results
  - Integration: `test_store_delete_nonexistent_doc_returns_zero` — delete unknown doc_id → returns 0, no crash
  - Integration: `test_store_rebuild_fts_index_makes_text_searchable` — ingest chunks, call `rebuild_fts_index`, then verify keyword search finds the document
  - Integration: `test_store_rebuild_fts_index_idempotent` — calling `rebuild_fts_index` twice does not raise
  - Integration: `test_store_list_collections_returns_correct_counts` — ingest 2 docs with 3 chunks each; verify `CollectionInfo.doc_count == 2` and `CollectionInfo.chunk_count == 6`
  - Checkpoint: `uv run pytest tests/rag/test_store.py -v`

---

### Phase 2 — ML Backends
> **Releasable**: after Task 2.2 — embed + rerank are independently testable with injected mock backends; no ML models downloaded in CI.

#### Task 2.0 — CI conftest for ML model isolation in `tests/rag/conftest.py`
- [x] **File**: `tests/rag/conftest.py`
- [x] **File**: `tests/rag/__init__.py`
- **Depends on**: nothing
- **Description**:
  - Create `tests/rag/conftest.py` with an `autouse` session-scoped fixture that patches `sentence_transformers.SentenceTransformer` and `sentence_transformers.CrossEncoder` at module import time for all tests not marked `@pytest.mark.live`. This prevents any model download in CI.
  - Patch at the module level: `monkeypatch` is not available at session scope — use `unittest.mock.patch` applied in `conftest.py` as a module-level autouse fixture with `scope="session"`.
  - The mock `SentenceTransformer` returns a dummy model whose `.encode()` returns zeroed numpy arrays of the correct dimension (768 for ModernBERT default).
  - The mock `CrossEncoder` returns a dummy model whose `.predict()` returns uniform float scores.
  - Create `tests/rag/__init__.py` (empty) alongside conftest.
- **Releasable**: after this task, all subsequent RAG tests run without downloading models
- **Tests (TDD)** — `tests/rag/test_conftest.py`:
  - Unit: `test_sentence_transformer_is_patched` — importing `sentence_transformers.SentenceTransformer` and instantiating with a real model name completes without network access
  - Checkpoint: `uv run pytest tests/rag/test_conftest.py -v`

#### Task 2.1 — `Embedder` with injectable backend in `archon/rag/embedder.py`
- [ ] **File**: `archon/rag/embedder.py`
- **Depends on**: nothing
- **Description**:
  - `class EmbedderBackend(Protocol)`: `def encode(self, texts: list[str]) -> list[list[float]]: ...`
  - `class ModelEmbedder` — loads `SentenceTransformer(model_name)` lazily on first call:
    - `__init__(self, model_name: str) -> None`
    - `def encode(self, texts: list[str]) -> list[list[float]]` — calls `self._model.encode(texts).tolist()`
  - `class Embedder`:
    - `__init__(self, backend: EmbedderBackend) -> None`
    - `embedding_dim: int` property — calls `backend.encode(["probe"])` once and caches result length
    - `async def embed(self, texts: list[str]) -> list[list[float]]` — wraps `backend.encode` in `asyncio.to_thread()`
    - `async def embed_one(self, text: str) -> list[float]` — calls `self.embed([text])`, returns first item
  - Factory: `def make_embedder(model_name: str) -> Embedder` — creates `ModelEmbedder` + wraps in `Embedder`
- **Releasable**: after this task, `Embedder` is usable with any backend conforming to the protocol
- **Tests (TDD)** — `tests/rag/test_embedder.py`:
  - Unit: `test_embedder_mock_backend_returns_correct_shape` — MockEmbedder(dim=4) → embed(["a","b"]) returns 2×4 list
  - Unit: `test_embedder_embed_one_returns_single_vector` — embed_one("x") returns list of floats
  - Unit: `test_embedder_embedding_dim_cached` — `embedding_dim` called twice → backend.encode called once
  - Unit: `test_embedder_uses_to_thread` — backend.encode is called in a thread (verified via threading.current_thread check in mock)
  - Checkpoint: `uv run pytest tests/rag/test_embedder.py -v`

#### Task 2.2 — `Reranker` with injectable backend in `archon/rag/reranker.py`
- [ ] **File**: `archon/rag/reranker.py`
- **Depends on**: Task 1.3 (`SearchResult` dataclass from `_types.py`)
- **Description**:
  - `class RerankerBackend(Protocol)`: `def predict(self, pairs: list[tuple[str, str]]) -> list[float]: ...`
  - `class ModelReranker` — loads `CrossEncoder(model_name)` lazily:
    - `__init__(self, model_name: str) -> None`
    - `def predict(self, pairs: list[tuple[str, str]]) -> list[float]` — returns `self._model.predict(pairs).tolist()`
  - `class Reranker`:
    - `__init__(self, backend: RerankerBackend) -> None`
    - `async def rerank(self, query: str, candidates: list[SearchResult], top_k: int) -> list[SearchResult]`
      — builds pairs `[(query, c.text) for c in candidates]`, calls `asyncio.to_thread(backend.predict, pairs)`,
      zips scores back to candidates, sorts descending, returns `candidates[:top_k]`
    - Returns empty list if `candidates` is empty (no crash)
  - Factory: `def make_reranker(model_name: str) -> Reranker`
- **Releasable**: after this task, reranking is testable without loading any ML model
- **Tests (TDD)** — `tests/rag/test_reranker.py`:
  - Unit: `test_reranker_sorts_by_score_descending` — MockReranker assigns known scores → output ordered correctly
  - Unit: `test_reranker_truncates_to_top_k` — 5 candidates, top_k=2 → 2 returned
  - Unit: `test_reranker_empty_candidates_returns_empty` — no crash
  - Unit: `test_reranker_calls_backend_with_correct_pairs` — pairs verified against query + candidate texts
  - Checkpoint: `uv run pytest tests/rag/test_reranker.py -v`

---

### Phase 3 — Document Processing
> **Releasable**: after Task 3.2 — any file can be parsed to Markdown and chunked into `ChunkRecord` list without ML models.

#### Task 3.1 — `DocumentParser` in `archon/rag/parser.py`
- [ ] **File**: `archon/rag/parser.py`
- **Depends on**: nothing (`ParseError` is defined locally in `parser.py`; `DocumentParser` returns plain strings — it does NOT import from `store.py` or `_types.py`. The pipeline layer creates `ChunkRecord` instances from parser output.)
- **Description**:
  - `class ParseError(Exception)`:
    - `__init__(self, path: Path, cause: Exception) -> None`
    - `path: Path` and `cause: Exception` attributes
  - `class DocumentParser`:
    - `async def parse(self, path: Path) -> str` — format router, all CPU work in `asyncio.to_thread()`:
      - `.md`, `.txt`, `.py`, `.js`, `.ts`, `.go`, `.rs`, `.java`, `.sh`, `.yaml`, `.yml`, `.json`, `.toml`, `.csv` → `_parse_plain`
      - `.html`, `.htm` → `_parse_html`
      - `.pdf` → `_parse_pdf`
      - `.docx`, `.pptx`, `.xlsx` → `_parse_office`
      - Any other extension → `_parse_plain` (best-effort UTF-8 read)
    - `def _parse_plain(self, path: Path) -> str` — `path.read_text(encoding="utf-8", errors="replace")`
    - `def _parse_html(self, path: Path) -> str` — `trafilatura.extract(path.read_text(), include_tables=True, include_links=False)` or fallback to plain read if extract returns None
    - `def _parse_pdf(self, path: Path) -> str` — `DocumentConverter().convert(str(path)).document.export_to_markdown()`
    - `def _parse_office(self, path: Path) -> str` — `MarkItDown().convert(str(path)).text_content`
    - All `_parse_*` methods wrap in try/except; on failure raise `ParseError(path, exc)`
    - `parse()` catches `ParseError` and re-raises; catches other exceptions and wraps in `ParseError`
- **Releasable**: after this task, any file path can be converted to Markdown string
- **Tests (TDD)** — `tests/rag/test_parser.py`:
  - Unit: `test_parser_md_returns_content` — writes temp `.md` file, parse returns its text
  - Unit: `test_parser_txt_returns_content` — same for `.txt`
  - Unit: `test_parser_unknown_extension_falls_back_to_plain` — `.xyz` file read as text
  - Unit: `test_parser_html_calls_trafilatura` (monkeypatched trafilatura) — verifies routing
  - Unit: `test_parser_pdf_calls_docling` (monkeypatched DocumentConverter) — verifies routing
  - Unit: `test_parser_office_calls_markitdown` (monkeypatched MarkItDown) — verifies routing
  - Unit: `test_parser_unreadable_raises_parse_error` — PermissionError → `ParseError`
  - Unit: `test_parser_html_trafilatura_returns_none_falls_back` — None → plain read used
  - Checkpoint: `uv run pytest tests/rag/test_parser.py -v`

#### Task 3.2 — `DocumentChunker` in `archon/rag/chunker.py`
- [ ] **File**: `archon/rag/chunker.py`
- **Depends on**: Task 1.3 (`ChunkRecord` from `_types.py`)
- **Description**:
  - `class DocumentChunker`:
    - `__init__(self, chunk_size: int = 512) -> None`
      — creates the chunker using Chonkie's `RecursiveChunker`. **Implementer note**: The correct Chonkie API is: `RecursiveChunker(tokenizer_or_token_counter="gpt2", chunk_size=chunk_size)` for token-based size-controlled chunking with default rules, or `RecursiveChunker.from_recipe('markdown', lang='en')` for Markdown-optimized rules with default size. These cannot be combined in one call — choose `RecursiveChunker(tokenizer_or_token_counter="gpt2", chunk_size=chunk_size)` to respect the configured `chunk_size` in tokens; if markdown-specific splitting rules are also desired, use `RecursiveRules` from Chonkie and pass to the constructor. `from_recipe()` does not accept a `chunk_size` parameter, and `RecursiveChunker()` does not accept a `recipe` parameter. The `tokenizer_or_token_counter` parameter controls what the `chunk_size` integer counts — `"character"` (default, counts characters) or a tokenizer name like `"gpt2"` (counts tokens). Use a token-based tokenizer to match the "Tokens per chunk" config description. Verify the exact API against the installed Chonkie version (`pip show chonkie`).
    - `def chunk(self, text: str, doc_id: str, source_path: str) -> list[ChunkRecord]`
      — calls `self._chunker.chunk(text)`, maps each result to `ChunkRecord`:
      - `doc_id` from argument
      - `chunk_id` = `""` (empty placeholder — the pipeline assigns the final `{doc_id}-{idx:06d}` format after chunking)
      - `text` = chunk text
      - `vector` = `[]` (filled later by pipeline)
      - `source_path` from argument
      - `indexed_at` = `datetime.now(timezone.utc).isoformat()`
    - Returns `[]` on empty text (Chonkie may return empty list)
- **Releasable**: after this task, Markdown text → ChunkRecord list without any ML
- **Tests (TDD)** — `tests/rag/test_chunker.py`:
  - Unit: `test_chunker_returns_chunk_records` — short markdown → list of `ChunkRecord`
  - Unit: `test_chunker_returns_empty_placeholder_chunk_id` — chunk_id field is empty string in chunker output (pipeline assigns sequential IDs)
  - Unit: `test_chunker_all_records_have_doc_id` — every record carries the provided `doc_id`
  - Unit: `test_chunker_vector_field_is_empty` — `vector == []` before pipeline fills it
  - Unit: `test_chunker_empty_text_returns_empty_list` — no crash
  - Unit: `test_chunker_long_text_produces_multiple_chunks` — 5000-char text → multiple chunks
  - Unit: `test_chunker_respects_chunk_size` — long text (5000+ chars) chunked with `chunk_size=512`; verify no chunk exceeds `chunk_size * 1.2` tokens
  - Checkpoint: `uv run pytest tests/rag/test_chunker.py -v`

---

### Phase 4 — Pipeline
> **Releasable**: after Task 4.1 — `RagPipeline` integrates all components; a file can be ingested and searched end-to-end using mock backends.

#### Task 4.1 — `RagPipeline` in `archon/rag/pipeline.py`
- [ ] **File**: `archon/rag/pipeline.py`
- **Depends on**: Tasks 1.3, 1.4, 2.1, 2.2, 3.1, 3.2 (Task 1.3 = `_types.py`, Task 1.4 = `RagStore`)
- **Description**:
  - `IngestResult` is imported from `archon/rag/_types.py` (Task 1.3) — not defined in `pipeline.py`
  - `class RagPipeline`:
    - `__init__(self, store: RagStore, embedder: Embedder, reranker: Reranker, chunker: DocumentChunker, parser: DocumentParser, history_collection: str, top_k_retrieve: int, top_k_return: int) -> None`
    - `async def ingest_file(self, path: Path, collection: str, rebuild_fts: bool = True) -> IngestResult`:
      1. `doc_id = hashlib.sha256(str(path.resolve()).encode()).hexdigest()` — full 64-char hex digest (SHA256 produces 64 hex characters, not 32); the `delete_document` validation regex `^[a-f0-9]{64}$` matches this exactly
      2. Delete existing chunks for this `doc_id` (idempotent re-ingest)
      3. Parse → Markdown; on `ParseError` return `IngestResult(doc_id, 0, "error", str(e))`
      4. Chunk → `list[ChunkRecord]` via `chunker.chunk(markdown, doc_id, str(path))`; records have `chunk_id=""` at this point
      4b. Assign sequential chunk_ids: `for idx, record in enumerate(records): record.chunk_id = f"{doc_id}-{idx:06d}"` — this must happen before embedding and before store ingestion so the `ingest_chunks` validator passes
      5. Embed texts in batch → fill each `record.vector`
      6. `store.ensure_collection(collection, embedder.embedding_dim)`
      7. `store.ingest_chunks(collection, records)`
      8. If `rebuild_fts=True`: call `store.rebuild_fts_index(collection)`. Default is `True` for single-file ingest; `ingest_directory` passes `False` and triggers one rebuild at the end.
      9. Return `IngestResult(doc_id, len(records), "ok")`
    - `async def ingest_directory(self, path: Path, collection: str, glob_pattern: str = "**/*", progress_cb: Callable[[int, int], Awaitable[None]] | None = None) -> list[IngestResult]`:
      — collects all files matching glob (skip dirs), returns `[]` immediately if no files found. Before processing, filter files: skip (a) hidden files/directories (names starting with `.`), (b) common binary extensions (`.pyc`, `.pyo`, `.so`, `.dll`, `.exe`, `.bin`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.zip`, `.tar`, `.gz`, `.bz2`, `.7z`, `.db`, `.sqlite`). Other extensions fall back to `_parse_plain` as before. Calls `ingest_file(path, collection, rebuild_fts=False)` for each file (skipping per-file FTS rebuild to avoid O(N²) rebuilds), calls `progress_cb(done, total)` after each file. After all files are ingested, calls `store.rebuild_fts_index(collection)` once — but only if at least one file was successfully ingested (i.e., at least one `IngestResult` has `status="ok"`). Skip the `rebuild_fts_index` call if no files matched the glob (early return already handles this) or if all ingest results have `status="error"` (collection was never created).
    - `async def search(self, query: str, collection: str) -> list[SearchResult]`:
      1. Embed query → vector
      2. `store.hybrid_search(collection, vector, query, top_k=top_k_retrieve)`
      3. `reranker.rerank(query, results, top_k=top_k_return)`
      4. Return reranked list
    - `async def search_with_context(self, query: str, collection: str, context_window: int = 1) -> list[dict[str, Any]]`:
      — calls `search()`. For each `SearchResult`, parses `center_idx` from chunk_id (`int(result.chunk_id.split("-")[-1])`), then calls `store.fetch_adjacent_chunks(collection, result.doc_id, center_idx, context_window)` to get neighboring chunks. Returns list of dicts `{"result": SearchResult, "context_before": [ChunkRecord, ...], "context_after": [ChunkRecord, ...]}`. Keys `context_before`/`context_after` replace `before`/`after` for clarity. Wrap the `int(...)` parse in try/except `ValueError`: if `chunk_id` is malformed or missing the index suffix, log a warning and skip adjacent chunk fetching for that result — return the result with empty `context_before=[]` and `context_after=[]` lists, no exception raised.
    - `async def delete_document(self, doc_id: str, collection: str) -> int` — delegates to `store.delete_document`
    - `async def list_collections(self) -> list[CollectionInfo]` — delegates to `store.list_collections`
    - `async def list_documents(self, collection: str, limit: int = 100) -> list[DocumentInfo]` — delegates to `store.list_documents`
    - `store: RagStore` — public attribute (store reference) exposed for lifecycle management by `server.py main()`
    - Factory: `def create_pipeline(cfg: RagConfig, embedder_backend: EmbedderBackend | None = None, reranker_backend: RerankerBackend | None = None) -> RagPipeline` — builds the full component graph from config. `embedder_backend` and `reranker_backend` default to `ModelEmbedder` and `ModelReranker` when not supplied (injectable for tests). Exported from `archon/rag/pipeline.py`.
- **Releasable**: after this task, the full RAG pipeline is callable with injected components
- **Tests (TDD)** — `tests/rag/test_pipeline.py`:
  - Integration: `test_pipeline_ingest_file_ok` — temp markdown file, mock embedder/reranker, real store (temp dir) → `IngestResult(status="ok", chunks_created>0)`
  - Integration: `test_pipeline_ingest_file_parse_error` — unreadable file → `IngestResult(status="error")`
  - Integration: `test_pipeline_ingest_is_idempotent` — ingest same file twice → doc appears once in `list_documents`
  - Unit: `test_pipeline_ingest_file_chunk_ids_sequential` — mock `store.ingest_chunks` to capture the records argument; call `ingest_file`; assert the captured `chunk_id` values are `{doc_id}-000000`, `{doc_id}-000001`, ... (verifies reassignment happens inside the pipeline BEFORE the store receives them, not just after DB round-trip)
  - Integration: `test_pipeline_ingest_directory` — 3 temp files → 3 IngestResults
  - Integration: `test_pipeline_ingest_directory_calls_progress_cb` — progress_cb called for each file
  - Integration: `test_pipeline_search_returns_ranked_results` — ingest then search → non-empty list
  - Integration: `test_pipeline_search_with_context_returns_neighbors` — `context_window=1` → dicts with "context_before"/"context_after" keys
  - Integration: `test_pipeline_delete_document` — ingest then delete → `list_documents` empty
  - Integration: `test_pipeline_list_collections_after_ingest` — collection appears in list
  - Integration: `test_pipeline_ingest_file_fts_searchable` — ingest single file via `ingest_file`, then call `search()` with a keyword unique to that file → file is found (verifies FTS rebuilt after single ingest)
  - Integration: `test_pipeline_ingest_directory_empty_dir` — empty temp directory → returns `[]`, no crash, no FTS rebuild attempted
  - Integration: `test_pipeline_ingest_directory_partial_failure` — directory with 3 files where 1 is unreadable; assert 2 `IngestResult(status="ok")` and 1 `IngestResult(status="error")` returned; other files not affected
  - Unit: `test_create_pipeline_wires_all_components` — call `create_pipeline(RagConfig())` with mocked heavy deps; assert returned `RagPipeline` has non-None `.store`, `.embedder`, `.reranker`, `.chunker`, `.parser` attributes
  - Unit: `test_create_pipeline_does_not_auto_connect` — call `create_pipeline(RagConfig())`; immediately call a store method (e.g., `list_collections()`); assert `RuntimeError("RagStore not connected")` raised (confirms factory does not call `store.connect()`)
  - Unit: `test_pipeline_search_with_context_malformed_chunk_id` — mock `store.hybrid_search` to return a `SearchResult` with a non-parseable chunk_id (e.g., `"no-index-here"`); verify the result is still returned in the output list with empty `context_before` and `context_after` lists, and no exception is raised
  - Integration: `test_pipeline_ingest_directory_rebuilds_fts_once` — mock `store.rebuild_fts_index`; call `ingest_directory` with 3 files; assert `rebuild_fts_index` called exactly once (not 3 times)
  - Integration: `test_pipeline_ingest_directory_skips_subdirectories` — create temp dir with 2 files and a subdirectory; verify only files appear in results (no crash from attempting to parse directory paths as documents)
  - Integration: `test_pipeline_ingest_directory_skips_hidden_files` — temp dir with `.hidden_file.md` and `visible.md`; verify only `visible.md` is ingested (1 IngestResult returned, not 2)
  - Integration: `test_pipeline_ingest_directory_all_failures_skips_fts_rebuild` — directory with 2 unreadable files; mock `store.rebuild_fts_index`; verify `rebuild_fts_index` NOT called and results contain 2 error IngestResults
  - Integration: `test_pipeline_ingest_directory_skips_binary_extensions` — temp dir containing `data.txt` and `image.png`; verify only `data.txt` produces an `IngestResult`, `image.png` is silently skipped (not present in results at all)
  - Checkpoint: `uv run pytest tests/rag/test_pipeline.py -v`

---

### Phase 5 — MCP Server
> **Releasable**: after Task 5.1 — `python -m archon.rag.server` starts a live FastMCP HTTP server; all 7 tools are callable via MCP protocol.

#### Task 5.1 — FastMCP HTTP server in `archon/rag/server.py`
- [ ] **File**: `archon/rag/server.py`
- **Depends on**: Tasks 1.2, 4.1
- **Description**:
  - `def create_app(pipeline: RagPipeline, default_collection: str) -> FastMCP`:
    - Creates `app = FastMCP("archon-rag")`
    - Registers 7 async tools via `@app.tool()` decorator:
      1. `search(query: str, collection: str | None = None) -> list[dict]`
         — calls `pipeline.search(query, collection or default_collection)`, serialises to dicts. `top_k` is a tuning parameter that comes from config, not from the LLM — do not expose it in the tool signature.
      2. `search_with_context(query: str, collection: str | None = None, context_window: int = 1) -> list[dict]`
         — calls `pipeline.search_with_context(...)`
      3. `ingest_file(path: str, collection: str | None = None) -> dict`
         — calls `pipeline.ingest_file(Path(path), collection or default_collection)`, returns `asdict(result)`
      4. `ingest_directory(path: str, glob_pattern: str = "**/*", collection: str | None = None) -> list[dict]`
         — calls `pipeline.ingest_directory(...)` with a `progress_cb` wired to `ctx.report_progress()`
      5. `list_collections() -> list[dict]` — calls `pipeline.list_collections()`, serialises
      6. `list_documents(collection: str | None = None, limit: int = 100) -> list[dict]`
      7. `delete_document(doc_id: str, collection: str | None = None) -> dict`
    - All tools catch exceptions and return `{"error": str(e)}` — never re-raise
    - All tools log to `logging.getLogger("archon.rag")` (stderr in production)
    - Returns `app`
  - `async def main() -> None`:
    - Loads config via `load_config()` (finds `config.toml` via standard path logic)
    - Calls `pipeline = create_pipeline(cfg.rag)` (factory from `archon/rag/pipeline.py`) — assembles all components from config. The factory also creates and holds the `RagStore` internally.
    - Calls `await pipeline.store.connect()` (or however the pipeline exposes the store for lifecycle management)
    - Creates app: `app = create_app(pipeline, cfg.rag.history_collection)`
    - Calls `await app.run_async(transport="http", host=cfg.rag.host, port=cfg.rag.port)`. Use `run_async()` (not `run()`) since `main()` is an `async def` — `run()` calls `asyncio.run()` internally and would raise `RuntimeError` in an async context. Note: verify the exact transport parameter name against the installed FastMCP version (`pip show fastmcp`). FastMCP 3.x uses `"http"` not `"streamable-http"`.
    - On `KeyboardInterrupt` / SIGTERM: calls `await pipeline.store.disconnect()`
    - **Signal handling**: register SIGTERM via `loop.add_signal_handler(signal.SIGTERM, shutdown_callback)` where `shutdown_callback` sets an asyncio `Event`. Use `await app.run_async(...)` inside a `try/finally` that always calls `await pipeline.store.disconnect()` regardless of how exit is triggered. **Note**: verify FastMCP 3.x provides async shutdown hooks or test that SIGTERM is handled correctly before relying on the finally block.
  - `if __name__ == "__main__": import asyncio; asyncio.run(main())`
- **Releasable**: after this task, `python -m archon.rag.server` serves all 7 MCP tools
- **Tests (TDD)** — `tests/rag/test_server.py`:
  - Unit: `test_create_app_returns_fastmcp_instance` — returns FastMCP with 7 registered tools
  - Unit: `test_search_tool_delegates_to_pipeline` (mocked pipeline) — correct args forwarded
  - Unit: `test_search_tool_uses_default_collection_when_none` — None → default_collection used
  - Unit: `test_search_with_context_tool` (mocked pipeline) — correct delegation
  - Unit: `test_ingest_file_tool_returns_result_dict` — IngestResult → dict returned
  - Unit: `test_ingest_directory_tool` (mocked pipeline) — list of dicts returned
  - Unit: `test_list_collections_tool` (mocked pipeline) — serialised CollectionInfo list
  - Unit: `test_list_documents_tool` (mocked pipeline) — serialised DocumentInfo list
  - Unit: `test_delete_document_tool` (mocked pipeline) — count returned in dict
  - Unit: `test_tool_exception_returns_error_dict` — pipeline.search raises → `{"error": "..."}` returned, no re-raise
  - Unit: `test_server_main_wires_all_components` — mock all heavy deps (sentence_transformers, lancedb, fastmcp.run); call `asyncio.run(main())` with mocked config; verify `store.connect()` awaited and `app.run()` called with correct host/port
  - Unit: `test_ingest_directory_tool_wires_progress_cb` — mock `pipeline.ingest_directory`; invoke the `ingest_directory` tool; verify `pipeline.ingest_directory` was called with a non-None `progress_cb` argument
  - Integration: `test_server_search_tool_with_real_pipeline` — create `RagPipeline` with mock ML backends and real store (tmp_path), ingest one file, call the `search` tool through FastMCP's `TestClient` (or equivalent test transport) to invoke the tool through the MCP protocol layer — not calling the underlying Python function directly. Verifies serialization and the full request-response cycle. → non-empty result list returned
  - Integration: `test_server_error_serialization_through_mcp_transport` — use FastMCP TestClient; trigger a pipeline exception (e.g., call `search` before `store.connect()`); verify the MCP response contains an `{"error": "..."}` dict and does not raise a transport-level exception (error is contained, not propagated)
  - Checkpoint: `uv run pytest tests/rag/test_server.py -v`

---

### Phase 6 — Archon Integration
> **Releasable**: after Task 6.5 — Archon gateway connects to the RAG server when `[rag] enabled = true`; Claude Code can call `search` in sessions; all previous QMD references removed.

#### Task 6.1 — Rename `qmd_url → rag_url` in `ai/claude_session.py`
- [ ] **File**: `archon/ai/claude_session.py`
- **Depends on**: Task 1.2
- **Description**:
  - Rename parameter `qmd_url: str | None = None` → `rag_url: str | None = None`
  - Rename `self._qmd_url` → `self._rag_url`
  - Change MCP server key: `mcp_servers["qmd"]` → `mcp_servers["rag"]`
  - Condition: `if self._rag_url is not None: mcp_servers["rag"] = {"type": "http", "url": self._rag_url}`
- **Releasable**: after this task, sessions register the RAG server as MCP tool provider
- **Tests (TDD)** — `tests/ai/test_claude_session.py` (update existing tests):
  - Unit: `test_rag_url_registers_mcp_server` — `rag_url` set → `mcp_servers["rag"]` built
  - Unit: `test_rag_url_none_omits_mcp_server` — `rag_url=None` → no "rag" key
  - Unit: `test_no_qmd_key_in_mcp_servers` — "qmd" key must not appear in `mcp_servers`
  - Checkpoint: `uv run pytest tests/ai/test_claude_session.py -v`

#### Task 6.2 — Rename `qmd_url → rag_url` in `ai/background_agent_manager.py`
- [ ] **File**: `archon/ai/background_agent_manager.py`
- **Depends on**: Task 6.1
- **Description**:
  - Rename `qmd_url: str | None = None` → `rag_url: str | None = None` in `__init__` and store as `self._rag_url`
  - Update pass-through to `ClaudeSession(rag_url=self._rag_url, ...)`
- **Releasable**: after this task, background agents also receive the RAG MCP URL
- **Tests (TDD)** — `tests/ai/test_background_agent_manager.py` (update existing):
  - Unit: `test_bam_passes_rag_url_to_session` — rag_url set → ClaudeSession receives it
  - Unit: `test_bam_no_qmd_url_attribute` — BAM has no `_qmd_url` attribute
  - Checkpoint: `uv run pytest tests/ai/test_background_agent_manager.py -v`

#### Task 6.3 — Update `ContextProvider` protocol + `HistoryCompactor.startup_context_prompt`
- [ ] **File**: `archon/ai/context_provider.py`
- [ ] **File**: `archon/ai/history_compactor.py`
- **Depends on**: nothing (isolated rename + wording update)
- **Description**:
  - In `context_provider.py`: rename `startup_context_prompt(self, qmd_enabled: bool = False)` → `startup_context_prompt(self, rag_enabled: bool = False)`
  - In `history_compactor.py`:
    - Rename parameter `qmd_enabled` → `rag_enabled`
    - Replace QMD-specific section text:
      ```python
      # OLD:
      qmd_section = (
          "\n\nThe QMD tools (qmd_deep_search / qmd_vector_search) provide fast "
          "semantic search over the full history — use them when looking for a "
          "specific topic instead of reading individual files."
          if qmd_enabled else ""
      )
      # NEW:
      rag_section = (
          "\n\nA local RAG search tool is available as the `search` MCP tool. "
          "Use it to find specific topics, conversations, or documents by meaning "
          "instead of reading individual files. Call `search` with a natural-language "
          "query; it returns the most relevant chunks with source paths."
          if rag_enabled else ""
      )
      ```
    - Replace `qmd_section` reference in return statement with `rag_section`
- **Releasable**: after this task, session startup prompts correctly describe the RAG tool
- **Tests (TDD)** — `tests/ai/test_history_compactor.py` (update existing):
  - Unit: `test_startup_prompt_rag_enabled_mentions_search_tool` — `rag_enabled=True` → text contains "search" and "MCP tool"
  - Unit: `test_startup_prompt_rag_enabled_no_qmd_text` — `rag_enabled=True` → text does not contain "QMD"
  - Unit: `test_startup_prompt_rag_disabled_omits_rag_section` — `rag_enabled=False` → no "search" tool mention
  - Checkpoint: `uv run pytest tests/ai/test_history_compactor.py -v`

#### Task 6.4 — Rename `qmd_url → rag_url` in `ai/session_manager.py`
- [ ] **File**: `archon/ai/session_manager.py`
- **Depends on**: Tasks 6.1, 6.3
- **Description**:
  - Rename `qmd_url: str | None = None` → `rag_url: str | None = None` in `__init__` and `self._rag_url`
  - Update `_create_session()`: pass `rag_url=self._rag_url` to `ClaudeSession`
  - Update `startup_context_prompt` call: `self._history_compactor.startup_context_prompt(rag_enabled=self._rag_url is not None)`
- **Releasable**: after this task, `SessionManager` is fully RAG-aware
- **Tests (TDD)** — `tests/ai/test_session_manager.py` (update existing):
  - Unit: `test_session_manager_passes_rag_url_to_session` — `rag_url` set → `ClaudeSession` called with it
  - Unit: `test_session_manager_startup_prompt_rag_enabled` — `rag_url` set → `startup_context_prompt(rag_enabled=True)` called
  - Checkpoint: `uv run pytest tests/ai/test_session_manager.py -v`

#### Task 6.5 — Replace `_ensure_qmd_daemon` with `_ensure_rag_server` in `gateway/gateway.py`
- [ ] **File**: `archon/gateway/gateway.py`
- **Depends on**: Tasks 1.2, 6.4
- **Description**:
  - Remove `_ensure_qmd_daemon()` function entirely
  - Remove `_QMD_DAEMON_STARTUP_WAIT` constant (should already be removed in Task 1.2 commit; verify)
  - Add `async def _ensure_rag_server(host: str, port: int) -> bool`:
    - For `host not in ("localhost", "127.0.0.1")`: log info "RAG server host is {host} — skipping probe; assuming running", return `True`
    - For localhost: TCP socket connection check — `asyncio.open_connection(host, port)` with 2s timeout. If connection succeeds, server is running; close the connection and return `True`. Avoids HTTP endpoint uncertainty (FastMCP does not expose a guaranteed `GET /mcp` endpoint).
    - On connection error or timeout → log warning → return `False`
  - In `_run()`: replace `cfg.qmd` block with:
    ```python
    rag_url: str | None = None
    if cfg.rag.enabled:
        server_ok = await _ensure_rag_server(cfg.rag.host, cfg.rag.port)
        if server_ok:
            rag_url = f"http://{cfg.rag.host}:{cfg.rag.port}/mcp"
            logger.info("RAG MCP endpoint: %s", rag_url)
        else:
            logger.warning("RAG server unreachable — RAG integration disabled for this session")
    ```
  - Pass `rag_url` to `SessionManager(rag_url=rag_url, ...)` and `BackgroundAgentManager(rag_url=rag_url, ...)`
  - Remove `scripts/qmd_installer.sh` (deleted file in same commit)
- **Releasable**: after this task, full Archon ↔ RAG server integration is wired; `qmd` references fully removed
- **Tests (TDD)** — `tests/gateway/test_gateway.py` (update existing):
  - Unit: `test_ensure_rag_server_reachable` — mocked `asyncio.open_connection` success → True
  - Unit: `test_ensure_rag_server_unreachable` — connection error → False
  - Unit: `test_ensure_rag_server_timeout` — `asyncio.TimeoutError` → False, warning logged
  - Unit: `test_ensure_rag_server_remote_host_skips_probe` — non-localhost → True without TCP call
  - Unit: `test_gateway_run_passes_rag_url_to_session_manager` — cfg.rag.enabled=True, mocked probe → SessionManager called with rag_url
  - Unit: `test_gateway_run_rag_disabled_passes_none` — cfg.rag.enabled=False → rag_url=None
  - Unit: `test_no_qmd_references_in_gateway` — grep the module for "qmd" → zero matches (checked via import inspection)
  - Integration: `test_gateway_rag_lifecycle` — gateway._run() with rag.enabled=True, mocked TCP probe failure → rag_url=None passed to SessionManager; next call with probe success → rag_url set correctly
  - Integration: `test_gateway_rag_full_chain` — gateway config with `rag.enabled=True`, mocked TCP probe success → SessionManager called with `rag_url` → ClaudeSession built with `mcp_servers["rag"]` set → startup prompt contains "search" (verifies config → gateway → session → MCP registration → prompt chain)
  - Checkpoint: `uv run pytest tests/gateway/ -v`

#### Task 6.6 — macOS RAG service in `archon/platform/macos/rag_service.py`
- [ ] **File**: `archon/platform/macos/rag_service.py`
- **Depends on**: Task 6.5 (confirms `PlatformService` ABC shape is unchanged)
- **Description**:
  - `class LaunchdRagService(PlatformService)`:
    - `service_name` property → `"launchd-rag"`
    - `_plist_path` property → `Path.home() / "Library/LaunchAgents/com.archon.rag.plist"`
    - `register(dry_run=False) -> int` — writes plist file:
      ```xml
      <?xml version="1.0" encoding="UTF-8"?>
      <!DOCTYPE plist PUBLIC ...>
      <plist version="1.0"><dict>
        <key>Label</key><string>com.archon.rag</string>
        <key>ProgramArguments</key>
        <array><string>{sys.executable}</string><string>-m</string><string>archon.rag.server</string></array>
        <key>WorkingDirectory</key><string>{cwd}</string>
        <key>EnvironmentVariables</key>
        <dict><key>ARCHON_CONFIG</key><string>{config_file_path}</string></dict>
        <key>StandardOutPath</key><string>{log_path}</string>
        <key>StandardErrorPath</key><string>{log_path}</string>
        <key>KeepAlive</key><true/>
        <key>RunAtLoad</key><true/>
      </dict></plist>
      ```
      where `log_path = Path.home() / ".archon/rag/archon-rag.log"`. No-op if `dry_run`. Returns 0 on success, 1 on failure.
    - `unregister(dry_run=False) -> int` — removes plist file; no-op if not present
    - `start(dry_run=False) -> int` — `launchctl load <plist_path> && launchctl start com.archon.rag`; no-op if `dry_run`
    - `stop(dry_run=False) -> int` — calls `launchctl unload <plist_path>` directly (this both stops the process and unloads the job definition, preventing KeepAlive restart); no-op if `dry_run`. No need to call `launchctl stop` first — `unload` is atomic. **Note**: Using `launchctl stop com.archon.rag && launchctl unload <plist_path>` is incorrect: if `launchctl stop` fails (e.g., process already stopped), the `&&` chain prevents `unload` from running, leaving the plist loaded and KeepAlive=true causing an immediate restart. Matches the existing `LaunchdService.stop()` pattern in `archon/platform/macos/service.py`.
    - `restart(dry_run=False) -> int` — stop then start
    - `status() -> ServiceInfo` — `launchctl list com.archon.rag`; returns `ServiceInfo`
    - `is_installed() -> bool` — `_plist_path.exists()`
    - `remediation_hint() -> str` — returns "Run `archon rag install` to register the RAG service"
    - `pre_activate_cleanup(dry_run=False) -> int` — no-op for RAG service; returns 0
  - Add `get_rag_service() -> PlatformService` singleton to `archon/platform/__init__.py`:
    - Same lazy-singleton pattern as `get_service()`: `_rag_service: PlatformService | None = None`
    - Update `override(service=None, runtime=None, rag_service=None)` to also set `_rag_service` when provided
    - Update `reset()` to also clear `_rag_service = None`
    - Add `archon/platform/windows/rag_service.py` — `WindowsRagService` stub: all methods print "RAG service management not supported on Windows; run `python -m archon.rag.server` manually" and return 1. Wire in `get_rag_service()`: detect Windows via `_detect()` (the existing platform detection function) and return `WindowsRagService()`.
- **Releasable**: after this task, `get_rag_service()` is callable on macOS; tests can inject mock via `override(rag_service=mock)`
- **Tests (TDD)** — `tests/platform/macos/test_rag_service.py`:
  - Unit: `test_rag_service_plist_contains_label` — `register()` writes plist with `com.archon.rag`
  - Unit: `test_rag_service_plist_contains_python_executable` — `sys.executable` in plist
  - Unit: `test_rag_service_register_dry_run_no_file` — `dry_run=True` → no file written
  - Unit: `test_rag_service_is_installed_true_when_plist_exists` — plist file exists → True
  - Unit: `test_get_rag_service_override_in_tests` — `override(rag_service=mock); get_rag_service()` returns mock; `reset()` clears it
  - Unit: `test_reset_clears_rag_service_singleton` — after `override(rag_service=mock)`, call `reset()`, then `get_rag_service()` returns the platform-appropriate service instance (not the mock)
  - Unit: `test_get_rag_service_windows_returns_stub` — on Windows platform (mock `_detect()` to return Windows), `get_rag_service()` returns a `WindowsRagService` instance
  - Checkpoint: `uv run pytest tests/platform/macos/test_rag_service.py -v`

#### Task 6.7 — Linux RAG service in `archon/platform/linux/rag_service.py`
- [ ] **File**: `archon/platform/linux/rag_service.py`
- **Depends on**: Task 6.6 (same pattern; `get_rag_service()` already added)
- **Description**:
  - `class SystemdRagService(PlatformService)`:
    - `service_name` property → `"systemd-rag"`
    - `_unit_path` property → `Path.home() / ".config/systemd/user/archon-rag.service"`
    - `register(dry_run=False) -> int` — writes systemd user unit file with `[Unit]`, `[Service]` (ExecStart, WorkingDirectory, Environment, Restart=always), `[Install]` sections; no-op if `dry_run`
    - `unregister(dry_run=False) -> int` — removes unit file
    - `start(dry_run=False) -> int` — `systemctl --user start archon-rag`
    - `stop(dry_run=False) -> int` — `systemctl --user stop archon-rag`
    - `restart(dry_run=False) -> int` — `systemctl --user restart archon-rag`
    - `status() -> ServiceInfo` — `systemctl --user status archon-rag`
    - `is_installed() -> bool` — `_unit_path.exists()`
    - `remediation_hint() -> str` — returns "Run `archon rag install` to register the RAG service"
    - `pre_activate_cleanup(dry_run=False) -> int` — no-op; returns 0
  - The `get_rag_service()` singleton (added in Task 6.6) returns `SystemdRagService()` on Linux
- **Releasable**: after this task, `get_rag_service()` is callable on Linux
- **Tests (TDD)** — `tests/platform/linux/test_rag_service.py`:
  - Unit: `test_linux_rag_service_unit_file_contains_service_name` — `register()` writes unit with `archon-rag`
  - Unit: `test_linux_rag_service_register_dry_run_no_file` — `dry_run=True` → no file written
  - Checkpoint: `uv run pytest tests/platform/linux/test_rag_service.py -v`

#### Task 6.8 — Rename remaining `qmd_url` references in `pipeline.py`, `classifier.py`, `decomposer.py`, `config_cmd.py`, `prompts/decomposer.md`, and `install.py`
- [ ] **File**: `archon/ai/pipeline.py`
- [ ] **File**: `archon/ai/classifier.py`
- [ ] **File**: `archon/ai/decomposer.py`
- [ ] **File**: `archon/cli/config_cmd.py`
- [ ] **File**: `archon/ai/prompts/decomposer.md`
- [ ] **File**: `install.py`
- **Depends on**: Tasks 6.1–6.7 (all prior qmd→rag renames complete)
- **Description**:
  - `archon/ai/pipeline.py`: rename `qmd_url` parameter in constructor → `rag_url`; rename `self._qmd_url` → `self._rag_url`; update all pass-through calls to `Classifier` and `Decomposer` to use `rag_url=`
  - `archon/ai/classifier.py`: rename `qmd_url` in `__init__` → `rag_url`; rename `self._qmd_url` → `self._rag_url`
  - `archon/ai/decomposer.py`: rename `qmd_url` in constructor → `rag_url`; rename `qmd_enabled` → `rag_enabled` in the `startup_context_prompt` call (and any internal flag). **Critical**: change `startup_context_prompt(qmd_enabled=False)` → `startup_context_prompt(rag_enabled=self._rag_url is not None)` — the hardcoded `False` means RAG tool hints never appear in decomposer sub-sessions even when RAG is enabled. Using `self._rag_url is not None` ensures decomposer sub-sessions receive the RAG search tool hint when RAG is enabled.
  - `archon/cli/config_cmd.py`: update the known-sections list — replace `"qmd"` entry with `"rag"`. (Note: this change can only be made after Task 1.2 completes; Task 6.8 already depends transitively on Task 1.2 via Tasks 6.1–6.7.)
  - `archon/ai/prompts/decomposer.md`: replace any QMD-specific text with: "Use the `search` RAG MCP tool to access conversation history"
  - `install.py` (main installer): remove `_set_qmd_enabled` and `_prompt_qmd` functions; replace references with `rag`-equivalent calls or remove entirely if QMD-specific. Do NOT remove `'qmd_checker.sh'` from the `_STALE_SCRIPTS` tuple — that entry correctly removes the legacy script location from users' `~/.archon/scripts/` on update. Do NOT add `'qmd_installer.sh'` to `_STALE_SCRIPTS` — it was never installed to `~/.archon/scripts/` (it is a repo-root source file); adding it to `_STALE_SCRIPTS` is a no-op and misleading. The deletion of `scripts/qmd_installer.sh` from the repo is handled as a git-tracked deletion in Task 6.5. No `_STALE_SCRIPTS` changes needed for `qmd_installer.sh`. The schedule bundle's `schedules/health-summary/scripts/qmd_checker.sh` is handled separately in the Task 6.9 schedule bundle cleanup step.
- **Releasable**: after this task, no `qmd_url` / `qmd_enabled` symbols remain in `archon/ai/` or `install.py`
- **Tests (TDD)**:
  - Unit: add to `tests/ai/test_decomposer.py` — `test_decomposer_has_no_qmd_url_attribute`: assert `Decomposer` instance has no `_qmd_url` attribute and accepts `rag_url` parameter
  - Unit: add to `tests/ai/test_decomposer.py` — `test_decomposer_startup_prompt_rag_enabled`: `Decomposer` instantiated with `rag_url` set → `startup_context_prompt` called with `rag_enabled=True`
  - Unit: add to `tests/ai/test_classifier.py` — `test_classifier_has_no_qmd_url_attribute`: assert `Classifier` instance has no `_qmd_url` attribute and accepts `rag_url` parameter
  - Unit: add to `tests/ai/test_pipeline.py` — `test_pipeline_has_no_qmd_url_attribute`: assert `Pipeline` instance has no `_qmd_url` attribute and accepts `rag_url` parameter
  - Checkpoint: `uv run pytest tests/ai/test_decomposer.py tests/ai/test_classifier.py tests/ai/test_pipeline.py -v`

#### Task 6.9 — Delete or rewrite all QMD test files
- [ ] **Files to delete** (dedicated QMD test files — coverage replaced by new RAG tests):
  - `tests/ai/test_qmd_session.py`
  - `tests/ai/test_qmd_live.py`
  - `tests/ai/test_qmd_integration.py`
  - `tests/config/test_qmd_config.py`
  - `tests/gateway/test_qmd_gateway_e2e.py`
  - `tests/gateway/test_qmd_daemon.py`
- [ ] **Files to update in-place** (incidental `qmd` references — update to use `rag_url`/`rag_enabled`):
  - `tests/test_installer_py.py` — replace all `qmd_url`, `_prompt_qmd`, `_set_qmd_enabled` references with their `rag` equivalents
  - `tests/ai/test_context_provider.py` — replace `qmd_enabled` parameter with `rag_enabled`
  - `tests/config/test_config_example_sync.py` — replace `qmd` section references with `rag`
  - ~~`tests/config/test_loader.py`~~ — **NOT in this list**. This file was already updated in Task 1.2's commit (same commit as the `QmdConfig` → `RagConfig` refactor). Do not touch it here.
  - `tests/chat/test_handler_live.py` — remove/update the hardcoded `qmd_checker.sh` string reference
- **Depends on**: Tasks 6.1–6.8 (all qmd→rag renames complete in production code)
- **Description**:
  - Delete all 6 dedicated `test_qmd_*.py` files — their coverage is superseded by new RAG tests added in Phases 1–7
  - Update 4 files with incidental `qmd` references in-place to use `rag_url`/`rag_enabled`
  - **Schedule bundle cleanup**: delete `schedules/health-summary/scripts/qmd_checker.sh`; update `schedules/health-summary/scripts/health_check.sh` to remove the "QMD daemon" section; update `schedules/health-summary/job.toml` to remove `qmd_checker.sh` references
  - **Final checkpoint**: `grep -ri qmd archon/ tests/ scripts/ schedules/` must return zero matches (subject to the exclusion below)
- **Releasable**: after this task, the codebase has zero QMD references; the acceptance criterion "All `qmd` symbols removed from codebase; no references remain" is satisfied
- **Tests (TDD)**:
  - Unit: `test_no_qmd_symbols_in_codebase` (add to integration test suite) — run `grep -ri qmd . --include='*.py' --include='*.toml' --include='*.sh'` programmatically (excluding `.git/`, `Documentation/Completed/`, and the `_STALE_SCRIPTS` section of `install.py` which may contain historical QMD references); assert output is empty
  - Checkpoint: `grep -ri qmd . --include='*.py' --include='*.md' --include='*.toml' --include='*.sh' --exclude-dir='.git' --exclude-dir='Documentation/Completed' | grep -v '_STALE_SCRIPTS'` returns zero matches. **Note**: The string `'qmd_checker.sh'` in `_STALE_SCRIPTS` is intentionally retained to clean up legacy user installs (`~/.archon/scripts/qmd_checker.sh`) and is excluded from the grep check via the `grep -v '_STALE_SCRIPTS'` filter.

---

### Phase 7 — Installer & CLI
> **Releasable**: after Task 7.2 — `archon rag install` completes on macOS, registers the service, and Archon can immediately connect to the RAG server.

#### Task 7.1 — `RagInstaller` class in `archon/rag/install.py`
- [ ] **File**: `archon/rag/install.py`
- **Depends on**: Tasks 1.2, 4.1, 5.1, 6.6, 6.7
- **Description**:
  - `class RagInstaller`:
    - `__init__(self, config_file: str = "config.toml", dry_run: bool = False) -> None`
      — loads `RagConfig` from config file; sets `self.cfg`, `self.dry_run`
    - `def check_deps(self) -> list[str]` — tries `import lancedb`, `import sentence_transformers`, `import docling`, `import markitdown`, `import trafilatura`, `import chonkie`, `import fastmcp`; returns names of packages whose import fails
    - `def install_deps(self) -> None` — runs `subprocess.run(["uv", "pip", "install", "-e", ".[rag]"], check=True)`; no-op if `dry_run`
    - `def create_data_dir(self) -> None` — `Path(self.cfg.db_path).expanduser().mkdir(parents=True, exist_ok=True)`; no-op if `dry_run`
    - **Platform abstraction**: Per the project constraint ("no `platform.system()` / `sys.platform` checks in `archon/`"), `RagInstaller` must NOT branch on platform directly. Instead, `archon/platform/macos/rag_service.py` and `archon/platform/linux/rag_service.py` provide `RagPlatformService(PlatformService)` subclasses (analogous to the existing Archon service implementations). A new `get_rag_service() -> PlatformService` singleton in `archon/platform/__init__.py` returns the appropriate subclass. `RagInstaller.write_service_file()` delegates to `get_rag_service().register(dry_run=self.dry_run)` and `RagInstaller.load_service()` delegates to `get_rag_service().start(dry_run=self.dry_run)`.
    - (Platform service files `archon/platform/{macos,linux,windows}/rag_service.py` are created in Tasks 6.6 and 6.7 — do not recreate them here.)
    - These subclasses override `service_name`, `register()` (writes plist/unit file), `unregister()`, `start()`, `stop()`, `status()` using the same launchctl/systemctl commands but for label `com.archon.rag` / service `archon-rag`.
    - `def write_service_file(self) -> None` — calls `get_rag_service().register(dry_run=self.dry_run)`. The platform-specific plist/unit file content is defined in `archon/platform/macos/rag_service.py` (Task 6.6) and `archon/platform/linux/rag_service.py` (Task 6.7).
    - `def load_service(self) -> int` — calls `get_rag_service().start(dry_run=self.dry_run)`; returns exit code
    - `def unload_service(self) -> int` — calls `get_rag_service().stop(dry_run=self.dry_run)`
    - `async def create_history_collection(self) -> None` — instantiates `RagPipeline` directly via `create_pipeline(self.cfg)` factory, calls `await pipeline.store.connect()`, then calls `await pipeline.ingest_directory(history_dir, history_collection)` inside a `try/finally` block that calls `await pipeline.store.disconnect()` on exit. Does NOT call the running server via HTTP. `run()` calls this via `asyncio.run(self.create_history_collection())`. **Important**: Direct pipeline access bypasses the running server. If the RAG server is running, do not call this simultaneously — LanceDB's local file format does not safely support concurrent writers from separate processes. `run()` should warn the user with a log/print message and proceed (do not block on user input) if the RAG service is running (`get_rag_service().status()` returns running state).
    - `def run(self, non_interactive: bool = False) -> int` — full install flow:
      1. Print warning about ~2 GB model download
      2. If not `non_interactive`: prompt to confirm (Y/N)
      3. `check_deps()` → if missing: `install_deps()`
      4. `create_data_dir()`
      5. Call `asyncio.run(self.create_history_collection())` — ingests history dir into LanceDB directly (the server is NOT running yet, so this is safe from concurrent-writer issues). **Note**: `run()` is synchronous; `asyncio.run()` creates a fresh event loop. Tests for this method must be sync (`def test_...`, not `async def`) to avoid "event loop already running" errors. History collection is ingested BEFORE the server starts to avoid concurrent LanceDB writes.
      6. Write service file (`write_service_file` — delegates to platform layer)
      7. `load_service()`
      8. Wait up to 30s for server to respond (HTTP probe loop)
      9. Print "RAG server ready. Enable in config.toml: [rag] enabled = true"
      10. Return 0 on success, 1 on failure
    - `def run_uninstall(self, delete_db: bool = False) -> int`:
      1. `unload_service()`
      2. Call `get_rag_service().unregister(dry_run=False)` (removes the plist/unit file via the platform service abstraction).
      3. If `delete_db`: remove `cfg.db_path` directory
      4. Print instructions to remove `[rag]` from config
- **Releasable**: after this task, `RagInstaller` is callable programmatically and from tests
- **Tests (TDD)** — `tests/rag/test_install.py`:
  - **(Setup)**: Use `override(rag_service=mock_service)` in test setup and `reset()` in teardown for all tests that call `get_rag_service()` indirectly. Alternatively, add this as a conftest fixture for `tests/rag/` to ensure no test triggers real `launchctl`/`systemctl` calls in CI.
  - Unit: `test_check_deps_all_present` — mock all imports succeed → empty list
  - Unit: `test_check_deps_missing_package` — mock one import fail → that name in list
  - Unit: `test_create_data_dir_creates_path` (tmp_path) — directory created
  - Unit: `test_create_data_dir_dry_run_no_op` — dry_run=True → no directory created
  - Unit: `test_write_service_file_delegates_to_platform` — `get_rag_service().register()` called
  - Unit: `test_write_service_file_dry_run` — dry_run=True → `register(dry_run=True)` called
  - Unit: `test_rag_platform_service_macos_plist_contains_label` (`tests/platform/macos/test_rag_service.py`) — plist XML contains `com.archon.rag`
  - Unit: `test_rag_platform_service_macos_plist_contains_python_executable` — `sys.executable` in plist
  - Unit: `test_run_aborts_on_user_decline` (monkeypatched input → "n") — returns without install
  - Unit: `test_installer_run_calls_create_history_collection` (mocked pipeline via create_pipeline mock) — `ingest_directory` called with history dir
  - Unit: `test_create_history_collection_builds_pipeline_and_ingests` — verifies `create_pipeline` called, `store.connect()` awaited BEFORE `ingest_directory`, and `store.disconnect()` awaited in finally block (use `mock.assert_has_calls([call.connect(), call.ingest_directory(...)], any_order=False)`)
  - Unit: `test_installer_run_warns_when_service_running` — mock `get_rag_service().status()` to return running state; verify a warning is printed to stdout and the command proceeds (does not block on user input or abort)
  - Unit: `test_create_history_collection_disconnects_on_ingest_failure` — mock `ingest_directory` to raise an exception; verify `store.disconnect()` is still called (finally block executed)
  - Unit: `test_run_uninstall_stops_and_unregisters_service` — mock `get_rag_service()`; verify both `stop()` and `unregister()` are called
  - Unit: `test_run_uninstall_delete_db_true_removes_directory` (tmp_path) — set `cfg.db_path` to a tmp directory; call `run_uninstall(delete_db=True)`; verify `cfg.db_path` directory is deleted
  - Unit: `test_run_uninstall_delete_db_false_preserves_directory` (tmp_path) — set `cfg.db_path` to a tmp directory; call `run_uninstall(delete_db=False)`; verify `cfg.db_path` directory is NOT deleted
  - Checkpoint: `uv run pytest tests/rag/test_install.py -v`

#### Task 7.2 — `archon rag` CLI subcommand in `archon/cli/rag_cmd.py` + `main.py`
- [ ] **File**: `archon/cli/rag_cmd.py`
- [ ] **File**: `archon/cli/main.py`
- **Depends on**: Task 7.1
- **Description**:
  - `archon/cli/rag_cmd.py`:
    - `def run_rag(args: argparse.Namespace) -> int` — dispatches to sub-actions below
    - `def _run_install(args: argparse.Namespace) -> int`:
      — `RagInstaller(dry_run=args.dry_run).run(non_interactive=args.non_interactive)`
    - `def _run_uninstall(args: argparse.Namespace) -> int`:
      — `RagInstaller().run_uninstall(delete_db=args.delete_db)`
    - `def _run_start(args: argparse.Namespace) -> int`:
      — calls `get_rag_service().start()`; prints status
    - `def _run_stop(args: argparse.Namespace) -> int`:
      — calls `get_rag_service().stop()`
    - `def _run_status(args: argparse.Namespace) -> int`:
      — calls `get_rag_service().status()` and prints state; then fetches collection stats by building only a `RagStore(cfg.rag.db_path)`, calling `asyncio.run(async_wrapper())` where `async_wrapper` does `connect -> list_collections -> disconnect` wrapped in try/finally. No embedder, reranker, chunker, or parser needed — `RagStore` is the only dependency for listing. LanceDB cross-process concurrent access is not guaranteed safe. The try/except around `list_collections()` is mandatory (not optional) — always catch LanceDB errors and display "Stats unavailable — server may be writing" rather than crashing. If the RAG service is not running or an error occurs, prints "unreachable" and returns non-zero exit code.
    - `def _run_ingest(args: argparse.Namespace) -> int`:
      — loads config; determines `path = args.path or cfg history_dir`, `collection = args.collection or cfg.rag.history_collection`. Before running: check if the RAG service is running via `get_rag_service().status()`. If running AND the service is reachable, warn the user and exit with a non-zero return code (do not proceed with direct write). Proceed with direct pipeline only when the service is confirmed stopped. This prevents data corruption from concurrent LanceDB writers. When proceeding: builds pipeline via `create_pipeline(cfg)` and wraps the full async flow — creates an async function that calls `await pipeline.store.connect()`, then `await pipeline.ingest_directory(path, collection)`, then `await pipeline.store.disconnect()` in a `finally` block — and calls `asyncio.run()` on that wrapper function. Do NOT call `asyncio.run(pipeline.ingest_directory(...))` directly — store must be connected first or every store operation raises `RuntimeError("RagStore not connected")`. Does NOT call the running server via HTTP — consistent with installer's direct pipeline approach, requires no MCP client library.
  - `archon/cli/main.py` additions:
    - Add `p_rag = sub.add_parser("rag", help="Manage the RAG search service")`
    - Add sub-subparser: `rag_sub = p_rag.add_subparsers(dest="rag_command")`
    - Register: `install` (with `--dry-run`, `--non-interactive`), `uninstall` (with `--delete-db`), `start`, `stop`, `status`, `ingest` (with optional `path` positional + `--collection`)
    - Add dispatch: `if args.command == "rag": from archon.cli.rag_cmd import run_rag; return run_rag(args)`
- **Releasable**: after this task, all `archon rag` subcommands are available from the CLI
- **Tests (TDD)** — `tests/cli/test_rag_cmd.py`:
  - **(Setup)**: Use `override(rag_service=mock_service)` in test setup and `reset()` in teardown for all tests that call `get_rag_service()` indirectly. Alternatively, add this as a conftest fixture for `tests/cli/` to ensure no test triggers real `launchctl`/`systemctl` calls in CI.
  - Unit: `test_rag_install_delegates_to_installer` (mocked RagInstaller) — `run()` called
  - Unit: `test_rag_install_dry_run_flag` — `--dry-run` → `dry_run=True` passed
  - Unit: `test_rag_uninstall_delegates` (mocked RagInstaller) — `run_uninstall()` called
  - Unit: `test_rag_ingest_no_args_uses_history_dir` (mocked pipeline) — history dir used as path
  - Unit: `test_rag_ingest_with_path_and_collection` — custom path + `--collection` forwarded correctly
  - Unit: `test_main_rag_command_registered` — `archon rag --help` exits 0 and includes "rag" subcommands
  - Unit: `test_rag_start_calls_platform_service` — `archon rag start` → `get_rag_service().start()` called
  - Unit: `test_rag_stop_calls_platform_service` — `archon rag stop` → `get_rag_service().stop()` called
  - Unit: `test_rag_status_prints_service_state` — `archon rag status` → output contains "running" or "stopped"
  - Unit: `test_rag_status_server_unreachable_prints_warning` — server not reachable → output contains "unreachable", returns non-zero exit code
  - Unit: `test_rag_ingest_warns_when_service_running` — mock `get_rag_service().status()` to return running state; verify a warning is printed to stdout before proceeding
  - Unit: `test_rag_status_disconnects_on_list_collections_failure` — mock `list_collections()` to raise; verify `store.disconnect()` is still called AND the output contains "Stats unavailable"
  - Unit: `test_rag_status_shows_unavailable_on_lock_error` — mock `list_collections()` to raise a LanceDB error; verify "Stats unavailable" appears in stdout output (not just that disconnect was called)
  - Unit: `test_rag_ingest_disconnects_on_failure` — mock `ingest_directory` to raise; verify `store.disconnect()` is called
  - Checkpoint: `uv run pytest tests/cli/test_rag_cmd.py -v`

---

### Phase 8 — Documentation
> **Releasable**: after Task 8.2 — all user-facing docs reflect the new `[rag]` config and `archon rag` CLI.

#### Task 8.1 — Update `examples/config.toml.example`
- [ ] **File**: `examples/config.toml.example`
- **Depends on**: Task 1.2
- **Description**:
  - Remove the `[qmd]` section entirely
  - Add a `[rag]` section with all fields, defaults, and inline comments explaining each option, model choices, and the ~2 GB download warning for first install
- **Releasable**: N/A (docs-only)
- **Tests (TDD)**: N/A
  - Checkpoint: manual review

#### Task 8.2 — User manual RAG section + CLAUDE.md update
- [ ] **File**: `Documentation/UserManual/user_manual.md`
- [ ] **File**: `CLAUDE.md`
- [ ] **File**: `Documentation/Backlog/RAG integration for multi-format document search.md` (delete)
- **Depends on**: Task 8.1
- **Description**:
  - `user_manual.md`: add "RAG Search" section after the existing "QMD" section (or replace if QMD section exists):
    - Installation steps (`archon rag install`)
    - Hardware requirements (~2 GB RAM for models)
    - How to add collections (`archon rag ingest /path --collection name` or via MCP `ingest_directory` tool)
    - Supported file formats (PDF, DOCX, XLSX, PPTX, HTML, MD, TXT, code files)
    - The 7 available MCP tools with brief descriptions
    - `archon rag` CLI reference
  - `CLAUDE.md`: update `[rag]` config fields table (currently documents `[qmd]`); update `archon/ai/context_provider.py` entry to reflect `rag_enabled` parameter name
  - `Documentation/Architecture/510_release_and_environment_strategy.md`: remove reference to `scripts/qmd_installer.sh` (script is deleted in Task 6.5)
  - `Documentation/Architecture/120_services_and_integration_architecture.md`: replace `mcp_servers["qmd"]` with `mcp_servers["rag"]`; update section "4. QMD MCP (Optional)" → "4. RAG MCP (Optional)"; update all Mermaid nodes referencing QMD
  - `Documentation/Architecture/200_testing_strategy.md`: replace `test_qmd_*.py` references with the new RAG test file names (`test_store.py`, `test_embedder.py`, `test_reranker.py`, `test_parser.py`, `test_chunker.py`, `test_pipeline.py`, `test_server.py`, `test_install.py`)
  - `Documentation/Architecture/160_operational_readiness_monitoring_and_reliability.md`: update health check entries and log-level table entries that reference QMD
  - `Documentation/Architecture/530_technical_debt_refactoring_roadmap.md`: remove QMD debt items; add RAG items (e.g., incremental re-indexing watcher, Windows service registration)
  - `Documentation/Architecture/110_component_catalog_and_layer_breakdown.md`: update component descriptions to reference RAG instead of QMD
  - `Documentation/ADRs/09_qmd_compatible_history_format.md`: archive (move to `Documentation/Completed/`) and create `Documentation/ADRs/09_rag_history_format.md` as its replacement describing the new RAG integration and technology selection rationale (reference `Documentation/Completed/26_rag_integration_research.md`)
  - `CLAUDE.md`: update remaining `[qmd]` references in the config section that are not already handled by Task 1.2 (e.g., config table row for `[qmd] history_collection` → `[rag] history_collection`)
  - Delete `Documentation/Backlog/RAG integration for multi-format document search.md` (already archived to `Documentation/Completed/26_rag_integration_research.md`)
- **Releasable**: N/A (docs-only)
- **Tests (TDD)**: N/A
  - Checkpoint: `uv run pytest` (full suite) + `uv run mypy archon/`
