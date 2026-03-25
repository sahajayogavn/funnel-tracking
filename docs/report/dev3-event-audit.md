# Dev3 Event Route Audit

## Scope

Audited Route 3 event advertising against:
- `memory/mas_strategy.md`
- `docs/ARCHITECTURE.md`
- `adk_agents/tools/l5_event_tools.py`
- `tools/l5_scheduler.py`
- `tests/test_interest_targeting.py`
- `tests/test_l5_event_tools.py`
- `tests/test_l5_scheduler.py`

Note: `docs/report/architecture-decisions.md` was requested by agent rules but is currently missing in the repo.

## Implemented logic

### 1. City targeting is implemented for both DM users and comment users
- DM targets are filtered with `WHERE u.city = ?` in `find_target_seekers_for_event(...)` at `adk_agents/tools/l5_event_tools.py:187-200`.
- Comment-user targets are filtered with `WHERE cu.city = ?` at `adk_agents/tools/l5_event_tools.py:226-234`.
- The scheduler passes each event's city into target discovery at `tools/l5_scheduler.py:723-727`.
- This matches the docs' city-targeted Route 3 requirement in `docs/ARCHITECTURE.md:425-437` and `memory/mas_strategy.md:409-415`.
- Test coverage exists in `tests/test_l5_event_tools.py:135-143` and `tests/test_l5_event_tools.py:180-183`.

### 2. Stage alias normalization and stage-priority sorting are implemented
- `find_target_seekers_for_event(...)` normalizes `lead_stage` via `normalize_lead_stage(...)` before ranking at `adk_agents/tools/l5_event_tools.py:203-221` and `adk_agents/tools/l5_event_tools.py:238-255`.
- `normalize_lead_stage(...)` is defined in `adk_agents/tools/l5_warmup_tools.py:211-219`.
- Stage priorities are encoded in `EVENT_STAGE_PRIORITIES` with `18-Week Seeker` highest, `Registered` / `Public Program Seeker` next, then `Seeker`, then `Intake` at `adk_agents/tools/l5_event_tools.py:23-29`.
- Final ordering sorts by `(lead_stage_priority, interest_score, last_interaction)` descending at `adk_agents/tools/l5_event_tools.py:258-266`.
- This partially aligns with architecture notes that Route 3 should prioritize `Registered` / `Public Program Seeker` before generic `Seeker` in `docs/ARCHITECTURE.md:434-437`.
- Test coverage exists for normalization and Registered-before-Seeker ordering in `tests/test_l5_event_tools.py:144-154` and `tests/test_interest_targeting.py:119-127`.

### 3. Interest matching is implemented for inbox users only
- Event interest is inferred from event name/description keywords in `_score_seeker_interest(...)` at `adk_agents/tools/l5_event_tools.py:117-141`.
- Event-type buckets cover music, healing, class, and meditation keywords at `adk_agents/tools/l5_event_tools.py:32-37` and `adk_agents/tools/l5_event_tools.py:121-129`.
- Per-thread recent messages are loaded by `_get_thread_messages_for_interest(...)` at `adk_agents/tools/l5_event_tools.py:144-153`.
- Inbox seekers receive computed `interest_score` at `adk_agents/tools/l5_event_tools.py:206-220`.
- Tests validate scoring behavior and tie-breaking by interest in `tests/test_interest_targeting.py:95-117`, `tests/test_interest_targeting.py:119-137`.

### 4. No-repeat-per-event for live sends is implemented
- DM users are excluded if a live row already exists in `event_campaigns` for the same `(event_id, thread_id)` at `adk_agents/tools/l5_event_tools.py:193-197`.
- Tests confirm dry-run rows do not suppress later live targeting, while live rows do suppress repeats in `tests/test_l5_event_tools.py:162-178` and `tests/test_interest_targeting.py:148-157`.
- This aligns with `memory/mas_strategy.md:418-420`.

### 5. Scheduler-side decision core enforces proactive route blocking, including dormant-quarterly
- `_evaluate_proactive_eligibility(...)` blocks:
  - spam / unsubscribed at `tools/l5_scheduler.py:305-306`
  - pending inbox reply at `tools/l5_scheduler.py:311-312`
  - recent live proactive touch within 24h at `tools/l5_scheduler.py:314-315`
  - dormant quarterly event limit at `tools/l5_scheduler.py:317-318`
- Recent live event detection is implemented in `_has_recent_live_event(...)` at `tools/l5_scheduler.py:217-229`.
- Route 3 calls the decision core before logging a campaign at `tools/l5_scheduler.py:757-763` and writes MAS decisions for both blocked and allowed outcomes at `tools/l5_scheduler.py:737-754` and `tools/l5_scheduler.py:776-785`.
- This aligns with the decision-core rules in `docs/ARCHITECTURE.md:541-552`.
- Test coverage for the dormant-quarterly block exists in `tests/test_l5_scheduler.py:222-236`.

### 6. Comment users are surfaced as candidates but blocked from delivery
- `find_target_seekers_for_event(...)` returns comment users as pseudo-thread IDs like `comment_<fb_user_id>` at `adk_agents/tools/l5_event_tools.py:237-255`.
- `run_event_cycle(...)` explicitly skips those candidates because there is no delivery channel, logs a blocked MAS decision, and does not send/log an event campaign row at `tools/l5_scheduler.py:734-755`.
- Tests verify inclusion of comment users in target discovery in `tests/test_l5_event_tools.py:185-193` and `tests/test_interest_targeting.py:139-146`.

## Missing logic

### 1. Comment users are not protected by no-repeat-per-event
- DM users are checked against prior live `event_campaigns` rows, but comment users are not filtered against the same table before inclusion; the comment query has no `NOT EXISTS` anti-repeat clause at `adk_agents/tools/l5_event_tools.py:226-234`.
- In current runtime this is partly masked because comment users are always blocked later for `no_delivery_channel` at `tools/l5_scheduler.py:734-755`.
- If comment-user delivery is added later, repeat suppression will be missing for that branch.

### 2. Interest matching is absent for comment users
- Comment users are always assigned `interest_score = 0` at `adk_agents/tools/l5_event_tools.py:248`.
- This does not implement the strategy doc's "interest match" expectation for Route 3 in `memory/mas_strategy.md:407-415`.
- Current tests explicitly lock in that limitation by expecting zero interest for comment users in `tests/test_interest_targeting.py:139-146`.

### 3. Route 3 has no stage gating for allowed stages beyond spam/unsubscribed exclusion
- Docs describe Route 3 as Stage 1–5 coverage, excluding Stage 0 User in `memory/mas_strategy.md:426-453`.
- Current target selection only excludes `spam` and `unsubscribed` in DM and comment queries at `adk_agents/tools/l5_event_tools.py:191-197` and `adk_agents/tools/l5_event_tools.py:230-234`.
- Because `EVENT_STAGE_PRIORITIES` includes `Intake` at priority 1 (`adk_agents/tools/l5_event_tools.py:23-29`), Stage 0 / legacy Intake-style records can still be targeted instead of being blocked.
- This is not covered by tests.

### 4. Event-specific interest taxonomy is narrow
- Strategy examples distinguish music/healing events vs class/meditation events in `memory/mas_strategy.md:411-415`.
- Runtime keyword buckets only cover four generic buckets and simple substring checks at `adk_agents/tools/l5_event_tools.py:32-37` and `adk_agents/tools/l5_event_tools.py:121-141`.
- There is no explicit handling for richer event classes such as community events, puja, or organizer/leadership targeting for Stage 5 noted in `memory/mas_strategy.md:247-249`.

## Mismatches with docs / runtime notes

### 1. Stage priority in code does not match the documented Route 3 priority order
- Strategy doc says city event targeting priority is `Registered > Curious Seeker > Follower` in `memory/mas_strategy.md:409-415`.
- Architecture doc says runtime prioritizes `Registered` / `Public Program Seeker` before generic `Seeker` in `docs/ARCHITECTURE.md:434-435`.
- Code additionally gives `18-Week Seeker` higher priority than Registered/Public Program (`adk_agents/tools/l5_event_tools.py:23-29`), which is not stated in either doc.
- This may be intentional, but it is an undocumented behavior change.

### 2. Route 3 target scope differs from strategy emphasis
- Strategy constraints emphasize prioritizing `lead_stage = Seeker` or `Seeker_Public_Program` at `memory/mas_strategy.md:418-420`.
- Code includes `Intake` in priorities and would also accept any normalized stage not equal to spam/unsubscribed, as long as city matches and repeat checks pass (`adk_agents/tools/l5_event_tools.py:23-29`, `adk_agents/tools/l5_event_tools.py:191-197`, `adk_agents/tools/l5_event_tools.py:230-234`).
- That broadens Route 3 beyond the documented target population.

### 3. Architecture says scheduler builds inline plain-text notifications, but runtime now uses ADK first with inline fallback
- Architecture states Route 3 runtime "builds a plain text notification inline" at `docs/ARCHITECTURE.md:429-433`.
- Current scheduler first calls `run_adk_event_advertiser(...)` and only falls back to inline text if ADK returns nothing at `tools/l5_scheduler.py:770-775`.
- This is a documentation lag rather than a bug, but the runtime behavior is no longer purely inline.

### 4. Docs present Route 3 as city-targeted plus interest-match, but production enforcement is asymmetric
- City targeting is enforced in SQL for both sources (`adk_agents/tools/l5_event_tools.py:187-200`, `adk_agents/tools/l5_event_tools.py:226-234`).
- Interest matching only affects ranking of inbox users; it does not act as an eligibility gate, and comment users bypass it entirely with score zero (`adk_agents/tools/l5_event_tools.py:206-220`, `adk_agents/tools/l5_event_tools.py:248`).
- That means the implementation is "city match + optional inbox ranking" rather than full cross-source interest matching implied by `memory/mas_strategy.md:60-63` and `memory/mas_strategy.md:407-415`.

### 5. Tests cover target discovery and eligibility helpers, but not the full Route 3 scheduler flow end-to-end
- There is direct test coverage for `find_target_seekers_for_event(...)`, `log_event_campaign(...)`, and decision-core helper behavior in `tests/test_l5_event_tools.py`, `tests/test_interest_targeting.py`, and `tests/test_l5_scheduler.py:205-236`.
- I did not find tests exercising `run_event_cycle(...)` itself for allowed vs blocked logging, ADK fallback behavior, or comment-user skip handling.
- This leaves the Route 3 orchestration path under-tested compared with the lower-level helpers.

## Bottom line

Route 3 core behavior is mostly present: city filtering, stage normalization, priority sorting, inbox interest scoring, live no-repeat-per-event, and scheduler-side dormant-quarterly blocking are all implemented.

The main gaps are:
1. comment-user targeting is discovery-only and lacks both interest matching and future-proof repeat suppression,
2. allowed-stage filtering is broader than the strategy docs imply,
3. documented priority/runtime notes lag behind the current code, especially the extra `18-Week Seeker` priority and ADK-first message generation,
4. `run_event_cycle(...)` orchestration lacks direct test coverage.
