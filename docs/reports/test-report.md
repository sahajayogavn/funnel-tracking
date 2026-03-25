# QA Test Plan & Validation Report

**Universal ID**: `doc:test-report-001`
**Date**: 2026-03-25
**Scope**: ADK inbox draft-only safety, latest-customer-turn draft suppression, QA regression coverage, and documentation alignment.

## 1. Change Set Under Test

- Inbox browser reply safety in `fb_pipeline/browser/l2_actions.py` so customer replies are typed only and never auto-sent.
- Inbox wrapper and runner semantics in `adk_agents/tools/l5_facebook_tools.py` and `tools/l5_inbox_mas_runner.py` so successful execution ends at drafted text for human review.
- `auto_replies` audit persistence and latest-customer-turn acknowledgement behavior in `fb_pipeline/persistence/l4_sqlite_store.py` and `adk_agents/tools/l5_seeker_tools.py`.
- Sanitization-empty handling so `no_reply` does not type text and does not create false suppression.
- Documentation and QA alignment in `docs/ARCHITECTURE.md`, `memory/mas_strategy.md`, `CLAUDE.md`, `GEMINI.md`, `web/README.md`, and this report.

## 2. QA Test Plan

### 2.1 Objectives

1. Prove no production inbox path can press Enter or auto-send a customer reply.
2. Prove a successfully typed draft is logged as a draft acknowledgement for the latest customer message boundary.
3. Prove a drafted reply suppresses repeat drafting until a newer customer message arrives.
4. Prove sanitization-empty and operational failures remain safe and do not create false suppression.
5. Verify docs describe draft-only inbox behavior rather than legacy live-send behavior.

### 2.2 Test Matrix

| Area | Risk | Validation method | Acceptance criteria |
| --- | --- | --- | --- |
| Low-level browser safety | Inbox automation still presses Enter | `tests/test_l2_inbox_draft_safety.py` | `send_reply_via_cdp()` types successfully and never calls Enter/send |
| Runner draft semantics | Runner still treats inbox replies as sent/live | `tests/test_l5_inbox_mas_runner.py` | Successful processing ends at `drafted`; sanitization-empty returns `no_reply` without typing |
| Wrapper audit logging | Wrapper still exposes send semantics or misses customer boundary | `tests/test_l5_facebook_tools.py` | Wrapper is draft-only and persists latest customer-message acknowledgement fields |
| Draft suppression query | Same thread is redrafted every cycle | query-focused inbox tests for `find_unreplied_threads()` | Draft acknowledgement suppresses repeats until a newer customer message arrives |
| Persistence migration | Existing DB misses new acknowledgement column | `tests/test_l4_inbox_persistence.py` | `auto_replies.customer_message_timestamp` exists and stores the latest customer boundary |
| Hung Bui / docs safety alignment | Browser QA or docs still imply customer auto-send | targeted grep/file review and Hung Bui-focused E2E wording checks | Hung Bui-only safety remains intact and docs describe draft-only inbox behavior |

### 2.3 Execution Order

1. Write QA plan.
2. Run focused regression tests for low-level draft safety, runner semantics, wrapper logging, persistence migration, and query suppression behavior.
3. Run any safe E2E or smoke coverage that validates draft-only behavior without sending customer messages.
4. Re-scan docs for stale live-send wording and Hung Bui safety drift.
5. Record results and QA signoff.

## 3. Execution Results

### 3.1 Automated tests

Executed on 2026-03-25:

1. Focused regression suite
   - Command: `.venv/bin/python -m pytest tests/test_l2_inbox_draft_safety.py tests/test_l5_inbox_mas_runner.py tests/test_l5_facebook_tools.py tests/test_l4_inbox_persistence.py tests/test_l5_inbox_query_actions.py tests/test_telegram_notify.py -v`
   - Result: **34 passed**
   - Coverage intent confirmed:
     - low-level inbox reply typing never presses Enter, even when legacy live-style flags are passed
     - runner semantics end at `drafted`, with `no_reply` and `draft_failed` staying safe
     - wrapper logging persists `customer_message_timestamp` when available
     - draft acknowledgement suppresses repeat drafting until a newer customer message arrives
     - schema migration supports `auto_replies.customer_message_timestamp`

2. ADK E2E smoke suite
   - Command: `.venv/bin/python -m pytest tests/test_adk_e2e.py -v`
   - Result: **10 skipped**
   - Reason: expected guardrail behavior when `OPENAI_API_BASE` / `OPENAI_API_KEY` are not set in the test environment

### 3.2 Documentation review

Performed targeted grep and file review after edits.

Verified outcomes:

- `docs/ARCHITECTURE.md` and `web/README.md` describe inbox handling as draft-only typing for human review.
- `CLAUDE.md` and `GEMINI.md` require Hung Bui-only browser E2E targeting and manual human send for inbox replies.
- `memory/mas_strategy.md` describes Inbox MAS as draft-only rather than auto-send.
- No touched user-facing or rule markdown file still claims that `tools/inbox_mas_runner.py --live` sends customer inbox replies.

## 4. QA Signoff

**Status**: PASS

QA signoff is complete for this change set. Focused draft-only regression coverage passed, ADK smoke coverage skipped cleanly due to missing credentials, and the touched docs now align with the non-send boundary.

## 5. Signoff Rule

QA signoff requires all focused draft-only regression tests to pass, any safe smoke/E2E checks to pass or skip cleanly due to missing credentials, and no remaining doc drift in the touched user-facing or rule files.
