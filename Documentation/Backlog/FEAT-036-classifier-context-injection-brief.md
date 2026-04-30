# Feature Brief: Classifier Context Injection & Reliability Fix

## Problem
The intent classifier (Haiku) calls the Search MCP tool on ambiguous follow-up messages ("continue", "do that"), exhausting its step budget and failing to produce JSON output — causing classification to silently fall back to the decomposer with a wrong intent.

## Goal
Classifier reliably produces a valid JSON classification on every call, handles follow-up messages correctly, and never calls external tools.

## Users & Context
All Telegram users of Archon. Occurs whenever a user sends a short follow-up message after a prior exchange — a common interaction pattern.

## Core Flow
1. User sends a message to Archon.
2. Pipeline reads the last 5 user messages from today's session history file.
3. Pipeline calls `classifier.classify(prompt, recent_context=<5 messages>)`.
4. Classifier builds its prompt: system prompt + labeled recent context block + current message.
5. Haiku classifies with full context; outputs raw JSON.
6. `parse_classification()` strips any markdown fences, parses JSON, returns result.
7. Pipeline routes based on result as before.

## In Scope
- Remove `search_url` from classifier's `_create_session()` — hard MCP access disable
- Add `recent_context: list[str] | None` parameter to `Classifier.classify()`
- Pipeline: read last 5 user messages from `~/.archon/history/sessions/YYYY-MM-DD.md` before calling classify
- Inject context into classifier prompt as labeled oldest-first list
- Update classifier system prompt: remove contradictory "5 steps" line; add one-liner fallback rule for ambiguous messages ("use recent context below if the message is ambiguous")
- Fix `parse_classification()`: strip markdown code fences before JSON parsing
- Update unit tests to cover context injection and fence-stripping
- Remove `xfail` markers from live tests once the fence bug is fixed

## Out of Scope
- AI response text injection — user messages alone cover 95%+ of ambiguous cases; AI responses add complexity for negligible gain
- Haiku summarisation of responses — extra LLM call per turn, bad cost/complexity tradeoff
- Cross-day history — today's session file is sufficient; yesterday's context is rarely relevant for intent classification
- Compacted history files — compaction loses the raw message signal needed here
- Classifier session persistence — remains single-turn, stateless by design

## Key Decisions
- **MCP disabled in Python, not prompt**: `search_url=None` in `_create_session()` is a hard guarantee; prompt instructions can drift or be ignored by the model.
- **History read in Pipeline, not Classifier**: Classifier stays a pure LLM wrapper. Pipeline already handles all context concerns (RAG injection for decomposer follows the same pattern).
- **Oldest-first labeled format**: `[5 most recent user messages, oldest first]` gives Haiku the recency and flow signal needed for "continue" / "do that" classification.
- **Fence-stripping in parse_classification()**: Haiku wraps JSON in markdown fences despite the prompt; confirmed in live tests. One-liner fix, unblocks existing `xfail` tests, zero scope risk.
- **Fallback rule in prompt, not planning constraint**: The "5 steps / plan" line contradicted the "output ONLY JSON" rule. Replaced with a single fallback sentence so Haiku knows when and how to use the injected context.

## Edge Cases & Constraints
- **No history file yet (first message of the day)**: `recent_context` is `None` or empty list; classifier falls back to message-only classification — same as current behaviour.
- **History file unreadable (permissions, I/O error)**: Pipeline catches the exception, passes `recent_context=None`, logs a warning. Classification proceeds without context.
- **Fewer than 5 prior messages**: Inject however many exist (1–4); no padding needed.
- **Very long user messages in history**: Truncate each injected message to 200 chars to keep the context block small.
- **`search_url` removal**: Classifier constructor still accepts `search_url` (Pipeline passes it) — it's just not forwarded to `_create_session()`. No interface change needed at the Pipeline level.

## Open Questions
- None. All design decisions resolved.

## Future Iterations
- Inject first sentence of the preceding AI response alongside each user message — covers the rare "do that" edge case where the AI response text is the only disambiguating signal.
- Per-user history isolation if multi-user support is added (currently single-user per session file).

## Recommendation
Build this now. The classifier failing silently on follow-up messages is a correctness bug, not a nice-to-have. The scope is tight (two files + one prompt + one parser fix), the pattern mirrors existing Pipeline context injection, and it eliminates the fragile "Haiku will plan within 5 steps" prompt approach entirely. The hardest part is reliable history file parsing — keep the reader simple and treat I/O errors as graceful degradation, not failures.
