# Stage Gates Audit

## Scope

Audit of whether the stage-transition QA gates described in `memory/mas_strategy.md` are implemented in code/tests, with cross-checks against `docs/ARCHITECTURE.md` and `docs/architecture-decisions.md`.

## Inputs reviewed

- `memory/mas_strategy.md:66-86`
- `memory/mas_strategy.md:109-250`
- `memory/mas_strategy.md:457-569`
- `memory/mas_strategy.md:608-667`
- `docs/ARCHITECTURE.md:621-630`
- `docs/architecture-decisions.md:1-334`
- `adk_agents/tools/l5_stage_tools.py:151-203`
- `adk_agents/tools/l5_warmup_tools.py:24-48`
- `adk_agents/tools/l5_warmup_tools.py:211-219`
- `adk_agents/tools/l5_event_tools.py:23-29`
- `adk_agents/tools/l5_event_tools.py:158-268`
- `tools/l5_inbox_mas_runner.py:346-384`
- `tests/test_stage_gates.py:8-146`

## Architecture-decision relevance

`docs/architecture-decisions.md` is about inbox draft safety, unreplied-thread gating, and human-in-the-loop reply semantics, not stage-transition design. It does not define stage-gate ownership or change the stage criteria from `mas_strategy.md`. So it does not resolve the stage-gate gaps below.

## Expected behavior from strategy/docs

### Strategy stages
- Strategy defines User → Follower → Curious Seeker → Registered → Deep Learner → Sahaja Yogi in `memory/mas_strategy.md:12-16`.
- It explicitly maps both Follower and Curious Seeker to DB/runtime `Seeker` in `memory/mas_strategy.md:111`, `memory/mas_strategy.md:139`, and `docs/ARCHITECTURE.md:625-629`.

### QA gates expected
- G1 User → Follower: at least one touch-point recorded in `memory/mas_strategy.md:482`.
- G2 Follower → Curious: seeker proactively asks for info in DM/comment in `memory/mas_strategy.md:483`.
- G3 Curious → Registered: valid phone/email plus specific class/event in `memory/mas_strategy.md:484`.
- G4 Registered → Deep: attended at least 3/4 intro sessions in `memory/mas_strategy.md:485`.
- G5 Deep → Yogi: completed 18 weeks plus at least 3 months practice in `memory/mas_strategy.md:486`.
- Execution guide says stage evaluation should check gate, update `lead_stage` on pass, and log reason on fail in `memory/mas_strategy.md:612-619`.

## What is implemented

### G1 is implemented
- `evaluate_stage_gate()` normalizes current stage, checks `_has_touchpoint(thread_id)`, and for `Intake` promotes to `Seeker` in `adk_agents/tools/l5_stage_tools.py:156-179`.
- This matches the documented G1 criterion at a basic inbox-thread level.
- Tests cover pass/fail in `tests/test_stage_gates.py:9-29` and `tests/test_stage_gates.py:31-48`.

### G3 is implemented
- For normalized `Seeker`, `evaluate_stage_gate()` requires:
  - valid contact via `_extract_valid_contact()` in `adk_agents/tools/l5_stage_tools.py:92-123`
  - specific program detection via `_has_specific_program()` in `adk_agents/tools/l5_stage_tools.py:127-132`
- On success it promotes to `Seeker_Public_Program` in `adk_agents/tools/l5_stage_tools.py:181-195`.
- Tests cover pass, missing program, and missing contact in `tests/test_stage_gates.py:50-121`.

### G4 and G5 exist only as manual placeholders
- Registered/public-program stage returns `gate="G4"`, `reason="manual_only"` in `adk_agents/tools/l5_stage_tools.py:197-198`.
- Deep/18-week stage returns `gate="G5"`, `reason="manual_only"` in `adk_agents/tools/l5_stage_tools.py:200-201`.
- Tests cover only this manual-only behavior in `tests/test_stage_gates.py:122-146`.

### Stage evaluation is wired into inbox processing
- After drafting an inbox reply, runner calls `evaluate_stage_gate(thread_id)` in `tools/l5_inbox_mas_runner.py:358`.
- Promotion outcomes are logged to `mas_decisions` only when promoted in `tools/l5_inbox_mas_runner.py:359-373`.
- So automatic stage evaluation currently happens in the inbox MAS path, not as a shared cross-route engine.

## What is missing

### G2 is not implemented
- There is no code branch that separately detects Follower → Curious behavior in `adk_agents/tools/l5_stage_tools.py:169-203`.
- Runtime jumps from:
  - `Intake` → `Seeker` via G1
  - `Seeker` → `Seeker_Public_Program` via G3
- Because both Follower and Curious Seeker use `Seeker`, there is no persisted distinction available for a real G2 transition.
- There are no G2 tests in `tests/test_stage_gates.py:8-146`.

### G4 pass criteria are not implemented
- Strategy requires attendance evidence `>=3/4` in `memory/mas_strategy.md:485`.
- No attendance source, lookup, or promotion logic exists in `adk_agents/tools/l5_stage_tools.py:197-198`.

### G5 pass criteria are not implemented
- Strategy requires 18-week completion and 3 months practice in `memory/mas_strategy.md:486`.
- No such validation exists in `adk_agents/tools/l5_stage_tools.py:200-201`.

### Fail/manual outcomes are not logged like strategy describes
- Strategy says fail should keep stage and log reason in `memory/mas_strategy.md:617-618`.
- Runner only logs `mas_decisions` when promotion happens in `tools/l5_inbox_mas_runner.py:359-373`.
- Failed G1/G3 and manual G4/G5 outcomes are returned but not logged there.

### No comment-side gate evaluation path
- Strategy allows DM or comment-based stage signals in `memory/mas_strategy.md:69-76` and `memory/mas_strategy.md:482-484`.
- Current stage gate logic only works on inbox `thread_id`, `users`, and `messages` in `adk_agents/tools/l5_stage_tools.py:47-71`.
- No equivalent gate evaluation exists for `comment_users` or comment-only seekers.

## Mapping / normalization findings

### Intentional normalization exists, but it hides some documented distinctions
- `normalize_lead_stage()` maps aliases across DB/runtime/display labels in `adk_agents/tools/l5_warmup_tools.py:24-48` and `adk_agents/tools/l5_warmup_tools.py:211-219`.
- `Seeker_Public_Program` normalizes to `Public Program Seeker`.
- `Seeker_18_Weeks` normalizes to `18-Week Seeker`.
- `Seed`, `Sahaja_Yogi`, `Sahaja_Yogi_Dedicated`, and `Sahaja_Mahayogi` also normalize to `18-Week Seeker` in `adk_agents/tools/l5_warmup_tools.py:41-47`.

### Main mismatch caused by normalization
- Docs keep Stage 4 Deep Learner and Stage 5 Sahaja Yogi distinct in `docs/ARCHITECTURE.md:629-630` and `memory/mas_strategy.md:228-232`.
- But proactive tools normalize all Stage 5 values back into `18-Week Seeker`, so downstream warm-up/event logic cannot distinguish Stage 4 from Stage 5 after normalization.

### Event prioritization keeps legacy/display labels mixed together
- Event priorities include both `Registered` and `Public Program Seeker` in `adk_agents/tools/l5_event_tools.py:23-29`.
- Current canonical DB stage `Seeker_Public_Program` normalizes to `Public Program Seeker`, not `Registered`, via `adk_agents/tools/l5_warmup_tools.py:32-35`.
- This suggests compatibility support for mixed labels rather than a single canonical runtime vocabulary.

## Test coverage summary

### Covered
- G1 pass/fail: `tests/test_stage_gates.py:9-48`
- G3 pass/fail: `tests/test_stage_gates.py:50-121`
- G4/G5 manual-only placeholders: `tests/test_stage_gates.py:122-146`

### Missing
- No G2 tests
- No tests for fail/manual decision logging
- No tests for comment-based stage progression
- No tests protecting Stage 5 from collapsing into Stage 4 through normalization

## Bottom line

### Implemented now
- G1 auto-promotion
- G3 auto-promotion
- G4/G5 manual-only placeholders
- Inbox-runner integration that evaluates gates after drafting reply text

### Not implemented now
- G2 transition logic
- G4 actual pass logic
- G5 actual pass logic
- Shared cross-route stage engine
- Fail/manual decision logging parity with strategy
- Comment-side gate evaluation

### Main doc/runtime mismatch
The strategy describes five QA gates in a sequential stage model, but code currently implements only G1 and G3 automatically. The biggest structural reason is that both Follower and Curious Seeker share the same persisted stage value `Seeker`, so G2 is described in docs but not representable as a distinct code transition with current state modeling.
