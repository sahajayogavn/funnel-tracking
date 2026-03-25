# Dev1 Stage Gate Audit

## Scope

Audit of stage-gate implementation versus strategy/docs, focused on:
- QA gates G1-G5
- `lead_stage` normalization/mapping
- whether strategy transitions are automated, implemented elsewhere, or still manual

## Inputs reviewed

- `memory/mas_strategy.md:66-86`
- `memory/mas_strategy.md:457-556`
- `memory/mas_strategy.md:608-667`
- `docs/ARCHITECTURE.md:621-630`
- `adk_agents/tools/l5_stage_tools.py:151-203`
- `adk_agents/tools/l5_warmup_tools.py:24-48`
- `adk_agents/tools/l5_warmup_tools.py:211-219`
- `adk_agents/tools/l5_event_tools.py:23-29`
- `adk_agents/tools/l5_event_tools.py:158-268`
- `tools/l5_inbox_mas_runner.py:346-384`
- `tests/test_stage_gates.py:8-146`

## Audit input gap

- Required file `docs/report/architecture-decisions.md` is missing, so no stage-gate ownership/decision record could be verified from that source.

## Expected strategy from docs

### Strategy model
- Strategy defines six journey stages: User → Follower → Curious Seeker → Registered → Deep Learner → Sahaja Yogi in `memory/mas_strategy.md:12-16`.
- Strategy explicitly separates:
  - Stage 1 Follower = DB `Seeker` in `memory/mas_strategy.md:109-133`
  - Stage 2 Curious Seeker = DB `Seeker` in `memory/mas_strategy.md:137-168`
- Architecture doc mirrors this collapse: both Follower and Curious Seeker use runtime/DB value `Seeker` in `docs/ARCHITECTURE.md:625-629`.

### Gate definitions
- G1 User → Follower requires at least one touch-point in `memory/mas_strategy.md:482`.
- G2 Follower → Curious requires seeker proactively asking for info via DM/comment in `memory/mas_strategy.md:483`.
- G3 Curious → Registered requires valid phone/email plus a specific class/event in `memory/mas_strategy.md:484`.
- G4 Registered → Deep requires attending at least 3/4 intro sessions in `memory/mas_strategy.md:485`.
- G5 Deep → Yogi requires completing 18 weeks plus 3 months of practice in `memory/mas_strategy.md:486`.
- Execution guide says stage evaluation should check current `users.lead_stage`, pass/fail QA gate, update `lead_stage`, and log reason in `memory/mas_strategy.md:612-619`.

## Implemented logic

### 1. G1 is implemented and auto-promotes
- `evaluate_stage_gate()` normalizes the current stage and, when normalized stage is `Intake`, checks `_has_touchpoint(thread_id)` in `adk_agents/tools/l5_stage_tools.py:156-172`.
- On success it updates `users.lead_stage` to `Seeker` and returns `gate="G1"`, `to_stage="Seeker"`, `reason="touchpoint_recorded"` in `adk_agents/tools/l5_stage_tools.py:172-179`.
- Test coverage exists for both pass and fail in `tests/test_stage_gates.py:9-29` and `tests/test_stage_gates.py:31-48`.

### 2. G3 is implemented and auto-promotes
- When normalized stage is `Seeker`, the code extracts phone/email from user row or messages via `_extract_valid_contact()` in `adk_agents/tools/l5_stage_tools.py:92-123`.
- It separately checks for a specific program keyword via `_has_specific_program()` in `adk_agents/tools/l5_stage_tools.py:127-132`.
- If both are satisfied it updates `users.lead_stage` to `Seeker_Public_Program` and returns `gate="G3"`, `to_stage="Seeker_Public_Program"`, `reason="valid_contact_and_program_detected"` in `adk_agents/tools/l5_stage_tools.py:181-195`.
- Test coverage exists for pass, missing program, and missing contact in `tests/test_stage_gates.py:50-76`, `tests/test_stage_gates.py:78-99`, and `tests/test_stage_gates.py:100-121`.

### 3. G4 and G5 are explicitly manual-only
- Registered/public-program stage returns `gate="G4"`, `reason="manual_only"` in `adk_agents/tools/l5_stage_tools.py:197-198`.
- Deep/18-week stage returns `gate="G5"`, `reason="manual_only"` in `adk_agents/tools/l5_stage_tools.py:200-201`.
- Tests cover these manual-only outcomes in `tests/test_stage_gates.py:122-146`.

### 4. Stage-gate evaluation is actually executed in the inbox flow
- After drafting and logging an inbox reply, `tools/l5_inbox_mas_runner.py` calls `evaluate_stage_gate(thread_id)` in `tools/l5_inbox_mas_runner.py:346-358`.
- If promotion occurs, it logs a MAS decision with route `stage_gate` and decision `promoted` in `tools/l5_inbox_mas_runner.py:359-373`.
- So the currently implemented automatic stage transitions happen only when an inbox thread reaches the inbox MAS reply flow, not as a general cross-route stage engine.

## Missing logic

### 1. G2 is not implemented
- `evaluate_stage_gate()` has branches for normalized stages `Intake`, `Seeker`, `Registered/Public Program Seeker`, and `18-Week Seeker`, but no branch that evaluates “Follower → Curious” separately in `adk_agents/tools/l5_stage_tools.py:169-203`.
- Because both Follower and Curious Seeker collapse to the same DB/runtime value `Seeker`, the code has no persisted state boundary to distinguish “Stage 1 Follower” from “Stage 2 Curious Seeker”.
- Result: the documented G2 gate in `memory/mas_strategy.md:483` is not represented in code or tests.

### 2. No implementation of G4 criteria
- Strategy requires attendance of at least 3/4 intro sessions in `memory/mas_strategy.md:485`.
- No attendance lookup, class-completion record, or analogous validation appears in `adk_agents/tools/l5_stage_tools.py:197-198`; it is only `manual_only`.
- No tests verify G4 pass criteria; only manual-only behavior is tested in `tests/test_stage_gates.py:122-146`.

### 3. No implementation of G5 criteria
- Strategy requires 18-week completion and 3 months of practice in `memory/mas_strategy.md:486`.
- `adk_agents/tools/l5_stage_tools.py:200-201` returns `manual_only` with no supporting checks.
- No tests exist for G5 pass criteria.

### 4. No explicit transition logging for blocked/manual outcomes
- Strategy says pass/fail should log transition reason in `memory/mas_strategy.md:617-618`.
- Runner logs only when `stage_result.get("promoted")` is true in `tools/l5_inbox_mas_runner.py:359-373`.
- Failed G1/G3 and manual-only G4/G5 outcomes are returned to caller but not logged to `mas_decisions` here.

### 5. No stage-gate coverage for comment/react-only progression
- Strategy G1/G2 mention comment/react/DM signals in `memory/mas_strategy.md:482-484`.
- `evaluate_stage_gate()` only accepts `thread_id` and reads `users` + inbox `messages` tables in `adk_agents/tools/l5_stage_tools.py:47-71`.
- There is no equivalent stage-gate path for `comment_users`/comments, so comment-only seekers are outside this implementation.

## Mismatches with docs

### 1. G2 exists in docs but is skipped in runtime behavior
- Docs define a linear gate sequence G1 → G2 → G3 in `memory/mas_strategy.md:464-467` and `memory/mas_strategy.md:480-485`.
- Runtime behavior jumps from:
  - `Intake` → `Seeker` by G1 in `adk_agents/tools/l5_stage_tools.py:169-179`
  - `Seeker` → `Seeker_Public_Program` by G3 in `adk_agents/tools/l5_stage_tools.py:181-195`
- Because `Seeker` represents both Follower and Curious Seeker, G2 is not enforceable and is effectively omitted.

### 2. Strategy/architecture runtime naming differs from stage-tool branches
- Strategy alignment says Stage 3 DB value is `Seeker_Public_Program` in `memory/mas_strategy.md:567` and Stage 4 DB value is `Seeker_18_Weeks` in `memory/mas_strategy.md:568`.
- `normalize_lead_stage()` maps:
  - `seeker_public_program` → `Public Program Seeker`
  - `seeker_18_weeks` → `18-Week Seeker`
  in `adk_agents/tools/l5_warmup_tools.py:32-40` and `adk_agents/tools/l5_warmup_tools.py:211-219`.
- `evaluate_stage_gate()` therefore branches on normalized display labels (`Public Program Seeker`, `18-Week Seeker`) rather than canonical DB values for later stages in `adk_agents/tools/l5_stage_tools.py:197-201`.
- This is intentional normalization, but it means stage-tool logic mixes canonical DB writes (`Seeker_Public_Program`) with display-label reads (`Public Program Seeker`, `18-Week Seeker`).

### 3. Stage aliasing collapses Stage 5 into Stage 4 for proactive tools
- `normalize_lead_stage()` maps `Seed`, `Sahaja_Yogi`, `Sahaja_Yogi_Dedicated`, and `Sahaja_Mahayogi` all to `18-Week Seeker` in `adk_agents/tools/l5_warmup_tools.py:41-47`.
- This conflicts with the documented stage model where Stage 5 is distinct in `docs/ARCHITECTURE.md:630` and `memory/mas_strategy.md:228-232`.
- Consequence: warm-up/event tooling cannot distinguish Deep Learner from Sahaja Yogi after normalization.

### 4. Event priorities include `Registered` even though normalization returns `Public Program Seeker`
- Event targeting priorities include both `Registered` and `Public Program Seeker` in `adk_agents/tools/l5_event_tools.py:23-29`.
- But `normalize_lead_stage()` does not map canonical `Seeker_Public_Program` to `Registered`; it maps it to `Public Program Seeker` in `adk_agents/tools/l5_warmup_tools.py:32-35`.
- So `Registered` in event priorities appears to support legacy/manual labels, while current canonical DB values normalize to `Public Program Seeker`.

### 5. Strategy says all stage transitions should pass QA gate before transition; implementation is inbox-reply-triggered only
- Execution guide describes a general stage evaluation phase in `memory/mas_strategy.md:608-619`.
- Actual code evaluates only during inbox reply drafting in `tools/l5_inbox_mas_runner.py:346-358`.
- Therefore documented stage-gate evaluation is not a shared lifecycle step across all routes/signals.

## Test coverage assessment

### Covered
- G1 pass/fail in `tests/test_stage_gates.py:9-48`
- G3 pass/fail permutations in `tests/test_stage_gates.py:50-121`
- G4/G5 manual-only behavior in `tests/test_stage_gates.py:122-146`

### Missing test coverage
- No G2 tests at all
- No tests for blocked/manual outcomes being logged
- No tests for comment-based stage signals
- No tests validating Stage 5 remains distinguishable after normalization

## Bottom line

### What is implemented now
- Automatic G1: `Intake` → `Seeker`
- Automatic G3: `Seeker` → `Seeker_Public_Program`
- Manual-only placeholders for G4 and G5
- Inbox-runner integration that performs stage evaluation after drafting a reply

### What is not implemented now
- G2 logic
- G4 pass criteria
- G5 pass criteria
- General cross-route stage engine
- Failure/manual decision logging parity with strategy
- Comment-side stage-gate evaluation

### Main architectural mismatch
- The strategy defines separate Follower and Curious Seeker stages, but both collapse to runtime value `Seeker`. That makes G2 undocumented in code terms and effectively unimplementable without another state signal or additional persisted discriminator.
