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
2. If not set, checks `~/.archon/.search.env`; loads the key from there if present.
3. If neither exists, generates a 32-byte random hex key via `secrets.token_hex(32)`, writes it to `~/.archon/.search.env` (chmod 600), and uses it for this run.
4. FastAPI middleware validates `Authorization: Bearer {key}` on every incoming request except `GET /health`. Missing key → 401. Wrong key → 403.
5. Archon starts; `SearchClient` reads `ARCHON_SEARCH_API_KEY` env var first, then falls back to `~/.archon/.search.env`. Key is loaded lazily — not at construction time — to tolerate archon-search starting after Archon.
6. `SearchClient` injects `Authorization: Bearer {key}` on every request via a default header on the `httpx.AsyncClient`.

## In Scope
- FastAPI middleware that validates the key on all routes except `GET /health`
- Auto-generation of key on first run → `~/.archon/.search.env` (chmod 600)
- `ARCHON_SEARCH_API_KEY` env var as higher-priority override (env var wins over file)
- `SearchClient` lazy key loading and header injection
- `archon doctor` check: key file exists and has permissions 600
- Unit tests: valid key, missing key, wrong key, `/health` bypass, key loading priority (env var > file > auto-generate)

## Out of Scope
- Key rotation — deferred; can be a future `archon search rotate-key` CLI command
- Multiple keys or per-key scopes — that is 5c (namespace isolation)
- `/status` and `/indexing-state` staying public — both are gated; operational inspection goes through `archon doctor` or MCP tools
- HTTPS/TLS between Archon and archon-search — separate concern; auth over plaintext localhost is acceptable for the daemon use case

## Key Decisions
- **Build now, not deferred**: auth is the prerequisite for 5b–5d; retrofitting it after namespaces exist is a breaking change for all callers.
- **Always enforce regardless of bind address**: a security toggle that silently changes with a config value is a footgun.
- **Only `GET /health` stays public**: consistent rule with no ambiguity; it is the only endpoint the gateway probes before a session is established.
- **Auto-generate on first run**: zero-config for existing local users.
- **`~/.archon/.search.env` with env var override**: keeps secrets out of TOML config; env var pattern works for Docker/CI without changing the file-based default.
- **Key format: `secrets.token_hex(32)`** (64-char hex string) — sufficient entropy, no special characters that break shell quoting or `.env` parsing.

## Edge Cases & Constraints
- **Startup race**: if Archon starts before archon-search has written `.search.env`, `SearchClient` will not find the key at construction time. Mitigation: load the key lazily (on first request, not at `__init__`). `SearchClient` already returns `None` on connection failure, so the window is handled gracefully.
- **File permissions**: `.search.env` is written chmod 600. If the file already exists with incorrect permissions, log a WARNING but do not overwrite — the user may have set them deliberately.
- **Upgrade order**: on upgrade, archon-search restarts first (generates and writes the key), then Archon restarts (reads the key). The gap between restarts produces 401s. Mitigation: document the restart order; `archon update` should restart archon-search before Archon.
- **Docker/CI**: `ARCHON_SEARCH_API_KEY` env var skips file loading entirely; no `~/.archon/` directory is required.

## Open Questions
- Should `archon update` enforce the archon-search-first restart order automatically? Not blocking for 5a — document the order for now; revisit when `archon update` is next touched.

## Future Iterations
- Key rotation via `archon search rotate-key` CLI command
- Multiple named keys with per-key scopes (requires 5c namespace isolation first)
- HTTPS between Archon and archon-search (relevant only if the service is exposed beyond localhost)
