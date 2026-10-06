#!/bin/bash
# Nightly wrapper for launchd/cron: runs the incremental sync and keeps a rolling log.
#   ./run_sync.sh            -> incremental
#   ./run_sync.sh --full     -> full backfill (any sync.py args pass through)
set -u
cd "$(dirname "$0")"
mkdir -p logs
LOG="logs/sync-$(date +%Y-%m-%d).log"
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
fi
echo "=== $(date -u +%FT%TZ) start: sync.py $* ===" >> "$LOG"
"$PY" sync.py "$@" >> "$LOG" 2>&1
RC=$?
echo "=== $(date -u +%FT%TZ) exit $RC ===" >> "$LOG"
# keep 30 days of logs
find logs -name 'sync-*.log' -mtime +30 -delete 2>/dev/null
exit $RC
