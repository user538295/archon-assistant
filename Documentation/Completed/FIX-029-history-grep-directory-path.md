# FIX-029 — history_grep/history_read directory-path confusion
**Purpose**: Fix LLM confusion when calling history tools with directory paths instead of file paths  
**Audience**: Internal — router LLM reliability  
**Status**: To Do

---

## Background

The router LLM occasionally calls `history_grep` or `history_read` with a directory path (e.g. `~/.archon/history/daily`) instead of a file path. The tools correctly reject this with an error, and the router recovers by calling `history_list` — but the failure wastes one of the router's 5 research-budget turns (~1s round-trip).

Root causes (from bug investigation `bug_investigation_02_history_grep_directory.md`):

1. **Tool schema** — the `path` parameter description in `_HISTORY_GREP_TOOL` and `_HISTORY_READ_TOOL` says "Absolute path to the file" but does not warn against directories or instruct the caller to use `history_list` first.
2. **Misdirecting error message** — when `history_read` receives a directory it currently says "Use history_grep to search within it" — but `history_grep` also rejects directories. This can lead the LLM into a second wasted call.
3. **Prompt gaps** — `orchestrator.md` (5-line system prompt) mentions `history_read` and `history_grep` but never mentions `history_list`, so the LLM has no discovery step in its highest-attention context. `route_task.md` documents the correct `history_list → history_grep` two-step but could make the file-only constraint more prominent.

---

## Goal

After this fix: the router LLM receives clear, consistent guidance at every level (schema, error messages, prompts) that `history_grep` and `history_read` require a **file** path, and that `history_list` must be used first to discover files. The `history_read` error message no longer misdirects to `history_grep`. The fix is low-risk (no new logic, no new tools) and is mechanically testable for the error message change.

---

## Scope

### In Scope
- Strengthen `path` parameter description in `_HISTORY_GREP_TOOL` and `_HISTORY_READ_TOOL` (file-only, use `history_list` first)
- Fix `history_read` directory-rejection error message to suggest `history_list`, not `history_grep`
- Add file-only warning line to the `history_grep` bullet in `route_task.md`
- Add `history_list` mention to `orchestrator.md` system prompt

### Out of Scope
- Option C (auto-handle directories by grepping recursively) — deferred; implement only if recurrence is observed post-fix
- Adding `logger.warning` to the directory-rejection branches — deferred to monitoring phase
- Monitoring recurrence in historical logs — operational task, not a code change

---

## Acceptance criteria
- [x] `_HISTORY_GREP_TOOL` `path` description explicitly states the path must be a file, not a directory, and instructs callers to use `history_list` first
- [x] `_HISTORY_READ_TOOL` `path` description explicitly states the path must be a file, not a directory, and instructs callers to use `history_list` first
- [x] `_tool_history_read` directory-rejection error message suggests `history_list`, not `history_grep`
- [x] `route_task.md` contains a file-only warning on the `history_grep` description line
- [x] `orchestrator.md` system prompt mentions `history_list` as the discovery step
- [x] Unit test: passing a directory to `_tool_history_read` asserts response contains `history_list` and does NOT contain `history_grep`
- [x] Unit test: passing a directory to `_tool_history_grep` asserts response contains `history_list`
- [x] All existing tests pass

---

## What does NOT change
- Tool behavior (directory inputs still rejected — no Option C semantics)
- `history_list` tool schema or implementation
- `_tool_history_grep` implementation beyond the error message (it already correctly rejects directories)
- `archon/ai/prompts/decomposer.md` — main execution session prompt, not used by the router

---

## Known limitations / accepted trade-offs
- Schema description changes are probabilistic — LLMs can still ignore them. Option C (deterministic directory handling) remains available if recurrence is observed after this fix.
- `{history_dir}` substitution in `route_task.md` still embeds directory paths as copy-paste targets; this is accepted as unavoidable given the tool's design.

---

## Architecture

No new modules, classes, or functions. Three targeted string changes and two prompt additions:

1. **`archon/ai/archon_router_mcp_server.py`**
   - `_HISTORY_READ_TOOL["inputSchema"]["properties"]["path"]["description"]` — new text
   - `_HISTORY_GREP_TOOL["inputSchema"]["properties"]["path"]["description"]` — new text
   - `_tool_history_read` directory-rejection message (lines 383–385) — new text

2. **`archon/ai/prompts/route_task.md`** — add one warning line after the `history_grep` bullet

3. **`archon/ai/prompts/orchestrator.md`** — add `history_list` to the tools sentence

---

## Tests

- **`test_history_read_directory_error_suggests_history_list`** (unit): pass a directory path to `_tool_history_read`; assert response text contains `history_list` and does NOT contain `history_grep`
- **`test_history_grep_directory_error_suggests_history_list`** (unit): pass a directory path to `_tool_history_grep`; assert response text contains `history_list`

---

## Documentation update
- N/A — no Architecture doc change required; this is an implementation-detail fix.

---

## Task breakdown

### Phase 1 — Error message and schema fixes
> **Releasable**: after Task 1.1 — error message bug is gone; after Task 1.2 — prompt guidance is complete.

#### Task 1.1 — Fix tool schema descriptions and history_read error message
- [x] **File**: `archon/ai/archon_router_mcp_server.py`
- **Depends on**: nothing
- **Description**:
  - Update `_HISTORY_READ_TOOL["inputSchema"]["properties"]["path"]["description"]` to:
    ```
    Absolute path to a FILE (not a directory). Must be under ~/.archon/history/.
    Example: ~/.archon/history/sessions/YYYY-MM-DD.md
    To discover available files, call history_list with the directory path first.
    ```
  - Update `_HISTORY_GREP_TOOL["inputSchema"]["properties"]["path"]["description"]` to the same text.
  - Fix the `_tool_history_read` directory-rejection error message (currently lines 383–385):
    - Old: `"Path is a directory, not a file. Use history_grep to search within it or provide a specific file path."`
    - New: `"Path is a directory, not a file. Call history_list(path) to discover files, then pass a specific file path."`
  - No other logic changes.
- **Releasable**: after this task, the misdirecting error message is gone and both tool schemas carry file-only guidance.
- **Tests (TDD)** — `tests/ai/test_archon_router_mcp_server.py`:
  - [x] Unit: `test_history_read_directory_error_suggests_history_list` — create a tmp directory, call `_tool_history_read` with its path, assert the returned error text contains `"history_list"` and does NOT contain `"history_grep"`
  - [x] Unit: `test_history_grep_directory_error_suggests_history_list` — create a tmp directory, call `_tool_history_grep` with its path and a valid pattern, assert the returned error text contains `"history_list"`
  - [x] Checkpoint: `uv run pytest tests/ai/test_archon_router_mcp_server.py -v`

#### Task 1.2 — Reinforce file-only guidance in router prompts
- [x] **File**: `archon/ai/prompts/route_task.md`
- [x] **File**: `archon/ai/prompts/orchestrator.md`
- **Depends on**: nothing (independent of Task 1.1)
- **Description**:
  - In `route_task.md`, after the `history_grep` bullet (currently line 20), add a warning line:
    ```
    ⚠️ `path` for both `history_grep` and `history_read` must be a path to a FILE, not a directory. Always call `history_list` first to discover file names.
    ```
  - In `orchestrator.md`, update the tools sentence (currently line 2) from:
    ```
    Use history_read and history_grep to research past work when needed.
    ```
    to:
    ```
    Use history_list to discover available files, then history_read or history_grep on specific file paths to research past work.
    ```
  - No other changes to either file.
- **Releasable**: after this task, both the per-call routing prompt and the system prompt carry consistent file-only guidance.
- **Tests (TDD)** — `tests/ai/test_archon_router_mcp_server.py`:
  - Unit: `test_orchestrator_prompt_mentions_history_list` — read `orchestrator.md` content, assert it contains `"history_list"`
  - Unit: `test_route_task_prompt_warns_file_only` — read `route_task.md` content, assert it contains a warning about file-only path requirement for `history_grep` and `history_read`
  - Checkpoint: `uv run pytest tests/ai/test_archon_router_mcp_server.py -v`
