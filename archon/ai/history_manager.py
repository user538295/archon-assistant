"""History manager — persists chat interactions to daily Markdown files."""
import asyncio
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from archon.ai.event_mapper import Event
from archon.ai.event_renderer import EventRenderer

log = logging.getLogger("archon")


def _migrate_legacy_files(history_dir: Path) -> None:
    """Move root-level YYYY-MM-DD.md files into the sessions/ subdirectory."""
    sessions_dir = history_dir / "sessions"
    for md_file in sorted(history_dir.glob("*.md")):
        try:
            date.fromisoformat(md_file.stem)
        except ValueError:
            continue
        dest = sessions_dir / md_file.name
        sessions_dir.mkdir(parents=True, exist_ok=True)
        md_file.rename(dest)
        log.info("Migrated legacy history file: %s → %s", md_file, dest)


class HistoryManager:
    """Writes conversation turns to ~/.archon/history/sessions/YYYY-MM-DD.md."""

    def __init__(
        self,
        directory: str,
        suppressed_tools: frozenset[str] | None = None,
    ) -> None:
        self._dir = Path(directory).expanduser() / "sessions"
        self._dir.mkdir(parents=True, exist_ok=True)
        _migrate_legacy_files(self._dir.parent)
        self._last_question: dict[int, str] = {}
        self._renderer = EventRenderer(suppressed_tools=suppressed_tools)

    async def record_user_message(self, user_id: int, text: str, cwd: str = "") -> None:
        self._last_question[user_id] = text
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S %Z")
        cwd_tag = f" · {cwd}" if cwd else ""
        self._ensure_header()
        content = f"\n## {ts} · User {user_id}{cwd_tag}\n\n{text}\n"
        await asyncio.to_thread(self._sync_append, content)

    async def record_event(self, user_id: int, event: Event) -> None:
        last_q = self._last_question.get(user_id, "")
        text = self._renderer.render(event, last_question=last_q)
        if text:
            await asyncio.to_thread(self._sync_append, text)

    def _ensure_header(self) -> None:
        path = self._today_path()
        if not path.exists():
            self._dir.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {date.today().isoformat()} — Archon Conversations\n", encoding="utf-8")

    def _sync_append(self, text: str) -> None:
        with self._today_path().open("a", encoding="utf-8") as f:
            f.write(text)

    def _today_path(self) -> Path:
        return self._dir / f"{date.today().isoformat()}.md"
