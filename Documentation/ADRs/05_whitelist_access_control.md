**Purpose**: Documents the decision to use a static Telegram user ID whitelist enforced at the middleware layer as the sole access control mechanism.
**Audience**: All developers, security reviewers
**Status**: Stable
**Last reviewed**: 2026-02-26
**Next review**: 2026-08-26

# 05. Whitelist-Based Telegram Access Control

**Status**: Accepted
**Date**: 2026-02-26
**Deciders**: Archon project team

## Context

Archon gives Claude Code full access to the user's local filesystem and can execute arbitrary shell commands on the host machine. The Telegram bot is publicly addressable — anyone who discovers the bot username can send it messages.

Access must be restricted to a small, known set of trusted users. The access control mechanism must:

- Be impossible to accidentally bypass or circumvent
- Apply before any session logic or command handler runs
- Cover all incoming event types, not just text messages
- Be simple enough to audit at a glance

## Decision

Implement access control via `WhitelistMiddleware`, an aiogram `BaseMiddleware` in `archon/chat/middleware.py`. The middleware holds a `frozenset[int]` of allowed Telegram user IDs loaded from `[access] allowed_user_ids` in `config.toml`.

On every incoming event the middleware checks `from_user.id` against the frozenset. Unauthorized `Message` and `CallbackQuery` events are silently dropped — the handler returns `None` immediately, a warning is logged (`Dropped <event type> from unauthorized user <id>`), and no reply is sent to the unauthorized user. Whitelisted events pass through to the next handler unchanged.

The middleware is registered in `gateway.py` on both the `message` and `callback_query` dispatchers via `dp.message.middleware(mw)` and `dp.callback_query.middleware(mw)`, ensuring full coverage of all user-facing interaction types. The `frozenset` type gives O(1) lookup and runtime immutability.

## Consequences

### Positive

- Telegram user IDs are globally unique and cannot be spoofed — zero false positives once the list is correct.
- Enforced at the middleware layer, before any session creation, command parsing, or Claude invocation.
- Silent drop prevents unauthorized users from probing the bot's capabilities or confirming its existence.
- Entire policy fits in one ~30-line file; easy to audit and test.
- Covers both `Message` and `CallbackQuery` event types, so inline keyboard interactions are also gated.

### Negative

- Static list — adding or removing users requires editing `config.toml` and restarting the daemon; there is no runtime management command.
- No revocation without a restart; a revoked user can interact until the daemon next restarts.
- No rate limiting or per-user abuse detection for whitelisted users.
- No delegation or role-based distinctions — all whitelisted users have identical, full access.

## Alternatives Considered

- **Bot password / shared token**: Users send a secret password to authenticate. Rejected because passwords can be leaked or shared, and the middleware would need a state machine to track authentication per chat session — significant complexity for marginal security gain.
- **OAuth / OIDC**: Rejected as massive over-engineering for a personal-use tool with a handful of trusted users. Requires an external identity provider and a web callback flow incompatible with the Telegram chat interface.
- **Telegram group or channel membership check**: Rejected because it requires an API call on every incoming event and introduces a dependency on Telegram's availability in the hot path of access control. It also cannot restrict which group members have access.
