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
   - **Iteration Strategy**: When focusing on fetching `l5_fetch_fb_messages.py` correctly, ensure you strictly follow the iteration loop: Review previous log ===> Check integrity ===> Planning to fix ===> Fix ===> Test and log.

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
   - **Thread/Message**: A private DM conversation. Each thread has a `thread_id` and contains messages from the User or the Page. **IMPORTANT: Facebook lazy-loads older messages. When scraping, you MUST scroll UP in the message container to load ALL messages before extraction. Without scroll-up, only the most recent messages are captured.**
   - **User**: A unified identity across channels. The **same user** can message AND comment. Tools must track `fb_user_id` and `fb_profile_url` for cross-channel recognition.
   - **Ad ID Tracking**: Users who interact via ads have `ad_id` labels (e.g., `ad_id.6930299765389`) in their thread detail panel. These must be extracted and stored in `user_ad_ids` for permanent tracking.
   - **Customer Journey**: A user's progression through multiple touch-points (first comment → reply → DM → registration → class attendance). Each touch-point is an opportunity for **personalized action**.
   - **Touch-point**: Every `INSERT` into the database is a potential touch-point. Tools must treat each interaction as data to inform the AI agent's next action (auto-reply, notify Telegram, suggest follow-up).

   **FrankenSQLite Schema Standards:**
   - `posts` table: tracks discovered posts with `page_id`, `post_name`, `post_url`
   - `comments` table: individual comments with `fb_user_id`, `is_reply`, `comment_date`
   - `comment_users` table: CRM data per commenter (phone, email, city, lead stage)
   - `threads` table: DM conversations with `thread_name`, `last_synced_time`
   - `messages` table: individual messages per thread
   - `users` table: CRM data per DM user (phone, email, city, fb_url)
   - `user_ad_ids` table: junction table linking thread_id ↔ ad_id (many-to-many)
   - `ad_posts` table: mapping ad_id → post_id, ad_content, city
   - All tools share the same `frankensqlite.db` in `memory/agent_memory/`

   **Data Flow:**

   ```
   Playwright (CDP/headless) → Scroll & Click → JS extraction → FrankenSQLite → AI Agent Actions
   ```

   **Existing Tools:**
   | Tool | Channel | Entity | Actions |
   |---|---|---|---|
   | `fetch_fb_messages.py` | DM | Threads → Messages → Users → Ad IDs | `fetch_messages`, `get_list_unique_user`, `fetch_message_by_user`, `get_user_ad_ids`, `resolve_ad_posts` |
   | `fetch_comments.py` | Public | Posts → Comments → Comment Users | `fetch_comments`, `get_comments_by_post`, `get_comment_users` |
   | `telegram_send_notify_to_group.py` | Notification | — | Send alerts to Telegram group |

8. **Full-Stack Web Development** (Next.js):
   When building or modifying the web dashboard in `web/`:
   - Follow rules in `.agents/rules/fullstack-rules.md`
   - Never import `better-sqlite3` or `db.ts` from client components — use `types.ts` for types
   - All DB-accessing pages MUST export `dynamic = 'force-dynamic'`
   - Use `react-force-graph-2d` for graph visualization, `@xyflow/react` for journey workflow
   - Tag components with Universal IDs: `// code:web-<entity>-NNN:<component>`
   - Test with `cd web && npm run build` before committing

   ```bash
   # Development server
   cd web && npm run dev

   # Production build
   cd web && npm run build
   ```

9. **Facebook DOM Parsing Resiliency Strategy (Anti-Fragile Scraper)**:
   Since Facebook's DOM changes frequently, resulting in skipped threads, missing messages, and wrong sender attribution, **YOU MUST NOT blind-edit the DOM parser repeatedly**. Follow this strict step-by-step resilient workflow:

   - **Step 1: Snapshot Driven Extraction (Test First)**
     On any parsing failure, immediately save the HTML snapshot of the target container (e.g., `logs/diagnostic/iteration-XXXX/failed_thread_dom.html`).
     **Rule**: NEVER debug DOM parsing by running the full Playwright browser suite repeatedly. Write a local `pytest` test that loads the `failed_thread_dom.html` and parses it offline.
     
   - **Step 2: Ban Obfuscated CSS Classes**
     Never rely on dynamic CSS classes (e.g., `.x1y123z`, `.x8t9aj`). They change per build.
     Instead, use:
     - **ARIA and Roles**: `[role="row"]`, `[aria-label="Messages"]`.
     - **Data Attributes**: `[data-scope]`, `[data-visualcompletion]`.
     - **Structural Selectors**: `nth-child`, direct sibling combinators inside a known stable parent.
     
   - **Step 3: Visual & Content Heuristics for Sender Detection**
     Do not rely on nesting to find senders. Instead, use rendering heuristics:
     - **Background Context**: Sender versus Page usually have different background color inheritances (e.g., `rgb(0, 132, 255)` vs grayscale) or explicit margin placements (left vs. right alignment or `flex-direction: row-reverse`).
     - **Avatar Presence**: Detect the existence of an image tag next to the message block.
     
   - **Step 4: The "Ralph Loop" Self-Healing Fallback**
     Implement a robust `try-except` hierarchy for parsing:
     1. *Fast Path (Primary Locators)*: Use stable ARIA/Role locators.
     2. *Heuristic Path (Regex/Text)*: Search `innerText` or `textContent` structure.
     3. *LLM Fallback (Slow Path)*: If primary and heuristic fail, dump the clean text tree of the DOM block to an LLM (LiteLLM/ADK) constraint output (JSON) to dynamically retrieve `thread_name`, `messages`, and `sender`. Update the logger that a fallback occurred so we know we must update the fast path.
     
   - **Step 5: Mandatory Data Integrity Gate**
     Before persistence into FrankenSQLite, run a validation function:
     - Did we skip a thread? (Compare `len(parsed_threads)` vs `expected_count`).
     - Are any messages completely empty? 
     - If the integrity check fails, throw an explicit `DOMStructureChangedError`, dump the HTML, and halt.

   - **Step 6: Two-Stage Fetching Protocol**
     Never attempt to scrape complex, deeply virtualized Facebook lists (like the Inbox or Comments) in a single pass. A single pass where you hover, scroll, open a thread, extract, and close it will immediately corrupt Facebook's memory-managed DOM structure.
     Always implement an explicit 2-Stage Strategy:
     - **Stage 1 (Discovery)**: Iteratively scroll to the bottom of the container to accumulate the Target Entity Metadata (e.g., Thread Names). Stop strictly based on `time_range` or `max_limit_quota`. Do NOT open or navigate any items.
     - **Stage 2 (Extraction)**: Reset the viewport. Loop sequentially through the immutable list of discovered items from Stage 1, using stable visual identifiers (like `sidebarIdentityKey`) to dynamically re-locate, select, and safely extract the deep payloads.

10. **Mandatory Retrospectives and Anti-Regression Documentation**:
   After resolving a bug or optimizing the pipeline, you must never leave "naked" fixes.
   - **Retrospective Comments**: Above the modified code blocks, add explicit `# Retrospective [Date]` comments explaining *why* the code was structured this way. Detail the exact Facebook UI anomaly, the root cause of the previous failure (e.g., a silent hang, an obfuscated state), and the mechanics of the fix.
   - **Continuous Evolution**: You must actively respect the retrospective. When touching existing code adorned with retrospective comments, do not obliterate the lessons learned. Instead, append new findings or adjust the strategy while maintaining the historical context.
