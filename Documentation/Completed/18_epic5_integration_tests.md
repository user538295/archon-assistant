**Purpose**: Completed stories for Epic 5 — integration and end-to-end tests across all layers
**Audience**: All developers
**Status**: Completed
**Last reviewed**: 2026-02-26
**Next review**: 2027-02-26

# Epic 5: Integration & E2E Tests

## Stories

### S5.1: AI pipeline integration test

**Status**: Completed ✅
**Priority**: Medium
**Estimated effort**: S

**User Story**: As a developer, I want an integration test that drives `EventMapper` with a scripted fake SDK message stream, so that I can verify the full AI mapping pipeline without mocking individual methods.

#### Acceptance Criteria

- A fake message sequence (constructed SDK dataclass objects) covers all five event types
- `EventMapper.map_messages(fake_stream)` is awaited and the emitted event sequence matches expected types and content
- Tests cover: `ThinkingResult`, `ToolStarted`, `ToolResult`, `ErrorEvent`, `Response` — at least one of each in a single run
- `SplitStrategy` truncation is applied to a content-bearing event to confirm the full AI-layer chain works
- No mocking of internal methods — only the SDK client boundary is substituted

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)

---

### S5.2: Chat + AI integration test

**Status**: Completed ✅
**Priority**: Medium
**Estimated effort**: S

**User Story**: As a developer, I want an integration test that wires whitelist middleware, message handler, `SessionManager`, and a mock `ClaudeSession`, so that I can verify the full Telegram→AI pathway without a live bot connection.

#### Acceptance Criteria

- Build an aiogram `Dispatcher` with `WhitelistMiddleware` and the message handler registered
- Use aiogram's test utilities to inject a fake `Message` from a whitelisted user ID
- The handler calls `session.send(text)` on a mock `ClaudeSession`
- A non-whitelisted user ID is dropped — no session is created or called
- Tests: whitelisted message reaches session, non-whitelisted message is silently dropped

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)

---

### S5.3: Full message flow e2e test

**Status**: Completed ✅
**Priority**: Medium
**Estimated effort**: M

**User Story**: As a developer, I want an end-to-end test that runs the full gateway with only external boundaries mocked, so that I can verify that a Telegram message produces the correct sequence of formatted Telegram replies.

#### Acceptance Criteria

- `Gateway.start()` is called in a test loop with mocked bot and scripted SDK client
- One simulated Telegram message is injected
- The bot stub records exactly the expected Telegram messages in order:
  1. `💭 Thinking complete:\n<content>`
  2. `🔧 Tool: <name>`
  3. `📤 Result:\n<content>`
  4. `✅ Response:\n<content>`
- Long content is split by `SplitStrategy` and multiple messages are recorded
- Log entries for the run are present in the log file

#### Technical Notes

Boundaries mocked:
- Telegram API: replaced with an in-process aiogram `Bot` stub that records `send_message` calls
- Claude SDK client: replaced with a scripted fake that emits a known event sequence

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)

---

### S5.4: Graceful shutdown e2e test

**Status**: Completed ✅
**Priority**: Medium
**Estimated effort**: S

**User Story**: As a developer, I want an end-to-end test that sends `SIGINT` to a running gateway and verifies a clean shutdown, so that I can confirm no SDK sessions are left open after the daemon stops.

#### Acceptance Criteria

- Gateway starts with at least one active mock `ClaudeSession`
- `SIGINT` is sent to the running event loop
- `SessionManager.stop_all()` is called and all sessions reach `is_alive == False`
- Telegram bot polling is disconnected
- Shutdown completes within 5 seconds
- Log messages "shutdown initiated" and "shutdown complete" are both present

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)

---

### S5.5: Live Claude Agent SDK test

**Status**: Completed ✅
**Priority**: Medium
**Estimated effort**: S

**User Story**: As a developer, I want a live test that uses the real Claude Agent SDK to process a trivial prompt, so that I can verify that `ClaudeSession` works against the actual Claude binary.

#### Acceptance Criteria

- `ClaudeSession.start()` connects using the real SDK
- A trivial prompt (e.g. `"Say: OK"`) is sent via `ClaudeSession.send()`
- At least one `Response` event with non-empty content is received within a 30-second timeout
- `ClaudeSession.stop()` disconnects; `is_alive` returns `False` afterwards
- No internal mocks of any kind

#### Technical Notes

- Prerequisites: `claude` binary present in `PATH`
- Test is marked `@pytest.mark.live` and skipped automatically if `which claude` fails

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)

---

### S5.6: Live full-stack e2e test

**Status**: Completed ✅
**Priority**: Low
**Estimated effort**: M

**User Story**: As a developer, I want a live test that runs the full gateway against the real Telegram API and real Claude Agent SDK, so that I can confirm the entire pipeline works in a production-identical environment.

#### Acceptance Criteria

- `Gateway.start()` is called; the real bot connects to Telegram polling
- The test directly calls `SessionManager.get_or_create(TELEGRAM_LIVE_CHAT_ID).send(prompt)` to inject a prompt
- The real Telegram bot sends formatted event messages to `TELEGRAM_LIVE_CHAT_ID` via the live API
- Test asserts at least one `✅ Response:` message is delivered (verified via `Bot.get_updates()` or a short polling loop)
- Shutdown is triggered after the response arrives; all sessions stop cleanly
- Total test timeout: 60 seconds

#### Technical Notes

- Prerequisites: `TELEGRAM_BOT_TOKEN` set in `.env`, `TELEGRAM_LIVE_CHAT_ID` set in `.env`, `claude` binary present in `PATH`
- Test is marked `@pytest.mark.live` and `@pytest.mark.requires_telegram`; skipped if any prerequisite is missing

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)

---

### S5.7: Live unit test — config loader

**Status**: Completed ✅
**Priority**: Low
**Estimated effort**: S

**User Story**: As a developer, I want a live unit test that exercises the config loader with real temporary files, so that I can verify file I/O paths work on the actual filesystem without any mocking.

#### Acceptance Criteria

- Test creates real temporary `.env` and `config.toml` files using `tmp_path`
- `load_config()` reads them successfully and returns correctly typed values
- Deleting the `.env` file and calling `load_config()` raises `ConfigError` with a real file-not-found path in the message
- Deleting `config.toml` raises `ConfigError` likewise
- No mocks, no patching of `open` or `os` — pure real file system calls

#### Technical Notes

- Test is marked `@pytest.mark.live`; no external services required
- Live tests (`@pytest.mark.live`) are excluded from `uv run pytest`; run with `uv run pytest -m live`

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)
