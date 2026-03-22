# System Architecture — Funnel Tracking Platform

**Universal ID**: `doc:architecture-001`

## Overview

The platform is a monorepo with four runtime pillars sharing a single FrankenSQLite database:

1. **Pipeline package** (`fb_pipeline/`) — shared Facebook ingestion, normalization, persistence, and session/bootstrap logic
2. **Tools** (`tools/`) — thin CLI wrappers and operator entry points around the shared package
3. **Agent Software** (`adk_agents/`) — Google ADK multi-agent workflows that consume persisted inbox data and MAS handoff context
4. **Web UI** (`web/`) — Next.js dashboard and CRM over the same SQLite store

```text
┌───────────────────────────────────────────────────────────────────────┐
│                         funnel-tracking (Monorepo)                   │
│                                                                       │
│  ┌────────────────┐   ┌────────────────┐   ┌──────────────────────┐   │
│  │   Tools/CLI    │   │  fb_pipeline   │   │     adk_agents       │   │
│  │   tools/       │──▶│ shared package │──▶│ MAS / auto-reply     │   │
│  │ thin wrappers  │   │ session/inbox  │   │ orchestration        │   │
│  │ operator UX    │   │ comments/store │   │                      │   │
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

## Shared Package Boundaries

`fb_pipeline/` is the architectural center of the Facebook ingestion stack.

### `fb_pipeline.contracts`

Shared typed records and pure helpers for normalization and enrichment.

- `contracts/inbox.py`
  - `InboxMessage`
  - `ThreadRecord`
  - `EnrichedThreadRecord`
  - `SeekerInfo`
  - `MasHandoff`
  - helpers such as `parse_page_id`, `extract_user_info`, `parse_ad_ids`, `detect_city`
- `contracts/comments.py`
  - `CommentRecord`
  - `PostRecord`
  - `EnrichedPostRecord`

These modules define the data shapes that flow between scraping, enrichment, persistence, and MAS orchestration.

### `fb_pipeline.session`

Reusable browser bootstrap and authorization checks.

- `session/bootstrap.py`
  - CDP attach on `http://127.0.0.1:9222`
  - Facebook authorization verification
  - target `asset_id` access verification
  - `AuthorizedSession` lifecycle for reused or newly created tabs
  - storage-state sanitization for saved cookies

All Facebook browser entry points should attach through this layer rather than embedding CDP logic in CLI tools.

### `fb_pipeline.inbox`

Private-message pipeline for Facebook inbox threads.

- `build_thread_record(...)`
- `enrich_thread_record(...)`
- `persist_thread_record(...)`
- `scrape_inbox(...)`

This layer turns visible thread rows and scraped message payloads into normalized records, derives seeker/contact context, builds MAS handoff payloads, and persists the final result.

### `fb_pipeline.comments`

Comment-ingestion pipeline with parity to the inbox layering.

- `build_post_record(...)`
- `enrich_post_record(...)`
- `persist_post_record(...)`

Comments now follow the same high-level shape as inbox ingestion:

1. capture visible Facebook unit
2. normalize into a record
3. enrich with contact/city context
4. persist into SQLite tables

Comments do not currently produce a MAS handoff payload; the parity here is on extraction and persistence boundaries.

### `fb_pipeline.persistence`

Shared SQLite access and schema bootstrapping.

- `persistence/sqlite_store.py`
  - `get_db_connection(...)`
  - `setup_database(...)`
  - cache helpers such as `should_fetch(...)` and `record_fetch(...)`

This module owns shared inbox-side schema initialization for `threads`, `messages`, `users`, `user_ad_ids`, `ad_posts`, and fetch-log state.

## Import Direction

The intended dependency direction is:

```text
tools/, adk_agents/, tests/
        ↓
   fb_pipeline.session / inbox / comments / persistence
        ↓
        fb_pipeline.contracts
```

Rules:

- `tools/` should be wrappers and operator-facing CLIs, not the home of reusable ingestion logic.
- `adk_agents/` should consume persisted data and shared DB/session helpers, not reimplement scraping internals.
- shared pipeline modules may depend on `fb_pipeline.contracts`.
- contracts stay free of Playwright and CLI concerns.

## End-to-End Data Flow

### Inbox flow: Facebook ingestion → analysis → persistence → MAS

1. Operator runs a tool wrapper such as `tools/fetch_fb_messages.py` or `tools/inbox_mas_runner.py`
2. Wrapper parses input and opens an authorized browser via `fb_pipeline.session.bootstrap.attach_to_authorized_session(...)`
3. `fb_pipeline.inbox.pipeline.scrape_inbox(...)` scrolls the inbox and captures visible thread/message payloads
4. `build_thread_record(...)` normalizes thread metadata
5. `enrich_thread_record(...)` derives contact info, city, ad IDs, and builds a `MasHandoff`
6. `persist_thread_record(...)` writes `threads`, `messages`, `users`, `user_ad_ids`, and `ad_posts`
7. `tools/inbox_mas_runner.py` and `adk_agents/` consume the persisted thread data plus MAS handoff context to classify and draft/send replies

### Comment flow: Facebook ingestion → enrichment → persistence

1. Operator runs `tools/fetch_comments.py`
2. Wrapper opens an authorized browser through the shared session bootstrap
3. Comment scraping collects visible posts and extracted comment payloads
4. `build_post_record(...)` normalizes post metadata
5. `enrich_post_record(...)` derives commenter contact info and city
6. `persist_post_record(...)` writes `posts`, `comments`, and `comment_users`

The comment path now matches the same layered pattern as inbox ingestion for normalization and persistence, but MAS orchestration is still inbox-driven.

## Tool Wrapper Role

`tools/` remains the operational surface area for humans and scripts.

Current wrappers and operators:

- `tools/fetch_fb_messages.py`
  - inbox fetch, cache checks, credential capture, DB lookup helpers
  - delegates shared pipeline/session/storage logic to `fb_pipeline/`
- `tools/fetch_comments.py`
  - comment fetch and DB lookup helpers
  - delegates record construction/persistence to `fb_pipeline.comments`
- `tools/inbox_mas_runner.py`
  - end-to-end operator command for fetch → unreplied lookup → ADK pipeline → CDP reply
- `tools/dedup_users.py`
  - maintenance utility for de-duplicating users that share the same Facebook profile URL

## MAS Boundary

The MAS boundary is currently defined at the inbox enrichment layer and consumed by the runner/agents.

- `fb_pipeline.contracts.inbox.MasHandoff` is the normalized payload for agent-side processing
- `fb_pipeline.inbox.pipeline.enrich_thread_record(...)` constructs that payload
- `tools/inbox_mas_runner.py` is the operator entry point for automated cycles
- `adk_agents/` consumes persisted thread data and shared DB helpers to classify, respond, and log auto-replies

This means the MAS layer depends on the shared pipeline contracts and stored inbox state, not on raw DOM scraping details.

## Operational Entry Points

### Operator commands

- Inbox fetch/capture: `python tools/fetch_fb_messages.py ...`
- Comment fetch/capture: `python tools/fetch_comments.py ...`
- Automated inbox cycle: `python tools/inbox_mas_runner.py --page-id <asset_id> --once`
- Live auto-reply mode: `python tools/inbox_mas_runner.py --page-id <asset_id> --once --live`
- User dedup maintenance: `python tools/dedup_users.py --dry-run` or `--execute`

### Developer entry points

Primary shared modules:

- `fb_pipeline/contracts/inbox.py`
- `fb_pipeline/contracts/comments.py`
- `fb_pipeline/session/bootstrap.py`
- `fb_pipeline/inbox/pipeline.py`
- `fb_pipeline/comments/pipeline.py`
- `fb_pipeline/persistence/sqlite_store.py`

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
