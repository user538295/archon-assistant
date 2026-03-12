# Bug 22 — Pipeline Routing Log Shows Empty Model Field

Status: RESOLVED

## Description

The Pipeline routing log entry consistently shows `Model: ` with an empty/blank value. The model name being used for inference is never recorded in the routing log, making it impossible to audit which model handled a given message from the history file alone.

## Observed in log: 2026-03-12

Every Pipeline routing entry in the session log has a blank model field:

```
### Pipeline · 16:25:56 UTC

Routing: direct chat response
Model:

### Pipeline · 18:47:55 UTC

Routing: direct task response
Model:
```

This occurs for both `chat` and `task` routing paths. The Classifier log correctly records the model used (`model: claude-haiku-4-5-20251001`), but the Pipeline log does not.

## Root Cause (verified 2026-03-13)

The original fix (commit 49880d9) added a redundant `self._model` field to Pipeline and a fallback `self._model or self._decomposer.model` in the `model` property. DA review correctly identified this as a no-op: the model IS always propagated through `Pipeline(model=...) -> Decomposer(model=...) -> ClaudeSession(model=...)`, so `Pipeline._model` and `self._decomposer.model` always hold the same value.

**The real root cause was config-level**: `ModelsConfig.default` is `str | None = None`. When the user's `config.toml` listed `[models] available = [...]` but omitted the `default` key, `config.models.default` was `None`. The gateway code (`if cfg.models.default: session_manager.set_model(...)`) is falsy for `None`, so `SessionManager._model` stayed `None`. This `None` propagated through the entire chain: `Pipeline(model=None)` -> `Decomposer(model=None)` -> `ClaudeSession(model=None)`, making `Pipeline.model` return `None` and `_routing_event()` convert it to `""`.

## Fix Applied

1. **Config validation** (`archon/config/loader.py`): When `models.available` is non-empty but `models.default` is `None`, automatically set `default = available[0]`. This ensures the model name is always available when the user has configured a models list.

2. **Removed redundant `self._model` from Pipeline** (`archon/ai/pipeline.py`): Reverted the `model` property to simply delegate to `self._decomposer.model`. The redundant field was a symptom-level fix that masked the real config issue.

3. **Updated e2e tests** (`tests/ai/test_bugs_e2e.py`): Tests now set `mock_decomposer.model = EXPECTED_MODEL` (reflecting correct propagation) instead of `None` (which was the bug symptom, not a valid production state after the config fix).

## Symptoms

- Every `### Pipeline` history entry shows `Model: ` with nothing after the colon
- Affects both `chat` and `task` routing
- Classifier entries correctly show model — inconsistency between the two log sections
