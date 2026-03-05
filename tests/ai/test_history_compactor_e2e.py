"""E2E tests for HistoryCompactor — full pipeline with mocked API.

Exercises the complete compaction flow: file discovery → filtering →
summarization → output file writing, using mocked Haiku API calls.
"""
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from archon.ai.history_compactor import HistoryCompactor

_DAILY_LOG = """\
## 09:00:00 UTC · User 111 · /work

Fix the login bug.

### 💭 Thinking · 09:00:01 UTC

I need to check the auth module.

### 🔧 Tool: Read [t1] · 09:00:02 UTC

```
/work/auth.py
```

### 📤 Result [t1] · 09:00:03 UTC

```
def login(user): return True  # BUG: always returns True
```

### ✅ Response · 09:00:04 UTC

> User: "Fix the login bug."

Fixed. The `login()` function now properly validates credentials.

---

## 09:15:00 UTC · User 111 · /work

Add unit tests for the fix.

### 🔧 Tool: Write [t2] · 09:15:01 UTC

```
tests/test_auth.py
```

### 📤 Result [t2] · 09:15:02 UTC

```
File written.
```

### ✅ Response · 09:15:03 UTC

> User: "Add unit tests for the fix."

Done. Added 5 unit tests covering happy path and edge cases. All pass.

---
"""


def _sessions(tmp_path: Path) -> Path:
    """Return the sessions subdirectory, creating it if necessary."""
    d = tmp_path / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _mock_client(text: str = "Mock summary.") -> MagicMock:
    message = MagicMock()
    message.content = [MagicMock(text=text)]
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=message)
    return client


# ── Full compaction pipeline ───────────────────────────────────────────────


async def test_e2e_compact_past_day_produces_output_file(tmp_path: Path) -> None:
    day = date.today() - timedelta(days=1)
    (_sessions(tmp_path) / f"{day}.md").write_text(_DAILY_LOG, encoding="utf-8")
    client = _mock_client("Summary: fixed login bug and added tests.")
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c.compact_pending_days()

    out = tmp_path / "daily" / f"{day}-compacted.md"
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert f"# {day} — Daily Summary" in text
    assert "Summary: fixed login bug and added tests." in text


async def test_e2e_compact_today_produces_partial_file(tmp_path: Path) -> None:
    today = date.today()
    (_sessions(tmp_path) / f"{today}.md").write_text(_DAILY_LOG, encoding="utf-8")
    client = _mock_client("Today: fixed auth and tests.")
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c.compact_today()

    out = tmp_path / "daily" / f"{today}-partial.md"
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert f"# {today} — Daily Summary" in text
    assert "Today: fixed auth and tests." in text


async def test_e2e_only_response_content_reaches_haiku(tmp_path: Path) -> None:
    day = date.today() - timedelta(days=1)
    (_sessions(tmp_path) / f"{day}.md").write_text(_DAILY_LOG, encoding="utf-8")
    client = _mock_client("Summary")
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c.compact_pending_days()

    prompt = client.messages.create.call_args.kwargs["messages"][0]["content"]
    # Response content is present
    assert "Fixed. The `login()` function" in prompt
    assert "Added 5 unit tests" in prompt
    # Tool calls and internal events are absent
    assert "Tool: Read" not in prompt
    assert "Tool: Write" not in prompt
    assert "I need to check the auth module" not in prompt
    assert "always returns True" not in prompt  # from tool result


async def test_e2e_context_injection_from_compacted_files(tmp_path: Path) -> None:
    """get_recent_context returns compacted summaries ready for session injection."""
    today = date.today()
    yesterday = today - timedelta(days=1)
    two_ago = today - timedelta(days=2)

    daily = tmp_path / "daily"
    daily.mkdir()
    (daily / f"{two_ago}-compacted.md").write_text("Day -2 summary.", encoding="utf-8")
    (daily / f"{yesterday}-compacted.md").write_text("Day -1 summary.", encoding="utf-8")
    (daily / f"{today}-partial.md").write_text("Today partial summary.", encoding="utf-8")

    c = HistoryCompactor(str(tmp_path), context_days=2, client=_mock_client())
    ctx = c.get_recent_context()

    assert ctx is not None
    assert "Day -2 summary." in ctx
    assert "Day -1 summary." in ctx
    assert "Today partial summary." in ctx
    # Oldest first
    assert ctx.index("Day -2") < ctx.index("Day -1") < ctx.index("Today partial")


async def test_e2e_multi_day_compaction(tmp_path: Path) -> None:
    """Three past days are all compacted in one run."""
    today = date.today()
    for i in range(1, 4):
        day = today - timedelta(days=i)
        (_sessions(tmp_path) / f"{day}.md").write_text(_DAILY_LOG, encoding="utf-8")
    client = _mock_client("Day summary.")
    c = HistoryCompactor(str(tmp_path), context_days=3, client=client)

    await c.compact_pending_days()

    assert client.messages.create.call_count == 3
    for i in range(1, 4):
        day = today - timedelta(days=i)
        assert (tmp_path / "daily" / f"{day}-compacted.md").exists()


async def test_e2e_idempotent_compaction(tmp_path: Path) -> None:
    """Running compact_pending_days twice does not re-compact already-done days."""
    day = date.today() - timedelta(days=1)
    (_sessions(tmp_path) / f"{day}.md").write_text(_DAILY_LOG, encoding="utf-8")
    client = _mock_client("Summary")
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c.compact_pending_days()
    await c.compact_pending_days()  # second run — should do nothing

    assert client.messages.create.call_count == 1


async def test_e2e_startup_prompt_contains_history_structure(tmp_path: Path) -> None:
    c = HistoryCompactor(str(tmp_path), context_days=2, client=_mock_client())
    prompt = c.startup_context_prompt()

    assert str(tmp_path) in prompt
    assert "YYYY-MM-DD.md" in prompt
    assert "compacted.md" in prompt
    assert "partial.md" in prompt
    assert date.today().isoformat() in prompt
