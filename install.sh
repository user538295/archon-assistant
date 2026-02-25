#!/usr/bin/env bash
# Archon Assistant — one-click installer
# Usage: curl -fsSL https://raw.githubusercontent.com/user538295/archon-assistant/main/install.sh | bash
set -euo pipefail

# ── constants ─────────────────────────────────────────────────────────────────
REPO_URL="https://github.com/user538295/archon-assistant.git"
REPO_BRANCH="main"
ARCHON_APP_DIR="$HOME/.archon/app"

# ── colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "  ${CYAN}▸${RESET} $*"; }
success() { echo -e "  ${GREEN}✔${RESET} $*"; }
warn()    { echo -e "  ${YELLOW}⚠${RESET}  $*"; }
die()     { echo -e "\n  ${RED}✖ Error:${RESET} $*\n"; exit 1; }
ask()     { echo -e "  ${BOLD}?${RESET}  $*"; }

echo ""
echo -e "${BOLD}  Archon Assistant — installer${RESET}"
echo "  ──────────────────────────────"
echo ""

# ── 1. prerequisites ─────────────────────────────────────────────────────────
info "Checking prerequisites..."

# git
if ! command -v git &>/dev/null; then
    die "git not found. Install git and retry."
fi
success "git $(git --version | awk '{print $3}')"

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

# ── 3. fetch / update app ────────────────────────────────────────────────────
echo -e "  ${BOLD}App${RESET}"
echo "  ────"
echo ""

if [[ -d "$ARCHON_APP_DIR/.git" ]]; then
    info "Updating app in $ARCHON_APP_DIR..."
    git -C "$ARCHON_APP_DIR" fetch --quiet origin "$REPO_BRANCH"
    git -C "$ARCHON_APP_DIR" reset --hard "origin/$REPO_BRANCH" --quiet
    success "App updated to latest $REPO_BRANCH"
else
    info "Cloning app to $ARCHON_APP_DIR..."
    mkdir -p "$(dirname "$ARCHON_APP_DIR")"
    git clone --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" "$ARCHON_APP_DIR" --quiet
    success "App cloned"
fi

ARCHON_DIR="$ARCHON_APP_DIR"

echo ""

# ── 4. collect configuration ──────────────────────────────────────────────────
echo -e "  ${BOLD}Configuration${RESET}"
echo "  ─────────────"
echo ""

# Bot token
if [[ -f "$HOME/.archon/.env" ]]; then
    EXISTING_TOKEN=$(grep -E '^TELEGRAM_BOT_TOKEN=' "$HOME/.archon/.env" 2>/dev/null | cut -d= -f2- | tr -d '"' || true)
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

ARCHON_HOME="$HOME/.archon"
mkdir -p "$ARCHON_HOME"
WORKING_DIR="$ARCHON_HOME/workspace"
mkdir -p "$WORKING_DIR"
mkdir -p "$ARCHON_HOME/cron.d"
mkdir -p "$ARCHON_HOME/scripts"

# Optional: QMD semantic search
echo ""
warn "QMD is an optional local AI search engine that lets Claude search your"
warn "conversation history semantically.  It requires ${BOLD}Node.js ≥ 22${RESET}${YELLOW} or ${BOLD}Bun ≥ 1.0${RESET}"
warn "and downloads ${BOLD}~3 GB${RESET}${YELLOW} of local AI models on first run."
echo ""
ask "Install QMD for semantic history search? [y/N]"
read -r INSTALL_QMD
echo ""

# ── 5. write .env ─────────────────────────────────────────────────────────────
info "Writing ~/.archon/.env..."
cat > "$ARCHON_HOME/.env" <<EOF
TELEGRAM_BOT_TOKEN=$BOT_TOKEN
EOF
success "~/.archon/.env written"

# ── 6. write config.toml ──────────────────────────────────────────────────────
if [[ -f "$ARCHON_HOME/config.toml" ]]; then
    warn "~/.archon/config.toml already exists — keeping existing values."
    # Only patch allowed_user_ids and working_directory
    sed -i.bak \
        -e "s|^allowed_user_ids = .*|allowed_user_ids = $IDS_TOML|" \
        -e "s|^working_directory = .*|working_directory = \"$WORKING_DIR\"|" \
        "$ARCHON_HOME/config.toml"
    rm -f "$ARCHON_HOME/config.toml.bak"
    success "~/.archon/config.toml updated"
else
    info "Writing ~/.archon/config.toml..."
    cat > "$ARCHON_HOME/config.toml" <<EOF
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

[history]
enabled = true
directory = "~/.archon/history"

[logging]
log_file = "~/.archon/archon.log"
log_level = "INFO"

[qmd]
# Enable after running: bash ~/.archon/app/scripts/qmd_installer.sh
enabled = false
port = 8181
history_collection = "archon-history"
EOF
    success "config.toml written"
fi

# ── 7. install dependencies ───────────────────────────────────────────────────
info "Installing Python dependencies..."
(cd "$ARCHON_DIR" && uv sync --quiet)
success "Dependencies installed"

echo ""

# ── 7.1. optional: install claude-mem plugin ─────────────────────────────────
echo -e "  ${BOLD}claude-mem${RESET} is an optional persistent memory plugin that lets Claude"
echo -e "  remember context across sessions using semantic search."
echo ""
ask "Install claude-mem?"
echo -e "    1) No — skip"
echo -e "    2) For Archon project only (project scope)"
echo -e "    3) Globally for all Claude sessions (user scope)"
ask "Choose [1/2/3] (default: 1):"
read -r INSTALL_CLAUDE_MEM
echo ""

case "${INSTALL_CLAUDE_MEM:-1}" in
    2)
        info "Installing claude-mem plugin (project scope)..."
        if (cd "$ARCHON_DIR" && claude plugin install claude-mem@thedotmack --scope project 2>&1); then
            success "claude-mem installed (project scope)"
        else
            warn "claude-mem installation failed — continuing without it."
            warn "Retry later:  claude plugin install claude-mem@thedotmack --scope project"
        fi
        ;;
    3)
        info "Installing claude-mem plugin (user scope)..."
        if claude plugin install claude-mem@thedotmack --scope user 2>&1; then
            success "claude-mem installed (user scope)"
        else
            warn "claude-mem installation failed — continuing without it."
            warn "Retry later:  claude plugin install claude-mem@thedotmack --scope user"
        fi
        ;;
    *)
        info "Skipping claude-mem."
        ;;
esac

echo ""

# ── 7.5. optional: install QMD ───────────────────────────────────────────────
if [[ "$INSTALL_QMD" =~ ^[Yy]$ ]]; then
    echo -e "  ${BOLD}QMD Setup${RESET}"
    echo "  ──────────"
    echo ""
    if bash "$ARCHON_DIR/scripts/qmd_installer.sh" --non-interactive; then
        # Use tomlkit (project dependency) for reliable TOML patching
        (cd "$ARCHON_DIR" && uv run python -c "
import tomlkit, pathlib
p = pathlib.Path.home() / '.archon' / 'config.toml'
doc = tomlkit.parse(p.read_text())
doc['qmd']['enabled'] = True
p.write_text(tomlkit.dumps(doc))
")
        success "QMD enabled in config.toml"
    else
        warn "QMD installation encountered an error — Archon will start without QMD."
        warn "Retry later:  bash ~/.archon/app/scripts/qmd_installer.sh"
        warn "Then set 'enabled = true' under [qmd] in config.toml"
    fi
    echo ""
fi

# ── 8. register & start service ───────────────────────────────────────────────
echo -e "  ${BOLD}Service${RESET}"
echo "  ───────"
echo ""

UV_PATH="$(command -v uv)"
LOG_FILE="$ARCHON_HOME/archon.log"

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
    warn "Run manually: cd ~/.archon/app && uv run python main.py"
fi

# ── 9. verify ─────────────────────────────────────────────────────────────────
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
echo -e "  Logs:    ${CYAN}tail -f $LOG_FILE${RESET}"
echo -e "  Update:  ${CYAN}curl -fsSL https://raw.githubusercontent.com/user538295/archon-assistant/main/install.sh | bash${RESET}"
echo -e "  Stop:    ${CYAN}launchctl unload ~/Library/LaunchAgents/com.archon.assistant.plist${RESET}  (macOS)"
echo -e "           ${CYAN}systemctl stop --user archon${RESET}  (Linux)"
echo ""
echo -e "  Open Telegram and send your bot a message to test."
echo -e "  ${BOLD}────────────────────────────────────────${RESET}"
echo ""
