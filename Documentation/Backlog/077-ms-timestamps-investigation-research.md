# Feature Investigation: Millisecond-Precision Timestamps in Session History

**Date**: 2026-04-17  
**Request**: Change session history timestamps from `17:48:42 UTC` to `17:48:42.123 UTC`

---

## Current Implementation

All timestamps are generated from `datetime.now(timezone.utc)` — which already carries **microsecond precision internally**. The precision is lost only at the formatting step.

**Current format string used**: `%H:%M:%S %Z` → produces `17:48:42 UTC`

### All locations that need to change (7 format strings across 6 files):

| File | Line(s) | Used for |
|---|---|---|
| `archon/ai/history_manager.py` | ~48, 55 | User message headers + Archon response headers |
| `archon/ai/event_renderer.py` | ~108 | Renders all ~20 event types (tools, responses, errors, etc.) — **highest leverage** |
| `archon/ai/agent_logger.py` | ~123, 143 | Agent log headers and footers |
| `archon/ai/job_scheduler.py` | ~136, 154 | Scheduled job headers and footers |
| `archon/chat/commands.py` | ~827 | Scheduler status display |
| `archon/gateway/gateway.py` | ~491 | Restart notification timestamps |

### Test files with hardcoded timestamp assertions (must update):
- `tests/ai/test_history_manager.py` — e.g., `"## 14:30:45 UTC · User 42"`
- `tests/schedule/test_job_scheduler.py` — e.g., `"10:05:30 UTC"`

### Documentation with format reference:
- `archon/ai/history_compactor.py` ~line 39 — template string `[HH:MM UTC]` (informational, not a format call)

---

## Timestamp Origin

All timestamps are locally generated via `datetime.now(timezone.utc)`. The SDK does **not** provide timestamps in events. This means:
- Microsecond precision is available at no extra cost
- No SDK changes needed
- Timestamps reflect when Archon processes events, not when the SDK generates them (±some ms)

---

## Options

### Option A: Use `%H:%M:%S.%f %Z` strftime format
Produces: `17:48:42.123456 UTC` (microseconds — 6 digits)

**Pros**: One-liner in each location; zero new code  
**Cons**: Shows microseconds (6 digits), user requested milliseconds (3 digits); looks verbose

### Option B: Custom formatter function (Recommended)
Create a utility function that formats to exactly milliseconds:

```python
def fmt_utc_ms(dt: datetime | None = None) -> str:
    """Format UTC datetime with millisecond precision: HH:MM:SS.mmm UTC"""
    if dt is None:
        dt = datetime.now(timezone.utc)
    return f"{dt.strftime('%H:%M:%S')}.{dt.microsecond // 1000:03d} {dt.strftime('%Z')}"
```

Produces: `17:48:42.123 UTC` — exactly what was requested.

**Pros**: Exact output match; reusable; testable; format centralized  
**Cons**: Adds one small utility function; needs importing in 6 files

### Option C: Use `isoformat()` truncated
```python
dt.isoformat(timespec='milliseconds')  # → "2026-04-17T17:48:42.123+00:00"
```

**Pros**: Standard Python; no custom code  
**Cons**: Includes date and timezone offset — wrong format for the history files, which only show time

---

## Recommendation

**Option B**: Add `fmt_utc_ms()` to `archon/ai/event_renderer.py` (or a new `archon/ai/timestamp.py`), then replace all 7 format-string calls with calls to this function.

This is a **low-risk, ~1-2 hour change** (6 production files + 2 test files + 1 doc string).

### Implementation steps:
1. Add `fmt_utc_ms()` utility (one function, ~5 lines)
2. Replace `datetime.now(timezone.utc).strftime("%H:%M:%S %Z")` with `fmt_utc_ms()` in all 7 locations
3. Update test assertions: `"## 14:30:45 UTC"` → `"## 14:30:45.\d{3} UTC"` (use regex match) or pin to a fixed mock datetime
4. Update `history_compactor.py` template comment from `[HH:MM UTC]` to `[HH:MM:SS.mmm UTC]`

### Test strategy:
- Mock `datetime.now` to return a fixed datetime with known microseconds (e.g., `microsecond=123456`)
- Assert output contains `.123 UTC`
- Existing tests that assert exact timestamp strings need regex or mock-datetime update
