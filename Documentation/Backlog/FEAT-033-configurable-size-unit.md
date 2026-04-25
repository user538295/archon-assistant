# FEAT-033 — Configurable Size Unit for Context Injection Display

**Purpose**: Allow users to configure a preferred size unit (`chars`, `codepoints`, `words`, `tokens`, `lines`, `sentences`) so that context injection events display sizes in a unit meaningful for reasoning about LLM context budget.
**Audience**: Power users monitoring context window usage in verbose/debug notification mode.
**Status**: To Do

---

## Background

When Archon injects context (history, skills) into the session, it reports sizes in characters — a unit that has no direct meaning for users thinking about LLM context windows. A user seeing `(1240 chars)` has to mentally convert to tokens to understand impact. Adding a `size_unit` config key lets users switch to `tokens` (or other units) so the display becomes directly actionable.

Currently, `ContextInjectedEvent.size_chars` and `SkillInjectedEvent.size_chars` are stored as raw `len(text)` and rendered literally in both `telegram_formatter.py` and `event_renderer.py`. The size conversion will be computed at event creation time and stored as a pre-formatted string in `size_display`.

---

## Goal

Users set `[output] size_unit = "tokens"` in `config.toml`. All injection event displays — Telegram messages and history markdown — use that unit consistently. The token approximation via `tiktoken` (`cl100k_base`) is accurate enough for context budget awareness and fast enough for a synchronous display path. Changing the unit requires no session restart — since the unit is read from the config singleton at event creation time, a config reload that updates `config.output.size_unit` will take effect on the next injection event.

---

## Scope

### In Scope
- New `size_unit` field on `OutputConfig`: `"chars"` (default) | `"codepoints"` | `"words"` | `"tokens"` | `"lines"` | `"sentences"`
- A `SizeFormatter` utility: pure function `format(text: str, unit: str) -> str` returning `"N unit"` string
- Token counting via `tiktoken` `cl100k_base`, lazy-imported only when `size_unit = "tokens"`
- Sentence counting via `re.split(r'(?<=[.!?])\s+', text)`
- Config validation: unknown `size_unit` raises `ConfigError` at startup
- `tiktoken` unavailability when `size_unit = "tokens"` raises `ConfigError` at startup (no silent fallback)
- Both display surfaces updated: `telegram_formatter.py` and `event_renderer.py`
- `tiktoken` added as optional dependency in `pyproject.toml` (extras: `[tokens]`)
- `ContextInjectedEvent.size_chars` and `SkillInjectedEvent.size_chars` fields: renamed to `size_display: str` — a pre-formatted string computed at event creation time (e.g., `"310 tokens"` or `"1240 chars"`)

### Out of Scope
- `ReminderInjectedEvent` — has no size field; not affected
- Tool result size display (`format_tool_result_size` in `tool_result_policy.py`) — uses bytes, separate concern
- Per-surface unit overrides (different unit for Telegram vs history)
- Anthropic `client.count_tokens()` API — async, adds latency to the synchronous display path
- `nltk` sentence tokenizer

---

## Acceptance criteria
- [ ] `[output] size_unit = "tokens"` in `config.toml` causes Telegram to show `📌 Context injected [history] (310 tokens)` instead of `(1240 chars)` — the display comes from `event.size_display` computed at creation time
- [ ] History markdown likewise shows `310 tokens`
- [ ] All 6 units (`chars`, `codepoints`, `words`, `tokens`, `lines`, `sentences`) produce correct output for ASCII, Unicode, and empty strings
- [ ] Unknown `size_unit` value raises `ConfigError` at startup with a descriptive message
- [ ] `tiktoken` is not imported at startup when `size_unit != "tokens"`
- [ ] `tiktoken` unavailability with `size_unit = "tokens"` raises `ConfigError` at startup
- [ ] `archon config set output.size_unit tokens` sets the value correctly
- [ ] `uv run pytest` passes with ≥85% coverage

---

## What does NOT change
- `ContextInjectedEvent` and `SkillInjectedEvent` rendering logic in display surfaces — they now simply read `event.size_display` (a pre-formatted string computed at creation time by calling `format_size(text, config.output.size_unit)`)
- `ReminderInjectedEvent` rendering
- `TruncationStrategy` and other `[output]` config fields
- Tool result size display in `tool_result_policy.py`
- All other event rendering in `telegram_formatter.py` and `event_renderer.py`

---

## Known limitations / accepted trade-offs
- `cl100k_base` is OpenAI's tokenizer (used by GPT-3.5/GPT-4), not Claude's tokenizer. Claude's tokenizer is not publicly available. The "5–15% off" figure is an estimate — actual divergence for Claude models is undocumented and may be higher for non-English or code-heavy content. Users should treat token counts as rough approximations for context budget awareness.
- Sentence counting via simple regex is imprecise on abbreviations (e.g., `"Dr. Smith went home."` counts as 2 sentences) — acceptable imprecision, documented by test.
- The `codepoints` unit counts Unicode codepoints (same as Python `len(text)`), which may differ from visible grapheme clusters for ZWJ sequences (e.g., `"👨‍👩‍👧‍👦"` is 7 codepoints but 1 visual glyph). This is deliberate and documented.
- Field rename from `size_chars: int` to `size_display: str` is a breaking change to the internal dataclass; no migration needed since events are not persisted.
- `chars` and `codepoints` currently produce identical numeric values — they differ only in label. This is intentional.
- All unit labels use plural form regardless of count (`"1 chars"`, `"1 lines"`) — deliberate simplification; no singular form handling.

---

## Architecture

### New module
- **`archon/ai/size_formatter.py`** — `SizeFormatter` utility
  - `VALID_SIZE_UNITS: frozenset[str]` — set of allowed unit strings
  - `format_size(text: str, unit: str) -> str` — pure function, no config dependency, no side effects; lazy-imports `tiktoken` on first call when `unit == "tokens"` (module-level `_tiktoken_enc` cache)
  - Helper: `_count_tokens(text: str) -> int` (lazy tiktoken), `_count_sentences(text: str) -> int` (regex)
  - Note: the `codepoints` unit uses `f"{len(text)} codepoints"` — Python `len()` counts Unicode codepoints (same as `len(text)` for all Unicode, not just BMP). This differs from visible grapheme clusters for ZWJ emoji sequences.

### Config changes
- `archon/config/loader.py`: add `size_unit: str = "chars"` to `OutputConfig`; add validation: `size_unit not in VALID_SIZE_UNITS → ConfigError` (using a local set literal, no import from `ai/`); add startup check: if `size_unit == "tokens"` and `tiktoken` unavailable → `ConfigError`

### Event dataclass changes
- `archon/ai/event_mapper.py`: rename `ContextInjectedEvent.size_chars: int` → `size_display: str` and `SkillInjectedEvent.size_chars: int` → `size_display: str`
- Construction sites call `format_size(text, config.output.size_unit)` at event creation and store the result in `size_display` (e.g., `"310 tokens"` or `"1240 chars"`)
- No `raw_text` field is added — the text is consumed at creation time, not stored on the event

### Display surface changes
- `archon/chat/telegram_formatter.py`: replace `event.size_chars` reference with `event.size_display` — no `config` or `format_size` import needed in the formatter; no `_render_size` helper needed
- `archon/ai/event_renderer.py`: same — replace `event.size_chars` reference with `event.size_display`

### Data flow
```
text → format_size(text, config.output.size_unit) → ContextInjectedEvent.size_display
                                                   → SkillInjectedEvent.size_display
```

**Decision**: Compute `size_display: str` at event creation time. Display surfaces (`telegram_formatter.py`, `event_renderer.py`) read `event.size_display` directly — no helper, no fallback, no config access at render time. Since `config.output.size_unit` is read from the config singleton at creation time, a config reload automatically takes effect on the next injection event.

### New config key
- `[output] size_unit` — `str`, default `"chars"`, valid values: `"chars" | "codepoints" | "words" | "tokens" | "lines" | "sentences"`

### Dependency change
- `pyproject.toml`: add `tiktoken` as optional dependency under `[project.optional-dependencies] tokens = ["tiktoken"]`; recommend installing with `uv add tiktoken` for `tokens` unit users

---

## Tests

**`tests/ai/test_size_formatter.py`**:
- **`test_format_size_chars`** (unit): `chars` unit returns `len(text)` as `"N chars"`
- **`test_format_size_codepoints_ascii`** (unit): ASCII codepoints == chars
- **`test_format_size_codepoints_unicode`** (unit): `format_size("caf\u00e9", "codepoints")` returns `"4 codepoints"` — uses NFC precomposed form U+00E9 (4 codepoints). The decomposed form `"cafe\u0301"` would return `"5 codepoints"`. Tests use precomposed form consistently.
- **`test_format_size_words`** (unit): whitespace-split word count
- **`test_format_size_lines`** (unit): newline-split line count
- **`test_format_size_sentences`** (unit): regex sentence count
- **`test_format_size_tokens`** (unit): tiktoken token count (mocked)
- **`test_format_size_empty_text_all_units`** (unit): empty string returns `"0 <unit>"` for all 6 units
- **`test_format_size_unknown_unit`** (unit): raises `ValueError` for unknown unit
- **`test_tiktoken_lazy_import`** (unit): `tiktoken` not imported when unit is `chars`
- **`test_format_size_sentences_abbreviation`** (unit): `format_size("Dr. Smith went home.", "sentences")` returns `"2 sentences"` — documents that abbreviations are false-positive split points (deliberate, acceptable imprecision)
- **`test_format_size_lines_trailing_newline`** (unit): `format_size("a\nb\n", "lines")` returns `"2 lines"` — trailing newline does not add a phantom line (`splitlines()` behavior)
- **`test_format_size_codepoints_zwj`** (unit): `format_size("👨‍👩‍👧‍👦", "codepoints")` returns `"7 codepoints"` — documents that ZWJ emoji count codepoints, not grapheme clusters (deliberate, documented limitation)

**`tests/config/test_loader.py`**:
- **`test_config_valid_size_unit`** (unit): all 6 values accepted by loader
- **`test_config_invalid_size_unit`** (unit): unknown value raises `ConfigError`
- **`test_config_tokens_unit_tiktoken_unavailable`** (unit): `tiktoken` import failure raises `ConfigError` at load time

**`tests/ai/test_claude_session.py`**:
- **`test_claude_session_context_injected_uses_format_size`** (unit): patch `config.output.size_unit` to `"words"`, call `session.send()`, capture the yielded `ContextInjectedEvent`; assert `event.size_display` ends with `"words"`
- **`test_size_unit_config_reload_takes_effect`** (integration): 1. start with `size_unit="chars"`, capture injection event, assert `size_display` ends with `"chars"`; 2. patch config singleton to `size_unit="words"`, trigger another injection event, assert `size_display` ends with `"words"` — verifies the construction site reads the config singleton at creation time, not cached at session start. Belongs here (not in `test_loader.py`) because it tests `claude_session.py`'s wiring.

**`tests/ai/test_event_mapper.py`**:
- **`test_context_injected_event_has_size_display`** (unit): `ContextInjectedEvent` has `size_display: str` field; `size_chars` does NOT exist (regression guard)
- **`test_skill_injected_event_has_size_display`** (unit): `SkillInjectedEvent` has `size_display: str` field; `size_chars` does NOT exist (regression guard)

**`tests/chat/test_telegram_formatter.py`**:
- **`test_telegram_formatter_context_injected_chars_unit`** (integration): event with `size_display="5 chars"` produces label `(5 chars)`
- **`test_telegram_formatter_context_injected_tokens_unit`** (integration): event with `size_display="2 tokens"` produces label `(2 tokens)`
- **`test_telegram_formatter_skill_injected_chars_unit`** (integration): `SkillInjectedEvent` renders `size_display` correctly
- **`test_telegram_formatter_skill_injected_tokens_unit`** (integration): `SkillInjectedEvent` renders `size_display` correctly

**`tests/ai/test_event_renderer.py`**:
- **`test_event_renderer_context_injected_tokens_unit`** (integration): history renderer outputs `event.size_display` verbatim
- **`test_event_renderer_skill_injected_tokens_unit`** (integration): history renderer outputs `event.size_display` verbatim

**`tests/cli/test_config_cmd.py`** (or integration test):
- **`test_cli_config_set_size_unit`** (integration): `archon config set output.size_unit tokens` updates the config file correctly; reading back shows `size_unit = "tokens"`

---

## Documentation update
- [ ] `CLAUDE.md`, section `Output event model`, update `ContextInjectedEvent` and `SkillInjectedEvent` row descriptions to reflect configurable unit
- [ ] `CLAUDE.md`, section `Configuration`, add `size_unit` to `[output]` key list
- [ ] `examples/config.toml.example`, section `[output]`, add `size_unit` with comment explaining valid values and default
- [ ] `Documentation/Architecture/110_component_catalog_and_layer_breakdown.md` — update `ContextInjectedEvent` and `SkillInjectedEvent` field references from `size_chars` to `size_display: str` (lines ~257-258)

---

## Task breakdown

### Phase 1 — Core utility and config
> **Releasable**: after Task 1.3 — `SizeFormatter` is usable in isolation and config accepts the new field; display surfaces not yet updated

#### Task 1.1 — `SizeFormatter` utility
- [x] **File**: `archon/ai/size_formatter.py`
- **Depends on**: nothing
- **Description**:
  - `VALID_SIZE_UNITS: frozenset[str] = frozenset({"chars", "codepoints", "words", "tokens", "lines", "sentences"})`
  - `format_size(text: str, unit: str) -> str`
    - `"chars"`: `f"{len(text)} chars"`
    - `"codepoints"`: `f"{len(text)} codepoints"` — counts Unicode codepoints (same as Python `len(text)`, works for all Unicode including non-BMP; differs from visible grapheme clusters for ZWJ sequences)
      Note: `codepoints` and `chars` produce the same numeric value for all Python str input (Python str is UTF-32 internally; `len()` counts codepoints in both cases). The units differ only in the label string. `chars` is recommended for general use; `codepoints` is provided for users who want to be explicit about Unicode codepoint semantics.
    - `"words"`: `f"{len(text.split())} words"` (empty string → `0 words`)
    - `"lines"`: `f"{len(text.splitlines()) or (0 if not text else 1)} lines"` — empty → 0, non-empty no-newline text → 1
    - `"sentences"`: regex `re.split(r'(?<=[.!?])\s+', text.strip())`, filter empty → count
    - `"tokens"`: lazy-import `tiktoken`; module-level `_tiktoken_enc: tiktoken.Encoding | None = None`; `_get_enc()` initializes on first call with `tiktoken.get_encoding("cl100k_base")`; count via `len(enc.encode(text))`
    - Unknown unit: raise `ValueError(f"Unknown size_unit: {unit!r}. Valid: {', '.join(sorted(VALID_SIZE_UNITS))}")`
    - Empty text fast-path: `if not text: return f"0 {unit}"`
  - **Pluralization**: All units use plural form unconditionally (e.g., `"1 chars"`, `"1 lines"`, `"1 sentences"`). This is intentional — pluralization logic adds complexity for a display-only feature; users monitoring context budget sizes will rarely see single-unit values.
  - `_count_tokens(text: str) -> int` — private helper, exposed for testing
  - `_count_sentences(text: str) -> int` — private helper, exposed for testing
- **Releasable**: `format_size()` callable from anywhere; no wiring yet
- **Tests (TDD)** — `tests/ai/test_size_formatter.py`:
  - Unit: `test_format_size_chars` — `format_size("hello world", "chars")` returns `"11 chars"`
  - Unit: `test_format_size_codepoints_ascii` — ASCII codepoints equal chars
  - Unit: `test_format_size_codepoints_unicode` — `format_size("caf\u00e9", "codepoints")` returns `"4 codepoints"` — uses NFC precomposed form U+00E9 (4 codepoints); the decomposed form `"cafe\u0301"` would return `"5 codepoints"`; tests use precomposed form consistently
  - Unit: `test_format_size_words` — `"foo bar baz"` returns `"3 words"`
  - Unit: `test_format_size_lines_single` — `"hello"` returns `"1 lines"` (plural form always used — no singular form handling)
  - Unit: `test_format_size_lines_multi` — `"a\nb\nc"` returns `"3 lines"`
  - Unit: `test_format_size_sentences` — `"Hello. World!"` returns `"2 sentences"`
  - Unit: `test_format_size_sentences_abbreviation` — `"Dr. Smith went home."` returns `"2 sentences"` (deliberate abbreviation imprecision)
  - Unit: `test_format_size_lines_trailing_newline` — `"a\nb\n"` returns `"2 lines"` (`splitlines()` strips trailing newline)
  - Unit: `test_format_size_codepoints_zwj` — `"👨‍👩‍👧‍👦"` returns `"7 codepoints"` (ZWJ sequence: 7 codepoints, 1 visible glyph)
  - Unit: `test_format_size_tokens` — mock `tiktoken.get_encoding` to return encoder producing 3 tokens; `format_size("abc", "tokens")` returns `"3 tokens"`
  - Unit: `test_format_size_empty_all_units` — parametrize all 6 units; each returns `"0 <unit>"`
  - Unit: `test_format_size_unknown_unit` — raises `ValueError` with descriptive message
  - Unit: `test_tiktoken_not_imported_for_chars` — after calling `format_size("x", "chars")`, assert `"tiktoken"` not in `sys.modules` (reset `sys.modules` in fixture)
  - Checkpoint: `uv run pytest tests/ai/test_size_formatter.py -v`

#### Task 1.2 — Rename `size_chars` → `size_display: str` on event dataclasses
- [x] **Files**: `archon/ai/event_mapper.py`, `archon/ai/claude_session.py`, and all test files listed below
- **Depends on**: Task 1.1 (needs `format_size`)
- **Description**:
  - `ContextInjectedEvent`: rename field `size_chars: int` → `size_display: str`
  - `SkillInjectedEvent`: rename field `size_chars: int` → `size_display: str`
  - Do NOT add `raw_text` field — the text is consumed at creation time, not stored on the event
  - Update all construction sites (e.g., `archon/ai/claude_session.py`) to call `format_size(text, config.output.size_unit)` and pass the result as `size_display=`

  #### Existing files requiring migration (grep for `size_chars`):
  - `tests/ai/test_event_mapper.py` — update all `size_chars=` kwargs to `size_display=`; replace int values with formatted strings (e.g., `size_chars=5` → `size_display="5 chars"`)
  - `tests/ai/test_event_renderer.py` — update event constructions and any `.size_chars` assertions
  - `tests/ai/test_claude_session.py` — update ~8 assertions that reference `.size_chars`; these should now assert `.size_display`
  - `tests/ai/test_pipeline.py` — update 1 event construction at the `size_chars=` call site
  - `tests/chat/test_handler.py` — update ~27 event constructions with `size_chars=` kwargs
  - Run `grep -rn "size_chars" tests/` to find any additional sites

  The mechanical transformation is: `size_chars=N` → `size_display=format_size(text, "chars")` or equivalently `size_display=f"{N} chars"` for tests that don't have the original text.

- **Releasable**: after this task, `size_display: str` field exists on both events, computed at creation time
- **Tests (TDD)** — `tests/ai/test_event_mapper.py` (existing file, add cases):
  - Unit: `test_context_injected_event_has_size_display` — construct with `size_display="5 chars"`; assert field exists and equals `"5 chars"`
  - Unit: `test_context_injected_event_no_size_chars` — assert `hasattr(event, "size_chars")` is `False` (regression guard against old field name)
  - Unit: `test_skill_injected_event_has_size_display` — construct with `size_display="10 words"`; same pattern
  - Unit: `test_skill_injected_event_no_size_chars` — same regression guard
  - Unit: `test_claude_session_context_injected_uses_format_size` — patch `config.output.size_unit` to `"words"`, call `session.send()`, capture the yielded `ContextInjectedEvent`; assert `event.size_display` ends with `"words"` (verifies the construction site reads config and calls `format_size`)
  - Checkpoint: `uv run pytest tests/ai/test_event_mapper.py tests/ai/test_claude_session.py -v`

#### Task 1.3 — `size_unit` field and validation in `OutputConfig`
- [ ] **File**: `archon/config/loader.py`
- **Depends on**: nothing (parallel with Task 1.1; validation uses a local set literal, not an import)
- **Description**:
  - Add `size_unit: str = "chars"` to `OutputConfig` dataclass (after `tail_chars`)
  - In `_parse_output_config()` (or the loader function at line ~511), read `output_data.get("size_unit", OutputConfig.size_unit)`
  - Validation block (after existing truncation strategy check); use a local set literal — do NOT import from `archon.ai.size_formatter` (config layer must not depend on ai layer):
    ```python
    VALID_SIZE_UNITS = {"chars", "codepoints", "words", "tokens", "lines", "sentences"}
    if output.size_unit not in VALID_SIZE_UNITS:
        raise ConfigError(
            f"Invalid size_unit: {output.size_unit!r}. "
            f"Must be one of: {', '.join(sorted(VALID_SIZE_UNITS))}"
        )
    if output.size_unit == "tokens":
        try:
            import tiktoken  # noqa: F401
        except ImportError:
            raise ConfigError(
                "size_unit = 'tokens' requires the tiktoken package. "
                "Install it with: uv add tiktoken"
            )
    ```
  - Also update the `OutputConfig` reconstruction block (~line 522) to include `size_unit=output.size_unit`
  - Consider using `Literal["chars", "codepoints", "words", "tokens", "lines", "sentences"]` instead of `str` for `size_unit` in `OutputConfig` to enable mypy static analysis of the field value. Runtime validation still applies; the `Literal` type provides IDE and type-checker support.
- **Releasable**: after this task, `config.output.size_unit` is a validated field available to display code
- **Tests (TDD)** — `tests/config/test_loader.py` (existing file, add cases):
  - Unit: `test_output_config_default_size_unit` — no `size_unit` in toml → `config.output.size_unit == "chars"`
  - Unit: `test_output_config_valid_size_units` — parametrize all 6 valid values; each parses without error
  - Unit: `test_output_config_invalid_size_unit` — unknown string raises `ConfigError` with message including the bad value
  - Unit: `test_output_config_tokens_tiktoken_missing` — patch `builtins.__import__` to raise `ImportError` for `tiktoken`; `size_unit = "tokens"` raises `ConfigError`
  - Unit: `test_valid_size_units_in_sync` — import `VALID_SIZE_UNITS` from `archon.ai.size_formatter`; assert it equals the expected set known to also be used in `loader.py`: `{"chars", "codepoints", "words", "tokens", "lines", "sentences"}`. Prevents silent drift when adding new units. Implementation:
    ```python
    from archon.ai.size_formatter import VALID_SIZE_UNITS
    # Matches the local set literal in loader.py
    _LOADER_VALID_UNITS = {"chars", "codepoints", "words", "tokens", "lines", "sentences"}
    assert VALID_SIZE_UNITS == _LOADER_VALID_UNITS
    ```
  - Checkpoint: `uv run pytest tests/config/test_loader.py tests/ai/test_size_formatter.py::test_valid_size_units_in_sync -v`

- **Test in `tests/ai/test_claude_session.py`** (integration — wiring test for config hot-reload):
  - Integration: `test_size_unit_config_reload_takes_effect`:
    1. Start a session with `size_unit = "chars"`
    2. Capture an injection event, assert `size_display` ends with `"chars"`
    3. Patch the config singleton to change `size_unit = "words"`
    4. Trigger another injection event, assert `size_display` ends with `"words"`

    This test belongs in `test_claude_session.py` (not `test_loader.py`) because it tests construction-site wiring — that the session reads from the config singleton at event creation time, not cached at session start.

---

### Phase 2 — Display surface wiring
> **Releasable**: after Task 2.2 — both Telegram and history markdown show the configured unit in injection event labels

#### Task 2.1 — Update `telegram_formatter.py` to use `event.size_display`
- [ ] **File**: `archon/chat/telegram_formatter.py`
- **Depends on**: Task 1.2
- **Description**:
  - In the `ContextInjectedEvent` branch, replace `event.size_chars` with `event.size_display`:
    ```python
    # before
    label = f"📌 Context injected [{html.escape(event.injection_type)}] ({event.size_chars} chars)"
    # after
    label = f"📌 Context injected [{html.escape(event.injection_type)}] ({event.size_display})"
    ```
  - In the `SkillInjectedEvent` branch, replace `event.size_chars` with `event.size_display` similarly
  - No `config` parameter change needed — `format_event` does not need to know about `size_unit`; `size_display` is already formatted
  - No `format_size` import needed in this file
  - No `_render_size` helper needed
- **Releasable**: Telegram injection event labels display the pre-computed unit string
- **Tests (TDD)** — `tests/chat/test_telegram_formatter.py` (existing file, add cases):
  - Integration: `test_context_injected_chars_unit` — event with `size_display="5 chars"`; `format_event` output contains `"(5 chars)"`
  - Integration: `test_context_injected_tokens_unit` — event with `size_display="2 tokens"`; output contains `"(2 tokens)"`
  - Integration: `test_skill_injected_chars_unit` — `SkillInjectedEvent` with `size_display="3 chars"`; output correct
  - Integration: `test_skill_injected_tokens_unit` — `SkillInjectedEvent` with `size_display="1 tokens"`; output correct
  - Checkpoint: `uv run pytest tests/chat/test_telegram_formatter.py -v`

#### Task 2.2 — Update `event_renderer.py` to use `event.size_display`
- [ ] **File**: `archon/ai/event_renderer.py`
- **Depends on**: Task 1.2
- **Description**:
  - In `render(event)`:
    - `ContextInjectedEvent` branch: replace `f"{event.size_chars} chars"` with `event.size_display`
    - `SkillInjectedEvent` branch: same replacement
  - No `self._size_unit` needed on `EventRenderer` — `size_display` is already formatted at event creation time
  - No `_render_size` helper needed
  - No constructor signature change needed
- **Releasable**: after this task, history markdown injection records display the pre-computed unit string; full feature is complete
- **Tests (TDD)** — `tests/ai/test_event_renderer.py` (existing file, add cases):
  - Integration: `test_render_context_injected_chars_unit` — event with `size_display="5 chars"`; rendered markdown contains `"5 chars"`
  - Integration: `test_render_context_injected_tokens_unit` — event with `size_display="2 tokens"`; rendered markdown contains `"2 tokens"`
  - Integration: `test_render_skill_injected_tokens_unit` — `SkillInjectedEvent` with `size_display="1 tokens"`; rendered markdown correct
  - Checkpoint: `uv run pytest tests/ai/test_event_renderer.py -v`

---

### Phase 3 — Documentation and dependency
> **Releasable**: after this phase — feature is fully documented and `tiktoken` is installable as an optional dep

#### Task 3.1 — Add `tiktoken` as optional dependency
- [ ] **File**: `pyproject.toml`
- **Depends on**: nothing (parallel with other tasks)
- **Description**:
  - Add to `[project.optional-dependencies]`:
    ```toml
    tokens = ["tiktoken>=0.7"]
    ```
  - Do NOT add to `[project.dependencies]` — `tiktoken` is only needed for `size_unit = "tokens"`; the startup `ConfigError` guides users to install it
  - No changes to `uv.lock` are needed at plan time; `uv add --optional tokens tiktoken` is the install command
- **Releasable**: users can install `tiktoken` support with `uv sync --extra tokens`
- **Tests (TDD)**: no new tests; existing `test_output_config_tokens_tiktoken_missing` covers the error path
  - Checkpoint: `uv run pytest tests/config/test_loader.py::test_output_config_tokens_tiktoken_missing -v`

#### Task 3.2 — Update `config.toml.example` and CLAUDE.md
- [ ] **Files**: `examples/config.toml.example`, `CLAUDE.md`
- **Depends on**: Task 1.3
- **Description**:
  - `examples/config.toml.example`, `[output]` section: add after `truncation_strategy`:
    ```toml
    # Size unit for context injection event display (chars/codepoints/words/tokens/lines/sentences)
    # "tokens" requires: uv add tiktoken
    # size_unit = "chars"
    ```
  - `CLAUDE.md`, `Output event model` table: update `ContextInjectedEvent` row to `(N <unit>)` and note `size_unit` config key
  - `CLAUDE.md`, `Configuration` section, `[output]` key list: add `size_unit` with valid values and default
- **Releasable**: documentation is accurate and complete for the feature
- **Tests (TDD)**: none (documentation task)
  - Checkpoint: `uv run pytest -q --tb=no` (full suite sanity check)

#### Task 3.3 — CLI config integration test
- [ ] **File**: `tests/cli/test_config_cmd.py` (existing file, add case)
- **Depends on**: Task 1.3, Task 3.2
- **Description**:
  - Verify that `archon config set output.size_unit tokens` correctly writes the value to the config file and that reading it back via `archon config get output.size_unit` returns `tokens`
- **Tests (TDD)**:
  - Integration: `test_cli_config_set_size_unit` — invoke the `config set` command with `output.size_unit tokens`; assert the config file contains `size_unit = "tokens"`; invoke `config get output.size_unit`; assert output is `tokens`
  - Checkpoint: `uv run pytest tests/cli/test_config_cmd.py::test_cli_config_set_size_unit -v`
