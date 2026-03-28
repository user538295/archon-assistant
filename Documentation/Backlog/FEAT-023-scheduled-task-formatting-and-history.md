# FEAT-023 — Scheduled Task Telegram Formatting Fix & History Logging
**Purpose**: Fix markdown→HTML conversion for scheduled task Telegram output to use the same `format_event` code path as regular conversations, and add opt-in history logging for scheduled job runs.
**Audience**: Archon users who use scheduled tasks via `[schedule]` config.
**Status**: To Do

---

## Background

Scheduled tasks broadcast their final result via `_broadcast()` in `job_scheduler.py`. Both `_broadcast()` and `format_event()` ultimately call `render_split_messages` with `md_to_html`, so the rendering pipeline is the same. However, `_broadcast` embeds the header prefix (e.g., `✅ <b>Scheduled: name</b>\n`) directly into each split chunk, while `format_event(Response(...))` uses the `✅ Response:\n` prefix consistent with regular conversation output. Users observed the scheduled task output format not matching the regular conversation response format in Telegram.

Additionally, there is no audit trail for scheduled job runs — no history file is written, making it hard to review what a job produced or debug failures. Users want an opt-in history log (`[schedule] history_enabled`) consistent with the existing `[history]` session logging system.

No intermediate Telegram notifications should be sent during job execution — only the final result.

## Goal

When a scheduled task completes, the Telegram notification uses the exact same `format_event(Response(...))` code path as regular conversations for prompt-step output, guaranteeing correct markdown→HTML conversion. Tool-step output keeps its current `_broadcast` format. If `[schedule] history_enabled = true`, all events during the job run are written continuously to `~/.archon/history/schedule/YYYY-MM-DD_HH-MM-SS_jobname.md` respecting `[history]` suppression settings.

---

## Scope

### In Scope
- New `history_enabled: bool = False` field in `[schedule]` config section
- `_ScheduleJobLogWriter` class — writes job events to a per-run Markdown file
- `_run_prompt` updated to forward events to the log writer (no Telegram delivery of intermediate events)
- `_run_job` updated to: create/finalize log writer; use `format_event(Response(...))` + `🗓 Scheduled: name` header for prompt-step final output
- `gateway.py` passes `notifications` and `history_config` to `JobScheduler`
- Config example and documentation updates

### Out of Scope
- Streaming intermediate events (ThinkingResult, ToolStarted, ToolResult) to Telegram
- Separate notification mode for scheduled tasks (uses user's configured `[notifications] mode`)
- History compaction for schedule logs
- Any changes to tool-step (`_run_tool`) behavior

---

## Acceptance criteria
- [ ] A scheduled prompt-step job produces two Telegram messages: a `🗓 Scheduled: name` header followed by one or more `✅ Response:\n[content]` parts using the same markdown→HTML conversion as a regular conversation response (truncation strategy is always `SplitStrategy`)
- [ ] A scheduled tool-step job still produces `✅ Scheduled: name\n[content]` (unchanged)
- [ ] With `history_enabled = false` (default), no history file is created
- [ ] With `history_enabled = true`, a file `~/.archon/history/schedule/YYYY-MM-DD_HH-MM-SS_jobname.md` is written with all events
- [ ] History file respects `[history] suppressed_tool_results` and `[history] suppressed_events`
- [ ] History file includes a `✅ Completed` or `❌ Failed` footer with duration
- [ ] No intermediate events are sent to Telegram during job execution
- [ ] All existing scheduled task tests pass
- [ ] Test coverage ≥ 85%

---

## What does NOT change
- `_run_tool` behavior (subprocess execution, stdout capture, `_broadcast` format for tool steps)
- `_broadcast()` method — still used for tool-step final output and all error broadcasts
- `[history] enabled` flag — schedule logging is independent and controlled by `[schedule] history_enabled`
- Intermediate event delivery to Telegram remains suppressed (no change from current behavior)
- All existing `JobScheduler` constructor calls without `notifications`/`history_config` remain valid (new params are optional with `None` defaults)

---

## Known limitations / accepted trade-offs
- History logging for tool steps only captures the final stdout string (logged as if it were a Response), not subprocess events — tool steps don't produce SDK events
- The `🗓 Scheduled: name` header and `✅ Response:` body are sent as two separate Telegram messages for prompt steps; minor extra message count is acceptable for clarity
- Schedule history files are never compacted or cleaned up automatically (same as agent log files)
- Both `send_message` calls for the prompt-step path MUST use `parse_mode='HTML'` — `format_event` returns HTML strings and omitting this causes all HTML tags to render as literal text
- Scheduled task Telegram output always uses `SplitStrategy` for message splitting, regardless of `[output] truncation_strategy` in `config.toml`. Configurable truncation strategy for scheduled tasks is out of scope.

---

## Architecture

### New class: `_ScheduleJobLogWriter` (in `archon/ai/job_scheduler.py`)
Private helper class, follows the same pattern as `AgentLogWriter` in `archon/ai/agent_logger.py`.

```python
class _ScheduleJobLogWriter:
    def __init__(
        self,
        path: Path,
        job_name: str,
        started_at: datetime,
        prompt: str = "",
        suppressed_tools: frozenset[str] | None = None,
        suppressed_events: frozenset[str] | None = None,
    ) -> None: ...

    async def record_event(self, event: Event) -> None: ...
    async def finalize(self, error: str | None = None) -> None: ...
    def _sync_append(self, text: str) -> None: ...
```

Uses `EventRenderer(suppressed_tools, suppressed_events)` from `archon/ai/event_renderer.py`.

### Config change: `ScheduleConfig.history_enabled`
New field in `archon/config/loader.py`:
```python
@dataclass
class ScheduleConfig:
    enabled: bool = True
    jobs_dir: str = "schedules"
    history_enabled: bool = False   # new
    jobs: list[ScheduledJobConfig] = field(default_factory=list)
```

### `JobScheduler.__init__` — two new optional params
```python
def __init__(
    self, config, bot, allowed_user_ids, model=None,
    jobs_dir_base=None, cwd=None,
    notifications: NotificationsConfig | None = None,   # new
    history_config: HistoryConfig | None = None,        # new
) -> None
```

`history_enabled` gate: `self._config.history_enabled` (from `ScheduleConfig`).
History path: `Path(history_config.directory).expanduser() / "schedule" / f"{date}_{time}_{safe_name}.md"`.
Name sanitisation: reuse `sanitize_name()` from `archon/ai/agent_logger.py`.

Note: `_sanitize_name` must be renamed to `sanitize_name` (remove the leading underscore) in `archon/ai/agent_logger.py` to make it a public utility function. Update Task 2.1 description and architecture to use `sanitize_name` (public).

### `_run_prompt` signature change
```python
async def _run_prompt(
    self,
    prompt_text: str,
    timeout: float,
    log_writer: "_ScheduleJobLogWriter | None" = None,
) -> str
```

### `_run_job` orchestration changes
- Creates `_ScheduleJobLogWriter` when `self._config.history_enabled and self._history_config is not None`
- Tracks `last_step_was_prompt: bool`
- For prompt steps: calls `_run_prompt(..., log_writer=log_writer)`
- On success, if `last_step_was_prompt`: sends `🗓 Scheduled: name` header + `format_event(Response(content=last_output), ...)` parts
- On success, if not `last_step_was_prompt` (tool): calls `_broadcast(job.name, last_output, error=False)` (unchanged)
- In `finally`: calls `await log_writer.finalize(error=error_msg)` when writer exists

Note: `format_event` must be extracted from `handler.py` to a new utility module `archon/chat/telegram_formatter.py` to avoid a layer violation. Both `handler.py` and `job_scheduler.py` will import from this utility module.

To reduce duplication, `_run_job` uses a private helper `async def _send_parts_to_all(self, header: str, parts: list[str], job_name: str) -> None` that handles the user iteration and exception handling. The `job_name` parameter is used in warning log messages for diagnostics.

Note on `notifications=None`: in production, `gateway.py` passes `notifications=cfg.notifications` which is a `NotificationsConfig`. In tests, callers may pass `None`, causing `format_event` to fall back to debug mode internally. This is safe for `Response` events because their rendering does not branch on notification mode — the output is identical regardless of mode.

### Gateway wiring (`archon/gateway/gateway.py`)
```python
job_scheduler = JobScheduler(
    ...,
    notifications=cfg.notifications,
    history_config=cfg.history,
)
```

### New config keys

| Key | Type | Default | Section |
|-----|------|---------|---------|
| `history_enabled` | `bool` | `false` | `[schedule]` |

---

## Tests

- **test_schedule_config_history_enabled_defaults_to_false** (unit): `ScheduleConfig()` has `history_enabled=False`
- **test_schedule_config_history_enabled_parsed_true** (unit): parses `history_enabled = true` from TOML
- **test_log_writer_creates_file_with_header** (unit): file created at given path with `# Scheduled:` header
- **test_log_writer_creates_parent_directory** (unit): `path.parent.mkdir(parents=True, exist_ok=True)` called
- **test_log_writer_record_event_appends_rendered_text** (unit): event rendered and written to file
- **test_log_writer_record_event_skips_empty_render** (unit): no write when renderer returns `""`
- **test_log_writer_finalize_success_writes_completed_footer** (unit): `✅ Completed` + duration in footer
- **test_log_writer_finalize_error_writes_failed_footer** (unit): `❌ Failed` + error message in footer
- **test_log_writer_suppressed_events_not_written** (unit): event suppressed by `suppressed_events` config is skipped
- **test_scheduler_accepts_notifications_and_history_config** (unit): constructor accepts new params
- **test_scheduler_new_params_default_to_none** (unit): existing call sites without new params still work
- **test_run_prompt_passes_events_to_log_writer** (unit): each event from session forwarded to `log_writer.record_event`
- **test_run_prompt_none_log_writer_still_collects_response** (unit): `log_writer=None` works (backward compat)
- **test_run_job_creates_log_writer_when_history_enabled** (unit): writer created with correct path when `history_enabled=True`
- **test_run_job_no_log_writer_when_history_disabled** (unit): no file created when `history_enabled=False` (default)
- **test_run_job_finalizes_log_writer_on_success** (unit): `finalize(error=None)` called after success
- **test_run_job_finalizes_log_writer_on_error** (unit): `finalize(error=str(exc))` called even on exception
- **test_run_job_prompt_step_sends_header_plus_response_format** (unit): two `send_message` calls — `🗓 Scheduled:` header + `✅ Response:\n` body
- **test_run_job_tool_step_keeps_broadcast_format** (unit): tool-step success still uses `_broadcast` format (`✅ Scheduled: name\n...`)
- **test_run_job_error_always_uses_broadcast_format** (unit): error broadcast format unchanged
- **test_run_job_history_file_path_uses_sanitized_name** (unit): job name with special chars sanitized in filename
- **test_gateway_passes_notifications_to_scheduler** (integration): scheduler instantiated with `notifications=cfg.notifications` and `history_config=cfg.history`
- **test_log_writer_collision_creates_numbered_suffix** (unit): if path already exists, a new path with `_2` suffix is used
- **test_log_writer_suppressed_tool_result_is_summarized** (unit): a `ToolResult` with a tool name in `suppressed_tools` is rendered as a summary (not full content) in the history file
- **test_run_job_finalize_exception_does_not_mask_job_error** (unit): when `finalize()` raises an exception, the `_broadcast` error call still happens and `status.is_running` is set to `False`
- **test_run_job_prompt_step_notifies_all_users** (unit): with `allowed_user_ids=[10, 20]`, both users receive the header and response parts
- **test_run_job_prompt_step_continues_after_send_failure** (unit): if sending to user 10 fails, user 20 still receives the message
- **test_run_prompt_log_writer_receives_all_event_types** (unit): ThinkingResult, ToolStarted, ToolResult, and Response events from session are all forwarded to log writer
- **test_run_job_prompt_step_no_intermediate_telegram_messages** (unit): when session yields ThinkingResult + ToolStarted + ToolResult + Response, exactly 2 `send_message` calls are made (header + response), not more
- **test_run_job_prompt_step_output_matches_format_event** (unit): for a given response string, the content part sent to Telegram equals `format_event(Response(content=text), SplitStrategy(), _TELEGRAM_MAX_LEN, None)` — `None` is used as the notifications baseline because `Response` rendering is mode-independent, so this comparison is valid regardless of the scheduler's `notifications` value

---

## Documentation update
- [ ] `examples/config.toml.example`, section: `[schedule]`, path: `examples/config.toml.example`
- [ ] `CLAUDE.md`, section: Configuration `[schedule]`, path: `CLAUDE.md`
- [ ] `Documentation/UserManual/schedule_guide.md`, section: Configuration reference, path: `Documentation/UserManual/schedule_guide.md`

---

## Task breakdown

### Phase 0 — Prerequisite Refactor
> **Releasable**: after Task 0 — `format_event` is in a shared utility module; no functional change.

#### Task 0 — Extract `format_event` to `archon/chat/telegram_formatter.py`
- [x] **File**: `archon/chat/telegram_formatter.py` (new), `archon/chat/handler.py`
- **Depends on**: nothing
- **Description**:
  - Move the `format_event` function from `archon/chat/handler.py` to the new module `archon/chat/telegram_formatter.py`
  - Update `archon/chat/handler.py` to re-export `format_event` via `from archon.chat.telegram_formatter import format_event` so that all existing importers (`archon/chat/voice.py`, test files) continue to work without changes
  - Verify all existing tests pass after the move — no functional change
  - Also in this task: rename `_sanitize_name` to `sanitize_name` (remove leading underscore) in `archon/ai/agent_logger.py` to make it a public utility. Update any internal callers (e.g., `AgentLogger._agent_path`) to use the new name. Also update `tests/ai/test_agent_logger.py` to import `sanitize_name` instead of `_sanitize_name` and update all test call sites.
  - Note: `format_event` is imported in tests across multiple directories (`tests/chat/`, `tests/gateway/`, `tests/ai/`). Run the full test suite to verify no import breakage.
- **Releasable**: `format_event` is importable from `archon/chat/telegram_formatter`; `handler.py` re-exports it via import
- **Tests (TDD)** — `tests/chat/`:
  - Integration: `test_handler_still_works_after_format_event_extraction` — end-to-end message formatting still produces the same output after the move
  - Unit: `test_sanitize_name_is_importable_as_public` — `from archon.ai.agent_logger import sanitize_name` works without error
  - Checkpoint: `uv run pytest tests/ -v`

---

### Phase 1 — Config: `ScheduleConfig.history_enabled`
> **Releasable**: after Task 1.1 — the new config key is parseable; all other phases depend on this.

#### Task 1.1 — Add `history_enabled` field to `ScheduleConfig`
- [x] **File**: `archon/config/loader.py`
- **Depends on**: nothing
- **Description**:
  - Add `history_enabled: bool = False` field to `ScheduleConfig` dataclass (after `jobs_dir`, before `jobs`)
  - In the config parser (around line 671), parse `raw_schedule.get("history_enabled", ScheduleConfig.history_enabled)` as `bool`
  - Add to the `ScheduleConfig(...)` constructor call in `load_config()`
- **Releasable**: `ScheduleConfig.history_enabled` is a valid, parseable field
- **Tests (TDD)** — `tests/config/test_config_loader.py` (or `tests/schedule/test_job_scheduler.py`):
  - [x] Unit: `test_schedule_config_history_enabled_defaults_to_false` — `ScheduleConfig()` default is `False`
  - [x] Unit: `test_schedule_config_history_enabled_parsed_true` — TOML with `history_enabled = true` sets field to `True`
  - [x] Unit: `test_schedule_config_history_enabled_absent_defaults_false` — missing key defaults to `False`
  - Checkpoint: `uv run pytest tests/config/ -k "schedule" -v`

---

### Phase 2 — `_ScheduleJobLogWriter` class
> **Releasable**: after Task 2.1 — the writer can be instantiated and used independently.

#### Task 2.1 — `_ScheduleJobLogWriter` — init, record_event, finalize
- [x] **File**: `archon/ai/job_scheduler.py`
- **Depends on**: nothing (no config dependency — receives suppression config at construction)
- **Description**:
  - Add private class `_ScheduleJobLogWriter` before `JobScheduler` class definition
  - `__init__(self, path: Path, job_name: str, started_at: datetime, prompt: str = "", suppressed_tools: frozenset[str] | None = None, suppressed_events: frozenset[str] | None = None) -> None`
    - Uses `from archon.ai.event_renderer import EventRenderer` (local import, avoids circular dep)
    - Calls `path.parent.mkdir(parents=True, exist_ok=True)` synchronously (cold path, one-time)
    - Writes header: `# Scheduled: {job_name} · {date_str}\n**Started:** {ts}\n\n---\n`
    - Appends prompt section if `prompt` is non-empty: `\n## 📝 Prompt · {ts}\n\n{prompt}\n\n---\n`
    - Uses `path.write_text(header, encoding="utf-8")` — creates file fresh
  - `async def record_event(self, event: Event) -> None`
    - Calls `self._renderer.render(event)` → if non-empty, calls `await asyncio.to_thread(self._sync_append, text)`
    - Note: `record_event` is always `await`ed inside a sequential `async for` loop in a single coroutine, so calls from that coroutine cannot overlap — the `await` serializes the thread dispatch, not `asyncio.to_thread` itself. Two concurrent jobs write to different files (different paths), so cross-job interference is not a concern.
  - `async def finalize(self, error: str | None = None) -> None`
    - Computes duration from `datetime.now(timezone.utc) - self._started_at`
    - If `error`: writes `\n## ❌ Failed · {ts}\n\n{error}\n\n**Duration:** {h}:{m:02d}:{s:02d}\n\n---\n`
    - Else: writes `\n## ✅ Completed · {ts}\n\n**Duration:** {h}:{m:02d}:{s:02d}\n\n---\n`
  - `def _sync_append(self, text: str) -> None` — opens file in append mode, writes text
  - Collision handling: if `path` already exists (same job fires twice in the same second), append a counter suffix: `path.stem + '_2'`, `path.stem + '_3'`, etc., using `open(path, 'x')` exclusive creation (same pattern as `AgentLogWriter._agent_path`). Update `__init__` to use this pattern.
- **Releasable**: `_ScheduleJobLogWriter` can be instantiated and used to write events to a file
- **Tests (TDD)** — `tests/schedule/test_job_scheduler.py`:
  - Unit: `test_log_writer_creates_file_with_header` — header contains `# Scheduled: name`, date, started timestamp
  - Unit: `test_log_writer_includes_prompt_section_when_given` — prompt section present when `prompt` provided
  - Unit: `test_log_writer_no_prompt_section_when_empty` — no prompt section when `prompt=""`
  - Unit: `test_log_writer_creates_parent_directory` — nonexistent parent dir is created
  - Unit: `test_log_writer_record_event_appends_rendered_text` — `record_event(Response(...))` appends rendered markdown to file
  - Unit: `test_log_writer_record_event_skips_empty_render` — no write when `EventRenderer.render` returns `""`
  - Unit: `test_log_writer_suppressed_event_not_written` — event in `suppressed_events` set produces no write
  - Unit: `test_log_writer_finalize_success_writes_completed_footer` — file ends with `✅ Completed` + duration
  - Unit: `test_log_writer_finalize_error_writes_failed_footer` — file ends with `❌ Failed` + error text
  - Unit: `test_log_writer_collision_creates_numbered_suffix` — if path already exists, a new path with `_2` suffix is used
  - Unit: `test_log_writer_suppressed_tool_result_is_summarized` — a `ToolResult` with a tool name in `suppressed_tools` is rendered as a summary (not full content) in the history file
  - Checkpoint: `uv run pytest tests/schedule/test_job_scheduler.py -k "log_writer" -v`

---

### Phase 3 — Wire history logging into `JobScheduler`
> **Releasable**: after Task 3.3 — schedule job runs write history files when enabled.

#### Task 3.1 — Add `notifications` and `history_config` params to `JobScheduler.__init__`
- [x] **File**: `archon/ai/job_scheduler.py`
- **Depends on**: Task 1.1 (needs `HistoryConfig`, `NotificationsConfig` in imports)
- **Description**:
  - Add to `from archon.config.loader import ...` the types `HistoryConfig` and `NotificationsConfig`
  - Add `notifications: "NotificationsConfig | None" = None` and `history_config: "HistoryConfig | None" = None` to `__init__` signature (after existing params)
  - Store as `self._notifications = notifications` and `self._history_config = history_config`
  - No other changes — existing tests pass without providing these params
- **Releasable**: `JobScheduler` accepts the new params; all existing construction call-sites still work
- **Tests (TDD)** — `tests/schedule/test_job_scheduler.py`:
  - Unit: `test_scheduler_accepts_notifications_and_history_config` — construct with both params, no error
  - Unit: `test_scheduler_new_params_default_to_none` — `_make_scheduler(cfg)` still works (no new params needed)
  - Checkpoint: `uv run pytest tests/schedule/test_job_scheduler.py -k "scheduler_accepts or new_params" -v`

---

#### Task 3.2 — Update `_run_prompt` to accept and use `log_writer`
- [x] **File**: `archon/ai/job_scheduler.py`
- **Depends on**: Task 2.1 (`_ScheduleJobLogWriter` exists)
- **Description**:
  - Add `log_writer: "_ScheduleJobLogWriter | None" = None` to `_run_prompt` signature
  - Inside `_collect()`, after `async for event in session.send(prompt_text):`, add:
    ```python
    if log_writer is not None:
        await log_writer.record_event(event)
    ```
  - `Response` collection logic unchanged — still sets `result = event.content`
  - No Telegram delivery — only history logging
- **Releasable**: `_run_prompt` forwards all SDK events to the log writer when provided
- **Tests (TDD)** — `tests/schedule/test_job_scheduler.py`:
  - [x] Unit: `test_run_prompt_passes_events_to_log_writer` — mock log writer's `record_event` receives all events yielded by the session
  - [x] Unit: `test_run_prompt_none_log_writer_still_works` — `log_writer=None` (default) still collects response correctly
  - [x] Unit: `test_run_prompt_log_writer_receives_all_event_types` — ThinkingResult, ToolStarted, ToolResult, Response all forwarded
  - Checkpoint: `uv run pytest tests/schedule/test_job_scheduler.py -k "run_prompt" -v`

---

#### Task 3.3 — Update `_run_job` to create, use, and finalize the log writer
- [x] **File**: `archon/ai/job_scheduler.py`
- **Depends on**: Task 3.1, Task 3.2, Task 2.1
- **Description**:
  - At the top of `_run_job` (after the `status.is_running = True` block), add log writer creation:
    ```python
    log_writer: _ScheduleJobLogWriter | None = None
    if self._config.history_enabled and self._history_config is not None:
        from archon.ai.agent_logger import sanitize_name  # local import
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
    ```
  - Add `error_msg: str | None = None` before the try block
  - In the pipeline loop, track `last_step_was_prompt: bool = False`; set `True` for prompt steps, `False` for tool steps
  - Pass `log_writer=log_writer` to `_run_prompt(...)` calls
  - Replace current final `await self._broadcast(job.name, last_output, error=False)` with:
    ```python
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
    ```
  - In `except Exception as exc:` block, set `error_msg = str(exc)` before calling `_broadcast`
  - In `finally:` block, add: `if log_writer is not None: await log_writer.finalize(error=error_msg)`. Log writer errors in `finalize()` must be caught and logged (`logger.warning`), not propagated. Use try/except around `await log_writer.finalize(...)` in the `finally` block.
  - Add a private `async def _send_parts_to_all(self, header: str, parts: list[str], job_name: str) -> None` helper method that iterates `self._allowed_user_ids`, sends the header and each non-empty part via `self._bot.send_message(..., parse_mode="HTML")`, and logs warnings on failure using `job_name` for context. This centralizes the user-iteration and exception-handling pattern.
- **Releasable**: scheduled jobs write history files and send properly formatted Telegram messages
- **Tests (TDD)** — `tests/schedule/test_job_scheduler.py`:
  - [x] Unit: `test_run_job_creates_log_writer_when_history_enabled` — `_ScheduleJobLogWriter` instantiated; file created in correct path
  - [x] Unit: `test_run_job_no_log_writer_when_history_disabled` — no file created with default config
  - [x] Unit: `test_run_job_no_log_writer_when_history_config_is_none` — no file when `history_config=None`
  - [x] Unit: `test_run_job_finalizes_log_writer_on_success` — `finalize(error=None)` called
  - [x] Unit: `test_run_job_finalizes_log_writer_on_error` — `finalize(error="...")` called even when job raises
  - [x] Unit: `test_run_job_finalize_exception_does_not_mask_job_error` — when `finalize()` raises an exception, the `_broadcast` error call still happens and `status.is_running` is set to `False`
  - [x] Unit: `test_run_job_prompt_step_sends_header_plus_response_format` — two send_message calls: `🗓 Scheduled: name` header then `✅ Response:\n[content]`
  - [x] Unit: `test_run_job_tool_step_uses_broadcast_format` — tool output uses existing `_broadcast` (single `✅ Scheduled: name\n...` message)
  - [x] Unit: `test_run_job_error_uses_broadcast_format` — error always uses `_broadcast` (unchanged)
  - [x] Unit: `test_run_job_history_file_path_uses_sanitized_name` — special chars in job name sanitized in filename
  - [x] Unit: `test_run_job_prompt_step_notifies_all_users` — with `allowed_user_ids=[10, 20]`, both users receive the header and response parts
  - [x] Unit: `test_run_job_prompt_step_continues_after_send_failure` — if sending to user 10 fails, user 20 still receives the message
  - [x] Unit: `test_run_job_send_parts_sends_to_all_users_even_if_one_fails` — `_send_parts_to_all` iterates all users even when one raises
  - [x] Unit: `test_run_job_prompt_step_no_intermediate_telegram_messages` — when session yields ThinkingResult + ToolStarted + ToolResult + Response, exactly 2 `send_message` calls are made (header + response), not more
  - [x] Unit: `test_run_job_prompt_step_output_matches_format_event` — for a given response string, the content part sent to Telegram equals `format_event(Response(content=text), SplitStrategy(), _TELEGRAM_MAX_LEN, None)`; `None` is intentional because `Response` rendering is mode-independent
  - Checkpoint: `uv run pytest tests/schedule/test_job_scheduler.py -k "run_job" -v`

---

### Phase 4 — Gateway wiring
> **Releasable**: after Task 4.1 — production Archon passes the correct config to `JobScheduler`.

#### Task 4.1 — Pass `notifications` and `history_config` to `JobScheduler` in gateway
- [x] **File**: `archon/gateway/gateway.py`
- **Depends on**: Task 3.1
- **Description**:
  - In `Gateway.start()` (around line 704), update `JobScheduler(...)` call to add:
    ```python
    notifications=cfg.notifications,
    history_config=cfg.history,
    ```
  - No other changes to gateway
- **Releasable**: production scheduler receives notification mode + history config at startup
- **Tests (TDD)** — `tests/gateway/test_gateway.py` (or integration test):
  - Integration: `test_gateway_passes_notifications_to_scheduler` — gateway instantiates `JobScheduler` with `notifications` set to `cfg.notifications`
  - Integration: `test_gateway_passes_history_config_to_scheduler` — `history_config` set to `cfg.history`
  - Checkpoint: `uv run pytest tests/gateway/ -k "scheduler" -v`

---

### Phase 5 — Documentation
> **Releasable**: after Task 5.1 — users can discover the new config key.

#### Task 5.1 — Document `history_enabled` in config reference and user manual
- [x] **File**: `examples/config.toml.example`
- [x] **File**: `CLAUDE.md`
- [x] **File**: `Documentation/UserManual/schedule_guide.md`
- **Depends on**: Task 1.1
- **Description**:
  - `examples/config.toml.example`: add `history_enabled = false  # write job run logs to ~/.archon/history/schedule/ (default: false)` to `[schedule]` section
  - `CLAUDE.md`: add `history_enabled` to the `[schedule]` config reference table
  - `Documentation/UserManual/schedule_guide.md`: add a "Job History Logs" section explaining the `history_enabled` option, log file path format, and which `[history]` settings apply
- **Releasable**: users can find and understand the new config option
- **Tests (TDD)**: N/A (documentation only)
- Checkpoint: `grep -n "history_enabled" examples/config.toml.example CLAUDE.md Documentation/UserManual/schedule_guide.md`
