**Purpose**: Completed stories for Epic 9 — runtime model selection via /model command
**Audience**: All developers
**Status**: Completed
**Last reviewed**: 2026-02-26
**Next review**: 2027-02-26

# Epic 9: Model Management

## Stories

### S9.1: Model selector (/model command)

**Status**: Completed ✅
**Priority**: Medium
**Estimated effort**: M

**User Story**: As a whitelisted user, I want to switch the Claude model from Telegram via `/model`, so that I can change between models without editing config files or restarting the daemon.

#### Acceptance Criteria

- `ModelsConfig` dataclass in `config/loader.py` with `available: list[str]` and `default: str | None`; parsed from `[models]` in `config.toml`
- `/model` (no args) shows current active model with an inline keyboard (one button per `available` model; active model marked with ` ✓`)
- Tapping a model button calls `session_manager.set_model(model)`, edits the keyboard message in-place, answers the callback query
- Sessions started after the switch use the new model (`SessionManager.set_model` stores the value; new `ClaudeSession` instances pick it up via the factory)
- `/model <name>` text subcommand also accepted
- `BOT_COMMANDS` entry for `/model` with description `"Show or switch the Claude model"`
- Tests: `/model` handler sends reply with `InlineKeyboardMarkup`, `model_callback` with valid model switches and edits, `model_callback` with unknown model is ignored gracefully, `session_manager.get_model()` returns updated value

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)
- [Component Catalog](../Architecture/110_component_catalog_and_layer_breakdown.md)
