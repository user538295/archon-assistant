# FEAT-035 — Response Validator

**Purpose**: Add a lightweight post-response quality gate that scores each answer against the user's intent and injects correction prompts into the Decomposer session when the score falls below a configurable threshold.
**Audience**: All Archon users. Power users in verbose/debug mode see scoring detail; quiet/normal users see streamed responses as today plus an exhaustion notice if all retries fail.
**Status**: To Do

---

## Background

Archon's pipeline delivers responses immediately after generation with no semantic quality check. For complex or ambiguous tasks this can produce incomplete, off-target, or incorrect responses that the user must manually follow up on.

A post-response Validator session receives the original request, the Classifier's intent, and the full response text. It scores 0–100 and, if the score is below threshold, injects a correction prompt into the existing Decomposer session so the conversation context and tool history are preserved. The loop continues until the score meets the threshold or `max_retries` correction attempts are exhausted.

The feature is opt-in (`enabled = false` default) because validation adds latency and LLM cost.

---

## Goal

After each `pipeline.send()` response is streamed to Telegram, a `ResponseValidator` ephemeral session evaluates goal alignment and outputs `result_score`, `approved`, and `gap_analysis`. The pipeline streams every attempt immediately; notification mode controls how much the user sees. When retries are exhausted, one informational message is always sent regardless of notification mode.

---

## Scope

### In Scope
- `ValidatorConfig` dataclass with `enabled`, `result_score_threshold`, `max_retries`
- `QualityScoreEvent` dataclass in the `Event` type union (visible verbose/debug only)
- `ValidatorExhaustionEvent` dataclass (always visible in all notification modes)
- `archon/ai/prompts/validator.md` — system prompt that outputs structured JSON
- `ResponseValidator` class — ephemeral satellite session (user's default model, one session per validate call)
- `ValidatorResult` dataclass + `parse_validator_result()` resilient JSON parser
- Pipeline integration: `_stream_correction()`, `_run_with_validation()`, wiring into `send()`
- `telegram_formatter.py` and `event_renderer.py` updated for both new event types
- Cost tracking: validator session cost folded into `Pipeline.usage_stats["sessions"]`

### Out of Scope
- Buffering or withholding responses — every attempt streams immediately
- Pre-execution plan validation (future iteration)
- Per-agent-wave validation during background tasks
- Chat-intent bypass — currently validator applies to ALL classification intents including `chat`. A future iteration may add intent-based skipping (see Known Limitations).
- Per-user threshold overrides via Telegram commands
- CLI `/validator` command
- Interactive "resend best" keyboard buttons

---

## Acceptance criteria
- [ ] `[validator] enabled = true` activates the validator; `enabled = false` (default) has zero overhead and does not change any existing behavior
- [ ] Validator scores the response and emits `QualityScoreEvent` — visible only in verbose/debug
- [ ] If `result_score >= result_score_threshold`, the pipeline returns after emitting `QualityScoreEvent`
- [ ] If `result_score < threshold` and `max_retries > 0`, a correction prompt is injected into the Decomposer; new response streams immediately; loop continues
- [ ] `max_retries = 0`: validate once; if below threshold, emit `ValidatorExhaustionEvent` immediately — no correction
- [ ] `ValidatorExhaustionEvent` is rendered in all notification modes (quiet, normal, verbose, debug)
- [ ] Best-attempt tracking: if best_attempt ≠ last_attempt, exhaustion message references best attempt by number and score
- [ ] Validator session timeout (45s): treated as `score = 0, approved = false`; counts as a retry; warning logged
- [ ] Correction prompt timeout (90s): emit `ErrorEvent` and exit the validation loop; the previously-streamed response (which may have been below threshold) remains the delivered content; no `ValidatorExhaustionEvent` is emitted (the timeout ErrorEvent signals the issue)
- [ ] Malformed validator JSON: treated as `score = 0, approved = false`; parse error logged
- [ ] `result_score_threshold` outside [0, 100] or `max_retries < 0` raises `ConfigError` at startup
- [ ] If `_task_direct_monitored` promotes the task to a background agent (yields `PromotionEvent` with no `Response`), the validator is skipped entirely — no `QualityScoreEvent`, no `ValidatorExhaustionEvent`
- [ ] Validator events appear in `HistoryManager` session logs
- [ ] `uv run pytest` passes with ≥85% coverage

---

## What does NOT change
- Pipeline `send()` behavior when `validator.enabled = false` — zero code paths changed
- `_task_direct_monitored()` — unchanged; called as today for the first attempt
- `Decomposer.answer()` — unchanged; called for each correction attempt
- All existing event types, their rendering, and notification mode filtering
- `Classifier`, `Decomposer`, or any other satellite sessions

---

## Known limitations / accepted trade-offs
- The Validator uses the user's default model (same as Decomposer) to preserve nuance on complex tasks. This adds cost per response when enabled; documented in config example.
- Validator receives only the final Response text, not the Decomposer's full tool history. This is deliberate: goal-alignment check, not process audit.
- Validation adds wall-clock latency between the final response and any follow-up messages. Users who care about throughput should leave `enabled = false`.
- The correction prompt format is not configurable; the `validator.md` system prompt drives it entirely.
- `ValidatorExhaustionEvent` is always sent. If the user is in quiet mode and responses succeed on first try, they never see any validator output (only the exhaustion path is mode-independent).
- Validator applies to all intents including `chat` (conversational messages). This adds unnecessary cost and latency for simple exchanges. Future iteration: skip validation when `classification.intent == 'chat'` and `confidence >= 0.8`.
- `result_score_threshold = 0` accepts every response unconditionally (score=0 from a timeout or parse error also passes). This makes the validator a no-op quality gate while still incurring the full LLM cost per request. Use only for debugging or telemetry purposes.
- Worst-case lock duration: `_TASK_DIRECT_TIMEOUT_S` (300s) + `(max_retries + 1) * (_VALIDATOR_SESSION_TIMEOUT_S + _CORRECTION_TIMEOUT_S)` = 300 + 3 × (45 + 90) = 705s for default config. During this time, any subsequent Telegram message from the user is queued. Users who care about responsiveness should keep `max_retries` low or leave `enabled = false`.
- Correction attempts (via `_stream_correction`) do not have tool-promotion monitoring. The 90s `_CORRECTION_TIMEOUT_S` is the sole guard against runaway tool usage during corrections. If a correction requires complex tool work, it may time out at 90s and emit an `ErrorEvent`.

---

## Architecture

### New module
- **`archon/ai/response_validator.py`**
  - `ValidatorResult(score: int, approved: bool, gap_analysis: str, error: str = "")` — dataclass; `approved` here is the **LLM's raw judgment** (from JSON), stored for reference/debugging only — the pipeline never reads it for control flow
  - `parse_validator_result(text: str) -> ValidatorResult` — resilient JSON parser; extracts `{"result_score": int, "approved": bool, "gap_analysis": string}` from the model output; handles ```json``` blocks and trailing text; on failure returns `ValidatorResult(score=0, approved=False, gap_analysis="", error="parse error: ...")`
    - NOTE: `ValidatorResult.approved` is the LLM's field; `QualityScoreEvent.approved` is the pipeline's decision (`score >= threshold`). These are different fields on different dataclasses with different semantics. The pipeline uses only `val_result.score` — never `val_result.approved` — for loop control.
  - `ResponseValidator(cwd: str | None, model: str | None)` — ephemeral satellite session
    - `_create_session() -> ClaudeSession` — fresh `ClaudeSession` per call (no tools, max_turns=1, disable_thinking=True)
    - `async validate(original_prompt, intent, confidence, response_text) -> ValidatorResult` — connects, sends prompt, disconnects; 45s timeout treated as score=0 approved=false; accumulates cost in `_carried_cost_usd`
    - `usage_stats: dict[str, Any] | None` — carried cost
    - `start() / stop()` — no-ops (no persistent session)

### New prompt file
- **`archon/ai/prompts/validator.md`** — instructs the model to answer three questions: (1) Was the user's goal achieved? (2) Was anything missed? (3) Was anything done the user didn't ask for? — then output strict JSON: `{"result_score": int, "approved": bool, "gap_analysis": string}`

### Config changes
- `archon/config/loader.py`: add `ValidatorConfig` dataclass with `enabled: bool = False`, `result_score_threshold: int = 80`, `max_retries: int = 2`; add `validator: ValidatorConfig` field to `Config`; add startup validation: `result_score_threshold` outside [0, 100] or `max_retries < 0` → `ConfigError`

### Event changes
- `archon/ai/event_mapper.py`: add `QualityScoreEvent` and `ValidatorExhaustionEvent` to the module and to the `Event` union

### Pipeline changes
- `archon/ai/pipeline.py`:
  - `_VALIDATOR_SESSION_TIMEOUT_S: float = 45.0`
  - `_CORRECTION_TIMEOUT_S: float = 90.0`
  - `_build_correction_prompt(gap_analysis: str, original_prompt: str) -> str` — pure module-level function
  - `Pipeline.__init__`: create `self._validator: ResponseValidator | None` when `config.validator.enabled`
  - `Pipeline.start() / stop()`: delegate to `self._validator` (no-ops, but keeps lifecycle symmetric)
  - `_stream_correction(correction_prompt: str, classification: Classification) -> AsyncGenerator[Event, None]` — calls `self._decomposer.answer(correction_prompt)` with `_CORRECTION_TIMEOUT_S` rolling deadline; on timeout yields `ErrorEvent` (source="pipeline")
  - `_run_with_validation(resolved: str, original_prompt: str, classification: Classification) -> AsyncGenerator[Event, None]` — runs `_task_direct_monitored`, captures `Response.content`, runs the validate/correct loop, emits `QualityScoreEvent` (verbose/debug) + `ValidatorExhaustionEvent` (all modes) as needed
  - `send()`: replace the `async for event in self._task_direct_monitored(resolved, classification)` call with `async for event in self._run_with_validation(resolved, original_prompt=prompt, classification=result.classification)` when validator is enabled; keep the original code path for the disabled case

### Display changes
- `archon/chat/telegram_formatter.py`: render `QualityScoreEvent` (verbose/debug only) and `ValidatorExhaustionEvent` (always)
- `archon/ai/event_renderer.py`: render both events in history markdown

### Data flow
```
pipeline.send(prompt)
  └─ _run_with_validation(resolved, original_prompt, classification)
       ├─ _task_direct_monitored(resolved, classification)  ← first attempt
       │    └─ yields events + captures last Response.content (keep-last)
       │         └─ no Response captured (PromotionEvent) → skip validation, return
       └─ validation loop (while True):
            ├─ ResponseValidator.validate(...)  [try/except: fail-open as score=0]
            ├─ yield QualityScoreEvent(approved = score >= threshold)  [verbose/debug]
            ├─ score >= threshold → return
            ├─ retries_remaining == 0 → yield ValidatorExhaustionEvent(...), return
            └─ _stream_correction(correction_prompt, classification)
                 ├─ yields events + captures new Response.content (keep-last)
                 └─ ErrorEvent → return  [no ValidatorExhaustionEvent]
```

### Config key additions
- `[validator] enabled` — `bool`, default `false`
- `[validator] result_score_threshold` — `int`, default `80`, valid range [0, 100]
- `[validator] max_retries` — `int`, default `2`, must be ≥ 0

---

## Tests

- **`test_validator_config_defaults`** (unit): `ValidatorConfig` has correct defaults
- **`test_validator_config_threshold_out_of_range`** (unit): threshold 101 and -1 each raise `ConfigError`
- **`test_validator_config_max_retries_negative`** (unit): `max_retries = -1` raises `ConfigError`
- **`test_validator_config_enabled_false_no_check`** (unit): `enabled = false` with `result_score_threshold = 150` does NOT raise `ConfigError` (validation only runs when enabled)
- **`test_validator_config_valid`** (unit): `enabled = true`, `result_score_threshold = 90`, `max_retries = 3` → parsed without error
- **`test_parse_validator_result_valid`** (unit): parses `{"result_score": 85, "approved": true, "gap_analysis": "..."}`
- **`test_parse_validator_result_json_block`** (unit): parses output wrapped in ```json...``` fences
- **`test_parse_validator_result_missing_fields`** (unit): score=0, approved=false when JSON is missing required keys
- **`test_parse_validator_result_invalid_json`** (unit): returns score=0, approved=false, error set
- **`test_parse_validator_result_trailing_text`** (unit): JSON extracted from response with prose after it
- **`test_parse_validator_result_score_clamped`** (unit): score=150 clamped to 100
- **`test_response_validator_approved`** (unit): `validate()` with mocked session returning score=90 → `ValidatorResult(score=90, approved=True)`
- **`test_response_validator_below_threshold`** (unit): score=60 → `ValidatorResult(score=60, approved=False)`
- **`test_response_validator_timeout`** (unit): session never responds → `asyncio.TimeoutError` → `ValidatorResult(score=0, approved=False)`
- **`test_response_validator_malformed_output`** (unit): session returns `"not json"` → `ValidatorResult(score=0, approved=False, error=...)`
- **`test_response_validator_accumulates_cost`** (unit): multiple calls accumulate `_carried_cost_usd`
- **`test_response_validator_start_stop_noop`** (unit): `start()` and `stop()` complete without error
- **`test_build_correction_prompt_includes_gap_analysis`** (unit): correction prompt contains gap_analysis text and original prompt
- **`test_stream_correction_yields_events`** (unit): `_stream_correction` streams decomposer events; captured `Response.content` equals last yielded Response
- **`test_stream_correction_timeout`** (unit): decomposer never responds → after 90s emits `ErrorEvent`
- **`test_run_with_validation_approved_first_try`** (unit): validator approves immediately → `QualityScoreEvent` emitted, no `ValidatorExhaustionEvent`
- **`test_run_with_validation_retry_then_approved`** (unit): validator rejects once, approves on retry → 2 `QualityScoreEvent`s, correction prompt sent to decomposer, no `ValidatorExhaustionEvent`
- **`test_run_with_validation_exhausted_same_best`** (unit): all retries fail, best=last → `ValidatorExhaustionEvent` has matching best/last score format
- **`test_run_with_validation_exhausted_better_earlier`** (unit): attempt 2 scored higher than last attempt → `ValidatorExhaustionEvent` references attempt 2
- **`test_run_with_validation_max_retries_zero`** (unit): validate once, below threshold → immediate `ValidatorExhaustionEvent`, no correction
- **`test_run_with_validation_exhausted_max_retries_default`** (unit): `max_retries=2` (default), all three validation attempts fail (scores 60, 65, 70 respectively) → `ValidatorExhaustionEvent(best_attempt=3, best_score=70, last_score=70, total_attempts=3, threshold=80)` emitted; exactly 3 `QualityScoreEvent`s and 2 correction prompts injected
- **`test_run_with_validation_threshold_zero_always_approves`** (unit): `result_score_threshold=0`, validator returns score=0 → immediately approved (score >= 0), no exhaustion
- **`test_run_with_validation_threshold_100_only_perfect`** (unit): `result_score_threshold=100`, validator returns score=99, `max_retries=0` → immediate `ValidatorExhaustionEvent`
- **`test_run_with_validation_skips_on_promotion`** (unit): `_task_direct_monitored` yields only a `PromotionEvent` and no `Response` → `_run_with_validation` yields the `PromotionEvent` and terminates without calling `validator.validate()`, no `QualityScoreEvent` emitted
- **`test_run_with_validation_captures_last_response`** (unit): `_task_direct_monitored` yields `Response("partial", source="decomposer")`, then `Response(content="router-decision", source="router")`, then `Response("complete", source="decomposer")` → `validator.validate()` called with `response_text="complete"` (last non-router Response); the router Response is correctly ignored
- **`test_run_with_validation_approved_by_threshold_not_json_field`** (unit): validator returns `{"result_score": 85, "approved": false}` with threshold=80 → pipeline treats it as approved (threshold wins over JSON `approved` field)
- **`test_run_with_validation_disabled`** (unit): `enabled = false` → passes through to `_task_direct_monitored` unchanged, no `QualityScoreEvent`
- **`test_quality_score_event_visible_verbose`** (unit): `QualityScoreEvent` visible in verbose/debug notification modes
- **`test_quality_score_event_hidden_quiet`** (unit): `QualityScoreEvent` suppressed in quiet/normal modes
- **`test_validator_exhaustion_event_always_visible`** (unit): `ValidatorExhaustionEvent` visible in all four notification modes
- **`test_quality_score_event_fields`** (unit): construct `QualityScoreEvent` and assert all fields present with correct types
- **`test_validator_exhaustion_event_fields`** (unit): construct `ValidatorExhaustionEvent` and assert all fields
- **`test_quality_score_event_in_event_union`** (unit): `QualityScoreEvent` and `ValidatorExhaustionEvent` are both included in the `Event` union type alias
- **`test_telegram_formatter_quality_score_verbose`** (integration): `QualityScoreEvent` renders correctly with score and threshold
- **`test_telegram_formatter_quality_score_debug_includes_gap`** (integration): gap_analysis rendered in debug mode
- **`test_telegram_formatter_exhaustion_same_best`** (integration): exhaustion message format when best=last
- **`test_telegram_formatter_exhaustion_different_best`** (integration): exhaustion message references better earlier attempt
- **`test_event_renderer_quality_score`** (integration): history markdown renders `QualityScoreEvent`
- **`test_event_renderer_exhaustion`** (integration): history markdown renders `ValidatorExhaustionEvent`
- **`test_quality_score_event_in_event_type_map`** (unit): `"quality_score"` is in `_EVENT_TYPE_MAP` and maps to `QualityScoreEvent`
- **`test_validator_exhaustion_in_event_type_map`** (unit): `"validator_exhaustion"` is in `_EVENT_TYPE_MAP` and maps to `ValidatorExhaustionEvent`
- **`test_quality_score_suppressed_in_history`** (unit): when `"quality_score"` is in `config.history.suppressed_events`, `EventRenderer` does not render `QualityScoreEvent`
- **`test_pipeline_start_stop_with_validator`** (integration): `Pipeline.start()` and `stop()` succeed with `validator.enabled = true`
- **`test_pipeline_usage_stats_includes_validator`** (integration): mock decomposer cost = $0.10, validator cost = $0.05 (set via mock `usage_stats`); `pipeline.usage_stats["sessions"]["validator"]["cost_usd"]` == 0.05; `pipeline.usage_stats["total_cost_usd"]` == 0.15 (not 0.20); validates that validator cost is included exactly once in the total
- **`test_pipeline_send_with_validator_end_to_end`** (integration): mocked Decomposer + Validator; `send()` yields events in correct order (classification → routing → response → quality_score)
- **`test_pipeline_send_with_validator_retry_then_approved`** (integration): full `Pipeline.send()` with validator rejecting first attempt (score=60) and approving second (score=85); verifies two Decomposer answer calls, two `QualityScoreEvent`s, no `ValidatorExhaustionEvent`, second event has `approved=True`
- **`test_run_with_validation_validator_raises_unexpected_exception`** (unit): `validator.validate()` raises `RuntimeError("SDK crashed")` → pipeline treats it as score=0, approved=false, continues the retry/exhaustion logic; no unhandled exception propagates from `_run_with_validation`
- **`test_quality_score_event_approved_reflects_pipeline_not_llm`** (unit): validator returns `{"result_score": 85, "approved": false}` with threshold=80 → `QualityScoreEvent.approved == True` (pipeline used score >= threshold); test verifies the event field matches the threshold check, not the JSON field
- **`test_run_with_validation_timeout_then_approved`** (unit): first `validate()` call returns `ValidatorResult(score=0, approved=False, error="validator session timed out")` (simulates 45s timeout); second `validate()` call returns score=85 with threshold=80 → pipeline injects one correction, validates again, approves; total: 2 `QualityScoreEvent`s emitted, 1 correction injected, no `ValidatorExhaustionEvent`
- **`test_response_validator_usage_stats_none_when_zero_cost`** (unit): no `validate()` calls made → `ResponseValidator.usage_stats` returns `None`
- **`test_pipeline_usage_stats_no_validator_cost_when_not_called`** (unit): pipeline with `validator.enabled=true` but `validate()` never called (e.g., PromotionEvent path) → `pipeline.usage_stats["sessions"]["validator"]["cost_usd"]` is `0.0`, `total_cost_usd` is not inflated
- **`test_run_with_validation_all_attempts_timeout`** (unit): `max_retries=1`, both `validate()` calls return score=0 (timeout) → `ValidatorExhaustionEvent(best_attempt=1, best_score=0, last_score=0, total_attempts=2, threshold=80)` emitted; best_attempt_idx stays at 1 because 0 is never > 0
- **`test_run_with_validation_correction_timeout_exits_cleanly`** (unit): collected events contain exactly one `ErrorEvent` and zero `ValidatorExhaustionEvent` instances; generator terminates cleanly

---

## Documentation update
- [ ] `CLAUDE.md`, section `Output event model`: add `QualityScoreEvent` and `ValidatorExhaustionEvent` rows
- [ ] `CLAUDE.md`, section `Configuration`: add `[validator]` section with `enabled`, `result_score_threshold`, `max_retries`
- [ ] `examples/config.toml.example`: add `[validator]` section with all three keys, commented out defaults, and a latency/cost warning
- [ ] `Documentation/Architecture/110_component_catalog_and_layer_breakdown.md`: add `ResponseValidator` to the satellite sessions list in the `archon/ai/` section

---

## Task breakdown

### Phase 1 — Config and event types
> **Releasable**: after Task 1.2 — config parses and validates; event dataclasses exist and can be constructed; no behavior change yet

#### Task 1.1 — `ValidatorConfig` dataclass and `Config` integration
- [ ] **File**: `archon/config/loader.py`
- **Depends on**: nothing
- **Description**:
  - Add `ValidatorConfig` dataclass:
    ```python
    @dataclass
    class ValidatorConfig:
        enabled: bool = False
        result_score_threshold: int = 80
        max_retries: int = 2
    ```
  - Add `validator: ValidatorConfig = field(default_factory=ValidatorConfig)` to `Config`
  - Add loader function `_parse_validator_config(data: dict[str, Any]) -> ValidatorConfig` reading from `[validator]` TOML section
  - Startup validation (in loader, after parsing): if `validator.enabled` is `True`:
    - `result_score_threshold` outside [0, 100] → `ConfigError("validator.result_score_threshold must be between 0 and 100, got {value}")`
    - `max_retries < 0` → `ConfigError("validator.max_retries must be >= 0, got {value}")`
  - Wire `_parse_validator_config()` into the main `load_config()` function
- **Releasable**: `config.validator` available to all callers; invalid config rejected at startup
- **Tests (TDD)** — `tests/config/test_loader.py`:
  - Unit: `test_validator_config_defaults` — omitting `[validator]` entirely → `ValidatorConfig(enabled=False, result_score_threshold=80, max_retries=2)`
  - Unit: `test_validator_config_threshold_out_of_range` — `result_score_threshold = 101` with `enabled = true` raises `ConfigError`; same for `-1`
  - Unit: `test_validator_config_max_retries_negative` — `max_retries = -1` with `enabled = true` raises `ConfigError`
  - Unit: `test_validator_config_enabled_false_no_check` — `enabled = false` with `result_score_threshold = 150` does NOT raise `ConfigError` (validation only runs when enabled)
  - Unit: `test_validator_config_valid` — `enabled = true`, `result_score_threshold = 90`, `max_retries = 3` → parsed without error
  - Checkpoint: `uv run pytest tests/config/test_loader.py -k "validator" -v`

#### Task 1.2 — `QualityScoreEvent` and `ValidatorExhaustionEvent` dataclasses
- [ ] **File**: `archon/ai/event_mapper.py`
- **Depends on**: nothing (parallel with Task 1.1)
- **Description**:
  - Add `QualityScoreEvent` dataclass:
    ```python
    @dataclass
    class QualityScoreEvent:
        """Emitted by the validation loop after each attempt is scored."""
        score: int              # 0–100
        approved: bool          # True if score >= threshold (pipeline decision, not LLM-provided approved field)
        gap_analysis: str       # full validator feedback; shown only in debug mode
        attempt: int            # 1-based attempt number
        threshold: int          # result_score_threshold from config
        source: str = "validator"
    ```
  - Add `ValidatorExhaustionEvent` dataclass:
    ```python
    @dataclass
    class ValidatorExhaustionEvent:
        """Emitted when max_retries is exhausted without approval. Always visible."""
        best_attempt: int       # 1-based index of highest-scoring attempt
        best_score: int
        last_score: int
        total_attempts: int
        threshold: int
        source: str = "validator"
    ```
  - Add both to the `Event` union type alias
- **Releasable**: event dataclasses constructable and included in the type union
- **Tests (TDD)** — `tests/ai/test_event_mapper.py`:
  - Unit: `test_quality_score_event_fields` — construct and assert all fields present with correct types
  - Unit: `test_validator_exhaustion_event_fields` — construct and assert all fields
  - Unit: `test_quality_score_event_in_event_union` — `isinstance(QualityScoreEvent(...), get_args(Event))` or type-narrowing test that the union is updated
  - Checkpoint: `uv run pytest tests/ai/test_event_mapper.py -k "quality_score or exhaustion" -v`

---

### Phase 2 — ResponseValidator satellite session
> **Releasable**: after Task 2.3 — `ResponseValidator.validate()` is callable in tests; no pipeline wiring yet

#### Task 2.1 — `validator.md` system prompt
- [ ] **File**: `archon/ai/prompts/validator.md`
- **Depends on**: nothing
- **Description**:
  - Instructs the model to evaluate a response against the user's original request
  - Three scoring questions: (1) Was the user's stated goal fully achieved? (2) Was anything the user asked for missed or incomplete? (3) Was anything done that the user did NOT ask for?
  - Scoring guide: 100 = goal fully met with nothing extra; 80–99 = minor gaps; 60–79 = partial; below 60 = substantial gaps or off-target
  - Strict output format: the model MUST respond with only the JSON object (no prose before or after):
    ```json
    {"result_score": <int 0-100>, "approved": <bool>, "gap_analysis": "<concise gap summary, max 200 chars>"}
    ```
  - `approved` should be `true` when `result_score >= threshold` (the threshold is included in the prompt as a variable substitution `{threshold}`)
  - Keep prompt brief — the validator is a single-turn session with no thinking
- **Releasable**: prompt loadable via `load_prompt("validator")`
- **Tests (TDD)**: no dedicated test; covered by `test_response_validator_approved` in Task 2.3
  - Checkpoint: `uv run pytest tests/ai/test_response_validator.py -v` (file created in Task 2.3)

#### Task 2.2 — `ValidatorResult` dataclass and `parse_validator_result()`
- [ ] **File**: `archon/ai/response_validator.py`
- **Depends on**: Task 2.1
- **Description**:
  - `ValidatorResult` dataclass:
    ```python
    @dataclass
    class ValidatorResult:
        score: int
        approved: bool
        gap_analysis: str
        error: str = ""
    ```
  - `parse_validator_result(text: str) -> ValidatorResult`:
    - Strip ```json...``` fences if present (regex: `` r"```(?:json)?\s*([\s\S]*?)\s*```" ``)
    - Find first `{...}` JSON object via `extract_json_object()` (already in `archon/ai/classification.py`)
    - Parse `result_score` (int, clamped to [0, 100]), `approved` (bool), `gap_analysis` (str, max 500 chars)
    - On any parse failure: `ValidatorResult(score=0, approved=False, gap_analysis="", error=f"parse error: {exc}")`
    - Log parse errors at WARNING level
- **Releasable**: `parse_validator_result()` callable in isolation
- **Tests (TDD)** — `tests/ai/test_response_validator.py`:
  - Unit: `test_parse_validator_result_valid_json` — clean JSON returns correct fields
  - Unit: `test_parse_validator_result_json_fence` — triple-backtick block extracted
  - Unit: `test_parse_validator_result_missing_fields` — JSON `{}` → score=0, approved=False
  - Unit: `test_parse_validator_result_invalid_json` — `"not json"` → error field set, score=0
  - Unit: `test_parse_validator_result_trailing_prose` — `{"result_score": 70, ...} Some extra text` — JSON extracted correctly
  - Unit: `test_parse_validator_result_score_clamped` — score=150 clamped to 100
  - Unit: `test_parse_validator_result_score_clamped_negative` — score=-10 in JSON clamped to 0
  - Checkpoint: `uv run pytest tests/ai/test_response_validator.py -k "parse" -v`

#### Task 2.3 — `ResponseValidator` class
- [ ] **File**: `archon/ai/response_validator.py`
- **Depends on**: Task 2.2
- **Description**:
  - `ResponseValidator(cwd: str | None, model: str | None)`:
    - `_cwd`, `_model` stored from constructor
    - `_prompt = load_prompt("validator")` — loaded once at construction
    - `_carried_cost_usd: float = 0.0`
  - `_create_session(threshold: int) -> ClaudeSession`:
    ```python
    return ClaudeSession(
        cwd=self._cwd,
        model=self._model,
        system_prompt=self._prompt.replace("{threshold}", str(threshold)),
        tools=[],
        max_turns=1,
        disable_thinking=True,
    )
    ```
  - `async def validate(self, original_prompt: str, intent: str, confidence: float, response_text: str, threshold: int) -> ValidatorResult`:
    - Build user message: `f"Original request: {original_prompt}\n\nClassification: {intent} ({confidence:.0%})\n\nResponse to evaluate:\n{response_text}"`
    - Create session, `start()`, `send()`, `stop()` (in try/finally)
    - 45s timeout via `asyncio.wait_for` around the `send()` loop
    - On `TimeoutError`: return `ValidatorResult(score=0, approved=False, gap_analysis="", error="validator session timed out")`, log warning
    - Accumulate cost: `self._carried_cost_usd += (session.usage_stats or {}).get("total_cost_usd", 0.0)`
    - Return `parse_validator_result(raw_response)`
  - `@property usage_stats -> dict[str, Any] | None`: returns `{"total_cost_usd": self._carried_cost_usd}` or `None` if 0
  - `async start(self) -> None`: no-op
  - `async stop(self) -> None`: no-op
- **Releasable**: `ResponseValidator.validate()` callable end-to-end
- **Tests (TDD)** — `tests/ai/test_response_validator.py`:
  - Unit: `test_response_validator_approved` — mock session returns score=90 → `ValidatorResult(score=90, approved=True)`
  - Unit: `test_response_validator_below_threshold` — score=60 → `ValidatorResult(score=60, approved=False)`
  - Unit: `test_response_validator_timeout` — mock session hangs → `asyncio.TimeoutError` → returns score=0 approved=False
  - Unit: `test_response_validator_malformed_output` — session returns `"not json"` → score=0 approved=False error set
  - Unit: `test_response_validator_accumulates_cost` — two `validate()` calls → `usage_stats["total_cost_usd"]` = sum of both
  - Unit: `test_response_validator_start_stop_noop` — `start()` and `stop()` complete without error
  - Unit: `test_response_validator_usage_stats_none_when_zero_cost` — no `validate()` calls made → `ResponseValidator.usage_stats` returns `None`
  - Checkpoint: `uv run pytest tests/ai/test_response_validator.py -v`

---

### Phase 3 — Pipeline integration
> **Releasable**: after Task 3.3 — `pipeline.send()` runs the full validation loop when `validator.enabled = true`

#### Task 3.1 — `_build_correction_prompt()` and `_stream_correction()`
- [ ] **File**: `archon/ai/pipeline.py`
- **Depends on**: Task 1.2 (ErrorEvent already exists; no new imports needed)
- **Description**:
  - Module-level constant: `_CORRECTION_TIMEOUT_S: float = 90.0`
  - `_build_correction_prompt(gap_analysis: str, original_prompt: str) -> str` — pure function:
    ```
    [Validator feedback: your previous response did not fully meet the user's goal.]

    Gap analysis:
    {gap_analysis}

    Please provide an improved response that addresses these gaps.
    Original request: {original_prompt}
    ```
  - `Pipeline._stream_correction(self, correction_prompt: str, classification: Classification) -> AsyncGenerator[Event, None]`:
    - Calls `self._decomposer.answer(correction_prompt)` — reuses the existing Decomposer session (context preserved)
    - Rolling deadline `_CORRECTION_TIMEOUT_S` using the same `asyncio.wait_for(_safe_anext(gen), timeout=remaining)` pattern from `_task_direct_monitored()`
    - **Timeout pattern**: Use the rolling-deadline `asyncio.wait_for(gen.__anext__(), timeout=remaining)` pattern — NOT `asyncio.timeout()` spanning a `yield`. Follow the same pattern used in `_task_direct_monitored()`'s rolling deadline implementation. Never use `asyncio.timeout()` as a context manager wrapping a generator iteration loop.
    - On `TimeoutError`: log error, yield `ErrorEvent(message="Correction prompt timed out after 90s — delivering last attempt.", source="pipeline")`; the caller treats the timeout `ErrorEvent` as a signal to exit the validation loop
    - Always closes `gen` in `finally` with `_ACLOSE_TIMEOUT_S` timeout
    - Does NOT recurse into `_task_direct_monitored` — correction is a direct `answer()` call (no tool-promotion monitoring)
- **Releasable**: `_stream_correction()` callable from tests; `_build_correction_prompt()` callable in isolation
- **Tests (TDD)** — `tests/ai/test_pipeline.py`:
  - Unit: `test_build_correction_prompt_contains_gap` — gap_analysis text and original_prompt both present in output
  - Unit: `test_stream_correction_yields_events` — mock decomposer yields `Response("ok")` → events flow through; response content captured correctly
  - Unit: `test_stream_correction_timeout` — mock decomposer never yields → after 90s emits `ErrorEvent` with "timed out" in message
  - Checkpoint: `uv run pytest tests/ai/test_pipeline.py -k "correction" -v`

#### Task 3.2 — `_run_with_validation()` orchestration method
- [ ] **File**: `archon/ai/pipeline.py`
- **Depends on**: Task 1.2, Task 2.3, Task 3.1
- **Description**:
  - Module-level constant: `_VALIDATOR_SESSION_TIMEOUT_S: float = 45.0` (used inside `ResponseValidator.validate()`, passed as timeout — alternatively configure as a parameter)
  - `Pipeline._run_with_validation(self, resolved: str, original_prompt: str, classification: Classification) -> AsyncGenerator[Event, None]`:
    - If `self._validator is None` (validator disabled): `async for event in self._task_direct_monitored(resolved, classification): yield event; return`
    - Otherwise:
      1. Run `_task_direct_monitored(resolved, classification)`, yielding all events; capture last `Response` content — overwrite `response_text` on each `Response` event with `source != "router"` (keep-last semantics, not concatenation)
         - **Promotion guard**: After iterating `_task_direct_monitored`, check if any `Response` event was captured. If `response_text` is empty/None (e.g., task was promoted to a background agent via `PromotionEvent`), skip the validation loop entirely and return. No `QualityScoreEvent` or `ValidatorExhaustionEvent` is emitted.
      2. Initialize: `retries_remaining = config.validator.max_retries`, `attempt_idx = 1`, `best_score = 0`, `best_attempt_idx = 1`, `last_score = 0`
      3. Validation loop (`while True`):
         - Call `self._validator.validate(original_prompt=original_prompt, intent=classification.intent, confidence=classification.confidence, response_text=response_text, threshold=config.validator.result_score_threshold)`
         - `validate()` call is wrapped in `try/except Exception as exc`: on unexpected exception (anything other than TimeoutError handled internally by validate()), log error at ERROR level, treat as `ValidatorResult(score=0, approved=False, gap_analysis="", error=f"unexpected error: {exc}")` — fail open, count as a retry, continue loop.
         - Update `last_score`, track best: `if val_result.score > best_score: best_score = val_result.score; best_attempt_idx = attempt_idx`
         - Yield `QualityScoreEvent(score=val_result.score, approved=(val_result.score >= config.validator.result_score_threshold), gap_analysis=val_result.gap_analysis, attempt=attempt_idx, threshold=config.validator.result_score_threshold)`
           - NOTE: `approved` in `QualityScoreEvent` reflects the pipeline's threshold decision, NOT the LLM's raw `approved` field. This ensures the event accurately describes what the pipeline did.
         - If `val_result.score >= config.validator.result_score_threshold`: return
         - If `retries_remaining <= 0`: break
         - Inject correction:
           ```
           async for event in self._stream_correction(...):
             yield event
             if isinstance(event, ErrorEvent):
               return  # exit loop; no ValidatorExhaustionEvent; ErrorEvent signals failure
             if isinstance(event, Response) and event.source != "router":
               response_text = event.content  # update response_text for next validation round
           ```
         - `retries_remaining -= 1; attempt_idx += 1`
      4. After loop (exhaustion): yield `ValidatorExhaustionEvent(best_attempt=best_attempt_idx, best_score=best_score, last_score=last_score, total_attempts=attempt_idx, threshold=config.validator.result_score_threshold)`
    - **ErrorEvent in correction**: if `_stream_correction` yields an `ErrorEvent` (e.g., correction timeout), the inline `return` inside the correction streaming loop exits the generator immediately — no `ValidatorExhaustionEvent` is emitted. The `ErrorEvent` itself signals the failure to the user.
- **Releasable**: full validation loop works end-to-end in tests
- **Tests (TDD)** — `tests/ai/test_pipeline.py`:
  - Unit: `test_run_with_validation_disabled` — `_validator=None` → passes through to `_task_direct_monitored` unchanged
  - Unit: `test_run_with_validation_approved_first_try` — validator approves score=90 → one `QualityScoreEvent`, no `ValidatorExhaustionEvent`
  - Unit: `test_run_with_validation_retry_then_approved` — first validation fails (score=60), correction injected, second validates (score=85) → two `QualityScoreEvent`s, correction prompt sent
  - Unit: `test_run_with_validation_exhausted_same_best` — `max_retries=1`, both attempts score 60 → `ValidatorExhaustionEvent(best_attempt=1, best_score=60, last_score=60, ...)`
  - Unit: `test_run_with_validation_exhausted_better_earlier` — `max_retries=1`, attempt 1 scores 80, attempt 2 scores 65 → `ValidatorExhaustionEvent(best_attempt=1, best_score=80, last_score=65, ...)`
  - Unit: `test_run_with_validation_max_retries_zero` — `max_retries=0`, score=60 → immediate `ValidatorExhaustionEvent`, no correction
  - Unit: `test_run_with_validation_correction_timeout_exits_cleanly` — `_stream_correction` yields `ErrorEvent(message="Correction prompt timed out after 90s...")` → collected events contain exactly one `ErrorEvent` and zero `ValidatorExhaustionEvent` instances; generator terminates cleanly
  - Unit: `test_run_with_validation_validator_raises_unexpected_exception` — `validator.validate()` raises `RuntimeError("SDK crashed")` → pipeline treats it as score=0, approved=false, continues the retry/exhaustion logic; no unhandled exception propagates from `_run_with_validation`
  - Unit: `test_quality_score_event_approved_reflects_pipeline_not_llm` — validator returns `{"result_score": 85, "approved": false}` with threshold=80 → `QualityScoreEvent.approved == True` (pipeline used score >= threshold); test verifies the event field matches the threshold check, not the JSON field
  - Unit: `test_run_with_validation_timeout_then_approved` — first `validate()` call returns `ValidatorResult(score=0, approved=False, error="validator session timed out")`; second call returns score=85 with threshold=80 → 2 `QualityScoreEvent`s, 1 correction injected, no `ValidatorExhaustionEvent`
  - Unit: `test_run_with_validation_all_attempts_timeout` — `max_retries=1`, both `validate()` calls return score=0 → `ValidatorExhaustionEvent(best_attempt=1, best_score=0, last_score=0, total_attempts=2, threshold=80)` emitted
  - Unit: `test_run_with_validation_captures_last_response` — see global Tests section for full description
  - Checkpoint: `uv run pytest tests/ai/test_pipeline.py -k "run_with_validation" -v`

#### Task 3.3 — Wire `ResponseValidator` into `Pipeline`
- [ ] **File**: `archon/ai/pipeline.py`
- **Depends on**: Task 2.3, Task 3.2
- **Description**:
  - Add import: `from archon.config import config as archon_config` (or use the existing `config` singleton access pattern in the file)
  - Import `ResponseValidator` from `archon.ai.response_validator`
  - `Pipeline.__init__`: after constructing `self._decomposer`, add:
    ```python
    from archon.config import config as _cfg
    self._validator: ResponseValidator | None = (
        ResponseValidator(cwd=cwd, model=model)
        if _cfg.validator.enabled
        else None
    )
    ```
  - `Pipeline.start()`: call `await self._validator.start()` (no-op but keeps lifecycle symmetric); only if `self._validator is not None`
  - `Pipeline.stop()`: call `await self._validator.stop()` (no-op)
  - `Pipeline.send()`: replace the two existing `async for event in self._task_direct_monitored(...)` calls (both the `chat` branch and the `task_direct` branch) with `async for event in self._run_with_validation(resolved, original_prompt=prompt, classification=result.classification)`
    - **Both routing branches** (`"chat"` and `"task_direct"`) go through `_run_with_validation` — validation applies regardless of classification
  - `Pipeline.usage_stats`: fold validator cost into the returned dict under `"sessions"`:
    ```python
    val = (self._validator.usage_stats or {}) if self._validator else {}
    val_cost = val.get("total_cost_usd", 0.0)
    ```
    NOTE: The `or {}` is required — `usage_stats` returns `None` when `_carried_cost_usd == 0.0` (no `validate()` calls made). Without `or {}`, calling `.get()` on `None` raises `AttributeError`.
    Add `"validator": {"cost_usd": val_cost}` to the `sessions` dict; include `val_cost` in `total_cost_usd`
- **Releasable**: `pipeline.send()` runs the validation loop when `validator.enabled = true`; disabled case is a zero-overhead pass-through
- **Tests (TDD)** — `tests/ai/test_pipeline.py`:
  - Integration: `test_pipeline_start_stop_with_validator` — `Pipeline` with `validator.enabled = true`; `start()` + `stop()` complete without error
  - Integration: `test_pipeline_usage_stats_includes_validator` — mock decomposer cost = $0.10, validator cost = $0.05 (set via mock `usage_stats`); `pipeline.usage_stats["sessions"]["validator"]["cost_usd"]` == 0.05; `pipeline.usage_stats["total_cost_usd"]` == 0.15 (not 0.20); validates that validator cost is included exactly once in the total
  - Integration: `test_pipeline_send_with_validator_end_to_end` — mocked Decomposer + Validator; `send()` yields events in correct order (classification → routing → response → quality_score)
  - Integration: `test_pipeline_usage_stats_no_validator_cost_when_not_called` — pipeline with `validator.enabled=true` but `validate()` never called (e.g., PromotionEvent path) → `pipeline.usage_stats["sessions"]["validator"]["cost_usd"]` is `0.0`, `total_cost_usd` is not inflated
  - Integration: `test_pipeline_send_with_validator_retry_then_approved` — full `Pipeline.send()` call with mocked Decomposer that answers twice and Validator that rejects first attempt (score=60) then approves second (score=85); asserts: (a) two Decomposer answer calls, (b) two `validate()` calls, (c) two `QualityScoreEvent`s in yielded events, (d) no `ValidatorExhaustionEvent`, (e) second `QualityScoreEvent.approved == True`
  - Checkpoint: `uv run pytest tests/ai/test_pipeline.py -v`

---

### Phase 4 — Display surfaces
> **Releasable**: after Task 4.3 — both Telegram messages and history markdown handle the new events correctly, and validator events are routed correctly in all notification modes

#### Task 4.1 — `telegram_formatter.py`: render validator events
- [ ] **File**: `archon/chat/telegram_formatter.py`
- **Depends on**: Task 1.2
- **Description**:
  - Import `QualityScoreEvent` and `ValidatorExhaustionEvent`
  - `QualityScoreEvent` branch — notification mode filtering (verbose/debug only, same pattern as `RoutingEvent`):
    - verbose mode: `f"🔍 Validator: score {event.score}/{event.threshold} (attempt {event.attempt})"`
    - debug mode: also append gap_analysis on a new line if non-empty: `f"\n{html.escape(event.gap_analysis)}"`
  - `ValidatorExhaustionEvent` branch — **always visible in all modes** (no notification mode check):
    - best = last (`event.best_attempt == event.total_attempts`): `f"🔍 Validator: retries exhausted (score: {event.last_score}/{event.threshold})."`
    - best ≠ last: `f"🔍 Validator: retries exhausted. Attempt {event.best_attempt} scored higher ({event.best_score} vs {event.last_score}). Check session history to review."`
- **Releasable**: Telegram renders validator events correctly
- **Tests (TDD)** — `tests/chat/test_telegram_formatter.py`:
  - Unit: `test_quality_score_event_verbose` — `QualityScoreEvent` in verbose mode renders score and threshold
  - Unit: `test_quality_score_event_debug_includes_gap` — debug mode includes `gap_analysis` text
  - Unit: `test_quality_score_event_quiet_returns_none` — quiet mode returns `None` (suppressed)
  - Unit: `test_quality_score_event_normal_returns_none` — normal mode returns `None`
  - Unit: `test_exhaustion_event_same_best_all_modes` — `ValidatorExhaustionEvent(best_attempt=2, ..., total_attempts=2)` renders in quiet mode
  - Unit: `test_exhaustion_event_different_best` — `best_attempt=1, total_attempts=3` → message references attempt 1
  - Checkpoint: `uv run pytest tests/chat/test_telegram_formatter.py -k "quality_score or exhaustion" -v`

#### Task 4.2 — `event_renderer.py`: render validator events for history
- [ ] **File**: `archon/ai/event_renderer.py`
- **Depends on**: Task 1.2
- **Description**:
  - Import `QualityScoreEvent` and `ValidatorExhaustionEvent`
  - `QualityScoreEvent`: render as markdown — always include gap_analysis in history (full detail is useful for debugging):
    - `f"🔍 **Validator** attempt {event.attempt}: score {event.score}/{event.threshold} ({'approved' if event.approved else 'rejected'})\n> {event.gap_analysis}"`
  - `ValidatorExhaustionEvent`:
    - best = last: `f"🔍 **Validator** retries exhausted — final score {event.last_score}/{event.threshold}"`
    - best ≠ last: `f"🔍 **Validator** retries exhausted — attempt {event.best_attempt} was best ({event.best_score} vs {event.last_score})"`
  - Add both new event types to `_EVENT_TYPE_MAP` so they are suppressible via `[history] suppressed_events` config:
    - `QualityScoreEvent`: key `"quality_score"`
    - `ValidatorExhaustionEvent`: key `"validator_exhaustion"`
  - Update `VALID_SUPPRESSED_EVENT_NAMES` (or its equivalent validation set) to include both new names
- **Releasable**: history markdown files capture full validator decision trail
- **Tests (TDD)** — `tests/ai/test_event_renderer.py`:
  - Unit: `test_render_quality_score_approved` — includes "approved" in output
  - Unit: `test_render_quality_score_rejected_with_gap` — gap_analysis text present in output
  - Unit: `test_render_exhaustion_same_best` — last score in output
  - Unit: `test_render_exhaustion_different_best` — better attempt number in output
  - Unit: `test_quality_score_event_in_event_type_map` — `"quality_score"` is in `_EVENT_TYPE_MAP` and maps to `QualityScoreEvent`
  - Unit: `test_validator_exhaustion_in_event_type_map` — `"validator_exhaustion"` is in `_EVENT_TYPE_MAP` and maps to `ValidatorExhaustionEvent`
  - Unit: `test_quality_score_suppressed_in_history` — when `"quality_score"` is in `config.history.suppressed_events`, `EventRenderer` skips `QualityScoreEvent`
  - Checkpoint: `uv run pytest tests/ai/test_event_renderer.py -k "quality_score or exhaustion" -v`

#### Task 4.3 — `handler.py`: wire validator events into mode filtering
- [ ] **File**: `archon/chat/handler.py`
- **Depends on**: Task 1.2
- **Description**:
  - In `handler.py`, locate the catch-all quiet/normal suppression guard: `elif not isinstance(event, (Response, ErrorEvent)): continue`. Add `ValidatorExhaustionEvent` to this tuple so it passes through in all modes: `elif not isinstance(event, (Response, ErrorEvent, ValidatorExhaustionEvent)): continue`
  - `QualityScoreEvent` requires NO change to the catch-all — it is already suppressed by the existing catch-all in quiet/normal mode (it is not `Response` or `ErrorEvent`). Do NOT add an explicit `QualityScoreEvent` suppression branch.
  - Import both `QualityScoreEvent` and `ValidatorExhaustionEvent` at the top of `handler.py`
- **Releasable**: validator events routed correctly to Telegram in all notification modes
- **Tests (TDD)** — `tests/chat/test_handler.py`:
  - Unit: `test_handler_exhaustion_event_passes_through_quiet_mode` — `ValidatorExhaustionEvent` is forwarded to Telegram even in quiet mode
  - Unit: `test_handler_quality_score_suppressed_quiet_mode` — `QualityScoreEvent` does NOT produce a Telegram message in quiet mode
  - Checkpoint: `uv run pytest tests/chat/test_handler.py -k "validator or quality_score or exhaustion" -v`

---

### Phase 5 — Documentation
> **Releasable**: after this phase — feature is fully documented

#### Task 5.1 — CLAUDE.md, config.toml.example, Architecture doc
- [ ] **Files**: `CLAUDE.md`, `examples/config.toml.example`, `Documentation/Architecture/110_component_catalog_and_layer_breakdown.md`
- **Depends on**: Task 1.1, Task 1.2
- **Description**:
  - `CLAUDE.md`, section `Output event model` table: add two rows:
    - `QualityScoreEvent` → `🔍 Validator: score N/T (attempt N)` (verbose/debug only)
    - `ValidatorExhaustionEvent` → `🔍 Validator: retries exhausted (score: N/T).` (all modes)
  - `CLAUDE.md`, section `Configuration`, add `[validator]` entry: `enabled` (default `false`), `result_score_threshold` (int, default `80`), `max_retries` (int, default `2`)
  - `examples/config.toml.example`: add `[validator]` section (commented out defaults) with latency/cost note
  - `Documentation/Architecture/110_component_catalog_and_layer_breakdown.md`: add `ResponseValidator` to the satellite sessions section noting: ephemeral session per validate call, user's default model, follows same pattern as Classifier
- **Releasable**: documentation accurate for new feature
- **Tests (TDD)**: none (documentation task)
  - Checkpoint: `uv run pytest -q --tb=no` (full suite sanity check)
