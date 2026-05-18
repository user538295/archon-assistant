# FEAT-035 — Response Validator

**Purpose**: Evaluate AI responses against user intent before delivery, with automatic retry on low quality scores.
**Audience**: End users receiving AI responses; system administrators configuring quality thresholds.
**Status**: Draft

---

## Background

The current Archon pipeline lacks a quality assurance layer. Responses are delivered immediately after generation, with no verification that the output actually meets the user's requirements. For complex tasks, multi-step plans, or ambiguous requests, this can result in incomplete or off-target responses.

The validator introduces a post-generation review agent that scores responses on a 0-100 scale against:
- **Intent match**: Does the response address the classified intent?
- **Completeness**: Does it cover all requirements from the original request?
- **Quality**: Is the answer well-formatted and actionable?
- **Accuracy**: Are there any obvious factual or logical errors?

If the score falls below a configurable threshold (default 80), the validator provides detailed feedback and the main session is instructed to improve its response. This loop continues until the score meets the threshold or `max_retries` (default 3) is exhausted.

---

## Goal

Add a transparent quality gate that ensures responses meet user expectations before delivery. Users see validation results only in debug/verbose mode. The system automatically retries improvements when the initial response is inadequate.

---

## Scope

### In Scope
- `ResponseValidator` class using main decomposer model (same model user selected) for evaluation
- `QualityScoreEvent` event dataclass for visibility
- `[validator]` section in `config.toml` with `enabled = true` to opt-in
- Pipeline integration after `send()` completion
- Retry loop using same main session context
- Improvement prompt with gap analysis
- Config-driven verbosity (debug/verbose only)
- Silent retry in Telegram (user sees only final response)
- Session history retains validation conversation for review

### Out of Scope
- Pre-execution plan validation (future: FEAT-XXX)
- Per-agent-wave validation during background tasks (future: FEAT-XXX)
- CLI command for `/validator` configuration
- Per-user threshold overrides via commands

---

## Acceptance criteria
- [ ] Validator can be enabled/disabled via `[validator] enabled = true/false` config
- [ ] Responses scoring below `result_score_threshold` trigger improvement attempts
- [ ] Improvement loop respects `max_retries` and does not hang indefinitely
- [ ] `QualityScoreEvent` visible only in debug/verbose notification mode
- [ ] Users in chat do not see intermediate (failed) responses — only final approved response
- [ ] Session history retains full validation conversation for user review
- [ ] Improvements reuse existing session context (no session restart)
- [ ] All new code passes existing test suite with ≥85% coverage on new modules
- [ ] Config validation rejects `result_score_threshold` outside [0, 100]

---

## What does NOT change
- Existing classification, routing, and decomposer logic (unchanged)
- Event streaming and Telegram delivery format (unchanged)
- Background agent execution (unchanged for this phase)
- Session lifecycle management (unchanged)
- History and compaction (unchanged)

---

## Known limitations / accepted trade-offs
1. **Same-model validation**: Using main decomposer model (not Sonnet) saves cost but may be less accurate at nuanced evaluation. Sonnet would be better for strict evaluation (future optimization).
2. **Same-session improvement**: The main session must recall the original failure. Without explicit context injection of the feedback, the session relies on its own memory. The improvement prompt includes full context to mitigate this.
3. **Silent retry in chat**: Users in normal mode do not see intermediate (failed) responses — only the final approved response. However, the session history retains the full conversation for transparency if users check later.
4. **No pre-execution validation**: Large multi-agent plans are approved after full execution, not before. Wasted resources possible if execution direction was wrong. Future phase will add pre-execution validation.

---

## Architecture

### New Components

#### `archon/ai/validator.py` — `ResponseValidator`

```python
@dataclass
class ValidationDecision:
    approved: bool
    score: int | None      # 0-100, None on error
    feedback: str           # reason or improvement instructions

class ResponseValidator:
    def __init__(
        self,
        model: str | None = None,  # Main decomposer model (inherit from config)
        result_score_threshold: int = 80,
        max_retries: int = 3,
        enabled: bool = True,  # Opt-in flag
        verbose: bool = True,  # Show feedback in chat
    )

    async def validate(
        self,
        original_prompt: str,
        classification: Classification | None,
        task_output: TaskOutput | None,
        events: list[tuple[float, Event]],
    ) -> ValidationDecision
        # Creates validator session, sends context, parses result

    async def improve_response(
        self,
        session: ClaudeSession,
        original_prompt: str,
        classification: Classification | None,
        task_output: TaskOutput | None,
        events: list[tuple[float, Event]],
        feedback: str,
    ) -> AsyncGenerator[Event, None]
        # Sends gap analysis prompt to same session, streams improved response
```

Constants:
- `_VALIDATE_TIMEOUT_S = 45.0`
- `_IMPROVE_TIMEOUT_S = 90.0`

#### `archon/ai/event_mapper.py` — `QualityScoreEvent`

```python
@dataclass
class QualityScoreEvent:
    """Emitted by Pipeline after validation."""
    score: int | None          # 0-100 or None on error
    approved: bool
    feedback: str = ""
    retries: int = 0
    source: str = "validator"
```

Added to `Event` type union.

#### `archon/ai/prompts/validator.md`

System prompt for quality evaluation agent. Specifies:
- Evaluation dimensions (intent match, completeness, quality, accuracy)
- JSON output format with `score`, `approved`, `feedback`
- Scoring rubric (0-100 scale with breakpoints)
- Example high/low score decisions

#### `archon/config/loader.py` — `[validator]` section

Config keys with defaults:
| Key | Type | Default | Validation |
|-----|------|---------|------------|
| `enabled` | bool | `false` | — |
| `result_score_threshold` | int | 80 | Must be in [0, 100] |
| `max_retries` | int | 3 | Must be ≥ 0 |
| `model` | str | `None` → inherits `models.default` | Must be in `models.available` |
| `verbose` | bool | `True` | Whether to show feedback in chat |

**Note**: `enabled = false` by default (opt-in). Users must explicitly enable the validator.

#### `archon/chat/md_formatter.py` — QualityScoreEvent rendering

Telegram format (debug/verbose only):
- **Approved**: `✅ **Quality Score**: {score}/100 — Approved`
- **Rejected**: `❌ **Quality Score**: {score}/100 — Rejected` (+ feedback preview)
- **Retry count**: `⚠️ After {retries} improvement attempt(s)`

#### `archon/gateway/main.py` — Validator wiring

Creates `ResponseValidator` from config, passes to `Pipeline`.

### Modified Components

#### `archon/ai/pipeline.py` — `Pipeline.send()`

After collecting all response events:
```python
# Collect response events (exclude router events, include only Response + Thinking)
response_events = [(ts, e) for ts, e in all_events 
                   if isinstance(e, (Response, ThinkingResult)) 
                   and not is_router_event(e)]

# Validate
decision = await self._validator.validate(
    original_prompt=prompt,
    classification=result.classification,
    task_output=task_output,
    events=response_events,
)

# Yield QualityScoreEvent (verbose/debug only)
yield QualityScoreEvent(
    score=decision.score,
    approved=decision.approved,
    feedback="",
    retries=retry_count,
)

# If not approved, retry
if not decision.approved and retry_count < self._validator.max_retries:
    retry_count += 1
    # Improve response using same session
    async for event in self._validator.improve_response(
        self._decomposer._session, prompt, result.classification,
        task_output, response_events, decision.feedback,
    ):
        # Collect improved events for next validation
        ...
    response_events = improved_response_events
    continue

if not decision.approved and retry_count >= self._validator.max_retries:
    # Deliver original response with error notification
    # (User sees this + one error message about validator failure)
    yield Response(content=original_response_content)
    yield ErrorEvent(
        message=f"Validator error after {retry_count} retries: {decision.feedback}",
        source="pipeline",
    )
```

---

## Tests

See plan tasks for full test specification.

- `tests/ai/test_validator.py` — Unit tests for `ResponseValidator`
- `tests/ai/test_pipeline_validation.py` — Integration tests for pipeline validation flow
- `tests/config/test_validator_config.py` — Config loading and validation tests
- `tests/chat/test_md_formatter_validation.py` — Event rendering tests
- `tests/gateway/test_validation_integration.py` — E2E flow tests

---

## Documentation update
- [ ] `config.toml.example` — Add `[validator]` section with comments
- [ ] Architecture docs (if section exists) — Add validator component description
- [ ] ADR directory — Consider creating ADR for validator design decision

---

## Task breakdown

### Phase 1 — Core Validator Module & Config

#### Task 1.1 — Create `archon/ai/validator.py`
- [ ] **File**: `archon/ai/validator.py`
- **Depends on**: nothing
- **Description**:
  - `ResponseValidator` class with **main decomposer model** session
  - `ValidationDecision` dataclass (`approved: bool`, `score: int | None`, `feedback: str`)
  - `validate(original_prompt, classification, task_output, events)` → creates validator session, evaluates response against user intent
  - `improve_response(session, original_prompt, classification, task_output, events, feedback)` → sends gap analysis prompt to main session for improvement
  - Uses `archon/ai/prompts/validator.md` for system prompt (loaded via `load_prompt()`)
  - Timeout handling: `_VALIDATE_TIMEOUT_S = 45.0`, `_IMPROVE_TIMEOUT_S = 90.0`
  - Uses rolling-deadline pattern for async generators (never `asyncio.timeout()` across yields)
  - All context events passed as `list[tuple[float, Event]]` (frozen copy for safety)
  - `enabled` flag checked before running validation
- **Releasable**: After this task, the validator can score and classify responses as pass/fail using the main decomposer model.
- **Tests** (TDD) — `tests/ai/test_validator.py`:
  - Unit: `test_validate_returns_approved_high_score` — mock model returns 90+ → approved=True
  - Unit: `test_validate_returns_denied_low_score` — mock model returns below threshold → approved=False
  - Unit: `test_validate_sends_context_block_to_model` — verify prompt structure includes original prompt + classification + task output
  - Unit: `test_validate_uses_classified_intent` — Classification passed through to validator prompt
  - Unit: `test_validate_disabled_does_not_run` — `enabled=False` → skips validation
  - Unit: `test_validate_timeout_falls_back_to_approved` — TimeoutError → approved=True with warning log
  - Unit: `test_validate_handles_api_error` — SDK error → approved=True with notification
  - Unit: `test_improve_response_returns_stream` — verify generator pattern
  - Unit: `test_improve_response_includes_feedback_context` — feedback included in improvement prompt
- **Checkpoint**: `pytest tests/ai/test_validator.py -v`

#### Task 1.2 — Create `archon/ai/prompts/validator.md`
- [ ] **File**: `archon/ai/prompts/validator.md`
- **Depends on**: nothing
- **Description**:
  - System prompt for quality validation agent
  - Instructions to evaluate: intent match, completeness, quality, accuracy
  - Output format: `{"score": int, "approved": bool, "feedback": string}`
  - Scoring rubric: 90+ = approved, 0-79 = needs improvement
  - Include examples of high-score vs low-score responses
  - Must instruct LLM to be precise and factual, avoid vague assessments
- **Releasable**: Validator has production-ready prompt for evaluation.

---

### Phase 2 — Pipeline Integration

#### Task 2.1 — Add `QualityScoreEvent` to `archon/ai/event_mapper.py`
- [ ] **File**: `archon/ai/event_mapper.py`
- **Depends on**: Task 1.1 (ValidationDecision exists)
- **Description**:
  - New dataclass:
    ```python
    @dataclass
    class QualityScoreEvent:
        """Emitted by Pipeline after validation."""
        score: int | None          # 0-100 or None on error
        approved: bool
        feedback: str = ""
        retries: int = 0
        source: str = "validator"
    ```
  - Add to `Event` type union
  - Source is always `"validator"` for consistent filtering
- **Releasable**: Event type exists for Telegram delivery.
- **Tests** (TDD) — `tests/ai/test_md_formatter_validation.py`:
  - Unit: `test_quality_score_event_dataclass` — all fields accessible
  - Unit: `test_quality_score_event_in_event_union` — event mapper handles it

**Checkpoint**: `pytest tests/ai/test_event_mapper.py -v`

#### Task 2.2 — Integrate `ResponseValidator` into `archon/ai/pipeline.py`
- [ ] **File**: `archon/ai/pipeline.py`
- **Depends on**: Task 1.1 (ResponseValidator), Task 2.1 (QualityScoreEvent)
- **Description**:
  - In `__init__`: `self._validator: ResponseValidator | None = validator`
  - Add `validator: ResponseValidator | None = None` parameter to constructor
  - In `send()`: After collecting all response events, add validation loop
  - Collect response events: `Response` + `ThinkingResult` only, exclude router events
  - If `approved` → yield `QualityScoreEvent` once, return
  - If not approved → call `improve_response()` with current session, repeat loop
  - Retry count tracked and capped at `max_retries`
  - On retry exhaustion without approval → deliver original response + ErrorEvent notification
  - Events passed to validator: `response_events` (frozen copy of collected events)
  - Timeout on improvement: `_IMPROVE_TIMEOUT_S = 90.0` per retry
  - `QualityScoreEvent` emitted at end of each validation cycle (user sees latest score)
  - **User visible behavior**:
    - Normal mode: User sees only final approved response
    - Debug/verbose mode: User sees QualityScoreEvent at validation outcome
    - Validator error: User sees original response + error notification message
- **Releasable**: Full validation loop integrated into pipeline request flow.
- **Tests** (TDD) — `tests/ai/test_pipeline_validation.py`:
  - Unit: `test_validation_passes_approved_response` — score ≥ threshold → no retry
  - Unit: `test_validation_triggers_improvement` — score < threshold → improve_response called
  - Unit: `test_validation_max_retries_exhausted` — after X retries still below → deliver original + error notice
  - Unit: `test_validation_no_validator_configured` — validator=None → skip validation, pass through
  - Unit: `test_validation_disabled` — enabled=False → skip validation
  - Unit: `test_validation_visible_in_debug_mode` — QualityScoreEvent sent in debug config
  - Unit: `test_validation_visible_in_verbose_mode` — QualityScoreEvent sent in verbose config
  - Unit: `test_validation_not_visible_in_quiet_mode` — QualityScoreEvent NOT sent in quiet config
  - Unit: `test_validation_passes_classification_context` — Classification passed to validator
  - Unit: `test_validation_passes_task_output_context` — TaskOutput passed to validator
  - Unit: `test_validation_improvement_timeout` — improve() times out → treated as approved
  - Unit: `test_validation_improvement_error` — improve() raises → treated as approved with warning
- **Checkpoint**: `pytest tests/ai/test_pipeline_validation.py tests/ai/test_validator.py -v`

---

### Phase 3 — Config & Event Rendering

#### Task 3.1 — Add `[validator]` section to `archon/config/loader.py`
- [ ] **File**: `archon/config/loader.py`
- **Depends on**: nothing
- **Description**:
  - Add to config section defaults:
    ```python
    VALIDATOR = {
        "enabled": False,  # Opt-in by default
        "result_score_threshold": 80,
        "max_retries": 3,
        "model": None,  # None = use DEFAULT_MODEL
        "verbose": True,
    }
    ```
  - Validation: `result_score_threshold` in [0, 100], `max_retries` >= 0
  - If model is None, inherit from `models.default`
  - Add to `merge_with_defaults()` if not present in user config
- **Releasable**: Config can be loaded, default values set.
- **Tests** (TDD) — `tests/config/test_validator_config.py`:
  - Unit: `test_validator_defaults_loaded` — all defaults present
  - Unit: `test_validator_enabled_opt_in_default` — `enabled=False` by default
  - Unit: `test_validator_threshold_range_validation` — values outside [0,100] raise ConfigError
  - Unit: `test_validator_max_retries_min_zero` — negative raises ConfigError
  - Unit: `test_validator_model_inherits_default` — None → uses models.default
  - Unit: `test_validator_config_section_preserved` — non-validator keys not removed
  - Unit: `test_validator_custom_values_override` — custom values properly loaded
- **Checkpoint**: `pytest tests/config/test_validator_config.py -v`

#### Task 3.2 — Add `QualityScoreEvent` rendering to `archon/chat/md_formatter.py`
- [ ] **File**: `archon/chat/md_formatter.py` (or `EventRenderer` if separate)
- **Depends on**: Task 2.1 (QualityScoreEvent class)
- **Description**:
  - Handle `QualityScoreEvent` in `_process_element()` or `EventRenderer`
  - Telegram format (visible in debug/verbose only):
    - **Approved**: `✅ **Quality Score**: {score}/100 — Approved`
    - **Rejected**: `❌ **Quality Score**: {score}/100 — Rejected` (truncate feedback to 80 chars)
  - Include retry count if applicable: `⚠️ After {retries} improvement attempt(s)`
  - **Rendering restriction**: `quiet` mode = hide, `normal` mode = hide, `verbose/debug` mode = show
- **Releasable**: Users see validation results in debug/verbose mode.
- **Tests** (TDD) — `tests/chat/test_md_formatter_validation.py`:
  - Unit: `test_approved_score_formatter` — formatted correctly with ✅
  - Unit: `test_rejected_score_formatter` — formatted with ❌ and feedback truncation
  - Unit: `test_retry_count_included` — shows attempt count
  - Unit: `test_quality_event_not_rendered_in_quiet` — hidden in quiet mode
  - Unit: `test_quality_event_not_rendered_in_normal` — hidden in normal mode
  - Unit: `test_quality_event_rendered_in_verbose` — shown in verbose mode
  - Unit: `test_quality_event_rendered_in_debug` — shown in debug mode
- **Checkpoint**: `pytest tests/chat/test_md_formatter_validation.py -v`

---

### Phase 4 — Gateway Wiring & Final Integration

#### Task 4.1 — Wire `ResponseValidator` in `archon/gateway/main.py`
- [ ] **File**: `archon/gateway/main.py` (or wherever Pipeline is instantiated)
- **Depends on**: Task 3.1 (config section exists)
- **Description**:
  - Read validator config from `cfg.validator` (or `config.validator`)
  - Handle case where validator section is missing (use defaults)
  - Check `enabled` flag — if `false`, do not create `ResponseValidator` (pass `None` to Pipeline)
  - Create `ResponseValidator(model=..., enabled=..., threshold=..., max_retries=..., verbose=...)`
  - Pass to Pipeline: `Pipeline(validator=validator)`
  - If model specified in validator config is not in `models.available`, log warning and fall back to `models.default`
- **Releasable**: Validator active in production deployment.
- **Tests** (TDD) — `tests/gateway/test_gateway_validation_wiring.py`:
  - Unit: `test_validator_wired_from_config` — validator created with config values
  - Unit: `test_validator_disabled_not_created` — `enabled=False` → Pipeline gets None
  - Unit: `test_validator_default_when_section_missing` — uses defaults when [validator] absent
  - Unit: `test_validator_invalid_model_falls_back` — invalid model name → fallback to default
- **Checkpoint**: `pytest tests/gateway/ -v`

---

### Phase 5 — Integration Tests

#### Task 5.1 — Integration test for full validation flow
- [ ] **File**: `tests/gateway/test_validation_integration.py`
- **Depends on**: All previous tasks
- **Description**:
  - Test complete flow: classification → task → response → validation → improvement → approval
  - Mock model responses across retries
  - Verify state machine: request → generate → validate → [approved or improve] → response
  - Test: single-request approval (score ≥ threshold immediately)
  - Test: improvement succeeds (score < threshold first, then ≥ threshold after retry)
  - Test: improvement fails (score < threshold after max_retries, deliver original + notice)
  - Test: validator disabled (config=None or disabled flag)
  - Note: This is an integration test, may require mocking `ResponseValidator.validate()` and `improve_response()`
- **Tests**:
  - Integration: `test_full_validation_approve_first_pass` — single-request approval
  - Integration: `test_full_validation_improve_then_approve` — improvement succeeds
  - Integration: `test_full_validation_max_retries_rejected` — improvement fails
  - Integration: `test_full_validation_disabled_skipped` — validator disabled → pass through
- **Checkpoint**: `pytest tests/gateway/test_validation_integration.py -v`

---

### Phase 6 — Config Example Update

#### Task 6.1 — Update `examples/config.toml.example`
- [ ] **File**: `examples/config.toml.example`
- **Depends on**: Task 3.1
- **Description**:
  - Add `[validator]` section with annotated comments
  - Show all configurable options with defaults
  - Include example values for different use cases:
    - Strict: `result_score_threshold = 90`
    - Balanced: `result_score_threshold = 80`
    - Relaxed: `result_score_threshold = 70`
  - Add comment: `enabled = true` to activate the validator
- **Releasable**: Config reference includes new section.
- **Checkpoint**: `pytest tests/config/ -v` (no code change, documentation only)

---

## Total Tasks: 9 (+ 1 integration test task)

---

## Resource Impact Estimate

| Metric | Value |
|--------|-------|
| Additional LLM calls per request | 1 (+ 1 per retry) |
| Average additional latency | 2-8 seconds |
| Cost increase (single retry) | ~15-40% (proportional to response length) |
| Code lines added | ~400-600 (validator + tests + config) |
| Files modified | 5 (pipeline, event_mapper, md_formatter, loader, gateway) |
| Files added | 3 (validator.py, validator.md, test files) |
