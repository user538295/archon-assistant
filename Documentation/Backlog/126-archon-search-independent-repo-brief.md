# Feature Brief: Extract archon-search as an Independent Repository

## Problem
`archon-search` lives inside the Archon monorepo but is logically a standalone product. This blocks independent versioning, publishing, and use by projects other than Archon.

## Goal
`archon-search` is a fully independent Python package: its own git repo, own PyPI package, own automated CalVer releases — requiring zero manual version management. Archon consumes it from PyPI like any other dependency.

## Users & Context
- **archon-search users**: developers or teams who want a local hybrid-search server independently of Archon.
- **Archon maintainer (you)**: wants to release archon-search changes without touching the Archon repo, and vice versa.
- **Archon runtime**: spawns `archon-search` as a subprocess; reads results over HTTP. No change to this flow.

## Core Flow

**Pre-work (do before Phase 1):** Verify that `archon-search` is an available name on PyPI at pypi.org/project/archon-search. If taken, a rename is required before any other work proceeds.

### Phase 1 — Decouple within the monorepo (committed, tested, merged first)
1. Copy `tests/_search_stubs.py` from the monorepo root into `packages/archon-search/tests/` as a self-contained file. After copying, add a comment at the top of the root `tests/_search_stubs.py` (or delete it if it's only used by archon-search tests) noting that the canonical copy is now `packages/archon-search/tests/_search_stubs.py`. The root copy can be removed once Phase 3 is complete and archon-search is no longer part of the monorepo.
1b. Update `packages/archon-search/tests/conftest.py`: replace any inline stub code that duplicates what's in the copied `_search_stubs.py` with an import of `install_stubs()` from `_search_stubs`. Remove all inline code from conftest.py that is now provided by `install_stubs()` — diff the two files to identify exact lines. This includes the env-var settings (approximately lines 27-30 and 47) in addition to the module-level stubs (approximately lines 50-101). Also remove the `_search_stubs_shim` import.
2. Delete `packages/archon-search/tests/_search_stubs_shim.py`.
   **2 (verification):** After Steps 1-2, run `uv run pytest packages/archon-search/tests/ -q` to confirm the stubs migration is clean before proceeding to path changes.
3. Remove `pipeline.py` and `mcp.py` from `_PENDING_MIGRATION` in `test_no_archon_imports.py` (no actual violations exist — the list is phantom).
4. Change the three hardcoded `~/.archon/` default paths in `config.py` to `~/.archon-search/` (`SearchConfig.db_path`, `SearchConfig.log_file`, and `TelemetryConfig.log_dir` (the telemetry log directory, a separate nested dataclass)).
4b. Change `KEY_FILE` in `key_manager.py` from `~/.archon/.search.env` to `~/.archon-search/.search.env`. Also update `packages/archon-search/README.md` anywhere it references the key file path.
4c. Change the default ingest path in `cli/ingest.py` from `~/.archon/history/sessions` to either `~/.archon-search/data/` or require `--path` explicitly (removing the default). The current default only makes sense when running embedded within Archon — standalone users have no `~/.archon/history/sessions` directory.
4d. Update `archon_search/platform/macos.py` and `archon_search/platform/linux.py` — change all hardcoded `~/.archon/` references to `~/.archon-search/` (including `cwd`, `config_path`, and `log_path` values used when writing the launchd plist / systemd unit). For Archon-managed installations, the `ARCHON_SEARCH_CONFIG` env var injected by Step 7 overrides the config path, so this change only affects standalone installs.
4e. Change `JOBS_FILE` in `archon_search/jobs/model.py` from `~/.archon/archon-search-jobs.json` to `~/.archon-search/archon-search-jobs.json`.
5. Change the default config file path in `get_default_config_path()` from `~/.archon/archon-search.toml` to `~/.archon-search/archon-search.toml`.
5b. In `archon_search/install.py`, change the fallback config path in `configure_gpu_provider()` (line ~151) from `Path.home() / '.archon' / 'archon-search.toml'` to `Path.home() / '.archon-search' / 'archon-search.toml'`. This is a separate code path from `get_default_config_path()` — Step 5 alone does not cover it.
6. Add `ARCHON_SEARCH_CONFIG` env var support in `config.py`: in `get_default_config_path()` (or at the top of `load_config()`), check `os.environ.get("ARCHON_SEARCH_CONFIG")` first — if set, use that value as the config file path (after expanding `~` via `os.path.expanduser()`). Note: the platform service files (`macos.py`, `linux.py`) already inject this env var into the service environment, but `config.py` does not yet read it — this step wires the two ends together. Precedence: `ARCHON_SEARCH_CONFIG` env var > any future `--config` CLI flag > `~/.archon-search/archon-search.toml` default.
7. Modify `_run_archon_search()` in `archon/cli/search_cmd.py` to inject `ARCHON_SEARCH_CONFIG` into the subprocess environment: pass `env={**os.environ, 'ARCHON_SEARCH_CONFIG': str(Path.home() / '.archon' / 'archon-search.toml')}` to `subprocess.run()`. Using `Path.home()` instead of `~` ensures the path is fully expanded before being passed. This single change covers all subcommands that go through `_run_archon_search()`.
7b. To preserve the API key for existing Archon users: update `_run_archon_search()` to also inject `ARCHON_SEARCH_API_KEY` by reading `~/.archon/.search.env` (the old key file location) before spawning. If the file exists and contains `ARCHON_SEARCH_API_KEY=<value>`, inject it as an env var so the subprocess uses the existing key. This prevents auth failures for users upgrading from pre-extraction Archon without moving their key file. Alternatively, make `key_manager.py` read its key file path from a `ARCHON_SEARCH_KEY_FILE` env var (analogous to `ARCHON_SEARCH_CONFIG`), and have `_run_archon_search()` inject that too — this is cleaner for long-term maintenance.
8. Update documentation files:
   - `archon-search.toml.example`: update the instructional comment (line 2: `# Copy to ~/.archon/archon-search.toml`) and all default value lines (db_path, log_file, log_dir, config path) from `~/.archon/` to `~/.archon-search/`. Add a note about `ARCHON_SEARCH_CONFIG` env var.
   - `packages/archon-search/README.md`: update all `~/.archon/` path references (key file path, telemetry paths, config path — approximately 5 lines). Step 4b covers the key file specifically; this step covers the remainder.
9. Switch `pyproject.toml` from `version = "26.4.0"` (static) to `dynamic = ["version"]` with `hatch-vcs`.
10. Add a `[tool.hatch.version]` section in `pyproject.toml` using `hatch-vcs`. Because hatch-vcs's default CalVer produces `.devN+gabcdef` suffixes on untagged commits, add a custom version scheme: create `packages/archon-search/_version_scheme.py` with a callable that runs `git rev-list --count HEAD` and returns `YY.M.<total-commit-count>` as a clean string. (After `git filter-repo` strips the `packages/archon-search/` prefix in Phase 2, this file lands at the new repo root. This placement relies on the exact `--path-rename packages/archon-search/:` in Phase 2 step 1 — any different rename target would break the module path.) Reference it in `pyproject.toml` as `version-scheme = '_version_scheme:calver_total_count'`. Also add `hatch-vcs` to `[build-system] requires` in `pyproject.toml`.
11. Update test assertions that check the **default value** of moved paths (e.g., assertions that `cfg.db_path == '~/.archon/search'` or `cfg.telemetry.log_dir == '~/.archon/search-logs'`) — change these to `~/.archon-search/`. Do NOT change tests that use `~/.archon/` paths as arbitrary input to test tilde-expansion or string-sanitization logic (those tests verify behavior independent of defaults), and do NOT change eval corpus fixture files. Identify candidates with `grep -r 'assert.*\.archon' packages/archon-search/tests/` for assertion-only hits.

Files confirmed to contain **default-value assertions** (must change to `~/.archon-search/`):
- `tests/test_config.py` — `get_default_config_path()` and default `db_path`/`log_file` assertions
- `tests/test_store.py` — default `_db_path` assertions including directory-creation assertions (e.g., line ~1616)
- `tests/test_pipeline.py` — default store `_db_path` assertion
- `tests/test_progress.py` — default `_state_dir` assertion
- `tests/test_job_store.py` — `JOBS_FILE` assertion (now updated by Step 4e)
- `tests/config/test_telemetry_config.py` — `log_dir` default assertions

Files containing `~/.archon/` references that **must NOT change** (they test arbitrary input or service labels):
- `tests/test_sync.py` — uses old path as arbitrary input to path sanitization; leave as-is
- `tests/test_service_macos.py` — `com.archon.search` is a service label, not a path; leave as-is

Then run all tests in both `tests/` and `packages/archon-search/tests/` — all must pass.

**Done when:** all tests in both `tests/` and `packages/archon-search/tests/` pass on CI with no archon imports in the search package.

### Phase 2 — Extract the repo and publish
1. Clone the monorepo locally; run `git filter-repo` with all five historical paths combined with the prefix strip in a single invocation (running two separate `git filter-repo` passes on the same clone is not supported). First, verify which paths have historical commits — if the output is empty for a path, omit it from the command (including a path with no history is harmless but misleading):
   ```
   git log --all --oneline -- tests/search | head -5
   git log --all --oneline -- tests/rag | head -5
   ```
   Then run:
   ```
   git filter-repo \
     --path archon/rag --path archon/search \
     --path packages/archon-search \
     --path tests/rag --path tests/search \
     --path-rename packages/archon-search/:
   ```
   Note: `tests/rag` and `tests/search` have no `packages/archon-search/` prefix, so the `--path-rename` does not affect them. After rewriting, the new repo structure will be: package files at the repo root (stripped from `packages/archon-search/`), plus `tests/rag/` and `tests/search/` directories preserved at their original paths. Historical commits from the `archon/rag` and `archon/search` eras will show files at those paths — the git log will have a multi-layout history, which is expected. Only the `packages/archon-search/` prefix is stripped (via `--path-rename`). If a cleaner history is desired, add `--path-rename archon/rag:archon_search` and `--path-rename archon/search:archon_search` to the filter-repo command. If continuous `git log --follow` and blame across the old `archon/rag` → current structure rename matters, these path-renames are required, not optional. If preserving blame continuity is not a priority, they can be omitted (the multi-layout history is then accepted as-is). **Recommendation:** include the path-renames for a cleaner result.
2. _(Combined with step 1 above — no separate step needed.)_
3. Push to a new GitHub repository (`archon-search`).
   **Rollback:** `git filter-repo` operates only on the local clone — the monorepo is untouched at this point. If the extracted history looks wrong, discard the local clone and start over. The push to the new GitHub repo can be undone by deleting the repo entirely.
4. Move `.github/workflows/archon-search-pr.yml` and `archon-search-release.yml` to the new repo; remove the `paths:` filter (not needed in a dedicated repo). When moving the workflow files, also remove any `working-directory: packages/archon-search` settings — in the new repo, the package root is the repo root. Also delete these two workflow files from the monorepo's `.github/workflows/` directory (or remove the archon-search `paths:` triggers from them) once the new repo workflows are confirmed working. During the Phase 2–3 transition window, the monorepo archon-search workflows may still trigger on PRs — after the new repo workflows are confirmed working, delete or disable the monorepo archon-search workflow files immediately, don't wait until Phase 3 is complete.
5. Configure PyPI trusted publisher for the new repo **before** adding the release workflow. PyPI trusted publisher setup must happen before the first workflow run. On PyPI: go to the project page → Publishing → Add a new pending publisher. Provide the GitHub owner, repo name, workflow file name, and environment name (if used). The package name must be pre-registered on PyPI (create the project) before OIDC can be configured.
6. Add a release workflow: on push to `main`, the workflow runs `git rev-list --count HEAD` to compute the total commit count, then creates and pushes a tag of the form `YY.M.<total-commit-count>`. The subsequent `hatch build` step reads that tag via hatch-vcs and produces the wheel. Finally, `pypa/gh-action-pypi-publish` (OIDC — no stored secret) publishes to PyPI.
   **Important:** the tag must be pushed using `GITHUB_TOKEN` (not a PAT). GitHub Actions `GITHUB_TOKEN`-generated pushes do not re-trigger `on: push: tags:` workflows, preventing infinite loops.
   **Concurrency:** add `concurrency: group: release, cancel-in-progress: false` to the release workflow to serialize releases and prevent two simultaneous runs from computing the same tag.
   **Skipping non-code changes:** to avoid publishing for README edits or CI config changes, add a `paths:` filter to the release workflow trigger, or adopt a `[skip release]` commit message convention. Alternatively, trigger on tag push only (`on: push: tags: ['[0-9]*']`) and have a separate step push the tag — this is the conventional pattern.
   **fetch-depth:** the `actions/checkout` step in the release workflow must use `fetch-depth: 0` (full history) — otherwise `git rev-list --count HEAD` returns 1 on a shallow clone, producing wrong CalVer versions.
   **First-tag bootstrap:** after `git filter-repo`, the new repo has no tags. The release workflow should: (1) compute the CalVer tag string using `git rev-list --count HEAD`, (2) check if a tag already exists for this version (`git tag -l "$TAG"`), (3) if not, create and push it (`git tag "$TAG" && git push origin "$TAG"`), (4) then run `hatch build`. Do not create a throwaway placeholder — compute and push the real tag directly.
7. Trigger first release; verify the package appears on PyPI.

**Done when:** `pip install archon-search` works from PyPI and `python -m archon_search --version` outputs the expected CalVer string.

### Phase 3 — Update parent Archon
1. In the root `pyproject.toml`, replace `archon-search = { path = "packages/archon-search" }` with `archon-search = "~=<first-published-version>"`. The `~=` (compatible-release) specifier is equivalent to `>=X.Y.Z, ==X.Y.*` per PEP 440 — it constrains to the same `YY.M` prefix while allowing any `N` increment. The Archon maintainer updates the lower bound when intentionally consuming new features.
2. Remove `packages/archon-search/` directory from the monorepo in a single git commit.
   **Rollback:** revert that commit to restore the directory. Note that any PyPI versions published before the rollback are immutable after 24 hours — if a broken package was published, release a patch version with the fix rather than attempting to delete from PyPI.
3. Remove the archon-search entry from the `[tool.uv.sources]` workspace block.
4. Verify parent tests pass (`uv run pytest`).
5. Commit and push.

**Done when:** Archon's CI passes with `packages/archon-search/` removed and archon-search installed from PyPI.

## In Scope
- Standalone `_search_stubs.py` in archon-search tests (shim deleted)
- `~/.archon-search/` as the new standalone default for all data paths, including the API key file (`key_manager.py`) and default ingest path (`cli/ingest.py`)
- `ARCHON_SEARCH_CONFIG` env var for config file path override
- Parent Archon passes explicit `ARCHON_SEARCH_CONFIG` when spawning archon-search (backwards compat)
- Dynamic CalVer via `hatch-vcs` (fully automatic, no human version bumps)
- Automated PyPI publish via OIDC trusted publisher on every push to `main`
- Full git history preserved across all five historical paths (`archon/rag`, `archon/search`, `packages/archon-search`, `tests/rag`, `tests/search`)
- Parent updated to consume archon-search from PyPI

## Out of Scope
- Changing the HTTP API contract between Archon and archon-search (no changes to `SearchClient`)
- Migrating existing user data from `~/.archon/search` to `~/.archon-search/` (user opt-in; documented)
- MCP or CLI interface changes
- Renaming the PyPI package slug (stays `archon-search`)

## Key Decisions
- **Dynamic CalVer via `hatch-vcs` with custom `YY.M.<total-commit-count>` scheme**: eliminates all manual version management; every commit to `main` produces a clean, monotonically increasing version in the same format Archon uses. Requires a small `_version_scheme.py` module at the repo root containing a callable, referenced from `pyproject.toml` by dotted path. The callable runs `git rev-list --count HEAD` and cannot be inlined in TOML. It computes the total commit count — not hatch-vcs's per-tag `distance`, which resets to 0 on each new tag and produces `.devN+gabcdef` suffixes on untagged commits. The custom callable bypasses hatch-vcs's default distance behavior and always returns a clean `YY.M.<total-commit-count>` string with no dirty markers.
- **Default path `~/.archon-search/`**: correct default for a standalone package; Archon overrides it explicitly via env var so existing users are unaffected without any data migration.
- **`ARCHON_SEARCH_CONFIG` env var** (not a CLI flag): avoids touching every subprocess call site; one env var in `search_cmd.py` covers all subcommands.
- **Phased extraction**: Phase 1 decoupling is committed and CI-green before any files move, giving a clean and verifiable extraction point.
- **PyPI OIDC trusted publisher**: no secrets stored in GitHub; publish permission is scoped to the workflow file path.
- **New repo under same GitHub owner as Archon**: zero setup overhead now; PyPI trusted publisher can be re-configured in 2 minutes if the repo is transferred to an org later. No impact on `pip install archon-search` either way.

## Edge Cases & Constraints
- **Existing Archon users with data at `~/.archon/`**: Archon's `search_cmd.py` sets `ARCHON_SEARCH_CONFIG=$HOME/.archon/archon-search.toml` (absolute path, not tilde) before spawning archon-search, so their config and data paths are unchanged. They are unaffected by the default change.
- **Standalone archon-search users (current)**: there are none yet (first public release), so the default path change has no migration cost.
- **CalVer commit count resets after `git filter-repo`**: the new repo starts from a lower commit count, which is correct — the version clock resets at extraction. The first tag will be something like `26.5.0` or `26.5.1`. This is expected and fine.
- **`archon_search.types` import in parent's `search_client.py`**: already guarded with `try/except ImportError`. Works identically whether archon-search is installed from a local path or PyPI.
- **`_PENDING_MIGRATION` exemptions**: no actual violations exist in `pipeline.py` or `mcp.py`. Removing them from the list closes the phantom exemption cleanly.
- **Active ingest jobs during transition**: The jobs file (`JOBS_FILE` in `jobs/model.py`) moves from `~/.archon/archon-search-jobs.json` to `~/.archon-search/archon-search-jobs.json`. Any in-flight jobs at upgrade time are silently lost. Archon users are unaffected (the env var override keeps their config at `~/.archon/`), but a standalone user upgrading mid-ingest should be aware.
- **hatch-vcs requires the new repo to have at least one tag**: the first release workflow run creates the initial tag; the package will not publish from an untagged state.
- **Custom CalVer scheme must produce clean versions on every commit**: no `.devN+gabcdef` suffixes. The custom `version_scheme` callable computes `YY.M.<total-commit-count>` (via `git rev-list --count HEAD`) rather than using hatch-vcs's default per-tag distance, so every commit on `main` is a publishable, clean version string with no dirty markers.

## Open Questions
None.

## Future Iterations
- Auto-migration script: detect `~/.archon/search` at startup and offer to migrate to `~/.archon-search/` with user confirmation.
- Separate release cadence documentation (e.g., a RELEASING.md explaining that archon-search releases are fully automated).

## Recommendation
This is the right move and the right time — the code boundary is already enforced by tests, the CLI coupling is subprocess-only, and the only real work is fixing the stubs shim and wiring the automated release. The hardest part is the `git filter-repo` rewrite (easy to get wrong by missing a legacy path), and the OIDC trusted publisher setup if you haven't done it before. Neither is risky. Phase the work as described and this goes smoothly.
