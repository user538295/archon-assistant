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
# Mapper
# ──────────────────────────────────────────────────────────────────


class EventMapper:
    """Maps Claude Agent SDK messages to archon event dataclasses."""

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
                    yield ToolStarted(name=block.name)
                elif isinstance(block, TextBlock):
                    pass  # final text arrives via ResultMessage.result
        elif isinstance(message, UserMessage):
            if isinstance(message.content, list):
                for block in message.content:
                    if isinstance(block, ToolResultBlock):
                        yield ToolResult(content=_tool_result_content(block))
        elif isinstance(message, ResultMessage):
            if message.is_error:
                yield ErrorEvent(message=message.result or "Unknown error")
            elif message.result:
                yield Response(content=message.result)
            else:
                logger.warning("ResultMessage received with no result text and no error flag")


def _tool_result_content(block: ToolResultBlock) -> str:
    if block.content is None:
        return ""
    if isinstance(block.content, str):
        return block.content
    return json.dumps(block.content)
