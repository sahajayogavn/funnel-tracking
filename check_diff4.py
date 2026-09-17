import json
import sqlite3

with open('tests/hungbui_test_output.json', 'r') as f:
    expected = json.load(f)

conn = sqlite3.connect('memory/agent_memory/frankensqlite.db')
conn.row_factory = sqlite3.Row
cur = conn.execute("SELECT sender, content as text, message_timestamp as timestamp FROM messages WHERE thread_id = '1548373332058326_100001005716854' ORDER BY id ASC")
actual = [dict(row) for row in cur.fetchall()]

# Compare the first N messages
N = len(expected)
if expected == actual[:N]:
    print("NO DIFF in the first", N, "messages")
else:
    print("DIFF FOUND in the first", N, "messages")
    for i in range(min(N, len(actual))):
        if expected[i] != actual[i]:
            print(f"Mismatch at {i}:")
            print("Exp:", expected[i])
            print("Act:", actual[i])
