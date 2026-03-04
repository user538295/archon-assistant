"""History compactor — summarises past daily history files using Claude Haiku."""
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic

logger = logging.getLogger("archon")

_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_MAX_CONTENT_CHARS = 100_000  # truncate very large histories before sending

_SUMMARY_PROMPT = """\
You are summarizing a daily conversation log between a user and an AI assistant (Archon).

Below is the full history for {date}. It may include tool calls, agent sub-task logs, and \
assistant responses.

Create a concise ~1000-word English summary that covers:
1. The main user requests and goals for this day
2. Key decisions, outcomes, and results achieved
3. Important context that would help continue the work in future sessions

Focus on user requests and assistant responses. Skip low-level tool calls and \
intermediate steps unless they reveal important context.

Write in English, past tense. Start directly with the summary content.

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


class HistoryCompactor:
    """Compacts past daily history files into ~1000-word summaries using Haiku.

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
        client: "AsyncAnthropic | None" = None,
    ) -> None:
        self._dir = Path(history_dir).expanduser()
        self._daily_dir = self._dir / "daily"
        self._context_days = context_days
        self._model = model
        if client is not None:
            self._client: "AsyncAnthropic" = client
        else:
            from anthropic import AsyncAnthropic
            self._client = AsyncAnthropic()

    async def compact_pending_days(self) -> None:
        """Compact all uncompacted past days.

        Skips today's file (still in-progress) and any day that already has
        a compacted file.  API errors for individual days are logged and
        swallowed so other days are still processed.
        """
        if not self._dir.exists():
            return
        today = date.today()
        for md_file in sorted(self._dir.glob("*.md")):
            if not _is_daily_file(md_file.name):
                continue
            try:
                file_date = date.fromisoformat(md_file.stem)
            except ValueError:
                continue
            if file_date >= today:
                continue
            if (self._daily_dir / f"{file_date}-compacted.md").exists():
                continue
            try:
                await self._compact_day(file_date)
            except Exception:
                logger.warning("Failed to compact history for %s", file_date, exc_info=True)

    async def _compact_day(self, day: date) -> None:
        """Generate and save a compacted summary for a single day."""
        content = self._collect_day_content(day)
        if not content.strip():
            return
        if len(content) > _MAX_CONTENT_CHARS:
            content = content[:_MAX_CONTENT_CHARS]
        logger.info("Compacting history for %s (%d chars)", day, len(content))
        summary = await self._summarize(content, day)
        self._daily_dir.mkdir(parents=True, exist_ok=True)
        out_path = self._daily_dir / f"{day}-compacted.md"
        out_path.write_text(summary, encoding="utf-8")
        logger.info("Compacted history saved: %s", out_path)

    def _collect_day_content(self, day: date) -> str:
        """Collect main conversation file + agent logs for *day* into one string."""
        parts: list[str] = []
        main_file = self._dir / f"{day}.md"
        if main_file.exists():
            parts.append(main_file.read_text(encoding="utf-8"))
        for agent_log in sorted(self._dir.glob(f"{day}-??-??-*.md")):
            parts.append(agent_log.read_text(encoding="utf-8"))
        return "\n\n---\n\n".join(parts)

    async def _summarize(self, content: str, day: date) -> str:
        """Call Haiku to produce a summary; return formatted Markdown."""
        prompt = _SUMMARY_PROMPT.format(date=day.isoformat(), content=content)
        message = await self._client.messages.create(
            model=self._model,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text  # type: ignore[union-attr]
        return f"# {day.isoformat()} — Daily Summary\n\n{text}\n"

    def get_recent_context(self) -> str | None:
        """Return the last *context_days* compacted summaries as a single string.

        Returns ``None`` if no compacted summaries exist for the relevant days.
        Summaries are ordered oldest-first so the most recent context appears last.
        """
        today = date.today()
        parts: list[str] = []
        for i in range(self._context_days, 0, -1):
            target = today - timedelta(days=i)
            compacted = self._daily_dir / f"{target}-compacted.md"
            if compacted.exists():
                parts.append(compacted.read_text(encoding="utf-8"))
        return "\n\n---\n\n".join(parts) if parts else None
