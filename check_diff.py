import json
import sqlite3
import os

with open('tests/hungbui_test_output.json', 'r') as f:
    expected = json.load(f)

conn = sqlite3.connect('memory/agent_memory/frankensqlite.db')
conn.row_factory = sqlite3.Row
cur = conn.execute("SELECT sender, content as text, message_timestamp as timestamp FROM messages WHERE thread_id = '1548373332058326_100001005716854' ORDER BY id ASC")
actual = [dict(row) for row in cur.fetchall()]

if expected == actual:
    print("NO DIFF")
else:
    print(f"DIFF FOUND: expected {len(expected)} msgs, actual {len(actual)} msgs")
    # print diff if any
