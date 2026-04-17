# Bug Investigation: history_grep Called with Directory Path

**Date**: 2026-04-17  
**Observed**: Router called `mcp__archon__history_grep` with `/Users/manczg/.archon/history/daily` (a directory), got error "Path is a directory, not a file. Provide a specific file path." Wasted 1 tool call + ~1s round-trip before recovering.

---

## Root Cause

Three interconnected weaknesses, no single fatal flaw:

### 1. Weak tool schema for `history_grep` and `history_read` (`archon/ai/archon_router_mcp_server.py`)
- `_HISTORY_GREP_TOOL` (lines 105–126): description says "Search for a pattern in a history file" and path parameter says "Absolute path to the file" — correct, but neither warns explicitly against passing a directory.
- `_HISTORY_READ_TOOL` (lines 85–103): same weakness; path parameter says "Absolute path to the file" without an explicit directory warning.

LLMs treat parameter descriptions as soft guidance, not hard rules.

### 2. Stochastic LLM failure despite existing guidance; compounded by error message misdirection
The router has two prompts:
- **`orchestrator.md`** (system prompt, 5 lines): says "Use history_read and history_grep to research past work when needed" but **never mentions `history_list`**. It also embeds a bare directory path: "Prefer daily compacted summaries (`~/.archon/history/daily/`) first." This gives the LLM a directory path as an example in its highest-attention context.
- **`route_task.md`** (per-call routing prompt): contains a full **"History research tools"** section (lines 14–28) that explicitly teaches the `history_list` → `history_grep` two-step pattern.

The guidance already exists in `route_task.md`. The router ignored it — a stochastic LLM failure, not a missing-documentation gap. A likely contributing trigger: both `orchestrator.md` and `route_task.md` embed actual directory paths (via `{history_dir}` substitution in `route_task.md`, and a hardcoded `~/.archon/history/daily/` in `orchestrator.md`), giving the LLM copy-paste targets for the `path` argument. Note: the `{history_dir}` substitution is confirmed at `decomposer.py:354` (`.replace("{history_dir}", _cfg.history.directory)`).

An additional structural contributor: all three history tools share the same parameter name `path`, but `history_list` expects a **directory** while `history_grep` and `history_read` expect a **file**. This naming collision is a direct source of LLM confusion — the same word means opposite things depending on which tool is being called.

Additionally, the `history_read` directory error message (lines 383–385) says: *"Use history_grep to search within it or provide a specific file path."* This is incorrect: `history_grep` **also rejects directories** (line 412–413). If the LLM passed a directory to `history_read` first, this error would actively direct it toward the same mistake with `history_grep`.

### 3. Graceful error recovery masks the confusion
The tool correctly returns an error message when given a directory. The router recovers by calling `history_list` next. This means no silent failure occurs, but the underlying LLM confusion goes unremediated — a future session may repeat it.

---

## Session Evidence

From `/Users/manczg/.archon/history/sessions/2026-04-17.md`:
- **17:48:44** — Router calls `history_grep(pattern="rerank", path="/Users/manczg/.archon/history/daily")` ← directory
- **17:48:45** — Returns "Path is a directory, not a file."
- **17:48:46** — Router calls `history_list("/Users/manczg/.archon/history/daily")` ← correct recovery
- **17:48:48+** — Router correctly calls `history_grep` on specific files

Cost: 1 wasted round-trip (~1s). One confirmed occurrence; frequency in historical logs is unknown. This is a self-recovering failure — not catastrophic, but each failed turn consumes from the router's `max_turns=5` research budget.

---

## Options

### Option A: Strengthen the tool descriptions and fix the `history_read` error message
Two changes in `archon_router_mcp_server.py`:

1. Update the `path` parameter description in both `_HISTORY_GREP_TOOL` and `_HISTORY_READ_TOOL`:
```
Absolute path to a FILE (not a directory). Must be under ~/.archon/history/.
Example: ~/.archon/history/sessions/YYYY-MM-DD.md
To discover available files, call history_list with the directory path first.
```

2. Fix the `history_read` directory error message (lines 383–385) to not misdirect to `history_grep`:
```python
"Path is a directory, not a file. "
"Call history_list(path) to discover files, then pass a specific file path."
```

**Pros**: Low-risk; covers both tools at the schema level; removes the cross-tool misdirection bug  
**Cons**: Still probabilistic — LLMs can ignore parameter descriptions; does not eliminate the failure mode  
**Testability**: Error message fix is mechanically testable (unit test: pass a directory to `history_read`, assert response suggests `history_list`, not `history_grep`). Schema description changes are not mechanically testable.

### Option B: Reinforce guidance in `route_task.md`
Clarify the history tools section in `archon/ai/prompts/route_task.md` to make the file-only requirement more explicit. For example, add a warning line after the `history_grep` description:
```
⚠️ `path` must be a path to a FILE, not a directory. Always call `history_list` first to discover file names.
```
Also consider updating `orchestrator.md` to mention `history_list`: *"Use history_list to discover files, then history_read or history_grep on specific file paths."* — the current system prompt omits the discovery tool entirely.

**Pros**: Targets the actual prompts the router sees; strengthens guidance at the point of use; fixing `orchestrator.md` addresses the system-prompt gap  
**Cons**: Adds tokens to every routing call; LLM non-determinism means this still cannot guarantee prevention; `{history_dir}` substitution still embeds directory paths in the prompt (the warning does not remove them)  
**Testability**: Not mechanically testable; effectiveness measured by monitoring router call patterns in production logs

### Option C: Auto-handle directories in the tool (deterministic fix)
If `path` is a directory, recursively grep all files under it and return matching lines, prefixed with `[path/to/file]` attribution. Note that `_MAX_GREP_MATCHES = 200` already bounds output size.

**Pros**: Eliminates the failure mode entirely regardless of LLM behavior — the only option providing a hard guarantee; consistent with how `grep` works in every Unix system; useful output instead of an error  
**Cons**: Changes observable tool semantics (directory input → content output instead of error); creates API contract inconsistency with `history_read` (which would still reject directories); implementation requires file discovery, per-file grep, result merging with source attribution — more than a trivial change  
**Testability**: Fully mechanically testable (unit test: pass a directory path with a pattern, assert attributed content is returned)

---

## Recommendation

**Option A first** (low-risk, fixes a real bug in the error message), **then Option B**, with **Option C as the primary escalation path**:

1. **(Immediate)** Apply Option A: strengthen `path` parameter descriptions in `_HISTORY_GREP_TOOL` and `_HISTORY_READ_TOOL`, and fix the `history_read` directory error message to suggest `history_list` instead of `history_grep`.
2. **(Short-term)** Apply Option B: add file-only warning to `route_task.md` and update `orchestrator.md` to mention `history_list`.
3. **(If recurrence in the 2 weeks post-fix)** Implement Option C — it is the only testable fix and aligns with the project's TDD mandate. Option C should be preferred over further prompt tweaks if probabilistic fixes prove insufficient. "Recurrence" means the "Path is a directory" error appears in a new session's logs despite Options A+B being in place.
4. **(Monitoring)** Before and after fixes: grep historical session logs for "Path is a directory" in `~/.archon/history/sessions/` to establish baseline frequency. Add `logger.warning` in the `_tool_history_grep` directory-rejection branch so production recurrence is automatically logged.

**Files to modify:**
- `archon/ai/archon_router_mcp_server.py` — strengthen `path` parameter description in `_HISTORY_GREP_TOOL` and `_HISTORY_READ_TOOL`; fix `history_read` directory error message (lines 383–385)
- `archon/ai/prompts/route_task.md` — add file-only warning to the `history_grep` bullet
- `archon/ai/prompts/orchestrator.md` — add `history_list` to the tools mentioned in the system prompt

**Do NOT modify** `archon/ai/prompts/decomposer.md` — that file is the main execution session's system prompt and is not used by the router.
