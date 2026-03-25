# Architecture Decisions — Safe Inbox Reply Drafting

**Universal ID**: `doc:architecture-decisions-001`
**Date**: 2026-03-25
**Scope**: ADK inbox MAS update for customer-reply safety, draft semantics, unreplied-thread gating, and QA.

## 1. Decision Summary

This sprint changes the inbox MAS from **"auto-reply pipeline with optional live send"** to **"draft-only pipeline with mandatory human send"**.

### Final product rule

1. The system may generate a reply and type it into the Facebook inbox composer.
2. The system must **never** press Enter, click Send, or trigger any equivalent send action for customer inbox replies.
3. A human operator must manually review the drafted text and manually send it.
4. This rule applies in all runtime modes, including CLI runs, polling loops, ADK tool calls, and browser E2E tests.

This supersedes any current code path or doc text that implies a "live send" mode for inbox replies.

---

## 2. Reply-Flow Semantics by Production File

### 2.1 `tools/l5_inbox_mas_runner.py`

**Current reality**
- `process_single_thread()` calls `send_reply_via_cdp(..., dry_run=dry_run)` and still supports `--live` as a mode that may send. See `tools/l5_inbox_mas_runner.py:332` and CLI flag text at `tools/l5_inbox_mas_runner.py:493`.
- `run_inbox_cycle()` treats `find_unreplied_threads()` output as actionable customer work and then processes each thread. See `tools/l5_inbox_mas_runner.py:424`.

**Required semantic change**
- `process_single_thread()` must become **draft_reply_for_human_review** in behavior, even if the function name stays unchanged.
- `dry_run` must stop meaning "test mode" and start meaning "non-send mode" only if kept at all.
- Preferred implementation direction: remove the inbox-specific meaning of `--live` entirely, or keep the flag accepted but ignored with a warning for backward CLI compatibility.
- Result status should distinguish:
  - `drafted` = reply successfully typed into composer, not sent
  - `no_reply` = sanitized reply empty or agent decided not to reply
  - `nav_failed` / `draft_failed` = operational failures
- Logging text must stop saying "sent" or implying automation-owned delivery.

**Architecture rule**
- The runner owns: fetch → identify actionable thread → generate reply → sanitize → navigate → type draft → audit log.
- The runner does **not** own the final send action anymore.

### 2.2 `adk_agents/tools/l5_facebook_tools.py`

**Current reality**
- Wrapper docstring still says live mode types and sends. See `adk_agents/tools/l5_facebook_tools.py:28-41`.
- `log_auto_reply()` currently inserts into `auto_replies` with `dry_run` flag. See `adk_agents/tools/l5_facebook_tools.py:58`.

**Required semantic change**
- `send_reply_via_cdp()` in this wrapper must be documented and treated as **draft-only**.
- The wrapper must no longer expose "send" semantics to ADK agents.
- Rename in docs/comments/tests toward "draft" terminology even if code symbol stays temporarily unchanged.

**Architecture rule**
- ADK tools may request: navigate to thread, type draft, log draft.
- ADK tools may not request: send message.

### 2.3 `fb_pipeline/browser/l2_actions.py`

**Current reality**
- `send_reply_via_cdp()` presses Enter when `dry_run=False`. See `fb_pipeline/browser/l2_actions.py:26-33`.

**Required semantic change**
- This module becomes the hard safety boundary.
- `send_reply_via_cdp()` must never press Enter for inbox replies under any caller path.
- Preferred direction:
  - keep typing behavior
  - remove Enter keypress path entirely for inbox reply usage
  - return success when text is typed and remains unsent in the composer
- If a generalized browser typing helper is needed later, a separate explicitly named function can exist, but the inbox MAS path must not call any send-capable primitive.

**Architecture rule**
- The lowest reusable browser action used by inbox MAS must itself be non-sending. Safety must not rely only on caller discipline.

### 2.4 Unreplied-thread detection in `adk_agents/tools/l5_seeker_tools.py`

**Current reality**
- `find_unreplied_threads()` currently suppresses work only if there is a **live** auto reply row (`dry_run = 0`) newer than the message. See `adk_agents/tools/l5_seeker_tools.py:104-118`.
- Because typed-but-unsent drafts are logged as `dry_run=1`, the same thread can keep being selected every cycle.

**Required semantic change**
- "Unreplied" must be redefined as:
  - actionable when there is a newer customer message than the latest recorded draft-or-send decision for that thread
  - not actionable when the latest customer message has already been drafted and no newer customer message has arrived
- Suppression must be keyed to the **latest customer message boundary**, not to whether a human actually sent the draft.

**Architecture rule**
- The MAS should draft **once per latest customer-turn**.
- A later new customer message re-opens the thread for processing.
- Human manual send is outside MAS visibility and must not be required for suppression.

---

## 3. Minimal DB and Query Behavior

## 3.1 Problem to solve

A typed-but-unsent draft should not be regenerated every polling cycle.
A later customer follow-up must make the thread actionable again.

## 3.2 Minimal persistence rule

The minimum acceptable persistence model is:
- log every successfully typed draft in `auto_replies`
- treat a draft row as an acknowledgement of the latest customer message snapshot
- use newest-customer-message time vs newest-draft time to decide whether the thread needs another draft

## 3.3 Minimal schema change

Keep `auto_replies` as the primary audit table, but add one field that records the customer-message boundary the draft answered.

### Required addition
- Add `customer_message_timestamp TEXT` to `auto_replies`

### Optional but recommended addition
- Add `message_count_snapshot INTEGER` to `auto_replies`

`customer_message_timestamp` is the real minimum needed. It lets the query answer:
- "Has the latest customer message already been drafted?"

## 3.4 Minimal logging contract

When `process_single_thread()` types a draft successfully, `log_auto_reply()` should store:
- `thread_id`
- `reply_text`
- `agent_name`
- `escalated`
- a new status meaning drafted/not sent
- `customer_message_timestamp` = timestamp of the newest customer message present in the thread when the draft was created

### Recommended interpretation of `dry_run`
Current `dry_run` is too overloaded.

Preferred direction:
- replace `dry_run` usage for inbox replies with a clearer state field such as `delivery_state` = `drafted`

Minimum-change fallback:
- keep `dry_run=1` for drafted replies
- but do **not** use `dry_run=0` as the only suppression condition anymore
- suppression logic must inspect latest draft rows regardless of `dry_run`

## 3.5 Query rule for actionable threads

The actionable-thread query should be based on these steps:

1. Compute each thread's latest customer message timestamp.
2. Compute each thread's latest draft/send acknowledgement timestamp, specifically the latest `auto_replies.customer_message_timestamp`.
3. Select the thread when:
   - it has at least one customer message, and
   - latest customer message timestamp is greater than latest acknowledged customer-message timestamp, or no acknowledgement exists.

### Required behavior examples

| Situation | Should thread be selected next cycle? |
| --- | --- |
| Customer message arrives, no draft exists | Yes |
| Draft typed for latest customer message, human has not sent yet | No |
| Human later manually sends same draft | No change needed |
| Customer sends another message after draft | Yes |
| Draft sanitize result is empty and thread logged as `no_reply` without typed text | Decision needed; preferred: do not suppress permanently |

## 3.6 No-reply handling

If `_sanitize_reply()` returns empty, that thread should return `no_reply` and should **not** create a suppression row that blocks future actionable customer follow-up forever.

Preferred minimal rule:
- do not write an `auto_replies` draft row when nothing was typed
- optionally log a MAS decision row in `mas_decisions` for observability

---

## 4. File Ownership Map for This Change

| File | Role in change | Expected update |
| --- | --- | --- |
| `fb_pipeline/browser/l2_actions.py` | Hard safety boundary | Remove inbox send behavior; type-only semantics |
| `adk_agents/tools/l5_facebook_tools.py` | ADK wrapper boundary | Update wrapper docs/behavior to draft-only |
| `tools/l5_inbox_mas_runner.py` | Main orchestration | Rename semantics from send to draft, sanitize/log/query integration |
| `adk_agents/tools/l5_seeker_tools.py` | Actionable-thread query | Rework `find_unreplied_threads()` around latest customer-turn acknowledgement |
| `fb_pipeline/persistence/l4_sqlite_store.py` | Schema + audit persistence | Add minimal `auto_replies` field(s) and migration |
| `tools/inbox_mas_runner.py` | Compatibility shim | No logic change if pure shim; verify exports/docs stay aligned |
| `adk_agents/tools/facebook_tools.py` | Compatibility shim | No logic change if pure shim; verify re-export stays aligned |

---

## 5. Exact Tests to Update or Add

## 5.1 Update existing unit tests

### `tests/test_l5_inbox_mas_runner.py`
Add coverage for:
- sanitized empty reply returns `no_reply` and does not type anything
- successful processing logs a drafted reply, not a sent reply
- runner no longer depends on `--live` to change inbox-send behavior
- `process_single_thread()` passes latest customer message boundary into audit logging

### `tests/test_l5_facebook_tools.py`
Add coverage for:
- wrapper calls only draft/type behavior
- wrapper API/docs do not claim Enter/send behavior anymore

### `tests/test_l4_inbox_persistence.py`
Add coverage for:
- schema migration adds new `auto_replies` field(s)
- inserted draft rows persist customer-message boundary correctly

## 5.2 Add or expand query-focused tests

### `tests/test_l5_inbox_query_actions.py`
This is the best existing location for unreplied/actionable thread logic.
Add coverage for:
- thread selected when latest customer message has no draft acknowledgement
- thread not selected after draft is logged for latest customer message
- thread selected again after a newer customer message is inserted
- draft rows with old customer boundary do not suppress newer customer messages

If this file is not currently the query owner, equivalent coverage may live in a new targeted test file for `find_unreplied_threads()`.

## 5.3 Hung Bui safety and E2E tests

### `tests/test_e2e_live_hung_bui.py`
Update expectations so the live Hung Bui test proves:
- only Hung Bui's thread is targeted
- reply generation may happen
- no Enter/send action is ever automated
- final system result is draft-only

### `tests/test_adk_e2e.py`
Keep it focused on agent pipeline outputs, but add assertion guidance that pipeline tests are about reply text generation only, not sending behavior.

### `tests/test_e2e_mas_strategy_hung_bui.py`
Update wording/expectations where it currently assumes end-to-end reply execution to clarify that inbox route output is a draft for human review.

## 5.4 QA regression coverage to add

### New recommended test: `tests/test_l2_inbox_draft_safety.py`
Purpose:
- assert `fb_pipeline.browser.l2_actions.send_reply_via_cdp()` never presses Enter
- assert it succeeds after typing text into the composer

This is the most important low-level safety regression because it locks the hard boundary.

---

## 6. Exact Documentation to Update

| File | Why update |
| --- | --- |
| `docs/ARCHITECTURE.md` | It currently says inbox reply may optionally send in live mode; must change to draft-only semantics |
| `memory/mas_strategy.md` | Message QA/execution sections should reflect human-reviewed draft-only inbox replies |
| `docs/reports/test-report.md` | QA report template/scope should reference no-auto-send acceptance criteria |
| `CLAUDE.md` | Section 10 already has strong safety rules; align inbox runner wording with never-auto-send rule |
| `GEMINI.md` | Must stay identical to `CLAUDE.md` per repo rule |

If user-facing README or operator docs mention `--live` sending inbox replies, those docs must also be updated in the same change.

---

## 7. QA Expectations and Acceptance Criteria

## 7.1 Safety acceptance criteria

The change is accepted only if all are true:

1. No production inbox path can press Enter or click Send automatically.
2. `fb_pipeline/browser/l2_actions.py` enforces type-only behavior for inbox replies.
3. `tools/l5_inbox_mas_runner.py` no longer exposes real customer auto-send semantics.
4. A drafted reply suppresses repeat drafting for the same latest customer message.
5. A newer later customer message makes the thread actionable again.
6. `_sanitize_reply()` still blocks reasoning leaks before anything is typed.
7. If sanitization yields empty text, nothing is typed and the thread is marked `no_reply`.

## 7.2 Hung Bui-only safety expectations

Browser E2E / live-environment validation must still enforce:
- target thread is **Hung Bui only**
- no real customer threads are processed in browser tests
- no automated send occurs even on Hung Bui's thread
- the highest-risk path tested is "draft typed into composer, waiting for human Enter"

## 7.3 Query/DB acceptance criteria

QA must prove this sequence:

1. Insert customer message A.
2. Run cycle → thread selected → draft typed → draft audit row written.
3. Run cycle again without new customer messages → thread not selected.
4. Insert later customer message B.
5. Run cycle again → same thread selected again.

## 7.4 Negative tests

QA must explicitly prove failure is safe:
- navigation failure does not write a successful draft acknowledgement
- typing failure does not suppress future processing
- sanitization-empty output does not press Enter and does not create false suppression for future customer follow-up

---

## 8. Implementation Constraints for Developers

1. Keep the change minimal: do not redesign the whole MAS.
2. Make the non-send guarantee true at the lowest reusable browser action level.
3. Prefer additive schema migration over table replacement.
4. Reuse `auto_replies` as the audit source of truth instead of introducing a second inbox draft table unless migration friction forces it.
5. Maintain compatibility shims, but move semantics and docs to "draft" language.

---

## 9. Recommended Developer Order

1. Update `fb_pipeline/browser/l2_actions.py` safety boundary.
2. Update `adk_agents/tools/l5_facebook_tools.py` wrapper semantics.
3. Add `auto_replies` acknowledgement field in `fb_pipeline/persistence/l4_sqlite_store.py`.
4. Rework `find_unreplied_threads()` in `adk_agents/tools/l5_seeker_tools.py`.
5. Update `tools/l5_inbox_mas_runner.py` orchestration/logging/CLI wording.
6. Update tests.
7. Update docs.

---

## 10. Final Architectural Decision

**Inbox MAS is now a human-in-the-loop drafting system, not an auto-sending system.**

The authoritative delivery state for inbox replies is:
- `generated` → reply text created
- `sanitized` → safe to type
- `drafted` → typed into composer
- `sent` → human-only action outside automation scope

Automation ends at `drafted`.
