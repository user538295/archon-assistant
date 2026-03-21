"""History manager — persists chat interactions to daily Markdown files."""
import asyncio
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from archon.ai.event_mapper import Event, is_router_event
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
        suppressed_events: frozenset[str] | None = None,
    ) -> None:
        self._dir = Path(directory).expanduser() / "sessions"
        # Intentionally synchronous: cold-path one-time startup operations, latency acceptable here.
        self._dir.mkdir(parents=True, exist_ok=True)
        _migrate_legacy_files(self._dir.parent)
        self._last_question: dict[int, str] = {}
        self._last_source: dict[int, str] = {}
        self._renderer = EventRenderer(suppressed_tools=suppressed_tools, suppressed_events=suppressed_events)

    async def record_user_message(self, user_id: int, text: str, cwd: str = "") -> None:
        self._last_question[user_id] = text
        self._last_source.pop(user_id, None)  # Reset source tracking for new conversation turn
        utc_now = datetime.now(timezone.utc)
        ts = utc_now.strftime("%H:%M:%S %Z")
        cwd_tag = f" · {cwd}" if cwd else ""
        self._ensure_header(utc_now)
        await self._append(f"\n## {ts} · User {user_id}{cwd_tag}\n\n{text}\n", utc_now)

    async def record_archon_message(self, text: str) -> None:
        """Record a message sent directly by Archon (not from a pipeline event)."""
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S %Z")
        await self._append(f"\n> Archon ({ts}): {text}\n")

    async def record_raw(self, user_id: int, content: str) -> None:
        """Append raw content directly to the history file (no rendering)."""
        if content:
            await self._append(content)

    async def record_event(self, user_id: int, event: Event) -> None:
        last_q = self._last_question.get(user_id, "")
        text = self._renderer.render(event, last_question=last_q)
        if text:
            # Auto-insert separator when source transitions between router and non-router.
            event_source = "router" if is_router_event(event) else "main"
            last = self._last_source.get(user_id)
            if last is not None and last != event_source:
                await self.record_raw(user_id, "\n---\n")
            self._last_source[user_id] = event_source
            await self._append(text)

    def _ensure_header(self, utc_now: datetime) -> None:
        # Intentionally synchronous: cold-path one-time-per-day operation, latency acceptable here.
        path = self._utc_path(utc_now)
        if not path.exists():
            self._dir.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {utc_now.date().isoformat()} — Archon Conversations\n", encoding="utf-8")

    async def _append(self, text: str, utc_now: datetime | None = None) -> None:
        await asyncio.to_thread(self._sync_append, text, utc_now)

    def _sync_append(self, text: str, utc_now: datetime | None = None) -> None:
        path = self._utc_path(utc_now) if utc_now is not None else self._utc_path(datetime.now(timezone.utc))
        with path.open("a", encoding="utf-8") as f:
            f.write(text)

    def _utc_path(self, utc_now: datetime) -> Path:
        return self._dir / f"{utc_now.date().isoformat()}.md"
