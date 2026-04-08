# BUG: Router Silent Failure — Film Research Incident (2026-04-08)

**Severity:** Critical
**Status:** Pending fix
**Reported:** 2026-04-08
**Session:** 09:44–09:53 UTC
**Prompt:** "Make a deep research about what films were made with AI assistant..."

---

## Summary

A user request produced no response and no error. The user saw `⏳ Processing...`, then
a `💭 [Router] Thinking` block from the router, then silence. Eight minutes later a `ping`
received an immediate `Pong.` — demonstrating the system had not hung but had silently
dropped the entire request with no user notification.

---

## Evidence

### archon.log (key entries)

```
09:44:25  Classification: intent=task confidence=0.95 duration=10.6s
09:44:29  Claude session started (Router, lazy-started)
09:44:29  Injecting history into router session: 2026-04-06-compacted.md, 2026-04-07-compacted.md
──────────── 9-minute gap: zero entries ────────────
09:53:50  Failed to fetch updates - TelegramNetworkError (unrelated)
09:53:52  Message received from user 154643621 (4 chars) [ping]
09:53:56  Classification: intent=chat confidence=1.00 duration=3.9s
```

**Smoking gun:** For the second request ("you started a task") the log contains:
```
09:55:29  route_task scope=trivial fallback=False for prompt: you started a task...
```
This entry is **absent** for the first request. `Pipeline.send()` never reached the
`logger.info("route_task scope=...")` line.

### session history (key entries)

```
07:44:25  🏷 Classification (task, 0.95) — raw_response contains JSON + extra Haiku reasoning
07:44:29  📌 Context injected [router_history]
07:44:29  📌 Context injected [router_workspace_agents]
07:45:29  💭 [Router] Thinking — ...ends mid-sentence, no Response follows
──────────── next entry is the "ping" conversation ────────────
```

No `🔀 Pipeline · Routing:` event. No main-session ThinkingResult. No Response. No error.

---

## Root Cause Analysis

### Bug 1 (Critical — Architecture): `asyncio.timeout()` fires in wrong execution context

**File:** `archon/ai/decomposer.py:391`
**Constant:** `_ROUTER_TIMEOUT_S: float = 60.0`

```python
async def route_task(self, prompt, ...) -> AsyncGenerator[Event | TaskOutput, None]:
    ...
    gen = router.send(instruction)
    try:
        try:
            async with asyncio.timeout(_ROUTER_TIMEOUT_S):   # ← timer starts
                async for event in gen:
                    yield event   # ← generator SUSPENDS; task moves to handler.py
                    ...
        except TimeoutError:          # ← intended catch — never reached
            yield TaskOutput(scope="small", ...)
            return
```

When `yield event` suspends `route_task()`, the consumer (`pipeline.send()`) resumes and
continues processing — calling `await history_manager.record_event()`, `await message.answer()`,
etc. in `handler.py`. In Python 3.12, `asyncio.timeout()` expires by calling `task.cancel()`
— on the **current task**, wherever it happens to be executing.

At the 60-second mark the task was in `handler.py` logging/sending the ThinkingResult.
The `CancelledError` is raised there. It propagates up through `pipeline.send()`'s
`async for item in router_gen` loop, which hits `pipeline.send()`'s
`finally: await router_gen.aclose()` block. The `aclose()` call throws `GeneratorExit`
(not `CancelledError`) into `route_task()`. The `asyncio.timeout()` context manager's
`__aexit__` inside `route_task()` receives `GeneratorExit`, not `CancelledError`, so it
cannot convert the error to `TimeoutError`. The `except TimeoutError:` fallback path
inside `route_task()` never executes.

**`handler.py:394`:**
```python
except Exception as exc:   # CancelledError inherits BaseException, not Exception → missed
```

Result: silent failure — but only because **both Bug 1 AND Bug 3 are present together**.
Bug 1 ensures the fallback path in `route_task()` never executes (wrong context, receives
`GeneratorExit` not `CancelledError`). Bug 3 ensures the `CancelledError` propagating
through `handler.py` is swallowed without user notification. Either bug alone would cause
degraded behavior; together they produce complete silence.

**Timing note:** The bug is a race condition. It only manifests when `asyncio.timeout()`
fires while the consumer (handler.py) is executing *between* iterations — i.e., after
`yield event` suspends the generator but before the next `__anext__()` call. If the
timeout fires *during* `__anext__()` (while the generator is actively running), the
`except TimeoutError:` catches it correctly. For this specific incident (60s timeout,
ThinkingResult arriving at T+60s), the generator had just yielded and the consumer was
actively executing `await history_manager.record_event()` / `await message.answer()`,
making the race deterministic in practice.

No user message. No log entry. The pipeline lock is eventually released deterministically
by `async with self._lock:`'s `__aexit__` during `CancelledError` stack unwinding.

**This is the architectural root cause of the entire incident.**

---

### Bug 2 (Critical — Configuration): `_ROUTER_TIMEOUT_S = 60s` too short for Sonnet + thinking

**File:** `archon/ai/decomposer.py:52`

The router session uses `self._model` — the same user-selected model as the main session
(`claude-sonnet-4-6`). Sonnet with extended thinking can use **60+ seconds for the
ThinkingResult alone**, leaving zero time for the routing JSON (Response).

The ThinkingResult arrived at exactly T+60s. Even if Bug 1 were fixed, the router would
time out on nearly every complex request and silently fall back to `scope="small"`.

---

### Bug 3 (Critical — Handler): `CancelledError` is silently swallowed in `handler.py`

**File:** `archon/chat/handler.py:394`

```python
try:
    async for event in session.send(text):
        ...
        await history_manager.record_event(user_id, event)
        ...
        await message.answer(part, parse_mode="HTML")
except Exception as exc:       # ← misses BaseException subclasses
    logger.error(...)
    await message.answer("❌ Error: ...")
finally:
    ...
```

`CancelledError` inherits from `BaseException`, not `Exception`. Any task cancellation —
from the router timeout, from aiogram shutdown, from any external `task.cancel()` — results
in:

- No user notification
- No `logger.error()` entry
- No attempt to recover
- User left hanging indefinitely

**Principle violated:** The system must never silently swallow errors. Every failure path
must (1) log to archon.log, (2) inform the user via Telegram, (3) attempt recovery if
possible, and (4) if no recovery is possible, still inform the user.

---

### Bug 4 (High — Classifier): Haiku ThinkingResult events discarded — invisible even in debug mode

**File:** `archon/ai/classifier.py:120`

```python
async for event in self._session.send(prompt):
    if isinstance(event, Response):
        raw_response = event.content
    # ThinkingResult, ToolStarted, ToolResult — all silently discarded
```

All non-Response events from the Classifier session are consumed and dropped. Even in
`debug` mode, where all events should be visible, the user never sees Haiku's thinking
process. This violates the observability contract: debug mode must expose all internal
processing.

The 10.6-second classification duration for a simple intent classification indicates the
model was doing extended thinking — that thinking was entirely invisible to the user.

---

### Bug 5 (High — Classifier): Haiku reasons about solving the request instead of classifying it

**File:** `archon/ai/prompts/classifier.md`
**Observed response from Haiku:**

```
{"intent": "task", "confidence": 0.95}

---

I appreciate the scope of this research task, but I need to be transparent about my
limitations before proceeding... [full refusal explaining lack of web search tools]
```

The classifier prompt states "Output ONLY valid JSON" but Haiku:
1. Generated extended thinking about whether it could fulfil the research request
2. Leaked that reasoning into its response text alongside the JSON

The classifier's job is exclusively to determine `chat` vs `task`. It has no business
evaluating whether it can execute the request. This behaviour indicates that extended
thinking is enabled for the classifier session, causing the model to "think its way" into
acting as a problem-solver instead of a classifier. The `parse_classification()` resilient
parser correctly extracted the JSON, but the leaked reasoning text:

- Pollutes `ClassificationEvent.raw_response` stored in session history
- Is visible in the history file as a confusing "refusal" entry
- Would be visible to the user in debug mode (if Bug 4 were fixed) as misleading output

---

### Bug 6 (Medium — UX): Silent router timeout fallback confuses users

**File:** `archon/ai/decomposer.py:402`

```python
# Silent fallback — timeout is an internal routing detail, not a user error.
yield TaskOutput(scope="small", prompt=prompt, is_fallback=True, fallback_reason="")
```

When the router times out, `fallback_reason=""` suppresses any `FallbackNoticeEvent`.
The user sees the Router ThinkingResult (significant effort visible) followed by either
silence (Bug 1) or a direct response with no explanation of what happened.

In the follow-up conversation, the second-pass router incorrectly reported that a 3-agent
plan had been "created" — because it read the ThinkingResult in session history and
mistook the thinking for a completed routing decision.

---

### Bug 7 (Critical — Architecture): `_task_direct_monitored` has identical timeout-in-generator bug

**File:** `archon/ai/pipeline.py:359`

The same architectural flaw present in `route_task()` (Bug 1) exists in the main
execution path `_task_direct_monitored`:

```python
async with asyncio.timeout(_TASK_DIRECT_TIMEOUT_S):
    async for event in gen:
        yield event   # ← same bug: timeout fires in consumer context, not here
```

When `yield event` suspends `_task_direct_monitored()` and the consumer is executing
in `handler.py`, the `asyncio.timeout()` fires in `handler.py`, not inside
`_task_direct_monitored()`. The `except TimeoutError:` inside the generator is never
reached — same `GeneratorExit` mechanism as Bug 1.

**Impact:** This affects ALL non-chat responses — the primary execution path, not just
the router path. Bug 7 has higher traffic exposure than Bug 1.

**Fix:** Apply Fix 1's per-event `asyncio.wait_for()` + rolling deadline approach to
`_task_direct_monitored` identically. See Fix 7.

---

### Bug 8 (Critical — Architecture): Retry path in `_task_direct_monitored` has the same bug

**File:** `archon/ai/pipeline.py:466`

The retry path (the recovery mechanism when the primary execution times out) has the
same `asyncio.timeout()` + `yield` pattern:

```python
async with asyncio.timeout(_RETRY_TIMEOUT_S):
    async for event in retry_gen:
        yield event   # ← same bug: timeout fires in consumer context
```

**Impact:** The retry path is specifically the fallback when Bug 7's primary timeout
fires. If the retry also has this bug, the recovery from Bug 7 itself fails silently —
a double silent failure on a single request.

**Fix:** Apply Fix 1's per-event `asyncio.wait_for()` + rolling deadline approach to the
retry loop identically. See Fix 7.

---

## Failure Chain

```
User: "Make a deep research about what films were made with AI assistant..."
  → Classifier: task, 0.95 (10.6s — Haiku used thinking, leaked reasoning into response)
  → Router session started; context injected (history + agents.md)
  → Router LLM: extended thinking ~60s → ThinkingResult yielded
  → route_task() yields ThinkingResult → pipeline.send() receives it → yields to handler.py
  → handler.py calls await history_manager.record_event() + await message.answer() (takes ~1s)
  → asyncio.timeout(60s) fires WHILE handler.py is executing
    → CancelledError raised in handler.py
    → except Exception: misses CancelledError (BaseException subclass)
    → finally: runs (cancels beacon task)
    → CancelledError propagates up through pipeline.send()'s async for loop
    → pipeline.send() finally block: await router_gen.aclose()  ← no timeout wrapper here
    → aclose() throws GeneratorExit (not CancelledError) into route_task()
    → asyncio.timeout().__aexit__ in route_task() receives GeneratorExit — cannot convert to TimeoutError
    → except TimeoutError: inside route_task() never executes — fallback TaskOutput never yielded
    → CancelledError continues propagating to aiogram — silently discarded
  → main session never called
  → Pipeline lock released deterministically by `async with self._lock:`'s __aexit__ during unwinding
  → User: no response, no error, 8 minutes of silence
```

---

## Proposed Fixes

### Fix 1 (Critical): Replace `asyncio.timeout()` in async generator with per-event `asyncio.wait_for()`

**Principle:** Never use `asyncio.timeout()` inside a yielding async generator when events
are consumed by an external handler. The timeout fires in the task's current execution
context (the consumer), not in the generator's exception handler.

**Approach: per-event `asyncio.wait_for()` with rolling deadline**

Preserves full streaming (events visible as they arrive) while ensuring the timeout fires
inside `route_task()`, not in the consumer.

The key insight: when the generator is suspended at `yield item`, there is **no active
`asyncio.timeout()` context** — the timeout only applies to the individual
`gen.__anext__()` call, which completes before the `yield`. The `asyncio.wait_for()` call
is fully resolved before the generator yields to the consumer.

**Why other options were rejected:**
- *Collect-then-stream*: batches all router events and yields them after routing completes — eliminates streaming, unacceptable UX degradation.
- *Elapsed-time check after yield*: does not guard against a hanging LLM that never delivers the next event (e.g. infinite thinking). Insufficient for production.

```python
# decomposer.py — replace the asyncio.timeout block in route_task()

deadline = asyncio.get_event_loop().time() + _ROUTER_TIMEOUT_S
gen = router.send(instruction)
try:
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            logger.warning(
                "route_task deadline exceeded for prompt: %.100s", prompt
            )
            yield TaskOutput(scope="small", prompt=prompt, is_fallback=True,
                             fallback_reason="Router timed out — handling directly")
            return
        try:
            item = await asyncio.wait_for(gen.__anext__(), timeout=remaining)
        except StopAsyncIteration:
            break
        except TimeoutError:
            logger.warning(
                "route_task timed out after %.0fs for prompt: %.100s",
                _ROUTER_TIMEOUT_S, prompt,
            )
            yield TaskOutput(scope="small", prompt=prompt, is_fallback=True,
                             fallback_reason="Router timed out — handling directly")
            return
        except Exception as exc:
            logger.error("route_task stream error: %s", exc, exc_info=True)
            yield TaskOutput(scope="small", summary="Direct handling", prompt=prompt,
                             is_fallback=True,
                             fallback_reason="Could not plan this task — attempting inline")
            return

        yield item
        if isinstance(item, Response):
            last_response = item
finally:
    try:
        await asyncio.wait_for(gen.aclose(), timeout=5.0)
    except Exception:
        logger.warning("route_task: gen.aclose() timed out or failed", exc_info=True)
```

**Why this is safe in Python 3.12:**
`asyncio.wait_for()` in Python 3.12 uses `asyncio.timeout()` internally. When it fires,
`task.cancel()` is called. The CancelledError is raised at the `await` inside
`gen.__anext__()`. At that moment, `route_task()` is NOT suspended at a `yield` — it is
actively executing the `await asyncio.wait_for(...)` call. The `asyncio.Timeout` context
manager inside `asyncio.wait_for()` correctly intercepts the CancelledError and converts
it to `TimeoutError`, which our `except TimeoutError:` catches. ✓

**Why Option A (collect-then-stream) was rejected:** Batching all router events and
yielding them after the router completes eliminates streaming — the user only sees the
ThinkingResult and tool calls after the full routing decision is made. This is an
unacceptable UX degradation.

**Why Option C (elapsed-time check after yield) was rejected:** Does not guard against
a hanging LLM call mid-event-stream (e.g. thinking that never completes). If the model
hangs forever between events, the check never fires. Not sufficient for production.

---

### Fix 2 (Critical): Increase `_ROUTER_TIMEOUT_S` to 180 seconds

**File:** `archon/ai/decomposer.py:52`

> **Primary fix: See Fix 5b** (disable extended thinking for the router session).
> Increasing the timeout is defense-in-depth only. If Fix 5b is applied, 60s is more
> than sufficient; 180s provides a safety margin.

```python
_ROUTER_TIMEOUT_S: float = 180.0  # was 60.0 — Sonnet thinking alone can take 60-90s
```

Sonnet with extended thinking needs ~60-90s for thinking + ~10-20s for routing JSON +
buffer. 180s is the minimum safe value if extended thinking remains enabled for the
router session. If Fix 5b (disable extended thinking for the router) is applied first,
routing latency drops to ~5s and 60s is already more than sufficient.

**Dependency:** If Fix 5b cannot be implemented (SDK does not support disabling thinking
per-session), Fix 2's `180.0` changes from defense-in-depth to a hard requirement. The
constant value remains 180 in both cases, but the rationale changes.

---

### Fix 3 (Critical): Handle `CancelledError` in `handler.py` and `voice.py`

**Files:** `archon/chat/handler.py` and `archon/chat/voice.py`

Apply the same fix to both files. `voice.py`'s message handler has the same
`except Exception as exc:` blind spot for `CancelledError`.

**Principle:** Never swallow task cancellation. Log it, inform the user, attempt recovery
if possible, and always leave the user with an explanation.

```python
try:
    async for event in session.send(text):
        ...
    await check_auto_compact(...)
except asyncio.CancelledError:
    # Task was cancelled (e.g. asyncio.timeout from router, aiogram shutdown, etc.)
    # Log the incident and inform the user before re-raising.
    logger.warning(
        "Message processing cancelled for user %d — task received CancelledError",
        user_id,
    )
    try:
        interrupted_msg = (
            "⚙️ Processing was interrupted unexpectedly. "
            "The system is recovering — please resend your message."
        )
        await message.answer(interrupted_msg)
        if history_manager is not None:
            await history_manager.record_archon_message(interrupted_msg)
    except Exception:
        logger.warning("Failed to deliver cancellation notice to user %d", user_id)
    raise  # re-raise so aiogram cleans up the task properly
    # NOTE: Before implementing, verify aiogram 3.x dispatcher behavior when a message
    # handler raises CancelledError. In aiogram 3.x, unhandled exceptions propagate
    # through the middleware chain. If aiogram catches CancelledError at the dispatcher
    # level and logs it, the raise is safe. If aiogram propagates it to the event loop
    # where it could cancel the polling task, a conditional re-raise may be needed.
    # Verification required.
except Exception as exc:
    logger.error(
        "Error processing message for user %d (%s)", user_id, type(exc).__name__,
        exc_info=True,
    )
    try:
        error_text = f"❌ Error: {html.escape(str(exc))}"
        await message.answer(error_text)
        if history_manager is not None:
            await history_manager.record_archon_message(error_text)
    except Exception:
        logger.warning("Failed to send error notification to user %d", user_id, exc_info=True)
finally:
    if update_task is not None:
        update_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await update_task
```

---

### Fix 4 (High): Surface Classifier events in debug mode

**File:** `archon/ai/classifier.py`

Extend `ClassifierResult` to carry all events from the classifier session:

```python
@dataclass
class ClassifierResult:
    classification: Classification
    raw_response: str = ""
    duration_s: float = 0.0
    parse_error: str = ""
    error: str = ""
    events: list = field(default_factory=list)  # ThinkingResult etc. for debug mode
```

In `Classifier.classify()`:
```python
async for event in self._session.send(prompt):
    if isinstance(event, Response):
        raw_response = event.content
    else:
        result_events.append(event)  # collect ThinkingResult, etc.
```

In `Pipeline.send()`, yield classifier events before `ClassificationEvent` in debug mode:
```python
# After classify() returns, before yielding ClassificationEvent:
if mode == "debug":
    for clf_event in result.events:
        yield clf_event
yield ClassificationEvent(...)
```

---

### Fix 5 (High): Harden the Classifier against off-script reasoning

**Two-part fix:**

**5a — Strengthen classifier system prompt** (`archon/ai/prompts/classifier.md`):

```markdown
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

**5b — Disable extended thinking for the Classifier session** (`archon/ai/classifier.py`):

The 10.6-second classification duration with full reasoning output indicates extended
thinking was active. The classifier does not benefit from deep reasoning — it makes a
binary classification. Disabling thinking reduces latency to <2s and eliminates the
model's ability to "think its way" into solving the user's problem.

Pass `thinking=disabled` (or SDK equivalent) when constructing the Classifier's
`ClaudeSession`. If the SDK does not support per-session thinking control, add a
`no_thinking` parameter to `ClaudeSession`.

> **Prerequisite:** Verify that extended thinking is actually enabled for the Classifier
> session. Check `ClaudeSession` constructor parameters and SDK defaults. The 10.6s
> classification duration and leaked reasoning strongly suggest it is enabled, but this
> should be confirmed before implementing. Also verify the exact SDK parameter name for
> disabling thinking — `thinking=disabled` is illustrative, not verified API syntax.

**Note:** The same reasoning applies to the Router session. Fix 5b should also be
applied to the router's `ClaudeSession` — disabling extended thinking there reduces
routing latency from ~60-90s to ~5s, making Fix 2's timeout increase unnecessary (see
Fix 2 note above).

---

### Fix 6 (Medium): Make router timeout fallback visible to users

**File:** `archon/ai/decomposer.py` + `archon/ai/pipeline.py`

When the router falls back, change `fallback_reason=""` to a user-visible reason:

```python
yield TaskOutput(
    scope="small",
    prompt=prompt,
    is_fallback=True,
    fallback_reason="Router timed out — handling directly",
)
```

`Pipeline.send()` will then emit a `FallbackNoticeEvent` which is shown in verbose/debug
mode, explaining why the user sees a ThinkingResult followed by a direct response.

### Fix 7 (Critical): Apply per-event `asyncio.wait_for()` to `_task_direct_monitored` and retry path

**File:** `archon/ai/pipeline.py`

Apply Fix 1's per-event `asyncio.wait_for()` + rolling deadline pattern to two additional
locations in `_task_direct_monitored`. The existing `except TimeoutError:` recovery blocks
remain unchanged — only the inner iteration mechanism changes.

**1. Primary execution loop** (`pipeline.py:359`) — replace `asyncio.timeout(_TASK_DIRECT_TIMEOUT_S)`:

```python
# Replace:
#   async with asyncio.timeout(_TASK_DIRECT_TIMEOUT_S):
#       async for event in gen:
#           ... process event ...
#           yield event
#
# With:
deadline = asyncio.get_event_loop().time() + _TASK_DIRECT_TIMEOUT_S
while True:
    remaining = deadline - asyncio.get_event_loop().time()
    if remaining <= 0:
        raise TimeoutError  # caught by existing except TimeoutError: block below
    try:
        event = await asyncio.wait_for(gen.__anext__(), timeout=remaining)
    except StopAsyncIteration:
        break
    except TimeoutError:
        raise  # propagate to outer except TimeoutError: which handles recovery
    # ... existing event processing logic (ToolStarted, promotion check, etc.) ...
    yield event
# The outer except TimeoutError: block (lines 402-484) handles the rest:
# aclose gen, RecoveryEvent, recover_session, promote or retry — unchanged.
```

**2. Retry path** (`pipeline.py:466`) — replace `asyncio.timeout(_RETRY_TIMEOUT_S)`:

```python
# Replace:
#   async with asyncio.timeout(_RETRY_TIMEOUT_S):
#       async for event in retry_gen:
#           yield event
#
# With:
retry_deadline = asyncio.get_event_loop().time() + _RETRY_TIMEOUT_S
while True:
    remaining = retry_deadline - asyncio.get_event_loop().time()
    if remaining <= 0:
        raise TimeoutError
    try:
        event = await asyncio.wait_for(retry_gen.__anext__(), timeout=remaining)
    except StopAsyncIteration:
        break
    except TimeoutError:
        raise  # caught by existing except TimeoutError: at line 469
    yield event
```

**Note:** `pipeline.send()` line 235 calls `await router_gen.aclose()` without a timeout
wrapper. This is a latent hang risk: if the router SDK subprocess is unresponsive, this
blocks the pipeline lock indefinitely. Add `asyncio.wait_for(router_gen.aclose(), timeout=_ACLOSE_TIMEOUT_S)`.

---

## Fix Priority Matrix

| # | Bug | Severity | Fix complexity | User impact |
|---|-----|----------|----------------|-------------|
| 1 | `asyncio.timeout()` fires in wrong context (router) | **Critical** | Medium | Eliminates silent total failures (router path) |
| 7 | `_task_direct_monitored` + retry path have identical bug | **Critical** | Medium | Eliminates silent total failures (ALL non-chat responses) |
| 3 | `CancelledError` swallowed in handler.py + voice.py | **Critical** | Low | User always gets a message |
| 2 | `_ROUTER_TIMEOUT_S = 60s` too short | **Critical** | Trivial | Router works for complex tasks |
| 4 | Classifier events discarded (debug invisible) | **High** | Low | Full debug observability |
| 5 | Haiku reasons about solving, not classifying | **High** | Low | Correct classifier behaviour |
| 6 | Silent router timeout fallback | Medium | Low | User understands what happened |

**Fixes 1 + 3 together** prevent the silent failure class entirely (router path).
**Fix 7** addresses the same bug on the main execution path (higher traffic) including the retry path.
**Fix 2** addresses the specific trigger; **Fix 5b is the primary fix** (primary: disable
extended thinking; Fix 2 timeout increase is defense-in-depth only).
**Fixes 4 + 5** restore the observability and correctness contract for the Classifier.
**Fix 6** is UX polish.

---

## Required Tests

These tests MUST be written before implementing the fixes (TDD is mandatory).

### Fix 1: per-event wait_for in route_task()
File: `tests/ai/test_decomposer.py`

1. **test_route_task_timeout_fires_during_consumer_async_work** — Reproduces Bug 1: create an async consumer that calls `await asyncio.sleep(0.1)` between each event iteration; configure timeout to fire during that sleep; verify `TaskOutput(is_fallback=True)` is yielded rather than silent failure.
2. **test_route_task_wait_for_handles_stop_async_iteration** — Generator yields N events then exhausts naturally; verify all N events yielded and loop exits without RuntimeError.
3. **test_route_task_wait_for_negative_remaining_time** — Deadline already elapsed when next iteration starts; verify fallback TaskOutput is yielded immediately.
4. **test_route_task_aclose_called_on_timeout** — Timeout fires; verify `gen.aclose()` is called in the finally block.
5. **test_route_task_aclose_cancelled_error_is_handled** — `gen.aclose()` raises CancelledError; verify it does not propagate out of route_task().

### Fix 2: _ROUTER_TIMEOUT_S constant
File: `tests/ai/test_decomposer.py`

6. **test_router_timeout_constant_minimum_value** — Policy assertion: `assert _ROUTER_TIMEOUT_S >= 60`, encoding the minimum viable timeout. Note: if Fix 5b (disable extended thinking) is applied, 60s is sufficient; if thinking remains enabled, 120s+ is required. The test uses 60 as the floor since Fix 5b takes precedence.

### Fix 3: CancelledError in handler.py and voice.py
Files: `tests/chat/test_handler.py` and `tests/chat/test_voice.py`

7. **test_handle_message_notifies_user_on_cancelled_error** — Session.send() raises CancelledError mid-stream; verify user receives interruption message AND CancelledError is re-raised.
8. **test_handle_message_cancelled_error_re_raised** — Same setup; verify `pytest.raises(asyncio.CancelledError)` from the handler.
9. **test_handle_message_cancelled_error_telegram_send_fails** — Notification attempt fails (Telegram unreachable); verify CancelledError is still re-raised.
10. **test_voice_handle_cancelled_error** — Apply tests 7-9 equivalents to voice.py handler.

### Fix 4: Classifier events surfacing
Files: `tests/ai/test_classifier.py` and `tests/ai/test_pipeline.py`

11. **test_classifier_preserves_non_response_events** — Pass [ThinkingResult(...), Response('{"intent":"task","confidence":0.9}')] to mock session; assert result.events == [ThinkingResult(...)].
12. **test_pipeline_yields_classifier_events_in_debug_mode** — Configure mode to "debug"; assert classifier ThinkingResult events appear before ClassificationEvent.
13. **test_pipeline_suppresses_classifier_events_in_normal_mode** — Configure mode to "normal"; assert no classifier events appear.

### Fix 6: fallback_reason non-empty
Files: `tests/ai/test_decomposer.py` and `tests/ai/test_pipeline.py`

14. **test_route_task_timeout_fallback_reason_non_empty** — Timer timeout; assert yielded TaskOutput.fallback_reason != "".
15. **test_pipeline_emits_fallback_notice_event_on_timeout** — Pipeline.send() processes a timeout fallback; assert FallbackNoticeEvent is emitted in verbose/debug mode.

**Update required:** `test_route_task_fallback_silent_on_reset_timeout` currently asserts `fallback_reason==""` — this test encodes the broken behavior and MUST be updated when Fix 6 is applied.

### Fix 7: _task_direct_monitored and retry path
File: `tests/ai/test_pipeline.py`

16. **test_task_direct_monitored_timeout_fires_during_consumer_async_work** — Consumer does async work between iterations; timeout fires during that work; verify fallback/timeout response rather than silent drop.
17. **test_task_direct_retry_timeout_fires_during_consumer_async_work** — Same test for the retry path; verify the retry failure also yields a user-visible response.
18. **test_task_direct_monitored_aclose_called_on_timeout** — Timeout fires in primary loop; verify `gen.aclose()` is called in finally block.
19. **test_pipeline_router_gen_aclose_has_timeout** — Verify `pipeline.send()` wraps `router_gen.aclose()` in `asyncio.wait_for(..., timeout=_ACLOSE_TIMEOUT_S)` to prevent pipeline lock hang on unresponsive SDK.

### Integration
File: `tests/ai/test_pipeline_e2e.py` (new file)

20. **test_pipeline_full_failure_chain_no_silent_drop** — End-to-end: configure slow router session (thinking takes >timeout); run through a consumer with async work between iterations; verify the user receives EITHER a response OR an explicit fallback/error message — never silence.

---

## General Principle: Never Swallow Errors

All error-handling code in the pipeline must follow this invariant:

1. **Log to archon.log** — every caught exception, every timeout, every fallback
2. **Attempt recovery** — if there is a known recovery path, try it and log the attempt
3. **Inform the user** — if recovery succeeds, optionally notify; if it fails or is
   impossible, always inform the user with a clear message via Telegram
4. **Never silently continue** — `pass`, bare `except`, and `except Exception:` without
   user notification are forbidden on code paths that affect user-visible responses

The `fallback_reason=""` (silent fallback) pattern was introduced to avoid "alarming" users
with internal routing details. This is the wrong tradeoff. A user who sends a complex
request and receives nothing is far more alarmed than a user who receives
"⚙️ Router timed out — handling your request directly."
