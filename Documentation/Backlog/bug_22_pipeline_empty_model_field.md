# Bug 22 — Pipeline Routing Log Shows Empty Model Field

Status: OPEN

## Description

The Pipeline routing log entry consistently shows `Model: ` with an empty/blank value. The model name being used for inference is never recorded in the routing log, making it impossible to audit which model handled a given message from the history file alone.

## Observed in log: 2026-03-12

Every Pipeline routing entry in the session log has a blank model field:

```
### 🔀 Pipeline · 16:25:56 UTC

Routing: direct chat response
Model:

### 🔀 Pipeline · 18:47:55 UTC

Routing: direct task response
Model:
```

This occurs for both `chat` and `task` routing paths. The Classifier log correctly records the model used (`model: claude-haiku-4-5-20251001`), but the Pipeline log does not.

## Expected Behaviour

The Pipeline routing log entry should include the model name that will be (or was) used for the response. For example:
```
### 🔀 Pipeline · 16:25:56 UTC

Routing: direct chat response
Model: claude-sonnet-4-6
```

## Likely Root Causes

1. **Model name not passed to logger**: When the Pipeline records the routing decision, the model name variable may be `None`, an empty string, or not yet resolved at the point the log is written.

2. **Timing issue**: The model field may be recorded before the session has resolved which model will be used (e.g., if model selection is lazy or comes from a downstream session property).

3. **Default model not populated**: If the user has not explicitly set a model, the default may not be resolved into a string before it is written to the history log.

## Symptoms

- Every `### 🔀 Pipeline` history entry shows `Model: ` with nothing after the colon
- Affects both `chat` and `task` routing
- Classifier entries correctly show model — inconsistency between the two log sections

## Tasks

1. Read `archon/ai/pipeline.py` — find the routing log / history write for Pipeline entries
2. Find where the model name is resolved and passed to the log writer
3. Write failing e2e test confirming empty model field in pipeline log
4. Fix: resolve model name before writing the Pipeline routing log entry
5. Run full test suite
