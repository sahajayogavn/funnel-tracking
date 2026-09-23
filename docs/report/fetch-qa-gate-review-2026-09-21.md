---
id: doc:inbox-fetch-qa-review-001
date: 2026-09-21
scope: every check that decides whether fetched inbox data is trusted
---

# Fetch QA gate review — is each check a real stop gate?

Principle (owner rule, 2026-09-21): a QA failure means extraction produced
wrong data, caused by exactly one of (A) our code is wrong, or (B) Facebook
changed its DOM/structure. A gate must therefore (1) fire on every wrong
extraction it can observe, (2) never fire on correct extraction, and (3)
actually stop the run. Each layer is graded on those three properties.

## Layer map

| # | Gate | Where | Fires on | Stops run? |
|---|------|-------|----------|------------|
| G1 | Thread identity | `thread_detail_parser.verify_thread_switch` | page/recipient URL ≠ expected, panel heading ≠ name, unstable poll | yes (thread `error` → worker retires) |
| G2 | Source model binding | `facebook_message_source.source_messages` | node id ≠ model id, participants/viewer wrong, sender direction wrong, bad epoch, unsupported payload, body ≠ DOM | quarantine (incomplete) or hard |
| G3 | Snapshot evidence | `l1_fetch_integrity.check_snapshot` | missing id/actor/day, duplicate id, day↔timestamp disagreement, non-monotonic order | quarantine or hard |
| G4 | Snapshot stability | `compare_snapshots` (two viewport reads) | any field differs between reads | hard |
| G5 | Stored consistency | `compare_stored` | same `source_id` already stored with other thread / sender / datetime | hard |
| G6 | Fetch QA-1 (order) | `l3_fetch_qa` | live top-10 rank vs `threads.inbox_sort_index` | **was report-only** |
| G7 | Fetch QA-2 (content) | `l3_fetch_qa` | live sidebar preview vs newest stored `kind='message'` row + sender side | **was report-only** |
| G8 | Run completion | `l3_parallel_fetch` `fetch_complete` | abandoned/stranded/failed/partial-history | **was report-only** |

## Findings

### F1 — G6/G7/G8 did not stop anything (code bug, fixed)
`tools/l5_fetch_fb_messages.py` computed `success` from `failed_threads` only.
Run 1 today ended with `qa_status=failed`, 74 abandoned threads, and **exit 0**;
`run_inbox_mas_loop.sh` would have scheduled the next cycle over wrong data.
Fix: `fetch_qa_failed` and `fetch_incomplete` now exit 76 (same code the loop
script already treats as "stop for evidence review"). A QA *crash* also yields
`qa_status=failed`, so an unverifiable run is a failed run.

### F2 — G5 rejected correct data (code bug, fixed)
Legacy rows carry the DOM cluster label (minute floor of the first bubble);
the source model carries the exact epoch. 176/176 measured conflicts were the
epoch 60 s–2 h **after** the label. `compare_stored` only allowed the same
minute. Now: forward drift ≤ 24 h from a non-source row is a refinement; an
earlier epoch, > 24 h, or epoch-vs-epoch disagreement stays hard.
Root-cause class: **A (code)** — the rule did not model what the legacy label
actually asserted.

### F3 — G2 body/DOM check rejected correct data (Facebook rendering, fixed)
Facebook paints `:)` as `<img alt="🙂">` and `<3` as `<img alt="❤">`; the
model keeps ASCII. The guard exists to detect a model bound to the wrong node,
so it now strips Facebook's documented emoticon set and compares letters/digits
only. Wrong-node binding still fires (test covers both). Conflicting DOM text
is now kept in the report (`source_dom_text`) so the next such case is
diagnosable from the JSON alone.
Root-cause class: **B (Facebook structure)** — a rendering convention we had
not modelled; note it is *not* a schema drift, node/model ids still matched.

### F4 — Stage 1 early exit after an aborted run (code bug, fixed)
"Two consecutive clean cards ⇒ stop scanning" is only valid if the previous run
completed. After the abort in F2, the next run skipped the top two and never
reached the stale card at rank 5. `previous_fetch_incomplete()` now disables
early exit when `MAX(threads.fetched_at)` is newer than the last `fetch_log`
row.

### F5 — G6/G7 could not tell "wrong extraction" from "raced by new activity" (fixed)
`fetch_started_at` was passed to QA but unused; a customer replying during a
20-minute run produced a *hard* reject. QA now reads the card's exact sidebar
epoch (`sidebarTimestampMs`): a card newer than `fetch_started_at` is reported
as `soft` with `reason=arrived_after_fetch`; older cards keep full strictness.
Hard now means "the data the fetch could have seen is wrong".

## Remaining weaknesses (not changed today — decisions for the owner)

| # | Gap | Effect | Proposed |
|---|-----|--------|----------|
| W1 | G7 compares only the newest `kind='message'` row. If the newest bubble is an attachment/sticker/reply-quote, G2 quarantines it (`unsupported_source_payload`) and G7 compares the *previous* text message against a preview like "Bạn đã gửi một ảnh" → hard reject on correct behaviour. | false hard | G7 should look at `inbox_fetch_observations` for the thread; newest quarantined bubble newer than the newest message ⇒ `soft` with `reason=newest_bubble_quarantined`. |
| W2 | G7 sender mismatch (preview `You:` vs DB `Customer`) is only `soft`. That is a wrong-actor extraction — exactly what the gates exist for. | missed hard | Make sender mismatch `hard` once W1 is in place (attachments are the main legit source of disagreement). |
| W3 | G6 name resolution: two seekers with the same display name anywhere on the page ⇒ unmatched ⇒ hard, even when only one of them is in the stored top-10. | false hard | Disambiguate against `db_top_10_ids` first, then the whole page. |
| W4 | G7 never checks *time*: newest stored `message_at` vs card `sidebarTimestampMs`. With source epochs both are exact, so `|Δ| > 60 s` would be a strong, cheap "we stored the wrong last message" signal. | missed hard | Add as a third QA-2 criterion; `soft` when the newest bubble is quarantined (W1). |
| W5 | 34 threads (10 of the top-10) still hold legacy `visual:` rows with cluster-label times and emoji-stripped bodies. They self-heal only when re-fetched. | latent G5/G7 hits | One deliberate `FUNNEL_FETCH_FORCE_REFRESH=1` run within the active range after the current fixes are confirmed; or accept gradual healing. |
| W6 | G2 relies on an undocumented React fiber shape (`memoizedProps.message`). Drift ⇒ every bubble `missing_source_model` ⇒ whole run `needs_review` (correct fail-closed), but the report will not say "schema drift". | slow diagnosis | Add a run-level check: if > 90 % of bubbles lack a model, emit a single `facebook_source_schema_drift` critical with one sample node's prop keys. |
| W7 | QA writes `qa_status` to the *latest* `fetch_log` row; when the run was incomplete no row was written, so it overwrites the previous complete run's status. | misleading history | Insert a `fetch_log` row with `qa_status` for incomplete runs too (with `threads_found=NULL`), or keep a separate `fetch_qa_log`. |

## Verification state

- Unit: 183 tests across the fetch suites pass (`test_decoupled_fetch_qa`, `test_fetch_integrity`, `test_facebook_message_source`, `test_l3_sync_decision`, `test_l3_inbox_worker`, `test_l3_parallel_fetch`, `test_thread_detail_parser_dom`, `test_inbox_system_events`).
- Live run 3 (before F3's `<3` fix): 12 threads persisted, then hard-stopped on `53b792ac20d144e2` (`<3`). Hoang An Minh (`a156cbe53c57970f`) was re-persisted from the source model; its stored body now carries the emoji.
- **No further live run has been started.** Next run is the owner's call after this review.

## Round 2 (22:20–23:00) — W1/W2/W4 done, then run-4 analysis

W1, W2, W4 implemented in `l3_fetch_qa.py` (tests `test_q16`–`test_q18`).

Live run 4 (720d, new code for F1–F5 + W1/W2/W4): 287 threads persisted,
**0 integrity failures**, 1008 new messages — then stopped by hand after the
metrics showed three resource defects that would recur every 15-minute cycle:

| # | Finding | Evidence | Class | Fix |
|---|---------|----------|-------|-----|
| R1 | 163/221 threads (74 %) had quarantined bubbles → `fetch_history_complete=0` → marker cleared → **re-opened every cycle forever** | 336 `unsupported_source_payload` bubbles: 142× quick-reply "Hỏi chi tiết" (templated), 73× Page image+caption, 65× media-only, 8× Zalo link | A (gate modelled "non-text" as "unverified actor") | `facebook_message_source`: actor/time come from the bound model, so attachment / quoted-reply / template turns are admitted. Body: model text; template → deduped rendered DOM lines; media-only → `[attachment]`. Payload shape recorded in `sender_evidence.payload`. Log messages stay unsupported. |
| R2 | The same bubble parsed twice: bound (model) + unbound legacy span with no id → 175 `missing_source_model` rows | 142× the same "Hỏi chi tiết" bubble | A | `reconcile_source` drops an unbound legacy row whose text is contained in a bound bubble's rendered text; unknown rows still quarantine. |
| R3 | Even a legitimately incomplete thread (e.g. "Message removed") was re-opened every cycle | Stage 1 `incomplete_history` → FETCH unconditionally; marker was nulled on partial fetch | A | Marker is now recorded on partial fetch (truth kept in `fetch_history_complete`); Stage 1 skips an unchanged incomplete card for 24 h (`incomplete_history_unchanged`), re-opens on card change or after 24 h. |
| R4 | QA-2: `[attachment]` vs "Bạn đã gửi một ảnh" would be a hard reject | — | A | `match_for_qa` recognises Meta's media-preview wording (vi/en). |
| R5 | 720d range + early exit disabled = ~2 100-thread historical backfill (~3 h, 2 workers) | Stage 1 `new_thread`=2087 | config | Not a bug; owner decision. Verification runs use 15d. |

190 fetch tests pass. Run 5 (15d) started 23:00 with all of the above.

## Round 3 (23:00–23:20) — runs 5, 6, 7

| Run | Window | Result | What it exposed | Fix |
|-----|--------|--------|-----------------|-----|
| 5 | 15d | 52 persisted, 0 integrity failures, quarantine 74 % → 6 %; QA **crashed** (`Target page … closed`) → reported as `failed`, stop gate fired (exit 76) | Orchestrator tab closed from outside during Stage 2 (both worker tabs also closed at 22:50:55 and re-attached themselves). Also `fetch_complete=False` solely because of 3 partial-history threads. | QA re-attaches an inbox tab when the orchestrator tab is closed (`tools/l5_fetch_fb_messages.py`). Partial history no longer makes the *run* incomplete (`l3_parallel_fetch.py`). |
| 6 | 15d | 0 to fetch; QA `failed`: QA-1 hard at rank 10, QA-2 two `[attachment]` rows newest but preview shows text | **(a)** Reused orchestrator tab still had a conversation selected (`selected_item_id`); Meta pins the selected card into the list → Stage 1 wrote a "Sep 6" thread at rank 8 among "Tue" cards → ranks 8–10 shifted. **(b)** Turns admitted on a later crawl (media bubbles from R1) were appended with `seq` after newer rows: `ORDER BY seq` no longer meant "latest". **(c)** W1 softened via an *old* observation whose bubbles had since been admitted. | (a) `l2_bootstrap`: always navigate a reused tab to the bare inbox URL when `selected_item_id=` is present (`code:inbox-order-invariant-001`). (b) `resequence_thread_by_time()` after every persist, seq follows exact source time, untimed rows inherit the previous timed row (`code:inbox-msg-order-001`); one-off `tmp/resequence_all_threads.py` fixed 34 threads / 296 rows. (c) W1 consults observations only while `fetch_history_complete=0`. |
| 7 | 15d | **QA passed: hard 0, soft 0, exit 0.** QA-2 `time_delta_s` 0.1–0.9 s on source rows; 290–895 s on three legacy-label rows (forward tolerance, W5). | — | — |

Class summary for the day: every root cause was **A (our code)**; the only
Facebook-side surprises were rendering conventions (emoticon images, pinned
selected card), not schema drift. 197 fetch tests pass.

Still open: W3 (duplicate display names → hard), W5 (legacy rows heal only on
re-fetch; visible as 290–895 s deltas), W6 (schema-drift diagnostics), W7
(`qa_status` written to the previous `fetch_log` row on incomplete runs), and
the 720d backfill decision (R5).

## Round 4 (23:25–) — W3, W6, W7 closed in code; W5 healed by one deliberate refresh run

| # | Change | Where | Test |
|---|--------|-------|------|
| W3 | Duplicate display name no longer a false hard. Card→thread resolution: unique name → it; several, one in stored top-N → that one; several in top-N → the unconsumed candidate nearest the card's rank (QA-2 still verifies the preview against that thread, so a wrong pick cannot pass silently); none in top-N → unmatched with `reason=duplicate_display_name`. Live DB today has 4 duplicated names (Hoàng Yến, Huong Nguyen, Huong Pham, Trần Nguyên); any of them entering the top-10 would have hard-failed QA-1. | `l3_fetch_qa._resolve_card_identity` (`code:inbox-fetch-qa-001:duplicate-display-name`) | `test_q08` (rewritten to the new policy), `test_q19` |
| W6 | Schema drift is diagnosed, not inferred from a wall of `missing_source_model`. `SOURCE_SCRIPT` now returns `region_found`, `page_message_nodes` and, for an unbound node only, the *names* of the nearest fibers' props (never values). `diagnose_schema_drift()` fires when ≥ 3 bubbles are rendered and ≤ 10 % bind to a model; every conversational row then carries the **hard** issue `facebook_source_schema_drift` (thread → `error`, worker retires, `failed_threads` → exit 76) and the first row carries the diagnosis into the integrity report (`detail`); the worker logs one `FACEBOOK_SOURCE_SCHEMA_DRIFT` critical line. One unbound bubble among bound ones stays a quarantine. | `facebook_message_source.diagnose_schema_drift`, `l1_fetch_integrity.check_snapshot`, `thread_worker` (`code:inbox-fetch-source-001:schema-drift`) | `test_schema_drift_is_one_hard_diagnosed_issue…`, `test_source_script_reports_region_and_prop_keys…` (real Chrome) |
| W7 | `qa_status` is attached to *this* run's `fetch_log` row. If the newest row is older than `fetch_started_at` (run incomplete ⇒ `record_fetch` never wrote one) QA inserts its own row with `threads_found`/`messages_found` NULL instead of overwriting the previous complete run. `previous_fetch_incomplete()` ignores NULL rows so a QA-only row can never re-enable Stage 1 early exit; `run_inbox_mas_loop.sh` reads `COALESCE(messages_found,0)=0` for it (MAS not triggered) and the MAS freshness gate sees the failed status (untrusted) — both the intended outcomes. | `l3_fetch_qa._record_qa_status` (`code:inbox-fetch-qa-001:qa-status-own-run`), `l3_sync_decision.previous_fetch_incomplete` | `test_q20`, `test_previous_fetch_incomplete…` (extended) |
| W5 | Before run 8: 16 threads / 170 `kind='message'` rows still carried `visual:` evidence (cluster-label minute, emoji-stripped body); all within 15d, 4 of them in the top-10. Re-crawl updates rows in place by `source_id` (`persist_thread_record`), so one `--refresh` over 15d replaces them with source epochs. | `tools/l5_fetch_fb_messages.py --time_range 15d --cdp --refresh --workers 3` (log: `logs/fetch-run8-force-refresh-2026-09-21.log`) | run 8 below |

Unit: 212 tests pass across the fetch suites (was 197).

### Runs 8–10 (23:35–23:49, all 15d, 3 workers)

| Run | Purpose | Result | What it exposed | Fix |
|-----|---------|--------|-----------------|-----|
| 8 | W5 heal: `--refresh` | 86 threads re-persisted, 0 integrity failures, 5 new messages, **QA passed (hard 0, soft 0), exit 0**. Legacy `visual:` message rows on this page: 170 → **0**. QA-2 `time_delta_s` now 0.1–0.9 s on every sampled row (was 290–895 s on legacy rows). `fetch_log` row 243 received `qa_status` via the W7 *update* path (row written by `record_fetch` after `fetch_started_at`). | QA sampled only **7** cards although 10 conversation cards exist (also true of run 7). | see W8 |
| 9 | verify after W3/W6/W7 (no refresh) | 0 to fetch; QA passed, exit 0; still 7 cards after a 5 s render wait. | **W8 — coverage, class A.** Meta virtualises the sidebar to the *window* height: on the 862 px CDP window the first card sits at y=440 with an 80 px pitch, so exactly 7 cards are in the DOM. "Top-10" QA had silently been "top-7" (ranks 8–10 unverified). The orchestrator tab also still carried `selected_item_id` at QA time, i.e. the pinned-card hazard from run 6(a) applied to QA sampling too. | `l3_fetch_qa.sample_top_cards` (`code:inbox-fetch-qa-001:sample-coverage`): navigate to the bare inbox URL if a conversation is selected, reset to top, then scroll the sidebar in short steps (`scroll_sidebar_and_wait`, ≤ 5 rounds) merging cards by absolute scroller offset until 10 are collected, and reset to top again; the report records `cards_sampled`. Tests: `test_sample_top_cards_scrolls_until_ten_cards_and_returns_to_top`. |
| 10 | verify W8 | 0 to fetch; **QA passed, hard 0, soft 0, `cards_sampled=10`, exit 0**; ranks 1–10 all `pass`, `time_delta_s` 0.08–0.88 s. Ranks 8–10 (Mai Hoa, Lê Ngọc Thông, Nguyễn Hồng An) verified for the first time since run 6. | — | — |

216 fetch tests pass (50 subtests). Every root cause in Round 4 is again class **A (our code)**: the only Facebook-side behaviour involved (viewport-sized virtualisation) is a rendering property we had not modelled, not a schema change — and W6 now makes a real schema change self-describing.

Open (owner decisions, unchanged): the 720d historical backfill (R5, ~2 100 threads, ~3 h with 2 worker tabs; verification runs today all used 15d).
