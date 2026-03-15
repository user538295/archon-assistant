**Purpose**: Documents the decision to run Archon as a local user-space daemon (launchd on macOS, systemd on Linux) rather than a cloud-hosted service.
**Audience**: All developers, operators
**Status**: Stable
**Last reviewed**: 2026-02-26
**Next review**: 2026-08-26

# 04. Local Daemon Deployment (launchd / systemd)

**Status**: Accepted
**Date**: 2026-02-26
**Deciders**: Archon project team

## Context

Archon bridges Telegram with the Claude Code CLI. The CLI (`claude`) requires local filesystem access, user credentials, and a full interactive environment — it cannot run in a serverless or minimal container context.

The user wants Claude Code accessible from anywhere via Telegram while the machine is running. The solution must:

- Start automatically at login without manual intervention
- Restart automatically after crashes
- Impose no ongoing cloud cost or external infrastructure management
- Keep all conversation data on the local machine

## Decision

Deploy Archon as a user-space daemon using the platform's native service manager:

- **macOS**: a LaunchAgent plist at `~/Library/LaunchAgents/com.archon.assistant.plist` with `KeepAlive=true` and `RunAtLoad=true`. launchd relaunches the process immediately on crash and starts it automatically at login.
- **Linux**: a systemd user unit at `~/.config/systemd/user/archon.service` with `Restart=on-failure` and `Type=simple`. The unit is enabled with `systemctl enable --user archon`.

Both configurations run `uv run python main.py` from the app directory and append stdout and stderr to `~/.archon/logs/archon.log`. Installation and service registration are handled by `install.py`, which detects the OS, writes the appropriate service file from the template in `scripts/`, and loads or enables it immediately.

## Consequences

### Positive

- No cloud cost or external infrastructure to maintain.
- Claude Code CLI runs with full local filesystem access and user permissions — no sandboxing restrictions.
- Automatic restart: `KeepAlive=true` (macOS) and `Restart=on-failure` (Linux) ensure the service recovers from crashes without manual intervention.
- Service starts automatically at user login on both platforms.
- All conversation data stays on the local machine; nothing leaves except via Telegram.

### Negative

- Service is unavailable when the machine is powered off, asleep, or the user is not logged in.
- No high-availability or multi-region redundancy.
- Requires the user's machine to have the `claude` CLI, Python 3.12+, and `uv` installed and available in `PATH` at service start time.
- Debugging requires SSH access to the machine or reading `~/.archon/logs/archon.log` directly.

## Alternatives Considered

- **Cloud deployment (AWS Lambda, Fly.io, etc.)**: Rejected because the Claude Code CLI requires a full interactive environment with local filesystem access. Serverless runtimes cannot host it, and a persistent VM would add ongoing cost and remote credential management complexity.
- **Docker container on the local machine**: Rejected because it still requires the host machine to be running and adds container overhead without solving the availability problem. Packaging the `claude` CLI and its authentication state inside a container is non-trivial and adds maintenance burden.
