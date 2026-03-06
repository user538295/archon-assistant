"""History compactor — summarises past daily history files using Claude Haiku."""

import logging
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("archon")

_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_MAX_CONTENT_CHARS = 600_000  # safety limit after filtering (~150K tokens for Haiku)
_COMPACTED_SUFFIX = "-compacted.md"
_PARTIAL_SUFFIX = "-partial.md"

_SUMMARY_PROMPT = """\
You are summarizing a daily conversation log between a user and an AI assistant (Archon).

Below is the full history for {date}. It may include tool calls, agent sub-task logs, and \
assistant responses.

Create a concise ~3000-word English summary that covers:
1. The main user requests and goals for this day
2. Key decisions, outcomes, and results achieved
3. Important context that would help continue the work in future sessions

Focus on user requests and assistant responses. Skip low-level tool calls and \
intermediate steps unless they reveal important context.

Write in English, past tense. Use exactly the following structure:

# {date} — Daily Summary

## Main User Requests and Goals

### 1. [Topic] ([HH:MM UTC])

## Key Decisions and Outcomes

### Completed Tasks

### Incomplete Tasks

## Important Context for Future Sessions

## Other Notes

History:
{content}"""


def _is_daily_file(name: str) -> bool:
    """Return True for ``YYYY-MM-DD.md`` files (not agent logs or other files)."""
    if not name.endswith(".md"):
        return False
    stem = name[:-3]
    if len(stem) != 10:
        return False
    try:
        date.fromisoformat(stem)
        return True
    except ValueError:
        return False


def _extract_responses(content: str) -> str:
    """Extract only ``✅ Response`` sections from a history MD file.

    Response sections (rendered by :class:`~archon.ai.event_renderer.EventRenderer`)
    contain the user question as a blockquote and the assistant response — the
    minimum required for a meaningful daily summary.  Tool calls, thinking
    blocks, routing events, and all other internal events are excluded.

    Returns an empty string if no response sections are found.
    """
    sections = re.findall(r"(### ✅ Response.*?)(?=\n### |\Z)", content, re.DOTALL)
    return "\n\n---\n\n".join(s.strip() for s in sections) if sections else ""


class HistorySummarizer:
    """Owns the LLM call to produce a daily summary via the Claude SDK."""

    def __init__(self, model: str, client: Any = None) -> None:
        self._model = model
        self._client = client  # SDK-style client; None means create fresh per call

    async def summarize(self, content: str, day: date) -> str:
        """Call Claude via SDK and return formatted Markdown summary."""
        prompt = _SUMMARY_PROMPT.format(date=day.isoformat(), content=content)
        if self._client is not None:
            sdk_client = self._client
        else:
            from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
            sdk_client = ClaudeSDKClient(options=ClaudeAgentOptions(
                permission_mode="bypassPermissions",
                model=self._model,
                max_turns=1,
            ))
        await sdk_client.connect()
        try:
            await sdk_client.query(prompt)
            from claude_agent_sdk import ResultMessage
            async for msg in sdk_client.receive_response():
                if isinstance(msg, ResultMessage):
                    return self._format(msg.result or "", day)
        finally:
            await sdk_client.disconnect()
        return self._format("", day)

    def _format(self, text: str, day: date) -> str:
        summary = text.strip()
        required_heading = f"# {day.isoformat()} — Daily Summary"
        heading_pattern = rf"(?m)^#\s+{re.escape(day.isoformat())}\s+—\s+Daily Summary\s*$"
        if not re.search(heading_pattern, summary):
            summary = f"{required_heading}\n\n{summary}" if summary else required_heading
        return f"{summary}\n"


class HistoryCompactor:
    """Compacts past daily history files into ~3000-word summaries using Haiku.

    On each call to :meth:`compact_pending_days`, it scans the history directory
    for ``YYYY-MM-DD.md`` files that have no corresponding compacted version yet
    (``daily/YYYY-MM-DD-compacted.md``) and generates one via the Haiku model.

    :meth:`get_recent_context` returns the last *context_days* compacted summaries
    as a single string suitable for injection into a new Claude session.
    """

    def __init__(
        self,
        history_dir: str,
        context_days: int = 2,
        model: str = _HAIKU_MODEL,
        client: Any = None,
    ) -> None:
        self._dir = Path(history_dir).expanduser()
        self._sessions_dir = self._dir / "sessions"
        self._daily_dir = self._dir / "daily"
        self._context_days = context_days
        self._summarizer = HistorySummarizer(model, client)

    async def compact_pending_days(self) -> None:
        """Compact all uncompacted past days.

        Skips today's file (still in-progress) and any day that already has
        a compacted file.  API errors for individual days are logged and
        swallowed so other days are still processed.
        """
        if not self._sessions_dir.exists():
            return
        today = date.today()
        for md_file in sorted(self._sessions_dir.glob("*.md")):
            if not _is_daily_file(md_file.name):
                continue
            try:
                file_date = date.fromisoformat(md_file.stem)
            except ValueError:
                continue
            if file_date >= today:
                continue
            if (self._daily_dir / f"{file_date}{_COMPACTED_SUFFIX}").exists():
                continue
            try:
                await self._compact_day(file_date)
            except Exception:
                logger.warning(
                    "Failed to compact history for %s", file_date, exc_info=True
                )

    async def compact_today(self) -> None:
        """Compact today's history into a partial summary (always overwrites).

        Saves to ``daily/YYYY-MM-DD-partial.md``.  Called at daemon startup so
        the current day's work is available for context injection even though
        the day is not yet complete.
        """
        today = date.today()
        out_path = self._daily_dir / f"{today}{_PARTIAL_SUFFIX}"
        await self._run_compaction(today, out_path)

    async def _compact_day(self, day: date) -> None:
        """Generate and save a compacted summary for a single past day."""
        out_path = self._daily_dir / f"{day}{_COMPACTED_SUFFIX}"
        await self._run_compaction(day, out_path)
        partial = self._daily_dir / f"{day}{_PARTIAL_SUFFIX}"
        if partial.exists():
            partial.unlink()
            logger.debug("Removed stale partial file: %s", partial)

    async def _run_compaction(self, day: date, out_path: Path) -> None:
        """Collect, filter, and summarize history for *day*, writing to *out_path*."""
        content = self._collect_day_content(day)
        if not content.strip():
            return
        filtered = _extract_responses(content)
        if not filtered:
            logger.debug("No response sections found in history for %s — skipping", day)
            return
        filtered = self._apply_size_limit(filtered, day)
        logger.info("Compacting history for %s (%d chars)", day, len(filtered))
        summary = await self._summarizer.summarize(filtered, day)
        self._daily_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_text(summary, encoding="utf-8")
        logger.info("Compacted history saved: %s", out_path)

    def _collect_day_content(self, day: date) -> str:
        """Collect main conversation file + agent logs for *day* into one string."""
        parts: list[str] = []
        main_file = self._sessions_dir / f"{day}.md"
        if main_file.exists():
            parts.append(main_file.read_text(encoding="utf-8"))
        for agent_log in sorted(self._sessions_dir.glob(f"{day}-??-??-*.md")):
            parts.append(agent_log.read_text(encoding="utf-8"))
        return "\n\n---\n\n".join(parts)

    def _apply_size_limit(self, content: str, day: date) -> str:
        """Apply tail-truncation if *content* exceeds ``_MAX_CONTENT_CHARS``.

        Keeps the most recent (tail) content so that the latest conversations
        are always included even when an older portion is dropped.
        """
        if len(content) > _MAX_CONTENT_CHARS:
            logger.warning(
                "Filtered history for %s is very large (%d chars) — "
                "tail-truncating to %d chars",
                day,
                len(content),
                _MAX_CONTENT_CHARS,
            )
            return content[-_MAX_CONTENT_CHARS:]
        return content

    def get_recent_context(self) -> str | None:
        """Return the last *context_days* compacted summaries plus today's partial.

        Returns ``None`` if no files exist for the relevant days.
        Summaries are ordered oldest-first; today's partial (if present) is last.
        """
        today = date.today()
        parts: list[str] = []
        for i in range(self._context_days, 0, -1):
            target = today - timedelta(days=i)
            compacted = self._daily_dir / f"{target}{_COMPACTED_SUFFIX}"
            if compacted.exists():
                parts.append(compacted.read_text(encoding="utf-8"))
        partial = self._daily_dir / f"{today}{_PARTIAL_SUFFIX}"
        if partial.exists():
            parts.append(partial.read_text(encoding="utf-8"))
        return "\n\n---\n\n".join(parts) if parts else None

    def startup_context_prompt(self, qmd_enabled: bool = False) -> str:
        """Return a meta-prompt explaining the history structure to the LLM.

        Injected into every new session so the model knows how to navigate
        conversation history without the user having to re-explain it.
        """
        today = date.today().isoformat()
        h = str(self._dir)
        s = str(self._sessions_dir)
        qmd_section = (
            "\n\nThe QMD tools (qmd_deep_search / qmd_vector_search) provide fast "
            "semantic search over the full history — use them when looking for a "
            "specific topic instead of reading individual files."
            if qmd_enabled
            else ""
        )
        return (
            f"## Conversation history\n\n"
            f"All past conversations with the user are stored under: {h}\n\n"
            f"File structure:\n"
            f"- `{s}/YYYY-MM-DD.md`                  — full verbose daily log"
            f" (every tool call, thinking, response)\n"
            f"- `{s}/YYYY-MM-DD-HH-MM-<name>.md`     — per-agent-run log for"
            f" background agent tasks\n"
            f"- `{h}/daily/YYYY-MM-DD-compacted.md`  — ~3000-word daily summary"
            f" (fast to read; covers user requests and outcomes)\n"
            f"- `{h}/daily/YYYY-MM-DD-partial.md`    — today's in-progress summary\n\n"
            f"Today is {today}.\n\n"
            f"When the user references past work, read the daily summaries first."
            f" Read the full daily log only when you need tool-level detail.\n\n"
            f"Today's partial summary (`{h}/daily/{today}-partial.md`) is generated"
            f" at daemon startup and may still be in progress. If the user asks about"
            f" today's work and the partial file does not yet exist,"
            f" read `{s}/{today}.md` directly."
            f"{qmd_section}\n\n"
            f"Use this history proactively to maintain continuity without the user"
            f" having to re-explain context."
        )
