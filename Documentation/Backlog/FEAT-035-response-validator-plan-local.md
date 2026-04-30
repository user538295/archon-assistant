# FEAT-035 — Response Validator
**Purpose**: Add a post-execution quality gate that validates the decomposer's response **after** it is generated but **before** delivery to the user, with automatic retry on low-scoring responses.
**Audience**: End users receiving AI responses; system administrators configuring quality thresholds.
**Status**: Draft (updated — validation-after-generation architecture)

---

## Background

The current Archon pipeline lacks a quality assurance layer. Responses are delivered to users immediately after the decomposer generates them, with no verification that the response quality meets user expectations. For complex tasks or ambiguous requests, this can result in incomplete, incorrect, or suboptimal answers being delivered without remediation.

The validator introduces a post-execution review step that scores the decomposer's **response** (final Response text) on a 0-100 scale against:
- **Intent match**: Does the response address the classified intent correctly?
- **Completeness**: Is the answer comprehensive for the request scope?
- **Quality**: Is the reasoning sound and the output well-structured?
- **Accuracy**: Are facts and conclusions correct given the available context?

If the score falls below a configurable threshold (default 80), the validator provides detailed feedback and the decomposer is called again via `route_task()` with improved instructions. This loop continues until the score meets the threshold or `max_retries` (default 3) is exhausted.

**Key architectural point**: Validation happens **after** the decomposer generates a response but **before** delivery to the user. The event stream is collected into a buffer for validation; approved responses are yielded to the user, rejected responses are shown in debug/verbose mode before retry.

In debug/verbose mode, users see the validation flow like this:
```
❌ Quality Score: 72/100 — Rejected
❌ Response (rejected)
... rejected response text comes here ...
<then here comes the retry, we will call tools, thinking etc..>
💭 Thinking
...
🔧 Tool
 📤 Result
...
✅ Quality Score: 91/100 — Approved
✅ Response (approved)
... here comes the final answer ...
```

---

## Goal

Add a transparent quality gate that ensures responses meet user expectations before delivery. Users see validation results only in debug/verbose mode. The system automatically retries improvements when the initial response is inadequate.

---

## Scope

### In Scope
- `ResponseValidator` class using main decomposer model (same model user selected) for evaluation
- `QualityScoreEvent` event dataclass for visibility
- `[validator]` section in `config.toml` with `enabled = true` by default (opt-out)
- Pipeline integration during response completion
- Retry loop calling `route_task()` with improved instructions
- Improvement prompt with gap analysis for decomposer
- Config-driven verbosity (debug/verbose only)
- Silent retry in Telegram (user sees only final response)
- Session history retains validation conversation for review
- **Validation uses a dedicated validator session** (separate from main decomposer session)

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
2. **Same-session improvement**: The main session must recall the original failure. The improvement prompt includes full context to mitigate memory limitations via `route_task()` on the main session.
3. **Silent retry in chat**: Users in normal mode do not see intermediate (failed) responses — only the final approved response. However, the session history retains the full conversation for transparency if users check later.
4. **Validation latency**: Validation runs in a separate session and adds async overhead per request. In quiet mode this is transparent; in verbose/debug mode the user sees the QualityScoreEvent during wait.

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
        enabled: bool = True,  # Opt-out flag (must match config default)
    ) -> None:

    async def validate(
        self,
        original_prompt: str,
        classification: Classification | None,
        task_output: TaskOutput,
        response_content: str,  # Final Response text to validate
    ) -> ValidationDecision:
        # Queries a separate validator session
        # Timeout: _VALIDATE_TIMEOUT_S = 45.0
        # Uses rolling-deadline pattern for async generator
        
    # Note: improve_route is NOT a separate method.
    # The retry loop is implemented inline in Pipeline.send() by calling
    # self._decomposer.route_task() directly with an improved prompt that
    # includes the validation feedback. This avoids duplicating the
    # route_task() logic and ensures the decomposer uses the same
    # execution paths (tool calls, thinking, etc.) as the original request.
```

Constants:
- `_VALIDATE_TIMEOUT_S = 45.0`
- `_IMPROVE_TIMEOUT_S = 300.0`  # Must match task timeout

#### `archon/ai/event_mapper.py` — `QualityScoreEvent`

```python
@dataclass
class QualityScoreEvent:
    """Emitted by Pipeline after validation."""
    score: int | None          # 0-100 or None on error
    approved: bool
    feedback: str = ""
    retry_number: int = 0      # Number of retry attempts made
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
| `enabled` | bool | `true` | — |
| `result_score_threshold` | int | 80 | Must be in [0, 100] |
| `max_retries` | int | 3 | Must be ≥ 0 |
| `model` | str | `None` → inherits `models.default` | Must be in `models.available` |

**Note**: `enabled = true` by default (opt-out). Users must explicitly disable the validator if needed.
**Note**: `verbose` parameter removed — notification visibility is controlled by `Config.notifications.mode` at the delivery layer, not the validator.

#### `archon/chat/md_formatter.py` — QualityScoreEvent rendering

Telegram format (debug/verbose only):
- **Approved**: `✅ **Quality Score**: {score}/100 — Approved`
- **Rejected**: `❌ **Quality Score**: {score}/100 — Rejected` (+ feedback preview)
- **Retry count**: `⚠️ After {retry_number} improvement attempt(s)`

#### `archon/gateway/gateway.py` — Validator wiring

Creates `ResponseValidator` from config, passes to `Pipeline`.

### Modified Components

#### `archon/ai/pipeline.py` — `Pipeline.send()`

After the decomposer completes its response:
1. Collect all response events (Response + ThinkingResult) into a buffer
2. Extract final Response content and run `self._validator.validate()` if validator is configured and enabled
3. If `approved` → yield `QualityScoreEvent` once, yield buffered events to user
4. If not approved → in debug/verbose mode, show rejected response text and QualityScoreEvent, then call `route_task()` with improved instructions (inline retry loop)
5. Retry count tracked and capped at `max_retries`
6. On retry exhaustion → continue with original response + ErrorEvent notification
7. `QualityScoreEvent` emitted at end of each validation cycle (user sees latest score)
8. **User visible behavior**:
    - Normal/quiet mode: User sees only final approved response
    - Debug/verbose mode: User sees QualityScoreEvent at validation outcome, plus rejected response text before retry
    - Validator error on retry: User sees error notification but execution continues
    - Retries are visible in debug/verbose as full re-execution (tools, thinking, etc.)

**Validation is skipped for `scope="large"`** — plan-based tasks bypass this mechanism (see Known Limitations).

**IMPORTANT**: On retry, `route_task()` is called **directly** on the decomposer (not via a validator method). This reuses the same execution path (tool calls, thinking, etc.) as the original request and avoids duplicating logic.

---

## Tests

See task breakdown below for per-task test specifications.

### Complete test catalog:
- `tests/ai/test_validator.py` — Unit tests for `ResponseValidator`
- `tests/ai/test_pipeline_validation.py` — Integration tests for pipeline validation flow
- `tests/config/test_validator_config.py` — Config loading and validation tests
- `tests/chat/test_md_formatter_validation.py` — Event rendering tests
- `tests/gateway/test_validation_integration.py` — E2E flow tests

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
  - `ResponseValidator` class queries the main decomposer model session (same session as original execution)
  - `ValidationDecision` dataclass (`approved: bool`, `score: int | None`, `feedback: str`)
  - `validate(original_prompt: str, classification: Classification | None, task_output: TaskOutput, response_content: str) -> ValidationDecision` — queries main session, evaluates response against user intent
  - Uses `archon/ai/prompts/validator.md` for system prompt (loaded via `load_prompt()`)
  - Timeout constants: `_VALIDATE_TIMEOUT_S = 45.0`, `_IMPROVE_TIMEOUT_S = 300.0`
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
        retry_number: int = 0      # Number of retry attempts made
        source: str = "validator"
    ```
  - Add to `Event` type union (line 212-231) — append after `RecoveryEvent`
  - Source is always `"validator"` for consistent filtering
  - Preserve existing `is_router_event()` function — no changes needed
- **Releasable**: Event type exists for Telegram delivery.
- **Tests (TDD)** — `tests/chat/test_md_formatter_validation.py`:
  - Unit: `test_quality_score_event_dataclass` — all fields accessible with defaults
- **Checkpoint**: `pytest tests/ai/test_event_mapper.py -v`

#### Task 2.2 — Add `QualityScoreEvent` rendering to `archon/chat/md_formatter.py`
- [ ] **File**: `archon/chat/md_formatter.py`
- **Depends on**: Task 2.1 (QualityScoreEvent class)
- **Description**:
  - Handle `QualityScoreEvent` in `_process_element()` or `EventRenderer._render_event()`
  - Telegram format (visible in debug/verbose only based on notification config):
    - **Approved**: `✅ **Quality Score**: {score}/100 — Approved`
    - **Rejected**: `❌ **Quality Score**: {score}/100 — Rejected` (truncate feedback to 80 chars)
  - Include retry count if applicable: `⚠️ After {retry_number} improvement attempt(s)`
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
- **CRITICAL**: Validation MUST happen after response is generated but **before** delivery to the user.
- **Description**:
  - In `__init__`: `self._validator: ResponseValidator | None = validator`
  - Add `validator: ResponseValidator | None = None` parameter to constructor
  - **In `send()`**: Insert validation **AFTER `_task_direct_monitored()` returns all events** (for `scope="small"` / `scope="trivial"` only):
    ```python
    # ── Existing route_task logic (lines 249-323) ──
    # ... [route_task → TaskOutput → _task_direct_monitored()] ...
    
    # ── NEW: Post-response validation (after line 323) ──
    # NOTE: Only for scope != "large" (plan validation deferred to future phase)
    
    # Collect all response events for validation
    response_events = []
    async for event in self._task_direct_monitored(resolved, result.classification):
        response_events.append(event)
        yield event  # yield inline as before (for visibility during execution)
    
    # Only validate if validator is configured, enabled, and not a plan task
    if (self._validator is not None 
        and self._validator.enabled  # config.opt_in_flag
        and task_output.scope != "large"):  # skip plan-based execution
        # Extract final Response content for validation
        response_content = _extract_last_response_content(response_events)
        
        # Validation loop
        retry_count = 0
        approved = False
        last_decision = None
        
        while not approved and retry_count <= self._validator.max_retries:
            decision = await self._validator.validate(
                original_prompt=prompt,
                classification=result.classification if result else None,
                task_output=task_output or TaskOutput(scope="small", prompt=prompt, is_fallback=True, fallback_reason="validation sentinel"),
                response_content=response_content,
            )
            last_decision = decision
            
            # Yield QualityScoreEvent (verbose/debug only — filtered by md_formatter)
            yield QualityScoreEvent(
                score=decision.score,
                approved=decision.approved,
                feedback=decision.feedback,
                retry_number=retry_count,
            )
            
            if decision.approved:
                approved = True
                break
            
            # Not approved — check if we have retries left
            if retry_count >= self._validator.max_retries:
                # Retries exhausted, yield original response with warning
                logger.warning(
                    "Validator rejected response after %d retries: %s",
                    retry_count, decision.feedback,
                )
                yield ErrorEvent(
                    message=f"Validator rejected response after {retry_count} retries: {decision.feedback}",
                    source="validator",  # consistent with QualityScoreEvent.source
                )
                # Yield the original response events anyway
                for event in response_events:
                    yield event
                return
            
            # Retry with improved instructions — call route_task() directly
            # The decomposer uses the same execution path (tools, thinking, etc.)
            retry_count += 1
            logger.info("Validator rejected response, improving: %s", decision.feedback)
            
            # Build improved prompt with validation feedback
            improved_prompt = _build_improvement_prompt(
                original_prompt=prompt,
                feedback=decision.feedback,
                classification=result.classification,
            )
            
            # Clear collected events — we'll re-collect the retry output
            response_events.clear()
            
            # Re-execute via route_task (same path as original)
            async for event in self._task_direct_monitored(improved_prompt, result.classification):
                response_events.append(event)
                yield event  # yield for visibility in debug/verbose
            
            # Extract new response content for validation
            response_content = _extract_last_response_content(response_events)
        
        # Validation passed — yield QualityScoreEvent (approved) and buffered events
        if approved and last_decision:
            yield QualityScoreEvent(
                score=last_decision.score,
                approved=True,
                feedback=last_decision.feedback,
                retry_number=retry_count,
            )
            for event in response_events:
                yield event
        return
    else:
        # No validator or enabled=False or scope="large" — pass through
        # (events already yielded above via _task_direct_monitored)
        pass
    ```
  - **IMPORTANT**: The retry loop calls `route_task()` **directly** on the decomposer (not via a validator method). This ensures the same execution path (tools, thinking, etc.) is used for improvement.
  - **IMPORTANT**: The validator reuses the **main decomposer session** (same session as original execution) — not a separate session.
  - **IMPORTANT**: Validation is skipped for `scope="large"` (plan-based tasks).
  - **IMPORTANT**: `_extract_last_response_content()` extracts the final `Response` content from collected events (iterate events, track last `Response` instance).
  - `_build_improvement_prompt()` builds a prompt that includes the original request + validation feedback + improvement instructions.
  - Timeout on improvement: `_IMPROVE_TIMEOUT_S = 300.0` per retry (matches task timeout)
  - `QualityScoreEvent` emitted at end of each validation cycle (user sees latest score)
  - **User visible behavior**:
    - Normal/quiet mode: User sees only final approved response
    - Debug/verbose mode: User sees QualityScoreEvent at validation outcome, plus rejected response text before retry, plus full re-execution during improvement
    - Validator error: User sees error notification but execution continues
  - If validator is `None` or `enabled=False` — skip validation entirely
- **Releasable**: Full validation loop integrated into pipeline request flow.
- **Tests (TDD)** — `tests/ai/test_pipeline_validation.py`:
  - Unit: `test_validation_passes_approved_response` — score ≥ threshold → no retry
  - Unit: `test_validation_triggers_improvement` — score < threshold → re-execute called
  - Unit: `test_validation_max_retries_exhausted` — after X retries still below → continue with original response + warning
  - Unit: `test_validation_no_validator_configured` — validator=None → skip validation, pass through
  - Unit: `test_validation_disabled` — enabled=False → skip validation
  - Unit: `test_validation_visible_in_debug_mode` — QualityScoreEvent sent in debug config
  - Unit: `test_validation_visible_in_verbose_mode` — QualityScoreEvent sent in verbose config
  - Unit: `test_validation_not_visible_in_quiet_mode` — QualityScoreEvent NOT sent in quiet config
  - Unit: `test_validation_passes_classification_context` — Classification passed to validator
  - Unit: `test_validation_passes_task_output_context` — TaskOutput passed to validator
  - Unit: `test_validation_improvement_timeout` — improvement times out → treated as approved
  - Unit: `test_validation_improvement_error` — improvement raises → treated as approved with warning
  - Unit: `test_validation_skipped_for_large_scope` — `scope="large"` → no validation
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
  - Create `ResponseValidator(model=..., enabled=..., threshold=..., max_retries=...)`
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
  - Test complete flow: classification → route_task → response → validation → improve_route → re-execute
  - Mock model responses across retries
  - Verify state machine: request → exec → validate response → [approved or improve_route] → re-execute
  - Test: single-request approval (score ≥ threshold immediately)
  - Test: improvement succeeds (score < threshold first, then ≥ threshold after retry)
  - Test: improvement fails (score < threshold after max_retries, continue with original response + notice)
  - Test: validator disabled (config=None or disabled flag)
  - Test: `scope="large"` bypasses validation
  - Note: This is an integration test, may require mocking `ResponseValidator.validate()` and `route_task()`
- **Tests**:
  - Integration: `test_full_validation_approve_first_pass` — single-request approval
  - Integration: `test_full_validation_improve_then_approve` — improvement succeeds
  - Integration: `test_full_validation_max_retries_rejected` — improvement fails
  - Integration: `test_full_validation_disabled_skipped` — validator disabled → pass through
  - Integration: `test_full_validation_large_scope_bypassed` — `scope="large"` → no validation
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
|-----|---|
| Additional LLM calls per request | 1 (+ 1 per retry) |
| Average additional latency | 2-8 seconds |
| Cost increase (single retry) | ~15-40% (proportional to response length) |
| Code lines added | ~400-600 (validator + tests + config) |
| Files modified | 5 (pipeline, event_mapper, md_formatter, loader, gateway) |
| Files added | 3 (validator.py, validator.md, test files) |
