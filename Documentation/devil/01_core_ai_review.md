# Devil's Advocate Review — Core AI Layer
**Reviewer**: DA-1
**Date**: 2026-03-08
**Files reviewed**:
- `archon/ai/claude_session.py`
- `archon/ai/pipeline.py`
- `archon/ai/event_mapper.py`
- `archon/ai/session_manager.py`
- `archon/ai/classification.py`
- `archon/ai/classifier.py`
- `archon/ai/decomposer.py`
- `archon/ai/prompts/__init__.py`

---

## Executive Summary

The core AI layer is structurally sound, but it harbours several real defects that would bite in production. The most dangerous is a `UnboundLocalError` lurking in the `send()` finally block when certain exceptions fire before `_user_message_queued` is assigned. The `os.environ.pop()` mutation in `start()` is a process-global race condition that will corrupt concurrent SDK sessions. `SessionManager._locks` leaks per-user `asyncio.Lock` objects indefinitely — every user who ever logs in accumulates a dead lock that never gets freed. In `pipeline.py`, a classifier error continues routing instead of aborting, meaning the user gets an error event *and* a full Decomposer response for the same broken classification. The `EventMapper` maintains per-instance tool-ID state that is shared across all turns of a session, causing IDs to grow without bound and the lookup dictionaries to grow proportionally — a slow but real memory leak for long-running sessions.

---

## Critical Findings (Severity: CRITICAL)

### [claude_session.py:332] `UnboundLocalError` in finally block when exception fires before try body

**Description**: `_user_message_queued` is set at line 238, inside the `try` block. If `self._connected` is `True` but an exception fires *before* line 238 is reached inside the try body — which is impossible today but is one innocent refactor away from being reachable — the `finally` block at line 332 references `_user_message_queued` and raises `UnboundLocalError`. More concretely: `_user_message_queued` is declared inside the `try`, not before it. Python resolves this at function scope so it will not crash today only because there is no codepath between `await self._send_lock.acquire()` (line 223) and `_user_message_queued = False` (line 238) that can raise. However, the lock is acquired *before* the try block (lines 223–227), meaning if an exception were added between lock-acquisition and the try body, the finally runs `_user_message_queued` as an unbound name and the lock is still released (line 340), but the process logs a confusing traceback.

**Impact**: In its current form, a latent `UnboundLocalError` risk. A more immediate consequence is that the variable should be initialised *before* the `try` block, adjacent to the other pre-try state mutations (`_processing`, `_last_send_at`, `_send_count`).

**Evidence**:
```python
# line 223-238 — lock acquired OUTSIDE try, _user_message_queued assigned INSIDE try
await self._send_lock.acquire()
self._processing = True
...
try:
    ...
    _user_message_queued = False   # line 238 — only reachable inside try

# line 332 — finally block references the variable unconditionally
if self._reminder is not None and _user_message_queued:
```

**Fix**: Move `_user_message_queued = False` to before the `try:` statement, alongside `self._processing = True`.

---

### [claude_session.py:182–187] `os.environ.pop()` is a process-global race condition

**Description**: `start()` mutates `os.environ` by popping `CLAUDECODE` before calling `await self._client.connect()`, then restores it in a `finally`. In Python, `os.environ` is a process-global singleton. If two `ClaudeSession.start()` calls run concurrently (e.g., two users' sessions being created simultaneously by `SessionManager.get_or_create`), both coroutines race on `os.environ`: one pops `CLAUDECODE`, the other sees it absent, both connect, then one re-inserts while the second's `finally` tries to do the same. The pop can also fail to restore if the event loop yields between pop and connect in a way that causes a second pop by another coroutine (resulting in `claudecode` being `None` in both, so the key is silently dropped).

**Impact**: A second SDK subprocess spawned by a concurrent `start()` may inherit `CLAUDECODE` (if it races before the pop) or may not (if the first `start()` already popped it). The environment is non-deterministically corrupted for child processes. On a multi-user deployment this is a real production bug.

**Evidence**:
```python
# lines 182-187
claudecode = os.environ.pop("CLAUDECODE", None)
try:
    await self._client.connect()    # can yield here; other coroutines run
finally:
    if claudecode is not None:
        os.environ["CLAUDECODE"] = claudecode
```

**Fix**: Use a module-level or class-level asyncio `Lock` to serialise `start()` calls, or pass the env-var suppression via the SDK's own subprocess options rather than mutating the process environment.

---

### [session_manager.py:117,121–122] `_locks` dictionary leaks forever — one `asyncio.Lock` per user, never cleaned up

**Description**: `_locks` is populated in `get_or_create()` (line 121) but never cleaned up in `stop()` (line 171) or `stop_all()` (line 181). Every user who ever creates a session accumulates a permanent entry in `_locks`. For a long-running daemon with rotating users (or a deployment that calls `/restart` per user), this is a slow memory leak. The `asyncio.Lock` object is small but the accumulation is unbounded.

**Impact**: Low memory cost per entry, but semantically wrong: after `stop(user_id)`, the user's lock remains, and a new `get_or_create` call for the same user reuses the old lock rather than creating a fresh one. This is correct behaviour by accident, but it also means `stop()` doesn't fully clean up.

**Evidence**:
```python
# session_manager.py:117
self._locks: dict[int, asyncio.Lock] = {}

# stop() at line 171 — no _locks cleanup:
async def stop(self, user_id: int) -> None:
    if user_id in self._timers:
        self._timers.pop(user_id).cancel()
    self._started_at.pop(user_id, None)
    session = self._sessions.pop(user_id, None)
    # _locks[user_id] is never removed

# stop_all() at line 181 — no _locks cleanup:
async def stop_all(self) -> None:
    for task in self._timers.values():
        task.cancel()
    self._timers.clear()
    # self._locks.clear() is missing
```

**Fix**: Add `self._locks.pop(user_id, None)` to `stop()` and `self._locks.clear()` to `stop_all()`.

---

## High Severity Findings

### [pipeline.py:112–123] Classifier error does not abort routing — user gets ErrorEvent *and* a Decomposer response

**Description**: When `result.error` is non-empty (line 112), an `ErrorEvent` is yielded, but execution continues unconditionally to yield the `ClassificationEvent` and proceed through the full routing algorithm. The intent is still read from `result.classification` (which defaults to `task, 0.0` on any error), so the user sees an error banner immediately followed by a decomposer response as if nothing went wrong.

**Impact**: Confusing UX: the user sees an error message but Claude still answers. More importantly, the error is silently swallowed in the routing path — the system presents a degraded classification as authoritative.

**Evidence**:
```python
# pipeline.py lines 112-127
if result.error:
    yield ErrorEvent(message=result.error, source="pipeline")

yield ClassificationEvent(...)   # always yields, regardless of error
# ... routing continues with default classification (task, 0.0 confidence)
```

**Fix**: After yielding `ErrorEvent` when `result.error` is set, either `return` early or proceed with an explicit fallback label so downstream code is aware the classification is degraded.

---

### [event_mapper.py:203–211] `EventMapper` tool-ID dictionaries grow without bound across turns

**Description**: `_tool_id_map` (str→int) and `_tool_name_map` (int→str) are per-`EventMapper` instance and never cleared. Each call to `_alloc_tool_id()` adds an entry. `EventMapper` is created once per `ClaudeSession` and lives for the session's lifetime, so in a long-running session with many tool calls, both dictionaries grow without a cap. `_next_id` is an unbounded integer counter. For typical sessions this is negligible, but for background agents that run hundreds of tool calls, it is a real leak.

**Impact**: Slow memory growth for long-lived sessions or heavy-tool-use agents. No functional correctness issue, but violates KISS and the stated memory-efficiency goals of the project.

**Evidence**:
```python
# event_mapper.py lines 204-211
def __init__(self) -> None:
    self._next_id = 0
    self._tool_id_map: dict[str, int] = {}      # grows forever
    self._tool_name_map: dict[int, str] = {}    # grows forever

def _alloc_tool_id(self, sdk_id: str) -> int:
    self._next_id += 1
    self._tool_id_map[sdk_id] = self._next_id   # never evicted
    return self._next_id
```

**Fix**: Use an LRU cache or a bounded deque (e.g., keep only the last 100 tool call pairs), or reset the maps per "response round" (between `query()` calls).

---

### [decomposer.py:96–100] `_orch_session` has no tools restriction and no system prompt — full tool access in JSON-generation mode

**Description**: The orchestration session (`_orch_session`) is constructed with only `max_turns=1` (line 99). It has no `tools=[]` restriction, meaning it inherits the SDK default (all tools enabled). Orchestration calls (`review()`, `route_task()`) are intended purely for JSON generation and should never invoke tools, but the session is configured to allow them. If the LLM decides to call a tool mid-orchestration, the `_orch_session` will do so, emit ToolStarted/ToolResult events that are silently discarded (only `Response` events are captured), potentially run filesystem tools in the user's workspace, and return an empty `raw_response`.

**Impact**: Silent tool execution during orchestration with no user visibility. The filesystem could be modified during a JSON classification call. Falls back to the default classification silently.

**Evidence**:
```python
# decomposer.py lines 96-100
self._orch_session = ClaudeSession(
    cwd=cwd,
    model=model,
    max_turns=1,   # only restriction — no tools=[], no system_prompt forbidding tool use
)
```

**Fix**: Add `tools=[]` to `_orch_session` to disable all tool access, the same way `_summary_session` correctly does at line 107.

---

### [decomposer.py:401–402] `_pending_turns.popleft()` uses `len(snapshot)` as pop count but snapshot may be stale

**Description**: After summarization, the code pops `min(len(snapshot), len(self._pending_turns))` entries from `_pending_turns` (line 401). `snapshot` was built from `list(self._pending_turns)` at line 375. However, new turns may have been appended to `_pending_turns` *during* the async `_summary_session.send()` call (line 396). The pop count is `len(snapshot)` which correctly equals the number of turns that were summarised. But if new turns arrived *between* `list(self._pending_turns)` and the pop loop, `popleft()` removes exactly the right items — the snapshot items — only if no items were prepended or reordered, which `deque.popleft()` guarantees (FIFO). So this is safe. **However**, if `_pending_turns` received new items and its current length is less than `len(snapshot)` (impossible since the deque only grows via `append`), the `min()` guard prevents over-popping. The actual bug is: if `len(self._pending_turns) < len(snapshot)` at pop time, which can only happen if something else is `popleft()`-ing concurrently. There is no such concurrent consumer currently, but the logic is subtler than it looks and relies on single-threaded asyncio ordering guarantees that are not documented in the code.

**Impact**: Correctness depends on undocumented asyncio single-task ordering guarantee. Any future refactor that makes `_refresh_summary` reentrant would break this.

**Fix**: Document the invariant explicitly, or capture the exact turns to remove in `snapshot` and remove them by identity/index rather than count.

---

### [claude_session.py:244–260] Reminder injection turn yields `ReminderInjectedEvent` even when `receive_response()` raises

**Description**: The reminder injection block (lines 244–260) drains `receive_response()` in a `async for` loop and then yields `ReminderInjectedEvent`. If `receive_response()` raises mid-iteration (e.g., SDK disconnection), the exception propagates out of the `async for`, bypasses the `yield ReminderInjectedEvent` at line 258, and falls into the `finally` block. But `_user_message_queued` is still `False` at that point, so `record_message()` is correctly skipped. This part is fine. The problem is that a partial `receive_response()` drain (exception after some messages but before `ResultMessage`) means `_cumulative_cache_creation` may be partially incremented from a turn that the SDK considers failed, with no way to roll back the increment.

**Impact**: `_cumulative_cache_creation` can be inflated by a failed reminder turn, causing the context window progress bar to advance spuriously. Low severity in isolation, but misleading diagnostics.

---

## Medium Severity Findings

### [session_manager.py:119–143] TOCTOU race between lock check and lock creation in `get_or_create()`

**Description**: Lines 121–122 check `if user_id not in self._locks` and then assign `self._locks[user_id] = asyncio.Lock()`. If two coroutines for the same `user_id` race at this exact point (both see the key absent, both create a lock), the second overwrite wins and the first lock is discarded. The inner `async with self._locks[user_id]` at line 123 then acquires whichever lock was last written, and the two coroutines may both proceed past it. The existing test `test_concurrent_get_or_create_does_not_double_start` (line 403 of test file) passes only because `asyncio.sleep(0)` in the mock lets the first coroutine complete before the second reads `self._locks` — the test does not exercise the true race window.

**Impact**: In theory, two concurrent `get_or_create` calls for a new user can both create and `start()` a session. `session.start()` is not idempotent — calling it twice on the same `ClaudeSDKClient` is undefined behaviour.

**Fix**: Use `self._locks.setdefault(user_id, asyncio.Lock())` (atomic in CPython due to GIL) to eliminate the TOCTOU window.

---

### [pipeline.py:106] `send()` typed as `AsyncGenerator` return but declared as `async def` — incorrect type signature

**Description**: `async def send(self, prompt: str) -> AsyncGenerator[Event, None]` (pipeline.py:106) is declared with a return type of `AsyncGenerator[Event, None]`. An `async def` containing `yield` is an `AsyncGenerator`, but the declared return annotation should be `AsyncGenerator[Event, None]` for the *type of the generator object*, which is correct syntactically but misleading: calling `pipeline.send(...)` returns a coroutine that must be awaited OR an async generator that must be iterated — and the caller pattern `async for event in pipeline.send(prompt):` does the latter. The annotation is technically correct but `mypy` may flag the return type as incompatible in some versions. More importantly, `ClaudeSession.send()` (claude_session.py:208) has the same signature, and both duck-type each other, but the type system cannot verify that they are actually substitutable without a formal protocol.

**Impact**: Type-checking gaps; if a caller awaits `send()` instead of iterating it, the error only surfaces at runtime.

---

### [decomposer.py:167] `context_block` string when `context` is empty produces a spurious double-newline

**Description**: Line 167: `context_block = f"\n\n{context}\n\n" if context else "\n\n"`. When `context` is empty the fallback is `"\n\n"` — a literal two-newline separator still injected into the prompt. This means orchestration prompts always contain a double-newline between the `[INTERNAL:]` prefix and the `review_prompt`, even when there is no context to inject. Minor but produces unnecessarily padded prompts that consume tokens.

**Evidence**:
```python
# decomposer.py line 167
context_block = f"\n\n{context}\n\n" if context else "\n\n"
```

---

### [classification.py:36–80] `extract_json_object()` does not handle single-quoted strings or nested arrays

**Description**: The JSON extractor's string-tracking logic (lines 58–79) only tracks `"` (double-quote) delimiters. JSON does not use single quotes, so this is technically correct per the spec. However, LLM outputs sometimes include JSON-like structures with single quotes, and the extractor will misparse them (treating `'` as regular characters, `"` as string delimiters within them). More concretely, the `escape_next` logic (line 63–66) processes backslash regardless of whether it's inside a string — it sets `escape_next = True` even outside strings when `ch == "\\"`. This means a backslash in JSON object keys (rare but possible in LLM output) is consumed and the next character skipped, potentially causing the depth counter to miscount a closing brace.

**Impact**: `extract_json_object()` may fail to extract valid JSON in edge cases, falling back to the default `task` classification. Low frequency but non-zero.

**Evidence**:
```python
# classification.py lines 63-66
if ch == "\\":
    if in_string:
        escape_next = True
    continue   # BUG: backslash is consumed even outside strings
```

**Fix**: Only consume backslash (set `escape_next`) when `in_string` is `True`.

---

### [session_manager.py:73–76] Custom `session_factory` signature wrapping silently ignores `user_id`

**Description**: When a custom `session_factory` is provided (for testing), the manager wraps it: `lambda c, uid: session_factory(c)` (line 75). The `uid` parameter is discarded. This is intentional (legacy factory signature), but the wrapping is not documented, and if any test passes a factory that *needs* the `user_id`, it silently gets `None`-equivalent behaviour. The `_default_factory` at line 78 correctly receives `uid: int | None`. The type annotation for `self._factory` on line 74 says `Callable[[str | None, int | None], ClaudeSession]` but the lambda from line 75 takes `(c, uid)` from the outer scope, shadowing the type annotation with a lambda that calls `session_factory(c)` (one arg). This works but is fragile.

---

### [decomposer.py:120–135] `_inject_workspace_agents()` reads disk synchronously in an async context

**Description**: `agents_path.read_text()` (line 126) is a blocking filesystem call executed synchronously inside an `async def start()` method (line 114). For most deployments the file is local and small, so it completes in microseconds. But if `cwd` is on a network mount or slow storage, this call blocks the event loop for an unbounded duration.

**Impact**: Event loop stall on network filesystems. Violates asyncio best practices (blocking I/O in coroutines).

**Fix**: Use `asyncio.to_thread(agents_path.read_text, encoding="utf-8")`.

---

### [pipeline.py:152] Routing branch: `estimated_tools > 1` sends to multi-agent, but `estimated_tools == 1` falls to `task_direct` — the "exactly 1 tool" case is never routed to multi-agent regardless of intent

**Description**: The routing logic (lines 146–163) routes `chat` directly, then checks `estimated_tools > 1` for multi-agent, and otherwise falls to `task_direct`. When `estimated_tools == 1` and `intent == "task"`, the task goes to `task_direct_monitored` where tool-count promotion may later escalate it. This means the Classifier's `estimated_tools=1` signal is indistinguishable from `estimated_tools=0` at routing time. If the classifier returns `estimated_tools=1` for a task that is genuinely a single-tool operation, the routing is correct. But if `estimated_tools=1` was a conservative estimate for a larger task, the promotion mechanism is the only safety valve. The threshold `> 1` (strictly greater than) means a classifier estimate of exactly 1 never triggers multi-agent planning. This is a logic design issue, not a bug, but it is undocumented.

---

## Low Severity / Style Issues

### [claude_session.py:242] Accessing private `_message_count` from `ContextReminder` breaks encapsulation

**Description**: Line 242: `msg_count = self._reminder._message_count`. `ClaudeSession` reaches into `ContextReminder`'s private attribute. This is a SOLID violation (tight coupling to internal state). If `ContextReminder` renames `_message_count`, `ClaudeSession` breaks silently at runtime.

**Fix**: Add a `message_count` property to `ContextReminder`.

---

### [claude_session.py:260] Accessing private `_config` from `ContextReminder` breaks encapsulation

**Description**: Line 260: `notify=self._reminder._config.notify`. Same pattern — accessing the private `_config` attribute of `ContextReminder`. Two separate private-attribute accesses in one method is a smell.

**Fix**: Add a `notify` property to `ContextReminder` that returns `self._config.notify`.

---

### [event_mapper.py:231–232] `TextBlock` in `AssistantMessage` is silently discarded without logging

**Description**: Lines 231–232: `elif isinstance(block, TextBlock): pass`. Text blocks from `AssistantMessage` are dropped with a comment "final text arrives via ResultMessage.result". This is correct for the SDK's streaming model, but if the SDK ever changes to deliver text in `AssistantMessage` rather than `ResultMessage`, the silence makes debugging very hard.

**Fix**: Add a `logger.debug` when a `TextBlock` is seen in `AssistantMessage`, at minimum.

---

### [prompts/__init__.py:8–14] `load_prompt()` raises `FileNotFoundError` with no context

**Description**: `path.read_text()` on line 14 raises `FileNotFoundError` if the prompt file is missing. The error message is `[Errno 2] No such file or directory: '...classifier.md'` which is adequate for a developer but gives no hint that this is a required prompt file for the pipeline. At startup, a missing prompt silently crashes `Classifier.__init__()` or `Decomposer.__init__()` with a confusing traceback.

**Fix**: Catch `FileNotFoundError` and re-raise with: `raise FileNotFoundError(f"Required prompt '{name}.md' not found in {_PROMPTS_DIR}") from exc`.

---

### [session_manager.py:209] `hasattr(session, "track_context")` duck-type check is fragile

**Description**: Line 209: `if session is not None and hasattr(session, "track_context")`. This runtime capability check exists because `ClaudeSession` does not have `track_context` but `Pipeline`/`Decomposer` do. The proper fix is a Protocol or ABC defining the duck-typed interface. Using `hasattr` couples `SessionManager` to the concrete implementation detail of which class has which method.

---

### [decomposer.py:233–235] `estimated_tools` in `_parse_review()` can return negative after `int()` conversion without clamping

**Description**: Line 233–235: `estimated_tools = int(estimated_tools)` with no `max(0, ...)` guard. If the LLM returns `"estimated_tools": -1`, this propagates as a negative integer into `ReviewEvent` and subsequently into routing logic. `pipeline.py:152` checks `estimated_tools > 1`, so a negative value routes to `task_direct` which is the safe fallback, but downstream code could theoretically act on the negative value.

**Fix**: `estimated_tools = max(0, int(estimated_tools))` mirroring `classification.py:133`.

---

### [classifier.py:17] Hardcoded model string not validated against configured `available` models

**Description**: `_CLASSIFIER_MODEL = "claude-haiku-4-5-20251001"` is a module-level constant that never checks `config.toml [models] available`. If the model is retired or renamed, this silently fails at runtime with an SDK error, not a config validation error.

---

### [decomposer.py:28–29] `_SUMMARIZER_MODEL` hardcoded — same issue as above

**Description**: `_SUMMARIZER_MODEL = "claude-haiku-4-5-20251001"` is hardcoded. Same concern as the Classifier model.

---

## Untested Code Paths

1. **`claude_session.py`: `start()` called when `CLAUDECODE` is not set** — the `finally` block's `if claudecode is not None:` path is the one actually tested; the branch where `claudecode` is `None` (env var absent) is not explicitly covered.

2. **`claude_session.py`: `_drain()` timeout branch** (line 321–325) — no test exercises the 5-second timeout expiry on the generator drain. The `asyncio.TimeoutError` warning path is dead code in tests.

3. **`claude_session.py`: reminder injection where `receive_response()` raises mid-iteration** — the partial-drain / cost-inflation scenario identified above has no test.

4. **`pipeline.py`: `_task_direct_monitored()` where `tool_promotion_threshold == 0`** — line 180 checks `if self._tool_promotion_threshold > 0`, meaning threshold=0 disables promotion entirely. This branch is not tested.

5. **`pipeline.py`: `_yield_plan()` where `topological_sort()` raises `ValueError`** (line 215–216) — `wave_count = 0` fallback path. No test covers a cyclic dependency graph reaching `_yield_plan`.

6. **`decomposer.py`: `_reset_orch_if_needed()` when `_orch_session.stop()` raises** — if `stop()` fails during periodic reset, the exception propagates out of `review()` or `route_task()`, which catch it and return defaults. But the `_orch_call_count` has already been incremented, meaning the next call will not attempt a reset (counter is at 0 after reset only if `stop()` succeeds). Actually, the counter is reset to 0 *after* `stop()` and `start()` at line 494, so if `stop()` raises, the counter is not reset and the next call increments to `_ORCH_RESET_THRESHOLD + 1`, triggering another reset attempt immediately. Infinite reset-attempt loop on persistent `stop()` failure.

7. **`session_manager.py`: `stop_all()` where an individual `session.stop()` raises** — the loop (lines 187–189) has no try/except; one failing `stop()` aborts the loop and leaves remaining sessions untopped.

8. **`classification.py`: `extract_json_object()` with a backslash outside a string** — the misparse edge case identified above is not covered by existing tests.

9. **`decomposer.py`: `_inject_workspace_agents()` when `agents.md` exists but is not UTF-8** — `OSError` is caught but `UnicodeDecodeError` is not. `read_text(encoding="utf-8")` raises `UnicodeDecodeError` (a `ValueError`, not `OSError`) on binary files. This propagates uncaught out of `start()`.

10. **`pipeline.py`: `stop()` where `_decomposer.stop()` raises** — decomposer stop failures are not caught (line 104), unlike classifier stop failures (line 100–103 has try/except). An exception in `_decomposer.stop()` leaks out of `Pipeline.stop()`.

---

## Convention Violations

1. **`session_manager.py:11`**: Imports `_TOOL_PROMOTION_THRESHOLD` from `pipeline.py` — a private (underscore-prefixed) module constant. This is an internal coupling anti-pattern; the constant should be surfaced via a public API or moved to a shared constants module.

2. **`decomposer.py:333`**: The `AgentTask` import inside `_parse_task_output()` (line 333: `from archon.ai.agent_plan import AgentTask`) is a deferred import inside a method body. Deferred imports are a code smell and make dependency tracking harder. The existing `TYPE_CHECKING` guard at the top of the file (line 23) already imports `AgentTask` for type hints but not at runtime. It should be a module-level runtime import.

3. **`session_manager.py:85`**: `[a for a in self._agent_loader.load_all() if a.is_archon]` — accesses `self._agent_loader` from inside the factory closure defined in `__init__`. This closure captures `self` at construction time, which is correct, but if `self._agent_loader` is mutated after construction, the factory sees the new value. This is likely intentional but not documented.

4. **CLAUDE.md SDK rule**: All SDK calls correctly use `ClaudeSDKClient`. No violations found.

5. **`decomposer.py:453`**: `from archon.ai.event_mapper import ToolStarted` inside `_extract_recent_file_paths()` — another deferred runtime import that should be at module level. `ToolStarted` is already imported at the module level in `event_mapper.py` but the import in `decomposer.py` is deferred.

6. **No `print()` statements found** — convention respected across all reviewed files.

7. **TDD coverage**: All reviewed modules have corresponding test files. Test count is high. However, as noted in "Untested Code Paths", several error and edge-case paths are not covered, and `stop_all()` has no test for exception propagation during partial shutdown.

---

## Overall Assessment

**Is this production-ready?** Conditionally, for a single-user deployment. For multi-user deployments, the `os.environ.pop()` race in `start()` is a genuine production bug that can corrupt child processes or silently drop the `CLAUDECODE` env var when two sessions start simultaneously. The `_locks` leak and the classifier-error-falls-through in `pipeline.py` are also real problems that will manifest under normal usage.

**Top 3 things that MUST be fixed before shipping to multi-user production:**

1. **`os.environ.pop()` race in `ClaudeSession.start()`** (claude_session.py:182–187): Serialise concurrent `start()` calls with a module-level lock or pass env suppression through SDK options. This is a real data-corruption race on process-global state.

2. **`_orch_session` has full tool access** (decomposer.py:96–100): Add `tools=[]` to the orchestration session. Without this, a JSON-classification call can silently run filesystem tools with no user visibility and no event emitted, producing a silent side-effect with empty output.

3. **`pipeline.py` classifier error does not abort routing** (pipeline.py:112–113): After yielding `ErrorEvent`, execution must `return` or clearly signal degraded mode. Currently the user sees an error banner followed by a normal response — a contradictory UX that destroys trust.
