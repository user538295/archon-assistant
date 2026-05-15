"""Suite 8: archon doctor Search Checks (H8.1–H8.6).

Cross-reference note: `tests/cli/test_doctor.py` already contains comprehensive
coverage for all six H8 scenarios via `_check_search_health` / `_check_search_server`.
This file adds focused, independent tests that target the same scenarios using
direct `SearchClient` AsyncMock injection (no `_MockHttp`/`_MockStateStore` helpers),
confirming exact output strings and guard conditions from a clean mock surface.

Scenarios already present in test_doctor.py (not duplicated here):
- H8.1 search disabled via _check_search_server → CheckResult(ok=True, detail="disabled")
  → TestCheckRagServer.test_disabled_returns_ok
- H8.1 search disabled via _check_search_health → "disabled" printed, health not called
  → test_doctor_search_disabled_shows_disabled
- H8.2 server healthy → CheckResult(ok=True, detail="running"), health() called
  → TestCheckRagServer.test_running_returns_ok, test_doctor_search_running_calls_health
- H8.3 server unreachable → CheckResult(ok=False), "not running" printed
  → TestCheckRagServer.test_not_running_returns_fail_with_start_guidance,
     test_doctor_search_not_running_shows_not_running
- H8.4 IN_PROGRESS → ⏳ partial (N/M files) printed
  → test_in_progress_label_is_in_progress, test_doctor_in_progress_shows_partial
- H8.5 FAILED → ❌ printed
  → test_doctor_failed_still_warns, test_doctor_failed_shows_error
- H8.6 PENDING → ⏳ pending (informational only, no ⚠)
  → test_doctor_pending_no_warning

Tests below provide independent verification using minimal mock setup:
direct patch of `archon.cli.doctor.SearchClient` with an AsyncMock, verifying
the output strings at the boundary rather than via the collection-state objects.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import archon.cli.doctor as doctor_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(enabled: bool = True, url: str = "http://localhost:8765") -> object:
    """Minimal config stub for search health checks."""
    import urllib.parse

    class _Search:
        pass

    class _Cfg:
        pass

    s = _Search()
    s.enabled = enabled
    s.url = url
    parsed = urllib.parse.urlparse(url)
    s.host_port = (parsed.hostname or "127.0.0.1", parsed.port or 8765)
    c = _Cfg()
    c.search = s
    return c


def _run(coro):
    return asyncio.run(coro)


def _make_jsonrpc_response(collections: list[dict]) -> dict:
    return {
        "result": {
            "content": [{"type": "text", "text": json.dumps(collections)}]
        }
    }


_HEALTH_OK = {"status": "ok"}


def _mock_search_client(
    health_return=_HEALTH_OK,
    indexing_state_return=None,
    jsonrpc_data: dict | None = None,
):
    """Build a minimal SearchClient AsyncMock for _check_search_health injection."""
    col_info_map = _extract_col_info_map(jsonrpc_data) if jsonrpc_data else {}

    async def fake_collection_info(name: str) -> dict | None:
        return col_info_map.get(name)

    client = AsyncMock()
    client.health = AsyncMock(return_value=health_return)
    client.indexing_state = AsyncMock(
        return_value=indexing_state_return if indexing_state_return is not None
        else {"collections": {}}
    )
    client.list_collections = AsyncMock(
        return_value=[{"name": n} for n in col_info_map]
    )
    client.collection_info = fake_collection_info
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _extract_col_info_map(jsonrpc_data: dict) -> dict:
    """Extract collection info from legacy JSON-RPC response_data format."""
    content_blocks = jsonrpc_data.get("result", {}).get("content", [])
    if not content_blocks or content_blocks[0].get("type") != "text":
        return {}
    try:
        raw_collections = json.loads(content_blocks[0]["text"])
    except (json.JSONDecodeError, KeyError):
        return {}
    col_info_map = {}
    for col in raw_collections:
        if not isinstance(col, dict) or "name" not in col:
            continue
        name = col["name"]
        col_info_map[name] = {
            "doc_count": col.get("doc_count", 0),
            "centroid_present": col.get("centroid") is not None,
            "last_indexed": col.get("last_indexed"),
        }
    return col_info_map


# ---------------------------------------------------------------------------
# H8.1 — search disabled → "disabled" printed, no network call
# ---------------------------------------------------------------------------

class TestH81SearchDisabled:
    def test_check_search_server_disabled_returns_ok(self) -> None:
        """_check_search_server: disabled config → ok=True, detail='disabled'."""
        cfg = _make_cfg(enabled=False)
        result = doctor_mod._check_search_server(cfg)
        assert result.ok is True
        assert result.detail == "disabled"
        assert result.name == "search server"

    def test_check_search_health_disabled_no_http_call(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """_check_search_health: disabled → prints 'disabled', SearchClient never instantiated."""
        cfg = _make_cfg(enabled=False)
        with patch("archon.cli.doctor.SearchClient") as mock_cls:
            _run(doctor_mod._check_search_health(cfg))
            mock_cls.assert_not_called()
        out = capsys.readouterr().out
        assert "disabled" in out


# ---------------------------------------------------------------------------
# H8.2 — search enabled + server healthy → check passes
# ---------------------------------------------------------------------------

class TestH82ServerHealthy:
    def test_check_search_server_running_returns_ok(self) -> None:
        """_check_search_server: socket connects → ok=True, detail='running'."""
        cfg = _make_cfg(enabled=True)
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        with patch("importlib.util.find_spec", return_value=MagicMock()), \
             patch("archon.cli.doctor.socket.create_connection", return_value=mock_conn):
            result = doctor_mod._check_search_server(cfg)
        assert result.ok is True
        assert result.detail == "running"

    def test_check_search_health_healthy_calls_health_endpoint(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """_check_search_health: health() returns dict → health() was awaited."""
        cfg = _make_cfg(enabled=True)
        client = _mock_search_client(health_return={"status": "ok"})
        with patch("archon.cli.doctor.SearchClient", return_value=client):
            _run(doctor_mod._check_search_health(cfg))
        client.health.assert_awaited_once()
        out = capsys.readouterr().out
        assert "not running" not in out
        assert "disabled" not in out


# ---------------------------------------------------------------------------
# H8.3 — search enabled + server unreachable → fails with connection message
# ---------------------------------------------------------------------------

class TestH83ServerUnreachable:
    def test_check_search_server_not_running_returns_fail(self) -> None:
        """_check_search_server: socket refuses → ok=False, detail mentions 'archon search start'."""
        cfg = _make_cfg(enabled=True)
        with patch("importlib.util.find_spec", return_value=MagicMock()), \
             patch("archon.cli.doctor.socket.create_connection",
                   side_effect=OSError("connection refused")):
            result = doctor_mod._check_search_server(cfg)
        assert result.ok is False
        assert "archon search start" in result.detail

    def test_check_search_health_health_returns_none_prints_not_running(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """_check_search_health: health() returns None → 'not running' printed."""
        cfg = _make_cfg(enabled=True)
        client = _mock_search_client(health_return=None, indexing_state_return=None)
        with patch("archon.cli.doctor.SearchClient", return_value=client):
            _run(doctor_mod._check_search_health(cfg))
        out = capsys.readouterr().out
        assert "not running" in out
        # indexing_state must NOT be called when health indicates server is down
        client.indexing_state.assert_not_awaited()


# ---------------------------------------------------------------------------
# H8.4 — collection is IN_PROGRESS → "⏳ partial (N/M files)"
# ---------------------------------------------------------------------------

class TestH84InProgress:
    def test_in_progress_with_files_shows_partial(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """IN_PROGRESS + processed_files > 0 → ⏳ partial (N/M files), no ⚠."""
        cfg = _make_cfg(enabled=True)
        state_data = {
            "collections": {
                "docs": {
                    "status": "in_progress",
                    "processed_files": 30,
                    "total_files": 60,
                    "error": None,
                }
            }
        }
        from datetime import datetime, timezone, timedelta
        recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        col = {
            "name": "docs",
            "doc_count": 10,
            "chunk_count": 50,
            "embedding_model": "BAAI/bge-small-en-v1.5",
            "centroid": [0.1, 0.2],
            "last_indexed": recent,
        }
        jsonrpc_data = _make_jsonrpc_response([col])
        client = _mock_search_client(
            health_return={"status": "ok"},
            indexing_state_return=state_data,
            jsonrpc_data=jsonrpc_data,
        )
        with patch("archon.cli.doctor.SearchClient", return_value=client):
            _run(doctor_mod._check_search_health(cfg))
        out = capsys.readouterr().out
        assert "⏳" in out
        assert "partial" in out
        assert "30/60 files" in out
        assert "⚠" not in out

    def test_in_progress_zero_files_shows_indexing_starting(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """IN_PROGRESS + processed_files == 0 → ⏳ indexing starting, no ⚠."""
        cfg = _make_cfg(enabled=True)
        state_data = {
            "collections": {
                "docs": {
                    "status": "in_progress",
                    "processed_files": 0,
                    "total_files": 100,
                    "error": None,
                }
            }
        }
        from datetime import datetime, timezone, timedelta
        recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        col = {
            "name": "docs",
            "doc_count": 0,
            "chunk_count": 0,
            "embedding_model": "BAAI/bge-small-en-v1.5",
            "centroid": [0.1, 0.2],
            "last_indexed": recent,
        }
        jsonrpc_data = _make_jsonrpc_response([col])
        client = _mock_search_client(
            health_return={"status": "ok"},
            indexing_state_return=state_data,
            jsonrpc_data=jsonrpc_data,
        )
        with patch("archon.cli.doctor.SearchClient", return_value=client):
            _run(doctor_mod._check_search_health(cfg))
        out = capsys.readouterr().out
        assert "⏳" in out
        assert "indexing starting" in out
        assert "⚠" not in out


# ---------------------------------------------------------------------------
# H8.5 — collection is FAILED → "❌"
# ---------------------------------------------------------------------------

class TestH85Failed:
    def test_failed_collection_shows_error_mark(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """FAILED status → ❌ printed with collection name and error message."""
        cfg = _make_cfg(enabled=True)
        state_data = {
            "collections": {
                "docs": {
                    "status": "failed",
                    "processed_files": 0,
                    "total_files": 0,
                    "error": "Disk quota exceeded",
                }
            }
        }
        from datetime import datetime, timezone, timedelta
        recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        col = {
            "name": "docs",
            "doc_count": 5,
            "chunk_count": 20,
            "embedding_model": "BAAI/bge-small-en-v1.5",
            "centroid": [0.1, 0.2],
            "last_indexed": recent,
        }
        jsonrpc_data = _make_jsonrpc_response([col])
        client = _mock_search_client(
            health_return={"status": "ok"},
            indexing_state_return=state_data,
            jsonrpc_data=jsonrpc_data,
        )
        with patch("archon.cli.doctor.SearchClient", return_value=client):
            _run(doctor_mod._check_search_health(cfg))
        out = capsys.readouterr().out
        assert "❌" in out
        assert "docs" in out
        assert "Disk quota exceeded" in out
        assert "✅" not in out


# ---------------------------------------------------------------------------
# H8.6 — collection is PENDING → informational output only (no ⚠)
# ---------------------------------------------------------------------------

class TestH86Pending:
    def test_pending_fresh_shows_pending_no_warning(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """PENDING + processed_files == 0 → ⏳ pending printed, no ⚠."""
        cfg = _make_cfg(enabled=True)
        state_data = {
            "collections": {
                "docs": {
                    "status": "pending",
                    "processed_files": 0,
                    "total_files": 0,
                    "error": None,
                }
            }
        }
        from datetime import datetime, timezone, timedelta
        recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        col = {
            "name": "docs",
            "doc_count": 5,
            "chunk_count": 20,
            "embedding_model": "BAAI/bge-small-en-v1.5",
            "centroid": [0.1, 0.2],
            "last_indexed": recent,
        }
        jsonrpc_data = _make_jsonrpc_response([col])
        client = _mock_search_client(
            health_return={"status": "ok"},
            indexing_state_return=state_data,
            jsonrpc_data=jsonrpc_data,
        )
        with patch("archon.cli.doctor.SearchClient", return_value=client):
            _run(doctor_mod._check_search_health(cfg))
        out = capsys.readouterr().out
        assert "⏳" in out
        assert "pending" in out
        assert "⚠" not in out
