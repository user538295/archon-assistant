# Bug Investigation #3: "Ping" Message Promoted to Background Agent After Timeout Recovery

**Date**: 2026-04-17  
**Affected Component**: Pipeline recovery logic (`archon/ai/pipeline.py`)  
**Severity**: High (incorrect routing; unnecessary background agent spawn; user confusion)

---

## Summary

After a timeout recovery event, the message "Ping" was unconditionally promoted to a background agent ("Sage") with 0 tool calls. This should never happen:

1. **"Ping" is trivial**: It's a simple chat message that should get an immediate "Pong" response
2. **Recovery ignores prior classification**: The timeout recovery path promotes to BAM without consulting the already-computed classification result
3. **All recoveries promote when BAM is enabled**: The code always promotes ANY timed-out message to background agents if `has_background_agents=True`, regardless of the message type

### Session Log Evidence

```
2026-04-17 21:03:14,577 archon INFO Classification: intent=chat confidence=0.98 duration=3.6s
2026-04-17 21:08:54,876 archon ERROR _task_direct_monitored timed out after 300s for prompt: Ping
2026-04-17 21:08:55,773 archon INFO Decomposer: recovering main session (stop + start)
2026-04-17 21:09:05,523 archon INFO Background agent 'Sage' spawned for user 154643621 (...)
2026-04-17 21:09:05,745 archon INFO Task promoted to agent 'Sage' (user=154643621, tools=0)
```

The message was correctly classified as **intent=chat confidence=0.98** at 21:03:14 (3.6s classification time). The timeout fired at 21:08:54 — 340 seconds later. The configured timeout is 300 seconds; the ~40s gap between classification completion and timeout start is likely routing/context injection overhead or lock wait time before `_task_direct_monitored()` was entered (requires separate investigation). Despite the correct prior classification, after recovery it was promoted to a background agent instead of being executed inline.

---

## Root Cause

**Location**: `archon/ai/pipeline.py:477–492`

The timeout recovery path in `_task_direct_monitored()` unconditionally promotes timed-out tasks to background agents when BAM is enabled:

```python
except TimeoutError:
    logger.error("_task_direct_monitored timed out after %.0fs for prompt: %.100s", ...)
    # ... (recovery steps 1-3) ...
    
    # Step 4: Promote or retry
    if self._has_bam:  # <-- BUG: always promotes if BAM is enabled
        yield RecoveryEvent(phase="promoting", message="Promoting task to background agent...")
        agent_prompt = _build_promotion_prompt(tool_pairs, prompt)
        yield PromotionEvent(
            agent_prompt=agent_prompt,
            original_prompt=prompt,
            tool_count=tool_count,  # <-- tool_count is 0 for "Ping"
        )
        self._decomposer.track_context(...)
        self._decomposer.flush_pending_context()
        # ... return without executing the message inline
    else:
        # Retry path for systems without BAM
        ...
```

### Why This Is Wrong

1. **Recovery ignores classification result**: The classification (`intent=chat, confidence=0.98`) was already computed before the message entered `_task_direct_monitored()`. The recovery path has access to rebuild this context but never checks it.

2. **Unconditional promotion**: The condition `if self._has_bam` is a boolean feature flag — it does not account for:
   - Message type/intent (chat vs. task)
   - Task complexity (trivial, small, or large)
   - Prior classification confidence
   - Actual tool call count

3. **Recovery is not a routing step**: Normal chat-message flow goes: Classification → (router skipped for chat) → `_task_direct_monitored()`. But the recovery path promotes to BAM regardless of whether the message would have gone through the router at all.

4. **Conflates two concepts**:
   - **Tool-count promotion** (lines 409–432): When a single message makes too many tool calls (≥ `tool_promotion_threshold`), promote it to background so the main session stays responsive. This is reasonable.
   - **Timeout recovery promotion** (lines 477–492): When a previous attempt timed out, promote the retry. This is questionable for trivial messages.

### Original Design Rationale

The timeout recovery → promotion path was designed for **long, complex tasks** that timed out after making some progress:

- It collects partial tool results (`tool_pairs`)
- It builds an enriched prompt with those partial results
- It promotes the task to a background agent with more time

This design makes sense for a task like "Analyze 100 files and generate a report" — if it times out, promote it to background so it can finish without blocking the user. The assumption was that any message reaching a 300-second timeout is inherently complex. This assumption breaks down for trivial messages in stuck sessions.

---

## Status of the Spawned "Sage" Agent

The log shows `Background agent 'Sage' spawned for user 154643621` at 21:09:05. The current status of this agent is unknown:

- If it is still running, it is consuming API tokens processing a "Ping" prompt in a full background agent session
- If it completed, the user received a "Pong"-equivalent response via the agent notification path rather than inline

**Action required**: Check `BackgroundAgentManager` for active runs for user 154643621. If the agent is still active, cancel it via `/cancel <run_id>` or `BackgroundAgentManager.stop_all()`. The fix described in this document prevents future Sage spawns for chat-classified messages; it does not retroactively cancel already-spawned agents.

---

## Open Question: Why Did "Ping" Hang for 300 Seconds?

**This is the more important question.** A `chat`-classified message with 98% confidence should produce a near-instant response. The document focuses on what happens after the timeout, but the root cause may be upstream:

- The decomposer session was stuck in a bad state from a previous interaction (most likely — the session is shared across messages)
- The SDK subprocess died silently with no heartbeat detection
- A network/API outage hit exactly at 21:03:14

**Why this matters for the fix**: All inline-retry options (2, 3, 5) assume `recover_session()` (line 465) successfully restores a functional session. If this assumption is false, the retry also hangs, and the worst-case wait before the user sees an error is approximately: 300s (original timeout) + 10s (`gen.aclose()`) + 30s (recovery) + 120s (`_RETRY_TIMEOUT_S`) + 10s (retry `gen.aclose()`) + 30s (post-retry recovery) ≈ **500 seconds**.

**Note on recovery mechanism**: The timeout recovery path calls `self._decomposer.recover_session()` (a regular async stop+start). This is a lighter operation than `_recover_session_in_clean_task()` used by the tool-count promotion path (which does `force_kill_for_recovery()` + restart in a separate asyncio task to avoid poisoned cancel scopes). If the hang was caused by a corrupted cancel scope or zombie subprocess, `recover_session()` may not fully clear it. This difference should be evaluated when investigating the root cause.

---

## How Recovery Bypasses Classification Context

**Current (pre-fix) behavior.** The classification result is available before `_task_direct_monitored()` is called, but it is not passed into the method and thus unavailable in the recovery path:

| Path | Classification | Routing | Recovery uses classification? |
|------|---|---|---|
| **Normal chat** | ✓ Yes (chat) | ✗ Skipped — chat goes directly to `_task_direct_monitored()` | N/A |
| **Normal task** | ✓ Yes (task) | ✓ Yes — `route_task()` → `_task_direct_monitored()` | N/A |
| **Normal large-scope** | ✓ Yes (task) | ✓ Yes — `route_task()` → `agent_plan` spawn | N/A |
| **Recovery after timeout** | ✗ Not re-run | ✗ Not re-run | ✗ **No — lost** |

**Note**: For `intent=chat` messages (including "Ping"), `Pipeline.send()` routes directly to `_task_direct_monitored()` at line 234 — the router (`route_task()`) is skipped entirely. The recovery path therefore bypasses both the router AND the classifier. The key fix is to make the classification intent available inside the recovery path, not to re-run the full classifier → router pipeline (which would change chat routing behavior unnecessarily).

The recovery logic is at the **bottom of the call stack** in `_task_direct_monitored()`. It cannot currently re-route the message; it can only decide whether to promote to BAM or retry inline.

---

## Options to Fix

### Option 1: Re-Route After Recovery

**Concept**: After session recovery succeeds, signal `Pipeline.send()` to re-dispatch the message through the full classification → routing pipeline.

**Pros**:
- Uses the same classification engine for all messages
- Respects message intent (chat vs. task)
- Will correctly identify trivial messages like "Ping"
- Consistent with the design principle that all messages flow through classification first

**Cons**:
- Requires significant refactoring: `_task_direct_monitored()` must signal `send()` to re-dispatch; `send()` needs a dispatch loop to handle re-routing
- The current `send()` flow has no loop — after `async for event in self._task_direct_monitored(...)` returns, there is no mechanism to re-dispatch
- The instance-flag approach (`self._recovery_requeue_prompt = prompt`) is not sufficient: `send()` holds `self._lock` during dispatch; re-entering `send()` would deadlock. The correct implementation requires a loop inside `send()` that re-runs the routing logic when a re-queue signal is detected
- Adds latency (re-classification after recovery, plus the 300s timeout)
- Chat messages currently bypass the router; re-routing would push them through the router for the first time, which changes behavior

**Implementation Sketch** (requires a dispatch loop in `send()`):
```python
# In Pipeline.send() — requires a loop, not just a flag
async def send(self, prompt):
    async with self._lock:
        requeue = True
        current_prompt = prompt
        while requeue:
            requeue = False
            async for event in self._dispatch(current_prompt):
                if isinstance(event, _RequeueSentinel):
                    requeue = True  # re-run the loop with updated prompt
                else:
                    yield event

# In _task_direct_monitored() except TimeoutError block:
yield RecoveryEvent(phase="session_recovered", ...)
yield _RequeueSentinel(prompt=prompt)  # signals send() to re-dispatch
return
```

Notes:
- `_RequeueSentinel` must be filtered in `send()` before yielding to callers.
- Infinite re-queue must be guarded (e.g., max retry count) — if re-dispatch also times out, the sentinel fires again → infinite loop.
- The loop re-dispatches while `self._lock` is still held; concurrent `send()` calls remain blocked during the full recovery + re-dispatch cycle.

---

### Option 2: Check Tool Count Before Promoting

**Concept**: Only promote to BAM if the timed-out message already made some tool calls (indicating complexity). For messages with 0 tool calls, retry inline instead.

**Pros**:
- Minimal change (add one condition)
- Fixes the immediate bug ("Ping" with 0 tools won't be promoted)
- No latency penalty
- Still supports the original intent (promote stuck long-running tasks that made progress)

**Cons**:
- `tool_count` is not a reliable proxy for complexity: a complex task that hangs during its thinking phase (before any tool call) also has `tool_count == 0`; it falls into the inline retry path and may timeout again
- Cascading timeout risk: if the retry path also hangs (session not fully recovered), the user waits 300s (original) + ~30s (recovery) + `_RETRY_TIMEOUT_S` (retry) before seeing an error
- Does not account for message intent (chat vs. task) — purely a heuristic
- If a future message type legitimately uses 0 tools, it will always retry inline regardless of complexity

**Implementation Sketch**:
```python
# 4. Promote or retry
if self._has_bam and tool_count > 0:  # NEW: Only promote if tools were called
    yield RecoveryEvent(phase="promoting", ...)
    # ... promotion logic ...
else:
    # Retry inline (like the no-BAM path)
    yield RecoveryEvent(phase="retrying", ...)
    retry_prompt = _build_retry_prompt(tool_pairs, prompt)
    # ... retry inline ...
```

---

### Option 3: Disable Recovery Promotion; Always Retry Inline

**Concept**: Remove the BAM promotion logic from the recovery path. After session recovery, always retry the message inline.

**Clarification**: Despite the "Pros" below, this option does NOT route through classification — it retries directly against the decomposer using `_build_retry_prompt()`, the same as the existing no-BAM branch (lines 493–528). "Normal classification after recovery" is not achieved by this option; that requires Option 1.

**Pros**:
- Simplest fix (remove the `if self._has_bam:` promotion block from the timeout handler)
- Avoids incorrectly promoting trivial messages
- Aligns with the principle that recovery is a reset, not a routing decision

**Cons**:
- For complex tasks that timed out with partial progress, retrying inline may timeout again — the promotion path was designed for exactly this case
- Complex tasks that do time out again fall to the second-timeout error path with no fallback
- Removes the only BAM fallback for legitimately stuck long-running tasks

**Implementation Sketch**:
```python
# 4. Recovery is a reset; always retry inline (same as no-BAM path)
yield RecoveryEvent(phase="retrying", message="Retrying inline after session recovery...")
retry_prompt = _build_retry_prompt(tool_pairs, prompt)
# ... retry inline ...
```

---

### Option 4: Re-Classify Before Promoting

**Concept**: Before promoting, re-run the classifier to check the message intent. Only promote if intent is "task"; for "chat" messages, retry inline.

**Pros**:
- Respects message intent
- Avoids promoting trivial chat messages
- Uses the existing classifier

**Cons**:
- Adds latency (re-classification is 3–5 seconds)
- Adds complexity: `_task_direct_monitored()` would need a classifier reference
- Does not address that routing decisions (task_direct vs. agent_plan) are also skipped

**Note**: The original classifier completed in 3.6 seconds and did NOT timeout. The 300-second timeout occurred in the decomposer, not the classifier. They use different SDK sessions. A re-classification timeout is unlikely but not impossible if the underlying API is degraded.

**Implementation Sketch**:
```python
except TimeoutError:
    # ... recovery steps 1-3 ...
    
    # Step 4: Re-check intent before deciding on promotion
    try:
        recheck_result = await self._classifier.classify(prompt)
        if recheck_result.classification.intent == "chat":
            yield RecoveryEvent(phase="retrying", ...)
            retry_prompt = _build_retry_prompt(tool_pairs, prompt)
            # ... retry inline ...
        else:
            if self._has_bam and tool_count > 0:
                # ... promote ...
            else:
                # ... retry ...
    except TimeoutError:
        logger.warning("Re-classification timed out; defaulting to inline retry")
        # ... retry inline ...
```

---

### Option 5: Pass Classification Intent Into Recovery (Recommended Immediate Fix)

**Concept**: The classification result (`intent=chat`) is already computed before `_task_direct_monitored()` is called. Pass it as a parameter and use it to gate promotion in the recovery path. Only promote if `intent != "chat"`.

**Pros**:
- Two-line change: add `intent` parameter to `_task_direct_monitored()` + add `intent != "chat"` condition in recovery
- No latency penalty (no re-classification)
- No architectural risk (no changes to `send()` dispatch loop or instance state)
- Semantically correct: uses the same classification that drove routing, not a heuristic
- Correctly handles "Ping" (`chat` intent → no promotion)
- Still promotes `task`-intent messages that time out after making progress

**Cons**:
- Doesn't handle the edge case where the classification itself was wrong (low-confidence chat that should have been a task); but this is the same situation as normal routing and is acceptable
- Does not fix the underlying question of why a chat message timed out at all

**Implementation Sketch** — both call sites must be updated:
```python
# In _task_direct_monitored() — intent is REQUIRED (no default, to force both callers to be explicit):
async def _task_direct_monitored(self, prompt: str, intent: str) -> ...:
    ...
    except TimeoutError:
        ...
        # Step 4: Promote or retry
        if self._has_bam and intent != "chat":  # NEW: never promote chat messages
            yield RecoveryEvent(phase="promoting", ...)
            # ... promotion logic (unchanged) ...
        else:
            # Retry inline
            ...

# Call site 1 — chat path (Pipeline.send(), line ~234):
async for event in self._task_direct_monitored(prompt, intent="chat"):
    yield event

# Call site 2 — task/router path (Pipeline.send(), line ~323):
# Note: variable is `resolved` (prompt after MCP injection), not `prompt`
async for event in self._task_direct_monitored(resolved, intent=intent):
    yield event
```

**Note on handler/voice changes**: `handler.py` and `voice.py` only receive `PromotionEvent` objects and do not need modification — Option 5 simply stops yielding `PromotionEvent` for chat-intent recoveries. No handler code changes required.

---

## Recommendation

**Immediate fix**: **Option 5 (Pass Classification Intent)**

- Two-line change with no architectural risk
- Semantically correct: uses the already-computed classification result
- Fixes the exact reported bug: `intent=chat` messages will never be promoted after timeout recovery
- Does not break promotion for `task`-intent messages that legitimately time out

**Optional extension (documented tradeoff, not recommended by default)**: Combine with Option 2 (`tool_count > 0`) to also gate task promotion: `self._has_bam and intent != "chat" and tool_count > 0`.

This prevents promoting task-intent messages that timed out before making a single tool call (likely stuck sessions). However, it is a **false-negative risk**: a genuinely complex task that hangs during its thinking phase (before any tool call) will fall into the inline retry path and may timeout again, giving the user an approximately 500-second wait before an error (300s original + 10s `gen.aclose()` + 30s recovery + 120s `_RETRY_TIMEOUT_S` + 10s retry `gen.aclose()` + 30s post-retry recovery). Adopt only if the operational trade-off (fewer unnecessary BAM promotions vs. degraded experience for stuck-thinking tasks) is explicitly accepted.

**Long term**: Refactor to **Option 1** (dispatch loop in `send()`) so recovery re-routes through the full classification → routing pipeline. This provides maximum correctness and consistency.

**Do not use Option 1 as an immediate fix** — the implementation requires a dispatch loop in `send()` that does not currently exist. The instance-flag approach sketched in earlier drafts would deadlock on `self._lock`.

---

## Verification

### Prerequisites

- `[background_agents]` section configured with BAM enabled (`spawn_rule` set, `host`/`port` configured)
- At least one background agent available

### Automated Test (Required Before Any Code Change — TDD)

**These are pseudocode sketches.** Before implementing, the following infrastructure must be created in `tests/ai/test_pipeline.py`:

- A `pipeline_with_bam` fixture: extend `_make_pipeline()` to accept `has_background_agents=True` and mock the `BackgroundAgentManager`
- A `patch_decomposer_timeout()` helper: make `decomposer.answer()` raise `TimeoutError` then succeed on retry
- A `patch_decomposer_timeout_after_tools(tool_count)` helper: yield N `ToolStarted` events then raise `TimeoutError`

```python
async def test_timeout_recovery_does_not_promote_chat_message(pipeline_with_bam):
    """After timeout recovery, a chat-classified message must not be promoted to BAM."""
    with patch_decomposer_timeout():
        events = [e async for e in pipeline_with_bam.send("Ping")]
    
    assert not any(isinstance(e, PromotionEvent) for e in events)
    assert any(isinstance(e, RecoveryEvent) for e in events)

async def test_timeout_recovery_still_promotes_task_with_tool_progress(pipeline_with_bam):
    """After timeout recovery, a task that made tool call progress should still be promoted."""
    with patch_decomposer_timeout_after_tools(tool_count=3):
        events = [e async for e in pipeline_with_bam.send("Analyze 100 files")]
    
    assert any(isinstance(e, PromotionEvent) for e in events)

## Only applicable if the optional "tool_count > 0" extension (Option 2 combination) is adopted:
async def test_timeout_recovery_task_zero_tools_retries_inline_with_option2_gate(pipeline_with_bam):
    """A task that times out before any tool call retries inline (not promoted) when
    the tool_count > 0 gate is in effect. Under pure Option 5, this message IS promoted
    because intent == 'task'."""
    with patch_decomposer_timeout_after_tools(tool_count=0):
        events = [e async for e in pipeline_with_bam.send("Analyze 100 files")]
    
    assert not any(isinstance(e, PromotionEvent) for e in events)
    assert any(isinstance(e, RecoveryEvent) and e.phase == "retrying" for e in events)

## Under pure Option 5 (no tool_count gate), a task that times out with 0 tool calls IS promoted:
async def test_timeout_recovery_task_zero_tools_promotes_under_option5_only(pipeline_with_bam):
    """Under pure Option 5, a task-intent message with 0 tool calls still gets promoted
    (intent != 'chat'). This is the correct behavior for Option 5 alone."""
    with patch_decomposer_timeout_after_tools(tool_count=0):
        events = [e async for e in pipeline_with_bam.send("Analyze 100 files")]
    
    assert any(isinstance(e, PromotionEvent) for e in events)
```

### Manual Verification Steps (Option 5)

1. Confirm `intent` is passed to `_task_direct_monitored()` in `Pipeline.send()` for the chat code path (line ~234)
2. In a session with BAM enabled, trigger a timeout on the "Ping" prompt (reduce `_TASK_DIRECT_TIMEOUT_S` in test or wait for real timeout)
3. Verify: **no** `PromotionEvent` in the event stream
4. Verify: **no** `"Task promoted to agent"` in logs
5. Verify: recovery events are present and inline retry completes

### Manual Verification Steps (Regression: Task Promotion Still Works)

1. Send a complex task (e.g., "Analyze 100 files and generate a report") that makes tool calls
2. Trigger a timeout after some tool calls are made
3. Verify: `PromotionEvent` IS yielded (task intent + tool progress → promotion preserved)

---

## Related Code References

- **Pipeline timeout recovery**: `archon/ai/pipeline.py:439–492` (the bug)
- **Tool-count promotion (different, works correctly)**: `archon/ai/pipeline.py:409–432`
- **Session recovery**: `archon/ai/pipeline.py:326–373` and `archon/ai/decomposer.py:199–209`
- **Normal promotion path**: `archon/ai/pipeline.py:315–324` (routing decides; lines 294–314 are RAG context injection)
- **Handler promotion dispatch**: `archon/chat/handler.py:335–372`
- **Voice handler promotion dispatch**: `archon/chat/voice.py:262` (same PromotionEvent handling — no changes needed for Option 5, listed for reference)
- **Config threshold**: `examples/config.toml.example:450` (`tool_promotion_threshold = 10`)
