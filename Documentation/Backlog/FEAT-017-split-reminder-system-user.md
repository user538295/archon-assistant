# FEAT-017 — Split REMINDER.md into system and user reminder files
**Purpose**: Separate the Archon Control Plane MCP tool reference (system invariant) from the user-editable behavioral rules in `REMINDER.md`, preventing accidental deletion or modification of system-level instructions.
**Audience**: Archon maintainers; end-users who customise `workspace/REMINDER.md`.
**Status**: Done

---

## Background

`workspace/REMINDER.md` currently contains two logically distinct sections:

1. **User-tunable rules** — reasoning principles, communication style, "when you lack knowledge" steps. Users are expected to read and adapt these.
2. **Archon Control Plane** — a catalog of MCP tools the AI must use instead of shell commands (`archon_status`, `cancel_agent`, `send_notification`, etc.). This section is system-invariant: it is versioned with the codebase and must never be absent.

Mixing them in a single user-editable file means a user reorganising their REMINDER.md can silently delete the control-plane section, leaving the AI free to issue `launchctl`/`systemctl`/`kill` commands directly.

## Goal

Move the Archon Control Plane section into a versioned file (`archon/ai/prompts/system_reminder.md`) that lives in the codebase, never in the workspace. The Python injection code reads both files and merges them into a single XML-wrapped injection. Users continue editing only `workspace/REMINDER.md`. The control-plane instructions are always present and protected.

---

## Scope

### In Scope
- New file `archon/ai/prompts/system_reminder.md` containing the Archon Control Plane section extracted from `workspace/REMINDER.md`
- Remove the Archon Control Plane section from `workspace/REMINDER.md`
- Update `ContextReminder` and `build_reminder_injection` to merge system + user content
- Tests covering the new merge behaviour
- Update `archon/ai/prompts/` directory (already exists for classifier/decomposer prompts)

### Out of Scope
- Changing injection frequency, thresholds, or XML wrapper format
- Adding new control-plane tools or changing existing tool descriptions
- Making system reminder configurable or disableable

---

## Acceptance criteria
- [x] `workspace/REMINDER.md` no longer contains the Archon Control Plane section
- [x] `archon/ai/prompts/system_reminder.md` contains the Archon Control Plane section verbatim
- [x] Both files are merged into a single XML-wrapped injection (system content first, then user content)
- [x] If `workspace/REMINDER.md` is absent, only system content is injected (no crash)
- [x] If `archon/ai/prompts/system_reminder.md` is absent, only user content is injected with a warning (graceful degradation)
- [x] `build_reminder_injection` returns merged content from both files
- [x] `ContextReminder.build_reminder_message` returns merged content from both files
- [x] All existing tests pass
- [x] New tests cover merge, each-file-absent, and both-absent scenarios
- [x] All 4 control-plane safety tests pass after migration to assert against `archon/ai/prompts/system_reminder.md`

---

## What does NOT change
- XML wrapper format (`<system_reminder type="mandatory_context_refresh">`)
- Injection trigger thresholds (message/token counts)
- `ContextReminder` public interface signatures (`record_message`, `record_tokens`, `should_inject`, `build_reminder_message`) — `should_inject()` behaviour changes (now checks either file) but its signature does not change
- `build_reminder_injection` function signature
- Call sites in `claude_session.py`, `background_agent_manager.py`, `decomposer.py` — they pass `workspace_dir`; the system file path is resolved internally
- `session_manager.py` — `ContextReminder` is constructed there; no change needed if system path is resolved inside `ContextReminder.__init__`

---

## Known limitations / accepted trade-offs
- The system reminder path is hardcoded relative to `archon/ai/prompts/` (resolved via `Path(__file__).parent / "prompts" / "system_reminder.md"`). It is not configurable — this is intentional to prevent accidental disabling.
- Combined injection slightly increases per-injection token cost vs. user-only content. Acceptable: the control-plane section is small (~500 tokens).

---

## Architecture

### New file
- `archon/ai/prompts/system_reminder.md` — static Markdown, Archon Control Plane section only. Resolved in `reminder.py` via `_SYSTEM_REMINDER_FILE = Path(__file__).parent / "prompts" / "system_reminder.md"`.

### Modified: `archon/ai/reminder.py`
- `_SYSTEM_REMINDER_FILE: Path` — module-level constant, path to the versioned system reminder
  > **Test strategy**: All unit tests that need to control the system file path must use `mock.patch("archon.ai.reminder._SYSTEM_REMINDER_FILE", new=<tmp_path / "system_reminder.md">)` or use `pytest` `tmp_path` fixtures and patch the constant. No functional change to the code is needed — the module-level constant approach is correct and the patch is straightforward.
- `_merge_contents(system: str | None, user: str | None) -> str | None` — concatenates non-None, non-empty parts with a blank line separator
- `ContextReminder.build_reminder_message() -> str` — reads system file + existing `self._file`, merges, wraps; resets counters unconditionally
- `ContextReminder.should_inject() -> bool` — checks `self._file.exists() or _SYSTEM_REMINDER_FILE.exists()` (either file present) AND thresholds met; prevents silent breakage when user deletes their REMINDER.md
- `build_reminder_injection(workspace_dir: Path) -> str | None` — reads system file + `workspace_dir/REMINDER.md`, merges, wraps; returns `None` only when both are absent/empty

### Data flow (unchanged externally)
```
claude_session / background_agent_manager / decomposer
    → build_reminder_injection(workspace_dir)
        → reads _SYSTEM_REMINDER_FILE  (codebase)
        → reads workspace_dir/REMINDER.md  (user workspace)
        → merges → XML-wraps → returns str | None
```

---

## Tests

- **test_merge_both_present** (unit): both files present → merged content = system + "\n\n" + user, wrapped
- **test_merge_system_only** (unit): user file absent → only system content wrapped, no crash
- **test_merge_user_only** (unit): system file absent → only user content wrapped, warning logged
- **test_merge_both_absent** (unit): both absent → returns None
- **test_merge_empty_user** (unit): user file is whitespace-only → only system content injected
- **test_build_reminder_injection_merged** (unit): `build_reminder_injection` returns merged XML; assert `result.index(system_line) < result.index(user_line)` to verify system content precedes user content
- **test_context_reminder_build_message_merged** (unit): `ContextReminder.build_reminder_message` returns merged XML
- **test_system_reminder_file_exists** (unit): `_SYSTEM_REMINDER_FILE` resolves to an existing file (smoke — catches accidental deletion of the file from the package)

---

## Documentation update
- [x] `CLAUDE.md`, section "Architecture > archon/ai/", path: `CLAUDE.md` — update `reminder.py` bullet to mention `system_reminder.md`
- [x] `workspace/REMINDER.md` — remove Archon Control Plane section

---

## Task breakdown

### Phase 1 — Extract and wire system reminder
> **Releasable**: after Task 1.3 — both files injected correctly in all contexts

#### Task 1.1 — Create `archon/ai/prompts/system_reminder.md`
- [x] **File**: `archon/ai/prompts/system_reminder.md`
- **Depends on**: nothing
- **Description**:
  - Move the `## Archon Control Plane` section (from the `## Archon Control Plane` heading to end of file) verbatim into this new file
  - File starts with `## Archon Control Plane` — no top-level `# Standing Rules` heading (that belongs to the user file)
  - Do not modify tool names, descriptions, or structure in any way
- **Releasable**: file exists and contains control-plane content; not yet wired
- **Tests (TDD)** — `tests/ai/test_reminder.py`:
  - Unit: `test_system_reminder_file_exists` — `Path(reminder.__file__).parent / "prompts" / "system_reminder.md"` resolves to an existing, non-empty file
  - Checkpoint: `uv run pytest tests/ai/test_reminder.py::test_system_reminder_file_exists -v`

#### Task 1.2 — Add `_merge_contents` helper and `_SYSTEM_REMINDER_FILE` constant to `reminder.py`
- [x] **File**: `archon/ai/reminder.py`
- **Depends on**: Task 1.1
- **Description**:
  - `_SYSTEM_REMINDER_FILE: Path = Path(__file__).parent / "prompts" / "system_reminder.md"` — module-level constant
  - `_read_file_safe(path: Path) -> str | None` — reads file, returns `None` on `OSError` or if `content.strip()` is empty; returns the **raw unstripped content** (same as `file.read_text()`); logs `WARNING` on `OSError`
  - `_merge_contents(system: str | None, user: str | None) -> str | None` — returns `None` if both `None`; concatenates non-None parts separated by `"\n\n"`; never returns empty string
  - No changes to `ContextReminder` or `build_reminder_injection` yet
- **Releasable**: helpers available for use in Tasks 1.3
- **Tests (TDD)** — `tests/ai/test_reminder.py`:
  - Unit: `test_merge_both_present` — both non-None → joined with `"\n\n"`
  - Unit: `test_merge_system_only` — user is `None` → returns system content unchanged
  - Unit: `test_merge_user_only` — system is `None` → returns user content unchanged
  - Unit: `test_merge_both_none` — both `None` → returns `None`
  - Unit: `test_read_file_safe_missing` — non-existent path → returns `None`, logs warning
  - Unit: `test_read_file_safe_whitespace` — whitespace-only content → returns `None`
  - Unit: `test_read_file_safe_valid` — valid file → returns raw content (not stripped)
  - Checkpoint: `uv run pytest tests/ai/test_reminder.py -k "merge or read_file_safe" -v`

#### Task 1.3 — Update `ContextReminder.build_reminder_message`, `should_inject`, and `build_reminder_injection` to merge both files
- [x] **File**: `archon/ai/reminder.py`
- **Depends on**: Task 1.2
- **Description**:
  - `ContextReminder.should_inject()`:
    - Update to return `True` if (`self._file.exists() or _SYSTEM_REMINDER_FILE.exists()`) AND the message/token thresholds are met. This prevents periodic re-injection from silently breaking when the user deletes their `workspace/REMINDER.md` while the system file is still present.
  - `ContextReminder.build_reminder_message()`:
    - Replace `self.read_and_wrap(self._file)` with: read system file via `_read_file_safe(_SYSTEM_REMINDER_FILE)`, read user file via `_read_file_safe(self._file)`, merge via `_merge_contents`, wrap result; if merged is `None` wrap empty string (preserving existing fallback behaviour)
    - Counters (`_message_count`, `_token_count`) are reset unconditionally in all code paths, same as current behaviour — including when merge returns `None`
    - Log combined char count and approximate token count at `INFO`
    - `_read_file_safe` already logs `WARNING` on `OSError` — do NOT add a second warning in `build_reminder_message` or `build_reminder_injection` for system file absence. The `INFO` log for combined char count is sufficient at the caller level.
  - `build_reminder_injection(workspace_dir: Path) -> str | None`:
    - Read system file via `_read_file_safe(_SYSTEM_REMINDER_FILE)`
    - Read user file via `_read_file_safe(workspace_dir / "REMINDER.md")`
    - Merge; if `None` return `None`
    - Wrap with `_XML_PREFIX` / `_XML_SUFFIX`
    - Log combined size; warn if over `_REMINDER_SIZE_WARNING_CHARS`
  - Remove the now-dead `read_and_wrap` staticmethod (was only used in `build_reminder_message`)
  - Note: ALL unit tests for `ContextReminder` and `build_reminder_injection` must patch `archon.ai.reminder._SYSTEM_REMINDER_FILE` to a `tmp_path` location (absent by default) to avoid picking up real package content. Only tests explicitly covering system file scenarios should create that file in `tmp_path`.
  - **Existing tests that must be updated:**
    - `test_file_absent` — tests `should_inject()` returning `False` when user file is absent. After this change, `should_inject()` must return `True` when the system file exists (even if user file is absent). **Rewrite** this test to cover two cases: (a) user absent + system present → `True`; (b) both absent → `False`. This replaces the single `test_should_inject_system_only_no_user_file` test already listed above — consolidate them.
    - `test_build_reminder_injection_file_missing` — tests `build_reminder_injection()` returning `None` when user file is absent. After this change, the function returns system-only XML instead. **Rewrite** to assert non-None with system content when only system file is present. Add `test_both_files_absent_returns_none` for the all-absent case.
  - **Existing tests referencing `read_and_wrap` — must be deleted or rewritten as part of this task**:
    - `test_read_and_wrap_returns_xml_wrapped_content` — **delete** (method removed)
    - `test_build_reminder_message_delegates_to_read_and_wrap` — **rewrite** to assert `build_reminder_message()` returns merged XML (replace with `test_context_reminder_build_message_merged`)
    - `test_build_reminder_message_handles_permission_error` — **rewrite** to patch `_read_file_safe` instead of `read_and_wrap`
- **Releasable**: both injection paths emit merged system+user content
- **Tests (TDD)** — `tests/ai/test_reminder.py`:
  - Unit: `test_build_reminder_injection_merged` — both files present → XML contains both sections; assert `result.index(system_line) < result.index(user_line)` to verify system content precedes user content
  - Unit: `test_build_reminder_injection_user_absent` — only system file present → XML contains system section only
  - Unit: `test_build_reminder_injection_system_absent` — only user file present → XML contains user section only, warning logged
  - Unit: `test_build_reminder_injection_both_absent` — neither file present → returns `None`
  - Unit: `test_context_reminder_build_message_merged` — `ContextReminder.build_reminder_message` returns merged XML
  - Unit: `test_context_reminder_build_message_system_absent` — system file missing → user-only XML, warning logged
  - Unit: `test_should_inject_system_only_no_user_file` — user file absent, system file present, thresholds exceeded → `should_inject()` returns `True`
  - Unit: `test_context_reminder_counters_reset_on_none_merge` — both files absent → counters are still reset to zero after calling `build_reminder_message()`
  - Unit: `test_build_reminder_message_handles_curly_braces_in_system_content` — system file contains curly braces `{}` → no format-string error, content preserved in output
  - Checkpoint: `uv run pytest tests/ai/test_reminder.py -v`

### Phase 2 — Remove control-plane section from user file
> **Releasable**: after Task 2.1 — `workspace/REMINDER.md` no longer contains system content

#### Task 2.1 — Remove Archon Control Plane section from `workspace/REMINDER.md`
- [x] **File**: `workspace/REMINDER.md`
- **Depends on**: Task 1.3
- **Description**:
  - Delete the `## Archon Control Plane` section (from the `## Archon Control Plane` heading to end of file)
  - Retain all content above that heading exactly as-is
  - No other modifications
  - **Migrate control-plane safety tests**: Update the four existing tests to assert against `archon/ai/prompts/system_reminder.md` instead of `workspace/REMINDER.md`:
    - `test_reminder_contains_control_plane_section`
    - `test_reminder_lists_mcp_tools`
    - `test_reminder_forbids_shell_commands`
    - `test_reminder_lists_all_tools`
    - These tests preserve the bidirectional ArchonToolkit drift guard — do NOT delete them.
- **Releasable**: user file contains only user-tunable content; system content served from `archon/ai/prompts/system_reminder.md`
- **Tests (TDD)** — `tests/ai/test_reminder.py`:
  - Unit: `test_workspace_reminder_no_control_plane` — reads `workspace/REMINDER.md`, asserts `"## Archon Control Plane"` is not present
  - All 4 control-plane safety tests (`test_reminder_contains_control_plane_section`, `test_reminder_lists_mcp_tools`, `test_reminder_forbids_shell_commands`, `test_reminder_lists_all_tools`) pass after being updated to assert against `archon/ai/prompts/system_reminder.md`
  - Checkpoint: `uv run pytest tests/ai/test_reminder.py -v`

### Phase 3 — Documentation
> **Releasable**: after Task 3.1

#### Task 3.1 — Update CLAUDE.md reminder.py bullet
- [x] **File**: `CLAUDE.md`
- **Depends on**: Task 2.1
- **Description**:
  - In the `archon/ai/` module list, update the `reminder.py` bullet to mention `system_reminder.md`:
    > `reminder.py`: `ContextReminder` — periodic injection of `REMINDER.md` to prevent context drift; merges versioned `archon/ai/prompts/system_reminder.md` (Archon Control Plane) with `workspace/REMINDER.md` (user rules)
- **Releasable**: documentation accurate
- **Tests (TDD)**: N/A (documentation only)
- **Checkpoint**: N/A
