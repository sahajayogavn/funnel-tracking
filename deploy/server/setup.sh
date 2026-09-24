#!/usr/bin/env bash
# Bootstraps the standalone deployment on the 10.0.1.42 server (no root needed):
# Python 3.13 venv (uv), Google Chrome, production web build, systemd --user
# units. Idempotent — re-run after pulling code or changing dependencies.
#
# Usage: deploy/server/setup.sh [--skip-chrome] [--skip-web]
# Universal ID: code:server-deploy-001:setup
set -euo pipefail

PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
export PATH="$HOME/.local/bin:$PATH"
skip_chrome=false
skip_web=false
for arg in "$@"; do
  case "$arg" in
    --skip-chrome) skip_chrome=true ;;
    --skip-web) skip_web=true ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

cd "$PROJECT_ROOT"
mkdir -p logs/services

echo "==> Python venv"
[[ -x .venv/bin/python ]] || uv venv --python 3.13 --seed .venv
uv pip install --python .venv/bin/python -r deploy/server/requirements.lock.txt

if [[ "$skip_chrome" == false ]]; then
  echo "==> Google Chrome"
  deploy/server/install_chrome.sh
fi

if [[ "$skip_web" == false ]]; then
  echo "==> Web build"
  (cd web && npm ci && npm run build)
fi

echo "==> systemd --user units"
unit_dir="$HOME/.config/systemd/user"
mkdir -p "$unit_dir"
for unit in deploy/server/systemd/*.service; do
  sed "s#@PROJECT_ROOT@#$PROJECT_ROOT#g" "$unit" > "$unit_dir/$(basename "$unit")"
done
systemctl --user daemon-reload
echo "Installed: $(ls deploy/server/systemd | tr '\n' ' ')"
echo "Enable with: systemctl --user enable --now <unit>  (see deploy/server/README.md)"
