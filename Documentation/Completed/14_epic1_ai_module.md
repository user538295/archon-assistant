**Purpose**: Completed stories for Epic 1 — Claude SDK session, event mapping, truncation, and session management
**Audience**: All developers
**Status**: Completed
**Last reviewed**: 2026-02-26
**Next review**: 2027-02-26

# Epic 1: AI Module

## Stories

### S1.1: Claude session (SDK)

**Status**: Completed ✅
**Priority**: High
**Estimated effort**: M

**User Story**: As a developer, I want to send prompts to Claude via the Claude Agent SDK and receive typed event dataclasses, so that I have a clean AI layer foundation without any PTY management or ANSI parsing.

#### Acceptance Criteria

- `ClaudeSession` wraps `ClaudeSDKClient` from `claude-agent-sdk`
- `ClaudeSession.start()` connects the SDK client (`ClaudeSDKClient.connect()`)
- `ClaudeSession.send(prompt: str)` is an async generator yielding archon event dataclasses
- `ClaudeSession.stop()` disconnects the SDK client (`ClaudeSDKClient.disconnect()`)
- `ClaudeSession.is_alive` returns `True` after `start()` and `False` after `stop()`
- Sessions are created with `permission_mode="bypassPermissions"` and `cwd` from config
- Tests: mock `ClaudeSDKClient`, verify `start()` calls `connect()`, `send()` calls `query()` and yields mapped events, `stop()` calls `disconnect()`, double `stop()` is a no-op

#### Technical Notes

- Package: `claude-agent-sdk` (import: `claude_agent_sdk`)
- `ClaudeSDKClient`: `connect()` → `query(prompt)` → `receive_response()` → `disconnect()`
- `permission_mode="bypassPermissions"` skips interactive permission prompts

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)
- [Component Catalog](../Architecture/110_component_catalog_and_layer_breakdown.md)

---

### S1.2: Event mapper

**Status**: Completed ✅
**Priority**: High
**Estimated effort**: M

**User Story**: As a developer, I want a mapper that translates Claude Agent SDK message objects into archon event dataclasses, so that the rest of the system works with a stable, SDK-independent event API.

#### Acceptance Criteria

- Event dataclasses (`ThinkingResult`, `ToolStarted`, `ToolResult`, `Response`, `ErrorEvent`) defined in `archon/ai/event_mapper.py`
- `EventMapper.map_messages(stream)` is an async generator of the above events
- Tests: given constructed SDK message objects → verify correct archon event sequence

#### Technical Notes

SDK message → archon event mapping:

- `AssistantMessage` with `ThinkingBlock` → `ThinkingResult(thinking)` (no `ThinkingStarted` — event was removed)
- `AssistantMessage` with `ToolUseBlock` → `ToolStarted(name)`
- `UserMessage` with `ToolResultBlock` in content list → `ToolResult(content)`
- `ResultMessage(is_error=False, result=…)` → `Response(content=result)`
- `ResultMessage(is_error=True)` → `ErrorEvent(message=result or fallback)`
- `SystemMessage`, `TextBlock` in `AssistantMessage`, empty `ResultMessage.result` → no event

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)
- [Component Catalog](../Architecture/110_component_catalog_and_layer_breakdown.md)

---

### S1.3: Truncation strategy

**Status**: Completed ✅
**Priority**: Medium
**Estimated effort**: S

**User Story**: As a developer, I want a pluggable truncation strategy applied to long event content before sending, so that Telegram's 4096-char limit is never exceeded and the strategy is swappable.

#### Acceptance Criteria

- `TruncationStrategy` ABC with `apply(text: str, max_len: int) -> list[str]` method
- `SplitStrategy`: splits text into chunks ≤ `max_len`, labels as `[1/N] ...`, `[2/N] ...`
- Strategy is selected at startup from `config.toml` (`output.truncation_strategy`)
- Tests: single chunk (no split needed), multi-chunk split, label format correct

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)

---

### S1.4: Session manager

**Status**: Completed ✅
**Priority**: High
**Estimated effort**: M

**User Story**: As a developer, I want per-user `ClaudeSession` instances created, reused, and cleaned up automatically, so that conversation context is maintained per Telegram user without resource leaks.

#### Acceptance Criteria

- `SessionManager.get_or_create(user_id)` returns existing or new `ClaudeSession` (calling `start()` on new sessions)
- Sessions are keyed by Telegram `user_id`
- Inactivity timeout (from config) triggers `session.stop()` and removes from registry
- `SessionManager.stop(user_id)` explicitly destroys a session
- `SessionManager.stop_all()` destroys all sessions (used at shutdown)
- Tests: session reuse, timeout eviction, explicit stop, stop_all

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)
- [Component Catalog](../Architecture/110_component_catalog_and_layer_breakdown.md)
