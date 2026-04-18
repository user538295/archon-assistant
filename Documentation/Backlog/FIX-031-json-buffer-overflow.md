# FIX-031 — JSON Buffer Overflow on Large File Read

**Purpose**: Prevent the Claude Agent SDK from crashing the active session when the LLM reads a file whose JSON-encoded tool result exceeds the 1 MB transport buffer limit.
**Audience**: All Archon users — any session where Claude reads a large file will crash without this fix.
**Status**: To Do

---

## Background

When the LLM reads a file ≥ ~500 KB, the SDK's `_read_messages_impl()` accumulates the JSON-encoded tool result in a buffer. Once the buffer exceeds `_DEFAULT_MAX_BUFFER_SIZE = 1 MB`, it raises `CLIJSONDecodeError`, which propagates as an exception through `receive_response()` → `claude_session.py` → `handler.py`. The user sees `❌ Error: Failed to decode JSON: JSON message exceeded maximum buffer size of 1048576 bytes`, the in-flight request is lost, and the cascading lock release can trigger the Bug 9 stuck-session pattern.

See full investigation: `Documentation/Backlog/bug_investigation_08_json_buffer_overflow.md`.

## Goal

After this fix, reading a file up to 5 MB succeeds (SDK buffer raised to 10 MB). Attempting to read a file larger than 5 MB via the Read tool is intercepted before execution and returns an actionable error to the LLM. The system prompt also instructs the LLM to avoid reading large binaries directly. All three mitigations are layered; no single one is a complete guarantee on its own.

---

## Scope

### In Scope
- Adding `max_buffer_size=10 * 1024 * 1024` to every `ClaudeAgentOptions` construction site in Archon (`claude_session.py`, `history_compactor.py`, `description_generator.py`)
- Adding a large-file warning to `archon/ai/prompts/system_reminder.md`
- Implementing a PreToolUse hook on `ClaudeSession` that rejects `Read` tool calls for files > 5 MB (with the 10 MB buffer), returning an actionable error message to the LLM

### Out of Scope
- Bash tool interception — parsing arbitrary shell commands to extract file paths is not tractable; the raised buffer is the mitigation for Bash-triggered overflows
- SDK patching / venv modification — `max_buffer_size` is a first-class `ClaudeAgentOptions` parameter; no patching required
- Streaming/chunked file reads (Option D) — requires SDK-level changes outside Archon's control
- `event_mapper.py` error-dict handling (Option C) — the SDK raises an exception, not a dict; `event_mapper.py` never sees it

---

## Acceptance criteria
- [ ] Reading a 1.1 MB file no longer crashes the session; error is displayed and the session remains usable
- [ ] `ClaudeAgentOptions` is constructed with `max_buffer_size=10 * 1024 * 1024` in all three construction sites
- [ ] A Read tool call on a file > 5 MB is denied by the hook with message: `"File too large (N MB > 5 MB limit). Use head/tail/grep for targeted extraction."`
- [ ] A Read tool call on a file ≤ 5 MB is not affected by the hook
- [ ] A stat failure (missing file, permission error) in the hook allows the tool call through (fail-open)
- [ ] Non-Read tool calls are not affected by the hook
- [ ] The system reminder includes a warning against reading files > 500 KB directly
- [ ] All existing tests pass; new tests at ≥ 85% coverage

---

## What does NOT change
- `event_mapper.py` — no changes; the exception never reaches it
- `handler.py` — already catches `Exception` correctly; no changes needed
- `ClaudeSession` public API (`start`, `stop`, `send`, `usage_stats`) — unchanged
- `history_compactor.py` and `description_generator.py` — only the `ClaudeAgentOptions` constructor call is touched; no other behavior changes
- The `can_use_tool` callback — not used (requires `AsyncIterable` prompt mode, incompatible with Archon's string-prompt pattern)

---

## Known limitations / accepted trade-offs
- **Bash tool is an open vector**: A `Bash` command running `cat` on a file > 5 MB can still trigger the buffer overflow. The 10 MB buffer raises the practical ceiling; a complete fix for Bash would require parsing arbitrary shell commands (not tractable).
- **`bypassPermissions` + hook interaction is unverified**: The behaviour of PreToolUse hooks when `permission_mode="bypassPermissions"` is not documented. Verify empirically before release (see Task 3.2 live test).
- **10 MB is still a finite ceiling**: Files between 5–10 MB with high JSON-escaping overhead could still exceed the buffer. The hook prevents the most likely cases; extreme edge cases are acknowledged.
- **Option E is probabilistic**: Prompt guidance reduces frequency but the LLM may still ignore it.

---

## Architecture

### New: `_read_tool_size_hook` in `archon/ai/claude_session.py`

A module-level async `HookCallback` function. Registered on `ClaudeAgentOptions.hooks` as a `PreToolUse` hook scoped to the `"Read"` tool matcher.

```python
_LARGE_FILE_HOOK_THRESHOLD = 5 * 1024 * 1024  # 5 MB (with 10 MB SDK buffer)

async def _read_tool_size_hook(
    hook_input: HookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> HookJSONOutput:
    ...
```

Returns `SyncHookJSONOutput(hookSpecificOutput=PreToolUseHookSpecificOutput(...))` on deny; `SyncHookJSONOutput()` on allow or stat failure (fail-open).

### Modified: `ClaudeAgentOptions` construction in three files

Every `ClaudeAgentOptions(...)` call gains `max_buffer_size=10 * 1024 * 1024`. Only `claude_session.py`'s call also gains the `hooks` parameter.

### Modified: `archon/ai/prompts/system_reminder.md`

A new "File size limits" section added with guidance to check file size before reading and to use `head`/`tail`/`grep` for files > 500 KB.

### Imports added to `claude_session.py`

```python
from claude_agent_sdk.types import (
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
    PreToolUseHookInput,
    PreToolUseHookSpecificOutput,
    SyncHookJSONOutput,
)
```

`HookEvent` is `Literal["PreToolUse"]` — used as a dict key literal, not imported separately.

---

## Tests

- **test_system_reminder_contains_file_size_warning** (unit): verifies the reminder text includes the 500 KB guidance
- **test_buffer_overflow_exception_propagates_to_user** (unit): mock `receive_response()` to raise `CLIJSONDecodeError`; verify `❌ Error` delivered, `send_lock` released, session reusable
- **test_buffer_overflow_send_lock_released** (unit): session remains unlocked after `CLIJSONDecodeError`
- **test_max_buffer_size_set_on_claude_session** (unit): `ClaudeAgentOptions` receives `max_buffer_size=10 * 1024 * 1024` in `claude_session.py`
- **test_max_buffer_size_set_on_history_compactor** (unit): same for `history_compactor.py`
- **test_max_buffer_size_set_on_description_generator** (unit): same for `description_generator.py`
- **test_read_tool_hook_allows_small_file** (unit): file ≤ 5 MB → `SyncHookJSONOutput()` (allow)
- **test_read_tool_hook_denies_large_file** (unit): file > 5 MB → deny with correct message
- **test_read_tool_hook_fail_open_on_missing_file** (unit): stat raises `OSError` → allow
- **test_read_tool_hook_fail_open_on_empty_path** (unit): empty `file_path` → allow
- **test_read_tool_hook_ignores_non_read_tool** (unit): `hook_input.hook_event_name != "PreToolUse"` or wrong tool → allow
- **test_hook_registered_on_claude_agent_options** (unit): `ClaudeAgentOptions.hooks` contains `"PreToolUse"` key with `HookMatcher(matcher="Read", hooks=[_read_tool_size_hook])`
- **test_read_tool_hook_bypassPermissions_invoked** (live e2e): verify hook is called when `permission_mode="bypassPermissions"` — confirms `bypassPermissions` does not suppress hook invocations

---

## Documentation update
- [ ] `Documentation/Backlog/bug_investigation_08_json_buffer_overflow.md`, section: Recommendation — mark Options E/B/A as implemented once tasks complete

---

## Task breakdown

### Phase 1 — Prompt guidance
> **Releasable**: after Task 1.1; deployable independently as defence-in-depth.

#### Task 1.1 — Add large-file warning to system_reminder.md
- [x] **File**: `archon/ai/prompts/system_reminder.md`
- **Depends on**: nothing
- **Description**:
  - Append a new section titled `## File size limits` at the end of the file (before any trailing newline)
  - Content:
    ```
    ## File size limits

    Before reading any file, check its size: `wc -c <file>` or `stat <file>`.
    Never use the Read tool on files larger than 500 KB — it will crash the session.
    For large files, use `head -n 100`, `tail`, or `grep` to extract only what you need.
    ```
  - Do not alter any existing content
- **Releasable**: after this task, the LLM is instructed to avoid large file reads on every session that injects the reminder
- **Tests (TDD)** — `tests/ai/test_prompts.py`:
  - Unit: `test_system_reminder_contains_file_size_warning` — load `system_reminder.md` via `load_prompt("system_reminder")` (or direct file read); assert the string `"500 KB"` and `"Read tool"` appear in the content
  - Checkpoint: `uv run pytest tests/ai/test_prompts.py -k "file_size_warning" -v`

---

### Phase 2 — SDK buffer increase
> **Releasable**: after Task 2.1; files up to ~5 MB no longer crash the session.

#### Task 2.1 — Add `max_buffer_size` to all `ClaudeAgentOptions` construction sites
- [x] **File**: `archon/ai/claude_session.py`, `archon/ai/history_compactor.py`, `archon/search/description_generator.py`
- **Depends on**: nothing (independent of Phase 1)
- **Description**:
  - **`archon/ai/claude_session.py` line ~199**: add `max_buffer_size=10 * 1024 * 1024` to the `ClaudeAgentOptions(...)` call. No other changes to this call in this task (hook wiring is Task 3.2).
  - **`archon/ai/history_compactor.py` line ~112**: add `max_buffer_size=10 * 1024 * 1024` to `ClaudeAgentOptions(...)`.
  - **`archon/search/description_generator.py` line ~85**: add `max_buffer_size=10 * 1024 * 1024` to `ClaudeAgentOptions(...)`.
  - No other changes to any of these files.
  - The constant value `10 * 1024 * 1024` is written inline at each call site — no shared constant needed (three sites, all in different modules).
- **Releasable**: after this task, the SDK buffer is 10 MB across all session types; files up to ~5 MB are handled without crashing
- **Tests (TDD)** — `tests/ai/test_claude_session.py`, `tests/ai/test_history_compactor.py`, `tests/search/test_description_generator.py`:
  - Unit: `test_max_buffer_size_set_on_claude_session` — patch `ClaudeAgentOptions` in `claude_session.py`; call `session.start()`; assert constructor was called with `max_buffer_size=10 * 1024 * 1024` (`tests/ai/test_claude_session.py`)
  - Unit: `test_max_buffer_size_set_on_history_compactor` — patch `ClaudeAgentOptions` in `history_compactor.py`; trigger `_get_client()`; assert `max_buffer_size=10 * 1024 * 1024` (`tests/ai/test_history_compactor.py`)
  - Unit: `test_max_buffer_size_set_on_description_generator` — patch `ClaudeAgentOptions` in `description_generator.py`; call `_call_haiku("prompt")`; assert `max_buffer_size=10 * 1024 * 1024` (`tests/search/test_description_generator.py`)
  - Checkpoint: `uv run pytest tests/ai/test_claude_session.py tests/ai/test_history_compactor.py tests/search/test_description_generator.py -k "max_buffer_size" -v`

---

### Phase 3 — PreToolUse hook for Read tool file size gating
> **Releasable**: after Task 3.2; Read tool calls on files > 5 MB are intercepted and return an actionable error.

#### Task 3.1 — Implement `_read_tool_size_hook` in `claude_session.py`
- [ ] **File**: `archon/ai/claude_session.py`
- **Depends on**: nothing (can be implemented alongside Task 2.1)
- **Description**:
  - Add module-level constant: `_LARGE_FILE_HOOK_THRESHOLD = 5 * 1024 * 1024  # 5 MB`
  - Add the following imports at the top of the file (alongside existing `claude_agent_sdk` imports):
    ```python
    from claude_agent_sdk.types import (
        HookContext,
        HookInput,
        HookJSONOutput,
        HookMatcher,
        PreToolUseHookInput,
        PreToolUseHookSpecificOutput,
        SyncHookJSONOutput,
    )
    ```
  - Implement module-level async function:
    ```python
    async def _read_tool_size_hook(
        hook_input: HookInput,
        tool_use_id: str | None,
        context: HookContext,
    ) -> HookJSONOutput:
        """PreToolUse hook: deny Read calls on files exceeding _LARGE_FILE_HOOK_THRESHOLD."""
        if not isinstance(hook_input, PreToolUseHookInput):
            return SyncHookJSONOutput()
        file_path: str = hook_input.tool_input.get("file_path", "")
        try:
            size = os.path.getsize(file_path)
        except (OSError, TypeError, ValueError):
            return SyncHookJSONOutput()  # fail-open: cannot stat — allow
        if size > _LARGE_FILE_HOOK_THRESHOLD:
            limit_mb = _LARGE_FILE_HOOK_THRESHOLD // (1024 * 1024)
            actual_mb = size // (1024 * 1024)
            return SyncHookJSONOutput(
                hookSpecificOutput=PreToolUseHookSpecificOutput(
                    hookEventName="PreToolUse",
                    permissionDecision="deny",
                    permissionDecisionReason=(
                        f"File too large ({actual_mb} MB > {limit_mb} MB limit). "
                        "Use head/tail/grep for targeted extraction instead of reading the whole file."
                    ),
                )
            )
        return SyncHookJSONOutput()
    ```
  - No wiring in this task — function is standalone and testable in isolation
- **Releasable**: after this task, the hook function is testable; not yet active (wired in Task 3.2)
- **Tests (TDD)** — `tests/ai/test_claude_session.py`:
  - Unit: `test_read_tool_hook_allows_small_file` — create a real temp file < 5 MB; call `_read_tool_size_hook(PreToolUseHookInput(hook_event_name="PreToolUse", tool_name="Read", tool_input={"file_path": str(tmp_path)}), None, {})` ; assert result is `SyncHookJSONOutput()` (allow)
  - Unit: `test_read_tool_hook_denies_large_file` — create a real temp file > 5 MB (or mock `os.path.getsize` to return `6 * 1024 * 1024`); assert `permissionDecision == "deny"` and `permissionDecisionReason` contains `"5 MB limit"`
  - Unit: `test_read_tool_hook_fail_open_on_missing_file` — pass a path that does not exist; assert allow (no deny)
  - Unit: `test_read_tool_hook_fail_open_on_empty_path` — pass `file_path=""` or `file_path=None`; assert allow
  - Unit: `test_read_tool_hook_ignores_non_read_tool` — construct a non-`PreToolUseHookInput` object or a `PreToolUseHookInput` for a different tool; assert allow
  - Checkpoint: `uv run pytest tests/ai/test_claude_session.py -k "read_tool_hook" -v`

#### Task 3.2 — Wire `_read_tool_size_hook` into `ClaudeAgentOptions`
- [ ] **File**: `archon/ai/claude_session.py`
- **Depends on**: Task 2.1 (max_buffer_size already added), Task 3.1 (hook function exists)
- **Description**:
  - In `ClaudeSession.start()` at line ~199, add `hooks={"PreToolUse": [HookMatcher(matcher="Read", hooks=[_read_tool_size_hook])]}` to the `ClaudeAgentOptions(...)` call (alongside the `max_buffer_size` added in Task 2.1):
    ```python
    options = ClaudeAgentOptions(
        ...
        max_buffer_size=10 * 1024 * 1024,
        hooks={"PreToolUse": [HookMatcher(matcher="Read", hooks=[_read_tool_size_hook])]},
    )
    ```
  - No changes to `history_compactor.py` or `description_generator.py` — these sessions don't process user file requests, so the hook is not needed there
  - No other changes
- **Releasable**: after this task, every `ClaudeSession` intercepts Read calls on files > 5 MB
- **Tests (TDD)** — `tests/ai/test_claude_session.py`:
  - Unit: `test_hook_registered_on_claude_agent_options` — patch `ClaudeAgentOptions`; call `session.start()`; assert the captured `hooks` kwarg equals `{"PreToolUse": [HookMatcher(matcher="Read", hooks=[_read_tool_size_hook])]}` (compare by matcher string and hook identity)
  - Unit: `test_buffer_overflow_exception_propagates_to_user` — mock `ClaudeSDKClient.receive_response()` to raise `CLIJSONDecodeError("buffer exceeded")`; call `handler.handle_message()`; assert `❌ Error` message was sent to Telegram
  - Unit: `test_buffer_overflow_send_lock_released` — same setup; after the exception, assert `session._send_lock.locked() == False` and a second `send()` call completes without deadlock
  - Live E2E: `test_read_tool_hook_bypassPermissions_invoked` — start a real `ClaudeSession` with a hook that records invocations; ask the session to read a small file; assert the hook was called — confirms `bypassPermissions` does not suppress hook invocations (`tests/ai/test_claude_session_live.py`)
  - Checkpoint: `uv run pytest tests/ai/test_claude_session.py -k "hook_registered or buffer_overflow" -v`
