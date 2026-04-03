import logging
from fb_pipeline.persistence.l4_sqlite_store import get_db_connection as shared_get_db_connection

logger = logging.getLogger("fetch_fb_db_queries")

def get_db_connection(memory_dir: str = None):
    return shared_get_db_connection(memory_dir, logger=logger)

# code:tool-fbmessages-002:list-users
def get_list_unique_user(page_id: str, time_range: str = "7d") -> dict:
    """List unique users sorted by last interaction, filtered by time range."""
    range_map = {"1d": "-1 day", "7d": "-7 days", "30d": "-30 days", "90d": "-90 days", "180d": "-180 days", "365d": "-365 days"}
    sql_range = range_map.get(time_range, "-7 days")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute(f'''
        SELECT u.id, u.thread_id, u.thread_name, u.phone, u.email, u.fb_url,
               u.city, u.lead_stage, u.first_seen, u.last_interaction,
               (SELECT COUNT(*) FROM messages m WHERE m.thread_id = u.thread_id) as msg_count
        FROM users u
        JOIN threads t ON u.thread_id = t.id
        WHERE t.page_id = ? AND u.last_interaction >= datetime('now', ?)
        ORDER BY u.last_interaction DESC
    ''', (page_id, sql_range))
    
    rows = cursor.fetchall()
    users = []
    for r in rows:
        users.append({
            "id": r["id"],
            "thread_id": r["thread_id"],
            "name": r["thread_name"],
            "phone": r["phone"],
            "email": r["email"],
            "fb_url": r["fb_url"],
            "city": r["city"],
            "lead_stage": r["lead_stage"],
            "first_seen": r["first_seen"],
            "last_interaction": r["last_interaction"],
            "msg_count": r["msg_count"],
        })
    
    conn.close()
    logger.info(f"get_list_unique_user: Found {len(users)} users for page {page_id} within {time_range}.")
    return {"success": True, "action": "get_list_unique_user", "count": len(users), "users": users}


# code:tool-fbmessages-002:user-messages
def fetch_message_by_user(page_id: str, user_id: str) -> dict:
    """Fetch messages for a user. user_id can be thread_id, phone, or email."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Find the user row
    cursor.execute('''
        SELECT u.* FROM users u
        JOIN threads t ON u.thread_id = t.id
        WHERE t.page_id = ? AND (u.thread_id = ? OR u.phone = ? OR u.email = ?)
    ''', (page_id, user_id, user_id, user_id))
    
    user_row = cursor.fetchone()
    if not user_row:
        conn.close()
        return {"success": False, "error": f"User '{user_id}' not found for page {page_id}."}
    
    thread_id = user_row["thread_id"]
    
    # Fetch all messages for this thread
    cursor.execute('''
        SELECT sender, content, message_timestamp, timestamp
        FROM messages
        WHERE thread_id = ?
        ORDER BY id ASC
    ''', (thread_id,))
    
    messages = []
    for m in cursor.fetchall():
        messages.append({
            "sender": m["sender"],
            "content": m["content"],
            "message_timestamp": m["message_timestamp"],
            "stored_at": m["timestamp"],
        })
    
    # Fetch ad_ids for this user
    cursor.execute('SELECT ad_id FROM user_ad_ids WHERE thread_id = ?', (thread_id,))
    ad_ids = [r["ad_id"] for r in cursor.fetchall()]
    
    user_info = {
        "id": user_row["id"],
        "name": user_row["thread_name"],
        "phone": user_row["phone"],
        "email": user_row["email"],
        "fb_url": user_row["fb_url"],
        "city": user_row["city"],
        "lead_stage": user_row["lead_stage"],
        "ad_ids": ad_ids,
    }
    
    conn.close()
    logger.info(f"fetch_message_by_user: Found {len(messages)} messages for user '{user_id}'.")
    return {"success": True, "action": "fetch_message_by_user", "user": user_info, "messages": messages}


# code:tool-fbmessages-003:get-user-ad-ids
def get_user_ad_ids(page_id: str) -> dict:
    """List all ad_id associations for users on this page."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT u.thread_name, u.city, u.lead_stage, ua.ad_id, ua.first_seen,
               ap.city AS ad_city, ap.ad_content
        FROM user_ad_ids ua
        JOIN users u ON ua.thread_id = u.thread_id
        JOIN threads t ON u.thread_id = t.id
        LEFT JOIN ad_posts ap ON ua.ad_id = ap.ad_id
        WHERE t.page_id = ?
        ORDER BY ua.first_seen DESC
    ''', (page_id,))
    
    rows = cursor.fetchall()
    results = []
    for r in rows:
        results.append({
            "name": r["thread_name"],
            "city": r["city"],
            "lead_stage": r["lead_stage"],
            "ad_id": r["ad_id"],
            "first_seen": r["first_seen"],
            "ad_city": r["ad_city"],
            "ad_content_preview": (r["ad_content"] or "")[:100],
        })
    
    conn.close()
    logger.info(f"get_user_ad_ids: Found {len(results)} ad associations for page {page_id}.")
    return {"success": True, "action": "get_user_ad_ids", "count": len(results), "associations": results}
