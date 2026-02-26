**Purpose**: Completed stories for Epic 8 — notification mode redesign with four verbosity levels
**Audience**: All developers
**Status**: Completed
**Last reviewed**: 2026-02-26
**Next review**: 2027-02-26

# Epic 8: Notification Mode Redesign

## Stories

### S8.1: Four named notification modes

**Status**: Completed ✅
**Priority**: Medium
**Estimated effort**: M

**User Story**: As a whitelisted user, I want a single notification verbosity axis with four named modes (quiet / normal / verbose / debug), so that I can control how much output I see without memorising two overlapping command dimensions.

#### Acceptance Criteria

- `NotificationsConfig` has exactly two fields: `mode: str = "normal"` and `interval_minutes: int = 2`
- Valid modes: `"quiet"`, `"normal"`, `"verbose"`, `"debug"`
- `format_event` filters by mode per the visibility matrix:
  - **always** (all modes): `✅ Response`, `❌ Error`
  - **normal+**: `🔧 ToolStarted` (name only), `📤 ToolResult` (brief one-line)
  - **verbose+**: `💭 ThinkingResult` (truncated), `🔧 ToolStarted` (name + args)
  - **debug only**: `📤 ToolResult` (full), `💭 ThinkingResult` (full, same as verbose but unabbreviated label)
- `handle_message` uses the mode to decide event routing; quiet mode suppresses intermediate events
- `load_config` reads new `[notifications]` keys; gracefully migrates old keys (`concise_mode="full"` → `"quiet"`, `"partial"` → `"normal"`, `"off"` → `"verbose"`)
- `save_notifications_config` writes only `mode` and `interval_minutes`; old keys are dropped
- Tests: every cell of the visibility matrix, config load/save round-trip, migration from old config

#### Technical Notes

The previous design exposed two independent dimensions (`concise_mode` × `show_thinking_result` / `brief_tool_output`) with opaque names (`off/full/partial`). Users think in terms of *"how much do I want to see?"*, not in terms of orthogonal toggles. A single mode axis is easier to grasp and switches in one tap.

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)

---

### S8.2: Quiet beacon mode

**Status**: Completed ✅
**Priority**: Medium
**Estimated effort**: S

**User Story**: As a whitelisted user, I want to optionally receive periodic heartbeat messages while Claude works in quiet mode, so that I know the bot is alive during long-running tasks without being flooded with events.

#### Acceptance Criteria

- `interval_minutes = 0` → no beacon (plain quiet mode)
- `interval_minutes > 0` → periodic `⏳ Working… (N tools, M thinking)` every `interval_minutes` minutes while in quiet mode
- Beacon task is cancelled cleanly when the response arrives or an error occurs
- Interval only applies in quiet mode; other modes stream events in real-time
- Tests: beacon fires at correct interval, beacon not started when `interval_minutes = 0`, beacon cancelled on completion, other modes unaffected

#### Technical Notes

In quiet mode all intermediate events are suppressed. Without feedback, a long task feels broken. The optional *beacon* sends `⏳ Working… (N tools, M thinking)` every `interval_minutes` minutes — a heartbeat signal that proves something is happening. When `interval_minutes = 0` the beacon is disabled.

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)

---

### S8.3: Inline keyboard for /notify and /settings

**Status**: Completed ✅
**Priority**: Medium
**Estimated effort**: M

**User Story**: As a whitelisted user, I want `/notify` and `/settings` to display an inline keyboard panel, so that I can switch modes with a single tap without typing subcommands.

#### Acceptance Criteria

- `/notify` (no arg) sends a message: `⚙️ Notification mode` with a 2×2 inline keyboard
- Button labels: `🔇 Quiet`, `🔔 Normal`, `📢 Verbose`, `🔬 Debug`; current mode marked with ` ✓`
- When in quiet beacon mode the Quiet button shows `🔇 Quiet 🔦Nm ✓` (N = interval minutes)
- Tapping a button: updates `notifications.mode`, saves config, edits the keyboard message in-place, answers the callback query
- `/settings` shows the same inline keyboard (replaces the old text-only view)
- Callback queries from non-whitelisted users are dropped (whitelist middleware extended to `dp.callback_query`)
- Tests: `/notify` sends reply with `InlineKeyboardMarkup`, callback updates mode + saves + edits message, Quiet button label reflects beacon state, whitelist drops unauthorised callbacks

#### Technical Notes

Telegram inline keyboards allow the bot to edit a single message in-place when a button is tapped — no extra messages pollute the chat. The panel shows all four modes with a ✓ on the current one; tapping another mode updates it immediately.

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)

---

### S8.4: Quick-switch mode commands

**Status**: Completed ✅
**Priority**: Medium
**Estimated effort**: S

**User Story**: As a whitelisted user, I want `/quiet [N]`, `/normal`, `/verbose`, and `/debug` shortcut commands, so that I can switch modes instantly without navigating the keyboard panel.

#### Acceptance Criteria

- `/quiet` → sets `mode="quiet"`, `interval_minutes=0`; replies `🔇 Quiet mode` + inline keyboard
- `/quiet N` (N > 0 integer) → sets `mode="quiet"`, `interval_minutes=N`; replies `🔇 Quiet — beacon every N min` + inline keyboard
- `/quiet 0` treated as `/quiet` (no beacon)
- `/normal` → sets `mode="normal"`; replies `🔔 Normal mode` + inline keyboard
- `/verbose` → sets `mode="verbose"`; replies `📢 Verbose mode` + inline keyboard
- `/debug` → sets `mode="debug"`; replies `🔬 Debug mode` + inline keyboard
- All four commands registered in dispatcher and appear in `BOT_COMMANDS`
- `/notify quiet [N]` text subcommand works identically to `/quiet [N]`
- Config saved after every change
- Tests: each command sets correct mode, interval parsing, `/quiet 0` clears beacon, config saved, reply text correct

#### Technical Notes

The inline keyboard is the primary UX for touch users. Power users who know what they want prefer a single command. The quick commands set the mode and echo the inline keyboard panel so the user always sees the current state.

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)
