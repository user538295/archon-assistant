**Purpose**: End-user guide for Telegram bot commands and features
**Audience**: End users
**Status**: Stable
**Last reviewed**: 2026-03-28
**Next review**: 2027-03-28

---

# Archon User Manual

Archon is your personal Claude Code bridge over Telegram. Send any message and Claude responds — streaming every thought, tool call, and answer back to you in real time.

---

## Getting started

Open the Archon bot in Telegram and send any text message. Claude will start working immediately. You don't need to run `/start` first — the session is created automatically on your first message.

> **Command menu:** Type `/` or tap the 📋 button to the left of the message input to browse all available commands interactively.

---

## Sending messages

Any text that isn't a command is forwarded to Claude. You can:

- Ask questions: `What is the current git status?`
- Give instructions: `Refactor the auth module to use JWT`
- Continue a conversation naturally — Claude remembers everything in the current session

**If Claude is still processing your previous message**, Archon replies immediately with:

```
⏳ Previous request still processing — your message is queued
```

Your new message is queued and will be processed as soon as the current request finishes.

---

## Sending files

You can send files directly to Archon in Telegram. Each file is saved to the workspace and Claude receives a structured prompt describing it.

**Supported file types:**

| Type | Examples | What Claude sees |
|---|---|---|
| Documents | `.pdf`, `.txt`, `.json`, `.csv`, `.py`, `.md` | File path, size, MIME type; text files include content preview |
| Images | `.jpg`, `.png`, `.gif`, `.webp` | File path, dimensions, format — no visual analysis (metadata only) |
| Video | `.mp4`, `.mov` | File path, size, duration |
| Stickers | Telegram stickers | Saved as `.webp`; emoji shown in prompt |
| Audio | `.mp3`, `.ogg`, `.wav` | File path, size, duration |
| Archives | `.zip`, `.tar.gz` | File path, size |

**Notes:**

- **Images**: Claude receives image metadata (dimensions, format, file size) but does **not** perform visual analysis. It can read the file from disk if needed.
- **PDFs**: Text extraction depends on CLI tools available in the workspace. Claude can use tools to read PDF content after receiving the file.
- **Media groups**: Send multiple files as a Telegram album — Archon batches them into a single prompt automatically (1-second collection window).
- **Captions**: Any caption you add to a file is included in the prompt alongside the file metadata.

**Configuration:**

| Key | Default | Description |
|---|---|---|
| `[session] attachments_dir` | `{working_directory}/attachments` | Where files are saved |
| `[session] attachments_cleanup_hours` | disabled | Auto-delete files older than N hours (by mtime) |

---

## Commands

### `/start`
Greets you and confirms the bot is running.

---

### `/status`
Shows the current session state.

**When a session is active:**
```
✅ Session active
Working directory: ~/projects/myapp
Uptime: 142s | Messages sent: 5
```

**When no session exists:**
```
ℹ️ No active session
```

---

### `/stop`
Terminates your current Claude session **and cancels all running background agents**. Use this when you want to pause and resume later, or free up resources.

Response depends on what was active:

| Condition | Response |
|---|---|
| Session + agents running | `✅ Session stopped, N background agents cancelled.` |
| Session only | `✅ Session stopped.` |
| Agents only (no session) | `✅ N background agents cancelled.` |
| Nothing active | `ℹ️ No active session` |

The next message you send will start a fresh session automatically.

---

### `/clear`
Stops the current session and **immediately starts a fresh one** — equivalent to `/clear` in the Claude Code TUI.

```
🧹 Context cleared. New session started.
```

Use this when Claude has accumulated too much context and you want to start a clean conversation. The new session is ready immediately so your next message has no cold-start delay.

**Difference from `/stop`:** `/stop` leaves no session running; `/clear` always leaves a fresh session ready.

---

### Auto-compaction

Archon can automatically compact and reset the session when the context window fills up — the same as running a manual `/clear`, but triggered automatically.

**How it works:** After each response is delivered, Archon checks the context window usage. If usage reaches the configured threshold, it:
1. Fires `compact_today()` in the background (non-blocking) — summarises today's conversation for the next session
2. Immediately clears the session (equivalent to `/clear`)
3. Sends a notification (verbose/debug mode only)

The fresh session re-injects compacted history as normal.

**Notification** (verbose/debug only):
```
⚙️ Auto-compaction triggered (context: 83% of 200K)
```

**Configuration** (`config.toml`):

| Key                                | Default | Description                                                                                                                 |
| ---------------------------------- | ------- | --------------------------------------------------------------------------------------------------------------------------- |
| `[history] auto_compact_threshold` | `80`    | Context percentage that triggers auto-compaction. `0` = disabled. Valid range: 20–100. Values 1–19 are rejected at startup. |

**Example:**
```toml
[history]
auto_compact_threshold = 80   # compact + reset when context reaches 80%
```

> **Note:** The background `compact_today()` call runs asynchronously and will not be reflected in the immediately-recreated session — the fresh session gets whatever partial summary already exists. The partial is updated for the next compaction cycle.

---

### `/restart`
Gracefully stops all active sessions and hot-reloads the Archon daemon process.

```
♻️ Restarting...
```

Once the daemon comes back up, it sends:
```
✅ Restarted. Archon ready.
```

Use this after updating Archon or changing `config.toml`. Session context is reset (new session on next message); conversation log files are preserved on disk.

---

### `/notify [<mode> [N] | interval N]`

Opens the notification panel or sets the mode directly.

**No argument** — sends a tap-to-switch inline keyboard:

```
⚙️ Notification mode
[ 🔇 Quiet ]  [ 🔔 Normal ✓ ]
[ 📢 Verbose] [ 🔬 Debug   ]
```

Tap any button to switch instantly. The current mode is marked with ✓. The keyboard edits in-place — no message spam.

**Subcommands:**

| Command | Effect |
|---|---|
| `/notify quiet` | Switch to quiet mode (silent, only final response) |
| `/notify quiet 5` | Switch to quiet mode with beacon every 5 minutes |
| `/notify quiet 0` | Switch to quiet mode with no beacon |
| `/notify normal` | Switch to normal mode |
| `/notify verbose` | Switch to verbose mode |
| `/notify debug` | Switch to debug mode |
| `/notify interval 10` | Change beacon interval to 10 min (mode unchanged) |

Settings are persisted to `config.toml` immediately.

---

### `/quiet [N]`
Switch to quiet mode. Optional `N` sets the beacon interval in minutes (`0` = no beacon).

```
/quiet       → 🔇 Quiet mode
/quiet 5     → 🔇 Quiet mode — beacon every 5 min
/quiet 0     → 🔇 Quiet mode
```

Replies with the inline keyboard so you can easily switch back.

---

### `/normal`
Switch to normal mode. Replies with the inline keyboard.

---

### `/verbose`
Switch to verbose mode. Replies with the inline keyboard.

---

### `/debug`
Switch to debug mode. Replies with the inline keyboard.

---

### `/context`
Shows context window usage for the current session.

```
📊 Context Window

[████████░░░░░░░░░░░░] 41%
85,234 / 200,000 tokens

📥 Input:        85,234 t
📤 Output:        4,102 t
♻️ Cache read:   12,800 t
🆕 Cache new:     3,400 t

🔄 12 turns  💰 $0.082  ⏱ 3.4s
```

**When no session is active**, the response depends on state:

| Condition | Response |
|---|---|
| A background agent is still running | `🔄 Context window cleared — a background agent is running` |
| Session was recently cleared or stopped | `🔄 Context window cleared — session saved` |
| No session has ever started | `📊 No context data yet — send a message first` |
| Session active but no messages sent yet | `📊 No context data yet — send a message first` |

---

### `/skills`
Lists all available skills — personal skills from `~/.claude/skills/` and any plugin-bundled skills.

```
🎯 Personal skills:

• my-skill
  Does something useful

🔌 Plugin skills:

[my-plugin v1.0]
• plugin-skill
  A plugin-provided skill
```

If no skills are configured: replies `No skills available.`

---

### `/skill <name>`
Activates a named skill for the current session. The skill's system prompt will be injected into your next message.

```
/skill my-skill  →  ✅ Skill `my-skill` activated — it will be applied to your next message
```

- If the skill name is not found: shows an error and suggests `/skills`
- Requires an active session — send a message first if none exists

---

### `/models [name|default]`
Shows the current model or switches to a different one.

**No argument** — shows the current model and an inline keyboard of configured models:

```
🤖 Current: claude-opus-4-6

[ claude-sonnet-4-6 ]  [ claude-opus-4-6 ✓ ]
[ Default (SDK)      ]
```

Tap a button to switch instantly. Switching clears the active session so the new model takes effect.

**With argument:**

| Command | Effect |
|---|---|
| `/models claude-sonnet-4-6` | Switch to the named model (session cleared) |
| `/models sonnet` | Short alias — resolves to `claude-sonnet-4-6` |
| `/models opus` | Short alias — resolves to `claude-opus-4-6` |
| `/models haiku` | Short alias — resolves to `claude-haiku-4-5` |
| `/models default` | Revert to SDK default model (session cleared) |

Model names must match entries in `[models] available` in `config.toml`. Short aliases (`sonnet`, `opus`, `haiku`) always work regardless of the available list. Any string is accepted when typed directly.

> **Hidden aliases:** `/model` still works as a backward-compatible alias for `/models`.

---

### `/agents`
Lists all available agent types discovered from `~/.claude/agents/*.md` files. Agents are split into two groups:

- **Archon agents** (filename ends with `-archon.md`) — automatically injected into every Claude session.
- **Other agents** — present in the directory but TUI-only; not injected by Archon.

```
🤖 Archon agents (active in sessions):

• Researcher (claude-sonnet-4-6)
  Specialises in web search and information gathering
  🔧 Tools: WebSearch, Read

🔍 Other agents (TUI-only, not injected):

• Coder
  Writes and reviews code
  🔧 Tools: Bash, Read, Write, Edit
```

If no agents are found: `ℹ️ No agent types configured. Add name-archon.md files to ~/.claude/agents/`

---

### `/scheduled`
Lists all configured scheduled jobs and their current status.

```
📅 Scheduled Jobs

• echo-test: ✅ 14:02:01 (runs: 3)
  └ hello from scheduled job
• health-summary: ⏳ waiting (runs: 0)
• nightly-backup: 🔄 running (runs: 12)
```

**Status icons:**

| Icon | Meaning |
|---|---|
| ⏳ waiting | Job has never run yet |
| ✅ HH:MM:SS | Last run time (error detail shown on next line if the run failed) |
| 🔄 running | Currently executing |
| ⚠️ invalid config | Pipeline validation error — config must be fixed before the job can run |

If the scheduler is not configured: replies `ℹ️ Job scheduler not configured.`

If no jobs are defined in `schedules/`: replies `ℹ️ No scheduled jobs configured.`

> **Hidden alias:** `/jobs` still works as a backward-compatible alias for `/scheduled`.

---

### `/tasks`

Lists all background agents currently running for your user, with a cancel button for each.

```
🤖 Running agents:

• Atlas (2m 14s)
  Analyse the auth module for security issues

• Orion (47s)
  Generate unit tests for gateway.py

[ ❌ Cancel Atlas ]
[ ❌ Cancel Orion ]
```

If no agents are running: replies `ℹ️ No background agents running.`

Tap **Cancel** next to an agent to stop it immediately.

> **Hidden alias:** `/running_agents` still works as a backward-compatible alias for `/tasks`.

---

## Background Agents

Claude can spawn background agents — isolated Claude sessions that run long tasks asynchronously while the main conversation stays fully interactive.

### How it works

When Claude decides to run a subtask in the background, it calls the `spawn_background_agent` MCP tool. Archon handles this by:

1. Assigning the agent a human-readable name from a pool of 30 (Atlas, Orion, Nova, …)
2. Starting an isolated Claude session as an asyncio task — the main conversation is **never blocked**
3. Sending you a spawn notification immediately:
   ```
   🤖 Agent <b>Atlas</b> spawned.
   ```
4. Sending periodic **beacon messages** (new messages, not edits) at the configured interval so you know the agent is still running:
   ```
   🤖 Agent <b>Atlas</b> is working...
   🤖 Agent <b>Atlas</b> is working... (3 tools)
   🤖 Agent <b>Atlas</b> is pondering... (3 tools, 1 thinking)
   ```
5. Sending a completion notification when the agent finishes:
   ```
   ✅ 🤖 Agent <b>Atlas</b> completed
   <result content>
   ```

### Agent names

Every background agent gets a unique human-readable name for its lifetime. No two concurrently-running agents share a name. Names are released when an agent finishes and can be reused by later agents.

### Managing agents

Use `/tasks` to see all active agents. Each agent has an inline **Cancel** button you can tap to stop it immediately.

> **Technical note:** Background agents are spawned exclusively via the `spawn_background_agent` MCP tool. Archon permanently disables the Claude Agent SDK's native `Task` tool, which would block the main conversation for the entire sub-agent duration. All agent Telegram messages (spawn, beacon, completion) come directly from `BackgroundAgentManager` — not from the SDK event stream.

---

## Scheduled Jobs

Archon can run automated jobs on a cron schedule — executing pipelines of shell commands (`_tool` steps) and Claude prompts (`_prompt` steps), then broadcasting results via Telegram to all allowed users.

Each job is a standalone `.toml` file in the `schedules/` directory with a `[pipeline]` section. Jobs support timezone-aware scheduling, step chaining via `{step_name}` references, and auto-disable on validation errors.

Use `/scheduled` in Telegram to list all jobs, their status, and next run times.

> **Full guide:** See the [Scheduled Jobs Guide](schedule_guide.md) for job file format, pipeline syntax, examples, validation, timezone handling, and troubleshooting.

---

## RAG Search

RAG (Retrieval-Augmented Generation) is an optional feature that gives Claude semantic and keyword search over your conversation history and any document collections you define. Once installed, Claude can call the `search` MCP tool automatically when it needs to recall past conversations or look up information from your documents.

### Hardware requirements

- **RAM**: ~2 GB recommended (embedder + reranker models loaded in memory)
- **Disk**: ~150 MB for ONNX model download on first install (~33–130 MB embedding model, ~85 MB reranker)
- **CPU**: All operations run on CPU by default; NVIDIA GPU is used automatically if detected via `nvidia-smi`

### Installation

```bash
archon rag install
```

This command:
1. Installs RAG Python dependencies (`uv pip install -e ".[rag]"`)
2. Creates `~/.archon/rag/` data directory
3. Downloads ONNX embedding and reranker models
4. Registers the RAG server as a launchd service (macOS) or systemd user service (Linux)
5. Runs an initial ingest of your conversation history into the `archon-history` collection

After installation, enable RAG in `config.toml`:

```toml
[rag]
enabled = true
host = "localhost"
port = 8282
history_collection = "archon-history"
```

Then restart Archon (`/restart`) to connect to the RAG server.

### Adding document collections

```bash
archon rag ingest /path/to/documents --collection my-docs
archon rag ingest                    # re-ingest history collection (no path)
```

**Supported file formats**: PDF, DOCX, XLSX, PPTX, HTML, MD, TXT, and common code files (`.py`, `.js`, `.ts`, `.go`, `.rs`, `.java`, `.cpp`, `.c`, `.sh`, etc.)

Ingestion parses each file, splits it into overlapping chunks, embeds them with a local ONNX model, and stores them in LanceDB. The collection name defaults to the directory basename if `--collection` is omitted.

### Available MCP tools

Once the RAG server is connected, Claude has access to 9 MCP tools:

| Tool | Description |
|---|---|
| `search` | Hybrid BM25 + vector search within the specified collection (defaults to history collection); returns ranked results with text, source path, and score |
| `search_with_context` | Like `search`, but includes surrounding chunks for richer context. `context_window` (default `1`) controls adjacent chunks included on each side |
| `ingest_file` | Parse, chunk, embed, and store a single file at a given path |
| `ingest_directory` | Ingest all supported files under a directory into a named collection. `glob_pattern` (default `**/*`) filters files |
| `list_collections` | List all indexed collections with document counts and sizes |
| `get_collections_meta` | Return full metadata for all collections including centroid vectors (used by routing) |
| `get_collection_meta` | Return full metadata for one named collection including centroid |
| `list_documents` | List documents within a specific collection |
| `delete_document` | Remove a document and all its chunks from the store |

### `archon rag` CLI reference

| Command | Description |
|---|---|
| `archon rag install` | Install dependencies, download models, register service, run initial ingest |
| `archon rag install --dry-run` | Print actions without executing |
| `archon rag install --non-interactive` | Skip confirmation prompt |
| `archon rag uninstall` | Stop and remove the RAG service; data in `~/.archon/rag/` is preserved |
| `archon rag uninstall --delete-db` | Stop and remove the RAG service; also deletes the vector database in `~/.archon/rag/db` |
| `archon rag start` | Start the RAG MCP server |
| `archon rag stop` | Stop the RAG MCP server |
| `archon rag status` | Show service state, port, and collection statistics |
| `archon rag ingest [path] [--collection name]` | Ingest files into a collection; defaults to history dir if no path given |

> **Note:** On Windows, `archon rag start/stop` print a message directing you to run `python -m archon.rag.server` manually. The server itself works on all platforms.

### Known limitations

- **No auto re-indexing** — run `archon rag ingest` after adding new documents or to pick up recent history.
- **Reranker latency** — adds ~160 ms per search on CPU (negligible for a personal knowledge base).
- **No QMD migration** — existing QMD collections are not imported; re-ingest from source files.

---

## Notification modes

Archon has four verbosity levels:

| Mode | What you see |
|---|---|
| **quiet** | `⏳ Working...` then only `✅ Response` (or `❌ Error`) |
| **normal** | Tool name only, final response |
| **verbose** | Tool name + arguments, brief one-line result summary, thinking |
| **debug** | Everything — full tool output, thinking, all events |

**Visibility matrix:**

| Event | quiet | normal | verbose | debug |
|---|:---:|:---:|:---:|:---:|
| ✅ Response | ✓ | ✓ | ✓ | ✓ |
| ❌ Error | ✓ | ✓ | ✓ | ✓ |
| 🔧 Tool name | ✗ | ✓ | ✓ | ✓ |
| 🔧 Tool arguments | ✗ | ✗ | ✓ | ✓ |
| 📤 Result (brief) | ✗ | ✗ | ✓ | ✗ |
| 📤 Result (full) | ✗ | ✗ | ✗ | ✓ |
| 💭 Thinking | ✗ | ✗ | ✓ | ✓ |
| 🤖 Agent start/stop | ✓ | ✓ | ✓ | ✓ |
| 🔔 Reminder injected | ✗ | ✗ | ✓ | ✓ |

**Beacon mode (quiet only):** when `interval_minutes > 0`, Archon sends a periodic `⏳ Working... (N tools, M thinking)` status update so you know it's still running. Set with `/quiet N` or `/notify interval N`. Use `/quiet 0` or `/notify quiet 0` to disable.

> **See also:** [Error Handling Strategy](../Architecture/140_error_handling_strategy.md) — documents how notification-mode filtering is applied per event type and how delivery errors are handled gracefully.

---

## Output events

Every Claude state change produces a Telegram message (which events are shown depends on the mode):

| Message | Meaning |
|---|---|
| `💭 Thinking: <content>` | Claude's internal reasoning |
| `🔧 Tool: <name>` | Claude is calling a tool, e.g. `Bash`, `Read` |
| `📤 <brief summary>` | Brief tool result summary (verbose only) |
| `📤 Result:\n<content>` | Full tool output (debug only) |
| `✅ Response: <content>` | Claude's final answer |
| `❌ Error: <message>` | Something went wrong |
| `🤖 Agent <b>Name</b> started` | Inline sub-agent started (SDK event) |
| `🤖 Agent <b>Name</b> done` | Inline sub-agent finished (SDK event) |
| `🤖 Agent <b>Name</b> spawned.` | Background agent started (spawn notification) |
| `🤖 Agent <b>Name</b> is working... (N tools)` | Periodic beacon — agent is still running |
| `✅ 🤖 Agent <b>Name</b> completed` | Background agent finished |

Long outputs are automatically split into numbered chunks: `[1/3]`, `[2/3]`, `[3/3]`.

> **See also:** [Services and Integration Architecture](../Architecture/120_services_and_integration_architecture.md) — documents the background agent lifecycle, `ArchonMCPServer`, and `BackgroundAgentManager` that produce the agent spawn/beacon/completion notifications above.

---

## Quick reference

```
/start      → confirm bot is running
/status     → session state, working directory, uptime
/stop       → terminate active session
/clear      → reset context, start fresh session
/restart    → hot-reload the daemon

/context    → context window usage (tokens, cost, turns)
/skills     → list available skills
/skill <n>  → activate a skill for the next message
/models     → show/switch Claude model
/agents     → list available agent types (~/.claude/agents/)
/scheduled  → list scheduled jobs and their status
/tasks      → list running background agents

/quiet [N]  → 🔇 silent, optional beacon every N min
/normal     → 🔔 tool names + brief results
/verbose    → 📢 tool args + thinking content
/debug      → 🔬 everything, full output

/notify     → tap-to-switch notification panel
```

---

## Config file resilience

Archon protects your `config.toml` against corruption caused by unexpected process termination (SIGTERM, SIGKILL, power loss, etc.).

**Atomic writes.** Every config change (e.g. switching notification mode) is written atomically: the new content is first flushed to a temporary file (`config.toml.tmp`), then atomically renamed over the original. This means `config.toml` is either fully written or untouched — never truncated.

**Automatic backup.** Each time Archon starts and successfully parses `config.toml`, it creates a backup copy at `config.toml.bak` in the same directory. This happens transparently on every boot.

**Auto-recovery.** If `config.toml` is found to be corrupt on startup (e.g. truncated by a prior crash), Archon automatically restores it from `config.toml.bak` and continues booting. A warning is logged so you know recovery occurred. If no backup exists, Archon reports a clear error explaining what happened.

> **Note:** The backup is created at load time, not on every write. If you manually edit `config.toml` and introduce a syntax error, `/restart` will attempt to restore the last known-good backup.

> **See also:** [Data Architecture and Persistence](../Architecture/130_data_architecture_and_persistence.md) — full details on the atomic write pattern, backup lifecycle, and `config.toml` schema. [Release and Environment Strategy](../Architecture/510_release_and_environment_strategy.md) — documents the `.env`/`config.toml` split and how configuration is managed across environments.

---

## Tips

- **Start fresh when Claude seems confused** — `/clear` resets context without restarting the daemon.
- **Long-running tasks** — use `/quiet 5` for beacon check-ins every 5 minutes without message spam.
- **Clean experience** — `/quiet` lets Claude work silently; you only see the final answer.
- **Debug a bad response** — `/debug` then repeat your message to see exactly what Claude was thinking and which tools it called.
- **After updating Archon** — use `/restart` to reload the daemon without losing your SSH session or terminal.
- **Watch your spend** — `/context` shows cumulative cost and token usage for the session at a glance.
- **Approaching context limit** — the `/context` progress bar fills toward 200k; use `/clear` before it reaches 100%, or set `auto_compact_threshold` in `config.toml` to let Archon handle it automatically.
- **Supercharge Claude** — `/skills` to browse available skills, then `/skill <name>` to inject one before your next message.
- **Switch models on the fly** — `/models` opens a keyboard; switching clears the session so the new model starts fresh.
