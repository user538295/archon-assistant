---
Purpose: Step-by-step migration guide for existing Archon users upgrading to the standalone archon-search package
Audience: Existing Archon users who set up Search before it became a standalone service
Status: Active
Last reviewed: 2026-05-03
Next review: 2026-08-03
---

# Search Migration Guide

## Overview

In a recent Archon release, the Search server was extracted into a standalone package (`archon-search`) with its own binary, config file, and service registration. This guide walks you through migrating from the older integrated setup to the new standalone setup.

**What changes:**

- Search server config moves from `~/.archon/config.toml [search]` to `~/.archon/archon-search.toml`
- The `archon-search` binary manages the service lifecycle (install, start, stop)
- Archon's `[search]` section retains only client-side connection settings
- The LanceDB database (`~/.archon/search/`) is unchanged — no data migration needed

**What stays the same:**

- All search functionality in Telegram and via Claude tools
- The `archon doctor` and `archon search ...` CLI commands
- Your indexed collections and their data on disk

> **Note on `archon search install`:** After migration, `archon search install` delegates to `archon-search install`, which creates directories and registers the service. It does **not** download models or run initial ingest — model loading happens lazily on first use.

---

## Prerequisites

Before starting the migration steps:

1. **Update Archon** to the version that includes this separation — the version where `archon/search/` was removed from the main package. Run `archon update` or `uv sync` in the project root to pull the latest release.
2. **Install `archon-search`** and confirm it is on your `PATH`. Installing via the workspace (`uv sync` in the repo root) includes it automatically. If you installed Archon from a release tarball, install the package separately:
   ```bash
   uv tool install archon-search
   ```
   Verify the binary is available:
   ```bash
   archon-search --help
   ```
3. **Back up your config** before making any changes:
   ```bash
   cp ~/.archon/config.toml ~/.archon/config.toml.bak
   ```

---

## Before you start

Identify which server-side config keys you currently have in `~/.archon/config.toml` under `[search]`. Any key that is not one of the four client-side fields below will need to move:

**Client-side fields (stay in `config.toml`):**

| Key | Description |
|---|---|
| `enabled` | Connect to the Search server on Archon startup |
| `url` | Search server URL (default `"http://127.0.0.1:8765"`) |
| `max_parallel_collections` | Max concurrent search operations per query |
| `top_k_return` | Final results returned to Claude after reranking |

**Server-side fields (move to `archon-search.toml`):**

Any other key you have under `[search]` — such as `db_path`, `embedding_model`, `reranker_model`, `chunk_size`, `providers`, `collections`, `pinned_collections`, `routing_confidence_threshold`, `routing_shortlist_size`, `host`, `port`, `sync_timeout_seconds`, `watch` — belongs in `archon-search.toml` now.

> **Deprecation warnings:** If Archon starts and finds unrecognised keys in `config.toml [search]`, it logs a warning for each one:
> ```
> [search] key 'embedding_model' is no longer read by Archon — move it to archon-search.toml
> ```
> These warnings appear in `~/.archon/logs/archon.log`. The keys are silently ignored — your Search server continues to run with whatever settings it had before. The warnings persist on every restart until you complete this migration.

---

## Step 1: Create `~/.archon/archon-search.toml`

Create the file manually using the template below. Fill in only the fields you previously customised — everything else can be omitted (the server uses built-in defaults).

```toml
# ~/.archon/archon-search.toml

[server]
host = "127.0.0.1"
port = 8765

[database]
db_path = "~/.archon/search"
embedding_model = "BAAI/bge-small-en-v1.5"
reranker_model = "cross-encoder/ms-marco-MiniLM-L-6-v2"  # see note below
chunk_size = 512
auto_reindex_on_chunk_size_change = true
# providers = []   # Uncomment for GPU: ["CUDAExecutionProvider"] or ["CoreMLExecutionProvider"]

[routing]
routing_shortlist_size = 8
routing_confidence_threshold = 0.30
max_parallel_collections = 3

[collections]
pinned_collections = []
collections = []
watch = false

[logging]
level = "INFO"
log_file = "~/.archon/logs/archon-search.log"
```

> **`reranker_model` note:** The built-in default is `"cross-encoder/ms-marco-MiniLM-L-6-v2"` (as shown in the template). The [Search Guide](search_guide.md) documents an alternative value (`"BAAI/bge-reranker-v2-m3"`). If you previously set a custom `reranker_model` in `config.toml`, carry that exact value across to `archon-search.toml` — do not change it, as switching rerankers requires a full re-index.

**Minimal file (use all defaults):**

If you never customised any server-side settings, you can create an empty file:

```bash
touch ~/.archon/archon-search.toml
```

The server reads all defaults from its built-in config when a key is absent.

---

## Step 2: Move server-side keys from `config.toml` to `archon-search.toml`

Open `~/.archon/config.toml` and locate the `[search]` section. For every key that is **not** `enabled`, `url`, `max_parallel_collections`, or `top_k_return`:

1. Copy the value to the corresponding section in `archon-search.toml` (see mapping table below).
2. Delete the key from `config.toml`.

**Key mapping:**

| Old key in `config.toml [search]` | New location in `archon-search.toml` |
|---|---|
| `host` | `[server] host` |
| `port` | `[server] port` |
| `db_path` | `[database] db_path` |
| `embedding_model` | `[database] embedding_model` |
| `reranker_model` | `[database] reranker_model` |
| `chunk_size` | `[database] chunk_size` |
| `providers` | `[database] providers` |
| `collections` | `[collections] collections` |
| `pinned_collections` | `[collections] pinned_collections` |
| `routing_confidence_threshold` | `[routing] routing_confidence_threshold` |
| `routing_shortlist_size` | `[routing] routing_shortlist_size` |
| `watch` | `[collections] watch` |
| `sync_timeout_seconds` | No equivalent — omit (server starts immediately) |

> **Note:** `top_k_retrieve` was not a standard `config.toml` field — it was internal to the server. Most users will not have it. Only the keys listed above need to be migrated.

---

## Step 3: Verify Archon's `[search]` section

After removing server-side keys, your `[search]` block in `config.toml` should look like this (containing only client-side fields):

```toml
[search]
enabled = true
url = "http://127.0.0.1:8765"
max_parallel_collections = 3
top_k_return = 5
```

If you use the default URL (`http://127.0.0.1:8765`) you can omit it. The minimal working config is:

```toml
[search]
enabled = true
```

---

## Step 4: Run `archon-search install`

**Before running the installer, stop the existing Archon-managed search service** to avoid a port conflict when the new standalone service starts:

```bash
archon search stop
```

`archon-search install` will attempt to remove the legacy service definition automatically, but stopping it first ensures a clean handover with no port 8765 conflict during the transition.

This rewrites the service definition to point to the standalone `archon-search` entrypoint. If a legacy Archon-managed service file exists, it is automatically unloaded and removed.

```bash
archon-search install
```

The installer will:

1. Create `~/.archon/archon-search.toml` with defaults if the file does not exist yet (but since you created it in Step 1, it will be used as-is).
2. Create the database and log directories if they do not exist.
3. Detect and remove any legacy service definition:
   - **macOS:** `~/Library/LaunchAgents/com.archon.search.plist`
   - **Linux:** `~/.config/systemd/user/archon-search.service`
4. Register and start the new standalone service.
5. Wait up to 60 seconds for the health endpoint to respond.

If you prefer to review what would happen without making changes:

```bash
archon-search install --dry-run
```

For scripted or unattended installs (skip the confirmation prompt):

```bash
archon-search install --non-interactive
```

> **Linux note:** systemd user services require loginctl lingering to survive session logout. If you want `archon-search` to stay running after you log out, run:
> ```bash
> loginctl enable-linger $USER
> ```

### If something goes wrong after `archon-search install`

If the service fails to start or behaves unexpectedly, check the logs first:

- **Linux:** `journalctl --user -u archon-search`
- **macOS:** `log show --predicate 'subsystem == "com.archon.search"' --last 5m`
- **Either platform:** `tail -n 50 ~/.archon/logs/archon-search.log`

To debug interactively, re-run the installer — it is safe to run multiple times:

```bash
archon-search install
```

Your LanceDB data at `~/.archon/search/` (or your configured `db_path`) is never touched by the installer. No data is at risk.

---

## Step 5: Verify the standalone service boots

```bash
archon-search start
```

If `archon-search install` already started the service in Step 4, this is a no-op. Check the status:

```bash
archon-search status
```

Expected output when running:

```
running (PID 12345, uptime 60s)
```

When stopped it prints `stopped`. The PID and uptime are shown only when available from the platform service layer.

Verify the HTTP health endpoint responds:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/health
# Expected: 200
```

The health endpoint returns a JSON body like `{"status": "running", "version": "..."}`. If the port differs from the default, substitute your configured port.

---

## Step 6: Run `archon doctor`

```bash
archon doctor
```

With `[search] enabled = true` in `config.toml` and the server running, `archon doctor` performs live Search health checks:

- Verifies the server is reachable via HTTP
- Reports each collection's status (done, partial, pending, failed)
- Flags staleness warnings (no ingest in the last 7 days)
- Flags model-mismatch warnings (embedding model changed since last ingest)
- Flags empty collections

A clean migration produces no warnings. If you see collection warnings, the data is intact — the warnings indicate maintenance tasks like reindexing. Refer to the [Search Guide](search_guide.md#health-checks-archon-doctor) for resolution steps.

---

## Smoke-test checklist

Run through these checks to confirm the migration is complete:

- [ ] **Start Search:** `archon-search start` exits cleanly (or `archon-search status` shows `running`)
- [ ] **Health endpoint:** `curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/health` prints `200`
- [ ] **Archon doctor:** `archon doctor` shows no deprecation warnings about `[search]` keys and Search health is reported via HTTP (not skipped)
- [ ] **No log warnings:** `archon logs | grep "no longer read"` returns nothing after restarting Archon
- [ ] **Disabled mode:** Set `enabled = false` in `config.toml [search]`, restart Archon — `archon doctor` should show `  ✔  search server         disabled` and skip all live Search checks. The search MCP tools will not be registered in Claude's available tools. Restore `enabled = true` when done.

---

## Data directory note

The LanceDB tables at `~/.archon/search/` (or your configured `db_path`) are unchanged by this migration. No re-indexing is required. All your existing collections, chunks, and embeddings remain intact.

---

## Troubleshooting

### `archon-search` command not found

The `archon-search` binary is provided by the `archon-search` package. If it is not on your `PATH`, install it:

```bash
uv tool install archon-search
```

Or run it directly from the package directory:

```bash
uv run archon-search install
```

### Deprecation warnings still appearing after migration

Restart Archon after editing `config.toml` — the config is loaded at startup:

```bash
archon restart
```

Then check `archon logs` to confirm the warnings are gone.

### Service fails to start after `archon-search install`

Check the archon-search log for errors:

```bash
tail -n 50 ~/.archon/logs/archon-search.log
```

Common causes:

- **Port conflict:** Another process is using port 8765. Change `[server] port` in `archon-search.toml` and update `[search] url` in `config.toml` to match.
- **Config parse error:** `archon-search.toml` contains invalid TOML. Run `archon-search config show` to validate.
- **Missing db_path directory:** The database directory could not be created. Check permissions on `~/.archon/`.

### `archon doctor` shows "Search server not reachable"

The server may not have started yet. Run:

```bash
archon-search status
archon-search start   # if stopped
```

Then re-run `archon doctor`.

---

## See also

- [Search Guide](search_guide.md) — full Search feature documentation
- [CLI Reference](cli_reference.md) — complete `archon` command reference
- [Search Architecture](../Architecture/180_search_architecture.md) — internal design and component breakdown
