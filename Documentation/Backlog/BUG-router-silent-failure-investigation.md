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

When `yield event` suspends `route_task()`, the asyncio task migrates to `handler.py`
(logging, Telegram send). In Python 3.12, `asyncio.timeout()` expires by calling
`task.cancel()` — on the **current task**, wherever it happens to be executing.

At the 60-second mark the task was in `handler.py` logging/sending the ThinkingResult.
The `CancelledError` is raised there, not inside `route_task()`. The
`asyncio.timeout()` context manager inside `route_task()` is never given a chance to
run its `__aexit__` and convert the error to `TimeoutError`.

**`handler.py:394`:**
```python
except Exception as exc:   # CancelledError inherits BaseException, not Exception → missed
```

Result: silent failure. No user message. No log entry. The fallback to direct handling
never executes. The pipeline lock is eventually released by Python's async-generator
finalizer (GC-driven, non-deterministic).

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

## Failure Chain

```
User: "Make a deep research about what films were made with AI assistant..."
  → Classifier: task, 0.95 (10.6s — Haiku used thinking, leaked reasoning into response)
  → Router session started; context injected (history + agents.md)
  → Router LLM: extended thinking ~60s → ThinkingResult yielded → logged to history ✓
  → asyncio.timeout(60s) fires while task is executing in handler.py
    → CancelledError raised in handler.py (at await history_manager.record_event OR
       await message.answer for the ThinkingResult)
    → except Exception: misses CancelledError (BaseException)
    → finally: runs (cancels beacon task)
    → CancelledError propagates to aiogram — silently discarded
  → route_task() fallback never executes
  → main session never called
  → Pipeline lock released asynchronously by GC finalizer
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

```python
_ROUTER_TIMEOUT_S: float = 180.0  # was 60.0 — Sonnet thinking alone can take 60-90s
```

Sonnet with extended thinking needs ~60-90s for thinking + ~10-20s for routing JSON +
buffer. 180s is the minimum safe value. Alternatively, explicitly disable extended
thinking for the router session (see Fix 5b) which would reduce routing latency to ~5s
and make 60s more than sufficient.

---

### Fix 3 (Critical): Handle `CancelledError` in `handler.py`

**File:** `archon/chat/handler.py`

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

---

## Fix Priority Matrix

| # | Bug | Severity | Fix complexity | User impact |
|---|-----|----------|----------------|-------------|
| 1 | `asyncio.timeout()` fires in wrong context | **Critical** | Medium | Eliminates silent total failures |
| 3 | `CancelledError` swallowed in handler.py | **Critical** | Low | User always gets a message |
| 2 | `_ROUTER_TIMEOUT_S = 60s` too short | **High** | Trivial | Router works for complex tasks |
| 4 | Classifier events discarded (debug invisible) | **High** | Low | Full debug observability |
| 5 | Haiku reasons about solving, not classifying | **High** | Low | Correct classifier behaviour |
| 6 | Silent router timeout fallback | Medium | Low | User understands what happened |

**Fixes 1 + 3 together** prevent the silent failure class entirely.
**Fix 2** addresses the specific trigger.
**Fixes 4 + 5** restore the observability and correctness contract for the Classifier.
**Fix 6** is UX polish.

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
