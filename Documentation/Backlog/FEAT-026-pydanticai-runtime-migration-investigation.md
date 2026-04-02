# FEAT-026 — PydanticAI Runtime Migration Investigation
**Purpose**: Evaluate replacing the Claude Agent SDK runtime with PydanticAI to gain model/provider agnosticism without breaking Archon's core coding-agent behavior.
**Audience**: Archon maintainers and operators making architecture and roadmap decisions.
**Status**: Research / To Do
**Date**: 2026-03-31

---

## Bottom line

PydanticAI is a strong fit if the goal is:

- model/provider abstraction
- multi-provider fallback
- better testability
- explicit human approval and deferred-tool workflows
- more explicit conversation-state control

PydanticAI is **not** a drop-in replacement for the current Claude Agent SDK integration.

The current Archon architecture is built around **Claude Code as the execution environment**, not just around a generic LLM SDK. A direct swap would regress core features unless Archon also rebuilds its local coding-agent toolchain and its session/event model.

**Recommendation**: do not do a big-bang replacement. Introduce a runtime abstraction, keep the Claude SDK backend, add a PydanticAI backend, and only consider making it the default after parity gates pass.

---

## Why this is not a drop-in swap

Archon's current design depends on Claude-specific assumptions documented in:

- `Documentation/ADRs/01_use_claude_agent_sdk.md`
- `Documentation/ADRs/03_one_session_per_user.md`
- `Documentation/Architecture/000_introduction_and_guiding_principles.md`
- `Documentation/Architecture/100_system_architecture_overview.md`
- `Documentation/Architecture/110_component_catalog_and_layer_breakdown.md`
- `Documentation/Architecture/120_services_and_integration_architecture.md`

Those documents and the codebase assume all of the following:

- one long-lived `ClaudeSDKClient` connection per user
- SDK-native multi-turn context retention
- SDK-native typed streaming for thinking, tool calls, tool results, and final response
- Claude Code local toolchain and code-workspace behavior
- Claude-specific skills, plugins, and agent definitions loaded from `~/.claude`

PydanticAI provides a different model:

- agents are reusable, but conversation continuity is carried explicitly with `message_history`
- event streaming is available, but Archon must reconstruct its Telegram event model from lower-level stream parts and graph events
- tool execution is explicit function/toolset/MCP orchestration
- provider-native built-in tools vary by provider and are sometimes executed in provider infrastructure, not locally

This means the migration is architectural, not mechanical.

---

## Architecture changes required

### 1. Replace the Claude-specific runtime with a backend abstraction

Archon needs a new AI-runtime boundary so the rest of the system no longer depends on `ClaudeSession` semantics.

That boundary must own:

- sending a user turn
- streaming normalized Archon events
- usage reporting
- context injection
- one-shot skill/instruction injection
- model selection
- capability reporting

Without this layer, the migration will spread provider-specific branching through `pipeline.py`, `session_manager.py`, chat handlers, and background-agent execution.

### 2. Replace persistent SDK sessions with a conversation-state store

Today, context lives inside the long-lived SDK connection. With PydanticAI, Archon must own conversation history explicitly.

That requires:

- a per-user message-history store
- serialization and restore across process restarts
- truncation and summarization policy
- a new definition of session lifecycle

This is the single biggest architectural change. ADR-03 would no longer be true as written.

### 3. Replace the SDK event mapper with a provider-neutral event adapter

`EventMapper` currently maps Claude SDK message/block types directly into Archon events. Under PydanticAI, Archon must normalize:

- part start / delta / end events
- tool-call events
- tool-result events
- final result events
- agent graph node transitions when needed

Archon's Telegram UX can stay, but the adapter becomes more complex and more provider-sensitive.

### 4. Separate tool execution from model runtime

The current runtime gets a large amount of behavior "for free" from Claude Code. PydanticAI does not.

Archon must define an explicit tool strategy for:

- local file operations
- search / grep / workspace traversal
- shell execution
- code editing
- background-agent spawning
- operator approvals for dangerous actions

The safest reusable path is to keep Archon's MCP servers and connect to them from PydanticAI through direct MCP/FastMCP clients. Provider-built tools should not become the core local-workspace path.

### 5. Replace Claude asset assumptions with Archon-native capability packs

The current `SkillLoader`, `PluginLoader`, and `AgentLoader` are tied to Claude filesystem conventions and Claude SDK concepts.

If Archon moves to PydanticAI as a first-class backend, it needs its own native concepts for:

- reusable instruction packs
- tool bundles/toolsets
- agent profiles
- optional compatibility import from `~/.claude`

Otherwise the migration breaks one of Archon's current operator workflows.

### 6. Expand model configuration from "model name" to "provider profile"

Current config is too Claude-shaped for a truly model-agnostic runtime.

Archon will need:

- provider identity
- model name
- provider-specific settings
- credentials/base URL indirection
- fallback chains
- per-model context budgets
- capability flags

### 7. Add capability gating and evaluation gates

"Model agnostic" does not mean "feature agnostic". Archon needs an explicit capability matrix per backend/provider/model.

Examples:

- thinking stream available or not
- built-in tool streaming parity available or not
- local file operations available or not
- tool approval flow required or not
- accurate context-window progress available or not

Without capability gating, the Telegram UX will promise features some models cannot actually deliver.

---

## Module impact

### Hard replace

- `archon/ai/claude_session.py`
- `archon/ai/event_mapper.py`
- `archon/ai/classifier.py`
- `archon/ai/decomposer.py`
- `archon/ai/pipeline.py`
- `archon/ai/history_compactor.py`

These modules are directly coupled to Claude SDK session semantics, Claude message types, or both.

### Major refactor

- `archon/ai/session_manager.py`
- `archon/ai/background_agent_manager.py`
- `archon/ai/job_scheduler.py`
- `archon/ai/constants.py`
- `archon/chat/handler.py`
- `archon/chat/voice.py`
- `archon/chat/commands.py`
- `archon/ai/event_renderer.py`
- `archon/ai/tool_result_policy.py`
- `archon/chat/telegram_formatter.py`
- `archon/config/loader.py`
- `archon/gateway/gateway.py`

These modules can survive, but their assumptions about sessions, event shapes, usage stats, and model selection will change.

### Redesign or deprecate

- `archon/ai/skill_loader.py`
- `archon/ai/plugin_loader.py`
- `archon/ai/agent_loader.py`

These are Claude-ecosystem integrations, not generic AI-runtime abstractions.

### Mostly reusable

- `archon/ai/archon_toolkit.py`
- `archon/ai/archon_toolkit_rag.py`
- `archon/ai/archon_mcp_server.py`
- `archon/ai/archon_router_mcp_server.py`
- attachment handling modules
- RAG server modules
- Telegram transport/formatting infrastructure
- platform/service modules

These are Archon-owned integrations and remain valuable. MCP is especially reusable because PydanticAI supports direct local and remote MCP usage.

### Test suites that would be heavily rewritten

- `tests/ai/` SDK-centric tests
- routing and streaming integration tests
- live/integration tests that assert Claude SDK event semantics
- any tests mocking `ClaudeSDKClient`, `ClaudeAgentOptions`, or Claude message blocks

The good news is that PydanticAI offers a better native testing surface, but the migration cost is still high.

---

## Feature comparison

| Area | Current Claude SDK runtime | PydanticAI impact |
|---|---|---|
| Provider choice | Claude-centric | Better |
| Multi-provider fallback | Minimal | Better |
| Explicit human approvals | Custom work needed | Better |
| Unit testing of agent logic | Heavy SDK mocking | Better |
| Local coding-agent behavior | Strong, because Claude Code provides it | Worse unless rebuilt |
| `~/.claude` skills/plugins/agents | Native | Worse |
| Real-time tool/thinking streaming parity | Strong and predictable | Worse/variable by provider |
| Persistent per-user session behavior | Native long-lived session | Must be rebuilt manually |
| `/context` fidelity | Strong because SDK usage/cache data is available | Worse unless redefined |
| Background-agent semantics | Already solved around current runtime | Similar outcome possible, but redesign required |
| Conversation persistence across daemon restart | Weak today | Potentially better if Archon stores message history |
| MCP reuse | Already used | Reusable and still valuable |

---

## What gets better

### Better provider flexibility

PydanticAI gives Archon a clean path to multiple providers, OpenAI-compatible endpoints, and model fallback chains.

### Better testing

PydanticAI's `TestModel` and `FunctionModel` are materially better than the current SDK-mocking strategy for most unit and integration tests.

### Better approval and deferred execution patterns

PydanticAI's deferred-tool model maps well to:

- operator approval flows
- background work handoff
- long-running tasks
- frontend or external tool completion

This directly supports security hardening already identified in the backlog.

### Better explicit state control

Owning message history explicitly makes future features easier:

- persistence across restart
- provider switching mid-conversation
- custom summarization
- replay/evals/debugging

---

## What gets worse or degrades

### 1. Local coding-agent capability

This is the biggest regression risk.

Claude Code gives Archon:

- local workspace awareness
- local file and shell tools
- coding-oriented agent behavior
- plugin/skill/agent ecosystem compatibility

PydanticAI does not provide that by itself. It is an orchestration framework, not a local coding-agent shell.

### 2. Session model

Current Archon relies on a long-lived per-user connection. PydanticAI expects Archon to pass conversation state explicitly between runs.

That means:

- more Archon-owned state management
- likely higher request payload size
- potentially higher latency/cost on long conversations
- less direct equivalence to today's "persistent session" model

### 3. Event streaming consistency

Archon's current Telegram UX depends on predictable Claude SDK event boundaries.

Under PydanticAI:

- event reconstruction becomes Archon's responsibility
- built-in tool events vary by provider
- some providers expose less streaming detail than others
- thinking visibility becomes model/provider dependent

### 4. `/context` semantics

Current `/context` relies on Claude SDK usage metadata and cache behavior. That exact signal does not generalize cleanly across providers.

Archon can still show usage, but context-progress accuracy will be weaker unless the command is redefined around estimated budgets instead of Claude cache metrics.

### 5. Claude ecosystem compatibility

The current loaders for Claude skills/plugins/agents stop being first-class. At best, they become compatibility importers.

---

## Degradation risks and mitigations

### Risk: losing local coding parity
**Mitigation**

- Keep the Claude backend during migration.
- Build Archon-native local tools first: file ops, search, edit, shell, approvals.
- Use Archon MCP/function tools, not provider-built code execution, for workspace actions.

### Risk: higher latency and token cost from replayed history
**Mitigation**

- Introduce explicit message-history storage.
- Add summarization/trimming policies.
- Keep cheap specialist models for classifier/router/summary paths.
- Use short-lived router runs and compacted histories.

### Risk: inconsistent streaming across providers
**Mitigation**

- Normalize everything through an Archon capability matrix.
- Promise only the common denominator in Telegram.
- Prefer Archon function tools/MCP tools where event parity matters.
- Treat thinking visibility as optional, not invariant.

### Risk: broken background-agent UX
**Mitigation**

- Keep the current `BackgroundAgentManager` concept.
- Re-implement `spawn_background_agent` as an Archon-owned external/deferred tool pattern.
- Do not switch to generic in-run delegation if it blocks the main chat UX.

### Risk: security regressions from new local tools
**Mitigation**

- Build approvals into the tool layer from day one.
- Reuse the current MCP isolation work and align with `FIX-027`.
- Treat file mutation, shell execution, config changes, and file sending as approval-gated by default.

### Risk: losing Claude-specific skill/plugin workflows
**Mitigation**

- Define Archon-native instruction packs and tool bundles.
- Add best-effort import from `~/.claude`, but do not keep Claude-specific filesystem formats as the core contract.

---

## Recommended migration plan

### Phase 1 — Introduce a runtime abstraction

Goal:

- isolate the Claude-specific runtime behind an Archon-owned backend interface
- add a backend capability matrix
- keep all current behavior unchanged

Deliverable:

- Claude backend remains default
- no user-visible regression

### Phase 2 — Add PydanticAI in low-risk paths first

Recommended first candidates:

- classifier
- history compactor
- scheduled prompt steps

Why:

- they need less local coding behavior
- they benefit immediately from provider choice and easier testing
- they do not force the main coding session to move yet

### Phase 3 — Build conversation-state ownership

Goal:

- replace "persistent SDK process equals persistent conversation" with an explicit per-user message-history store
- redesign `/context`
- add summarization and trimming policy

This phase is mandatory before the main session can move.

### Phase 4 — Build Archon-native tool parity

Goal:

- recreate the minimum tool/capability surface Archon needs for coding workflows

Minimum parity target:

- read/search workspace
- write/edit files
- shell execution
- RAG/MCP access
- background-agent spawn
- approval-gated dangerous actions

### Phase 5 — Move background agents and routing

Goal:

- port `BackgroundAgentManager`, routing, and task decomposition onto the new backend
- preserve today's invariant: sub-agents never block the main conversation

This is where most user-visible regression risk lives.

### Phase 6 — Evaluate default-backend readiness

Only consider making PydanticAI the default when all of the following are true:

- Telegram event parity is acceptable
- background-agent UX is preserved
- coding-tool parity is acceptable
- `/status` and `/context` remain trustworthy
- test coverage and eval results meet current standards
- core docs and ADRs are updated

If those gates fail, keep PydanticAI as an additional backend, not a replacement.

---

## Strong recommendation

If Archon's product identity remains "Claude Code in Telegram", then a full replacement is the wrong move.

If Archon's product identity becomes "a local Telegram control plane for multiple agent backends", then PydanticAI is a good strategic addition, but only behind a new runtime abstraction.

In practical terms:

- **Recommended**: dual-backend architecture, Claude SDK backend + PydanticAI backend
- **Not recommended**: direct full replacement of the current main coding runtime in one step

---

## Documentation that must be rewritten if migration proceeds

- `README.md`
- `Documentation/Architecture/000_introduction_and_guiding_principles.md`
- `Documentation/Architecture/010_engineering_principles_and_constraints.md`
- `Documentation/Architecture/100_system_architecture_overview.md`
- `Documentation/Architecture/110_component_catalog_and_layer_breakdown.md`
- `Documentation/Architecture/120_services_and_integration_architecture.md`
- `Documentation/ADRs/01_use_claude_agent_sdk.md`
- `Documentation/ADRs/03_one_session_per_user.md`

These are not minor edits. They encode assumptions that the migration would invalidate.

---

## Research basis

### Official PydanticAI sources

- Model providers and fallback: `https://ai.pydantic.dev/models/overview/`
- Agents, runs, streaming, and usage: `https://ai.pydantic.dev/agent/`
- Message history and cross-run conversation state: `https://ai.pydantic.dev/message-history/`
- MCP support: `https://ai.pydantic.dev/mcp/overview/`
- Deferred tools / approvals / external execution: `https://ai.pydantic.dev/deferred-tools/`
- Multi-agent patterns: `https://ai.pydantic.dev/multi-agent-applications/`
- Built-in tools and provider-adaptive capabilities: `https://ai.pydantic.dev/builtin-tools/`
- Testing: `https://ai.pydantic.dev/testing/`
- Durable execution: `https://ai.pydantic.dev/durable_execution/overview/`

### Most relevant findings from those sources

- PydanticAI is model-agnostic and supports multiple providers and OpenAI-compatible endpoints.
- Conversations across runs are explicit and use `message_history`; they are not hidden inside a long-lived provider session.
- Streaming all events is possible, but Archon must reconstruct final text and output from streamed parts/events.
- PydanticAI can connect directly to local and remote MCP servers, which means Archon's internal MCP investment remains useful.
- Deferred tools provide native support for approvals and external/background execution.
- Built-in tools are provider-dependent, and some are executed in provider infrastructure rather than the local machine.
- Some providers expose weaker streaming/tool detail than others, so feature parity cannot be assumed.
- PydanticAI's testing surface is materially better than the current SDK-mocking approach.

