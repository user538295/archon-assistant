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
- Tagged `_pending_context: list[tuple[str, str]]` (text, injection_type)
- Updated `inject_context(text, injection_type)` signature
- Event emission inside `ClaudeSession.send()` for both context and skill pending queues
- `format_event()` cases in `handler.py` (verbose/debug gating, same as `ReminderInjectedEvent`)
- `render()` cases in `event_renderer.py`
- Updated `Event` union type in `event_mapper.py`
- Caller updates: `session_manager`, `decomposer`, `background_agent_manager`
- Removal of the special-case `pop_last_injected_files` history notification from `handler.py`

### Out of Scope
- Changing injection behaviour (what/when/whether to inject) — only visibility
- Router injection events: router sessions already suppress most events; router injection events follow the same suppression rules (quiet/normal hidden, verbose/debug shown). No special router-only logic needed.
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
- `injection_type` is a free-form string, not an enum, to keep the change minimal; callers are responsible for using the constants defined in `event_mapper.py`.
- The special-case debug-only history-file notification is removed; users who relied on it in debug mode will now see the event in verbose mode too (broader visibility, not narrower).

---

## Architecture

### New dataclasses (in `archon/ai/event_mapper.py`)

```python
INJECTION_TYPE_HISTORY = "history"
INJECTION_TYPE_WORKSPACE_AGENTS = "workspace_agents"
INJECTION_TYPE_BACKGROUND_AGENT_COMPLETION = "background_agent_completion"
INJECTION_TYPE_ROUTER_HISTORY = "router_history"
INJECTION_TYPE_ROUTER_WORKSPACE_AGENTS = "router_workspace_agents"

@dataclass
class ContextInjectedEvent:
    injection_type: str          # one of the INJECTION_TYPE_* constants
    size_chars: int              # len(text)
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
_pending_context: list[tuple[str, str]]   # (text, injection_type)
```

`inject_context(text: str, injection_type: str = "context") -> None` — appends `(text, injection_type)`.

### Emission in `ClaudeSession.send()`

```python
# Context drain (before client.query)
for text, injection_type in self._pending_context:
    prefix_parts.append(text)
    yield ContextInjectedEvent(injection_type=injection_type, size_chars=len(text))
self._pending_context.clear()

# Skill drain (before client.query)
for s in self._pending_skills:
    prefix_parts.append(f"[Skill: {s.name}]\n{s.content}\n[End Skill: {s.name}]")
    yield SkillInjectedEvent(skill_name=s.name, size_chars=len(s.content))
self._pending_skills.clear()
```

### Telegram formatter (`handler.py:format_event()`)

```python
if isinstance(event, ContextInjectedEvent):
    if mode not in ("verbose", "debug"):
        return []
    return [f"📌 Context injected [{event.injection_type}] ({event.size_chars} chars)"]

if isinstance(event, SkillInjectedEvent):
    if mode not in ("verbose", "debug"):
        return []
    return [f"🎯 Skill injected: {html.escape(event.skill_name)} ({event.size_chars} chars)"]
```

### History renderer (`event_renderer.py:render()`)

```python
if isinstance(event, ContextInjectedEvent):
    return f"\n### 📌 Context injected [{event.injection_type}] · {ts}\n\n{event.size_chars} chars\n"
if isinstance(event, SkillInjectedEvent):
    return f"\n### 🎯 Skill injected: {event.skill_name} · {ts}\n\n{event.size_chars} chars\n"
```

### Caller injection-type mapping

| Caller | Call site | `injection_type` |
|--------|-----------|-----------------|
| `session_manager.py:208` | `get_or_create()` history injection | `"history"` |
| `decomposer.py:130` | `_inject_workspace_agents()` main session | `"workspace_agents"` |
| `decomposer.py:131` | `_inject_workspace_agents()` router session | `"router_workspace_agents"` |
| `background_agent_manager.py` | via `session_manager.inject_agent_context()` | `"background_agent_completion"` |
| `decomposer.py:217` | `_ensure_router_session()` history | `"router_history"` |
| `decomposer.py:232` | `_ensure_router_session()` workspace agents | `"router_workspace_agents"` |

`SessionManager.inject_agent_context(user_id, text)` → `session.inject_context(text, INJECTION_TYPE_BACKGROUND_AGENT_COMPLETION)`.

### Removed special case

`handler.py:388–404` (`pop_last_injected_files` block) is deleted. `SessionManager.pop_last_injected_files()` and `_last_injected_files` are also deleted (no longer needed).

---

## Tests

- **test_context_injected_event_dataclass** (unit): fields and defaults
- **test_skill_injected_event_dataclass** (unit): fields and defaults
- **test_inject_context_stores_tagged_tuple** (unit): `inject_context("x", "history")` stores `("x", "history")` in `_pending_context`
- **test_inject_context_default_type** (unit): default `injection_type` is `"context"`
- **test_send_yields_context_injected_event** (unit): `send()` yields `ContextInjectedEvent` for each pending item before SDK events
- **test_send_yields_skill_injected_event** (unit): `send()` yields `SkillInjectedEvent` for each pending skill
- **test_send_clears_pending_context_after_emit** (unit): `_pending_context` is empty after `send()`
- **test_send_clears_pending_skills_after_emit** (unit): `_pending_skills` is empty after `send()`
- **test_send_injection_events_precede_sdk_events** (unit): `ContextInjectedEvent` comes before any `Response`/`ToolStarted` in the event stream
- **test_format_event_context_injected_verbose** (unit): returns non-empty list in verbose mode
- **test_format_event_context_injected_debug** (unit): returns non-empty list in debug mode
- **test_format_event_context_injected_quiet** (unit): returns `[]` in quiet mode
- **test_format_event_context_injected_normal** (unit): returns `[]` in normal mode
- **test_format_event_skill_injected_verbose** (unit): returns non-empty list in verbose mode
- **test_format_event_skill_injected_quiet** (unit): returns `[]` in quiet mode
- **test_render_context_injected_event** (unit): history output contains injection_type and size
- **test_render_skill_injected_event** (unit): history output contains skill_name and size
- **test_session_manager_injects_history_with_type** (unit): `inject_context` called with `"history"` type
- **test_inject_agent_context_passes_type** (unit): `inject_agent_context()` calls `inject_context(text, "background_agent_completion")`
- **test_decomposer_inject_workspace_agents_main_type** (unit): main session gets `"workspace_agents"` type
- **test_decomposer_inject_workspace_agents_router_type** (unit): router session gets `"router_workspace_agents"` type
- **test_ensure_router_session_history_type** (unit): router history injection uses `"router_history"`
- **test_pop_last_injected_files_removed** (unit): `SessionManager` no longer has `pop_last_injected_files`
- **test_handler_no_history_injection_special_case** (integration): `handle_message()` does not send a special history notice; `ContextInjectedEvent` flows through the normal event pipeline instead

---

## Documentation update
- [ ] `CLAUDE.md`, Output event model table: add `ContextInjectedEvent` and `SkillInjectedEvent` rows (visible in verbose/debug mode)

---

## Task breakdown

### Phase 1 — Event dataclasses and tagged context queue
> **Releasable**: After Task 1.2 — `inject_context()` accepts a type tag and `send()` structure is ready for event emission (no UI change yet).

#### Task 1.1 — Add `ContextInjectedEvent` and `SkillInjectedEvent` to `event_mapper.py`
- [ ] **File**: `archon/ai/event_mapper.py`
- **Depends on**: nothing
- **Description**:
  - Add module-level string constants: `INJECTION_TYPE_HISTORY = "history"`, `INJECTION_TYPE_WORKSPACE_AGENTS = "workspace_agents"`, `INJECTION_TYPE_BACKGROUND_AGENT_COMPLETION = "background_agent_completion"`, `INJECTION_TYPE_ROUTER_HISTORY = "router_history"`, `INJECTION_TYPE_ROUTER_WORKSPACE_AGENTS = "router_workspace_agents"`
  - Add `@dataclass class ContextInjectedEvent: injection_type: str; size_chars: int; source: str = "orchestrator"`
  - Add `@dataclass class SkillInjectedEvent: skill_name: str; size_chars: int; source: str = "orchestrator"`
  - Add both to the `Event` union type (after `ReminderInjectedEvent`)
- **Releasable**: After this task, the new event types are importable.
- **Tests (TDD)** — `tests/ai/test_event_mapper.py`:
  - Unit: `test_context_injected_event_dataclass` — `ContextInjectedEvent("history", 42)` has correct fields and default source
  - Unit: `test_skill_injected_event_dataclass` — `SkillInjectedEvent("my-skill", 100)` has correct fields and default source
  - Unit: `test_injection_type_constants_defined` — all five `INJECTION_TYPE_*` constants are non-empty strings and distinct
  - Checkpoint: `uv run pytest tests/ai/test_event_mapper.py -v -k "injection"`

#### Task 1.2 — Change `_pending_context` to tagged tuples and update `inject_context()`
- [ ] **File**: `archon/ai/claude_session.py`
- **Depends on**: Task 1.1
- **Description**:
  - Change `_pending_context: list[str]` to `_pending_context: list[tuple[str, str]]` in `__init__`
  - Update `inject_context(self, text: str, injection_type: str = "context") -> None` to append `(text, injection_type)` instead of `text`
  - Update `flush_pending_context()` to clear the list (no behaviour change needed — it just clears)
  - Update the drain loop in `send()` to unpack tuples: `for text, _type in self._pending_context: prefix_parts.append(text)` — do NOT emit events yet (that is Task 2.1)
  - Import `INJECTION_TYPE_*` constants from `event_mapper` (used by callers; imported here to avoid circular imports only if needed, else import in callers)
- **Releasable**: After this task, `inject_context("x", "history")` works; all callers compile (still passing `str` without type tag — will be updated in Phase 4).
- **Tests (TDD)** — `tests/ai/test_claude_session.py`:
  - Unit: `test_inject_context_stores_tagged_tuple` — `inject_context("x", "history")` stores `("x", "history")`
  - Unit: `test_inject_context_default_type` — `inject_context("x")` stores `("x", "context")`
  - Unit: `test_flush_pending_context_clears_tagged_list` — after flush, `_pending_context == []`
  - Unit: `test_send_still_prepends_context_text` — `send()` still passes the text to `full_prompt` (content unchanged)
  - Checkpoint: `uv run pytest tests/ai/test_claude_session.py -v -k "inject_context or pending_context or flush"`

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
    for text, injection_type in self._pending_context:
        prefix_parts.append(text)
        yield ContextInjectedEvent(injection_type=injection_type, size_chars=len(text))
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
  - Checkpoint: `uv run pytest tests/ai/test_claude_session.py -v -k "context_injected or pending_context"`

#### Task 2.2 — Yield `SkillInjectedEvent` in `send()` at skill drain
- [ ] **File**: `archon/ai/claude_session.py`
- **Depends on**: Task 2.1
- **Description**:
  - Import `SkillInjectedEvent` from `archon.ai.event_mapper`
  - Replace the skill drain block (lines ~303–309) with:
    ```python
    for s in self._pending_skills:
        prefix_parts.append(f"[Skill: {s.name}]\n{s.content}\n[End Skill: {s.name}]")
        yield SkillInjectedEvent(skill_name=s.name, size_chars=len(s.content))
    self._pending_skills.clear()
    ```
  - Edge case: if `_pending_skills` is empty, nothing is yielded
  - Skill events follow context events in emission order (context drain happens first)
- **Releasable**: After this task, both `ContextInjectedEvent` and `SkillInjectedEvent` are emitted.
- **Tests (TDD)** — `tests/ai/test_claude_session.py`:
  - Unit: `test_send_yields_skill_injected_event` — mock SDK; activate a skill; `send()` yields `SkillInjectedEvent` with correct `skill_name` and `size_chars`
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
        return [f"📌 Context injected [{event.injection_type}] ({event.size_chars} chars)"]

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
        return f"\n### 📌 Context injected [{event.injection_type}] · {ts}\n\n{event.size_chars} chars\n"
    if isinstance(event, SkillInjectedEvent):
        return f"\n### 🎯 Skill injected: {event.skill_name} · {ts}\n\n{event.size_chars} chars\n"
    ```
  - Must come before the final `return ""` (line ~196)
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
  - In `get_or_create()`, change `session.inject_context(injected)` to `session.inject_context(injected, INJECTION_TYPE_HISTORY)` (import constant from `event_mapper`)
  - Remove `_last_injected_files: dict[int, list[str]]` field from `__init__`
  - Remove `self._last_injected_files[user_id] = file_names` assignment after the inject call
  - Remove `pop_last_injected_files(self, user_id: int) -> list[str]` method entirely
  - Keep the `logger.info()` calls (they're fine for server-side logging)
  - `inject_agent_context(self, user_id: int, text: str) -> None`: change `session.inject_context(text)` to `session.inject_context(text, INJECTION_TYPE_BACKGROUND_AGENT_COMPLETION)`
- **Releasable**: After this task, history and background-agent-completion injections are typed.
- **Tests (TDD)** — `tests/ai/test_session_manager.py` (and `tests/chat/test_handler.py`):
  - Unit: `test_get_or_create_injects_history_with_type` — mock compactor; assert `inject_context` is called with `(injected, "history")`
  - Unit: `test_inject_agent_context_passes_completion_type` — assert `inject_context(text, "background_agent_completion")`
  - Unit: `test_pop_last_injected_files_not_present` — `SessionManager` has no `pop_last_injected_files` attribute
  - Checkpoint: `uv run pytest tests/ai/test_session_manager.py -v`

#### Task 4.2 — Workspace agents injection: `decomposer.py`
- [ ] **File**: `archon/ai/decomposer.py`
- **Depends on**: Task 1.2
- **Description**:
  - Import `INJECTION_TYPE_WORKSPACE_AGENTS`, `INJECTION_TYPE_ROUTER_WORKSPACE_AGENTS`, `INJECTION_TYPE_ROUTER_HISTORY` from `archon.ai.event_mapper`
  - In `_inject_workspace_agents()`:
    - `self._session.inject_context(ctx)` → `self._session.inject_context(ctx, INJECTION_TYPE_WORKSPACE_AGENTS)`
    - `self._router_session.inject_context(ctx)` → `self._router_session.inject_context(ctx, INJECTION_TYPE_ROUTER_WORKSPACE_AGENTS)`
  - In `_ensure_router_session()`:
    - `self._router_session.inject_context(injected)` → `self._router_session.inject_context(injected, INJECTION_TYPE_ROUTER_HISTORY)`
    - `self._router_session.inject_context(workspace_ctx)` → `self._router_session.inject_context(workspace_ctx, INJECTION_TYPE_ROUTER_WORKSPACE_AGENTS)`
- **Releasable**: After this task, all decomposer injections are typed.
- **Tests (TDD)** — `tests/ai/test_decomposer.py`:
  - Unit: `test_inject_workspace_agents_main_session_type` — main session `inject_context` called with `"workspace_agents"`
  - Unit: `test_inject_workspace_agents_router_session_type` — router session `inject_context` called with `"router_workspace_agents"`
  - Unit: `test_ensure_router_session_history_type` — router history injection uses `"router_history"`
  - Unit: `test_ensure_router_session_workspace_type` — router workspace injection uses `"router_workspace_agents"`
  - Checkpoint: `uv run pytest tests/ai/test_decomposer.py -v -k "inject"`

#### Task 4.3 — Remove `pop_last_injected_files` special case from `handler.py`
- [ ] **File**: `archon/chat/handler.py`
- **Depends on**: Task 4.1, Task 3.1
- **Description**:
  - Delete the block `handler.py:388–404` that calls `pop_last_injected_files()` and sends a special Telegram message
  - Remove the import of `pop_last_injected_files` if it is a named import (it's a method call on `session_manager`, so just remove the usage)
  - The `ContextInjectedEvent` with `injection_type="history"` emitted from `send()` now covers this use case through the normal event pipeline
  - Verify that no other code references `pop_last_injected_files` (grep to confirm)
- **Releasable**: After this task, handler is clean — no special-case injection notification.
- **Tests (TDD)** — `tests/chat/test_handler.py`:
  - Unit: `test_handle_message_no_history_notice_sent` — `handle_message()` with a new session (which has a pending context item) does not send a separate history-notice message; the `ContextInjectedEvent` flows through `format_event()` instead
  - Unit: `test_handle_message_history_injected_visible_in_verbose` — `ContextInjectedEvent(injection_type="history", ...)` produces a Telegram message in verbose mode
  - Checkpoint: `uv run pytest tests/chat/test_handler.py -v -k "history_notice or history_injected"`

---

### Phase 5 — Documentation and full test sweep
> **Releasable**: After Task 5.1 — documentation updated and all tests green.

#### Task 5.1 — Update CLAUDE.md output event model table
- [ ] **File**: `CLAUDE.md`
- **Depends on**: Task 3.1
- **Description**:
  - In the "Output event model" table, add two rows after the `ReminderInjectedEvent` row:
    | `ContextInjectedEvent` | `📌 Context injected [<type>] (N chars)` (verbose/debug only) |
    | `SkillInjectedEvent` | `🎯 Skill injected: <name> (N chars)` (verbose/debug only) |
  - No other documentation changes required (implementation details live in Architecture docs)
- **Releasable**: After this task, the feature is fully documented.
- **Tests (TDD)**: N/A (documentation only)
- **Checkpoint**: `uv run pytest -x` (full suite)
