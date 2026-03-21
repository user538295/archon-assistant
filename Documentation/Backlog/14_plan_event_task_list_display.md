# Backlog: Show Per-Agent Tasks in PlanEvent Notification

**Last reviewed:** 2026-03-21
**Next review:** 2026-06-21
**Status:** Implemented (live E2E tasks 3.2–3.4 pending manual validation)

---

## Problem

When Archon spawns multiple background agents, the Telegram notification shows only a generic count:

```
📋 Plan: Answer /jobs rename question and investigate bug where background agent results get re-sent
🔄 Spawning 2 agents...
```

The user has no idea which agent will do what. The task breakdown is invisible until the agents individually announce themselves via `SubagentStarted`.

---

## Goal

Replace the agent count with a bullet list of task descriptions so the user immediately sees the full plan:

```
📋 Plan: Answer /jobs rename question and investigate re-send bug
• Answer /jobs rename question
• Investigate bug where background agent results get re-sent on next user message
🔄 Spawning 2 agents...
```

Single-agent plans are unaffected (no bullet list needed — the summary is sufficient).

---

## Scope

**Files changed:**
- `archon/chat/handler.py` — `format_event()` updated to render task bullets
- `Documentation/ADRs/02_logical_boundary_output_streaming.md` — table row updated to reflect new format

**One test file updated:** `tests/chat/test_handler.py`

No data model changes. No changes to `EventRenderer`, `event_mapper.py`, `agent_plan.py`, `decomposer.py`, or any other module.

`voice.py` imports `format_event` from `handler.py` and inherits the change automatically — no direct changes needed to `voice.py`.

---

## Implementation

### `archon/chat/handler.py` — `format_event()`, lines 266–270

**Current:**
```python
if isinstance(event, PlanEvent):
    n = len(event.plan.agents)
    return [
        f"📋 Plan: {html.escape(event.summary)}\n🔄 Spawning {n} agent{'s' if n != 1 else ''}..."
    ]
```

**New:**
```python
if isinstance(event, PlanEvent):
    n = len(event.plan.agents)
    agent_word = "agent" if n == 1 else "agents"
    if n > 1:
        bullets = "\n".join(f"• {html.escape(a.task)}" for a in event.plan.agents)
        body = f"📋 Plan: {html.escape(event.summary)}\n{bullets}\n🔄 Spawning {n} {agent_word}..."
    else:
        body = f"📋 Plan: {html.escape(event.summary)}\n🔄 Spawning {n} {agent_word}..."
    return [body]
```

**Rationale for `n > 1` guard:** A single-agent plan has the task described in the summary already — listing it again as a bullet is redundant.

> **Note (UX judgment call):** The assumption that "single-agent plan summary is self-descriptive" is a deliberate UX decision, not a structural constraint. It should be validated against real decomposer output before closing this item — if the decomposer summary diverges significantly from the single agent's task text, the guard condition may need revisiting.

---

## Tests

### Tests to update in `tests/chat/test_handler.py`

**`test_format_plan_event_debug`** (line 870): currently asserts `"3 agents" in result[0]`. Update to also assert each task bullet is present:
```python
assert "• Task 1" in result[0]
assert "• Task 2" in result[0]
assert "• Task 3" in result[0]
assert "🔄 Spawning 3 agents..." in result[0]
```

**`test_format_plan_event_quiet/normal/verbose`** (lines 879–898): these use a 2-agent default fixture, so after the change they exercise the bullet code path. Update each of these three tests to also assert bullet presence, e.g.:
```python
assert "• Task 1" in result[0]
assert "• Task 2" in result[0]
```
Without these assertions the bullet rendering is exercised but silently unchecked.

### New tests to add

```python
def test_format_plan_event_zero_agents() -> None:
    """Zero-agent PlanEvent produces no bullets and a valid spawning line.

    Zero-agent PlanEvent cannot be produced by the pipeline
    (parse_agent_plan rejects empty agent lists) — this is a defensive
    boundary test.
    """
    notif = NotificationsConfig(mode="normal")
    result = format_event(_make_plan_event(0), _split, notifications=notif)
    assert "•" not in result[0]
    assert "🔄 Spawning 0 agents..." in result[0]


def test_format_plan_event_shows_task_bullets_for_multi_agent() -> None:
    """Multi-agent PlanEvent lists each agent's task as a bullet."""
    notif = NotificationsConfig(mode="normal")
    result = format_event(_make_plan_event(3), _split, notifications=notif)
    assert "• Task 1" in result[0]
    assert "• Task 2" in result[0]
    assert "• Task 3" in result[0]
    assert "🔄 Spawning 3 agents..." in result[0]


def test_format_plan_event_no_bullets_for_single_agent() -> None:
    """Single-agent PlanEvent does not show a bullet list."""
    notif = NotificationsConfig(mode="normal")
    result = format_event(_make_plan_event(1), _split, notifications=notif)
    assert "•" not in result[0]
    assert "🔄 Spawning 1 agent..." in result[0]


def test_format_plan_event_html_escapes_task_text() -> None:
    """Task text containing HTML special chars is escaped."""
    agents = [
        AgentTask(id="a1", task="Fix <b>bold</b> & check"),
        AgentTask(id="a2", task="Deploy to prod"),
    ]
    plan = AgentPlan(scope="large", summary="Two tasks", agents=agents)
    event = PlanEvent(plan=plan, summary=plan.summary)
    notif = NotificationsConfig(mode="normal")
    result = format_event(event, _split, notifications=notif)
    assert "&lt;b&gt;bold&lt;/b&gt;" in result[0]
    assert "&amp;" in result[0]
```

---

## What does NOT change

- `event_renderer.py` — history log already renders tasks as `a1 (Research), a2 (Implement)` (adequate for log files; not user-facing)
- All other modules — untouched

---

## Known limitations / accepted trade-offs

`PlanEvent` currently bypasses `TruncationStrategy` — the entire message is sent as a single string. For a plan with many agents each with long task descriptions, this could approach Telegram's 4096-character message limit. This is accepted for the current scope: typical plans produced by the decomposer have 2–5 agents with short task summaries, making overflow practically impossible. If plans grow significantly larger in future, `format_event` for `PlanEvent` should be routed through the truncation pipeline.

---

## ADR update

`Documentation/ADRs/02_logical_boundary_output_streaming.md`, table row:

| Before | After |
|--------|-------|
| `📋 Plan: <summary>\n🔄 Spawning N agents...` | `📋 Plan: <summary>\n• task 1\n• task 2\n🔄 Spawning N agents...` (multi-agent only) |

---

## Acceptance criteria

- [x] Multi-agent plan: each agent's task appears as a `•` bullet between summary and spawning line
- [x] Single-agent plan: no bullets shown
- [x] Zero-agent plan: no bullets, valid `"🔄 Spawning 0 agents..."` line *(defensive boundary check — zero-agent PlanEvent cannot be produced by the pipeline, as `parse_agent_plan` rejects empty agent lists)*
- [x] HTML special characters in task text are escaped
- [x] All existing `test_format_plan_event_*` tests updated to assert bullet presence where the fixture uses ≥2 agents, and all pass
- [x] Four new tests added and passing (`zero_agents`, `shows_task_bullets_for_multi_agent`, `no_bullets_single_agent`, `html_escapes_task_text`)
- [x] ADR table row updated
- [ ] `n > 1` guard assumption validated against real decomposer output before closing *(requires live E2E — skipped per user instruction)*

---

## Task breakdown

> **Estimated effort**: 3 phases, 7 tasks

### Phase 1 — TDD: Write failing tests

#### Task 1.1 — Add new unit tests for bullet rendering

- [x] **File**: `tests/chat/test_handler.py`
- **Depends on**: nothing
- **Description**: Add 4 new test functions covering all new bullet-rendering cases. Run and confirm they all fail before touching production code.
  - `test_format_plan_event_zero_agents` — zero-agent PlanEvent: no `•` in output, valid `"🔄 Spawning 0 agents..."` line. (Defensive boundary — `parse_agent_plan` rejects empty lists; zero-agent event cannot arise from the pipeline.)
  - `test_format_plan_event_shows_task_bullets_for_multi_agent` — 3-agent PlanEvent: `• Task 1`, `• Task 2`, `• Task 3`, and `"🔄 Spawning 3 agents..."` all present in `result[0]`.
  - `test_format_plan_event_no_bullets_for_single_agent` — 1-agent PlanEvent: no `•` in output, `"🔄 Spawning 1 agent..."` present.
  - `test_format_plan_event_html_escapes_task_text` — 2-agent PlanEvent where first task is `"Fix <b>bold</b> & check"`: `&lt;b&gt;bold&lt;/b&gt;` and `&amp;` present in `result[0]`.
- **Tests (TDD)**:
  - Unit: `test_format_plan_event_zero_agents` — assert `"•" not in result[0]` and `"🔄 Spawning 0 agents..." in result[0]`
  - Unit: `test_format_plan_event_shows_task_bullets_for_multi_agent` — assert `"• Task 1"`, `"• Task 2"`, `"• Task 3"`, `"🔄 Spawning 3 agents..."` in `result[0]`
  - Unit: `test_format_plan_event_no_bullets_for_single_agent` — assert `"•" not in result[0]` and `"🔄 Spawning 1 agent..." in result[0]`
  - Unit: `test_format_plan_event_html_escapes_task_text` — assert `"&lt;b&gt;bold&lt;/b&gt;"` and `"&amp;"` in `result[0]`
- **Checkpoint**: `uv run pytest tests/chat/test_handler.py -k "zero_agents or task_bullets or single_agent or html_escapes" -v` — expect 4 failures

#### Task 1.2 — Update existing PlanEvent tests to assert bullet presence

- [x] **File**: `tests/chat/test_handler.py`
- **Depends on**: Task 1.1
- **Description**: The 4 existing `test_format_plan_event_*` tests use ≥2-agent fixtures and will exercise the new bullet code path after Task 2.1 — but currently contain no bullet assertions. Update each to assert bullet presence so the behavior is checked, not silently uncovered.
  - `test_format_plan_event_debug` (3-agent fixture, line ~870): add `assert "• Task 1" in result[0]`, `"• Task 2"`, `"• Task 3"`.
  - `test_format_plan_event_quiet` (2-agent fixture, line ~879): add `assert "• Task 1" in result[0]`, `"• Task 2"`.
  - `test_format_plan_event_normal` (2-agent fixture, line ~887): same as quiet.
  - `test_format_plan_event_verbose` (2-agent fixture, line ~894): same as quiet.
- **Tests (TDD)**:
  - Unit: `test_format_plan_event_debug` — existing test extended with bullet assertions
  - Unit: `test_format_plan_event_quiet` — existing test extended with bullet assertions
  - Unit: `test_format_plan_event_normal` — existing test extended with bullet assertions
  - Unit: `test_format_plan_event_verbose` — existing test extended with bullet assertions
- **Checkpoint**: `uv run pytest tests/chat/test_handler.py -k "plan_event" -v` — expect all 8 PlanEvent tests to fail

---

### Phase 2 — Implementation

#### Task 2.1 — Implement `format_event()` PlanEvent bullet rendering

- [x] **File**: `archon/chat/handler.py` (lines 266–270)
- **Depends on**: Task 1.2
- **Description**: Replace the current single-string PlanEvent branch with an `n > 1` guarded implementation:
  ```python
  if isinstance(event, PlanEvent):
      n = len(event.plan.agents)
      agent_word = "agent" if n == 1 else "agents"
      if n > 1:
          bullets = "\n".join(f"• {html.escape(a.task)}" for a in event.plan.agents)
          body = f"📋 Plan: {html.escape(event.summary)}\n{bullets}\n🔄 Spawning {n} {agent_word}..."
      else:
          body = f"📋 Plan: {html.escape(event.summary)}\n🔄 Spawning {n} {agent_word}..."
      return [body]
  ```
  `voice.py` imports `format_event` from `handler.py` — no change needed, inherits automatically.
- **Tests (TDD)**:
  - Unit: all 8 `test_format_plan_event_*` tests — must all go green
  - Integration: `uv run pytest tests/chat/test_voice.py` — verify `voice.py` import not broken
- **Checkpoint**: `uv run pytest tests/chat/ -v`

#### Task 2.2 — Full regression check

- [x] **File**: none
- **Depends on**: Task 2.1
- **Description**: Run the full test suite to catch any unintended regressions across all modules.
- **Tests (TDD)**:
  - Unit + Integration: `uv run pytest`
- **Checkpoint**: `uv run pytest` — all tests pass, ≥85% coverage maintained

---

### Phase 3 — Documentation & Live validation

#### Task 3.1 — Update ADR table row

- [x] **File**: `Documentation/ADRs/02_logical_boundary_output_streaming.md`
- **Depends on**: Task 2.1
- **Description**: Update the `PlanEvent` row in the output event table to reflect the new multi-agent format:

  | Before | After |
  |--------|-------|
  | `📋 Plan: <summary>\n🔄 Spawning N agents...` | `📋 Plan: <summary>\n• task 1\n• task 2\n🔄 Spawning N agents...` (multi-agent only) |

- **Tests**: none (doc-only)

#### Task 3.2 — Live E2E: multi-agent plan shows bullets

- [ ] **File**: none (manual validation)
- **Depends on**: Task 2.1
- **Description**: Send a real multi-step task via Telegram that causes the decomposer to produce a 2+ agent plan. Confirm the `📋 Plan:` Telegram notification contains `•` bullet lines between the summary and the `🔄 Spawning N agents...` line.
- **Tests**:
  - Live E2E: observe Telegram notification output directly

#### Task 3.3 — Live E2E: single-agent plan shows no bullets

- [ ] **File**: none (manual validation)
- **Depends on**: Task 2.1
- **Description**: Send a real single-step task that results in a 1-agent plan. Confirm the notification contains only the summary and `🔄 Spawning 1 agent...` — no bullet lines.
- **Tests**:
  - Live E2E: observe Telegram notification output directly

#### Task 3.4 — Validate `n > 1` guard assumption

- [ ] **File**: none (log inspection)
- **Depends on**: Task 3.2, Task 3.3
- **Description**: Inspect `~/.archon/history/sessions/` for a 1-agent plan entry. Compare the decomposer `summary` against the single agent's `task` text. If they diverge significantly (summary is not self-descriptive), revisit the `n > 1` guard condition and reopen this item.
- **Tests**:
  - Live E2E: log file inspection — `~/.archon/history/sessions/YYYY-MM-DD.md`
