# Kiểm chứng implementation MAS — 19/09/2026

Kết luận: có tiến bộ rõ ở đợt 0–1, chưa đạt nghiệm thu tổng thể. Review working
tree hiện tại; không sửa runtime, gọi model live hoặc kích hoạt worker.

## Phần đã xác nhận

- Care chạy specialist theo thứ tự do Python điều phối; đúng composer theo
  purpose, tối đa hai lượt sửa; không dùng final turn CareOrchestrator làm draft.
- Inbox/Care có `_approved_draft` yêu cầu PASS, chặn final text khác draft.
  Đây là guard kết quả, chưa phải kiểm chứng đầy đủ provenance/version của QA.
- Operator warmup/event đi qua recommend_care; care_purpose tường minh;
  `_pick_event` Python đã bỏ fallback event cũ.
- HITL executor không claim/gửi DM; direct delivery call bị từ chối.
- Telegram thiếu message_id trả pending; dry-run session open/attendance đã
  tránh các mutation từng tìm thấy. Không mở rộng kết luận này cho mọi scheduler route.

## Findings còn phải xử lý

### P1 — Web fallback vẫn vượt precheck/QA, có thể bịa hoặc dùng event cũ

`web/src/app/api/action-queue/recommendations/route.ts:457` vẫn gọi template
engine khi MAS lỗi với type warmup/event/all. Nhánh event `:325–362` dùng
event mặc định hoặc row mới nhất không lọc ngày, lấy city của seeker làm nơi
diễn ra và không lọc opt-out tại query target. Python đã sửa nhưng end-to-end
chưa fail-closed. Cần lỗi nội bộ/needs_review hoặc fallback cùng gate/facts,
không gọi nhánh hiện tại là đã đạt contract.

### P1 — Reviewer thiếu input gốc để kiểm tra độc lập

`adk_agents/agent.py:376–377` chỉ nêu tên state keys bằng chữ, không interpolate
draft/context/facts. Care runner `tools/l5_inbox_mas_pipeline.py:215` chỉ gửi
prompt chung. Thử bằng Runner + InMemorySessionService thật, Fake BaseLlm
(không mạng và tắt trace callbacks): request QA có outputs Analyst/Librarian/
Composer qua lịch sử, nhưng không có marker chỉ dẫn operator và hội thoại gốc.
QA vì thế không được bảo đảm thấy verified session/event/profile/now nếu các
specialist trước không thuật lại. Cần input QA tường minh có facts và source
snapshot, regression kiểm tra request model thật thay vì chỉ terminal state.

### P1 — Scheduler live còn đường cũ, dry-run toàn hệ thống chưa an toàn

`tools/l5_proactive_routes.py:182–212` claim không compare-and-set, gọi composer
trực tiếp, chưa recheck opt-out/expiry/tin mới/đăng ký. Đây là phần được ghi
nhận chưa migrate, không phải regression mới. `tools/l5_scheduler_routes.py:303–316`
và `:438–450` vẫn enqueue và gửi Telegram cho warmup/event, không guard theo
dry_run ở boundary. Không chạy các route này trên DB thật để thử dry-run.

### P2 — Regenerate làm mất mục đích của draft

`web/src/components/action-queues.tsx:250–255` map toàn bộ proactive_message
thành warmup. Chọn reminder/event để regenerate sẽ tạo warmup, không bảo toàn
purpose/session/event và không thay đúng draft gốc theo type. Cần regenerate
theo queue ID + version + payload purpose, không chỉ thread ID/queue type.

### P2 — Handoff conversation state và feedback còn lỗi

`tools/l5_mas_recommend.py:319–327` chưa đặt conversation_state vào care_brief,
trong khi pipeline chỉ đọc từ đó. Mock adapter xác nhận giá trị thiếu.
Analyst prompt cũng chưa interpolate conversation_state đã thêm vào session.

`tools/l5_inbox_mas_pipeline.py:236` sửa dict từ get_session. ADK đang cài trả
deepcopy; thử get_session → sửa qa_feedback → get_session xác nhận không lưu.
Composer có thể đọc correction qua lịch sử (đã quan sát bằng Fake BaseLlm),
nên không kết luận mọi repair đều thất bại. Tuy nhiên handoff tường minh chưa
hoạt động, sẽ mất correction nếu cắt lịch sử để tối ưu. Cần state_delta/event
hoặc truyền correction trực tiếp, test với session service thật.

### P2 — Sentinel có thể vào outbound queue

`_is_safe_final_reply` cho phép `[OUT_OF_SCOPE]`, nhưng `recommend_care:337`
chỉ loại NO_REPLY/NO_SEND. Mock pipeline trả PASS + draft `[OUT_OF_SCOPE]`
đã làm `_enqueue` được gọi với chính sentinel. Không ghi DB trong kiểm chứng.
Cần parse output business outcome chung, không xử lý sentinel rải rác.

### Khoảng trống đợt 2–4 vẫn còn

`_care_skip_reason` vẫn dùng 5 tin cuối, program_code thay registration evidence;
chưa có contact budget/ledger chung, lease/retry, recheck trước enqueue và
draft version gắn QA. Gate inbox còn coi Page nói sau khách là đã trả lời;
unknown time vẫn có thể vào reply. Chưa đủ căn cứ nói đã hoàn thành phương án.

## Kiểm chứng đã thực hiện

- 147 tests pass trong 14.94s: test_adk_wiring, test_mas_recommend,
  test_class_schedule_and_care_routes, test_l5_hitl_execution,
  test_conversation_state, test_l5_inbox_mas_runner, test_telegram_hitl,
  test_l5_scheduler, test_llm_trace.
- `cd web && npm run build`: pass, gồm TypeScript và production compilation.
- ADK thật với Fake BaseLlm: chạy Analyst → Librarian → Composer → QA REPAIR
  → Composer → QA PASS, capture request để kiểm tra handoff. Không gọi API.
- InMemorySessionService thật: chứng minh mutation trên get_session copy
  không persist. Mock adapter: conversation_state thiếu và OUT_OF_SCOPE enqueue.
- Không live E2E, không benchmark latency mới, không đo coverage toàn repo.
  Tests care hiện mock terminal state mỗi bước nên chưa chứng minh full handoff.

## Đề nghị nghiệm thu

Sửa web fallback, input QA, regenerate và sentinel; thêm integration test với
ADK thật/model giả cho cả ba purposes và vòng REPAIR. Sau đó replay snapshot
ẩn danh để đánh giá nội dung, rồi pilot có người review. Scheduler chưa migrate
cần được quản lý riêng; chỉ gọi toàn hệ thống ổn sau kiểm thử concurrency,
opt-out/lịch đổi giữa phiên, dry-run mọi boundary và retry/crash recovery.

Về inference: Care đã bỏ các lượt LLM điều phối, nhưng Librarian vẫn retrieval
qua tool nên thông thường dự kiến 5 model calls, chưa phải 4. Inbox vẫn dùng
LLM orchestrator. Chưa có benchmark mới để xác nhận mức giảm thời gian/chi phí.
