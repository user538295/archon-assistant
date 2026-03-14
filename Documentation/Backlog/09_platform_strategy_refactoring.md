
# Platform Strategy Refactoring — Implementation Plan

Last reviewed: 2026-03-14
Next review: 2026-06-14

## Context

Archon's platform-specific code (macOS launchd vs Linux systemd) is scattered across 5+ files with `if platform.system() == "Darwin"` checks. This causes:
- Wrong error messages on Linux (macOS-specific remediation text)
- Missing systemd hardening (no `RestartSec`, no `loginctl enable-linger`, no `Environment=PATH`)
- `os.execv()` in `/restart` breaks systemd process tracking
- Whisper binary discovery is Homebrew-biased

**Goal:** Refactor into a Strategy pattern with `PlatformService` and `PlatformRuntime` ABCs, enabling clean macOS + Linux support now and Windows later.

## Decisions

- `install.py` stays standalone PEP 723 — fix bugs inline, no `archon.platform` imports
- `restart_process()` lifecycle: caller performs app-level cleanup first, then calls `restart_process()` which only handles the OS-level restart. On macOS: `os.execv()` (launchd tracks the new PID). On Linux: always `os.execv()` too — NOT `systemctl restart`, because that SIGTERMs the calling process mid-execution. Both platforms use the same mechanism; the difference is only in `find_binary()` search paths.
- `health_check.sh` stays as shell (add-on, not core)
- Module-level lazy singletons with `override()`/`reset()` for DI in tests. An `autouse` fixture in `tests/platform/conftest.py` calls `reset()` after every test to prevent singleton leakage.
- `ServiceInfo` replaces `cli/status.py`'s existing `ServiceInfo` — the new `platform.types.ServiceInfo` adds a `label` field; `status.py` will import from `archon.platform.types` instead of defining its own
- `register_signals(loop, shutdown_callback)` contract: caller provides the asyncio loop and an `async def shutdown_callback()` coroutine; the platform implementation calls `loop.add_signal_handler(sig, handler)` where `handler` is a sync closure that calls `loop.create_task(shutdown_callback())`. Double-signal guard tracks the task object: re-triggers only if `_shutdown_task is None or _shutdown_task.done()` (matching gateway.py's edge case where a completed-but-process-still-running situation allows re-trigger). This ensures the `finally` cleanup block always runs. Signature: `shutdown_callback: Callable[[], Coroutine[Any, Any, None]]`.
- **Idempotency contract** — `start()` returns 0 when already running (not 1). `stop()` returns 0 when already stopped (not 1). "Already in desired state" is success, not failure.
- `process_uptime(pid)` stays on `PlatformRuntime` as a utility — `PlatformService.status()` may call `get_runtime().process_uptime()` internally
- **No manual testing** — all verification is automated. Every checkpoint is a `uv run pytest` gate, nothing else.
- **Shared `_RunMixin`** — the `_run(cmd, dry_run)` helper is a mixin class inherited by both `PlatformService` and `PlatformRuntime`, eliminating code duplication. The mixin also provides `_run(cmd, dry_run, stdout=)` where a synthetic `stdout` can be provided in dry-run mode for testing output-parsing paths.
- **Dry-run with command recording** — all platform methods that call `subprocess.run()` go through the `_RunMixin._run(cmd)` helper. When `dry_run=True`, it appends the command to `self.command_log` and returns a `CompletedProcess(returncode=0, stdout=synthetic_stdout, stderr="")`. The optional `stdout` parameter allows dry-run tests to feed realistic output for parsing tests (e.g., `launchctl list` output with PID). Error-path testing uses separate unit tests that mock `_run()` to return non-zero rc or raise `FileNotFoundError`.
- **Cross-platform test execution** — all platform test files use `pytest.mark` guards: `@pytest.mark.macos` for macOS-only tests, `@pytest.mark.linux` for Linux-only tests. Tests that are fully mocked (no real OS calls) run on all platforms without guards. `conftest.py` registers custom marks and auto-skips based on `sys.platform`.
- **Live E2E tests** — marked `@pytest.mark.live`, excluded from default runs via `pyproject.toml` `addopts = "-m 'not live'"`, runnable locally with `uv run pytest -m live`. NOT skipped via conftest — the pyproject.toml filter handles it so `-m live` overrides correctly.
- **PlatformService error contract** — all mutating methods (`start`, `stop`, `restart`, `register`, `unregister`) return `int` (0=success, 1=failure). They do NOT raise on operational failures — only on programming errors (`TypeError`, `ValueError`). Error messages are logged via `logging.getLogger("archon")`. This enables CLI consumers to `return get_service().start()` directly as exit codes. The `status()` method returns `ServiceInfo` (never raises). `remediation_hint()` returns `str`.
- **Shared POSIX runtime logic** — `register_signals()` and `process_uptime()` are implemented as **concrete methods** on `PlatformRuntime` base class (not abstract). Mac and Linux subclasses inherit them — no copy-paste. Only `find_binary()` search paths and `restart_process()` differ per platform (remain abstract).

## Target Structure

```
archon/platform/
├── __init__.py          # get_service(), get_runtime(), override(), reset()
├── types.py             # ServiceInfo dataclass
├── _run_mixin.py        # _RunMixin with _run(cmd, dry_run, stdout) + command_log
├── service.py           # PlatformService ABC (extends _RunMixin)
├── runtime.py           # PlatformRuntime ABC (extends _RunMixin)
├── macos/
│   ├── __init__.py
│   ├── service.py       # LaunchdService
│   └── runtime.py       # MacRuntime
├── linux/
│   ├── __init__.py
│   ├── service.py       # SystemdService
│   └── runtime.py       # LinuxRuntime
└── windows/
    ├── __init__.py
    ├── service.py       # WindowsService (stub)
    └── runtime.py       # WindowsRuntime (stub)
```

## Existing Patterns to Follow

- **ABC pattern:** `archon/ai/truncation.py` — `TruncationStrategy` ABC + `_STRATEGIES` dict + `get_truncation_strategy()` factory
- **DI pattern:** `archon/ai/session_manager.py` — constructor injection with optional params + defaults
- **Test pattern:** `monkeypatch.setattr()` + `patch()` context managers, `tmp_path` fixtures
- **Config:** `pyproject.toml` — `asyncio_mode = "auto"`, `--cov-fail-under=85`

## Key Files to Modify

| File | Action |
|------|--------|
| `archon/cli/service.py` | Replace `_is_macos()`, `_PLIST_PATH`, all branching → `get_service()` delegation |
| `archon/cli/status.py` | Replace local `ServiceInfo` + `_get_service_info()` + `platform.system()` at line 122 → import from `archon.platform.types` + `get_service().status()` + `get_service().service_name` |
| `archon/gateway/gateway.py:431-443` | Replace inline `add_signal_handler` → `get_runtime().register_signals(loop, callback)` |
| `archon/chat/commands.py:167` | Replace `os.execv()` → `get_runtime().restart_process()` (cleanup stays in caller) |
| `archon/ai/stt.py:27-44` | Replace `_find_whisper_binary()` → `get_runtime().find_binary("whisper")` |
| `install.py:362,1003,1048,1060` | Fix error messages + add platform guard (inline, no imports) |
| `scripts/archon.service` | Add RestartSec, TimeoutStopSec, PATH, network-online |

---

## Tasks

### Phase A — Foundation

- [X] **T1** — Create `archon/platform/` package skeleton
  - **Deps:** — | **Par:** T2, T3
  - **Files:** `archon/platform/__init__.py`, `archon/platform/macos/__init__.py`, `archon/platform/linux/__init__.py`, `archon/platform/windows/__init__.py`
  - **What:** Create the directory structure with empty `__init__.py` files for each platform subpackage. No logic yet — just the package skeleton so imports work.
  - **Tests:** None (structural only — verified implicitly by T4-T7 imports).

- [X] **T2** — `ServiceInfo` dataclass
  - **Deps:** — | **Par:** T1, T3
  - **Files:** `archon/platform/types.py`, `tests/platform/test_types.py`
  - **What:** Create a frozen dataclass `ServiceInfo` with fields: `running: bool`, `pid: int | None`, `label: str`, `uptime: str | None`. This replaces the existing `ServiceInfo` in `cli/status.py` (which lacks `label`).
  - **Unit tests:** (a) Construction with all fields, (b) default `None` values for optional fields, (c) equality between identical instances, (d) immutability (frozen).

- [X] **T3** — `_RunMixin` shared helper
  - **Deps:** — | **Par:** T1, T2
  - **Files:** `archon/platform/_run_mixin.py`, `tests/platform/test_run_mixin.py`
  - **What:** Create a mixin class with a concrete `_run(cmd: list[str], dry_run: bool = False, stdout: str = "") → subprocess.CompletedProcess` method. When `dry_run=True`: appends `cmd` to `self.command_log: list[list[str]]` and returns `CompletedProcess(args=cmd, returncode=0, stdout=stdout, stderr="")`. When `dry_run=False`: calls `subprocess.run(cmd, capture_output=True, text=True)` and returns the result. Also provide `_run_with_timeout(cmd, timeout, dry_run)` that wraps `_run` with `subprocess.TimeoutExpired` handling.
  - **Unit tests:** (a) `dry_run=True` records command in `command_log` and returns synthetic result, (b) `dry_run=True` with custom `stdout` parameter returns that stdout in the result, (c) `dry_run=False` calls `subprocess.run` (mock subprocess), (d) `command_log` accumulates across multiple `_run` calls, (e) `_run_with_timeout` raises `TimeoutExpired` when subprocess exceeds timeout (mock), (f) `_run_with_timeout` with `dry_run=True` returns instantly (no timeout behavior).

- [X] **T4** — `PlatformService` ABC
  - **Deps:** T2, T3 | **Par:** T5
  - **Files:** `archon/platform/service.py`, `tests/platform/test_service_abc.py`
  - **What:** Create an abstract base class extending `_RunMixin`. Abstract methods: `service_name` (property → `str`), `register(dry_run) → int`, `unregister(dry_run) → int`, `is_installed() → bool`, `start(dry_run) → int`, `stop(dry_run) → int`, `restart(dry_run) → int`, `status() → ServiceInfo`, `remediation_hint() → str`, `pre_activate_cleanup(dry_run) → int`. All mutating methods accept `dry_run: bool = False` and return `int` (0=success, 1=failure). They do NOT raise on operational failures.
  - **Unit tests:** (a) Cannot instantiate `PlatformService` directly (`TypeError`), (b) a minimal concrete subclass must implement ALL abstract methods to instantiate, (c) a subclass missing any method raises `TypeError`, (d) inherited `_run` from `_RunMixin` works on subclass instances, (e) return type annotation assertions.

- [X] **T5** — `PlatformRuntime` ABC
  - **Deps:** T3 | **Par:** T4
  - **Files:** `archon/platform/runtime.py`, `tests/platform/test_runtime_abc.py`
  - **What:** Create an abstract base class extending `_RunMixin`. **Concrete methods** (shared POSIX logic, inherited by Mac/Linux): `register_signals(loop, shutdown_callback)` — registers `SIGTERM` and `SIGINT` via `loop.add_signal_handler`, wraps `shutdown_callback` (an `async def`) in `loop.create_task()`, with an idempotent double-signal guard (second signal ignored if shutdown already in progress — preserves current gateway behavior). `process_uptime(pid: int) → str | None` — runs `ps -p <pid> -o etime=`, parses and returns the elapsed time string, or `None` on failure. **Abstract methods:** `restart_process()`, `find_binary(name: str, extra_paths: list[Path] | None = None) → Path | None`.
  - **Unit tests:** (a) Cannot instantiate `PlatformRuntime` directly, (b) a minimal concrete subclass must implement `restart_process` and `find_binary`, (c) `register_signals` on a concrete subclass: registers both SIGTERM and SIGINT on a mock loop, (d) `register_signals`: first signal invocation creates a task from the async callback, (e) `register_signals`: second signal invocation is ignored (idempotent — does NOT call `sys.exit`), (f) `process_uptime`: parses `"01:23:45"` → `"01:23:45"`, (g) `process_uptime`: parses `"3-02:15:30"` (days format), (h) `process_uptime`: returns `None` when subprocess fails, (i) `process_uptime`: returns `None` for non-existent PID.

- [X] **T6** — Platform detection + singletons
  - **Deps:** T4, T5 | **Par:** —
  - **Files:** `archon/platform/__init__.py`, `tests/platform/test_init.py`
  - **What:** Implement `_detect() → str` (returns `sys.platform`), `get_service() → PlatformService` (lazy singleton using `_detect()` to choose `LaunchdService` / `SystemdService`), `get_runtime() → PlatformRuntime` (lazy singleton, same logic). Uses lazy imports to avoid importing all platform modules. Raises `NotImplementedError("Unsupported platform: {name}")` for unknown platforms.
  - **Unit tests:** (a) Mock `sys.platform = "darwin"` → `get_service()` returns `LaunchdService` instance, (b) mock `sys.platform = "linux"` → returns `SystemdService`, (c) mock `sys.platform = "freebsd"` → raises `NotImplementedError`, (d) singleton: calling `get_service()` twice returns same instance, (e) same for `get_runtime()`.

- [X] **T7** — DI: `override()` / `reset()` + test fixture
  - **Deps:** T6 | **Par:** —
  - **Files:** `archon/platform/__init__.py`, `tests/platform/test_init.py`, `tests/platform/conftest.py`
  - **What:** Add `override(service=None, runtime=None)` — replaces the lazy singletons with provided instances. Add `reset()` — clears singletons so next `get_*()` call re-detects. Create `tests/platform/conftest.py` with: (1) `pytest_configure` registering `macos`, `linux`, `live` markers, (2) `pytest_collection_modifyitems` auto-skipping `@pytest.mark.macos` on non-Darwin and `@pytest.mark.linux` on non-Linux (live tests are excluded via `pyproject.toml addopts`, NOT here), (3) `autouse` fixture `_reset_platform_singletons` calling `reset()` after every test.
  - **Unit tests:** (a) `override(service=mock)` → `get_service()` returns the mock, (b) `reset()` → `get_service()` re-detects platform, (c) no leakage: test A overrides, test B (without override) gets fresh singleton, (d) `override` with only `service=` doesn't affect `runtime` and vice versa.

> **CP-A** (after T7): Foundation complete — ABCs, mixin, DI, auto-cleanup. No consumer changes, macOS app unchanged. Gate: `uv run pytest` green.

---

### Phase B — macOS Service Implementation

- [X] **T8** — `LaunchdService` scaffold + `is_installed()`
  - **Deps:** T4 | **Par:** T17-T19
  - **Files:** `archon/platform/macos/service.py`, `tests/platform/macos/test_service.py`
  - **What:** Create `LaunchdService(PlatformService)`. `service_name` property → `"launchd"`. Define `_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.archon.assistant.plist"` and `_LABEL = "com.archon.assistant"`. `is_installed()` → `self._PLIST_PATH.exists()`.
  - **Unit tests:** (a) `service_name == "launchd"`, (b) `is_installed()` returns `True` when plist exists (`tmp_path`), (c) returns `False` when missing.

- [X] **T9** — `LaunchdService._is_loaded()`
  - **Deps:** T8 | **Par:** T17-T19
  - **Files:** same as T8
  - **What:** Internal method `_is_loaded() → bool` — runs `launchctl list <label>`, returns `True` if rc=0.
  - **Unit tests:** (a) rc=0 → `True`, (b) rc≠0 → `False`, (c) `FileNotFoundError` (launchctl not found) → `False`.

- [X] **T10** — `LaunchdService.start()`
  - **Deps:** T8, T9 | **Par:** T11, T12, T13
  - **Files:** same as T8
  - **What:** `start(dry_run=False) → int` — checks `is_installed()` (returns 1 if not), checks `_is_loaded()` (returns 0 if already loaded), then `self._run(["launchctl", "load", str(self._PLIST_PATH)], dry_run)`. Returns 0 on success, 1 on failure. Logs errors.
  - **Unit tests:** (a) success → returns 0, subprocess called with correct args, (b) plist missing → returns 1, no subprocess call, (c) already loaded → returns 0, no load call, (d) `FileNotFoundError` (launchctl missing) → returns 1, (e) `dry_run=True` → `command_log` contains `["launchctl", "load", <path>]`, no subprocess called.

- [X] **T11** — `LaunchdService.stop()`
  - **Deps:** T8, T9 | **Par:** T10, T12, T13
  - **Files:** same as T8
  - **What:** `stop(dry_run=False) → int` — tries `self._run(["launchctl", "unload", str(self._PLIST_PATH)], dry_run)`. If plist doesn't exist but service is loaded, falls back to `bootout gui/{uid}/{label}`. Returns 0 on success, 1 on failure.
  - **Unit tests:** (a) unload success → returns 0, (b) plist exists but unload fails → returns 1, (c) already stopped → returns 0, (d) `bootout` fallback when plist missing but loaded, (e) `FileNotFoundError` → returns 1, (f) `dry_run=True` → correct `command_log`, no subprocess.

- [X] **T12** — `LaunchdService.status()`
  - **Deps:** T8, T9 | **Par:** T10, T11, T13
  - **Files:** same as T8
  - **What:** `status() → ServiceInfo` — runs `launchctl list <label>`, parses PID from stdout using regex `"PID"\s*=\s*(\d+)`. Calls `get_runtime().process_uptime(pid)` for uptime field. Returns `ServiceInfo(running=pid>0, pid=pid, label=self._LABEL, uptime=uptime)`. Never raises — returns `ServiceInfo(running=False, ...)` on any error.
  - **Unit tests (all use mocked subprocess with realistic stdout, NOT dry-run):** (a) running with PID=1234 → `ServiceInfo(running=True, pid=1234)`, (b) stopped (no PID line) → `ServiceInfo(running=False, pid=None)`, (c) PID=0 → `ServiceInfo(running=False, pid=None)`, (d) malformed output (no regex match) → `ServiceInfo(running=False)`, (e) empty stdout → `ServiceInfo(running=False)`, (f) non-numeric PID value `"PID" = "abc"` → `ServiceInfo(running=False)`, (g) subprocess fails → `ServiceInfo(running=False)`.

- [X] **T13** — `LaunchdService.remediation_hint()` + `pre_activate_cleanup()`
  - **Deps:** T8, T9 | **Par:** T10, T11, T12
  - **Files:** same as T8
  - **What:** `remediation_hint() → str` — returns macOS-specific help text with `launchctl` commands. `pre_activate_cleanup(dry_run=False) → int` — unloads service if currently loaded (idempotent), returns 0.
  - **Unit tests:** (a) hint contains `"launchctl"`, (b) cleanup when loaded → unloads, returns 0, (c) cleanup when not loaded → no-op, returns 0, (d) cleanup with `dry_run=True` → records command, no subprocess.

- [X] **T14** — `LaunchdService.restart()`
  - **Deps:** T10, T11 | **Par:** —
  - **Files:** same as T8
  - **What:** `restart(dry_run=False) → int` — calls `stop(dry_run)` then `start(dry_run)`. If stop fails, still attempts start. Returns 0 only if both succeed.
  - **Unit tests:** (a) both succeed → returns 0, (b) stop fails → still calls start, returns 1, (c) start fails → returns 1, (d) `dry_run=True` → `command_log` contains stop command before start command.

- [X] **T15** — `LaunchdService.register()` + `unregister()`
  - **Deps:** T10, T11 | **Par:** —
  - **Files:** same as T8
  - **What:** `register(dry_run=False) → int` — reads `scripts/com.archon.assistant.plist` template, substitutes `__ARCHON_DIR__`, `__UV_PATH__`, `__LOG_FILE__` with runtime values, writes to `~/Library/LaunchAgents/` (skip write when dry_run), then `self._run(["launchctl", "load", ...], dry_run)`. If load fails, cleans up the written plist. `unregister(dry_run=False) → int` — unloads + deletes plist (skip delete when dry_run).
  - **Unit tests:** (a) template placeholders substituted correctly in output, (b) file written to correct path (`tmp_path`), (c) `dry_run=True` → no file written, commands recorded in `command_log`, (d) `PermissionError` on write → returns 1, no launchctl called, (e) write succeeds but `launchctl load` fails → plist file cleaned up, returns 1, (f) `unregister` removes file + unloads, (g) `unregister` with `dry_run=True` → no file deleted, commands recorded.

- [X] **T16** — LaunchdService integration test (dry-run lifecycle)
  - **Deps:** T15 | **Par:** —
  - **Files:** `tests/platform/macos/test_service.py`
  - **What:** Full lifecycle test using dry-run: `register(dry_run=True)` → `start(dry_run=True)` → `restart(dry_run=True)` → `stop(dry_run=True)` → `unregister(dry_run=True)`. No subprocess calls, no file I/O.
  - **Integration test:** Assert `command_log` ordering constraints: (a) all commands reference `launchctl`, (b) restart section contains a stop-type command before a start-type command, (c) total command count matches expected number of operations. Do NOT assert exact command lists — allows internal changes (e.g., `bootout` vs `unload`).

> **CP-B** (after T16): macOS platform service fully implemented and tested. App still unchanged. Gate: `uv run pytest` green.

---

### Phase C — macOS Runtime Implementation

- [X] **T17** — `MacRuntime` scaffold + verify inherited methods
  - **Deps:** T5 | **Par:** T8-T16, T20-T30
  - **Files:** `archon/platform/macos/runtime.py`, `tests/platform/macos/test_runtime.py`
  - **What:** Create `MacRuntime(PlatformRuntime)`. Inherits `register_signals()` and `process_uptime()` from `PlatformRuntime` base — no reimplementation needed. Only needs to implement the abstract methods `restart_process()` and `find_binary()` (done in T18, T19).
  - **Unit tests (verify inherited methods work on MacRuntime instance):** (a) `register_signals` registers both SIGTERM and SIGINT on a mock loop, (b) first signal creates task from async callback, (c) second signal is ignored (idempotent — does NOT force-exit, preserves current gateway behavior).

- [X] **T18** — `MacRuntime.restart_process()`
  - **Deps:** T17 | **Par:** T19, T20-T30
  - **Files:** same as T17
  - **What:** `restart_process()` — calls `os.execv(sys.executable, [sys.executable] + sys.argv)`. No cleanup — caller's responsibility (see Decisions).
  - **Unit tests:** (a) `os.execv` called with correct args (mock `os.execv`), (b) `OSError` from `execv` is propagated (not swallowed).

- [X] **T19** — `MacRuntime.find_binary()`
  - **Deps:** T17 | **Par:** T18, T20-T30
  - **Files:** same as T17
  - **What:** `find_binary(name, extra_paths=None) → Path | None` — search order: (1) `shutil.which(name)`, (2) `/opt/homebrew/bin/{name}`, (3) `/usr/local/bin/{name}`, (4) each path in `extra_paths`. Returns first found `Path` or `None`. Uses `shutil.which()` for PATH lookup (checks executable bit) and `Path.exists()` for hardcoded paths.
  - **Unit tests:** (a) found via `shutil.which` → returns that path, (b) not in PATH but exists at `/opt/homebrew/bin/` → returns homebrew path, (c) not found anywhere → returns `None`, (d) `extra_paths` fallback works, (e) search order is correct (which > homebrew > local > extra).

---

### Phase D — Linux Service Implementation

_All Phase D tasks are parallel with Phase B+C (independent platform)_

- [X] **T20** — `SystemdService` scaffold + `is_installed()`
  - **Deps:** T4 | **Par:** T8-T19
  - **Files:** `archon/platform/linux/service.py`, `tests/platform/linux/test_service.py`
  - **What:** Create `SystemdService(PlatformService)`. `service_name` property → `"systemd"`. Define `_UNIT_PATH = Path.home() / ".config" / "systemd" / "user" / "archon.service"` and `_SERVICE_NAME = "archon"`. `is_installed()` → `self._UNIT_PATH.exists()`.
  - **Unit tests:** (a) `service_name == "systemd"`, (b) `is_installed()` with unit file present → `True`, (c) missing → `False`.

- [X] **T21** — `SystemdService.start()`
  - **Deps:** T20 | **Par:** T22, T23, T24, T25
  - **Files:** same as T20
  - **What:** `start(dry_run=False) → int` — `self._run(["systemctl", "--user", "start", "archon"], dry_run)`. Returns 0 if rc=0, 1 otherwise.
  - **Unit tests:** (a) success → returns 0, correct subprocess args, (b) failure (rc=1) → returns 1, (c) `FileNotFoundError` (systemctl missing) → returns 1, (d) `dry_run=True` → `command_log` correct, no subprocess.

- [X] **T22** — `SystemdService.stop()`
  - **Deps:** T20 | **Par:** T21, T23, T24, T25
  - **Files:** same as T20
  - **What:** `stop(dry_run=False) → int` — `self._run(["systemctl", "--user", "stop", "archon"], dry_run)`. Returns 0 if rc=0, 1 otherwise.
  - **Unit tests:** Same pattern as T21.

- [X] **T23** — `SystemdService.status()`
  - **Deps:** T20 | **Par:** T21, T22, T24, T25
  - **Files:** same as T20
  - **What:** `status() → ServiceInfo` — runs `systemctl --user is-active archon` (returns "active"/"inactive"/"failed") + `systemctl --user show archon --property=MainPID` (returns `MainPID=<n>`). Parses `MainPID=(\d+)`. Returns `ServiceInfo(running=is_active, pid=main_pid, label="archon", uptime=...)`. Never raises.
  - **Unit tests (all use mocked subprocess with realistic stdout, NOT dry-run):** (a) active with MainPID=5678 → `ServiceInfo(running=True, pid=5678)`, (b) inactive → `ServiceInfo(running=False)`, (c) failed → `ServiceInfo(running=False)`, (d) MainPID=0 → `ServiceInfo(running=False, pid=None)`, (e) malformed output → `ServiceInfo(running=False)`, (f) empty stdout → `ServiceInfo(running=False)`, (g) non-numeric MainPID → `ServiceInfo(running=False)`.

- [X] **T24** — `SystemdService.remediation_hint()` + `pre_activate_cleanup()`
  - **Deps:** T20 | **Par:** T21, T22, T23, T25
  - **Files:** same as T20
  - **What:** `remediation_hint() → str` — Linux-specific text with `systemctl` commands. `pre_activate_cleanup(dry_run=False) → int` — stops service (ignores errors), returns 0.
  - **Unit tests:** (a) hint contains `"systemctl"`, (b) cleanup calls stop, returns 0, (c) cleanup with `dry_run=True` → records command.

- [X] **T25** — `SystemdService.restart()`
  - **Deps:** T21, T22 | **Par:** —
  - **Files:** same as T20
  - **What:** `restart(dry_run=False) → int` — `self._run(["systemctl", "--user", "restart", "archon"], dry_run)`. Returns 0 if rc=0, 1 otherwise.
  - **Unit tests:** (a) success → 0, (b) failure → 1, (c) `dry_run=True` → correct `command_log`.

- [X] **T26** — `SystemdService.register()` + `unregister()`
  - **Deps:** T21, T22 | **Par:** —
  - **Files:** same as T20
  - **What:** `register(dry_run=False) → int` — reads `scripts/archon.service` template, substitutes `__ARCHON_DIR__`, `__UV_PATH__`, `__LOG_FILE__`, writes to `~/.config/systemd/user/` (skip when dry_run), then runs `daemon-reload` + `enable` + `start` + `loginctl enable-linger $USER`. If enable fails, cleans up unit file. `unregister(dry_run=False) → int` — stop + disable + remove file + daemon-reload.
  - **Unit tests:** (a) template substituted correctly, (b) file written to correct path, (c) `dry_run=True` → no file written, commands recorded, (d) linger called after enable, (e) unregister removes file + runs correct systemctl commands, (f) `PermissionError` on write → returns 1, no systemctl called, (g) write succeeds but enable fails → unit file cleaned up, returns 1.

- [X] **T27** — SystemdService integration test (dry-run lifecycle)
  - **Deps:** T26 | **Par:** —
  - **Files:** `tests/platform/linux/test_service.py`
  - **What:** Full lifecycle test using dry-run, same pattern as T16 but for systemd.
  - **Integration test:** Assert `command_log` ordering constraints: all commands reference `systemctl` or `loginctl`, register includes `daemon-reload` → `enable` → `start` in order, restart command present. Do NOT assert exact command lists.

> **CP-D** (after T19+T27): Both platform service implementations done and tested. App still unchanged. Gate: `uv run pytest` green.

---

### Phase E — Linux Runtime Implementation

_All Phase E tasks are parallel with Phase D_

- [X] **T28** — `LinuxRuntime` scaffold + verify inherited methods
  - **Deps:** T5 | **Par:** T8-T27
  - **Files:** `archon/platform/linux/runtime.py`, `tests/platform/linux/test_runtime.py`
  - **What:** Create `LinuxRuntime(PlatformRuntime)`. Inherits `register_signals()` and `process_uptime()` from base. Only needs to implement `restart_process()` and `find_binary()` (T29, T30).
  - **Unit tests:** Same as T17 — verify inherited methods work on `LinuxRuntime` instance.

- [X] **T29** — `LinuxRuntime.restart_process()`
  - **Deps:** T28 | **Par:** T30
  - **Files:** same as T28
  - **What:** `restart_process()` — `os.execv(sys.executable, [sys.executable] + sys.argv)`. Same as Mac — NOT `systemctl restart` (that SIGTERMs the calling process).
  - **Unit tests:** Same pattern as T18.

- [X] **T30** — `LinuxRuntime.find_binary()`
  - **Deps:** T28 | **Par:** T29
  - **Files:** same as T28
  - **What:** `find_binary(name, extra_paths=None) → Path | None` — search order: (1) `shutil.which(name)`, (2) `~/.local/bin/{name}`, (3) `/usr/local/bin/{name}`, (4) `extra_paths`. Same logic as T19 but different hardcoded paths.
  - **Unit tests:** Same pattern as T19 but with Linux-specific paths.

> **CP-E** (after T30): Linux Runtime complete. All platform implementations done for both macOS and Linux. Gate: `uv run pytest` green.

---

### Phase F — Consumer Migration

**IMPORTANT:** T31 runs first with its own checkpoint (CP-F0) to validate the `override()` DI pattern before parallelizing the rest.

- [X] **T31** — Migrate `cli/service.py` → `get_service()`
  - **Deps:** T16, T27 | **Par:** —
  - **Files:** `archon/cli/service.py`, `tests/cli/test_service.py`
  - **What:** Remove `_is_macos()`, `_macos_is_loaded()`, `_PLIST_PATH`, and all platform branching. `run_start()` becomes `return get_service().start()`. Same for `run_stop()`, `run_restart()`. Print user-facing messages based on the int return value (0 = success message, 1 = failure message). The current `test_service.py` has 30+ tests — these must be preserved as behavioral equivalence tests, NOT replaced with simple delegation checks.
  - **Unit tests (mock via `override()`):** (a) `run_start()` returns 0 when `get_service().start()` returns 0, (b) returns 1 when service returns 1, (c) prints correct success message ("Archon started"), (d) prints correct failure message ("Failed to start"), (e) same pattern for stop and restart, (f) error handling when `get_service()` raises `NotImplementedError` (unsupported platform).

> **CP-F0** (after T31): First consumer migrated. DI pattern validated with real consumer code. Gate: `uv run pytest` green.

- [X] **T32** — Migrate `cli/status.py` → `get_service().status()`
  - **Deps:** T31 | **Par:** T33, T34, T35
  - **Files:** `archon/cli/status.py`, `tests/cli/test_status.py`
  - **What:** Remove local `ServiceInfo` dataclass — import from `archon.platform.types`. Remove `_get_service_info()` and the `platform.system()` call at line 122. Replace with `get_service().status()` and `get_service().service_name`.
  - **Unit tests (mock via `override()`):** (a) status display shows correct service name from `get_service().service_name`, (b) running service shows PID and uptime, (c) stopped service shows "not running", (d) output format matches existing behavior.

- [X] **T33** — Migrate `gateway.py` signal handling → `get_runtime().register_signals()`
  - **Deps:** T17, T28 | **Par:** T32, T34, T35
  - **Files:** `archon/gateway/gateway.py`, gateway tests
  - **What:** Remove the inline `loop.add_signal_handler()` block and the `_signal_handler` closure with `nonlocal _shutdown_task` at lines 431-443. Replace with `get_runtime().register_signals(loop, shutdown_callback)` where `shutdown_callback` is the async function that calls `dp.stop_polling()`.
  - **Unit tests:** (a) `register_signals()` called with correct loop and async callback, (b) **integration test:** wire real gateway shutdown callback through `register_signals()` on a mock loop → invoke the registered handler → verify `dp.stop_polling()` task is created (proves callback shape compatibility), (c) **double-signal preservation:** invoke the registered handler twice rapidly → verify second invocation is ignored (idempotent, NOT force-exit) — ensures `finally` cleanup block still runs.

- [X] **T34** — Migrate `commands.py` `/restart` → `get_runtime().restart_process()`
  - **Deps:** T18, T29 | **Par:** T32, T33, T35
  - **Files:** `archon/chat/commands.py`, commands tests
  - **What:** Replace `os.execv(sys.executable, [sys.executable] + sys.argv)` at line 167 with `get_runtime().restart_process()`. Keep all existing cleanup code in the caller (job_scheduler → bg_manager → bg_mcp_server → session_manager cleanup happens BEFORE `restart_process()`).
  - **Unit tests:** (a) cleanup sequence unchanged: job_scheduler.stop → bg_manager.stop_all → bg_mcp_server.stop → session_manager.close_all → `restart_process()`, (b) `restart_process()` is called AFTER all cleanup completes, (c) error from `restart_process()` (`OSError`) is handled gracefully.

- [X] **T35** — Migrate `stt.py` → `get_runtime().find_binary("whisper")`
  - **Deps:** T19, T30 | **Par:** T32, T33, T34
  - **Files:** `archon/ai/stt.py`, `tests/ai/test_stt.py`
  - **What:** Replace `_find_whisper_binary()` (which uses `path.exists()` on hardcoded paths) with `get_runtime().find_binary("whisper")`. **This is a deliberate behavior change:** `shutil.which()` checks PATH + executable bit, while the old code used `path.exists()` only. Document this in a test.
  - **Unit tests:** (a) whisper found via `find_binary` → `STTHandler` uses it, (b) whisper not found → fallback behavior preserved, (c) custom extra paths work, (d) **`test_find_binary_requires_executable_bit`** — binary exists at known path but lacks +x permission → returns `None` (documents the intentional behavior change vs old `path.exists()` code).

> **CP-F1** (after T32): `cli/service.py` and `cli/status.py` migrated. Gate: `uv run pytest` green.

- [X] **T36** — Remove dead code + AST-based platform check guard
  - **Deps:** T31, T32, T33, T34, T35 | **Par:** —
  - **Files:** all migrated files, `tests/platform/test_no_platform_checks.py`
  - **What:** Remove leftover `import platform`, dead constants (`_PLIST_PATH`), dead helper functions (`_is_macos`, `_macos_is_loaded`, `_find_whisper_binary`, `_get_service_info`). Create an automated guard test.
  - **Guard test:** AST-based scan of ALL Python files under `archon/` (excluding `archon/platform/`) for: `platform.system` attribute access, `sys.platform` attribute access, `os.name` attribute access, `os.uname` calls, `from platform import system`, `from sys import platform` direct imports. Asserts zero matches. Broader than just migrated files — prevents future regressions.

- [X] **T37** — DI-wired E2E test
  - **Deps:** T36 | **Par:** —
  - **Files:** `tests/platform/test_e2e_dry_run.py`
  - **What:** Full-stack test exercising the consumer → platform delegation chain through real code paths. Uses `override()` to inject platform service.
  - **E2E tests:** (a) `run_start()` via CLI layer → service's `start()` called with dry-run, returns correct exit code (0 or 1), `command_log` contains correct platform command, (b) `run_stop()` → same pattern, (c) `run_restart()` → same pattern, (d) `run_status()` → mocks `subprocess.run` to return realistic stdout (NOT dry-run — dry-run can't test parsing), verifies `ServiceInfo` returned with correct `service_name` + parsed PID, (e) signal registration → mock loop confirms handlers registered with correct async callback shape + idempotent double-signal guard, (f) `find_binary("whisper")` → returns `Path | None` via mocked `shutil.which`, no real subprocess.

- [X] **T38** — Live E2E test
  - **Deps:** T37 | **Par:** —
  - **Files:** `tests/platform/test_live_e2e.py`
  - **What:** Real OS interaction test. Marked `@pytest.mark.live`, excluded from default runs via `pyproject.toml addopts = "-m 'not live'"`. Run locally with `uv run pytest -m live`. Tests on current platform only.
  - **Pre-test cleanup:** Calls `pre_activate_cleanup()` + `unregister()` (idempotent) at test START to clear stale state from interrupted prior runs.
  - **Live E2E tests:** (a) `register()` → verify service file exists at expected path, (b) `start()` → verify service is running (real `launchctl list` or `systemctl is-active`), (c) `status()` → returns `ServiceInfo(running=True, pid>0)`, (d) `stop()` → verify service stopped, (e) `unregister()` → verify service file removed.
  - **Post-test cleanup in `finally`:** `stop()` (ignore errors) + `unregister()` (ignore errors). Double cleanup (before + after) ensures resilience against crashes/SIGKILL from prior runs.

> **CP-F2** (after T38): All consumers migrated, dead code removed, DI-wired E2E + live E2E pass. Gate: `uv run pytest` green.

---

### Phase G — install.py Inline Fixes (independent track)

_All Phase G tasks can run in parallel with Phases A-F (install.py is standalone PEP 723)_

- [X] **T39** — Guard plist unload on Linux
  - **Deps:** — | **Par:** T40, T40a, T41, all A-F
  - **Files:** `install.py`, `tests/test_installer_py.py`
  - **What:** Guard plist unload at line 1003-1005 with `if platform.system() != "Linux":`. Note: `plist.exists()` already returns `False` on Linux making this functionally redundant, but the explicit guard makes intent clear and prevents future breakage if path logic changes.
  - **Unit tests:** (a) on mocked Linux platform → launchctl not called, (b) on mocked macOS → launchctl called normally.

- [X] **T40** — Fix error messages in install.py
  - **Deps:** — | **Par:** T39, T40a, T41
  - **Files:** `install.py`, `tests/test_installer_py.py`
  - **What:** Fix error/remediation messages at lines 362, 1048, 1060 — currently hardcode `launchctl` text. Make platform-conditional: show `launchctl` commands on macOS, `systemctl` commands on Linux.
  - **Unit tests:** (a) on mocked macOS → messages contain `"launchctl"`, (b) on mocked Linux → messages contain `"systemctl"`, (c) no `"launchctl"` in Linux messages.

- [X] **T40a** — Fix `_do_uninstall()` platform bugs
  - **Deps:** — | **Par:** T39, T40, T41
  - **Files:** `install.py`, `tests/test_installer_py.py`
  - **What:** Fix `_do_uninstall()` (line 708) — same class of platform-specific bugs as `register_service()`: wrong remediation text, missing Linux guards on launchd commands. Add platform-conditional error messages.
  - **Unit tests:** (a) on mocked Linux → no launchctl calls, correct systemctl commands, (b) on mocked macOS → launchctl unload called, (c) error messages are platform-appropriate.

- [X] **T41** — Add `loginctl enable-linger` in Linux install
  - **Deps:** — | **Par:** T39, T40, T40a
  - **Files:** `install.py`, `tests/test_installer_py.py`
  - **What:** In the Linux `register_service()` path, after `systemctl --user enable`, add `loginctl enable-linger $USER` call (or advisory message if it fails).
  - **Unit tests:** (a) on mocked Linux → `loginctl enable-linger` called after `systemctl enable`, (b) linger failure → warning printed but install continues.

- [X] **T42** — install.py integration tests
  - **Deps:** T39, T40, T40a, T41 | **Par:** —
  - **Files:** `tests/test_installer_py.py`
  - **What:** Full Linux install + uninstall flow with mocked subprocess.
  - **Integration tests:** (a) Linux install flow: verify systemd commands in correct order + linger hint + correct error messages, (b) Linux uninstall flow: correct `systemctl` stop/disable/remove commands, no `launchctl` calls, (c) macOS install flow still works (regression check).

- [X] **T43** — Template sync test
  - **Deps:** T15, T26 | **Par:** —
  - **Files:** `tests/platform/test_template_sync.py`
  - **What:** Verify `install.py` template placeholders (`__ARCHON_DIR__`, `__UV_PATH__`, `__LOG_FILE__`) match those used in `LaunchdService.register()` and `SystemdService.register()` — prevents drift between standalone installer and platform module.
  - **Guard test:** Parse both `install.py` and the platform register methods, extract placeholder strings, assert they are identical sets.

> **CP-G** (after T43): install.py bugs fixed, Linux install/uninstall path works. Gate: `uv run pytest` green.

---

### Phase H — Harden systemd service template (independent track)

- [X] **T44** — Harden `scripts/archon.service`
  - **Deps:** — | **Par:** all A-G
  - **Files:** `scripts/archon.service`, `tests/test_service_template.py`
  - **What:** Add `RestartSec=5`, `TimeoutStopSec=10`, `After=network-online.target`, `Wants=network-online.target` to the systemd unit template.
  - **Unit tests:** Parse the service file as INI, assert each required key is present with correct value.

- [X] **T45** — Dynamic PATH injection in systemd service
  - **Deps:** T44 | **Par:** —
  - **Files:** `install.py`, `tests/test_installer_py.py`
  - **What:** In `install.py:register_service()`, capture current `$PATH` at install time, inject as `Environment=PATH=...` into the systemd service file before writing.
  - **Unit tests:** (a) generated service file contains `Environment=PATH=` with current PATH value, (b) PATH value is correctly escaped.

---

### Phase I — Remaining Fixes

- [X] **T46** — Add `ffmpeg` check in `STTHandler`
  - **Deps:** T35 | **Par:** T47, T48
  - **Files:** `archon/ai/stt.py`, `tests/ai/test_stt.py`
  - **What:** In `STTHandler.__init__()`, check `shutil.which("ffmpeg")`. If missing, log a warning (Whisper requires ffmpeg for audio decoding).
  - **Unit tests:** (a) ffmpeg found → no warning logged, (b) ffmpeg missing → warning logged with helpful message.

- [X] **T47** — Fix editor fallback + pip→uv references
  - **Deps:** — | **Par:** T46, T48
  - **Files:** `archon/cli/config_cmd.py`, `archon/ai/tts.py`, respective test files
  - **What:** Change editor fallback from `"nano"` to `"vi"` in `config_cmd.py:46` (`vi` is POSIX-mandated, available everywhere). Change `"pip install httpx"` to `"uv add httpx"` in `tts.py:84` and `"pip install edge-tts"` to `"uv add edge-tts"` in `tts.py:127`.
  - **Unit tests:** (a) default editor is `"vi"` when `$EDITOR` and `$VISUAL` unset, (b) `$EDITOR` takes precedence, (c) tts error messages reference `"uv add"` not `"pip install"`.

- [X] **T48** — Fix `health_check.sh` pgrep pattern
  - **Deps:** — | **Par:** T46, T47
  - **Files:** `scripts/health_check.sh`
  - **What:** Update pgrep pattern to also match `.venv/bin/python` in addition to `python.*archon`.
  - **Unit tests:** `bash -n scripts/health_check.sh` syntax validation test.

---

> **CP-Release** (after T48): **macOS + Linux v1 release checkpoint.** Gate: `uv run pytest` green (85%+ coverage) + `uv run mypy archon/` clean.

---

### Phase J — Windows Stubs

- [X] **T49** — `WindowsService` stub
  - **Deps:** T4 | **Par:** T50
  - **Files:** `archon/platform/windows/service.py`, `tests/platform/windows/test_service.py`
  - **What:** Create `WindowsService(PlatformService)`. `service_name` → `"windows"`. All other methods raise `NotImplementedError("Windows service management not yet supported — run Archon manually with: uv run python main.py")`.
  - **Unit tests:** (a) `service_name == "windows"`, (b) `start()` raises `NotImplementedError` with helpful message, (c) same for all other methods.

- [X] **T50** — `WindowsRuntime` stub
  - **Deps:** T5 | **Par:** T49
  - **Files:** `archon/platform/windows/runtime.py`, `tests/platform/windows/test_runtime.py`
  - **What:** Create `WindowsRuntime(PlatformRuntime)`. Overrides `register_signals()` — cannot use `loop.add_signal_handler` on Windows, so uses `signal.signal(SIGINT/SIGTERM)` with `loop.call_soon_threadsafe(lambda: loop.create_task(shutdown_callback()))` to bridge thread→asyncio safely. `find_binary` → `shutil.which(name)` only (no platform-specific paths). `process_uptime` → returns `None`. `restart_process` → `os.execv`.
  - **Unit tests:** (a) signal handlers registered via `signal.signal`, (b) callback bridges to asyncio loop correctly (mock `loop.call_soon_threadsafe`), (c) `find_binary` delegates to `shutil.which` only, (d) `process_uptime` returns `None`, (e) `restart_process` calls `os.execv`.

- [X] **T51** — Wire Windows into platform detection
  - **Deps:** T49, T50 | **Par:** —
  - **Files:** `archon/platform/__init__.py`, `tests/platform/test_init.py`
  - **What:** Add `"win32"` branch in `_detect()` → returns `WindowsService`/`WindowsRuntime`. Remove `NotImplementedError` for Windows.
  - **Unit tests:** (a) mock `sys.platform = "win32"` → `get_service()` returns `WindowsService`, (b) `get_runtime()` returns `WindowsRuntime`.

- [X] **T52** — Final validation + documentation
  - **Deps:** T51, T38, T42, T45, T48 | **Par:** —
  - **Files:** `CLAUDE.md`, docs
  - **What:** Run full test suite. Update CLAUDE.md architecture section to document the new `archon/platform/` module. Document Windows limitations (service management not yet supported, manual run only).
  - **Verification:** `uv run pytest` green, `uv run mypy archon/` clean.

---

## Test Strategy

### Test Pyramid

| Layer | What | How | Tasks |
|-------|------|-----|-------|
| **Unit** | Each method in isolation — return values, exceptions, edge cases | `monkeypatch`/`patch()` on `subprocess.run`. Realistic stdout fixtures for parsing tests (T12, T23). Mock `_run()` for error paths. | T2-T5, T8-T15, T17-T26, T28-T30, T31-T35, T39-T41, T44-T50 |
| **Integration** | Full lifecycle sequences — command ordering, state transitions | Dry-run mode: `command_log` ordering constraints (not exact sequences). No subprocess, no file I/O. | T16, T27, T42 |
| **E2E (DI-wired)** | Consumer → platform delegation through real code paths | `override()` + dry-run for commands, mocked subprocess for `status()` parsing. Tests at CLI layer. | T37 |
| **Live E2E** | Real OS interaction — actual service install/start/stop/uninstall | `@pytest.mark.live`, excluded via pyproject.toml. Pre+post cleanup (idempotent). | T38 |
| **Guards** | Prevent regression of platform abstraction | AST scan of all `archon/` (excl `platform/`). Template sync. Autouse `reset()`. | T36, T43 |

### What Dry-Run Tests Prove vs Don't Prove

| Proves | Does NOT prove |
|--------|----------------|
| Correct command construction (args, order) | Commands succeed on real OS |
| Correct command sequencing (stop before start) | stdout/stderr parsing (use mocked subprocess for that) |
| No subprocess calls when dry_run=True | File permissions, PID files, SIP restrictions |
| DI wiring: consumer → platform → correct command | Actual service lifecycle on target OS |

The gap is covered by: (1) unit tests with realistic stdout fixtures (T12, T23), (2) live E2E (T38), (3) mocked error tests (T15, T26).

### Cross-Platform Test Execution

```python
# tests/platform/conftest.py
import pytest, sys
from archon.platform import reset

def pytest_configure(config):
    config.addinivalue_line("markers", "macos: macOS-only test")
    config.addinivalue_line("markers", "linux: Linux-only test")
    config.addinivalue_line("markers", "live: requires real OS service manager")

def pytest_collection_modifyitems(items):
    for item in items:
        if "macos" in item.keywords and sys.platform != "darwin":
            item.add_marker(pytest.mark.skip(reason="macOS only"))
        if "linux" in item.keywords and sys.platform != "linux":
            item.add_marker(pytest.mark.skip(reason="Linux only"))
    # @pytest.mark.live is excluded via pyproject.toml addopts = "-m 'not live'"
    # NOT skipped here — so `uv run pytest -m live` correctly overrides and runs them.

@pytest.fixture(autouse=True)
def _reset_platform_singletons():
    yield
    reset()
```

Fully mocked tests run on **all platforms** — no skip marker needed. Only tests touching real OS paths need `@pytest.mark.macos`/`@pytest.mark.linux`.

---

## Checkpoints Summary

| Checkpoint | After | What's verified | Gate |
|------------|-------|-----------------|------|
| CP-A | T7 | Foundation: ABCs, mixin, DI, autouse cleanup | `uv run pytest` |
| CP-B | T16 | macOS LaunchdService + dry-run lifecycle | `uv run pytest` |
| CP-D | T19+T27 | Both platform service implementations + dry-run lifecycles | `uv run pytest` |
| CP-E | T30 | Both platform runtimes complete | `uv run pytest` |
| CP-F0 | T31 | First consumer migrated — DI pattern validated | `uv run pytest` |
| CP-F1 | T32 | `cli/service.py` + `cli/status.py` migrated | `uv run pytest` |
| CP-F2 | T38 | All consumers migrated + E2E + live E2E + guards | `uv run pytest` |
| CP-G | T43 | install.py Linux fixes + template sync | `uv run pytest` |
| CP-Release | T48 | macOS + Linux v1 release ready | `uv run pytest` + `uv run mypy` |

**Rule:** At every checkpoint, `uv run pytest` must be green. If a checkpoint fails, stop and fix before proceeding.

## Parallelism Summary

```
Track 1 (Strategy):  A(T1-T7) → B+C+D+E parallel(T8-T30) → F(T31-T38)
Track 2 (install.py): G(T39-T43) — fully independent, parallel with Track 1
Track 3 (systemd):    H(T44-T45) — fully independent
Track 4 (misc fixes): I(T46-T48) — T46 depends on T35, rest independent
Track 5 (Windows):    J(T49-T52) — after Tracks 1-4 complete
```

## Verification

All verification is automated — no manual testing required (except optional `@pytest.mark.live`).

After all tasks complete:
1. `uv run pytest` — all tests pass, 85%+ coverage
2. `uv run pytest -m live` — live E2E on current platform (optional, local only)
3. `uv run mypy archon/` — no type errors
