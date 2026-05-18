# Bug 03 — SDK took 19 minutes to respond to simple chat message

Status: FIXED

## Description

On 2026-03-10, after the AI read REMINDER.md and MEMORY.md (tool calls completed at ~18:00:54 UTC), the response was only delivered at 18:19:53 UTC — almost 19 minutes later, for a simple "Hey, how you doing?" greeting.

From session log:
```
### 🔧 Tool: Read [4] · 18:00:54 UTC → /Users/manczg/.archon/workspace/MEMORY.md
### ✅ Response · 18:19:53 UTC
```

No thinking, no tool calls, no log entries between those two events.

## Observed symptoms

- 19 minutes of complete silence for a trivial chat response
- No archon.log entries between 19:00:54 and 19:34:43 local time (the next log entry)

## Root cause hypotheses

1. **Resource starvation**: 4 Claude SDK subprocesses start at first message (classifier, decomposer main, orch_session, summary_session), all competing for resources. The SDK subprocess producing the chat response may have been starved.
2. **SDK subprocess hang**: The Claude SDK subprocess genuinely hung waiting for API response (network issue, API timeout).
3. **Generator not consumed**: The response generator may have been awaiting consumption but something was blocked.

## Related issues

- Concurrent message handling (Bug 04/05/06): three more messages arrived at 18:34:44, causing 109s classification times — same resource contention pattern.
- Generator drain timeout (Bug 10): log shows "Generator drain timed out after 5s" — related SDK response handling issue.

## Tasks

1. Identify how many SDK subprocesses are started at first message and whether eager-start of orch/summary sessions causes resource contention
2. Check if there's a wall-clock timeout on decomposer.answer() — there should be
3. Check decomposer.py for `_orch_session` startup (lazy vs eager)
4. Implement lazy-start for orch_session and summary_session if they are eagerly started
5. Add wall-clock timeout to `_task_direct_monitored` decomposer path
6. Write test(s) for the fix
7. Fix the bug

## AI Notes

### Fix applied (2026-03-11)

**Root cause confirmed**: 4 SDK subprocesses were started simultaneously at first message (classifier, decomposer main, orch_session, summary_session). All compete for the same Claude Code CLI binary. The first message response was delivered by the decomposer main session, which was waiting for resources held by the orch/summary sessions started just before it.

**Fix**: Made orch and summary sessions lazy-started in `archon/ai/decomposer.py`:
- `_orch_session` and `_summary_session` initialized to `None` (line 107-108)
- `Decomposer.start()` only starts the main session (lines 116-119)
- `_ensure_orch_session()` creates and starts orch session on first `route_task()` call (lines 155-188)
- `_ensure_summary_session()` creates and starts summary session on first `_update_context_summary()` call (lines 190-202)
- `stop()` guards against stopping sessions that were never started (lines 133-151)
- Assign-before-start ordering fixed: sessions assigned to `self._orch_session` / `self._summary_session` only AFTER `start()` returns successfully (2026-03-11 DA followup)

**Result**: Only 2 SDK subprocesses at first message (classifier + decomposer main). Orch session starts lazily on first routing call. Summary session starts lazily on first summarization call.

**Tests added** in `tests/ai/test_decomposer.py`:
- `test_start_starts_main_session_only`
- `test_stop_skips_sessions_not_started`
- `test_ensure_orch_session_returns_cached_session`
- `test_ensure_summary_session_returns_cached_session`
- `test_orch_session_created_with_max_turns_5`
- `test_orch_session_created_with_tools_empty_list`

All 2308 tests pass.

## DA Review (2026-03-11)

### Verified in Code

1. **Lazy-start is correctly implemented.** `_orch_session` and `_summary_session` are `None` at init (line 107-108). `start()` only starts `self._session` (lines 116-118). `_ensure_orch_session()` and `_ensure_summary_session()` guard on `is None` and create+start on first use (lines 155-190, 192-204).

2. **Assign-after-start ordering is correct.** Both `_ensure_orch_session()` (line 163: local `session`, line 172: `await session.start()`, line 174: `self._orch_session = session`) and `_ensure_summary_session()` (lines 195-203) use a local variable and only assign to `self._*_session` after `start()` succeeds. A failed `start()` leaves the attribute as `None`, so the next call retries cleanly.

3. **`stop()` handles None sessions.** Lines 141 and 146 guard with `is not None` before calling `.stop()`. Verified by test `test_stop_skips_sessions_not_started`.

4. **Wall-clock timeout on `_task_direct_monitored`.** `pipeline.py` line 208 wraps `decomposer.answer()` in `asyncio.timeout(_TASK_DIRECT_TIMEOUT_S)` (300s). On timeout, yields `ErrorEvent` (lines 243-252). This directly addresses the 19-minute hang -- under the new code, max wait is 5 minutes. Test `test_pipeline.py` line 1440 verifies this.

5. **Subprocess count at first message reduced from 4 to 2.** Classifier + decomposer main session start at `Pipeline.start()`. Orch session only starts at first `route_task()` call. Summary session only starts at first `_refresh_summary()` call.

### Issues Found

1. **[MINOR] Double injection of workspace agents on orch reset.** `_reset_orch_if_needed()` (line 478-489) calls `_ensure_orch_session()` which internally calls `load_workspace_agents()` and injects (lines 187-189), then immediately calls `_inject_workspace_agents()` (line 486) which also injects into `self._orch_session` (line 130-131). The workspace agents context is injected twice into the orch session after every reset. Functionally harmless (duplicate context wastes some tokens) but is a latent bug.

2. **[INFO] Root cause hypothesis is plausible but not proven.** The bug file states the 19-minute hang was caused by "4 SDK subprocesses competing for resources." This is a reasonable hypothesis, but the actual root cause was never definitively diagnosed (no CPU/memory profiling, no SDK-level debugging). The fix (lazy-start + wall-clock timeout) mitigates the symptom regardless of root cause, which is the correct engineering approach. But if the true cause was a network hang or API-side issue, the lazy-start alone would not prevent a recurrence -- the 300s timeout is the actual safety net.

3. **[MINOR] 300s timeout is generous.** `_TASK_DIRECT_TIMEOUT_S = 300.0` means a hung response still blocks the user for 5 minutes before the timeout fires. For a "Hey, how you doing?" greeting, even 60s would be generous. The timeout is a last-resort safety net, not a responsiveness optimization. This is acceptable but worth noting.

### Conclusion

The fix correctly addresses the bug through two complementary mechanisms: (a) reducing startup resource contention via lazy sessions (prevention), and (b) adding a 300s wall-clock timeout to `_task_direct_monitored` (mitigation). Both are well-tested. Status FIXED is warranted.

## DA Followup (2026-03-11)

### Issue: Double workspace-agent injection on orch reset

**DA finding**: After every orch reset, `_reset_orch_if_needed()` calls `_ensure_orch_session()` which injects workspace agents internally (lines 187-189), then immediately calls `_inject_workspace_agents()` (line 486 in old code) which re-injects into `self._orch_session`.

**Verified**: The finding was based on **old code that no longer exists**. In the current `decomposer.py`, `_reset_orch_if_needed()` (lines 478-490) only calls `await self._ensure_orch_session()` as its final step — there is NO second call to `_inject_workspace_agents()`. The comment at lines 488-489 explicitly states: "no need to call `_inject_workspace_agents()` separately." The double injection was already fixed.

**Action**: No code change needed.

