"""Unit tests for CronScheduler — mocked subprocess and Claude session."""
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.cron_scheduler import CronScheduler, JobStatus, _substitute_refs
from archon.config.loader import CronConfig, CronJobConfig, CronPipelineStep


# ── Helpers ───────────────────────────────────────────────────────


def _make_job(
    name: str = "test_job",
    schedule: str = "* * * * *",
    pipeline: list | None = None,
    timeout_seconds: float = 30.0,
    enabled: bool = True,
    timezone: str | None = None,
    validation_error: str | None = None,
) -> CronJobConfig:
    if pipeline is None:
        pipeline = [CronPipelineStep(name="echo_tool", kind="tool", value="echo hello")]
    return CronJobConfig(
        name=name,
        schedule=schedule,
        pipeline=pipeline,
        timeout_seconds=timeout_seconds,
        enabled=enabled,
        timezone=timezone,
        validation_error=validation_error,
    )


def _make_config(*jobs: CronJobConfig, enabled: bool = True) -> CronConfig:
    return CronConfig(enabled=enabled, jobs=list(jobs))


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


def _make_scheduler(
    config: CronConfig,
    bot: MagicMock | None = None,
    allowed_user_ids: list[int] | None = None,
    **kwargs: object,
) -> CronScheduler:
    return CronScheduler(
        config,
        bot or _make_bot(),
        allowed_user_ids=allowed_user_ids or [],
        **kwargs,
    )


# ── Start / Stop ──────────────────────────────────────────────────


class TestCronSchedulerLifecycle:
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


# ── _should_fire ──────────────────────────────────────────────────


class TestShouldFire:
    def test_should_fire_true_when_cron_matches_now(self) -> None:
        """'* * * * *' fires every minute — should be True within 60s of prev tick."""
        cfg = _make_config(_make_job(schedule="* * * * *"))
        scheduler = _make_scheduler(cfg)
        test_time = datetime(2025, 1, 1, 12, 0, 5)  # 5 seconds after minute boundary
        assert scheduler._should_fire(cfg.jobs[0], test_time) is True

    def test_should_fire_false_for_non_matching_expression(self) -> None:
        """'0 0 1 1 *' (Jan 1 midnight) should not fire on a random summer day."""
        cfg = _make_config(_make_job(schedule="0 0 1 1 *"))
        scheduler = _make_scheduler(cfg)
        test_time = datetime(2025, 6, 15, 12, 30, 0)
        assert scheduler._should_fire(cfg.jobs[0], test_time) is False

    def test_should_fire_false_when_already_fired_this_slot(self) -> None:
        """Should not fire again if last_fire_at >= previous cron slot."""
        cfg = _make_config(_make_job(schedule="* * * * *"))
        scheduler = _make_scheduler(cfg)
        test_time = datetime(2025, 1, 1, 12, 0, 5)
        # Simulate already having fired at 12:00:03
        scheduler._statuses["test_job"].last_fire_at = datetime(2025, 1, 1, 12, 0, 3)
        assert scheduler._should_fire(cfg.jobs[0], test_time) is False

    def test_should_fire_invalid_expression_returns_false(self) -> None:
        """Bad cron expressions must not crash — return False."""
        cfg = _make_config(_make_job(schedule="not_a_cron"))
        scheduler = _make_scheduler(cfg)
        assert scheduler._should_fire(cfg.jobs[0], datetime.now()) is False

    def test_should_fire_true_after_different_slot(self) -> None:
        """Fire again in the next minute even if already fired in the previous minute."""
        cfg = _make_config(_make_job(schedule="* * * * *"))
        scheduler = _make_scheduler(cfg)
        # last_fire_at was in the previous minute (12:01:xx), now it's 12:02:05
        scheduler._statuses["test_job"].last_fire_at = datetime(2025, 1, 1, 12, 1, 30)
        test_time = datetime(2025, 1, 1, 12, 2, 5)
        assert scheduler._should_fire(cfg.jobs[0], test_time) is True


# ── _should_fire with timezone ────────────────────────────────────


class TestShouldFireWithTimezone:
    def test_should_fire_utc_timezone_every_minute(self) -> None:
        """'* * * * *' with timezone='UTC' fires — UTC is always a valid zone."""
        job = _make_job(schedule="* * * * *", timezone="UTC")
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)
        assert scheduler._should_fire(cfg.jobs[0], datetime.now()) is True

    def test_should_fire_with_iana_timezone(self) -> None:
        """Any IANA timezone fires '* * * * *' as expected."""
        job = _make_job(schedule="* * * * *", timezone="Europe/Budapest")
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)
        assert scheduler._should_fire(cfg.jobs[0], datetime.now()) is True

    def test_should_fire_false_when_already_fired_this_slot_with_timezone(self) -> None:
        """Does not fire twice in the same cron slot even with a timezone set."""
        job = _make_job(schedule="* * * * *", timezone="UTC")
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)
        # Set last_fire_at to a very recent local-naive time (within the current minute)
        scheduler._statuses["test_job"].last_fire_at = datetime.now()
        assert scheduler._should_fire(cfg.jobs[0], datetime.now()) is False

    def test_should_fire_invalid_timezone_returns_false(self) -> None:
        """An unrecognised IANA timezone name is caught and returns False."""
        job = _make_job(schedule="* * * * *", timezone="Not/A_Real_Zone")
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)
        assert scheduler._should_fire(cfg.jobs[0], datetime.now()) is False

    def test_next_run_times_tz_aware_for_timezone_job(self) -> None:
        """next_run_times() returns a timezone-aware datetime for a job with timezone."""
        job = _make_job(schedule="0 9 * * *", timezone="Europe/Budapest")
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)
        times = scheduler.next_run_times()
        next_dt = times[job.name]
        assert next_dt is not None
        assert next_dt.tzinfo is not None  # must be tz-aware

    def test_next_run_times_aware_for_job_without_timezone(self) -> None:
        """next_run_times() returns a timezone-aware datetime even for a job without timezone."""
        job = _make_job(schedule="0 9 * * *")  # no timezone
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)
        times = scheduler.next_run_times()
        next_dt = times[job.name]
        assert next_dt is not None
        assert next_dt.tzinfo is not None  # always tz-aware (local system timezone)

    def test_next_run_times_invalid_timezone_returns_none(self) -> None:
        """next_run_times() maps to None when the timezone is invalid."""
        job = _make_job(schedule="0 9 * * *", timezone="Bogus/Zone")
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

    async def test_run_tool_passes_cwd_to_subprocess(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Scheduler passes self._cwd as the working directory to subprocesses."""
        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg, cwd=str(tmp_path))
        result = await scheduler._run_tool("pwd", timeout=10.0)
        assert result == str(tmp_path)

    async def test_run_tool_relative_script_resolves_against_process_cwd(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Relative script paths resolve against the daemon's process CWD, not the session cwd."""
        from unittest.mock import patch

        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        script = scripts_dir / "greet.sh"
        script.write_text("#!/usr/bin/env bash\necho hi\n")
        script.chmod(0o755)

        # session_cwd is a different directory — scripts/ does NOT exist there
        session_cwd = tmp_path / "session_workdir"
        session_cwd.mkdir()

        cfg = _make_config(_make_job())
        scheduler = _make_scheduler(cfg, cwd=str(session_cwd))

        # Pretend the daemon's process CWD is tmp_path (where scripts/ lives)
        with patch("os.getcwd", return_value=str(tmp_path)):
            result = await scheduler._run_tool("scripts/greet.sh", timeout=10.0)
        assert result == "hi"

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

        with patch("archon.ai.cron_scheduler.ClaudeSession", return_value=mock_session):
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

        with patch("archon.ai.cron_scheduler.ClaudeSession", return_value=mock_session):
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

        with patch("archon.ai.cron_scheduler.ClaudeSession", return_value=mock_session):
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

        with patch("archon.ai.cron_scheduler.ClaudeSession", return_value=mock_session):
            with pytest.raises(RuntimeError, match="timed out"):
                await scheduler._run_prompt("test prompt", timeout=0.05)
        mock_session.stop.assert_awaited_once()


# ── _run_job ──────────────────────────────────────────────────────


class TestRunJob:
    async def test_run_job_success_notifies_all_users(self) -> None:
        bot = _make_bot()
        job = _make_job(pipeline=[CronPipelineStep(name="echo_tool", kind="tool", value="echo done")])
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
        job = _make_job(pipeline=[CronPipelineStep(name="echo_tool", kind="tool", value="echo test")])
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[])
        await scheduler._run_job(job)
        bot.send_message.assert_not_awaited()

    async def test_run_job_updates_status(self) -> None:
        job = _make_job(pipeline=[CronPipelineStep(name="result_tool", kind="tool", value="echo result")])
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
        job = _make_job(pipeline=[CronPipelineStep(name="fail_tool", kind="tool", value="bash -c 'exit 1'")])
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[99])
        await scheduler._run_job(job)
        status = scheduler.job_statuses[job.name]
        assert status.last_error is not None
        assert status.last_result is None

    async def test_run_job_failure_sends_error_notification(self) -> None:
        bot = _make_bot()
        job = _make_job(pipeline=[CronPipelineStep(name="fail_tool", kind="tool", value="bash -c 'exit 1'")])
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[99])
        await scheduler._run_job(job)
        bot.send_message.assert_awaited_once()
        msg = bot.send_message.call_args[0][1]
        assert "❌" in msg

    async def test_run_job_skips_when_already_running(self) -> None:
        bot = _make_bot()
        job = _make_job(pipeline=[CronPipelineStep(name="echo_tool", kind="tool", value="echo test")])
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot)
        scheduler._statuses[job.name].is_running = True
        await scheduler._run_job(job)
        # run_count must still be 0 (skipped)
        assert scheduler.job_statuses[job.name].run_count == 0

    async def test_run_job_resets_is_running_after_completion(self) -> None:
        job = _make_job(pipeline=[CronPipelineStep(name="ok_tool", kind="tool", value="echo ok")])
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)
        await scheduler._run_job(job)
        assert scheduler.job_statuses[job.name].is_running is False

    async def test_run_job_run_count_increments_on_each_call(self) -> None:
        job = _make_job(pipeline=[CronPipelineStep(name="run_tool", kind="tool", value="echo run")])
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)
        await scheduler._run_job(job)
        await scheduler._run_job(job)
        assert scheduler.job_statuses[job.name].run_count == 2

    async def test_run_job_notification_contains_check_mark(self) -> None:
        bot = _make_bot()
        job = _make_job(pipeline=[CronPipelineStep(name="success_tool", kind="tool", value="echo success")])
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
        assert all("✅ <b>Cron: nightly</b>\n" in message for message in messages)
        assert any("&lt;" in message for message in messages)

    async def test_broadcast_escapes_job_name(self) -> None:
        bot = _make_bot()
        scheduler = _make_scheduler(_make_config(_make_job()), bot, allowed_user_ids=[1])

        await scheduler._broadcast(job_name='job <& "nightly">', text="done", error=False)

        msg = bot.send_message.call_args[0][1]
        assert '<b>Cron: job &lt;&amp; &quot;nightly&quot;&gt;</b>' in msg

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
    """Tests for CronScheduler.reload_jobs()."""

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
        jobs_dir = tmp_path / "cron.d"
        jobs_dir.mkdir()
        # Write a new job file
        (jobs_dir / "new-job.toml").write_text(
            'schedule = "0 9 * * *"\n[pipeline]\nhi_tool = "echo hi"\n'
        )
        cfg = CronConfig(enabled=True, jobs=[], jobs_dir="cron.d")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)
        scheduler.reload_jobs()
        assert len(scheduler._config.jobs) == 1
        assert scheduler._config.jobs[0].name == "new-job"
        assert "new-job" in scheduler._statuses

    def test_reload_preserves_runtime_status_for_existing_job(self, tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
        """Runtime status (last_run, run_count) survives a reload for jobs still on disk."""
        jobs_dir = tmp_path / "cron.d"
        jobs_dir.mkdir()
        (jobs_dir / "stable.toml").write_text(
            'schedule = "* * * * *"\n[pipeline]\nok_tool = "echo ok"\n'
        )
        cfg = CronConfig(enabled=True, jobs=[], jobs_dir="cron.d")
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
        jobs_dir = tmp_path / "cron.d"
        jobs_dir.mkdir()
        toml_file = jobs_dir / "gone.toml"
        toml_file.write_text(
            'schedule = "0 6 * * *"\n[pipeline]\nbye_tool = "echo bye"\n'
        )
        cfg = CronConfig(enabled=True, jobs=[], jobs_dir="cron.d")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)
        scheduler.reload_jobs()
        assert "gone" in scheduler._statuses
        # Now remove the file and reload again
        toml_file.unlink()
        scheduler.reload_jobs()
        assert "gone" not in scheduler._statuses
        assert scheduler._config.jobs == []

    def test_reload_updates_schedule_for_existing_job(self, tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
        """Changing the schedule in a .toml file is reflected after reload."""
        jobs_dir = tmp_path / "cron.d"
        jobs_dir.mkdir()
        toml_file = jobs_dir / "daily.toml"
        toml_file.write_text(
            'schedule = "0 8 * * *"\n[pipeline]\nmorning_tool = "echo morning"\n'
        )
        cfg = CronConfig(enabled=True, jobs=[], jobs_dir="cron.d")
        scheduler = _make_scheduler(cfg, jobs_dir_base=tmp_path)
        scheduler.reload_jobs()
        assert scheduler._config.jobs[0].schedule == "0 8 * * *"
        # Change the schedule on disk
        toml_file.write_text(
            'schedule = "0 20 * * *"\n[pipeline]\nevening_tool = "echo evening"\n'
        )
        scheduler.reload_jobs()
        assert scheduler._config.jobs[0].schedule == "0 20 * * *"


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
            CronPipelineStep(name="echo_tool", kind="tool", value="echo tooloutput"),
            CronPipelineStep(name="summarize_prompt", kind="prompt", value="Process: {echo_tool}"),
        ]
        job = _make_job(pipeline=pipeline)
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)

        with patch("archon.ai.cron_scheduler.ClaudeSession", return_value=mock_session):
            await scheduler._run_job(job)

        assert captured == ["Process: tooloutput"]

    async def test_ref_in_tool_command_substituted(self) -> None:
        """A {ref} in a tool command is replaced with the earlier step's output."""
        pipeline = [
            CronPipelineStep(name="word_tool", kind="tool", value="echo hello"),
            CronPipelineStep(name="echo_tool", kind="tool", value="echo {word_tool}"),
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
            CronPipelineStep(name="data_tool", kind="tool", value="echo my_data"),
            CronPipelineStep(name="analyze_prompt", kind="prompt", value="Analyze: {data_tool}"),
        ]
        job = _make_job(pipeline=pipeline)
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)

        with patch("archon.ai.cron_scheduler.ClaudeSession", return_value=mock_session):
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
            CronPipelineStep(name="a_tool", kind="tool", value="echo aaa"),
            CronPipelineStep(name="b_tool", kind="tool", value="echo bbb"),
            CronPipelineStep(name="merge_prompt", kind="prompt", value="Merge: {a_tool} and {b_tool}"),
        ]
        job = _make_job(pipeline=pipeline)
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)

        with patch("archon.ai.cron_scheduler.ClaudeSession", return_value=mock_session):
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
            CronPipelineStep(name="data_tool", kind="tool", value="echo raw_data"),
            CronPipelineStep(name="summary_prompt", kind="prompt", value="Summarize: {data_tool}"),
        ]
        job = _make_job(pipeline=pipeline)
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg)

        with patch("archon.ai.cron_scheduler.ClaudeSession", return_value=mock_session):
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
            pipeline=[CronPipelineStep(name="echo_tool", kind="tool", value="echo ok")],
            validation_error=None,
        )
        cfg = _make_config(job)
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[1])
        await scheduler._run_job(job)
        assert scheduler.job_statuses[job.name].run_count == 1
        assert scheduler.job_statuses[job.name].last_result == "ok"


# ── Disable-on-error behavior ──────────────────────────────────────


class TestDisableOnError:
    """CronScheduler disables invalid jobs in memory after the first fire."""

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
        jobs_dir = tmp_path / "cron.d"
        jobs_dir.mkdir()
        job_file = jobs_dir / "bad-job.toml"
        doc = tomlkit.document()
        doc["schedule"] = "* * * * *"
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
        cfg = CronConfig(enabled=True, jobs_dir="cron.d", jobs=[job])
        scheduler = CronScheduler(
            config=cfg,
            bot=bot,
            allowed_user_ids=[1],
            jobs_dir_base=str(tmp_path),
        )

        await scheduler._run_job(job)

        # Read the TOML back and verify enabled=false
        updated = tomlkit.parse(job_file.read_text())
        assert updated["enabled"] is False
