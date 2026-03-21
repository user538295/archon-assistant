# Backlog: Show Per-Agent Tasks in PlanEvent Notification

**Last reviewed:** 2026-03-21
**Next review:** 2026-06-21
**Status:** Ready for implementation

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

- [ ] Multi-agent plan: each agent's task appears as a `•` bullet between summary and spawning line
- [ ] Single-agent plan: no bullets shown
- [ ] Zero-agent plan: no bullets, valid `"🔄 Spawning 0 agents..."` line *(defensive boundary check — zero-agent PlanEvent cannot be produced by the pipeline, as `parse_agent_plan` rejects empty agent lists)*
- [ ] HTML special characters in task text are escaped
- [ ] All existing `test_format_plan_event_*` tests updated to assert bullet presence where the fixture uses ≥2 agents, and all pass
- [ ] Four new tests added and passing (`zero_agents`, `shows_task_bullets_for_multi_agent`, `no_bullets_single_agent`, `html_escapes_task_text`)
- [ ] ADR table row updated
- [ ] `n > 1` guard assumption validated against real decomposer output before closing
