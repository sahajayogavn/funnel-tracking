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
│  │   tools/       │──▶│ shared package │──▶│ MAS / auto-reply     │   │
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
7. `tools/l5_inbox_mas_runner.py` and `adk_agents/tools/l5_*` consume the persisted thread data plus MAS handoff context to classify and draft/send replies

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
- `tools/l5_inbox_mas_runner.py` is the canonical operator entry point for automated cycles
- `adk_agents/tools/l5_seeker_tools.py` and `adk_agents/tools/l5_facebook_tools.py` consume persisted thread data and shared helpers to classify, respond, and log auto-replies

This means the MAS layer depends on shared pipeline contracts and stored inbox state, not on raw DOM scraping details.

## Operational Entry Points

### Operator commands

- Inbox fetch/capture: `python tools/fetch_fb_messages.py ...`
- Comment fetch/capture: `python tools/fetch_comments.py ...`
- Automated inbox cycle: `python tools/inbox_mas_runner.py --page-id <asset_id> --once`
- Live auto-reply mode: `python tools/inbox_mas_runner.py --page-id <asset_id> --once --live`
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
│   └── user_ad_ids / ad_posts
└── Unified User Identity
    └── Customer Journey: Unknown → Seeker → ... → Sahaja Mahayogi
```

## Seeker Journey Stages

| # | Stage | Description |
| --- | --- | --- |
| 0 | Unknown | Not yet identified |
| 1 | Seeker | First interaction with Page |
| 2 | Public Program Seeker | Attending public meditation programs |
| 3 | 18-Week Seeker | Enrolled in deep learning course |
| 4 | Seed | Foundation of Sahaja Yoga |
| 5 | Sahaja Yogi | Regular practitioner |
| 6 | Dedicated Sahaja Yogi | Fully dedicated |
| 7 | Sahaja Mahayogi | Highest spiritual dedication |

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
