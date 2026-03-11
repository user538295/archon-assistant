# Bug 09 — Repeated model warnings in logs: claude-haiku-4-5-20251001 not in config.models.available

Status: FIXED

## Description

From archon.log on startup:
```
2026-03-10 18:59:42,729 archon WARNING Classifier model 'claude-haiku-4-5-20251001' not in config.models.available — update DEFAULT_FAST_MODEL in archon/ai/constants.py
2026-03-10 18:59:42,731 archon WARNING Decomposer summarizer model 'claude-haiku-4-5-20251001' not in config.models.available — update DEFAULT_FAST_MODEL in archon/ai/constants.py
2026-03-10 16:28:59,670 archon WARNING HistoryCompactor model 'claude-haiku-4-5-20251001' not in config.models.available — update DEFAULT_FAST_MODEL in archon/ai/constants.py
```

The `DEFAULT_FAST_MODEL` constant in `archon/ai/constants.py` is set to `claude-haiku-4-5-20251001` but this model ID is not in `config.models.available`.

## Root cause

The model ID `claude-haiku-4-5-20251001` was recently updated/changed and the config was not updated to match. Either:
1. The `DEFAULT_FAST_MODEL` constant needs updating to the current Haiku model ID
2. Or the `config.models.available` list needs to include the Haiku model

## Tasks

1. Check `archon/ai/constants.py` for DEFAULT_FAST_MODEL value
2. Check `~/.archon/config.toml` for `models.available` list
3. Determine the correct current Haiku model ID (check Claude docs or SDK)
4. Update `DEFAULT_FAST_MODEL` in constants.py OR update config.toml
5. Verify warnings disappear
6. Write test if possible

## Resolution

**Root cause**: `~/.archon/config.toml` had `"claude-haiku-4-5"` (short alias) in `models.available`, but `DEFAULT_FAST_MODEL` in `archon/ai/constants.py` is `"claude-haiku-4-5-20251001"` (full versioned ID). The warning logic does an exact string match, so the two strings never matched.

**Fix**: Added `"claude-haiku-4-5-20251001"` to `models.available` in `~/.archon/config.toml`. The model ID in `constants.py` is correct per Anthropic docs — the config was simply missing the versioned alias.

**Tests added**: `tests/ai/test_constants.py` — 4 new tests covering:
- Warning emitted when `DEFAULT_FAST_MODEL` is not in `config.models.available` (Classifier, HistoryCompactor)
- No warning when `DEFAULT_FAST_MODEL` is in `config.models.available` (Classifier, HistoryCompactor)

All 2296 tests pass.

## DA Review (2026-03-11)

**Verdict: Fix is correct for the immediate symptom. Two concerns noted below.**

### Evidence

1. **Config fix verified** -- `~/.archon/config.toml` line 41 now includes `"claude-haiku-4-5-20251001"` in `models.available` alongside the short alias `"claude-haiku-4-5"`. The exact-match warning check in `Classifier.__init__` (line 42-47), `Decomposer.__init__` (line 80-85), and `HistoryCompactor.__init__` (line 165-170) will now find the model in the list and skip the warning.

2. **`DEFAULT_FAST_MODEL` value is correct** -- `archon/ai/constants.py` sets it to `"claude-haiku-4-5-20251001"`. This is the versioned model ID for Claude Haiku 4.5 per Anthropic's model naming convention. The value is sound.

3. **Shared constant verified** -- Tests confirm `_CLASSIFIER_MODEL`, `_SUMMARIZER_MODEL`, and `_HAIKU_MODEL` all point to the same `DEFAULT_FAST_MODEL` constant via `is` identity checks (`test_constants.py` lines 28-39). A future change to the constant propagates everywhere.

4. **Warning/no-warning tests are sound** -- The 4 regression tests (`test_classifier_warns_when_model_not_in_available`, `test_classifier_no_warning_when_model_in_available`, `test_history_compactor_warns_when_model_not_in_available`, `test_history_compactor_no_warning_when_model_in_available`) properly mock `config.models.available` and assert on caplog records. The mock approach (`patch.object(cfg_module, "config", ...)`) correctly targets the lazy `from archon.config import config` inside each `__init__`.

### Concerns

**[MINOR] Missing Decomposer warning test** -- The bug manifested in three modules: Classifier, Decomposer, and HistoryCompactor (all three are shown in the original log excerpt). The fix added regression tests for Classifier and HistoryCompactor but not for Decomposer. The Decomposer `__init__` at line 80-85 of `archon/ai/decomposer.py` has the same warning pattern. The shared-constant identity test (`test_decomposer_uses_shared_constant`) confirms the constant is shared, which provides indirect coverage -- but a direct warning emission test for Decomposer would complete the set. Low risk since the pattern is identical, but it is an asymmetry.

**[INFO] Config fix is runtime-only, not committed** -- The fix was applied to `~/.archon/config.toml` (user's runtime config), not to a project-level default config file. This is the correct approach since `config.toml` is user-specific and not checked into git. However, if another user deploys Archon and forgets to add the versioned model ID to their `models.available`, they will see the same warnings. The warning message text ("update DEFAULT_FAST_MODEL in archon/ai/constants.py") is misleading in that scenario -- it tells users to change the source code constant rather than their config. This is a pre-existing UX issue in the warning message, not introduced by this fix.

**[INFO] Test count discrepancy** -- The doc claims "All 2296 tests pass" but the codebase contains approximately 1815 test function definitions (including live/e2e tests). Parametrized tests can inflate the count, so this is likely accurate at runtime. Not an issue, just noting the difference between function count and test-item count.

## DA Follow-up Fixes (2026-03-11)

Two issues from the DA review were addressed:

**Issue 1 — Misleading warning message text (fixed)**

All three warning messages pointed users to `archon/ai/constants.py` ("update DEFAULT_FAST_MODEL in archon/ai/constants.py"), but the correct fix is to update `~/.archon/config.toml`. The messages in Classifier, Decomposer, and HistoryCompactor were updated to say: "add it to models.available in config.toml".

Files changed:
- `archon/ai/classifier.py` line 45
- `archon/ai/decomposer.py` line 84
- `archon/ai/history_compactor.py` line 168

**Issue 2 — Missing Decomposer warning test (fixed)**

Two new tests added to `tests/ai/test_constants.py`:
- `test_decomposer_warns_when_model_not_in_available`
- `test_decomposer_no_warning_when_model_in_available`

These mirror the existing Classifier and HistoryCompactor test pairs, completing the set for all three warning sources. All 11 tests in `test_constants.py` pass.

## AI Notes
