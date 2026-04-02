# Security Review: Consolidated Review

**Date:** 2026-03-30
**Reviewer:** Codex
**Scope:** Consolidated security review merging:
- the project-wide review of the current workspace as of 2026-03-30
- the commit-range review `fec0dfc46974124b7025738ac1cc7e808f06f53e..b68422c9817dbe557aacbad384ca2e113065cb7e`
- the commit-range review `bebfa1068a3ea3a5f8cae8d1c03e12d75c754707..b68422c9817dbe557aacbad384ca2e113065cb7e`

**Method:** Read-only code review. Overlapping findings from the three source reviews were deduplicated and re-validated against the current workspace before consolidation.

## Findings

### 1. High: the main Claude session has unrestricted access to the full Archon administrative toolkit

- The main session is wired to the background-agent MCP server:
  - `archon/ai/session_manager.py:103-107`
  - `archon/ai/claude_session.py:175-185`
- The session runs with `permission_mode="bypassPermissions"`:
  - `archon/ai/claude_session.py:196-205`
- That MCP server exposes the full toolkit without a per-tool allowlist:
  - `archon/ai/archon_mcp_server.py:228-242`
- Privileged handlers exposed through that path include restart, config writes, and file sending:
  - `archon/ai/archon_toolkit.py:949-969`
  - `archon/ai/archon_toolkit.py:1565-1594`
  - `archon/ai/archon_toolkit.py:1626-1715`

**Why this matters**

Any prompt injection that reaches the main model through repository content, attachments, tool output, or retrieved context can drive daemon-level actions. Because the session bypasses the SDK permission prompts, there is no interactive approval layer to stop those actions once the model decides to call a tool.

**Recommended fix**

- Split the toolkit into capability tiers and expose only a minimal allowlisted subset to the main session.
- Put high-risk actions such as `set_config`, `archon_restart`, and `send_file` behind explicit operator-confirmed flows.
- Treat repository content, RAG context, attachment-derived text, and tool output as untrusted input when deciding which tools the main session may call.

### 2. High: background agents can message other whitelisted users and send them files

- Background agents are wired to the router MCP server with an allowlist that includes `get_config`, `send_notification`, and `send_file`:
  - `archon/gateway/gateway.py:649-656`
  - `archon/gateway/gateway.py:690-700`
  - `archon/ai/background_agent_manager.py:342-349`
- `get_config` returns non-redacted config values except for keys matching `(token|password|secret|key)`:
  - `archon/ai/archon_toolkit.py:484-499`
  - `archon/ai/archon_toolkit.py:1524-1563`
- `send_notification` accepts any target user ID:
  - `archon/ai/archon_toolkit.py:1104-1139`
- `send_file` only checks that the target is whitelisted, not that the target is the caller:
  - `archon/ai/archon_toolkit.py:1626-1715`

**Why this matters**

In a multi-user whitelist, a background agent created for user A can read `access.allowed_user_ids`, learn user B's Telegram ID, and then send messages or allowed files to user B. That is a cross-user data leak and a privilege-boundary failure.

**Recommended fix**

- Bind user-targeted toolkit actions to the caller's `user_id` unless an explicitly elevated admin mode is active.
- Remove `get_config` from the background-agent allowlist, or at minimum redact `allowed_user_ids`.
- Separate "message self" and "send file to self" from "message arbitrary user" capabilities.

### 3. High: background agents can read shared history across users

- Background agents are given the router MCP server endpoint:
  - `archon/gateway/gateway.py:690-700`
  - `archon/ai/background_agent_manager.py:342-344`
- The router MCP server exposes `history_list`, `history_read`, and `history_grep` on user-routed endpoints:
  - `archon/ai/archon_router_mcp_server.py:184-185`
  - `archon/ai/archon_router_mcp_server.py:340-352`
  - `archon/ai/archon_router_mcp_server.py:357-418`
- History is written into shared daily files keyed only by date, with multiple users' content co-located in the same file:
  - `archon/ai/history_manager.py:27-52`
  - `archon/ai/history_manager.py:75-91`

**Why this matters**

Any background agent can enumerate and read files under the shared history root. Because conversation history is not partitioned per user, one user's agent can read another user's conversation history in any deployment with more than one whitelisted user.

**Recommended fix**

- Partition history by user ID instead of a single shared daily file.
- Enforce per-user authorization inside `history_list`, `history_read`, and `history_grep`.
- Avoid exposing raw history paths directly to background agents.

### 4. High: the RAG FastMCP HTTP service is unauthenticated

- The RAG service registers its tools on a `FastMCP("archon-rag")` app with no bearer-token or caller-authentication layer:
  - `archon/rag/server.py:26-176`
- The service binds directly on the configured host and port:
  - `archon/rag/server.py:215-216`
  - `archon/config/loader.py:100-103`
- By contrast, Archon's internal MCP servers do enforce bearer tokens:
  - `archon/ai/archon_mcp_server.py:112-118`
  - `archon/ai/archon_mcp_server.py:155-172`
  - `archon/ai/archon_router_mcp_server.py:11-15`
  - `archon/ai/archon_router_mcp_server.py:178-185`
  - `archon/ai/archon_router_mcp_server.py:233-237`
- Archon's documented access boundary is the whitelist-backed Telegram ingress:
  - `Documentation/Architecture/150_security_and_privacy_architecture.md:11-15`
  - `Documentation/Architecture/150_security_and_privacy_architecture.md:19-21`
  - `Documentation/Architecture/150_security_and_privacy_architecture.md:155-168`

**Why this matters**

This creates a separate HTTP entry point that does not enforce either the Telegram whitelist or the bearer-token pattern used by the internal MCP servers. Any local process that can reach the port can search indexed data and invoke mutating RAG operations. If `cfg.rag.host` is changed away from loopback, the same interface becomes remotely reachable.

**Recommended fix**

- Add authentication and authorization to the RAG service.
- Default to loopback-only binding and fail closed when a non-loopback host is configured without an explicit insecure override.
- Consider using a Unix domain socket or another non-public transport if external clients are not required.

### 5. High: RAG ingestion surfaces accept arbitrary filesystem paths with no allowlist or containment checks

- The RAG HTTP tools accept caller-supplied paths and pass them directly into the ingestion pipeline:
  - `archon/rag/server.py:67-99`
- The Archon MCP handlers do the same:
  - `archon/ai/archon_toolkit_rag.py:226-236`
  - `archon/ai/archon_toolkit_rag.py:449-487`
- The pipeline recursively walks the chosen directory and parses readable files:
  - `archon/rag/pipeline.py:118-145`
  - `archon/rag/parser.py:78-82`
- Main sessions run with `permission_mode="bypassPermissions"`:
  - `archon/ai/claude_session.py:196-205`

**Why this matters**

An MCP caller or local HTTP caller can index arbitrary readable paths such as `~/.ssh`, `~/.archon`, repository secrets, or other sensitive local directories and later retrieve their contents through RAG search or document listing. That is a material expansion of the local-data exposure surface.

**Recommended fix**

- Restrict RAG collection roots to an explicit allowlist, for example Archon history and explicitly approved workspace paths.
- Reject out-of-bounds resolved paths after symlink resolution.
- Require an explicit operator-approved config flag or confirmation flow before allowing arbitrary collection roots.

### 6. High: workspace `agents.md` and `REMINDER.md` can exfiltrate arbitrary local files via symlinks

- Workspace `agents.md` is read directly from the workspace path with `read_text()`:
  - `archon/ai/agent_loader.py:24-46`
- Workspace `REMINDER.md` is read directly through `_read_file_safe()`, which also calls `read_text()` with no symlink or containment check:
  - `archon/ai/reminder.py:12-21`
  - `archon/ai/reminder.py:102-122`
- `agents.md` is injected into the main and router sessions:
  - `archon/ai/decomposer.py:165-178`
  - `archon/ai/decomposer.py:236-279`
- `REMINDER.md` is injected into the main session, router prompt, and background agents:
  - `archon/ai/session_manager.py:109-125`
  - `archon/ai/claude_session.py:287-294`
  - `archon/ai/decomposer.py:355-369`
  - `archon/ai/background_agent_manager.py:366-372`

**Why this matters**

A malicious repository can ship `agents.md` or `REMINDER.md` as a symlink to a sensitive local file such as `~/.archon/.env` or `~/.ssh/config`. Archon will read and inject that file automatically, sending its contents into model context without any model action.

**Recommended fix**

- Reject symlinks for workspace policy files before reading them.
- Require these files to resolve under the workspace root after symlink resolution.
- Consider disabling automatic workspace policy injection for untrusted repositories.

### 7. Medium: MCP tool audit logging records raw tool arguments in the daemon log

- The architecture doc says Archon should log metadata, not content:
  - `Documentation/Architecture/150_security_and_privacy_architecture.md:13`
  - `Documentation/Architecture/150_security_and_privacy_architecture.md:63-77`
- Every MCP tool call serializes and logs its arguments before dispatch:
  - `archon/ai/archon_toolkit.py:785-792`

**Why this matters**

Tool arguments can contain user content, filesystem paths, scheduled prompts, or future secret-bearing values. Those values now enter `archon.log`, which conflicts with the documented logging policy.

**Recommended fix**

- Stop logging raw argument payloads.
- Log only tool name, caller, and a redacted or schema-aware summary.
- If detailed audit logs are required, redact per-tool sensitive fields before serialization and keep them out of the main daemon log.

### 8. Medium: `rag_collection_add` persists untrusted paths before ingest succeeds

- `rag_collection_add` appends the caller-supplied path to `config.toml` before attempting ingestion:
  - `archon/ai/archon_toolkit_rag.py:466-487`
- The config mutation is durable because it uses `config_collections_append()`:
  - `archon/config/config_rw.py:71-96`
- Configured collections are retried later during server startup and install-time bootstrap:
  - `archon/rag/server.py:192-211`
  - `archon/rag/install.py:203-212`

**Why this matters**

Even if ingestion fails or is interrupted, the path remains in config and will be retried later. That turns a one-shot malicious or mistaken tool call into a persistent configuration foothold.

**Recommended fix**

- Ingest first, then persist config only after successful completion.
- If config must be updated first, roll it back on ingest failure.
- Log and notify on rollback so the operator can see that a persistent change was prevented.

### 9. Medium: history and log files are created with default filesystem permissions

- History directories and files are created without permission tightening:
  - `archon/ai/history_manager.py:36-39`
  - `archon/ai/history_manager.py:77-88`
- Agent logs are also created without explicit mode setting:
  - `archon/ai/agent_logger.py:103-105`
  - `archon/ai/agent_logger.py:154`
  - `archon/ai/agent_logger.py:160-162`
- Daemon logs are created without explicit `chmod`:
  - `archon/log_setup.py:95-107`
- Installer-created log directories are also left at default permissions:
  - `install.py:582-585`

**Why this matters**

On systems using a permissive umask such as `022`, these files are typically readable by other local users. That exposes prompts, responses, tool output, and operational details.

**Recommended fix**

- Create `~/.archon`, `history`, and `logs` directories with `0700`.
- Create history and log files with `0600`.
- Audit rotation and migration paths so permissions stay tight after rename or copy operations.

### 10. Medium: config backup and lock paths follow symlinks

- Backup restore and backup creation use `shutil.copy2()` with no symlink checks:
  - `archon/config/loader.py:415-438`
- Config lock files are opened with `"w"` before locking:
  - `archon/config/config_rw.py:35-38`
  - `archon/config/config_rw.py:76-79`
  - `archon/config/config_rw.py:102-105`

**Why this matters**

If a symlink can be placed under `~/.archon`, config backup and config-write flows can overwrite or truncate arbitrary writable files owned by the Archon user.

**Recommended fix**

- Refuse to operate on symlinked config, backup, and lock paths.
- Use secure open patterns that do not follow symlinks for lock files.
- Validate that resolved paths remain under the intended config directory before copying or writing.

### 11. Medium: the installer defaults to installing from the current working tree

- CLI help explicitly says omitting `--tag` installs from the current directory:
  - `install.py:1066-1075`
- Default source selection uses `Path.cwd()` unless a tag is provided:
  - `install.py:1086-1087`
- Candidate preparation clones from that local checkout:
  - `install.py:300-317`

**Why this matters**

Running the installer from an attacker-controlled checkout without `--tag` installs and starts that untrusted code. That is a dangerous default for something presented as an installer.

**Recommended fix**

- Make `--tag` or an explicit `--local` flag mandatory.
- Default to a pinned remote release, not the current working directory.
- Make local-install mode visibly unsafe in both help text and runtime prompts.

### 12. Medium: Linux service generation trusts raw `PATH`

- `install.py` injects the current `PATH` directly into the generated systemd unit:
  - `install.py:607-613`
- The Linux platform service path does the same:
  - `archon/platform/linux/service.py:142-144`

**Why this matters**

If service registration runs in a hostile environment, attacker-controlled `PATH` contents can alter the generated unit-file semantics and introduce extra directives.

**Recommended fix**

- Escape or sanitize `PATH` before inserting it into unit files.
- Prefer a fixed minimal service environment over inheriting the caller environment.

### 13. Medium: callback-based background-agent cancellation does not verify ownership

- The callback handler looks up the run globally and cancels it directly:
  - `archon/chat/commands.py:969-991`
- The manager lookup and cancellation APIs are global by `run_id`:
  - `archon/ai/background_agent_manager.py:279-290`

**Why this matters**

If callback data is replayed or manipulated to contain another user's `run_id`, one whitelisted user can cancel another user's background agent.

**Recommended fix**

- Verify `run.user_id == callback.from_user.id` before cancellation.
- Consider including a per-user signed token in callback payloads for destructive actions.

### 14. Medium: voice transcription content is logged in plaintext

- The architecture doc says Archon should log metadata, not content:
  - `Documentation/Architecture/150_security_and_privacy_architecture.md:13`
  - `Documentation/Architecture/150_security_and_privacy_architecture.md:63-77`
- The voice pipeline logs the first 80 characters of each transcription:
  - `archon/chat/voice.py:155-156`

**Why this matters**

Sensitive spoken content can end up in `archon.log`, which conflicts with the documented logging policy.

**Recommended fix**

- Log only transcription length and status, not transcription content.

## Notes

- This consolidated review is based on read-only inspection. I did not run dynamic tests, exploit attempts, or service deployment steps.
- All findings above were re-validated against the current workspace before consolidation.
