# FIX-032 — Decomposer: tool-failure fallback and commitment discipline
**Purpose**: Prevent the Decomposer from surrendering when a tool fails and from making empty promises without same-turn follow-through.
**Audience**: Internal maintainers
**Status**: To Do

---

## Background

Two behavioural bugs surfaced in the session of 2026-04-17 18:20–20:31 UTC (see `bug_investigation_05_06_llm_giveup_promise.md`):

- **Bug 5 — Tool failure → premature surrender**: When `mcp__search__search` failed with a fastembed import error, the Decomposer immediately told the user it had no context and asked for a recap. Session history was fully available via `Grep`/`Read` — tools the Decomposer already had — but it never tried them. The root cause is that `decomposer.md` lists available tools but gives no instruction on what to do when one of them fails.

- **Bug 6a — Empty promise ("Won't happen again")**: The Decomposer said "Won't happen again" after being corrected, but took no save action in that turn. Only after the user complained 7 minutes later did it actually write to `MEMORY.md`. The root cause is that `decomposer.md` has no rule requiring a commitment to be fulfilled in the same response turn.

The classifier degradation observed in the same session (Bug 6b) is a separate, independent issue already tracked in FIX-030 and is **out of scope here**.

The fastembed import error itself was fixed in commit `62ccb26`. The prompt additions in this FIX therefore serve as defence-in-depth for future tool failures of any kind.

**Prerequisites before implementing**:
1. Verify whether `/Users/manczg/.archon/workspace/CLAUDE.md` or any ancestor `CLAUDE.md` already contains equivalent fallback guidance loaded by the SDK. If it does, the root cause shifts to "model ignores existing instruction under stress", which prompt additions alone cannot reliably fix.
2. Confirm commit `62ccb26` is deployed — if yes, Bug 5 priority is defence-in-depth, not urgent.

## Goal

After this fix, the Decomposer prompt (`archon/ai/prompts/decomposer.md`) contains:
1. An explicit **Fallback Strategy When Tools Fail** section instructing it to try `Grep`/`Read`/`Glob`/`Bash` before declaring information unavailable.
2. A **Commitment = Immediate Action** section instructing it never to make a promise (memory save, correction, etc.) without executing the action in the same response turn.

Both additions have deterministic string-match unit tests confirming they are present. No code changes are required.

---

## Scope

### In Scope
- Editing `archon/ai/prompts/decomposer.md` to add two new sections
- Unit tests asserting the new prompt sections exist (string match)
- Unit tests asserting the prompt loads correctly via `load_prompt()`

### Out of Scope
- Classifier session management (FIX-030)
- Pipeline-level `ToolResult` error detection (Option C from the investigation — cannot prevent within-turn surrender; not worth implementing)
- `~/.archon/workspace/REMINDER.md` changes (not version-controlled; lost on `archon update`)
- `archon/ai/classifier.py` threshold changes (FIX-030)
- Any changes to `archon/ai/decomposer.py` Python code

---

## Acceptance criteria
- [x] `archon/ai/prompts/decomposer.md` contains a section titled `## Fallback Strategy When Tools Fail`
- [x] `archon/ai/prompts/decomposer.md` contains a section titled `## Commitment = Immediate Action`
- [x] Unit test `test_decomposer_prompt_contains_fallback_section` passes
- [x] Unit test `test_decomposer_prompt_contains_commitment_section` passes
- [x] Unit test `test_decomposer_prompt_loads_without_error` passes
- [x] All existing tests pass (`uv run pytest`)

---

## What does NOT change
- `archon/ai/decomposer.py` — no Python logic changes
- `archon/ai/classifier.py` — threshold stays at 50 (FIX-030's concern)
- `archon/ai/prompts/classifier.md` — unchanged
- All chat, gateway, platform, and CLI modules — unchanged
- Existing `decomposer.md` content — new sections are appended, nothing removed

---

## Known limitations / accepted trade-offs
- **Prompt engineering is probabilistic**: adding instructions does not deterministically change LLM behaviour. Under high stress (e.g. many tool failures in one turn), the model may still ignore the new rules. The fix is best-effort, not guaranteed.
- **Option C (pipeline ToolResult detection) is intentionally excluded**: `inject_context()` only fires on the *next* `send()` call, so it cannot prevent within-turn surrender — the exact failure mode observed.
- **Bug 6b (classifier JSON corruption) is excluded**: its root cause (Mode A TextBlock discard / Mode C post-crash corruption) is unrelated to `decomposer.md` and is tracked in FIX-030.

---

## Architecture

No new modules, classes, or functions. The change is confined to one Markdown prompt file.

**File modified**: `archon/ai/prompts/decomposer.md`

Two sections are appended at the end of the file:

```markdown
## Fallback Strategy When Tools Fail

When a primary tool fails (import error, network timeout, permission denied):
1. Do NOT give up or declare information unavailable immediately.
2. Identify what you were trying to find and where it might live (files, directories, logs).
3. Try alternative access methods in order:
   - `Grep` — pattern search across known directories (e.g. `~/.archon/history/sessions/`)
   - `Read` — direct file access if the path is known or guessable
   - `Glob` — discover file patterns if directory is known
   - `Bash` — last resort for complex queries
4. Only state "information unavailable" after ALL alternative methods are exhausted and have returned no results.
5. Never ask the user to supply context that you could retrieve yourself via these fallbacks.

## Commitment = Immediate Action

When you make a commitment — saving to memory, correcting a past mistake, updating a file — execute it in the same response turn:
- Do NOT say "I will save this" or "won't happen again" without doing it NOW.
- Complete the action (Write/Edit tool call) BEFORE writing your closing response text.
- If the action fails, acknowledge the failure explicitly: "I tried to save this but the write failed."
- Vague promises with no same-turn action are not acceptable.
```

**Prompt load path**: `archon/ai/prompts/__init__.py` → `load_prompt("decomposer")` reads the file via `importlib.resources`. No changes needed there.

---

## Tests

- **test_decomposer_prompt_contains_fallback_section** (unit): asserts `decomposer.md` content contains `"## Fallback Strategy When Tools Fail"`
- **test_decomposer_prompt_contains_commitment_section** (unit): asserts `decomposer.md` content contains `"## Commitment = Immediate Action"`
- **test_decomposer_prompt_loads_without_error** (unit): calls `load_prompt("decomposer")` and asserts it returns a non-empty string without raising

---

## Documentation update
- [ ] `Documentation/Backlog/bug_investigation_05_06_llm_giveup_promise.md`, Files to Update section: mark `archon/ai/prompts/decomposer.md` items as complete once this FIX is done

---

## Task breakdown

### Phase 1 — Prompt update and tests
> **Releasable**: after Task 1.2 completes — the prompt is updated and tests confirm both sections are present.

#### Task 1.1 — Add fallback and commitment sections to decomposer.md
- [x] **File**: `archon/ai/prompts/decomposer.md`
- **Depends on**: nothing
- **Description**:
  - Append the following two sections to the end of the file, after the existing `## Guidelines` section:

    ```markdown
    ## Fallback Strategy When Tools Fail

    When a primary tool fails (import error, network timeout, permission denied):
    1. Do NOT give up or declare information unavailable immediately.
    2. Identify what you were trying to find and where it might live (files, directories, logs).
    3. Try alternative access methods in order:
       - `Grep` — pattern search across known directories (e.g. `~/.archon/history/sessions/`)
       - `Read` — direct file access if the path is known or guessable
       - `Glob` — discover file patterns if directory is known
       - `Bash` — last resort for complex queries
    4. Only state "information unavailable" after ALL alternative methods are exhausted and have returned no results.
    5. Never ask the user to supply context that you could retrieve yourself via these fallbacks.

    ## Commitment = Immediate Action

    When you make a commitment — saving to memory, correcting a past mistake, updating a file — execute it in the same response turn:
    - Do NOT say "I will save this" or "won't happen again" without doing it NOW.
    - Complete the action (Write/Edit tool call) BEFORE writing your closing response text.
    - If the action fails, acknowledge the failure explicitly: "I tried to save this but the write failed."
    - Vague promises with no same-turn action are not acceptable.
    ```

  - Do not alter any existing content; only append.
  - Ensure the file ends with a single newline.
- **Releasable**: after this task the updated prompt is ready; tests in Task 1.2 confirm correctness.
- **Tests (TDD)** — `tests/ai/test_decomposer_prompt.py`:
  - Write tests FIRST (Task 1.2), then implement this task to make them pass.

#### Task 1.2 — Unit tests for prompt content and load
- [x] **File**: `tests/ai/test_decomposer_prompt.py`
- **Depends on**: nothing (write tests first — TDD)
- **Description**:
  - Create a new test file with three unit tests.
  - **`test_decomposer_prompt_contains_fallback_section`**: reads `archon/ai/prompts/decomposer.md` directly (via `importlib.resources` or `pathlib`) and asserts the string `"## Fallback Strategy When Tools Fail"` is present.
  - **`test_decomposer_prompt_contains_commitment_section`**: same approach, asserts `"## Commitment = Immediate Action"` is present.
  - **`test_decomposer_prompt_loads_without_error`**: calls `load_prompt("decomposer")` (from `archon.ai.prompts`) and asserts the return value is a non-empty `str` containing both section headers. Verifies the prompt loader picks up the updated file.
  - No mocking needed — these are string assertions on a static file.
  - Import pattern: `from archon.ai.prompts import load_prompt` (verify this import works in the existing test suite before finalising).
- **Releasable**: after this task, CI has deterministic coverage that both prompt sections exist and the file loads correctly.
- **Tests (TDD)** — `tests/ai/test_decomposer_prompt.py`:
  - Unit: `test_decomposer_prompt_contains_fallback_section` — asserts fallback section header present in file
  - Unit: `test_decomposer_prompt_contains_commitment_section` — asserts commitment section header present in file
  - Unit: `test_decomposer_prompt_loads_without_error` — `load_prompt("decomposer")` returns non-empty str with both headers
  - Checkpoint: `uv run pytest tests/ai/test_decomposer_prompt.py -v`
