"""Event mapper — maps Claude Agent SDK messages to archon event dataclasses."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, AsyncGenerator, AsyncIterable

if TYPE_CHECKING:
    from archon.ai.agent_plan import AgentPlan

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
class ThinkingResult:
    content: str
    source: str = "orchestrator"


@dataclass
class ToolStarted:
    name: str
    input: str = ""
    id: int = 0
    source: str = "orchestrator"


@dataclass
class ToolResult:
    content: str
    id: int = 0
    tool_name: str = ""      # name of the tool that produced this result
    is_error: bool = False   # True when the tool call failed
    source: str = "orchestrator"


@dataclass
class Response:
    content: str
    source: str = "orchestrator"


@dataclass
class ErrorEvent:
    message: str
    source: str = "orchestrator"


@dataclass
class ClassificationEvent:
    """Emitted by the Pipeline after the Classifier classifies a user message."""
    intent: str
    confidence: float
    raw_response: str = ""
    model: str = ""
    duration_s: float = 0.0
    parse_error: str = ""
    source: str = "pipeline"


@dataclass
class SubagentStarted:
    """Fired when the main agent spawns a sub-agent (e.g. via the Task tool)."""
    agent_id: str
    agent_type: str
    agent_name: str = ""  # human-readable name assigned by Archon's name registry
    user_request: str = ""  # original Telegram message that triggered the spawn
    agent_task: str = ""    # full orchestrator-constructed prompt sent to the agent
    source: str = "orchestrator"


@dataclass
class SubagentStopped:
    """Fired when a sub-agent completes its work."""
    agent_id: str
    agent_type: str
    agent_name: str = ""  # human-readable name assigned by Archon's name registry
    final_result: str = ""  # final response text — written as the last log entry
    source: str = "orchestrator"


@dataclass
class PlanEvent:
    """Emitted by the Pipeline when the Decomposer outputs an agent plan."""
    plan: AgentPlan
    summary: str
    source: str = "pipeline"


@dataclass
class ReviewEvent:
    """Emitted when the Decomposer re-evaluates a low-confidence classification."""
    original_intent: str
    original_confidence: float
    updated_intent: str
    updated_confidence: float
    estimated_tools: int = 0
    source: str = "pipeline"


@dataclass
class RoutingEvent:
    """Emitted by the Pipeline after the Decomposer completes, showing the routing decision."""
    routing: str       # "chat_direct", "task_direct", "agent_spawn", "agent_plan"
    model: str         # decomposer model name
    agent_count: int = 0
    wave_count: int = 0
    source: str = "pipeline"


@dataclass
class WaveStarted:
    """Emitted by PlanExecutor when a wave of agents begins execution."""
    wave_number: int
    agent_ids: list[str] = field(default_factory=list)
    source: str = "plan_executor"


@dataclass
class WaveCompleted:
    """Emitted by PlanExecutor when a wave of agents finishes execution."""
    wave_number: int
    agent_ids: list[str] = field(default_factory=list)
    failed_ids: list[str] = field(default_factory=list)
    source: str = "plan_executor"


Event = (
    ThinkingResult
    | ToolStarted
    | ToolResult
    | Response
    | ErrorEvent
    | ClassificationEvent
    | ReviewEvent
    | SubagentStarted
    | SubagentStopped
    | PlanEvent
    | RoutingEvent
    | WaveStarted
    | WaveCompleted
)


# ──────────────────────────────────────────────────────────────────
# Mapper
# ──────────────────────────────────────────────────────────────────


class EventMapper:
    """Maps Claude Agent SDK messages to archon event dataclasses."""

    def __init__(self) -> None:
        self._next_id = 0
        self._tool_id_map: dict[str, int] = {}
        self._tool_name_map: dict[int, str] = {}  # maps tool_id → tool name

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
                    yield ThinkingResult(content=block.thinking)
                elif isinstance(block, ToolUseBlock):
                    tool_id = self._alloc_tool_id(block.id)
                    self._tool_name_map[tool_id] = block.name
                    yield ToolStarted(name=block.name, input=_tool_input_text(block.input), id=tool_id)
                elif isinstance(block, TextBlock):
                    pass  # final text arrives via ResultMessage.result
        elif isinstance(message, UserMessage):
            if isinstance(message.content, list):
                for block in message.content:
                    if isinstance(block, ToolResultBlock):
                        tool_id = self._tool_id_map.get(block.tool_use_id, 0)
                        tool_name = self._tool_name_map.get(tool_id, "")
                        is_error = getattr(block, "is_error", False)
                        yield ToolResult(
                            content=_tool_result_content(block),
                            id=tool_id,
                            tool_name=tool_name,
                            is_error=is_error,
                        )
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
