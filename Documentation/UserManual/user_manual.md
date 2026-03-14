**Purpose**: End-user guide for Telegram bot commands and features
**Audience**: End users
**Status**: Stable
**Last reviewed**: 2026-02-26
**Next review**: 2027-02-26

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
Uptime: 142s
```

**When no session exists:**
```
ℹ️ No active session
```

---

### `/stop`
Terminates your current Claude session without starting a new one. Use this when you want to pause and resume later, or free up resources.

- If a session is active: stops it and confirms with `✅ Session stopped.`
- If no session is active: replies `ℹ️ No active session`

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
/quiet 0     → 🔇 Quiet mode  (no beacon)
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

- If there is no active session: replies `ℹ️ No active session`
- If no messages have been sent yet: replies `📊 No context data yet — send a message first`

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
| ✅ HH:MM:SS | Last successful run time |
| 🔄 running | Currently executing |
| ❌ error text | Last run failed — error preview shown below |

If the scheduler is not configured: replies `ℹ️ Job scheduler not configured.`

If no jobs are defined in `schedules/`: replies `ℹ️ No scheduled jobs configured.`

> **Hidden alias:** `/jobs` still works as a backward-compatible alias for `/scheduled`.

---

### `/tasks`

Lists all background agents currently running for your user, with a cancel button for each.

```
🤖 Running agents (2)

• Atlas — Analyse the auth module for security issues
  ⏱ 2m 14s  [ Cancel ]

• Orion — Generate unit tests for gateway.py
  ⏱ 0m 47s  [ Cancel ]
```

If no agents are running: replies `ℹ️ No background agents currently running.`

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

Use `/tasks` to see all active agents and cancel any of them. You can also cancel via the `/cancel <run_id>` command from the Telegram bot.

> **Technical note:** Background agents are spawned exclusively via the `spawn_background_agent` MCP tool. Archon permanently disables the Claude Agent SDK's native `Task` tool, which would block the main conversation for the entire sub-agent duration. All agent Telegram messages (spawn, beacon, completion) come directly from `BackgroundAgentManager` — not from the SDK event stream.

---

## Scheduled Jobs

Archon can run automated jobs on a schedule, execute pipelines (bash scripts → Claude prompts), and send you the result via Telegram.

### How it works

1. Enable the scheduler in `config.toml`
2. Create a job bundle directory per job in `schedules/` (e.g. `schedules/my-job/job.toml`)
3. The directory name becomes the job name shown in `/scheduled`
4. Archon checks every minute and fires jobs whose schedule is due

### Enabling the scheduler

In `config.toml`:

```toml
[schedule]
enabled = true
jobs_dir = "schedules"   # relative to config.toml location
```

### Job bundles

Each job lives in its own directory under `schedules/`. The directory name is the job name and the configuration file is always called `job.toml`:

```
schedules/
├── echo-test/
│   └── job.toml
└── health-summary/
    ├── job.toml
    └── scripts/          # optional — bundled scripts, data, etc.
        └── check.sh
```

The directory can contain any supporting files (scripts, data, templates) alongside `job.toml`. When the scheduler installs jobs, the entire directory is copied as a unit.

### Job file format

```toml
# schedules/my-job/job.toml

cron = "*/5 * * * *"    # standard 5-field cron expression
notify_user_id = 123456789  # Telegram user ID to notify on completion
timeout_seconds = 30        # per-step timeout (default: 60)
enabled = true              # set to false to disable without deleting the file

[[pipeline]]
tool = "scripts/health_check.sh"   # bash command; stdout feeds the next step

[[pipeline]]
prompt = "Summarize in one line: {input}"  # Claude prompt; {input} = previous step's output
```

**Pipeline steps:**

| Key | Type | Description |
|---|---|---|
| `tool` | string | Bash command or script path. stdout is passed to the next step. |
| `prompt` | string | Claude prompt. `{input}` is replaced with the previous step's output. Runs in an isolated Claude session. |

Steps are chained: the stdout of step N is automatically passed as the input to step N+1.

### Naming conventions

- Use **kebab-case** for directory names (e.g. `health-check/job.toml`, `nightly-backup/job.toml`)
- The directory name is the job name everywhere — in `/scheduled` output and Telegram notifications
- Jobs are loaded alphabetically so ordering is deterministic

### Migrating from flat files

Flat files (`schedules/name.toml`) still work but are **deprecated**. Archon sends a Telegram warning on startup for each flat-file job detected. Both formats in the same directory for the same job name (e.g. `schedules/my-job.toml` and `schedules/my-job/job.toml`) cause a collision error at startup.

To migrate:

```bash
# 1. Create a directory with the job name
mkdir schedules/my-job

# 2. Move the flat file into it as job.toml
mv schedules/my-job.toml schedules/my-job/job.toml
```

No changes to the TOML content are needed -- the file format is identical.

### Cron expression syntax

Standard 5-field cron: `minute hour day-of-month month day-of-week`

| Expression | Meaning |
|---|---|
| `* * * * *` | Every minute |
| `*/5 * * * *` | Every 5 minutes |
| `0 8 * * *` | Daily at 08:00 |
| `0 8 * * 1` | Every Monday at 08:00 |
| `0 9,17 * * 1-5` | 09:00 and 17:00, Monday–Friday |

### Example: daily summary

```toml
# schedules/daily-summary/job.toml
cron = "0 8 * * *"
notify_user_id = 123456789
timeout_seconds = 60

[[pipeline]]
tool = "git -C ~/projects/myapp log --oneline --since='24 hours ago'"

[[pipeline]]
prompt = "Summarise these recent commits in 2-3 bullet points: {input}"
```

### Notifications

On job completion Archon sends:

```
✅ Scheduled: daily-summary
Summarised 5 commits: ...
```

On failure:

```
❌ Scheduled: daily-summary
Tool step failed (exit 1): permission denied
```

---

## Notification modes

Archon has four verbosity levels:

| Mode | What you see |
|---|---|
| **quiet** | `⏳ Working...` then only `✅ Response` (or `❌ Error`) |
| **normal** | Tool name, brief one-line result summary, final response |
| **verbose** | Tool name + arguments, brief result, thinking complete |
| **debug** | Everything — full tool output, thinking, all events |

**Visibility matrix:**

| Event | quiet | normal | verbose | debug |
|---|:---:|:---:|:---:|:---:|
| ✅ Response | ✓ | ✓ | ✓ | ✓ |
| ❌ Error | ✓ | ✓ | ✓ | ✓ |
| 🔧 Tool name | ✗ | ✓ | ✓ | ✓ |
| 🔧 Tool arguments | ✗ | ✗ | ✓ | ✓ |
| 📤 Result (brief) | ✗ | ✓ | ✓ | ✗ |
| 📤 Result (full) | ✗ | ✗ | ✗ | ✓ |
| 💭 Thinking | ✗ | ✗ | ✓ | ✓ |
| 🤖 Agent start/stop | ✓ | ✓ | ✓ | ✓ |

**Beacon mode (quiet only):** when `interval_minutes > 0`, Archon sends a periodic `⏳ Working... (N tools, M thinking)` status update so you know it's still running. Set with `/quiet N` or `/notify interval N`. Use `/quiet 0` or `/notify quiet 0` to disable.

> **See also:** [Error Handling Strategy](../Architecture/140_error_handling_strategy.md) — documents how notification-mode filtering is applied per event type and how delivery errors are handled gracefully.

---

## Output events

Every Claude state change produces a Telegram message (which events are shown depends on the mode):

| Message | Meaning |
|---|---|
| `💭 Thinking: <content>` | Claude's internal reasoning |
| `🔧 Tool: <name>` | Claude is calling a tool, e.g. `Bash`, `Read` |
| `📤 ✓ <first line>` | Brief one-line tool result summary (normal/verbose) |
| `📤 Result: <content>` | Full tool output (debug) |
| `✅ Response: <content>` | Claude's final answer |
| `❌ Error: <message>` | Something went wrong |
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
- **Approaching context limit** — the `/context` progress bar fills toward 200k; use `/clear` before it reaches 100%.
- **Supercharge Claude** — `/skills` to browse available skills, then `/skill <name>` to inject one before your next message.
- **Switch models on the fly** — `/models` opens a keyboard; switching clears the session so the new model starts fresh.
