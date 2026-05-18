**Purpose**: Completed stories for Epic 4 — logging, daemon deployment, and daily log rotation
**Audience**: All developers
**Status**: Completed
**Last reviewed**: 2026-02-26
**Next review**: 2027-02-26

# Epic 4: Daemon

## Stories

### S4.1: Logging

**Status**: Completed ✅
**Priority**: High
**Estimated effort**: S

**User Story**: As an operator, I want structured rotating log files, so that I can debug issues without the log growing unbounded.

#### Acceptance Criteria

- Rotating file handler: max 10 MB per file, keep 5 backups *(original spec — superseded by S4.4, which replaces `RotatingFileHandler` with `TimedRotatingFileHandler` for daily rotation; see S4.4 for current implementation)*
- Log file path configurable in `config.toml` (default: `~/.archon/logs/archon.log`)
- Log level configurable (`INFO` default, `DEBUG` via config)
- All modules use the same logger (`logging.getLogger("archon")`)
- Tests: verify log file created, verify level filtering

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)
- [Operational Readiness](../Architecture/160_operational_readiness_monitoring_and_reliability.md)

---

### S4.2: launchd service (macOS)

**Status**: Completed ✅
**Priority**: Medium
**Estimated effort**: S

**User Story**: As an operator, I want a `make install` command that installs Archon as a launchd service, so that the daemon starts automatically on login without manual intervention.

#### Acceptance Criteria

- `scripts/com.archon.assistant.plist` template with correct paths
- `make install` copies plist to `~/Library/LaunchAgents/` and runs `launchctl load`
- `make uninstall` unloads and removes the plist
- `make logs` tails the log file
- Plist uses `KeepAlive = true` for auto-restart on crash

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)

---

### S4.3: systemd service (Linux)

**Status**: Completed ✅
**Priority**: Low
**Estimated effort**: S

**User Story**: As an operator on Linux, I want a systemd unit file, so that the daemon auto-starts on boot.

#### Acceptance Criteria

- `scripts/archon.service` unit file with `Restart=on-failure`
- `make install-linux` copies unit and runs `systemctl enable --user archon`
- `make uninstall-linux` disables and removes

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)

---

### S4.4: Daily log rotation

**Status**: Completed ✅
**Priority**: Medium
**Estimated effort**: S

**User Story**: As an operator, I want the log file to rotate every day, so that each day's log is in its own file and old logs are easy to find by date.

#### Acceptance Criteria

- `_daily_log_namer("…/archon.log.2026-02-22")` → `"…/archon.2026-02-22.log"`
- `_rotate_on_startup` is a no-op when the file does not exist or its mtime is today
- `_rotate_on_startup` renames the file to `archon.<mtime_date>.log` when mtime < today
- `setup_logging` calls `_rotate_on_startup` before opening the handler
- Handler is `TimedRotatingFileHandler` with `when="MIDNIGHT"` and `backupCount=0`
- Handler's `namer` attribute is `_daily_log_namer`
- Tests: namer unit tests (correct rename, parent dir preserved, date in stem), `_rotate_on_startup` (no file, today, yesterday, 5 days old), handler wiring, full `setup_logging` integration

#### Technical Notes

- At midnight the current `archon.log` is renamed `archon.YYYY-MM-DD.log` (yesterday's date) and a fresh `archon.log` starts
- On daemon startup, if an existing `archon.log` has an mtime from a previous day it is renamed immediately (handles crash/stop-before-midnight)
- All daily log files are kept (no automatic deletion)
- Replaces `RotatingFileHandler` with `TimedRotatingFileHandler(when="midnight", backupCount=0)`
- Custom `namer` callable transforms the stdlib default `archon.log.YYYY-MM-DD` → `archon.YYYY-MM-DD.log`
- `_rotate_on_startup(log_path)` handles the startup edge case
- Both helpers are exposed as module-level functions for unit testing

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)
- [Operational Readiness](../Architecture/160_operational_readiness_monitoring_and_reliability.md)
