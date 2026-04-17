# FIX-030 — Classifier zero confidence (three failure modes)
**Purpose**: Fix the three root causes that produce `{"intent": "task", "confidence": 0.0}` with "no JSON object found in response"
**Audience**: Internal maintainers
**Status**: To Do

---

## Background

Three distinct failure modes all produce the same observable symptom — classifier returns `confidence=0.0` with
`"no JSON object found in response"`:

- **Mode A** — SDK delivers valid JSON in an `AssistantMessage` `TextBlock`, but `ResultMessage.result` is
  empty/None (triggered by `RateLimitEvent`). `event_mapper.py` discards `TextBlock` content at line 268–271,
  so the classifier never receives the JSON.

- **Mode B** — Session history accumulation causes the model to generate narrative prose instead of JSON.
  The persistent classifier session accumulates history across up to 50 calls; prior error recoveries contaminate
  the context window and shift the model away from JSON-only output.

- **Mode C** — After a crash (e.g., the Bug 8 JSON buffer overflow), the classifier session is corrupted:
  `ToolStarted`/`ToolResult` events leak into a session configured with `tools=[]`. The model produces
  prose and the parse fails.

See `Documentation/Backlog/bug_investigation_01_classifier_zero_confidence.md` for the full log evidence,
option analysis, and rationale.

## Goal

After this fix, all three modes are eliminated:
- Mode A: the classifier falls back to the `TextBlock` content when `ResultMessage.result` is empty.
- Modes B + C: each `classify()` call uses a fresh `ClaudeSession` — no history accumulation, no session
  corruption. Usage stats still accumulate correctly across calls for `Pipeline.usage_stats` reporting.

---

## Scope

### In Scope
- Making `Classifier` stateless (fresh session per `classify()` call) — eliminates Modes B and C
- Adding `TextBlockEvent` to `event_mapper.py` and a TextBlock fallback in `classify()` — fixes Mode A
- Improved diagnostic logging in `classification.py` and `classifier.py`

### Out of Scope
- The underlying SDK session-resume corruption mechanism (separate investigation)
- The `event_mapper.py` falsy guard (`elif message.result:`) code-quality fix — no observed symptom
- Retry logic for transient SDK failures

---

## Acceptance Criteria

- [ ] **Mode A**: When the SDK returns `AssistantMessage(TextBlock('{"intent": "task", "confidence": 0.95}'))` followed
  by `ResultMessage(result=None)`, `classify()` returns `confidence == 0.95`.
- [ ] **Mode B + C**: Each `classify()` call creates a new `ClaudeSession` instance — verified by test asserting
  two separate calls produce two separate session objects.
- [ ] **Usage stats**: `Classifier.usage_stats` reports cumulative cost across all per-call sessions — same
  semantics as before (matches `test_usage_stats_survive_session_reset`).
- [ ] **No regression**: `test_result_message_with_none_result_produces_no_response` (event_mapper line 249)
  still passes — `ResultMessage(result=None)` still produces no `Response` event.
- [ ] **Logging (Mode A)**: When `raw_response` is empty after the event loop, a distinct WARNING is logged:
  `"Classifier received no Response event — raw_response empty"`.
- [ ] **Logging (Mode B/C)**: When `parse_classification("")` is called, the log says `"empty response —
  no content from model"` rather than `"no JSON object found in response"`.
- [ ] All existing tests pass.

---

## What does NOT change

- `parse_classification()` return type and default behaviour — still returns `Classification(intent="task", confidence=0.0)` on failure
- `EventMapper` handling of `ResultMessage` — `result=None` still produces no `Response` event
- `Classifier.usage_stats` dict shape — `total_cost_usd` and `cumulative_cache_creation` keys
- `Pipeline.start()` / `Pipeline.stop()` call signatures
- All other consumers of `EventMapper` events — they simply ignore the new `TextBlockEvent`

---

## Known limitations / accepted trade-offs

- **Per-call cold start**: each `classify()` call creates and destroys a `ClaudeSession`. For Haiku with
  `max_turns=1` this overhead is negligible in practice.
- **No session warmup caching**: the persistent-session prompt-caching benefit is lost. Accepted — correctness
  over marginal cache savings.
- **Mode C root cause not fixed here**: the SDK session-resume corruption remains. This plan eliminates Mode C
  *symptomatically* (no persistent session to corrupt) but not the underlying SDK defect.

---

## Architecture

### New dataclass: `TextBlockEvent` (event_mapper.py)
```python
@dataclass(frozen=True, slots=True)
class TextBlockEvent:
    content: str
    source: str = "orchestrator"
```
Added to the `Event` union type. `EventMapper._map()` yields `TextBlockEvent(content=block.text)` instead of
discarding `AssistantMessage` `TextBlock`s. All existing consumers ignore it (no match in their event handlers).

### Stateless `Classifier` (classifier.py)
- `__init__` no longer creates a `ClaudeSession`. Stores: `_cwd`, `_search_url`, `_carried_cost_usd`,
  `_carried_cache_creation`. Removes: `_session`, `_call_count`, `_CLASSIFIER_RESET_THRESHOLD`, `_reset_session()`.
- `classify()` creates a fresh `ClaudeSession(model=_CLASSIFIER_MODEL, tools=[], max_turns=1,
  disable_thinking=True, cwd=self._cwd, search_url=self._search_url)`, calls `await session.start()`,
  iterates events, calls `await session.stop()`, accumulates stats into `_carried_cost_usd` /
  `_carried_cache_creation`.
- `start()` / `stop()` become no-ops (no persistent session to manage).

### TextBlock fallback in `classify()` (classifier.py)
After the event loop, if `raw_response` is empty and any `TextBlockEvent` was received, call
`extract_json_object()` on each captured TextBlock content (in order) and use the first that returns
valid JSON as `raw_response`.

### Connection to existing components
- `Pipeline` calls `classifier.start()` / `classifier.stop()` at lines 173 and 179 — these become no-ops,
  no change needed in `pipeline.py`.
- `Pipeline.usage_stats` (line 605) reads `Classifier.usage_stats` — the `_carried_*` accumulation
  preserves this interface.
- `event_mapper.map_messages()` is unchanged in contract; `TextBlockEvent` is additive to the `Event` union.

---

## Tests

- **test_classify_creates_fresh_session_per_call** (unit): two `classify()` calls produce two separate
  `ClaudeSession` constructor invocations.
- **test_classify_accumulates_cost_across_calls** (unit): after two calls with mocked session costs,
  `usage_stats["total_cost_usd"]` equals the sum.
- **test_usage_stats_survive_session_reset** (unit — existing, must still pass): cumulative stats
  accumulate correctly across multiple calls.
- **test_start_is_noop** (unit): `await classifier.start()` does not create a `ClaudeSession`.
- **test_stop_is_noop** (unit): `await classifier.stop()` does not call any session method.
- **test_classify_textblock_fallback_when_no_response** (unit): SDK returns `TextBlock('{"intent": "task",
  "confidence": 0.95}')` + `ResultMessage(result=None)` → `classification.confidence == 0.95`.
- **test_classify_textblock_fallback_ignores_prose** (unit): TextBlock contains prose + `ResultMessage(result=None)`
  → fallback extraction fails → `confidence == 0.0`.
- **test_classify_textblock_fallback_takes_first_valid** (unit): multiple TextBlocks, first has valid JSON
  → that JSON is used.
- **test_classify_logs_warning_on_empty_raw_response** (unit): when `raw_response` is empty after event loop,
  logger emits WARNING containing "no Response event".
- **test_text_block_in_assistant_message_yields_text_block_event** (unit): `EventMapper._map()` yields
  `TextBlockEvent` for `AssistantMessage` containing `TextBlock`.
- **test_text_block_event_content_preserved** (unit): `TextBlockEvent.content` equals the `TextBlock.text`.
- **test_text_block_event_in_event_union** (unit): `TextBlockEvent` is a valid member of the `Event` union.
- **test_parse_classification_empty_string_logs_distinct_message** (unit): `parse_classification("")` logs
  "empty response" (not "no JSON object found").
- **test_parse_classification_non_json_prose_logs_distinct_message** (unit): `parse_classification("You're right")`
  logs "no JSON object found in response".
- **test_result_message_with_none_result_produces_no_response** (unit — existing, must still pass): no regression.
- **test_session_recycled_after_threshold** (unit — existing, DELETED/REPLACED): replaced by
  `test_classify_creates_fresh_session_per_call`.
- **test_session_recycled_exactly_at_threshold** (unit — existing, DELETED/REPLACED): replaced above.

---

## Documentation update

- [ ] `Documentation/Architecture/180_search_architecture.md`: N/A (classifier not covered there)
- [ ] `Documentation/Backlog/bug_investigation_01_classifier_zero_confidence.md`: mark resolved after merge

---

## Task breakdown

### Phase 1 — Stateless classifier (eliminates Modes B and C)
> **Releasable**: after Task 1.3 — each `classify()` call is independent; no session corruption possible.

#### Task 1.1 — Strip persistent session from `Classifier.__init__()`
- [ ] **File**: `archon/ai/classifier.py`
- **Depends on**: nothing
- **Description**:
  - Remove `self._session: ClaudeSession` field and its construction call in `__init__()` (lines 42–55).
  - Remove `self._call_count: int = 0` field.
  - Remove the `_CLASSIFIER_RESET_THRESHOLD = 50` module-level constant (line 19).
  - Remove the `_reset_session()` method entirely (lines 85–110).
  - Keep: `self._cwd`, `self._search_url`, `self._carried_cost_usd: float = 0.0`,
    `self._carried_cache_creation: float = 0.0`.
  - `__init__` signature unchanged: `def __init__(self, cwd: str | None = None, search_url: str | None = None) -> None`.
  - `usage_stats` property (lines 61–77): unchanged — still returns `{"total_cost_usd": ..., "cumulative_cache_creation": ...}` summing `_carried_*` fields. With a stateless classifier `_session` no longer exists, so remove the `+ self._session.usage_stats[...]` additions and use only `_carried_*`.
- **Releasable**: after this task, the module compiles without `_reset_session` or threshold logic.
- **Tests (TDD)** — `tests/ai/test_classifier.py`:
  - Unit: `test_init_has_no_session` — `Classifier()` has no `_session` attribute.
  - Unit: `test_start_is_noop` — `await classifier.start()` completes without creating a session.
  - Unit: `test_stop_is_noop` — `await classifier.stop()` completes without calling any session method.
  - Unit: `test_usage_stats_returns_carried_zeros_initially` — fresh classifier has `total_cost_usd == 0.0`.
  - Delete/replace: `test_session_recycled_after_threshold`, `test_session_recycled_exactly_at_threshold`.
  - Checkpoint: `uv run pytest tests/ai/test_classifier.py -k "noop or no_session or carried_zeros or recycled" -v`

#### Task 1.2 — Create fresh `ClaudeSession` per `classify()` call
- [ ] **File**: `archon/ai/classifier.py`
- **Depends on**: Task 1.1
- **Description**:
  - Replace the existing event loop in `classify()` (lines 112–152) with:
    1. Create `session = ClaudeSession(model=_CLASSIFIER_MODEL, tools=[], max_turns=1, disable_thinking=True, cwd=self._cwd, search_url=self._search_url)`.
    2. `await session.start()`.
    3. Iterate `async for event in session.send(prompt)` — collect `Response.content` into `raw_response`, non-Response events into `result_events` list.
    4. `await session.stop()`.
    5. Accumulate stats: `self._carried_cost_usd += session.usage_stats.get("total_cost_usd", 0.0)` and `self._carried_cache_creation += session.usage_stats.get("cumulative_cache_creation", 0.0)`.
  - Remove threshold check (`if self._call_count >= _CLASSIFIER_RESET_THRESHOLD`).
  - Remove `self._call_count += 1`.
  - Wrap step 3–4 in `try/finally` so `session.stop()` is always called even on exception.
  - `classify()` signature unchanged: `async def classify(self, prompt: str) -> ClassifierResult`.
- **Releasable**: after this task, each classify call is fully independent with no session history carryover.
- **Tests (TDD)** — `tests/ai/test_classifier.py`:
  - Unit: `test_classify_creates_fresh_session_per_call` — mock `ClaudeSession`; two `classify()` calls → two constructor invocations.
  - Unit: `test_classify_accumulates_cost_across_calls` — mock session with `usage_stats={"total_cost_usd": 0.01}` × 2 calls → `usage_stats["total_cost_usd"] == 0.02`.
  - Unit: `test_usage_stats_survive_session_reset` (existing) — must still pass with accumulated carried values.
  - Unit: `test_classify_stops_session_on_exception` — if `session.send()` raises, `session.stop()` still called.
  - Unit: `test_classify_returns_valid_classification` (existing) — must still pass.
  - Unit: `test_classify_returns_default_on_bad_json` (existing) — must still pass.
  - Checkpoint: `uv run pytest tests/ai/test_classifier.py -v`

#### Task 1.3 — Make `start()` / `stop()` no-ops; verify Pipeline wiring
- [ ] **File**: `archon/ai/classifier.py`
- **Depends on**: Task 1.2
- **Description**:
  - `start()`: `async def start(self) -> None: pass` — remove `await self._session.start()`.
  - `stop()`: `async def stop(self) -> None: pass` — remove `await self._session.stop()`.
  - No changes to `archon/ai/pipeline.py` — it already calls `classifier.start()` / `classifier.stop()` which are now no-ops; interface is preserved.
  - Verify `Pipeline.usage_stats` (line 605 of `pipeline.py`) still reads `self._classifier.usage_stats` without error — `_carried_*` fields satisfy the same dict contract.
- **Releasable**: after this task, the stateless classifier is fully wired and Pipeline lifecycle works correctly.
- **Tests (TDD)** — `tests/ai/test_classifier.py`, `tests/ai/test_pipeline.py`:
  - Unit: `test_start_is_noop` — `start()` returns None without side effects (no mock needed).
  - Unit: `test_stop_is_noop` — `stop()` returns None without side effects.
  - Integration: `test_pipeline_start_stop_with_stateless_classifier` — `Pipeline.start()` / `Pipeline.stop()` complete without error.
  - Checkpoint: `uv run pytest tests/ai/test_classifier.py tests/ai/test_pipeline.py -v`

---

### Phase 2 — TextBlock fallback (fixes Mode A)
> **Releasable**: after Task 2.2 — SDK-delivered JSON in TextBlock is recovered without retries.

#### Task 2.1 — Add `TextBlockEvent` dataclass and yield from `EventMapper`
- [ ] **File**: `archon/ai/event_mapper.py`
- **Depends on**: nothing (independent of Phase 1)
- **Description**:
  - Add `TextBlockEvent` dataclass after `ErrorEvent` (around line 80):
    ```python
    @dataclass(frozen=True, slots=True)
    class TextBlockEvent:
        content: str
        source: str = "orchestrator"
    ```
  - Add `TextBlockEvent` to the `Event` union type (line 208–227).
  - In `_map()` (line 268–271): replace the `logger.debug("TextBlock in AssistantMessage discarded...")` branch with `yield TextBlockEvent(content=block.text)`. Remove the debug discard log.
  - All existing consumers of events (handler, decomposer, background agents, voice, formatter) have exhaustive event handlers or ignore unknown events — no changes needed.
  - Existing test `test_text_block_in_assistant_message_produces_no_events` (line 304–318) must be updated (see tests below).
- **Releasable**: after this task, `TextBlockEvent` is available in the event stream for any consumer to capture.
- **Tests (TDD)** — `tests/ai/test_event_mapper.py`:
  - Unit: `test_text_block_in_assistant_message_yields_text_block_event` — `AssistantMessage(content=[TextBlock("hello")])` → events contains one `TextBlockEvent(content="hello")`.
  - Unit: `test_text_block_event_content_preserved` — `TextBlockEvent.content == block.text`.
  - Unit: `test_text_block_event_in_event_union` — `isinstance(TextBlockEvent("x"), Event)` is True (via `get_args`).
  - Update: `test_text_block_in_assistant_message_produces_no_events` → renamed to `test_text_block_in_assistant_message_yields_text_block_event` (existing test was asserting `== []`, now asserts single `TextBlockEvent`).
  - Unit: `test_result_message_with_none_result_produces_no_response` (existing, line 249) — must still pass (no regression on ResultMessage handling).
  - Checkpoint: `uv run pytest tests/ai/test_event_mapper.py -v`

#### Task 2.2 — Capture `TextBlockEvent` as fallback in `classify()`
- [ ] **File**: `archon/ai/classifier.py`
- **Depends on**: Task 1.2 (stateless classify loop), Task 2.1 (TextBlockEvent exists)
- **Description**:
  - In the `classify()` event loop, add a branch for `TextBlockEvent`:
    ```python
    elif isinstance(event, TextBlockEvent):
        textblock_fallback_candidates.append(event.content)
    ```
    Initialise `textblock_fallback_candidates: list[str] = []` before the loop.
  - After the loop, add (before calling `parse_classification`):
    ```python
    if not raw_response and textblock_fallback_candidates:
        for candidate in textblock_fallback_candidates:
            extracted = extract_json_object(candidate)
            if extracted:
                raw_response = extracted
                logger.debug("Classifier using TextBlock fallback content")
                break
    ```
  - Import `TextBlockEvent` from `event_mapper`, `extract_json_object` from `classification`.
  - This runs only when `raw_response` is still empty after the loop — no change to normal path.
- **Releasable**: after this task, Mode A (SDK delivers JSON in TextBlock) is fully recovered.
- **Tests (TDD)** — `tests/ai/test_classifier.py`:
  - Unit: `test_classify_textblock_fallback_when_no_response` — session yields `TextBlockEvent('{"intent": "task", "confidence": 0.95}')` + no `Response` → `result.classification.confidence == 0.95`.
  - Unit: `test_classify_textblock_fallback_ignores_prose` — TextBlock contains `"You're right, I'll investigate"` + no Response → fallback extraction fails → `confidence == 0.0`.
  - Unit: `test_classify_textblock_fallback_takes_first_valid` — two TextBlocks: first `'{"intent": "chat", "confidence": 0.8}'`, second `'{"intent": "task", "confidence": 0.6}'` → first is used → `confidence == 0.8`.
  - Unit: `test_classify_response_takes_priority_over_textblock` — session yields both `TextBlockEvent` and `Response` → `Response` content is used (not fallback).
  - Checkpoint: `uv run pytest tests/ai/test_classifier.py -k "textblock" -v`

---

### Phase 3 — Enhanced diagnostic logging
> **Releasable**: after Task 3.2 — improved log messages aid future debugging; no behavior change.

#### Task 3.1 — Distinguish empty string from malformed JSON in `parse_classification()`
- [ ] **File**: `archon/ai/classification.py`
- **Depends on**: nothing
- **Description**:
  - At the start of `parse_classification()` (before line 95), add:
    ```python
    if not raw:
        error = "empty response — no content from model"
        logger.warning("Classification parse failed: %s", error)
        return ClassificationResult(_default(), error=error)
    ```
  - The existing `"no JSON object found in response"` message (line 100) is now only reached for non-empty
    text that contains no parseable JSON object (Mode B/C prose case).
  - `"malformed JSON in response"` (line 106) remains for text where a JSON object was found but is invalid.
  - Return type and default behaviour unchanged — `ClassificationResult(_default(), error=...)` in all failure cases.
- **Releasable**: after this task, log messages distinguish the three failure sub-types clearly.
- **Tests (TDD)** — `tests/ai/test_classification.py`:
  - Unit: `test_parse_classification_empty_string_logs_empty_response` — `parse_classification("")` → warning log contains "empty response — no content from model".
  - Unit: `test_parse_classification_prose_logs_no_json_found` — `parse_classification("You're right")` → warning log contains "no JSON object found in response".
  - Unit: `test_parse_classification_malformed_json_logs_malformed` — `parse_classification('{"intent": "task"')` → warning log contains "malformed JSON in response".
  - Existing parse tests must still pass (happy path, valid JSON, field validation).
  - Checkpoint: `uv run pytest tests/ai/test_classification.py -v`

#### Task 3.2 — Log warning in `classify()` when `raw_response` is empty after event loop
- [ ] **File**: `archon/ai/classifier.py`
- **Depends on**: Task 1.2 (stateless classify loop), Task 2.2 (TextBlock fallback)
- **Description**:
  - After the TextBlock fallback block (Task 2.2) and before calling `parse_classification(raw_response)`,
    add:
    ```python
    if not raw_response:
        logger.warning(
            "Classifier received no Response event — raw_response empty"
        )
    ```
  - This log fires only when both the `Response` event path AND the TextBlock fallback path produce nothing —
    i.e., a genuine SDK-level failure to deliver any content.
  - No change to `ClassifierResult` or return value.
- **Releasable**: after this task, the warning is observable in `archon.log` to distinguish Mode A (pre-fix)
  from a new unknown failure.
- **Tests (TDD)** — `tests/ai/test_classifier.py`:
  - Unit: `test_classify_logs_warning_on_empty_raw_response` — session yields no `Response` and no `TextBlockEvent` → logger emits WARNING containing "no Response event".
  - Unit: `test_classify_no_warning_when_response_present` — session yields `Response("...")` → no WARNING logged.
  - Unit: `test_classify_no_warning_when_textblock_fallback_succeeds` — session yields `TextBlockEvent` with valid JSON, no `Response` → no WARNING (fallback succeeded, `raw_response` is non-empty before the log check).
  - Checkpoint: `uv run pytest tests/ai/test_classifier.py -k "warning or logs" -v`
