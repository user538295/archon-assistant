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
- Collection renaming
- Incremental re-index (changed files) — sync always re-ingests the full directory
- Access control per collection
- `archon rag ingest` with a manual `--collection` flag (still works unchanged for one-off use)

---

## Acceptance criteria
- [ ] `RagConfig` has `collections: list[str]` with default `["~/.archon/history/sessions", "~/.archon/workspace"]`
- [ ] `path_to_collection_name` converts any path to a valid, unique LanceDB table name
- [ ] `RagCollectionSync.sync()` ingests paths in config not yet in LanceDB
- [ ] `RagCollectionSync.sync()` drops LanceDB collections whose paths are no longer in config
- [ ] `server.py:main()` runs sync before the HTTP server starts accepting connections
- [ ] `archon rag sync` runs sync and prints added/removed/unchanged counts
- [ ] `archon rag collection list` shows each collection with source path, doc/chunk counts, and indexed/orphan status
- [ ] `archon rag collection add <path>` appends path to `config.toml [rag] collections`, immediately ingests, prints confirmation
- [ ] `archon rag collection add <path>` on a path already in config prints "already registered" and exits 0
- [ ] `archon rag collection remove <path>` removes path from config, drops collection if service is stopped; warns and requires `--force` if service is running
- [ ] `archon rag collection remove <path>` on a path not in config prints error and exits 1
- [ ] `archon rag help` prints `archon rag` subcommand listing and exits 0
- [ ] `archon rag collection help` prints collection subcommand listing and exits 0
- [ ] `history_collection` is auto-derived as `path_to_collection_name(config.history.directory + "/sessions")` — no longer a user-editable field
- [ ] Removing a path from `collections` and restarting the service (or running `archon rag sync`) drops its LanceDB table
- [ ] Adding a path to `collections` and running `archon rag sync` ingests the directory
- [ ] All existing tests pass; new tests cover sync, drop, derivation, and all CLI actions

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

---

## Architecture

### `path_to_collection_name(path: str) -> str`

New module-level function in `archon/rag/pipeline.py` (or a new `archon/rag/collection_utils.py`).

Derivation rule:
1. Expand `~`, resolve to absolute path
2. Use the last path component (directory name) as the collection name
3. Sanitize: lowercase, replace non-alphanumeric with `_`, collapse multiple `_`, strip leading/trailing `_`
4. Collision handling: if two config paths resolve to the same last component, prepend the parent component — e.g., `archon_sessions` and `project_sessions`

```python
def path_to_collection_name(path: str) -> str:
    resolved = Path(path).expanduser().resolve()
    name = resolved.name.lower()
    import re
    name = re.sub(r"[^a-z0-9]+", "_", name).strip("_")
    return name or "collection"
```

Collision detection happens at sync time (see `RagCollectionSync`).

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
```

`history_collection` is removed. Callers that needed `cfg.rag.history_collection` (gateway, server, context_provider) will call `path_to_collection_name(cfg.history.directory + "/sessions")` instead. Since `RagConfig` does not know about `HistoryConfig`, this derivation happens at the call sites in `gateway.py` and `server.py`.

### `RagStore.drop_collection(name: str) -> None` in `archon/rag/store.py`

```python
async def drop_collection(self, name: str) -> None:
    names = await asyncio.to_thread(lambda: self._db.table_names())
    if name not in names:
        raise KeyError(name)
    await asyncio.to_thread(self._db.drop_table, name)
```

### `SyncResult` and `RagCollectionSync` in `archon/rag/sync.py` (new file)

```python
@dataclass
class SyncResult:
    added: list[str]      # collection names ingested
    removed: list[str]    # collection names dropped
    unchanged: list[str]  # collection names already present
    errors: list[str]     # paths that failed to ingest

class RagCollectionSync:
    def __init__(self, pipeline: RagPipeline) -> None: ...

    async def sync(self, collections: list[str]) -> SyncResult:
        """Reconcile config paths with LanceDB state."""
        # 1. Build desired: {collection_name: path} with collision resolution
        # 2. Get existing: set of collection names from store.list_collections()
        # 3. To remove: existing - desired.keys()
        # 4. To add: desired.keys() - existing
        # 5. Drop removed, ingest added, report unchanged
```

Collision resolution in `sync()`: if two paths derive the same name, prepend parent component. E.g., `/a/history/sessions` and `/b/project/sessions` → `history_sessions` and `project_sessions`.

### Service startup: `server.py:main()`

After `pipeline.store.connect()` and before `app.run_http_async(...)`:

```python
from archon.rag.sync import RagCollectionSync
sync = RagCollectionSync(pipeline)
result = await sync.sync(cfg.rag.collections)
logger.info("RAG sync: added=%s removed=%s unchanged=%s errors=%s",
            result.added, result.removed, result.unchanged, result.errors)
```

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
    # Open store, list_collections(), cross-reference with cfg.rag.collections
    # Print: name | path | docs=N | chunks=M | status (indexed / orphan)
    # Orphan = in LanceDB but not in config (will be dropped on next sync)
```

### CLI: `archon rag collection add <path>`

```python
def _run_collection_add(args: Namespace) -> int:
    path = str(Path(args.path).expanduser().resolve())
    cfg = load_config()
    if path in [str(Path(p).expanduser().resolve()) for p in cfg.rag.collections]:
        print(f"Already registered: {args.path}")
        return 0
    # Write to config.toml using tomlkit (same pattern as configure_providers)
    _config_collections_append(config_path, args.path)
    # Immediately ingest (safe: new collection = no service read conflict)
    pipeline = create_pipeline(load_config().rag)
    asyncio.run(_ingest_one(pipeline, path))
    print(f"Collection added and indexed: {args.path}")
    print("Run 'archon rag restart' for the service to start serving it.")
    return 0
```

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
        print("  archon rag stop && archon rag collection remove <path>")
        return 1
    if svc_running:
        print("Warning: removing collection while service is running.")
    _config_collections_remove(config_path, args.path)
    col_name = path_to_collection_name(path)
    pipeline = create_pipeline(load_config().rag)
    asyncio.run(_drop_one(pipeline, col_name))
    print(f"Collection removed: {args.path}")
    return 0
```

### Config list writer: `_config_collections_append` / `_config_collections_remove`

Private helpers in `archon/cli/rag_cmd.py` (or reuse tomlkit directly):

```python
def _config_collections_append(config_path: Path, path: str) -> None:
    doc = tomlkit.parse(config_path.read_text())
    rag = doc.setdefault("rag", tomlkit.table())
    cols = list(rag.get("collections", []))
    cols.append(path)
    rag["collections"] = cols
    config_path.write_text(tomlkit.dumps(doc))

def _config_collections_remove(config_path: Path, path: str) -> None:
    resolved = str(Path(path).expanduser().resolve())
    doc = tomlkit.parse(config_path.read_text())
    rag = doc.setdefault("rag", tomlkit.table())
    cols = [p for p in list(rag.get("collections", []))
            if str(Path(p).expanduser().resolve()) != resolved]
    rag["collections"] = cols
    config_path.write_text(tomlkit.dumps(doc))
```

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
- **`test_drop_collection_raises_keyerror_on_missing`** (unit): table not in `table_names()` → `KeyError`
- **`test_rag_config_default_collections`** (unit): default contains history and workspace paths
- **`test_history_collection_derived_from_history_dir`** (unit): `path_to_collection_name` applied to history sessions path = `"sessions"`
- **`test_sync_cli_command_prints_result`** (unit): mock sync → assert output format
- **`test_collection_list_shows_path_and_counts`** (unit): mock store + config → assert output with indexed/orphan status
- **`test_collection_add_appends_to_config_and_ingests`** (unit): new path → config written + `ingest_directory` called
- **`test_collection_add_already_registered_exits_0`** (unit): duplicate path → "already registered", exit 0
- **`test_collection_add_normalizes_tilde`** (unit): `~/docs` and `/home/user/docs` are the same path
- **`test_collection_remove_removes_from_config_and_drops`** (unit): path in config, service stopped → config written + `drop_collection` called
- **`test_collection_remove_path_not_in_config_exits_1`** (unit): unknown path → error + exit 1
- **`test_collection_remove_service_running_without_force_exits_1`** (unit): service running + no `--force` → error + exit 1
- **`test_collection_remove_service_running_with_force_proceeds`** (unit): service running + `--force` → warns + proceeds
- **`test_config_collections_append_writes_tomlkit`** (unit): path appended to `[rag] collections` in config file
- **`test_config_collections_remove_normalizes_tilde`** (unit): `~/docs` removed even if stored as `/home/user/docs`
- **`test_rag_help_prints_usage`** (unit): `archon rag help` → prints rag subcommand list, exit 0
- **`test_collection_help_prints_usage`** (unit): `archon rag collection help` → prints collection subcommand list, exit 0
- **`test_sync_integration`** (integration): temp LanceDB + real RagCollectionSync; add/remove paths → verify state
- **`test_server_runs_sync_on_startup`** (integration): mock sync called before `app.run_http_async`
- **`test_collection_add_integration`** (integration): real config file + temp LanceDB; add path → collection appears in list_collections
- **`test_collection_remove_integration`** (integration): real config file + temp LanceDB; add then remove → collection absent

---

## Documentation update
- [ ] `rag_guide.md`, sections: Declarative Collections + CLI Collection Management, path: `Documentation/UserManual/rag_guide.md`
- [ ] `examples/config.toml.example` — add `collections` key with defaults and comments
- [ ] `Documentation/Architecture/180_rag_architecture.md` — update collection management section

---

## Task breakdown

### Phase 1 — Data layer
> **Releasable**: after Task 1.3; store and sync classes usable by server and CLI

#### Task 1.1 — Add `path_to_collection_name()` utility
- [ ] **File**: `archon/rag/sync.py` (new file)
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
- [ ] **File**: `archon/rag/store.py`
- **Depends on**: nothing
- **Description**:
  - `async def drop_collection(self, name: str) -> None`
  - Check `name in await asyncio.to_thread(lambda: self._db.table_names())`; raise `KeyError(name)` if not found
  - `await asyncio.to_thread(self._db.drop_table, name)` to drop
  - Must be called after `store.connect()`
- **Releasable**: store can drop collections
- **Tests (TDD)** — `tests/rag/test_store.py`:
  - Unit: `test_drop_collection_removes_table`
  - Unit: `test_drop_collection_raises_keyerror_on_missing`
  - Integration: `test_drop_collection_integration` — ingest → drop → list_collections asserts gone
  - Checkpoint: `uv run pytest tests/rag/test_store.py -k "drop_collection" -v`

#### Task 1.3 — Add `SyncResult` dataclass and `RagCollectionSync` class
- [ ] **File**: `archon/rag/sync.py`
- **Depends on**: Task 1.1, Task 1.2
- **Description**:
  - `@dataclass class SyncResult: added: list[str]; removed: list[str]; unchanged: list[str]; errors: list[str]`
  - `class RagCollectionSync: __init__(self, pipeline: RagPipeline) -> None`
  - `async def sync(self, collections: list[str]) -> SyncResult`:
    1. Build `desired: dict[str, str]` — `{collection_name: resolved_path}` using `path_to_collection_name`
    2. Collision resolution: if two paths derive same name, prepend parent component to both (e.g., `parent_name`)
    3. Get `existing: set[str]` from `pipeline.store.list_collections()` names
    4. To remove: `existing - desired.keys()` → call `pipeline.store.drop_collection(name)` for each
    5. To add: `desired.keys() - existing` → call `pipeline.ingest_directory(Path(path), name)` for each; catch exceptions into `errors`
    6. Unchanged: `existing & desired.keys()`
  - Only paths that exist on disk are ingested; missing paths are recorded in `errors` without raising
- **Releasable**: sync runs correctly — adds, removes, reports
- **Tests (TDD)** — `tests/rag/test_sync.py`:
  - Unit: `test_sync_adds_new_collection`
  - Unit: `test_sync_drops_removed_collection`
  - Unit: `test_sync_skips_unchanged_collection`
  - Unit: `test_sync_resolves_collision`
  - Unit: `test_sync_records_ingest_error`
  - Integration: `test_sync_integration`
  - Checkpoint: `uv run pytest tests/rag/test_sync.py -v`

### Phase 2 — Config changes
> **Releasable**: after Task 2.2; `RagConfig` carries `collections` and `history_collection` is derived

#### Task 2.1 — Add `collections` field to `RagConfig`
- [ ] **File**: `archon/config/loader.py`
- **Depends on**: nothing
- **Description**:
  - Add `collections: list[str] = field(default_factory=lambda: ["~/.archon/history/sessions", "~/.archon/workspace"])`
  - Remove `history_collection: str` field from `RagConfig` dataclass
  - Update `load_config()` TOML parsing: read `collections` from `rag_data.get("collections", RagConfig().collections)`; remove `history_collection` parsing
- **Releasable**: config loads `collections`; `history_collection` removed from config
- **Tests (TDD)** — `tests/config/test_rag_config.py`:
  - Unit: `test_rag_config_default_collections`
  - Unit: `test_rag_config_parses_collections_from_toml`
  - Unit: `test_rag_config_history_collection_field_removed`
  - Checkpoint: `uv run pytest tests/config/ -v`

#### Task 2.2 — Replace `history_collection` references with derived value
- [ ] **Files**: `archon/rag/server.py`, `archon/gateway/gateway.py`, `archon/cli/rag_cmd.py`, `archon/rag/install.py`
- **Depends on**: Task 1.1, Task 2.1
- **Description**:
  - Remove all `cfg.rag.history_collection` references
  - Replace with: `from archon.rag.sync import path_to_collection_name; history_col = path_to_collection_name(str(Path(cfg.history.directory).expanduser() / "sessions"))`
  - In `server.py:create_app(pipeline, default_collection)` call: pass the derived name
  - In `gateway.py`: compute the derived name when building `rag_url` context
  - In `rag_cmd.py:_run_ingest`: use derived name as fallback when `--collection` not given
  - In `install.py`: remove `create_history_collection()`; replace call in `run()` with `RagCollectionSync.sync(cfg.collections)`
- **Releasable**: all callers use derived history collection name
- **Tests (TDD)** — `tests/gateway/test_rag_integration.py`, `tests/rag/test_install.py`:
  - Unit: `test_history_collection_derived_from_history_dir`
  - Unit: `test_server_uses_derived_history_collection`
  - Checkpoint: `uv run pytest tests/gateway/ tests/rag/ -v`

### Phase 3 — Service startup sync
> **Releasable**: after Task 3.1; service auto-syncs collections on every start

#### Task 3.1 — Call `RagCollectionSync.sync()` in `server.py:main()`
- [ ] **File**: `archon/rag/server.py`
- **Depends on**: Task 1.3, Task 2.2
- **Description**:
  - After `await pipeline.store.connect()`, before `await app.run_http_async(...)`:
    ```python
    from archon.rag.sync import RagCollectionSync
    sync_result = await RagCollectionSync(pipeline).sync(cfg.rag.collections)
    logger.info("RAG sync: added=%s removed=%s unchanged=%s errors=%s",
                sync_result.added, sync_result.removed, sync_result.unchanged, sync_result.errors)
    ```
  - Log at WARNING if `sync_result.errors` is non-empty, else INFO
  - Sync failure on one collection does not abort server startup
- **Releasable**: service auto-syncs on every start
- **Tests (TDD)** — `tests/rag/test_server.py`:
  - Integration: `test_server_runs_sync_on_startup` — assert sync called before HTTP starts
  - Unit: `test_server_logs_warning_on_sync_errors`
  - Checkpoint: `uv run pytest tests/rag/test_server.py -v`

### Phase 4 — CLI commands
> **Releasable**: after Task 4.5; full collection management available from the terminal

#### Task 4.1 — Add `archon rag sync` CLI command
- [ ] **File**: `archon/cli/rag_cmd.py`
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
- [ ] **File**: `archon/cli/rag_cmd.py`
- **Depends on**: Task 1.1, Task 2.1
- **Description**:
  - Add `"collection": _run_collection` to `dispatch`
  - `_run_collection(args)` dispatches on `args.collection_command` to `list`, `add`, `remove`, `help`
  - `_run_collection_list(args)`:
    - Load config; build `desired: dict[str, str]` (name → path) using `path_to_collection_name`
    - Open store, call `list_collections()`, disconnect
    - For each LanceDB collection: print `name  path=<from desired or "(orphan)">  docs=N  chunks=M`
    - Mark collections in LanceDB but not in config as `(orphan — will be removed on next sync)`
    - For paths in config but not yet indexed: print `name  path=<path>  (not yet indexed)`
    - If nothing: print `"No collections found."`
- **Releasable**: `archon rag collection list` shows full state
- **Tests (TDD)** — `tests/cli/test_rag_cmd.py`:
  - Unit: `test_collection_list_shows_path_and_counts`
  - Unit: `test_collection_list_marks_orphans`
  - Unit: `test_collection_list_shows_unindexed_config_paths`
  - Unit: `test_collection_list_empty`
  - Checkpoint: `uv run pytest tests/cli/test_rag_cmd.py -k "collection_list" -v`

#### Task 4.3 — Add `archon rag collection add <path>` CLI command
- [ ] **File**: `archon/cli/rag_cmd.py`
- **Depends on**: Task 1.3, Task 2.1
- **Description**:
  - `_run_collection_add(args)`: `args.path: str`
  - Resolve `Path(args.path).expanduser().resolve()` for normalised comparison
  - If resolved path already in config (after normalising stored paths): print `"Already registered: <path>"`, exit 0
  - Call `_config_collections_append(config_path, args.path)` — appends the path as given (preserving `~` if the user typed it)
  - Immediately ingest: create pipeline, connect, call `pipeline.ingest_directory(resolved_path, col_name)`, disconnect
  - On ingest error: print error, but path remains in config (will retry on next sync); exit 1
  - Print: `"Collection added and indexed: <path>"` + `"Run 'archon rag restart' for the service to start serving it."`
  - `_config_collections_append(config_path: Path, path: str) -> None`: reads config.toml with tomlkit, appends to `[rag] collections`, writes back
- **Releasable**: `archon rag collection add <path>` registers and ingests a directory
- **Tests (TDD)** — `tests/cli/test_rag_cmd.py`:
  - Unit: `test_collection_add_appends_to_config_and_ingests`
  - Unit: `test_collection_add_already_registered_exits_0`
  - Unit: `test_collection_add_normalizes_tilde`
  - Unit: `test_config_collections_append_writes_tomlkit`
  - Integration: `test_collection_add_integration`
  - Checkpoint: `uv run pytest tests/cli/test_rag_cmd.py -k "collection_add" -v`

#### Task 4.4 — Add `archon rag collection remove <path>` CLI command
- [ ] **File**: `archon/cli/rag_cmd.py`
- **Depends on**: Task 1.2, Task 1.3, Task 2.1
- **Description**:
  - `_run_collection_remove(args)`: `args.path: str`, `args.force: bool`
  - Normalise resolved path; if not in config: print `"Error: not in collections: <path>"`, exit 1
  - If service is running and not `args.force`: print error with stop instructions, exit 1
  - If service is running and `args.force`: print `"Warning: removing collection while service is running."`
  - Call `_config_collections_remove(config_path, args.path)` — removes the matching entry
  - Derive collection name with `path_to_collection_name`; call `store.drop_collection(name)`; ignore `KeyError` (already gone)
  - Print: `"Collection removed: <path>"`
  - `_config_collections_remove(config_path: Path, path: str) -> None`: reads config.toml with tomlkit, filters out the resolved path match, writes back
- **Releasable**: `archon rag collection remove <path>` deregisters and drops a collection
- **Tests (TDD)** — `tests/cli/test_rag_cmd.py`:
  - Unit: `test_collection_remove_removes_from_config_and_drops`
  - Unit: `test_collection_remove_path_not_in_config_exits_1`
  - Unit: `test_collection_remove_service_running_without_force_exits_1`
  - Unit: `test_collection_remove_service_running_with_force_proceeds`
  - Unit: `test_config_collections_remove_normalizes_tilde`
  - Integration: `test_collection_remove_integration`
  - Checkpoint: `uv run pytest tests/cli/test_rag_cmd.py -k "collection_remove" -v`

#### Task 4.5 — Add `archon rag help` and `archon rag collection help`
- [ ] **Files**: `archon/cli/main.py`, `archon/cli/rag_cmd.py`
- **Depends on**: Task 4.1, Task 4.2, Task 4.3, Task 4.4
- **Description**:
  - `main.py`: add `rag_sub.add_parser("help", help="Show rag subcommand help")` and `collection_sub.add_parser("help", help="Show collection subcommand help")`
  - Pass `p_rag` and `p_collection` into `run_rag(args, rag_parser=p_rag, collection_parser=p_collection)` at the call site in `main.py`
  - `rag_cmd.py:run_rag(args, rag_parser, collection_parser)`:
    - `"help"` dispatch → `rag_parser.print_help(); return 0`
  - `_run_collection(args, collection_parser)`:
    - `"help"` dispatch → `collection_parser.print_help(); return 0`
    - `None` (no subcommand given) → same as `"help"`
  - `main.py`: also register `rag_sub.add_parser("sync")` and the full `collection` sub-tree (see Architecture section)
  - If `args.rag_command is None`: print rag help and return 0 (consistent with top-level `help` handling)
- **Releasable**: `archon rag help` and `archon rag collection help` work; missing subcommand shows help instead of silent error
- **Tests (TDD)** — `tests/cli/test_rag_cmd.py`, `tests/cli/test_main.py`:
  - Unit: `test_rag_help_prints_usage`
  - Unit: `test_collection_help_prints_usage`
  - Unit: `test_rag_no_subcommand_prints_help`
  - Unit: `test_collection_no_subcommand_prints_help`
  - Checkpoint: `uv run pytest tests/cli/ -k "help" -v`

### Phase 5 — Documentation
> **Releasable**: after Task 5.1

#### Task 5.1 — Update docs for declarative collections and CLI management
- [ ] **Files**: `Documentation/UserManual/rag_guide.md`, `examples/config.toml.example`, `Documentation/Architecture/180_rag_architecture.md`
- **Depends on**: Task 4.5
- **Description**:
  - `rag_guide.md`:
    - "Declarative Collections" section — default paths, how config drives sync, restart vs `archon rag sync`
    - "CLI Collection Management" section — `add`, `remove`, `list`, `help` with examples
    - Migration note: remove `history_collection` key from existing configs
    - Collision resolution note (same basename → parent-prefixed name)
  - `config.toml.example`: add `collections = [...]` with defaults and inline comments
  - `180_rag_architecture.md`: update collection management section to describe sync model and CLI flow
- **Tests (TDD)**: N/A
- Checkpoint: `uv run pytest tests/ -v`
