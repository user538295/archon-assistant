"""Live E2E tests for HistoryCompactor — real Haiku API calls.

These tests make actual Anthropic API calls and write real files.
Requires ANTHROPIC_API_KEY in the environment.
Excluded from default test runs (marked @pytest.mark.live).
"""
from datetime import date, timedelta
from pathlib import Path

import pytest

from archon.ai.history_compactor import HistoryCompactor, _extract_responses

_SAMPLE_LOG = """\
## 10:00:00 UTC · User 42 · /workspace

Please explain what a Python generator is.

### 💭 Thinking · 10:00:01 UTC

The user wants a conceptual explanation. I'll keep it concise.

### ✅ Response · 10:00:02 UTC

> User: "Please explain what a Python generator is."

A Python generator is a function that uses `yield` instead of `return` to
produce values lazily. It pauses execution at each `yield`, resuming where it
left off on the next call. This makes generators memory-efficient for large
sequences since values are produced one at a time.

Example:
```python
def count_up(n):
    for i in range(n):
        yield i
```

---

## 10:05:00 UTC · User 42 · /workspace

Can you show a practical use case?

### 🔧 Tool: Read [t1] · 10:05:01 UTC

```
/workspace/examples.py
```

### 📤 Result [t1] · 10:05:02 UTC

```
# file not found
```

### ✅ Response · 10:05:03 UTC

> User: "Can you show a practical use case?"

A common use case is reading large files line by line:

```python
def read_large_file(path):
    with open(path) as f:
        for line in f:
            yield line.strip()
```

This avoids loading the entire file into memory at once.

---
"""


@pytest.mark.live
async def test_live_extract_responses_produces_filtered_content(tmp_path: Path) -> None:
    """Verify _extract_responses filters correctly before any API call."""
    result = _extract_responses(_SAMPLE_LOG)

    assert "A Python generator is a function" in result
    assert "A common use case is reading large files" in result
    assert "Tool: Read" not in result
    assert "file not found" not in result
    assert "The user wants a conceptual explanation" not in result


@pytest.mark.live
async def test_live_compact_today_calls_real_api(tmp_path: Path) -> None:
    """compact_today calls real Haiku API and produces a valid partial summary."""
    today = date.today()
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / f"{today}.md").write_text(_SAMPLE_LOG, encoding="utf-8")
    c = HistoryCompactor(str(tmp_path), context_days=2)

    await c.compact_today()

    partial = tmp_path / "daily" / f"{today}-partial.md"
    assert partial.exists()
    text = partial.read_text(encoding="utf-8")
    assert f"# {today} — Daily Summary" in text
    # Real Haiku should produce meaningful content mentioning generators
    content_lower = text.lower()
    assert any(kw in content_lower for kw in ["generator", "python", "yield", "user"])


@pytest.mark.live
async def test_live_compact_pending_day_calls_real_api(tmp_path: Path) -> None:
    """compact_pending_days calls real Haiku API for a past day."""
    day = date.today() - timedelta(days=1)
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / f"{day}.md").write_text(_SAMPLE_LOG, encoding="utf-8")
    c = HistoryCompactor(str(tmp_path), context_days=2)

    await c.compact_pending_days()

    compacted = tmp_path / "daily" / f"{day}-compacted.md"
    assert compacted.exists()
    text = compacted.read_text(encoding="utf-8")
    assert f"# {day} — Daily Summary" in text
    assert len(text) > 100  # real Haiku response should be substantial


@pytest.mark.live
async def test_live_filtered_content_improves_summary_quality(tmp_path: Path) -> None:
    """Filtered content (responses only) produces a coherent summary.

    Verifies that Haiku can produce a meaningful summary from response-only
    content — the summary should mention the key topics discussed.
    """
    today = date.today()
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / f"{today}.md").write_text(_SAMPLE_LOG, encoding="utf-8")
    c = HistoryCompactor(str(tmp_path), context_days=2)

    await c.compact_today()

    partial = tmp_path / "daily" / f"{today}-partial.md"
    text = partial.read_text(encoding="utf-8").lower()
    # The summary should reference the main topic (generators)
    assert "generator" in text or "python" in text
