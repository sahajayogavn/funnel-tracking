# PRD: Parallel Inbox Fetch (`--workers`)

> **2026-09-22 behavior update:** The orchestrator is worker 0 and fetches
> details inline during discovery. Fetch defaults to one total tab; `--worker`
> aliases `--workers`. All modes use the checkpoint engine. Known-ID tasks may
> be dispatched to worker 1/2; worker 0 drains remaining work after discovery.
> Sidebar lookup precedes saved-ID URL fallback. This supersedes the earlier
> idle-orchestrator/default-3 requirements below. See
> [worker-0 implementation](../report/inbox-worker0-2026-09-22.md).

**Universal ID:** `prd:inbox-parallel-fetch-001`
**Design:** `doc:inbox-fetch-pipeline-001` — [`../architect/inbox-fetch-pipeline.md`](../architect/inbox-fetch-pipeline.md)
**Status:** Implemented (Phases 1–3) — live validation (Phase 4) pending
**Date:** 2026-09-16
**Requested by:** operator (page `1548373332058326`)

## 1. Context

`tools/l5_fetch_fb_messages.py --action fetch_messages --cdp` scrapes the
Business Suite inbox in two sequential stages inside one Chrome tab. On a
90-day refresh Stage 1 (sidebar discovery) takes ~3 minutes while Stage 2
(per-thread extraction) takes ~110 minutes, because each thread costs
~15 s of SPA waits and the tab is otherwise idle. Stage 2 cannot begin
until Stage 1 has scrolled to the end of the range.

## 2. Goal

Add `--workers N` (default `4`) so that one orchestrator tab performs
Stage 1 and streams discovered threads into a queue that `N-1` worker tabs
consume immediately, each extracting thread details in parallel.

## 3. Requirements

| ID | Requirement | Priority |
| --- | --- | --- |
| `prd:inbox-parallel-fetch-001:cli` | `--workers N` on `fetch_messages`; default 3; clamped to 1–3; `N` = total tabs (1 orchestrator + at most 2 worker tabs). | Must |
| `prd:inbox-parallel-fetch-001:legacy-mode` | `--workers 1` runs the existing sequential path with no threads, no extra tabs, and identical output/stats keys. | Must |
| `prd:inbox-parallel-fetch-001:stream-dispatch` | The orchestrator enqueues threads as soon as each visible-cards round is parsed; it never waits for Stage 1 to reach the end of the time range before workers start. | Must |
| `prd:inbox-parallel-fetch-001:worker-isolation` | Each worker owns its own Chrome tab (role `scan_inbox_worker:<i>`), Playwright instance and SQLite connection; no Playwright or DB handle is shared across threads. | Must |
| `prd:inbox-parallel-fetch-001:locate-ladder` | A worker locates a thread by direct URL when a PSID is known, otherwise by sidebar identity match; every locate is verified before extraction (URL, header name, preview). | Must |
| `prd:inbox-parallel-fetch-001:output-equivalence` | For the same inbox state, `threads`/`messages`/`users` rows persisted with `N` workers are identical to `N=1` (except `last_synced_at`). `inbox_sort_index` equals the Stage 1 ordinal. | Must |
| `prd:inbox-parallel-fetch-001:fault-tolerance` | Loss of a worker tab re-attaches once and re-queues the in-flight task; a dead worker never loses tasks (the surviving worker tab drains the remainder; the orchestrator tab never fetches — tasks left with no worker are reported as `tasks_stranded`). | Must |
| `prd:inbox-parallel-fetch-001:stop-rules` | `--maxThreads`, `--targetMessages`, `--no-early-exit`, cache-hit early exit and `Ctrl-C` behave as today; workers stop after their current task when a stop is signalled. | Must |
| `prd:inbox-parallel-fetch-001:read-only` | Workers never type, focus the composer, or call `send_reply_via_cdp`; fetching stays read-only (CLAUDE.md §10.3). | Must |
| `prd:inbox-parallel-fetch-001:orchestrator-joins` | After Stage 1 the orchestrator remains idle and waits for all worker tasks and retries to finish. A timed join is only a heartbeat, never a completion deadline. Unfinished tasks are reported with a replayable manifest; incomplete runs cannot stamp success. | Must |
| `prd:inbox-parallel-fetch-001:observability` | Stats add `workers`, `tasks_dispatched`, `tasks_abandoned`, `locate_methods`, `stage1_ms`, `stage2_ms`, `per_worker`; every worker log line carries `[worker:i]`; tasks/results are logged at `DEBUG` as `logs:inbox-parallel-fetch-001:*`. | Should |
| `prd:inbox-parallel-fetch-001:speedup` | On a 90d `--refresh` with `--workers 3`, Stage 2 wall time ≤ 1/3 of the `--workers 1` baseline. | Should |
| `prd:inbox-parallel-fetch-001:post-scrape` | `record_fetch` runs only after all workers finish with complete histories and no failed/abandoned/stranded tasks. Partial persistence is reported explicitly. Classification may consume only admitted evidence. | Must |

## 4. Out of scope

- Parallelising Stage 1 itself (sidebar discovery is ~2 % of the run).
- Headless Mode 3 (`fb_credential_*.json` launch) — stays sequential.
- Inbox search-box based locate (needs its own spike).
- Fixing the Stage 1 provisional-identity mismatch (`bug:inbox-thread-identity-001`, tracked separately).

## 5. Acceptance criteria

1. `pytest tests/` green, including new `tests/test_l3_inbox_worker.py` and `tests/test_l3_parallel_fetch.py`.
2. Hung Bui snapshot gate passes for `--workers 1` and `--workers 3` (two consecutive runs each, identical `tests/hungbui_test_output.json`).
3. 7d live run: DB diff between `--workers 1` and `--workers 3` is empty except `last_synced_at`.
4. 90d live run: speed-up measured and recorded in `doc:inbox-fetch-pipeline-001` §1.

## 6. Traceability

| Requirement | Design section | Code ID | Test ID |
| --- | --- | --- | --- |
| `…:cli`, `…:legacy-mode` | §3.1, §8, §10 Phase 3.4 | `code:inbox-parallel-fetch-001:cli` | `code:test-inbox-parallel-fetch-001:cli` |
| `…:stream-dispatch`, `…:orchestrator-joins`, `…:stop-rules` | §3.3, §7, Phase 3.1–3.3 | `code:inbox-parallel-fetch-001:orchestrator`, `…:stop-rules` | `code:test-inbox-parallel-fetch-001:orchestrator` |
| `…:worker-isolation`, `…:fault-tolerance` | §6, §7, Phase 2.4, 3.2 | `code:inbox-parallel-fetch-001:worker-main`, `…:worker-session` | `code:test-inbox-parallel-fetch-001:worker-main` |
| `…:locate-ladder` | §5, Phase 2.1–2.3 | `code:inbox-parallel-fetch-001:locator-direct`, `…:locator-sidebar`, `…:psid-hint` | `code:test-inbox-parallel-fetch-001:locator` |
| `…:output-equivalence`, `…:read-only`, `…:post-scrape` | §8, Phase 1, Phase 4 | `code:inbox-parallel-fetch-001:thread-worker` | Hung Bui gate + Phase 4 DB diff |
| `…:observability`, `…:speedup` | §7, Phase 4 | `code:inbox-parallel-fetch-001:orchestrator` | Phase 4 log |
