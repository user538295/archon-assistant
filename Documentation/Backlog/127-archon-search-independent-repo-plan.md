# FEAT-046 — Extract archon-search as an Independent Repository
**Purpose**: Make `archon-search` a fully independent Python package — its own git repo, own PyPI package, own automated CalVer releases — so it can be developed, versioned, and consumed independently of Archon.
**Audience**: Archon maintainer (you); future standalone `archon-search` users; Archon runtime (unchanged subprocess consumer).
**Status**: To Do

---

## Background
`archon-search` lives inside the Archon monorepo at `packages/archon-search/` but is logically a standalone product. All current default paths (`~/.archon/`) couple it to the Archon installation. The stubs shim (`_search_stubs_shim.py`) ties its test suite to the monorepo root. Version management is manual. This blocks independent versioning, publishing, and use by projects other than Archon.

## Goal
After this feature: (1) `archon-search` has its own GitHub repo with full preserved git history across all five historical paths; (2) every push to `main` automatically publishes a clean `YY.M.<total-commit-count>` version to PyPI via OIDC — zero manual version management; (3) Archon consumes `archon-search` from PyPI like any other dependency; (4) standalone users get a clean `~/.archon-search/` default; (5) existing Archon users are unaffected (config path injected via env var).

---

## Scope

### In Scope
- Standalone `_search_stubs.py` in archon-search tests (shim deleted)
- `~/.archon-search/` as the new standalone default for all data paths, including API key file and default ingest path
- `ARCHON_SEARCH_CONFIG` env var support in `config.py`
- Parent Archon injects `ARCHON_SEARCH_CONFIG` and `ARCHON_SEARCH_API_KEY` when spawning archon-search (backwards compat)
- Dynamic CalVer via `hatch-vcs` with custom `YY.M.<total-commit-count>` scheme
- Automated PyPI publish via OIDC trusted publisher on every push to `main`
- Full git history preserved across all five historical paths
- Parent updated to consume archon-search from PyPI

### Out of Scope
- Changing the HTTP API contract between Archon and archon-search
- Migrating existing user data from `~/.archon/search` to `~/.archon-search/`
- MCP or CLI interface changes
- Renaming the PyPI package slug

---

## Acceptance criteria

> Acceptance criteria are verified in the final task. See Task 4.1 — Final verification & documentation update.

---

## What does NOT change
- `SearchClient` in `archon/ai/search_client.py` — no changes; all integration tests must pass unchanged
- REST API contract — no URL, parameter, or response shape changes
- MCP tool names and parameter signatures
- Archon users' existing data at `~/.archon/` (protected by env var injection)

---

## Known limitations / accepted trade-offs
- CalVer version clock resets after `git filter-repo` — the new repo starts from a lower commit count. First tag will be something like `26.5.N`. Expected and correct.
- In-flight standalone ingest jobs at upgrade time are silently lost when `JOBS_FILE` moves from `~/.archon/` to `~/.archon-search/`. Archon-managed users are unaffected.
- Standalone users upgrading from the monorepo version to the extracted PyPI package will find all defaults pointing to `~/.archon-search/` while their data lives in `~/.archon/`. This is a **silent data loss** risk. Mitigation: on startup, if `~/.archon/archon-search.toml` exists and `~/.archon-search/archon-search.toml` does not exist, `archon-search` should log a WARNING: "Config found at old path ~/.archon/archon-search.toml. Set ARCHON_SEARCH_CONFIG=~/.archon/archon-search.toml or migrate your data to ~/.archon-search/." This fallback detection is out of scope for this feature but MUST be added as a follow-up issue before the PyPI release is announced to standalone users. **Action required before public announcement**: create a GitHub issue tracking this.
- API key file: standalone users' `~/.archon/.search.env` will not be found at the new default `~/.archon-search/.search.env`. Archon-managed users are protected by Task 1.8 injection. Standalone users must manually copy their key file or set `ARCHON_SEARCH_API_KEY` env var.
- Immutable PyPI releases: if a broken package is published, release a patch rather than attempting deletion.

---

## Architecture
- `_version_scheme.py` — new file at `packages/archon-search/` root; contains `calver_total_count()` callable that runs `git rev-list --count HEAD` and returns `YY.M.<count>`. Referenced from `pyproject.toml` as `raw-options.version_scheme = '_version_scheme:calver_total_count'` (the `setuptools-scm` pass-through key under `hatch-vcs`).
- `ARCHON_SEARCH_CONFIG` env var — checked at the top of `get_default_config_path()` in `config.py`; overrides the file-system default. Archon's `search_cmd.py` injects this to point at `~/.archon/archon-search.toml`.
- After Phase 2 extraction, `_version_scheme.py` lands at the new repo root (stripped by `--path-rename packages/archon-search/:`).
- No new modules in the parent Archon repo; `search_cmd.py` gains env injection only.

---

## Task breakdown

### Phase 0 — Pre-work

#### Task 0.1 — Verify PyPI name availability
- [x] **File**: N/A (manual check)
- **Depends on**: nothing
- **Description**:
  - Open `https://pypi.org/project/archon-search` — if it returns a 404, the name is available and Phase 1 can proceed. If it returns a live project not owned by you, a rename is required before any other work proceeds.
  - Record the result (available / taken) in this task.
  - **Result**: Available (HTTP 404 confirmed on 2026-05-20).
- **Releasable**: after this task, the go/no-go decision for the extraction is confirmed.
- **Tests (TDD)**: N/A — manual verification.
- **Checkpoint**: manual — visit the URL and confirm.

---

### Phase 1 — Decouple within the monorepo
> **Releasable**: after this phase is complete, CI is green on both `tests/` and `packages/archon-search/tests/`. The monorepo is in a clean state ready for extraction — no archon imports in the search package, no shim, paths updated, env var wired.

#### Task 1.1 — Copy `_search_stubs.py` into archon-search package tests
- [x] **File**: `packages/archon-search/tests/_search_stubs.py` (new copy)
- **Depends on**: Task 0.1
- **Description**:
  - Copy `tests/_search_stubs.py` from the monorepo root into `packages/archon-search/tests/_search_stubs.py`.
  - Add a comment at the top of the root `tests/_search_stubs.py`: `# Canonical copy is now packages/archon-search/tests/_search_stubs.py. This root copy will be removed in Phase 3.`
  - Do NOT modify the root copy's logic; the comment is the only change.
- **Releasable**: after this task, `packages/archon-search/tests/` has a self-contained stubs file importable without repo-root path manipulation.
- **Tests (TDD)** — `packages/archon-search/tests/`:
  - Unit: verify `_search_stubs.py` exists in the package tests dir and `install_stubs()` is importable from it directly (no shim needed).
  - Checkpoint: `python -c "import sys; sys.path.insert(0, 'packages/archon-search/tests'); from _search_stubs import install_stubs; print('ok')"` from monorepo root.

#### Task 1.2 — Update `conftest.py` to import from copied stubs
- [x] **File**: `packages/archon-search/tests/conftest.py`
- **Depends on**: Task 1.1
- **Description**:
  - Replace the `from _search_stubs_shim import install_stubs` import (and any direct `_search_stubs_shim` usage) with `from _search_stubs import install_stubs`.
  - Diff `conftest.py` against `_search_stubs.py` to identify all duplicated code: inline stub code (module-level mock setup — look for `_FakeTextEmbedding`, `_FakeTextCrossEncoder`, fastembed submodule registration, and sentence_transformers/onnxruntime blocking) and env-var settings (look for `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS`, `OMP_NUM_THREADS`, `TOKENIZERS_PARALLELISM` — all of these are already set by `install_stubs()`). Remove every block from `conftest.py` that is now provided by `install_stubs()`.
  - After the edit, `conftest.py` must call `install_stubs()` (or equivalent) and contain no inline stub definitions.
- **Releasable**: after this task, `conftest.py` no longer depends on the shim or the repo root.
- **Tests (TDD)** — `packages/archon-search/tests/`:
  - Integration: run the full archon-search test suite — all tests must pass without errors related to missing stubs.
  - Checkpoint: `uv run pytest packages/archon-search/tests/ -q --no-cov`

#### Task 1.3 — Delete `_search_stubs_shim.py`
- [x] **File**: `packages/archon-search/tests/_search_stubs_shim.py` (delete)
- **Depends on**: Task 1.2
- **Description**:
  - Delete `packages/archon-search/tests/_search_stubs_shim.py`.
  - Verify no remaining import of `_search_stubs_shim` anywhere in `packages/archon-search/` via `grep -r "_search_stubs_shim" packages/archon-search/` — must return empty.
- **Releasable**: after this task, the shim is gone and the package tests are fully self-contained.
- **Tests (TDD)** — `packages/archon-search/tests/`:
  - Integration: `uv run pytest packages/archon-search/tests/ -q --no-cov` must pass.
  - Checkpoint: `uv run pytest packages/archon-search/tests/ -q --no-cov`

#### Task 1.4 — Remove phantom `_PENDING_MIGRATION` entries
- [x] **File**: `packages/archon-search/tests/test_no_archon_imports.py`
- **Depends on**: nothing (independent of stubs tasks)
- **Description**:
  - In `test_no_archon_imports.py`, remove `"pipeline.py"` and `"mcp.py"` from `_PENDING_MIGRATION`. No actual import violations exist in those files — the entries are phantom exemptions.
  - After the edit, `_PENDING_MIGRATION` should be empty (`set()`) or removed entirely if nothing remains.
- **Releasable**: after this task, the no-archon-imports test enforces the boundary strictly for all files.
- **Tests (TDD)** — `packages/archon-search/tests/`:
  - Unit: run `test_no_archon_imports.py` directly — must pass with no phantom exemptions.
  - Checkpoint: `uv run pytest packages/archon-search/tests/test_no_archon_imports.py -v --no-cov`

#### Task 1.5 — Rename `~/.archon/` defaults in `config.py`
- [x] **File**: `packages/archon-search/archon_search/config.py`
- **Depends on**: nothing
- **Description**:
  - In `TelemetryConfig`: change `log_dir` default from `"~/.archon/search-logs"` to `"~/.archon-search/search-logs"`.
  - In `SearchConfig`: change `db_path` default from `"~/.archon/search"` to `"~/.archon-search/search"` and `log_file` default from `"~/.archon/logs/archon-search.log"` to `"~/.archon-search/logs/archon-search.log"`.
  - In `get_default_config_path()`: change `Path.home() / ".archon" / "archon-search.toml"` to `Path.home() / ".archon-search" / "archon-search.toml"`.
  - Three changes in one file; all are default value literals only.
- **Releasable**: after this task, standalone installs default to `~/.archon-search/` for all data paths.
- **Implementation approach**: Tasks 1.5, 1.7, and 1.11 are tightly coupled (changing defaults breaks test assertions). To comply with the commit-per-task rule while keeping CI green, implement them in order with a `# type: ignore` / skip marker in CI, OR (preferred) combine them into a single 'atomic' task. The implementer should merge the changes for (1.5 + 1.11-subset-for-config) and (1.7 + 1.11-subset-for-paths) into single implementations at the task boundary. When using `/implement-next`, treat '1.5+1.11-config-subset' as one implementation step and '1.7+1.11-path-subset' as another. The commit message should note both task IDs: e.g., `feat: rename default paths in config.py and update test assertions (Tasks 1.5 + 1.11 config-subset)`.
- **Tests (TDD)** — `packages/archon-search/tests/test_config.py`:
  - Unit: `test_get_default_config_path` — assert `result == Path.home() / '.archon-search' / 'archon-search.toml'` and that the returned path starts with `Path.home()`.
  - Unit: `test_default_db_path` — assert default `db_path == "~/.archon-search/search"`.
  - Unit: `test_default_log_file` — assert default `log_file == "~/.archon-search/logs/archon-search.log"`.
  - Unit (in `tests/config/test_telemetry_config.py`): `test_default_log_dir` — assert `log_dir == "~/.archon-search/search-logs"`.
  - Checkpoint: `uv run pytest packages/archon-search/tests/test_config.py packages/archon-search/tests/config/test_telemetry_config.py -v --no-cov`

#### Task 1.6 — Add `ARCHON_SEARCH_CONFIG` env var override in `config.py`
- [x] **File**: `packages/archon-search/archon_search/config.py`
- **Depends on**: Task 1.5
- **Description**:
  - At the top of `get_default_config_path()`, check `os.environ.get("ARCHON_SEARCH_CONFIG")` — if set and non-empty, return `Path(os.path.expanduser(value))`.
  - Precedence: `ARCHON_SEARCH_CONFIG` env var > `~/.archon-search/archon-search.toml` default.
  - `os` is already imported or add it. Tilde expansion via `os.path.expanduser()` is required.
- **Releasable**: after this task, `get_default_config_path()` is overridable by env var. Archon can now inject the old path without any file move.
- **Tests (TDD)** — `packages/archon-search/tests/test_config.py`:
  - Unit: `test_env_var_overrides_default` — set `ARCHON_SEARCH_CONFIG=/tmp/custom.toml` in `os.environ`, assert `get_default_config_path() == Path("/tmp/custom.toml")`.
  - Unit: `test_env_var_expands_tilde` — set `ARCHON_SEARCH_CONFIG=~/.custom/archon-search.toml`, assert result is an absolute path AND `str(result).startswith(str(Path.home()))` — using `str(Path.home())` as the prefix, not just asserting `result.is_absolute()`.
  - Unit: `test_env_var_empty_uses_default` — set `ARCHON_SEARCH_CONFIG=""`, assert result is the `~/.archon-search/` default.
  - Unit: `test_env_var_relative_path` — set `ARCHON_SEARCH_CONFIG=relative/path.toml`; **Chosen behavior**: resolve against `Path.cwd()` — `get_default_config_path()` should return `Path.cwd() / value` for relative paths (same behavior as `Path(value)` when no tilde). This is safe because the function is used as a config file path. Assert `result == (Path.cwd() / 'relative/path.toml').resolve()` or similar. Document this in the implementation with a comment.
  - Checkpoint: `uv run pytest packages/archon-search/tests/test_config.py -k "env_var" -v --no-cov`

#### Task 1.7 — Rename `~/.archon/` defaults in `key_manager.py`, `install.py`, `jobs/model.py`, `cli/ingest.py`, and platform files
- [x] **File**: `packages/archon-search/archon_search/key_manager.py`, `packages/archon-search/archon_search/install.py`, `packages/archon-search/archon_search/jobs/model.py`, `packages/archon-search/archon_search/cli/ingest.py`, `packages/archon-search/archon_search/platform/macos.py`, `packages/archon-search/archon_search/platform/linux.py`
- **Depends on**: Task 1.5
- **Description**:
  - **`key_manager.py`**: change `KEY_FILE = Path("~/.archon/.search.env").expanduser()` to `Path("~/.archon-search/.search.env").expanduser()`. Also add a `ARCHON_SEARCH_KEY_FILE` env var override parallel to `ARCHON_SEARCH_CONFIG`: if `ARCHON_SEARCH_KEY_FILE` is set, use that path instead of `~/.archon-search/.search.env`. Parent `search_cmd.py` (Task 1.8) will inject this pointing to `~/.archon/.search.env` for backward compatibility. **Implementation approach**: Implement the env var override at the MODULE LEVEL — replace the constant definition with: `KEY_FILE: Path = Path(os.environ.get('ARCHON_SEARCH_KEY_FILE', '~/.archon-search/.search.env')).expanduser()`. This works because the parent sets env vars via `subprocess.run(env=...)` before the child process imports the module. **Test note**: `test_key_file_env_override` must set the env var via `monkeypatch.setenv('ARCHON_SEARCH_KEY_FILE', '/tmp/test.env')` BEFORE the module is (re)imported — use `importlib.reload(key_manager)` after setting the env var, then assert `key_manager.KEY_FILE == Path('/tmp/test.env')`. Do NOT monkeypatch `KEY_FILE` directly (that bypasses the env var mechanism being tested).
  - **`install.py` line ~151**: change the fallback `Path.home() / ".archon" / "archon-search.toml"` to `Path.home() / ".archon-search" / "archon-search.toml"` in `configure_gpu_provider()`.
  - **`jobs/model.py`**: change `JOBS_FILE = Path.home() / ".archon" / "archon-search-jobs.json"` to `Path.home() / ".archon-search" / "archon-search-jobs.json"`.
  - **`cli/ingest.py` line ~20**: change the default ingest path from `Path.home() / ".archon" / "history" / "sessions"` to `Path.home() / ".archon-search" / "history" / "sessions"` (preserving the subdirectory structure — only the root `.archon` prefix changes). Update the echo message to match. **Do NOT change to `data/` — the directory structure `history/sessions/` is the expected standalone convention.** Users who want a different path must pass `--path` explicitly.
  - **`platform/macos.py`**: change `cwd` (line ~71), `config_path` (line ~72), and `log_path` (line ~73) from `.archon/` to `.archon-search/`.
  - **`platform/linux.py`**: change `cwd` (line ~92) and `config_path` (line ~93) from `.archon/` to `.archon-search/`.
- **Releasable**: after this task, every default path in the package points to `~/.archon-search/`.
- **Tests (TDD)**:
  - Unit (`tests/test_job_store.py`): `test_jobs_file_default` — assert `JOBS_FILE` ends with `.archon-search/archon-search-jobs.json`.
  - Unit (`tests/test_key_manager.py`): `test_key_file_default_path` — assert `key_manager.KEY_FILE` resolves to `~/.archon-search/.search.env`.
  - Unit (`tests/test_key_manager.py`): `test_key_file_env_override` — set `ARCHON_SEARCH_KEY_FILE=/tmp/test.env`; assert `KEY_FILE` (or the key-loading function) uses that path instead.
  - Unit (`tests/test_install.py` or equivalent): `test_install_fallback_config_path` — assert `configure_gpu_provider()` (or the relevant function) creates/references config at `~/.archon-search/archon-search.toml`, not `~/.archon/archon-search.toml`.
  - Unit (`tests/cli/test_ingest.py`): `test_default_ingest_path` — assert default path ends with `.archon-search/history/sessions`.
  - Unit (`tests/test_service_macos.py`): service label `com.archon.search` must NOT change — verify plist label is unchanged.
  - Unit (`tests/test_service_macos.py`): `test_service_label_unchanged` — assert generated plist `Label` == `com.archon.search`.
  - Unit (`tests/test_service_macos.py`): `test_cwd_is_archon_search` — assert generated plist `WorkingDirectory` ends with `.archon-search`.
  - Unit (`tests/test_service_linux.py`): `test_service_name_unchanged` — assert generated systemd unit file `[Unit] Description` or service name contains `archon-search` (not `archon`).
  - Unit (`tests/test_service_linux.py`): `test_cwd_is_archon_search` — assert generated unit file `WorkingDirectory` ends with `.archon-search`.
  - Unit (`tests/test_service_macos.py`): `test_config_path_is_archon_search` — assert generated plist `ProgramArguments` or `EnvironmentVariables` references `~/.archon-search/archon-search.toml`.
  - Unit (`tests/test_service_linux.py`): `test_config_path_is_archon_search` — assert generated systemd unit `ExecStart` or environment references `~/.archon-search/archon-search.toml`.
  - Checkpoint: `uv run pytest packages/archon-search/tests/test_job_store.py packages/archon-search/tests/test_key_manager.py packages/archon-search/tests/test_service_macos.py packages/archon-search/tests/test_service_linux.py -v --no-cov`

#### Task 1.8 — Inject `ARCHON_SEARCH_CONFIG` from parent `search_cmd.py`
- [x] **File**: `archon/cli/search_cmd.py`
- **Depends on**: Task 1.6
- **Description**:
  - In `_run_archon_search()`, replace `subprocess.run(["archon-search", *args])` with `subprocess.run(["archon-search", *args], env={**os.environ, "ARCHON_SEARCH_CONFIG": str(Path.home() / ".archon" / "archon-search.toml")})`.
  - Also inject `ARCHON_SEARCH_API_KEY`: before spawning, read `Path.home() / ".archon" / ".search.env"`. If it exists, parse lines matching `ARCHON_SEARCH_API_KEY=<value>` and add the key to the env dict. This prevents auth failures for users upgrading without moving their key file.
  - Also inject `ARCHON_SEARCH_KEY_FILE` pointing to `~/.archon/.search.env` (in addition to parsing and injecting the key value directly). This allows `key_manager.py`'s env var override to function as a fallback if direct key injection is not available.
  - **Implementation note**: The direct `ARCHON_SEARCH_API_KEY` value injection (parsing the key file and adding the key value directly to env) is a belt-and-suspenders approach alongside `ARCHON_SEARCH_KEY_FILE`. Since `key_manager.py` already handles key loading when `ARCHON_SEARCH_KEY_FILE` is set, the direct value injection is only needed if `key_manager.py`'s env var mechanism is not implemented (Task 1.7). Keep both for defense-in-depth, but note that they duplicate parsing logic. If `key_manager.py`'s env var is implemented, the `ARCHON_SEARCH_KEY_FILE` injection alone is sufficient.
  - Add `import os` and `from pathlib import Path` if not already present.
  - Both injections happen via `env=` on the single `subprocess.run` call — no other call sites need changing since all subcommands go through `_run_archon_search()`.
- **Releasable**: after this task, Archon users are fully protected from the default path change — their `~/.archon/archon-search.toml` is always used.
- **Tests (TDD)** — `tests/cli/test_search_cmd.py`:
  - Unit: `test_run_injects_archon_search_config` — mock `subprocess.run`; assert `env` kwarg contains `ARCHON_SEARCH_CONFIG` pointing to `~/.archon/archon-search.toml`.
  - Unit: `test_run_injects_api_key_when_key_file_exists` — mock key file with `ARCHON_SEARCH_API_KEY=testkey`; assert `env` contains `ARCHON_SEARCH_API_KEY=testkey`.
  - Unit: `test_run_no_api_key_when_key_file_missing` — no key file; assert `ARCHON_SEARCH_API_KEY` not in env (or inherits from `os.environ`).
  - Unit: `test_run_injects_key_file_path` — assert `env` kwarg contains `ARCHON_SEARCH_KEY_FILE` pointing to `~/.archon/.search.env`.
  - Unit: `test_run_does_not_log_api_key` — enable DEBUG logging via `caplog` fixture, call `_run_archon_search` with a mocked key file containing `ARCHON_SEARCH_API_KEY=secretkey`; assert `'secretkey'` does not appear in any log record message.
  - Checkpoint: `uv run pytest tests/cli/test_search_cmd.py -v --no-cov`

#### Task 1.9 — Update documentation files
- [x] **File**: `packages/archon-search/archon-search.toml.example`, `packages/archon-search/README.md`
- **Depends on**: Tasks 1.5, 1.7
- **Description**:
  - **`archon-search.toml.example`**: update line 2 instructional comment from `# Copy to ~/.archon/archon-search.toml` to `# Copy to ~/.archon-search/archon-search.toml`. Update all default value lines (`db_path`, `log_file`, `log_dir`, config path mentions) from `~/.archon/` to `~/.archon-search/`. Add a comment block noting the `ARCHON_SEARCH_CONFIG` env var.
  - **`README.md`**: update all `~/.archon/` path references (key file path, telemetry paths, config path — approximately 5 lines, excluding the `com.archon.search` service label which must not change).
- **Releasable**: after this task, documentation reflects the new defaults.
- **Tests (TDD)**: N/A — documentation task; verify manually by grep.
- **Checkpoint**: `grep -r "~/.archon[^-]" packages/archon-search/archon-search.toml.example packages/archon-search/README.md` must return empty.

#### Task 1.10 — Switch `pyproject.toml` to dynamic CalVer via hatch-vcs
- [x] **File**: `packages/archon-search/pyproject.toml`, `packages/archon-search/_version_scheme.py` (new), `packages/archon-search/archon_search/__main__.py` (new)
- **Depends on**: nothing
- **Description**:
  - **Pre-step (required for Task 2.6 CLI verification)**: Create `packages/archon-search/archon_search/__main__.py` with:
    ```python
    from archon_search.cli.main import main
    main()
    ```
    This enables `python -m archon_search`. Also add `@click.version_option` to `cli/main.py`'s Click group. See Task 2.6 for the required try/except pattern. Doing this in Phase 1 ensures it is included in the extraction and tested in the monorepo before Phase 2.
  - In `pyproject.toml`:
    - Replace `version = "26.4.0"` with `dynamic = ["version"]`.
    - Add `hatch-vcs` to `[build-system] requires` (alongside existing `hatchling`): `requires = ["hatchling", "hatch-vcs"]`.
    - Add section:
      ```toml
      [tool.hatch.version]
      source = "vcs"
      raw-options.version_scheme = "_version_scheme:calver_total_count"
      ```
      Note: `version-scheme` does not exist in `hatch-vcs`. The correct key is `raw-options.version_scheme`, which passes through to `setuptools-scm`.
  - Create `packages/archon-search/_version_scheme.py`:
    ```python
    import subprocess, datetime

    # setuptools-scm version_scheme protocol: first argument is a ScmVersion object,
    # not a plain string. config is the setuptools-scm config dict.
    def calver_total_count(version, config, **kwargs):
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            capture_output=True, text=True,
        )
        count = result.stdout.strip() if result.returncode == 0 else "0"
        now = datetime.datetime.now(datetime.timezone.utc)
        return f"{now.strftime('%y')}.{now.month}.{count}"
    ```
  - **Signature verification required**: Verify the actual `setuptools-scm` `version_scheme` callable protocol before implementation. If `setuptools-scm` uses a single-arg protocol `(version: ScmVersion) -> str`, change to `def calver_total_count(version)`. Run `python -c 'from setuptools_scm.version import get_no_local_node; import inspect; print(inspect.signature(get_no_local_node))'` to see an example of the actual protocol in use.
  - The callable signature matches the `setuptools-scm` `version_scheme` protocol where `version` is a `ScmVersion` object.
  - **Note**: Before the first `hatch build` in the new repo, ensure at least one git tag exists. Run `git tag 26.5.0 <initial-sha>` (or the actual CalVer value) if no tags are present; `hatch-vcs` relies on `setuptools-scm` which requires a base tag. This note applies to local development builds only. In the CI release workflow (Task 2.5), the tag is created in Step 3 before `hatch build` in Step 4, so no manual pre-tagging is needed in CI. For local development, create a tag once: `git tag 26.5.0 HEAD`.
  - After Phase 2 extraction, this file lands at the new repo root (stripped by `--path-rename packages/archon-search/:`).
- **Releasable**: after this task, `hatch build` from `packages/archon-search/` produces a wheel with a clean CalVer string.
- **Tests (TDD)** — `packages/archon-search/tests/`:
  - Unit: `test_calver_format` in `tests/test_version_scheme.py` — call `calver_total_count(None, {})` and assert the returned string matches `r"^\d{2}\.\d+\.\d+$"`.
  - Unit: `test_calver_on_git_failure` — mock `subprocess.run` to return non-zero; assert version still returns a valid `YY.M.0` string without raising.
  - Unit: `test_main_module_invocable` — run `python -m archon_search --version` (subprocess) or `python -m archon_search --help`; assert exit code is 0.
  - Checkpoint: `uv run pytest packages/archon-search/tests/test_version_scheme.py -v --no-cov`
  - Additional checkpoint: `cd packages/archon-search && hatch build --clean` — this must succeed and produce a `.whl` file. If it fails with `ImportError` or `ModuleNotFoundError` for `_version_scheme`, the file placement or `sys.path` is wrong; move `_version_scheme.py` inside `archon_search/` and update `pyproject.toml` to use `archon_search._version_scheme:calver_total_count`.

#### Task 1.11 — Update test assertions for new default paths
- [x] **File**: `packages/archon-search/tests/test_config.py`, `packages/archon-search/tests/test_job_store.py`, `packages/archon-search/tests/config/test_telemetry_config.py`, `packages/archon-search/tests/test_app.py`
- **Depends on**: Tasks 1.5, 1.7
- **Implementation approach**: Tasks 1.5, 1.7, and 1.11 are tightly coupled (changing defaults breaks test assertions). To comply with the commit-per-task rule while keeping CI green, implement them in order with a `# type: ignore` / skip marker in CI, OR (preferred) combine them into a single 'atomic' task. The implementer should merge the changes for (1.5 + 1.11-subset-for-config) and (1.7 + 1.11-subset-for-paths) into single implementations at the task boundary. When using `/implement-next`, treat '1.5+1.11-config-subset' as one implementation step and '1.7+1.11-path-subset' as another. The commit message should note both task IDs: e.g., `feat: rename default paths in config.py and update test assertions (Tasks 1.5 + 1.11 config-subset)`.
- **Description**:
  - Update every assertion that checks a **default value** containing `~/.archon/` — change these to `~/.archon-search/`.
  - Identify candidates: `grep -rn 'assert.*\.archon[^-]' packages/archon-search/tests/` — review each hit and change only default-value assertions.
  - Files confirmed to require changes (from brief): `test_config.py` (`get_default_config_path`, `db_path`, `log_file`), `test_job_store.py` (`JOBS_FILE`), `test_telemetry_config.py` (`log_dir`), `test_app.py` — **verify intent before changing**: line ~59 uses `/home/user/.archon/history` as an input to `path_to_collection_name()` path-sanitization logic. If this is testing the default history path, change to `~/.archon-search/history`. If this is an arbitrary input testing path sanitization, add to the 'Do NOT change' list. Inspect the assertion to determine which.
  - ~~`test_store.py`~~ — **Do NOT change**: lines 1415-1430 and 1597-1616 use `"~/.archon/search"` as explicit constructor input to test tilde-expansion behavior, not as a default-value assertion. Changing these breaks tilde-expansion regression coverage.
  - ~~`test_pipeline.py`~~ — **Do NOT change**: uses `cfg.db_path = "~/.archon/search"` explicitly to test tilde expansion in `create_pipeline()`, not the default value.
  - ~~`test_progress.py`~~ — **Do NOT change**: uses `IndexingStateStore(Path("~/.archon/search"))` as explicit constructor input. If line 241 is a default-value test (testing that no-path uses the default), it should be updated — verify before changing.
  - Do NOT change: `tests/test_sync.py` (uses `~/.archon/` as arbitrary input to path sanitization), `tests/test_sync_e2e.py` (line ~627: uses `~/.archon/` as test fixture data, not as a default path assertion — do not change), `tests/test_service_macos.py` (`com.archon.search` is a service label, not a path), `packages/archon-search/tests/eval/corpus/` — **Do NOT change**: eval corpus files contain `~/.archon/` references as content being indexed, not as path configuration. Changing them alters eval baselines.
- **Releasable**: after this task, the full archon-search test suite passes with the new defaults.
- **Tests (TDD)**:
  - Run all archon-search tests: `uv run pytest packages/archon-search/tests/ -q --no-cov` — all must pass.
  - Checkpoint: `uv run pytest packages/archon-search/tests/ -q --no-cov`

#### Task 1.12 — Full suite verification (both test trees)
- [x] **File**: N/A (verification task)
- **Depends on**: Tasks 1.1–1.11
- **Description**:
  - Run the complete monorepo test suite: `uv run pytest -q --no-cov` — all tests in both `tests/` and `packages/archon-search/tests/` must pass.
  - Verify no `~/.archon[^-]` references remain in archon-search source (excluding service labels): `grep -rn '~/.archon[^-]' packages/archon-search/archon_search/ packages/archon-search/tests/ --exclude-dir=eval/corpus` must return empty (except `com.archon.search` label string which is fine). Eval corpus is excluded: `packages/archon-search/tests/eval/corpus/` contains `~/.archon/` references as content being indexed, not as path configuration — do not change these files.
  - Verify no `_search_stubs_shim` imports remain: `grep -rn "_search_stubs_shim" packages/archon-search/` must return empty.
- **Releasable**: Phase 1 complete. The monorepo is CI-green and ready for extraction.
- **Tests (TDD)**: N/A — this is a verification checkpoint.
- **Checkpoint**: `uv run pytest -q --no-cov`

---

### Phase 2 — Extract the repo and publish
> **Releasable**: after each task is confirmed. Task 2.6 (first successful PyPI release) marks Phase 2 complete.

#### Task 2.1 — Extract git history with `git filter-repo`
- [x] **File**: N/A (git operation)
- **Depends on**: Task 1.12
- **Description**:
  - **Pre-check 1 — Verify which legacy paths have history (run this first):**
    ```bash
    git log --all --oneline -- archon/rag | head -5
    git log --all --oneline -- archon/search | head -5
    git log --all --oneline -- tests/rag | head -5
    git log --all --oneline -- tests/search | head -5
    ```
    If a path returns empty output, remove it from the `--path` and `--path-rename` arguments below.

  - **Pre-check 2 — Check for filename collisions between `archon/rag` and `archon/search`:**
    ```bash
    comm -12 \
      <(git log --all --name-only -- archon/rag | grep '^archon/rag/' | sed 's|^archon/rag/||' | sort -u) \
      <(git log --all --name-only -- archon/search | grep '^archon/search/' | sed 's|^archon/search/||' | sort -u)
    ```
    If this returns any filenames, both paths share files with the same name — after `--path-rename`, the last rename overwrites the first. The correct fallback is to omit the `--path-rename` for both legacy paths and leave them as `archon/rag/` and `archon/search/` in the extracted repo (their historical filenames are preserved correctly). Renaming to `archon_search_rag/` and `archon_search_search/` creates a package that cannot be imported.

  - Create a fresh clone of the monorepo in a temp directory (do NOT run filter-repo on the working copy — it rewrites the repo in place and the original must remain untouched):
    ```bash
    git clone . /tmp/archon-search-extract
    cd /tmp/archon-search-extract
    ```
  - Run in a single invocation (two passes on the same clone are not supported):
    ```bash
    git filter-repo \
      --path archon/rag --path archon/search \
      --path packages/archon-search \
      --path tests/rag --path tests/search \
      --path-rename packages/archon-search/: \
      --path-rename archon/rag:archon_search \
      --path-rename archon/search:archon_search
    ```
  - After rewriting: package files are at the repo root (stripped from `packages/archon-search/`); `tests/rag/` and `tests/search/` are preserved; historical `archon/rag` and `archon/search` files are renamed to `archon_search/`.
  - Verify the new root contains `pyproject.toml`, `archon_search/`, `tests/`, `README.md`.
  - **Post-extraction check**: Verify the new repo's `pyproject.toml` testpaths includes all test directories (`tests/`, and `tests/rag/`, `tests/search/` if they have history). Update `[tool.pytest.ini_options] testpaths` accordingly.
  - **Rollback**: discard the `/tmp/archon-search-extract` clone — the original monorepo is untouched.
- **Releasable**: after this task, a clean extracted repo with preserved history exists locally.
- **Tests (TDD)**: N/A — git operation; verify via `git log --oneline | head -10` and `ls` in the extracted clone.
- **Post-extraction**: Run `cd /tmp/archon-search-extract && uv lock` to generate a fresh `uv.lock` for the standalone repo (the monorepo's lockfile is workspace-aware and not valid for a standalone project). Commit the new lockfile.
- **Checkpoint**: `ls /tmp/archon-search-extract && git -C /tmp/archon-search-extract log --oneline | head -5`

#### Task 2.1b — Adapt eval/contract tests for standalone repo structure
- [x] **File**: `tests/eval/test_ci_contract.py`, `tests/eval/test_phase0_contract.py`, `tests/telemetry/test_docs_contract.py` (in extracted clone)
- **Depends on**: Task 2.1
- **Description**:
  - In the extracted clone at `/tmp/archon-search-extract`, handle the eval/contract tests that are coupled to the monorepo layout:
  - **`test_ci_contract.py` and `test_phase0_contract.py` — DELETE BOTH FILES**: These files verify compliance with the Archon monorepo's CI infrastructure and release process — they test `release.sh`, `Documentation/`, monorepo-level plan codex files, and workflow path patterns that do not exist in the standalone repo. Specifically: `test_ci_contract.py` references `_RELEASE_SH`, `_RELEASE_DOC`, `_PACKAGE_PYPROJECT`, `_NESTED_WORKFLOW_DIR`, and ~5 tests reading `release.sh`/`Documentation/release-process.md`, plus a `test_ci_gates_use_package_pytest_config` asserting `"cd packages/archon-search"` in workflow files. `test_phase0_contract.py` references `PLAN_CODEX`, `FEAT_038_ARTIFACT`, `DOC_INDEX` — all pointing to `Documentation/Backlog/` paths that raise `FileNotFoundError` in the standalone repo, failing 10+ tests. **Delete both files entirely**:
    ```bash
    rm tests/eval/test_ci_contract.py
    rm tests/eval/test_phase0_contract.py
    ```
  - **Add a note** in a new `tests/eval/README.md`: `# Eval tests — note: test_ci_contract.py and test_phase0_contract.py were deleted during standalone repo extraction (they verified monorepo CI/release compliance). Standalone CI contract tests should be added as a follow-up.`
  - **`tests/telemetry/test_docs_contract.py`**: change `Path(__file__).parents[4]` to `Path(__file__).parents[2]`. For the ADR-dependent tests (`test_adr_10_exists_and_documents_required_sections`, `test_arch_doc_mentions_telemetry_section`, `test_doc_index_includes_telemetry_plan_and_adr`): add a skip guard — `if not ADR_PATH.exists(): pytest.skip('ADR not present in standalone repo')` (or similar) at the top of each such test. The telemetry README and stats tests are standalone-relevant and must be kept.
  - Commit these changes to the extracted clone before pushing in Task 2.2.
- **Releasable**: after this task, eval/contract tests reflect standalone repo structure.
- **Tests (TDD)**:
  - Checkpoint: `cd /tmp/archon-search-extract && uv run pytest tests/telemetry/ -v --no-cov` — the two eval contract test files have been deleted, so only telemetry tests run here. All must pass.

#### Task 2.2 — Create GitHub repo and push extracted history
- [x] **File**: N/A (GitHub operation)
- **Depends on**: Task 2.1b
- **Description**:
  - Create a new empty GitHub repo named `archon-search` under the same owner as Archon (do not initialize with README).
  - Add the new remote and push:
    ```bash
    cd /tmp/archon-search-extract

    # Delete monorepo release tags from the extracted clone before pushing
    git tag -l | xargs git tag -d

    # Push only the main branch (not all refs from the monorepo)
    git push origin main
    ```
  - **Do NOT use `git push --mirror`** — it would push all monorepo release tags to the new repo, polluting the tag namespace and potentially causing `setuptools-scm` to pick up a stale Archon tag when computing the CalVer version.
  - Verify the new repo contains the expected commit history and file structure.
  - **Rollback**: delete the GitHub repo entirely if history looks wrong; discard the local clone; start Task 2.1 over.
- **Releasable**: after this task, the extracted repo is on GitHub.
- **Tests (TDD)**: N/A — manual verification.
- **Checkpoint**: `git -C /tmp/archon-search-extract log --oneline | head -10` matches the new repo's commit history on GitHub.

#### Task 2.3 — Migrate and update CI workflow files
- [x] **File**: `.github/workflows/archon-search-pr.yml` and `.github/workflows/archon-search-release.yml` (in new repo); same files in monorepo (**disable** — rename to `.disabled`, delete in Task 3.2)
- **Depends on**: Task 2.2
- **Description**:
  - Copy `.github/workflows/archon-search-pr.yml` and `archon-search-release.yml` from the monorepo to the new repo's `.github/workflows/` (the filter-repo did not preserve them — they live at the root `.github/` level, not under `packages/archon-search/`).
  - In the new repo: remove all `paths:` trigger filters (not needed in a dedicated repo). Remove all `working-directory: packages/archon-search` settings — package root is now the repo root. Ensure the PR workflow trigger in the new repo is: `on: pull_request:` (all PRs, no path filter) — removing the `paths:` filter from a `pull_request:` trigger means the workflow runs on every PR, which is the correct behavior for a dedicated repo.
  - In the monorepo: **Disable** (do NOT delete) both `archon-search-pr.yml` and `archon-search-release.yml`: rename them with a `.disabled` suffix (e.g., `archon-search-pr.yml.disabled`). This stops them from triggering while preserving them for rollback. **Do NOT delete them yet** — they serve as rollback artifacts. The actual deletion happens in Task 3.2 alongside the source removal, once the PyPI release is confirmed in Task 2.6.
  - **Important**: The existing `archon-search-release.yml` in the monorepo is an eval-gate workflow (runs pytest + coverage thresholds), NOT a publish workflow. Task 2.5 will overwrite it with the publish workflow. The eval gate must be preserved: either (a) merge the eval/test job as a prerequisite job in the new release workflow (recommended), or (b) keep it as a separate `archon-search-ci.yml` file. Document which approach is chosen before implementing Task 2.5.
  - Commit and push both sets of changes.
- **Releasable**: after this task, CI runs on the new repo's own workflows.
- **Tests (TDD)**: N/A — verify by triggering a test PR on the new repo and confirming the workflow runs.
- **Checkpoint**: open a test PR on the new repo; confirm `archon-search-pr.yml` triggers and passes.

#### Task 2.4 — Configure PyPI OIDC trusted publisher
- [x] **File**: N/A (PyPI web configuration)
- **Depends on**: Task 2.2
- **Description**:
  - On PyPI (`pypi.org`): go to the `archon-search` project page → Publishing → Add a new pending publisher.
  - Provide: GitHub owner, repo name (`archon-search`), workflow file name (`archon-search-release.yml`), environment name (if used).
  - The package project on PyPI must be pre-registered (create it if not done by Task 0.1 check) before OIDC can be configured.
  - This step must complete before the release workflow runs for the first time.
- **Releasable**: after this task, the release workflow has publish permission without any stored secrets.
- **Tests (TDD)**: N/A — manual configuration; verify the pending publisher appears in the PyPI publishing settings.
- **Checkpoint**: PyPI publishing settings show the pending trusted publisher for `archon-search-release.yml`.

#### Task 2.5 — Add automated release workflow to new repo
- [x] **File**: `.github/workflows/archon-search-release.yml` (in new repo)
- **Depends on**: Tasks 2.3, 2.4
- **Description**:
  - The workflow triggers on push to `main` (with a `paths:` filter to skip README-only changes, or use `[skip release]` commit convention).
  - Add `concurrency: group: release, cancel-in-progress: false` to serialize runs.
  - `actions/checkout` must use `fetch-depth: 0` (full history for accurate commit count).
  - Steps:
    0. Run tests as prerequisite job (runs in parallel with or before the publish job): `uv run pytest -q --no-cov`. The publish job must depend on the test job passing (`needs: test`). The eval-gate coverage thresholds and eval-slice checks from the existing `archon-search-release.yml` must be included in this step. **Decision checkpoint**: in Task 2.3, document whether the eval gate is merged into this workflow as a `test:` job, or kept as a separate `archon-search-ci.yml` that protects PRs. Either way, the publish step must not run if tests fail.
    1. Compute CalVer tag: `TAG=$(python -c "import datetime; now=datetime.datetime.now(datetime.timezone.utc); print(f'{now.strftime(\"%y\")}.{now.month}.')" )$(git rev-list --count HEAD)`.
    2. Check for existing tag: `git tag -l "$TAG"` — skip if already tagged.
    3. Create and push tag using `GITHUB_TOKEN`: `git tag "$TAG" && git push origin "$TAG"`. (`GITHUB_TOKEN`-pushed tags do not re-trigger `on: push: tags:` — no infinite loop.)
    4. Build wheel: `hatch build`.
    5. Publish to PyPI via `pypa/gh-action-pypi-publish` (OIDC, no stored secrets).
- **Recovery**: If a workflow run is cancelled or fails after tag creation but before PyPI publish, re-running the workflow will skip (`git tag -l "$TAG"` returns non-empty) and never publish. Recovery procedure: delete the tag (`git tag -d "$TAG" && git push origin :refs/tags/$TAG`), then re-trigger the workflow. Add this to the workflow's README or runbook.
- **Releasable**: after this task, every push to `main` produces an automatic PyPI release.
- **Tests (TDD)**: N/A — verified by triggering (Task 2.6).
- **Checkpoint**: workflow YAML is syntactically valid (`yamllint .github/workflows/archon-search-release.yml`).

#### Task 2.6 — Trigger first release and verify PyPI publication
- [x] **File**: N/A (trigger + verify)
- **Depends on**: Task 2.5
- **Description**:
  - **Required sub-task (before first push)**: Ensure `archon_search/__main__.py` exists with `from archon_search.cli.main import main; main()` so `python -m archon_search` works. Ensure `cli/main.py`'s Click group includes:
    ```python
    try:
        _version = importlib.metadata.version('archon-search')
    except importlib.metadata.PackageNotFoundError:
        _version = 'dev'

    @click.version_option(version=_version, prog_name='archon-search')
    ```
    (import `importlib.metadata` at top). This pattern matches the existing `server/app.py:28-31` approach. Required to prevent `PackageNotFoundError` crashes when running from source without `pip install -e .`. These MUST be added in Task 1.10 (Phase 1) so they are part of the extraction. If they are absent at this stage, go back and add them to the monorepo BEFORE re-running the extraction.
  - **Pre-condition**: Before Task 2.6, verify that `archon_search/__init__.py` or `archon_search/cli/main.py` reads the version via `importlib.metadata.version('archon-search')` and exposes it via `--version`. If not, this is addressed by the Required sub-task above.
  - Push a trivial commit to `main` on the new repo (e.g., update a comment in README) to trigger the release workflow.
  - Monitor the Actions run — confirm tag creation, `hatch build`, and `pypa/gh-action-pypi-publish` succeed.
  - Verify from PyPI: `pip install archon-search` (in a fresh venv) and `python -m archon_search --version` must output the expected CalVer string (e.g., `26.5.N`).
- **Releasable**: Phase 2 complete. `pip install archon-search` works from PyPI.
- **Tests (TDD)**: N/A — live verification.
- **Checkpoint**: `pip install archon-search && python -m archon_search --version` in a clean venv; output matches `r"^\d{2}\.\d+\.\d+$"`.

---

### Phase 3 — Update parent Archon
> **Releasable**: after Task 3.1, Archon can be released without `packages/archon-search/` in the repo. Task 4.1 closes the feature.

#### Task 3.1 — Update root `pyproject.toml` to consume archon-search from PyPI
- [ ] **File**: `pyproject.toml` (monorepo root)
- **Depends on**: Task 2.6
- **Description**:
  - In `[project] dependencies`, replace `archon-search = { path = "packages/archon-search" }` with `archon-search = ">=<first-published-version>"` where `<first-published-version>` is the CalVer string verified in Task 2.6 (e.g., `>=26.5.0`). Use `>=` (floor pin), not `~=` (compatible release). The `~=` specifier binds the upper bound to the same `YY.M` prefix, which would block updates every time the calendar month changes. `>=26.5.N` allows all future releases while preventing downgrades.
  - In `[tool.uv.sources]`, remove the `archon-search` workspace entry (the path override).
  - After editing `pyproject.toml` and `[tool.uv.sources]`: run `uv lock` to regenerate the full lockfile with `archon-search` now resolved from PyPI. A plain `uv lock` is correct here — `--upgrade-package` is not needed because the source type change (path → registry) causes a full re-resolution regardless. Commit the updated lockfile.
- **Releasable**: after this task, `uv sync` installs archon-search from PyPI rather than the local path.
- **Tests (TDD)** — `tests/`:
  - Run `uv sync && uv run pytest -q --no-cov` — full monorepo test suite must pass with archon-search from PyPI.
  - Checkpoint: `uv run pytest -q --no-cov` after `uv sync`.
  - Checkpoint: `grep archon-search uv.lock` — the entry must reference a PyPI URL (e.g., `https://files.pythonhosted.org/`) not a local path. If it still shows `path = 'packages/archon-search'`, the workspace sources entry was not fully removed — check `[tool.uv.workspace]` and `[tool.uv.sources]` in the root `pyproject.toml`.

#### Task 3.2 — Remove `packages/archon-search/` from the monorepo
- [ ] **File**: `packages/archon-search/` (delete entire directory)
- **Depends on**: Task 3.1
- **Description**:
  - Run `git rm -r packages/archon-search/` and commit in a single git commit with message `chore: remove archon-search subpackage (now published on PyPI)`.
  - **Rollback**: `git revert <commit>` to restore the directory. Any PyPI versions published before rollback are immutable after 24 hours — release a patch if needed.
  - Also remove the root `tests/_search_stubs.py` comment added in Task 1.1 (or delete the file if it is only referenced by archon-search tests — confirm with `grep -r "_search_stubs" tests/`).
  - Also delete the disabled workflow files created in Task 2.3: `git rm .github/workflows/archon-search-pr.yml.disabled .github/workflows/archon-search-release.yml.disabled` (confirm the exact filenames match what was created in Task 2.3). Include this deletion in the same commit.
- **Releasable**: after this task, the monorepo no longer contains the archon-search source.
- **Tests (TDD)**:
  - Run `uv run pytest -q --no-cov` — all parent Archon tests must pass with the directory removed.
  - Checkpoint: `uv run pytest -q --no-cov`

---

### Phase 4 — Verification & Documentation

#### Task 4.1 — Final verification & documentation update
- [ ] **File**: N/A (agent task)
- **Depends on**: all prior tasks
- **Description**:
  - Spawn an agent to discover all documentation in the project (CLAUDE.md, README.md, ADRs, Architecture docs, UserManual, Backlog/Completed, RELEASE.md, examples/) and update every file whose content is affected by this feature:
    - CLAUDE.md: update `[search]` config section to note `ARCHON_SEARCH_CONFIG` env var; update any `packages/archon-search` references to reflect it is now an external PyPI dependency.
    - `Documentation/Architecture/`: update any component diagrams or descriptions that reference `packages/archon-search/` as a local path.
    - `examples/config.toml.example`: verify `[search]` section no longer needs a `db_path` local default note.
    - Move this plan file from `Backlog/` to `Completed/` and set `**Status**: Complete`.
  - Verify all acceptance criteria below are met before marking this task complete.
- **Releasable**: after this task, the feature is fully verified and all documentation reflects the delivered implementation.
- **Acceptance criteria** (must all pass):
  - [ ] `pip install archon-search` works in a clean venv; `python -m archon_search --version` outputs a CalVer string matching `r"^\d{2}\.\d+\.\d+$"`.
  - [ ] `uv run pytest -q --no-cov` passes in the Archon monorepo with `packages/archon-search/` absent.
  - [ ] No `~/.archon[^-]` references remain in archon-search source (verified in Task 1.12 before `packages/archon-search/` was deleted; this criterion is satisfied if Task 1.12 passed). In the new standalone repo, also verify: `grep -rn '~/.archon[^-]' archon_search/` returns empty.
  - [ ] No `_search_stubs_shim` imports exist anywhere in the repo.
  - [ ] `_PENDING_MIGRATION` in archon-search's `test_no_archon_imports.py` is empty — verified in Task 1.4 (monorepo) and should be confirmed in the new standalone repo: `grep -r "_PENDING_MIGRATION" tests/test_no_archon_imports.py` (in the new repo, not the deleted monorepo path) returns empty set or the set contains no phantom entries.
  - [ ] Archon's `_run_archon_search()` injects `ARCHON_SEARCH_CONFIG` pointing to `~/.archon/archon-search.toml`.
  - [ ] New repo's release workflow ran successfully; PyPI package page shows the published version.
  - [ ] CLAUDE.md accurately reflects archon-search as an external PyPI dependency.
  - [ ] A GitHub issue has been created in the archon-search repo titled 'Add startup warning for standalone users migrating from ~/.archon/ to ~/.archon-search/' — this is the follow-up issue required before the PyPI release is publicly announced to standalone users.
- **Tests (TDD)**: N/A — this is a verification and documentation task.
- **Checkpoint**: manually confirm every acceptance criterion above is checked.
