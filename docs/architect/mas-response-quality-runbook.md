# MAS Response Quality Runbook

**Purpose:** review a proposed reply, identify the missing context or failed
runtime layer, and make the smallest safe improvement in the MAS codebase.

MAS creates drafts only. A human reviews and sends them in Facebook; a draft or
approval is never proof that the customer has been answered.

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

## 4. Where to edit

| Problem | Primary file | What to change |
| --- | --- | --- |
| LLM uses the wrong address or ignores late timing | `adk_agents/agent.py` | Update the active `ReplyComposer` instruction and its QA rule. Keep the deterministic state as the authority. |
| A fallback draft is generic or asks a known fact again | `web/src/app/api/action-queue/recommendations/route.ts` | Update `createFallbackReply`; use `message_at`, latest message intent, and `users.phone/email/city`. |
| Scheduler selects an answered, stale, or inappropriate thread | `fb_pipeline/contracts/l1_conversation_state.py` | Change the pure conversation-state rule and add a focused unit test first. |
| MAS lacks facts about classes or registration | `tools/l5_inbox_mas_context.py` and the relevant `memory/agent_memory/*.md` source | Improve scoped retrieval or correct the source fact. Do not hard-code an unverified schedule in a prompt. |
| LLM run fails but looks successful | `web/src/app/api/action-queue/recommendations/route.ts` and `tools/l5_mas_recommend.py` | Preserve the child-process error/stderr in the job result and show the fallback label in the UI. |
| `/llm` misses a call that reached the model | `fb_pipeline/persistence/l4_llm_trace.py` | Trace at the HTTP/ADK boundary; test success, error, and retry paths. |

## 5. Required regression cases

Add or update a test for every behavior change:

- fresh class question: direct, fact-grounded answer;
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
