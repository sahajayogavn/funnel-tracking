# MAS Response Quality Runbook

**Purpose:** review a proposed reply, identify the missing context or failed
runtime layer, and make the smallest safe improvement in the MAS codebase.

MAS creates drafts only. After WebUI or Telegram approval, the independent
executor opens and fills Facebook (default), or sends with explicit `--auto-send`.
It verifies recipient and message freshness first. A draft or approval is never
proof that the customer has been answered. See the delivery amendment in the care
execution contract dated 19/09/2026.

## 1. Review a questionable reply in this order

1. Open `/seekers/<id>` and read the genuine message history. Ignore banners,
   reactions, and the `timestamp` at which the record was imported.
2. Use `messages.message_at` as the canonical customer-message time. Compare it
   with the decision's `now` value, not with the browser fetch time.
3. Inspect `mas_decisions` for `route='inbox_gate'`. Its payload shows the
   deterministic state, `age_hours`, `late`, last customer text, and reason.
4. Open `/llm?trace=<job id>` for a web recommendation, or filter by the
   thread ID for a scheduled run. Read the prompt/state, output, sanitized text,
   status, and error for every call in the trace.
5. Open the matching `action_queue` item. Check `payload_json.source`:
   `inbox_mas` means an LLM generated the draft; `recommendation_engine` means
   a deterministic fallback created it.

## 2. Interpret the evidence

| Evidence | Meaning | Next action |
| --- | --- | --- |
| `inbox_gate.action = reply` | Customer request is fresh (up to 24h). | Draft should answer the actual request. |
| `inbox_gate.action = reply_late` | Request is 24h–7d old. | Draft begins with a brief apology, then answers using current facts. |
| `inbox_gate.action = warmup`, reason `customer_turn_older_than_7d` | Reactive request is stale. | Do not draft as though it arrived today; review as a warm-up/brief candidate. |
| `/llm` contains calls for the trace | MAS reached an LLM. | Improve prompt, retrieved facts, or QA based on the failing call. |
| Job engine is `template_fallback` and no LLM trace exists | LLM preflight or MAS process failed before an LLM callback. | Read the job error/runtime logs; do not diagnose the wording as an LLM-quality issue. |
| `source=recommendation_engine` | Fallback wrote the draft. | Fix deterministic fallback context and templates. |

## 3. Voice and address policy

Default Page voice is **mình / bạn**. It is the safe collective voice of the
CLB when age and relationship are not known.

Use **em / anh / chị** only when the conversation explicitly establishes that
relationship. Use **chúng cháu / cô / chú** only when the customer explicitly
establishes that they are a cô/chú. Never infer age from a Vietnamese name.

For a late reply, use a short, accountable opening:

> Dạ mình xin lỗi bạn vì phản hồi muộn nhé.

Do not use a generic “Cảm ơn bạn đã nhắn…” opener after several days, and do
not ask again for facts already present in the seeker profile.

For class reminders, follow `memory/SOUL.md` directly: gently recall an existing
appointment (“Chào bạn, chúng ta có hẹn lớp thiền …”) only with registration or
appointment evidence for that class. Interest/program_code alone is insufficient.
Otherwise share the schedule without implying commitment. Avoid “mình nhắc bạn”,
“Rất mong”, “đừng quên” and attendance pressure. Closing is optional.
Include only relevant, verified logistics; do not add fees unless currently
asked/requested, invented preparation advice, or unsupported causal connections.
Composer and QA receive SOUL verbatim on agent initialization; restart long-lived
MAS processes after policy changes. QA uses REPAIR for these wording problems.
Care runtime converts a false PASS on known pressure phrases or fee-to-clothing
causation into bounded repair (maximum two rewrites), then fails closed.

Cadence is part of politeness: default to one sent reminder per concrete
Page/thread/class/session date. New command IDs, new wording, added companions
and regeneration do not authorize another reminder. The operator path checks
executed reminders before drafting; drafts and approvals are not sent evidence.
When a sent reminder exists, CareInstructionInterpreter interprets operator
permission semantically and logs the original instruction, literal evidence,
decision and Vietnamese reason. It accepts paraphrases/typing errors such as
“hoàn toàn được phép giục liên. tục”; there is no fixed password. Negated/quoted
phrases and generic urgency do not opt in. Invalid JSON, absent evidence and
provider failure return `care_instruction_unresolved`, not a fabricated denial.
This exception never overrides opt-out or session eligibility. Analyst and QA
can also return `NO_SEND: <Vietnamese explanation>` from conversation evidence;
runtime ends with no outbound draft and returns the reason to the operator.
All Care skips are recorded in `/llm` as `CareAdmissionDecision`, explicitly
`model=deterministic`, `status=skipped`, zero tokens, alongside real model calls.
These audit events remain in the timeline but do not count as LLM requests.

## 4. Where to edit

| Problem | Primary file | What to change |
| --- | --- | --- |
| LLM uses the wrong address or ignores late timing | `adk_agents/agent.py` | Update the active `ReplyComposer` instruction and its QA rule. Keep the deterministic state as the authority. |
| A fallback draft is generic or asks a known fact again | `web/src/app/api/action-queue/recommendations/route.ts` | Update `createFallbackReply`; use `message_at`, latest message intent, and `users.phone/email/city`. |
| Scheduler selects an answered, stale, or inappropriate thread | `fb_pipeline/contracts/l1_conversation_state.py` | Change the pure conversation-state rule and add a focused unit test first. |
| Operator repeats a reminder already sent for the same session | `tools/l5_mas_recommend.py`, `tools/l5_inbox_mas_pipeline.py`, `adk_agents/agent.py` | Check sent session evidence independently of command dedupe; preserve Analyst/QA NO_SEND as a normal outcome. |
| MAS lacks facts about classes or registration | `tools/l5_inbox_mas_context.py` and the relevant `memory/agent_memory/*.md` source | Improve scoped retrieval or correct the source fact. Do not hard-code an unverified schedule in a prompt. |
| LLM run fails but looks successful | `web/src/app/api/action-queue/recommendations/route.ts` and `tools/l5_mas_recommend.py` | Preserve the child-process error/stderr in the job result and show the fallback label in the UI. |
| `/llm` misses a call that reached the model | `fb_pipeline/persistence/l4_llm_trace.py` | Trace at the HTTP/ADK boundary; test success, error, and retry paths. |

## 5. Required regression cases

Add or update a test for every behavior change:

- Follow-up job 38: semantically interpret explicit repeat permission (including
  “hoàn toàn được phép giục liên. tục”), generic urgency, negation, quotation,
  conflicting instructions and malformed/model-error responses. Strict typed
  boolean + verbatim operator evidence; errors fail closed with a distinct reason.
  SQLite integration must record the raw instruction, previous sent action,
  interpretation and final admission/rejection under the same job/thread trace.
  Deterministic decisions are labelled non-model events, with zero tokens and
  excluded from inference statistics. Verify `/llm?trace=<job>` via web build
  and a local route check; no outbound messages are sent for validation.
- trace 36 cadence (`code:tool-mas-recommend-001:reminder-cadence`, satisfying
  `prd:mas-time-aware-001` P2): same Page/thread/class/session already sent →
  no draft, including a new command ID and regenerate. Different session/Page,
  rejected/failed/pending/approved/drafted-only records are not sent evidence.
  Only explicit operator permission, understood semantically, authorizes repeats;
  generic reminders, negated instructions and customer/quoted text do not.
- Care Analyst/QA `NO_SEND: <reason>` stops the workflow and returns an operator
  explanation, with empty outward text and no queue write. Replay trace 36 on
  temporary SQLite with mocked ADK; never resend the production message for QA.
  Unit coverage includes scope/status/override boundaries; integration coverage
  checks recommendation → interpretation → gate → no drafting/no enqueue and
  Analyst/QA exits; deterministic eligibility blocks do not call a model.
  Build the web result-summary path and run conversation/recommendation/wiring
  regressions. This is the offline E2E plan; live delivery is outside this fix.
- fresh class question: direct, fact-grounded answer;
- trace 30 reminder: no fee-to-clothing causation or pressure; a valid appointment
  draft passes, false QA PASS triggers repair, repeated violations return no reply;
- direct SOUL delivery to Composer/QA, independent of librarian summary;
- fees/preparation currently requested: relevant sourced answers remain permitted;
- 24h–7d online-registration request: apology plus online-specific next step;
- over-7d request: no reactive fallback reply;
- profile already has phone/email: do not ask for it again;
- unknown age: Page writes `mình / bạn`;
- explicitly established cô/chú or anh/chị relationship: permitted respectful form;
- LLM unavailable: result is visibly `template_fallback`, preserves failure
  diagnostics, and still follows time/intent rules.

Run the focused Python tests for conversation state and MAS recommendation, then
run `cd web && npm run build` after TypeScript changes. Finally inspect one
real pending draft before sending anything.
