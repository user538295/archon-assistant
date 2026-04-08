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
- Yield classifier events unconditionally in `Pipeline.send()` before `ClassificationEvent`; debug-only filtering applied in `format_event()` in `telegram_formatter.py`
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
- [ ] Router timeout fallback emits `FallbackNoticeEvent(reason="Router timed out — handling directly")` in all notification modes
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
- `archon/chat/file_handler.py` — all file handler methods delegate to `handle_message()`, inheriting the CancelledError fix from Task 1.5 without changes

---

## Known limitations / accepted trade-offs
- **Fix 3 re-raises `CancelledError`** into aiogram's middleware chain. Aiogram 3.x handles this gracefully at the dispatcher level (logs and moves on). If a future aiogram upgrade changes this, a conditional `raise` may be needed.
- **Fix 7 (per-event wait_for in `_task_direct_monitored`)** does not change the recovery logic — a timeout during the retry path still yields an `ErrorEvent`. This is correct: the retry has already exhausted the recovery budget.
- **Fix 5b (disable extended thinking)** is conditional on SDK capability. If `ClaudeSDKClient` / `ClaudeSession` does not support per-session thinking control, Fix 5b is deferred; Fix 2 (180s timeout) then becomes the sole mitigation for slow routing.
- **`gen.aclose()` in Fix 1 finally block** uses a 5.0s hardcoded timeout (matching existing pattern in `_task_direct_monitored`). The inner generator (`router.send()`) may be in an indeterminate state after `wait_for` cancels `__anext__()`; `aclose()` is best-effort cleanup.
- **Rolling-deadline timeout semantic**: The new per-event `asyncio.wait_for(gen.__anext__(), timeout=remaining)` pattern measures cumulative time spent inside `__anext__()` calls, not wall-clock time. Consumer-side latency (Telegram message delivery, flood control backoff, etc.) is not counted against the deadline. Under extreme flood control (Telegram `RetryAfter` of 30s+), wall-clock elapsed time can significantly exceed the configured timeout without triggering it. This is accepted: the timeout is defensive protection against generator hangs, not an SLA guarantee.

---

## Architecture

### Modified files
- `archon/ai/decomposer.py` — `route_task()`: replace `asyncio.timeout()` block with rolling-deadline `wait_for` loop; change `_ROUTER_TIMEOUT_S` to 180.0; change timeout `fallback_reason=""` to non-empty
- `archon/ai/pipeline.py` — `_task_direct_monitored()`: replace two `asyncio.timeout()` blocks (primary loop + retry loop) with rolling-deadline `wait_for`; `pipeline.send()`: wrap `router_gen.aclose()` in `wait_for`; yield classifier events stamped with `source='classifier'` before `ClassificationEvent`. Classifier events yielded from `result.events` are stamped with `source='classifier'` before yielding — this allows `format_event()` to apply debug-only filtering distinct from orchestrator `ThinkingResult`.
- `archon/chat/handler.py` — add `except asyncio.CancelledError:` before `except Exception as exc:`
- `archon/chat/handler.py` — add `if getattr(event, 'source', '') == 'classifier': continue` immediately after the `is_router_event()` skip (around line 295), so classifier events are skipped without incrementing beacon counts
- `archon/chat/voice.py` — add `except asyncio.CancelledError:` before `except Exception as exc:`
- `archon/ai/classifier.py` — add `events: list[Event]` to `ClassifierResult`; collect non-Response events in `classify()`
- `archon/ai/prompts/classifier.md` — strengthen prompt
- `archon/chat/telegram_formatter.py` — update `format_event()` to: show `ThinkingResult` with `source='classifier'` only when `mode == 'debug'`

### Rolling-deadline pattern (used in Tasks 1.1, 1.3, 1.4)
```python
deadline = asyncio.get_running_loop().time() + _TIMEOUT_S
while True:
    remaining = deadline - asyncio.get_running_loop().time()
    # The `remaining <= 0` check is outside the inner try — it raises TimeoutError
    # directly to the outer handler. The inner `except TimeoutError: raise` handles
    # wait_for's timeout. Both paths propagate to the same outer handler.
    if remaining <= 0:
        raise TimeoutError          # caught by outer except TimeoutError:
    try:
        item = await asyncio.wait_for(gen.__anext__(), timeout=remaining)
    except StopAsyncIteration:
        break
    except TimeoutError:
        raise                       # propagate to outer except TimeoutError:
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
- **test_pipeline_emits_fallback_notice_event_on_router_timeout** (unit): router times out; Pipeline emits `FallbackNoticeEvent` in all notification modes (unconditional)
- **test_handle_message_notifies_user_on_cancelled_error** (unit): `session.send()` raises CancelledError; user receives interruption message
- **test_handle_message_cancelled_error_re_raised** (unit): CancelledError; verify `pytest.raises(asyncio.CancelledError)` from handler
- **test_handle_message_cancelled_error_telegram_fails** (unit): notification fails; CancelledError still re-raised
- **test_voice_handle_cancelled_error_notifies_user** (unit): mock pipeline raises CancelledError; verify user notification sent
- **test_voice_handle_cancelled_error_re_raised** (unit): verify `asyncio.CancelledError` propagates from voice handler
- **test_voice_handle_cancelled_error_telegram_fails** (unit): notification fails; CancelledError still propagates from voice handler
- **test_task_direct_monitored_timeout_fires_during_consumer_async_work** (unit): consumer sleeps between iterations; timeout fires; verify RecoveryEvent yielded (not silence)
- **test_task_direct_retry_timeout_fires_during_consumer_async_work** (unit): retry path consumer sleeps; timeout fires; verify ErrorEvent yielded (not silence)
- **test_task_direct_monitored_aclose_called_on_timeout** (unit): verify `gen.aclose()` called in finally on primary loop timeout
- **test_pipeline_router_gen_aclose_has_timeout** (unit): verify `pipeline.send()` wraps `router_gen.aclose()` in `wait_for`
- **test_classifier_preserves_non_response_events** (unit): mock yields `[ThinkingResult, Response]`; assert `result.events == [ThinkingResult(...)]`
- **test_pipeline_yields_classifier_events_unconditionally** (unit): Pipeline always yields classifier events (stamped with `source='classifier'`) regardless of mode; mode filtering belongs in `format_event()` in telegram_formatter.py
- **test_pipeline_classifier_events_stamped_with_classifier_source** (unit): classifier ThinkingResult in stream has source == 'classifier' exactly
- **test_format_event_suppresses_classifier_thinking_in_normal_mode** (unit): mode=normal; classifier `ThinkingResult` with `source="classifier"`; assert `format_event()` suppresses it
- **test_format_event_suppresses_classifier_thinking_in_verbose_mode** (unit): mode=verbose; classifier `ThinkingResult`; assert suppressed (only debug surfaces them)
- **test_format_event_delivers_classifier_thinking_in_debug_mode** (unit): mode=debug; classifier `ThinkingResult` with `source="classifier"`; assert rendered output is non-empty
- **test_format_event_regular_thinking_unchanged** (unit): non-classifier ThinkingResult still renders in normal/verbose/debug after classifier filter addition
- **test_quiet_mode_classifier_thinking_not_counted_in_beacon** (unit): in handler.py quiet mode, classifier ThinkingResult is skipped without incrementing counts["thinking"]
- **test_pipeline_drops_non_thinking_classifier_events** (unit): non-ThinkingResult classifier events (ErrorEvent, etc.) are silently dropped — not yielded to consumers
- **test_task_direct_monitored_aclose_cancelled_error_is_handled** (unit): `gen.aclose()` raises CancelledError inside the timeout recovery block; verify it does not propagate out of `_task_direct_monitored()`
- **test_task_direct_retry_aclose_cancelled_error_is_handled** (unit): `retry_gen.aclose()` raises CancelledError in the finally block; verify it does not propagate
- **test_task_direct_monitored_negative_remaining_time** (unit): deadline already elapsed; verify immediate TimeoutError triggers the recovery handler (RecoveryEvent yielded)
- **test_task_direct_retry_negative_remaining_time** (unit): deadline already elapsed in retry loop; verify immediate TimeoutError triggers ErrorEvent
- **test_pipeline_full_failure_chain_no_silent_drop** (integration): end-to-end consumer with async work between iterations; slow router session; verify response or explicit fallback — never silence
- **test_pipeline_task_direct_no_silent_drop** (integration): `_task_direct_monitored` path; consumer does async work between iterations; timeout fires; verify `RecoveryEvent` — not silence
- **test_classifier_empty_events_on_response_only** (unit): mock session yields only `Response`; assert `result.events == []`
- **test_classifier_result_events_field_type** (unit): assert `result.events` is a list (regression: `default_factory` used correctly, not a shared mutable default)
- **test_classifier_prompt_forbids_reasoning** (unit): classifier.md contains 'Do NOT evaluate' and 'ONLY classify it' constraint phrases
- **test_classifier_session_constructed_with_thinking_disabled** (unit, conditional on Task 3.4 = YES): `Classifier.__init__` constructs session with `disable_thinking=True`
- **test_router_session_constructed_with_thinking_disabled** (unit, conditional on Task 3.4 = YES): router `ClaudeSession` uses `disable_thinking=True`
- **test_claude_session_disable_thinking_passes_config_to_sdk** (unit, conditional on Task 3.4 = YES): when `disable_thinking=True`, SDK receives correct thinking-disable config

---

## Documentation update
- [ ] `Documentation/Backlog/BUG-router-silent-failure-investigation.md`, section: Status — update to `Resolved` with reference to FIX-028
- [ ] `CLAUDE.md` — add note to the `archon/ai/` section documenting: "Async generator timeout pattern: never use `asyncio.timeout()` spanning a `yield`. Use the rolling-deadline `asyncio.wait_for(gen.__anext__(), timeout=remaining)` pattern. See FIX-028."
- [ ] `archon/chat/handler.py` — inline docstring noting that CancelledError is explicitly handled and re-raised

---

## Task breakdown

### Phase 1 — Silent Failure Prevention
> **Releasable**: after Task 1.6 — all three generator timeout sites and both handler CancelledError gaps are fixed; no user request can be silently dropped. Task 1.7 is a verification step (integration test) that runs immediately after but does not gate the functional release.

#### Task 1.1 — Replace `asyncio.timeout()` in `route_task()` with rolling-deadline `wait_for`
- [x] **File**: `archon/ai/decomposer.py`
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
  - Unit: `test_route_task_wait_for_negative_remaining_time` — set `_ROUTER_TIMEOUT_S=0.0001` (or mock so that `deadline` is already in the past when the loop starts, e.g. by using a real tiny timeout and adding `await asyncio.sleep(0.01)` before consuming the generator); verify immediate fallback `TaskOutput` with `is_fallback=True`. Do NOT monkeypatch `loop.time()` — this breaks asyncio internals.
  - Unit: `test_route_task_aclose_called_on_timeout` — mock `gen.aclose()` to track calls; trigger timeout; assert `aclose()` was called exactly once
  - Unit: `test_route_task_aclose_cancelled_error_is_handled` — mock `gen.aclose()` to raise `asyncio.CancelledError`; trigger timeout; assert no exception propagates out of `route_task()`
  - Checkpoint: `uv run pytest tests/ai/test_decomposer.py -v --no-cov`

#### Task 1.2 — Fix unprotected `router_gen.aclose()` in `pipeline.send()`
- [x] **File**: `archon/ai/pipeline.py`
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
- [x] **File**: `archon/ai/pipeline.py`
- **Depends on**: nothing
- **Description**:
  - Replace `async with asyncio.timeout(_TASK_DIRECT_TIMEOUT_S):` (line 359) and its inner `async for event in gen: yield event` block with the rolling-deadline `wait_for` pattern
  - The outer `except TimeoutError:` recovery block (lines 402–490: aclose gen, `RecoveryEvent`, `recover_session()`, promote/retry decision) remains **completely unchanged** — only the inner iteration changes
  - The `raise TimeoutError` / `raise` idiom in the new loop propagates to the existing handler: no new recovery logic needed
  - The rolling-deadline pattern does NOT set `gen_closed`. The existing outer `except TimeoutError:` handler at line 402 already calls `gen.aclose()` and sets `gen_closed = True` (line 417). The new pattern simply raises `TimeoutError` to let that existing handler do its work. Do NOT add `gen_closed = True` inside the rolling-deadline loop — the existing handler owns the flag lifecycle through all paths: normal exhaustion (does nothing, gen completes), promotion (line 386, sets True), timeout (line 417, sets True via existing handler).
  - Compatibility: existing tests `test_task_direct_monitored_times_out` and `test_timeout_does_not_deadlock_next_call` should still pass — the timeout behavior is preserved; only the mechanism changes. These are included in the full-suite checkpoint below.
  - **Promotion+recovery timeout note**: The existing `asyncio.timeout(_TASK_DIRECT_TIMEOUT_S)` currently wraps both the iteration loop AND the promotion+recovery call at lines 386-395. After removing the `asyncio.timeout()` block, `_recover_session_in_clean_task()` retains its own timeout protection: it internally calls `asyncio.create_task(_do_restart())` followed by `await asyncio.wait_for(recovery_task, timeout=_RECOVERY_TIMEOUT_S)`. No additional `wait_for` wrapping is needed at the call site — adding one would create double-wrapping. Verify this is still true when implementing (check lines 386-400 of pipeline.py for any refactors).
- **Releasable**: `_task_direct_monitored` primary loop now correctly fires `TimeoutError` into its own handler when the consumer is executing between iterations
- **Tests (TDD)** — `tests/ai/test_pipeline.py`:
  - [x] Unit: `test_task_direct_monitored_timeout_fires_during_consumer_async_work` — consumer calls `await asyncio.sleep(0.01)` between iterations; mock `_decomposer.answer()` to yield one event then sleep forever; set `_TASK_DIRECT_TIMEOUT_S=0.05`; verify `RecoveryEvent(phase="timeout_detected", ...)` is yielded (not silence)
  - [x] Unit: `test_task_direct_monitored_aclose_called_on_timeout` — mock `gen.aclose()`; trigger timeout; assert it was called
  - [x] Unit: `test_task_direct_monitored_aclose_cancelled_error_is_handled` — `gen.aclose()` raises CancelledError inside the timeout recovery block; verify it does not propagate out of `_task_direct_monitored()`
  - [x] Unit: `test_task_direct_monitored_negative_remaining_time` — set `_TASK_DIRECT_TIMEOUT_S=0.0001` (or arrange for deadline to be already elapsed before the loop starts, e.g. real tiny timeout + `await asyncio.sleep(0.01)` before consuming); verify immediate TimeoutError triggers the recovery handler (RecoveryEvent yielded). Do NOT monkeypatch `loop.time()` — this breaks asyncio internals.
  - [x] Checkpoint: `uv run pytest tests/ai/test_pipeline.py -v --no-cov`

#### Task 1.4 — Replace `asyncio.timeout()` in `_task_direct_monitored` retry loop
- [x] **File**: `archon/ai/pipeline.py`
- **Depends on**: Task 1.3
- **Description**:
  - Replace `async with asyncio.timeout(_RETRY_TIMEOUT_S):` (line 466) and its `async for event in retry_gen: yield event` with the rolling-deadline `wait_for` pattern
  - The outer `except TimeoutError:` at line 469 (logs, optional session recovery, `ErrorEvent` yield) remains unchanged — `raise` from the new loop propagates to it
  - `retry_gen.aclose()` in the `finally` block at line 488 is already wrapped in `asyncio.wait_for` — leave it unchanged
- **Releasable**: retry path correctly fires its `except TimeoutError:` handler and yields `ErrorEvent` when the consumer is executing between iterations
- **Tests (TDD)** — `tests/ai/test_pipeline.py`:
  - [x] Unit: `test_task_direct_retry_timeout_fires_during_consumer_async_work` — simulate scenario where primary times out (BAM disabled, retry path taken); retry consumer sleeps between iterations; mock `retry_gen` to yield one event then sleep; set `_RETRY_TIMEOUT_S=0.05`; verify `ErrorEvent` is eventually yielded (not silence)
  - [x] Unit: `test_task_direct_retry_aclose_cancelled_error_is_handled` — `retry_gen.aclose()` raises CancelledError in the finally block; verify it does not propagate
  - [x] Unit: `test_task_direct_retry_negative_remaining_time` — set `_RETRY_TIMEOUT_S=0.0001` (or arrange for deadline to already be elapsed before the loop starts); verify immediate TimeoutError triggers ErrorEvent. Do NOT monkeypatch `loop.time()` — this breaks asyncio internals.
  - [x] Checkpoint: `uv run pytest tests/ai/test_pipeline.py -v --no-cov`

#### Task 1.5 — Add `CancelledError` handler to `handler.py`
- [x] **File**: `archon/chat/handler.py`
- **Depends on**: nothing
- **Description**:
  - In `handle_message()`, add `except asyncio.CancelledError:` immediately before the existing `except Exception as exc:` at line 394
  - Handler body:
    1. `logger.warning("Message processing cancelled for user %d — task received CancelledError", user_id)`
    2. `try: await message.answer("⚙️ Processing was interrupted unexpectedly. The system is recovering — please resend your message.") / except Exception: logger.warning("Failed to deliver cancellation notice to user %d", user_id)`
    3. `raise` — re-raise so aiogram handles task cleanup
  - After `await message.answer(...)`, optionally also call `await history_manager.record_archon_message(interrupted_text)` if `history_manager is not None`, where `interrupted_text` is the same string passed to `message.answer()` (e.g., `"⚙️ Processing was interrupted unexpectedly. The system is recovering — please resend your message."`). Both calls are wrapped in the same `try/except Exception` so either can fail without preventing the re-raise.
  - The `finally:` block (beacon task cancel) continues to run after re-raise, which is correct
- **Releasable**: any task cancellation in the message handler produces a logged warning and a user-visible Telegram message before the CancelledError propagates
- **Tests (TDD)** — `tests/chat/test_handler.py`:
  - Unit: `test_handle_message_notifies_user_on_cancelled_error` — mock `session.send()` to raise `asyncio.CancelledError` on first iteration; call `handle_message(...)`; assert `message.answer` called with interruption text; assert `logger.warning` called; assert `asyncio.CancelledError` is re-raised
  - Unit: `test_handle_message_cancelled_error_re_raised` — same setup; assert `pytest.raises(asyncio.CancelledError)` wrapping the handler call
  - Unit: `test_handle_message_cancelled_error_telegram_send_fails` — `message.answer` raises `TelegramError` inside the cancellation handler; assert `asyncio.CancelledError` still propagates (outer `except Exception` inside the cancellation handler suppresses Telegram failure correctly)
  - Checkpoint: `uv run pytest tests/chat/test_handler.py -k "cancelled_error" -v --no-cov`

#### Task 1.6 — Add `CancelledError` handler to `voice.py`
- [x] **File**: `archon/chat/voice.py`
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
- [x] **File**: `tests/ai/test_pipeline_e2e.py` (new file)
- **Depends on**: Tasks 1.1, 1.2, 1.3, 1.4
- **Description**:
  - New test file wiring `Pipeline` → a consumer coroutine that does real async work between iterations
  - The consumer simulates `handler.py` behavior: `await asyncio.sleep(0.05)` between each event (representing Telegram send latency)
  - Use a mock router session that yields one `ThinkingResult` then sleeps forever; set `_ROUTER_TIMEOUT_S=0.1`
  - Assert: the consumer receives a `FallbackNoticeEvent` (once Task 2.2 is applied) or, before Task 2.2, does not hang indefinitely — receives either an event or clean completion. The test should check that `pipeline.send()` terminates (does not hang) and that either (a) a `Response` event arrives from the direct fallback path, or (b) a `FallbackNoticeEvent` arrives. Do NOT assert `TaskOutput(is_fallback=True)` — `TaskOutput` is consumed internally by `pipeline.send()` and never yielded to the external consumer.
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
- [x] **File**: `archon/ai/decomposer.py`
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
- [x] **File**: `archon/ai/decomposer.py`
- **Depends on**: Task 1.1
- **Description**:
  - In `route_task()`, change the `except TimeoutError:` fallback (current line 403):
    - Old: `yield TaskOutput(scope="small", prompt=prompt, is_fallback=True, fallback_reason="")`
    - New: `yield TaskOutput(scope="small", prompt=prompt, is_fallback=True, fallback_reason="Router timed out — handling directly")`
  - `Pipeline.send()` at line 261 already gates `FallbackNoticeEvent` on `fallback_reason` being non-empty — no pipeline changes needed
  - Also update `test_route_task_fallback_silent_on_reset_timeout` note: that test covers the `_ROUTER_RESET_TIMEOUT_S` path (line 383), not this path — it is **not** affected by this change and must remain unchanged
  - Other `fallback_reason=""` sites in decomposer.py (`_ROUTER_RESET_TIMEOUT_S` fallback at line 338, reset exception at line 342, init timeout at line 379, init exception at line 383) are **NOT changed** in this task — they remain with empty strings. Only the main `_ROUTER_TIMEOUT_S` timeout fallback at line 403 gets the visible reason string. Changing the other sites would be a separate decision and may have different UX implications.
  - Optionally (if desired for consistency): also change line 383 reset-init fallback `fallback_reason=""` to `"Router init timed out — handling directly"` — treat as a separate decision; do not bundle unless explicitly agreed
- **Releasable**: in verbose/debug mode, users see `FallbackNoticeEvent` explaining why routing was bypassed
- **Tests (TDD)** — `tests/ai/test_decomposer.py` and `tests/ai/test_pipeline.py`:
  - [x] Unit: `test_route_task_timeout_fallback_reason_non_empty` — trigger router session timeout via slow mock; assert yielded `TaskOutput.fallback_reason == "Router timed out — handling directly"`
  - [x] Unit: `test_pipeline_emits_fallback_notice_event_on_router_timeout` — `Pipeline.send()` with timed-out router; assert `FallbackNoticeEvent` emitted with correct reason (in any mode — unconditional)
  - [x] Checkpoint: `uv run pytest tests/ai/test_decomposer.py -k "fallback_reason" -v --no-cov && uv run pytest tests/ai/test_decomposer.py -v --no-cov && uv run pytest tests/ai/test_pipeline.py -k "fallback_notice" -v --no-cov`

---

### Phase 3 — Classifier Hardening
> **Releasable**: Task 3.1 + 3.2 together make classifier internals visible in debug mode; Task 3.3 is independently deployable; Task 3.4 (investigation) must complete before Task 3.5 (implementation) can begin

#### Task 3.1 — Add `events` field to `ClassifierResult` and collect non-Response events
- [x] **File**: `archon/ai/classifier.py`
- **Depends on**: nothing
- **Description**:
  - Add `events: list[Event] = field(default_factory=list)` to `ClassifierResult` dataclass (after `parse_error: str = ""`)
  - In `Classifier.classify()`, collect all non-Response events into a local list `result_events: list[Event] = []`; in the `async for` loop: `if isinstance(event, Response): raw_response = event.content` else `result_events.append(event)`
  - Pass `events=result_events` when constructing the returned `ClassifierResult`
  - Type import: add `Event` to the import from `archon.ai.event_mapper` (currently only `Response` is imported from that module)
  - The classifier docstring at line 37 says "No events yielded — just returns data"; update to: "Non-Response events (ThinkingResult etc.) are collected and returned in `events` for debug-mode surfacing."
- **Releasable**: `ClassifierResult` carries internal events; `Pipeline.send()` can inspect them (Task 3.2)
- **Tests (TDD)** — `tests/ai/test_classifier.py`:
  - [x] Unit: `test_classifier_preserves_non_response_events` — mock session yields `[ThinkingResult(content="thinking..."), Response(content='{"intent":"task","confidence":0.9}')]`; assert `result.events == [ThinkingResult(content="thinking...")]`
  - [x] Unit: `test_classifier_empty_events_on_response_only` — mock session yields only `Response`; assert `result.events == []`
  - [x] Unit: `test_classifier_result_events_field_type` — assert `result.events` is a list (regression: default_factory used correctly, not a shared mutable default)
  - Checkpoint: `uv run pytest tests/ai/test_classifier.py -v --no-cov`

#### Task 3.2 — Yield classifier events in `Pipeline.send()` unconditionally
- [x] **Files**: `archon/ai/pipeline.py`, `archon/chat/telegram_formatter.py`, `archon/chat/handler.py`
- **Depends on**: Task 3.1
- **Description**:
  - In `Pipeline.send()`, after `result = await self._classifier.classify(prompt)` (around line 192) and before `yield ClassificationEvent(...)`, yield all events from `result.events` with source stamping:
    ```python
    for clf_event in result.events:
        if isinstance(clf_event, ThinkingResult):
            yield dataclasses.replace(clf_event, source='classifier')
    ```
  - Only `ThinkingResult` events are yielded — other event types (ErrorEvent, ToolStarted, etc.) are silently dropped. This is intentional: only classifier thinking is surfaced in debug mode; other classifier internal events (which should not occur given `tools=[]`, `max_turns=1`) are discarded defensively. Add a `logger.debug("Dropped non-ThinkingResult classifier event: %s", type(clf_event).__name__)` in the else-branch so future maintainers know the drop is deliberate.
  - **Import**: Add `ThinkingResult` to the `from archon.ai.event_mapper import (...)` block in `pipeline.py` (currently at line 14). This import is required for the `isinstance(clf_event, ThinkingResult)` check.
  - Before yielding, stamp each event: `clf_event = dataclasses.replace(clf_event, source='classifier')` — this distinguishes classifier `ThinkingResult` from orchestrator `ThinkingResult` so `format_event()` can apply the correct filtering.
  - Pipeline yields classifier events unconditionally — mode-based filtering (suppress in quiet/normal/verbose, show in debug only) belongs in `format_event()` in `archon/chat/telegram_formatter.py`, following the same pattern as `ClassificationEvent` (filtered at line 126-128 in `format_event`, NOT in handler.py)
  - In `format_event()` (telegram_formatter.py), add the classifier source check INSIDE the existing `isinstance(event, ThinkingResult)` branch (currently at line 136), NOT as a new top-level block. Pattern: `if getattr(event, 'source', '') == 'classifier': return [rendered_output] if mode == 'debug' else []` before falling through to the generic ThinkingResult rendering. This mirrors the existing `is_router_event()` guard pattern. The `source='classifier'` check must be the **FIRST statement** inside the `isinstance(event, ThinkingResult)` block at line 136, before the existing `if mode not in (...)` guard at line 137. Note: there is a SECOND `ThinkingResult` check at line 120 inside the `is_router_event()` guard — do NOT modify that one; only the line 136 branch (general path) needs the classifier source check. Placing the check after the mode guard at line 137 would apply the wrong suppression logic.
  - A `ThinkingResult` with `source='classifier'` emitted during quiet mode also bypasses handler.py quiet-mode beacon counting by adding a **standalone `if` block** (not an `elif`) immediately after the `is_router_event()` block (after line ~298), before the existing `elif isinstance(event, (SubagentStarted, ...))` chain: `if getattr(event, 'source', '') == 'classifier': continue`. This prevents classifier thinking from inflating the beacon counter.
  - **Handler quiet-mode skip**: In `handler.py` quiet-mode event dispatch (around line 295), add a **standalone `if` block** (not an `elif`) immediately after the `is_router_event()` block (after line ~298), before the existing `elif isinstance(event, (SubagentStarted, ...))` chain: `if getattr(event, 'source', '') == 'classifier': continue`. This must be a standalone `if` (not `elif`) so it is always checked, and must come before the `elif isinstance(event, ThinkingResult)` at line 308 to prevent classifier thinking from incrementing beacon counts.
  - **History recording**: Classifier events stamped with `source='classifier'` will be recorded in session history files via `handler.py`'s `record_event()` call. This is intentional: history files capture all pipeline events for debugging. If classifier thinking in history is undesirable, add `'classifier'` as a suppressible source via the `suppressed_events` config — this is a follow-on concern.
  - Events yielded here are NOT tagged with `source="router"` — they use `source='classifier'` to distinguish them from both orchestrator events and router events
  - No changes to the `ClassificationEvent` yield logic
- **Releasable**: classifier events flow through Pipeline for handler.py to filter by mode; in debug mode users see classifier thinking before the classification decision
- **Tests (TDD)** — `tests/ai/test_pipeline.py` and `tests/chat/test_telegram_formatter.py`:
  - Unit (`tests/ai/test_pipeline.py`): `test_pipeline_yields_classifier_events_unconditionally` — mock classifier returning `ClassifierResult(events=[ThinkingResult(content="...")])` alongside a valid classification; assert classifier `ThinkingResult` with `source='classifier'` appears in the stream before `ClassificationEvent` regardless of notification mode (only `ThinkingResult` events are yielded; other event types are filtered by the isinstance check)
  - Unit (`tests/ai/test_pipeline.py`): `test_pipeline_classifier_events_stamped_with_classifier_source` — mock classifier returning `ClassifierResult(events=[ThinkingResult(content="...")])`, collect stream; assert the `ThinkingResult` in stream has `source == 'classifier'` exactly. Note: this value must match what `format_event()` and `handler.py` check — it is a load-bearing stringly-typed contract.
  - Unit (`tests/chat/test_telegram_formatter.py`): `test_format_event_suppresses_classifier_thinking_in_normal_mode` — `mode="normal"`, classifier `ThinkingResult` with `source="classifier"`; assert `format_event()` returns `None` or empty (event suppressed)
  - Unit (`tests/chat/test_telegram_formatter.py`): `test_format_event_suppresses_classifier_thinking_in_verbose_mode` — `mode="verbose"`, classifier `ThinkingResult` with `source="classifier"`; assert `format_event()` returns `None` or empty (only debug surfaces them)
  - Unit (`tests/chat/test_telegram_formatter.py`): `test_format_event_delivers_classifier_thinking_in_debug_mode` — `mode="debug"`, classifier `ThinkingResult` with `source="classifier"`; assert rendered output is non-empty
  - Unit (`tests/chat/test_telegram_formatter.py`): `test_format_event_regular_thinking_unchanged` — `ThinkingResult(content="...", source="orchestrator")` with `mode="normal"`; assert `format_event()` returns non-empty output (regression: classifier filtering must not intercept non-classifier ThinkingResult)
  - Unit (`tests/chat/test_handler.py`): `test_quiet_mode_classifier_thinking_not_counted_in_beacon` — set mode=quiet; yield `ThinkingResult(source='classifier')` through handler; assert beacon counts["thinking"] is NOT incremented
  - Unit (`tests/ai/test_pipeline.py`): `test_pipeline_drops_non_thinking_classifier_events` — mock classifier returning `ClassifierResult(events=[ErrorEvent(message="timeout")])` alongside a valid classification; collect stream; assert no `ErrorEvent` with `source='classifier'` appears in stream; assert the `ErrorEvent` was silently dropped (not propagated)
  - Checkpoint: `uv run pytest tests/ai/test_pipeline.py tests/chat/test_telegram_formatter.py tests/chat/test_handler.py -v --no-cov`

#### Task 3.3 — Strengthen classifier system prompt
- [x] **File**: `archon/ai/prompts/classifier.md`
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
  - Unit: `test_classifier_prompt_forbids_reasoning` — read `classifier.md`; assert it contains key constraint phrases (e.g. `'Do NOT evaluate'` and `'ONLY classify it'`). Note: this is a regression guard test — it will need updating if the prompt is legitimately reworded. A behavioral live test is stronger but requires a real API call.
  - Live E2E: `test_classifier_raw_response_is_directly_json_parseable` — (existing test if present; tighten assertion to also verify no text after the closing `}`)
  - Checkpoint: `uv run pytest tests/ai/test_classifier.py -k "prompt" -v --no-cov`

#### Task 3.4 — Investigate SDK thinking-control capability
- [ ] **Files**: read-only investigation — `archon/ai/claude_session.py`, SDK docs/source
- **Depends on**: nothing
- **Description**:
  - Check `ClaudeSession.__init__` signature — does it accept a `thinking` or `no_thinking` parameter?
  - Check the Claude Agent SDK docs/source for per-session thinking control
  - Document whether `disable_thinking` is feasible and what the parameter name is
  - Outcome: YES (with parameter name) or NO (document the limitation)
  - If NO: `_ROUTER_TIMEOUT_S = 180` (Task 2.1) remains the sole mitigation; Task 3.5 is deferred
  - No code changes in this task — investigation only
- **Releasable**: produces a documented decision that unblocks Task 3.5

#### Task 3.5 — Implement `disable_thinking` for Classifier and Router sessions
- [ ] **Files**: `archon/ai/classifier.py`, `archon/ai/decomposer.py`, and possibly `archon/ai/claude_session.py`
- **Depends on**: Task 3.4
- **Description** (conditional on Task 3.4 outcome — YES only):
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
