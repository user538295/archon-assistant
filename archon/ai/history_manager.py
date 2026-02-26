"""History manager — persists chat interactions to daily Markdown files."""
from datetime import date, datetime, timezone
from pathlib import Path

from archon.ai.event_mapper import Event
from archon.ai.event_renderer import EventRenderer


class HistoryManager:
    """Writes conversation turns to ~/.archon/history/YYYY-MM-DD.md."""

    def __init__(
        self,
        directory: str,
        suppressed_tools: frozenset[str] | None = None,
    ) -> None:
        self._dir = Path(directory).expanduser()
        self._last_question: dict[int, str] = {}
        self._renderer = EventRenderer(suppressed_tools=suppressed_tools)

    def record_user_message(self, user_id: int, text: str, cwd: str = "") -> None:
        self._last_question[user_id] = text
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S %Z")
        cwd_tag = f" · {cwd}" if cwd else ""
        self._ensure_header()
        self._append(f"\n## {ts} · User {user_id}{cwd_tag}\n\n{text}\n")

    def record_event(self, user_id: int, event: Event) -> None:
        last_q = self._last_question.get(user_id, "")
        text = self._renderer.render(event, last_question=last_q)
        if text:
            self._append(text)

    def _ensure_header(self) -> None:
        path = self._today_path()
        if not path.exists():
            self._dir.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {date.today().isoformat()} — Archon Conversations\n", encoding="utf-8")

    def _append(self, text: str) -> None:
        with self._today_path().open("a", encoding="utf-8") as f:
            f.write(text)

    def _today_path(self) -> Path:
        return self._dir / f"{date.today().isoformat()}.md"
