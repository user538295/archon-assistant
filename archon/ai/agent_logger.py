"""Agent logger — persists per-agent work to dedicated Markdown log files.

FR.003: Each sub-agent session writes to a separate file in the history
directory.  Files are named YYYY-MM-DD-HH-MM-{agent-name}.md (timestamp =
agent start time) and written *continuously* as events arrive — not batched.

Architecture:
  AgentLogger     — top-level manager; one instance per Archon session.
  AgentLogWriter  — one instance per active sub-agent; opened on SubagentStarted,
                    finalized on SubagentStopped.

The logger maintains a stack of active writers to correctly handle nested
sub-agents: the innermost writer receives all non-lifecycle events.
"""
from datetime import datetime, timezone
from pathlib import Path

from archon.ai.event_mapper import (
    ErrorEvent,
    Event,
    Response,
    SubagentStarted,
    SubagentStopped,
    ThinkingResult,
    ThinkingStarted,
    ToolResult,
    ToolStarted,
)


def _sanitize_name(name: str) -> str:
    """Sanitize an agent name for safe use as a filename component.

    Replaces every character that is not alphanumeric, a hyphen, or an
    underscore with a hyphen, then strips leading/trailing hyphens.
    Returns ``"agent"`` when the sanitized result is empty.
    """
    result = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)
    result = result.strip("-")
    return result or "agent"


class AgentLogWriter:
    """Writes a single agent's events to a dedicated Markdown file.

    Events are flushed to disk on every :meth:`record_event` call (continuous
    write) so partial logs are readable even if the process is interrupted.

    File format::

        # Agent: Nova · 2026-02-25 14:30
        **Type:** general-purpose
        **Started:** 14:30:45 UTC

        ---

        ### 💭 Thought · 14:30:46

        I need to read the config.

        ### 🔧 Tool: Read [1] · 14:30:47

        ```
        /path/to/config.toml
        ```

        ### 📤 Result [1] · 14:30:48

        ```
        [access]
        ...
        ```

        ## Completed · 14:31:00

        **Duration:** 0:00:15

        ---
    """

    def __init__(
        self,
        path: Path,
        agent_name: str,
        agent_type: str,
        started_at: datetime,
    ) -> None:
        self._path = path
        self._started_at = started_at
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_header(agent_name, agent_type, started_at)

    # ── Public interface ──────────────────────────────────────────────────────

    @property
    def path(self) -> Path:
        """Absolute path of the log file."""
        return self._path

    def record_event(self, event: Event) -> None:
        """Render *event* and append to the log file immediately."""
        text = self._render(event)
        if text:
            self._append(text)

    def finalize(self) -> None:
        """Append the completion footer (timestamp + duration) and close the log."""
        now = datetime.now(timezone.utc)
        ts = now.strftime("%H:%M:%S UTC")
        delta = now - self._started_at
        total_secs = int(delta.total_seconds())
        h, rem = divmod(total_secs, 3600)
        m, s = divmod(rem, 60)
        self._append(
            f"\n## Completed · {ts}\n\n"
            f"**Duration:** {h}:{m:02d}:{s:02d}\n\n"
            f"---\n"
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _write_header(self, agent_name: str, agent_type: str, started_at: datetime) -> None:
        date_str = started_at.strftime("%Y-%m-%d %H:%M")
        ts = started_at.strftime("%H:%M:%S UTC")
        type_line = f"\n**Type:** {agent_type}" if agent_type else ""
        self._path.write_text(
            f"# Agent: {agent_name} · {date_str}{type_line}\n"
            f"**Started:** {ts}\n\n"
            f"---\n",
            encoding="utf-8",
        )

    def _append(self, text: str) -> None:
        with self._path.open("a", encoding="utf-8") as f:
            f.write(text)

    def _render(self, event: Event) -> str:  # noqa: PLR0911
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
            return f"\n### ✅ Response · {ts}\n\n{event.content}\n\n---\n"
        if isinstance(event, ErrorEvent):
            return f"\n### ❌ Error · {ts}\n\n{event.message}\n\n---\n"
        return ""  # pragma: no cover


class AgentLogger:
    """Manages per-agent Markdown log files in the history directory.

    Maintains a stack of active writers to support nested sub-agents.
    The innermost (most recently started) agent receives all non-lifecycle
    events until it stops, at which point the stack is popped and the writer
    for the next outer agent (if any) becomes active again.

    Usage::

        logger = AgentLogger("~/.archon/history")
        async for event in session.send(prompt):
            logger.record_event(event)          # routing is automatic
    """

    def __init__(self, directory: str) -> None:
        self._dir = Path(directory).expanduser()
        # Stack: list of (agent_id, AgentLogWriter) — top is last element.
        self._active: list[tuple[str, AgentLogWriter]] = []

    def record_event(self, event: Event) -> None:
        """Route *event* to the appropriate writer.

        - :class:`~archon.ai.event_mapper.SubagentStarted` — opens a new file,
          pushes the writer onto the stack.
        - :class:`~archon.ai.event_mapper.SubagentStopped` — finalizes the
          matching writer (searching from the top), pops it from the stack.
        - All other events — forwarded to the innermost active writer (if any).
          Events received when no agent is active are silently discarded.
        """
        if isinstance(event, SubagentStarted):
            started_at = datetime.now(timezone.utc)
            path = self._agent_path(event.agent_name, started_at)
            writer = AgentLogWriter(path, event.agent_name, event.agent_type, started_at)
            self._active.append((event.agent_id, writer))
        elif isinstance(event, SubagentStopped):
            # Search stack from top for the matching agent_id.
            for i in range(len(self._active) - 1, -1, -1):
                if self._active[i][0] == event.agent_id:
                    _, writer = self._active.pop(i)
                    writer.finalize()
                    break
            # Unmatched stop event — silently ignore (defensive).
        else:
            # Forward to innermost (top-of-stack) active writer.
            if self._active:
                self._active[-1][1].record_event(event)

    def _agent_path(self, agent_name: str, started_at: datetime) -> Path:
        """Build the log file path for *agent_name* started at *started_at*.

        Handles filename collisions by appending a counter suffix when a file
        with the same name already exists (e.g. two agents with the same name
        starting in the same minute).
        """
        date_prefix = started_at.strftime("%Y-%m-%d-%H-%M")
        safe_name = _sanitize_name(agent_name)
        base = self._dir / f"{date_prefix}-{safe_name}.md"
        if not base.exists():
            return base
        # Collision — append counter suffix
        counter = 2
        while True:
            candidate = self._dir / f"{date_prefix}-{safe_name}-{counter}.md"
            if not candidate.exists():
                return candidate
            counter += 1
