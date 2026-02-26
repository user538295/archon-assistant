**Purpose**: Documents the decision to send each logical SDK event (thinking, tool call, tool result, response) as a separate Telegram message rather than streaming raw tokens.
**Audience**: All developers
**Status**: Accepted
**Last reviewed**: 2026-02-26
**Next review**: 2026-05-26

# 02. Send Each Logical Event as a Separate Telegram Message

**Status**: Accepted
**Date**: 2026-02-26
**Deciders**: Project maintainer

## Context

Claude Code's work spans multiple phases: extended thinking, tool calls (each with a result), and
a final text response. Users need to see Claude's progress in real time so they know what is
happening during long-running tasks. Telegram's API does not support character-level streaming;
messages can only be sent whole or edited in place.

The core question is what unit of output to send — raw tokens, a periodic status ping, or a
message per logical event boundary.

## Decision

Map each logical SDK event to one or more Telegram messages. Seven event dataclasses are defined
in `archon/ai/event_mapper.py`:

| Dataclass | Telegram format (debug mode) |
|---|---|
| `ThinkingResult` | `💭 Thinking:\n<content>` |
| `ToolStarted` | `🔧 Tool [id]: <name>` (with input in verbose/debug) |
| `ToolResult` | `📤 Result [id]:\n<content>` |
| `Response` | `✅ Response:\n<content>` |
| `ErrorEvent` | `❌ Error: <message>` |
| `SubagentStarted` | `🤖 Agent <b>name</b> started` |
| `SubagentStopped` | `🤖 Agent <b>name</b> done` |

`EventMapper.map_messages()` in `archon/ai/event_mapper.py` translates raw SDK messages
(`AssistantMessage`, `UserMessage`, `ResultMessage`) into these dataclasses: `ThinkingBlock` →
`ThinkingResult`, `ToolUseBlock` → `ToolStarted`, `ToolResultBlock` → `ToolResult`,
`ResultMessage` → `Response` or `ErrorEvent`.

`format_event()` in `archon/chat/handler.py` converts each dataclass into Telegram HTML strings.
`handle_message()` then sends each string via `message.answer()` — one Telegram message per
formatted string.

Content-bearing events pass through `TruncationStrategy.apply(text, max_len)` before sending.
`SplitStrategy` chunks content longer than 4000 characters into pages labelled `[1/N]`.

Visibility is configurable through four notification modes in `config.toml`:

- `quiet` — only `Response`, `ErrorEvent`, `SubagentStarted`, `SubagentStopped`
- `normal` — tool name only, brief single-line `ToolResult`, no thinking
- `verbose` — tool name + input, brief `ToolResult`, full thinking
- `debug` — tool name + input, full `ToolResult`, full thinking

`Response`, `ErrorEvent`, `SubagentStarted`, and `SubagentStopped` are invariants: they are
always delivered to the user regardless of the active notification mode.

## Consequences

### Positive

- Users see Claude's progress in real time — each thinking block, tool call, and result arrives as
  it happens
- Each Telegram message carries clear semantic meaning via its emoji prefix
- Notification modes let users reduce noise (`quiet`) or maximize detail (`debug`) without changing
  the underlying event model
- The event model is decoupled from the SDK — adding a new event type only requires changes in
  `event_mapper.py` and `handler.py`

### Negative

- Long responses with many tool calls produce many Telegram messages, increasing chat noise
- Telegram rate limits on `sendMessage` can cause delivery delays when tool calls are rapid
- Thinking content can be lengthy; `SplitStrategy` pagination adds `[1/N]` labels that fragment
  the text across multiple messages

## Alternatives Considered

- **Stream raw characters**: Send each token as it arrives. Rejected because Telegram's API does
  not support character-level streaming, and doing so would require thousands of `editMessage` API
  calls or an impractical polling loop.
- **Single message at completion**: Wait for Claude to finish, then send one consolidated message.
  Rejected because users would see no progress indicator during long-running tasks, which can span
  many seconds.
- **Periodic status-only updates**: Send only a "still working…" ping at fixed intervals, hiding
  individual events. Partially adopted as the `quiet` mode with `interval_minutes` configuration,
  but not as the default, because hiding tool activity removes information that is frequently
  useful for debugging and oversight.
