**Purpose**: Completed stories for Epic 12 — filesystem-based agent loader reading ~/.claude/agents/*.md
**Audience**: All developers
**Status**: Completed
**Last reviewed**: 2026-02-26
**Next review**: 2027-02-26

# Epic 12: Filesystem Agent Loader

## Stories

### S12.1: Filesystem-based agent loader (AgentLoader)

**Status**: Completed ✅
**Priority**: Medium
**Estimated effort**: M

**User Story**: As an operator, I want Archon to automatically discover and load agent definitions from `~/.claude/agents/*.md`, so that I can manage my agent team by editing markdown files (the same files used by the Claude TUI) without having to maintain a parallel `[agents]` section in `config.toml`.

#### Acceptance Criteria

- `AgentLoader` loads all `.md` files from `~/.claude/agents/`; archon agents sorted before non-archon agents
- `Agent.is_archon` is `True` iff `name.endswith("-archon")`
- Malformed files (no frontmatter, missing required fields) are skipped with a warning log; valid siblings are still returned
- `_build_sdk_agents(agents)` converts `list[Agent]` to `dict[str, AgentDefinition]`; empty tools → `None`; returns `None` for empty/None input
- `SessionManager` with `agent_loader` uses only archon agents for the SDK; merges with config.toml agents (loader wins on name collision)
- Gateway instantiates `AgentLoader` at startup and wires it into the dispatcher
- `/agents` lists archon agents (🤖) and non-archon agents (🔍) in separate sections; sections absent when empty
- All existing tests remain green; full suite ≥ 97% coverage
- Tests: full suite in `tests/ai/test_agent_loader.py`; updated `test_subagent_integration.py` imports `_build_sdk_agents_config` for old config tests; updated `test_commands.py` for new `/agents` output format

#### Technical Notes

S11.2 introduced config.toml-based agent definitions. This creates duplication with the Claude TUI, which already manages agents as markdown files in `~/.claude/agents/`. The `AgentLoader` reads these files directly, using an opt-in `-archon` suffix convention to distinguish agents designed for the Archon API environment from TUI-only agents.

**Opt-in convention:** An agent is an *Archon agent* when its `name` frontmatter field ends with `-archon` (e.g. `researcher-archon`). TUI-only agents are loaded and shown in `/agents` but are **not** passed to the Claude SDK.

**Agent file format (`~/.claude/agents/<stem>.md`):**
```markdown
---
name: researcher-archon
description: Web research and data-gathering specialist
model: haiku
tools: WebSearch, Read
---

You are a research specialist. ...
```

New file `archon/ai/agent_loader.py`:
- `_strip_quotes(value: str) -> str` — strips surrounding double-quotes from YAML string values
- `Agent` dataclass: `name`, `description`, `prompt` (body), `model: str | None`, `tools: list[str]`; `is_archon` property returns `name.endswith("-archon")`
- `AgentLoader(agents_dir: Path = Path("~/.claude/agents"))` with `load_all()`, `get(name)`, `_load_agent(path)`

`load_all()` ordering: archon agents (sorted alphabetically) followed by non-archon agents (sorted alphabetically); result is cached after first call.

Changes to `session_manager.py`:
- Renamed `_build_sdk_agents(AgentsConfig | None)` → `_build_sdk_agents_config` (kept for backward compat)
- New `_build_sdk_agents(agents: list[Agent] | None) -> dict[str, AgentDefinition] | None`
- `SessionManager.__init__` gains `agent_loader: AgentLoader | None = None` parameter

Changes to `commands.py`: `/agents` output split into two sections: 🤖 Archon agents (filesystem, is_archon=True), 🔍 Other agents (filesystem, is_archon=False).

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)
- [Component Catalog](../Architecture/110_component_catalog_and_layer_breakdown.md)
- [23 Epic 11 Context & Sub-agents](./23_epic11_context_subagents.md)
