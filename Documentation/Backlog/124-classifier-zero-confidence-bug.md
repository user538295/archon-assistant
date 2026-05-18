# Bug Investigation: Classifier Zero Confidence

**Date**: 2026-04-17  
**Observed**: Multiple occurrences in session log — `{"intent": "task", "confidence": 0.0}` with parse error "no JSON object found in response"

---

## Failure Modes

Three distinct failure modes produce the same symptom (confidence=0.0, "no JSON object found in response"), but each has a different root cause.

### Mode A — SDK delivers content in TextBlock, not ResultMessage (17:48:41)

The SDK returned the classifier's JSON answer inside an `AssistantMessage` `TextBlock`, but the final `ResultMessage.result` was empty/None — likely because a `RateLimitEvent` interrupted the response. `event_mapper.py` (line 268–272) discards `TextBlock` content from `AssistantMessage` by design, so no `Response` event reaches the classifier. The classifier's `raw_response` stays `""`, and `parse_classification("")` returns `Classification(intent="task", confidence=0.0)`.

The `elif message.result:` falsy guard (line 289) means no `Response` event is yielded for empty/None results, but this is secondary — even if it were fixed, `parse_classification("")` still returns 0.0. The actual JSON content is in the discarded `TextBlock`.

### Mode B — Model returns narrative prose instead of JSON (18:21–18:31)

The model returned apology/explanation text instead of JSON. `ResultMessage.result` IS non-empty (it contains the prose), so a `Response` event IS emitted and `raw_response` receives the prose text. `parse_classification()` fails because the text contains no JSON object.

Root cause: classifier session history accumulation shifts the model away from JSON-only output. The persistent session means prior exchanges (including error recoveries) pollute the context window, causing the model to generate conversational prose instead of the required JSON schema.

### Mode C — Session corruption after crash (18:44:19)

After the Bug 8 JSON buffer overflow crash, the classifier session was corrupted. `ToolStarted`/`ToolResult` events leaked into a session configured with `tools=[]`. The model produced narrative prose and the parse failed.

Root cause: session state corruption from the prior crash. The SDK resumed a corrupted session that no longer respected the `tools=[]` constraint.

**Note**: Mode C is NOT directly fixed by addressing session state — the underlying SDK session-resume corruption is a separate bug requiring its own investigation. However, Mode C is eliminated indirectly by Option E (stateless classifier), which removes the persistent session that could be corrupted. If Option E is not adopted, a separate fix is needed (e.g., fresh session creation after crash detection).

---

## App Log Evidence (Verified)

Four distinct occurrences found in `/Users/manczg/.archon/logs/archon.log`:

**17:48:41 UTC** — JSON was in an AssistantMessage TextBlock that was discarded, followed by a RateLimitEvent, then the parse failure:
```
DEBUG: TextBlock in AssistantMessage discarded: {"intent": "task", "confidence": 0.95}
DEBUG: Unhandled SDK message type: RateLimitEvent
WARNING: Classification parse failed: no JSON object found in response
INFO:  Classification: intent=task confidence=0.00 duration=2.3s
DEBUG: Dropped non-ThinkingResult classifier event: ToolStarted
DEBUG: Dropped non-ThinkingResult classifier event: ToolResult
DEBUG: Dropped non-ThinkingResult classifier event: ErrorEvent
```

**Note**: The ToolStarted/ToolResult events at 17:48:41 suggest Mode C contamination (tool events in a `tools=[]` session) may have been present earlier than the 18:44:19 occurrence. The three failure modes may co-occur rather than be strictly sequential.

**18:21–18:31 UTC** — Classifier received narrative prose (apology text) instead of JSON:
```
DEBUG: TextBlock in AssistantMessage discarded: "You're absolutely right—I apologize..."
WARNING: Classification parse failed: no JSON object found in response
INFO:  Classification: intent=task confidence=0.00 duration=1.9–4.1s
```

**18:44:19 UTC** — Session corrupted after Bug 8 JSON crash, multiple tool events leaked:
```
DEBUG: TextBlock in AssistantMessage discarded: "You're right. I'll investigate..."
WARNING: Classification parse failed: no JSON object found in response
DEBUG: Dropped non-ThinkingResult classifier event: ToolStarted  (×2)
DEBUG: Dropped non-ThinkingResult classifier event: ToolResult   (×2)
DEBUG: Dropped non-ThinkingResult classifier event: ErrorEvent
```

---

## Options

### Option A: Fix event_mapper — always yield Response for ResultMessage

```python
elif isinstance(message, ResultMessage):
    if message.is_error:
        yield ErrorEvent(message=message.result or "Unknown error")
    else:
        yield Response(content=message.result or "")
```

**Pros**: Consistent event contract; downstream code always gets one Response per non-error ResultMessage.

**Cons**:
- Does NOT fix Mode A — `parse_classification("")` still returns 0.0. The JSON content is in the discarded TextBlock, not in `ResultMessage.result`.
- Does NOT fix Modes B or C — those already have non-empty `ResultMessage.result` (prose text), so the falsy guard is irrelevant.
- **User-visible regression**: `Response(content="")` flows through `telegram_formatter.py` `format_event()` (line 196) → `render_split_messages()`, which produces `["✅ Response:\n"]` — an empty response message sent to the user.
- **Breaks existing test**: `test_result_message_with_none_result_produces_no_response` in `tests/ai/test_event_mapper.py` (line 249) asserts `events == []` for `result=None`.
- **Loses diagnostic precision**: `message.result or ""` conflates `None` (no response generated) with `""` (empty response generated). A more precise approach would be `message.result if message.result is not None else ""` with an explicit log for `None`.
- Masks SDK failure (same concern raised against Option C).

**Consumer impact of `Response(content="")`**:
| Consumer | File | Effect |
|---|---|---|
| `telegram_formatter.format_event()` | `archon/chat/telegram_formatter.py:196` | Sends `"✅ Response:\n"` to user (empty message) |
| `handler.py` event loop | `archon/chat/handler.py:292` | Records empty Response event in session history (main event loop, unconditional) |
| `voice.py` TTS capture | `archon/chat/voice.py:237` | Sets `response_text = ""`. TTS silently skipped (empty string is falsy — `if response_text and self.tts` guard at line 341 prevents voice reply). No regression from current behavior |
| `decomposer.py` | `archon/ai/decomposer.py:305` | Sets `last_response = ""`. Subsequent `if last_response:` check at line 308 prevents empty response from being appended to `_pending_turns` — effectively a silent drop of the exchange from summary context |
| `background_agent_manager.py` | `archon/ai/background_agent_manager.py:424` | Sets agent `result = ""` |
| `job_scheduler.py` | `archon/ai/job_scheduler.py:651` | Returns `""` as job result |
| `event_renderer.py` | `archon/ai/event_renderer.py:121` | Writes empty response to history file |

### Option B: Enhanced logging in classifier and classification.py (diagnostic only)

In `classifier.py` after the event loop (after line 133), check `if not raw_response:` and log a distinct warning ("Classifier received no Response event — defaulting"). In `classification.py` `parse_classification()`, distinguish empty string from malformed JSON with a dedicated log message (e.g., "empty response — no content from model" vs. "no JSON object found in response").

**Pros**: Better diagnostics; localized to classifier; adds hook points for future retry logic.
**Cons**: Diagnostic improvement only — does not fix any of the three failure modes. Must be combined with an actual fix.

### Option C: Classifier retry on empty response

Add retry logic (up to 2 retries) in `classifier.py` when empty response is detected, using a fresh prompt.

**Pros**: Recovers from transient SDK failures (Mode A with RateLimitEvent) automatically; retry is textbook correct for transient errors.
**Cons**: Adds latency and complexity; will not help for session contamination modes (B, C) since the same corrupted session is reused; same "masking" concern as Option A (both hide the SDK-level issue, just in different ways).

### Option D: TextBlock fallback in classifier

The JSON answer is already present in the `AssistantMessage` `TextBlock` that `event_mapper.py` discards (line 268–272). The classifier can capture `TextBlock` content as a fallback when no `Response` event arrives.

The classifier iterates mapped events via `self._session.send(prompt)` (line 125 of `classifier.py`), which internally calls `event_mapper.map_messages()` (line 353 of `claude_session.py`). The `TextBlock` is discarded in `event_mapper._map()` (line 268–272) and never yielded — the classifier does NOT see raw SDK messages.

**Implementation approach**: Add a new lightweight `TextBlockEvent` dataclass in `event_mapper.py` and yield it for `AssistantMessage` `TextBlock`s instead of discarding them. In `classifier.py` `classify()` (line 125–129), when iterating events, capture `TextBlockEvent` content in a `textblock_fallback` variable. After the event loop, if `raw_response` is empty and `textblock_fallback` is non-empty, use `extract_json_object()` on the fallback content. For multiple `TextBlock`s: iterate them in order, call `extract_json_object()` on each, and take the first that returns valid JSON. All other consumers (handler, decomposer, background agents) simply ignore `TextBlockEvent` — no behavior change.

**Files to modify**: `archon/ai/event_mapper.py` (add `TextBlockEvent` dataclass, yield it instead of logging+discarding), `archon/ai/classifier.py` (capture `TextBlockEvent` as fallback).

**Pros**: Specifically addresses Mode A — recovers the actual JSON that the SDK already delivered. Does not change the `Response` contract. No retry latency.
**Cons**: Does not address Modes B or C (the TextBlock contains prose in those cases too). Adds a new event type that most consumers will ignore.

### Option E: Stateless classifier — fresh SDK session per classify() call

Replace the persistent classifier session with a fresh `ClaudeSession` created (and destroyed) on each `classify()` call. No session resume, no history accumulation.

**Pros**: Eliminates session contamination entirely — Mode C becomes impossible (no session to corrupt); Mode B becomes significantly less likely (no accumulated history to contaminate context). Classification is stateless by nature (each call is independent). No need for `_CLASSIFIER_RESET_THRESHOLD` or `_reset_session()` logic.
**Cons**: Cold-start latency per call (likely small for Haiku). Loses any potential benefit of session warmup and system prompt caching. Higher SDK overhead from repeated connect/disconnect cycles. `_carried_cost_usd` and `_carried_cache_creation` must be retained and updated after each per-call session stop (see Recommendation).

---

## Recommendation

Each failure mode requires a different fix:

### For Mode A (SDK delivers JSON in TextBlock, not ResultMessage)

**Primary**: **Option D** (TextBlock fallback in classifier). The JSON content is already available — it just needs to be captured. This directly recovers the classification without retries or contract changes.

**Complement**: **Option B** (enhanced logging) to distinguish "no Response event received" from "Response received but not JSON", aiding future debugging.

### For Modes B and C (session contamination causing prose output)

**Primary**: **Option E** (stateless classifier). Classification is inherently stateless — there is no reason for the classifier to maintain session history. A fresh session per call eliminates history contamination and session corruption as failure vectors. This removes `_reset_session()` and `_CLASSIFIER_RESET_THRESHOLD`. However, `_carried_cost_usd` and `_carried_cache_creation` are load-bearing: `Pipeline.usage_stats` (line 605 of `pipeline.py`) aggregates classifier cost via `Classifier.usage_stats`, which sums carried values with current session stats. With a fresh session per call, the carry-over accumulation pattern must be adapted: after each `session.stop()`, accumulate the session's stats into `_carried_cost_usd` and `_carried_cache_creation` before destroying the session, so that `usage_stats` continues to report cumulative costs. `test_usage_stats_survive_session_reset` in `tests/ai/test_classifier.py` must still pass.

**Alternative**: If Option E's cold-start latency is unacceptable, add **reset-on-parse-error** as a targeted mitigation: when `parse_classification()` returns an error, immediately call `_reset_session()` instead of waiting for the 50-call threshold. This addresses Modes B and C by clearing contaminated state on first failure rather than after 50 calls.

Reset-on-parse-error details:
- **Circuit breaker**: Track consecutive reset count; after 2 consecutive resets with no successful classification, stop resetting and return the error classification. Reset the counter on any successful classification.
- **Trigger scope**: Only trigger reset when `ClassificationResult.error` contains "no JSON object found" or "malformed JSON". Field validation errors (invalid intent, missing confidence) do NOT trigger reset — those indicate a model output issue, not session contamination.
- **Timeout guard**: `_reset_session()` is async and calls `session.stop()` which may hang. Wrap in `asyncio.wait_for(..., timeout=5.0)` using the existing timeout pattern. On timeout, log a warning and proceed (the next `classify()` call will create a fresh session anyway via the threshold reset).

### Implementation Order

1. **Option E** (stateless classifier) OR **reset-on-parse-error** first — eliminates Modes B and C
2. **Option D** (TextBlock fallback) second — addresses Mode A
3. **Option B** (enhanced logging) last — purely diagnostic, can ship anytime

### Falsy guard fix (code quality)

The `elif message.result:` guard in `event_mapper.py` (line 289) is a minor code quality issue, not the root cause of any observed symptom. If fixed:
- Use `message.result if message.result is not None else ""` (not `message.result or ""`) to preserve diagnostic distinction between None and empty string.
- Add an empty-content guard in `format_event()` to prevent sending `"✅ Response:\n"` with no content to Telegram.
- Add a test for `result=""` — the existing test (`test_result_message_with_none_result_produces_no_response`, line 249 in `tests/ai/test_event_mapper.py`) only covers `result=None`.

### Files to modify

| File | Function/Location | Change | Test file |
|---|---|---|---|
| `archon/ai/event_mapper.py` | `_map()` (line 268–272) | Add `TextBlockEvent` dataclass; yield it for `AssistantMessage` `TextBlock`s instead of discarding (Option D) | `tests/ai/test_event_mapper.py` |
| `archon/ai/classifier.py` | `classify()` (line 125–129) | Capture `TextBlockEvent` as fallback; iterate TextBlocks in order, use `extract_json_object()` on each, take first valid JSON (Option D) | `tests/ai/test_classifier.py` |
| `archon/ai/classifier.py` | `classify()` (line 134–138) | Add warning log when `raw_response` is empty after event loop (Option B) | `tests/ai/test_classifier.py` |
| `archon/ai/classifier.py` | `__init__()`, `classify()`, `_reset_session()` | Replace persistent session with per-call session creation (Option E), OR add `_reset_session()` call when `result.error` is set (reset-on-error alternative). Retain `_carried_cost_usd`/`_carried_cache_creation` — accumulate stats after each per-call `session.stop()` | `tests/ai/test_classifier.py` |
| `archon/ai/pipeline.py` | `Pipeline.start()` (line 171), `Pipeline.stop()` (line 176) | With stateless classifier (Option E): `classifier.start()` becomes a no-op (nothing to initialize without a persistent session). `classifier.stop()` accumulates final stats from the last per-call session into the carried fields, then returns. Remove the calls from `Pipeline.start()`/`Pipeline.stop()` at `pipeline.py` lines 173 and 179, or retain them as no-ops — either is valid. Each `classify()` call must explicitly call `await session.start()` before `session.send(prompt)` and `await session.stop()` after (accumulating stats into `_carried_cost_usd` and `_carried_cache_creation` before releasing the session reference) | `tests/ai/test_pipeline.py` |
| `archon/ai/classification.py` | `parse_classification()` (line 94–102) | Distinguish empty string from malformed JSON in log message (Option B) | `tests/ai/test_classification.py` |
| `archon/ai/event_mapper.py` | `_map()` (line 289) | Optional: change `elif message.result:` to handle None vs empty distinctly; add explicit log for None (code quality) | `tests/ai/test_event_mapper.py` |
| `archon/chat/telegram_formatter.py` | `format_event()` (line 196) | If falsy guard is fixed: add guard for `Response(content="")` to prevent empty Telegram messages | `tests/chat/test_telegram_formatter.py` |

---

## Acceptance Criteria

Verifiable conditions for each failure mode fix:

- **Mode A**: When the SDK delivers valid JSON in an `AssistantMessage` `TextBlock` with empty `ResultMessage.result`, the classifier must use the `TextBlock` content and return `confidence > 0.0`. Verified by test: mock SDK to return `AssistantMessage` with `TextBlock('{"intent": "task", "confidence": 0.95}')` followed by `ResultMessage(result=None)` — assert `classification.confidence == 0.95`.
- **Mode B**:
  - If Option E (primary): Each `classify()` call uses a fresh session with no history from prior calls. Verified by test: after a failed classification, the next `classify()` call creates a new `ClaudeSession` instance with no accumulated history.
  - If reset-on-parse-error (alternative): After a parse failure, the classifier session must reset before the next call, within the circuit breaker limit (max 2 consecutive resets). Verified by log assertion: `"Classifier session reset after parse error"` appears in archon.log.
- **Mode C**:
  - If Option E (primary): A per-call session cannot inherit tool events or state from a prior session. Verified by test: after simulating a session that yields `ToolStarted` events in a `tools=[]` classifier session, the next `classify()` call uses a fully independent session and returns a valid classification with `confidence > 0.0`.
  - If Option E is not adopted: tracked in a separate investigation (Mode C — session corruption after crash).
- **Falsy guard fix** (if applied): A `ResultMessage` with `result=None` must NOT produce an empty Telegram message to the user. Verified by test: `Response(content="")` must not pass through `format_event()` to Telegram delivery (either suppressed at event_mapper level or filtered in formatter).
