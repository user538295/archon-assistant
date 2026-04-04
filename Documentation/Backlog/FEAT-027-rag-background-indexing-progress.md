# Feature Brief: RAG Background Indexing with Progress Tracking

## Problem
During `archon install` and `archon update`, the user is blocked waiting for RAG collections to finish indexing — which can take several minutes for large collections — before the daemon starts and they can begin using it. After setup, there is no way to observe indexing progress, detect failures, or avoid redundant re-indexing on restart.

## Goal
Install and update complete immediately after the daemon starts. The user can query per-collection indexing status at any time via `archon rag status` and the `rag_status` MCP tool. Subsequent syncs are fast (only changed files re-indexed), resilient (resumable after crash), and smart (pinned collections indexed first). The feature ships in small, independently releasable phases.

## Users & Context
Any user with RAG enabled, during initial install, `archon update`, or ongoing use with large or frequently-changing collections. They are at the terminal waiting for setup to finish, or in a Telegram conversation asking Claude "is RAG ready yet?"

---

## Phases

### Phase 1 — Non-blocking install + progress visibility ✅ Done

**Delivers**: Progress visibility for background indexing. User can see per-collection indexing status from CLI and Telegram.

> **Note**: With the current default `sync_timeout_seconds=0`, `server.py` already runs sync as a background `asyncio.create_task`. The install path (`install.py` → start service → service runs sync) is already non-blocking. This phase focuses on **progress visibility** — the state file and status display — not on making install non-blocking (it already is). The `install.py` change is limited to adding a status hint message on exit.

#### Use Cases
- **First install**: User runs `uv run install.py`, enables RAG, and gets back to the terminal in seconds. They know indexing is happening in the background.
- **After `archon update`**: Daemon restarts immediately; indexing runs in the background without delaying the restart.
- **Checking progress from the terminal**: User runs `archon rag status` and sees which collections are done, in progress, or failed.
- **Checking progress from Telegram**: User asks Claude "is RAG ready?" and gets a concrete answer per collection.
- **Diagnosing a failed collection**: User sees `failed` in status output with the error message, without digging through log files.
- **Non-interactive / scripted install**: `install.py --non-interactive` exits immediately with zero once the service is running; indexing runs asynchronously.

#### Scenarios

| Scenario | Expected behaviour |
|---|---|
| Install completes, indexing starts | Install exits; service running; state file shows `in_progress` |
| User runs `archon rag status` mid-index | Shows `in_progress 87/120` per collection |
| Indexing completes successfully | State shows `done 120/120`; `completed_at` set |
| Some files in a collection fail to parse | Collection shows `done 38/50`; `error_count=12`; last error message shown. (`failed` is reserved for `ingest_directory` exceptions, not individual file errors.) |
| RAG service not running | `archon rag status` shows service status; no state data available |
| State file missing on first run | Falls back to `CollectionMeta` (`last_indexed` date only) |
| State file corrupt | Silently ignored; same fallback as missing |
| `rag_status` MCP tool called mid-index | Returns `status`, `processed_files`, `total_files` per collection in JSON |

#### Phase Scope
**In scope**:
- `archon/rag/progress.py` (new) — `IndexingStateStore`: atomic read/write of `.indexing_state.json` via tmpfile swap
- `RagCollectionSync.sync()` — write state before, during (batched every 50 files via `progress_cb`), and after each collection
- `RagCollectionSync` — add per-collection `asyncio.Lock` to prevent concurrent sync runs (timeout fallback in `server.py` and MCP `rag_sync` can overlap)
- `install.py` — add status hint message on exit: _"RAG enabled. Indexing in background — run `archon rag status` to track progress."_
- `archon rag status` CLI — display state file data alongside existing `CollectionMeta`
- `rag_status` MCP tool — add `status`, `processed_files`, `total_files` to each collection in JSON response
- Exit code: `archon rag status` exits non-zero if any collection is `failed`

**Out of scope**:
- Partial readiness / health check changes — Phase 2
- Pinned collections priority ordering — Phase 2
- Resumable indexing (skipping already-processed files) — Phase 3
- ETA or time remaining — Phase 7
- Telegram notification on completion — Phase 5
- `archon doctor` real-time integration — Phase 6
- Current file name shown in status — not planned; file count is sufficient

**Core flow**:
1. User runs `uv run install.py` or `archon update`.
2. Install configures RAG, starts the service, fires background sync, and **exits immediately**: _"RAG enabled. Indexing in background — run `archon rag status` to track progress."_
3. `RagCollectionSync.sync()` writes per-collection progress to `~/.archon/rag/.indexing_state.json` before, during (batched every 50 files), and after each collection.
4. `archon rag status` reads the state file and displays:
   ```
   RAG  running (pid 12345)

   Collection          Status        Progress
   ─────────────────────────────────────────
   sessions            in_progress   87 / 120 files
   my-project          done          340 / 340 files
   docs                pending       —
   old-notes           failed        12 / 50 files  (parse error)
   ```
5. `rag_status` MCP tool returns the same data as JSON — Claude can answer "is indexing done?" from Telegram.

**Callback composition**: `sync()` wraps the caller's `progress_cb` internally — it writes state first (batched), then calls through to the caller's callback. The caller does not need to know about state file writes. This keeps the existing single-callback API while allowing both state persistence and caller-visible progress.

**Batched state writes**: State file is written every 50 files (not per-file) to avoid blocking the asyncio event loop with thousands of synchronous disk writes. On crash, at most 50 files of progress are lost — acceptable since Phase 3 handles resumability. A final write always occurs on collection completion or error.

**Changes**:
- `archon/rag/progress.py` (new) — `IndexingStateStore`: atomic read/write of `.indexing_state.json` via tmpfile swap
- `RagCollectionSync.sync()` — write state before, during (batched every 50 files via wrapped `progress_cb`), and after each collection
- `RagCollectionSync` — per-collection `asyncio.Lock` to prevent concurrent sync runs
- `install.py` — add status hint message on exit
- `archon rag status` CLI — display state file data alongside existing collection info (note: current `rag status` reads `CollectionInfo` from `store.list_collections()`, not `CollectionMeta` directly)
- `rag_status` MCP tool — add `status`, `processed_files`, `total_files` to each collection in JSON response

**State schema**:
```json
{
  "collections": {
    "sessions": {
      "status": "in_progress",
      "total_files": 120,
      "processed_files": 87,
      "started_at": "2026-04-02T10:00:00Z",
      "completed_at": null,
      "error": null,
      "error_count": 0
    }
  },
  "last_updated": "2026-04-02T10:05:30Z"
}
```
Status values: `pending` | `in_progress` | `done` | `failed`

`error` holds the last error message; `error_count` tracks total failures. If multiple files fail, `error` shows the most recent and `error_count` tells the user how many. `ingest_directory()` returns `list[IngestResult]` where each result has a status — `processed_files` counts only successful ingests.

---

### Phase 2 — Pinned collections first + partial readiness ✅ Done

**Delivers**: The most critical collections are searchable earliest. Health checks stop false-alarming on in-progress collections.

#### Use Cases
- **New install, user immediately asks a question**: Claude can search `pinned_collections` as soon as they finish, even if other collections are still indexing.
- **`archon rag status` or `archon doctor` during indexing**: Instead of showing a collection as "unhealthy", it shows `partial (87/120 files)` — accurate and not alarming.
- **Large collection with slow ingest**: User can query the collection immediately after the first files are indexed, without waiting for 100% completion.
- **Health check in CI or monitoring**: A collection mid-index does not trigger a false alert.

#### Scenarios

| Scenario | Expected behaviour |
|---|---|
| Sync starts with mixed pinned + regular collections | Pinned collections ingested first, in declaration order |
| `archon rag status` shows a collection at 40% | Status shows `partial (48/120 files)` — not `unhealthy` |
| RAG search called on a partially-indexed collection (vector) | Returns results from already-indexed documents; no error |
| RAG search called on a partially-indexed collection (FTS/hybrid) | FTS index only rebuilt at end of `ingest_directory()`; FTS returns incomplete results until indexing completes. Vector search works immediately. |
| `archon doctor` runs during background sync | Shows `partial` for in-progress; `done` for completed; `failed` for errors |
| All collections finish | Status transitions from `partial` to `done` |

#### Phase Scope
**In scope**:
- `RagCollectionSync.sync()` — sort collections so `pinned_collections` (from config) are ingested before regular ones
- `archon rag status` — show `partial (N/M files)` for in-progress collections
- `rag_status` MCP tool — reflect `partial` status in JSON response
- `_check_rag_health()` in `doctor.py` — treat `in_progress` collections as `partial`, not a warning

**Out of scope**:
- Changing search routing logic — pinned collections already bypass routing; no change needed
- Priority ordering within pinned collections — declaration order in config is sufficient
- Per-collection priority weights — not planned
- `archon doctor` full real-time integration — Phase 6 (this phase only fixes the false-alarm, not the full display overhaul)

**Known limitation — FTS during partial indexing**: `ingest_directory()` calls `store.rebuild_fts_index(collection)` only once at the end. During partial indexing, vector search works immediately but FTS/hybrid search returns incomplete results for recently ingested documents. This is acceptable for Phase 2 — periodic FTS rebuilds during ingestion can be added later if needed.

**Changes**:
- `RagCollectionSync.sync()` — sort collections so `pinned_collections` (from config) are ingested before regular ones. One-line sort change.
- `archon rag status`, `rag_status` MCP tool, `archon doctor` — show `partial (87/120 files)` instead of treating an in-progress collection as unhealthy or "not ready". A collection is queryable as soon as it has any documents (vector search; FTS after completion).

---

### Phase 3 — Resumable indexing ✅ Done

**Delivers**: A crashed or timed-out sync resumes from where it stopped instead of restarting from scratch. Critical for large collections.

#### Use Cases
- **Service crash mid-index**: User restarts the daemon; indexing resumes from the last processed file instead of starting over.
- **Sync timeout** (`sync_timeout_seconds` exceeded): The background task picks up from where it timed out; no files are re-processed.
- **Machine sleep/wake during indexing**: Sync is interrupted by OS sleep; on wake, the service resumes without duplicating already-indexed documents.
- **Large collection (10k+ files)**: A 2-hour index run that crashes at 90% does not require a full restart.
- **Forced full re-index**: User explicitly runs `archon rag reindex <collection>` to clear resume state (e.g. after changing the embedding model).

#### Scenarios

| Scenario | Expected behaviour |
|---|---|
| Service crashes at file 850/1000 | On restart: skip files 1–850; resume from file 851 |
| Sync times out at 600/1000 files | Background task resumes; processes files 601–1000 |
| User runs `archon rag reindex sessions` | `processed_paths` cleared; full re-index runs |
| Sync completes successfully | `processed_paths` retained (reused by Phase 4 change detection) |
| New files added to collection directory | New paths not in `processed_paths` are ingested; existing paths skipped |
| File in `processed_paths` is deleted | Deletion not tracked in this phase — chunks remain until Phase 4 |

#### Phase Scope
**In scope**:
- Add `processed_paths: list[str]` per collection to state file
- `RagCollectionSync.sync()` — on start, load `processed_paths`; skip files whose paths are already present
- `archon rag reindex <collection>` — clears **all** per-collection state (`processed_paths`, and later `file_mtimes`/`file_hashes` from Phase 4) and forces full re-index
- State file write batched (same 50-file cadence as Phase 1); `processed_paths` accumulated in memory between writes

**Out of scope**:
- Deletion detection (files removed from disk still in LanceDB) — Phase 4
- Hash-based change detection — Phase 4
- Auto-expiry of `processed_paths` after 24h TTL — if a completed run's `processed_paths` is older than 24h, it is reset at the start of the next sync so stale state does not block fresh re-indexing
- Resuming mid-file (partial document ingest) — not feasible; file-level granularity only

**Changes**:
- Add `processed_paths: list[str]` per collection to state file
- `RagCollectionSync.sync()` — on startup, load `processed_paths`; skip files already in the list
- On crash/restart: state file retains the list; sync picks up at the next unprocessed file
- `archon rag reindex <collection>` — clears all per-collection state and forces full re-index

---

### Phase 4 — File-level change detection ✅ Done

**Delivers**: `archon update` only re-indexes files that have actually changed. Large stable collections sync in seconds.

#### Use Cases
- **`archon update` on a 5000-file collection**: Only the 12 modified files are re-indexed. Sync completes in seconds instead of 30+ minutes.
- **Daily sync of `sessions` collection**: Only new history files are ingested; unchanged files are skipped.
- **File deleted from source directory**: The corresponding chunks are removed from LanceDB.
- **File content changed** (same path, different content): Old chunks removed; new chunks ingested.
- **Network filesystem or container with unreliable mtimes**: User opts into hash-based detection for that collection.
- **Embedding model changed in config**: All files must be re-indexed; mtime cache is automatically invalidated.

#### Scenarios

| Scenario | Expected behaviour |
|---|---|
| File mtime unchanged since last sync | File skipped entirely |
| File mtime changed | File re-ingested; old chunks replaced; mtime updated in state |
| File deleted from source directory | Chunks removed from LanceDB; path removed from state |
| New file added to source directory | File ingested; path + mtime added to state |
| Embedding model changed in config | All mtimes invalidated; full re-index triggered automatically |
| `file_hashes` mode enabled for collection | sha256 compared instead of mtime; slower but reliable on NFS |
| State file has no mtime for a file | Treated as new; file ingested |

#### Phase Scope
**In scope**:
- Add `file_mtimes: dict[str, float]` (path → mtime) per collection to state file
- `RagCollectionSync.sync()` — compare current mtime against stored value; skip unchanged; re-ingest changed
- Deletion detection: files in `processed_paths` but no longer on disk → remove chunks from LanceDB
- Mtime update in state after each successful ingest
- Embedding model change detection: if `config.rag.embedding_model` differs from `CollectionMeta.embedding_model`, invalidate all mtimes and run full re-index
- Opt-in `file_hashes: dict[str, str]` per collection via config flag

**Out of scope**:
- Directory-level change detection — file-level is sufficient
- Tracking renamed files (rename = delete + add)
- Content-addressable deduplication across collections — not planned
- Chunk size change behaviour is configurable via `[rag] auto_reindex_on_chunk_size_change`:
  - `false` (default) — warn in `archon doctor` and `archon rag status` (`⚠️ chunk size mismatch: indexed 512, config 256`); user triggers re-index manually via `archon rag reindex <collection>`
  - `true` — auto-invalidate all mtimes and force a full re-index, same as embedding model change

**`sync()` algorithm restructuring**: This is the largest code change in the feature. Currently, `sync()` puts existing collections in `unchanged` (skipped entirely). Phase 4 requires changing `unchanged` → `to_check`: existing collections must be scanned for file changes (new, modified, deleted). The `to_add`/`unchanged`/`to_remove` logic becomes `to_add`/`to_check`/`to_remove`, where `to_check` runs mtime/hash comparison per file.

**Changes**:
- Add `file_mtimes: dict[str, float]` (path → mtime) per collection to state file
- Restructure `RagCollectionSync.sync()` — existing collections enter a `to_check` path that compares current mtime against stored value; skip unchanged files; re-ingest changed; remove deleted
- On successful ingest, update stored mtime for that file
- Opt-in `file_hashes: dict[str, str]` (sha256) for filesystems with unreliable mtimes — configurable per collection

---

### Phase 5 — Telegram notification on completion/failure ✅ Done

**Delivers**: User gets notified in Telegram when background indexing finishes or fails — no polling needed.

#### Use Cases
- **Install + walk away**: User enables RAG, closes the terminal, and gets a Telegram message when all collections are ready.
- **`archon update` with large collections**: Update triggers a re-index; user is in Telegram already and gets notified when it's done.
- **Partial failure**: One collection fails; user gets a notification with which collection failed, not a silent log entry.
- **Manual sync** (`archon rag sync`): Notification intentionally suppressed — user is watching the terminal already.

#### Scenarios

| Scenario | Expected behaviour |
|---|---|
| All collections index successfully | Telegram: _"✅ RAG indexing complete — all 3 collections ready."_ |
| One collection fails | Telegram: _"⚠️ RAG indexing finished — `old-notes` failed. Run `archon rag status` for details."_ |
| All collections fail | Telegram: _"❌ RAG indexing failed — no collections are ready. Run `archon rag status` for details."_ |
| Triggered by `archon rag sync` (manual) | No notification sent |
| Triggered by install or update | Notification sent on completion |
| Daemon not connected to Telegram | Notification silently skipped; no error |

#### Phase Scope
**In scope**:
- Main daemon polls state file for `done`/`failed` terminal transitions and sends notification via `ArchonToolkit.send_notification()`
- Three notification states: full success, partial failure, total failure
- Notification only for install/update-triggered syncs (not manual `archon rag sync`)
- Notification content includes collection name(s) on failure

**Delivery mechanism**: The RAG server runs as a separate process — `sync()` cannot directly call `ArchonToolkit.send_notification()`. Instead, the main daemon periodically reads the state file and detects terminal state transitions (`in_progress` → `done`/`failed`). When all collections reach a terminal state after an install/update trigger, the daemon sends a single summary notification. A `trigger` field in the state file root (`"install"` | `"update"` | `"manual"` | `null`) tells the daemon whether to notify.

**Out of scope**:
- Per-collection notifications (one per collection) — too noisy; one summary message only
- Notification mode gating: notification respects the current `notifications.mode` — suppressed in `quiet` mode, sent in `normal`/`verbose`/`debug`
- Retry notification if Telegram delivery fails — not planned
- Progress notifications mid-index ("50% done") — Phase 7 covers ETA in status; push notifications are summary-only

**Changes**:
- Add `trigger` field to state file root (`"install"` | `"update"` | `"manual"` | `null`)
- Main daemon: periodic state file check (e.g. every 30s) — detect all-terminal-state transition; send notification; clear trigger
- Success: _"✅ RAG indexing complete — all N collections ready."_
- Partial failure: _"⚠️ RAG indexing finished — 2 collections failed. Run `archon rag status` for details."_
- No notification for `trigger: "manual"` or `trigger: null`

---

### Phase 6 — `archon doctor` integration

**Delivers**: `archon doctor` shows real-time indexing status per collection instead of a binary staleness check.

#### Use Cases
- **Post-install health check**: User runs `archon doctor` after install; sees which collections are still indexing, which are ready, which failed.
- **Diagnosing a slow or stuck sync**: Doctor output shows if a collection is stuck `in_progress` for an unexpectedly long time.
- **After a crash or restart**: Doctor shows `partial` for collections that were mid-index when the service went down.
- **Model mismatch after config change**: Doctor detects indexed model differs from configured model and surfaces it explicitly.

#### Scenarios

| Scenario | Expected behaviour |
|---|---|
| Collection fully indexed | `✅ sessions — done (340 docs)` |
| Collection in progress | `⏳ my-project — in_progress (87/120 files)` |
| Collection partial (service restarted mid-index) | `⚠️ docs — partial (48/120 files)` |
| Collection failed | `❌ old-notes — failed: parse error on report.pdf` |
| Collection stale (>7 days, no active sync) | `⚠️ sessions — stale (last indexed 12 days ago)` |
| Embedding model mismatch | `⚠️ my-project — model mismatch (indexed: bge-small, config: bge-base)` |
| State file missing | Doctor falls back to `CollectionMeta` staleness check only |

#### Phase Scope
**In scope**:
- `_check_rag_health()` in `doctor.py` — read state file alongside `CollectionMeta`; merge into per-collection status
- Replace static `> 7 days` staleness check with multi-state output: `done`, `in_progress`, `partial`, `failed`, `stale`
- Surface per-collection error messages from state file
- Preserve existing model mismatch and empty collection checks

**Out of scope**:
- `archon doctor --json` structured output — not planned in this phase; doctor is CLI-only
- Doctor watching state file for live updates (`--watch` mode) — not planned
- Doctor auto-fixing issues (e.g. triggering re-index on stale) — doctor is read-only

**Changes**:
- `_check_rag_health()` in `doctor.py` — read state file alongside `CollectionMeta`
- Replace the static `> 7 days` staleness check with: `in_progress`, `partial`, `failed`, or `stale (last indexed N days ago)`
- Surface per-collection errors directly in doctor output for fast diagnosis

---

### Phase 7 — ETA in status output ✅ Done

**Delivers**: `archon rag status` shows estimated time remaining for in-progress collections.

#### Use Cases
- **Deciding whether to wait or come back later**: User sees `~2 min remaining` and waits; sees `~45 min remaining` and closes the terminal.
- **Monitoring a large initial index**: User checks status periodically and sees the estimate converge as indexing progresses.
- **Claude answering "how long until RAG is ready?"**: `rag_status` MCP response includes ETA; Claude gives a concrete answer in Telegram.

#### Scenarios

| Scenario | Expected behaviour |
|---|---|
| 10+ files processed, sync in progress | ETA shown: `~3 min remaining` |
| Fewer than 10 files processed | ETA suppressed — too early to be reliable |
| Single-file collection | ETA not shown |
| ETA < 10 seconds | Shows `< 1 min remaining` |
| Files vary wildly in parse time (PDF vs md) | ETA is best-effort; no confidence interval shown |

#### Phase Scope
**In scope**:
- Compute `files_per_second = processed_files / (now - started_at).total_seconds()`
- Compute `eta_seconds = (total_files - processed_files) / files_per_second`
- Show in `archon rag status` as `~N min remaining` (rounded to nearest minute)
- Include `eta_seconds` field in `rag_status` MCP JSON response
- Suppress ETA when fewer than 10 files processed

**Out of scope**:
- Per-file timing (some files take 10× longer — not tracked at this level)
- Confidence intervals or uncertainty ranges
- Historical ETA accuracy tracking across runs
- ETA in `archon doctor` — doctor is a snapshot check, not a progress monitor

**Changes**:
- Compute `files_per_second = processed_files / elapsed_seconds` from `started_at`
- Show `~3 min remaining` in `archon rag status` and `rag_status` MCP response
- Only displayed when at least 10 files have been processed (avoids noisy early estimates)

---

### Phase 8 — Watch mode

**Delivers**: File changes in collection source directories trigger incremental re-indexing automatically. No manual sync needed.

#### Use Cases
- **Active project directory**: User adds a new file; it appears in RAG search within seconds without running `archon rag sync`.
- **`sessions` collection (history files)**: History files are written daily; watch mode picks them up automatically.
- **Documentation under active editing**: User edits a markdown file; the updated content is searchable immediately.
- **Opting out**: User with a 100k-file collection does not want background CPU usage; watch is `false` by default.
- **Manual sync during watch**: User runs `archon rag sync`; it does not conflict with an active watcher.

#### Scenarios

| Scenario | Expected behaviour |
|---|---|
| New file added to watched directory | Debounced 5s; file ingested; state updated |
| Existing file modified | Debounced 5s; old chunks removed; file re-ingested |
| File deleted | Chunks removed from LanceDB; path cleared from state |
| Rapid successive writes to same file (log append) | Debounce absorbs all writes; one ingest triggered |
| `archon rag sync` runs while watch is active | Sync queued; watch-triggered task waits; no conflict |
| `[rag] watch = false` (default) | No watcher started; no background CPU usage |
| Service restart | Watcher re-initialises for all watched collections on startup |
| Directory removed or unmounted | Watcher logs warning; stops watching that directory; no crash |
| Subdirectory added to watched tree | Watcher detects it and begins watching recursively |

#### Phase Scope
**In scope**:
- `archon/rag/watcher.py` (new) — wraps `watchdog` (FSEvents on macOS, inotify on Linux, ReadDirectoryChangesW on Windows)
- One watcher per collection directory; started alongside the RAG service when `[rag] watch = true`
- Debounce: 5s window per file path before triggering ingest
- Per-collection sync lock: only one sync per collection at a time (watch-triggered or manual)
- Integration with Phase 4 change detection: watcher uses the same mtime/hash comparison before re-ingesting
- `[rag] watch = true` in `config.toml` (default `false`)
- `archon rag status` shows `watching` indicator for collections with active watchers

**Out of scope**:
- Watch mode on Windows — `watchdog` supports it but requires separate testing; stub only in this phase
- Watching remote (NFS/SMB) directories — explicitly unsupported; local directories only
- Configurable debounce interval per collection — 5s is fixed in this phase
- Real-time push to Telegram on each file change — too noisy; status is queryable, not pushed per-file

**Changes**:
- New `archon/rag/watcher.py` — wraps `watchdog` (FSEvents/inotify/ReadDirectoryChangesW) per collection directory
- On file change: queue incremental re-index for the affected collection only; debounce 5s to batch rapid changes
- `[rag] watch = true` in `config.toml` (default `false`) to opt in
- Watch-triggered sync and manual `archon rag sync` share the same lock — only one sync per collection at a time
- Most valuable for `sessions` (history files written daily) and active project directories

---

## State Schema Evolution

The schema is additive across phases — earlier readers ignore unknown fields:

| Field | Added in |
|---|---|
| `status`, `total_files`, `processed_files`, `started_at`, `completed_at`, `error`, `error_count` | Phase 1 |
| `processed_paths` | Phase 3 |
| `file_mtimes`, `file_hashes` | Phase 4 |
| `trigger` (root-level) | Phase 5 |

**State file size budget**: For a 10,000-file collection, `processed_paths` ≈ 1MB (100 bytes/path). With `file_mtimes` added in Phase 4, this doubles. Target: state file should stay under 10MB total across all collections. For collections exceeding 10k files, consider storing path hashes (truncated sha256) instead of full paths to reduce size ~6×.

---

## Key Decisions

- **Dedicated JSON state file, not LanceDB**: `CollectionMeta` in LanceDB stores final, clean state. The state file holds in-progress scratch data (Phases 1–3) and becomes **semi-persistent** from Phase 4 onward (it holds `file_mtimes`/`file_hashes` — the primary mechanism for incremental sync). Deleting the state file is safe but causes a full re-index on next sync. Mixing partial ingest state into LanceDB risks inconsistency and requires schema migration.
- **Fire-and-forget install**: A progress bar at install time is a prettier version of the same blocking problem. Non-blocking means non-blocking.
- **Atomic writes**: State file updates use `write + rename` (tmpfile swap) to prevent corrupt reads if the service crashes mid-write. Note: `os.replace()` is atomic on POSIX but not guaranteed atomic on Windows when the target is open by another reader. On Windows, wrap reads in a retry loop with brief backoff.
- **Additive schema**: Each phase extends the state file non-destructively. Phase 1 code is never broken by Phase 3 additions.
- **Partial readiness from Phase 2**: A collection with any indexed documents should be usable. Blocking search on 100% completion creates unnecessary downtime.
- **Pinned-first ordering in Phase 2**: One sort line — highest impact-to-effort ratio in the entire feature.
- **`watchdog` for Phase 8**: Cross-platform, well-maintained, already used in similar daemons. Avoids platform-specific polling.
- **`auto_reindex_on_chunk_size_change` (Phase 4)**: Chunk size change degrades search quality but does not break vector space compatibility (unlike embedding model change). Default `false` warns without forcing a potentially hours-long re-index. Set `true` for strict consistency — mirrors the auto-invalidation already done on model change.

## Edge Cases & Constraints

- **Service crash mid-index**: Status stays `in_progress`. On next startup, `sync()` resets stale `in_progress` entries to `pending` before restarting. No "forever in_progress" state.
- **State file missing or corrupt**: Treat as all-unknown. `rag status` falls back to `CollectionMeta` only. Never crash on missing or unparseable state.
- **Collection removed from config while in_progress**: `RagCollectionSync` already drops unmanaged collections; state file entry is cleared at the same time.
- **Concurrent sync calls**: Although the server is single-threaded asyncio, concurrent sync tasks can arise: (1) `server.py` timeout fallback creates a new `asyncio.create_task` while the timed-out task may still be running cooperatively; (2) MCP `rag_sync` can overlap with startup sync. A per-collection `asyncio.Lock` in `RagCollectionSync` prevents concurrent state file writes and LanceDB conflicts.
- **`sync_timeout_seconds > 0`**: Server times out and falls back to `asyncio.create_task`. State file behavior is identical regardless of trigger.
- **install.py non-interactive mode**: Same fire-and-forget behavior; status hint printed to stdout.
- **`archon rag status` exit code**: Exit non-zero if any collection is `failed` — supports scripting and CI use.
- **Phase 3+ `archon rag reindex`**: `reindex` must clear **all** per-collection state — `processed_paths` (Phase 3), `file_mtimes`, `file_hashes` (Phase 4) — before starting, or it would immediately skip all files. Each phase that adds per-collection state must register its fields for cleanup.
- **Phase 4 mtime reliability**: Networked filesystems and some containers report stale mtimes. Hash fallback is opt-in per collection, not global.
- **Phase 8 debounce**: Rapid successive writes (e.g. a log file being appended) must be batched — 5s debounce before triggering sync.

## Open Questions

None.

## Implementation Order

| Priority | Phase | Task |
|---|---|---|
| 1 | 1 | `IndexingStateStore` — state file read/write with atomic swap |
| 2 | 1 | Per-collection `asyncio.Lock` in `RagCollectionSync` to prevent concurrent sync |
| 3 | 1 | `RagCollectionSync.sync()` — write state before/during(batched)/after each collection |
| 4 | 1 | `install.py` — add status hint message on exit |
| 5 | 1 | `archon rag status` — display progress from state file |
| 6 | 1 | `rag_status` MCP tool — add progress fields to JSON response |
| 7 | 2 | Pinned collections sorted first in sync |
| 8 | 2 | Partial readiness — `partial` status in health checks and `rag_status` |
| 9 | 3 | Resumable indexing — track `processed_paths`, skip on restart |
| 10 | 4 | Restructure `sync()` algorithm (`unchanged` → `to_check`) for change detection |
| 11 | 4 | File-level change detection — `file_mtimes` diff, only re-ingest changed files |
| 12 | 5 | Telegram notification — daemon polls state file for terminal transitions |
| 13 | 6 | `archon doctor` integration — real-time status replaces staleness check |
| 14 | 7 | ETA calculation — files/second → estimated time remaining |
| 15 | 8 | Watch mode — `watchdog` incremental re-index on file change |
