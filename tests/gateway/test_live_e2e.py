"""S5.6 — Live full-stack e2e test.

Requires:
- TELEGRAM_BOT_TOKEN in .env
- TELEGRAM_LIVE_CHAT_ID in .env
- claude binary in PATH

Run with: uv run pytest -m "live and requires_telegram" tests/gateway/test_live_e2e.py -v
"""
import asyncio
import os
import shutil
import signal
import socket
from unittest.mock import patch

import pytest
from dotenv import load_dotenv

from archon.ai.event_mapper import Response
from archon.ai.session_manager import SessionManager
from archon.ai.truncation import SplitStrategy
from archon.chat.bot import create_bot as _real_create_bot
from archon.chat.handler import format_event
from archon.gateway.gateway import Gateway

load_dotenv()

pytestmark = [
    pytest.mark.live,
    pytest.mark.requires_telegram,
    pytest.mark.skipif(
        shutil.which("claude") is None,
        reason="claude binary not found in PATH",
    ),
]

_TIMEOUT = 60.0
_PROMPT = "What is 2+2? Reply with just the number."


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not set in environment")
    return value


def _find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


async def test_live_full_stack_e2e() -> None:
    """Full pipeline: Gateway polling → SessionManager → Claude SDK → Telegram → ✅ Response delivered."""
    _require_env("TELEGRAM_BOT_TOKEN")
    chat_id = int(_require_env("TELEGRAM_LIVE_CHAT_ID"))

    captured: dict = {}
    truncation = SplitStrategy()
    real_sm_cls = SessionManager

    def _capturing_sm(*args, **kwargs) -> SessionManager:
        sm = real_sm_cls(*args, **kwargs)
        captured["sm"] = sm
        return sm

    def _capturing_create_bot(token: str):
        bot = _real_create_bot(token)
        captured["bot"] = bot
        return bot

    # Use random free ports for both MCP servers to avoid conflicts with a running Archon instance
    free_port_bg = _find_free_port()
    free_port_orch = _find_free_port()
    real_load_config = __import__("archon.config.loader", fromlist=["load_config"]).load_config

    def _patched_load_config(*args, **kwargs):
        cfg = real_load_config(*args, **kwargs)
        cfg.background_agents.port = free_port_bg
        cfg.background_agents.router_mcp_port = free_port_orch
        return cfg

    with (
        patch("archon.gateway.gateway.SessionManager", side_effect=_capturing_sm),
        patch("archon.gateway.gateway.create_bot", side_effect=_capturing_create_bot),
        patch("archon.config.loader.load_config", side_effect=_patched_load_config),
    ):
        gw_task = asyncio.create_task(Gateway._run())
        try:
            # Wait for gateway to start and captures to be populated
            async with asyncio.timeout(10.0):
                while len(captured) < 2:
                    await asyncio.sleep(0.1)

            sm: SessionManager = captured["sm"]
            bot = captured["bot"]

            # Inject prompt directly into the session manager (bypassing Telegram input)
            session = await sm.get_or_create(user_id=chat_id)
            response_delivered = False

            async with asyncio.timeout(_TIMEOUT):
                async for event in session.send(_PROMPT):
                    for text in format_event(event, truncation):
                        await bot.send_message(chat_id=chat_id, text=text)
                    if isinstance(event, Response):
                        response_delivered = True
                        break

            assert response_delivered, "No ✅ Response event received within 60s"
        finally:
            os.kill(os.getpid(), signal.SIGINT)
            await asyncio.wait_for(gw_task, timeout=10.0)
