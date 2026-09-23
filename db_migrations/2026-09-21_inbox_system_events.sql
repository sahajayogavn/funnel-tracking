-- Preserve system events and exact ad/post targets outside conversational messages.
CREATE TABLE IF NOT EXISTS inbox_system_events (
    event_id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    target_url TEXT,
    observed_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inbox_system_events_thread ON inbox_system_events(thread_id);

ALTER TABLE threads ADD COLUMN IF NOT EXISTS fetch_history_complete INTEGER NOT NULL DEFAULT 1;
