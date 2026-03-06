"""Cron job scheduler — runs pipeline jobs on a cron expression schedule.

Each job defines a pipeline of steps that execute sequentially.  Each step
references earlier steps by name via ``{ref}`` placeholders in its value.
Two step types are supported:

- keys ending in ``_tool``   — shell command run via ``asyncio.create_subprocess_exec``
- keys ending in ``_prompt`` — prompt sent to an isolated ``ClaudeSession``

On completion (or failure) the job broadcasts a Telegram notification to all
``allowed_user_ids`` from the access config.
"""
import asyncio
import html
import logging
import re
import shlex
import tomlkit
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter  # type: ignore[import-untyped]

from archon.ai.claude_session import ClaudeSession
from archon.ai.truncation import SplitStrategy
from archon.chat.md_formatter import md_to_html
from archon.chat.telegram_delivery import render_split_messages
from archon.config.loader import CronConfig, CronJobConfig, CronPipelineStep, REF_RE, atomic_write

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger("archon")
_TELEGRAM_MAX_LEN = 4000

def _substitute_refs(value: str, outputs: dict[str, str]) -> str:
    """Replace all {ref} patterns in value with matching outputs. Leave unmatched refs as-is."""
    def _replace(m: re.Match[str]) -> str:
        key = m.group(1)
        return outputs.get(key, m.group(0))
    return REF_RE.sub(_replace, value)


@dataclass
class JobStatus:
    """Runtime status for a single cron job."""
    name: str
    last_run: datetime | None = None
    last_fire_at: datetime | None = None    # when the scheduler last queued this job
    last_result: str | None = None
    last_error: str | None = None
    run_count: int = 0
    is_running: bool = False


class CronScheduler:
    """Asyncio-based cron scheduler.

    Uses ``croniter`` to interpret cron expressions.  A background asyncio task
    ticks every 60 seconds and fires any jobs whose previous cron slot falls
    within the last 60 seconds and has not already been fired this slot.
    """

    def __init__(
        self,
        config: CronConfig,
        bot: "Bot",
        allowed_user_ids: list[int],
        model: str | None = None,
        jobs_dir_base: str | Path | None = None,
        cwd: str | None = None,
    ) -> None:
        self._config = config
        self._bot = bot
        self._allowed_user_ids = allowed_user_ids
        self._model = model
        self._jobs_dir_base = Path(jobs_dir_base) if jobs_dir_base is not None else None
        self._cwd = cwd
        self._task: asyncio.Task[None] | None = None
        self._statuses: dict[str, JobStatus] = {
            j.name: JobStatus(name=j.name) for j in config.jobs
        }

    # ── Public API ────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the background scheduling loop.

        Does nothing if the scheduler is disabled in config.
        """
        if not self._config.enabled:
            logger.info("CronScheduler disabled — skipping start")
            return
        self._task = asyncio.create_task(self._loop(), name="cron-loop")
        logger.info("CronScheduler started with %d job(s)", len(self._config.jobs))

    async def stop(self) -> None:
        """Cancel the background loop and wait for it to finish."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("CronScheduler stopped")

    @property
    def job_statuses(self) -> dict[str, JobStatus]:
        """Return a shallow copy of the job status dict."""
        return dict(self._statuses)

    @property
    def job_configs(self) -> list[CronJobConfig]:
        """Return the list of configured cron jobs."""
        return list(self._config.jobs)

    def get_job_config(self, name: str) -> CronJobConfig | None:
        """Return the job config for *name*, or ``None`` if not found."""
        return next((j for j in self._config.jobs if j.name == name), None)

    def next_run_times(self) -> dict[str, datetime | None]:
        """Return the next scheduled run time for each configured job.

        Disabled jobs map to ``None``.  Jobs with an unparseable cron
        expression also map to ``None``.  Jobs with a ``timezone`` setting
        return a timezone-aware datetime in that timezone; jobs without one
        return a timezone-aware datetime in the local system timezone.
        """
        result: dict[str, datetime | None] = {}
        for job in self._config.jobs:
            if not job.enabled or job.validation_error is not None:
                result[job.name] = None
                continue
            try:
                if job.timezone:
                    tz = ZoneInfo(job.timezone)
                    now: datetime = datetime.now(tz)
                else:
                    now = datetime.now(timezone.utc).astimezone()
                it = croniter(job.schedule, now)
                result[job.name] = it.get_next(datetime)
            except Exception:
                result[job.name] = None
        return result

    def reload_jobs(self) -> None:
        """Re-read job configs from disk and update in-memory state.

        Only runs when ``jobs_dir_base`` was provided at construction time
        (i.e. in production via the gateway).  When it is absent (e.g. in
        tests that build a scheduler with synthetic in-memory jobs) the call
        is a safe no-op so existing state is preserved.

        Behaviour:
        - Loads all ``*.toml`` files from the configured ``jobs_dir``.
        - Preserves runtime status (``last_run``, ``run_count``, …) for jobs
          that are still present after the reload.
        - Adds a blank ``JobStatus`` for brand-new jobs.
        - Removes ``JobStatus`` entries for jobs that no longer exist on disk.
        """
        if self._jobs_dir_base is None:
            logger.debug("CronScheduler.reload_jobs: jobs_dir_base not set, skipping")
            return

        from archon.config.loader import load_cron_jobs  # local import avoids circular dep

        new_jobs = load_cron_jobs(self._config.jobs_dir, base_dir=self._jobs_dir_base)
        self._config.jobs = new_jobs

        new_names = {j.name for j in new_jobs}

        # Add blank status for brand-new jobs
        for job in new_jobs:
            if job.name not in self._statuses:
                self._statuses[job.name] = JobStatus(name=job.name)

        # Drop statuses for jobs removed from disk
        for name in list(self._statuses):
            if name not in new_names:
                del self._statuses[name]

        logger.info("CronScheduler reloaded %d job(s) from disk", len(new_jobs))

    # ── Internal loop ─────────────────────────────────────────────

    async def _loop(self) -> None:
        """Tick every 60 seconds and fire any due jobs as concurrent tasks."""
        while True:
            now = datetime.now(timezone.utc).astimezone()
            for job in self._config.jobs:
                if not job.enabled:
                    continue
                if self._should_fire(job, now):
                    # Record fire time *before* creating the task to prevent
                    # the next tick from firing the same slot again.
                    self._statuses[job.name].last_fire_at = now
                    asyncio.create_task(
                        self._run_job(job), name=f"cron-{job.name}"
                    )
            await asyncio.sleep(60)

    def _should_fire(self, job: CronJobConfig, now: datetime) -> bool:
        """Return True if *job* is due to fire at *now*.

        Conditions:
          1. The most recent cron slot (``prev``) is within the last 60 seconds.
          2. The job has not already been fired at or after ``prev``
             (prevents duplicate fires on back-to-back ticks in the same minute).

        When ``job.timezone`` is set, the cron expression is evaluated in that
        timezone so that e.g. ``0 9 * * *`` fires at 9 AM local-to-the-job time
        regardless of the machine's system timezone.  ``last_fire_at`` is always
        stored as a timezone-aware datetime in the local system timezone.
        """
        try:
            if job.timezone:
                tz = ZoneInfo(job.timezone)
                tz_now = datetime.now(tz)
                it = croniter(job.schedule, tz_now)
                prev_aware: datetime = it.get_prev(datetime)
                if (tz_now - prev_aware).total_seconds() >= 60:
                    return False
                prev: datetime = prev_aware.astimezone()
            else:
                it = croniter(job.schedule, now)
                prev = it.get_prev(datetime)
                if (now - prev).total_seconds() >= 60:
                    return False
            status = self._statuses[job.name]
            if status.last_fire_at is not None and status.last_fire_at >= prev:
                return False
            return True
        except Exception:
            logger.warning("Invalid cron expression for job %r: %r", job.name, job.schedule)
            return False

    # ── Job execution ─────────────────────────────────────────────

    async def _run_job(self, job: CronJobConfig) -> None:
        """Execute all pipeline steps for *job* sequentially."""
        if not job.enabled:
            return
        status = self._statuses[job.name]
        if status.is_running:
            logger.warning("Job %r already running — skipping duplicate fire", job.name)
            return

        if job.validation_error is not None:
            logger.warning(
                "Cron job %r skipped — config error: %s", job.name, job.validation_error
            )
            await self._disable_invalid_job(job)
            await self._broadcast_validation_error(job)
            return

        status.is_running = True
        status.last_run = datetime.now(timezone.utc).astimezone()
        status.run_count += 1
        logger.info("Cron job %r started (run #%d)", job.name, status.run_count)

        try:
            outputs: dict[str, str] = {}
            last_output = ""
            for step in job.pipeline:
                resolved_value = _substitute_refs(step.value, outputs)
                if step.kind == "tool":
                    result = await self._run_tool(resolved_value, job.timeout_seconds)
                else:
                    result = await self._run_prompt(resolved_value, job.timeout_seconds)
                outputs[step.name] = result
                last_output = result

            status.last_result = last_output
            status.last_error = None
            logger.info("Cron job %r finished (%d chars)", job.name, len(last_output))

            await self._broadcast(job.name, last_output, error=False)

        except Exception as exc:
            status.last_error = str(exc)
            status.last_result = None
            logger.exception("Cron job %r failed", job.name)
            await self._broadcast(job.name, str(exc), error=True)

        finally:
            status.is_running = False

    async def _run_tool(self, command: str, timeout: float) -> str:
        """Run *command* as a subprocess with empty stdin; return stdout.

        Relative paths in *command* are resolved against ``self._cwd`` when
        set (the project working directory).  If ``self._cwd`` is ``None`` the
        subprocess inherits the daemon's working directory.
        """
        cmd = shlex.split(command)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(b""), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise RuntimeError(
                f"Tool step timed out after {timeout}s: {command!r}"
            )

        if proc.returncode != 0:
            err_text = stderr.decode().strip()
            raise RuntimeError(
                f"Tool step failed (exit {proc.returncode}): {err_text}"
            )

        return stdout.decode().strip()

    async def _run_prompt(self, prompt_text: str, timeout: float) -> str:
        """Run *prompt_text* through an isolated ClaudeSession; return the response text."""
        from archon.ai.event_mapper import Response  # local import avoids circular dep

        session = ClaudeSession(model=self._model)
        await session.start()
        try:
            async def _collect() -> str:
                result = ""
                async for event in session.send(prompt_text):
                    if isinstance(event, Response):
                        result = event.content
                return result

            try:
                return await asyncio.wait_for(_collect(), timeout=timeout)
            except asyncio.TimeoutError:
                raise RuntimeError(
                    f"Prompt step timed out after {timeout}s: {prompt_text[:50]!r}"
                )
        finally:
            await session.stop()

    async def _disable_invalid_job(self, job: CronJobConfig) -> None:
        """Disable *job* in memory and persist enabled=false to its TOML file on disk.

        This ensures the user is notified exactly once about the config error.
        The user must manually fix the TOML file and set ``enabled = true`` to re-activate.
        """
        job.enabled = False
        if self._jobs_dir_base is None:
            return  # test context — no TOML file to update
        job_file = Path(self._jobs_dir_base) / self._config.jobs_dir / f"{job.name}.toml"
        if not job_file.exists():
            return
        try:
            content = await asyncio.get_event_loop().run_in_executor(None, job_file.read_text)
            doc = tomlkit.parse(content)
            doc["enabled"] = False
            atomic_write(job_file, tomlkit.dumps(doc))
            logger.info("Disabled job %r on disk (validation error)", job.name)
        except Exception as exc:
            logger.warning("Failed to disable job %r on disk: %s", job.name, exc)

    async def _broadcast_validation_error(self, job: CronJobConfig) -> None:
        """Send a one-time validation error notification for *job* to all allowed users."""
        body = (
            f"⚠️ <b>Cron: {html.escape(job.name)}</b>\n"
            f"Job disabled — config error:\n"
            f"<code>{html.escape(job.validation_error or '')}</code>\n\n"
            f"Fix the config file and set <code>enabled = true</code> to re-activate."
        )
        for user_id in self._allowed_user_ids:
            try:
                await self._bot.send_message(user_id, body, parse_mode="HTML")
            except Exception as exc:
                logger.warning(
                    "Failed to notify user %d for invalid job %r: %s", user_id, job.name, exc
                )

    async def _broadcast(self, job_name: str, text: str, *, error: bool) -> None:
        """Send a Telegram notification about *job_name* to all allowed users."""
        icon = "❌" if error else "✅"
        prefix = f"{icon} <b>Cron: {html.escape(job_name)}</b>\n"
        messages = render_split_messages(
            text,
            prefix,
            SplitStrategy(),
            _TELEGRAM_MAX_LEN,
            md_to_html,
        )
        for user_id in self._allowed_user_ids:
            for msg in messages:
                try:
                    await self._bot.send_message(user_id, msg, parse_mode="HTML")
                except Exception as exc:
                    logger.warning(
                        "Failed to notify user %d for job %r: %s", user_id, job_name, exc
                    )
