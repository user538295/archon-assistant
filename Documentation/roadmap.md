**Purpose**: Tracks the product roadmap — completed phases and pending features with exact task IDs from `Documentation/tasks.md`.
**Audience**: All developers
**Status**: Stable
**Last reviewed**: 2026-02-26
**Next review**: 2026-05-26

# Archon Assistant — Roadmap

## Principles

1. **Task IDs are the source of truth** — every item here maps 1:1 to a task in `Documentation/tasks.md`; keep them in sync.
2. **Completed work is stable** — items in Phase 1–4 are shipped; do not re-open them without a new task ID.
3. **Pending items are ordered by value** — higher items unblock more downstream work; tackle them first.
4. **TDD applies to every item** — each pending feature requires unit, integration, e2e, and (where applicable) live tests.
5. **Update this file when tasks move** — mark items done in `Documentation/tasks.md` and here in the same PR.

---

## Phase 1 — Foundation ✅

All core infrastructure stories are complete.

| Story | Description |
|---|---|
| S0.1 | Project structure initialised |
| S0.2 | Config loader (`config.toml` + `.env`) |
| S4.1 | Rotating file logger, `logging.getLogger("archon")` |
| S4.4 | Daily log rotation at midnight; startup rotation |
| S1.1 | `ClaudeSession` wrapping `ClaudeSDKClient` |
| S1.2 | `EventMapper` — SDK messages → typed event dataclasses |
| S1.3 | `TruncationStrategy` ABC + `SplitStrategy` |
| S1.4 | `SessionManager` — per-user registry with inactivity eviction |

## Phase 2 — Chat & Gateway ✅

| Story | Description |
|---|---|
| S2.1–S2.6 | Telegram bot, whitelist middleware, commands, native command menu |
| S3.1–S3.2 | Gateway orchestrator, graceful shutdown (SIGTERM/SIGINT, ≤5s) |
| S4.2–S4.3 | launchd (macOS) and systemd (Linux) service installation |

## Phase 3 — Features ✅

| Story | Description |
|---|---|
| S7.1 | Chat history — daily Markdown files in `~/.archon/history/sessions/` |
| S8.1–S8.4 | Notification modes (quiet / normal / verbose / debug) + inline keyboard |
| S6.1–S6.2 | Skills integration (`SkillLoader`, `/skills`, `/skill`) |
| S9.1 | Model switching via `/models` inline keyboard |
| S10.1 | Plugin loading from `~/.claude/plugins/` |
| S11.1 | Context window tracking (`/context` command) |
| S11.2–S11.3 | Sub-agent support + per-agent notification config |
| S12.1 | Filesystem agent loader (`~/.claude/agents/*.md`) |
| S14.1 | Session diagnostics (`/status` processing state, idle time) |

## Phase 4 — Background Agents & Distribution ✅

| Story | Description |
|---|---|
| S15.1–S15.6 | Background agent execution — `BackgroundAgentManager`, `ArchonMCPServer`, `/tasks`, live e2e |
| S16.0 *(bonus)* | Shell installer (`install.sh`) — prerequisites check, service registration |

---

## Phase 5 — Pending

### S16.1 — Python installer
**Task**: Replace `install.sh` with `install.py` (PEP 723 inline metadata).

The new installer must support:
- `--dry-run` — print what would happen without side effects
- `--uninstall` — remove service and config
- `--update` — pull latest code and restart service
- `--non-interactive` — accept defaults; suitable for CI or scripted runs
- `rich`-based output with progress indicators
- Pure functions for each install step (testable without subprocess stubs or fake `HOME`)
- Standard `pytest` unit tests

See `stories.md § S16.1` for full acceptance criteria.

---

### FR.005 — Context compaction with session summary
**Task**: Watch the context window after each response. When usage approaches the limit, generate a summary of the current session, `/clear` the session, inject the summary, and continue — transparently.

Scope: `ClaudeSession`, `SessionManager`, config key in `[session]`, unit + integration + e2e + live tests (TDD). Start with happy paths, then edge cases.

---

### FR.009 — Cron job pipeline format fix
**Task**: The current pipeline TOML uses `[[pipeline]]` multi-table syntax. Change it to inline array notation for consistency with the original specification:

```toml
# current (wrong)
[[pipeline]]
tool = "scripts/health_check.sh"

[[pipeline]]
prompt = "Summarize in one line: {input}"

# target (correct)
pipeline = [
  { tool = "scripts/health_check.sh" },
  { prompt = "Summarize in one line: {input}" },
]
```

Scope: `config/loader.py`, `CronScheduler`, `cron.d/` example files, `README.md` cron section, unit + integration + e2e + live tests (TDD).

---

### FR.010 — UTC label on all timestamps in logs and history
**Task**: Every timestamp in log output and `~/.archon/history/` files currently shows the time without an explicit timezone suffix. Append `UTC` everywhere so no timestamp is ambiguous.

Scope: `log_setup.py` formatter, `HistoryManager` date headers, unit tests for all affected formatters.

---

### FR.011 — Compaction counter in `/context`
**Task**: Count context compaction events per session and surface the count in the `/context` command output alongside token usage, cost, and turn count.

Scope: `ClaudeSession`, `SessionManager.context_stats()`, `/context` handler, unit + integration + e2e + live tests (TDD).

---

### FR.012 — Agent task brief in start notification
**Task**: When an agent starts, include a one-line task brief in the spawn notification:

```
🤖 Agent Nova started: Summarize the content of xyz.txt
```

Scope: `BackgroundAgentManager`, `ArchonMCPServer` (expose task in spawn tool descriptor), `format_event` for `SubagentStarted`, unit + integration tests.

---

### FR.013 — Thinking brief in normal mode
**Task**: In normal notification mode, show a short summary of Claude's thinking alongside tool results. Trim after two sentences or before the first `\n` (whichever comes first).

Scope: `handler.format_event`, `EventMapper`, unit tests covering all truncation edge cases.

---

### Other pending items

| ID | Description |
|---|---|
| — | `/status` shows plugin and third-party component health (e.g., QMD connectivity) |
| — | Disable the interactive question UI — not supported via Claude Code SDK + Telegram |
| — | Fix installer paths: all installed files go under `~/.archon/` |
| — | macOS native app wrapper for TCC permissions — gives `archon_server` its own identity in System Settings → Privacy (see `Documentation/Backlog/02_macos_tcc_native_app_wrapper.md`) |

---

## See also

- `Documentation/tasks.md` — full implementation checklist with acceptance criteria
- `docs/stories.md` — user stories
- `contributing.md` — how to pick up and implement a pending item
