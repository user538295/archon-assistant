# FEAT-025 — Voice Install Support
**Purpose**: Add first-class installation, configuration, and management of voice features (STT via Whisper + TTS via edge-tts) so users are no longer silently missing prerequisites and the startup ffmpeg warning is eliminated.
**Audience**: End users who want Telegram voice messages transcribed (STT) and/or spoken TTS replies.
**Status**: To Do

---

## Background

Archon logs `WARNING ffmpeg not found on PATH; Whisper requires ffmpeg for audio decoding` on every
startup because `archon/ai/stt.py` checks for `ffmpeg` at import time, but the installer never
sets up the prerequisites. Voice features (`[voice] enabled = false` by default) require:

- **openai-whisper** (Python package) — not in base deps
- **ffmpeg** (system binary) — must be installed via OS package manager

`edge-tts` is already in base deps and works out of the box; only STT is missing from a fresh
install. The installer already has an identical pattern for RAG (`_offer_rag_setup`,
`archon rag install`, `archon/rag/install.py`). This feature replicates that pattern for voice.

---

## Goal

After this feature, `uv run install.py` asks "Enable voice features (STT/TTS)?" after the RAG
prompt. If yes, it installs `openai-whisper`, checks for `ffmpeg` (printing install instructions if
missing), configures the STT model, sets `voice.enabled = true`, and restarts Archon. Users can
also manage voice at any time via `archon voice install|status|enable|disable` and three new MCP
toolkit tools (`voice_status`, `voice_enable`, `voice_disable`). The startup ffmpeg warning is
eliminated for users who complete setup.

---

## Scope

### In Scope
- `archon/voice/install.py` — `VoiceInstaller` class
- `archon/cli/voice_cmd.py` — `run_voice()` CLI dispatcher
- `archon/cli/main.py` — register `archon voice` subparser
- `archon/ai/archon_toolkit_voice.py` — three MCP tools
- `archon/ai/archon_toolkit.py` — register voice tools
- `install.py` — `_voice_already_enabled()` + `_offer_voice_setup()` post-install prompt
- `pyproject.toml` — `voice = ["openai-whisper"]` optional-dependency group + mypy override

### Out of Scope
- Auto-installing `ffmpeg` — requires OS package manager, too risky; only detect and guide
- TTS provider selection during install — edge-tts is default; user can change via `archon config set voice.tts.provider openai`
- `archon voice test` live smoke command
- Modifying `archon/ai/stt.py` or `archon/ai/tts.py` — voice runtime handlers unchanged
- Windows service support — voice is in-process, no background service needed

---

## Acceptance criteria
- [ ] `uv run install.py` prompts "Enable voice features (STT/TTS)? [y/N]" after the RAG prompt
- [ ] `archon voice install` installs `openai-whisper` and prints numbered progress `[1/3]…[3/3]`
- [ ] `archon voice install` prints ffmpeg install instructions (brew/apt/URL) if missing, exits 0
- [ ] `archon voice install --non-interactive` uses model=medium with no prompts
- [ ] `archon voice status` prints whisper, edge-tts, and ffmpeg availability
- [ ] `archon voice enable` / `disable` sets `voice.enabled` in config.toml via tomlkit
- [ ] MCP tool `voice_status` returns JSON `{enabled, whisper_installed, ffmpeg_found, edge_tts_installed}`
- [ ] MCP tools `voice_enable` / `voice_disable` set `voice.enabled` and return a success string
- [ ] `_offer_voice_setup` prints restart instructions after enabling (does NOT call `archon restart` — RAG already does)
- [ ] `--update` skips the voice prompt if `voice.enabled = true` already in config
- [ ] All new and existing tests pass

---

## What does NOT change
- `archon/ai/stt.py` and `archon/ai/tts.py` — runtime voice handlers
- `archon/config/loader.py` — `VoiceConfig` dataclass
- `archon rag` commands and `RagInstaller`
- `archon config set voice.enabled true` (existing mechanism continues to work)
- `pyproject.toml` base `dependencies` list — `edge-tts` stays, nothing removed

---

## Known limitations / accepted trade-offs
- ffmpeg cannot be auto-installed cross-platform without risky sudo/brew calls; users see instructions and install manually.
- `openai-whisper` requires PyTorch (~2GB+ download on a fresh install); Whisper model weights download on **first use** (not during `archon voice install`). Users are warned during install with: "Installing openai-whisper (requires PyTorch ~2GB; model weights download on first use)…"
- `VoiceInstaller.run()` uses `input()` directly (not `Console.ask()`) — consistent with `RagInstaller.run()` which is a runtime module, not install-time.
- No `archon voice start/stop` — voice is in-process, not a background service.

---

## Architecture

### New modules
- **`archon/voice/__init__.py`** — empty package marker
- **`archon/voice/install.py`** — `VoiceInstaller` class (checks, install, configure, status, run)
- **`archon/cli/voice_cmd.py`** — CLI dispatcher `run_voice(args, voice_parser) -> int`
- **`archon/ai/archon_toolkit_voice.py`** — `_register_voice_tools(toolkit: ArchonToolkit) -> None`

### Modified files
- **`archon/cli/main.py`** — `p_voice` subparser + dispatch block (after RAG block, line ~79)
- **`archon/ai/archon_toolkit.py`** — one-line `_register_voice_tools(self)` call after RAG registration
- **`install.py`** — `_voice_already_enabled()` + `_offer_voice_setup()` called after `_offer_rag_setup`
- **`pyproject.toml`** — `voice` optional-dep group + mypy `whisper.*` override

### Key interfaces

```python
# archon/voice/install.py
class VoiceInstaller:
    def __init__(self, config_file: str | None = None) -> None
        # config_file defaults to str(Path.home() / ".archon" / "config.toml")

    def check_whisper(self) -> bool         # importlib.import_module("whisper") + hasattr(module, "load_model"); False on ImportError or missing attribute
    def check_ffmpeg(self) -> bool          # shutil.which("ffmpeg") is not None
    def check_edge_tts(self) -> bool        # importlib.import_module("edge_tts"); False on ImportError
    def status(self) -> dict[str, bool]     # {"whisper_installed", "ffmpeg_found", "edge_tts_installed"}

    def install_deps(self) -> None
        # subprocess.run(["uv","pip","install","--python",sys.executable,"openai-whisper"], check=True)
        # propagates CalledProcessError to caller

    def configure_stt_model(self, model: str) -> None
        # tomlkit parse → set voice.stt.model → write back (UTF-8)
        # creates [voice] and [voice.stt] sections if absent
        # logs warning and returns (no raise) if config file missing

    def run(self, non_interactive: bool = False) -> int
        # returns 0 on success, 1 on abort or install failure
        # 3 phases with numbered output; ffmpeg missing = warning, not failure

# archon/cli/voice_cmd.py
def run_voice(args: argparse.Namespace,
              voice_parser: argparse.ArgumentParser | None = None) -> int

# archon/ai/archon_toolkit_voice.py
def _register_voice_tools(toolkit: ArchonToolkit) -> None
```

### Config keys (existing — not new)
- `voice.enabled` (bool, default `false`) — toggled by enable/disable
- `voice.stt.model` (str, default `"medium"`) — written by `configure_stt_model`

### Data flow — install.py prompt
```
main()
  └─ _offer_voice_setup(paths, console, non_interactive)
       ├─ console.ask("Enable voice features? [y/N]")
       ├─ subprocess: archon voice install --non-interactive
       │    └─ VoiceInstaller.run(non_interactive=True)
       │         ├─ [1/3] install_deps()       # uv pip install openai-whisper
       │         ├─ [2/3] check_ffmpeg()        # warn + instructions if missing
       │         └─ [3/3] configure_stt_model("medium")
       ├─ subprocess: archon config set voice.enabled true
       └─ console.success("Voice enabled. Run: archon restart")
          # NOTE: NO subprocess restart — _offer_rag_setup (called just before) already
          # restarts Archon if RAG was accepted. A second restart would be a race condition.
```

---

## Tests

- **`test_check_whisper_importable`** (unit): patch `importlib.import_module` → mock with `load_model` present → True
- **`test_check_whisper_missing`** (unit): patch → ImportError → False
- **`test_check_whisper_wrong_package`** (unit): mock without `load_model` attribute → False
- **`test_check_ffmpeg_found`** (unit): patch `shutil.which` → path → True
- **`test_check_ffmpeg_missing`** (unit): patch `shutil.which` → None → False
- **`test_check_edge_tts_importable`** (unit): patch import → True
- **`test_check_edge_tts_missing`** (unit): patch import → ImportError → False
- **`test_status_all_present`** (unit): all checks True → dict all True
- **`test_status_partial`** (unit): whisper False, others True → correct dict
- **`test_install_deps_calls_uv`** (unit): assert `subprocess.run` args = `["uv","pip","install","--python",sys.executable,"openai-whisper"]`
- **`test_install_deps_propagates_failure`** (unit): `CalledProcessError` from subprocess → re-raised
- **`test_install_deps_file_not_found`** (unit): `FileNotFoundError` (uv not on PATH) → re-raised
- **`test_configure_writes_model`** (unit): tmp_path config; assert `voice.stt.model == "tiny"` after call
- **`test_configure_creates_voice_section`** (unit): config without `[voice]` → section + key created
- **`test_configure_creates_stt_subsection`** (unit): `[voice]` present, no `[voice.stt]` → created
- **`test_configure_preserves_comments`** (unit): `# my comment` survives via `set_config_value` round-trip
- **`test_configure_missing_config_logs_warning`** (unit): `set_config_value` raises `FileNotFoundError` → warning logged, no raise from `configure_stt_model`
- **`test_configure_overwrites_existing_model`** (unit): `[voice.stt]\nmodel = "tiny"` → call `configure_stt_model("medium")` → model is `"medium"`
- **`test_run_non_interactive_success`** (unit): all checks pass → rc=0, configure called with `"medium"`
- **`test_run_non_interactive_uses_medium_model`** (unit): `non_interactive=True` → configure(`"medium"`)
- **`test_run_user_declines`** (unit): mock `input` → `"n"` → rc=1, `install_deps` not called
- **`test_run_user_accepts`** (unit): mock `input` → `"y"` → install flow runs
- **`test_run_installs_when_whisper_missing`** (unit): `check_whisper=False` → `install_deps` called
- **`test_run_skips_install_when_whisper_present`** (unit): `check_whisper=True` → `install_deps` NOT called
- **`test_run_ffmpeg_missing_still_returns_zero`** (unit): `check_ffmpeg=False` → rc=0, warning printed
- **`test_run_install_deps_failure_returns_one`** (unit): `install_deps` raises `CalledProcessError` → rc=1
- **`test_run_install_deps_file_not_found_returns_one`** (unit): `install_deps` raises `FileNotFoundError` → rc=1
- **`test_run_interactive_model_tiny`** (unit): inputs `"y"` then `"tiny"` → `configure_stt_model("tiny")`
- **`test_run_interactive_model_invalid_falls_back_to_medium`** (unit): second input `"huge"` → `configure_stt_model("medium")`
- **`test_run_interactive_model_empty_falls_back_to_medium`** (unit): second input `""` → `configure_stt_model("medium")`
- **`test_voice_cmd_install_dispatches`** (unit): `voice_command="install"` → `VoiceInstaller.run` called
- **`test_voice_cmd_install_non_interactive_flag`** (unit): `args.non_interactive=True` → `run(non_interactive=True)`
- **`test_voice_cmd_status_all_present`** (unit): patch `status()` all True → "installed"/"found" in stdout
- **`test_voice_cmd_status_partial`** (unit): whisper False → "not installed" in stdout
- **`test_voice_cmd_enable_calls_set_config_value`** (unit): assert `set_config_value("voice.enabled","true",…)`
- **`test_voice_cmd_disable_calls_set_config_value`** (unit): assert `set_config_value("voice.enabled","false",…)`
- **`test_voice_cmd_enable_prints_restart_hint`** (unit): "restart" in stdout
- **`test_voice_cmd_no_subcommand_prints_usage`** (unit): `voice_command=None` → rc=0, usage in stdout
- **`test_voice_cmd_unknown_subcommand_returns_one`** (unit): `voice_command="bogus"` → rc=1
- **`test_main_voice_install_dispatches`** (unit): `argv=["voice","install"]` → `run_voice` called
- **`test_main_voice_status_dispatches`** (unit): `argv=["voice","status"]` → `run_voice` called
- **`test_main_voice_no_subcommand`** (unit): `argv=["voice"]` → rc=0
- **`test_rag_dispatch_still_works_after_voice_added`** (unit): `argv=["rag","status"]` → `run_rag` called, NOT `run_voice`
- **`test_voice_status_tool_returns_json`** (unit): patch `VoiceInstaller.status()`; assert all JSON fields correct
- **`test_voice_status_tool_enabled_flag`** (unit): `config.voice.enabled=True` → `"enabled": true` in JSON
- **`test_voice_status_tool_no_config`** (unit): `toolkit._config=None` → `"enabled": false`, no exception
- **`test_voice_enable_tool_calls_set_config_value`** (unit): assert `set_config_value("voice.enabled","true",…)`
- **`test_voice_enable_tool_returns_success_string`** (unit): return value contains "enabled"
- **`test_voice_disable_tool_calls_set_config_value`** (unit): assert `set_config_value("voice.enabled","false",…)`
- **`test_voice_disable_tool_returns_success_string`** (unit): return value contains "disabled"
- **`test_voice_tools_registered_in_toolkit`** (unit): `ArchonToolkit(config=None)`; assert 3 voice tool names in `toolkit.tool_names`
- **`test_rag_tools_still_registered_after_voice_added`** (unit): `ArchonToolkit(config=None)`; assert both `"rag_status"` and `"voice_status"` in `toolkit.tool_names`
- **`test_voice_already_enabled_true`** (unit): `voice.enabled = true` in config → True
- **`test_voice_already_enabled_false`** (unit): no `[voice]` section → False
- **`test_voice_already_enabled_missing_file`** (unit): file absent → False
- **`test_voice_already_enabled_malformed_toml`** (unit): invalid TOML → False, no raise
- **`test_offer_voice_setup_non_interactive_skips`** (unit): `non_interactive=True` → `console.ask` not called
- **`test_offer_voice_setup_user_declines`** (unit): ask → `"n"` → no `subprocess.run`
- **`test_offer_voice_setup_happy_path`** (unit): all subprocesses rc=0 → `console.success` called, NO restart subprocess
- **`test_offer_voice_setup_install_fails`** (unit): voice install rc=1 → `console.warn`, no config set
- **`test_offer_voice_setup_config_set_fails`** (unit): config set rc=1 → warn, no further subprocesses
- **`test_offer_voice_setup_eoferror`** (unit): `console.ask` raises `EOFError` → returns silently
- **`test_offer_voice_setup_keyboardinterrupt`** (unit): `console.ask` raises `KeyboardInterrupt` → returns silently, no subprocess
- **`test_offer_voice_setup_archon_bin_missing`** (unit): `archon_bin.exists()=False` → warn, no subprocess
- **`test_offer_voice_setup_oserror`** (unit): subprocess raises `OSError` → `console.warn`, no raise

---

## Documentation update
- N/A — no user-visible behaviour change beyond feature addition; `CLAUDE.md` component catalog can note `archon/voice/` in a follow-up.

---

## Task breakdown

### Phase 1 — Core installer and CLI
> **Releasable**: after Task 1.6 — `archon voice install|status|enable|disable` fully functional end-to-end

#### Task 1.1 — `pyproject.toml`: voice optional-dependency group
- [x] **File**: `pyproject.toml`
- **Depends on**: nothing
- **Description**:
  - Add under `[project.optional-dependencies]` after the existing `rag` group:
    ```toml
    voice = [
        "openai-whisper",
    ]
    ```
  - `edge-tts` is already in base `dependencies` — do NOT add it here
  - Add mypy override so `uv run mypy archon/` passes without whisper type stubs:
    ```toml
    [[tool.mypy.overrides]]
    module = ["whisper", "whisper.*"]
    ignore_missing_imports = true
    ```
- **Releasable**: after this task, `uv sync --extra voice` installs `openai-whisper`
- **Tests (TDD)**: N/A — metadata only
- Checkpoint: `uv sync --extra voice && uv run python -c "import whisper; print('ok')"`

---

#### Task 1.2 — `archon/voice/install.py`: `VoiceInstaller` checks and `status()`
- [ ] **Files**: `archon/voice/__init__.py` (empty) + `archon/voice/install.py`
- **Depends on**: nothing
- **Description**:
  - `VoiceInstaller.__init__(self, config_file: str | None = None) -> None`
    - `self._config_file = config_file or str(Path.home() / ".archon" / "config.toml")`
  - `check_whisper(self) -> bool`
    - `importlib.import_module("whisper")` → check `hasattr(module, "load_model")` → True if present; `ImportError` or missing attribute → False
    - The `load_model` attribute check guards against a `whisper` package name collision (e.g. a different `whisper` library on PATH that is not openai-whisper)
  - `check_ffmpeg(self) -> bool`
    - `shutil.which("ffmpeg") is not None`
  - `check_edge_tts(self) -> bool`
    - `importlib.import_module("edge_tts")` → True; `ImportError` → False
  - `status(self) -> dict[str, bool]`
    - Returns `{"whisper_installed": self.check_whisper(), "ffmpeg_found": self.check_ffmpeg(), "edge_tts_installed": self.check_edge_tts()}`
  - All check methods are pure — no subprocess, no side effects
- **Releasable**: after this task, `VoiceInstaller().status()` returns availability dict
- **Tests (TDD)** — `tests/voice/test_install.py`:
  - Unit: `test_check_whisper_importable` — patch `importlib.import_module` to return a mock module with `load_model` attribute present → True
  - Unit: `test_check_whisper_missing` — patch to raise `ImportError` → False
  - Unit: `test_check_whisper_wrong_package` — patch `importlib.import_module` to return a mock without `load_model` attribute → False (guards against package name collision)
  - Unit: `test_check_ffmpeg_found` — patch `shutil.which("ffmpeg")` → `"/usr/bin/ffmpeg"` → True
  - Unit: `test_check_ffmpeg_missing` — patch `shutil.which("ffmpeg")` → `None` → False
  - Unit: `test_check_edge_tts_importable` — patch import → True
  - Unit: `test_check_edge_tts_missing` — patch import → `ImportError` → False
  - Unit: `test_status_all_present` — all checks patched True → dict all True
  - Unit: `test_status_partial` — whisper False, others True → correct mixed dict
  - Checkpoint: `uv run pytest tests/voice/test_install.py -k "check or status" -v --no-cov`

---

#### Task 1.3 — `archon/voice/install.py`: `install_deps()`
- [ ] **File**: `archon/voice/install.py`
- **Depends on**: Task 1.2
- **Description**:
  - `install_deps(self) -> None`
    - `subprocess.run(["uv", "pip", "install", "--python", sys.executable, "openai-whisper"], check=True)`
    - `check=True` — `CalledProcessError` propagates to caller unchanged
    - No guard inside this method; caller (`run()`) decides whether to skip
- **Releasable**: after this task, `VoiceInstaller().install_deps()` can install openai-whisper
- **Tests (TDD)** — `tests/voice/test_install.py`:
  - Unit: `test_install_deps_calls_uv` — patch `subprocess.run`; assert called with exact args `["uv","pip","install","--python",sys.executable,"openai-whisper"]`, `check=True`
  - Unit: `test_install_deps_propagates_failure` — subprocess raises `CalledProcessError` → re-raised from `install_deps`
  - Unit: `test_install_deps_file_not_found` — `subprocess.run` raises `FileNotFoundError` (uv not on PATH) → re-raised from `install_deps`
  - Checkpoint: `uv run pytest tests/voice/test_install.py -k "install_deps" -v --no-cov`

---

#### Task 1.4 — `archon/voice/install.py`: `configure_stt_model()`
- [ ] **File**: `archon/voice/install.py`
- **Depends on**: Task 1.2
- **Description**:
  - `configure_stt_model(self, model: str) -> None` — delegates entirely to `set_config_value`:
    ```python
    def configure_stt_model(self, model: str) -> None:
        from archon.config.config_rw import set_config_value  # lazy import
        try:
            set_config_value("voice.stt.model", model, Path(self._config_file))
        except (FileNotFoundError, OSError) as exc:
            logging.getLogger("archon").warning("Config file %s not found — skipping stt model config: %s", self._config_file, exc)
    ```
  - `set_config_value` signature (verified): `set_config_value(path: str, value: str, config_file: Path) -> None`
  - `set_config_value` handles section creation, locking, atomic write, and comment preservation — no custom tomlkit logic needed here.
  - Catches `(FileNotFoundError, OSError)` so a missing config during install does not crash the flow; logs a warning instead.
  - NOTE: Do NOT import `set_config_value` at module level — lazy import inside the method keeps `archon/voice/install.py` importable even without the full Archon package installed.
- **Releasable**: after this task, `VoiceInstaller().configure_stt_model("medium")` writes config safely
- **Tests (TDD)** — `tests/voice/test_install.py`:
  - Unit: `test_configure_writes_model` — minimal config in `tmp_path`; assert `voice.stt.model == "tiny"` after `configure_stt_model("tiny")` (exercises `set_config_value` behavior)
  - Unit: `test_configure_creates_voice_section` — config without `[voice]` → section + key created; valid TOML via `tomllib.loads` (exercises `set_config_value` section-creation logic)
  - Unit: `test_configure_creates_stt_subsection` — `[voice]` present but no `[voice.stt]` → stt subsection created
  - Unit: `test_configure_preserves_comments` — config with `# top comment` above `[access]` → comment present after write; patch target is `archon.config.config_rw.set_config_value` if mocking, or use a real tmp file to test tomlkit preservation via `set_config_value`
  - Unit: `test_configure_missing_config_logs_warning` — patch `set_config_value` to raise `FileNotFoundError` → warning logged, no exception raised from `configure_stt_model`
  - Unit: `test_configure_overwrites_existing_model` — config with `[voice.stt]\nmodel = "tiny"` → call `configure_stt_model("medium")` → model is now `"medium"`, other keys preserved
  - Checkpoint: `uv run pytest tests/voice/test_install.py -k "configure" -v --no-cov`

---

#### Task 1.5 — `archon/voice/install.py`: `run()` full install flow
- [ ] **File**: `archon/voice/install.py`
- **Depends on**: Task 1.3, Task 1.4
- **Description**:
  - `run(self, non_interactive: bool = False) -> int` — returns 0 on success, 1 on abort/error
  - Full flow:
    ```
    print("Voice installer — STT (Whisper) + TTS (edge-tts already installed)")

    if not non_interactive:
        answer = input("Proceed with installation? [y/N] ").strip().lower()
        if answer != "y":
            print("Installation aborted.")
            return 1

    # [1/3] Python dependencies
    if self.check_whisper():
        print("[1/3] openai-whisper already installed — skipping.")
    else:
        print("[1/3] Installing openai-whisper (requires PyTorch ~2GB; model weights download on first use)…")
        try:
            self.install_deps()
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            print(f"[1/3] Installation failed: {exc}")
            return 1
        print("[1/3] openai-whisper installed.")

    # [2/3] ffmpeg check
    if self.check_ffmpeg():
        print("[2/3] ffmpeg found on PATH.")
    else:
        print(
            "[2/3] Warning: ffmpeg not found on PATH.\n"
            "      Whisper requires ffmpeg for audio decoding. Install it:\n"
            "        macOS:   brew install ffmpeg\n"
            "        Ubuntu:  sudo apt install ffmpeg\n"
            "        Windows: https://ffmpeg.org/download.html\n"
            "      STT will not work until ffmpeg is on PATH."
        )
        # NOT return 1 — ffmpeg missing is a warning, install continues

    # [3/3] Configure STT model
    if not non_interactive:
        print("      Model sizes (larger = more accurate, slower first-run download):")
        print("        tiny (~75 MB)   small (~466 MB)   medium (~1.5 GB)")
        model_input = input("  STT model [tiny/small/medium]: ").strip().lower()
        model = model_input if model_input in {"tiny", "small", "medium"} else "medium"
    else:
        model = "medium"
    self.configure_stt_model(model)
    print(f"[3/3] STT model set to '{model}'.")

    print("Voice support installed. Enable with: archon config set voice.enabled true")
    return 0
    ```
- **Releasable**: after this task, `VoiceInstaller().run()` and `VoiceInstaller().run(non_interactive=True)` are fully functional
- **Tests (TDD)** — `tests/voice/test_install.py`:
  - Unit: `test_run_non_interactive_success` — patch `check_whisper=True`, `check_ffmpeg=True`, `configure_stt_model`; assert rc=0
  - Unit: `test_run_non_interactive_uses_medium_model` — `non_interactive=True` → `configure_stt_model` called with `"medium"`
  - Unit: `test_run_user_declines` — mock `input` → `"n"` → rc=1; `install_deps` not called
  - Unit: `test_run_user_accepts` — mock `input` → `"y"` → install flow runs, rc=0
  - Unit: `test_run_installs_when_whisper_missing` — `check_whisper=False` → `install_deps` called
  - Unit: `test_run_skips_install_when_whisper_present` — `check_whisper=True` → `install_deps` NOT called
  - Unit: `test_run_ffmpeg_missing_still_returns_zero` — `check_ffmpeg=False` → rc=0 (warning only)
  - Unit: `test_run_install_deps_failure_returns_one` — `install_deps` raises `CalledProcessError` → rc=1
  - Unit: `test_run_install_deps_file_not_found_returns_one` — `install_deps` raises `FileNotFoundError` (uv not on PATH) → rc=1
  - Unit: `test_run_interactive_model_tiny` — mock both `input` calls: first `"y"`, second `"tiny"` → `configure_stt_model` called with `"tiny"`
  - Unit: `test_run_interactive_model_invalid_falls_back_to_medium` — second input is `"huge"` (not in valid set) → `configure_stt_model("medium")`
  - Unit: `test_run_interactive_model_empty_falls_back_to_medium` — second input is `""` → `configure_stt_model("medium")`
  - Checkpoint: `uv run pytest tests/voice/test_install.py -k "run" -v --no-cov`

---

#### Task 1.6 — `archon/cli/voice_cmd.py`: `run_voice()` dispatcher
- [ ] **File**: `archon/cli/voice_cmd.py`
- **Depends on**: Task 1.5
- **Description**:
  - Module-level: `_CONFIG_PATH = Path.home() / ".archon" / "config.toml"`
  - `run_voice(args: argparse.Namespace, voice_parser: argparse.ArgumentParser | None = None) -> int`
    - If `args.voice_command in (None, "help")`: print help via `voice_parser.print_help()` or usage string; return 0
    - Dispatch dict: `{"install": _run_install, "status": _run_status, "enable": _run_enable, "disable": _run_disable}`
    - Unknown command: print usage; return 1
  - `_run_install(args) -> int`: `VoiceInstaller().run(non_interactive=getattr(args, "non_interactive", False))`
  - `_run_status(args) -> int`:
    ```
    s = VoiceInstaller().status()
    print(f"openai-whisper : {'installed'   if s['whisper_installed'] else 'not installed'}")
    print(f"edge-tts       : {'installed'   if s['edge_tts_installed'] else 'not installed'}")
    print(f"ffmpeg         : {'found'       if s['ffmpeg_found']       else 'not found'}")
    return 0
    ```
  - `_run_enable(args) -> int`:
    ```python
    from archon.config.config_rw import set_config_value
    set_config_value("voice.enabled", "true", _CONFIG_PATH)
    print("voice.enabled = true")
    print("Run 'archon restart' to apply.")
    return 0
    ```
  - `_run_disable(args) -> int`: same but value `"false"` and message `"voice.enabled = false"`
- **Releasable**: after this task + Task 1.7, `archon voice` subcommands work end-to-end
- **Tests (TDD)** — `tests/cli/test_voice_cmd.py`:
  - Unit: `test_install_dispatches_to_voice_installer` — patch `VoiceInstaller.run`; assert called with `non_interactive=False`
  - Unit: `test_install_non_interactive_flag` — `args.non_interactive=True` → `run(non_interactive=True)`
  - Unit: `test_status_all_present` — patch `status()` all True; assert "installed" and "found" in captured stdout
  - Unit: `test_status_partial` — `whisper_installed=False` → "not installed" in stdout
  - Unit: `test_enable_calls_set_config_value` — patch `set_config_value`; assert called with `("voice.enabled","true",_CONFIG_PATH)`
  - Unit: `test_disable_calls_set_config_value` — assert called with `("voice.enabled","false",_CONFIG_PATH)`
  - Unit: `test_enable_prints_restart_hint` — "restart" in stdout
  - Unit: `test_no_subcommand_returns_zero` — `args.voice_command=None` → rc=0, usage in stdout
  - Unit: `test_unknown_subcommand_returns_one` — `args.voice_command="bogus"` → rc=1
  - Checkpoint: `uv run pytest tests/cli/test_voice_cmd.py -v --no-cov`

---

#### Task 1.7 — `archon/cli/main.py`: register `archon voice` subparser
- [ ] **File**: `archon/cli/main.py`
- **Depends on**: Task 1.6
- **Description**:
  - After the existing `rag` subparser block (line ~79), insert:
    ```python
    p_voice = sub.add_parser("voice", help="Manage voice features (STT/TTS)")
    voice_sub = p_voice.add_subparsers(dest="voice_command", metavar="<action>")
    p_voice_install = voice_sub.add_parser("install", help="Install voice dependencies (Whisper + ffmpeg check)")
    p_voice_install.add_argument("--non-interactive", action="store_true", dest="non_interactive")
    voice_sub.add_parser("status",  help="Show voice feature availability")
    voice_sub.add_parser("enable",  help="Enable voice in config (requires restart)")
    voice_sub.add_parser("disable", help="Disable voice in config (requires restart)")
    voice_sub.add_parser("help",    help="Show voice help")
    ```
  - After the `if args.command == "rag":` dispatch block, add:
    ```python
    if args.command == "voice":
        from archon.cli.voice_cmd import run_voice
        return run_voice(args, voice_parser=p_voice)
    ```
- **Releasable**: after this task, `archon voice install|status|enable|disable` work end-to-end from the shell
- **Tests (TDD)** — `tests/cli/test_main.py`:
  - Unit: `test_voice_install_dispatches` — `argv=["voice","install"]` → `run_voice` called (patched)
  - Unit: `test_voice_status_dispatches` — `argv=["voice","status"]` → `run_voice` called
  - Unit: `test_voice_no_subcommand_returns_zero` — `argv=["voice"]` → rc=0
  - Unit: `test_rag_dispatch_still_works_after_voice_added` — `argv=["rag","status"]` → `run_rag` called (patched), NOT `run_voice` (regression guard)
  - Checkpoint: `uv run pytest tests/cli/test_main.py -k "voice" -v --no-cov`

---

### Phase 2 — MCP toolkit tools
> **Releasable**: after Task 2.2 — Claude can call `voice_status`, `voice_enable`, `voice_disable` via MCP in live sessions

#### Task 2.1 — `archon/ai/archon_toolkit_voice.py`: three MCP tools
- [ ] **File**: `archon/ai/archon_toolkit_voice.py`
- **Depends on**: Task 1.5 (`VoiceInstaller`), Task 1.7 (CLI functional)
- **Description**:
  - Follow the pattern in `archon/ai/archon_toolkit_rag.py` for handler signatures and `functools.partial` wiring
  - **CRITICAL**: All imports of `VoiceInstaller`, `whisper`, and `set_config_value` MUST be lazy (inside handler functions), NOT at module level. This ensures `archon_toolkit_voice.py` is importable even when `openai-whisper` is not installed.
  - `_register_voice_tools(toolkit: ArchonToolkit) -> None` — public entry point, called from `archon_toolkit.py`
  - **`voice_status`** (no input parameters):
    - Lazy-import `VoiceInstaller`; call `.status()`
    - Read `toolkit._config.voice.enabled` — default `False` if `toolkit._config is None`
    - Return `json.dumps({"enabled": bool, "whisper_installed": bool, "ffmpeg_found": bool, "edge_tts_installed": bool})`
  - **`voice_enable`** (no input parameters):
    - Lazy-import `set_config_value` from `archon.config.config_rw`
    - Resolve config path: `Path(toolkit._config_file) if toolkit._config_file else Path.home()/".archon"/"config.toml"`
    - `await asyncio.to_thread(set_config_value, "voice.enabled", "true", config_path)`
    - Return `"Voice enabled in config. Restart Archon to apply."`
  - **`voice_disable`** (no input parameters):
    - Same as `voice_enable` but value `"false"`; return `"Voice disabled in config. Restart Archon to apply."`
  - All handlers are `async def`; use `asyncio.to_thread` for blocking `set_config_value` call
- **Releasable**: after this task, the three tools can be registered in ArchonToolkit
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_voice.py`:
  - Unit: `test_voice_status_returns_json` — patch `VoiceInstaller.status()` and mock config; assert all 4 JSON fields present with correct types
  - Unit: `test_voice_status_enabled_flag` — `toolkit._config.voice.enabled = True` → `"enabled": true` in JSON
  - Unit: `test_voice_status_no_config` — `toolkit._config = None` → `"enabled": false`, no exception
  - Unit: `test_voice_enable_calls_set_config_value` — assert `set_config_value("voice.enabled","true", config_path)` called via `to_thread`
  - Unit: `test_voice_enable_returns_success_string` — return value contains "enabled"
  - Unit: `test_voice_disable_calls_set_config_value` — assert `set_config_value("voice.enabled","false", config_path)` called
  - Unit: `test_voice_disable_returns_success_string` — return value contains "disabled"
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_voice.py -v --no-cov`

---

#### Task 2.2 — `archon/ai/archon_toolkit.py`: register voice tools
- [ ] **File**: `archon/ai/archon_toolkit.py`
- **Depends on**: Task 2.1
- **Description**:
  - Find `_register_rag_tools(self)` call in `ArchonToolkit.__init__`
  - Immediately after it, add:
    ```python
    from archon.ai.archon_toolkit_voice import _register_voice_tools
    _register_voice_tools(self)
    ```
  - No other changes to this file
- **Releasable**: after this task, Claude can invoke all three voice MCP tools in any live session
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_voice.py`:
  - Unit: `test_voice_tools_registered_in_toolkit` — construct `ArchonToolkit()` (same pattern as `tests/ai/test_archon_toolkit_rag.py` which uses `ArchonToolkit(config=None)`); assert `"voice_status"`, `"voice_enable"`, `"voice_disable"` are in `toolkit.tool_names` (a `set[str]`; the verified attribute from `archon_toolkit.py` line 607)
  - Unit: `test_rag_tools_still_registered_after_voice_added` — construct `ArchonToolkit(config=None)`; assert both `"rag_status"` and `"voice_status"` are in `toolkit.tool_names`
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_voice.py::test_voice_tools_registered_in_toolkit -v --no-cov`

---

### Phase 3 — Installer prompt
> **Releasable**: after Task 3.2 — `uv run install.py` offers voice setup as part of the post-install flow

#### Task 3.1 — `install.py`: `_voice_already_enabled()`
- [ ] **File**: `install.py`
- **Depends on**: nothing
- **Description**:
  - `_voice_already_enabled(archon_home: Path) -> bool`
    - `config_path = archon_home / "config.toml"`; if not exists → `return False`
    - `cfg = tomllib.loads(config_path.read_text())`
    - `return bool(cfg.get("voice", {}).get("enabled", False))`
    - Catch `(tomllib.TOMLDecodeError, OSError, ValueError)` → `return False`
  - Direct model: `_rag_already_enabled()` at lines 450–459 of `install.py`
  - Place immediately after `_rag_already_enabled()`
- **Releasable**: after this task, `_voice_already_enabled()` is callable for the install main flow
- **Tests (TDD)** — `tests/test_installer_py.py` (new class `TestVoiceAlreadyEnabled`):
  - Unit: `test_voice_already_enabled_true` — config with `[voice]\nenabled = true` → True
  - Unit: `test_voice_already_enabled_false` — config without `[voice]` section → False
  - Unit: `test_voice_already_enabled_missing_file` — `config.toml` absent → False
  - Unit: `test_voice_already_enabled_malformed_toml` — invalid TOML content → False, no raise
  - Checkpoint: `uv run pytest tests/test_installer_py.py -k "TestVoiceAlreadyEnabled" -v --no-cov`

---

#### Task 3.2 — `install.py`: `_offer_voice_setup()`
- [ ] **File**: `install.py`
- **Depends on**: Task 3.1, Task 1.7 (`archon voice install` must exist)
- **Description**:
  - `_offer_voice_setup(paths: InstallerPaths, console: Console, non_interactive: bool) -> None`
    - If `non_interactive`: return immediately
    - ```python
      try:
          answer = console.ask("Enable voice features (STT/TTS)? [y/N]").strip().lower()
      except (EOFError, KeyboardInterrupt):
          return
      if answer != "y":
          return
      ```
    - `archon_bin = _get_archon_bin(paths)`; if `not archon_bin.exists()`: `console.warn("archon binary not found — run 'archon voice install' manually.")` + return
    - `console.info("Installing voice dependencies (requires PyTorch ~2GB; model weights download on first use)…")`
    - Wrap the following in `except OSError as exc: console.warn(f"Voice setup failed: {exc}. Run 'archon voice install' to retry.")`:
      ```
      result = subprocess.run([str(archon_bin), "voice", "install", "--non-interactive"], check=False)
      if result.returncode != 0:
          console.warn("Voice installation failed. Run 'archon voice install' to retry.")
          return
      rc_cfg = subprocess.run([str(archon_bin), "config", "set", "voice.enabled", "true"], check=False).returncode
      if rc_cfg != 0:
          console.warn("Failed to enable voice. Run: archon config set voice.enabled true")
          return
      ```
    - `console.success("Voice enabled. Run: archon restart to apply.")`
    - NOTE: Do NOT add a `subprocess: archon restart` call here. `_offer_rag_setup()` (called just before `_offer_voice_setup()`) already restarts Archon when RAG is accepted. A second restart here would create a race condition. The user is instructed to restart manually if needed.
  - **Call site** in `main()`, after `_offer_rag_setup(...)`:
    ```python
    _offer_voice_setup(
        paths,
        console,
        non_interactive=args.non_interactive or (args.update and _voice_already_enabled(archon_home)),
    )
    ```
  - Direct model: `_offer_rag_setup()` at lines 462–499
- **Releasable**: after this task, the full feature is complete across all surfaces (installer, CLI, MCP)
- **Tests (TDD)** — `tests/test_installer_py.py` (new class `TestOfferVoiceSetup`):
  - Unit: `test_offer_voice_setup_non_interactive_skips` — `non_interactive=True` → `console.ask` not called
  - Unit: `test_offer_voice_setup_user_declines` — ask → `"n"` → zero `subprocess.run` calls
  - Unit: `test_offer_voice_setup_happy_path` — all subprocess rc=0 → `console.success` called, NO restart subprocess
  - Unit: `test_offer_voice_setup_install_fails` — voice install rc=1 → `console.warn`, config set not called
  - Unit: `test_offer_voice_setup_config_set_fails` — install ok, config set rc=1 → warn, no further subprocesses
  - Unit: `test_offer_voice_setup_eoferror` — `console.ask` raises `EOFError` → returns silently, no subprocess
  - Unit: `test_offer_voice_setup_keyboardinterrupt` — `console.ask` raises `KeyboardInterrupt` → returns silently, no subprocess
  - Unit: `test_offer_voice_setup_archon_bin_missing` — `archon_bin.exists()=False` → `console.warn`, no subprocess
  - Unit: `test_offer_voice_setup_oserror` — subprocess raises `OSError` → `console.warn`, no re-raise
  - Checkpoint: `uv run pytest tests/test_installer_py.py -k "TestOfferVoiceSetup or TestVoiceAlreadyEnabled" -v --no-cov`

---

## Final verification

```bash
# All new tests
uv run pytest tests/voice/ tests/cli/test_voice_cmd.py tests/ai/test_archon_toolkit_voice.py \
              tests/test_installer_py.py -k "voice or Voice" -v --no-cov

# Full suite — no regressions
uv run pytest --no-cov -q

# Manual smoke (after install)
archon voice status
archon voice install --non-interactive
archon voice enable
archon config get voice.enabled    # → true
archon voice status                # → whisper: installed, ffmpeg: found/not found, edge-tts: installed
```
