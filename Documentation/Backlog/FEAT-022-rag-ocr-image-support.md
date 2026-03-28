# FEAT-022 — RAG OCR Image Support
**Purpose**: Enable raster image files (PNG, JPEG, TIFF, BMP, WebP) to be indexed by the RAG pipeline via docling's OCR engine, so text embedded in screenshots, scanned documents, and diagrams becomes searchable.
**Audience**: Archon operators who store or receive image files in indexed collections (e.g. `~/.archon/attachments/`, project folders with architecture screenshots).
**Status**: To Do

---

## Background

The RAG pipeline today skips all image extensions — they are listed in `_BINARY_EXTENSIONS` in `archon/rag/pipeline.py` and never passed to the parser. However, `docling` (already a hard dependency for PDF parsing) supports image input through the same `DocumentConverter` API and runs an OCR pipeline to extract text. Removing image extensions from the binary exclusion list and adding an `_parse_image` method to `DocumentParser` is the minimal change needed to unlock this capability.

The Archon attachment store (`~/.archon/attachments/`) regularly receives screenshots and photos sent by users via Telegram. These are currently invisible to RAG context injection.

## Goal

When a user adds a folder containing image files to `[rag] providers`, those images are OCR-processed by docling and their extracted text is chunked, embedded, and stored in LanceDB. Queries that match text visible in an image surface that image as a result. Non-readable images (photos with no text) produce an empty string and are silently skipped rather than raising an error.

---

## Scope

### In Scope
- Raster image formats: `.png`, `.jpg`, `.jpeg`, `.tiff`, `.tif`, `.bmp`, `.webp`
- OCR via `docling.DocumentConverter` (same as PDF path — no new dependency)
- Empty-result handling: images with no extractable text are skipped at ingest time
- Remove the indexed image extensions from `_BINARY_EXTENSIONS` in `pipeline.py`
- `.svg` is excluded — SVG is XML text, not a raster image, and is unsuitable for OCR. Parsing SVG as plain XML/text is a separate future feature.
- `.gif` is excluded — GIF files are typically animated; docling/Pillow would only OCR the first frame, producing misleading results. Rarely text-bearing. Excluded as not worth the risk.

### Out of Scope
- Vision LLM description (semantic understanding of photo content)
- `.gif`, `.ico`, `.svg` — remain in `_BINARY_EXTENSIONS`
- Re-indexing existing collections (sync is additive; existing docs untouched)
- Any UI or Telegram-side changes

---

## Acceptance criteria
- [ ] `DocumentParser.parse()` routes `.png`, `.jpg`, `.jpeg`, `.tiff`, `.tif`, `.bmp`, `.webp` through `_parse_image()` which calls `docling.DocumentConverter`
- [ ] Image extensions are removed from `_BINARY_EXTENSIONS` in `pipeline.py`
- [ ] A file whose OCR yields an empty string is skipped at ingest (no chunk stored, no error raised)
- [ ] A docling failure on an image raises `ParseError` with the file path and cause
- [ ] All existing parser and pipeline tests continue to pass
- [ ] New unit tests cover: successful OCR, empty OCR result, docling failure
- [ ] A PDF whose `export_to_markdown()` returns `None` or whitespace does not store a `"None"` document

---

## What does NOT change
- `_parse_pdf` — refactored in Task 1.1 to delegate to `_parse_with_docling`; its `str()` wrapper is removed (fixing the `None` → `"None"` bug) and it gains converter caching. The external signature and `ParseError` contract are unchanged.
- `DocumentChunker`, `Embedder`, `RagStore` — no changes
- `.gif`, `.ico`, `.svg` remain in `_BINARY_EXTENSIONS`
- The `ParseError` interface — same constructor and attributes
- All existing collection sync, search, and reranker logic

---

## Known limitations / accepted trade-offs
- **OCR quality**: docling OCR quality depends on image resolution and font clarity. Low-quality images may produce garbled text that is indexed but not useful — accepted; no quality gate added.
- **OCR backend required**: image OCR requires a functional OCR engine. Docling uses EasyOCR by default (requires PyTorch) or Tesseract (requires system binary + `TESSDATA_PREFIX`). Users relying on docling only for PDF text extraction may not have a working OCR backend. If docling is not installed or the OCR backend is missing, `_parse_image` raises `ParseError` consistently — same behavior as `_parse_pdf`. The `archon doctor` command does not currently check for OCR backend availability.
- **No OCR timeout**: `DocumentConverter().convert()` is called with default settings and no timeout. A corrupted or unusually large image may cause the OCR pipeline to run for an extended time. The call runs in `asyncio.to_thread()` so it does not block the event loop, but the thread pool slot is held until completion. A follow-up task should add a `document_timeout` parameter to the converter or a pre-ingest file-size check (e.g. skip files > 50 MB).
- **Empty-result flow**: `_parse_image` returns `""` for images with no extractable text → the chunker's `if not text or not text.strip(): return []` guard produces an empty chunk list → `ingest_file` returns `IngestResult(status="ok", chunks_created=0)` without touching the store. No duplicate guard needed in the parser.
- **Docling auto-detection**: `DocumentConverter()` is constructed with default settings, relying on docling's extension-based format auto-detection. Explicit `ImageFormatOption` configuration (OCR engine, language, GPU device) is a future enhancement — acceptable for the initial implementation.
- **Cached converter is not thread-safe**: `_parse_with_docling` does a check-then-set on `self._converter` without a lock. The current pipeline processes files sequentially so concurrent access cannot occur today. If parallel file ingestion is added in the future, a `threading.Lock` must guard the initialisation check in `_parse_with_docling`.

---

## Architecture

### Changes to existing modules

**`archon/rag/parser.py`**:
- Add `_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"}` constant
- Add routing branch in `parse()`: `elif suffix in _IMAGE_EXTENSIONS: fn = self._parse_image`
- Add `__init__` with `self._converter: DocumentConverter | None = None` — instance-level, not class-level, so each `DocumentParser` instance has its own converter
- Extract shared helper `_parse_with_docling(self, path: Path) -> str` — lazy-initialises `self._converter` on first call; used by both `_parse_pdf` and `_parse_image` to avoid repeated heavyweight `DocumentConverter` instantiation
- Add `_parse_image(self, path: Path) -> str` — delegates to `self._parse_with_docling(path)`
- Update `_parse_pdf` to delegate to `self._parse_with_docling(path)` instead of constructing a fresh converter
- Update module docstring to list image formats

**`archon/rag/pipeline.py`**:
- Remove `.png`, `.jpg`, `.jpeg`, `.tiff`, `.tif`, `.bmp`, `.webp` from `_BINARY_EXTENSIONS`
- `.gif`, `.ico`, `.svg` remain excluded

### Data flow

```
ingest_directory()
  → file.suffix in _BINARY_EXTENSIONS?  → skip (.gif, .ico, .svg, ...)
  → DocumentParser.parse(file)
      → suffix in _IMAGE_EXTENSIONS → _parse_image → DocumentConverter → markdown text
      → text == ""  → ingest_file returns early (no chunk stored)
      → text != ""  → DocumentChunker → Embedder → RagStore
```

### No new config keys, env vars, or API changes.

---

## Tests

- **`test_parser_image_calls_docling`** (unit): `.png` routed to `_parse_image`, `DocumentConverter` called, result returned
- **`test_parser_image_empty_ocr_returns_empty_string`** (unit): `DocumentConverter` returns whitespace-only markdown → `_parse_image` returns `""`
- **`test_parser_image_docling_failure_raises_parse_error`** (unit): `DocumentConverter.convert()` raises → `ParseError` with correct `path` and `cause`
- **`test_parser_all_image_extensions_routed`** (unit): parametrize over all 7 extensions — each routes to `_parse_image`
- **`test_parser_image_none_ocr_returns_empty_string`** (unit): mock `export_to_markdown()` returns `None` → assert `parse()` returns `""`
- **`test_parser_image_corrupt_file_raises_parse_error`** (unit): zero-byte or corrupt `.png`; mock `DocumentConverter.convert()` raises `ValueError`; assert `ParseError` with `.path` and `.cause`
- **`test_parser_converter_reused_across_calls`** (unit): call `parse()` twice on different images; assert `DocumentConverter()` instantiated exactly once
- **`test_parser_pdf_none_ocr_returns_empty_string`** (unit): mock `export_to_markdown()` returns `None` → assert `parse("doc.pdf")` returns `""` (not `"None"`)
- **`test_parser_pdf_whitespace_returns_empty_string`** (unit): mock `export_to_markdown()` returns `"  \n  "` → assert `parse("doc.pdf")` returns `""`
- **`test_pipeline_image_extensions_not_in_binary`** (unit): assert none of the 7 image extensions appear in `_BINARY_EXTENSIONS`
- **`test_pipeline_ingest_directory_includes_png`** (integration): `.png` in tmp dir reaches `ingest_file` (parser mocked to return text); assert `ingest_file` called AND `result.status == "ok"` AND `result.chunks_created > 0`
- **`test_pipeline_ingest_directory_skips_binary_image`** (integration): parametrize over `.gif`, `.svg`, `.ico`; for each, assert `ingest_file` is NOT called
- **`test_pipeline_ingest_image_empty_ocr_produces_no_chunk`** (integration): parser returns `""` for `.png`; assert `IngestResult.chunks_created == 0` and `IngestResult.status == "ok"` (the skip is in the chunker returning `[]`, not by store interaction)
- **`test_pipeline_ingest_directory_skips_binary_extensions`** updated — gif/exe used as binary fixture instead of png

---

## Documentation update
- [ ] `archon/rag/parser.py` module docstring, section: Supported formats — add image formats line
- [ ] `Documentation/UserManual/rag_guide.md`, section: Supported file types — add image row to table

---

## Task breakdown

### Phase 1 — Parser image routing
> **Releasable**: after Task 1.1 — `DocumentParser` can OCR images; pipeline still skips them until Task 2.1.

#### Task 1.1 — Add `_IMAGE_EXTENSIONS` constant and `_parse_image` method to `DocumentParser`
- [ ] **File**: `archon/rag/parser.py`
- **Depends on**: nothing
- **Description**:
  - Add module-level constant:
    ```python
    _IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"}
    ```
  - Add routing branch in `parse()` after the `_OFFICE_EXTENSIONS` check and before the `else` plain-text fallback. The full routing order must be: HTML → PDF → Office → Image → plain-text (else):
    ```python
    elif suffix in _IMAGE_EXTENSIONS:
        fn = self._parse_image
    ```
  - Add `__init__` to `DocumentParser` with an instance-level converter field (not class-level — avoids sharing across instances and makes intent explicit):
    ```python
    def __init__(self) -> None:
        self._converter: DocumentConverter | None = None  # lazy-initialised on first docling call
    ```
  - Add shared helper (eliminates duplication between PDF and image paths; caches the heavy `DocumentConverter` across calls on this instance):
    ```python
    def _parse_with_docling(self, path: Path) -> str:
        try:
            from docling.document_converter import DocumentConverter  # noqa: PLC0415
            if self._converter is None:
                self._converter = DocumentConverter()
            result = self._converter.convert(str(path)).document.export_to_markdown()
            return result.strip() if result else ""
        except Exception as exc:
            raise ParseError(path, exc) from exc
    ```
  - Add thin delegating methods:
    ```python
    def _parse_image(self, path: Path) -> str:
        return self._parse_with_docling(path)
    ```
  - Fix `_parse_pdf` — remove the `str()` wrapper (current code calls `str(export_to_markdown())` which converts `None` to `"None"`) and delegate to the shared helper:
    ```python
    def _parse_pdf(self, path: Path) -> str:
        return self._parse_with_docling(path)
    ```
  - Update module docstring to add: `- Images: .png, .jpg, .jpeg, .tiff, .tif, .bmp, .webp — via docling OCR`
- **Releasable**: `DocumentParser` can now OCR image files when called directly.
- **Tests (TDD)** — `tests/rag/test_parser.py`:
  - Unit: `test_parser_image_calls_docling` — mock `DocumentConverter`, assert called with image path, assert text returned
  - Unit: `test_parser_image_empty_ocr_returns_empty_string` — mock returns `"   \n  "` → assert `parse()` returns `""`
  - Unit: `test_parser_image_none_ocr_returns_empty_string` — mock `export_to_markdown()` returns `None` → assert `parse()` returns `""`
  - Unit: `test_parser_image_docling_failure_raises_parse_error` — `DocumentConverter.convert()` raises `RuntimeError` → `ParseError` with correct `.path` and `.cause`
  - Unit: `test_parser_image_corrupt_file_raises_parse_error` — zero-byte or corrupt `.png`; mock `DocumentConverter.convert()` raises `ValueError`; assert `ParseError` with `.path` and `.cause`
  - Unit: `test_parser_all_image_extensions_routed` — parametrize `.png .jpg .jpeg .tiff .tif .bmp .webp`; mock `_parse_image`; assert called for each
  - Unit: `test_parser_converter_reused_across_calls` — call `parse()` twice on different images; assert `DocumentConverter()` constructor called exactly once (lazy caching works)
  - Unit: `test_parser_pdf_none_ocr_returns_empty_string` — mock `export_to_markdown()` returns `None` → assert `parse("doc.pdf")` returns `""` (not `"None"`)
  - Unit: `test_parser_pdf_whitespace_returns_empty_string` — mock `export_to_markdown()` returns `"  \n  "` → assert `parse("doc.pdf")` returns `""`
  - Checkpoint: `uv run pytest tests/rag/test_parser.py -v`

---

### Phase 2 — Pipeline binary exclusion update
> **Releasable**: after Task 2.1 — full ingest pipeline indexes image files end-to-end.

#### Task 2.1 — Remove image extensions from `_BINARY_EXTENSIONS`
- [ ] **File**: `archon/rag/pipeline.py`
- **Depends on**: Task 1.1
- **Description**:
  - Remove from `_BINARY_EXTENSIONS`:
    `.png`, `.jpg`, `.jpeg`, `.tiff`, `.tif`, `.bmp`, `.webp`
  - `.gif`, `.ico`, `.svg` must remain in the set
  - No other changes to `pipeline.py`
  - Update existing test `test_pipeline_ingest_directory_skips_binary_extensions` in `tests/rag/test_pipeline.py` — change the binary file in that test from `.png` to `.gif` (or `.exe`) since `.png` will no longer be skipped
- **Releasable**: `ingest_directory` now passes image files to `DocumentParser` and they flow through the full ingest pipeline.
- **Tests (TDD)** — `tests/rag/test_pipeline.py`:
  - Unit: `test_pipeline_image_extensions_not_in_binary` — assert each of the 7 extensions is absent from `_BINARY_EXTENSIONS`
  - Unit: `test_pipeline_gif_svg_ico_remain_binary` — assert `.gif`, `.svg`, `.ico` are still in `_BINARY_EXTENSIONS`
  - Integration: `test_pipeline_ingest_directory_includes_png` — tmp dir with a `.png`; parser mocked to return `"ocr text"`; assert `ingest_file` called AND `result.status == "ok"` AND `result.chunks_created > 0`
  - Integration: `test_pipeline_ingest_directory_skips_binary_image` — parametrize over `.gif`, `.svg`, `.ico`; for each, place that file in a tmp dir and assert `ingest_file` is NOT called
  - Integration: `test_pipeline_ingest_image_empty_ocr_produces_no_chunk` — parser returns `""` for `.png`; assert `IngestResult.chunks_created == 0` and `IngestResult.status == "ok"` (the skip is in the chunker returning `[]`, not by store interaction)
  - `test_pipeline_ingest_directory_skips_binary_extensions` updated — gif/exe used as binary fixture instead of png
  - Checkpoint: `uv run pytest tests/rag/test_pipeline.py -v`

---

### Phase 3 — Documentation
> **Releasable**: after Task 3.1 — user-facing docs reflect image support.

#### Task 3.1 — Update RAG user guide and parser docstring
- [ ] **File**: `Documentation/UserManual/rag_guide.md`
- [ ] **File**: `archon/rag/parser.py` (docstring only — already done in Task 1.1)
- **Depends on**: Task 2.1
- **Description**:
  - In `rag_guide.md`, find the supported file types section and add a row for images:
    `| Images | .png, .jpg, .jpeg, .tiff, .tif, .bmp, .webp | OCR via docling — text visible in the image is extracted |`
  - Note that `.gif`, `.svg`, `.ico` are not supported
  - No other doc changes required
- **Releasable**: users reading the guide know image files are indexed.
- **Tests (TDD)**: N/A — documentation task.
  - Checkpoint: N/A
