---
Purpose: Comprehensive guide to Archon's RAG (Retrieval-Augmented Generation) feature
Audience: Archon users who want semantic search over conversation history and documents
Status: Active
Last reviewed: 2026-03-26
Next review: 2026-06-26
---

# RAG Search Guide

## Overview

RAG (Retrieval-Augmented Generation) is an optional Archon feature that gives Claude persistent semantic and keyword search over your conversation history and any document collections you define. Once installed, Claude can use the `search` MCP tool to recall past conversations or look up information from your documents — entirely offline, without any cloud service.

**What RAG enables:**

- Claude remembers past conversations across sessions, even after context is cleared
- Claude can search your PDFs, notes, code files, and any other documents you ingest
- Searches combine vector similarity (semantic) and BM25 keyword matching for best results
- Results are reranked by a cross-encoder model for accuracy

**When to use RAG:**

- You frequently ask Claude to recall something said weeks ago
- You have reference documents (technical specs, notes, manuals) you want Claude to draw from
- You want Claude to have a long-term memory that survives `/clear`

---

## Hardware requirements

| Resource | Requirement                                                                          |
| -------- | ------------------------------------------------------------------------------------ |
| RAM      | ~2 GB recommended (both models loaded in memory)                                     |
| Disk     | ~150 MB for model download on first install                                          |
| CPU      | All operations work on CPU by default                                                |
| GPU      | NVIDIA: used automatically if `nvidia-smi` is available; Apple Silicon: CoreML used automatically on ARM64 (requires macOS 12+) |

The RAG server runs as a background service and uses ~300–500 MB of RAM at steady state after models are loaded.

---

## Installation

```bash
archon rag install
```

This command performs the following steps automatically:

1. Installs RAG Python dependencies (`uv pip install -e ".[rag]"`)
2. Creates `~/.archon/rag/` data directory
3. Downloads ONNX embedding model (~33–130 MB, `BAAI/bge-small-en-v1.5` by default)
4. Downloads ONNX reranker model (~85 MB, `BAAI/bge-reranker-v2-m3` by default)
5. Detects GPU: NVIDIA via `nvidia-smi` (installs `fastembed-gpu`); Apple Silicon via ARM64 check (validates CoreML, writes `providers` on success)
6. Registers the RAG server as a background service:
   - **macOS**: launchd service `com.archon.rag`
   - **Linux**: systemd user service `archon-rag`
7. Runs an initial ingest of your conversation history into the `archon-history` collection

> **Dry run:** Add `--dry-run` to print every action without executing it.

> **Non-interactive:** Add `--non-interactive` to skip confirmation prompts (for scripted installs).

After installation completes, enable RAG in `~/.archon/config.toml`:

```toml
[rag]
enabled = true
```

Then restart Archon to connect to the RAG server:

- Via Telegram: `/restart`
- Via CLI: `archon restart`

Archon will log a confirmation message when it connects to the RAG server at startup. If the server is unreachable, Archon logs a warning and continues without RAG — no crash, no loss of other functionality.

---

## Configuration

All RAG settings live under the `[rag]` section in `~/.archon/config.toml`.

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `false` | Connect to the RAG server on startup |
| `host` | str | `"localhost"` | RAG server hostname |
| `port` | int | `8282` | RAG server HTTP port |
| `db_path` | str | `"~/.archon/rag"` | LanceDB database directory |
| `history_collection` | str | `"archon-history"` | Collection name for conversation history |
| `embedding_model` | str | `"BAAI/bge-small-en-v1.5"` | fastembed embedding model |
| `reranker_model` | str | `"BAAI/bge-reranker-v2-m3"` | fastembed reranker model |
| `providers` | list[str] | `[]` | ONNX execution providers (`[]` = CPU; `["CUDAExecutionProvider"]` = NVIDIA; `["CoreMLExecutionProvider"]` = Apple Silicon) — applied to both embedding and reranker models |
| `top_k_retrieve` | int | `20` | Candidates retrieved before reranking |
| `top_k_return` | int | `5` | Final results returned to Claude after reranking |
| `chunk_size` | int | `512` | Tokens per text chunk |

**Minimal working config** (all other values use defaults):

```toml
[rag]
enabled = true
```

**Custom port and collection:**

```toml
[rag]
enabled = true
port = 9090
history_collection = "my-history"
```

**GPU acceleration** (set automatically by `archon rag install`):

```toml
[rag]
enabled = true
providers = ["CUDAExecutionProvider"]      # NVIDIA GPU
# providers = ["CoreMLExecutionProvider"]  # Apple Silicon
```

---

## Apple Silicon GPU Acceleration

On Macs with Apple Silicon (M1, M2, M3, …), the installer automatically detects the ARM64 architecture and attempts to configure the CoreML execution provider for ONNX Runtime. CoreML routes inference through the Neural Engine and GPU cores, which can significantly reduce embedding latency compared to CPU-only operation.

### Auto-detection during install

`archon rag install` prints one of two messages at the end of the GPU configuration step:

```
CoreML acceleration validated — GPU/Neural Engine active.
```

or

```
Warning: CoreML validation failed — falling back to CPU. macOS 12+ required.
```

If validation succeeds, the installer writes `providers = ["CoreMLExecutionProvider"]` to the RAG configuration. If validation fails, **no `providers` key is written** — the RAG server falls back to CPU automatically. There is no broken configuration left behind.

### What CoreML does

ONNX Runtime's `CoreMLExecutionProvider` compiles the ONNX model graph for Apple's Neural Engine / GPU. This compilation happens once on first use (a few seconds). Subsequent queries use the compiled graph, which is generally faster than CPU inference on Apple Silicon.

The `providers` setting is applied to both the embedding model and the reranker model. The installer validates the embedding model only; the reranker receives the same provider list but is not independently validated during install.

### macOS 12+ requirement

CoreML execution requires macOS 12 Monterey or later. On macOS 11 or earlier, validation will fail and the installer automatically falls back to CPU — no action needed.

### Runtime fallback

After a successful install, CoreML can silently fall back to CPU if the system is updated to a macOS version that breaks the compiled CoreML graph, or if the ONNX Runtime or fastembed packages are upgraded to an incompatible version.

If you notice a performance regression, re-run the installer to re-validate and re-configure:

```bash
archon rag install
```

### Manual override

To force a specific provider (or to restore CoreML after an update), use:

```bash
archon config set rag.providers '["CoreMLExecutionProvider"]'
archon rag stop && archon rag start
```

To revert to CPU:

```bash
archon config set rag.providers '[]'
archon rag stop && archon rag start
```

### Known limitation

The validation step tests the **embedding model** only (`BAAI/bge-small-en-v1.5` by default). The reranker receives the same `providers` list and will attempt to use CoreML at runtime, but is not explicitly validated during install — if the reranker's model graph is not CoreML-compatible, it will silently fall back to CPU.

---

## Adding document collections

### Ingest a directory

```bash
archon rag ingest /path/to/documents --collection my-docs
```

This parses every supported file in the directory, splits it into overlapping text chunks, embeds each chunk, and stores the results in a named LanceDB collection. Progress is printed for each file processed.

If `--collection` is omitted, the collection name defaults to the directory's basename (e.g., `/home/user/notes` → collection `notes`).

### Ingest a single file

```bash
archon rag ingest /path/to/document.pdf --collection research
```

### Re-ingest conversation history

```bash
archon rag ingest
```

With no path argument, Archon re-ingests your conversation history directory into the `history_collection` defined in config (default: `archon-history`). Run this periodically to pick up recent conversations.

### Supported file formats

| Category | Extensions |
|---|---|
| Documents | `.pdf`, `.docx`, `.xlsx`, `.pptx` |
| Web | `.html`, `.htm` |
| Text | `.md`, `.txt`, `.rst` |
| Code | `.py`, `.js`, `.ts`, `.go`, `.rs`, `.java`, `.cpp`, `.c`, `.sh`, and more |

Unsupported extensions are read as plain text and indexed as-is.

> **Re-ingesting is safe.** Ingesting a file that was already indexed replaces its previous chunks — it does not create duplicates. Archon identifies documents by a hash of their file path.

---

## How Claude uses RAG

When RAG is enabled, Claude automatically has access to a `search` MCP tool. Claude decides when to call it — you do not need to trigger it manually. In practice, Claude searches when:

- You ask about something that might be in past conversations ("do you remember when we discussed X?")
- You ask a factual question that might be in your ingested documents
- You reference a topic that appears in multiple past sessions

The system prompt Claude receives at session startup includes a reminder that the `search` tool is available and should be used for recall tasks.

> **Tip:** You can ask Claude explicitly to search: "Search your history for our discussion about authentication." Claude will call the `search` tool and report what it finds.

---

## Available MCP tools

These tools are available to Claude once the RAG server is connected. You can also ask Claude to use them directly by name.

| Tool                  | Description                                                                                   |
| --------------------- | --------------------------------------------------------------------------------------------- |
| `search`              | Hybrid BM25 + vector search; returns ranked results with text snippet, source path, and score |
| `search_with_context` | Like `search`, but includes surrounding chunks for fuller context                             |
| `ingest_file`         | Parse, chunk, embed, and store a single file                                                  |
| `ingest_directory`    | Ingest all supported files under a directory path                                             |
| `list_collections`    | List all indexed collections with document count and total chunk count                        |
| `list_documents`      | List documents within a specific collection                                                   |
| `delete_document`     | Remove a document and all its chunks from the collection                                      |

**Example prompts:**

- "Search for our discussion about the database migration last month"
- "Ingest the file at /Users/me/notes/api-design.md into the docs collection"
- "List all collections you have access to"
- "Delete the document with ID abc123 from the my-docs collection"

---

## CLI reference

| Command | Description |
|---|---|
| `archon rag install` | Install dependencies, download models, register service, run initial ingest |
| `archon rag install --dry-run` | Print all actions without executing |
| `archon rag install --non-interactive` | Skip confirmation prompts |
| `archon rag uninstall` | Stop and remove the RAG service; database in `~/.archon/rag/db` is preserved |
| `archon rag uninstall --delete-db` | Stop and remove the service and delete the vector database |
| `archon rag start` | Start the RAG MCP server |
| `archon rag stop` | Stop the RAG MCP server |
| `archon rag status` | Show service state, port, and collection statistics |
| `archon rag ingest [path] [--collection name]` | Ingest files; defaults to history dir if no path given |

> **Windows note:** `archon rag start` and `archon rag stop` are not supported on Windows. Run the server manually with `python -m archon.rag.server`. All other functionality works on Windows.

---

## Checking RAG status

```bash
archon rag status
```

Example output:

```
RAG Service
  Status:     running
  Host:       localhost
  Port:       8282
  DB path:    ~/.archon/rag/db

Collections
  archon-history    142 documents    3,847 chunks
  my-docs            28 documents      761 chunks
```

If the server is not running:

```
RAG Service
  Status:     stopped

Start with: archon rag start
```

---

## Known limitations

- **No automatic re-indexing** — run `archon rag ingest` manually after adding new documents or to pick up recent history. Automatic file watching is not implemented.
- **Reranker latency** — adds ~160 ms per search query on CPU. This is negligible for a personal knowledge base.
- **No QMD migration** — previous QMD collections are not imported. Re-ingest from your source files.
- **No incremental ingest (v1)** — re-ingesting a collection replaces all chunks for changed documents (identified by file path hash). Full incremental diffing is deferred.
- **Windows service management** — `archon rag start/stop` are stubs on Windows; run the server manually.
- **GPU support: NVIDIA and Apple Silicon** — the installer detects NVIDIA via `nvidia-smi` and Apple Silicon via ARM64 architecture check. AMD/ROCm is not supported; those systems use CPU.
- **Reranker GPU support unvalidated** — the reranker receives the same `providers` setting as the embedding model but is not validated during install. If the reranker's ONNX graph is incompatible with the configured provider, it silently falls back to CPU.

---

## Troubleshooting

### RAG server not connecting

Check that the server is running:

```bash
archon rag status
```

If stopped, start it:

```bash
archon rag start
```

Verify `config.toml` has `enabled = true` under `[rag]` and the port matches. Then restart Archon.

### Model download fails during install

The ONNX models are downloaded by `fastembed` on first use. If the download fails (network timeout, disk space), re-run:

```bash
archon rag install
```

The installer is idempotent — it skips steps that already completed.

### Search returns no results

Verify that ingestion ran:

```bash
archon rag status   # check chunk counts
```

If counts are zero, run:

```bash
archon rag ingest   # re-ingest history
```

### High memory usage

The RAG server loads both the embedding model and the reranker model into memory on first query. This is expected (~1.5–2 GB total). If memory is a concern, you can stop the RAG server when not needed:

```bash
archon rag stop
```

Archon will log a warning at startup that RAG is unavailable, but continues normally.

---

## See also

- [CLI Reference](cli_reference.md) — full `archon` command reference
- [RAG Architecture](../Architecture/180_rag_architecture.md) — internal design and component breakdown (developer reference)
- [ADR 09 — RAG history format](../ADRs/09_rag_history_format.md) — decision record for the RAG integration
