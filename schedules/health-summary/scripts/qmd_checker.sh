#!/usr/bin/env bash
# Archon — QMD health checker
# Exit codes:
#   0  All good: qmd installed, daemon running and healthy, history collection registered
#   1  qmd not installed (not found in PATH)
#   2  qmd installed but daemon not running
#   3  qmd daemon running but not responding on expected port
#   4  qmd daemon healthy but archon-history collection not registered
set -euo pipefail

QMD_PORT="${QMD_PORT:-8181}"
COLLECTION_NAME="${COLLECTION_NAME:-archon-history}"

# ── 1. Is qmd installed? ──────────────────────────────────────────────────────
if ! command -v qmd &>/dev/null; then
    echo "NOT_INSTALLED: qmd not found in PATH" >&2
    exit 1
fi

# ── 2. Is daemon running? (check PID file) ────────────────────────────────────
PID_FILE="$HOME/.cache/qmd/mcp.pid"
DAEMON_RUNNING=false

if [[ -f "$PID_FILE" ]]; then
    PID="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
        DAEMON_RUNNING=true
    fi
fi

if ! $DAEMON_RUNNING; then
    echo "DAEMON_NOT_RUNNING: qmd MCP daemon is not running (no live PID at $PID_FILE)" >&2
    exit 2
fi

# ── 3. Is daemon responding? (HTTP probe) ─────────────────────────────────────
# POST a minimal JSON-RPC initialize request; any HTTP response means it's up.
HTTP_CODE="$(curl -s -o /dev/null -w "%{http_code}" \
    --max-time 3 \
    -X POST \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"archon-checker","version":"1.0"}}}' \
    "http://localhost:${QMD_PORT}/mcp" 2>/dev/null || echo "000")"

if [[ "$HTTP_CODE" == "000" ]]; then
    echo "DAEMON_NOT_HEALTHY: qmd daemon PID alive but not responding on port ${QMD_PORT}" >&2
    exit 3
fi

# ── 4. Is archon-history collection registered? ───────────────────────────────
if ! qmd collection list 2>/dev/null | grep -q "^${COLLECTION_NAME} "; then
    echo "COLLECTION_MISSING: archon-history collection not registered" >&2
    exit 4
fi

# ── All good ──────────────────────────────────────────────────────────────────
echo "OK: qmd daemon running on port ${QMD_PORT}, archon-history collection registered"
exit 0
