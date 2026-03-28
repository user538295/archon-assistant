from __future__ import annotations
import json
import pytest
import urllib.error
from unittest.mock import patch, MagicMock
from pathlib import Path
import archon.cli.doctor as doctor_mod
from archon.cli.doctor import CheckResult


_ALL_CHECKS = [
    "_check_git", "_check_uv", "_check_python", "_check_claude",
    "_check_env_file", "_check_config_file", "_check_logs_dir",
    "_check_health", "_check_app_dir", "_check_bot_token",
]


def _all_ok() -> list[CheckResult]:
    return [CheckResult(f"check{i}", True, "OK") for i in range(len(_ALL_CHECKS))]


def test_all_pass_returns_0(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    ok = CheckResult("x", True, "OK")
    for name in _ALL_CHECKS:
        monkeypatch.setattr(doctor_mod, name, lambda _ok=ok: _ok)
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
    result = doctor_mod.run_doctor()
    assert result == 1
    assert "issue" in capsys.readouterr().out


def test_check_git_found() -> None:
    with patch("archon.cli.doctor.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="git version 2.39")):
        result = doctor_mod._check_git()
    assert result.ok is True


def test_check_git_not_found() -> None:
    with patch("archon.cli.doctor.subprocess.run", side_effect=FileNotFoundError):
        result = doctor_mod._check_git()
    assert result.ok is False


def test_check_uv_found() -> None:
    with patch("archon.cli.doctor.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="uv 0.4.10")):
        result = doctor_mod._check_uv()
    assert result.ok is True


def test_check_python_312() -> None:
    with patch("archon.cli.doctor.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="Python 3.12.3", stderr="")):
        result = doctor_mod._check_python()
    assert result.ok is True


def test_check_python_below_312() -> None:
    with patch("archon.cli.doctor.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="Python 3.11.0", stderr="")):
        result = doctor_mod._check_python()
    assert result.ok is False


def test_check_env_file_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=abc123\n")
    result = doctor_mod._check_env_file()
    assert result.ok is True


def test_check_env_file_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    result = doctor_mod._check_env_file()
    assert result.ok is False


def test_check_config_valid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    (tmp_path / "config.toml").write_text('[access]\nallowed_user_ids = [1]\n')
    result = doctor_mod._check_config_file()
    assert result.ok is True


def test_check_config_invalid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    (tmp_path / "config.toml").write_text("NOT VALID TOML @@@")
    result = doctor_mod._check_config_file()
    assert result.ok is False


def test_check_logs_dir_writable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    result = doctor_mod._check_logs_dir()
    assert result.ok is True


def test_check_logs_dir_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    result = doctor_mod._check_logs_dir()
    assert result.ok is False


def test_check_health_ok() -> None:
    with patch("archon.cli.doctor.urllib.request.urlopen", return_value=MagicMock()):
        result = doctor_mod._check_health()
    assert result.ok is True


def test_check_health_fail() -> None:
    with patch("archon.cli.doctor.urllib.request.urlopen", side_effect=Exception("refused")):
        result = doctor_mod._check_health()
    assert result.ok is False


def test_check_app_dir_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    (tmp_path / "app").mkdir()
    result = doctor_mod._check_app_dir()
    assert result.ok is True


def test_check_app_dir_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    result = doctor_mod._check_app_dir()
    assert result.ok is False


def test_check_env_file_commented_token_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A commented-out token line must not pass as healthy."""
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    env = tmp_path / ".env"
    env.write_text("# TELEGRAM_BOT_TOKEN=abc123\n")
    result = doctor_mod._check_env_file()
    assert result.ok is False


def test_check_env_file_empty_value_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty token value (TELEGRAM_BOT_TOKEN=) must not pass as healthy."""
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=\n")
    result = doctor_mod._check_env_file()
    assert result.ok is False


def test_check_env_file_token_keyword_only_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Presence of the keyword in an unrelated string must not pass."""
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    env = tmp_path / ".env"
    env.write_text("# export TELEGRAM_BOT_TOKEN\n")
    result = doctor_mod._check_env_file()
    assert result.ok is False


def test_check_health_reads_port_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_check_health() must use the port from config.toml when available."""
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    (tmp_path / "config.toml").write_text(
        "[background_agents]\nport = 19999\n"
    )
    captured_urls: list[str] = []

    def fake_urlopen(url: str, timeout: int) -> MagicMock:
        captured_urls.append(url)
        return MagicMock()

    with patch("archon.cli.doctor.urllib.request.urlopen", side_effect=fake_urlopen):
        doctor_mod._check_health()

    assert len(captured_urls) == 1
    assert ":19999/" in captured_urls[0]


def test_check_health_uses_default_port_when_config_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_check_health() must fall back to port 18182 when config is absent."""
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    # No config.toml in tmp_path
    captured_urls: list[str] = []

    def fake_urlopen(url: str, timeout: int) -> MagicMock:
        captured_urls.append(url)
        raise Exception("refused")

    with patch("archon.cli.doctor.urllib.request.urlopen", side_effect=fake_urlopen):
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
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=123:ABC\n")
    with patch("archon.cli.doctor.urllib.request.urlopen", _mock_urlopen_ok("archon_bot")):
        result = doctor_mod._check_bot_token()
    assert result.ok is True
    assert "archon_bot" in result.detail


def test_check_bot_token_invalid_401(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP 401 from Telegram means the token is wrong — return ok=False with clear message."""
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=bad:token\n")
    http_err = urllib.error.HTTPError(None, 401, "Unauthorized", {}, None)  # type: ignore[arg-type]
    with patch("archon.cli.doctor.urllib.request.urlopen", side_effect=http_err):
        result = doctor_mod._check_bot_token()
    assert result.ok is False
    assert "invalid" in result.detail.lower() or "unauthorized" in result.detail.lower()


def test_check_bot_token_network_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A network error returns ok=False with a Telegram-related message."""
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=123:ABC\n")
    with patch("archon.cli.doctor.urllib.request.urlopen", side_effect=Exception("timeout")):
        result = doctor_mod._check_bot_token()
    assert result.ok is False
    assert "telegram" in result.detail.lower()


def test_check_bot_token_no_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing .env file returns ok=False."""
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    result = doctor_mod._check_bot_token()
    assert result.ok is False


def test_check_bot_token_missing_in_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty token value in .env returns ok=False without making a network call."""
    monkeypatch.setattr(doctor_mod, "_ARCHON_HOME", tmp_path)
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=\n")
    with patch("archon.cli.doctor.urllib.request.urlopen") as mock_urlopen:
        result = doctor_mod._check_bot_token()
    assert result.ok is False
    mock_urlopen.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# RAG collection health checks
# ──────────────────────────────────────────────────────────────────

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock


def _make_rag_config(
    enabled: bool = True,
    host: str = "localhost",
    port: int = 8282,
    embedding_model: str = "BAAI/bge-small-en-v1.5",
    collections: list[str] | None = None,
    pinned_collections: list[str] | None = None,
) -> object:
    """Build a minimal fake config with rag section."""
    class FakeRag:
        pass

    class FakeCfg:
        pass

    rag = FakeRag()
    rag.enabled = enabled
    rag.host = host
    rag.port = port
    rag.embedding_model = embedding_model
    rag.collections = collections if collections is not None else []
    rag.pinned_collections = pinned_collections if pinned_collections is not None else []

    cfg = FakeCfg()
    cfg.rag = rag
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
    """_check_rag_health prints a warning for collections last indexed >7 days ago."""
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
    mock_response = _make_httpx_response(response_data)
    with patch("archon.cli.doctor.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client
        _run(doctor_mod._check_rag_health(cfg))
    out = capsys.readouterr().out
    assert "⚠ Collection 'my_collection' last indexed 10 days ago" in out


def test_doctor_warns_model_mismatch(capsys: pytest.CaptureFixture) -> None:
    """_check_rag_health prints a warning when embedding model differs from config."""
    cfg = _make_rag_config(embedding_model="BAAI/bge-small-en-v1.5")
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
    mock_response = _make_httpx_response(response_data)
    with patch("archon.cli.doctor.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client
        _run(doctor_mod._check_rag_health(cfg))
    out = capsys.readouterr().out
    assert "⚠ Collection 'my_collection' indexed with 'old-model/v1', current model is 'BAAI/bge-small-en-v1.5' — reindex required" in out


def test_doctor_warns_empty_collection(capsys: pytest.CaptureFixture) -> None:
    """_check_rag_health prints a warning for collections with doc_count == 0."""
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
    mock_response = _make_httpx_response(response_data)
    with patch("archon.cli.doctor.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client
        _run(doctor_mod._check_rag_health(cfg))
    out = capsys.readouterr().out
    assert "⚠ Collection 'empty_col' is empty" in out


def test_doctor_warns_missing_centroid(capsys: pytest.CaptureFixture) -> None:
    """_check_rag_health prints a warning when centroid is None."""
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
    mock_response = _make_httpx_response(response_data)
    with patch("archon.cli.doctor.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client
        _run(doctor_mod._check_rag_health(cfg))
    out = capsys.readouterr().out
    assert "⚠ Collection 'no_centroid_col' has no centroid — routing disabled for this collection" in out


def test_doctor_no_warnings_on_healthy_collections(capsys: pytest.CaptureFixture) -> None:
    """_check_rag_health prints no warnings for a healthy collection."""
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
    mock_response = _make_httpx_response(response_data)
    with patch("archon.cli.doctor.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client
        _run(doctor_mod._check_rag_health(cfg))
    out = capsys.readouterr().out
    assert "⚠" not in out


def test_doctor_skips_rag_checks_when_server_down(capsys: pytest.CaptureFixture) -> None:
    """_check_rag_health skips collection checks and prints a message when server is unreachable."""
    import httpx as httpx_mod
    cfg = _make_rag_config()
    with patch("archon.cli.doctor.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx_mod.ConnectError("refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client
        _run(doctor_mod._check_rag_health(cfg))
    out = capsys.readouterr().out
    assert "RAG server is not running — RAG health checks skipped" in out
    assert "⚠" not in out


def test_doctor_warns_pinned_not_in_collections(capsys: pytest.CaptureFixture) -> None:
    """_check_rag_health warns about pinned paths not present in rag.collections."""
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
    mock_response = _make_httpx_response(response_data)
    with patch("archon.cli.doctor.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client
        _run(doctor_mod._check_rag_health(cfg))
    out = capsys.readouterr().out
    assert "⚠ Pinned collection '~/.archon/workspace' is not declared in rag.collections — it will be skipped at runtime" in out


def test_doctor_pinned_check_runs_when_server_down(capsys: pytest.CaptureFixture) -> None:
    """Pinned-not-in-collections check runs even when the RAG server is unreachable."""
    import httpx as httpx_mod
    cfg = _make_rag_config(
        collections=["~/.archon/history/sessions"],
        pinned_collections=["~/.archon/history/sessions", "~/.archon/workspace"],
    )
    with patch("archon.cli.doctor.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx_mod.ConnectError("refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client
        _run(doctor_mod._check_rag_health(cfg))
    out = capsys.readouterr().out
    assert "RAG server is not running — RAG health checks skipped" in out
    assert "⚠ Pinned collection '~/.archon/workspace' is not declared in rag.collections — it will be skipped at runtime" in out


def test_doctor_does_not_warn_stale_at_boundary_7_days(capsys: pytest.CaptureFixture) -> None:
    """_check_rag_health does NOT warn for a collection last indexed exactly 7 days ago."""
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
    mock_response = _make_httpx_response(response_data)
    with patch("archon.cli.doctor.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client
        _run(doctor_mod._check_rag_health(cfg))
    out = capsys.readouterr().out
    assert "last indexed" not in out


def test_doctor_warns_stale_at_8_days(capsys: pytest.CaptureFixture) -> None:
    """_check_rag_health warns for a collection last indexed 8 days ago."""
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
    mock_response = _make_httpx_response(response_data)
    with patch("archon.cli.doctor.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client
        _run(doctor_mod._check_rag_health(cfg))
    out = capsys.readouterr().out
    assert "⚠ Collection 'stale_col' last indexed 8 days ago" in out


def test_doctor_no_staleness_warning_when_last_indexed_missing(capsys: pytest.CaptureFixture) -> None:
    """_check_rag_health does NOT warn about staleness when last_indexed is absent."""
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
    mock_response = _make_httpx_response(response_data)
    with patch("archon.cli.doctor.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client
        _run(doctor_mod._check_rag_health(cfg))
    out = capsys.readouterr().out
    assert "last indexed" not in out


def test_run_doctor_rag_health_called_when_rag_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """run_doctor() calls _check_rag_health when config.toml exists and RAG is enabled."""
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
        rag = FakeRag()

    mock_check = AsyncMock()

    with patch("archon.cli.doctor._check_rag_health", mock_check):
        with patch("archon.config.config", FakeCfg()):
            doctor_mod.run_doctor()

    mock_check.assert_called_once()
