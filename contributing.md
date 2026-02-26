**Purpose**: Explains how to contribute to Archon Assistant — running tests, coding conventions, branch workflow, and documentation requirements.
**Audience**: All developers
**Status**: Stable
**Last reviewed**: 2026-02-26
**Next review**: 2027-02-26

# Contributing to Archon Assistant

## Principles

1. **TDD is mandatory** — write the failing test before writing any production code; no exceptions.
2. **KISS over cleverness** — apply the simplest solution that satisfies the requirement; increase complexity only when simplicity is genuinely insufficient.
3. **All tests must be green** — never merge with a failing test; fix it or delete it, never skip it.
4. **Behaviour changes require documentation updates** — every PR that alters observable behaviour must update the relevant docs in the same commit.
5. **Facts over assumptions** — never state or document something you have not verified in the source.

---

## Running tests

```bash
# Run all non-live tests (default — excludes external dependencies)
uv run pytest

# Run a single test file
uv run pytest tests/ai/test_event_mapper.py

# Run a single test by name pattern
uv run pytest -k "test_split_strategy_labels"

# Run live tests (require real credentials and the claude binary)
uv run pytest -m live --no-cov -v
```

Coverage is enforced at **≥ 85 %** by `pyproject.toml` (`--cov-fail-under=85`). The CI run fails if coverage drops below this threshold.

### Test markers

| Marker | When to use |
|---|---|
| *(none)* | Pure unit / integration tests — no external dependencies; run by default |
| `@pytest.mark.live` | Requires real filesystem, `claude` binary, or network |
| `@pytest.mark.requires_telegram` | Requires `TELEGRAM_BOT_TOKEN` and `TELEGRAM_LIVE_CHAT_ID` in env |

---

## Type checking

```bash
uv run mypy archon/
```

The project runs mypy in **strict** mode (`strict = true` in `pyproject.toml`). All new code must pass without errors or suppressions.

---

## Coding conventions

### Logging — no `print()`
Every module uses `logging.getLogger("archon")`. Never call `print()` anywhere in production code. Log only metadata, never message content (security requirement — see `Bug.002` rationale in `Documentation/tasks.md`).

```python
# correct
import logging
logger = logging.getLogger("archon")
logger.info("Received message (%d chars)", len(text))

# wrong
print(text)
logger.info("Received message: %s", text)  # leaks content
```

### KISS first
Prefer a flat function over a class, a class over a framework. Add abstractions when they genuinely simplify — not by default.

### Clean Code inside KISS
Use meaningful names, short functions, and no magic numbers. But never let Clean Code practices add complexity that KISS prohibits.

### Error handling
Fail fast in config loading (`ConfigError`). Propagate context in async handlers. Centralise recovery in the Gateway. See `Documentation/Architecture/140_error_handling_strategy.md` for full details.

### New truncation strategies
Add a subclass of `TruncationStrategy` in `archon/ai/truncation.py`. No changes to Gateway or Chat are needed. Set the strategy name in `config.toml [output] truncation_strategy`.

### Whitelist enforcement
The whitelist check lives exclusively in `WhitelistMiddleware` — never inside command handlers or the message handler.

---

## Branch and PR workflow

1. Create a feature branch from `main`: `git checkout -b feat/short-description`
2. Write the failing test first (TDD).
3. Implement the feature until the test passes.
4. Run the full test suite: `uv run pytest`
5. Run the type checker: `uv run mypy archon/`
6. Update documentation if the change is user-visible or alters system behaviour (see below).
7. Open a PR targeting `main`; the description must reference the story or task ID from `Documentation/tasks.md`.

Commit messages use the imperative mood: `Add context compaction summary`, not `Added…`.

---

## Documentation update requirements

Every PR that changes observable behaviour **must** update relevant documentation in the same commit. Specifically:

| Change type | Required doc update |
|---|---|
| New bot command | `README.md` command table + `Documentation/quick_start.md` if it affects onboarding |
| New config key | `README.md` configuration section + `examples/config.toml.example` |
| New architecture component | `CLAUDE.md` architecture section + relevant `Documentation/Architecture/` file |
| New feature | `README.md` Features list |
| Roadmap item completed | Mark as done in `Documentation/roadmap.md` |
| New pending task | Add to `Documentation/roadmap.md` |

Documentation lives in exactly one place. Cross-link rather than repeat. See `Documentation/Architecture/990_documentation_index_and_contribution_guide.md` for the full documentation structure.

---

## Documentation maintenance

Every PR that changes observable behaviour must update relevant documentation.

### When to update docs

| Change type | Docs to update |
|---|---|
| New command or config option | `README.md`, `Documentation/UserManual/user_manual.md` |
| New architecture component | `Documentation/Architecture/110_component_catalog_and_layer_breakdown.md` |
| New architecture decision | New ADR in `Documentation/ADRs/` |
| Changed event format | `README.md` output events table, `CLAUDE.md` event model |
| New feature story | `Documentation/Backlog/` or `Documentation/Completed/` |

### Adding a new Architecture document

1. Choose the correct directory: `Documentation/Architecture/` for design docs, `Documentation/ADRs/` for decisions
2. Follow naming: `NNN_snake_case_name.md` (Architecture) or `NN_topic_name.md` (ADRs)
3. Add the required 5-line metadata header (Purpose, Audience, Status, Last reviewed, Next review)
4. Start with 3–5 guiding principles before diving into details
5. Use Mermaid for all diagrams — no ASCII art
6. Update `Documentation/990_documentation_index_and_contribution_guide.md` to add the new file

### Review cycle

| Document type | Review frequency |
|---|---|
| Architecture docs | Quarterly |
| ADRs | When superseded |
| User manual | On any command/behaviour change |
| Contributing guide | Annually |
