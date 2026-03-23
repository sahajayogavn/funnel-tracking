# QA Test Plan & Validation Report

**Universal ID**: `doc:test-report-001`
**Date**: 2026-03-23
**Scope**: ADK inbox memory adaptation, MAS strategy normalization, event targeting, and documentation alignment.

## 1. Change Set Under Test

- Runtime loading of markdown knowledge sources into ADK session state in `tools/l5_inbox_mas_runner.py`.
- Session-state propagation for `thread_messages`, `seeker_context`, and `knowledge_context` into the inbox ADK pipeline.
- Lead-stage normalization for warm-up and event routes in `adk_agents/tools/l5_warmup_tools.py` and `adk_agents/tools/l5_event_tools.py`.
- Event target prioritization by normalized stage.
- Documentation and knowledge-source alignment in:
  - `docs/ARCHITECTURE.md`
  - `README.md`
  - `README-vi.md`
  - `.agents/rules/tool-writing.md`
  - `.agents/rules/adk-agent-rules.md`
  - `memory/SOUL.md`
  - `memory/research.md`
  - `memory/agent_memory/faq.md`
  - `memory/agent_memory/lop-hoc.md`
  - `memory/agent_memory/su-kien.md`

## 2. QA Test Plan

### 2.1 Objectives

1. Prove the inbox runtime now loads the new `./memory` sources into `knowledge_context`.
2. Prove the ADK runner receives the expected session state.
3. Prove MAS strategy aliases normalize consistently across warm-up and event flows.
4. Prove event targeting favors `Registered` / `Public Program Seeker` ahead of generic `Seeker`.
5. Verify docs describe implementation truth rather than aspirational behavior.

### 2.2 Test Matrix

| Area | Risk | Validation method | Acceptance criteria |
| --- | --- | --- | --- |
| Knowledge loading | Runtime ignores new memory files | `tests/test_l5_inbox_mas_runner.py::test_load_knowledge_context_includes_all_sources` | All required markdown sources are present in the assembled context |
| ADK session state | Pipeline runs without injected knowledge/state | `tests/test_l5_inbox_mas_runner.py::test_run_adk_pipeline_populates_session_state` | Session state contains `thread_messages`, `seeker_context`, and `knowledge_context` |
| Warm-up strategy normalization | Stage aliases drift between web/runtime/strategy naming | `tests/test_l5_warmup_tools.py` | Journey-engine and legacy aliases normalize to expected strategy stages |
| Event targeting | Wrong users prioritized for event outreach | `tests/test_l5_event_tools.py` | Normalized registered/public-program seekers rank ahead of generic seekers |
| Live ADK smoke | Pipeline shape regresses under real runner | `tests/test_adk_e2e.py` | Either passes with configured env, or cleanly skips when env is absent |
| Docs consistency | Docs overstate route maturity or old commands | targeted file review + grep checks | Docs state inbox is production-wired; routes 1-3 remain scaffolded/logging/template based |

### 2.3 Execution Order

1. Write QA plan.
2. Run focused regression tests for inbox runner, warm-up tools, and event tools.
3. Run ADK E2E smoke suite if env is available; otherwise confirm skip behavior.
4. Re-scan docs for known stale phrases and command drift.
5. Record results and QA signoff.

## 3. Execution Results

### 3.1 Automated tests

Executed on 2026-03-23:

1. Focused regression suite
   - Command: `.venv/bin/python -m pytest tests/test_l5_inbox_mas_runner.py tests/test_l5_warmup_tools.py tests/test_l5_event_tools.py -v`
   - Result: **28 passed**
   - Coverage intent confirmed:
     - knowledge loading includes all required sources
     - ADK session state is populated with `thread_messages`, `seeker_context`, and `knowledge_context`
     - warm-up stage aliases normalize correctly
     - event target selection prioritizes normalized registered/public-program seekers

2. ADK E2E smoke suite
   - Command: `.venv/bin/python -m pytest tests/test_adk_e2e.py -v`
   - Result: **10 skipped**
   - Reason: expected guardrail behavior when `OPENAI_API_BASE` / `OPENAI_API_KEY` are not set in the test environment

### 3.2 Documentation review

Performed targeted grep and file review after edits.

Verified outcomes:

- `README-vi.md` now matches the English README for runtime `knowledge_context`, route maturity, journey taxonomy, and `adk web .` usage.
- `docs/ARCHITECTURE.md` describes inbox reply as the only production-wired ADK route and marks react / warm-up / event as not yet scheduler-wired through ADK end-to-end.
- `.agents/rules/tool-writing.md` and `.agents/rules/adk-agent-rules.md` now reflect the current stage model and ADK command usage.
- Remaining `adk web adk_agents/` hits are only explanatory guardrails in `CLAUDE.md` / `GEMINI.md` saying **not** to use that command.
- No remaining stale `Intake → Engaged → Registered → Attending` funnel string was found in markdown docs.

## 4. QA Signoff

**Status**: PASS WITH E2E SKIP

Focused regression coverage passed, documentation drift checks passed, and the E2E suite skipped cleanly for missing live LLM credentials exactly as designed. No QA defects were identified in the tested change set.

## 5. Signoff Rule

QA signoff requires all focused regression tests to pass, E2E smoke tests to pass or skip cleanly due to missing credentials, and no remaining doc drift in the touched user-facing or rule files.
