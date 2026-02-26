**Purpose**: Completed stories for Epic 6 — skills integration and live skill loader test
**Audience**: All developers
**Status**: Completed
**Last reviewed**: 2026-02-26
**Next review**: 2027-02-26

# Epic 6: Skills Integration

## Stories

### S6.1: Skills integration

**Status**: Completed ✅
**Priority**: Medium
**Estimated effort**: M

**User Story**: As a Telegram user, I want to list and activate Claude Code skills from the Telegram chat, so that I can leverage specialized skill prompts without leaving Telegram or copy-pasting them manually.

#### Acceptance Criteria

- `SkillLoader.load_all()` reads all `~/.claude/skills/*/SKILL.md` files, parses frontmatter, returns a `Skill` list; malformed frontmatter is logged as a warning and skipped
- `SkillLoader.get(name)` returns the matching `Skill` or `None`
- Every new `ClaudeSession` receives a `system_prompt` that lists all installed skill names and descriptions
- `/skills` replies with a formatted list of skill names and descriptions
- `/skill <name>` with a valid name: queues skill, replies `` ✅ Skill `<name>` activated — it will be applied to your next message ``
- `/skill <name>` with an unknown name: replies `` ❌ Unknown skill `<name>`. Use /skills to see available skills ``
- `/skill <name>` when no session exists: replies `No active session. Send a message first to start one`
- The first `send()` after activation prepends the full skill body as a context block; subsequent sends do not re-inject it (one-shot)
- Tests: `SkillLoader` with `tmp_path` skills (happy path, malformed frontmatter, empty skills dir), `ClaudeSession` system prompt contains the compact registry, skill activation and one-shot injection verified, `/skills` and `/skill` handler unit tests with mock session

#### Technical Notes

- Claude Code skills are Markdown files at `~/.claude/skills/<name>/SKILL.md` with YAML frontmatter (`name`, `description`) and a body containing specialized instructions
- A compact skill registry (name + description of each installed skill) is injected into every new `ClaudeSession` via `ClaudeAgentOptions.system_prompt`
- When the user activates a skill via `/skill <name>`, the full `SKILL.md` body is queued and prepended as a context block to the **next outgoing message only** (one-shot injection)
- Skills are loaded from disk at `SessionManager` startup and cached in memory; skill changes require an Archon restart
- Skill bodies are not injected into the system prompt at startup — they are too large and most will never be used in a given session
- `ClaudeAgentOptions.system_prompt: str | None` is confirmed available in `claude-agent-sdk` 0.1.39

New module: `archon/ai/skill_loader.py`
- `Skill` dataclass: `name: str`, `description: str`, `content: str` (SKILL.md body with frontmatter stripped)
- `SkillLoader` class with `load_all() -> list[Skill]` and `get(name: str) -> Skill | None`

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)
- [Component Catalog](../Architecture/110_component_catalog_and_layer_breakdown.md)

---

### S6.2: Live skill loader test

**Status**: Completed ✅
**Priority**: Low
**Estimated effort**: S

**User Story**: As a developer, I want a live test that exercises `SkillLoader` against the real `~/.claude/skills/` directory, so that I can verify frontmatter parsing and file I/O work against actual installed skills without any mocking.

#### Acceptance Criteria

- `SkillLoader().load_all()` returns at least one `Skill` with non-empty `name`, `description`, and `content`
- `SkillLoader().get(first_skill.name)` returns the same skill object
- `SkillLoader().get("nonexistent-skill")` returns `None`
- No mocks, no patching — pure real filesystem reads

#### Technical Notes

- Test is marked `@pytest.mark.live`; no external services required
- `~/.claude/skills/` must exist and contain at least one skill; test is skipped otherwise

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)
