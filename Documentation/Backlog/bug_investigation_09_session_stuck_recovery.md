# Bug Investigation: Session Stuck After Error → Wrong Recovery Promotion

**Date**: 2026-04-17  
**Observed**: After JSON buffer overflow at 18:41 UTC (Bug 8), session became unresponsive for ~19 minutes. User's "Ping" at 19:03 was eventually promoted to background agent "Sage" with 0 tool calls. No response was delivered between 18:41 and 19:09.

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

---

## Root Cause: Dangling SDK Generator After JSON Crash

The JSON buffer overflow at 18:41:24 (Bug 8) crashed the route_task generator mid-stream. The SDK generator was in active use when the crash occurred — it was neither cleanly finalized nor garbage-collected. This left the decomposer's ClaudeSession in a **corrupted, partially-consumed state** with no clean shutdown.

### Chain of failures:

**1. Crash leaves session in corrupted state** (`archon/ai/decomposer.py`)  
The route_task() async generator was streaming when the JSON error hit. The generator closed abruptly, but the underlying SDK session object retained stale state. The `_send_lock` or equivalent internal state was not released cleanly.

**2. Next message ("Ping" at 19:03) starts with corrupted session**  
Pipeline.send() successfully acquires the lock and starts `_task_direct_monitored()`. The classifier correctly classifies "Ping" as chat (confidence=0.98). However, `decomposer.answer()` is called on the still-corrupted session, which enters a deadlock — the SDK's internal generator neither streams nor completes.

**3. 300-second wall-clock timeout fires** (`archon/ai/pipeline.py` line ~58)  
`_TASK_DIRECT_TIMEOUT_S = 300.0`. The `asyncio.wait_for()` around `_safe_anext(gen)` counts down 300s before raising TimeoutError. This is why the user got no response for ~5 minutes.

**4. Recovery logic unconditionally promotes** (`archon/ai/pipeline.py` lines ~477–492)  
After session recovery, the code checks `if self._has_bam:` — this is always True when BAM is configured. It promotes the message with `tool_count=0` regardless of message type or intent.

**Key log entries:**
```
21:03:14 - Classification: intent=chat confidence=0.98 duration=3.6s
21:08:54 - ERROR: _task_direct_monitored timed out after 300s for prompt: Ping
21:08:55 - INFO: Decomposer: recovering main session (stop + start)
21:09:05 - INFO: Background agent 'Sage' spawned for user 154643621 (tools=0)
21:09:05 - INFO: Task promoted to agent 'Sage' (user=154643621, tools=0)
```

---

## Secondary Effect: Classifier Also Failed (Bug 1 Cascade)

At 18:44:19 the classifier failed with confidence=0.0 (empty response). This happened because the classifier's ClaudeSession was in a contaminated state after the JSON crash. Log shows:
```
DEBUG: TextBlock in AssistantMessage discarded: "You're right. I'll investigate..."
WARNING: Classification parse failed: no JSON object found in response
DEBUG: Dropped non-ThinkingResult classifier event: ToolStarted
DEBUG: Dropped non-ThinkingResult classifier event: ToolResult
```
The classifier (which should have `tools=[]`) received ToolStarted/ToolResult events — evidence of session contamination from previous state bleeding into the new call.

---

## Options

### Option A: Immediate Session Kill on Serious Errors (Recommended for long-term)
In `decomposer.py`, wrap `answer()` and `route_task()` with exception handlers that detect "serious" errors (JSON buffer overflow, SDK connection reset, etc.) and immediately kill + restart the session synchronously before the next call.

```python
except Exception as exc:
    if _is_serious_error(exc):  # JSON overflow, connection reset, etc.
        logger.error("Serious error in decomposer, forcing session restart: %s", exc)
        await self._session.stop()
        await self._session.start()
    raise
```
**Pros**: Prevents 5-minute hangs; next message gets a clean session; addresses root cause  
**Cons**: Adds synchronous kill on error path; must identify which errors are "serious" vs transient

### Option B: Reduce Timeout + Deadlock Detection
Lower `_TASK_DIRECT_TIMEOUT_S` from 300s to ~30s. Add deadlock detection: if `_safe_anext()` yields nothing for 5 consecutive seconds, treat as deadlock.

**Pros**: Faster user-visible failure (30s vs 300s); works without session introspection  
**Cons**: May false-positive on legitimate slow operations; doesn't fix root corruption

### Option C: Don't Promote When tool_count == 0 (Recommended for immediate fix)
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
**Pros**: 3-line fix; prevents "Ping" being promoted to agent; fixes immediate symptom  
**Cons**: Doesn't fix 5-minute hang; session still corrupted; retry may also deadlock

---

## Recommendation

**Immediate (Option C)**: Add `tool_count > 0` guard before BAM promotion in `pipeline.py`. This prevents the absurd case of promoting trivial messages with 0 tool calls. Paired with Bug 3's fix (same code location), one change covers both bugs.

**Follow-up (Option A)**: Add serious-error detection in `decomposer.py` to force session restart after JSON buffer overflow or connection reset. This prevents the 5-minute deadlock entirely.

Together these address the full causal chain: crash → session corruption → 5min deadlock → wrong promotion.

**Files to modify:**
- `archon/ai/pipeline.py` ~line 482 — add `tool_count > 0` guard (immediate)
- `archon/ai/decomposer.py` — add serious-error session restart (follow-up)
