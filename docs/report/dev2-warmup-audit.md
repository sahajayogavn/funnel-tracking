# Warm-up Route Audit

## Scope

Audited Route 2 warm-up behavior against `memory/mas_strategy.md`, `docs/ARCHITECTURE.md`, and the current implementation/tests.

Note: `docs/report/architecture-decisions.md` was requested in the task brief but does not exist in the repo, so this audit used `docs/ARCHITECTURE.md` as the architecture source.

## Implemented logic

### 1. Scheduler-side temperature decision core exists
- Route 2 uses a scheduler decision core with explicit temperature thresholds by stage in `tools/l5_scheduler.py:83`.
- `_compute_temperature()` preserves operator hard-stop states `dormant` and `unsubscribed`, converts `spam`/`unsubscribed` lead stages into a hard stop, and maps inactivity to `hot/warm/cool/cold` in `tools/l5_scheduler.py:135`.
- Tests cover manual `unsubscribed`, recent registered, and old seeker temperature outcomes in `tests/test_l5_scheduler.py:191`.

### 2. Proactive eligibility checks are partially centralized
- `_evaluate_proactive_eligibility()` blocks warm-up when user state is missing, when lead stage is `spam`/`unsubscribed`, when computed temperature is `unsubscribed`, when the thread still has pending inbox follow-up, when any live proactive touch happened in the last 24h, and when warm-up targets are already `dormant` in `tools/l5_scheduler.py:288`.
- It also enforces the dormant quarterly event rule for Route 3, not Route 2, in `tools/l5_scheduler.py:317`.
- Tests cover pending inbox reply, dormant warm-up blocking, and dormant quarterly event limit in `tests/test_l5_scheduler.py:205` and `tests/test_cool_sequence.py:27`.

### 3. Cool sequence state machine is implemented in the scheduler
- The cool sequence template set matches the playbook’s 3-step pattern: check-in, 5-minute tip, and city-based new opportunity in `tools/l5_scheduler.py:91`.
- `_get_next_cool_step()` advances 0→1→2→3 and stops after step 3 in `tools/l5_scheduler.py:233`.
- `run_warmup_cycle()`:
  - enters cool-sequence logic only when computed temperature is `cool` in `tools/l5_scheduler.py:597`
  - blocks step 2 unless 3 days have passed since the last warm-up in `tools/l5_scheduler.py:622`
  - blocks step 3 unless 5 days have passed since the last warm-up in `tools/l5_scheduler.py:626`
  - marks the seeker `cold` and resets `cool_step` to 0 when the sequence is exhausted in `tools/l5_scheduler.py:604`
- Tests cover template content, step advancement, gap enforcement, and exhaustion behavior in `tests/test_cool_sequence.py:12`, `tests/test_cool_sequence.py:20`, `tests/test_cool_sequence.py:46`, and `tests/test_cool_sequence.py:108`.

### 4. Decision/audit logging is present
- Warm-up route logs blocked/allowed decisions through `log_mas_decision()` before or alongside campaign logging in `tools/l5_scheduler.py:561`, `tools/l5_scheduler.py:588`, `tools/l5_scheduler.py:606`, `tools/l5_scheduler.py:623`, `tools/l5_scheduler.py:627`, and `tools/l5_scheduler.py:664`.
- Warm-up attempts are logged to `warmup_campaigns` through `log_warmup_campaign()` in `tools/l5_scheduler.py:676` and `adk_agents/tools/l5_warmup_tools.py:254`.
- Tests verify warm-up campaign logging and dry-run behavior for recent-warm-up checks in `tests/test_l5_warmup_tools.py:127` and `tests/test_l5_warmup_tools.py:190`.

### 5. ADK warm-up composer is wired into Route 2
- `run_warmup_cycle()` loads knowledge context and calls `run_adk_warmup_composer()` for both cool-sequence messages and normal strategy-based warm-ups in `tools/l5_scheduler.py:543`, `tools/l5_scheduler.py:635`, and `tools/l5_scheduler.py:657`.
- `run_adk_warmup_composer()` invokes `warmup_composer` from `adk_agents.agent` and falls back to a template when the ADK output is empty in `tools/l5_scheduler.py:457` and `tools/l5_scheduler.py:635`.
- `WarmUpComposer` is defined in `adk_agents/agent.py:142` and the architecture doc claim that it is “not wired into the scheduler yet” is therefore outdated (`docs/ARCHITECTURE.md:419`).

### 6. Comment-user support is partially implemented but non-deliverable
- `find_dormant_seekers()` returns both inbox `users` and `comment_users`, including `temperature`, `last_warmup_at`, `warmup_count`, and `cool_step`, in `adk_agents/tools/l5_warmup_tools.py:99`.
- `_load_user_state()` and `_update_user_decision_state()` support `comment_` thread IDs in `tools/l5_scheduler.py:159` and `tools/l5_scheduler.py:240`.
- But Route 2 explicitly blocks comment users with `no_delivery_channel` in `tools/l5_scheduler.py:559`.
- Tests cover comment-user state loading/updating and inclusion in dormant seeker selection in `tests/test_l5_scheduler.py:239` and `tests/test_l5_warmup_tools.py:112`.

## Missing logic

### 1. Temperature thresholds do not fully match the strategy docs
- The strategy doc says:
  - Follower hot `<3`, warm `3–7`, cool `7–21`, cold `>21` in `memory/mas_strategy.md:301`
  - Curious Seeker hot `<3`, warm `3–7`, cool `7–14`, cold `>14` in `memory/mas_strategy.md:304`
  - Registered hot `<2`, warm `2–5`, cool `5–14`, cold `>14` in `memory/mas_strategy.md:305`
  - Deep Learner and Sahaja Yogi depend on missed sessions / collective participation, not simple day buckets, in `memory/mas_strategy.md:306` and `memory/mas_strategy.md:307`
- Current code uses day-based thresholds for every stage, including Deep Learner and Sahaja Yogi, in `tools/l5_scheduler.py:83`.
- There is no implementation of attendance-based thresholds such as “1–2 buổi vắng”, “3 buổi vắng”, or “tham gia collective”.

### 2. Dormant transition after cold is not implemented
- The strategy says cold seekers should receive one last nudge, then after 14 days of silence be marked `dormant` in `memory/mas_strategy.md:340`.
- Current scheduler never sends a distinct cold-stage “last nudge”. It only:
  - blocks warm-up immediately if temperature is already `dormant` in `tools/l5_scheduler.py:308`
  - marks a cool seeker `cold` once the 3-step sequence is exhausted in `tools/l5_scheduler.py:604`
- There is no Route 2 logic that waits 14 days after a cold-stage last nudge and then transitions to `dormant`.

### 3. Unsubscribed hard stop is incomplete relative to doc wording
- The strategy requires instant stop when seeker says they do not want messages, marking them `unsubscribed` in `memory/mas_strategy.md:359`.
- Route 2 blocks `lead_stage in ('spam', 'unsubscribed')` and computed `temperature == 'unsubscribed'` in `tools/l5_scheduler.py:305`, and `find_dormant_seekers()` excludes those lead stages in `adk_agents/tools/l5_warmup_tools.py:123` and `adk_agents/tools/l5_warmup_tools.py:154`.
- However, there is no audited Route 2 code here that detects opt-out language and sets the user state to `unsubscribed`; it only respects existing state.

### 4. Warm-up QA gates from strategy are not implemented
- The strategy requires message-gate checks for personalization and tone before sending/review in `memory/mas_strategy.md:488`.
- Route 2 only checks timing/status arbitration; it does not validate that messages contain personalization or non-sales tone before logging in `tools/l5_scheduler.py:642` and `tools/l5_scheduler.py:664`.
- `WarmUpComposer` instructions encourage personalization and non-sales tone, but that is prompt guidance rather than an enforced gate in `adk_agents/agent.py:153`.

### 5. Delivery remains logging-only
- The strategy doc and route mapping describe proactive DM warm-up behavior in Route 2, but implementation only logs campaigns and decisions; no CDP send/type path exists for Route 2 in `tools/l5_scheduler.py:676`.
- `docs/ARCHITECTURE.md` correctly states CDP delivery is not wired yet for warm-up in `docs/ARCHITECTURE.md:420`.

### 6. Coverage gaps remain around key rules
- No tests verify exact threshold boundaries for each strategy stage.
- No tests verify that a `cold` seeker gets a distinct last-nudge treatment.
- No tests verify the 14-day cold→dormant sunset behavior because that behavior is absent.
- No tests verify blocking based on operator state `dormant` inside `find_dormant_seekers()` itself; blocking happens later in scheduler eligibility.
- No tests verify message QA gates because those gates are absent.

## Mismatches with docs

### 1. Architecture doc says WarmUpComposer is not wired, but code wires it
- `docs/ARCHITECTURE.md:419` says `WarmUpComposer` is defined but not wired into the scheduler.
- Actual scheduler calls `run_adk_warmup_composer()` from Route 2 in `tools/l5_scheduler.py:635` and `tools/l5_scheduler.py:657`.
- This is a documentation mismatch.

### 2. Architecture doc describes template warm-up logging, but runtime is ADK-first with template fallback
- Architecture says Route 2 “generates template-based message text” in `docs/ARCHITECTURE.md:420` and pipeline is `select_warmup_strategy()` → template message in `docs/ARCHITECTURE.md:423`.
- Actual code is ADK-first and only falls back to templates when ADK returns empty in `tools/l5_scheduler.py:635` and `tools/l5_scheduler.py:657`.
- This is a behavior mismatch.

### 3. Warm-up strategy matrix in `mas_strategy.md` does not match current route strategy table
- `mas_strategy.md` expects:
  - Follower 3–5 days → opening check-in in `memory/mas_strategy.md:396`
  - Curious Seeker 3–7 days → class reminder in `memory/mas_strategy.md:397`
  - Curious Seeker 7–14 days → meditation tips in `memory/mas_strategy.md:398`
  - Registered 1 day before class / 2 days after no-show in `memory/mas_strategy.md:399`
  - Deep Learner based on absences/milestones in `memory/mas_strategy.md:401`
- `select_warmup_strategy()` instead maps normalized stages to a single min/max dormancy band plus one template per stage in `adk_agents/tools/l5_warmup_tools.py:51`.
- It does not model Follower separately, splits Curious vs Follower differently, and does not encode schedule-aware reminders like T-1 or no-show follow-up.

### 4. Stage mapping diverges from route coverage in the strategy docs
- The strategy route matrix says Route 2 applies to Stages 1–4, not Stage 5 in `memory/mas_strategy.md:445`.
- But scheduler stage normalization maps `Seed`/`Sahaja_Yogi` variants to `Sahaja Yogi` temperature thresholds in `tools/l5_scheduler.py:108`, and warm-up strategy normalization maps those same values to `18-Week Seeker` in `adk_agents/tools/l5_warmup_tools.py:41`.
- So Stage 5 values are still eligible for warm-up strategy selection rather than being excluded from Route 2.

### 5. Architecture says input is dormant seekers; actual candidate set is broader
- Architecture describes Route 2 input as dormant seekers from `users` table in `docs/ARCHITECTURE.md:417`.
- Actual code reads both `users` and `comment_users` via `find_dormant_seekers()` in `adk_agents/tools/l5_warmup_tools.py:115` and `adk_agents/tools/l5_warmup_tools.py:146`, then blocks comment users later because there is no delivery channel in `tools/l5_scheduler.py:559`.
- Also, selection is time-based for all non-spam/non-unsubscribed stages; it is not limited to already-marked `dormant` seekers.

## Test assessment

### Covered well
- Cool sequence step progression and templates: `tests/test_cool_sequence.py:12`
- Gap enforcement before cool step 2/3: `tests/test_cool_sequence.py:46`
- Cool sequence exhaustion to `cold`: `tests/test_cool_sequence.py:108`
- Manual state preservation for `unsubscribed`: `tests/test_l5_scheduler.py:191`
- Dormant/event quarterly rule: `tests/test_l5_scheduler.py:222`
- Comment-user state compatibility: `tests/test_l5_scheduler.py:239`
- Warm-up tool stage alias normalization and live-vs-dry-run recent-warmup checks: `tests/test_l5_warmup_tools.py:122` and `tests/test_l5_warmup_tools.py:169`

### Not covered or under-covered
- Exact stage threshold boundaries from strategy matrix
- Stage 5 exclusion from Route 2
- Cold last-nudge + 14-day sunset to dormant
- Personalization/tone QA gate enforcement
- Whether Route 2 should reject or specially handle pre-existing `cold` users
- Documentation parity assertions for ADK-vs-template behavior

## Bottom line

Route 2 is more advanced than the architecture doc claims: it has a shared scheduler decision core, cool-sequence enforcement, MAS decision logging, and active ADK warm-up composition with template fallback. But it still does not fully implement the strategy playbook. The biggest gaps are the missing cold last-nudge → dormant sunset flow, the simplified day-based thresholds for Deep Learner / Sahaja Yogi, the lack of enforced content QA gates, and the mismatch between the documented warm-up matrix and the actual single-band-per-stage strategy table.