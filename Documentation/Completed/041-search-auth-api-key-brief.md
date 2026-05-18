# Feature Brief: Search API Key Authentication (Roadmap 5a)

## Problem
archon-search exposes every endpoint to any local process with no authentication. This blocks the namespace isolation work (5b–5d) and makes the service unsafe to expose beyond localhost without a breaking change later.

## Goal
Every archon-search API call except `GET /health` requires a valid `ARCHON_SEARCH_API_KEY`. The key is auto-generated on first run and written to `~/.archon/.search.env`. `SearchClient` reads and injects it automatically. Existing local users need zero manual configuration.

## Users & Context
- **Archon operators running the daemon locally** — auth happens transparently; they never touch a key manually.
- **Developers deploying in Docker/CI** — they set `ARCHON_SEARCH_API_KEY` in the environment and skip the file entirely.

## Core Flow

1. archon-search starts; checks `ARCHON_SEARCH_API_KEY` env var first.
2. If not set, checks `~/.archon/.search.env`; loads the key from there if present. The file format is a single dotenv-style line: `ARCHON_SEARCH_API_KEY=<64-char-hex>` (no quotes, no trailing space, newline-terminated). On load, the value is validated: it must be a non-empty hex string. If malformed, log ERROR and treat as "not found."
3. If neither exists, auto-generates a 64-char hex key via `secrets.token_hex(32)` using this sequence: (a) attempt `os.open('~/.archon/.search.env.tmp', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)` — if this raises `FileExistsError`, another process is concurrently generating; sleep 100ms and attempt to read `.search.env`. If `.search.env` now exists, use its key. If it still does not exist, the previous process crashed leaving an orphaned temp file — delete `.search.env.tmp` and retry this step once. (b) Write the `ARCHON_SEARCH_API_KEY=<hex>` line to the file descriptor. (c) Call `os.replace('~/.archon/.search.env.tmp', '~/.archon/.search.env')` to atomically move it into place. `~/.archon/` is created with `os.makedirs(exist_ok=True)` before this sequence.
4. FastAPI middleware validates `Authorization: Bearer {key}` on every incoming request except `GET /health`. Both missing key and wrong key return **401** with a `WWW-Authenticate: Bearer` header. 403 is not used here — it is reserved for "authenticated but not authorized" scenarios in 5c. Key comparison MUST use `secrets.compare_digest()`, not `==`, to prevent timing side-channels. archon-search logs at INFO on startup indicating auth is enabled and which source the key came from: `"API key authentication enabled (source: env var | auto-generated | file: ~/.archon/.search.env)"`. The key itself must never appear in logs.
5. Archon starts; `SearchClient` reads `ARCHON_SEARCH_API_KEY` env var first, then falls back to `~/.archon/.search.env`. Key loading is lazy — not at construction time — to tolerate archon-search starting after Archon. Caching behaviour: cache the key on successful load; do not cache failures (retry on miss so the startup race self-heals as soon as archon-search writes the key file). If no key can be loaded from any source, `SearchClient` logs a WARNING: `"No ARCHON_SEARCH_API_KEY found — all search requests will fail with 401"`.
6. `SearchClient` injects `Authorization: Bearer {key}` via a custom `httpx.Auth` subclass (not a default header on `httpx.AsyncClient` — headers set at construction time are incompatible with lazy loading). The `Auth` subclass resolves and caches the key on first use. On receiving a 401 response, it clears the cached value, re-reads the key file once, and retries the request. If the retry also returns 401, the `async_auth_flow()` generator stops yielding — the 401 response propagates to the caller as an `HTTPStatusError` when `raise_for_status()` is called. Before stopping, the Auth subclass logs at ERROR: `"Search authentication failed — check ARCHON_SEARCH_API_KEY or ~/.archon/.search.env"`. The SearchClient method-level `except HTTPStatusError` handler catches this exception and returns `None` — but must NOT log a generic HTTP warning for 401/403 (the Auth subclass has already logged the actionable ERROR). Checking `exc.response.status_code in (401, 403)` before the generic warning prevents double-logging.

## In Scope
- FastAPI middleware that validates the key on all routes except `GET /health`; exact path match only (`/health/` with trailing slash is NOT exempt)
- Auto-generation of key on first run → `~/.archon/.search.env` (atomic write, chmod 600)
- `ARCHON_SEARCH_API_KEY` env var as higher-priority override (env var wins over file); empty string treated as absent
- `SearchClient` lazy key loading via `httpx.Auth` subclass; caches on success, retries on miss, re-reads on 401, distinct ERROR log on auth failure
- Fail-closed logging: WARNING when no key source is found; ERROR (with actionable message) on 401/403 responses
- `secrets.compare_digest()` for key comparison in middleware (required; prevents timing side-channels)
- archon-search startup log indicating auth source (key value must never appear)
- `archon doctor` check: key file exists, has permissions 600, AND authenticated `GET /status` via `SearchClient` returns 200 (tests the full chain; 401 → actionable failure message)
- All callers that go through `SearchClient` inherit auth automatically via the `httpx.Auth` subclass — no change required at those call sites. The following raw `httpx.AsyncClient` callers bypass `SearchClient` entirely and MUST be fixed:
  - `doctor.py` `_check_search_health()` (line ~91): raw `httpx.AsyncClient` POST to search URL — must be replaced with calls to the existing `SearchClient.list_collections()` and/or `SearchClient.collection_info(name)` REST methods, which return equivalent collection metadata. Do NOT add JSON-RPC support to `SearchClient`. **Verification required**: before replacing the JSON-RPC call, the implementer must confirm that `GET /collections` (`list_collections()`) and `GET /collections/{name}` (`collection_info(name)`) return the same fields currently used by `_check_search_health()` for staleness and centroid checks (`last_indexed`, `doc_count`, centroid data). If fields differ, the archon-search REST endpoints must be extended to include them.
  - `search_context_provider.py` `_search_collection()` (line ~44–56): raw `httpx.AsyncClient` JSON-RPC POST — this is the primary production search path; must be routed through `SearchClient` by adding a `SearchClient.search(collection, query, top_k, ...)` method that calls a new `POST /search` REST endpoint on archon-search. The `self._http` raw `httpx.AsyncClient` in `SearchContextProvider.__init__` (line ~156) becomes dead code after this change and must be removed along with its `close()` lifecycle call. Note: this requires adding `POST /search` to the archon-search server — this is a required server-side change bundled with this feature. The `POST /search` endpoint contract (request: `{collection, query, top_k}`, response: list of result objects with `text`, `score`, `source_path`, `chunk_id`) must be defined as part of implementation planning — the brief does not lock down the full schema but the implementer must align with the existing `SearchResult` domain type.
  Both are required fixes; missing either one will cause silent 401 failures on real search queries.
- Unit tests (see Test Coverage section below)

## Out of Scope
- Key rotation — deferred; can be a future `archon search rotate-key` CLI command
- Multiple keys or per-key scopes — that is 5c (namespace isolation)
- `/status` and `/indexing-state` staying public — both are gated; operational inspection goes through `archon doctor` or MCP tools
- HTTPS/TLS between Archon and archon-search — separate concern; auth over plaintext localhost is acceptable for the daemon use case

## Key Decisions
- **Build now, not deferred**: auth is the prerequisite for 5b–5d; retrofitting it after namespaces exist is a breaking change for all callers.
- **Always enforce regardless of bind address**: a security toggle that silently changes with a config value is a footgun.
- **Only `GET /health` stays public** (exact path match — `/health/` with trailing slash is not exempt): consistent rule with no ambiguity; it is the only endpoint the gateway probes before a session is established.
- **Auto-generate on first run**: zero-config for existing local users.
- **`~/.archon/.search.env` with env var override**: keeps secrets out of TOML config; env var pattern works for Docker/CI without changing the file-based default.
- **Key format: `secrets.token_hex(32)`** (64-char hex string) — sufficient entropy, no special characters that break shell quoting or `.env` parsing. File format: `ARCHON_SEARCH_API_KEY=<hex>` (no quotes, no trailing space, newline-terminated).
- **`httpx.Auth` subclass for injection**: `httpx.AsyncClient(auth=SearchApiKeyAuth())` is set at construction time. This is NOT the same as `headers={"Authorization": ...}` — the Auth subclass's `async_auth_flow()` method is invoked per-request by `httpx.AsyncClient` and lazily resolves the key on first call. Construction is cheap; key resolution is deferred. The same instance handles success caching and 401 retry.
- **`async_auth_flow()` (not `sync_auth_flow()` or `auth_flow()`)**: Since `SearchClient` exclusively uses `httpx.AsyncClient`, the Auth subclass MUST override `async_auth_flow()` — that is what `AsyncClient` invokes. `sync_auth_flow()` is called by `httpx.Client` (the sync client) and is NOT invoked by `AsyncClient`. The base `auth_flow()` is I/O-free and must NOT be used for disk-reading auth. The disk read (`Path.read_text()` for a single tiny local file) is performed inside the async generator; use `asyncio.to_thread()` if the sync read causes measurable blocking (unlikely for a tiny file, but acceptable to do up front). The 401 retry is handled by yielding a second request inside `async_auth_flow()`: `response = yield request; if response.status_code == 401: self._clear_cache(); yield self._new_request_with_refreshed_key(request)`. If writing tests with `httpx.Client` (sync test client), override `sync_auth_flow()` as well — but the production path uses `async_auth_flow()` only.
- **`secrets.compare_digest()` in middleware**: prevents timing side-channels; required even for localhost deployments.
- **Both missing key and wrong key → 401**: per RFC 9110, 403 means "authenticated but not authorized." Until 5c introduces per-key scopes, there is no authorization layer — only authentication.

## Test Coverage

### Middleware (archon-search side)
- Valid key → 200
- Missing `Authorization` header → 401 with `WWW-Authenticate: Bearer`
- Wrong key → 401 with `WWW-Authenticate: Bearer`
- Malformed `Authorization` header (e.g., `Basic ...`, no space after `Bearer`) → 401
- Empty bearer value → 401
- `GET /health` bypasses auth → 200
- `POST /health`, `DELETE /health` etc. — auth IS required (only `GET /health` is exempt)
- Exact path match only: `GET /health/` (trailing slash) does NOT bypass auth → 401
- Key comparison uses `secrets.compare_digest()` (not `==`) — verify via inspection or mock

### Key loading priority (archon-search + SearchClient)
- Env var present → used
- Env var absent + file present → file used
- Both absent → auto-generated
- Env var takes priority over file when both present
- Env var set to empty string → treated as absent (falls back to file)

### Auto-generation
- Generates 64-char hex string
- Writes `ARCHON_SEARCH_API_KEY=<hex>` format (no quotes, newline-terminated)
- Uses atomic exclusive create (`os.open(temp, O_WRONLY|O_CREAT|O_EXCL, 0o600)` on the temp file)
- `O_EXCL` raises `FileExistsError` (concurrent start) → process reads existing `.search.env`
- Orphaned `.search.env.tmp` exists (prior crash), `.search.env` absent → deletes temp, retries, succeeds
- Uses write-to-temp-then-rename (`os.replace`) to prevent partial-write visibility

### File format validation
- Malformed key (non-hex value) → log ERROR, treat as not found
- Empty value → log ERROR, treat as not found
- Key with extra whitespace → strip and validate

### File permissions
- Auto-generated file created with 600
- File with permissions wider than 600 → `chmod 600` attempted; if `chmod` fails, WARNING logged and proceed

### SearchClient lazy load and retry
- No key at construction time → ok (no error at startup)
- Key loaded on first request (not at `__init__`)
- Retry on miss (failure not cached) — self-heals when archon-search writes the key
- Cached on successful load
- 401 response → clears cache, re-reads key file once, retries request
- 401 after retry → ERROR logged with `"Search authentication failed — check ARCHON_SEARCH_API_KEY or ~/.archon/.search.env"`, returns `None`
- 401 response after retry: ERROR logged exactly once (Auth subclass logs; method handler skips 401/403 warning)
- No key from any source → WARNING logged with `"No ARCHON_SEARCH_API_KEY found — all search requests will fail with 401"`

### `archon doctor` checks
- Key file missing → reported as failure
- Key file permissions wider than 600 → reported
- Authenticated `GET /status` succeeds → pass
- Authenticated `GET /status` returns 401 → fail with actionable message

## Edge Cases & Constraints
- **Startup race**: if Archon starts before archon-search has written `.search.env`, `SearchClient` will not find the key on first request. Mitigation: do not cache load failures — retry on every request until the key is found. The race self-heals as soon as archon-search writes the key file. No restart is required.
- **Stale key / upgrade race**: when `SearchClient`'s `httpx.Auth` subclass receives a 401 response, it clears the cached key, re-reads the key file once, and retries the request. If the retry also returns 401, it logs at ERROR level and returns `None`. This eliminates the upgrade-order race without requiring strict restart sequencing.
- **File format**: `~/.archon/.search.env` contains exactly one line: `ARCHON_SEARCH_API_KEY=<64-char-hex>` (no quotes, no trailing space, newline-terminated). Both archon-search (writer) and SearchClient (reader) must agree on this format. On load, the value is validated; non-hex or empty values are treated as "not found" with an ERROR log.
- **Atomic write**: key file is written via `os.open(temp, O_WRONLY|O_CREAT|O_EXCL, 0o600)` then `os.replace(temp, final)`. Permissions are set on the temp file before any rename — there is no world-readable window. The temp file is in the same directory as `.search.env` to ensure rename is atomic (same filesystem).
- **Concurrent first-run**: `O_EXCL` on the temp file ensures only one process writes at a time. The losing process (gets `FileExistsError`) sleeps briefly then reads the final file. If the winning process crashed (temp exists, final does not), the losing process deletes the orphaned temp and retries once.
- **Directory creation**: archon-search creates `~/.archon/` with `os.makedirs(exist_ok=True)` before attempting to write `.search.env`. This handles fresh installs where archon-search starts before Archon has initialised its data directory.
- **File permissions — wider than 600**: if `.search.env` already exists with permissions wider than 600, attempt `chmod 600` and log INFO. If `chmod` fails, log WARNING and proceed. Auto-generated files are not user-authored; there is no legitimate reason to keep a world-readable secret key.
- **Windows compatibility**: `chmod 600` is POSIX-only. On Windows, `os.chmod` does not restrict file access by user. The permission check in `archon doctor` and the chmod-on-load path must be skipped or adapted on Windows (log INFO: `"permission check skipped on Windows"`). This follows the existing platform-specific pattern in `archon/platform/` — platform-specific behaviour belongs there.
- **Docker/CI**: `ARCHON_SEARCH_API_KEY` env var skips file loading entirely; no `~/.archon/` directory is required.
- **JSON-RPC endpoint coverage**: `search_context_provider.py` and `doctor.py` make JSON-RPC calls to the search server. If these are served on the same FastAPI app and port as the REST API, the middleware covers them. If FastMCP serves JSON-RPC on a separate port, those endpoints are NOT covered by the middleware and require a separate auth strategy. This must be verified at implementation time; the brief assumes a single port/app.
- **Fail-closed logging**: if no key can be loaded from any source, `SearchClient` logs WARNING: `"No ARCHON_SEARCH_API_KEY found — all search requests will fail with 401"`. 401/403 HTTP responses log at ERROR with: `"Search authentication failed — check ARCHON_SEARCH_API_KEY or ~/.archon/.search.env"`.

## Open Questions
- Should `archon update` enforce the archon-search-first restart order automatically? Not blocking for 5a — document the order for now; revisit when `archon update` is next touched.

## Future Iterations
- Key rotation via `archon search rotate-key` CLI command
- Multiple named keys with per-key scopes (requires 5c namespace isolation first)
- HTTPS between Archon and archon-search (relevant only if the service is exposed beyond localhost)
