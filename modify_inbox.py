import re
with open('tools/l5_inbox_mas_runner.py', 'r') as f:
    content = f.read()

# 1. Remove playwright and CDP connection, and scrape_inbox.
content = re.sub(r'with sync_playwright\(\) as p:.*?try:\s+conn = get_db_connection\(\)', 'try:\n        conn = get_db_connection()', content, flags=re.DOTALL)
# It's better to just replace the whole function

