# Test Plan — LLM Observability (`/llm`, `llm_calls`, warmup thủ công)

**Universal ID:** `doc:llm-observability-test-plan-001`
**Verifies:** `prd:llm-observability-001` / `doc:llm-observability-001`
**Date:** 2026-09-17

Quy ước: test mới trong `tests/`, tag `# code:test-llm-obs-001:<component>`. Test ADK dùng
agent giả (`LlmAgent` với model mock qua `litellm` mock hoặc `BaseLlm` stub) — không cần LLM
thật; test có tag *(live)* auto-skip khi thiếu `OPENAI_API_BASE`.

## 0. Lệnh chạy tổng

```bash
.venv/bin/python -m pytest tests/test_llm_obs_warmup.py tests/test_llm_obs_trace.py -v
cd web && npm run build && node ../tests/test_llm_obs_page.mjs
```

Static:

```bash
grep -n "run_all()" tools/l5_scheduler.py                     # kỳ vọng: 0
grep -n '"warmup"' tools/l5_scheduler.py | grep ALL_ROUTES    # kỳ vọng: 0
grep -c "traced(" adk_agents/agent.py                         # kỳ vọng: = số LlmAgent (6)
grep -n "before_model_callback" adk_agents/agent.py fb_pipeline/persistence/l4_llm_trace.py
```

## 1. `code:test-llm-obs-001:warmup` — `tests/test_llm_obs_warmup.py`

| ID | Tên | Cách làm | Pass khi |
| --- | --- | --- | --- |
| W-01 | Mặc định không có warmup | `setup_schedule(page, dry_run, routes=ALL_ROUTES, …)` | `registered` không chứa "warmup"; `schedule.get_jobs()` không có job `run_warmup_cycle` |
| W-02 | `--routes warmup` bị từ chối | parse args với `--routes react,warmup` | log WARNING chứa "manual-only"; chỉ react được đăng ký |
| W-03 | `--once --routes warmup` không cờ | | Không chạy warmup, exit code 0, WARNING |
| W-04 | `--once --routes warmup --allow-manual-routes` | mock `run_warmup_cycle` | Được gọi đúng 1 lần với `trigger='cli'` trong span |
| W-05 | Khởi động không chạy job ngày | Đăng ký event daily + fetch 15 phút; mock cả hai; gọi phần "initial run" của loop | fetch được gọi, event **không** |
| W-06 | `/queues` vẫn kích hoạt warmup | `POST /api/action-queue/recommendations {type:'warmup', threadIds:[…]}` (mock python) | spawn `l5_mas_recommend.py --type warmup --job-id <jobId>` |
| W-07 | Regenerate warmup từ feedback vẫn chạy | Row `telegram_hitl_queue` route warmup, `feedback_text` | `run_adk_warmup_composer` được gọi với `feedback`, span `trigger='hitl_regen'` |

## 2. `code:test-llm-obs-001:trace` — `tests/test_llm_obs_trace.py`

### 2.1 Module `l4_llm_trace`

| ID | Tên | Cách làm | Pass khi |
| --- | --- | --- | --- |
| T-01 | Schema tạo được | `init_db` trên DB tạm | Bảng `llm_calls` + 4 index tồn tại |
| T-02 | `span` đặt ngữ cảnh | `with span(trigger='scheduler', route='propose', subject=('thread','t1','Hung Bui'))`: `start_call(...)` | Dòng có đúng trigger/route/route_group='MAS'/subject_*; `seq_in_trace=1` |
| T-03 | Span lồng nhau kế thừa trace | span ngoài route='recommend' → span trong route='warmup' | Cùng `trace_id`; call trong có route='warmup' |
| T-04 | Không span → vẫn ghi | `start_call` ngoài span | `trigger='unknown'`, `trace_id` không NULL |
| T-05 | `end_call` tính duration/status | ok text / "" / raise | `ok` / `empty` / `error` + `error` message; `duration_ms ≥ 0` |
| T-06 | `mark_sanitized` | call ok → sanitized "" | `status='sanitized_empty'`, `sanitized_text=''`, `response_text` giữ nguyên |
| T-07 | `link_outcome` cho cả span | 3 call trong span → `link_outcome('hitl_queue', 42)` | cả 3 có `outcome_ref='42'` |
| T-08 | Ghi lỗi không làm hỏng caller | DB path read-only | `start_call` trả None, không raise; caller tiếp tục |
| T-09 | Cắt prompt lớn | `LLM_TRACE_MAX_PROMPT_BYTES=1000`, prompt 5 KB | lưu ≤ 1000 + hậu tố `…[truncated]` |
| T-10 | route_group đúng | mọi route trong danh sách | classify_* → LLM, còn lại → MAS |

### 2.2 ADK callbacks

| ID | Tên | Cách làm | Pass khi |
| --- | --- | --- | --- |
| T-11 | Mọi LlmAgent được `traced` | Import `adk_agents.agent`, duyệt các `LlmAgent` module-level | tất cả có `before_model_callback` và `after_model_callback` không None |
| T-12 | before/after ghi 1 dòng/lượt model | `Runner` với `InboxPipeline` (Classifier→Responder) và model stub trả text | 2 dòng, cùng trace, `parent_call_id` của Responder = id Classifier, `agent_name` đúng |
| T-13 | System instruction đã render | state `thread_messages="xin chào"` | `system_prompt` chứa "xin chào", không chứa `{thread_messages?}` |
| T-14 | State snapshot không rò khoá tạm | | `state_json` không chứa `_llm_trace_*` |
| T-15 | usage_metadata | stub trả usage | `tokens_in/out` đúng; stub không usage → NULL, không lỗi |
| T-16 | Callback lỗi không chặn model | `start_call` raise (mock) | Runner vẫn trả output |

### 2.3 HTTP wrapper

| ID | Tên | Cách làm | Pass khi |
| --- | --- | --- | --- |
| T-17 | `chat_completion_text` ghi call (non-stream) | mock `requests.post` | 1 dòng `agent_name='city_llm'`, `messages_json` = payload messages, `response_text` đúng |
| T-18 | Streaming | mock SSE | `response_text` = text ghép, status ok |
| T-19 | Retry = nhiều dòng | `_call_llm_with_retry` fail 2 lần rồi ok | 3 dòng, `attempt` 1,2,3, 2 error + 1 ok, cùng trace |
| T-20 | detect vs verify | `_post_scrape_llm_city_classify` với mock LLM, 1 batch | 2 dòng: route `classify_detect` và `classify_verify`, cùng trace, `subject_type='batch'` |
| T-21 | outcome classify | như T-20 | `outcome_type='user_classification'` cho cả 2 |

### 2.4 Tích hợp route

| ID | Tên | Cách làm | Pass khi |
| --- | --- | --- | --- |
| T-22 | PROPOSE ghi trace | `run_propose_cycle(background=False)` với MAS mock, 2 thread | 1 trace, ≥ 2 call, mỗi call `subject_id` = thread, `outcome_type='hitl_queue'` |
| T-23 | REACT ghi trace | `run_react_cycle` với 1 reaction | 1 call route react, subject reaction |
| T-24 | Recommend từ web | chạy `l5_mas_recommend.py --thread-ids t1 --type warmup --job-id job-x` với mock | `trace_id='job-x'`, `trigger='web'` |
| T-25 | DoD-4: subject không NULL | chạy T-22, T-23, T-20 rồi `SELECT COUNT(*) WHERE route IN (…) AND subject_id IS NULL` | 0 |
| T-26 *(live)* | Hung Bui E2E | `l5_inbox_mas_runner.py --target-thread "Hung Bui"` | `llm_calls` có 2 dòng (Classifier, Responder) cho thread Hung Bui, `outcome_ref` = id hàng đợi |

## 3. `code:test-llm-obs-001:page` — `tests/test_llm_obs_page.mjs` + kiểm tra thủ công

Fixture: `tests/fixtures/llm_calls_seed.sql` — 6 trace / 15 call trải 3 ngày, đủ mọi
route/trigger/status, một trace `trace_id='job-x'`.

| ID | Tên | Cách làm | Pass khi |
| --- | --- | --- | --- |
| G-01 | `GET /api/llm` mặc định hôm nay | seed có 2 trace hôm nay | Trả 2 trace, mới nhất trước |
| G-02 | Filter ngày | `from=<3 ngày trước>&to=<hôm nay>` | 6 trace |
| G-03 | Filter route/group/trigger/status | từng tham số | Kết quả khớp seed; kết hợp = AND |
| G-04 | Filter subject | `subject=Hung` | Trace có `subject_label LIKE '%Hung%'` |
| G-05 | Full-text `q` | `q=knowledge_context` | Chỉ call có chuỗi đó trong prompt |
| G-06 | `trace=job-x` | | 1 trace, đủ 4 call |
| G-07 | Phân trang | `limit=2` | có `nextCursor`; trang 2 không trùng |
| G-08 | `GET /api/llm/[id]` | | Trả đủ `system_prompt/messages_json/state_json/response_text` |
| G-09 | `/api/llm/stats` | cùng filter G-02 | `total=15`, p50/p95 số, `by_route` có mọi route, `by_day` 3 mục |
| G-10 | Trang render | `npm run build` + fetch `/llm` | 200, có filter bar, stats, list |
| G-11 | Hàng trace màu theo tệ nhất | trace có 1 error | class `status-error` |
| G-12 | Panel chi tiết & 4 tab | click call | Prompt/State/Response/Outcome đều có nội dung |
| G-13 | 5 nút copy | click từng nút (mock `navigator.clipboard.writeText`) | Nội dung theo bảng §5.3 design; `Copy repro` HTTP **không** chứa API key thật |
| G-14 | Filter lên URL | chọn filter → URL đổi; reload giữ filter | |
| G-15 | Link chéo | seeker detail có link `/llm?subject=…`; `/queues` proposal có `/llm?trace=…`; nav có `/llm` | |
| G-16 | Prompt dài | seed 200 KB | `<pre>` cuộn, không tràn ngang, trang không đơ |
| G-17 | DB không có bảng `llm_calls` | DB cũ | Trang hiện empty-state "Chưa có dữ liệu — chạy scheduler với trace", không 500 |

## 4. Regression

```bash
.venv/bin/python -m pytest tests/test_l5_inbox_mas_runner.py tests/test_adk_wiring.py \
  tests/test_cool_sequence.py tests/test_mas_recommend.py tests/test_l5_warmup_tools.py -v
.venv/bin/python -m pytest tests/test_adk_e2e.py tests/test_e2e_mas_strategy_hung_bui.py -v   # live
```

`test_adk_wiring.py` phải được cập nhật nếu nó assert cấu trúc `LlmAgent` (callback mới).

## 5. Ma trận thoả mãn

| PRD component | Test IDs |
| --- | --- |
| `prd:llm-observability-001:warmup-manual-001` | W-01…W-07 |
| `prd:llm-observability-001:trace-001` | T-01…T-26 |
| `prd:llm-observability-001:page-001` | G-01…G-08, G-10…G-17 |
| `prd:llm-observability-001:stats-001` | G-09, G-10 |
