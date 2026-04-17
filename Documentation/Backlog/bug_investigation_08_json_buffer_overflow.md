# Bug Investigation: JSON Buffer Overflow on Large File Read

**Date**: 2026-04-17  
**Error**: `❌ Error: Failed to decode JSON: JSON message exceeded maximum buffer size of 1048576 bytes`  
**Context**: LLM read `/tmp/stanford_rag.pdf` (1.1 MB), crashing the session mid-stream.

---

## Root Cause

The Claude Agent SDK has a **hardcoded 1 MB (1,048,576 bytes) buffer limit** for JSON line messages:

**File**: `.venv/lib/python3.12/site-packages/claude_agent_sdk/_internal/transport/subprocess_cli.py`  
**Line 28**: `_DEFAULT_MAX_BUFFER_SIZE = 1024 * 1024  # 1MB buffer limit`  
**Failure point**: Lines ~579–587 in `_read_loop()` — buffer size is checked on each accumulated chunk

When the LLM reads a file >~900 KB:
1. Claude Code CLI loads the file and returns it as JSON tool result
2. SDK's `_read_loop()` accumulates the JSON in its buffer
3. Buffer exceeds 1,048,576 bytes → raises `SDKJSONDecodeError` with "Fatal error in message reader"
4. Error propagated as `{"type": "error"}` in the message channel
5. `event_mapper.py` lines ~248–256 doesn't handle this error dict format → silent drop
6. The pipeline exception propagates up to `handler.py` lines ~409–411 which sends `❌ Error` to the user

### App Log Evidence (20:41:24 UTC)
```
ERROR Fatal error in message reader: Failed to decode JSON: JSON message exceeded maximum buffer size of 1048576 bytes...
ERROR Error processing message for user 154643621 (Exception)
```
The session recovered (send lock released in `claude_session.py` `finally` block ~line 364–419), but the active request's data was lost and triggered Bug 9's cascade.

---

## Why It Happens

The SDK uses a JSON-line protocol: each message is one JSON line, terminated by `\n`. Tool results (file contents) are embedded as strings in that JSON. A 1.1 MB PDF file becomes a JSON string of ~1.5 MB (with escaping) — far exceeding the buffer.

This is a fundamental constraint of the current SDK transport design: **all tool output must fit in one JSON line under 1 MB**.

---

## Affected Files

**SDK (in venv — read-only):**
- `claude_agent_sdk/_internal/transport/subprocess_cli.py` line 28, 579–587
- `claude_agent_sdk/_internal/query.py` lines ~249, 256

**Archon:**
- `archon/ai/event_mapper.py` lines ~248–256 — doesn't handle SDK error dict
- `archon/ai/claude_session.py` lines ~334, 339, 364–419 — session recovery
- `archon/chat/handler.py` lines ~409–411 — error display

---

## Options

### Option A: Pre-flight file size check (Recommended immediate fix)
Intercept Read tool calls before they reach the SDK. If the file is >900 KB, return a clear error to the LLM instead of attempting to read it.

This could be implemented as a hook/wrapper in Archon's MCP layer or by teaching the LLM to check file size first with a Bash stat call.

**Pros**: Zero SDK changes; prevents the crash entirely; clear user-facing message ("File too large for direct read — use `head -n 100` or extract specific sections")  
**Cons**: Requires either code in MCP layer or prompt instruction; LLM might still bypass it

### Option B: Increase SDK buffer limit (Medium-term)
Patch the installed SDK's `_DEFAULT_MAX_BUFFER_SIZE` to 10 MB:

```python
# subprocess_cli.py line 28
_DEFAULT_MAX_BUFFER_SIZE = 10 * 1024 * 1024  # 10MB
```

This requires patching a venv file or forking the SDK.

**Pros**: Handles larger files without code changes in Archon; transparent to LLM  
**Cons**: Patches a venv file (fragile, lost on reinstall); 10 MB still has a limit; large JSON messages increase memory pressure

### Option C: Catch SDK error dict in event_mapper
In `event_mapper.py` lines ~248–256, add handling for the `{"type": "error"}` dict that the SDK emits on buffer overflow, converting it to a proper `ErrorEvent`.

**Pros**: Prevents silent crash; user gets clean error message; session stays stable  
**Cons**: Doesn't prevent the crash — session is already corrupted when this runs; treats symptom not cause

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

**Option A + Option C** together as a two-phase fix:

1. **Option C (immediate)**: Fix `event_mapper.py` to handle SDK error dicts (`{"type": "error"}`) as `ErrorEvent`. This prevents silent crashes and ensures session state is cleanly reset. Low risk, one function change.

2. **Option A (short-term)**: Add a pre-flight size check. Best placement: a wrapper around the Read tool in Archon's MCP server, or a check in `claude_session.py` before forwarding tool results. Return `"File too large (>900 KB) — use head/tail/grep for targeted extraction"` to the LLM.

3. **Option E (now)**: Add a note to `archon/ai/prompts/system_reminder.md` warning against reading large binary files.

**Option B** (SDK patching) should be evaluated separately as a longer-term fix.

**Files to modify:**
- `archon/ai/event_mapper.py` ~line 248 — handle `{"type": "error"}` SDK dict
- `archon/ai/prompts/system_reminder.md` — add large file warning
- (Optional) SDK venv patch or MCP wrapper for pre-flight size check
