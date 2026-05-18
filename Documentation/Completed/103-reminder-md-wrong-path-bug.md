# Bug 02 — REMINDER.md read from wrong path at session start

Status: FIXED

## Description

At the first message, the AI tried to read `/Users/manczg/.archon/REMINDER.md` but the file doesn't exist at that path. The actual file is at `/Users/manczg/.archon/workspace/REMINDER.md` (the configured working directory is `/Users/manczg/.archon/workspace`).

From session log:
```
> Archon:
📤 Result [1]:
File does not exist. Note: your current working directory is /Users/manczg/.archon/workspace. Did you mean /Users/manczg/.archon/workspace/REMINDER.md?

> Archon:
📤 Result [2]:
<tool_use_error>Cancelled: parallel tool call Read(/Users/manczg/.archon/REMINDER.md) errored</tool_use_error>
```

## Root cause hypothesis

The AGENTS.md at `/Users/manczg/.archon/workspace/AGENTS.md` tells the AI to "Read REMINDER.md" without an explicit absolute path. The AI may be constructing the path as `/Users/manczg/.archon/REMINDER.md` (parent of workspace) from old history context that referenced the pre-redesign path.

Around March 8, workspace files were reorganized and moved from `~/.archon/` to `~/.archon/workspace/`. Old history/daily summaries still reference the old path, causing the AI to try the wrong location.

## Tasks

1. Confirm root cause: check AGENTS.md and startup context injected text for path references
2. Check what context_provider injects and whether it mentions old paths
3. Fix: either update AGENTS.md with explicit workspace-relative paths, or inject a correction in startup context
4. Write regression test if possible
5. Verify the fix works

## Fix Applied (2026-03-11)

**Root cause confirmed:** `AGENTS.md` used bare relative names `REMINDER.md` and `MEMORY.md` without explicit paths. The AI constructed absolute paths by prepending `~/.archon/` (the parent of the workspace directory) rather than `~/.archon/workspace/` (the actual CWD). This was confirmed in session logs from 2026-03-08, 2026-03-09, and 2026-03-10.

**What was checked:**
- `/Users/manczg/.archon/REMINDER.md` — does NOT exist (confirmed)
- `/Users/manczg/.archon/workspace/REMINDER.md` — EXISTS (confirmed)
- `/Users/manczg/.archon/MEMORY.md` — does NOT exist (confirmed)
- `/Users/manczg/.archon/workspace/MEMORY.md` — EXISTS (confirmed)
- `archon/ai/history_compactor.py` `startup_context_prompt()` — no REMINDER.md path references
- `archon/ai/reminder.py` — uses `workspace_dir / "REMINDER.md"` (correct, code-side is fine)
- History daily summaries — no old-path references; all daily summaries correctly use workspace path
- History session logs — old path (`/Users/manczg/.archon/REMINDER.md`) appeared in sessions from 2026-03-08, 2026-03-09, 2026-03-10

**Fix applied:** Updated `/Users/manczg/.archon/workspace/AGENTS.md` — changed both entries in the "At every session start" section from bare relative names to explicit absolute paths:
- `REMINDER.md` → `/Users/manczg/.archon/workspace/REMINDER.md`
- `MEMORY.md` → `/Users/manczg/.archon/workspace/MEMORY.md`

MEMORY.md was found to have the same issue and was fixed at the same time (Option A per the bug spec).

No code changes were required. No tests needed (workspace file change only).

## DA Review (2026-03-11)

### Verified

1. **AGENTS.md lines 7-8 now use absolute paths** -- confirmed. `/Users/manczg/.archon/workspace/REMINDER.md` and `/Users/manczg/.archon/workspace/MEMORY.md` are explicitly specified in the "At every session start" section.
2. **Both target files exist** -- confirmed. `/Users/manczg/.archon/workspace/REMINDER.md` (36 lines) and `/Users/manczg/.archon/workspace/MEMORY.md` (45 lines) are present.
3. **Code-side path construction is correct** -- `archon/ai/reminder.py` line 24 uses `workspace_dir / "REMINDER.md"` where `workspace_dir` comes from config. No code change was needed.
4. **Compacted daily summaries do NOT contain wrong paths** -- grep of `/Users/manczg/.archon/history/daily/` confirmed zero matches for the old wrong-parent path pattern. Since `get_recent_context()` only reads from `daily/` (not `sessions/`), old session logs with wrong paths are NOT injected into startup context.
5. **TOOLS.md does NOT reference REMINDER.md or MEMORY.md** -- confirmed clean.
6. **config.toml `working_directory`** is set to `/Users/manczg/.archon/workspace` -- the absolute paths in AGENTS.md match this.

### Residual Issues Found

**[MAJOR] Bare `MEMORY.md` references remain in AGENTS.md lines 15-40.** Only lines 7-8 were updated to absolute paths. The rest of AGENTS.md still contains 10+ bare `MEMORY.md` references (lines 15, 17, 21, 23, 26, 27, 31, 32, 40) and 1 bare `REMINDER.md` reference (line 33: "update AGENTS.md, TOOLS.md, or the relevant skill" -- though this one is about concepts, not file reads). Examples:

- Line 15: `**Long-term:** \`MEMORY.md\``
- Line 32: `When someone says "remember this" -> update your MEMORY.md`
- Line 40: `Write to \`MEMORY.md\` **immediately, in the same turn**`

While the AI should resolve these relative to CWD (which is `/Users/manczg/.archon/workspace`), the same ambiguity that caused the original bug could cause the AI to construct wrong absolute paths from these bare references. The fix only hardened the two most critical references (the explicit "Read" instructions at session start) but left the remaining references as bare names.

**Risk assessment**: Medium. The AI's CWD is correctly set to `/Users/manczg/.archon/workspace`, so bare `MEMORY.md` in write instructions should resolve correctly. The original bug was specifically about the AI constructing an absolute path by combining old history context with a bare name. Since the two "Read" instructions are now absolute, the AI is less likely to construct wrong paths for the remaining bare references. However, if old session logs with the wrong path are ever surfaced (e.g., the user asks "what happened on March 8?"), the AI could re-learn the wrong path and apply it to the bare `MEMORY.md` write instructions.

**[MINOR] Old session logs with wrong paths still exist.** Files at `/Users/manczg/.archon/history/sessions/2026-03-08.md`, `2026-03-09-20-36-Yara.md`, and `2026-03-08-06-09-Atlas.md` contain the wrong path `/Users/manczg/.archon/REMINDER.md` and `/Users/manczg/.archon/MEMORY.md`. These are NOT injected via `startup_context_prompt()` or `get_recent_context()` (which only read from `daily/`). However, if the AI reads these session logs on user request ("what happened March 8?"), it could re-learn the wrong path. This is low risk but worth noting.

**[INFO] Portability concern.** The fix hardcodes `/Users/manczg/.archon/workspace/` as an absolute path in AGENTS.md. If `config.toml`'s `working_directory` changes, or the system is moved to a different machine/user, the AGENTS.md paths will be wrong. The installer (`tests/test_installer_py.py` shows workspace file copying logic) would need to template these paths. This is acceptable for a single-user deployment but would be a problem for distribution.

### Conclusion

The fix is correct and addresses the immediate bug. The two critical "Read" instructions now use absolute paths. The residual bare `MEMORY.md` references in AGENTS.md are a secondary risk that could cause a recurrence under specific conditions (AI reading old session logs and applying stale path knowledge to bare-name write instructions).

## DA Fix (2026-03-11)

**Finding reviewed:** "10+ bare MEMORY.md references remain in AGENTS.md lines 15-40 (write/update instructions). These were not updated to absolute paths."

**Verdict: No fix required.**

All bare `MEMORY.md` references on lines 15-40 were audited against the decision criteria (Read = needs absolute path; Write/descriptive = no path resolution, safe to leave as-is):

| Line | Content | Context | Action |
|------|---------|---------|--------|
| 15 | `` `MEMORY.md` `` | Descriptive label ("Long-term: `MEMORY.md`") | No change |
| 17 | `MEMORY.md` | Descriptive prose ("write to MEMORY.md") | No change |
| 21 | `MEMORY.md` | Section heading title | No change |
| 23 | `MEMORY.md` | Capability description ("read, edit, and update MEMORY.md freely") | No change — describing capability, not issuing a Read instruction |
| 26 | `MEMORY.md` | Write/update instruction | No change |
| 27 | `MEMORY.md` | Write/update instruction | No change |
| 32 | `MEMORY.md` | Write instruction ("update your MEMORY.md") | No change |
| 33 | `AGENTS.md`, `TOOLS.md` | Write instruction ("update AGENTS.md, TOOLS.md") | No change |
| 40 | `` `MEMORY.md` `` | Write instruction ("Write to `MEMORY.md`") | No change |
| 70 | `TOOLS.md` | Write instruction ("Keep local notes in `TOOLS.md`") | No change |

**Rationale:** The original bug was triggered by an explicit "Read MEMORY.md" instruction (now fixed at lines 7-8 with absolute paths). Write instructions and descriptive prose do not cause the AI to resolve bare names as filesystem paths — they instruct the AI to *write* using its CWD, which is correctly set to `/Users/manczg/.archon/workspace`. No path ambiguity exists for write operations.

Line 23 ("read, edit, and update MEMORY.md freely") could theoretically prompt a read, but it is a capability description within a documentation section, not a procedural instruction. The AI only executes explicit "Read X" instructions as tool calls, not capability descriptions.

**No AGENTS.md changes were made.** The primary fix (lines 7-8) is sufficient to resolve Bug 02.

## AI Notes
