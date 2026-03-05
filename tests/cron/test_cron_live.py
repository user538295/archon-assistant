"""Live integration tests for CronScheduler — real subprocess, real asyncio.

Run manually with:
  uv run pytest tests/cron/test_cron_live.py -m live -v -s

These tests are excluded from the default CI run (marker: live).
The 'test_live_cron_fires_within_90_seconds' test schedules a '* * * * *' job,
starts the scheduler, and waits for the job to fire (first tick is immediate).
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from archon.ai.cron_scheduler import CronScheduler
from archon.config.loader import CronConfig, CronJobConfig, CronPipelineStep


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


@pytest.mark.live
async def test_live_tool_step_echo() -> None:
    """A simple echo tool step returns the correct output."""
    cfg = CronConfig(
        enabled=True,
        jobs=[CronJobConfig(name="echo_test", schedule="* * * * *", pipeline=[])],
    )
    scheduler = CronScheduler(cfg, _make_bot(), allowed_user_ids=[])
    step = CronPipelineStep(tool="echo hello from cron")
    result = await scheduler._run_tool(step, "", timeout=10.0)
    assert result == "hello from cron"


@pytest.mark.live
async def test_live_tool_step_stdin_piping() -> None:
    """Tool step receives stdin from the previous step."""
    cfg = CronConfig(
        enabled=True,
        jobs=[CronJobConfig(name="cat_test", schedule="* * * * *", pipeline=[])],
    )
    scheduler = CronScheduler(cfg, _make_bot(), allowed_user_ids=[])
    step = CronPipelineStep(tool="cat")
    result = await scheduler._run_tool(step, "piped data", timeout=10.0)
    assert result == "piped data"


@pytest.mark.live
async def test_live_pipeline_tool_chaining() -> None:
    """Multi-step pipeline: echo → cat (stdin passthrough)."""
    bot = _make_bot()
    job = CronJobConfig(
        name="chain_test",
        schedule="* * * * *",
        pipeline=[
            CronPipelineStep(tool="echo step_one_output"),
            CronPipelineStep(tool="cat"),
        ],
    )
    cfg = CronConfig(enabled=True, jobs=[job])
    scheduler = CronScheduler(cfg, bot, allowed_user_ids=[])
    await scheduler._run_job(job)
    status = scheduler.job_statuses["chain_test"]
    assert status.last_result == "step_one_output"
    assert status.run_count == 1
    assert status.is_running is False


@pytest.mark.live
async def test_live_prompt_step_executes() -> None:
    """A real Claude session returns a non-empty response for a prompt step."""
    cfg = CronConfig(
        enabled=True,
        jobs=[CronJobConfig(name="prompt_test", schedule="* * * * *", pipeline=[])],
    )
    scheduler = CronScheduler(cfg, _make_bot(), allowed_user_ids=[])
    step = CronPipelineStep(prompt="Reply with exactly one word: hello")
    result = await scheduler._run_prompt(step, "", timeout=60.0)
    assert len(result) > 0
    assert "hello" in result.lower()


@pytest.mark.live
async def test_live_cron_fires_within_90_seconds() -> None:
    """Full scheduler with '* * * * *' fires on first tick and notifies.

    The scheduler loop checks immediately on start. For '* * * * *', the
    previous cron slot is always within the last 60 seconds, so the job fires
    on the FIRST loop tick (within a second of start). Timeout is 90s for safety.
    """
    fired = asyncio.Event()
    received_messages: list[str] = []

    bot = MagicMock()

    async def _capture_notify(chat_id: int, text: str, **kwargs: object) -> None:
        received_messages.append(text)
        fired.set()

    bot.send_message = _capture_notify

    cfg = CronConfig(
        enabled=True,
        jobs=[
            CronJobConfig(
                name="live_cron_test",
                schedule="* * * * *",
                pipeline=[CronPipelineStep(tool="echo hello from cron")],
                timeout_seconds=10.0,
            )
        ],
    )
    scheduler = CronScheduler(cfg, bot, allowed_user_ids=[])
    await scheduler.start()
    try:
        await asyncio.wait_for(fired.wait(), timeout=90.0)
    finally:
        await scheduler.stop()

    assert len(received_messages) >= 1
    assert "hello from cron" in received_messages[0]
    status = scheduler.job_statuses["live_cron_test"]
    assert status.run_count >= 1
    assert status.last_result == "hello from cron"
    assert status.is_running is False


@pytest.mark.live
async def test_live_job_status_fully_populated_after_run() -> None:
    """After a job runs, all status fields are set correctly."""
    job = CronJobConfig(
        name="status_test",
        schedule="* * * * *",
        pipeline=[CronPipelineStep(tool="echo status_ok")],
    )
    cfg = CronConfig(enabled=True, jobs=[job])
    scheduler = CronScheduler(cfg, _make_bot(), allowed_user_ids=[])
    await scheduler._run_job(job)
    status = scheduler.job_statuses["status_test"]
    assert status.run_count == 1
    assert status.last_run is not None
    assert status.last_result == "status_ok"
    assert status.last_error is None
    assert status.is_running is False
