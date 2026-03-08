"""Integration tests for HistoryCompactor — realistic MD content, mocked SDK.

These tests verify that the full collect → filter → summarize pipeline works
correctly with realistic history file content matching EventRenderer output.
"""
from datetime import date, timedelta
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
from claude_agent_sdk import ResultMessage

from archon.ai.history_compactor import HistoryCompactor, _extract_responses

# ── Realistic MD content matching EventRenderer output ────────────────────

_REALISTIC_DAILY_LOG = """\
## 10:00:00 UTC · User 123456 · /workspace

Hello, please help me fix this bug.

### 🏷 Classification · 10:00:01 UTC

`{"intent": "task", "confidence": 0.97}`

### 🔀 Pipeline · 10:00:02 UTC

Routing: direct task response
Model: claude-sonnet-4-6

### 💭 Thinking · 10:00:03 UTC

Let me analyze the code to find the bug. I should read the relevant files first.

### 🔧 Tool: Read [t1] · 10:00:04 UTC

```
/workspace/main.py
```

### 📤 Result [t1] · 10:00:05 UTC

```
def main():
    pass
```

### 🔧 Tool: Bash [t2] · 10:00:06 UTC

```
pytest tests/
```

### 📤 Result [t2] · 10:00:07 UTC

```
FAILED tests/test_main.py::test_hello - AssertionError
```

### ✅ Response · 10:00:08 UTC

> User: "Hello, please help me fix this bug."

I found and fixed the bug. The `main()` function was empty. I've updated it to
print "Hello, World!" and all tests now pass.

---

## 10:10:00 UTC · User 123456 · /workspace

Can you also add documentation?

### 💭 Thinking · 10:10:01 UTC

The user wants documentation. Let me write a README.

### 🔧 Tool: Write [t3] · 10:10:02 UTC

```
README.md
```

### 📤 Result [t3] · 10:10:03 UTC

```
File written.
```

### ✅ Response · 10:10:04 UTC

> User: "Can you also add documentation?"

Done. I've created a README.md with usage instructions and examples.

---
"""

_AGENT_LOG = """\
# Agent: Harbor
**Type:** general-purpose
**Started:** 10:05:00 UTC

### 🔧 Tool: Glob [t1] · 10:05:01 UTC

```
**/*.py
```

### 📤 Result [t1] · 10:05:02 UTC

Suppressed (Glob): matched 12 files

### ✅ Response · 10:05:03 UTC

Found 12 Python files. Reviewed all of them and identified 3 that need updating.

## Completed
**Duration:** 45s
"""


def _sessions(tmp_path: Path) -> Path:
    """Return the sessions subdirectory, creating it if necessary."""
    d = tmp_path / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _mock_client(summary: str = "Summary of the day.") -> MagicMock:
    result_msg = ResultMessage(
        subtype="success", duration_ms=0, duration_api_ms=0,
        is_error=False, num_turns=1, session_id="test", result=summary,
    )

    async def _gen() -> AsyncGenerator[ResultMessage, None]:
        yield result_msg

    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.query = AsyncMock()
    client.receive_response = MagicMock(side_effect=lambda: _gen())
    return client


# ── extract_responses with realistic content ──────────────────────────────


def test_extract_responses_from_realistic_daily_log() -> None:
    result = _extract_responses(_REALISTIC_DAILY_LOG)

    # Responses preserved
    assert "I found and fixed the bug." in result
    assert "Done. I've created a README.md" in result

    # User context preserved
    assert '> User: "Hello, please help me fix this bug."' in result
    assert '> User: "Can you also add documentation?"' in result

    # Tool calls and thinking removed
    assert "Tool: Read" not in result
    assert "Tool: Bash" not in result
    assert "Tool: Write" not in result
    assert "pytest tests/" not in result
    assert "Let me analyze the code" not in result
    assert "Classification" not in result
    assert "Pipeline" not in result


def test_extract_responses_from_agent_log() -> None:
    result = _extract_responses(_AGENT_LOG)
    assert "Found 12 Python files." in result
    assert "Tool: Glob" not in result


def test_extract_responses_count_matches_response_events() -> None:
    """Number of extracted sections matches Response events in the log."""
    result = _extract_responses(_REALISTIC_DAILY_LOG)
    # Two response sections in the log
    assert result.count("### ✅ Response") == 2


# ── full compaction with realistic content ────────────────────────────────


async def test_compact_day_with_realistic_log(tmp_path: Path) -> None:
    day = date.today() - timedelta(days=1)
    (_sessions(tmp_path) / f"{day}.md").write_text(_REALISTIC_DAILY_LOG, encoding="utf-8")
    client = _mock_client("Good summary of a productive day.")
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c.compact_pending_days()

    compacted = tmp_path / "daily" / f"{day}-compacted.md"
    assert compacted.exists()
    assert "Good summary of a productive day." in compacted.read_text()


async def test_compact_day_api_receives_filtered_content(tmp_path: Path) -> None:
    """API call must receive only response sections, not tool calls."""
    day = date.today() - timedelta(days=1)
    (_sessions(tmp_path) / f"{day}.md").write_text(_REALISTIC_DAILY_LOG, encoding="utf-8")
    client = _mock_client("Summary")
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c.compact_pending_days()

    prompt = client.query.call_args.args[0]
    assert "I found and fixed the bug." in prompt
    assert "Done. I've created a README.md" in prompt
    assert "Tool: Read" not in prompt
    assert "Tool: Bash" not in prompt
    assert "pytest tests/" not in prompt
    assert "Let me analyze the code" not in prompt


async def test_compact_day_with_main_log_and_agent_log(tmp_path: Path) -> None:
    """Both main log and agent log responses are included in the summary."""
    day = date.today() - timedelta(days=1)
    (_sessions(tmp_path) / f"{day}.md").write_text(_REALISTIC_DAILY_LOG, encoding="utf-8")
    (_sessions(tmp_path) / f"{day}-10-05-Harbor.md").write_text(_AGENT_LOG, encoding="utf-8")
    client = _mock_client("Summary")
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c.compact_pending_days()

    prompt = client.query.call_args.args[0]
    # Main log responses
    assert "I found and fixed the bug." in prompt
    # Agent log responses
    assert "Found 12 Python files." in prompt
    # No tool calls from either
    assert "Tool: Glob" not in prompt
    assert "Tool: Read" not in prompt


async def test_compact_today_with_realistic_log(tmp_path: Path) -> None:
    today = date.today()
    (_sessions(tmp_path) / f"{today}.md").write_text(_REALISTIC_DAILY_LOG, encoding="utf-8")
    client = _mock_client("Today's work summary.")
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c.compact_today()

    partial = tmp_path / "daily" / f"{today}-partial.md"
    assert partial.exists()
    assert "Today's work summary." in partial.read_text()


async def test_compact_today_api_receives_filtered_content(tmp_path: Path) -> None:
    today = date.today()
    (_sessions(tmp_path) / f"{today}.md").write_text(_REALISTIC_DAILY_LOG, encoding="utf-8")
    client = _mock_client("Summary")
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c.compact_today()

    prompt = client.query.call_args.args[0]
    assert "I found and fixed the bug." in prompt
    assert "Tool: Bash" not in prompt
