# Archon Assistant

A local daemon that bridges Telegram with Claude Code via PTY, streaming every state transition — thinking, tool calls, responses — as real-time Telegram notifications.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Claude Code CLI (`claude`) installed and authenticated
- A Telegram bot token ([create one via @BotFather](https://t.me/BotFather))

## Setup

```bash
# 1. Install dependencies
uv sync

# 2. Configure secrets
cp .env.example .env
# Edit .env and set TELEGRAM_BOT_TOKEN

# 3. Configure the daemon
cp config.toml.example config.toml
# Edit config.toml: set allowed_user_ids, working_directory, etc.
```

## Run

```bash
# Run directly
uv run python main.py

# Install as a background service (macOS, auto-starts on login)
make install

# Uninstall service
make uninstall

# Tail logs
make logs
```

## Development

```bash
# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/ai/test_output_parser.py

# Run a single test by name
uv run pytest -k "test_split_strategy_labels"

# Type check
uv run mypy archon/
```

## Telegram commands

| Command | Description |
|---|---|
| `/start` | Confirm the bot is running |
| `/status` | Show active session info |
| `/stop` | Kill the current Claude session |
