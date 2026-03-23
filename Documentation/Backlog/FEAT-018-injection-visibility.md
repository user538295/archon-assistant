# FEAT-018 — Injection Visibility in Session History and Telegram

**Purpose**: Make all silent context/skill injections visible in session history and (in verbose/debug mode) in Telegram, replacing ad-hoc special-casing with a unified event-based pipeline.
**Audience**: Archon users who rely on verbose/debug mode for transparency; developers debugging session context issues.
**Status**: To Do

---

## Background

Archon has several injection mechanisms that silently merge content into the Claude prompt without generating any observable event:

| Injection | Location | Mechanism |
|-----------|----------|-----------|
| History files | `session_manager.py:208` | `inject_context(text)` → `_pending_context` |
| Workspace agents (`agents.md`) | `decomposer.py:130,150,162` | `inject_context(ctx)` → `_pending_context` |
| Background agent completion hint | `background_agent_manager.py:491` | `inject_agent_context()` → `inject_context()` |
| Router history + workspace agents | `decomposer.py:217,232` | `inject_context()` → `_pending_context` |
| Skills | `claude_session.py:303–309` | `_pending_skills` → string concat |

The only visible injection is `ReminderInjectedEvent` (yields an explicit event, formatted in handler.py, rendered in event_renderer.py). All others silently pre-pend text to `full_prompt` with no user-facing feedback.

A partial workaround exists for history-file injection: `pop_last_injected_files()` in `handler.py:388–404` sends a special-case Telegram message in debug mode only. This is inconsistent and bypasses the event pipeline.

## Goal

Every injection has a corresponding event dataclass. Events are emitted inside `send()` when the pending queues drain, so they appear inline with the response that consumed the injected context. All injection events are: suppressed in quiet/normal mode, shown in verbose/debug mode, and always written to session history. The special-case history-file notification in `handler.py` is removed and replaced by the event pipeline.

---

## Scope

### In Scope
- New `ContextInjectedEvent` and `SkillInjectedEvent` dataclasses
- Tagged `_pending_context: list[tuple[str, str, str | None]]` (text, injection_type, detail)
- Updated `inject_context(text, injection_type, detail)` signature
- Event emission inside `ClaudeSession.send()` for both context and skill pending queues
- `format_event()` cases in `handler.py` (verbose/debug gating, same as `ReminderInjectedEvent`)
- `render()` cases in `event_renderer.py`
- Updated `Event` union type in `event_mapper.py`
- Caller updates: `session_manager`, `decomposer`, `background_agent_manager`
- Removal of the special-case `pop_last_injected_files` history notification from `handler.py`

### Out of Scope
- Changing injection behaviour (what/when/whether to inject) — only visibility
- Router injection events: router injection events (`router_history`, `router_workspace_agents`) are re-tagged with `source='router'` by `Pipeline.route_task()` and silently suppressed in all Telegram modes (the router event branch in `handler.py` has no `isinstance` check for the new event types). They are still written to session history. No additional handler logic is needed.
- Showing injection content in Telegram (only type + size is shown, not raw content)

---

## Acceptance criteria
- [ ] `ContextInjectedEvent` is yielded from `send()` for every `_pending_context` item, with correct `injection_type` and `size_chars`
- [ ] `SkillInjectedEvent` is yielded from `send()` for every `_pending_skills` item, with correct `skill_name` and `size_chars`
- [ ] In verbose/debug mode: Telegram shows `📌 Context injected [workspace_agents] (N chars)` etc.
- [ ] In quiet/normal mode: no Telegram message for any injection event
- [ ] All injection events appear in session history (`.md` files in `~/.archon/history/`)
- [ ] The special-case `pop_last_injected_files` history notification in `handler.py` is removed; history injection is now covered by `ContextInjectedEvent` with `injection_type="history"`
- [ ] All injection types produce their own log entry in event_renderer output
- [ ] All callers of `inject_context()` pass an `injection_type` string
- [ ] `inject_agent_context()` in `session_manager.py` passes `injection_type="background_agent_completion"`
- [ ] All existing tests pass; new tests achieve ≥85% coverage for modified code
- [ ] `Pipeline.inject_context()` and `Decomposer.inject_context()` accept and forward `injection_type`
- [ ] No reference to `pop_last_injected_files` remains in any test file or production code
- [ ] Router injection events (`router_history`, `router_workspace_agents`) are always suppressed in Telegram but always written to history

---

## What does NOT change
- Reminder injection — `ReminderInjectedEvent` and its emission point in `send()` are untouched
- What content is injected and when — only the observability layer changes
- `flush_pending_context()` signature — it clears the queue without emitting events (called on abort paths only)
- `ClaudeSession.activate_skill()` signature
- The SDK query call sequence (`client.query(full_prompt)`)
- The `suppress_tool_result` / history-suppression logic

---

## Known limitations / accepted trade-offs
- Injection content is NOT shown in Telegram (only type + char count) to avoid flooding; full content is available in session history `.md` files.
- Router session injections are invisible in quiet/normal mode (consistent with all other router events).
- `injection_type` is a free-form string with 6 defined constants (`INJECTION_TYPE_HISTORY`, `INJECTION_TYPE_WORKSPACE_AGENTS`, `INJECTION_TYPE_BACKGROUND_AGENT_COMPLETION`, `INJECTION_TYPE_ROUTER_HISTORY`, `INJECTION_TYPE_ROUTER_WORKSPACE_AGENTS`, `INJECTION_TYPE_BACKGROUND_AGENT_REMINDER`), not an enum, to keep the change minimal; callers are responsible for using the constants defined in `event_mapper.py`. Typos in free-form strings are silent — always use the constants.
- The special-case debug-only history-file notification is removed; users who relied on it in debug mode will now see the event in verbose mode too (broader visibility, not narrower).
- Router session injection events (`router_history`, `router_workspace_agents`) are re-tagged with `source='router'` by `Pipeline.route_task()` via `dataclasses.replace()`. They are silently suppressed in all notification modes because the router event branch in `handler.py` has no `isinstance` check for `ContextInjectedEvent` or `SkillInjectedEvent`. This is intentional — router session context is internal plumbing not useful to end users. History renderer still records them (via event_renderer.py which ignores source). If future requirements need router injection events visible in verbose/debug, add isinstance cases to the router branch.
- `ContextInjectedEvent` and `SkillInjectedEvent` are NOT added to `_EVENT_TYPE_MAP` or `VALID_SUPPRESSED_EVENT_NAMES` in `event_renderer.py` — these events are always written to session history and cannot be suppressed via `[history] suppressed_events`. This is intentional: injection visibility in history is the primary purpose of this feature; suppressing it would defeat the goal.

---

## Architecture

### New dataclasses (in `archon/ai/event_mapper.py`)

```python
INJECTION_TYPE_HISTORY = "history"
INJECTION_TYPE_WORKSPACE_AGENTS = "workspace_agents"
INJECTION_TYPE_BACKGROUND_AGENT_COMPLETION = "background_agent_completion"
INJECTION_TYPE_ROUTER_HISTORY = "router_history"
INJECTION_TYPE_ROUTER_WORKSPACE_AGENTS = "router_workspace_agents"
INJECTION_TYPE_BACKGROUND_AGENT_REMINDER = "background_agent_reminder"

@dataclass
class ContextInjectedEvent:
    injection_type: str          # one of the INJECTION_TYPE_* constants
    size_chars: int              # len(text)
    detail: str | None = None    # e.g. "file1.md, file2.md" for history injection
    source: str = "orchestrator"

@dataclass
class SkillInjectedEvent:
    skill_name: str
    size_chars: int
    source: str = "orchestrator"
```

Added to the `Event` union.

### Modified `_pending_context` in `ClaudeSession`

```python
# Before
_pending_context: list[str]

# After
_pending_context: list[tuple[str, str, str | None]]   # (text, injection_type, detail)
```

`inject_context(text: str, injection_type: str = "context", detail: str | None = None) -> None` — appends `(text, injection_type, detail)`.

### Emission in `ClaudeSession.send()`

```python
# Context drain (before client.query)
for text, injection_type, detail in self._pending_context:
    prefix_parts.append(text)
    yield ContextInjectedEvent(injection_type=injection_type, size_chars=len(text), detail=detail)
self._pending_context.clear()

# Skill drain (before client.query)
for s in self._pending_skills:
    skill_block = f"[Skill: {s.name}]\n{s.content}\n[End Skill: {s.name}]"
    prefix_parts.append(skill_block)
    yield SkillInjectedEvent(skill_name=s.name, size_chars=len(skill_block))
self._pending_skills.clear()
```

Note: `size_chars` for `SkillInjectedEvent` measures the full injected block (including `[Skill: ...]` wrappers), not just `s.content` — this accurately reflects the chars added to the prompt.

### Telegram formatter (`handler.py:format_event()`)

```python
if isinstance(event, ContextInjectedEvent):
    if mode not in ("verbose", "debug"):
        return []
    label = f"📌 Context injected [{html.escape(event.injection_type)}] ({event.size_chars} chars)"
    if event.detail:
        label += f": {html.escape(event.detail)}"
    return [label]

if isinstance(event, SkillInjectedEvent):
    if mode not in ("verbose", "debug"):
        return []
    return [f"🎯 Skill injected: {html.escape(event.skill_name)} ({event.size_chars} chars)"]
```

### History renderer (`event_renderer.py:render()`)

```python
if isinstance(event, ContextInjectedEvent):
    detail_line = f"\n**Detail**: {event.detail}" if event.detail else ""
    return f"\n### 📌 Context injected [{event.injection_type}] · {ts}\n\n{event.size_chars} chars{detail_line}\n"
if isinstance(event, SkillInjectedEvent):
    return f"\n### 🎯 Skill injected: {event.skill_name} · {ts}\n\n{event.size_chars} chars\n"
```

### Caller injection-type mapping

| Caller | Call site | `injection_type` |
|--------|-----------|-----------------|
| `session_manager.py:208` | `get_or_create()` history injection | `"history"` |
| `session_manager.py` | `_create_session()` auto-compact recycling | `"history"` |
| `decomposer.py:130` | `_inject_workspace_agents()` main session | `"workspace_agents"` |
| `decomposer.py:131` | `_inject_workspace_agents()` router session | `"router_workspace_agents"` |
| `background_agent_manager.py` | via `session_manager.inject_agent_context()` | `"background_agent_completion"` |
| `background_agent_manager.py:358` | background agent `agents.md` injection | `"workspace_agents"` |
| `background_agent_manager.py:369` | background agent reminder injection | `"background_agent_reminder"` |
| `decomposer.py:217` | `_ensure_router_session()` history | `"router_history"` |
| `decomposer.py:232` | `_ensure_router_session()` workspace agents | `"router_workspace_agents"` |

`SessionManager.inject_agent_context(user_id, text)` → `session.inject_context(text, INJECTION_TYPE_BACKGROUND_AGENT_COMPLETION)`.

### Removed special case

`handler.py:388–404` (`pop_last_injected_files` block) is deleted. `SessionManager.pop_last_injected_files()` and `_last_injected_files` are also deleted (no longer needed).

---

## Tests

- **test_context_injected_event_dataclass** (unit): fields and defaults (including `detail=None`)
- **test_skill_injected_event_dataclass** (unit): fields and defaults
- **test_inject_context_stores_tagged_tuple** (unit): `inject_context("x", "history")` stores `("x", "history", None)` in `_pending_context`
- **test_inject_context_default_type** (unit): default `injection_type` is `"context"`
- **test_inject_context_with_detail** (unit): `inject_context("x", "history", detail="f1.md, f2.md")` stores `("x", "history", "f1.md, f2.md")`
- **test_send_yields_context_injected_event** (unit): `send()` yields `ContextInjectedEvent` for each pending item before SDK events
- **test_send_yields_skill_injected_event** (unit): `send()` yields `SkillInjectedEvent` for each pending skill
- **test_send_clears_pending_context_after_emit** (unit): `_pending_context` is empty after `send()`
- **test_send_clears_pending_skills_after_emit** (unit): `_pending_skills` is empty after `send()`
- **test_send_injection_events_precede_sdk_events** (unit): `ContextInjectedEvent` comes before any `Response`/`ToolStarted` in the event stream
- **test_send_no_context_event_after_flush** (unit): inject context item, flush, then call `send()` — verify no `ContextInjectedEvent` is yielded
- **test_skill_injected_event_size_chars_full_block** (unit): `SkillInjectedEvent.size_chars` equals `len("[Skill: name]\ncontent\n[End Skill: name]")`, not `len(content)`
- **test_format_event_context_injected_verbose** (unit): returns non-empty list in verbose mode
- **test_format_event_context_injected_debug** (unit): returns non-empty list in debug mode
- **test_format_event_context_injected_quiet** (unit): returns `[]` in quiet mode
- **test_format_event_context_injected_normal** (unit): returns `[]` in normal mode
- **test_format_event_skill_injected_verbose** (unit): returns non-empty list in verbose mode
- **test_format_event_skill_injected_quiet** (unit): returns `[]` in quiet mode
- **test_context_injected_event_detail_in_verbose_mode** (unit): when `detail` is set, Telegram message includes the detail string
- **test_render_context_injected_event** (unit): history output contains injection_type and size
- **test_render_context_injected_event_with_detail** (unit): history output contains `**Detail**: ...` when detail is set
- **test_render_skill_injected_event** (unit): history output contains skill_name and size
- **test_session_manager_injects_history_with_type** (unit): `inject_context` called with `"history"` type and file names as `detail`
- **test_inject_agent_context_passes_type** (unit): `inject_agent_context()` calls `inject_context(text, "background_agent_completion")`
- **test_decomposer_inject_workspace_agents_main_type** (unit): main session gets `"workspace_agents"` type
- **test_decomposer_inject_workspace_agents_router_type** (unit): router session gets `"router_workspace_agents"` type
- **test_ensure_router_session_history_type** (unit): router history injection uses `"router_history"`
- **test_background_agent_injects_agents_with_type** (unit): background agent session `inject_context` called with `"workspace_agents"` type at line 358
- **test_background_agent_injects_reminder_with_type** (unit): background agent session `inject_context` called with `"background_agent_reminder"` type at line 369
- **test_pipeline_inject_context_forwards_type** (unit): `Pipeline.inject_context("x", "history", detail="f1.md")` forwards all three args including `detail` to the inner session
- **test_pipeline_inject_context_forwards_detail_none** (unit): `Pipeline.inject_context("x", "history")` with no detail arg forwards `detail=None` to inner session
- **test_decomposer_inject_context_forwards_type** (unit): `Decomposer.inject_context("x", "workspace_agents", detail="f1.md")` forwards all three args to the inner session
- **test_pop_last_injected_files_removed** (unit): `SessionManager` no longer has `pop_last_injected_files`
- **test_handler_no_history_injection_special_case** (integration): `handle_message()` does not send a special history notice; `ContextInjectedEvent` flows through the normal event pipeline instead
- **test_router_injection_event_has_source_router** (integration): `ContextInjectedEvent` emitted from the router session's `send()` emerges from `Pipeline.send()` with `source="router"`, confirming `dataclasses.replace()` re-tagging works for the new event type

---

## Documentation update
- [ ] `CLAUDE.md`, Output event model table: add `ContextInjectedEvent` and `SkillInjectedEvent` rows (visible in verbose/debug mode)

---

## Task breakdown

### Phase 1 — Event dataclasses and tagged context queue
> **Releasable**: After Task 1.2 — `inject_context()` accepts a type tag and `send()` structure is ready for event emission (no UI change yet).

#### Task 1.1 — Add `ContextInjectedEvent` and `SkillInjectedEvent` to `event_mapper.py`
- [x] **File**: `archon/ai/event_mapper.py`
- **Depends on**: nothing
- **Description**:
  - Add module-level string constants: `INJECTION_TYPE_HISTORY = "history"`, `INJECTION_TYPE_WORKSPACE_AGENTS = "workspace_agents"`, `INJECTION_TYPE_BACKGROUND_AGENT_COMPLETION = "background_agent_completion"`, `INJECTION_TYPE_ROUTER_HISTORY = "router_history"`, `INJECTION_TYPE_ROUTER_WORKSPACE_AGENTS = "router_workspace_agents"`, `INJECTION_TYPE_BACKGROUND_AGENT_REMINDER = "background_agent_reminder"`
  - Add `@dataclass class ContextInjectedEvent: injection_type: str; size_chars: int; detail: str | None = None; source: str = "orchestrator"`
  - Add `@dataclass class SkillInjectedEvent: skill_name: str; size_chars: int; source: str = "orchestrator"`
  - Add both to the `Event` union type (after `ReminderInjectedEvent`)
- **Releasable**: After this task, the new event types are importable.
- **Tests (TDD)** — `tests/ai/test_event_mapper.py`:
  - Unit: `test_context_injected_event_dataclass` — `ContextInjectedEvent("history", 42)` has correct fields, `detail=None` default, and default source
  - Unit: `test_skill_injected_event_dataclass` — `SkillInjectedEvent("my-skill", 100)` has correct fields and default source
  - Unit: `test_injection_type_constants_defined` — all six `INJECTION_TYPE_*` constants are non-empty strings and distinct
  - Checkpoint: `uv run pytest tests/ai/test_event_mapper.py -v -k "injection"`

#### Task 1.2 — Change `_pending_context` to tagged tuples and update `inject_context()`
- [ ] **File**: `archon/ai/claude_session.py`
- **Depends on**: Task 1.1
- **Description**:
  - Change `_pending_context: list[str]` to `_pending_context: list[tuple[str, str, str | None]]` in `__init__`
  - Update `inject_context(self, text: str, injection_type: str = "context", detail: str | None = None) -> None` to append `(text, injection_type, detail)` instead of `text`
  - Update `flush_pending_context()` to clear the list (no behaviour change needed — it just clears)
  - Update the drain loop in `send()` to unpack tuples: `for text, _type, _detail in self._pending_context: prefix_parts.append(text)` — do NOT emit events yet (that is Task 2.1)
  - Import `INJECTION_TYPE_*` constants from `event_mapper` (used by callers; imported here to avoid circular imports only if needed, else import in callers)
- **Releasable**: After this task, `inject_context("x", "history")` works; all callers compile (still passing `str` without type tag — will be updated in Phase 4).
- **Tests (TDD)** — `tests/ai/test_claude_session.py`:
  - Unit: `test_inject_context_stores_tagged_tuple` — `inject_context("x", "history")` stores `("x", "history", None)`
  - Unit: `test_inject_context_with_detail` — `inject_context("x", "history", detail="f1.md")` stores `("x", "history", "f1.md")`
  - Unit: `test_inject_context_default_type` — `inject_context("x")` stores `("x", "context", None)`
  - Unit: `test_flush_pending_context_clears_tagged_list` — after flush, `_pending_context == []`
  - Unit: `test_send_still_prepends_context_text` — `send()` still passes the text to `full_prompt` (content unchanged)
  - Update existing tests that assert `_pending_context` contents as plain strings (e.g., `test_inject_context_queues_text`, `test_inject_context_multiple_calls_accumulate`) — change assertions from `== ["text"]` to `== [("text", "context", None)]`
  - Checkpoint: `uv run pytest tests/ai/test_claude_session.py::TestInjectContext -v`

---

### Phase 2 — Event emission inside `send()`
> **Releasable**: After Task 2.2 — `send()` yields `ContextInjectedEvent` and `SkillInjectedEvent` events. Visible only once formatters and renderers are wired (Phase 3).

#### Task 2.1 — Yield `ContextInjectedEvent` in `send()` at context drain
- [ ] **File**: `archon/ai/claude_session.py`
- **Depends on**: Task 1.2
- **Description**:
  - Import `ContextInjectedEvent` from `archon.ai.event_mapper`
  - Replace the context drain block (lines ~299–301) with:
    ```python
    for text, injection_type, detail in self._pending_context:
        prefix_parts.append(text)
        yield ContextInjectedEvent(injection_type=injection_type, size_chars=len(text), detail=detail)
    self._pending_context.clear()
    ```
  - The `yield` must come after `prefix_parts.append(text)` but before `client.query()` — events are emitted as soon as the context is consumed into the prompt
  - Edge case: if `_pending_context` is empty, nothing is yielded (no change in behaviour)
- **Releasable**: After this task, `ContextInjectedEvent` flows through the event stream from `send()`.
- **Tests (TDD)** — `tests/ai/test_claude_session.py`:
  - Unit: `test_send_yields_context_injected_event` — mock SDK; call `send()` with a pending context item; assert `ContextInjectedEvent` is in the yielded events with correct `injection_type` and `size_chars`
  - Unit: `test_send_yields_multiple_context_events` — two items in `_pending_context` produce two `ContextInjectedEvent`s
  - Unit: `test_send_context_events_before_sdk_events` — `ContextInjectedEvent` precedes any `Response` in the event stream
  - Unit: `test_send_pending_context_cleared_after_emit` — `_pending_context` is empty after `send()` completes
  - Unit: `test_send_no_context_event_when_empty` — no `ContextInjectedEvent` when `_pending_context` is empty
  - Unit: `test_send_no_context_event_after_flush` — `inject_context("x")` then `flush_pending_context()` then `send()` yields zero `ContextInjectedEvent`s
  - Checkpoint: `uv run pytest tests/ai/test_claude_session.py -v -k "context_injected or pending_context"`

#### Task 2.2 — Yield `SkillInjectedEvent` in `send()` at skill drain
- [ ] **File**: `archon/ai/claude_session.py`
- **Depends on**: Task 2.1
- **Description**:
  - Import `SkillInjectedEvent` from `archon.ai.event_mapper`
  - Replace the skill drain block (lines ~303–309) with:
    ```python
    for s in self._pending_skills:
        skill_block = f"[Skill: {s.name}]\n{s.content}\n[End Skill: {s.name}]"
        prefix_parts.append(skill_block)
        yield SkillInjectedEvent(skill_name=s.name, size_chars=len(skill_block))
    self._pending_skills.clear()
    ```
  - Note: build the full block first, then measure it — `size_chars` must reflect the chars actually added to the prompt (wrappers included), not just `s.content`
  - Edge case: if `_pending_skills` is empty, nothing is yielded
  - Skill events follow context events in emission order (context drain happens first)
- **Releasable**: After this task, both `ContextInjectedEvent` and `SkillInjectedEvent` are emitted.
- **Tests (TDD)** — `tests/ai/test_claude_session.py`:
  - Unit: `test_send_yields_skill_injected_event` — mock SDK; activate a skill; `send()` yields `SkillInjectedEvent` with correct `skill_name` and `size_chars` equal to `len("[Skill: name]\ncontent\n[End Skill: name]")`, not `len(content)`
  - Unit: `test_send_skill_events_after_context_events` — when both are pending, context events come first
  - Unit: `test_send_pending_skills_cleared_after_emit` — `_pending_skills` is empty after `send()`
  - Unit: `test_send_no_skill_event_when_empty` — no `SkillInjectedEvent` when no skills are activated
  - Checkpoint: `uv run pytest tests/ai/test_claude_session.py -v -k "skill_injected or pending_skills"`

---

### Phase 3 — Telegram formatter and history renderer
> **Releasable**: After Task 3.2 — injection events produce Telegram messages (in verbose/debug) and history entries.

#### Task 3.1 — Add `format_event()` cases for injection events in `handler.py`
- [ ] **File**: `archon/chat/handler.py`
- **Depends on**: Task 1.1
- **Description**:
  - Import `ContextInjectedEvent`, `SkillInjectedEvent` from `archon.ai.event_mapper`
  - Add after the `ReminderInjectedEvent` block (line ~336):
    ```python
    if isinstance(event, ContextInjectedEvent):
        if mode not in ("verbose", "debug"):
            return []
        label = f"📌 Context injected [{html.escape(event.injection_type)}] ({event.size_chars} chars)"
        if event.detail:
            label += f": {html.escape(event.detail)}"
        return [label]

    if isinstance(event, SkillInjectedEvent):
        if mode not in ("verbose", "debug"):
            return []
        return [f"🎯 Skill injected: {html.escape(event.skill_name)} ({event.size_chars} chars)"]
    ```
  - Both blocks must come before the final `return []` at line ~338
  - No TTS capture for injection events (no text content to speak)
  - No beacon count increment for injection events
- **Releasable**: After this task, injection events render correctly in Telegram (when verbose/debug) and are silently dropped in quiet/normal.
- **Tests (TDD)** — `tests/chat/test_handler.py`:
  - Unit: `test_format_context_injected_verbose` — returns `["📌 Context injected [workspace_agents] (100 chars)"]`
  - Unit: `test_format_context_injected_debug` — non-empty list in debug mode
  - Unit: `test_format_context_injected_quiet` — returns `[]`
  - Unit: `test_format_context_injected_normal` — returns `[]`
  - Unit: `test_format_skill_injected_verbose` — returns `["🎯 Skill injected: my-skill (50 chars)"]`
  - Unit: `test_format_skill_injected_quiet` — returns `[]`
  - Checkpoint: `uv run pytest tests/chat/test_handler.py -v -k "context_injected or skill_injected"`

#### Task 3.2 — Add `render()` cases for injection events in `event_renderer.py`
- [ ] **File**: `archon/ai/event_renderer.py`
- **Depends on**: Task 1.1
- **Description**:
  - Import `ContextInjectedEvent`, `SkillInjectedEvent` from `archon.ai.event_mapper`
  - Add after the `ReminderInjectedEvent` block (line ~195):
    ```python
    if isinstance(event, ContextInjectedEvent):
        detail_line = f"\n**Detail**: {event.detail}" if event.detail else ""
        return f"\n### 📌 Context injected [{event.injection_type}] · {ts}\n\n{event.size_chars} chars{detail_line}\n"
    if isinstance(event, SkillInjectedEvent):
        return f"\n### 🎯 Skill injected: {event.skill_name} · {ts}\n\n{event.size_chars} chars\n"
    ```
  - Must come before the final `return ""` (line ~196)
  - Do NOT add `ContextInjectedEvent` or `SkillInjectedEvent` to `_EVENT_TYPE_MAP` or `VALID_SUPPRESSED_EVENT_NAMES` — these events are always written to history.
- **Releasable**: After this task, injection events are written to session history `.md` files.
- **Tests (TDD)** — `tests/ai/test_event_renderer.py`:
  - Unit: `test_render_context_injected_event` — output contains `"📌 Context injected [history]"` and `"42 chars"`
  - Unit: `test_render_skill_injected_event` — output contains `"🎯 Skill injected: my-skill"` and `"100 chars"`
  - Checkpoint: `uv run pytest tests/ai/test_event_renderer.py -v -k "context_injected or skill_injected"`

---

### Phase 4 — Update callers with injection type tags
> **Releasable**: After each task — that injection point becomes typed and the Telegram/history entry shows the correct label.

#### Task 4.1 — History injection: `session_manager.py` + remove `pop_last_injected_files`
- [ ] **File**: `archon/ai/session_manager.py`
- **Depends on**: Task 1.2
- **Description**:
  - In `get_or_create()`, change `session.inject_context(injected)` to `session.inject_context(injected, INJECTION_TYPE_HISTORY, detail=', '.join(file_names) if file_names else None)` (import constant from `event_mapper`). The `detail` field carries the actual file names (e.g. `"2026-03-12-compacted.md, 2026-03-13-partial.md"`) so verbose/debug users can see which files were injected.
  - Also update `_create_session()` (the internal method used by auto-compact recycling) — same change: `session.inject_context(injected, INJECTION_TYPE_HISTORY, detail=', '.join(file_names) if file_names else None)`. Verify by grepping for all `inject_context` calls in `session_manager.py`.
  - **Ordering**: compute `file_names = [f.name for f in files]` BEFORE the `inject_context()` call so it's available to pass as `detail`. Currently `file_names` is computed after the inject call (for logging) — move it before.
  - Remove `_last_injected_files: dict[int, list[str]]` field from `__init__`
  - Remove `self._last_injected_files[user_id] = file_names` assignment after the inject call
  - Remove `pop_last_injected_files(self, user_id: int) -> list[str]` method entirely
  - Keep the `logger.info()` calls (they're fine for server-side logging)
  - `inject_agent_context(self, user_id: int, text: str) -> None`: change `session.inject_context(text)` to `session.inject_context(text, INJECTION_TYPE_BACKGROUND_AGENT_COMPLETION)`
- **Releasable**: After this task, history and background-agent-completion injections are typed.
- **Tests (TDD)** — `tests/ai/test_session_manager.py` (and `tests/chat/test_handler.py`):
  - Unit: `test_get_or_create_injects_history_with_type` — mock compactor; assert `inject_context` is called with `(injected, "history", detail=<file_names_string>)`
  - Unit: `test_create_session_auto_compact_injects_history_with_type` — assert `_create_session()` also passes `INJECTION_TYPE_HISTORY` and detail
  - Unit: `test_inject_agent_context_passes_completion_type` — assert `inject_context(text, "background_agent_completion")`
  - Unit: `test_pop_last_injected_files_not_present` — `SessionManager` has no `pop_last_injected_files` attribute
  - Checkpoint: `uv run pytest tests/ai/test_session_manager.py -v`

#### Task 4.2 — Workspace agents injection: `decomposer.py` and `background_agent_manager.py`
- [ ] **Files**: `archon/ai/decomposer.py`, `archon/ai/background_agent_manager.py`
- **Depends on**: Task 1.2
- **Description**:
  - In `decomposer.py`: import `INJECTION_TYPE_WORKSPACE_AGENTS`, `INJECTION_TYPE_ROUTER_WORKSPACE_AGENTS`, `INJECTION_TYPE_ROUTER_HISTORY` from `archon.ai.event_mapper`
  - In `_inject_workspace_agents()`:
    - `self._session.inject_context(ctx)` → `self._session.inject_context(ctx, INJECTION_TYPE_WORKSPACE_AGENTS)`
    - `self._router_session.inject_context(ctx)` → `self._router_session.inject_context(ctx, INJECTION_TYPE_ROUTER_WORKSPACE_AGENTS)`
  - In `_ensure_router_session()`:
    - `self._router_session.inject_context(injected)` → `self._router_session.inject_context(injected, INJECTION_TYPE_ROUTER_HISTORY)`
    - `self._router_session.inject_context(workspace_ctx)` → `self._router_session.inject_context(workspace_ctx, INJECTION_TYPE_ROUTER_WORKSPACE_AGENTS)`
  - In `background_agent_manager.py`: import `INJECTION_TYPE_WORKSPACE_AGENTS`, `INJECTION_TYPE_BACKGROUND_AGENT_REMINDER` from `archon.ai.event_mapper`
    - Line 358 (background agent `agents.md` injection): `session.inject_context(agents_ctx, INJECTION_TYPE_WORKSPACE_AGENTS)`
    - Line 369 (background agent reminder injection): `session.inject_context(reminder_ctx, INJECTION_TYPE_BACKGROUND_AGENT_REMINDER)`
- **Releasable**: After this task, all decomposer and background agent session injections are typed.
- **Tests (TDD)** — `tests/ai/test_decomposer.py`, `tests/ai/test_background_agent_manager.py`:
  - Unit: `test_inject_workspace_agents_main_session_type` — main session `inject_context` called with `"workspace_agents"`
  - Unit: `test_inject_workspace_agents_router_session_type` — router session `inject_context` called with `"router_workspace_agents"`
  - Unit: `test_ensure_router_session_history_type` — router history injection uses `"router_history"`
  - Unit: `test_ensure_router_session_workspace_type` — router workspace injection uses `"router_workspace_agents"`
  - Unit: `test_background_agent_injects_agents_with_type` — background agent session `inject_context` called with `"workspace_agents"` at agents.md injection point
  - Unit: `test_background_agent_injects_reminder_with_type` — background agent session `inject_context` called with `"background_agent_reminder"` at reminder injection point
  - Integration: `test_router_injection_event_has_source_router` — inject context into router session; after `route_task()` processes it, the emitted `ContextInjectedEvent` has `source="router"`, confirming the router suppression path works end-to-end
  - Checkpoint: `uv run pytest tests/ai/test_decomposer.py tests/ai/test_background_agent_manager.py -v -k "inject"`

#### Task 4.3 — Remove `pop_last_injected_files` special case from `handler.py`
- [ ] **File**: `archon/chat/handler.py`
- **Depends on**: Task 4.1, Task 3.1
- **Description**:
  - Delete the block `handler.py:388–404` that calls `pop_last_injected_files()` and sends a special Telegram message
  - Remove the import of `pop_last_injected_files` if it is a named import (it's a method call on `session_manager`, so just remove the usage)
  - The `ContextInjectedEvent` with `injection_type="history"` emitted from `send()` now covers this use case through the normal event pipeline
  - Remove ALL `pop_last_injected_files` mock setups from: `tests/chat/test_handler.py`, `tests/chat/test_file_handler_integration.py`, `tests/chat/test_handler_prompt_override.py`, `tests/gateway/test_full_flow.py` (grep for `pop_last_injected_files` to find all sites — approximately 52 total across these files)
  - Verify that no other code or test file references `pop_last_injected_files` (grep to confirm zero remaining occurrences)
- **Releasable**: After this task, handler is clean — no special-case injection notification.
- **Tests (TDD)** — `tests/chat/test_handler.py`:
  - Unit: `test_handle_message_no_history_notice_sent` — `handle_message()` with a new session (which has a pending context item) does not send a separate history-notice message; the `ContextInjectedEvent` flows through `format_event()` instead
  - Unit: `test_handle_message_history_injected_visible_in_verbose` — `ContextInjectedEvent(injection_type="history", ...)` produces a Telegram message in verbose mode
  - Checkpoint: `uv run pytest tests/chat/test_handler.py -v -k "history_notice or history_injected"`

#### Task 4.4 — Update `Pipeline.inject_context()` and `Decomposer.inject_context()` pass-through signatures
- [ ] **Files**: `archon/ai/pipeline.py`, `archon/ai/decomposer.py`
- **Depends on**: Task 1.2
- **Description**:
  - Both `Pipeline.inject_context(self, text: str)` (pipeline.py:564) and `Decomposer.inject_context(self, text: str)` (decomposer.py:594) are pass-through wrappers that delegate to the inner session. They currently accept only one argument.
  - Update `Pipeline.inject_context` to: `def inject_context(self, text: str, injection_type: str = "context", detail: str | None = None) -> None` and forward all three args to the inner session.
  - Update `Decomposer.inject_context` to the same signature and forward all args.
  - Without this fix, `inject_agent_context()` (which calls `session.inject_context(text, INJECTION_TYPE_BACKGROUND_AGENT_COMPLETION)` where `session` is a `Pipeline` duck-type) raises `TypeError` at runtime.
- **Releasable**: After this task, the duck-typed interface is consistent end-to-end.
- **Tests (TDD)**:
  - Unit: `test_pipeline_inject_context_forwards_type` (`tests/ai/test_pipeline.py`) — `Pipeline.inject_context("x", "history", detail="f1.md")` forwards all three args (including `detail`) to the inner session's `inject_context`; assert mock called with `("x", "history", detail="f1.md")`
  - Unit: `test_decomposer_inject_context_forwards_type` (`tests/ai/test_decomposer.py`) — `Decomposer.inject_context("x", "workspace_agents", detail="f1.md")` forwards all three args; assert mock called with `("x", "workspace_agents", detail="f1.md")`
  - Unit: `test_pipeline_inject_context_forwards_detail_none` (`tests/ai/test_pipeline.py`) — `Pipeline.inject_context("x", "history")` with no `detail` arg forwards `detail=None` to the inner session
  - Checkpoint: `uv run pytest tests/ai/test_decomposer.py tests/ai/test_pipeline.py -v -k "inject_context"`

---

### Phase 5 — Documentation and full test sweep
> **Releasable**: After Task 5.1 — documentation updated and all tests green.

#### Task 5.1 — Update CLAUDE.md output event model table and component catalog
- [ ] **Files**: `CLAUDE.md`, `Documentation/Architecture/110_component_catalog_and_layer_breakdown.md`
- **Depends on**: Task 3.1
- **Description**:
  - In `CLAUDE.md` "Output event model" table, add two rows after the `ReminderInjectedEvent` row:
    | `ContextInjectedEvent` | `📌 Context injected [<type>] (N chars)[: detail]` (verbose/debug only) |
    | `SkillInjectedEvent` | `🎯 Skill injected: <name> (N chars)` (verbose/debug only) |
  - In `Documentation/Architecture/110_component_catalog_and_layer_breakdown.md`, SessionManager API table: remove the `pop_last_injected_files` row (method no longer exists)
  - No other documentation changes required (implementation details live in Architecture docs)
- **Releasable**: After this task, the feature is fully documented.
- **Tests (TDD)**: N/A (documentation only)
- **Checkpoint**: `uv run pytest -x` (full suite)
