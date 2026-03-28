**Purpose**: Reference guide for the `archon` CLI management tool
**Audience**: Operators installing and managing the Archon daemon
**Status**: Stable
**Last reviewed**: 2026-03-28
**Next review**: 2027-03-28

---

# Archon CLI Reference

The `archon` CLI is a command-line management tool for the Archon daemon. It lets you start, stop, and inspect the service; view logs; run pre-flight checks; update to a new version; and read or modify configuration — all from a terminal without opening Telegram.

**When to use the CLI vs Telegram:**

- Use the **CLI** for daemon lifecycle (start/stop/restart), installation verification, log inspection, updates, and config edits. These operations happen before the bot is reachable or after it stops responding.
- Use the **Telegram bot** for session control (`/stop`, `/clear`), notification mode, skill and model selection, and anything that requires Claude to be actively running.

---

## Installation

After running the installer, the `archon` command is available system-wide. The installer symlinks it to `~/.local/bin/archon`. Verify the installation:

```
archon --help
```

If the command is not found, ensure `~/.local/bin` is on your `PATH`. Add it to your shell profile if needed:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

To install from source using `uv`:

```bash
uv sync
```

This makes `archon` available via `uv run archon` in the project directory. The installed symlink is created by the installer script, not by `uv sync`.

---

## Command reference

### Service management

#### `archon start`

Starts the Archon daemon.

```
archon start
```

On macOS, loads the launchd plist (`~/Library/LaunchAgents/com.archon.assistant.plist`). On Linux, starts the systemd user service (`archon`).

**Success:**
```
Archon started
```

**Failure:**
```
Failed to start Archon
```

If the service fails to start, check the logs: `archon logs -n 30`. On macOS, the plist must be installed first — if missing, run the installer: `uv run install.py`.

---

#### `archon stop`

Stops the running daemon.

```
archon stop
```

On macOS, unloads the launchd plist. If the plist file is missing, the service is stopped by label (`launchctl bootout`) instead — so `archon stop` works even if the plist has been deleted. On Linux, stops the systemd user service.

**Success:**
```
Archon stopped
```

**Failure:**
```
Failed to stop Archon
```

---

#### `archon restart`

Restarts the daemon. On macOS, runs `archon stop` then `archon start` (two launchd operations). On Linux, issues a single `systemctl --user restart` — atomic, with no service-down window between the two steps.

```
archon restart
```

**Success:**
```
Archon restarted
```

On macOS, both stop and start are always attempted. If either step fails, the command returns a non-zero exit code. Use `archon status` and `archon logs` to investigate.

---

### Status

#### `archon status`

Prints a status panel showing whether the daemon is running and the current configuration at a glance.

```
archon status
```

**Example output (running):**

```
● Archon v1.2.0  —  running
──────────────────────────────────────────
  Service    launchd · PID 8421 · uptime 02:14:37
  Health     localhost:18182 ✔ (12ms)
  MCP        localhost:18182 (archon-mcp)
  Plugins    3 loaded
  Model      claude-opus-4-6
  Notify     normal · beacon 2 min
  Voice      STT whisper/medium · TTS openai/nova (inbound)
  Log        ~/.archon/logs/archon.log
  Config     /Users/you/.archon/config.toml
```

**Example output (stopped):**

```
○ Archon v1.2.0  —  stopped
──────────────────────────────────────────
  Health     localhost:18182 ✗ (unreachable)
  MCP        localhost:18182 (archon-mcp)
  Plugins    3 loaded
  Model      claude-opus-4-6
  Notify     normal · beacon 2 min
  Voice      disabled
  Log        ~/.archon/logs/archon.log
  Config     /Users/you/.archon/config.toml
```

**Panel fields:**

| Field | Description |
|---|---|
| `● / ○ Archon vX.Y.Z` | Running state: filled circle = running, empty = stopped |
| `Service` | Platform service backend (`launchd` on macOS, `systemd` on Linux), process ID, and elapsed uptime. Shown only when running. |
| `Health` | HTTP reachability of the MCP/health endpoint and round-trip latency. `✔` = reachable, `✗` = not. |
| `MCP` | Address of the built-in `archon-mcp` server. Same port as the health endpoint. |
| `Plugins` | Count of plugin directories found in the configured plugins directory. |
| `Model` | Default model from `config.toml [models] default`. |
| `Notify` | Notification mode and beacon interval from `config.toml [notifications]`. |
| `Voice` | Voice settings (STT model, TTS provider/voice/mode) or `disabled` if `[voice] enabled = false`. |
| `Log` | Path to the current log file as read from `config.toml`. |
| `Config` | Full path to `~/.archon/config.toml`. Appends `(not found)` if missing. |

---

### Pre-flight checks

#### `archon doctor`

Runs ten pre-flight checks and reports each one (plus an eleventh RAG server check when `[rag] enabled = true`). Use this to diagnose why Archon is not starting or misbehaving.

```
archon doctor
```

**Example output (all passing):**

```
Archon Doctor — pre-flight checks
──────────────────────────────────────
  ✔  git                 git version 2.44.0
  ✔  uv                  uv 0.4.18 (...)
  ✔  python              Python 3.12.4
  ✔  claude              claude 1.0.17
  ✔  env file            /Users/you/.archon/.env
  ✔  bot token           @YourBotName
  ✔  config file         /Users/you/.archon/config.toml OK
  ✔  logs dir            /Users/you/.archon/logs writable
  ✔  health check        http://localhost:18182/health OK
  ✔  app dir             /Users/you/.archon/app exists

All checks passed.
```

**Example output (with failures):**

```
Archon Doctor — pre-flight checks
──────────────────────────────────────
  ✔  git                 git version 2.44.0
  ✔  uv                  uv 0.4.18 (...)
  ✔  python              Python 3.12.4
  ✗  claude              not found
  ✔  env file            /Users/you/.archon/.env
  ✗  bot token           invalid — check TELEGRAM_BOT_TOKEN in ~/.archon/.env
  ✗  config file         /Users/you/.archon/config.toml not found
  ✔  logs dir            /Users/you/.archon/logs writable
  ✗  health check        http://localhost:18182/health unreachable — is Archon running?
  ✔  app dir             /Users/you/.archon/app exists

4 issues found.
```

The command exits with code 1 if any check fails, 0 if all pass. This makes it suitable for scripting.

**Check list:**

| Check | What it verifies |
|---|---|
| `git` | `git` is installed and on PATH |
| `uv` | `uv` is installed and on PATH |
| `python` | Python 3.12 or newer is available via `uv run python` |
| `claude` | The `claude` CLI (Claude Code) is installed and on PATH |
| `env file` | `~/.archon/.env` exists and contains `TELEGRAM_BOT_TOKEN` |
| `bot token` | `TELEGRAM_BOT_TOKEN` is valid — verified by calling Telegram's `getMe` API; reports bot username on success |
| `config file` | `~/.archon/config.toml` exists and is valid TOML |
| `logs dir` | `~/.archon/logs/` exists and is writable |
| `health check` | The daemon's HTTP health endpoint at `localhost:<port>/health` responds 200 |
| `app dir` | `~/.archon/app/` exists (the installed application directory) |
| `rag server` *(conditional)* | When `[rag] enabled = true`: verifies RAG is installed, service is registered, and the server is reachable. Reports `disabled` when RAG is not enabled. |

**Remediation quick reference:**

- `claude not found` — install Claude Code: `npm install -g @anthropic-ai/claude-code`
- `env file ... TELEGRAM_BOT_TOKEN missing` — add your bot token to `~/.archon/.env`
- `bot token invalid` — verify the token in `~/.archon/.env` is correct (copy it from @BotFather)
- `config file not found` — run the installer or copy an example config to `~/.archon/config.toml`
- `health check unreachable` — the daemon is not running; start it with `archon start`
- `app dir not found` — re-run the installer
- `rag server` not running — start it with `archon rag start`

---

### Logs

#### `archon logs`

Shows the last 50 lines of the current log file.

```
archon logs
```

The log file path is read from `config.toml [logging] log_file`. If that field is absent or the config is unreadable, it falls back to `~/.archon/logs/archon.log`.

---

#### `archon logs -n N`

Shows the last N lines instead of the default 50.

```
archon logs -n 100
archon logs --lines 100
```

---

#### `archon logs -f`

Follows the log in real time (equivalent to `tail -f`). Press `Ctrl-C` to stop.

```
archon logs -f
archon logs --follow
```

Useful when testing a change or watching a long-running session.

---

#### `archon logs --date YYYY-MM-DD`

Shows the log for a specific date. Archon rotates logs daily; historical logs are named `archon.YYYY-MM-DD.log` in the same directory.

```
archon logs --date 2026-03-05
```

If the log file for that date does not exist, the command prints an error and exits with code 1:

```
Log file not found: /Users/you/.archon/logs/archon.2026-03-05.log
```

---

### Updates

#### `archon update`

Updates Archon to the latest published release. Resolves the latest tag from GitHub, checks whether the local version is already current (skipping if so), then delegates to the installer (`~/.archon/app/install.py --update --tag <version>`).

```
archon update
```

**Example output (update available):**
```
Resolving latest release...
Updating Archon to v1.3.0...
[installer output follows]
```

**Example output (already current):**
```
Resolving latest release...
Already up to date (v1.2.0).
```

If the installer is not found at `~/.archon/app/install.py`:
```
Installer not found: /Users/you/.archon/app/install.py
Re-run the installer to fix this.
```

---

#### `archon update --tag X.Y.Z`

Pins the update to a specific release tag instead of pulling the latest.

```
archon update --tag 1.3.0
```

**Example output:**
```
Updating Archon to v1.3.0...
[installer output follows]
```

Use this to roll back to a known-good version or to test a specific release.

---

#### `archon version`

Shows the installed version and checks GitHub for a newer release.

```
archon version
```

**Up to date:**
```
archon 1.2.0
Up to date.
```

**Update available:**
```
archon 1.2.0
Latest available: 1.3.0  (run: archon update)
```

If the GitHub API is unreachable (no internet, rate-limited), the version check is silently skipped and only the local version is printed.

---

### Uninstall

#### `archon uninstall`

Stops the service and removes the installed application. Delegates to the installer (`~/.archon/app/install.py --uninstall`).

```
archon uninstall
```

If the installer is not found at `~/.archon/app/install.py`:
```
Installer not found: /Users/you/.archon/app/install.py
Re-run the installer to fix this.
```

---

### Configuration

The `archon config` command group lets you read and modify `~/.archon/config.toml` without opening a text editor. All subcommands operate on the same file that the daemon reads.

#### `archon config` / `archon config show`

Prints the full contents of `config.toml` to stdout.

```
archon config
archon config show
```

**Example output:**
```
# /Users/you/.archon/config.toml
[access]
allowed_user_ids = [123456789]

[session]
working_directory = "~/projects"
inactivity_timeout_seconds = 3600

[notifications]
mode = "normal"
interval_minutes = 2

...
```

---

#### `archon config edit`

Opens `config.toml` in your `$EDITOR`. Falls back to `$VISUAL`, then to `vi` if neither is set.

```
archon config edit
```

The editor opens with the full file. Save and exit as normal. Changes take effect after restarting the daemon (`archon restart`).

---

#### `archon config get <key>`

Reads a single value from the config using a dotted key path.

```
archon config get notifications.mode
archon config get session.working_directory
archon config get background_agents.port
archon config get voice.tts.provider
```

**Example:**
```
$ archon config get notifications.mode
normal

$ archon config get background_agents.port
18182
```

If the key does not exist:
```
Key not found: notifications.mode
```

The key path mirrors the TOML section structure: `section.subsection.field`. For nested tables use additional dots, e.g. `voice.tts.provider`.

---

#### `archon config set <key> <value>`

Writes a value to the config. The file is updated in place using `tomlkit`, which preserves comments and formatting.

```
archon config set notifications.mode verbose
archon config set notifications.interval_minutes 5
archon config set voice.enabled true
archon config set session.inactivity_timeout_seconds 7200
```

**Example:**
```
$ archon config set notifications.mode verbose
Set notifications.mode = verbose

$ archon config set notifications.interval_minutes 5
Set notifications.interval_minutes = 5
```

**Type coercion:** values are automatically converted based on their content:

| Input | Stored as | Example |
|---|---|---|
| Starts with `[` and is valid JSON | array (homogeneous primitives) | `[1,2,3]` → `[1, 2, 3]` |
| All digits | integer | `5` → `5` |
| Valid float | float | `3.5` → `3.5` |
| `true` / `false` (case-insensitive) | boolean | `true` → `true` |
| Anything else | string | `verbose` → `"verbose"` |

> **Note:** Changes to `config.toml` do not take effect until the daemon is restarted. Run `archon restart` after setting values.

---

### RAG management

#### `archon rag install`

Installs the optional RAG (Retrieval-Augmented Generation) server: downloads ONNX models, registers the service with launchd (macOS) or systemd (Linux), and runs an initial ingest of conversation history.

```
archon rag install
archon rag install --dry-run           # print actions without executing
archon rag install --non-interactive   # skip confirmation prompt
```

---

#### `archon rag uninstall`

Stops and removes the RAG service. Data in `~/.archon/rag/` is preserved by default.

```
archon rag uninstall
archon rag uninstall --delete-db   # also delete the vector database in ~/.archon/rag/db
```

---

#### `archon rag start`

Starts the RAG MCP server.

```
archon rag start
```

> **Note:** On Windows, this prints a message directing you to run `python -m archon.rag.server` manually.

---

#### `archon rag stop`

Stops the RAG MCP server.

```
archon rag stop
```

---

#### `archon rag status`

Shows the current service state, configured port, and collection statistics.

```
archon rag status
```

---

#### `archon rag ingest [path] [--collection name]`

Ingests files into a named collection. If no path is given, re-ingests the conversation history collection. If `--collection` is omitted, the collection name defaults to the directory basename.

```
archon rag ingest                              # re-ingest history collection
archon rag ingest /path/to/docs               # ingest directory, name = dir basename
archon rag ingest /path/to/docs --collection my-docs
```

---

#### `archon rag sync`

Manually reconciles all collections declared in `[rag] collections` with LanceDB. Adds missing collections and removes ones that were dropped from the config. Does not re-ingest files within already-indexed collections — use `archon rag ingest` or `archon rag collection reindex` for that.

```
archon rag sync
```

Useful after editing `config.toml` to add or remove collection paths, without restarting the RAG service.

---

#### `archon rag collection`

Subcommand group for imperative collection management. Run without arguments to print help.

```
archon rag collection list
archon rag collection add <path>
archon rag collection remove <path> [--force] [--dry-run]
archon rag collection info <name>
archon rag collection reindex <name>
```

| Subcommand | Description |
|---|---|
| `list` | List all LanceDB collections with path, doc/chunk counts, and status (`indexed`, `orphan (managed)`, `unmanaged`) |
| `add <path>` | Register path in `[rag] collections`, immediately ingest all supported files, and update config |
| `remove <path>` | Drop the LanceDB collection and remove the path from config. Service must be stopped first; use `--force` to skip that check. Use `--dry-run` to preview without changes. |
| `info <name>` | Show metadata for one collection: doc/chunk count, embedding model, centroid status, last indexed timestamp |
| `reindex <name>` | Force full re-ingest of a collection (service must be stopped). Use to fix model-mismatch warnings or regenerate missing centroids. |

> **See also:** The [RAG Search Guide](rag_guide.md#cli-collection-management) has detailed examples for each subcommand.

---

## Common workflows

### Restart after changing the config

```bash
archon config set notifications.mode quiet
archon config set notifications.interval_minutes 10
archon restart
```

Or edit the file directly and restart in one step:

```bash
archon config edit
# (save and exit the editor)
archon restart
```

---

### Check why Archon is not responding

Start with doctor to rule out environment issues:

```bash
archon doctor
```

If all checks pass but the bot is still silent, check service state and recent logs:

```bash
archon status
archon logs -n 50
```

Look for error lines near the bottom of the log. If the service is stopped, start it:

```bash
archon start
archon logs -f
```

Watch the follow output for startup errors (config parse errors, missing bot token, network failures).

---

### Update to the latest version

Check whether an update is available, then apply it:

```bash
archon version
archon update
archon restart
```

To pin to a specific release:

```bash
archon update --tag 1.3.0
archon restart
```

---

### Tail the logs while testing

Open a second terminal window and follow the log before sending test messages in Telegram:

```bash
archon logs -f
```

Switch to debug notification mode to see everything Claude is doing:

```bash
archon config set notifications.mode debug
archon restart
```

Send a message in Telegram and watch the full event stream appear in the log. Reset when done:

```bash
archon config set notifications.mode normal
archon restart
```

---

## Troubleshooting

| Symptom | Command | What to look for |
|---|---|---|
| Bot not responding in Telegram | `archon status` | Service shows `stopped` |
| Bot not responding in Telegram | `archon doctor` | Failed checks (especially `claude` and `health check`) |
| Service fails to start | `archon logs -n 30` | ConfigError, missing token, permission error |
| Unexpected behaviour after config change | `archon config show` | Verify the value is what you intended |
| Log file missing | `archon doctor` | `logs dir` check — directory may not exist |
| Cannot find `archon` command | — | Add `~/.local/bin` to `PATH`; re-run the installer |
| Health check always fails | `archon status` | Daemon may be stopped; `archon start` |
| Want to roll back a version | `archon update --tag X.Y.Z` then `archon restart` | Check `archon version` to confirm |

---

> **See also:** [User Manual](user_manual.md) — Telegram bot commands and notification modes. [Quick Start](../quick_start.md) — first-time setup guide. [Operational Readiness](../Architecture/160_operational_readiness_monitoring_and_reliability.md) — logging, health checks, and daemon reliability.
