import sqlite3
from adk_agents.tools.seeker_tools import find_unreplied_threads

conn = sqlite3.connect(':memory:')
conn.row_factory = sqlite3.Row
conn.executescript('''
    CREATE TABLE threads (id TEXT PRIMARY KEY, page_id TEXT, thread_name TEXT, inbox_sort_index INTEGER);
    CREATE TABLE messages (seq INTEGER, thread_id TEXT, sender TEXT, timestamp INTEGER, message_timestamp INTEGER, content TEXT);
    CREATE TABLE telegram_hitl_queue (id INTEGER PRIMARY KEY, route TEXT, thread_id TEXT, payload_json TEXT, status TEXT);
''')
# insert test data with message_timestamp
conn.execute("INSERT INTO threads (id, page_id, thread_name, inbox_sort_index) VALUES ('T1', 'P1', 'T1', 1)")
conn.execute("INSERT INTO messages (seq, thread_id, sender, timestamp, message_timestamp) VALUES (1, 'T1', 'Customer', 100, 100)")
conn.execute("INSERT INTO threads (id, page_id, thread_name, inbox_sort_index) VALUES ('T2', 'P1', 'T2', 2)")
conn.execute("INSERT INTO messages (seq, thread_id, sender, timestamp, message_timestamp) VALUES (1, 'T2', 'Customer', 100, 100)")
conn.execute("INSERT INTO messages (seq, thread_id, sender, timestamp, message_timestamp) VALUES (2, 'T2', 'Page', 101, 101)")
conn.execute("INSERT INTO threads (id, page_id, thread_name, inbox_sort_index) VALUES ('T3', 'P1', 'T3', 3)")
conn.execute("INSERT INTO messages (seq, thread_id, sender, timestamp, message_timestamp) VALUES (5, 'T3', 'Customer', 100, 100)")
conn.execute("INSERT INTO telegram_hitl_queue (route, thread_id, payload_json) VALUES ('inbox', 'T3', '{\"last_message_seq\": 5}')")
conn.execute("INSERT INTO threads (id, page_id, thread_name, inbox_sort_index) VALUES ('T4', 'P1', 'T4', 4)")
conn.execute("INSERT INTO messages (seq, thread_id, sender, timestamp, message_timestamp) VALUES (7, 'T4', 'Customer', 102, 102)")
conn.execute("INSERT INTO telegram_hitl_queue (route, thread_id, payload_json) VALUES ('inbox', 'T4', '{\"last_message_seq\": 5}')")
conn.commit()

import fb_pipeline.persistence.l4_sqlite_store
fb_pipeline.persistence.l4_sqlite_store.get_db_connection = lambda: conn

print(find_unreplied_threads('P1'))
