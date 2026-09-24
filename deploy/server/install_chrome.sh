#!/usr/bin/env bash
# Installs Google Chrome stable into ~/.local/opt/google-chrome without root
# (the 10.0.1.42 server has no sudo). tools/start_cdp_browser.sh picks it up
# automatically. Re-run to upgrade.
#
# Universal ID: code:server-deploy-001:install-chrome
set -euo pipefail

DEST="${CHROME_INSTALL_DIR:-$HOME/.local/opt/google-chrome}"
URL="https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

curl -fsSL -o "$work/chrome.deb" "$URL"
dpkg-deb -x "$work/chrome.deb" "$work/root"

rm -rf "$DEST.new"
mkdir -p "$(dirname -- "$DEST")"
mv "$work/root/opt/google/chrome" "$DEST.new"
rm -rf "$DEST"
mv "$DEST.new" "$DEST"

missing="$(ldd "$DEST/chrome" | grep 'not found' || true)"
if [[ -n "$missing" ]]; then
  echo "Chrome installed but shared libraries are missing:" >&2
  echo "$missing" >&2
  exit 1
fi
"$DEST/google-chrome" --version
