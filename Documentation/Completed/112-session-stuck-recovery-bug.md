# Bug Investigation: Session Stuck After Error → Wrong Recovery Promotion

**Date**: 2026-04-17  
**Observed**: After JSON buffer overflow at 18:41 UTC (Bug 8), session became unresponsive for ~28 minutes wall clock (from crash at 18:41 to recovery at 19:09); the user's first follow-up message at 18:44 was ~19 minutes before "Ping" at 19:03. User's "Ping" at 19:03 was eventually promoted to background agent "Sage" with 0 tool calls. No response was delivered between 18:41 and 19:09.

---

## Timeline

| Time (UTC) | Event |
|---|---|
| 18:41:24 | JSON buffer overflow crash during route_task streaming (Bug 8) |
| 18:44:16 | User: "Don't stop until you finish my request!" |
| 18:44:19 | Classifier parse failure: confidence=0.0 (empty response) |
| 18:44:38 | Routing decision produced, decomposer started |
| 19:03:10 | User: "Ping" |
| 19:03:14 | Classifier: intent=chat, confidence=0.98 |
| 19:03:14–19:08:54 | Session hangs for ~300s processing "Ping" |
| 19:08:54 | Timeout fires: `_task_direct_monitored timed out after 300s` |
| 19:09:04 | Session recovered |
| 19:09:05 | "Ping" promoted to background agent "Sage" with tool_count=0 |
| 19:09:11 | Agent Sage: "Pong!" |

> **Open Question — 18:44–19:03 gap (~18 minutes)**: It is unknown whether the 18:44:38 decomposer call succeeded, hung, or was silently abandoned. If it was still running when "Ping" arrived at 19:03, the actual root cause may be resource contention (a lock held by a live but silent request) rather than session corruption — a fundamentally different failure mode requiring investigation before implementing Option A.

---

## Root Cause Hypothesis: Dangling SDK Generator After JSON Crash

> **Note**: This hypothesis assumes the 18:44 decomposer call completed or was abandoned before 19:03. If it was still holding the `_send_lock` when "Ping" arrived, the actual root cause is lock contention — not corruption — and the chain of failures below does not apply. The open question in the Timeline must be resolved before this hypothesis can be confirmed.

The JSON buffer overflow at 18:41:24 (Bug 8) crashed the route_task generator mid-stream. The SDK generator was in active use when the crash occurred — it was neither cleanly finalized nor garbage-collected. This may have left the decomposer's ClaudeSession in a **corrupted, partially-consumed state** with no clean shutdown.

### Chain of failures:

**1. Crash leaves session in corrupted state** (`archon/ai/decomposer.py`)  
The route_task() async generator was streaming when the JSON error hit. The generator closed abruptly, but the underlying SDK session object retained stale state. The `_send_lock` or equivalent internal state was not released cleanly.

**2. Next message ("Ping" at 19:03) starts with corrupted session**  
Pipeline.send() successfully acquires the lock and starts `_task_direct_monitored()`. The classifier correctly classifies "Ping" as chat (confidence=0.98). However, `decomposer.answer()` is called on the still-corrupted session, which may enter a deadlock — the SDK's internal generator neither streams nor completes.

**3. 300-second wall-clock timeout fires** (`archon/ai/pipeline.py` line ~58)  
`_TASK_DIRECT_TIMEOUT_S = 300.0`. The `asyncio.wait_for()` around `_safe_anext(gen)` counts down 300s before raising TimeoutError. This is why the user got no response for ~5 minutes. Note: the 300s timeout starts at the `_task_direct_monitored` level — before `_send_lock` acquisition inside `ClaudeSession.send()`. This means both lock-contention and session-corruption scenarios produce the same observable 300s timeout, which is why the two hypotheses are hard to distinguish from logs alone.

**4. Recovery logic unconditionally promotes** (`archon/ai/pipeline.py` lines ~477–492)  
After session recovery, the code checks `if self._has_bam:` — this is always True when BAM is configured. It promotes the message with `tool_count=0` regardless of message type or intent.

**Key log entries:**

> **Note**: Log timestamps are in UTC+2 (local time). Subtract 2 hours to convert to UTC, matching the timeline above.

```
21:03:14 - Classification: intent=chat confidence=0.98 duration=3.6s
21:08:54 - ERROR: _task_direct_monitored timed out after 300s for prompt: Ping
21:08:55 - INFO: Decomposer: recovering main session (stop + start)
21:09:05 - INFO: Background agent 'Sage' spawned for user 154643621 (tools=0)
21:09:05 - INFO: Task promoted to agent 'Sage' (user=154643621, tools=0)
```

---

## Secondary Effect: Classifier Also Failed (Separate Anomaly)

At 18:44:19 the classifier failed with confidence=0.0 (empty response). The log evidence is real:
```
DEBUG: TextBlock in AssistantMessage discarded: "You're right. I'll investigate..."
WARNING: Classification parse failed: no JSON object found in response
DEBUG: Dropped non-ThinkingResult classifier event: ToolStarted
DEBUG: Dropped non-ThinkingResult classifier event: ToolResult
```

The classifier (which should have `tools=[]`) received ToolStarted/ToolResult events. However, this **cannot** be explained by contamination from the decomposer crash: the classifier and decomposer have completely independent `ClaudeSession` instances backed by separate SDK subprocesses. A crash in the decomposer's `route_task()` cannot propagate into the classifier's session.

The classifier failure is a **separate, currently-unexplained anomaly**. Possible explanations include:

- (a) An SDK bug where the model invoked tools despite the `tools=[]` constraint
- (b) Log lines misattributed to the classifier due to a logging context leak
- (c) History accumulation in the classifier session causing the model to replicate prior tool-calling behavior

This requires separate investigation and should not be conflated with the decomposer session corruption described above.

---

## Options

### Option A: Immediate Session Kill on Serious Errors (Recommended for long-term)
In `Decomposer`'s internal implementation bodies of `answer()` and `route_task()` (not at Pipeline's call sites), wrap the implementation with exception handlers that detect "serious" errors (JSON buffer overflow, SDK connection reset, etc.) and immediately kill + restart the session synchronously before the next call.

```python
except Exception as exc:
    if _is_serious_error(exc):  # JSON overflow, connection reset, etc.
        logger.error("Serious error in decomposer, forcing session restart: %s", exc)
        # NOTE: Use force_kill_for_recovery() + restart, not stop()/start().
        # The gentle stop() may itself hang on a truly deadlocked session.
        # See the promotion path (pipeline.py) for the aggressive recovery pattern.
        force_kill_for_recovery()
        await self._session.start()
    raise
```
**Pros**: Prevents 5-minute hangs; next message gets a clean session; addresses root cause  
**Cons**: Adds synchronous kill on error path; `_is_serious_error()` requires identifying the exact exception type chain from the Bug 8 logs — if that chain is unclear, a simpler alternative is unconditional session recovery after ANY unhandled exception from `answer()` or `route_task()`, since session state after an arbitrary exception is unknowable anyway

> **Recovery path note**: Option A's implementation should prefer `force_kill_for_recovery()` over the gentle `stop()`/`start()` to avoid a secondary hang on a deadlocked session. The timeout-path recovery (triggered here after 300s) uses the gentler `stop()`/`start()` sequence, which may itself hang on a truly deadlocked session; the `_RECOVERY_TIMEOUT_S` guard prevents infinite hang but causes recovery failure, not a clean restart. This path should also be evaluated for replacement with `force_kill_for_recovery()`. The promotion path already uses aggressive `force_kill_for_recovery()` (SIGKILL).

> **Open Question**: Before implementing Option A, the 18:44–19:03 gap must be understood (see Timeline). If the decomposer was still holding a live request during that period, the 5-minute hang at 19:03 may be caused by lock contention rather than session corruption — which Option A would not address.

### Option B: Reduce Timeout + Deadlock Detection
Lower `_TASK_DIRECT_TIMEOUT_S` from 300s to ~30s. Add deadlock detection: if `_safe_anext()` yields nothing for 5 consecutive seconds, treat as deadlock.

**Pros**: Faster user-visible failure (30s vs 300s); works without session introspection  
**Cons**: May false-positive on legitimate slow operations; doesn't fix root corruption

> **Note**: Rather than a uniform global reduction, differentiated timeouts by classifier intent should also be considered — for example, `chat` intent with high confidence → 30–60s, `task` intent → full 300s. This avoids penalizing complex tasks while still recovering quickly from simple-message hangs.

### Option C: Don't Promote When tool_count == 0 ✅ Implemented (FIX-031)
In `pipeline.py` recovery path (~line 482), add guard: only promote to BAM if `tool_count > 0`. If no tools were called (message was deadlocked from the start), retry inline instead.

```python
if self._has_bam and tool_count > 0:
    # Promote — task made progress before timing out
    yield PromotionEvent(...)
else:
    # Retry inline — no progress was made; not a complex task
    yield RecoveryEvent(phase="retrying", ...)
    retry_gen = self._decomposer.answer(_build_retry_prompt(tool_pairs, prompt))
```
**Pros**: Prevents "Ping" being promoted to agent; fixes immediate symptom  
**Cons**: Doesn't fix 5-minute hang; session still corrupted after recovery; the retry here runs on the already-recovered session (recovery happens earlier in the pipeline, before this branch), so it will not deadlock on the old corrupted state. However, the recovery before this branch uses the gentle `recover_session()` path — if the session was truly deadlocked, this recovery may also fail (covered by `_RECOVERY_TIMEOUT_S`), in which case the retry never fires. Implementing Option A's `force_kill_for_recovery()` approach would make this retry path more reliable. The retry WILL start fresh with no conversation context (history from the hung request is lost). Additionally, the retry path inside the BAM branch either duplicates the retry logic from the existing non-BAM else-branch or requires extracting that logic into a shared helper — this is not a trivial change. When `tool_pairs` is empty (because `tool_count == 0`), `_build_retry_prompt` effectively produces a prompt identical or very similar to the original — there is no tool context to include. For a "Ping"-style message this is acceptable, since there is nothing meaningful to retry with additional context, but it is worth noting that the retry adds no new information in this case.

### Option D: Intent-based Promotion Guard ✅ Implemented (FIX-031)
In `pipeline.py` recovery path, add a guard that prevents promotion for `chat`-classified messages entirely, regardless of tool count.

```python
if self._has_bam and tool_count > 0 and classification.intent != "chat":
    yield PromotionEvent(...)
```

**Pros**: More semantically correct than `tool_count > 0` alone; a `chat` message should never become a background agent task regardless of how it was processed  
**Cons**: Requires changing `_task_direct_monitored`'s method signature to accept the `Classification` object, plus all call sites in Pipeline — this is a structural interface change, not just passing a parameter.

This complements Option C: Option C catches zero-tool-count cases; Option D catches cases where a `chat`-classified message happened to make some tool calls before timing out (e.g., if the classifier runs tools internally for context — unlikely but possible).

---

## Test Cases

The following tests are required before implementation (TDD mandatory):

- **Option C — zero tool calls**: `tool_count == 0` after timeout → retry is yielded, NOT `PromotionEvent`
- **Option C — non-zero tool calls**: `tool_count > 0` after timeout → `PromotionEvent` is still yielded
- **Option A (discriminated) — serious vs non-serious errors**: `_is_serious_error()` returns True for JSON buffer overflow and connection reset → session restart is triggered; a non-serious exception (e.g., `ValueError`) does not trigger restart
- **Option A (unconditional) — restart on any unhandled exception**: any unhandled exception from `answer()` triggers session restart before the next call proceeds
- **General — classifier fallback**: classifier failure (confidence=0.0) falls back to `task` intent without affecting the decomposer session

---

## Recommendation

**Immediate (Option C)**: Add `tool_count > 0` guard before BAM promotion in `pipeline.py`. This prevents the absurd case of promoting trivial messages with 0 tool calls. Paired with Bug 3's fix (same code location), one change covers both bugs.

**Follow-up (Option A)**: Add serious-error detection in `decomposer.py` to force session restart after JSON buffer overflow or connection reset. This prevents the 5-minute deadlock entirely. Before implementing, resolve the open question about the 18:44–19:03 gap — if the root cause is lock contention rather than session corruption, the recovery strategy differs and Option A alone will not prevent a repeat.

Together these address the full causal chain: crash → session corruption → 5min deadlock → wrong promotion.

**Files to modify:**
- `archon/ai/pipeline.py` ~line 482 — add `tool_count > 0` guard (immediate)
- `archon/ai/decomposer.py` — add serious-error session restart (follow-up)
