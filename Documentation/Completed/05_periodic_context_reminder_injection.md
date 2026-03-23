# Feature: Periodic Context Reminder Injection

## Context

LLMs experience **context drift** in long-running sessions: critical constraints from early in the
conversation get diluted as the context window fills. The model may gradually stop following
project-specific rules, change behavioral patterns, or forget user preferences — not due to any
explicit change, but simply because earlier instructions are now far from the attention window's
focus.

This feature introduces a **heartbeat reminder** mechanism: a user-maintained `REMINDER.md` file
that is periodically re-injected into the active session as a strong-signal system message,
resetting the model's attention to critical constraints without requiring a session restart.


## Design decisions

**File location**: `~/.archon/workspace/REMINDER.md` — alongside `agents.md` in the Archon
workspace directory. The path follows the `workspace_dir` config value, not hardcoded.

**Trigger**: dual-threshold — inject when **either** the message count OR token count threshold is
reached, whichever comes first. Both counters reset after each injection.

**Hot-reload**: the file is re-read from disk on every injection, so users can edit it mid-session
without restarting the daemon.

**Injection placement**: injected as a **separate turn** immediately before the user's message (not
prepended inline). This gives the reminder full attention weight as its own context unit.

**Signal strength**: the reminder is wrapped in a strongly-framed XML block that signals to the
model this is a mandatory system-level re-read, not conversational input:

```
<system_reminder type="mandatory_context_refresh">
WARNING: MANDATORY CONTEXT REFRESH — re-read and strictly re-apply all constraints below.
This is a periodic injection to prevent context drift. These instructions override any
behavioral drift that may have occurred.

{reminder file content}
</system_reminder>
```

**Telegram notification**: a `ReminderInjectedEvent` is emitted when a reminder is injected.
Visibility follows notification mode: shown in verbose/debug only; suppressed in quiet/normal.

**No-op when file absent**: if `~/.archon/workspace/REMINDER.md` does not exist, the feature
silently skips injection — no error, no warning.


## Configuration

New `[reminder]` section in `config.toml`:

```toml
[reminder]
enabled = true
interval_messages = 20
interval_tokens = 10000
```

- `enabled` — master switch; default `true`
- `interval_messages` — inject after this many user messages; default `20`
- `interval_tokens` — inject after this many cumulative tokens
  (input_tokens + output_tokens per turn); default `10000`


## Files to create / modify

| File | Scope |
|------|-------|
| `archon/ai/reminder.py` | New: `ContextReminder` class — counter tracking, file loading, message formatting |
| `archon/config/config.py` | Add `ReminderConfig` dataclass + `[reminder]` section loading |
| `archon/ai/claude_session.py` | Inject reminder turn before `send()` when `ContextReminder.should_inject()` is true |
| `archon/chat/message_handler.py` | Pass token count from `ResultMessage` to `ContextReminder` after each response |
| `tests/ai/test_reminder.py` | New: TDD tests for all `ContextReminder` behavior |
| `tests/config/test_config.py` | Tests for `ReminderConfig` loading + defaults |


## Steps

### Step 1: `ReminderConfig` + config loading

#### Tests (TDD — write first):

1. `test_reminder_config_defaults` — when `[reminder]` section is absent, defaults apply:
   `enabled=True`, `interval_messages=20`, `interval_tokens=40000`
2. `test_reminder_config_loads_from_toml` — explicit values in `config.toml` override defaults
3. `test_reminder_config_disabled` — `enabled=false` is respected

#### Implementation (`archon/config/config.py`):

```python
@dataclass
class ReminderConfig:
    enabled: bool = True
    interval_messages: int = 20
    interval_tokens: int = 40_000
```

Load from `config.toml` in the existing config loader. Add `reminder: ReminderConfig` field to
the top-level `Config` dataclass.


### Step 2: `ContextReminder` class

#### Tests (TDD — write first):

4. `test_should_inject_false_when_disabled` — `enabled=False` → `should_inject()` always `False`
5. `test_should_inject_false_when_file_absent` — no file → `should_inject()` `False` even if
   thresholds exceeded
6. `test_should_inject_true_on_message_threshold` — after N `record_message()` calls, returns
   `True`
7. `test_should_inject_true_on_token_threshold` — after cumulative `record_tokens()` exceeds
   threshold, returns `True`
8. `test_should_inject_whichever_first` — message threshold hit before token threshold →
   inject; token threshold hit before message threshold → inject
9. `test_reset_after_inject` — counters reset to 0 after `build_reminder_message()` is called
10. `test_build_reminder_message_wraps_content` — returned string contains the XML wrapper and
    file content
11. `test_build_reminder_message_hot_reload` — file is re-read on each call (content changed
    between calls reflects in output)
12. `test_should_inject_false_when_not_at_threshold` — below both thresholds → `False`

#### Implementation (`archon/ai/reminder.py`):

```python
class ContextReminder:
    _WRAPPER = (
        '<system_reminder type="mandatory_context_refresh">\n'
        'WARNING: MANDATORY CONTEXT REFRESH — re-read and strictly re-apply all constraints below.\n'
        'This is a periodic injection to prevent context drift. These instructions override any\n'
        'behavioral drift that may have occurred.\n\n'
        '{content}\n'
        '</system_reminder>'
    )

    def __init__(self, config: ReminderConfig, workspace_dir: Path) -> None:
        self._config = config
        self._file = workspace_dir / "REMINDER.md"
        self._message_count: int = 0
        self._token_count: int = 0

    def record_message(self) -> None:
        self._message_count += 1

    def record_tokens(self, count: int) -> None:
        self._token_count += count

    def should_inject(self) -> bool:
        if not self._config.enabled:
            return False
        if not self._file.exists():
            return False
        return (
            self._message_count >= self._config.interval_messages
            or self._token_count >= self._config.interval_tokens
        )

    def build_reminder_message(self) -> str:
        content = self._file.read_text(encoding="utf-8")
        self._message_count = 0
        self._token_count = 0
        return self._WRAPPER.format(content=content)
```


### Step 3: Injection in `ClaudeSession`

#### Tests (TDD — write first):

13. `test_reminder_injected_as_separate_turn` — when `should_inject()` is `True`, `send()` sends
    reminder content as a standalone message before the user prompt
14. `test_reminder_not_injected_when_below_threshold` — `should_inject()` `False` → only user
    prompt sent
15. `test_reminder_not_injected_when_disabled` — `enabled=False` → no extra turn

#### Implementation (`archon/ai/claude_session.py`):

Before yielding from `send(prompt)`, check `self._reminder.should_inject()`. If true, send the
reminder as a standalone message first, then proceed with the user prompt normally.

```python
async def send(self, prompt: str) -> AsyncGenerator[Event, None]:
    if self._reminder and self._reminder.should_inject():
        reminder_text = self._reminder.build_reminder_message()
        async for _ in self._client.query(reminder_text):
            pass  # consume silently — reminder turn has no events to surface
    async for event in self._client.query(prompt):
        yield self._mapper.map(event)
```

`ContextReminder` is injected into `ClaudeSession` at construction time via the gateway.


### Step 4: Token + message tracking

#### Tests (TDD — write first):

16. `test_record_message_called_after_each_user_message` — after `pipeline.send()` completes,
    `reminder.record_message()` called once
17. `test_record_tokens_called_with_result_token_count` — `ResultMessage` token count forwarded
    to `reminder.record_tokens()`

#### Implementation:

In the message handler (`archon/chat/message_handler.py`), after the `async for event in
pipeline.send(text)` loop completes:

```python
reminder.record_message()
if last_result_tokens:
    reminder.record_tokens(last_result_tokens)
```

Token count is read from `ResultMessage` during the streaming loop and stored in a local variable.


### Step 5: Telegram notification

#### Tests (TDD — write first):

18. `test_notify_sent_in_verbose_mode` — injection + `verbose` notification mode → notification
    event emitted
19. `test_notify_not_sent_in_normal_mode_when_notify_false` — `notify=False` + `normal` mode →
    no notification
20. `test_notify_sent_when_notify_true_regardless_of_mode` — `notify=True` → notification emitted
    even in `normal` mode

#### Implementation:

When `should_inject()` is `True` and about to inject, emit a `SystemEvent` (or equivalent) that
the message handler sends as a Telegram message:

```
Reminder injected (message N)
```

Visibility rules:
- Shown in verbose/debug mode only; suppressed in quiet/normal


### Step 6: Run tests and mypy

```bash
uv run pytest tests/ai/test_reminder.py tests/config/test_config.py tests/ai/test_claude_session.py -v --override-ini='addopts='
uv run mypy archon/ai/reminder.py archon/ai/claude_session.py archon/config/config.py archon/chat/message_handler.py
uv run pytest --override-ini='addopts=' -m 'not live'
```


## Architecture flow

```
User sends message
  → message_handler calls reminder.record_message()
  → pipeline.send(prompt) called
      → ClaudeSession.send() checks reminder.should_inject()
          → True: build_reminder_message() reads file from disk, resets counters
          → sends reminder as standalone turn (silently consumed)
      → user prompt sent normally
      → events streamed to Telegram
  → ResultMessage token count → reminder.record_tokens(N)
  → if injected and notify conditions met → Telegram notification sent
```


## Verification

- All new tests pass (20 tests covering: config defaults, threshold logic, hot-reload,
  injection placement, counter reset, token tracking, notification visibility rules)
- Full test suite passes: `uv run pytest --override-ini='addopts=' -m 'not live'`
- mypy clean: `uv run mypy archon/`
- Manual: create `~/.archon/workspace/REMINDER.md`, send 20 messages, verify reminder injected
  and counters reset
