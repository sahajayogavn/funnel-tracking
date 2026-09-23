# Fetch loop review — 2026-09-22

## Live verification

Resumed `tools/run_inbox_mas_loop.sh fetch` with `FUNNEL_FETCH_TIME_RANGE=15d`, default 900-second cycle, 3 workers, no forced refresh. Historical 720d backfill was not started.

First cycle: 00:06:18–00:06:32. Exit 0 (runner reached its sleep branch without a fetch-failure status), fetch complete, no failed/abandoned/stranded tasks, no new messages. QA report `logs/fetch-qa/1548373332058326-20260922-000631.json`: passed, hard 0, soft 0, cards 10, QA-1 10/10 pass, QA-2 10/10 pass, time deltas 0.082–0.88 seconds. This verifies the unchanged-inbox path; no new extraction was exercised.

First scheduled observation at 00:11:18 confirms accepted=true and loop alive. Loop PID 2850; observer PID 7487. The read-only observer runs every 300 seconds and appends explicit acceptance checks to `logs/fetch-monitor-2026-09-22.jsonl`; fetch output is in `logs/fetch-loop-2026-09-22-monitor.log`. Observer source: `logs/monitor_fetch_20260922.py`. It stops after observing the loop has exited. It does not fix failures, send alerts, or restart the loop. Future observations are automated and are not yet reviewed by the assistant.

## Review findings still open

1. **Coverage is not a stop gate.** `sample_top_cards` only warns below 10 cards, and `_qa_logic` derives status only from mismatch counts. An undersampled report can still pass. The observer independently requires 10 cards and 10 results in each QA section.
2. **Unknown rank-1 card is unconditionally soft.** `_qa_logic` uses `rank == 1 or raced` when identity cannot be resolved; rank alone is not evidence of new activity. The CLI stops on `failed`, not `warn`. Thus a run can exit 0 despite not meeting the requested soft=0 acceptance condition.
3. **Duplicate names are heuristically resolved.** Nearest stored rank plus matching content/sender/time reduces ambiguity but does not prove identity if two same-name threads also have identical last-message evidence. The Round 4 assertion that a wrong pick cannot silently pass is too strong.
4. `summary.pass` stays 0 even when all checks pass; inspect individual verdicts to count passing checks.

No production code was changed in this review.

## Tests

`bash -n tools/run_inbox_mas_loop.sh` passed. Eight fetch suites passed 196 tests; supplemental CLI/notation suites passed 6 and failed 6, totaling 202 passed / 6 failed. This does not reproduce the previous handover's 216-pass claim.

Logs: `logs/fetch-tests-2026-09-22-monitor.log`, `logs/fetch-tests-extra-2026-09-22-monitor.log`.

One CLI failure is an unconfigured mock `page.is_closed()` triggering the QA reattach path and violating the expected attachment count; its mocked QA also logs a MagicMock/int comparison error. Five headless tests return success=false; their root causes have not been established. These failures were observed on the existing working tree before any production edits.

Conclusion: the new live cycle meets the requested numerical acceptance checks. The broader claim that every QA gate is complete, or that all fetch tests pass, is not established.
