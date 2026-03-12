# Bug 20 — Message Shifting / Response Misalignment

Status: OPEN

## Description

Responses to user message N appear as the Telegram reply to user message N+1, not to the message that triggered them. The actual message N+1 may be partially or fully ignored/swallowed. This causes cascading misalignment across a session.

## Observed in log: 2026-03-12

The user explicitly reported this at 18:59:19 UTC:
> "The messages shifted again. That was my previous message. Zephyr had to save it, but it didn't do it and stopped. Then after my next message you saved it and doing it as a direct task but didn't response the actual message."

Further evidence in the same session:
- At 19:00:51 UTC: `[Agent Echo]` bin-reminder fix response labelled `> User: "Ping"` — wrong message
- At 19:05:11 UTC: `[Agent Umbra]` changes summary labelled `> User: "Ping"` — wrong message
- At 19:05:16 UTC: `[Agent Onyx]` bin-reminder diagnosis labelled `> User: "Ping"` — wrong message
- At 19:06:12 UTC: Plan completion `[System] Plan completed: 2/2 agents succeeded` attached to `> User: "There is another bug..."` — wrong message
- At 19:06:56 UTC: Response to beacon-double-send report was `Pong.` — completely wrong content

## Root Causes (identified in agent investigation 18:59:54 UTC)

### Root Cause 1 — Double Delivery (`background_agent_manager.py`)

When a background agent completes, the result is:
1. Delivered to the user via Telegram (correct)
2. **Also injected** into `_session._pending_context` as a 500-character result preview

On the next user message, `ClaudeSession.send()` prepends this stale context to the prompt. Claude then echoes/re-processes the already-delivered agent result as if it was addressing the new message. The actual new message gets overshadowed.

### Root Cause 2 — Stale Context Accumulation (`pipeline.py`)

When a user message is routed through `route_task()`, it uses `_orch_session`. The main `_session._pending_context` is never consumed by this code path. Stale agent results accumulate and later leak into the next message routed through `answer()` — even if it's an unrelated "Ping".

### Manifestation — "Ping" Promoted to Background Agent

At 19:00:14 UTC, a "Ping" message (classified as `chat`, 0.98 confidence) began making file-system tool calls for the bin-reminder investigation — tool calls that belonged to agent a1. After 7 tool calls, the Ping handler was promoted to a background agent with the wrong task context. This shows the stale context fully hijacked the inline message handler.

## Proposed Fixes

1. **background_agent_manager.py**: Replace the 500-character result preview in `_pending_context` with a status-only note (e.g., "Agent X completed. Result already delivered."). No result content should be injected.

2. **pipeline.py**: Flush `_pending_context` before calling `route_task()`. Stale context must not survive across routing path changes.

## Symptoms

- User sees responses misaligned by 1 (or more) messages
- Simple chat messages trigger irrelevant tool calls
- Agent completion responses appear for wrong user messages
- Plan completion events attached to wrong user messages
- Beacon or system messages appear as response to unrelated messages

## Tasks

1. Read `archon/ai/background_agent_manager.py` — find agent completion context injection
2. Read `archon/ai/pipeline.py` — find `route_task()` call and `_pending_context` flush
3. Read `archon/ai/claude_session.py` — find `send()` method and `_pending_context` prepend
4. Write failing e2e tests confirming the misalignment
5. Fix Root Cause 1: inject status-only note instead of result preview
6. Fix Root Cause 2: flush `_pending_context` before `route_task()`
7. Run full test suite
