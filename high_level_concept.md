# Archon Assistant

## Short App definition

This app is an AI assistant which can do almost anything on your computer. In Phase 1 it works with Claude Code and it connects to Telegram chat. The first MVP goal is to connect Claude Code with Telegram. You can send messages in Telegram and they will be sent to Claude Code to process. Claude Code output will be redirected to the Telegram chat and give a response to the user. It would be great if during the work the app could send the texts while Claude works (sending thinkings, tool results — everything that is printed to the terminal when the user uses Claude Code directly from terminal).
The app consists of 3 main parts: chat integration (start with Telegram), the AI integration that handles the input and output of the AI (in this case it will be Claude Code for now), and the gateway that will handle the communication between the chat integration and AI integration. The gateway will start and stop everything when needed and establish the connection between the parts.

## Architecture Decisions (Phase 1 MVP)

| Concern | Decision | Rationale |
|---|---|---|
| Claude Code control | **Claude Agent SDK** (`claude-agent-sdk`) | Structured typed messages (no ANSI parsing), built-in multi-turn sessions, official Python API |
| Output streaming | Logical boundaries | Tool calls, thinking blocks, and final responses sent as separate Telegram messages — structured and readable |
| Session management | One persistent Claude session per user | Full conversation context maintained per Telegram user via SDK session resume |
| Deployment | Local daemon (launchd on macOS / systemd on Linux) | Private, no cloud cost, always-on while machine is running |
| Access control | Whitelist of Telegram user IDs | Simple, hard to bypass, configured in env/config file |

## Module Structure

```
archon/
├── chat/       # Telegram bot — message routing, whitelist enforcement (aiogram 3.x)
├── ai/         # Claude session (SDK), event mapper, truncation, session manager
└── gateway/    # Orchestrator — connects chat ↔ AI, handles start/stop, event routing
```

## Data Flow

```
Telegram ──▶ Gateway ──▶ ClaudeSession (SDK) ──▶ claude CLI
   ▲               │
   └───────────────┘
   (streaming: tool calls / thinking / response as separate messages)
```

## Tech Stack

- **Language:** Python 3.12+ (asyncio)
- **Telegram:** aiogram 3.x
- **Claude Code:** `claude-agent-sdk` (official Claude Agent SDK)
- **Daemon:** launchd plist (macOS) / systemd unit (Linux)
- **Config:** `.env` file (bot token) + `config.toml` (structured config)
