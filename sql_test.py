import sqlite3
import json

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

query = '''
            WITH last AS (
                SELECT thread_id, MAX(seq) AS max_seq FROM messages GROUP BY thread_id
            ),
            lm AS (
                SELECT m.thread_id, m.sender, m.seq, m.message_timestamp, m.timestamp AS recorded_at 
                FROM messages m JOIN last ON last.thread_id=m.thread_id AND last.max_seq=m.seq
            ),
            proposed AS (
                SELECT thread_id, MAX(CAST(json_extract(payload_json,'$.last_message_seq') AS INTEGER)) AS seq
                FROM telegram_hitl_queue WHERE route='inbox' GROUP BY thread_id
            )
            SELECT t.id, t.thread_name
            FROM lm JOIN threads t ON t.id=lm.thread_id
            LEFT JOIN proposed p ON p.thread_id=lm.thread_id
            WHERE t.page_id=? AND lm.sender='Customer' AND (p.seq IS NULL OR p.seq < lm.seq)
            ORDER BY t.inbox_sort_index LIMIT ?;
'''
rows = conn.execute(query, ('P1', 10)).fetchall()
print([dict(r) for r in rows])
