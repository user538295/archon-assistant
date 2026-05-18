# FEAT-018 — Improve /context messages when no active session
**Purpose**: Replace the generic "No active session" reply with context-aware messages that tell the user whether the context was cleared (background agent running or session saved) or simply never used.
**Audience**: Archon end users interacting via Telegram
**Status**: To Do

---

## Background

`/context` currently returns "ℹ️ No active session" whenever `SessionManager.has_session()` is False. This message is misleading in two real scenarios:

1. **Background agent running** — a task was promoted or spawned as a background agent. The inactivity timer may evict the main session while the agent runs. The context window was cleared intentionally, not abandoned.
2. **Session timed out** — the main session was evicted after the inactivity timeout (default 30 min). The window is gone but the user may not know why.

In both cases "No active session" implies nothing happened, confusing users who expect to see context info.

## Goal

When `/context` is invoked with no active session, return a message that accurately reflects the state: cleared for a background agent, cleared by timeout, or simply never started. Users should understand what happened and what they can do next.

---

## Scope

### In Scope
- Add `was_evicted(user_id) -> bool` to `SessionManager` via a private `_evicted_users: set[int]` tracking set
- Update `context_command` to accept `background_agent_manager: BackgroundAgentManager | None = None` and apply three-way logic for the no-session case
- Update all affected tests; no changes to gateway wiring (aiogram DI handles it automatically since `background_agent_manager` is already in `dp[...]`)

### Out of Scope
- Changes to the "session exists but no data yet" path (unchanged)
- Changing the session timeout value or eviction policy
- Any UI changes beyond the text of the `message.answer()` call

---

## Acceptance criteria
- [x] `/context` with no session and a running background agent replies with text containing "background agent"
- [x] `/context` with no session and a previous inactivity eviction replies with text containing "session saved"
- [x] `/context` with no session, no background agent, no prior eviction replies with "no context data" / "send a message first"
- [x] `/context` with an active session but no stats still replies with "no context data" / "send a message first" (unchanged)
- [x] `/context` with full stats still shows the progress bar and turn count (unchanged)
- [x] All existing tests pass after the parameter addition (no regressions)

---

## What does NOT change
- `_evict_after()` eviction logic and timing
- `context_stats()` return value contract
- `_fmt_context()` and the stats-display path
- Gateway wiring (`_setup_dp` already sets `dp["background_agent_manager"]`)
- `create_dispatcher()` in `bot.py` (no changes needed)

---

## Known limitations / accepted trade-offs
- `_evicted_users` is in-memory only; a daemon restart clears it, so after restart the "session saved" message cannot appear even if the previous session saved. Accepted: this is a UX hint, not critical state.
- `stop_all()` clears `_evicted_users`, so a full daemon shutdown resets the flag for all users. Accepted: consistent with in-memory-only design.
- `was_evicted()` returns True until a new session is created. Multiple `/context` calls after timeout will all say "cleared — session saved". Accepted: accurate and consistent.
- An explicit `/stop` clears the eviction flag (via `_evicted_users.discard(user_id)` at the start of `stop()`), so a deliberate stop does not produce "session saved" on the next `/context`.
- If `stop()` raises inside `_evict_after()`, the eviction flag is still set (via `finally`), but the session may remain partially alive in `_sessions`. A subsequent message will recreate the session normally. This is an accepted edge case — the flag is a UX hint only.

---

## Architecture

### New: `SessionManager._evicted_users: set[int]`
- Added in `__init__`: `self._evicted_users: set[int] = set()`
- Updated in `_evict_after()`: wrap `await self.stop(user_id)` in a try/finally block so the flag is set even if `stop()` raises:
  ```python
  try:
      await self.stop(user_id)
  finally:
      self._evicted_users.add(user_id)
  ```
- Updated in `stop()`: add `self._evicted_users.discard(user_id)` at the start of `stop()` (before calling `_teardown_session()`), so a manual explicit stop clears any stale eviction flag. Net effect for `_evict_after()`: discard runs first (no-op since not evicted yet), then `stop()` tears down, then `finally` adds — correct.
- Updated in `_create_session()`: at the start of the method (before creating the session), add `self._evicted_users.discard(user_id)`
- Updated in `stop_all()`: add `self._evicted_users.clear()` so a full daemon shutdown resets all flags
- New method: `def was_evicted(self, user_id: int) -> bool` — returns `user_id in self._evicted_users`

### Updated: `context_command` signature
```python
async def context_command(
    message: Message,
    session_manager: SessionManager,
    background_agent_manager: "BackgroundAgentManager | None" = None,
    notifications: "NotificationsConfig | None" = None,
) -> None:
```

`background_agent_manager` is optional with a default of `None`. When `None` (e.g. in direct test calls without DI), the agent-running check is skipped.

### Decision logic for no-session branch
```python
if not session_manager.has_session(user_id):
    if background_agent_manager is not None and background_agent_manager.list_running(user_id):
        await message.answer("🔄 Context window cleared — a background agent is running")
    elif session_manager.was_evicted(user_id):
        await message.answer("🔄 Context window cleared — session saved")
    else:
        await message.answer("📊 No context data yet — send a message first")
    return
```

### Dependency injection
`background_agent_manager` is already stored in `dp["background_agent_manager"]` by `_setup_dp()` in `gateway.py`. Aiogram 3.x injects it automatically by parameter name — no gateway changes needed. The parameter's `None` default also allows direct test calls like `context_command(msg, mgr)` without passing a third argument.

---

## Tests

- **test_context_no_session_running_agent_replies_agent_message** (unit): no session + running agent → reply contains "background agent"
- **test_context_no_session_evicted_replies_session_timed_out** (unit): no session + evicted + no running agent → reply contains "cleared" and "session saved"
- **test_context_no_session_never_used_replies_no_data** (unit): no session + not evicted + no running agent → reply contains "no context data" / "send a message"
- **test_context_no_session_replies_no_session** (unit — updated): existing test updated to pass `background_agent_manager` mock (no running agents, not evicted) and assert "no context data" instead of "no active session"
- **test_session_manager_was_evicted_false_before_eviction** (unit): `was_evicted()` returns False for a user with no eviction history
- **test_session_manager_was_evicted_true_after_eviction** (unit): after `_evict_after()` completes, `was_evicted()` returns True
- **test_session_manager_was_evicted_cleared_on_new_session** (unit): after `_create_session()`, `was_evicted()` returns False again

---

## Documentation update
- N/A — no user-facing docs or architecture docs need updating for this UX-only text change

---

## Task breakdown

### Phase 1 — SessionManager eviction tracking
> **Releasable**: after Task 1.1 — `was_evicted()` is callable and tested in isolation

#### Task 1.1 — Add `_evicted_users` tracking and `was_evicted()` to `SessionManager`
- [x] **File**: `archon/ai/session_manager.py`
- **Depends on**: nothing
- **Description**:
  - Add `self._evicted_users: set[int] = set()` to `__init__` after `self._locks`
  - In `stop()`: add `self._evicted_users.discard(user_id)` at the very start (before calling `_teardown_session()`), so an explicit `/stop` clears any prior eviction flag. This fires as a no-op in the `_evict_after()` flow (no flag set yet) and then the `finally` block re-adds it.
  - In `_evict_after()`: wrap the `await self.stop(user_id)` call in a try/finally block so the flag is set even if `stop()` raises:
    ```python
    try:
        await self.stop(user_id)
    finally:
        self._evicted_users.add(user_id)
    ```
  - In `_create_session()`: at the start of the method (before creating the session), add `self._evicted_users.discard(user_id)`. Note: discard happens before `session.start()` succeeds. If `start()` fails, the eviction history is lost and the user will see "no context data" rather than "session saved". This is acceptable — a session creation failure is a rarer, more pressing concern.
  - In `stop_all()`: add `self._evicted_users.clear()` so a full daemon shutdown resets all eviction flags
  - Add method: `def was_evicted(self, user_id: int) -> bool: return user_id in self._evicted_users`
  - No config keys or constants introduced
- **Releasable**: `was_evicted()` is importable and unit-testable
- **Tests (TDD)** — `tests/ai/test_session_manager.py`:
  - Unit: `test_was_evicted_returns_false_before_any_eviction` — fresh `SessionManager`, call `was_evicted(42)`, assert False
  - Unit: `test_was_evicted_returns_true_after_eviction` — mock `asyncio.sleep` to return immediately; call `_evict_after(42)` on a manager with a mock session; assert `was_evicted(42)` is True after it completes
  - Unit: `test_was_evicted_cleared_after_create_session` — set `_evicted_users = {42}` directly; call `_create_session(42)` (with mocked factory); assert `was_evicted(42)` is False
  - Unit: `test_was_evicted_unrelated_user_unaffected` — evict user 42; assert `was_evicted(99)` is still False
  - Unit: `test_was_evicted_cleared_by_stop_all` — set `_evicted_users = {42}` directly; call `stop_all()`; assert `was_evicted(42)` is False
  - Unit: `test_explicit_stop_clears_eviction_flag` — set `_evicted_users = {42}` directly; call `stop(42)` (with mocked session); assert `was_evicted(42)` is False
  - Unit: `test_eviction_deferred_does_not_set_was_evicted` — mock `is_processing=True` on the session; call `_evict_after(42)` (sleep mocked to return immediately); assert it returns without calling `stop()`, and `was_evicted(42)` is still False AND the session is still registered. This guards against a future refactor that accidentally wraps the entire `_evict_after()` body in try/finally (which would set the flag even on reschedule).
  - Unit: `test_was_evicted_set_even_when_stop_raises` — mock `stop()` to raise `RuntimeError`; call `_evict_after(42)` (sleep mocked to return immediately); catch the exception; assert `was_evicted(42)` is True. This is the direct test for the try/finally pattern's value.
  - Unit: `test_auto_compact_clears_eviction_flag` — set `_evicted_users = {42}` directly; trigger `auto_compact_if_needed(42)` with a mocked session and mocked factory; assert `was_evicted(42)` is False (verifies the `_create_session` discard fires through the auto-compact path, not just the explicit session-creation path).
  - Checkpoint: `uv run pytest tests/ai/test_session_manager.py -k "evict" -v`

---

### Phase 2 — context_command logic update
> **Releasable**: after Task 2.1 — `/context` returns correct messages for all no-session states

#### Task 2.1 — Update `context_command` with three-way no-session logic
- [x] **File**: `archon/chat/commands.py`
- **Depends on**: Task 1.1
- **Description**:
  - Add import: `from archon.ai.background_agent_manager import BackgroundAgentManager`
  - Change signature: `background_agent_manager: BackgroundAgentManager | None = None` as the third parameter (after `session_manager`, before `notifications`). The `None` default allows direct test calls like `context_command(msg, mgr)` without a third argument, and is safe for any test that does not supply DI.
  - Replace the existing `has_session` False branch with the three-way logic shown in the Architecture section above (guard with `background_agent_manager is not None` before calling `list_running()`).
  - Log line updated: `logger.info("/context for user %d: no session (agent=%s, evicted=%s)", ...)`
  - No other paths changed
  - `_mock_manager_with_context` test helper must be updated to add `mgr.was_evicted.return_value = False` as a default (because `SessionManager` spec now includes `was_evicted()`). Tests that need `was_evicted=True` must explicitly override: `mgr.was_evicted.return_value = True`.
- **Releasable**: `/context` returns correct message for all no-session states; all callers with aiogram DI work automatically
- **Tests (TDD)** — `tests/chat/test_commands.py`:
  - Unit: `test_context_no_session_running_agent_replies_agent_message` — `has_session=False`, `list_running` returns one agent, `was_evicted=False` → reply contains "background agent"
  - Unit: `test_context_no_session_evicted_replies_session_timed_out` — `has_session=False`, `list_running` returns `[]`, `was_evicted=True` → reply contains "cleared" (case-insensitive) and "session saved"
  - Unit: `test_context_no_session_never_used_replies_no_data` — `has_session=False`, `list_running` returns `[]`, `was_evicted=False` → reply contains "no context data" or "send a message"
  - Unit: `test_context_no_session_replies_no_session` (update existing) — update to pass mock `background_agent_manager` with `list_running=[]` and `was_evicted=False`; update assertion from "no active session" to "no context data" or "send a message"
  - Unit: `test_context_no_session_agent_running_and_evicted_prefers_agent_message` — `has_session=False`, `list_running` returns an agent, `was_evicted=True` → reply contains "background agent" (agent message takes priority over timed-out message)
  - Unit: `test_context_no_session_manager_none_skips_agent_check` — `background_agent_manager=None`, `has_session=False`, `was_evicted=False` → reply contains "no context data" (no AttributeError raised)
  - Unit: `test_context_no_session_bam_none_and_evicted_shows_timed_out` — `has_session=False`, `background_agent_manager=None`, `was_evicted=True` → reply contains "timed out" (agent check is skipped when bam is None, so eviction branch fires)
  - Unit: `test_context_session_no_data_yet_replies_accordingly` (existing — verify unchanged; no `background_agent_manager` arg needed since default is `None`)
  - Unit: `test_context_with_stats_replies_once` (existing — verify unchanged; no `background_agent_manager` arg needed since default is `None`)
  - Unit: `test_context_uses_user_id_from_message` (existing — verify unchanged; no `background_agent_manager` arg needed since default is `None`)
  - Unit: `test_context_with_stats_contains_progress_bar` (existing — update to add `mgr.was_evicted.return_value = False` via `_mock_manager_with_context` helper update; no explicit `background_agent_manager` arg needed)
  - Unit: `test_context_with_stats_contains_turns` (existing — same as above; no explicit arg needed)
  - Unit: `test_context_verbose_shows_sub_session_section` (existing — same as above; no explicit arg needed)
  - Unit: `test_context_normal_hides_sub_session_section` (existing — same as above; no explicit arg needed)
  - Unit: `test_context_command_without_notifications_hides_sub_sessions` (existing — same as above; no explicit arg needed)
  - Checkpoint: `uv run pytest tests/chat/test_commands.py -k "context" -v`
