"""Tests for ArchonRouterMCPServer — Step 2 orchestration redesign."""
import pytest
from pathlib import Path
from unittest.mock import patch
from aiohttp.test_utils import TestClient, TestServer

from archon.ai.archon_router_mcp_server import ArchonRouterMCPServer


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
async def router_server_client(tmp_path):
    """Provide (server, TestClient) — server exposes the token property.

    Passes tmp_path as history_root so tests never touch ~/.archon/history/.
    """
    server = ArchonRouterMCPServer(history_root=str(tmp_path))
    client = TestClient(TestServer(server._app))
    await client.start_server()
    yield server, client
    await client.close()


@pytest.fixture
async def router_client(router_server_client):
    """Provide a thin wrapper that auto-injects the correct Bearer token."""
    server, raw_client = router_server_client

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
    async def test_token_is_64_hex_chars(self, router_server_client) -> None:
        """ArchonRouterMCPServer generates a 64-char hex token at construction."""
        server, _ = router_server_client
        assert len(server.token) == 64
        assert all(c in "0123456789abcdef" for c in server.token)

    async def test_missing_auth_header_returns_401(self, router_client) -> None:
        """POST /mcp without Authorization header -> 401 Unauthorized."""
        raw_resp = await router_client.post_mcp_no_auth(_rpc("initialize"))
        assert raw_resp.status == 401

    async def test_wrong_token_returns_401(self, router_client) -> None:
        """POST /mcp with an incorrect Bearer token -> 401 Unauthorized."""
        raw_resp = await router_client._inner.post(
            "/mcp",
            json=_rpc("initialize"),
            headers={"Authorization": "Bearer wrong_token_value"},
        )
        assert raw_resp.status == 401

    async def test_malformed_auth_scheme_returns_401(self, router_client) -> None:
        """Authorization header that is not 'Bearer ...' -> 401."""
        raw_resp = await router_client._inner.post(
            "/mcp",
            json=_rpc("initialize"),
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert raw_resp.status == 401

    async def test_correct_token_returns_200(self, router_client) -> None:
        """POST /mcp with the correct Bearer token -> 200 and valid JSON-RPC response."""
        resp = await router_client.post_mcp(_rpc("initialize"))
        assert resp["jsonrpc"] == "2.0"
        assert "result" in resp

    async def test_each_server_instance_has_unique_token(self, tmp_path) -> None:
        """Two independently constructed servers must have different tokens."""
        s1 = ArchonRouterMCPServer(history_root=str(tmp_path))
        s2 = ArchonRouterMCPServer(history_root=str(tmp_path))
        assert s1.token != s2.token

    async def test_health_endpoint_requires_no_auth(self, router_client) -> None:
        """GET /health must return 200 without any Authorization header."""
        raw_resp = await router_client._inner.get("/health")
        assert raw_resp.status == 200


# ──────────────────────────────────────────────────────────────────
# initialize
# ──────────────────────────────────────────────────────────────────


class TestInitialize:
    async def test_initialize_returns_capabilities(self, router_client) -> None:
        resp = await router_client.post_mcp(_rpc("initialize"))
        assert resp["jsonrpc"] == "2.0"
        result = resp["result"]
        assert "serverInfo" in result
        assert "capabilities" in result
        assert "tools" in result["capabilities"]


# ──────────────────────────────────────────────────────────────────
# tools/list
# ──────────────────────────────────────────────────────────────────


class TestToolsList:
    async def test_tools_list_returns_exactly_three_tools(self, router_client) -> None:
        resp = await router_client.post_mcp(_rpc("tools/list"))
        tools = resp["result"]["tools"]
        assert len(tools) == 3
        names = {t["name"] for t in tools}
        assert names == {"history_list", "history_read", "history_grep"}
        by_name = {t["name"]: t for t in tools}
        assert "truncated" in by_name["history_read"]["description"].lower()
        assert "truncated" in by_name["history_grep"]["description"].lower()


# ──────────────────────────────────────────────────────────────────
# history_read
# ──────────────────────────────────────────────────────────────────


class TestHistoryRead:
    async def test_history_read_valid_path(self, router_client) -> None:
        """A valid path under ~/.archon/history/ returns file content."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "2026-01-01.md")
        file_content = "# Session log\nsome content here"

        with patch("archon.ai.archon_router_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.is_file", return_value=True), \
             patch("pathlib.Path.read_text", return_value=file_content):
            resp = await router_client.post_mcp(
                _rpc("tools/call", {"name": "history_read", "arguments": {"path": valid_path}}),
            )

        assert resp["result"]["isError"] is False
        assert file_content in resp["result"]["content"][0]["text"]

    async def test_history_read_path_outside_history_is_denied(self, router_client) -> None:
        """A path outside ~/.archon/history/ is denied with isError=true."""
        resp = await router_client.post_mcp(
            _rpc("tools/call", {"name": "history_read", "arguments": {"path": "/etc/passwd"}}),
        )
        assert resp["result"]["isError"] is True
        assert "Access denied" in resp["result"]["content"][0]["text"]

    async def test_history_read_path_traversal_denied(self, router_client) -> None:
        """Path traversal attempt is blocked after resolve()."""
        traversal = "~/.archon/history/../../../etc/passwd"
        resp = await router_client.post_mcp(
            _rpc("tools/call", {"name": "history_read", "arguments": {"path": traversal}}),
        )
        assert resp["result"]["isError"] is True
        assert "Access denied" in resp["result"]["content"][0]["text"]

    async def test_history_read_file_not_found(self, router_client) -> None:
        """Valid path but missing file returns isError=true."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "nonexistent-9999-99-99.md")

        with patch("archon.ai.archon_router_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=False):
            resp = await router_client.post_mcp(
                _rpc("tools/call", {"name": "history_read", "arguments": {"path": valid_path}}),
            )

        assert resp["result"]["isError"] is True
        assert "not found" in resp["result"]["content"][0]["text"].lower()

    async def test_history_read_truncates_large_file(self, router_client) -> None:
        """Files exceeding 50,000 chars are truncated with a notice appended."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "2026-01-01.md")
        large_content = "x" * 60_000

        with patch("archon.ai.archon_router_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.is_file", return_value=True), \
             patch("pathlib.Path.read_text", return_value=large_content):
            resp = await router_client.post_mcp(
                _rpc("tools/call", {"name": "history_read", "arguments": {"path": valid_path}}),
            )

        assert resp["result"]["isError"] is False
        text = resp["result"]["content"][0]["text"]
        assert len(text) < 60_000
        assert "[Truncated:" in text
        assert "history_grep" in text

    async def test_history_read_does_not_truncate_small_file(self, router_client) -> None:
        """Files within the 50,000-char limit are returned in full without a truncation notice."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "2026-01-01.md")
        small_content = "# Session log\n" + "y" * 1_000

        with patch("archon.ai.archon_router_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.is_file", return_value=True), \
             patch("pathlib.Path.read_text", return_value=small_content):
            resp = await router_client.post_mcp(
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
    async def test_history_grep_valid_path_returns_matches(self, router_client) -> None:
        """Pattern matching returns only matching lines."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "2026-01-01.md")
        file_content = "line one\nerror occurred here\nanother line\nerror again"

        with patch("archon.ai.archon_router_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.is_file", return_value=True), \
             patch("pathlib.Path.read_text", return_value=file_content):
            resp = await router_client.post_mcp(
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

    async def test_history_grep_no_matches_returns_placeholder(self, router_client) -> None:
        """When pattern matches nothing, return '(no matches)'."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "2026-01-01.md")
        file_content = "line one\nline two\nline three"

        with patch("archon.ai.archon_router_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.is_file", return_value=True), \
             patch("pathlib.Path.read_text", return_value=file_content):
            resp = await router_client.post_mcp(
                _rpc("tools/call", {
                    "name": "history_grep",
                    "arguments": {"pattern": "zzz_nomatch", "path": valid_path},
                }),
            )

        assert resp["result"]["isError"] is False
        assert resp["result"]["content"][0]["text"] == "(no matches)"

    async def test_history_grep_path_outside_history_is_denied(self, router_client) -> None:
        """history_grep also enforces path restriction."""
        resp = await router_client.post_mcp(
            _rpc("tools/call", {
                "name": "history_grep",
                "arguments": {"pattern": "root", "path": "/etc/passwd"},
            }),
        )
        assert resp["result"]["isError"] is True
        assert "Access denied" in resp["result"]["content"][0]["text"]

    async def test_history_grep_invalid_regex_returns_tool_error(self, router_client) -> None:
        """An invalid regex pattern returns isError=true with a descriptive message."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "2026-01-01.md")
        file_content = "line one\nline two"

        with patch("archon.ai.archon_router_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.is_file", return_value=True), \
             patch("pathlib.Path.read_text", return_value=file_content):
            resp = await router_client.post_mcp(
                _rpc("tools/call", {
                    "name": "history_grep",
                    "arguments": {"pattern": "[unclosed", "path": valid_path},
                }),
            )

        assert resp["result"]["isError"] is True
        assert "Invalid regex pattern" in resp["result"]["content"][0]["text"]

    async def test_history_grep_line_size_limit(self, router_client) -> None:
        """Lines exceeding 10KB are skipped during grep to bound execution time."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "2026-01-01.md")
        # One huge line (>10KB) containing "needle", one normal line with "needle"
        huge_line = "a" * 10001 + "needle"
        normal_line = "small line with needle"
        file_content = f"{huge_line}\n{normal_line}"

        with patch("archon.ai.archon_router_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.is_file", return_value=True), \
             patch("pathlib.Path.read_text", return_value=file_content):
            resp = await router_client.post_mcp(
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
    async def test_hmac_compare_digest_is_used(self, router_client) -> None:
        """Auth check must delegate to hmac.compare_digest (timing-safe comparison)."""
        import hmac as hmac_mod

        call_args: list = []

        original = hmac_mod.compare_digest

        def capturing_compare_digest(a: str, b: str) -> bool:
            call_args.append((a, b))
            return original(a, b)

        with patch("archon.ai.archon_router_mcp_server.hmac.compare_digest", capturing_compare_digest):
            resp = await router_client.post_mcp(_rpc("initialize"))

        assert resp["jsonrpc"] == "2.0"
        assert len(call_args) == 1, "hmac.compare_digest must be called exactly once per request"

    async def test_wrong_token_still_returns_401_with_timing_safe_check(self, router_client) -> None:
        """Wrong token returns 401 even when hmac.compare_digest is in use."""
        raw_resp = await router_client._inner.post(
            "/mcp",
            json=_rpc("initialize"),
            headers={"Authorization": "Bearer wrong_token_value"},
        )
        assert raw_resp.status == 401


# ──────────────────────────────────────────────────────────────────
# Fix 2: history_grep output size cap + large-file behaviour
# ──────────────────────────────────────────────────────────────────


class TestHistoryGrepOutputLimits:
    async def test_history_grep_output_truncated_when_too_many_matches(self, router_client) -> None:
        """300 matching lines → only _MAX_GREP_MATCHES lines returned + truncation notice."""
        from archon.ai.archon_router_mcp_server import _MAX_GREP_MATCHES

        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "2026-01-01.md")
        file_content = "\n".join(f"match line {i}" for i in range(300))

        with patch("archon.ai.archon_router_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.is_file", return_value=True), \
             patch("pathlib.Path.read_text", return_value=file_content):
            resp = await router_client.post_mcp(
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

    async def test_history_grep_large_file_searches_entire_content(self, router_client) -> None:
        """A file exceeding _MAX_FILE_CHARS chars is grepped in full — no size-based rejection.

        Matches anywhere in the file (including beyond the 50K char mark) must be returned.
        """
        from archon.ai.archon_router_mcp_server import _MAX_FILE_CHARS

        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "2026-01-01.md")
        # Build content where the match only appears AFTER the 50K char boundary
        prefix = "a\n" * (_MAX_FILE_CHARS // 2)  # many 'a' lines filling >50K chars
        suffix = "needle_in_large_file\n"
        oversized_content = prefix + suffix

        with patch("archon.ai.archon_router_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.is_file", return_value=True), \
             patch("pathlib.Path.read_text", return_value=oversized_content):
            resp = await router_client.post_mcp(
                _rpc("tools/call", {
                    "name": "history_grep",
                    "arguments": {"pattern": "needle_in_large_file", "path": valid_path},
                }),
            )

        assert resp["result"]["isError"] is False
        assert "needle_in_large_file" in resp["result"]["content"][0]["text"]


# ──────────────────────────────────────────────────────────────────
# Fix 3: non-UTF-8 files handled gracefully (errors="replace")
# ──────────────────────────────────────────────────────────────────


class TestNonUtf8Files:
    async def test_history_read_non_utf8_returns_content_with_replacement_chars(
        self, router_client
    ) -> None:
        """read_text with errors='replace' substitutes bad bytes instead of raising."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "corrupt.md")
        # Simulate what read_text(encoding="utf-8", errors="replace") returns on bad bytes
        replaced_content = "good line\n\ufffdcorrupt byte here\ngood line again"

        with patch("archon.ai.archon_router_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.is_file", return_value=True), \
             patch("pathlib.Path.read_text", return_value=replaced_content):
            resp = await router_client.post_mcp(
                _rpc("tools/call", {"name": "history_read", "arguments": {"path": valid_path}}),
            )

        assert resp["result"]["isError"] is False
        assert "\ufffd" in resp["result"]["content"][0]["text"]

    async def test_history_grep_non_utf8_returns_matches_with_replacement_chars(
        self, router_client
    ) -> None:
        """history_grep with errors='replace' substitutes bad bytes instead of raising."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "corrupt.md")
        replaced_content = "good line with needle\n\ufffdcorrupt\nneedle again"

        with patch("archon.ai.archon_router_mcp_server._is_allowed_path", return_value=True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.is_file", return_value=True), \
             patch("pathlib.Path.read_text", return_value=replaced_content):
            resp = await router_client.post_mcp(
                _rpc("tools/call", {
                    "name": "history_grep",
                    "arguments": {"pattern": "needle", "path": valid_path},
                }),
            )

        assert resp["result"]["isError"] is False
        assert "needle" in resp["result"]["content"][0]["text"]

    async def test_history_read_actual_non_utf8_bytes(self, tmp_path) -> None:
        """read_text with errors='replace' does not raise on files with invalid UTF-8 bytes."""
        # Write a real file with invalid UTF-8 bytes
        corrupt_file = tmp_path / "corrupt.md"
        corrupt_file.write_bytes(b"valid start\xff\xfe invalid bytes\nvalid end")

        server = ArchonRouterMCPServer(history_root=str(tmp_path))
        with patch("archon.ai.archon_router_mcp_server._is_allowed_path", return_value=True):
            result = await server._tool_history_read({"path": str(corrupt_file)})

        # Must not raise; must return content with replacement characters
        assert result["isError"] is False
        assert "\ufffd" in result["content"][0]["text"]


# ──────────────────────────────────────────────────────────────────
# Unknown tool
# ──────────────────────────────────────────────────────────────────


class TestUnknownTool:
    async def test_unknown_tool_returns_error(self, router_client) -> None:
        """tools/call with an unknown tool name returns a JSON-RPC error."""
        resp = await router_client.post_mcp(
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

        server = ArchonRouterMCPServer(history_root=str(tmp_path))
        with patch("archon.ai.archon_router_mcp_server._is_allowed_path", return_value=True):
            result = await server._tool_history_read({"path": str(subdir)})

        assert result["isError"] is True
        assert "directory" in result["content"][0]["text"].lower()

    async def test_history_grep_directory_path_returns_tool_error(self, tmp_path) -> None:
        """history_grep on a directory path returns isError=true with 'directory' in message."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        server = ArchonRouterMCPServer(history_root=str(tmp_path))
        with patch("archon.ai.archon_router_mcp_server._is_allowed_path", return_value=True):
            result = await server._tool_history_grep({"path": str(subdir), "pattern": "foo"})

        assert result["isError"] is True
        assert "directory" in result["content"][0]["text"].lower()


# ──────────────────────────────────────────────────────────────────
# Fix 5: history_grep with empty/whitespace pattern returns tool error
# ──────────────────────────────────────────────────────────────────


class TestHistoryGrepEmptyPattern:
    async def test_history_grep_empty_pattern_returns_tool_error(self, router_client) -> None:
        """history_grep with pattern='' returns isError=true mentioning empty."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "2026-01-01.md")

        with patch("archon.ai.archon_router_mcp_server._is_allowed_path", return_value=True):
            resp = await router_client.post_mcp(
                _rpc("tools/call", {
                    "name": "history_grep",
                    "arguments": {"pattern": "", "path": valid_path},
                }),
            )

        assert resp["result"]["isError"] is True
        assert "empty" in resp["result"]["content"][0]["text"].lower()

    async def test_history_grep_whitespace_pattern_returns_tool_error(self, router_client) -> None:
        """history_grep with pattern='   ' (whitespace only) returns isError=true."""
        history_root = Path("~/.archon/history/").expanduser().resolve()
        valid_path = str(history_root / "2026-01-01.md")

        with patch("archon.ai.archon_router_mcp_server._is_allowed_path", return_value=True):
            resp = await router_client.post_mcp(
                _rpc("tools/call", {
                    "name": "history_grep",
                    "arguments": {"pattern": "   ", "path": valid_path},
                }),
            )

        assert resp["result"]["isError"] is True


# ──────────────────────────────────────────────────────────────────
# Fix 1: history_grep boundary tests (_MAX_GREP_MATCHES)
# ──────────────────────────────────────────────────────────────────


class TestHistoryGrepBoundary:
    async def test_history_grep_exactly_max_matches_no_truncation(self, router_server_client) -> None:
        """Exactly _MAX_GREP_MATCHES matching lines → all returned, no truncation notice."""
        from archon.ai.archon_router_mcp_server import _MAX_GREP_MATCHES

        server, raw_client = router_server_client
        match_file = server._history_root / "boundary.md"
        match_file.write_text("\n".join(f"match line {i}" for i in range(_MAX_GREP_MATCHES)))

        auth_client = raw_client
        resp = await auth_client.post(
            "/mcp",
            json=_rpc("tools/call", {
                "name": "history_grep",
                "arguments": {"pattern": "match", "path": str(match_file)},
            }),
            headers={"Authorization": f"Bearer {server.token}"},
        )
        data = await resp.json()

        assert data["result"]["isError"] is False
        text = data["result"]["content"][0]["text"]
        lines = text.splitlines()
        assert len(lines) == _MAX_GREP_MATCHES
        assert "[Truncated:" not in text

    async def test_history_grep_one_over_max_matches_truncated(self, router_server_client) -> None:
        """_MAX_GREP_MATCHES + 1 matching lines → exactly 200 returned + truncation notice mentioning '1 more'."""
        from archon.ai.archon_router_mcp_server import _MAX_GREP_MATCHES

        server, raw_client = router_server_client
        match_file = server._history_root / "over_boundary.md"
        match_file.write_text("\n".join(f"match line {i}" for i in range(_MAX_GREP_MATCHES + 1)))

        resp = await raw_client.post(
            "/mcp",
            json=_rpc("tools/call", {
                "name": "history_grep",
                "arguments": {"pattern": "match", "path": str(match_file)},
            }),
            headers={"Authorization": f"Bearer {server.token}"},
        )
        data = await resp.json()

        assert data["result"]["isError"] is False
        text = data["result"]["content"][0]["text"]
        lines = text.splitlines()
        match_lines = [l for l in lines if not l.startswith("[Truncated:")]
        assert len(match_lines) == _MAX_GREP_MATCHES
        assert any("[Truncated:" in l for l in lines)
        assert "1 more" in text

    async def test_history_grep_empty_file_returns_no_matches(self, router_server_client) -> None:
        """history_grep on an empty file returns no error and a no-matches indicator."""
        server, raw_client = router_server_client
        empty_file = server._history_root / "empty.md"
        empty_file.write_text("")

        resp = await raw_client.post(
            "/mcp",
            json=_rpc("tools/call", {
                "name": "history_grep",
                "arguments": {"pattern": "anything", "path": str(empty_file)},
            }),
            headers={"Authorization": f"Bearer {server.token}"},
        )
        data = await resp.json()

        assert data["result"]["isError"] is False
        text = data["result"]["content"][0]["text"]
        assert text == "(no matches)"


# ──────────────────────────────────────────────────────────────────
# history_list
# ──────────────────────────────────────────────────────────────────


class TestHistoryList:
    async def test_history_list_valid_directory(self, router_server_client) -> None:
        """history_list on a valid directory returns sorted filenames."""
        server, raw_client = router_server_client
        subdir = server._history_root / "daily"
        subdir.mkdir()
        (subdir / "2026-03-09-compacted.md").write_text("")
        (subdir / "2026-03-08-compacted.md").write_text("")
        (subdir / "2026-03-07-compacted.md").write_text("")

        resp = await raw_client.post(
            "/mcp",
            json=_rpc("tools/call", {
                "name": "history_list",
                "arguments": {"path": str(subdir)},
            }),
            headers={"Authorization": f"Bearer {server.token}"},
        )
        data = await resp.json()

        assert data["result"]["isError"] is False
        text = data["result"]["content"][0]["text"]
        filenames = text.splitlines()
        assert filenames == [
            "2026-03-07-compacted.md",
            "2026-03-08-compacted.md",
            "2026-03-09-compacted.md",
        ]

    async def test_history_list_includes_subdirectories(self, router_server_client) -> None:
        """history_list returns subdirectories with trailing '/' to distinguish them."""
        server, raw_client = router_server_client
        parent = server._history_root / "mixed"
        parent.mkdir()
        (parent / "file.md").write_text("")
        (parent / "subdir").mkdir()

        resp = await raw_client.post(
            "/mcp",
            json=_rpc("tools/call", {
                "name": "history_list",
                "arguments": {"path": str(parent)},
            }),
            headers={"Authorization": f"Bearer {server.token}"},
        )
        data = await resp.json()

        assert data["result"]["isError"] is False
        text = data["result"]["content"][0]["text"]
        entries = text.splitlines()
        assert "file.md" in entries
        assert "subdir/" in entries

    async def test_history_list_path_outside_restriction_denied(self, router_server_client) -> None:
        """history_list on a path outside history_root returns isError=true."""
        server, raw_client = router_server_client

        resp = await raw_client.post(
            "/mcp",
            json=_rpc("tools/call", {
                "name": "history_list",
                "arguments": {"path": "/etc"},
            }),
            headers={"Authorization": f"Bearer {server.token}"},
        )
        data = await resp.json()

        assert data["result"]["isError"] is True
        assert "Access denied" in data["result"]["content"][0]["text"]

    async def test_history_list_on_file_returns_error(self, router_server_client) -> None:
        """history_list on a file path (not a directory) returns isError=true."""
        server, raw_client = router_server_client
        file_path = server._history_root / "some.md"
        file_path.write_text("content")

        resp = await raw_client.post(
            "/mcp",
            json=_rpc("tools/call", {
                "name": "history_list",
                "arguments": {"path": str(file_path)},
            }),
            headers={"Authorization": f"Bearer {server.token}"},
        )
        data = await resp.json()

        assert data["result"]["isError"] is True
        assert "directory" in data["result"]["content"][0]["text"].lower()

    async def test_history_list_empty_directory_returns_placeholder(self, router_server_client) -> None:
        """history_list on an empty directory returns '(empty)'."""
        server, raw_client = router_server_client
        empty_dir = server._history_root / "empty_subdir"
        empty_dir.mkdir()

        resp = await raw_client.post(
            "/mcp",
            json=_rpc("tools/call", {
                "name": "history_list",
                "arguments": {"path": str(empty_dir)},
            }),
            headers={"Authorization": f"Bearer {server.token}"},
        )
        data = await resp.json()

        assert data["result"]["isError"] is False
        assert data["result"]["content"][0]["text"] == "(empty)"

    async def test_history_list_nonexistent_directory_returns_error(self, router_server_client) -> None:
        """history_list on a nonexistent path returns isError=true."""
        server, raw_client = router_server_client
        missing = server._history_root / "nonexistent_dir"

        resp = await raw_client.post(
            "/mcp",
            json=_rpc("tools/call", {
                "name": "history_list",
                "arguments": {"path": str(missing)},
            }),
            headers={"Authorization": f"Bearer {server.token}"},
        )
        data = await resp.json()

        assert data["result"]["isError"] is True
        assert "not found" in data["result"]["content"][0]["text"].lower()


# ──────────────────────────────────────────────────────────────────
# GET /health
# ──────────────────────────────────────────────────────────────────


class TestHealth:
    async def test_health_endpoint(self, router_client) -> None:
        resp = await router_client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert data == {"status": "ok"}


# ──────────────────────────────────────────────────────────────────
# ArchonToolkit integration — Task 1.2
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def router_client_with_toolkit(tmp_path):
    """Provide (server, _AuthClient) with an ArchonToolkit wired in.

    Posts to /mcp/42 (user route) so toolkit tools are visible via per-route filtering.
    """
    from archon.ai.archon_toolkit import ArchonToolkit

    toolkit = ArchonToolkit()

    async def _ping(arguments: dict, **kwargs: object) -> str:
        return "pong"

    toolkit.register_tool(
        "ping",
        {"name": "ping", "description": "Ping test", "inputSchema": {"type": "object", "properties": {}}},
        _ping,
    )

    server = ArchonRouterMCPServer(
        history_root=str(tmp_path), toolkit=toolkit, allowed_tools=frozenset(toolkit.tool_names),
    )
    client = TestClient(TestServer(server._app))
    await client.start_server()

    class _AuthClient:
        def __init__(self, inner: TestClient, tok: str) -> None:
            self._inner = inner
            self.token = tok

        async def post_mcp(self, body: dict, *, token: str | None = None) -> dict:
            tok = token if token is not None else self.token
            resp = await self._inner.post(
                "/mcp/42", json=body, headers={"Authorization": f"Bearer {tok}"},
            )
            return await resp.json()

    yield _AuthClient(client, server.token)
    await client.close()


class TestToolkitIntegration:
    async def test_all_toolkit_tools_exposed(self, router_client_with_toolkit) -> None:
        """tools/list includes both history tools and toolkit tools."""
        resp = await router_client_with_toolkit.post_mcp(_rpc("tools/list"))
        tools = resp["result"]["tools"]
        names = {t["name"] for t in tools}
        # History tools + test "ping" + all built-in toolkit tools must be present
        assert {"history_list", "history_read", "history_grep", "ping"}.issubset(names)
        assert "archon_status" in names
        assert "archon_restart" in names
        assert "list_running_agents" in names

    async def test_history_tools_still_work_with_toolkit(self, router_client_with_toolkit) -> None:
        """history_list still works when toolkit is present."""
        resp = await router_client_with_toolkit.post_mcp(
            _rpc("tools/call", {"name": "history_list", "arguments": {"path": "/nonexistent"}}),
        )
        # Will get an error because path doesn't exist/isn't allowed, but that's fine —
        # the point is it dispatches to the history handler, not to toolkit
        result = resp["result"]
        assert result["isError"] is True
        assert "Access denied" in result["content"][0]["text"]

    async def test_toolkit_tool_call_delegated(self, router_client_with_toolkit) -> None:
        """A toolkit tool call is delegated to toolkit.call_tool."""
        resp = await router_client_with_toolkit.post_mcp(
            _rpc("tools/call", {"name": "ping", "arguments": {}}),
        )
        assert resp["result"]["isError"] is False
        assert "pong" in resp["result"]["content"][0]["text"]

    async def test_unknown_tool_still_rejected(self, router_client_with_toolkit) -> None:
        """A tool unknown to both history and toolkit is rejected."""
        resp = await router_client_with_toolkit.post_mcp(
            _rpc("tools/call", {"name": "totally_unknown", "arguments": {}}),
        )
        assert "error" in resp
        assert resp["error"]["code"] == -32602

    async def test_get_agent_by_name_via_anonymous_route_rejected(
        self, router_client_with_toolkit
    ) -> None:
        """get_agent_by_name called through /mcp (anonymous route) is rejected because
        toolkit tools are not exposed on the anonymous route (per-route filtering)."""
        # Post to /mcp (anonymous) — toolkit tools are not available
        resp = await router_client_with_toolkit._inner.post(
            "/mcp",
            json=_rpc("tools/call", {"name": "get_agent_by_name", "arguments": {"name": "Atlas"}}),
            headers={"Authorization": f"Bearer {router_client_with_toolkit.token}"},
        )
        data = await resp.json()
        assert "error" in data
        assert data["error"]["code"] == -32602


# ──────────────────────────────────────────────────────────────────
# allowed_tools filtering — Task 0.2
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def router_client_allowed_tools(tmp_path):
    """Server with toolkit but allowed_tools=frozenset() (empty = no toolkit tools exposed)."""
    from archon.ai.archon_toolkit import ArchonToolkit

    toolkit = ArchonToolkit()

    async def _ping(arguments: dict, **kwargs: object) -> str:
        return "pong"

    toolkit.register_tool(
        "ping",
        {"name": "ping", "description": "Ping test", "inputSchema": {"type": "object", "properties": {}}},
        _ping,
    )

    server = ArchonRouterMCPServer(
        history_root=str(tmp_path), toolkit=toolkit, allowed_tools=frozenset(),
    )
    client = TestClient(TestServer(server._app))
    await client.start_server()

    class _AuthClient:
        def __init__(self, inner: TestClient, tok: str) -> None:
            self._inner = inner
            self.token = tok

        async def post_mcp(self, body: dict, *, token: str | None = None) -> dict:
            tok = token if token is not None else self.token
            resp = await self._inner.post(
                "/mcp", json=body, headers={"Authorization": f"Bearer {tok}"},
            )
            return await resp.json()

    yield server, _AuthClient(client, server.token)
    await client.close()


class TestAllowedToolsFiltering:
    async def test_router_mcp_server_tools_list_empty_toolkit(
        self, router_client_allowed_tools
    ) -> None:
        """With allowed_tools=frozenset(), tools/list returns zero toolkit tools;
        history tools (history_list, history_read, history_grep) are still present."""
        _server, auth_client = router_client_allowed_tools
        resp = await auth_client.post_mcp(_rpc("tools/list"))
        tools = resp["result"]["tools"]
        names = {t["name"] for t in tools}
        # Only the three hardcoded history tools
        assert names == {"history_list", "history_read", "history_grep"}
        # Specifically no toolkit tools
        assert "archon_status" not in names
        assert "cancel_agent" not in names
        assert "ping" not in names

    async def test_router_mcp_server_rejects_toolkit_call(
        self, router_client_allowed_tools
    ) -> None:
        """Calling a toolkit tool (e.g. archon_status) when allowed_tools=frozenset()
        returns an error response — the tool is not executed."""
        _server, auth_client = router_client_allowed_tools
        resp = await auth_client.post_mcp(
            _rpc("tools/call", {"name": "archon_status", "arguments": {}}),
        )
        # Should be a JSON-RPC error (unknown tool) since it's not in the allowlist
        assert "error" in resp
        assert resp["error"]["code"] == -32602

    async def test_router_mcp_server_history_read_hardcoded_not_allowlist_gated(
        self, router_client_allowed_tools
    ) -> None:
        """history_read executes normally even with allowed_tools=frozenset() because
        history tools are hardcoded handlers, not gated by allowed_tools."""
        server, auth_client = router_client_allowed_tools
        # Create a real file in the history root so we can read it
        test_file = server._history_root / "test-session.md"
        test_file.write_text("hello from history")

        resp = await auth_client.post_mcp(
            _rpc("tools/call", {
                "name": "history_read",
                "arguments": {"path": str(test_file)},
            }),
        )
        assert resp["result"]["isError"] is False
        assert "hello from history" in resp["result"]["content"][0]["text"]

    async def test_allowed_tools_selective_filtering(self, tmp_path) -> None:
        """When allowed_tools contains specific tool names, only those toolkit tools
        are exposed on the /mcp/{user_id} route in tools/list and callable."""
        from archon.ai.archon_toolkit import ArchonToolkit

        toolkit = ArchonToolkit()

        async def _ping(arguments: dict, **kwargs: object) -> str:
            return "pong"

        async def _noop(arguments: dict, **kwargs: object) -> str:
            return "noop"

        toolkit.register_tool(
            "ping",
            {"name": "ping", "description": "Ping", "inputSchema": {"type": "object", "properties": {}}},
            _ping,
        )
        toolkit.register_tool(
            "noop",
            {"name": "noop", "description": "Noop", "inputSchema": {"type": "object", "properties": {}}},
            _noop,
        )

        server = ArchonRouterMCPServer(
            history_root=str(tmp_path), toolkit=toolkit, allowed_tools=frozenset({"ping"}),
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        # Use /mcp/{user_id} route — toolkit tools visible via per-route filtering
        resp = await client.post(
            "/mcp/42",
            json=_rpc("tools/list"),
            headers={"Authorization": f"Bearer {server.token}"},
        )
        data = await resp.json()
        names = {t["name"] for t in data["result"]["tools"]}
        # ping is allowed, noop is not; history tools always present
        assert "ping" in names
        assert "noop" not in names
        assert "history_read" in names

        # Calling ping works on user route
        resp = await client.post(
            "/mcp/42",
            json=_rpc("tools/call", {"name": "ping", "arguments": {}}),
            headers={"Authorization": f"Bearer {server.token}"},
        )
        data = await resp.json()
        assert data["result"]["isError"] is False
        assert "pong" in data["result"]["content"][0]["text"]

        # Calling noop is rejected on user route (not in allowed_tools)
        resp = await client.post(
            "/mcp/42",
            json=_rpc("tools/call", {"name": "noop", "arguments": {}}),
            headers={"Authorization": f"Bearer {server.token}"},
        )
        data = await resp.json()
        assert "error" in data
        assert data["error"]["code"] == -32602

        await client.close()


# ──────────────────────────────────────────────────────────────────
# Epic 12 Task 1.1 — mcp_url_for / mcp_headers_for / user_id route
# ──────────────────────────────────────────────────────────────────


class TestMcpUrlFor:
    def test_mcp_url_for_returns_correct_url(self) -> None:
        """mcp_url_for(user_id) returns the correct URL including user_id in the path."""
        server = ArchonRouterMCPServer(history_root="/tmp", host="localhost", port=18184)
        url = server.mcp_url_for(42)
        assert url == "http://localhost:18184/mcp/42"

    def test_mcp_url_for_different_user_ids(self) -> None:
        """mcp_url_for returns distinct URLs for different user IDs."""
        server = ArchonRouterMCPServer(history_root="/tmp", host="127.0.0.1", port=9999)
        assert server.mcp_url_for(111) == "http://127.0.0.1:9999/mcp/111"
        assert server.mcp_url_for(222) == "http://127.0.0.1:9999/mcp/222"

    def test_mcp_headers_for_returns_auth_header(self) -> None:
        """mcp_headers_for returns a dict with the correct Bearer token."""
        server = ArchonRouterMCPServer(history_root="/tmp")
        headers = server.mcp_headers_for(42)
        assert headers == {"Authorization": f"Bearer {server.token}"}

    def test_mcp_headers_for_same_token_any_user_id(self) -> None:
        """mcp_headers_for returns the same token regardless of user_id."""
        server = ArchonRouterMCPServer(history_root="/tmp")
        h1 = server.mcp_headers_for(111)
        h2 = server.mcp_headers_for(222)
        assert h1 == h2


class TestUserIdRoute:
    """Test the /mcp/{user_id} route delegates to call_tool with user_id."""

    @pytest.fixture
    async def user_id_server_client(self, tmp_path):
        """Provide (server, TestClient) with /mcp/{user_id} route."""
        from archon.ai.archon_toolkit import ArchonToolkit

        toolkit = ArchonToolkit()

        captured_user_ids: list[int | None] = []
        original_call_tool = toolkit.call_tool

        async def _tracking_call_tool(name: str, arguments: dict, **kwargs):
            captured_user_ids.append(kwargs.get("user_id"))
            return await original_call_tool(name, arguments, **kwargs)

        toolkit.call_tool = _tracking_call_tool  # type: ignore[assignment]

        server = ArchonRouterMCPServer(
            history_root=str(tmp_path),
            toolkit=toolkit,
            allowed_tools=frozenset(toolkit.tool_names),
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()
        yield server, client, captured_user_ids
        await client.close()

    async def test_user_id_route_passes_user_id_to_call_tool(self, user_id_server_client) -> None:
        """POST /mcp/42 passes user_id=42 to toolkit.call_tool."""
        server, client, captured_user_ids = user_id_server_client
        resp = await client.post(
            "/mcp/42",
            json=_rpc("tools/call", {"name": "archon_status", "arguments": {}}),
            headers={"Authorization": f"Bearer {server.token}"},
        )
        data = await resp.json()
        assert data["result"]["isError"] is False
        assert 42 in captured_user_ids

    async def test_user_id_route_rejects_non_numeric_user_id(self, user_id_server_client) -> None:
        """POST /mcp/abc returns 400 Bad Request when user_id is not numeric."""
        server, client, _ = user_id_server_client
        resp = await client.post(
            "/mcp/abc",
            json=_rpc("tools/call", {"name": "archon_status", "arguments": {}}),
            headers={"Authorization": f"Bearer {server.token}"},
        )
        assert resp.status == 400
        text = await resp.text()
        assert "Invalid user_id" in text

    async def test_user_id_route_tools_list_works(self, user_id_server_client) -> None:
        """POST /mcp/42 with tools/list returns tools."""
        server, client, _ = user_id_server_client
        resp = await client.post(
            "/mcp/42",
            json=_rpc("tools/list"),
            headers={"Authorization": f"Bearer {server.token}"},
        )
        data = await resp.json()
        names = {t["name"] for t in data["result"]["tools"]}
        assert "history_list" in names


# ──────────────────────────────────────────────────────────────────
# FIX-029 Task 1.1 — directory error messages suggest history_list
# ──────────────────────────────────────────────────────────────────


class TestDirectoryErrorSuggestsHistoryList:
    async def test_history_read_directory_error_suggests_history_list(self, tmp_path) -> None:
        """history_read on a directory returns an error mentioning history_list, not history_grep."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        server = ArchonRouterMCPServer(history_root=str(tmp_path))
        with patch("archon.ai.archon_router_mcp_server._is_allowed_path", return_value=True):
            result = await server._tool_history_read({"path": str(subdir)})

        assert result["isError"] is True
        text = result["content"][0]["text"]
        assert "history_list" in text
        assert "history_grep" not in text

    async def test_history_grep_directory_error_suggests_history_list(self, tmp_path) -> None:
        """history_grep on a directory returns an error mentioning history_list, not history_grep."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        server = ArchonRouterMCPServer(history_root=str(tmp_path))
        with patch("archon.ai.archon_router_mcp_server._is_allowed_path", return_value=True):
            result = await server._tool_history_grep({"path": str(subdir), "pattern": "foo"})

        assert result["isError"] is True
        text = result["content"][0]["text"]
        assert "history_list" in text
        assert "history_grep" not in text


# ──────────────────────────────────────────────────────────────────
# FIX-029 Task 1.1 — schema description strings for path parameters
# ──────────────────────────────────────────────────────────────────


class TestSchemaDescriptions:
    def test_history_read_tool_path_description_contains_file_not_directory(self) -> None:
        """_HISTORY_READ_TOOL path description must say 'FILE (not a directory)'."""
        from archon.ai.archon_router_mcp_server import _HISTORY_READ_TOOL

        desc = _HISTORY_READ_TOOL["inputSchema"]["properties"]["path"]["description"]
        assert "FILE (not a directory)" in desc

    def test_history_read_tool_path_description_mentions_history_list(self) -> None:
        """_HISTORY_READ_TOOL path description must mention 'history_list' as discovery tool."""
        from archon.ai.archon_router_mcp_server import _HISTORY_READ_TOOL

        desc = _HISTORY_READ_TOOL["inputSchema"]["properties"]["path"]["description"]
        assert "history_list" in desc

    def test_history_grep_tool_path_description_contains_file_not_directory(self) -> None:
        """_HISTORY_GREP_TOOL path description must say 'FILE (not a directory)'."""
        from archon.ai.archon_router_mcp_server import _HISTORY_GREP_TOOL

        desc = _HISTORY_GREP_TOOL["inputSchema"]["properties"]["path"]["description"]
        assert "FILE (not a directory)" in desc

    def test_history_grep_tool_path_description_mentions_history_list(self) -> None:
        """_HISTORY_GREP_TOOL path description must mention 'history_list' as discovery tool."""
        from archon.ai.archon_router_mcp_server import _HISTORY_GREP_TOOL

        desc = _HISTORY_GREP_TOOL["inputSchema"]["properties"]["path"]["description"]
        assert "history_list" in desc


# ──────────────────────────────────────────────────────────────────
# Epic 12 Task 1.2 — per-route tool filtering on single server
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def per_route_server_client(tmp_path):
    """Server with toolkit and allowed_tools set to a subset of toolkit tools.

    The anonymous /mcp route should see only history tools.
    The /mcp/{user_id} route should see history tools + allowed toolkit tools.
    """
    from archon.ai.archon_toolkit import ArchonToolkit

    toolkit = ArchonToolkit()

    async def _ping(arguments: dict, **kwargs: object) -> str:
        return "pong"

    toolkit.register_tool(
        "ping",
        {"name": "ping", "description": "Ping test", "inputSchema": {"type": "object", "properties": {}}},
        _ping,
    )

    # allowed_tools includes "ping" and some built-in toolkit tools
    allowed = frozenset({"ping", "archon_status", "list_running_agents", "get_config", "send_notification"})
    server = ArchonRouterMCPServer(
        history_root=str(tmp_path), toolkit=toolkit, allowed_tools=allowed,
    )
    client = TestClient(TestServer(server._app))
    await client.start_server()
    yield server, client
    await client.close()


class TestPerRouteToolFiltering:
    """Epic 12 Task 1.2 — /mcp gets no toolkit tools, /mcp/{user_id} gets allowed toolkit tools."""

    async def test_anonymous_route_gets_no_toolkit_tools(self, per_route_server_client) -> None:
        """/mcp route returns only history tools in tools/list, even when allowed_tools is non-empty."""
        server, client = per_route_server_client
        resp = await client.post(
            "/mcp",
            json=_rpc("tools/list"),
            headers={"Authorization": f"Bearer {server.token}"},
        )
        data = await resp.json()
        names = {t["name"] for t in data["result"]["tools"]}
        assert names == {"history_list", "history_read", "history_grep"}
        # No toolkit tools on anonymous route
        assert "ping" not in names
        assert "archon_status" not in names

    async def test_anonymous_route_rejects_toolkit_call(self, per_route_server_client) -> None:
        """/mcp route rejects toolkit tool calls, even when allowed_tools includes them."""
        server, client = per_route_server_client
        resp = await client.post(
            "/mcp",
            json=_rpc("tools/call", {"name": "ping", "arguments": {}}),
            headers={"Authorization": f"Bearer {server.token}"},
        )
        data = await resp.json()
        # Should be a JSON-RPC error (unknown tool) since anonymous route has no toolkit access
        assert "error" in data
        assert data["error"]["code"] == -32602

    async def test_user_route_gets_allowed_toolkit_tools(self, per_route_server_client) -> None:
        """/mcp/{user_id} route returns allowed_tools toolkit tools in tools/list."""
        server, client = per_route_server_client
        resp = await client.post(
            "/mcp/42",
            json=_rpc("tools/list"),
            headers={"Authorization": f"Bearer {server.token}"},
        )
        data = await resp.json()
        names = {t["name"] for t in data["result"]["tools"]}
        # History tools always present
        assert {"history_list", "history_read", "history_grep"}.issubset(names)
        # Allowed toolkit tools present on user route
        assert "ping" in names
        assert "archon_status" in names
        # Disallowed toolkit tools must NOT be present (no over-exposure)
        assert "archon_restart" not in names
        assert "cancel_agent" not in names
        assert "spawn_background_agent" not in names

    async def test_user_route_executes_allowed_toolkit_call(self, per_route_server_client) -> None:
        """/mcp/{user_id} route executes allowed toolkit tool calls."""
        server, client = per_route_server_client
        resp = await client.post(
            "/mcp/42",
            json=_rpc("tools/call", {"name": "ping", "arguments": {}}),
            headers={"Authorization": f"Bearer {server.token}"},
        )
        data = await resp.json()
        assert data["result"]["isError"] is False
        assert "pong" in data["result"]["content"][0]["text"]

    async def test_user_route_rejects_disallowed_toolkit_call(self, per_route_server_client) -> None:
        """/mcp/{user_id} route rejects toolkit tool calls not in allowed_tools."""
        server, client = per_route_server_client
        # archon_restart is a built-in toolkit tool but NOT in the allowed_tools set
        resp = await client.post(
            "/mcp/42",
            json=_rpc("tools/call", {"name": "archon_restart", "arguments": {}}),
            headers={"Authorization": f"Bearer {server.token}"},
        )
        data = await resp.json()
        assert "error" in data
        assert data["error"]["code"] == -32602
