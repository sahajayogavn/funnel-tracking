#!/usr/bin/env bash

# Run one pipeline command every 15 minutes, accounting for processing time.
# Usage:
#   tools/run_inbox_mas_loop.sh fetch
#   tools/run_inbox_mas_loop.sh mas
#   tools/run_inbox_mas_loop.sh classify
#   tools/run_inbox_mas_loop.sh mas-classify  # classify -> MAS
#   tools/run_inbox_mas_loop.sh both       # fetch -> classify -> MAS
#   tools/run_inbox_mas_loop.sh pipeline   # same as both

set -u

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(dirname -- "$SCRIPT_DIR")"
PYTHON="$PROJECT_ROOT/.venv/bin/python"

PAGE_ID="${FUNNEL_PAGE_ID:-1548373332058326}"
INTERVAL_SECONDS="${FUNNEL_INTERVAL_SECONDS:-900}"
FETCH_WORKERS="${FUNNEL_FETCH_WORKERS:-5}"
MAS_MAX_THREADS="${FUNNEL_MAS_MAX_THREADS:-5}"
MODE="${1:-}"

if [[ ! -x "$PYTHON" ]]; then
  echo "Python virtual environment not found: $PYTHON" >&2
  exit 1
fi

case "$MODE" in
  fetch|classify|mas|mas-classify|both|pipeline) ;;
  *)
    echo "Usage: $0 {fetch|classify|mas|mas-classify|both|pipeline}" >&2
    exit 2
    ;;
esac

run_fetch() {
  "$PYTHON" "$PROJECT_ROOT/tools/l5_fetch_fb_messages.py" \
    --pageId "$PAGE_ID" \
    --credential default \
    --time_range 7d \
    --cdp \
    --refresh \
    --workers "$FETCH_WORKERS"
}

run_mas() {
  "$PYTHON" "$PROJECT_ROOT/tools/l5_inbox_mas_runner.py" \
    --page-id "$PAGE_ID" \
    --once \
    --max-threads "$MAS_MAX_THREADS"
}

run_classify() {
  "$PYTHON" "$PROJECT_ROOT/tools/l5_fetch_fb_messages.py" \
    --pageId "$PAGE_ID" \
    --action classify_city_llm
}

trap 'echo "Stopping inbox/MAS loop."; exit 0' INT TERM

while true; do
  cycle_started="$(date +%s)"
  cycle_label="$(date '+%Y-%m-%d %H:%M:%S')"
  echo "[$cycle_label] Starting $MODE cycle for page $PAGE_ID"

  fetch_status=0
  classify_status=0
  pipeline_mode=false
  if [[ "$MODE" == "both" || "$MODE" == "pipeline" ]]; then
    pipeline_mode=true
  fi

  if [[ "$MODE" == "fetch" || "$pipeline_mode" == true ]]; then
    run_fetch || fetch_status=$?
    if (( fetch_status != 0 )); then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] Inbox fetch failed with status $fetch_status"
    fi
  fi

  if [[ "$MODE" == "classify" || "$MODE" == "mas-classify" ]]; then
    run_classify || classify_status=$?
    if (( classify_status != 0 )); then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] City/program classification failed with status $classify_status"
    fi
    if [[ "$MODE" == "mas-classify" && "$classify_status" -eq 0 ]]; then
      run_mas || echo "[$(date '+%Y-%m-%d %H:%M:%S')] MAS failed with status $?"
    elif [[ "$MODE" == "mas-classify" ]]; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] Skipping MAS because classification failed"
    fi
  elif [[ "$pipeline_mode" == true && "$fetch_status" -eq 0 ]]; then
    run_classify || classify_status=$?
    if (( classify_status != 0 )); then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] City/program classification failed with status $classify_status"
    fi
    if (( classify_status == 0 )); then
      run_mas || echo "[$(date '+%Y-%m-%d %H:%M:%S')] MAS failed with status $?"
    else
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] Skipping MAS because classification failed"
    fi
  elif [[ "$MODE" == "mas" ]]; then
    run_mas || echo "[$(date '+%Y-%m-%d %H:%M:%S')] MAS failed with status $?"
  elif [[ "$pipeline_mode" == true ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Skipping MAS because fetch failed"
  fi

  cycle_finished="$(date +%s)"
  elapsed=$((cycle_finished - cycle_started))
  remaining=$((INTERVAL_SECONDS - elapsed))
  if (( remaining > 0 )); then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Cycle took ${elapsed}s; sleeping ${remaining}s"
    sleep "$remaining"
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Cycle took ${elapsed}s; starting next cycle immediately"
  fi
done
