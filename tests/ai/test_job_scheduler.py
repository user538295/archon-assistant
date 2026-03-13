"""Tests for JobScheduler — task observability fixes."""
import asyncio
import logging
import os
import stat
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.job_scheduler import JobScheduler, _log_task_exception
from archon.config.loader import ScheduleConfig, ScheduledJobConfig, SchedulePipelineStep


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_job(name: str = "test_job", cron: str = "* * * * *") -> ScheduledJobConfig:
    return ScheduledJobConfig(
        name=name,
        cron=cron,
        pipeline=[SchedulePipelineStep(name="step_tool", kind="tool", value="echo hi")],
    )


def _make_scheduler(jobs: list[ScheduledJobConfig] | None = None) -> JobScheduler:
    config = ScheduleConfig(enabled=True, jobs=jobs or [])
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return JobScheduler(config=config, bot=bot, allowed_user_ids=[1])


# ──────────────────────────────────────────────────────────────────
# _log_task_exception helper
# ──────────────────────────────────────────────────────────────────


async def test_log_task_exception_logs_error_on_failure(caplog: pytest.LogCaptureFixture) -> None:
    """An exception inside a scheduled task must be logged at ERROR level."""
    async def _fail() -> None:
        raise RuntimeError("boom")

    task = asyncio.create_task(_fail())
    with pytest.raises(Exception):
        await asyncio.shield(task)

    with caplog.at_level(logging.ERROR, logger="archon"):
        _log_task_exception(task, "my_job")

    assert any("my_job" in r.message and r.levelno == logging.ERROR for r in caplog.records)


async def test_log_task_exception_silent_on_cancelled() -> None:
    """A cancelled task must NOT produce an error log."""
    async def _wait() -> None:
        await asyncio.sleep(100)

    task = asyncio.create_task(_wait())
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    records: list[logging.LogRecord] = []
    handler = logging.handlers_mock = MagicMock()

    import logging as _logging
    archon_logger = _logging.getLogger("archon")
    original_handlers = archon_logger.handlers[:]
    captured: list[logging.LogRecord] = []

    class _Cap(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    cap = _Cap()
    archon_logger.addHandler(cap)
    try:
        _log_task_exception(task, "my_job")
    finally:
        archon_logger.removeHandler(cap)

    assert not any(r.levelno >= logging.ERROR for r in captured)


# ──────────────────────────────────────────────────────────────────
# Task tracking: _tasks set
# ──────────────────────────────────────────────────────────────────


async def test_task_added_to_tasks_set_and_removed_on_completion() -> None:
    """Tasks spawned by _loop are tracked in _tasks and auto-removed when done."""
    job = _make_job()
    scheduler = _make_scheduler([job])

    # Patch _run_job to a fast coroutine
    async def _fast_job(_job: ScheduledJobConfig) -> None:
        return

    scheduler._run_job = _fast_job  # type: ignore[method-assign]

    # Manually simulate what _loop does
    task = asyncio.create_task(scheduler._run_job(job), name="schedule-test")
    scheduler._tasks.add(task)
    task.add_done_callback(scheduler._tasks.discard)
    task.add_done_callback(
        lambda t, name=job.name: _log_task_exception(t, name)
    )

    assert task in scheduler._tasks
    await task
    # discard callback fires synchronously after task completes
    await asyncio.sleep(0)  # allow callbacks to run
    assert task not in scheduler._tasks


# ──────────────────────────────────────────────────────────────────
# Integration: exception in _run_job surfaces via done-callback log
# ──────────────────────────────────────────────────────────────────


async def test_exception_in_run_job_is_logged_via_callback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If _run_job raises (bypassing internal try/except), the callback logs it."""
    job = _make_job()
    scheduler = _make_scheduler([job])

    async def _exploding_job(_job: ScheduledJobConfig) -> None:
        raise RuntimeError("unexpected crash")

    scheduler._run_job = _exploding_job  # type: ignore[method-assign]

    with caplog.at_level(logging.ERROR, logger="archon"):
        task = asyncio.create_task(scheduler._run_job(job))
        scheduler._tasks.add(task)
        task.add_done_callback(scheduler._tasks.discard)
        task.add_done_callback(
            lambda t, name=job.name: _log_task_exception(t, name)
        )
        # Await without raising so callback fires
        try:
            await task
        except RuntimeError:
            pass
        await asyncio.sleep(0)

    assert any(
        "test_job" in r.message and r.levelno == logging.ERROR
        for r in caplog.records
    )


# ──────────────────────────────────────────────────────────────────
# asyncio.get_running_loop() — no DeprecationWarning
# ──────────────────────────────────────────────────────────────────


async def test_disable_invalid_job_uses_running_loop(tmp_path: pytest.TempPathFactory) -> None:
    """_disable_invalid_job must not raise when called from a running event loop."""
    job = _make_job()
    job.validation_error = "bad config"
    scheduler = _make_scheduler([job])
    scheduler._jobs_dir_base = tmp_path  # type: ignore[assignment]

    # No TOML file on disk — should be a safe no-op without warnings
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        await scheduler._disable_invalid_job(job)  # must not raise DeprecationWarning


# ──────────────────────────────────────────────────────────────────
# Issue A: _loop tick body wrapped in try/except
# ──────────────────────────────────────────────────────────────────


async def test_loop_continues_after_tick_exception(caplog: pytest.LogCaptureFixture) -> None:
    """A transient exception during a tick must be logged and the loop must continue."""
    job = _make_job()
    scheduler = _make_scheduler([job])

    tick_count = 0

    async def _fake_sleep(delay: float) -> None:
        nonlocal tick_count
        tick_count += 1
        if tick_count >= 2:
            raise asyncio.CancelledError

    original_should_fire = scheduler._should_fire

    call_count = 0

    def _raise_on_first(j: ScheduledJobConfig, now: datetime) -> bool:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("transient tick error")
        return False

    scheduler._should_fire = _raise_on_first  # type: ignore[method-assign]

    with caplog.at_level(logging.ERROR, logger="archon"):
        with patch("asyncio.sleep", side_effect=_fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await scheduler._loop()

    # Loop ran at least 2 ticks (did not die after first exception)
    assert tick_count >= 2
    assert any("Scheduler tick failed" in r.message for r in caplog.records)


# ──────────────────────────────────────────────────────────────────
# Issue B: jobs_dir world-writable warning
# ──────────────────────────────────────────────────────────────────


def test_world_writable_jobs_dir_logs_warning(
    tmp_path: pytest.TempPathFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """Creating a scheduler with a world-writable jobs_dir must log a security warning."""
    jobs_dir = tmp_path / "jobs"  # type: ignore[operator]
    jobs_dir.mkdir()
    # Make directory world-writable
    current_mode = jobs_dir.stat().st_mode
    jobs_dir.chmod(current_mode | stat.S_IWOTH)

    try:
        with caplog.at_level(logging.WARNING, logger="archon"):
            _make_scheduler_with_jobs_dir(jobs_dir_base=jobs_dir)

        assert any(
            "world-writable" in r.message and "security risk" in r.message
            for r in caplog.records
        )
    finally:
        # Restore permissions so tmp_path cleanup works
        jobs_dir.chmod(current_mode)


def test_non_world_writable_jobs_dir_no_warning(
    tmp_path: pytest.TempPathFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """A jobs_dir that is NOT world-writable must not produce a security warning."""
    jobs_dir = tmp_path / "jobs"  # type: ignore[operator]
    jobs_dir.mkdir()
    # Ensure world-write bit is NOT set
    current_mode = jobs_dir.stat().st_mode
    jobs_dir.chmod(current_mode & ~stat.S_IWOTH)

    with caplog.at_level(logging.WARNING, logger="archon"):
        _make_scheduler_with_jobs_dir(jobs_dir_base=jobs_dir)

    assert not any("world-writable" in r.message for r in caplog.records)


def _make_scheduler_with_jobs_dir(jobs_dir_base: object) -> JobScheduler:
    config = ScheduleConfig(enabled=True, jobs=[])
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return JobScheduler(
        config=config,
        bot=bot,
        allowed_user_ids=[1],
        jobs_dir_base=jobs_dir_base,  # type: ignore[arg-type]
    )


# ──────────────────────────────────────────────────────────────────
# Issue C: _should_fire — naive datetime from get_prev gets tzinfo
# ──────────────────────────────────────────────────────────────────


def test_should_fire_handles_naive_get_prev(caplog: pytest.LogCaptureFixture) -> None:
    """If croniter.get_prev() returns a naive datetime, _should_fire must not raise TypeError."""
    from zoneinfo import ZoneInfo

    job = _make_job()
    job.timezone = "UTC"
    scheduler = _make_scheduler([job])

    tz = ZoneInfo("UTC")
    naive_dt = datetime(2024, 1, 1, 9, 0, 0)  # no tzinfo
    assert naive_dt.tzinfo is None

    with patch("archon.ai.job_scheduler.croniter") as mock_croniter:
        mock_it = MagicMock()
        mock_it.get_prev.return_value = naive_dt
        mock_croniter.return_value = mock_it

        now = datetime.now(timezone.utc).astimezone()
        # Must not raise — returns False because the naive prev is far in the past
        result = scheduler._should_fire(job, now)

    assert isinstance(result, bool)


def test_should_fire_logs_warning_on_type_error(caplog: pytest.LogCaptureFixture) -> None:
    """A TypeError in _should_fire must be caught, logged as warning, and return False."""
    job = _make_job()
    scheduler = _make_scheduler([job])

    with patch("archon.ai.job_scheduler.croniter") as mock_croniter:
        mock_it = MagicMock()
        mock_it.get_prev.side_effect = TypeError("comparison error")
        mock_croniter.return_value = mock_it

        now = datetime.now(timezone.utc).astimezone()
        with caplog.at_level(logging.WARNING, logger="archon"):
            result = scheduler._should_fire(job, now)

    assert result is False
    assert any("Invalid cron expression" in r.message for r in caplog.records)


# ──────────────────────────────────────────────────────────────────
# Issue D: _run_prompt passes cwd to ClaudeSession
# ──────────────────────────────────────────────────────────────────


async def test_run_prompt_passes_cwd_to_claude_session() -> None:
    """_run_prompt must pass cwd=self._cwd to ClaudeSession."""
    from archon.ai.event_mapper import Response

    config = ScheduleConfig(enabled=True, jobs=[])
    bot = MagicMock()
    bot.send_message = AsyncMock()
    scheduler = JobScheduler(
        config=config,
        bot=bot,
        allowed_user_ids=[1],
        cwd="/my/working/dir",
    )

    captured_kwargs: dict = {}

    class _FakeSession:
        def __init__(self, **kwargs: object) -> None:
            captured_kwargs.update(kwargs)

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

        async def send(self, prompt: str):  # type: ignore[override]
            yield Response(content="done")

    with patch("archon.ai.job_scheduler.ClaudeSession", side_effect=_FakeSession):
        result = await scheduler._run_prompt("hello", timeout=5.0)

    assert captured_kwargs.get("cwd") == "/my/working/dir"
    assert result == "done"


def test_should_fire_logs_warning_on_value_error(caplog: pytest.LogCaptureFixture) -> None:
    """A ValueError in _should_fire must be caught, logged as warning, and return False."""
    job = _make_job()
    scheduler = _make_scheduler([job])

    with patch("archon.ai.job_scheduler.croniter") as mock_croniter:
        mock_croniter.side_effect = ValueError("bad schedule expression")

        now = datetime.now(timezone.utc).astimezone()
        with caplog.at_level(logging.WARNING, logger="archon"):
            result = scheduler._should_fire(job, now)

    assert result is False
    assert any("Invalid cron expression" in r.message for r in caplog.records)
