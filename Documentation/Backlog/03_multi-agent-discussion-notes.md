# Multi-Agent Architecture — Discussion Notes (2026-02-27)

## Agreed Architecture

2 LLM sessions per user. No config toggle. No backward compatibility. This is how the system works.

### Components

1. **Classifier** (Haiku, persistent per user)
   - Receives user message
   - Outputs ONLY JSON: `{intent: "chat"|"task", confidence: 0.0-1.0}`
   - Stripped-down session: no plugins, no agents, no background agent MCP. Only QMD if available.
   - If confidence < 0.80 and QMD available → queries QMD before re-classifying

2. **Decomposer** (Sonnet, persistent per user)
   - The single brain. Receives the original user prompt + the Classifier's JSON classification
   - For chat intent → generates conversational response
   - For task intent, small scope → handles directly
   - For task intent, large scope → generates agent plan JSON for Gateway to execute (Phase 2)
   - Has full access: skills, plugins, agents, QMD, background agent MCP, spawn_rule

3. **Pipeline** (routing logic, part of Gateway)
   - Receives user message from Chat Handler
   - Sends to Classifier → gets JSON back
   - Parses and validates JSON (handles parse failures → default to task intent)
   - Passes original prompt + classification to Decomposer
   - Returns Decomposer's event stream

4. **Chat Handler** (NOT an LLM — delivery layer)
   - Currently: Telegram (`handler.py` + `bot.py`)
   - Receives user messages from the channel, passes to Pipeline
   - Receives events from Pipeline, formats and delivers to user
   - Decoupled from LLM logic — opens the door for Slack, Discord, web UI later

### Flow

```
User message (Telegram)
  → Chat Handler (handler.py) receives it
    → Pipeline (routing logic)
      → Classifier (Haiku) → JSON {intent, confidence}
      → Pipeline parses/validates JSON
      → Decomposer (Sonnet) receives: prompt + classification
        ├─ chat → generates conversational response
        ├─ task (small) → handles directly
        └─ task (large) → generates agent plan (Phase 2)
      → Events stream back
    → Chat Handler formats and sends to Telegram
```

### What does NOT exist

- No config.toml toggle (no `[pipeline]` section, no opt-in/opt-out)
- No backward compatibility with the old single-session flow
- No separate Chat Handler LLM session — the Chat Handler is just the delivery layer
- No "system_prompt_prefix" concept — each session (Classifier, Decomposer) has its own system prompt, that's a normal ClaudeSession feature

### Naming

| Component | Role |
|---|---|
| Classifier | Haiku session, outputs JSON classification |
| Decomposer | Sonnet session, the brain — handles everything |
| Pipeline | Routing logic: classify → parse → route (inside Gateway) |
| Chat Handler | Delivery layer (Telegram, channel-agnostic) |

### Phasing

- **Phase 1**: Classifier + Pipeline routing + Decomposer handling chat/tasks directly (no agent spawning)
- **Phase 2**: Decomposer generates agent plans with `depends_on`, Gateway executes dependency graph, Workers + Synthesizer
- **Phase 3**: Synthesizer + hierarchical output aggregation

### Open questions resolved

- Sessions per user: **2** (Classifier + Decomposer)
- Config toggle: **none** — this is the only flow
- Classifier model / confidence threshold: hardcoded defaults, not in config.toml
- handler.py changes: Pipeline replaces ClaudeSession as the object SessionManager returns — handler.py calls the same `.send()` method

