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

### Phase 1 — Decouple within the monorepo (committed, tested, merged first)
1. Copy `tests/_search_stubs.py` from the monorepo root into `packages/archon-search/tests/` as a self-contained file.
2. Delete `packages/archon-search/tests/_search_stubs_shim.py`.
3. Remove `pipeline.py` and `mcp.py` from `_PENDING_MIGRATION` in `test_no_archon_imports.py` (no actual violations exist — the list is phantom).
4. Change the three hardcoded `~/.archon/` default paths in `config.py` to `~/.archon-search/` (`db_path`, `log_file`, `log_dir`).
5. Change the default config file path in `get_default_config_path()` from `~/.archon/archon-search.toml` to `~/.archon-search/archon-search.toml`.
6. Add `ARCHON_SEARCH_CONFIG` env var support: if set, it overrides the config file path entirely.
7. Update `archon/cli/search_cmd.py` to pass `ARCHON_SEARCH_CONFIG=~/.archon/archon-search.toml` when spawning the subprocess, so existing Archon installations keep reading their current config without migration.
8. Update `archon-search.toml.example` and README to document the new default path and env var.
9. Switch `pyproject.toml` from `version = "26.4.0"` (static) to `dynamic = ["version"]` with `hatch-vcs`.
10. Add a `[tool.hatch.version]` section using a CalVer scheme (`YY.M.<distance>`).
11. Run all tests in both `tests/` and `packages/archon-search/tests/` — all must pass.

### Phase 2 — Extract the repo and publish
1. Clone the monorepo locally; run `git filter-repo` with all five historical paths:
   ```
   --path archon/rag --path archon/search --path packages/archon-search
   --path tests/rag --path tests/search
   ```
2. Rewrite paths: strip the `packages/archon-search/` prefix so the package sits at the repo root.
3. Push to a new GitHub repository (`archon-search`).
4. Move `.github/workflows/archon-search-pr.yml` and `archon-search-release.yml` to the new repo; remove the `paths:` filter (not needed in a dedicated repo).
5. Add a release workflow: on push to `main`, compute CalVer tag (`YY.M.<commit-count>`), create and push the tag, publish to PyPI via trusted publisher (OIDC — no stored secret).
6. Configure PyPI trusted publisher for the new repo.
7. Trigger first release; verify the package appears on PyPI.

### Phase 3 — Update parent Archon
1. In the root `pyproject.toml`, replace `archon-search = { path = "packages/archon-search" }` with `archon-search = ">=<first-published-version>"`.
2. Remove `packages/archon-search/` directory from the monorepo.
3. Remove the archon-search entry from the `[tool.uv.sources]` workspace block.
4. Verify parent tests pass (`uv run pytest`).
5. Commit and push.

## In Scope
- Standalone `_search_stubs.py` in archon-search tests (shim deleted)
- `~/.archon-search/` as the new standalone default for all data paths
- `ARCHON_SEARCH_CONFIG` env var for config file path override
- Parent Archon passes explicit `ARCHON_SEARCH_CONFIG` when spawning archon-search (backwards compat)
- Dynamic CalVer via `hatch-vcs` (fully automatic, no human version bumps)
- Automated PyPI publish via OIDC trusted publisher on every push to `main`
- Full git history preserved across all three legacy paths (`archon/rag`, `archon/search`, `packages/archon-search`) plus matching test directories
- Parent updated to consume archon-search from PyPI

## Out of Scope
- Changing the HTTP API contract between Archon and archon-search (no changes to `SearchClient`)
- Migrating existing user data from `~/.archon/search` to `~/.archon-search/` (user opt-in; documented)
- MCP or CLI interface changes
- Renaming the PyPI package slug (stays `archon-search`)

## Key Decisions
- **Dynamic CalVer via `hatch-vcs` with custom `YY.M.<commit-count>` scheme**: eliminates all manual version management; every commit to `main` produces a clean, monotonically increasing version in the same format Archon uses. Requires a small custom version scheme in `pyproject.toml` (a `version_scheme` callable that formats `YY.M.<distance>` from the tag base).
- **Default path `~/.archon-search/`**: correct default for a standalone package; Archon overrides it explicitly via env var so existing users are unaffected without any data migration.
- **`ARCHON_SEARCH_CONFIG` env var** (not a CLI flag): avoids touching every subprocess call site; one env var in `search_cmd.py` covers all subcommands.
- **Phased extraction**: Phase 1 decoupling is committed and CI-green before any files move, giving a clean and verifiable extraction point.
- **PyPI OIDC trusted publisher**: no secrets stored in GitHub; publish permission is scoped to the workflow file path.
- **New repo under same GitHub owner as Archon**: zero setup overhead now; PyPI trusted publisher can be re-configured in 2 minutes if the repo is transferred to an org later. No impact on `pip install archon-search` either way.

## Edge Cases & Constraints
- **Existing Archon users with data at `~/.archon/`**: Archon's `search_cmd.py` sets `ARCHON_SEARCH_CONFIG=~/.archon/archon-search.toml` before spawning archon-search, so their config and data paths are unchanged. They are unaffected by the default change.
- **Standalone archon-search users (current)**: there are none yet (first public release), so the default path change has no migration cost.
- **CalVer commit count resets after `git filter-repo`**: the new repo starts from a lower commit count, which is correct — the version clock resets at extraction. The first tag will be something like `26.5.0` or `26.5.1`. This is expected and fine.
- **`archon_search.types` import in parent's `search_client.py`**: already guarded with `try/except ImportError`. Works identically whether archon-search is installed from a local path or PyPI.
- **`_PENDING_MIGRATION` exemptions**: no actual violations exist in `pipeline.py` or `mcp.py`. Removing them from the list closes the phantom exemption cleanly.
- **hatch-vcs requires the new repo to have at least one tag**: the first release workflow run creates the initial tag; the package will not publish from an untagged state.
- **Custom CalVer scheme must produce clean versions on every commit**: no `.devN+gabcdef` suffixes. The scheme computes `YY.M.<total-commit-count>` so every commit on `main` is a publishable, clean version string with no dirty markers.

## Open Questions
None.

## Future Iterations
- Auto-migration script: detect `~/.archon/search` at startup and offer to migrate to `~/.archon-search/` with user confirmation.
- Separate release cadence documentation (e.g., a RELEASING.md explaining that archon-search releases are fully automated).

## Recommendation
This is the right move and the right time — the code boundary is already enforced by tests, the CLI coupling is subprocess-only, and the only real work is fixing the stubs shim and wiring the automated release. The hardest part is the `git filter-repo` rewrite (easy to get wrong by missing a legacy path), and the OIDC trusted publisher setup if you haven't done it before. Neither is risky. Phase the work as described and this goes smoothly.
