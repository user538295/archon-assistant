# ADR 09 — QMD-Compatible History Format

**Purpose**: Architecture decision record for the Markdown history file format chosen for QMD searchability
**Audience**: Backend engineers
**Status**: Accepted
**Last reviewed**: 2026-02-26
**Next review**: 2026-05-26

---

## Status

Accepted

## Date

2026-02-26

## Context

Archon persists every conversation turn to daily Markdown files at `~/.archon/history/YYYY-MM-DD.md`. These files serve two purposes:

1. **Audit log** — human-readable record of all interactions.
2. **Searchable memory** — Claude can query its own past conversations via the QMD (Queryable Markdown Documents) MCP server, which indexes these files and exposes full-text and semantic search tools.

The format must be compatible with how QMD structures and retrieves documents. QMD expects:
- H1 for the document title (collection-level metadata)
- H2 for top-level sections (primary retrieval unit)
- H3 for sub-sections within a turn
- Contextual Retrieval blockquote (`> User: "..."`) prepended to assistant responses to anchor retrieved chunks to their originating question

Without QMD-compatible structure, retrieved chunks lose context (e.g. a `Response` block with no indication of what the user asked).

## Decision

Write history files in **QMD-compatible Markdown** using a specific H2/H3 structure:

```markdown
# YYYY-MM-DD — Archon Conversations

## HH:MM:SS UTC · User {user_id} · {cwd}

{user_message_text}

### 💭 Thought · HH:MM:SS UTC

{thinking_content}

### 🔧 Tool: {name} [{id}] · HH:MM:SS UTC

```
{tool_input}
```

### 📤 Result [{id}] · HH:MM:SS UTC

```
{tool_output}
```

### ✅ Response · HH:MM:SS UTC

> User: "{first_120_chars_of_question}..."

{response_content}

---

### ❌ Error · HH:MM:SS UTC

{error_message}

---
```

**Key format decisions** (all verified in `archon/ai/history_manager.py`):

- **H2 per user message** — `## HH:MM:SS UTC · User {user_id}` — QMD's primary retrieval unit.
- **H3 per event** — thinking, tool use, tool result, response, error — QMD sub-chunks.
- **Contextual Retrieval blockquote** — `> User: "{q[:120]}..."` prepended to `### ✅ Response` — ensures retrieved response chunks carry the originating question as context.
- **Horizontal rule after responses/errors** — `---` separates turns visually and as a QMD section boundary.
- **UTC timestamps on every heading** — enables time-based search and deduplication.

## Consequences

### Positive

- Claude can search its own history via QMD MCP tools, enabling self-aware continuity across sessions.
- The H2/H3 hierarchy maps naturally to QMD's document model.
- Human-readable as a plain Markdown file even without QMD.
- The Contextual Retrieval blockquote significantly improves the precision of semantic search results.

### Negative

- The format is opinionated — changing the heading level or timestamp format would break QMD indexing.
- Timestamps use UTC (`timezone.utc`), which may differ from the user's local time and make manual reading slightly less intuitive.
- The Contextual Retrieval blockquote is truncated to 120 chars — longer questions lose tail context in retrieved chunks (acceptable trade-off for chunk size).
- QMD integration is optional (disabled by default, enabled via `config.toml [qmd] enabled = true`). The history format was designed for QMD compatibility speculatively, before QMD was integrated.

## Alternatives Considered

### Plain text log (one line per event)

Simple, compact, easily `grep`-able. Rejected because QMD cannot retrieve structured chunks, and the log becomes unreadable for long tool outputs.

### JSON-lines format

Machine-readable, easy to parse. Rejected because QMD requires Markdown, and the history must also be human-readable.

### One file per conversation turn

Each `send()` call writes to a separate file named `{timestamp}_{user_id}.md`. Rejected because QMD collection management becomes complex, and daily files are the natural granularity for `git`-tracked history.

### Standard H1 per turn, no sub-headings

Simpler but causes QMD to retrieve entire turns as single chunks, even when only the thinking or the tool output is relevant to a query.

## Related Documents

- `archon/ai/history_manager.py` — implementation
- `archon/ai/claude_session.py` — integrates `HistoryManager`, passes `qmd_url` to `ClaudeAgentOptions`
- [`Documentation/Architecture/130_data_architecture_and_persistence.md`](../Architecture/130_data_architecture_and_persistence.md) — history file storage and retention
- [`Documentation/Architecture/120_services_and_integration_architecture.md`](../Architecture/120_services_and_integration_architecture.md) — QMD MCP integration
