# Plan: RAG Integration UX Fixes

## Overview

When a user sets `rag.enabled = true` in config and restarts, the system should automatically detect the RAG state and start the service if it is installed but not running. If RAG is not installed, it should send a clear Telegram notification with actionable guidance. The installer should also include RAG discovery guidance.

Four root causes diagnosed. Seven tasks across four areas.

---

## Root Causes

| # | Issue | Evidence |
|---|-------|----------|
| 1 | Installer never mentions RAG | `install.py` lines 1125–1225 (no RAG guidance in success output) |
| 2 | `rag.enabled=true` + server not running → gateway only logs WARNING to file; nothing in Telegram; RAG silently disabled | `archon/gateway/gateway.py` lines 446–455 |
| 3 | Doctor exits 0 when RAG is enabled but broken | `archon/cli/doctor.py` lines 180–189 |
| 4 | FEAT-022 doc shows `Status: To Do` despite all tasks complete | `Documentation/Backlog/FEAT-022-rag-intelligent-collection-routing.md` line 5 |

---

## Design Decisions (from DA Review)

### Auto-START, not auto-INSTALL

**Never** run `archon rag install` (package installation) from inside the gateway. Reasons:
- `RagInstaller.run()` calls `asyncio.run()` internally — cannot be called from within an active event loop
- `subprocess.run()` for `uv pip install` blocks the asyncio event loop for 5–10 minutes
- Daemon restarts (launchd/systemd) happen silently after reboots; downloading 400MB automatically is inappropriate
- Partial install leaves venv in undefined state

**What the gateway WILL auto-do**: If RAG packages are installed and the service is registered (plist/unit exists) but the server is not listening → call `get_rag_service().start()` via `asyncio.to_thread()`. This is lightweight (seconds), safe, and respects the daemon contract.

### Three RAG states on startup

| State | Condition | Gateway action |
|-------|-----------|----------------|
| `RUNNING` | TCP probe succeeds | Use it, no notification |
| `NOT_INSTALLED` | Probe fails + `lancedb` not importable | Send Telegram: install guidance |
| `NOT_REGISTERED` | Probe fails + packages installed + plist/unit missing | Send Telegram: run `archon rag install` |
| `NOT_RUNNING` | Probe fails + packages installed + service registered | Auto-start service, re-probe, notify success or failure |

### All notifications via `dp.startup.register()`

Bot session is not live until `dp.start_polling(bot)`. All RAG state notifications must use the existing startup-hook pattern (`dp.startup.register(async_hook)`) to ensure the bot connection is established before any `bot.send_message()` is called. This matches the established `_register_deprecated_rag_notification` pattern in `gateway.py`.

### `_check_rag_server` must be synchronous

`run_doctor()` processes a `checks` list of synchronous callables returning `CheckResult`. The new `_check_rag_server` must use `socket.create_connection()` not `asyncio.open_connection()`.

---

## Tasks

- [x] Task E.1: Add `_detect_rag_state()` to `gateway.py`
- [x] Task E.2: Add `_auto_start_rag_service()` to `gateway.py`
- [ ] Task E.3: Add RAG state notifications and wire into `Gateway._run()`
- [ ] Task C.1: Extract `_check_rag_server` as a synchronous `CheckResult` function
- [ ] Task C.2: Wire `_check_rag_server` into `run_doctor()` exit code
- [ ] Task A.1: Add RAG guidance to post-install success output in `install.py`
- [ ] Task D.1: Update FEAT-022 status and move to `Completed/`

### Area E: Auto-Start RAG on Restart

#### Task E.1: Add `_detect_rag_state()` to `gateway.py`

**File(s)**: `archon/gateway/gateway.py`

**What**: Add an async function `_detect_rag_state(cfg: RagConfig) -> RagState` where `RagState` is a `str` enum with values `RUNNING`, `NOT_INSTALLED`, `NOT_REGISTERED`, `NOT_RUNNING`. Logic:
1. Run `_ensure_rag_server(cfg.host, cfg.port)` → if True, return `RUNNING`
2. Check importability of `lancedb` via `importlib.util.find_spec("lancedb")` → if None, return `NOT_INSTALLED`
3. Check if service is registered via `get_rag_service().is_installed()` → if False, return `NOT_REGISTERED`
4. Return `NOT_RUNNING`

**Why**: Precise state detection enables targeted user guidance and determines whether auto-start is appropriate.

**TDD Steps**:
1. In `tests/gateway/test_rag_auto_start.py`, add `TestDetectRagState`:
   - `test_returns_running_when_probe_succeeds` — patches `_ensure_rag_server` to return `True`, asserts `RUNNING`
   - `test_returns_not_installed_when_lancedb_missing` — probe fails, `find_spec("lancedb")` returns `None`, asserts `NOT_INSTALLED`
   - `test_returns_not_registered_when_packages_present_service_not_registered` — probe fails, lancedb importable, `get_rag_service().is_installed()` returns `False`, asserts `NOT_REGISTERED`
   - `test_returns_not_running_when_packages_installed_service_registered` — probe fails, lancedb importable, `get_rag_service().is_installed()` returns `True`, asserts `NOT_RUNNING`
2. Implement `RagState` enum (or `Literal`) and `_detect_rag_state()` in `gateway.py`
3. Run tests

**Acceptance criteria**:
- All four states correctly detected
- `find_spec` check does not import lancedb (just probes importability)
- `get_rag_service()` is the platform-abstracted service singleton

---

#### Task E.2: Add `_auto_start_rag_service()` to `gateway.py`

**File(s)**: `archon/gateway/gateway.py`

**What**: Add `async def _auto_start_rag_service(host: str, port: int) -> bool` that:
1. Calls `exit_code = await asyncio.to_thread(get_rag_service().start)` (non-blocking start)
2. If `exit_code != 0`: return `False` immediately (service failed to start — no point probing)
3. Waits up to 10 seconds for server to become reachable (re-probing every 1s via `_ensure_rag_server`)
4. Returns `True` if server responds within 10s, `False` on timeout

**Why**: `get_rag_service().start()` uses `subprocess.run()` internally (via `_run` mixin) and returns an integer exit code. Wrapping in `asyncio.to_thread()` prevents blocking the event loop. Checking the exit code before entering the re-probe loop avoids 10 seconds of fruitless polling when the service clearly failed. The re-probe loop handles the normal startup delay.

**TDD Steps**:
1. In `tests/gateway/test_rag_auto_start.py`, add `TestAutoStartRagService`:
   - `test_returns_true_when_service_starts_successfully` — `get_rag_service().start()` returns 0, `_ensure_rag_server` returns True on second probe, asserts `True`
   - `test_returns_false_immediately_when_service_exit_code_nonzero` — `start()` returns 1, asserts `False` without entering re-probe loop
   - `test_returns_false_when_server_does_not_respond_within_timeout` — `start()` returns 0 but `_ensure_rag_server` always returns False, asserts `False` after timeout
   - `test_does_not_block_event_loop` — patches `asyncio.to_thread`, asserts it is awaited (not called directly)
2. Implement `_auto_start_rag_service()` in `gateway.py`
3. Run tests

**Acceptance criteria**:
- `get_rag_service().start()` is called via `asyncio.to_thread()` (never directly)
- Returns `False` immediately when `start()` exit code is non-zero (no unnecessary polling)
- Returns `True` only when TCP probe succeeds within 10s
- Returns `False` on timeout
- No blocking of event loop

---

#### Task E.3: Add RAG state notifications and wire into `Gateway._run()`

**File(s)**: `archon/gateway/gateway.py`

**What**:

Part A — Add `_register_rag_state_notification(dp, *, rag_state, auto_started, allowed_user_ids)` that registers a startup hook sending the appropriate Telegram message per state:

| State | Message |
|-------|---------|
| `RUNNING` | (no notification) |
| `NOT_RUNNING` + auto_started=True | `✅ <b>RAG started automatically.</b>` |
| `NOT_RUNNING` + auto_started=False | `⚠️ <b>RAG service failed to start.</b>\nCheck: <code>archon rag status</code>\nLogs: <code>archon logs</code>` |
| `NOT_REGISTERED` | `⚠️ <b>RAG packages installed but service not registered.</b>\nRun: <code>archon rag install</code>` |
| `NOT_INSTALLED` | `⚠️ <b>RAG is enabled but not installed.</b>\nRun: <code>archon rag install</code> (~150MB)\nThen: <code>archon rag start</code>` |

Part B — Wire into `Gateway._run()`: Replace the existing `_ensure_rag_server` probe block (lines 446–455) with this extended block, still BEFORE `Pipeline` and `BackgroundAgentManager` construction (which use `rag_url`):
1. Call `state = await _detect_rag_state(cfg.rag)` (subsumes the existing TCP probe)
2. `auto_started = False`
3. If `state == RUNNING`: set `rag_url = f"http://{cfg.rag.host}:{cfg.rag.port}/mcp"`
4. If `state == NOT_RUNNING`: call `auto_started = await _auto_start_rag_service(cfg.rag.host, cfg.rag.port)`; if True, set `rag_url = f"http://{cfg.rag.host}:{cfg.rag.port}/mcp"`
5. (For NOT_INSTALLED / NOT_REGISTERED / failed auto-start: `rag_url` stays `None`)
6. After `dp = create_dispatcher()` (which happens later in `_run()`): call `_register_rag_state_notification(dp, rag_state=state, auto_started=auto_started, allowed_user_ids=cfg.access.allowed_user_ids)` — only when state is not `RUNNING`

**Critical constraint**: Steps 1–5 (all `rag_url` updates) MUST occur before `Pipeline(rag_url=rag_url, ...)` and `BackgroundAgentManager(rag_url=rag_url, ...)` are constructed. These constructors take `rag_url` as a constructor argument and do not observe mutations after construction.

**Why**: Combines the notification function and wiring into one task since they are tightly coupled and smaller than the state/start tasks.

**TDD Steps**:
1. In `tests/gateway/test_rag_auto_start.py`, add `TestRagStateNotification`:
   - `test_not_installed_message_sent_to_all_users` — state=NOT_INSTALLED, asserts message contains "archon rag install" and "archon rag start"
   - `test_not_registered_message_contains_install_command` — state=NOT_REGISTERED, asserts message contains "archon rag install"
   - `test_not_running_auto_started_true_sends_success` — auto_started=True, asserts "✅" and "started automatically"
   - `test_not_running_auto_started_false_sends_failure` — auto_started=False, asserts "⚠️" and "archon rag status"
   - `test_running_no_notification_registered` — state=RUNNING, asserts `dp.startup` has no RAG hook registered
   - `test_per_user_error_isolation` — first user raises, second still gets message
2. In `tests/gateway/test_startup_notification.py`, add wiring tests:
   - `test_gateway_auto_starts_when_state_is_not_running` — patches `_detect_rag_state` → NOT_RUNNING, patches `_auto_start_rag_service` → True, asserts `_auto_start_rag_service` was called
   - `test_gateway_skips_auto_start_when_not_installed` — state=NOT_INSTALLED, asserts `_auto_start_rag_service` NOT called
   - `test_gateway_updates_rag_url_after_successful_auto_start` — auto_start succeeds, asserts `rag_url` is set (not None) for the session
   - `test_gateway_no_notification_when_rag_disabled` — `cfg.rag.enabled=False`, asserts `_register_rag_state_notification` not called
3. Implement both functions and wiring; run all tests

**Acceptance criteria**:
- Auto-start only happens when state is `NOT_RUNNING` (packages installed, service registered)
- Notification registered for all states except `RUNNING`
- `rag_url` is updated when auto-start succeeds (session gets RAG context)
- `rag_url` stays `None` when auto-start fails or state is not `RUNNING`
- All notifications use `parse_mode="HTML"` and per-user error isolation

---

### Area C: Doctor Improvements

#### Task C.1: Extract `_check_rag_server` as a synchronous `CheckResult` function

**File(s)**: `archon/cli/doctor.py`

**What**: Extract the RAG server reachability check from `_check_rag_health()` into a new **synchronous** function `_check_rag_server(cfg: Config) -> CheckResult` using `socket.create_connection((cfg.rag.host, cfg.rag.port), timeout=2)`. Return values:

| Condition | `CheckResult` |
|-----------|---------------|
| `rag.enabled = false` | `CheckResult("rag server", True, "disabled")` |
| Enabled + lancedb not importable | `CheckResult("rag server", False, "RAG not installed — run: archon rag install")` |
| Enabled + packages installed, service not registered | `CheckResult("rag server", False, "service not registered — run: archon rag install")` |
| Enabled + probe fails (registered but not running) | `CheckResult("rag server", False, "not running — run: archon rag start")` |
| Enabled + probe succeeds | `CheckResult("rag server", True, "running")` |

**Why**: The current reachability check lives inside `_check_rag_health()` (async, returns None), so it never contributes to the `failures` list. The new `_check_rag_server` **replaces** the reachability probe inside `_check_rag_health` and promotes it to a first-class synchronous check. `_check_rag_health` retains only the per-collection sub-checks (staleness, model mismatch, empty collections) and is now only called when `_check_rag_server` returns ok. This avoids duplicating the TCP probe logic.

**TDD Steps**:
1. In `tests/cli/test_doctor.py`, add `TestCheckRagServer`:
   - `test_disabled_returns_ok` — `rag.enabled=False`, asserts `result.ok is True`
   - `test_not_installed_returns_fail_with_install_guidance` — `find_spec("lancedb")` returns None, asserts fail + "archon rag install"
   - `test_not_registered_returns_fail_with_install_guidance` — packages present, `get_rag_service().is_installed()` False, asserts fail
   - `test_not_running_returns_fail_with_start_guidance` — packages present, registered, socket connect fails, asserts fail + "archon rag start"
   - `test_running_returns_ok` — socket connect succeeds, asserts `result.ok is True`
2. Implement `_check_rag_server()` in `doctor.py`
3. Run tests

**Acceptance criteria**:
- Fully synchronous (no `asyncio.run()`, no `httpx`)
- All five states return correct `CheckResult`
- Remediation text matches the state (install vs start)

---

#### Task C.2: Wire `_check_rag_server` into `run_doctor()` exit code

**File(s)**: `archon/cli/doctor.py`

**What**: Add `_check_rag_server` to the synchronous `checks` list inside `run_doctor()`. The call must be guarded by config availability (same as today). The existing async `_check_rag_health()` (per-collection checks) should only run when `_check_rag_server` returns ok AND the per-collection check is relevant.

**Why**: Today, `run_doctor()` returns 0 even when RAG is enabled but broken. Adding `_check_rag_server` to the `checks` list means its failure increments `failures` and causes exit code 1.

**TDD Steps**:
1. In `tests/cli/test_doctor.py`, add `TestRunDoctorRagExitCode`:
   - `test_returns_1_when_rag_enabled_not_running` — all non-RAG checks ok, `_check_rag_server` returns fail, asserts `run_doctor()` returns 1
   - `test_returns_0_when_rag_disabled` — `rag.enabled=False`, asserts returns 0
   - `test_prints_check_mark_x_for_rag_failure` — asserts `✗` and "rag server" in stdout
   - `test_collection_checks_skipped_when_server_not_running` — `_check_rag_server` fails, asserts `_check_rag_health` NOT called
2. Update `run_doctor()` to include `_check_rag_server` in the checks list
3. Run tests; confirm all existing tests still pass

**Acceptance criteria**:
- `run_doctor()` returns 1 when `rag.enabled=true` and server not reachable
- Returns 0 when `rag.enabled=false`
- Per-collection `_check_rag_health()` only runs when server is reachable
- Exit code 1 is new behavior — no existing tests should break

---

### Area A: Installer Guidance

#### Task A.1: Add RAG guidance to post-install success output in `install.py`

**File(s)**: `install.py`

**What**: In the post-install success output block (around line 1194), always append RAG discovery guidance. No subprocess calls. No user prompt (keep it simple — the user already went through the install):

```
Optional: Enable semantic search (RAG)
  archon rag install    # install RAG dependencies (~150MB)
  archon rag start      # start the RAG service
  archon config set rag.enabled true
  archon restart
```

**Why**: The installer is the primary discovery path. Adding a small guidance block costs nothing and ensures every user sees the path to RAG. No subprocess RAG install during the main installer — it's too heavy and risky (packages, downloads, event loop issues in the RagInstaller).

**TDD Steps**:
1. In `tests/test_installer_py.py`, add `TestPostInstallRagGuidance`:
   - `test_success_message_always_includes_rag_guidance` — mocks a successful install run, captures output via `capsys`, asserts `"archon rag install"` and `"archon rag start"` appear
   - `test_rag_guidance_present_in_non_interactive_mode` — `--non-interactive` flag, asserts guidance still appears
2. Update the success output block in `main()`
3. Run tests; confirm all existing installer tests pass

**Acceptance criteria**:
- Post-install output always contains `archon rag install` and `archon rag start`
- Always shown (not conditional on any prompt)
- No subprocess calls to install RAG during main installer

---

### Area D: Documentation Fix

#### Task D.1: Update FEAT-022 status and move to Completed/

**File(s)**: `Documentation/Backlog/FEAT-022-rag-intelligent-collection-routing.md` → `Documentation/Completed/FEAT-022-rag-intelligent-collection-routing.md`

**What**:
1. Change the `**Status**` line from `To Do` to `Completed`
2. Move the file from `Documentation/Backlog/` to `Documentation/Completed/`
3. Update `Documentation/990_documentation_index_and_contribution_guide.md` if it references the file path

**Why**: All acceptance criteria are marked `[x]` complete (git log: `2a7a021 docs(feat-022): mark all acceptance criteria complete`). Leaving a completed feature in `Backlog/` is inconsistent with project conventions — `Documentation/Completed/` is for implemented features.

**TDD Steps**: No automated test needed. Verify manually: file exists at new path, old path is gone, status header reads "Completed".

**Acceptance criteria**:
- File exists at `Documentation/Completed/FEAT-022-rag-intelligent-collection-routing.md`
- `**Status**: Completed` in header
- File removed from `Documentation/Backlog/`

---

## Implementation Order

```
E.1 → E.2 → E.3 → C.1 → C.2 → A.1 → D.1
```

Rationale:
- E tasks build on each other (state detection → auto-start → notification + wiring)
- C tasks are pure-CLI, no dependency on E
- A is standalone in `install.py`
- D is documentation only

---

## Key Patterns to Follow

| Pattern | Reference |
|---------|-----------|
| `CheckResult` usage | `_check_git()`, `_check_uv()` in `archon/cli/doctor.py` |
| `dp.startup.register()` notification hook | `_register_deprecated_rag_notification()` in `archon/gateway/gateway.py` |
| Gateway integration test wiring | `test_gateway_registers_deprecated_notification_*` in `tests/gateway/test_startup_notification.py` lines 648-663 |
| `asyncio.to_thread()` for blocking calls | Used for subprocess operations elsewhere in the codebase |
| Installer test | `patch("install.subprocess.run", ...)` + `capsys` in `tests/test_installer_py.py` |

---

## Out of Scope

- Auto-installing RAG packages from the gateway (too heavy, blocks event loop, `asyncio.run()` conflict)
- Auto-running `archon rag sync` on startup (sync is user-initiated; server handles its own startup indexing)
- `rag.auto_start` config flag (YAGNI — auto-start is always the right behavior when packages are installed; user who wants manual control should not register the service)
- Adding RAG status to `archon status` — a worthwhile future improvement but not part of this fix
