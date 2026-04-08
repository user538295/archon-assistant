# FIX-028 — Router Silent Failure: asyncio timeout misuse and error swallowing
**Purpose**: Eliminate a class of silent total failures where user requests are dropped without any notification, caused by `asyncio.timeout()` firing in the wrong execution context inside async generators and `CancelledError` being silently swallowed by `except Exception`.
**Audience**: Archon users and operators — end users who lose responses silently; maintainers who rely on logs to diagnose failures.
**Status**: To Do

---

## Background

On 2026-04-08, a user request ("Make a deep research about what films were made with AI assistant...") produced no response and no error. The system waited silently for 8 minutes before becoming responsive again. Root cause: `asyncio.timeout()` in `route_task()` expired while the asyncio task was suspended in `handler.py` (between generator iterations), not inside `route_task()` itself. The `CancelledError` was raised in `handler.py`, which only catches `Exception` — not `BaseException` — so it was swallowed without logging or user notification.

The same architectural flaw exists in three places: `route_task()` (router path), `_task_direct_monitored()` primary loop (main execution path, higher traffic), and `_task_direct_monitored()` retry path (recovery mechanism). All three use `async with asyncio.timeout():` spanning a `yield`, which makes timeout-to-TimeoutError conversion unreliable. Additionally, the router's 60s timeout is too short for Sonnet with extended thinking (which can take 60s+ for thinking alone), and the classifier silently discards all non-Response events, making the 10.6s classification invisible even in debug mode.

Full analysis: `Documentation/Backlog/BUG-router-silent-failure-investigation.md`

---

## Goal

After this fix, no user request can be silently dropped. Every timeout, cancellation, or generator error produces either a successful response or an explicit user-visible message. The asyncio.timeout-in-generator misuse is eliminated from all three affected code paths. The classifier's extended thinking is surfaced in debug mode and hardened against off-script reasoning.

---

## Scope

### In Scope
- Replace `asyncio.timeout()` spanning `yield` with per-event `asyncio.wait_for()` + rolling deadline in `route_task()`, `_task_direct_monitored()` primary loop, and `_task_direct_monitored()` retry path
- Add `asyncio.wait_for(..., timeout=_ACLOSE_TIMEOUT_S)` to the unprotected `router_gen.aclose()` in `pipeline.send()`
- Add `except asyncio.CancelledError:` handler to `handler.py` and `voice.py` that logs, notifies the user, and re-raises
- Increase `_ROUTER_TIMEOUT_S` from 60.0 to 180.0
- Change router timeout `fallback_reason=""` to a user-visible string so `FallbackNoticeEvent` is emitted
- Add `events: list[Event]` field to `ClassifierResult`; collect non-Response events in `Classifier.classify()`
- Yield classifier events in `Pipeline.send()` in debug mode before `ClassificationEvent`
- Strengthen classifier system prompt to prevent off-script reasoning
- Investigate and apply SDK mechanism to disable extended thinking for the Classifier and Router sessions

### Out of Scope
- Changing the router to use a different (non-user-selected) model — that is a separate configuration concern
- Fixing silent fallbacks in paths other than the three identified generator timeout sites (CLAUDE.md: KISS first)
- Changes to how `FallbackNoticeEvent` is rendered in Telegram — existing rendering is correct
- Aiogram dispatcher internals — Fix 3's `raise` relies on aiogram handling `CancelledError` gracefully; if that turns out to be unsafe, a conditional re-raise can be added as a follow-on

---

## Acceptance criteria
- [ ] A consumer that calls `await asyncio.sleep(0.1)` between `route_task()` iterations and hits the timeout receives `TaskOutput(is_fallback=True)` — not silence
- [ ] A consumer that calls `await asyncio.sleep(0.1)` between `_task_direct_monitored()` iterations and hits `_TASK_DIRECT_TIMEOUT_S` receives a `RecoveryEvent` — not silence
- [ ] A simulated `CancelledError` raised mid-stream in `handler.py` results in the user receiving the interruption message and the error being logged
- [ ] The same applies to `voice.py`
- [ ] `_ROUTER_TIMEOUT_S` is 180.0
- [ ] Router timeout fallback emits `FallbackNoticeEvent(reason="Router timed out — handling directly")` in verbose/debug mode
- [ ] `ClassifierResult.events` is populated with non-Response events from the classifier session
- [ ] In debug mode, `Pipeline.send()` yields classifier `ThinkingResult` events before `ClassificationEvent`
- [ ] In normal/verbose mode, no classifier internal events are visible
- [ ] Classifier system prompt explicitly forbids evaluating request content and disables reasoning about task fulfilment
- [ ] All tests pass; ≥85% coverage maintained

---

## What does NOT change
- `_CLASSIFY_TIMEOUT_S` — wraps a regular coroutine (`await classify()`), not a generator yield; safe as-is
- `_RECOVERY_TIMEOUT_S` — wraps `await self._decomposer.recover_session()`, not a generator yield; safe
- The `_task_direct_monitored` `except TimeoutError:` recovery block (RecoveryEvent, session recovery, promote/retry logic) — only the iteration mechanism changes; recovery logic is untouched
- `Pipeline.send()` locking, routing, and event flow — only `router_gen.aclose()` gains a timeout wrapper
- `FallbackNoticeEvent` rendering in `handler.py` and Telegram formatter
- `test_route_task_reset_timeout_falls_back` and `test_route_task_fallback_includes_is_fallback_flag_on_reset_timeout` — these test the `_ROUTER_RESET_TIMEOUT_S` path (pre-yield), which is unaffected by this fix

---

## Known limitations / accepted trade-offs
- **Fix 3 re-raises `CancelledError`** into aiogram's middleware chain. Aiogram 3.x handles this gracefully at the dispatcher level (logs and moves on). If a future aiogram upgrade changes this, a conditional `raise` may be needed.
- **Fix 7 (per-event wait_for in `_task_direct_monitored`)** does not change the recovery logic — a timeout during the retry path still yields an `ErrorEvent`. This is correct: the retry has already exhausted the recovery budget.
- **Fix 5b (disable extended thinking)** is conditional on SDK capability. If `ClaudeSDKClient` / `ClaudeSession` does not support per-session thinking control, Fix 5b is deferred; Fix 2 (180s timeout) then becomes the sole mitigation for slow routing.
- **`gen.aclose()` in Fix 1 finally block** uses a 5.0s hardcoded timeout (matching existing pattern in `_task_direct_monitored`). The inner generator (`router.send()`) may be in an indeterminate state after `wait_for` cancels `__anext__()`; `aclose()` is best-effort cleanup.

---

## Architecture

### Modified files
- `archon/ai/decomposer.py` — `route_task()`: replace `asyncio.timeout()` block with rolling-deadline `wait_for` loop; change `_ROUTER_TIMEOUT_S` to 180.0; change timeout `fallback_reason=""` to non-empty
- `archon/ai/pipeline.py` — `_task_direct_monitored()`: replace two `asyncio.timeout()` blocks (primary loop + retry loop) with rolling-deadline `wait_for`; `pipeline.send()`: wrap `router_gen.aclose()` in `wait_for`; yield classifier events in debug mode
- `archon/chat/handler.py` — add `except asyncio.CancelledError:` before `except Exception as exc:`
- `archon/chat/voice.py` — add `except asyncio.CancelledError:` before `except Exception as exc:`
- `archon/ai/classifier.py` — add `events: list[Event]` to `ClassifierResult`; collect non-Response events in `classify()`
- `archon/ai/prompts/classifier.md` — strengthen prompt

### Rolling-deadline pattern (used in Tasks 1.1, 1.3, 1.4)
```python
deadline = asyncio.get_event_loop().time() + _TIMEOUT_S
while True:
    remaining = deadline - asyncio.get_event_loop().time()
    if remaining <= 0:
        raise TimeoutError          # caught by outer except TimeoutError:
    try:
        item = await asyncio.wait_for(gen.__anext__(), timeout=remaining)
    except StopAsyncIteration:
        break
    except TimeoutError:
        raise                       # propagate to outer except TimeoutError:
    except Exception:
        raise                       # propagate to outer except Exception:
    yield item
```
`asyncio.wait_for()` in Python 3.12 wraps the coroutine in a child Task and cancels that child task on timeout. The `CancelledError` is raised inside the child task (inside `__anext__()`), converted to `TimeoutError` by `wait_for`'s internal `asyncio.timeout()`, and raised in the parent — which is now executing `await asyncio.wait_for(...)`, NOT suspended at `yield`. The outer `except TimeoutError:` correctly fires.

### No new config keys or environment variables

---

## Tests

- **test_route_task_timeout_fires_during_consumer_async_work** (unit): consumer sleeps between iterations; verify fallback TaskOutput yielded, not silence
- **test_route_task_wait_for_handles_natural_exhaustion** (unit): generator yields N events then exhausts; verify all N events received, no RuntimeError
- **test_route_task_wait_for_negative_remaining_time** (unit): deadline already elapsed; verify immediate fallback TaskOutput
- **test_route_task_aclose_called_on_timeout** (unit): timeout fires; verify `gen.aclose()` called in finally
- **test_route_task_aclose_cancelled_error_is_handled** (unit): `gen.aclose()` raises CancelledError; verify does not propagate
- **test_router_timeout_constant_value** (unit): policy assertion `_ROUTER_TIMEOUT_S == 180.0`
- **test_route_task_timeout_fallback_reason_non_empty** (unit): timeout fires; assert `TaskOutput.fallback_reason != ""`
- **test_pipeline_emits_fallback_notice_event_on_router_timeout** (unit): router times out; Pipeline emits `FallbackNoticeEvent` in verbose mode
- **test_handle_message_notifies_user_on_cancelled_error** (unit): `session.send()` raises CancelledError; user receives interruption message
- **test_handle_message_cancelled_error_re_raised** (unit): CancelledError; verify `pytest.raises(asyncio.CancelledError)` from handler
- **test_handle_message_cancelled_error_telegram_fails** (unit): notification fails; CancelledError still re-raised
- **test_voice_handle_cancelled_error** (unit): same three cases for voice.py handler
- **test_task_direct_monitored_timeout_fires_during_consumer_async_work** (unit): consumer sleeps between iterations; timeout fires; verify RecoveryEvent yielded (not silence)
- **test_task_direct_retry_timeout_fires_during_consumer_async_work** (unit): retry path consumer sleeps; timeout fires; verify ErrorEvent yielded (not silence)
- **test_task_direct_monitored_aclose_called_on_timeout** (unit): verify `gen.aclose()` called in finally on primary loop timeout
- **test_pipeline_router_gen_aclose_has_timeout** (unit): verify `pipeline.send()` wraps `router_gen.aclose()` in `wait_for`
- **test_classifier_preserves_non_response_events** (unit): mock yields `[ThinkingResult, Response]`; assert `result.events == [ThinkingResult(...)]`
- **test_pipeline_yields_classifier_events_in_debug_mode** (unit): mode=debug; assert ThinkingResult appears before ClassificationEvent
- **test_pipeline_suppresses_classifier_events_in_normal_mode** (unit): mode=normal; assert no classifier events in stream
- **test_pipeline_full_failure_chain_no_silent_drop** (integration): end-to-end consumer with async work between iterations; slow router session; verify response or explicit fallback — never silence

---

## Documentation update
- [ ] `Documentation/Backlog/BUG-router-silent-failure-investigation.md`, section: Status — update to `Resolved` with reference to FIX-028

---

## Task breakdown

### Phase 1 — Silent Failure Prevention
> **Releasable**: after Task 1.7 — all three generator timeout sites and both handler CancelledError gaps are fixed; no user request can be silently dropped

#### Task 1.1 — Replace `asyncio.timeout()` in `route_task()` with rolling-deadline `wait_for`
- [ ] **File**: `archon/ai/decomposer.py`
- **Depends on**: nothing
- **Description**:
  - Replace the inner `try: async with asyncio.timeout(_ROUTER_TIMEOUT_S): async for event in gen: yield event` block (lines 390–410) with the rolling-deadline `wait_for` pattern
  - Keep the outer `except TimeoutError:` block and its fallback `yield TaskOutput(...)` unchanged — only the iteration mechanism changes
  - Keep the existing `finally: await asyncio.wait_for(gen.aclose(), timeout=5.0)` unchanged
  - The `except Exception as exc:` fallback for non-timeout errors stays unchanged
  - `_ROUTER_TIMEOUT_S` stays at 60.0 in this task (Task 2.1 raises it)
  - No signature changes to `route_task()`
- **Releasable**: `route_task()` now yields fallback `TaskOutput` when the router hangs, even when the timeout fires during consumer execution
- **Tests (TDD)** — `tests/ai/test_decomposer.py`:
  - Unit: `test_route_task_timeout_fires_during_consumer_async_work` — create a consumer coroutine that appends to a list AND calls `await asyncio.sleep(0.01)` between each `__anext__()` call; set `_ROUTER_TIMEOUT_S=0.05`; mock router.send() to yield one event then sleep forever; verify that a `TaskOutput(is_fallback=True)` is eventually yielded (not silence/hang)
  - Unit: `test_route_task_wait_for_handles_natural_exhaustion` — router yields 3 events then stops; verify all 3 events received in order and loop exits cleanly without RuntimeError or StopAsyncIteration propagating
  - Unit: `test_route_task_wait_for_negative_remaining_time` — monkeypatch `asyncio.get_event_loop().time()` so `remaining` is negative on first iteration; verify immediate fallback `TaskOutput` with `is_fallback=True`
  - Unit: `test_route_task_aclose_called_on_timeout` — mock `gen.aclose()` to track calls; trigger timeout; assert `aclose()` was called exactly once
  - Unit: `test_route_task_aclose_cancelled_error_is_handled` — mock `gen.aclose()` to raise `asyncio.CancelledError`; trigger timeout; assert no exception propagates out of `route_task()`
  - Checkpoint: `uv run pytest tests/ai/test_decomposer.py -k "test_route_task_timeout_fires_during_consumer_async_work or test_route_task_wait_for" -v --no-cov`

#### Task 1.2 — Fix unprotected `router_gen.aclose()` in `pipeline.send()`
- [ ] **File**: `archon/ai/pipeline.py`
- **Depends on**: nothing
- **Description**:
  - In `pipeline.send()` at line 235: change `await router_gen.aclose()` to `await asyncio.wait_for(router_gen.aclose(), timeout=_ACLOSE_TIMEOUT_S)` inside a `try/except Exception` block that logs on failure
  - `_ACLOSE_TIMEOUT_S` is already defined at line 42 (10.0s) — reuse it
  - Pattern to follow: identical to how `_task_direct_monitored` handles `gen.aclose()` at line 494
  - No other changes to `pipeline.send()`
- **Releasable**: `pipeline.send()` cannot hang indefinitely on an unresponsive SDK subprocess during router generator cleanup
- **Tests (TDD)** — `tests/ai/test_pipeline.py`:
  - Unit: `test_pipeline_router_gen_aclose_has_timeout` — mock `router_gen.aclose()` to hang; assert `pipeline.send()` returns within `_ACLOSE_TIMEOUT_S + 1s`; assert a warning is logged
  - Checkpoint: `uv run pytest tests/ai/test_pipeline.py -k "test_pipeline_router_gen_aclose_has_timeout" -v --no-cov`

#### Task 1.3 — Replace `asyncio.timeout()` in `_task_direct_monitored` primary loop
- [ ] **File**: `archon/ai/pipeline.py`
- **Depends on**: nothing
- **Description**:
  - Replace `async with asyncio.timeout(_TASK_DIRECT_TIMEOUT_S):` (line 359) and its inner `async for event in gen: yield event` block with the rolling-deadline `wait_for` pattern
  - The outer `except TimeoutError:` recovery block (lines 402–490: aclose gen, `RecoveryEvent`, `recover_session()`, promote/retry decision) remains **completely unchanged** — only the inner iteration changes
  - The `raise TimeoutError` / `raise` idiom in the new loop propagates to the existing handler: no new recovery logic needed
  - Note: unlike `route_task()`, this function's `TimeoutError` handler already calls `gen.aclose()` explicitly (line 414) inside the `except TimeoutError:` block — the new loop must set `gen_closed = True` equivalent to avoid double-close; match the existing `gen_closed` flag usage
- **Releasable**: `_task_direct_monitored` primary loop now correctly fires `TimeoutError` into its own handler when the consumer is executing between iterations
- **Tests (TDD)** — `tests/ai/test_pipeline.py`:
  - Unit: `test_task_direct_monitored_timeout_fires_during_consumer_async_work` — consumer calls `await asyncio.sleep(0.01)` between iterations; mock `_decomposer.answer()` to yield one event then sleep forever; set `_TASK_DIRECT_TIMEOUT_S=0.05`; verify `RecoveryEvent(phase="timeout_detected", ...)` is yielded (not silence)
  - Unit: `test_task_direct_monitored_aclose_called_on_timeout` — mock `gen.aclose()`; trigger timeout; assert it was called
  - Checkpoint: `uv run pytest tests/ai/test_pipeline.py -k "test_task_direct_monitored_timeout" -v --no-cov`

#### Task 1.4 — Replace `asyncio.timeout()` in `_task_direct_monitored` retry loop
- [ ] **File**: `archon/ai/pipeline.py`
- **Depends on**: Task 1.3
- **Description**:
  - Replace `async with asyncio.timeout(_RETRY_TIMEOUT_S):` (line 466) and its `async for event in retry_gen: yield event` with the rolling-deadline `wait_for` pattern
  - The outer `except TimeoutError:` at line 469 (logs, optional session recovery, `ErrorEvent` yield) remains unchanged — `raise` from the new loop propagates to it
  - `retry_gen.aclose()` in the `finally` block at line 488 is already wrapped in `asyncio.wait_for` — leave it unchanged
- **Releasable**: retry path correctly fires its `except TimeoutError:` handler and yields `ErrorEvent` when the consumer is executing between iterations
- **Tests (TDD)** — `tests/ai/test_pipeline.py`:
  - Unit: `test_task_direct_retry_timeout_fires_during_consumer_async_work` — simulate scenario where primary times out (BAM disabled, retry path taken); retry consumer sleeps between iterations; mock `retry_gen` to yield one event then sleep; set `_RETRY_TIMEOUT_S=0.05`; verify `ErrorEvent` is eventually yielded (not silence)
  - Checkpoint: `uv run pytest tests/ai/test_pipeline.py -k "test_task_direct_retry_timeout" -v --no-cov`

#### Task 1.5 — Add `CancelledError` handler to `handler.py`
- [ ] **File**: `archon/chat/handler.py`
- **Depends on**: nothing
- **Description**:
  - In `handle_message()`, add `except asyncio.CancelledError:` immediately before the existing `except Exception as exc:` at line 394
  - Handler body:
    1. `logger.warning("Message processing cancelled for user %d — task received CancelledError", user_id)`
    2. `try: await message.answer("⚙️ Processing was interrupted unexpectedly. The system is recovering — please resend your message.") / except Exception: logger.warning("Failed to deliver cancellation notice to user %d", user_id)`
    3. `raise` — re-raise so aiogram handles task cleanup
  - Do not wrap `record_archon_message` in the cancellation handler — it may itself raise; the user notification is the priority
  - The `finally:` block (beacon task cancel) continues to run after re-raise, which is correct
- **Releasable**: any task cancellation in the message handler produces a logged warning and a user-visible Telegram message before the CancelledError propagates
- **Tests (TDD)** — `tests/chat/test_handler.py`:
  - Unit: `test_handle_message_notifies_user_on_cancelled_error` — mock `session.send()` to raise `asyncio.CancelledError` on first iteration; call `handle_message(...)`; assert `message.answer` called with interruption text; assert `logger.warning` called; assert `asyncio.CancelledError` is re-raised
  - Unit: `test_handle_message_cancelled_error_re_raised` — same setup; assert `pytest.raises(asyncio.CancelledError)` wrapping the handler call
  - Unit: `test_handle_message_cancelled_error_telegram_send_fails` — `message.answer` raises `TelegramError` inside the cancellation handler; assert `asyncio.CancelledError` still propagates (outer `except Exception` inside the cancellation handler suppresses Telegram failure correctly)
  - Checkpoint: `uv run pytest tests/chat/test_handler.py -k "cancelled_error" -v --no-cov`

#### Task 1.6 — Add `CancelledError` handler to `voice.py`
- [ ] **File**: `archon/chat/voice.py`
- **Depends on**: nothing
- **Description**:
  - Identical pattern to Task 1.5 applied to the `handle_voice_message()` / main processing handler in `voice.py` at line 318
  - Add `except asyncio.CancelledError:` before the existing `except Exception as exc:` at line 318
  - Same three-step body: log warning, attempt user notification, `raise`
- **Releasable**: voice message processing cancellations are logged and user-visible
- **Tests (TDD)** — `tests/chat/test_voice.py`:
  - Unit: `test_voice_handle_cancelled_error_notifies_user` — mock pipeline to raise CancelledError; verify user notification sent
  - Unit: `test_voice_handle_cancelled_error_re_raised` — verify `asyncio.CancelledError` propagates
  - Unit: `test_voice_handle_cancelled_error_telegram_fails` — notification fails; CancelledError still propagates
  - Checkpoint: `uv run pytest tests/chat/test_voice.py -k "cancelled_error" -v --no-cov`

#### Task 1.7 — Integration test: full failure chain produces no silent drop
- [ ] **File**: `tests/ai/test_pipeline_e2e.py` (new file)
- **Depends on**: Tasks 1.1, 1.2, 1.3, 1.4, 1.5
- **Description**:
  - New test file wiring `Pipeline` → a consumer coroutine that does real async work between iterations
  - The consumer simulates `handler.py` behavior: `await asyncio.sleep(0.05)` between each event (representing Telegram send latency)
  - Use a mock router session that yields one `ThinkingResult` then sleeps forever; set `_ROUTER_TIMEOUT_S=0.1`
  - Assert: either a `TaskOutput(is_fallback=True)` or a `FallbackNoticeEvent` is received — never timeout + silence
  - Second case: mock decomposer main session that also sleeps; verify `RecoveryEvent` is yielded — not silence
- **Releasable**: Phase 1 complete — no silent drops on timeout for any code path
- **Tests (TDD)** — `tests/ai/test_pipeline_e2e.py`:
  - Integration: `test_pipeline_full_failure_chain_no_silent_drop` — as described above
  - Integration: `test_pipeline_task_direct_no_silent_drop` — `_task_direct_monitored` path version
  - Checkpoint: `uv run pytest tests/ai/test_pipeline_e2e.py -v --no-cov`

---

### Phase 2 — Timeout Stabilization and UX Visibility
> **Releasable**: after each task independently — Task 2.1 makes complex routing requests viable; Task 2.2 makes timeout fallbacks visible in verbose/debug mode

#### Task 2.1 — Increase `_ROUTER_TIMEOUT_S` to 180 seconds
- [ ] **File**: `archon/ai/decomposer.py`
- **Depends on**: Task 1.1
- **Description**:
  - Change `_ROUTER_TIMEOUT_S: float = 60.0` (line 52) to `_ROUTER_TIMEOUT_S: float = 180.0`
  - Comment: `# 180s: Sonnet extended thinking alone can take 60-90s; defense-in-depth pending Fix 5b`
  - No other changes
- **Releasable**: router no longer times out on complex Sonnet + extended-thinking routing decisions
- **Tests (TDD)** — `tests/ai/test_decomposer.py`:
  - Unit: `test_router_timeout_constant_value` — `assert _ROUTER_TIMEOUT_S == 180.0`; documents the value as an intentional constraint
  - Checkpoint: `uv run pytest tests/ai/test_decomposer.py -k "test_router_timeout_constant_value" -v --no-cov`

#### Task 2.2 — Make router timeout fallback user-visible
- [ ] **File**: `archon/ai/decomposer.py`
- **Depends on**: Task 1.1
- **Description**:
  - In `route_task()`, change the `except TimeoutError:` fallback (current line 403):
    - Old: `yield TaskOutput(scope="small", prompt=prompt, is_fallback=True, fallback_reason="")`
    - New: `yield TaskOutput(scope="small", prompt=prompt, is_fallback=True, fallback_reason="Router timed out — handling directly")`
  - `Pipeline.send()` at line 261 already gates `FallbackNoticeEvent` on `fallback_reason` being non-empty — no pipeline changes needed
  - Also update `test_route_task_fallback_silent_on_reset_timeout` note: that test covers the `_ROUTER_RESET_TIMEOUT_S` path (line 383), not this path — it is **not** affected by this change and must remain unchanged
  - Optionally (if desired for consistency): also change line 383 reset-init fallback `fallback_reason=""` to `"Router init timed out — handling directly"` — treat as a separate decision; do not bundle unless explicitly agreed
- **Releasable**: in verbose/debug mode, users see `FallbackNoticeEvent` explaining why routing was bypassed
- **Tests (TDD)** — `tests/ai/test_decomposer.py` and `tests/ai/test_pipeline.py`:
  - Unit: `test_route_task_timeout_fallback_reason_non_empty` — trigger router session timeout via slow mock; assert yielded `TaskOutput.fallback_reason == "Router timed out — handling directly"`
  - Unit: `test_pipeline_emits_fallback_notice_event_on_router_timeout` — `Pipeline.send()` with timed-out router; assert `FallbackNoticeEvent` emitted with correct reason in verbose mode
  - Unit: `test_pipeline_no_fallback_notice_in_normal_mode` — same setup with mode=normal; assert no `FallbackNoticeEvent` emitted (existing behavior verified)
  - Checkpoint: `uv run pytest tests/ai/test_decomposer.py -k "fallback_reason" -v --no-cov && uv run pytest tests/ai/test_pipeline.py -k "fallback_notice" -v --no-cov`

---

### Phase 3 — Classifier Hardening
> **Releasable**: Task 3.1 + 3.2 together make classifier internals visible in debug mode; Task 3.3 is independently deployable; Task 3.4 requires SDK investigation before coding

#### Task 3.1 — Add `events` field to `ClassifierResult` and collect non-Response events
- [ ] **File**: `archon/ai/classifier.py`
- **Depends on**: nothing
- **Description**:
  - Add `events: list[Event] = field(default_factory=list)` to `ClassifierResult` dataclass (after `parse_error: str = ""`)
  - In `Classifier.classify()`, collect all non-Response events into a local list `result_events: list[Event] = []`; in the `async for` loop: `if isinstance(event, Response): raw_response = event.content` else `result_events.append(event)`
  - Pass `events=result_events` when constructing the returned `ClassifierResult`
  - Type import: `Event` from `archon.ai.event_mapper` — it is already imported in the module
  - The classifier docstring at line 37 says "No events yielded — just returns data"; update to: "Non-Response events (ThinkingResult etc.) are collected and returned in `events` for debug-mode surfacing."
- **Releasable**: `ClassifierResult` carries internal events; `Pipeline.send()` can inspect them (Task 3.2)
- **Tests (TDD)** — `tests/ai/test_classifier.py`:
  - Unit: `test_classifier_preserves_non_response_events` — mock session yields `[ThinkingResult(content="thinking..."), Response(content='{"intent":"task","confidence":0.9}')]`; assert `result.events == [ThinkingResult(content="thinking...")]`
  - Unit: `test_classifier_empty_events_on_response_only` — mock session yields only `Response`; assert `result.events == []`
  - Unit: `test_classifier_result_events_field_type` — assert `result.events` is a list (regression: default_factory used correctly, not a shared mutable default)
  - Checkpoint: `uv run pytest tests/ai/test_classifier.py -v --no-cov`

#### Task 3.2 — Yield classifier events in `Pipeline.send()` debug mode
- [ ] **File**: `archon/ai/pipeline.py`
- **Depends on**: Task 3.1
- **Description**:
  - In `Pipeline.send()`, after `result = await self._classifier.classify(prompt)` (around line 192) and before `yield ClassificationEvent(...)`:
    ```python
    if self._notification_mode == "debug":
        for clf_event in result.events:
            yield clf_event
    ```
  - `self._notification_mode` already exists on `Pipeline` — verify the attribute name and use it directly
  - Events yielded here are NOT tagged with `source="router"` — they originate from the classifier session; they have their own source field or default source
  - No changes to the `ClassificationEvent` yield logic
- **Releasable**: in debug mode, users see classifier thinking before the classification decision; in all other modes behavior is unchanged
- **Tests (TDD)** — `tests/ai/test_pipeline.py`:
  - Unit: `test_pipeline_yields_classifier_events_in_debug_mode` — mock classifier returning `ClassifierResult(events=[ThinkingResult(content="...")])` alongside a valid classification; set `mode="debug"`; assert `ThinkingResult` appears in the stream before `ClassificationEvent`
  - Unit: `test_pipeline_suppresses_classifier_events_in_normal_mode` — same mock, `mode="normal"`; assert no `ThinkingResult` in stream
  - Unit: `test_pipeline_suppresses_classifier_events_in_verbose_mode` — same, `mode="verbose"`; assert no classifier `ThinkingResult` (only debug surfaces them)
  - Checkpoint: `uv run pytest tests/ai/test_pipeline.py -k "classifier_events" -v --no-cov`

#### Task 3.3 — Strengthen classifier system prompt
- [ ] **File**: `archon/ai/prompts/classifier.md`
- **Depends on**: nothing
- **Description**:
  - Replace current prompt content with:
    ```
    You are a fast intent classifier. Your ONLY job is to output a JSON classification.

    Output ONLY a raw JSON object. No markdown, no code fences, no explanations,
    no reasoning, no commentary — nothing before or after the JSON.
    Do NOT evaluate whether you can fulfil the request.
    Do NOT respond to the content of the message.
    ONLY classify it.

    Schema: {"intent": "chat" | "task", "confidence": 0.0-1.0}

    - "chat": conversational, greetings, casual questions, thank you, feedback
    - "task": requests requiring action, research, code, files, analysis, multi-step work

    If unsure, classify as "task" with lower confidence.
    ```
  - Keep existing tests passing — `parse_classification()` is resilient; this change tightens the model's output, not the parser
  - No code changes — prompt file only
- **Releasable**: classifier is less likely to append reasoning text after the JSON
- **Tests (TDD)** — `tests/ai/test_classifier.py`:
  - Unit: `test_classifier_prompt_forbids_reasoning` — read `classifier.md`; assert it contains "Do NOT evaluate whether you can fulfil" and "ONLY classify it" (content guard against regression)
  - Live E2E: `test_classifier_raw_response_is_directly_json_parseable` — (existing test if present; tighten assertion to also verify no text after the closing `}`)
  - Checkpoint: `uv run pytest tests/ai/test_classifier.py -k "prompt" -v --no-cov`

#### Task 3.4 — Disable extended thinking for Classifier and Router sessions
- [ ] **Files**: `archon/ai/classifier.py`, `archon/ai/decomposer.py`, and possibly `archon/ai/claude_session.py`
- **Depends on**: Task 3.1
- **Prerequisite investigation** (must complete before coding):
  1. Check `ClaudeSession.__init__` signature — does it accept a `thinking` or `no_thinking` parameter?
  2. If not, check the Claude Agent SDK docs/source for per-session thinking control
  3. If the SDK supports it: add a `disable_thinking: bool = False` parameter to `ClaudeSession`; pass it through to the SDK's session config
  4. If the SDK does NOT support it: document the limitation and defer; `_ROUTER_TIMEOUT_S = 180` (Task 2.1) is the fallback mitigation
- **Description** (conditional on SDK capability):
  - `ClaudeSession.__init__(self, ..., disable_thinking: bool = False)` — new optional parameter
  - When `disable_thinking=True`, pass the appropriate SDK config to disable extended thinking
  - In `Classifier.__init__`, construct session with `disable_thinking=True`
  - In `Decomposer._router_session` construction, pass `disable_thinking=True`
  - Both classifier and router are classification/routing tasks; neither benefits from extended thinking
- **Releasable**: classifier latency drops from ~10s to <2s; router latency drops from ~60-90s to ~5s; `_ROUTER_TIMEOUT_S = 180` becomes defense-in-depth rather than a necessity
- **Tests (TDD)** — `tests/ai/test_classifier.py` and `tests/ai/test_decomposer.py`:
  - Unit: `test_classifier_session_constructed_with_thinking_disabled` — assert `ClaudeSession` is constructed with `disable_thinking=True` in `Classifier.__init__`
  - Unit: `test_router_session_constructed_with_thinking_disabled` — assert router `ClaudeSession` uses `disable_thinking=True`
  - Unit: `test_claude_session_disable_thinking_passes_config_to_sdk` — when `disable_thinking=True`, verify the SDK receives the correct thinking-disable config
  - Checkpoint: `uv run pytest tests/ai/test_classifier.py tests/ai/test_decomposer.py -k "thinking_disabled" -v --no-cov`
