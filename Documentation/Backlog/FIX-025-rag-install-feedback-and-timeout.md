# FIX-025 — RAG install: silent progress and 30-second timeout
**Purpose**: Fix two bugs in `archon rag install`: silent output after `uv pip install` ends, and a race condition that causes "RAG service did not become ready within 30 seconds."
**Audience**: End users who run `archon rag install`.
**Status**: To Do

---

## Background

Two linked problems are reported:

**Problem 1 — Silent cursor.** After `uv pip install` output ends, the terminal freezes with a blinking cursor for minutes with no feedback. The silent gap is caused by `validate_providers()` which downloads ~150 MB of fastembed model data and runs a test embedding — all without any output. Subsequently `_bootstrap_collections()` and `_wait_for_service()` also produce no output.

**Problem 2 — "RAG service did not become ready within 30 seconds."** The root cause is a race condition between two independent 30-second timers:

```
server.py startup sequence:
  1. load_config / create_pipeline / LanceDB connect   ~3-5 s
  2. wait_for(sync.sync(collections), timeout=30)      ← blocks HTTP startup for up to 30 s
  3. create_app()
  4. app.run_http_async()                              ← /health only available HERE

install.py polling loop:
  _wait_for_service(timeout=30)                        ← gives up after 30 s
```

`server.py` blocks HTTP startup on collection sync (default `sync_timeout_seconds=30`). Combined with Python startup overhead (~3-5 s), `_wait_for_service` times out before the HTTP server ever binds. The existing `sync_timeout_seconds=0` code path in `server.py` already defers sync to `asyncio.create_task` and starts HTTP immediately — only the **default** needs to change.

---

## Goal

After this fix: `archon rag install` shows a numbered step label before every major operation (including a clear message before the model download), displays progress dots during the service readiness poll, and always completes successfully on first install instead of reporting a false timeout error.

---

## Scope

### In Scope
- Change `sync_timeout_seconds` default from `30` to `0` in `archon/config/loader.py`
- Add numbered step labels (`[1/5] … [5/5]`) and status messages to `run()` in `archon/rag/install.py`
- Add progress dots in `_wait_for_service()` and increase its default timeout from 30 s to 60 s
- Update `tests/config/test_rag_config.py` default assertion
- Update `examples/config.toml.example`, `Documentation/UserManual/rag_guide.md`, and `Documentation/Architecture/180_rag_architecture.md`

### Out of Scope
- Changing `server.py` logic (the `sync_timeout_seconds=0` path already works)
- Adding rich/tqdm/third-party progress libraries
- Changing sync behaviour at runtime (users who explicitly set `sync_timeout_seconds > 0` keep that behaviour)

---

## Acceptance criteria
- [ ] `archon rag install` prints a status line before `validate_providers()` (the slow model-download step)
- [ ] `archon rag install` shows numbered step labels `[1/5]` through `[5/5]` before each major operation
- [ ] `_wait_for_service()` prints a dot every second and ` ready.` or ` timed out.` at the end (leading space separates the dots from the status word)
- [ ] `_wait_for_service()` default timeout is 60 s (up from 30 s)
- [ ] `RagConfig.sync_timeout_seconds` default is `0`
- [ ] On a clean first install (no pre-existing session files), the service becomes ready without a timeout error
- [ ] All existing tests pass

---

## What does NOT change
- `server.py` logic — no changes needed
- `tests/rag/test_server.py` — tests explicitly set `sync_timeout_seconds=5`; they are unaffected by the default change
- `RagInstaller.run_uninstall()` — no progress changes needed
- The format of `uv pip install` subprocess output (printed directly to terminal, not captured)

---

## Known limitations / accepted trade-offs
- Users who relied on blocking startup sync (explicit `sync_timeout_seconds > 0`) keep that behaviour unchanged; the default change only affects fresh installs
- Progress dots in `_wait_for_service()` are not printed in `dry_run` mode (function returns immediately before the loop)
- The default change in `loader.py` (Task 1.1) only takes effect when `config.toml` does not contain an explicit `sync_timeout_seconds` value. The `config.toml.example` update (Task 3.1) must ship in the same release as Task 1.1 for fresh installs to benefit. Existing users with `sync_timeout_seconds = 30` baked into their config must either remove the line or set it to `0` manually.

---

## Architecture

No new modules. All changes are within `archon/rag/install.py` and `archon/config/loader.py`.

**`archon/config/loader.py`**
- `RagConfig.sync_timeout_seconds: int` — default changes from `30` to `0`
- No other changes to class or loader

**`archon/rag/install.py`**
- `_WAIT_FOR_SERVICE_TIMEOUT = 60` — module-level constant in `install.py` (replaces the inline default of `30`)
- `_wait_for_service(self, timeout: int = _WAIT_FOR_SERVICE_TIMEOUT) -> bool` — default arg changes from `30` to `60` via the constant; body adds `print("Waiting for RAG service", end="", flush=True)` before loop (no trailing space — the leading space on `" ready."` / `" timed out."` provides the separator), `print(".", end="", flush=True)` each iteration, `print(" ready.")` / `print(" timed out.")` on exit; wraps the loop in `try`/`except KeyboardInterrupt` — on interrupt calls `print()` (bare newline) and re-raises, ensuring the line is terminated on Ctrl+C without adding an extra newline on normal success/timeout exit
- `run(self, non_interactive: bool = False) -> int` — wraps each major step with a `print(f"[N/5] ...")` prefix line; adds explicit "Validating GPU acceleration (first run downloads ~150 MB model data) ..." before `validate_providers()`

**Step labels in `run()`:**

| Step | Operation |
|------|-----------|
| `[1/5]` | Dependency install (or "All packages already installed") |
| `[2/5]` | GPU acceleration validation / provider configuration |
| `[3/5]` | Data directory creation |
| `[4/5]` | Collection bootstrap |
| `[5/5]` | Service registration and start |

---

## Tests

- **`test_rag_config_default_sync_timeout_is_zero`** (unit): `RagConfig()` default `sync_timeout_seconds` is `0`
- **`test_run_prints_step_labels`** (unit): `run()` stdout contains `[1/5]` through `[5/5]`
- **`test_run_prints_validating_message_for_apple_silicon`** (unit): stdout contains `[2/5] Validating GPU acceleration` when GPU is apple_silicon
- **`test_run_prints_providers_configured_for_non_apple_silicon`** (unit): stdout contains `[2/5]` when GPU is `none`
- **`test_run_prints_packages_already_installed_when_no_missing`** (unit): stdout contains `already installed` when `check_deps()` returns `[]`
- **`test_run_prints_installing_packages_when_missing`** (unit): stdout contains `[1/5] Installing` when `check_deps()` returns non-empty list
- **`test_wait_for_service_prints_dots_then_ready`** (unit): `_is_service_running` returns False×2 then True; stdout contains `..` and ` ready.`
- **`test_wait_for_service_default_timeout_is_60`** (unit): `_wait_for_service.__defaults__` is `(60,)` OR assert `_WAIT_FOR_SERVICE_TIMEOUT == 60`
- **`test_wait_for_service_prints_timed_out_on_timeout`** (unit): mock `_is_service_running` always `False`; mock `time.sleep`; provide `time.monotonic` `side_effect=[0.0, 10.0, 30.0, 70.0]` (first value sets baseline; values 2-3 are < 60; value 4 exceeds deadline); capture stdout; assert `timed out.` appears
- **`test_run_returns_error_code_when_service_not_ready`** (unit): mock `_wait_for_service` to return `False`; assert `run()` returns `1`
- **`test_run_prints_error_message_when_service_not_ready`** (unit): mock `_wait_for_service` to return `False`; capture stdout via `capsys`; assert `f"RAG service did not become ready within {_WAIT_FOR_SERVICE_TIMEOUT} seconds."` (i.e. `"within 60 seconds"`) appears in stdout
- **`test_run_calls_wait_for_service_without_explicit_timeout`** (unit): mock `_wait_for_service`; assert `mock_wait_for_service.call_args == call()` (no arguments — uses default)
- **`test_run_returns_error_code_when_load_service_fails`** (unit): mock all side-effectful methods (`detect_gpu`, `_is_service_running`, `check_deps`, `configure_providers`, `write_service_file`, `load_service`, `_bootstrap_collections`); mock `load_service()` to return `2`; assert `run()` returns `2`; also assert `_wait_for_service` was not called (mock it and use `assert_not_called()`)
- **`test_run_prints_packages_installed_confirmation_when_missing`** (unit): `check_deps()` returns `["lancedb"]`, mock `install_deps()`; assert `[1/5] Packages installed.` appears in stdout
- **`test_dry_run_does_not_print_wait_for_service_output`** (unit): create `RagInstaller(dry_run=True)`; mock all side-effectful methods (`detect_gpu`, `_is_service_running`, `check_deps`, `configure_providers`, `write_service_file`, `load_service`, `_bootstrap_collections`) before calling `run()`; assert `"Waiting for RAG service"` does NOT appear in stdout
- **`test_wait_for_service_keyboard_interrupt_prints_newline`** (unit): mock `_is_service_running` to raise `KeyboardInterrupt`; capture stdout; assert `KeyboardInterrupt` propagates (use `pytest.raises`); assert stdout contains a newline after `"Waiting for RAG service"`

---

## Documentation update
- [ ] `examples/config.toml.example`, section `[rag]`, path: `examples/config.toml.example` — update `sync_timeout_seconds` from `30` to `0`
- [ ] `Documentation/UserManual/rag_guide.md`, section "Configuration reference" + "Startup sync behaviour", path: `Documentation/UserManual/rag_guide.md` — update default from `30` to `0`; update prose to say `0` is the recommended default
- [ ] `Documentation/Architecture/180_rag_architecture.md` — update `sync_timeout_seconds: int = 30` to `= 0` in the RagConfig dataclass listing; update `_wait_for_service(timeout=30)` to `timeout=60`

---

## Task breakdown

### Phase 1 — Root-cause fix: default sync_timeout_seconds = 0
> **Releasable**: after Task 1.1 — no more 30-second timeout on fresh installs

#### Task 1.1 — Change `sync_timeout_seconds` default to 0
- [x] **File**: `archon/config/loader.py`
- **Depends on**: nothing
- **Note**: Task 3.1 MUST be released in the same version as this task. The default change only takes effect when `config.toml` has no explicit `sync_timeout_seconds` value; the `config.toml.example` update in Task 3.1 ensures fresh installs pick up `0`. See "Known limitations" section.
- **Description**:
  - `RagConfig.sync_timeout_seconds: int = 0` (was `30`)
  - No other changes to the dataclass or loader
  - Effect: `server.py` now always defers startup sync to `asyncio.create_task`, starts HTTP immediately (existing `if sync_timeout == 0:` branch in `server.py:main()`)
- **Releasable**: after this task, fresh `archon rag install` no longer times out waiting for HTTP
- **Tests (TDD)** — `tests/config/test_rag_config.py`:
  - Unit: `test_rag_config_default_sync_timeout_is_zero` — `RagConfig().sync_timeout_seconds == 0`; **update** existing `test_rag_config_default_sync_timeout` assertion from `30` to `0`
  - Checkpoint: `uv run pytest tests/config/test_rag_config.py --no-cov -v`

---

### Phase 2 — Progress feedback
> **Releasable**: after Task 2.1 — user sees clear step-by-step output throughout install

#### Task 2.1 — Add step labels and status messages to `run()`
- [x] **File**: `archon/rag/install.py`
- **Depends on**: nothing (independent of Task 1.1)
- **Description**:
  - Replace bare `print("Installing missing packages: ...")` with `print(f"[1/5] Installing packages: {', '.join(missing)} ...")`; after `install_deps()` returns, print `[1/5] Packages installed.`
  - When `check_deps()` returns `[]`, print `[1/5] All packages already installed.`
  - Before `validate_providers()` (apple_silicon branch), print `[2/5] Validating GPU acceleration (first run downloads ~150 MB model data) ...`
  - After `validate_providers()` succeeds, print `[2/5] CoreML acceleration validated — GPU/Neural Engine active.`
  - After `validate_providers()` fails, print `[2/5] Warning: CoreML validation failed — falling back to CPU. macOS 12+ required.`
  - For non-apple_silicon, before `configure_providers()`, print `[2/5] Configuring providers for {gpu} ...`; after `configure_providers()`, print `[2/5] Providers configured for {gpu}.`
  - Before `create_data_dir()`, print `[3/5] Creating data directory ...`
  - Before `_bootstrap_collections()`, print `[4/5] Bootstrapping collections ...`; after, print `[4/5] Collections ready.`
  - Before `write_service_file()` + `load_service()`, print `[5/5] Starting RAG service ...`
  - No change to the final success/error messages; no new imports required
- **Releasable**: after this task, all major steps emit output before executing
- **Tests (TDD)** — `tests/rag/test_install.py`:
  - Unit: `test_run_prints_step_labels` — mock all side-effectful methods + `_wait_for_service`; capture stdout via `capsys`; assert all of `[1/5]`, `[2/5]`, `[3/5]`, `[4/5]`, `[5/5]` appear in output
  - Unit: `test_run_prints_validating_message_for_apple_silicon` — GPU=`apple_silicon`, assert `[2/5] Validating GPU acceleration` in stdout
  - Unit: `test_run_prints_providers_configured_for_non_apple_silicon` — GPU=`none`, assert `[2/5] Providers configured for none` in stdout
  - Unit: `test_run_prints_packages_already_installed_when_no_missing` — `check_deps()` returns `[]`, assert `already installed` in stdout
  - Unit: `test_run_prints_installing_packages_when_missing` — `check_deps()` returns `["lancedb"]`, assert `[1/5] Installing packages: lancedb` in stdout
  - Unit: `test_run_prints_packages_installed_confirmation_when_missing` — `check_deps()` returns `["lancedb"]`, mock `install_deps()`; assert `[1/5] Packages installed.` appears in stdout
  - Checkpoint: `uv run pytest tests/rag/test_install.py --no-cov -v -k "test_run_prints"`

#### Task 2.2 — Add progress dots to `_wait_for_service()`
- [x] **File**: `archon/rag/install.py`
- **Depends on**: nothing (independent of Tasks 1.1 and 2.1)
- **Description**:
  - Add module-level constant: `_WAIT_FOR_SERVICE_TIMEOUT = 60` (replaces the old inline `30` default)
  - Change signature: `def _wait_for_service(self, timeout: int = _WAIT_FOR_SERVICE_TIMEOUT) -> bool:`
  - Before the loop: `print("Waiting for RAG service", end="", flush=True)`
  - Wrap the dot-printing loop in `try`/`except KeyboardInterrupt`: if `KeyboardInterrupt` is raised, call `print()` (bare newline) and re-raise. This ensures the line is terminated on Ctrl+C without adding an extra newline on normal success/timeout exit.
  - Each failed probe iteration: `print(".", end="", flush=True)` (before `time.sleep(1)`)
  - On success: `print(" ready.")`; return `True`
  - After loop exhausted: `print(" timed out.")`; return `False`

  Example structure:
  ```python
  print("Waiting for RAG service", end="", flush=True)
  try:
      while time.monotonic() < deadline:
          if self._is_service_running():
              print(" ready.")
              return True
          print(".", end="", flush=True)
          time.sleep(1)
      print(" timed out.")
      return False
  except KeyboardInterrupt:
      print()
      raise
  ```

  - Update the error message at the call-site (currently reads "RAG service did not become ready within 30 seconds") to: `f"RAG service did not become ready within {_WAIT_FOR_SERVICE_TIMEOUT} seconds."` (uses the module-level constant, not a local variable)
  - In `run()`: remove the explicit `timeout=30` argument from the `_wait_for_service(timeout=30)` call, making it `self._wait_for_service()` (so it uses the new default of 60)
- **Releasable**: after this task, user sees real-time progress during the service readiness poll
- **Tests (TDD)** — `tests/rag/test_install.py`:
  - Unit: `test_wait_for_service_default_timeout_is_60` — inspect `RagInstaller._wait_for_service.__defaults__` == `(60,)` OR assert `_WAIT_FOR_SERVICE_TIMEOUT == 60`
  - Unit: `test_wait_for_service_prints_dots_then_ready` — mock `_is_service_running` side_effect `[False, False, True]`; mock `time.sleep`; capture stdout; assert `..` and ` ready.` appear
  - Unit: `test_wait_for_service_prints_timed_out_on_timeout` — mock `_is_service_running` always `False`; mock `time.sleep` (to prevent actual sleeping); provide `time.monotonic` `side_effect=[0.0, 10.0, 30.0, 70.0]` (first value sets baseline; values 2-3 are < 60; value 4 exceeds deadline); capture stdout; assert `timed out.` appears
  - Unit: `test_run_returns_error_code_when_service_not_ready` — mock `_wait_for_service` to return `False`; assert `run()` returns `1`
  - Unit: `test_run_prints_error_message_when_service_not_ready` — mock `_wait_for_service` to return `False`; capture stdout via `capsys`; assert `f"RAG service did not become ready within {_WAIT_FOR_SERVICE_TIMEOUT} seconds."` (i.e. `"within 60 seconds"`) appears in stdout
  - Unit: `test_run_calls_wait_for_service_without_explicit_timeout` — mock `_wait_for_service`; assert `mock_wait_for_service.call_args == call()` (no arguments — uses default)
  - Unit: `test_run_returns_error_code_when_load_service_fails` — mock all side-effectful methods (`detect_gpu`, `_is_service_running`, `check_deps`, `configure_providers`, `write_service_file`, `load_service`, `_bootstrap_collections`) before calling `run()`; mock `load_service()` to return `2`; assert `run()` returns `2`; assert `_wait_for_service` was not called (`assert_not_called()`)
  - Unit: `test_dry_run_does_not_print_wait_for_service_output` — create `RagInstaller(dry_run=True)`; mock all side-effectful methods (`detect_gpu`, `_is_service_running`, `check_deps`, `configure_providers`, `write_service_file`, `load_service`, `_bootstrap_collections`) before calling `run()`; assert `"Waiting for RAG service"` does NOT appear in stdout
  - Unit: `test_wait_for_service_keyboard_interrupt_prints_newline` — mock `_is_service_running` to raise `KeyboardInterrupt`; capture stdout; use `pytest.raises(KeyboardInterrupt)`; assert stdout contains a newline after `"Waiting for RAG service"`
  - Checkpoint: `uv run pytest tests/rag/test_install.py --no-cov -v -k "test_wait_for_service"`

---

### Phase 3 — Documentation
> **Releasable**: after Task 3.1

#### Task 3.1 — Update docs and config example
- [ ] **File**: `examples/config.toml.example`, `Documentation/UserManual/rag_guide.md`, `Documentation/Architecture/180_rag_architecture.md`
- **Depends on**: nothing (must ship in the same release as Task 1.1 — see "Known limitations")
- **Description**:
  - `examples/config.toml.example` line 282: change `sync_timeout_seconds = 30` → `sync_timeout_seconds = 0`; update inline comment to: `# 0 = defer sync to background, HTTP starts immediately (recommended)`
  - `Documentation/UserManual/rag_guide.md` configuration table: update default column from `30` to `0`
  - `Documentation/UserManual/rag_guide.md` "Startup sync behaviour" section: update prose — change "default 30" to "default 0" and note that `0` is recommended for reliable install; note that setting a positive value blocks HTTP startup for that many seconds before responding to health checks
  - `Documentation/Architecture/180_rag_architecture.md`: update the `RagConfig` dataclass listing — change `sync_timeout_seconds: int = 30` to `= 0`; update any reference to `_wait_for_service(timeout=30)` to reflect `timeout=60`
- **Releasable**: after this task, docs match implementation
- **Tests (TDD)**: N/A — documentation only
  - Checkpoint: N/A; verify manually with `grep sync_timeout_seconds examples/config.toml.example Documentation/UserManual/rag_guide.md Documentation/Architecture/180_rag_architecture.md`
