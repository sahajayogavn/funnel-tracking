# QA Audit Report — 2026-03-25

## Summary
- Audit scope: stage gates, scheduler architecture, inbox route, warmup route, event route, overall alignment
- Result: 3 PASS / 3 FAIL
- Teammate audit validation status: no audit output files from `arch-audit`, `qa1-audit`, `dev1-audit`, `dev2-audit`, or `dev3-audit` were present in the repo at audit time, so validation was performed directly against code, tests, and docs only.

## Results

### Stage gates — PASS
**Evidence**
- Gate logic exists and matches the tested transitions in `adk_agents/tools/l5_stage_tools.py:151`.
- G1 promotes `Intake -> Seeker` only when a touchpoint exists in `adk_agents/tools/l5_stage_tools.py:169`.
- G3 promotes `Seeker -> Seeker_Public_Program` only when both valid contact and specific program evidence exist in `adk_agents/tools/l5_stage_tools.py:181`.
- Registered/Public Program and 18-Week stages are manual-only in `adk_agents/tools/l5_stage_tools.py:197` and `adk_agents/tools/l5_stage_tools.py:200`.
- Tests cover the positive and negative cases in `tests/test_stage_gates.py:9`, `tests/test_stage_gates.py:31`, `tests/test_stage_gates.py:50`, `tests/test_stage_gates.py:78`, `tests/test_stage_gates.py:100`, and `tests/test_stage_gates.py:122`.

**Audit verdict**
- PASS. Stage gate implementation and test coverage are aligned.

### Scheduler architecture — FAIL
**Evidence**
- `docs/ARCHITECTURE.md:376` says the scheduler fetches **inbox + comments** every 15 minutes.
- Actual `run_fetch_cycle()` only calls `run_inbox_cycle()` in `tools/l5_scheduler.py:326`, and `run_inbox_cycle()` only scrapes inbox via `scrape_inbox(...)` in `tools/l5_inbox_mas_runner.py:420`; there is no comment fetch call in that path.
- `docs/ARCHITECTURE.md:382-383` says only inbox reply currently runs ADK end-to-end.
- Actual scheduler also invokes ADK for warmup and event message generation in `tools/l5_scheduler.py:457` and `tools/l5_scheduler.py:493`.
- The persistence schema expected by the docs exists: `auto_replies.customer_message_timestamp`, `mas_decisions`, `temperature`, `last_warmup_at`, `warmup_count`, and `cool_step` are created in `fb_pipeline/persistence/l4_sqlite_store.py:299-327` and `fb_pipeline/persistence/l4_sqlite_store.py:191-199` / `fb_pipeline/persistence/l4_sqlite_store.py:391-399`.
- Decision-core behavior is implemented in `tools/l5_scheduler.py:288` and tested in `tests/test_l5_scheduler.py:190`.

**Audit verdict**
- FAIL. The persistence layer matches the documented scheduler architecture, but the runtime scheduler behavior does not fully match the docs: comment fetching is not wired into `run_fetch_cycle`, and ADK usage now extends beyond inbox reply.

### Inbox route — PASS
**Evidence**
- Knowledge loading includes `memory/mas_strategy.md` in `tools/l5_inbox_mas_runner.py:66-73`, and the loader reads all configured sources in `tools/l5_inbox_mas_runner.py:76`.
- The runner is draft-only even when `--live` is passed: warning and forced draft semantics in `tools/l5_inbox_mas_runner.py:302-304` and `tools/l5_inbox_mas_runner.py:519-522`.
- The low-level browser action never presses Enter in `fb_pipeline/browser/l2_actions.py:6-30`.
- Draft acknowledgements store `customer_message_timestamp` through `log_auto_reply(...)` in `tools/l5_inbox_mas_runner.py:346-354` and `adk_agents/tools/l5_facebook_tools.py:59-100`.
- Unreplied-thread detection suppresses repeat drafting based on latest acknowledged customer-turn, regardless of `dry_run`, in `adk_agents/tools/l5_seeker_tools.py:104-135`.
- Tests cover empty sanitized reply/no draft, draft logging with customer boundary, `--live` ignored, query suppression, persistence migration, and low-level no-Enter safety in `tests/test_l5_inbox_mas_runner.py:97`, `tests/test_l5_inbox_mas_runner.py:125`, `tests/test_l5_inbox_mas_runner.py:189`, `tests/test_l5_inbox_query_actions.py:59`, `tests/test_l4_inbox_persistence.py:109`, and `tests/test_l2_inbox_draft_safety.py:18`.

**Audit verdict**
- PASS. The inbox route is aligned with the draft-only architecture and customer-message-boundary persistence.

### Warmup route — PASS
**Evidence**
- Candidate discovery, live 7-day suppression, stage normalization, strategy selection, and campaign logging are implemented in `adk_agents/tools/l5_warmup_tools.py:99`, `adk_agents/tools/l5_warmup_tools.py:186`, `adk_agents/tools/l5_warmup_tools.py:211`, `adk_agents/tools/l5_warmup_tools.py:222`, and `adk_agents/tools/l5_warmup_tools.py:254`.
- Scheduler-side eligibility and decision logging are enforced before campaign logging in `tools/l5_scheduler.py:582-589` and `tools/l5_scheduler.py:665-688`.
- Hard-stop logic for spam/unsubscribed and reactive-beats-proactive gating come from the shared decision core in `tools/l5_scheduler.py:305-320`.
- Tests cover dormant seeker discovery, spam exclusion, 7-day live suppression, alias normalization, and logging in `tests/test_l5_warmup_tools.py:92`, `tests/test_l5_warmup_tools.py:101`, `tests/test_l5_warmup_tools.py:122`, `tests/test_l5_warmup_tools.py:169`, and `tests/test_l5_warmup_tools.py:190`.

**Audit verdict**
- PASS. Warmup route behavior and coverage are internally consistent with the current scheduler design.

### Event route — FAIL
**Evidence**
- Docs say Route 3 action is logging candidate notifications to `event_campaigns`; CDP sending is not wired in `docs/ARCHITECTURE.md:431-437`.
- Implementation matches that part: `run_event_cycle()` builds text and calls `log_event_campaign(...)` in `tools/l5_scheduler.py:764-793`.
- Docs also say only inbox reply currently runs ADK end-to-end in `docs/ARCHITECTURE.md:382-383`, but event route now invokes the ADK `EventAdvertiser` in `tools/l5_scheduler.py:493-514` and `tools/l5_scheduler.py:770-775`.
- Docs describe city-targeted matching and stage prioritization in `docs/ARCHITECTURE.md:430-435`, and the implementation does normalize stages and prioritize them in `adk_agents/tools/l5_event_tools.py:158-268`.
- Tests cover upcoming-event filtering, city targeting, stage alias normalization, ordering, dry-run suppression behavior, comment-user metadata, and logging in `tests/test_l5_event_tools.py:113`, `tests/test_l5_event_tools.py:134`, and `tests/test_l5_event_tools.py:196`.

**Audit verdict**
- FAIL. The route implementation is coherent and tested, but the current architecture doc is stale because Route 3 is no longer purely inline/template-only; it now uses ADK generation.

### Overall alignment — FAIL
**Evidence**
- Inbox route, stage gates, and persistence contracts are aligned across code/tests/docs.
- Scheduler runtime diverges from the architecture doc on comment fetching (`docs/ARCHITECTURE.md:376` vs `tools/l5_scheduler.py:326` + `tools/l5_inbox_mas_runner.py:420`).
- Scheduler runtime diverges from the architecture doc on ADK scope (`docs/ARCHITECTURE.md:382-383` vs `tools/l5_scheduler.py:457-514`).
- `tools/l5_scheduler.py` still advertises `--live` as “sends real messages” in its module docstring at `tools/l5_scheduler.py:19-20`, while `docs/ARCHITECTURE.md:565` correctly narrows inbox behavior to draft-only. That operator-facing wording is still misleading.

**Audit verdict**
- FAIL. The codebase is partially aligned, but the scheduler/route documentation and some operator-facing scheduler text lag behind the implemented behavior.

## Disagreements with teammate findings
- No teammate audit reports were available in the repository during this audit, so there were no concrete teammate claims to confirm or dispute.

## Final call
- Not fully aligned yet.
- Highest-priority mismatches are all in scheduler/documentation alignment, not in the inbox draft-only safety path.