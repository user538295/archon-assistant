# S16.1 — Python Installer via `uv run`

**Purpose**: Backlog item for replacing the bash installer with a maintainable Python module
**Audience**: All developers
**Status**: Pending
**Last reviewed**: 2026-02-26
**Next review**: 2026-05-26

---

## Status

Pending

## Priority

P3 — Medium (downgraded: all user stories complete; rewrite of a working installer)

## Estimated Effort

Large (5–8 days: macOS-only phase 1 + Linux as separate follow-on story)

## User Story

**As a** new user,
**I want** to install Archon with a single command that requires no repository cloning,
**so that** setup is frictionless and the installer is maintainable as a regular Python module.

## Background

The current `install.sh` is functional but harder to unit-test than a Python module (mocking bash subprocesses requires process-level faking). Because `uv` is already a hard prerequisite, `uv run <remote-url>` (uv 0.4+) lets us replace it with a proper Python module — testable with standard pytest and explicit `try/except` error paths.

**Entry point (one command, no pre-clone needed):**
```bash
uv run https://raw.githubusercontent.com/user538295/archon-assistant/v<TAG>/install.py
```

> **Security note**: the URL must always pin to a release tag or commit SHA, never `main`. Pulling from `main` is a supply chain risk — any push to the branch immediately affects all installs with no integrity check.

**Phase 1 (this story): macOS only.** Linux systemd support is a separate follow-on story.

## Acceptance Criteria

### Script metadata (PEP 723)

- `install.py` declares inline script metadata with `requires-python = ">=3.12"` and no external dependencies (stdlib only — no `rich`)
- Running `uv run install.py` (locally) or `uv run <url>` (remotely) works without a pre-existing virtualenv
- Styled output uses stdlib ANSI codes (`\033[...]`) — no PyPI fetch needed during bootstrap

### Functional parity with `install.sh` (macOS only)

- Checks prerequisites: `git`, `uv` (version ≥ 0.4), Python 3.12+, `claude` CLI — exits with a clear error message if any are missing
- Fresh install: clones repo to `~/.archon/app` via `subprocess` + `git clone --depth 1 --branch <tag>`; clones a pinned release tag, never a branch tip
- Update install: detects `~/.archon/app/.git` and runs `git fetch --tags` + `git checkout <tag>` (not `reset --hard origin/main` — preserves no local changes but is explicit and auditable)
- On partial failure at any step: prints the failed step, leaves `~/.archon/app.partial` instead of `~/.archon/app`, and exits non-zero so the user can retry cleanly
- Detects existing macOS launchd plist and prompts for reinstall confirmation
- Writes `~/.archon/.env` with `TELEGRAM_BOT_TOKEN`; token value is stripped and shell-quoted to handle special characters
- Writes `~/.archon/config.toml` on first install; on update, merges only `allowed_user_ids` and `working_directory` using `tomllib` + `tomli_w` — all other user-set keys are preserved
- Creates `~/.archon/workspace/`, `~/.archon/schedules/`, `~/.archon/scripts/`
- Runs `uv sync` in `~/.archon/app`; on failure rolls back by removing the partial clone
- Registers and starts the launchd plist (macOS); Linux systemd is out of scope for this story
- Prompts for optional QMD installation

### CLI flags

- `--dry-run` — prints every action without executing it; exits 0
- `--uninstall` — stops and removes the service + optionally removes `~/.archon/app`
- `--update` — skips config prompts, only pulls latest code + restarts service
- `--non-interactive` — reads `ARCHON_BOT_TOKEN` (stripped, shell-quoted) and `ARCHON_USER_IDS` (comma-separated integers) from environment; exits with a clear error if either is absent or malformed

### Code quality

- Organised into pure functions: `check_prerequisites()`, `fetch_or_update_app()`, `write_config()`, `register_service()`, `verify_running()` — each independently testable
- No global state; functions accept explicit paths and return result objects
- A simple `Console` wrapper (stdlib only) used for all output; injectable `quiet=True` mode for tests — no `rich` dependency

### Tests (`tests/test_installer_py.py`)

- Unit tests mock `subprocess.run` and filesystem; no fake HOME needed
- `test_check_prerequisites_raises_on_missing_git` — `FileNotFoundError` on missing `git`
- `test_fresh_install_calls_git_clone` — clone called with correct pinned tag URL and target
- `test_update_install_calls_git_fetch_and_checkout` — fetch+checkout called when `.git` exists (not `reset --hard`)
- `test_partial_failure_leaves_partial_dir_not_app` — failed clone leaves `~/.archon/app.partial`, not `~/.archon/app`
- `test_write_config_creates_expected_files` — `.env` and `config.toml` written with correct content
- `test_update_preserves_user_config_keys` — keys other than `allowed_user_ids` and `working_directory` survive an update
- `test_token_with_special_chars_is_shell_quoted` — bot token containing `$`, `!`, `@` is written safely to `.env`
- `test_service_placeholders_substituted` — plist file has no `__PLACEHOLDER__` tokens
- `test_dry_run_makes_no_filesystem_changes` — `--dry-run` produces no side effects
- `test_non_interactive_reads_env_vars` — `ARCHON_BOT_TOKEN` + `ARCHON_USER_IDS` consumed correctly
- `test_non_interactive_exits_on_malformed_user_ids` — non-integer value in `ARCHON_USER_IDS` exits non-zero
- `test_missing_uv_exits_with_message` — clear error, non-zero exit
- `test_update_flag_skips_config_prompts` — `--update` does not call `input()`

## Technical Notes

- `uv run <url>` downloads and executes the script in a temporary environment. No `git clone` or `pip install` is needed by the user.
- The URL must pin to a release tag or commit SHA — never `main`. Recommended form: `uv run https://raw.githubusercontent.com/user538295/archon-assistant/v1.2.3/install.py`
- No external PyPI dependencies in `install.py`. Styled output via stdlib ANSI codes keeps the bootstrap self-contained and immune to PyPI outages.
- `config.toml` merging on update uses `tomllib` (stdlib, Python 3.11+) to parse and `tomli_w` to serialise — explicit, lossless, preserves comments where possible. If `tomli_w` is unavailable, fall back to string-based patching of only the two target fields with a logged warning.
- The Python installer replaces `install.sh` entirely; `install.sh` should be removed once S16.1 is complete.
- **macOS only** — launchd plist as today. Linux systemd support is a separate follow-on story and is out of scope here.

## Related Documents

- [`Documentation/Architecture/510_release_and_environment_strategy.md`](../Architecture/510_release_and_environment_strategy.md) — deployment model
- [`Documentation/Architecture/530_technical_debt_refactoring_roadmap.md`](../Architecture/530_technical_debt_refactoring_roadmap.md) — lists `install.sh` replacement as known debt
- [`Documentation/tasks.md`](../../Documentation/tasks.md) — S16.1 task entry
