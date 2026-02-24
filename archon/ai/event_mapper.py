"""Event mapper — maps Claude Agent SDK messages to archon event dataclasses."""
import json
import logging
from dataclasses import dataclass
from typing import AsyncGenerator, AsyncIterable

logger = logging.getLogger("archon")

from claude_agent_sdk import (
    AssistantMessage,
    Message,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)


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
    input: str = ""
    id: int = 0


@dataclass
class ToolResult:
    content: str
    id: int = 0


@dataclass
class Response:
    content: str


@dataclass
class ErrorEvent:
    message: str


@dataclass
class SubagentStarted:
    """Fired when the main agent spawns a sub-agent (e.g. via the Task tool)."""
    agent_id: str
    agent_type: str


@dataclass
class SubagentStopped:
    """Fired when a sub-agent completes its work."""
    agent_id: str
    agent_type: str


Event = (
    ThinkingStarted
    | ThinkingResult
    | ToolStarted
    | ToolResult
    | Response
    | ErrorEvent
    | SubagentStarted
    | SubagentStopped
)


# ──────────────────────────────────────────────────────────────────
# Mapper
# ──────────────────────────────────────────────────────────────────


class EventMapper:
    """Maps Claude Agent SDK messages to archon event dataclasses."""

    def __init__(self) -> None:
        self._next_id = 0
        self._tool_id_map: dict[str, int] = {}

    def _alloc_tool_id(self, sdk_id: str) -> int:
        self._next_id += 1
        self._tool_id_map[sdk_id] = self._next_id
        return self._next_id

    async def map_messages(
        self, stream: AsyncIterable[Message]
    ) -> AsyncGenerator[Event, None]:
        async for message in stream:
            async for event in self._map(message):
                yield event

    async def _map(self, message: Message) -> AsyncGenerator[Event, None]:
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ThinkingBlock):
                    yield ThinkingStarted()
                    yield ThinkingResult(content=block.thinking)
                elif isinstance(block, ToolUseBlock):
                    tool_id = self._alloc_tool_id(block.id)
                    yield ToolStarted(name=block.name, input=_tool_input_text(block.input), id=tool_id)
                elif isinstance(block, TextBlock):
                    pass  # final text arrives via ResultMessage.result
        elif isinstance(message, UserMessage):
            if isinstance(message.content, list):
                for block in message.content:
                    if isinstance(block, ToolResultBlock):
                        tool_id = self._tool_id_map.get(block.tool_use_id, 0)
                        yield ToolResult(content=_tool_result_content(block), id=tool_id)
        elif isinstance(message, ResultMessage):
            if message.is_error:
                yield ErrorEvent(message=message.result or "Unknown error")
            elif message.result:
                yield Response(content=message.result)
            else:
                logger.warning("ResultMessage received with no result text and no error flag")


def _tool_input_text(inp: dict[str, object]) -> str:
    if not inp:
        return ""
    if "command" in inp:
        return str(inp["command"])
    if "file_path" in inp:
        return str(inp["file_path"])
    values = list(inp.values())
    if len(values) == 1:
        return str(values[0])
    return json.dumps(inp, ensure_ascii=False)


def _tool_result_content(block: ToolResultBlock) -> str:
    if block.content is None:
        return ""
    if isinstance(block.content, str):
        return block.content
    # Extract text from SDK content blocks [{"type": "text", "text": "..."}].
    # This avoids leaking raw JSON into displayed tool results.
    texts = [
        item["text"]
        for item in block.content
        if isinstance(item, dict) and item.get("type") == "text" and "text" in item
    ]
    if texts:
        return "".join(texts)
    # Fall back to JSON for non-text content blocks (e.g. images).
    return json.dumps(block.content)
