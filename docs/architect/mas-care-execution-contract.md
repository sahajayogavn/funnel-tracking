# MAS care execution contract

**Universal ID:** `doc:mas-care-execution-contract-001`

**Cập nhật:** 17/09/2026

**Trạng thái:** contract đích, chưa xác nhận mọi nhánh runtime đã tuân thủ.

Tách và chỉnh lý phần kiến trúc, dữ liệu và vận hành Telegram trước đây nằm
trong [MAS Strategy](../../memory/mas_strategy.md). Policy chăm sóc nằm ở
strategy; lộ trình khắc phục nằm ở [PRD §7](../PRDs/mas-time-aware-care-plan.md).
Không sao chép lại luồng auto-send DM hoặc SQL migration cũ vốn mâu thuẫn với
quyết định gửi tay và mô hình dữ liệu đã thay đổi.

## 1. Luồng thực thi và quyền hạn

```text
Facebook DM / comment / reaction
  → ingestion: identity, kind, raw time, absolute time, confidence
  → persistence: thread, message, profile, evidence
      ├─ inbox gate → reply / late / no_reply / human review
      │                → draft → human review → yogi gửi tay
      ├─ daily care / warm-up / event eligibility
      │                → digest → Mở phiên → recheck → draft
      │                → human review → yogi gửi tay
      ├─ registration SLA / morning brief → internal notification
      └─ registration / attendance evidence → stage evaluation

Gửi thực tế được xác nhận → contact log, budget, kết quả chăm sóc
```

Stage evaluation độc lập với việc tạo draft. Mọi DM, kể cả warm-up/event và
nhắc lớp, đều do yogi gửi tay. Reaction/comment có target và approval riêng;
không được diễn giải thành quyền nhắn DM.

## 2. Trách nhiệm theo thành phần

| Thành phần | Contract cần đáp ứng |
| --- | --- |
| Parser / persistence | Tách message/system/reaction/attachment/ad source; giữ raw time, thời điểm quan sát và mức chắc chắn; contact có nguồn từ seeker |
| `find_unreplied_threads` / conversation gate | Xác định nhu cầu còn chờ; phân biệt proposed và served; không làm mất câu hỏi do reject; không coi mọi Page reply là đã giải quyết |
| Inbox runner | Cursor theo version tin mới, xử lý hết backlog, idempotent; không stage mutation do draft |
| ADK single/batch/regen | Cùng decision contract, now có timezone, timestamp từng tin, nguồn knowledge theo seeker, không ép sinh reply. Mọi draft outbound do người vận hành kích hoạt — reply, nhắc lớp, sự kiện, warm-up — phải chạy trong một orchestrated session có tối thiểu `ConversationAnalyst → KnowledgeLibrarian → đúng Composer → ReplyQAReviewer`; không gọi composer trực tiếp để bỏ qua phân tích, knowledge hoặc QA. |
| Action queue | Tách mở phiên, duyệt phiên bản draft, gửi thực tế; reject/expiry/supersede có lý do |
| Telegram | Thông báo và quyết định được gắn đúng target/version; không tự approve khi thiếu cấu hình hoặc message ID |
| Care scheduler | 08:30 Asia/Ho_Chi_Minh; hôm nay/ngày mai; SLA kiểm tra riêng trong ngày; không LLM trước mở phiên proactive |
| Session worker | Claim atomic; recheck page, opt-out, lớp/buổi, expiry, tin mới, budget; retry không nhân bản draft |
| Attendance / stage | unknown khác absent; người xác nhận, thời điểm và nguồn; stage gate theo bằng chứng đã lưu |

Một trường `has_phone` từ regex không chứng minh persistence thành công. Brief
cho composer phải cung cấp bằng chứng đã nhận đăng ký từ hồ sơ đã lưu. Keyword
“lớp” trong lời Page cũng không chứng minh seeker chọn chương trình đó.

## 3. Trạng thái và bằng chứng dữ liệu

Các tên sau là khái niệm đích, không phải khẳng định cột/bảng đã tồn tại:

- Registration: contact nguồn nào, đã lưu lúc nào, class/session nào được chọn,
  ai xác nhận; một seeker có thể có nhiều đăng ký.
- Message time: raw timestamp, observed_at, absolute time, timezone, confidence.
  Không lấy observed_at làm tin mới khi thiếu absolute time đáng tin cậy.
- Conversation: last customer turn, yêu cầu còn chờ, human reply liên quan,
  source version; rejected proposal không đồng nghĩa served.
- Session: page, purpose, session/event key, targets, operator, opened_at,
  expiry; bản nháp sinh từ đúng phiên đã mở.
- Contact: proposed/drafted/approved không phải sent. Chỉ sent có bằng chứng
  mới tăng contact budget hoặc cool_step; trạng thái không rõ cần xác minh.
- Attendance: unknown/attended/absent/cancelled, person/session, verifier,
  verified_at; không phụ thuộc việc đã gửi lời nhắc.
- Contact preference tách khỏi temperature; opt-out áp dụng cả draft đã duyệt.

Không chạy lại các câu `ALTER TABLE` từ strategy cũ. Khi implementation cần
migration, kiểm tra schema thực tế, lên migration có version và rollback phù hợp
WAL; lượt cập nhật tài liệu này không thay đổi DB.

## 4. Output, queue và Telegram

Hiện inbox sử dụng `reply_text` với sentinel `[NO_REPLY: <reason>]` và
`[OUT_OF_SCOPE]`. Contract đích phải giữ chúng ngoài outbound text; HANDOVER và
LATE là quyết định/metadata nghiệp vụ cần mapping nhất quán, không giả định đã
có sentinel HANDOVER trong mọi nhánh code. Không đưa chuỗi điều khiển tới seeker.

Nếu dùng Telegram 👍: với digest là Mở phiên; với draft là duyệt đúng phiên bản
nội dung. Feedback làm phát sinh phiên bản cần review lại. Không có approval nào
thực thi gửi DM. Không Telegram vẫn có thể review trên `/queues`; lỗi thông báo
phải còn hiển thị, không biến thành approved.

Telegram lấy cấu hình qua `SYVN_TELEGRAM_GROUP_ID` và `TELEGRAM_BOT_TOKEN` theo
config loader của dự án. Không hardcode group ID hay đưa credential vào strategy,
prompt hoặc tài liệu công khai.

## 5. Kiểm chứng trước khi coi implementation hoàn tất

- Replay banner/closer/đổi buổi/tin ở cổng đã cũ/unknown time; single, batch và
  regeneration cho cùng quyết định, không bỏ sót yêu cầu thật.
- Retry và hai worker cùng mở phiên không tạo hai draft; opt-out/tin mới/hủy
  buổi sau mở phiên được phát hiện trước bước tiếp theo.
- Lớp ngày mai 21h và 05:30 được xét; không tạo buổi cho khóa hết hiệu lực.
- Draft/approve không tăng số tin đã gửi, không đóng SLA và không nâng stage.
- Thiếu Telegram không auto-approve; không nhánh automation nào gửi DM.
- Dry-run không ghi queue/DB hay gửi thông báo; kiểm thử dùng DB tạm/fixture.
- Với `class_reminder`, `event`, `warmup`: trace phải chứng minh đã có
  ConversationAnalyst, KnowledgeLibrarian, composer đúng purpose và
  ReplyQAReviewer; một composer-only call không đạt contract.

Các kiểm chứng này là yêu cầu cho sprint implementation, không phải báo cáo
test đã chạy trong lần sửa strategy.
