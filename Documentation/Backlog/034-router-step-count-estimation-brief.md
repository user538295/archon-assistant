# Feature Brief: Router Step-Count Estimation

## Problem
The router mis-classifies ~80% of tasks as "small" because it estimates scope from abstract criteria (file count, topic count) without grounding the decision in a concrete execution plan, causing complex tasks to run inline instead of as parallel background agents.

## Goal
The router enumerates atomic execution steps before deciding scope, outputs a `step_count` in its JSON response, uses that count as the primary classification signal (anchored by a soft guideline), and surfaces the count inline in the routing notification in verbose/debug mode — making misclassifications observable and diagnosable.

## Users & Context
Developers using Archon for multi-step coding or research tasks. They send a request, expect background agents for complex work, and instead get an inline session that stalls or times out — with no visibility into why the wrong path was taken.

## Core Flow
1. User sends a message; Pipeline calls `Decomposer.route_task()`
2. Router session (extended thinking enabled) reads the request
3. Prompt instructs: enumerate the atomic steps you would take — each tool call (Read, Write, Bash, Grep, Glob, WebFetch, WebSearch, MCP), each thinking block, each file operation counts as one step
4. Router reasons over the step list, applies soft guideline (≥6 steps → large, ≤3 → trivial), outputs JSON with `"step_count": N` alongside the existing `scope`/`summary`/`prompt`/`agents` fields
5. `_parse_task_output()` extracts `step_count` into `TaskOutput.step_count`
6. Routing notification in verbose/debug mode reads: `🎯 Routing: large (12 steps)` — inline augmentation of the existing routing event, no new message

## In Scope
- Extend `route_task.md` prompt: step enumeration instruction, explicit taxonomy, soft threshold guideline (≥6 → large, ≤3 → trivial)
- Add `step_count: int` field to `TaskOutput` dataclass
- Update `_parse_task_output()` to parse `step_count` from JSON (graceful: absent/invalid → 0, no routing change)
- Augment existing routing notification string with `(N steps)` in verbose/debug mode
- Unit tests: `step_count` parsing, missing/invalid field fallback, inline notification format
- E2E live tests: tasks that previously mis-routed to small now route large

## Out of Scope
- Hard threshold table — deferred until real traffic data validates the soft guideline values (see Future Iterations)
- Separate pre-routing LLM call for decomposition — thinking tokens already serve this purpose
- Surfacing `step_count` in non-debug/verbose Telegram modes
- Dedicated `StepCountEvent` — inline augmentation of existing event is sufficient

## Key Decisions
- **Prompt + JSON field over prompt-only**: A pure prompt change is untestable and silently regressable; `step_count` in the JSON output forces commitment and enables assertions
- **Model reasons from count (no hard table), but with soft anchor**: A hard table locks in uncalibrated thresholds; "≥6 steps is typically large" guides the model without overriding judgment on edge cases
- **Inline routing notification over separate event**: Keeps message count down; `step_count` is tightly coupled to the routing decision and belongs on the same line
- **Explicit step taxonomy in prompt**: Without a list of what counts as one step, the model applies inconsistent granularity across requests; naming the SDK tool types (Read, Write, Bash, Grep, Glob, WebFetch, WebSearch, MCP call, thinking block) anchors the count

## Edge Cases & Constraints
- **`step_count` absent from JSON** (old format, parse error, fallback response): parse as `0`, routing proceeds unchanged — backwards-compatible
- **Model counts steps but still outputs wrong scope**: Now observable — debug mode shows the discrepancy (`🎯 Routing: small (9 steps)` is a clear signal something is miscalibrated)
- **Trivial scope**: `step_count` expected to be 0–2; prompt should note this explicitly
- **Router thinking tokens are already captured**: `ThinkingResult` events from the router session flow through the existing event pipeline — no changes needed to capture reasoning
- **`_ROUTER_TIMEOUT_S = 180s`**: Step enumeration adds to thinking time; the existing timeout already accommodates 60–90s thinking, so no timeout change needed

## Open Questions
- Exact soft threshold values (6 for large, 3 for trivial) are initial guesses — need validation against real traffic. Plan-maker should add a note to revisit after first 50 routed requests.
- Where exactly in the rendering path does `step_count` get injected into the routing notification string? Needs a read of `pipeline.py` routing event emission to confirm the right insertion point.

## Future Iterations
- **Hard threshold table** once empirical data validates the soft guideline — replace the "soft suggestion" with `step_count >= N → scope must be large` as a non-overridable rule
- **Expose in `/status` diagnostics** — include last routing decision + step_count in session diagnostics output
- **Per-step-type weighting** — MCP calls and WebSearch are heavier than file reads; a weighted count could improve accuracy further

## Recommendation
This is the right fix to ship now — FIX-032 already improved the rubric once and still left 80% misclassification. The core issue is the signal, not the threshold: the model needs to count before it classifies. The implementation is small (one prompt file, one dataclass field, one parsing line, one string format change) and the observability gain is immediate — misclassifications become visible in debug mode the moment it ships. The hardest part is calibrating the soft threshold; start conservative (≥6 = large) and adjust. Do not compromise on the `step_count` field being in the JSON output — prompt-only changes to routing have already proven insufficient.
