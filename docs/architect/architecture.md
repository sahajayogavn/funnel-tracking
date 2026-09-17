# Funnel Tracking Architecture

**Universal ID:** `doc:architecture-001`  
**Consolidates:** `docs/ARCHITECTURE.md` and `docs/architecture-decisions.md`  
**Decision date:** 2026-03-25  
**Review basis:** repository source reviewed 2026-09-14

## Purpose and source-of-truth rule

This is the consolidated architecture reference for the Funnel Tracking monorepo. It retains the substantive platform design, operational model, safety decision, persistence contract, route design, and validation guidance from its two source documents without changing either original. Where the source documents describe a desired state rather than an observed implementation, this document labels it **Target**; the implementation review at the end is authoritative for current status.

The platform has four runtime pillars sharing a SQLite-based CRM/audit store (called “FrankenSQLite” in the codebase):

1. `fb_pipeline/` — reusable Facebook ingestion, normalization, persistence, browser bootstrap, and contracts.
2. `tools/` — L5 operator CLIs and compatibility shims.
3. `adk_agents/` — Google ADK workflows and persisted-inbox/MAS tools.
4. `web/` — Next.js dashboard and CRM over the same store.

```text
Tools/CLI ──┐
fb_pipeline ├──> FrankenSQLite (threads, messages, users, posts, comments,
adk_agents ─┘                    replies, decisions, campaigns) ──> web/
```

## Layered architecture and dependency direction

Canonical files use an L1–L5 prefix. Unprefixed legacy modules remain thin re-export shims for operator and test compatibility.

```text
L5  tools/l5_*.py and adk_agents/tools/l5_*.py
    operator entry points, ADK wrappers, scheduled routes
 ↓
L4  fb_pipeline/persistence/l4_sqlite_store.py
    schema, migrations, database connections, cache and audit helpers
 ↓
L3  fb_pipeline/inbox/l3_pipeline.py
    fb_pipeline/comments/l3_pipeline.py
    fb_pipeline/browser/l3_inbox.py and l3_comments.py
    DOM-backed scraping, normalization, enrichment, persistence payloads
 ↓
L2  fb_pipeline/session/l2_bootstrap.py
    fb_pipeline/browser/l2_actions.py
    CDP attach, authorization, navigation and composer actions
 ↓
L1  fb_pipeline/contracts/l1_{session,inbox,comments}.py
    fb_pipeline/comments/l1_helpers.py
    typed contracts and pure parsing/enrichment helpers
```

Rules:

- Canonical code imports prefixed modules directly; legacy files only re-export them.
- Reusable Facebook logic belongs in `fb_pipeline/`; `tools/` is an operator/compatibility layer, not a second pipeline.
- L5 wrappers consume shared persistence, session, and pipeline helpers rather than recreate scraping internals.
- L4/L3/L2 modules depend only on lower shared layers; L1 is free of Playwright and CLI concerns.
- Tests prove wrapper compatibility and import direction in addition to behavior.

Canonical L5 files include `tools/l5_fetch_fb_messages.py`, `l5_fetch_comments.py`, `l5_inbox_mas_runner.py`, `l5_fb_browser_bootstrap.py`, and ADK `l5_facebook_tools.py` / `l5_seeker_tools.py`. Stable legacy entry points include `tools/fetch_fb_messages.py`, `fetch_comments.py`, `inbox_mas_runner.py`, and `adk_agents/tools/{facebook_tools,seeker_tools}.py`.

## Shared boundaries and data flow

### Browser and ingestion boundaries

- L2 session bootstrap attaches to CDP at `http://127.0.0.1:9222`, verifies Facebook authorization and `asset_id` access, and yields an `AuthorizedSession`.
- L2 browser actions contain `navigate_to_thread(...)`, draft typing, composer clearing, and—currently—an independently exported send-capable commit action.
- L3 inbox scraping uses a two-stage strategy: discover target threads by scrolling the virtualized sidebar within `timerange` / `maxThreads`, reset to the top, then extract only that discovered set. L3 comments scraping parallels this for posts and comments.
- **Target (`prd:inbox-parallel-fetch-001`)**: Stage 2 moves to an orchestrator/worker model — the discovery tab streams `ThreadTask`s into a queue and `--workers N-1` worker tabs extract in parallel. Ingestion internals, locate ladder, concurrency rules and the implementation plan live in [`inbox-fetch-pipeline.md`](inbox-fetch-pipeline.md) (`doc:inbox-fetch-pipeline-001`).
- L3 inbox construction normalizes a `ThreadRecord`, enriches it with user, city, ad, and `MasHandoff` data, then persists `threads`, `messages`, `users`, `user_ad_ids`, and `ad_posts`. The comments path persists `posts`, `comments`, and `comment_users`.

### End-to-end flows

Inbox: operator fetch wrapper → authorized CDP session → `l3_inbox.scrape_inbox_ui(...)` → `build_thread_record(...)` → `enrich_thread_record(...)` → `persist_thread_record(...)` → persisted context supplied to ADK classification/reply drafting.

Comments: operator comments wrapper → authorized CDP session → `l3_comments.scrape_comments_ui(...)` → `build_post_record(...)` → `enrich_post_record(...)` → `persist_post_record(...)`.

The MAS boundary is `fb_pipeline.contracts.l1_inbox.MasHandoff`, constructed by inbox enrichment and consumed by runner/agent logic. MAS depends on normalized stored state, not raw DOM scraping details.

## Inbox safety decision: human-in-the-loop drafting

### Target policy (binding architecture decision)

Inbox MAS is a human-in-the-loop drafting system, not an auto-sending system.

1. Automation may generate a reply and type it into the Facebook composer.
2. Automation must never press Enter, click Send, or invoke an equivalent delivery action for a customer inbox reply.
3. A human must review and manually send the draft.
4. The rule applies to CLIs, polling loops, ADK tools, browser E2E tests, and all runtime modes; `--live` may not opt out.

The intended state machine is `generated` → `sanitized` → `drafted` → `sent`, where automation ends at `drafted` and `sent` is a human-only action outside MAS scope.

### Intended file ownership and behavior

| Boundary | Intended responsibility |
| --- | --- |
| `fb_pipeline/browser/l2_actions.py` | Hard safety boundary: type a draft only; no reusable inbox path may send. |
| `adk_agents/tools/l5_facebook_tools.py` | ADK-facing navigation, draft typing, and draft audit only; use draft wording even when a legacy symbol remains. |
| `tools/l5_inbox_mas_runner.py` | Fetch, select actionable thread, generate, sanitize, navigate, type draft, audit; never final delivery. |
| `adk_agents/tools/l5_seeker_tools.py` | Select a thread only when its latest customer turn lacks a draft/send acknowledgement. |
| `fb_pipeline/persistence/l4_sqlite_store.py` | Additively migrate and retain draft acknowledgement state. |
| legacy inbox/facebook tool modules | Pure compatibility shims; no independent behavioral path. |

Required result categories are `drafted`, `no_reply`, `nav_failed`, and `draft_failed`. Sanitization-empty text must not be typed or create a successful draft acknowledgement.

### Draft acknowledgement persistence contract

`auto_replies` is the audit source of truth. A successful typed draft records `thread_id`, `reply_text`, `agent_name`, `escalated`, draft/not-sent state, and the latest customer-message boundary it answered.

Minimum schema contract:

```sql
CREATE TABLE auto_replies (
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
```

`customer_message_timestamp` is required because it identifies the customer turn acknowledged by a draft. `message_count_snapshot` is an optional defensive addition. A clearer `delivery_state='drafted'` is preferred long-term; `dry_run=1` remains the minimum-change compatibility representation and must not be used as the only suppression predicate.

Actionable-thread query rule:

1. Determine the latest customer message timestamp for a thread.
2. Determine the latest non-null `auto_replies.customer_message_timestamp` acknowledgement.
3. Select the thread only when it has a customer message and the latest customer boundary is newer than (or lacks) an acknowledgement.

| Situation | Expected next-cycle selection |
| --- | --- |
| Customer message, no draft | Yes |
| Draft typed for that message; human has not sent | No |
| Human later sends same draft | No change |
| Customer sends a later message | Yes |
| Sanitization is empty / no text typed | Do not permanently suppress a later actionable turn |

## MAS routes, decision core, and Telegram HITL

`tools/l5_scheduler.py` owns a local scheduled daemon. The documented cadence is inbox/comments fetch, reaction, and inbox reply every 15 minutes; warm-up daily at 09:00; event advertising daily at 10:00. It also polls Telegram and executes approved items. Operator surface:

| Command | Purpose |
| --- | --- |
| `python tools/scheduler.py --page-id <id>` | Run unified daemon in dry-run mode. |
| `python tools/scheduler.py --page-id <id> --live` | Enable supported proactive live delivery; inbox is still target-draft-only. |
| `python tools/scheduler.py --page-id <id> --routes react,warmup` | Select routes. |
| `python tools/inbox_mas_runner.py --page-id <id> --once` | One inbox drafting cycle. |

### Route responsibilities

- **Inbox reply:** persisted thread data + CRM context → `MessageClassifier` → `Responder` → sanitized proposal/draft and audit. Out-of-scope messages are escalated to Telegram without draft typing.
- **React:** `find_unreacted_items()` → ADK `Reactor` where available or heuristic → `log_reaction()`. Live CDP clicking is a future integration target.
- **Warm-up:** dormant seekers → eligibility / strategy → template or `WarmUpComposer` → `warmup_campaigns`; comment-only leads are skipped because no delivery channel exists.
- **Event advertising:** upcoming `events` + city/stage target lookup → template or `EventAdvertiser` → `event_campaigns`.

All routes use Telegram proposal/approval/revision state in `telegram_hitl_queue`. The intended approval behavior for inbox is human review and manual Facebook send; approved warm-up/event proposals may later be delivered by their separately authorized route. The implementation review identifies a current divergence from this rule for inbox.

### Decision core and route arbitration

The production design inserts a decision core between candidate discovery and action logging. It derives a `temperature` snapshot from lead stage/inactivity, observes hard stops, prevents overlapping proactive touches, and writes allow/block outcomes to `mas_decisions`.

- Reactive follow-up beats proactive activity.
- One proactive touch at a time: recent live warm-up, event, or inbox acknowledgement suppresses competing touches for 24 hours.
- Live warm-up is limited to one per thread each seven days.
- `temperature='dormant'` may receive event outreach only after 90 days without a live event campaign.
- `spam`, `unsubscribed`, and operator-marked unsubscribed users are hard stops.

Rollout: add schema/ledger/eligibility checks; keep content generation stable; migrate content selection to ADK agents; only after ledger and QA gates are stable, add real CDP delivery for proactive routes.

## Data model, product concepts, and technology

```text
Page
├── Post → Comment → comment_users CRM
├── Thread → Message → users CRM → user_ad_ids / ad_posts → auto_replies
└── Customer journey: Unknown → Seeker → … → Sahaja Mahayogi
```

`users.last_interaction` and `comment_users.last_interaction` advance only for new customer content; `last_synced_at` advances for scrape synchronization even when content did not change. Extended tables include `reactions`, `warmup_campaigns`, `events`, `event_campaigns`, `mas_decisions`, and Telegram HITL queue/offset state.

Journey values: `User` / legacy `Intake`; `Seeker`; `Seeker_Public_Program` (displayed as Public Program Seeker); `Seeker_18_Weeks` (18-Week Seeker); and `Seed` → `Sahaja_Mahayogi`.

The stack is Python 3.13, Playwright/CDP, Google ADK with LiteLLM-compatible models, SQLite, and Next.js 16 / React 19 / Tailwind. City classification prioritizes customer messages, page replies, then ad content, using an LLM with keyword fallback for Hà Nội, TP. Hồ Chí Minh, Đà Nẵng, Huế, Hội An, Nghệ An, Hải Phòng, and Online.

## Scraper resilience policy and recorded retrospectives

Facebook DOM work follows an anti-fragile policy: investigate failures using saved snapshots or incremental verification; prefer structural/ARIA/geometry heuristics over obfuscated CSS; preserve dated inline retrospective comments for defensive fixes; and prove physical scrolling through before/after `scrollTop` state.

Recorded 2026-04-06 lessons:

- Initialize the hovercard/profile URL fallback before any first-thread early return.
- Allow a short polling grace period for React Router to add `selected_item_id` after a click.
- Scope reaction image selection to the message bubble so reaction emoji do not pollute message text.
- Parse historical Vietnamese timestamps exactly, avoid sequential “now” insertion that reverses chronology, trust stable timeline values in the frontend, and recover safely from a stale SQLite WAL/base-file mismatch.

## Validation and QA contract

Architectural gates map crawler integrity (`test_l3_inbox_pipeline.py`), normalization/enrichment (`test_l1_inbox_contracts.py`), persistence (`test_l4_inbox_persistence.py`), MAS handoff/query (`test_l5_inbox_query_actions.py`), agent/HITL (`test_l5_inbox_mas_runner.py`, `test_telegram_hitl.py`), and browser output (`test_l2_inbox_draft_safety.py`, `test_async_inbox_hitl.py`).

Inbox safety acceptance requires: no automated send; a low-level type-only boundary; no live inbox mode; per-customer-turn suppression after drafting; reopening on a new customer turn; sanitation before typing; safe no-reply behavior; and a Hung Bui-only browser E2E target. Negative cases must prove that navigation/type failures do not acknowledge a draft.

Recommended coverage includes wrapper draft-only behavior, schema migration and boundary persistence, acknowledgement-query scenarios, `no_reply` behavior, `--live` compatibility behavior, Hung Bui no-send E2E behavior, and a low-level test that draft typing never presses Enter.

## Implementation review (2026-09-14)

Status definitions: **Implemented** means evidence matches the documented behavior; **Partial** means it exists but does not fully meet the target; **Missing** means no implementation was found; **Unknown** means source inspection cannot establish runtime behavior.

| Area | Status | Evidence and finding |
| --- | --- | --- |
| L1–L5 package/wrapper structure | Implemented | Canonical L1–L5 modules and unprefixed shims exist under `fb_pipeline/`, `tools/`, and `adk_agents/tools/`; import-boundary tests are present in `tests/test_l4_import_boundaries.py` and `tests/test_l5_wrapper_bootstrap_imports.py`. |
| CDP session, DOM scrape, normalized persistence | Implemented | `fb_pipeline/session/l2_bootstrap.py`, `fb_pipeline/browser/l3_inbox.py`, `fb_pipeline/inbox/l3_pipeline.py`, and `fb_pipeline/persistence/l4_sqlite_store.py`. |
| Low-level inbox draft typing | Implemented | `fb_pipeline/browser/l2_actions.py:send_reply_via_cdp` strips carriage returns and uses `Shift+Enter` only for line breaks; `tests/test_l2_inbox_draft_safety.py` asserts no keyboard press for single-line drafts, including `dry_run=False`. |
| ADK wrapper draft-only semantics | Partial | `adk_agents/tools/l5_facebook_tools.py:send_reply_via_cdp` forces `dry_run=True` and documents draft-only behavior, but the same wrapper exports `commit_reply_via_cdp`, a direct send action. |
| Inbox runner `--live` compatibility | Implemented | `tools/l5_inbox_mas_runner.py` accepts `--live` only as an ignored compatibility flag and forces `dry_run=True`; coverage is in `tests/test_l5_inbox_mas_runner.py`. |
| Single-thread draft → audit path | Implemented | `tools/l5_inbox_mas_thread.py:process_single_thread` navigates, drafts with `dry_run=True`, then records `customer_message_timestamp`; its tests cover `no_reply`, drafted, and draft failure behavior. |
| Current batch runner accurately types before calling a result `drafted` | Partial | `tools/l5_inbox_mas_runner.py:run_inbox_cycle` creates a Telegram proposal and writes `auto_replies`, but its batch path does not call `navigate_to_thread` or `send_reply_via_cdp`; therefore its `drafted` result/audit means proposed rather than browser-typed. |
| Customer-turn acknowledgement and reopening query | Implemented | `auto_replies.customer_message_timestamp` is created/migrated in `fb_pipeline/persistence/l4_sqlite_store.py`; `adk_agents/tools/l5_seeker_tools.py:find_unreplied_threads` compares latest message to latest non-null acknowledgement; `tests/test_l5_inbox_query_actions.py` covers suppression/reopening. |
| No automated inbox delivery in every runtime path | Missing | `fb_pipeline/browser/l2_actions.py:commit_reply_via_cdp` presses Enter; `tools/l5_scheduler.py:hitl_execution_job` imports it and invokes it for approved `route='inbox'` proposals. This violates the binding target rule despite the draft-only main runner. |
| Telegram HITL queue/polling/revision mechanism | Implemented | `tools/l5_telegram_hitl.py`, `tools/l5_scheduler.py`, and `tests/test_telegram_hitl.py` implement proposal persistence, approval/rejection, and regeneration. Whether the real Telegram credentials/service are configured is unknown. |
| Reaction/warm-up/event route orchestration and decision ledger | Implemented | `tools/l5_scheduler_routes.py`, `tools/l5_scheduler_core.py`, `adk_agents/tools/l5_{reaction,warmup,event}_tools.py`, and `log_mas_decision` in `l4_sqlite_store.py`; targeted tests cover route behavior. |
| Live external Facebook/Telegram operation | Unknown | Source and mocked tests establish intent and code paths, but cannot prove external credentials, browser authorization, Telegram connectivity, or production scheduler operation. |

### Required remediation to meet the binding inbox policy

1. Remove or strictly prohibit the inbox branch of `commit_reply_via_cdp` in `tools/l5_scheduler.py:hitl_execution_job`; approved inbox proposals must remain for a human to send manually.
2. Prevent the ADK inbox-facing wrapper from exposing a send-capable primitive, or isolate it to explicitly non-inbox proactive workflows with route-level guards.
3. Make `run_inbox_cycle` either invoke the verified type-only drafting path before writing an acknowledgement, or rename its proposal/audit result so it cannot claim a browser draft that was not typed.
4. Add an end-to-end regression that approves an inbox Telegram proposal and proves no Enter/send action occurs.

