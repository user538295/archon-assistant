# 14 — Auto-Compaction on Context Pressure

**Purpose**: Automatically compact today's history and clear the session when context window usage exceeds a configurable threshold, preventing context overflow without manual `/clear`.
**Audience**: Archon developers
**Status**: Completed
**Priority**: P2 (quality-of-life improvement for long sessions)
**Estimated Effort**: M (~2 days)
**Depends on**: None (all building blocks exist)
**Last reviewed**: 2026-03-21
**Next review**: 2026-06-21

---

## Background

Long-running Archon sessions accumulate context until the SDK's context window fills up. Currently the user must manually run `/clear` to reset the session. This feature adds an automatic safety valve: when context usage exceeds a configurable percentage (e.g. 80%), Archon compacts today's history and clears the session — identical to what a manual `/clear` does, but preceded by a `compact_today()` call to preserve the conversation.

### Context budget math

- Each compacted day: ~3000 words ≈ ~4000 tokens
- Default `context_days = 2` → up to 3 summaries (2 past + today's partial) ≈ 12K tokens
- 12K / 200K context window = ~6%
- Even with `context_days = 7`: 8 × 4K = 32K ≈ 16%
- **Minimum safe threshold: 20%** — below this, the re-injected history could immediately re-trigger compaction

### Behaviour

1. After each response is fully delivered to Telegram, check context percentage
2. If `context_percentage >= auto_compact_threshold` and threshold is enabled (> 0):
   a. Log at `logger.info` level (with context percentage)
   b. Fire `compact_today()` as a background task (`asyncio.create_task`) — this runs asynchronously and updates the partial summary for the NEXT session clear
   c. Immediately stop and recreate the session (same as `/clear`) — these are fast operations
   d. Send Telegram notification (verbose/debug mode only) and record to session history
3. The fresh session re-injects compacted history via `get_recent_context()` as usual

**Note on async compaction**: The background `compact_today()` call will NOT be reflected in the immediately-recreated session — the fresh session gets whatever partial already exists from the last startup or midnight compaction run. This is an acceptable tradeoff: the user gets an instant session reset without blocking on an LLM call, and the partial is updated for the *next* compaction cycle. The alternative (blocking on `compact_today()` in the handler) would add seconds of latency to every auto-compaction.

### Design decisions

- **Check timing**: after delivery, not mid-response — no interruption risk
- **No cooldown needed**: compacted summaries are tiny (~6% of context even at `context_days=2`), so re-trigger is impossible with threshold ≥ 20%
- **Scope**: compact only today — `compact_pending_days()` already handles past days at startup and midnight
- **Notification visibility**: verbose/debug only — this is an internal optimisation, not user-facing in normal mode
- **Config validation**: reject thresholds between 1–19 at config load time
- **Async compaction**: `compact_today()` runs as fire-and-forget via `asyncio.create_task()` to avoid blocking the handler on an LLM call (Haiku SDK). The stop+recreate happens immediately — the fresh session gets the previous partial.
- **ContextProvider protocol**: NOT polluted — `compact_today()` is accessed via `isinstance()` check against the concrete `HistoryCompactor` type, not added to the read-only protocol
- **Concurrency**: `auto_compact_if_needed()` does NOT acquire the per-user lock (would cause deadlock with `get_or_create()`). Instead, it uses an `is_processing` guard: if the session is currently processing another message (`Pipeline.is_processing == True`), auto-compaction is skipped and retried on the next completed message. The method uses lock-free internal helpers (`_teardown_session`, `_create_session`) for the actual teardown/recreation. The background `compact_today()` call is fire-and-forget and does not interact with session locks.

---

## User Story

**As a** user running long Archon sessions,
**I want** the session to automatically compact and reset when the context window fills up,
**so that** I don't lose context or hit SDK errors due to context overflow.

---

## Acceptance Criteria

- [x] New `auto_compact_threshold` field in `[history]` config (integer percentage, default `0` = disabled)
- [x] Values 1–19 rejected at config load with `ConfigError`
- [x] After response delivery, if context % ≥ threshold: fire-and-forget compact_today + clear session
- [x] Session history log records the action with context percentage
- [x] Telegram notification sent in verbose/debug mode only
- [x] No notification in quiet/normal mode
- [x] Fresh session loads compacted history normally
- [x] Auto-compaction does not block the handler (compact_today runs as background task)
- [x] Voice handler path also triggers auto-compaction after delivery
- [x] All existing tests pass
- [x] ≥85% coverage on new code

---

## Tasks

### Task 1 — Config: add `auto_compact_threshold` to `HistoryConfig`

**Depends on**: none

**Description**:
Add `auto_compact_threshold: int = 0` to the `HistoryConfig` dataclass in `archon/config/loader.py`. Value semantics: `0` = disabled, `20–100` = percentage threshold, `1–19` = invalid (raise `ConfigError` during config loading).

**Files to modify**:
- [x] `archon/config/loader.py` — add field to `HistoryConfig` dataclass; add validation in the config loading path (where other `ConfigError` checks happen)
- [x] `examples/config.toml.example` — add commented `auto_compact_threshold` entry under `[history]` with explanation

**Tests**:
- [x] *Unit* `tests/config/test_config.py` — `test_auto_compact_threshold_defaults_to_zero`: absent field → `config.history.auto_compact_threshold == 0`
- [x] *Unit* `tests/config/test_config.py` — `test_auto_compact_threshold_loads_valid_value`: `auto_compact_threshold = 80` in TOML → parsed correctly
- [x] *Unit* `tests/config/test_config.py` — `test_auto_compact_threshold_rejects_below_20`: values 1–19 raise `ConfigError`
- [x] *Unit* `tests/config/test_config.py` — `test_auto_compact_threshold_accepts_boundary_values`: 0 (disabled) and 20 (minimum) both accepted

**Checkpoint**: `uv run pytest tests/config/test_config.py -v`

---

### Task 2 — Extract context window constant + add `context_percentage()` to `ClaudeSession`

**Depends on**: none (can run in parallel with Task 1)

**Description**:
The `_CONTEXT_WINDOW_TOKENS = 200_000` constant is currently defined in `archon/chat/commands.py` (line 234) and used only by `_fmt_context()`. Move it to `archon/ai/constants.py` (where `DEFAULT_MODEL`, `AVAILABLE_MODELS` etc. already live) so both `commands.py` and `ClaudeSession` can reference it.

Add a `context_percentage() -> int` method to `ClaudeSession` that returns the current context window usage as an integer percentage (0–100+). The calculation mirrors what `_fmt_context()` does in `commands.py`:

```python
stats = self.usage_stats
if stats is None:
    return 0
usage = stats.get("usage") or {}
input_t = usage.get("input_tokens") or 0
cumul_cc = stats.get("cumulative_cache_creation") or 0
return round(100 * (cumul_cc + input_t) / CONTEXT_WINDOW_TOKENS)
```

The data path is `usage_stats["usage"]["input_tokens"]` for the last turn's input and `usage_stats["cumulative_cache_creation"]` for cumulative cache writes. This matches the existing formula in `_fmt_context()`.

Since `Pipeline` duck-types as `ClaudeSession`, also add `context_percentage()` to `Pipeline` — delegating through the `Decomposer` to the internal main session. The delegation chain is: **Pipeline → `self._decomposer` → Decomposer's internal `self._session` (ClaudeSession)**. Pipeline has no `self._session` attribute — it accesses the session through Decomposer.

Add `context_percentage()` to `Decomposer` as well, delegating to `self._session.context_percentage()`.

**Important**: `Pipeline.context_percentage()` and `Decomposer.context_percentage()` MUST delegate to the inner `ClaudeSession` — they must NOT recompute from their own `usage_stats` property. `Pipeline.usage_stats` aggregates sub-session data (classifier costs, router costs) which would inflate the percentage and cause premature compaction. Only the main session's raw `cumulative_cache_creation` and `input_tokens` reflect the actual context window usage.

**Files to modify**:
- [x] `archon/ai/constants.py` — add `CONTEXT_WINDOW_TOKENS = 200_000`
- [x] `archon/chat/commands.py` — replace local `_CONTEXT_WINDOW_TOKENS` with import from `archon.ai.constants`
- [x] `archon/ai/claude_session.py` — add `context_percentage() -> int` method using `usage_stats` data and `CONTEXT_WINDOW_TOKENS`
- [x] `archon/ai/decomposer.py` — add `context_percentage() -> int` delegating to `self._session.context_percentage()`
- [x] `archon/ai/pipeline.py` — add `context_percentage() -> int` delegating to `self._decomposer.context_percentage()`

**Tests**:
- [x] *Unit* `tests/ai/test_claude_session.py` — `test_context_percentage_zero_when_no_usage`: fresh session → `context_percentage() == 0`
- [x] *Unit* `tests/ai/test_claude_session.py` — `test_context_percentage_calculates_correctly`: mock `_last_usage` and `_cumulative_cache_creation` with known values → verify percentage matches expected `round(100 * (cumul_cc + input_t) / 200_000)`
- [x] *Unit* `tests/ai/test_claude_session.py` — `test_context_percentage_can_exceed_100`: set values totalling > 200K → returns value > 100 (no clamping)
- [x] *Unit* `tests/ai/test_pipeline.py` — `test_pipeline_context_percentage_delegates`: mock `self._decomposer.context_percentage()` → verify Pipeline returns same value
- [x] *Unit* `tests/ai/test_decomposer.py` — `test_decomposer_context_percentage_delegates`: mock inner session → verify Decomposer returns same value
- [x] *Unit* `tests/chat/test_commands.py` — `test_fmt_context_uses_shared_constant`: existing `/context` tests still pass after constant extraction (regression)

**Checkpoint**: `uv run pytest tests/ai/test_claude_session.py tests/ai/test_pipeline.py tests/ai/test_decomposer.py tests/chat/test_commands.py -v`

---

### Task 3 — Auto-compaction orchestration in `SessionManager`

**Depends on**: Task 1, Task 2

**Description**:
Add an `auto_compact_if_needed(user_id: int) -> int | None` method to `SessionManager`. Return type semantics:
- `None` = not triggered (threshold disabled, below threshold, no session, or no compactor)
- `int` = the context percentage that triggered the compaction

This method:

1. Checks if `auto_compact_threshold` is enabled (> 0) in config
2. Gets the active session for `user_id` and calls `context_percentage()`
3. If percentage ≥ threshold:
   a. **Guards with `session.is_processing`** — if `True`, another message is currently streaming through this session (Pipeline lock is held by another caller). In this case, skip auto-compaction and return `None`. The next completed message will trigger the check again. This prevents tearing down a session while another message is mid-stream.
   b. Captures the percentage value (before the session is cleared)
   c. Fires `compact_today()` as a background task via `asyncio.create_task()` — does NOT block. Uses an `isinstance(self._history_compactor, HistoryCompactor)` check to access `compact_today()` (imported inside the method to avoid circular imports). If the compactor is only a `ContextProvider` (not `HistoryCompactor`), skips compaction but still clears the session.
   d. Tears down the session using a **lock-free internal helper** `_teardown_session(user_id)` that inlines the teardown logic from `stop()` WITHOUT popping the lock: cancel inactivity timer, pop `_started_at`, pop session from `_sessions`, await `session.stop()`. This avoids the deadlock that would occur if `auto_compact_if_needed()` acquired the per-user lock and then called `get_or_create()` (which also acquires it — `asyncio.Lock` is not reentrant).
   e. Creates a fresh session using a **lock-free internal helper** `_create_session(user_id)` that inlines the creation logic from `get_or_create()` without the lock acquisition: call factory, inject history context, store in `_sessions`, reset inactivity timer.
   f. Returns the captured percentage

   **Why no lock?** The `is_processing` guard (step 3a) is the concurrency protection. After `send()` completes, the Pipeline lock is released and there are `await` points in the handler before `auto_compact_if_needed()` runs. A concurrent message could acquire the Pipeline lock in that window. The `is_processing` check detects this and skips compaction — the next completed message will retry.

   **Refactoring note**: `stop()` should be refactored to call `_teardown_session(user_id)` followed by `self._locks.pop(user_id, None)`. This keeps the two code paths consistent — `_teardown_session` does everything except remove the lock, and `stop()` adds the lock removal on top. The difference is intentional: `auto_compact_if_needed()` preserves the lock so subsequent `get_or_create()` calls by other messages still serialize correctly.
4. Otherwise returns `None`

The method also logs the duration of the entire auto-compact operation (stop+recreate) at `logger.info` for operational visibility.

The background `compact_today()` task must:
- Log its own duration when complete
- Catch and log exceptions gracefully (never crash)
- Be held in `_background_tasks` set to prevent GC

The `auto_compact_threshold` value is passed from `HistoryConfig` through the `SessionManager` constructor (the gateway already passes other history-related values like `history_compactor`).

**Important**: Do NOT add `compact_today()` to the `ContextProvider` protocol. The protocol is read-only. Instead, use `isinstance()` to check if the compactor is a `HistoryCompactor` at runtime:

```python
from archon.ai.history_compactor import HistoryCompactor  # imported inside method

if isinstance(self._history_compactor, HistoryCompactor):
    task = asyncio.create_task(self._background_compact_today())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
```

**Files to modify**:
- [x] `archon/ai/session_manager.py` — add `auto_compact_threshold` constructor parameter; add `auto_compact_if_needed(user_id) -> int | None` method (lock-free); extract `_teardown_session(user_id)` and `_create_session(user_id)` lock-free helpers from existing `stop()` / `get_or_create()` logic; add `_background_compact_today()` helper; add `_background_tasks: set[asyncio.Task]` instance attribute
- [x] `archon/gateway/gateway.py` — pass `auto_compact_threshold=cfg.history.auto_compact_threshold` to `SessionManager` constructor

**Tests**:
- [x] *Unit* `tests/ai/test_session_manager.py` — `test_auto_compact_not_triggered_when_disabled`: threshold=0 → method returns `None`, no stop/create called
- [x] *Unit* `tests/ai/test_session_manager.py` — `test_auto_compact_not_triggered_below_threshold`: context at 50%, threshold at 80% → returns `None`
- [x] *Unit* `tests/ai/test_session_manager.py` — `test_auto_compact_triggered_at_threshold`: context at 80%, threshold at 80% → returns `80`; verify session teardown + recreation happened (old session's `stop()` called, new session created)
- [x] *Unit* `tests/ai/test_session_manager.py` — `test_auto_compact_triggered_above_threshold`: context at 95%, threshold at 80% → returns `95`
- [x] *Unit* `tests/ai/test_session_manager.py` — `test_auto_compact_skipped_when_processing`: session at 85% but `is_processing=True` → returns `None` (another message is streaming; session not torn down)
- [x] *Unit* `tests/ai/test_session_manager.py` — `test_auto_compact_no_active_session`: no session for user → returns `None` (no crash)
- [x] *Unit* `tests/ai/test_session_manager.py` — `test_auto_compact_without_compactor`: `history_compactor=None`, context at 85%, threshold at 80% → returns `85` (session still cleared); `compact_today()` NOT called (no compactor to call it on)
- [x] *Unit* `tests/ai/test_session_manager.py` — `test_auto_compact_compact_today_failure`: `compact_today()` raises → session is still stopped and recreated (graceful degradation); exception is logged
- [x] *Unit* `tests/ai/test_session_manager.py` — `test_auto_compact_returns_percentage`: verify the returned int matches the percentage from `context_percentage()`
- [x] *Unit* `tests/ai/test_session_manager.py` — `test_auto_compact_logs_duration`: verify `logger.info` is called with timing info
- [x] *Unit* `tests/ai/test_session_manager.py` — `test_auto_compact_second_call_returns_none`: call `auto_compact_if_needed()` twice in sequence — first returns `int`, second returns `None` (fresh session is below threshold)
- [x] *Integration* `tests/ai/test_session_manager.py` — `test_auto_compact_creates_fresh_session_with_history`: after auto-compact, new session has compacted history injected (mock compactor returns context)

**Checkpoint**: `uv run pytest tests/ai/test_session_manager.py -v`

---

### Task 4 — Handler integration: trigger auto-compaction after delivery

**Depends on**: Task 3

**Description**:
In `archon/chat/handler.py`, after the `async for event in session.send(text)` loop completes (and all events are delivered to Telegram), call `session_manager.auto_compact_if_needed(user_id)`. The method returns `int | None`:

- `None` → no action needed
- `int` → the context percentage that triggered compaction

If triggered (`int` returned):

1. Write a log entry to session history via `history_manager.record_archon_message()`:
   `⚙️ Auto-compaction triggered (context: {pct}% of 200K)`
2. Send a Telegram notification to the user — but only if notification mode is `verbose` or `debug`
3. Log at `logger.info` level for daemon logs

The check must happen inside the `try` block but after the event streaming loop, before the `finally` block. If auto-compaction itself fails (e.g. compactor error), catch and log the exception — do not crash the handler.

**Voice handler coverage**: `archon/chat/voice.py` has its own event loop in `_process_and_respond()` — it does NOT delegate to `handle_message`. Extract the auto-compaction check into a shared utility function that both `handler.py` and `voice.py` call:

```python
async def check_auto_compact(
    session_manager: SessionManager,
    user_id: int,
    message: Message,
    history_manager: "HistoryManager | None",
    notifications: "NotificationsConfig | None",
) -> None:
    """Check and trigger auto-compaction after response delivery. Fire-and-forget safe."""
    try:
        pct = await session_manager.auto_compact_if_needed(user_id)
        if pct is not None:
            note = f"⚙️ Auto-compaction triggered (context: {pct}% of 200K)"
            logger.info(note)
            if history_manager is not None:
                await history_manager.record_archon_message(note)
            mode = notifications.mode if notifications else "debug"
            if mode in ("verbose", "debug"):
                await message.answer(note)
    except Exception:
        logger.error("Auto-compaction check failed for user %d", user_id, exc_info=True)
```

Place this in `handler.py` and import it in `voice.py`. Call it at the end of the event streaming loop in both handlers.

**Files to modify**:
- [x] `archon/chat/handler.py` — add `check_auto_compact()` utility function; call it after the event streaming loop in `handle_message()`
- [x] `archon/chat/voice.py` — import and call `check_auto_compact()` after the event streaming loop in `_process_and_respond()`

**Tests**:
- [x] *Unit* `tests/chat/test_handler.py` — `test_auto_compact_called_after_delivery`: mock `auto_compact_if_needed` → verify called once with correct `user_id` after event loop completes
- [x] *Unit* `tests/chat/test_handler.py` — `test_auto_compact_notification_verbose`: auto-compact returns `85` + mode=verbose → Telegram message sent with context percentage
- [x] *Unit* `tests/chat/test_handler.py` — `test_auto_compact_notification_debug`: auto-compact returns `85` + mode=debug → Telegram message sent
- [x] *Unit* `tests/chat/test_handler.py` — `test_auto_compact_no_notification_normal`: auto-compact returns `85` + mode=normal → no Telegram message (history still logged)
- [x] *Unit* `tests/chat/test_handler.py` — `test_auto_compact_no_notification_quiet`: auto-compact returns `85` + mode=quiet → no Telegram message
- [x] *Unit* `tests/chat/test_handler.py` — `test_auto_compact_history_logged`: auto-compact returns `85` → `record_archon_message` called with percentage string
- [x] *Unit* `tests/chat/test_handler.py` — `test_auto_compact_none_no_side_effects`: auto-compact returns `None` → no notification, no history log entry
- [x] *Unit* `tests/chat/test_handler.py` — `test_auto_compact_error_handled_gracefully`: `auto_compact_if_needed` raises → exception logged, handler does not crash
- [x] *Unit* `tests/chat/test_voice.py` — `test_voice_auto_compact_called_after_delivery`: verify `check_auto_compact` is called after voice event loop completes
- [x] *Unit* `tests/chat/test_voice.py` — `test_voice_auto_compact_error_handled`: `check_auto_compact` failure does not crash voice handler
- [x] *Integration* `tests/chat/test_handler.py` — `test_auto_compact_end_to_end`: full handler flow with mock session at 85% context + threshold 80% → verify compact + clear + notification sequence

**Checkpoint**: `uv run pytest tests/chat/test_handler.py tests/chat/test_voice.py -v`

---

### Task 5 — Documentation

**Depends on**: Task 4

**Description**:
Update all relevant documentation to reflect the new feature.

**Files to modify**:
- [x] `CLAUDE.md` — add `auto_compact_threshold` to the `[history]` config field list in the Configuration section
- [x] `Documentation/Architecture/110_component_catalog_and_layer_breakdown.md` — mention auto-compaction in the `SessionManager` component description
- [x] `Documentation/UserManual/user_manual.md` — add a section on auto-compaction under history/compaction docs (what it does, how to configure, what the notification looks like)
- [x] `README.md` — add `auto_compact_threshold` to the config reference if one exists
- [x] Move this document to `Documentation/Completed/` and update status

**Tests**:
- [x] *E2E* (manual): set `auto_compact_threshold = 80` in `config.toml`, run a long session that fills context past 80%, verify auto-compaction fires, session is cleared, and notification appears in verbose mode
- [x] *Live E2E*: send enough messages to fill context past threshold → verify Telegram receives the compaction notification (verbose), session is cleared, next message works on a fresh session with compacted history

**Checkpoint**: `uv run pytest` (full suite green) + `uv run mypy archon/`

---

## Dependency graph

```
Task 1 (config)──────┐
                      ├── Task 3 (SessionManager) ── Task 4 (handler+voice) ── Task 5 (docs)
Task 2 (constant)────┘
```

Tasks 1 and 2 can be implemented in parallel. Task 3 depends on both. Task 4 depends on Task 3. Task 5 is last.

---

## Summary

| Task | Key change | Files |
|---|---|---|
| **1** | `auto_compact_threshold` config field + validation | `loader.py`, `config.toml.example` |
| **2** | Shared `CONTEXT_WINDOW_TOKENS` constant + `context_percentage()` method | `constants.py`, `commands.py`, `claude_session.py`, `decomposer.py`, `pipeline.py` |
| **3** | `auto_compact_if_needed()` orchestration in SessionManager (lock-free, `is_processing` guarded) | `session_manager.py`, `gateway.py` |
| **4** | Handler + voice integration — trigger + notification + history log | `handler.py`, `voice.py` |
| **5** | Documentation updates | `CLAUDE.md`, Architecture, UserManual, README |
