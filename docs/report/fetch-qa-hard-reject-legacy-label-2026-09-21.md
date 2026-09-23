---
id: bug:inbox-fetch-qa-legacy-label-001
date: 2026-09-21
status: fixed (pending live QA confirmation)
---

# Fetch QA hard reject — "Hoang An Minh" (sidebar preview ≠ stored content)

## Symptom

`./tools/run_inbox_mas_loop.sh fetch` stopped with Fetch QA `failed`, 1 hard reject:
thread `1548373332058326_a156cbe53c57970f`. The sidebar preview carried the Page
reply with emoji (`🌺 Hẹn gặp bạn … 🏠 … 🧘 … ✨ … 🙏`), the stored row had the same
text with every emoji missing. Report: `logs/fetch-qa/1548373332058326-20260921-210905.json`.

## Root cause chain (three defects, one visible symptom)

1. **Stale legacy row.** The stored row was written by the pre-source-model DOM
   parser (`innerText`, which drops `<img alt="🌺">` emoji). The new
   `facebook_bound_message_v1` path updates rows in place by `source_id`, so a
   successful re-fetch would have corrected it — but the re-fetch never landed.
2. **Integrity gate rejected every legacy thread** (`stored_evidence_conflict`,
   field `datetime`, 176 occurrences across 32 threads). Legacy rows store the
   DOM *cluster* label (divider / first-bubble hover, minute precision), while the
   source model yields the exact per-message epoch. All 176 epochs were **60 s –
   2 h later** than the label; `compare_stored` only tolerated the same minute.
   The worker retires on a quality reject (by design), so 74 threads were
   abandoned and Hoang An Minh was never refreshed.
3. **Stage 1 early exit after an aborted run.** The next run skipped two
   re-marked threads at the top and early-exited ("2 consecutive clean cards"),
   never reaching rank 5. That heuristic is only sound when the previous run
   completed.
4. (Found on the re-run) **`source_body_dom_conflict` on emoticons.** Facebook
   paints `:)` as `<img alt="🙂">`, so `textPayload.text` ≠ rendered DOM text
   (`Khanh Van Quach`, `mid.$cAAXaz0r-l1tr3lmEqFlrRQxtx-R-`).

## Fixes

| File | Change |
|---|---|
| `fb_pipeline/contracts/l1_fetch_integrity.py` | `legacy_label_refinement`: a source epoch may move a non-source (legacy label) row **forward** by up to 24 h; earlier-than-label, >24 h, or epoch-vs-epoch disagreement still fail closed. |
| `fb_pipeline/inbox/l3_sync_decision.py` | `previous_fetch_incomplete()`: `MAX(threads.fetched_at) > last fetch_log.fetched_at` proves an aborted run. |
| `fb_pipeline/browser/l3_inbox.py` | Stage 1 disables early exit for the run when the previous run was incomplete. |
| `fb_pipeline/browser/inbox/facebook_message_source.py` | Body/DOM guard compares letters+digits only (still catches a model bound to the wrong node); conflicting `dom_text` is now kept as `source_dom_text` for the report. |
| tests | `test_facebook_message_source.py`, `test_l3_sync_decision.py` regression cases. |

## Verification

- `pytest tests/test_facebook_message_source.py tests/test_fetch_integrity.py tests/test_decoupled_fetch_qa.py tests/test_l3_inbox_worker.py tests/test_l3_sync_decision.py` → all pass.
- Live run 2 (after fixes 1–3): Hoang An Minh `status=persisted`; run aborted on defect 4.
- Live run 3 (after fix 4): see the appended result below.

## Result (appended 21:45)

- Run 3 persisted Hoang An Minh from the source model; stored body now starts
  `🌺 Hẹn gặp bạn …`, evidence `facebook_bound_message_v1`, `message_at 2026-09-15 09:17:58`.
  The original QA-2 hard reject is resolved at the data level.
- Run 3 then hard-stopped on a second emoticon variant (`<3` → `❤`, thread
  `53b792ac20d144e2`); fixed by stripping Facebook's emoticon set (see
  `docs/report/fetch-qa-gate-review-2026-09-21.md`, F3). Run was stopped by hand;
  no further live run started pending the gate review.
- Also fixed on review: Fetch QA / incomplete run now exit 76 (previously exit 0).
