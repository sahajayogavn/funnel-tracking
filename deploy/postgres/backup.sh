#!/bin/sh
set -eu

base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$base/.env"
mkdir -p "$base/backups"
tmp="$base/backups/.$APP_DB-$(date +%F).sql.gz.tmp"
out="$base/backups/$APP_DB-$(date +%F).sql.gz"

docker exec -e PGPASSWORD="$APP_PASSWORD" postgres-funnel-tracking \
  pg_dump --no-owner --no-privileges -U "$APP_USER" -d "$APP_DB" | gzip -c > "$tmp"
mv "$tmp" "$out"
find "$base/backups" -type f -name '*.sql.gz' -mtime +14 -delete
