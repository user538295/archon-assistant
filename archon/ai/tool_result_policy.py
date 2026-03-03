"""Shared policy helpers for rendering tool results safely."""

from archon.ai.event_mapper import ToolResult

DEFAULT_SUPPRESSED_TOOLS: frozenset[str] = frozenset(
    {"Read", "Glob", "Grep", "WebFetch"}
)


def format_tool_result_size(byte_count: int) -> str:
    """Format a byte count as a human-readable string (B or KB)."""
    if byte_count < 1024:
        return f"{byte_count} B"
    return f"{byte_count / 1024:.1f} KB"


def should_suppress_tool_result(
    event: ToolResult,
    suppressed_tools: frozenset[str] | None = None,
) -> bool:
    """Return whether *event* must never render its full successful body."""
    active = suppressed_tools if suppressed_tools is not None else DEFAULT_SUPPRESSED_TOOLS
    return event.tool_name in active and not event.is_error


def summarize_tool_result(
    event: ToolResult,
    suppressed_tools: frozenset[str] | None = None,
) -> str:
    """Return the compact summary line for a suppressed successful tool result."""
    if not should_suppress_tool_result(event, suppressed_tools):
        raise ValueError("Tool result is not suppressible")
    lines = len(event.content.splitlines())
    size = format_tool_result_size(len(event.content.encode("utf-8")))
    tool = event.tool_name or "tool"
    return f"✓ {tool} completed ({lines} lines, {size})"
