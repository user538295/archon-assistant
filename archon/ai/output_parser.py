"""Output parser — converts raw PTY byte stream into typed event dataclasses."""
import re
from dataclasses import dataclass
from typing import AsyncGenerator

# ──────────────────────────────────────────────────────────────────
# Event dataclasses
# ──────────────────────────────────────────────────────────────────


@dataclass
class ThinkingStarted:
    pass


@dataclass
class ThinkingResult:
    content: str


@dataclass
class ToolStarted:
    name: str


@dataclass
class ToolResult:
    content: str


@dataclass
class Response:
    content: str


@dataclass
class ErrorEvent:
    message: str


Event = ThinkingStarted | ThinkingResult | ToolStarted | ToolResult | Response | ErrorEvent

# ──────────────────────────────────────────────────────────────────
# Patterns
# ──────────────────────────────────────────────────────────────────

# Strips all ANSI/VT escape sequences
_ANSI = re.compile(r"\x1b(?:\[[0-9;?]*[A-Za-z]|\][^\x07]*(?:\x07|\x1b\\)|.)")

# "Thinking..." — case-insensitive, optional trailing dots
_RE_THINKING = re.compile(r"Thinking\.{0,3}", re.IGNORECASE)

# ⏺/⏹/✻/●/◆ followed by a tool name word then "("
_RE_TOOL = re.compile(r"[⏺⏹✻●◆]\s+([A-Za-z][A-Za-z_]*)\s*\(")

# ⎿/└/▸ — tool result indent marker
_RE_RESULT = re.compile(r"^[⎿└▸]\s*(.*)")

# "Error:" or "error:" patterns
_RE_ERROR = re.compile(r"(?:Error|error)[:\s]+(.+)")


def _strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


# ──────────────────────────────────────────────────────────────────
# Parser
# ──────────────────────────────────────────────────────────────────


class OutputParser:
    """Parses a raw PTY byte stream into typed event dataclasses."""

    async def parse(
        self, stream: AsyncGenerator[bytes, None]
    ) -> AsyncGenerator[Event, None]:
        state = "idle"           # idle | in_thinking | in_tool
        thinking_buf: list[str] = []
        tool_result_buf: list[str] = []
        response_buf: list[str] = []
        raw_buf = b""

        async for chunk in stream:
            raw_buf += chunk
            while b"\n" in raw_buf:
                raw_line, raw_buf = raw_buf.split(b"\n", 1)
                line = _strip_ansi(raw_line.decode("utf-8", errors="replace")).rstrip("\r")
                stripped = line.strip()
                if not stripped:
                    continue

                # ── Thinking start ──────────────────────────────
                if _RE_THINKING.search(stripped):
                    if state == "in_thinking" and thinking_buf:
                        yield ThinkingResult(content="\n".join(thinking_buf))
                        thinking_buf = []
                    if tool_result_buf:
                        yield ToolResult(content="\n".join(tool_result_buf))
                        tool_result_buf = []
                    state = "in_thinking"
                    yield ThinkingStarted()
                    continue

                # ── Tool start ──────────────────────────────────
                m = _RE_TOOL.search(stripped)
                if m:
                    if state == "in_thinking" and thinking_buf:
                        yield ThinkingResult(content="\n".join(thinking_buf))
                        thinking_buf = []
                    if tool_result_buf:
                        yield ToolResult(content="\n".join(tool_result_buf))
                        tool_result_buf = []
                    state = "in_tool"
                    yield ToolStarted(name=m.group(1))
                    continue

                # ── Tool result indent ──────────────────────────
                m = _RE_RESULT.match(stripped)
                if m and state == "in_tool":
                    tool_result_buf.append(m.group(1))
                    continue

                # Leaving tool result block
                if tool_result_buf:
                    yield ToolResult(content="\n".join(tool_result_buf))
                    tool_result_buf = []
                    state = "idle"

                # ── Error ───────────────────────────────────────
                m = _RE_ERROR.search(stripped)
                if m:
                    yield ErrorEvent(message=m.group(1).strip())
                    state = "idle"
                    continue

                # ── Buffer line ─────────────────────────────────
                if state == "in_thinking":
                    thinking_buf.append(stripped)
                else:
                    state = "idle"
                    response_buf.append(stripped)

        # ── Flush remaining buffers ─────────────────────────────
        if thinking_buf:
            yield ThinkingResult(content="\n".join(thinking_buf))
        if tool_result_buf:
            yield ToolResult(content="\n".join(tool_result_buf))
        if response_buf:
            yield Response(content="\n".join(response_buf))
