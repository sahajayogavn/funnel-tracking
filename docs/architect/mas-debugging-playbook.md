# MAS Debugging Playbook

**Universal ID:** `doc:mas-debugging-playbook-001`

**Mục tiêu:** khi một reply/case cụ thể của inbox MAS bị coi là tệ (sai ý,
sai nhận thức thời gian, lặp lại câu hỏi, escalate vô lý...), tài liệu này là
điểm vào duy nhất để một agent (hoặc người) tái tạo lại toàn bộ context của
lần chạy đó và tìm nguyên nhân **mà không cần dò lại từ đầu**. Khi được giao
"Hãy phân tích MAS cho case sau, tìm nguyên nhân", đọc tài liệu này trước,
sau đó chạy công cụ ở Mục 1.

Đây là tài liệu điều tra. Runbook về *quy tắc trả lời đúng* (giọng văn, khi
nào apology, khi nào không hỏi lại thông tin...) nằm ở
[mas-response-quality-runbook.md](mas-response-quality-runbook.md) — đọc
song song, không thay thế nhau.

## 1. Công cụ: `tools/l5_mas_trace_debug.py`

Script chỉ đọc (`code:mas-debug-001`), không gọi LLM, không ghi DB. Đọc trực
tiếp `llm_calls`, `mas_decisions`, `action_queue`, `messages`, và tính lại
`compute_conversation_state()` tại thời điểm hiện tại để so sánh với những gì
hệ thống thực sự đã quyết định lúc chạy.

### Lệnh chính — dùng đầu tiên luôn

```bash
.venv/bin/python tools/l5_mas_trace_debug.py report <trace_id_hoặc_thread_id>
```

Một lệnh này trả về:

- gate xác định thời gian/trạng thái hội thoại tính **lại ngay bây giờ**
  (`already_answered`, `open_question`, `stale`, ... — xem
  `fb_pipeline/contracts/l1_conversation_state.py`);
- toàn bộ hội thoại gốc (`kind='message'`, đã lọc banner/reaction);
- lịch sử `mas_decisions` (gate, stage_gate, escalation) của thread;
- các `action_queue` item đã tạo ra cho thread đó và trạng thái cuối
  (`pending` / `approved` / `rejected` / `executed`) — **`rejected` nghĩa là
  người vận hành đã tự tay từ chối draft đó, luôn kiểm tra trước khi kết
  luận**;
- **toàn bộ trace_id khác từng chạy trên cùng thread** — nếu danh sách này
  dài, gần như chắc chắn có vấn đề chạy lặp lại lãng phí token (xem Mục 6,
  case 2026-09-17);
- timeline từng bước LLM call (agent, token in/out, thời lượng, status);
- input/output đầy đủ (system prompt, messages, state, tools, response) của
  từng bước, mặc định cắt ở 1200 ký tự — thêm `--full` để lấy nguyên văn.

### Các lệnh con khác

| Lệnh | Dùng khi nào |
| --- | --- |
| `timeline <trace_id>` | Chỉ cần nhìn nhanh có bao nhiêu vòng lặp, agent nào tốn token nhất. |
| `call <call_id> --full` | Đọc nguyên văn input/output của đúng một bước (ví dụ bước QA escalate vô lý). |
| `thread <thread_id> [--json]` | Không có trace_id trong tay, chỉ có tên/thread; xem gate + decisions + queue mà không cần chọn trace. |
| `find --thread <thread_id>` | Liệt kê mọi trace_id từng chạy trên thread này. |
| `find --subject <tên khách>` | Liệt kê trace_id theo tên khách khi chưa có thread_id. |

`report --json` xuất JSON thô nếu cần đưa dữ liệu vào một script khác.

**Lưu ý về trace_id:** đã quan sát được `trace_id` bị trùng giữa hai lần
chạy độc lập cách nhau vài phút/giờ (xem Mục 6). Vì vậy `get_trace_calls`
sắp theo `id` tăng dần (thứ tự insert thật), không chỉ dựa vào
`seq_in_trace`. Khi nghi ngờ một trace có hai "đợt" `seq_in_trace` quay lại
từ 1, đó chính là dấu hiệu trùng id — dùng `find --thread` để xem có bao
nhiêu trace_id thật đang bị lẫn vào nhau.

## 2. Quy trình điều tra chuẩn

1. Chạy `report <id>`. Đọc phần "Deterministic gate, re-run now" trước tiên
   — nó cho biết **lẽ ra** hệ thống phải làm gì (skip / reply / reply_late /
   warmup) dựa trên dữ liệu message thô, độc lập với việc LLM đã làm gì.
2. So sánh với `mas_decisions` (`route=inbox_gate`) của cùng thời điểm chạy,
   nếu có — trigger `scheduler` (từ `run_inbox_mas_loop.sh`) luôn ghi dòng
   này; trigger `manual_recommendation` (nút "Gợi ý trả lời" trên dashboard)
   **cố ý bỏ qua gate này theo thiết kế** (`tools/l5_mas_recommend.py`), nên
   đừng ngạc nhiên nếu không thấy `inbox_gate` — điều đó tự nó là một manh
   mối (case này chỉ có thể đến từ đường thủ công).
3. Đọc "Genuine conversation" — xác định **ai đang giữ lượt** (Page hay
   Customer nói câu cuối cùng) và tuổi thực của tin nhắn khách gần nhất.
   Đây là nguồn sự thật duy nhất; không suy luận thời gian từ văn bản do LLM
   sinh ra.
4. Đọc timeline: agent nào chạy, bao nhiêu vòng, token in tăng bao nhiêu mỗi
   vòng. Token in tăng đều mỗi bước là dấu hiệu ADK đang gửi lại toàn bộ
   lịch sử tool-call/tool-response thay vì chỉ dùng state đã chốt.
5. Nếu nghi accuracy của một bước cụ thể (ví dụ QA escalate dù dữ liệu đủ),
   chạy `call <call_id> --full` cho đúng bước đó và bước ngay trước nó
   (input mà nó thực sự nhận được), không đoán từ response.
6. Đối chiếu `action_queue`: draft có được enqueue không, trạng thái cuối là
   gì. `rejected` bởi người vận hành nghĩa là lỗi đã được người thật xác
  nhận, không chỉ là cảm nhận.
   Với proactive reminder, kiểm tra cả các action đã executed cho cùng
   Page/thread/class/session_date qua mọi command ID. Draft/approval không phải
   bằng chứng đã gửi; quote legacy không xác định sender cũng không phải bằng
   chứng độc lập. Reactive gate không thay thế kiểm tra tần suất proactive.
7. Viết kết luận theo khung ở Mục 5.

## 3. Bản đồ codebase MAS (đọc code, không đoán)

| File | Vai trò |
| --- | --- |
| `adk_agents/agent.py` | Định nghĩa `InboxOrchestrator` và `CareOrchestrator`. Cả hai dùng ConversationAnalyst, KnowledgeLibrarian và ReplyQAReviewer; CareOrchestrator gọi ClassReminderComposer/WarmUpComposer/EventAdvertiser theo purpose. Vòng lặp, loop guard 30 lần, cú pháp `[ESCALATE: reason] note`. |
| `adk_agents/tools/l5_orchestrator_tools.py` | Tool `propose_seeker_update`, `get_seeker_profile` mà orchestrator gọi giữa vòng lặp. |
| `adk_agents/tools/l5_seeker_tools.py` | `get_thread_messages`, `lookup_seeker` — nguồn dữ liệu message/seeker cho agent tools. |
| `fb_pipeline/contracts/l1_conversation_state.py` | Gate xác định trạng thái hội thoại thuần hàm: `compute_conversation_state`, hằng số `ACTION_*`/`STATE_*`, `format_now_context`, `format_conversation_lines`. Không DB, không LLM — sửa ở đây trước, viết test trước. |
| `tools/l5_inbox_mas_pipeline.py` | `run_adk_pipeline` / `run_adk_care_pipeline` — dựng session ADK cho reactive reply và proactive care. Cũng có `_sanitize_reply`, `_is_safe_final_reply`, `_is_spurious_knowledge_gap`. |
| `tools/l5_inbox_mas_runner.py` | Đường tự động (`run_inbox_mas_loop.sh mas`/`mas-classify`). Áp dụng gate `compute_conversation_state` **trước** khi tốn LLM call (dòng ~186), claim message theo `seq` để tránh xử lý trùng. |
| `tools/l5_mas_recommend.py` | Đường thủ công (`manual_recommendation`, nút dashboard). Cố ý bỏ qua gate. `_guard()` chỉ chặn khi đã có proposal `pending`/`executing` trong `action_queue` — **không** chặn khi lần chạy trước kết thúc bằng escalate (lỗ hổng đã biết, xem Mục 6). |
| `tools/l5_inbox_mas_context.py` | Truy xuất knowledge (lịch học, giá, Zoom...) cho `KnowledgeLibrarian`. |
| `fb_pipeline/persistence/l4_llm_trace.py` | Sinh `trace_id`, ghi mỗi LLM call vào bảng `llm_calls` (`span()`, `start_call`, `end_call`). Nơi cần sửa nếu `trace_id` bị trùng giữa hai lần chạy độc lập. |
| `fb_pipeline/persistence/l4_sqlite_store.py` | `get_db_connection`, `log_mas_decision` (bảng `mas_decisions`), schema các bảng liên quan. |
| `tools/l5_action_queue.py` | `enqueue_action`, `replace_action`, `active_proposal_status`, `has_active_proposal` — vòng đời `action_queue`. |
| `tools/l5_mas_trace_debug.py` | Công cụ điều tra ở Mục 1 (script này). |

## 4. Tài liệu cần đọc theo thứ tự

1. [mas-response-quality-runbook.md](mas-response-quality-runbook.md) — quy
   tắc nội dung/giọng văn đúng, bảng tra "vấn đề → file cần sửa".
2. [mas-care-execution-contract.md](mas-care-execution-contract.md) — contract
   đích cho ingestion/gate/queue/Telegram.
3. `docs/PRDs/mas-time-aware-care-plan.md` (`prd:mas-time-aware-001`) — kế
   hoạch gốc đã sinh ra `l1_conversation_state.py`; đọc để biết ý định thiết
   kế ban đầu của gate trước khi kết luận nó "sai".
4. `docs/report/mas-execution-audit-2026-09-17.md` — audit trước đó về mù
   thời gian/banner/NO_REPLY.
5. `docs/report/mas-strategy-memory-review-2026-09-17.md` và
   `memory/mas_strategy.md` §10.5–10.6 — taxonomy lý do escalate, quy tắc
   `propose_seeker_update`.
6. `docs/universal-id-registry.md` — tra Universal ID của bất kỳ tài liệu
   nào được nhắc ở trên.

## 5. Khung báo cáo nguyên nhân

Khi kết luận một case, viết đúng các mục sau (agent sau đọc lại phải hiểu
ngay, không cần chạy lại điều tra):

- **Trace/thread điều tra:** trace_id, thread_id, tên khách.
- **Trigger thật sự:** `scheduler` hay `manual_recommendation` — quan trọng
  vì hai đường có gate khác nhau.
- **Gate lẽ ra phải quyết định gì** (từ `compute_conversation_state` chạy
  lại) **vs. thực tế đã xảy ra gì**.
- **File/dòng code gây ra sai lệch** — trích dẫn cụ thể, không mô tả chung
  chung.
- **Bằng chứng** — id các `llm_calls` liên quan, nội dung `state_json` nếu
  cần chứng minh dữ liệu đã có sẵn nhưng agent bỏ qua.
- **Đề xuất sửa** — file cần đổi, có cần test mới không (theo yêu cầu ở
  `.agents/rules/devops-qa.md`).

## 6. Nhật ký kỹ thuật (technical notes đã biết)

Cập nhật mục này mỗi khi tìm ra nguyên nhân mới, để lần sau không điều tra
lại từ đầu.

### 2026-09-17/18 — case Bùi Thị Thúy (thread `..._37d6c3d9d89bd7d5`), trace `20`

- Trigger thật là `manual_recommendation`, **không phải**
  `run_inbox_mas_loop.sh mas-classify` như báo cáo ban đầu — đường tự động
  đã đúng khi gate của nó (`l5_inbox_mas_runner.py`) sẽ skip thread này vì
  state thật là `already_answered` (Page đã hỏi lại khách về Zoom sau lượt
  cuối của khách, khách chưa trả lời — lượt đang chờ là của khách).
- `compute_conversation_state()` tính đúng `already_answered`, nhưng kết quả
  này **không được forward vào prompt LLM** ở cả hai pipeline:
  `tools/l5_mas_recommend.py` (comment dòng ~114 nói "LLM vẫn nhận được time
  context" — sai với code thật) và `tools/l5_inbox_mas_runner.py` (dòng
  ~274, gọi `run_adk_pipeline` không truyền `conversation_state`).
  `tools/l5_inbox_mas_pipeline.py::_run_adk_pipeline` chỉ đưa
  `now_context`/`thread_messages`/`seeker_context` vào session state. →
  Nguyên nhân gốc của "sai nhận thức thời gian": model phải tự suy luận ai
  đang giữ lượt từ transcript thô và đoán sai, sinh câu xin lỗi "phản hồi
  muộn" dù thực ra Page đã trả lời và đang chờ khách.
  **Sửa:** truyền `conversation_state.to_dict()` vào session state ở cả
  hai nơi gọi, và bắt buộc ConversationAnalyst nêu rõ ai đang giữ lượt.
- `find --thread` cho thread này trả về **5 trace_id khác nhau** trong vòng
  chưa đầy 2 ngày cho cùng một thread không có tin nhắn mới — bao gồm 2
  proposal (`action_queue` #295, #296) đều bị người vận hành **rejected**.
  `tools/l5_mas_recommend.py::_guard()` chỉ chặn khi đã có proposal
  `pending`/`executing`; một lần chạy kết thúc bằng escalate hoặc bị reject
  không để lại gì để `_guard()` nhìn thấy, nên thread bị chạy lại nhiều lần
  từ đầu. **Sửa:** thêm cooldown dựa trên lần chạy escalate/reject gần nhất
  cho cùng thread, trừ khi `regenerate=True`.
- `ReplyQAReviewer` từng escalate `[ESCALATE: knowledge_gap] ... were not
  provided` (call id 897/898 trong trace 20) trong khi `state_json` của
  chính bước đó (đọc bằng `call 897 --full`) cho thấy
  `conversation_analysis`, `knowledge_context`, `draft_reply` đều đã có sẵn
  — escalate giả. `_is_spurious_knowledge_gap()` trong
  `tools/l5_inbox_mas_pipeline.py` được viết để bắt đúng lỗi này nhưng
  không chặn được lần này; **cần điều tra thêm** vì sao guard không kích
  hoạt (chưa xác định được nguyên nhân trong lượt điều tra này).
- Hai lần chạy quan sát trực tiếp (id 888–898 và 910–919) **chia sẻ cùng
  một `trace_id="20"`** dù cách nhau 20 phút và độc lập hoàn toàn —
  `seq_in_trace` reset về 1 ở đợt thứ hai. Đây là lỗi sinh `trace_id` ở
  `fb_pipeline/persistence/l4_llm_trace.py`, làm hỏng mọi truy vấn theo
  trace_id nếu không sắp theo `id` (đã vá tạm trong
  `l5_mas_trace_debug.py::get_trace_calls` bằng cách sort theo `id`, nhưng
  **gốc rễ chưa sửa**).
- Trong một lần chạy, `tokens_in` của `InboxOrchestrator` tăng đều mỗi vòng
  (1607→1973→2085→2512→2903→3209) — dấu hiệu ADK gửi lại toàn bộ lịch sử
  tool-call/tool-response mỗi turn thay vì chỉ dùng các state key đã chốt
  (`conversation_analysis`, `knowledge_context`, `draft_reply`). Chưa sửa.

### 2026-09-18 — review contract CareOrchestrator trên working tree

- **Trace/thread điều tra:** không có case production hay trace/thread được chỉ
  định. Review code và tái hiện bằng mock in-memory; không gọi LLM, không ghi
  queue/DB thật. Không suy luận tần suất xảy ra ngoài production từ mock.
- **Trigger thật sự:** modal seeker gửi `type=care`; Recommendations ở
  `/queues` còn gửi `warmup`/`event` trực tiếp
  (`web/src/components/action-queues.tsx:189,448`). Scheduler ngoài phạm vi sửa.
- **Gate/contract kỳ vọng vs thực tế:** outbound care phải có analysis →
  knowledge → đúng composer → QA PASS trước enqueue. Nhánh `recommend_care`
  đã gọi CareOrchestrator nhưng runtime chưa cưỡng chế contract:
  `tools/l5_inbox_mas_pipeline.py:189–208` đọc `qa_verdict` mà không kiểm tra;
  `adk_agents/agent.py:416–443` chỉ giới hạn số tool calls. Mock runner chỉ
  phát final text từ CareOrchestrator, không analysis/knowledge/draft; cả ba
  verdict rỗng, `REPAIR: incorrect date`, `ESCALATE: knowledge_gap: missing
  facts` đều trả `reply_text` bình thường. `recommend_care:396–403` cho phép
  enqueue kết quả đó. Cần kiểm tra workflow thực thi, đúng composer, QA PASS
  trên đúng phiên bản draft và final text trùng draft đã duyệt.
- **Bypass ở Recommendations:** `tools/l5_mas_recommend.py:230,279,478–482`
  vẫn gọi composer riêng cho `warmup`/`event`/`all`, không chạy precheck care
  hay reviewer. Web route còn fallback template cho các type này
  (`web/src/app/api/action-queue/recommendations/route.ts:456`). Nhận định
  "mọi draft do operator kích hoạt" chưa đúng ngoài riêng `type=care`.
- **Sai purpose do bộ lọc UI:** modal gửi `programCode` từ bộ lọc cho cả nút
  warm-up (`web/src/components/seekers-table.tsx:255–260,714`), trong khi
  `_care_route` (`tools/l5_mas_recommend.py:305–309`) ưu tiên mọi program_code
  thành class_reminder. Tái hiện: instruction warm-up + program_code →
  class_reminder; "Mời tham gia sự kiện Chủ nhật" cũng → class_reminder.
  Cần truyền purpose tường minh và giữ bộ lọc độc lập với mục đích.
- **Facts chưa được xác thực đầy đủ trước LLM:** `_pick_event`
  (`tools/l5_mas_recommend.py:249–258`) fallback lấy row id mới nhất, bỏ điều
  kiện ngày/city, rồi `recommend_care:385` đặt nó vào `verified_event`.
  Mock không có upcoming event Hà Nội nhưng có row năm 2020 ở city khác vẫn
  trả row đó. Gate đăng ký `:326` chỉ kiểm tra program_code khác rỗng, chưa
  chứng minh seeker chọn lớp; profile Intake chỉ có program_code vẫn qua.
  Opt-out `:319–321` chỉ quét 5 tin cuối và không phân biệt sender. Cần
  regression cho sự kiện cũ/sai city, registration evidence và opt-out cũ.
- **Handoff:** có now_context, transcript, profile, analysis/knowledge brief;
  nhưng `conversation_state` được tính tại `tools/l5_mas_recommend.py:117`
  chỉ lưu queue payload `:410`, chưa truyền vào care session state
  (`tools/l5_inbox_mas_pipeline.py:160–170`). Analyst vẫn phải suy luận lượt
  hội thoại từ transcript, tương tự hạng mục đã ghi trong case Bùi Thị Thúy.
- **Kiểm chứng:** `pytest tests/test_mas_recommend.py tests/test_adk_wiring.py
  tests/test_class_schedule_and_care_routes.py -q` → **46 passed**. Tests care
  recommendation mock cả pipeline; wiring test kiểm tra tools/prompt/output
  keys, chưa chứng minh runtime có đủ trace và QA bắt buộc.
- **Đề xuất sửa:** ưu tiên runtime fail-closed và regression các verdict/
  bước thiếu; thống nhất các entry point operator; explicit purpose; siết
  event/registration eligibility. Chưa sửa implementation trong lượt review.

### 2026-09-18 — mở rộng review vòng đời MAS và lập phương án tổng thể

- **Trace/thread:** review code, không case production; không gọi worker live.
- **Trigger:** inbox scheduler, proactive scheduler/session worker và HITL.
- **Kỳ vọng vs thực tế:** strategy quy định gửi DM tay, dry-run không ghi,
  retry không làm mất nhu cầu, attendance độc lập draft. Code còn nhánh
  `tools/l5_hitl_execution.py:81–108` commit DM qua CDP sau approval;
  `tools/l5_proactive_routes.py:173,193,280` vẫn ghi trong dry-run; session
  claim tại `:173` không compare-and-set. Đây là khả năng đường code,
  không chứng minh worker live đang bật hay đã xảy ra gửi/ghi ngoài ý muốn.
- **Bằng chứng bổ sung:** `fb_pipeline/contracts/l1_conversation_state.py:171`
  skip khi có Page message sau khách dù chưa biết phục vụ đủ; cuối hàm cho
  reply khi age_hours chưa rõ. `adk_agents/tools/l5_seeker_tools.py:160` claim
  seq trước LLM không tách lease/attempt/outcome; lỗi tạm thời không tự được
  scheduler thử lại cho cùng seq. `tools/l5_telegram_hitl.py:252` còn helper
  auto-approve khi thiếu message_id; đường gửi mới đã giữ local proposal,
  nên cần kiểm tra caller trước khi đánh giá reachability của helper cũ.
- **Đề xuất:** đã lập [phương án tổng thể](../report/mas-improvement-plan-2026-09-18.md)
  với runtime Python điều phối specialists, gate/context chung, evidence,
  work lease/retry, draft version/sent ledger, migration scheduler và bộ nghiệm
  thu. Không sửa logic, schema hay cấu hình vận hành trong lượt lập phương án.

### 2026-09-18 — đánh giá inference và chi phí từ trace hiện có

- **Trace/thread:** `22`, `23`, `85298cc5-ad30-48e1-a5a5-531ff8cfbc7f`;
  chạy `report 23` trước đọc sâu. DB đọc bằng SQLite mode=ro; không gọi model.
  Snapshot có 608 calls từ 03:20 đến 06:38 UTC ngày 18/09, chỉ ba lượt inbox
  orchestrated đầy đủ, chưa có CareOrchestrator. Không dùng làm baseline p95
  production hoặc benchmark của code Care mới.
- **Trigger:** `23` manual recommendation, UUID trên là scheduler theo decision
  report. Gate tính lại cho thread `..._9ecd28b577001e02` là reply_late;
  câu hỏi còn chờ phù hợp để xem pipeline, không kết luận nội dung cuối đạt.
- **Bằng chứng latency/token:** mỗi trace 11 calls: orchestrator 6, analyst 1,
  librarian 2, composer 1, QA 1. Tổng duration tương ứng 194.832s / 116.783s /
  205.669s; input 21,696 / 23,836 / 23,872; output 1,971 / 2,285 / 2,423.
  Orchestrator riêng chiếm 76.312s / 66.862s / 106.202s (39.2–57.3% thời gian)
  và 57.9–60.4% input tokens. Trace 23 calls #2284–2294: lần cuối chỉ trả lại
  draft mất 36.242s; input orchestrator tăng 1,607 → 2,957 qua các bước.
- **Nguyên nhân kiến trúc:** `adk_agents/agent.py:447` LLM điều phối các bước
  tuần tự; Librarian `:313` phải gọi retrieval rồi quay lại model. Các adapter
  operator và `l5_inbox_mas_runner.py` xử lý seekers tuần tự. Không có bằng
  chứng ở đây phân tách được gateway wait, prefill, reasoning và decode.
- **Chi phí toàn hệ thống:** city_llm có 553 calls ok, 1,056,146 input tokens
  trong tổng 1,136,169 input tokens ok (~93%). Phải audit classify incremental/
  cache và mức cạnh tranh endpoint; chưa chứng minh calls này là dư thừa.
  Tên model trace là gpt-5.6-luna qua endpoint compatible; chưa có biểu giá
  gateway/cache billing nên không quy token thành USD.
- **Đề xuất ưu tiên:** Python điều phối giữ đủ bốn specialist (11 → 5 calls),
  prefetch retrieval từ scope Analyst rồi một lượt Librarian (→4 calls),
  typed handoff/context đủ bằng chứng; QA đúng version và runtime PASS bắt buộc.
  Giới hạn hai vòng repair, cache theo source/version, concurrency nhỏ giữa
  seekers và priority riêng inbox/classification. Không parallel các bước phụ
  thuộc trong cùng thread; không bỏ QA để giảm latency. Thử model/effort riêng
  từng role sau khi sửa context, đánh giá blinded trên cùng fixture.
- **Giới hạn ước lượng:** trừ thời gian orchestrator cho phần specialist còn
  49.921–118.520s, chỉ là phép tính phản thực từ trace, chưa phải benchmark
  runtime mới. Không cộng tỷ lệ tiết kiệm calls/token thành tỷ lệ giảm tiền.
  QA #2293 trả PASS nhưng usage có thoughts_token_count=317; reply ngắn không
  đồng nghĩa compute thấp. Không cộng thoughts lần nữa vào output khi chưa
  xác minh mapping usage của gateway.

### 2026-09-19 — triển khai đợt 0–1 cho Care/Recommendations

- **Phạm vi đã migrate:** `tools/l5_inbox_mas_pipeline.py` chạy Care theo
  Python coordinator: `ConversationAnalyst → KnowledgeLibrarian → composer
  đúng purpose → ReplyQAReviewer`; tối đa hai lượt REPAIR, không còn dùng
  final turn của `CareOrchestrator` làm authority để enqueue. Runtime chỉ lấy
  `draft_reply` đã PASS, cùng phiên; QA rỗng/REPAIR/ESCALATE hoặc text cuối
  khác draft đều fail-closed. Đây loại các lượt LLM điều phối Care trung gian;
  cần đo lại trace trước khi công bố số latency/chi phí mới.
- **Entry point operator:** `tools/l5_mas_recommend.py` chuyển `warmup`,
  `event`, `all` và `care` về cùng Care workflow. `care` buộc
  `care_purpose` tường minh (`class_reminder`, `warmup`, `event`); program
  filter không còn quyết định purpose. API/UI truyền `carePurpose`; event chỉ
  dùng record upcoming, đúng city/scope, không fallback event cũ bất kỳ.
  Global knowledge corpus không còn được preload vào mọi role; Librarian
  retrieval là nguồn handoff.
- **Ranh giới vận hành đã đóng:** `l5_hitl_execution.py` không claim/gửi DM
  qua CDP sau approval; `_execute_approved_action` từ chối direct call.
  `check_hitl_status("")` trả pending thay vì auto-approve. Dry-run của
  session open/attendance/SLA/reminder route không claim, enqueue, ghi
  attendance/reminder/decision hoặc gửi thông báo.
- **Ngoài phạm vi còn lại:** scheduler class-reminder vẫn dùng wrapper composer
  cũ khi chạy live; chưa được chuyển sang coordinator trong lượt này. Chưa có
  lease/retry/schema version/contact ledger của đợt 2–4. Không xác nhận worker
  live trước/sau có đang bật; thay đổi chặn khả năng code, không suy diễn lịch
  sử gửi tin.
- **Regression/QA:** các ca QA thiếu/REPAIR/ESCALATE, PASS draft cũ, purpose
  + filter, adapter warmup/event, dry-run read-only, Telegram thiếu ID và
  approval không auto-send có tests. Chạy focused MAS/HITL/scheduler: 110
  tests pass; `cd web && npm run build` pass. `pytest-cov` chưa được cài nên
  không có báo cáo coverage; `git diff --check` toàn worktree còn whitespace
  có sẵn tại `fb_pipeline/contracts/l1_class_schedule.py:119`.

### 2026-09-19 — kiểm chứng độc lập sau implementation đợt 0–1

- **Trace/thread:** kiểm chứng working tree bằng tests và ADK thật/model giả;
  không case production, không gọi model live hay worker.
- **Trigger:** Care/Recommendations, regenerate queue, scheduler/HITL.
- **Kỳ vọng vs thực tế:** Python Care workflow và PASS guard đã có; chưa đủ
  nghiệm thu. Web fallback vẫn vượt QA khi MAS lỗi (`recommendations/route.ts:457`),
  event fallback còn row cũ/default (`:325`). QA prompt `adk_agents/agent.py:376`
  không inject state gốc. Capture request ADK thấy QA nhận summaries/draft qua
  lịch sử nhưng thiếu marker hội thoại gốc/operator instruction.
- **Bằng chứng mới:** session service thật trả deepcopy; mutation qa_feedback
  tại `l5_inbox_mas_pipeline.py:236` không persist, dù composer còn thấy repair
  qua history. Adapter `l5_mas_recommend.py:319` thiếu conversation_state trong
  brief; mock PASS + OUT_OF_SCOPE đi đến enqueue tại `:337–340`. Queue regenerate
  `action-queues.tsx:254` biến mọi proactive thành warmup. Scheduler legacy
  vẫn có writes/Telegram dù dry_run tại `l5_scheduler_routes.py:303,438`.
- **Kiểm chứng:** 147 focused tests pass; web build pass; ADK fake-model repair
  chạy đủ 6 bước. Tests terminal-state hiện tại chưa cover các handoff này.
- **Đề xuất:** sửa fallback, inject QA context, persist feedback, preserve
  regenerate purpose, centralize sentinel outcomes; nghiệm thu scheduler riêng.
  Chi tiết tại [báo cáo kiểm chứng](../report/mas-implementation-verification-2026-09-19.md).

### 2026-09-19 — audit notation history, quote, reaction và sender

- **Trace/thread:** không chọn case production; audit toàn đường ingestion/MAS,
  DOM tổng hợp chạy parser JS thật trong Chrome headless và DB in-memory.
- **Trigger:** fetch history → persistence → MAS/UI. Lỗi xảy ra trước LLM.
- **Kỳ vọng vs thực tế:** phải bảo toàn sender, ngày, body/quote, reaction
  actor/target/scope. Parser `thread_detail_parser.py:423` gộp text bằng quote
  label; `l1_message_kind.py:56` xóa nhãn đó trong prompt, giữ nội dung quote
  dưới sender hiện tại. Reaction `:403–417` chỉ còn emoji, không actor/target.
- **Bằng chứng:** HTML ngày 10/09 + giờ 9AM bị trả chỉ 9AM và resolve thành
  ngày anchor 19/09; hai text container thành một quoted row; aria-label chỉ
  rõ reaction actor bị bỏ. `l3_pipeline.py:225–280` dedupe set sender+text làm
  mất Customer 'Dạ' thứ hai dù khác ngày sau Page reply. UI `queries.ts:70`
  sửa sender theo keyword khác DB. 100 tests hiện có vẫn pass.
- **Đề xuất:** structured event contract, identity/time confidence, tách quote/
  reaction và formatter dùng chung; DOM golden tests + đối chiếu live có nguồn
  trước sửa dữ liệu legacy. Không gọi model, không ghi DB live, không sửa runtime.
  [Báo cáo đầy đủ](../report/message-history-notation-audit-2026-09-19.md).

### 2026-09-19 — đóng bypass Care và siết dry-run scheduler

- **Trace/thread:** implementation và regression trên DB/model giả; không gọi
  model live, không chạy worker production, không gửi Facebook/Telegram.
- **Care contract:** route Recommendations không còn fallback template khi
  `all`/`warmup`/`event`/`care` lỗi hoặc không có target được chọn. Các route
  này báo lỗi workflow thay vì tạo outbound draft không QA. `recommend_care`
  chặn rỗng, `[OUT_OF_SCOPE]`, `[NO_REPLY...]` và `[NO_SEND...]` trước enqueue.
- **QA/handoff:** QA nhận snapshot explicit trong user message gồm transcript
  gốc, `now_context`, conversation state, profile, purpose, operator brief,
  facts session/event, analyst/librarian output và chính draft. REPAIR được
  đưa trực tiếp vào prompt composer của vòng tiếp theo; không còn mutation vào
  bản sao `get_session()` giả định là persist. Care brief có
  `conversation_state` deterministic.
- **Regenerate:** queue giữ `carePurpose`, program/session scope, event ID và
  instruction cũ; chặn batch trộn purpose/scope/instruction. API chỉ cho care
  không instruction khi đó là regenerate, còn lệnh Care mới vẫn bắt buộc có
  purpose và chỉ dẫn.
- **Dry-run scheduler:** react không enqueue; fetch dry-run không mở CDP;
  warmup/event dry-run không JIT fetch, không queue/Telegram; nhánh cool-step
  exhausted không cập nhật user state khi dry-run. Quyết định dry-run hiện vẫn
  được ghi dưới cờ `dry_run` cho audit, nên chưa gọi đây là no-write tuyệt đối.
- **Giới hạn còn lại:** live warmup/event scheduler vẫn dùng composer legacy,
  chưa có Care session, QA PASS/recheck/lease chung. Đây là đợt scheduler hội
  tụ riêng, không được ngụy trang là đường Care đã nghiệm thu.
- **Regression:** 168 tests focused MAS/HITL/scheduler/trace/conversation pass
  sau Care fixes; bổ sung dry-run scheduler đưa nhóm scheduler/cool sequence
  lên 26 pass. `cd web && npm run build` pass. `pytest-cov` không cài; toàn
  worktree còn một trailing whitespace có sẵn ngoài phạm vi tại
  `fb_pipeline/contracts/l1_class_schedule.py:119`.

### 2026-09-19 — Fetch-QA top-10 báo unmatched liên tục

- **Case điều tra:** page `1548373332058326`, fetch log #140–147 (lần mới
  nhất `2026-09-19T00:44:23`), report
  `logs/fetch-qa/1548373332058326-20260919-004423.json`. Đây là lỗi
  ingestion/Fetch-QA, không phải kết luận rằng mười khách đều `city=Unknown`
  hay lỗi reply của MAS.
- **Bằng chứng DOM thật:** đọc-only qua CDP tại thời điểm điều tra: sidebar chỉ
  render 8 card (`Trader Nguyễn`, `Nguyễn Ngọc Giàu`, `Thuý Bùi Thị`, `Hoang
  An Minh`, `Xuan Dinh Vu`, `Khanh Van Quach`, `Mai Hoa`, `Lê Ngọc Thông`). Cả
  8 đều có `href="#"`, `facebookUid=""`, `selectedItemId=""`, không có
  hovercard ID. QA #147 vì vậy tạo 1 soft + 7 hard với `dom_id=""`; DB chỉ
  còn 5 `inbox_sort_index` không NULL, nên không hề có một snapshot top-10
  hợp lệ để so sánh.
- **Nguyên nhân gốc 1 — contract identity mâu thuẫn (trước follow-up sửa
  matcher):** ingest cho phép lấy cached PSID bằng display name
  (`l4_sqlite_store.py:808–831`; gọi ở `l3_inbox.py:209–247`) nhưng Fetch-QA
  từng chỉ chấp nhận UID DOM. Khi Meta không expose UID ở card chưa mở, QA
  chắc chắn fail. Fallback ingest cũng không bền: card hiện là `Khanh Van
  Quach`, DB là `Quách Khánh Vân`, nên không resolve được PSID.
- **Nguyên nhân gốc 2 — snapshot bị cắt sau 2 card:** Stage 1 dừng ngay khi
  gặp hai preview khớp (`l3_inbox.py:300–315`). Các log #140–147 đều ghi
  `threads_seen=8`, `skipped_threads=2`, `tasks_dispatched=0`, `Stage 1
  Complete. Listed 2 threads`; nó chỉ cập nhật rank của hai card đầu và vẫn
  chạy QA trên sidebar đầy đủ. Điều này tự tạo DB top-N partial/stale.
- **Nguyên nhân gốc 3 — failure không được nâng thành failure vận hành:**
  `fetch_messages` trả `success=True` sau khi gắn `stats["qa"]` dù
  `qa_status=failed` (`tools/l5_fetch_fb_messages.py:411–447`), nên vòng shell
  15 phút tiếp tục chạy (`run_inbox_mas_loop.sh:98–153`). Đường alert còn gọi
  biến không tồn tại `db` thay vì connection (`l3_fetch_qa.py:298–305`), nên
  log đã xác nhận `Failed to send Telegram alert: name 'db' is not defined`.
- **Vì sao test xanh nhưng lỗi cơ bản lặp lại:** fixture QA chỉ mô phỏng card
  có `selectedItemId` (ví dụ `tests/test_decoupled_fetch_qa.py:104–106`),
  không có DOM hiện tại `href="#"`/UID rỗng, name drift, snapshot thiếu 10,
  hay QA hard-fail phải làm command thất bại. Các Q04–Q13 còn là `pass` dummy
  (`:199–221`). Focused suite vẫn 50 pass nên không phải bằng chứng smoke
  test Facebook thật hoạt động.
- **Sửa tối thiểu đề xuất:** (1) coi `QA failed` là fetch failure và sửa
  enqueue alert dùng `conn`; dừng/backoff loop thay vì lặp im lặng; (2) không
  cho early-exit 2 preview khi Fetch-QA cần top-10, hoặc đánh dấu snapshot
  partial và skip QA; (3) lấy/stamp PSID ổn định trong Stage 1 (không dùng
  name), rồi cùng source identity đó cho QA; (4) thay dummy tests bằng fixture
  DOM thật và test command exit/alert/snapshot partial.
- **Follow-up theo yêu cầu operator:** Fetch-QA đã đổi sang match tên Facebook
  render ở sidebar với `threads.thread_name` (Unicode/khoảng trắng chuẩn hoá),
  không còn phụ thuộc UID. Tên trùng vẫn fail-safe; kết quả cũng phân biệt
  thread có trong DB nhưng vắng khỏi snapshot top-N. Regression có card
  `href="#"`/không UID và ca trùng tên.

### 2026-09-19 — City/Program/Name/Phone dùng sai provider khi `.env` có hai loại key

- **Case điều tra:** route `[CLASSIFY]` cho City/Program và contact extraction;
  không chạy transcript khách hay worker mới trong lượt audit này. Log thật
  `logs/fetch_fb_messages.log:142494–142577` cho thread `Trader Nguyễn` cho
  thấy 10 lần gọi `https://apikey.click/v1/chat/completions` đều trả 401.
- **Nguyên nhân gốc:** `.env` có cả `GOOGLE_API_KEY`/`GEMINI_MODEL` và legacy
  `OPENAI_COMPATIBLE_*`. Loader trước đó có thể chọn Google, nhưng
  `l1_city_llm.py` chỉ thực hiện HTTP `/chat/completions`; contract giữa
  provider được chọn và transport không thống nhất. Vì vậy City/Program/Name/
  Phone không có đường Gemini native, còn process cũ dùng endpoint legacy.
- **Sửa:** `tools/l5_llm_provider.py` là nguồn cấu hình/transport duy nhất,
  bắt buộc `provider=google`, nạp key từ `.env`, xóa `OPENAI_API_*` kế thừa
  trong process ADK và gọi native Gemini `generateContent` với
  `x-goog-api-key`. City classifier, fallback contract và MAS/Recommendations
  đều dùng lib này; không còn runtime OpenAI-compatible fallback.
- **Kiểm chứng:** probe read-only Google Models API trả HTTP 200 cho
  `models/gemini-3.8-flash` và xác nhận `generateContent`; 85 regression tests
  liên quan config/city/MAS/ADK pass. Chưa gửi message khách và không chạy
  inference production chỉ để kiểm tra key.

### 2026-09-19 — API failure bị đóng dấu là City/Program đã xác minh nên không retry

- **Case điều tra:** `FUNNEL_MAS_CITY="Hà Nội" ./tools/run_inbox_mas_loop.sh
  mas-classify`; thread `1548373332058326_9401961e0ea95278` (`Trader Nguyễn`).
  DB read-only cho thấy `city=Unknown`, proof `API error: 401 ...` và
  `classification_verified_at=2026-09-18 23:44:38`.
- **Nguyên nhân gốc:** `_post_scrape_llm_city_classify`
  (`tools/l5_fetch_fb_city_classify.py`) ghi mọi response, kể cả
  `detect_city_llm()` trả `API error`, thành `city=Unknown` và stamp cả
  `classification_verified_at` lẫn `contact_extracted_at`. Predicate stale chỉ
  xét timestamp/message, nên 401 cũ được coi là classification hợp lệ và không
  vào epoch `[CLASSIFY]` sau. `_call_llm_with_retry` còn thử 401 đến 10 lần với
  exponential backoff, có thể kéo dài hơn một epoch.
- **Sửa:** HTTP 400/401/403/404/422 fail-fast để outer `mas-classify` loop là
  retry boundary. Failure API/timeout/parse không ghi đè City/Program/Name/
  Phone, chỉ ghi proof `RETRYABLE_LLM_FAILURE`; stale predicate cũng nhận diện
  proof lỗi lịch sử (`API error`, timeout, parse) để recover dữ liệu đã bị
  đóng stamp. `Unknown` có reasoning bình thường vẫn là kết quả hợp lệ và chờ
  tin nhắn khách mới.
- **Kiểm chứng:** DB hiện có một historical retryable record và predicate mới
  trả `stale_after_retry_predicate=1`; test regression giữ nguyên profile khi
  401 và buộc record remain stale. Focused City tests pass. Không tự gọi model
  live; vòng shell đang chạy sẽ retry ở epoch kế tiếp.

### 2026-09-19 — Gemini trả JSON bị cắt, UI chỉ hiện City mà mất Program/Name/Phone

- **Case điều tra:** `/seekers`, thread `Trader Nguyễn`
  (`1548373332058326_9401961e0ea95278`). LLM trace #2551 gọi native Gemini
  thành công nhưng response dài 150 ký tự, bị dừng tại `"proof`; phần đầu đã
  chứa `program_code=14h30-CN-Vương Thừa Vũ-HN`, tên và phone. DB sau đó lại
  có `city=Hà Nội`, `program_code=NULL`, proof `Extracted from free-text
  response`, nên table chỉ render badge City.
- **Nguyên nhân gốc:** `_parse_llm_response` dùng `json.loads` all-or-nothing;
  khi JSON không đóng, fallback chỉ quét city trong free text và bỏ toàn bộ
  structured fields đã hoàn chỉnh. UI `seekers-table.tsx` thực tế đã render
  `seeker.programCode` dưới City, nên không phải lỗi CSS/render.
- **Sửa:** prompt Gemini chỉ yêu cầu 5 scalar fields, không yêu cầu proof/
  reasoning; parser recover một JSON prefix chỉ khi Program/Name/Phone là
  scalar đã đóng và qua validators catalogue/phone/name. Các record fallback
  lịch sử được xem stale để epoch sau chạy lại và persist đủ field.
- **Kiểm chứng:** test fixture reproduction từ trace #2551 recover chính xác
  City/Program/Name/Phone; 40 focused parser/classification/UI-contract tests
  pass. Queue retry hiện có 1 record; không tự chạy inference live trong lượt
  điều tra.

### 2026-09-19 — triển khai source-aware message history notation

- **Trace/thread:** không có thread production được chọn; dùng fixture DOM
  Chrome headless, SQLite tạm và model không gọi live. Đây khắc phục dữ liệu
  đầu vào cho MAS, không suy ra chất lượng hay tỷ lệ lỗi history production.
- **Evidence contract:** `InboxMessage`, `messages` và
  `crawled_message_reactions` nay giữ nullable `source_id`, sender confidence,
  raw/day/time evidence, reply target, quote sender/text và reaction actor /
  target / scope. Legacy data không được backfill bằng nội dung, màu hay LLM.
  `crawled_message_reactions` tách khỏi bảng action reaction của agent.
- **Parser/persistence:** `thread_detail_parser.py` tách body khỏi quote,
  không nối `[Quoted Reply/Link]`; date separator có năm + clock trở thành
  một timestamp có day context, còn clock-only không bị anchor vào ngày crawl.
  Sender không có evidence DOM là `Unknown` (candidate heuristic không thành
  actor). `l3_pipeline.py` upsert theo source ID và ordered overlap; không còn
  dedupe global theo sender/body, do đó giữ được "Dạ" lặp lại ở ngày khác.
- **MAS/UI handoff:** `l1_message_kind.py`/`format_conversation_lines()` render
  quote/reaction thành metadata tách biệt và gate chỉ xét body sender cho
  phone/closer/intent. `l5_seeker_tools.get_thread_messages()` forward evidence
  và chỉ gắn reaction khi target source ID thật sự khớp. UI không còn sửa sender
  bằng keyword, ưu tiên `message_at` cho grouping, và render quote/reaction
  evidence riêng; graph endpoint cũng không suy Page từ page ID.
- **Regression:** DOM golden gồm day+clock, quote/reply, reaction aria-label;
  SQLite gồm repeated body, source-ID update, reaction target/reaction-only và
  clock-only timestamp. Focused cross-layer suite: 181 pass; `cd web && npm run build`
  pass. `pytest-cov` chưa được cài. Full pytest đã tiến tới 11% rồi không dùng
  CPU/không sinh output hơn hai phút tại một nhóm integration ngoài phạm vi và
  được dừng; không ghi nhận nó là pass. Focused cross-layer suite là evidence
  test hoàn tất của lượt này.

### 2026-09-19 — formatter MAS không được gán quote/reaction legacy cho sender

- **Phạm vi:** remediation ở formatter/gate thuần hàm; không crawl Facebook,
  không đọc/ghi DB, không gọi LLM hay thay parser/persistence/UI trong phần
  sửa này.
- **Nguyên nhân:** `strip_reaction_markers()` từng xoá prefix
  `[Quoted Reply/Link]` cùng với marker reaction. `format_conversation_lines()`
  sau đó ghép phần quote còn lại vào dòng của sender; quote có thể thành bằng
  chứng câu hỏi/địa điểm/đăng ký của người gửi hiện tại. Cùng lúc reaction
  legacy chỉ còn emoji nên actor/target/scope không thể suy từ bubble.
- **Sửa:** `l1_message_kind.py::parse_legacy_message_annotations` tách body,
  quote legacy và emoji reaction. `strip_non_sender_annotations` chỉ trả body
  cho `compute_conversation_state`, nên quote/reaction không tạo closer,
  question hay phone signal. `format_conversation_lines` biểu diễn quote và
  reaction thành metadata riêng, giữ actor/target/time khi contract có trường
  cấu trúc và ghi `Unknown` khi thiếu; không bao giờ nối chúng vào body sender.
- **Kiểm chứng:** quote legacy, quote có sender/target, reaction có cấu trúc,
  reaction legacy và sender/time confidence được cover bởi
  `tests/test_conversation_state.py`; focused regression formatter,
  persistence và MAS pass **138 tests**. Chưa suy luận rằng dữ liệu legacy đã
  có thể phục hồi actor/target; cần parser/persistence mới hoặc recrawl để có
  bằng chứng đó.

### 2026-09-19 — persistence giữ evidence message và không dedupe toàn cục theo body

- **Phạm vi:** remediation tại persistence, dùng DB test in-memory/temporary;
  không crawl Facebook, không gọi LLM và không ghi snapshot production. Đây là
  phần theo sau audit notation history, không suy luận hay backfill actor/ID
  cho row legacy.
- **Nguyên nhân:** `persist_thread_record()` trước đó dùng một global set
  `(normalized sender, normalized content)`. Vì thế một "Dạ" mới sau lượt
  Page có thể bị bỏ chỉ vì trùng chữ với "Dạ" cũ. Cùng hàm còn gán
  `Auto_Page` từ canned wording, khiến persistence tự sửa attribution mà
  không có source evidence.
- **Sửa:** `fb_pipeline/inbox/l3_pipeline.py:138–155` chuyển toàn bộ metadata
  contract (`source_id`, confidence sender, raw/day/time, reply/quote,
  reactions) vào `InboxMessage`; `:201–263` ghi reaction crawl riêng trong
  `crawled_message_reactions`, tách khỏi action-log `reactions`. `:310–408`
  dedupe source-id trước, fallback chỉ dùng ordered overlap (và ngày ISO do
  parser cấp) thay cho global body set; source id trùng update evidence,
  sender `Unknown` giữ nguyên. Không còn tạo `Auto_Page` mới; chỉ map giá trị
  legacy khi so khớp idempotent.
- **Schema/migration:** `l4_sqlite_store.py:209–320` thêm các cột nullable
  evidence, partial unique `(thread_id, source_id)` và table reaction crawl.
  `setup_database()` giữ row/table legacy, không backfill source id, target
  reply hay actor từ text. Các row legacy vẫn thiếu evidence cần recrawl hoặc
  người xác minh, không thể phục hồi chắc bằng prompt.
- **Kiểm chứng:** regression giữ hai "Dạ" ở ngày 10/09 và 19/09, source-id
  upsert, `Unknown` sender, reply/quote metadata, reaction actor/target và
  resync reaction idempotent; migration từ bảng messages cũ giữ data. Chạy
  `pytest tests/test_l3_inbox_pipeline.py tests/test_l4_inbox_persistence.py
  tests/test_l3_inbox_worker.py tests/test_l1_inbox_contracts.py
  tests/test_conversation_state.py tests/test_l5_inbox_mas_runner.py -q` →
  **116 passed**. Cần reader/UI/MAS query dùng các cột và table mới trước khi
  coi end-to-end notation đã hoàn tất.

### 2026-09-19 — Gemini 3 thinking có thể cắt JSON structured dù model trả HTTP 200

- **Case điều tra:** retry City/Program/Name/Phone cho các seeker thiếu
  `real_name`. Trace cho thấy `finishReason=MAX_TOKENS`: 400
  `maxOutputTokens` bị tiêu thụ phần lớn bởi thinking tokens của Gemini 3,
  sau đó output bị dừng giữa object JSON. Đây không phải kết quả City
  confidence thấp và không được phép city-only fallback.
- **Sửa:** common transport `tools/l5_llm_provider.py` gửi
  `thinkingConfig.thinkingLevel=LOW`, tăng cap single-seeker có cấu hình được
  (`CITY_LLM_SINGLE_OUTPUT_TOKENS`, mặc định 1024), trace thêm
  `thoughts_tokens`/`finish_reason`, và raise `IncompleteGeminiResponse` khi
  `MAX_TOKENS`. City detector đánh dấu response đó là parse failure retryable
  ở epoch sau; persistence giữ nguyên field đã có.
- **Kiểm chứng live:** preflight cho `Trader Nguyễn` trả đủ Hà Nội,
  `14h30-CN-Vương Thừa Vũ-HN`, `Nguyễn Thị Hải Yến`, `0938241999`. Lệnh
  `--missing-real-name --workers 10` hoàn tất, bổ sung 139 tên thật, 149 số
  điện thoại và 87 programme; các seeker không có bằng chứng customer vẫn
  để trống tên thay vì suy đoán. Focused regression tests pass **37 tests**.

### 2026-09-19 — independent review: source-aware notation còn lỗi xuyên tầng

- **Phạm vi:** review implementation, không sửa runtime/production DB, không
  gọi LLM hay Facebook. Chạy JavaScript thật qua Playwright Chrome headless
  trên DOM tổng hợp và persist vào SQLite `:memory:`. Đây là counterexample
  cho contract, không phải đo tỷ lệ DOM tương ứng ngoài production.
- **P1 / incoming bị bỏ qua:** `thread_detail_parser.py:478` explicitSender
  chỉ có Page hoặc null, không có nhánh Customer. DOM incoming có
  `aria-label="Lan sent a message"` cho sender Unknown; qua persistence và
  `l1_conversation_state.py:125` trả `no_customer_message / skip`.
  Cần actor identity mapping dựa trên nguồn đã xác thực và gate review khi
  actor chưa rõ, không khôi phục đoán bằng màu/nội dung.
- **P1 / quote vẫn nhiễm body:** `thread_detail_parser.py:597` lấy innerText
  của mọi `.x1y1aw1k`, bao gồm ancestor chứa quote. Fixture outer container
  chứa quote `Không nhắn nữa` của Lan và body `Dạ`, wrapper Page, trả body
  `Không nhắn nữa\nDạ` của Page dù quoted_text cũng được tách. Cần loại quote
  subtree khỏi body extraction, xác định boundary từng message trước khi
  lấy text; formatter downstream không sửa được attribution đã sai.
- **P1 / mất body vì source collision:** `thread_detail_parser.py:665` emit
  hai text segments `Dòng một`, `Dòng hai` cùng ID wrapper `m3`;
  `l3_pipeline.py:415` giữ segment đầu, bỏ segment sau. Fixture DB chỉ còn
  `Dòng một`. Phải xác định source ID thuộc message hay cluster và assemble
  body theo đúng message boundary trước upsert, không đơn thuần bỏ dedupe.
- **P1 / canonical time stale:** UPDATE tại `l3_pipeline.py:390` không ghi
  message_at/message_at_approx. Recrawl source ID m3 từ Sep 10 09:00 sang
  Sep 11 10:00 năm 2026 cập nhật raw/day nhưng message_at vẫn
  `2026-09-10 09:00:00`. Cần cập nhật bộ evidence/time nhất quán trong cùng
  transaction, bao gồm trường hợp unresolved -> resolved.
- **P2 / reaction identity và observation time:** parser `:446` dùng cùng
  sourceId lookup cho reaction ID và target ID. Fixture bubble target-m1,
  img có data-message-id reaction-r1 trả target_message_id reaction-r1,
  confidence explicit thay vì target-m1. `:466` lấy observed_at từ timestamp
  message (Sep 10 09:00), không phải thời điểm quan sát. Cần phân biệt
  reaction source ID / target message ID / snapshot observation time.
- **P2 / evidence handoff chưa đầy đủ:** `enrich_thread_record` không giữ
  sender_evidence, quote_evidence, quoted_sender_confidence; reaction writer
  không giữ actor_role. Seeker tool không trả reaction raw_label và loại
  thread/unknown-target events khỏi payload thay vì trả danh sách độc lập.
  UI Messenger render structured reaction thành emoji, chưa hiện actor/scope.
- **Kiểm chứng độc lập:** 132 tests pass trong 3.21s với
  `tests/test_thread_detail_parser_notation.py`,
  `tests/test_message_history_evidence.py`, `tests/test_l3_inbox_pipeline.py`,
  `tests/test_l4_inbox_persistence.py`, `tests/test_l1_inbox_contracts.py`,
  `tests/test_conversation_state.py`, `tests/test_l5_inbox_mas_runner.py`,
  `tests/test_web_filters_static.py`; `cd web && npm run build` pass.
  Đây không phải rerun chính xác bộ 181 tests hay full pytest. Parser tests
  notation hiện mock page.evaluate trả rows chuẩn bị sẵn, nên không bắt
  các counterexample JavaScript thật ở trên. Cần automated DOM -> parser ->
  persistence -> getter -> gate tests và DOM snapshot đã ẩn thông tin riêng
  từ Inbox thật trước khi xác nhận rollout an toàn.

### 2026-09-19 — remediation notation history sau counterexample DOM thật

- **Phạm vi/an toàn:** sửa parser, contract, persistence, reader, gate và UI
  bằng fixture DOM tổng hợp + SQLite tạm. Không crawl Facebook, không gọi LLM,
  không chạy worker hoặc sửa dữ liệu lịch sử/production. Vì vậy đây là bằng
  chứng chống regression, không phải tần suất lỗi trên Inbox live.
- **Actor và quote:** `thread_detail_parser.py` chỉ xác nhận Customer từ nhãn
  nền tảng kiểu `Lan sent a message`; màu, căn lề và text không được dùng để
  đoán actor. Subtree quote được loại khỏi body trước khi Python nhận dữ liệu.
  `Unknown` có body nay cho `uncertain_sender / needs_review`; truy vấn inbox
  đưa nó tới deterministic gate để ghi quyết định, nhưng không claim hay gọi
  LLM/draft. Empty Unknown vẫn bị bỏ qua.
- **Boundary/identity/time:** parser không copy ID wrapper vào sibling body;
  persistence coi source ID collision là không đủ bằng chứng (lưu NULL) và
  dùng ordered overlap để giữ cả hai body. Source-ID re-crawl update nguyên tử
  raw/day/precision cùng `message_at`/`message_at_approx`, bao gồm chuyển giữa
  timestamp unresolved và resolved.
- **Evidence đầy đủ:** `InboxMessage`, migration và upsert nay giữ
  sender/quote evidence + quoted-sender confidence. Reactions giữ actor role,
  scope, evidence, target message tách reaction-control ID và `observed_at`
  do Python truyền lúc crawl. `get_thread_messages()` trả toàn bộ reactions ở
  `reaction_events`, kể cả thread/unknown target, thay vì gắn đoán vào bubble.
  Formatter MAS đưa chúng thành metadata riêng, không phải lời người gửi; UI
  hiển thị actor/scope trên bubble có target và bảng evidence cho event rời.
- **Regression:** Chrome headless thực thi đúng JavaScript parser, rồi test
  DOM -> parser -> `enrich_thread_record` -> SQLite -> reader -> MAS gate;
  có case Customer explicit, quote lồng, sibling body, reaction control target,
  recrawl timestamp và Unknown/needs_review. Chạy focused suite gồm parser,
  persistence, evidence, state/query, MAS/Care wiring, E2E fixture và web static checks:
  **213 passed, 3 skipped**. `cd web && npm run build` pass. `pytest-cov` không cài; full
  pytest không chạy lại vì lần trước treo ngoài phạm vi, nên không ghi nhận là
  pass. Cần DOM snapshot Facebook đã ẩn dữ liệu riêng trước pilot rollout.

### 2026-09-19 — care reminder tự thêm lời dặn không có căn cứ và closing gây áp lực

- **Trace/thread điều tra:** trace `30`, thread
  `1548373332058326_a1b27ae3f95bf3ed`, Quách Khánh Vân; action queue #301
  đang `pending`. Không gửi Facebook trong lượt điều tra.
- **Trigger thật sự:** `operator_care_command`, route `care`, purpose
  `class_reminder` (không phải phản hồi một tin nhắn mới). Gate tính lại là
  `closed_by_human/react` vì khách đã kết thúc bằng “Thanks bạn” ngày
  15/09; care T-1 vẫn hợp lệ vì operator đã chọn seeker và có
  `verified_session` ngày 20/09 14:30 tại Vương Thừa Vũ.
- **Sai lệch:** call #3412 (`ClassReminderComposer`) sinh “Lớp học hoàn toàn
  miễn phí nên bạn chỉ cần mặc trang phục thoải mái là được ạ. Rất mong và
  hẹn gặp bạn chiều mai nhé!”. “Trang phục thoải mái” không có trong
  `verified_session`, operator instruction, transcript, hay factual brief;
  đây là suy diễn của model, không phải fact được phép thêm. “Rất mong” tạo
  kỳ vọng/áp lực trái `memory/SOUL.md:35-37` và
  `memory/mas_strategy.md:9-11`; lời nhắc chỉ cần closing tự nguyện, ví dụ
  “Hẹn gặp lại bạn chiều mai nhé.”
- **Nguyên nhân gốc, có bằng chứng:** ConversationAnalyst #3409 đã đưa
  “Completely free of charge” vào `Required Facts` dù mục đích reminder chỉ
  cần lịch/địa điểm; KnowledgeLibrarian #3411 lại nhắc fee. Prompt composer
  `adk_agents/agent.py:243-255` chỉ nói “Mention that it is free only if
  natural” và bắt buộc “End with a gentle hẹn gặp line”; cả hai là ràng buộc
  mơ hồ, không cấm suy diễn hướng dẫn tham dự hoặc lời mong đợi. Prompt QA
  `adk_agents/agent.py:401-409` chỉ kiểm tra fact, safety, concise và form
  xưng hô, không có check “chỉ facts cần cho purpose”/“không thúc giục”, nên
  call #3413 trả `PASS`. `_is_safe_final_reply()` tại
  `tools/l5_inbox_mas_pipeline.py:360-379` chỉ chặn reasoning và viết tắt,
  do đó không phải một backstop cho lỗi tone này.
- **Đề xuất sửa nhỏ, theo thứ tự:** (1) siết handoff/composer: class reminder
  chỉ gồm thời gian, địa điểm và một thông tin logistics *chỉ khi* có trong
  verified brief và cần cho chuyến đi; không nhắc miễn phí trừ khi seeker vừa
  hỏi hoặc operator yêu cầu; cấm thêm trang phục/chuẩn bị/lợi ích không có
  nguồn. Closing là tùy chọn, ngắn và không chứa “rất mong”, “mong được đón”,
  “hy vọng bạn sắp xếp”, “đừng quên”, hay từ ngữ tạo nghĩa vụ. (2) siết QA
  thành `REPAIR` khi có fact/khuyến nghị không phục vụ mục đích đã chọn hoặc
  ngôn ngữ thúc giục; QA phải nêu replacement cụ thể. (3) thêm backstop
  deterministic trước enqueue cho các phrase đã biết để một QA PASS bất ngờ
  fail-closed thay vì tạo draft. (4) regression: fixture care với đúng facts
  của trace này phải chấp nhận draft lịch + “Hẹn gặp lại bạn chiều mai nhé”,
  nhưng REPAIR/chặn draft chứa “trang phục thoải mái” và “Rất mong ... hẹn
  gặp”; kiểm tra prompt QA/composer chứa policy mới và test runtime không
  enqueue draft bị backstop từ chối.

### 2026-09-19 — triển khai voice policy và repair cho reminder trace 30

- **Trace/trigger:** follow-up trace `30`, thread
  `1548373332058326_a1b27ae3f95bf3ed`, `operator_care_command`. Giữ nguyên
  gate; sửa chất lượng draft sau khi care đã được admit. Không regenerate hay
  thay queue #301 và không gửi tin khách trong lượt này.
- **Policy:** `memory/SOUL.md` bổ sung giọng đồng hành “chúng ta có hẹn” khi
  có bằng chứng đăng ký/cuộc hẹn; không dùng “mình nhắc bạn”, “Rất mong” hay
  lời thúc giục. Closing tùy chọn; miễn phí chỉ nhắc khi có nhu cầu hiện tại;
  không tự thêm logistics hoặc suy ra nhân quả giữa các facts độc lập.
- **Implementation:** `adk_agents/agent.py:243` siết reminder composer;
  Analyst/Librarian phân biệt required facts và background, QA kiểm tra
  relevance/source/logic/pressure với REPAIR. `:471-478` nạp SOUL nguyên văn
  trực tiếp vào instruction của sáu role viết/review, gồm legacy composers,
  độc lập librarian summary. Policy nạp lúc import, process lâu dài cần reload.
- **Runtime:** `tools/l5_inbox_mas_pipeline.py:248-254,297-311` phát hiện các
  phrase reminder gây áp lực đã biết và fee-to-clothing causation. False PASS
  chuyển thành effective REPAIR, truyền correction vào composer, tối đa hai
  rewrite rồi trả reply rỗng. Model verdict gốc vẫn ở trace; kết quả pipeline
  có `wording_repairs`. Guard chỉ áp dụng class_reminder và không phải bộ
  kiểm chứng ngữ nghĩa tổng quát; những diễn đạt khác vẫn cần QA/operator.
- **Kiểm chứng:** 123 tests pass với `test_adk_wiring`, `test_mas_recommend`,
  `test_conversation_state`, `test_class_schedule_and_care_routes`. Có test
  ADK Runner thật chặn tại before_model để xác nhận rendered instruction của
  composer/QA chứa SOUL khi không có librarian summary; không gọi model live.
  Fixture trace 30 kiểm tra false PASS → sửa thành công và hết budget → rỗng;
  unit cases cho lời hẹn hợp lệ, closing, fees/preparation không có causal lỗi.
  Coverage hai module thay đổi: 82%; các dòng guard/repair mới đều được chạy.
  `git diff --check` pass. Chưa đánh giá chất lượng sinh câu bằng model live;
  test offline không chứng minh mọi biến thể ngôn ngữ đều được bắt.

### 2026-09-19 — trace 31 Warm Up: vai đại diện Page và câu hỏi về thực hành

- **Trace/thread:** `31`, `1548373332058326_100001005716854`, Bùi Duy Hùng;
  calls #3446–3450, queue #302 `approved` tại lúc rà soát. Approval không chứng
  minh tin đã được gửi. Chỉ đọc trace/DB và ghi nhật ký; chưa sửa runtime case này.
- **Trigger/gate:** `operator_care_command`, purpose `warmup`. Reactive gate
  hiện là `already_answered/skip`, khách nói cuối ngày 08/03/2017, Page có tin
  ngày 31/03/2026. Đây là proactive care theo operator, không phải reactive
  reply; không dùng skip của inbox để kết luận riêng việc chọn warmup là sai.
- **Bằng chứng:** Analyst #3446 đề xuất “anh Hùng – em / Sahaja Yoga”; lịch sử
  có cách gọi “anh”, nhưng không thiết lập người đại diện hiện tại xưng “em”.
  Librarian #3448 lặp đề xuất đó. Composer #3449 sinh “lâu rồi em mới có dịp…”
  và “việc hành thiền ... vẫn an lành và đều đặn chứ”; QA #3450 PASS.
  System prompt #3449 và #3450 đều có `Authoritative voice policy` cùng SOUL
  mới của bản sửa reminder. Không phải lỗi chưa reload policy.
- **Nguồn sai lệch:** `memory/SOUL.md` bảng xưng hô vẫn có “anh/chị – em”;
  `adk_agents/agent.py:204-212` yêu cầu casual check-in, “like a friend” và
  bắt buộc CTA. Analyst `:302` chưa tách danh xưng người nhận khỏi vai người
  gửi; QA `:401` cho phép respectful form, chưa có contract Page/anh cho
  warmup. Guard `tools/l5_inbox_mas_pipeline.py::_reminder_wording_correction`
  chỉ chạy class_reminder, không kiểm tra warmup.
- **Lỗi bổ sung:** “vẫn ... đều đặn” lấy thông tin thực hành năm 2017 như một
  trạng thái liên tục tới 2026; “ghé lại” ngầm khẳng định từng tham dự tập thể
  dù lịch sử đang thấy chỉ có ý định tham dự. Analyst còn gắn nhãn Yogi và
  Librarian nói quen anh Thi từ một câu Page hỏi, không có customer xác nhận.
- **Đề xuất:** policy riêng warmup xưng Page/Sahaja Yoga Việt Nam, giữ danh
  xưng anh/chị có evidence nhưng không suy ra “em”. Dùng “thực hành thiền” và
  câu hỏi “Dạo này việc thực hành thiền của anh có tiến triển tốt không ạ?”;
  không mô tả việc thực hành là “an lành”, không mặc định đều đặn từ lịch sử
  cũ. CTA tùy chọn. Đồng bộ SOUL, Analyst, Librarian, WarmUpComposer và QA;
  REPAIR các lỗi vai người gửi, kết hợp từ và giả định tiếp diễn/tham dự.
  Regression cần chứa đúng transcript cũ, Page/anh hợp lệ, em/anh bị sửa,
  không suy diễn attendance/Yogi; guard nếu thêm phải phân biệt “em” tự xưng
  với “anh em” chỉ cộng đồng.
- **Tokens in/out theo call:** #3446 Analyst 2267/370; #3447 Librarian retrieval
  2464/36; #3448 Librarian brief 5965/507; #3449 Composer 5033/65;
  #3450 QA 9970/1. Tổng 25699/979; Librarian cộng hai calls 8429/543.
  Đây là prompt/candidate token counts đã lưu, không phải tổng billing gồm
  mọi reasoning/cache token.

### 2026-09-19 — approved Warm-up không được executor xử lý; delivery contract được đổi

- **Case/trigger:** trace `31`, action #302, Bùi Duy Hùng; operator tạo Warm-up
  trên web và approved. Executor bị vô hiệu hoá bởi `OUTBOUND_QUEUE_TYPES=()`
  trong commit `42cc0e2`; đây là nguyên nhân không claim, không phải reactive
  gate `already_answered` hay cờ `dry_run` trên LLM trace.
- **Quyết định operator mới:** WebUI approval đủ quyền executor xử lý, không
  cần Telegram. `live` mặc định mở tab và draft, người vận hành nhấn Enter;
  `live --auto-send` cho phép gửi sau kiểm tra. Amendment trong care execution
  contract thay thế policy cũ cấm mọi delivery sau approval.
- **Implementation:** `tools/l5_hitl_execution.py` xử lý hai queue DM,
  Page-scoped atomic claim; draft pending khác không chặn approval, cùng người
  nhận không được có hai draft đang xử lý. Telegram lỗi không chặn web work.
  `tools/l5_delivery_guard.py` xác minh Page/PSID trong URL live,
  đọc messages trực tiếp trước/sau fill; không overwrite composer có sẵn.
  Web MAS snapshot toàn lịch sử DB trước khi gọi model, giữ body/actor/ID/time/
  quote/count; phát hiện cả tin lặp giống hệt khi thiếu platform message ID.
- **Trạng thái:** draft dùng `executing` + `delivery_status=drafted`, không có
  `executed_at`; poll tab để xác nhận manual send. Tin/context thay đổi →
  `rejected` + `delivery_status=outdated`, error Out-date và durable
  `fetch_request=pending`; targeted refresh dùng parser/persistence hiện có,
  lỗi giữ pending để retry. Chỉ auto/manual send có message evidence mới
  thành executed; send không chắc chắn không tự retry.
- **Live evidence đọc-only:** tab PSID `100001005716854` hiện có header
  `Hung Bui`, DB `threads/users.thread_name` vẫn `Bùi Duy Hùng`. Header Meta
  dùng `div._4ik4._4ik5`, không có semantic heading. Tên Facebook chỉ là
  metadata, không được dùng làm identity. #302 còn thiếu input snapshot;
  cần fetch đồng bộ tên/history rồi tạo/approve proposal mới. Không backfill
  snapshot cũ bằng DB hiện tại; không thay queue production trong lượt sửa.
- **Kiểm chứng:** focused pytest cho queue, delivery guard, executor, MAS
  recommendation/runner, care routes và Telegram; real Chrome local fixture
  xác minh ID/header, fill multiline không Enter. Web production build, shell
  syntax và diff checks pass. Kết quả cuối: **105 tests passed**;
  coverage queue/executor/guard **84%** (guard riêng 89%).
  Chưa chạy live auto-send; không gửi Facebook/Telegram hay inference live.

### 2026-09-19 — targeted refetch action #302 xác minh nhầm bằng tên Meta

- **Trace/thread điều tra:** action #302, Page `1548373332058326`, PSID
  `100001005716854`, recipient lưu là `Bùi Duy Hùng`.
- **Trigger thật sự:** `refresh_outdated_actions()` đã mở đúng direct URL và
  composer (log 11:57:12/11:57:27), nhưng `assert_recipient()` từ chối header
  nên durable fetch request cứ giữ `pending`.
- **Nguyên nhân:** Meta render header là `Hung Bui` (bỏ tên đệm, đảo trật tự),
  trong khi `tools/l5_delivery_guard.py` chỉ chấp nhận chuỗi NFC bằng tuyệt
  đối. URL Page/PSID vẫn chính xác và header hiện diện, nhưng so sánh tên quá
  chặt làm retry không thể hoàn thành. Lỗi `div._5_n1` xảy ra ở fallback
  sidebar của một lần direct navigation chưa ổn định; lần retry direct URL đã
  xác nhận được đúng thread.
- **Sửa cuối:** bỏ hoàn toàn heading/name khỏi điều kiện delivery. Facebook
  display name có thể đổi hoặc sai; duy nhất `asset_id` + numeric
  `selected_item_id` (Page-scoped PSID) là identity. Regression xác nhận tên
  đổi vẫn tiếp tục được, còn Page/PSID sai vẫn fail-closed.

### 2026-09-19 — action #304 bị Out-date giả vì so snapshot DB đầy đủ với DOM live rút gọn

- **Trace/thread điều tra:** trace `33`, action queue #304, Page
  `1548373332058326`, thread/PSID `1548373332058326_100001005716854` (Bùi
  Duy Hùng). Chỉ đọc trace/DB/log và code; không regenerate, không sửa queue,
  không gửi Facebook.
- **Trigger thật sự:** `operator_care_command`, purpose `warmup`. Đây là
  outreach chủ động; reactive gate tính lại `already_answered/skip` là thông
  tin context, không phải nguyên nhân delivery bị từ chối.
- **Gate/kỳ vọng vs thực tế:** #304 có `conversation_snapshot.version=1`,
  `complete=true`, 28 rows (21 lịch sử 2017, 1 Page 2026, 6 message test có
  source ID). Lúc delivery, log xác nhận direct URL và composer đã sẵn sàng;
  `scroll_up_message_panel` kết luận chỉ 8 element và `extract_thread_messages`
  trả representation live rút gọn. `assert_context_current()` vì vậy ném
  `conversation_changed: message count differs from MAS snapshot`. Không có
  bằng chứng khách gửi/edit tin nhắn giữa lúc tạo #304 (05:21:44) và kiểm tra
  (05:24:57); count khác là khác nguồn dữ liệu, không phải conversation change.
- **Nguyên nhân gốc:** `_load_thread()` ở `tools/l5_mas_recommend.py:105-132`
  lấy toàn bộ `messages` đã persist trong DB rồi đóng snapshot `complete=True`.
  Delivery lại đọc riêng DOM hiện tại ở `tools/l5_delivery_guard.py:76-94`.
  Guard `:44-54` diễn giải mọi khác biệt độ dài của snapshot complete là dữ
  liệu mới/mất. `scroll_up_message_panel()` không chứng minh browser đã materialize
  cùng canonical history DB (đặc biệt với virtualized/clustered Messenger DOM).
  Refetch `:194-205` persist lại DOM rút gọn nhưng không sửa snapshot đóng băng
  của action #304, nên chỉ tạo dữ liệu DB lai và yêu cầu regenerate, không phải
  xác minh rằng action đã thực sự outdated.
- **Đề xuất sửa/test:** snapshot delivery phải bắt nguồn từ *cùng live DOM
  representation* sẽ được kiểm trước send, hoặc dùng stable Facebook
  `source_id` cho suffix/identity comparison và chỉ cho `complete=true` khi
  browser chứng minh full-history coverage. Khi coverage không chứng minh được,
  fail `unverifiable_context`/ask operator thay vì ghi sai `conversation_changed`.
  Bổ sung regression: DB full 28 rows vs DOM equivalent/latest 8 rows không
  được gọi `conversation_changed` khi không có source ID mới; một source ID mới,
  source-ID edit, hay verified complete-history count change vẫn phải block.

### 2026-09-19 — implementation: delivery guard chỉ xác minh latest stable message

- **Phạm vi:** sửa action delivery snapshot/guard và regression offline; không
  chạy browser live, không thay action #304 hay gửi Facebook.
- **Thay đổi:** `tools/l5_mas_recommend.py::_load_thread` không còn snapshot
  full DB history. Nó đóng snapshot v2 của event cuối từ transcript đã fetch.
  `tools/l5_delivery_guard.py::assert_context_current` so event cuối đó với
  event cuối browser trước và sau khi fill composer; v1 snapshot cũ cũng chỉ
  dùng event cuối để #304 không còn fail chỉ vì 28 vs 8 DOM rows. Latest event
  phải có `source_id` Facebook; không có ID trả `unverifiable_context` và
  không draft/send. `tools/l5_hitl_execution.py` cũng không còn scroll-up
  history trong delivery path. New/latest edit/delete vẫn trả
  `conversation_changed`.
- **Kiểm chứng:** regression gồm virtualized DB history 28 rows vs live tail
  8 rows, legacy v1 full snapshot, no-ID fail-closed, mutation/new message,
  prepare/reconcile draft. `pytest tests/test_hitl_delivery_guard.py
  tests/test_l5_hitl_execution.py tests/test_mas_recommend.py -q` → **70
  passed**. Coverage chạy bằng `coverage.py` (pytest-cov không cài): delivery
  guard 88%, executor 72%, recommend 88%, total 85%. `py_compile` và
  `git diff --check` pass.

### 2026-09-19 — trace 33: hai Librarian items là hai LLM requests, không phải tool-only row

- **Trace/thread/trigger:** `33`, thread `1548373332058326_100001005716854`,
  `operator_care_command`, purpose warmup. Đã chạy report trước đọc calls;
  reactive gate hiện `already_answered/skip`, không phải gate quyết định cho
  operator care. Phạm vi review observability; không inference, gửi tin, sửa
  runtime hay queue. Action #304 rejected tại lúc kiểm tra, delivery đã được
  điều tra riêng ở entry trước.
- **Bằng chứng:** #3457 KnowledgeLibrarian là model request thật: 2943 input,
  42 candidate output, 69 thoughts tokens, 3645 ms. Response không có text
  nhưng có function_call get_knowledge ID `call_295900`, city Hà Nội, question
  lớp Hà Nội/online/lịch cộng đồng. requested/started/completed đều nằm trong
  response_json.tool_calls của chính row này; completion trả refreshed,
  chars=10289. #3458 là model request tiếp theo, 6452 input/518 candidate
  output, 375 thoughts tokens, 6672 ms, viết factual brief. messages_json
  chứa function_call và function_response cùng ID call_295900.
- **Nguyên nhân:** `adk_agents/agent.py:331` yêu cầu first call get_knowledge;
  `adk_agents/tools/l5_orchestrator_tools.py:120` thực thi build_knowledge_context
  (file/DB retrieval, không LLM) và ghi knowledge_context vào session.
  `l4_llm_trace.py:408,454` tạo/kết thúc row theo model callback;
  `:492,507` append tool events vào row đã gọi tool, không tạo tool-only row.
  `web/src/app/llm/llm-client.tsx:835` map trace.calls thành từng hàng, vì vậy
  cùng role xuất hiện hai lần. Đây là call-level view đúng số requests nhưng
  dễ nhầm khi operator muốn agent-level view.
- **UI đã triển khai:** giữ nguyên raw #3457/#3458 và render một invocation
  KnowledgeLibrarian với badge `2 LLM calls / 1 tool execution`, hai child
  rows có thể chọn riêng, và lifecycle `REQUESTED → STARTED → COMPLETED` ở
  giữa. Điều kiện group cố ý chặt: cùng trace/agent/attempt, child trực tiếp
  (`parent_call_id`), liền sequence, và call trước có tool `requested` cùng
  `started` hoặc `completed`. Group tổng input 9395, candidate output 560,
  model duration 10317 ms; wall time từ đầu #3457 đến cuối #3458 khoảng
  10.334s. Không coi response_text rỗng là không inference, không cộng
  requested/started/completed thành ba executions. Events chưa có
  timestamp/duration riêng, không suy thời gian tool chính xác từ gap.
  Cần `agent_invocation_id`/stage/repair iteration cho grouping bền vững;
  `parent_call_id` hiện là last-call chain (`l4_llm_trace.py:424`), không phải
  hierarchy agent. Vì thế UI không group retry, call không liền kề, hoặc
  cùng agent nhưng không có tool execution đã ghi nhận.
- **Tối ưu độc lập với UI:** nếu cần giảm inference thực, coordinator có thể
  prefetch theo city/purpose/scope đã xác thực rồi cho Librarian viết brief
  một lượt. Khoản 3.645s của #3457 chỉ là cơ hội giảm trên trace này, không
  phải benchmark hoặc bảo đảm latency mới. Cần regression scope/knowledge
  failure/QA trước thay đổi. Gộp UI đơn thuần không giảm tokens hay cost.
- **Kiểm chứng tests:** `pytest tests/test_llm_trace.py -q`: 6 passed,
  1 failed. Các tests ADK model/tool callbacks pass; test City HTTP trace
  fail tại monkeypatch `LLM_STREAM` vì module city_llm hiện không còn thuộc
  tính đó (`tests/test_llm_trace.py:310`), trước khi kiểm tra usage. Không
  báo cả suite pass; chưa sửa test/provider ngoài phạm vi review này.

### 2026-09-19 — Job #35: nút nhắc lịch bị chặn sai khi UI không lọc lớp

- **Trace/thread điều tra:** recommendation job `#35`, thread
  `1548373332058326_9401961e0ea95278` (Trader Nguyễn). Đọc-only DB/job và
  `l5_mas_trace_debug.py report`; không chạy lại job, không tạo queue hay gửi
  Facebook trong lượt điều tra.
- **Trigger thật sự:** `operator_care_command`, `type=care`, purpose
  `class_reminder`; request không có `programCode` vì UI đang ở bộ lọc lớp
  `all`. Job hoàn tất trong một giây với `count=0`, skip
  `class_session_not_unique`; do dừng trước `run_adk_care_pipeline`, không có
  LLM trace của job để sanitize hay enqueue.
- **Gate/kỳ vọng vs thực tế:** hồ sơ seeker đã có
  `program_code=14h30-CN-Vương Thừa Vũ-HN` và lịch 7 ngày có đúng một buổi
  tương ứng: 20/09/2026 14:30. Nhưng cùng cửa sổ còn nhiều lớp khác. Mã cũ
  tại `tools/l5_mas_recommend.py:310-316` lọc `upcoming_sessions()` bằng
  `not program_code`, nên khi filter trống nó đòi *toàn bộ catalogue* có đúng
  một buổi và trả `class_session_not_unique`. Đây không phải seeker thiếu
  đăng ký hoặc sự cố ADK.
- **Sửa:** explicit `programCode` vẫn là scope chung và phải resolve đúng một
  buổi; không có filter thì mỗi selected seeker resolve session bằng
  `seeker.program_code` đã xác thực. Mã lớp không có/không resolve duy nhất
  vẫn fail-closed với `class_session_not_found`/`class_session_not_unique`.
- **Kiểm chứng:** thêm regression cho selected seeker không có UI program
  filter nhưng có `program_code` xác thực; kiểm tra session trong Care brief
  và queue payload. `pytest tests/test_mas_recommend.py
  tests/test_class_schedule_and_care_routes.py -q` → **45 passed**;
  `pytest-cov` không được cài trong `.venv`; `git diff --check` pass.

### 2026-09-19 — trace 36: nhắc lại buổi 20/09 dù đã gửi hôm trước

- **Trace/thread:** `36`, call #3461–3465, thread
  `1548373332058326_9401961e0ea95278` (Trader Nguyễn / Yến). Chạy `report 36`
  và đọc state/calls/queue. #299 `executed`, `executed_at=2026-09-18 06:39:38`
  UTC, payload `class_reminder` cho `14h30-CN-Vương Thừa Vũ-HN`, ngày
  20/09/2026. #305 tạo 19/09 06:11:56 UTC, cùng buổi, còn pending lúc review.
  User xác nhận đã nhắc hôm trước; không gọi LLM live hay gửi tin trong sửa này.
- **Trigger thật:** `operator_care_command`, purpose `class_reminder`, instruction
  mặc định “Soạn tin nhắc lịch học phù hợp ...”. Không chỉ định nhắc dồn dập.
  Reactive state `closed_by_human/react` là context; quyết định cần có của care
  là không nhắc lại cùng buổi đã được nhắc, không phải ép dùng gate reactive.
- **Thực tế:** Analyst #3461 đề xuất cá nhân hóa lời nhắc bằng người đi cùng
  thay vì từ chối nhắc lặp; Composer #3464 soạn “Chào bạn Yến, chúng ta có hẹn
  ... chiều mai ...”; QA #3465 PASS. Transcript legacy có reminder cũ nằm trong
  quote Unknown sender, nên không coi riêng quote là bằng chứng đã gửi.
  Queue #299 cung cấp session/send record mà care brief trước sửa không có.
- **Nguyên nhân/file:** `tools/l5_mas_recommend.py::recommend_care` chỉ dedupe
  theo command_id, không xét sent history. Analyst instruction tại
  `adk_agents/agent.py:314` còn nói precheck đã admit, không được override;
  coordinator luôn chuyển Analyst → Librarian → Composer, không có stop bình
  thường vì timing không phù hợp. QA chỉ tập trung wording/facts.
- **Sửa:** `_reminder_cadence` tại `tools/l5_mas_recommend.py:293` đọc executed
  reminder theo Page/thread/class/session_date, bỏ drafted/outdated/uncertain;
  `:386` chặn trước inference, kể cả regenerate/new command. Không coi pending,
  approved, rejected hoặc failed là đã gửi. Một câu operator khẳng định độc lập
  “Đây là sự kiện cần nhắc lịch dồn dập.” hoặc “Đây là buổi học cần nhắc lịch
  dồn dập.” mở ngoại lệ có chủ đích; không dùng substring để nhận phủ định/quote.
  Opt-out và scope vẫn ưu tiên. Brief có evidence cùng cờ override đáng tin cậy.
- **Stop semantics:** Analyst/QA có `NO_SEND: <lý do tiếng Việt>`; runtime tại
  `tools/l5_inbox_mas_pipeline.py:221,256` dừng với `reply_text/draft_reply` rỗng,
  không escalation/repair. Recommendation trả skipped reason và note; web
  summary tại `web/src/app/api/action-queue/recommendations/route.ts:418` giải
  thích “Chưa phù hợp để nhắc lại ... không tạo tin để tránh làm phiền”. SOUL
  và response runbook đồng bộ policy một reminder đã gửi cho mỗi buổi.
- **Giới hạn:** deterministic evidence dùng action queue sent record; tin gửi
  tay ngoài queue được Analyst/QA đánh giá qua transcript, chưa có semantic
  detector deterministic cho mọi cách diễn đạt. Không sửa scheduler legacy hay
  tự reject #305; bản nháp cũ vẫn cần operator xử lý. Policy/prompt mới cần
  reload ở process giữ agent trong bộ nhớ. Không chạy pilot model live.
- **Kiểm chứng:** read-only replay helper trên snapshot #3461 tìm đúng #299,
  `repeat_explicitly_requested=false`, `blocked=true`. Regression DB tạm bao
  gồm ngày kế tiếp, regenerate, khác ngày/lớp/Page/thread, trạng thái chưa gửi,
  override/negation/quote/urgency và opt-out; fake ADK kiểm chứng Analyst dừng
  trước retrieval/composer, QA dừng không repair, adapter không enqueue.
  `pytest tests/test_mas_recommend.py tests/test_adk_wiring.py
  tests/test_conversation_state.py tests/test_class_schedule_and_care_routes.py
  -q` → **143 passed**; coverage.py ba module sửa **87%** (recommend 90%,
  pipeline 83%, agent 82%). Các nhánh cadence/NO_SEND mới đều được thực thi;
  toàn bộ scenario trong test plan runbook đã được cover offline. Web production
  build và `git diff --check` pass. Không coi kết quả mock là đánh giá chất lượng
  suy luận model live cho các diễn đạt ngoài fixture.

### 2026-09-19 — job 38: quyền nhắc lại bị khóa vào câu mẫu và skip mất trace

- **Trace/thread/trigger:** job `38`, `operator_care_command`, thread
  `1548373332058326_9401961e0ea95278`, class_reminder. Report thread resolve
  trace 36 vì job 37/38 dừng trước model nên chưa có row `llm_calls`.
  Đọc request/result job 38 xác nhận instruction nguyên văn có “Đây là lớp
  học gấp, nên hoàn toàn được phép giục liên. tục.”. Job 37 là lệnh mặc định.
- **Kỳ vọng vs thực tế:** #38 đã chỉ định quyền nhắc liên tục, đáng lẽ được
  vượt điều kiện “đã nhắc cùng buổi” rồi tiếp tục workflow/QA bình thường;
  code cũ trả `reminder_already_sent_for_session`. Reactive state vẫn
  `closed_by_human/react`; không phải gate dùng để quyết định quyền này.
- **Nguyên nhân:** `_reminder_cadence` trước sửa split instruction bằng
  `[.!?\n]+` rồi so cả câu với set đúng hai chuỗi, không hiểu paraphrase hay
  typo. `recommend_care` append skipped và continue không ghi trace;
  result có `/llm?trace=38` nhưng bảng llm_calls không có row tương ứng.
  Đây là giới hạn của implementation trace 36, không phải operator nhập sai.
- **Sửa:** `tools/l5_care_admission.py::interpret_repeat_permission` (dòng 31)
  gọi Gemini native một lượt khi đã có sent reminder, nhận JSON boolean,
  trích nguyên văn permission từ instruction và lý do tiếng Việt. True thiếu
  literal evidence/JSON sai/provider error → `care_instruction_unresolved`,
  không nhầm với operator không cho phép. Prompt phân biệt generic urgency,
  phủ định, quote, mâu thuẫn và explicit permission; không dùng magic phrase.
  `tools/l5_mas_recommend.py:383` chuyển decision/evidence vào care brief và
  queue payload; không bỏ opt-out, registration/session, workflow hay QA.
- **Observability:** `record_care_decision` ghi mọi skip bên trong
  recommend_care và lần admit repeat vào cùng trace/thread/Page. Row
  `CareAdmissionDecision`, model `deterministic`, event_kind care_decision,
  model_called=false, 0 tokens; skipped không phải API error. Actual inference
  có row `CareInstructionInterpreter`, prompt/input/output/usage riêng.
  `web/src/app/llm/page.tsx:77` loại audit row khỏi inference count/latency,
  giữ trong timeline và sort theo insert id khi ADK reset seq. UI có filter
  “Không đề xuất” và nhãn “Không gọi model”.
- **Dữ liệu vận hành:** bổ sung đúng hai audit rows hồi cứu cho job 37/38,
  idempotent theo trace/agent; giữ original request, result skip, reminder #299,
  original timestamp và `retrospective=true`. Ghi rõ #38 bị chặn sai bởi rule
  cũ. Không giả tạo model call lịch sử, không chạy lại job, không tạo queue hay
  gửi Facebook/Telegram; không thay #305. GET port 9995 `/llm?trace=37/38`
  xác nhận cả event, nguyên nhân, no-model và skip label hiển thị.
- **Kiểm chứng:** 155 focused tests pass (admission/recommendation/ADK wiring/
  conversation/schedule). Coverage module mới 100%, recommend 90%, pipeline
  83%, tổng 88%. Native Gemini live trong SQLite tạm: 6/6 đúng gồm nguyên văn
  #38, default, negation, customer quote, paraphrase permission và urgency-only;
  không có send/queue action. Đây là sample validation, không bảo đảm model
  phân loại hoàn hảo mọi câu. Model interpretation thêm một request cho case
  đã có sent evidence; không thể gọi đây là gate “không inference” như trước.
  Web production build và diff check pass. Test plan và runbook đã được sửa
  để không còn yêu cầu operator dùng đúng một câu mẫu.

### 2026-09-20 — Recent Messages hiển thị snapshot re-fetch chưa định danh

- **Thread/case:** `1548373332058326_9401961e0ea95278` (Trader Nguyễn). Không
  chạy fetch mới, không gọi model và không sửa/xóa dữ liệu production.
- **Bằng chứng:** `l5_mas_trace_debug.py report` cho thấy 16 message có
  `message_at` và sender đã lưu, sau đó là các rows `Unknown` lặp body, không
  có `message_at`; truy vấn DB xác nhận các row re-fetch này được append với
  sequence mới. Sidebar dùng `.slice(-20)`, nên chọn chính các rows chưa có
  thời điểm và hiển thị chúng như lịch sử gần đây.
- **Khắc phục UI/API:** `web/src/lib/queries.ts::displayableMessageHistory`
  giữ lịch sử có `message_at` khi cùng thread đã có timeline xác định; thread
  legacy hoàn toàn không có thời điểm vẫn giữ nguyên rows. Không suy luận lại
  sender/time và không xóa evidence crawl thiếu thông tin. Full detail và
  sidebar cùng dùng response này, nên count `Messages` phản ánh 16 sự kiện có
  thể hiển thị thay vì 64 rows lai.
- **Kiểm chứng:** regression UI contract cho mixed/legacy path pass;
  `cd web && npm run build` pass. Đọc lại `/seekers/5691` trên local Edge cho
  thấy 16 messages, các ngày 16/09 và 18/09 theo thứ tự, không còn dòng
  `Unknown at 1:40 PM` lặp. Test UI cũ về `handleSort('lastMessageDate')`
  fail vì API sort đó đã bị thay trong worktree trước case này; không thuộc
  thay đổi history.

### 2026-09-21 — seeker #4348 (Phố Châu): legacy quote/quick-reply bị gán cho Page

- **Thread/case:** `/seekers/4348`, thread
  `1548373332058326_285fee1acbea12c7` (Phố Châu). Điều tra read-only DB,
  fetch log và `l5_mas_trace_debug.py report`; không gọi model, crawl lại hay
  sửa dữ liệu production.
- **Bằng chứng:** các row legacy #13177--13179, persisted ngày 16/09, ghi
  Page cho một blob: lời Page “Chào Châu...” +
  `[Quoted Reply/Link]: Hỏi chi tiết` ba lần. Lần parser nguồn-aware sau đó
  ghi chính các body này ở #16181--#16188 với `sender=Unknown`,
  `sender_confidence=unknown`; evidence chỉ là `data-message-id` hoặc
  `aria-label=Message containing multiple sub messages`, không hề có actor.
  Vì vậy không có bằng chứng để backfill “Hỏi chi tiết” là Page hay Customer.
- **Nguyên nhân gốc:** parser trước commit `42cc0e2` gom mọi text container
  trong một `.x1fqp7bg` bằng `texts.join('\n[Quoted Reply/Link]: ')`, rồi gọi
  `detect_sender(raw["htmlStr"], raw["bg"])` một lần cho toàn blob
  (`thread_detail_parser.py` bản cũ khoảng dòng 302--329). Heuristic
  CSS/layout này gán Page cho toàn cluster, kể cả quick-reply/quote là một
  event riêng. UI detail sau đó render nguyên `content` legacy dưới label Page
  (`web/src/components/seeker-detail.tsx:178--186`), nên người vận hành thấy
  sender sai. Đây là lỗi ingestion legacy, không phải MAS/LLM tự phân loại.
- **Ảnh hưởng MAS:** deterministic gate hiện vẫn dùng row legacy có sender
  Page/Customer và skip `already_answered`; nó không dùng các row Unknown để
  suy actor. Không có draft/reply inbox MAS cho thread này. Tuy vậy mọi row
  legacy gộp quote/reply kiểu này là evidence không an toàn cho agent và UI.
- **Đề xuất sửa:** không suy diễn lại sender bằng text/màu/LLM. UI/API cần
  tách hoặc ẩn sender claim của legacy blob chứa `[Quoted Reply/Link]` (render
  body/quote thành evidence `Unknown`), và gate/MAS cần loại các legacy
  annotation đó khỏi actor-bearing body. Thêm regression đúng blob Phố Châu:
  Page greeting, quick-reply “Hỏi chi tiết”, customer registration và Page
  class link không được gộp hay gán chung sender. Chỉ recrawl được DOM có
  explicit actor/source evidence mới được nâng confidence cho từng event.

#### 2026-09-21 — remediation, commit archaeology và regression UI

- **Commit đã tạo đường lỗi:** `f3ecd921` (04/04,
  `fix(fb_pipeline): resolve sender attribution errors and clean debug scripts`)
  thêm `texts.join('\n[Quoted Reply/Link]: ')` và gọi `detect_sender()` một
  lần cho blob. Chính message commit nói parser CSS nhận diện Page và “aggregated
  fragmented quoted reply arrays”, tức comment/ý định lúc đó là *giữ* quote
  cùng sender; đây là giả định sai. `1e3b56fd` tiếp tục tối ưu heuristic
  gradient/layout CSS, không thay contract một-event/một-actor.
- **Commit đã sửa một phần nhưng không migrate legacy:** `42cc0e28` (19/09,
  `feat: comprehensive MAS time-aware care and inbox improvements`) thay DOM
  parser sang tách body/quote/reaction và chỉ chấp nhận actor explicit; comment
  tại `thread_detail_parser.py:816--823` nói màu/alignment không phải evidence.
  `8aed3e41` (20/09) chỉ lọc re-fetch *không có timestamp*. Không commit nào
  quarantine/render lại các row CSS-era đã persist, nên #13177 còn nguyên và
  các #16181--#16188 `Unknown` nhưng có timestamp vẫn chui vào UI.
- **Vá runtime (không sửa/xóa DB):**
  `l1_message_kind.canonical_sender_for_actor()` chỉ hạ sender khi **cả ba**
  điều kiện cùng đúng: claim Page/Customer/Auto_Page, confidence không đủ,
  và body có delimiter quote/reaction legacy. `compute_conversation_state()`
  biến claim đó thành `Unknown`; nếu nó nằm sau customer turn, trả
  `uncertain_sender/needs_review`, không auto reply. `l5_seeker_tools` dùng
  cùng canonical sender, nên prompt MAS không nhận một claim CSS-era như fact.
  Không blanket-downgrade mọi row legacy: các row không có marker vẫn giữ
  readable sender, vì chỉ thiếu confidence không chứng minh riêng row đó sai.
- **Vá reader-facing history:** `web/src/lib/queries.ts` bỏ fragment sau
  `[Quoted Reply/Link]` khỏi bubble legacy (không render “Trích dẫn từ
  Unknown”, vì điều đó vẫn ám chỉ một quan hệ quote không có bằng chứng). Khi
  cùng thread có turn Page/Customer, `displayableMessageHistory()` dùng chúng
  làm timeline và không trộn snapshot `Unknown` re-fetch; raw `Unknown` rows
  vẫn giữ DB cho audit/fetch diagnosis. Với #4348, UI còn ba turn 30/08:
  Page greeting, customer registration, Page class details.
- **Regression đã phát hiện trong lần vá đầu:** rule thử nghiệm ban đầu hạ
  *mọi* sender confidence `unknown` thành Unknown. Nó làm cả Message History
  trở nên sai/không đọc được. Đã gỡ trong cùng lượt và thay bằng predicate
  structural ở trên; test nêu rõ không được re-label toàn bộ legacy rows.
  Đây là lỗi implementation trong remediation, không phải fetch mới; không
  được lặp lại rule blanket này.
- **Kiểm chứng:** static UI regressions cho legacy split và mixed
  actor-labelled/Unknown history: `2 passed`; `cd web && npm run build`: pass;
  `git diff --check`: pass. Chạy broader Python suite hiện bị chặn **lúc
  collection, trước test sender** bởi circular import có sẵn trong working
  tree fetch: `l1_message_time → fb_pipeline.browser.__init__ → l3_inbox →
  thread_worker → l1_fetch_integrity → l1_message_time`. Không sửa phần fetch
  đó trong case này để tránh mở rộng scope; cần xử lý blocker riêng rồi chạy
  lại suite conversation/parser/persistence đầy đủ.

### 2026-09-21 — follow-up: đối chiếu identity/sender/time trước khi ghi

- Điều tra commit trước sửa: `f3ecd92` đưa CSS/cluster aggregation vào sender;
  `eea9563` giữ bypass first-thread không có ID; `42cc0e2` chuyển evidence
  contract nhưng không đóng mọi verifier/shared-wrapper path. `6ce76fa` là
  account-block gate, không phải chứng nhận tính đúng của history.
- DB read-only hiện có 13 rows của #4348, 0 explicit sender, 6 source IDs,
  13 canonical times. Không được suy rằng timestamp có giá trị thì sender đúng.
  Trace có city-classification call #2956; không có inbox reply không đồng nghĩa
  không có LLM nào từng đọc dữ liệu đó. Aggregate source-ID cross-thread collision
  trong cùng Page bằng 0, nhưng legacy thiếu ID không chứng minh được đúng người.
- Worker yêu cầu Page/PSID/heading, kiểm tra lại sau đọc; kiểm tra message ID,
  actor evidence, day/clock, chronology và stored-source conflicts; sai lệch chặn
  trước contact extraction/persist. Shared-wrapper sender không truyền xuống các
  sibling. Explicit future dates không còn bị âm thầm lùi một ngày.
- Báo lỗi có trường/source/thread và lưu observation JSON riêng để review;
  không requeue conflict. Exit 76 dừng vòng fetch; targeted refresh chuyển
  `needs_review` và không quét full history. Không sửa dữ liệu production.
- Chi tiết commit, test plan, giới hạn và tiêu chí nghiệm thu live:
  [fetch-integrity-audit-2026-09-21.md](../report/fetch-integrity-audit-2026-09-21.md).
  Repeated DOM read chỉ kiểm tra stability; chưa phải hai nguồn độc lập hay
  bảo đảm 100%. Heading/PSID thiếu thì dừng, không khôi phục fallback đoán.
