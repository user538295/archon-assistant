"""Tests for ArchonToolkit — Task 1.2 scaffold."""
import logging
import pytest
from unittest.mock import AsyncMock

from archon.ai.archon_toolkit import ArchonToolkit
from archon.ai.event_mapper import ToolStarted, ToolResult


# ──────────────────────────────────────────────────────────────────
# Construction
# ──────────────────────────────────────────────────────────────────


class TestConstruction:
    def test_construction_with_no_deps(self) -> None:
        """Instantiate with all None — must not crash (archon_status is pre-registered)."""
        toolkit = ArchonToolkit()
        assert "archon_status" in toolkit.tool_names
        assert any(d["name"] == "archon_status" for d in toolkit.tool_definitions)

    def test_tool_definitions_is_instance_attr(self) -> None:
        """Two instances must have independent tool_definitions lists."""
        t1 = ArchonToolkit()
        t2 = ArchonToolkit()
        initial_len = len(t2.tool_definitions)
        t1.tool_definitions.append({"name": "test_tool"})
        assert len(t2.tool_definitions) == initial_len


# ──────────────────────────────────────────────────────────────────
# call_tool — unknown tool
# ──────────────────────────────────────────────────────────────────


class TestCallToolUnknown:
    async def test_call_tool_unknown_raises(self) -> None:
        """Calling an unknown tool must raise ValueError."""
        toolkit = ArchonToolkit()
        with pytest.raises(ValueError, match="Unknown tool"):
            await toolkit.call_tool("nonexistent", {})


# ──────────────────────────────────────────────────────────────────
# Audit logging
# ──────────────────────────────────────────────────────────────────


class TestAuditLogging:
    async def test_audit_logging_on_unknown_tool(self, caplog: pytest.LogCaptureFixture) -> None:
        """Audit log entry is written even when the tool is unknown (before raising)."""
        toolkit = ArchonToolkit()
        with caplog.at_level(logging.INFO, logger="archon"):
            with pytest.raises(ValueError):
                await toolkit.call_tool("bad_tool", {"key": "val"}, user_id=42)
        assert any("MCP tool call" in r.message and "bad_tool" in r.message and "42" in r.message for r in caplog.records)

    async def test_audit_logging_on_known_tool(self, caplog: pytest.LogCaptureFixture) -> None:
        """Audit log entry is written for a successful tool call."""
        toolkit = ArchonToolkit()

        async def _dummy_handler(arguments: dict, **kwargs: object) -> str:
            return "ok"

        toolkit.register_tool("my_tool", {"name": "my_tool", "description": "test", "inputSchema": {}}, _dummy_handler)

        with caplog.at_level(logging.INFO, logger="archon"):
            result = await toolkit.call_tool("my_tool", {"x": 1}, user_id=7)

        assert result == "ok"
        assert any("MCP tool call" in r.message and "my_tool" in r.message and "7" in r.message for r in caplog.records)


# ──────────────────────────────────────────────────────────────────
# register_tool + call_tool with dummy tool
# ──────────────────────────────────────────────────────────────────


class TestRegisterAndCallTool:
    async def test_register_tool_makes_it_callable(self) -> None:
        """A registered tool can be called via call_tool."""
        toolkit = ArchonToolkit()

        async def _echo(arguments: dict, **kwargs: object) -> str:
            return f"echo: {arguments.get('msg', '')}"

        toolkit.register_tool(
            "echo_tool",
            {"name": "echo_tool", "description": "echoes", "inputSchema": {}},
            _echo,
        )

        assert "echo_tool" in toolkit.tool_names
        assert any(d["name"] == "echo_tool" for d in toolkit.tool_definitions)
        result = await toolkit.call_tool("echo_tool", {"msg": "hello"})
        assert result == "echo: hello"


# ──────────────────────────────────────────────────────────────────
# event_callback
# ──────────────────────────────────────────────────────────────────


class TestEventCallback:
    async def test_event_callback_emits_tool_started_and_result(self) -> None:
        """When event_callback is provided, ToolStarted and ToolResult are emitted."""
        toolkit = ArchonToolkit()
        events: list = []

        async def _capture(event: object) -> None:
            events.append(event)

        async def _handler(arguments: dict, **kwargs: object) -> str:
            return "done"

        toolkit.register_tool("demo", {"name": "demo", "description": "d", "inputSchema": {}}, _handler)
        await toolkit.call_tool("demo", {"a": 1}, event_callback=_capture)

        assert len(events) == 2
        assert isinstance(events[0], ToolStarted)
        assert events[0].name == "demo"
        assert isinstance(events[1], ToolResult)
        assert events[1].tool_name == "demo"
        assert "done" in events[1].content

    async def test_event_callback_none_no_error(self) -> None:
        """Calling without event_callback must not raise."""
        toolkit = ArchonToolkit()

        async def _handler(arguments: dict, **kwargs: object) -> str:
            return "ok"

        toolkit.register_tool("noop", {"name": "noop", "description": "n", "inputSchema": {}}, _handler)
        result = await toolkit.call_tool("noop", {})
        assert result == "ok"


# ──────────────────────────────────────────────────────────────────
# Deduplication guard
# ──────────────────────────────────────────────────────────────────


class TestRegisterToolDeduplication:
    def test_register_tool_duplicate_raises(self) -> None:
        """Registering the same tool name twice must raise ValueError."""
        toolkit = ArchonToolkit()

        async def _handler(arguments: dict, **kwargs: object) -> str:
            return "ok"

        toolkit.register_tool("dup", {"name": "dup", "description": "d", "inputSchema": {}}, _handler)
        with pytest.raises(ValueError, match="Tool already registered"):
            toolkit.register_tool("dup", {"name": "dup", "description": "d2", "inputSchema": {}}, _handler)


# ──────────────────────────────────────────────────────────────────
# Error handling in call_tool
# ──────────────────────────────────────────────────────────────────


class TestCallToolErrorHandling:
    async def test_call_tool_handler_exception_emits_error_result(self) -> None:
        """When handler raises and event_callback is provided, ToolResult(is_error=True) is emitted."""
        toolkit = ArchonToolkit()
        events: list = []

        async def _capture(event: object) -> None:
            events.append(event)

        async def _failing_handler(arguments: dict, **kwargs: object) -> str:
            raise RuntimeError("boom")

        toolkit.register_tool("fail", {"name": "fail", "description": "f", "inputSchema": {}}, _failing_handler)

        with pytest.raises(RuntimeError, match="boom"):
            await toolkit.call_tool("fail", {}, event_callback=_capture)

        assert len(events) == 2
        assert isinstance(events[0], ToolStarted)
        assert events[0].name == "fail"
        assert isinstance(events[1], ToolResult)
        assert events[1].is_error is True
        assert "boom" in events[1].content

    async def test_call_tool_handler_exception_reraises(self) -> None:
        """When handler raises without event_callback, exception propagates."""
        toolkit = ArchonToolkit()

        async def _failing_handler(arguments: dict, **kwargs: object) -> str:
            raise RuntimeError("kaboom")

        toolkit.register_tool("fail2", {"name": "fail2", "description": "f", "inputSchema": {}}, _failing_handler)

        with pytest.raises(RuntimeError, match="kaboom"):
            await toolkit.call_tool("fail2", {})
