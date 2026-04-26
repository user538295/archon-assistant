# Bug Investigation: fastembed TextCrossEncoder Import Error

**Date**: 2026-04-17  
**Error**: `cannot import name 'TextCrossEncoder' from 'fastembed' (/Users/manczg/.archon/app/.venv/lib/python3.12/site-packages/fastembed/__init__.py)`

---

## Root Cause

**`archon/search/reranker.py` line 31** contained (now fixed):
```python
from fastembed import TextCrossEncoder
```

The installed **fastembed 0.8.0** does NOT export `TextCrossEncoder` from its top-level `__init__.py`.

### fastembed 0.8.0 top-level exports (from `fastembed/__init__.py`):
```python
__all__ = [
    "TextEmbedding",
    "SparseTextEmbedding",
    "SparseEmbedding",
    "ImageEmbedding",
    "LateInteractionTextEmbedding",
    "LateInteractionMultimodalEmbedding",
]
```
`TextCrossEncoder` is NOT in `__all__`.

### Where it actually lives:
- **File**: `fastembed/rerank/cross_encoder/text_cross_encoder.py` line 16
- **Exported from**: `fastembed.rerank.cross_encoder.__init__.py` line 1
- **Correct import**: `from fastembed.rerank.cross_encoder import TextCrossEncoder`

### Version context:
- Installed: `fastembed==0.8.0` (confirmed via `fastembed-0.8.0.dist-info/METADATA`)
- Required in `pyproject.toml` line 25: `"fastembed>=0.8.0"`
- The API was restructured in 0.8.0 — `TextCrossEncoder` moved to the `rerank` submodule

---

## Impact

This breaks ALL search functionality that involves reranking. The `mcp__search__search` tool fails completely with this import error. The error occurs at import time (lazy import on first use), so it doesn't crash startup but kills the first search call.

---

## App Log Evidence

Check `/Users/manczg/.archon/logs/archon.log` around 18:20 UTC for `ImportError` traceback from the fastembed import.

---

## Options

### Option A: Fix the import path (Recommended)
Change `archon/search/reranker.py` line 31:
```python
# Before:
from fastembed import TextCrossEncoder
# After:
from fastembed.rerank.cross_encoder import TextCrossEncoder
```
**Pros**: One-line fix; uses the correct canonical API; works with fastembed 0.8.0+; keeps dependencies modern  
**Cons**: Requires updating one test mock target and one architecture doc reference; both are straightforward

### Option B: Pin fastembed to pre-0.8.0 version
Change `pyproject.toml`: `"fastembed>=0.0.1,<0.8.0"`

**Pros**: Preserves current import; no code change  
**Cons**: Locks to outdated version; misses bug fixes/improvements; creates maintenance debt; the older API may eventually vanish

### Option C: Compatibility shim
Add a local shim that tries both import paths:
```python
try:
    from fastembed import TextCrossEncoder
except ImportError:
    from fastembed.rerank.cross_encoder import TextCrossEncoder
```
**Pros**: Works across multiple fastembed versions gracefully  
**Cons**: Over-engineering for a single simple fix; the old import path won't come back

---

## Recommendation

**Option A**: Change the import in `archon/search/reranker.py` to `from fastembed.rerank.cross_encoder import TextCrossEncoder`. This is correct, minimal, and permanent.

Also update `tests/search/conftest.py` to inject `fastembed.rerank.cross_encoder` into `sys.modules`, update the patch target in `test_model_reranker_init_called_once_under_concurrent_predict`, and update `Documentation/Architecture/180_search_architecture.md` line 206.

**Note**: `embedder.py` (`from fastembed import TextEmbedding`) and `install.py` (`from fastembed import TextEmbedding`) are unaffected since `TextEmbedding` is correctly exported from `fastembed.__init__`.

**Files to modify:**
- `archon/search/reranker.py` line 31
- `tests/search/conftest.py` — inject fastembed.rerank.cross_encoder submodule into sys.modules
- `tests/search/test_reranker.py` — fix patch target + add regression test
- `tests/search/test_conftest.py` — verify submodule path is patched
- `Documentation/Architecture/180_search_architecture.md` line 206

---

## Resolution

**Status**: Fixed — 2026-04-17

All files above were modified. All 11 reranker tests pass (`uv run pytest tests/search/test_reranker.py tests/search/test_conftest.py --no-cov`).

A regression test (`test_model_reranker_uses_submodule_import_path`) was added to prevent reversion: it temporarily hides `TextCrossEncoder` from the top-level `fastembed` module and asserts `ModelReranker.predict()` still works via the submodule path. If the import in `reranker.py` is reverted to `from fastembed import TextCrossEncoder`, this test fails.
