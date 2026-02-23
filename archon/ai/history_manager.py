"""History manager — persists chat interactions to daily Markdown files."""
from datetime import date, datetime, timezone
from pathlib import Path

from archon.ai.event_mapper import (
    ErrorEvent,
    Event,
    Response,
    ThinkingResult,
    ThinkingStarted,
    ToolResult,
    ToolStarted,
)


class HistoryManager:
    """Writes conversation turns to ~/.archon/history/YYYY-MM-DD.md."""

    def __init__(self, directory: str) -> None:
        self._dir = Path(directory).expanduser()
        self._last_question: dict[int, str] = {}

    def record_user_message(self, user_id: int, text: str, cwd: str = "") -> None:
        self._last_question[user_id] = text
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        cwd_tag = f" · {cwd}" if cwd else ""
        self._ensure_header()
        self._append(f"\n## {ts} · User {user_id}{cwd_tag}\n\n{text}\n")

    def record_event(self, user_id: int, event: Event) -> None:
        text = self._render(event, user_id)
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

    def _render(self, event: Event, user_id: int) -> str:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        if isinstance(event, ThinkingStarted):
            return ""
        if isinstance(event, ThinkingResult):
            return f"\n### 💭 Thought · {ts}\n\n{event.content}\n"
        if isinstance(event, ToolStarted):
            id_tag = f" [{event.id}]" if event.id else ""
            return f"\n### 🔧 Tool: {event.name}{id_tag} · {ts}\n\n```\n{event.input}\n```\n"
        if isinstance(event, ToolResult):
            id_tag = f" [{event.id}]" if event.id else ""
            return f"\n### 📤 Result{id_tag} · {ts}\n\n```\n{event.content}\n```\n"
        if isinstance(event, Response):
            q = self._last_question.get(user_id, "")
            q_ctx = f'> User: "{q[:120]}{"..." if len(q) > 120 else ""}"\n\n' if q else ""
            return f"\n### ✅ Response · {ts}\n\n{q_ctx}{event.content}\n\n---\n"
        if isinstance(event, ErrorEvent):
            return f"\n### ❌ Error · {ts}\n\n{event.message}\n\n---\n"
        return ""  # pragma: no cover
