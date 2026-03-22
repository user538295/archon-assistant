# REFACTOR-015 — Config Single Source of Truth via `config.toml.example`

**Last reviewed:** 2026-03-22
**Next review:** 2026-06-22
**Purpose**: Eliminate duplicated config field definitions by making `examples/config.toml.example` the single source of truth for the install template, removing the hardcoded `_default_config()` string and scattered model/port constants.
**Audience**: Maintainers adding new config fields; users who get a fully-annotated config on fresh install.
**Status**: To Do

---

## Background

Config values are currently defined in up to four places simultaneously:

| Location | Role |
|---|---|
| `archon/config/loader.py` dataclasses | Runtime defaults and validation (authoritative at runtime) |
| `install.py` `_default_config()` | 50-line hardcoded TOML string for fresh installs |
| `examples/config.toml.example` | Annotated reference doc (505 lines with comments) |
| `archon/ai/constants.py` + CLI files | Scattered model/port constants with `# Keep in sync` comments |

When a new config field is added, all four locations must be updated manually. The `install.py` model list even carries a `# Keep in sync with archon/ai/constants.py` comment — a textbook sign of the problem. Additionally, `_SPARSE_PATHS` in `install.py` references `"config.toml.example"` at the repo root, but the file lives at `examples/config.toml.example`, meaning the sparse checkout silently skips the example file.

## Goal

Replace `_default_config()` in `install.py` with a template function that reads `examples/config.toml.example` and substitutes user-specific values at install time using regex line replacement. New installs get a fully-annotated config with all options visible. Fix the sparse checkout path. Remove the redundant model-injection in the update path and centralize the duplicate `_DEFAULT_BG_PORT` constant.

---

## Scope

### In Scope
- Use regex line replacement in `_render_config_template()` to substitute `allowed_user_ids` and `working_directory` values from the example file — one modification to the example file required: uncomment `models.default`
- Uncomment `# default = "claude-sonnet-4-6"` in `examples/config.toml.example` so new installs receive an explicit default model
- Add `_render_config_template(template: str, user_ids: list[int], workspace_dir: Path) -> str` to `install.py`
- Replace `_default_config()` call with `_render_config_template()` on fresh install
- Delete `_default_config()` function
- Fix `_SPARSE_PATHS`: remove the stale `"config.toml.example"` entry AND add `"examples"` (it is not currently in the list). Both changes are needed: removing the stale root-level entry and adding the directory that actually contains the file.
- Remove hardcoded model-injection block from the `--update` path in `install.py` (loader handles missing `[models]` via dataclass defaults)
- Centralize `_DEFAULT_BG_PORT = 18182` — remove the duplicate in `doctor.py`, keep it only in `status.py` and import from there (or extract to a shared constant)

### Out of Scope
- Generating `config.toml.example` programmatically from `loader.py` dataclasses (high complexity, low urgency)
- Migrating `DEFAULT_MODEL` / `AVAILABLE_MODELS` out of `constants.py` (those serve a runtime purpose beyond install; addressed separately if needed)
- Changes to `loader.py` dataclass structure or validation logic — but **default values may be corrected** where they diverge from the example (see Task 1.4)

---

## Acceptance criteria
- [x] Fresh install produces a `config.toml` that is a near-identical copy of `config.toml.example` with `allowed_user_ids` and `working_directory` lines replaced by real values via regex
- [x] `_default_config()` function is deleted from `install.py`
- [x] `_SPARSE_PATHS` contains `"examples"` and not a stale root-level `"config.toml.example"` entry
- [x] The `# Keep in sync with archon/ai/constants.py` comment is gone from `install.py`
- [ ] `_DEFAULT_BG_PORT` is defined in exactly one CLI file; the other imports or removes the duplicate
- [x] A sync test in `tests/config/test_config_example_sync.py` passes and would fail if any uncommented value in `config.toml.example` diverges from the corresponding Python dataclass default
- [x] All existing installer tests pass
- [x] New tests cover the template rendering and sparse-path fix
- [x] `uv run pytest tests/test_installer_py.py tests/config/test_config_example_sync.py` passes with ≥85% coverage on changed code
- [x] `examples/config.toml.example` has `default = "claude-sonnet-4-6"` uncommented in `[models]`
- [x] Users running `--update` without `[models]` see an informational message pointing to the example file

---

## What does NOT change
- `archon/config/loader.py` — dataclass structure and validation logic are untouched; only a single default value is corrected (Task 1.4: `VoiceTTSConfig.provider` `"openai"` → `"edge"`)
- `archon/ai/constants.py` — `DEFAULT_MODEL`, `AVAILABLE_MODELS`, `DEFAULT_FAST_MODEL` remain
- `config.toml.example` file content and structure — no modifications needed. The file stays valid TOML at all times.
- The `--update` path logic for `allowed_user_ids` and `working_directory` — these still get patched via `tomli_w`
- `archon/cli/status.py` and `doctor.py` behaviour — functionally identical, just deduplicated constant

---

## Known limitations / accepted trade-offs
- `loader.py` dataclass defaults remain the runtime safety net for partial/legacy configs that pre-date this change. Two places still define some defaults (dataclasses + example file), but they serve genuinely different purposes (runtime fallback vs. install template). This is acceptable.
- Regex line replacement is simple and targeted — no templating engine. This is intentional (KISS); the two dynamic values are well-isolated lines in the example file.
- Users who have already installed and run `--update` will not have their config regenerated. They keep their existing (non-annotated) config. This is correct: `--update` never overwrites user config.
- The sync test checks only **uncommented** fields in the example. Fields that are commented out (e.g. `router_mcp_port`, `language`) are not checked — they represent optional overrides, not documented defaults. This is intentional: the test verifies the values users see, not the entire dataclass surface.
- `[models]` section is explicitly excluded from the sync test. The example ships a curated list (`["claude-sonnet-4-6", "claude-haiku-4-5"]`) while the dataclass defaults to `[]` — this divergence is intentional: new installs get the curated list from the example template (correct behaviour), while the dataclass empty-list default serves as a safe fallback for legacy/partial configs. The sync test would always fail on `[models]` so it is excluded; correctness for new installs is verified by `test_write_config_fresh_install_models_list` instead.
- **Known pre-existing mismatch** (to be fixed by Task 1.4): `VoiceTTSConfig.provider` defaults to `"openai"` in Python but the example shows `"edge"`. The example is correct — `"edge"` requires no API key and is the better default for new users. Task 1.4 fixes the Python default to match.
- `[models].default` is explicitly set to `"claude-sonnet-4-6"` in the example file (uncommented as part of this change), so new installs behave identically to the previous `_default_config()` output. Task 1.4's call-site audit for `None` tolerance is still worth doing but is no longer blocking.

---

## Architecture

### New / changed components

**`examples/config.toml.example`** (modified)
- Uncomment `# default = "claude-sonnet-4-6"` → `default = "claude-sonnet-4-6"`. This ensures new installs receive an explicit default model rather than `None`, matching the previous `_default_config()` behaviour. No other changes.

**`install.py`** (modified)
- `_SPARSE_PATHS`: remove the stale `"config.toml.example"` entry AND add `"examples"` (it is not currently in the list). Both changes are needed: removing the stale root-level entry and adding the directory that actually contains the file.
- New function:
  ```python
  import re
  def _render_config_template(template: str, user_ids: list[int], workspace_dir: Path) -> str:
      """Substitute install-time values in the config.toml.example template."""
      if not user_ids:
          raise ValueError("user_ids must not be empty")
      ids_toml = ", ".join(str(uid) for uid in user_ids)
      # Escape backslashes for TOML string value (Windows paths)
      safe_dir = str(workspace_dir).replace("\\", "\\\\")
      template = re.sub(
          r"^allowed_user_ids\s*=.*$", f"allowed_user_ids = [{ids_toml}]", template, flags=re.MULTILINE
      )
      template = re.sub(
          r'^working_directory\s*=.*$', f'working_directory = "{safe_dir}"', template, flags=re.MULTILINE
      )
      return template
  ```
  This mirrors the existing regex-based fallback in the update path (lines 506–520 of install.py) and keeps the example file valid TOML throughout.
- `write_config()`: resolve the template path as `archon_home / "app.candidate" / "examples" / "config.toml.example"` at function entry. Check that the file exists **unconditionally** (both dry-run and live) and raise `FileNotFoundError` immediately if it is missing — this surfaces the problem even on dry-run. Read the file contents and call `_render_config_template()` only inside the `elif not dry_run:` fresh-install branch (not the update branch, which does not need the template). Remove `_default_config()` call.
- `_default_config()`: deleted entirely
- Update path (lines 492–497): remove the `if "models" not in doc:` block and the `# Keep in sync` comment. The loader already handles a missing `[models]` section via dataclass defaults (`available=[]`, `default=None`).

**`archon/cli/doctor.py`** (modified)
- Remove `_DEFAULT_BG_PORT = 18182` module-level constant
- In `_check_health()`: replace `port = _DEFAULT_BG_PORT` with `port = 18182` inline (it's used in exactly one place in a single function)

### Data flow (fresh install)
```
install.py
  → reads examples/config.toml.example from archon_home / "app.candidate" (valid TOML throughout)
  → _render_config_template(template, user_ids, workspace_dir) — regex line replacement
  → writes ~/.archon/config.toml
```

---

## Tests

- **test_render_config_template_replaces_user_ids_line** (unit): regex replaces the `allowed_user_ids` line
- **test_render_config_template_replaces_working_directory_line** (unit): regex replaces the `working_directory` line
- **test_render_config_template_preserves_other_lines** (unit): other config lines are unchanged after substitution
- **test_render_config_template_multiple_user_ids** (unit): multiple IDs produce valid TOML list syntax
- **test_render_config_template_preserves_comments** (unit): comment lines in template are unchanged
- **test_render_config_template_raises_on_empty_user_ids** (unit): `ValueError` raised when `user_ids=[]`
- **test_render_config_template_escapes_windows_backslashes** (unit): input `Path("C:\\Users\\test\\work")`, assert output contains properly double-escaped backslashes valid for TOML, and `tomllib.loads()` on the result succeeds
- **test_write_config_fresh_install_uses_template** (integration): create a fake template at `tmp_path / "app.candidate" / "examples" / "config.toml.example"` with realistic lines, call `write_config(archon_home=tmp_path, ...)`, assert written config has substituted values
- **test_write_config_fresh_install_contains_all_sections** (integration): written config contains all 14 sections from the example
- **test_write_config_fresh_install_no_sentinel_remaining** (integration): neither the original `allowed_user_ids` placeholder value nor the original `working_directory` placeholder value appears unchanged in the written file
- **test_write_config_fresh_install_missing_example_raises** (unit): call `write_config(archon_home=tmp_path, ...)` where `tmp_path / "app.candidate" / "examples" / "config.toml.example"` does NOT exist; assert `FileNotFoundError` is raised with a descriptive message
- **test_write_config_fresh_install_produces_valid_toml** (integration): reads real `config.toml.example`, runs substitution with dummy values, parses result with `tomllib.loads()`, asserts no exception raised
- **test_write_config_fresh_install_models_list** (integration): after fresh install, load the written config and assert `models.available == ["claude-sonnet-4-6", "claude-haiku-4-5"]` and `models.default == "claude-sonnet-4-6"`
- **test_write_config_dry_run_missing_example_raises** (unit): dry-run call with missing template raises `FileNotFoundError` — confirms dry-run validates template existence
- **test_sparse_paths_includes_examples** (unit): `assert "examples" in _SPARSE_PATHS and "config.toml.example" not in _SPARSE_PATHS`
- **test_update_path_no_model_injection** (integration): `--update` on a config without `[models]` section no longer injects a models block
- **test_update_path_warns_when_models_absent** (integration): when `[models]` is absent from the existing config during `--update`, an informational `con.info()` message is emitted pointing to the example file
- **test_doctor_check_health_reads_port_from_config** (unit): `_check_health()` reads port from config.toml when present
- **test_doctor_check_health_defaults_port_inline** (unit): `_check_health()` uses `18182` when config is absent (no module-level constant required)
- **test_example_session_defaults_match_python** (unit): `inactivity_timeout_seconds` matches `SessionConfig` (the only uncommented scalar field; `attachments_dir` and `attachments_cleanup_hours` are commented out and covered by the reverse check via `_SKIP_REVERSE`)
- **test_example_output_defaults_match_python** (unit): uncommented `[output]` fields match `OutputConfig` defaults
- **test_example_logging_defaults_match_python** (unit): uncommented `[logging]` fields match `LoggingConfig` defaults
- **test_example_notifications_defaults_match_python** (unit): uncommented `[notifications]` fields match `NotificationsConfig` defaults
- **test_example_notifications_agents_defaults_match_python** (unit): verifies `NotificationsAgentsConfig` sub-section handling via explicit `_check_section(parsed['notifications']['agents'], NotificationsAgentsConfig)` call — even if no uncommented keys are present, the reverse check still runs
- **test_example_history_defaults_match_python** (unit): uncommented `[history]` fields match `HistoryConfig` defaults
- **test_example_plugins_defaults_match_python** (unit): uncommented `[plugins]` fields match `PluginsConfig` defaults
- **test_example_qmd_defaults_match_python** (unit): uncommented `[qmd]` fields match `QmdConfig` defaults
- **test_example_schedule_defaults_match_python** (unit): uncommented `[schedule]` fields match `ScheduleConfig` defaults
- **test_example_background_agents_defaults_match_python** (unit): uncommented `[background_agents]` fields match `BackgroundAgentsConfig` defaults
- **test_example_voice_defaults_match_python** (unit): uncommented `[voice]`, `[voice.stt]`, `[voice.tts]` fields match `VoiceConfig`, `VoiceSTTConfig`, `VoiceTTSConfig` defaults
- **test_example_reminder_defaults_match_python** (unit): uncommented `[reminder]` fields match `ReminderConfig` defaults
- **test_example_models_section_excluded_from_sync** (unit): `[models]` section is explicitly excluded — documents the intentional divergence between example's curated list and the empty-list dataclass default
- **test_all_python_defaults_have_example_entry** (unit): for each dataclass, every scalar-default field either appears uncommented in the example or is listed in `_SKIP_REVERSE` with a documented reason; fields with factory defaults (lists, sub-dataclasses) are excluded from this reverse check
- **test_voice_tts_provider_default_is_edge** (unit): `VoiceTTSConfig().provider == "edge"` (regression guard for the fixed mismatch)

---

## Documentation update
- [ ] `Documentation/Architecture/130_data_architecture_and_persistence.md`, section: Configuration — update to reflect that `config.toml.example` is now the install template (read at install time, not modified): `path: Documentation/Architecture/130_data_architecture_and_persistence.md`

---

## Task breakdown

### Phase 1 — Template rendering, sparse-path fix, and default sync test
> **Releasable**: after Task 1.2 — fresh installs produce annotated configs and the sparse-path bug is fixed; after Task 1.4 — CI guards against future default drift

#### Task 1.1 — Add `_render_config_template()` to `install.py`
- [x] **File**: `install.py`
- **Depends on**: nothing
- **Description**:
  - Add function immediately above `_default_config()`:
    ```python
    import re
    def _render_config_template(template: str, user_ids: list[int], workspace_dir: Path) -> str:
        """Substitute install-time values in the config.toml.example template."""
        if not user_ids:
            raise ValueError("user_ids must not be empty")
        ids_toml = ", ".join(str(uid) for uid in user_ids)
        # Escape backslashes for TOML string value (Windows paths)
        safe_dir = str(workspace_dir).replace("\\", "\\\\")
        template = re.sub(
            r"^allowed_user_ids\s*=.*$", f"allowed_user_ids = [{ids_toml}]", template, flags=re.MULTILINE
        )
        template = re.sub(
            r'^working_directory\s*=.*$', f'working_directory = "{safe_dir}"', template, flags=re.MULTILINE
        )
        return template
    ```
    This mirrors the existing regex-based fallback in the update path (lines 506–520 of install.py) and keeps the example file valid TOML throughout.
  - No other changes in this task
- **Releasable**: function is callable and unit-testable
- **Tests (TDD)** — `tests/test_installer_py.py`:
  - Unit: `test_render_config_template_replaces_user_ids_line` — regex replaces the `allowed_user_ids` line with formatted IDs
  - Unit: `test_render_config_template_replaces_working_directory_line` — regex replaces the `working_directory` line with the given path
  - Unit: `test_render_config_template_preserves_other_lines` — other config lines are unchanged after substitution
  - Unit: `test_render_config_template_multiple_user_ids` — two IDs `[1, 2]` → `allowed_user_ids = [1, 2]`
  - Unit: `test_render_config_template_preserves_comments` — `# comment line` appears unchanged in output
  - Unit: `test_render_config_template_raises_on_empty_user_ids` — assert `ValueError` raised when `user_ids=[]`
  - Unit: `test_render_config_template_escapes_windows_backslashes` — input `Path("C:\\Users\\test\\work")`, assert output contains properly double-escaped backslashes valid for TOML, and `tomllib.loads()` on the result succeeds
  - Checkpoint: `uv run pytest tests/test_installer_py.py -k "render_config_template" -v`

#### Task 1.2 — Replace `_default_config()` call with template approach in `write_config()`
- [x] **File**: `install.py`
- **Depends on**: Task 1.1
- **Description**:
  - In `write_config()` (fresh-install branch, currently line 523):
    - Read the template file ONLY inside the `elif not dry_run:` branch (fresh-install branch). Do not read the template at function entry or in the update branch — doing so would cause `FileNotFoundError` on `--update` runs.
    - Locate `examples/config.toml.example` as `archon_home / "app.candidate" / "examples" / "config.toml.example"`. Note: `write_config()` does not receive `paths`; the candidate path is derived from `archon_home` (the same convention used by `_paths(archon_home).candidate`). The template is read from the candidate, not `app`, because `write_config()` is called before activation renames `app.candidate` → `app`.
    - If the file does not exist, raise `FileNotFoundError` with message `"config.toml.example not found at {path} — cannot generate config"`; do not fall back to `_default_config()`
    - Call `_render_config_template(template_text, user_ids, workspace_dir)` and write result to `config_file`
  - Delete `_default_config()` function entirely
  - Fix `_SPARSE_PATHS`: remove the stale `"config.toml.example"` entry AND add `"examples"` (it is not currently in the list). Both changes are needed: removing the stale root-level entry and adding the directory that actually contains the file.
  - The function signature of `write_config()` is unchanged
- **Releasable**: fresh installs now produce a fully-annotated `config.toml`; `_default_config()` is gone
- **Tests (TDD)** — `tests/test_installer_py.py`:
  - Integration: `test_write_config_fresh_install_uses_template` — create a fake template at `tmp_path / "app.candidate" / "examples" / "config.toml.example"` with realistic lines (including `allowed_user_ids = [123456789]` and `working_directory = "~/.archon/workspace"`), call `write_config(archon_home=tmp_path, ...)`, assert written config has substituted values
  - Integration: `test_write_config_fresh_install_contains_all_sections` — real `config.toml.example` used, assert all 14 section headers present in written file
  - Integration: `test_write_config_fresh_install_no_sentinel_remaining` — assert placeholder values from example are replaced in written file
  - Integration: `test_write_config_fresh_install_produces_valid_toml` — reads real `config.toml.example`, runs substitution with dummy values, parses result with `tomllib.loads()`, asserts no exception raised
  - Integration: `test_write_config_fresh_install_models_list` — after fresh install, load the written config and assert `models.available == ["claude-sonnet-4-6", "claude-haiku-4-5"]` and `models.default == "claude-sonnet-4-6"`
  - Unit: `test_write_config_fresh_install_missing_example_raises` — call `write_config(archon_home=tmp_path, ...)` where `tmp_path / "app.candidate" / "examples" / "config.toml.example"` does NOT exist; assert `FileNotFoundError` is raised with a descriptive message
  - Unit: `test_write_config_dry_run_missing_example_raises` — call `write_config(archon_home=tmp_path, dry_run=True, ...)` where the template does NOT exist; assert `FileNotFoundError` is raised (dry-run validates template existence)
  - Unit: `test_sparse_paths_includes_examples` — `assert "examples" in _SPARSE_PATHS and "config.toml.example" not in _SPARSE_PATHS`
  - Checkpoint: `uv run pytest tests/test_installer_py.py -v`

#### Task 1.3 — Add config-example sync test and fix discovered default mismatch
- [x] **File**: `tests/config/test_config_example_sync.py` (new), `archon/config/loader.py`
- **Depends on**: nothing (reads example file as-is; no sentinels)
- **Description**:
  - Create `tests/config/test_config_example_sync.py` with a test that:
    1. Reads `examples/config.toml.example` as text
    2. Substitutes the `allowed_user_ids` and `working_directory` lines with dummy valid values (e.g. `allowed_user_ids = [99999]`, `working_directory = "/tmp"`) so `tomllib` can parse it
    3. Parses with `tomllib.loads()`
    4. For each config section, iterates the parsed dict keys and asserts each uncommented value equals the corresponding `dataclasses.fields()` default on the Python dataclass
    5. Explicitly excludes `[access]` (no defaults), `[session].working_directory` (required, no default), and `[models]` (intentional divergence)
  - Use a shared helper `_check_section(parsed_section: dict, dataclass_type: type, skip: list[str] = [])` that iterates `parsed_section.items()`, skips `skip` keys and nested-dict values (sub-sections), and asserts `value == field_default` for each remaining key. For fields with `field(default_factory=...)` that appear uncommented in the example (e.g. `HistoryConfig.suppressed_tool_results`), `_check_section` must call `field.default_factory()` to get the comparable value, since `field.default` is `dataclasses.MISSING` for factory fields. Pseudo-code: `expected = field.default if field.default is not MISSING else field.default_factory()`. Fields with factory defaults that produce sub-dataclasses or empty lists (like `agents`, `stt`, `tts`, `jobs`, `suppressed_events`) are skipped since the helper skips nested dicts, and the reverse check excludes factory-default fields.
  - Handle sub-sections explicitly: call `_check_section` on `parsed['notifications']['agents']` with `dataclass_type=NotificationsAgentsConfig`. Note that `mode` is commented out in the example (no uncommented value), so the reverse check must list `mode` in `_SKIP_REVERSE` for `NotificationsAgentsConfig` with reason: `'inherit-from-parent, no documented default in example'`.
  - Add a reverse check: for each dataclass, assert that every field with a non-`field(default_factory=...)` default is either (a) present as an uncommented key in the example TOML section, or (b) listed in an explicit `_SKIP_REVERSE` set with a documented reason. Fields with factory defaults (lists, sub-dataclasses) are excluded from the reverse check since they cannot be represented as simple scalar values. The `_SKIP_REVERSE` set must be exhaustive and reviewed whenever the example file changes.

  The complete `_SKIP_REVERSE` set at time of writing (update this set when adding new dataclass fields):

  | Dataclass | Field | Reason |
  |---|---|---|
  | `NotificationsAgentsConfig` | `mode` | Inherit-from-parent default; commented out in example — no documented default |
  | `SessionConfig` | `attachments_dir` | Optional path override; commented out in example |
  | `SessionConfig` | `attachments_cleanup_hours` | Optional; commented out in example |
  | `QmdConfig` | `binary_path` | Optional binary path; not present in example at all |
  | `BackgroundAgentsConfig` | `router_mcp_port` | Optional port override; commented out in example |
  | `VoiceSTTConfig` | `language` | Optional BCP-47 hint; commented out in example |
  | `ModelsConfig` | `default` | Entire `[models]` section excluded from sync (intentional divergence between curated example list and empty dataclass default) |

  Fields with `field(default_factory=...)` (e.g. `suppressed_tool_results`, `suppressed_events`, `agents`, `stt`, `tts`, `jobs`) are handled separately — see below.
  - Test structure — one `test_` function per section — so a single field drift produces a named, targeted failure, not a generic assertion error
  - Fix the pre-existing mismatch found during plan authoring: in `archon/config/loader.py` line ~113, change `VoiceTTSConfig.provider: str = "openai"` → `"edge"`. Rationale: `"edge"` requires no API key and is the correct user-facing default; the Python code was wrong, the example was right.
  - No other changes to `loader.py`
- **Releasable**: any future drift between example and Python defaults will be caught immediately by CI
- **Tests (TDD)** — `tests/config/test_config_example_sync.py`:
  - Unit: `test_example_session_defaults_match_python` — `inactivity_timeout_seconds` matches `SessionConfig` (the only uncommented scalar field; `attachments_dir` and `attachments_cleanup_hours` are commented out and covered by the reverse check via `_SKIP_REVERSE`)
  - Unit: `test_example_output_defaults_match_python` — `max_message_length`, `truncation_strategy`, `head_chars`, `tail_chars` match `OutputConfig`
  - Unit: `test_example_logging_defaults_match_python` — `log_file`, `log_level` match `LoggingConfig`
  - Unit: `test_example_notifications_defaults_match_python` — `mode`, `interval_minutes` match `NotificationsConfig`
  - Unit: `test_example_notifications_agents_defaults_match_python` — verifies `NotificationsAgentsConfig` sub-section handling via explicit `_check_section(parsed['notifications']['agents'], NotificationsAgentsConfig)` call — even if no uncommented keys are present, the reverse check still runs
  - Unit: `test_example_history_defaults_match_python` — `enabled`, `directory`, `suppressed_tool_results`, `compaction_enabled`, `context_days`, `auto_compact_threshold` match `HistoryConfig`
  - Unit: `test_example_plugins_defaults_match_python` — `enabled`, `plugins_dir`, `settings_path` match `PluginsConfig`
  - Unit: `test_example_qmd_defaults_match_python` — `enabled`, `host`, `port`, `history_collection` match `QmdConfig`
  - Unit: `test_example_schedule_defaults_match_python` — `enabled`, `jobs_dir` match `ScheduleConfig`
  - Unit: `test_example_background_agents_defaults_match_python` — `spawn_rule`, `max_parallel`, `host`, `port`, `beacon_interval_minutes`, `tool_promotion_threshold` match `BackgroundAgentsConfig`
  - Unit: `test_example_voice_defaults_match_python` — `enabled` matches `VoiceConfig`; `model` matches `VoiceSTTConfig`; `provider`, `model`, `voice`, `auto`, `max_text_length`, `edge_voice` match `VoiceTTSConfig`
  - Unit: `test_example_reminder_defaults_match_python` — `enabled`, `interval_messages`, `interval_tokens` match `ReminderConfig`
  - Unit: `test_example_models_section_excluded_from_sync` — documents the intentional sync-test exclusion: `ModelsConfig` dataclass default is `available == []` but example has a curated list; new installs get the curated list via the template (verified by `test_write_config_fresh_install_models_list`)
  - Unit: `test_all_python_defaults_have_example_entry` — for each dataclass, every scalar-default field either appears uncommented in the example or is in `_SKIP_REVERSE`
  - Unit: `test_voice_tts_provider_default_is_edge` — `VoiceTTSConfig().provider == "edge"` (regression guard for the fixed mismatch)
  - Checkpoint: `uv run pytest tests/config/test_config_example_sync.py -v`

#### Task 1.4 — Verify `config.models.default` call sites tolerate `None`
- [x] **File**: any file calling `config.models.default`
- **Depends on**: Task 1.2 (establishes that new installs have `models.default = None`)
- **Description**:
  - Audit every call site of `config.models.default` in the codebase
  - Confirm each tolerates `None` (falls back to `DEFAULT_MODEL` from `constants.py` or equivalent)
  - If any call site does not handle `None` safely, either fix the call site or uncomment `default = "claude-sonnet-4-6"` in `examples/config.toml.example`
  - Document the outcome as a comment in the relevant code or in this task's completion notes
- **Releasable**: safe to ship fresh installs with `models.default = None`
- **Tests (TDD)**: no new test file; covered by `test_write_config_models_default_is_none` in Task 1.2 and existing model-selection tests

---

### Phase 2 — Remove redundant model injection and centralize port constant
> **Releasable**: after Task 2.1 — `install.py` is free of all `# Keep in sync` comments; after Task 2.2 — port constant is defined once

#### Task 2.1 — Remove model-injection block from `install.py` update path
- [x] **File**: `install.py`
- **Depends on**: Task 1.2
- **Description**:
  - In `write_config()` update path (currently lines 492–497):
    ```python
    if "models" not in doc:
        # Keep in sync with archon/ai/constants.py
        doc["models"] = {
            "available": ["claude-sonnet-4-6", "claude-haiku-4-5"],
            "default": "claude-sonnet-4-6",
        }
    ```
    Delete this entire block.
  - **Breaking behavior**: Existing users who lack a `[models]` section and run `--update` will no longer get a models list injected. They must manually add `[models]` to their config. This is an intentional trade-off — emit an informational `con.info()` message during the update if `[models]` is absent from the existing config: `con.info("Note: your config has no [models] section. Add one to enable the /models keyboard — see examples/config.toml.example for the format.")`
  - Rationale: `loader.py` `ModelsConfig` dataclass already defaults to `available=[]` and `default=None`; existing behaviour when `[models]` is absent is handled by `constants.py` fallback in session/model selection. The injected block was only a convenience for old installs; it is no longer needed and creates a sync risk.
  - No other changes to the update path
- **Releasable**: `install.py` no longer contains model strings or `# Keep in sync` comments
- **Tests (TDD)** — `tests/test_installer_py.py`:
  - Integration: `test_update_path_preserves_existing_models` — config with `[models]` section is untouched after update
  - Integration: `test_update_path_no_model_injection_when_absent` — config without `[models]` section is written back without one being added
  - Integration: `test_update_path_warns_when_models_absent` — verify the `con.info()` message is emitted when `[models]` is absent
  - Checkpoint: `uv run pytest tests/test_installer_py.py -k "update" -v`

#### Task 2.2 — Deduplicate `_DEFAULT_BG_PORT` between `status.py` and `doctor.py`
- [ ] **File**: `archon/cli/doctor.py`
- **Depends on**: nothing (independent)
- **Description**:
  - `archon/cli/status.py` already defines `_DEFAULT_BG_PORT = 18182` and uses it correctly as a fallback when reading config
  - `archon/cli/doctor.py` has its own identical `_DEFAULT_BG_PORT = 18182` module-level constant (line 13)
  - In `doctor.py`: remove the module-level `_DEFAULT_BG_PORT = 18182`
  - In `_check_health()` (line 100), replace `port = _DEFAULT_BG_PORT` with the inline literal `port = 18182`; this keeps the fallback without creating a named constant that duplicates `status.py`
  - Rationale: the constant in `doctor.py` is used in a single function body; an inline literal is simpler than a shared import. If a shared constant is needed later (third use site), extract to `archon/cli/_constants.py` at that point.
- **Releasable**: `_DEFAULT_BG_PORT` constant exists in exactly one file (`status.py`)
- **Tests (TDD)** — `tests/cli/test_doctor.py`:
  - Unit: `test_check_health_reads_port_from_config` — when config.toml has `background_agents.port = 19999`, that port is used
  - Unit: `test_check_health_uses_default_port_when_config_absent` — when config absent, port `18182` is used
  - Checkpoint: `uv run pytest tests/cli/test_doctor.py -k "health" -v`
