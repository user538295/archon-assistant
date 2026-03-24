#!/usr/bin/env bash
# Watchdog: kill runaway pytest/ML worker processes when count exceeds threshold.
# Targets only pytest and its children — does NOT kill all Python on the machine.
#
# Usage:
#   bash scripts/python_watchdog.sh            # live mode
#   bash scripts/python_watchdog.sh --dry-run  # observe only, no kills

THRESHOLD=50
INTERVAL=5       # seconds between checks
COOLDOWN=30      # seconds to wait after a kill before checking again
LOGFILE="${HOME}/.archon/logs/python_watchdog.log"

DRY_RUN=false
if [ "${1:-}" = "--dry-run" ]; then
    DRY_RUN=true
fi

mkdir -p "$(dirname "$LOGFILE")"
log() { echo "[watchdog] $(date '+%H:%M:%S') $*" | tee -a "$LOGFILE"; }

last_kill=0

log "started — threshold=$THRESHOLD, interval=${INTERVAL}s, cooldown=${COOLDOWN}s, dry_run=$DRY_RUN, pid=$$"

while true; do
    # Count only test-related Python processes (pytest + their ML worker children)
    matches=$(pgrep -af "python|pytest" 2>/dev/null \
        | grep "pytest\|tokenizers\|fastembed\|onnxruntime\|sentence.transform")
    count=$(echo "$matches" | grep -c . || echo 0)

    now=$(date +%s)

    if [ "$count" -gt "$THRESHOLD" ]; then
        if $DRY_RUN; then
            log "DRY-RUN: $count test-related processes would be killed:"
            echo "$matches" | while read -r line; do
                log "  $line"
            done
            sleep "$INTERVAL"
            continue
        fi

        # Cooldown: don't kill-loop if processes respawn within cooldown window
        if [ $((now - last_kill)) -lt "$COOLDOWN" ]; then
            sleep "$INTERVAL"
            continue
        fi

        log "ALERT: $count test-related processes — sending SIGTERM to pytest"
        log "Matching processes:"
        echo "$matches" | while read -r line; do log "  $line"; done

        # Step 1: SIGTERM — lets Python clean up multiprocessing pools
        pkill -TERM -f pytest 2>/dev/null
        pkill -TERM -f "uv run pytest" 2>/dev/null

        sleep 3

        # Step 2: SIGKILL only survivors
        remaining=$(pgrep -af "python|pytest" 2>/dev/null \
            | grep -c "pytest\|tokenizers\|fastembed\|onnxruntime\|sentence.transform" \
            || echo 0)

        if [ "$remaining" -gt 0 ]; then
            log "SIGTERM insufficient ($remaining remain) — sending SIGKILL"
            pkill -9 -f pytest 2>/dev/null
            pkill -9 -f "uv run pytest" 2>/dev/null
        fi

        last_kill=$(date +%s)
        log "done — cooling down for ${COOLDOWN}s"
    fi

    sleep "$INTERVAL"
done
