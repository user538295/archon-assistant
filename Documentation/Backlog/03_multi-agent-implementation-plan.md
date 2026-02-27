# Multi-Agent Pipeline — Phase 1 Implementation Plan

> **Source docs:** `03_multi-agent-architecture.md` (full spec), `03_multi-agent-discussion-notes.md` (agreed decisions)
> **Scope:** Phase 1 — Classifier + Pipeline routing + Decomposer handling directly (no agent spawning)
> **Started:** 2026-02-27

---

## Architecture Summary

2 LLM sessions per user. No config toggle. No backward compatibility.

```
User message (Telegram)
  → Chat Handler (handler.py)
    → Pipeline (routing logic)
      → Classifier (Haiku) → JSON {intent, confidence}
      → Pipeline parses/validates JSON
      → Decomposer (Sonnet) receives: prompt + classification
        ├─ chat → conversational response
        └─ task → handles directly (Phase 1)
      → Events stream back
    → Chat Handler formats and sends to Telegram
```

| Component | Role |
|---|---|
| Classifier | Haiku session, outputs JSON classification. No skills/plugins/agents. Only QMD if available. |
| Decomposer | Sonnet session, the brain. Full access: skills, plugins, agents, QMD, background agent MCP, spawn_rule. |
| Pipeline | Routing logic: classify → parse → route to Decomposer. Duck-types as ClaudeSession. |
| Chat Handler | Delivery layer (Telegram). Unchanged — calls `pipeline.send()` same as `session.send()`. |

---

## Key Design Decisions

- Classifier model + confidence threshold: **hardcoded**, not in config.toml
- Malformed classifier JSON: **default to task intent** (confidence=0.0) + log warning
- Decomposer system prompt: lives in **`archon/ai/prompts/decomposer.md`**
- Classifier system prompt: lives in **`archon/ai/prompts/classifier.md`**
- Pipeline delegates all properties/methods to Decomposer (duck-typing surface for handler.py)
- No `system_prompt_prefix` concept — `system_prompt` is a normal ClaudeSession parameter

---

## Tasks

### Layer 1 — Internal plumbing (no behavior change)

- [x] **#1 — Classification schema + parser**
  - `archon/ai/classification.py`: `Classification` dataclass + `parse_classification()`
  - `tests/ai/test_classification.py`: happy path, malformed JSON, missing fields, clamping, invalid intent
  - TDD: tests first → implement → green
  - Tests: unit
  - Checkpoint: `uv run pytest tests/ai/test_classification.py && uv run pytest`

- [x] **#2 — system_prompt param + prompt files + loader**
  - `system_prompt: str | None` param on `ClaudeSession.__init__()`, wired into `_build_system_prompt()`
  - `archon/ai/prompts/__init__.py`: `load_prompt(name) -> str`
  - `archon/ai/prompts/classifier.md` + `archon/ai/prompts/decomposer.md`
  - Tests in `test_claude_session.py` + `tests/ai/test_prompts.py`
  - TDD: tests first → implement → green
  - Tests: unit
  - Checkpoint: `uv run pytest tests/ai/test_claude_session.py tests/ai/test_prompts.py && uv run pytest`

### Layer 2 — First feature (classification visible in Telegram)

- [x] **#3 — Pipeline + Classifier wired — classification visible in Telegram** *(blocked by #1, #2)*
  - `archon/ai/pipeline.py`: Pipeline class (Classifier Haiku + Decomposer Sonnet)
    - `send()`: classify → parse → yield ClassificationEvent → route to Decomposer → yield events
    - `start()`/`stop()` manage both sessions
    - Delegates properties/methods to Decomposer
  - `ClassificationEvent(intent, confidence, source)` in `event_mapper.py`, added to `Event` union
  - `format_event()` handles ClassificationEvent: verbose/debug → `"🏷 task (0.95)"`, normal/quiet → filtered
  - SessionManager default factory creates Pipeline instead of ClaudeSession
  - TDD: tests first → implement → green
  - Tests: unit + integration
  - Checkpoint: `uv run pytest tests/ai/test_pipeline.py tests/ai/test_session_manager.py && uv run pytest`
  - **User sees:** 🏷 classification on every message in verbose/debug mode

### Layer 3 — Classification drives behavior

- [x] **#4 — Classification drives routing — chat vs task behavior** *(blocked by #3)*
  - Pipeline prepends classification JSON to Decomposer prompt
  - Enhanced `decomposer.md`: role description, classification handling, scope heuristics, Phase 1 "handle directly"
  - TDD: tests first → implement → green
  - Tests: integration
  - Checkpoint: `uv run pytest tests/ai/test_pipeline_integration.py && uv run pytest`
  - **User sees:** "hello" → conversational response. "write a test" → full tool-using response.

### Layer 4 — Hardening + verification

- [x] **#5 — Classifier failure handling — graceful degradation** *(blocked by #4)*
  - Pipeline handles: Classifier crash → default task intent + log error
  - Pipeline handles: Classifier empty/no Response → default task intent
  - Pipeline handles: Classifier timeout
  - TDD: tests first → implement → green
  - Tests: unit + integration
  - Checkpoint: `uv run pytest tests/ai/test_pipeline.py && uv run pytest`
  - **User sees:** system stays up even when Classifier breaks

- [ ] **#6 — E2E smoke test — full message flow verified** *(blocked by #5)*
  - `tests/ai/test_pipeline_e2e.py`: patches at SDK level only
    - Chat flow end-to-end
    - Task flow end-to-end
    - Malformed classifier → fallback → Decomposer responds
    - Two users: independent Pipelines
    - Session lifecycle: create → send → stop → recreate
  - TDD: tests first → green
  - Tests: E2E
  - Checkpoint: `uv run pytest tests/ai/test_pipeline_e2e.py && uv run pytest`
  - **Phase 1 complete.** Full test pyramid: unit + integration + E2E.

---

## Test Pyramid Progression

| Task | Unit | Integration | E2E | User value |
|------|------|-------------|-----|------------|
| #1   | ✅   |             |     | internal   |
| #2   | ✅   |             |     | internal   |
| #3   | ✅   | ✅           |     | 🏷 classification visible |
| #4   |      | ✅           |     | 🎯 chat ≠ task |
| #5   | ✅   | ✅           |     | 🛡 robust  |
| #6   |      |             | ✅   | ✅ production-ready |

---

## Files Created / Modified

| File | Task | Action |
|------|------|--------|
| `archon/ai/classification.py` | #1 | new |
| `tests/ai/test_classification.py` | #1 | new |
| `archon/ai/claude_session.py` | #2 | modify (system_prompt param) |
| `tests/ai/test_claude_session.py` | #2 | modify (new tests) |
| `archon/ai/prompts/__init__.py` | #2 | new |
| `archon/ai/prompts/classifier.md` | #2 | new |
| `archon/ai/prompts/decomposer.md` | #2, #4 | new, then modify |
| `tests/ai/test_prompts.py` | #2 | new |
| `archon/ai/pipeline.py` | #3, #4, #5 | new, then modify |
| `archon/ai/event_mapper.py` | #3 | modify (ClassificationEvent) |
| `archon/chat/handler.py` | #3 | modify (format ClassificationEvent) |
| `archon/ai/session_manager.py` | #3 | modify (factory creates Pipeline) |
| `tests/ai/test_pipeline.py` | #3, #5 | new, then extend |
| `tests/ai/test_session_manager.py` | #3 | modify (Pipeline tests) |
| `tests/chat/test_handler.py` | #3 | modify (ClassificationEvent formatting) |
| `tests/ai/test_pipeline_integration.py` | #4, #5 | new, then extend |
| `tests/ai/test_pipeline_e2e.py` | #6 | new |
