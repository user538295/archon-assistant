# PRD: Workspace agents.md Context Injection

## Overview

On every new Decomposer session start, Archon reads `agents.md` from the configured workspace folder (`[session] working_directory`) and injects it into the session context before any history. This gives the Decomposer a persistent, user-editable registry of available agents and their capabilities — without touching spawned agents or the Classifier.

## Goals

- Give the Decomposer awareness of workspace-specific agents on every session start
- Keep the content fresh: re-read from disk on each new session, picking up edits without restart
- Follow the existing context-injection pattern (`_pending_context`) for architectural consistency
- Zero cost for Classifier and spawned background agents — they don't receive it

## Quality Gates

These commands must pass for every user story:

- `uv run pytest` — all tests green
- `uv run mypy archon/` — no type errors

## User Stories

### US-001: Read agents.md from workspace on session start
As a developer, I want Archon to read `agents.md` from the workspace directory when a new Decomposer session starts so that I can define available agents in a single file.

**Acceptance Criteria:**
- [ ] When a new Decomposer session is created, Archon reads `{working_directory}/agents.md` from disk
- [ ] The file is read on every session creation (not cached across sessions)
- [ ] If the file does not exist, an info-level message is logged (`archon` logger) and injection is skipped silently
- [ ] If the file exists but is empty, injection is skipped (no empty section injected)
- [ ] File read errors (permissions, I/O) are caught, logged at warning level, and do not crash the session

### US-002: Inject agents.md into Decomposer context before history
As a developer, I want `agents.md` to appear at the start of the Decomposer's context so that the model has full capability awareness before reading any conversation history.

**Acceptance Criteria:**
- [ ] The content is injected via `_pending_context` (same mechanism as `inject_context()`)
- [ ] Injection order in the first message prefix: `agents.md` content → history context → user message
- [ ] The injected block is labeled with a clear header, e.g. `# Workspace Agents\n\n{content}`
- [ ] Only the main Decomposer `ClaudeSession` receives the injection — the orchestration session, Classifier, and spawned background agents do not
- [ ] Existing session resume (inactivity timeout → new session) also triggers a fresh read and injection

### US-003: Unit tests for agents.md loading and injection
As a developer, I want tests covering the load-and-inject logic so that regressions are caught early.

**Acceptance Criteria:**
- [ ] Test: file exists → content injected with correct header before history context
- [ ] Test: file missing → info log emitted, no injection
- [ ] Test: file empty → no injection, no error
- [ ] Test: file read raises `OSError` → warning log, no crash, session continues
- [ ] Test: re-read on each call (no caching) — two calls with different file content return different results
- [ ] `uv run pytest tests/ai/` passes with ≥85% coverage on the new code

## Functional Requirements

- FR-1: The workspace path is read from `config.session.working_directory` — no new config field needed
- FR-2: The file name is hardcoded as `agents.md` — not configurable in v1
- FR-3: Injection uses `_pending_context` prepend, not the SDK-level system prompt
- FR-4: Only `ClaudeSession` instances that represent the Decomposer receive injection; spawned agent sessions (`BackgroundAgentManager`) do not
- FR-5: The logger used is `logging.getLogger("archon")` — no `print()`
- FR-6: The feature is always-on; no config flag needed in v1

## Non-Goals

- Making the file name configurable (v1 hardcodes `agents.md`)
- Injecting into the Classifier session
- Injecting into spawned background agents
- Watching the file for changes mid-session (only read at session start)
- Parsing YAML frontmatter or any structured format in `agents.md`
- A config toggle to disable the feature

## Technical Considerations

- `SessionManager.get_or_create()` is the natural injection point — call a new `_load_workspace_agents()` helper there before `inject_context()` for history
- `AgentLoader` (reads `~/.claude/agents/*.md`) is a separate concern and should not be modified — the new feature reads one specific file from the workspace, not from the Claude config directory
- Keep the helper small: read file → prepend header → call `session.inject_context()` — no new class needed
- `Path(config.session.working_directory) / "agents.md"` is the full path

## Success Metrics

- All tests pass, mypy clean
- A manually created `agents.md` in the workspace is visible in the Decomposer's first message context
- Sessions created after editing `agents.md` pick up the new content without restarting Archon

## Open Questions

- Should v2 support multiple agent registry files (e.g. `agents/*.md`)? Not in scope now.