from __future__ import annotations
import json
import pytest
import urllib.error
from unittest.mock import patch, MagicMock
from pathlib import Path
import archon.cli.doctor as doctor_mod
import archon.diagnostics as diagnostics_mod
from archon.cli.doctor import CheckResult


_ALL_CHECKS = [
    "_check_git", "_check_uv", "_check_python", "_check_claude",
    "_check_env_file", "_check_config_file", "_check_logs_dir",
    "_check_health", "_check_app_dir", "_check_bot_token",
    "_check_context_windows",
]


def _all_ok() -> list[CheckResult]:
    return [CheckResult(f"check{i}", True, "OK") for i in range(len(_ALL_CHECKS))]


def test_all_pass_returns_0(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    ok = CheckResult("x", True, "OK")
    for name in _ALL_CHECKS:
        monkeypatch.setattr(doctor_mod, name, lambda _ok=ok: _ok)
    monkeypatch.setattr(doctor_mod, "_check_search_server", lambda cfg: ok)
    result = doctor_mod.run_doctor()
    assert result == 0
    assert "All checks passed" in capsys.readouterr().out


def test_one_fail_returns_1(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    ok = CheckResult("x", True, "OK")
    fail = CheckResult("env file", False, "not found")
    for name in _ALL_CHECKS:
        if name == "_check_env_file":
            monkeypatch.setattr(doctor_mod, name, lambda: fail)
        else:
            monkeypatch.setattr(doctor_mod, name, lambda _ok=ok: _ok)
    monkeypatch.setattr(doctor_mod, "_check_search_server", lambda cfg: ok)
    result = doctor_mod.run_doctor()
    assert result == 1
    assert "issue" in capsys.readouterr().out


def test_check_git_found() -> None:
    with patch("archon.diagnostics.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="git version 2.39")):
        result = doctor_mod._check_git()
    assert result.ok is True


def test_check_git_not_found() -> None:
    with patch("archon.diagnostics.subprocess.run", side_effect=FileNotFoundError):
        result = doctor_mod._check_git()
    assert result.ok is False


def test_check_uv_found() -> None:
    with patch("archon.diagnostics.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="uv 0.4.10")):
        result = doctor_mod._check_uv()
    assert result.ok is True


def test_check_python_312() -> None:
    with patch("archon.diagnostics.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="Python 3.12.3", stderr="")):
        result = doctor_mod._check_python()
    assert result.ok is True


def test_check_python_below_312() -> None:
    with patch("archon.diagnostics.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="Python 3.11.0", stderr="")):
        result = doctor_mod._check_python()
    assert result.ok is False


def test_check_env_file_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diagnostics_mod, "_ARCHON_HOME", tmp_path)
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=abc123\n")
    result = doctor_mod._check_env_file()
    assert result.ok is True


def test_check_env_file_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diagnostics_mod, "_ARCHON_HOME", tmp_path)
    result = doctor_mod._check_env_file()
    assert result.ok is False


def test_check_config_valid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diagnostics_mod, "_ARCHON_HOME", tmp_path)
    (tmp_path / "config.toml").write_text('[access]\nallowed_user_ids = [1]\n')
    result = doctor_mod._check_config_file()
    assert result.ok is True


def test_check_config_invalid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diagnostics_mod, "_ARCHON_HOME", tmp_path)
    (tmp_path / "config.toml").write_text("NOT VALID TOML @@@")
    result = doctor_mod._check_config_file()
    assert result.ok is False


def test_check_logs_dir_writable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diagnostics_mod, "_ARCHON_HOME", tmp_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    result = doctor_mod._check_logs_dir()
    assert result.ok is True


def test_check_logs_dir_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diagnostics_mod, "_ARCHON_HOME", tmp_path)
    result = doctor_mod._check_logs_dir()
    assert result.ok is False


def test_check_health_ok() -> None:
    with patch("archon.diagnostics.urllib.request.urlopen", return_value=MagicMock()):
        result = doctor_mod._check_health()
    assert result.ok is True


def test_check_health_fail() -> None:
    with patch("archon.diagnostics.urllib.request.urlopen", side_effect=Exception("refused")):
        result = doctor_mod._check_health()
    assert result.ok is False


def test_check_app_dir_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diagnostics_mod, "_ARCHON_HOME", tmp_path)
    (tmp_path / "app").mkdir()
    result = doctor_mod._check_app_dir()
    assert result.ok is True


def test_check_app_dir_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diagnostics_mod, "_ARCHON_HOME", tmp_path)
    result = doctor_mod._check_app_dir()
    assert result.ok is False


def test_check_env_file_commented_token_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A commented-out token line must not pass as healthy."""
    monkeypatch.setattr(diagnostics_mod, "_ARCHON_HOME", tmp_path)
    env = tmp_path / ".env"
    env.write_text("# TELEGRAM_BOT_TOKEN=abc123\n")
    result = doctor_mod._check_env_file()
    assert result.ok is False


def test_check_env_file_empty_value_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty token value (TELEGRAM_BOT_TOKEN=) must not pass as healthy."""
    monkeypatch.setattr(diagnostics_mod, "_ARCHON_HOME", tmp_path)
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=\n")
    result = doctor_mod._check_env_file()
    assert result.ok is False


def test_check_env_file_token_keyword_only_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Presence of the keyword in an unrelated string must not pass."""
    monkeypatch.setattr(diagnostics_mod, "_ARCHON_HOME", tmp_path)
    env = tmp_path / ".env"
    env.write_text("# export TELEGRAM_BOT_TOKEN\n")
    result = doctor_mod._check_env_file()
    assert result.ok is False


def test_check_health_reads_port_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_check_health() must use the port from config.toml when available."""
    monkeypatch.setattr(diagnostics_mod, "_ARCHON_HOME", tmp_path)
    (tmp_path / "config.toml").write_text(
        "[background_agents]\nport = 19999\n"
    )
    captured_urls: list[str] = []

    def fake_urlopen(url: str, timeout: int) -> MagicMock:
        captured_urls.append(url)
        return MagicMock()

    with patch("archon.diagnostics.urllib.request.urlopen", side_effect=fake_urlopen):
        doctor_mod._check_health()

    assert len(captured_urls) == 1
    assert ":19999/" in captured_urls[0]


def test_check_health_uses_default_port_when_config_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_check_health() must fall back to port 18182 when config is absent."""
    monkeypatch.setattr(diagnostics_mod, "_ARCHON_HOME", tmp_path)
    # No config.toml in tmp_path
    captured_urls: list[str] = []

    def fake_urlopen(url: str, timeout: int) -> MagicMock:
        captured_urls.append(url)
        raise Exception("refused")

    with patch("archon.diagnostics.urllib.request.urlopen", side_effect=fake_urlopen):
        doctor_mod._check_health()

    assert len(captured_urls) == 1
    assert ":18182/" in captured_urls[0]


# ──────────────────────────────────────────────────────────────────
# _check_bot_token
# ──────────────────────────────────────────────────────────────────


def _mock_urlopen_ok(username: str = "mybot") -> MagicMock:
    """Return a mock for urlopen that returns a valid getMe response."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(
        {"ok": True, "result": {"username": username}}
    ).encode()
    mock_urlopen = MagicMock()
    mock_urlopen.return_value.__enter__ = MagicMock(return_value=mock_resp)
    mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
    return mock_urlopen


def test_check_bot_token_valid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid token returns ok=True with the bot username."""
    monkeypatch.setattr(diagnostics_mod, "_ARCHON_HOME", tmp_path)
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=123:ABC\n")
    with patch("archon.diagnostics.urllib.request.urlopen", _mock_urlopen_ok("archon_bot")):
        result = doctor_mod._check_bot_token()
    assert result.ok is True
    assert "archon_bot" in result.detail


def test_check_bot_token_invalid_401(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP 401 from Telegram means the token is wrong — return ok=False with clear message."""
    monkeypatch.setattr(diagnostics_mod, "_ARCHON_HOME", tmp_path)
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=bad:token\n")
    http_err = urllib.error.HTTPError(None, 401, "Unauthorized", {}, None)  # type: ignore[arg-type]
    with patch("archon.diagnostics.urllib.request.urlopen", side_effect=http_err):
        result = doctor_mod._check_bot_token()
    assert result.ok is False
    assert "invalid" in result.detail.lower() or "unauthorized" in result.detail.lower()


def test_check_bot_token_network_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A network error returns ok=False with a Telegram-related message."""
    monkeypatch.setattr(diagnostics_mod, "_ARCHON_HOME", tmp_path)
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=123:ABC\n")
    with patch("archon.diagnostics.urllib.request.urlopen", side_effect=Exception("timeout")):
        result = doctor_mod._check_bot_token()
    assert result.ok is False
    assert "telegram" in result.detail.lower()


def test_check_bot_token_no_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing .env file returns ok=False."""
    monkeypatch.setattr(diagnostics_mod, "_ARCHON_HOME", tmp_path)
    result = doctor_mod._check_bot_token()
    assert result.ok is False


def test_check_bot_token_missing_in_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty token value in .env returns ok=False without making a network call."""
    monkeypatch.setattr(diagnostics_mod, "_ARCHON_HOME", tmp_path)
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=\n")
    with patch("archon.diagnostics.urllib.request.urlopen") as mock_urlopen:
        result = doctor_mod._check_bot_token()
    assert result.ok is False
    mock_urlopen.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# RAG collection health checks
# ──────────────────────────────────────────────────────────────────

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock


@pytest.fixture(autouse=True)
def _no_state_store_io(monkeypatch):
    """No-op fixture kept for compatibility. IndexingStateStore is no longer in doctor.py.

    New tests use SearchClient HTTP mocks instead.
    """


def _make_rag_config(
    enabled: bool = True,
    url: str = "http://localhost:8282",
    collections: list[str] | None = None,
    pinned_collections: list[str] | None = None,
    # legacy params kept for call-site compatibility but ignored (server-side fields)
    host: str = "localhost",
    port: int = 8282,
    embedding_model: str = "BAAI/bge-small-en-v1.5",
    db_path: str = "/tmp/test_rag_db",
    chunk_size: int = 512,
    auto_reindex_on_chunk_size_change: bool = False,
) -> object:
    """Build a minimal fake config with search section (client-only fields)."""
    class FakeSearch:
        pass

    class FakeCfg:
        pass

    search = FakeSearch()
    search.enabled = enabled
    search.url = url
    # Provide host_port as a tuple property (duck-typed)
    import urllib.parse
    _parsed = urllib.parse.urlparse(url)
    search.host_port = (_parsed.hostname or "127.0.0.1", _parsed.port or 8765)

    cfg = FakeCfg()
    cfg.search = search
    return cfg


def _make_meta_response(collections: list[dict]) -> dict:
    """Build a FastMCP JSON-RPC response containing the given collection dicts."""
    return {
        "result": {
            "content": [
                {"type": "text", "text": json.dumps(collections)}
            ]
        }
    }


def _make_httpx_response(data: dict) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.json.return_value = data
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


def _run(coro):
    return asyncio.run(coro)


def test_doctor_warns_stale_collection(capsys: pytest.CaptureFixture) -> None:
    """_check_search_health prints a warning for collections last indexed >7 days ago."""
    cfg = _make_rag_config()
    old_date = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    response_data = _make_meta_response([
        {
            "name": "my_collection",
            "doc_count": 5,
            "chunk_count": 10,
            "embedding_model": "BAAI/bge-small-en-v1.5",
            "centroid": [0.1, 0.2],
            "last_indexed": old_date,
        }
    ])
    with _mock_http(response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "⚠ Collection 'my_collection' last indexed 10 days ago" in out


def test_doctor_warns_model_mismatch(capsys: pytest.CaptureFixture) -> None:
    """Model mismatch checks are server-side — archon doctor no longer warns on model differences."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config()
    state = IndexingState(collections={
        "my_collection": CollectionProgress(status=IndexingStatus.DONE)
    })
    recent_date = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    response_data = _make_meta_response([
        {
            "name": "my_collection",
            "doc_count": 5,
            "chunk_count": 10,
            "embedding_model": "old-model/v1",
            "centroid": [0.1, 0.2],
            "last_indexed": recent_date,
        }
    ])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "reindex required" not in out
    assert "✅" in out


def test_doctor_warns_empty_collection(capsys: pytest.CaptureFixture) -> None:
    """_check_search_health prints a warning for collections with doc_count == 0."""
    cfg = _make_rag_config()
    recent_date = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    response_data = _make_meta_response([
        {
            "name": "empty_col",
            "doc_count": 0,
            "chunk_count": 0,
            "embedding_model": "BAAI/bge-small-en-v1.5",
            "centroid": [0.1, 0.2],
            "last_indexed": recent_date,
        }
    ])
    with _mock_http(response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "⚠ Collection 'empty_col' is empty" in out


def test_doctor_warns_missing_centroid(capsys: pytest.CaptureFixture) -> None:
    """_check_search_health prints a warning when centroid is None."""
    cfg = _make_rag_config()
    recent_date = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    response_data = _make_meta_response([
        {
            "name": "no_centroid_col",
            "doc_count": 5,
            "chunk_count": 10,
            "embedding_model": "BAAI/bge-small-en-v1.5",
            "centroid": None,
            "last_indexed": recent_date,
        }
    ])
    with _mock_http(response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "⚠ Collection 'no_centroid_col' has no centroid — routing disabled for this collection" in out


def test_doctor_no_warnings_on_healthy_collections(capsys: pytest.CaptureFixture) -> None:
    """_check_search_health prints no warnings for a healthy collection."""
    cfg = _make_rag_config(embedding_model="BAAI/bge-small-en-v1.5")
    recent_date = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    response_data = _make_meta_response([
        {
            "name": "healthy_col",
            "doc_count": 10,
            "chunk_count": 50,
            "embedding_model": "BAAI/bge-small-en-v1.5",
            "centroid": [0.1, 0.2, 0.3],
            "last_indexed": recent_date,
        }
    ])
    with _mock_http(response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "⚠" not in out


def test_doctor_skips_rag_checks_when_server_down(capsys: pytest.CaptureFixture) -> None:
    """_check_search_health skips collection checks when list_collections() returns empty."""
    cfg = _make_rag_config()
    # SearchClient health() returns ok, but list_collections() returns empty (no collections)
    with patch("archon.cli.doctor.SearchClient") as mock_cls:
        sc_mock = AsyncMock()
        sc_mock.health = AsyncMock(return_value={"status": "ok"})
        sc_mock.indexing_state = AsyncMock(return_value={"collections": {}})
        sc_mock.list_collections = AsyncMock(return_value=[])
        sc_mock.collection_info = AsyncMock(return_value=None)
        sc_mock.__aenter__ = AsyncMock(return_value=sc_mock)
        sc_mock.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = sc_mock
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "⚠" not in out


def test_doctor_does_not_warn_pinned_not_in_collections_legacy(capsys: pytest.CaptureFixture) -> None:
    """_check_search_health must NOT warn about pinned paths absent from search.collections (removed check)."""
    cfg = _make_rag_config(
        collections=["~/.archon/history/sessions"],
        pinned_collections=["~/.archon/history/sessions", "~/.archon/workspace"],
    )
    recent_date = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    response_data = _make_meta_response([
        {
            "name": "~/.archon/history/sessions",
            "doc_count": 5,
            "chunk_count": 20,
            "embedding_model": "BAAI/bge-small-en-v1.5",
            "centroid": [0.1, 0.2],
            "last_indexed": recent_date,
        }
    ])
    with _mock_http(response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "is not declared in search.collections" not in out


def test_doctor_pinned_check_removed_when_server_down(capsys: pytest.CaptureFixture) -> None:
    """Pinned-not-in-collections check is removed; no such warning when list_collections empty."""
    cfg = _make_rag_config(
        collections=["~/.archon/history/sessions"],
        pinned_collections=["~/.archon/history/sessions", "~/.archon/workspace"],
    )
    with patch("archon.cli.doctor.SearchClient") as mock_cls:
        sc_mock = AsyncMock()
        sc_mock.health = AsyncMock(return_value={"status": "ok"})
        sc_mock.indexing_state = AsyncMock(return_value={"collections": {}})
        sc_mock.list_collections = AsyncMock(return_value=[])
        sc_mock.collection_info = AsyncMock(return_value=None)
        sc_mock.__aenter__ = AsyncMock(return_value=sc_mock)
        sc_mock.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = sc_mock
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "is not declared in search.collections" not in out


def test_doctor_does_not_warn_stale_at_boundary_7_days(capsys: pytest.CaptureFixture) -> None:
    """_check_search_health does NOT warn for a collection last indexed exactly 7 days ago."""
    cfg = _make_rag_config()
    boundary_date = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    response_data = _make_meta_response([
        {
            "name": "boundary_col",
            "doc_count": 5,
            "chunk_count": 10,
            "embedding_model": "BAAI/bge-small-en-v1.5",
            "centroid": [0.1, 0.2],
            "last_indexed": boundary_date,
        }
    ])
    with _mock_http(response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "last indexed" not in out


def test_doctor_warns_stale_at_8_days(capsys: pytest.CaptureFixture) -> None:
    """_check_search_health warns for a collection last indexed 8 days ago."""
    cfg = _make_rag_config()
    old_date = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    response_data = _make_meta_response([
        {
            "name": "stale_col",
            "doc_count": 5,
            "chunk_count": 10,
            "embedding_model": "BAAI/bge-small-en-v1.5",
            "centroid": [0.1, 0.2],
            "last_indexed": old_date,
        }
    ])
    with _mock_http(response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "⚠ Collection 'stale_col' last indexed 8 days ago" in out


def test_doctor_no_staleness_warning_when_last_indexed_missing(capsys: pytest.CaptureFixture) -> None:
    """_check_search_health does NOT warn about staleness when last_indexed is absent."""
    cfg = _make_rag_config()
    response_data = _make_meta_response([
        {
            "name": "no_date_col",
            "doc_count": 5,
            "chunk_count": 10,
            "embedding_model": "BAAI/bge-small-en-v1.5",
            "centroid": [0.1, 0.2],
            # deliberately no "last_indexed" key
        }
    ])
    with _mock_http(response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "last indexed" not in out


# ──────────────────────────────────────────────────────────────────
# _check_search_server
# ──────────────────────────────────────────────────────────────────

import importlib.util
import socket


def _make_full_config(
    search_enabled: bool = True,
    url: str = "http://localhost:8282",
) -> object:
    """Build a minimal fake Config with a search section."""
    class FakeSearch:
        pass

    class FakeCfg:
        pass

    search = FakeSearch()
    search.enabled = search_enabled
    search.url = url

    import urllib.parse
    parsed = urllib.parse.urlparse(url)
    search.host_port = (parsed.hostname or "127.0.0.1", parsed.port or 8765)

    cfg = FakeCfg()
    cfg.search = search
    return cfg


class TestCheckRagServer:
    def test_disabled_returns_ok(self) -> None:
        cfg = _make_full_config(search_enabled=False)
        result = doctor_mod._check_search_server(cfg)
        assert result.ok is True
        assert result.detail == "disabled"
        assert result.name == "search server"

    def test_not_installed_returns_fail_with_install_guidance(self) -> None:
        cfg = _make_full_config(search_enabled=True)
        with patch("importlib.util.find_spec", return_value=None):
            result = doctor_mod._check_search_server(cfg)
        assert result.ok is False
        assert "not installed" in result.detail
        assert "archon search install" in result.detail

    def test_not_running_returns_fail_with_start_guidance(self) -> None:
        cfg = _make_full_config(search_enabled=True)
        with patch("importlib.util.find_spec", return_value=MagicMock()), \
             patch("archon.cli.doctor.socket.create_connection", side_effect=OSError("connection refused")):
            result = doctor_mod._check_search_server(cfg)
        assert result.ok is False
        assert "archon search start" in result.detail

    def test_running_returns_ok(self) -> None:
        cfg = _make_full_config(search_enabled=True)
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        with patch("importlib.util.find_spec", return_value=MagicMock()), \
             patch("archon.cli.doctor.socket.create_connection", return_value=mock_conn):
            result = doctor_mod._check_search_server(cfg)
        assert result.ok is True
        assert result.detail == "running"


def test_run_doctor_search_health_called_when_search_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """run_doctor() calls _check_search_health when config.toml exists and RAG is enabled."""
    ok = CheckResult("x", True, "OK")
    for name in _ALL_CHECKS:
        monkeypatch.setattr(doctor_mod, name, lambda _ok=ok: _ok)

    # Point _ARCHON_HOME at a temp dir with a fake config.toml so cfg_path.exists() is True
    fake_config_toml = tmp_path / "config.toml"
    fake_config_toml.write_text("")
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)

    # Build a fake config object with RAG enabled
    class FakeRag:
        enabled = True

    class FakeCfg:
        search = FakeRag()

    mock_check = AsyncMock()

    with patch("archon.cli.doctor._check_search_health", mock_check):
        with patch("archon.config.config", FakeCfg()):
            # Also patch _check_search_server to return ok so _check_search_health is called
            monkeypatch.setattr(
                doctor_mod, "_check_search_server",
                lambda cfg: CheckResult("search server", True, "running")
            )
            doctor_mod.run_doctor()

    mock_check.assert_called_once()


# ──────────────────────────────────────────────────────────────────
# TestRunDoctorRagExitCode
# ──────────────────────────────────────────────────────────────────


def _make_all_ok_monkeypatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch all non-RAG checks to return ok=True."""
    ok = CheckResult("x", True, "OK")
    for name in _ALL_CHECKS:
        monkeypatch.setattr(doctor_mod, name, lambda _ok=ok: _ok)


class TestRunDoctorRagExitCode:
    def test_returns_1_when_search_enabled_not_running(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """run_doctor() returns 1 when all non-RAG checks pass but RAG server is not running."""
        _make_all_ok_monkeypatch(monkeypatch)

        fake_config_toml = tmp_path / "config.toml"
        fake_config_toml.write_text("")
        monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)

        rag_fail = CheckResult("search server", False, "not running — run: archon search start")

        class FakeRag:
            enabled = True

        class FakeCfg:
            search = FakeRag()

        monkeypatch.setattr(doctor_mod, "_check_search_server", lambda cfg: rag_fail)

        with patch("archon.cli.doctor._check_search_health", AsyncMock()):
            with patch("archon.config.config", FakeCfg()):
                result = doctor_mod.run_doctor()

        assert result == 1

    def test_returns_0_when_rag_disabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """run_doctor() returns 0 when all checks pass and RAG is disabled."""
        _make_all_ok_monkeypatch(monkeypatch)

        fake_config_toml = tmp_path / "config.toml"
        fake_config_toml.write_text("")
        monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)

        rag_ok = CheckResult("search server", True, "disabled")

        class FakeRag:
            enabled = False

        class FakeCfg:
            search = FakeRag()

        monkeypatch.setattr(doctor_mod, "_check_search_server", lambda cfg: rag_ok)

        with patch("archon.cli.doctor._check_search_health", AsyncMock()):
            with patch("archon.config.config", FakeCfg()):
                result = doctor_mod.run_doctor()

        assert result == 0

    def test_prints_check_mark_x_for_rag_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """run_doctor() prints ✗ and 'rag server' when RAG server check fails."""
        _make_all_ok_monkeypatch(monkeypatch)

        fake_config_toml = tmp_path / "config.toml"
        fake_config_toml.write_text("")
        monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)

        rag_fail = CheckResult("search server", False, "not running — run: archon search start")

        class FakeRag:
            enabled = True

        class FakeCfg:
            search = FakeRag()

        monkeypatch.setattr(doctor_mod, "_check_search_server", lambda cfg: rag_fail)

        with patch("archon.cli.doctor._check_search_health", AsyncMock()):
            with patch("archon.config.config", FakeCfg()):
                doctor_mod.run_doctor()

        out = capsys.readouterr().out
        assert "✗" in out
        assert "search server" in out

    def test_collection_checks_skipped_when_server_not_running(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """_check_search_health is NOT called when _check_search_server returns a failing result."""
        _make_all_ok_monkeypatch(monkeypatch)

        fake_config_toml = tmp_path / "config.toml"
        fake_config_toml.write_text("")
        monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)

        rag_fail = CheckResult("search server", False, "not running — run: archon search start")

        class FakeRag:
            enabled = True

        class FakeCfg:
            search = FakeRag()

        monkeypatch.setattr(doctor_mod, "_check_search_server", lambda cfg: rag_fail)

        mock_health = AsyncMock()
        with patch("archon.cli.doctor._check_search_health", mock_health):
            with patch("archon.config.config", FakeCfg()):
                doctor_mod.run_doctor()

        mock_health.assert_not_called()


# ---------------------------------------------------------------------------
# Task 2.5 — Doctor: state file integration + partial/pending/failed suppression
# ---------------------------------------------------------------------------

def _make_healthy_col(name: str, doc_count: int = 5) -> dict:
    """Build a healthy collection dict for JSON-RPC response."""
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    return {
        "name": name,
        "doc_count": doc_count,
        "chunk_count": doc_count * 10,
        "embedding_model": "BAAI/bge-small-en-v1.5",
        "centroid": [0.1, 0.2],
        "last_indexed": recent,
    }


class _MockHttp:
    """Context manager: mock SearchClient (health+state+list_collections+collection_info).

    Accepts the legacy JSON-RPC response_data format for backward compatibility
    with existing tests. Extracts collection metadata from the JSON-RPC payload and
    wires it into the new list_collections() + collection_info() REST mocks.
    """

    def __init__(self, response_data: dict, state_data: dict | None = None):
        self._response_data = response_data
        self._state_data = state_data if state_data is not None else {"collections": {}}
        self._sc_patcher = None

    def _extract_collections(self) -> list[dict]:
        """Parse collections from legacy JSON-RPC response_data format."""
        content_blocks = self._response_data.get("result", {}).get("content", [])
        if not content_blocks or content_blocks[0].get("type") != "text":
            return []
        try:
            return json.loads(content_blocks[0]["text"])
        except (json.JSONDecodeError, KeyError):
            return []

    def __enter__(self):
        raw_collections = self._extract_collections()

        # Build per-collection info dicts for the REST mock
        col_info_map: dict[str, dict] = {}
        for col in raw_collections:
            if not isinstance(col, dict) or "name" not in col:
                continue
            name = col["name"]
            col_info_map[name] = {
                "doc_count": col.get("doc_count", 0),
                "centroid_present": col.get("centroid") is not None,
                "last_indexed": col.get("last_indexed"),
            }

        async def fake_collection_info(name: str) -> dict | None:
            return col_info_map.get(name)

        # Mock SearchClient for all methods
        sc_mock = AsyncMock()
        sc_mock.health = AsyncMock(return_value={"status": "ok"})
        sc_mock.indexing_state = AsyncMock(return_value=self._state_data)
        sc_mock.list_collections = AsyncMock(
            return_value=[{"name": n} for n in col_info_map]
        )
        sc_mock.collection_info = fake_collection_info
        sc_mock.__aenter__ = AsyncMock(return_value=sc_mock)
        sc_mock.__aexit__ = AsyncMock(return_value=False)
        self._sc_patcher = patch("archon.cli.doctor.SearchClient", return_value=sc_mock)
        self._sc_patcher.__enter__()

        return sc_mock

    def __exit__(self, *args):
        self._sc_patcher.__exit__(*args)


def _mock_http(response_data: dict, state_data: dict | None = None):
    """Context manager: mock SearchClient HTTP calls (list_collections + collection_info)."""
    return _MockHttp(response_data, state_data)


def _state_to_indexing_state_response(state) -> dict:
    """Convert an IndexingState object to the HTTP /indexing-state response dict."""
    if state is None:
        return {"collections": {}}
    return {
        "collections": {
            name: {
                "status": str(cp.status),
                "processed_files": cp.processed_files,
                "total_files": cp.total_files,
                "error": cp.error,
                "indexed_chunk_size": cp.indexed_chunk_size,
            }
            for name, cp in state.collections.items()
        }
    }


class _MockStateStore:
    """Context manager: mock SearchClient with state + optional collection metadata.

    When response_data is provided, also mocks list_collections() + collection_info()
    using the collection metadata extracted from the legacy JSON-RPC response_data format.
    """

    def __init__(self, state, response_data: dict | None = None):
        self._state_data = _state_to_indexing_state_response(state)
        self._response_data = response_data
        self._mock_client = None
        self._patcher = None

    def _extract_col_info_map(self) -> dict:
        if not self._response_data:
            return {}
        content_blocks = self._response_data.get("result", {}).get("content", [])
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

    def __enter__(self):
        col_info_map = self._extract_col_info_map()

        async def fake_collection_info(name: str) -> dict | None:
            return col_info_map.get(name)

        mock_client = AsyncMock()
        mock_client.health = AsyncMock(return_value={"status": "ok"})
        mock_client.indexing_state = AsyncMock(return_value=self._state_data)
        mock_client.list_collections = AsyncMock(
            return_value=[{"name": n} for n in col_info_map]
        )
        mock_client.collection_info = fake_collection_info
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        self._mock_client = mock_client
        self._patcher = patch("archon.cli.doctor.SearchClient", return_value=mock_client)
        self._patcher.__enter__()
        return mock_client

    def __exit__(self, *args):
        self._patcher.__exit__(*args)


def _mock_state_store(state, response_data: dict | None = None):
    """Context manager: mock SearchClient.indexing_state() to return the given IndexingState.

    Pass response_data to also mock list_collections() + collection_info().
    """
    return _MockStateStore(state, response_data)


def test_in_progress_label_is_in_progress(capsys: pytest.CaptureFixture) -> None:
    """IN_PROGRESS + processed_files=50 → prints ⏳ partial (N/M files), no ⚠."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config()
    state = IndexingState(collections={
        "docs": CollectionProgress(
            status=IndexingStatus.IN_PROGRESS,
            total_files=100,
            processed_files=50,
        )
    })
    response_data = _make_meta_response([_make_healthy_col("docs")])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "⏳" in out
    assert "partial" in out
    assert "50" in out and "100" in out
    assert "⚠" not in out


def test_in_progress_no_files_label(capsys: pytest.CaptureFixture) -> None:
    """IN_PROGRESS + processed_files=0 → prints ⏳ indexing starting, no ⚠."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config()
    state = IndexingState(collections={
        "docs": CollectionProgress(
            status=IndexingStatus.IN_PROGRESS,
            total_files=100,
            processed_files=0,
        )
    })
    response_data = _make_meta_response([_make_healthy_col("docs", doc_count=0)])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "⏳" in out
    assert "indexing starting" in out
    assert "⚠" not in out


def test_doctor_pending_no_warning(capsys: pytest.CaptureFixture) -> None:
    """PENDING state → prints ⏳ pending, no ⚠."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config()
    state = IndexingState(collections={
        "docs": CollectionProgress(status=IndexingStatus.PENDING)
    })
    response_data = _make_meta_response([_make_healthy_col("docs")])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "⏳" in out
    assert "pending" in out
    assert "⚠" not in out


def test_doctor_failed_still_warns(capsys: pytest.CaptureFixture) -> None:
    """FAILED state (JSON-RPC path) → prints ❌ with error message."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config()
    state = IndexingState(collections={
        "docs": CollectionProgress(
            status=IndexingStatus.FAILED,
            error="Embedding API timeout",
        )
    })
    response_data = _make_meta_response([_make_healthy_col("docs")])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "❌" in out
    assert "failed" in out
    assert "Embedding API timeout" in out


def test_doctor_done_staleness_still_checked(capsys: pytest.CaptureFixture) -> None:
    """DONE state → staleness check still runs; stale collection still triggers ⚠."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config()
    state = IndexingState(collections={
        "docs": CollectionProgress(status=IndexingStatus.DONE)
    })
    old_date = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    col = {
        "name": "docs",
        "doc_count": 5,
        "chunk_count": 50,
        "embedding_model": "BAAI/bge-small-en-v1.5",
        "centroid": [0.1, 0.2],
        "last_indexed": old_date,
    }
    response_data = _make_meta_response([col])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "⚠" in out
    assert "last indexed" in out


def test_doctor_state_only_collection_visible(capsys: pytest.CaptureFixture) -> None:
    """Collection in state file but not in JSON-RPC → still printed (not invisible)."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config()
    state = IndexingState(collections={
        "pending_col": CollectionProgress(
            status=IndexingStatus.PENDING,
            total_files=20,
            processed_files=0,
        )
    })
    response_data = _make_meta_response([])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "pending_col" in out
    assert "⏳" in out


def test_doctor_state_only_failed_warns(capsys: pytest.CaptureFixture) -> None:
    """FAILED in state file but not in JSON-RPC → prints ❌ (state-only path)."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config()
    state = IndexingState(collections={
        "bad_col": CollectionProgress(
            status=IndexingStatus.FAILED,
            error="Disk full",
        )
    })
    response_data = _make_meta_response([])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "❌" in out
    assert "bad_col" in out
    assert "Disk full" in out


def test_doctor_missing_state_file_fallback(capsys: pytest.CaptureFixture) -> None:
    """State file returns None → existing staleness checks run unchanged."""
    cfg = _make_rag_config()
    old_date = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    col = {
        "name": "docs",
        "doc_count": 5,
        "chunk_count": 50,
        "embedding_model": "BAAI/bge-small-en-v1.5",
        "centroid": [0.1, 0.2],
        "last_indexed": old_date,
    }
    response_data = _make_meta_response([col])
    with _mock_state_store(None, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "⚠" in out
    assert "last indexed" in out


def test_doctor_reads_state_file(capsys: pytest.CaptureFixture) -> None:
    """Integration: doctor calls SearchClient.indexing_state() and shows in_progress status."""
    cfg = _make_rag_config(host="localhost", port=8282)
    state_data = {
        "collections": {
            "docs": {
                "status": "in_progress",
                "processed_files": 20,
                "total_files": 40,
                "error": None,
                "indexed_chunk_size": 0,
            }
        }
    }
    response_data = _make_meta_response([_make_healthy_col("docs")])

    captured_urls: list[str] = []

    def fake_search_client(url: str):
        captured_urls.append(url)
        sc_mock = AsyncMock()
        sc_mock.health = AsyncMock(return_value={"status": "ok"})
        sc_mock.indexing_state = AsyncMock(return_value=state_data)
        sc_mock.__aenter__ = AsyncMock(return_value=sc_mock)
        sc_mock.__aexit__ = AsyncMock(return_value=False)
        return sc_mock

    with _mock_http(response_data):
        with patch("archon.cli.doctor.SearchClient", side_effect=fake_search_client):
            _run(doctor_mod._check_search_health(cfg))

    assert captured_urls == ["http://localhost:8282"]
    out = capsys.readouterr().out
    # in_progress with 20/40 files processed → ⏳ line with "docs" and file counts
    assert "docs" in out
    assert "20" in out and "40" in out


def test_doctor_chunk_size_mismatch_warning(capsys: pytest.CaptureFixture) -> None:
    """Chunk size mismatch checks are server-side — archon doctor no longer warns on chunk differences."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config()
    state = IndexingState(collections={
        "docs": CollectionProgress(
            status=IndexingStatus.DONE,
            indexed_chunk_size=512,
        )
    })
    response_data = _make_meta_response([_make_healthy_col("docs")])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "chunk size mismatch" not in out
    assert "✅" in out


def test_doctor_chunk_size_mismatch_auto_reindex_suppressed(capsys: pytest.CaptureFixture) -> None:
    """Chunk mismatch + auto_reindex — all chunk checks are server-side, archon doctor shows ✅."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config()
    state = IndexingState(collections={
        "docs": CollectionProgress(
            status=IndexingStatus.DONE,
            indexed_chunk_size=512,
        )
    })
    response_data = _make_meta_response([_make_healthy_col("docs")])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "chunk size mismatch" not in out
    assert "auto-reindex pending" not in out
    assert "✅" in out


def test_doctor_chunk_size_mismatch_auto_reindex_with_stale(capsys: pytest.CaptureFixture) -> None:
    """DONE + stale → staleness ⚠ shown; chunk mismatch and auto-reindex are server-side, no ✅."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus
    from datetime import datetime, timedelta, timezone

    cfg = _make_rag_config()
    state = IndexingState(collections={
        "docs": CollectionProgress(
            status=IndexingStatus.DONE,
            indexed_chunk_size=512,
        )
    })
    # days_old=10 > _SEARCH_STALE_DAYS (7) → staleness warning fires
    response_data = _make_meta_response([_make_done_col("docs", days_old=10, indexed_chunk_size=512)])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "chunk size mismatch" not in out
    assert "auto-reindex pending" not in out
    assert "✅" not in out
    assert "last indexed 10 days ago" in out


def test_doctor_chunk_size_zero_no_warning(capsys: pytest.CaptureFixture) -> None:
    """indexed_chunk_size=0 (never indexed) → no chunk size warning even if config differs."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config(chunk_size=256, auto_reindex_on_chunk_size_change=False)
    state = IndexingState(collections={
        "docs": CollectionProgress(
            status=IndexingStatus.DONE,
            indexed_chunk_size=0,  # default — size was never recorded
        )
    })
    response_data = _make_meta_response([_make_healthy_col("docs")])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "chunk size mismatch" not in out


def test_doctor_chunk_size_no_state_no_warning(capsys: pytest.CaptureFixture) -> None:
    """Collection present in LanceDB but absent from state (cp=None) → no chunk size warning."""
    from archon_search.progress import IndexingState

    cfg = _make_rag_config(chunk_size=256, auto_reindex_on_chunk_size_change=False)
    # State exists but has no entry for "docs"
    state = IndexingState(collections={})
    response_data = _make_meta_response([_make_healthy_col("docs")])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "chunk size mismatch" not in out


def test_doctor_chunk_size_match_no_warning(capsys: pytest.CaptureFixture) -> None:
    """indexed_chunk_size == config chunk_size → no chunk size warning."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config(chunk_size=512, auto_reindex_on_chunk_size_change=False)
    state = IndexingState(collections={
        "docs": CollectionProgress(
            status=IndexingStatus.DONE,
            indexed_chunk_size=512,
        )
    })
    response_data = _make_meta_response([_make_healthy_col("docs")])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "chunk size mismatch" not in out


# Task 6.1 — Rename IN_PROGRESS label and add PENDING partial detection

def test_pending_with_prior_progress_shows_partial(capsys: pytest.CaptureFixture) -> None:
    """PENDING + processed_files > 0 → output contains 'partial' with ⚠️ and NOT '— pending'."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config()
    state = IndexingState(collections={
        "docs": CollectionProgress(
            status=IndexingStatus.PENDING,
            total_files=60,
            processed_files=30,
        )
    })
    response_data = _make_meta_response([_make_healthy_col("docs")])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "⚠️" in out
    assert "partial" in out
    assert "30" in out and "60" in out
    assert "— pending" not in out


def test_pending_fresh_shows_pending(capsys: pytest.CaptureFixture) -> None:
    """PENDING + processed_files == 0 → output contains 'pending' with ⏳ and NOT 'partial'."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config()
    state = IndexingState(collections={
        "docs": CollectionProgress(
            status=IndexingStatus.PENDING,
            total_files=0,
            processed_files=0,
        )
    })
    response_data = _make_meta_response([_make_healthy_col("docs")])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "⏳" in out
    assert "pending" in out
    assert "partial" not in out


def test_state_only_in_progress_label(capsys: pytest.CaptureFixture) -> None:
    """IN_PROGRESS + processed_files > 0 in state but NOT in LanceDB → output contains 'in_progress'."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config()
    state = IndexingState(collections={
        "new_col": CollectionProgress(
            status=IndexingStatus.IN_PROGRESS,
            total_files=50,
            processed_files=25,
        )
    })
    response_data = _make_meta_response([])  # not in LanceDB
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "in_progress" in out
    assert "new_col" in out
    assert "partial" not in out


def test_state_only_in_progress_no_files_label(capsys: pytest.CaptureFixture) -> None:
    """IN_PROGRESS + processed_files == 0 in state but NOT in LanceDB → output contains 'indexing starting'."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config()
    state = IndexingState(collections={
        "new_col": CollectionProgress(
            status=IndexingStatus.IN_PROGRESS,
            total_files=50,
            processed_files=0,
        )
    })
    response_data = _make_meta_response([])  # not in LanceDB
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "⏳" in out
    assert "indexing starting" in out
    assert "new_col" in out
    assert "in_progress" not in out


def test_state_only_pending_partial(capsys: pytest.CaptureFixture) -> None:
    """PENDING + processed_files > 0 in state but NOT in LanceDB → output contains 'partial' with ⚠️."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config()
    state = IndexingState(collections={
        "new_col": CollectionProgress(
            status=IndexingStatus.PENDING,
            total_files=50,
            processed_files=25,
        )
    })
    response_data = _make_meta_response([])  # not in LanceDB
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "⚠️" in out
    assert "partial" in out
    assert "new_col" in out
    assert "25" in out and "50" in out


def test_state_only_pending_fresh(capsys: pytest.CaptureFixture) -> None:
    """PENDING + processed_files == 0 in state but NOT in LanceDB → output contains 'pending' with ⏳."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config()
    state = IndexingState(collections={
        "new_col": CollectionProgress(
            status=IndexingStatus.PENDING,
            total_files=0,
            processed_files=0,
        )
    })
    response_data = _make_meta_response([])  # not in LanceDB
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "⏳" in out
    assert "pending" in out
    assert "new_col" in out
    assert "partial" not in out


def test_state_only_done_silently_skipped(capsys: pytest.CaptureFixture) -> None:
    """DONE in state file, collection NOT in LanceDB → collection name does NOT appear in output."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config()
    state = IndexingState(collections={
        "ghost_col": CollectionProgress(status=IndexingStatus.DONE)
    })
    response_data = _make_meta_response([])  # not in LanceDB
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "ghost_col" not in out


# ---------------------------------------------------------------------------
# Task 6.2 — ✅ done positive confirmation for healthy DONE collections
# ---------------------------------------------------------------------------


def _make_done_col(
    name: str,
    doc_count: int = 10,
    embedding_model: str = "BAAI/bge-small-en-v1.5",
    centroid: list | None = None,
    days_old: int = 1,
    indexed_chunk_size: int = 512,
) -> dict:
    """Build a collection dict that is healthy by default."""
    recent = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    return {
        "name": name,
        "doc_count": doc_count,
        "chunk_count": doc_count * 5,
        "embedding_model": embedding_model,
        "centroid": centroid if centroid is not None else [0.1, 0.2],
        "last_indexed": recent,
        "indexed_chunk_size": indexed_chunk_size,
    }


def test_done_no_issues_prints_checkmark(capsys: pytest.CaptureFixture) -> None:
    """DONE, recent, matching model, doc_count > 0, centroid, chunk matches → prints ✅ with 'done' and doc count."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config(chunk_size=512)
    state = IndexingState(collections={
        "my_col": CollectionProgress(
            status=IndexingStatus.DONE,
            indexed_chunk_size=512,
        )
    })
    response_data = _make_meta_response([_make_done_col("my_col", doc_count=42)])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "✅" in out
    assert "done" in out
    assert "42" in out


def test_done_stale_no_checkmark(capsys: pytest.CaptureFixture) -> None:
    """DONE + last_indexed > 7 days ago → staleness ⚠ printed; no ✅ line."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config(chunk_size=512)
    state = IndexingState(collections={
        "stale_col": CollectionProgress(
            status=IndexingStatus.DONE,
            indexed_chunk_size=512,
        )
    })
    response_data = _make_meta_response([_make_done_col("stale_col", days_old=10)])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "⚠" in out
    assert "last indexed" in out
    assert "✅" not in out


def test_done_model_mismatch_no_checkmark(capsys: pytest.CaptureFixture) -> None:
    """DONE + embedding model differs — model/chunk checks are now server-side.
    Archon doctor no longer has the configured model in its config, so collections
    with only a model mismatch (no staleness, not empty, has centroid) show ✅."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config(embedding_model="BAAI/bge-small-en-v1.5", chunk_size=512)
    state = IndexingState(collections={
        "mismatch_col": CollectionProgress(
            status=IndexingStatus.DONE,
            indexed_chunk_size=512,
        )
    })
    response_data = _make_meta_response([
        _make_done_col("mismatch_col", embedding_model="old-model/v1")
    ])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    # Model mismatch check is server-side; archon doctor no longer warns on it.
    assert "reindex required" not in out
    assert "✅" in out


def test_done_empty_no_checkmark(capsys: pytest.CaptureFixture) -> None:
    """DONE + doc_count == 0 → empty ⚠ printed; no ✅ line."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config(chunk_size=512)
    state = IndexingState(collections={
        "empty_col": CollectionProgress(
            status=IndexingStatus.DONE,
            indexed_chunk_size=512,
        )
    })
    response_data = _make_meta_response([_make_done_col("empty_col", doc_count=0)])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "⚠" in out
    assert "is empty" in out
    assert "✅" not in out


def test_done_chunk_mismatch_no_checkmark(capsys: pytest.CaptureFixture) -> None:
    """DONE + indexed_chunk_size != config chunk_size — chunk mismatch checks are now server-side.
    Archon doctor no longer has chunk_size in its config, so collections with only a chunk mismatch
    (no staleness, not empty, has centroid) show ✅."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config()
    state = IndexingState(collections={
        "chunk_col": CollectionProgress(
            status=IndexingStatus.DONE,
            indexed_chunk_size=512,
        )
    })
    response_data = _make_meta_response([_make_done_col("chunk_col")])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "chunk size mismatch" not in out
    assert "✅" in out


def test_done_missing_centroid_no_checkmark(capsys: pytest.CaptureFixture) -> None:
    """DONE + centroid is None → centroid ⚠ printed; no ✅ line."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config(chunk_size=512)
    state = IndexingState(collections={
        "no_centroid": CollectionProgress(
            status=IndexingStatus.DONE,
            indexed_chunk_size=512,
        )
    })
    col = _make_done_col("no_centroid")
    col["centroid"] = None
    response_data = _make_meta_response([col])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "⚠" in out
    assert "no centroid" in out
    assert "✅" not in out


def test_done_multiple_issues_no_checkmark(capsys: pytest.CaptureFixture) -> None:
    """DONE + stale → staleness ⚠ printed; model mismatch is server-side so no 'reindex required'; no ✅."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config()
    state = IndexingState(collections={
        "multi_issue": CollectionProgress(
            status=IndexingStatus.DONE,
            indexed_chunk_size=512,
        )
    })
    response_data = _make_meta_response([
        _make_done_col("multi_issue", days_old=10, embedding_model="old-model/v1")
    ])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "last indexed" in out
    assert "reindex required" not in out
    assert "✅" not in out


def test_done_no_state_no_checkmark(capsys: pytest.CaptureFixture) -> None:
    """Collection in LanceDB with healthy metadata, but state is None → no ✅ line (cp is None)."""
    from archon_search.progress import IndexingState

    cfg = _make_rag_config(chunk_size=512)
    # State has NO entry for "healthy_col"
    state = IndexingState(collections={})
    response_data = _make_meta_response([_make_done_col("healthy_col")])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "✅" not in out


def test_failed_no_checkmark(capsys: pytest.CaptureFixture) -> None:
    """FAILED collection → ❌ line printed; no ✅ line."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config(chunk_size=512)
    state = IndexingState(collections={
        "failed_col": CollectionProgress(
            status=IndexingStatus.FAILED,
            error="Embedding timeout",
        )
    })
    response_data = _make_meta_response([_make_done_col("failed_col")])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "❌" in out
    assert "✅" not in out


def test_in_progress_no_checkmark(capsys: pytest.CaptureFixture) -> None:
    """IN_PROGRESS collection → ⏳ status line printed; no ✅ line."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config(chunk_size=512)
    state = IndexingState(collections={
        "active_col": CollectionProgress(
            status=IndexingStatus.IN_PROGRESS,
            total_files=100,
            processed_files=40,
        )
    })
    response_data = _make_meta_response([_make_done_col("active_col")])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "⏳" in out
    assert "✅" not in out


def test_pending_no_checkmark(capsys: pytest.CaptureFixture) -> None:
    """PENDING collection → ⏳ pending line printed; no ✅ line."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config(chunk_size=512)
    state = IndexingState(collections={
        "queued_col": CollectionProgress(
            status=IndexingStatus.PENDING,
            total_files=0,
            processed_files=0,
        )
    })
    response_data = _make_meta_response([_make_done_col("queued_col")])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "⏳" in out
    assert "pending" in out
    assert "✅" not in out


def test_done_chunk_mismatch_auto_reindex_shows_checkmark(capsys: pytest.CaptureFixture) -> None:
    """DONE + chunk mismatch — chunk checks are server-side; archon doctor shows ✅."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config()
    state = IndexingState(collections={
        "reindex_col": CollectionProgress(
            status=IndexingStatus.DONE,
            indexed_chunk_size=512,
        )
    })
    response_data = _make_meta_response([_make_done_col("reindex_col")])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "chunk size mismatch" not in out
    assert "auto-reindex pending" not in out
    assert "✅" in out


def test_done_state_file_absent_no_checkmark(capsys: pytest.CaptureFixture) -> None:
    """State file doesn't exist (state=None) → cp is None → no ✅ line."""
    cfg = _make_rag_config(chunk_size=512)
    response_data = _make_meta_response([_make_done_col("my_col")])
    with _mock_state_store(None, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "✅" not in out


def test_done_warning_resets_between_collections(capsys: pytest.CaptureFixture) -> None:
    """First collection has a warning, second is healthy → second gets ✅."""
    from archon_search.progress import CollectionProgress, IndexingState, IndexingStatus

    cfg = _make_rag_config(embedding_model="BAAI/bge-small-en-v1.5", chunk_size=512)
    state = IndexingState(collections={
        "stale_col": CollectionProgress(
            status=IndexingStatus.DONE,
            indexed_chunk_size=512,
        ),
        "healthy_col": CollectionProgress(
            status=IndexingStatus.DONE,
            indexed_chunk_size=512,
        ),
    })
    response_data = _make_meta_response([
        _make_done_col("stale_col", days_old=10),
        _make_done_col("healthy_col", days_old=1),
    ])
    with _mock_state_store(state, response_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "last indexed" in out
    assert "✅" in out
    assert "healthy_col" in out


# ──────────────────────────────────────────────────────────────────
# Task 7.5 — SearchClient HTTP integration in doctor.py
# ──────────────────────────────────────────────────────────────────


def test_doctor_search_running_calls_health(capsys: pytest.CaptureFixture) -> None:
    """SearchClient.health() is called; when it returns a dict, search shows healthy."""
    cfg = _make_rag_config(enabled=True)
    with patch("archon.cli.doctor.SearchClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.health = AsyncMock(return_value={"status": "ok"})
        mock_client.indexing_state = AsyncMock(return_value={"collections": {}})
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client
        _run(doctor_mod._check_search_health(cfg))
    mock_client.health.assert_awaited_once()


def test_doctor_search_not_running_shows_not_running(capsys: pytest.CaptureFixture) -> None:
    """health() returning None → 'not running' printed; no crash, no further HTTP calls."""
    cfg = _make_rag_config(enabled=True)
    with patch("archon.cli.doctor.SearchClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.health = AsyncMock(return_value=None)
        mock_client.indexing_state = AsyncMock(return_value=None)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "not running" in out
    mock_client.indexing_state.assert_not_awaited()


def test_doctor_search_disabled_shows_disabled(capsys: pytest.CaptureFixture) -> None:
    """search.enabled=False → 'Search: disabled' printed; no HTTP call made."""
    cfg = _make_rag_config(enabled=False)
    with patch("archon.cli.doctor.SearchClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "disabled" in out
    mock_client.health.assert_not_awaited()


def test_doctor_in_progress_shows_partial(capsys: pytest.CaptureFixture) -> None:
    """in_progress status with files > 0 → '⏳ partial (N/M files)' output."""
    cfg = _make_rag_config(enabled=True)
    state_data = {
        "collections": {
            "docs": {
                "status": "in_progress",
                "processed_files": 50,
                "total_files": 100,
                "error": None,
            }
        }
    }
    meta_response = _make_meta_response([_make_healthy_col("docs")])
    with _mock_http(meta_response, state_data=state_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "⏳" in out
    assert "partial" in out
    assert "50" in out
    assert "100" in out


def test_doctor_failed_shows_error(capsys: pytest.CaptureFixture) -> None:
    """failed status → '❌' printed with collection name."""
    cfg = _make_rag_config(enabled=True)
    state_data = {
        "collections": {
            "docs": {
                "status": "failed",
                "processed_files": 0,
                "total_files": 0,
                "error": "Embedding timeout",
            }
        }
    }
    meta_response = _make_meta_response([_make_healthy_col("docs")])
    with _mock_http(meta_response, state_data=state_data):
        _run(doctor_mod._check_search_health(cfg))
    out = capsys.readouterr().out
    assert "❌" in out
    assert "docs" in out


# ──────────────────────────────────────────────────────────────────
# Warning icon rendering
# ──────────────────────────────────────────────────────────────────


def test_doctor_output_shows_warning_icon(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    warn_result = CheckResult("context windows", True, "mismatch: ...", warn=True)
    ok = CheckResult("x", True, "OK")
    for name in _ALL_CHECKS:
        monkeypatch.setattr(doctor_mod, name, lambda _ok=ok: _ok)
    monkeypatch.setattr(doctor_mod, "_check_search_server", lambda cfg: ok)
    monkeypatch.setattr(doctor_mod, "_check_context_windows", lambda: warn_result)
    result = doctor_mod.run_doctor()
    out = capsys.readouterr().out
    assert "⚠" in out
    assert result == 0


# ──────────────────────────────────────────────────────────────────
# Task 5.2 — Remove JSON-RPC, use SearchClient.list_collections() + collection_info()
# ──────────────────────────────────────────────────────────────────


def test_no_jsonrpc_payload() -> None:
    """_SEARCH_JSONRPC_PAYLOAD constant must not exist; no raw httpx in _check_search_health."""
    import archon.cli.doctor as mod
    assert not hasattr(mod, "_SEARCH_JSONRPC_PAYLOAD"), (
        "_SEARCH_JSONRPC_PAYLOAD constant must be removed from doctor.py"
    )
    import inspect
    src = inspect.getsource(mod._check_search_health)
    assert "httpx.AsyncClient" not in src, (
        "raw httpx.AsyncClient construction must be removed from _check_search_health"
    )


def test_staleness_check_uses_collection_info(capsys: pytest.CaptureFixture) -> None:
    """list_collections() + collection_info() with old last_indexed triggers staleness warning."""
    cfg = _make_rag_config()
    old_date = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()

    with patch("archon.cli.doctor.SearchClient") as mock_cls:
        sc_mock = AsyncMock()
        sc_mock.health = AsyncMock(return_value={"status": "ok"})
        sc_mock.indexing_state = AsyncMock(return_value={"collections": {}})
        sc_mock.list_collections = AsyncMock(return_value=[{"name": "my_col"}])
        sc_mock.collection_info = AsyncMock(return_value={
            "last_indexed": old_date,
            "doc_count": 5,
            "centroid_present": True,
        })
        sc_mock.__aenter__ = AsyncMock(return_value=sc_mock)
        sc_mock.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = sc_mock
        _run(doctor_mod._check_search_health(cfg))

    out = capsys.readouterr().out
    assert "last indexed 10 days ago" in out


def test_centroid_check_uses_centroid_present(capsys: pytest.CaptureFixture) -> None:
    """collection_info() with centroid_present=False triggers centroid warning."""
    cfg = _make_rag_config()
    recent_date = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    with patch("archon.cli.doctor.SearchClient") as mock_cls:
        sc_mock = AsyncMock()
        sc_mock.health = AsyncMock(return_value={"status": "ok"})
        sc_mock.indexing_state = AsyncMock(return_value={"collections": {}})
        sc_mock.list_collections = AsyncMock(return_value=[{"name": "no_centroid_col"}])
        sc_mock.collection_info = AsyncMock(return_value={
            "last_indexed": recent_date,
            "doc_count": 5,
            "centroid_present": False,
        })
        sc_mock.__aenter__ = AsyncMock(return_value=sc_mock)
        sc_mock.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = sc_mock
        _run(doctor_mod._check_search_health(cfg))

    out = capsys.readouterr().out
    assert "no centroid" in out
    assert "no_centroid_col" in out


def test_doc_count_zero(capsys: pytest.CaptureFixture) -> None:
    """collection_info() with doc_count=0 triggers empty collection warning."""
    cfg = _make_rag_config()
    recent_date = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    with patch("archon.cli.doctor.SearchClient") as mock_cls:
        sc_mock = AsyncMock()
        sc_mock.health = AsyncMock(return_value={"status": "ok"})
        sc_mock.indexing_state = AsyncMock(return_value={"collections": {}})
        sc_mock.list_collections = AsyncMock(return_value=[{"name": "empty_col"}])
        sc_mock.collection_info = AsyncMock(return_value={
            "last_indexed": recent_date,
            "doc_count": 0,
            "centroid_present": True,
        })
        sc_mock.__aenter__ = AsyncMock(return_value=sc_mock)
        sc_mock.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = sc_mock
        _run(doctor_mod._check_search_health(cfg))

    out = capsys.readouterr().out
    assert "is empty" in out
    assert "empty_col" in out


def test_collection_info_none_skipped(capsys: pytest.CaptureFixture) -> None:
    """collection_info() returning None → no exception, collection skipped silently."""
    cfg = _make_rag_config()

    with patch("archon.cli.doctor.SearchClient") as mock_cls:
        sc_mock = AsyncMock()
        sc_mock.health = AsyncMock(return_value={"status": "ok"})
        sc_mock.indexing_state = AsyncMock(return_value={"collections": {}})
        sc_mock.list_collections = AsyncMock(return_value=[{"name": "ghost_col"}])
        sc_mock.collection_info = AsyncMock(return_value=None)
        sc_mock.__aenter__ = AsyncMock(return_value=sc_mock)
        sc_mock.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = sc_mock
        # Must not raise
        _run(doctor_mod._check_search_health(cfg))

    out = capsys.readouterr().out
    # No crash; collection not mentioned (skipped)
    assert "ghost_col" not in out


def test_doctor_collection_checks_parallel(capsys: pytest.CaptureFixture) -> None:
    """list_collections() returns 3 collections; all 3 collection_info() calls complete."""
    import time
    cfg = _make_rag_config()
    recent_date = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    collection_info_calls: list[str] = []

    async def fake_collection_info(name: str) -> dict:
        collection_info_calls.append(name)
        await asyncio.sleep(0.01)
        return {
            "last_indexed": recent_date,
            "doc_count": 5,
            "centroid_present": True,
        }

    with patch("archon.cli.doctor.SearchClient") as mock_cls:
        sc_mock = AsyncMock()
        sc_mock.health = AsyncMock(return_value={"status": "ok"})
        sc_mock.indexing_state = AsyncMock(return_value={"collections": {}})
        sc_mock.list_collections = AsyncMock(return_value=[
            {"name": "col_a"},
            {"name": "col_b"},
            {"name": "col_c"},
        ])
        sc_mock.collection_info = fake_collection_info
        sc_mock.__aenter__ = AsyncMock(return_value=sc_mock)
        sc_mock.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = sc_mock

        start = time.monotonic()
        _run(doctor_mod._check_search_health(cfg))
        elapsed = time.monotonic() - start

    assert set(collection_info_calls) == {"col_a", "col_b", "col_c"}, (
        "All 3 collection_info() calls must be made"
    )
    # Parallel: all 3 with 0.01s delay each should complete in ~0.01s, not 0.03s
    assert elapsed < 0.09, f"Parallel gather too slow: {elapsed:.3f}s (expected < 0.09s)"


def test_centroid_check_no_warning_when_present(capsys: pytest.CaptureFixture) -> None:
    """collection_info() with centroid_present=True → no centroid warning."""
    cfg = _make_rag_config()
    recent_date = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    with patch("archon.cli.doctor.SearchClient") as mock_cls:
        sc_mock = AsyncMock()
        sc_mock.health = AsyncMock(return_value={"status": "ok"})
        sc_mock.indexing_state = AsyncMock(return_value={"collections": {}})
        sc_mock.list_collections = AsyncMock(return_value=[{"name": "ok_col"}])
        sc_mock.collection_info = AsyncMock(return_value={
            "last_indexed": recent_date,
            "doc_count": 10,
            "centroid_present": True,
        })
        sc_mock.__aenter__ = AsyncMock(return_value=sc_mock)
        sc_mock.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = sc_mock
        _run(doctor_mod._check_search_health(cfg))

    out = capsys.readouterr().out
    assert "centroid" not in out.lower()


# ──────────────────────────────────────────────────────────────────
# _check_search_key_file
# ──────────────────────────────────────────────────────────────────

import os
import sys
import stat


def test_check_search_key_file_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No key file and no env var → failed CheckResult with 'not found' in message."""
    monkeypatch.delenv("ARCHON_SEARCH_API_KEY", raising=False)
    with patch("archon.diagnostics.Path.home", return_value=tmp_path):
        with patch("archon.diagnostics.load_config") as mock_cfg:
            mock_cfg.return_value.search.enabled = True
            result = diagnostics_mod._check_search_key_file()
    assert result.ok is False
    assert "not found" in result.detail.lower()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
def test_check_search_key_file_wrong_perms(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Key file exists but mode is 0o644 → failed CheckResult mentioning 'permissions'."""
    monkeypatch.delenv("ARCHON_SEARCH_API_KEY", raising=False)
    archon_dir = tmp_path / ".archon"
    archon_dir.mkdir()
    key_file = archon_dir / ".search.env"
    key_file.write_text("ARCHON_SEARCH_API_KEY=abc\n")
    key_file.chmod(0o644)
    with patch("archon.diagnostics.Path.home", return_value=tmp_path):
        with patch("archon.diagnostics.load_config") as mock_cfg:
            mock_cfg.return_value.search.enabled = True
            result = diagnostics_mod._check_search_key_file()
    assert result.ok is False
    assert "permissions" in result.detail.lower()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
def test_check_search_key_file_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Key file exists with mode 0o600 → passed CheckResult with 'ok'."""
    monkeypatch.delenv("ARCHON_SEARCH_API_KEY", raising=False)
    archon_dir = tmp_path / ".archon"
    archon_dir.mkdir()
    key_file = archon_dir / ".search.env"
    key_file.write_text("ARCHON_SEARCH_API_KEY=abc\n")
    key_file.chmod(0o600)
    with patch("archon.diagnostics.Path.home", return_value=tmp_path):
        with patch("archon.diagnostics.load_config") as mock_cfg:
            mock_cfg.return_value.search.enabled = True
            result = diagnostics_mod._check_search_key_file()
    assert result.ok is True
    assert result.detail == "ok"


def test_check_search_key_file_windows_skip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """On Windows, permissions check is skipped → passed CheckResult mentioning 'Windows'."""
    monkeypatch.delenv("ARCHON_SEARCH_API_KEY", raising=False)
    archon_dir = tmp_path / ".archon"
    archon_dir.mkdir()
    key_file = archon_dir / ".search.env"
    key_file.write_text("ARCHON_SEARCH_API_KEY=abc\n")
    with patch("archon.diagnostics.Path.home", return_value=tmp_path):
        with patch("archon.diagnostics._platform_detect", return_value="win32"):
            with patch("archon.diagnostics.load_config") as mock_cfg:
                mock_cfg.return_value.search.enabled = True
                result = diagnostics_mod._check_search_key_file()
    assert result.ok is True
    assert "windows" in result.detail.lower()


def test_check_search_key_file_env_var_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valid 64-char hex in ARCHON_SEARCH_API_KEY env var → passed, even if file absent."""
    valid_key = "a" * 64
    monkeypatch.setenv("ARCHON_SEARCH_API_KEY", valid_key)
    with patch("archon.diagnostics.load_config") as mock_cfg:
        mock_cfg.return_value.search.enabled = True
        result = diagnostics_mod._check_search_key_file()
    assert result.ok is True
    assert "env var" in result.detail.lower()


def test_check_search_key_file_env_var_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid value in ARCHON_SEARCH_API_KEY → failed CheckResult mentioning invalid value."""
    monkeypatch.setenv("ARCHON_SEARCH_API_KEY", "not-a-valid-hex!")
    with patch("archon.diagnostics.load_config") as mock_cfg:
        mock_cfg.return_value.search.enabled = True
        result = diagnostics_mod._check_search_key_file()
    assert result.ok is False
    assert "invalid" in result.detail.lower()


def test_check_search_key_file_search_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """search.enabled=False → passed CheckResult with 'search disabled', no file access."""
    monkeypatch.delenv("ARCHON_SEARCH_API_KEY", raising=False)
    with patch("archon.diagnostics.load_config") as mock_cfg:
        mock_cfg.return_value.search.enabled = False
        with patch("archon.diagnostics.Path.exists") as mock_exists:
            result = diagnostics_mod._check_search_key_file()
    assert result.ok is True
    assert "search disabled" in result.detail.lower()
    mock_exists.assert_not_called()
