# Empty-DB fetch regression — 21/09/2026

**Follow-up: fixed zero seekers after unresolved fetch.** The initial change
below saved only observations; the dashboard reads `threads LEFT JOIN users`,
so it still displayed zero contacts. `save_unresolved_observation` now atomically
upserts the verified Page/PSID identity into `threads` and `users` along with the
observation. It validates the canonical thread hash, refuses conflicting stored
Page/recipient IDs, and rolls back the entire operation on a conflict. Existing
phone/city/program/stage fields are preserved. New `last_interaction` stays NULL;
`fetched_at` is not stamped because complete history was not verified.

`seekers_saved` is reported separately from canonical message counts and
`threads_needs_review`. Incomplete messages remain quarantined; creating a seeker
does not promote their sender or datetime into verified facts.

The existing verified observation was replayed locally without another crawl,
creating **Dinh Nguyen Thi #14349**. Two bounded live worker checks created
**Trader Nguyễn #14350** and **Nguyễn Ngọc Giàu #14351** with their separately
verified recipient IDs. Executing the dashboard's actual `getAllSeekers()` against
the configured PostgreSQL database returned **3 contacts**. This verification
used the server query directly; HTTP `/seekers` requires the user's login.
The page is `force-dynamic`, so reload reads the new rows. No full 1000-thread
run, inferred contact details, or message relabelling was performed.

Regression additions cover dashboard base-table visibility, idempotent identity
insertion, existing profile preservation, canonical-ID validation and atomic
rollback on stored Page/recipient conflicts.
Final follow-up run: **180 tests passed**; observation/identity writer coverage
100%, worker 93%, combined 94%; `git diff --check` passed.

## Evidence and cause

Run `logs/parallel-fetch/assignments_20260921_113719.jsonl` dispatched 86 tasks,
attempted 12, abandoned 74, persisted zero. Each attempted thread used
`sidebar_identity`. `logs/fetch_fb_messages.log` at 11:36:54–11:37:19 records
`missing_numeric_page_or_recipient_id` for all twelve, then both workers retire
after six failures. QA's nine hard/one soft findings are downstream missing-data
symptoms, not evidence that clicking a sidebar card failed.

Commit `014b3ba` introduced an invalid cold-start dependency: the verifier
required a numeric PSID before reading the post-click URL, while the worker only
assigned that PSID after successful verification. New cards commonly expose no
PSID and the empty DB supplies no cache hint. The prior tests verified rejecting
missing IDs but omitted the discover-ID-then-verify path.

Live DOM inspection additionally confirmed the conversation title uses
`div._4ik4._4ik5`; semantic headings often expose only `Inbox`. Sidebar rows use
the same title class, so they must explicitly be excluded from panel evidence.

## Implementation

- `ThreadRecord.identity_discovery_clicked` is ephemeral, initially false.
  The locator sets it only after its own successful click, resets it on entry,
  and requires one visible same-name card for discovery without a PSID. Duplicate
  names are not resolved by choosing the first match. Known-ID preview fallback
  also requires a unique match.
- `verify_thread_switch` permits discovery only with that click proof, a numeric
  Page ID, one numeric selected PSID on business.facebook.com, and the expected
  panel name. Two consecutive observations must agree. Sidebar/list/tab titles
  are excluded; a known PSID cannot silently change. The verified ID then feeds
  the canonical thread ID and the worker's post-read checks.
- Verification failures now carry a reason in the assignment result, rather
  than returning an empty error under `click_verify_failed`.
- Live probe exposed a second limitation: the parser could not establish sender
  or absolute day for the visible messages. Rejecting every incomplete observation
  as a worker failure would still retire the pool. Missing evidence now yields
  `needs_review` and saves raw structured observations in
  `inbox_fetch_observations`, **not** canonical `messages` or seeker contacts.
  Snapshot changes, duplicated message IDs and recipient conflicts remain hard
  failures. Both complete and incomplete snapshots get the second viewport read.
- Review items do not retire/requeue workers. Run stats report
  `threads_needs_review` and include review details in `failed_threads`, keeping
  the CLI non-success/exit-76 behavior so unattended loops cannot silently
  accept incomplete history or automatically repeat the full scan.
- Additive PostgreSQL migration
  `db_migrations/2026-09-21_inbox_fetch_observations.sql` was applied locally.
  SQLite setup creates the same table. Observations use a hash for idempotence,
  numeric Page/recipient binding, UTC observation time and JSON evidence.

## Verification

178 focused tests pass: integrity, locator, worker, pipeline, DOM parser,
notation, parallel fetch, SQLite persistence and delivery guard. Coverage of
the selected modules is 81% overall: locator 78%, Python parser 68%, worker
93%, cross-check 100%, observation writer 100%. JS coverage is not measured by
coverage.py; the actual scripts execute in Chrome fixtures.

New tests cover cold-start PSID acquisition; stale/wrong panel; unstable polls;
duplicate visible names; sidebar name mistaken for panel name; idempotent
quarantine; worker health/retry behavior; and empty SQLite → actual sidebar JS
click → URL discovery → panel verification → parser → canonical message insert
when explicit actor/time/ID evidence exists. Whitespace checks pass.

Live probe started with an empty PSID hint and selected **Dinh Nguyen Thi**,
discovering **100004924655726**. The actual worker completed with `needs_review`,
saved one observation containing seven parsed events for canonical thread
`1548373332058326_3684be99173d0160`, and did not write unverified canonical
messages. Observation ID:
`23bc2503d16a9af973082cba5e4a66a32c99f63cd35ad11d9ca8f937e0e2f030`.
Local report: `logs/fetch-integrity/conflict-d7k4li70.json`.

This fixes cold-start rejection and preserves incomplete evidence. It does not
claim the live parser now supplies verified senders/dates for every message, nor
that a full 86/1000-thread production run passed. The dashboard/MAS do not yet
consume the quarantine table. Further parser work must use authoritative source
actor/time evidence, not infer it from colour or prose. Existing concurrent
orchestrator/worker scheduling edits were preserved and tested, not replaced.
