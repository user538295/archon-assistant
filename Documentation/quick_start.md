**Purpose**: Gets a developer from zero to a running Archon daemon in under 10 minutes, covering prerequisites, installation, configuration, and first test run.
**Audience**: All developers
**Status**: Stable
**Last reviewed**: 2026-02-26
**Next review**: 2027-02-26

# Quick Start

## Principles

1. **Verify prerequisites before cloning** — missing `uv`, `claude`, or Python 3.12+ causes install failure; confirm them first.
2. **One source of truth for config** — `.env` holds secrets; `config.toml` holds everything else; never mix them.
3. **The installer is the recommended path** — `install.sh` handles cloning, config, service registration, and dependency install in one step.
4. **Manual setup is for development** — use it when you need to run from a local clone or modify source code.
5. **Tests prove the stack works** — run `uv run pytest` before and after any change.

---

## Prerequisites

Install and verify each dependency before proceeding.

| Dependency | Version | Install |
|---|---|---|
| Python | 3.12+ | `uv python install 3.12` |
| [uv](https://docs.astral.sh/uv/) | any | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) | latest | follow official docs; authenticate before use |
| Telegram bot token | — | create via [@BotFather](https://t.me/BotFather) |
| Telegram user ID | — | find yours via [@userinfobot](https://t.me/userinfobot) |

Verify:

```bash
uv --version
python3 --version        # must be 3.12+
claude --version         # must be authenticated and in PATH
```

---

## Option A — One-click installer (recommended)

The installer clones the repo to `~/.archon/app`, prompts for credentials, writes config, installs dependencies, and registers the system service.

```bash
curl -fsSL https://raw.githubusercontent.com/user538295/archon-assistant/main/install.sh | bash
```

Or, if you have already cloned the repo:

```bash
bash install.sh
```

The installer:
1. Verifies `git`, `uv`, Python 3.12+, and `claude` CLI
2. Prompts for your Telegram bot token and your Telegram user ID
3. Writes `~/.archon/.env` (bot token) and `~/.archon/config.toml` (all other settings)
4. Runs `uv sync` to install Python dependencies
5. Registers and starts the daemon — launchd on macOS, systemd on Linux

After install, send a message to your bot on Telegram to confirm it responds.

---

## Option B — Manual setup (for development)

Use this when you are working on the source code directly.

### 1. Clone

```bash
git clone https://github.com/user538295/archon-assistant.git
cd archon-assistant
```

### 2. Install dependencies

```bash
uv sync
```

This reads `pyproject.toml` and installs all runtime and dev dependencies into a local virtual environment managed by `uv`.

### 3. Configure credentials

Create `.env` in the project root (or at `~/.archon/.env`):

```bash
TELEGRAM_BOT_TOKEN=your_bot_token_here
```

### 4. Configure the daemon

Copy the example config and edit it:

```bash
cp examples/config.toml.example config.toml
```

The minimum required changes:

```toml
[access]
# Your Telegram user ID — find it via @userinfobot
allowed_user_ids = [123456789]

[session]
# Directory Claude Code uses as its working directory
working_directory = "~/.archon/workspace"
```

All other sections have sensible defaults. See `README.md` for the full configuration reference.

### 5. Run the daemon

```bash
uv run python main.py
```

The daemon starts in the foreground. Send a message to your bot on Telegram to confirm it is running.

---

## Run tests

```bash
# Run all non-live tests (default)
uv run pytest

# Run a single test file
uv run pytest tests/ai/test_event_mapper.py

# Run a single test by name pattern
uv run pytest -k "test_split_strategy_labels"

# Run live tests (require real claude binary and credentials)
uv run pytest -m live --no-cov -v
```

Coverage must remain at **≥ 85 %** — the test run fails if it drops below.

---

## Install as a system service

For production use, register Archon as a daemon so it auto-starts on login.

### macOS (launchd)

```bash
make install      # install and load the launchd service
make logs         # tail ~/.archon/archon.log
make uninstall    # unload and remove the service
```

The service plist is installed to `~/Library/LaunchAgents/com.archon.assistant.plist` with `KeepAlive = true`.

### Linux (systemd user service)

```bash
make install-linux    # copy unit file and enable --user
make uninstall-linux  # disable and remove the unit file
```

The unit file is installed to `~/.config/systemd/user/archon.service`.

---

## Verify the installation

1. Open Telegram and send your bot any message.
2. You should see `✅ Response:` arrive within a few seconds.
3. Send `/status` to confirm the session is active.
4. Send `/context` to confirm context window tracking is working.

---

## Troubleshooting

**Bot does not respond**
- Check the log: `make logs` (macOS) or `journalctl --user -u archon -f` (Linux)
- Confirm `TELEGRAM_BOT_TOKEN` is set in `.env` and `allowed_user_ids` includes your Telegram user ID in `config.toml`

**`claude: command not found`**
- Install Claude Code CLI and authenticate. It must be in `PATH` when the daemon starts.

**`ModuleNotFoundError`**
- Run `uv sync` to ensure all dependencies are installed.

---

## Next steps

- Full configuration reference → `README.md`
- Contributing guide → `contributing.md`
- Architecture overview → `Documentation/Architecture/100_system_architecture_overview.md`
- Pending features → `Documentation/roadmap.md`
