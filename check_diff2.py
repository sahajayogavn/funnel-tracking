import json

with open('tests/hungbui_test_output.json', 'r') as f:
    expected = json.load(f)

with open('tests/e2e_hung_bui_report.json', 'r') as f:
    report = json.load(f)

actual = report['crawl']['messages']

if expected == actual:
    print("NO DIFF")
else:
    print(f"DIFF FOUND: expected {len(expected)} msgs, actual {len(actual)} msgs")
