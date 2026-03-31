# System Architecture — Funnel Tracking Platform

**Universal ID**: `doc:architecture-001`

## Overview

The platform is a monorepo with four runtime pillars sharing a single FrankenSQLite database:

1. **Pipeline package** (`fb_pipeline/`) — shared Facebook ingestion, normalization, persistence, browser/bootstrap, and contracts
2. **Tools** (`tools/`) — L5 CLI wrappers; canonical implementations live in `l5_*` files while old filenames remain compatibility shims
3. **Agent Software** (`adk_agents/`) — Google ADK multi-agent workflows that consume persisted inbox data and MAS handoff context
4. **Web UI** (`web/`) — Next.js dashboard and CRM over the same SQLite store

```text
┌───────────────────────────────────────────────────────────────────────┐
│                         funnel-tracking (Monorepo)                   │
│                                                                       │
│  ┌────────────────┐   ┌────────────────┐   ┌──────────────────────┐   │
│  │   Tools/CLI    │   │  fb_pipeline   │   │     adk_agents       │   │
│  │   tools/       │──▶│ shared package │──▶│ MAS / inbox drafting │   │
│  │ l5_* + shims   │   │ l1→l4 layers   │   │ orchestration        │   │
│  │ operator UX    │   │ session/store  │   │                      │   │
│  └────────┬───────┘   └────────┬───────┘   └──────────┬───────────┘   │
│           │                    │                      │               │
│           └────────────────────┼──────────────────────┘               │
│                                ▼                                      │
│                    ┌──────────────────────────┐                       │
│                    │ FrankenSQLite            │                       │
│                    │ memory/agent_memory      │                       │
│                    │ threads/messages/users   │                       │
│                    │ posts/comments/comment_* │                       │
│                    │ user_ad_ids/ad_posts     │                       │
│                    └─────────────┬────────────┘                       │
│                                  │                                    │
│                                  ▼                                    │
│                         ┌────────────────┐                            │
│                         │     web/       │                            │
│                         │ dashboard/CRM  │                            │
│                         └────────────────┘                            │
└───────────────────────────────────────────────────────────────────────┘
```

## L1–L5 Hierarchy

The hierarchy now expresses itself directly in the canonical filenames. Prefixed files are the implementation paths; old unprefixed modules remain compatibility shims.

```text
L5  tools/l5_*.py + adk_agents/tools/l5_*.py
    operator entrypoints, ADK wrappers, stable CLI surface via legacy shims
 ↓
L4  fb_pipeline/persistence/l4_sqlite_store.py
    FrankenSQLite schema, cache, DB connections
 ↓
L3  fb_pipeline/inbox/l3_pipeline.py
    fb_pipeline/comments/l3_pipeline.py
    fb_pipeline/browser/l3_inbox.py
    fb_pipeline/browser/l3_comments.py
    parsing, enrichment, and DOM-backed scrape pipelines
 ↓
L2  fb_pipeline/session/l2_bootstrap.py
    fb_pipeline/browser/l2_actions.py
    CDP 9222 attach, authorization, click/navigate/type actions
 ↓
L1  fb_pipeline/contracts/l1_session.py
    fb_pipeline/contracts/l1_inbox.py
    fb_pipeline/contracts/l1_comments.py
    fb_pipeline/comments/l1_helpers.py
    data contracts, pure helpers, and parsing utilities
```

Acceptance intent for the hierarchy:
- prefixed files are the canonical implementation paths
- unprefixed modules remain thin re-export shims for CLI/test compatibility
- information should flow downward through shared boundaries, never sideways through wrapper modules
- reusable Facebook logic belongs in `fb_pipeline/`, while `tools/` remains a thin compatibility and operator layer
- tests should prove both wrapper compatibility and import direction

## Shared Package Boundaries

### L1 — contracts and pure helpers

Shared typed records and pure helpers for normalization and enrichment.

- `fb_pipeline/contracts/l1_session.py`
  - `AuthorizedSession`
  - `FacebookAuthorizationError`
  - `PageAccessError`
  - `CDPConnectionError`
- `fb_pipeline/contracts/l1_inbox.py`
  - `InboxMessage`
  - `ThreadRecord`
  - `EnrichedThreadRecord`
  - `SeekerInfo`
  - `MasHandoff`
  - helpers such as `parse_page_id`, `extract_user_info`, `parse_ad_ids`, `detect_city`
- `fb_pipeline/contracts/l1_comments.py`
  - `CommentRecord`
  - `PostRecord`
  - `EnrichedPostRecord`
- `fb_pipeline/comments/l1_helpers.py`
  - `parse_page_id`
  - `parse_post_id`
  - `extract_user_info`
  - `detect_city`

These modules define the data shapes and pure helper behavior that flow between scraping, enrichment, persistence, and MAS orchestration.

### L2 — browser bootstrap and UI actions

Reusable browser bootstrap, authorization checks, and DOM actions.

- `fb_pipeline/session/l2_bootstrap.py`
  - CDP attach on `http://127.0.0.1:9222`
  - Facebook authorization verification
  - target `asset_id` access verification
  - `AuthorizedSession` lifecycle for reused or newly created tabs
  - storage-state sanitization for saved cookies
- `fb_pipeline/browser/l2_actions.py`
  - `navigate_to_thread(...)`
  - `send_reply_via_cdp(...)`

All Facebook browser entry points should attach through this layer rather than embedding CDP logic in CLI tools.

### L3 — DOM-backed scrape pipelines and normalization

This layer turns visible Facebook DOM state into normalized records and persistence payloads.

- `fb_pipeline/browser/l3_inbox.py`
  - `scrape_inbox_ui(...)`
  - `extract_ad_id_labels(...)`
- `fb_pipeline/browser/l3_comments.py`
  - `scrape_comments_ui(...)`
- `fb_pipeline/inbox/l3_pipeline.py`
  - `build_thread_record(...)`
  - `enrich_thread_record(...)`
  - `persist_thread_record(...)`
  - `scrape_inbox(...)`
- `fb_pipeline/comments/l3_pipeline.py`
  - `build_post_record(...)`
  - `enrich_post_record(...)`
  - `persist_post_record(...)`

This layer builds normalized records, derives seeker/contact context, builds MAS handoff payloads for inbox, and prepares persistence inputs.

### L4 — persistence

Shared SQLite access and schema bootstrapping.

- `fb_pipeline/persistence/l4_sqlite_store.py`
  - `get_db_connection(...)`
  - `get_comment_db_connection(...)`
  - `setup_database(...)`
  - `setup_comment_database(...)`
  - cache helpers such as `should_fetch(...)`, `record_fetch(...)`, `should_fetch_comments(...)`

This module owns shared inbox-side and comments-side schema initialization for `threads`, `messages`, `users`, `user_ad_ids`, `ad_posts`, `posts`, `comments`, `comment_users`, and fetch-log state.

### L5 — wrappers and operator surfaces

Canonical operator-facing wrappers now live in prefixed files:

- `tools/l5_fetch_fb_messages.py`
- `tools/l5_fetch_comments.py`
- `tools/l5_inbox_mas_runner.py`
- `tools/l5_fb_browser_bootstrap.py`
- `adk_agents/tools/l5_facebook_tools.py`
- `adk_agents/tools/l5_seeker_tools.py`

Legacy filenames remain supported as compatibility shims:

- `tools/fetch_fb_messages.py`
- `tools/fetch_comments.py`
- `tools/inbox_mas_runner.py`
- `tools/fb_browser_bootstrap.py`
- `adk_agents/tools/facebook_tools.py`
- `adk_agents/tools/seeker_tools.py`

## Import Direction

The intended dependency direction is:

```text
legacy tools/*.py shims, tests/
          ↓
canonical L5 wrappers
          ↓
L4 / L3 / L2 fb_pipeline modules
          ↓
L1 contracts and helpers
```

Rules:

- canonical code should import prefixed modules directly
- unprefixed shim files should only re-export from canonical modules
- L5 `tools/` should be wrappers and operator-facing CLIs, not the home of reusable ingestion logic
- L5 `adk_agents/` should consume persisted data and shared DB/session helpers, not reimplement scraping internals
- L4/L3/L2 shared pipeline modules may depend on lower `fb_pipeline` layers only
- L1 contracts stay free of Playwright and CLI concerns

## Operational Hierarchy

The runtime hierarchy for inbox automation is:

```text
USER / OPERATOR
    |
    v
+--------------------------------------------------+
| L5 WRAPPERS / OPERATOR ENTRYPOINTS               |
|--------------------------------------------------|
| tools/l5_fetch_fb_messages.py                    |
| tools/l5_inbox_mas_runner.py                     |
| legacy shims: tools/fetch_fb_messages.py         |
|               tools/inbox_mas_runner.py          |
+--------------------------------------------------+
    |
    v
+--------------------------------------------------+
| L2 CDP 9222 / BROWSER BOOTSTRAP                  |
|--------------------------------------------------|
| fb_pipeline/session/l2_bootstrap.py              |
|  - connect_to_cdp_browser()                      |
|  - attach_to_authorized_session()                |
|  - ensure_facebook_authorized()                  |
|  - ensure_page_access()                          |
|                                                  |
| output: AuthorizedSession { browser, context,    |
|                             page }               |
+--------------------------------------------------+
    |
    v
+--------------------------------------------------+
| L2/L3 FACEBOOK DOM HANDLER                       |
|--------------------------------------------------|
| A) Crawl / scrape DOM                            |
|    fb_pipeline/browser/l3_inbox.py               |
|     - scrape_inbox_ui()                          |
|                                                  |
| B) Click / navigate / type into UI               |
|    fb_pipeline/browser/l2_actions.py             |
|    adk_agents/tools/l5_facebook_tools.py         |
|     - navigate_to_thread()                       |
|     - send_reply_via_cdp()                       |
|       dry_run=True => type only, NO Enter/SEND   |
+--------------------------------------------------+
    |
    v
+--------------------------------------------------+
| L1/L3 PARSER / NORMALIZER / ENRICHER             |
|--------------------------------------------------|
| fb_pipeline/inbox/l3_pipeline.py                 |
|  - build_thread_record()                         |
|  - enrich_thread_record()                        |
|                                                  |
| fb_pipeline/contracts/l1_inbox.py                |
|  - extract_user_info()                           |
|  - detect_city()                                 |
|  - parse_ad_ids()                                |
|                                                  |
| output:                                          |
|  - ThreadRecord                                  |
|  - EnrichedThreadRecord                          |
|  - SeekerInfo                                    |
|  - MasHandoff                                    |
+--------------------------------------------------+
    |
    v
+--------------------------------------------------+
| L4 PERSISTENCE / CRM STATE                       |
|--------------------------------------------------|
| fb_pipeline/inbox/l3_pipeline.py                 |
|  - persist_thread_record()                       |
|                                                  |
| fb_pipeline/persistence/l4_sqlite_store.py       |
|  - get_db_connection()                           |
|  - setup_database()                              |
|                                                  |
| writes: threads / messages / users /             |
|         user_ad_ids / ad_posts                   |
+--------------------------------------------------+
    |
    v
+--------------------------------------------------+
| L5 SEEKER LOOKUP / MAS ORCHESTRATION             |
|--------------------------------------------------|
| adk_agents/tools/l5_seeker_tools.py              |
|  - lookup_seeker()                               |
|  - get_thread_messages()                         |
|                                                  |
| tools/l5_inbox_mas_runner.py                     |
|  - run_inbox_cycle()                             |
|                                                  |
| flow:                                            |
|  DB messages + seeker profile -> ADK reply       |
|  -> navigate to thread -> type reply             |
|  -> optionally send                              |
+--------------------------------------------------+
```

In short:

```text
L2 CDP9222 / browser handle
    -> L2/L3 Facebook DOM handler
       -> crawl / click / navigate / type (dry-run = no SEND)
          -> L1/L3 parser for seeker/customer info
             -> L4 persistence / CRM lookup
                -> L5 MAS reply flow
```

## End-to-End Data Flow

### Inbox flow: Facebook ingestion → analysis → persistence → MAS

1. Operator runs a stable command such as `tools/fetch_fb_messages.py` or the canonical wrapper `tools/l5_fetch_fb_messages.py`
2. Wrapper parses input and opens an authorized browser via `fb_pipeline.session.l2_bootstrap.attach_to_authorized_session(...)`
3. `fb_pipeline.browser.l3_inbox.scrape_inbox_ui(...)` scrolls the inbox and captures visible thread/message payloads
4. `fb_pipeline.inbox.l3_pipeline.build_thread_record(...)` normalizes thread metadata
5. `fb_pipeline.inbox.l3_pipeline.enrich_thread_record(...)` derives contact info, city, ad IDs, and builds a `MasHandoff`
6. `fb_pipeline.inbox.l3_pipeline.persist_thread_record(...)` writes `threads`, `messages`, `users`, `user_ad_ids`, and `ad_posts`
7. `tools/l5_inbox_mas_runner.py` and `adk_agents/tools/l5_*` consume the persisted thread data plus MAS handoff context to classify and draft replies for human review

### Comment flow: Facebook ingestion → enrichment → persistence

1. Operator runs `tools/fetch_comments.py` or the canonical wrapper `tools/l5_fetch_comments.py`
2. Wrapper opens an authorized browser through `fb_pipeline.session.l2_bootstrap`
3. `fb_pipeline.browser.l3_comments.scrape_comments_ui(...)` collects visible posts and extracted comment payloads
4. `fb_pipeline.comments.l3_pipeline.build_post_record(...)` normalizes post metadata
5. `fb_pipeline.comments.l3_pipeline.enrich_post_record(...)` derives commenter contact info and city
6. `fb_pipeline.comments.l3_pipeline.persist_post_record(...)` writes `posts`, `comments`, and `comment_users`

The comment path matches the same layered pattern as inbox ingestion for normalization and persistence, but MAS orchestration is still inbox-driven.

## Tool Wrapper Role

`tools/` remains the operational surface area for humans and scripts.

Stable operator commands remain:

- `tools/fetch_fb_messages.py`
- `tools/fetch_comments.py`
- `tools/inbox_mas_runner.py`
- `tools/dedup_users.py`

Canonical implementation files for wrapper logic are now:

- `tools/l5_fetch_fb_messages.py`
- `tools/l5_fetch_comments.py`
- `tools/l5_inbox_mas_runner.py`
- `tools/l5_fb_browser_bootstrap.py`

Reusable Facebook logic should go into `fb_pipeline/`, not the legacy wrapper shims.

## MAS Boundary

The MAS boundary is currently defined at the inbox enrichment layer and consumed by the runner/agents.

- `fb_pipeline.contracts.l1_inbox.MasHandoff` is the normalized payload for agent-side processing
- `fb_pipeline.inbox.l3_pipeline.enrich_thread_record(...)` constructs that payload
- `tools/l5_inbox_mas_runner.py` is the canonical operator entry point for automated draft cycles
- `adk_agents/tools/l5_seeker_tools.py` and `adk_agents/tools/l5_facebook_tools.py` consume persisted thread data and shared helpers to classify, draft, and log draft replies

This means the MAS layer depends on shared pipeline contracts and stored inbox state, not on raw DOM scraping details.

## MAS Trigger Routes

The MAS currently exposes one production-wired inbox ADK flow plus three scheduled route scaffolds in `tools/l5_scheduler.py`.

### Trigger Architecture

```text
┌──────────────────────────────────────────────────────────────────────┐
│  l5_scheduler.py  (unified daemon, runs on local Mac)               │
│                                                                      │
│  every 15 min ──→ fetch inbox + comments (existing L5 wrappers)     │
│  every 15 min ──→ Route 1: reaction heuristic + reaction logging    │
│  every 15 min ──→ Inbox Reply: ADK Classifier → Responder           │
│  every day 9AM ─→ Route 2: strategy/template warm-up logging        │
│  every day 10AM → Route 3: event targeting + notification logging   │
│                                                                      │
│  Telegram HITL  ─→ Listens to Telegram Group for LIKE/REPLY actions │
│                    to approve/rewrite pending MAS outbox items.     │
│                                                                      │
│  Shared state: FrankenSQLite + L5 wrappers + Telegram Pending Queue │
└──────────────────────────────────────────────────────────────────────┘
```

### Telegram Human-in-the-Loop (HITL) Workflow

All MAS execution routes (Inbox, Comment Reply, Warm-up, and Event Advertising) are governed by a mandatory Human-in-the-Loop approval step via Telegram.

- **Configuration**: Uses `SYVN_TELEGRAM_GROUP_ID` (-1003703002550) and `TELEGRAM_BOT_TOKEN` defined in `.env`.
- **Workflow Mechanics**:
  1. **Proposal Phase**: The agent (or scheduler route) drafts a response or formulates an execution plan (e.g., target list and message for warm-up) and sends it to the configured Telegram group.
  2. **Queuing (Non-Blocking)**: The MAS instantly closes the headless browser and persists the AI context (chat history, seeker dict, strategy context) exclusively to the `telegram_hitl_queue` SQLite database. The Python scheduler seamlessly continues its operation unblocked.
  3. **Reaction - LIKE (Approval)**: If an operator leaves a 👍 reaction on the proposal, the background `hitl_execution_job` detects the approval within 30 seconds. It re-launches Playwright, navigates perfectly to the specific Facebook thread, dynamically injects the draft text, and commits the `Enter` action. Finally, it drops a 💯 emoji reaction gracefully back on the exact Telegram Proposal to confirm task completion.
  4. **Reaction - REPLY (Revision)**: If the operator replies text directly to the proposal message with specific feedback, the background orchestrator unpacks the persisted interaction history, injects the operator's reply as system feedback via an LLM instruction, regenerates the draft, and queues a *new* proposal to Telegram.

### Inbox Reply (ADK-backed production flow)

| Aspect | Detail |
| --- | --- |
| Trigger | Every 15 minutes via `run_reply_cycle()` or manually via `tools/l5_inbox_mas_runner.py` |
| Input | Persisted thread messages + seeker CRM context from FrankenSQLite |
| ADK agents | `MessageClassifier` → `Responder` |
| Knowledge source | `load_knowledge_context()` loads `SOUL.md`, `faq.md`, `lop-hoc.md`, `su-kien.md`, `research.md`, and `mas_strategy.md` into session `knowledge_context` |
| Optimization | **Grouped LLM Generation**: Combines multiple pending threads (A, B, C, D) into a single LLM request for rapid, parallelized reasoning. The batched response is then unpacked into individual Telegram HITL messages per thread. |
| Filtering | **Out of MAS Range**: The LLM classifier explicitly detects non-meditation-related chatter (e.g. sales, random spam). If flagged as `[OUT_OF_SCOPE]`, the system completely skips CDP browser drafting and strictly emits an alert to the Telegram HITL queue. |
| Action | AI statically determines the thread context and bundles it directly to the Telegram API. Execution typing deferral shifts 100% to the independent `hitl_execution_job()` async daemon pipeline. |

Pipeline: persisted thread data → `run_adk_pipeline()` session state injection → `MessageClassifier` → `Responder` → `send_proposal_to_telegram()` queued insertion → **Telegram HITL Database Pipeline** → `hitl_execution_job()` Playwright re-hydration → `commit_reply_via_cdp()` firing → Telegram Completion (💯).

### Route 1: React (Reaction to New Messages/Comments)

| Aspect | Detail |
| --- | --- |
| Trigger | Every 15 minutes (post-fetch) |
| Input | Unreacted messages and comments from FrankenSQLite |
| Runtime implementation | Scheduler currently uses `_select_reaction_heuristic()` in `tools/l5_scheduler.py` |
| ADK status | `Reactor` is defined in `adk_agents/agent.py` but is not wired into the scheduler yet |
| Action | Logs reaction decisions to `reactions`; live CDP clicking is still a stub in `apply_reaction_via_cdp()` |
| Dry-run | Default `dry_run=True` — dry-run rows do not suppress later live reactions |

Pipeline: `find_unreacted_items()` → `_select_reaction_heuristic()` → `log_reaction()`

### Route 2: Warm-up (Proactive Dormant Seeker Nurturing)

| Aspect | Detail |
| --- | --- |
| Trigger | Daily at configurable time (default 9:00 AM) |
| Input | Dormant seekers from `users` table (last_interaction > N days) |
| Runtime implementation | Scheduler uses `find_dormant_seekers()`, `was_recently_warmed_up()`, and `select_warmup_strategy()` |
| ADK status | `WarmUpComposer` is defined in `adk_agents/agent.py` but is not wired into the scheduler yet |
| Action | Generates template-based message text and logs the attempt to `warmup_campaigns`; CDP delivery is not wired yet |
| Constraints | Max 1 live warmup per seeker per 7 days; never warmup spam/unsubscribed |

Pipeline: `find_dormant_seekers()` → `was_recently_warmed_up()` → `select_warmup_strategy()` → template message → `log_warmup_campaign()`

### Route 3: Event Advertising (City-Targeted Notifications)

| Aspect | Detail |
| --- | --- |
| Trigger | Daily at configurable time (default 10:00 AM) |
| Input | Upcoming events from `events` plus seekers matched by city |
| Runtime implementation | Scheduler builds a plain text notification inline after querying events/targets |
| ADK status | `EventAdvertiser` is defined in `adk_agents/agent.py` but is not wired into the scheduler yet |
| Action | Logs candidate notifications to `event_campaigns`; CDP sending is not wired yet |
| Strategy | `find_target_seekers_for_event()` normalizes stage aliases and prioritizes `Registered` / `Public Program Seeker` before generic `Seeker` |
| Dry-run | Dry-run campaign rows do not suppress later live event targeting |

Pipeline: `get_upcoming_events()` → `find_target_seekers_for_event()` → inline scheduler template → `log_event_campaign()`

### Extended DB Schema (Trigger Routes)

```sql
-- Route 1: Reaction tracking
CREATE TABLE IF NOT EXISTS reactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_type TEXT NOT NULL,       -- 'message' or 'comment'
    item_id TEXT NOT NULL,         -- thread_id or comment row id
    reaction_type TEXT NOT NULL,   -- 'like', 'love', 'care', 'haha', 'wow', 'sad', 'angry'
    agent_name TEXT DEFAULT 'reactor',
    dry_run BOOLEAN DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_reactions_live_unique
ON reactions(item_type, item_id)
WHERE dry_run = 0;

-- Route 2: Warm-up campaign tracking
CREATE TABLE IF NOT EXISTS warmup_campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    seeker_name TEXT,
    journey_stage TEXT,
    strategy_type TEXT,
    message_text TEXT NOT NULL,
    sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    dry_run BOOLEAN DEFAULT 1,
    response_received BOOLEAN DEFAULT 0,
    response_at DATETIME
);

-- Route 3: Event catalog
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    event_date TEXT NOT NULL,
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Route 3: Event campaign tracking
CREATE TABLE IF NOT EXISTS event_campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    thread_id TEXT NOT NULL,
    seeker_name TEXT,
    message_text TEXT NOT NULL,
    sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    dry_run BOOLEAN DEFAULT 1,
    FOREIGN KEY (event_id) REFERENCES events(id)
);

-- Inbox reply audit tracking
CREATE TABLE IF NOT EXISTS auto_replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    reply_text TEXT NOT NULL,
    agent_name TEXT DEFAULT 'responder',
    confidence REAL DEFAULT 1.0,
    escalated BOOLEAN DEFAULT 0,
    dry_run BOOLEAN DEFAULT 1,
    customer_message_timestamp TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- MAS decision ledger and proactive state
CREATE TABLE IF NOT EXISTS mas_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id TEXT,
    route TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT,
    dry_run BOOLEAN DEFAULT 1,
    payload_json TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE users ADD COLUMN temperature TEXT DEFAULT 'warm';
ALTER TABLE users ADD COLUMN last_warmup_at DATETIME;
ALTER TABLE users ADD COLUMN warmup_count INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN cool_step INTEGER DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_users_temperature_last_interaction
ON users(temperature, last_interaction);

ALTER TABLE comment_users ADD COLUMN temperature TEXT DEFAULT 'warm';
ALTER TABLE comment_users ADD COLUMN last_warmup_at DATETIME;
ALTER TABLE comment_users ADD COLUMN warmup_count INTEGER DEFAULT 0;
ALTER TABLE comment_users ADD COLUMN cool_step INTEGER DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_comment_users_temperature_last_interaction
ON comment_users(temperature, last_interaction);
```

### Production MAS decision core

**Universal ID**: `doc:architecture-001:mas-decision-core-001`

The production rollout keeps the existing scheduler and route tools, but inserts a shared decision core between candidate discovery and action logging.

Decision-core responsibilities:
- derive a working `temperature` snapshot from `lead_stage` + inactivity while preserving hard-stop operator states such as `dormant` and `unsubscribed`
- block proactive execution when a thread already has pending inbox follow-up or a recent live outbound touch
- enforce route arbitration so one seeker does not receive overlapping proactive actions in the same short window
- write every block / allow outcome to `mas_decisions` before or alongside route audit tables

Route arbitration rules for the first production slice:
- **Reactive beats proactive**: if a thread still needs inbox follow-up, block warm-up and event advertising for that thread
- **One proactive touch at a time**: if a live warm-up, event notification, or inbox draft acknowledgement happened in the last 24 hours, suppress new proactive sends
- **Warm-up cadence**: live warm-up remains capped at 1 send per 7 days per thread
- **Dormant quarterly events**: `temperature = 'dormant'` may receive event outreach only when no live event campaign was logged in the last 90 days
- **Hard stops**: `spam`, `unsubscribed`, and operator-marked `temperature = 'unsubscribed'` never receive proactive outreach

Rollout path:
1. add schema + ledger + scheduler-side eligibility checks without changing existing fetch/reply interfaces
2. keep message generation as-is (heuristic/template) while decisioning becomes centralized and auditable
3. later swap route content generation to ADK `Reactor`, `WarmUpComposer`, and `EventAdvertiser`
4. only after ledger + QA gates hold steady, wire real CDP delivery for warm-up / event / reactions

### Operational Surface

| Command | Purpose |
| --- | --- |
| `python tools/scheduler.py --page-id <id>` | Start unified daemon (all routes, dry-run) |
| `python tools/scheduler.py --page-id <id> --live` | Start daemon with proactive live-delivery mode where supported; inbox replies remain draft-only |
| `python tools/scheduler.py --page-id <id> --routes react,warmup` | Enable specific routes only |

## Operational Entry Points

### Operator commands

- Inbox fetch/capture: `python tools/fetch_fb_messages.py ...`
- Comment fetch/capture: `python tools/fetch_comments.py ...`
- Automated inbox draft cycle: `python tools/inbox_mas_runner.py --page-id <asset_id> --once`
- User dedup maintenance: `python tools/dedup_users.py --dry-run` or `--execute`

The legacy command surface remains stable even though the canonical implementation files are now `tools/l5_*.py`.

### Developer entry points

Primary shared modules:

- `fb_pipeline/contracts/l1_session.py`
- `fb_pipeline/contracts/l1_inbox.py`
- `fb_pipeline/contracts/l1_comments.py`
- `fb_pipeline/comments/l1_helpers.py`
- `fb_pipeline/session/l2_bootstrap.py`
- `fb_pipeline/browser/l2_actions.py`
- `fb_pipeline/browser/l3_inbox.py`
- `fb_pipeline/browser/l3_comments.py`
- `fb_pipeline/inbox/l3_pipeline.py`
- `fb_pipeline/comments/l3_pipeline.py`
- `fb_pipeline/persistence/l4_sqlite_store.py`

These are the preferred extension points for future Facebook ingestion work.

## Data Model

```text
Page (page_id)
├── Post (post_id)
│   ├── Comment
│   └── comment_users CRM table
├── Thread (thread_id)
│   ├── Message
│   ├── users CRM table
│   ├── user_ad_ids / ad_posts
│   └── auto_replies audit trail
└── Unified User Identity
    └── Customer Journey: Unknown → Seeker → ... → Sahaja Mahayogi
```

Inbox CRM timing state:
- `users.last_interaction` advances only when a newly inserted customer message is observed
- `users.last_synced_at` advances on scrape sync even when there is no new customer activity

Comment CRM timing state:
- `comment_users.last_interaction` advances only when a newly inserted comment is observed for that commenter
- `comment_users.last_synced_at` advances on scrape sync even when no new comment was inserted

## Seeker Journey Stages

| # | Strategy stage | Runtime / DB values | Description |
| --- | --- | --- | --- |
| 0 | User | `User`, legacy `Intake` | Not yet identified or only minimally known |
| 1 | Follower | `Seeker` | Early engagement with Page or community touch-points |
| 2 | Curious Seeker | `Seeker` | Asking about classes, programs, or next steps |
| 3 | Registered | `Seeker_Public_Program`, normalized display `Public Program Seeker` | Has shared registration details for a class or program |
| 4 | Deep Learner | `Seeker_18_Weeks`, normalized display `18-Week Seeker` | Continuing in longer-form class journey |
| 5 | Sahaja Yogi | `Seed` → `Sahaja_Mahayogi` | Ongoing practitioner path beyond seeker onboarding |

## Tech Stack

| Layer | Technology |
| --- | --- |
| Facebook ingestion | Python 3.13, Playwright, CDP |
| Shared pipeline | `fb_pipeline` package |
| Agent software | Google ADK, LiteLLM-compatible models |
| Database | SQLite (FrankenSQLite) |
| Backend/UI | Next.js 16, React 19, Tailwind |

## Cities

The current keyword-based pipeline recognizes these location buckets:

- Hà Nội
- TP. Hồ Chí Minh
- Đà Nẵng
- Huế
- Hội An
- Nghệ An
- Hải Phòng
- Online
