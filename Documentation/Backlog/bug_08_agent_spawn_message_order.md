# Bug 08 — Wrong message order when promoting task to background agent

Status: FIXED

## Description

When a task is promoted to a background agent (after 7 tools), the messages appear in wrong order:

```
> Archon: 🤖 Agent Jade spawned.
> Archon: 🔄 Task is bigger than expected — handing off to Agent Jade (7 tools used)
```

The "spawned" message comes FIRST, then the "handing off" message. The correct UX should be:
```
> Archon: 🔄 Task is bigger than expected — handing off to Agent Jade (7 tools used)
> Archon: 🤖 Agent Jade spawned.
```

## Root cause

In `archon/chat/handler.py` lines ~452-476:

```python
if isinstance(event, PromotionEvent) and background_agent_manager is not None:
    notification = "🔄 Task is bigger than expected — handing off to a background agent..."
    try:
        run = await background_agent_manager.spawn(...)  # ← spawn() calls _notify_spawn() internally!
        notification = f"🔄 Task is bigger than expected — handing off to Agent {run.name}..."
    ...
    try:
        await message.answer(notification)  # ← "handing off" sent AFTER spawn notification
```

`background_agent_manager.spawn()` internally calls `_notify_spawn()` which sends "🤖 Agent X spawned." to Telegram. This happens BEFORE `message.answer(notification)` which sends the "handing off" message.

## Fix

Send the "handing off to background agent" notification BEFORE calling `spawn()`. Since we don't know the agent name yet, use a generic message first, then optionally update it:

Option A (simple): Send generic "🔄 Task is bigger than expected — handing off to background agent (N tools used)" BEFORE spawn, then spawn sends "🤖 Agent X spawned."

Option B: Move spawn notification out of `_notify_spawn()` into handler.py after we know the name, but before sending the "handing off" message.

Option A is simpler (KISS).

## Tasks

1. Confirm root cause in handler.py and background_agent_manager.py
2. Implement Option A: send the "handing off" notification BEFORE calling spawn()
3. Update the notification message if spawn succeeds to include the agent name (or leave it generic)
4. Write a test that verifies message order
5. Fix

## AI Notes

### Fix applied (2026-03-11)

**Root cause confirmed**: `background_agent_manager.spawn()` calls `_notify_spawn()` internally at line 236 before returning. Handler.py was calling `spawn()` first, then sending the "handing off" notification — so "🤖 Agent X spawned." always arrived before "🔄 handing off".

**Fix**: Option A (KISS) in `archon/chat/handler.py` lines ~452-487:
- Send the generic "🔄 Task is bigger than expected — handing off to a background agent (N tools used)" message BEFORE calling `spawn()`.
- `spawn()` then sends "🤖 Agent X spawned." via `_notify_spawn()` — correct order.
- On spawn failure, a separate "⚠️ Task promotion failed" message is sent afterward.
- The post-spawn "handing off to Agent Name" update was removed (no longer needed — cleaner, 2 messages total on success).

**Tests updated** in `tests/ai/test_handler_promotion.py`:
- `test_promotion_event_sends_handing_off_message` — updated (agent name no longer in handing-off message with Option A)
- `test_promotion_event_handing_off_sent_before_spawn` — new test verifying call order
- `test_promotion_event_spawn_failure_sends_failure_notification` — updated assertion to match new 2-message failure flow

All 2297 tests pass.

## DA Review (2026-03-11)

### Verified

1. **Message order is correct.** In `archon/chat/handler.py` lines 452-486: the "handing off" message is sent via `message.answer()` at line 457, BEFORE `background_agent_manager.spawn()` is called at line 464. `spawn()` internally calls `_notify_spawn()` at line 236 of `background_agent_manager.py`, which sends the "spawned" message. Execution order is deterministic (single asyncio task, no concurrent awaits between the two sends).

2. **`_notify_spawn()` is still called inside `spawn()`.** Confirmed at `background_agent_manager.py` line 236: `await self._notify_spawn(run)` is the last thing before `return run`. The fix did NOT move or remove this call -- it only reordered the handler-side code to send the "handing off" message first.

3. **`continue` on line 486 skips `format_event` for PromotionEvent.** When BAM is available, the handler sends its own messages and skips the generic `format_event` path. When BAM is `None`, the event falls through to `format_event` which returns the "background agents unavailable" message (line 272). Both paths are correct.

4. **Test `test_promotion_event_handing_off_sent_before_spawn` correctly verifies order.** It uses `call_order` list with side_effect on both `message.answer` and `bam.spawn`, asserting `["answer", "spawn"]`. The side_effect on `message.answer` only records calls containing "handing off", filtering out the initial "Processing..." ack. This is sound.

5. **Spawn failure path is correct.** Lines 474-485: if `spawn()` raises, a separate failure notification is sent. The "handing off" message was already sent before the spawn attempt, so the user sees: (1) "handing off to background agent", (2) "Task promotion failed". This is acceptable UX.

6. **mypy check**: `handler.py` uses `parse_mode="HTML"` on the new `message.answer()` call at line 457. The handoff message `"... handing off to a background agent (N tools used)"` contains no HTML entities, so this is safe. No type issues expected.

### Issues Found

**[MINOR] Agent name lost from "handing off" message -- intentional UX trade-off.** Before the fix, the message said "handing off to Agent Jade". After the fix, it says "handing off to a background agent" (generic). The bug spec's "correct UX" example at lines 16-17 shows the agent name in both messages. The fix chose Option A (KISS) which sacrifices the name in the first message. This is documented in the fix notes ("post-spawn handing off to Agent Name update was removed") and is a reasonable trade-off: the user still sees the agent name in the immediately following "spawned" message. Not a regression -- a conscious simplification.

**[MINOR] `parse_mode="HTML"` on plain-text handoff message.** Line 457 passes `parse_mode="HTML"` for the message `"... handing off to a background agent (N tools used)"`. This message contains no HTML markup. If `event.tool_count` were ever to produce a value containing `<` or `>` (it is an `int`, so it cannot), it would break. This is harmless but inconsistent -- the failure notification at line 478 also uses `parse_mode="HTML"` for a plain-text message. Not a bug, but unnecessary.

### Conclusion

The fix is correct, minimal, and well-tested. The message ordering bug is resolved. The new test explicitly verifies the ordering invariant. The trade-off of losing the agent name from the "handing off" message is reasonable and documented.
