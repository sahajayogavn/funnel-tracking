# Inbox Fetch Pipeline — Orchestrator / Worker Design

**Universal ID:** `doc:inbox-fetch-pipeline-001`
**Satisfies:** `prd:inbox-parallel-fetch-001` (see [`../PRDs/prd-inbox-parallel-fetch.md`](../PRDs/prd-inbox-parallel-fetch.md))
**Status:** Phases 1–3 implemented (2026-09-16, unit-tested); Phase 4 live validation pending
**Date:** 2026-09-16
**Owner surface:** `tools/l5_fetch_fb_messages.py --action fetch_messages`

This is the canonical design document for the Facebook Business Suite inbox
ingestion path (`fb_pipeline/browser/l3_inbox.py` and its `inbox/` helpers).
It consolidates the current two-stage sequential crawler and specifies the
`--workers N` orchestrator/worker model that replaces sequential Stage 2.
The platform-level view stays in [`architecture.md`](architecture.md); this
document owns the ingestion internals.

---

## 1. As-is: two-stage sequential crawl

```text
 single CDP tab (role: scan_inbox)
 ┌──────────────────────────────────────────────────────────────────────┐
 │ STAGE 1  discover                       STAGE 2  extract             │
 │ ┌────────────────────┐                  ┌──────────────────────────┐ │
 │ │ reset sidebar top  │                  │ reset sidebar top        │ │
 │ │ loop:              │   collected      │ for each collected:      │ │
 │ │   read visible     │──threads[]──────▶│   jump to absoluteTop    │ │
 │ │   parse time token │  (in memory,     │   find card by identity  │ │
 │ │   dedup / cutoff   │   whole list)    │   click, verify switch   │ │
 │ │   scroll + wait    │                  │   scroll up msg panel    │ │
 │ └────────────────────┘                  │   extract, enrich,       │ │
 │        ~3 min / 90d                     │   persist                │ │
 │                                         └──────────────────────────┘ │
 │                                              ~15 s / thread          │
 └──────────────────────────────────────────────────────────────────────┘
```

Measured on the 2026-09-16 `--time_range 90d --refresh` run (page
`1548373332058326`, `logs/fetch_fb_messages.log`):

| Phase | Work | Wall time | Share |
| --- | --- | --- | --- |
| Stage 1 | 75 sidebar scroll rounds, 440 threads listed | 2 min 39 s | ~2 % |
| Stage 2 | 440 sequential click + extract cycles | ≈ 15 s × 440 ≈ 110 min | ~98 % |

Stage 2 is I/O bound on Facebook's SPA (thread switch, message-panel
back-scroll, network waits). The CPU on the host is idle most of the time,
and Stage 2 cannot start until Stage 1 has reached the end of the time range.

### 1.1 Stage 1 contract (unchanged by this design)

- Input: authorized `page` on `inbox/all?asset_id=<page_id>`, `time_range`, `max_threads`.
- Emits, in strict Inbox top-down order, one `ThreadRecord` per conversation
  card whose sidebar time token is inside the range. `dom_index` is the global
  Stage-1 ordinal (persisted as `inbox_sort_index`).
- Stops on: `max_threads`, 4 consecutive out-of-range cards, cache-hit early
  exit (2 consecutive already-synced threads when `allow_early_exit`), 120 s
  without a newly discovered card, or `target_total_messages` already met.
- Identity available at this point: `thread_name`, `preview_text`,
  `sidebar_time_text`, `sidebar_identity_key`, `absoluteTop` (pixel offset in
  the virtualized scroller). `selected_item_id` (the PSID) is **usually empty**
  because unselected cards render `href="#"`.

### 1.1a Stage 1 "already fetched?" skip (code:inbox-sync-skip-001, 2026-09-21)

Problem observed on 2026-09-21: ~90 % of dispatched threads came back with
`messages_added=0` (worker:1 50/56, worker:2 48/53, ≈20 s each). The only
skip signal was a preview-text match against the last 3 stored messages, which
label chips, banners and "You:" prefixes routinely defeated.

Each sidebar card carries the conversation time twice:
`<span class="accessible_elem">Sunday</span>` and
`<abbr class="timestamp" title="Sunday" data-utime="1789896374.244">Sun</abbr>`,
followed by label chips ("Intake", "Qualified", "ad_id…"). The extractor
(`thread_list_parser.extract_visible_threads`) now reads the token **from the
abbr** (`sidebarTimeText="Sun"`, `sidebarTimestampMs`, `sidebarTimeSource="utime"`)
and strips both duplicates from the preview. Without an abbr it falls back to
the old forward scan and flags `sidebarTimeSource="scan"` (untrusted: a date
inside the preview can be picked up).

Every sync — Stage 2 persist **and** a Stage 1 skip — writes the "fetched"
marker on `threads`: `fetched_sidebar_token` (exactly as rendered: a clock
today, a weekday this week, `Aug 6` / `10/3/25` further back),
`fetched_sidebar_kind`, `fetched_sidebar_utime_ms`, `fetched_preview_norm`,
`fetched_at` (VN local; anchors relative tokens). PostgreSQL DDL:
`db_migrations/2026-09-21_threads_fetched_marker.sql`.

Decision (`fb_pipeline/inbox/l3_sync_decision.decide_by_fetched_marker`), in order:

| Condition | Result |
|---|---|
| `--refresh` | fetch (`force_refresh`) |
| no marker on the row | undecided → legacy preview match |
| `--refresh-older-than N` and `fetched_at` older than N days | fetch (`marker_stale`) |
| utime present on both sides | equal → skip (`utime_match`); else fetch (`utime_changed`) |
| `sidebarTimeSource` not `utime`/`tail`, or card newer than its predecessor by > 1 day (`sidebar_time_out_of_order`) | undecided → legacy preview match |
| tokens resolve to the same moment (stored token parsed relative to `fetched_at`, live token relative to now, e.g. `Tue` ↔ `Sep 15`, `8:56 PM` ↔ `Yesterday 8:56 PM`) and that moment is before today | skip (`token_match`) |
| same moment today | skip only if normalized preview unchanged, else fetch (`preview_changed`) |
| different moment | fetch (`token_changed`) |

The "You:/Bạn:" guard is kept for preview-based skips: if the newest stored
row is not from the Page the reply was never persisted → fetch
(`page_reply_missing`). Stage 1 logs one line per card
(`Stage1 '<name>' decision=skip|fetch reason=… token_now=… token_db=…`) and a
summary (`skip_reasons`, `fetch_reasons`, `time_out_of_order`) in
`stats`; the target metric is the share of persisted threads with
`messages_added=0`.

Operator flags: `--refresh` (env `FUNNEL_FETCH_FORCE_REFRESH=1`) re-fetches
every thread in range; `--refresh-older-than DAYS`
(`FUNNEL_FETCH_REFRESH_OLDER_THAN`) re-fetches only threads whose marker is
older than DAYS.

### 1.2 Stage 2 per-thread contract (unchanged semantics, relocated)

1. Locate the card in the sidebar and click it (identity key → PSID → hovercard → name → name+preview).
2. `verify_thread_switch` — confirm URL `selected_item_id` / panel fingerprint / header name.
3. Recompute `thread_id = <page_id>_<sha256(PSID)[:16]>` once the PSID is known.
4. `extract_ad_context`, `scroll_up_message_panel`, `extract_thread_messages`, `validate_thread_integrity`, `extract_ad_id_labels`.
5. `enrich_thread_record` → `persist_thread_record` (writes `threads`, `messages`, `users`, `user_ad_ids`, `ad_posts`).

Everything in step 2–5 is a pure function of (`page`, `ThreadRecord`,
`conn`). It does not depend on Stage 1 state other than the record itself.
That is what makes it movable to another tab.

---

## 2. Problem statement

1. **Latency**: a 90-day refresh takes ~2 hours, during which the operator's
   Chrome is monopolized and any crash (e.g. `TargetClosedError` when the
   tab is closed) discards all Stage 2 work still pending.
2. **Serialization**: Stage 2 waits for the *whole* Stage 1 list even though
   the first threads are known after the first sidebar snapshot (~5 s).
3. **No fault isolation**: one thread that hangs in the 150-attempt click loop
   blocks every thread behind it.

Target from the PRD: `--workers 3` (1 orchestrator + 2 workers) reduces the
Stage 2 wall time by ≥ 3× on a 90d refresh with byte-identical persisted
output, and `--workers 1` keeps today's behaviour exactly.

---

## 3. To-be: orchestrator + worker pool

```text
                    Chrome (CDP :9222), one profile, one Business Suite login
 ┌───────────────────────────────────────────────────────────────────────────────┐
 │  tab role: scan_inbox            tab: scan_inbox_worker:1 … scan_inbox_worker:4│
 │  ┌─────────────────────┐          ┌────────────┐ ┌────────────┐ ┌────────────┐ │
 │  │ ORCHESTRATOR        │          │ WORKER 1   │ │ WORKER 2   │ │ WORKER n   │ │
 │  │ (main thread)       │          │ (thread)   │ │ (thread)   │ │ (thread)   │ │
 │  │                     │ ThreadTask│            │ │            │ │            │ │
 │  │ Stage 1 loop:       │─────────▶│ locate     │ │ locate     │ │ locate     │ │
 │  │  read visible cards │ task_q   │ verify     │ │ verify     │ │ verify     │ │
 │  │  dedup / cutoff     │ (FIFO)   │ extract    │ │ extract    │ │ extract    │ │
 │  │  enqueue IMMEDIATELY│          │ enrich     │ │ enrich     │ │ enrich     │ │
 │  │  scroll + wait      │◀─────────│ persist    │ │ persist    │ │ persist    │ │
 │  │                     │ result_q │            │ │            │ │            │ │
 │  │ after Stage 1:      │          └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ │
 │  │  put N sentinels    │                │              │              │        │
 │  │  join queue as      │                ▼              ▼              ▼        │
 │  │  worker 0 (own tab) │        sqlite conn (WAL, busy_timeout) — one per thread│
 │  │  drain result_q     │                                                       │
 │  │  record_fetch       │                                                       │
 │  │  LLM city classify  │                                                       │
 │  └─────────────────────┘                                                       │
 └───────────────────────────────────────────────────────────────────────────────┘
```

### 3.1 Roles

| Role | Thread | Tab role marker | Responsibility |
| --- | --- | --- | --- |
| Orchestrator | main | `scan_inbox` (existing) | Stage 1 discovery; task dispatch; stop conditions; stats aggregation; `record_fetch`; post-scrape LLM city classification. After Stage 1 finishes it runs the worker loop itself on its own tab ("worker 0") because that tab already has the full sidebar loaded and is otherwise idle. |
| Worker *i* | `threading.Thread` | `scan_inbox_worker:<i>` | Pull `ThreadTask`s FIFO, locate the thread in **its own tab**, run the Stage 2 per-thread contract, persist through its own SQLite connection, push a `ThreadResult`. |

`--workers N` means `N` browser tabs in total: 1 orchestrator + `N-1`
workers. `--workers 1` is the legacy sequential path and must stay
byte-for-byte equivalent (no worker threads, no extra tabs).

### 3.2 Why tabs, not processes

- Role tabs already coexist in production (`scan_inbox`, `scan_comments`,
  `outbound:<id>` in `l5_inbox_mas_runner.py` / `hitl_execution_job.py`),
  so Business Suite tolerates several inbox tabs on one login.
- The Playwright sync API is not thread-safe across threads, but each thread
  may own its own `sync_playwright()` instance and its own
  `connect_over_cdp(...)`. No Playwright object crosses a thread boundary;
  only plain dataclasses travel through the queues.
- Processes would need a second `.venv` bootstrap per worker and cannot share
  the in-memory Stage 1 dedup set; threads are enough because the work is
  network-bound.

### 3.3 Dispatch timing

The orchestrator enqueues every non-skipped thread **at the end of each
visible-cards round**, i.e. before scrolling the sidebar again. Workers
therefore start ~5 s after launch. Short threads finish quickly and the
worker immediately takes the next task, exactly as the PRD requires.

The queue is unbounded: a `ThreadTask` is a few hundred bytes and a 365-day
crawl is a few thousand tasks.

---

## 4. Task and result contracts (L1)

New module `fb_pipeline/contracts/l1_inbox_tasks.py` (`code:inbox-parallel-fetch-001:contracts`).

```python
@dataclass(frozen=True)
class ThreadTask:
    ordinal: int                 # Stage 1 global ordinal → inbox_sort_index
    record: ThreadRecord         # provisional identity from the sidebar card
    absolute_top: float          # scroller offset hint from Stage 1
    psid_hint: str               # "" or a PSID resolved from users.fb_url
    is_new: bool                 # no threads row matched in Stage 1
    attempt: int = 1             # incremented on re-queue after tab loss

@dataclass
class ThreadResult:
    ordinal: int
    thread_id: str               # final id (after PSID recompute), "" if failed
    status: Literal["persisted", "no_messages", "click_verify_failed",
                    "locate_failed", "error"]
    messages_added: int = 0
    locate_method: str = ""      # "direct_url" | "sidebar_identity" | ...
    elapsed_ms: int = 0
    worker: str = ""             # "orchestrator" | "worker:1" …
    error: str = ""
```

Both types are pure data (no Playwright, no sqlite handles) so they can be
logged as JSON at `DEBUG` with the universal ID
`logs:inbox-parallel-fetch-001:<task|result>`.

---

## 5. Worker locate ladder

A worker tab does not share the orchestrator's sidebar scroll position, so
locating a thread is the only genuinely new browser behaviour. Strategies
are tried in order; the first verified success wins.

| Step | Strategy | Precondition | Cost | Verification |
| --- | --- | --- | --- | --- |
| L0 | **Direct URL** `inbox/all?asset_id=<page>&mailbox_id=<page>&selected_item_id=<psid>&thread_type=FB_MESSAGE` (same URL shape `l2_actions.navigate_to_thread` already uses in production) | `task.record.selected_item_id` or `task.psid_hint` non-empty | 1 `goto` ≈ 3–5 s | URL still contains the PSID **and** header name matches `thread_name` **and** last extracted message ≈ `preview_text` (normalised prefix compare, same rule as Stage 1 dedup). Any mismatch → fall through. |
| L1 | **Sidebar identity locate** — existing Stage 2 click loop (identity key → PSID → hovercard → unique name → name+preview) preceded by a progressive `scroll_sidebar_and_wait` walk instead of blind `mouse.wheel` | always | proportional to the thread's depth; amortised ≈ one Stage 1 pass per worker because tasks arrive in inbox order and each worker only ever scrolls **downward** | `verify_thread_switch` (unchanged) |
| L2 | Give up | L1 exhausted (`MAX_THREAD_LOADING_WAIT_MS`, stagnant retries, or the sidebar's last visible time token is older than the task's token by more than one bucket — overshoot) | — | `ThreadResult.status = "locate_failed"` |

`psid_hint` resolution (orchestrator side, Stage 1): `SELECT fb_url FROM
users WHERE thread_name = ? AND thread_id LIKE '<page_id>_%'` — used only
when the name is unique for the page. On the first parallel run most tasks
have no hint and take L1; each verified thread writes its PSID to
`users.fb_url`, so subsequent `--refresh` runs hit L0 for almost every
thread. The design gets faster with use.

Sidebar search (the Inbox search box) is intentionally **not** in the ladder:
its DOM is unverified and would need its own anti-fragile spike
(`doc:spike-inbox-search-locate-001`, not scheduled).

---

## 6. Concurrency and resource model

| Resource | Rule |
| --- | --- |
| Playwright | one `sync_playwright()` + `connect_over_cdp` per thread, created inside the thread, closed by the thread. |
| Tabs | `attach_to_authorized_session(..., tab_role="scan_inbox_worker:<i>")`. Tab lookup/creation is serialised with a module-level `threading.Lock` because it enumerates and evaluates on every page in the context. Role tabs are long-lived by design (`AuthorizedSession.close_page` no-ops for role tabs); a run reuses them. |
| SQLite | one `get_db_connection()` per thread (default `check_same_thread=True` is respected). `PRAGMA journal_mode=WAL` already set; add `PRAGMA busy_timeout=30000`. Each `persist_thread_record` is one short transaction, so writer contention is bounded by 4–5 concurrent short writes. |
| Stage 1 dedup set / stats | owned by the orchestrator thread only. Workers never touch them; they report through `result_q`. |
| Message-target counter | `threading.Lock`-guarded int updated from `ThreadResult.messages_added`; orchestrator sets `stop_event` when `existing + added ≥ target_total_messages`. |
| Logging | one `logging` handler set (thread-safe); every worker log line is prefixed `[worker:i]`. |
| Worker cap | `--workers` clamped to `[1, 3]`: 1 orchestrator and at most 2 worker tabs. This cap avoids excess Business Suite rate limiting and host-memory pressure (≈ 400 MB / tab). |

---

## 7. Lifecycle, stop conditions, failure handling

```text
 start ──▶ orchestrator attaches scan_inbox tab
       ──▶ spawn N-1 worker threads (each: playwright → CDP → role tab → conn)
       ──▶ Stage 1 loop { read → enqueue tasks → scroll }
       ──▶ Stage 1 ends (any existing stop rule)  ──▶ put N-1 sentinels
       ──▶ orchestrator runs worker loop on its own tab until queue empty
       ──▶ join workers; drain result_q; aggregate stats
       ──▶ record_fetch(); LLM city classify (unchanged); write diag log
```

| Event | Behaviour |
| --- | --- |
| Worker tab closed / `TargetClosedError` | worker re-attaches a fresh role tab (max 2 re-attaches per run); the in-flight task is re-queued with `attempt+1` if `attempt < 2`, else reported `error`. |
| Worker thread dies (unhandled) | logged; orchestrator notices via `is_alive()` on join and drains the remaining queue itself. No task is lost silently. |
| `target_total_messages` reached | `stop_event` set; orchestrator stops enqueueing; workers finish the current task and exit; leftover tasks are counted in `stats["tasks_abandoned"]`. |
| `Ctrl-C` | `stop_event` set; same as above; role tabs stay open (design of role tabs). |
| Orchestrator tab lost during Stage 1 | Stage 1 ends early (existing exception path); already-queued tasks are still processed by workers before the run reports failure — partial progress is persisted, not lost as today. |
| Duplicate PSID resolved by two tasks (card changed preview during crawl) | both persist the same `thread_id`; `persist_thread_record` is upsert + message dedup, so the second is a no-op refresh. |

Stats returned to the CLI keep every existing key and add
`workers`, `tasks_dispatched`, `tasks_abandoned`,
`locate_methods` (`{"direct_url": n, "sidebar_identity": n}`),
`stage1_ms`, `stage2_ms`, `per_worker` (list of `{worker, processed, errors, elapsed_ms}`),
`assignments` (one row per task: inbox index, thread name, worker, status, locate method, elapsed) and
`assignment_log` (path of the per-run `logs/parallel-fetch/assignments_<ts>.jsonl`, `logs:inbox-parallel-fetch-001:assignment`).

---

## 8. Invariants that must hold in every mode

1. `--workers 1` executes the current code path (no threads, no extra tabs, identical stats keys) — protected by `tests/test_l3_inbox_pipeline.py::test_scrape_inbox_performs_one_sidebar_scroll_and_one_wait_cycle` and the Hung Bui snapshot gate (CLAUDE.md §10.1).
2. `inbox_sort_index` equals the Stage 1 ordinal regardless of which worker persisted the row.
3. Persisted rows for a given inbox state are identical whether produced by 1 or N workers (order of persistence is irrelevant because `persist_thread_record` is idempotent).
4. Fetching remains read-only: no worker ever focuses the composer or calls anything from `l2_actions` other than URL navigation. `send_reply_via_cdp` stays exclusive to the HITL daemon (CLAUDE.md §10.3).
5. City classification still runs once, after all persistence, and never after an empty scrape.

---

## 9. Pre-existing weakness surfaced by this design (out of scope, tracked)

`bug:inbox-thread-identity-001` — Stage 1's provisional `thread_id` is
`sha256(sidebar_identity_key)`, which embeds the preview text and time token,
while persisted rows use `sha256(PSID)`. The two never match, so the Stage 1
"already synced" check (`skipped_threads`, `consecutive_clean_threads`) is
effectively dead on every run (`new_threads: 30, skipped_threads: 0` on the
7d run). This is why `--refresh` and non-refresh behave identically today.
The parallel design does not depend on it, but fixing it (look up existing
rows by `(page_id, thread_name, normalised preview)` and carry the PSID as
`psid_hint`) would make L0 the common path and restore the early-exit cache.
Schedule as its own bug fix after Phase 4.

---

## 10. Implementation plan

Universal ID prefix for code: `code:inbox-parallel-fetch-001:<component>`.
Tests: `code:test-inbox-parallel-fetch-001:<component>`.

### Phase 1 — Extract the per-thread unit (no behaviour change)

| # | Change | File | ID |
| --- | --- | --- | --- |
| 1.1 | Move the Stage 2 loop body into `process_thread_task(page, conn, task, deps, logger) -> ThreadResult`, where `deps` bundles `extract_ad_id_labels`, `extract_user_info`, `detect_city`. | new `fb_pipeline/browser/inbox/thread_worker.py` | `…:thread-worker` |
| 1.2 | Move the click/identity-match JS and its retry loop into `locate_thread_in_sidebar(page, task, logger) -> LocateResult`. | new `fb_pipeline/browser/inbox/thread_locator.py` | `…:locator-sidebar` |
| 1.3 | Split Stage 1 into `discover_threads(page, …, on_task: Callable[[ThreadTask], None]) -> Stage1Stats`. `scrape_inbox` becomes: `discover_threads(on_task=collected.append)` + sequential `process_thread_task` — the existing behaviour. | `fb_pipeline/browser/l3_inbox.py` | `…:discover` |
| 1.4 | Add `ThreadTask` / `ThreadResult`. | `fb_pipeline/contracts/l1_inbox_tasks.py` | `…:contracts` |

Exit criteria: full `pytest tests/` green; Hung Bui snapshot run
(`--maxThreads 1`, thread must be Hung Bui) identical to
`tests/hungbui_test_output.json` on two consecutive runs.

### Phase 2 — Worker locate ladder and session bootstrap

| # | Change | File | ID |
| --- | --- | --- | --- |
| 2.1 | `locate_thread_direct(page, page_id, task, logger)` — L0 with the three-way verification from §5. | `thread_locator.py` | `…:locator-direct` |
| 2.2 | Progressive sidebar walk in L1: replace blind `mouse.wheel` retries with `scroll_sidebar_and_wait` rounds + time-token overshoot stop. | `thread_locator.py` | `…:locator-sidebar` |
| 2.3 | `resolve_psid_hint(conn, page_id, thread_name)` (unique-name lookup). | `fb_pipeline/persistence/l4_sqlite_store.py` | `…:psid-hint` |
| 2.4 | `attach_worker_session(playwright, page_id, inbox_url, worker_index)` + attach lock. | `fb_pipeline/session/l2_bootstrap.py` | `…:worker-session` |
| 2.5 | `PRAGMA busy_timeout=30000` in `get_db_connection`. | `l4_sqlite_store.py` | `…:db-busy-timeout` |

Unit tests with fake `page` objects (pattern of `tests/test_l3_inbox_pipeline.py`):
direct-URL accept/reject matrix (URL ok + name ok + preview ok → accept; any
one wrong → fall through), overshoot stop, hint uniqueness rule.

### Phase 3 — Orchestrator

| # | Change | File | ID |
| --- | --- | --- | --- |
| 3.1 | `run_parallel_fetch(page, page_id, time_range, max_threads, conn, workers, …) -> stats`: spawns workers, runs `discover_threads(on_task=task_q.put)`, sentinels, orchestrator-as-worker-0, join, aggregate. | new `fb_pipeline/inbox/l3_parallel_fetch.py` | `…:orchestrator` |
| 3.2 | `worker_main(worker_index, page_id, inbox_url, task_q, result_q, stop_event, deps)` — own playwright/CDP/tab/conn, re-attach policy, `[worker:i]` log prefix. | `fb_pipeline/inbox/l3_parallel_fetch.py` | `…:worker-main` |
| 3.3 | Stop conditions (§7) and stats keys. | same | `…:stop-rules` |
| 3.4 | CLI: `--workers N` (default 3, clamp 1–3: 1 orchestrator + at most 2 worker tabs). `workers == 1` → `scrape_inbox`; else `run_parallel_fetch`. Only valid with `--cdp` (headless Mode 3 launches its own browser and stays sequential in this iteration). | `tools/l5_fetch_fb_messages.py` | `…:cli` |

Unit tests: fake worker threads and a fake `discover_threads` to prove
FIFO dispatch before Stage 1 ends, sentinel shutdown, re-queue on tab loss,
`target_total_messages` stop, stats aggregation, and that `--workers 1`
never imports threading paths.

### Phase 4 — Live validation (operator run, after the current 90d job finishes)

1. `--time_range 7d --cdp --refresh --workers 1` → snapshot DB (`threads`, `messages` counts per thread).
2. Same with `--workers 3` → diff must be empty apart from `last_synced_at`.
3. Hung Bui snapshot gate with `--workers 3 --maxThreads 3` (Hung Bui must be among the first three cards; abort otherwise).
4. `--time_range 90d --refresh --workers 3` → record `stage2_ms` and speed-up in `logs/` and in §1 of this document.
5. Tune: worker cap, `busy_timeout`, overshoot bucket.

### Phase 5 — Close-out

- Retrospective entries (`# Retrospective [date]`) for every Facebook UI
  anomaly met during Phase 4, per the anti-fragile rule in `architecture.md`.
- Update this document's status to **Implemented**, fill the measured table.
- Iteration handover in `logs/`.
- Open `bug:inbox-thread-identity-001` (§9).

---

## 10a. Phase 4 live validation log (2026-09-16, `7d --workers 5 --maxThreads 5 --cdp --refresh`)

| Run | Outcome | Stage 2 | Locate | Notes |
| --- | --- | --- | --- | --- |
| 1 | 5/5 persisted, 0 errors | 56.0 s | `sidebar_identity` ×5 | L0 rejected everywhere: post-`goto` header is the generic "Inbox" h1 (same anomaly as `verify_thread_switch`). Fixed: generic headers are neutral; PSID + preview decide. |
| 2 | 2/5 persisted, orchestrator 3 errors | 21.1 s | `direct_url` ×2 | Orchestrator tab reported `TargetClosedError` mid-task; not reproduced since; `close`-event + attach diagnostics added. Root cause open (see §11). |
| 3 | 5/5, 0 errors | 19.5 s | `direct_url` ×5 | Workers created 2 new tabs: `page.goto` wipes the DOM role marker. Fixed: `stamp_tab_role` re-applied after every task. |
| 4 | 5/5, 0 errors | 12.6 s | `direct_url` ×5 | All 4 worker tabs reused (attach 1–3 s instead of ~13 s); tab count stable at 5. |

| 5–6 | `90d --maxThreads 5/8` | 39 s / 38 s | `direct_url` ×7, `sidebar_identity` ×1 | Distribution made visible: `[dispatch]`/`Picked`/`Finished` INFO lines and a per-run `logs/parallel-fetch/assignments_<ts>.jsonl`. Found: 'Mai Hoa' failed L0 (quoted-reply bubble + sidebar label noise broke the preview prefix rule) and then L1 (tab already on the PSID → no URL change, header "Inbox"). |
| 7 | `90d --maxThreads 8` | crash in Stage 1 | — | **Root cause of run 2**: with many tabs open the orchestrator's URL-based tab pick (`prefer_new_tab=False`) selected a role-marked worker tab; the worker's L0 `goto` then destroyed the orchestrator's execution context. Fixed: URL-based selection skips any tab carrying a `masTabRole` marker. |
| 8 | `90d --maxThreads 8` | 32.2 s | `direct_url` ×8 | Preview rule now probe-based (leading 24 chars either way); `verify_thread_switch` accepts `selected_item_id == target PSID` as definitive (`method=selected_item_id_exact`); `psid_hint` is copied into `record.selected_item_id` before locating. 8/8 persisted, no warnings. |

Sequential baseline for 8 threads ≈ 8 × 15 s ≈ 120 s, so run 8 is ≈ 3.7× on Stage 2 at this size (tab bootstrap and the first-task cost dominate); the full 90d measurement (Phase 4 step 4) is still pending.

## 11. Risks and open questions

| Risk | Mitigation |
| --- | --- |
| Business Suite rate-limits concurrent inbox tabs | Cap at 3 total tabs (1 orchestrator + at most 2 workers), default 3; a `--worker-delay-ms` throttle is available if further moderation is needed. |
| Sidebar order shifts while workers are scrolling (new incoming message moves a thread to the top) | L1 matches by identity, not position; `absolute_top` is only a hint. The orchestrator's Stage 1 dedup already tolerates this. |
| Same-name conversations | L0 requires preview match; L1 already disambiguates by preview; hint lookup requires unique name. |
| Host memory | ≈ 400 MB per tab; 5 tabs is well within the operator Mac. |
| Playwright per-thread instances | documented supported pattern; each thread owns and closes its own instance. |
| Orchestrator `TargetClosedError` / "execution context destroyed" (runs 2 and 7) | **Resolved**: orchestrator and a worker shared one tab because URL-based tab selection did not exclude role-marked tabs. `attach_to_authorized_session(prefer_new_tab=False)` now skips tabs with a `masTabRole` marker (`test_url_based_selection_skips_role_tabs`). Attach/close diagnostics remain in place. |
| Headless Mode 3 (`fb_credential_*.json`) stays sequential | acceptable; CDP mode is the operational path (CLAUDE.md §10.4). Revisit if headless ever becomes the default. |
