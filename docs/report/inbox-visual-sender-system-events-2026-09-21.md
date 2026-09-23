# Inbox visual sender and system interactions — 2026-09-21

Implemented at the user's request: use the left/right painted message body and
its colour as actor evidence; retain centre/system rows and their ad/post
interactions independently of conversational messages.

## Changes

- Measure the painted body relative to the message region; left = Customer,
  right = Page. Preserve side/background/gradient evidence. If an identical
  colour/gradient occurs on both sides, or an explicit label contradicts the
  side, quarantine the attribution instead of silently guessing. Existing
  explicit/layout/avatar fallback remains for bodies without measurable paint.
- Quotes, sibling bodies and shared wrappers do not supply the body's paint or
  a system event's target link. Centred unpainted rows without a message ID or
  explicit human label are System events. Recognised operational banners are
  also System; identical words inside a painted side bubble stay human speech.
- Persist system events separately in `inbox_system_events`, including raw text,
  timestamps and source links. Extract Facebook ad/post URLs and data-ad-id /
  data-post-id attributes local to the event. Keep missing targets nullable.
  Ad IDs populate `user_ad_ids`; post IDs are never misfiled as advertising IDs.
- Network reads exact event targets and adds ad/post → seeker edges. Unknown
  targets remain stored observations, without manufacturing a post node.
- A verified system event can be saved even when human-message evidence is
  incomplete. It is excluded from contact extraction and the message timeline.
- Persist `threads.fetch_history_complete`; incomplete histories force fetch
  before either token or preview-based skipping. Partial saves clear the normal
  completion marker. Existing caller-facing worker status remains `persisted`
  when some rows were saved; completeness is independently tracked.

## Live evidence and migration

Read-only extraction of five already-open Inbox tabs: 61 human messages
attributed to Page/Customer, 8 System events, zero Unknown in those snapshots.
No clicking, scrolling, full recrawl, outbound messaging or sender backfill.
These snapshots do not certify all historical or future Meta layouts.

Applied additive PostgreSQL migration for the system-event table/index and
fetch-completeness column. Replayed historical observations idempotently:
113 system events, including 91 ad/post interactions and 22 other system events.
All 113 lack target IDs in the retained source data. The live banner samples
also exposed no ad/post link. The code preserves this gap rather than guessing
an ad/post ID from an unrelated message or thread label.

## Validation

Real headless Chrome fixtures cover light/dark/gradient bubbles, left/right
placement without ARIA/avatar labels, quotes, conflicting colour evidence,
centred system rows, ad/post source links and shared-wrapper boundaries.
SQLite regressions cover idempotent event persistence and distinct post/ad ID
namespaces, worker admission, and bypass prevention for incomplete histories.
TypeScript check passes.

The expanded test run reported 176 passed and one failure in the pre-existing
clock-only notation expectation: `test_parser_does_not_infer_a_day_from_a_clock_only_label`.
The working tree already resolved clock-only labels against crawl time before
this change. That time-resolution behavior is untouched; the old test expects
`9:00 AM` to remain unresolved. This is not reported as a fully green suite.
