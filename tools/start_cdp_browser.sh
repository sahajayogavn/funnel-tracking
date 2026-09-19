#!/usr/bin/env bash
# Starts/stops the Edge profile the inbox/MAS pipeline attaches to over CDP
# (fb_pipeline/session/l2_bootstrap.py connect_over_cdp at :9222), positioned
# off-screen so the window never pops up over the operator's desktop.
#
# Usage: tools/start_cdp_browser.sh {start|stop|status} [head|headless]
set -u

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(dirname -- "$SCRIPT_DIR")"

EDGE_BIN="/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
USER_DATA_DIR="${CDP_USER_DATA_DIR:-$PROJECT_ROOT/data/chrome-profiles}"
PROFILE_DIRECTORY="${CDP_PROFILE_DIRECTORY:-Profile 4}"
REMOTE_DEBUGGING_PORT="${CDP_PORT:-9222}"
# Off-screen, not minimized: a minimized/headless-behind-real-profile window
# can get its rendering throttled by Edge, which breaks CDP scraping. Placing
# it past the right edge of the display keeps it fully "visible" to the
# renderer while staying out of the operator's way.
WINDOW_POSITION="${CDP_WINDOW_POSITION:-3000,3000}"
WINDOW_SIZE="${CDP_WINDOW_SIZE:-1280,900}"
LOG_FILE="${CDP_LOG_FILE:-$PROJECT_ROOT/logs/cdp_browser.log}"
PID_FILE="${CDP_PID_FILE:-$PROJECT_ROOT/logs/cdp_browser.pid}"

MODE="${1:-start}"
# head: real off-screen window (default) — required for interactive login and
# for sites that detect/degrade headless browsers, incl. Facebook/Meta.
# headless: --headless=new, no window at all. Needs USER_DATA_DIR/PROFILE_DIRECTORY
# to already hold a logged-in session, since you can't interact with it to log in.
DISPLAY_MODE="${2:-${CDP_DISPLAY_MODE:-head}}"

is_up() {
  curl -s -o /dev/null -m 2 "http://127.0.0.1:$REMOTE_DEBUGGING_PORT/json/version"
}

cmd_start() {
  if [[ ! -x "$EDGE_BIN" ]]; then
    echo "Microsoft Edge not found at: $EDGE_BIN" >&2
    exit 1
  fi

  if is_up; then
    echo "CDP browser already running on port $REMOTE_DEBUGGING_PORT."
    exit 0
  fi

  local extra_args=()
  case "$DISPLAY_MODE" in
    head)
      extra_args+=(--window-position="$WINDOW_POSITION" --window-size="$WINDOW_SIZE")
      ;;
    headless)
      extra_args+=(--headless=new --window-size="$WINDOW_SIZE")
      ;;
    *)
      echo "Unknown display mode: $DISPLAY_MODE (expected head|headless)" >&2
      exit 2
      ;;
  esac

  mkdir -p "$(dirname -- "$LOG_FILE")"

  nohup "$EDGE_BIN" \
    --user-data-dir="$USER_DATA_DIR" \
    --profile-directory="$PROFILE_DIRECTORY" \
    --remote-debugging-port="$REMOTE_DEBUGGING_PORT" \
    --no-first-run \
    --no-default-browser-check \
    "${extra_args[@]}" \
    >>"$LOG_FILE" 2>&1 &

  local pid=$!
  disown
  echo "$pid" > "$PID_FILE"

  echo "Started CDP browser (pid $pid), port $REMOTE_DEBUGGING_PORT, mode=$DISPLAY_MODE. Log: $LOG_FILE"
}

cmd_stop() {
  local stopped=false

  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid="$(cat "$PID_FILE")"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null
      stopped=true
    fi
    rm -f "$PID_FILE"
  fi

  # Fallback: the pidfile only holds the top-level process, and Edge/Chromium
  # re-exec into helper processes that don't share it. Match on this
  # profile's --user-data-dir so we only ever kill the CDP-launched instance,
  # never an unrelated Edge window the operator has open.
  local matches
  matches="$(pgrep -f "Microsoft Edge.*--user-data-dir=$USER_DATA_DIR" || true)"
  if [[ -n "$matches" ]]; then
    echo "$matches" | xargs kill 2>/dev/null
    stopped=true
  fi

  if [[ "$stopped" == true ]]; then
    echo "Stopped CDP browser on port $REMOTE_DEBUGGING_PORT."
  else
    echo "No CDP browser process found for $USER_DATA_DIR."
  fi
}

cmd_status() {
  if is_up; then
    echo "CDP browser is up on port $REMOTE_DEBUGGING_PORT."
  else
    echo "CDP browser is not responding on port $REMOTE_DEBUGGING_PORT."
  fi
}

case "$MODE" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  *)
    echo "Usage: $0 {start|stop|status}" >&2
    exit 2
    ;;
esac
