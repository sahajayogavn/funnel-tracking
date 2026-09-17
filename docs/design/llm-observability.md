# Design: LLM Observability — bảng `llm_calls` và trang `/llm`

**Universal ID:** `doc:llm-observability-001`
**Satisfies:** `prd:llm-observability-001` — [`../PRDs/prd-llm-observability.md`](../PRDs/prd-llm-observability.md)
**Related:** `doc:inbox-decoupled-jobs-001`, `code:agent-mas-001`, `code:tool-citydetect-001`, `code:tool-scheduler-001`
**Date:** 2026-09-17

## 1. Vấn đề

MAS hiện chạy rồi "bỏ vào log": prompt đã render, session state, response thô, số lần
retry, thời gian, token — tất cả biến mất khi tiến trình kết thúc. Người vận hành chỉ thấy
văn bản cuối trong Telegram/`/queues`, nên không thể trả lời các câu hỏi tối ưu MAS như:
"Responder đã nhìn thấy `knowledge_context` nào khi viết câu này?", "Bao nhiêu % lượt bị
sanitize thành rỗng?", "Verify của CLASSIFY lật bao nhiêu kết quả của detect?".

Có **hai đường gọi LLM** khác nhau về kỹ thuật, và thiết kế phải bắt cả hai tại đúng một
"cửa" cho mỗi đường, không rải instrumentation vào từng route:

| Đường | Nơi gọi | Cửa để bắt |
| --- | --- | --- |
| ADK (`LlmAgent` qua LiteLLM) | mọi MAS route: reply/react/warmup/event, recommend từ web, regenerate HITL | `before_model_callback` / `after_model_callback` của `LlmAgent` — nhận `LlmRequest` (instruction **đã render** `{var?}`, contents, tools) và `LlmResponse` (text, function_calls, `usage_metadata`) |
| HTTP trực tiếp (`requests.post …/chat/completions`) | `fb_pipeline/contracts/l1_city_llm.py` (detect / batch / verify) | `chat_completion_text()` — điểm duy nhất tất cả 3 hàm đi qua, kể cả streaming |

## 2. Kiến trúc

```
 caller (route/job)                   llm_trace (contextvars)              SQLite
 ─────────────────                    ───────────────────────              ──────
 with llm_trace.span(                 trace_id, trigger, route,
      trigger="scheduler",  ───────►  page_id, subject, dry_run   ─┐
      route="propose",                                              │
      subject=("thread", tid)):                                     │
                                                                    ▼
   ADK Runner ──► LlmAgent.before_model_callback ──► llm_trace.start_call(req) ──► INSERT llm_calls (status='running')
               ◄── LlmAgent.after_model_callback ◄── llm_trace.end_call(resp)  ──► UPDATE llm_calls (response, usage, status)

   l1_city_llm.chat_completion_text ──► start_call(payload) / end_call(text|error) ──► như trên

   _sanitize_reply(text) ──► llm_trace.mark_sanitized(call_id, cleaned)            ──► UPDATE sanitized_text, status
   enqueue HITL / log_mas_decision ──► llm_trace.link_outcome(call_ids, ref)      ──► UPDATE outcome_ref
```

- Module mới `fb_pipeline/persistence/l4_llm_trace.py` (`code:llm-trace-001:*`) — không phụ
  thuộc ADK, chỉ SQLite + `contextvars`. Tất cả hàm nuốt lỗi (log WARNING), **không bao giờ**
  làm hỏng luồng nghiệp vụ (R2.8).
- Kết nối SQLite riêng cho trace (không dùng chung `conn` của route) để commit từng dòng mà
  không ảnh hưởng transaction của caller; `busy_timeout=5000`.
- Trace không có `span()` bao ngoài (ví dụ ai đó gọi agent từ REPL) vẫn được ghi với
  `trigger='unknown'`, `trace_id` sinh mới mỗi call — không được bỏ sót.

## 3. Schema `llm_calls` (`code:llm-trace-001:schema`)

```sql
CREATE TABLE IF NOT EXISTS llm_calls (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  trace_id        TEXT NOT NULL,          -- một lượt chạy route/job; = jobId của /queues khi trigger='web'
  parent_call_id  INTEGER,                -- call cha trong SequentialAgent (Classifier → Responder)
  seq_in_trace    INTEGER NOT NULL,       -- thứ tự trong trace
  attempt         INTEGER DEFAULT 1,      -- lần retry (HTTP path)
  started_at      DATETIME NOT NULL,      -- UTC ISO
  finished_at     DATETIME,
  duration_ms     INTEGER,
  trigger         TEXT NOT NULL,          -- scheduler | web | cli | hitl_regen | unknown
  route           TEXT NOT NULL,          -- propose | react | warmup | event | stage_gate | recommend | classify_detect | classify_verify
  route_group     TEXT NOT NULL,          -- MAS | LLM   (dẫn xuất từ route, lưu để filter nhanh)
  agent_name      TEXT,                   -- MessageClassifier | Responder | Reactor | WarmUpComposer | EventAdvertiser | BatchInboxAgent | city_llm
  model           TEXT,
  page_id         TEXT,
  subject_type    TEXT,                   -- thread | user | reaction | batch
  subject_id      TEXT,                   -- thread_id / PSID / reaction id / 'batch:<n>'
  subject_label   TEXT,                   -- thread_name để hiển thị & tìm
  dry_run         BOOLEAN DEFAULT 1,
  system_prompt   TEXT,                   -- instruction đã render (ADK) / messages[0] system (HTTP)
  messages_json   TEXT,                   -- contents (ADK) / messages (HTTP), JSON
  state_json      TEXT,                   -- ADK session state tại thời điểm gọi / payload batch
  tools_json      TEXT,                   -- tool declarations nếu có
  response_text   TEXT,                   -- text thô model trả về
  response_json   TEXT,                   -- function_calls / usage / raw chunks tóm tắt
  sanitized_text  TEXT,                   -- sau _sanitize_reply (NULL nếu không áp dụng)
  status          TEXT NOT NULL,          -- running | ok | empty | sanitized_empty | error | timeout
  error           TEXT,
  tokens_in       INTEGER,
  tokens_out      INTEGER,
  outcome_type    TEXT,                   -- hitl_queue | mas_decision | user_classification | none
  outcome_ref     TEXT                    -- id tương ứng
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_started ON llm_calls(started_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_llm_calls_trace   ON llm_calls(trace_id, seq_in_trace);
CREATE INDEX IF NOT EXISTS idx_llm_calls_subject ON llm_calls(subject_type, subject_id, started_at);
CREATE INDEX IF NOT EXISTS idx_llm_calls_route   ON llm_calls(route, started_at);
```

Kích thước: prompt CLASSIFY batch ~20–40 KB, Responder ~5–10 KB. 500 call/ngày ≈ 10 MB/ngày.
Chấp nhận được; `tools/l5_llm_trace_purge.py --older-than 90d` (thủ công) và tuỳ chọn
`LLM_TRACE_MAX_PROMPT_BYTES` (mặc định 256 KB) cắt prompt quá lớn với dấu `…[truncated]`.

## 4. Điểm gắn instrumentation

### 4.1 Context (`code:llm-trace-001:context`)

```python
# fb_pipeline/persistence/l4_llm_trace.py
@contextmanager
def span(*, trigger: str, route: str, page_id: str | None = None,
         subject: tuple[str, str, str | None] | None = None,   # (type, id, label)
         dry_run: bool = True, trace_id: str | None = None):
    """Đặt ngữ cảnh cho mọi call LLM bên trong. Lồng nhau được: span con kế thừa trace_id,
    ghi đè subject (ví dụ span route 'propose' → span mỗi thread)."""
```

Nơi bọc `span`:

| Caller | `trigger` | `route` | subject |
| --- | --- | --- | --- |
| `run_propose_cycle` → mỗi thread | scheduler | propose | thread |
| `run_react_cycle` → mỗi item | scheduler | react | reaction |
| `run_event_cycle` → mỗi seeker | scheduler | event | thread |
| `run_warmup_cycle` (chỉ còn CLI) | cli | warmup | thread |
| `run_classify_cycle` → `_post_scrape_llm_city_classify` → mỗi batch | scheduler | classify_detect / classify_verify | batch (+ mỗi user ghi vào `state_json.users[]`) |
| `hitl_execution_job` nhánh regenerate | hitl_regen | warmup / event / propose | thread |
| `l5_mas_recommend.py main()` | web | recommend → span con theo `--type` | thread; `trace_id = --job-id` (API truyền xuống) |
| `l5_inbox_mas_runner.py main()` | cli | propose | thread |

### 4.2 ADK callbacks (`code:llm-trace-001:adk-callbacks`)

```python
# adk_agents/agent.py — gắn cho MỌI LlmAgent qua helper, không sửa từng agent
from fb_pipeline.persistence.l4_llm_trace import adk_before_model, adk_after_model

def traced(agent: LlmAgent) -> LlmAgent:
    agent.before_model_callback = adk_before_model   # ghi request, trả None (không chặn)
    agent.after_model_callback  = adk_after_model    # ghi response, trả None
    return agent

classifier = traced(LlmAgent(name="MessageClassifier", ...))
```

- `before`: `callback_context.agent_name`, `llm_request.config.system_instruction`,
  `llm_request.contents`, `llm_request.tools_dict`, `callback_context.state.to_dict()`
  → `start_call()`; `call_id` cất trong `callback_context.state["_llm_trace_call_id"]`
  (khoá tạm, prefix `_` để không rò vào template).
- `after`: `llm_response.content.parts[*].text`, `function_call`, `usage_metadata`
  → `end_call()`. `parent_call_id`: call trước đó cùng trace trong cùng `SequentialAgent`
  (lấy từ `state["_llm_trace_last_call_id"]`).
- Nếu ADK phiên bản không expose `usage_metadata` → để NULL, không lỗi.

### 4.3 HTTP wrapper (`code:llm-trace-001:http-wrapper`)

`chat_completion_text(url, payload, headers, timeout)` bọc: `start_call(messages=payload["messages"],
model=payload["model"], agent_name="city_llm")` → gọi như cũ → `end_call(text)` hoặc
`end_call(error=…)`. `_call_llm_with_retry` truyền `attempt` qua contextvar để mỗi retry là một
dòng riêng cùng `seq_in_trace`, `attempt` tăng. `detect_*` vs `verify_*` phân biệt bằng span con
`route="classify_detect"` / `"classify_verify"` đặt ngay trong `_post_scrape_llm_city_classify`.

### 4.4 Sanitize & outcome (`code:llm-trace-001:outcome-link`)

- `_sanitize_reply()` (`l5_inbox_mas_pipeline.py`) gọi `llm_trace.mark_sanitized(cleaned)`
  cho call cuối cùng của span hiện tại; rỗng → `status='sanitized_empty'`.
- `send_proposal_to_telegram` / insert `telegram_hitl_queue` → `link_outcome("hitl_queue", id)`
  cho **mọi** call trong span hiện tại. `log_mas_decision` → `link_outcome("mas_decision", id)`.
  CLASSIFY: khi ghi `users` → `link_outcome("user_classification", thread_id)`.

### 4.5 Tiến trình con từ web (`code:web-llm-001:job-link`)

`POST /api/action-queue/recommendations` đã sinh `jobId`; truyền xuống
`l5_mas_recommend.py --job-id <jobId>` → `span(trigger="web", trace_id=jobId)`. Response
của API thêm `llmTraceUrl: "/llm?trace=<jobId>"`; `mas-progress.tsx` hiển thị link "Xem lượt
gọi LLM" khi job xong.

## 5. Trang `/llm` (`code:web-llm-001:*`)

### 5.1 API

| Endpoint | Tham số | Trả về |
| --- | --- | --- |
| `GET /api/llm` | `from,to` (ISO date, mặc định hôm nay theo local), `trigger[]`, `route[]`, `group` (MAS/LLM), `agent[]`, `status[]`, `subject` (khớp `subject_id` hoặc `subject_label LIKE`), `q` (LIKE trên `system_prompt/messages_json/response_text`), `trace`, `cursor`, `limit` (mặc định 50 trace) | `{traces:[{trace_id, started_at, trigger, route, route_group, page_id, calls:[…tóm tắt…], n_calls, n_ok, n_empty, n_error, duration_ms, tokens_in, tokens_out, subjects:[label…]}], nextCursor}` — group trong SQL bằng `GROUP BY trace_id` + subquery calls |
| `GET /api/llm/[id]` | — | toàn bộ dòng `llm_calls` (prompt/state/response đầy đủ) |
| `GET /api/llm/stats` | cùng filter | `{total, p50_ms, p95_ms, tokens_in, tokens_out, pct_empty, pct_error, by_route:[…], by_day:[{day, calls, errors}], classify_flip_rate}` |

`queries.ts` dùng `better-sqlite3` readonly như các trang khác (`code:web-db-004:llm-calls`).
Route API là `dynamic` (không cache) theo `fullstack-rules.md`.

### 5.2 Layout

```
┌ /llm ──────────────────────────────────────────────────────────────────────────────────┐
│ [Hôm nay ▾][7 ngày][30 ngày][từ __ đến __]  Nhóm: (MAS)(LLM)  Route: ☐Auto-reply ☐Warmup │
│ ☐Event ☐React ☐Stage-gate ☐City detect ☐City verify   Trigger: ☐scheduler ☐web ☐cli    │
│ ☐hitl_regen   Status: ☐ok ☐empty ☐sanitized ☐error   Subject: [________]  q: [_______] │
├─ Stats ────────────────────────────────────────────────────────────────────────────────┤
│ 312 calls · p50 4.1s · p95 18s · 1.2M in / 84k out · 6.4% rỗng/sanitized · 1.9% lỗi    │
│ [bar theo ngày]                       [bảng theo route: calls / p50 / %empty / %error] │
├─ Traces (mới nhất trước) ──────────────────────────────────────────────┬─ Call detail ─┤
│ ▸ 09:32:10 scheduler · PROPOSE · 6 calls · 3 ok 1 sanitized · 41s      │ MessageClassifier│
│ ▾ 09:30:02 web · RECOMMEND(warmup) · 4 calls · job 8f2c…  [Copy trace] │ propose · thread │
│     ├ WarmUpComposer · Nguyễn Ngọc Giàu · ok · 6.2s · 3.1k/210         │ Hung Bui · ok    │
│     ├ WarmUpComposer · Trần A · sanitized_empty · 5.9s      ← đỏ       │ [Prompt][State]  │
│ ▸ 09:00:00 scheduler · CLASSIFY · 2 calls (detect, verify) · batch:12  │ [Response][Out.] │
│ ▸ 08:45:31 hitl_regen · WARMUP · 1 call                                │ <pre>…</pre>     │
│                                                                        │ [Copy prompt]    │
│                                                                        │ [Copy response]  │
│                                                                        │ [Copy state]     │
│                                                                        │ [Copy JSON]      │
│                                                                        │ [Copy repro]     │
└────────────────────────────────────────────────────────────────────────┴──────────────────┘
```

- Nhãn route hiển thị: `propose`→"MAS: Auto-reply", `warmup`→"MAS: Warmup",
  `event`→"MAS: Event", `react`→"MAS: React", `stage_gate`→"MAS: Stage-gate",
  `recommend`→"MAS: Recommend (web)", `classify_detect`→"LLM: City/Program detect",
  `classify_verify`→"LLM: City/Program verify". Map ở một chỗ (`web/src/lib/llm-labels.ts`).
- Hàng trace tô màu theo tệ nhất trong các call (error > sanitized_empty > empty > ok).
- Filter lên URL (`useSearchParams`) → link share được; `?trace=` mở sẵn trace + call đầu.
- Panel chi tiết là client component; danh sách là server component nhận `searchParams`
  (theo `nextjs-app-router-patterns`).

### 5.3 Nút copy (`code:web-llm-001:copy`)

| Nút | Nội dung |
| --- | --- |
| Copy prompt | `system_prompt` + "\n\n---\n\n" + messages render dạng `role: text` |
| Copy response | `response_text` (raw); nếu có `sanitized_text` khác raw → kèm khối `--- sanitized ---` |
| Copy state | `state_json` pretty |
| Copy JSON | toàn bộ dòng `llm_calls` (pretty, `ensure_ascii=false`) |
| Copy repro | với ADK: `.venv/bin/python tools/l5_llm_replay.py --call-id <id>` (tool nhỏ đọc `state_json` + `agent_name` và chạy lại đúng agent với cùng state — thuộc PRD sau, nhưng lệnh được sinh sẵn); với HTTP: `curl` tương đương (URL/API key thay bằng `$OPENAI_API_BASE`/`$OPENAI_API_KEY`, **không** copy key thật) |
| Copy trace JSON | mảng calls của trace |

### 5.4 Link chéo

- `seeker-detail.tsx`: nút "Lượt gọi LLM" → `/llm?subject=<thread_id>&from=<30 ngày>`.
- `action-queues.tsx`: proposal có `payload.trace_id` → "Xem LLM" → `/llm?trace=…`.
- Nav `layout.tsx`: thêm `/llm`.

## 6. Warmup thủ công (`code:tool-scheduler-001:warmup-manual`, `:no-startup-daily`)

- `ALL_ROUTES = {"react", "reply", "event", "classify"}`; `MANUAL_ONLY_ROUTES = {"warmup"}`.
  `--routes` chứa `warmup` → `logger.warning("[WARMUP] is manual-only; trigger it from /queues")`,
  loại khỏi tập route. `--once --routes warmup` vẫn được phép (test/E2E) qua cờ riêng
  `--allow-manual-routes`.
- `run_scheduler_loop`: thay `schedule.run_all()` bằng chạy ngay các job có `unit` là
  seconds/minutes; job `every().day.at()` chờ đúng giờ. (`schedule` cho phép kiểm tra
  `job.unit`.)
- Sau thay đổi, nguồn kích hoạt warmup còn lại: `/queues` (M1), regenerate theo feedback (A6),
  CLI có cờ. Cả ba đều xuất phát từ hành động của người → khớp yêu cầu.
- Event vẫn theo lịch 10:00 (chưa có yêu cầu đổi), nhưng không còn chạy lúc khởi động.

## 7. Ranh giới file cho agent implement

| Bước | File được sửa / tạo | Không đụng |
| --- | --- | --- |
| warmup-manual-001 | `tools/l5_scheduler.py` | routes, adk, web |
| trace-001 | mới `fb_pipeline/persistence/l4_llm_trace.py`, `l4_sqlite_store.py` (CREATE TABLE), `adk_agents/agent.py` (helper `traced`), `fb_pipeline/contracts/l1_city_llm.py` (`chat_completion_text`), `tools/l5_scheduler_routes.py` + `l5_scheduler.py` + `l5_mas_recommend.py` + `l5_inbox_mas_runner.py` + `l5_fetch_fb_city_classify.py` (bọc `span`), `l5_inbox_mas_pipeline.py` (`_sanitize_reply` mark), `l5_telegram_hitl.py` (link outcome), mới `tools/l5_llm_trace_purge.py` | `web/` |
| page-001 + stats-001 | mới `web/src/app/llm/page.tsx`, `web/src/app/api/llm/{route.ts,[id]/route.ts,stats/route.ts}`, `web/src/components/llm-{trace-list,call-panel,filter-bar,stats}.tsx`, `web/src/lib/llm-labels.ts`, `queries.ts` (+ hàm llm), `types.ts`, `layout.tsx` (nav), `seeker-detail.tsx` & `action-queues.tsx` (link), `recommendations/route.ts` (`--job-id`, `llmTraceUrl`) | mọi file Python trừ đọc schema |

Thứ tự: warmup-manual-001 (10 phút, độc lập) → trace-001 → page-001/stats-001 (cần dữ liệu
thật trong `llm_calls` để phát triển UI; agent web có thể dùng fixture SQL trong test plan).
