-- Unresolved observations are not canonical messages and must not feed MAS.
CREATE TABLE IF NOT EXISTS inbox_fetch_observations (
    observation_id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL,
    recipient_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    thread_name TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
