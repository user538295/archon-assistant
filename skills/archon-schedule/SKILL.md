---
name: archon-schedule
description: Create, update, remove, and troubleshoot Archon scheduled jobs. Use when the user asks to manage cron-based automation — creating new job TOML files, editing pipelines, enabling/disabling jobs, or diagnosing schedule issues.
---

# Archon Schedule Management

## Purpose

This skill enables you to manage Archon's scheduled jobs — creating, updating, removing, and troubleshooting `.toml` job files that define automated cron-based pipelines.

## Key Paths

- **Jobs directory:** `~/.archon/schedules/` (relative to `config.toml`, configured via `[schedule] jobs_dir`)
- **Config file:** `~/.archon/config.toml` — must have `[schedule] enabled = true`
- **Existing examples:** Check the project's `schedules/` directory for reference files

## Workflow

### Before any action

1. Read `~/.archon/config.toml` to check if `[schedule] enabled = true` and what `jobs_dir` is set to
2. List existing files in the jobs directory to understand current state
3. If creating a new job, confirm the job name with the user

### Creating a job

1. Choose a kebab-case filename (e.g. `daily-report.toml`)
2. Write the TOML file to the jobs directory
3. Start with `enabled = false` so the user can verify via `/scheduled` before activating
4. Tell the user to send `/scheduled` in Telegram to verify, then set `enabled = true` when ready

### Updating a job

1. Read the existing file first
2. Make the requested changes
3. Remind the user that `/scheduled` triggers a hot-reload — no restart needed

### Removing a job

1. Confirm with the user before deleting
2. Delete the file
3. Tell the user to send `/scheduled` to confirm removal

### Enabling/disabling

- **Per-job:** Set `enabled = true/false` in the job's TOML file
- **Global:** Set `[schedule] enabled = true/false` in `config.toml`
- Both must be `true` for a job to run

---

## Job File Format Reference

Each `.toml` file in the jobs directory defines one job. The filename stem (without `.toml`) becomes the job name.

### Top-level fields

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `cron` | string | **yes** | — | Standard 5-field cron expression (`minute hour day-of-month month day-of-week`) |
| `timeout_seconds` | number | no | `60` | Per-step timeout in seconds |
| `enabled` | boolean | no | `true` | Set to `false` to disable without deleting |
| `timezone` | string | no | system local | IANA timezone name (e.g. `"Europe/Berlin"`) |

### Pipeline section

The pipeline is a **flat TOML table** (`[pipeline]`), NOT an array of tables. Each key-value pair is one step:

```toml
[pipeline]
step_one_tool = "echo hello"
step_two_prompt = "Summarize: {step_one_tool}"
```

**Key naming rules:**

- Keys ending in `_tool` — shell command, stdout captured as output
- Keys ending in `_prompt` — Claude prompt, response text is output
- Any other suffix → **validation error**, job auto-disabled

Steps execute sequentially in TOML order.

### Step references

Reference earlier steps with `{step_name}` (full key name including suffix):

```toml
[pipeline]
check_tool = "scripts/health_check.sh"
summarize_prompt = "Summarize: {check_tool}"
```

**Rules:**
- Only **backward references** allowed (steps defined above)
- Forward/self references → validation error
- Unknown references → left as-is (not substituted)
- Escape literal braces with `$`: `${literal}` is not treated as a reference

### Validation errors cause auto-disable

When a job with a validation error is due to fire:
1. `enabled = false` is written to the TOML file on disk
2. A warning notification is sent to all users
3. User must fix the file and set `enabled = true` to re-activate

---

## Examples

### Minimal: echo test

```toml
cron = "* * * * *"
timezone = "Europe/Berlin"
timeout_seconds = 10
enabled = false

[pipeline]
test_echo_tool = "echo hello from scheduler"
```

### Two-step: tool → prompt

```toml
cron = "0 6 * * *"
timezone = "Europe/Berlin"
timeout_seconds = 30
enabled = false

[pipeline]
health_check_tool = "scripts/health_check.sh"
summarize_prompt = "Summarize in one line with useful data: {health_check_tool}"
```

### Multi-step chain

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

```toml
cron = "0 8 * * 1-5"
timezone = "Europe/Berlin"
timeout_seconds = 60

[pipeline]
commits_tool = "git -C ~/projects/myapp log --oneline --since='24 hours ago'"
summary_prompt = "Summarize these recent commits in 2-3 bullet points:\n{commits_tool}"
```

---

## Cron Cheat Sheet

| Expression | Schedule |
|---|---|
| `* * * * *` | Every minute |
| `*/5 * * * *` | Every 5 minutes |
| `0 * * * *` | Every hour |
| `0 8 * * *` | Daily at 08:00 |
| `0 8 * * 1-5` | Weekdays at 08:00 |
| `0 9,17 * * 1-5` | 09:00 and 17:00, weekdays |
| `0 0 1 * *` | First of every month |

Format: `minute hour day-of-month month day-of-week`

---

## Important Details

- **Notifications** go to ALL `allowed_user_ids` — no per-job targeting
- **Long output** is split at 4000 characters
- **Duplicate-fire guard:** if a job is still running when its next slot fires, execution is skipped
- **Prompt model:** uses the global `[models] default` from config — cannot be overridden per-job
- **Tool CWD:** tool steps run with CWD = `~/.archon` (not the session working directory)
- **stdin** is closed — interactive commands will fail
- **Invalid timezone** → job silently never fires (check logs)

## Common Mistakes to Avoid

1. **Using `[[pipeline]]` array syntax** — wrong! Use `[pipeline]` flat table
2. **Using `{input}` placeholder** — wrong! Use `{step_name_tool}` or `{step_name_prompt}` (full key name)
3. **Using `notify_user_id`** — doesn't exist; notifications go to all allowed users
4. **Missing `_tool` or `_prompt` suffix** on pipeline keys → validation error
5. **Forward references** in pipeline → validation error
