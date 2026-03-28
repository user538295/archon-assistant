"""Unit tests for JobScheduler — mocked subprocess and Claude session."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.job_scheduler import JobScheduler, JobStatus, _ScheduleJobLogWriter, _substitute_refs
from archon.ai.event_mapper import Response, ToolResult
from archon.config.loader import HistoryConfig, NotificationsConfig, ScheduleConfig, ScheduledJobConfig, SchedulePipelineStep


# ── Helpers ───────────────────────────────────────────────────────


def _make_job(
    name: str = "test_job",
    cron: str = "* * * * *",
    pipeline: list | None = None,
    timeout_seconds: float = 30.0,
    enabled: bool = True,
    timezone: str | None = None,
    validation_error: str | None = None,
    source_dir: Path | None = None,
) -> ScheduledJobConfig:
    if pipeline is None:
        pipeline = [SchedulePipelineStep(name="echo_tool", kind="tool", value="echo hello")]
    return ScheduledJobConfig(
        name=name,
        cron=cron,
        pipeline=pipeline,
        timeout_seconds=timeout_seconds,
        enabled=enabled,
        timezone=timezone,
        validation_error=validation_error,
        source_dir=source_dir,
    )


def _make_config(*jobs: ScheduledJobConfig, enabled: bool = True) -> ScheduleConfig:
    return ScheduleConfig(enabled=enabled, jobs=list(jobs))


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


def _make_scheduler(
    config: ScheduleConfig,
    bot: MagicMock | None = None,
    allowed_user_ids: list[int] | None = None,
    **kwargs: object,
) -> JobScheduler:
    return JobScheduler(
        config,
        bot or _make_bot(),
        allowed_user_ids=allowed_user_ids or [],
        **kwargs,
    )


# ── New params: notifications + history_config ───────────────────


class TestJobSchedulerNewParams:
    def test_scheduler_accepts_notifications_and_history_config(self) -> None:
        cfg = _make_config(_make_job())
        notifications = NotificationsConfig()
        history_config = HistoryConfig()
        scheduler = _make_scheduler(cfg, notifications=notifications, history_config=history_config)
        assert scheduler._notifications is notifications
        assert scheduler._history_config is history_config

    def test_scheduler_new_params_default_to_none(self) -> None:
        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg)
        assert scheduler._notifications is None
        assert scheduler._history_config is None

    def test_scheduler_backward_compatible_with_all_existing_kwargs(self) -> None:
        """Gateway call pattern still works without new params."""
        import tempfile
        cfg = _make_config(_make_job())
        with tempfile.TemporaryDirectory() as tmpdir:
            scheduler = JobScheduler(
                config=cfg,
                bot=None,
                allowed_user_ids=[123],
                model="claude-sonnet-4-5",
                jobs_dir_base=tmpdir,
                cwd=tmpdir,
                # notifications and history_config intentionally omitted
            )
            assert scheduler._notifications is None
            assert scheduler._history_config is None


# ── Start / Stop ──────────────────────────────────────────────────


class TestJobSchedulerLifecycle:
    async def test_start_disabled_creates_no_task(self) -> None:
        cfg = _make_config(_make_job(), enabled=False)
        scheduler = _make_scheduler(cfg)
        await scheduler.start()
        assert scheduler._task is None

    async def test_start_enabled_creates_task(self) -> None:
        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg)

        async def _noop_loop() -> None:
            await asyncio.sleep(999)

        with patch.object(scheduler, "_loop", side_effect=_noop_loop):
            await scheduler.start()
        assert scheduler._task is not None
        scheduler._task.cancel()
        try:
            await scheduler._task
        except asyncio.CancelledError:
            pass

    async def test_stop_cancels_running_task(self) -> None:
        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg)

        async def _slow_loop() -> None:
            await asyncio.sleep(999)

        with patch.object(scheduler, "_loop", side_effect=_slow_loop):
            await scheduler.start()
        await scheduler.stop()
        assert scheduler._task is not None and scheduler._task.done()

    async def test_stop_when_not_started_is_safe(self) -> None:
        cfg = _make_config(_make_job(), enabled=False)
        scheduler = _make_scheduler(cfg)
        await scheduler.start()  # no-op (disabled)
        await scheduler.stop()  # should not raise

    async def test_stop_cancels_running_job_tasks(self) -> None:
        """stop() must cancel active job tasks — not just the tick loop."""
        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg)

        job_started = asyncio.Event()

        async def _slow_job() -> None:
            job_started.set()
            await asyncio.sleep(999)

        # Inject a long-running job task directly into _tasks (simulates a job
        # that was fired by the tick loop and is still executing).
        job_task: asyncio.Task[None] = asyncio.create_task(_slow_job())
        scheduler._tasks.add(job_task)

        # Wait until the task is actually running before we stop.
        await job_started.wait()

        await scheduler.stop()

        assert job_task.done(), "running job task should be cancelled by stop()"
        assert job_task.cancelled(), "job task should be cancelled, not finished normally"


# ── _should_fire ──────────────────────────────────────────────────


class TestShouldFire:
    def test_should_fire_true_when_cron_matches_now(self) -> None:
        """'* * * * *' fires every minute — should be True within 60s of prev tick."""
        cfg = _make_config(_make_job(cron="* * * * *"))
        scheduler = _make_scheduler(cfg)
        test_time = datetime(2025, 1, 1, 12, 0, 5)  # 5 seconds after minute boundary
        assert scheduler._should_fire(cfg.jobs[0], test_time) is True

    def test_should_fire_false_for_non_matching_expression(self) -> None:
        """'0 0 1 1 *' (Jan 1 midnight) should not fire on a random summer day."""
        cfg = _make_config(_make_job(cron="0 0 1 1 *"))
        scheduler = _make_scheduler(cfg)
        test_time = datetime(2025, 6, 15, 12, 30, 0)
        assert scheduler._should_fire(cfg.jobs[0], test_time) is False

    def test_should_fire_false_when_already_fired_this_slot(self) -> None:
        """Should not fire again if last_fire_at >= previous cron slot."""
        cfg = _make_config(_make_job(cron="* * * * *"))
        scheduler = _make_scheduler(cfg)
        test_time = datetime(2025, 1, 1, 12, 0, 5)
        # Simulate already having fired at 12:00:03
        scheduler._statuses["test_job"].last_fire_at = datetime(2025, 1, 1, 12, 0, 3)
        assert scheduler._should_fire(cfg.jobs[0], test_time) is False

    def test_should_fire_invalid_expression_returns_false(self) -> None:
        """Bad cron expressions must not crash — return False."""
        cfg = _make_config(_make_job(cron="not_a_cron"))
        scheduler = _make_scheduler(cfg)
        assert scheduler._should_fire(cfg.jobs[0], datetime.now()) is False

    def test_should_fire_true_after_different_slot(self) -> None:
        """Fire again in the next minute even if already fired in the previous minute."""
        cfg = _make_config(_make_job(cron="* * * * *"))
        scheduler = _make_scheduler(cfg)
        # last_fire_at was in the previous minute (12:01:xx), now it's 12:02:05
        scheduler._statuses["test_job"].last_fire_at = datetime(2025, 1, 1, 12, 1, 30)
        test_time = datetime(2025, 1, 1, 12, 2, 5)
        assert scheduler._should_fire(cfg.jobs[0], test_time) is True


# ── _should_fire with timezone ────────────────────────────────────


class TestShouldFireWithTimezone:
    def test_should_fire_utc_timezone_every_minute(self) -> None:
        """'* * * * *' with timezone='UTC' fires — UTC is always a valid zone."""
        job = _make_job(cron="* * * * *", timezone="UTC")
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)
        assert scheduler._should_fire(cfg.jobs[0], datetime.now()) is True

    def test_should_fire_with_iana_timezone(self) -> None:
        """Any IANA timezone fires '* * * * *' as expected."""
        job = _make_job(cron="* * * * *", timezone="Europe/Budapest")
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)
        assert scheduler._should_fire(cfg.jobs[0], datetime.now()) is True

    def test_should_fire_false_when_already_fired_this_slot_with_timezone(self) -> None:
        """Does not fire twice in the same cron slot even with a timezone set."""
        job = _make_job(cron="* * * * *", timezone="UTC")
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)
        # Set last_fire_at to a very recent local-naive time (within the current minute)
        scheduler._statuses["test_job"].last_fire_at = datetime.now()
        assert scheduler._should_fire(cfg.jobs[0], datetime.now()) is False

    def test_should_fire_invalid_timezone_returns_false(self) -> None:
        """An unrecognised IANA timezone name is caught and returns False."""
        job = _make_job(cron="* * * * *", timezone="Not/A_Real_Zone")
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)
        assert scheduler._should_fire(cfg.jobs[0], datetime.now()) is False

    def test_next_run_times_tz_aware_for_timezone_job(self) -> None:
        """next_run_times() returns a timezone-aware datetime for a job with timezone."""
        job = _make_job(cron="0 9 * * *", timezone="Europe/Budapest")
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)
        times = scheduler.next_run_times()
        next_dt = times[job.name]
        assert next_dt is not None
        assert next_dt.tzinfo is not None  # must be tz-aware

    def test_next_run_times_aware_for_job_without_timezone(self) -> None:
        """next_run_times() returns a timezone-aware datetime even for a job without timezone."""
        job = _make_job(cron="0 9 * * *")  # no timezone
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)
        times = scheduler.next_run_times()
        next_dt = times[job.name]
        assert next_dt is not None
        assert next_dt.tzinfo is not None  # always tz-aware (local system timezone)

    def test_next_run_times_invalid_timezone_returns_none(self) -> None:
        """next_run_times() maps to None when the timezone is invalid."""
        job = _make_job(cron="0 9 * * *", timezone="Bogus/Zone")
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)
        times = scheduler.next_run_times()
        assert times[job.name] is None

    def test_next_run_times_returns_none_for_invalid_job(self) -> None:
        """next_run_times() maps to None for a job with validation_error set."""
        job = _make_job(validation_error="step 'bad' has no recognized suffix")
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)
        times = scheduler.next_run_times()
        assert times[job.name] is None


# ── _run_tool ─────────────────────────────────────────────────────


class TestRunTool:
    async def test_run_tool_returns_stdout(self) -> None:
        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg)
        result = await scheduler._run_tool("echo hello", timeout=10.0)
        assert result == "hello"

    async def test_run_tool_timeout_raises_runtime_error(self) -> None:
        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg)
        with pytest.raises(RuntimeError, match="timed out"):
            await scheduler._run_tool("sleep 999", timeout=0.05)

    async def test_run_tool_nonzero_exit_raises_runtime_error(self) -> None:
        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg)
        with pytest.raises(RuntimeError, match="exit 1"):
            await scheduler._run_tool("bash -c 'exit 1'", timeout=10.0)

    async def test_run_tool_uses_explicit_cwd(self, tmp_path: Path) -> None:
        """Tool subprocess runs in the explicitly provided cwd."""
        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg)
        result = await scheduler._run_tool("pwd", timeout=10.0, cwd=str(tmp_path))
        assert result == str(tmp_path)

    async def test_run_tool_no_cwd_inherits_process_directory(self) -> None:
        """When cwd=None the subprocess inherits the process working directory."""
        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg)
        assert scheduler._cwd is None
        result = await scheduler._run_tool("echo ok", timeout=10.0)
        assert result == "ok"

    async def test_run_tool_empty_stdin(self) -> None:
        """Tool receives empty stdin (new model uses {ref} for data passing)."""
        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg)
        result = await scheduler._run_tool("echo no_stdin", timeout=10.0)
        assert result == "no_stdin"

    async def test_run_tool_with_bundle_cwd_resolves_relative_scripts(self, tmp_path: Path) -> None:
        """Relative script paths resolve against the provided cwd (bundle dir)."""
        bundle = tmp_path / "schedules" / "my-bundle"
        scripts = bundle / "scripts"
        scripts.mkdir(parents=True)
        script = scripts / "greet.sh"
        script.write_text("#!/usr/bin/env bash\necho hi from bundle\n")
        script.chmod(0o755)

        cfg = _make_config(_make_job(source_dir=bundle))
        scheduler = _make_scheduler(cfg, jobs_dir_base=str(tmp_path))
        result = await scheduler._run_tool("scripts/greet.sh", timeout=10.0, cwd=str(bundle))
        assert result == "hi from bundle"


# ── _resolve_tool_cwd ─────────────────────────────────────────────


class TestResolveToolCwd:
    def test_source_dir_takes_priority(self, tmp_path: Path) -> None:
        bundle = tmp_path / "bundle"
        bundle.mkdir()
        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg, jobs_dir_base=str(tmp_path), cwd="/some/cwd")
        assert scheduler._resolve_tool_cwd(source_dir=bundle) == str(bundle)

    def test_falls_back_to_jobs_dir_base(self, tmp_path: Path) -> None:
        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg, jobs_dir_base=str(tmp_path), cwd="/some/cwd")
        assert scheduler._resolve_tool_cwd() == str(tmp_path)

    def test_falls_back_to_cwd(self) -> None:
        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg, cwd="/fallback")
        assert scheduler._resolve_tool_cwd() == "/fallback"

    def test_returns_none_without_any_cwd(self) -> None:
        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg)
        assert scheduler._resolve_tool_cwd() is None


# ── _run_prompt ───────────────────────────────────────────────────


class TestRunPrompt:
    async def test_run_prompt_returns_response_text(self) -> None:
        from archon.ai.event_mapper import Response

        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg)

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.stop = AsyncMock()

        async def _mock_send(prompt: str):  # type: ignore[return]
            yield Response(content="mocked response")

        mock_session.send = _mock_send

        with patch("archon.ai.job_scheduler.ClaudeSession", return_value=mock_session):
            result = await scheduler._run_prompt("Say: hello world", timeout=30.0)
        assert result == "mocked response"

    async def test_run_prompt_sends_prompt_text_directly(self) -> None:
        from archon.ai.event_mapper import Response

        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg)
        captured: list[str] = []

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.stop = AsyncMock()

        async def _mock_send(prompt: str):  # type: ignore[return]
            captured.append(prompt)
            yield Response(content="summary")

        mock_session.send = _mock_send

        with patch("archon.ai.job_scheduler.ClaudeSession", return_value=mock_session):
            await scheduler._run_prompt("Summarize: hello world", timeout=30.0)
        assert captured == ["Summarize: hello world"]

    async def test_run_prompt_stops_session_on_completion(self) -> None:
        from archon.ai.event_mapper import Response

        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg)

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.stop = AsyncMock()

        async def _mock_send(prompt: str):  # type: ignore[return]
            yield Response(content="ok")

        mock_session.send = _mock_send

        with patch("archon.ai.job_scheduler.ClaudeSession", return_value=mock_session):
            await scheduler._run_prompt("test", timeout=30.0)
        mock_session.stop.assert_awaited_once()

    async def test_run_prompt_raises_on_timeout(self) -> None:
        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg)

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.stop = AsyncMock()

        async def _slow_send(prompt: str):  # type: ignore[return]
            await asyncio.sleep(999)
            yield  # never reached

        mock_session.send = _slow_send

        with patch("archon.ai.job_scheduler.ClaudeSession", return_value=mock_session):
            with pytest.raises(RuntimeError, match="timed out"):
                await scheduler._run_prompt("test prompt", timeout=0.05)
        mock_session.stop.assert_awaited_once()


# ── _run_prompt log_writer ────────────────────────────────────────


class TestRunPromptLogWriter:
    async def test_run_prompt_passes_events_to_log_writer(self) -> None:
        from archon.ai.event_mapper import Response, ToolStarted

        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg)

        event1 = ToolStarted(name="bash", input={"command": "ls"})
        event2 = Response(content="done")

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.stop = AsyncMock()

        async def _mock_send(prompt: str):  # type: ignore[return]
            yield event1
            yield event2

        mock_session.send = _mock_send

        log_writer = MagicMock()
        log_writer.record_event = AsyncMock()

        with patch("archon.ai.job_scheduler.ClaudeSession", return_value=mock_session):
            await scheduler._run_prompt("hello", timeout=30.0, log_writer=log_writer)

        assert log_writer.record_event.await_count == 2
        log_writer.record_event.assert_any_await(event1)
        log_writer.record_event.assert_any_await(event2)

    async def test_run_prompt_none_log_writer_still_works(self) -> None:
        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg)

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.stop = AsyncMock()

        async def _mock_send(prompt: str):  # type: ignore[return]
            yield Response(content="result text")

        mock_session.send = _mock_send

        with patch("archon.ai.job_scheduler.ClaudeSession", return_value=mock_session):
            result = await scheduler._run_prompt("hello", timeout=30.0, log_writer=None)

        assert result == "result text"

    async def test_run_prompt_log_writer_receives_all_event_types(self) -> None:
        from archon.ai.event_mapper import ThinkingResult, ToolStarted, ToolResult, Response

        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg)

        e1 = ThinkingResult(content="thinking...")
        e2 = ToolStarted(name="bash", input={"command": "ls"})
        e3 = ToolResult(content="file.txt")
        e4 = Response(content="all done")

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.stop = AsyncMock()

        async def _mock_send(prompt: str):  # type: ignore[return]
            yield e1
            yield e2
            yield e3
            yield e4

        mock_session.send = _mock_send

        log_writer = MagicMock()
        log_writer.record_event = AsyncMock()

        with patch("archon.ai.job_scheduler.ClaudeSession", return_value=mock_session):
            result = await scheduler._run_prompt("task", timeout=30.0, log_writer=log_writer)

        assert result == "all done"
        assert log_writer.record_event.await_count == 4
        log_writer.record_event.assert_any_await(e1)
        log_writer.record_event.assert_any_await(e2)
        log_writer.record_event.assert_any_await(e3)
        log_writer.record_event.assert_any_await(e4)

    async def test_run_prompt_empty_event_stream_returns_empty(self) -> None:
        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg)

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.stop = AsyncMock()

        async def _mock_send(prompt: str):
            return
            yield  # make it an async generator

        mock_session.send = _mock_send
        log_writer = MagicMock()
        log_writer.record_event = AsyncMock()

        with patch("archon.ai.job_scheduler.ClaudeSession", return_value=mock_session):
            result = await scheduler._run_prompt("hello", timeout=30.0, log_writer=log_writer)

        assert result == ""
        assert log_writer.record_event.await_count == 0

    async def test_run_prompt_log_writer_error_does_not_abort_job(self) -> None:
        from archon.ai.event_mapper import Response

        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg)

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.stop = AsyncMock()

        async def _mock_send(prompt: str):
            yield Response(content="final answer")

        mock_session.send = _mock_send

        log_writer = MagicMock()
        log_writer.record_event = AsyncMock(side_effect=OSError("disk full"))

        with patch("archon.ai.job_scheduler.ClaudeSession", return_value=mock_session):
            result = await scheduler._run_prompt("hello", timeout=30.0, log_writer=log_writer)

        assert result == "final answer"
        mock_session.stop.assert_awaited_once()

    async def test_run_prompt_error_event_forwarded_to_log_writer(self) -> None:
        from archon.ai.event_mapper import ErrorEvent, Response

        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg)

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.stop = AsyncMock()

        e1 = ErrorEvent(message="something went wrong")
        e2 = Response(content="recovered")

        async def _mock_send(prompt: str):
            yield e1
            yield e2

        mock_session.send = _mock_send
        log_writer = MagicMock()
        log_writer.record_event = AsyncMock()

        with patch("archon.ai.job_scheduler.ClaudeSession", return_value=mock_session):
            result = await scheduler._run_prompt("hello", timeout=30.0, log_writer=log_writer)

        assert result == "recovered"
        assert log_writer.record_event.await_count == 2
        log_writer.record_event.assert_any_await(e1)
        log_writer.record_event.assert_any_await(e2)


# ── _run_job ──────────────────────────────────────────────────────


class TestRunJob:
    async def test_run_job_success_notifies_all_users(self) -> None:
        bot = _make_bot()
        job = _make_job(pipeline=[SchedulePipelineStep(name="echo_tool", kind="tool", value="echo done")])
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[99, 100])
        await scheduler._run_job(job)
        assert bot.send_message.await_count == 2
        sent_ids = {call[0][0] for call in bot.send_message.call_args_list}
        assert sent_ids == {99, 100}
        msg = bot.send_message.call_args_list[0][0][1]
        assert "done" in msg

    async def test_run_job_no_allowed_users_skips_send(self) -> None:
        bot = _make_bot()
        job = _make_job(pipeline=[SchedulePipelineStep(name="echo_tool", kind="tool", value="echo test")])
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[])
        await scheduler._run_job(job)
        bot.send_message.assert_not_awaited()

    async def test_run_job_bundle_tool_resolves_relative_script(self, tmp_path: Path) -> None:
        """Integration: _run_job passes source_dir through to _run_tool cwd."""
        bundle = tmp_path / "schedules" / "echo-test"
        scripts = bundle / "scripts"
        scripts.mkdir(parents=True)
        script = scripts / "check.sh"
        script.write_text("#!/usr/bin/env bash\necho bundle-ok\n")
        script.chmod(0o755)

        job = _make_job(
            source_dir=bundle,
            pipeline=[SchedulePipelineStep(name="check_tool", kind="tool", value="scripts/check.sh")],
        )
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, jobs_dir_base=str(tmp_path))
        await scheduler._run_job(job)
        status = scheduler.job_statuses[job.name]
        assert status.last_result == "bundle-ok"
        assert status.last_error is None

    async def test_run_job_flat_file_tool_resolves_against_jobs_dir_base(self, tmp_path: Path) -> None:
        """Integration: flat-file jobs (no source_dir) resolve scripts against jobs_dir_base."""
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        script = scripts / "check.sh"
        script.write_text("#!/usr/bin/env bash\necho flat-ok\n")
        script.chmod(0o755)

        job = _make_job(
            pipeline=[SchedulePipelineStep(name="check_tool", kind="tool", value="scripts/check.sh")],
        )
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, jobs_dir_base=str(tmp_path))
        await scheduler._run_job(job)
        status = scheduler.job_statuses[job.name]
        assert status.last_result == "flat-ok"
        assert status.last_error is None

    async def test_run_job_updates_status(self) -> None:
        job = _make_job(pipeline=[SchedulePipelineStep(name="result_tool", kind="tool", value="echo result")])
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)
        await scheduler._run_job(job)
        status = scheduler.job_statuses[job.name]
        assert status.run_count == 1
        assert status.last_result == "result"
        assert status.last_error is None
        assert status.last_run is not None
        assert status.is_running is False

    async def test_run_job_failure_sets_last_error(self) -> None:
        bot = _make_bot()
        job = _make_job(pipeline=[SchedulePipelineStep(name="fail_tool", kind="tool", value="bash -c 'exit 1'")])
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[99])
        await scheduler._run_job(job)
        status = scheduler.job_statuses[job.name]
        assert status.last_error is not None
        assert status.last_result is None

    async def test_run_job_failure_sends_error_notification(self) -> None:
        bot = _make_bot()
        job = _make_job(pipeline=[SchedulePipelineStep(name="fail_tool", kind="tool", value="bash -c 'exit 1'")])
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[99])
        await scheduler._run_job(job)
        bot.send_message.assert_awaited_once()
        msg = bot.send_message.call_args[0][1]
        assert "❌" in msg

    async def test_run_job_skips_when_already_running(self) -> None:
        bot = _make_bot()
        job = _make_job(pipeline=[SchedulePipelineStep(name="echo_tool", kind="tool", value="echo test")])
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot)
        scheduler._statuses[job.name].is_running = True
        await scheduler._run_job(job)
        # run_count must still be 0 (skipped)
        assert scheduler.job_statuses[job.name].run_count == 0

    async def test_run_job_resets_is_running_after_completion(self) -> None:
        job = _make_job(pipeline=[SchedulePipelineStep(name="ok_tool", kind="tool", value="echo ok")])
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)
        await scheduler._run_job(job)
        assert scheduler.job_statuses[job.name].is_running is False

    async def test_run_job_run_count_increments_on_each_call(self) -> None:
        job = _make_job(pipeline=[SchedulePipelineStep(name="run_tool", kind="tool", value="echo run")])
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)
        await scheduler._run_job(job)
        await scheduler._run_job(job)
        assert scheduler.job_statuses[job.name].run_count == 2

    async def test_run_job_notification_contains_check_mark(self) -> None:
        bot = _make_bot()
        job = _make_job(pipeline=[SchedulePipelineStep(name="success_tool", kind="tool", value="echo success")])
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[1])
        await scheduler._run_job(job)
        msg = bot.send_message.call_args[0][1]
        assert "✅" in msg

    async def test_broadcast_splits_long_output_within_limit(self) -> None:
        bot = _make_bot()
        scheduler = _make_scheduler(_make_config(_make_job()), bot, allowed_user_ids=[1])

        await scheduler._broadcast(job_name="nightly", text="<" * 5000, error=False)

        messages = [call[0][1] for call in bot.send_message.call_args_list]
        assert len(messages) > 1
        assert all(len(message) <= 4000 for message in messages)
        assert all("✅ <b>Scheduled: nightly</b>\n" in message for message in messages)
        assert any("&lt;" in message for message in messages)

    async def test_broadcast_escapes_job_name(self) -> None:
        bot = _make_bot()
        scheduler = _make_scheduler(_make_config(_make_job()), bot, allowed_user_ids=[1])

        await scheduler._broadcast(job_name='job <& "nightly">', text="done", error=False)

        msg = bot.send_message.call_args[0][1]
        assert '<b>Scheduled: job &lt;&amp; &quot;nightly&quot;&gt;</b>' in msg

    async def test_broadcast_sends_to_each_user(self) -> None:
        bot = _make_bot()
        scheduler = _make_scheduler(
            _make_config(_make_job()), bot, allowed_user_ids=[10, 20, 30]
        )
        await scheduler._broadcast(job_name="multi", text="hi", error=False)
        sent_ids = [call[0][0] for call in bot.send_message.call_args_list]
        assert sent_ids == [10, 20, 30]


# ── job_statuses ──────────────────────────────────────────────────


class TestJobStatuses:
    def test_job_statuses_returns_copy(self) -> None:
        cfg = _make_config(_make_job(name="j1"))
        scheduler = _make_scheduler(cfg)
        statuses = scheduler.job_statuses
        statuses["j1"] = JobStatus(name="mutated")
        # Internal dict unchanged
        assert scheduler._statuses["j1"].name == "j1"

    def test_initial_statuses_all_zeroed(self) -> None:
        cfg = _make_config(_make_job(name="alpha"), _make_job(name="beta"))
        scheduler = _make_scheduler(cfg)
        for name in ("alpha", "beta"):
            s = scheduler.job_statuses[name]
            assert s.run_count == 0
            assert s.last_run is None
            assert s.is_running is False
            assert s.last_fire_at is None


# ── reload_jobs ────────────────────────────────────────────────────


class TestReloadJobs:
    """Tests for JobScheduler.reload_jobs()."""

    def test_reload_noop_when_no_jobs_dir_base(self) -> None:
        """reload_jobs() is a no-op when jobs_dir_base is not set."""
        cfg = _make_config(_make_job(name="original"))
        scheduler = _make_scheduler(cfg)  # no jobs_dir_base
        # Mark the status so we can detect if it survived the call
        scheduler._statuses["original"].run_count = 7
        scheduler.reload_jobs()
        assert "original" in scheduler._statuses
        assert scheduler._statuses["original"].run_count == 7

    def test_reload_picks_up_new_job_from_disk(self, tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
        """A new .toml file written after construction is discovered on reload."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        # Write a new job file
        (jobs_dir / "new-job.toml").write_text(
            'cron = "0 9 * * *"\n[pipeline]\nhi_tool = "echo hi"\n'
        )
        cfg = ScheduleConfig(enabled=True, jobs=[], jobs_dir="schedules")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)
        scheduler.reload_jobs()
        assert len(scheduler._config.jobs) == 1
        assert scheduler._config.jobs[0].name == "new-job"
        assert "new-job" in scheduler._statuses

    def test_reload_preserves_runtime_status_for_existing_job(self, tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
        """Runtime status (last_run, run_count) survives a reload for jobs still on disk."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        (jobs_dir / "stable.toml").write_text(
            'cron = "* * * * *"\n[pipeline]\nok_tool = "echo ok"\n'
        )
        cfg = ScheduleConfig(enabled=True, jobs=[], jobs_dir="schedules")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)
        scheduler.reload_jobs()  # first load
        # Simulate some runtime state
        scheduler._statuses["stable"].run_count = 5
        scheduler._statuses["stable"].last_run = datetime(2025, 1, 1, 8, 0, 0)
        scheduler.reload_jobs()  # second reload — same file on disk
        s = scheduler._statuses["stable"]
        assert s.run_count == 5
        assert s.last_run == datetime(2025, 1, 1, 8, 0, 0)

    def test_reload_removes_deleted_job(self, tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
        """A job whose .toml file is deleted is removed from statuses on reload."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toml_file = jobs_dir / "gone.toml"
        toml_file.write_text(
            'cron = "0 6 * * *"\n[pipeline]\nbye_tool = "echo bye"\n'
        )
        cfg = ScheduleConfig(enabled=True, jobs=[], jobs_dir="schedules")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)
        scheduler.reload_jobs()
        assert "gone" in scheduler._statuses
        # Now remove the file and reload again
        toml_file.unlink()
        scheduler.reload_jobs()
        assert "gone" not in scheduler._statuses
        assert scheduler._config.jobs == []

    def test_reload_updates_schedule_for_existing_job(self, tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
        """Changing the cron expression in a .toml file is reflected after reload."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toml_file = jobs_dir / "daily.toml"
        toml_file.write_text(
            'cron = "0 8 * * *"\n[pipeline]\nmorning_tool = "echo morning"\n'
        )
        cfg = ScheduleConfig(enabled=True, jobs=[], jobs_dir="schedules")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)
        scheduler.reload_jobs()
        assert scheduler._config.jobs[0].cron == "0 8 * * *"
        # Change the cron expression on disk
        toml_file.write_text(
            'cron = "0 20 * * *"\n[pipeline]\nevening_tool = "echo evening"\n'
        )
        scheduler.reload_jobs()
        assert scheduler._config.jobs[0].cron == "0 20 * * *"

    def test_reload_discovers_new_bundle(self, tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
        """A new bundle directory added after construction is discovered on reload."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        cfg = ScheduleConfig(enabled=True, jobs=[], jobs_dir="schedules")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)
        # Add a bundle after construction
        bundle = jobs_dir / "new-bundle"
        bundle.mkdir()
        (bundle / "job.toml").write_text(
            'cron = "0 9 * * *"\n[pipeline]\nhi_tool = "echo hi"\n'
        )
        scheduler.reload_jobs()
        assert len(scheduler._config.jobs) == 1
        assert scheduler._config.jobs[0].name == "new-bundle"
        assert scheduler._config.jobs[0].source_dir == bundle
        assert "new-bundle" in scheduler._statuses

    def test_reload_removes_deleted_bundle(self, tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
        """A bundle whose directory is deleted is removed from statuses on reload."""
        import shutil
        jobs_dir = tmp_path / "schedules"
        bundle = jobs_dir / "ephemeral"
        bundle.mkdir(parents=True)
        (bundle / "job.toml").write_text(
            'cron = "0 6 * * *"\n[pipeline]\nbye_tool = "echo bye"\n'
        )
        cfg = ScheduleConfig(enabled=True, jobs=[], jobs_dir="schedules")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)
        scheduler.reload_jobs()
        assert "ephemeral" in scheduler._statuses
        # Remove the bundle and reload
        shutil.rmtree(bundle)
        scheduler.reload_jobs()
        assert "ephemeral" not in scheduler._statuses

    def test_reload_preserves_status_for_running_job(self, tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
        """A job whose file is temporarily absent but is_running=True survives reload."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toml_file = jobs_dir / "active.toml"
        toml_file.write_text(
            'cron = "* * * * *"\n[pipeline]\ngo_tool = "echo go"\n'
        )
        cfg = ScheduleConfig(enabled=True, jobs=[], jobs_dir="schedules")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)
        scheduler.reload_jobs()
        # Simulate the job running
        scheduler._statuses["active"].is_running = True
        scheduler._statuses["active"].run_count = 3
        # Remove the file (simulates editor atomic save: delete-then-write)
        toml_file.unlink()
        scheduler.reload_jobs()
        # Status must survive because it's still running
        assert "active" in scheduler._statuses
        assert scheduler._statuses["active"].is_running is True
        assert scheduler._statuses["active"].run_count == 3

    def test_reload_removes_non_running_deleted_job(self, tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
        """A deleted job that is NOT running is removed from statuses."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toml_file = jobs_dir / "idle.toml"
        toml_file.write_text(
            'cron = "* * * * *"\n[pipeline]\ngo_tool = "echo go"\n'
        )
        cfg = ScheduleConfig(enabled=True, jobs=[], jobs_dir="schedules")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)
        scheduler.reload_jobs()
        assert scheduler._statuses["idle"].is_running is False
        toml_file.unlink()
        scheduler.reload_jobs()
        assert "idle" not in scheduler._statuses


# ── Auto-reload (file snapshot + change detection) ───────────────


class TestFileSnapshot:
    """Tests for JobScheduler._file_snapshot()."""

    def test_returns_empty_dict_when_no_jobs_dir_base(self) -> None:
        """No jobs_dir_base → empty snapshot (test mode)."""
        cfg = _make_config()
        scheduler = _make_scheduler(cfg)
        assert scheduler._file_snapshot() == {}

    def test_returns_empty_dict_when_dir_missing(self, tmp_path: Path) -> None:
        """Missing schedules directory → empty snapshot, no error."""
        cfg = ScheduleConfig(enabled=True, jobs=[], jobs_dir="schedules")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)
        # schedules/ dir does not exist
        assert scheduler._file_snapshot() == {}

    def test_captures_flat_file_mtime(self, tmp_path: Path) -> None:
        """Flat .toml file mtimes are included in the snapshot."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        f = jobs_dir / "myjob.toml"
        f.write_text('cron = "* * * * *"\n[pipeline]\necho_tool = "echo"\n')
        cfg = ScheduleConfig(enabled=True, jobs=[], jobs_dir="schedules")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)
        snap = scheduler._file_snapshot()
        assert f in snap
        assert isinstance(snap[f], float)

    def test_captures_bundle_job_toml_mtime(self, tmp_path: Path) -> None:
        """Bundle job.toml mtimes are included in the snapshot."""
        jobs_dir = tmp_path / "schedules"
        bundle = jobs_dir / "mybundle"
        bundle.mkdir(parents=True)
        f = bundle / "job.toml"
        f.write_text('cron = "* * * * *"\n[pipeline]\necho_tool = "echo"\n')
        cfg = ScheduleConfig(enabled=True, jobs=[], jobs_dir="schedules")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)
        snap = scheduler._file_snapshot()
        assert f in snap

    def test_captures_dot_prefixed_bundle(self, tmp_path: Path) -> None:
        """Dot-prefixed bundle directories are included in the snapshot."""
        jobs_dir = tmp_path / "schedules"
        bundle = jobs_dir / ".hidden"
        bundle.mkdir(parents=True)
        f = bundle / "job.toml"
        f.write_text('cron = "* * * * *"\n[pipeline]\necho_tool = "echo"\n')
        cfg = ScheduleConfig(enabled=True, jobs=[], jobs_dir="schedules")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)
        snap = scheduler._file_snapshot()
        assert f in snap

    def test_ignores_non_toml_files(self, tmp_path: Path) -> None:
        """Non-.toml files in the jobs directory are not in the snapshot."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        (jobs_dir / "readme.md").write_text("# Notes")
        cfg = ScheduleConfig(enabled=True, jobs=[], jobs_dir="schedules")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)
        assert scheduler._file_snapshot() == {}

    def test_snapshot_changes_on_file_modify(self, tmp_path: Path) -> None:
        """Modifying a file produces a different snapshot (mtime changes)."""
        import time
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        f = jobs_dir / "myjob.toml"
        f.write_text('cron = "* * * * *"\n[pipeline]\necho_tool = "echo"\n')
        cfg = ScheduleConfig(enabled=True, jobs=[], jobs_dir="schedules")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)
        snap1 = scheduler._file_snapshot()
        time.sleep(0.05)  # ensure mtime differs
        f.write_text('cron = "0 * * * *"\n[pipeline]\necho_tool = "echo"\n')
        snap2 = scheduler._file_snapshot()
        assert snap1 != snap2

    def test_snapshot_changes_on_file_add(self, tmp_path: Path) -> None:
        """Adding a file produces a different snapshot (new key)."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        cfg = ScheduleConfig(enabled=True, jobs=[], jobs_dir="schedules")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)
        snap1 = scheduler._file_snapshot()
        (jobs_dir / "new.toml").write_text('cron = "* * * * *"\n[pipeline]\necho_tool = "echo"\n')
        snap2 = scheduler._file_snapshot()
        assert snap1 != snap2

    def test_snapshot_changes_on_file_delete(self, tmp_path: Path) -> None:
        """Deleting a file produces a different snapshot (removed key)."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        f = jobs_dir / "myjob.toml"
        f.write_text('cron = "* * * * *"\n[pipeline]\necho_tool = "echo"\n')
        cfg = ScheduleConfig(enabled=True, jobs=[], jobs_dir="schedules")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)
        snap1 = scheduler._file_snapshot()
        f.unlink()
        snap2 = scheduler._file_snapshot()
        assert snap1 != snap2

    def test_per_entry_oserror_preserves_other_entries(self, tmp_path: Path) -> None:
        """An OSError on a single entry (e.g. TOCTOU race) preserves the rest."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        good = jobs_dir / "good.toml"
        good.write_text('cron = "* * * * *"\n[pipeline]\necho_tool = "echo"\n')
        # Create a bundle where is_dir() succeeds but job.toml disappears
        bundle = jobs_dir / "vanishing"
        bundle.mkdir()
        job_toml = bundle / "job.toml"
        job_toml.write_text('cron = "* * * * *"\n[pipeline]\necho_tool = "echo"\n')
        cfg = ScheduleConfig(enabled=True, jobs=[], jobs_dir="schedules")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)
        # Both in snapshot normally
        snap = scheduler._file_snapshot()
        assert good in snap
        assert job_toml in snap
        # Delete the bundle's job.toml — simulates TOCTOU where file vanishes
        job_toml.unlink()
        snap2 = scheduler._file_snapshot()
        assert good in snap2  # survives
        assert job_toml not in snap2  # gone

    def test_oserror_returns_empty_dict(self, tmp_path: Path) -> None:
        """OSError during scan returns empty dict, not an exception."""
        cfg = ScheduleConfig(enabled=True, jobs=[], jobs_dir="schedules")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)
        # Point to a non-existent base that would cause stat errors
        scheduler._jobs_dir_base = tmp_path / "nonexistent"
        assert scheduler._file_snapshot() == {}


class TestAutoReload:
    """Tests for automatic reload triggered by file changes in the loop."""

    @pytest.mark.asyncio
    async def test_loop_reloads_on_file_change(self, tmp_path: Path) -> None:
        """The loop calls reload_jobs() when the file snapshot changes."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        cfg = ScheduleConfig(enabled=True, jobs=[], jobs_dir="schedules")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)

        # Write a job file after construction
        (jobs_dir / "new.toml").write_text(
            'cron = "0 0 1 1 *"\n[pipeline]\necho_tool = "echo hi"\n'
        )
        with patch.object(scheduler, "reload_jobs", wraps=scheduler.reload_jobs) as mock_reload:
            # Simulate one tick: call _auto_reload_if_changed
            scheduler._auto_reload_if_changed()
            mock_reload.assert_called_once()
        assert len(scheduler._config.jobs) == 1
        assert scheduler._config.jobs[0].name == "new"

    @pytest.mark.asyncio
    async def test_loop_skips_reload_when_unchanged(self, tmp_path: Path) -> None:
        """No reload when the file snapshot hasn't changed."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        (jobs_dir / "stable.toml").write_text(
            'cron = "0 0 1 1 *"\n[pipeline]\necho_tool = "echo"\n'
        )
        cfg = ScheduleConfig(enabled=True, jobs=[], jobs_dir="schedules")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)

        # Prime the snapshot
        scheduler._auto_reload_if_changed()

        with patch.object(scheduler, "reload_jobs") as mock_reload:
            # Second call with no file changes
            scheduler._auto_reload_if_changed()
            mock_reload.assert_not_called()

    @pytest.mark.asyncio
    async def test_loop_noop_without_jobs_dir_base(self) -> None:
        """Auto-reload is a no-op when jobs_dir_base is not set (test mode)."""
        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg)
        with patch.object(scheduler, "reload_jobs") as mock_reload:
            scheduler._auto_reload_if_changed()
            mock_reload.assert_not_called()

    @pytest.mark.asyncio
    async def test_loop_detects_modified_file(self, tmp_path: Path) -> None:
        """Modifying a job file triggers reload on next check."""
        import time
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        f = jobs_dir / "myjob.toml"
        f.write_text('cron = "0 8 * * *"\n[pipeline]\necho_tool = "echo"\n')
        cfg = ScheduleConfig(enabled=True, jobs=[], jobs_dir="schedules")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)

        # Prime snapshot
        scheduler._auto_reload_if_changed()
        assert scheduler._config.jobs[0].cron == "0 8 * * *"

        # Modify file
        time.sleep(0.05)
        f.write_text('cron = "0 20 * * *"\n[pipeline]\necho_tool = "echo"\n')

        scheduler._auto_reload_if_changed()
        assert scheduler._config.jobs[0].cron == "0 20 * * *"

    @pytest.mark.asyncio
    async def test_loop_detects_deleted_file(self, tmp_path: Path) -> None:
        """Deleting a job file triggers reload on next check."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        f = jobs_dir / "ephemeral.toml"
        f.write_text('cron = "* * * * *"\n[pipeline]\necho_tool = "echo"\n')
        cfg = ScheduleConfig(enabled=True, jobs=[], jobs_dir="schedules")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)

        scheduler._auto_reload_if_changed()
        assert len(scheduler._config.jobs) == 1

        f.unlink()
        scheduler._auto_reload_if_changed()
        assert len(scheduler._config.jobs) == 0

    @pytest.mark.asyncio
    async def test_snapshot_failure_returns_empty_no_reload(self, tmp_path: Path) -> None:
        """If jobs dir is inaccessible, _file_snapshot returns {} — no crash."""
        cfg = ScheduleConfig(enabled=True, jobs=[], jobs_dir="schedules")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)
        # jobs_dir doesn't exist → empty snapshot, no reload
        with patch.object(scheduler, "reload_jobs") as mock_reload:
            scheduler._auto_reload_if_changed()
            mock_reload.assert_not_called()

    @pytest.mark.asyncio
    async def test_first_run_with_empty_snapshot_primes_correctly(self, tmp_path: Path) -> None:
        """Empty snapshot on first run does not re-enter priming on second call."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        cfg = ScheduleConfig(enabled=True, jobs=[], jobs_dir="schedules")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)

        # First call — primes with empty snapshot
        scheduler._auto_reload_if_changed()
        assert scheduler._snapshot_primed is True

        # Add a file — second call must detect change and reload
        (jobs_dir / "new.toml").write_text(
            'cron = "0 0 1 1 *"\n[pipeline]\necho_tool = "echo"\n'
        )
        with patch.object(scheduler, "reload_jobs", wraps=scheduler.reload_jobs) as mock_reload:
            scheduler._auto_reload_if_changed()
            mock_reload.assert_called_once()

    @pytest.mark.asyncio
    async def test_first_run_with_existing_jobs_primes_without_reload(self, tmp_path: Path) -> None:
        """When scheduler starts with jobs already loaded, first call primes without reload."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        (jobs_dir / "existing.toml").write_text(
            'cron = "* * * * *"\n[pipeline]\necho_tool = "echo"\n'
        )
        job = _make_job(name="existing")
        cfg = _make_config(job)
        cfg.jobs_dir = "schedules"
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)

        with patch.object(scheduler, "reload_jobs") as mock_reload:
            scheduler._auto_reload_if_changed()
            mock_reload.assert_not_called()
        assert scheduler._snapshot_primed is True

    @pytest.mark.asyncio
    async def test_manual_reload_prevents_redundant_auto_reload(self, tmp_path: Path) -> None:
        """After reload_jobs() (e.g. /scheduled), auto-reload does not re-reload."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        (jobs_dir / "job.toml").write_text(
            'cron = "* * * * *"\n[pipeline]\necho_tool = "echo"\n'
        )
        cfg = ScheduleConfig(enabled=True, jobs=[], jobs_dir="schedules")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)

        # Prime
        scheduler._auto_reload_if_changed()

        # Simulate /scheduled → manual reload (updates _last_snapshot internally)
        scheduler.reload_jobs()

        # Auto-reload should NOT fire because reload_jobs() updated the snapshot
        with patch.object(scheduler, "reload_jobs") as mock_reload:
            scheduler._auto_reload_if_changed()
            mock_reload.assert_not_called()


# ── _substitute_refs unit tests ───────────────────────────────────


class TestSubstituteRefs:
    def test_dollar_prefix_suppresses_substitution(self) -> None:
        """${ref} is left as-is — the $ prefix escapes the substitution."""
        result = _substitute_refs("cmd ${foo}", {"foo": "bar"})
        assert result == "cmd ${foo}"

    def test_dollar_prefix_leaves_dollar_in_output(self) -> None:
        """The $ character itself is preserved in output when escaping a ref."""
        result = _substitute_refs("prefix ${step1_tool} suffix", {"step1_tool": "output"})
        assert result == "prefix ${step1_tool} suffix"

    def test_mixed_escaped_and_unescaped_refs(self) -> None:
        """Escaped ${ref} is left as-is; unescaped {ref} is substituted normally."""
        result = _substitute_refs("{a} and ${b}", {"a": "hello", "b": "world"})
        assert result == "hello and ${b}"

    def test_normal_ref_substituted(self) -> None:
        """Unescaped {ref} is replaced with matching output."""
        result = _substitute_refs("echo {foo}", {"foo": "bar"})
        assert result == "echo bar"

    def test_unknown_ref_left_as_is(self) -> None:
        """An unescaped {ref} with no matching output key is left as-is."""
        result = _substitute_refs("{unknown}", {})
        assert result == "{unknown}"


# ── Pipeline ref substitution ─────────────────────────────────────


class TestPipelineRefSubstitution:
    async def test_ref_substituted_from_earlier_tool_output(self) -> None:
        """Output of earlier step is substituted into later step's value."""
        from archon.ai.event_mapper import Response

        captured: list[str] = []
        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.stop = AsyncMock()

        async def _mock_send(prompt: str):  # type: ignore[return]
            captured.append(prompt)
            yield Response(content="summary")

        mock_session.send = _mock_send

        pipeline = [
            SchedulePipelineStep(name="echo_tool", kind="tool", value="echo tooloutput"),
            SchedulePipelineStep(name="summarize_prompt", kind="prompt", value="Process: {echo_tool}"),
        ]
        job = _make_job(pipeline=pipeline)
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)

        with patch("archon.ai.job_scheduler.ClaudeSession", return_value=mock_session):
            await scheduler._run_job(job)

        assert captured == ["Process: tooloutput"]

    async def test_ref_in_tool_command_substituted(self) -> None:
        """A {ref} in a tool command is replaced with the earlier step's output."""
        pipeline = [
            SchedulePipelineStep(name="word_tool", kind="tool", value="echo hello"),
            SchedulePipelineStep(name="echo_tool", kind="tool", value="echo {word_tool}"),
        ]
        job = _make_job(pipeline=pipeline)
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)
        await scheduler._run_job(job)
        assert scheduler.job_statuses[job.name].last_result == "hello"

    async def test_ref_in_prompt_substituted(self) -> None:
        """A {ref} in a prompt is replaced with the earlier step's output."""
        from archon.ai.event_mapper import Response

        captured: list[str] = []
        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.stop = AsyncMock()

        async def _mock_send(prompt: str):  # type: ignore[return]
            captured.append(prompt)
            yield Response(content="done")

        mock_session.send = _mock_send

        pipeline = [
            SchedulePipelineStep(name="data_tool", kind="tool", value="echo my_data"),
            SchedulePipelineStep(name="analyze_prompt", kind="prompt", value="Analyze: {data_tool}"),
        ]
        job = _make_job(pipeline=pipeline)
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)

        with patch("archon.ai.job_scheduler.ClaudeSession", return_value=mock_session):
            await scheduler._run_job(job)

        assert captured[0] == "Analyze: my_data"

    async def test_multiple_refs_substituted(self) -> None:
        """Multiple {ref}s in a single step are all substituted."""
        from archon.ai.event_mapper import Response

        captured: list[str] = []
        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.stop = AsyncMock()

        async def _mock_send(prompt: str):  # type: ignore[return]
            captured.append(prompt)
            yield Response(content="merged")

        mock_session.send = _mock_send

        pipeline = [
            SchedulePipelineStep(name="a_tool", kind="tool", value="echo aaa"),
            SchedulePipelineStep(name="b_tool", kind="tool", value="echo bbb"),
            SchedulePipelineStep(name="merge_prompt", kind="prompt", value="Merge: {a_tool} and {b_tool}"),
        ]
        job = _make_job(pipeline=pipeline)
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)

        with patch("archon.ai.job_scheduler.ClaudeSession", return_value=mock_session):
            await scheduler._run_job(job)

        assert captured[0] == "Merge: aaa and bbb"

    async def test_last_step_output_is_job_result(self) -> None:
        """The last step's output becomes the job's last_result."""
        from archon.ai.event_mapper import Response

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.stop = AsyncMock()

        async def _mock_send(prompt: str):  # type: ignore[return]
            yield Response(content="final_answer")

        mock_session.send = _mock_send

        pipeline = [
            SchedulePipelineStep(name="data_tool", kind="tool", value="echo raw_data"),
            SchedulePipelineStep(name="summary_prompt", kind="prompt", value="Summarize: {data_tool}"),
        ]
        job = _make_job(pipeline=pipeline)
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)

        with patch("archon.ai.job_scheduler.ClaudeSession", return_value=mock_session):
            await scheduler._run_job(job)

        assert scheduler.job_statuses[job.name].last_result == "final_answer"


# ── Invalid job runtime ───────────────────────────────────────────


class TestInvalidJobRuntime:
    async def test_invalid_job_skipped_at_fire_time(self) -> None:
        """A job with validation_error is not executed."""
        bot = _make_bot()
        job = _make_job(
            validation_error="step 'bad_step' in job 'test_job' has no recognized suffix."
        )
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[1])
        await scheduler._run_job(job)
        # Job should not be treated as run — no result set
        assert scheduler.job_statuses[job.name].last_result is None

    async def test_invalid_job_sends_telegram_warning(self) -> None:
        """A job with validation_error sends a warning notification to all users."""
        bot = _make_bot()
        job = _make_job(
            validation_error="step 'bad_step' has no recognized suffix"
        )
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[42, 99])
        await scheduler._run_job(job)
        assert bot.send_message.await_count == 2
        msg = bot.send_message.call_args_list[0][0][1]
        assert "⚠️" in msg
        assert "bad_step" in msg

    async def test_invalid_job_run_count_not_incremented(self) -> None:
        """An invalid job's run_count stays at 0 when skipped."""
        job = _make_job(validation_error="some config error")
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)
        await scheduler._run_job(job)
        assert scheduler.job_statuses[job.name].run_count == 0

    async def test_valid_job_runs_normally_after_invalid_config_fixed(self) -> None:
        """When validation_error is None the job runs normally."""
        bot = _make_bot()
        job = _make_job(
            pipeline=[SchedulePipelineStep(name="echo_tool", kind="tool", value="echo ok")],
            validation_error=None,
        )
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[1])
        await scheduler._run_job(job)
        assert scheduler.job_statuses[job.name].run_count == 1
        assert scheduler.job_statuses[job.name].last_result == "ok"


# ── Disable-on-error behavior ──────────────────────────────────────


class TestDisableOnError:
    """JobScheduler disables invalid jobs in memory after the first fire."""

    @pytest.mark.asyncio
    async def test_invalid_job_disabled_in_memory_after_run(self) -> None:
        """_run_job sets job.enabled=False when validation_error is set."""
        bot = _make_bot()
        job = _make_job(
            validation_error="pipeline is empty",
        )
        job.enabled = True
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[123])

        await scheduler._run_job(job)

        # Job must be disabled in memory
        assert job.enabled is False

    @pytest.mark.asyncio
    async def test_invalid_job_notifies_user_exactly_once(self) -> None:
        """_run_job sends exactly one broadcast notification for an invalid job."""
        bot = _make_bot()
        job = _make_job(
            validation_error="some config error",
        )
        job.enabled = True
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[42])

        # Run twice — second run should not send another notification because job is disabled
        await scheduler._run_job(job)
        await scheduler._run_job(job)

        # Notification sent exactly once: first _run_job sets job.enabled=False via _disable_invalid_job.
        # Second _run_job hits 'if not job.enabled: return' guard before any notification is sent.
        assert bot.send_message.call_count == 1

    @pytest.mark.asyncio
    async def test_invalid_job_writes_enabled_false_to_toml(self, tmp_path: pytest.TempPathFactory) -> None:
        """_disable_invalid_job writes enabled=false to the TOML file on disk."""
        import tomlkit

        # Create a real TOML file with enabled=true
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        job_file = jobs_dir / "bad-job.toml"
        doc = tomlkit.document()
        doc["cron"] = "* * * * *"
        doc["enabled"] = True
        doc["pipeline"] = tomlkit.table()
        job_file.write_text(tomlkit.dumps(doc))

        bot = _make_bot()
        job = _make_job(
            name="bad-job",
            pipeline=[],
            validation_error="pipeline is empty",
        )
        job.enabled = True
        cfg = ScheduleConfig(enabled=True, jobs_dir="schedules", jobs=[job])
        scheduler = JobScheduler(
            config=cfg,
            bot=bot,
            allowed_user_ids=[1],
            jobs_dir_base=str(tmp_path),
        )

        await scheduler._run_job(job)

        # Read the TOML back and verify enabled=false
        updated = tomlkit.parse(job_file.read_text())
        assert updated["enabled"] is False


# ── _disable_invalid_job with bundles ──────────────────────────────


class TestDisableInvalidJobBundles:
    """Tests for source_dir-aware _disable_invalid_job()."""

    @pytest.mark.asyncio
    async def test_disable_writes_to_bundle_job_toml(self, tmp_path: Path) -> None:
        """Writes enabled=false to source_dir/job.toml."""
        import tomlkit

        jobs_dir = tmp_path / "schedules"
        bundle = jobs_dir / "broken"
        bundle.mkdir(parents=True)
        job_file = bundle / "job.toml"
        doc = tomlkit.document()
        doc["cron"] = "* * * * *"
        doc["enabled"] = True
        doc["pipeline"] = tomlkit.table()
        job_file.write_text(tomlkit.dumps(doc))

        bot = _make_bot()
        job = _make_job(
            name="broken", pipeline=[], validation_error="bad",
            source_dir=bundle,
        )
        job.enabled = True
        cfg = ScheduleConfig(enabled=True, jobs_dir="schedules", jobs=[job])
        scheduler = JobScheduler(
            config=cfg, bot=bot, allowed_user_ids=[1],
            jobs_dir_base=str(tmp_path),
        )
        await scheduler._run_job(job)
        updated = tomlkit.parse(job_file.read_text())
        assert updated["enabled"] is False

    @pytest.mark.asyncio
    async def test_disable_writes_to_flat_toml(self, tmp_path: Path) -> None:
        """Legacy behavior preserved when source_dir=None."""
        import tomlkit

        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir(parents=True)
        job_file = jobs_dir / "legacy.toml"
        doc = tomlkit.document()
        doc["cron"] = "* * * * *"
        doc["enabled"] = True
        doc["pipeline"] = tomlkit.table()
        job_file.write_text(tomlkit.dumps(doc))

        bot = _make_bot()
        job = _make_job(
            name="legacy", pipeline=[], validation_error="bad",
            source_dir=None,
        )
        job.enabled = True
        cfg = ScheduleConfig(enabled=True, jobs_dir="schedules", jobs=[job])
        scheduler = JobScheduler(
            config=cfg, bot=bot, allowed_user_ids=[1],
            jobs_dir_base=str(tmp_path),
        )
        await scheduler._run_job(job)
        updated = tomlkit.parse(job_file.read_text())
        assert updated["enabled"] is False

    @pytest.mark.asyncio
    async def test_disable_collision_disables_both(self, tmp_path: Path) -> None:
        """Both name.toml and name/job.toml get disabled."""
        import tomlkit

        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir(parents=True)
        # Flat file
        flat_file = jobs_dir / "clash.toml"
        flat_doc = tomlkit.document()
        flat_doc["cron"] = "* * * * *"
        flat_doc["enabled"] = True
        flat_doc["pipeline"] = tomlkit.table()
        flat_file.write_text(tomlkit.dumps(flat_doc))
        # Bundle
        bundle = jobs_dir / "clash"
        bundle.mkdir()
        bundle_file = bundle / "job.toml"
        bundle_doc = tomlkit.document()
        bundle_doc["cron"] = "* * * * *"
        bundle_doc["enabled"] = True
        bundle_doc["pipeline"] = tomlkit.table()
        bundle_file.write_text(tomlkit.dumps(bundle_doc))

        bot = _make_bot()
        job = _make_job(
            name="clash", pipeline=[],
            validation_error="collision: both exist",
            source_dir=bundle,
        )
        job.enabled = True
        cfg = ScheduleConfig(enabled=True, jobs_dir="schedules", jobs=[job])
        scheduler = JobScheduler(
            config=cfg, bot=bot, allowed_user_ids=[1],
            jobs_dir_base=str(tmp_path),
        )
        await scheduler._run_job(job)
        assert tomlkit.parse(flat_file.read_text())["enabled"] is False
        assert tomlkit.parse(bundle_file.read_text())["enabled"] is False

    @pytest.mark.asyncio
    async def test_disable_missing_bundle_file_graceful(self, tmp_path: Path) -> None:
        """Missing job.toml in source_dir doesn't raise."""
        bot = _make_bot()
        missing_dir = tmp_path / "schedules" / "ghost"
        job = _make_job(
            name="ghost", pipeline=[], validation_error="bad",
            source_dir=missing_dir,
        )
        job.enabled = True
        cfg = ScheduleConfig(enabled=True, jobs_dir="schedules", jobs=[job])
        scheduler = JobScheduler(
            config=cfg, bot=bot, allowed_user_ids=[1],
            jobs_dir_base=str(tmp_path),
        )
        await scheduler._run_job(job)  # should not raise
        assert job.enabled is False


# ── _check_jobs_dir_permissions with bundles ───────────────────────


class TestCheckPermissionsBundles:
    """Tests for bundle-aware permission checks."""

    def test_warns_world_writable_bundle_dir(self, tmp_path: Path) -> None:
        import logging
        bundle = tmp_path / "schedules" / "myjob"
        bundle.mkdir(parents=True)
        (bundle / "job.toml").write_text('cron = "* * * * *"\n\n[pipeline]\necho_tool = "echo x"\n')
        bundle.chmod(0o777)
        try:
            job = _make_job(name="myjob", source_dir=bundle)
            cfg = _make_config(job)
            with patch("archon.ai.job_scheduler.logger") as mock_logger:
                _make_scheduler(cfg, jobs_dir_base=str(tmp_path))
                assert any("world-writable" in str(c) and "myjob" in str(c) for c in mock_logger.warning.call_args_list)
        finally:
            bundle.chmod(0o755)

    def test_warns_world_writable_scripts_subdir(self, tmp_path: Path) -> None:
        bundle = tmp_path / "schedules" / "myjob"
        scripts = bundle / "scripts"
        scripts.mkdir(parents=True)
        (bundle / "job.toml").write_text('cron = "* * * * *"\n\n[pipeline]\necho_tool = "echo x"\n')
        scripts.chmod(0o777)
        try:
            job = _make_job(name="myjob", source_dir=bundle)
            cfg = _make_config(job)
            with patch("archon.ai.job_scheduler.logger") as mock_logger:
                _make_scheduler(cfg, jobs_dir_base=str(tmp_path))
                assert any("world-writable" in str(c) and "scripts" in str(c) for c in mock_logger.warning.call_args_list)
        finally:
            scripts.chmod(0o755)

    def test_no_false_positive_safe_dirs(self, tmp_path: Path) -> None:
        bundle = tmp_path / "schedules" / "safe"
        bundle.mkdir(parents=True)
        (bundle / "job.toml").write_text('cron = "* * * * *"\n\n[pipeline]\necho_tool = "echo x"\n')
        bundle.chmod(0o755)
        job = _make_job(name="safe", source_dir=bundle)
        cfg = _make_config(job)
        with patch("archon.ai.job_scheduler.logger") as mock_logger:
            _make_scheduler(cfg, jobs_dir_base=str(tmp_path))
            world_writable_calls = [c for c in mock_logger.warning.call_args_list if "world-writable" in str(c)]
            assert len(world_writable_calls) == 0

    def test_skips_nonexistent_scripts_dir(self, tmp_path: Path) -> None:
        bundle = tmp_path / "schedules" / "noscripts"
        bundle.mkdir(parents=True)
        (bundle / "job.toml").write_text('cron = "* * * * *"\n\n[pipeline]\necho_tool = "echo x"\n')
        job = _make_job(name="noscripts", source_dir=bundle)
        cfg = _make_config(job)
        _make_scheduler(cfg, jobs_dir_base=str(tmp_path))  # should not raise

    def test_skips_flat_jobs(self, tmp_path: Path) -> None:
        job = _make_job(name="flat", source_dir=None)
        cfg = _make_config(job)
        with patch("archon.ai.job_scheduler.logger") as mock_logger:
            _make_scheduler(cfg, jobs_dir_base=str(tmp_path))
            bundle_calls = [c for c in mock_logger.warning.call_args_list if "bundle" in str(c)]
            assert len(bundle_calls) == 0


# ── Deprecation warnings ──────────────────────────────────────────


class TestLegacyWarnings:
    """Tests for _broadcast_legacy_warnings().

    _loop is patched to a no-op to prevent real job execution during tests.
    """

    @staticmethod
    async def _start_and_drain(scheduler: JobScheduler) -> None:
        """Start the scheduler (with _loop patched) and let the warning task complete."""
        with patch.object(scheduler, "_loop", new_callable=AsyncMock):
            await scheduler.start()
        await asyncio.sleep(0)  # yield to let the fire-and-forget warning task run

    @pytest.mark.asyncio
    async def test_legacy_warning_sent_per_flat_job(self) -> None:
        bot = _make_bot()
        job1 = _make_job(name="flat1", source_dir=None)
        job2 = _make_job(name="flat2", source_dir=None)
        cfg = _make_config(job1, job2)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[1])
        await self._start_and_drain(scheduler)
        await scheduler.stop()
        # Each flat job → one warning per user
        warning_msgs = [c[0][1] for c in bot.send_message.call_args_list if "deprecated" in c[0][1]]
        assert len(warning_msgs) == 2

    @pytest.mark.asyncio
    async def test_legacy_warning_skips_bundle_jobs(self) -> None:
        bot = _make_bot()
        job = _make_job(name="bundled", source_dir=Path("/tmp/fake-bundle"))
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[1])
        await self._start_and_drain(scheduler)
        await scheduler.stop()
        warning_msgs = [c[0][1] for c in bot.send_message.call_args_list if "deprecated" in c[0][1]]
        assert len(warning_msgs) == 0

    @pytest.mark.asyncio
    async def test_legacy_warning_all_users(self) -> None:
        bot = _make_bot()
        job = _make_job(name="legacy", source_dir=None)
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[10, 20])
        await self._start_and_drain(scheduler)
        await scheduler.stop()
        warning_msgs = [c for c in bot.send_message.call_args_list if "deprecated" in c[0][1]]
        user_ids = {c[0][0] for c in warning_msgs}
        assert user_ids == {10, 20}

    @pytest.mark.asyncio
    async def test_legacy_warning_handles_send_failure(self) -> None:
        bot = _make_bot()
        bot.send_message = AsyncMock(side_effect=Exception("network error"))
        job = _make_job(name="broken", source_dir=None)
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[1])
        await self._start_and_drain(scheduler)  # should not raise
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_legacy_warning_once_on_boot(self) -> None:
        bot = _make_bot()
        job = _make_job(name="flat", source_dir=None)
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[1])
        await self._start_and_drain(scheduler)
        await scheduler.stop()
        warning_count = sum(1 for c in bot.send_message.call_args_list if "deprecated" in c[0][1])
        assert warning_count == 1

    @pytest.mark.asyncio
    async def test_legacy_warning_not_on_reload(self, tmp_path: Path) -> None:
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        (jobs_dir / "flat.toml").write_text(
            'cron = "* * * * *"\n\n[pipeline]\necho_tool = "echo x"\n'
        )
        bot = _make_bot()
        cfg = ScheduleConfig(enabled=True, jobs=[], jobs_dir="schedules")
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[1], jobs_dir_base=tmp_path)
        scheduler.reload_jobs()
        # reload should NOT send warnings
        warning_msgs = [c for c in bot.send_message.call_args_list if "deprecated" in str(c)]
        assert len(warning_msgs) == 0

    @pytest.mark.asyncio
    async def test_legacy_warning_skipped_when_disabled(self) -> None:
        bot = _make_bot()
        job = _make_job(name="flat", source_dir=None)
        cfg = _make_config(job, enabled=False)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[1])
        await scheduler.start()
        await scheduler.stop()
        warning_msgs = [c for c in bot.send_message.call_args_list if "deprecated" in str(c)]
        assert len(warning_msgs) == 0

    @pytest.mark.asyncio
    async def test_legacy_warning_message_format(self) -> None:
        bot = _make_bot()
        job = _make_job(name="old-job", source_dir=None)
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[1])
        await self._start_and_drain(scheduler)
        await scheduler.stop()
        warning_msgs = [c[0][1] for c in bot.send_message.call_args_list if "deprecated" in c[0][1]]
        assert len(warning_msgs) == 1
        msg = warning_msgs[0]
        assert "old-job" in msg
        assert "deprecated" in msg
        assert "old-job/job.toml" in msg


# ── _ScheduleJobLogWriter ──────────────────────────────────────────


class TestScheduleJobLogWriter:
    def _make_started_at(self) -> datetime:
        return datetime(2026, 3, 28, 10, 5, 30, tzinfo=timezone.utc)

    def test_log_writer_creates_file_with_header(self, tmp_path: Path) -> None:
        path = tmp_path / "logs" / "myjob.md"
        started_at = self._make_started_at()
        _ScheduleJobLogWriter(path, "myjob", started_at)
        assert path.exists()
        content = path.read_text()
        assert "# Scheduled: myjob" in content
        assert "2026-03-28" in content
        assert "10:05:30 UTC" in content
        assert "# Scheduled: myjob · 2026-03-28" in content
        assert "**Started:** 10:05:30 UTC" in content
        assert "---" in content

    def test_log_writer_includes_prompt_section_when_given(self, tmp_path: Path) -> None:
        path = tmp_path / "myjob.md"
        started_at = self._make_started_at()
        _ScheduleJobLogWriter(path, "myjob", started_at, prompt="hello")
        content = path.read_text()
        assert "## 📝 Prompt" in content
        assert "hello" in content
        assert "## 📝 Prompt · 10:05:30 UTC" in content

    def test_log_writer_no_prompt_section_when_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "myjob.md"
        started_at = self._make_started_at()
        _ScheduleJobLogWriter(path, "myjob", started_at, prompt="")
        content = path.read_text()
        assert "## 📝 Prompt" not in content

    def test_log_writer_creates_parent_directory(self, tmp_path: Path) -> None:
        path = tmp_path / "deeply" / "nested" / "myjob.md"
        started_at = self._make_started_at()
        _ScheduleJobLogWriter(path, "myjob", started_at)
        assert path.exists()

    async def test_log_writer_record_event_appends_rendered_text(self, tmp_path: Path) -> None:
        path = tmp_path / "myjob.md"
        started_at = self._make_started_at()
        writer = _ScheduleJobLogWriter(path, "myjob", started_at)
        event = Response(content="All done!")
        await writer.record_event(event)
        content = path.read_text()
        assert "All done!" in content

    async def test_log_writer_record_event_skips_empty_render(self, tmp_path: Path) -> None:
        path = tmp_path / "myjob.md"
        started_at = self._make_started_at()
        writer = _ScheduleJobLogWriter(path, "myjob", started_at)
        initial_content = path.read_text()
        with patch("archon.ai.event_renderer.EventRenderer.render", return_value=""):
            await writer.record_event(Response(content="ignored"))
        assert path.read_text() == initial_content

    async def test_log_writer_suppressed_event_not_written(self, tmp_path: Path) -> None:
        path = tmp_path / "myjob.md"
        started_at = self._make_started_at()
        writer = _ScheduleJobLogWriter(
            path, "myjob", started_at, suppressed_events=frozenset({"response"})
        )
        initial_content = path.read_text()
        await writer.record_event(Response(content="suppressed"))
        assert path.read_text() == initial_content

    async def test_log_writer_finalize_success_writes_completed_footer(self, tmp_path: Path) -> None:
        path = tmp_path / "myjob.md"
        started_at = self._make_started_at()
        writer = _ScheduleJobLogWriter(path, "myjob", started_at)
        await writer.finalize(error=None)
        content = path.read_text()
        assert "## ✅ Completed" in content
        assert "**Duration:**" in content
        assert "---" in content

    async def test_log_writer_finalize_error_writes_failed_footer(self, tmp_path: Path) -> None:
        path = tmp_path / "myjob.md"
        started_at = self._make_started_at()
        writer = _ScheduleJobLogWriter(path, "myjob", started_at)
        await writer.finalize(error="boom")
        content = path.read_text()
        assert "## ❌ Failed" in content
        assert "boom" in content
        assert "**Duration:**" in content
        assert "---" in content

    def test_log_writer_collision_creates_numbered_suffix(self, tmp_path: Path) -> None:
        path = tmp_path / "myjob.md"
        # Pre-create the file to trigger collision handling
        path.write_text("existing")
        started_at = self._make_started_at()
        writer = _ScheduleJobLogWriter(path, "myjob", started_at)
        # The writer should use a _2 suffixed path
        assert writer._path != path
        assert writer._path.stem == "myjob_2"
        assert writer._path.exists()

    def test_log_writer_raises_if_started_at_is_naive(self, tmp_path: Path) -> None:
        path = tmp_path / "myjob.md"
        naive_dt = datetime(2026, 3, 28, 10, 5, 30)  # no tzinfo
        with pytest.raises(ValueError, match="timezone-aware"):
            _ScheduleJobLogWriter(path, "myjob", naive_dt)

    def test_log_writer_collision_chain_to_suffix_3(self, tmp_path: Path) -> None:
        path = tmp_path / "myjob.md"
        # Pre-create both original and _2 to force _3
        path.write_text("existing")
        (tmp_path / "myjob_2.md").write_text("existing2")
        started_at = self._make_started_at()
        writer = _ScheduleJobLogWriter(path, "myjob", started_at)
        assert writer._path.stem == "myjob_3"
        assert writer._path.exists()

    async def test_log_writer_suppressed_tool_result_is_summarized(self, tmp_path: Path) -> None:
        path = tmp_path / "myjob.md"
        started_at = self._make_started_at()
        writer = _ScheduleJobLogWriter(
            path, "myjob", started_at, suppressed_tools=frozenset({"Read"})
        )
        event = ToolResult(content="file content here\nline2\nline3", tool_name="Read")
        await writer.record_event(event)
        content = path.read_text()
        # Should have summarized (compact) content, not the full file content
        assert "Read completed" in content
        assert "file content here" not in content


# ── _run_job log_writer integration ──────────────────────────────


def _make_history_config(tmp_path: Path) -> "HistoryConfig":
    return HistoryConfig(
        enabled=True,
        directory=str(tmp_path / "history"),
        suppressed_tool_results=[],
        suppressed_events=[],
    )


def _make_scheduler_with_history(
    config: "ScheduleConfig",
    bot: MagicMock | None = None,
    allowed_user_ids: list[int] | None = None,
    history_config: "HistoryConfig | None" = None,
    **kwargs: object,
) -> JobScheduler:
    """Create a scheduler with history_enabled=True in config."""
    from archon.config.loader import ScheduleConfig as SC
    # Patch history_enabled on the config
    cfg_with_history = ScheduleConfig(
        enabled=config.enabled,
        jobs=config.jobs,
        jobs_dir=config.jobs_dir,
        history_enabled=True,
    )
    return JobScheduler(
        cfg_with_history,
        bot or _make_bot(),
        allowed_user_ids=allowed_user_ids or [],
        history_config=history_config,
        **kwargs,
    )


def _mock_session_for_prompt(response_text: str = "response") -> MagicMock:
    """Build a mock ClaudeSession that yields a single Response event."""
    mock_session = MagicMock()
    mock_session.start = AsyncMock()
    mock_session.stop = AsyncMock()

    async def _mock_send(prompt: str):  # type: ignore[return]
        from archon.ai.event_mapper import Response
        yield Response(content=response_text)

    mock_session.send = _mock_send
    return mock_session


class TestRunJobLogWriter:
    async def test_run_job_creates_log_writer_when_history_enabled(self, tmp_path: Path) -> None:
        """Log file is created under history_dir/schedule/ with the correct name pattern."""
        history_config = _make_history_config(tmp_path)
        job = _make_job(
            name="my-job",
            pipeline=[SchedulePipelineStep(name="say_prompt", kind="prompt", value="say hi")],
        )
        cfg = _make_config(job)
        scheduler = _make_scheduler_with_history(cfg, history_config=history_config)

        mock_session = _mock_session_for_prompt("hello")
        with patch("archon.ai.job_scheduler.ClaudeSession", return_value=mock_session):
            await scheduler._run_job(job)

        schedule_dir = tmp_path / "history" / "schedule"
        log_files = list(schedule_dir.glob("*my-job*.md"))
        assert len(log_files) == 1

    async def test_run_job_no_log_writer_when_history_disabled(self, tmp_path: Path) -> None:
        """_ScheduleJobLogWriter is never instantiated when history_enabled=False."""
        job = _make_job(
            name="my-job",
            pipeline=[SchedulePipelineStep(name="echo_tool", kind="tool", value="echo hi")],
        )
        cfg = _make_config(job)
        assert cfg.history_enabled is False
        scheduler = _make_scheduler(cfg, history_config=_make_history_config(tmp_path))

        with patch("archon.ai.job_scheduler._ScheduleJobLogWriter") as mock_cls:
            await scheduler._run_job(job)

        mock_cls.assert_not_called()

    async def test_run_job_no_log_writer_when_history_config_is_none(self, tmp_path: Path) -> None:
        """_ScheduleJobLogWriter is never instantiated when history_config is None."""
        job = _make_job(
            name="my-job",
            pipeline=[SchedulePipelineStep(name="echo_tool", kind="tool", value="echo hi")],
        )
        cfg = _make_config(job)
        scheduler = _make_scheduler_with_history(cfg, history_config=None)

        with patch("archon.ai.job_scheduler._ScheduleJobLogWriter") as mock_cls:
            await scheduler._run_job(job)

        mock_cls.assert_not_called()

    async def test_run_job_finalizes_log_writer_on_success(self, tmp_path: Path) -> None:
        """finalize(error=None) is called after a successful job run."""
        history_config = _make_history_config(tmp_path)
        job = _make_job(
            name="finalize-test",
            pipeline=[SchedulePipelineStep(name="say_prompt", kind="prompt", value="say done")],
        )
        cfg = _make_config(job)
        scheduler = _make_scheduler_with_history(cfg, history_config=history_config)

        mock_session = _mock_session_for_prompt("all done")
        with patch("archon.ai.job_scheduler.ClaudeSession", return_value=mock_session):
            with patch("archon.ai.job_scheduler._ScheduleJobLogWriter") as mock_writer_cls:
                mock_writer = MagicMock()
                mock_writer.path = tmp_path / "fake.md"
                mock_writer.record_event = AsyncMock()
                mock_writer.finalize = AsyncMock()
                mock_writer_cls.return_value = mock_writer
                await scheduler._run_job(job)

        mock_writer.finalize.assert_awaited_once_with(error=None)

    async def test_run_job_finalizes_log_writer_on_error(self, tmp_path: Path) -> None:
        """finalize(error=<msg>) is called when the job fails."""
        history_config = _make_history_config(tmp_path)
        job = _make_job(
            name="error-test",
            pipeline=[SchedulePipelineStep(name="fail_tool", kind="tool", value="bash -c 'exit 1'")],
        )
        cfg = _make_config(job)
        scheduler = _make_scheduler_with_history(cfg, history_config=history_config)

        with patch("archon.ai.job_scheduler._ScheduleJobLogWriter") as mock_writer_cls:
            mock_writer = MagicMock()
            mock_writer.path = tmp_path / "fake.md"
            mock_writer.record_event = AsyncMock()
            mock_writer.finalize = AsyncMock()
            mock_writer_cls.return_value = mock_writer
            await scheduler._run_job(job)

        call_kwargs = mock_writer.finalize.call_args
        assert call_kwargs is not None
        error_arg = call_kwargs.kwargs.get("error") or call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("error")
        assert error_arg is not None and len(error_arg) > 0

    async def test_run_job_finalize_exception_does_not_mask_job_error(self, tmp_path: Path) -> None:
        """If finalize() raises, the job error is still broadcast normally."""
        bot = _make_bot()
        history_config = _make_history_config(tmp_path)
        job = _make_job(
            name="mask-test",
            pipeline=[SchedulePipelineStep(name="fail_tool", kind="tool", value="bash -c 'exit 1'")],
        )
        cfg = _make_config(job)
        scheduler = _make_scheduler_with_history(
            cfg, bot=bot, allowed_user_ids=[42], history_config=history_config
        )

        with patch("archon.ai.job_scheduler._ScheduleJobLogWriter") as mock_writer_cls:
            mock_writer = MagicMock()
            mock_writer.path = tmp_path / "fake.md"
            mock_writer.record_event = AsyncMock()
            mock_writer.finalize = AsyncMock(side_effect=OSError("disk full"))
            mock_writer_cls.return_value = mock_writer
            await scheduler._run_job(job)

        # Job error notification must still be sent
        bot.send_message.assert_awaited()
        msgs = [call[0][1] for call in bot.send_message.call_args_list]
        assert any("❌" in m for m in msgs)

    async def test_run_job_prompt_step_sends_header_plus_response_format(self, tmp_path: Path) -> None:
        """Prompt steps: first send_message is the header, subsequent are formatted response parts."""
        bot = _make_bot()
        job = _make_job(
            name="prompt-format",
            pipeline=[SchedulePipelineStep(name="say_prompt", kind="prompt", value="say hi")],
        )
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[1])

        mock_session = _mock_session_for_prompt("the answer")
        with patch("archon.ai.job_scheduler.ClaudeSession", return_value=mock_session):
            await scheduler._run_job(job)

        calls = bot.send_message.call_args_list
        # At least 2 calls: header + response
        assert len(calls) >= 2
        header_msg = calls[0][0][1]
        assert "🗓" in header_msg
        assert "prompt-format" in header_msg
        # Response part(s) should contain the actual content
        response_parts = [call[0][1] for call in calls[1:]]
        assert any("the answer" in p or "✅" in p for p in response_parts)
        # All calls must use HTML parse mode
        for call in calls:
            assert call.kwargs.get("parse_mode") == "HTML"

    async def test_run_job_tool_step_uses_broadcast_format(self) -> None:
        """Tool-only pipeline: result uses the _broadcast (✅ icon) format."""
        bot = _make_bot()
        job = _make_job(
            name="tool-only",
            pipeline=[SchedulePipelineStep(name="echo_tool", kind="tool", value="echo tool-output")],
        )
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[1])
        await scheduler._run_job(job)

        calls = bot.send_message.call_args_list
        assert len(calls) >= 1
        msg = calls[0][0][1]
        assert "✅" in msg
        assert "tool-output" in msg
        # All calls must use HTML parse mode
        for call in calls:
            assert call.kwargs.get("parse_mode") == "HTML"

    async def test_run_job_error_uses_broadcast_format(self) -> None:
        """Error always uses _broadcast format (❌ icon), regardless of step type."""
        bot = _make_bot()
        job = _make_job(
            name="error-format",
            pipeline=[SchedulePipelineStep(name="fail_tool", kind="tool", value="bash -c 'exit 1'")],
        )
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[1])
        await scheduler._run_job(job)

        calls = bot.send_message.call_args_list
        assert len(calls) >= 1
        msg = calls[0][0][1]
        assert "❌" in msg
        # All calls must use HTML parse mode
        for call in calls:
            assert call.kwargs.get("parse_mode") == "HTML"

    async def test_run_job_history_file_path_uses_sanitized_name(self, tmp_path: Path) -> None:
        """Special characters in job name are sanitized in the log file path."""
        from archon.ai.agent_logger import sanitize_name
        history_config = _make_history_config(tmp_path)
        job = _make_job(
            name="My Job / Special!",
            pipeline=[SchedulePipelineStep(name="echo_tool", kind="tool", value="echo hi")],
        )
        cfg = _make_config(job)
        scheduler = _make_scheduler_with_history(cfg, history_config=history_config)
        await scheduler._run_job(job)

        schedule_dir = tmp_path / "history" / "schedule"
        files = list(schedule_dir.glob("*.md"))
        assert len(files) == 1
        safe = sanitize_name("My Job / Special!")
        # The sanitized name must appear in the file stem
        assert safe in files[0].stem
        # Literal slash and space must not appear in the filename
        assert "/" not in files[0].name
        assert " " not in files[0].name

    async def test_run_job_prompt_step_header_escapes_html_in_job_name(self, tmp_path: Path) -> None:
        """Job names with HTML-special chars are escaped in the header."""
        bot = _make_bot()
        job = _make_job(
            name="<evil>&job",
            pipeline=[SchedulePipelineStep(name="say_prompt", kind="prompt", value="hello")],
        )
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[1])

        mock_session = _mock_session_for_prompt("result")
        with patch("archon.ai.job_scheduler.ClaudeSession", return_value=mock_session):
            await scheduler._run_job(job)

        calls = bot.send_message.call_args_list
        headers = [call[0][1] for call in calls if "🗓" in call[0][1]]
        assert len(headers) == 1
        assert "&lt;evil&gt;&amp;job" in headers[0]
        assert "<evil>" not in headers[0]

    async def test_run_job_prompt_step_notifies_all_users(self) -> None:
        """Both allowed users receive header + response for a prompt step."""
        bot = _make_bot()
        job = _make_job(
            name="multi-user",
            pipeline=[SchedulePipelineStep(name="say_prompt", kind="prompt", value="say hello")],
        )
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[10, 20])

        mock_session = _mock_session_for_prompt("hello!")
        with patch("archon.ai.job_scheduler.ClaudeSession", return_value=mock_session):
            await scheduler._run_job(job)

        sent_ids = [call[0][0] for call in bot.send_message.call_args_list]
        assert 10 in sent_ids
        assert 20 in sent_ids

    async def test_run_job_prompt_step_continues_after_send_failure(self) -> None:
        """If sending to user 10 fails, user 20 still receives the message."""
        bot = _make_bot()

        call_count: dict[int, int] = {10: 0, 20: 0}

        async def _side_effect(user_id: int, msg: str, **kwargs: object) -> None:
            call_count[user_id] = call_count.get(user_id, 0) + 1
            if user_id == 10:
                raise Exception("send failed")

        bot.send_message = AsyncMock(side_effect=_side_effect)

        job = _make_job(
            name="continue-test",
            pipeline=[SchedulePipelineStep(name="say_prompt", kind="prompt", value="say hi")],
        )
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[10, 20])

        mock_session = _mock_session_for_prompt("hi there")
        with patch("archon.ai.job_scheduler.ClaudeSession", return_value=mock_session):
            await scheduler._run_job(job)

        assert call_count.get(20, 0) >= 1

    async def test_run_job_send_parts_sends_to_all_users_even_if_one_fails(self) -> None:
        """_send_parts_to_all continues sending to remaining users if one fails."""
        bot = _make_bot()
        sent_to: list[int] = []

        async def _side_effect(user_id: int, msg: str, **kwargs: object) -> None:
            sent_to.append(user_id)
            if user_id == 10:
                raise Exception("network error")

        bot.send_message = AsyncMock(side_effect=_side_effect)
        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[10, 20, 30])

        await scheduler._send_parts_to_all(
            header="🗓 <b>Scheduled: test</b>",
            parts=["some response"],
            job_name="test",
        )

        assert 20 in sent_to
        assert 30 in sent_to

    async def test_run_job_prompt_step_no_intermediate_telegram_messages(self) -> None:
        """With 4 events in the stream, only the final header + response are sent (not per-event)."""
        bot = _make_bot()
        job = _make_job(
            name="no-intermediate",
            pipeline=[SchedulePipelineStep(name="say_prompt", kind="prompt", value="go")],
        )
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[1])

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.stop = AsyncMock()

        async def _mock_send(prompt: str):  # type: ignore[return]
            from archon.ai.event_mapper import ThinkingResult, ToolStarted, ToolResult, Response
            yield ThinkingResult(content="thinking")
            yield ToolStarted(name="bash", input={"command": "ls"})
            yield ToolResult(content="file.txt")
            yield Response(content="done!")

        mock_session.send = _mock_send

        with patch("archon.ai.job_scheduler.ClaudeSession", return_value=mock_session):
            await scheduler._run_job(job)

        # Only header + response parts should be sent (not 4 per-event messages)
        assert bot.send_message.await_count == 2

    async def test_run_job_prompt_step_output_matches_format_event(self) -> None:
        """The response parts sent match what format_event(Response(...), SplitStrategy(), ...) returns."""
        from archon.ai.event_mapper import Response as ResponseEvent
        from archon.ai.truncation import SplitStrategy
        from archon.chat.telegram_formatter import format_event
        from archon.ai.job_scheduler import _TELEGRAM_MAX_LEN

        bot = _make_bot()
        response_text = "This is the scheduled job output."
        job = _make_job(
            name="format-match",
            pipeline=[SchedulePipelineStep(name="say_prompt", kind="prompt", value="say something")],
        )
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[1])

        mock_session = _mock_session_for_prompt(response_text)
        with patch("archon.ai.job_scheduler.ClaudeSession", return_value=mock_session):
            await scheduler._run_job(job)

        expected_parts = format_event(
            ResponseEvent(content=response_text),
            SplitStrategy(),
            _TELEGRAM_MAX_LEN,
            None,
        )

        sent_msgs = [call[0][1] for call in bot.send_message.call_args_list]
        # First message is the header; rest are response parts
        actual_response_parts = sent_msgs[1:]
        assert actual_response_parts == expected_parts

    async def test_run_job_tool_then_prompt_uses_send_parts(self) -> None:
        """Pipeline ending with a prompt step uses _send_parts_to_all (header + response parts)."""
        bot = _make_bot()
        tool_step = SchedulePipelineStep(name="t", kind="tool", value="echo tool-result")
        prompt_step = SchedulePipelineStep(name="p", kind="prompt", value="summarize {t}")
        job = _make_job(
            name="mixed",
            pipeline=[tool_step, prompt_step],
        )
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[42])

        mock_session = _mock_session_for_prompt("summary")
        with patch("archon.ai.job_scheduler.ClaudeSession", return_value=mock_session), \
             patch.object(scheduler, "_run_tool", return_value="tool output"):
            await scheduler._run_job(job)

        calls = bot.send_message.call_args_list
        msgs = [call[0][1] for call in calls]
        # Must send a header (🗓) then response content — uses _send_parts_to_all
        assert any("🗓" in m for m in msgs)
        assert any("summary" in m or "✅" in m for m in msgs)
        # Must NOT use plain _broadcast-only format without a header
        assert not all("🗓" not in m for m in msgs)

    async def test_run_job_prompt_then_tool_uses_broadcast(self) -> None:
        """Pipeline ending with a tool step uses _broadcast (✅ Scheduled prefix, no header)."""
        bot = _make_bot()
        prompt_step = SchedulePipelineStep(name="p", kind="prompt", value="get cmd")
        tool_step = SchedulePipelineStep(name="t", kind="tool", value="echo file.txt")
        job = _make_job(
            name="mixed2",
            pipeline=[prompt_step, tool_step],
        )
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[42])

        mock_session = _mock_session_for_prompt("ls -la")
        with patch("archon.ai.job_scheduler.ClaudeSession", return_value=mock_session), \
             patch.object(scheduler, "_run_tool", return_value="file.txt"):
            await scheduler._run_job(job)

        calls = bot.send_message.call_args_list
        msgs = [call[0][1] for call in calls]
        # Must use _broadcast: contains ✅ Scheduled prefix
        assert any("✅" in m for m in msgs)
        # Must NOT use _send_parts_to_all header format
        assert not any("🗓" in m for m in msgs)
