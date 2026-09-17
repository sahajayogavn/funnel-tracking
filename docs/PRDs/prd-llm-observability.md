# PRD: Trang `/llm` — quan sát & review mọi lần kích hoạt MAS / LLM

**Universal ID:** `prd:llm-observability-001`
**Design:** `doc:llm-observability-001` — [`../design/llm-observability.md`](../design/llm-observability.md)
**Test plan:** `doc:llm-observability-test-plan-001` — [`../tests/test-plan-llm-observability.md`](../tests/test-plan-llm-observability.md)
**Status:** Proposed
**Date:** 2026-09-17
**Requested by:** operator (page `1548373332058326`)
**Related:** `prd:inbox-decoupled-jobs-001`, `code:tool-scheduler-001`, `code:tool-citydetect-001`

## 1. Bối cảnh — kết quả rà soát các điểm kích hoạt MAS/LLM (2026-09-17)

Rà soát `tools/l5_scheduler.py`, `l5_scheduler_routes.py`, `l5_scheduler_adk.py`,
`l5_inbox_mas_pipeline.py`, `l5_mas_recommend.py`, `fb_pipeline/contracts/l1_city_llm.py`,
`web/src/app/api/action-queue/recommendations/route.ts`.

### 1.1 Kích hoạt **tự động** (scheduler, `--routes` mặc định = `react,reply,warmup,event,classify`)

| # | Route / job | Lịch | Gọi LLM qua | Agent / endpoint | Ghi chú |
| --- | --- | --- | --- | --- | --- |
| A1 | `[CLASSIFY]` | mỗi 30 phút | HTTP trực tiếp `/chat/completions` | `detect_city_batch_llm`, `verify_city_program_batch_llm` (2 lượt/batch), `detect_city_llm` | Không lưu prompt/response; chỉ ghi kết quả vào `users`. |
| A2 | `[REACT]` | mỗi 15 phút | ADK `Runner` | `Reactor` (`LlmAgent`) — 1 lượt/reaction item | Fallback heuristic nếu LLM rỗng. Không lưu. |
| A3 | `[REPLY]`/`[PROPOSE]` | mỗi 15 phút | ADK `Runner` | `MessageClassifier → Responder` (`InboxPipeline`) hoặc `BatchInboxAgent` | Kết quả cuối vào `telegram_hitl_queue`; prompt/state không lưu. `stage_gate` ghi `mas_decisions` nhưng không có LLM I/O. |
| A4 | **`[WARMUP]`** | **hàng ngày 09:00** | ADK `Runner` | `WarmUpComposer` — 1 lượt/seeker (max 5) | **Chủ động chọn seeker dormant và soạn tin** — không có người ra lệnh. |
| A5 | `[EVENT]` | hàng ngày 10:00 | ADK `Runner` | `EventAdvertiser` — 1 lượt/seeker (max 10) | Tương tự A4, chủ động. |
| A6 | `hitl_execution_job` — nhánh regenerate | mỗi 30 giây | ADK `Runner` | `WarmUpComposer` / `EventAdvertiser` / `InboxPipeline` với `feedback` | Kích hoạt khi Telegram trả feedback từ chối. Không lưu. |
| A7 | **`schedule.run_all()` lúc khởi động** | mỗi lần start scheduler | — | Chạy **tất cả** route đã đăng ký ngay lập tức, **kể cả WARMUP và EVENT** bất kể giờ | Restart scheduler = một đợt warmup/event ngoài kế hoạch. |

### 1.2 Kích hoạt **thủ công**

| # | Nguồn | Gọi LLM qua | Agent | Ghi chú |
| --- | --- | --- | --- | --- |
| M1 | `/queues` → `POST /api/action-queue/recommendations` → `tools/l5_mas_recommend.py --thread-ids … --type reply\|warmup\|event\|all` | ADK `Runner` | `BatchInboxAgent`, `WarmUpComposer`, `EventAdvertiser` | Có tiến trình (`mas-progress.tsx`) nhưng prompt/response chỉ ở stdout của tiến trình con. |
| M2 | CLI `tools/l5_inbox_mas_runner.py` (`--target-thread "Hung Bui"`) | ADK | `InboxPipeline` | E2E. |
| M3 | CLI `tools/classify_city.py`, `l5_fetch_fb_city_classify.py`, `l5_scheduler.py --once` | HTTP / ADK | như A1–A5 | |
| M4 | `adk web .` | ADK | bất kỳ | Debug tương tác. |

### 1.3 Kết luận rà soát

1. **Không có lượt gọi LLM nào được lưu prompt + response + state.** `mas_decisions` chỉ lưu
   *quyết định* (blocked/promoted), `telegram_hitl_queue` chỉ lưu *văn bản cuối*. Không thể
   review "LLM đã thấy gì và trả gì" sau khi tiến trình kết thúc.
2. **Warmup (A4) và Event (A5) là hai route duy nhất *chủ động chọn đối tượng* và soạn tin
   mà không có người ra lệnh**; các route còn lại đều phản ứng lại sự kiện đến (tin nhắn,
   reaction, user mới).
3. A7 khiến mọi lần restart scheduler đều kích hoạt warmup/event ngay.

## 2. Mục tiêu

| # | Mục tiêu | Thành phần PRD |
| --- | --- | --- |
| G1 | Warmup **không** chạy theo lịch. Nguồn kích hoạt duy nhất là người vận hành chọn seeker trên `/queues` (M1). Event giữ lịch nhưng cũng không chạy lúc khởi động. | `prd:llm-observability-001:warmup-manual-001` |
| G2 | Mọi lượt gọi LLM (ADK lẫn HTTP trực tiếp) được ghi vào bảng `llm_calls` với đầy đủ prompt/state/response/latency/token/kết quả, gắn `trace_id` cho cả lượt chạy và `subject_id` (thread/user). | `prd:llm-observability-001:trace-001` |
| G3 | Trang `/llm` liệt kê các lượt chạy, group theo trace và route, filter theo ngày/route/trigger/agent/status/subject, mở từng call xem chi tiết, **nút copy** prompt / response / JSON / lệnh tái tạo. | `prd:llm-observability-001:page-001` |
| G4 | Thống kê đầu trang: số call, latency p50/p95, token, tỉ lệ lỗi/rỗng/sanitized theo route và theo ngày — để đo tối ưu MAS. | `prd:llm-observability-001:stats-001` |

## 3. Ngoài phạm vi

- Không đổi prompt/instruction của agent nào.
- Không chỉnh sửa prompt từ UI rồi chạy lại (replay) — chỉ *copy* để tái tạo ở ngoài; replay là PRD sau.
- Không lưu log này ra hệ thống bên ngoài (Langfuse, OTEL); SQLite local là đủ.

## 4. Yêu cầu chức năng

### 4.1 `…:warmup-manual-001`

- R1.1 `ALL_ROUTES` bỏ `warmup`; `--routes` mặc định là `react,reply,event,classify`. Truyền
  `warmup` → scheduler log cảnh báo "warmup is manual-only (use /queues)" và bỏ qua.
- R1.2 `run_scheduler_loop` **không** `schedule.run_all()` cho job theo ngày (`event`);
  chỉ chạy ngay các job theo phút (fetch/react/reply/classify/poller/hitl).
- R1.3 `run_warmup_cycle` giữ lại cho `--once --routes warmup` (test) nhưng ghi
  `trigger='cli'`; nhánh regenerate warmup trong `hitl_execution_job` (A6) vẫn cho phép vì
  nó phản hồi feedback của người.
- R1.4 `l5_mas_recommend.py --type warmup` (M1) là đường chính; giữ nguyên hợp đồng API.

### 4.2 `…:trace-001` — bảng `llm_calls`

- R2.1 Bảng `llm_calls` (schema trong design §3). Mỗi lượt request tới model = 1 dòng.
- R2.2 Một *trace* = một lần chạy route/job (ví dụ 1 tick PROPOSE, 1 batch CLASSIFY, 1 lần
  bấm "Recommend" trên `/queues`). Mọi call trong trace dùng chung `trace_id`; call con của
  `SequentialAgent` có `parent_call_id`.
- R2.3 Bắt ADK bằng `before_model_callback`/`after_model_callback` gắn vào **mọi**
  `LlmAgent` trong `adk_agents/agent.py` → lưu `LlmRequest` (system instruction đã render,
  contents, tools) và `LlmResponse` (text, function calls, usage).
- R2.4 Bắt HTTP trực tiếp bằng wrapper trong `l1_city_llm.chat_completion_text` (và
  `_call_llm_with_retry` để ghi từng lần retry là một call, `attempt` tăng dần).
- R2.5 Ngữ cảnh gọi (trigger, route, page_id, subject_type/id, dry_run) được đặt bởi caller
  qua `contextvars` (`llm_trace.span(route=…, subject=…)`), không truyền tay qua từng hàm.
- R2.6 `status ∈ {ok, empty, sanitized_empty, error, timeout}`; `sanitized_empty` khi
  `_sanitize_reply()` biến output thành rỗng (reasoning leak) — đây là chỉ số tối ưu quan
  trọng.
- R2.7 `outcome_ref` liên kết tới kết quả nghiệp vụ: `telegram_hitl_queue.id`,
  `mas_decisions.id`, hoặc `users.thread_id` (classify).
- R2.8 Ghi *không được* làm hỏng luồng chính: mọi lỗi ghi trace → log WARNING và tiếp tục.
  Ghi qua kết nối riêng, commit từng dòng, ≤ 5 ms/dòng.
- R2.9 Tiến trình con `l5_mas_recommend.py` (M1) ghi thẳng vào DB (cùng file), gắn
  `trigger='web'`, `trace_id` = `jobId` mà API trả về client, để `/queues` có thể link sang
  `/llm?trace=<jobId>`.
- R2.10 Retention: tool `tools/l5_llm_trace_purge.py --older-than 90d` (không tự động).

### 4.3 `…:page-001` — trang `/llm`

- R3.1 Route `web/src/app/llm/page.tsx`, API `GET /api/llm` (list, phân trang cursor theo
  `started_at DESC, id DESC`), `GET /api/llm/[id]` (chi tiết), `GET /api/llm/stats`.
- R3.2 Thanh filter: khoảng ngày (mặc định hôm nay; preset Hôm nay/7 ngày/30 ngày/tuỳ chọn),
  `trigger` (scheduler/web/cli/hitl_regen), `route` (nhóm: **MAS** — Auto-reply, Warmup,
  Event, React, Stage-gate; **LLM** — City/Program detect, City/Program verify),
  `agent`, `status`, `subject` (tìm theo thread_name/thread_id/PSID), `q` (full-text trong
  prompt/response). Filter được đưa lên URL để share.
- R3.3 Danh sách **group theo trace** (một hàng = một lượt chạy: thời điểm, trigger, route,
  số call, tổng latency, tổng token, số ok/empty/error, subject nổi bật). Mở rộng hàng → các
  call theo thứ tự thời gian, thụt lề theo `parent_call_id`.
- R3.4 Chi tiết call (drawer/panel bên phải): metadata; tab **Prompt** (system instruction
  đã render + messages), tab **State** (ADK session state / payload batch), tab **Response**
  (raw, sau sanitize, function calls), tab **Outcome** (link tới hàng đợi HITL / seeker).
- R3.5 **Nút copy** ở mỗi call: `Copy prompt`, `Copy response`, `Copy state`, `Copy JSON`
  (toàn bộ dòng), `Copy repro` (lệnh CLI/pytest tái tạo call với cùng input, xem design §5),
  và ở mức trace: `Copy trace JSON`. Copy dùng `navigator.clipboard`, có toast "Đã copy".
- R3.6 Sort mặc định mới nhất trước; cho phép sort theo latency, token, status.
- R3.7 Đường link chéo: từ `/seekers` (seeker detail) → `/llm?subject=<thread_id>`; từ
  `/queues` (một proposal) → `/llm?trace=<trace_id>`.
- R3.8 Hiển thị tốt ở 1280px; prompt dài hiển thị `<pre>` cuộn, không làm vỡ layout.

### 4.4 `…:stats-001`

- R4.1 Header: tổng call, p50/p95 latency, tổng token in/out, % `empty+sanitized_empty`,
  % `error`, theo bộ lọc hiện tại.
- R4.2 Bảng phân rã theo `route` và biểu đồ cột theo ngày (số call, số lỗi) trong khoảng lọc.
- R4.3 Với CLASSIFY: tỉ lệ `verify` lật kết quả của `detect` (đo được vì cả hai đều là call
  trong cùng trace).

## 5. Tiêu chí nghiệm thu (DoD)

- DoD-1 `l5_scheduler.py --routes warmup` không đăng ký job warmup; restart scheduler không
  sinh call nào có `route='warmup'` trong `llm_calls`.
- DoD-2 Chạy `--once --routes classify,reply,react` + một lần Recommend từ `/queues` → mọi
  call đều có dòng trong `llm_calls` với `trigger` đúng và `trace_id` đúng nhóm.
- DoD-3 `/llm` mở trace của Recommend từ `/queues` qua link, hiển thị đủ prompt/response,
  copy được 5 loại.
- DoD-4 Không call nào thiếu `subject_id` khi route là reply/warmup/event/classify.
- DoD-5 Trace ghi thất bại (mô phỏng DB read-only) → route vẫn hoàn thành, log WARNING.
- DoD-6 Test plan `doc:llm-observability-test-plan-001` pass.

## 6. Ma trận thoả mãn

| PRD ID | Code ID dự kiến | Test ID |
| --- | --- | --- |
| `prd:llm-observability-001:warmup-manual-001` | `code:tool-scheduler-001:warmup-manual`, `code:tool-scheduler-001:no-startup-daily` | `code:test-llm-obs-001:warmup` |
| `prd:llm-observability-001:trace-001` | `code:llm-trace-001:{schema,context,adk-callbacks,http-wrapper,outcome-link,purge}` | `code:test-llm-obs-001:trace` |
| `prd:llm-observability-001:page-001` | `code:web-llm-001:{api-list,api-detail,page,filters,call-panel,copy}` | `code:test-llm-obs-001:page` |
| `prd:llm-observability-001:stats-001` | `code:web-llm-001:{api-stats,stats-header}` | `code:test-llm-obs-001:stats` |
