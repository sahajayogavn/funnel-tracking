---
id: doc:inbox-automated-page-turn-001
date: 2026-09-22
scope: Inbox automation turns ("Automated response. Manage Automations") and duplicated crawled reactions
---

# Automated Page turns are labelled from Meta's own evidence

Owner rule (2026-09-22, seeker 14351): a bubble that Meta renders with the
"Automated response. Manage Automations" footer is a canned reply the Page
configured for a post/ad or keyword. Its sender is the Page, but readers
(MAS, Telegram card, dashboard) must see **"Page (automated message)"** so it
is clear who answered and why the templated text is there.

## What the DOM/model actually says

The footer is a sibling element *after* the bubble, with no
`data-message-id` — the legacy DOM parser never captured it. The bound
message model carries the fact directly:

| `creatorInfo.creatorType` | `creatorName` | Meaning |
|---|---|---|
| `automated_response` | "Automated Response" | Inbox automation (the footer case) |
| `direct_admin` | admin display name (e.g. "Hung Bui") | a person replied from the Page |
| `null` | — | plain Page turn; Meta gives no origin |

So the sender is taken from the label, never inferred from wording (the
2026-09-17 rule that persistence must not guess `Auto_Page` from canned text
still holds; only the *source* layer may set it, and only on evidence).

## Changes (`code:inbox-fetch-source-001:automated-page-turn`)

| Layer | Change |
|---|---|
| `facebook_message_source.SOURCE_SCRIPT` | reads `creator_type`, `creator_name` (scalars only) |
| `source_messages` | Page turn with `creator_type == automated_response` → sender `Auto_Page` (`explicit`); evidence payload records `creator_type`/`creator_name` for every labelled turn (109 rows now carry the admin who replied) |
| `l1_fetch_integrity` | `Auto_Page` is an admitted verified sender; `compare_stored` treats Page↔Auto_Page as the same side (a refinement), Customer↔Page still a conflict |
| `l3_pipeline.persist_thread_record` | unchanged: re-crawl updates the stored row in place, so existing `Page` rows became `Auto_Page` on refresh |
| `l1_conversation_state.format_conversation_lines` | `[time \| Page (automated message)] …` in the MAS thread; stored value stays `Auto_Page`, and `compute_conversation_state` already counts only `Page` as a human reply |
| `tools/l5_telegram_hitl.py` | same label on the HITL card |
| `web/src/components/{seeker-detail,seeker-sidebar,network-graph}.tsx` | `@Auto_Page` → `Page (automated message)` |

Tests: `test_inbox_automation_is_auto_page_from_meta_creator_label_never_from_wording`,
`test_format_conversation_lines_labels_inbox_automation_as_page_automated_message`,
Telegram card test updated.

## Reactions were multiplied once per crawl (fixed)

The element the owner selected showed "❤ unknown · message" five times on
one bubble. `crawled_message_reactions.reaction_key` hashed `observed_at`,
i.e. *when we looked*, so every crawl inserted the same heart again
(219 rows, 91 distinct). `observed_at` is no longer part of the identity;
`tmp/rekey_crawled_reactions.py --apply` re-keyed 91 rows and removed 128
duplicates (thread 14351: 15 → 3). Test: reaction identity excludes
`observed_at` in `test_persist_stores_crawled_reaction_as_structured_evidence`.

## Verification

Run 11 (`--time_range 15d --cdp --refresh`, `logs/fetch-run11-auto-page-refresh-2026-09-22.log`):
0 integrity failures, Fetch QA **passed** (hard 0 / soft 0 / 10 cards), exit 0.
Thread 14351 seq 1 ("Xin chào … để lại Họ tên và số điện thoại") is now
`Auto_Page`; 2 automation rows in the 15d window. 245 tests pass across the
touched suites; `tsc --noEmit` clean.

## Open — needs the owner

Thread 14351 also has same-second Page replies with **no** creator label
(seq 5 "Chào Ngọc Giàu! Chúng tôi có thể giúp gì cho bạn?", seq 10/13 "…bạn
quan tâm tới lớp thiền ở Hà Nội phải không?"), sent ≤ 1 s after the
customer's quick-reply "Đăng Ký Học Thiền". They behave like a Messenger
flow/instant reply, but Meta marks them neither `automated_response` nor
`direct_admin`. Under the no-inference rule they stay `Page`. If they should
read as automated too, the only available evidence is timing (Page turn
within N seconds of a customer turn, no creator label) — that is a policy
decision, not something the fetch can prove.
