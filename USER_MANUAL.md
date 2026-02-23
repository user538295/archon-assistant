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

### `/notify [thinking|tools|off|full|partial [N]]`

Controls all notification settings in one command.

**Subcommands:**

| Command | Effect |
|---|---|
| `/notify` *(no arg)* | Show all current notification settings |
| `/notify thinking` | Toggle Claude's thinking content on/off |
| `/notify tools` | Toggle tool output between full and brief (one-line summary) |
| `/notify off` | Stream all events (thinking, tools, results, response) |
| `/notify full` | Show only the final response |
| `/notify partial` | Show final response + periodic status with current interval |
| `/notify partial 5` | Show final response + status every 5 minutes |

**Streaming modes:**

| Mode | What you see |
|---|---|
| `off` | Everything — thinking, tool calls, tool results, final response |
| `full` | A "working…" status while Claude runs, then only the final response |
| `partial` | The final response + brief status updates every N minutes |

**Example output for `/notify`:**
```
⚙️ Notification settings:
  💭 thinking results: on
  🔧 tool output: full
  ⚡ mode: off

Change: /notify thinking | /notify tools | /notify off|full|partial [N]
```

**Example after `/notify full`:**
```
⚡ Mode: full
```

**Example after `/notify partial 5`:**
```
⚡ Mode: partial (every 5 min)
```

Settings are persisted to `config.toml` immediately.

---

### `/settings`
Shows a summary of all current notification settings. Read-only — use `/notify` to change them.

```
⚙️ Notification settings:
  💭 thinking results: on
  🔧 tool output: full
  ⚡ mode: partial (2 min)
```

---

## Output events

When concise mode is `off`, every Claude state change produces a Telegram message:

| Message | Meaning |
|---|---|
| `💭 Thinking...` | Claude is reasoning (start) |
| `💭 Thought: <content>` | Claude's internal reasoning (result) |
| `🔧 Tool: <name>` | Claude is calling a tool, e.g. `Bash`, `Read` |
| `📤 Result: <content>` | Output returned from the tool |
| `✅ Response: <content>` | Claude's final answer |
| `❌ Error: <message>` | Something went wrong |

Long outputs are automatically split into numbered chunks: `[1/3]`, `[2/3]`, `[3/3]`.

---

## Notification modes quick reference

```
/notify off     → 💭 Thinking... | 💭 Thought | 🔧 Tool | 📤 Result | ✅ Response
/notify full    → (working quietly) → ✅ Response
/notify partial → (status every N min) → ✅ Response

/notify thinking → show/hide 💭 Thought content
/notify tools    → full / brief 🔧 Tool + 📤 Result
```

---

## Tips

- **Start fresh when Claude seems confused** — `/clear` resets context without restarting the daemon.
- **Long-running tasks** — use `/notify partial 5` to get periodic check-ins without message spam.
- **Quiet mode** — `/notify full` gives you a clean experience: Claude works silently and you only see the answer.
- **Debug a bad response** — `/notify off` then repeat your message to see exactly what Claude was thinking and which tools it called.
- **After updating Archon** — use `/restart` to reload the daemon without losing your SSH session or terminal.
