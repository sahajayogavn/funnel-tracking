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
            last_interaction DATETIME DEFAULT CURRENT_TIMESTAMP
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
    conn.commit()


def setup_comment_database(conn: sqlite3.Connection):
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
            UNIQUE(post_id, commenter_name)
        )
    ''')
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
