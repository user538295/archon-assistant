**Purpose**: Completed stories for Epic 7 — chat history persistence in QMD-compatible Markdown files
**Audience**: All developers
**Status**: Completed
**Last reviewed**: 2026-02-26
**Next review**: 2027-02-26

# Epic 7: Memory & History

## Stories

### S7.1: Chat history persistence (QMD-compatible)

**Status**: Completed ✅
**Priority**: Medium
**Estimated effort**: L

**User Story**: As a developer, I want all conversation turns persisted to daily Markdown files in `~/.archon/history/`, so that Claude Code can later search its own past conversations as semantic memory via QMD's MCP server.

#### Acceptance Criteria

- `HistoryManager` creates `~/.archon/history/YYYY-MM-DD.md` with correct header on first write per day
- Header is not duplicated on subsequent writes to the same file
- File rotates to a new `.md` when the date changes
- Directory is created if missing
- `record_user_message(user_id, text, cwd)` writes `## HH:MM:SS UTC · User {id} · {cwd}` section + body
- Each event type renders the correct H3 subsection (`ThinkingResult` → `### 💭 Thought · HH:MM`)
- `Response` includes contextual retrieval blockquote (user's last question, truncated at 120 chars)
- `Response` and `ErrorEvent` end with `\n\n---\n`
- `HistoryConfig` defaults: `enabled=True`, `directory="~/.archon/history"`; overridable via `[history]` in `config.toml`
- `history_manager=None` → no crash (history is optional)
- All tests pass; ≥85% total coverage; `mypy` clean

#### Technical Notes

QMD exposes `qmd mcp` tools (`qmd_deep_search`, `qmd_vector_search`). Once history files exist, a future setup step (`qmd collection add ~/.archon/history --name archon`) + `qmd mcp --daemon` lets Claude Code call those tools directly to retrieve past context — no retrieval code needed inside Archon itself.

**Format — daily `.md` file (`~/.archon/history/YYYY-MM-DD.md`):**
- `# YYYY-MM-DD — Archon Conversations` — one-time file header (QMD uses title for chunk prefix)
- `## HH:MM:SS UTC · User {id} · {cwd}` — H2 = one chunk boundary per conversation turn
- `### {emoji} {type} · HH:MM:SS` — H3 per event within a turn; timestamps enable BM25 temporal queries
- `### ✅ Response` repeats the user question as a blockquote (Contextual Retrieval — reduces retrieval failure 49% per Anthropic research)
- `### ✅ Response` and `### ❌ Error` end with `\n\n---\n` (turn separator)
- Tool I/O in fenced code blocks (prevents code-token noise in prose embeddings); `ThinkingResult` produces `### 💭 Thought · HH:MM`

**New files:**
- `archon/ai/history_manager.py` — `HistoryManager(directory)` with `record_user_message(user_id, text, cwd)` and `record_event(user_id, event)`
- `tests/ai/test_history_manager.py` — 20 TDD tests

**Modified files:**
- `archon/config/loader.py` — `HistoryConfig(enabled, directory)` + `Config.history` field + `[history]` parsing
- `archon/chat/handler.py` — `cwd` and `history_manager` params; calls `record_user_message` + `record_event`
- `archon/gateway/gateway.py` — wires `HistoryManager` into dispatcher when enabled
- `config.toml.example` — `[history]` section documented

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)
- [Component Catalog](../Architecture/110_component_catalog_and_layer_breakdown.md)
