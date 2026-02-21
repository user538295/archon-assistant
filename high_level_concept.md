# Archon Assistant

## Short App definition

This app is an AI assistant which can do almost anything on your computer. In Phase 1 it works with Claude Code and it connects to Telegram chat. The first MVP goal is to connect claude code with telegram. You can send messages in telegram and it will be send to claude code to process. Claude code output will be redirected to the telegram chat and give response to the user. It would be great if during the work the app could send the texts while claude works (sending thinkings, tool result everything that is printed to the terminal when the user use claude code directly from terminal).
The app consists of 3 main pars: chat integration (start with telegram), the AI integration which handle the input and output of the AI (in this case it will be Claude Code for now), and the gateway which will handle the communication between the chat integration and AI integration. The gateway will start and stop everything when needed and establish the connection between the parts.

## Architecture Decisions (Phase 1 MVP)

| Concern | Decision | Rationale |
|---|---|---|
| Claude Code control | PTY (pseudo-terminal) wrapper | Captures exactly what appears in terminal, including interactive prompts and ANSI output |
| Output streaming | Logical boundaries | Tool calls, thinking blocks, and final responses sent as separate Telegram messages — structured and readable |
| Session management | One persistent Claude session per user | Full conversation context maintained per Telegram user |
| Deployment | Local daemon (launchd on macOS / systemd on Linux) | Private, no cloud cost, always-on while machine is running |
| Access control | Whitelist of Telegram user IDs | Simple, hard to bypass, configured in env/config file |

## Module Structure

```
archon/
├── chat/       # Telegram bot — message routing, whitelist enforcement (aiogram 3.x)
├── ai/         # PTY session manager, output parser, per-user session lifecycle
└── gateway/    # Orchestrator — connects chat ↔ AI, handles start/stop, event routing
```

## Data Flow

```
Telegram ──▶ Gateway ──▶ PTY wrapper ──▶ claude CLI
   ▲               │
   └───────────────┘
   (streaming: tool calls / thinking / response as separate messages)
```

## Tech Stack

- **Language:** Python (asyncio)
- **Telegram:** aiogram 3.x
- **PTY control:** `ptyprocess` or `pexpect`
- **Daemon:** launchd plist (macOS) / systemd unit (Linux)
- **Config:** `.env` file (bot token, whitelisted user IDs, working directory)

