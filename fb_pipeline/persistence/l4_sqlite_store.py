import json
import os
import sqlite3
from datetime import datetime


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


# code:arch-schema-002

def migrate_schema_v2(conn: sqlite3.Connection):
    cursor = conn.cursor()
    alter_specs = {
        "messages": [
            ("seq", "seq INTEGER DEFAULT 0"),
        ],
        "users": [
            ("last_synced_at", "last_synced_at DATETIME"),
            ("temperature", "temperature TEXT DEFAULT 'warm'"),
            ("last_warmup_at", "last_warmup_at DATETIME"),
            ("warmup_count", "warmup_count INTEGER DEFAULT 0"),
            ("cool_step", "cool_step INTEGER DEFAULT 0"),
        ],
        "auto_replies": [
            ("dry_run", "dry_run BOOLEAN DEFAULT 1"),
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
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
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
            phone TEXT,
            email TEXT,
            fb_url TEXT,
            city TEXT DEFAULT 'Unknown',
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
    _ensure_column(cursor, "users", "temperature", "temperature TEXT DEFAULT 'warm'")
    _ensure_column(cursor, "users", "last_warmup_at", "last_warmup_at DATETIME")
    _ensure_column(cursor, "users", "warmup_count", "warmup_count INTEGER DEFAULT 0")
    _ensure_column(cursor, "users", "cool_step", "cool_step INTEGER DEFAULT 0")
    # code:arch-schema-002
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_users_temperature_last_interaction
        ON users(temperature, last_interaction)
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
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    _ensure_column(cursor, "auto_replies", "dry_run", "dry_run BOOLEAN DEFAULT 1")
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
    if memory_dir is None:
        memory_dir = os.path.join("memory", "agent_memory")
    os.makedirs(memory_dir, exist_ok=True)
    db_path = os.path.join(memory_dir, "frankensqlite.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    setup_database(conn, logger=logger)
    return conn


def get_comment_db_connection(memory_dir: str = None) -> sqlite3.Connection:
    if memory_dir is None:
        memory_dir = os.path.join("memory", "agent_memory")
    os.makedirs(memory_dir, exist_ok=True)
    db_path = os.path.join(memory_dir, "frankensqlite.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    setup_comment_database(conn)
    return conn


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
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (page_id, route, subject_type, subject_id, decision, reason, dry_run, payload_json),
        )
        conn.commit()
        return {"status": "logged", "decision_id": cursor.lastrowid}
    finally:
        if owns_connection and conn is not None:
            conn.close()
