# Architecture Decisions — MAS Strategy Audit

**Universal ID**: `doc:architecture-audit-001`
**Date**: 2026-03-25
**Scope**: Audit of runtime implementation coverage against `memory/mas_strategy.md`, `docs/ARCHITECTURE.md`, and existing architecture decisions for inbox MAS, reaction, warm-up, event, and stage-gate behavior.

## 1. Task Summary

- Map the current implementation by route and execution stage.
- Assign authoritative file ownership for the audit surface so downstream work can avoid overlap.
- Compare runtime behavior against `memory/mas_strategy.md` and `docs/ARCHITECTURE.md`.
- Call out gaps, divergences, and QA validation priorities.

## 2. Implementation Map by Route / Stage

### 2.1 Shared execution stages

| Stage | Purpose | Primary implementation files | Notes |
| --- | --- | --- | --- |
| Data ingestion | Scrape inbox / comments into FrankenSQLite | `/Users/steve/sahajayogavn/funnel-tracking/tools/l5_inbox_mas_runner.py`, `/Users/steve/sahajayogavn/funnel-tracking/fb_pipeline/inbox/l3_pipeline.py`, `/Users/steve/sahajayogavn/funnel-tracking/fb_pipeline/persistence/l4_sqlite_store.py` | Inbox fetch is embedded inside reply-cycle execution, not isolated as a separate service. |
| Browser boundary | Navigate inbox UI and type drafts | `/Users/steve/sahajayogavn/funnel-tracking/fb_pipeline/browser/l2_actions.py`, `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/tools/l5_facebook_tools.py` | Current low-level boundary is draft-only for inbox replies. |
| Agent execution | LLM agents for inbox, reaction, warm-up, event | `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/agent.py`, `/Users/steve/sahajayogavn/funnel-tracking/tools/l5_inbox_mas_runner.py`, `/Users/steve/sahajayogavn/funnel-tracking/tools/l5_scheduler.py` | Inbox route is fully wired through ADK; other routes use ADK content generation selectively. |
| Persistence / audit | Shared DB schema and route ledgers | `/Users/steve/sahajayogavn/funnel-tracking/fb_pipeline/persistence/l4_sqlite_store.py` | `auto_replies`, `reactions`, `warmup_campaigns`, `event_campaigns`, and `mas_decisions` are present. |
| Decision / gating | Actionability, proactive eligibility, stage promotion | `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/tools/l5_seeker_tools.py`, `/Users/steve/sahajayogavn/funnel-tracking/tools/l5_scheduler.py`, `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/tools/l5_stage_tools.py` | Decision logic is split across route tools and scheduler helpers rather than centralized behind one module. |

### 2.2 Inbox MAS route

| Step | Implemented in | Runtime status |
| --- | --- | --- |
| Load knowledge context | `/Users/steve/sahajayogavn/funnel-tracking/tools/l5_inbox_mas_runner.py` | Implemented. Loads `SOUL.md`, FAQ, classes, events, research, and MAS strategy. |
| Fetch latest inbox data | `/Users/steve/sahajayogavn/funnel-tracking/tools/l5_inbox_mas_runner.py` | Implemented. Scrapes inbox before every reply cycle. |
| Select actionable threads | `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/tools/l5_seeker_tools.py` | Implemented. Uses latest customer message vs latest draft acknowledgement. |
| Run classifier + responder | `/Users/steve/sahajayogavn/funnel-tracking/tools/l5_inbox_mas_runner.py`, `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/agent.py` | Implemented. ADK inbox pipeline is production-wired. |
| Sanitize reasoning leaks | `/Users/steve/sahajayogavn/funnel-tracking/tools/l5_inbox_mas_runner.py` | Implemented. Empty sanitized output returns `no_reply`. |
| Navigate to thread | `/Users/steve/sahajayogavn/funnel-tracking/fb_pipeline/browser/l2_actions.py` | Implemented. Includes sidebar reset mitigation in runner. |
| Draft only, never send | `/Users/steve/sahajayogavn/funnel-tracking/fb_pipeline/browser/l2_actions.py`, `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/tools/l5_facebook_tools.py` | Implemented. `--live` is compatibility-only for inbox route. |
| Log reply acknowledgement | `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/tools/l5_facebook_tools.py`, `/Users/steve/sahajayogavn/funnel-tracking/fb_pipeline/persistence/l4_sqlite_store.py` | Implemented with `customer_message_timestamp`. |
| Trigger Telegram follow-up | `/Users/steve/sahajayogavn/funnel-tracking/tools/l5_inbox_mas_runner.py` | Implemented. Heuristic trigger only. |
| Evaluate stage promotion | `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/tools/l5_stage_tools.py` | Partially implemented. Only G1 and G3 are automated. |

### 2.3 Route 1 — React

| Step | Implemented in | Runtime status |
| --- | --- | --- |
| Find unreacted inbox messages and comments | `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/tools/l5_reaction_tools.py` | Implemented. Uses live-only suppression (`dry_run = 0`). |
| Choose reaction | `/Users/steve/sahajayogavn/funnel-tracking/tools/l5_scheduler.py`, `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/agent.py` | Implemented. ADK Reactor is wired with heuristic fallback. |
| Apply live reaction in UI | `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/tools/l5_reaction_tools.py` | Not implemented. UI click path is still a stub. |
| Persist reaction audit | `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/tools/l5_reaction_tools.py`, `/Users/steve/sahajayogavn/funnel-tracking/fb_pipeline/persistence/l4_sqlite_store.py` | Implemented. |

### 2.4 Route 2 — Warm-up

| Step | Implemented in | Runtime status |
| --- | --- | --- |
| Find dormant seekers | `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/tools/l5_warmup_tools.py` | Implemented for inbox users and comment users. |
| Normalize stage / choose strategy | `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/tools/l5_warmup_tools.py`, `/Users/steve/sahajayogavn/funnel-tracking/tools/l5_scheduler.py` | Implemented, but strategy ownership is split between static templates and scheduler cool-sequence logic. |
| Apply proactive eligibility rules | `/Users/steve/sahajayogavn/funnel-tracking/tools/l5_scheduler.py` | Implemented. Checks pending inbox reply, recent live touches, dormant limits, hard stops. |
| Compose message with ADK | `/Users/steve/sahajayogavn/funnel-tracking/tools/l5_scheduler.py`, `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/agent.py` | Implemented. ADK WarmUpComposer is runtime-wired. |
| Deliver via channel | `/Users/steve/sahajayogavn/funnel-tracking/tools/l5_scheduler.py` | Not implemented. Only logs campaigns; comment users are explicitly skipped. |
| Update seeker temperature / cool-step state | `/Users/steve/sahajayogavn/funnel-tracking/tools/l5_scheduler.py` | Implemented, but only mutates counters on non-dry-run sends. |
| Persist warm-up audit and decision ledger | `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/tools/l5_warmup_tools.py`, `/Users/steve/sahajayogavn/funnel-tracking/fb_pipeline/persistence/l4_sqlite_store.py` | Implemented. |

### 2.5 Route 3 — Event advertising

| Step | Implemented in | Runtime status |
| --- | --- | --- |
| Load upcoming events | `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/tools/l5_event_tools.py` | Implemented. |
| Select targets by city / stage / prior sends | `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/tools/l5_event_tools.py`, `/Users/steve/sahajayogavn/funnel-tracking/tools/l5_scheduler.py` | Implemented. Includes stage priority and event suppression for live rows. |
| Apply proactive eligibility rules | `/Users/steve/sahajayogavn/funnel-tracking/tools/l5_scheduler.py` | Implemented. Shared decision core path. |
| Compose event notification | `/Users/steve/sahajayogavn/funnel-tracking/tools/l5_scheduler.py`, `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/agent.py` | Implemented. ADK EventAdvertiser is wired with fallback template. |
| Deliver via channel | `/Users/steve/sahajayogavn/funnel-tracking/tools/l5_scheduler.py` | Not implemented. Logs only. Comment users are skipped. |
| Persist event audit and decision ledger | `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/tools/l5_event_tools.py`, `/Users/steve/sahajayogavn/funnel-tracking/fb_pipeline/persistence/l4_sqlite_store.py` | Implemented. |

### 2.6 Stage-gate implementation coverage

| Gate | Strategy expectation | Current implementation | Status |
| --- | --- | --- | --- |
| G1 User → Follower | Require first touch-point | `evaluate_stage_gate()` promotes `Intake` to `Seeker` when any message exists | Implemented, but naming maps User/Follower to legacy `Intake`/`Seeker`. |
| G2 Follower → Curious | Require explicit seeker question / info intent | No dedicated gate logic | Gap |
| G3 Curious → Registered | Require valid contact plus concrete class/event context | `evaluate_stage_gate()` checks regex contact + program keywords | Partially aligned |
| G4 Registered → Deep Learner | Require attendance threshold | Returns `manual_only` | Gap |
| G5 Deep Learner → Yogi | Require 18-week completion + sustained practice | Returns `manual_only` | Gap |

## 3. File Ownership Map for the Audit

| File / Module | Owner | Read-only access |
| --- | --- | --- |
| `/Users/steve/sahajayogavn/funnel-tracking/docs/report/architecture-decisions.md` | ARCH | SM, QA |
| `/Users/steve/sahajayogavn/funnel-tracking/docs/ARCHITECTURE.md` | ARCH / docs | QA, Dev |
| `/Users/steve/sahajayogavn/funnel-tracking/docs/architecture-decisions.md` | ARCH / docs | QA, Dev |
| `/Users/steve/sahajayogavn/funnel-tracking/memory/mas_strategy.md` | Product / strategy | ARCH, QA, Dev |
| `/Users/steve/sahajayogavn/funnel-tracking/tools/l5_inbox_mas_runner.py` | Inbox route owner | QA, ARCH |
| `/Users/steve/sahajayogavn/funnel-tracking/tools/l5_scheduler.py` | Scheduler / decision-core owner | QA, ARCH |
| `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/agent.py` | ADK agent owner | QA, ARCH |
| `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/tools/l5_facebook_tools.py` | Inbox browser-wrapper owner | QA, ARCH |
| `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/tools/l5_seeker_tools.py` | Inbox query owner | QA, ARCH |
| `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/tools/l5_reaction_tools.py` | Reaction route owner | QA, ARCH |
| `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/tools/l5_warmup_tools.py` | Warm-up route owner | QA, ARCH |
| `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/tools/l5_event_tools.py` | Event route owner | QA, ARCH |
| `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/tools/l5_stage_tools.py` | Stage-gate owner | QA, ARCH |
| `/Users/steve/sahajayogavn/funnel-tracking/fb_pipeline/browser/l2_actions.py` | Browser safety-boundary owner | QA, ARCH |
| `/Users/steve/sahajayogavn/funnel-tracking/fb_pipeline/persistence/l4_sqlite_store.py` | Shared persistence owner | QA, ARCH |
| `/Users/steve/sahajayogavn/funnel-tracking/tools/inbox_mas_runner.py` | Compatibility shim owner | Read-only for all |
| `/Users/steve/sahajayogavn/funnel-tracking/tools/scheduler.py` | Compatibility shim owner | Read-only for all |
| `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/tools/facebook_tools.py` | Compatibility shim owner | Read-only for all |
| `/Users/steve/sahajayogavn/funnel-tracking/adk_agents/tools/seeker_tools.py` | Compatibility shim owner | Read-only for all |

## 4. Alignment Summary — `mas_strategy` vs Runtime Architecture

### 4.1 Strong alignment

- Inbox MAS matches the current safety direction: draft generation, reasoning sanitization, and manual human send are aligned between strategy and runtime.
- Architecture layering is mostly respected: scheduler and runner stay in L5, browser automation stays in L2, and shared persistence stays in L4.
- Route inventory matches strategy: inbox, react, warm-up, and event routes all exist in code and scheduler wiring.
- Shared decision logging exists through `mas_decisions`, which aligns with the strategy requirement for auditable allow/block outcomes.
- Temperature support exists in schema and scheduler logic, including dormant and unsubscribed hard stops.

### 4.2 Partial alignment

- Strategy presents all four routes as end-to-end operational behaviors, but only Inbox MAS is fully production-complete. Warm-up and event routes generate and log messages without delivery; react logs decisions without real CDP application.
- ADK agent definitions exist for Reactor, WarmUpComposer, and EventAdvertiser, but route ownership is split with scheduler-side fallbacks and heuristics, so the architecture is only partially agent-driven.
- Stage mapping is conceptually aligned, but runtime relies on alias normalization between strategy labels and legacy DB values instead of one canonical stage model.
- The decision core implements anti-spam and route arbitration, but those rules live inside `tools/l5_scheduler.py` rather than an independently reusable policy boundary.

## 5. Explicit Gaps and Divergences

### 5.1 Delivery completeness gaps

1. **Route 1 React is not operationally complete**
   - Decision and logging are implemented.
   - Real Facebook reaction application remains a stub.
   - Strategy language implies a usable route; runtime currently provides audit-only behavior.

2. **Route 2 Warm-up is log-only**
   - Message composition and decisioning work.
   - No delivery path is wired.
   - Comment-user candidates are selected, then blocked due to no delivery channel.

3. **Route 3 Event is log-only**
   - Event targeting and composition work.
   - No live send path is wired.
   - Comment-user targets are also blocked due to no delivery channel.

### 5.2 Stage-model divergences

4. **G2 is absent**
   - Strategy requires a dedicated Follower → Curious check for seeker-initiated questions.
   - Runtime jumps from generic `Seeker` semantics straight to G3-style registration checks.

5. **G4 and G5 are manual placeholders**
   - Strategy describes attendance and practice-based progression.
   - Runtime returns `manual_only` with no implementation.

6. **Canonical stage vocabulary is not unified**
   - Strategy uses `User`, `Follower`, `Curious Seeker`, `Registered`, `Deep Learner`, `Sahaja Yogi`.
   - DB/runtime still uses `Intake`, `Seeker`, `Seeker_Public_Program`, `Seeker_18_Weeks`, `Seed`, and alias normalization tables.
   - This increases drift risk across scheduler, stage tools, event targeting, and QA assertions.

### 5.3 Architectural divergences

7. **Decision core is centralized only for proactive routes**
   - Warm-up and event use `_evaluate_proactive_eligibility()`.
   - Inbox reply actionability still lives separately in `find_unreplied_threads()`.
   - Strategy presents a more uniform gate-first execution model than the current split implementation.

8. **Inbox fetch is coupled to reply-cycle execution**
   - `run_inbox_cycle()` always performs scraping before actionability checks.
   - Architecture docs describe layered flow correctly, but operationally this makes reply route validation dependent on scrape success and browser availability.

9. **Comment-user coverage is only analytical, not actionable**
   - Strategy diagrams present comments and reactions as first-class route inputs.
   - Runtime can classify/comment-route candidates for react and proactive targeting, but proactive delivery to comment users is intentionally blocked.

10. **Route-level QA gates exist mostly as scheduler rules, not explicit reusable contracts**
   - Strategy depicts formal gate checkpoints.
   - Runtime spreads equivalent checks across scheduler helpers, route tools, and inbox-specific sanitization / query logic.

## 6. Recommended Validation Focus for QA

### 6.1 Highest priority

1. **Inbox draft safety boundary**
   - Verify no inbox path auto-sends.
   - Regress `--live` behavior to confirm it stays compatibility-only.
   - Validate sanitize-empty output returns `no_reply` and creates no false acknowledgement.

2. **Latest-customer-turn suppression**
   - Prove one drafted reply suppresses repeat drafting until a newer customer message arrives.
   - Verify navigation failures and draft failures do not suppress later work.

3. **Scheduler decision-core arbitration**
   - Confirm proactive routes block when inbox has pending reply.
   - Confirm 24-hour recent-live-touch suppression works across warm-up, event, and inbox audit tables.
   - Confirm dormant quarterly event limit and unsubscribed hard stop behavior.

### 6.2 Medium priority

4. **Stage-gate coverage boundaries**
   - Assert only G1 and G3 are automated today.
   - Ensure G4/G5 remain blocked/manual rather than silently auto-promoting.
   - Add explicit negative tests for missing G2 semantics so future drift is visible.

5. **Stage-alias normalization consistency**
   - Compare warm-up, event, scheduler, and stage-tool interpretations of the same `lead_stage` values.
   - Focus on `Intake/User`, `Seeker/Follower/Curious`, `Seeker_Public_Program/Registered`, `Seeker_18_Weeks/Deep Learner`, and `Seed/Sahaja Yogi`.

6. **Comment-user route handling**
   - Verify comment-origin users are either blocked with explicit ledger entries or handled consistently.
   - Ensure they are never treated as deliverable DM targets.

### 6.3 Lower priority but important for doc integrity

7. **Compatibility shim behavior**
   - Confirm legacy `tools/inbox_mas_runner.py`, `tools/scheduler.py`, `adk_agents/tools/facebook_tools.py`, and `adk_agents/tools/seeker_tools.py` remain thin re-export shims.

8. **Docs-to-runtime drift scan**
   - Recheck that docs do not imply live route delivery where runtime only logs.
   - Specifically watch Route 1, Route 2, Route 3 wording in architecture and strategy docs.

## 7. Final Architectural Decision

The current codebase supports **one production-complete human-in-the-loop route** (Inbox MAS) and **three decision-and-audit routes that are only partially operational** (React, Warm-up, Event).

For audit and implementation planning, downstream teams should treat the system as:

- **Inbox MAS**: production-ready draft workflow with strong safety controls.
- **React / Warm-up / Event**: scheduler-driven decision engines with persistence and ADK-assisted content generation, but not full delivery automation.
- **Stage progression**: partially automated and still dependent on legacy stage aliases, with only G1 and G3 implemented.

Any future claim of “full MAS route coverage” should be rejected until delivery semantics, stage-gate completeness, and canonical stage naming are unified.