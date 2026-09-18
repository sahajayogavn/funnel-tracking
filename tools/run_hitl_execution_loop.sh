#!/usr/bin/env bash
# Run only the human-approved action executor.  No fetch, classification, or
# MAS proposal generation is started by this command.
#
# Usage:
#   FUNNEL_PAGE_ID=1548373332058326 ./tools/run_hitl_execution_loop.sh dry-run
#   FUNNEL_PAGE_ID=1548373332058326 ./tools/run_hitl_execution_loop.sh live
set -u

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(dirname -- "$SCRIPT_DIR")"
PYTHON="$PROJECT_ROOT/.venv/bin/python"
PAGE_ID="${FUNNEL_PAGE_ID:-1548373332058326}"
INTERVAL_SECONDS="${FUNNEL_HITL_INTERVAL_SECONDS:-30}"
MODE="${1:-}"

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
  "${live_args[@]}"
