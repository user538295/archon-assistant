---
Purpose: Comprehensive guide to Archon's Search (Retrieval-Augmented Generation) feature
Audience: Archon users who want semantic search over conversation history and documents
Status: Active
Last reviewed: 2026-03-28
Next review: 2026-06-28
---

# Search Guide

## Overview

Search (Retrieval-Augmented Generation) is an Archon feature that gives Claude persistent semantic and keyword search over your conversation history and any document collections you define. The underlying service is the [`archon-search`](https://pypi.org/project/archon-search/) package — installed automatically as a dependency of Archon and **enabled by default** since FEAT-046. Once running, Claude can use the `search` MCP tool to recall past conversations or look up information from your documents — entirely offline, without any cloud service.

**What Search enables:**

- Claude remembers past conversations across sessions, even after context is cleared
- Claude can search your PDFs, notes, code files, and any other documents you ingest
- Searches combine vector similarity (semantic) and BM25 keyword matching for best results
- Results are reranked by a cross-encoder model for accuracy

**When to use Search:**

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

The Search server runs as a background service and uses ~300–500 MB of RAM at steady state after models are loaded.

---

## Installation

```bash
archon search install
```

This command performs the following steps automatically:

1. Installs Search Python dependencies (`uv pip install -e ".[search]"`)
2. Creates `~/.archon/search/` data directory
3. Downloads ONNX embedding model (~33–130 MB, `BAAI/bge-small-en-v1.5` by default)
4. Downloads ONNX reranker model (~85 MB, `BAAI/bge-reranker-v2-m3` by default)
5. Detects GPU: NVIDIA via `nvidia-smi` (installs `fastembed-gpu`); Apple Silicon via ARM64 check (validates CoreML, writes `providers` on success)
6. Registers the Search server as a background service:
   - **macOS**: launchd service `com.archon.search`
   - **Linux**: systemd user service `archon-search`
7. Runs an initial ingest of your conversation history into the `archon-history` collection

> **Dry run:** Add `--dry-run` to print every action without executing it.

> **Non-interactive:** Add `--non-interactive` to skip confirmation prompts (for scripted installs).

After installation completes, enable Search in `~/.archon/config.toml`:

```toml
[search]
enabled = true
```

Then restart Archon to connect to the Search server:

- Via Telegram: `/restart`
- Via CLI: `archon restart`

Archon will log a confirmation message when it connects to the Search server at startup. If the server is unreachable, Archon logs a warning and continues without Search — no crash, no loss of other functionality.

---

## Configuration

All Search settings live under the `[search]` section in `~/.archon/config.toml`.

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `false` | Connect to the Search server on startup |
| `host` | str | `"localhost"` | Search server hostname |
| `port` | int | `8282` | Search server HTTP port |
| `db_path` | str | `"~/.archon/search"` | LanceDB database directory |
| `collections` | list[str] | `["~/.archon/history/sessions", "~/.archon/workspace"]` | Directories to index; synced on every service start |
| `sync_timeout_seconds` | int | `0` | Max seconds to wait for startup sync; `0` = defer sync to background, HTTP starts immediately (recommended) |
| `embedding_model` | str | `"BAAI/bge-small-en-v1.5"` | fastembed embedding model |
| `reranker_model` | str | `"BAAI/bge-reranker-v2-m3"` | fastembed reranker model |
| `providers` | list[str] | `[]` | ONNX execution providers (`[]` = CPU; `["CUDAExecutionProvider"]` = NVIDIA; `["CoreMLExecutionProvider"]` = Apple Silicon) — applied to both embedding and reranker models |
| `top_k_retrieve` | int | `20` | Candidates retrieved before reranking |
| `top_k_return` | int | `5` | Final results returned to Claude after reranking |
| `chunk_size` | int | `512` | Tokens per text chunk |
| `pinned_collections` | list[str] | `["~/.archon/history/sessions", "~/.archon/workspace"]` | Paths always searched regardless of routing; bypass confidence gate and decomposer selection |
| `max_parallel_collections` | int | `3` | Maximum concurrent LanceDB search operations per query; pinned collections consume slots first |
| `routing_confidence_threshold` | float | `0.30` | Minimum cosine similarity (0.0–1.0) to include a collection in the Tier 3 shortlist; if no collection exceeds this, only pinned collections are searched. Applies only in Tier 3 (centroid pre-ranking). |
| `routing_shortlist_size` | int | `8` | Maximum collections forwarded to the decomposer for Tier 2/3 selection |

**Minimal working config** (all other values use defaults):

```toml
[search]
enabled = true
```

**Custom port and additional collections:**

```toml
[search]
enabled = true
port = 9090
collections = ["~/.archon/history/sessions", "~/.archon/workspace", "~/Documents/notes"]
```

**GPU acceleration** (set automatically by `archon search install`):

```toml
[search]
enabled = true
providers = ["CUDAExecutionProvider"]      # NVIDIA GPU
# providers = ["CoreMLExecutionProvider"]  # Apple Silicon
```

---

## Pinned Collections

`pinned_collections` is a list of paths that are **always searched**, regardless of routing decisions. They bypass the confidence gate and decomposer selection.

```toml
[search]
pinned_collections = [
  "~/.archon/history/sessions",
  "~/.archon/workspace",
]
```

By default, `pinned_collections` mirrors the default `collections` list, so the two default collections are always searched. Set it to `[]` to rely entirely on routing.

Pinned collections consume slots from `max_parallel_collections`, so the remaining slots go to routable collections selected by the decomposer. If `pinned_collections` fills all slots, no routable collections are searched that query.

> **Note:** A pinned path that is not declared in `[search] collections` is silently skipped at runtime and flagged by `archon doctor`.

---

## Multi-Collection Routing

When you have more than three non-pinned collections, Archon uses a three-tier routing strategy to select which ones to search:

| Tier | Condition | Behaviour |
|---|---|---|
| 1 | ≤3 routable collections | Search all; skip decomposer |
| 2 | 4–`routing_shortlist_size` routable | Decomposer selects from all routable |
| 3 | >`routing_shortlist_size` routable | Centroid pre-ranking narrows to `routing_shortlist_size`, then decomposer selects |

**Centroid pre-ranking (Tier 3):** Archon embeds the query and computes cosine similarity against each collection's centroid vector (the mean of all chunk embeddings). Collections below `routing_confidence_threshold` are excluded. The top `routing_shortlist_size` remaining collections are forwarded to the decomposer.

**Decomposer context block:** When routing involves the decomposer (Tiers 2 and 3), Archon appends a `<search_collections>` block to the routing prompt listing each candidate collection and its description. The decomposer selects relevant collections and outputs their names in `<search_selected_collections>` tags.

Example `<search_collections>` block:

```
<search_collections>
Available collections (select 1–2 most relevant for this query, output their names in
<search_selected_collections>name1, name2</search_selected_collections> tags at the end of your routing decision):
- sessions: (no description)
- docs: Technical reference documents for the API
</search_collections>
```

After collection selection, Archon runs parallel searches (up to `max_parallel_collections` concurrently), then normalizes and merges results before injecting them as context.

---

## Apple Silicon GPU Acceleration

On Macs with Apple Silicon (M1, M2, M3, …), the installer automatically detects the ARM64 architecture and attempts to configure the CoreML execution provider for ONNX Runtime. CoreML routes inference through the Neural Engine and GPU cores, which can significantly reduce embedding latency compared to CPU-only operation.

### Auto-detection during install

`archon search install` prints one of two messages at the end of the GPU configuration step:

```
CoreML acceleration validated — GPU/Neural Engine active.
```

or

```
Warning: CoreML validation failed — falling back to CPU. macOS 12+ required.
```

If validation succeeds, the installer writes `providers = ["CoreMLExecutionProvider"]` to the Search configuration. If validation fails, **no `providers` key is written** — the Search server falls back to CPU automatically. There is no broken configuration left behind.

### What CoreML does

ONNX Runtime's `CoreMLExecutionProvider` compiles the ONNX model graph for Apple's Neural Engine / GPU. This compilation happens once on first use (a few seconds). Subsequent queries use the compiled graph, which is generally faster than CPU inference on Apple Silicon.

The `providers` setting is applied to both the embedding model and the reranker model. The installer validates the embedding model only; the reranker receives the same provider list but is not independently validated during install.

### macOS 12+ requirement

CoreML execution requires macOS 12 Monterey or later. On macOS 11 or earlier, validation will fail and the installer automatically falls back to CPU — no action needed.

### Runtime fallback

After a successful install, CoreML can silently fall back to CPU if the system is updated to a macOS version that breaks the compiled CoreML graph, or if the ONNX Runtime or fastembed packages are upgraded to an incompatible version.

If you notice a performance regression, re-run the installer to re-validate and re-configure:

```bash
archon search install
```

### Manual override

To force a specific provider (or to restore CoreML after an update), use:

```bash
archon config set search.providers '["CoreMLExecutionProvider"]'
archon search stop && archon search start
```

To revert to CPU:

```bash
archon config set search.providers '[]'
archon search stop && archon search start
```

### Known limitation

The validation step tests the **embedding model** only (`BAAI/bge-small-en-v1.5` by default). The reranker receives the same `providers` list and will attempt to use CoreML at runtime, but is not explicitly validated during install — if the reranker's model graph is not CoreML-compatible, it will silently fall back to CPU.

---

## Declarative Collections

Archon manages your indexed collections declaratively: the `[search] collections` list in `config.toml` defines the desired state, and Archon automatically reconciles it with the LanceDB database.

### Default paths

By default, two directories are indexed:

| Directory | Collection name | Contents |
|---|---|---|
| `~/.archon/history/sessions` | `sessions` | Daily conversation history files |
| `~/.archon/workspace` | `workspace` | Files in your Claude working directory |

### How sync works

**On service startup** — Archon runs a background sync against all paths listed in `[search] collections`. Existing collections are left unchanged; missing ones are ingested from scratch; managed collections that were removed from the config list are dropped from LanceDB (unmanaged collections — created outside Archon — are never touched).

**Manual sync** — To reconcile immediately without restarting the service:

```bash
archon search sync
```

This is useful after editing `config.toml` to add or remove paths.

### Sync timeout

The `sync_timeout_seconds` setting controls how long Archon waits for the startup sync to finish before starting the HTTP endpoint:

```toml
[search]
sync_timeout_seconds = 0   # recommended: defer sync to background, HTTP starts immediately
```

The default is `0` — sync runs in the background and Archon starts immediately. Setting a positive value (e.g. `30`) blocks HTTP startup for **up to** that many seconds while waiting for the sync to finish before responding to health checks. Using a positive value delays the HTTP health endpoint, which can cause `archon search install` to time out if combined Python startup and sync time exceeds the installer's readiness budget.

### Collision resolution

Collection names are derived from the last path component (e.g., `~/projects/notes` → `notes`). If two configured paths share the same basename, Archon walks up the directory tree to include the parent name (e.g., `work_notes` vs `personal_notes`). If collisions persist at maximum depth, a 6-character SHA-1 hash suffix is appended (e.g., `notes_3a7f2c`).

---

## CLI Collection Management

The `archon search collection` subcommand provides imperative control over individual collections without editing `config.toml` manually.

### List collections

```bash
archon search collection list
```

Shows all LanceDB collections with their source path, document count, chunk count, and status:

- `indexed` — in both config and LanceDB
- `orphan (managed)` — in manifest but not in config (will be dropped on next sync)
- `unmanaged` — in LanceDB but not in config or manifest (created outside Archon)

Example output:

```
sessions  path=~/.archon/history/sessions  docs=142  chunks=3847  status=indexed
workspace  path=~/.archon/workspace  docs=28  chunks=761  status=indexed
my-docs  path=/Users/me/documents  (not yet indexed)
```

### Add a collection

```bash
archon search collection add /path/to/directory
```

Registers the path in `[search] collections`, immediately ingests all supported files from that directory, and prints instructions to restart the service. The config is updated first — if ingestion fails, the path remains registered so you can retry with `archon search sync`.

> **Note:** If the Search service is currently running, a write-conflict warning is printed. The add proceeds anyway, but you may see inconsistent results until you restart the service.

### Remove a collection

```bash
archon search collection remove /path/to/directory
```

Drops the LanceDB collection and removes the path from `[search] collections`. The service must be stopped first; add `--force` to skip that check:

```bash
archon search collection remove /path/to/directory --force
```

Use `--dry-run` to preview what would be removed without making any changes:

```bash
archon search collection remove /path/to/directory --dry-run
```

`--dry-run` and `--force` are mutually exclusive.

### Inspect a collection

```bash
archon search collection info <name>
```

Prints metadata for a single collection by name:

```
name:            sessions
description:     (none)
doc_count:       142
chunk_count:     3847
embedding_model: BAAI/bge-small-en-v1.5
centroid:        present
last_indexed:    2026-03-27T10:15:42+00:00
```

`centroid: absent` means the collection has not been indexed since centroid computation was added — run `archon search collection reindex <name>` to regenerate it.

### Force full reindex

```bash
archon search collection reindex <name>
```

Re-ingests all files in the collection's source directory, replacing chunks for any changed files. Files removed from disk since last ingest are not automatically cleaned up. Regenerates embeddings, centroids, and descriptions. The service must be stopped first. Use this to:

- Fix a model-mismatch warning (embedding model was changed since last ingest)
- Regenerate missing centroids
- Pick up file changes that startup sync does not detect

```bash
archon search stop
archon search collection reindex sessions
archon search start
```

### Help

```bash
archon search collection help
archon search collection        # same effect
```

---

## Migration

### Upgrading from `history_collection`

Earlier versions of Archon used a `history_collection = "archon-history"` key to name the conversation history index. This key is no longer supported.

**What happens automatically:**
- On the first sync after upgrade, if a `archon-history` LanceDB table exists and no `sessions` table exists, Archon renames it to `sessions` automatically.
- If both `archon-history` and `sessions` exist, Archon logs a warning and skips migration. Remove `archon-history` manually once you have confirmed `sessions` is correct.

**Remove the old key from your config.** If `history_collection` is present in `~/.archon/config.toml`, Archon logs a deprecation warning and ignores the value. Edit the file and delete the line:

```bash
archon config edit    # opens ~/.archon/config.toml in $EDITOR
```

### Manifest file

Archon tracks which collections it manages in a JSON manifest file:

```
{db_path}/sync_manifest.json
```

Default location: `~/.archon/search/sync_manifest.json`

The manifest maps collection names to their source paths. Collections not in the manifest are treated as "unmanaged" — Archon will not drop them during sync. You can inspect it with:

```bash
cat ~/.archon/search/sync_manifest.json
```

---

## Adding document collections

### Ingest a directory

```bash
archon search ingest /path/to/documents --collection my-docs
```

This parses every supported file in the directory, splits it into overlapping text chunks, embeds each chunk, and stores the results in a named LanceDB collection. Progress is printed for each file processed.

If `--collection` is omitted, the collection name defaults to the directory's basename (e.g., `/home/user/notes` → collection `notes`).

### Ingest a single file

```bash
archon search ingest /path/to/document.pdf --collection research
```

### Re-ingest conversation history

```bash
archon search ingest
```

With no path argument, Archon re-ingests your conversation history sessions directory. Run this periodically to pick up recent conversations.

### Supported file formats

| Category | Extensions | Notes |
|---|---|---|
| Documents | `.pdf`, `.docx`, `.xlsx`, `.pptx` | |
| Web | `.html`, `.htm` | |
| Text | `.md`, `.txt`, `.rst` | |
| Code | `.py`, `.js`, `.ts`, `.go`, `.rs`, `.java`, `.cpp`, `.c`, `.sh`, and more | |
| Images | `.png`, `.jpg`, `.jpeg`, `.tiff`, `.tif`, `.bmp`, `.webp` | OCR via docling — text visible in the image is extracted |

> **Note:** Animated and vector image formats (`.gif`, `.svg`, `.ico`) are not supported and are skipped during ingest.

Unsupported extensions are read as plain text and indexed as-is.

> **Re-ingesting is safe.** Ingesting a file that was already indexed replaces its previous chunks — it does not create duplicates. Archon identifies documents by a hash of their file path.

---

## How Claude uses Search

When Search is enabled, Claude automatically has access to a `search` MCP tool. Claude decides when to call it — you do not need to trigger it manually. In practice, Claude searches when:

- You ask about something that might be in past conversations ("do you remember when we discussed X?")
- You ask a factual question that might be in your ingested documents
- You reference a topic that appears in multiple past sessions

The system prompt Claude receives at session startup includes a reminder that the `search` tool is available and should be used for recall tasks.

> **Tip:** You can ask Claude explicitly to search: "Search your history for our discussion about authentication." Claude will call the `search` tool and report what it finds.

---

## Available MCP tools

These tools are available to Claude once the Search server is connected. You can also ask Claude to use them directly by name.

| Tool                    | Description                                                                                   |
| ----------------------- | --------------------------------------------------------------------------------------------- |
| `search`                | Hybrid BM25 + vector search; returns ranked results with text snippet, source path, and score |
| `search_with_context`   | Like `search`, but includes surrounding chunks for fuller context. `context_window` (default `1`) controls how many adjacent chunks are included on each side |
| `ingest_file`           | Parse, chunk, embed, and store a single file                                                  |
| `ingest_directory`      | Ingest all supported files under a directory path. `glob_pattern` (default `**/*`) filters which files to include |
| `list_collections`      | List all indexed collections with document count and total chunk count (centroid vectors omitted) |
| `get_collections_meta`  | Return full `CollectionMeta` for all collections, **including centroid vectors** (used by the routing system) |
| `get_collection_meta`   | Return full `CollectionMeta` for one named collection, including centroid                     |
| `list_documents`        | List documents within a specific collection                                                   |
| `delete_document`       | Remove a document and all its chunks from the collection                                      |

**Example prompts:**

- "Search for our discussion about the database migration last month"
- "Ingest the file at /Users/me/notes/api-design.md into the docs collection"
- "List all collections you have access to"
- "Delete the document with ID abc123 from the my-docs collection"

---

## CLI reference

| Command | Description |
|---|---|
| `archon search install` | Install dependencies, download models, register service, run initial ingest |
| `archon search install --dry-run` | Print all actions without executing |
| `archon search install --non-interactive` | Skip confirmation prompts |
| `archon search uninstall` | Stop and remove the Search service; database in `~/.archon/search/db` is preserved |
| `archon search uninstall --delete-db` | Stop and remove the service and delete the vector database |
| `archon search start` | Start the Search MCP server |
| `archon search stop` | Stop the Search MCP server |
| `archon search status` | Show service state, port, and collection statistics |
| `archon search ingest [path] [--collection name]` | Ingest files; defaults to history sessions dir if no path given |
| `archon search sync` | Manually reconcile all configured collections with LanceDB |
| `archon search collection list` | List all collections with status, path, doc and chunk counts |
| `archon search collection add <path>` | Register path, ingest it, and add to config |
| `archon search collection remove <path>` | Drop LanceDB collection and remove from config (service must be stopped) |
| `archon search collection remove <path> --force` | Remove collection even while service is running |
| `archon search collection remove <path> --dry-run` | Print what would be removed without making changes |
| `archon search collection info <name>` | Show metadata for a collection: doc/chunk count, embedding model, centroid status, last indexed |
| `archon search collection reindex <name>` | Force full re-ingest of a collection (service must be stopped) |

> **Windows note:** `archon search start` and `archon search stop` are not supported on Windows. Run the server manually with `python -m archon.search.server`. All other functionality works on Windows.

---

## Checking Search status

```bash
archon search status
```

Example output:

```
Search Service
  Status:     running
  Host:       localhost
  Port:       8282
  DB path:    ~/.archon/search/db

Collections
  sessions    done       142 documents    3,847 chunks
  workspace   partial    28 / 120 files    761 chunks
  notes       pending    —
```

While background indexing is running, collections show their current state:
- `done` — fully indexed and searchable
- `partial (N/M files)` — indexing in progress with some files done; vector search available on already-indexed content
- `in_progress` — indexing started but no files processed yet
- `pending` — queued, not yet started
- `failed` — indexing failed; check `archon logs` for details

If the server is not running:

```
Search Service
  Status:     stopped

Start with: archon search start
```

---

## Known limitations

- **No automatic re-indexing of existing collections** — startup sync only adds missing collections and removes dropped ones. It does not detect file changes within an already-indexed collection. `archon search sync` only adds NEW collections and removes DROPPED ones — it does not re-ingest files within an already-indexed collection. Use `archon search ingest <path>` to pick up new/changed files in an existing collection.
- **Reranker latency** — adds ~160 ms per search query on CPU. This is negligible for a personal knowledge base.
- **No QMD migration** — previous QMD collections are not imported. Re-ingest from your source files.
- **No incremental ingest (v1)** — re-ingesting a collection replaces all chunks for changed documents (identified by file path hash). Full incremental diffing is deferred.
- **Windows service management** — `archon search start/stop` are stubs on Windows; run the server manually.
- **GPU support: NVIDIA and Apple Silicon** — the installer detects NVIDIA via `nvidia-smi` and Apple Silicon via ARM64 architecture check. AMD/ROCm is not supported; those systems use CPU.
- **Reranker GPU support unvalidated** — the reranker receives the same `providers` setting as the embedding model but is not validated during install. If the reranker's ONNX graph is incompatible with the configured provider, it silently falls back to CPU.

---

## Health Checks (`archon doctor`)

`archon doctor` includes Search-specific checks when `[search] enabled = true`.

**Config-only check (always runs):**

- Each path in `pinned_collections` is verified to also appear in `collections`. A path that is pinned but not declared as a managed collection is skipped at runtime and flagged as a warning.

**Live checks (require the Search server to be running):**

Collections that are still indexing are shown as informational — not warnings:

| Output | Meaning |
|---|---|
| `⏳ Collection 'X' — partial (N/M files)` | Indexing in progress; vector search already works on indexed content |
| `⏳ Collection 'X' — indexing starting` | Indexing started but no files processed yet |
| `⏳ Collection 'X' — pending` | Collection queued, not yet started |
| `❌ Collection 'X' — failed: <error>` | Indexing failed — action required |

Warnings for fully-indexed (done) collections:

| Warning | Cause | Resolution |
|---|---|---|
| `Collection 'X' last indexed N days ago` | No ingest in the last 7 days | Run `archon search collection reindex X` or `archon search ingest` |
| `Collection 'X' indexed with 'old-model', current model is 'new-model'` | Embedding model was changed | Run `archon search collection reindex X` |
| `Collection 'X' is empty` | Ingest never ran or failed | Run `archon search ingest` or `archon search collection reindex X` |
| `Collection 'X' has no centroid — routing disabled for this collection` | Collection was indexed before centroid support was added | Run `archon search collection reindex X` |

If the Search server is not reachable, live checks are skipped and `archon doctor` prints a notice.

---

## Query Telemetry (opt-in)

Archon Search can log anonymised query metadata to a local JSONL file for your own analysis. Telemetry is **disabled by default** and never transmitted anywhere — all data stays on your machine.

### Enabling telemetry

Add a `[telemetry]` section to `~/.archon/archon-search.toml`:

```toml
[telemetry]
enabled = true
retention_days = 30       # delete files older than this (default: 30)
log_dir = "~/.archon/search-logs"  # where JSONL files are written
```

Restart the Search server for the change to take effect:

```bash
archon search stop && archon search start
```

### What is logged

Each query appends one JSON line to a daily file (`~/.archon/search-logs/YYYY-MM-DD.jsonl`). Log files rotate at UTC midnight and old files are deleted automatically after `retention_days` days.

Every entry contains:

| Field | Description |
|---|---|
| `query_id` | Random UUID — no connection to query text |
| `timestamp` | UTC ISO 8601 |
| `endpoint` | `"search"`, `"search_with_context"`, or `"route"` |
| `latency_ms` | End-to-end handler latency |
| `status` | `"ok"`, `"timeout"`, `"validation_error"`, or `"internal_error"` |
| `collection` | Collection searched (retrieval calls only) |
| `result_count` | Number of results returned (retrieval calls only) |
| `result_doc_ids` | Document IDs of results (retrieval calls only) |
| `collections` | Collections selected (route calls only) |
| `decomposer_invoked` | Whether the decomposer chose the collections (route calls only) |
| `error_kind` | Coarse error category on failure — never contains error message text |

### What is never logged

- **Your query text** — the raw query string never enters the telemetry pipeline at any point. This is a structural guarantee: the entry factories that build log records do not accept a `query` parameter.
- Exception messages, stack traces, or any text that could echo user input.

### Privacy note: `doc_id` and filesystem paths

When telemetry is enabled, `result_doc_ids` are logged. In Archon Search, `doc_id` values are derived from the source file path (e.g., `/Users/<name>/Documents/<project>/<file>.md`). If your file paths include personally identifiable information (usernames, project names), those paths will appear in the telemetry log. You accept this trade-off when you opt in. A hashed-doc-id mode is planned for a future release.

### Disabling telemetry

Set `enabled = false` (or remove the section entirely) and restart the Search server. No log files are created or written while telemetry is disabled.

---

## Troubleshooting

### Search server not connecting

Check that the server is running:

```bash
archon search status
```

If stopped, start it:

```bash
archon search start
```

Verify `config.toml` has `enabled = true` under `[search]` and the port matches. Then restart Archon.

### Model download fails during install

The ONNX models are downloaded by `fastembed` on first use. If the download fails (network timeout, disk space), re-run:

```bash
archon search install
```

The installer is idempotent — it skips steps that already completed.

### Search returns no results

Verify that ingestion ran:

```bash
archon search status   # check chunk counts
```

If counts are zero, run:

```bash
archon search ingest   # re-ingest history
```

### High memory usage

The Search server loads both the embedding model and the reranker model into memory on first query. This is expected (~1.5–2 GB total). If memory is a concern, you can stop the Search server when not needed:

```bash
archon search stop
```

Archon will log a warning at startup that Search is unavailable, but continues normally.

---

## See also

- [CLI Reference](cli_reference.md) — full `archon` command reference
- [Search Architecture](../Architecture/180_search_architecture.md) — internal design and component breakdown (developer reference)
- [ADR 09 — Search history format](../ADRs/09_search_history_format.md) — decision record for the Search integration
