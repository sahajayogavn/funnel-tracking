import json
import os
import sqlite3
from datetime import datetime

from fb_pipeline.persistence.db import connect as connect_database


CACHE_TTL_SECONDS = 3600


def should_fetch(page_id: str, conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT fetched_at FROM fetch_log WHERE page_id=? ORDER BY id DESC LIMIT 1",
        (page_id,)
    ).fetchone()
    if not row:
        return True
    try:
        last = datetime.fromisoformat(row[0])
        return (datetime.now() - last).total_seconds() > CACHE_TTL_SECONDS
    except Exception:
        return True



def record_fetch(page_id: str, threads_found: int, messages_found: int, conn: sqlite3.Connection):
    conn.execute(
        "INSERT INTO fetch_log (page_id, fetched_at, threads_found, messages_found) VALUES (?, ?, ?, ?)",
        (page_id, datetime.now().isoformat(), threads_found, messages_found)
    )
    conn.commit()



def _ensure_column(cursor: sqlite3.Cursor, table_name: str, column_name: str, column_ddl: str):
    cursor.execute(f"PRAGMA table_info({table_name})")
    cols = [row[1] for row in cursor.fetchall()]
    if column_name not in cols:
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_ddl}")



def _table_exists(cursor: sqlite3.Cursor, table_name: str) -> bool:
    row = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


# code:route-class-reminder-001:migration
def _widen_action_queue_check(cursor: sqlite3.Cursor, logger=None):
    """Rebuild action_queue when its CHECK still rejects the internal decision
    queues (session_proposal, attendance_check). SQLite cannot ALTER a CHECK,
    so the table is copied; indexes are recreated by the caller."""
    if not _table_exists(cursor, "action_queue"):
        return
    row = cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='action_queue'").fetchone()
    if not row or ("session_proposal" in (row[0] or "") and "'deleted'" in (row[0] or "")):
        return
    cursor.execute("DROP INDEX IF EXISTS idx_action_queue_fifo")
    cursor.execute("DROP INDEX IF EXISTS idx_action_queue_one_active_per_target")
    cursor.execute("ALTER TABLE action_queue RENAME TO action_queue_old")
    cursor.execute('''
        CREATE TABLE action_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            queue_type TEXT NOT NULL,
            page_id TEXT,
            target_type TEXT NOT NULL,
            target_id TEXT,
            target_name TEXT,
            action_text TEXT,
            reaction_type TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',
            approval_source TEXT,
            approved_at DATETIME,
            claimed_at DATETIME,
            executed_at DATETIME,
            error_text TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            CHECK (queue_type IN ('reply_message', 'reply_comment', 'proactive_comment', 'proactive_message', 'session_proposal', 'attendance_check')),
            CHECK (status IN ('pending', 'approved', 'executing', 'executed', 'rejected', 'failed', 'deleted'))
        )
    ''')
    cursor.execute('''
        INSERT INTO action_queue (id, queue_type, page_id, target_type, target_id, target_name, action_text,
                                  reaction_type, payload_json, status, approval_source, approved_at, claimed_at,
                                  executed_at, error_text, created_at, updated_at)
        SELECT id, queue_type, page_id, target_type, target_id, target_name, action_text,
               reaction_type, payload_json, status, approval_source, approved_at, claimed_at,
               executed_at, error_text, created_at, updated_at
        FROM action_queue_old
    ''')
    cursor.execute("DROP TABLE action_queue_old")
    if logger:
        logger.info("Migrated action_queue: CHECK now allows session_proposal / attendance_check.")


# code:bug-action-queue-duplicate-proposal-001:migration
def _dedupe_active_action_queue(cursor: sqlite3.Cursor):
    """One-time cleanup, run before creating idx_action_queue_one_active_per_target:
    older DBs may already have more than one live (non-terminal) proposal for the
    same target_id+queue_type+payload.type, since nothing enforced that before this
    unique index existed. For each such group, keep the most authoritative row
    (executing/approved beats pending, ties broken by most recent id) and reject
    the rest as superseded duplicates, so the CREATE UNIQUE INDEX below succeeds."""
    if not _table_exists(cursor, "action_queue"):
        return
    rows = cursor.execute('''
        SELECT id, target_id, queue_type, status,
               COALESCE(json_extract(payload_json, '$.dedupe_key'), json_extract(payload_json, '$.type'), '') AS dedupe_key
        FROM action_queue
        WHERE target_id IS NOT NULL AND status NOT IN ('executed', 'rejected', 'failed')
        ORDER BY target_id, queue_type, dedupe_key,
                 CASE status WHEN 'executing' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
                 id DESC
    ''').fetchall()
    seen = set()
    to_reject = []
    for row in rows:
        key = (row[1], row[2], row[4])
        if key in seen:
            to_reject.append(row[0])
        else:
            seen.add(key)
    for qid in to_reject:
        cursor.execute('''
            UPDATE action_queue SET status='rejected', error_text='superseded_duplicate', updated_at=datetime('now')
            WHERE id=?
        ''', (qid,))


# code:arch-schema-002

def migrate_schema_v2(conn: sqlite3.Connection):
    cursor = conn.cursor()
    alter_specs = {
        "messages": [
            ("seq", "seq INTEGER DEFAULT 0"),
        ],
        "users": [
            ("real_name", "real_name TEXT"),
            ("last_synced_at", "last_synced_at DATETIME"),
            ("temperature", "temperature TEXT DEFAULT 'warm'"),
            ("last_warmup_at", "last_warmup_at DATETIME"),
            ("warmup_count", "warmup_count INTEGER DEFAULT 0"),
            ("cool_step", "cool_step INTEGER DEFAULT 0"),
            ("program_code", "program_code TEXT"),
            ("classification_proof", "classification_proof TEXT"),
            ("classification_verified_at", "classification_verified_at DATETIME"),
        ],
        "auto_replies": [
            ("dry_run", "dry_run BOOLEAN DEFAULT 1"),
            ("customer_message_timestamp", "customer_message_timestamp TEXT"),
        ],
        "comment_users": [
            ("last_synced_at", "last_synced_at DATETIME"),
            ("temperature", "temperature TEXT DEFAULT 'warm'"),
            ("last_warmup_at", "last_warmup_at DATETIME"),
            ("warmup_count", "warmup_count INTEGER DEFAULT 0"),
            ("cool_step", "cool_step INTEGER DEFAULT 0"),
        ],
    }

    for table_name, columns in alter_specs.items():
        if not _table_exists(cursor, table_name):
            continue
        for _column_name, column_ddl in columns:
            try:
                cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_ddl}")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise

    if _table_exists(cursor, "users"):
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_temperature_last_interaction "
            "ON users(temperature, last_interaction)"
        )
    if _table_exists(cursor, "comment_users"):
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_comment_users_temperature_last_interaction "
            "ON comment_users(temperature, last_interaction)"
        )
    conn.commit()



def setup_database(conn: sqlite3.Connection, logger=None):
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS threads (
            id TEXT PRIMARY KEY,
            page_id TEXT,
            thread_name TEXT,
            last_synced_time TEXT,
            inbox_sort_index INTEGER,
            last_message_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # The DOM position is Meta's own inbox ordering.  Keep it separately from
    # sync time and message display labels, both of which are not sortable
    # conversation timestamps.
    _ensure_column(cursor, "threads", "inbox_sort_index", "inbox_sort_index INTEGER")
    _ensure_column(cursor, "threads", "last_message_at", "last_message_at DATETIME")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT,
            sender TEXT,
            content TEXT,
            message_timestamp TEXT,
            seq INTEGER DEFAULT 0,
            -- Source evidence is intentionally nullable: legacy snapshots and
            -- some Inbox DOM variants do not expose Facebook's message id.
            -- Never manufacture an id from body text or the local row id.
            source_id TEXT,
            sender_confidence TEXT DEFAULT 'unknown',
            raw_timestamp TEXT,
            day_context TEXT,
            time_precision TEXT DEFAULT 'unknown',
            reply_to_message_id TEXT,
            quoted_sender TEXT,
            quoted_sender_confidence TEXT DEFAULT 'unknown',
            quoted_text TEXT,
            sender_evidence TEXT,
            quote_evidence TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(thread_id, sender, content, message_timestamp, seq)
        )
    ''')
    try:
        cursor.execute("PRAGMA table_info(messages)")
        cols = [row[1] for row in cursor.fetchall()]
        if 'seq' not in cols:
            cursor.execute("ALTER TABLE messages ADD COLUMN seq INTEGER DEFAULT 0")
        cursor.execute("SELECT sql FROM sqlite_master WHERE name='messages'")
        table_sql = cursor.fetchone()
        if table_sql:
            sql_text = table_sql[0]
            import re as _re
            unique_match = _re.search(r'UNIQUE\s*\(([^)]+)\)', sql_text, _re.IGNORECASE)
            if unique_match:
                unique_cols = unique_match.group(1)
                if 'seq' not in unique_cols:
                    cursor.execute("ALTER TABLE messages RENAME TO messages_old")
                    cursor.execute('''
                        CREATE TABLE messages (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            thread_id TEXT,
                            sender TEXT,
                            content TEXT,
                            message_timestamp TEXT,
                            seq INTEGER DEFAULT 0,
                            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                            UNIQUE(thread_id, sender, content, message_timestamp, seq)
                        )
                    ''')
                    cursor.execute('''
                        INSERT INTO messages (id, thread_id, sender, content, message_timestamp, seq, timestamp)
                        SELECT id, thread_id, sender, content, message_timestamp, COALESCE(seq, 0), timestamp
                        FROM messages_old
                    ''')
                    cursor.execute("DROP TABLE messages_old")
                    if logger:
                        logger.info("Migrated messages table: UNIQUE constraint now includes seq column.")
    except Exception as e:
        if logger:
            logger.debug(f"Messages table migration check: {e}")
    # code:inbox-msg-kind-001 / code:inbox-msg-abs-time-001
    # `kind` separates real turns from Inbox system rows (banners, reactions);
    # `message_at` is the label resolved to an absolute local datetime at
    # scrape time, so ageing decisions never depend on "Mon 11:10 AM".
    _ensure_column(cursor, "messages", "kind", "kind TEXT NOT NULL DEFAULT 'message'")
    _ensure_column(cursor, "messages", "message_at", "message_at DATETIME")
    _ensure_column(cursor, "messages", "message_at_approx", "message_at_approx INTEGER DEFAULT 0")
    # code:message-history-evidence-001
    # These fields preserve what the crawler actually observed.  They are
    # deliberately not backfilled from content/colour/sequence: an unknown
    # source actor or reply target must remain unknown.
    _ensure_column(cursor, "messages", "source_id", "source_id TEXT")
    _ensure_column(cursor, "messages", "sender_confidence", "sender_confidence TEXT DEFAULT 'unknown'")
    _ensure_column(cursor, "messages", "raw_timestamp", "raw_timestamp TEXT")
    _ensure_column(cursor, "messages", "day_context", "day_context TEXT")
    _ensure_column(cursor, "messages", "time_precision", "time_precision TEXT DEFAULT 'unknown'")
    _ensure_column(cursor, "messages", "reply_to_message_id", "reply_to_message_id TEXT")
    _ensure_column(cursor, "messages", "quoted_sender", "quoted_sender TEXT")
    _ensure_column(cursor, "messages", "quoted_sender_confidence", "quoted_sender_confidence TEXT DEFAULT 'unknown'")
    _ensure_column(cursor, "messages", "quoted_text", "quoted_text TEXT")
    _ensure_column(cursor, "messages", "sender_evidence", "sender_evidence TEXT")
    _ensure_column(cursor, "messages", "quote_evidence", "quote_evidence TEXT")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_thread_kind_seq ON messages(thread_id, kind, seq)")
    # Facebook ids are only unique within an Inbox thread for the purposes of
    # this store.  A partial index permits legacy NULL ids while making an
    # observed id an append/upsert identity instead of a body-text heuristic.
    cursor.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_thread_source_id
        ON messages(thread_id, source_id)
        WHERE source_id IS NOT NULL AND source_id != ''
    ''')
    # Crawled reactions are evidence events, not outbound agent action logs in
    # `reactions`.  Keep actor/target/scope as observed; unknown is a value,
    # never an invitation to infer it from the enclosing message sender.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS crawled_message_reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            reaction_key TEXT NOT NULL,
            source_id TEXT,
            actor TEXT NOT NULL DEFAULT 'unknown',
            actor_role TEXT NOT NULL DEFAULT 'unknown',
            emoji TEXT NOT NULL,
            target_type TEXT NOT NULL DEFAULT 'unknown',
            target_message_id TEXT,
            target_scope TEXT NOT NULL DEFAULT 'unknown',
            observed_at TEXT,
            occurred_at TEXT,
            raw_label TEXT,
            evidence TEXT,
            parse_confidence TEXT DEFAULT 'unknown',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(thread_id, reaction_key)
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_crawled_message_reactions_target
        ON crawled_message_reactions(thread_id, target_type, target_message_id)
    ''')
    _ensure_column(cursor, "crawled_message_reactions", "actor_role", "actor_role TEXT DEFAULT 'unknown'")
    _ensure_column(cursor, "crawled_message_reactions", "target_scope", "target_scope TEXT DEFAULT 'unknown'")
    _ensure_column(cursor, "crawled_message_reactions", "evidence", "evidence TEXT")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fetch_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_id TEXT NOT NULL,
            fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            threads_found INTEGER,
            messages_found INTEGER
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT UNIQUE,
            thread_name TEXT,
            real_name TEXT,
            phone TEXT,
            email TEXT,
            fb_url TEXT,
            city TEXT DEFAULT 'Unknown',
            program_code TEXT,
            classification_proof TEXT,
            classification_verified_at DATETIME,
            contact_extracted_at DATETIME,
            lead_stage TEXT DEFAULT 'Intake',
            first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_interaction DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_synced_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Ensure last_synced_at exists for schemas created before it was added to CREATE TABLE.
    # NOTE: SQLite forbids ALTER TABLE ADD COLUMN with non-constant defaults (e.g. CURRENT_TIMESTAMP),
    # so we use a plain DATETIME (NULL default) here for migration compatibility.
    _ensure_column(cursor, "users", "last_synced_at", "last_synced_at DATETIME")
    _ensure_column(cursor, "users", "real_name", "real_name TEXT")
    _ensure_column(cursor, "users", "program_code", "program_code TEXT")
    _ensure_column(cursor, "fetch_log", "qa_status", "qa_status TEXT")
    _ensure_column(cursor, "fetch_log", "qa_report_path", "qa_report_path TEXT")
    _ensure_column(cursor, "users", "classification_proof", "classification_proof TEXT")
    _ensure_column(cursor, "users", "classification_verified_at", "classification_verified_at DATETIME")
    _ensure_column(cursor, "users", "contact_extracted_at", "contact_extracted_at DATETIME")
    _ensure_column(cursor, "users", "temperature", "temperature TEXT DEFAULT 'warm'")
    _ensure_column(cursor, "users", "last_warmup_at", "last_warmup_at DATETIME")
    _ensure_column(cursor, "users", "warmup_count", "warmup_count INTEGER DEFAULT 0")
    _ensure_column(cursor, "users", "cool_step", "cool_step INTEGER DEFAULT 0")
    # code:agent-mas-002:seeker-field-source — tracks who last set a strategic
    # field (city/program_code) so a low-confidence MAS guess can never
    # silently overwrite a value a human operator confirmed.
    _ensure_column(cursor, "users", "city_source", "city_source TEXT DEFAULT 'classifier'")
    _ensure_column(cursor, "users", "program_code_source", "program_code_source TEXT DEFAULT 'classifier'")
    # code:arch-schema-002
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_users_temperature_last_interaction
        ON users(temperature, last_interaction)
    ''')
    # code:agent-mas-002:seeker-field-changes — durable audit trail of every
    # city/program_code/name correction the InboxOrchestrator proposes,
    # whether or not it was auto-applied. Never overwritten, only appended.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS seeker_field_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            field TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT NOT NULL,
            evidence_seq INTEGER,
            reason TEXT,
            source TEXT NOT NULL DEFAULT 'mas',
            confidence REAL,
            status TEXT NOT NULL DEFAULT 'proposed',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_seeker_field_changes_thread
        ON seeker_field_changes(thread_id, field, created_at)
    ''')
    # code:route-class-reminder-001 / code:route-post-session-001
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reminder_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            class_key TEXT NOT NULL,
            session_date TEXT NOT NULL,
            session_proposal_id INTEGER,
            draft_action_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(thread_id, class_key, session_date)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            class_key TEXT NOT NULL,
            session_date TEXT NOT NULL,
            attended INTEGER NOT NULL,
            source TEXT,
            recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(thread_id, class_key, session_date)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sla_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            last_customer_at TEXT,
            alerted_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_ad_ids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT,
            ad_id TEXT,
            first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(thread_id, ad_id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ad_posts (
            ad_id TEXT PRIMARY KEY,
            post_id TEXT,
            ad_content TEXT,
            city TEXT DEFAULT 'Unknown',
            resolved_at DATETIME
        )
    ''')
    # --- MAS Trigger Routes tables ---
    # code:schema-mas-triggers-001
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_type TEXT NOT NULL,
            item_id TEXT NOT NULL,
            reaction_type TEXT NOT NULL,
            agent_name TEXT DEFAULT 'reactor',
            dry_run BOOLEAN DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    try:
        cursor.execute("SELECT sql FROM sqlite_master WHERE name='reactions'")
        table_sql = cursor.fetchone()
        if table_sql and table_sql[0] and 'UNIQUE(item_type,item_id)' in table_sql[0].replace(' ', ''):
            cursor.execute("ALTER TABLE reactions RENAME TO reactions_old")
            cursor.execute('''
                CREATE TABLE reactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_type TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    reaction_type TEXT NOT NULL,
                    agent_name TEXT DEFAULT 'reactor',
                    dry_run BOOLEAN DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                INSERT INTO reactions (id, item_type, item_id, reaction_type, agent_name, dry_run, created_at)
                SELECT id, item_type, item_id, reaction_type, agent_name, COALESCE(dry_run, 1), created_at
                FROM reactions_old
            ''')
            cursor.execute("DROP TABLE reactions_old")
            if logger:
                logger.info("Migrated reactions table: removed global UNIQUE(item_type, item_id) for dry-run-safe logging.")
    except Exception as e:
        if logger:
            logger.debug(f"Reactions table migration check: {e}")
    cursor.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_reactions_live_unique
        ON reactions(item_type, item_id)
        WHERE dry_run = 0
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS warmup_campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            seeker_name TEXT,
            journey_stage TEXT,
            strategy_type TEXT,
            message_text TEXT NOT NULL,
            sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            dry_run BOOLEAN DEFAULT 1,
            response_received BOOLEAN DEFAULT 0,
            response_at DATETIME
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            city TEXT NOT NULL,
            event_date TEXT NOT NULL,
            description TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS event_campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            thread_id TEXT NOT NULL,
            seeker_name TEXT,
            message_text TEXT NOT NULL,
            sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            dry_run BOOLEAN DEFAULT 1,
            FOREIGN KEY (event_id) REFERENCES events(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS auto_replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            reply_text TEXT NOT NULL,
            agent_name TEXT DEFAULT 'responder',
            confidence REAL DEFAULT 1.0,
            escalated BOOLEAN DEFAULT 0,
            dry_run BOOLEAN DEFAULT 1,
            customer_message_timestamp TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    _ensure_column(cursor, "auto_replies", "dry_run", "dry_run BOOLEAN DEFAULT 1")
    _ensure_column(cursor, "auto_replies", "customer_message_timestamp", "customer_message_timestamp TEXT")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mas_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_id TEXT,
            route TEXT NOT NULL,
            subject_type TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            decision TEXT NOT NULL,
            reason TEXT,
            dry_run BOOLEAN DEFAULT 1,
            payload_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_mas_decisions_subject_route_created
        ON mas_decisions(subject_type, subject_id, route, created_at)
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS telegram_hitl_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            route TEXT NOT NULL,
            thread_id TEXT,
            telegram_message_id TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            proposed_text TEXT,
            feedback_text TEXT,
            payload_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # code:agent-mas-002:escalation-taxonomy — set only when the InboxOrchestrator
    # could not converge on a safe reply (contradiction, sensitive topic,
    # adversarial probe, knowledge gap, low-confidence identity change, or the
    # 30-iteration loop budget). NULL means an ordinary reply proposal.
    _ensure_column(cursor, "telegram_hitl_queue", "escalation_reason", "escalation_reason TEXT")
    _ensure_column(cursor, "telegram_hitl_queue", "escalation_note", "escalation_note TEXT")
    # Human-approved outbound work. MAS may only insert a pending item; a WebUI
    # click or an approved Telegram reaction is required before an executor can
    # claim it.  `id` is also the FIFO order within each queue_type.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS action_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            queue_type TEXT NOT NULL,
            page_id TEXT,
            target_type TEXT NOT NULL,
            target_id TEXT,
            target_name TEXT,
            action_text TEXT,
            reaction_type TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',
            approval_source TEXT,
            approved_at DATETIME,
            claimed_at DATETIME,
            executed_at DATETIME,
            error_text TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            CHECK (queue_type IN ('reply_message', 'reply_comment', 'proactive_comment', 'proactive_message', 'session_proposal', 'attendance_check')),
            CHECK (status IN ('pending', 'approved', 'executing', 'executed', 'rejected', 'failed', 'deleted'))
        )
    ''')
    # A scheduler admission is durable work, even when the subsequent LLM call
    # fails.  Without this boundary the 15-minute poll re-buys the same MAS
    # generation until an agent happens to complete.  A later customer message
    # has a higher sequence and is deliberately eligible again.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inbox_mas_processed_messages (
            thread_id TEXT PRIMARY KEY,
            last_message_seq INTEGER NOT NULL,
            processed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Meta's source id, rather than outgoing text, is the durable link from a
    # delivered Facebook message to the MAS proposal that produced it.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mas_message_provenance (
            message_source_id TEXT PRIMARY KEY,
            page_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            action_queue_id INTEGER NOT NULL UNIQUE,
            send_mode TEXT NOT NULL CHECK (send_mode IN ('auto_send', 'human_enter')),
            confirmed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(action_queue_id) REFERENCES action_queue(id)
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_mas_message_provenance_thread
        ON mas_message_provenance(thread_id, message_source_id)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_inbox_mas_processed_message_seq
        ON inbox_mas_processed_messages(thread_id, last_message_seq)
    ''')
    _widen_action_queue_check(cursor, logger)
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_action_queue_fifo
        ON action_queue(queue_type, status, id)
    ''')
    # Upgrade the active-item index in place.  Old automatic proposals use
    # their route type; manual operator commands provide a per-command key.
    cursor.execute('DROP INDEX IF EXISTS idx_action_queue_one_active_per_target')
    _dedupe_active_action_queue(cursor)
    cursor.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_action_queue_one_active_per_target
        ON action_queue(target_id, queue_type, COALESCE(json_extract(payload_json, '$.dedupe_key'), json_extract(payload_json, '$.type'), ''))
        WHERE status NOT IN ('executed', 'rejected', 'failed', 'deleted')
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS telegram_offset (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_update_id INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS llm_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT NOT NULL,
            parent_call_id INTEGER,
            seq_in_trace INTEGER NOT NULL,
            attempt INTEGER DEFAULT 1,
            started_at DATETIME NOT NULL,
            finished_at DATETIME,
            duration_ms INTEGER,
            trigger TEXT NOT NULL,
            route TEXT NOT NULL,
            route_group TEXT NOT NULL,
            agent_name TEXT,
            model TEXT,
            page_id TEXT,
            subject_type TEXT,
            subject_id TEXT,
            subject_label TEXT,
            dry_run BOOLEAN DEFAULT 1,
            system_prompt TEXT,
            messages_json TEXT,
            state_json TEXT,
            tools_json TEXT,
            response_text TEXT,
            response_json TEXT,
            sanitized_text TEXT,
            status TEXT NOT NULL,
            error TEXT,
            tokens_in INTEGER,
            tokens_out INTEGER,
            outcome_type TEXT,
            outcome_ref TEXT
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_llm_calls_started ON llm_calls(started_at DESC, id DESC)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_llm_calls_trace ON llm_calls(trace_id, seq_in_trace)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_llm_calls_subject ON llm_calls(subject_type, subject_id, started_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_llm_calls_route ON llm_calls(route, started_at)')
    migrate_schema_v2(conn)
    conn.commit()


def setup_comment_database(conn: sqlite3.Connection):
    # code:arch-schema-002
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS posts (
            id TEXT PRIMARY KEY,
            page_id TEXT,
            post_name TEXT,
            post_url TEXT,
            last_synced_time TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id TEXT,
            commenter_name TEXT,
            comment_text TEXT,
            comment_timestamp TEXT,
            fb_profile_url TEXT,
            fb_user_id TEXT,
            is_reply INTEGER DEFAULT 0,
            comment_date TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(post_id, commenter_name, comment_text)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS comment_fetch_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_id TEXT NOT NULL,
            fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            posts_found INTEGER,
            comments_found INTEGER
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS comment_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id TEXT,
            commenter_name TEXT,
            fb_user_id TEXT,
            fb_profile_url TEXT,
            phone TEXT,
            email TEXT,
            city TEXT DEFAULT 'Unknown',
            lead_stage TEXT DEFAULT 'Intake',
            first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_interaction DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_synced_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(post_id, commenter_name)
        )
    ''')
    # Use NULL default for last_synced_at in ALTER TABLE (CURRENT_TIMESTAMP not allowed as non-constant)
    _ensure_column(cursor, "comment_users", "last_synced_at", "last_synced_at DATETIME")
    _ensure_column(cursor, "comment_users", "temperature", "temperature TEXT DEFAULT 'warm'")
    _ensure_column(cursor, "comment_users", "last_warmup_at", "last_warmup_at DATETIME")
    _ensure_column(cursor, "comment_users", "warmup_count", "warmup_count INTEGER DEFAULT 0")
    _ensure_column(cursor, "comment_users", "cool_step", "cool_step INTEGER DEFAULT 0")
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_comment_users_temperature_last_interaction
        ON comment_users(temperature, last_interaction)
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS telegram_hitl_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            route TEXT NOT NULL,
            thread_id TEXT,
            telegram_message_id TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            proposed_text TEXT,
            feedback_text TEXT,
            payload_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    _ensure_column(cursor, "telegram_hitl_queue", "escalation_reason", "escalation_reason TEXT")
    _ensure_column(cursor, "telegram_hitl_queue", "escalation_note", "escalation_note TEXT")
    # Comments-only deployments use the same human approval queue.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS action_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            queue_type TEXT NOT NULL,
            page_id TEXT,
            target_type TEXT NOT NULL,
            target_id TEXT,
            target_name TEXT,
            action_text TEXT,
            reaction_type TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',
            approval_source TEXT,
            approved_at DATETIME,
            claimed_at DATETIME,
            executed_at DATETIME,
            error_text TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            CHECK (queue_type IN ('reply_message', 'reply_comment', 'proactive_comment', 'proactive_message', 'session_proposal', 'attendance_check')),
            CHECK (status IN ('pending', 'approved', 'executing', 'executed', 'rejected', 'failed', 'deleted'))
        )
    ''')
    _widen_action_queue_check(cursor)
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_action_queue_fifo ON action_queue(queue_type, status, id)')
    # A manual operator command is intentionally a separate proposal stream;
    # automation keeps the stable route type as its dedupe key.
    cursor.execute('DROP INDEX IF EXISTS idx_action_queue_one_active_per_target')
    _dedupe_active_action_queue(cursor)
    cursor.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_action_queue_one_active_per_target
        ON action_queue(target_id, queue_type, COALESCE(json_extract(payload_json, '$.dedupe_key'), json_extract(payload_json, '$.type'), ''))
        WHERE status NOT IN ('executed', 'rejected', 'failed', 'deleted')
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS telegram_offset (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_update_id INTEGER DEFAULT 0
        )
    ''')
    migrate_schema_v2(conn)
    conn.commit()


def should_fetch_comments(page_id: str, conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT fetched_at FROM comment_fetch_log WHERE page_id=? ORDER BY id DESC LIMIT 1",
        (page_id,)
    ).fetchone()
    if not row:
        return True
    try:
        last = datetime.fromisoformat(row[0])
        return (datetime.now() - last).total_seconds() > CACHE_TTL_SECONDS
    except Exception:
        return True


def record_comment_fetch(page_id: str, posts_found: int, comments_found: int, conn: sqlite3.Connection):
    conn.execute(
        "INSERT INTO comment_fetch_log (page_id, fetched_at, posts_found, comments_found) VALUES (?, ?, ?, ?)",
        (page_id, datetime.now().isoformat(), posts_found, comments_found)
    )
    conn.commit()


def get_db_connection(memory_dir: str = None, logger=None) -> sqlite3.Connection:
    """Open the configured backend; SQLite remains the default until cutover."""
    return connect_database(memory_dir, logger, sqlite_connect=sqlite3.connect)


def get_comment_db_connection(memory_dir: str = None) -> sqlite3.Connection:
    """Open the configured backend and ensure the legacy comment schema on SQLite."""
    return connect_database(memory_dir, comment_schema=True, sqlite_connect=sqlite3.connect)


# code:inbox-parallel-fetch-001:psid-hint
def resolve_psid_hint(conn: sqlite3.Connection, page_id: str, thread_name: str) -> str:
    """Resolve a cached PSID hint for a thread name, unique to the page.

    Returns the ``users.fb_url`` value only when exactly one distinct,
    non-empty, numeric ``fb_url`` matches ``(thread_name, page_id)`` -- an
    ambiguous (same-name) or missing match returns "".
    """
    if not thread_name:
        return ""
    rows = conn.execute(
        "SELECT fb_url FROM users WHERE thread_name = ? AND thread_id LIKE ?",
        (thread_name, f"{page_id}_%"),
    ).fetchall()

    distinct_urls = set()
    for row in rows:
        fb_url = row["fb_url"] if isinstance(row, sqlite3.Row) else row[0]
        fb_url = (fb_url or "").strip()
        if fb_url and fb_url.isdigit():
            distinct_urls.add(fb_url)

    if len(distinct_urls) == 1:
        return next(iter(distinct_urls))
    return ""


def log_mas_decision(
    page_id: str | None,
    route: str,
    subject_type: str,
    subject_id: str,
    decision: str,
    reason: str | None = None,
    dry_run: bool = True,
    payload: dict | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict:
    owns_connection = conn is None
    try:
        if conn is None:
            conn = get_db_connection()
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True) if payload is not None else None
        cursor = conn.execute(
            "INSERT INTO mas_decisions (page_id, route, subject_type, subject_id, decision, reason, dry_run, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (page_id, route, subject_type, subject_id, decision, reason, dry_run, payload_json),
        )
        decision_id = cursor.fetchone()[0]
        conn.commit()
        return {"status": "logged", "decision_id": decision_id}
    finally:
        if owns_connection and conn is not None:
            conn.close()


# code:agent-mas-002:seeker-field-changes
_SEEKER_MUTABLE_FIELDS = {"city": "city_source", "program_code": "program_code_source", "thread_name": None}


def get_user_row(thread_id: str, conn: sqlite3.Connection | None = None) -> dict | None:
    """Return the full users row for one thread, or None if unknown."""
    owns_connection = conn is None
    try:
        if conn is None:
            conn = get_db_connection()
        row = conn.execute("SELECT * FROM users WHERE thread_id = ?", (thread_id,)).fetchone()
        return dict(row) if row else None
    finally:
        if owns_connection and conn is not None:
            conn.close()


def record_seeker_field_change(
    thread_id: str,
    field: str,
    new_value: str,
    *,
    evidence_seq: int | None = None,
    reason: str | None = None,
    source: str = "mas",
    confidence: float | None = None,
    apply: bool = False,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Record a proposed correction to a strategic seeker field (city, program_code,
    thread_name) and, when `apply` is True, write it to `users` — unless a human
    already set that field, in which case a human correction is the only thing
    allowed to overwrite it (code:agent-mas-002:field-source-guard).

    Every call — applied or not — leaves a durable row in `seeker_field_changes`
    so a wrong MAS guess is always auditable, never a silent overwrite.
    """
    if field not in _SEEKER_MUTABLE_FIELDS:
        return {"status": "error", "error": f"field '{field}' is not mutable by MAS"}
    owns_connection = conn is None
    try:
        if conn is None:
            conn = get_db_connection()
        current = conn.execute(f"SELECT {field} FROM users WHERE thread_id = ?", (thread_id,)).fetchone()
        old_value = current[0] if current else None
        source_col = _SEEKER_MUTABLE_FIELDS[field]
        current_source = None
        if source_col:
            source_row = conn.execute(f"SELECT {source_col} FROM users WHERE thread_id = ?", (thread_id,)).fetchone()
            current_source = source_row[0] if source_row else None
        blocked_by_human = bool(source_col) and current_source == "human" and source != "human"
        applied = False
        if apply and current is not None and not blocked_by_human and old_value != new_value:
            if source_col:
                conn.execute(
                    f"UPDATE users SET {field} = ?, {source_col} = ?, last_interaction = datetime('now') WHERE thread_id = ?",
                    (new_value, source, thread_id),
                )
            else:
                conn.execute(
                    f"UPDATE users SET {field} = ?, last_interaction = datetime('now') WHERE thread_id = ?",
                    (new_value, thread_id),
                )
            applied = True
        cursor = conn.execute(
            "INSERT INTO seeker_field_changes "
            "(thread_id, field, old_value, new_value, evidence_seq, reason, source, confidence, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (thread_id, field, old_value, new_value, evidence_seq, reason, source, confidence,
             "applied" if applied else ("blocked_human_owned" if blocked_by_human else "proposed")),
        )
        change_id = cursor.fetchone()[0]
        conn.commit()
        return {
            "status": "applied" if applied else ("blocked_human_owned" if blocked_by_human else "proposed"),
            "change_id": change_id,
            "field": field,
            "old_value": old_value,
            "new_value": new_value,
        }
    finally:
        if owns_connection and conn is not None:
            conn.close()
