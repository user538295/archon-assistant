# Feature Brief: Response Validator (FEAT-035)

## Problem
Archon's pipeline has no semantic quality gate — responses are delivered immediately after generation with no verification that the output actually met the user's goal. For complex tasks or ambiguous requests, this can produce incomplete, off-target, or incorrect responses that the user must manually follow up on.

## Goal
After each response is sent, a lightweight Validator session evaluates whether it achieved the user's intent, scores it 0–100, and injects a correction prompt into the Decomposer session if the score falls below a configurable threshold. The loop continues until the score meets the threshold or `max_retries` is exhausted. The user receives every attempt as it streams — the notification mode determines how much they see.

## Users & Context
All Archon users in any notification mode. Power users in verbose/debug mode see full scoring detail. Quiet/normal users receive streamed responses as usual, with an additional summary message only if retries are exhausted.

## Core Flow
1. User sends a message → Archon classifies, routes, and generates a response
2. Response streams to Telegram immediately (as today)
3. Validator session runs post-response: receives original request + classification intent + response content
4. Validator outputs a `result_score` (0–100) and gap analysis
5. If `result_score ≥ result_score_threshold`: done — `QualityScoreEvent` emitted (visible in verbose/debug)
6. If `result_score < threshold` and retries remain: Validator injects a correction prompt into the existing Decomposer session; Decomposer streams an improved response to Telegram; go to step 3
7. If `max_retries` exhausted without approval: send one informational message (always, all notification modes):
   - Best attempt = last attempt: `"🔍 Validator: retries exhausted (score: 72/80)."`
   - Best attempt ≠ last attempt: `"🔍 Validator: retries exhausted. Attempt 2 scored higher (80 vs 65). Check session history to review."` — no duplicate response; the history log already contains every attempt

## In Scope
- `ResponseValidator` class — ephemeral satellite session (same model as user's configured default)
- `archon/ai/prompts/validator.md` — system prompt instructing the Validator to answer: (1) Was the user's goal achieved? (2) Was anything missed? (3) Was anything done the user didn't ask for? — outputting structured JSON: `{"result_score": int, "approved": bool, "gap_analysis": string}`
- `QualityScoreEvent` dataclass, added to the `Event` type union
- `[validator]` config section: `enabled`, `result_score_threshold`, `max_retries`
- Pipeline integration: post-response validation loop
- Correction prompt injected into existing Decomposer session (no session restart)
- All attempts stream to Telegram immediately; no buffering
- `QualityScoreEvent` visible only in verbose/debug mode (matches Router event pattern)
- Exhaustion system message always sent in all modes
- Best-attempt tracking: track highest score + attempt index across retries
- Exhaustion message always sent in all notification modes; if best attempt ≠ last attempt, reference it by attempt number + score and direct user to session history — no duplicate response sent

## Out of Scope
- **Buffering or withholding responses**: every attempt is sent immediately — always
- **Pre-execution plan validation**: validate before generating, not after (future iteration)
- **Per-agent-wave validation** during background tasks
- **Chat-intent-only skipping**: validator applies to all responses regardless of classification
- **Per-user threshold overrides** via Telegram commands
- **CLI `/validator` command**
- **Interactive "resend best" keyboard buttons**: best attempt is re-sent inline when needed — no interactive callback required

## Key Decisions
- **Model: user's configured default** — same model that produced the response evaluates it, ensuring the validator understands what was attempted. Haiku would be cheaper but misses nuance on complex tasks.
- **Validate always, stream always** — responses are not withheld. The user's notification mode (quiet/normal/verbose/debug) controls how much they see of the validation process. This is consistent with how background agent events work.
- **Inject into existing Decomposer session** — the Decomposer already has conversation context and tool history. A correction message leverages that context rather than starting cold. The validator's feedback becomes part of the natural conversation.
- **Best-attempt referenced via session history, never re-sent** — sending two responses confuses the user about which answer is canonical. The session history log already captures every attempt. The exhaustion message directs the user there if a better-scoring attempt exists: `"Attempt 2 scored higher (80 vs 65). Check session history to review."` This works in all notification modes with no duplicate content.
- **Opt-in by default** (`enabled = false`) — validation adds latency and cost. Users who want it enable it explicitly.
- **Validator session context** — the Validator receives: (a) the original user message, (b) the Classifier's intent + confidence, (c) the full response text. It does NOT receive the Decomposer's full conversation history or tool call logs — those would inflate token cost with marginal benefit. The validator's job is goal alignment, not process audit.

## Edge Cases & Constraints
- **Validator session timeout** (45s): treat as `score = 0, approved = false`; count as a retry; log warning. Do not block indefinitely.
- **Correction prompt timeout** (90s per retry): treat as approved (deliver last attempt); emit `ErrorEvent` with explanation.
- **Malformed validator output** (unparseable JSON): treat as `score = 0, approved = false`; log parse error.
- **`max_retries = 0`**: validate once; if score < threshold, send exhaustion message immediately — no correction prompt is ever injected. Zero retries means zero correction attempts.
- **All attempts same score**: last attempt wins — it's already in the chat.
- **`enabled = false`**: skip entirely; pipeline behaves as today with zero overhead.
- **Session restart during validation loop**: if Decomposer session is evicted or restarted mid-loop, abort loop and send exhaustion message.
- **Config `result_score_threshold` outside [0, 100]** or `max_retries < 0`: raise `ConfigError` at startup.

## Open Questions
- Should the Validator's correction prompt be included in `HistoryManager` session logs? Current assumption: yes — the full conversation including validator feedback is useful for debugging. Needs confirmation during planning.
- Should `QualityScoreEvent` include the full gap analysis text or just the score + approved flag? Full text is useful in debug mode; brief summary avoids noise in verbose.

## Future Iterations
- **Pre-execution validation** — validate the task plan before execution starts (saves wasted tool calls on wrong-direction plans)
- **Per-agent-wave validation** — validate each background agent's output independently
- **Haiku validator mode** — configurable validator model for cost-sensitive deployments
- **Per-user threshold override** — `/validator threshold 90` command for users who want stricter or looser gates
- **Validation history analytics** — track average scores, common failure patterns, retry rates

## Recommendation
This is a high-value feature for users running Archon on complex tasks where response quality matters. The implementation fits cleanly into the existing satellite-session pattern (Classifier, Router, Summary are all precedents). The hardest part is the retry loop state machine inside `pipeline.send()` — specifically tracking scores across retries while preserving the streaming event model. The opt-in default and the "stream everything" approach are the right calls: they keep the feature transparent and avoid surprising users with buffering delays. What must not be compromised: every attempt must stream immediately, and the validation loop must be strictly bounded by `max_retries` with no chance of hanging.
