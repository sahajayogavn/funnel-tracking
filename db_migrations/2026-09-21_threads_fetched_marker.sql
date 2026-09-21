-- code:inbox-sync-skip-001:schema (PostgreSQL)
-- "Fetched" marker used by Stage 1 to skip threads whose sidebar time token
-- has not moved since the last sync.  Mirrors the SQLite _ensure_column calls
-- in fb_pipeline/persistence/l4_sqlite_store.py.  Nullable, additive, safe to
-- re-run.
ALTER TABLE threads ADD COLUMN IF NOT EXISTS fetched_sidebar_token TEXT;
ALTER TABLE threads ADD COLUMN IF NOT EXISTS fetched_sidebar_kind  TEXT;
-- exact epoch (ms) from the card's <abbr data-utime>; the primary skip signal
ALTER TABLE threads ADD COLUMN IF NOT EXISTS fetched_sidebar_utime_ms BIGINT;
ALTER TABLE threads ADD COLUMN IF NOT EXISTS fetched_preview_norm  TEXT;
ALTER TABLE threads ADD COLUMN IF NOT EXISTS fetched_at            TIMESTAMP WITHOUT TIME ZONE;
