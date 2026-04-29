# FEAT-035 — Pre-Response Validator
**Purpose**: Add a pre-execution quality gate that validates the decomposer's plan/decision before streaming the response, with automatic retry on invalid plans.
**Audience**: End users receiving AI responses; system administrators configuring quality thresholds.
**Status**: Draft (updated to reflect pre-response validation architecture)

---

## Background

The current Archon pipeline lacks a quality assurance layer. Plans/decisions are executed immediately via `Pipeline.send()`, with no verification that the decomposer's output task scope or plan is appropriate for the request. For complex tasks or ambiguous requests, this can result in inefficient or incorrect execution paths.

The validator introduces a pre-execution review step that scores the decomposer's **plan/decision** (TaskOutput) on a 0-100 scale against:
- **Scope appropriateness**: Does the selected scope (`large`/`small`) match the task complexity?
- **Feasibility**: Is the proposed plan executable within constraints?
- **Correctness**: Are routing decisions aligned with the classified intent?
- **Resource efficiency**: Is the plan using resources proportionally to the task?

If the score falls below a configurable threshold (default 80), the validator provides detailed feedback and `route_task()` is called again with improved instructions. This loop continues until the score meets the threshold or `max_retries` (default 3) is exhausted.

**Key architectural point**: Validation happens on the **decomposer's output** before execution (not after streaming is complete). This avoids the fundamental streaming incompatibility of buffering entire responses.

---

## Goal

Add a transparent quality gate that ensures responses meet user expectations before delivery. Users see validation results only in debug/verbose mode. The system automatically retries improvements when the initial response is inadequate.

---

## Scope

### In Scope
- `ResponseValidator` class using main decomposer model (same model user selected) for evaluation
- `QualityScoreEvent` event dataclass for visibility
- `[validator]` section in `config.toml` with `enabled = false` by default (opt-in)
- Pipeline integration during `send()` before plan execution
- Retry loop calling `route_task()` with improved instructions
- Improvement prompt with gap analysis for decomposer
- Config-driven verbosity (debug/verbose only)
- Silent retry in Telegram (user sees only final response)
- Session history retains validation conversation for review

### Out of Scope
- Pre-execution plan validation (future: FEAT-XXX)
- Per-agent-wave validation during background tasks (future: FEAT-XXX)
- CLI command for `/validator` configuration
- Per-user threshold overrides via commands
- Per-request quality score override via Telegram command

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
- Existing Event type union — only `QualityScoreEvent` added

---

## Known limitations / accepted trade-offs
1. **Same-model validation**: Using main decomposer model (not Sonnet) saves cost but may be less accurate at nuanced evaluation. Sonnet would be better for strict evaluation (future optimization).
2. **Same-session improvement**: The main session must recall the original failure. Without explicit context injection of the feedback, the session relies on its own memory. The improvement prompt includes full context to mitigate this.
3. **Silent retry in chat**: Users in normal mode do not see intermediate (failed) responses — only the final approved response. However, the session history retains the full conversation for transparency if users check later.
4. **No pre-execution validation**: Large multi-agent plans are approved after full execution, not before. Wasted resources possible if execution direction was wrong. Future phase will add pre-execution validation.
5. **Blocking behavior**: Validation adds 2-8 seconds of latency per request. In quiet mode this is transparent; in verbose/debug mode the user sees the QualityScoreEvent during wait.

---

## Architecture

### New Components

#### `archon/ai/validator.py` — `ResponseValidator`

```python
@dataclass
class ValidationDecision:
    """Result of a single validation attempt."""
    approved: bool
    score: int | None      # 0-100, None on error
    feedback: str           # reason or improvement instructions

class ResponseValidator:
    def __init__(
        self,
        model: str | None = None,  # Main decomposer model (inherit from config)
        result_score_threshold: int = 80,
        max_retries: int = 3,
        enabled: bool = False,  # Opt-in flag (must match config default)
        verbose: bool = True,  # Show feedback in chat
    ) -> None

    async def validate(
        self,
        original_prompt: str,
        classification: Classification | None,
        task_output: TaskOutput,  # REQUIRED - validates decomposer's plan
    ) -> ValidationDecision:
        # Creates validator session, sends context, parses result
        # Timeout: _VALIDATE_TIMEOUT_S = 45.0
        # Uses rolling-deadline pattern for async generator
        
    async def improve_route(
        self,
        prompt: str,
        classification: Classification,
        task_output: TaskOutput,
        feedback: str,
    ) -> AsyncGenerator[TaskOutput, None]:
        # Sends gap analysis prompt to decomposer, streams improved plan
        # Timeout: _IMPROVE_TIMEOUT_S = 300.0 (matches task timeout, not 90)
        # Returns TaskOutput, not Event stream
```

Constants:
- `_VALIDATE_TIMEOUT_S = 45.0`
- `_IMPROVE_TIMEOUT_S = 300.0`  # Must match task timeout, 90 is too short for complex improvement tasks

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

Added to `Event` type union (currently at line 212-231).

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

#### `archon/gateway/gateway.py` — Validator wiring

Creates `ResponseValidator` from config, passes to `Pipeline`.

### Modified Components

#### `archon/ai/pipeline.py` — `Pipeline.send()`

After collecting all response events (Response + ThinkingResult):
1. Run `self._validator.validate()` if validator is configured and enabled
2. If `approved` → yield `QualityScoreEvent` once, return
3. If not approved → call `improve_route()` with decomposer instructions, repeat loop
4. Retry count tracked and capped at `max_retries`
5. On retry exhaustion → continue with original plan + ErrorEvent notification
6. `QualityScoreEvent` emitted at end of each validation cycle (user sees latest score)
7. **User visible behavior**:
   - Normal/quiet mode: User sees only final approved response
   - Debug/verbose mode: User sees QualityScoreEvent at validation outcome
   - Validator error on retry: User sees original response + error notification message

---

## Tests

See task breakdown below for per-task test specifications.

### Complete test catalog:
- `tests/ai/test_validator.py` — Unit tests for `ResponseValidator`
- `tests/ai/test_pipeline_validation.py` — Integration tests for pipeline validation flow
- `tests/config/test_validator_config.py` — Config loading and validation tests
- `tests/chat/test_md_formatter_validation.py` — Event rendering tests
- `tests/gateway/test_validation_integration.py` — E2E flow tests
- `tests/ai/test_md_formatter_validation.py` — QualityScoreEvent dataclass tests

---

## Documentation update
- [ ] `examples/config.toml.example` — Add `[validator]` section with comments
- [ ] `Documentation/Architecture/` — Add validator component description to 110_component_catalog_and_layer_breakdown.md
- [ ] `Documentation/ADRs/` — Consider creating ADR for validator design decision

---

## Task breakdown

### Phase 1 — Core Validator Module & Prompt

> **Releasable**: after the validator can independently score responses

#### Task 1.1 — Create `archon/ai/validator.py`
- [ ] **File**: `archon/ai/validator.py`
- **Depends on**: nothing
- **Description**:
  - `ResponseValidator` class with main decomposer model session
  - `ValidationDecision` dataclass (`approved: bool`, `score: int | None`, `feedback: str`)
  - `validate(original_prompt: str, classification: Classification | None, task_output: TaskOutput | None, events: list[tuple[float, Event]]) -> ValidationDecision` — creates validator session, evaluates response against user intent
  - `improve_route(prompt: str, classification: Classification | None, task_output: TaskOutput, feedback: str) -> AsyncGenerator[TaskOutput, None]` — sends gap analysis prompt to decomposer for improved plan, returns TaskOutput
  - Uses `archon/ai/prompts/validator.md` for system prompt (loaded via `load_prompt()`)
  - Timeout constants: `_VALIDATE_TIMEOUT_S = 45.0`, `_IMPROVE_TIMEOUT_S = 90.0`
  - Uses rolling-deadline pattern for async generators (never `asyncio.timeout()` across yields)
  - All context events passed as `list[tuple[float, Event]]` (frozen copy for safety)
  - `enabled` flag checked before running validation
  - On validation timeout: log warning, return `ValidationDecision(approved=True, score=None, feedback="Validation timeout")`
  - On SDK error: log warning, return `ValidationDecision(approved=True, score=None, feedback="Validation error")`
- **Releasable**: After this task, the validator can score and classify responses as pass/fail using the main decomposer model.
- **Tests (TDD)** — `tests/ai/test_validator.py`:
  - Unit: `test_validate_returns_approved_high_score` — mock model returns 90+ → approved=True
  - Unit: `test_validate_returns_denied_low_score` — mock model returns below threshold → approved=False
  - Unit: `test_validate_sends_context_block_to_model` — verify prompt structure includes original prompt + classification + task output
  - Unit: `test_validate_uses_classified_intent` — Classification passed through to validator prompt
  - Unit: `test_validate_disabled_does_not_run` — `enabled=False` → skips validation, returns approved
  - Unit: `test_validate_timeout_falls_back_to_approved` — TimeoutError → approved=True with warning log
  - Unit: `test_validate_handles_api_error` — SDK error → approved=True with notification
  - Unit: `test_improve_route_returns_stream` — verify generator pattern
  - Unit: `test_improve_route_includes_feedback_context` — feedback included in improvement prompt
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
  - JSON output must be valid and parseable — include error handling instruction
- **Releasable**: Validator has production-ready prompt for evaluation.

---

### Phase 2 — Event Type & Rendering

> **Releasable**: QualityScoreEvent can be rendered and streamed (no validation logic yet)

#### Task 2.1 — Add `QualityScoreEvent` to `archon/ai/event_mapper.py`
- [ ] **File**: `archon/ai/event_mapper.py`
- **Depends on**: Task 1.1 (ValidationDecision exists)
- **Description**:
  - Create dataclass with exact structure:
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
  - Add to `Event` type union (line 212-231) — append after `RecoveryEvent`
  - Source is always `"validator"` for consistent filtering
  - Preserve existing `is_router_event()` function — no changes needed
- **Releasable**: Event type exists for Telegram delivery.
- **Tests (TDD)** — `tests/ai/test_md_formatter_validation.py`:
  - Unit: `test_quality_score_event_dataclass` — all fields accessible with defaults
  - Unit: `test_quality_score_event_in_event_union` — event mapper handles it
- **Checkpoint**: `pytest tests/ai/test_event_mapper.py -v`

#### Task 2.2 — Add `QualityScoreEvent` rendering to `archon/chat/md_formatter.py`
- [ ] **File**: `archon/chat/md_formatter.py`
- **Depends on**: Task 2.1 (QualityScoreEvent class)
- **Description**:
  - Handle `QualityScoreEvent` in `_process_element()` or `EventRenderer._render_event()`
  - Telegram format (visible in debug/verbose only based on notification config):
    - **Approved**: `✅ **Quality Score**: {score}/100 — Approved`
    - **Rejected**: `❌ **Quality Score**: {score}/100 — Rejected` (truncate feedback to 80 chars)
  - Include retry count if applicable: `⚠️ After {retries} improvement attempt(s)`
  - **Rendering restriction**: `quiet` mode = hide, `normal` mode = hide, `verbose/debug` mode = show
  - Must check `cfg.notifications.get("mode", "normal")` before rendering
- **Releasable**: Users can see validation results in debug/verbose mode.
- **Tests (TDD)** — `tests/chat/test_md_formatter_validation.py`:
  - Unit: `test_approved_score_formatter` — formatted correctly with ✅
  - Unit: `test_rejected_score_formatter` — formatted with ❌ and feedback truncation
  - Unit: `test_retry_count_included` — shows attempt count
  - Unit: `test_quality_event_not_rendered_in_quiet` — hidden in quiet mode
  - Unit: `test_quality_event_not_rendered_in_normal` — hidden in normal mode
  - Unit: `test_quality_event_rendered_in_verbose` — shown in verbose mode
  - Unit: `test_quality_event_rendered_in_debug` — shown in debug mode
- **Checkpoint**: `pytest tests/chat/test_md_formatter_validation.py -v`

---

### Phase 3 — Config & Schema

> **Releasable**: Config section can be loaded, validated, and applied to the validator

#### Task 3.1 — Add `[validator]` section to `archon/config/loader.py`
- [ ] **File**: `archon/config/loader.py`
- **Depends on**: nothing
- **Description**:
  - **IMPORTANT**: Use inline defaults pattern within `load_config()` — do NOT use `merge_with_defaults()` (does not exist)
  - Follow the established pattern used in `loader.py:830-856` for `reminder` config:
    ```python
    raw_validator = data.get("validator", {})
    validator_config = ValidatorConfig(
        enabled=bool(raw_validator.get("enabled", ValidatorConfig.enabled)),
        result_score_threshold=int(raw_validator.get("result_score_threshold", ValidatorConfig.result_score_threshold)),
        max_retries=int(raw_validator.get("max_retries", ValidatorConfig.max_retries)),
        model=raw_validator.get("model") or None,
        verbose=bool(raw_validator.get("verbose", ValidatorConfig.verbose)),
    )
    # Validation
    if not (0 <= validator_config.result_score_threshold <= 100):
        raise ConfigError("[validator] result_score_threshold must be in [0, 100]")
    if validator_config.max_retries < 0:
        raise ConfigError("[validator] max_retries must be >= 0")
    if validator_config.model is not None and validator_config.model not in models.available:
        logger.warning("[validator] model %r not in models.available, using %r", 
                       validator_config.model, models.default)
        validator_config = dataclasses.replace(validator_config, model=models.default)
    elif validator_config.model is None:
        validator_config = dataclasses.replace(validator_config, model=models.default)
    ```
  - **Add new config dataclass** in `archon/config/models.py` or inline in `loader.py`:
    ```python
    @dataclass(kw_only=True)
    class ValidatorConfig:
        enabled: bool = False  # Opt-in by default
        result_score_threshold: int = 80
        max_retries: int = 3
        model: str | None = None  # None → use Default model
        verbose: bool = True
    ```
  - Add to `Config` dataclass with `validator: ValidatorConfig` field
- **Releasable**: Config can be loaded, default values set, and validated.
- **Tests (TDD)** — `tests/config/test_validator_config.py`:
  - Unit: `test_validator_defaults_loaded` — all defaults present
  - Unit: `test_validator_enabled_opt_in_default` — `enabled=False` by default
  - Unit: `test_validator_threshold_range_validation` — values outside [0,100] raise ConfigError
  - Unit: `test_validator_max_retries_min_zero` — negative raises ConfigError
  - Unit: `test_validator_model_inherits_default` — None → uses models.default
  - Unit: `test_validator_config_section_preserved` — non-validator keys not removed
  - Unit: `test_validator_custom_values_override` — custom values properly loaded
  - Unit: `test_validator_invalid_model_fallback` — invalid model name falls back to default
- **Checkpoint**: `pytest tests/config/test_validator_config.py -v`

---

### Phase 4 — Pipeline Integration

> **Releasable**: Full validation loop integrated into pipeline request flow

#### Task 4.1 — Integrate `ResponseValidator` into `archon/ai/pipeline.py`
- [ ] **File**: `archon/ai/pipeline.py`
- **Depends on**: Task 1.1 (ResponseValidator), Task 2.1 (QualityScoreEvent)
- **CRITICAL**: Validation MUST happen on decomposer's plan/decision BEFORE streaming (not after). This is the fundamental architectural shift.
- **Description**:
  - In `__init__`: `self._validator: ResponseValidator | None = validator`
  - Add `validator: ResponseValidator | None = None` parameter to constructor
  - **In `send()`**: Insert validation **AFTER `route_task()` returns TaskOutput, BEFORE executing the plan** (around line 267-300):
    ```python
    # ── Existing route_task logic (lines 249-266) ──
    task_output: TaskOutput | None = None
    router_gen = self._decomposer.route_task(prompt, search_pre_context=search_pre_context)
    try:
        async for item in router_gen:
            if isinstance(item, TaskOutput):
                task_output = item
            else:
                assert hasattr(item, "source"), f"Event {type(item).__name__} missing 'source' field"
                yield dataclasses.replace(item, source="router")
    finally:
        try:
            await asyncio.wait_for(router_gen.aclose(), timeout=_ACLOSE_TIMEOUT_S)
        except Exception:
            logger.warning(
                "pipeline.send: router_gen.aclose() timed out or failed for prompt: %.100s",
                prompt,
                exc_info=True,
            )

    # ── NEW: Pre-execution validation (lines 268-291) ──
    # Validate decomposer's plan BEFORE executing it
    if self._validator is not None and self._validator.enabled:
        decision = await self._validator.validate(
            original_prompt=prompt,
            classification=result.classification if result else None,
            task_output=task_output or TaskOutput(scope="small", prompt=prompt, is_fallback=True, fallback_reason="validation sentinel"),
        )
        
        # Yield QualityScoreEvent (verbose/debug only)
        yield QualityScoreEvent(
            score=decision.score,
            approved=decision.approved,
            feedback="",
            retries=retry_count,
        )
        
        # If not approved, retry with improved instructions
        while not decision.approved and retry_count < self._validator.max_retries:
            retry_count += 1
            # Call route_task again with improved instructions
            async for item in self._validator.improve_route(
                prompt,
                result.classification if result else None,
                task_output,
                decision.feedback,
            ):
                # Pass through improvements as router events
                yield dataclasses.replace(item, source="router")
                if isinstance(item, TaskOutput):
                    task_output = item
            
            # Re-validate
            decision = await self._validator.validate(
                prompt,
                result.classification if result else None,
                task_output,
            )
            yield QualityScoreEvent(score=decision.score, approved=decision.approved,
                                    feedback="", retries=retry_count)
        
        # Final decision: if still not approved after max retries, continue with
        # original task_output but notify user in debug/verbose mode
        if not decision.approved and retry_count >= self._validator.max_retries:
            # Log warning, continue execution with original plan
            logger.warning(
                "Validator rejected plan after %d retries, continuing with original plan: %s",
                retry_count, decision.feedback,
            )
            yield ErrorEvent(
                message=f"Validator rejected plan after {retry_count} retries: {decision.feedback}",
                source="pipeline",
            )
    ```
  - **IMPORTANT**: Do NOT collect response events - validate the **plan** (TaskOutput), not the response
  - **IMPORTANT**: Do NOT call `self._validator.improve_response()` - call `improve_route()` which returns TaskOutput, not Event stream
  - **IMPORTANT**: Do NOT access `self._decomposer._session` - the validator works with TaskOutput, not sessions
  - Timeout on improvement: `_IMPROVE_TIMEOUT_S = 300.0` per retry (matches task timeout)
  - `QualityScoreEvent` emitted at end of each validation cycle (user sees latest score)
  - **User visible behavior**:
    - Normal/quiet mode: User sees only final approved response
    - Debug/verbose mode: User sees QualityScoreEvent at validation outcome
    - Validator error: User sees error notification but execution continues
  - If validator is `None` or `enabled=False` — skip validation entirely
- **Releasable**: Full validation loop integrated into pipeline request flow.
- **Tests (TDD)** — `tests/ai/test_pipeline_validation.py`:
  - Unit: `test_validation_passes_approved_plan` — score ≥ threshold → no retry
  - Unit: `test_validation_triggers_improvement` — score < threshold → improve_route called
  - Unit: `test_validation_max_retries_exhausted` — after X retries still below → continue with original plan + warning
  - Unit: `test_validation_no_validator_configured` — validator=None → skip validation, pass through
  - Unit: `test_validation_disabled` — enabled=False → skip validation
  - Unit: `test_validation_visible_in_debug_mode` — QualityScoreEvent sent in debug config
  - Unit: `test_validation_visible_in_verbose_mode` — QualityScoreEvent sent in verbose config
  - Unit: `test_validation_not_visible_in_quiet_mode` — QualityScoreEvent NOT sent in quiet config
  - Unit: `test_validation_passes_classification_context` — Classification passed to validator
  - Unit: `test_validation_passes_task_output_context` — TaskOutput passed to validator
  - Unit: `test_validation_improvement_timeout` — improve_route() times out → treated as approved
  - Unit: `test_validation_improvement_error` — improve_route() raises → treated as approved with warning
- **Checkpoint**: `pytest tests/ai/test_pipeline_validation.py tests/ai/test_validator.py -v`

---

### Phase 5 — Gateway Wiring

> **Releasable**: Validator active in production deployment

#### Task 5.1 — Wire `ResponseValidator` in `archon/gateway/gateway.py`
- [ ] **File**: `archon/gateway/gateway.py`
- **Depends on**: Task 3.1 (config section exists)
- **Description**:
  - Read validator config from `cfg.validator` (or `config.validator`)
  - Handle case where validator section is missing (use defaults from Config)
  - Check `enabled` flag — if `false`, do not create `ResponseValidator` (pass `None` to Pipeline)
  - Create `ResponseValidator(model=..., enabled=..., threshold=..., max_retries=..., verbose=...)`
  - Pass to Pipeline: `Pipeline(validator=validator)`
  - If model specified in validator config is not in `models.available`, log warning and fall back to `models.default`
  - Import additions: `from archon.ai.validator import ResponseValidator`
- **Releasable**: Validator active in production deployment.
- **Tests (TDD)** — `tests/gateway/test_gateway_validation_wiring.py`:
  - Unit: `test_validator_wired_from_config` — validator created with config values
  - Unit: `test_validator_disabled_not_created` — `enabled=False` → Pipeline gets None
  - Unit: `test_validator_default_when_section_missing` — uses defaults when [validator] absent
  - Unit: `test_validator_invalid_model_falls_back` — invalid model name → fallback to default
- **Checkpoint**: `pytest tests/gateway/test_gateway_validation_wiring.py -v`

---

### Phase 6 — Integration Tests

> **Releasable**: Full end-to-end flow can be tested

#### Task 6.1 — Integration test for full validation flow
- [ ] **File**: `tests/gateway/test_validation_integration.py`
- **Depends on**: All previous tasks
- **Description**:
  - Test complete flow: classification → route_task → validation → improve_route → execution
  - Mock model responses across retries
  - Verify state machine: request → validate plan → [approved or improve_route] → execute
  - Test: single-request approval (score ≥ threshold immediately)
  - Test: improvement succeeds (score < threshold first, then ≥ threshold after retry)
  - Test: improvement fails (score < threshold after max_retries, continue with original plan + notice)
  - Test: validator disabled (config=None or disabled flag)
  - Note: This is an integration test, may require mocking `ResponseValidator.validate()` and `improve_route()`
- **Tests**:
  - Integration: `test_full_validation_approve_first_pass` — single-request approval
  - Integration: `test_full_validation_improve_then_approve` — improvement succeeds
  - Integration: `test_full_validation_max_retries_rejected` — improvement fails
  - Integration: `test_full_validation_disabled_skipped` — validator disabled → pass through
- **Checkpoint**: `pytest tests/gateway/test_validation_integration.py -v`

---

### Phase 7 — Documentation

> **Releasable**: User documentation for the feature

#### Task 7.1 — Update `examples/config.toml.example`
- [ ] **File**: `examples/config.toml.example`
- **Depends on**: Task 3.1
- **Description**:
  - Add `[validator]` section with annotated comments
  - Show all configurable options with defaults
  - Include example values for different use cases:
    - Strict: `result_score_threshold = 90`
    - Balanced: `result_score_threshold = 80`
    - Relaxed: `result_score_threshold = 70`
  - Add comment: `# enabled = true  # set to opt-in`
- **Releasable**: Config reference includes new section.
- **Checkpoint**: `pytest tests/config/ -v` (no code change, documentation only)

---

## Total Tasks: 10

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
