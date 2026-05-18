# 10 — File Attachment Support (Inbound & Outbound)

**Purpose**: Enable Archon to receive and process file attachments from Telegram users, save them to the working directory, and inform Claude so it can process them with its tools.
**Audience**: AI agents implementing this feature, maintainers, reviewers.
**Status**: Pending
**Priority**: P2 (extends core UX — currently text/voice only)
**Estimated Effort**: Large (40 tasks across 8 phases)
**Last reviewed**: 2026-03-16
**Next review**: 2026-06-14

---

## User Story

As an Archon user, I want to send files (images, documents, code, video, archives) via Telegram and have Claude process them, so that I can interact with Claude beyond text and voice.

---

## Background

Archon currently handles only text messages and voice/audio (transcribed to text via Whisper). Users cannot send images, PDFs, code files, or other attachments. This limits the assistant's usefulness for tasks like document analysis, code review, or file processing.

The Claude Agent SDK sends text prompts via `ClaudeSDKClient.query(str)`. Files cannot be embedded as content blocks in the SDK message. Instead, files are saved to the working directory and Claude is informed via a structured text prompt — Claude then uses its built-in tools (Read, Bash, etc.) to inspect and process the files.

### Image Analysis — Deferred

**Important limitation**: The SDK accepts `str` only — no multimodal content blocks. Claude cannot "see" images through the text-only Read tool (it returns binary data). Visual image analysis (vision) requires either SDK content block support or base64 injection into prompts — both are out of scope for this iteration.

Images are still saved and Claude is informed of their metadata (filename, dimensions, size), but Claude cannot visually analyze image contents. It can run CLI tools (e.g., `file`, `identify`, `exiftool`) on them via Bash. Once the SDK supports content blocks or a validated vision path exists, image analysis can be enabled as a follow-up.

### File Type Capabilities

| File type | Claude can... | How |
|-----------|--------------|-----|
| Text files (.py, .js, .md, .csv, .json, etc.) | Fully read and process | Read tool reads text content directly |
| PDF documents | Extract text via CLI tools | Bash tool with `pdftotext`, `mutool`, or similar — **not** the Read tool (PDFs are binary) |
| Images (JPEG, PNG, GIF, WebP) | Inspect metadata only | Bash tool with `file`, `identify`, `exiftool` — cannot see image contents |
| Video, archives, other binary | Save and manage | Bash tool for basic operations; user decides intent |

---

## Scope

### In Scope

- **Inbound**: Receive files from Telegram → save to attachments directory → inform Claude via structured prompt
- **Supported types**: Images (JPEG, PNG, GIF, WebP — saved, metadata only), documents (PDF, text, code — readable via appropriate tools), video, archives, any other Telegram file type
- **Image auto-resize**: Detect images exceeding size thresholds and auto-resize via Pillow. Preserve original. Inform Claude the image is resized. (Prepares for future vision support.)
- **Media groups**: Telegram albums (multiple files in one message) handled as separate saves with one combined prompt
- **Captions**: Telegram captions included as the user's message text alongside attachment blocks
- **Configurable storage**: `[session] attachments_dir` in `config.toml`
- **TTL-based cleanup**: `[session] attachments_cleanup_hours` — positive float enables auto-cleanup at gateway startup and periodically
- **File size guard**: Reject files exceeding Telegram's 20 MB bot API download limit before attempting download
- **Filename sanitization**: Strip path traversal characters, validate saved paths stay within attachments directory
- **Download timeout**: All file downloads have a configurable timeout (default 30s) to prevent hanging on slow connections

### Out of Scope (Deferred)

- **Visual image analysis**: Claude cannot see images via the text-only SDK. Deferred until SDK supports content blocks or a validated alternative exists.
- **Outbound file sending**: Claude producing files and auto-sending them to Telegram (except existing voice TTS)
- **Base64 content blocks**: Embedding images/files directly in SDK messages (SDK currently accepts text-only prompts)
- **Streaming large files**: No chunked upload/download beyond Telegram's built-in limits (20 MB download, 50 MB upload)

---

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| How files reach Claude | Save to working dir + text prompt | KISS — works with all file types, Claude uses existing tools to inspect. No SDK changes needed. |
| Storage structure | `attachments/YYYY-MM-DD/filename.ext` | By-date folders for organization |
| File naming | Original filename + collision suffix (`report.pdf`, `report_2.pdf`) + sanitization | Readable; filenames are sanitized (strip `..`, `/`, `\`, null bytes) and resolved path is validated to stay within `attachments_dir` |
| Image resizing | Auto via Pillow before saving | Prepares for future vision support. Original preserved as `{stem}_resized{suffix}` (resized copy gets the suffix, not the original — avoids collision with user files named `*_original.*`). |
| Resize threshold | >5 MB file size OR >8000 px on any edge → resize to long edge ≤1568 px | Matches Claude's vision API specs for when content blocks become available |
| Cleanup mechanism | TTL-based using file mtime, runs at gateway startup + periodically (every 6 hours) | File mtime avoids day-boundary edge cases. Gateway startup ensures cleanup runs even for long-lived sessions. Periodic check prevents unbounded growth during 24/7 daemon operation. |
| Cleanup config | `attachments_cleanup_hours = 12.5` (0 or omit = disabled) | Self-documenting — no separate boolean. Positive float enables. |
| Video/archive handling | Save file, Claude asks user what to do | Avoids guessing intent for ambiguous file types |
| Media groups | Separate saves, one combined prompt | Claude sees all files in context together |
| Outbound files | Not auto-sent | Avoids complexity. User can ask Claude to describe where files are. |
| Animated GIFs | Skip resize, save as-is | Resizing animated GIFs is complex (multi-frame), low value without vision. Save original and report metadata. |
| File handlers use Pipeline | All file handlers call `session.send()` via `Pipeline` (from `session_manager.get_or_create()`), not `ClaudeSession` directly | Maintains classification, routing, and multi-agent orchestration layer. |
| Handler registration order | Commands → sticker → photo → video → voice/audio (mutually exclusive) → document → generic text | Explicit priority order prevents filter-match bugs. Voice and audio-as-attachment registrations are mutually exclusive via `if cfg.voice.enabled` / `else` — only one handler per filter at a time. |
| File handler delegation | File handlers build the attachment prompt, then delegate to `handle_message()` by passing the prompt as `prompt_override` | `handle_message()` is modified to accept an optional `prompt_override: str \| None = None` parameter. When provided, it is used instead of `message.text` (bypasses the `message.text is None` early return). File handlers receive the same DI-injected parameters as `handle_message()` (aiogram injects them from dispatcher context) and forward them in the call. This avoids extracting the complex event streaming loop. The file handler's job is: download → save → build prompt → call `handle_message(message, ..., prompt_override=prompt)`. |

---

## Security

### Filename Sanitization (Task 1.3)

All Telegram filenames are user-controlled and must be sanitized before use:

1. Strip path separators (`/`, `\`), `..` sequences, and null bytes
2. Reject empty filenames after sanitization (generate a fallback: `attachment_{timestamp}.{ext}`)
3. Resolve the final absolute path and assert it is a child of `attachments_dir` (prevent symlink escape)
4. Limit filename length (255 chars max)

### Attachments Directory Validation (Task 1.1)

- On startup, validate `attachments_dir` is not a symlink (or resolve and warn)
- Cleanup only operates on directories that match the `YYYY-MM-DD` naming pattern within `attachments_dir`
- Never delete files outside `attachments_dir`

### File Size

- Reject files >20 MB before download (Telegram cloud Bot API limit)
- `message.document.file_size` may be `None` — if so, attempt download; if the Bot API returns a 400 error (file too large), catch and report to user
- `message.photo[].file_size` is also optional — same approach

### Download Timeout

- All `bot.download_file()` calls use `asyncio.wait_for()` with a 30-second timeout (configurable)
- On timeout, send user-friendly error: "File download timed out. Please try again."

---

## Configuration

New keys in `config.toml` under `[session]`:

```toml
[session]
# Existing keys...
# working_directory = "~/projects"

# New keys for file attachment support:
attachments_dir = "/Users/user123/Downloads/attachments"  # default: {working_directory}/attachments
attachments_cleanup_hours = 12.5                           # 0 or omit = disabled
```

---

## Prompt Template (Fixed)

When a file is saved, Claude receives a structured text block prepended to the user's message:

**Single text/code file:**
```
[Attachment: attachments/2026-03-16/utils.py]
Type: Python file, 12 KB

User message: Review this code
```

**PDF document:**
```
[Attachment: attachments/2026-03-16/report.pdf]
Type: PDF document, 2.3 MB
Note: PDF is a binary format — use a CLI tool (e.g., pdftotext, mutool) to extract text content. The Read tool will not work on PDFs.

User message: Summarize this report
```

**Single image (no vision — metadata only):**
```
[Attachment: attachments/2026-03-16/photo.jpg]
Type: JPEG image, 1.2 MB, 3024×4032
Note: Image saved to disk. Visual analysis is not available — you can inspect metadata via CLI tools (file, identify, exiftool).

User message: What's in this image?
```

**Resized image:**
```
[Attachment: attachments/2026-03-16/photo.jpg]
Type: JPEG image, 4000×3000 (original)
Resized copy: attachments/2026-03-16/photo_resized.jpg (1568×1045, 1.2 MB)
Note: Image saved to disk. Visual analysis is not available.
```

**Media group with caption:**
```
[Attachment: attachments/2026-03-16/screenshot1.png]
Type: PNG image, 850 KB, 1920×1080

[Attachment: attachments/2026-03-16/screenshot2.png]
Type: PNG image, 1.1 MB, 4000×3000
Resized copy: attachments/2026-03-16/screenshot2_resized.png (1568×1045)

User message: Compare these two screenshots
```

**File without caption (no user message):**
```
[Attachment: attachments/2026-03-16/data.csv]
Type: CSV file, 45 KB

The user sent this file without a message. Ask what they'd like you to do with it.
```

---

## Acceptance Criteria

1. User sends a text/code file → file is saved to `attachments/YYYY-MM-DD/` → Claude is informed and can read it via Read tool
2. User sends a PDF → file is saved → Claude is informed and prompted to use CLI tools for text extraction
3. User sends an image → file is saved → Claude is informed of metadata (dimensions, size) but cannot visually analyze it
4. User sends a video or archive → file is saved → Claude asks user what to do
5. Images exceeding 5 MB or 8000 px are auto-resized via Pillow; original is preserved; resized copy has `_resized` suffix
6. Media groups (albums) produce one combined prompt with all attachment blocks
7. Captions are included as the user's message text
8. Original filenames are sanitized and preserved; collisions get a numeric suffix
9. Filenames are sanitized against path traversal; saved paths are validated to stay within `attachments_dir`
10. `attachments_dir` is configurable via `config.toml`; defaults to `{working_directory}/attachments`
11. `attachments_cleanup_hours` enables TTL cleanup (using file mtime) at gateway startup and periodically; `0` or omit disables
12. Files >20 MB are rejected before download with a user-friendly message
13. File downloads have a 30-second timeout
14. Existing voice/audio handling is not broken
15. All file handlers go through `Pipeline` (not `ClaudeSession` directly)
16. File handlers delegate to `handle_message(..., prompt_override=prompt)` — no event streaming duplication
17. All new code has tests (TDD, ≥85% coverage)
18. No `platform.system()` checks outside `archon/platform/`
19. Whitelist middleware still runs before any new handler

---

## Tasks

Tasks are ordered by delivery priority. Each task includes its dependencies and required tests. A release point (---) marks every 5–8 tasks where the codebase is in a releasable state with all tests passing.

### Release 1 — Foundation: Config, Storage, Shared Types (Tasks 1–7)

Infrastructure for all file types. No Telegram handlers yet — just the building blocks.

- [ ] **1. Add config schema for attachments** *(no deps)*
  Add `attachments_dir` (str, default `"{working_directory}/attachments"`) and `attachments_cleanup_hours` (float, default `0`) to the `[session]` section of the config model. Validate on startup: if `attachments_dir` is a symlink, resolve it and log a warning; ensure parent directory exists. Update `config.toml.example` with annotated entries.
  **Tests (unit):** config loading with default values, explicit values, zero means disabled, symlink resolution, missing parent directory warning.

- [ ] **2. Create `AttachmentInfo` dataclass and MIME detection** *(no deps)*
  New module `archon/ai/attachment_types.py`. Define `AttachmentInfo` dataclass: `path: Path`, `mime_type: str`, `size_bytes: int`, `dimensions: tuple[int, int] | None` (images only), `resized_from: tuple[int, int] | None`, `resized_path: Path | None`. Helper function `detect_mime_type(filename: str, telegram_mime: str | None) -> str` — use Telegram's MIME if available, fall back to `mimetypes.guess_type()`. Utility function `format_file_size(size_bytes: int) -> str` — returns `"45 KB"`, `"2.3 MB"`, `"1.1 GB"`.
  **Tests (unit):** known extensions, unknown extensions, Telegram MIME override, size formatting at byte/KB/MB/GB boundaries, dataclass construction with all field combinations.

- [ ] **3. File size guard utility** *(depends on: 2)*
  Function `check_file_size(file_size: int | None, max_bytes: int = 20 * 1024 * 1024) -> str | None`. Returns a user-friendly error message if `file_size` exceeds `max_bytes` (e.g., `"File is too large (25.3 MB). Telegram limits bot downloads to 20 MB."`). Returns `None` if size is acceptable or unknown (`file_size is None` — allow download attempt; the Bot API will return a 400 error if the file exceeds the cloud API limit, which is caught in error handling).
  **Tests (unit):** under limit, at limit, over limit, None size, custom max_bytes.

- [ ] **4. Create `AttachmentStore` class** *(depends on: 1)*
  New module `archon/ai/attachment_store.py`. Responsible for: creating date-based subdirectories (`YYYY-MM-DD`), saving bytes to disk with sanitized original filename, handling collision suffixes (`report.pdf` → `report_2.pdf`). **Filename sanitization**: strip `..`, `/`, `\`, null bytes; reject control characters; limit to 255 chars; generate fallback `attachment_{timestamp}.{ext}` if filename is empty after sanitization. **Path validation**: after constructing the full path, resolve it and assert it is a child of `attachments_dir` — raise `ValueError` if not. Method: `save(filename: str, data: bytes, date: date) -> Path`. Returns the saved path relative to `attachments_dir`. Uses synchronous I/O (`Path.write_bytes()`) — no TOCTOU race because asyncio is single-threaded with no `await` between existence check and write.
  **Tests (unit):** basic save, collision handling (same name 2x, 3x), date folder creation, path traversal rejected (`../../etc/passwd`, `../config.toml`), null bytes stripped, empty filename gets fallback, resolved path outside attachments_dir raises ValueError, control characters rejected.

- [ ] **5. Add TTL cleanup to `AttachmentStore`** *(depends on: 4)*
  Method: `cleanup(max_age_hours: float) -> int` — scans all files recursively within `attachments_dir`, deletes files whose `mtime` is older than `max_age_hours`. Removes empty date-based subdirectories after file deletion. Only operates on directories matching `YYYY-MM-DD` pattern — ignores any other directories/files at the root level. Returns count of deleted files. Skips if `max_age_hours <= 0`.
  **Tests (unit):** file older than TTL is deleted, file within TTL is kept, zero TTL skips cleanup, empty attachments dir is no-op, non-date-pattern directories are not touched, empty date folders removed after last file deleted.

- [ ] **6. Create `AttachmentPromptBuilder`** *(depends on: 2)*
  New module `archon/ai/attachment_prompt.py`. Builds the structured text prompt from a list of `AttachmentInfo` objects. Output: formatted string matching the prompt template spec. **PDF-specific note**: for PDF MIME types, includes the note: "PDF is a binary format — use a CLI tool (e.g., pdftotext, mutool) to extract text content. The Read tool will not work on PDFs." For images, includes the "Visual analysis is not available" note. For video/archive types, includes the "Ask the user what they'd like you to do" note.
  **Tests (unit):** single text file, single PDF (with CLI tool note), single image with metadata, resized image with resized_path, media group (multiple files), file with caption, file without caption (adds "ask user" suffix), video file, archive file.

- [ ] **7. Trigger cleanup at gateway startup and periodically** *(depends on: 5)*
  In `Gateway.start()`, call `attachment_store.cleanup()` once during startup (before bot polling begins). Schedule a periodic cleanup task via `asyncio.create_task()` that runs every 6 hours. Store the task reference so it can be cancelled in `stop_all()` during graceful shutdown (within the 5-second budget). Wire `AttachmentStore` into the dependency graph via dispatcher context (`dp["attachment_store"]`). Keeps cleanup out of the session creation path.
  **Tests (unit):** cleanup runs at startup, periodic task runs on schedule, cleanup does not run in session creation path, periodic task is cancelled on shutdown.

> **Release 1 checkpoint**: All foundation modules exist with full unit test coverage. Config loads correctly, files can be saved/cleaned up, prompts can be built. No user-facing changes yet — internal infrastructure only.

---

### Release 2 — Document Support: End-to-End (Tasks 8–14)

First user-facing feature: send a document via Telegram → Claude receives it.

- [ ] **8. Add `prompt_override` parameter to `handle_message()`** *(no deps)*
  Modify `handle_message()` in `archon/chat/handler.py` to accept an optional `prompt_override: str | None = None` parameter. When provided: skip the `message.text is None` early return, use `prompt_override` as the prompt text (instead of `message.text`), and pass it to the session/Pipeline. The `message.from_user` check remains. All existing behavior is unchanged when `prompt_override` is `None`.
  **Tests (unit):** existing handler tests still pass (regression), calling with `prompt_override` sends override text to session, calling with `prompt_override` on a message where `message.text is None` works correctly, `message.from_user is None` still returns early.

- [ ] **9. Create `FileHandler` — document handler** *(depends on: 8, 4, 6, 3)*
  New module `archon/chat/file_handler.py`. Class `FileHandler` with method `handle_document(message: Message, ...) -> None`. Receives DI-injected parameters from dispatcher context (`session_manager`, `truncation`, `max_len`, `notifications`, `cwd`, `history_manager`, `agent_logger`, `background_agent_manager`) plus `attachment_store: AttachmentStore`. **Flow**: (1) check file size via guard — if too large, reply with error and return; (2) extract `message.document.file_id`, `message.document.file_name`, `message.document.mime_type`; (3) download via `message.bot.get_file()` + `asyncio.wait_for(message.bot.download_file(...), timeout=30)` — on timeout, reply with error and return; (4) save via `AttachmentStore.save()`; (5) build `AttachmentInfo`; (6) build prompt via `AttachmentPromptBuilder`; (7) combine with caption (`message.caption` or no-caption fallback); (8) delegate: call `await handle_message(message, ..., prompt_override=prompt)`.
  **Tests (unit):** document is downloaded, saved, prompt is built correctly, `handle_message` is called with `prompt_override`, DI parameters are forwarded, file size rejection works, download timeout works.

- [ ] **10. Register document handler in gateway** *(depends on: 9)*
  In `gateway.py`, register `file_handler.handle_document` with `F.document` filter. Follow the canonical handler priority order (before generic text handler). Wire `AttachmentStore` and `FileHandler` into dispatcher context.
  **Tests (unit):** document message reaches document handler not generic handler, handler registration order is correct.

- [ ] **11. Integration test: document flow** *(depends on: 10)*
  **Tests (integration):** mock Telegram document message → size check → download (with timeout) → save → prompt built (verify PDF gets CLI tool note) → `handle_message()` called with `prompt_override` → Pipeline.send() → events streamed back. Verify with PDF (.py file, .csv file). Verify file size rejection for oversized file. Verify download timeout handling.

- [ ] **12. Telegram download failure handling** *(depends on: 9)*
  If `bot.get_file()` or `bot.download_file()` fails (network error, file expired, Bot API 400 error for oversized files), send user-friendly error message: `"Failed to download the file. Please try again."`. Log the error with details. Note: Telegram guarantees file paths (from `getFile`) are valid for at least 1 hour.
  **Tests (unit):** network error, file not found, timeout, Bot API 400 error — each produces user-friendly message and logs error.

- [ ] **13. Disk space / write failure handling** *(depends on: 4)*
  If `AttachmentStore.save()` fails (disk full, permission denied), send user-friendly error: `"Failed to save the file. Check disk space and permissions."`. Log the error.
  **Tests (unit):** disk full simulation, permission denied simulation — each produces user-friendly message and logs error.

- [ ] **14. Concurrent file + text message handling** *(depends on: 9)*
  Verify that the `_send_lock` in `ClaudeSession` correctly serializes a file attachment prompt and a simultaneous text message. Since file handlers delegate via `handle_message(..., prompt_override=...)`, they inherit the same lock behavior via Pipeline.
  **Tests (integration):** concurrent file + text → both processed sequentially via `_send_lock`, no corruption.

> **Release 2 checkpoint**: Users can send documents (text, code, PDF) via Telegram. Claude receives them with proper prompts, including PDF CLI tool notes. Error handling covers download failures, disk errors, and file size limits. All existing voice/text handling unchanged.

---

### Release 3 — Image Support: Save + Metadata (Tasks 15–22)

Images saved to disk with auto-resize. No visual analysis.

- [ ] **15. Add Pillow to project dependencies** *(no deps)*
  Add `Pillow` to `pyproject.toml` under dependencies. Run `uv sync`. Verify import works.
  **Tests (unit):** verify `from PIL import Image` succeeds.

- [ ] **16. Create `ImageResizer` class** *(depends on: 15)*
  New module `archon/ai/image_resizer.py`. Uses Pillow. Method: `resize_if_needed(image_path: Path) -> ResizeResult` where `ResizeResult` contains: `resized: bool`, `source_path: Path` (original file path — unchanged), `original_dimensions: tuple[int, int] | None`, `new_dimensions: tuple[int, int] | None`, `resized_path: Path | None` (path to resized copy if created). **Order of operations**: (1) try to open with Pillow — if it fails, return `resized=False, dimensions=None` (corrupted image, do not raise); (2) apply EXIF orientation before reading dimensions; (3) check `Image.is_animated` — if true, skip resize, return dimensions only; (4) check thresholds: >5 MB file size OR >8000 px on any edge → resize to long edge ≤1568 px, preserving aspect ratio; (5) resized copy saved as `{stem}_resized{suffix}`. **WebP with transparency**: preserve alpha channel (RGBA mode).
  **Tests (unit):** image under threshold unchanged, image over size threshold resized, image over dimension threshold resized, aspect ratio preserved, `_resized` suffix, animated GIF skipped (returns dimensions, no resize), corrupted image graceful (no exception, dimensions=None), WebP alpha preserved, EXIF orientation applied.

- [ ] **17. Pillow import fallback** *(depends on: 16)*
  If Pillow is not installed (import fails), skip image resizing — save original as-is, log warning. `ImageResizer` returns `resized=False` with `dimensions=None`. Feature degrades gracefully.
  **Tests (unit):** mock Pillow unavailable → no resize, no crash, warning logged.

- [ ] **18. Add photo handler to `FileHandler`** *(depends on: 9, 16)*
  Method: `handle_photo(message: Message, ...) -> None`. Extract `message.photo[-1]` (largest size — Telegram provides photos in ascending size order). Check file size via guard. Download with 30s timeout. Save via `AttachmentStore.save()`. Run `ImageResizer.resize_if_needed()`. Build `AttachmentInfo` with dimensions and resize info. Build prompt via `AttachmentPromptBuilder` (includes "visual analysis not available" note). Combine with caption. Delegate via `handle_message(..., prompt_override=prompt)`.
  **Tests (unit):** photo downloaded, saved, resized if needed, prompt includes metadata and limitation note, delegation works, largest photo size selected from array.

- [ ] **19. Handle uncompressed images (sent as document)** *(depends on: 18, 9)*
  Users can send images as "files" (uncompressed) via Telegram's paperclip menu. These arrive as `message.document` with image MIME types (`image/jpeg`, `image/png`, `image/gif`, `image/webp`). In `handle_document`, detect image MIME type and route through the image pipeline (resize + image-specific prompt).
  **Tests (unit):** document with image MIME type handled as image, non-image document handled as document, edge MIME types (`image/svg+xml` treated as non-image).

- [ ] **20. Register photo handler in gateway** *(depends on: 18)*
  Register `file_handler.handle_photo` with `F.photo` filter, following canonical handler priority order.
  **Tests (unit):** photo message reaches photo handler not document or generic handler.

- [ ] **21. Integration test: end-to-end image flow** *(depends on: 20, 19)*
  **Tests (integration):** mock Telegram photo → size check → download with timeout → save → resize if needed → prompt with metadata → `handle_message()` called with `prompt_override` → Pipeline.send() → events streamed back. Test under-threshold and over-threshold images. Verify "visual analysis not available" note in prompt.

- [ ] **22. Integration test: image-as-document flow** *(depends on: 19)*
  **Tests (integration):** mock Telegram document with `image/jpeg` MIME → routed through image pipeline → resized → prompt includes image metadata. Mock Telegram document with `application/pdf` MIME → routed through document pipeline → no resize.

> **Release 3 checkpoint**: Users can send photos and images-as-files via Telegram. Images are saved, auto-resized if too large, and Claude is informed of metadata. Combined with Release 2, both documents and images are fully supported.

---

### Release 4 — Media Groups + Captions (Tasks 23–28)

Album support and caption polish across all file types.

- [ ] **23. Media group collector** *(no deps)*
  Create `MediaGroupCollector` — accumulates messages by `media_group_id` with a generous timeout (default 1.0s after last message). Returns the complete list of messages. **Concurrency**: buffers messages only — no downloads, no session interaction, no lock acquisition during collection. **Cross-handler aggregation**: both `F.photo` and `F.document` handlers route to the same collector when `media_group_id` is present. **Processing ownership**: the handler whose `await collector.add()` triggers timeout expiry processes the entire group; others return early.
  **Tests (unit):** single message (no group) passes through immediately, grouped messages collected, timeout fires after last message, different group IDs independent, mixed group (photo + document) collected together, only one handler processes (others return early), timeout edge cases.

- [ ] **24. Integrate media group collector with handlers** *(depends on: 23, 18, 9)*
  When `message.media_group_id` is set on a photo or document message, route through `MediaGroupCollector`. After collection: download and save all files (sequentially, 30s timeout each), route each through type-specific pipeline (image resize for photos/image-documents, direct save for other documents), build one combined prompt with all `[Attachment]` blocks + single caption (from first captioned message), delegate via `handle_message(..., prompt_override=combined_prompt)`.
  **Tests (unit):** album of 3 photos → 3 saves, 1 combined prompt. Mixed album (2 photos + 1 PDF) → all saved through correct pipelines, 1 combined prompt. Only one handler processes.

- [ ] **25. Integration test: media group flow** *(depends on: 24)*
  **Tests (integration):** 3 mock photo messages with same `media_group_id` → collected → saved → combined prompt → Pipeline.send(). Verify caption from first captioned message. Test mixed album with photos and documents.

- [ ] **26. Caption extraction and entity stripping** *(depends on: 9, 18)*
  Ensure `message.caption` is used as user message text for all file types (photo, document, video). If caption is `None`, prompt includes `"The user sent this file without a message. Ask what they'd like you to do with it."`. Handle caption entities (bold, links): strip Telegram entity formatting, keep plain text. Caption entity stripping also applies in the media group path — the caption from the first captioned message goes through the same stripping.
  **Tests (unit):** with caption, without caption, caption with bold/link/mention entities → plain text, media group caption with entities.

- [ ] **27. History logging for attachments** *(depends on: 6)*
  Extend `HistoryManager` to log attachment events. Format in history includes the full prompt sent to Claude (metadata, type, notes), not just the filename — so history reviewers can see exactly what Claude was told about the file.
  **Tests (unit):** attachment logged in session history file with full metadata, multiple attachments in one session.

- [ ] **28. Status command update** *(depends on: 1)*
  Update `/status` command output to show attachments dir path and disk usage (total size of attachments folder).
  **Tests (unit):** status output includes attachments info, handles empty/missing attachments dir gracefully.

> **Release 4 checkpoint**: Full album/media group support. Captions handled correctly across all file types with entity stripping. History logs attachment metadata. `/status` shows attachments disk usage.

---

### Release 5 — Video, Stickers, Archives, Audio Fallback (Tasks 29–35)

Remaining file types: video, stickers, archives, and audio-when-voice-disabled.

- [ ] **29. Add video handler** *(depends on: 9)*
  Method: `handle_video(message: Message, ...) -> None`. Check file size via guard. Download `message.video` or `message.video_note` (round video) with 30s timeout. Save via `AttachmentStore`. Build prompt: `"The user sent a video file. Ask the user what they'd like you to do with it."`. Include caption if present. Delegate via `handle_message(..., prompt_override=prompt)`. Register with `F.video | F.video_note` filter following canonical handler order.
  **Tests (unit):** video downloaded and saved, video_note handled, prompt asks user intent, caption included, file size guard works, registration order correct.

- [ ] **30. Add sticker handler** *(depends on: 9)*
  Telegram stickers arrive as `message.sticker`. Save the sticker file. For static stickers (WebP), save and report as image (no vision). For animated stickers (TGS/WebM), save as-is and inform Claude it's an animated sticker. Register with `F.sticker` filter following canonical handler order. Delegate via `handle_message(..., prompt_override=prompt)`.
  **Tests (unit):** static sticker saved as WebP with image metadata, animated sticker saved with animated note, registration order correct.

- [ ] **31. Add generic file fallback for archives and unknown types** *(depends on: 9, 19)*
  In `handle_document`, after image MIME check, handle remaining types. For archive types (`.zip`, `.tar`, `.gz`, `.rar`, `.7z`), prompt includes: `"The user sent an archive file. Ask the user what they'd like you to do with it."`. For all other types, save and inform Claude with generic metadata.
  **Tests (unit):** zip file prompt asks intent, tar.gz prompt asks intent, unknown binary saved with generic metadata, MIME detection for archive types.

- [ ] **32. Add audio-as-attachment handler (voice disabled)** *(depends on: 9)*
  When voice is disabled in config (`cfg.voice.enabled = false`), audio messages (`F.audio`) are silently dropped. **Registration**: in gateway.py, `F.audio` registration is inside `if cfg.voice.enabled: ... else: ...` — mutually exclusive. When voice enabled, `VoiceMessageHandler.handle_audio` registered. When disabled, `FileHandler.handle_audio_attachment` registered instead. Only one handler per filter at runtime. The audio-as-attachment handler saves the audio file and informs Claude via `handle_message(..., prompt_override=prompt)`.
  **Tests (unit):** voice enabled → transcription handler registered, voice disabled → audio-as-attachment handler registered.
  **Tests (integration):** audio message with voice disabled → file saved, Claude informed. Audio message with voice enabled → existing transcription (regression).

- [ ] **33. Integration test: video flow** *(depends on: 29)*
  **Tests (integration):** mock Telegram video message → size check → download → save → prompt asks user intent → Pipeline.send(). Verify with regular video and video_note.

- [ ] **34. Integration test: sticker and archive flows** *(depends on: 30, 31)*
  **Tests (integration):** mock sticker message → saved → prompt with metadata. Mock zip file → saved → prompt asks intent. Verify static vs animated sticker handling.

- [ ] **35. Regression test: voice/audio not broken** *(depends on: 32)*
  **Tests (regression):** full voice message flow still works when voice enabled (transcription → text → Claude). Audio message flow still works. Verify no handler registration conflicts.

> **Release 5 checkpoint**: All file types supported — documents, images, video, stickers, archives, audio fallback. Every Telegram attachment type is handled. Voice/audio transcription unaffected.

---

### Release 6 — Documentation + Final Test Suite (Tasks 36–41)

Documentation updates and comprehensive final test coverage.

- [ ] **36. Update CLAUDE.md** *(depends on: all implementation tasks)*
  Add `FileHandler` to `archon/chat/` section. Add `AttachmentStore`, `ImageResizer`, `AttachmentPromptBuilder`, `AttachmentInfo` to `archon/ai/` section. Add `MediaGroupCollector` to `archon/chat/` section. Update config section with new `[session]` keys. Document canonical handler registration order. Document the delegation pattern (file handlers → `handle_message` with `prompt_override`).

- [ ] **37. Update README.md architecture section** *(depends on: all implementation tasks)*
  Add file attachment support to the architecture description. Update the component list with new modules.

- [ ] **38. Update user manual** *(depends on: all implementation tasks)*
  Add "Sending Files" section to `Documentation/UserManual/user_manual.md`: supported file types, image limitation (no vision), PDF extraction note, media groups, cleanup settings, configuration options.

- [ ] **39. Update config.toml.example** *(depends on: 1)*
  Add annotated `attachments_dir` and `attachments_cleanup_hours` entries to the example config with comments explaining defaults and behavior.

- [ ] **40. Final integration test suite** *(depends on: all implementation tasks)*
  **Tests (e2e):** comprehensive test covering: text file, PDF (with CLI note), image (metadata only, no vision), video, archive, sticker, media group (including mixed photo+document), caption, no caption, resize (under/over threshold), cleanup (TTL expiry), file size rejection, download timeout, filename sanitization (path traversal), disk write failure, concurrent file + text. Verify ≥85% coverage on all new modules.

- [ ] **41. Coverage verification and cleanup** *(depends on: 40)*
  Run full test suite. Verify ≥85% coverage on: `attachment_store.py`, `attachment_types.py`, `attachment_prompt.py`, `image_resizer.py`, `file_handler.py`, `media_group_collector.py`. Fix any gaps. Remove any TODO comments. Verify no new warnings introduced.
  **Tests:** coverage report, warning-free build.

> **Release 6 checkpoint**: Feature complete. All documentation updated. ≥85% test coverage verified. Ready for production.

---

## Dependency Graph (Phases)

```
Phase 1 (Foundation) ──► Phase 2 (Documents) ──► Phase 3 (Images) ──► Phase 4 (Media Groups)
                                              ──► Phase 5 (Video/Sticker/Archive)
                                                                       Phase 6 (Polish)     ◄── Phases 3-5
                                                                       Phase 7 (Error Handling) ◄── Phases 3-5
                                                                           ──► Phase 8 (Docs)
```

Phase 1 provides all shared infrastructure.
Phase 2 (documents) is the first deliverable — highest value.
Phase 3 (images) follows Phase 2.
Phase 4 (media groups) depends on Phase 3 (needs photo handler for mixed albums).
Phase 5 can be developed in parallel with Phases 3-4 after Phase 2.
Phases 6 and 7 can be developed in parallel after Phases 3–5.

---

## Handler Registration Order (Canonical)

All message handlers must be registered in this exact order on the same `Dispatcher` instance in `gateway.py` (not on sub-routers). aiogram checks filters top-to-bottom on the dispatcher and dispatches to the first match.

```
1. Command handlers (CommandStart, Command("status"), etc.)
2. Callback query handlers (notify:, model:, cancel_agent:)
3. F.sticker              → file_handler.handle_sticker
4. F.photo                → file_handler.handle_photo
5. F.video | F.video_note → file_handler.handle_video
6. F.voice                → voice_handler.handle_voice       (if voice enabled)
7. F.audio                → voice_handler.handle_audio       (if voice enabled)
                          → file_handler.handle_audio_attachment (if voice disabled)
   [Mutually exclusive: only ONE audio handler is registered at runtime, via if/else in gateway.py]
8. F.document             → file_handler.handle_document
9. (no filter)            → handle_message                   (generic text fallback)
```

---

## Known Limitations

- **No visual image analysis**: Claude cannot see image contents via the text-only SDK. Images are saved and metadata is reported, but visual analysis requires SDK content block support (deferred).
- **PDF requires CLI tools**: Claude's Read tool returns binary data for PDFs. The prompt template instructs Claude to use `pdftotext`, `mutool`, or similar CLI tools. These must be installed on the host system.
- **20 MB download limit**: Telegram's cloud Bot API limits bot file downloads to 20 MB. Users running a local Bot API server have no such limit — the file size guard will incorrectly reject files between 20-2000 MB in that case. Not addressed in this iteration.
- **Forwarded messages**: Forwarded files are handled identically to direct files. `message.forward_from` and `message.forward_date` are not used or logged. If forwarded file behavior diverges, this may need revisiting.
- **File ID validity**: Telegram guarantees file paths (from `getFile`) are valid for at least 1 hour. The `file_id` itself may remain valid longer but this is not guaranteed. All handlers download immediately, so this is not a concern.
- **No rate limiting on file uploads**: A user spamming many files rapidly could cause I/O pressure. Not addressed in this iteration — the whitelist (trusted users only) mitigates the risk.
- **Long-running sessions and cleanup**: Cleanup runs at gateway startup and every 6 hours via a periodic task. If the daemon runs continuously, cleanup is guaranteed. If the periodic task is cancelled unexpectedly, cleanup falls back to the next gateway restart.
- **Default `attachments_dir` may be inside a git repo**: The default `{working_directory}/attachments` creates the folder inside the user's project directory. Users should add `attachments/` to `.gitignore` or set an explicit `attachments_dir` outside the repo (e.g., `~/.archon/attachments`).
- **No content-based deduplication**: If the same file is sent twice, it is saved twice (with collision suffix). Hash-based dedup is not implemented — deferred to a future iteration.
- **`AttachmentStore.save()` uses synchronous I/O**: File writes are synchronous (`Path.write_bytes()`), which is acceptable for the expected file sizes (<20 MB) and single-user concurrency model. No TOCTOU race on collision detection because asyncio is single-threaded and there is no `await` between the existence check and the write.

---

## AI Notes

- The Claude Agent SDK (`ClaudeSDKClient.query()`) accepts `str` only — no content blocks. All file interaction goes through the working directory + text prompt approach.
- **Critical**: All file handlers must go through `Pipeline` (via `session_manager.get_or_create()`), not `ClaudeSession` directly. The Pipeline provides classification, decomposition, and multi-agent routing. File handlers achieve this by delegating to `handle_message()`.
- **Delegation pattern**: File handlers do NOT implement event streaming. They download, save, build the prompt, and call `handle_message(message, ..., prompt_override=prompt)`. The `prompt_override` parameter (added in task 2.1) bypasses the `message.text is None` guard and uses the attachment prompt instead. File handlers receive the same DI-injected parameters as `handle_message()` (aiogram injects from dispatcher context) and forward them in the call. This reuses the full streaming loop (beacon management, mid-query mode switching, PlanExecutor spawning, notification mode, history logging) without duplication.
- Voice/audio handling (`VoiceMessageHandler`) must NOT be broken — it has its own handler registered before the document handler. Audio handler registration is mutually exclusive: voice enabled → VoiceMessageHandler, voice disabled → FileHandler.handle_audio_attachment.
- New handlers must be registered in `gateway.py` following the canonical handler priority order documented above.
- `AttachmentStore` should be injected via dispatcher context (`dp["attachment_store"]`), following the existing DI pattern.
- Image resizing uses Pillow — the resized copy gets the `_resized` suffix (not the original). Aspect ratio must be preserved. Order of operations: open → EXIF orientation → animated check → threshold check → resize.
- File naming collision suffix: `report.pdf`, `report_2.pdf`, `report_3.pdf` — start counting from 2.
- Cleanup uses file `mtime` (not folder name dates) to avoid day-boundary edge cases. Runs at gateway startup + every 6 hours via periodic asyncio task. Does NOT run in session creation path (avoids latency on first message).
- Media group collection uses a 1.0s timeout (generous — prioritizes completeness over speed). The collector is handler-agnostic: both photo and document handlers route messages to the same collector when `media_group_id` is present. No downloads or session interaction during collection window.
- All file handler downloads use `asyncio.wait_for(..., timeout=30)` to prevent hanging on slow connections. Note: the existing voice handler uses different timeouts (60s/120s for combined download+transcribe) — the 30s timeout applies only to new file attachment handlers.
- PDF files are binary — the prompt template explicitly tells Claude to use CLI tools (`pdftotext`, `mutool`) rather than the Read tool.
- History logging for attachments includes the full prompt metadata, not just the filename.
