# FEAT-029 — Unified Model Registry: merge AVAILABLE_MODELS + MODEL_CONTEXT_WINDOWS
**Purpose**: Replace the parallel `AVAILABLE_MODELS: list[str]` + `MODEL_CONTEXT_WINDOWS: dict[str, int]` with a single `AVAILABLE_MODELS: dict[str, int]`; change `config.toml [models] available` from a list to a dict; update `update_models.py` to sync context window sizes from the Anthropic API on release.
**Audience**: Developers (correct release workflow, no more desync), operators (simplified config), end users (correct context window stats for all models).
**Status**: To Do

---

## Background

FEAT-024 added per-model context windows via a `MODEL_CONTEXT_WINDOWS` dict alongside the existing `AVAILABLE_MODELS` list. The two structures are parallel and can silently desync: today 5 of 9 models have no entry in `MODEL_CONTEXT_WINDOWS`, `claude-opus-4-6` is set to 200k instead of 1M, and `update_models.py` syncs only the model list — not context windows. This refactor merges both into one `dict[str, int]` and makes the release script the single authority for keeping values accurate.

> **Note**: The existing `constants.py` comment says "the Anthropic API does not expose context window sizes" — this was accurate historically. As of the Anthropic Models API v2, the response does include a `context_window` field per model. `update_models.py` currently ignores this field; Phase 3 adds extraction of this field. The 1M context window for `claude-opus-4-6` is per [Anthropic's published model documentation](https://docs.anthropic.com/en/docs/about-claude/models).

---

## Goal

After this feature, `AVAILABLE_MODELS: dict[str, int]` in `constants.py` is the single source of truth for both which models exist and their context window sizes. Adding a model without a context window becomes impossible. `config.toml [models.available]` is also a `dict[str, int]`, giving operators one place to declare which models are enabled and with what context window. `update_models.py` fetches both model IDs and `context_window` values from the Anthropic API on each release and keeps both `constants.py` and `config.toml.example` current automatically.

---

## Scope

### In Scope
- `AVAILABLE_MODELS` in `constants.py` changes from `list[str]` to `dict[str, int]` (model → context window)
- `MODEL_CONTEXT_WINDOWS` removed; `get_context_window()` updated to look up `AVAILABLE_MODELS`
- Correct context window for `claude-opus-4-6`: `1_000_000`
- `ModelsConfig.available` changes from `list[str]` to `dict[str, int]`
- `ModelsConfig.context_windows` field removed entirely
- Config loader parses `[models.available]` as a TOML table (dict), removes `[models.context_windows]` parsing
- Gateway passes `cfg.models.available` (instead of `cfg.models.context_windows`) as the context window override source to `SessionManager`
- All call sites that iterate or check `models.available` updated to work with a dict
- `scripts/update_models.py` updated to: (1) extract `context_window` per model from the API response, (2) write `AVAILABLE_MODELS` in dict format, (3) write `[models.available]` as a TOML table in `config.toml.example`
- `examples/config.toml.example` updated to use `[models.available]` table format; `[models.context_windows]` comment block removed
- All affected tests updated

### Out of Scope
- Changing `/models` keyboard UX or Telegram command behaviour
- Adding other per-model metadata (max output tokens, pricing)
- Validating that a user-configured context window matches the real API value

---

## Acceptance criteria
- [ ] `AVAILABLE_MODELS` is `dict[str, int]`; `MODEL_CONTEXT_WINDOWS` does not exist
- [ ] `get_context_window("claude-opus-4-6")` returns `1_000_000`
- [ ] `get_context_window("claude-sonnet-4-6")` returns `200_000`
- [ ] `get_context_window("unknown-model")` returns `200_000` (fallback unchanged)
- [ ] `ModelsConfig.available` is `dict[str, int]`; `ModelsConfig.context_windows` does not exist
- [ ] Loading `[models.available]\n"claude-opus-4-6" = 1_000_000` from TOML gives `config.models.available == {"claude-opus-4-6": 1_000_000}`
- [ ] `config.models.available` list format raises `ConfigError`
- [ ] `config.models.available` with non-positive int value raises `ConfigError`
- [ ] `context_percentage()` uses the correct window for `claude-opus-4-6` (20% for 200k usage, not 100%) — fixture: `input_tokens=200_000, cumulative_cache_creation=0`
- [ ] `update_models.py` fed API JSON with `context_window` field writes dict format to `constants.py`
- [ ] `update_models.py` fed API JSON without `context_window` field falls back to `200_000` (no crash)
- [ ] `update_models.py` updates `[models.available]` table in `config.toml.example`
- [ ] `release.sh` stages both `constants.py` and `config.toml.example` when either changes after the model sync
- [ ] `archon doctor` shows ⚠️ when a configured model's context window differs from the canonical value in `AVAILABLE_MODELS`
- [ ] `archon doctor` does not warn for custom/proxy models not in `AVAILABLE_MODELS`
- [ ] All existing tests pass; 85%+ coverage maintained

---

## What does NOT change
- `get_context_window()` public signature — `(model: str | None, overrides: dict[str, int] | None = None) -> int`
- Context window override semantics: config value wins over constants value (config `available` dict acts as the override layer, same as `context_windows` did before)
- `SessionManager`, `Pipeline`, `Decomposer`, `ClaudeSession` constructor signatures — they still receive `context_window_overrides: dict[str, int] | None`
- `usage_stats["context_window"]` key and `context_percentage()` method — behaviour unchanged, just correct values
- `MODEL_ALIASES` dict — unchanged
- `DEFAULT_MODEL`, `DEFAULT_FAST_MODEL` constants — unchanged
- `/context` display — correct values automatically because `get_context_window()` is already wired

---

## Known limitations / accepted trade-offs
- Models present in `AVAILABLE_MODELS` constants but absent from the user's `[models.available]` config are not constrained; the `get_context_window()` fallback still applies.
- `DEFAULT_FAST_MODEL = "claude-haiku-4-5-20251001"` is in `AVAILABLE_MODELS`; `MODEL_ALIASES["haiku"] = "claude-haiku-4-5"` (undated) is not. The fallback to 200k for undated aliases is intentional and unchanged.
- **`claude-haiku-4-5` (undated alias)**: currently present in `MODEL_CONTEXT_WINDOWS` but is NOT in `AVAILABLE_MODELS` (the full model list). It will not appear in the new unified dict. `MODEL_ALIASES['haiku']` still resolves to this undated string; calls to `get_context_window('claude-haiku-4-5')` will fall through to the 200k default. This is intentional and unchanged.
- **Dual role of `[models.available]`**: The `available` dict serves both as the model allowlist (models users can select via `/models`) and as context window overrides passed to `get_context_window()`. An operator who restricts `[models.available]` to a subset cannot override context windows for models used internally (e.g., `DEFAULT_FAST_MODEL` used by the Classifier) without also exposing them to end users. If fine-grained override control is needed for internal models, a separate `[models.context_windows]` table can be added in a future feature. This trade-off is accepted for this release.
- **`archon_toolkit.py` vs `commands.py` empty-available behavior**: When `[models.available]` is empty, `commands.py` falls back to `AVAILABLE_MODELS` (all models available in the picker), but `archon_toolkit.py`'s `set_model` tool rejects all models. This inconsistency pre-exists this feature and is out of scope for FEAT-029.

---

## Architecture

### `archon/ai/constants.py`
```python
# Before
AVAILABLE_MODELS = ["claude-3-haiku-20240307", ..., "claude-sonnet-4-6"]  # list[str]
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-opus-4-6": 200_000, ...  # 4 entries only
}

# After
AVAILABLE_MODELS: dict[str, int] = {
    "claude-3-haiku-20240307":    200_000,
    "claude-haiku-4-5-20251001":  200_000,
    "claude-opus-4-1-20250805":   200_000,
    "claude-opus-4-20250514":     200_000,
    "claude-opus-4-5-20251101":   200_000,
    "claude-opus-4-6":          1_000_000,
    "claude-sonnet-4-20250514":   200_000,
    "claude-sonnet-4-5-20250929": 200_000,
    "claude-sonnet-4-6":          200_000,
}
# MODEL_CONTEXT_WINDOWS: removed

def get_context_window(model: str | None, overrides: dict[str, int] | None = None) -> int:
    """Lookup order: overrides → AVAILABLE_MODELS → 200_000 default."""
    if overrides and model in overrides:
        return overrides[model]
    return AVAILABLE_MODELS.get(model or "", 200_000)
```

### `archon/config/loader.py`
```python
@dataclass
class ModelsConfig:
    available: dict[str, int] = field(default_factory=dict)   # was list[str]
    default: str | None = None
    # context_windows: removed

# Loader (replaces lines 603-625):
models_data = data.get("models", {})
raw_available = models_data.get("available") or {}
if isinstance(raw_available, list):
    raise ConfigError("[models] available must be a TOML table ([models.available]), not a list")
elif not isinstance(raw_available, dict):
    raise ConfigError("[models] available must be a TOML table, not a scalar value")
bad_type = [k for k, v in raw_available.items() if not isinstance(v, int) or isinstance(v, bool)]
if bad_type:
    raise ConfigError(f"[models] available values must be integers, got wrong type for: {bad_type!r}")
invalid_cw = [k for k, v in raw_available.items() if isinstance(v, int) and not isinstance(v, bool) and v <= 0]
if invalid_cw:
    raise ConfigError(f"[models] available values must be > 0, got non-positive for: {invalid_cw!r}")
models_available: dict[str, int] = dict(raw_available)
models_default = models_data.get("default") or None
if models_default is None and models_available:
    models_default = next(iter(models_available))   # dict preserves insertion order (Python 3.7+)
models = ModelsConfig(available=models_available, default=models_default)
```

### `archon/gateway/gateway.py`
```python
# Before
context_window_overrides=cfg.models.context_windows or None,
# After
context_window_overrides=cfg.models.available or None,
```

### `archon/chat/commands.py`
```python
# _model_keyboard (line 589) — for name in models.available: — works unchanged (dict iterates keys)

# model_callback (line 705)
# Before:
allowed = models_config.available or list(AVAILABLE_MODELS)
# After:
allowed = models_config.available or AVAILABLE_MODELS   # both are dict[str, int]
# "name not in allowed" still works — dict membership test checks keys
```

### `archon/ai/archon_toolkit.py`
```python
# Line 1189
# Before:
available: list[str] = self._config.models.available
# After:
available: dict[str, int] = self._config.models.available
# ", ".join(available) → ", ".join(available)  — dict iteration is already over keys, no change needed
```

### `scripts/update_models.py`
```python
data = json.load(sys.stdin)
models_raw = {
    m['id']: m.get('context_window', 200_000)
    for m in data.get('data', [])
    if m['id'].startswith('claude-') and not m['id'].endswith('-latest')
}
ids = sorted(models_raw)

# constants.py: write dict format
dict_body = ''.join(f'    "{i}": {models_raw[i]:_},\n' for i in ids)
new_block = f'AVAILABLE_MODELS: dict[str, int] = {{\n{dict_body}}}'
# Regex matches both old list and new dict formats for idempotency:
new_txt = re.sub(
    r'AVAILABLE_MODELS(?:\s*:\s*dict\[str,\s*int\])?\s*=\s*(?:\[[^\]]*\]|\{[^}]*\})',
    new_block, txt, flags=re.DOTALL
)

# config.toml.example: write [models.available] table
table_body = ''.join(f'"{i}" = {models_raw[i]:_}\n' for i in ids)
# Replace the [models.available] table block using a multiline regex:
# The table runs from `[models.available]` to the next `[section]` header or EOF.
# Use this regex (re.DOTALL):
new_toml_txt = re.sub(
    r'(\[models\.available\])[^\[]*',
    f'[models.available]\n# Each entry enables a model and declares its context window size (tokens).\n# update_models.py keeps this list current on every release.\n# Add custom or proxy models here with their actual context window.\n{table_body}\n',
    toml_txt,
    flags=re.DOTALL,
)
# Note: the replacement preserves the `[models.available]` header and all comment lines;
# `table_body` contains the key=value lines only (no header).
```

> **Note**: `ids = sorted(models_raw)` sorts alphabetically. The generated `config.toml.example` will list models in alphabetical order. Since the loader uses `next(iter(models_available))` for the default when none is set, operators who omit `default =` get the alphabetically first model. The example config always sets `default = "claude-sonnet-4-6"` explicitly to avoid this — this explicit `default` line must be preserved when updating `[models.available]`.

> **Note**: API-returned `context_window` values are not validated in `update_models.py`; they are written as-is. Invalid values (zero, negative) would be caught by the config loader validation when operators load the generated config.

### `examples/config.toml.example` — models section
```toml
# Before
[models]
available = [
    "claude-3-haiku-20240307",
    ...
]
# [models.context_windows]   ← commented example removed

# After
[models]
default = "claude-sonnet-4-6"

[models.available]
# Each model maps to its context window size (tokens).
# Add custom or proxy models here with their actual context window.
"claude-3-haiku-20240307"    = 200_000
"claude-haiku-4-5-20251001"  = 200_000
"claude-opus-4-6"            = 1_000_000
"claude-sonnet-4-6"          = 200_000
# ... (full list maintained by update_models.py on release)
```

---

### Phase 5 — Doctor and status warning
> **Releasable**: after Task 5.1 — `archon doctor` warns when a configured context window differs from the canonical value

#### Task 5.1 — Add context window mismatch check to `diagnostics.py` and update `CheckResult`
- [ ] **Files**: `archon/diagnostics.py`, `archon/cli/doctor.py`
- **Depends on**: Task 2.1 (ModelsConfig.available is dict), Task 1.1 (AVAILABLE_MODELS is dict)
- **Description**:
  - Add `warn: bool = False` as the **last field** in `CheckResult(name: str, ok: bool, detail: str, warn: bool = False)` so all existing positional callers (which pass 3 positional args) are unaffected
  - Update `archon/cli/doctor.py` rendering: currently ✅/❌; add ⚠️ for `warn=True and ok=True` — e.g. `"⚠️"` icon, printed without raising an error exit code
  - Add `_check_context_windows(cfg: Config | None = None) -> CheckResult` in `archon/diagnostics.py`:
    - If `cfg` is not provided, call `load_config()` internally; wrap in `try/except ConfigError` → return `CheckResult("context windows", False, "config error: ...")`
    - For each `(model, configured_window)` in `cfg.models.available.items()`:
      - Skip model if not in `AVAILABLE_MODELS` (custom/proxy — user's deliberate choice)
      - If `configured_window != AVAILABLE_MODELS[model]`, add to mismatch list: `f"{model}: configured {configured_window:,}, canonical {AVAILABLE_MODELS[model]:,}"`
    - If `mismatches`: return `CheckResult("context windows", ok=True, warn=True, detail="mismatch: " + "; ".join(mismatches))`
    - If no mismatches: return `CheckResult("context windows", ok=True, detail="all configured windows match canonical values")`
    - Empty `cfg.models.available`: return `CheckResult("context windows", ok=True, detail="no models configured (using defaults)")`
  - Add `_check_context_windows` to `run_checks()` list
- **Releasable**: `archon doctor` shows ⚠️ with detail for any model with a misconfigured context window
- **Tests (TDD)** — `tests/cli/test_doctor.py` and a new `tests/test_diagnostics.py` (or extend existing):
  - Unit: `test_check_result_warn_field_defaults_false` — `CheckResult("x", True, "ok").warn == False`
  - Unit: `test_check_context_windows_all_match` — `available == {"claude-sonnet-4-6": 200_000}` → `ok=True, warn=False`
  - Unit: `test_check_context_windows_mismatch_returns_warn` — `available == {"claude-opus-4-6": 200_000}` → `ok=True, warn=True`, detail contains "1,000,000"
  - Unit: `test_check_context_windows_custom_model_skipped` — `available == {"my-proxy": 500_000}` → no warning (not in `AVAILABLE_MODELS`)
  - Unit: `test_check_context_windows_empty_available` — `available == {}` → `ok=True, warn=False`
  - Unit: `test_check_context_windows_multiple_mismatches_reported` — `available == {"claude-opus-4-6": 200_000, "claude-sonnet-4-6": 100_000}` → `warn=True`, detail contains both model names
  - Unit: `test_doctor_output_shows_warning_icon` — doctor CLI renders ⚠️ for warn=True result
  - Unit: `test_check_context_windows_cfg_none_calls_load_config` — calling `_check_context_windows()` with no arguments calls `load_config()` internally; mock `load_config` to raise `ConfigError` → returns `CheckResult(ok=False, detail='config error: ...')`
  - Checkpoint: `uv run pytest tests/cli/test_doctor.py -v --no-cov`

---

## Tests

- **test_available_models_is_dict** (unit): `AVAILABLE_MODELS` is `dict[str, int]`
- **test_model_context_windows_removed** (unit): `MODEL_CONTEXT_WINDOWS` name does not exist in `constants` module
- **test_get_context_window_known_model** (unit): `get_context_window("claude-sonnet-4-6")` returns `200_000`
- **test_get_context_window_opus_returns_1m** (unit): `get_context_window("claude-opus-4-6")` returns `1_000_000`
- **test_get_context_window_unknown_defaults_200k** (unit): unknown model → `200_000`
- **test_get_context_window_none_defaults_200k** (unit): `None` model → `200_000`
- **test_get_context_window_empty_string_defaults_200k** (unit): `get_context_window("")` returns `200_000`
- **test_get_context_window_override_wins** (unit): override dict takes precedence over `AVAILABLE_MODELS`
- **test_default_model_in_available_models** (unit): `DEFAULT_MODEL in AVAILABLE_MODELS` (invariant check)
- **test_all_available_models_have_positive_window** (unit): all values in `AVAILABLE_MODELS` are `> 0`
- **test_models_config_available_defaults_empty_dict** (unit): `ModelsConfig()` default `.available == {}`
- **test_models_config_available_loaded_as_dict** (unit): `[models.available]` TOML table → `dict[str, int]`
- **test_models_config_available_list_raises** (unit): `available = ["model"]` (list) raises `ConfigError`
- **test_models_config_available_scalar_raises** (unit): `available = 42` raises `ConfigError`
- **test_models_config_available_nonpositive_raises** (unit): value `<= 0` raises `ConfigError`
- **test_models_config_available_bool_value_raises** (unit): `"model" = true` (TOML boolean) raises `ConfigError`
- **test_models_config_default_uses_first_key** (unit): `available` with two models, no `default` set → `config.models.default == first_key`
- **test_models_config_context_windows_removed** (unit): `ModelsConfig` has no `context_windows` attribute
- **test_models_config_context_windows_section_logs_deprecation** (unit): TOML with `[models.context_windows]` present logs a deprecation warning
- **test_gateway_passes_available_dict_as_context_overrides** (unit): `cfg.models.available = {"m": 500_000}` → `SessionManager` receives `context_window_overrides={"m": 500_000}`; verifies gateway reads `cfg.models.available` (not `cfg.models.context_windows`)
- **test_gateway_empty_available_converts_to_none** (unit): `cfg.models.available = {}` → `context_window_overrides=None`
- **test_model_keyboard_iterates_available_dict** (unit): `_model_keyboard` with dict `available` builds correct keyboard
- **test_model_callback_validates_against_dict** (unit): callback validates model name against dict keys
- **test_archon_toolkit_set_model_validates_dict** (unit): `set_model` validates against dict-type `available`
- **test_update_models_writes_dict_format** (unit): API JSON with `context_window` → dict written to constants.py
- **test_update_models_missing_context_window_defaults_200k** (unit): model without `context_window` field → 200_000
- **test_update_models_writes_toml_table** (unit): config.toml.example updated with `[models.available]` table
- **test_update_models_excludes_latest_aliases** (unit): `-latest` models excluded
- **test_update_models_ignores_non_claude** (unit): non-`claude-*` models excluded
- **test_update_models_idempotent** (unit): running twice produces no second diff
- **test_update_models_empty_data_array** (unit): `{"data": []}` → empty dict written to constants.py, warning printed, exit 0
- **test_update_models_no_models_after_filtering** (unit): all models are non-claude or latest-aliases → empty dict written, warning printed, exit 0
- **test_release_sh_stages_config_example** (unit): after `update_models.py` changes `config.toml.example`, `release.sh` stages it alongside `constants.py`
- **test_context_percentage_uses_opus_1m_window** (integration): session with `claude-opus-4-6`, `input_tokens=200_000`, `cumulative_cache_creation=0` → `context_percentage() == 20`
- **test_check_result_warn_field_defaults_false** (unit): `CheckResult("x", True, "ok").warn == False`
- **test_check_context_windows_all_match** (unit): matching config → `ok=True, warn=False`
- **test_check_context_windows_mismatch_returns_warn** (unit): opus at 200k → `ok=True, warn=True`, detail mentions 1,000,000
- **test_check_context_windows_custom_model_skipped** (unit): proxy model not in constants → no warning
- **test_check_context_windows_empty_available** (unit): `available == {}` → `ok=True, warn=False`
- **test_check_context_windows_multiple_mismatches_reported** (unit): `available == {"claude-opus-4-6": 200_000, "claude-sonnet-4-6": 100_000}` → `warn=True`, detail contains both model names
- **test_doctor_output_shows_warning_icon** (unit): doctor CLI renders ⚠️ for warn result
- **test_check_context_windows_cfg_none_calls_load_config** (unit): `_check_context_windows()` with no args calls `load_config()` internally

---

## Documentation update
- [ ] `examples/config.toml.example`, section `[models]`: convert to table format, path: `examples/config.toml.example`
- [ ] `CLAUDE.md`, section `archon/ai/`: update the `constants.py` description line — change `AVAILABLE_MODELS: list[str]` to `AVAILABLE_MODELS: dict[str, int]` and remove any reference to `MODEL_CONTEXT_WINDOWS`

---

## Task breakdown

### Phase 1 — Constants refactor
> **Releasable**: after Task 1.1 — `get_context_window()` uses `AVAILABLE_MODELS` dict; opus returns 1M

#### Task 1.1 — Replace `AVAILABLE_MODELS` list + `MODEL_CONTEXT_WINDOWS` with unified dict in `constants.py`
- [x] **File**: `archon/ai/constants.py`
- **Depends on**: nothing
- **Description**:
  - Change `AVAILABLE_MODELS` from `list[str]` to `dict[str, int]` — include all 9 current models; set `"claude-opus-4-6": 1_000_000`, all others `200_000`
  - Remove `MODEL_CONTEXT_WINDOWS` dict entirely
  - Update `get_context_window()`: replace `MODEL_CONTEXT_WINDOWS.get(model or "", 200_000)` with `AVAILABLE_MODELS.get(model or "", 200_000)` — signature unchanged
  - Update the module comment on line 12-13 to reflect that context windows are now in `AVAILABLE_MODELS`; also remove the "API does not expose context window sizes" note — this is no longer accurate after this feature
  - `MODEL_ALIASES` unchanged
  - Note: The `1_000_000` value for `claude-opus-4-6` in the initial dict is manually set based on Anthropic documentation. Phase 3 will keep this accurate via API sync. The `update_models.py` fallback of `200_000` applies only when the API response lacks a `context_window` field — at launch, the API does return this field for current Claude models.
- **Releasable**: `get_context_window()` works correctly for all models including opus; `MODEL_CONTEXT_WINDOWS` no longer exported
- **Tests (TDD)** — `tests/ai/test_constants.py`:
  - Unit: `test_available_models_is_dict` — `assert isinstance(AVAILABLE_MODELS, dict)`
  - Unit: `test_model_context_windows_removed` — `assert not hasattr(constants_module, 'MODEL_CONTEXT_WINDOWS')`
  - Unit: `test_get_context_window_opus_returns_1m` — `get_context_window("claude-opus-4-6") == 1_000_000`
  - Unit: `test_get_context_window_known_model` — `get_context_window("claude-sonnet-4-6") == 200_000`
  - Unit: `test_get_context_window_unknown_defaults_200k` — `get_context_window("gpt-5") == 200_000`
  - Unit: `test_get_context_window_none_defaults_200k` — `get_context_window(None) == 200_000`
  - Unit: `test_get_context_window_empty_string_defaults_200k` — `get_context_window("") == 200_000`
  - Unit: `test_get_context_window_override_wins` — override `{"claude-opus-4-6": 500_000}` → `500_000`
  - Unit: `test_default_model_in_available_models` — `DEFAULT_MODEL in AVAILABLE_MODELS`
  - Unit: `test_all_available_models_have_positive_window` — all values `> 0`
  - Update existing: `test_opus_in_available_models` — `"claude-opus-4-6" in AVAILABLE_MODELS` (still passes; key exists in dict)
  - Checkpoint: `uv run pytest tests/ai/test_constants.py -v --no-cov`

---

### Phase 2 — Config schema change
> **Releasable**: after Task 2.3 — config loads `available` as `dict[str, int]`; full chain from config to session wired

#### Task 2.1 — Update `ModelsConfig` dataclass and config loader
- [x] **File**: `archon/config/loader.py`
- **Depends on**: Task 1.1
- **Description**:
  - Change `ModelsConfig.available` from `list[str]` to `dict[str, int] = field(default_factory=dict)`
  - Remove `ModelsConfig.context_windows: dict[str, int]` field entirely
  - In loader block (≈line 602), replace the `available` parsing:
    - `raw_available = models_data.get("available") or {}`
    - If `raw_available` is a `list`, raise `ConfigError('[models] available must be a TOML table ([models.available]), not a list')`
    - If not `dict` and not `list`, raise `ConfigError("[models] available must be a TOML table, not a scalar value")`
    - Validate all values: `isinstance(v, int) and not isinstance(v, bool)` — else raise `ConfigError`
    - Validate all values `> 0` — else raise `ConfigError`
    - `models_available: dict[str, int] = dict(raw_available)`
  - Log a deprecation warning if `data.get('models', {}).get('context_windows')` is present in the raw config: `logger.warning('[models.context_windows] is no longer used — merge your context window values into [models.available] entries.')`
  - "default = first available" fallback (line 607): change `models_available[0]` to `next(iter(models_available))` — `iter()` over a dict yields keys
  - Remove the entire `raw_cw` / `context_windows` parsing block (lines 609-620)
  - `ModelsConfig(available=models_available, default=models_default)` — no `context_windows` arg
- **Releasable**: config loads `[models.available]` as dict; `context_windows` field gone
- **Tests (TDD)** — `tests/config/test_config_loader.py` and `tests/config/test_loader.py`:
  - Unit: `test_models_config_available_loaded_as_dict` — TOML `[models.available]\n"claude-opus-4-6" = 1_000_000` → `cfg.models.available == {"claude-opus-4-6": 1_000_000}`
  - Unit: `test_models_config_available_defaults_empty_dict` — `[models]` with no `available` key → `cfg.models.available == {}`
  - Unit: `test_models_config_available_list_raises` — `available = ["claude-sonnet-4-6"]` (list form) raises `ConfigError`
  - Unit: `test_models_config_available_nonpositive_raises` — `"m" = 0` raises `ConfigError`
  - Unit: `test_models_config_available_bool_value_raises` — `"m" = true` raises `ConfigError`
  - Unit: `test_models_config_available_scalar_raises` — `available = 42` (scalar, not dict or list) raises `ConfigError`
  - Unit: `test_models_config_context_windows_attr_removed` — `ModelsConfig()` has no `context_windows` attribute
  - Unit: `test_models_config_context_windows_section_logs_deprecation` — TOML with `[models.context_windows]` present logs a deprecation warning
  - Unit: `test_models_config_default_uses_first_key` — available with two models, no default set → default = first key
  - Update existing: `test_models_config_context_windows_loaded_from_toml` → remove or replace with above
  - Update existing: all `ModelsConfig(available=[], ...)` constructions in test_loader.py → `available={}`
  - Checkpoint: `uv run pytest tests/config/ -v --no-cov`

---

#### Task 2.2 — Update gateway, commands, and archon_toolkit call sites
- [x] **Files**: `archon/gateway/gateway.py`, `archon/chat/commands.py`, `archon/ai/archon_toolkit.py`
- **Depends on**: Task 2.1
- **Description**:
  - `gateway.py` (≈line 695): `context_window_overrides=cfg.models.context_windows or None` → `context_window_overrides=cfg.models.available or None`
  - `commands.py` (line 705): `allowed = models_config.available or list(AVAILABLE_MODELS)` → `allowed = models_config.available or AVAILABLE_MODELS`
    - `name not in allowed` still works — both are dicts, membership test checks keys
  - `commands.py` (line 589): `for name in models.available:` — no change needed, dict iteration yields keys
  - `commands.py` (≈line 648): `'\n'.join(f'...' for m in AVAILABLE_MODELS)` — dict iteration yields keys; no change needed, but verify during implementation
  - `archon_toolkit.py` (line 1189): `available: list[str] = self._config.models.available` → `available: dict[str, int] = self._config.models.available`
    - `", ".join(available)` — dict iteration yields keys; no change needed
    - `model not in available` — dict membership; no change needed
- **Releasable**: full chain wired — `cfg.models.available` serves as context window override source; all model selection logic works with dict
- **Tests (TDD)**:
  - `tests/gateway/test_gateway.py`:
    - Unit: `test_gateway_passes_available_dict_as_context_overrides` — `cfg.models.available = {"m": 500_000}` → `SessionManager` receives `context_window_overrides={"m": 500_000}`; verifies gateway reads `cfg.models.available` (not `cfg.models.context_windows`)
    - Unit: `test_gateway_empty_available_converts_to_none` — `cfg.models.available = {}` → `context_window_overrides=None` (empty dict is falsy, `{} or None` is `None`)
    - Update existing: `test_gateway_converts_empty_context_windows_to_none` → rename to `test_gateway_converts_empty_available_to_none`, update to use `available={}`
    - Update existing: `test_gateway_passes_context_windows_to_session_manager` → rename to `test_gateway_passes_available_dict_as_context_overrides` (see above)
    - Update all `ModelsConfig(available=[], ...)` → `available={}`
  - `tests/chat/test_commands.py`:
    - Unit: `test_model_keyboard_iterates_available_dict` — `_model_keyboard(ModelsConfig(available={"m1": 200_000, "m2": 200_000}), None)` → keyboard has "m1" and "m2" buttons
    - Unit: `test_model_callback_validates_against_dict_keys` — callback with model not in dict keys → rejected; model in dict keys → accepted
    - Update `_mock_models()` helper (line 1351): `available: dict[str, int] | None = None` → `return ModelsConfig(available=available or {}, default=default)`
    - Update `test_model_is_in_available` and `test_model_not_in_available` tests (lines 1562, 1595) to pass dict available
    - Update all `ModelsConfig(available=[], ...)` → `available={}`
  - `tests/ai/test_archon_toolkit_config.py`:
    - Update `config.models.available = available` (line 32) to use dict form
  - Type check: `uv run mypy archon/` — verify no new type errors from the dict annotation changes
  - Checkpoint: `uv run pytest tests/gateway/ tests/chat/test_commands.py tests/ai/test_archon_toolkit_config.py -v --no-cov`

---

### Phase 3 — Release script update
> **Releasable**: after Task 3.1 — `release.sh` keeps `AVAILABLE_MODELS` dict and `config.toml.example` table accurate on every release

#### Task 3.1 — Update `update_models.py` and `release.sh` to sync context windows and stage both files
- [x] **Files**: `scripts/update_models.py`, `release.sh`
- **Depends on**: Task 1.1
- **Description**:
  - **Initial conversion note**: `update_models.py`'s TOML regex expects `[models.available]` to already exist as a section header in `config.toml.example`. The initial one-time conversion from `available = [...]` (list key under `[models]`) to `[models.available]` (table section) is handled within Task 3.1 itself — update `update_models.py` to detect either format **in `config.toml.example`** and perform the conversion on first run. After the initial conversion, subsequent runs use the idempotent regex. Note: this is a `config.toml.example` file transformation only — the config loader still raises `ConfigError` if a user's `config.toml` contains the old list format.
  - Change model parsing to extract `context_window` from each model in the API response:
    ```python
    models_raw = {
        m['id']: m.get('context_window', 200_000)
        for m in data.get('data', [])
        if m['id'].startswith('claude-') and not m['id'].endswith('-latest')
    }
    ids = sorted(models_raw)
    ```
  - Build the dict body for constants.py: `''.join(f'    "{i}": {models_raw[i]:_},\n' for i in ids)`
  - New block: `f'AVAILABLE_MODELS: dict[str, int] = {{\n{dict_body}}}'`
  - Regex must match both old list format `AVAILABLE_MODELS = [...]` and new dict format `AVAILABLE_MODELS: dict[str, int] = {...}` for idempotency — use `re.DOTALL`:
    ```python
    new_txt = re.sub(
        r'AVAILABLE_MODELS(?:\s*:\s*dict\[str,\s*int\])?\s*=\s*(?:\[[^\]]*\]|\{[^}]*\})',
        new_block,
        txt,
        flags=re.DOTALL,
    )
    ```
  - For `config.toml.example`: build a TOML table body `''.join(f'"{i}" = {models_raw[i]:_}\n' for i in ids)` and replace the `[models.available]` table block using the following regex (re.DOTALL):
    ```python
    new_toml_txt = re.sub(
        r'(\[models\.available\])[^\[]*',
        f'[models.available]\n# Each entry enables a model and declares its context window size (tokens).\n# update_models.py keeps this list current on every release.\n# Add custom or proxy models here with their actual context window.\n{table_body}\n',
        toml_txt,
        flags=re.DOTALL,
    )
    ```
    Note: the replacement preserves the `[models.available]` header and all comment lines; `table_body` contains the key=value lines only (no header).
  - Note: `ids = sorted(models_raw)` sorts alphabetically. The generated `config.toml.example` will list models in alphabetical order. Since the loader uses `next(iter(models_available))` for the default when none is set, operators who omit `default =` get the alphabetically first model. The example config always sets `default = "claude-sonnet-4-6"` explicitly to avoid this — this explicit `default` line must be preserved when updating `[models.available]`.
  - Gracefully handle missing `context_window` field (already covered by `.get('context_window', 200_000)`)
  - When `data.get('data', [])` is empty or all models are filtered out, `models_raw` will be `{}` and `ids` will be `[]`. In this case: write `AVAILABLE_MODELS: dict[str, int] = {}` to `constants.py` and write an empty `[models.available]` table to `config.toml.example`. Print a warning: `'Warning: no models found after filtering — wrote empty AVAILABLE_MODELS'`. Do NOT exit with code 1 in this case.
  - Note: API-returned `context_window` values are not validated in `update_models.py`; they are written as-is. Invalid values (zero, negative) would be caught by the config loader validation when operators load the generated config.
  - Print updated dict summary on change; print "already up to date" if no diff; exit 1 if pattern not found (same as before)
  - `release.sh`: the existing staging logic already conditionally stages both `constants.py` and `config.toml.example` after model sync. Verify the existing block handles both files; no new staging logic should be needed. Update only if the existing `git-diff/git-add` block does not already cover `config.toml.example`:
    ```bash
    if ! git diff --quiet archon/ai/constants.py; then
        run "git add archon/ai/constants.py"
        ok "Staged updated constants.py"
    fi
    if ! git diff --quiet examples/config.toml.example; then
        run "git add examples/config.toml.example"
        ok "Staged updated config.toml.example"
    fi
    ```
- **Releasable**: on every release, `AVAILABLE_MODELS` dict in `constants.py` and `[models.available]` table in `config.toml.example` are both updated from the API and both staged for the release commit
- **Tests (TDD)** — `tests/scripts/test_update_models.py`:
  - Unit: `test_update_models_writes_dict_format` — feed `{"data": [{"id": "claude-foo", "context_window": 300_000}]}` → `constants.py` contains `"claude-foo": 300_000`
  - Unit: `test_update_models_missing_context_window_defaults_200k` — model without `context_window` key → written with `200_000`
  - Unit: `test_update_models_writes_toml_table` — `config.toml.example` contains `"claude-foo" = 300_000` under `[models.available]`
  - Unit: `test_update_models_idempotent` — running on already-updated files produces no diff
  - Unit: `test_update_models_excludes_latest_aliases` — `-latest` models excluded (existing, update to dict)
  - Unit: `test_update_models_ignores_non_claude` — non-`claude-*` excluded (existing, update to dict)
  - Unit: `test_update_models_no_change_returns_unchanged` — identical list → no file write (update to dict format)
  - Unit: `test_update_models_empty_data_array` — `{"data": []}` → empty dict written to constants.py, warning printed, exit 0
  - Unit: `test_update_models_no_models_after_filtering` — all models are non-claude or latest-aliases → empty dict written, warning printed, exit 0
  - Unit: `test_update_models_converts_old_list_format_in_example` — if `config.toml.example` has `available = [...]` (old key format under `[models]`), it is converted to `[models.available]` table format on first run
  - Unit: `test_release_sh_stages_config_example` — after `update_models.py` changes `config.toml.example`, `release.sh` stages it alongside `constants.py`
  - Checkpoint: `uv run pytest tests/scripts/test_update_models.py -v --no-cov`

---

### Phase 4 — Config example and documentation
> **Releasable**: after this phase — operators have accurate reference config

#### Task 4.1 — Update `config.toml.example` and `CLAUDE.md`
- [ ] **Files**: `examples/config.toml.example`, `CLAUDE.md`
- **Depends on**: Task 2.1, Task 3.1
- **Description**:
  - `config.toml.example`: replace `available = [\n    "model",\n    ...\n]` list block with `[models.available]` TOML table:
    ```toml
    [models]
    default = "claude-sonnet-4-6"

    [models.available]
    # Each entry enables a model and declares its context window size (tokens).
    # update_models.py keeps this list current on every release.
    # Add custom or proxy models here with their actual context window.
    "claude-3-haiku-20240307"    = 200_000
    "claude-haiku-4-5-20251001"  = 200_000
    "claude-opus-4-1-20250805"   = 200_000
    "claude-opus-4-20250514"     = 200_000
    "claude-opus-4-5-20251101"   = 200_000
    "claude-opus-4-6"            = 1_000_000
    "claude-sonnet-4-20250514"   = 200_000
    "claude-sonnet-4-5-20250929" = 200_000
    "claude-sonnet-4-6"          = 200_000
    ```
  - Remove the commented `[models.context_windows]` example block entirely
  - `CLAUDE.md`: in the `archon/ai/` section, update the `constants.py` description line — change `AVAILABLE_MODELS: list[str]` to `AVAILABLE_MODELS: dict[str, int]` and remove any reference to `MODEL_CONTEXT_WINDOWS`
  - `tests/config/test_config_example_sync.py` (line 169): `ModelsConfig().available == []` → `ModelsConfig().available == {}`
  - `tests/test_installer_py.py` (line 1853): `doc["models"]["available"] == AVAILABLE_MODELS` — after the change, both are dicts; test should still pass, but verify the TOML serialization produces a table that round-trips correctly
  - Type check: `uv run mypy archon/` — verify no new type errors from the dict annotation changes
- **Releasable**: documentation reflects the new format
- **Tests**: Covered by config example sync tests
  - Checkpoint: `uv run pytest --no-cov` (full suite)
