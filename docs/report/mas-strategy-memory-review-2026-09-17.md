# Rà soát MAS Strategy và cấu trúc memory — 17/09/2026

**Universal ID:** `doc:mas-strategy-memory-review-001`  
**Trạng thái:** phân tích và đề xuất; chưa triển khai trong lượt rà soát này.  
**Đối chiếu:** [audit ban đầu](mas-execution-audit-2026-09-17.md), [kế hoạch](../PRDs/mas-time-aware-care-plan.md).  
**Phạm vi:** toàn bộ sáu file Markdown trong `memory/`, cấu trúc thư mục, code inbox → batch → queue/Telegram, care routes, README/use cases và truy vấn DB chỉ đọc. Không đọc nội dung credential, không chạy scheduler/LLM/browser, không sửa dữ liệu.

## 1. Kết luận và quyết định của chủ dự án

Giữ journey và giọng giao tiếp ấm áp của MAS. Thay trọng tâm từ “seeker im lặng thì tìm cách nhắn tiếp” sang “đội chăm sóc có việc gì cần xử lý, dựa trên bằng chứng nào”. Một người đã hẹn đi học không nhắn thêm không đồng nghĩa họ mất quan tâm.

- Được nói **“CLB đã nhận được thông tin đăng ký”** khi tin đăng ký thật đã được bóc thông tin và lưu vào hồ sơ `/seekers`. Không yêu cầu yogi duyệt mới được thừa nhận việc tiếp nhận này. Tuy nhiên, tiếp nhận thông tin không đồng nghĩa đã xếp lớp, giữ chỗ hay xác nhận attendance.
- Nhắc lịch chạy một lần trong khung **08:00–09:00**, đề xuất mặc định 08:30 giờ Việt Nam.
- Proactive tạo digest/candidate cho quản trị viên trước; chỉ sau **Mở phiên** mới soạn draft. Duyệt draft vẫn không đồng nghĩa gửi DM; yogi gửi tay.
- Công việc hiện tại chỉ cập nhật tài liệu và lập kế hoạch. Code đã sửa sẵn trong workspace không phải thay đổi của lượt rà soát này và chưa được coi là đã nghiệm thu.

## 2. Audit cũ và hiện trạng khác nhau

Quan sát DB khoảng **16:41 ngày 17/09/2026, Asia/Ho_Chi_Minh**; đây là dữ liệu đang hoạt động, số đếm có thể tiếp tục đổi:

| Dữ liệu | Kết quả đọc |
| --- | --- |
| messages | 3.550: 3.050 message, 453 system_banner, 8 reaction, 39 ad_source |
| message_at NULL | 0; cả 3.550 dòng có message_at_approx=0 — không chứng minh tất cả ngày suy ra đều đúng |
| reply_message queue | 41 pending, 213 rejected; không có nhóm approved/executed |
| events / attendance / reminder_log | 0 / 0 / 0 |
| users.lead_stage | 489 Intake, 54 Seeker, 6 Seeker_Public_Program |
| llm_calls còn trong DB | 22 dòng ngày 17/09, route=unknown, tokens_in tổng 2.388 |

Không thể tái chứng minh 244 call/~255K token bằng bảng trace hiện tại. Giữ chúng là số liệu của audit trước khi dọn; không cộng lẫn hai thời điểm. Tương tự, “222 manual_clear” và “213 rejected reply” không cùng phạm vi đếm nếu chưa có truy vấn chứng minh.

Ca Quang Chiến: banner hiện đã là `system_banner`; lời cảm ơn và câu “Dạ !” đều được resolve thành 14/09. Không còn đúng khi mô tả code hiện tại là hoàn toàn thiếu time gate/NO_REPLY. Tuy nhiên, vẫn có lỗ hổng về bằng chứng, idempotency và prompt.

## 3. Những điều cần sửa trong MAS Strategy

| Vấn đề trong `memory/mas_strategy.md` | Điều chỉnh đề xuất |
| --- | --- |
| Telegram Approval Flow và Phase 6 chỉ dẫn LIKE → Hit Enter → gửi hàng loạt; Stage 2 lại nói gửi thủ công | Một contract thống nhất: digest → mở phiên → draft → duyệt → gửi tay → ghi nhận đã gửi. Reaction/comment có contract riêng, không suy quyền gửi DM từ chúng |
| Warm-up Cool +3 ngày, rồi +5 ngày; anti-spam lại tối đa 1/7 ngày | Một policy dùng chung mọi route, tính từ tin thực sự gửi; không tăng cool_step do tạo draft. Bỏ chuỗi cứng 3/5 ngày |
| Dormant vừa được nhận event mỗi quý vừa bị Message QA Gate chặn toàn bộ | Tách engagement và quyền liên hệ; dormant mặc định ngừng proactive, ngoại lệ phải có căn cứ đồng ý và quyết định riêng |
| Follower và Curious cùng map `Seeker`; một DM bị coi như bằng chứng follow | Lưu intent/touchpoint riêng; không suy follow từ DM. Không cần thêm stage chỉ để phân loại câu hỏi |
| Registered “hoàn thành 4 tuần”, G4 “≥3/4 buổi”, phần Deep lại yêu cầu đang học 18 tuần | Attendance là bằng chứng đủ điều kiện, còn tham gia khóa sâu là quyết định riêng của người phụ trách |
| Nhiệt độ dựa trên silence_days, kể cả người đã hẹn lớp hoặc học đều | Ưu tiên lịch hẹn/attendance. Tách `last_customer_message_at`, `last_human_reply_at`, `last_attended_at`, `last_proactive_sent_at` |
| Stage cao gắn số tháng, hành vi thực hành và vai trò cộng đồng | Không tự suy phẩm chất/tâm thức hoặc nâng vai trò từ tin nhắn. Chỉ lưu tiến trình và xác nhận của người phụ trách |
| Event online “gửi tất cả Stage 1+”, cross-sell mặc định | Event là lời mời có liên quan đến nhu cầu/đồng ý, chỉ tạo candidate; online không có nghĩa mọi seeker đều muốn nhận |

Thứ tự quyết định đề xuất: kiểm tra quyền liên hệ/kênh → nhu cầu còn chờ và độ tin cậy thời gian → hẹn lớp cụ thể → SLA/điểm danh → warm-up hoặc event nếu có lý do. Temperature chỉ giúp sắp ưu tiên, không tự tạo lý do nhắn.

Nên tách bốn trục: **journey stage**, **conversation state**, **participation/registration**, **contact preference**. `unsubscribed` không phải một mức nhiệt độ; `unknown attendance` không phải `absent`.

## 4. Vì sao context dài — đo theo từng đường chạy

Đo bằng `len(text)` và số byte UTF-8 của file hiện có; không suy token từ số ký tự tiếng Việt.

| Nguồn | Ký tự | Byte UTF-8 | Vai trò phù hợp |
| --- | ---: | ---: | --- |
| SOUL.md | 5.463 | 6.805 | Giọng điệu và ranh giới, chỉ nạp một lần |
| faq.md | 6.881 | 8.776 | Lấy mục liên quan |
| lop-hoc.md | 9.656 | 11.364 | Dữ kiện lớp đúng địa điểm/hình thức |
| su-kien.md | 3.236 | 4.002 | Sự kiện còn hiệu lực |
| research.md | 10.957 | 13.692 | Chỉ nội dung đã kiểm chứng, khi cần |
| mas_strategy.md | 34.793 | 46.353 | Tài liệu điều phối, không phải context trả lời |

Loader cũ lấy năm file đầu và 250 dòng đầu strategy (12.354 ký tự), tổng nội dung 48.547 ký tự trước nhãn nguồn/dấu phân cách; giải thích mức ~48,8K trong audit. Cắt 250 dòng còn bỏ phần chống spam/HITL ở cuối nhưng vẫn giữ sơ đồ kiến trúc đầu file: vừa thừa vừa thiếu.

**Code hiện tại:** `load_knowledge_context()` còn 4 file SOUL/FAQ/lớp/event, đo được **25.386 ký tự**. Warm-up/event/recommend còn các call site dùng loader này. Inbox dùng `build_knowledge_context()`, bỏ strategy, research chỉ thêm theo keyword; batch bỏ SOUL khỏi knowledge vì đã inline trong instruction.

| Input builder, không SOUL/không ví dụ hội thoại | Ký tự knowledge |
| --- | ---: |
| Hà Nội, “Cho em hỏi lịch học” | 3.522 |
| HCM, cùng câu hỏi | 2.399 |
| Chưa biết city, cùng câu hỏi | 5.871 |
| Hà Nội, hỏi bằng chứng nghiên cứu khoa học | 9.554 |

Đây không phải toàn bộ request: còn instruction, lịch sử, profile, few-shot và retry. Batch là một request cho nhiều người; cần báo cả token/request và token/seeker.

Các điểm còn yếu:

- Unknown city nạp mọi lớp; mọi city luôn kèm online. Nên hỏi rõ nơi/hình thức hoặc chỉ dùng lựa chọn thực sự phù hợp, không tự chuyển nhu cầu offline sang online.
- Batch gộp city và câu hỏi thành một knowledge chung; cần nguồn theo thread hoặc batch cùng city/intent, giữ mapping nguồn cho từng người.
- `_class_sections()` bỏ intro, đồng thời làm mất policy quan trọng “có thể tham gia bất kỳ lúc nào”. Phải tách policy dùng chung khỏi ghi chú vận hành.
- Research cắt 6.000 ký tự đầu có thể giữ phần “rung động lên nước” và cắt mất bối cảnh. Nên retrieval theo mục được duyệt, không cắt đầu file tùy độ dài.
- `recent_human_page_examples()` lấy nội dung Page của người khác cùng city, không khử thông tin cá nhân. Xưng hô phải lấy từ chính thread; ví dụ liên thread chỉ để học văn phong sau khi khử tên/SĐT, lịch hẹn và dữ kiện riêng. Địa chỉ lấy từ nguồn lịch đã xác minh.

## 5. Đề xuất theo từng file/thành phần memory

| Thành phần | Đánh giá và kế hoạch |
| --- | --- |
| `mas_strategy.md` | Rút thành policy chăm sóc: mục tiêu, quyền hạn, ma trận quyết định, bằng chứng stage, giới hạn liên hệ, KPI. Chuyển sơ đồ kỹ thuật, SQL và hướng dẫn Telegram sang docs |
| `SOUL.md` | Giữ miễn phí, kiên nhẫn, ngắn gọn; thêm quyền không trả lời, giữ xưng hô của thread, thừa nhận giới hạn. Gỡ mô tả loader 6 file đã lỗi thời. Prompt batch “You are NOT a bot” lệch với SOUL “trợ lý”; nên thống nhất trợ lý soạn cho yogi, không giả định danh tính người thật |
| `lop-hoc.md` | Giữ nội dung dễ đọc; bổ sung class_id, loại lớp, lịch hiệu lực, timezone, verified_at/owner, trạng thái nhận đăng ký. Nghệ An là khóa 8 tuần từ 10/08/2025: không tự biến thành lịch lặp vô hạn. HCM đang gom đăng ký, chưa có buổi cố định. Chuyển QR/CLI và hashtag khỏi context trả lời |
| `su-kien.md` | Tháng 4/2026 vẫn gắn “sắp diễn ra” dù nay tháng 9. Đưa vào archive; mục chỉ có tháng không tự dựng ngày chính xác. events=0 hợp lệ khi không có sự kiện tương lai đủ dữ kiện; không cần tạo event giả để làm route hoạt động |
| `faq.md` | Tách câu trả lời ngắn và phần giải thích dài/trích dẫn. Bảng liên hệ dùng một nguồn. Các câu khẳng định tuyệt đối cần quy trình biên tập trước khi đưa vào câu trả lời |
| `research.md` | Hai tổng quan trùng nhau, thiếu liên kết/DOI và mức bằng chứng; câu “đã được khoa học chứng minh” mạnh hơn SOUL cho phép. Tách thư mục tham khảo khỏi đoạn được duyệt cho seeker. Chưa kiểm chứng các nghiên cứu bên ngoài trong audit này; không kết luận đúng/sai y khoa từ file nội bộ |
| DB, WAL/SHM, backups | Là trạng thái runtime, không phải knowledge để nạp vào LLM. Migration/rollback phải xử lý DB nhất quán khi WAL đang hoạt động; không sửa hay xóa trong lượt này |
| `fb_credential_*.json`, QR assets | Credential là dữ liệu phiên riêng tư; chỉ kiểm kê tên, không đọc/nạp vào prompt. QR là asset tra theo class_id, không phải văn bản chỉ dẫn cho LLM |

Cấu trúc đích đề xuất: giữ SOUL và strategy gọn; `knowledge/` cho FAQ/đoạn nghiên cứu được duyệt; `catalog/` cho lớp, buổi cụ thể, sự kiện và liên hệ; `examples/` cho mẫu đã khử dữ liệu riêng; dữ liệu runtime/credential tách khỏi nguồn retrieval. Đây là sơ đồ tổ chức để triển khai sau, chưa di chuyển file. Không giữ đồng thời hai nguồn lịch có thể sửa độc lập: catalog là nguồn chuẩn, Markdown là bản trình bày hoặc được import có version.

## 6. Lỗ hổng hiện tại cần đưa vào kế hoạch

1. **Closer quá rộng:** `is_closer()` dùng substring và tối đa 12 từ; kiểm tra thuần Python trả `True` cho “Dạ em muốn đổi sang Chủ Nhật”. Bộ lọc có thể bỏ mất yêu cầu đổi buổi. Dùng full-match cho acknowledgment; yêu cầu chưa rõ chuyển review thay vì skip.
2. **Timestamp chưa chắc:** code dùng `datetime.now()` rồi gắn nhãn giờ Việt Nam, chưa bảo đảm timezone của host. NULL time có thể rơi vào reply mới. Không lấy giờ fetch làm bằng chứng tin mới; lưu raw, thời điểm quan sát, độ chắc chắn; unknown → review. 17/09/2026 là **Thứ Năm**, không phải Thứ Tư như ví dụ audit cũ.
3. **Tin ngắn có thời hạn:** “em tới cổng rồi” không còn hành động được sau vài giờ, dù dưới 7 ngày. Thêm thời hạn theo intent/session; LATE không chỉ là gắn lời xin lỗi vào chỉ dẫn hết hiệu lực.
4. **Stage vẫn nằm trong runner:** `evaluate_stage_gate()` còn gọi khi `state.has_phone`. `_has_specific_program()` chỉ tìm từ như “lớp/online”, có thể từ Page; contact fallback cũng quét mọi sender. Chưa đủ bằng chứng seeker chọn lớp cụ thể. Chuyển sang sự kiện tiếp nhận contact/registration đã lưu và attendance xác minh, độc lập với draft.
5. **Banner không chứng minh không đăng ký:** phải kiểm tra cả hồ sơ/lịch sử. `registration_phone` hiện suy từ message rồi prompt khẳng định đã lưu; cần trường `registration_received` có nguồn xác nhận persistence, không suy commit thành công từ regex.
6. **Prompt mâu thuẫn:** batch vừa NO_REPLY sau Page, vừa bảo Page cuối thì follow-up nhẹ. Responder bắt đầu bằng greeting “highest priority” nhưng cũng cho sentinel. Một decision contract chung cho single/batch/regen, ưu tiên NO_REPLY/HANDOVER rõ ràng.
7. **Reply được đề xuất chưa chắc đã được phục vụ:** query unreplied loại thread theo MAX(last_message_seq) của mọi proposal, không phân biệt rejected/failed. Lưu dấu đã xử lý theo message version và trạng thái; reject không được làm câu hỏi biến mất khỏi morning brief.
8. **Trigger chưa bền:** shell chỉ gate trong pipeline/both, mode mas và mas-classify vẫn gọi mỗi chu kỳ. fetch_log cũng được comment fetch dùng chung. Cần cursor inbox/message_id được claim/ack, không chỉ “dòng log mới nhất có count > 0”; tránh bỏ backlog nếu batch chỉ lấy 5 người.
9. **Care cần cách ly và revalidation:** query reminder hiện không lọc page_id/opt-out, cho qua bằng phone dù ngoài stage; mở phiên chưa chứng minh tất cả điều kiện còn hợp lệ. Cần kiểm tra page, quyền liên hệ, lớp/buổi, tin mới và expiry trước soạn/duyệt. Claim phải atomic, retry không tạo hai draft cùng buổi.
10. **HITL thiếu Telegram:** `check_hitl_status()` trả approved khi thiếu message_id. Phải giữ pending/failed notification, có thể duyệt trên web; không tự coi lỗi thông báo là duyệt.

## 7. Thiết kế nhịp chăm sóc đề xuất

08:30 tạo một brief: việc còn chờ người → SLA → điểm danh buổi hôm qua → lớp hôm nay/ngày mai. Tách cảnh báo SLA khỏi LLM; không trì hoãn cảnh báo 2 giờ đến sáng hôm sau. SLA đo từ tin có thông tin đăng ký chưa được người nhận xử lý, không từ tin cuối bất kỳ.

Chọn lớp theo **ngày lịch hôm nay và ngày mai**, tính trong Asia/Ho_Chi_Minh, thay cửa sổ 36h: lúc 08:30, lớp 21:00 ngày mai cách 36,5h sẽ bị bỏ sót. Lớp 05:30/08:30 cần được giới thiệu từ brief hôm trước. Buổi bị hủy/đổi lịch hoặc mở phiên quá muộn phải được kiểm tra lại.

City khớp chỉ là candidate cần chọn lớp, không phải bằng chứng ghi danh đúng lớp. Một người có thể đăng ký nhiều lớp; dùng registration gắn class/session và source_message_id. Không gửi lại lời nhắc cho cùng người/buổi; phân biệt proposed/drafted/sent/skipped để chưa gửi vẫn có thể tiếp tục xử lý.

Điểm danh phải có `unknown / attended / absent / cancelled`, người xác nhận và thời điểm. Chưa được đánh dấu không tự thành no-show; không phụ thuộc việc có phiên nhắc trước đó. Sau xác nhận absent mới xét warm-up sau ba ngày, vẫn qua giới hạn liên hệ chung và bước mở phiên.

## 8. Kế hoạch triển khai tiếp theo (chưa thực hiện)

| Bước | Công việc | Tiêu chí nghiệm thu |
| --- | --- | --- |
| A — Hợp nhất contract | Đồng bộ strategy, UC-05–12, SOUL và prompt; phân biệt received/assigned/attended, NO_REPLY/HANDOVER, mở phiên/duyệt/gửi | Không còn chỉ dẫn DM auto-send; cùng input cho single/batch/regen cùng quyết định; không hỏi lại SĐT đã có |
| B — Sửa gate và evidence | Closer, thời gian/expiry, registration persistence, stage event, message version/cursor; giữ câu hỏi rejected trong brief | Ca Quang Chiến skip; yêu cầu đổi buổi không skip; tin ở cổng cũ không được chỉ đường như đang diễn ra; tạo draft không đổi stage |
| C — Lịch và SLA | Catalog có hiệu lực, buổi cụ thể, registration đúng lớp, SLA clock riêng, brief 08:30 | HCM chờ mở không phát sinh buổi; lớp hết khóa không lặp; tối mai 21h/sáng mai 05:30 đều được xét; tin mới không reset SLA cũ |
| D — Phiên và attendance | Atomic claim, idempotency, page/opt-out/expiry recheck, trạng thái gửi và attendance; kiểm tra dry-run không ghi | Hai worker hoặc retry chỉ tạo một phiên/draft hợp lệ; chưa điểm danh vẫn unknown; không Telegram vẫn không auto-approve |
| E — Knowledge theo nhiệm vụ | Bỏ full bundle ở mọi composer, nguồn theo thread, cùng-thread xưng hô, few-shot đã khử dữ liệu riêng, policy lớp không mất | Không lẫn tên/SĐT/lịch hẹn người khác; không mất policy tham gia bất kỳ lúc nào; đo tokens/request và tokens/seeker thực tế |
| F — Kiểm chứng trước vận hành | Replay dữ liệu đã ẩn danh trên DB tạm, shadow decisions; sau đó pilot vận hành riêng | 0 draft cho banner/closed/expired; 0 draft proactive trước mở phiên; 0 DM tự gửi; đo bỏ sót câu hỏi và thời gian xử lý |

Không cần thêm agent hay đổi model trước khi hoàn thành B–D. Thời lượng chỉ ước tính sau khi phân loại phần code đã có là đạt/chưa đạt; không triển khai lại toàn bộ P0–P3 từ đầu.

KPI ưu tiên: câu hỏi thật bị bỏ sót; thời gian từ đăng ký đến người xử lý; tỷ lệ draft dùng/chỉnh/bỏ theo lý do; nhắc đúng buổi đã gửi; tỷ lệ attendance unknown; token trên ca đã xử lý. Giảm calls/ngày là chỉ số phụ, vì giảm bằng cách bỏ sót seeker không phải thành công. Các mục tiêu giảm 60% hoặc <40 call/ngày của PRD cũ là giả thuyết cần đo trên workload tương đương.

## 9. Tái kiểm chỉ đọc

```sql
SELECT kind, COUNT(*) FROM messages GROUP BY kind;
SELECT status, COUNT(*) FROM action_queue
WHERE queue_type='reply_message' GROUP BY status;
SELECT COUNT(*) FROM messages WHERE message_at IS NULL;
SELECT message_at_approx, COUNT(*) FROM messages GROUP BY message_at_approx;
SELECT COUNT(*) FROM events;
SELECT COUNT(*) FROM attendance;
SELECT COUNT(*) FROM reminder_log;
SELECT date(started_at), route, COUNT(*), SUM(tokens_in), ROUND(AVG(duration_ms))
FROM llm_calls GROUP BY 1,2;
```

Chạy bằng `sqlite3 -readonly memory/agent_memory/frankensqlite.db`. Muốn so sánh lịch sử cần snapshot tương ứng; dữ liệu trace hiện tại không thay thế trace đã dọn. Kiểm tra trong lượt này gồm đọc source, SQL chỉ đọc, đo builder không lấy few-shot DB và gọi hàm closer thuần; không chạy lại toàn bộ test suite hay kiểm chứng hoạt động live.
