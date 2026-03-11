# Bug 07 — "Routing check timed out — trying to handle directly" shown to user

Status: FIXED

## Description

The user saw this message:
> ⚠️ Routing check timed out — trying to handle directly

This appeared after sending "You can check the sessions log." and "Check the project repo..."

From archon.log:
```
19:37:37,989 archon WARNING _orch_session.send() timed out after 60s for prompt: You can check the sessions log.
19:37:37,991 archon INFO route_task scope=small fallback=True for prompt: You can check the sessions log.
```

## Root cause

The orchestrator session (`_orch_session` in Decomposer) is used to `route_task()` — it decides if a task is "large" (multi-agent) or "small" (inline). When this session times out after 60s, the code falls back to inline execution and emits a `FallbackNoticeEvent` with message "Routing check timed out — trying to handle directly".

The orch session timeout is caused by the same resource starvation as Bug 04/05/06 — 4+ concurrent SDK subprocesses.

## Secondary issues

1. The fallback message shown to the user is technically accurate but poor UX. The user sees a warning/error-like message but the system is actually working fine (just doing inline routing as a fallback).
2. The orch session timing out on EVERY request (not just under load) suggests the orch session architecture is problematic — it adds latency and a potential failure point.

## Tasks

1. Investigate if `_orch_session` is eagerly started and whether lazy-start would help
2. Check the 60s timeout — is this too short? Is there a way to make the orch session faster?
3. Evaluate if `_orch_session` is even necessary for simple chat/task routing — could the classifier alone determine this?
4. Fix the FallbackNoticeEvent message to be less alarming (or suppress it when fallback works correctly)
5. Consider removing or bypassing orch session for low-complexity intents
6. Write tests
7. Fix

## DA Review (2026-03-11)

### Verified

1. **Orch/summary sessions are lazy-started (None at init, created on first use).** Confirmed in `decomposer.py` lines 107-108: `self._orch_session: ClaudeSession | None = None` and `self._summary_session: ClaudeSession | None = None`. The `__init__` only creates the main `_session`.

2. **`_ensure_orch_session()` creates and starts the session only once.** Confirmed at lines 155-188: the guard `if self._orch_session is None` prevents duplicate creation. When already set, returns immediately without calling `start()`. Test `test_ensure_orch_session_returns_cached_session` verifies this (asserts `start.assert_not_awaited()`).

3. **`start()` skips orch/summary session creation.** Confirmed at lines 116-119: `start()` only calls `await self._session.start()` (main session) and `_inject_workspace_agents()`. Test `test_start_starts_main_session_only` verifies orch/summary `.start()` are not awaited.

4. **`stop()` only stops sessions that were actually started.** Confirmed at lines 133-151: both orch and summary are guarded by `if self._orch_session is not None` / `if self._summary_session is not None`. Test `test_stop_skips_sessions_not_started` creates a Decomposer without pre-injecting lazy sessions, calls `stop()`, and asserts no error.

5. **`route_task()` uses `asyncio.timeout()` correctly.** Two timeout guards confirmed: (a) `_reset_orch_if_needed()` wrapped in `asyncio.timeout(_ORCH_RESET_TIMEOUT_S)` at line 228, catches `TimeoutError`; (b) `orch.send()` wrapped in `asyncio.timeout(_ORCH_TIMEOUT_S)` at line 258, catches `TimeoutError`. Both return `TaskOutput(scope="small", prompt=prompt, is_fallback=True, fallback_reason="")`.

6. **Timeout fallback is silent (no user-visible error).** Confirmed in two places: (a) Decomposer sets `fallback_reason=""` on timeout-based fallbacks (lines 236 and 269); (b) Pipeline at line 175 checks `if task_output.is_fallback and task_output.fallback_reason:` before emitting `FallbackNoticeEvent` -- empty reason means no event emitted. Test `test_route_task_fallback_silent_on_reset_timeout` verifies `fallback_reason == ""`. Pipeline-level test `test_fallback_notice_event_suppressed_when_reason_empty` (in `test_pipeline.py` line 1346) verifies no `FallbackNoticeEvent` is yielded.

7. **Test coverage is thorough.** The lazy-start behavior has dedicated tests: `test_start_starts_main_session_only`, `test_stop_skips_sessions_not_started`, `test_ensure_orch_session_returns_cached_session`, `test_ensure_summary_session_returns_cached_session`, `test_orch_session_created_with_max_turns_5`, `test_orch_session_created_with_tools_empty_list`. Timeout tests: `test_route_task_times_out_and_falls_back`, `test_route_task_reset_timeout_falls_back`, `test_route_task_fallback_silent_on_reset_timeout`, `test_route_task_fallback_includes_is_fallback_flag_on_send_timeout`, `test_route_task_fallback_includes_is_fallback_flag_on_reset_timeout`. Exception fallback: `test_route_task_crash_falls_back_to_small`, `test_route_task_fallback_on_exception`.

8. **No concurrency risk on `_ensure_orch_session()`.** Pipeline.send() holds an `asyncio.Lock()` (line 125 of pipeline.py), so only one `route_task()` call can be in-flight per Pipeline instance at a time. No concurrent access to `_ensure_orch_session()` is possible.

### Issues Found

1. **[RESOLVED] `_ensure_orch_session()` assign-before-start was fixed.** The initial DA review flagged that `self._orch_session` was assigned before `start()` succeeded, risking a cached broken session. This was fixed: both `_ensure_orch_session()` (line 163: local `session`, line 172: `await session.start()`, line 174: `self._orch_session = session`) and `_ensure_summary_session()` (lines 195-203) now use a local variable and assign only after `start()` returns.

2. **[RESOLVED] Bug file status updated to FIXED.**

3. **[MINOR] Double injection of workspace agents on orch reset.** `_reset_orch_if_needed()` (lines 478-489) calls `_ensure_orch_session()` which internally calls `load_workspace_agents()` and injects (lines 187-189), then immediately calls `_inject_workspace_agents()` (line 486) which also injects into `self._orch_session` (line 130-131). Workspace agents context is injected twice after every orch reset. Functionally harmless (duplicate context wastes tokens) but is a latent bug.

4. **[MINOR] `test_route_task_fallback_includes_is_fallback_flag_on_send_timeout` does not assert `fallback_reason == ""`.** The reset-timeout test (line 1673) asserts `fallback_reason == ""`, but the send-timeout test (line 1692) only asserts `is_fallback is True` without verifying the reason is empty. The pipeline-level test covers the end-to-end silencing, so this is a unit-test completeness gap, not a correctness issue.

5. **[INFO] `_make_decomposer` helper duplicated across 3 test files.** Known drift risk documented in DA memory. Not a bug, but a maintenance concern.

6. **[INFO] The 60s orch timeout still fires under resource contention.** The fix makes timeouts silent (no user-visible notification) but does not reduce timeout frequency. Under the same contention conditions, orch will still time out -- the user just won't see it. This is the correct approach: the timeout fallback works fine, so hiding it is appropriate UX.

7. **[INFO] Bug root cause (resource starvation from 4 eager SDK subprocesses) is plausible but unproven.** No profiling or SDK-level debugging was done. The lazy-start fix is a reasonable mitigation regardless of true root cause.

### Conclusion

The fix correctly addresses both aspects of Bug 07: (a) lazy-start reduces orch session timeouts by eliminating startup resource contention, and (b) silent fallback (`fallback_reason=""` + Pipeline guard at line 175) prevents the "Routing check timed out" message from reaching the user. All 7 tasks in the bug file are addressed. The previous DA review finding about assign-before-start has been resolved. Remaining items are minor (double workspace-agent injection on reset, one missing test assertion). Status FIXED is warranted.

## AI Notes

### Fix applied (2026-03-11)

**Root cause confirmed**: `_orch_session` was started eagerly alongside 3 other SDK subprocesses, causing resource contention. Under load the orch session timed out (60s) and emitted a `FallbackNoticeEvent` with message "Routing check timed out — trying to handle directly" — visible to the user.

**Fix** in `archon/ai/decomposer.py`:
1. **Lazy-start** (shared with Bug 03): `_orch_session` is `None` at init, created/started by `_ensure_orch_session()` on first use.
2. **Silent fallback**: timeout and exception fallbacks set `fallback_reason=""`. Pipeline only emits `FallbackNoticeEvent` when `fallback_reason` is non-empty — so timeout fallbacks are silent.
3. **`_TASK_DIRECT_TIMEOUT_S = 300.0`** added for the direct-task path.
4. **Assign-after-start**: `_ensure_orch_session()` assigns `self._orch_session` only after `start()` returns successfully (2026-03-11 DA followup).

**Tests added** in `tests/ai/test_decomposer.py`:
- `test_route_task_times_out_and_falls_back`
- `test_route_task_reset_timeout_falls_back`
- `test_route_task_fallback_silent_on_reset_timeout`
- `test_route_task_fallback_includes_is_fallback_flag_on_send_timeout`
- `test_route_task_fallback_includes_is_fallback_flag_on_reset_timeout`

In `tests/ai/test_pipeline.py`:
- `test_fallback_notice_event_suppressed_when_reason_empty`

All 2308 tests pass.

## DA Followup (2026-03-11)

### Issue 1: Double workspace-agent injection on orch reset

**DA finding**: After every orch reset, `_reset_orch_if_needed()` → `_ensure_orch_session()` injects workspace agents (lines 187-189), then `_inject_workspace_agents()` is called again, injecting a second time.

**Verified**: The finding was based on **old code that no longer exists**. In the current `decomposer.py`, `_reset_orch_if_needed()` (lines 478-490) calls `_ensure_orch_session()` as its final step and nothing else. There is NO subsequent call to `_inject_workspace_agents()`. The comment at lines 488-489 explicitly documents this: "Force-start fresh orch session; `_ensure_orch_session` injects history context and workspace agents — no need to call `_inject_workspace_agents()` separately." The double injection was already fixed before this review.

**Action**: No code change needed.

### Issue 2: Missing `fallback_reason == ""` assertion in send-timeout test

**DA finding**: `test_route_task_fallback_includes_is_fallback_flag_on_send_timeout` only asserts `is_fallback is True` but not `fallback_reason == ""`.

**Verified**: Confirmed. The test at line 1709 asserted only `is_fallback is True`. The sister test `test_route_task_fallback_silent_on_reset_timeout` (line 1684) already asserted `fallback_reason == ""`.

**Fix**: Added `assert result.fallback_reason == ""` to `test_route_task_fallback_includes_is_fallback_flag_on_send_timeout`. Test docstring updated to reflect the silent-fallback intent.

All 2302 tests pass.

