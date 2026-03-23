# FEAT-017 — Telegram /command executor
**Purpose**: Expose Claude Code custom slash commands (`~/.claude/commands/` and `<cwd>/.claude/commands/`) to the Telegram bot, so users can list and execute them without leaving the chat.
**Audience**: Archon end-users operating Claude Code via Telegram.
**Status**: To Do

---

## Background
Claude Code supports custom slash commands stored as `.md` files in `~/.claude/commands/` (global) and `<project>/.claude/commands/` (project-level). Currently Archon has no way to list or run these commands from Telegram — users must know the command name in advance and type it as raw text. A dedicated `/command` bot command fills this gap.

## Goal
Users can type `/command` to see a categorised list of all available custom commands (global and project), and `/command <name> [args…]` to execute any of them directly from Telegram. Execution streams events back exactly like a normal message. A "running" confirmation is shown before streaming (suppressed in quiet mode); a "not found" error is always shown regardless of notification mode.

---

## Scope

### In Scope
- `CommandLoader` — discovers `.md` files in global and project command directories
- `command_command` handler — list mode and execute mode
- Registration in `bot.py` dispatcher and `BOT_COMMANDS`
- "Running" pre-execution notification (suppressed in quiet mode)
- "Not found" error notification (all modes)
- Tests for all new logic

### Out of Scope
- Reading or rendering the content of command `.md` files for the listing (name only)
- Sub-directory nesting inside `~/.claude/commands/` (flat scan only)
- Editing or creating commands via Telegram
- Skills (`~/.claude/skills/`) — handled by the existing `/skills` / `/skill` commands

---

## Acceptance criteria
- [ ] `/command` with no args replies with two emoji-labelled sections: 🌐 Global and 📁 Project, each listing command names
- [ ] `/command` shows only non-empty sections (omits section header when directory is empty or absent)
- [ ] `/command <name>` where `<name>.md` exists sends `🔧 Running /<name>…` (not sent in quiet mode), then streams the command as `/<name>` prompt
- [ ] `/command <name> arg1 arg2` forwards `/<name> arg1 arg2` as the prompt
- [ ] `/command <name>` where `<name>.md` does not exist in either directory replies `❌ Command not found: /<name>` in **all** notification modes
- [ ] A session is created automatically if none exists when executing a command
- [ ] All tests pass; coverage ≥ 85 %

---

## What does NOT change
- `/skills` and `/skill` commands — unaffected
- `handle_message` in `archon/chat/handler.py` — called unchanged via `prompt_override`
- `SessionManager` — no new methods needed
- Notification mode logic elsewhere in the codebase

---

## Known limitations / accepted trade-offs
- Listing shows filenames only (no description) — command `.md` files have no standardised frontmatter to extract a description from
- Flat scan: commands nested in sub-directories inside `~/.claude/commands/` are ignored (matches Claude Code's own behaviour)
- Project commands path is the configured `cwd` (`session.working_directory`) — not the session's runtime working directory
- On name collision, project (local) command wins over global — this matches Claude Code's own resolution order
- Command file listing is not cached — added/removed files are reflected immediately on next `/command` call

---

## Architecture

### New: `archon/chat/command_loader.py`
Lives in `archon/chat/` because it is a filesystem scanner for Telegram command discovery — no AI execution involvement.

```python
@dataclass
class CommandInfo:
    name: str          # filename without .md extension
    source: Literal["global", "project"]

class CommandLoader:
    def __init__(self, global_dir: Path | None = None, project_dir: Path | None = None) -> None: ...

    def load_all(self) -> list[CommandInfo]:
        """Returns globals first (sorted alphabetically by name), then project commands (sorted alphabetically by name).
        Non-existent dirs silently skipped.
        Filename stems that do not match ^[a-zA-Z0-9_-]+$ are silently excluded."""

    def exists(self, name: str) -> bool:
        """True if <name>.md is present in either directory.
        Sanitizes name before filesystem lookup: must match ^[a-zA-Z0-9_-]+$;
        returns False immediately for any name that fails validation (no error raised)."""
```

- `global_dir` defaults to `Path.home() / ".claude" / "commands"`
- `project_dir` is `Path(cwd) / ".claude" / "commands"` — passed from gateway via `dp["command_loader"]`
- **Collision rule**: when the same name exists in both directories, the project command wins — it appears under 📁 only; the global entry is suppressed from the listing entirely. `exists()` returns `True` regardless of source.
- **Caching**: `load_all()` does NOT cache — it rescans the filesystem on every call. This ensures freshly added or removed command files are visible immediately. Contrast with `SkillLoader` which caches because skills are intended to be stable across a session.

### Modified: `archon/chat/commands.py`
New handler:
```python
async def command_command(
    message: Message,
    command_loader: CommandLoader,
    session_manager: SessionManager,
    truncation: TruncationStrategy,
    max_len: int = DEFAULT_MAX_LEN,
    notifications: NotificationsConfig | None = None,
    cwd: str = "",
    history_manager: HistoryManager | None = None,
    agent_logger: AgentLogger | None = None,
    background_agent_manager: BackgroundAgentManager | None = None,
) -> None:
```

Behaviour:
- Parse `message.text` → `parts = text.split(maxsplit=2)`
- No arg (`len(parts) < 2`): call `command_loader.load_all()`, format and reply
- With arg: check `command_loader.exists(cmd)`, send error or execute

**Execute mode guard for notifications**:
- `if notifications is not None and notifications.mode != "quiet":` → send `🔧 Running /<cmd>…` notification
- This guard is required — `notifications` can be `None` and must not cause a crash

### Modified: `archon/chat/bot.py`
- Import `command_command`
- Add `BotCommand(command="command", description="List or run a Claude Code command")`
- Register `dp.message.register(command_command, Command("command"))`
- Also register `dp.message.register(command_command, Command("commands"))` as a hidden alias (plural form, consistent with `/skills`, `/agents`, `/tasks`)

### Modified: `archon/gateway/gateway.py`
- Construct `CommandLoader(global_dir=..., project_dir=Path(cfg.session.working_directory) / ".claude" / "commands")`
- Wire: `dp["command_loader"] = command_loader`
- Import `CommandLoader` from `archon.chat.command_loader`

### Notification behaviour
| Situation | quiet | normal | verbose | debug |
|-----------|-------|--------|---------|-------|
| `🔧 Running /cmd…` | suppressed | shown | shown | shown |
| `❌ Command not found: /cmd` | shown | shown | shown | shown |

---

## Tests

- **test_command_loader_load_all_global_only** (unit): only global dir present → returns global commands with `source="global"`
- **test_command_loader_load_all_project_only** (unit): only project dir present → returns project commands with `source="project"`
- **test_command_loader_load_all_both** (unit): both dirs present → returns all commands with correct sources
- **test_command_loader_load_all_empty_dirs** (unit): both dirs empty → returns `[]`
- **test_command_loader_load_all_missing_dirs** (unit): dirs do not exist → returns `[]` without error
- **test_command_loader_load_all_ignores_non_md** (unit): non-`.md` files in dir are excluded
- **test_command_loader_load_all_sorted_within_source** (unit): filenames out of alpha order → output is sorted A-Z by name within each source group
- **test_command_loader_load_all_collision_project_wins** (unit): same name in both dirs → only project entry returned, global suppressed
- **test_command_loader_load_all_ignores_invalid_names** (unit): files with invalid names (spaces, dots, slashes in stem) excluded from results
- **test_command_loader_exists_global** (unit): `exists("plan-maker")` returns True when global file present
- **test_command_loader_exists_project** (unit): `exists("plan-maker")` returns True when project file present
- **test_command_loader_exists_false** (unit): `exists("missing")` returns False when absent from both
- **test_command_loader_exists_collision** (unit): name in both dirs → `True`
- **test_command_loader_exists_rejects_path_traversal** (unit): `exists("../../etc/passwd")` returns `False`
- **test_command_command_no_arg_lists_global_and_project** (unit): listing reply contains 🌐 and 📁 sections
- **test_command_command_no_arg_only_global** (unit): project dir empty → only 🌐 section shown
- **test_command_command_no_arg_only_project** (unit): global dir empty → only 📁 section shown
- **test_command_command_no_arg_no_commands** (unit): both empty → graceful "no commands" reply
- **test_command_command_execute_valid_sends_running_notification** (unit): valid command + non-quiet mode → answer called with "🔧 Running"
- **test_command_command_execute_quiet_suppresses_running_notification** (unit): valid command + quiet mode → running notification not sent
- **test_command_command_execute_notifications_none_no_crash** (unit): execute mode with `notifications=None`; mock `handle_message`; assert `message.answer` is NOT called before `handle_message` is called (the running notification is suppressed); assert `handle_message` is called with `notifications=None` forwarded
- **test_command_command_execute_calls_handle_message_with_prompt_override** (unit): `handle_message` called with `prompt_override="/plan-maker plan.md help"`
- **test_command_command_execute_no_args_prompt** (unit): `/command commit` → `prompt_override="/commit"`
- **test_command_command_not_found_sends_error** (unit): unknown command → `❌ Command not found: /foo` sent
- **test_command_command_not_found_sent_in_quiet_mode** (unit): quiet mode does NOT suppress the "not found" error
- **test_command_command_not_found_escapes_html_in_cmd** (unit): `/command <b>evil</b>` produces error reply with HTML-escaped command name, not rendered tags
- **test_create_dispatcher_registers_commands_alias** (unit): `Command("commands")` handler is also present in the dispatcher (plural alias)
- **test_command_command_passes_all_deps_to_handle_message** (unit): notifications, cwd, history_manager etc. forwarded correctly

---

## Documentation update
- [ ] `CLAUDE.md`, section: Commands table, path: `CLAUDE.md` — add `/command` row
- [ ] `Documentation/Architecture/110_component_catalog_and_layer_breakdown.md`, section: chat layer — mention `CommandLoader`

---

## Task breakdown

### Phase 0 — Prerequisites
> **Releasable**: after Task 0.1 — test suite is green again (all existing tests pass)

#### Task 0.1 — Remove stale test artifacts from prior incomplete session
- [ ] **File**: `tests/chat/test_commands.py`
- **Depends on**: nothing
- **Description**:
  - The test suite is currently broken at import time because a prior incomplete session left stale references in `tests/chat/test_commands.py`:
  1. Remove `command_command` from the import list at the top of the file
  2. Remove all `command_command` test functions that call the handler with a `SkillLoader` mock (`_mock_skill_loader`) — these are incompatible with the new `CommandLoader` architecture
  - Verify the full suite is green after cleanup: `uv run pytest tests/chat/test_commands.py -v`
- **Releasable**: test suite passes; ready to begin TDD for FEAT-017
- **Tests (TDD)**: N/A — this is cleanup only; no new tests are added
- Checkpoint: `uv run pytest tests/chat/test_commands.py -v`

---

### Phase 1 — CommandLoader
> **Releasable**: after Task 1.1 — `CommandLoader` is independently testable and usable

#### Task 1.1 — CommandLoader: discover commands from global and project dirs
- [ ] **File**: `archon/chat/command_loader.py`
- **Depends on**: Task 0.1 (stale test cleanup)
- **Description**:
  - `CommandInfo` dataclass: `name: str`, `source: Literal["global", "project"]`
  - `CommandLoader.__init__(self, global_dir: Path | None = None, project_dir: Path | None = None) -> None`
    - `global_dir` defaults to `Path.home() / ".claude" / "commands"`
    - `project_dir` defaults to `None` (no project commands)
  - `CommandLoader.load_all(self) -> list[CommandInfo]`
    - Scans each non-None, existing directory for `*.md` files (flat, no recursion)
    - Filename stems that do not match `^[a-zA-Z0-9_-]+$` are silently excluded
    - **Collision rule**: if the same name exists in both directories, only the project entry is returned (global suppressed)
    - Returns globals first (sorted alphabetically by name), then project commands (sorted alphabetically by name)
    - Missing or non-existent directories silently skipped (no exception)
    - Does NOT cache — rescans filesystem on every call
  - `CommandLoader.exists(self, name: str) -> bool`
    - Sanitizes `name` before filesystem lookup: must match `^[a-zA-Z0-9_-]+$`; returns `False` immediately for any name that fails validation (no error raised)
    - Returns `True` if `<name>.md` is found in either directory (collision irrelevant)
- **Releasable**: `CommandLoader` can be imported and used independently
- **Tests (TDD)** — `tests/chat/test_command_loader.py`:
  - Unit: `test_load_all_global_only` — tmp dir with two `.md` files, no project dir → two global CommandInfo entries
  - Unit: `test_load_all_project_only` — no global dir, tmp project dir → two project entries
  - Unit: `test_load_all_both_dirs` — both dirs with distinct files → all entries, globals first
  - Unit: `test_load_all_empty_dirs` — dirs exist but are empty → returns `[]`
  - Unit: `test_load_all_missing_dirs` — paths do not exist → returns `[]`, no exception
  - Unit: `test_load_all_ignores_non_md` — `.txt` and `.yaml` files in dir → excluded
  - Unit: `test_load_all_sorted_within_source` — filenames out of alpha order → output is sorted A-Z by name within each source group
  - Unit: `test_load_all_collision_project_wins` — same name in both dirs → only project entry in result, global suppressed
  - Unit: `test_load_all_ignores_invalid_names` — files with invalid names (spaces, dots, slashes in stem) excluded from results
  - Unit: `test_exists_global` — `<name>.md` in global dir → `True`
  - Unit: `test_exists_project` — `<name>.md` in project dir → `True`
  - Unit: `test_exists_collision` — name in both dirs → `True`
  - Unit: `test_exists_false` — absent from both → `False`
  - Unit: `test_exists_rejects_path_traversal` — `exists("../../etc/passwd")` returns `False`
  - Checkpoint: `uv run pytest tests/chat/test_command_loader.py -v`

---

### Phase 2 — command_command handler
> **Releasable**: after Task 2.2 — fully wired and usable from Telegram

#### Task 2.1 — command_command handler in commands.py
- [ ] **File**: `archon/chat/commands.py`
- **Depends on**: Task 1.1
- **Description**:
  - Import `CommandLoader` from `archon.chat.command_loader`
  - Import `TruncationStrategy` from `archon.ai.truncation`
  - Add to `TYPE_CHECKING` block: `HistoryManager`, `AgentLogger`, `BackgroundAgentManager`
  - Import `DEFAULT_MAX_LEN` from `archon.chat.handler`. If that creates a circular import issue at implementation time, extract it to `archon/chat/constants.py` as the fallback — do not add a duplicate constant.
  - `async def command_command(message, command_loader, session_manager, truncation, max_len, notifications, cwd, history_manager, agent_logger, background_agent_manager) -> None`
  - **List mode** (no arg after `/command`):
    - Call `command_loader.load_all()`
    - Group into global (`source="global"`) and project (`source="project"`) lists
    - Build reply: `🌐 <b>Global commands:</b>` section if any globals; `📁 <b>Project commands:</b>` section if any project commands
    - Each entry: `• <code>/<name></code>`
    - If both lists empty: reply `"No commands available."`
  - **Execute mode** (arg present):
    - `parts = (message.text or "").split(maxsplit=2)` → `cmd = parts[1]`, `rest = parts[2] if len(parts) > 2 else ""`
    - `if not command_loader.exists(cmd)`: reply `f"❌ Command not found: <code>/{html.escape(cmd)}</code>"` and return
    - `if notifications is not None and notifications.mode != "quiet":` → `await message.answer(f"🔧 Running <code>/{cmd}</code>…")`
    - Build `prompt = f"/{cmd} {rest}".strip()`
    - Local import `from archon.chat.handler import handle_message`
    - `await handle_message(message=message, session_manager=..., truncation=..., max_len=..., notifications=..., cwd=..., history_manager=..., agent_logger=..., background_agent_manager=..., prompt_override=prompt)`
- **Releasable**: handler is callable and testable in isolation
- **Tests (TDD)** — `tests/chat/test_commands.py` (new section `# /command`):
  - Unit: `test_command_command_no_arg_lists_global_and_project` — mock loader returns both sources → reply contains 🌐 and 📁
  - Unit: `test_command_command_no_arg_only_global` — no project commands → only 🌐 section
  - Unit: `test_command_command_no_arg_only_project` — no global commands → only 📁 section
  - Unit: `test_command_command_no_arg_empty_replies_gracefully` — both empty → "No commands available"
  - Unit: `test_command_command_execute_valid_sends_running_notification` — valid cmd, normal mode → answer called with "🔧 Running"
  - Unit: `test_command_command_execute_quiet_no_running_notification` — valid cmd, quiet mode → running notification NOT sent
  - Unit: `test_command_command_execute_notifications_none_no_crash` — execute mode with `notifications=None`; mock `handle_message`; assert `message.answer` is NOT called before `handle_message` is called (the running notification is suppressed); assert `handle_message` is called with `notifications=None` forwarded
  - Unit: `test_command_command_execute_calls_handle_message` — mock `handle_message`; verify `prompt_override="/plan-maker plan.md help"`
  - Unit: `test_command_command_execute_no_extra_args_prompt` — `/command commit` → `prompt_override="/commit"`
  - Unit: `test_command_command_not_found_error` — unknown cmd → `❌ Command not found: /foo`
  - Unit: `test_command_command_not_found_escapes_html_in_cmd` — `/command <b>evil</b>` produces error reply with HTML-escaped command name, not rendered tags
  - Unit: `test_command_command_not_found_shown_in_quiet` — quiet mode does not suppress "not found" error
  - Unit: `test_command_command_passes_deps_to_handle_message` — notifications, cwd forwarded correctly
  - Checkpoint: `uv run pytest tests/chat/test_commands.py -k "command_command" -v`

#### Task 2.2 — Register command in bot.py and wire CommandLoader in gateway.py
- [ ] **File**: `archon/chat/bot.py`, `archon/gateway/gateway.py`
- **Depends on**: Task 2.1
- **Description**:
  - `bot.py`:
    - Add `command_command` to import from `archon.chat.commands`
    - Add `BotCommand(command="command", description="List or run a Claude Code command")` to `BOT_COMMANDS`
    - Register `dp.message.register(command_command, Command("command"))` in `create_dispatcher()`
    - Also register `dp.message.register(command_command, Command("commands"))` as a hidden alias (plural form, consistent with `/skills`, `/agents`, `/tasks`)
  - `gateway.py`:
    - Import `CommandLoader` from `archon.chat.command_loader`
    - In `_setup_dp()`: construct `CommandLoader(project_dir=Path(cfg.session.working_directory) / ".claude" / "commands")` (global_dir uses default)
    - Wire: `dp["command_loader"] = command_loader`
- **Releasable**: `/command` is fully operational from Telegram after this task
- **Tests (TDD)** — `tests/chat/test_bot.py` and `tests/gateway/test_gateway.py`:
  - Unit: `test_create_dispatcher_registers_command_command` — `Command("command")` handler present in dispatcher
  - Unit: `test_create_dispatcher_registers_commands_alias` — `Command("commands")` handler is also present in the dispatcher (plural alias)
  - Unit: `test_bot_commands_includes_command` — `BOT_COMMANDS` list contains entry with `command="command"`
  - Unit: `test_setup_dp_wires_command_loader` — `dp["command_loader"]` is a `CommandLoader` instance after `_setup_dp()`
  - Checkpoint: `uv run pytest tests/chat/test_bot.py tests/gateway/test_gateway.py -v`

---

### Phase 3 — Documentation
> **Releasable**: after this phase

#### Task 3.1 — Update CLAUDE.md and component catalog
- [ ] **File**: `CLAUDE.md`, `Documentation/Architecture/110_component_catalog_and_layer_breakdown.md`
- **Depends on**: Task 2.2
- **Description**:
  - `CLAUDE.md` commands table: add `/command` row with description "List or run a Claude Code command"
  - `110_component_catalog_and_layer_breakdown.md`: add `CommandLoader` to the `archon/chat/` module list with one-line description
- **Releasable**: documentation is current
- **Tests (TDD)**: N/A
- Checkpoint: N/A
