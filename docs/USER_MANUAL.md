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

Use this after updating Archon or changing `config.toml`. Your conversation history is not lost — the next message resumes normally.

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

### `/settings`
Alias for `/notify` — shows the tap-to-switch inline keyboard panel.

---

## Notification modes

Archon has four verbosity levels:

| Mode | What you see |
|---|---|
| **quiet** | `⏳ Working...` then only `✅ Response` (or `❌ Error`) |
| **normal** | Tool name, brief one-line result summary, final response |
| **verbose** | Tool name + arguments, brief result, thinking start + content |
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
| 💭 Thinking start | ✗ | ✗ | ✓ | ✓ |
| 💭 Thinking content | ✗ | ✗ | ✓ | ✓ |

**Beacon mode (quiet only):** when `interval_minutes > 0`, Archon sends a periodic `⏳ Working... (N tools, M thinking)` status update so you know it's still running. Set with `/quiet N` or `/notify interval N`. Use `/quiet 0` or `/notify quiet 0` to disable.

---

## Output events

Every Claude state change produces a Telegram message (which events are shown depends on the mode):

| Message | Meaning |
|---|---|
| `💭 Thinking...` | Claude is reasoning (start) |
| `💭 Thought: <content>` | Claude's internal reasoning (result) |
| `🔧 Tool: <name>` | Claude is calling a tool, e.g. `Bash`, `Read` |
| `📤 ✓ <first line>` | Brief one-line tool result summary (normal/verbose) |
| `📤 Result: <content>` | Full tool output (debug) |
| `✅ Response: <content>` | Claude's final answer |
| `❌ Error: <message>` | Something went wrong |

Long outputs are automatically split into numbered chunks: `[1/3]`, `[2/3]`, `[3/3]`.

---

## Quick reference

```
/quiet [N]  → 🔇 silent, optional beacon every N min
/normal     → 🔔 tool names + brief results
/verbose    → 📢 tool args + thinking content
/debug      → 🔬 everything, full output

/notify     → tap-to-switch panel
/settings   → same panel
```

---

## Tips

- **Start fresh when Claude seems confused** — `/clear` resets context without restarting the daemon.
- **Long-running tasks** — use `/quiet 5` for beacon check-ins every 5 minutes without message spam.
- **Clean experience** — `/quiet` lets Claude work silently; you only see the final answer.
- **Debug a bad response** — `/debug` then repeat your message to see exactly what Claude was thinking and which tools it called.
- **After updating Archon** — use `/restart` to reload the daemon without losing your SSH session or terminal.
