"""Quick standalone test for run_adk_batch_pipeline.
Run: .venv/bin/python tmp/test_batch.py
"""
import os, sys, json

# Decoded credentials
os.environ["OPENAI_API_BASE"] = os.popen("echo aHR0cDovLzEwLjAuMS40Mjo4MzE3L3Yx | base64 -d").read().strip()
os.environ["OPENAI_API_KEY"] = os.popen("echo aHVuZ2J1aS0yNTE2 | base64 -d").read().strip()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.l5_inbox_mas_runner import run_adk_batch_pipeline

dummy_batch = [
    {
        "thread_id": "t_test_123",
        "thread_name": "Test User 1",
        "seeker": {},
        "messages": [
            {"sender": "Customer", "content": "How much does the meditation cost?", "timestamp": "123"}
        ]
    },
    {
        "thread_id": "t_test_789",
        "thread_name": "Test User 2",
        "seeker": {},
        "messages": [
            {"sender": "Customer", "content": "Khoá học bắt đầu khi nào vậy?", "timestamp": "456"}
        ]
    }
]

print("=== Calling ADK batch pipeline with 2 dummy threads ===")
results = run_adk_batch_pipeline(dummy_batch)
print("\n=== Pipeline complete ===")
print(f"Number of results: {len(results)}")
print(json.dumps(results, ensure_ascii=False, indent=2))
