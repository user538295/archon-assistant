# Devil's Advocate Review — Background Execution & Planning
**Reviewer**: DA-2
**Date**: 2026-03-08
**Files reviewed**:
- `archon/ai/background_agent_manager.py`
- `archon/ai/agent_plan.py`
- `archon/ai/plan_executor.py`
- `archon/ai/cron_scheduler.py`
- `archon/ai/archon_mcp_server.py`

**Cross-reference context**:
- `archon/ai/claude_session.py`
- `CLAUDE.md`

---

## Executive Summary

The background execution layer has a systemic resource-leak pattern: `session.stop()` is never called on the happy path in `_run_agent()` because the `await session.stop()` call at line 378 is placed *inside the success branch but outside the finally block*, meaning any exception after the `for` loop but before that line will leak a live SDK connection. The cron scheduler uses `asyncio.get_event_loop()` (deprecated in Python 3.10+) and spawns fire-and-forget tasks via `asyncio.create_task()` with no reference retention, silently dropping all job exceptions without notifying the operator. The MCP server performs zero authentication on the `user_id` path parameter, meaning any process on localhost can spawn agents for any user ID. The plan executor contains a silent double-validation redundancy (calls both `validate_dependency_graph` and `topological_sort` which independently detect cycles), and the wave execution loop will deadlock indefinitely if a spawned agent never sets its `done` event due to a panic in `_run_agent`'s `finally` block. Collectively, these are not cosmetic issues — three of the five modules have paths that can corrupt runtime state or leak resources under normal failure conditions.

---

## Critical Findings (Severity: CRITICAL)

### [background_agent_manager.py:378] Session not stopped on success path — SDK connection leak

**Description**: In `_run_agent()`, `await session.stop()` at line 378 is placed in the `try` block *after* the inner `try/finally` for the event loop and *before* the `except asyncio.CancelledError` and `except Exception` branches. The `else:` clause at line 440 calls `_release_name()`. This means: if `session.stop()` itself raises an exception (e.g., the SDK disconnects unexpectedly), control jumps to `except Exception`, which calls `session.stop()` *again* (line 436), but the original `session` is now in an undefined state. More critically, the `finally: run.done.set()` at line 443 always fires — but the SDK client referenced by `session._client` is never `.disconnect()`-ed. The `stop()` method in `claude_session.py` calls `client.disconnect()` which is the only way to close the anyio/subprocess handle.

**Impact**: Every successfully completed background agent that has `session.stop()` raise an exception leaks a live Claude subprocess + file descriptors. Under load (many agents completing), this exhausts process table limits.

**Evidence**:
```python
# line 351-378: the inner try/finally closes the log, but session is still open
try:
    async for event in session.send(prompt):
        ...
finally:
    if self._agent_logger is not None:
        self._agent_logger.record_event(SubagentStopped(...))
await session.stop()   # ← OUTSIDE the finally; if this raises, leak occurs

run.status = "completed"
```

**Fix**: Wrap `session.start()` and the entire body in an outer `try/finally` that unconditionally calls `session.stop()`. The `except CancelledError` and `except Exception` branches already attempt `session.stop()`, but the success path (`else:`) does not guard against `session.stop()` failure.

---

### [archon_mcp_server.py:140-144] No authentication on user_id path parameter — arbitrary agent spawning

**Description**: The `_handle_post` handler extracts `user_id` directly from the URL path (`/mcp/{user_id}`) with no verification that the caller is authorized for that user ID. Any process on localhost (or any machine if the server is bound to `0.0.0.0`) can POST to `/mcp/12345` and spawn an unlimited number of agents billed to user 12345's session, bypassing the Telegram whitelist entirely.

**Impact**: A malicious local process or a misconfigured port exposure can spam agent spawns for any user, exhausting the `max_parallel` limit for all users simultaneously, or causing arbitrary code execution via crafted task prompts injected into agent sessions.

**Evidence**:
```python
# line 140-144
user_id_str = request.match_info.get("user_id", "0")
try:
    user_id = int(user_id_str)
except ValueError:
    user_id = 0
# No check: is this user_id in the allowed_user_ids list?
```

**Fix**: The MCP server must be constructed with `allowed_user_ids: list[int]` and must validate that the path `user_id` is in that list before dispatching to `_handle_tools_call`. A missing validation here renders the Telegram whitelist middleware meaningless for the spawn pathway.

---

### [cron_scheduler.py:200-202] Fire-and-forget cron tasks lose all exceptions silently

**Description**: `asyncio.create_task(self._run_job(job), ...)` at line 200-202 creates an untracked task. When `_run_job` raises an unhandled exception (which can happen if `_broadcast` raises, or any internal logic panics), the exception is silently suppressed by Python's asyncio machinery and only printed to stderr if the task's `__del__` is called without a result being retrieved. No reference to the task is retained anywhere. The `except Exception` at line 282 only catches errors *inside* `_run_job`'s try block — any exception raised *after* `status.is_running = False` in the `finally` (line 288-289) escapes to the untracked task.

**Impact**: Cron job failures can be entirely invisible in logs. Specifically: if `asyncio.create_task` fires a job that raises after the `finally` block (e.g., in `_broadcast`), the asyncio event loop will log "Task exception was never retrieved" to `stderr`, bypassing the configured log file entirely.

**Evidence**:
```python
# line 200-202: no reference stored, no done callback registered
asyncio.create_task(
    self._run_job(job), name=f"cron-{job.name}"
)
```

**Fix**: Either store the task reference in a set (with a `done_callback` that removes it and logs exceptions), or use `asyncio.shield` + `ensure_future` with explicit exception handling. At minimum, add `task.add_done_callback(lambda t: t.exception() and logger.error(...))`.

---

### [plan_executor.py:102-105] Indefinite wait on `done` event — potential deadlock

**Description**: `await asyncio.gather(*[run.done.wait() for _, run in wave_runs])` at lines 102-105 waits forever. If a background agent's asyncio task panics in the `finally: run.done.set()` block of `_run_agent` (e.g., if `asyncio.Event.set()` itself raises — theoretically impossible but defensive coding requires it), the `done` event is never set and the wave wait blocks forever, hanging the entire `PlanExecutor` task indefinitely.

More practically: if `spawn()` raises `RuntimeError` (max parallel exceeded) for one agent in a multi-agent wave, the partial `wave_runs` still blocks on `.done.wait()` for the successfully spawned agents while the plan never notifies about the failed spawn. There is no timeout on the gather.

**Impact**: PlanExecutor task hangs indefinitely, holding the user's session without any feedback. The main session remains functional but no plan completion notification is ever sent.

**Evidence**:
```python
# line 102-105: no timeout, no cancellation path
if wave_runs:
    await asyncio.gather(
        *[run.done.wait() for _, run in wave_runs]
    )
```

**Fix**: Wrap the gather in `asyncio.wait_for(..., timeout=<configurable_max>)` with a fallback notification. Alternatively, use `asyncio.wait()` with `return_when=asyncio.ALL_COMPLETED` plus a per-agent timeout.

---

## High Severity Findings

### [background_agent_manager.py:192-209] TOCTOU race on max_parallel check

**Description**: `list_running(user_id)` is called at line 192 to check if the user has hit `max_parallel`. Then the agent is created and added to `self._runs` at line 209. Between these two points, if two concurrent `spawn()` calls arrive for the same user, both can pass the check before either writes to `self._runs`, allowing `max_parallel + N` concurrent agents where N is the number of concurrent spawns. In asyncio this is only a problem if there's an `await` between the check and the write — and there is none here, so in *practice* this is safe. However, `await self._notify_spawn(run)` at line 231 is called *after* the task is already started, meaning a user can trigger further spawns between the task creation and the notification, and the count may momentarily exceed the limit without error.

**Impact**: Under rapid concurrent Telegram message handling or MCP calls, a user can exceed `max_parallel` transiently.

**Evidence**:
```python
# line 192-209
running = self.list_running(user_id)      # check
if len(running) >= self._max_parallel:
    raise RuntimeError(...)
...
self._runs[run_id] = run                  # write (no await between check and write)
run._task_ref = asyncio.create_task(...)  # task starts
```

**Fix**: Since asyncio is single-threaded, the check-and-write without an intervening `await` is actually atomic. The real concern is that `_task_ref` starts before `_notify_spawn` completes; the task could immediately complete and call `_release_name` before the spawn notification is sent, but this is a cosmetic ordering issue, not a correctness bug. Documenting this assumption is the minimum fix.

---

### [cron_scheduler.py:291-326] Command injection via unsanitized shell command in `_run_tool`

**Description**: `shlex.split(command)` at line 300 partially mitigates shell injection (by preventing shell metacharacter expansion), but `command` itself originates from the TOML config file. If the TOML file is writable by another process or loaded from an untrusted location, an attacker can inject arbitrary executables. More importantly, `shlex.split` does NOT prevent path traversal or execution of arbitrary binaries — it only prevents shell operator injection (`&&`, `|`, `;`). There is no allowlist validation of what commands are permitted.

**Impact**: Any process with write access to the TOML job files can execute arbitrary binaries as the Archon daemon user.

**Evidence**:
```python
# line 300-307
cmd = shlex.split(command)  # no allowlist check
proc = await asyncio.create_subprocess_exec(*cmd, ...)
```

**Fix**: This is by design (cron jobs execute arbitrary commands). The risk is fully accepted if the jobs directory has correct permissions (`chmod 700`, owned by the daemon user). The gap is there is no documentation of this assumption and no permission check at load time.

---

### [cron_scheduler.py:364] `asyncio.get_event_loop()` — deprecated in Python 3.10+

**Description**: `await asyncio.get_event_loop().run_in_executor(None, job_file.read_text)` at line 364. `asyncio.get_event_loop()` is deprecated since Python 3.10 and raises `DeprecationWarning`; in Python 3.12+ (the project's minimum version per `CLAUDE.md`) it emits `DeprecationWarning` when there is no running loop in the current OS thread. Inside a running coroutine, `asyncio.get_event_loop()` returns the running loop correctly, so this is non-breaking today, but it generates a `DeprecationWarning` on some Python 3.12 builds.

**Impact**: Generates warnings in production logs, violating the project convention "ALWAYS resolve all warnings". Should use `asyncio.get_running_loop()` instead.

**Evidence**:
```python
# line 364
content = await asyncio.get_event_loop().run_in_executor(None, job_file.read_text)
```

**Fix**: Replace with `asyncio.get_running_loop().run_in_executor(...)` or simply `await asyncio.to_thread(job_file.read_text)`.

---

### [plan_executor.py:85-93] `spawn()` exceptions inside wave loop are unhandled — plan aborts silently

**Description**: If `self._bam.spawn()` raises a `RuntimeError` (max parallel exceeded, or any other error), the exception propagates up through `_execute_plan()` and is caught by the outer `except Exception` in `execute()` at line 55-57, which sends "Plan execution failed unexpectedly." The user gets no indication of which agents were spawned successfully, which were skipped, and no cleanup of the already-running wave agents is performed.

**Impact**: If wave 1 spawns 3 agents and wave 2's first spawn fails due to max_parallel, the user gets a generic error message and the 3 already-running wave 1 agents continue running without any coordination. The plan is orphaned.

**Evidence**:
```python
# line 87-93: RuntimeError from spawn() escapes the wave loop
run = await self._bam.spawn(
    user_id=self._user_id,
    task=task_prompt,
    context=self._context_summary,
    user_request=plan.summary,
)
```

**Fix**: Wrap `self._bam.spawn()` in a try/except inside the wave loop, add the task to `failed_ids` on spawn failure, and continue processing the remaining tasks in the wave.

---

### [background_agent_manager.py:408-422] `CancelledError` swallows `session.stop()` errors

**Description**: In the `except asyncio.CancelledError` branch (lines 408-422), `session.stop()` is called inside a bare `except Exception: pass` block (lines 419-421). If `session.stop()` raises, the error is silently swallowed. The `CancelledError` is then re-raised at line 422. This is correct for `CancelledError` propagation, but the silent swallow of `session.stop()` errors means the SDK subprocess may not be cleaned up correctly.

**Impact**: Cancelled agent leaves a zombie Claude subprocess if `session.stop()` raises during cancellation.

**Evidence**:
```python
# line 418-421
try:
    await session.stop()
except Exception:
    pass  # ← swallowed, no logging
```

**Fix**: Log the exception at WARNING level: `except Exception: logger.warning("session.stop() failed during cancellation of agent %r", run.name, exc_info=True)`. The `pass` violates the project convention of not silently swallowing exceptions.

---

### [agent_plan.py:85-89] `queue.pop(0)` in Kahn's algorithm — O(n²) performance

**Description**: `validate_dependency_graph` uses `queue.pop(0)` at line 89, which is O(n) for a list. For a plan with n tasks, the full Kahn's algorithm becomes O(n²). For large plans this is a minor performance issue. The `topological_sort` function at line 128 uses a list but replaces `queue` entirely each iteration (`queue = next_queue`) so it avoids `pop(0)` and is O(n).

**Impact**: Performance degradation on large plans (>50 agents). The code duplication between `validate_dependency_graph` and `topological_sort` is a more significant SOLID violation — the same graph traversal logic is implemented twice with slightly different data structures.

**Evidence**:
```python
# agent_plan.py line 87-93: validate_dependency_graph
queue = [aid for aid, deg in in_degree.items() if deg == 0]
visited = 0
while queue:
    node = queue.pop(0)  # ← O(n) list pop
```

**Fix**: Use `collections.deque` with `popleft()`. More importantly, `validate_dependency_graph` should call `topological_sort` in a try/except rather than re-implementing the cycle detection.

---

### [plan_executor.py:59-67] Double cycle detection — redundant and contradictory error handling

**Description**: `_execute_plan` calls `validate_dependency_graph(plan)` at line 63, and then calls `topological_sort(plan)` at line 67. Both independently detect cycles. If `validate_dependency_graph` returns `False`, the method returns early. But `topological_sort` raises `ValueError` on cycles — and since `validate_dependency_graph` already checked, this `ValueError` can never be raised in practice. The outer `except Exception` in `execute()` would catch it anyway, but the behavior is inconsistent: one path gives a user-visible "invalid dependencies" message, the other gives "Plan execution failed unexpectedly."

**Impact**: The user-facing error message for a cycle depends on which code path detects it first. The logic is duplicated and the `ValueError` path in `topological_sort` is dead code in this usage.

**Evidence**:
```python
# line 63-67
if not validate_dependency_graph(plan):
    await self._notify("❌ Plan has invalid dependencies...")
    return

waves = topological_sort(plan)  # also raises ValueError on cycle — dead code path
```

**Fix**: Remove `validate_dependency_graph` call from `_execute_plan`. Wrap `topological_sort` in try/except `ValueError` and send the user-visible error message there. Alternatively, keep `validate_dependency_graph` but have it call `topological_sort` internally.

---

## Medium Severity Findings

### [cron_scheduler.py:189-203] `_loop` loses all tick exceptions silently; scheduler can die silently

**Description**: `_loop()` at line 189-203 has no exception handler around the tick body. If `self._should_fire()` or `asyncio.create_task()` raise an unexpected exception (e.g., `RuntimeError` from an asyncio state issue), the entire `_loop` coroutine propagates the exception to the untracked task and dies. Since no reference to `self._task` is held by any watchdog, the scheduler silently stops scheduling jobs with no operator notification.

**Impact**: After a single unexpected exception in the scheduler tick, all cron jobs stop running permanently until daemon restart. This could go unnoticed for days.

**Evidence**:
```python
# line 189-203
async def _loop(self) -> None:
    while True:
        now = datetime.now(timezone.utc).astimezone()
        for job in self._config.jobs:  # any exception here kills the loop
            ...
        await asyncio.sleep(60)
```

**Fix**: Wrap the tick body in `try/except Exception: logger.exception(...)` to prevent transient errors from killing the loop.

---

### [cron_scheduler.py:205-238] `_should_fire` timezone comparison mixes aware/naive datetimes

**Description**: When `job.timezone` is set, `prev_aware` is a timezone-aware datetime in the job's timezone. It is then converted to the system timezone: `prev = prev_aware.astimezone()` at line 226. The `status.last_fire_at` is always stored as a system-local timezone-aware datetime (set at line 199: `self._statuses[job.name].last_fire_at = now` where `now = datetime.now(timezone.utc).astimezone()`). The comparison `status.last_fire_at >= prev` at line 233 compares two timezone-aware datetimes in different explicit timezones — Python normalizes these to UTC for comparison, so the comparison is correct. However, if `croniter.get_prev()` returns a naive datetime (which some versions of croniter do for timezone-aware inputs), the comparison raises `TypeError`.

**Impact**: If croniter returns a naive datetime from a timezone-aware input, the entire `_should_fire` method raises `TypeError`, which is caught by the broad `except Exception` at line 236 and returns `False` silently — all jobs with timezone settings stop firing permanently without any notification.

**Evidence**:
```python
# line 219-233
tz = ZoneInfo(job.timezone)
tz_now = datetime.now(tz)
it = croniter(job.schedule, tz_now)
prev_aware: datetime = it.get_prev(datetime)  # croniter may return naive datetime
if (tz_now - prev_aware).total_seconds() >= 60:  # TypeError if prev_aware is naive
```

**Fix**: After `get_prev()`, explicitly check `prev_aware.tzinfo` and attach the timezone if missing: `if prev_aware.tzinfo is None: prev_aware = prev_aware.replace(tzinfo=tz)`.

---

### [background_agent_manager.py:281-288] Name pool not released on successful completion until `else` clause

**Description**: The `else` clause of the `try` block (line 440-441) releases the name via `self._release_name(run.name)`. The `else` clause runs only when the `try` block completes without exception, and only *after* `_notify_success` and context injection calls. If `_notify_success` raises (e.g., Telegram API failure), control jumps to `except Exception` (line 424), which also calls `_release_name`. So the name IS released in both failure paths. However, there is a window between `run.status = "completed"` (line 380) and `_release_name()` (line 441) where `list_running()` correctly returns 0 (status is no longer "running") but `_active_names` still contains the name. This means a concurrent `_assign_name()` call could still see the name as unavailable and generate a suffixed fallback name unnecessarily.

**Impact**: Low-probability cosmetic issue — agents briefly appear with suffixed names during high-concurrency scenarios.

**Fix**: Move `_release_name(run.name)` to immediately after `run.status = "completed"` rather than in the `else` clause.

---

### [archon_mcp_server.py:149] Exception handler catches `json.JSONDecodeError` redundantly

**Description**: `except (json.JSONDecodeError, Exception)` at line 149 is redundant — `json.JSONDecodeError` is a subclass of `ValueError` which is a subclass of `Exception`. The `json.JSONDecodeError` in the tuple adds no value.

**Evidence**:
```python
# line 149
except (json.JSONDecodeError, Exception):
```

**Fix**: Use `except Exception:` only.

---

### [background_agent_manager.py:351-377] Inner `try/finally` swallows `CancelledError` from `session.send()`

**Description**: The inner `try/finally` at lines 351-377 wraps the `async for event in session.send(prompt)` loop. If `session.send()` or the event iteration is cancelled while the inner finally block is executing (writing `SubagentStopped`), the `CancelledError` is suppressed by the finally clause until the outer `except asyncio.CancelledError` at line 408 catches it. The `agent_logger.record_event(SubagentStopped(...))` in the inner finally uses `result = ""` because no `Response` event was yielded yet. This is correct behavior, but the `result` variable assigned at line 363 (`if isinstance(event, Response): result = event.content`) will be empty even if partial results were received before cancellation, because `result` is reset at line 335.

**Impact**: On agent cancellation, the log always records `final_result=""` even if the agent produced partial output before cancellation. The log is misleading.

**Fix**: Initialize `result = ""` but track partial results separately — save the most recent non-empty `Response.content` as `result` regardless of whether the iteration completed.

---

### [plan_executor.py:97-99] `WaveStarted` recorded before agents are actually running

**Description**: `self._record_event(WaveStarted(...))` at line 99 is called *after* all agents in the wave are spawned. But `spawn()` is async — by the time the last `spawn()` returns, the first agent's task may have already been scheduled and even started executing. The `WaveStarted` event is technically accurate (the wave was started), but its position in the code after all spawns means it fires after all agents are already queued, not before.

**Impact**: Minor ordering issue in history logs. `WaveStarted` is recorded after `SubagentStarted` events from the agents themselves, inverting the expected timeline in the log.

**Fix**: Record `WaveStarted` before the wave's `spawn()` loop begins.

---

### [cron_scheduler.py:246-249] Overlapping run guard uses mutable state without async synchronization

**Description**: `status.is_running` at line 247 is checked and set synchronously (lines 247, 259). Since asyncio is single-threaded, this is safe — no preemption between the check and the set. However, `status.is_running = True` is set *before* `status.last_run` and `status.run_count` at lines 260-261. If the scheduler tick fires again before `_run_job` exits (which happens if `_run_job` takes more than 60 seconds and the tick fires again), the guard correctly prevents re-entry. But the guard reads `status.is_running` inside `_run_job` itself, which means there's no way for the scheduler to distinguish a normally-running job from a hung job — both block future fires indefinitely.

**Impact**: A hung job (e.g., a slow `_run_prompt` step that doesn't respect `timeout_seconds`) blocks all subsequent fires of that job permanently without any notification that the job is hung. The `timeout_seconds` parameter should prevent this, but if `asyncio.wait_for` itself hangs (due to a blocked event loop), the timeout doesn't fire.

**Fix**: Add a `last_fire_at` vs `is_running` age check — if `is_running` is True and the job has been running for more than `N * timeout_seconds`, log a warning and reset `is_running`.

---

## Low Severity / Style Issues

### [agent_plan.py:12-16] `AgentTask.depends_on` as `list[str]` in frozen dataclass with `slots=True`

**Description**: `depends_on: list[str] = field(default_factory=list)` on a frozen, slotted dataclass means the list is mutable even though the dataclass is frozen (the slot itself can't be reassigned, but the list contents can be mutated). This is a well-known Python footgun. No code currently mutates `depends_on`, but it's an invitation for future bugs.

**Fix**: Use `tuple[str, ...]` instead of `list[str]` for a truly immutable dependency list.

---

### [background_agent_manager.py:209-213] Task created before `_runs[run_id]` is registered atomically

**Description**: `self._runs[run_id] = run` at line 209, then `run._task_ref = asyncio.create_task(...)` at line 210. If `asyncio.create_task()` raises (theoretically possible if the event loop is closing), `run` is in `_runs` but `_task_ref` is `None`, and `list_running()` will include this orphaned run permanently.

**Fix**: Assign `_task_ref` first, then register in `_runs`.

---

### [cron_scheduler.py:149-186] `reload_jobs()` is synchronous but modifies shared mutable state

**Description**: `reload_jobs()` is a synchronous method that modifies `self._config.jobs` and `self._statuses` while the async `_loop()` may be iterating over `self._config.jobs`. In asyncio this is safe (no concurrent modification without awaits), but the method is exposed as public API and could be called from a context that interleaves with the loop.

**Fix**: This is safe as-is in single-threaded asyncio, but add a comment documenting the threading model assumption.

---

### [archon_mcp_server.py:190-192] `_handle_tools_call` raises `_RpcError` for unknown tool, but `_ToolError` is never used

**Description**: `_ToolError` exception class is defined at lines 233-236 but is never raised anywhere in the codebase. It was presumably intended for tool-level errors that should return `isError: true` in the JSON-RPC response, but all tool errors are currently returned as inline dicts (line 210-213) or as `_RpcError`. The `except _ToolError` handler at line 162 is dead code.

**Fix**: Remove `_ToolError` and its handler, or use it consistently for all tool-level errors.

---

### [plan_executor.py:121-133] Final summary counts do not account for cancelled agents

**Description**: The final summary at lines 122-126 counts `succeeded`, `failed`, `skipped` but not `cancelled`. If an agent in a wave is cancelled (e.g., user cancels it via `/cancel` during plan execution), `run.status == "cancelled"` is neither "completed" nor "failed" nor in `skipped_ids`. The summary will undercount — `succeeded + failed + skipped < total` with no explanation.

**Fix**: Add `cancelled = sum(1 for r in runs.values() if r.status == "cancelled")` and include it in the summary if > 0.

---

### [background_agent_manager.py:92-112] `_agent_status_text` uses f-string concatenation instead of `html.escape` on `word` parameter

**Description**: `word` parameter in `_agent_status_text` at line 112 is inserted directly into the HTML template without `html.escape()`. `word` comes from the `_AGENT_BEACON_WORDS` constant or the literal `"working"`, both of which are safe static strings. But the function signature accepts arbitrary `word: str = "working"`, so a future caller passing an HTML-containing string would cause injection.

**Fix**: Apply `html.escape(word)` in the format string. Low risk given current callers.

---

### [cron_scheduler.py:329-349] `_run_prompt` creates session with `cwd=None` in production

**Description**: `ClaudeSession(model=self._model)` at line 332 does not pass `cwd`. The background agent manager always passes `cwd=self._cwd`. For cron jobs that use `_prompt` steps to interact with project files, the session won't have the correct working directory, potentially causing tool calls to fail or produce incorrect results.

**Fix**: Pass `cwd=self._cwd` to `ClaudeSession` in `_run_prompt`.

---

## Untested Code Paths

1. **`_agent_beacon_task` beacon loop**: Tests exist for beacon configuration (`test_beacon_interval_defaults_to_two`, `test_beacon_interval_configurable`) but there is no test that actually runs `_agent_beacon_task` through multiple intervals to verify the `call_count` logic and word rotation.

2. **`session.stop()` raises in `_run_agent` success path**: No test covers the case where `session.stop()` raises after a successful run. The SDK connection leak scenario is untested.

3. **`PlanExecutor` with cancelled agents mid-wave**: No test for the plan summary when one agent in a wave is cancelled externally (via `bam.cancel()`) while the wave wait is in progress.

4. **`PlanExecutor.execute()` outer exception handler**: The test at `test_executor_crash_sends_error_notification` covers exceptions in `_execute_plan`, but not exceptions in `_execute_plan`'s `_notify()` calls themselves.

5. **`_run_tool` with `jobs_dir_base=None` and `cwd=None`**: `tool_cwd` becomes `None` (line 301: `str(self._jobs_dir_base) if ... else self._cwd`). `asyncio.create_subprocess_exec` with `cwd=None` inherits the process cwd. No test verifies this path; `test_run_tool_no_cwd_inherits_process_directory` tests this but uses `jobs_dir_base=None` without explicitly testing what happens when `self._cwd` is also `None`.

6. **`_should_fire` with DST transition**: No test covers a cron job scheduled at 2:30 AM during a DST spring-forward (that time doesn't exist) or fall-back (that time occurs twice). The `croniter` library handles this, but the behavior is untested.

7. **`reload_jobs()` called while a job is running**: No test verifies that `reload_jobs()` removing a job from `_config.jobs` while `_run_job` is actively running that job does not crash (it won't because `_run_job` has a local reference to `job`, but the `status` dict removal is untested in this race).

8. **`_build_task_prompt` with missing dependency run**: If `dep_id` is in `agent_task.depends_on` but not in `runs` (e.g., if the dependency was skipped), `runs.get(dep_id)` returns `None` and the upstream line is silently omitted. No test covers this path explicitly.

9. **`ArchonMCPServer` with `user_id=0`** (default fallback when `int()` fails): The user 0 is almost certainly not in `allowed_user_ids`, so any spawn call to an invalid user ID path falls through to the manager without error. No test verifies this.

10. **`_disable_invalid_job` with a non-existent TOML file**: `job_file.exists()` returns `False` at line 362, so the method silently returns without disabling on disk. The in-memory `job.enabled = False` is still set. This path is technically covered by the early return, but there's no test asserting that the job remains disabled in memory when the file doesn't exist.

---

## Convention Violations

1. **`cron_scheduler.py:364` — `asyncio.get_event_loop()` deprecated**: Project requires warning-free code (CLAUDE.md: "ALWAYS resolve all warnings"). Use `asyncio.get_running_loop()` or `asyncio.to_thread()`.

2. **`cron_scheduler.py:200-202` — fire-and-forget task with no reference**: Untracked asyncio tasks are an anti-pattern. The project architecture doc does not explicitly permit fire-and-forget in the scheduler; `PlanExecutor` documents that it "always runs as a detached asyncio task" by design, but the cron scheduler has no such documented justification.

3. **`plan_executor.py:63-67` — logic duplication**: `validate_dependency_graph` and `topological_sort` both implement Kahn's algorithm. The KISS principle (CLAUDE.md: "Simplicity is mandatory") is violated by maintaining two implementations.

4. **`agent_plan.py:85` — `queue.pop(0)` instead of `deque.popleft()`**: Suboptimal data structure choice for BFS/topological sort is inconsistent with the idiomatic Python in `topological_sort` (which avoids `pop(0)`).

5. **`background_agent_manager.py:419-421` — silent `except Exception: pass`**: CLAUDE.md requires logging, not silent swallowing. The `session.stop()` failure in the cancellation handler has `pass` with no logging.

6. **`cron_scheduler.py:332` — `ClaudeSession` without `cwd`**: Background agents pass `cwd`; cron prompt sessions do not. This is an inconsistency in how isolated sessions are constructed, violating the DRY/consistency conventions visible throughout the codebase.

7. **No test file for `cron_scheduler.py` in `tests/ai/`**: The cron scheduler tests are in `tests/cron/`, which is correct for project organization. However, there is no `test_cron_scheduler_e2e.py` analogous to `test_background_agent_e2e.py` and `test_plan_executor_e2e.py`. The cron integration test covers basic functionality but the DST, timezone, and duplicate-fire-prevention paths are not validated end-to-end.

---

## Overall Assessment

**Is this production-ready?** Conditionally. The core correctness of background agent lifecycle management is sound — the `done` event pattern, name pool management, and beacon task design are all well-structured. The test coverage for happy paths is comprehensive. However, three issues make this not production-ready without fixes:

**Top 3 must-fix items:**

1. **MCP server authentication (CRITICAL)**: Any local process can spawn agents for arbitrary user IDs. This bypasses the entire Telegram whitelist security model. This is a single-line fix (add `user_id not in allowed_user_ids` check) but its absence is a security hole.

2. **Session not stopped on success path exception (CRITICAL)**: If `session.stop()` raises after a successful agent run, the SDK subprocess leaks. Under sustained load this exhausts file descriptors. The fix is to restructure `_run_agent`'s try/finally to unconditionally stop the session.

3. **`_loop` and fire-and-forget tasks can die silently (CRITICAL + HIGH)**: The cron scheduler's tick loop and individual job tasks can all die silently. An exception in `_should_fire()` kills the entire scheduler loop with no notification. Job tasks that panic after their `finally` blocks produce "Task exception was never retrieved" messages to stderr that bypass the configured log file. For a production daemon, the scheduler dying silently is a severe operational reliability issue.
