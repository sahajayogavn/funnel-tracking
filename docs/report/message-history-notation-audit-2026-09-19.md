# Audit notation message history — 19/09/2026

Kết luận: chưa đạt yêu cầu bảo toàn ngày, sender, reply và reaction. Có mất
ngữ nghĩa được tái hiện bằng code thật, không chỉ nghi ngờ prompt. Không sửa
parser/DB/live worker trong lượt audit này.

Phương pháp: đọc DOM parser → contract → persistence → MAS formatter → UI;
SQLite production mode=ro chỉ thống kê; chạy parser JavaScript thật bằng Chrome
headless trên HTML tổng hợp, SQLite in-memory cho persistence. Không mở Facebook
live; không khẳng định HTML tổng hợp là DOM Facebook đang hiển thị hôm nay.

## Findings

### P1 — Mất ranh giới quote, có thể gán lời người khác cho sender hiện tại

`fb_pipeline/browser/inbox/thread_detail_parser.py:365–424` gom text containers
trong bubble cluster rồi nối mọi đoạn bằng `[Quoted Reply/Link]`. Không nhận
diện reply_to, quoted sender, message ID hay phân biệt link preview với quote.
Nested `.x1fqp7bg` bị skip `:309–318`; cluster có nhiều message không còn chắc
là một message. Chạy DOM tổng hợp có hai `.x1y1aw1k` bình thường nhận một row:
`Tin thứ nhất\n[Quoted Reply/Link]: Tin thứ hai`.

`fb_pipeline/contracts/l1_message_kind.py:46–59` xóa mọi prefix quote, không
chỉ prefix của reaction. `format_conversation_lines` gọi hàm này. Tái hiện:

```text
Input Customer: Dạ\n[Quoted Reply/Link]: Bạn ở Hà Nội phải không?

MAS nhận:
[2026-09-19 09:00 | Customer] Dạ
Bạn ở Hà Nội phải không?
```

Không còn thông tin câu thứ hai là quote, của ai, đáp vào message nào. Đây là
mất attribution trước inference; reviewer không thể phục hồi chắc chắn từ text.

### P1 — Reaction không có actor hoặc target; không phân biệt thread/message

Parser `:403–417` chỉ đọc img alt thành LOVE/LIKE/...; không đọc actor, target,
count, timestamp, tooltip/aria-label. Tag được nối vào text cluster qua cùng
prefix quote. HTML kiểm thử có `aria-label="You reacted Love to Tin thứ hai"`
vẫn chỉ trả `:::REACTION_LOVE:::`. Không được suy actor là người đối diện sender.

`InboxMessage` (`l1_inbox.py:24`) và schema messages không có reaction_actor,
target_message_id, scope, reply_to. Bảng reactions (`l4_sqlite_store.py:396`)
là log hành động agent/dry-run, không phải dữ liệu reaction được crawl từ khách.
Persistence `l3_pipeline.py:237–240` chỉ giữ kind=message: pure reaction bị bỏ;
reaction thêm vào message cũ bị normalizer bỏ khi dedupe, không có upsert reaction
riêng. MAS lọc pure reaction và strip marker; UI hiển thị content thô, không có
reaction entity. Hiện không thể khẳng định ai react, react cho ai, hay scope nào.

### P1 — Ngày và giờ chỉ là một biến label, không phải cấu trúc ngày + giờ

Parser `:285,303–306` overwrite currentTimestamp mỗi khi gặp label. DOM thử:
`Sep 10, 2026` → `9:00 AM` → message chỉ trả `9:00 AM`. Resolver với anchor
19/09/2026 10:00 cho `2026-09-19 09:00:00, approx=False`, sai ngày của fixture.
Không ghép clock label với day separator; không lưu nguồn/confidence của label.

Date-only resolver tự chọn noon/giờ anchor và đánh dấu approx, nhưng
`l5_seeker_tools.py:77` không lấy message_at_approx. MAS không thấy cảnh báo
độ chắc chắn. Date grouping UI `messenger-message-list.tsx` chỉ parse tháng
tiếng Anh hoặc slash date, không dùng message_at: Today/Mon/ISO không được
group đúng đầy đủ. Giá trị timestamp khác nhau giữa DOM, DB, UI và MAS chưa có
một contract thống nhất.

### P1 — Sender dựa vào màu và được sửa tiếp bằng nội dung

`sender_validator.py` coi gradient/màu khác xám/màu trắng hoặc chuỗi 'Đã gửi'
là Page, còn lại Customer; không có Unknown/confidence. Parser lấy background
của descendant đầu tiên hoặc ancestor tối đa 6 tầng, có thể lấy của preview/
wrapper. HTML thử text bubble trắng bị gán Page; đây là chứng minh heuristic,
không khẳng định khách trên Facebook hiện dùng bubble trắng.

Persistence `l3_pipeline.py:245–294` đổi Page thành Auto_Page dựa từ khóa nội
dung hoặc AD-context prefix, không bằng chứng tài khoản/automation.
UI `web/src/lib/queries.ts:70–90` lại đổi Customer thành Page nếu text chứa
'bạn ạ', 'hoàn toàn miễn phí', v.v.; Auto_Page cũng bị gộp thành Page.
Vì vậy UI có thể trông đúng trong một case nhưng khác sender mà MAS đọc từ DB.
Đây ảnh hưởng trực tiếp gate ai giữ lượt, contact extraction và registration.

### P1 — Dedupe làm mất tin lặp thật và có thể sai thứ tự lịch sử

`l3_pipeline.py:225–280` dùng set của (normalized sender, normalized text),
không message identity hoặc thời gian. SQLite in-memory input:
Customer 'Dạ' ngày 10/09 → Page 'Hẹn gặp bạn' → Customer 'Dạ' ngày 19/09.
Chỉ lưu hai row đầu. Last-turn gate sẽ hiểu Page giữ lượt sau khi tin khách mới
bị mất. Các message cũ mới được phát hiện cũng được append seq sau tail hiện
có; chưa có chronology merge theo stable identity. Không thể sửa bằng prompt.

### P2 — Kiểm chứng ingestion chưa đủ độc lập

`integrity_validator.py` chỉ xét rỗng/số lượng, không kiểm tra sender/date/
reply/reaction; worker còn không dùng kết quả is_valid để chặn persist.
`l3_fetch_qa.py:175–212` kiểm tra last preview và sender, không toàn timeline;
sender mismatch khi text khớp chỉ là soft. Tests reaction parser mock kết quả
page.evaluate nên không thực thi JS nhận diện actor/target. Fixture JSON chỉ
có sender/text/timestamp không phải bằng chứng ground truth từ DOM/screenshot.

## Phạm vi dữ liệu và kiểm thử

- Snapshot DB: 3.556 messages, 725 có `[Quoted Reply/Link]`, 174 có marker
  reaction. Hai nhóm có thể chồng lấp; không phải tỷ lệ sai. Không cột stable
  Facebook message ID, reply target, reaction actor/scope.
- 100 tests pass: l3_inbox_pipeline, l3_inbox_worker, l4_inbox_persistence,
  conversation_state, l1_inbox_contracts, seeker_timeline_component.
- Parser JS thật trên Chrome headless tái hiện grouping/time/reaction metadata
  loss; pure formatter tái hiện quote loss; persistence in-memory tái hiện
  repeated-message loss. Không gọi API LLM hoặc ghi DB nghiệp vụ.
- Chưa có đối chiếu Facebook live với các rows lịch sử; không gán tỷ lệ lỗi
  production hay tự sửa sender/dates dựa nội dung.

## Hướng sửa và điều kiện nghiệm thu

Ưu tiên tầng evidence trước tuning MAS. Event contract tối thiểu:

```text
message: source_id, sender_actor/role, body, time_raw, day_context,
         occurred_at, time_precision, source/evidence, parse_confidence
reply:   message_id, reply_to_message_id, quoted_sender, quoted_text
reaction: actor, emoji, target_type(message/thread/unknown), target_id,
          observed_at, occurred_at_if_known, source/evidence
```

Giữ body độc lập quote/link/media; không chắc actor/target/time → unknown,
không đoán. ID nội bộ không được giả là Facebook source ID. Khi source ID
không khả dụng dùng snapshot-scoped identity và alignment có độ chắc chắn,
giữ hai tin giống text nếu evidence là hai message riêng.

Lưu raw snapshot phù hợp và parser_version để replay; append/upsert theo
identity, không global text set. Một formatter chung cho MAS/UI/Telegram:
date grouping rõ, mỗi message một sender, reply chỉ rõ target hoặc unknown,
reaction event riêng; giữ Page vs Auto_Page khi có bằng chứng. Tránh biến
quote thành nội dung khách trong contact/classification/registration extraction.

Bộ regression cần DOM + expected structured events: hai ngày với clock riêng;
ngày/giờ VI/EN; nhiều bubble cùng sender; nested quote sender đối diện; quote
chưa tải target; link preview; emoji message khác reaction; Page/Customer/self
reaction; nhiều người react; reaction được thêm/xóa; unknown/thread scope;
hai câu giống nhau khác ngày; lịch sử lazy-load; Page manual dùng canned text.

Sau đó đối chiếu một tập threads live với screenshot/DOM có chú thích người
review trước khi recrawl/migrate. Dữ liệu legacy thiếu actor/target không thể
backfill chắc bằng LLM. Lập danh sách cần xác minh, giữ audit trail và không
ghi đè bằng suy đoán. Chưa triển khai thay đổi hay dừng worker trong audit này.
