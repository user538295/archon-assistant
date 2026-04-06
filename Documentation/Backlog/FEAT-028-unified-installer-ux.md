# FEAT-028 — Unified Installer UX
**Purpose**: Consistent, accurate output across all installer paths
**Audience**: Developer / power user running Archon for the first time or enabling optional features
**Status**: To Do

---

## Background
When a user installs voice or search features, the messages are confusing, inconsistent, and sometimes contradictory — leaving them unable to answer basic questions like "did the 2GB download actually happen?". The fix is mechanical and low-risk: extract the shared `Console` class into `archon/cli/console.py`, migrate both installers, and fix the specific message defects identified in the brief.

## Goal
Every installer interaction — from the root `install.py` through `archon voice install` and `archon search install` — uses the same visual style, states facts clearly, and never contradicts itself. After this work, a user watching the output can tell exactly what was downloaded, what was skipped, and what to do next.

---

## Scope

### In Scope
- Extract `Console` class from `install.py` into `archon/cli/console.py`
- Migrate `VoiceInstaller` from raw `print()` to `Console`
- Migrate `SearchInstaller` from raw `print()` to `Console`
- Add `check_torch()` to `VoiceInstaller`; use it for accurate PyTorch pre-install message
- Remove the hardcoded header string from `VoiceInstaller.run()`
- Suppress VoiceInstaller's "Enable with:" hint when `non_interactive=True`
- VoiceInstaller enable hint text: `archon voice enable` (replaces current `archon config set voice.enabled true`)
- SearchInstaller `run()` final success message uses Console; no enable hint (service is already running after install)
- Fix jargon: "STT model" → "Speech-to-text model (Whisper)"; ffmpeg step gains "(needed for audio decoding)"
- Fix `_offer_voice_setup` in root `install.py`: remove redundant pre-install message; fix success wording
- Fix `_offer_search_setup` in root `install.py`: remove redundant pre-install message (SearchInstaller now emits its own)

### Out of Scope
- `archon voice status`, `archon search status`, `archon voice enable/disable` output styling
- Adding output-content tests to voice/search installer test suites (no existing baseline; risk of over-specifying messages)
- Changing `Console` class behaviour in root `install.py` (it already works correctly)
- Windows-specific installer paths

---

## Acceptance criteria
- [x] `archon/cli/console.py` exists and exports a `Console` class with `info`, `success`, `warn`, `error`, `ask` methods matching the `install.py` API
- [ ] `VoiceInstaller` uses `Console` for all output; no raw `print()` remains in `run()`
- [x] `VoiceInstaller.check_torch()` returns `True` when `torch` is importable, `False` otherwise
- [ ] VoiceInstaller `run()` pre-install message reflects whether PyTorch is already present
- [ ] VoiceInstaller header line ("Voice installer — STT (Whisper) + TTS") is gone
- [ ] VoiceInstaller enable hint is printed when `non_interactive=False`, suppressed when `True`
- [ ] `SearchInstaller` uses `Console` for all output; no raw `print()` remains in `run()` / `run_uninstall()`
- [ ] SearchInstaller `run()` final success message uses Console; no enable hint is needed (service is already running after install)
- [ ] The Console migration for `run_uninstall()` does not require non_interactive gating — it has no enable hint; the final message is always shown
- [ ] All existing `tests/search/test_install.py` tests pass after migration
- [ ] Jargon fixed: "Speech-to-text model (Whisper)" used, ffmpeg line includes "(needed for audio decoding)"
- [ ] `_offer_voice_setup` in `install.py` no longer prints a redundant pre-install message
- [ ] `_offer_voice_setup` success message reads "Voice configured. Start or restart Archon: archon restart"
- [ ] All existing tests continue to pass

---

## What does NOT change
- `Console` class in root `install.py` (stays duplicated; `install.py` is a PEP 723 standalone script that cannot import from `archon/`)
- `VoiceInstaller.check_whisper()`, `check_ffmpeg()`, `check_edge_tts()`, `install_deps()`, `configure_stt_model()`, `status()` — logic unchanged
- `SearchInstaller` check methods, GPU logic, service management — logic unchanged
- `voice_cmd.py` and `search_cmd.py` dispatch logic — only the installer classes change

---

## Known limitations / accepted trade-offs
- `importlib.util.find_spec("torch")` in `check_torch()` checks the active venv; torch in a different venv/system Python appears missing — acceptable since uv always installs into the current venv
- uv may serve PyTorch from local cache even when "not installed yet" message fires — timing surprise is a uv implementation detail beyond our control
- `SearchInstaller._wait_for_service()` keeps raw `print()` calls — its progress-dot loop (`print(".", end="", flush=True)`) is incompatible with Console's line-per-call API and requires `flush=True` for real-time dots. This creates one visually inconsistent path in an otherwise Console-migrated installer; tracked as future work if Console gains a progress-line method.
- VoiceInstaller `run()` already-installed branch (whisper found): the message says 'PyTorch already present' without calling `check_torch()` — this assumes whisper-installed implies torch-installed, which is true in practice (whisper's pip install requires torch) but not strictly verified at runtime. Calling `check_torch()` in this branch too would add accuracy but is not worth the complexity for the already-happy path.

---

## Architecture

### New modules
- **`archon/cli/console.py`** — `Console` class with `quiet` flag and four output methods + `ask()`. Identical API to the one in `install.py` so both codebases can evolve the same interface from a single source.

### Modified modules
- **`archon/voice/install.py`** — `VoiceInstaller` gains `check_torch() -> bool` method and a `console: Console | None = None` constructor parameter. `run()` switches to `self._console`, drops the header print, uses `check_torch()` to vary the PyTorch line, applies jargon fixes, and conditionally shows the enable hint.
- **`archon/search/install.py`** — `SearchInstaller` gains a `console: Console | None = None` constructor parameter. `run()` and `run_uninstall()` switch to `self._console`; `run()` ends with a plain success message (no enable hint — service is already running); `run_uninstall()` final message is always shown (no non_interactive gating).
- **`install.py`** — `_offer_voice_setup()` loses one redundant `console.info()` line and has its success message corrected. `_offer_search_setup()` loses the redundant `console.info("Installing RAG dependencies (~150MB)...")` line — SearchInstaller now emits its own accurate step messages.

### Console API (`archon/cli/console.py`)
```python
class Console:
    def __init__(self, quiet: bool = False) -> None: ...
    def info(self, msg: str) -> None: ...    # cyan ▸ (suppressed when quiet)
    def success(self, msg: str) -> None: ... # green ✔ (suppressed when quiet)
    def warn(self, msg: str) -> None: ...    # yellow ⚠ to stdout (suppressed when quiet)
    def error(self, msg: str) -> None: ...   # red ✖ to stderr (never suppressed)
    def ask(self, prompt: str) -> str: ...   # returns "" when quiet
```

### VoiceInstaller constructor change
```python
def __init__(
    self,
    config_file: str | None = None,
    console: Console | None = None,
) -> None:
    self._console = console or Console()  # assign first for consistency with SearchInstaller
    self._config_file = config_file or str(Path.home() / ".archon" / "config.toml")
```

### VoiceInstaller new method
```python
def check_torch(self) -> bool:
    """Return True if torch is installed in the current Python environment."""
    import importlib.util
    return importlib.util.find_spec("torch") is not None
```
> **Note**: Use `importlib.util.find_spec` — NOT `importlib.import_module`. Importing torch triggers the full PyTorch runtime load (2-5 seconds, hundreds of MB of side effects).

### SearchInstaller constructor change
```python
def __init__(
    self,
    config_file: str | None = None,
    dry_run: bool = False,
    console: Console | None = None,
) -> None:
    self._console = console or Console()  # assign first, before load_config()
    self.config_file = config_file or str(Path.home() / ".archon" / "config.toml")
    self.dry_run = dry_run
    # load_config() follows here
    ...
```
> **`self._console` must be the first assignment in `__init__`**, before `load_config()` is called, so config load errors can be reported via Console.

---

## Tests

- **`test_console_info_prints`** (unit): `info()` writes formatted cyan line to stdout
- **`test_console_success_prints`** (unit): `success()` writes formatted green line to stdout
- **`test_console_warn_prints`** (unit): `warn()` writes formatted yellow line to stdout
- **`test_console_error_prints_to_stderr`** (unit): `error()` writes to stderr, not stdout
- **`test_console_ask_returns_input`** (unit): `ask()` returns mocked input value
- **`test_console_quiet_suppresses_info`** (unit): `quiet=True` → no stdout output from `info()`
- **`test_console_quiet_suppresses_success`** (unit): `quiet=True` → no stdout output from `success()`
- **`test_console_quiet_suppresses_warn`** (unit): `quiet=True` → no stdout output from `warn()`
- **`test_console_quiet_does_not_suppress_error`** (unit): `quiet=True` → `error()` still writes to stderr
- **`test_console_quiet_ask_returns_empty_string`** (unit): `quiet=True` → `ask()` returns `""`
- **`test_check_torch_installed`** (unit): `@patch('importlib.util.find_spec', return_value=MagicMock())` → `check_torch()` returns `True`. Patch at stdlib level (not `archon.voice.install.importlib.util.find_spec`) because the import is local to the method body, not at module level.
- **`test_check_torch_missing`** (unit): `@patch('importlib.util.find_spec', return_value=None)` → `check_torch()` returns `False`.
- **`test_voice_run_non_interactive_suppresses_enable_hint`** (unit): `run(non_interactive=True)` → enable-hint Console method never called
- **`test_voice_run_interactive_shows_enable_hint`** (unit): inject mock Console; mock `builtins.input` with `side_effect=['y', '']`; mock subprocess and check methods; call `run(non_interactive=False)`; verify mock Console's `success()` was called with text containing 'archon voice enable'
- **`test_voice_run_whisper_missing_torch_absent_shows_download_message`** (unit): verify `run()` calls Console.info() with download-needed message when torch is absent
- **`test_voice_run_whisper_missing_torch_present_shows_no_download_message`** (unit): verify `run()` calls Console.info() with no-download message when torch is already present
- **`test_search_run_final_message_uses_console`** (unit): construct with mock Console injected via constructor; call `run()` with all heavy methods mocked; verify `self._console.success.call_args_list[-1]` contains the final success message text (check last call, not any call)
- **`test_search_uninstall_uses_console`** (unit): construct with mock Console injected via constructor; call `run_uninstall()` with service methods mocked; verify `self._console.info()` or `self._console.success()` is called at least once for the final message

---

## Documentation update
- N/A — no user-facing documentation changes required for messaging fixes

---

## Task breakdown

### Phase 1 — Shared Console module
> **Releasable**: after Task 1.1 — `Console` is importable by any `archon/` module

#### Task 1.1 — Create `archon/cli/console.py` with Console class
- [x] **File**: `archon/cli/console.py`
- **Depends on**: nothing
- **Description**:
  - Copy the `Console` class verbatim from `install.py` (lines 68–93): same ANSI constants, same four output methods plus `ask()`
  - Add `from __future__ import annotations` and module docstring
  - ANSI constants (`_RESET`, `_BOLD`, `_RED`, `_GREEN`, `_YELLOW`, `_CYAN`) defined at module level (same values as `install.py`)
  - `Console(quiet: bool = False)` — constructor
  - `info(msg: str) -> None` — prints `  {_CYAN}▸{_RESET} {msg}`, suppressed when quiet
  - `success(msg: str) -> None` — prints `  {_GREEN}✔{_RESET} {msg}`, suppressed when quiet
  - `warn(msg: str) -> None` — prints `  {_YELLOW}⚠{_RESET}  {msg}`, suppressed when quiet
  - `error(msg: str) -> None` — prints to stderr: `\n  {_RED}✖ Error:{_RESET} {msg}\n`, never suppressed
  - `ask(prompt: str) -> str` — returns `input(f"  {_BOLD}?{_RESET}  {prompt} ")` or `""` when quiet
  - Do NOT modify `install.py`; its Console class remains standalone
- **Releasable**: after this task, any `archon/` module can `from archon.cli.console import Console`
- **Tests (TDD)** — `tests/cli/test_console.py`:
  - Unit: `test_console_info_prints` — capture stdout; verify non-empty output on `info("x")`
  - Unit: `test_console_success_prints` — same for `success()`
  - Unit: `test_console_warn_prints` — same for `warn()`
  - Unit: `test_console_error_prints_to_stderr` — `capsys`; verify `err` non-empty, `out` empty
  - Unit: `test_console_ask_returns_input` — patch `builtins.input`, verify return value
  - Unit: `test_console_quiet_suppresses_info` — `Console(quiet=True).info("x")`; stdout empty
  - Unit: `test_console_quiet_suppresses_success` — same for `success()`
  - Unit: `test_console_quiet_suppresses_warn` — same for `warn()`
  - Unit: `test_console_quiet_does_not_suppress_error` — `Console(quiet=True).error("x")`; stderr non-empty
  - Unit: `test_console_quiet_ask_returns_empty_string` — `Console(quiet=True).ask("?")` returns `""`
  - Checkpoint: `uv run pytest tests/cli/test_console.py -v`

---

### Phase 2 — VoiceInstaller migration
> **Releasable**: after Task 2.2 — `archon voice install` uses consistent styled output and accurate messaging

#### Task 2.1 — Add `check_torch()` to `VoiceInstaller`
- [x] **File**: `archon/voice/install.py`
- **Depends on**: nothing (pure logic addition; does not require Console yet)
- **Description**:
  - Add `check_torch(self) -> bool` method to `VoiceInstaller`
  - Implementation: use `importlib.util.find_spec("torch")`; return `True` if spec is not None, `False` otherwise. Do NOT use `importlib.import_module` — it triggers the full PyTorch runtime load (2-5 seconds, hundreds of MB).
  - Same pattern as existing `check_whisper()` / `check_edge_tts()`
  - No subprocess, no side effects
- **Releasable**: after this task, callers can query whether torch is present in the current venv
- **Tests (TDD)** — `tests/voice/test_install.py`:
  - Unit: `test_check_torch_installed` — `@patch('importlib.util.find_spec', return_value=MagicMock())` → `check_torch()` returns `True`. Patch at stdlib level (not `archon.voice.install.importlib.util.find_spec`) because the import is local to the method body, not at module level.
  - Unit: `test_check_torch_missing` — `@patch('importlib.util.find_spec', return_value=None)` → `check_torch()` returns `False`.
  - Checkpoint: `uv run pytest tests/voice/test_install.py -v -k "check_torch"`

#### Task 2.2 — Migrate `VoiceInstaller` to `Console` and fix all messaging
- [ ] **File**: `archon/voice/install.py`
- **Depends on**: Task 1.1, Task 2.1
- **Description**:
  - Add import: `from archon.cli.console import Console`
  - Add `console: Console | None = None` parameter to `__init__`; store as `self._console = console or Console()`
  - In `run()`:
    - Remove header line: `print("Voice installer — STT (Whisper) + TTS (edge-tts already installed)")` — deleted
    - `[1/3]` already-installed branch: `self._console.info("openai-whisper already installed — PyTorch already present, skipping download")`
    - `[1/3]` missing branch (torch absent): `self._console.info("Installing openai-whisper (PyTorch not installed — downloading ~2 GB; model weights download on first use)…")`
    - `[1/3]` missing branch (torch present): `self._console.info("Installing openai-whisper (PyTorch already installed — no large download needed)…")`
    - Use `self.check_torch()` before calling `install_deps()` to decide which message to print
    - `[1/3]` install failure: `self._console.error(f"Installation failed: {exc}")`; return 1
    - `[1/3]` installed: `self._console.success("openai-whisper installed.")`
    - `[2/3]` ffmpeg found: `self._console.success("ffmpeg found on PATH (needed for audio decoding).")`
    - `[2/3]` ffmpeg missing: `self._console.warn(...)` with brew/apt/windows instructions and "(needed for audio decoding)" in the first line
    - Model prompt: change label from "STT model" to "Speech-to-text model (Whisper)"
    - `[3/3]`: `self._console.success(f"Speech-to-text model set to '{model}'.")`
    - Enable hint (final line): show `self._console.success("Voice support installed. Enable with: archon voice enable")` only when `not non_interactive`; suppressed entirely when `non_interactive=True`
    - `input()` for model prompt stays as-is (interactive only, already gated on `not non_interactive`)
    - `input()` for "Proceed?" prompt stays as-is; "Installation aborted." → `self._console.warn("Installation aborted.")`
  - **Interactive prompts stay as raw `input()`**: Both `input("Proceed? [y/N]")` and `input("  Speech-to-text model [tiny/small/medium]...")` keep their raw `input()` form. `Console.ask()` is NOT used in VoiceInstaller — this preserves compatibility with existing tests that mock `builtins.input`. The `Console.ask()` method is available for future callers.
- **Releasable**: after this task, `archon voice install` uses styled Console output with accurate PyTorch messaging and correct jargon
- **Tests (TDD)** — `tests/voice/test_install.py`:
  - Unit: `test_voice_run_non_interactive_suppresses_enable_hint` — construct with `Console(quiet=False)` mock; call `run(non_interactive=True)`; verify Console's `success()` was never called with text containing "Enable with"
  - Unit: `test_voice_run_interactive_shows_enable_hint` — inject mock Console; mock `builtins.input` with `side_effect=['y', '']` (handles Proceed prompt and model prompt); mock subprocess and check methods; call `run(non_interactive=False)`; verify mock Console's `success()` was called with text containing 'archon voice enable'
  - Unit: `test_voice_run_whisper_missing_torch_absent_shows_download_message` — mock `check_whisper()` → False, `check_torch()` → False, `install_deps()` no-op, `check_ffmpeg()` → True, `configure_stt_model()` no-op; call `run(non_interactive=True)`; verify mock Console's `info()` was called with text containing '~2 GB' (the download-needed message)
  - Unit: `test_voice_run_whisper_missing_torch_present_shows_no_download_message` — mock `check_whisper()` → False, `check_torch()` → True, `install_deps()` no-op, `check_ffmpeg()` → True, `configure_stt_model()` no-op; call `run(non_interactive=True)`; verify mock Console's `info()` was called with text containing 'already installed — no large download needed'
  - Checkpoint: `uv run pytest tests/voice/test_install.py -v`

---

### Phase 3 — SearchInstaller migration
> **Releasable**: after Task 3.1 — `archon search install` and `archon search uninstall` use consistent styled output

#### Task 3.1 — Migrate `SearchInstaller` to `Console`
- [ ] **File**: `archon/search/install.py`
- **Depends on**: Task 1.1
- **Description**:
  - Add import: `from archon.cli.console import Console`
  - Add `console: Console | None = None` parameter to `__init__`; store as `self._console = console or Console()` — **this must be the first assignment in `__init__`**, before `load_config()` is called, so config load errors can be reported via Console
  - Replace all `print(...)` calls in `run()` and `run_uninstall()` with `self._console.*()` using appropriate level:
    - Warning text → `self._console.warn(...)`
    - Error text → `self._console.error(...)`
  - **Important**: The "Search service did not become ready within N seconds." message (currently a `print()`) must use `self._console.warn()`, NOT `self._console.error()`. Reason: `error()` writes to stderr, which would break `test_run_prints_error_message_when_service_not_ready` that asserts on `captured.out`. The service readiness timeout is a soft failure (install partially succeeded), making `warn()` semantically appropriate.
  - **Keep "Warning:" in message text**: For messages that currently read `print("[N/N] Warning: ...")`, keep the "Warning:" prefix inside the message string passed to `self._console.warn()`. Example: `self._console.warn("[2/5] Warning: CoreML validation failed — falling back to CPU. macOS 12+ required.")`. This preserves the two test assertions: `"Warning: CoreML validation failed" in captured.out`.
    - Step confirmations (packages installed, dir created, etc.) → `self._console.success(...)`
    - Step in-progress lines (installing, creating, starting) → `self._console.info(...)`
  - `_wait_for_service()`: `print("Waiting for search service", ...)` / dots / `"ready."` / `"timed out."` stay as raw `print()` — these are progress indicators on a single line, incompatible with the Console API; no change needed
  - End of `run()`: the final message is simply `self._console.success("Search service installed and running.")` — always shown, no enable hint, no non_interactive suppression. The service is already started by `run()` (it calls `load_service()` and waits for readiness); there is nothing more to "enable".
  - `run_uninstall()` final message: always shown via `self._console.info(...)` — no enable hint, no non_interactive gating.
  - **Interactive prompts stay as raw `input()`**: `input("Proceed? [y/N]")` keeps its raw `input()` form. `Console.ask()` is NOT used in SearchInstaller — this preserves compatibility with existing tests that mock `builtins.input`.
  - **Test migration required**: `_make_installer()` uses `SearchInstaller.__new__()` to bypass `__init__()`, so `_console` is never set. Every call to `run()` or `run_uninstall()` will crash with `AttributeError` unless `_console` is patched in.

    Fix strategy:
    1. Add `installer._console = Console()` (NOT `MagicMock()`) to `_make_installer()` — this lets all 17 existing tests continue writing to real stdout/stderr, so `capsys` still captures output.
    2. The 14 `run()`-level `capsys` tests (in `TestInstallerRun` and related classes) assert on strings like `"[1/5]"`. The existing `in`-based substring assertions (e.g., `'[1/5]' in captured.out`) will pass WITHOUT modification — ANSI color codes wrap only the prefix icon, not the message text. No ANSI stripping is needed. The two real breakage risks are: (a) any message routed to `error()` writes to stderr — assert on `captured.err` for those; (b) any 'Warning:' prefix that gets dropped when migrating to `warn()` — keep the prefix in the message string.
    3. The 3 `_wait_for_service()` capsys tests are unaffected — that path keeps raw `print()`.
    4. New tests (`test_search_run_final_message_uses_console`, `test_search_uninstall_uses_console`) should construct with an explicit `MagicMock()` console passed to the constructor.
- **Releasable**: after this task, `archon search install` / `archon search uninstall` use styled Console output
- **Tests (TDD)** — `tests/search/test_install.py`:
  - Unit: `test_search_run_final_message_uses_console` — inject mock Console via constructor; call `run()` with all heavy methods mocked; verify `mock_console.success.call_args_list[-1]` (the last `success()` call) contains the final success message — checking the last call distinguishes it from step-confirmation calls earlier in the flow
  - Unit: `test_search_uninstall_uses_console` — construct with mock Console injected via constructor; call `run_uninstall()` with service methods mocked; verify `self._console.info()` or `self._console.success()` is called at least once for the final message
  - Checkpoint: `uv run pytest tests/search/test_install.py -v`

---

### Phase 4 — Fix `_offer_voice_setup` in root `install.py`
> **Releasable**: after Task 4.1 — root installer no longer shows redundant pre-install message or incorrect success wording

#### Task 4.1 — Fix `_offer_voice_setup` and `_offer_search_setup` messaging in `install.py`
- [ ] **File**: `install.py`
- **Depends on**: Task 2.2 (VoiceInstaller now prints its own pre-install message), Task 3.1 (SearchInstaller now prints its own step messages)
- **Description**:
  - Remove the line `console.info("Installing voice dependencies (requires PyTorch ~2GB; model weights download on first use)…")` from `_offer_voice_setup()` — VoiceInstaller's `run()` now emits an accurate pre-install message itself; the outer message is redundant and incorrect (it always claims "~2GB" regardless of torch state)
  - Change `console.success("Voice enabled. Run: archon restart to apply.")` → `console.success("Voice configured. Start or restart Archon: archon restart")`
  - Also in `_offer_search_setup()`: remove the `console.info("Installing RAG dependencies (~150MB)...")` line — SearchInstaller now emits its own accurate step messages; the outer message is redundant
  - No changes to `Console` class, no changes to function signatures, no other logic changes
  - `install.py`'s Console class remains duplicated (PEP 723 constraint); do NOT import from `archon/`
- **Releasable**: after this task, the root installer's voice and search setup flows are consistent and non-contradictory
- **Tests (TDD)** — **No new tests required** — the changes remove lines and fix a string literal. Existing `install.py` tests cover call flow. **Regression risk**: if any existing test asserts on the INFO message text or INFO call count in `_offer_voice_setup`/`_offer_search_setup`, it must be updated. Check with: `uv run pytest tests/ -k 'offer_voice or offer_search or voice_setup or prompts_for_rag or confirms_rag or declines_rag' -v` (note: exact test names in `TestOfferSearchSetup` class use patterns like `rag` and `search_setup`, not just `offer_search`). Run the full test class directly for completeness: `uv run pytest tests/test_installer_py.py::TestOfferSearchSetup -v`
