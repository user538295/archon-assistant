# Multi-Agent Orchestration System — Architecture Specification

## Overview

This document describes the architecture of a multi-agent orchestration system inspired by OpenClaw/NanoClaw. The system routes user prompts through a layered pipeline: a fast classifier, a decomposer/orchestrator, a Python gateway as control plane, and parallel worker agents. A local QMD instance serves as shared semantic memory across all agents.

---

## Core Design Principles

- **LLMs are stateless workers** — they do not orchestrate each other
- **The Python gateway is the control plane** — it owns spawning, concurrency, error handling, and routing
- **QMD is shared long-term memory** — all agents can query it; agent run logs are indexed into it asynchronously
- **Each layer has a single responsibility** — classifier classifies, decomposer orchestrates, workers execute

---

## System Components

### 1. Classifier (Haiku)
- Fast, cheap first layer
- Receives the raw user prompt
- Outputs a structured classification

**Output schema:**
```json
{
  "intent": "chat" | "task",
  "confidence": 0.92,
  "original_prompt": "..."
}
```

- If `confidence < 0.80`, the Classifier queries QMD before making its decision to improve accuracy
- The Classifier does **not** describe the task or decide execution shape — that belongs to the Decomposer

---

### 2. Gateway (Python)
- Receives all structured outputs from LLMs
- Routes based on `intent` and `scope`
- Spawns and manages worker agents
- Enforces concurrency limits
- Handles failure policies
- Passes upstream agent log file paths to dependent agents
- Returns final output to the user
- Sends status-only feedback to the Decomposer after task completion

---

### 3. Decomposer (Sonnet)
- Receives the Classifier's output + full conversation history
- if `intent == "chat"` then returns answer directly to the user via the gateway
- Acts as the single decision-maker for task execution shape
- Always has full context; can query QMD at any time
- Decides `scope`: whether the task is small (handle directly) or large (spawn agents)
- For large tasks: generates a structured agent execution plan including per-agent prompts with embedded context queries

**Scope heuristics (defined in Decomposer system prompt):**

| Scope   | Criteria                                                                                                      |
| ------- | ------------------------------------------------------------------------------------------------------------- |
| `small` | Single file read/write, single API call, answer derivable from existing context, no step dependencies         |
| `large` | Multiple steps, file creation + validation, output of one step feeds another, requires external investigation |

**Agent plan output schema:**
```json
{
  "scope": "large",
  "agents": [
    {
      "id": "a1",
      "prompt": "Full instruction including what context to retrieve from QMD",
      "depends_on": []
    },
    {
      "id": "a2",
      "prompt": "Full instruction including reference to a1's output",
      "depends_on": ["a1"]
    }
  ]
}
```

- The `prompt` field is self-contained: it includes the task instruction and the context query the agent should use to retrieve relevant information from QMD
- The Decomposer also generates a synthesis-scoped prompt for the Synthesizer agent

---

### 5. Worker Agents (Sonnet or Haiku)
- Spawned by the gateway based on the Decomposer's agent plan
- Receive their `prompt` (which includes context query instructions)
- Query QMD independently as needed (QMD = shared long-term memory)
- Receive file paths of upstream agents' log files directly from the gateway (for same-run dependencies, before QMD indexing catches up)
- Write their output to their own markdown log file upon completion
- Do **not** communicate with each other directly — all coordination goes through the gateway

---

### 6. Synthesizer Agent (Sonnet)
- Spawned by the gateway after all worker agents complete
- Reads worker output log files directly (file paths passed by gateway)
- Synthesizes a coherent final response for the user
- Receives a synthesis-scoped prompt from the Decomposer specifying what to focus on relative to the user's original intent

**Handling large output (context window overflow):**

| Strategy | When to use |
|----------|-------------|
| **Hierarchical synthesis** | Default — synthesize each dependency chain first, then synthesize the summaries |
| **Haiku compression** | Fallback — gateway runs Haiku over each log chunk to compress before passing to Synthesizer |

The Decomposer's dependency structure (`depends_on`) informs how the gateway groups agent outputs for hierarchical synthesis.

---

### 7. QMD (Local Semantic Search)
- Runs locally, indexes all conversation history and agent log files
- Accessible by all agents (Classifier, Decomposer, Workers, Synthesizer) as a tool call
- Used for **historical context** — current run's agent logs are not yet indexed (indexing is asynchronous)
- Current run outputs are accessed via **direct file paths** passed by the gateway
- Acts as the system's long-term shared memory

---

## Full Pipeline Flow

```
User prompt
    │
    ▼
[Classifier / Haiku]
  - confidence < 0.80 → query QMD first
  - output: { intent, confidence, original_prompt }
    │
    ▼
[Gateway] routes on intent:
    │
    ├─ intent == "chat"
    │       └─→ [Decomposer / Sonnet]
    │               Decomposer handles directly
    │               └─→ gateway → user
    │
    └─ intent == "task"
            └─→ [Decomposer / Sonnet]
                    full context + Classifier output injected
                    QMD available
                    decides scope:
                    │
                    ├─ scope == "small"
                    │       └─→ Decomposer handles directly
                    │               └─→ gateway → user
                    │
                    └─ scope == "large"
                            └─→ agent plan { agents[], depends_on[] }
                                    │
                                    ▼
                            [Gateway] manages execution:
                              - resolves depends_on ordering
                              - enforces concurrency limits
                              - passes upstream log file paths to each agent
                              - spawns workers
                                    │
                                    ▼
                            [Worker Agents]
                              - query QMD as needed
                              - read upstream log files directly
                              - write output to own .md log
                                    │
                              on failure:
                              gateway → Decomposer { agent_id, error, partial_output }
                              Decomposer decides:
                                ├─ recoverable → inject fix/investigation agent
                                └─ unrecoverable → notify user with partial result
                                    │
                                    ▼
                            [Synthesizer Agent]
                              - reads all worker log files
                              - hierarchical synthesis if outputs too large
                              - Haiku compression fallback
                                    │
                                    ▼
                            [Gateway] → response → user
                            [Gateway] → status + log paths → Decomposer
                            [QMD] indexes all agent logs asynchronously
```

---

## Context Management Strategy

| Context type | Source | Who accesses it |
|---|---|---|
| Full conversation history | Injected by gateway | Decomposer, Chat Handler |
| Historical knowledge | QMD query (semantic search) | Classifier (on low confidence), Decomposer, Workers, Synthesizer |
| Current run agent outputs | Direct file path (passed by gateway) | Dependent workers, Synthesizer |
| Synthesizer focus | Decomposer-generated synthesis prompt | Synthesizer |

**Key rule:** QMD = historical memory. Direct file access = current run outputs. These two sources are never confused.

---

## Error Handling Policy

When a worker agent fails, the gateway collects:
- `agent_id`
- Error type and message
- What the agent had completed before failure
- Which downstream agents were blocked

This is sent to the Decomposer, which decides:

- **Recoverable** → inject an investigation/fix agent into the existing plan; gateway resumes execution
- **Unrecoverable** → gateway returns partial result to user with explanation; Decomposer logs the failure

---

## Key Design Decisions and Rationale

| Decision                                          | Rationale                                                                               |
| ------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Haiku as classifier, not Sonnet                   | Speed and cost — classification is a simple intent detection task, not a reasoning task |
| Classifier only outputs intent + confidence       | Keeps first layer minimal; Decomposer owns all downstream decisions                     |
| Gateway as control plane, not an LLM              | Determinism, error handling, concurrency control, auditability                          |
| `depends_on` dependency graph                     | Enables mixed parallel/sequential execution without hardcoding topology                 |
| Agent prompt contains context query               | Self-contained agents; no separate context injection step needed                        |
| QMD as shared memory, not injected context        | Scales with history; agents pull only what they need                                    |
| Direct file access for current run                | QMD indexing is async; file paths are the only reliable same-run data source            |
| Synthesizer as dedicated agent                    | Aggregation is a distinct responsibility; workers should not self-synthesize            |
| Hierarchical synthesis for large outputs          | Avoids context window overflow without losing information                               |
| Decomposer receives status only (not full output) | Keeps Decomposer lean; it can query QMD for full details once indexed                   |
