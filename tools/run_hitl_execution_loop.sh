#!/usr/bin/env bash
# Run the human-approved DM executor. Out-date actions request a targeted
# message refresh; no classification or MAS generation is started here.
#
# Usage:
#   FUNNEL_PAGE_ID=1548373332058326 ./tools/run_hitl_execution_loop.sh dry-run
#   FUNNEL_PAGE_ID=1548373332058326 ./tools/run_hitl_execution_loop.sh live
#   FUNNEL_PAGE_ID=1548373332058326 ./tools/run_hitl_execution_loop.sh live --auto-send
set -u

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(dirname -- "$SCRIPT_DIR")"
PYTHON="$PROJECT_ROOT/.venv/bin/python"
PAGE_ID="${FUNNEL_PAGE_ID:-1548373332058326}"
INTERVAL_SECONDS="${FUNNEL_HITL_INTERVAL_SECONDS:-30}"
MODE="${1:-}"
DELIVERY="${2:---draft-only}"
if [[ "$#" -gt 2 || ( "$DELIVERY" != "--draft-only" && "$DELIVERY" != "--auto-send" ) ]]; then
  echo "Usage: $0 {live|dry-run} [--draft-only|--auto-send]" >&2
  exit 2
fi

if [[ ! -x "$PYTHON" ]]; then
  echo "Python virtual environment not found: $PYTHON" >&2
  exit 1
fi

case "$MODE" in
  live) live_args=(--live) ;;
  dry-run) live_args=() ;;
  *)
    echo "Usage: FUNNEL_PAGE_ID=<page-id> $0 {live|dry-run}" >&2
    exit 2
    ;;
esac

exec "$PYTHON" "$PROJECT_ROOT/tools/l5_hitl_execution.py" \
  --page-id "$PAGE_ID" \
  --interval "$INTERVAL_SECONDS" \
  "$DELIVERY" \
  "${live_args[@]}"
