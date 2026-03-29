# FEAT-024 — Per-Model Context Window Sizes
**Purpose**: Replace the single hardcoded `CONTEXT_WINDOW_TOKENS = 200_000` constant with a per-model lookup dict and an optional config override, so auto-compact and the `/context` display use the correct context window size for each model. Also adds `claude-opus-4-6` to `AVAILABLE_MODELS` and auto-syncs the model list from the Anthropic API during releases.
**Audience**: Archon operators (correct auto-compact behaviour), end users (correct `/context` display), developers (release process).
**Status**: To Do

---

## Background

Auto-compact is triggered when context usage reaches a configured percentage of `CONTEXT_WINDOW_TOKENS`. This constant is 200 000 for all models. If a future model has a different context window (e.g. 1 M tokens), auto-compact fires at the wrong threshold. Separately, `claude-opus-4-6` is defined in `MODEL_ALIASES` but is missing from `AVAILABLE_MODELS`, making it unreachable via the `/models` keyboard. Finally, `release.sh` never updates `AVAILABLE_MODELS` when Anthropic releases new models.

This feature is tracked in the tech-debt roadmap as TD.029 (`530_technical_debt_refactoring_roadmap.md` line 94).

---

## Goal

When the daemon selects a model (default, user-chosen, or alias-resolved), both `context_percentage()` and the `/context` display use the correct window size for that model. A developer can override the window size for a custom/proxy model in `config.toml` without changing source code. `release.sh` refreshes `AVAILABLE_MODELS` from the live Anthropic API on every release, keeping the list current automatically.

---

## Scope

### In Scope
- `MODEL_CONTEXT_WINDOWS` dict + `get_context_window()` helper in `constants.py`
- `claude-opus-4-6` added to `AVAILABLE_MODELS`
- `context_window_overrides: dict[str, int]` config field under `[models]`
- `context_window` key added to `usage_stats` dict (used by `context_percentage()` and `_fmt_context()`)
- `context_window_overrides` threaded through: `ClaudeSession → Decomposer → Pipeline → SessionManager → gateway`
- `/context` display denominator becomes model-aware
- `release.sh` model sync step via Anthropic API (non-fatal, ANTHROPIC_API_KEY optional)

### Out of Scope
- Context window sizes for background-agent sub-sessions (router/summary) — they never call `context_percentage()`
- Fetching context window sizes from the API (API does not expose them) — sizes are maintained manually in `constants.py`
- Automatic context window discovery at runtime

---

## Acceptance criteria
- [ ] `get_context_window("claude-sonnet-4-6")` returns `200_000`
- [ ] `get_context_window("unknown-model")` returns `200_000` (safe fallback)
- [ ] `get_context_window("custom", {"custom": 1_000_000})` returns `1_000_000`
- [ ] `context_percentage()` computes against the model's window size, not a global constant
- [ ] `/context` display shows the correct denominator for the active model
- [ ] `claude-opus-4-6` appears in the `/models` keyboard (it is in `AVAILABLE_MODELS`)
- [ ] `[models.context_windows]` in `config.toml` overrides the window for any model
- [ ] `release.sh` updates `AVAILABLE_MODELS` when run with `ANTHROPIC_API_KEY` set
- [ ] `release.sh` skips the sync gracefully when `ANTHROPIC_API_KEY` is absent
- [ ] All existing tests pass; 85%+ coverage maintained

---

## What does NOT change
- `usage_stats` dict schema — only adds one key (`context_window`); all existing keys unchanged
- `context_percentage()` method signature — callers unchanged
- `_fmt_context()` signature — context_window is read from the `stats` dict, not a new parameter
- `CONTEXT_WINDOW_TOKENS` import in tests other than `test_fmt_context_uses_shared_constant`
  (that specific test is replaced — see Task 3.1)
- Router session and summary session in `Decomposer` — no changes needed
- All other `SessionManager` constructor parameters
- `install.py` — no model references exist there (the comment in `constants.py` is stale; updated in Task 1.1)
- Config field naming: `ModelsConfig.context_windows` (config layer) maps to `context_window_overrides` in all code layers (`ClaudeSession`, `Decomposer`, `Pipeline`, `SessionManager`). The gateway converts with `cfg.models.context_windows or None`. This naming difference is intentional: "context_windows" describes the config meaning (model-to-window mapping), while "context_window_overrides" describes the runtime meaning (user-defined overrides that take precedence over built-in constants).

---

## Known limitations / accepted trade-offs
- `MODEL_CONTEXT_WINDOWS` must be manually updated when Anthropic releases a model with a different window. This is a low-frequency event and a safe default (200K fallback) prevents breakage.
- The 200K fallback is safe only if future models have context windows ≥ 200K. If a future model has a *smaller* window (e.g., 128K), auto-compact will trigger too late and the daemon may hit the API context limit before compacting. Mitigation: add any model with a non-200K window to `MODEL_CONTEXT_WINDOWS` before deploying it.
- `release.sh` sync updates `AVAILABLE_MODELS` only, not `MODEL_CONTEXT_WINDOWS` — the API does not expose context window sizes.
- An unknown model (e.g. SDK default when `self._model is None`) resolves to 200K via the empty-string lookup. This is intentional and safe for all current Claude models.

---

## Architecture

### New in `archon/ai/constants.py`
```python
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-opus-4-6":           200_000,
    "claude-sonnet-4-6":         200_000,
    "claude-haiku-4-5":          200_000,
    "claude-haiku-4-5-20251001": 200_000,  # pinned fast-model variant
}

def get_context_window(model: str | None, overrides: dict[str, int] | None = None) -> int:
    """Return the context window size for *model*.

    Lookup order: config overrides → MODEL_CONTEXT_WINDOWS → 200_000 default.
    """
    if overrides and model in overrides:
        return overrides[model]
    return MODEL_CONTEXT_WINDOWS.get(model or "", 200_000)
```
`CONTEXT_WINDOW_TOKENS` is removed. `AVAILABLE_MODELS` gains `"claude-opus-4-6"`.

### New `ModelsConfig` field (`archon/config/loader.py`)
```python
@dataclass
class ModelsConfig:
    available: list[str] = field(default_factory=list)
    default: str | None = None
    context_windows: dict[str, int] = field(default_factory=dict)  # NEW
```
Parsed from `[models.context_windows]` TOML table (defaults to `{}`).

### Changes to `archon/ai/claude_session.py`
- New `__init__` parameter: `context_window_overrides: dict[str, int] | None = None`
- Stored as `self._context_window_overrides: dict[str, int] | None`
- `usage_stats` dict gains key: `"context_window": get_context_window(self._model, self._context_window_overrides)`
- `context_percentage()` reads window from `stats["context_window"]` instead of the removed constant

### Changes to `archon/ai/decomposer.py`
- New `__init__` parameter: `context_window_overrides: dict[str, int] | None = None`
- Forwarded to `ClaudeSession(...)` at line 134

### Changes to `archon/ai/pipeline.py`
- New `__init__` parameter: `context_window_overrides: dict[str, int] | None = None`
- Forwarded to `Decomposer(...)` at line 136

### Changes to `archon/ai/session_manager.py`
- New `__init__` parameter: `context_window_overrides: dict[str, int] | None = None`
- Stored as `self._context_window_overrides`
- Passed as `context_window_overrides=self._context_window_overrides` in `_default_factory` to `Pipeline(...)`

### Changes to `archon/gateway/gateway.py`
- Pass `context_window_overrides=cfg.models.context_windows or None` to `SessionManager()`

### Changes to `archon/chat/commands.py`
- `_fmt_context()` replaces `CONTEXT_WINDOW_TOKENS` (3 occurrences) with `stats.get("context_window", 200_000)`
- Remove `CONTEXT_WINDOW_TOKENS` from the import on line 25

### Changes to `release.sh`
New model-sync step (before the existing `git add`), delegates to a committed helper script `scripts/update_models.py`. In `--dry-run` mode, the entire block is skipped:
```bash
if ! $DRY_RUN; then
  # ─── sync AVAILABLE_MODELS from Anthropic API ───────────────────
  if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
    echo "Syncing AVAILABLE_MODELS from Anthropic API..."
    MODELS_JSON=$(curl -s -f \
      -H "x-api-key: ${ANTHROPIC_API_KEY}" \
      -H "anthropic-version: 2023-06-01" \
      "https://api.anthropic.com/v1/models" 2>/dev/null) || MODELS_JSON=""
    if [ -n "$MODELS_JSON" ]; then
      echo "$MODELS_JSON" | python3 scripts/update_models.py \
        || echo "Warning: model update script failed — continuing"
      if ! git diff --quiet archon/ai/constants.py; then
        run "git add archon/ai/constants.py"
        ok "Staged updated constants.py"
      fi
    else
      echo "Warning: Anthropic API call failed — skipping AVAILABLE_MODELS sync"
    fi
  else
    echo "Warning: ANTHROPIC_API_KEY not set — skipping AVAILABLE_MODELS sync"
  fi
else
  echo "[dry-run] Skipping AVAILABLE_MODELS sync"
fi
```

---

## Tests

- **test_get_context_window_known_model** (unit): returns value from MODEL_CONTEXT_WINDOWS for a known model
- **test_get_context_window_unknown_model_defaults_200k** (unit): unknown model string returns 200_000
- **test_get_context_window_empty_string_defaults_200k** (unit): empty string (None model) returns 200_000
- **test_get_context_window_none_model_defaults_200k** (unit): `get_context_window(None)` returns `200_000` — covers the `self._model` being `None` when no model is explicitly set (handled by `model or ""` inside the helper)
- **test_get_context_window_override_takes_precedence** (unit): override dict wins over constants
- **test_get_context_window_override_none_uses_constants** (unit): None overrides → uses constants
- **test_get_context_window_empty_override_uses_constants** (unit): empty dict overrides → uses constants
- **test_opus_in_available_models** (unit): `"claude-opus-4-6"` is in `AVAILABLE_MODELS`
- **test_models_config_context_windows_loaded** (unit): dict parsed from `[models.context_windows]` TOML
- **test_models_config_context_windows_defaults_empty** (unit): missing section → empty dict
- **test_usage_stats_includes_context_window** (unit): ClaudeSession.usage_stats contains `"context_window"` key
- **test_context_percentage_uses_model_specific_window** (unit): mock 200K usage on 1M-window model → 20%
- **test_context_percentage_uses_override** (unit): config override for model → used in percentage calc
- **test_context_percentage_unknown_model_defaults_200k** (unit): unknown model → 200K denominator
- **test_decomposer_forwards_overrides_to_session** (unit): Decomposer passes context_window_overrides to ClaudeSession
- **test_pipeline_forwards_overrides_to_decomposer** (unit): Pipeline passes context_window_overrides to Decomposer
- **test_session_manager_passes_overrides_to_pipeline** (unit): SessionManager factory passes overrides
- **test_fmt_context_uses_context_window_from_stats** (unit): replaces deleted test; verifies display denominator comes from stats["context_window"]
- **test_fmt_context_custom_window** (unit): stats with context_window=1_000_000 → denominator shown as "1,000,000"
- **test_fmt_context_missing_context_window_defaults_200k** (unit): stats without context_window key → 200K fallback
- **test_update_models_replaces_available_models** (unit, scripts): mock API JSON → correct sorted model list written to constants.py
- **test_update_models_excludes_latest_aliases** (unit, scripts): `-latest` suffixed IDs excluded from output
- **test_update_models_no_change_returns_unchanged** (unit, scripts): identical list → no file write
- **test_update_models_ignores_non_claude_models** (unit, scripts): non-`claude-*` IDs excluded

---

## Documentation update
- [ ] `Documentation/Architecture/530_technical_debt_refactoring_roadmap.md`, section TD.029 row: mark resolved, path: `Documentation/Architecture/530_technical_debt_refactoring_roadmap.md`
- [ ] `CLAUDE.md` models section: add opus to example available list
- [ ] `examples/config.toml.example`: add opus to available, add commented `[models.context_windows]` example

---

## Task breakdown

### Phase 1 — Foundation: constants and config schema
> **Releasable**: after Task 1.2 — `get_context_window()` is callable and `ModelsConfig` accepts overrides

#### Task 1.1 — Add `MODEL_CONTEXT_WINDOWS` dict and `get_context_window()` to `constants.py`
- [x] **File**: `archon/ai/constants.py`
- **Depends on**: nothing
- **Description**:
  - Remove `CONTEXT_WINDOW_TOKENS = 200_000`
  - Remove stale comment "install.py duplicates these values" (install.py has no model refs)
  - Add `MODEL_CONTEXT_WINDOWS: dict[str, int]` with four entries: `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-5`, `claude-haiku-4-5-20251001` — all `200_000`
  - Add `def get_context_window(model: str | None, overrides: dict[str, int] | None = None) -> int` — lookup order: overrides → MODEL_CONTEXT_WINDOWS → 200_000 (uses `model or ""` for the dict lookup so `None` is handled safely)
  - Add `"claude-opus-4-6"` as first entry in `AVAILABLE_MODELS` list
  - New module-level exports: `MODEL_CONTEXT_WINDOWS`, `get_context_window`
  - Removed export: `CONTEXT_WINDOW_TOKENS`
- **Releasable**: `get_context_window()` is importable and callable
- **Tests (TDD)** — `tests/ai/test_constants.py` (new file):
  - Unit: `test_get_context_window_known_model` — `get_context_window("claude-sonnet-4-6")` returns `200_000`
  - Unit: `test_get_context_window_unknown_model_defaults_200k` — `get_context_window("gpt-5")` returns `200_000`
  - Unit: `test_get_context_window_empty_string_defaults_200k` — `get_context_window("")` returns `200_000`
  - Unit: `test_get_context_window_none_model_defaults_200k` — `get_context_window(None)` returns `200_000` (ensures the `None` sentinel used by `self._model` when no model is set returns 200_000 via the `model or ""` internal lookup)
  - Unit: `test_get_context_window_override_takes_precedence` — `get_context_window("claude-sonnet-4-6", {"claude-sonnet-4-6": 1_000_000})` returns `1_000_000`
  - Unit: `test_get_context_window_override_none_uses_constants` — `get_context_window("claude-sonnet-4-6", None)` returns `200_000`
  - Unit: `test_get_context_window_empty_override_dict_uses_constants` — `get_context_window("claude-sonnet-4-6", {})` returns `200_000`
  - Unit: `test_opus_in_available_models` — `"claude-opus-4-6" in AVAILABLE_MODELS`
  - Checkpoint: `uv run pytest tests/ai/test_constants.py -v --no-cov`

---

#### Task 1.2 — Add `context_windows: dict[str, int]` to `ModelsConfig` and config loader
- [x] **File**: `archon/config/loader.py`
- **Depends on**: nothing (parallel to Task 1.1)
- **Description**:
  - Add `context_windows: dict[str, int] = field(default_factory=dict)` to `ModelsConfig` dataclass
  - In the models loader block (≈line 598), parse: `context_windows = dict(models_data.get("context_windows") or {})`
  - Pass `context_windows=context_windows` to `ModelsConfig(...)` constructor call
  - Values are plain ints; validate that each value is a positive integer (`> 0`) — raise `ConfigError` at load time if any value is `<= 0`. This prevents ZeroDivisionError in `context_percentage()`.
- **Releasable**: `config.models.context_windows` is populated from `[models.context_windows]` TOML
- **Tests (TDD)** — `tests/config/test_config_loader.py` (extend existing):
  - Unit: `test_models_config_context_windows_loaded_from_toml` — inline TOML `[models.context_windows]\n"my-model" = 1000000` → `config.models.context_windows == {"my-model": 1_000_000}`
  - Unit: `test_models_config_context_windows_defaults_empty` — `[models]` section without `context_windows` key → `config.models.context_windows == {}`
  - Unit: `test_models_config_context_windows_rejects_nonpositive` — `[models.context_windows]` with `"m" = 0` or `"m" = -1` → raises `ConfigError`
  - Checkpoint: `uv run pytest tests/config/ -v --no-cov`

---

### Phase 2 — Core session: thread overrides and fix context_percentage
> **Releasable**: after Task 2.4 — `context_percentage()` uses model-specific window end-to-end

#### Task 2.1 — Add `context_window_overrides` to `ClaudeSession` and fix `context_percentage`
- [x] **File**: `archon/ai/claude_session.py`
- **Depends on**: Task 1.1
- **Description**:
  - Remove import: `from archon.ai.constants import CONTEXT_WINDOW_TOKENS`
  - Add import: `from archon.ai.constants import get_context_window`
  - Add `context_window_overrides: dict[str, int] | None = None` to `__init__` (after `reminder` param, before close)
  - Store as `self._context_window_overrides: dict[str, int] | None = context_window_overrides`
  - In `usage_stats` property (line ≈508), add key: `"context_window": get_context_window(self._model, self._context_window_overrides)` to the returned dict
  - In `context_percentage()` (line 520), replace `CONTEXT_WINDOW_TOKENS` with `stats["context_window"]`:
    ```python
    def context_percentage(self) -> int:
        stats = self.usage_stats
        if stats is None:
            return 0
        usage = stats.get("usage") or {}
        input_t = usage.get("input_tokens") or 0
        cumul_cc = stats.get("cumulative_cache_creation") or 0
        return round(100 * (cumul_cc + input_t) / stats["context_window"])
    ```
  - Edge case: `self._model` is `None` when no model was explicitly set → `get_context_window(None, ...)` returns 200K default (handled by `model or ""` inside the helper)
- **Releasable**: `ClaudeSession.context_percentage()` uses model-specific window
- **Tests (TDD)** — `tests/ai/test_claude_session.py` (extend existing):
  - Unit: `test_usage_stats_includes_context_window` — after a mocked response, `session.usage_stats["context_window"]` equals `get_context_window(session_model)`
  - Unit: `test_context_percentage_uses_model_specific_window` — create session with `context_window_overrides={"test-model": 1_000_000}`, mock usage of 200K tokens, verify `context_percentage()` returns `20` not `100`
  - Unit: `test_context_percentage_uses_override` — config override for model key → denominator matches override
  - Unit: `test_context_percentage_unknown_model_defaults_200k` — session with model `"unknown"`, no overrides → 200K denominator
  - Checkpoint: `uv run pytest tests/ai/test_claude_session.py -k "context" -v --no-cov`

---

#### Task 2.2 — Thread `context_window_overrides` through `Decomposer`
- [x] **File**: `archon/ai/decomposer.py`
- **Depends on**: Task 2.1
- **Description**:
  - Add `context_window_overrides: dict[str, int] | None = None` to `Decomposer.__init__` (after `router_mcp_headers`, line ≈125)
  - Pass `context_window_overrides=context_window_overrides` to `ClaudeSession(...)` at line ≈134
  - Router session (`self._router_session`) and summary session (`self._summary_session`) do NOT receive overrides — they never call `context_percentage()`; this is intentional
- **Releasable**: Decomposer forwards overrides to its main ClaudeSession
- **Tests (TDD)** — `tests/ai/test_decomposer.py` (extend or create):
  - Unit: `test_decomposer_forwards_overrides_to_session` — construct `Decomposer(context_window_overrides={"m": 1_000_000})`, inspect `decomposer._session._context_window_overrides == {"m": 1_000_000}`
  - Checkpoint: `uv run pytest tests/ai/test_decomposer.py -k "override" -v --no-cov`

---

#### Task 2.3 — Thread `context_window_overrides` through `Pipeline`
- [x] **File**: `archon/ai/pipeline.py`
- **Depends on**: Task 2.2
- **Description**:
  - Add `context_window_overrides: dict[str, int] | None = None` to `Pipeline.__init__` (after `has_background_agents`, line ≈124)
  - Pass `context_window_overrides=context_window_overrides` to `Decomposer(...)` at line ≈136
- **Releasable**: Pipeline forwards overrides through to Decomposer → ClaudeSession
- **Tests (TDD)** — `tests/ai/test_pipeline.py` (extend):
  - Unit: `test_pipeline_forwards_overrides_to_decomposer` — construct `Pipeline(context_window_overrides={"m": 500_000})`, inspect `pipeline._decomposer._session._context_window_overrides == {"m": 500_000}`
  - Checkpoint: `uv run pytest tests/ai/test_pipeline.py -k "override" -v --no-cov`

---

#### Task 2.4 — Thread `context_window_overrides` through `SessionManager` and `gateway`
- [ ] **Files**: `archon/ai/session_manager.py`, `archon/gateway/gateway.py`
- **Depends on**: Task 2.3, Task 1.2
- **Description**:
  - `session_manager.py`: add `context_window_overrides: dict[str, int] | None = None` to `SessionManager.__init__` (after `auto_compact_threshold`), store as `self._context_window_overrides`
  - In `_default_factory` (line ≈85), pass `context_window_overrides=self._context_window_overrides` to `Pipeline(...)`
  - `gateway.py` (line ≈679): add `context_window_overrides=cfg.models.context_windows or None` to the `SessionManager(...)` call. Empty dict (`{}`) is falsy — `or None` ensures we store `None` (not `{}`) when no overrides are configured, avoiding the dual-representation bug
- **Releasable**: full end-to-end path from config.toml → session wired; auto-compact and `/context` both use correct window
- **Tests (TDD)**:
  - `tests/ai/test_session_manager.py`: `test_session_manager_passes_overrides_to_pipeline` — construct `SessionManager(context_window_overrides={"m": 400_000}, ...)`, call `_factory(cwd, uid)`, verify resulting Pipeline has the overrides set
  - `tests/gateway/test_gateway.py` (extend): `test_gateway_converts_empty_context_windows_to_none` — configure `cfg.models.context_windows = {}`, verify `SessionManager` receives `context_window_overrides=None`, not `{}`
  - Integration: `test_context_window_override_end_to_end` — create a config with `context_windows = {"test-model": 500_000}`, wire it through gateway -> SessionManager -> Pipeline -> Decomposer -> ClaudeSession, verify `session.usage_stats["context_window"]` returns `500_000`
  - Checkpoint: `uv run pytest tests/ai/test_session_manager.py -k "override" -v --no-cov`

---

### Phase 3 — Display: fix `/context` command
> **Releasable**: after Task 3.1 — `/context` shows model-correct denominator

#### Task 3.1 — Fix `_fmt_context` in `commands.py` to use `stats["context_window"]`
- [ ] **File**: `archon/chat/commands.py`
- **Depends on**: Task 2.1
- **Description**:
  - Remove `CONTEXT_WINDOW_TOKENS` from the import on line 25
  - In `_fmt_context(stats, notifications)`, replace all three occurrences of `CONTEXT_WINDOW_TOKENS` with `stats.get("context_window", 200_000)`:
    - line 276: `pct = round(100 * total_ctx / stats.get("context_window", 200_000))`
    - line 277: `bar = _progress_bar(total_ctx, stats.get("context_window", 200_000))`
    - line 284: `f"<b>{total_ctx:,} / {stats.get('context_window', 200_000):,} tokens</b>\n\n"`
  - No change to `_fmt_context` signature
  - Replace test `test_fmt_context_uses_shared_constant` (tests/chat/test_commands.py:3093) with two new tests (see below) — the old test verified a coupling that no longer exists
- **Releasable**: `/context` display denominator is model-aware
- **Tests (TDD)** — `tests/chat/test_commands.py`:
  - Unit: `test_fmt_context_uses_context_window_from_stats` — pass stats dict with `context_window=400_000`; verify `"400,000"` appears in output (replaces deleted `test_fmt_context_uses_shared_constant`)
  - Unit: `test_fmt_context_missing_context_window_defaults_200k` — pass stats dict without `context_window` key; verify `"200,000"` in output
  - Unit: `test_fmt_context_custom_window_shown_in_denominator` — `context_window=1_000_000` → `"1,000,000"` in the `tokens` line
  - Checkpoint: `uv run pytest tests/chat/test_commands.py -k "fmt_context" -v --no-cov`

---

### Phase 4 — release.sh model sync
> **Releasable**: after Task 4.1 — `release.sh` auto-updates `AVAILABLE_MODELS` on each release

#### Task 4.1 — Add `AVAILABLE_MODELS` sync step to `release.sh`
- [ ] **File**: `release.sh`
- **Depends on**: Task 1.1
- **Description**:
  - Insert new block between the README update (line 73) and the git-add step (line 77)
  - Calls committed helper `scripts/update_models.py` (no tempfile, no shell injection risk)
  - Python script (`scripts/update_models.py`) reads JSON from stdin; filters `claude-*` model IDs; excludes `-latest` suffixed aliases; sorts; rewrites `AVAILABLE_MODELS = [...]` in `constants.py` using `[^\]]*` regex (single-line lists only — warns if pattern not matched); writes only if changed
  - Only stages `constants.py` if the file was actually modified (`git diff --quiet` check)
  - Non-fatal: API call failure or missing `ANTHROPIC_API_KEY` prints a warning and skips silently
  - `--dry-run` mode: the entire sync block is skipped using `if ! $DRY_RUN; then` to match the existing `release.sh` convention (`DRY_RUN` is set to `false`/`true`, not empty/non-empty); a `[dry-run] Skipping AVAILABLE_MODELS sync` message is printed
  - The Python rewrite logic lives in `scripts/update_models.py` (committed to the repo) so it can be unit-tested without shell execution. `release.sh` calls it via: `echo "$MODELS_JSON" | python3 scripts/update_models.py`
  - Full block:
    ```bash
    if ! $DRY_RUN; then
      # ─── sync AVAILABLE_MODELS from Anthropic API ───────────────────
      if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
        echo "Syncing AVAILABLE_MODELS from Anthropic API..."
        MODELS_JSON=$(curl -s -f \
          -H "x-api-key: ${ANTHROPIC_API_KEY}" \
          -H "anthropic-version: 2023-06-01" \
          "https://api.anthropic.com/v1/models" 2>/dev/null) || MODELS_JSON=""
        if [ -n "$MODELS_JSON" ]; then
          echo "$MODELS_JSON" | python3 scripts/update_models.py \
            || echo "Warning: model update script failed — continuing"
          if ! git diff --quiet archon/ai/constants.py; then
            run "git add archon/ai/constants.py"
            ok "Staged updated constants.py"
          fi
        else
          echo "Warning: Anthropic API call failed — skipping AVAILABLE_MODELS sync"
        fi
      else
        echo "Warning: ANTHROPIC_API_KEY not set — skipping AVAILABLE_MODELS sync"
      fi
    else
      echo "[dry-run] Skipping AVAILABLE_MODELS sync"
    fi
    ```

  `scripts/update_models.py` content:
  ```python
  # scripts/update_models.py — called by release.sh to sync AVAILABLE_MODELS
  import sys, json, re, pathlib

  data = json.load(sys.stdin)
  ids = sorted(set(
      m['id'] for m in data.get('data', [])
      if m['id'].startswith('claude-') and not m['id'].endswith('-latest')
  ))
  model_list = '[' + ', '.join(f'"{i}"' for i in ids) + ']'
  f = pathlib.Path('archon/ai/constants.py')
  txt = f.read_text()
  new_txt = re.sub(r'AVAILABLE_MODELS\s*=\s*\[[^\]]*\]', f'AVAILABLE_MODELS = {model_list}', txt)
  if new_txt != txt:
      f.write_text(new_txt)
      print(f"Updated AVAILABLE_MODELS: {model_list}")
  elif 'AVAILABLE_MODELS' in txt:
      print("AVAILABLE_MODELS already up to date")
  else:
      print("Warning: AVAILABLE_MODELS pattern not found in constants.py — no update performed", file=sys.stderr)
      sys.exit(1)
  ```
- **Releasable**: `release.sh` keeps `AVAILABLE_MODELS` current on every release
- **Tests** — `tests/scripts/test_update_models.py` (new file):
  - Unit: `test_update_models_replaces_available_models` — feed mock JSON `{"data": [{"id": "claude-foo-1"}, {"id": "claude-bar-2"}]}` and a sample `constants.py` content; verify output has `AVAILABLE_MODELS = ["claude-bar-2", "claude-foo-1"]` (sorted)
  - Unit: `test_update_models_excludes_latest_aliases` — model IDs ending in `-latest` are excluded from the output
  - Unit: `test_update_models_no_change_returns_unchanged` — feed JSON matching the existing list; verify the output equals the input (no spurious write)
  - Unit: `test_update_models_ignores_non_claude_models` — model IDs not starting with `claude-` are excluded
  - Checkpoint: `uv run pytest tests/scripts/test_update_models.py -v --no-cov`
  - Manual: `bash release.sh --dry-run` (no ANTHROPIC_API_KEY → prints warning, no crash)

---

### Phase 5 — Documentation and config
> **Releasable**: after this phase — operators can configure custom model window sizes

#### Task 5.1 — Update `examples/config.toml.example` and tech-debt roadmap
- [ ] **Files**: `examples/config.toml.example`, `Documentation/Architecture/530_technical_debt_refactoring_roadmap.md`
- **Depends on**: Task 1.2
- **Description**:
  - `config.toml.example`: add `"claude-opus-4-6"` to the `available` list (line 226); add commented `[models.context_windows]` block after the `default` line:
    ```toml
    # Optional: override the context window size for custom or proxy models.
    # Built-in claude-* models are set automatically from archon/ai/constants.py.
    # Only set this when using a non-standard model endpoint with a different limit.
    # [models.context_windows]
    # "my-proxy-model" = 1_000_000
    ```
  - `530_technical_debt_refactoring_roadmap.md`: mark TD.029 row as resolved (add ` ✅ FEAT-024` to the description cell and update the summary list item at line 222)
- **Releasable**: documentation reflects the new feature
- **Tests**: N/A (documentation only)
  - Checkpoint: `uv run pytest --no-cov` (full suite — verify no regressions)
