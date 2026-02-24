#!/usr/bin/env bash
# Archon — QMD installer
# Downloads and configures QMD for use with Archon.
# Can be called from install.sh (interactive) or by Archon at runtime.
#
# Usage:
#   bash scripts/qmd_installer.sh [--non-interactive]
#
# Options:
#   --non-interactive   Skip all confirmation prompts (for runtime invocation via Archon)
#
# Environment:
#   QMD_PORT             MCP daemon port (default: 8181)
#   COLLECTION_NAME      Archon history collection name (default: archon-history)
#   HISTORY_DIR          Path to Archon history directory (default: ~/.archon/history)
set -euo pipefail

NON_INTERACTIVE=false
for arg in "$@"; do
    [[ "$arg" == "--non-interactive" ]] && NON_INTERACTIVE=true
done

QMD_PORT="${QMD_PORT:-8181}"
COLLECTION_NAME="${COLLECTION_NAME:-archon-history}"
HISTORY_DIR="${HISTORY_DIR:-$HOME/.archon/history}"

# ── colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "  ${CYAN}▸${RESET} $*"; }
success() { echo -e "  ${GREEN}✔${RESET} $*"; }
warn()    { echo -e "  ${YELLOW}⚠${RESET}  $*"; }
die()     { echo -e "\n  ${RED}✖ Error:${RESET} $*\n"; exit 1; }

echo ""
echo -e "${BOLD}  QMD Installer for Archon${RESET}"
echo "  ──────────────────────────"
echo ""

# ── 1. Mandatory warning ──────────────────────────────────────────────────────
warn "QMD requires ${BOLD}Node.js ≥ 22${RESET}${YELLOW} or ${BOLD}Bun ≥ 1.0${RESET}."
warn "On first run it will automatically download ${BOLD}~3 GB${RESET}${YELLOW} of local AI models:"
echo ""
echo -e "    ${CYAN}~/.cache/qmd/models/${RESET}"
echo -e "    • embeddinggemma-300M-Q8_0.gguf       (~300 MB)"
echo -e "    • qwen3-reranker-0.6b-q8_0.gguf       (~600 MB)"
echo -e "    • qmd-query-expansion-1.7B-q4_k_m.gguf (~1 GB)"
echo ""
warn "This download happens ${BOLD}once${RESET}${YELLOW} and requires a stable internet connection."
echo ""

if ! $NON_INTERACTIVE; then
    echo -e "  ${BOLD}?${RESET}  Continue with QMD installation? [y/N]"
    read -r REPLY
    if ! [[ "$REPLY" =~ ^[Yy]$ ]]; then
        echo ""
        info "QMD installation skipped."
        echo ""
        exit 0
    fi
    echo ""
fi

# ── 2. Check runtime (Node.js ≥ 22 or Bun ≥ 1.0) ────────────────────────────
HAS_RUNTIME=false
INSTALL_CMD=""

# Check Bun first (preferred — qmd is distributed via npm but Bun is faster)
if command -v bun &>/dev/null; then
    BUN_VERSION="$(bun --version 2>/dev/null | head -1)"
    BUN_MAJOR="$(echo "$BUN_VERSION" | cut -d. -f1)"
    if [[ "$BUN_MAJOR" -ge 1 ]]; then
        HAS_RUNTIME=true
        INSTALL_CMD="bun"
        success "Bun $BUN_VERSION (will be used for install)"
    else
        warn "Bun $BUN_VERSION found but ≥ 1.0 required"
    fi
fi

# Fall back to Node.js
if ! $HAS_RUNTIME && command -v node &>/dev/null; then
    NODE_VERSION="$(node --version 2>/dev/null | sed 's/v//')"
    NODE_MAJOR="$(echo "$NODE_VERSION" | cut -d. -f1)"
    if [[ "$NODE_MAJOR" -ge 22 ]]; then
        HAS_RUNTIME=true
        INSTALL_CMD="npm"
        success "Node.js $NODE_VERSION (will be used for install)"
    else
        warn "Node.js $NODE_VERSION found but ≥ 22 required"
    fi
fi

if ! $HAS_RUNTIME; then
    die "Neither Bun ≥ 1.0 nor Node.js ≥ 22 was found.\n\n  Install one of:\n    • Bun:     https://bun.sh\n    • Node.js: https://nodejs.org (use version 22 LTS or later)"
fi

# ── 3. Install qmd ────────────────────────────────────────────────────────────
if command -v qmd &>/dev/null; then
    CURRENT_VERSION="$(qmd --version 2>/dev/null | awk '{print $2}' || echo 'unknown')"
    success "qmd $CURRENT_VERSION already installed — skipping install step"
else
    info "Installing qmd globally via $INSTALL_CMD..."
    if [[ "$INSTALL_CMD" == "bun" ]]; then
        bun install -g @tobilu/qmd
    else
        npm install -g @tobilu/qmd
    fi
    success "qmd installed"
fi

# Verify qmd is now in PATH
if ! command -v qmd &>/dev/null; then
    die "qmd installed but not found in PATH. Add your package manager's bin directory to PATH and retry."
fi

# ── 4. Register archon-history collection ─────────────────────────────────────
mkdir -p "$HISTORY_DIR"

if qmd collection list 2>/dev/null | grep -q "^${COLLECTION_NAME} "; then
    success "Collection '${COLLECTION_NAME}' already registered — skipping"
else
    info "Registering '${COLLECTION_NAME}' collection (${HISTORY_DIR})..."
    qmd collection add "$HISTORY_DIR" --name "$COLLECTION_NAME"
    qmd context add "qmd://${COLLECTION_NAME}" \
        "Archon conversation history: daily Markdown logs of messages, tool calls, and Claude responses"
    success "Collection '${COLLECTION_NAME}' registered"
fi

# ── 5. Initial embed (downloads models on first run) ─────────────────────────
info "Running initial embed (this may download models on first run — ~3 GB)..."
info "This can take several minutes. Please wait..."
qmd embed
success "Embeddings generated"

# ── 6. Start daemon ───────────────────────────────────────────────────────────
PID_FILE="$HOME/.cache/qmd/mcp.pid"
DAEMON_RUNNING=false

if [[ -f "$PID_FILE" ]]; then
    PID="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
        DAEMON_RUNNING=true
    fi
fi

if $DAEMON_RUNNING; then
    success "QMD daemon already running (PID $PID)"
else
    info "Starting QMD MCP daemon on port ${QMD_PORT}..."
    qmd mcp --http --port "$QMD_PORT" --daemon
    sleep 1  # give daemon a moment to write PID file

    # Confirm it started
    if [[ -f "$PID_FILE" ]]; then
        NEW_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
        success "QMD daemon started (PID $NEW_PID, port ${QMD_PORT})"
    else
        warn "Daemon started but PID file not found — check qmd status"
    fi
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "  ${BOLD}────────────────────────────────────────${RESET}"
echo -e "  ${GREEN}${BOLD}✔ QMD is ready for Archon${RESET}"
echo ""
echo -e "  Enable it in ${CYAN}config.toml${RESET}:"
echo -e "    ${CYAN}[qmd]${RESET}"
echo -e "    ${CYAN}enabled = true${RESET}"
echo ""
echo -e "  Check status anytime: ${CYAN}qmd status${RESET}"
echo -e "  ${BOLD}────────────────────────────────────────${RESET}"
echo ""
