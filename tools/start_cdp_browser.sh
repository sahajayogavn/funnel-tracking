#!/usr/bin/env bash
# Starts/stops the Chrome profile the inbox/MAS pipeline attaches to over CDP
# (fb_pipeline/session/l2_bootstrap.py connect_over_cdp at :9222), positioned
# off-screen so the window never pops up over the operator's desktop.
#
# Usage: tools/start_cdp_browser.sh {start|run|stop|status} [head|headless]
set -u

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(dirname -- "$SCRIPT_DIR")"

# CHROME_BIN overrides auto-detection. macOS uses the app bundle; Linux (the
# 10.0.1.42 server) prefers the user-level install from
# deploy/server/install_chrome.sh, then any system Chrome/Chromium.
detect_chrome() {
  local candidate
  for candidate in \
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    "$HOME/.local/opt/google-chrome/google-chrome" \
    "$(command -v google-chrome-stable 2>/dev/null)" \
    "$(command -v google-chrome 2>/dev/null)" \
    "$(command -v chromium 2>/dev/null)" \
    "$(command -v chromium-browser 2>/dev/null)"; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}
CHROME_BIN="${CHROME_BIN:-$(detect_chrome || true)}"
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

# Fills CHROME_ARGS; shared by the background (start) and foreground (run) modes.
build_chrome_args() {
  if [[ -z "$CHROME_BIN" || ! -x "$CHROME_BIN" ]]; then
    echo "Google Chrome not found (set CHROME_BIN or run deploy/server/install_chrome.sh): ${CHROME_BIN:-<none>}" >&2
    exit 1
  fi

  CHROME_ARGS=(
    --user-data-dir="$USER_DATA_DIR"
    --profile-directory="$PROFILE_DIRECTORY"
    --remote-debugging-port="$REMOTE_DEBUGGING_PORT"
    --no-first-run
    --no-default-browser-check
  )
  case "$DISPLAY_MODE" in
    head)
      CHROME_ARGS+=(--window-position="$WINDOW_POSITION" --window-size="$WINDOW_SIZE")
      ;;
    headless)
      # Some macOS GPU drivers repeatedly crash in Chrome's headless compositor,
      # leaving CDP unavailable even though the parent process was launched.
      # Inbox scraping uses DOM/CDP only, so software compositing is sufficient.
      CHROME_ARGS+=(--headless=new --disable-gpu --window-size="$WINDOW_SIZE")
      ;;
    *)
      echo "Unknown display mode: $DISPLAY_MODE (expected head|headless)" >&2
      exit 2
      ;;
  esac

  # Linux: Chrome ties cookie encryption to the desktop keyring unless told
  # otherwise, and a systemd --user service may not reach that keyring — the
  # Facebook session would then look logged-out. A basic password store keeps
  # the profile readable from both the desktop and the service. Head mode needs
  # an X display: default to the server's desktop (:0, reachable via RustDesk)
  # so the operator can log in interactively.
  if [[ "$(uname -s)" == "Linux" ]]; then
    CHROME_ARGS+=(--password-store=basic)
    # A user-level Chrome install has no root-owned setuid chrome-sandbox, and
    # Ubuntu's AppArmor blocks the unprivileged user-namespace fallback outside
    # /opt/google/chrome — Chrome then aborts at startup. Only in that case run
    # unsandboxed; fixing chrome-sandbox ownership (see deploy/server/README.md)
    # re-enables the sandbox automatically.
    local sandbox_helper
    sandbox_helper="$(dirname -- "$(readlink -f -- "$CHROME_BIN")")/chrome-sandbox"
    if ! [[ -u "$sandbox_helper" && "$(stat -c %u -- "$sandbox_helper" 2>/dev/null)" == 0 ]] \
      && ! unshare -Ur true 2>/dev/null; then
      echo "WARNING: no usable Chrome sandbox (setuid helper or user namespaces); starting with --no-sandbox." >&2
      CHROME_ARGS+=(--no-sandbox)
    fi
    if [[ "$DISPLAY_MODE" == "head" ]]; then
      export DISPLAY="${DISPLAY:-${CDP_DISPLAY:-:0}}"
    fi
  fi

  mkdir -p "$(dirname -- "$LOG_FILE")"
}

cmd_start() {
  if is_up; then
    echo "CDP browser already running on port $REMOTE_DEBUGGING_PORT."
    exit 0
  fi
  build_chrome_args

  nohup "$CHROME_BIN" "${CHROME_ARGS[@]}" >>"$LOG_FILE" 2>&1 &

  local pid=$!
  disown
  echo "$pid" > "$PID_FILE"

  echo "Started CDP browser (pid $pid), port $REMOTE_DEBUGGING_PORT, mode=$DISPLAY_MODE. Log: $LOG_FILE"
}

# Foreground mode for a process supervisor (deploy/server/systemd): Chrome
# replaces this shell, so the supervisor sees its exit and can restart it.
cmd_run() {
  if is_up; then
    echo "Port $REMOTE_DEBUGGING_PORT is already serving CDP; refusing to start a second browser." >&2
    exit 1
  fi
  build_chrome_args
  echo "Running CDP browser in foreground, port $REMOTE_DEBUGGING_PORT, mode=$DISPLAY_MODE."
  exec "$CHROME_BIN" "${CHROME_ARGS[@]}"
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

  # Fallback: the pidfile only holds the top-level process, and Chrome/Chromium
  # re-exec into helper processes that don't share it. Match on this
  # profile's --user-data-dir so we only ever kill the CDP-launched instance,
  # never an unrelated Chrome window the operator has open.
  local matches
  matches="$(pgrep -f -- "--user-data-dir=$USER_DATA_DIR" || true)"
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
  run) cmd_run ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  *)
    echo "Usage: $0 {start|run|stop|status} [head|headless]" >&2
    exit 2
    ;;
esac
