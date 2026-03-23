# Startup Notification

**Status**: Planned
**Created**: 2026-03-16
**Reviewed**: 2026-03-16 (devil's advocate pass)

## Problem

When Archon starts — especially from external restarts (launchd auto-restart, scripts, crash recovery) — users receive no notification. The only way to discover Archon is running is to send a message or check logs manually. This is an observability gap for a daemon.

## User Story

**As a** user,
**I want** a Telegram notification whenever Archon starts,
**so that** I know when something restarted it (or when my changes took effect).

---

## Design Decisions (Post-Review)

### Gating: `normal` mode threshold

Send the startup notification for `normal`, `verbose`, and `debug` modes. Suppress for `quiet` only. No dedicated config flag — reuse the existing notification mode hierarchy.

### Message content: tiered by mode

**Base message** (`normal`+):
```
Archon started
Version: 26.3.364
2026-03-16 09:24
```

**Rich message** (`verbose`/`debug` only) — appends:
```
Skills: 5 · Plugins: 2 · Agents: 3 · Jobs: 4
```

### Keep `_register_restart_notification` — merge, don't replace

`_register_restart_notification` is a targeted ack to the user who issued `/restart`. It confirms a requested action completed. The startup notification is a broadcast lifecycle event. These serve different purposes.

**Merge strategy**: When the boot was triggered by `/restart` (detected via `ARCHON_RESTART_NOTIFY_CHAT_ID` env var), merge the startup info into the restart ack message for that user. Other whitelisted users receive the standalone startup notification. This avoids duplicate messages to the `/restart` requester.

### Crash-loop protection

Write a timestamp to `~/.archon/.last_startup`. If the previous startup was less than 30 seconds ago, skip the broadcast and log a warning. This prevents Telegram flooding when launchd keeps restarting a crashing Archon.

### Pre-compute `get_version()`

`get_version()` shells out to `git` synchronously. Call it in `Gateway._run()` scope before registering the startup hook, and capture the result in the closure. Do not call it inside the async hook.

### Broadcast to all whitelisted users

Send to every `cfg.access.allowed_user_ids` entry. Follow the `JobScheduler._broadcast()` error isolation pattern: per-user try/except, log warnings on failure, never block startup.

### HTML-escape dynamic content

Working directory and any user-derived strings must be `html.escape()`-d since the bot uses `parse_mode="HTML"`.

---

## Implementation Plan

### 1. Crash-loop guard utility

Create a function (in `archon/gateway/`) that:
- Reads `~/.archon/.last_startup` timestamp
- Returns `True` if the last startup was < 30s ago
- Writes the current timestamp on every call
- Handles missing file (first-ever start) gracefully

### 2. Startup notification function

Create an async function that:
- Accepts `bot`, `allowed_user_ids`, notification mode, version, and loader counts
- Checks crash-loop guard — skip broadcast if too recent
- Builds the message (base or rich depending on mode)
- Loops over all user IDs, sends with per-user error isolation
- All exceptions caught and logged as warnings

### 3. Register as `dp.startup` hook

In `Gateway._run()`, after `setup_bot_commands` and `_register_restart_notification` registration:
- Pre-compute version string
- Collect loader counts (skills, plugins, agents, jobs)
- Register the startup notification as a `dp.startup` hook

### 4. Merge with `/restart` ack

Modify `_notify_restart()` to accept optional startup info (version, counts) and include it in the restart ack message. In the startup hook, skip the broadcast for the chat ID that already received the merged restart ack.

### 5. Tests (TDD)

Write tests before implementation:
- Broadcasts to all whitelisted users
- Suppressed in `quiet` mode
- Per-user error isolation (one failure doesn't block others)
- Does not block startup on Telegram failure
- Message content correctness (base vs. rich)
- Crash-loop guard: skips when < 30s since last startup
- Crash-loop guard: sends when >= 30s or first start
- Deduplication with `/restart` ack
- HTML-escaping of dynamic content

### 6. Documentation updates

Update architecture docs that reference `_register_restart_notification`:
- `110_component_catalog_and_layer_breakdown.md`
- `120_services_and_integration_architecture.md`
- `140_error_handling_strategy.md`

---

## Acceptance Criteria

- [ ] Startup notification sent to all whitelisted users on every Archon start (non-`quiet` mode)
- [ ] Base message contains version and timestamp
- [ ] `verbose`/`debug` messages include loader counts
- [ ] No notification in `quiet` mode
- [ ] `/restart` requester receives merged ack+startup message (no duplicate)
- [ ] Other users receive standalone startup notification on `/restart`-triggered boot
- [ ] Crash-loop protection: no broadcast if last startup was < 30s ago
- [ ] Telegram failures do not block Archon startup
- [ ] Per-user send errors are isolated (one failure doesn't prevent others)
- [ ] All tests pass with >= 85% coverage on new code

## Related Documents

- [System Architecture Overview](../Architecture/100_system_architecture_overview.md)
- [Component Catalog](../Architecture/110_component_catalog_and_layer_breakdown.md)
- [Services & Integration](../Architecture/120_services_and_integration_architecture.md)
- [Error Handling Strategy](../Architecture/140_error_handling_strategy.md)
