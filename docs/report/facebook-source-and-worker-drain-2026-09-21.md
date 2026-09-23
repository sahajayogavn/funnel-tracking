# Facebook source extraction, pending history, and worker drain

## Scope and root causes

Implemented the operator's three requests: trustworthy message extraction,
visible unresolved history in detail/sidebar, and completion of the Stage-2 queue.
No DB deletion, sender backfill by text, external message sending or LLM call.

Prior fixes did not cover the complete ingestion lifecycle:

- `f3ecd92` / subsequent CSS heuristics and quote flattening could attribute a
  cluster's text to one actor. `42cc0e2` added source-aware fields but did not
  repair historical merged rows. Current fixes preserve uncertain legacy data.
- `014b3ba` hardened identity/integrity admission. Missing actor/time evidence
  then became observations rather than canonical messages, but the dashboard
  read only canonical messages and incorrectly looked empty.
- `b106031` introduced the `join(timeout=120)` worker waits. These were used
  as if they established completion. With daemon workers, return could strand
  queued tasks while no successful history was persisted. The idle orchestrator
  is not itself a bug: it can remain idle if workers are allowed to finish.

## Source contract

Live Facebook inspection found `memoizedProps.message` on ancestors of each
DOM message node's React fiber. The allowlisted data includes `messageID`,
`sender.userID`, `timestamp` (epoch milliseconds), `textPayload.text`, and the
containing props' `viewerID`, `participants[].userID`, `isFromViewer`.
No whole fiber/app store, tokens, profile photos or private APIs are extracted.

`facebook_message_source.py` validates the node/model ID, Page/recipient URL,
exact two-participant set, sender membership, viewer-direction agreement,
body vs rendered text and epoch validity. It never accepts a source failure
by falling back to CSS. Original epoch milliseconds are retained in evidence;
stored times preserve seconds in `Asia/Ho_Chi_Minh`, independent of host timezone.
Facebook emoji images require their `alt` text during body comparison; the first
La Pham live trial caught this as a conflict and wrote **nothing**. A regression
now exercises this representation and the corrected trial passed.

Text-only source messages are supported. Media, templates and reply models
whose shape has not been validated remain observations, not guessed messages.
Existing reaction evidence can transfer only on the exact source message ID.
This source is undocumented and may change: quarantine/report is the intended
failure mode. Passing sampled cases is not proof of universal 100% coverage.

The worker still verifies thread identity before/after reads, requires a stable
second snapshot, rejects cross-thread source-ID reuse or stored sender/time
contradictions, and preserves unresolved observations separately from MAS input.
Existing rows with conflicting old sender/time evidence are **not** silently
overwritten. No full-history recrawl or DB wipe is needed to inspect a saved
observation/report.

An exact source epoch may refine an older AM/PM minute-only label within the
**same minute**, same source ID, thread and sender. Synthetic `:00` seconds in
that old label are not independent evidence. This does not allow a different
minute/day, a sender change, a cross-thread ID, or changing a previously stored
source epoch; these remain hard conflicts. Tests cover all those boundaries.

## Dashboard

`getSeekerById` reads the latest observation scoped to the exact thread ID.
The shared `PendingMessageHistory` section appears in both detail and sidebar,
with neutral layout, reasons and the **scan** time clearly distinguished from
send time. No guessed sender/date is forwarded in the presentation contract.
Admitted explicit source IDs suppress matching observations, never matching by
body text or name. Unmatched legacy evidence remains visible for review;
malformed observation JSON shows an error rather than silently disappearing.
React escapes text, including HTML-like content. Canonical message counts and
MAS inputs do not include these observations.

## Worker drain

- Removed both fixed 120-second completion waits; joins now poll live workers.
- Workers are non-daemon; Stage-1 errors stop dispatch and cleanly drain/exit.
- Stage-2 interruption requests stop, waits for current tasks and records partial
  results before propagating the interruption.
- `fetch_complete` is false for failed/no-message/review results, partial
  histories, abandoned tasks or stranded retries. No successful fetch marker
  is written for those runs.
- Assignment logs include complete task records for unfinished work. This is
  a replay manifest for an operator/tool, not an automatic crash-resume service.
  No global deadline is imposed; a hung worker remains visibly running and
  must be interrupted/diagnosed, never misreported as complete.

## Validation and actual data changes

- Live read-only sampling of 9 existing Facebook tabs: 81 conversation/observation
  rows, 70 source-verified, 11 pending, 0 hard integrity contradictions after the
  emoji fix. This is loaded DOM coverage, not a claim that all Facebook history
  was scrolled or the entire 1,000-thread fetch completed.
- Targeted normal worker refresh of **La Pham, seeker 14436**, exact recipient
  `61577776390883`: 4 messages persisted, 1 system event stored separately.
  Senders: Page, Customer, Page, Customer. Source times on 2026-09-06:
  02:45:24, 02:45:25, 02:45:28, 02:45:55 (Vietnam timezone).
- Actual dashboard query confirms 4 messages and 1 unmatched old observation
  (`La Pham replied to an ad.`). That old row has no source ID/kind, so it remains
  neutral review evidence rather than being silently relabelled or deleted.
- **Additional legacy-data defect found, not repaired silently:** read-only
  comparison for seeker **14351 (Nguyễn Ngọc Giàu)** found 7 stored timestamps
  disagreeing with exact source messages. Examples: 2023-11-10 16:50:00 vs
  16:51:14; 2026-09-14 08:04:00 vs 08:35:22; 2026-09-15 17:54:00 vs 17:59:35.
  This is consistent with inherited cluster time labels, not a seconds-only
  precision refinement. Report: `logs/fetch-integrity/conflict-skky4vy3.json`.
  No rows of this seeker were rewritten. A new refresh will reject/report these
  conflicts until an audited historical-data repair is performed. The worker's
  error payload now includes stored-comparison issues (previously only the
  pre-comparison issue list was included, hiding this failure's explanation).
- Tests include source UID/message ID/body conflicts, stale participant sets,
  missing/future/invalid timestamps, unsupported payloads, emoji DOM execution,
  cold-start browser-to-SQLite persistence, virtual 300-second worker wait,
  all 86 queued tasks including ordinal 84, and dead-worker partial manifests.
- `node tests/test_pending_history.cjs`: presentation assertions pass.
- `npm run build`: production compilation and TypeScript pass.
- Final relevant Python regression run: **268 passed** in 8.71 seconds.
  Coverage: source extraction 98%, integrity contract 98%, parallel fetch 86%
  (89% combined). Browser execution also checked the pending-history component
  at a 430px viewport with no horizontal overflow.
- Additional pipeline/history/conversation/UI suite: 95 passed, 1 pre-existing
  failure in `test_seekers_table_last_message_datetime_sorting`. The test expects
  a clickable `lastMessageDate` header absent in the **HEAD** version of
  `seekers-table.tsx`; neither that component nor test was changed in this task.

Full 86/1,000-thread live refresh is **not** claimed as completed. Refresh the
dashboard to see La Pham's verified messages; run a new fetch process to load
the new parser/drain code for remaining threads.
