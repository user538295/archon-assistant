# Plan: FR.001 — Human-readable Agent Names

**Feature**: When the orchestrator spawns a sub-agent, assign it a random human-readable
name from a fixed pool of 30. Avoid assigning the same name to two concurrently-running
agents. Release the name when the agent stops so it can be reused.

**Methodology**: TDD — all tests written *first* (red), then implementation (green).

---

## Phase 0: Documentation Discovery — COMPLETE ✅

All facts confirmed by reading source. No assumptions.

### Verified APIs & Exact Signatures

#### `archon/ai/claude_session.py`

| Symbol | Location | Signature |
|--------|----------|-----------|
| `ClaudeSession.__init__` | line 43 | `(cwd, skills, model, plugins, agents) → None` |
| `_build_hooks` | line 81 | `(self) → dict` — returns `{"SubagentStart": [HookMatcher(...)], "SubagentStop": [...]}` |
| `_on_subagent_start` (closure) | line 85 | `(hook_input: Any, tool_use_id: str\|None, ctx: Any) → dict` |
| `_on_subagent_stop` (closure) | line 92 | same signature |
| `hook_input.get("agent_id", "")` | line 87 | SDK-provided dict key |
| `hook_input.get("agent_type", "")` | line 88 | SDK-provided dict key |
| `queue.put_nowait(SubagentStarted(...))` | line 86 | side-channel hook queue |

**Key implementation note**: Hook callbacks are **closures** defined inside `_build_hooks`,
not instance methods. They can capture `self` via a local variable:
`session = self` before the `async def`.

#### `archon/ai/event_mapper.py`

| Dataclass | Location | Current Fields |
|-----------|----------|----------------|
| `SubagentStarted` | line 60–63 | `agent_id: str`, `agent_type: str` |
| `SubagentStopped` | line 67–70 | `agent_id: str`, `agent_type: str` |
| `Event` union | line 73–82 | includes `SubagentStarted \| SubagentStopped` |

#### `archon/chat/handler.py`

| Pattern | Location | Current format string |
|---------|----------|-----------------------|
| `SubagentStarted` branch | line 169–174 | `f"🤖 Agent: <b>{agent_type}</b> started"` |
| `SubagentStopped` branch | line 176–181 | `f"🤖 Agent: <b>{agent_type}</b> done"` |
| HTML escaping | lines 173, 180 | `html.escape(event.agent_type)` |

#### Test Patterns (from `tests/ai/test_agent_loader.py`)

- Uses `tmp_path` pytest fixture for file-based tests
- Constructs dataclasses directly: `SubagentStarted(agent_id="x", agent_type="t")`
- Uses `unittest.mock.patch` for injecting mocks
- Modular helper functions at top of file

### Anti-Patterns Confirmed to Avoid

1. **Don't make `_on_subagent_start` an instance method** — it must remain a closure
   inside `_build_hooks` (the current pattern uses closures that capture `queue`).
   Capture `self` as `session = self` inside `_build_hooks` instead.
2. **Don't add `agent_name` as required positional field** — use `agent_name: str = ""`
   (with default) to avoid breaking the ~10 existing tests that construct
   `SubagentStarted(agent_id=..., agent_type=...)` without a name.
3. **Don't assume the SDK provides a name** — `hook_input` only has `agent_id` and
   `agent_type`; archon assigns the human-readable name entirely.
4. **Don't persist names to disk** — in-memory per-session is the correct scope.

---

## Phase 1: TDD Red — Write All Tests First

**Goal**: All new tests exist and fail (import errors or assertion failures). Do not touch
implementation files yet.

### 1.1 Create `tests/ai/test_agent_names.py`

New test file. All tests in this file should fail at this stage because:
- `ClaudeSession` has no `_assign_agent_name` / `_release_agent_name` methods yet
- `SubagentStarted` / `SubagentStopped` have no `agent_name` field yet

```
tests/ai/test_agent_names.py
```

**Tests to write** (22 unit + integration tests):

```
# ── Name pool tests ──────────────────────────────────────────────────────────
test_pool_has_exactly_30_names
    from archon.ai.claude_session import _AGENT_NAMES
    assert len(_AGENT_NAMES) == 30

test_pool_names_are_unique
    assert len(set(_AGENT_NAMES)) == 30

test_pool_names_are_nonempty_strings
    assert all(isinstance(n, str) and n for n in _AGENT_NAMES)

# ── _assign_agent_name ────────────────────────────────────────────────────────
test_assign_returns_name_from_pool
    session = ClaudeSession()
    name = session._assign_agent_name("agent-001")
    assert name in _AGENT_NAMES

test_assign_stores_mapping
    session = ClaudeSession()
    name = session._assign_agent_name("agent-001")
    assert session._active_agent_names["agent-001"] == name

test_assign_two_concurrent_agents_get_different_names
    session = ClaudeSession()
    n1 = session._assign_agent_name("a1")
    n2 = session._assign_agent_name("a2")
    assert n1 != n2

test_assign_same_agent_id_returns_same_name
    # idempotent for the same agent_id
    session = ClaudeSession()
    n1 = session._assign_agent_name("a1")
    n2 = session._assign_agent_name("a1")  # same id — already in _active_agent_names
    assert n1 == n2

test_assign_exhausted_pool_returns_fallback
    # When all 30 names are taken, should not raise
    session = ClaudeSession()
    for i in range(30):
        session._assign_agent_name(f"agent-{i}")
    # 31st agent — pool exhausted
    name = session._assign_agent_name("agent-30")
    assert isinstance(name, str) and name  # non-empty fallback

# ── _release_agent_name ───────────────────────────────────────────────────────
test_release_removes_mapping
    session = ClaudeSession()
    session._assign_agent_name("a1")
    session._release_agent_name("a1")
    assert "a1" not in session._active_agent_names

test_release_returns_the_name
    session = ClaudeSession()
    assigned = session._assign_agent_name("a1")
    released = session._release_agent_name("a1")
    assert released == assigned

test_release_nonexistent_agent_returns_none
    session = ClaudeSession()
    result = session._release_agent_name("ghost")
    assert result is None

test_name_reused_after_release
    session = ClaudeSession()
    n1 = session._assign_agent_name("a1")
    session._release_agent_name("a1")
    # Fill all other 29 names
    for i in range(29):
        session._assign_agent_name(f"b{i}")
    # Now the only available name should be n1
    n2 = session._assign_agent_name("a2")
    assert n2 == n1

# ── SubagentStarted / SubagentStopped agent_name field ───────────────────────
test_subagent_started_has_agent_name_field
    e = SubagentStarted(agent_id="x", agent_type="t", agent_name="Atlas")
    assert e.agent_name == "Atlas"

test_subagent_started_agent_name_defaults_to_empty_string
    e = SubagentStarted(agent_id="x", agent_type="t")
    assert e.agent_name == ""

test_subagent_stopped_has_agent_name_field
    e = SubagentStopped(agent_id="x", agent_type="t", agent_name="Orion")
    assert e.agent_name == "Orion"

test_subagent_stopped_agent_name_defaults_to_empty_string
    e = SubagentStopped(agent_id="x", agent_type="t")
    assert e.agent_name == ""

# ── Hook integration: names are assigned via hooks ────────────────────────────
# These use a mocked _hook_queue drain to inspect events put in the queue.

test_hook_start_puts_subagent_started_with_name
    # Simulate _on_subagent_start callback
    session = ClaudeSession()
    hooks = session._build_hooks()
    start_fn = hooks["SubagentStart"][0].hooks[0]  # the async callback
    await start_fn({"agent_id": "a1", "agent_type": "bash"}, None, None)
    event = session._hook_queue.get_nowait()
    assert isinstance(event, SubagentStarted)
    assert event.agent_name in _AGENT_NAMES
    assert event.agent_id == "a1"

test_hook_stop_puts_subagent_stopped_with_same_name
    session = ClaudeSession()
    hooks = session._build_hooks()
    start_fn = hooks["SubagentStart"][0].hooks[0]
    stop_fn  = hooks["SubagentStop"][0].hooks[0]
    await start_fn({"agent_id": "a1", "agent_type": "bash"}, None, None)
    started = session._hook_queue.get_nowait()
    await stop_fn({"agent_id": "a1", "agent_type": "bash"}, None, None)
    stopped = session._hook_queue.get_nowait()
    assert stopped.agent_name == started.agent_name

test_hook_stop_releases_name_from_active_registry
    session = ClaudeSession()
    hooks = session._build_hooks()
    start_fn = hooks["SubagentStart"][0].hooks[0]
    stop_fn  = hooks["SubagentStop"][0].hooks[0]
    await start_fn({"agent_id": "a1", "agent_type": "bash"}, None, None)
    session._hook_queue.get_nowait()  # drain
    await stop_fn({"agent_id": "a1", "agent_type": "bash"}, None, None)
    session._hook_queue.get_nowait()  # drain
    assert "a1" not in session._active_agent_names

test_two_concurrent_hooks_assign_different_names
    session = ClaudeSession()
    hooks = session._build_hooks()
    start_fn = hooks["SubagentStart"][0].hooks[0]
    await start_fn({"agent_id": "a1", "agent_type": "bash"}, None, None)
    await start_fn({"agent_id": "a2", "agent_type": "bash"}, None, None)
    e1 = session._hook_queue.get_nowait()
    e2 = session._hook_queue.get_nowait()
    assert e1.agent_name != e2.agent_name
```

### 1.2 Extend `tests/chat/test_handler.py`

Append to the existing SubagentStarted/SubagentStopped test section:

```
test_format_subagent_started_shows_agent_name
    event = SubagentStarted(agent_id="x", agent_type="bash", agent_name="Atlas")
    msgs = format_event(event, notifications_normal, split_strategy)
    assert any("Atlas" in m for m in msgs)
    assert all("bash" not in m for m in msgs)  # name replaces type

test_format_subagent_stopped_shows_agent_name
    event = SubagentStopped(agent_id="x", agent_type="bash", agent_name="Orion")
    msgs = format_event(event, notifications_normal, split_strategy)
    assert any("Orion" in m for m in msgs)

test_format_subagent_started_falls_back_to_type_when_no_name
    event = SubagentStarted(agent_id="x", agent_type="bash", agent_name="")
    msgs = format_event(event, notifications_normal, split_strategy)
    assert any("bash" in m for m in msgs)

test_format_subagent_stopped_falls_back_to_type_when_no_name
    event = SubagentStopped(agent_id="x", agent_type="bash", agent_name="")
    msgs = format_event(event, notifications_normal, split_strategy)
    assert any("bash" in m for m in msgs)

test_format_subagent_name_is_html_escaped
    event = SubagentStarted(agent_id="x", agent_type="t", agent_name="<script>")
    msgs = format_event(event, notifications_normal, split_strategy)
    assert all("<script>" not in m for m in msgs)
    assert any("&lt;script&gt;" in m for m in msgs)
```

### Verification Checklist for Phase 1

```bash
# All new tests must be collected (not import-error)
pytest tests/ai/test_agent_names.py --collect-only

# All new tests must FAIL (red phase)
pytest tests/ai/test_agent_names.py -v 2>&1 | grep -E "FAILED|ERROR|PASSED"
# Expected: all FAILED or ERROR, zero PASSED

# Existing test suite must still pass (no regressions from test-only changes)
pytest tests/ -x -q --ignore=tests/ai/test_agent_names.py
```

---

## Phase 2: Add `agent_name` Field to Event Dataclasses

**File**: `archon/ai/event_mapper.py`

**What to change**: Add `agent_name: str = ""` as the **third field** (after `agent_type`)
to both `SubagentStarted` and `SubagentStopped`.

**Pattern to follow**: Other optional fields in this file use the same `field: type = default`
syntax. `agent_name` must be **optional with default `""`** to preserve backward compatibility
with all existing tests that construct these dataclasses without a name.

**Before** (line 60–70):
```python
@dataclass
class SubagentStarted:
    """Fired when the main agent spawns a sub-agent (e.g. via the Task tool)."""
    agent_id: str
    agent_type: str


@dataclass
class SubagentStopped:
    """Fired when a sub-agent completes its work."""
    agent_id: str
    agent_type: str
```

**After**:
```python
@dataclass
class SubagentStarted:
    """Fired when the main agent spawns a sub-agent (e.g. via the Task tool)."""
    agent_id: str
    agent_type: str
    agent_name: str = ""  # human-readable name assigned by Archon's name registry


@dataclass
class SubagentStopped:
    """Fired when a sub-agent completes its work."""
    agent_id: str
    agent_type: str
    agent_name: str = ""  # human-readable name assigned by Archon's name registry
```

No other changes needed in `event_mapper.py`. The `Event` union type is unchanged.

### Verification Checklist for Phase 2

```bash
# Dataclass field tests now pass
pytest tests/ai/test_agent_names.py -k "agent_name_field or defaults_to_empty" -v

# Existing SubagentStarted/SubagentStopped tests still pass (backward compat)
pytest tests/chat/test_handler.py -v -k "subagent"
pytest tests/ai/ -v -q

# No broken imports
python -c "from archon.ai.event_mapper import SubagentStarted, SubagentStopped; print('OK')"
```

---

## Phase 3: Implement Name Registry in `ClaudeSession`

**File**: `archon/ai/claude_session.py`

### 3.1 Add module-level name pool constant

Add after the existing imports (before the class definition):

```python
import random

_AGENT_NAMES: list[str] = [
    "Atlas",   "Sage",   "Orion",  "Nova",   "Echo",
    "Cipher",  "Dusk",   "Ember",  "Flux",   "Gale",
    "Harbor",  "Iris",   "Jade",   "Kite",   "Lyra",
    "Mist",    "Nexus",  "Onyx",   "Pearl",  "Quest",
    "Raven",   "Sable",  "Terra",  "Umbra",  "Vega",
    "Wisp",    "Xara",   "Yara",   "Zara",   "Zephyr",
]  # exactly 30 unique human-readable names
```

> **Important**: Count must be exactly 30. Each name must be unique. Names should be
> single-word, title-cased, and free of special characters.

### 3.2 Add `_active_agent_names` to `__init__`

In `ClaudeSession.__init__` (line 43+), add alongside existing instance vars:

```python
self._active_agent_names: dict[str, str] = {}  # agent_id → assigned name
```

**Pattern to follow**: Insert near the other `dict`-typed instance vars.

### 3.3 Add `_assign_agent_name` method

Add as an instance method after `_build_hooks`:

```python
def _assign_agent_name(self, agent_id: str) -> str:
    """Assign a unique human-readable name to an agent_id.

    If the agent_id already has a name assigned, the existing name is returned
    (idempotent).  When the pool is exhausted (all 30 running concurrently),
    a safe fallback string is returned instead of raising.
    """
    if agent_id in self._active_agent_names:
        return self._active_agent_names[agent_id]
    in_use = set(self._active_agent_names.values())
    available = [n for n in _AGENT_NAMES if n not in in_use]
    name = random.choice(available) if available else (agent_id[:8] or "Agent")
    self._active_agent_names[agent_id] = name
    return name
```

### 3.4 Add `_release_agent_name` method

```python
def _release_agent_name(self, agent_id: str) -> str | None:
    """Release the name assigned to agent_id, returning it (or None if unknown)."""
    return self._active_agent_names.pop(agent_id, None)
```

### Verification Checklist for Phase 3

```bash
# Name pool tests pass
pytest tests/ai/test_agent_names.py -k "pool" -v

# _assign / _release tests pass
pytest tests/ai/test_agent_names.py -k "assign or release or reused" -v

# Full new test file progress check
pytest tests/ai/test_agent_names.py -v 2>&1 | tail -20

# No regressions
pytest tests/ai/ -q
```

---

## Phase 4: Wire Name Assignment into Hook Callbacks

**File**: `archon/ai/claude_session.py`

**Context**: The hook callbacks are closures inside `_build_hooks`. They capture `queue`
from the outer scope. Add `session = self` capture before the closures, then call
`session._assign_agent_name()` and `session._release_agent_name()`.

**Before** (lines 81–102):
```python
def _build_hooks(self) -> dict:
    """Create SubagentStart/Stop hook matchers that push events into the hook queue."""
    queue = self._hook_queue

    async def _on_subagent_start(hook_input: Any, tool_use_id: str | None, ctx: Any) -> dict:
        queue.put_nowait(SubagentStarted(
            agent_id=hook_input.get("agent_id", ""),
            agent_type=hook_input.get("agent_type", ""),
        ))
        return {"continue_": True}

    async def _on_subagent_stop(hook_input: Any, tool_use_id: str | None, ctx: Any) -> dict:
        queue.put_nowait(SubagentStopped(
            agent_id=hook_input.get("agent_id", ""),
            agent_type=hook_input.get("agent_type", ""),
        ))
        return {"continue_": True}

    return {
        "SubagentStart": [HookMatcher(hooks=[_on_subagent_start])],
        "SubagentStop":  [HookMatcher(hooks=[_on_subagent_stop])],
    }
```

**After**:
```python
def _build_hooks(self) -> dict:
    """Create SubagentStart/Stop hook matchers that push events into the hook queue."""
    queue = self._hook_queue
    session = self  # captured so closures can call name-registry methods

    async def _on_subagent_start(hook_input: Any, tool_use_id: str | None, ctx: Any) -> dict:
        agent_id   = hook_input.get("agent_id", "")
        agent_type = hook_input.get("agent_type", "")
        agent_name = session._assign_agent_name(agent_id)
        queue.put_nowait(SubagentStarted(
            agent_id=agent_id,
            agent_type=agent_type,
            agent_name=agent_name,
        ))
        return {"continue_": True}

    async def _on_subagent_stop(hook_input: Any, tool_use_id: str | None, ctx: Any) -> dict:
        agent_id   = hook_input.get("agent_id", "")
        agent_type = hook_input.get("agent_type", "")
        agent_name = session._release_agent_name(agent_id) or ""
        queue.put_nowait(SubagentStopped(
            agent_id=agent_id,
            agent_type=agent_type,
            agent_name=agent_name,
        ))
        return {"continue_": True}

    return {
        "SubagentStart": [HookMatcher(hooks=[_on_subagent_start])],
        "SubagentStop":  [HookMatcher(hooks=[_on_subagent_stop])],
    }
```

### Verification Checklist for Phase 4

```bash
# Hook integration tests pass
pytest tests/ai/test_agent_names.py -k "hook" -v

# Full new test file — all green
pytest tests/ai/test_agent_names.py -v

# Full test suite — no regressions
pytest tests/ -x -q
```

---

## Phase 5: Update Event Formatter in Handler

**File**: `archon/chat/handler.py`

**Current** (lines 169–181):
```python
if isinstance(event, SubagentStarted):
    agent_mode = _resolve_agent_mode(notifications)
    if agent_mode == "quiet":
        return []
    agent_type = html.escape(event.agent_type) if event.agent_type else "unknown"
    return [f"🤖 Agent: <b>{agent_type}</b> started"]

if isinstance(event, SubagentStopped):
    agent_mode = _resolve_agent_mode(notifications)
    if agent_mode == "quiet":
        return []
    agent_type = html.escape(event.agent_type) if event.agent_type else "unknown"
    return [f"🤖 Agent: <b>{agent_type}</b> done"]
```

**After** — use `agent_name` when available, fall back to `agent_type`:
```python
if isinstance(event, SubagentStarted):
    agent_mode = _resolve_agent_mode(notifications)
    if agent_mode == "quiet":
        return []
    display = html.escape(event.agent_name) if event.agent_name else (
        html.escape(event.agent_type) if event.agent_type else "unknown"
    )
    return [f"🤖 Agent <b>{display}</b> started"]

if isinstance(event, SubagentStopped):
    agent_mode = _resolve_agent_mode(notifications)
    if agent_mode == "quiet":
        return []
    display = html.escape(event.agent_name) if event.agent_name else (
        html.escape(event.agent_type) if event.agent_type else "unknown"
    )
    return [f"🤖 Agent <b>{display}</b> done"]
```

> **Note**: The format string changes slightly (`"Agent:"` → `"Agent "`) — update any
> existing tests that check for the exact string `"Agent:"` to match the new format.

### Verification Checklist for Phase 5

```bash
# Handler formatting tests pass
pytest tests/chat/test_handler.py -k "subagent" -v

# Full handler test suite
pytest tests/chat/test_handler.py -v

# All new agent name tests still green
pytest tests/ai/test_agent_names.py -v
```

---

## Final Phase: Full Verification

### Run Complete Test Suite

```bash
# Full suite — must pass with zero failures
pytest tests/ -q

# Coverage report
pytest tests/ --cov=archon --cov-report=term-missing -q

# Confirm new test file is included
pytest tests/ai/test_agent_names.py -v --tb=short
```

### Structural Checks

```bash
# Confirm exactly 30 names
python -c "
from archon.ai.claude_session import _AGENT_NAMES
assert len(_AGENT_NAMES) == 30, f'Got {len(_AGENT_NAMES)}'
assert len(set(_AGENT_NAMES)) == 30, 'Names not unique'
print('✅ Pool: 30 unique names')
"

# Confirm agent_name field exists on both dataclasses
python -c "
from archon.ai.event_mapper import SubagentStarted, SubagentStopped
e1 = SubagentStarted(agent_id='x', agent_type='t', agent_name='Atlas')
e2 = SubagentStopped(agent_id='x', agent_type='t', agent_name='Orion')
e3 = SubagentStarted(agent_id='x', agent_type='t')  # backward compat
assert e1.agent_name == 'Atlas'
assert e2.agent_name == 'Orion'
assert e3.agent_name == ''
print('✅ Dataclass fields OK')
"

# Confirm html.escape is applied in handler (grep check)
grep -n "html.escape(event.agent_name)" archon/chat/handler.py
# Expected: 2 matches (one for SubagentStarted, one for SubagentStopped)

# Anti-pattern check: no invented API methods
grep -rn "hook_input\." archon/ai/claude_session.py
# Expected: only .get("agent_id", ...) and .get("agent_type", ...)
```

### Acceptance Criteria Mapping

| FR.001 requirement | Verification |
|--------------------|-------------|
| Every sub-agent gets a name | `test_hook_start_puts_subagent_started_with_name` |
| Name comes from pool of 30 | `test_pool_has_exactly_30_names` + `test_assign_returns_name_from_pool` |
| No two concurrent agents share a name | `test_two_concurrent_hooks_assign_different_names` |
| Name released when agent stops | `test_hook_stop_releases_name_from_active_registry` |
| Released names can be reused | `test_name_reused_after_release` |
| Pool exhaustion handled gracefully | `test_assign_exhausted_pool_returns_fallback` |
| Name shown in Telegram messages | `test_format_subagent_started_shows_agent_name` |
| HTML injection prevented | `test_format_subagent_name_is_html_escaped` |
| TDD methodology | Phase 1 (red) before Phases 2–5 (green) |

---

## Files Modified Summary

| File | Change |
|------|--------|
| `tests/ai/test_agent_names.py` | **NEW** — 22 unit + integration tests (Phase 1) |
| `tests/chat/test_handler.py` | **APPEND** — 5 handler format tests (Phase 1) |
| `archon/ai/event_mapper.py` | Add `agent_name: str = ""` to 2 dataclasses (Phase 2) |
| `archon/ai/claude_session.py` | Add `_AGENT_NAMES`, `_active_agent_names`, `_assign_agent_name`, `_release_agent_name`; update `_build_hooks` (Phases 3–4) |
| `archon/chat/handler.py` | Update 2 `format_event` branches to display `agent_name` (Phase 5) |

**Total new tests**: ~27 (22 in new file + 5 in handler test file)
