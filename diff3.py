import json

with open('tests/hungbui_test_output.json', 'r') as f:
    expected = json.load(f)

with open('tests/e2e_hung_bui_report.json', 'r') as f:
    report = json.load(f)

actual = report['persist']['mas_handoff']['messages']
# Format: expected has 'text' instead of 'content' and 'timestamp' instead of 'message_timestamp'
actual_formatted = [{"sender": m["sender"], "text": m["content"], "timestamp": m["message_timestamp"]} for m in actual]

if expected == actual_formatted:
    print("NO DIFF")
else:
    print(f"DIFF FOUND: expected {len(expected)} msgs, actual {len(actual)} msgs")
