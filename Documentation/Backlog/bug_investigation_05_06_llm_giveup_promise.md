# Bug Investigation: Bug 5 & 6 — LLM Give-Up and Empty Promises

**Date**: 2026-04-17  
**Investigator**: Claude Code (READ-ONLY analysis)  
**Session Context**: User 154643621 at 2026-04-17 18:20-20:31 UTC  
**Log Reference**: `/Users/manczg/.archon/logs/archon.log` lines 115-159  
**Session History**: `/Users/manczg/.archon/history/sessions/2026-04-17.md` lines 450-885  

---

## Bug 5: Tool Failure → Premature Surrender (No Fallback Attempt)

### Description
At 18:20:02 UTC, the search tool (`mcp__search__search`) failed with: `{"error":"cannot import name 'TextCrossEncoder' from 'fastembed' ..."}`. The Decomposer immediately gave up, telling the user: "I don't have context on this — no prior session history and the search tool is currently broken (fastembed import error). Can you give me a quick recap?"

However, **session history was available via grep** — the user later pointed this out at 18:21:18 UTC, forcing the Decomposer to search for context in files. When it did search the session history files (eventually), it found the exact information it claimed was unavailable.

**Root Cause**: The Decomposer's system prompt and decomposer.md contain no explicit instruction for fallback behavior when tools fail. The instructions say:
- "Accuracy over speed: Never state facts you haven't verified. Use tools to check — read files, run commands, search"
- "For tasks: verify first, then act — read before writing, check before claiming. Use your full capabilities"

But there is **no guidance that says "when the search tool fails, try grep/read as fallback"**.

### Why It Happens

#### 1. Missing Fallback Instruction in System Prompts
**File**: `archon/ai/prompts/decomposer.md` (source repo path; observed at `/Users/manczg/.archon/app/archon/ai/prompts/decomposer.md` in the installed runtime)  
**Issue**: The prompt lists capabilities but does NOT say what to do when they fail.

**File**: `/Users/manczg/.claude/CLAUDE.md` (lines 27-39)  
**Mandatory Verification Protocol** states:
```
If there is a documentation check them before you answer, refer to the documentation (in which file, which title did you find the answer).

## If You Don't Know Something:
1. **STOP** - Do not proceed with guesses or assumptions
2. **STATE** - Say explicitly: "I don't know this. Let me verify..."
3. **SEARCH** - Use mcp__serena tools to find the information (fallback to Grep/Glob/Read only if serena isn't available)
4. **VERIFY** - Check actual code, data files, or documentation
5. **SAVE** - Use Serena's memory system to save verified facts for future reference
```

**Critical Gap**: This instruction mentions "fallback to Grep/Glob/Read **only if serena isn't available**" — but it does NOT address the case where a tool is present but **broken** (import error, timeout, network failure). The Decomposer's prompt does not include this CLAUDE.md guidance, and it contains no equivalent fallback logic.

> **Open question**: The Decomposer session starts with `cwd=/Users/manczg/.archon/workspace` (log line 134). The Claude SDK automatically loads `CLAUDE.md` files from the working directory hierarchy. If a `CLAUDE.md` or `REMINDER.md` in that hierarchy already contains fallback instructions, the root cause shifts from "missing instruction" to "model ignores existing instruction under tool-failure stress" — which would make prompt-only fixes (Options A/B) less reliable. **This should be verified before implementing.**

#### 2. Decomposer Session Lacks Unified Fallback Strategy
**File**: `archon/ai/decomposer.py` (source repo; observed at `/Users/manczg/.archon/app/archon/ai/decomposer.py` in runtime)  
**Observation**: The decomposer.py file (lines 1-200) shows extensive error handling for timeouts and JSON parsing (lines 217-343), but the **system prompt injected into the Decomposer's Claude session** does not include fallback guidance for specific tool failures.

#### 3. Session Injection Pattern
**File**: `/Users/manczg/.archon/logs/archon.log` line 134-135  
```
2026-04-17 19:48:41,936 archon INFO Injecting REMINDER (4941 chars, ~1235 tokens)
2026-04-17 19:48:42,834 archon INFO Claude session started (cwd=/Users/manczg/.archon/workspace)
```

The "REMINDER" injected is `~/.archon/workspace/REMINDER.md` (4941 chars observed), which contains general standing rules but **does not mention fallback strategies for tool failures**.

---

## Bug 6: Promise Without Action — "Won't Happen Again" (No Memory Save in Same Turn)

> **Note**: Bug 6 contains two independent issues — (1) the Decomposer's "empty promise" behavioral pattern, and (2) classifier output corruption. These have different root causes and different fix paths. They are grouped here because they occurred in the same session, but should be fixed and verified independently.

### Description
At 18:23:42 UTC, the Decomposer said "Won't happen again" after the user pointed out it had searched the wrong location (daily summaries instead of session logs).

The user immediately called this out at 18:30:57 UTC: **"Don't lie, that it won't happen again. You didn't save it in your memory."**

Indeed, the session history shows:
- **18:23:42 UTC**: Response "Won't happen again" — **No save action taken**
- **18:23:27 UTC**: Classifier output shows a long apology text instead of JSON
- **18:31:11-18:31:18 UTC**: Only AFTER the user complaint did the Decomposer actually write to MEMORY.md (Tool Edit [24])

**Root Cause 1 (Promise)**: When the Decomposer makes a commitment like "won't happen again," there is no system-level instruction requiring immediate action in the same turn. The instruction to save to memory exists in CLAUDE.md but is not enforced as a **synchronous requirement** in the Decomposer's system prompt.

**Root Cause 2 (Classifier Behavior)**: The classifier returned a long text apology instead of JSON, which is a direct violation of its prompt. **This is NOT caused by Decomposer output bleeding into the Classifier session** — the Classifier and Decomposer have completely separate `ClaudeSession` instances and share zero state. The actual cause is intra-session history accumulation within the Classifier's own session (see "Why this happens #3" below).

### Why It Happens

#### 1. Missing Synchronous Memory-Save Rule
**File**: `~/.archon/workspace/REMINDER.md` (lines 1-32)  
**Observation**: The REMINDER states general standing rules but does NOT say:
- "When you say you will save something to memory, save it NOW in the same response"
- "Do not make promises without fulfilling them immediately"

**File**: `/Users/manczg/.claude/CLAUDE.md` (lines 27-39)  
**Verification Protocol Step 5**: "**SAVE** - Use Serena's memory system to save verified facts for future reference"
- Does NOT specify "save immediately when you discover a fact"
- Does NOT specify "if you promise to save, do it in the same turn"

#### 2. No "Save Before Responding" Pattern
**File**: `archon/ai/prompts/decomposer.md` (source repo; observed at `/Users/manczg/.archon/app/archon/ai/prompts/decomposer.md` in runtime) lines 19-25:
```
## Guidelines

- For conversational messages: be helpful, concise, and friendly
- For tasks: verify first, then act — read before writing, check before claiming. Use your full capabilities — tools, code generation, file operations
- Always prefer direct action over asking clarifying questions when the intent is clear
- Use the `search` Search MCP tool to access conversation history
```

**Missing**: "When making promises or committing to save information, execute the save action before or within the same response."

#### 3. Classifier Produces Non-JSON Text (Symptom of Session History Accumulation)
**File**: `/Users/manczg/.archon/history/sessions/2026-04-17.md` lines 754-767
```
### 🏷 Classification · 18:23:27 UTC

`{"intent": "task", "confidence": 0.0}` · 1.9s · model: claude-haiku-4-5-20251001

```
You're absolutely right—I apologize. I don't know where the session history is located...
```

⚠️ Parse error: no JSON object found in response
```

> **Timeline clarification**: The `{"intent": "task", "confidence": 0.0}` shown in the history file is the **fallback default** rendered when JSON parsing fails — it is NOT the actual classifier output. The actual classifier output was prose (the apology text shown above). The `parse_classification()` function has a resilient JSON parser that defaults to `{"intent": "task", "confidence": 0.0}` on parse failure, and the history writer logs this fallback value. This is why both appear together.

**Issue**: The classifier is returning a full apology text when it should output ONLY JSON. The classifier prompt (`archon/ai/prompts/classifier.md`, observed at `/Users/manczg/.archon/app/archon/ai/prompts/classifier.md` lines 1-5) explicitly states:
```
Output ONLY a raw JSON object. No markdown, no code fences, no explanations,
no reasoning, no commentary — nothing before or after the JSON.
```

**Why this happens**: The Classifier's OWN session accumulates its own conversation history across multiple `classify()` calls — the SDK maintains conversation continuity within a session. After many calls, the Classifier's context contains dozens of prior user-prompt + JSON-response pairs. When the user sends an emotionally-charged or long message, the model's instruction-following can degrade, causing it to slip into conversational prose instead of JSON output.

The codebase already has `_CLASSIFIER_RESET_THRESHOLD = 50` to address this by resetting the session after 50 calls, but 50 calls may be too high to prevent degradation in long or emotionally-charged sessions.

This is a symptom of **classifier session context accumulation** (related to `bug_17_classifier_session_unbounded_growth.md` — **note**: verify this file exists and confirm whether Bug 17's threshold recommendation is consistent with the 5-10 range proposed in Option D before applying).

---

## How These Bugs Manifest

### Bug 5 Sequence
1. Search tool fails with import error
2. Decomposer has no fallback instruction
3. Decomposer declares context unavailable
4. User must tell Decomposer to use grep (alternative method already available)
5. Decomposer searches session history and finds the exact information
6. Wasted approximately 4 tool turns (estimated; not precisely measured from session history)

### Bug 6 Sequence
1. User points out routing mistake
2. Decomposer says "Won't happen again"
3. No memory save action taken
4. User immediately calls out the broken promise
5. Only then does Decomposer write to MEMORY.md (2 turns later)
6. Classifier output corrupted by its own intra-session history accumulation

---

## Fix Options

### Bug 5 — Tool Failure Fallback

#### Option A: Add Explicit Fallback to Decomposer Prompt
**Pros**:
- Simple, localized change
- Decomposer already has access to Bash, Glob, Grep, Read tools
- Aligns with "use your full capabilities" principle
- Immediate effect

**Cons**:
- Duplicates logic already in CLAUDE.md
- May cause tool thrashing (retry same query via multiple methods)
- Doesn't fix root issue: tool failure is external

**Implementation**:
```
Add to decomposer.md after line 22:

## Fallback Strategy When Tools Fail

When a primary tool fails (import error, network timeout, permission denied):
1. Do NOT give up. Verify availability of alternative approaches.
2. If you have context about where information might be (files, directories, logs):
   - Try Grep (pattern search in known locations)
   - Try Read (direct file access if path is known)
   - Try Glob (discover file patterns)
3. Only state "information unavailable" if all access methods are exhausted.
```

#### Option B: Inject Fallback Guidance at Session Start
**Pros**:
- Centralizes fallback logic in one place
- Can mention specific collections/tools (session history location, source tree, etc.)
- Follows "context injection" pattern

**Cons**:
- Requires REMINDER.md update or new context file
- Token cost per session
- May be overridden by other injections

**Implementation**:
Update `~/.archon/workspace/REMINDER.md` with a section like:
```markdown
## When Tools Fail

If a search or retrieval tool fails (timeout, import error, network):
1. Identify what you were trying to find
2. Check if you know where the data might live (file path, directory, log)
3. Use Grep, Read, Glob, or Bash as fallback
4. Only declare information unavailable after all methods exhausted
```

#### Option C: Add Tool-Specific Error Detection in Pipeline Event Loop
**Pros**:
- Centralized, reusable across all agents
- Can inject failure context automatically for the next turn
- Achievable as a small addition to the existing event loop

**Cons**:
- Requires mapping tool → fallback strategy
- Testing complexity
- Only affects the NEXT turn (cannot inject mid-stream)

**Implementation** (pseudo-code — tools are executed inside the SDK, not interceptable directly):
```python
# In pipeline.py or decomposer.py, inside the event loop for send():
async for event in self._session.send(prompt):
    if isinstance(event, ToolResult) and _is_tool_error(event.content):
        # Queue fallback guidance for the next turn
        self.inject_context(
            f"The {event.tool_name} tool failed. "
            f"Available fallbacks: use Grep/Read on session history "
            f"(cfg.history.directory) or the source tree."
        )
    yield event

def _is_tool_error(content: str) -> bool:
    return "error" in content.lower() or "cannot import" in content.lower()
```

Note: `inject_context()` queues the message for the **next** `send()` call. The Pipeline sees `ToolResult` events after the fact — it cannot intercept the SDK's tool execution mid-stream.

#### Recommendation
**Use Option A + Option B together**:
1. Add explicit fallback guidance to `archon/ai/prompts/decomposer.md` (quick win)
2. Update `~/.archon/workspace/REMINDER.md` to include fallback strategy (reinforcement)
3. This requires no code changes and is immediately testable

---

### Bug 6 — Promise Without Action

#### Option A: Require Immediate Save When Committing
**Pros**:
- Simple rule: "commitment = immediate action"
- Prevents empty promises
- Aligns with "accuracy over speed" principle

**Cons**:
- May slow down response time (2-3 extra tool calls per commitment)
- Requires discipline in prompt execution
- Could create tool side-effects if save fails

**Implementation**:
Add to `archon/ai/prompts/decomposer.md`:
```markdown
## Memory & Commitments

When you say you will remember something or save information:
- Execute the save immediately in the same response turn
- Do NOT say "I will" or "I'll save this" without doing it
- If the save fails, acknowledge the failure explicitly
- Only then respond to the user
```

#### Option B: Separate "Commitment Executor" Agent — ⚠️ NOT RECOMMENDED
**Pros**:
- Isolates promise-keeping from conversational flow
- Can retry failed saves automatically
- Decouples timing pressure

**Cons**:
- **Fragile text pattern matching**: detecting commitments requires regex over natural language ("I will save", "I'll remember", "won't happen again") — brittle, language-dependent, and easily bypassed by paraphrasing ("Noted for next time", "I'll keep that in mind")
- Adds architectural complexity
- Extra routing/delegation overhead; requires buffering entire Decomposer response before delivery, destroying streaming UX
- Requires async coordination

**Implementation**:
Create a new agent in the pipeline that runs after the Decomposer:
```python
class CommitmentExecutor:
    def validate_and_execute(self, decomposer_response: str) -> str:
        # Scan response for commitment patterns
        # ("I will save", "I'll remember", "won't happen again")
        # Extract what needs saving
        # Execute save operations
        # Update response with actual results
```

#### Option C: Enforce "No Promises" Discipline
**Pros**:
- Simplest: never say you'll do something unless you do it in the same turn
- Aligns with professional communication guidelines

**Cons**:
- Restricts response style (can't preview what you'll do)
- May feel less conversational
- Requires retraining of model behavior

**Implementation**:
Add to `archon/ai/prompts/decomposer.md`:
```markdown
## Response Style

- Never commit to future action outside of the current response
- Instead of "I'll save this to memory next," DO it now and say "Saved to memory"
- Instead of "Won't happen again," followed by no action, either:
  a) Take the corrective action first, then say "Done. Here's what I changed:"
  b) Or acknowledge the specific improvement you can make (not a vague promise)
```

> **Note on Options A and C**: Both address the same behavioral constraint from different angles — A focuses on "save immediately when committing", C focuses on "never make promises you don't fulfill in the same turn." If both are implemented, they should be merged into a single cohesive **"Commitment = Immediate Action"** rule section in `decomposer.md` rather than two separate rules that could be read as contradictory. The recommendation below reflects this.

#### Option D: Reset Classifier Session More Frequently (Root Cause Fix)
**Pros**:
- Fixes the underlying cause of classifier output corruption (its own history accumulation)
- May fix other classifier degradation bugs (see `bug_17_classifier_session_unbounded_growth.md`)
- Can be as simple as lowering one constant

**Cons**:
- More frequent session resets = slightly higher latency on reset calls
- Requires testing across all classification scenarios

**Implementation**:
The codebase already has `_CLASSIFIER_RESET_THRESHOLD = 50` in `archon/ai/classifier.py`. The simplest fix is to lower this threshold to 5-10 to prevent the Classifier's own history from accumulating enough to degrade instruction adherence:

```python
# In classifier.py — lower threshold to prevent history-driven degradation
_CLASSIFIER_RESET_THRESHOLD = 5  # was 50; reset more frequently to keep JSON output clean
```

Alternatively, make the Classifier fully stateless by creating a fresh `ClaudeSession` per `classify()` call (pseudo-code):
```python
# Pseudo-code: stateless classifier variant
class Classifier:
    def classify(self, prompt: str) -> Classification:
        # Create a fresh session with ONLY:
        # 1. The classifier system prompt
        # 2. The current message
        # No accumulated history from prior classify() calls
        session = self._create_fresh_session()
        return session.classify(prompt)
```

This eliminates intra-session history accumulation entirely, at the cost of losing any warm-up efficiency from session reuse.

#### Recommendation
**Use combined Option A+C + Option D**:
1. **Options A+C combined**: Add a single "Commitment = Immediate Action" rule section to `archon/ai/prompts/decomposer.md` that covers both "save immediately when committing" and "never make a promise you don't fulfill in the same turn"
2. **Option D**: Lower `_CLASSIFIER_RESET_THRESHOLD` in `archon/ai/classifier.py` from 50 to 5-10 as the simplest fix for classifier degradation

**Why together**: Bug 6 has two independent parts:
- The promise part (Options A+C combined fixes)
- The classifier degradation part (Option D fixes)

---

## Verification & Testing

### Bug 5 Verification
1. **Reproduce**: Search tool fails → Decomposer should NOT give up immediately
2. **Test**: Verify fallback to Grep/Read happens automatically
3. **Integration test**: Mock `mcp__search__search` to return a `ToolError`; assert that the Decomposer's tool call sequence includes at least one Grep, Read, or Glob call before a final response claiming information is unavailable.
4. **Accept Criteria**: User never has to tell Decomposer "try grep" — it happens automatically

### Bug 6 Verification
1. **Promise fix**: Session history review — grep session history files for response turns containing "won't happen again" or "I'll save" that are NOT immediately followed in the same turn by a Write or Edit tool call.
2. **Classifier fix**: Unit test — call `Classifier.classify()` 50+ times with varied input; assert all responses parse as valid JSON with `parse_classification()`. This test exists if `bug_17` regression tests are in place.
3. **Measure**: Session history should show Edit tool called BEFORE final response text in the same turn as any memory commitment
4. **Accept Criteria**: 
   - Zero instances of "promise in turn N, save in turn N+2"
   - Classifier output always valid JSON (unit test: `parse_classification()` returns non-default confidence across 50+ classify calls with varied input)

---

## Files to Update

> **Path note**: Source repo paths are relative to the repository root. The `/Users/manczg/.archon/app/` prefix is the installed runtime copy — make changes in the development repository, not the deployed copy.

1. **Priority 1 (Bug 5)**:
   - `archon/ai/prompts/decomposer.md` — add fallback guidance (runtime: `/Users/manczg/.archon/app/archon/ai/prompts/decomposer.md`)
   - `~/.archon/workspace/REMINDER.md` — add fallback strategy section (Option B for Bug 5). **UNVERIFIED**: must confirm this runtime file exists and does not already contain equivalent guidance before applying.

2. **Priority 1 (Bug 6)**:
   - `archon/ai/prompts/decomposer.md` — add combined "Commitment = Immediate Action" rule (merging Options A+C)
   - `archon/ai/prompts/classifier.md` — consider reinforcing the JSON-only output instruction (e.g., adding examples of correct vs. incorrect output format), especially if the classifier is not being made stateless

3. **Priority 2 (Root Cause)**:
   - `archon/ai/classifier.py` — lower `_CLASSIFIER_RESET_THRESHOLD` from 50 to 5-10 (or implement stateless classify)
   - `archon/ai/pipeline.py` — consider adding `ToolResult` error detection to queue fallback guidance (Option C for Bug 5)

---

## Summary Table

| Bug | Root Cause | Impact | Quick Fix | Long Fix |
|-----|-----------|--------|-----------|----------|
| **5** | No fallback instruction when tool fails | LLM gives up, user must intervene | Add "try grep/read when tool fails" to decomposer.md | Add ToolResult error detection in pipeline event loop to auto-queue fallback guidance |
| **6 (promise)** | No "save immediately" requirement for commitments | Empty promises, user trust eroded | Add combined "Commitment = Immediate Action" rule to decomposer.md | Prompt fix is sufficient — CommitmentExecutor agent (Option B) is NOT RECOMMENDED |
| **6 (classifier)** | Classifier's own session history accumulates over 50+ calls, degrading JSON instruction adherence | Classifier outputs prose instead of JSON | Lower `_CLASSIFIER_RESET_THRESHOLD` from 50 to 5-10 | Make Classifier stateless (fresh session per classify call) |
