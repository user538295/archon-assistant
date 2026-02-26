**Purpose**: Documents the decision to expose background agent spawning as an MCP tool via a local aiohttp HTTP server instead of using the Claude Agent SDK's native Task tool.
**Audience**: All developers
**Status**: Stable
**Last reviewed**: 2026-02-26
**Next review**: 2026-08-26

# 06. Background Agents via Local MCP HTTP Server

**Status**: Accepted
**Date**: 2026-02-26
**Deciders**: Archon project team

## Context

Claude Code can use its built-in `Task` tool to spawn sub-agents, but the SDK's `Task` tool runs the sub-agent synchronously inside the current session turn. This would block `ClaudeSession.send()` for the entire sub-agent duration — the user could not send new messages until the sub-agent finished. This violates the core invariant that the main conversation must remain interactive at all times.

A non-blocking mechanism was needed that:

- Lets Claude autonomously decide to offload long tasks to isolated agents
- Returns control to the main session immediately after spawning
- Uses a standard, tool-agnostic interface (not Archon-specific SDK hacks)
- Keeps background agent state isolated from the main conversation

## Decision

The SDK's native `Task` tool is unconditionally disabled by adding it to `disallowed_tools` in `ClaudeSession.start()`, alongside `EnterPlanMode` and `ExitPlanMode` (which require an interactive TTY dialog unavailable in a headless SDK session):

```python
disallowed: list[str] = ["EnterPlanMode", "ExitPlanMode", "Task"]
```

Sub-agent spawning is instead exposed through `ArchonMCPServer` (`archon/ai/archon_mcp_server.py`), a minimal aiohttp HTTP server that implements MCP protocol version `2024-11-05` over JSON-RPC 2.0. The server binds to `localhost` on a configurable port (default 18182) and exposes a single route: `POST /mcp/{user_id}`.

The server advertises one tool:

| Parameter | Type | Required | Description |
|---|---|---|---|
| `task` | string | yes | The task for the agent to perform |
| `context` | string | no | Relevant context or data the agent needs |
| `user_request` | string | no | Original Telegram message; recorded as first entry in the agent's log file |

Each `ClaudeSession` is given its own MCP endpoint URL (`http://localhost:{port}/mcp/{user_id}`) so spawn requests are routed to `BackgroundAgentManager` with the correct user context.

When Claude calls `spawn_background_agent`, `ArchonMCPServer` delegates to `BackgroundAgentManager.spawn()`, which creates an `asyncio.Task` and returns immediately. Each background agent runs in its own isolated `ClaudeSession` instance — no shared state with the main session. The tool returns a confirmation string (`Agent {name} started (run_id: {id})`) to Claude before the agent has done any work.

The MCP server always starts unconditionally on daemon boot (no `enabled` flag); it is inseparable from the daemon's operation because `Task` is always disabled.

Claude's autonomous decision to spawn is shaped by a `spawn_rule` system prompt hint (`eager`, `auto`, or `manual`) configured in `config.toml`.

## Consequences

### Positive

- Main conversation never blocks: `send()` returns as soon as Claude responds; the user can immediately send another message.
- Standard MCP interface: Claude treats `spawn_background_agent` as any other tool — no SDK-specific code paths.
- Full isolation: each background agent has its own `ClaudeSession`, independent context window, and lifecycle.
- Up to `max_parallel` concurrent agents per user (default 5), enforced by `BackgroundAgentManager.spawn()` before creating the asyncio task.
- `spawn_rule` gives the operator control over how aggressively Claude uses background agents.

### Negative

- Adds an HTTP server running inside the daemon process, increasing internal complexity.
- Requires `aiohttp` as a runtime dependency.
- Agent results are delivered asynchronously via Telegram notification — not inline in the conversation turn that triggered the spawn.
- The MCP server is localhost-only by design; it cannot be reached from remote processes.

## Alternatives Considered

- **SDK native `Task` tool**: Rejected because it executes sub-agents synchronously within the current session turn, blocking `send()` and preventing the user from sending new messages for the agent's entire duration. Documented as a key design invariant and the motivation behind Bug.005.
- **Direct asyncio task without MCP wrapper**: Rejected because it would require Archon to intercept SDK tool calls with custom code rather than letting Claude autonomously call a standard tool. The MCP interface is cleaner and keeps the AI layer decoupled from the spawning mechanism.
- **Subprocess**: Rejected because a subprocess cannot share the asyncio event loop, requires inter-process communication for returning results, and adds process management overhead without any benefit over asyncio tasks.
