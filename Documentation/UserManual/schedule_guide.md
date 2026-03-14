---
Purpose: Comprehensive guide to Archon's scheduled jobs feature
Audience: Archon users and AI assistants configuring automated jobs
Status: Active
Last reviewed: 2026-03-15
Next review: 2026-06-15
---

# Scheduled Jobs Guide

## Overview

Archon can run automated jobs on a cron schedule. Each job defines a **pipeline** of sequential steps — shell commands (`_tool`) and Claude prompts (`_prompt`) — that execute one after another. On completion or failure, Archon broadcasts a Telegram notification to **all** users listed in `allowed_user_ids`.

The scheduler ticks every **60 seconds**. At each tick it evaluates every enabled job's cron expression and fires any job whose most recent cron slot falls within the last 60 seconds.

**Two-level enable:** A job only runs when *both* the global `[schedule] enabled` flag and the per-job `enabled` field are `true`.

## Quick Start

1. Enable the scheduler in `~/.archon/config.toml`:

   ```toml
   [schedule]
   enabled = true
   jobs_dir = "schedules"
   ```

2. Create a job file in the `schedules/` directory (relative to `config.toml`):

   ```toml
   # schedules/hello.toml
   cron = "*/5 * * * *"
   enabled = true

   [pipeline]
   greet_tool = "echo Hello from Archon"
   ```

3. Send `/scheduled` in Telegram to verify the job appears.

4. Wait for the next cron slot — you'll receive a Telegram notification with the output.

## Job File Reference

Each `.toml` file in the `schedules/` directory defines one job. The filename stem (without `.toml`) becomes the job name used in `/scheduled` output and notifications.

### Top-level fields

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `cron` | string | **yes** | — | Standard 5-field cron expression (`minute hour day-of-month month day-of-week`) |
| `timeout_seconds` | number | no | `60` | Per-step timeout in seconds. Both tool and prompt steps are killed if they exceed this. |
| `enabled` | boolean | no | `true` | Set to `false` to disable without deleting the file. |
| `timezone` | string | no | system local | IANA timezone name (e.g. `"Europe/Berlin"`). The cron expression is evaluated in this timezone. |

### Pipeline section

The pipeline is a **flat TOML table** (not an array of tables). Each key–value pair defines one step:

```toml
[pipeline]
step_one_tool = "echo hello"
step_two_prompt = "Summarize: {step_one_tool}"
```

**Key naming rules:**

- Keys ending in `_tool` — the value is a shell command run via `asyncio.create_subprocess_exec`. stdout is captured as the step's output.
- Keys ending in `_prompt` — the value is a prompt sent to an isolated `ClaudeSession`. The final response text is the step's output.
- Keys with **any other suffix** produce a validation error and the job is auto-disabled.

Steps execute **sequentially** in the order they appear in the TOML file. Each step's output is available to subsequent steps via `{step_name}` references.

### Step references

Step values can reference the output of **earlier** steps using `{step_name}` syntax, where `step_name` is the full key name (including suffix):

```toml
[pipeline]
check_tool = "scripts/health_check.sh"
summarize_prompt = "Summarize in one line: {check_tool}"
report_prompt = "Format as HTML: {summarize_prompt}"
```

**Validation rules:**

- Only **backward references** are allowed — a step can only reference steps defined above it.
- **Forward references** (referencing a step defined later) produce a validation error.
- **Self-references** (a step referencing itself) produce a validation error.
- **Unknown references** (referencing a name that doesn't match any step) are left as-is in the string (not substituted).

**Escaping literal braces:** Prefix with `$` to prevent substitution. `${literal}` is passed through as-is and is not validated as a step reference. This is useful when your shell command or prompt contains literal `{word}` patterns that should not be treated as references.

## Examples

### Minimal: echo test

From `schedules/echo-test.toml`:

```toml
cron = "* * * * *"              # every minute
timezone = "Europe/Berlin"
timeout_seconds = 10
enabled = false

[pipeline]
test_echo_tool = "echo hello from scheduler"
```

### Two-step: health summary

From `schedules/health-summary.toml`:

```toml
cron = "0 6 * * *"              # daily at 06:00 Europe/Berlin time
timezone = "Europe/Berlin"
timeout_seconds = 30
enabled = false

[pipeline]
health_check_tool = "scripts/health_check.sh"
summarize_prompt = "Summarize in one line with useful data: {health_check_tool}"
```

### Multi-step chain

A synthetic example showing three chained steps:

```toml
cron = "0 9 * * 1-5"
timezone = "America/New_York"
timeout_seconds = 120

[pipeline]
disk_tool = "df -h / | tail -1"
memory_tool = "free -h | grep Mem"
report_prompt = "System report:\nDisk: {disk_tool}\nMemory: {memory_tool}\nGive a one-paragraph health assessment."
```

### Daily git summary

A practical real-world example:

```toml
cron = "0 8 * * 1-5"
timezone = "Europe/Berlin"
timeout_seconds = 60

[pipeline]
commits_tool = "git -C ~/projects/myapp log --oneline --since='24 hours ago'"
summary_prompt = "Summarize these recent commits in 2-3 bullet points:\n{commits_tool}"
```

## Creating, Updating, and Removing Jobs

**Create:** Add a `.toml` file to the `schedules/` directory. Use kebab-case naming (e.g. `health-check.toml`, `nightly-backup.toml`). Start with `enabled = false` for staging — verify via `/scheduled`, then set `enabled = true`.

**Update:** Edit the file on disk. The next `/scheduled` command triggers a hot-reload — no Archon restart needed.

**Remove:** Delete the file. Send `/scheduled` to confirm it's gone.

**Enable/disable:** Both the global `[schedule] enabled` flag in `config.toml` and the per-job `enabled` field must be `true` for a job to run.

## Validation and Error Handling

### Config validation errors

These are detected at load time and prevent the job from ever running:

| Error | Cause |
|---|---|
| Bad suffix | A pipeline key doesn't end in `_tool` or `_prompt` |
| Forward reference | A step references a step defined after it |
| Empty pipeline | The `[pipeline]` section exists but has no keys |
| Missing `cron` | The required `cron` field is absent |

### Auto-disable

When a job with a validation error is due to fire:

1. Archon sets `enabled = false` in the job's TOML file on disk (atomic write).
2. A `⚠️` notification is sent to all allowed users with the error message.
3. The job will not fire again until the user fixes the config and manually sets `enabled = true`.

### Recovery

1. Fix the TOML file (correct the step name, suffix, or reference).
2. Set `enabled = true` in the file.
3. Send `/scheduled` to verify the job loads without errors.

### Runtime errors

| Error | Cause | Notification |
|---|---|---|
| Non-zero exit | Tool step returns exit code ≠ 0 | `❌ Scheduled: <name>` + stderr |
| Timeout | Step exceeds `timeout_seconds` | `❌ Scheduled: <name>` + timeout message |
| Prompt failure | ClaudeSession error during prompt step | `❌ Scheduled: <name>` + exception message |

## Monitoring with `/scheduled`

Send `/scheduled` in Telegram to see all configured jobs. Each `/scheduled` call **reloads job files from disk** (hot-reload), so edits take effect immediately.

### Status icons

| Icon | Meaning |
|---|---|
| ⏳ | Waiting — job has never run yet |
| 🔄 | Currently running |
| ✅ | Last run succeeded (shows timestamp) |
| ⚠️ | Invalid config — validation error (shows error message) |

### Output format

Each job shows:

- **Name** and state icon with run count
- Last result preview (first 120 characters) or last error
- Next scheduled run time (or "disabled" / "fix config to enable")

## Notifications

All notifications are sent to **every user** in `allowed_user_ids` — there is no per-job targeting.

### Formats

| Event | Format |
|---|---|
| Success | `✅ Scheduled: <job-name>` + final step output |
| Failure | `❌ Scheduled: <job-name>` + error message |
| Validation error | `⚠️ Scheduled: <job-name>` + config error + recovery instructions |

Long output is split at **4000 characters** using the `SplitStrategy` truncation, with messages labeled `[1/N]`, `[2/N]`, etc.

## Timezone Handling

Set the `timezone` field to an [IANA timezone name](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) (e.g. `"Europe/Berlin"`, `"America/New_York"`, `"Asia/Tokyo"`).

When set, the cron expression is evaluated in that timezone. For example, `cron = "0 9 * * *"` with `timezone = "Europe/Berlin"` fires at 9:00 AM Berlin time regardless of the server's system timezone.

When omitted, the system's local timezone is used.

**Invalid timezone names** (e.g. a typo like `"Eurpoe/Berlin"`) cause the job to never fire — a warning is logged but the job is not auto-disabled. Check the log if a job never triggers.

## Duplicate-Fire Guard

If a job is still running when its next cron slot fires, the execution is **skipped**. This prevents overlapping runs of slow jobs. A warning is logged: `"Job '<name>' already running — skipping duplicate fire"`.

## Prompt Step Model

Prompt steps run in a fresh, isolated `ClaudeSession` — completely separate from the main conversation. The model used is the **global default** from `[models] default` in `config.toml`. This cannot be overridden per-job.

## Security Notes

- **Execution privileges:** `_tool` steps run with Archon's process privileges. Any command the Archon user can run, a scheduled tool step can run.
- **Directory permissions:** Archon logs a warning at startup if the `jobs_dir` is world-writable, since anyone could inject tool commands.
- **stdin:** Tool steps receive empty stdin (stdin is closed). Commands requiring interactive input will fail.
- **Working directory:** Tool steps run with CWD set to the `jobs_dir_base` directory (typically `~/.archon`), **not** the session `working_directory`. Use absolute paths in commands to avoid confusion.

## Cron Cheat Sheet

| Expression | Schedule |
|---|---|
| `* * * * *` | Every minute |
| `*/5 * * * *` | Every 5 minutes |
| `0 * * * *` | Every hour (at :00) |
| `0 8 * * *` | Daily at 08:00 |
| `0 8 * * 1-5` | Weekdays at 08:00 |
| `0 8 * * 1` | Every Monday at 08:00 |
| `0 9,17 * * 1-5` | 09:00 and 17:00, Monday–Friday |
| `0 0 1 * *` | First day of every month at midnight |
| `30 2 * * 0` | Every Sunday at 02:30 |

Format: `minute hour day-of-month month day-of-week`

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Job doesn't appear in `/scheduled` | No `.toml` files in `schedules/` | Create a `.toml` file in the directory configured by `[schedule] jobs_dir` |
| Job shows "disabled" | `enabled = false` in job file or `[schedule] enabled = false` globally | Set both to `true` |
| Job never fires | Invalid timezone name, or cron expression doesn't match current time | Check spelling of timezone; test cron expression |
| Job shows "⚠️ invalid config" | Pipeline validation error (bad suffix, forward ref, empty pipeline) | Read the error message, fix the TOML, set `enabled = true` |
| Job was auto-disabled | Validation error triggered auto-disable | Fix the config error, set `enabled = true`, send `/scheduled` |
| Timeout error | Step took longer than `timeout_seconds` | Increase `timeout_seconds` or optimize the command/prompt |
| "Permission denied" in tool step | Script not executable or path not found | `chmod +x script.sh`; use absolute paths |
| Duplicate fire skipped | Previous run still in progress | Increase `timeout_seconds` or simplify the pipeline |
| Wrong time for cron trigger | Timezone mismatch | Set `timezone` explicitly to your IANA timezone |
