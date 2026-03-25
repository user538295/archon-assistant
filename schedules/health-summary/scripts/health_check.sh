#!/usr/bin/env bash
# Archon health check — reports process status, recent log errors,
# disk usage, and memory.  Outputs plain text suitable for Claude to summarize.
set -euo pipefail

LOG_FILE="${HOME}/.archon/logs/archon.log"

echo "=== Archon Health Check: $(date '+%Y-%m-%d %H:%M:%S') ==="
echo ""

# ── 1. Archon process ─────────────────────────────────────────────
echo "--- Process ---"
PIDS=$(pgrep -f "python.*archon" 2>/dev/null || pgrep -f ".venv/bin/python.*main" 2>/dev/null || true)
if [[ -n "${PIDS}" ]]; then
    echo "Archon: RUNNING (PID(s): ${PIDS})"
else
    echo "Archon: NOT RUNNING"
fi
echo ""

# ── 2. Recent log errors ──────────────────────────────────────────
echo "--- Log Errors ---"
if [[ -f "${LOG_FILE}" ]]; then
    ERROR_COUNT=$(grep -c "ERROR\|CRITICAL" "${LOG_FILE}" 2>/dev/null || echo "0")
    echo "Total errors in log: ${ERROR_COUNT}"
    RECENT_ERRORS=$(grep "ERROR\|CRITICAL" "${LOG_FILE}" | tail -5 || true)
    if [[ -n "${RECENT_ERRORS}" ]]; then
        echo "Last 5 errors:"
        echo "${RECENT_ERRORS}"
    else
        echo "No errors found"
    fi
else
    echo "Log file not found: ${LOG_FILE}"
fi
echo ""

# ── 3. Disk space ─────────────────────────────────────────────────
echo "--- Disk (home) ---"
df -h "${HOME}" | tail -1 | awk '{printf "Used %s of %s (%s full)\n", $3, $2, $5}'
echo ""

# ── 4. Memory ─────────────────────────────────────────────────────
echo "--- Memory ---"
if [[ "$(uname)" == "Darwin" ]]; then
    # Total RAM from sysctl
    TOTAL_BYTES=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
    TOTAL_GB=$(echo "$TOTAL_BYTES" | awk '{printf "%.1f", $1/1073741824}')
    # Used RAM from vm_stat (pages × 4096 bytes; strip trailing period from values)
    USED_GB=$(vm_stat | awk '
        /Pages active/   { gsub(/\./, "", $NF); active=$NF+0 }
        /Pages inactive/ { gsub(/\./, "", $NF); inactive=$NF+0 }
        /Pages wired/    { gsub(/\./, "", $NF); wired=$NF+0 }
        END { printf "%.1f", (active + inactive + wired) * 4096 / 1073741824 }
    ')
    echo "Used ${USED_GB} GB / Total ${TOTAL_GB} GB"
else
    free -h | awk '/^Mem:/ {printf "Used %s / Total %s\n", $3, $2}'
fi
