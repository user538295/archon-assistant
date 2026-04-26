# Bug: Scheduled Agents Fail to Discover Deferred Tool Schemas (ToolSearch Not Called)

| Field                | Value                                                              |
|----------------------|--------------------------------------------------------------------|
| **Status**           | Open                                                               |
| **Severity**         | High                                                               |
| **Priority**         | P1                                                                 |
| **Date**             | 2026-04-18                                                         |
| **Reporter**         | Unknown                                                            |
| **Assignee**         | Unassigned                                                         |
| **Component**        | Archon / Decomposer System Prompt; Agent Runtime / Tool Loading Infrastructure |
| **Affected Versions**| Unknown — to be determined upon investigation                      |
| **Fix Version**      | TBD                                                                |

---

## Symptom

Scheduled agents return a confident false-negative message claiming a tool is unavailable, even when that tool is configured and functional in the environment. The failure is **not silent** — the agent produces an explicit error response — but the message gives no indication that the tool could have been loaded via `ToolSearch`.

**Observed failure:**

```
ai-coding-news-daily (2026-04-18 07:00)
→ "I don't have a web search tool available in this environment."
```

**Concurrent success (same environment, ~15 minutes earlier):**

```
ai-news-daily (2026-04-18 06:45)
→ Delivered full 12-story briefing with real URLs from TechCrunch, The Verge, VentureBeat
```

The `ai-news-daily` success is not yet explained (see [Open Questions](#open-questions)). The same deferred tool architecture applies to both jobs; why one agent succeeded and the other did not is an open question that must be resolved during investigation.

---

## Root Cause (Hypothesis — Unverified)

> **Note:** The contents of `decomposer.md` have not been inspected as part of this report. The following is a working hypothesis based on observed behavior. Investigation must confirm or revise this before a fix is applied.

Archon agents start with a **deferred tool set**. Tool schemas are not loaded by default — tools are listed by name in `<system-reminder>` injections but cannot be called until their schemas are explicitly fetched via `ToolSearch`. `ToolSearch` itself is a base (non-deferred) tool and does not require self-loading before it can be used.

The **decomposer system prompt is believed to not mandate calling `ToolSearch`** before concluding a tool is unavailable. If correct, the failure chain is:

1. Agent inspects directly-callable (non-deferred) tools — finds no search tool with a loadable schema
2. Agent concludes web search is unavailable without consulting the deferred tool list
3. Agent returns a false-negative error message without ever attempting to load the tool via `ToolSearch`

This would be a **capability discovery failure**, not a genuinely missing tool. The tool exists and is configured; the agent never looked for it properly.

Two distinct failure modes must be distinguished:
- **(a) Schema not loaded** — tool is registered in the deferred set but ToolSearch was never called. Fixable by ToolSearch.
- **(b) Tool not configured in environment** — tool is genuinely absent. ToolSearch will not help, and the agent's error is correct.

The observed failure is consistent with (a), but this must be confirmed. The fix described below only addresses (a).

---

## Steps to Reproduce

1. Configure a scheduled job that requires a deferred tool (e.g. `mcp__search__search` or a browser automation tool).
2. Ensure the tool is present in the deferred tool list injected via `<system-reminder>` (confirm via agent log).
3. Trigger the job manually (e.g. via `RemoteTrigger` or the Archon schedule runner).
4. Observe the agent's execution trace — specifically whether `ToolSearch` is called before the agent concludes a tool is unavailable.
5. If `ToolSearch` is never called and the agent returns "tool not available," the bug is confirmed.

---

## Impact

Any scheduled job routed through the decomposer that requires deferred tools (search, browser automation, external APIs, etc.) is potentially vulnerable to false-negative failures. The failure mode is deceptive: the agent produces a confident-sounding error message, giving no indication that the tool could have been loaded.

**Scope:** Limited to agents routed through the decomposer that rely on deferred tools. The `ai-news-daily` success demonstrates that not all agents or all runs are affected — the exact scope depends on factors identified in [Open Questions](#open-questions). Agents that bypass the decomposer (sub-agents, `RemoteTrigger`-invoked agents that use a different system prompt) are outside this fix's scope.

---

## Fix

Update the decomposer system prompt at:

```
archon/ai/prompts/decomposer.md
```

Add a protocol for deferred tool discovery, with these precise semantics:

> **When a tool needed for the current task is not found in the directly callable (non-deferred) tool set, you MUST call `ToolSearch` to check whether it exists as a deferred tool and load its schema.** Do not call `ToolSearch` unconditionally before every action — only when a required tool is absent from the base callable set. Deferred tools are fully functional once loaded; never report a tool as absent without first attempting to load it via `ToolSearch`.
>
> Use the tool's registered MCP name (e.g. `mcp__search__search`) rather than a generic description (e.g. "web search") when querying `ToolSearch`, as keyword matching is exact. If the deferred tool set is large, increase `max_results` beyond the default of 5 to avoid missing relevant tools.

**Scope of this fix:** This patch covers agents that are routed through the decomposer. It does **not** cover sub-agents, `RemoteTrigger`-invoked agents, or any agent that uses a different or no system prompt. Those paths require separate investigation.

**Limitation:** Prompt-based instructions are best-effort. An LLM agent may still skip `ToolSearch` in complex reasoning chains — the same limitation that applies to per-job prompt patching. This fix reduces the probability of the failure but does not eliminate it deterministically. See [Future Improvement](#future-improvement) for a deterministic solution.

---

## Rejected Anti-Pattern

**Per-job prompt patching** — adding tool-discovery instructions to individual job prompts — was considered and explicitly rejected:

- Does not scale: every new job must be patched independently
- Creates ongoing maintenance debt
- Does not fix existing jobs that have not been patched
- Subject to the same LLM-compliance limitation as any prompt-based instruction

Note: the fragility concern (agents ignoring instructions mid-reasoning) applies equally to the decomposer prompt fix. Per-job patching is rejected primarily because it does not scale and creates maintenance burden, not because the decomposer approach is fundamentally more reliable.

---

## Future Improvement

A deterministic, prompt-independent solution would be to **auto-load deferred tool schemas at agent startup in infrastructure code**, before the agent begins reasoning. This would:

- Guarantee all registered tools are callable without agent cooperation
- Eliminate the class of capability-discovery failures entirely
- Remove the need for prompt-level workarounds

This approach was not implemented as the immediate fix due to scope, but should be tracked as a follow-on improvement. The current prompt fix is a pragmatic short-term mitigation; the infrastructure-level fix is the correct long-term solution.

---

## Verification

After applying the fix to `decomposer.md`:

**Happy path — confirm the fix works:**
1. Re-run `ai-coding-news-daily` manually (or wait for next scheduled run at 07:00)
2. Confirm the agent calls `ToolSearch` in its execution trace when a deferred tool is needed
3. Confirm it successfully loads and uses the search tool
4. Confirm the briefing output contains real URLs and current news stories

**Regression — confirm existing successes are unaffected:**
5. Re-run `ai-news-daily` after the change and confirm it still delivers a correct briefing

**Negative case — confirm genuine absences still fail cleanly:**
6. Run an agent in an environment where a tool is genuinely not configured (not in the deferred list)
7. Confirm the agent calls `ToolSearch`, finds nothing, and returns a clear "tool not available" error — not confusion or a retry loop

**Deployment note:**
- A daemon restart may be required for changes to `decomposer.md` to propagate. Confirm the restart procedure before testing.

---

## Open Questions

- **Why did `ai-news-daily` succeed at 06:45 when `ai-coding-news-daily` failed at 07:00?** The 15-minute gap and concurrent success are unexplained. Possible hypotheses: transient failure (cold-start, cache miss, rate limiting), a deployment or config change between 06:45–07:00, different decomposer routing, or a difference in how each job's prompt triggers tool usage. This must be investigated — the root cause hypothesis above may be wrong or incomplete.
- **Does `decomposer.md` actually lack the `ToolSearch` instruction?** The file has not been read as part of this report. Confirm before writing the fix.
- **Are sub-agents and `RemoteTrigger`-invoked agents affected?** Requires tracing which system prompt (if any) they receive.
- **Is this reproducible deterministically or intermittent?** If intermittent, prompt changes alone will not be a reliable fix.

---

## References

- Job config: `ai-coding-news-daily` (path TBD)
- Job config: `ai-news-daily` (path TBD)
- Decomposer prompt: `archon/ai/prompts/decomposer.md` (to be read during investigation)
