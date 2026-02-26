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

P2 — High

## Estimated Effort

Medium (3–5 days)

## User Story

**As a** new user,
**I want** to install Archon with a single command that requires no repository cloning,
**so that** setup is frictionless and the installer is maintainable as a regular Python module.

## Background

The current `install.sh` bash script works but has structural weaknesses: fragile error handling (`set -euo pipefail`), awkward testability (subprocess stubs + fake HOME), and poor cross-platform semantics. Because `uv` is already a hard prerequisite, `uv run <remote-url>` (uv 0.4+) lets us replace the bash script with a proper Python module — testable with standard pytest, robust error handling via `try/except`, and `rich` for styled output.

**Entry point (one command, no pre-clone needed):**
```bash
uv run https://raw.githubusercontent.com/user538295/archon-assistant/main/install.py
```

## Acceptance Criteria

### Script metadata (PEP 723)

- `install.py` declares inline script metadata with `requires-python = ">=3.12"` and `dependencies = ["rich"]`
- Running `uv run install.py` (locally) or `uv run <url>` (remotely) works without a pre-existing virtualenv

### Functional parity with `install.sh`

- Checks prerequisites: `git`, `uv` (version), Python 3.12+, `claude` CLI — exits with a clear error message if any are missing
- Fresh install: clones repo to `~/.archon/app` via `subprocess` + `git clone --depth 1`
- Update install: detects `~/.archon/app/.git` and runs `git fetch` + `git reset --hard origin/main`
- Detects existing service (macOS plist / Linux unit file) and prompts for reinstall confirmation
- Writes `~/.archon/.env` with `TELEGRAM_BOT_TOKEN`
- Writes `~/.archon/config.toml` on first install; patches `allowed_user_ids` + `working_directory` on update
- Creates `~/.archon/workspace/`, `~/.archon/cron.d/`, `~/.archon/scripts/`
- Runs `uv sync` in `~/.archon/app`
- Registers and starts the service: launchd plist on macOS, systemd user unit on Linux
- Prompts for optional QMD installation

### CLI flags

- `--dry-run` — prints every action without executing it; exits 0
- `--uninstall` — stops and removes the service + optionally removes `~/.archon/app`
- `--update` — skips config prompts, only pulls latest code + restarts service
- `--non-interactive` — reads `ARCHON_BOT_TOKEN` and `ARCHON_USER_IDS` from environment (CI/scripting use)

### Code quality

- Organised into pure functions: `check_prerequisites()`, `fetch_or_update_app()`, `write_config()`, `register_service()`, `verify_running()` — each independently testable
- No global state; functions accept explicit paths and return result objects
- `rich.Console` used for all output; `Console(quiet=True)` injectable for tests

### Tests (`tests/test_installer_py.py`)

- Unit tests mock `subprocess.run` and filesystem; no fake HOME needed
- `test_check_prerequisites_raises_on_missing_git` — `FileNotFoundError` on missing `git`
- `test_fresh_install_calls_git_clone` — clone called with correct URL and target
- `test_update_install_calls_git_fetch_reset` — fetch+reset called when `.git` exists
- `test_write_config_creates_expected_files` — `.env` and `config.toml` written with correct content
- `test_service_placeholders_substituted` — plist/unit file has no `__PLACEHOLDER__` tokens
- `test_dry_run_makes_no_filesystem_changes` — `--dry-run` produces no side effects
- `test_non_interactive_reads_env_vars` — `ARCHON_BOT_TOKEN` + `ARCHON_USER_IDS` consumed correctly
- `test_missing_uv_exits_with_message` — clear error, non-zero exit
- `test_update_flag_skips_config_prompts` — `--update` does not call `input()`

## Technical Notes

- `uv run <url>` downloads and executes the script in a temporary environment with the declared dependencies installed. No `git clone` or `pip install` is needed by the user.
- The Python installer replaces `install.sh` entirely; `install.sh` should be removed once S16.1 is complete.
- Cross-platform: macOS uses launchd plist (as today), Linux uses systemd user unit.
- The `rich` dependency provides styled terminal output with progress bars and coloured messages.

## Related Documents

- [`Documentation/Architecture/510_release_and_environment_strategy.md`](../Architecture/510_release_and_environment_strategy.md) — deployment model
- [`Documentation/Architecture/530_technical_debt_refactoring_roadmap.md`](../Architecture/530_technical_debt_refactoring_roadmap.md) — lists `install.sh` replacement as known debt
- [`Documentation/tasks.md`](../../Documentation/tasks.md) — S16.1 task entry
