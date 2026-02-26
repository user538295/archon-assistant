**Purpose**: Completed stories for Epic 14 — session state tracking, diagnostics, and enhanced /status command
**Audience**: All developers
**Status**: Completed
**Last reviewed**: 2026-02-26
**Next review**: 2027-02-26

# Epic 14: Session Observability & Diagnostics

## Stories

### S14.1: Session state tracking & diagnostics

**Status**: Completed ✅
**Priority**: Medium
**Estimated effort**: L

**User Story**: As an operator, I want to inspect whether a `ClaudeSession` is actively processing or stuck, how long it has been running, and what events it has recently emitted, so that I can detect hangs, surface processing state in `/status`, and use this information programmatically (e.g. from the cron scheduler or future health-check tooling).

#### Acceptance Criteria

- `ClaudeSession` tracks `_processing`, `_last_send_at`, `_last_response_at`, `_send_count`, `_event_log` (deque maxlen=200)
- `is_processing` is `True` while `send()` generator is being iterated; `False` before, after, and after early `break` or exception
- `processing_seconds` is `None` when not processing; positive float while processing
- `idle_seconds` is `None` before first response; non-negative float after
- `is_stuck(threshold)` returns `False` when not processing; `True` when `processing_seconds > threshold`
- `_event_log` auto-drops oldest beyond 200; `recent_events(n)` returns the last `n` entries
- `diagnostics` dict contains all defined keys with correct types
- `SessionManager.session_diagnostics(unknown)` returns `None`; known user returns dict
- `SessionManager.processing_sessions()` returns correct `{user_id: seconds}` map
- `SessionManager.stuck_sessions(threshold)` returns correct list
- `/status` shows `🔄 Processing for X.Xs` or `💤 Idle for Xs` plus message count when session active
- All 44 test cases pass; full suite coverage remains ≥ 85%
- Live tests: `is_processing` transitions correctly around a real SDK query; `event_log` populated; `diagnostics` fully populated

#### Technical Notes

Currently `ClaudeSession.is_alive` only indicates whether the SDK subprocess is connected — not whether a request is in flight. This story adds lightweight timing state and a bounded in-memory event log.

**New `ClaudeSession.__init__` attributes:**
- `_processing: bool = False` — True while the `send()` generator is being iterated
- `_last_send_at: float | None = None` — `time.monotonic()` set when `send()` body first executes
- `_last_response_at: float | None = None` — `time.monotonic()` set when a `Response` or `ErrorEvent` is yielded
- `_send_count: int = 0` — incremented once per `send()` call
- `_event_log: deque[tuple[float, Event]] = deque(maxlen=200)` — bounded ring-buffer

**New properties/methods:**
- `is_processing: bool` — property returning `_processing`
- `processing_seconds: float | None` — seconds since `send()` was called; `None` when not processing
- `idle_seconds: float | None` — seconds since last `Response`/`ErrorEvent`; `None` if never responded
- `send_count: int` — total prompts sent in this session
- `is_stuck(threshold_seconds: float = 120.0) -> bool` — `True` if processing and duration exceeds threshold
- `recent_events(n: int = 20) -> list[tuple[float, Event]]` — last `n` `(timestamp, event)` pairs
- `diagnostics: dict` — complete state snapshot

**New `SessionManager` methods:**
- `session_diagnostics(user_id: int) -> dict | None`
- `processing_sessions() -> dict[int, float]`
- `stuck_sessions(threshold_seconds: float = 120.0) -> list[int]`

**Test files:**
- `tests/ai/test_claude_session.py` — new class `TestClaudeSessionDiagnostics` (22 cases)
- `tests/ai/test_session_manager.py` — new class `TestSessionManagerDiagnostics` (10 cases)
- `tests/ai/test_session_diagnostics_e2e.py` — new file, mocked slow SDK (5 cases)
- `tests/ai/test_session_diagnostics_live.py` — new file, `@pytest.mark.live`, real SDK (7 cases)
- `tests/chat/test_commands.py` — extended for enhanced `/status` output (3 new cases)

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)
- [Operational Readiness](../Architecture/160_operational_readiness_monitoring_and_reliability.md)
- [Component Catalog](../Architecture/110_component_catalog_and_layer_breakdown.md)
