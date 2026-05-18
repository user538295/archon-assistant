**Purpose**: Reference document for the completed FR.001 feature — human-readable sub-agent names
**Audience**: Backend engineers
**Status**: Completed
**Last reviewed**: 2026-02-26
**Next review**: 2027-02-26

---

# FR.001 — Human-readable Agent Names

## Story

**Status**: Completed ✅
**Priority**: High
**Estimated effort**: M

**User Story**: As a Telegram user, I want sub-agents to be identified by human-readable names, so that I can tell which agent is working on which task without decoding opaque SDK identifiers.

## What was built

When the main agent spawns a sub-agent via the Task tool, Archon assigns it a random
human-readable name drawn from a fixed pool of 30. No two concurrently-running sub-agents
share a name. The name is released when the agent stops, making it available for
reassignment. The name is shown in Telegram notifications in place of the opaque SDK
`agent_type` string.

---

## Name pool

Exactly 30 single-word, title-cased names defined as `_AGENT_NAMES: list[str]` (module
constant) in `archon/ai/claude_session.py`:

```
Atlas,  Sage,  Orion, Nova,  Echo,
Cipher, Dusk,  Ember, Flux,  Gale,
Harbor, Iris,  Jade,  Kite,  Lyra,
Mist,   Nexus, Onyx,  Pearl, Quest,
Raven,  Sable, Terra, Umbra, Vega,
Wisp,   Xara,  Yara,  Zara,  Zephyr,
```

---

## Design decisions

**1. Names are Archon-assigned; the SDK provides none.**
The SDK's `hook_input` dict exposes only `agent_id` and `agent_type`. Archon assigns the
human-readable name entirely — the SDK has no awareness of it.

**2. Name assignment is per-session, in-memory only.**
`ClaudeSession` holds `_active_agent_names: dict[str, str]` mapping `agent_id` to name.
Names are not persisted to disk. Each new session starts with an empty registry.

**3. Hook callbacks must remain closures, not instance methods.**
`_on_subagent_start` and `_on_subagent_stop` are async closures defined inside
`_build_hooks`. They access the session instance via `session = self` captured before the
closure definitions — not converted to instance methods — to preserve the existing closure
pattern that already captures `queue`.

**4. `agent_name` is an optional field defaulting to `""`.**
The field was added as `agent_name: str = ""` on both `SubagentStarted` and
`SubagentStopped` to preserve backward compatibility with existing tests that construct
these dataclasses with only `agent_id` and `agent_type`.

**5. Pool exhaustion is handled gracefully without raising.**
When all 30 names are in use, `_assign_agent_name` falls back to `agent_id[:8] or "Agent"`
instead of raising. This keeps the system functional under extreme concurrency.

**6. Display priority: name → type → "unknown".**
`handler.py` uses `agent_name` when non-empty, falls back to `agent_type`, and finally to
the literal `"unknown"`. `html.escape` is applied at every branch to prevent HTML injection.

---

## Implementation scope

| File | Change |
|------|--------|
| `archon/ai/claude_session.py` | Added `_AGENT_NAMES` pool constant, `_active_agent_names` registry, `_assign_agent_name`, `_release_agent_name`; updated `_build_hooks` closures to call them |
| `archon/ai/event_mapper.py` | Added `agent_name: str = ""` to `SubagentStarted` and `SubagentStopped` |
| `archon/chat/handler.py` | Updated `SubagentStarted`/`SubagentStopped` branches to display `agent_name` with fallback to `agent_type` |
| `tests/ai/test_agent_names.py` | New — 22 unit + integration tests |
| `tests/chat/test_handler.py` | Extended — 5 handler format tests |

---

## Acceptance criteria

| Requirement | Test |
|---|---|
| Every sub-agent gets a name | `test_hook_start_puts_subagent_started_with_name` |
| Name comes from pool of 30 | `test_pool_has_exactly_30_names` + `test_assign_returns_name_from_pool` |
| No two concurrent agents share a name | `test_two_concurrent_hooks_assign_different_names` |
| Name released when agent stops | `test_hook_stop_releases_name_from_active_registry` |
| Released names can be reused | `test_name_reused_after_release` |
| Pool exhaustion handled gracefully | `test_assign_exhausted_pool_returns_fallback` |
| Name shown in Telegram messages | `test_format_subagent_started_shows_agent_name` |
| HTML injection prevented | `test_format_subagent_name_is_html_escaped` |

---

## Anti-patterns to avoid

The following patterns were explicitly identified as wrong during implementation. They are preserved here so future work on the name registry or hook system does not re-introduce them.

1. **Do not convert hook callbacks to instance methods.** `_on_subagent_start` and `_on_subagent_stop` are closures defined inside `_build_hooks`. Converting them to `self.` methods would break the closure pattern that captures both `queue` and `session`. Capture `self` as a local variable (`session = self`) inside `_build_hooks` instead.

2. **Do not add `agent_name` as a required positional field.** The field must be `agent_name: str = ""` (optional with default) on both `SubagentStarted` and `SubagentStopped`. Making it required would break the ~10 existing tests that construct these dataclasses with only `agent_id` and `agent_type`.

3. **Do not assume the SDK provides a name.** The SDK's `hook_input` dict exposes only `agent_id` and `agent_type`. Archon assigns the human-readable name entirely — the SDK has no awareness of it.

4. **Do not persist names to disk.** The name registry is in-memory per `ClaudeSession`. Each new session starts with an empty `_active_agent_names` dict. Persisting names across sessions would create stale state after restarts.

---

## Related Documents

- [110 Component Catalog and Layer Breakdown](../Architecture/110_component_catalog_and_layer_breakdown.md) — `ClaudeSession` (name registry), `EventMapper` (`SubagentStarted`/`SubagentStopped` dataclasses), and `BackgroundAgentManager` (separate pool for MCP-spawned background agents)
