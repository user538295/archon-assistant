"""Job scheduler — runs pipeline jobs on a cron expression schedule.

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
import os
import re
import shlex
import stat
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
from archon.config.loader import HistoryConfig, NotificationsConfig, ScheduleConfig, ScheduledJobConfig, SchedulePipelineStep, REF_RE, atomic_write

if TYPE_CHECKING:
    from aiogram import Bot
    from archon.ai.event_mapper import Event

logger = logging.getLogger("archon")


def _log_task_exception(task: asyncio.Task[None], job_name: str) -> None:
    """Done-callback: log any unhandled exception from a scheduled job task."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Scheduled job %r failed", job_name, exc_info=exc)
_TELEGRAM_MAX_LEN = 4000

def _substitute_refs(value: str, outputs: dict[str, str]) -> str:
    """Replace all {ref} patterns in value with matching outputs. Leave unmatched refs as-is."""
    def _replace(m: re.Match[str]) -> str:
        key = m.group(1)
        return outputs.get(key, m.group(0))
    return REF_RE.sub(_replace, value)


@dataclass
class JobStatus:
    """Runtime status for a single scheduled job."""
    name: str
    last_run: datetime | None = None
    last_fire_at: datetime | None = None    # when the scheduler last queued this job
    last_result: str | None = None
    last_error: str | None = None
    run_count: int = 0
    is_running: bool = False


class _ScheduleJobLogWriter:
    """Writes a scheduled job's events to a dedicated Markdown log file.

    Events are appended to disk on every :meth:`record_event` call so partial
    logs are readable even if the process is interrupted.

    File format::

        # Scheduled: myjob · 2026-03-28
        **Started:** 10:05:30 UTC

        ---

        ## 📝 Prompt · 10:05:30 UTC

        hello

        ---

        ### ✅ Response · 10:05:45 UTC

        All done!

        ## ✅ Completed · 10:05:45 UTC

        **Duration:** 0:00:15

        ---
    """

    def __init__(
        self,
        path: Path,
        job_name: str,
        started_at: datetime,
        prompt: str = "",
        suppressed_tools: frozenset[str] | None = None,
        suppressed_events: frozenset[str] | None = None,
    ) -> None:
        if started_at.tzinfo is None:
            raise ValueError("started_at must be timezone-aware")
        from archon.ai.event_renderer import EventRenderer  # local import avoids circular dep

        self._path = self._claim_path(path)
        self._started_at = started_at
        self._renderer = EventRenderer(
            suppressed_tools=suppressed_tools,
            suppressed_events=suppressed_events,
        )
        self._write_header(job_name, started_at, prompt)

    @property
    def path(self) -> Path:
        """Absolute path of the log file."""
        return self._path

    async def record_event(self, event: "Event") -> None:
        """Render *event* and append to the log file immediately."""
        text = self._renderer.render(event)
        if text:
            await asyncio.to_thread(self._sync_append, text)

    async def finalize(self, error: str | None = None) -> None:
        """Append success or failure footer with duration."""
        now = datetime.now(timezone.utc)
        ts = now.strftime("%H:%M:%S UTC")
        delta = now - self._started_at
        total_s = int(delta.total_seconds())
        h = total_s // 3600
        m = (total_s % 3600) // 60
        s = total_s % 60
        if error:
            footer = f"\n## ❌ Failed · {ts}\n\n{error}\n\n**Duration:** {h}:{m:02d}:{s:02d}\n\n---\n"
        else:
            footer = f"\n## ✅ Completed · {ts}\n\n**Duration:** {h}:{m:02d}:{s:02d}\n\n---\n"
        await asyncio.to_thread(self._sync_append, footer)

    def _sync_append(self, text: str) -> None:
        with self._path.open("a", encoding="utf-8") as f:
            f.write(text)

    def _write_header(self, job_name: str, started_at: datetime, prompt: str) -> None:
        date_str = started_at.strftime("%Y-%m-%d")
        ts = started_at.strftime("%H:%M:%S UTC")
        content = f"# Scheduled: {job_name} · {date_str}\n**Started:** {ts}\n\n---\n"
        if prompt:
            content += f"\n## 📝 Prompt · {ts}\n\n{prompt}\n\n---\n"
        self._path.write_text(content, encoding="utf-8")

    @staticmethod
    def _claim_path(path: Path) -> Path:
        """Atomically claim *path*, returning a collision-free alternative if needed."""
        path.parent.mkdir(parents=True, exist_ok=True)
        counter = 2
        candidate = path
        while True:
            try:
                candidate.open("x").close()
                return candidate
            except FileExistsError:
                candidate = path.parent / f"{path.stem}_{counter}{path.suffix}"
                counter += 1


class JobScheduler:
    """Asyncio-based job scheduler.

    Uses ``croniter`` to interpret cron expressions.  A background asyncio task
    ticks every 60 seconds and fires any jobs whose previous cron slot falls
    within the last 60 seconds and has not already been fired this slot.
    """

    def __init__(
        self,
        config: ScheduleConfig,
        bot: "Bot",
        allowed_user_ids: list[int],
        model: str | None = None,
        jobs_dir_base: str | Path | None = None,
        cwd: str | None = None,
        notifications: NotificationsConfig | None = None,
        history_config: HistoryConfig | None = None,
    ) -> None:
        self._config = config
        self._bot = bot
        self._allowed_user_ids = allowed_user_ids
        self._model = model
        self._jobs_dir_base = Path(jobs_dir_base) if jobs_dir_base is not None else None
        self._cwd = cwd
        self._notifications = notifications
        self._history_config = history_config
        self._task: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._statuses: dict[str, JobStatus] = {
            j.name: JobStatus(name=j.name) for j in config.jobs
        }
        self._last_snapshot: dict[Path, float] = {}
        self._snapshot_primed = False
        self._check_jobs_dir_permissions()

    def _check_world_writable(self, path: Path, label: str) -> None:
        """Warn if *path* is world-writable."""
        try:
            mode = os.stat(path).st_mode
            if mode & stat.S_IWOTH:
                logger.warning(
                    "%s (%s) is world-writable; job tool execution is a security risk",
                    label, path,
                )
        except OSError:
            pass

    def _check_jobs_dir_permissions(self) -> None:
        """Warn if jobs_dir_base or any bundle dir is world-writable."""
        if self._jobs_dir_base is None:
            return
        self._check_world_writable(self._jobs_dir_base, "jobs_dir")
        for job in self._config.jobs:
            if job.source_dir is None:
                continue
            self._check_world_writable(job.source_dir, f"bundle '{job.name}'")
            scripts_dir = job.source_dir / "scripts"
            if scripts_dir.is_dir():
                self._check_world_writable(scripts_dir, f"bundle '{job.name}' scripts")

    # ── Public API ────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the background scheduling loop.

        Does nothing if the scheduler is disabled in config.
        """
        if not self._config.enabled:
            logger.info("JobScheduler disabled — skipping start")
            return
        self._task = asyncio.create_task(self._loop(), name="schedule-loop")
        asyncio.create_task(self._broadcast_legacy_warnings(), name="schedule-legacy-warn")
        logger.info("JobScheduler started with %d job(s)", len(self._config.jobs))

    async def stop(self) -> None:
        """Cancel the background loop and all running job tasks, then wait for them."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._tasks:
            for task in list(self._tasks):
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()
        logger.info("JobScheduler stopped")

    @property
    def job_statuses(self) -> dict[str, JobStatus]:
        """Return a shallow copy of the job status dict."""
        return dict(self._statuses)

    @property
    def job_configs(self) -> list[ScheduledJobConfig]:
        """Return the list of configured scheduled jobs."""
        return list(self._config.jobs)

    @property
    def jobs_dir(self) -> Path | None:
        """Return the absolute jobs directory path, or None if not configured."""
        if self._jobs_dir_base is None:
            return None
        return Path(self._jobs_dir_base) / self._config.jobs_dir

    def get_job_config(self, name: str) -> ScheduledJobConfig | None:
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
                it = croniter(job.cron, now)
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
        - Loads all job bundles (``name/job.toml``) and flat ``*.toml`` files
          from the configured ``jobs_dir``.
        - Preserves runtime status (``last_run``, ``run_count``, …) for jobs
          that are still present after the reload.
        - Adds a blank ``JobStatus`` for brand-new jobs.
        - Removes ``JobStatus`` entries for jobs that no longer exist on disk.
        """
        if self._jobs_dir_base is None:
            logger.debug("JobScheduler.reload_jobs: jobs_dir_base not set, skipping")
            return

        from archon.config.loader import load_scheduled_jobs  # local import avoids circular dep

        new_jobs = load_scheduled_jobs(self._config.jobs_dir, base_dir=self._jobs_dir_base)
        self._config.jobs = new_jobs

        new_names = {j.name for j in new_jobs}

        # Add blank status for brand-new jobs
        for job in new_jobs:
            if job.name not in self._statuses:
                self._statuses[job.name] = JobStatus(name=job.name)

        # Drop statuses for jobs removed from disk (preserve running jobs
        # to avoid losing state during atomic editor saves: delete→write)
        for name in list(self._statuses):
            if name not in new_names and not self._statuses[name].is_running:
                del self._statuses[name]

        self._check_jobs_dir_permissions()
        # Sync snapshot so auto-reload doesn't redundantly re-reload after
        # a manual reload (e.g. /scheduled command).
        self._last_snapshot = self._file_snapshot()
        logger.info("JobScheduler reloaded %d job(s) from disk", len(new_jobs))

    # ── Auto-reload ────────────────────────────────────────────────

    def _file_snapshot(self) -> dict[Path, float]:
        """Return ``{filepath: mtime}`` for all job files in the schedules dir.

        Covers bundle ``job.toml`` files and flat ``*.toml`` files.
        Returns an empty dict when ``jobs_dir_base`` is unset (test mode)
        or the directory does not exist.  Individual stat failures are
        skipped (TOCTOU race: file deleted between iterdir and stat).
        """
        if self._jobs_dir_base is None:
            return {}
        jobs_dir = Path(self._jobs_dir_base) / self._config.jobs_dir
        try:
            if not jobs_dir.exists():
                return {}
            result: dict[Path, float] = {}
            for entry in jobs_dir.iterdir():
                if entry.is_symlink():
                    continue
                try:
                    if entry.is_dir():
                        job_toml = entry / "job.toml"
                        if job_toml.exists():
                            result[job_toml] = job_toml.stat().st_mtime
                    elif entry.is_file() and entry.suffix == ".toml":
                        result[entry] = entry.stat().st_mtime
                except OSError:
                    continue  # file deleted between iterdir() and stat()
        except OSError:
            logger.warning("Failed to scan jobs directory: %s", jobs_dir)
            return {}
        return result

    def _auto_reload_if_changed(self) -> None:
        """Compare current file snapshot to the cached one; reload on change.

        On the first call, primes the snapshot cache.  If jobs were already
        loaded at startup (``self._config.jobs`` is non-empty), the first
        call returns without reloading.  If no jobs are loaded but files
        exist on disk, the first call triggers a reload to pick them up.
        """
        snapshot = self._file_snapshot()
        if not self._snapshot_primed:
            self._snapshot_primed = True
            self._last_snapshot = snapshot
            if not self._config.jobs and snapshot:
                # No jobs loaded at startup but files exist → reload to discover them
                logger.info("Auto-reloading jobs (file changes detected)")
                self.reload_jobs()
            return
        if snapshot != self._last_snapshot:
            self._last_snapshot = snapshot
            logger.info("Auto-reloading jobs (file changes detected)")
            self.reload_jobs()

    # ── Internal loop ─────────────────────────────────────────────

    async def _loop(self) -> None:
        """Tick every 60 seconds and fire any due jobs as concurrent tasks."""
        while True:
            try:
                self._auto_reload_if_changed()
                now = datetime.now(timezone.utc).astimezone()
                for job in self._config.jobs:
                    if not job.enabled:
                        continue
                    if self._should_fire(job, now):
                        # Record fire time *before* creating the task to prevent
                        # the next tick from firing the same slot again.
                        self._statuses[job.name].last_fire_at = now
                        task = asyncio.create_task(
                            self._run_job(job), name=f"schedule-{job.name}"
                        )
                        self._tasks.add(task)
                        task.add_done_callback(self._tasks.discard)
                        task.add_done_callback(
                            lambda t, name=job.name: _log_task_exception(t, name)
                        )
            except Exception:
                logger.exception("Scheduler tick failed, continuing")
            await asyncio.sleep(60)

    def _should_fire(self, job: ScheduledJobConfig, now: datetime) -> bool:
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
                it = croniter(job.cron, tz_now)
                prev_aware: datetime = it.get_prev(datetime)
                if prev_aware.tzinfo is None:
                    prev_aware = prev_aware.replace(tzinfo=tz)
                if (tz_now - prev_aware).total_seconds() >= 60:
                    return False
                prev: datetime = prev_aware.astimezone()
            else:
                it = croniter(job.cron, now)
                prev = it.get_prev(datetime)
                if (now - prev).total_seconds() >= 60:
                    return False
            status = self._statuses[job.name]
            if status.last_fire_at is not None and status.last_fire_at >= prev:
                return False
            return True
        except (TypeError, ValueError, ZoneInfoNotFoundError):
            logger.warning("Invalid cron expression for job %r: %r", job.name, job.cron)
            return False

    # ── Job execution ─────────────────────────────────────────────

    async def _run_job(self, job: ScheduledJobConfig) -> None:
        """Execute all pipeline steps for *job* sequentially."""
        if not job.enabled:
            return
        status = self._statuses[job.name]
        if status.is_running:
            logger.warning("Job %r already running — skipping duplicate fire", job.name)
            return

        if job.validation_error is not None:
            logger.warning(
                "Scheduled job %r skipped — config error: %s", job.name, job.validation_error
            )
            await self._disable_invalid_job(job)
            await self._broadcast_validation_error(job)
            return

        status.is_running = True
        status.last_run = datetime.now(timezone.utc).astimezone()
        status.run_count += 1
        logger.info("Scheduled job %r started (run #%d)", job.name, status.run_count)

        log_writer: _ScheduleJobLogWriter | None = None
        error_msg: str | None = None
        try:
            if self._config.history_enabled and self._history_config is not None:
                from archon.ai.agent_logger import sanitize_name  # local import avoids circular dep
                started_at = datetime.now(timezone.utc)
                history_dir = Path(self._history_config.directory).expanduser() / "schedule"
                date_str = started_at.strftime("%Y-%m-%d")
                time_str = started_at.strftime("%H-%M-%S")
                safe_name = sanitize_name(job.name)
                log_path = history_dir / f"{date_str}_{time_str}_{safe_name}.md"
                first_prompt = next((s.value for s in job.pipeline if s.kind != "tool"), "")
                log_writer = _ScheduleJobLogWriter(
                    path=log_path,
                    job_name=job.name,
                    started_at=started_at,
                    prompt=first_prompt,
                    suppressed_tools=frozenset(self._history_config.suppressed_tool_results),
                    suppressed_events=frozenset(self._history_config.suppressed_events),
                )
            outputs: dict[str, str] = {}
            last_output = ""
            last_step_was_prompt: bool = False
            for step in job.pipeline:
                resolved_value = _substitute_refs(step.value, outputs)
                if step.kind == "tool":
                    tool_cwd = self._resolve_tool_cwd(job.source_dir)
                    result = await self._run_tool(resolved_value, job.timeout_seconds, cwd=tool_cwd)
                    last_step_was_prompt = False
                else:
                    result = await self._run_prompt(resolved_value, job.timeout_seconds, log_writer=log_writer)
                    last_step_was_prompt = True
                outputs[step.name] = result
                last_output = result

            status.last_result = last_output
            status.last_error = None
            logger.info("Scheduled job %r finished (%d chars)", job.name, len(last_output))

            if last_step_was_prompt:
                from archon.ai.event_mapper import Response
                from archon.chat.telegram_formatter import format_event  # local import
                header = f"🗓 <b>Scheduled: {html.escape(job.name)}</b>"
                parts = format_event(
                    Response(content=last_output),
                    SplitStrategy(),
                    _TELEGRAM_MAX_LEN,
                    self._notifications,
                )
                await self._send_parts_to_all(header, parts, job.name)
            else:
                await self._broadcast(job.name, last_output, error=False)

        except Exception as exc:
            error_msg = str(exc)
            status.last_error = error_msg
            status.last_result = None
            logger.exception("Scheduled job %r failed", job.name)
            await self._broadcast(job.name, error_msg, error=True)

        finally:
            status.is_running = False
            if log_writer is not None:
                try:
                    await log_writer.finalize(error=error_msg)
                except Exception:
                    logger.warning(
                        "Failed to finalize schedule log for job %s", job.name, exc_info=True
                    )

    def _resolve_tool_cwd(self, source_dir: Path | None = None) -> str | None:
        """Return the working directory for a tool subprocess.

        Bundle jobs use *source_dir* so relative paths like ``scripts/foo.sh``
        resolve against the bundle.  Flat-file jobs fall back to
        ``jobs_dir_base``.  Test mode (no ``jobs_dir_base``) uses ``self._cwd``.
        """
        if source_dir is not None:
            return str(source_dir)
        if self._jobs_dir_base is not None:
            return str(self._jobs_dir_base)
        return self._cwd

    def _is_world_writable(self, path: str | Path) -> bool:
        """Return True if *path* has the world-writable bit set."""
        try:
            return bool(os.stat(path).st_mode & stat.S_IWOTH)
        except OSError:
            return False

    async def _run_tool(self, command: str, timeout: float, *, cwd: str | None = None) -> str:
        """Run *command* as a subprocess with empty stdin; return stdout.

        *cwd* sets the subprocess working directory.  When ``None`` the process
        inherits the parent's working directory.
        """
        cmd = shlex.split(command)
        tool_cwd = cwd or (str(self._jobs_dir_base) if self._jobs_dir_base is not None else self._cwd)
        if tool_cwd is not None and self._is_world_writable(tool_cwd):
            logger.error(
                "Refusing to execute tool step: working directory %s is world-writable",
                tool_cwd,
            )
            raise RuntimeError(
                f"Tool step refused: working directory {tool_cwd!r} is world-writable"
            )
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
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

    async def _run_prompt(
        self,
        prompt_text: str,
        timeout: float,
        log_writer: _ScheduleJobLogWriter | None = None,
    ) -> str:
        """Run *prompt_text* through an isolated ClaudeSession; return the response text."""
        from archon.ai.event_mapper import Response  # local import avoids circular dep

        session = ClaudeSession(model=self._model, cwd=self._cwd)
        await session.start()
        try:
            async def _collect() -> str:
                result = ""
                async for event in session.send(prompt_text):
                    if log_writer is not None:
                        try:
                            await log_writer.record_event(event)
                        except Exception:
                            logger.warning("Failed to log event for scheduled job", exc_info=True)
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

    async def _disable_invalid_job(self, job: ScheduledJobConfig) -> None:
        """Disable *job* in memory and persist enabled=false to its TOML file(s) on disk.

        For collisions, disables BOTH the flat file and the bundle file.
        The user must manually fix the TOML file and set ``enabled = true`` to re-activate.
        """
        job.enabled = False
        if self._jobs_dir_base is None:
            return  # test context — no TOML file to update

        files_to_disable: list[Path] = []
        if job.source_dir is not None:
            files_to_disable.append(job.source_dir / "job.toml")
        flat_file = Path(self._jobs_dir_base) / self._config.jobs_dir / f"{job.name}.toml"
        if flat_file.exists() and flat_file not in files_to_disable:
            files_to_disable.append(flat_file)

        for job_file in files_to_disable:
            if not job_file.exists():
                continue
            try:
                loop = asyncio.get_running_loop()
                content = await loop.run_in_executor(None, job_file.read_text)
                doc = tomlkit.parse(content)
                doc["enabled"] = False
                await loop.run_in_executor(None, atomic_write, job_file, tomlkit.dumps(doc))
                logger.info("Disabled job %r on disk (%s)", job.name, job_file)
            except Exception as exc:
                logger.warning("Failed to disable job %r on disk (%s): %s", job.name, job_file, exc)

    async def _broadcast_legacy_warnings(self) -> None:
        """Send a deprecation warning for each flat-file job (once per boot)."""
        if self._jobs_dir_base is None:
            return  # test context — no filesystem jobs, nothing to warn about
        for job in self._config.jobs:
            if job.source_dir is not None:
                continue
            body = (
                f"⚠️ <b>Scheduled: {html.escape(job.name)}</b>\n"
                f"Flat file <code>{html.escape(job.name)}.toml</code> is deprecated.\n"
                f"Migrate to bundle: <code>schedules/{html.escape(job.name)}/job.toml</code>"
            )
            for user_id in self._allowed_user_ids:
                try:
                    await self._bot.send_message(user_id, body, parse_mode="HTML")
                except Exception as exc:
                    logger.warning(
                        "Failed to send legacy warning for job %r to user %d: %s",
                        job.name, user_id, exc,
                    )

    async def _broadcast_validation_error(self, job: ScheduledJobConfig) -> None:
        """Send a one-time validation error notification for *job* to all allowed users."""
        body = (
            f"⚠️ <b>Scheduled: {html.escape(job.name)}</b>\n"
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
        prefix = f"{icon} <b>Scheduled: {html.escape(job_name)}</b>\n"
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

    async def _send_parts_to_all(
        self, header: str, parts: list[str], job_name: str
    ) -> None:
        """Send header + response parts to all allowed users."""
        for user_id in self._allowed_user_ids:
            try:
                await self._bot.send_message(user_id, header, parse_mode="HTML")
                for part in parts:
                    if part:
                        await self._bot.send_message(user_id, part, parse_mode="HTML")
            except Exception:
                logger.warning(
                    "Failed to send scheduled job result to user %s for job %s",
                    user_id,
                    job_name,
                    exc_info=True,
                )
