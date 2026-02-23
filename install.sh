#!/usr/bin/env bash
# Archon Assistant — one-click installer
# Usage: bash install.sh
set -euo pipefail

# ── colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "  ${CYAN}▸${RESET} $*"; }
success() { echo -e "  ${GREEN}✔${RESET} $*"; }
warn()    { echo -e "  ${YELLOW}⚠${RESET}  $*"; }
die()     { echo -e "\n  ${RED}✖ Error:${RESET} $*\n"; exit 1; }
ask()     { echo -e "  ${BOLD}?${RESET}  $*"; }

ARCHON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo -e "${BOLD}  Archon Assistant — installer${RESET}"
echo "  ──────────────────────────────"
echo ""

# ── 1. prerequisites ─────────────────────────────────────────────────────────
info "Checking prerequisites..."

# uv
if ! command -v uv &>/dev/null; then
    die "uv not found. Install it from https://docs.astral.sh/uv/ and retry."
fi
success "uv $(uv --version | awk '{print $2}')"

# Python 3.12+
PYTHON_VERSION=$(uv run python --version 2>&1 | awk '{print $2}')
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
if [[ "$PYTHON_MAJOR" -lt 3 ]] || { [[ "$PYTHON_MAJOR" -eq 3 ]] && [[ "$PYTHON_MINOR" -lt 12 ]]; }; then
    die "Python 3.12+ required (found $PYTHON_VERSION). Update via uv: uv python install 3.12"
fi
success "Python $PYTHON_VERSION"

# claude CLI
if ! command -v claude &>/dev/null; then
    die "'claude' not found in PATH. Install Claude Code and authenticate first."
fi
success "claude $(claude --version 2>/dev/null | head -1 || echo '(found)')"

echo ""

# ── 2. existing installation check ───────────────────────────────────────────
OS="$(uname -s)"
ALREADY_INSTALLED=false

if [[ "$OS" == "Darwin" ]]; then
    PLIST="$HOME/Library/LaunchAgents/com.archon.assistant.plist"
    [[ -f "$PLIST" ]] && ALREADY_INSTALLED=true
elif [[ "$OS" == "Linux" ]]; then
    UNIT="$HOME/.config/systemd/user/archon.service"
    [[ -f "$UNIT" ]] && ALREADY_INSTALLED=true
fi

if $ALREADY_INSTALLED; then
    warn "Archon is already installed."
    ask "Reinstall / update? [y/N]"
    read -r REPLY
    [[ "$REPLY" =~ ^[Yy]$ ]] || { echo ""; info "Nothing changed. Exiting."; echo ""; exit 0; }
    # Unload existing service before reinstalling
    if [[ "$OS" == "Darwin" ]]; then
        launchctl unload "$PLIST" 2>/dev/null || true
    elif [[ "$OS" == "Linux" ]]; then
        systemctl stop --user archon 2>/dev/null || true
        systemctl disable --user archon 2>/dev/null || true
    fi
    echo ""
fi

# ── 3. collect configuration ──────────────────────────────────────────────────
echo -e "  ${BOLD}Configuration${RESET}"
echo "  ─────────────"
echo ""

# Bot token
if [[ -f "$ARCHON_DIR/.env" ]]; then
    EXISTING_TOKEN=$(grep -E '^TELEGRAM_BOT_TOKEN=' "$ARCHON_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '"' || true)
else
    EXISTING_TOKEN=""
fi

if [[ -n "$EXISTING_TOKEN" && "$EXISTING_TOKEN" != "your_bot_token_here" ]]; then
    ask "Telegram bot token [current: ${EXISTING_TOKEN:0:8}…] (Enter to keep):"
    read -r INPUT_TOKEN
    BOT_TOKEN="${INPUT_TOKEN:-$EXISTING_TOKEN}"
else
    ask "Telegram bot token (from @BotFather):"
    read -r BOT_TOKEN
    [[ -z "$BOT_TOKEN" ]] && die "Bot token is required."
fi

# Allowed user IDs
ask "Your Telegram user ID (find it via @userinfobot):"
read -r USER_ID
[[ -z "$USER_ID" ]] && die "User ID is required."
# Accept comma-separated IDs, normalise to TOML array
IDS_TOML="[$(echo "$USER_ID" | tr ',' '\n' | sed 's/[[:space:]]//g' | tr '\n' ',' | sed 's/,$//')]"

WORKING_DIR="$HOME/.archon/workspace"
mkdir -p "$WORKING_DIR"

echo ""

# ── 4. write .env ─────────────────────────────────────────────────────────────
info "Writing .env..."
cat > "$ARCHON_DIR/.env" <<EOF
TELEGRAM_BOT_TOKEN=$BOT_TOKEN
EOF
success ".env written"

# ── 5. write config.toml ──────────────────────────────────────────────────────
if [[ -f "$ARCHON_DIR/config.toml" ]]; then
    warn "config.toml already exists — keeping existing values."
    # Only patch allowed_user_ids and working_directory
    sed -i.bak \
        -e "s|^allowed_user_ids = .*|allowed_user_ids = $IDS_TOML|" \
        -e "s|^working_directory = .*|working_directory = \"$WORKING_DIR\"|" \
        "$ARCHON_DIR/config.toml"
    rm -f "$ARCHON_DIR/config.toml.bak"
    success "config.toml updated"
else
    info "Writing config.toml..."
    cat > "$ARCHON_DIR/config.toml" <<EOF
[access]
allowed_user_ids = $IDS_TOML

[session]
working_directory = "$WORKING_DIR"
inactivity_timeout_seconds = 1800

[output]
max_message_length = 4000
truncation_strategy = "split"

[notifications]
show_thinking_result = true
brief_tool_output = false
concise_mode = "off"
concise_interval_minutes = 2

[logging]
log_file = "$HOME/.archon/archon.log"
log_level = "INFO"
EOF
    success "config.toml written"
fi

# ── 6. install dependencies ───────────────────────────────────────────────────
info "Installing Python dependencies..."
(cd "$ARCHON_DIR" && uv sync --quiet)
success "Dependencies installed"

echo ""

# ── 7. register & start service ───────────────────────────────────────────────
echo -e "  ${BOLD}Service${RESET}"
echo "  ───────"
echo ""

UV_PATH="$(command -v uv)"
LOG_FILE="$HOME/.archon/archon.log"
mkdir -p "$HOME/.archon"

if [[ "$OS" == "Darwin" ]]; then
    info "Registering launchd service..."
    LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
    mkdir -p "$LAUNCH_AGENTS"
    sed \
        -e "s|__ARCHON_DIR__|$ARCHON_DIR|g" \
        -e "s|__UV_PATH__|$UV_PATH|g" \
        -e "s|__LOG_FILE__|$LOG_FILE|g" \
        "$ARCHON_DIR/scripts/com.archon.assistant.plist" \
        > "$LAUNCH_AGENTS/com.archon.assistant.plist"
    launchctl load "$LAUNCH_AGENTS/com.archon.assistant.plist"
    success "launchd service loaded — auto-starts on login"

elif [[ "$OS" == "Linux" ]]; then
    info "Registering systemd user service..."
    SYSTEMD_USER="$HOME/.config/systemd/user"
    mkdir -p "$SYSTEMD_USER"
    sed \
        -e "s|__ARCHON_DIR__|$ARCHON_DIR|g" \
        -e "s|__UV_PATH__|$UV_PATH|g" \
        -e "s|__LOG_FILE__|$LOG_FILE|g" \
        "$ARCHON_DIR/scripts/archon.service" \
        > "$SYSTEMD_USER/archon.service"
    systemctl --user daemon-reload
    systemctl enable --user archon
    systemctl start --user archon
    success "systemd user service enabled and started"

else
    warn "Unsupported OS ($OS). Service not registered."
    warn "Run manually: uv run python main.py"
fi

# ── 8. verify ─────────────────────────────────────────────────────────────────
echo ""
info "Waiting for Archon to start..."
sleep 2

RUNNING=false
if [[ "$OS" == "Darwin" ]]; then
    launchctl list | grep -q "com.archon.assistant" && RUNNING=true
elif [[ "$OS" == "Linux" ]]; then
    systemctl is-active --user --quiet archon && RUNNING=true
fi

echo ""
echo -e "  ${BOLD}────────────────────────────────────────${RESET}"
if $RUNNING; then
    echo -e "  ${GREEN}${BOLD}✔ Archon is running!${RESET}"
else
    echo -e "  ${YELLOW}${BOLD}⚠  Service may need a moment to start.${RESET}"
fi
echo ""
echo -e "  Logs:    ${CYAN}make logs${RESET}   or   ${CYAN}tail -f $LOG_FILE${RESET}"
echo -e "  Stop:    ${CYAN}make uninstall${RESET}"
echo -e "  Restart: ${CYAN}make uninstall && bash install.sh${RESET}"
echo ""
echo -e "  Open Telegram and send your bot a message to test."
echo -e "  ${BOLD}────────────────────────────────────────${RESET}"
echo ""
