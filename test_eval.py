from tools import l5_inbox_mas_pipeline as runner_mod

formatted_messages = "\n".join([
    f"[{m.get('sender', 'Unknown')}] {m.get('content', '')}"
    for m in [
        {"sender": "Customer", "content": "Xin chào"},
        {"sender": "Page", "content": "Chào bạn"},
    ]
])
print(repr(formatted_messages))
print(repr("[Customer] Xin chào\n[Page] Chào bạn"))
print(formatted_messages == "[Customer] Xin chào\n[Page] Chào bạn")
