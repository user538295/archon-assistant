## Archon Control Plane

You have MCP tools for managing Archon. NEVER use shell commands
(launchctl, systemctl, kill, pkill, killall) to manage Archon,
its services, or background agents. Use these MCP tools instead:

### Service
- archon_status — check daemon health and uptime
- archon_restart — schedule a safe graceful restart
- get_logs — read the last N lines of the Archon daemon log (optional date for rotated logs)
- get_version — return the installed Archon version string
- archon_doctor — run pre-flight health checks and return results as JSON

### Agent Management
- list_running_agents — list running (or all) background agents
- get_agent_status — get status and details of an agent by run_id
- get_agent_by_name — get full details of an agent by name
- cancel_agent — cancel a running background agent
- read_agent_log — read the log file of an agent

### Session Management
- get_session_status — get active session state for a user
- get_context_stats — get token usage and cost stats for a session

### Communication
- send_notification — send a Telegram message to a user
- set_notification_mode — set notification verbosity (quiet/normal/verbose/debug)

### Model & Config
- get_model — get the current active model
- set_model — switch to a different model
- list_skills — list available skills
- list_scheduled_tasks — list all scheduled jobs

### Schedule Management
- add_scheduled_task — create a new scheduled job (created disabled, requires /scheduled to enable)
- update_scheduled_task — update cron, prompt, enabled state, or timeout of a job
- remove_scheduled_task — permanently remove a scheduled job

### Config & Job Access
- get_config — read a config.toml value by dot-notation path (e.g. notifications.mode)
- set_config — write a config.toml value by dot-notation path
- get_job_config — read the TOML configuration of a scheduled job by name

### Files
- list_attachments — list stored file attachments with optional date, MIME, and limit filters
- send_file — send a file to a Telegram user (rate-limited, max 50 MB)

### Search
- search_status — check Search service status, PID, and indexed collection counts
- search_start — start the Search service
- search_stop — stop the Search service
- search_ingest — ingest a directory into a Search collection
- search_sync — reconcile all configured Search collections with LanceDB
- search_collection_list — list all Search collections with path, doc/chunk counts, and sync status
- search_collection_add — add a filesystem path as a Search collection and immediately ingest it
- search_collection_remove — remove a Search collection (drops table, removes from config and manifest)
- search_collection_info — get detailed metadata for a specific Search collection
- search_collection_reindex — force full re-ingest of a collection, bypassing change thresholds

### Voice
- voice_status — check voice feature status (enabled flag, whisper/ffmpeg/edge-tts availability)
- voice_enable — enable voice feature in config.toml
- voice_disable — disable voice feature in config.toml

## File size limits

Before reading any file, check its size: `wc -c <file>` or `stat <file>`.
Never use the Read tool on files larger than 500 KB — it will crash the session.
For large files, use `head -n 100`, `tail`, or `grep` to extract only what you need.
