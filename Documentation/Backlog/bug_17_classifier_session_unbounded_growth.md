# Bug 17 — Classifier session never recycled — unbounded SDK history accumulation

Status: FIXED

## Description

The `Classifier` creates one `ClaudeSession(max_turns=1)` at construction and reuses it for every `classify()` call across the entire daemon lifetime. Unlike the Decomposer's orch session (reset every 20 calls via `_reset_orch_if_needed`) and summary session (reset every 30 calls), the classifier session has NO recycling mechanism.

`max_turns=1` limits tool-use loops WITHIN a single `send()` call — it does NOT prevent the SDK from accumulating conversation history across calls. Each `session.send(prompt)` appends the prompt and JSON classification response to the SDK's internal conversation history. After 100+ messages, the classifier carries 100+ prior classifications as context.

## Observed symptoms

- Classification latency increases monotonically over a long session
- Token cost per classification increases over time (larger context per call)
- Eventually the classifier's context window fills (Haiku's limit: 200k tokens), potentially causing:
  - SDK truncation with unpredictable results
  - SDK errors that trigger the 30s timeout fallback (Bug 07)
  - Degraded classification quality as recent messages drown in historical ones

## Root cause

In `archon/ai/classifier.py`:

```python
class Classifier:
    def __init__(self, ...):
        self._session = ClaudeSession(max_turns=1, ...)  # created once

    async def classify(self, prompt: str) -> ClassifierResult:
        ...
        async for event in self._session.send(prompt):  # reused forever
            ...
```

Contrast with `Decomposer._reset_orch_if_needed()` (orch resets every 20 calls) and `_refresh_summary()` (summary resets every 30 calls). The classifier — which runs on EVERY message — has no equivalent reset.

## Why Bug 07 fix didn't address this

Bug 07 added a 30-second timeout for classification, which treats the SYMPTOM (slow classification due to large context) but not the CAUSE (unbounded context growth). After enough messages, even 30 seconds won't be sufficient.

## Reproduction scenario

1. Start Archon and send 200 messages (any mix of chat and task).
2. Each classify() call accumulates one more exchange in the classifier's session.
3. After 200 calls, the classifier session contains 200 prompt/response pairs.
4. Classification latency may grow from ~2s (fresh) to 5-10s+ as input tokens grow.
5. After ~1000 calls (depending on prompt lengths), the context window overflows.

## Tasks

1. Read `archon/ai/classifier.py` and verify there is no reset mechanism
2. Write a failing test:
   - Call `classify()` N times (where N = reset threshold)
   - Verify that `session.stop()` is called and a new session is started after N calls
   - Currently fails because no reset happens
3. Add a call counter and reset threshold (`_CLASSIFIER_RESET_THRESHOLD = 50`) to Classifier
4. Implement a `_reset_session()` method that stops the current session and starts a fresh one
5. Before stopping the old session, accumulate its cost into a carryover field so `usage_stats` remains accurate across resets
6. Run full test suite

## AI Notes

### Fix approach (2026-03-11)

Following the same pattern as `Decomposer._reset_orch_if_needed()`:

```python
_CLASSIFIER_RESET_THRESHOLD = 50  # reset after 50 classify() calls

class Classifier:
    def __init__(self, ...):
        self._classify_call_count = 0
        self._cost_carryover = 0.0  # preserve cost across resets

    async def classify(self, prompt: str) -> ClassifierResult:
        self._classify_call_count += 1
        if self._classify_call_count >= _CLASSIFIER_RESET_THRESHOLD:
            await self._reset_session()
            self._classify_call_count = 0
        ...

    async def _reset_session(self) -> None:
        # Accumulate cost before stopping old session
        stats = self._session.usage_stats or {}
        self._cost_carryover += stats.get("total_cost_usd", 0.0)
        old = self._session
        self._session = ClaudeSession(max_turns=1, ...)
        await old.stop()
        await self._session.start()
```

### Fix applied (2026-03-11)

**Changes**: `archon/ai/classifier.py`:
- Added `_CLASSIFIER_RESET_THRESHOLD = 50` constant
- Added `_classify_call_count`, `_carried_cost_usd`, `_carried_cache_creation` to `__init__`
- Added `_reset_session()`: saves old session cost into carryover, creates fresh session, stops old
- `classify()`: increments counter, calls `_reset_session()` at threshold
- `usage_stats`: merges current session stats with carryover accumulators

**Tests added** in `tests/ai/test_classifier.py`:
- `test_session_recycled_after_threshold`
- `test_session_recycled_exactly_at_threshold`
- `test_usage_stats_survive_session_reset`

All 2361 tests pass.
