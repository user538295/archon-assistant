# FEAT-036 — Classifier Context Injection & Reliability Fix
**Purpose**: Make the classifier reliably produce valid JSON on every call, handle ambiguous follow-up messages correctly, and never call external tools.
**Audience**: Internal — Archon maintainers
**Status**: To Do

---

## Background

The intent classifier (Haiku) fails silently on short follow-up messages ("continue", "do that").
Two root causes are confirmed:

1. **Tool budget exhaustion**: `_create_session()` forwards `search_url` to `ClaudeSession`, giving
   Haiku access to Search MCP tools. On ambiguous messages it calls those tools, exhausts its
   `max_turns=1` budget, and never outputs JSON — silently falling back to `(task, 0.0)`.

2. **Prompt contradiction**: The system prompt contained "You have only 5 steps … MUST start with
   thinking and plan the remaining 4 steps" — this planning instruction triggered multi-step
   behaviour in a `max_turns=1` session, racing with the JSON output requirement.

3. **Missing context**: The classifier sees only the current message, with no knowledge of the prior
   conversation, so "continue" or "do that" is genuinely unclassifiable without context.

**Note**: `parse_classification()` in `classification.py` already handles markdown code-fence
stripping via `extract_json_object()` (added in a prior fix). The live test `xfail` markers that
were added for the parse-failure bug (BUG-2) can therefore now be removed.

---

## Goal

After this feature: the classifier always produces a valid, calibrated JSON classification.
Ambiguous follow-up messages are routed correctly because the classifier receives the last 5 user
messages from today's session as labeled context. The MCP tools are hard-disabled (not
prompt-instructed) so exhaustion cannot occur. `xfail` markers on parse-failure tests are gone.

---

## Scope

### In Scope
- Remove `search_url` forwarding in `Classifier._create_session()` — hard MCP disable
- Update `prompts/classifier.md`: remove contradictory "5 steps" planning line; add one-line
  fallback rule for ambiguous messages
- Add `recent_context: list[str] | None` parameter to `Classifier.classify()`
- Module-level `_read_recent_user_messages()` helper in `pipeline.py`
- Wire context reading and injection into `Pipeline.send()`
- Plumb `history_dir` through `Gateway → SessionManager → Pipeline`
- Remove BUG-2 `xfail` markers from `tests/ai/test_classifier_live.py`; add context-injection
  live tests

### Out of Scope
- AI response text injection (user messages cover 95%+ of ambiguous cases)
- Haiku summarisation of AI responses (extra LLM call per turn)
- Cross-day history (today's session file is sufficient)
- Compacted history files (loses raw message signal)
- Classifier session persistence (remains single-turn, stateless)
- Per-user history scoping for context injection (see Future Iterations for multi-user note)

---

## Acceptance criteria
- [x] Classifier session is created with no `search_url` argument; `Classifier.__init__` no longer accepts `search_url`
- [x] `classifier.md` system prompt contains no "5 steps" planning instruction
- [x] `classifier.md` contains a fallback rule for ambiguous messages referencing the injected context
- [x] `Classifier.classify(prompt, recent_context=[...])` builds a labeled context block prepended to the prompt
- [x] `_read_recent_user_messages()` returns last ≤5 user messages from today's session file, oldest-first, each truncated to 200 chars
- [x] Missing history file (file not found) → `recent_context=None`, no warning logged, classification proceeds
- [x] Unreadable history file (I/O or encoding error) → `recent_context=None`, `logger.warning` called, classification proceeds
- [x] `Pipeline.send()` reads recent context before calling `classify()` and passes it through
- [x] `history_dir` is wired from gateway config → SessionManager → Pipeline; `None` when `cfg.history.enabled` is `false`
- [x] All BUG-2 `xfail` markers removed from live tests
- [x] ≥85% test coverage maintained

---

## What does NOT change
- `parse_classification()` / `extract_json_object()` — already correct, no changes
- Routing algorithm, confidence thresholds, or any downstream Pipeline logic
- `ClaudeSession` interface
- `SessionManager` public API (only `__init__` gains an optional kwarg)

---

## Known limitations / accepted trade-offs
- Only today's history file is read — yesterday's context is never injected; cross-day context
  is rarely needed for intent classification
- If the history file is large, reading it synchronously on every classify call adds I/O latency
  (mitigated by: `asyncio.to_thread()` wraps the sync `_read_recent_user_messages()` call in
  `Pipeline.send()` so the event loop is not blocked; file stays small within a day, only last-5
  messages extracted)
- **Multi-user history not scoped by user**: In multi-user deployments (multiple whitelisted user
  IDs in config), all users write to the same date-keyed session file via `HistoryManager`. The
  `_read_recent_user_messages()` function has no `user_id` parameter and therefore returns
  interleaved messages from all users. This is acceptable for MVP — context injection still improves
  classification in the common single-user case. Per-user scoping is deferred to a future iteration.

---

## Architecture

### New modules / functions
- `_read_recent_user_messages(history_dir: str, today: date | None = None, limit: int = 5) -> list[str]`
  — module-level function in `archon/ai/pipeline.py`. Reads
  `{history_dir}/sessions/{YYYY-MM-DD}.md`, extracts user message text from
  `## HH:MM:SS UTC · User` sections (via regex), returns last `limit` entries oldest-first,
  each truncated to 200 chars. Returns `[]` on file-not-found; logs warning on I/O error.
  `today` defaults to `datetime.now(timezone.utc).date()` (not `date.today()`) so the correct
  UTC date is used — consistent with `HistoryManager._utc_path()`. This avoids a midnight
  mismatch for non-UTC users.

### Modified modules
- `archon/ai/classifier.py` — `_create_session()`: remove `search_url` forwarding; `classify()`:
  add `recent_context` param, build context block; also remove `search_url` parameter from
  `__init__`; update Pipeline's `Classifier(...)` call site to omit `search_url`
- `archon/ai/prompts/classifier.md` — remove "5 steps" line, add fallback rule
- `archon/ai/pipeline.py` — `Pipeline.__init__`: add `history_dir: str | None = None`;
  `Pipeline.send()`: call `_read_recent_user_messages()`, pass result to `classify()`
- `archon/ai/session_manager.py` — add `history_dir: str | None = None` to `SessionManager.__init__`,
  pass it to `Pipeline()`
- `archon/gateway/gateway.py` — pass `history_dir=cfg.history.directory` to `SessionManager()`

### Context block format (injected into classifier prompt)
```
[Recent context — last N user messages, oldest first]
1. <message 1 (≤200 chars)>
2. <message 2 (≤200 chars)>
…

Current message: <prompt>
```

### History file parsing
Pattern: lines matching `^## \d{2}:\d{2}:\d{2} UTC · User` are section headers.
Text content = first non-empty, non-`>`, non-`#` paragraph following the header.

**Extraction caveats (accepted limitations)**:
- Only the first line of a user message is extracted; subsequent lines of multi-line messages are discarded.
- Lines starting with `>` (block-quotes, Archon event output) or `#` (section headings) are skipped — this is intentional to exclude Archon event lines from the injected context.
- User messages that start with `>` or `#` will be skipped entirely and not injected — this is an accepted limitation of the simple line-based extraction.

---

## Tests

- **test_create_session_search_url_is_none** (unit): `ClaudeSession` called with `search_url=None` or kwarg absent
- **test_create_session_does_not_forward_search_url** (unit): `Classifier()` constructed without `search_url`; `_create_session()` calls `ClaudeSession` without any `search_url` kwarg
- **test_classify_with_context_includes_context_block** (unit): prompt sent to session contains labeled context lines, correct N in header, and `Current message:` suffix
- **test_classify_context_header_n_matches_count** (unit): context block header `last N user messages` matches the number of injected messages
- **test_classify_without_context_no_context_block** (unit): `recent_context=None` → no context prefix in prompt
- **test_classify_context_empty_list_no_context_block** (unit): `recent_context=[]` → prompt unchanged, no context block prepended
- **test_classify_context_truncates_long_messages** (unit): messages >200 chars are truncated
- **test_classify_context_oldest_first** (unit): context block lists messages in oldest-first order
- **test_read_recent_messages_happy_path** (unit): correctly extracts user messages from fixture file content
- **test_read_recent_messages_returns_last_n_when_over_limit** (unit): fixture with 7 messages; asserts only the last 5 (oldest-first within those 5) are returned
- **test_read_recent_messages_fewer_than_limit** (unit): returns all messages when fewer than 5 exist
- **test_read_recent_messages_no_file** (unit): returns `[]` when file doesn't exist; assert `logger.warning` was NOT called
- **test_read_recent_messages_io_error** (unit): returns `[]` and logs warning on `OSError`
- **test_read_recent_messages_unicode_error** (unit): returns `[]` and logs warning on `UnicodeDecodeError`
- **test_read_recent_messages_truncates_long_messages** (unit): messages >200 chars are truncated
- **test_read_recent_messages_oldest_first** (unit): returns messages in chronological order (oldest first)
- **test_read_recent_messages_multiline_takes_first_line** (unit): only the first line of a multi-line user message is extracted
- **test_read_recent_messages_skips_archon_and_event_lines** (unit): Archon `> ` lines and `###` event lines interspersed in fixture are excluded; only user text is returned
- **test_pipeline_passes_recent_context_to_classifier** (unit/integration): `Pipeline.send()` calls `classify()` with `recent_context` containing messages from history
- **test_pipeline_context_empty_list_passes_none** (unit): `_read_recent_user_messages` returns `[]`; assert `classify` called with `recent_context=None`
- **test_pipeline_no_history_dir_passes_none** (unit): `Pipeline(history_dir=None)`; assert `classify` called with `recent_context=None` without calling `_read_recent_user_messages`
- **test_pipeline_context_uses_to_thread** (unit): mock `asyncio.to_thread`; assert it was called with `_read_recent_user_messages` as first arg and history dir as second
- **test_pipeline_context_read_error_falls_back** (unit): unexpected exception from `asyncio.to_thread` → `recent_context=None`, classify still called
- **test_session_manager_passes_history_dir_to_pipeline** (integration): `SessionManager(history_dir="/tmp/h")`; `_default_factory()` returns `Pipeline` with `_history_dir == "/tmp/h"`
- **test_classifier_live_task_confidence_above_threshold** (live e2e): remove `xfail`, assert confidence ≥ threshold for unambiguous task messages
- **test_classifier_live_chat_intent** (live e2e): remove `xfail`, assert correct intent for chat messages
- **test_classifier_live_context_injection_disambiguates** (live e2e): "continue" with context classified as `task`; without context may return lower confidence

---

## Documentation update
- [x] `CLAUDE.md`, section `archon/ai/` — update `classifier.py` entry to mention `recent_context` parameter

---

## Task breakdown

### Phase 1 — Hard MCP disable + prompt fix
> **Releasable**: after Task 1.2; the classifier can no longer exhaust its budget on MCP tool calls and the contradictory planning instruction is gone.

#### Task 1.1 — Remove search_url forwarding in Classifier._create_session()
- [x] **File**: `archon/ai/classifier.py`
- **Depends on**: nothing
- **Description**:
  - In `_create_session()`, remove `search_url=self._search_url` from the `ClaudeSession()`
    constructor call. Remove the `_search_url` attribute and the `search_url` parameter from
    `__init__` entirely; update Pipeline's call to `Classifier()` to omit `search_url`.
  - Result: `ClaudeSession` is always constructed with no `search_url` argument for the classifier.
  - No other changes to `classifier.py` in this task.
- **Releasable**: after this task, the classifier session has no MCP access — budget exhaustion on Search tools cannot occur.
- **Tests (TDD)** — `tests/ai/test_classifier.py`:
  - Unit: `test_create_session_search_url_is_none` — mock `ClaudeSession`; call `_create_session()`;
    assert `search_url` kwarg is absent from the `ClaudeSession()` call
  - Unit: `test_create_session_does_not_forward_search_url` — construct `Classifier()` (no
    `search_url` parameter); call `_create_session()`; assert `ClaudeSession` NOT called with
    any `search_url` kwarg
  - Checkpoint: `uv run pytest tests/ai/test_classifier.py -k "search_url" -v`

#### Task 1.2 — Fix classifier system prompt
- [x] **File**: `archon/ai/prompts/classifier.md`
- **Depends on**: nothing (independent of Task 1.1)
- **Description**:
  - Remove the line: `"You have only 5 steps to produce JSON output. **You MUST start with thinking
    and plan the remaining 4 steps. At the end you MUST give the proper JSON output.**"`
  - Add after the schema/intent rules (before the "If unsure" line):
    `"If the message is ambiguous (e.g. 'continue', 'do that', 'yes'), use the recent context
    below — if provided — to determine the correct intent."`
  - The rest of the prompt is unchanged: "Output ONLY raw JSON", "No markdown, no code fences",
    schema definition, intent descriptions, and "If unsure, classify as 'task'" all remain.
- **Releasable**: after this task, Haiku no longer attempts a multi-step planning sequence that races with the JSON output requirement.
- **Tests (TDD)** — `tests/ai/test_classifier.py`:
  - Unit: `test_classifier_prompt_has_no_5_steps_line` — load `load_prompt("classifier")`; assert
    `"5 steps"` not in the prompt string
  - Unit: `test_classifier_prompt_has_fallback_rule` — assert `"ambiguous"` in the prompt string
  - Checkpoint: `uv run pytest tests/ai/test_classifier.py -k "prompt" -v`

---

### Phase 2 — Context injection
> **Releasable**: after Task 2.3; the classifier receives the last 5 user messages as context on every call.

#### Task 2.1 — Add recent_context parameter to Classifier.classify()
- [x] **File**: `archon/ai/classifier.py`
- **Depends on**: Task 1.1, Task 1.2
- **Description**:
  - Change signature: `async def classify(self, prompt: str, recent_context: list[str] | None = None) -> ClassifierResult:`
  - Before creating the session and calling `send()`, build the enriched prompt:
    - If `recent_context` is `None` or empty: send `prompt` unchanged
    - If non-empty: prepend a labeled context block (see format below) and append `f"\nCurrent message: {prompt}"`
  - Context block format:
    ```
    [Recent context — last {N} user messages, oldest first]
    1. <msg 1, truncated to 200 chars>
    2. <msg 2, truncated to 200 chars>
    …
    ```
  - Truncation: `msg[:200]` (simple char slice, no word-boundary needed — brief explicitly allows it)
  - The enriched prompt is what gets passed to `session.send()`, not the original `prompt`
  - `ClassifierResult` is unchanged
- **Releasable**: after this task, the classifier can accept and use recent conversation context when provided by the caller.
- **Tests (TDD)** — `tests/ai/test_classifier.py`:
  - Unit: `test_classify_with_context_includes_context_block` — call `classify("do that", recent_context=["write tests", "ok done"])`;
    capture the prompt passed to `session.send()`; assert it contains `"[Recent context"`, `"last 2 user messages"` (N matches count),
    both context messages, and ends with `"Current message: do that"` (the original prompt as the final line)
  - Unit: `test_classify_context_header_n_matches_count` — call `classify()` with 3 messages in `recent_context`; assert the header contains `"last 3 user messages"`
  - Unit: `test_classify_without_context_no_context_block` — call `classify("hello", recent_context=None)`;
    assert prompt sent to session equals `"hello"` unchanged
  - Unit: `test_classify_context_empty_list_no_context_block` — `recent_context=[]` → prompt unchanged
  - Unit: `test_classify_context_truncates_long_messages` — pass a 300-char message in `recent_context`;
    assert truncated to 200 chars in the built prompt
  - Unit: `test_classify_context_oldest_first` — pass `["a", "b", "c"]`; assert they appear as `1. a`, `2. b`, `3. c` in order
  - Checkpoint: `uv run pytest tests/ai/test_classifier.py -k "context" -v`

#### Task 2.2 — _read_recent_user_messages() history reader
- [x] **File**: `archon/ai/pipeline.py`
- **Depends on**: nothing (independent utility function)
- **Description**:
  - Module-level function (not a method): `def _read_recent_user_messages(history_dir: str, today: date | None = None, limit: int = 5) -> list[str]:`
  - `today` defaults to `datetime.now(timezone.utc).date()` when `None` (matches `HistoryManager._utc_path()`; avoids midnight mismatch for non-UTC users)
  - Session file path: `Path(history_dir).expanduser() / "sessions" / f"{today.isoformat()}.md"`
  - Returns `[]` if the file does not exist (no logging needed — first message of the day)
  - On `(OSError, UnicodeDecodeError)`: log `logger.warning("Failed to read history for classifier context: %s", exc)` and return `[]`.
    `OSError` covers I/O failures; `UnicodeDecodeError` covers corrupt or non-UTF-8 file content
    raised by `Path.read_text(encoding="utf-8")`.
  - Parsing algorithm:
    - Read the file with `encoding="utf-8"`
    - Split into sections using regex `r"^## \d{2}:\d{2}:\d{2} UTC · User"` (multiline)
    - For each section that matches, extract the first non-empty line that does not start with `>` or `#`
    - That line is the user message text
    - Truncate to 200 chars: `text[:200]`
    - Collect all extracted messages, then return the last `limit` entries in original order (oldest first)
  - Import: `from datetime import date, datetime, timezone` (use `datetime.now(timezone.utc).date()` for default); `re` standard library; `Path` from `pathlib`
- **Releasable**: after this task, the history reader is callable and testable in isolation.
- **Tests (TDD)** — `tests/ai/test_pipeline.py` (or `tests/ai/test_pipeline_history.py` if that file is separate):
  - Unit: `test_read_recent_messages_happy_path` — write a fixture history file string with 3 user message sections; assert all 3 returned in order
  - Unit: `test_read_recent_messages_returns_last_n_when_over_limit` — fixture with 7 user message sections; assert only 5 returned (the last 5, oldest-first within those 5)
  - Unit: `test_read_recent_messages_fewer_than_limit` — 3 messages with limit=5; assert all 3 returned
  - Unit: `test_read_recent_messages_no_file` — call with non-existent directory; assert `[]` returned, no exception; assert `logger.warning` was NOT called (`mock_logger.warning.assert_not_called()`)
  - Unit: `test_read_recent_messages_io_error` — patch `Path.read_text` to raise `OSError`; assert `[]` returned and `logger.warning` called
  - Unit: `test_read_recent_messages_unicode_error` — patch `Path.read_text` to raise `UnicodeDecodeError`; assert `[]` returned and `logger.warning` called
  - Unit: `test_read_recent_messages_truncates_long_messages` — fixture with a 300-char message; assert returned string is 200 chars
  - Unit: `test_read_recent_messages_oldest_first` — 3 messages at different timestamps; assert order is chronological (oldest first)
  - Unit: `test_read_recent_messages_skips_archon_and_event_lines` — fixture with Archon `> ` lines and `###` event lines interspersed; assert only user text extracted
  - Unit: `test_read_recent_messages_multiline_takes_first_line` — fixture with a user message section containing multiple non-empty lines; assert only the first line is returned (subsequent lines discarded)
  - Checkpoint: `uv run pytest tests/ai/test_pipeline.py -k "recent_messages" -v`

#### Task 2.3 — Wire context reading in Pipeline.send() and plumb history_dir
- [x] **Files**: `archon/ai/pipeline.py`, `archon/ai/session_manager.py`, `archon/gateway/gateway.py`
- **Depends on**: Task 2.1, Task 2.2
- **Description**:
  - **`pipeline.py` — `Pipeline.__init__`**: add `history_dir: str | None = None` kwarg; store as `self._history_dir`
  - **`pipeline.py` — `Pipeline.send()`**: before calling `self._classifier.classify(prompt)`:
    ```python
    recent_context: list[str] | None = None
    if self._history_dir:
        try:
            messages = await asyncio.to_thread(_read_recent_user_messages, self._history_dir)
            recent_context = messages or None
        except Exception as exc:
            logger.warning("Classifier context read failed unexpectedly: %s", exc)
    result = await self._classifier.classify(prompt, recent_context=recent_context)
    ```
    `_read_recent_user_messages` is a synchronous function designed to run in a thread pool via
    `asyncio.to_thread()` to avoid blocking the event loop. `[] or None` collapses an empty list
    to `None` so `classify()` gets `None` on no-history days.
  - **`session_manager.py` — `SessionManager.__init__`**: add `history_dir: str | None = None` kwarg;
    store as `self._history_dir`; pass `history_dir=self._history_dir` to `Pipeline()` in `_default_factory`
  - **`gateway.py`**: add `history_dir=cfg.history.directory if cfg.history.enabled else None` to the
    `SessionManager(...)` call
  - Exception safety: `_read_recent_user_messages()` swallows `(OSError, UnicodeDecodeError)`
    internally. The `try/except Exception` in `send()` provides an additional safety net for
    unexpected exceptions (programming errors, unexpected return types) that should not prevent
    classification from running.
- **Releasable**: after this task, the full context injection pipeline is wired end-to-end.
- **Tests (TDD)** — `tests/ai/test_pipeline.py` and `tests/gateway/test_gateway.py`:
  - Unit: `test_pipeline_passes_recent_context_to_classifier` — construct `Pipeline(history_dir="/tmp/h")`;
    mock `_read_recent_user_messages` to return `["msg1", "msg2"]`; call `send("continue")`;
    assert `classifier.classify` called with `recent_context=["msg1", "msg2"]`
  - Unit: `test_pipeline_context_empty_list_passes_none` — mock returns `[]`; assert `classify` called with `recent_context=None`
  - Unit: `test_pipeline_no_history_dir_passes_none` — `Pipeline(history_dir=None)`; assert `classify` called with `recent_context=None` without calling `_read_recent_user_messages`
  - Unit: `test_pipeline_context_uses_to_thread` — mock `asyncio.to_thread`; construct `Pipeline(history_dir="/tmp/h")`; call `send("continue")`; assert `asyncio.to_thread` was called with `_read_recent_user_messages` as the first argument and the history dir as the second
  - Integration: `test_session_manager_passes_history_dir_to_pipeline` — construct `SessionManager(history_dir="/tmp/h")`;
    call `_default_factory()`; assert the returned `Pipeline._history_dir == "/tmp/h"`
  - Checkpoint: `uv run pytest tests/ai/test_pipeline.py tests/gateway/ -k "history_dir or recent_context" -v`

---

### Phase 3 — Live test cleanup
> **Releasable**: after Task 3.1; live tests reflect the current state of the classifier and can be run without expected failures masking regressions.

#### Task 3.1 — Remove BUG-2 xfail markers and add context injection live tests
- [x] **File**: `tests/ai/test_classifier_live.py`
- **Depends on**: Task 1.1, Task 1.2, Task 2.1
- **Description**:
  - **Pre-verification**: before removing any `xfail` markers, run:
    `uv run pytest -m live tests/ai/test_classifier_live.py --runxfail -v`
    and verify all BUG-2 tests pass. Only remove the markers once they are confirmed passing.
  - Remove `@pytest.mark.xfail` from tests marked with `reason="BUG-2: parse failure causes confidence=0.0"`
    and `reason="BUG-1/BUG-2: parse failure may cause wrong intent"` — `parse_classification()` now
    handles fences, so these must no longer be expected to fail
  - For BUG-1 tests (raw format — `reason="BUG-1: Haiku may wrap JSON in markdown fences"`):
    keep `xfail(strict=False)` — these test model behaviour that may still occur; they are informational
  - Update the file-level comment block to reflect current status: "BUG-2 (parse failure) is fixed;
    BUG-1 (raw format) is still informational — kept as xfail(strict=False)"
  - Add one new live test: `test_classifier_live_context_injection_disambiguates` — construct a
    `Classifier`, call `classify("continue", recent_context=["write tests for the pipeline"])`;
    assert `result.classification.intent == "task"` and `result.parse_error == ""`
- **Releasable**: after this task, live tests are clean — no false xfail noise, context injection verified against the real model.
- **Tests (TDD)** — `tests/ai/test_classifier_live.py`:
  - Live E2E: `test_classifier_live_task_confidence_above_threshold` — (formerly xfail BUG-2) assert confidence ≥ threshold
  - Live E2E: `test_classifier_live_chat_intent_classification` — (formerly xfail BUG-2) assert correct intent for chat messages
  - Live E2E: `test_classifier_live_context_injection_disambiguates` — new; "continue" with task context → `task` intent
  - Checkpoint: `uv run pytest -m live tests/ai/test_classifier_live.py -v`
