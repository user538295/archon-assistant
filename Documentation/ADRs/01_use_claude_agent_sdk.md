**Purpose**: Documents the decision to use `claude-agent-sdk` (`ClaudeSDKClient`) for all Claude Code interactions instead of PTY/subprocess control.
**Audience**: All developers
**Status**: Accepted
**Last reviewed**: 2026-02-26
**Next review**: 2026-05-26

# 01. Use Claude Agent SDK for Claude Code Control

**Status**: Accepted
**Date**: 2026-02-26
**Deciders**: Project maintainer

## Context

Archon bridges Telegram with Claude Code, requiring programmatic control of Claude. Claude Code
normally runs interactively in a terminal, emitting ANSI-escaped output that must be parsed to
extract structured information. Controlling Claude this way from a headless daemon creates several
hard problems: parsing ANSI escape sequences is brittle, interactive permission dialogs block
execution, and multi-turn context management must be reimplemented manually.

Constraints:

- Claude's output must be parsed into structured event types (thinking, tool calls, text response)
- Multi-turn conversation context must be maintained across Telegram messages
- Interactive permission prompts cannot be shown in a headless daemon process

## Decision

Use the `claude-agent-sdk` package (import: `claude_agent_sdk`) and its `ClaudeSDKClient` class
for all Claude Code interactions.

`archon/ai/claude_session.py` wraps `ClaudeSDKClient` in a `ClaudeSession` class:

- `ClaudeAgentOptions` is constructed with `permission_mode="bypassPermissions"` to skip all
  interactive permission dialogs
- `await client.connect()` starts the Claude process (`ClaudeSession.start()`)
- `await client.query(prompt)` submits a prompt, followed by
  `async for msg in client.receive_response()` to consume the response stream
  (`ClaudeSession.send()`)
- `await client.disconnect()` terminates the process (`ClaudeSession.stop()`)

The SDK yields typed messages — `AssistantMessage`, `UserMessage`, and `ResultMessage` — whose
content blocks (`ThinkingBlock`, `ToolUseBlock`, `ToolResultBlock`, `TextBlock`) are mapped by
`EventMapper` into Archon's own event dataclasses. The `ResultMessage.result` field delivers the
final response text.

`ClaudeAgentOptions` also accepts `cwd`, `system_prompt`, `model`, `plugins`, `agents`,
`disallowed_tools`, and `mcp_servers` — all injected per-session from config, keeping the SDK
layer decoupled from gateway concerns.

## Consequences

### Positive

- No PTY or ANSI parsing — the SDK delivers structured, typed messages
- Multi-turn conversation context is maintained natively by the long-lived `ClaudeSDKClient`
  connection without manual history management
- `permission_mode="bypassPermissions"` eliminates all interactive prompts in the headless daemon
- Official Python API with typed interfaces reduces integration surface area

### Negative

- Tight coupling to `claude-agent-sdk` version and its API surface; breaking changes require
  updates to `ClaudeSession`
- Claude Code features not exposed by the SDK cannot be used
- The SDK is in 0.x; its API stability guarantees are limited

## Alternatives Considered

- **Subprocess + PTY (pexpect)**: Drive the `claude` binary via a pseudo-terminal, parse ANSI
  escape sequences to extract structured output. Rejected because ANSI parsing is fragile,
  requires reverse-engineering the output format, and breaks whenever Claude's UI changes.
- **Direct Anthropic API calls**: Call the Anthropic Messages API directly, bypassing Claude Code
  entirely. Rejected because Claude Code's built-in file system tools, codebase awareness, and
  tool execution environment would be unavailable — reducing the assistant to a plain chat model.
