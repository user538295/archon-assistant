# PRD: Periodic Context Reminder Injection

> **Post-implementation note**: The `notify` config flag was removed and `ReminderInjectedEvent` notifications are now shown only in **verbose/debug** mode; they are suppressed in quiet and normal modes. All references to `notify: bool = False` below are outdated — visibility is now mode-gated by `format_event()`.

## Overview

LLMs experience context drift in long-running sessions: critical constraints from early in the conversation get diluted as the context window fills. This feature introduces a heartbeat reminder mechanism: a user-maintained `REMINDER.md` file that is periodically re-injected into the active session as a strong-signal separate Claude turn, resetting the model's attention to critical constraints without requiring a session restart.

## Goals

- Prevent behavioral drift in long sessions by periodically re-injecting key constraints
- Use a dual-threshold trigger (message count OR token count), whichever fires first
- Hot-reload the file on every injection so users can edit it mid-session
- Silently skip injection when `REMINDER.md` is absent — no error, no warning
- Emit a `ReminderInjectedEvent` that `format_event()` shows in verbose/debug mode only (suppressed in quiet/normal)

## Quality Gates

These commands must pass for every user story:

- `uv run pytest tests/ai/test_reminder.py tests/config/test_loader.py tests/ai/test_claude_session.py tests/chat/test_handler.py -v --override-ini='addopts='` — targeted test suite
- `uv run pytest --override-ini='addopts=' -m 'not live'` — full test suite (no live tests)
- `uv run mypy archon/` — type checking, must be clean

## User Stories

### US-001: ReminderConfig dataclass and loader

As a developer, I want `ReminderConfig` loaded from `config.toml [reminder]` with sensible defaults so the feature can be configured without requiring a `[reminder]` section.

**Acceptance Criteria:**
- [ ] `ReminderConfig` dataclass added to `archon/config/loader.py` with fields: `enabled: bool = True`, `interval_messages: int = 20`, `interval_tokens: int = 10000`
- [ ] `Config` dataclass in `archon/config/loader.py` has a new field: `reminder: ReminderConfig = field(default_factory=ReminderConfig)`
- [ ] `load_config()` parses `[reminder]` section and applies defaults when section is absent
- [ ] `tests/config/test_loader.py` extended with: `test_reminder_config_defaults`, `test_reminder_config_loads_from_toml`, `test_reminder_config_disabled`

### US-002: ContextReminder class

As a developer, I want a `ContextReminder` class that tracks message/token counts and produces the formatted reminder turn so that injection logic is isolated and testable.

**Acceptance Criteria:**
- [ ] `archon/ai/reminder.py` created with `ContextReminder` class
- [ ] Constructor: `__init__(self, config: ReminderConfig, workspace_dir: Path)` — `_file = workspace_dir / "REMINDER.md"`
- [ ] `record_message()` increments internal message counter
- [ ] `record_tokens(count: int)` accumulates token counter
- [ ] `should_inject()` returns `False` when `config.enabled` is False
- [ ] `should_inject()` returns `False` when `_file` does not exist, even if thresholds exceeded
- [ ] `should_inject()` returns `True` when `_message_count >= interval_messages` OR `_token_count >= interval_tokens` (whichever first)
- [ ] `should_inject()` returns `False` below both thresholds
- [ ] `build_reminder_message()` reads the file from disk (hot-reload), resets both counters to 0, and returns the content wrapped in the XML block:
  ```
  <system_reminder type="mandatory_context_refresh">
  WARNING: MANDATORY CONTEXT REFRESH — re-read and strictly re-apply all constraints below.
  This is a periodic injection to prevent context drift. These instructions override any
  behavioral drift that may have occurred.

  {file content}
  </system_reminder>
  ```
- [ ] `tests/ai/test_reminder.py` created with all 9 unit tests (disabled, file absent, message threshold, token threshold, whichever-first, reset-after-inject, wraps content, hot-reload, below-threshold)

### US-003: ReminderInjectedEvent dataclass

As a developer, I want a `ReminderInjectedEvent` dataclass in `event_mapper.py` so the reminder injection can be surfaced as a first-class event through the existing event pipeline.

**Acceptance Criteria:**
- [ ] `ReminderInjectedEvent` dataclass added to `archon/ai/event_mapper.py` with fields: `message_count: int` (the count that triggered injection), `source: str = "orchestrator"`
- [ ] `format_event()` in `archon/chat/handler.py` handles `ReminderInjectedEvent`:
  - Shown in verbose/debug mode only; suppressed in quiet/normal
  - Text: `🔔 Reminder injected (message {message_count})`
- [ ] `tests/chat/test_handler.py` extended with: `test_reminder_event_shown_in_verbose_mode`, `test_reminder_event_suppressed_in_quiet_mode`, `test_reminder_event_suppressed_in_normal_mode`

### US-004: Reminder injection in ClaudeSession

As a developer, I want `ClaudeSession.send()` to inject the reminder as a separate SDK turn before the user prompt so the reminder has full attention weight as its own context unit.

**Acceptance Criteria:**
- [ ] `ClaudeSession.__init__()` accepts optional `reminder: ContextReminder | None = None`, stored as `self._reminder`
- [ ] `ClaudeSession.reminder` property exposed: `@property def reminder(self) -> ContextReminder | None`
- [ ] At the start of `send()` (inside the lock, before building the full prompt), check `self._reminder and self._reminder.should_inject()`:
  - If True: capture `msg_count` before reset, call `build_reminder_message()`, then send via:
    ```python
    await self._client.query(reminder_text)
    async for _ in self._client.receive_response():
        pass  # consume silently — no events to surface
    ```
  - Then `yield ReminderInjectedEvent(message_count=msg_count)` before proceeding to the main query
- [ ] `tests/ai/test_claude_session.py` extended with: `test_reminder_injected_as_separate_turn`, `test_reminder_not_injected_when_below_threshold`, `test_reminder_not_injected_when_disabled`

### US-005: Token and message tracking in handle_message

As a developer, I want `handle_message()` to record each completed turn's message count and token usage into the session's `ContextReminder` so thresholds are tracked correctly.

**Acceptance Criteria:**
- [ ] After the `async for event in session.send(message.text)` loop completes in `archon/chat/handler.py`, access `session.reminder` (via a `reminder` property on the session object — `Pipeline.reminder` delegates to the decomposer's `ClaudeSession.reminder`)
- [ ] If `session.reminder` is not None: call `session.reminder.record_message()`
- [ ] If `session.reminder` is not None and `session.usage_stats` is not None: call `session.reminder.record_tokens(session.usage_stats["usage"].get("input_tokens", 0))`
- [ ] `Pipeline` exposes a `reminder` property that returns the underlying decomposer session's `ContextReminder`
- [ ] `tests/chat/test_handler.py` extended with: `test_record_message_called_after_each_user_message`, `test_record_tokens_called_with_result_token_count`

### US-006: Gateway wiring — ReminderConfig to SessionManager

As a developer, I want the gateway to wire `ReminderConfig` and the workspace path into `SessionManager` so each session gets a properly configured `ContextReminder`.

**Acceptance Criteria:**
- [ ] `SessionManager.__init__()` accepts optional `reminder_config: ReminderConfig | None = None`
- [ ] `SessionManager._default_factory` creates `ContextReminder(config=reminder_config, workspace_dir=Path(cwd))` and passes it to `Pipeline(reminder=...)`
- [ ] `Pipeline.__init__()` accepts optional `reminder: ContextReminder | None = None` and passes it to the decomposer's `ClaudeSession`
- [ ] `archon/gateway/gateway.py` passes `reminder_config=cfg.reminder` (when `cfg.reminder.enabled`) to `SessionManager`
- [ ] When `reminder_config.enabled` is False or reminder_config is None, no `ContextReminder` is created — `session.reminder` returns None

## Functional Requirements

- FR-1: File path for `REMINDER.md` must be `Path(config.session.working_directory) / "REMINDER.md"` — derived from `working_directory`, not hardcoded
- FR-2: Both counters (message and token) reset to 0 after each injection
- FR-3: File is re-read from disk on every injection (hot-reload)
- FR-4: Injection is a separate SDK turn sent and consumed before the user's main prompt — not text prepended to the user message
- FR-5: When `REMINDER.md` is absent, the feature is a no-op — no error logged
- FR-6: `ReminderInjectedEvent` is the first event yielded by `send()` when injection occurs (before all main-query events)

## Non-Goals

- Multiple reminder files (only `REMINDER.md` is supported)
- Separate reminder files per user (single global file)
- Injecting the reminder into background agent sessions
- Editing `REMINDER.md` via Telegram commands

## Technical Considerations

- Correct SDK injection pattern (verified from `claude_session.py:247–252`):
  ```python
  await self._client.query(reminder_text)
  async for _ in self._client.receive_response():
      pass
  ```
- Token tracking uses `session.usage_stats["usage"].get("input_tokens", 0)` — accessed after the `send()` loop completes (populated by `ResultMessage` inside `_intercept()`)
- File names: `archon/config/loader.py` (not `config.py`), `archon/chat/handler.py` (not `message_handler.py`), test files are `tests/config/test_loader.py` and `tests/chat/test_handler.py`
- `Pipeline` already duck-types as `ClaudeSession`; adding `.reminder` property follows the same pattern

## Success Metrics

- All 20 new tests pass
- Full test suite passes (`not live`)
- mypy clean
- Manual verification: create `~/.archon/workspace/REMINDER.md`, send 20 messages, confirm reminder injected and counters reset

## Open Questions

- Should `record_tokens()` use `input_tokens` (total input to model) or only newly-generated tokens? The plan specifies `input_tokens` from `ResultMessage` — this is the correct choice as it tracks growing context window pressure.