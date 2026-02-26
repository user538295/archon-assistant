**Purpose**: Completed stories for Epic 0 — initial project scaffolding and config loading
**Audience**: All developers
**Status**: Completed
**Last reviewed**: 2026-02-26
**Next review**: 2027-02-26

# Epic 0: Project Setup

## Stories

### S0.1: Initialize project structure

**Status**: Completed ✅
**Priority**: High
**Estimated effort**: S

**User Story**: As a developer, I want a properly initialized Python 3.12 project with uv and the correct folder structure, so that all subsequent stories have a consistent foundation to build on.

#### Acceptance Criteria

- `uv init` with Python 3.12+ constraint in `pyproject.toml`
- Directory structure: `archon/chat/`, `archon/ai/`, `archon/gateway/`, `archon/config/`
- Each module has an `__init__.py`
- `.env.example` with `TELEGRAM_BOT_TOKEN=`
- `config.toml.example` with all supported keys and comments
- `.gitignore` excludes `.env`, `*.log`, `__pycache__`
- `README.md` with quickstart (install, configure, run)
- `pytest` configured and a passing smoke test exists

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)
- [Component Catalog](../Architecture/110_component_catalog_and_layer_breakdown.md)

---

### S0.2: Config loader

**Status**: Completed ✅
**Priority**: High
**Estimated effort**: S

**User Story**: As a developer, I want a typed config object loaded from `.env` and `config.toml` at startup, so that all modules can access configuration without reading files themselves.

#### Acceptance Criteria

- Loads `TELEGRAM_BOT_TOKEN` from `.env`
- Loads all `config.toml` keys into a typed dataclass/Pydantic model
- Raises a clear `ConfigError` on startup if required fields are missing
- Config is a singleton, importable as `from archon.config import config`
- Tests: missing token raises error, missing config file raises error, valid config loads correctly

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)
