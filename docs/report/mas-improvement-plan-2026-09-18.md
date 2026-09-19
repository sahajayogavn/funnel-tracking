# Phương án cải thiện tổng thể MAS

Ngày: 18/09/2026. Trạng thái: đề xuất triển khai, chưa thay đổi runtime.

Phạm vi: working tree hiện tại; inbox, Care/Recommendations, scheduler,
knowledge, queue, Telegram, trace và bằng chứng journey. Không khẳng định các
worker live đang chạy hay các lỗi dưới đây đã xảy ra trong production.
Kế hoạch này cụ thể hóa PRD `mas-time-aware-care-plan.md` §7, không thay thế
policy trong `memory/mas_strategy.md` hoặc execution contract.

## 1. Kết luận kiến trúc

MAS đã có các thành phần phù hợp nhưng chưa có một contract thực thi chung.
Điểm nghẽn chính là nhiều entry point tự quyết định eligibility, tự gọi agent,
tự diễn giải output và tự ghi queue. Thêm prompt không bảo đảm được các bất
biến nghiệp vụ. Đề xuất giữ specialist ADK nhưng chuyển điều phối bắt buộc sang
Python, cùng một decision service và cùng một draft service cho mọi entry point.

Ba ranh giới phải rõ:

- Dữ kiện có nguồn khác với diễn giải của model.
- Đã thử xử lý / đã tạo draft khác với nhu cầu đã được người xử lý.
- Mở phiên / duyệt nội dung khác với gửi thực tế.

## 2. Hiện trạng có bằng chứng

| Tầng | Quan sát trong code | Hệ quả cần xử lý |
| --- | --- | --- |
| Inbox gate | `l1_conversation_state.py:171` có Page message sau tin khách là skip; cuối hàm cho reply dù age_hours không xác định | Có thể bỏ sót yêu cầu chưa được trả lời đủ hoặc coi tin chưa rõ giờ là mới |
| Inbox worker | `l5_inbox_mas_runner.py:236` claim trước LLM; `l5_seeker_tools.py:160` ghi dấu xử lý theo seq, kể cả lỗi | Chống lặp nhưng không có lease/retry phân biệt lỗi tạm thời và kết quả nghiệp vụ |
| Runtime | `l5_inbox_mas_pipeline.py:189` đọc QA nhưng không buộc PASS; guard agent chỉ đếm calls | Prompt yêu cầu workflow không tương đương bảo đảm workflow |
| Operator | `l5_mas_recommend.py:230,279` gọi composer trực tiếp; `:305` suy purpose từ program filter/keyword | Bypass pipeline và nhầm warm-up/event thành nhắc lớp |
| Precheck | `l5_mas_recommend.py:249,313` fallback event không lọc ngày/city; registration chỉ program_code; opt-out 5 tin | Nhãn verified mạnh hơn bằng chứng thật |
| Session worker | `l5_proactive_routes.py:162–210` đọc approved rồi update không compare-and-set; không recheck đầy đủ | Hai worker có thể cùng xử lý; dùng session/profile đã cũ |
| Dry-run | `l5_proactive_routes.py:173,193,280` vẫn update/insert; attendance sync vẫn gọi stage gate | Không thể dùng dry-run production làm sandbox nghiệm thu |
| Scheduler proactive | `l5_scheduler_routes.py:246,269,407` sinh draft trước phiên; `:303,442` ghi queue/thông báo | Chưa theo policy digest → mở phiên → draft |
| Gửi DM | `l5_hitl_execution.py:81–108` có nhánh commit_reply_via_cdp sau approve | Mâu thuẫn policy gửi tay; đây là khả năng code, chưa xác nhận worker live |
| Telegram | `l5_telegram_hitl.py:252` trả approved khi thiếu message_id; đường gửi mới giữ local proposal khi lỗi | Cần đóng helper cũ, đồng thời kiểm tra reachability trước kết luận mức ảnh hưởng |
| Attendance | `l5_proactive_routes.py:230,279` dựa reminder_log; reject được diễn giải absent | Chưa đủ unknown/cancelled, phụ thuộc draft lời nhắc |
| Context | care có now/transcript/profile nhưng thiếu conversation_state; knowledge dùng ví dụ Page theo city | Mất kết luận deterministic; có nguy cơ dùng thông tin khác thread như fact |

Review trước đã chạy 46 tests liên quan, đều pass. Mock runtime xác nhận draft
vẫn được trả khi QA rỗng/REPAIR/ESCALATE nếu final text bình thường. Đây là bằng
chứng thiếu guard; không phải thống kê chất lượng model production.

## 3. Luồng đích

```text
UI / CLI / scheduler
  → Chuẩn hóa request + xác định page/target/purpose
  → Snapshot có version + policy gate chung
      ├─ blocked / no_reply / needs_review → lưu quyết định, việc cần người
      ├─ proactive candidate → digest → operator mở phiên → recheck
      └─ reactive eligible hoặc phiên proactive hợp lệ
           → Analyst → Librarian → Composer theo purpose → QA
               ├─ repair → sửa có giới hạn → QA lại
               ├─ escalate → công việc nội bộ cho yogi
               └─ pass → kiểm tra cuối + recheck version → draft pending
                    → review đúng phiên bản → yogi gửi tay
                    → xác nhận gửi thực tế → contact ledger/budget
```

Một lệnh Care rõ mục đích, target và lớp/event có thể chính là thao tác mở
phiên; không bắt operator bấm hai lần. Hệ thống phải lưu session/operator/scope.
Scheduler chỉ tạo candidate/digest, không tự coi lịch chạy là mở phiên.

### Request và context chung

Request: request_id, trigger, page_id, thread_id, purpose tường minh,
session_id/event_id/program_id tùy purpose, operator_id, feedback,
regenerate_of và idempotency_key. Bộ lọc UI không được đổi purpose.

Snapshot: now có timezone, message version, conversation_state, các yêu cầu
còn chờ, profile kèm nguồn/version, registration/attendance, contact preference,
contact budget, session/event còn hiệu lực và knowledge version. Transcript
gồm tin gần đây cộng các tin bằng chứng liên quan; không chỉ cắt 15 tin cuối.

Gate chung chặn opt-out, sai page/kênh, lịch hết hạn/hủy, thiếu registration
cần thiết, trùng work và vượt budget. Manual được chọn thời điểm mở ca nhưng
không vượt các chặn này. Timestamp/đăng ký không chắc → needs_review, không
tự gắn verified. Tin mới từ seeker vẫn được xét reactive dù họ đã ngừng proactive.

### Phân công deterministic và LLM

Python sở hữu quyền liên hệ, thời gian, lịch, ngân sách, state transition,
idempotency và retry. Analyst xác định ý nghĩa, các nhu cầu còn chờ, xưng hô,
mâu thuẫn và bằng chứng message IDs. Có Page reply chỉ là tín hiệu; khi chưa
đủ bằng chứng phục vụ, giữ needs_review hoặc phân tích, không đóng yêu cầu.

Librarian nhận scope đã xác định, trả facts có source_id, thời hạn và trạng
thái conflict/missing. QA kiểm tra claim của draft với facts, không coi một
đoạn văn Librarian sinh ra tự động là bằng chứng xác thực. Ví dụ khác thread
chỉ dùng cho văn phong sau khử dữ liệu riêng; xưng hô lấy trong cùng thread.

Composer được chọn bằng bảng purpose → agent. Output có draft_id/version.
QA trả cấu trúc verdict, draft_version/hash, issue codes và evidence refs.
Chỉ PASS của đúng draft/context mới được enqueue; final text do runtime lấy
từ draft đã duyệt, không để orchestrator viết lại sau QA. Sanitize làm đổi
nội dung thì phải xác định lại version trước QA, không sửa âm thầm sau PASS.

Giới hạn đề xuất: một lượt ban đầu + tối đa hai lượt sửa; chạy lại Analyst/
Librarian chỉ khi issue cần facts mới hoặc context thay đổi. Ngân sách calls,
tokens và thời gian do runtime chặn; lỗi parse được retry có giới hạn riêng.
Không override ESCALATE thành draft chỉ vì các state key có dữ liệu. Thiếu
QA hoặc specialist → invalid_result nội bộ, không biến thành no_reply nghiệp vụ.

## 4. Dữ liệu và vòng đời

Triển khai migration bổ sung, có version, thử restore backup trên DB tạm;
không suy dữ kiện mới từ dữ liệu cũ thiếu nguồn.

| Đối tượng | Trường/bất biến cần có |
| --- | --- |
| Work item / attempt | page + thread + purpose + source version + session/event; lease, attempt, retry_after, status, outcome |
| Registration | received evidence riêng với selected program/session evidence; cho phép nhiều đăng ký |
| Contact preference | phạm vi/kênh, allowed/opt_out/unknown, source message, thời điểm thay đổi |
| Class/event catalog | ID ổn định, timezone, thời hạn, trạng thái hủy/đổi, source/version; một nguồn chuẩn |
| Care session | operator, selected targets, purpose, opened_at, expires_at, source versions |
| Draft | version, context hash, QA review, approved version, superseded/expired reason |
| Contact ledger | sent_confirmed/sent_unknown, thời gian/người/bằng chứng, purpose/session/event |
| Attendance | unknown/attended/absent/cancelled, người xác nhận, thời gian, nguồn |

Claim atomic theo work item, có lease và recover sau crash. Lỗi mạng/model
retry cùng work với attempt mới; escalate cần người xử lý không tự chạy vòng
lại. Reject draft vẫn giữ nhu cầu mở; regenerate ghi feedback và phiên bản mới.

Không xóa khả năng operator tạo hai command khác nhau. Dedupe retry theo
command, đồng thời điều phối xung đột theo người/buổi/event và budget chung.
Kiểm tra trước LLM giảm chi phí; transaction/unique constraint mới bảo đảm
không enqueue trùng khi hai worker chạy đồng thời.

Recheck trước chạy và trước enqueue/approve; nếu tin mới, opt-out, thay lịch
hoặc phiên hết hạn, chuyển stale/needs_review. Trước gửi tay UI phải hiển thị
bối cảnh mới nhất; hệ thống không thể bảo đảm hành động gửi ngoài UI nên cần
ghi nhận người xác nhận và thời điểm kiểm tra/gửi.

Chỉ sent_confirmed tính budget proactive chung một DM/7 ngày theo strategy.
sent_unknown ngăn tự thử gửi lại và tạo việc xác minh. Draft/approve không
tăng cool_step, không đóng SLA, không nâng stage. Stage evaluator chạy từ
evidence events, tách khỏi runner sinh draft.

## 5. Scheduler và vận hành

- Brief 08:30 Asia/Ho_Chi_Minh; lớp còn tương lai trong hôm nay/ngày mai theo
  ngày lịch, không dùng cửa sổ 36h. SLA tính từ đăng ký chưa được xử lý.
- Warm-up/event chỉ tạo candidates có lý do; reactive còn chờ được ưu tiên.
- Attendance checklist từ đăng ký/lịch hẹn, không từ việc có draft lời nhắc.
- Session worker dùng cùng draft service với modal, claim atomic và recheck.
- Theo policy hiện hành, DM approval chỉ duyệt nội dung. Khi triển khai cần
  chặn nhánh executor tự gửi DM; inventory worker/config bằng đọc trước khi
  thay đổi vận hành. Comment/reaction giữ quyền và workflow riêng.
- Dry-run là chế độ không side effect thực sự: không DB nghiệp vụ, queue,
  stage mutation, Telegram hay Facebook. Replay LLM là chế độ riêng, opt-in,
  ghi kết quả trong kho thử nghiệm; không đánh đồng dry-run với gọi model.
- Web/Telegram cùng trỏ action và version. Thiếu cấu hình/thông báo lỗi giữ
  pending + delivery error; không helper nào được tự approve.

## 6. Trace và chất lượng

Tách job_id (một thao tác batch), run_id (một seeker/work), attempt_id và
call_id; UUID cho run, không dùng số job làm trace duy nhất. Chưa khẳng định
nguồn gốc trace trùng chỉ ở UUID generator: span hiện có kế thừa parent ID,
nên phải kiểm tra caller truyền ID và correlation xuyên batch.

Mỗi kết quả lưu decision + reason + context/version + agent transitions + QA
version + queue outcome, kể cả blocked/skip/escalate/error. UI cho biết vì sao
chọn người này, facts từ đâu, bước nào lỗi, ai cần xử lý tiếp. Template fallback
không được mang nhãn MAS/QA PASS; mặc định lỗi model tạo việc nội bộ. Nếu giữ
template nghiệp vụ, cho đi qua cùng eligibility/fact validation và gắn nhãn riêng.

Đo baseline trước tối ưu: draft dùng nguyên/chỉnh/bỏ; false-negative trên
skip; unresolved requests; SLA; stale drafts; opt-out/budget violations;
duplicate work; tokens và latency p50/p95 theo purpose và kết quả. Không lấy
giảm calls làm thành công nếu tăng bỏ sót. Chưa ấn định phần trăm tiết kiệm
khi chưa có workload baseline tương đương.

## 7. Thứ tự triển khai và nghiệm thu

| Đợt | Thay đổi | Tiêu chí kết thúc |
| --- | --- | --- |
| 0 — baseline và ranh giới | Inventory entry points/workers; fixture ẩn danh; đóng QA bypass, dry-run writes, missing-ID auto-approve, DM auto-send theo policy | Không draft thiếu PASS; không mutation trong dry-run; approve DM không gọi send |
| 1 — runtime thống nhất | Typed request/result/context; Python workflow; explicit purpose; nối modal, Recommendations và regenerate | Mọi operator path cùng contract; trace đủ bước đúng thứ tự; repair/version tests pass |
| 2 — gate và evidence | Contact preference, registration/catalog, source versions; gate chung; xử lý unknown/expired/unresolved | Không sự kiện cũ/sai city; không suy đăng ký; không bỏ yêu cầu chỉ vì Page nói sau |
| 3 — vòng đời bền vững | Work lease/retry, idempotency, draft version, sent ledger; migration queue/HITL | Crash/retry/hai worker không trùng; reject không mất việc; approve không tính sent |
| 4 — scheduler hội tụ | Candidate/digest → mở phiên → draft service; lịch ngày; SLA/attendance riêng | Không LLM trước mở proactive; recheck opt-out/tin mới/hủy lớp; attendance 4 trạng thái |
| 5 — pilot và tối ưu | Shadow trên snapshot; pilot target riêng; scoped retrieval/cache/version; loại đường legacy | Zero vi phạm bất biến trên bộ nghiệm thu; chất lượng/chi phí so baseline; không worker cũ chạy song song |

Đợt 0 chặn việc tạo/gửi không đúng contract; không có nghĩa tắt toàn bộ inbox.
Các route chưa migrate cần hiển thị blocked/needs_review rõ ràng. Schema tối
thiểu work/version có thể thêm ở đợt 1; đợt 3 hoàn thiện retry/ledger. Không
đưa scheduler lên runtime mới trước khi phần gate và vòng đời đã nghiệm thu.

Các file sở hữu chính: `l5_inbox_mas_pipeline.py`, `adk_agents/agent.py` cho
runtime; `l1_conversation_state.py`, `l1_class_schedule.py` cho luật thuần;
`l5_mas_recommend.py` và web API cho adapters; `l4_sqlite_store.py` và
`l5_action_queue.py` cho persistence; `l5_proactive_routes.py`,
`l5_scheduler_routes.py`, `l5_hitl_execution.py`, `l5_telegram_hitl.py` cho
vận hành. Mỗi đợt tách PR nhỏ kèm tests theo `.agents/rules/devops-qa.md`.

## 8. Bộ nghiệm thu bắt buộc

1. Workflow: thiếu/sai thứ tự agent, sai composer, QA rỗng/REPAIR/ESCALATE,
   PASS trên draft cũ, model sửa text sau PASS → không enqueue.
2. Purpose: warm-up khi đang lọc lớp; event chứa tên thứ/ngày; regenerate
   reminder giữ purpose/session; single và batch không trộn context.
3. Conversation: fresh, late, stale, giờ không rõ, banner, closer, Page hỏi
   tiếp nhưng chưa giải quyết, yêu cầu đổi buổi, tin "tới cổng" đã hết hạn.
4. Evidence: contact đã lưu không hỏi lại; chưa chọn lớp không gán lịch;
   opt-out ngoài 5 tin cuối; event cũ/sai city/hủy; facts mâu thuẫn cần review.
5. Concurrency: double click, hai worker, crash trước/sau enqueue, lease hết
   hạn, tin mới/opt-out/hủy buổi giữa run và enqueue/approve.
6. Lifecycle: reject còn unresolved; transient error retry có hạn; hai command
   độc lập giữ audit; draft/approve không sent, không stage/cool_step/SLA closure.
7. Operations: dry-run spy cấm mọi write/network side effect; Telegram thiếu
   config/message ID không approve; DM approval không send; attendance unknown
   khác absent, cancelled khác no-show; lớp 21h/05:30 ngày mai đều được xét.
8. Content: giọng mình/bạn mặc định; cô/chú có nguồn; không mượn địa chỉ/xưng
   hô khác seeker; escalation không biến thành outbound text.

Unit và integration dùng DB tạm. Replay ẩn danh đánh giá cả draft lẫn skip;
live model eval tách khỏi deterministic CI. Khi sửa TypeScript chạy web build.
Pilot giữ gửi tay; rollback dừng worker mới và giữ dữ liệu/audit, không bật lại
đường bypass hoặc replay tự động các action approved cũ. Chỉ mở rộng sau khi
operator review mẫu và các bất biến trên đạt yêu cầu.
