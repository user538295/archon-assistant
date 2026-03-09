"""Tests for ArchonOrchestratorMCPServer — Step 2 orchestration redesign."""
import pytest
from pathlib import Path
from unittest.mock import patch
from aiohttp.test_utils import TestClient, TestServer

from archon.ai.archon_orch_mcp_server import ArchonOrchestratorMCPServer


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _rpc(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    req: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        req["params"] = params
    return req


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def orch_server_client():
    """Provide (server, TestClient) — server exposes the token property."""
    server = ArchonOrchestratorMCPServer()
    client = TestClient(TestServer(server._app))
    await client.start_server()
    yield server, client
    await client.close()


@pytest.fixture
async def orch_client(orch_server_client):
    """Provide a thin wrapper that auto-injects the correct Bearer token."""
    server, raw_client = orch_server_client

    class _AuthClient:
        """Auto-injects Authorization header on POST /mcp; delegates GET to raw client."""

        def __init__(self, inner: TestClient, tok: str) -> None:
            self._inner = inner
            self.token = tok

        async def post_mcp(self, body: dict, *, token: str | None = None) -> dict:
            tok = token if token is not None else self.token
            resp = await self._inner.post(
                "/mcp",
                json=body,
                headers={"Authorization": f"Bearer {tok}"},
            )
            return await resp.json()

        async def post_mcp_no_auth(self, body: dict):
            return await self._inner.post("/mcp", json=body)

        async def get(self, path: str):
            return await self._inner.get(path)

    yield _AuthClient(raw_client, server.token)


# ──────────────────────────────────────────────────────────────────
# Bearer-token authentication
# ──────────────────────────────────────────────────────────────────


class TestAuth:
    async def test_token_is_64_hex_chars(self, orch_server_client) -> None:
        """ArchonOrchestratorMCPServer generates a 64-char hex token at construction."""
        server, _ = orch_server_client
        assert len(server.token) == 64
        assert all(c in "0123456789abcdef" for c in server.token)

    async def test_missing_auth_header_returns_401(self, orch_client) -> None:
        """POST /mcp without Authorization header -> 401 Unauthorized."""
        raw_resp = await orch_client.post_mcp_no_auth(_rpc("initialize"))
        assert raw_resp.status == 401

    async def test_wrong_token_returns_401(self, orch_client) -> None:
        """POST /mcp with an incorrect Bearer token -> 401 Unauthorized."""
        raw_resp = await orch_client._inner.post(
            "/mcp",
            json=_rpc("initialize"),
            headers={"Authorization": "Bearer wrong_token_value"},
        )
        assert raw_resp.status == 401

    async def test_malformed_auth_scheme_returns_401(self, orch_client) -> None:
        """Authorization header that is not 'Bearer ...' -> 401."""
        raw_resp = await orch_client._inner.post(
            "/mcp",
            json=_rpc("initialize"),
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert raw_resp.status == 401

    async def test_correct_token_returns_200(self, orch_client) -> None:
        """POST /mcp with the correct Bearer token -> 200 and valid JSON-RPC response."""
        resp = await orch_client.post_mcp(_rpc("initialize"))
        assert resp["jsonrpc"] == "2.0"
        assert "result" in resp

    async def test_each_server_instance_has_unique_token(self) -> None:
        """Two independently constructed servers must have different tokens."""
        s1 = ArchonOrchestratorMCPServer()
        s2 = ArchonOrchestratorMCPServer()
        assert s1.token != s2.token

    async def test_health_endpoint_requires_no_auth(self, orch_client) -> None:
        """GET /health must return 200 without any Authorization header."""
        raw_resp = await orch_client._inner.get("/health")
        assert raw_resp.status == 200


# ──────────────────────────────────────────────────────────────────
# initialize
# ──────────────────────────────────────────────────────────────────


class TestInitialize:
    async def test_initialize_returns_capabilities(self, orch_client) -> None:
        resp = await orch_client.post_mcp(_rpc("initialize"))
        assert resp["jsonrpc"] == "2.0"
        result = resp["result"]
        assert "serverInfo" in result
        assert "capabilities" in result
        assert "tools" in result["capabilities"]


# ──────────────────────────────────────────────────────────────────
# tools/list
# ──────────────────────────────────────────────────────────────────


class TestToolsList:
    async def test_tools_list_returns_exactly_two_tools(self, orch_client) -> None:
        resp = await orch_client.post_mcp(_rpc("tools/list"))
        tools = resp["result"]["tools"]
        assert len(tools) == 2
        names = {t["name"] for t in tools}
        assert names == {"history_read", "history_grep"}


# ──────────────────────────────────────────────────────────────────
# history_read
# ──────────────────────────────────────────────────────────────────


class TestHistoryRead:
    async def test_history_read_valid_path(self, orch_client) -> None:
        """A valid path under ~/.archon/history/ returns file content."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "2026-01-01.md")
        file_content = "# Session log\nsome content here"

        with patch("archon.ai.archon_orch_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.is_file", return_value=True), \
             patch("pathlib.Path.read_text", return_value=file_content):
            resp = await orch_client.post_mcp(
                _rpc("tools/call", {"name": "history_read", "arguments": {"path": valid_path}}),
            )

        assert resp["result"]["isError"] is False
        assert file_content in resp["result"]["content"][0]["text"]

    async def test_history_read_path_outside_history_is_denied(self, orch_client) -> None:
        """A path outside ~/.archon/history/ is denied with isError=true."""
        resp = await orch_client.post_mcp(
            _rpc("tools/call", {"name": "history_read", "arguments": {"path": "/etc/passwd"}}),
        )
        assert resp["result"]["isError"] is True
        assert "Access denied" in resp["result"]["content"][0]["text"]

    async def test_history_read_path_traversal_denied(self, orch_client) -> None:
        """Path traversal attempt is blocked after resolve()."""
        traversal = "~/.archon/history/../../../etc/passwd"
        resp = await orch_client.post_mcp(
            _rpc("tools/call", {"name": "history_read", "arguments": {"path": traversal}}),
        )
        assert resp["result"]["isError"] is True
        assert "Access denied" in resp["result"]["content"][0]["text"]

    async def test_history_read_file_not_found(self, orch_client) -> None:
        """Valid path but missing file returns isError=true."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "nonexistent-9999-99-99.md")

        with patch("archon.ai.archon_orch_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=False):
            resp = await orch_client.post_mcp(
                _rpc("tools/call", {"name": "history_read", "arguments": {"path": valid_path}}),
            )

        assert resp["result"]["isError"] is True
        assert "not found" in resp["result"]["content"][0]["text"].lower()

    async def test_history_read_truncates_large_file(self, orch_client) -> None:
        """Files exceeding 50,000 chars are truncated with a notice appended."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "2026-01-01.md")
        large_content = "x" * 60_000

        with patch("archon.ai.archon_orch_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.is_file", return_value=True), \
             patch("pathlib.Path.read_text", return_value=large_content):
            resp = await orch_client.post_mcp(
                _rpc("tools/call", {"name": "history_read", "arguments": {"path": valid_path}}),
            )

        assert resp["result"]["isError"] is False
        text = resp["result"]["content"][0]["text"]
        assert len(text) < 60_000
        assert "[Truncated:" in text
        assert "history_grep" in text

    async def test_history_read_does_not_truncate_small_file(self, orch_client) -> None:
        """Files within the 50,000-char limit are returned in full without a truncation notice."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "2026-01-01.md")
        small_content = "# Session log\n" + "y" * 1_000

        with patch("archon.ai.archon_orch_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.is_file", return_value=True), \
             patch("pathlib.Path.read_text", return_value=small_content):
            resp = await orch_client.post_mcp(
                _rpc("tools/call", {"name": "history_read", "arguments": {"path": valid_path}}),
            )

        assert resp["result"]["isError"] is False
        text = resp["result"]["content"][0]["text"]
        assert text == small_content
        assert "[Truncated:" not in text


# ──────────────────────────────────────────────────────────────────
# history_grep
# ──────────────────────────────────────────────────────────────────


class TestHistoryGrep:
    async def test_history_grep_valid_path_returns_matches(self, orch_client) -> None:
        """Pattern matching returns only matching lines."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "2026-01-01.md")
        file_content = "line one\nerror occurred here\nanother line\nerror again"

        with patch("archon.ai.archon_orch_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=file_content):
            resp = await orch_client.post_mcp(
                _rpc("tools/call", {
                    "name": "history_grep",
                    "arguments": {"pattern": "error", "path": valid_path},
                }),
            )

        assert resp["result"]["isError"] is False
        text = resp["result"]["content"][0]["text"]
        assert "error occurred here" in text
        assert "error again" in text
        assert "line one" not in text

    async def test_history_grep_no_matches_returns_placeholder(self, orch_client) -> None:
        """When pattern matches nothing, return '(no matches)'."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "2026-01-01.md")
        file_content = "line one\nline two\nline three"

        with patch("archon.ai.archon_orch_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=file_content):
            resp = await orch_client.post_mcp(
                _rpc("tools/call", {
                    "name": "history_grep",
                    "arguments": {"pattern": "zzz_nomatch", "path": valid_path},
                }),
            )

        assert resp["result"]["isError"] is False
        assert resp["result"]["content"][0]["text"] == "(no matches)"

    async def test_history_grep_path_outside_history_is_denied(self, orch_client) -> None:
        """history_grep also enforces path restriction."""
        resp = await orch_client.post_mcp(
            _rpc("tools/call", {
                "name": "history_grep",
                "arguments": {"pattern": "root", "path": "/etc/passwd"},
            }),
        )
        assert resp["result"]["isError"] is True
        assert "Access denied" in resp["result"]["content"][0]["text"]

    async def test_history_grep_invalid_regex_returns_tool_error(self, orch_client) -> None:
        """An invalid regex pattern returns isError=true with a descriptive message."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "2026-01-01.md")
        file_content = "line one\nline two"

        with patch("archon.ai.archon_orch_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=file_content):
            resp = await orch_client.post_mcp(
                _rpc("tools/call", {
                    "name": "history_grep",
                    "arguments": {"pattern": "[unclosed", "path": valid_path},
                }),
            )

        assert resp["result"]["isError"] is True
        assert "Invalid regex pattern" in resp["result"]["content"][0]["text"]

    async def test_history_grep_line_size_limit(self, orch_client) -> None:
        """Lines exceeding 10KB are skipped during grep to bound execution time."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "2026-01-01.md")
        # One huge line (>10KB) containing "needle", one normal line with "needle"
        huge_line = "a" * 10001 + "needle"
        normal_line = "small line with needle"
        file_content = f"{huge_line}\n{normal_line}"

        with patch("archon.ai.archon_orch_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=file_content):
            resp = await orch_client.post_mcp(
                _rpc("tools/call", {
                    "name": "history_grep",
                    "arguments": {"pattern": "needle", "path": valid_path},
                }),
            )

        assert resp["result"]["isError"] is False
        text = resp["result"]["content"][0]["text"]
        # The huge line must be skipped; the normal line must match
        assert normal_line in text
        assert huge_line not in text


# ──────────────────────────────────────────────────────────────────
# Fix 1: timing-safe token comparison
# ──────────────────────────────────────────────────────────────────


class TestTimingSafeTokenComparison:
    async def test_hmac_compare_digest_is_used(self, orch_client) -> None:
        """Auth check must delegate to hmac.compare_digest (timing-safe comparison)."""
        import hmac as hmac_mod

        call_args: list = []

        original = hmac_mod.compare_digest

        def capturing_compare_digest(a: str, b: str) -> bool:
            call_args.append((a, b))
            return original(a, b)

        with patch("archon.ai.archon_orch_mcp_server.hmac.compare_digest", capturing_compare_digest):
            resp = await orch_client.post_mcp(_rpc("initialize"))

        assert resp["jsonrpc"] == "2.0"
        assert len(call_args) == 1, "hmac.compare_digest must be called exactly once per request"

    async def test_wrong_token_still_returns_401_with_timing_safe_check(self, orch_client) -> None:
        """Wrong token returns 401 even when hmac.compare_digest is in use."""
        raw_resp = await orch_client._inner.post(
            "/mcp",
            json=_rpc("initialize"),
            headers={"Authorization": "Bearer wrong_token_value"},
        )
        assert raw_resp.status == 401


# ──────────────────────────────────────────────────────────────────
# Fix 2: history_grep output size cap + large-file guard
# ──────────────────────────────────────────────────────────────────


class TestHistoryGrepOutputLimits:
    async def test_history_grep_output_truncated_when_too_many_matches(self, orch_client) -> None:
        """300 matching lines → only _MAX_GREP_MATCHES lines returned + truncation notice."""
        from archon.ai.archon_orch_mcp_server import _MAX_GREP_MATCHES

        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "2026-01-01.md")
        file_content = "\n".join(f"match line {i}" for i in range(300))

        with patch("archon.ai.archon_orch_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=file_content):
            resp = await orch_client.post_mcp(
                _rpc("tools/call", {
                    "name": "history_grep",
                    "arguments": {"pattern": "match", "path": valid_path},
                }),
            )

        assert resp["result"]["isError"] is False
        text = resp["result"]["content"][0]["text"]
        lines = text.splitlines()
        # Last line is the truncation notice; remaining lines are the capped matches
        match_lines = [l for l in lines if not l.startswith("[Truncated:")]
        assert len(match_lines) == _MAX_GREP_MATCHES
        assert any("[Truncated:" in l for l in lines)
        assert "more matches omitted" in text

    async def test_history_grep_large_file_rejected(self, orch_client) -> None:
        """A file exceeding _MAX_FILE_CHARS bytes is rejected with isError=true."""
        from archon.ai.archon_orch_mcp_server import _MAX_FILE_CHARS

        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "2026-01-01.md")
        oversized_content = "x" * (_MAX_FILE_CHARS + 1)

        with patch("archon.ai.archon_orch_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=oversized_content):
            resp = await orch_client.post_mcp(
                _rpc("tools/call", {
                    "name": "history_grep",
                    "arguments": {"pattern": "x", "path": valid_path},
                }),
            )

        assert resp["result"]["isError"] is True


# ──────────────────────────────────────────────────────────────────
# Fix 3: non-UTF-8 files handled gracefully (errors="replace")
# ──────────────────────────────────────────────────────────────────


class TestNonUtf8Files:
    async def test_history_read_non_utf8_returns_content_with_replacement_chars(
        self, orch_client
    ) -> None:
        """read_text with errors='replace' substitutes bad bytes instead of raising."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "corrupt.md")
        # Simulate what read_text(encoding="utf-8", errors="replace") returns on bad bytes
        replaced_content = "good line\n\ufffdcorrupt byte here\ngood line again"

        with patch("archon.ai.archon_orch_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.is_file", return_value=True), \
             patch("pathlib.Path.read_text", return_value=replaced_content):
            resp = await orch_client.post_mcp(
                _rpc("tools/call", {"name": "history_read", "arguments": {"path": valid_path}}),
            )

        assert resp["result"]["isError"] is False
        assert "\ufffd" in resp["result"]["content"][0]["text"]

    async def test_history_grep_non_utf8_returns_matches_with_replacement_chars(
        self, orch_client
    ) -> None:
        """history_grep with errors='replace' substitutes bad bytes instead of raising."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "corrupt.md")
        replaced_content = "good line with needle\n\ufffdcorrupt\nneedle again"

        with patch("archon.ai.archon_orch_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=replaced_content):
            resp = await orch_client.post_mcp(
                _rpc("tools/call", {
                    "name": "history_grep",
                    "arguments": {"pattern": "needle", "path": valid_path},
                }),
            )

        assert resp["result"]["isError"] is False
        assert "needle" in resp["result"]["content"][0]["text"]

    async def test_history_read_actual_non_utf8_bytes(self, tmp_path) -> None:
        """read_text with errors='replace' does not raise on files with invalid UTF-8 bytes."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        # Write a real file with invalid UTF-8 bytes
        corrupt_file = tmp_path / "corrupt.md"
        corrupt_file.write_bytes(b"valid start\xff\xfe invalid bytes\nvalid end")

        with patch("archon.ai.archon_orch_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=True):
            # Patch read_text to use the real file but via the actual implementation
            server = ArchonOrchestratorMCPServer()
            # Call the method directly with a path pointing to the tmp file
            with patch("archon.ai.archon_orch_mcp_server._is_allowed_path", return_value=True):
                # Temporarily redirect the path check
                result = server._tool_history_read({"path": str(corrupt_file)})

        # Must not raise; must return content with replacement characters
        assert result["isError"] is False
        assert "\ufffd" in result["content"][0]["text"]


# ──────────────────────────────────────────────────────────────────
# Unknown tool
# ──────────────────────────────────────────────────────────────────


class TestUnknownTool:
    async def test_unknown_tool_returns_error(self, orch_client) -> None:
        """tools/call with an unknown tool name returns a JSON-RPC error."""
        resp = await orch_client.post_mcp(
            _rpc("tools/call", {"name": "rm_rf", "arguments": {}}),
        )
        assert "error" in resp
        assert resp["error"]["code"] == -32602


# ──────────────────────────────────────────────────────────────────
# Fix 4: history_read on a directory returns a clean tool error
# ──────────────────────────────────────────────────────────────────


class TestHistoryReadDirectory:
    async def test_history_read_directory_path_returns_tool_error(self, tmp_path) -> None:
        """history_read on a directory path returns isError=true with 'directory' in message."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        server = ArchonOrchestratorMCPServer()
        with patch("archon.ai.archon_orch_mcp_server._is_allowed_path", return_value=True):
            result = server._tool_history_read({"path": str(subdir)})

        assert result["isError"] is True
        assert "directory" in result["content"][0]["text"].lower()


# ──────────────────────────────────────────────────────────────────
# Fix 5: history_grep with empty/whitespace pattern returns tool error
# ──────────────────────────────────────────────────────────────────


class TestHistoryGrepEmptyPattern:
    async def test_history_grep_empty_pattern_returns_tool_error(self, orch_client) -> None:
        """history_grep with pattern='' returns isError=true mentioning empty."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "2026-01-01.md")

        with patch("archon.ai.archon_orch_mcp_server._is_allowed_path", return_value=True):
            resp = await orch_client.post_mcp(
                _rpc("tools/call", {
                    "name": "history_grep",
                    "arguments": {"pattern": "", "path": valid_path},
                }),
            )

        assert resp["result"]["isError"] is True
        assert "empty" in resp["result"]["content"][0]["text"].lower()

    async def test_history_grep_whitespace_pattern_returns_tool_error(self, orch_client) -> None:
        """history_grep with pattern='   ' (whitespace only) returns isError=true."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "2026-01-01.md")

        with patch("archon.ai.archon_orch_mcp_server._is_allowed_path", return_value=True):
            resp = await orch_client.post_mcp(
                _rpc("tools/call", {
                    "name": "history_grep",
                    "arguments": {"pattern": "   ", "path": valid_path},
                }),
            )

        assert resp["result"]["isError"] is True


# ──────────────────────────────────────────────────────────────────
# GET /health
# ──────────────────────────────────────────────────────────────────


class TestHealth:
    async def test_health_endpoint(self, orch_client) -> None:
        resp = await orch_client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert data == {"status": "ok"}
