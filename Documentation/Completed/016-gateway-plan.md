**Purpose**: Completed stories for Epic 3 — gateway orchestration and graceful shutdown
**Audience**: All developers
**Status**: Completed
**Last reviewed**: 2026-02-26
**Next review**: 2027-02-26

# Epic 3: Gateway

## Stories

### S3.1: Gateway core

**Status**: Completed ✅
**Priority**: High
**Estimated effort**: M

**User Story**: As a developer, I want a gateway that wires the Telegram bot and session manager together in a single asyncio event loop, so that the app runs as a cohesive whole from `main.py`.

#### Acceptance Criteria

- `Gateway.start()` initializes config, starts the Telegram bot, and starts the session manager
- Telegram message events are routed to the correct user session
- Session output events are routed back to the correct Telegram chat
- `main.py` calls `Gateway.start()` and blocks until shutdown
- Tests: integration test — send mock Telegram message, verify mock session receives it and response is sent back

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)
- [Component Catalog](../Architecture/110_component_catalog_and_layer_breakdown.md)

---

### S3.2: Graceful shutdown

**Status**: Completed ✅
**Priority**: High
**Estimated effort**: S

**User Story**: As an operator, I want the daemon to shut down cleanly on SIGTERM or SIGINT, so that no Claude SDK sessions are left open.

#### Acceptance Criteria

- SIGTERM/SIGINT triggers `SessionManager.stop_all()` then Telegram bot disconnect
- Shutdown completes within 5 seconds
- Log message emitted on shutdown start and completion
- Tests: send SIGINT to process, verify all sessions stopped

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)
- [ADR-02](../ADRs/02_session_management_and_lifecycle.md)
