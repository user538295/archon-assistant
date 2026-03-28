"""Tests for Task E.1: _detect_rag_state() in gateway.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.config.loader import RagConfig


def _make_rag_cfg(host: str = "localhost", port: int = 8282) -> RagConfig:
    cfg = RagConfig()
    cfg.host = host
    cfg.port = port
    return cfg


# ──────────────────────────────────────────────────────────────────
# TestDetectRagState
# ──────────────────────────────────────────────────────────────────


class TestDetectRagState:
    async def test_returns_running_when_probe_succeeds(self) -> None:
        from archon.gateway.gateway import RagState, _detect_rag_state

        with patch(
            "archon.gateway.gateway._ensure_rag_server", new_callable=AsyncMock, return_value=True
        ):
            result = await _detect_rag_state(_make_rag_cfg())

        assert result == RagState.RUNNING

    async def test_returns_not_installed_when_lancedb_missing(self) -> None:
        from archon.gateway.gateway import RagState, _detect_rag_state

        with (
            patch(
                "archon.gateway.gateway._ensure_rag_server",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("archon.gateway.gateway.importlib.util.find_spec", return_value=None),
        ):
            result = await _detect_rag_state(_make_rag_cfg())

        assert result == RagState.NOT_INSTALLED

    async def test_returns_not_registered_when_packages_present_service_not_registered(
        self,
    ) -> None:
        from archon.gateway.gateway import RagState, _detect_rag_state

        mock_rag_service = MagicMock()
        mock_rag_service.is_installed.return_value = False

        with (
            patch(
                "archon.gateway.gateway._ensure_rag_server",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "archon.gateway.gateway.importlib.util.find_spec",
                return_value=MagicMock(),  # non-None → lancedb importable
            ),
            patch(
                "archon.gateway.gateway.get_rag_service",
                return_value=mock_rag_service,
            ),
        ):
            result = await _detect_rag_state(_make_rag_cfg())

        assert result == RagState.NOT_REGISTERED

    async def test_returns_not_running_when_packages_installed_service_registered(
        self,
    ) -> None:
        from archon.gateway.gateway import RagState, _detect_rag_state

        mock_rag_service = MagicMock()
        mock_rag_service.is_installed.return_value = True

        with (
            patch(
                "archon.gateway.gateway._ensure_rag_server",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "archon.gateway.gateway.importlib.util.find_spec",
                return_value=MagicMock(),  # non-None → lancedb importable
            ),
            patch(
                "archon.gateway.gateway.get_rag_service",
                return_value=mock_rag_service,
            ),
        ):
            result = await _detect_rag_state(_make_rag_cfg())

        assert result == RagState.NOT_RUNNING
