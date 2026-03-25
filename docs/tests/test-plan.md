# Audit Test Plan — MAS Strategy vs Architecture Alignment

Source set:
- `/Users/steve/sahajayogavn/funnel-tracking/docs/architecture-decisions.md`
- `/Users/steve/sahajayogavn/funnel-tracking/docs/ARCHITECTURE.md`
- `/Users/steve/sahajayogavn/funnel-tracking/memory/mas_strategy.md`

Scope:
- Audit-only verification of whether MAS strategy logic is implemented and aligned with architecture.
- This plan defines concrete checks, exact files to inspect, and exact commands a downstream QA operator may run.
- This plan does not execute tests.

## Audit commands

Run from project root:

```bash
.venv/bin/python -m pytest tests/test_l5_inbox_mas_runner.py -v
.venv/bin/python -m pytest tests/test_l5_facebook_tools.py -v
.venv/bin/python -m pytest tests/test_l4_inbox_persistence.py -v
.venv/bin/python -m pytest tests/test_l5_inbox_query_actions.py -v
.venv/bin/python -m pytest tests/test_l5_event_tools.py -v
.venv/bin/python -m pytest tests/test_l5_warmup_tools.py -v
.venv/bin/python -m pytest tests/test_cool_sequence.py -v
.venv/bin/python -m pytest tests/test_adk_wiring.py -v
.venv/bin/python -m pytest tests/test_e2e_full_pipeline.py -v
.venv/bin/python -m pytest tests/test_adk_e2e.py -v
.venv/bin/python -m pytest tests/test_e2e_live_hung_bui.py -v
.venv/bin/python -m pytest tests/test_e2e_mas_strategy_hung_bui.py -v
```

Targeted code inspection commands:

```bash
grep -n "send_reply_via_cdp\|find_unreplied_threads\|customer_message_timestamp\|mas_decisions\|find_dormant_seekers\|select_warmup_strategy\|find_target_seekers_for_event\|run_reply_cycle\|run_inbox_cycle" \
  fb_pipeline/browser/l2_actions.py \
  adk_agents/tools/l5_facebook_tools.py \
  adk_agents/tools/l5_seeker_tools.py \
  adk_agents/tools/l5_warmup_tools.py \
  adk_agents/tools/l5_event_tools.py \
  fb_pipeline/persistence/l4_sqlite_store.py \
  tools/l5_inbox_mas_runner.py \
  tools/l5_scheduler.py
```

---

## Route × Stage audit matrix

Legend:
- PASS = code/tests prove route is implemented for the stage and respects architecture constraints
- FAIL = missing implementation, contradiction, or missing evidence
- N/A = strategy says route does not apply to that stage

| Route / Stage | Stage 0 User | Stage 1 Follower | Stage 2 Curious | Stage 3 Registered | Stage 4 Deep Learner | Stage 5 Yogi | Required evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Inbox MAS | N/A | PASS/FAIL | PASS/FAIL | PASS/FAIL | PASS/FAIL | PASS/FAIL | `tools/l5_inbox_mas_runner.py`, `adk_agents/tools/l5_facebook_tools.py`, `fb_pipeline/browser/l2_actions.py`, `adk_agents/tools/l5_seeker_tools.py`, inbox tests |
| Route 1 React | N/A | PASS/FAIL | PASS/FAIL | N/A | N/A | N/A | `tools/l5_scheduler.py`, `adk_agents/tools/l5_reaction_tools.py`, `fb_pipeline/persistence/l4_sqlite_store.py`, reaction-related tests |
| Route 2 Warm-up | N/A | PASS/FAIL | PASS/FAIL | PASS/FAIL | PASS/FAIL | N/A | `tools/l5_scheduler.py`, `adk_agents/tools/l5_warmup_tools.py`, warmup tests, cool-sequence tests |
| Route 3 Event | N/A | PASS/FAIL | PASS/FAIL | PASS/FAIL | PASS/FAIL | PASS/FAIL | `tools/l5_scheduler.py`, `adk_agents/tools/l5_event_tools.py`, event tests |
| Stage gates | PASS/FAIL | PASS/FAIL | PASS/FAIL | PASS/FAIL | PASS/FAIL | PASS/FAIL | strategy docs vs implemented selectors/queries/decision-core tests/logging |

Pass rule for each non-N/A cell: at least one implementation path exists, no architecture contradiction is present, and at least one test or explicit inspection step proves the behavior.

---

## Test cases

## TC-INBOX-001: Inbox route is draft-only at the lowest browser boundary
- **Type**: unit / audit
- **Files to inspect**:
  - `fb_pipeline/browser/l2_actions.py`
  - `adk_agents/tools/l5_facebook_tools.py`
- **Command**: `grep -n "send_reply_via_cdp\|keyboard.press\|Enter\|click.*Send\|draft" fb_pipeline/browser/l2_actions.py adk_agents/tools/l5_facebook_tools.py`
- **Pass criteria**:
  - inbox reply path never presses Enter or clicks Send
  - docs/comments in wrapper describe draft-only semantics
  - architecture decision in `docs/architecture-decisions.md` section 2.3 is satisfied
- **Fail criteria**:
  - any inbox path can send automatically
  - wrapper docs still advertise send behavior

## TC-INBOX-002: Runner semantics are draft, not send
- **Type**: unit / audit
- **Files to inspect**:
  - `tools/l5_inbox_mas_runner.py`
- **Command**: `grep -n "process_single_thread\|run_inbox_cycle\|--live\|drafted\|sent\|no_reply\|nav_failed\|draft_failed" tools/l5_inbox_mas_runner.py`
- **Pass criteria**:
  - status vocabulary distinguishes draft outcomes from send outcomes
  - CLI/runtime wording does not promise auto-send for inbox replies
  - `--live` does not enable inbox auto-send semantics
- **Fail criteria**:
  - runner still claims or performs send behavior
  - result logging still implies automation-owned delivery

## TC-INBOX-003: Sanitizer blocks reasoning leaks before typing
- **Type**: unit
- **Command**: `.venv/bin/python -m pytest tests/test_l5_inbox_mas_runner.py -v`
- **Pass criteria**:
  - test evidence shows `_sanitize_reply()`-empty output returns `no_reply`
  - no typing happens when sanitized reply is empty
- **Fail criteria**:
  - empty sanitized reply is still typed or logged as drafted

## TC-INBOX-004: Draft acknowledgement suppresses repeat drafting for same customer turn
- **Type**: unit / query
- **Files to inspect**:
  - `adk_agents/tools/l5_seeker_tools.py`
  - `fb_pipeline/persistence/l4_sqlite_store.py`
- **Command**: `.venv/bin/python -m pytest tests/test_l5_inbox_query_actions.py -v`
- **Pass criteria**:
  - thread is selected when latest customer message has no acknowledgement
  - thread is not selected after a draft is logged for that same customer-message boundary
- **Fail criteria**:
  - query still suppresses only on `dry_run = 0`
  - drafted-but-unsent rows do not suppress duplicate drafting

## TC-INBOX-005: New customer message re-opens actionable thread
- **Type**: unit / query
- **Command**: `.venv/bin/python -m pytest tests/test_l5_inbox_query_actions.py -v`
- **Pass criteria**:
  - test evidence proves: message A → draft → no reselection → message B → reselection
- **Fail criteria**:
  - new customer message does not make thread actionable again

## TC-INBOX-006: Auto-reply audit schema persists customer-turn boundary
- **Type**: unit / persistence
- **Files to inspect**:
  - `fb_pipeline/persistence/l4_sqlite_store.py`
- **Command**: `.venv/bin/python -m pytest tests/test_l4_inbox_persistence.py -v`
- **Pass criteria**:
  - schema contains `customer_message_timestamp`
  - migration preserves compatibility
  - inserted audit rows persist customer-turn acknowledgement data
- **Fail criteria**:
  - field missing
  - migration absent or broken

## TC-INBOX-007: Runner passes customer-turn boundary into audit logging
- **Type**: unit
- **Command**: `.venv/bin/python -m pytest tests/test_l5_inbox_mas_runner.py -v`
- **Pass criteria**:
  - test evidence shows latest customer message timestamp is passed into draft audit logging
- **Fail criteria**:
  - draft log rows cannot be tied to latest customer-turn boundary

## TC-INBOX-008: Hung Bui E2E safety remains draft-only
- **Type**: E2E / smoke
- **Command**: `.venv/bin/python -m pytest tests/test_e2e_live_hung_bui.py -v`
- **Pass criteria**:
  - only Hung Bui thread is targeted
  - reply may be generated and typed as draft only
  - no automated send occurs
- **Fail criteria**:
  - any non-Hung-Bui thread is targeted
  - any automated send action occurs

## TC-INBOX-009: ADK E2E scope is generation-only, not delivery
- **Type**: E2E / audit
- **Command**: `.venv/bin/python -m pytest tests/test_adk_e2e.py -v`
- **Pass criteria**:
  - assertions focus on classifier/responder output quality and pipeline state
  - tests do not rely on auto-send semantics
- **Fail criteria**:
  - E2E assertions assume or require automated sending

## TC-INBOX-010: MAS strategy Hung Bui scenario aligns with draft-only inbox semantics
- **Type**: E2E / audit
- **Command**: `.venv/bin/python -m pytest tests/test_e2e_mas_strategy_hung_bui.py -v`
- **Pass criteria**:
  - wording and assertions describe inbox output as human-reviewed draft
- **Fail criteria**:
  - scenario still expects automated final delivery

## TC-REACT-001: Route 1 exists only for Stage 1-2 strategy coverage
- **Type**: audit
- **Files to inspect**:
  - `tools/l5_scheduler.py`
  - `adk_agents/tools/l5_reaction_tools.py`
  - `fb_pipeline/persistence/l4_sqlite_store.py`
- **Command**: `grep -n "find_unreacted_items\|_select_reaction_heuristic\|log_reaction\|reactions" tools/l5_scheduler.py adk_agents/tools/l5_reaction_tools.py fb_pipeline/persistence/l4_sqlite_store.py`
- **Pass criteria**:
  - scheduler implements reaction heuristic/logging path
  - architecture matches current reality: logging is wired, live CDP reaction still stubbed if stated in docs
  - route is not claimed for Stage 3-5 in implementation docs/tests
- **Fail criteria**:
  - route missing or architecture claims behavior absent in code

## TC-REACT-002: Reaction dry-run does not suppress later live reaction
- **Type**: unit / persistence
- **Command**: `.venv/bin/python -m pytest tests/test_l4_inbox_persistence.py -v`
- **Pass criteria**:
  - unique index applies only to live reactions
  - dry-run rows can coexist without blocking later live rows
- **Fail criteria**:
  - dry-run logging prevents later live execution

## TC-WARMUP-001: Warm-up route candidate selection matches stage scope and hard stops
- **Type**: unit / audit
- **Files to inspect**:
  - `adk_agents/tools/l5_warmup_tools.py`
  - `tools/l5_scheduler.py`
- **Command**: `.venv/bin/python -m pytest tests/test_l5_warmup_tools.py -v`
- **Pass criteria**:
  - candidate selection uses dormant seekers
  - spam/unsubscribed are excluded
  - route scope is Stage 1-4 only per strategy
- **Fail criteria**:
  - warm-up candidates include prohibited states or unsupported stages

## TC-WARMUP-002: Warm-up cadence enforces max one proactive touch per seven days
- **Type**: unit / audit
- **Command**: `grep -n "was_recently_warmed_up\|7 days\|warmup_campaigns" adk_agents/tools/l5_warmup_tools.py tools/l5_scheduler.py && .venv/bin/python -m pytest tests/test_l5_warmup_tools.py -v`
- **Pass criteria**:
  - code and tests prove live warm-up is capped at 1 per 7 days
- **Fail criteria**:
  - cadence check absent or weaker than strategy/architecture

## TC-WARMUP-003: Cool-sequence logic is capped and transitions safely
- **Type**: unit
- **Command**: `.venv/bin/python -m pytest tests/test_cool_sequence.py -v`
- **Pass criteria**:
  - cool sequence is limited to 3 steps
  - subsequent handling aligns with cold/dormant strategy expectations
- **Fail criteria**:
  - cool sequence exceeds 3 steps or lacks safe fallback

## TC-WARMUP-004: Scheduler logs warm-up attempts through route audit table and decision ledger
- **Type**: audit
- **Files to inspect**:
  - `tools/l5_scheduler.py`
  - `fb_pipeline/persistence/l4_sqlite_store.py`
- **Command**: `grep -n "log_warmup_campaign\|mas_decisions\|record_mas_decision\|warmup_campaigns" tools/l5_scheduler.py fb_pipeline/persistence/l4_sqlite_store.py`
- **Pass criteria**:
  - warm-up path logs route outcome to `warmup_campaigns`
  - decision allow/block outcomes are recorded in `mas_decisions` where architecture requires centralized arbitration
- **Fail criteria**:
  - route logs without decision ledger evidence
  - no proof of centralized block/allow recording

## TC-EVENT-001: Event targeting matches city and stage priority rules
- **Type**: unit
- **Command**: `.venv/bin/python -m pytest tests/test_l5_event_tools.py -v`
- **Pass criteria**:
  - target selection respects city matching
  - stage alias normalization and priority are implemented
  - unsubscribed/spam exclusions are enforced if architecture/strategy require them
- **Fail criteria**:
  - cross-city targeting occurs without online-event exception
  - stage priorities contradict strategy/architecture

## TC-EVENT-002: Dormant quarterly event rule is enforced through arbitration
- **Type**: audit
- **Files to inspect**:
  - `tools/l5_scheduler.py`
  - `adk_agents/tools/l5_event_tools.py`
  - `fb_pipeline/persistence/l4_sqlite_store.py`
- **Command**: `grep -n "90 days\|event_campaigns\|mas_decisions\|dormant" tools/l5_scheduler.py adk_agents/tools/l5_event_tools.py fb_pipeline/persistence/l4_sqlite_store.py`
- **Pass criteria**:
  - dormant seekers receive event outreach no more than once per 90 days
  - enforcement is visible in scheduler decision/arbitration logic
- **Fail criteria**:
  - dormant quarterly rule exists only in docs, not in code

## TC-EVENT-003: Event route logs notifications but does not claim unwired CDP delivery
- **Type**: audit
- **Command**: `grep -n "log_event_campaign\|event_campaigns\|CDP\|send" tools/l5_scheduler.py adk_agents/tools/l5_event_tools.py docs/ARCHITECTURE.md`
- **Pass criteria**:
  - runtime behavior and docs consistently describe logging/candidate generation only when delivery is unwired
- **Fail criteria**:
  - docs overclaim actual delivery behavior

## TC-DECISION-001: Shared decision core arbitrates proactive routes before execution
- **Type**: audit
- **Files to inspect**:
  - `tools/l5_scheduler.py`
  - `fb_pipeline/persistence/l4_sqlite_store.py`
- **Command**: `grep -n "recent live outbound\|pending inbox\|24 hours\|90 days\|mas_decisions\|route arbitration\|run_reply_cycle\|run_warmup_cycle\|run_event_cycle" tools/l5_scheduler.py fb_pipeline/persistence/l4_sqlite_store.py`
- **Pass criteria**:
  - code implements centralized checks for reactive-beats-proactive and one-proactive-touch-at-a-time rules
  - allow/block outcomes are written to `mas_decisions`
- **Fail criteria**:
  - route arbitration exists only in docs
  - proactive routes run independently without shared gating evidence

## TC-DECISION-002: Pending inbox follow-up blocks proactive warm-up and event routes
- **Type**: audit / integration
- **Command**: `.venv/bin/python -m pytest tests/test_adk_wiring.py -v && .venv/bin/python -m pytest tests/test_e2e_full_pipeline.py -v`
- **Pass criteria**:
  - evidence shows pending inbox work suppresses proactive routes for same thread in scheduler path
- **Fail criteria**:
  - a thread can receive proactive outreach while still awaiting inbox follow-up

## TC-GATE-001: Stage gate G1 is represented by recorded touch-point requirements
- **Type**: audit
- **Command**: `grep -n "touch-point\|comment\|react\|DM\|lead_stage" memory/mas_strategy.md docs/ARCHITECTURE.md tools/l5_scheduler.py adk_agents/tools/l5_seeker_tools.py`
- **Pass criteria**:
  - implementation inputs for Stage 1 eligibility are consistent with recorded touch-point evidence
- **Fail criteria**:
  - strategy gate exists only in doc text with no implementation counterpart

## TC-GATE-002: Stage gate G2 is represented by seeker-asked-info signals
- **Type**: audit
- **Command**: `grep -n "ask_class\|ask_event\|Curious\|lead_stage\|find_unreplied_threads" adk_agents tools tests -R`
- **Pass criteria**:
  - code/tests expose recognizable signals for inquiry-driven transition into Curious Seeker handling
- **Fail criteria**:
  - no implementation evidence supports this stage distinction

## TC-GATE-003: Stage gate G3 requires contact detail plus concrete class/event context
- **Type**: audit
- **Command**: `grep -n "phone\|email\|Seeker_Public_Program\|Public Program Seeker\|register" adk_agents fb_pipeline tools tests -R`
- **Pass criteria**:
  - architecture/runtime artifacts show registration state requires contact evidence and program context
- **Fail criteria**:
  - Stage 3 can be reached without contact/program evidence

## TC-GATE-004: Strategy gate rules that are not implemented are documented as gaps
- **Type**: audit
- **Command**: `grep -n "G1\|G2\|G3\|G4\|G5\|QA Gate" memory/mas_strategy.md docs/ARCHITECTURE.md docs/architecture-decisions.md`
- **Pass criteria**:
  - any gate not implemented in executable code is clearly treated as strategic guidance, not falsely claimed production behavior
- **Fail criteria**:
  - docs imply production gate enforcement without implementation evidence

## TC-DOC-001: Architecture docs align with current runtime semantics
- **Type**: audit
- **Command**: `grep -n "draft-only\|send\|live mode\|Warm-up\|Event\|Reactor\|WarmUpComposer\|EventAdvertiser" docs/ARCHITECTURE.md docs/architecture-decisions.md memory/mas_strategy.md`
- **Pass criteria**:
  - `docs/ARCHITECTURE.md`, `docs/architecture-decisions.md`, and `memory/mas_strategy.md` do not contradict each other on route wiring or inbox delivery semantics
- **Fail criteria**:
  - any document claims live inbox auto-send or fully wired proactive delivery where code does not support it

---

## Required evidence checklist by route

### Inbox MAS
- `fb_pipeline/browser/l2_actions.py` proves non-send boundary
- `adk_agents/tools/l5_facebook_tools.py` exposes draft-only wrapper semantics
- `tools/l5_inbox_mas_runner.py` uses draft terminology and statuses
- `adk_agents/tools/l5_seeker_tools.py` suppresses repeat drafts by latest customer-turn acknowledgement
- `tests/test_l5_inbox_mas_runner.py`
- `tests/test_l5_facebook_tools.py`
- `tests/test_l4_inbox_persistence.py`
- `tests/test_l5_inbox_query_actions.py`
- `tests/test_e2e_live_hung_bui.py`

### Route 1 React
- `tools/l5_scheduler.py`
- `adk_agents/tools/l5_reaction_tools.py`
- `fb_pipeline/persistence/l4_sqlite_store.py`
- reaction logging/index tests in `tests/test_l4_inbox_persistence.py`

### Route 2 Warm-up
- `tools/l5_scheduler.py`
- `adk_agents/tools/l5_warmup_tools.py`
- `tests/test_l5_warmup_tools.py`
- `tests/test_cool_sequence.py`

### Route 3 Event
- `tools/l5_scheduler.py`
- `adk_agents/tools/l5_event_tools.py`
- `tests/test_l5_event_tools.py`
- `tests/test_interest_targeting.py`

### Shared decision core / arbitration
- `tools/l5_scheduler.py`
- `fb_pipeline/persistence/l4_sqlite_store.py`
- any scheduler-path tests proving `mas_decisions` logging and proactive suppression

---

## Exit criteria for this audit plan

The MAS strategy implementation is considered aligned only if all of the following are proven:
1. Inbox route ends at `drafted`, never automated `sent`.
2. A typed draft suppresses repeat drafting for the same latest customer message.
3. A later customer message re-opens the thread.
4. Reactive inbox follow-up blocks overlapping proactive routes.
5. Warm-up cadence and hard-stop exclusions are enforced.
6. Event targeting respects city/stage rules and dormant quarterly limits.
7. Route wiring claims in docs match current code reality.
8. Any strategy-only gates not yet implemented are called out as gaps, not mislabeled as production-enforced behavior.
