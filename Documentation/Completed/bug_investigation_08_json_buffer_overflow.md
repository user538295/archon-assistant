# Bug Investigation: JSON Buffer Overflow on Large File Read

**Date**: 2026-04-17  
**Severity**: High (user-facing crash with data loss)  
**Error**: `❌ Error: Failed to decode JSON: JSON message exceeded maximum buffer size of 1048576 bytes`  
**Context**: LLM read `/tmp/stanford_rag.pdf` (1.1 MB), crashing the session mid-stream.

---

## Root Cause

The Claude Agent SDK (version 0.1.39) has a **hardcoded 1 MB (1,048,576 bytes) buffer limit** for JSON line messages:

**File**: `.venv/lib/python3.12/site-packages/claude_agent_sdk/_internal/transport/subprocess_cli.py`  
**Line 28**: `_DEFAULT_MAX_BUFFER_SIZE = 1024 * 1024  # 1MB buffer limit`  
**Failure point**: Lines ~546–554 in `_read_messages_impl()` — buffer size is checked on each accumulated chunk

When the LLM reads a file >~500 KB (see threshold reasoning in Option A):
1. Claude Code CLI loads the file and returns it as a JSON tool result
2. SDK's `_read_messages_impl()` accumulates the JSON in its buffer
3. Buffer exceeds 1,048,576 bytes → raises `SDKJSONDecodeError` with "Fatal error in message reader"
4. The exception propagates out of `receive_response()` in `claude_session.py`
5. `handler.py` lines ~409–411 catches the exception in its `except` clause and sends `❌ Error` to the user

> **Note**: There is no silent drop. The error is an exception raised by the SDK, not a dict yielded through the event stream. `event_mapper.py` never sees it.

### App Log Evidence (20:41:24 UTC)
```
ERROR Fatal error in message reader: Failed to decode JSON: JSON message exceeded maximum buffer size of 1048576 bytes...
ERROR Error processing message for user 154643621 (Exception)
```
The session recovered (send lock released in `claude_session.py` `finally` block ~line 364–419), but the active request's data was lost and triggered Bug 9's cascade.

> **Bug 9 cross-reference**: Bug 9 (`bug_investigation_09`) describes how a stuck session causes subsequent requests to pile up behind the `send_lock`. A buffer overflow that releases the lock mid-stream leaves the session in an intermediate state that can trigger that stuck-session behaviour.

---

## Why It Happens

The SDK uses a JSON-line protocol: each message is one JSON line, terminated by `\n`. Tool results (file contents) are embedded as strings in that JSON. A 1.1 MB PDF file becomes a JSON string of ~1.5 MB (with escaping) — far exceeding the buffer.

This is a fundamental constraint of the current SDK transport design: **all tool output must fit in one JSON line under 1 MB**.

> **Note**: This vulnerability is not limited to the Read tool. Any tool that returns large output — `Bash` running `cat` on a large file, search results, etc. — can trigger the same buffer overflow if the JSON-encoded result exceeds ~1 MB.

---

## Affected Files

**SDK (in venv — read-only):**
- `claude_agent_sdk/_internal/transport/subprocess_cli.py` line 28, 546–554
- `claude_agent_sdk/_internal/query.py` lines ~249, 256

**Archon:**
- `archon/ai/claude_session.py` lines ~334, 339, 364–419 — exception propagation and session recovery
- `archon/chat/handler.py` lines ~409–411 — error display

---

## Options

### Option A: Pre-flight file size check (short-term)
Intercept Read tool calls before they execute. If the target file is >500 KB (standalone, without Option B) or >5 MB (with Option B in place), return a clear error to the LLM instead of attempting to read it.

**Threshold reasoning**: The SDK buffer is 1 MB. Typical JSON string escaping adds 30–100% overhead for mixed or binary content, meaning a 500–700 KB file can easily produce a ~1 MB JSON line. A 500 KB threshold provides a safe margin **when deployed standalone (without Option B)**. If Option B is also applied (raising the buffer to 10 MB), the threshold should be raised to **5 MB** to account for ~2x JSON overhead on the larger buffer — rejecting files that Option B could handle fine would be incoherent.

**Primary viable implementation — SDK PreToolUse Hooks**: The Claude Agent SDK defines `PreToolUseHookInput` (fields: `tool_name`, `tool_input`) and `PreToolUseHookSpecificOutput` (field: `permissionDecision: "deny"`, `permissionDecisionReason`) in `types.py`. PreToolUse hooks are invoked before each tool execution, do not require the prompt to be an `AsyncIterable`, and work with Archon's existing string-prompt pattern. Archon can register a PreToolUse hook in `claude_session.py` that:
1. Checks `tool_name == "Read"`
2. Extracts the file path from `hook_input.tool_input` (available on `PreToolUseHookInput`; contains the Read tool's arguments, e.g. `file_path` field). The callback signature is `(hook_input: HookInput, tool_use_id: str | None, context: HookContext) -> Awaitable[HookJSONOutput]` — the `hook_input` union type narrows to `PreToolUseHookInput` here because the `HookMatcher` already filters to the Read tool.
3. Stats the file — if size exceeds the threshold, returns a denial wrapped as:
   ```python
   SyncHookJSONOutput(
       hookSpecificOutput=PreToolUseHookSpecificOutput(
           hookEventName="PreToolUse",
           permissionDecision="deny",
           permissionDecisionReason="File too large (>N MB) — use head/tail/grep for targeted extraction"
       )
   )
   ```
4. If the stat fails (file does not exist), fails open (allow)

> **Note**: the interaction between `bypassPermissions` mode and hook invocations is unverified — confirm that hooks are invoked when `permission_mode='bypassPermissions'` before implementing.

**Scope: Read tool only.** Bash tool calls that read large files (e.g., `cat largefile`) remain an open vector — parsing arbitrary shell commands to extract file paths is not tractable. Option B's raised buffer limit is the primary mitigation for that case.

**Simpler fallback — prompt guidance**: Add a note to `archon/ai/prompts/system_reminder.md` instructing the LLM to check file size first (`wc -c <file>`) and avoid reading files larger than 500 KB. This has zero code cost but is unreliable — the LLM may ignore it.

> **Note**: A wrapper around Claude Code's built-in Read tool via Archon's MCP layer is not possible — Archon's MCP servers only expose Archon-specific control-plane tools and do not wrap Claude Code's built-in tools. A check in `claude_session.py` before forwarding tool results is also not viable — Archon does not see tool calls before the CLI executes them.

> **`can_use_tool` callback is NOT viable for Archon**: The `can_use_tool` callback on `ClaudeAgentOptions` explicitly requires the prompt to be an `AsyncIterable`. Archon currently calls `query(full_prompt)` with a plain string — the SDK raises `ValueError` if a string is passed when `can_use_tool` is set. Using this callback would require a significant refactoring of Archon's query interface. Additionally, its interaction with `bypassPermissions` mode is unverified. Use the PreToolUse Hooks API instead.

**Pros**: Zero SDK changes; prevents the crash entirely for Read tool; clear user-facing message  
**Cons**: Requires implementing file-stat logic for the hook; Bash tool remains an open vector; LLM prompt guidance alone is not a technical guarantee

### Option B: Increase SDK buffer limit (Medium-term)
Pass `max_buffer_size=10 * 1024 * 1024` when constructing `ClaudeAgentOptions` in `archon/ai/claude_session.py`. The SDK exposes this as a first-class parameter — no venv patching or forking required. This is a single line change:

```python
# in claude_session.py, when constructing ClaudeAgentOptions
options = ClaudeAgentOptions(
    ...
    max_buffer_size=10 * 1024 * 1024,  # 10MB
)
```

**Pros**: Handles larger files without SDK patching; transparent to LLM; single line change in Archon  
**Cons**: 10 MB still has a limit; large JSON messages increase memory pressure; does not prevent pathological cases (e.g. a 50 MB file)

### Option C: Catch SDK error in event_mapper
Add handling in `event_mapper.py` for a `{"type": "error"}` dict, converting it to a proper `ErrorEvent`.

> **Important caveat**: Based on the observed behaviour (exception propagation, not dict yielding), the SDK raises an exception rather than yielding an error dict. If that is confirmed, this option is moot — `event_mapper.py` never sees the error. **Verify the actual SDK behaviour before implementing Option C.**

If the SDK does yield error dicts in some code paths, implementing this would prevent a silent crash in those paths.

**Pros**: Prevents any remaining silent crashes; user gets clean error message  
**Cons**: Treats symptom not cause; may be irrelevant if SDK always raises exceptions; does not prevent data loss on the failed request. Note: the session is recoverable after the exception (send lock released in `finally` block), but the failed request's data is lost.

### Option D: Streaming/chunked file reading
Replace the Read tool's whole-file-at-once approach with chunked streaming that reads and returns file content in segments under the buffer limit.

**Pros**: Handles arbitrarily large files  
**Cons**: Requires significant SDK and tool changes; complex implementation; out of Archon's control (SDK-level)

### Option E: Add prompt guidance to avoid reading large binaries
Add to system prompt/REMINDER.md: "Never use the Read tool on files larger than 500 KB. Use `wc -c <file>` to check size first, then use `head`/`tail`/`grep` for targeted extraction."

**Pros**: Zero code changes; immediate effect  
**Cons**: LLM may ignore it; doesn't prevent the crash if LLM does read a large file; not a technical guarantee

---

## Recommendation

**Option E + Option B + Option A (PreToolUse hook)** as a phased fix:

1. **Option E (immediate, zero cost)**: Add a note to `archon/ai/prompts/system_reminder.md` warning against reading large binary files. Zero code change; immediate effect on LLM behaviour.

2. **Option B (immediate, one line)**: Set `max_buffer_size=10 * 1024 * 1024` on `ClaudeAgentOptions` in `claude_session.py`. Single line change; no SDK patching required. Raises the practical ceiling significantly. Apply to **all** `ClaudeAgentOptions` construction sites (see Files to modify).

3. **Option A — PreToolUse hook (short-term)**: Implement a PreToolUse hook in `claude_session.py` that rejects Read tool calls targeting files larger than **5 MB** (with Option B in place, accounting for ~2x JSON overhead on the 10 MB buffer). Returns `"File too large (>5 MB) — use head/tail/grep for targeted extraction"` to the LLM. **Note**: the 500 KB threshold in Option A's description is the threshold for standalone deployment without Option B; once Option B is applied, use 5 MB here. Scope is Read tool only — Bash tool calls remain an open vector (Option B's larger buffer is the primary mitigation for those).

**Option C** should only be evaluated after confirming whether the SDK yields error dicts or raises exceptions exclusively — it may be irrelevant.

**Files to modify:**
- `archon/ai/prompts/system_reminder.md` — add large file warning (Option E)
- `archon/ai/claude_session.py` — set `max_buffer_size` on `ClaudeAgentOptions` (Option B); add PreToolUse hook (Option A)
- `archon/ai/history_compactor.py` — set `max_buffer_size` on `ClaudeAgentOptions` (Option B)
- `archon/search/description_generator.py` — set `max_buffer_size` on `ClaudeAgentOptions` (Option B)

---

## Testing

The following tests must be written before implementing any fix (TDD):

1. **Buffer overflow exception propagation** (unit test): Mock `ClaudeSDKClient.receive_response()` to raise `CLIJSONDecodeError` (public name: `claude_agent_sdk.CLIJSONDecodeError`; the internal alias `SDKJSONDecodeError` used in transport internals is not directly importable). Send a message via `handler.py`. Verify:
   - The user receives an `❌ Error: ...` message (not silence)
   - The session `send_lock` is released
   - A subsequent message can be processed normally (session is reusable)

   Accept: handler catches exception, error delivered, session reusable.

2. **PreToolUse hook test** (unit test, if Option A is implemented): Register a size-checking PreToolUse hook that denies Read tool calls on a file larger than the configured threshold. Test the following cases:
   - **(a)** File exactly at threshold — hook allows the call
   - **(b)** File 1 byte over threshold — hook denies with the correct user-facing message (`"File too large (>N MB) — use head/tail/grep for targeted extraction"`)
   - **(c)** File path does not exist (stat fails) — hook allows the call (fail-open)
   - **(d)** Non-Read tool with large input — hook allows the call (scope is Read only)

   Accept: session remains usable after denial; correct decision and message in each case.

3. **`ClaudeAgentOptions` max_buffer_size** (unit test, if Option B is implemented): Mock the `ClaudeAgentOptions` constructor. Call `claude_session.py`'s session setup. Assert `max_buffer_size=10 * 1024 * 1024` is passed. Also verify all other `ClaudeAgentOptions` construction sites (`history_compactor.py` and any others) pass the same value — the fix must be applied consistently across the codebase.
