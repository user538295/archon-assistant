# EVAL-001: Migration from Claude Agent SDK to PydanticAI

**Status:** Research / Evaluation
**Created:** 2026-04-01
**Priority:** Strategic

---

## 1. Motivation

Archon currently depends on `claude-agent-sdk` (`ClaudeSDKClient`), locking all AI operations to Claude models. Migrating to [PydanticAI](https://ai.pydantic.dev/) would make Archon **model-agnostic** — supporting OpenAI, Gemini, Mistral, Groq, Bedrock, local models via Ollama, and more — while gaining type safety, structured outputs, and a richer hook/event system.

---

## 2. Fundamental Architecture Shift

### What changes at the core

| Aspect | Claude Agent SDK (current) | PydanticAI (target) |
|--------|---------------------------|---------------------|
| **Execution model** | Spawns Claude Code as a **subprocess**; communicates via transport protocol | Calls model **APIs directly**; runs in-process |
| **Built-in tools** | Gets all Claude Code tools for free (Read, Write, Edit, Bash, Glob, Grep, Git, Agent spawning) | **No built-in tools** — must be implemented as PydanticAI tool functions |
| **Session persistence** | SDK manages session resume automatically | **Manual** — application stores and passes `message_history` |
| **Model scope** | Claude-only | Any provider (Anthropic, OpenAI, Google, xAI, Bedrock, Ollama, etc.) |
| **Process model** | Multi-process (subprocess per session) | Single-process (async coroutines) |

> **Critical insight:** The Claude Agent SDK doesn't just call the Claude API — it runs the full Claude Code agent with ~20 built-in tools. PydanticAI calls raw model APIs. This is the single largest migration risk.

---

## 3. Modules Requiring Refactoring

### 3.1 Core — Full Rewrite

| Module | Current Role | Migration Impact |
|--------|-------------|-----------------|
| `archon/ai/claude_session.py` | Wraps `ClaudeSDKClient` (subprocess lifecycle, env var management, force-kill recovery) | **Full rewrite.** Replace with PydanticAI `Agent` wrapper. No more subprocess management, CLAUDECODE env handling, or transport-level force-kill. Becomes dramatically simpler. |
| `archon/ai/event_mapper.py` | Transforms SDK message types (`AssistantMessage`, `ToolUseBlock`, `ThinkingBlock`, etc.) to Archon events | **Full rewrite.** Map PydanticAI streaming events (`PartStartEvent`, `PartDeltaEvent`, `FunctionToolCallEvent`, etc.) to Archon's 6-event model instead. |
| `archon/ai/decomposer.py` | Manages 3 `ClaudeSession` instances (main, router, summary) | **Significant refactor.** Replace with 3 PydanticAI `Agent` instances. Multi-model becomes trivial (different providers per agent). |
| `archon/ai/classifier.py` | Uses `ClaudeSession` with Haiku for intent classification | **Moderate refactor.** Replace with PydanticAI `Agent` using structured output (native Pydantic model validation — better than current JSON parsing). |

### 3.2 Supporting — Moderate Changes

| Module | Current Role | Migration Impact |
|--------|-------------|-----------------|
| `archon/ai/pipeline.py` | Orchestrates Classifier → Decomposer; handles tool promotion, force-kill recovery | **Moderate refactor.** Tool promotion logic changes (no subprocess to kill). Graph-based workflows in PydanticAI could simplify the state machine. |
| `archon/ai/background_agent_manager.py` | Spawns fresh `ClaudeSession` per background agent | **Moderate refactor.** Replace with PydanticAI `Agent.run()` in asyncio tasks. No subprocess pool needed. Resource usage drops significantly. |
| `archon/ai/session_manager.py` | Creates sessions, manages `AgentDefinition` SDK type | **Moderate refactor.** Replace `AgentDefinition` with PydanticAI agent configuration. Session creation logic simplified. |
| `archon/ai/history_compactor.py` | Direct `ClaudeSDKClient` usage for Haiku summarization | **Moderate refactor.** Replace with PydanticAI `Agent` with structured output for summaries. |
| `archon/rag/description_generator.py` | Direct `ClaudeSDKClient` usage for RAG description generation | **Moderate refactor.** Same pattern as history compactor. |

### 3.3 Unchanged or Minimal Changes

| Module | Why Unchanged |
|--------|--------------|
| `archon/chat/*` | Consumes Archon events, not SDK types. No changes needed if event model stays stable. |
| `archon/gateway/*` | Orchestrates modules, doesn't touch SDK directly. |
| `archon/config/*` | Configuration layer — model names change but structure stays. |
| `archon/cli/*` | Service management — no SDK dependency. |
| `archon/platform/*` | OS service management — no SDK dependency. |
| `archon/rag/*` (except description_generator) | RAG pipeline is SDK-independent. |
| `archon/ai/truncation.py` | Operates on Archon events, not SDK types. |
| `archon/ai/archon_toolkit.py` | MCP tool definitions — SDK-agnostic. |
| `archon/ai/skill_loader.py`, `plugin_loader.py`, `agent_loader.py` | File-based loaders — SDK-agnostic. |
| `archon/ai/reminder.py` | Context injection — works on prompts, not SDK types. |
| `archon/ai/voice/stt.py`, `tts.py` | External tool wrappers — SDK-independent. |

### 3.4 Tests — Significant Rework

All tests under `tests/ai/` that mock `ClaudeSDKClient` or use SDK message types need rewriting. PydanticAI provides `TestModel` for deterministic testing without API calls — this is actually **better** than current mocking patterns.

---

## 4. Feature Comparison

### 4.1 Features That Improve

| Feature | Current State | With PydanticAI |
|---------|-------------|-----------------|
| **Model flexibility** | Claude-only | Any provider; mix models per agent; `FallbackModel` for automatic provider failover |
| **Classifier structured output** | Manual JSON parsing with resilient fallback | Native Pydantic model validation with `ModelRetry` self-correction |
| **Tool interception** | Limited (can only observe via EventMapper) | Comprehensive hooks: `before_tool_execute`, `after_tool_execute`, `wrap_tool_execute` with name filtering |
| **Testing** | Mock SDK client manually | `TestModel` — deterministic, no API calls, validates tool schemas |
| **Type safety** | Minimal | Full generics: `Agent[DepsType, OutputType]` with mypy/pyright support |
| **Observability** | Basic event logging | OpenTelemetry, Logfire integration, Pydantic Evals |
| **Resource usage** | Multiple subprocesses (one per session) | Single process, async coroutines — dramatically lower memory/CPU |
| **Approval system** | `bypassPermissions` (all-or-nothing) | `ApprovalRequiredToolset` — granular per-tool approval with filter functions |
| **Streaming granularity** | Event-level (ThinkingResult, ToolStarted) | Token-level deltas + structured output streaming |
| **History processing** | Manual | Built-in `history_processors` (trim, filter, summarize) |

### 4.2 Features That Degrade or Are Lost

| Feature | Current State | With PydanticAI | Severity |
|---------|-------------|-----------------|----------|
| **Claude Code built-in tools** | ~20 tools (Read, Write, Edit, Bash, Glob, Grep, Git, Agent, etc.) available automatically | **LOST.** Must reimplement each tool as a PydanticAI tool function. | **CRITICAL** |
| **Automatic session resume** | SDK handles context window, session state, resume tokens | **LOST.** Must implement manual `message_history` management with token counting and context window tracking. | **HIGH** |
| **CLAUDECODE agent ecosystem** | Agents defined in `~/.claude/agents/*.md` are loaded by SDK natively | **LOST.** Must reimplement agent loading and prompt injection. | **MEDIUM** |
| **Plugin system** | SDK loads plugins from `~/.claude/plugins/` with `settings.json` | **LOST.** Plugin format is Claude Code-specific. Must redesign or drop. | **MEDIUM** |
| **Skill injection** | Skills loaded from `~/.claude/skills/*/SKILL.md` with YAML frontmatter | **Partially lost.** Skill loading is custom code, but SDK-level skill activation is gone. System prompt injection can substitute. | **LOW** |
| **Context window management** | SDK auto-manages context, compaction, cache | **LOST.** Must implement token counting, context trimming, and cache management manually or via `history_processors`. | **HIGH** |
| **Cost tracking** | `ResultMessage` provides `total_cost_usd`, `usage` dict, `duration_ms` | **Partially available.** PydanticAI provides `usage` via `result.usage()` but cost calculation varies by provider. | **LOW** |
| **MCP server registration** | Pass `mcp_servers` dict to `ClaudeAgentOptions` | **Available but different.** PydanticAI has `MCP` capability for client-side MCP. Server-side MCP (ArchonMCPServer) needs reimplementation. | **MEDIUM** |

### 4.3 Features That Are Equivalent

| Feature | Notes |
|---------|-------|
| **Streaming events** | Both support event-based streaming. Mapping logic changes but capability is equivalent. |
| **Multi-agent orchestration** | Both support it. PydanticAI via tool delegation + graphs; current via Pipeline + BackgroundAgentManager. |
| **MCP client** | Both support connecting to MCP servers. |
| **Background execution** | Both rely on asyncio tasks. Neither has a built-in daemon. |

---

## 5. Mitigation Strategies

### 5.1 CRITICAL — Claude Code Built-in Tools

**Problem:** Losing ~20 built-in tools (file system, bash, git, etc.) breaks the core value proposition — AI that can read/write code and run commands.

**Mitigation options:**

| Option | Effort | Trade-off |
|--------|--------|-----------|
| **A. Reimplement as PydanticAI tools** | HIGH (weeks) | Full control, model-agnostic, but must handle security (sandboxing, path validation, injection prevention) that Claude Code handles today |
| **B. Use MCP servers for tool provision** | MEDIUM | Run file-system and shell MCP servers; PydanticAI connects as MCP client. Decouples tools from agent framework. Several open-source MCP servers exist. |
| **C. Hybrid: Keep Claude Code for complex tasks** | LOW | Use PydanticAI for classification/routing and Claude Agent SDK for execution. Defeats the model-agnostic goal for the execution layer. |

**Recommendation:** **Option B** — MCP-based tooling. Use existing MCP servers (filesystem, shell) and PydanticAI's `MCP` capability. This keeps tools framework-agnostic and reusable.

### 5.2 HIGH — Session/Context Management

**Problem:** No automatic session resume, context window management, or cache handling.

**Mitigation:**
- Use PydanticAI's `message_history` parameter with a custom `SessionStore` that persists messages to disk/DB
- Use `history_processors` for automatic context trimming (token budget enforcement)
- Implement a `ContextManager` class that tracks token usage per provider's counting method
- Accept that provider-native caching (e.g., Anthropic prompt caching) requires provider-specific code

### 5.3 MEDIUM — Agent/Plugin Ecosystem

**Problem:** `~/.claude/agents/*.md` and `~/.claude/plugins/` formats are Claude Code-specific.

**Mitigation:**
- Define an Archon-native agent format (already partially done — `AgentLoader` parses Markdown frontmatter)
- Convert agents to PydanticAI `Agent` instances with injected system prompts
- Drop plugin dependency on Claude Code's `settings.json`; define Archon-native plugin config

### 5.4 MEDIUM — MCP Server-Side

**Problem:** `ArchonMCPServer` and `ArchonRouterMCPServer` currently serve tools to the SDK subprocess. With PydanticAI running in-process, tools can be registered directly.

**Mitigation:**
- Register Archon toolkit tools directly as PydanticAI tools (no MCP HTTP overhead)
- Keep MCP servers only for external consumers (if any)
- RAG MCP server can remain as-is — PydanticAI connects to it as MCP client

---

## 6. Migration Strategy

### Phase 0 — Abstraction Layer (prep, non-breaking)
- Introduce an `AIClient` protocol/ABC that both `ClaudeSDKClient` and a future PydanticAI adapter can implement
- Extract SDK-specific types from `EventMapper` into a thin adapter
- Ensure all tests use the protocol, not concrete SDK types

### Phase 1 — Classifier + Decomposer Router (low risk)
- Migrate `Classifier` to PydanticAI `Agent` with structured `Classification` output
- Migrate Decomposer's `_router_session` and `_summary_session` to PydanticAI
- These are stateless, single-turn, fast-model calls — simplest migration targets
- Validates the PydanticAI integration pattern before touching the main session

### Phase 2 — Tool Infrastructure
- Set up MCP servers for file system and shell operations (or implement as PydanticAI tools)
- Validate that tool round-trips work correctly across providers
- Implement tool approval/permission model

### Phase 3 — Main Session
- Migrate `ClaudeSession` to PydanticAI `Agent` with full tool set
- Implement session history management (store, restore, trim)
- Migrate `EventMapper` to PydanticAI event types
- Migrate `BackgroundAgentManager` to PydanticAI agents in asyncio tasks

### Phase 4 — Multi-Provider Testing
- Test with OpenAI, Gemini, Mistral, local models
- Identify provider-specific quirks (tool call format, thinking support, token limits)
- Implement `FallbackModel` chains for reliability

---

## 7. Effort Estimate

| Phase | Scope | Relative Effort |
|-------|-------|-----------------|
| Phase 0 | Abstraction layer | Small |
| Phase 1 | Classifier + router sessions | Small–Medium |
| Phase 2 | Tool infrastructure (MCP or native) | Medium–Large |
| Phase 3 | Main session + event mapping + background agents | Large |
| Phase 4 | Multi-provider validation | Medium |
| Test rewrite | All `tests/ai/` mocking patterns | Medium |
| **Total** | | **Large** (multi-week effort) |

---

## 8. Decision Criteria

**Migrate if:**
- Model agnosticism is a strategic requirement (use OpenAI, Gemini, local models)
- Subprocess overhead is a problem (memory, startup time, force-kill complexity)
- Better testing and type safety are high priority
- Tool interception/approval granularity is needed

**Stay on Claude Agent SDK if:**
- Claude-only is acceptable for the foreseeable future
- The ~20 built-in tools are critical and reimplementing them is not justified
- Session management complexity is not worth taking on
- Current architecture is working well and migration risk outweighs benefits

---

## 9. Recommendation

The migration is **feasible but significant**. The biggest risk is not the framework swap itself — PydanticAI's API is cleaner and more capable in most dimensions. The risk is **losing Claude Code's built-in tooling** and **session management**.

**Suggested approach:** Start with Phase 0 (abstraction layer) and Phase 1 (classifier/router). These are low-risk, validate the pattern, and deliver immediate value (structured classifier output, multi-model router). Defer Phase 2–3 until tool infrastructure is proven.

If the primary motivation is model agnosticism for the *execution* layer (not just classification/routing), the MCP-based tool approach (Option B in §5.1) is the most pragmatic path — it decouples tools from both frameworks.
