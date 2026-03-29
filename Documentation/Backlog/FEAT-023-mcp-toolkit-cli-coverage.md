# FEAT-023 — MCP Toolkit CLI Coverage
**Purpose**: Expose every meaningful `archon` CLI command as an MCP tool so Claude (running inside Archon) can invoke RAG management, log reading, version checks, and diagnostics without shelling out.
**Audience**: Claude sessions running inside Archon; any agent that needs to inspect or manage the Archon daemon at runtime.
**Status**: To Do

---

## Background

The ArchonToolkit currently exposes 23 MCP tools covering service lifecycle (status, restart), agent management, notification control, model selection, scheduling, config read/write, and file attachments. However, the entire `archon rag` command namespace (12 subcommands) and several other CLI commands — `archon logs`, `archon version`, `archon doctor`, and `archon config show` (full dump) — have no MCP equivalents. Claude cannot manage RAG collections, check logs, or run health checks via MCP; it must fall back to shell execution, which is slower, less type-safe, and requires an active terminal.

## Goal

Add 13 new MCP tools to `ArchonToolkit` and modify 1 existing tool (`get_config`), mapping 1-to-1 with the missing CLI commands, making every meaningful runtime operation accessible from within an active Claude session. After this feature, there will be no gap between what `archon <cmd>` can do and what an Archon MCP tool can do — excluding destructive/infra-only operations (install, uninstall, update, start/stop the daemon process itself).

---

## Scope

### In Scope
- RAG service lifecycle: `rag_status`, `rag_start`, `rag_stop`
- RAG data operations: `rag_ingest`, `rag_sync`
- RAG collection management: `rag_collection_list`, `rag_collection_add`, `rag_collection_remove`, `rag_collection_info`, `rag_collection_reindex`
- Daemon observability: `get_logs`, `get_version`, `archon_doctor`
- Config dump: extend `get_config` to accept empty/missing `path` and return the full config

### Out of Scope
- `archon start` / `archon stop` — managing the daemon process from within itself
- `archon update` — in-process update is risky and architecturally unsound
- `archon uninstall` — destructive infrastructure operation, not a runtime tool
- `archon rag install` / `archon rag uninstall` — one-time install ops, not runtime
- `archon config edit` — interactive editor, not applicable to MCP
- `--follow` / streaming log tailing — MCP is request/response, not a streaming channel

---

## Acceptance criteria
- [ ] All 13 new tools are registered in `ArchonToolkit.__init__` and appear in `tool_definitions`
- [ ] `get_config` (1 modified tool) returns the full redacted config when `path` is absent or empty
- [ ] RAG service tools (`rag_start`, `rag_stop`, `rag_status`) call `get_rag_service()` methods with `await asyncio.to_thread(...)`
- [ ] `rag_ingest` and `rag_collection_reindex` return an error if the RAG service is running (write conflict guard)
- [ ] `rag_collection_add` appends the path to `config.toml` collections and ingests immediately
- [ ] `rag_collection_remove` drops the LanceDB table, removes from config, cleans up manifest
- [ ] `get_logs` reads the log file using `collections.deque` (no subprocess) and returns last N lines
- [ ] `get_version` returns the version string from `archon.version.get_version()`
- [ ] `archon_doctor` returns synchronous check results as JSON (same synchronous checks as the CLI; async RAG health check omitted — see Known Limitations)
- [ ] All RAG handlers use lazy imports of `archon.rag.*` and return a clear error if RAG is unavailable
- [ ] Test coverage ≥ 85% for all new handlers
- [ ] All existing tests continue to pass

---

## What does NOT change
- Existing 23 registered tools and their schemas remain untouched (except `get_config` schema: `path` changes from required to optional)
- `archon/cli/rag_cmd.py` — the CLI implementation is not modified (collection helpers are extracted by Phase 0 but the CLI continues to work via import)
- `archon/cli/doctor.py` — not modified (diagnostics are extracted by Phase 0 but `doctor.py` continues to import from `archon.diagnostics`)
- `archon/platform/` RAG service implementations — unchanged
- `archon/rag/` pipeline, store, sync — unchanged

---

## Known limitations / accepted trade-offs
- `rag_ingest` and `rag_sync` block the event loop for the duration of ingestion. This cannot be trivially fixed by wrapping in `asyncio.to_thread` since the operations contain internal `await` calls. The handlers include a TODO comment noting that the recommended future improvement is to route ingest/sync through `BackgroundAgentManager` as proper background tasks. Users should only invoke these when no time-sensitive Telegram interactions are expected.
- `rag_collection_add` is not atomic: config is written before ingest completes (same trade-off as the CLI).
- `archon_doctor` MCP tool omits the async RAG health check (`_check_rag_health`) that the CLI version runs. The CLI doctor output and MCP doctor output are therefore not identical. This is a known gap — the async check requires network I/O and a running RAG server; including it in the MCP tool would require a separate design decision.
- `archon_doctor` calls synchronous subprocess checks (`git --version`, `uv --version`) from within the async handler using `await asyncio.to_thread(run_checks)` to avoid blocking.
- `get_config` full-dump has the same per-key redaction logic; any dict value whose key matches the sensitive regex is redacted.

---

## Architecture

### New modules / classes / functions

**Phase 0 introduces two new shared modules (required before Phases 1–4):**

- `archon/config/config_rw.py` (already exists) — extended with two public functions extracted from `archon/cli/rag_cmd.py`: `config_collections_append(config_path, path)`, `config_collections_remove(config_path, path)`. The CLI's private `_config_collections_append` and `_config_collections_remove` functions delegate to the new public functions (or are replaced by direct calls to the shared module). **`manifest_remove_entry` is NOT added here** — it belongs in `archon/rag/sync.py` (see below).
- `archon/rag/sync.py` (existing) — add public function `manifest_remove_entry(manifest_path: Path, col_name: str) -> None` extracted from `archon/cli/rag_cmd.py`. The CLI's private `_manifest_remove_entry` delegates to this new public function. Placing it in `sync.py` keeps manifest logic co-located with the sync machinery that creates and reads the manifest.
- `archon/diagnostics.py` (new) — contains `CheckResult` dataclass, all `_check_*` synchronous functions, and a public `def run_checks() -> list[CheckResult]` function. `archon/cli/doctor.py` imports from here. The async `_check_rag_health` stays in `doctor.py` (CLI-only). **Note**: `_ARCHON_HOME` constant must be explicitly defined in `archon/diagnostics.py` (moved from `doctor.py`) — it is used by `_check_env_file`, `_check_config_file`, `_check_logs_dir`, `_check_health`, `_check_app_dir`, and `_check_bot_token`, making it a non-obvious shared dependency that must travel with the extracted functions.

**Phases 1–4 add RAG tools and observability tools:**

All new tool schemas and handler functions are placed in `archon/ai/archon_toolkit_rag.py` (new file) as a **standalone helper module — NOT a mixin or base class**. It defines: (a) all RAG schema constants (e.g. `_RAG_STATUS_SCHEMA`), and (b) a module-level function `def _register_rag_tools(toolkit: ArchonToolkit) -> None` that calls `toolkit.register_tool(...)` for each RAG tool, using `functools.partial` or lambda to bind handler functions to the toolkit instance. Handler functions are defined as module-level `async def _handle_rag_*(toolkit: ArchonToolkit, arguments: dict, *, user_id: int | None = None) -> str`, taking `toolkit` as their first parameter to access `toolkit._config`, `toolkit._config_file`, etc. In `ArchonToolkit.__init__`, the final line of tool registration is `_register_rag_tools(self)`. This avoids any class hierarchy changes and keeps tooling and type checkers happy.

Observability tools (`get_logs`, `get_version`, `archon_doctor`) and the `get_config` extension follow the existing pattern and are added directly to `archon/ai/archon_toolkit.py`.

**Pattern for each new tool in `archon_toolkit_rag.py`:**
1. A `_SCHEMA` constant (module-level `dict[str, Any]`)
2. A module-level `async def _handle_rag_*(toolkit: ArchonToolkit, arguments: dict, *, user_id: int | None = None) -> str` handler function — NOT a method on `ArchonToolkit`; takes `toolkit` as the first parameter
3. `_register_rag_tools(toolkit)` calls `toolkit.register_tool(...)` for each RAG tool, binding each handler via `functools.partial(_handle_rag_*, toolkit)` or equivalent lambda

### Mock strategy for "RAG not available" tests
> **Note**: `archon_toolkit_rag.py` uses a **module-level availability flag** rather than per-handler lazy imports. At the top of the module:
> ```python
> try:
>     from archon.platform import get_rag_service
>     from archon.rag.pipeline import create_pipeline
>     from archon.rag.store import RagStore
>     from archon.rag.sync import RagCollectionSync, path_to_collection_name, manifest_lookup_by_path
>     _RAG_AVAILABLE = True
> except ImportError:
>     _RAG_AVAILABLE = False
> ```
> Each handler's first statement is `if not _RAG_AVAILABLE: return "RAG not available"`. All `_rag_unavailable` tests use `patch('archon.ai.archon_toolkit_rag._RAG_AVAILABLE', False)`. This is simpler, testable, and avoids per-handler try/except repetition. The Architecture and per-task descriptions that say "Lazy-import … catch ImportError" should be interpreted as this module-level pattern.

### Dependency on `get_rag_service()`
RAG service lifecycle handlers call `archon.platform.get_rag_service()` directly (same as the CLI). No injection needed — it's a module-level singleton factory. Import is done lazily inside the handler to avoid import-time failures when the platform module hasn't been initialised.

All `get_rag_service().start()`, `.stop()`, `.status()` calls are wrapped in `await asyncio.to_thread(...)` because these methods call `subprocess.run()` internally and block the event loop.

### Dependency on `archon.rag.*`
Data operation handlers (`rag_ingest`, `rag_sync`, `rag_collection_*`) import `create_pipeline`, `RagStore`, `RagCollectionSync`, `path_to_collection_name`, `manifest_lookup_by_path` lazily inside the handler, catching `ImportError` and returning `"RAG not available"`.

### Config file access
`_config_file` (already in `__init__`) is used for collection list reads; `_CONFIG_PATH = Path.home() / ".archon" / "config.toml"` is used for write-back (same as CLI). Alternatively the toolkit can derive it from `self._config_file or Path("~/.archon/config.toml").expanduser()`.

### Config freshness for collection-management handlers
Collection-management handlers (Tasks 3.1, 3.2, 3.3, 3.4, 3.5) must NOT rely on `toolkit._config` for collection data, as `toolkit._config` may be stale after prior write-back operations. These handlers call `cfg = load_config()` from `archon.config.loader` at the start of each handler and use `cfg` for all config reads within that handler invocation.

### New tool schemas (summary)

| Tool | Key inputs | Returns |
|---|---|---|
| `rag_status` | — | JSON `{running, pid, collections:[{name, doc_count, chunk_count}]}` |
| `rag_start` | — | string success/error |
| `rag_stop` | — | string success/error |
| `rag_ingest` | `path?`, `collection?` | JSON `{ok, errors}` |
| `rag_sync` | — | JSON `{added, removed, unchanged, errors}` |
| `rag_collection_list` | — | JSON array `[{name, path, doc_count, chunk_count, status}]` |
| `rag_collection_add` | `path` | string success/warning/error |
| `rag_collection_remove` | `path`, `force?` | string success/error |
| `rag_collection_info` | `collection_name` | JSON CollectionMeta fields |
| `rag_collection_reindex` | `collection_name` | JSON `{ok, errors}` |
| `get_logs` | `lines?` (default 50), `date?` | string (log content) |
| `get_version` | — | string version |
| `archon_doctor` | — | JSON array `[{name, ok, detail}]` |

`get_config` schema change: `path` moves from `required` to optional (empty/absent → full dump).

---

## Tests

> **Mock strategy note**: All "RAG not available" tests use `patch('archon.ai.archon_toolkit_rag._RAG_AVAILABLE', False)` to simulate the unavailable-RAG state. See Architecture section for the module-level flag pattern.

- **test_rag_status_running** (unit): RAG service running → JSON with pid and collections list
- **test_rag_status_stopped** (unit): RAG service stopped → `{"running": false, "pid": null, "collections": []}`
- **test_rag_status_rag_unavailable** (unit): `patch('archon.ai.archon_toolkit_rag.get_rag_service', side_effect=ImportError)` → `"RAG not available"`
- **test_rag_status_disconnect_on_error** (unit): `store.list_collections()` raises → `store.disconnect()` is still called
- **test_rag_start_rag_unavailable** (unit): `_RAG_AVAILABLE=False` → `"RAG not available"`
- **test_rag_start_success** (unit): `get_rag_service().start()` returns 0 → success message
- **test_rag_start_failure** (unit): `get_rag_service().start()` returns non-zero → failure message
- **test_rag_start_disconnect_on_error** (unit): `start()` raises → handler returns error (no uncaught exception)
- **test_rag_stop_rag_unavailable** (unit): `_RAG_AVAILABLE=False` → `"RAG not available"`
- **test_rag_stop_success** (unit): `get_rag_service().stop()` returns 0 → success message
- **test_rag_stop_failure** (unit): `get_rag_service().stop()` returns non-zero → failure message
- **test_rag_stop_disconnect_on_error** (unit): `stop()` raises → handler returns error (no uncaught exception)
- **test_rag_ingest_service_running_blocked** (unit): RAG service running → returns error string
- **test_rag_ingest_success** (unit): service stopped, valid path → JSON `{ok:N, errors:0}`
- **test_rag_ingest_default_path** (unit): no path argument → uses history sessions dir
- **test_rag_ingest_custom_collection** (unit): `collection` arg overrides derived name
- **test_rag_ingest_disconnect_on_error** (unit): `pipeline.ingest_directory` raises → `pipeline.store.disconnect()` is still called
- **test_rag_sync_success** (unit): sync returns result with added/removed/unchanged/errors
- **test_rag_sync_no_config** (unit): `_config` is None → returns error
- **test_rag_sync_service_running_includes_warning** (unit): RAG service running → return JSON includes `{"warning": "RAG service is running — write conflicts are possible", ...}`
- **test_rag_sync_disconnect_on_error** (unit): `RagCollectionSync.sync()` raises → `pipeline.store.disconnect()` is still called
- **test_rag_collection_list_rag_unavailable** (unit): `_RAG_AVAILABLE=False` → `"RAG not available"`
- **test_rag_collection_list_no_config** (unit): `cfg.rag is None` → `"Configuration not available."`
- **test_rag_collection_list_empty** (unit): no collections → empty JSON array
- **test_rag_collection_list_with_data** (unit): collections from store + desired from config → correct status labels (`indexed`, `orphan (managed)`, `unmanaged`, `not yet indexed`)
- **test_rag_collection_list_unmanaged** (unit): collection exists in LanceDB but is in neither manifest nor config; assert `status="unmanaged"`
- **test_rag_collection_list_disconnect_on_error** (unit): `store.list_collections()` raises → `store.disconnect()` is still called
- **test_rag_collection_add_already_registered** (unit): path already in config → returns `"Already registered: ..."`
- **test_rag_collection_add_success** (unit): valid path → config updated, ingest called, success message
- **test_rag_collection_add_service_running_warns** (unit): RAG service is running → return value contains `"Warning"` AND `config_collections_append` was called AND `ingest_directory` was called — confirming operation was NOT blocked
- **test_rag_collection_add_disconnect_on_error** (unit): `pipeline.ingest_directory` raises → `pipeline.store.disconnect()` is still called
- **test_rag_collection_remove_not_found** (unit): path not in config → error message
- **test_rag_collection_remove_service_running_no_force** (unit): service running, `force=false` → error
- **test_rag_collection_remove_service_running_with_force** (unit): service running, `force=true` → proceeds
- **test_rag_collection_remove_success** (unit): drops table, removes from config, cleans manifest
- **test_rag_collection_remove_drop_fails_and_disconnects** (unit): `store.drop_collection` raises → response contains `"Drop failed:"` AND `store.disconnect()` is still called
- **test_rag_collection_info_found** (unit): collection exists → JSON with all meta fields
- **test_rag_collection_info_not_found** (unit): unknown name → error message
- **test_rag_collection_info_disconnect_on_error** (unit): `pipeline.get_collection_meta` raises → `pipeline.store.disconnect()` is still called
- **test_rag_ingest_rag_unavailable** (unit): `patch('archon.ai.archon_toolkit_rag.get_rag_service', side_effect=ImportError)` → `"RAG not available"`
- **test_rag_sync_rag_unavailable** (unit): same patch → `"RAG not available"`
- **test_rag_collection_add_rag_unavailable** (unit): same patch → `"RAG not available"`
- **test_rag_collection_remove_rag_unavailable** (unit): same patch → `"RAG not available"`
- **test_rag_collection_info_rag_unavailable** (unit): same patch → `"RAG not available"`
- **test_rag_collection_reindex_rag_unavailable** (unit): same patch → `"RAG not available"`
- **test_rag_collection_reindex_service_running** (unit): service running → error
- **test_rag_collection_reindex_not_in_config** (unit): collection not in config → error
- **test_rag_collection_reindex_success** (unit): valid collection → JSON `{ok:N, errors:0}`
- **test_rag_collection_reindex_disconnect_on_error** (unit): `pipeline.ingest_directory` raises → `pipeline.store.disconnect()` is still called
- **test_get_logs_default** (unit): 100-line file, no args → last 50 lines returned; reads via `collections.deque(log_path.open(...), maxlen=50)`
- **test_get_logs_with_lines** (unit): `lines=10` → last 10 lines
- **test_get_logs_with_date** (unit): `date="2026-01-15"` → reads `archon.2026-01-15.log`
- **test_get_logs_file_not_found** (unit): log file missing → error message
- **test_get_logs_date_invalid** (unit): invalid date format → error message
- **test_get_logs_config_none_uses_default_path** (unit): when `_config=None`, handler falls back to `~/.archon/logs/archon.log`; verify the correct path is read
- **test_get_version** (unit): returns version string from `get_version()`
- **test_archon_doctor_all_pass** (unit): all checks return `ok=True` → JSON array with all ok
- **test_archon_doctor_some_fail** (unit): some checks fail → JSON includes `ok=False` entries
- **test_get_config_empty_path_returns_all** (unit): `path=""` → full config dict (redacted)
- **test_get_config_missing_path_returns_all** (unit): `path` key absent → full config dict
- **test_get_config_path_still_works** (unit): `path="notifications.mode"` → existing behaviour unchanged
- **test_get_config_full_dump_redacts_sensitive** (unit): TOML has `[test_section] secret_key = "hidden"`; assert full dump redacts the value
- **test_get_config_empty_path_file_not_found** (unit): config file does not exist; call with empty path; assert returns `"Config file not found."`
- **test_run_checks_returns_list_of_check_results** (unit, `tests/test_diagnostics.py`): patch `urllib.request.urlopen`, monkeypatch `archon.diagnostics._ARCHON_HOME` to `tmp_path`, mock subprocess; verify return type is `list[CheckResult]` with expected fields `name`, `ok`, `detail`
- **test_run_checks_includes_all_check_functions** (unit, `tests/test_diagnostics.py`): same patches; use `inspect.getmembers` to find all `_check_*` functions in `archon.diagnostics`; hardcode `EXPECTED_COUNT = 9` (the 9 checks in `run_checks()` — `_check_health` is explicitly excluded, see Task 0.2); assert `len(result) == EXPECTED_COUNT` and assert `_check_health` is NOT in the result names
- **test_run_checks_handles_check_exception** (unit, `tests/test_diagnostics.py`): patch `urllib.request.urlopen`, monkeypatch `_ARCHON_HOME`, mock subprocess; patch one `_check_*` to raise; assert result has `ok=False` with error detail; other checks still appear

---

## Documentation update
- [ ] `Documentation/UserManual/user_manual.md`, section "Archon MCP Tools": add RAG tool descriptions and note that `archon_doctor` omits the async RAG health check
- [ ] `CLAUDE.md`, section `archon/ai/` `archon_toolkit.py`: update tool count and add RAG tool names; mention `archon_toolkit_rag.py` as the RAG standalone helper module (not a mixin)
- [ ] `CLAUDE.md`, section `archon/ai/`: add `archon/diagnostics.py` entry: "`diagnostics.py`: `CheckResult` dataclass + all `_check_*` functions + `run_checks() -> list[CheckResult]` — synchronous health checks shared by CLI and MCP toolkit"

---

## Task breakdown

### Phase 0 — Extract Shared Helpers (prerequisite for Phases 1–4)
> **Releasable**: after Task 0.2 — shared modules are in place; Phases 1–4 can proceed without layer violations

#### Task 0.1 — Extract collection config helpers to `archon/config/config_rw.py` and `archon/rag/sync.py`
- [x] **File**: `archon/config/config_rw.py` (existing), `archon/rag/sync.py` (existing), `archon/cli/rag_cmd.py` (update imports only)
- **Depends on**: nothing
- **Description**:
  - Move `_config_collections_append` and `_config_collections_remove` from `archon/cli/rag_cmd.py` into `archon/config/config_rw.py` as **public** functions: `config_collections_append(config_path, path)`, `config_collections_remove(config_path, path)`. Both functions must adopt the same `_file_lock(config_path)` pattern used by `set_config_value` in `config_rw.py`. **Threat model**: `_file_lock` uses `fcntl.flock` — a process-level file lock that protects against *concurrent OS processes* writing to `config.toml` at the same time (e.g., `archon config set` running in a terminal while an MCP tool call writes). It does NOT protect against concurrent async coroutines within the same process; however, both functions are entirely synchronous (no `await` calls), so they complete atomically within a single event loop tick and cannot be interleaved with other coroutines. The existing CLI code does not lock; add it here to match the cross-process safety already provided by `set_config_value`.
  - Move `_manifest_remove_entry` from `archon/cli/rag_cmd.py` into `archon/rag/sync.py` as a **public** function: `manifest_remove_entry(manifest_path: Path, col_name: str) -> None`. This keeps manifest logic co-located with the sync machinery that creates and reads it.
  - In `archon/cli/rag_cmd.py`: replace the three private function definitions with imports from their new locations (`archon.config.config_rw` and `archon.rag.sync`); keep the old private names as thin wrappers or rename all call sites to use the public names directly
  - No behaviour changes — just moving code to the appropriate shared modules
- **Releasable**: collection config helpers available without `cli` layer dependency; `rag_cmd.py` functionality preserved via imports
- **Tests (TDD)** — `tests/config/test_config_rw.py` (add new tests):
  - Unit: `test_config_collections_append_adds_path` — write a temp TOML; call `config_collections_append`; reload TOML; assert path present in `[rag] collections`
  - Unit: `test_config_collections_remove_removes_path` — write TOML with one path; call `config_collections_remove`; assert path gone
  - Unit: `test_config_collections_remove_noop_if_missing_section` — TOML without `[rag]` section; call succeeds without error
  - Unit: `test_config_collections_append_uses_file_lock` — patch `_file_lock` and verify it is called with the config path during append, confirming cross-process write safety matches `set_config_value`
  - Unit: `test_config_collections_remove_uses_file_lock` — same as above for remove
  - Unit: `test_rag_cmd_functions_importable_after_extraction` — import `config_collections_append` and `config_collections_remove` from `archon.config.config_rw` and verify they are callable; separately import `manifest_remove_entry` from `archon.rag.sync` and verify it is callable. This is the regression check that confirms extraction did not break public contracts.
- **Tests (TDD)** — `tests/rag/test_sync.py` (add new tests for the extracted function):
  - Unit: `test_manifest_remove_entry_removes_key` — write JSON manifest; call `manifest_remove_entry`; reload; assert key gone
  - Unit: `test_manifest_remove_entry_noop_if_missing` — nonexistent manifest path; call succeeds without error
  - Checkpoint: `uv run pytest tests/config/test_config_rw.py tests/rag/test_sync.py tests/cli/ --no-cov`

#### Task 0.2 — Extract diagnostics to `archon/diagnostics.py`
- [x] **File**: `archon/diagnostics.py` (new), `archon/cli/doctor.py` (update imports only)
- **Depends on**: nothing
- **Description**:
  - Create `archon/diagnostics.py` containing: `CheckResult` dataclass, the `_ARCHON_HOME` constant (moved from `doctor.py` — it is a non-obvious shared dependency used by six of the extracted `_check_*` functions), the synchronous `_check_*` functions that require no external config argument (`_check_git`, `_check_uv`, `_check_python`, `_check_claude`, `_check_env_file`, `_check_config_file`, `_check_logs_dir`, `_check_health`, `_check_app_dir`, `_check_bot_token`), and `def run_checks() -> list[CheckResult]`. `_check_rag_server` (requires a `cfg` argument) and `_check_rag_health` (async) remain in `doctor.py`.
  - **`run_checks()` specification**:
    - Calls each synchronous `_check_*` function in sequence
    - Does NOT call `_check_rag_server` (requires a `cfg` argument and lancedb import) or `_check_rag_health` (async, network I/O) — these remain CLI-only
    - If any individual check raises an unexpected exception, that check's result is `CheckResult(name=<fn_name>, ok=False, detail=str(exc))` — never lets one bad check abort the entire list
    - Returns `list[CheckResult]`
  - **`run_checks()` includes these checks** (in order): `_check_git`, `_check_uv`, `_check_python`, `_check_claude`, `_check_env_file`, `_check_config_file`, `_check_logs_dir`, `_check_app_dir`, `_check_bot_token`. **`_check_health` is excluded**: it calls `localhost:{port}/health` to check whether Archon is running — but since `run_checks()` is only ever called from within the running daemon, this check is tautologically always `ok=True` and provides no diagnostic value in the MCP context.
  - In `archon/cli/doctor.py`: remove the moved definitions; import `CheckResult`, `run_checks`, `_ARCHON_HOME`, and the individual `_check_*` functions needed by `run_doctor()` from `archon.diagnostics`; `_check_rag_server` and `_check_rag_health` remain in `doctor.py` (they have CLI-specific dependencies or are async)
  - **Note**: `run_checks()` in `archon/diagnostics.py` contains only synchronous checks. The async `_check_rag_health` is NOT included in `run_checks()` — it is omitted from the MCP tool result. This is documented as a known gap in the Known Limitations section above.
  - **Critical**: All `test_run_checks_*` tests must patch `urllib.request.urlopen` (used by `_check_bot_token` to call `api.telegram.org`) to prevent live network calls. Additionally, `_ARCHON_HOME` must be monkeypatched to a `tmp_path` to prevent reading the real user's `~/.archon/config.toml` and `.env` files. (`_check_health` is excluded from `run_checks()` and does not need to be patched here.)
  - **Note on `_check_health`**: it is moved to `archon/diagnostics.py` alongside the other checks, but `run_checks()` does not call it. `archon/cli/doctor.py` must explicitly import `_check_health` from `archon.diagnostics` (in addition to the other functions) to keep it in `run_doctor()`. This import must be listed in the `doctor.py` update step.
- **Releasable**: `run_checks()` available from `archon.diagnostics`; `doctor.py` continues to work
- **Tests (TDD)** — `tests/test_diagnostics.py` (new file):
  - Unit: `test_run_checks_returns_list_of_check_results` — patch `urllib.request.urlopen`, monkeypatch `archon.diagnostics._ARCHON_HOME` to `tmp_path`, mock subprocess calls; verify return type is `list[CheckResult]` with expected fields (`name`, `ok`, `detail`) on each item
  - Unit: `test_run_checks_includes_all_check_functions` — same patches; hardcode `EXPECTED_COUNT = 9`; call `run_checks()`; assert `len(result) == EXPECTED_COUNT`; additionally assert that no result has `name == "_check_health"` (confirming its explicit exclusion). Comment in the test: `# 9 = all sync checks except _check_health (tautological inside daemon)`.
  - Unit: `test_run_checks_handles_check_exception` — patch `urllib.request.urlopen`, monkeypatch `_ARCHON_HOME`, mock subprocess; patch one `_check_*` function to raise; assert result has `ok=False` with the error detail; other checks still appear
  - Unit: `test_check_result_dataclass_fields` — `CheckResult("git", True, "git 2.x")` has correct attribute access (no network/fs patches needed for this test)
  - Checkpoint: `uv run pytest tests/test_diagnostics.py --no-cov`

---

### Phase 1 — RAG Service Lifecycle
> **Releasable**: after Task 1.3 — Claude can start, stop, and query RAG service status via MCP

#### Task 1.1 — `rag_status` tool
- [x] **File**: `archon/ai/archon_toolkit_rag.py` (new), `archon/ai/archon_toolkit.py` (register via `_register_rag_tools()`)
- **Depends on**: Task 0.1
- **Description**:
  - Create `archon/ai/archon_toolkit_rag.py` as a standalone helper module (NOT a mixin or base class)
  - **Circular-import guard**: `archon_toolkit.py` will import from `archon_toolkit_rag.py`, and `archon_toolkit_rag.py` type-hints `toolkit: ArchonToolkit`. Use `from __future__ import annotations` at the top of `archon_toolkit_rag.py` AND guard the `ArchonToolkit` import under `TYPE_CHECKING`: `from typing import TYPE_CHECKING; if TYPE_CHECKING: from archon.ai.archon_toolkit import ArchonToolkit`. At runtime `ArchonToolkit` is never imported by `archon_toolkit_rag.py`, eliminating the circular dependency.
  - Add `_RAG_STATUS_SCHEMA: dict[str, Any]` (no input properties)
  - Add module-level `async def _handle_rag_status(toolkit: ArchonToolkit, arguments: dict[str, Any], *, user_id: int | None = None) -> str`
  - Lazy-import `from archon.platform import get_rag_service` inside the handler; catch `ImportError` → return `"RAG not available"`
  - Call `info = await asyncio.to_thread(get_rag_service().status)` — wraps the blocking `subprocess.run()` call
  - If not running: return `json.dumps({"running": False, "pid": None, "collections": []})`
  - If running: lazy-import `from archon.rag.store import RagStore`; create `RagStore(cfg.rag.db_path)` (cfg from `toolkit._config`); if `toolkit._config` is None return `json.dumps({"running": True, "pid": info.pid, "collections": []})`
  - `await store.connect()` → `await store.list_collections()` → `await store.disconnect()` in try/finally; catch all exceptions and return `collections: []` with error note
  - Return `json.dumps({"running": True, "pid": info.pid, "collections": [{"name": c.name, "doc_count": c.doc_count, "chunk_count": c.chunk_count} for c in collections]})`
  - Add module-level `def _register_rag_tools(toolkit: ArchonToolkit) -> None` that calls `toolkit.register_tool("rag_status", _RAG_STATUS_SCHEMA, functools.partial(_handle_rag_status, toolkit))` (and all subsequent RAG tools)
  - In `archon/ai/archon_toolkit.py`: at the end of `__init__`, call `_register_rag_tools(self)` (imported from `archon_toolkit_rag`)
- **Releasable**: `rag_status` callable via MCP after this task
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_rag.py`:
  - Unit: `test_rag_status_stopped` — mock `asyncio.to_thread(get_rag_service().status)` → `running=False`; assert JSON `running: false, collections: []`
  - Unit: `test_rag_status_running_with_collections` — mock service running + `store.list_collections()` returns 2 items; assert JSON contains them
  - Unit: `test_rag_status_rag_unavailable` — `patch('archon.ai.archon_toolkit_rag._RAG_AVAILABLE', False)`; assert returns `"RAG not available"`
  - Unit: `test_rag_status_store_error` — store raises on `list_collections`; assert returns running=True with empty collections
  - Unit: `test_rag_status_disconnect_on_error` — `store.list_collections()` raises; assert `store.disconnect()` is still called
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_rag.py -k "status" --no-cov`

#### Task 1.2 — `rag_start` tool
- [x] **File**: `archon/ai/archon_toolkit_rag.py`
- **Depends on**: Task 1.1 (file exists)
- **Description**:
  - Add `_RAG_START_SCHEMA: dict[str, Any]` (no input properties; description: "Start the RAG search service.")
  - Add module-level `async def _handle_rag_start(toolkit: ArchonToolkit, arguments: dict[str, Any], *, user_id: int | None = None) -> str`
  - Lazy-import `get_rag_service`; catch `ImportError` → return `"RAG not available"`
  - Call `rc = await asyncio.to_thread(get_rag_service().start)` — wraps the blocking `subprocess.run()` call
  - Return `"RAG service started."` if `rc == 0` else `f"RAG service start failed (exit code {rc})."`
  - Register in `_register_rag_tools()`
- **Releasable**: `rag_start` callable via MCP
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_rag.py`:
  - Unit: `test_rag_start_success` — mock `asyncio.to_thread(get_rag_service().start)` returns 0; assert `"RAG service started."`
  - Unit: `test_rag_start_failure` — mock returns 1; assert contains `"failed"`
  - Unit: `test_rag_start_disconnect_on_error` — `start()` raises; handler returns error (no uncaught exception propagates)
  - Unit: `test_rag_start_rag_unavailable` — `patch('archon.ai.archon_toolkit_rag._RAG_AVAILABLE', False)`; assert returns `"RAG not available"`
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_rag.py -k "rag_start" --no-cov`

#### Task 1.3 — `rag_stop` tool
- [x] **File**: `archon/ai/archon_toolkit_rag.py`
- **Depends on**: Task 1.1 (file exists)
- **Description**:
  - Add `_RAG_STOP_SCHEMA: dict[str, Any]` (no input properties; description: "Stop the RAG search service.")
  - Add module-level `async def _handle_rag_stop(toolkit: ArchonToolkit, arguments: dict[str, Any], *, user_id: int | None = None) -> str`
  - Lazy-import `get_rag_service`; catch `ImportError` → return `"RAG not available"`
  - Call `rc = await asyncio.to_thread(get_rag_service().stop)` — wraps the blocking `subprocess.run()` call
  - Return `"RAG service stopped."` if `rc == 0` else `f"RAG service stop failed (exit code {rc})."`
  - Register in `_register_rag_tools()`
- **Releasable**: `rag_stop` callable via MCP
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_rag.py`:
  - Unit: `test_rag_stop_success` — mock `asyncio.to_thread(get_rag_service().stop)` returns 0; assert `"RAG service stopped."`
  - Unit: `test_rag_stop_failure` — mock returns 2; assert contains `"failed"`
  - Unit: `test_rag_stop_disconnect_on_error` — `stop()` raises; handler returns error (no uncaught exception propagates)
  - Unit: `test_rag_stop_rag_unavailable` — `patch('archon.ai.archon_toolkit_rag._RAG_AVAILABLE', False)`; assert returns `"RAG not available"`
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_rag.py -k "rag_stop" --no-cov`

---

### Phase 2 — RAG Data Operations
> **Releasable**: after Task 2.2 — Claude can ingest directories and sync all configured collections

#### Task 2.1 — `rag_ingest` tool
- [x] **File**: `archon/ai/archon_toolkit_rag.py`
- **Depends on**: Task 1.1
- **Description**:
  - Add `_RAG_INGEST_SCHEMA: dict[str, Any]` with optional `path` (string, directory to ingest; default: history sessions dir) and optional `collection` (string, target collection name)
  - Add module-level `async def _handle_rag_ingest(toolkit: ArchonToolkit, arguments: dict[str, Any], *, user_id: int | None = None) -> str`
  - Guard: if `toolkit._config` is None → return `"Configuration not available."`
  - Lazy-import `get_rag_service`, `create_pipeline`, `path_to_collection_name` from `archon.rag.sync`; catch `ImportError` → `"RAG not available"`
  - Guard: `status = await asyncio.to_thread(get_rag_service().status)`; `if status.running: return "Error: RAG service is running. Stop it first (rag_stop) to avoid data races."`
  - Resolve path: `Path(arguments["path"])` if provided, else `Path(toolkit._config.history.directory).expanduser() / "sessions"`
  - Resolve collection: `arguments.get("collection") or path_to_collection_name(str(resolved_path))`
  - `pipeline = create_pipeline(toolkit._config.rag)`
  - `await pipeline.store.connect()` → `results = await pipeline.ingest_directory(resolved_path, collection)` → `await pipeline.store.disconnect()` in try/finally
  - **Event loop note**: `pipeline.ingest_directory()` is async but contains CPU-bound embedding computations that will block the event loop for the duration of ingestion. Do NOT attempt to wrap this in `asyncio.to_thread` — `to_thread` expects a synchronous callable and will fail with an async coroutine. For MVP, this is documented as a known limitation. Add a `# TODO: route through BackgroundAgentManager as a proper background task` comment on the `await pipeline.ingest_directory(...)` call.
  - Count `ok` and `errors` from results; return `json.dumps({"ok": ok, "errors": errors, "collection": collection})`
  - Catch all exceptions → return `f"Ingest failed: {exc}"`
  - Register in `_register_rag_tools()`
- **Releasable**: `rag_ingest` callable via MCP
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_rag.py`:
  - Unit: `test_rag_ingest_service_running_blocked` — mock service running; assert error string returned
  - Unit: `test_rag_ingest_success` — mock service stopped, mock `pipeline.ingest_directory` returns 3 ok results; assert JSON `{ok:3, errors:0}`
  - Unit: `test_rag_ingest_default_path` — no `path` arg; assert path arg to `ingest_directory` ends with `sessions`
  - Unit: `test_rag_ingest_custom_collection` — `collection="my_col"` arg; assert passed through
  - Unit: `test_rag_ingest_no_config` — `_config=None`; assert `"Configuration not available."`
  - Unit: `test_rag_ingest_exception` — pipeline raises; assert response contains `"Ingest failed:"`
  - Unit: `test_rag_ingest_disconnect_on_error` — `pipeline.ingest_directory` raises; assert `pipeline.store.disconnect()` is still called
  - Unit: `test_rag_ingest_rag_unavailable` — `patch('archon.ai.archon_toolkit_rag._RAG_AVAILABLE', False)`; assert returns `"RAG not available"`
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_rag.py -k "ingest" --no-cov`

#### Task 2.2 — `rag_sync` tool
- [x] **File**: `archon/ai/archon_toolkit_rag.py`
- **Depends on**: Task 1.1
- **Description**:
  - Add `_RAG_SYNC_SCHEMA: dict[str, Any]` (no inputs; description: "Reconcile all configured RAG collections with LanceDB — adds new files, removes deleted ones.")
  - Add module-level `async def _handle_rag_sync(toolkit: ArchonToolkit, arguments: dict[str, Any], *, user_id: int | None = None) -> str`
  - Guard: `toolkit._config is None` → `"Configuration not available."`
  - Lazy-import `create_pipeline`, `RagCollectionSync`, `get_rag_service`; catch `ImportError` → `"RAG not available"`
  - **Service-running guard**: `status = await asyncio.to_thread(get_rag_service().status)`; if running, include `"warning": "RAG service is running — write conflicts are possible"` in the return JSON (not a hard block — mirrors CLI `_run_sync` warning behaviour)
  - `pipeline = create_pipeline(toolkit._config.rag)`
  - `await pipeline.store.connect()` → `result = await RagCollectionSync(pipeline).sync(toolkit._config.rag.collections)` → `await pipeline.store.disconnect()` in try/finally
  - **Event loop note**: `RagCollectionSync.sync()` is async but contains CPU-bound embedding computations that will block the event loop for the duration of the sync. Do NOT attempt to wrap this in `asyncio.to_thread` — `to_thread` expects a synchronous callable and will fail with an async coroutine. For MVP, this is documented as a known limitation. Add a `# TODO: route through BackgroundAgentManager as a proper background task` comment on the `await RagCollectionSync(pipeline).sync(...)` call.
  - Return `json.dumps({"added": list(result.added), "removed": list(result.removed), "unchanged": len(result.unchanged), "errors": list(result.errors)})` — if service was running, merge in `"warning": "..."` key
  - Catch exceptions → `f"Sync failed: {exc}"`
  - Register in `_register_rag_tools()`
- **Releasable**: `rag_sync` callable via MCP
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_rag.py`:
  - Unit: `test_rag_sync_success` — mock `RagCollectionSync.sync()` returns SyncResult with 2 added, 1 removed, 5 unchanged, 0 errors; assert JSON
  - Unit: `test_rag_sync_no_config` — `_config=None`; assert `"Configuration not available."`
  - Unit: `test_rag_sync_with_errors` — sync returns 1 error; assert JSON `errors` list non-empty
  - Unit: `test_rag_sync_service_running_includes_warning` — mock service running; assert returned JSON has `"warning"` key with the expected text
  - Unit: `test_rag_sync_disconnect_on_error` — `RagCollectionSync.sync()` raises; assert `pipeline.store.disconnect()` is still called
  - Unit: `test_rag_sync_rag_unavailable` — `patch('archon.ai.archon_toolkit_rag._RAG_AVAILABLE', False)`; assert returns `"RAG not available"`
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_rag.py -k "sync" --no-cov`

---

### Phase 3 — RAG Collection Management
> **Releasable**: after each task individually — each collection tool is independently usable once registered

#### Task 3.1 — `rag_collection_list` tool
- [x] **File**: `archon/ai/archon_toolkit_rag.py`
- **Depends on**: Task 1.1
- **Description**:
  - Add `_RAG_COLLECTION_LIST_SCHEMA: dict[str, Any]` (no inputs; description: "List all RAG collections: their source path, doc/chunk counts, and sync status.")
  - Add module-level `async def _handle_rag_collection_list(toolkit: ArchonToolkit, arguments: dict[str, Any], *, user_id: int | None = None) -> str`
  - **Load fresh config**: `cfg = load_config()` from `archon.config.loader` — do NOT use `toolkit._config` for collection management handlers since `toolkit._config` may be stale after prior write-back operations
  - Guard: `if not _RAG_AVAILABLE: return "RAG not available"` (module-level flag)
  - Guard: `if cfg.rag is None: return "Configuration not available."` — handles the case where the `[rag]` section is absent from config
  - Load manifest from `Path(cfg.rag.db_path).expanduser() / "sync_manifest.json"` (same logic as CLI `_run_collection_list`)
  - Build `desired: dict[str, str]` from `cfg.rag.collections`
  - `store = RagStore(cfg.rag.db_path)` → `await store.connect()` → `collections = await store.list_collections()` → `await store.disconnect()` in try/finally
  - Build result list combining LanceDB collections with status labels: `"indexed"` (in both store and `desired`), `"orphan (managed)"` (in manifest but not `desired`), `"unmanaged"` (in store but in neither manifest nor `desired`), `"not yet indexed"` (in `desired` but not in store)
  - Return `json.dumps([{...} for each])` — fields: `name`, `path`, `doc_count`, `chunk_count`, `status`
  - Register in `_register_rag_tools()`
- **Releasable**: `rag_collection_list` callable via MCP
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_rag.py`:
  - Unit: `test_rag_collection_list_empty` — no collections in store or config; assert `[]`
  - Unit: `test_rag_collection_list_indexed` — collection in both store and config; assert `status="indexed"`
  - Unit: `test_rag_collection_list_orphan` — collection in manifest but not config; assert `status="orphan (managed)"`
  - Unit: `test_rag_collection_list_not_yet_indexed` — collection in config but not store; assert `status="not yet indexed"`
  - Unit: `test_rag_collection_list_unmanaged` — collection exists in LanceDB but is in neither manifest nor config; assert `status="unmanaged"`
  - Unit: `test_rag_collection_list_disconnect_on_error` — `store.list_collections()` raises; assert `store.disconnect()` is still called
  - Unit: `test_rag_collection_list_rag_unavailable` — `patch('archon.ai.archon_toolkit_rag._RAG_AVAILABLE', False)`; assert returns `"RAG not available"`
  - Unit: `test_rag_collection_list_no_config` — `load_config()` returns config where `cfg.rag is None`; assert returns `"Configuration not available."`
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_rag.py -k "collection_list" --no-cov`

#### Task 3.2 — `rag_collection_add` tool
- [x] **File**: `archon/ai/archon_toolkit_rag.py`
- **Depends on**: Task 3.1
- **Description**:
  - Add `_RAG_COLLECTION_ADD_SCHEMA: dict[str, Any]` with required `path` (string, filesystem path to add as a collection)
  - Add module-level `async def _handle_rag_collection_add(toolkit: ArchonToolkit, arguments: dict[str, Any], *, user_id: int | None = None) -> str`
  - **Load fresh config**: `cfg = load_config()` from `archon.config.loader` — do NOT use `toolkit._config` for collection management handlers since `toolkit._config` may be stale after prior write-back operations
  - Lazy-imports + `ImportError` guard
  - Resolve `path = Path(arguments["path"]).expanduser().resolve()`
  - Check if already registered: iterate `cfg.rag.collections`; return `f"Already registered: {path}"` if duplicate
  - **Service-running guard (non-blocking)**: `status = await asyncio.to_thread(get_rag_service().status)`; if running, set `warning = 'Warning: RAG service is running — write conflicts are possible.'` but DO NOT return. Continue to config write and ingest. Include the warning in the final response: `f'{warning} Collection added and indexed: {path}'`.
  - Determine `config_file = toolkit._config_file or Path("~/.archon/config.toml").expanduser()`
  - Append path to config: call `config_collections_append(config_file, arguments["path"])` from `archon.config.config_rw` (extracted in Task 0.1)
  - Determine collection name via manifest lookup (`manifest_lookup_by_path`) then fallback to `path_to_collection_name(arguments["path"])`
  - `pipeline = create_pipeline(cfg.rag)` → `await pipeline.store.connect()` → `await pipeline.ingest_directory(resolved, col_name)` → `await pipeline.store.disconnect()` in try/finally
  - Return `f"Collection added and indexed: {arguments['path']}"` on success (or with warning prefix if service was running), or `f"Ingest error: {exc}"` on failure
  - Register in `_register_rag_tools()`
- **Releasable**: `rag_collection_add` callable via MCP
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_rag.py`:
  - Unit: `test_rag_collection_add_already_registered` — path already in `cfg.rag.collections`; assert `"Already registered:"`
  - Unit: `test_rag_collection_add_success` — valid new path; assert config write called + ingest called + success message
  - Unit: `test_rag_collection_add_ingest_error` — ingest raises; assert response contains `"Ingest error:"`
  - Unit: `test_rag_collection_add_service_running_warns` — mock service running; assert return value contains `"Warning"` AND verify that `config_collections_append` was called AND `ingest_directory` was called — confirming the operation was NOT blocked
  - Unit: `test_rag_collection_add_disconnect_on_error` — `pipeline.ingest_directory` raises; assert `pipeline.store.disconnect()` is still called
  - Unit: `test_rag_collection_add_rag_unavailable` — `patch('archon.ai.archon_toolkit_rag._RAG_AVAILABLE', False)`; assert returns `"RAG not available"`
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_rag.py -k "collection_add" --no-cov`

#### Task 3.3 — `rag_collection_remove` tool
- [x] **File**: `archon/ai/archon_toolkit_rag.py`
- **Depends on**: Task 3.1
- **Description**:
  - Add `_RAG_COLLECTION_REMOVE_SCHEMA: dict[str, Any]` with required `path` (string) and optional `force` (boolean, default `false`)
  - Add module-level `async def _handle_rag_collection_remove(toolkit: ArchonToolkit, arguments: dict[str, Any], *, user_id: int | None = None) -> str`
  - **Load fresh config**: `cfg = load_config()` from `archon.config.loader` — do NOT use `toolkit._config` for collection management handlers since `toolkit._config` may be stale after prior write-back operations
  - Lazy-imports + `ImportError` guard
  - Resolve path; check if registered in `cfg.rag.collections` → `f"Error: not in collections: {path}"` if not found
  - Determine `col_name` via manifest then fallback
  - Check RAG service: `status = await asyncio.to_thread(get_rag_service().status)`; if running and not `force=True` → `"Error: RAG service is running. Use force=true to remove anyway."`
  - `store = RagStore(cfg.rag.db_path)` → `await store.connect()` → `await store.drop_collection(col_name)` (ignore `KeyError`) → `await store.disconnect()` in try/finally
  - On drop success: call `config_collections_remove(config_file, arguments["path"])` from `archon.config.config_rw` (extracted in Task 0.1) and `manifest_remove_entry(manifest_path, col_name)` imported from `archon.rag.sync` (moved there in Task 0.1)
  - Return `f"Collection removed: {arguments['path']}"` or `f"Drop failed: {exc}"`
  - Register in `_register_rag_tools()`
- **Releasable**: `rag_collection_remove` callable via MCP
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_rag.py`:
  - Unit: `test_rag_collection_remove_not_registered` — path not in config; assert error message
  - Unit: `test_rag_collection_remove_service_running_no_force` — service running, `force=false`; assert error
  - Unit: `test_rag_collection_remove_service_running_force` — service running, `force=true`; proceeds to drop
  - Unit: `test_rag_collection_remove_success` — drop succeeds; config and manifest updated; assert success message
  - Unit: `test_rag_collection_remove_drop_fails_and_disconnects` — `store.drop_collection` raises; assert response contains `"Drop failed:"` **AND** assert `store.disconnect()` is still called (verifies both the error message and the try/finally cleanup in a single test)
  - Unit: `test_rag_collection_remove_rag_unavailable` — `patch('archon.ai.archon_toolkit_rag._RAG_AVAILABLE', False)`; assert returns `"RAG not available"`
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_rag.py -k "collection_remove" --no-cov`

#### Task 3.4 — `rag_collection_info` tool
- [x] **File**: `archon/ai/archon_toolkit_rag.py`
- **Depends on**: Task 3.1
- **Description**:
  - Add `_RAG_COLLECTION_INFO_SCHEMA: dict[str, Any]` with required `collection_name` (string)
  - Add module-level `async def _handle_rag_collection_info(toolkit: ArchonToolkit, arguments: dict[str, Any], *, user_id: int | None = None) -> str`
  - **Load fresh config**: `cfg = load_config()` from `archon.config.loader`; guard: if `cfg.rag` not configured → `"Configuration not available."`. Do NOT use `toolkit._config` — use `load_config()` for config freshness consistency with other collection-management handlers.
  - Lazy-imports + `ImportError` guard
  - `pipeline = create_pipeline(cfg.rag)` → `await pipeline.store.connect()` → `meta = await pipeline.get_collection_meta(col_name)` → `await pipeline.store.disconnect()` in try/finally
  - If `meta is None` → `f"Error: collection {col_name!r} not found."`
  - Return `json.dumps({"name": meta.name, "description": meta.description, "doc_count": meta.doc_count, "chunk_count": meta.chunk_count, "embedding_model": meta.embedding_model, "centroid": meta.centroid is not None, "last_indexed": meta.last_indexed.isoformat() if meta.last_indexed else None})`
  - Register in `_register_rag_tools()`
- **Releasable**: `rag_collection_info` callable via MCP
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_rag.py`:
  - Unit: `test_rag_collection_info_found` — mock returns CollectionMeta; assert JSON has all fields
  - Unit: `test_rag_collection_info_not_found` — `get_collection_meta` returns None; assert error message
  - Unit: `test_rag_collection_info_store_error` — pipeline raises; assert exception message returned
  - Unit: `test_rag_collection_info_disconnect_on_error` — when `pipeline.get_collection_meta` raises an exception, assert `pipeline.store.disconnect()` was still called
  - Unit: `test_rag_collection_info_rag_unavailable` — `patch('archon.ai.archon_toolkit_rag._RAG_AVAILABLE', False)`; assert returns `"RAG not available"`
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_rag.py -k "collection_info" --no-cov`

#### Task 3.5 — `rag_collection_reindex` tool
- [x] **File**: `archon/ai/archon_toolkit_rag.py`
- **Depends on**: Task 3.1
- **Description**:
  - Add `_RAG_COLLECTION_REINDEX_SCHEMA: dict[str, Any]` with required `collection_name` (string; description: "Force full re-ingest of a collection, bypassing change thresholds.")
  - Add module-level `async def _handle_rag_collection_reindex(toolkit: ArchonToolkit, arguments: dict[str, Any], *, user_id: int | None = None) -> str`
  - **Load fresh config**: `cfg = load_config()` from `archon.config.loader` — do NOT use `toolkit._config` for collection management handlers since `toolkit._config` may be stale after prior write-back operations
  - Lazy-imports + `ImportError` guard
  - Guard: `status = await asyncio.to_thread(get_rag_service().status)`; `if status.running: return "Error: RAG service is running. Stop it first (rag_stop)."`
  - Find source path: iterate `cfg.rag.collections` looking for `path_to_collection_name(raw) == col_name`; if not found → `f"Error: collection {col_name!r} not found in config."`
  - `pipeline = create_pipeline(cfg.rag)` → `await pipeline.store.connect()` → `results = await pipeline.ingest_directory(resolved, col_name, force_regenerate_description=True)` → `await pipeline.store.disconnect()` in try/finally
  - Count ok/errors; return `json.dumps({"ok": ok, "errors": errors})`
  - Register in `_register_rag_tools()`
- **Releasable**: `rag_collection_reindex` callable via MCP
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_rag.py`:
  - Unit: `test_rag_collection_reindex_service_running` — service running; assert error
  - Unit: `test_rag_collection_reindex_not_in_config` — col not in `cfg.rag.collections`; assert error
  - Unit: `test_rag_collection_reindex_success` — valid collection; assert `ingest_directory` called with `force_regenerate_description=True`; JSON returned
  - Unit: `test_rag_collection_reindex_disconnect_on_error` — `pipeline.ingest_directory` raises; assert `pipeline.store.disconnect()` is still called
  - Unit: `test_rag_collection_reindex_rag_unavailable` — `patch('archon.ai.archon_toolkit_rag._RAG_AVAILABLE', False)`; assert returns `"RAG not available"`
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_rag.py -k "reindex" --no-cov`

---

### Phase 4 — Daemon Observability
> **Releasable**: after each task individually

#### Task 4.1 — `get_logs` tool
- [x] **File**: `archon/ai/archon_toolkit.py`
- **Depends on**: nothing
- **Description**:
  - Add `_GET_LOGS_SCHEMA: dict[str, Any]` with optional `lines` (integer, default 50, description: "Number of lines to return from the tail of the log") and optional `date` (string YYYY-MM-DD, description: "Read archived log for this date instead of current log")
  - Add `async def _handle_get_logs(self, arguments: dict[str, Any], *, user_id: int | None = None) -> str`
  - Determine log path: read `self._config.logging.log_file` if `_config` is not None, else fall back to `Path.home() / ".archon" / "logs" / "archon.log"`
  - If `date` argument provided: validate format with `re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)`; if invalid return `"Invalid date format: expected YYYY-MM-DD."`; derive path as `log_path.parent / f"archon.{date}.log"`
  - `lines_count = max(1, min(1000, int(arguments.get("lines", 50))))`
  - Read file efficiently using deque (no subprocess, no full file load):
    ```python
    import collections
    with log_path.open(encoding="utf-8", errors="replace") as f:
        lines_deque = collections.deque(f, maxlen=lines_count)
    return "\n".join(lines_deque)
    ```
  - If `FileNotFoundError` → return `f"Log file not found: {log_path}"`
  - Register: `self.register_tool("get_logs", _GET_LOGS_SCHEMA, self._handle_get_logs)`
- **Releasable**: `get_logs` callable via MCP
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_observability.py`:
  - Unit: `test_get_logs_default` — 100-line file, no args → last 50 lines returned; verify reading uses `collections.deque` semantics (only last N lines, not entire file content)
  - Unit: `test_get_logs_custom_lines` — `lines=10` → last 10 lines
  - Unit: `test_get_logs_with_date` — `date="2026-01-15"` → reads `archon.2026-01-15.log`
  - Unit: `test_get_logs_file_not_found` — log path missing → error string contains `"not found"`
  - Unit: `test_get_logs_invalid_date` — `date="not-a-date"` → `"Invalid date format:"`
  - Unit: `test_get_logs_lines_clamped` — `lines=9999` → clamped to 1000
  - Unit: `test_get_logs_config_none_uses_default_path` — `_config=None`; assert handler uses `~/.archon/logs/archon.log` as the log path
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_observability.py -k "logs" --no-cov`

#### Task 4.2 — `get_version` tool
- [x] **File**: `archon/ai/archon_toolkit.py`
- **Depends on**: nothing
- **Description**:
  - Add `_GET_VERSION_SCHEMA: dict[str, Any]` (no inputs; description: "Return the installed Archon version string.")
  - Add `async def _handle_get_version(self, arguments: dict[str, Any], *, user_id: int | None = None) -> str`
  - Import `from archon.version import get_version` (not lazy — this is a core module, always available)
  - Return `get_version()`
  - Register: `self.register_tool("get_version", _GET_VERSION_SCHEMA, self._handle_get_version)`
- **Releasable**: `get_version` callable via MCP
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_observability.py`:
  - Unit: `test_get_version_returns_string` — mock `get_version` to return `"26.3.1"`; assert result equals `"26.3.1"`
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_observability.py -k "version" --no-cov`

#### Task 4.3 — `archon_doctor` tool
- [x] **File**: `archon/ai/archon_toolkit.py`
- **Depends on**: Task 0.2
- **Description**:
  - Add `_ARCHON_DOCTOR_SCHEMA: dict[str, Any]` (no inputs; description: "Run pre-flight health checks and return results as JSON.")
  - Add `async def _handle_archon_doctor(self, arguments: dict[str, Any], *, user_id: int | None = None) -> str`
  - Import `from archon.diagnostics import run_checks` (extracted in Task 0.2; not from `archon.cli.doctor`)
  - Since `_check_*` functions call `subprocess.run()`, wrap the call in `await asyncio.to_thread(run_checks)` to avoid blocking the event loop
  - **Note**: `run_checks()` in `archon/diagnostics.py` contains only synchronous checks. The async RAG health check (`_check_rag_health`) is NOT included in `run_checks()` — it is omitted from the MCP tool result. This is documented as a known gap in the Known Limitations section.
  - Return `json.dumps([{"name": r.name, "ok": r.ok, "detail": r.detail} for r in results])`
  - Register: `self.register_tool("archon_doctor", _ARCHON_DOCTOR_SCHEMA, self._handle_archon_doctor)`
- **Releasable**: `archon_doctor` callable via MCP
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_observability.py`:
  - Unit: `test_archon_doctor_all_pass` — mock `run_checks` to return all-ok list; assert JSON array all `ok=true`
  - Unit: `test_archon_doctor_some_fail` — mock returns 1 failing check; assert JSON contains `ok=false` entry
  - Unit: `test_archon_doctor_returns_json_array` — assert result is parseable JSON list
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_observability.py -k "doctor" --no-cov`

#### Task 4.4 — Extend `get_config` for full-dump
- [ ] **File**: `archon/ai/archon_toolkit.py`
- **Depends on**: nothing
- **Description**:
  - Change `_GET_CONFIG_SCHEMA`: remove `"path"` from `"required"` list (keep it in `properties` as optional); update description to: "Read a config value by dot-notation path (e.g. 'notifications.mode'). If path is omitted or empty, returns the entire config. Sensitive paths are redacted."
  - Change `_handle_get_config`: after `path = str(arguments.get("path", ""))` and BEFORE the sensitive-path check, insert the empty-path early-return:
    - The empty-path early-return (`if not path:`) MUST be inserted AFTER `path = str(arguments.get("path", ""))` and BEFORE the sensitive-path check. This ensures empty path correctly returns the full dump and cannot be confused with a sensitive-key match.
    - If `path` is empty: load full TOML using `with open(config_file, "rb") as f: data = tomllib.load(f)`; apply `_redact_sensitive_dict(data)` recursively (the existing helper already handles dicts); return `json.dumps(data)`. Wrap in the same `try/except` block that guards `FileNotFoundError`, `TOMLDecodeError`, `PermissionError`.
  - Existing behaviour for non-empty `path` is unchanged
- **Releasable**: `get_config` with empty/missing path returns the full config; all existing uses unaffected
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_config.py` (existing file, add new tests):
  - Unit: `test_get_config_empty_path_returns_full_config` — write a temp TOML with 2 sections; call with `path=""`; assert returned dict has both sections
  - Unit: `test_get_config_missing_path_key_returns_full_config` — call with `arguments={}` (no `path` key); assert full dump returned
  - Unit: `test_get_config_full_dump_redacts_sensitive` — TOML has `[test_section] secret_key = "hidden"`; assert full dump redacts the value (use a synthetic section/key matching the sensitive regex, not `[access] bot_token` which lives in `.env` not `config.toml`)
  - Unit: `test_get_config_existing_path_still_works` — call with `path="notifications.mode"`; existing behaviour unchanged
  - Unit: `test_get_config_empty_path_file_not_found` — config file does not exist; call with empty `path`; assert returns `"Config file not found."`
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_config.py --no-cov`
