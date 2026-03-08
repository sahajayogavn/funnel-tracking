---
description: Workflow to write Python script for Agent Skills tools with isolated Python 3.13 environment
---

# Write Agent Tools Workflow

This workflow provides step-by-step instructions for writing Python scripts for Agent Skills tools, ensuring a correct setup with Python 3.13 using `pyenv`.

1. **Verify Python Environment**:
   Ensure you are in the isolated Python 3.13 environment.

   ```bash
   python --version # Should output Python 3.13.x
   ```

2. **Create New Tool File**:
   Create a new `.py` file inside the `tools/` directory. Be descriptive with the filename.

   ```bash
   touch tools/my_new_tool.py
   ```

3. **Implement the CLI Interface**:
   Use `argparse` to handle arguments so that the script can act as a standalone tool.

   ```python
   import argparse

   def main():
       parser = argparse.ArgumentParser(description="Description of the capability")
       # add arguments here
       args = parser.parse_args()
       # execute logic

   if __name__ == "__main__":
       main()
   ```

4. **Add Universal ID**:
   Include a Universal ID tag in the comments right above your core logic function/class:

   ```python
   # code:tool-name-001:specific-component
   def core_logic():
       pass
   ```

5. **Write Unit Tests**:
   Create a corresponding test file in the `tests/` directory and ensure it passes before finalizing the tool.

   ```bash
   python -m unittest tests/test_my_new_tool.py
   ```

6. **Log Execution & Ralph Loop**:
   Make sure to output logs to `./logs/` and include Universal ID in debug prints. For complex browser automation (e.g., Playwright CDP port 9222), you must store extensive diagnostics to `./logs/diagnostic/iteration-XXXX/` including `consoleLog`, script logs, and `DOM` states. Because we are working dynamically, you have to try multiple times (implement a "ralph loop" retry mechanism) until you can reach the exact target intent.

7. **Social Network Domain Model**:
   Every tool in this project operates within a Facebook social network funnel. You **MUST** understand and respect this hierarchy:

   ```
   Page (page_id)
   ├── Post (post_id) ← ads, video, image, text posts
   │   ├── Comment (by UserID) ← public comment
   │   │   └── Reply (by PageID or UserID) ← nested reply
   │   └── Comment Users (CRM: phone, email, city, fb_user_id, fb_profile_url)
   ├── Thread (thread_id) ← private DM conversation
   │   ├── Message (by UserID or PageID)
   │   └── Users (CRM: phone, email, city, fb_url)
   └── Unified User Identity
       └── Customer Journey → Touch-points → Personalized Actions
   ```

   **Key Concepts:**
   - **Page**: We manage only 1 Facebook Business Page (`page_id`). All data is scoped to this page.
   - **Post**: Any content published by the Page — ads, videos, images, text posts. Identified by `post_id` (from `selected_item_id` in FB inbox URL).
   - **Comment**: A public interaction on a Post. Can be by a **UserID** (seeker) or a reply by the **PageID** (our response). Each comment captures: `commenter_name`, `comment_text`, `fb_user_id`, `fb_profile_url`, `is_reply`, `comment_date`.
   - **Thread/Message**: A private DM conversation. Each thread has a `thread_id` and contains messages from the User or the Page.
   - **User**: A unified identity across channels. The **same user** can message AND comment. Tools must track `fb_user_id` and `fb_profile_url` for cross-channel recognition.
   - **Customer Journey**: A user's progression through multiple touch-points (first comment → reply → DM → registration → class attendance). Each touch-point is an opportunity for **personalized action**.
   - **Touch-point**: Every `INSERT` into the database is a potential touch-point. Tools must treat each interaction as data to inform the AI agent's next action (auto-reply, notify Telegram, suggest follow-up).

   **FrankenSQLite Schema Standards:**
   - `posts` table: tracks discovered posts with `page_id`, `post_name`, `post_url`
   - `comments` table: individual comments with `fb_user_id`, `is_reply`, `comment_date`
   - `comment_users` table: CRM data per commenter (phone, email, city, lead stage)
   - `threads` table: DM conversations with `thread_name`, `last_synced_time`
   - `messages` table: individual messages per thread
   - `users` table: CRM data per DM user (phone, email, city, fb_url)
   - All tools share the same `frankensqlite.db` in `memory/agent_memory/`

   **Data Flow:**

   ```
   Playwright (CDP/headless) → Scroll & Click → JS extraction → FrankenSQLite → AI Agent Actions
   ```

   **Existing Tools:**
   | Tool | Channel | Entity | Actions |
   |---|---|---|---|
   | `fetch_fb_messages.py` | DM | Threads → Messages → Users | `fetch_messages`, `get_list_unique_user`, `fetch_message_by_user` |
   | `fetch_comments.py` | Public | Posts → Comments → Comment Users | `fetch_comments`, `get_comments_by_post`, `get_comment_users` |
   | `telegram_send_notify_to_group.py` | Notification | — | Send alerts to Telegram group |
