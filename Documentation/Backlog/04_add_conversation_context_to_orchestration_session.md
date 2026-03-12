# Plan: Add Conversation Context to Orchestration Session

## Context

After splitting the Decomposer into _session (user conversation) and _orch_session (review/route_task), the orchestration session lost visibility into what answer() discussed. When a user says "thanks" or
"now add tests for what you just did", the orch session can't determine intent because it doesn't know what happened on _session.
Key insight: The _orch_session already accumulates its own SDK conversation history — it remembers all prior review() and route_task() exchanges automatically. The gap is strictly: what did answer() on _session discuss?
Design: Track recent answer() turns in a buffer → use Haiku to produce a rolling summary in the background → embed the summary in every orchestration instruction. Haiku is the sole context source.


## Files to modify

┌───────────────────────────────────────────┬───────────────────────────────────────────────────────────────────────────┐
│                   File                    │                                   Scope                                   │
├───────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────┤
│ archon/ai/decomposer.py                   │ Core: turn buffer, Haiku summarization, context injection, session resets │
├───────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────┤
│ archon/ai/pipeline.py                     │ track_context() delegation + record context after task escalation         │
├───────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────┤
│ archon/ai/background_agent_manager.py     │ Record context after agent completes                                      │
├───────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────┤
│ archon/ai/session_manager.py              │ Add track_context() public method                                         │
├───────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────┤
│ tests/ai/test_decomposer.py               │ TDD tests for all decomposer changes + update _make_decomposer helper     │
├───────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────┤
│ tests/ai/test_pipeline.py                 │ Test escalation context tracking                                          │
├───────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────┤
│ tests/ai/test_background_agent_manager.py │ Test agent completion context tracking                                    │
└───────────────────────────────────────────┴───────────────────────────────────────────────────────────────────────────┘


## Steps

### Step 1: Core — answer() tracking + Haiku summarization + context injection

#### Tests (TDD — write first):

1. test_answer_tracks_turn_in_buffer — consume answer() with Response → _pending_turns has (prompt, response_content)
2. test_answer_skips_tracking_when_no_response — generator closed before Response → _pending_turns empty
3. test_answer_schedules_summary_after_tracking — after answer() with Response, _summary_task is created
4. test_schedule_summary_skips_when_already_running — if summary task is in-flight, new schedule is skipped (no cancellation)
5. test_refresh_summary_updates_context_summary — after _refresh_summary(), _context_summary is set to Haiku output
6. test_refresh_summary_clears_summarized_turns — summarized turns removed via popleft()
7. test_refresh_summary_preserves_turns_added_during_summarization — turns appended during Haiku survive the drain
8. test_refresh_summary_self_schedules_when_pending_turns_remain — if new turns arrived during Haiku, another task is created
9. test_refresh_summary_does_not_self_schedule_on_failure — on Haiku error, no self-scheduling (prevents infinite retry loops)
10. test_refresh_summary_incorporates_previous_summary — prompt includes previous _context_summary for incremental summarization
11. test_refresh_summary_failure_keeps_turns — on Haiku error, turns stay in buffer
12. test_build_orch_context_returns_summary — returns formatted _context_summary
13. test_build_orch_context_empty_when_no_summary — returns "" when _context_summary is empty
14. test_review_awaits_pending_summary — if summary task is running, review() awaits it before proceeding
15. test_review_includes_conversation_context — answer() → review() → orch prompt contains context
16. test_review_no_context_on_first_message — first review() has no context
17. test_route_task_awaits_and_includes_context — same as review but for route_task
18. test_track_context_appends_and_schedules_summary — external callers inject context entries


#### Implementation

Update _make_decomposer helper — now mocks 3 sessions:
def _make_decomposer(session_events=None, orch_events=None, summary_events=None, **kwargs):
    """Build a Decomposer with mocked main, orchestration, and summary sessions."""
    if session_events is None:
        session_events = [Response(content="Done.")]
    if orch_events is None:
        orch_events = [Response(content='{"intent":"task","confidence":0.9}')]
    if summary_events is None:
        summary_events = [Response(content="User discussed topic X.")]

    main_session = _mock_session(*session_events)
    orch_session = _mock_session(*orch_events)
    summary_session = _mock_session(*summary_events)

    with patch(
        "archon.ai.decomposer.ClaudeSession",
        side_effect=[main_session, orch_session, summary_session],
    ):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            decomposer = Decomposer(**kwargs)

    return decomposer, main_session, orch_session, summary_session

All existing tests need their unpacking updated from 3-tuple to 4-tuple (or _ for the summary session they don't use).

Production (archon/ai/decomposer.py):

New state in __init__:
import asyncio
from collections import deque

_SUMMARIZER_MODEL = "claude-haiku-4-5-20251001"
_SUMMARIZER_PROMPT = (
    "Summarize the conversation exchanges below in 2-3 concise sentences. "
    "Focus on: topics discussed, actions taken or planned, decisions made. "
    "Be factual and brief."
)

# In __init__:
self._pending_turns: deque[tuple[str, str]] = deque()  # unbounded — drained by Haiku
self._context_summary: str = ""
self._summary_session = ClaudeSession(
    model=_SUMMARIZER_MODEL,
    system_prompt=_SUMMARIZER_PROMPT,
    tools=[],
    max_turns=1,
)
self._summary_task: asyncio.Task[None] | None = None

_pending_turns is unbounded (no maxlen). In normal operation it stays at 0-2 items because Haiku drains it after each answer(). Only grows during Haiku downtime, where memory is negligible (few KB of string
 tuples).

Update start() / stop() — manage summary session lifecycle. In stop(), cancel _summary_task if running.

Update answer() — track turn + schedule Haiku:
async def answer(self, prompt: str) -> AsyncGenerator[Event, None]:
    last_response = ""
    try:
        async for event in self._session.send(prompt):
            if isinstance(event, Response):
                last_response = event.content
            yield event
    finally:
        if last_response:
            self._pending_turns.append((prompt, last_response))
            self._schedule_summary()

Add _schedule_summary() — skip-if-running to avoid corrupting Haiku session:
def _schedule_summary(self) -> None:
    if self._summary_task and not self._summary_task.done():
        return  # already running; new turns picked up by self-scheduling
    self._summary_task = asyncio.create_task(self._refresh_summary())

Add _refresh_summary() — incremental Haiku summarization with self-scheduling:
_SUMMARY_RESET_THRESHOLD = 30

async def _refresh_summary(self) -> None:
    """Summarize pending turns using Haiku (fire-and-forget)."""
    snapshot = list(self._pending_turns)
    if not snapshot:
        return

    # Reset summary session periodically to clear accumulated SDK history.
    # Stateless by design (previous summary passed each call), so no data loss.
    self._summary_call_count += 1
    if self._summary_call_count >= _SUMMARY_RESET_THRESHOLD:
        await self._summary_session.stop()
        await self._summary_session.start()
        self._summary_call_count = 0

    parts = []
    if self._context_summary:
        parts.append(f"Previous summary:\n{self._context_summary}")
    parts.append("New exchanges:")
    for prompt, response in snapshot:
        parts.append(f"User: {prompt}")
        parts.append(f"Assistant: {response}")
    try:
        summary = ""
        async for event in self._summary_session.send("\n".join(parts)):
            if isinstance(event, Response):
                summary = event.content
        if summary:
            self._context_summary = summary
            for _ in range(min(len(snapshot), len(self._pending_turns))):
                self._pending_turns.popleft()
            # Self-schedule if new turns arrived during this run
            if self._pending_turns:
                self._summary_task = asyncio.create_task(self._refresh_summary())
    except Exception:
        logger.warning("Context summarization failed, keeping turns in buffer", exc_info=True)
        # No self-scheduling on failure — prevents infinite retry loops.
        # Next answer() or track_context() call will schedule again.

Self-scheduling only after success: if new turns arrived during Haiku, they're picked up immediately without waiting for the next answer().

Add _await_pending_summary() — eliminates race condition on fast follow-ups:
_SUMMARY_WAIT_TIMEOUT = 3.0

async def _await_pending_summary(self) -> None:
    """Wait for in-flight summary to complete (with timeout)."""
    if self._summary_task and not self._summary_task.done():
        try:
            await asyncio.wait_for(
                asyncio.shield(self._summary_task), timeout=_SUMMARY_WAIT_TIMEOUT
            )
        except (asyncio.TimeoutError, asyncio.CancelledError):
            logger.debug("Summary wait timed out after %.1fs", _SUMMARY_WAIT_TIMEOUT)

asyncio.shield() prevents wait_for's internal cancellation from killing the summary task.

Add _build_orch_context() — Haiku summary only:
def _build_orch_context(self) -> str:
    if not self._context_summary:
        return ""
    return (
        "[Main-session context for routing:]\n"
        f"{self._context_summary}\n"
        "[End context]"
    )

Update review() and route_task() — await summary + embed context:
async def review(self, prompt, classification):
    await self._await_pending_summary()
    await self._reset_orch_if_needed()
    context = self._build_orch_context()
    context_block = f"\n\n{context}\n\n" if context else "\n\n"
    review_prompt = load_prompt("review")
    instruction = (
        f"[INTERNAL: pipeline orchestration — not a user message]"
        f"{context_block}"
        f"{review_prompt}\n\n"
        f"[Original classification: intent={classification.intent}, "
        f"confidence={classification.confidence}]\n\n"
        f"User message: {prompt}"
    )
    ...

Same pattern for route_task().

Add public track_context() — for pipeline and BackgroundAgentManager:
def track_context(self, prompt: str, summary: str) -> None:
    """Record a context entry from an external source (escalation, agent completion)."""
    self._pending_turns.append((prompt, summary))
    self._schedule_summary()

Also expose on Pipeline (same delegation pattern as inject_context and activate_skill):
def track_context(self, prompt: str, summary: str) -> None:
    self._decomposer.track_context(prompt, summary)


### Step 2: Task escalation context tracking

When a user message starts inline but gets escalated to a background agent (3+ tool calls detected), this event is invisible to the orch session. The next message's review/route_task has no idea a task was
escalated.

#### Tests (TDD):

19. test_escalation_records_context — after _task_direct_monitored() yields PromotionEvent, decomposer's _pending_turns contains the escalation record

Production (archon/ai/pipeline.py):

#### Implementation:

In _task_direct_monitored(), after yielding PromotionEvent:
yield PromotionEvent(...)
self._decomposer.track_context(
    prompt,
    f"[Task escalated to background agent after {tool_count} tool calls]",
)
return

### Step 3: Background agent completion context tracking

When a background agent finishes, neither session knows the result. The user sees it in Telegram, but if they say "looks good, deploy it", the orch session has no idea what they're referring to.

#### Tests (TDD):

20. test_agent_completion_tracks_context — after agent completes, session_manager.track_context() called
21. test_agent_failure_does_not_track_context — failed/cancelled agents don't record context

#### Production:

archon/ai/session_manager.py — add public method:
def track_context(self, user_id: int, prompt: str, summary: str) -> None:
    """Record context in the user's session for orchestration awareness."""
    session = self._sessions.get(user_id)
    if session is not None:
        session.track_context(prompt, summary)

archon/ai/background_agent_manager.py — in _run_agent(), after _notify_success(run):
await self._notify_success(run)
try:
    self._session_manager.track_context(
        run.user_id,
        run.user_request or run.task,
        f"[Background agent {run.name} completed — result already delivered]",
    )
    completion_ctx = (
        f"[BACKGROUND STATUS — do not echo, summarize, or mention to the user]\n"
        f"Background agent '{run.name}' completed successfully. "
        f"The full result was already delivered to the user via Telegram.\n"
        f"[END BACKGROUND STATUS]"
    )
    self._session_manager.inject_agent_context(run.user_id, completion_ctx)
except Exception:
    logger.warning("Failed to track agent completion context", exc_info=True)

### Step 4: Orch session reset

After many orchestration calls, the SDK's accumulated history grows. Periodically restart the orch session. No inject_context needed — _build_orch_context() embeds the summary in every instruction string,
so the fresh session gets context on its first call. _context_summary lives on the Decomposer object and survives resets.

#### Tests (TDD):

22. test_orch_reset_restarts_session_after_threshold — after 20 orch calls, session restarted
23. test_orch_reset_preserves_context_summary — _context_summary unchanged after reset
24. test_summary_session_resets_independently — summary session resets every 30 calls, decoupled from orch

#### Production (archon/ai/decomposer.py):

_ORCH_RESET_THRESHOLD = 20

# In __init__:
self._orch_call_count: int = 0
self._summary_call_count: int = 0

async def _reset_orch_if_needed(self) -> None:
    self._orch_call_count += 1
    if self._orch_call_count < _ORCH_RESET_THRESHOLD:
        return
    await self._orch_session.stop()
    await self._orch_session.start()
    self._orch_call_count = 0

Summary session reset is in _refresh_summary() (every 30 calls, shown in Step 1).


### Step 5: Run tests and mypy

uv run pytest tests/ai/test_decomposer.py tests/ai/test_pipeline.py tests/ai/test_background_agent_manager.py -v --override-ini='addopts='
uv run mypy archon/ai/decomposer.py archon/ai/pipeline.py archon/ai/background_agent_manager.py archon/ai/session_manager.py

## Architecture flow

answer() completes with Response
  → append (prompt, response) to _pending_turns buffer
  → fire-and-forget _refresh_summary() via Haiku
      → Haiku receives: previous summary + new turns
      → produces updated 2-3 sentence summary
      → _context_summary updated, summarized turns drained
      → if new turns arrived during Haiku → self-schedules another run

Task escalation (3+ tools → background agent)
  → pipeline calls decomposer.track_context(prompt, "[escalated...]")
  → appends to buffer → schedules Haiku

Background agent completes
  → BackgroundAgentManager calls session_manager.track_context()
  → delegates to Pipeline → Decomposer → appends to buffer → schedules Haiku

review() / route_task() called
  → _await_pending_summary() — waits up to 3s for in-flight Haiku
  → _reset_orch_if_needed() — restart orch session every 20 calls
  → _build_orch_context() → returns _context_summary (Haiku output)
  → summary embedded in instruction string sent to orch session

## Verification

- All existing decomposer + pipeline + BackgroundAgentManager tests pass (with updated helper)
- 24 new tests cover: turn tracking, skip-on-no-response, Haiku lifecycle, incremental summary, self-scheduling, failure resilience, skip-if-running, await-before-orch, context in review/route_task,
track_context API, escalation tracking, agent completion tracking, orch/summary session resets
- Full test suite: uv run pytest --override-ini='addopts=' -m 'not live'
- mypy clean: uv run mypy archon/
