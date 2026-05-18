# Feature Brief: Model Registry with Context Windows

## Problem
`AVAILABLE_MODELS` (a list) and `MODEL_CONTEXT_WINDOWS` (a dict) are two parallel structures that can silently desync. Today, 5 of 9 models in `AVAILABLE_MODELS` have no entry in `MODEL_CONTEXT_WINDOWS`, and `claude-opus-4-6` is incorrectly set to 200k instead of 1M.

## Goal
A single `AVAILABLE_MODELS: dict[str, int]` that is the canonical source of truth for both which models exist and their context window sizes — kept automatically accurate by the release script.

## Users & Context
Developers maintaining the project and users running `archon` who select models via `/models`. The broken context window on Opus silently gives wrong stats to anyone using it.

## Core Flow

1. `constants.py` defines `AVAILABLE_MODELS: dict[str, int]` mapping each model ID to its context window size.
2. `MODEL_CONTEXT_WINDOWS` is removed entirely — `AVAILABLE_MODELS` replaces it.
3. `get_context_window()` looks up the model in `AVAILABLE_MODELS`, with config `available` dict as an override layer on top.
4. `config.toml` `available` changes from a list of strings to a dict (`"model-id" = context_window`), allowing users to declare exactly which models they enable and with what context window.
5. `ModelsConfig.available` changes from `list[str]` to `dict[str, int]`; `ModelsConfig.context_windows` is removed entirely.
6. The `[models.context_windows]` config section is removed — users add custom/proxy models directly to `[models.available]` instead.
7. At release time, `update_models.py` fetches the Anthropic models endpoint, extracts both model IDs and `context_window` values, and updates the `AVAILABLE_MODELS` dict in `constants.py` and the `available` table in `config.toml.example`.

## In Scope

- Merge `AVAILABLE_MODELS` (list) + `MODEL_CONTEXT_WINDOWS` (dict) into one `AVAILABLE_MODELS: dict[str, int]` in `constants.py`
- Fix incorrect context window values (e.g. `claude-opus-4-6` → 1_000_000)
- Change `config.toml` / `config.toml.example` `available` from `list` to inline dict
- Change `ModelsConfig.available` from `list[str]` to `dict[str, int]`
- Remove `ModelsConfig.context_windows` and `[models.context_windows]` config section
- Update config loader validation accordingly
- Update `get_context_window()` to use `AVAILABLE_MODELS` as the base and `cfg.models.available` as the override layer
- Update `update_models.py` to fetch and sync context window sizes from the Anthropic API
- Update `config.toml.example` to reflect new format
- Update all tests

## Out of Scope

- Changing the `/models` UI or Telegram command behaviour — model selection logic stays the same
- Adding per-model rate limits, pricing, or other metadata — only context window size is in scope
- Validating that a user-configured context window matches the real API value — trust the user

## Key Decisions

- **Merge into `AVAILABLE_MODELS` dict instead of keeping separate structures**: eliminates desync by making it impossible to add a model without a context window.
- **Config `available` becomes a dict (no backward compat)**: consistent type with constants; no real users to migrate.
- **Remove `[models.context_windows]` config section**: made redundant — custom/proxy models are added directly to `available` with their context window. Simpler config surface.
- **`update_models.py` syncs context windows from the API**: the Anthropic models endpoint returns `context_window` per model; the previous manual-only approach and the comment claiming the API doesn't expose this were both wrong.

## Edge Cases & Constraints

- **Model in `available` config but not in `AVAILABLE_MODELS` constants** (custom/proxy): value from config is used — this is the intended path for proxy models.
- **Model in both constants and config `available`**: config value wins (same override semantics as before).
- **`DEFAULT_FAST_MODEL` not in `AVAILABLE_MODELS`** (e.g. `claude-haiku-4-5` alias vs `claude-haiku-4-5-20251001`): `get_context_window()` must fall back to 200k gracefully — no change needed here.
- **`update_models.py` receives a model with no `context_window` field from the API**: script must handle gracefully (skip or use 200k default) rather than crash.
- **All existing tests** for `test_constants.py`, `test_config_loader.py`, `test_update_models.py`, `test_gateway.py`, `test_session_manager.py`, `test_claude_session.py` need updating.

## Open Questions

- None — scope is fully defined.

## Future Iterations

- Sync other per-model metadata (max output tokens, pricing) using the same registry pattern.
- Warn the user in `/status` or `archon doctor` if their configured context window is significantly different from the canonical value.

## Recommendation

This is a clean, high-value fix that closes a real silent-bug vector with minimal risk. The hardest part is the `update_models.py` change — verifying the Anthropic API actually returns `context_window` in the models list response and handling any model that doesn't. Do not compromise on removing `[models.context_windows]`; keeping it alongside the new `available` dict would recreate the two-structure problem in a different form.
