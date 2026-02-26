**Purpose**: Completed stories for Epic 2 — Telegram bot, whitelist, message handler, commands, and command menu
**Audience**: All developers
**Status**: Completed
**Last reviewed**: 2026-02-26
**Next review**: 2027-02-26

# Epic 2: Chat Module

## Stories

### S2.1: Telegram bot bootstrap

**Status**: Completed ✅
**Priority**: High
**Estimated effort**: S

**User Story**: As a user, I want the bot to start and respond to a `/start` command, so that I can confirm the bot is running and connected.

#### Acceptance Criteria

- aiogram 3.x `Application` starts with token from config
- `/start` replies with a greeting message
- Bot reconnects automatically on network drop (aiogram default polling handles this)
- Tests: mock bot token, verify `/start` handler fires

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)

---

### S2.2: Whitelist middleware

**Status**: Completed ✅
**Priority**: High
**Estimated effort**: S

**User Story**: As an operator, I want messages from non-whitelisted users to be silently dropped, so that only authorized users can interact with Claude.

#### Acceptance Criteria

- Middleware checks `message.from_user.id` against `config.access.allowed_user_ids`
- Non-whitelisted messages are dropped with no response
- Whitelisted messages pass through to handlers
- Tests: whitelisted user ID passes, non-whitelisted user ID is dropped

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)

---

### S2.3: Message handler + event formatter

**Status**: Completed ✅
**Priority**: High
**Estimated effort**: M

**User Story**: As a whitelisted user, I want my Telegram messages forwarded to Claude and each output event sent back as a formatted message, so that I can follow along with Claude's work in real-time.

#### Acceptance Criteria

- Incoming text message triggers `async for event in session.send(text):` and each event is sent to Telegram
- Each event type is formatted correctly:
  - `ThinkingResult` → `💭 Thinking complete:\n<content>` (truncation applied)
  - `ToolStarted` → `🔧 Tool: <name>`
  - `ToolResult` → `📤 Result:\n<content>` (truncation applied)
  - `Response` → `✅ Response:\n<content>` (truncation applied)
  - `ErrorEvent` → `❌ Error: <message>`
- Tests: mock session, verify each event type produces the correct Telegram message format

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)

---

### S2.4: Bot commands

**Status**: Completed ✅
**Priority**: High
**Estimated effort**: S

**User Story**: As a whitelisted user, I want `/status` and `/stop` commands, so that I can check session state and terminate Claude when needed.

#### Acceptance Criteria

- `/status` replies with: session active/inactive, working directory, uptime
- `/stop` calls `SessionManager.stop(user_id)` and replies with confirmation
- `/stop` when no session is active replies with "No active session"
- Tests: each command with active session, `/stop` with no session

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)

---

### S2.5: Clear command

**Status**: Completed ✅
**Priority**: Medium
**Estimated effort**: S

**User Story**: As a whitelisted user, I want a `/clear` command that starts a fresh context window, so that I can begin a new conversation with Claude without accumulated history, just like `/clear` in the Claude Code TUI.

#### Acceptance Criteria

- `/clear` calls `SessionManager.stop(user_id)` then `SessionManager.get_or_create(user_id)` to immediately start a fresh session
- Replies with `🧹 Context cleared. New session started.`
- Works whether or not a session was previously active (`stop` is a no-op when no session exists)
- New session is started eagerly so the next message has no cold-start delay
- Tests: `stop()` called with correct `user_id`, `get_or_create()` called, confirmation reply sent, works with no prior session; `clear_command` registered in dispatcher

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)

---

### S2.6: Telegram command menu

**Status**: Completed ✅
**Priority**: Medium
**Estimated effort**: S

**User Story**: As a whitelisted user, I want to see all available bot commands when I type `/` or tap the 📋 menu button in Telegram, so that I can discover and invoke commands without memorizing them.

#### Acceptance Criteria

- `BOT_COMMANDS: list[BotCommand]` defined in `archon/chat/bot.py` as single source of truth for all 18 command names and descriptions
- `setup_bot_commands(bot: Bot)` async function calls `bot.set_my_commands(commands=BOT_COMMANDS, scope=BotCommandScopeAllPrivateChats())`
- A startup hook `dp.startup.register(setup_bot_commands)` is registered in `Gateway._run()` so the menu is updated every time the daemon starts
- All 18 commands (`start`, `status`, `context`, `stop`, `clear`, `restart`, `notify`, `quiet`, `normal`, `verbose`, `debug`, `settings`, `skills`, `skill`, `model`, `agents`, `jobs`, `running_agents`) appear in the Telegram command menu with human-readable descriptions

#### Technical Notes

Telegram's native command menu is populated via the `setMyCommands` Bot API method. Commands are shown as an auto-suggestion overlay when the user types `/`, and via a persistent 📋 menu button next to the message input. Using `BotCommandScopeAllPrivateChats` restricts the menu to private chats.

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)
