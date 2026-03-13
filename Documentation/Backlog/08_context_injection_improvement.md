# Context Injection Improvement — REMINDER.md for Orch & Agents

**Status**: Planned
**Created**: 2026-03-13
**Reviewed**: 2026-03-13 (devil's advocate pass)

## Problem

Several session types receive incomplete context at creation/spawn time:

| Context | `_session` (main) | `_orch_session` | Background agents |
|---|---|---|---|
| History (compacted) | ✅ | ✅ | ❌ (out of scope) |
| agents.md | ✅ | ✅ | ✅ |
| REMINDER.md | ✅ periodic | ❌ never | ❌ never |
| QMD tools | ✅ | ❌ | ✅ |

The orch session decides task scope and writes agent task prompts — it needs REMINDER.md constraints on every routing call. Background agents spawn with no project constraints at all.

Additionally, the orch session's agent task prompts for `scope=large` plans do not explicitly require including project state/decisions — agents are isolated and operate only from their task field.

---

## Design Decisions (Post-Review)

- **Orch REMINDER.md must be per-call, not one-shot.** `inject_context()` is cleared after the first `send()`. The orch session persists for 20 calls before reset — one-shot injection would leave it with stale constraints for the entire lifecycle. Instead, REMINDER.md is re-read and embedded directly in the `instruction` string on every `route_task()` call.
- **Background agents use one-shot injection.** Each agent spawn creates a fresh `ClaudeSession` — there is no lifecycle staleness issue. `inject_context()` is correct here.
- **QMD for orch session: deferred.** The orch has `max_turns=5`; adding QMD tools without adjusting the turn budget risks degrading routing quality. Revisit when there is evidence of routing failures caused by missing semantic search.
- **No raw conversation history injection into agents.** Improves determinism without the complexity of turn selection and token management.
- **REMINDER.md size guard.** REMINDER.md is now injected on every orch routing call and every agent spawn. Large files silently inflate token costs. Log at INFO with char count and approximate token count on every injection; log at WARNING if the file exceeds 8,000 chars (~2,000 tokens).

### Token Cost Estimate

Approximate cost per injection: `chars / 4 ≈ tokens`

| Scenario | Example size | Cost |
|---|---|---|
| REMINDER.md = 1,000 chars | ~250 tokens | per orch call + per agent spawn |
| Large plan, 5 agents | 5 × 250 | ~1,250 input tokens extra |
| 20 orch routing calls (one reset cycle) | 20 × 250 | ~5,000 input tokens extra |
| REMINDER.md = 8,000 chars (warning threshold) | ~2,000 tokens | WARNING logged |

---

## Tasks

### Task A — Refactor `reminder.py`: shared static method + `build_reminder_injection()` helper

**File**: `archon/ai/reminder.py`

**Two changes:**

1. Extract a `@staticmethod` on `ContextReminder` for the stateless read-and-wrap operation:
   ```python
   @staticmethod
   def read_and_wrap(file: Path) -> str:
   ```
   The existing `build_reminder_message()` delegates to it. Single source of truth for `_XML_WRAPPER` usage.

2. Add a module-level helper for one-shot injection callers:
   ```python
   def build_reminder_injection(workspace_dir: Path) -> str | None:
   ```
   - Calls `ContextReminder.read_and_wrap(file)` internally
   - Returns `None` when file is missing, content is empty/whitespace, or `OSError` occurs
   - Logs `INFO` with char count and approximate token count on successful read
   - Logs `WARNING` if content exceeds 8,000 chars (~2,000 tokens)
   - Logs `WARNING` on unexpected `OSError`

**Tests** (`test_reminder.py` — 7 new tests):
- `read_and_wrap()`: file present → returns XML-wrapped content
- `read_and_wrap()`: `build_reminder_message()` delegates to it (single XML-wrap implementation)
- `build_reminder_injection()`: file present → returns XML-wrapped content
- `build_reminder_injection()`: file missing → returns `None`
- `build_reminder_injection()`: file empty → returns `None`
- `build_reminder_injection()`: whitespace-only → returns `None`
- `build_reminder_injection()`: `OSError` on read → returns `None`, warning logged

---

### Task B — Inject REMINDER.md into `_orch_session` per routing call

**File**: `archon/ai/decomposer.py`
**Depends on**: Task A

**Not** via `inject_context()` (which is one-shot and would go stale across the 20-call session lifecycle). Instead, embed REMINDER.md content directly in the `instruction` string on every `route_task()` call, alongside `context_block` and `paths_block`:

```python
reminder_block = ""
if self._cwd:
    reminder_ctx = build_reminder_injection(Path(self._cwd))
    if reminder_ctx is not None:
        reminder_block = f"\n\n{reminder_ctx}"

instruction = (
    f"[INTERNAL: pipeline orchestration — not a user message]"
    f"{context_block}"
    f"{paths_block}"
    f"{reminder_block}"
    f"{route_prompt}\n\nUser request: {prompt}"
)
```

Resulting context order in every orch routing prompt:
```
[main-session context summary] → [recent file paths] → [REMINDER.md] → [route_task rules + user request]
```

REMINDER.md is always fresh — re-read from disk on every call.

Session log: `logger.info("Injecting REMINDER.md into orch routing call (%d chars, ~%d tokens)", ...)` — already handled by `build_reminder_injection()` in Task A.

**Tests** (`test_decomposer.py` — 5 new tests):
- `REMINDER.md` exists → content appears in the `instruction` string passed to `orch.send()`
- `cwd=None` → no reminder block in instruction
- `REMINDER.md` absent → no reminder block in instruction
- `build_reminder_injection()` error → `route_task()` still completes, fallback to `scope=small`
- Content is re-read on every call (two calls with changed file → different content each time)

**Integration test** (`test_decomposer.py` — 1 new integration test):
- Verify the full `instruction` string ordering: context block appears before paths block, REMINDER.md appears after paths block and before the route_task rules.

---

### Task C — Inject REMINDER.md into background agents after agents.md

**File**: `archon/ai/background_agent_manager.py`
**Depends on**: Task A

In `_run_agent()`, after the `load_workspace_agents` injection, before `session.send(prompt)`:

```python
reminder_ctx = build_reminder_injection(Path(self._cwd))
if reminder_ctx is not None:
    session.inject_context(reminder_ctx)
```

One-shot injection is correct here — each agent spawn creates a fresh `ClaudeSession` with no lifecycle staleness.

Resulting context order (all `inject_context()` calls are prepended on `session.send(prompt)`):
```
[agents.md] → [REMINDER.md] → [task prompt]
```

Session log: `logger.info("Injecting REMINDER.md into agent %r (%d chars, ~%d tokens)", ...)` — already handled by `build_reminder_injection()` in Task A.

**Note on existing test**: `test_background_agent_injects_agents_md_content` asserts `inject_context.assert_called_once()` — must be updated to `call_count == 2` when both `agents.md` and `REMINDER.md` exist.

**Tests** (`test_background_agent_manager.py` — 4 new tests):
- `REMINDER.md` exists in `cwd` → `inject_context` called with XML-wrapped content
- `cwd=None` → no reminder injection (inject_context call count unchanged)
- `REMINDER.md` absent → no reminder injection
- `build_reminder_injection()` error → agent still runs, error swallowed

**Integration test** (`test_background_agent_manager.py` — 1 new integration test):
- Verify the full prompt received by `session.send()` contains agents.md content, then REMINDER.md content, then the task string — in that order.

---

### Task E — Strengthen agent task prompt generation in `route_task.md`

**File**: `archon/ai/prompts/route_task.md`
**Independent** of all other tasks

Add a rule under "Rules for agent plans" instructing the orch to include project context in every agent task description when `scope=large`:

> - Each agent's task must include **all relevant project context** (current state, recent decisions, constraints, relevant file paths) — agents have no conversation history and operate only from their task field

**Tests**: none (prompt change; verifiable by integration test or manual review)

---

## Execution Waves

```
Wave 1 — parallel:
  ├── Task A  reminder.py refactor + build_reminder_injection helper
  └── Task E  route_task.md prompt strengthened

Wave 2 — parallel, after Wave 1 completes:
  ├── Task B  REMINDER.md → orch (per-call in route_task())   depends on A
  └── Task C  REMINDER.md → background agents                 depends on A
```

---

## Files Changed

| File | Tasks |
|---|---|
| `archon/ai/reminder.py` | A |
| `archon/ai/decomposer.py` | B |
| `archon/ai/background_agent_manager.py` | C |
| `archon/ai/prompts/route_task.md` | E |
| `tests/ai/test_reminder.py` | A |
| `tests/ai/test_decomposer.py` | B (unit + integration) |
| `tests/ai/test_background_agent_manager.py` | C (unit + integration) |
