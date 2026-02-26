**Purpose**: Documents the decision to maintain one persistent `ClaudeSession` per Telegram user, with inactivity eviction, rather than creating a new session per message or sharing a single session across users.
**Audience**: All developers
**Status**: Accepted
**Last reviewed**: 2026-02-26
**Next review**: 2026-05-26

# 03. Maintain One Persistent ClaudeSession per Telegram User

**Status**: Accepted
**Date**: 2026-02-26
**Deciders**: Project maintainer

## Context

Each Claude interaction requires a connected `ClaudeSDKClient` instance. Creating a new connection
per message discards conversation context, forcing manual history injection, and adds connection
startup latency to every message. At the same time, routing all users through a single shared
session conflates their conversation histories. Sessions must not run indefinitely when users are
inactive, to avoid resource leaks.

## Decision

`SessionManager` in `archon/ai/session_manager.py` maintains a `dict[int, ClaudeSession]` keyed
by the Telegram `user_id` (integer). A per-user `asyncio.Lock` in `self._locks` prevents race
conditions when two messages from the same user arrive concurrently during session creation.

`get_or_create(user_id)` is the single entry point used by `handle_message()`:

1. On the first call for a user, it instantiates a `ClaudeSession` via `_default_factory`, calls
   `session.start()` (which calls `ClaudeSDKClient.connect()`), and stores the session in
   `self._sessions`.
2. On subsequent calls it returns the existing session directly.
3. An inactivity timer is reset on every call via `_reset_timer(user_id)`. When the timer fires
   after `inactivity_timeout_seconds`, `_evict_after()` calls `session.stop()`
   (`ClaudeSDKClient.disconnect()`) and removes the entry from `self._sessions`.

Conversation context is maintained entirely by the long-lived `ClaudeSDKClient` connection — the
SDK handles multi-turn context internally. No explicit `session_id` is stored or passed; context
survives as long as the connection remains open.

The inactivity timeout is configured via `[session] inactivity_timeout_seconds` in `config.toml`.

## Consequences

### Positive

- Full conversation history is preserved across messages within the inactivity window, with no
  manual context injection required
- Per-user isolation — each user has an independent `ClaudeSDKClient` process and conversation
- `get_or_create()` is transparent to callers; `handle_message()` never manages session lifecycle
  directly
- Automatic inactivity eviction prevents unbounded resource accumulation

### Negative

- When a session is evicted due to inactivity, conversation context is permanently lost; the next
  message starts a fresh session
- Session state is in-memory only — a daemon restart drops all active sessions and their
  conversation contexts
- High concurrency means one `ClaudeSDKClient` process per active user; resource usage scales
  linearly with active user count

## Alternatives Considered

- **New session per message**: Create a fresh `ClaudeSession` for every incoming Telegram message.
  Rejected because each message would start a blank conversation — Claude would have no memory of
  previous exchanges — and connection startup latency would add overhead to every interaction.
- **Single shared session for all users**: Route all users through one `ClaudeSession` instance.
  Rejected because a single session cannot maintain separate conversation histories for multiple
  users simultaneously; responses would bleed context across users.
- **Session pool with manual context injection**: Maintain a pool of reusable sessions and inject
  the user's conversation history as text before each message. Rejected because it adds
  significant complexity and text-based context injection is lossy compared to the SDK's native
  multi-turn handling.
