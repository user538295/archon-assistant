# FEAT-021 — RAG Declarative Multi-Collection Management
**Purpose**: Let operators declare a list of directory paths in `config.toml [rag] collections` and have Archon automatically keep LanceDB in sync — ingesting added paths, dropping removed ones.
**Audience**: Archon operators who want to index custom document directories alongside conversation history.
**Status**: To Do

---

## Background

The RAG pipeline already supports arbitrary named collections — `RagPipeline.ingest_directory(path, collection)` creates a LanceDB table on first use. However, there is no config-driven way to declare which directories to index: operators must run `archon rag ingest` manually, stop/start the service themselves, and there is no mechanism to remove a collection when a path is no longer wanted.

The desired model is declarative: the config file is the source of truth. Collections that appear in the config are created and indexed; collections that disappear are dropped. This mirrors how modern config-driven systems (e.g., systemd, Kubernetes) reconcile desired vs actual state.

## Goal

Operators add directory paths to `collections` in `config.toml`. On every service start (and on `archon rag sync`), Archon computes the desired set of collections from that list, compares it against LanceDB's actual state, ingests new directories, and drops removed ones. Default collections cover history and workspace — no manual setup required after a fresh install.

---

## Scope

### In Scope
- New `collections: list[str]` field in `RagConfig`, default `["~/.archon/history/sessions", "~/.archon/workspace"]`
- `path_to_collection_name(path: str) -> str` — deterministic, collision-safe name derivation
- `RagCollectionSync` class — reconciles config list vs LanceDB state; produces `SyncResult`
- `RagStore.drop_collection(name: str)` — drops a LanceDB table
- `RagStore.rename_collection(old: str, new: str)` — renames a LanceDB table (used by migration step in `sync()`)
- `server.py:main()` calls sync before accepting connections
- `archon rag sync` CLI command — manual full reconciliation
- `archon rag collection list` — shows all collections with source path, doc count, chunk count, and indexed status
- `archon rag collection add <path>` — appends path to config, immediately ingests
- `archon rag collection remove <path>` — removes path from config, immediately drops collection if service is stopped (warns otherwise)
- `archon rag help` — print `archon rag` subcommand help
- `archon rag collection help` — print `archon rag collection` subcommand help
- `history_collection` derived automatically from the history-sessions path instead of being user-editable
- Updated `rag_guide.md` with declarative collections and CLI management sections
- `archon rag install` bootstraps the default collections (replaces `create_history_collection`)

### Out of Scope
- File-watch based live re-indexing (sync runs at startup and on explicit command only)
- Per-collection configuration (chunk size, model) — all share `[rag]` config
- User-facing collection renaming CLI (e.g., `archon rag collection rename`) — internal rename via `RagStore.rename_collection()` is used only for the `archon-history` → `sessions` migration
- Incremental re-index (changed files) — sync always re-ingests the full directory
- Access control per collection
- `archon rag ingest` with a manual `--collection` flag (still works unchanged for one-off use)

---

## Acceptance criteria
- [x] `RagConfig` has `collections: list[str]` with default `["~/.archon/history/sessions", "~/.archon/workspace"]`
- [x] `path_to_collection_name` converts any path to a valid, unique LanceDB table name
- [x] `RagCollectionSync.sync()` ingests paths in config not yet in LanceDB
- [x] `RagCollectionSync.sync()` drops LanceDB collections whose paths are no longer in config
- [x] `server.py:main()` runs sync before the HTTP server starts accepting connections
- [x] `archon rag sync` runs sync and prints added/removed/unchanged counts
- [x] `archon rag collection list` shows each collection with source path, doc/chunk counts, and indexed/orphan(managed)/unmanaged status
- [x] `archon rag collection add <path>` appends path to `config.toml [rag] collections`, immediately ingests, prints confirmation
- [x] `archon rag collection add <path>` on a path already in config prints "already registered" and exits 0
- [x] `archon rag collection remove <path>` removes path from config, drops collection if service is stopped; warns and requires `--force` if service is running
- [x] `archon rag collection remove <path>` on a path not in config prints error and exits 1
- [x] `archon rag help` prints `archon rag` subcommand listing and exits 0
- [x] `archon rag collection help` prints collection subcommand listing and exits 0
- [x] `history_collection` is auto-derived as `path_to_collection_name(config.history.directory + "/sessions")` — no longer a user-editable field
- [x] If `config.toml` contains `[rag] history_collection`, a WARNING is logged and a Telegram notification is sent at startup; the key is ignored
- [x] Removing a path from `collections` and restarting the service (or running `archon rag sync`) drops its LanceDB table
- [x] Adding a path to `collections` and running `archon rag sync` ingests the directory
- [x] On first sync after upgrade, if `archon-history` table exists and `sessions` does not, `archon-history` is renamed to `sessions` (not dropped)
- [x] Sync does not drop collections not managed by sync (e.g., created via `archon rag ingest --collection` flag)
- [x] `SyncResult` includes a `skipped` field listing unmanaged collections that were not touched
- [x] `archon rag collection add <path>` prints a warning if the RAG service is running
- [x] Sync skips non-existent paths with a WARNING log and records them in `SyncResult.errors`
- [x] Setting `collections = []` and running sync drops only previously managed collections, not manually-created ones
- [x] Service starts successfully even if startup sync times out; sync continues in a background task
- [x] `sync_timeout_seconds` (default 30) is configurable in `[rag]` config
- [x] Setting `sync_timeout_seconds = 0` runs sync entirely as a background task without blocking startup (no `asyncio.wait_for` is used)
- [x] All existing tests pass; new tests cover sync, drop, derivation, migration, manifest tracking, timeout, and all CLI actions

---

## What does NOT change
- `RagPipeline.ingest_directory()` and `RagPipeline.search()` — collection logic unchanged
- `server.py` MCP tools (`search`, `ingest_file`, etc.) — accept `collection` parameter unchanged
- `archon rag ingest` CLI command — still works for manual one-off ingests
- CUDA / Apple Silicon GPU paths — separate feature (FEAT-020)

---

## Known limitations / accepted trade-offs
- Sync always re-ingests the full directory (no incremental update). For large corpora this is slow. Accepted: incremental re-index is a future enhancement.
- `archon rag sync` must be run (or service restarted) for config changes to take effect. There is no file-watch mechanism.
- `history_collection` removal is a breaking config change for users who set it explicitly. Migration note in docs: remove the key and let it be derived.
- Sync only removes collections that it previously created (tracked via a manifest file). Collections created via `archon rag ingest --collection` are never dropped by sync. This is by design.
- Startup sync runs with a configurable timeout (default 30 s). On timeout, the server starts with partial sync results and continues ingest in the background. Searches during background ingest may return partial results.
- On startup sync timeout, a full re-sync is initiated in the background (not a continuation of the timed-out work). For very large collections, this means up to 2× ingest work if the timeout fires mid-ingest.

---

## Migration

### Existing `archon-history` collection rename

Existing users may have `archon-history` as their LanceDB history collection (the old default, created by `create_history_collection()`). The new derivation `path_to_collection_name("~/.archon/history/sessions")` = `"sessions"`. Without a migration step, `archon-history` would appear as an orphan on first sync and be silently dropped — this is irreversible data loss.

**Migration algorithm** (executed once, at the start of `RagCollectionSync.sync()`, before any add/remove logic):

1. Check whether `archon-history` exists as a LanceDB table.
2. If `archon-history` exists **and** `sessions` does not: rename `archon-history` → `sessions` using `await self._pipeline.store.rename_collection("archon-history", "sessions")` (which calls `self._db.rename_table` internally; or, if `rename_table` is not available in the installed LanceDB version, copy-ingest + drop as a fallback).
3. If both `archon-history` **and** `sessions` exist: log a WARNING — the user has two copies; migration is skipped and the user must resolve the conflict manually.
4. Log an INFO message whenever migration runs: `"Migrated LanceDB table archon-history → sessions"`.
5. If the manifest exists and contains an entry for `archon-history`, update it: replace the `archon-history` key with `sessions` (keeping the same source path). This ensures the manifest stays consistent with the renamed LanceDB table.

**Config migration** (at `load_config()` time): if the loaded TOML contains a `history_collection` key under `[rag]`, log a WARNING and emit a Telegram notification: `"config.toml [rag] history_collection is no longer supported and is being ignored. Remove this key to silence this warning."` The key is silently discarded; the derived name is used instead.

**Manual migration fallback** (for operators who cannot upgrade in place): run `archon rag sync` once after upgrading. The migration detection in `sync()` will rename the table automatically. No data is lost.

---

## Architecture

### `path_to_collection_name(path: str) -> str`

New module-level function in `archon/rag/sync.py`.

Derivation rule:
1. Expand `~`, resolve to absolute path
2. Use the last path component (directory name) as the collection name
3. Sanitize: lowercase, replace non-alphanumeric with `_`, collapse multiple `_`, strip leading/trailing `_`

**This function is collision-unaware by design.** It always returns the same name for a given path. Collision resolution is applied in `RagCollectionSync.sync()`, which tracks a global `{name: path}` mapping across all configured paths and modifies derived names only when conflicts are detected.

```python
def path_to_collection_name(path: str) -> str:
    resolved = Path(path).expanduser().resolve()
    name = resolved.name.lower()
    import re
    name = re.sub(r"[^a-z0-9]+", "_", name).strip("_")
    return name or "collection"
```

**`history_collection` derivation note**: `history_collection` is derived from a fixed default path (`~/.archon/history/sessions`), which is always the first item in the default `collections` list. It will therefore always use the basename `sessions` without collision resolution interference. However, if a user adds another collection whose path also ends in `sessions`, collision resolution in `sync()` will rename the OTHER collection (the later one in the list), not the history one — since the history path is processed first.

**Collision resolution in `sync()`**: when two paths produce the same name, prepend the parent path component and try again. For example, `/a/history/sessions` and `/b/project/sessions` both derive `sessions`; after prepending parent: `history_sessions` and `project_sessions`. If the conflict persists after prepending one parent level (both parent+basename also match), continue prepending ancestor components up to the full path depth. If still not unique (extremely unlikely, e.g. identical absolute paths), append a short hash as final tiebreaker: `<name>_<sha1_of_path[:6]>`.

### `RagConfig` changes in `archon/config/loader.py`

```python
@dataclass
class RagConfig:
    enabled: bool = False
    host: str = "localhost"
    port: int = 8282
    db_path: str = "~/.archon/rag"
    # history_collection removed — derived via path_to_collection_name
    collections: list[str] = field(default_factory=lambda: [
        "~/.archon/history/sessions",
        "~/.archon/workspace",
    ])
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    providers: list[str] = field(default_factory=list)
    top_k_retrieve: int = 20
    top_k_return: int = 5
    chunk_size: int = 512
    sync_timeout_seconds: int = 30  # startup sync timeout; 0 = skip wait_for entirely and run sync as background task immediately (no partial-sync race)
```

`history_collection` is removed. Callers that needed `cfg.rag.history_collection` (gateway, server, context_provider) will call `path_to_collection_name(cfg.history.directory + "/sessions")` instead. Since `RagConfig` does not know about `HistoryConfig`, this derivation happens at the call sites in `gateway.py` and `server.py`.

**`RagPipeline` constructor change**: the existing `RagPipeline.__init__` and `create_pipeline` factory both accept `history_collection` as a parameter (read from `cfg.rag.history_collection`). Removing `history_collection` from `RagConfig` will cause `AttributeError` at all call sites that use `cfg.rag.history_collection`. As part of Task 2.2, `RagPipeline.__init__` and `create_pipeline` must have the `history_collection` parameter removed; all call sites in `server.py` and `gateway.py` must derive the history collection name via `path_to_collection_name(...)` instead.

### `RagStore.drop_collection(name: str) -> None` in `archon/rag/store.py`

`RagStore` uses `lancedb.connect_async()` which returns an `AsyncConnection`. Its `list_tables()` and `drop_table()` are native async methods — no `asyncio.to_thread()` wrapper needed.

```python
async def drop_collection(self, name: str) -> None:
    names = (await self._db.list_tables()).tables  # list_tables() returns ListTablesResponse with .tables: list[str]
    if name not in names:
        raise KeyError(name)
    await self._db.drop_table(name)
```

### `RagStore.rename_collection(old: str, new: str) -> None` in `archon/rag/store.py`

```python
async def rename_collection(self, old: str, new: str) -> None:
    names = (await self._db.list_tables()).tables  # list_tables() returns ListTablesResponse with .tables: list[str]
    if old not in names:
        raise KeyError(old)
    try:
        await self._db.rename_table(old, new)
    except AttributeError:
        # Fallback for LanceDB versions without rename_table:
        # re-ingest is handled at the caller (RagCollectionSync migration step)
        raise NotImplementedError("rename_table not available; use copy-ingest + drop")
```

This method encapsulates the LanceDB rename operation so that `RagCollectionSync` (which holds a `RagPipeline`, not a raw db connection) can call it without violating encapsulation.

If `NotImplementedError` is raised, the migration step falls back to treating the existing `archon-history` table as unmanaged (it remains in LanceDB, the new `sessions` collection is freshly ingested from the history directory, and the user is warned to manually drop `archon-history` once migration is confirmed successful).

### `SyncResult` and `RagCollectionSync` in `archon/rag/sync.py` (new file)

```python
@dataclass
class SyncResult:
    added: list[str]      # collection names ingested
    removed: list[str]    # collection names dropped
    unchanged: list[str]  # collection names already present
    errors: list[str]     # paths/names that failed (with descriptive message); accumulates both ingest failures and drop failures, including KeyError for phantom manifest entries
    skipped: list[str]    # unmanaged collections not touched by sync

class RagCollectionSync:
    def __init__(self, pipeline: RagPipeline) -> None: ...

    async def sync(self, collections: list[str]) -> SyncResult:
        """Reconcile config paths with LanceDB state."""
        # 0. Run one-time migration: rename archon-history → sessions if needed
        # 1. Build desired: {collection_name: path} with collision resolution
        # 2. Get existing: set of collection names from store.list_collections()
        # 3. Load manifest: {collection_name: source_path} from sync_manifest.json
        # 4. To remove: (existing ∩ managed_names) - desired.keys()
        #    where managed_names = set of names recorded in the manifest
        #    Wrap each drop_collection(name) call in try/except KeyError:
        #      on KeyError, log WARNING "Collection {name} in manifest but not in LanceDB; skipping drop"
        #      and record in SyncResult.errors
        # 5. To add: desired.keys() - existing
        # 6. Drop removed, ingest added, report unchanged; update manifest
        # 7. Skipped: existing collections NOT in manifest (unmanaged — never touched)
```

**Manifest file**: `RagCollectionSync` maintains a manifest at `{db_path}/sync_manifest.json` (where `db_path` is `Path(cfg.rag.db_path).expanduser()`) recording `{collection_name: source_path}` for every sync-managed collection. The manifest is always co-located with the LanceDB data directory so that moving `db_path` keeps the manifest and database in sync. On each sync:
- Collections that appear in the manifest but are no longer in config are dropped.
- Collections not in the manifest (created manually via `archon rag ingest --collection`) are never dropped by sync. They appear in `SyncResult.skipped`.
- After sync completes, the manifest is updated to reflect the new desired state.

**Non-existent paths**: paths configured in `collections` that do not exist on disk at sync time are skipped with a WARNING log. They are not treated as an error that stops sync. They appear in `SyncResult.errors` with the message `"path does not exist: <path>"`. Paths that exist but are empty directories are ingested as empty collections (the LanceDB table is created but contains zero rows).

**Empty `collections` list**: if `collections = []`, `sync()` drops all sync-managed collections (those in the manifest) and returns them in `SyncResult.removed`. Collections not in the manifest are never touched.

### Service startup: `server.py:main()`

After `pipeline.store.connect()` and before `app.run_http_async(...)`, sync runs with a configurable timeout. Large directories could otherwise block startup for an extended period and risk launchd/systemd killing the process.

```python
from archon.rag.sync import RagCollectionSync
sync = RagCollectionSync(pipeline)
sync_timeout = cfg.rag.sync_timeout_seconds  # default: 30
if sync_timeout == 0:
    # Run sync entirely in background (don't wait at all)
    asyncio.create_task(sync.sync(cfg.rag.collections))
    logger.info("RAG sync: running in background (sync_timeout_seconds=0)")
else:
    try:
        result = await asyncio.wait_for(
            sync.sync(cfg.rag.collections),
            timeout=sync_timeout,
        )
        logger.info("RAG sync: added=%s removed=%s unchanged=%s errors=%s skipped=%s",
                    result.added, result.removed, result.unchanged, result.errors, result.skipped)
    except asyncio.TimeoutError:
        logger.warning("RAG sync timed out after %ds; restarting sync in background", sync_timeout)
        asyncio.create_task(sync.sync(cfg.rag.collections))
```

**Timeout configuration**: add `sync_timeout_seconds: int = 30` to `RagConfig`. Setting `sync_timeout_seconds = 0` skips `asyncio.wait_for` entirely and creates a background task immediately — this avoids the partial-sync race that would occur if `asyncio.wait_for(coro, timeout=0)` were used (which would immediately cancel the coroutine and raise `TimeoutError`, resulting in a double-sync). During background ingest after a timeout or `0` setting, searches may return partial results — this is documented behaviour.

**`history_collection` derivation note** (call-site reminder): because `path_to_collection_name` is collision-unaware and is called independently in `gateway.py` and `server.py`, the history collection name used for searching must match the name used during ingest. Since the history path is always the first `collections` entry and collision resolution is stable for a given config, the derived names will match. If a user has a custom `collections` list that causes collision resolution to rename `sessions`, both gateway and server must derive the name through `sync()`'s resolved mapping — not by calling `path_to_collection_name` in isolation. This is a known design tension; it is acceptable for the MVP since the history path is fixed and always collision-free.

### `history_collection` derivation

In `gateway.py` (where `rag_url` is built) and `server.py` (where `create_app` is called), replace `cfg.rag.history_collection` with:

```python
from archon.rag.sync import path_to_collection_name
history_col = path_to_collection_name(str(Path(cfg.history.directory).expanduser() / "sessions"))
```

### CLI: `archon rag sync`

New action in `archon/cli/rag_cmd.py`:

```python
def _run_sync(args: Namespace) -> int:
    # Warn if service is running (write conflicts possible), but do not block
    pipeline = create_pipeline(cfg.rag)
    result = asyncio.run(_do_sync(pipeline, cfg.rag.collections))
    print(f"Sync complete: {len(result.added)} added, {len(result.removed)} removed, "
          f"{len(result.unchanged)} unchanged, {len(result.errors)} errors.")
    return 0 if not result.errors else 1
```

### CLI: `archon rag collection list`

```python
def _run_collection_list(args: Namespace) -> int:
    # Open store, list_collections(), cross-reference with cfg.rag.collections and the manifest
    # Three states:
    #   indexed        — in config AND in LanceDB (managed by sync)
    #   orphan (managed) — in manifest, NOT in config; will be removed on next sync
    #   unmanaged      — in LanceDB but NOT in manifest (created via `archon rag ingest --collection`); will NOT be touched by sync
    # Print: name | path | docs=N | chunks=M | status
```

### CLI: `archon rag collection add <path>`

```python
def _run_collection_add(args: Namespace) -> int:
    path = str(Path(args.path).expanduser().resolve())
    cfg = load_config()
    if path in [str(Path(p).expanduser().resolve()) for p in cfg.rag.collections]:
        print(f"Already registered: {args.path}")
        return 0
    svc_running = get_rag_service().status().running
    if svc_running:
        print("Warning: RAG service is running. The collection will be indexed "
              "but opening a second LanceDB connection may cause write conflicts. "
              "Consider stopping the service first: archon rag stop")
    # Write to config.toml using tomlkit (same pattern as configure_providers)
    _config_collections_append(config_path, args.path)
    pipeline = create_pipeline(load_config().rag)
    # Derive collection name: use manifest if path was previously synced, else naive derivation
    # The name may be updated (collision-resolved) on the next archon rag sync
    col_name = _manifest_lookup_by_path(manifest_path, path) or path_to_collection_name(path)
    asyncio.run(_ingest_one(pipeline, path, col_name))
    print(f"Collection added and indexed: {args.path}")
    print("Run 'archon rag stop && archon rag start' for the service to start serving it.")
    return 0
```

**NOTE — collision-aware name handling for `collection add`**: The collection name used for immediate ingest is derived via `path_to_collection_name(path)` (collision-unaware). If a collision exists with an already-registered collection, the immediately-ingested collection will use the naive name. The collision will be detected and resolved on the next sync, which may rename or re-ingest the collection. If the manifest already contains an entry for the same path (e.g., from a previous sync), use the existing name from the manifest; otherwise derive via `path_to_collection_name` (the name will be collision-checked during the next sync). For correctness, users with collision-prone path names should prefer editing `config.toml` directly and running `archon rag sync` instead of using `collection add`.

### CLI: `archon rag collection remove <path>`

```python
def _run_collection_remove(args: Namespace) -> int:
    path = str(Path(args.path).expanduser().resolve())
    cfg = load_config()
    normalized = [str(Path(p).expanduser().resolve()) for p in cfg.rag.collections]
    if path not in normalized:
        print(f"Error: path not in collections: {args.path}")
        return 1
    svc_running = get_rag_service().status().running
    if svc_running and not args.force:
        print("Error: RAG service is running. Stop it first or use --force.")
        print("  archon rag stop && archon rag start")
        return 1
    if svc_running:
        print("Warning: removing collection while service is running.")
    _config_collections_remove(config_path, args.path)
    # Look up the actual (collision-resolved) name from the manifest, not re-derive it
    col_name = _manifest_lookup_by_path(manifest_path, path)
    if col_name is None:
        # Path not yet synced — fall back to naive derivation
        col_name = path_to_collection_name(path)
    pipeline = create_pipeline(load_config().rag)
    try:
        asyncio.run(_drop_one(pipeline, col_name))
    except KeyError:
        pass  # already gone
    print(f"Collection removed: {args.path}")
    return 0
```

`_manifest_lookup_by_path(manifest_path: Path, resolved_path: str) -> str | None` reads the manifest JSON (`{collection_name: source_path}`) and returns the collection name whose value matches `resolved_path`, or `None` if no entry is found. (defined in `archon/rag/sync.py` alongside other manifest utilities)

```python
def _manifest_lookup_by_path(manifest_path: Path, resolved_path: str) -> str | None:
    """Return the collection name for the given resolved path, or None if not in manifest."""
    if not manifest_path.exists():
        return None
    import json
    manifest: dict[str, str] = json.loads(manifest_path.read_text())
    for col_name, src_path in manifest.items():
        if str(Path(src_path).expanduser().resolve()) == resolved_path:
            return col_name
    return None
```

### Config list writer: `_config_collections_append` / `_config_collections_remove`

Private helpers in `archon/cli/rag_cmd.py`. These use tomlkit's native `Array` operations to preserve inline comments and multi-line formatting (per ADR-08). They also follow the atomic write pattern from `config_rw.py` (temp file + `os.replace()`) to prevent partial writes.

```python
def _config_collections_append(config_path: Path, path: str) -> None:
    doc = tomlkit.parse(config_path.read_text())
    rag = doc.setdefault("rag", tomlkit.table())
    if "collections" not in rag:
        rag["collections"] = tomlkit.array()
    rag["collections"].append(path)  # native Array.append — preserves existing formatting
    tmp = config_path.with_suffix(".tmp")
    tmp.write_text(tomlkit.dumps(doc))
    os.replace(tmp, config_path)

def _config_collections_remove(config_path: Path, path: str) -> None:
    resolved = str(Path(path).expanduser().resolve())
    doc = tomlkit.parse(config_path.read_text())
    rag = doc.get("rag", {})
    if "collections" in rag:
        new_cols = tomlkit.array()
        for p in rag["collections"]:
            if str(Path(p).expanduser().resolve()) != resolved:
                new_cols.append(p)
        rag["collections"] = new_cols
    tmp = config_path.with_suffix(".tmp")
    tmp.write_text(tomlkit.dumps(doc))
    os.replace(tmp, config_path)
```

**Note**: do NOT convert tomlkit `Array` to a plain Python `list` before reassigning — this destroys comments and formatting. Always use `tomlkit.array()` and its `.append()` method.

### `archon rag help` and `archon rag collection help`

In `main.py`, `p_rag` and `p_collection` parser references are captured and passed down via `args` or via a closure. The `help` subcommand under each group calls `parser.print_help()` on the relevant parser.

```python
# main.py
rag_sub.add_parser("help", help="Show rag subcommand help")
collection_sub.add_parser("help", help="Show collection subcommand help")

# rag_cmd.py dispatch
"help": lambda args: (p_rag.print_help(), 0)[1],
# collection dispatch
"help": lambda args: (p_collection.print_help(), 0)[1],
```

Since `print_help()` needs the parser object, `run_rag()` receives it as a parameter, or the parser is recreated from `main.py` refs. The cleanest pattern: pass `rag_parser` and `collection_parser` into `run_rag(args, rag_parser, collection_parser)`.

### Argparser registration in `main.py`

```python
p_rag = sub.add_parser("rag", help="Manage the RAG search service")
rag_sub = p_rag.add_subparsers(dest="rag_command", metavar="<action>")
rag_sub.add_parser("help", help="Show this help")
# ... existing: install, uninstall, start, stop, status, ingest, sync ...
rag_sub.add_parser("sync", help="Reconcile collections with config")

p_collection = rag_sub.add_parser("collection", help="Manage indexed collections")
collection_sub = p_collection.add_subparsers(dest="collection_command", metavar="<action>")
collection_sub.add_parser("list", help="List all collections")
collection_sub.add_parser("help", help="Show this help")

p_col_add = collection_sub.add_parser("add", help="Add a directory as a collection")
p_col_add.add_argument("path", help="Directory path to index")

p_col_remove = collection_sub.add_parser("remove", help="Remove a collection")
p_col_remove.add_argument("path", help="Directory path to remove")
p_col_remove.add_argument("--force", "-f", action="store_true", help="Remove even if service is running")
```

### Install bootstrap update

`RagInstaller.create_history_collection()` → replaced by calling `RagCollectionSync.sync(cfg.rag.collections)`. This ingests all default collections (history + workspace) on first install.

---

## Tests

- **`test_path_to_collection_name_basic`** (unit): `~/.archon/history/sessions` → `"sessions"`
- **`test_path_to_collection_name_sanitizes_special_chars`** (unit): path with spaces/dots → valid name
- **`test_path_to_collection_name_empty_component_fallback`** (unit): root `/` → `"collection"`
- **`test_sync_adds_new_collection`** (unit): config has path not in LanceDB → `ingest_directory` called
- **`test_sync_drops_removed_collection`** (unit): LanceDB has collection not in config → `drop_collection` called
- **`test_sync_skips_unchanged_collection`** (unit): collection in both config and LanceDB → no ingest, no drop
- **`test_sync_resolves_collision`** (unit): two paths with same basename → distinct names via parent prefix
- **`test_sync_records_ingest_error`** (unit): `ingest_directory` raises → error in `SyncResult.errors`, other collections still processed
- **`test_drop_collection_removes_table`** (unit): mock `_db.drop_table`; assert called with correct name
- **`test_drop_collection_raises_keyerror_on_missing`** (unit): table not in `list_tables()` result → `KeyError`
- **`test_rename_collection_renames_table`** (unit): mock `_db.rename_table`; assert called with correct old/new names
- **`test_rename_collection_raises_keyerror_on_missing`** (unit): old name not in `list_tables()` result → `KeyError`
- **`test_rag_config_default_collections`** (unit): default contains history and workspace paths
- **`test_history_collection_derived_from_history_dir`** (unit): `path_to_collection_name` applied to history sessions path = `"sessions"`
- **`test_sync_cli_command_prints_result`** (unit): mock sync → assert output format
- **`test_collection_list_shows_path_and_counts`** (unit): mock store + config → assert output with indexed/orphan(managed)/unmanaged status
- **`test_collection_list_distinguishes_managed_orphan_from_unmanaged`** (unit): mock store with 3 collections — one in config+manifest (indexed), one in manifest only (managed orphan, will be removed on next sync), one in LanceDB only not in manifest (unmanaged, will NOT be touched by sync)
- **`test_collection_add_appends_to_config_and_ingests`** (unit): new path → config written + `ingest_directory` called
- **`test_collection_add_already_registered_exits_0`** (unit): duplicate path → "already registered", exit 0
- **`test_collection_add_normalizes_tilde`** (unit): `~/docs` and `/home/user/docs` are the same path
- **`test_collection_remove_removes_from_config_and_drops`** (unit): path in config, service stopped → config written + `drop_collection` called
- **`test_collection_remove_path_not_in_config_exits_1`** (unit): unknown path → error + exit 1
- **`test_collection_remove_service_running_without_force_exits_1`** (unit): service running + no `--force` → error + exit 1
- **`test_collection_remove_service_running_with_force_proceeds`** (unit): service running + `--force` → warns + proceeds
- **`test_config_collections_append_writes_tomlkit`** (unit): path appended to `[rag] collections` in config file
- **`test_config_collections_append_preserves_existing_comments`** (unit): inline comments in existing `collections` array are preserved after append
- **`test_config_collections_remove_normalizes_tilde`** (unit): `~/docs` removed even if stored as `/home/user/docs`
- **`test_rag_help_prints_usage`** (unit): `archon rag help` → prints rag subcommand list, exit 0
- **`test_collection_help_prints_usage`** (unit): `archon rag collection help` → prints collection subcommand list, exit 0
- **`test_sync_integration`** (integration): temp LanceDB + real RagCollectionSync; add/remove paths → verify state
- **`test_server_runs_sync_on_startup`** (integration): mock sync called before `app.run_http_async`
- **`test_collection_add_integration`** (integration): real config file + temp LanceDB; add path → collection appears in list_collections
- **`test_collection_remove_integration`** (integration): real config file + temp LanceDB; add then remove → collection absent
- **`test_migration_renames_archon_history_to_derived_name`** (unit): when `archon-history` table exists and `sessions` does not, sync renames table to `sessions` instead of dropping it
- **`test_migration_skips_if_both_tables_exist`** (unit): when both `archon-history` and `sessions` exist, migration is skipped and a WARNING is logged
- **`test_sync_resolves_three_way_collision`** (unit): three paths all sharing the same basename → all three get distinct names via parent prefix
- **`test_sync_resolves_deep_collision_with_hash_fallback`** (unit): two paths that share identical parent+basename components → final tiebreaker appends `_<sha1[:6]>`
- **`test_sync_preserves_unmanaged_manually_ingested_collection`** (unit): a collection not in the manifest is not dropped by sync even when not in config
- **`test_sync_records_warning_for_nonexistent_path`** (unit): path in config that does not exist on disk → appears in `SyncResult.errors` with descriptive message, other paths still processed
- **`test_sync_with_empty_collections_drops_only_managed`** (unit): `collections = []` → only manifest-tracked collections are dropped; unmanaged collections untouched
- **`test_sync_handles_keyerror_on_drop_phantom_manifest_entry`** (unit): manifest has a collection name that is not in LanceDB → `drop_collection` raises `KeyError`, WARNING is logged, error recorded in `SyncResult.errors`, sync continues without crashing
- **`test_collection_add_warns_if_service_running`** (unit): service is running when `collection add` is called → warning printed, ingest still proceeds
- **`test_collection_add_uses_naive_name_collision_resolved_on_next_sync`** (unit): `collection add` derives name via `path_to_collection_name` (naive, collision-unaware); when a collision exists, the naive name is used for immediate ingest and sync resolves the collision on the next run
- **`test_server_starts_even_if_sync_times_out`** (integration): sync takes longer than `sync_timeout_seconds` → server starts anyway, background sync task created

---

## Documentation update
- [x] `rag_guide.md`, sections: Declarative Collections + CLI Collection Management, path: `Documentation/UserManual/rag_guide.md`
- [x] `examples/config.toml.example` — add `collections` key with defaults and comments
- [x] `Documentation/Architecture/180_rag_architecture.md` — update collection management section

---

## Task breakdown

### Phase 1 — Data layer
> **Releasable**: after Task 1.3; store and sync classes usable by server and CLI

#### Task 1.1 — Add `path_to_collection_name()` utility
- [x] **File**: `archon/rag/sync.py` (new file)
- **Depends on**: nothing
- **Description**:
  - Module-level function: `def path_to_collection_name(path: str) -> str`
  - Expand `~`, resolve absolute, take `Path.name`
  - Sanitize: `re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")` or `"collection"` if empty
  - Deterministic — same path always yields same name
  - No collision handling here; collision handling is in `RagCollectionSync.sync()`
- **Releasable**: utility callable in sync, server, and gateway
- **Tests (TDD)** — `tests/rag/test_sync.py`:
  - Unit: `test_path_to_collection_name_basic`
  - Unit: `test_path_to_collection_name_sanitizes_special_chars`
  - Unit: `test_path_to_collection_name_empty_component_fallback`
  - Checkpoint: `uv run pytest tests/rag/test_sync.py -k "path_to_collection_name" -v`

#### Task 1.2 — Add `RagStore.drop_collection()`
- [x] **File**: `archon/rag/store.py`
- **Depends on**: nothing
- **Description**:
  - `async def drop_collection(self, name: str) -> None`
  - `RagStore` uses `lancedb.connect_async()` → `AsyncConnection`; use native async methods (no `asyncio.to_thread` needed)
  - `names = (await self._db.list_tables()).tables` — `list_tables()` returns `ListTablesResponse` with `.tables: list[str]`
  - If `name not in names`: raise `KeyError(name)`
  - `await self._db.drop_table(name)` to drop
  - Must be called after `store.connect()`
- **Releasable**: store can drop collections
- **Tests (TDD)** — `tests/rag/test_store.py`:
  - Unit: `test_drop_collection_removes_table`
  - Unit: `test_drop_collection_raises_keyerror_on_missing`
  - Integration: `test_drop_collection_integration` — ingest → drop → list_collections asserts gone
  - Checkpoint: `uv run pytest tests/rag/test_store.py -k "drop_collection" -v`

#### Task 1.3 — Add `SyncResult` dataclass and `RagCollectionSync` class
- [x] **File**: `archon/rag/sync.py`
- **Depends on**: Task 1.1, Task 1.2
- **Description**:
  - `@dataclass class SyncResult: added: list[str]; removed: list[str]; unchanged: list[str]; errors: list[str]; skipped: list[str]`
  - `class RagCollectionSync: __init__(self, pipeline: RagPipeline) -> None`
  - `async def sync(self, collections: list[str]) -> SyncResult`:
    0. Run migration: if `archon-history` table exists and `sessions` does not, call `pipeline.store.rename_collection("archon-history", "sessions")`; log INFO on rename, WARNING if both exist
    1. Build `desired: dict[str, str]` — `{collection_name: resolved_path}` using `path_to_collection_name` with full collision resolution (prepend parent; hash tiebreaker)
    2. Get `existing: set[str]` from `pipeline.store.list_collections()` names
    3. Load manifest from `{db_path}/sync_manifest.json` (i.e. `Path(cfg.rag.db_path).expanduser() / "sync_manifest.json"`) → `managed_names: set[str]`
    4. To remove: `(existing ∩ managed_names) - desired.keys()` → call `pipeline.store.drop_collection(name)` for each; catch `KeyError` (collection in manifest but not in LanceDB) — log WARNING, record in `errors`, continue
    5. Skipped: `existing - managed_names - desired.keys()` (unmanaged collections, never touched)
    6. To add: `desired.keys() - existing` → call `pipeline.ingest_directory(Path(path), name)` for each
       - Skip paths that do not exist on disk; record in `errors` as `"path does not exist: <path>"`
       - Catch other exceptions into `errors`
    7. Unchanged: `existing & desired.keys()`
    8. Update manifest to reflect new `desired` mapping; write atomically
  - Manifest file: `<db_path>/sync_manifest.json` (i.e. `Path(cfg.rag.db_path).expanduser() / "sync_manifest.json"`), format `{collection_name: source_path}`
- **Releasable**: sync runs correctly — adds, removes, reports; manifest preserved; unmanaged collections untouched
- **Tests (TDD)** — `tests/rag/test_sync.py`:
  - Unit: `test_sync_adds_new_collection`
  - Unit: `test_sync_drops_removed_collection`
  - Unit: `test_sync_skips_unchanged_collection`
  - Unit: `test_sync_resolves_collision`
  - Unit: `test_sync_resolves_three_way_collision`
  - Unit: `test_sync_resolves_deep_collision_with_hash_fallback`
  - Unit: `test_sync_records_ingest_error`
  - Unit: `test_sync_preserves_unmanaged_manually_ingested_collection`
  - Unit: `test_sync_records_warning_for_nonexistent_path`
  - Unit: `test_sync_with_empty_collections_drops_only_managed`
  - Unit: `test_sync_handles_keyerror_on_drop_phantom_manifest_entry`
  - Unit: `test_migration_renames_archon_history_to_derived_name`
  - Unit: `test_migration_skips_if_both_tables_exist`
  - Integration: `test_sync_integration`
  - Checkpoint: `uv run pytest tests/rag/test_sync.py -v`

### Phase 2 — Config changes
> **Releasable**: after Task 2.2; `RagConfig` carries `collections` and `history_collection` is derived

#### Task 2.1 — Add `collections` field to `RagConfig`
- [x] **File**: `archon/config/loader.py`
- **Depends on**: nothing
- **Description**:
  - Add `collections: list[str] = field(default_factory=lambda: ["~/.archon/history/sessions", "~/.archon/workspace"])`
  - Add `sync_timeout_seconds: int = 30`
  - Remove `history_collection: str` field from `RagConfig` dataclass
  - Update `load_config()` TOML parsing: read `collections` from `rag_data.get("collections", RagConfig().collections)`; remove `history_collection` parsing
  - In `load_config()`: if `rag_data` contains `history_collection` key, log a WARNING and queue a Telegram notification (via the gateway notification channel) — the key is ignored
- **Releasable**: config loads `collections`; `history_collection` removed from config; deprecated key triggers warning
- **Tests (TDD)** — `tests/config/test_rag_config.py`:
  - Unit: `test_rag_config_default_collections`
  - Unit: `test_rag_config_parses_collections_from_toml`
  - Unit: `test_rag_config_history_collection_field_removed`
  - Unit: `test_rag_config_warns_on_legacy_history_collection_key`
  - Checkpoint: `uv run pytest tests/config/ -v`

#### Task 2.2 — Replace `history_collection` references with derived value
- [x] **Files**: `archon/rag/server.py`, `archon/rag/pipeline.py`, `archon/gateway/gateway.py`, `archon/cli/rag_cmd.py`, `archon/rag/install.py`
- **Depends on**: Task 1.1, Task 2.1
- **Description**:
  - Remove all `cfg.rag.history_collection` references
  - Replace with: `from archon.rag.sync import path_to_collection_name; history_col = path_to_collection_name(str(Path(cfg.history.directory).expanduser() / "sessions"))`
  - **`RagPipeline` constructor**: remove the `history_collection` parameter from `RagPipeline.__init__` and the `create_pipeline` factory function; update all call sites in `server.py` and `gateway.py` to derive the name via `path_to_collection_name(...)`
  - In `server.py:create_app(pipeline, default_collection)` call: pass the derived name
  - In `gateway.py`: compute the derived name when building `rag_url` context
  - In `rag_cmd.py:_run_ingest`: use derived name as fallback when `--collection` not given
  - In `install.py`: remove `create_history_collection()`; replace call in `run()` with `RagCollectionSync.sync(cfg.collections)`
- **Releasable**: all callers use derived history collection name; no `AttributeError` at startup
- **Tests (TDD)** — `tests/gateway/test_rag_integration.py`, `tests/rag/test_install.py`:
  - Unit: `test_history_collection_derived_from_history_dir`
  - Unit: `test_server_uses_derived_history_collection`
  - Unit: `test_create_pipeline_no_history_collection_param`
  - Checkpoint: `uv run pytest tests/gateway/ tests/rag/ -v`

### Phase 3 — Service startup sync
> **Releasable**: after Task 3.1; service auto-syncs collections on every start

#### Task 3.1 — Call `RagCollectionSync.sync()` in `server.py:main()`
- [x] **File**: `archon/rag/server.py`
- **Depends on**: Task 1.3, Task 2.2
- **Description**:
  - After `await pipeline.store.connect()`, before `await app.run_http_async(...)`:
    - Wrap `sync()` in `asyncio.wait_for(..., timeout=cfg.rag.sync_timeout_seconds)`
    - On success: log INFO with added/removed/unchanged/errors counts
    - On `asyncio.TimeoutError`: log WARNING; schedule `asyncio.create_task(sync.sync(...))` for background completion; start server with partial results
  - Log at WARNING if `sync_result.errors` is non-empty (when sync completes within timeout)
  - Sync failure on one collection does not abort server startup
- **Releasable**: service auto-syncs on every start; timeout-safe
- **Tests (TDD)** — `tests/rag/test_server.py`:
  - Integration: `test_server_runs_sync_on_startup` — assert sync called before HTTP starts
  - Unit: `test_server_logs_warning_on_sync_errors`
  - Integration: `test_server_starts_even_if_sync_times_out`
  - Checkpoint: `uv run pytest tests/rag/test_server.py -v`

### Phase 4 — CLI commands
> **Releasable**: after Task 4.5; full collection management available from the terminal

#### Task 4.1 — Add `archon rag sync` CLI command
- [x] **File**: `archon/cli/rag_cmd.py`
- **Depends on**: Task 1.3, Task 2.1
- **Description**:
  - `run_rag(args, rag_parser, collection_parser)` — add two parser params for help commands (see Task 4.5)
  - Add `"sync": _run_sync` to `dispatch` in `run_rag()`
  - `_run_sync(args)`:
    - Warn if service is running (write conflicts possible)
    - Load config, create pipeline, connect store, call `RagCollectionSync(pipeline).sync(cfg.rag.collections)`, disconnect
    - Print: `"Sync complete: N added, M removed, K unchanged, E errors."`
    - Print each added name, each removed name, each error path
    - Return 0 if no errors, 1 if any errors
- **Releasable**: `archon rag sync` reconciles all collections from CLI
- **Tests (TDD)** — `tests/cli/test_rag_cmd.py`:
  - Unit: `test_sync_cli_command_prints_result`
  - Unit: `test_sync_cli_returns_1_on_errors`
  - Unit: `test_sync_cli_warns_if_service_running`
  - Checkpoint: `uv run pytest tests/cli/test_rag_cmd.py -k "test_sync" -v`

#### Task 4.2 — Add `archon rag collection list` CLI command
- [x] **File**: `archon/cli/rag_cmd.py`
- **Depends on**: Task 1.1, Task 2.1
- **Description**:
  - Add `"collection": _run_collection` to `dispatch`
  - `_run_collection(args)` dispatches on `args.collection_command` to `list`, `add`, `remove`, `help`
  - `_run_collection_list(args)`:
    - Load config; open store, call `list_collections()`, load manifest; disconnect
    - Build name-to-path mapping using the manifest as the primary source; fall back to `path_to_collection_name` for paths not yet synced (i.e., not present in the manifest)
    - Three status values per collection:
      - `indexed` — name in desired (config) AND in LanceDB
      - `orphan (managed)` — name in manifest but NOT in config; will be removed on next sync
      - `unmanaged` — name in LanceDB but NOT in manifest (created via `archon rag ingest --collection`); will NOT be touched by sync
    - For each LanceDB collection: print `name  path=<from desired or manifest or "(unknown)">  docs=N  chunks=M  status=<indexed|orphan (managed)|unmanaged>`
    - For paths in config but not yet indexed: print `name  path=<path>  (not yet indexed)`
    - If nothing: print `"No collections found."`
- **Releasable**: `archon rag collection list` shows full state
- **Tests (TDD)** — `tests/cli/test_rag_cmd.py`:
  - Unit: `test_collection_list_shows_path_and_counts`
  - Unit: `test_collection_list_marks_orphans`
  - Unit: `test_collection_list_distinguishes_managed_orphan_from_unmanaged`
  - Unit: `test_collection_list_shows_unindexed_config_paths`
  - Unit: `test_collection_list_empty`
  - Checkpoint: `uv run pytest tests/cli/test_rag_cmd.py -k "collection_list" -v`

#### Task 4.3 — Add `archon rag collection add <path>` CLI command
- [x] **File**: `archon/cli/rag_cmd.py`
- **Depends on**: Task 1.3, Task 2.1
- **Description**:
  - `_run_collection_add(args)`: `args.path: str`
  - Resolve `Path(args.path).expanduser().resolve()` for normalised comparison
  - If resolved path already in config (after normalising stored paths): print `"Already registered: <path>"`, exit 0
  - Check `get_rag_service().status().running`; if running: print a warning about write conflicts (do not block — just warn)
  - Call `_config_collections_append(config_path, args.path)` — appends the path as given (preserving `~` if the user typed it); uses tomlkit native `Array.append()` + atomic write
  - Immediately ingest: create pipeline, connect, call `pipeline.ingest_directory(resolved_path, col_name)`, disconnect
    - `col_name` is derived via `path_to_collection_name(path)` (collision-unaware). If the manifest already contains an entry for the same path (from a previous sync), use the existing name from the manifest; otherwise derive via `path_to_collection_name` (the name will be collision-checked on the next sync). If a collision with another collection exists, the naive name is used and will be resolved on the next `archon rag sync`.
  - On ingest error: print error, but path remains in config (will retry on next sync); exit 1
  - Print: `"Collection added and indexed: <path>"` + `"Run 'archon rag stop && archon rag start' for the service to start serving it."`
  - `_config_collections_append(config_path: Path, path: str) -> None`: reads config.toml with tomlkit, appends to `[rag] collections` using native Array, writes back atomically
  - `_manifest_lookup_by_path` renamed to `manifest_lookup_by_path` (public) in `archon/rag/sync.py`
- **Releasable**: `archon rag collection add <path>` registers and ingests a directory
- **Tests (TDD)** — `tests/cli/test_rag_cmd.py`:
  - [x] Unit: `test_collection_add_appends_to_config_and_ingests`
  - [x] Unit: `test_collection_add_already_registered_exits_0`
  - [x] Unit: `test_collection_add_normalizes_tilde`
  - [x] Unit: `test_collection_add_warns_if_service_running`
  - [x] Unit: `test_collection_add_uses_naive_name_collision_resolved_on_next_sync`
  - [x] Unit: `test_config_collections_append_writes_tomlkit`
  - [x] Unit: `test_config_collections_append_preserves_existing_comments`
  - [x] Integration: `test_collection_add_integration`
  - [x] Unit: `test_collection_add_uses_manifest_name_when_available` *(added during DA review)*
  - [x] Unit: `test_collection_add_ingest_error_path_stays_in_config` *(added during DA review)*
  - [x] Unit: `test_config_collections_append_creates_missing_rag_section` *(added during DA review)*
  - [x] Unit: `test_collection_add_nonexistent_directory_ingest_fails` *(added during DA review)*
  - Checkpoint: `uv run pytest tests/cli/test_rag_cmd.py -k "collection_add" -v`

#### Task 4.4 — Add `archon rag collection remove <path>` CLI command
- [x] **File**: `archon/cli/rag_cmd.py`
- **Depends on**: Task 1.2, Task 1.3, Task 2.1
- **Description**:
  - [x] `_run_collection_remove(args)`: `args.path: str`, `args.force: bool`
  - [x] Normalise resolved path; if not in config: print `"Error: not in collections: <path>"`, exit 1
  - [x] If service is running and not `args.force`: print error with stop instructions, exit 1
  - [x] If service is running and `args.force`: print `"Warning: removing collection while service is running."`
  - [x] Call `_config_collections_remove(config_path, args.path)` — removes the matching entry
  - [x] Look up the actual (collision-resolved) collection name from the manifest via `_manifest_lookup_by_path(manifest_path, path)`; if not found (path not yet synced), fall back to `path_to_collection_name(path)`; call `store.drop_collection(name)`; ignore `KeyError` (already gone)
  - [x] Print: `"Collection removed: <path>"`
  - [x] `_config_collections_remove(config_path: Path, path: str) -> None`: reads config.toml with tomlkit, filters out the resolved path match, writes back
  - [x] `_manifest_remove_entry(manifest_path, col_name)` for best-effort manifest cleanup
  - [x] Drop happens before config remove (correct ordering: drop first, then config)
  - [x] Non-KeyError exceptions from drop_collection handled gracefully
  - [x] `"remove": _run_collection_remove` added to dispatch dict, usage string updated
- **Releasable**: `archon rag collection remove <path>` deregisters and drops a collection
- **Tests (TDD)** — `tests/cli/test_rag_cmd.py`:
  - [x] Unit: `test_collection_remove_removes_from_config_and_drops`
  - [x] Unit: `test_collection_remove_path_not_in_config_exits_1`
  - [x] Unit: `test_collection_remove_service_running_without_force_exits_1`
  - [x] Unit: `test_collection_remove_service_running_with_force_proceeds`
  - [x] Unit: `test_config_collections_remove_normalizes_tilde`
  - [x] Integration: `test_collection_remove_integration`
  - [x] Unit: `test_collection_remove_drop_failure_leaves_config_intact`
  - [x] Unit: `test_collection_remove_uses_manifest_name_for_drop`
  - Checkpoint: `uv run pytest tests/cli/test_rag_cmd.py -k "collection_remove" -v`

#### Task 4.5 — Add `archon rag help` and `archon rag collection help`
- [x] **Files**: `archon/cli/main.py`, `archon/cli/rag_cmd.py`
- **Depends on**: Task 4.1, Task 4.2, Task 4.3, Task 4.4
- **Description**:
  - [x] `main.py`: add `rag_sub.add_parser("help", help="Show rag subcommand help")` and `collection_sub.add_parser("help", help="Show collection subcommand help")`
  - [x] Pass `p_rag` and `p_collection` into `run_rag(args, rag_parser=p_rag, collection_parser=p_collection)` at the call site in `main.py`
  - [x] `rag_cmd.py:run_rag(args, rag_parser, collection_parser)`:
    - [x] `"help"` dispatch → `rag_parser.print_help(); return 0`
  - [x] `_run_collection(args, collection_parser)`:
    - [x] `"help"` dispatch → `collection_parser.print_help(); return 0`
    - [x] `None` (no subcommand given) → same as `"help"`
  - [x] `main.py`: also register `rag_sub.add_parser("sync")` and the full `collection` sub-tree (see Architecture section)
  - [x] If `args.rag_command is None`: print rag help and return 0 (consistent with top-level `help` handling)
- **Releasable**: `archon rag help` and `archon rag collection help` work; missing subcommand shows help instead of silent error
- **Tests (TDD)** — `tests/cli/test_rag_cmd.py`, `tests/cli/test_main.py`:
  - [x] Unit: `test_rag_help_prints_usage`
  - [x] Unit: `test_collection_help_prints_usage`
  - [x] Unit: `test_rag_no_subcommand_prints_help`
  - [x] Unit: `test_collection_no_subcommand_prints_help`
  - Checkpoint: `uv run pytest tests/cli/ -k "help" -v`

### Phase 5 — Documentation
> **Releasable**: after Task 5.1

#### Task 5.1 — Update docs for declarative collections and CLI management
- [x] **Files**: `Documentation/UserManual/rag_guide.md`, `examples/config.toml.example`, `Documentation/Architecture/180_rag_architecture.md`
- **Depends on**: Task 4.5
- **Description**:
  - `rag_guide.md`:
    - "Declarative Collections" section — default paths, how config drives sync, restart vs `archon rag sync`
    - "CLI Collection Management" section — `add`, `remove`, `list`, `help` with examples
    - "Migration" section — `archon-history` → `sessions` automatic rename on first sync; removing `history_collection` from existing configs; manifest file location
    - Collision resolution note (same basename → parent-prefixed name; hash tiebreaker)
  - `config.toml.example`: add `collections = [...]` with defaults and inline comments
  - `180_rag_architecture.md`: update collection management section to describe sync model and CLI flow
- **Tests (TDD)**: N/A
- Checkpoint: `uv run pytest tests/ -v`
