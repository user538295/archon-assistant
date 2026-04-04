# REFACTOR-001 — Rename `rag` → `search` throughout the codebase
**Purpose**: Pure rename refactor — no functional changes
**Audience**: Developers maintaining and extending the search subsystem
**Status**: To Do

---

## Background
The search subsystem was originally named "RAG" (Retrieval-Augmented Generation), an implementation-detail acronym unfamiliar to general users. Renaming to "search" makes the config, CLI, and code self-explanatory and future-proofs the naming if the backend technology changes. No users are in production yet, so backward compatibility is not required.

## Goal
Every public and internal occurrence of `rag` / `RAG` / `Rag` that refers to this subsystem is replaced with `search` / `SEARCH` / `Search`. The codebase compiles, all tests pass, and no `rag`-named symbol, file, directory, config key, CLI command, or documentation heading remains — except as an acronym explanation in comments where it aids comprehension.

---

## Scope

### In Scope
- Directory renames: `archon/rag/` → `archon/search/`, `tests/rag/` → `tests/search/`
- Python symbols: class names (`RagStore`, `RagPipeline`, `RagInstaller`, `RagCollectionSync`), module-level functions and variables with `rag_` prefix, config class `RagConfig`
  - `RagState` → `SearchState` (enum in `archon/gateway/gateway.py`)
  - `get_rag_service()` → `get_search_service()` (platform API in `archon/platform/__init__.py`)
  - Instance attributes/parameters: `rag_url`, `rag_enabled`, `rag_pre_context`, `_rag_provider` across AI modules
  - `rag_retrieval` string literal in `archon/chat/telegram_formatter.py` (injection type)
  - `FastMCP("archon-rag")` app name string in `archon/search/server.py` (after rename)
  - `logging.getLogger("archon.rag")` → `logging.getLogger("archon.search")` in server.py
  - MCP server key `"rag"` → `"search"` in `archon/ai/claude_session.py`
  - `<rag_selected_collections>` XML tag in `archon/ai/decomposer.py` + `_RAG_COLLECTIONS_RE` constant
  - `[rag]` dependency group in `pyproject.toml` (extra) → `[search]`
  - Prompt files: `archon/ai/prompts/system_reminder.md`, `archon/ai/prompts/decomposer.md` — `rag_*` tool names and headings
  - `archon/config/config_rw.py` — hardcoded `"rag"` string literals for TOML section manipulation
  - `asyncio.create_task(..., name="rag-indexing-monitor")` → `"search-indexing-monitor"` in gateway.py
- Config attribute access: `config.rag` → `config.search` everywhere
- Config files: `[rag]` section → `[search]`, `db_path` default `~/.archon/rag` → `~/.archon/search`
- MCP tool names: `rag_status`, `rag_start`, `rag_stop`, `rag_ingest`, `rag_sync`, `rag_collection_*` → `search_*` equivalents
- CLI subcommand: `archon rag` → `archon search`
- AI module files: `archon_toolkit_rag.py` → `archon_toolkit_search.py`, `rag_context_provider.py` → `search_context_provider.py`
- Platform service files: `rag_service.py` → `search_service.py` (macOS, Linux, Windows)
- Documentation: all `.md` files in `Documentation/`, `CLAUDE.md`, `README.md`, `examples/config.toml.example`
- Root `install.py` (project installer script): `_offer_rag_setup()` → `_offer_search_setup()`, `_rag_already_enabled()` → `_search_already_enabled()`, user-facing strings `"archon rag install"` → `"archon search install"`, config key `"rag.enabled"` → `"search.enabled"`

### Out of Scope
- `IndexingState`, `IndexingStatus`, `IndexingStateStore`, `CollectionProgress` — these names describe indexing mechanics, not the RAG concept; they stay unchanged
- `IndexingNotificationMonitor` — same rationale
- `MultiCollectionRouter` — not RAG-specific naming; unchanged
- `WatcherManager`, `CollectionWatcher` — unchanged
- Test function names inside test files that don't contain `rag` in the name (e.g., `test_chunker.py`, `test_store.py` content — only rename class/function names that contain `rag`)
- Completed backlog docs in `Documentation/Completed/` that describe past work — update file names and headings but keep historical references to "RAG" in prose where they describe what was built
- LanceDB, fastembed, or third-party library names — unchanged

---

## Acceptance criteria
- [ ] `archon/rag/` directory no longer exists; `archon/search/` exists with identical content (renamed)
- [ ] `tests/rag/` directory no longer exists; `tests/search/` exists
- [ ] `from archon.rag` import appears nowhere in the codebase
- [ ] `config.rag` attribute access appears nowhere in the codebase
- [ ] `[rag]` section header appears nowhere in any `.toml` file
- [ ] `RagConfig`, `RagStore`, `RagPipeline`, `RagInstaller`, `RagCollectionSync` class names appear nowhere
- [ ] `RagState`, `RagContextProvider`, `RagService` class names appear nowhere
- [ ] `get_rag_service` function name appears nowhere in `archon/`
- [ ] `rag_url`, `rag_enabled`, `rag_pre_context` parameter names appear nowhere in `archon/`
- [ ] MCP tools named `rag_*` appear nowhere in `archon_toolkit_search.py` tool name strings
- [ ] `rag_*` tool names appear nowhere in `archon/ai/prompts/`
- [ ] `"rag_retrieval"` injection type string appears nowhere
- [ ] `[rag]` dependency group no longer exists in `pyproject.toml`
- [ ] `archon rag` CLI subcommand no longer exists; `archon search` works
- [ ] `uv run pytest tests/ --no-cov -q` passes with zero failures
- [ ] `uv run mypy archon/` passes with zero errors
- [ ] `grep -rniP "\brag\b" archon/ tests/ --include="*.py" --include="*.toml" --include="*.md"` returns zero results (uses word-boundary matching to avoid false positives from words like `storage`/`paragraph`; catches class names, identifiers like `rag_url`, string literals like `"archon-rag"`, config keys like `"rag"`, and prompt file tool names)

**Note**: The final docs grep (`grep -riP "\brag\b" Documentation/`) will produce matches in `Documentation/Completed/` prose and ADR historical descriptions — these are acceptable and expected. All other matches must be reviewed and justified.

---

## What does NOT change
- All runtime behavior, algorithm logic, and data contracts
- LanceDB schema, embedding model names, reranker model names
- `IndexingState`/`IndexingStatus`/`CollectionProgress`/`IndexingStateStore` class names
- `IndexingNotificationMonitor` class name
- Config key names *within* the `[search]` section (e.g., `embedding_model`, `host`, `port`, `db_path`, `chunk_size`)
- Test logic and assertions — only names of test classes/functions that contain `rag_`
- `~/.archon/` data directory structure (aside from the `rag` subdirectory default)

---

## Known limitations / accepted trade-offs
- Existing `~/.archon/rag` data directories on developer machines will not be auto-migrated; devs must rename manually or re-index. Acceptable since no production users exist.
- Completed backlog docs in `Documentation/Completed/` retain "RAG" in prose descriptions of historical decisions — they describe what was built, not what to build.
- Developer machines with launchd/systemd service registered under `com.archon.rag` / `archon-rag` must manually unload the old service (`archon search uninstall` on old version, then `archon search install` on new version) — simply running the new installer creates a parallel service.
- `__pycache__` directories under the old `archon/rag/` and `tests/rag/` paths may cause stale bytecode issues. Run `find . -type d -name __pycache__ | xargs rm -rf` after Phase 1 if import errors occur.

---

## Architecture
No new modules or classes introduced. Pure rename — the post-refactor structure mirrors the pre-refactor structure with all `rag` identifiers replaced by `search`.

Key mapping:
| Before | After |
|---|---|
| `archon/rag/` | `archon/search/` |
| `tests/rag/` | `tests/search/` |
| `archon/ai/archon_toolkit_rag.py` | `archon/ai/archon_toolkit_search.py` |
| `archon/ai/rag_context_provider.py` | `archon/ai/search_context_provider.py` |
| `archon/cli/rag_cmd.py` | `archon/cli/search_cmd.py` |
| `archon/platform/*/rag_service.py` | `archon/platform/*/search_service.py` |
| `RagConfig` | `SearchConfig` |
| `RagStore` | `SearchStore` |
| `RagPipeline` | `SearchPipeline` |
| `RagInstaller` | `SearchInstaller` |
| `RagCollectionSync` | `SearchCollectionSync` |
| `RagState` (gateway.py) | `SearchState` |
| `get_rag_service()` (platform/__init__.py) | `get_search_service()` |
| `rag_url` / `_rag_url` (AI module attributes) | `search_url` / `_search_url` |
| `"rag"` MCP server key (claude_session.py) | `"search"` |
| `FastMCP("archon-rag")` (server.py) | `FastMCP("archon-search")` |
| MCP tool names in prompts (system_reminder.md, decomposer.md) | `search_*` equivalents |
| `archon/config/config_rw.py` `"rag"` string literals | `"search"` |
| `config.rag` | `config.search` |
| `[rag]` (config section) | `[search]` |
| `archon rag <cmd>` CLI | `archon search <cmd>` |
| MCP tools: `rag_*` | MCP tools: `search_*` |
| `_offer_rag_setup()` / `_rag_already_enabled()` (install.py root) | `_offer_search_setup()` / `_search_already_enabled()` |

---

## Tests
All existing tests continue to pass — no new tests added, no test logic changed. Test file names within `tests/search/` keep their current names (e.g., `test_store.py`, `test_sync.py`). Only test class names containing `Rag` and test function names containing `rag_` are renamed.

- **all existing rag tests** (unit): renamed and verified to pass under `tests/search/`
- **full suite** (integration): `uv run pytest tests/ --no-cov -q` — zero failures

---

## Documentation update
- [ ] `Documentation/Architecture/180_rag_architecture.md` → `180_search_architecture.md`, content updated
- [ ] `Documentation/ADRs/09_rag_history_format.md` → `09_search_history_format.md`, content updated
- [ ] `Documentation/UserManual/rag_guide.md` → `search_guide.md`, content updated
- [ ] All `Documentation/Backlog/FEAT-*-rag-*.md` files: headings and code blocks updated (file names kept — they are historical IDs)
- [ ] `Documentation/Completed/` — headings and code blocks updated
- [ ] `CLAUDE.md` — all `archon/rag/` paths, `config.rag`, `[rag]` references updated
- [ ] `README.md` — all RAG section headings and references updated
- [ ] `examples/config.toml.example` — `[rag]` → `[search]`

---

## Task breakdown

### Phase 1 — Directory and import foundation
> **Releasable**: after Task 1.2, `uv run pytest tests/search/ --no-cov -q` passes

#### Task 1.1 — Rename `archon/rag/` → `archon/search/`
- [x] **File**: `archon/search/` (directory rename from `archon/rag/`)
- **Depends on**: nothing
- **Description**:
  - Rename the directory `archon/rag/` → `archon/search/` using `git mv`
  - Update `archon/search/__init__.py`: change any `from archon.rag` self-references to `from archon.search`
  - Update all files *outside* `archon/search/` that import from `archon.rag.*` → `archon.search.*`: covers `archon/ai/`, `archon/cli/`, `archon/gateway/`, `archon/platform/`, `tests/` (except `tests/rag/` which is handled in Task 1.2)
  - Files to update outside the directory: `archon/ai/archon_toolkit_rag.py`, `archon/ai/rag_context_provider.py`, `archon/ai/pipeline.py`, `archon/ai/classifier.py`, `archon/ai/decomposer.py`, `archon/ai/history_compactor.py`, `archon/ai/context_provider.py`, `archon/cli/rag_cmd.py`, `archon/cli/main.py`, `archon/cli/doctor.py`, `archon/gateway/*.py`, `archon/gateway/gateway.py` — update `from archon.rag` imports, `archon/config/config_rw.py` — update `"rag"` TOML section string literals, all `archon/platform/` files
  - Internal cross-imports within `archon/search/` (e.g., `from archon.rag.store import ...` → `from archon.search.store import ...`)
  - Do NOT rename any class names yet — only the module path
  - **Note**: after the directory rename, files in `archon/search/` will still contain docstrings and comments referencing `RagPipeline`, `RagStore`, `RagCollectionSync`, and other class names. These will be renamed in Phases 3-4 when the class renames occur. Do not update docstrings in Task 1.1 — wait for the class rename tasks. The final acceptance grep will catch any remaining docstring references.
- **Releasable**: after this task, `from archon.search import ...` resolves; `from archon.rag import ...` raises ImportError
- **Tests (TDD)** — verify imports only (no logic tests change):
  - Checkpoint: `python -c "from archon.search import *; print('ok')"` and `uv run mypy archon/search/`

#### Task 1.2 — Rename `tests/rag/` → `tests/search/`
- [x] **File**: `tests/search/` (directory rename from `tests/rag/`)
- **Depends on**: Task 1.1
- **Description**:
  - Rename the directory `tests/rag/` → `tests/search/` using `git mv`
  - Update `tests/search/conftest.py`: change any `from archon.rag` → `from archon.search`
  - Update all other test files in `tests/search/`: change `from archon.rag` → `from archon.search` in imports
  - Do NOT rename test class names or test function names yet
- **Releasable**: after this task, `uv run pytest tests/search/ --no-cov -q` passes
- **Tests (TDD)** — `tests/search/`:
  - Checkpoint: `uv run pytest tests/search/ --no-cov -q`

#### Task 1.3 — Rename `rag` symbols in `archon/gateway/gateway.py`
- [x] **File**: `archon/gateway/gateway.py`
- **Depends on**: Task 1.1
- **Description**:
  - Rename `RagState` → `SearchState` (enum)
  - Rename `_ensure_rag_server` → `_ensure_search_server`
  - Rename `_detect_rag_state` → `_detect_search_state`
  - Rename `_auto_start_rag_service` → `_auto_start_search_service`
  - Rename `_register_rag_state_notification` → `_register_search_state_notification`
  - Rename `_register_deprecated_rag_notification` → `_register_deprecated_search_notification`
  - Rename local variables `rag_url` → `search_url`, `rag_state` → `search_state`
  - Update user-facing notification strings: `"archon rag install"` → `"archon search install"`, `"RAG started automatically"` → `"Search started automatically"`
  - Rename asyncio task name `"rag-indexing-monitor"` → `"search-indexing-monitor"`
  - Update log messages containing "rag"
  - In `archon/cli/doctor.py`: rename `CheckResult("rag server", ...)` check name strings → `CheckResult("search server", ...)` (5 locations); update user-facing messages `"archon rag install"` → `"archon search install"`, `"archon rag start"` → `"archon search start"`
  - Rename `_RAG_JSONRPC_PAYLOAD` → `_SEARCH_JSONRPC_PAYLOAD` and `_RAG_STALE_DAYS` → `_SEARCH_STALE_DAYS` module-level constants in `archon/cli/doctor.py`
  - Update `tests/cli/test_doctor.py`: update all mock paths and assertion strings that reference `_RAG_JSONRPC_PAYLOAD` or `_RAG_STALE_DAYS`
  - Update `tests/cli/test_doctor.py`: update `"rag server"` check name assertions, `RagConfig` usages, and `"archon rag install"` string assertions
- **Releasable**: after this task, gateway.py contains no `rag` identifiers
- **Tests (TDD)** — `tests/gateway/`:
  - Update `tests/gateway/test_rag_auto_start.py` → rename to `test_search_auto_start.py` (`git mv`), update class/function names and mock targets
  - Update `tests/gateway/test_rag_integration.py` → rename to `test_search_integration.py` (`git mv`), update imports and mock targets
  - Update `tests/gateway/test_gateway.py`: update all `RagState`, `_detect_rag_state`, `_auto_start_rag_service`, `_register_rag_state_notification` mock targets and symbol references; update `"rag-indexing-monitor"` task name assertions; rename `test_ensure_rag_server_*` → `test_ensure_search_server_*` (7 functions); rename `test_gateway_rag_url_*` → `test_gateway_search_url_*` (4 functions); rename `test_monitor_started_when_rag_enabled_*` → `test_monitor_started_when_search_enabled_*`; rename `rag_url` local variables in tests
  - Update `tests/gateway/test_startup_notification.py`: update `_register_rag_state_notification`, `_register_deprecated_rag_notification` mock paths and `RagConfig` usages
  - Checkpoint: `uv run pytest tests/gateway/ --no-cov -q`

---

### Phase 2 — Config rename
> **Releasable**: after Task 2.3, `config.search` is the live attribute and `[search]` is the config section

#### Task 2.1 — Rename `RagConfig` → `SearchConfig` in config loader
- [x] **File**: `archon/config/loader.py`
- **Depends on**: Task 1.1
- **Description**:
  - Rename class `RagConfig` → `SearchConfig`
  - Update the attribute on the root config dataclass: `rag: RagConfig` → `search: SearchConfig`
  - Update `db_path` default value: `"~/.archon/rag"` → `"~/.archon/search"`
  - Update the TOML section mapping: `[rag]` → `[search]` (the key used by `tomllib`/`tomli` to load the section)
  - Export `SearchConfig` from `archon/config/__init__.py` (remove `RagConfig` export)
  - Rename module-level constant `_DEFAULT_RAG_COLLECTIONS` → `_DEFAULT_SEARCH_COLLECTIONS` in `archon/config/loader.py`
  - Rename all `rag_`-prefixed local variables in the config parsing function (lines 633-688): `rag_data`, `rag_port`, `rag_top_k_retrieve`, `rag_top_k_return`, `rag_chunk_size`, `rag_sync_timeout`, `rag = RagConfig(...)` → `search_data`, `search_port`, etc.
  - Update error message string: `"[rag] history_collection is no longer supported"` → `"[search] history_collection is no longer supported"` (line 657)
  - Update comment `"e.g. RAG-only commands"` → `"e.g. search-only commands"` if present (line 406)
  - Also update `archon/config/config_rw.py`: rename `"rag"` string literals used for TOML section access → `"search"` (this file is inside `archon/config/` so Task 2.2 which covers "outside `archon/config/`" will miss it)
  - Also update `archon/cli/config_cmd.py`: rename `"rag"` entry in `_KNOWN_SECTIONS` frozenset → `"search"` (otherwise `archon config set search.key value` fails validation)
  - `git mv tests/config/test_rag_config.py tests/config/test_search_config.py` and update class/function names inside
  - Update `tests/config/test_config_example_sync.py`: update `RagConfig` import → `SearchConfig`, rename `test_example_rag_defaults_match_python` → `test_example_search_defaults_match_python`, update `parsed["rag"]` dict key accesses → `parsed["search"]`
- **Releasable**: after this task, `config.search` is the typed attribute; `config.rag` raises `AttributeError`
- **Tests (TDD)** — `tests/config/`:
  - Unit: `test_search_config_loads` — verify `config.search` attribute exists with correct type
  - Unit: `test_rag_section_header_in_toml` → verify `[search]` section is parsed correctly
  - Checkpoint: `uv run pytest tests/config/ --no-cov -q`

#### Task 2.2 — Replace `config.rag` with `config.search` everywhere outside config module
- [x] **File**: all Python files outside `archon/config/` that access `config.rag`
- **Depends on**: Task 2.1
- **Description**:
  - Replace every occurrence of `config.rag` → `config.search` across: `archon/ai/`, `archon/cli/`, `archon/gateway/`, `archon/platform/`, `archon/search/` (the module itself)
  - Also replace `cfg.rag` → `cfg.search` and `self.rag` → `self.search` where `self` is the root config
  - Also rename instance attributes and parameters: `rag_url` → `search_url`, `_rag_url` → `_search_url`, `rag_enabled` → `search_enabled`, `rag_pre_context` → `search_pre_context`, `_rag_provider` → `_search_provider` across: `archon/ai/pipeline.py`, `archon/ai/decomposer.py`, `archon/ai/classifier.py`, `archon/ai/session_manager.py`, `archon/ai/claude_session.py`, `archon/ai/background_agent_manager.py`, `archon/ai/history_compactor.py`, `archon/ai/context_provider.py`, `tests/conftest.py`
  - Note: `rag_enabled` is a protocol method parameter on `ContextProvider.startup_context_prompt()` — rename the parameter name in the protocol definition in `context_provider.py` AND in all implementations: `history_compactor.py`, `decomposer.py`, `session_manager.py`; also update all call sites that pass `rag_enabled=...` as a keyword argument
  - Also update these test files that contain `rag_url`, `rag_enabled`, `_rag_url`, `"rag_retrieval"`, or MCP `"rag"` key references: `tests/ai/test_pipeline.py`, `tests/ai/test_decomposer.py`, `tests/ai/test_session_manager.py`, `tests/ai/test_claude_session.py`, `tests/ai/test_background_agent_manager.py`, `tests/chat/test_handler.py`
  - Also update MCP server key in `archon/ai/claude_session.py`: `mcp_servers["rag"]` → `mcp_servers["search"]`
  - Also update `"rag_retrieval"` injection type string in `archon/chat/telegram_formatter.py` and its producer in `archon/ai/pipeline.py`
  - Also update `<rag_selected_collections>` XML tag in `archon/ai/decomposer.py` and `_RAG_COLLECTIONS_RE` constant → `_SEARCH_COLLECTIONS_RE`
  - Rename function `_extract_rag_selected_collections()` → `_extract_search_selected_collections()` in `archon/ai/decomposer.py`
  - Also update `archon/search/router.py` (inside the search module): rename `rag_url` parameter → `search_url`, `self._rag_url` → `self._search_url`, XML tag production `<rag_collections>` → `<search_collections>` and `<rag_selected_collections>` → `<search_selected_collections>` (must match the consumer pattern `_SEARCH_COLLECTIONS_RE` in `decomposer.py`)
  - Also update `[rag]` dependency group in `pyproject.toml` → `[search]`
  - In `archon/ai/history_compactor.py`: rename `rag_section` local variable → `search_section`; update string `"A local RAG search tool"` → `"A local search tool"`
  - In `archon/chat/telegram_formatter.py`: also update the `"🔍 RAG: "` user-facing display string → `"🔍 Search: "` (separate from the `"rag_retrieval"` injection type already listed above)
  - Grep to verify zero remaining occurrences: `grep -rniP "\brag\b" archon/ tests/ --include="*.py" --include="*.toml" --include="*.md"`
- **Releasable**: after this task, all runtime code reads from `config.search`
- **Tests (TDD)**:
  - Checkpoint: `uv run pytest tests/ --no-cov -q`

#### Task 2.3 — Update `[rag]` → `[search]` in config files and test fixtures
- [ ] **Files**: `examples/config.toml.example`, any `.toml` test fixtures under `tests/`
- **Depends on**: Task 2.1
- **Description**:
  - Replace `[rag]` section header with `[search]` in `examples/config.toml.example`
  - Replace `[rag]` in any test `.toml` fixture files
  - Update `db_path` default comment in `config.toml.example` to `~/.archon/search`
- **Releasable**: after this task, no `.toml` file contains `[rag]`
- **Tests (TDD)**:
  - Checkpoint: `uv run pytest tests/config/ --no-cov -q`

---

### Phase 3 — Class renames within the search module
> **Releasable**: after Task 3.4, all public class names are `Search*`-prefixed; all tests pass

#### Task 3.1 — Rename `RagStore` → `SearchStore`
- [ ] **File**: `archon/search/store.py`
- **Depends on**: Task 1.1
- **Description**:
  - Rename class `RagStore` → `SearchStore` in `store.py`
  - Update all callers: `archon/search/pipeline.py`, `archon/search/install.py`, `archon/search/sync.py`, `archon/ai/archon_toolkit_rag.py`, any other file importing `RagStore`
  - Update `archon/search/__init__.py` export if `RagStore` is exported
- **Releasable**: after this task, `SearchStore` is the importable class name
- **Tests (TDD)** — `tests/search/test_store.py`:
  - Rename `TestRagStore` → `TestSearchStore` if present
  - Checkpoint: `uv run pytest tests/search/test_store.py --no-cov -q`

#### Task 3.2 — Rename `RagPipeline` → `SearchPipeline` and `create_pipeline()`
- [ ] **File**: `archon/search/pipeline.py`
- **Depends on**: Task 3.1
- **Description**:
  - Rename class `RagPipeline` → `SearchPipeline`
  - `create_pipeline()` function name stays (not `rag`-prefixed); update its return type annotation
  - Update all callers: `archon/search/server.py`, `archon/search/install.py`, `archon/ai/archon_toolkit_rag.py`, gateway files
- **Releasable**: after this task, `SearchPipeline` is the importable class
- **Tests (TDD)** — `tests/search/test_pipeline.py`:
  - Rename `TestRagPipeline` → `TestSearchPipeline` if present
  - Checkpoint: `uv run pytest tests/search/test_pipeline.py --no-cov -q`

#### Task 3.3 — Rename `RagInstaller` → `SearchInstaller`
- [ ] **File**: `archon/search/install.py`
- **Depends on**: Task 2.1, Task 3.1, Task 3.2
- **Description**:
  - Rename class `RagInstaller` → `SearchInstaller`
  - Update docstring: `"RagInstaller -- install, configure, and manage the RAG service"` → `"SearchInstaller -- install, configure, and manage the search service"`
  - Update `self.cfg: RagConfig = cfg.rag` → `self.cfg: SearchConfig = cfg.search` (class instance attribute, depends on Task 2.1 completing first)
  - Update `self._full_cfg.rag.*` attribute accesses (5 occurrences: `rag.pinned_collections`, `rag.embedding_model`, `rag.chunk_size`, `rag.auto_reindex_on_chunk_size_change`, `rag.collections`) → `self._full_cfg.search.*` (also covered by Task 2.2, but explicitly called out here for this file)
  - Update internal TOML write strings: `doc["rag"]`, `rag_section = doc["rag"]` → `doc["search"]`, `search_section = doc["search"]`
  - Rename `_RAG_PACKAGES` module-level constant → `_SEARCH_PACKAGES`
  - Leave `get_rag_service()` calls unchanged in this task — they will be renamed to `get_search_service()` by Task 5.2 which handles the platform API rename and all its call sites
  - Update user-facing strings: `"RAG service uninstalled. Remove [rag] section..."` → `"Search service uninstalled. Remove [search] section..."`; `"archon rag test"` → `"archon search test"`
  - In `archon/search/notification_monitor.py` (after Phase 1 rename): update user-facing Telegram strings `"archon rag status"` → `"archon search status"` (lines 79-80)
  - In `archon/search/sync.py` (after Phase 1 rename): update log message `"run \`archon rag reindex\`"` → `"run \`archon search reindex\`"` (line 410)
  - Update all callers in CLI, gateway, and toolkit files
- **Releasable**: after this task, `SearchInstaller` is the importable class
- **Tests (TDD)** — `tests/search/test_install.py`:
  - Rename test class if `TestRagInstaller` exists
  - Checkpoint: `uv run pytest tests/search/test_install.py --no-cov -q`

#### Task 3.4 — Rename `RagCollectionSync` → `SearchCollectionSync`
- [ ] **File**: `archon/search/sync.py`
- **Depends on**: Task 1.1
- **Description**:
  - Rename class `RagCollectionSync` → `SearchCollectionSync`
  - `SyncResult` stays (not `rag`-prefixed)
  - Update all callers in toolkit, gateway, and install files
- **Releasable**: after this task, `SearchCollectionSync` is the importable class
- **Tests (TDD)** — `tests/search/test_sync.py`:
  - Rename `TestRagCollectionSync` → `TestSearchCollectionSync` if present
  - Checkpoint: `uv run pytest tests/search/test_sync.py --no-cov -q`

---

### Phase 4 — AI module file renames
> **Releasable**: after Task 4.2, all AI helper files use `search_` naming; MCP tools exposed as `search_*`

#### Task 4.1 — Rename `archon_toolkit_rag.py` → `archon_toolkit_search.py` and MCP tool names
- [ ] **File**: `archon/ai/archon_toolkit_search.py` (renamed from `archon_toolkit_rag.py`)
- **Depends on**: Task 1.1, Phase 3
- **Description**:
  - Rename file `archon/ai/archon_toolkit_rag.py` → `archon/ai/archon_toolkit_search.py` using `git mv`
  - Rename internal function `_register_rag_tools()` → `_register_search_tools()`
  - Rename all MCP tool name strings: `"rag_status"` → `"search_status"`, `"rag_start"` → `"search_start"`, `"rag_stop"` → `"search_stop"`, `"rag_ingest"` → `"search_ingest"`, `"rag_sync"` → `"search_sync"`, `"rag_collection_list"` → `"search_collection_list"`, `"rag_collection_add"` → `"search_collection_add"`, `"rag_collection_remove"` → `"search_collection_remove"`, `"rag_collection_info"` → `"search_collection_info"`, `"rag_collection_reindex"` → `"search_collection_reindex"`
  - Rename all `_RAG_*` module-level schema constants: `_RAG_AVAILABLE` → `_SEARCH_AVAILABLE`, `_RAG_STATUS_SCHEMA` → `_SEARCH_STATUS_SCHEMA`, `_RAG_START_SCHEMA` → `_SEARCH_START_SCHEMA`, `_RAG_STOP_SCHEMA` → `_SEARCH_STOP_SCHEMA`, `_RAG_INGEST_SCHEMA` → `_SEARCH_INGEST_SCHEMA`, `_RAG_SYNC_SCHEMA` → `_SEARCH_SYNC_SCHEMA`, `_RAG_COLLECTION_LIST_SCHEMA` → `_SEARCH_COLLECTION_LIST_SCHEMA`, `_RAG_COLLECTION_ADD_SCHEMA` → `_SEARCH_COLLECTION_ADD_SCHEMA`, `_RAG_COLLECTION_REMOVE_SCHEMA` → `_SEARCH_COLLECTION_REMOVE_SCHEMA`, `_RAG_COLLECTION_INFO_SCHEMA` → `_SEARCH_COLLECTION_INFO_SCHEMA`, `_RAG_COLLECTION_REINDEX_SCHEMA` → `_SEARCH_COLLECTION_REINDEX_SCHEMA`
  - Note: `tests/ai/test_archon_toolkit_search.py` (renamed file) contains 70+ mock patch paths referencing `archon.ai.archon_toolkit_rag.*` and `_RAG_AVAILABLE` — update all to `archon.ai.archon_toolkit_search.*` and `_SEARCH_AVAILABLE`
  - Update the import in `archon/ai/archon_toolkit.py`: `from archon.ai.archon_toolkit_rag import _register_rag_tools` → `from archon.ai.archon_toolkit_search import _register_search_tools`
  - Update all callers of `_register_rag_tools` → `_register_search_tools`
  - Update `archon/ai/prompts/system_reminder.md`: rename `rag_status`, `rag_start`, `rag_stop`, `rag_ingest`, `rag_sync`, `rag_collection_*` tool names → `search_*`; rename `### RAG` section heading → `### Search`
  - Update `archon/ai/prompts/decomposer.md`: update any "RAG MCP tool" references
  - Update `archon/search/server.py` (after rename): change `FastMCP("archon-rag")` → `FastMCP("archon-search")` and `logging.getLogger("archon.rag")` → `logging.getLogger("archon.search")`
  - Note: `archon/search/server.py` also has 13 occurrences of `cfg.rag.*` — these are covered by Task 2.2 which renames `config.rag` → `config.search` everywhere. Do not re-apply in this task.
  - Update references in `CLAUDE.md` architecture section
  - `git mv tests/ai/test_archon_toolkit_rag.py tests/ai/test_archon_toolkit_search.py` and update class/function names inside
  - `git mv tests/integration/test_rag_routing.py tests/integration/test_search_routing.py` (if present); rename `_RAG_URL` constant → `_SEARCH_URL`; update `RagContextProvider` → `SearchContextProvider` references (note: `RagContextProvider` is renamed in Task 4.2, so this file may need a second pass after Task 4.2)
- **Releasable**: after this task, MCP exposes `search_*` tool names to Claude; `rag_*` tool names no longer exist
- **Tests (TDD)** — `tests/ai/`:
  - Rename any test class/function containing `rag_tools` or `RagTools`
  - Checkpoint: `uv run pytest tests/ai/ --no-cov -q`
  - Integration checkpoint: `uv run pytest tests/integration/ --no-cov -q`

#### Task 4.2 — Rename `rag_context_provider.py` → `search_context_provider.py`
- [ ] **File**: `archon/ai/search_context_provider.py` (renamed from `rag_context_provider.py`)
- **Depends on**: Task 1.1
- **Description**:
  - Rename file `archon/ai/rag_context_provider.py` → `archon/ai/search_context_provider.py` using `git mv`
  - Rename any class `RagContextProvider` → `SearchContextProvider` if present
  - Rename function `_search_collection_via_rag()` → `_search_collection()` (remove the `_via_rag` suffix as it is now redundant) — or rename to `_search_collection_via_search()` only if the name is part of a naming convention; prefer `_search_collection()` for clarity
  - Rename `rag_url` parameter → `search_url` and `self._rag_url` → `self._search_url` within this file (these are also covered by Task 2.2, but explicit mention here prevents the rename from being missed during the file rename)
  - Update all imports of this file in other AI modules
  - `git mv tests/ai/test_rag_context_provider.py tests/ai/test_search_context_provider.py` and update class/function names inside
- **Releasable**: after this task, `SearchContextProvider` is importable
- **Tests (TDD)** — `tests/ai/`:
  - Rename any related test class/function
  - Checkpoint: `uv run pytest tests/ai/ --no-cov -q`

---

### Phase 5 — CLI and platform renames
> **Releasable**: after Task 5.3, `archon search <cmd>` works; `archon rag` no longer exists; root installer uses search naming

#### Task 5.1 — Rename `rag_cmd.py` → `search_cmd.py` and update CLI routing
- [ ] **Files**: `archon/cli/search_cmd.py` (renamed from `rag_cmd.py`), `archon/cli/main.py`
- **Depends on**: Task 2.2, Phase 3, Phase 4
- **Description**:
  - Rename `archon/cli/rag_cmd.py` → `archon/cli/search_cmd.py` using `git mv`
  - In `search_cmd.py`: rename any `rag_*` function names → `search_*`; update subcommand strings from `"rag"` → `"search"` (e.g., `parser.add_subparsers` entry)
  - In `archon/cli/main.py`: update import and subcommand registration: `from archon.cli.rag_cmd import ...` → `from archon.cli.search_cmd import ...`; update help text; register as `search` subcommand instead of `rag`
  - `git mv tests/cli/test_rag_cmd.py tests/cli/test_search_cmd.py` and update class/function names inside
- **Releasable**: after this task, `archon search start/stop/status` works; `archon rag` raises `unrecognized command`
- **Tests (TDD)** — `tests/cli/`:
  - Checkpoint: `uv run pytest tests/cli/ --no-cov -q`

#### Task 5.2 — Rename `rag_service.py` → `search_service.py` in platform modules
- [ ] **Files**: `archon/platform/macos/search_service.py`, `archon/platform/linux/search_service.py`, `archon/platform/windows/search_service.py` (each renamed from `rag_service.py`)
- **Depends on**: Task 2.2
- **Description**:
  - `git mv` each `rag_service.py` → `search_service.py` in macos/, linux/, windows/
  - Rename any class `RagService` → `SearchService` in each file
  - Update `archon/platform/__init__.py` and any service-discovery imports that reference `rag_service`
  - In `archon/platform/__init__.py`: rename `get_rag_service()` → `get_search_service()`, `_rag_service` singleton → `_search_service`, `override(rag_service=...)` parameter → `override(search_service=...)`. Update all 30+ call sites in `archon/gateway/`, `archon/cli/`, `archon/ai/`, `archon/search/`
  - Update launchd/systemd plist/unit file names if they contain `rag` (e.g., `com.archon.rag` label → `com.archon.search`). Note: updating the service label requires the old service to be unloaded before the new label can be registered (see Known limitations)
  - Update the Python module path strings in platform service files: `"archon.rag.server"` → `"archon.search.server"` — this appears in:
    - macOS plist template: `<string>archon.rag.server</string>` (ProgramArguments)
    - Linux systemd unit: `ExecStart={python} -m archon.rag.server`
    - Windows stub message: `python -m archon.rag.server`
    - `archon/search/server.py` docstring: `python -m archon.rag.server` (update to `python -m archon.search.server`)
  - Also update hardcoded strings: `_LABEL = "com.archon.rag"` → `"com.archon.search"`, `"archon-rag.log"` → `"archon-search.log"`, `"launchd-rag"` → `"launchd-search"` (macOS); `_SERVICE_NAME = "archon-rag"` → `"archon-search"` (Linux)
  - `git mv tests/platform/macos/test_rag_service.py tests/platform/macos/test_search_service.py` and update class/function names inside
  - `git mv tests/platform/linux/test_rag_service.py tests/platform/linux/test_search_service.py` and update class/function names inside
- **Releasable**: after this task, service management for the search subsystem uses `search_service`
- **Tests (TDD)** — `tests/platform/`:
  - Update any test importing `rag_service` or `RagService`
  - Checkpoint: `uv run pytest tests/platform/ --no-cov -q`

#### Task 5.3 — Rename `rag` symbols in root `install.py`
- [ ] **File**: `install.py` (project root installer)
- **Depends on**: Task 2.2
- **Description**:
  - Rename `_offer_rag_setup()` → `_offer_search_setup()`
  - Rename `_rag_already_enabled()` → `_search_already_enabled()`
  - Update user-facing strings: `"archon rag install"` → `"archon search install"`, `"archon rag status"` → `"archon search status"`, `"archon rag install' manually"` → `"archon search install' manually"`
  - Update config key access: `"rag.enabled"` → `"search.enabled"`
- **Releasable**: after this task, root installer uses `search` naming throughout
- **Tests (TDD)** — `tests/test_installer_py.py`:
  - Rename `TestRagAlreadyEnabled` → `TestSearchAlreadyEnabled` and all contained test methods
  - Update all `"rag"` string assertions
  - Checkpoint: `uv run pytest tests/test_installer_py.py --no-cov -q`

---

### Phase 6 — Documentation
> **Releasable**: after Task 6.2, no `rag`-labeled heading or config snippet remains in docs

#### Task 6.1 — Rename and update `180_rag_architecture.md`
- [ ] **File**: `Documentation/Architecture/180_search_architecture.md` (renamed from `180_rag_architecture.md`)
- **Depends on**: nothing (documentation-only)
- **Description**:
  - `git mv Documentation/Architecture/180_rag_architecture.md Documentation/Architecture/180_search_architecture.md`
  - Update document title, all section headings, class/module references, config snippets, file paths within the document
  - Update cross-references in other Architecture docs that link to `180_rag_architecture.md`
  - Update `Documentation/990_documentation_index_and_contribution_guide.md` entry
- **Releasable**: after this task, architecture docs are internally consistent
- **Tests (TDD)**: N/A (documentation)
- **Checkpoint**: `grep -r "180_rag_architecture" Documentation/ README.md CLAUDE.md` returns zero results

#### Task 6.2 — Update all remaining documentation
- [ ] **Files**: all `.md` files in `Documentation/ADRs/`, `Documentation/Backlog/`, `Documentation/Completed/`, `Documentation/UserManual/`, `CLAUDE.md`, `README.md`
- **Depends on**: Task 6.1
- **Description**:
  - `Documentation/ADRs/09_rag_history_format.md` → `09_search_history_format.md` (`git mv`); update title and content
  - `Documentation/UserManual/rag_guide.md` → `search_guide.md` (`git mv`); update all headings, CLI examples (`archon rag` → `archon search`), config snippets
  - All `Documentation/Backlog/FEAT-*-rag-*.md` files: update headings, code blocks, `[rag]` config references — keep file names (historical IDs)
  - `Documentation/Completed/` files: update headings and code snippets; retain "RAG" in historical prose where it describes past decisions
  - `CLAUDE.md`: update `archon/rag/` → `archon/search/`, `config.rag` → `config.search`, `[rag]` → `[search]`, tool names, CLI subcommand, architecture table
  - `README.md`: update all RAG section references
  - Verify: `grep -ri "\brag\b" Documentation/ CLAUDE.md README.md` returns only acceptable historical prose references
- **Releasable**: after this task, the full rename is complete end-to-end
- **Tests (TDD)**: N/A (documentation)
- **Checkpoint**:
  - Final Python code verification: `grep -rniP "\brag\b" archon/ tests/ --include="*.py" --include="*.toml" --include="*.md"` — must return zero results (word-boundary match; catches all identifiers, string literals, class names, and prompt file tool names containing "rag")
  - Final docs verification: `grep -ri "rag" Documentation/ CLAUDE.md README.md archon/ai/prompts/` — review each match; acceptable matches are historical prose in `Documentation/Completed/` and acronym explanations in comments
  - `uv run pytest tests/ --no-cov -q && uv run mypy archon/`
