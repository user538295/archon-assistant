"""Live e2e test: chat shifting bug reproduction.

Verifies that after tool-count promotion, the next user message is processed
cleanly — not contaminated by the promoted task's SDK conversation history.

Run with:
  env -u CLAUDECODE uv run pytest tests/ai/test_chat_shifting_live.py -m live -v -s
"""

import asyncio
import os
import shutil

import pytest

from archon.ai.event_mapper import (
    Event,
    PromotionEvent,
    Response,
    ToolResult,
    ToolStarted,
)
from archon.ai.pipeline import Pipeline

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        shutil.which("claude") is None,
        reason="claude binary not found in PATH",
    ),
    pytest.mark.skipif(
        bool(os.environ.get("CLAUDECODE")),
        reason="Cannot start nested Claude sessions (CLAUDECODE env set)",
    ),
]

_TIMEOUT = 180.0  # generous timeout for real SDK calls


async def _collect_until_done(
    pipeline: Pipeline, prompt: str
) -> list[Event]:
    """Collect all events from pipeline.send(), with a timeout."""
    events: list[Event] = []
    async with asyncio.timeout(_TIMEOUT):
        async for event in pipeline.send(prompt):
            events.append(event)
    return events


async def _run_in_task(coro):
    """Run a coroutine in a separate asyncio task.

    Each Telegram message handler runs in its own asyncio task in
    production.  Using separate tasks here matches that model and
    isolates anyio cancel-scope state between steps.
    """
    return await asyncio.create_task(coro)


@pytest.mark.live
async def test_chat_shifting_bug_reproduction() -> None:
    """Live e2e: after tool-count promotion, the next message must not
    continue the promoted task.

    Step 1: Send a prompt that triggers 3+ tool calls -> promotion.
    Step 2: Send 'Say exactly: pong' -> must get 'pong', not file operations.
    """
    # Low threshold for faster promotion
    pipeline = Pipeline(tool_promotion_threshold=3, model="claude-sonnet-4-6")
    await pipeline.start()

    try:
        # ── Step 1: Trigger promotion ───────────────────────────────
        # Run in a separate task (matches production: each Telegram
        # message is a separate aiogram handler task).
        events1 = await _run_in_task(
            _collect_until_done(
                pipeline,
                "Read these files one by one: archon/version.py, "
                "archon/__init__.py, archon/ai/__init__.py, archon/ai/constants.py. "
                "Summarize each file's contents.",
            )
        )

        promotions = [e for e in events1 if isinstance(e, PromotionEvent)]
        tools = [e for e in events1 if isinstance(e, ToolStarted)]
        print(f"\nStep 1: {len(tools)} tool calls, promotion={'YES' if promotions else 'NO'}")
        for t in tools:
            print(f"  Tool: {t.name}({t.input[:60]})")

        if not promotions:
            pytest.skip(
                f"Promotion did not trigger (got {len(tools)} tool calls, "
                f"need 3). Events: {[type(e).__name__ for e in events1]}"
            )

        # ── Step 2: Send a completely different follow-up ───────────
        events2 = await _run_in_task(
            _collect_until_done(
                pipeline,
                "Say exactly: pong",
            )
        )

        responses2 = [e for e in events2 if isinstance(e, Response)]
        tools2 = [e for e in events2 if isinstance(e, ToolStarted)]
        print(f"\nStep 2: {len(tools2)} tool calls, {len(responses2)} responses")
        for r in responses2:
            print(f"  Response: {r.content[:200]}")
        for t in tools2:
            print(f"  Tool: {t.name}({t.input[:60]})")

        # ── Assertions ──────────────────────────────────────────────
        assert responses2, "No response to follow-up message"
        response_text = responses2[-1].content.lower()

        # Contamination signals: response talks about files instead of saying "pong"
        contamination_keywords = ["version.py", "__init__", "constants.py", "summarize"]
        is_contaminated = any(kw in response_text for kw in contamination_keywords)

        assert "pong" in response_text or not is_contaminated, (
            f"CHAT SHIFTING BUG: follow-up 'Say exactly: pong' got contaminated response.\n"
            f"Response: {responses2[-1].content[:500]}\n"
            f"Tools used on follow-up: {[t.name for t in tools2]}\n"
            f"Expected 'pong', got file-related content instead."
        )

    finally:
        await _run_in_task(pipeline.stop())
