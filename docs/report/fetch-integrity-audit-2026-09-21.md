# Fetch integrity — 21/09/2026

Scope: seeker 4348 (Phố Châu), thread `1548373332058326_285fee1acbea12c7`,
then investigation of sender, recipient identity and event time across ingestion.
No production message rows were rewritten, no full Facebook recrawl was run,
and no outbound messages were sent during this investigation.

## Commit archaeology, checked before implementation

| Commit | Existing fix / documentation | Remaining problem |
| --- | --- | --- |
| `f3ecd92` (04/04) | “resolve sender attribution errors”; commit body explicitly calls white bubbles Page and aggregates quoted arrays | CSS was promoted to actor identity; a cluster became one actor-bearing event |
| `eea9563` (06/04) | First-thread React Router grace period; comments call missing-ID fallback safe | First thread could be admitted without verified recipient; arbitrary links, changed panel text and name matching also bypassed identity |
| `42cc0e2` (19/09) | Source-aware notation, evidence columns and DOM regressions; `message-history-notation-audit-2026-09-19.md` and MAS playbook | Legacy claims remained; shared wrapper actor labels still reached child bodies; verifier bypasses remained |
| `ff7e420` (19/09) | Delivery identifies recipients by Page + PSID, not mutable display names | Separate ingestion verifier retained weaker fallbacks |
| `8aed3e4` | Hide unresolved refetch history in mixed timelines | Having a timestamp did not validate a legacy sender claim |
| `6ce76fa` (20/09) | Facebook temporary-block gate | This gate handles account blocking, not message/recipient/time correctness |

The defect was not dashboard CSS. CSS was used as ingestion evidence and the
result was persisted. Existing tests also explicitly expected first-thread and
hovercard fallbacks to succeed without a recipient ID; those expectations have
been reversed. Prior green tests were not sufficient live evidence.

## Read-only observations

The current DB contains 13 rows for this thread, **zero explicit sender
confirmations**, 6 non-null source IDs and 13 non-null canonical times. A
timestamp being populated does not prove it is correct. The old combined body
and later separated Unknown observations remain evidence, not a safe basis for
automatic relabelling. The trace report also contains city-classification calls;
“no inbox reply” must not be misstated as “no model ever saw this history”.

An aggregate query found zero source IDs shared by distinct threads within a
Page. This does **not** prove there was no wrong-recipient ingestion: old rows
without source IDs cannot be checked that way, and two accounts may have the
same display name. Numeric Inbox `selected_item_id` is a Page-scoped recipient
ID (PSID), not automatically a global Facebook profile UID. Do not equate them.

## Changes in this follow-up

- Ingestion verifier requires exact numeric Page and recipient IDs on
  `business.facebook.com` plus the expected visible heading in the same poll.
  Removed first-thread, changed-content, partial-name and arbitrary-link bypasses.
  Heading is corroboration only; it cannot independently establish identity.
- Worker checks identity again after extraction and after a second viewport
  read. It now honors the integrity validator's result before extracting contacts
  or writing messages. No additional history scrolling occurs for the second read.
- New `l1_fetch_integrity` checks per-message source ID uniqueness, explicit actor
  evidence, absolute day/clock agreement and chronological order. Missing evidence
  fails admission. It compares ordered snapshots and existing evidence for the same
  source IDs within a Page; cross-thread ID, explicit-sender and exact-time conflicts
  block the batch rather than overwrite existing history.
- Actor extraction stops before shared wrappers containing multiple bodies. Real
  Chrome regression reproduces Page greeting plus unlabelled “Hỏi chi tiết”; only
  the greeting remains Page. Colour, text and wrapper actor are not substitutes.
- Explicit future dates are no longer silently moved to yesterday. Invalid
  12-hour clocks and future US-format dates are unresolved.
- Worker emits `fetch_integrity_failed` with Page/recipient/thread, message index,
  source ID, affected field and reason. Local `logs/fetch-integrity/conflict-*.json`
  preserves observed/confirmation parser output for offline review (0600 files).
  These are parser observations, not raw DOM or independent ground truth.
- Conflicting batches are not immediately requeued. Fetch returns failure and
  exit 76; the normal loop stops before another scan/classification/MAS cycle.
  Other launchers must honor the nonzero result too. No Telegram/email is sent.
- Targeted outdated-action refresh validates the tail twice and against stored
  evidence; conflict becomes durable `fetch_request=needs_review`. It no longer
  scrolls all history only to return to the bottom immediately afterward.

Separate sender-reader/UI changes were already being edited concurrently in the
shared worktree. They are preserved; this report does not attribute those edits
to the fetch hardening or assert they cover every consumer.

## Verification plan and release limits

Unit: wrong Page/PSID, first thread, name substring, missing actor/day/source ID,
duplicate IDs, order inversion, changed body/sender/time, cross-thread stored IDs,
future/invalid absolute time, no automatic requeue, private replay report.
Integration: worker conflicts must call neither contact extraction nor persistence;
temporary SQLite happy path; targeted refresh; parser JavaScript executes in real
headless Chrome. Existing parser/worker/parallel/gate/history tests are rerun.

This is conservative admission, **not a 100% extraction certification**. The
current Meta UI may expose only an “Inbox” heading, no PSID on discovery cards,
or no actor/time/source ID; those conversations now stop for review. Mutable or
reordered names can cause safe false rejections. Cached name-to-PSID lookup is
still a locator hint, not independent identity proof. A stable wrong DOM can
pass repeated reads; a message ID cannot prove sender by itself.

Remaining acceptance work: capture consented real DOM/source snapshots with
independently annotated Page/PSID, actors and absolute time/timezone; replay each
through parsing, persistence and readers; verify same-name recipients, account
switches, partial/virtualized history and edits. Add a source-bound panel identity
or authorized platform source before removing conservative heading checks.
DB conflict reads are not a cross-process uniqueness constraint; concurrent
writers and alternate direct persistence callers still need a shared database
admission contract. Do not represent this worker gate as that global guarantee.

Legacy repair must retain original observations and separately mark verified
corrections with provenance. Do not relabel by text/colour/LLM or blindly recrawl
all history. Start with the recorded failing observation and affected message IDs.
No live rollout or production migration is claimed by this report.

## Results

Final focused run: **201 passed, 4 subtests passed** across fetch integrity,
delivery guard, worker, inbox pipeline, real-DOM parser, notation, parallel fetch,
Facebook-block gate, conversation state and message evidence. Coverage: new
cross-check module 100%, worker 93%, Python parser 64%, time resolver 85%; total
81%. Python coverage does not measure JavaScript branches; DOM fixtures execute
the parser's JavaScript in Chrome. All scenarios listed above have offline tests.
CLI regression confirms exit 76; targeted-refresh regression confirms the second
invocation does not rescan a needs-review item. Compile, shell syntax and diff
whitespace checks pass.

A broader run including the concurrently edited `test_seekers_ui_improvements.py`
reported two static-assertion failures: obsolete `handleSort('lastMessageDate')`
expectation and changed `displayableMessageHistory` return text. These are not
reported as passing or silently removed. No full-suite or live accuracy claim.
