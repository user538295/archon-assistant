# FEAT-027-P5 — Telegram Notification on Indexing Completion
**Purpose**: Notify the user in Telegram when background RAG indexing finishes or fails — no terminal polling needed
**Audience**: Users with RAG enabled who trigger indexing via install or update and monitor progress from Telegram
**Status**: To Do

---

## Background
Phases 1–4 (Done) added progress visibility, pinned-first ordering, resumable indexing, and file-level change detection. The user can check status via `archon rag status` or the `rag_status` MCP tool, but must actively poll. Phase 5 adds a push notification so the user hears about completion without asking.

Full feature spec: `Documentation/Backlog/FEAT-027-rag-background-indexing-progress.md`, Phase 5 section.

## Goal
When an install-triggered background sync reaches a terminal state (all collections `done` or `failed`), the daemon sends a single Telegram summary message to all whitelisted users — respecting the current notification mode. (`archon update` also triggers this path via the same `server.py` startup sync — see Known limitations.) Manual syncs (`archon rag sync`, `rag_sync` MCP tool) are explicitly silent. Once notified, the trigger is cleared so the notification fires only once.

---

## Scope

### In Scope
- Add `trigger: str | None` field to `IndexingState` (values: `"install"` | `"manual"` | `null`; `"update"` reserved for a future phase)
- `to_dict` / `from_dict` serialisation of the new field
- `IndexingStateStore.set_trigger(trigger: str | None)` — convenience write: read-modify-write to set only the trigger field
- `server.py` startup sync — write `trigger="install"` into state before kicking off sync
- `_handle_rag_sync` in `archon_toolkit_rag.py` — write `trigger="manual"` before sync (suppresses notification)
- New `archon/rag/notification_monitor.py` — `IndexingNotificationMonitor`: asyncio background task that polls state every 30s, detects all-terminal transition, sends notification, clears trigger
- Gateway wires up the monitor when RAG is enabled: creates task after bot is live, cancels on shutdown
- Three notification message formats (success / partial failure / total failure)
- Notification respects `notifications.mode`: suppressed in `quiet`, sent in `normal`/`verbose`/`debug`

### Out of Scope
- Per-collection notifications — one summary message only
- Retry on Telegram delivery failure — silently skipped, same pattern as other gateway notifications
- Progress notifications mid-index ("50% done")
- Notification in `install.py` — install is a separate process; `install.py` sets the trigger, the running daemon notifies
- `archon update` trigger — `archon update` restarts the daemon, which starts a new sync; the `server.py` startup path already handles it via `trigger="install"` (same trigger for both)
- Watch-mode-triggered sync notification — Phase 8

---

## Acceptance criteria
- [x] `IndexingState` has `trigger: str | None` field (default `None`)
- [x] `to_dict` serialises `trigger` as a top-level key; `from_dict` reads it safely (invalid type → `None`)
- [x] `IndexingStateStore.set_trigger(trigger: str | None)` reads current state, sets trigger, writes atomically
- [ ] `server.py` startup sync writes `trigger="install"` to state file before starting sync
- [ ] `_handle_rag_sync` writes `trigger="manual"` before calling `sync.sync()`
- [ ] `IndexingNotificationMonitor` polls every 30 seconds
- [ ] Monitor only fires notification when `trigger` is `"install"`; silently skips `"manual"` and `None` (`"update"` reserved for future phase — check is `trigger in ("install", "update")` to be forward-compatible)
- [ ] Monitor fires only when **all** collections have reached a terminal state (`done` or `failed`)
- [ ] Monitor clears `trigger` (sets to `None`) **before** sending the notification — prevents double-send if a subsequent poll fires before delivery completes
- [ ] All-success: `"✅ RAG indexing complete — all N collection(s) ready."`
- [ ] Partial failure: `"⚠️ RAG indexing finished — N collection(s) failed. Run <code>archon rag status</code> for details."`
- [ ] Total failure: `"❌ RAG indexing failed — no collections are ready. Run <code>archon rag status</code> for details."`
- [ ] Notification is suppressed when `notifications.mode == "quiet"`
- [ ] Notification sent to all `cfg.access.allowed_user_ids`
- [ ] Monitor task is started in gateway when RAG is enabled; cancelled gracefully on shutdown
- [ ] If state file is absent or has no collections, monitor does nothing
- [ ] If bot is not yet connected when notification fires, exception is caught and logged (not raised)
- [ ] If `set_trigger(None)` raises (e.g., disk full), the exception is caught and logged at WARNING level; `_send_to_all` is NOT called for this poll cycle; the trigger remains set so the next poll will retry (infinite retry until disk error resolves)

---

## What does NOT change
- `IndexingStateStore.read()`, `write()`, `update_collection()`, `remove_collection()` — all existing methods unchanged
- `RagCollectionSync.sync()` — no changes; trigger is set by the caller (server.py / toolkit)
- `CollectionProgress` dataclass — no new fields
- `archon rag status` CLI output — unchanged
- `rag_status` MCP tool response — unchanged (trigger is internal plumbing)
- Existing gateway shutdown logic — monitor cancel is additive

---

## Known limitations / accepted trade-offs
- `archon update` restarts the daemon which triggers `server.py` startup, setting `trigger="install"`. A distinct `"update"` value is reserved in the trigger check (`trigger in ("install", "update")`) for future differentiation, but is not written in this phase.
- State is read from disk every 30s; no inotify/FSEvents watch. 30s lag is acceptable for a completion notification.
- If the daemon restarts while a sync is in progress (unlikely for update, possible for crash), the monitor will pick up and notify on the next poll after the new sync completes.
- Single summary notification: if one collection is `done` and another is still `in_progress`, no notification is sent until all reach terminal state.
- Trigger is cleared **before** `_send_to_all` is called. If all Telegram deliveries fail after the clear, the notification is permanently lost (trigger is `None`; no retry). If `set_trigger` itself fails (e.g., disk full), the trigger remains set and the monitor retries every 30s until the disk error resolves — effectively an infinite retry loop. Both failure modes are accepted; per-user delivery failures are already logged.
- If the user triggers a manual `rag_sync` while a startup sync is in progress, `set_trigger("manual")` overwrites `trigger="install"`. When the startup sync completes, the monitor sees `"manual"` and suppresses the notification. This is an accepted race condition; manual sync is user-initiated and the user can check status directly.
- The monitor is only created at gateway startup if `rag_state == RagState.RUNNING`. If RAG is started later in the session (e.g., via `rag_start` MCP tool), no monitor is created for that session. The user will not receive a notification for syncs triggered in that session.
- If the daemon is not running when the install-triggered sync completes (e.g., first-time install where `install.py` starts the server but the gateway starts after the sync finishes), the monitor does not exist and no notification is sent. This is acceptable for first-time installs; the user can check status via `archon rag status`.

---

## Architecture

### New module: `archon/rag/notification_monitor.py`

```python
class IndexingNotificationMonitor:
    def __init__(
        self,
        state_store: IndexingStateStore,
        bot: Bot,
        allowed_user_ids: list[int],
        notifications_config: NotificationsConfig,
        poll_interval: float = 30.0,
    ) -> None: ...

    async def run(self) -> None:
        """Poll state file every poll_interval seconds. Detect terminal transition and notify."""

    async def _check_and_notify(self) -> None:
        """Read state, check if all collections terminal and trigger is notifiable, send message."""

    def _build_message(self, state: IndexingState) -> str:
        """Compose notification text from terminal collection states."""

    async def _send_to_all(self, message: str) -> None:
        """Send message to all allowed_user_ids; log and continue on failure."""
```

### State file change: `IndexingState`
```python
@dataclass
class IndexingState:
    collections: dict[str, CollectionProgress] = field(default_factory=dict)
    last_updated: str = ...
    trigger: str | None = None          # NEW: "install" | "manual" | None  ("update" reserved)
```

### `IndexingStateStore.set_trigger`
```python
def set_trigger(self, trigger: str | None) -> None:
    """Read current state (or create empty state if file absent), set trigger field, write atomically."""
```

### Gateway wiring (`gateway.py`)
```python
# After bot is live, before polling starts:
# Declare before the try block (same pattern as _cleanup_task):
# _monitor_task: asyncio.Task | None = None
if cfg.rag.enabled and rag_state == RagState.RUNNING:
    monitor = IndexingNotificationMonitor(
        state_store=IndexingStateStore(Path(cfg.rag.db_path)),
        bot=bot,
        allowed_user_ids=cfg.access.allowed_user_ids,
        notifications_config=cfg.notifications,
    )
    _monitor_task = asyncio.create_task(monitor.run(), name="rag-indexing-monitor")
# On shutdown: if _monitor_task is not None: _monitor_task.cancel() + await with suppress(CancelledError)
```

### server.py trigger injection
```python
# Place set_trigger BEFORE the if sync_timeout == 0: branch — executes regardless of path:
state_store = IndexingStateStore(Path(cfg.rag.db_path))
state_store.set_trigger("install")  # called exactly once per startup
# set_trigger is called exactly once per startup, before whichever sync path executes
asyncio.create_task(sync.sync(cfg.rag.collections))
```

### `_handle_rag_sync` trigger injection
```python
# Before calling sync.sync():
state_store.set_trigger("manual")
result = await sync.sync(toolkit._config.rag.collections)
```

### Notification content logic
```python
failed = [name for name, cp in state.collections.items() if cp.status == IndexingStatus.FAILED]
done = [name for name, cp in state.collections.items() if cp.status == IndexingStatus.DONE]

if not failed:
    # All success
    msg = f"✅ RAG indexing complete — all {len(done)} collection(s) ready."
elif not done:
    # Total failure
    msg = "❌ RAG indexing failed — no collections are ready. Run <code>archon rag status</code> for details."
else:
    # Partial failure
    msg = f"⚠️ RAG indexing finished — {len(failed)} collection(s) failed. Run <code>archon rag status</code> for details."
```

---

## Tests

- **test_trigger_field_default** (unit): `IndexingState()` has `trigger=None`
- **test_to_dict_includes_trigger** (unit): `to_dict` serialises `trigger` as top-level key
- **test_from_dict_reads_trigger** (unit): `from_dict` deserialises `trigger` correctly
- **test_from_dict_invalid_trigger_type** (unit): non-string trigger in JSON (int, list, bool) → `None`; note: `isinstance(True, int)` is `True` in Python so the implementation must use `isinstance(val, str)` not a truthiness check
- **test_from_dict_trigger_none** (unit): missing `trigger` key → `None`
- **test_set_trigger_creates_state** (unit): `set_trigger` on absent state file creates file with trigger set
- **test_set_trigger_updates_existing** (unit): `set_trigger` preserves existing collections, updates trigger only
- **test_set_trigger_clears_trigger** (unit): `set_trigger(None)` sets trigger to `None`
- **test_no_notification_when_state_absent** (unit): monitor does nothing when state file is absent
- **test_no_notification_when_no_collections** (unit): monitor does nothing when state has no collections
- **test_no_notification_when_trigger_manual** (unit): trigger=`"manual"`, all terminal → no message sent
- **test_no_notification_when_trigger_none** (unit): trigger=`None`, all terminal → no message sent
- **test_no_notification_when_in_progress** (unit): trigger=`"install"`, one collection `in_progress` → no message
- **test_sends_success_notification** (unit): trigger=`"install"`, all `done` → `bot.send_message` called for all user IDs with the exact success message text
- **test_sends_partial_failure_notification** (unit): trigger=`"install"`, mixed `done`/`failed` → `bot.send_message` called with the exact partial failure message text
- **test_sends_total_failure_notification** (unit): trigger=`"install"`, all `failed` → `bot.send_message` called with the exact total failure message text
- **test_clears_trigger_after_notify** (unit): `set_trigger(None)` called **before** `_send_to_all`; verified by recording invocation order
- **test_no_double_notify** (unit): after trigger cleared, subsequent poll does not re-send
- **test_quiet_mode_suppresses** (unit): `notifications.mode="quiet"` → no message sent even on terminal state
- **test_normal_mode_sends** (unit): `notifications.mode="normal"` → message sent
- **test_verbose_mode_sends** (unit): `notifications.mode="verbose"` → message sent
- **test_debug_mode_sends** (unit): `notifications.mode="debug"` → message sent
- **test_send_failure_is_caught** (unit): bot raises exception on `send_message` → caught, logged, no re-raise
- **test_monitor_set_trigger_failure_caught** (unit): `state_store.set_trigger` raises `OSError` → exception caught and logged at WARNING, `_send_to_all` never called; trigger NOT cleared (retry on next poll)
- **test_no_notification_when_pending** (unit): trigger=`"install"`, one collection `pending` → no message
- **test_monitor_run_calls_check** (unit): `run()` calls `_check_and_notify`; use a mock `_check_and_notify` with a side effect that sets an `asyncio.Event`, then `await event.wait()` with a timeout for deterministic test completion before cancelling the task
- **test_monitor_run_cancellation** (unit): cancelling the `run()` task exits cleanly without exception
- **test_no_notification_when_all_pending** (unit): trigger=`"install"`, all collections `PENDING` → no message
- **test_no_send_when_no_users** (unit): empty `allowed_user_ids` → trigger cleared, no send, WARNING logged
- **test_monitor_send_partial_failure_continues** (unit): first user send raises exception, second user still receives message
- **test_server_sets_install_trigger** (unit/integration): `server.py` startup sync path writes `trigger="install"` before calling `sync()`
- **test_rag_sync_tool_sets_manual_trigger** (unit): `_handle_rag_sync` writes `trigger="manual"` before sync
- **test_server_timeout_fallback_path_sets_install_trigger** (unit): when `wait_for` raises `asyncio.TimeoutError` and a fallback `create_task` is used, `set_trigger("install")` was already called before any branch executes
- **test_build_message_success** (unit): all done → correct success message text
- **test_build_message_partial_failure** (unit): mixed done/failed → correct partial failure message
- **test_build_message_total_failure** (unit): all failed → correct total failure message

---

## Documentation update
- [ ] `CLAUDE.md`, `archon/rag/` section: add `notification_monitor.py` entry (the module lives at `archon/rag/notification_monitor.py`)
- [ ] `Documentation/Backlog/FEAT-027-rag-background-indexing-progress.md`, Phase 5 section: mark ✅ Done when complete

---

## Task breakdown

### Phase 5 — Telegram notification on completion/failure
> **Releasable**: after Task 5.4 — the full notification pipeline is wired end-to-end; notifications fire on daemon startup sync completing

#### Task 5.1 — Add `trigger` field to `IndexingState` + serialisation
- [x] **File**: `archon/rag/progress.py`
- **Depends on**: nothing (builds on existing `IndexingState` / `to_dict` / `from_dict`)
- **Description**:
  - Add `trigger: str | None = None` to `IndexingState` dataclass
  - `to_dict`: add `"trigger": state.trigger` as a top-level key in the returned dict
  - `from_dict`: read `data.get("trigger")` — accept only `str` or `None`; any other type (int, list, bool, etc.) maps to `None`; use `isinstance(val, str)` not a truthiness check (because `isinstance(True, int)` is `True` in Python)
  - `IndexingStateStore.set_trigger(trigger: str | None) -> None` — reads current state (or creates `IndexingState()`), sets `state.trigger = trigger`, writes atomically via `self.write(state)`
  - No changes to `CollectionProgress`, `update_collection`, `remove_collection`, or `read`/`write`
- **Releasable**: `trigger` field is readable/writable via `IndexingStateStore.set_trigger()` and survives round-trips through JSON
- **Tests (TDD)** — `tests/rag/test_progress.py`:
  - Unit: `test_trigger_field_default` — `IndexingState()` has `trigger=None`
  - Unit: `test_to_dict_includes_trigger` — serialised dict has `"trigger": None` when not set, `"trigger": "install"` when set
  - Unit: `test_from_dict_reads_trigger` — `from_dict({"trigger": "install", "collections": {}})` → `state.trigger == "install"`
  - Unit: `test_from_dict_invalid_trigger_type` — `from_dict({"trigger": 42, ...})` → `state.trigger is None`; also test with `True` (bool) and `[]` (list); implementation must use `isinstance(val, str)` not a truthiness check
  - Unit: `test_from_dict_trigger_missing` — no `trigger` key → `state.trigger is None`
  - Unit: `test_set_trigger_creates_state` — absent state file, call `set_trigger("install")` → file created, trigger set, collections empty
  - Unit: `test_set_trigger_updates_existing` — existing state with collections, `set_trigger("manual")` → collections preserved, trigger updated
  - Unit: `test_set_trigger_clears_trigger` — `set_trigger(None)` → `trigger` in written JSON is `null`
  - Checkpoint: `uv run pytest tests/rag/test_progress.py -v --no-cov`

#### Task 5.2 — `IndexingNotificationMonitor` core logic
- [x] **File**: `archon/rag/notification_monitor.py` (new)
- **Depends on**: Task 5.1 (trigger field + `set_trigger`)
- **Description**:
  - `IndexingNotificationMonitor.__init__(self, state_store: IndexingStateStore, bot: Bot, allowed_user_ids: list[int], notifications_config: NotificationsConfig, poll_interval: float = 30.0) -> None`
    - Stores all args; `NotificationsConfig` imported from `archon.config.loader`
  - `async def run(self) -> None` — infinite loop: `await asyncio.sleep(poll_interval)` then `await self._check_and_notify()`; exits cleanly on `asyncio.CancelledError`
  - `async def _check_and_notify(self) -> None`:
    1. Read state via `state_store.read()` — return immediately if `None` or `state.collections` is empty
    2. Return immediately if `state.trigger not in ("install", "update")` (covers `"manual"` and `None`; `"update"` is reserved for a future phase but included in the check for forward-compatibility)
    3. Check all collections are terminal: all `cp.status in (IndexingStatus.DONE, IndexingStatus.FAILED)` — if any is `pending` or `in_progress`, return
    4. Return immediately if `notifications_config.mode == "quiet"`
    5. Build message via `_build_message(state)`
    6. Call `state_store.set_trigger(None)` to clear trigger **before** sending (prevents double-send if a subsequent poll fires before delivery). If `set_trigger` raises (e.g., `OSError`), catch exception, log at WARNING level, and return without calling `_send_to_all` — trigger stays set so the next poll retries (infinite retry until the disk error resolves)
    7. Call `await self._send_to_all(message)`
  - `def _build_message(self, state: IndexingState) -> str`:
    - Count `failed` and `done` collections
    - All done: `f"✅ RAG indexing complete — all {total} collection(s) ready."`
    - None done: `"❌ RAG indexing failed — no collections are ready. Run <code>archon rag status</code> for details."`
    - Mixed: `f"⚠️ RAG indexing finished — {len(failed)} collection(s) failed. Run <code>archon rag status</code> for details."`
  - `async def _send_to_all(self, message: str) -> None`:
    - If `allowed_user_ids` is empty: log WARNING and return without sending
    - For each `user_id` in `allowed_user_ids`: `await bot.send_message(user_id, message, parse_mode="HTML")`; catch all exceptions per user, log warning and continue to next user (same pattern as `_send_notification` in `background_agent_manager.py`)
  - All logging via `logging.getLogger("archon")`
- **Releasable**: monitor can be instantiated and its logic unit-tested; not yet wired into gateway
- **Tests (TDD)** — `tests/rag/test_notification_monitor.py` (new file):
  - Unit: `test_no_notification_when_state_absent` — `state_store.read()` returns `None` → `_send_to_all` never called
  - Unit: `test_no_notification_when_no_collections` — state with empty `collections` → no send
  - Unit: `test_no_notification_when_trigger_manual` — trigger=`"manual"`, all terminal → no send
  - Unit: `test_no_notification_when_trigger_none` — trigger=`None`, all terminal → no send
  - Unit: `test_no_notification_when_in_progress` — trigger=`"install"`, one collection `in_progress` → no send
  - Unit: `test_no_notification_when_pending` — trigger=`"install"`, one collection `pending` → no send
  - Unit: `test_no_notification_when_all_pending` — trigger=`"install"`, ALL collections `pending` → no send (pre-sync state)
  - Unit: `test_sends_success_notification` — trigger=`"install"`, two `done` → `bot.send_message` called for all user IDs with the exact success message text
  - Unit: `test_sends_partial_failure_notification` — one `done`, one `failed` → `bot.send_message` called with the exact partial failure message text
  - Unit: `test_sends_total_failure_notification` — all `failed` → `bot.send_message` called with the exact total failure message text
  - Unit: `test_clears_trigger_after_notify` — `set_trigger(None)` called **before** `_send_to_all`; verified by recording invocation order (e.g., via side_effect on mock or asserting `mock_manager.assert_has_calls([call.set_trigger(None), call._send_to_all(...)], any_order=False)`)
  - Unit: `test_no_double_notify` — after trigger cleared, second call to `_check_and_notify` does not re-send
  - Unit: `test_quiet_mode_suppresses` — mode=`"quiet"` → no send
  - Unit: `test_normal_mode_sends` — mode=`"normal"` → send called
  - Unit: `test_verbose_mode_sends` — mode=`"verbose"` → message sent
  - Unit: `test_debug_mode_sends` — mode=`"debug"` → message sent
  - Unit: `test_send_failure_is_caught` — `bot.send_message` raises → caught, `_check_and_notify` completes without re-raising
  - Unit: `test_monitor_set_trigger_failure_caught` — `state_store.set_trigger` raises `OSError` → exception caught and logged, `_send_to_all` never called
  - Unit: `test_run_calls_check_and_notify` — mock `_check_and_notify` with a side effect that sets an `asyncio.Event`; create monitor with `poll_interval=0`, start task, `await asyncio.wait_for(event.wait(), timeout=1.0)`, then cancel; verify `_check_and_notify` was called (deterministic, no timing dependency)
  - Unit: `test_run_exits_cleanly_on_cancelled_error` — cancel the `run()` task; task finishes without raising
  - Unit: `test_no_send_when_no_users` — `allowed_user_ids=[]`, trigger=`"install"`, all terminal → trigger cleared, no send attempted, WARNING logged
  - Unit: `test_send_to_second_user_after_first_fails` — `allowed_user_ids=[111, 222]`, `bot.send_message(111, ...)` raises, `bot.send_message(222, ...)` still called
  - Unit: `test_build_message_success` — all done → correct text
  - Unit: `test_build_message_partial_failure` — mixed → correct text
  - Unit: `test_build_message_total_failure` — all failed → correct text
  - Checkpoint: `uv run pytest tests/rag/test_notification_monitor.py -v --no-cov`

#### Task 5.3 — Inject trigger in `server.py` and `archon_toolkit_rag.py`
- [x] **Files**: `archon/rag/server.py`, `archon/ai/archon_toolkit_rag.py`
- **Depends on**: Task 5.1 (`set_trigger` available)
- **Description**:
  - **`server.py`** — `_run_rag_server()` function: place `state_store.set_trigger("install")` BEFORE the `if sync_timeout == 0:` branch — it must execute regardless of which path (background, foreground, or timeout-fallback) is taken
    - `set_trigger("install")` is called exactly once per startup, before the `if sync_timeout == 0:` branch — not inside each branch — to avoid duplicate calls in the timeout-fallback path
    - If `state_store` is not yet constructed at that point, construct it with `Path(cfg.rag.db_path)` and call `set_trigger`
  - **`archon_toolkit_rag.py`** — `_handle_rag_sync()`: after `IndexingStateStore` is constructed (already present at line ~333) and before `result = await sync.sync(...)`, call `state_store.set_trigger("manual")`
    - This ensures the monitor will NOT notify on manual/MCP-triggered syncs
  - No other callers of `sync.sync()` exist at this phase; `install.py` runs in a separate process and does not need a trigger
- **Releasable**: trigger is correctly written to state file on each sync initiation; monitor (Task 5.2) will read the right value
- **Tests (TDD)** — `tests/rag/test_server.py` + `tests/ai/test_archon_toolkit_rag.py`:
  - Unit: `test_server_startup_sync_sets_install_trigger` — `_run_rag_server` (or startup path) calls `set_trigger("install")` before `sync.sync()`; verify via mock on `IndexingStateStore`
  - Unit: `test_server_background_path_sets_install_trigger` — `sync_timeout_seconds=0` path also calls `set_trigger("install")`
  - Unit: `test_server_timeout_fallback_path_sets_install_trigger` — when `wait_for` raises `asyncio.TimeoutError` and a fallback `create_task` is used, `set_trigger("install")` was already called before any branch executes
  - Unit: `test_rag_sync_tool_sets_manual_trigger` — `_handle_rag_sync` calls `set_trigger("manual")` before sync; mock `state_store.set_trigger` and assert called with `"manual"`
  - Checkpoint: `uv run pytest tests/rag/test_server.py tests/ai/test_archon_toolkit_rag.py -v --no-cov -k "trigger"`

#### Task 5.4 — Wire monitor into gateway
- [ ] **File**: `archon/gateway/gateway.py`
- **Depends on**: Task 5.2 (`IndexingNotificationMonitor` exists), Task 5.3 (trigger written to state file)
- **Description**:
  - Import `IndexingNotificationMonitor` from `archon.rag.notification_monitor` — guarded by `if cfg.rag.enabled` (same pattern as other RAG imports in gateway)
  - Declare `_monitor_task: asyncio.Task | None = None` before the `try` block (same pattern as `_cleanup_task`); assign inside the startup hook; guard the cancel in `stop_all()` with `if _monitor_task is not None:`
  - Note: the monitor is only created at startup time; if RAG starts later in the session the monitor is not retroactively started (accepted limitation)
  - After the bot polling startup hook fires and RAG is confirmed running (`rag_state == RagState.RUNNING`): create monitor and start task
    ```python
    if cfg.rag.enabled and rag_state == RagState.RUNNING:
        monitor = IndexingNotificationMonitor(
            state_store=IndexingStateStore(Path(cfg.rag.db_path)),
            bot=bot,
            allowed_user_ids=cfg.access.allowed_user_ids,
            notifications_config=cfg.notifications,
        )
        _monitor_task = asyncio.create_task(monitor.run(), name="rag-indexing-monitor")
    ```
  - In `stop_all()` / shutdown: `if _monitor_task is not None: _monitor_task.cancel()` + await with `suppress(asyncio.CancelledError)` — same pattern as other background tasks in gateway
  - If RAG is disabled or not running: skip monitor creation entirely (`_monitor_task` remains `None`)
- **Releasable**: end-to-end notification pipeline is active; install/update syncs send a Telegram message on completion
- **Tests (TDD)** — `tests/gateway/test_gateway.py` (or appropriate existing gateway test file):
  - Unit: `test_monitor_started_when_rag_enabled_and_running` — when `cfg.rag.enabled=True` and `rag_state == RagState.RUNNING`, `asyncio.create_task` called with a `monitor.run()` coroutine named `"rag-indexing-monitor"`
  - Unit: `test_monitor_not_started_when_rag_disabled` — `cfg.rag.enabled=False` → no monitor task
  - Unit: `test_monitor_not_started_when_rag_not_running` — `cfg.rag.enabled=True`, `rag_state != RagState.RUNNING` → no monitor task
  - Unit: `test_monitor_task_cancelled_on_shutdown` — shutdown path cancels the monitor task
  - Unit: `test_monitor_task_none_on_shutdown_when_rag_disabled` — when monitor was never created (`_monitor_task is None`), shutdown does not raise
  - Checkpoint: `uv run pytest tests/gateway/ -v --no-cov -k "monitor"`
