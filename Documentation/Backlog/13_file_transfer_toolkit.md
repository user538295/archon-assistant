# 13 — File Transfer Toolkit Tools

**Purpose**: Extend the Archon MCP toolkit with `send_file` and `list_attachments` tools so background agents (and router sessions) can send files to Telegram users and discover user-uploaded attachments.
**Audience**: Archon developers, background agents
**Status**: Planned
**Priority**: P2
**Last reviewed**: 2026-03-20
**Next review**: 2026-06-20

---

## Background

Background agents and router sessions can call toolkit tools (`archon_status`, `send_notification`, etc.) via MCP. However, there is no way for an agent to:

1. **Send a file** to the Telegram user (e.g., a generated report, exported data, an image).
2. **Discover files** the user has previously uploaded (stored by `AttachmentStore` in date-based directories).

Agents can already read files on disk via SDK tools (Read, Glob), but they cannot push files to Telegram or list the attachment store contents without knowing exact paths.

### Existing infrastructure to reuse

| Component | Location | Relevance |
|---|---|---|
| `AttachmentStore` | `archon/ai/attachment_store.py` | Date-based file storage with sanitization, collision handling, cleanup |
| `AttachmentInfo` | `archon/ai/attachment_types.py` | Metadata dataclass (path, mime, size, dimensions) |
| `detect_mime_type()` | `archon/ai/attachment_types.py` | MIME detection from filename/extension |
| `format_file_size()` | `archon/ai/attachment_types.py` | Human-readable file sizes |
| `check_file_size()` | `archon/ai/attachment_types.py` | Size validation (20 MB default) |
| `FileHandler` | `archon/chat/file_handler.py` | Telegram→Archon download pipeline (inbound direction) |
| `send_notification` handler | `archon/ai/archon_toolkit.py` | Pattern for bot-based tools with rate limiting |
| `ArchonToolkit` | `archon/ai/archon_toolkit.py` | Tool registry, dispatch, event callbacks |
| `BG_AGENT_ALLOWED_TOOLS` | `archon/gateway/gateway.py:584` | Per-route tool filtering for background agents |

---

## Tool designs

### `send_file`

Sends a file from the local filesystem to a Telegram user via `bot.send_document()`.

**Parameters:**
- `user_id` (integer, required) — Target Telegram user ID
- `file_path` (string, required) — Absolute or CWD-relative path to the file
- `caption` (string, optional) — Document caption (max 1024 chars, Telegram limit)

**Security constraints:**
- `user_id` must be in `config.access.allowed_user_ids` — agents cannot send files to arbitrary Telegram accounts.
- Resolved path must be within the working directory (`config.session.working_directory`) OR the attachment store base directory — no arbitrary filesystem access.
- Symlinks are resolved before the containment check (prevents symlink traversal).
- Caption is HTML-escaped via `html.escape()` before sending — the bot has `parse_mode=HTML` globally (`archon/chat/bot.py`), so raw `<>&` in captions would break or be interpreted as markup.

**Limits:**
- File size: 50 MB (Telegram `send_document` limit). Validated before upload — Telegram returns HTTP 400 for oversized files, but pre-validation avoids the wasted upload.
- Rate limit: 10 s per `user_id` (separate from `send_notification` rate limiter).

**Return:** `"File sent: {filename} ({size})"` on success, error string on failure.

**aiogram API:**
```python
import html
from aiogram.types import FSInputFile
document = FSInputFile(resolved_path)
safe_caption = html.escape(caption) if caption else None
await self._bot.send_document(chat_id=user_id, document=document, caption=safe_caption)
```

### `list_attachments`

Lists files in the attachment store with optional filtering.

**Parameters:**
- `date` (string, optional) — ISO date (`YYYY-MM-DD`) to filter by. If omitted, lists all dates.
- `mime_pattern` (string, optional) — Prefix filter (e.g., `"image/"`, `"text/"`). If omitted, lists all types.
- `limit` (integer, optional) — Max entries to return (default 50, max 200).

**Return:** JSON array of objects:
```json
[
  {
    "filename": "report.csv",
    "path": "2026-03-20/report.csv",
    "abs_path": "/home/user/.archon/workspace/attachments/2026-03-20/report.csv",
    "size_bytes": 1234,
    "size_human": "1.2 KB",
    "mime_type": "text/csv",
    "date": "2026-03-20",
    "mtime": "2026-03-20T14:30:00"
  }
]
```

**Security:** Read-only. Only lists files within the attachment store base directory. Symlink directories are skipped (same as `cleanup()`).

---

## Implementation plan

### Task 1: `AttachmentStore.list_entries()` method

> Adds a list capability to the existing store — needed by the `list_attachments` handler.

- [x] **1.1** Write unit tests for `list_entries()`
  - **Deps:** none
  - **File:** `tests/ai/test_attachment_store.py`
  - **Tests:**
    - `test_list_entries_empty_store` — returns empty list when no files
    - `test_list_entries_single_file` — returns correct metadata (filename, path, size, mime, date, mtime)
    - `test_list_entries_multiple_dates` — files across `2026-03-19/` and `2026-03-20/` both listed
    - `test_list_entries_date_filter` — only files from specified date returned
    - `test_list_entries_date_filter_no_match` — returns empty when date has no files
    - `test_list_entries_mime_prefix_filter` — `"image/"` matches `image/png` but not `text/csv`
    - `test_list_entries_combined_filters` — date + mime together
    - `test_list_entries_limit` — respects limit parameter, returns newest first
    - `test_list_entries_skips_symlinks` — symlinked files/directories are excluded
    - `test_list_entries_skips_non_date_dirs` — directories not matching `YYYY-MM-DD` ignored
    - `test_list_entries_nonexistent_base` — returns empty list when base_dir doesn't exist

- [x] **1.2** Implement `list_entries()` method
  - **Deps:** 1.1
  - **File:** `archon/ai/attachment_store.py`
  - **Signature:** `def list_entries(self, *, date: str | None = None, mime_prefix: str | None = None, limit: int = 50) -> list[dict[str, Any]]`
  - **Logic:** iterate date-dirs matching `_DATE_DIR_RE` pattern (extract shared constant from `cleanup()`), stat each file, detect MIME via `detect_mime_type()`, apply filters, sort by mtime descending (filename as tie-breaker), truncate at `limit`
  - **Returns:** list of dicts with keys: `filename`, `path` (relative), `abs_path`, `size_bytes`, `size_human`, `mime_type`, `date`, `mtime` (ISO format)

- [x] **1.3** Verify tests pass: `uv run pytest tests/ai/test_attachment_store.py -v`
  - **Deps:** 1.2

---

### Task 2: `list_attachments` toolkit tool

> Registers the tool in ArchonToolkit, wires the attachment store dependency. Includes all test levels.

- [x] **2.1** Add `attachment_store` parameter to `ArchonToolkit.__init__()`
  - **Deps:** 1.3
  - **File:** `archon/ai/archon_toolkit.py`
  - **Details:** add `attachment_store: Any = None` to constructor kwargs → `self._attachment_store`. Constructor-only — the gateway creates the store before the toolkit, so `set_late_deps()` is not needed for this dependency.

- [x] **2.2** Define `_LIST_ATTACHMENTS_SCHEMA` constant
  - **Deps:** 2.1
  - **File:** `archon/ai/archon_toolkit.py`
  - **Details:** MCP tool schema dict with `name`, `description`, `inputSchema` (properties: `date`, `mime_pattern`, `limit`)

- [x] **2.3** Write unit tests for `_handle_list_attachments`
  - **Deps:** 2.2
  - **File:** `tests/ai/test_archon_toolkit_files.py` (new file — follows `test_archon_toolkit_comms.py` pattern)
  - **Unit tests:**
    - `test_list_attachments_success` — returns JSON array with correct structure
    - `test_list_attachments_date_filter` — date param passed through to store
    - `test_list_attachments_mime_filter` — mime_pattern param passed through
    - `test_list_attachments_limit_default` — default limit is 50
    - `test_list_attachments_limit_clamped` — limit > 200 clamped to 200
    - `test_list_attachments_empty` — returns `"[]"` when no files
    - `test_list_attachments_missing_store` — raises RuntimeError when attachment_store is None

- [x] **2.4** Implement `_handle_list_attachments` handler + register tool
  - **Deps:** 2.3
  - **File:** `archon/ai/archon_toolkit.py`
  - **Signature:** `async def _handle_list_attachments(self, arguments: dict[str, Any], *, user_id: int | None = None) -> str`
  - **Logic:** validate `self._attachment_store` is set, extract/validate args, delegate to `await asyncio.to_thread(self._attachment_store.list_entries, ...)` (filesystem I/O must not block the event loop), return `json.dumps(result)`
  - **Registration:** `self.register_tool("list_attachments", _LIST_ATTACHMENTS_SCHEMA, self._handle_list_attachments)`

- [x] **2.5** Write integration tests (MCP server)
  - **Deps:** 2.4
  - **File:** `tests/ai/test_archon_toolkit_files.py`
  - **Integration tests:**
    - `test_list_attachments_via_mcp` — callable via ArchonRouterMCPServer TestClient when in allowed_tools
    - `test_list_attachments_blocked_when_not_allowed` — not exposed when not in allowed_tools

- [x] **2.6** Write E2E test (real AttachmentStore)
  - **Deps:** 2.4
  - **File:** `tests/ai/test_archon_toolkit_files.py`
  - **E2E test:**
    - `test_list_attachments_e2e_real_store` — create real AttachmentStore in tmp_path, save files, call toolkit, verify JSON matches actual files on disk

- [x] **2.7** Write live E2E test (real background agent via MCP)
  - **Deps:** 2.6
  - **File:** `tests/ai/test_archon_toolkit_files.py`
  - **Markers:** `pytest.mark.live`, skip if `claude` binary not found
  - **Live E2E test:** `test_list_attachments_live_agent`
    1. Create real `AttachmentStore` in tmp_path, save a test file
    2. Create real `ArchonToolkit` with store
    3. Create real `ArchonRouterMCPServer` with toolkit + allowed_tools including `list_attachments`
    4. Spawn background agent with task: "Call the list_attachments tool, then reply with the filename you found"
    5. Wait for agent completion, verify agent log contains `list_attachments` tool call
    6. Verify agent response references the test filename

- [x] **2.8** Verify all tests pass: `uv run pytest tests/ai/test_archon_toolkit_files.py tests/ai/test_attachment_store.py -v`
  - **Deps:** 2.7

---

### Task 3: `send_file` toolkit tool

> Registers the tool with path security, size validation, rate limiting, and Telegram upload. Includes all test levels.

- [ ] **3.1** Define `_SEND_FILE_SCHEMA` constant
  - **Deps:** 2.1 (needs `attachment_store` param in toolkit for path validation)
  - **File:** `archon/ai/archon_toolkit.py`
  - **Details:** MCP tool schema dict with `name`, `description`, `inputSchema` (properties: `user_id`, `file_path`, `caption`)

- [ ] **3.2** Write unit tests for `_handle_send_file`
  - **Deps:** 3.1
  - **File:** `tests/ai/test_archon_toolkit_files.py`
  - **Unit tests:**
    - `test_send_file_success` — bot.send_document called with FSInputFile, returns success message with filename and size
    - `test_send_file_with_caption` — caption passed through to send_document
    - `test_send_file_relative_path` — relative path resolved against CWD from config
    - `test_send_file_from_attachments_dir` — path within attachment store allowed
    - `test_send_file_path_escape_rejected` — path outside CWD + attachments_dir returns error string
    - `test_send_file_symlink_escape_rejected` — symlink pointing outside allowed dirs rejected
    - `test_send_file_not_found` — nonexistent path returns error string
    - `test_send_file_is_directory` — directory path returns error string
    - `test_send_file_too_large` — file > 50 MB returns error string (without uploading)
    - `test_send_file_bot_unavailable` — raises RuntimeError when bot is None
    - `test_send_file_invalid_user_id` — returns error string for missing/invalid user_id
    - `test_send_file_non_whitelisted_user_rejected` — user_id not in `allowed_user_ids` returns error string
    - `test_send_file_telegram_error` — Telegram API exception caught, returns error string
    - `test_send_file_rate_limited` — second call within 10 s returns rate-limit message
    - `test_send_file_rate_limit_expires` — call after 10 s succeeds (injectable clock)
    - `test_send_file_caption_truncated` — caption > 1024 chars truncated with suffix
    - `test_send_file_caption_html_escaped` — caption with `<>&` chars is HTML-escaped before sending

- [ ] **3.3** Implement `_handle_send_file` handler + register tool
  - **Deps:** 3.2
  - **File:** `archon/ai/archon_toolkit.py`
  - **Logic:**
    1. Validate `self._bot` is set
    2. Parse `user_id`, `file_path`, `caption` from arguments
    3. Validate `user_id` is in `self._config.access.allowed_user_ids` — reject otherwise (prevents exfiltration to arbitrary accounts)
    4. Resolve path (if relative, join with `self._config.session.working_directory`); reject if CWD is empty/unset
    5. Security check: `resolved.is_relative_to(cwd)` OR `resolved.is_relative_to(self._attachment_store.base_dir)` — reject otherwise. (`AttachmentStore.base_dir` is an existing public property returning `self._base`, see `attachment_store.py:28-29`)
    6. Validate: `resolved.exists()`, `resolved.is_file()`, size ≤ 50 MB
    7. Rate limit check: `self._file_last_sent[user_id]` with 10 s window
    8. Truncate caption to 1024 chars if needed, then `html.escape()` (bot has global `parse_mode=HTML`)
    9. `await self._bot.send_document(chat_id=user_id, document=FSInputFile(resolved), caption=safe_caption)`
    10. Update rate limiter, return success string
  - **Registration:** `self.register_tool("send_file", _SEND_FILE_SCHEMA, self._handle_send_file)` + add `self._file_last_sent: dict[int, float] = {}` in `__init__`

- [ ] **3.4** Write integration tests (MCP server)
  - **Deps:** 3.3
  - **File:** `tests/ai/test_archon_toolkit_files.py`
  - **Integration tests:**
    - `test_send_file_via_mcp` — callable via ArchonRouterMCPServer TestClient
    - `test_send_file_blocked_when_not_allowed` — not exposed when not in allowed_tools

- [ ] **3.5** Write E2E test (real toolkit + mock bot)
  - **Deps:** 3.3
  - **File:** `tests/ai/test_archon_toolkit_files.py`
  - **E2E test:**
    - `test_send_file_e2e_real_file` — create real file in tmp CWD, mock bot, call toolkit, verify bot.send_document called with correct FSInputFile path

- [ ] **3.6** Write live E2E test (real background agent via MCP)
  - **Deps:** 3.5
  - **File:** `tests/ai/test_archon_toolkit_files.py`
  - **Markers:** `pytest.mark.live`, skip if `claude` binary not found
  - **Live E2E test:** `test_send_file_live_agent`
    1. Create real file in tmp CWD
    2. Create real toolkit with mock bot, real `AttachmentStore`, config pointing to tmp CWD
    3. Create real `ArchonRouterMCPServer` with allowed_tools including `send_file`
    4. Spawn background agent with task: "Call the send_file tool to send the file at {path} to user_id {id}"
    5. Wait for completion, verify `bot.send_document` was called
    6. Verify agent log contains `send_file` tool call

- [ ] **3.7** Verify all tests pass: `uv run pytest tests/ai/test_archon_toolkit_files.py -v`
  - **Deps:** 3.6

---

### Task 4: Gateway wiring + allowed tools

> Wire attachment store into toolkit and expose both tools to background agents.

- [ ] **4.1** Pass `attachment_store` to `ArchonToolkit` in `gateway.py`
  - **Deps:** 2.8, 3.7
  - **File:** `archon/gateway/gateway.py`
  - **Details:** the `AttachmentStore` is already created in `gateway.py` (for `FileHandler`). Pass same instance to `ArchonToolkit(attachment_store=attachment_store, ...)`.
  - **Note:** the store is created before the toolkit in gateway.py, so constructor injection works directly.

- [ ] **4.2** Add `send_file` and `list_attachments` to `BG_AGENT_ALLOWED_TOOLS`
  - **Deps:** 4.1
  - **File:** `archon/gateway/gateway.py`
  - **Details:** add both tool names to the frozenset at line 584

- [ ] **4.3** Write gateway wiring tests
  - **Deps:** 4.2
  - **File:** `tests/ai/test_archon_toolkit_files.py`
  - **Unit tests:**
    - `test_toolkit_has_attachment_store` — toolkit constructed with store, `_attachment_store` is set

- [ ] **4.4** Verify full test suite passes: `uv run pytest -v`
  - **Deps:** 4.3

---

### Task 5: Documentation

- [ ] **5.1** Update `CLAUDE.md` — add `send_file` and `list_attachments` to the toolkit tool list in the `archon/ai/` section
  - **Deps:** 4.4
  - **File:** `CLAUDE.md`

- [ ] **5.2** Update `Documentation/Architecture/110_component_catalog_and_layer_breakdown.md` — add new tools to ArchonToolkit component description
  - **Deps:** 5.1
  - **File:** `Documentation/Architecture/110_component_catalog_and_layer_breakdown.md`

- [ ] **5.3** Move this file to `Documentation/Completed/13_file_transfer_toolkit.md` with final status, implementation deviations (if any)
  - **Deps:** 5.2

---

## Task dependency graph

```
1.1 → 1.2 → 1.3 → 2.1 → 2.2 → 2.3 → 2.4 → 2.5 → 2.6 → 2.7 → 2.8 ──┐
                    │                                                     ├→ 4.1 → 4.2 → 4.3 → 4.4 → 5.1 → 5.2 → 5.3
                    └──→ 3.1 → 3.2 → 3.3 → 3.4 → 3.5 → 3.6 → 3.7 ────┘
```

## Test summary

| Task | Unit | Integration (MCP) | E2E (real components) | Live E2E (real agent) |
|------|------|--------------------|-----------------------|-----------------------|
| 1 — `list_entries()` | 11 | — | — | — |
| 2 — `list_attachments` | 7 | 2 | 1 | 1 |
| 3 — `send_file` | 17 | 2 | 1 | 1 |
| 4 — Gateway wiring | 1 | — | — | — |
| **Total** | **37** | **4** | **2** | **2** |

## Risk notes

- **Telegram 50 MB limit**: `send_document` returns HTTP 400 for files > 50 MB. Pre-validate on our side to avoid wasting upload bandwidth.
- **User ID whitelist**: `send_file` validates `user_id` against `config.access.allowed_user_ids` — agents cannot exfiltrate files to arbitrary Telegram accounts. (Note: the pre-existing `send_notification` tool has the same gap — consider backfilling the whitelist check there too.)
- **HTML escaping**: The bot has `parse_mode=HTML` globally. Captions must be `html.escape()`-d to prevent broken markup or accidental HTML injection.
- **Event loop blocking**: `list_entries()` does synchronous filesystem I/O. The handler wraps it in `asyncio.to_thread()` to avoid blocking the event loop.
- **Rate limiting**: 10 s window per user_id prevents agents from spamming files. Separate rate limiter from `send_notification` so they don't interfere.
- **Path security**: `send_file` restricts to CWD + attachment store. Always resolve symlinks before checking containment. `list_attachments` is read-only within the store.
- **Bot availability**: The `bot` instance may be `None` during testing or misconfiguration. Both handlers must raise `RuntimeError` early (same as `send_notification`).
- **Large attachment stores**: `list_entries()` iterates all date directories. The `limit` parameter (default 50, max 200) bounds output size. Sorted by mtime descending with filename tie-breaker for deterministic results.
- **`detect_mime_type()` is filename-only**: It checks Telegram-reported MIME or falls back to extension mapping — no file content I/O. Safe to call inside `list_entries()` without additional threading concerns.

## Follow-up backlog items (out of scope)

- **Harden `send_notification`**: The pre-existing `send_notification` tool has the same two gaps this epic fixes for `send_file`: (1) no `user_id` whitelist validation — agents can message arbitrary Telegram accounts, (2) no `html.escape()` on message text — broken HTML in messages can cause Telegram API errors or unintended formatting. Both should be backfilled with the same patterns used here.
