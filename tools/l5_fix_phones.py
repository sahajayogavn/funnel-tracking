#!/usr/bin/env python3
"""
Fix Phone Numbers Script
Extracts phone numbers strictly from Customer messages and updates the `users` table.
If a user's phone number was previously extracted from an Admin/Page message, 
it will be set back to NULL if no Customer message contained a valid phone number.
"""

import sqlite3
import re
import os
import logging

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, "memory", "agent_memory", "frankensqlite.db")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("fix_phones")

def fix_database_phones():
    if not os.path.exists(DB_PATH):
        logger.error(f"Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get all users who currently have a phone number assigned
    cursor.execute("SELECT thread_id, thread_name, phone FROM users WHERE phone IS NOT NULL")
    users = cursor.fetchall()
    
    fixed_count = 0
    wiped_count = 0
    kept_count = 0

    for thread_id, thread_name, old_phone in users:
        # Fetch ONLY Customer messages for this thread
        cursor.execute("SELECT content FROM messages WHERE thread_id = ? AND sender = 'Customer'", (thread_id,))
        messages = cursor.fetchall()
        
        customer_texts = [msg[0] for msg in messages if msg[0]]
        all_text = " ".join(customer_texts)
        
        phone_match = re.findall(r'(?:0\d{9,10}|\+84\d{9,10})', all_text)
        new_phone = phone_match[0] if phone_match else None
        
        if new_phone != old_phone:
            if new_phone is None:
                logger.info(f"Wiping wrongly assigned phone '{old_phone}' for {thread_name}")
                cursor.execute("UPDATE users SET phone = NULL WHERE thread_id = ?", (thread_id,))
                wiped_count += 1
            else:
                logger.info(f"Correcting phone for {thread_name}: '{old_phone}' -> '{new_phone}'")
                cursor.execute("UPDATE users SET phone = ? WHERE thread_id = ?", (new_phone, thread_id))
                fixed_count += 1
        else:
            kept_count += 1

    conn.commit()
    conn.close()
    
    logger.info("=== Sanitization Complete ===")
    logger.info(f"Phones Kept (Correct): {kept_count}")
    logger.info(f"Phones Fixed (Updated): {fixed_count}")
    logger.info(f"Phones Wiped (Admin Leak): {wiped_count}")

if __name__ == "__main__":
    fix_database_phones()
