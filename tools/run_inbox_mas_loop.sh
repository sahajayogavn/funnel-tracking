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
# Total browser tabs: 1 orchestrator + at most 3 worker tabs.
FETCH_WORKERS="${FUNNEL_FETCH_WORKERS:-4}"
FETCH_TIME_RANGE="${FUNNEL_FETCH_TIME_RANGE:-7d}"
FETCH_MAX_THREADS="${FUNNEL_FETCH_MAX_THREADS:-1000}"
FETCH_NO_EARLY_EXIT="${FUNNEL_FETCH_NO_EARLY_EXIT:-0}"
MAS_MAX_THREADS="${FUNNEL_MAS_MAX_THREADS:-5}"
CLASSIFY_WORKERS="${FUNNEL_CLASSIFY_WORKERS:-10}"
MAS_CITY="${FUNNEL_MAS_CITY:-Hà Nội}"
MAS_ALWAYS="${FUNNEL_MAS_ALWAYS:-0}"
FETCH_FORCE_REFRESH="${FUNNEL_FETCH_FORCE_REFRESH:-0}"
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
  # The loop polls for changes, so unchanged threads must remain eligible for
  # Stage 1's preview/cache skip. Use FUNNEL_FETCH_FORCE_REFRESH=1 only for a
  # deliberate full re-scan.
  # Do not expand an empty array while ``set -u`` is active: bash 3.x treats
  # ``${empty_array[@]}`` as an unbound variable. Build argv incrementally so
  # both optional flags can be absent on the normal fetch path.
  set -- "$PYTHON" "$PROJECT_ROOT/tools/l5_fetch_fb_messages.py" \
    --pageId "$PAGE_ID" \
    --credential default \
    --time_range "$FETCH_TIME_RANGE" \
    --cdp
  case "$FETCH_FORCE_REFRESH" in
    1|true|TRUE|yes|YES) set -- "$@" --refresh ;;
  esac
  case "$FETCH_NO_EARLY_EXIT" in
    1|true|TRUE|yes|YES) set -- "$@" --no-early-exit ;;
  esac
  set -- "$@" --workers "$FETCH_WORKERS" --maxThreads "$FETCH_MAX_THREADS"
  "$@"
}

run_mas() {
  "$PYTHON" "$PROJECT_ROOT/tools/l5_inbox_mas_runner.py" \
    --page-id "$PAGE_ID" \
    --once \
    --city "$MAS_CITY" \
    --max-threads "$MAS_MAX_THREADS"
}

# code:route-morning-brief-001:event-trigger
# In pipeline mode the MAS only runs when the fetch that just finished stored new
# messages; polling the LLM every 15 minutes over an unchanged DB was the main
# source of wasted calls. FUNNEL_MAS_ALWAYS=1 restores the old behaviour.
fetch_found_new_messages() {
  case "$MAS_ALWAYS" in 1|true|TRUE|yes|YES) return 0 ;; esac
  local count
  count="$("$PYTHON" - "$PROJECT_ROOT" "$PAGE_ID" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from fb_pipeline.persistence.l4_sqlite_store import get_db_connection
conn = get_db_connection()
row = conn.execute(
    "SELECT COALESCE(messages_found, 0) FROM fetch_log WHERE page_id=? ORDER BY id DESC LIMIT 1",
    (sys.argv[2],),
).fetchone()
print(int(row[0]) if row else 0)
PY
)"
  [[ "${count:-0}" -gt 0 ]]
}

run_classify() {
  "$PYTHON" "$PROJECT_ROOT/tools/l5_fetch_fb_messages.py" \
    --pageId "$PAGE_ID" \
    --action classify_city_llm \
    --workers "$CLASSIFY_WORKERS"
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
      if (( fetch_status == 75 )); then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Facebook temporary-block gate tripped; stopping this loop."
        exit 75
      fi
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
    if (( classify_status != 0 )); then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] Skipping MAS because classification failed"
    elif ! fetch_found_new_messages; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] Skipping MAS: fetch stored no new messages (set FUNNEL_MAS_ALWAYS=1 to override)"
    else
      run_mas || echo "[$(date '+%Y-%m-%d %H:%M:%S')] MAS failed with status $?"
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
