# Use cases — chăm sóc seeker và vận hành funnel

**Mã tài liệu:** `doc:usecases-001`  
**Đối tượng:** đội chăm sóc seeker (yogis), người duyệt nội dung, điều phối viên
và người vận hành hệ thống.  
**Cơ sở:** README, tài liệu kiến trúc hợp nhất và các audit trong repository,
đối chiếu vào ngày 2026-09-14.

## Mục đích và phạm vi

Funnel Tracking giúp đội Sahaja Yoga Việt Nam thu nhận tương tác Facebook,
nhìn thấy hành trình của một seeker, và nhận các đề xuất chăm sóc có kiểm soát.
Nó không phải là công cụ tự động gửi mọi tin nhắn. Đặc biệt, phản hồi DM do AI
soạn phải được con người xem lại và tự gửi trên Facebook.

Các vai trò dùng trong tài liệu:

| Vai trò | Nhu cầu chính |
| --- | --- |
| **Seeker** | Hỏi về thiền, lớp học hoặc sự kiện qua Facebook; nhận được hỗ trợ phù hợp. |
| **Yogi chăm sóc** | Xem lịch sử, kiểm tra thông tin và trả lời seeker một cách có ngữ cảnh. |
| **Người duyệt** | Duyệt hoặc từ chối từng đề xuất outbound do MAS tạo. |
| **Điều phối viên** | Theo dõi funnel, chương trình/sự kiện và kết quả chăm sóc. |
| **Người vận hành** | Cấu hình quyền Facebook/Telegram, đồng bộ dữ liệu và chạy scheduler/CLI. |

### Các ràng buộc quan trọng

- Dữ liệu DM và comment được lưu riêng rồi hiển thị như danh sách seeker hợp
  nhất. Một người chỉ mới comment chưa có kênh DM để hệ thống gửi tin riêng.
- Quyết định stage không hoàn toàn tự động: có signal tự động cho tương tác và
  thông tin đăng ký, còn các bước sang học sâu hơn cần người phụ trách xác
  nhận.
- Hàng đợi hành động bảo toàn thứ tự FIFO trong từng loại. Một mục chưa được
  quyết định ở đầu hàng đợi sẽ chặn các mục sau của cùng hàng.
- Scheduler và tích hợp bên ngoài chỉ hoạt động khi credential, phiên Facebook
  CDP và Telegram được cấu hình. Tài liệu/mã nguồn không chứng minh các dịch vụ
  này đang chạy ở môi trường thực.

## Bản đồ use case

| Mã | Use case | Vai trò chính | Kết quả mong muốn |
| --- | --- | --- | --- |
| UC-01 | Đồng bộ hội thoại Facebook | Người vận hành | DM, seeker và lịch sử mới có trong CRM. |
| UC-02 | Đồng bộ comment bài viết | Người vận hành | Comment và người bình luận có trong CRM. |
| UC-03 | Theo dõi funnel | Điều phối viên | Có cái nhìn tổng quan về nguồn và mức độ tương tác. |
| UC-04 | Tìm hiểu một seeker | Yogi chăm sóc | Có đủ bối cảnh để chăm sóc đúng người. |
| UC-05 | Xử lý DM mới bằng bản nháp AI | Yogi chăm sóc | Có bản nháp an toàn để người thật kiểm tra và gửi. |
| UC-06 | Duyệt đề xuất trong hàng đợi | Người duyệt | Chỉ hành động phù hợp mới được xếp thực thi. |
| UC-07 | Chăm sóc lại seeker im lặng | Người duyệt, Yogi chăm sóc | Có đề xuất đúng nhịp, không làm phiền quá mức. |
| UC-08 | Mời seeker tham gia sự kiện theo khu vực | Điều phối viên, Người duyệt | Đề xuất sự kiện phù hợp thành phố và lịch sử quan tâm. |
| UC-09 | Theo dõi hành trình và chuyển stage | Điều phối viên | Stage phản ánh bằng chứng, không chỉ là suy đoán của AI. |
| UC-10 | Xử lý ngoại lệ và phản hồi không thuộc phạm vi | Yogi chăm sóc | Không gửi nội dung thiếu an toàn; ca việc được escalated cho người thật. |

---

## UC-01 — Đồng bộ hội thoại Facebook

**Mục tiêu:** đưa các tin nhắn DM gần đây vào CRM để đội chăm sóc có dữ liệu
mới nhất.

**Tác nhân chính:** Người vận hành.  
**Kích hoạt:** lịch scheduler hoặc một lần chạy thủ công.  
**Điều kiện trước:** phiên Facebook Business Inbox được cấp quyền qua CDP; có
Page ID và dữ liệu cấu hình hợp lệ.

**Luồng chính:**

1. Người vận hành chạy trình fetch với khoảng thời gian và giới hạn thread phù hợp.
2. Hệ thống mở Business Inbox, cuộn danh sách hội thoại, đọc các thread đã phát
   hiện và chuẩn hóa tin nhắn, thời gian, nguồn quảng cáo và thông tin liên hệ.
3. Hệ thống lưu thread, message và hồ sơ DM vào FrankenSQLite; dữ liệu trùng
   được tránh theo khóa của message.
4. Dashboard/CRM đọc dữ liệu mới để người dùng xem và xử lý tiếp.

**Kết quả:** danh sách DM và mốc `last_interaction` phản ánh nội dung mới từ
khách; lần đồng bộ không có nội dung mới chỉ cập nhật trạng thái đồng bộ.

**Ngoại lệ:** nếu không đăng nhập/không được cấp quyền Facebook hoặc giao diện
Facebook thay đổi, phiên đồng bộ thất bại và người vận hành cần kiểm tra phiên
CDP/snapshot trước khi chạy lại. Cache một giờ có thể làm lần chạy không fetch
lại nếu không yêu cầu refresh.

---

## UC-02 — Đồng bộ comment bài viết

**Mục tiêu:** biến tương tác công khai thành lead có thể theo dõi.

**Tác nhân chính:** Người vận hành.  
**Kích hoạt:** chạy fetch comment theo lịch hoặc thủ công.

**Luồng chính:**

1. Hệ thống duyệt các post trong khoảng thời gian chọn, rồi lấy comment/reply
   từ từng post.
2. Hệ thống lưu post, comment và `comment_users`, đồng thời nhận diện thông tin
   liên hệ, thành phố và stage khi có đủ dữ liệu.
3. Điều phối viên thấy commenter trong CRM, biểu đồ mạng và dashboard.
4. Yogi chăm sóc có thể xem comment, post gốc và profile Facebook để mời người
   đó inbox theo cách thủ công khi phù hợp.

**Kết quả:** interaction công khai được ghi nhận như touch-point trong hành
trình seeker.

**Ranh giới:** comment-only lead hiện không có delivery channel riêng. Các
route warm-up/event có thể phát hiện lead này nhưng phải chặn gửi DM tự động;
không coi đó là thất bại của use case.

---

## UC-03 — Theo dõi funnel và nguồn tương tác

**Mục tiêu:** điều phối viên hiểu khối lượng và chất lượng đầu vào để phân bổ
nguồn lực chăm sóc.

**Tác nhân chính:** Điều phối viên.  
**Điều kiện trước:** dữ liệu đã được đồng bộ vào cơ sở dữ liệu.

**Luồng chính:**

1. Điều phối viên mở trang Dashboard.
2. Họ xem DM users, total contacts, tổng message, post, comment và commenter.
3. Họ xem phân bố seeker theo journey để nhận ra chỗ funnel cần quan tâm.
4. Khi cần tìm nguyên nhân, họ đi tới Seekers, Network Graph hoặc Journey.

**Kết quả:** một ảnh chụp vận hành của dữ liệu hiện có, không phải chỉ số chuyển
đổi đã được hiệu chỉnh hoàn chỉnh.

**Lưu ý hiện tại:** một số số đếm stage trên dashboard vẫn là biểu diễn đơn giản
và không nên được dùng một mình để đánh giá hiệu quả chương trình hay kết luận
về toàn bộ các stage cao.

---

## UC-04 — Tìm, kiểm tra và chuẩn bị chăm sóc một seeker

**Mục tiêu:** yogi có bối cảnh đầy đủ trước khi liên hệ hoặc trả lời.

**Tác nhân chính:** Yogi chăm sóc.

**Luồng chính:**

1. Yogi mở **Seekers**, tìm theo tên, thành phố, số điện thoại hoặc email và
   sắp xếp theo tương tác gần nhất.
2. Yogi mở hàng của seeker hoặc trang chi tiết để xem nguồn (DM/comment), city,
   stage, thông tin liên hệ, lịch sử DM/comment và nguồn quảng cáo nếu có.
3. Yogi dùng liên kết Facebook profile hoặc Business Inbox để kiểm tra bối cảnh
   gốc khi cần.
4. Yogi chọn cách hỗ trợ phù hợp: trả lời câu hỏi, xác nhận thông tin đăng ký,
   mời inbox, hoặc chuyển ca cho người phụ trách.

**Kết quả:** quyết định chăm sóc dựa trên lịch sử thay vì chỉ tin nhắn cuối.

**Bảo mật và riêng tư:** chỉ dùng dữ liệu liên hệ cho hoạt động chăm sóc seeker
được ủy quyền; không sao chép credential hay URL danh mục lớp học nội bộ vào
ghi chú công khai.

---

## UC-05 — Xử lý DM mới bằng bản nháp AI

**Mục tiêu:** seeker nhận được phản hồi nhanh, nhất quán và có người chịu trách
nhiệm kiểm tra trước khi gửi.

**Tác nhân chính:** Yogi chăm sóc; hệ thống MAS hỗ trợ.  
**Kích hoạt:** có customer message mới trong một thread chưa được acknowledge.

**Luồng chính:**

1. Hệ thống lấy thread, lịch sử hội thoại, hồ sơ seeker và knowledge context về
   lớp/sự kiện.
2. `MessageClassifier` phân loại ý định; `Responder` soạn nội dung, sau đó hệ
   thống sanitize nội dung trước khi đưa ra bản nháp.
3. Bản nháp được ghi audit theo đúng customer turn để lần chạy sau không tạo lặp
   lại cho cùng một tin nhắn.
4. Yogi mở thread trong Facebook, đọc/chỉnh sửa bản nháp và **tự bấm Send** nếu
   nội dung phù hợp.
5. Nếu seeker gửi tin nhắn mới, thread lại trở thành actionable cho một bản
   nháp tiếp theo.

**Kết quả:** một bản nháp đã được chuẩn bị, hoặc `no_reply`/escalation nếu hệ
thống không nên trả lời.

**Quy tắc an toàn bắt buộc:** automation không được nhấn Enter, click Send hay
gọi một cơ chế gửi tương đương cho DM inbox — kể cả khi chạy với `--live`.

**Tình trạng cần lưu ý:** đường xử lý từng thread đã có hành vi type-only; đường
batch hiện có thể ghi `drafted` cho một đề xuất Telegram trước khi bản nháp đã
được gõ vào composer. Người vận hành phải coi trạng thái này là *đề xuất cần
kiểm tra*, không phải bằng chứng tin đã được gõ hoặc gửi, cho tới khi điểm này
được khắc phục.

---

## UC-06 — Duyệt hoặc từ chối đề xuất outbound

**Mục tiêu:** mọi hành động do MAS đề xuất phải có quyết định của con người.

**Tác nhân chính:** Người duyệt.

**Luồng chính:**

1. MAS tạo đề xuất (reply/reaction/comment/proactive message) và đưa vào
   Telegram HITL hoặc trang **Human approval queues**.
2. Người duyệt đọc target, nội dung hoặc reaction và bối cảnh cần thiết.
3. Người duyệt chọn **Duyệt** hoặc **Từ chối**. Quyết định được lưu cùng nguồn
   phê duyệt.
4. Worker chỉ có thể claim mục đã duyệt ở đầu hàng đợi tương ứng; mục bị từ chối
   không được thực thi.
5. Người duyệt kiểm tra trạng thái `approved`, `executing`, `executed` hoặc
   `failed` để xử lý tiếp khi có lỗi.

**Kết quả:** có audit trail của đề xuất, quyết định và kết quả thực thi.

**Ranh giới inbox:** phê duyệt một đề xuất inbox không cấp quyền cho automation
gửi DM. Yogi vẫn phải review và gửi thủ công trong Facebook. Tài liệu kiến trúc
ghi nhận một nhánh scheduler cũ có thể vi phạm ranh giới này; không dùng nhánh
đó cho inbox cho đến khi đã bị loại bỏ/khóa ở mức code.

---

## UC-07 — Chăm sóc lại seeker im lặng

**Mục tiêu:** gợi lại sự quan tâm mà không spam seeker hoặc cạnh tranh với một
phản hồi DM đang chờ.

**Tác nhân chính:** Hệ thống đề xuất; Người duyệt quyết định; Yogi chăm sóc
theo dõi phản hồi.

**Luồng chính:**

1. Scheduler tìm seeker DM không tương tác trong khoảng thời gian phù hợp và
   xác định temperature từ stage, thời gian im lặng và trạng thái vận hành.
2. Decision core chặn spam, unsubscribed, customer turn còn chờ phản hồi, một
   proactive touch gần đây và các trường hợp không đủ điều kiện.
3. Nếu đủ điều kiện, hệ thống tạo một nội dung warm-up được cá nhân hóa (ADK có
   fallback template), ghi quyết định/audit và tạo đề xuất HITL.
4. Người duyệt kiểm tra, phê duyệt hoặc từ chối. Với luồng được hỗ trợ, worker
   chỉ xử lý mục đã duyệt.
5. Khi seeker phản hồi, đội chăm sóc chuyển sang UC-05 và ưu tiên phản hồi
   reactive hơn proactive.

**Kết quả:** một chuỗi chăm sóc có nhịp, với giới hạn live warm-up tối đa một
lần mỗi thread trong bảy ngày và tránh chồng hành động trong 24 giờ.

**Ngoại lệ/giới hạn:** chuỗi cool ba bước và decision logging đã có, nhưng luồng
cold → dormant và các QA gate về cá nhân hóa/tone chưa hoàn chỉnh. Comment-only
lead bị chặn vì không có kênh gửi.

---

## UC-08 — Mời tham gia sự kiện theo khu vực và sự quan tâm

**Mục tiêu:** điều phối viên tiếp cận seeker phù hợp với một sự kiện mà không
gửi lặp hoặc gửi sai khu vực.

**Tác nhân chính:** Điều phối viên; hệ thống MAS; Người duyệt.

**Luồng chính:**

1. Điều phối viên tạo/cập nhật sự kiện có thành phố, thời gian và mô tả.
2. Hệ thống chọn seeker cùng thành phố, chuẩn hóa stage và ưu tiên candidate;
   với DM, nội dung lịch sử còn được chấm điểm mức phù hợp về sở thích.
3. Decision core chặn spam/unsubscribed, DM còn pending, proactive touch quá
gần, và giới hạn sự kiện quý cho seeker dormant.
4. Hệ thống tạo đề xuất mời, lưu quyết định/campaign và gửi vào luồng duyệt.
5. Người duyệt quyết định hành động, sau đó theo dõi kết quả và phản hồi.

**Kết quả:** chiến dịch có thể truy vết theo event và seeker; một live campaign
đã có không được lặp lại cho cùng event/thread.

**Giới hạn hiện tại:** city match có cho DM và comment, nhưng interest scoring
chỉ có cho DM và comment-user luôn bị chặn delivery. Stage targeting trong code
cũng rộng hơn playbook ở một số trường hợp; người điều phối nên review proposal
thay vì coi ranking là quyết định cuối.

---

## UC-09 — Theo dõi hành trình và chuyển stage

**Mục tiêu:** đội hiểu seeker đang ở đâu trong hành trình từ tương tác đầu tiên
tới thực hành Sahaja Yoga ổn định.

**Tác nhân chính:** Điều phối viên và Yogi chăm sóc.

**Luồng chính:**

1. Đội mở trang **Journey** để xem các stage và trigger chuyển tiếp.
2. Khi có touch-point, hệ thống có thể nâng từ `User/Intake` sang `Seeker`.
3. Khi có thông tin liên hệ hợp lệ **và** bằng chứng về chương trình cụ thể,
   hệ thống có thể nâng từ `Seeker` sang `Seeker_Public_Program`.
4. Đội xác minh attendance/kết quả lớp và thực hiện các chuyển stage cao hơn
   theo quy trình người phụ trách.
5. Đội dùng stage cùng với temperature và lịch sử để chọn cách chăm sóc tiếp.

**Kết quả:** journey là công cụ chung để lập kế hoạch chăm sóc, gồm các stage:
`User → Seeker → Seeker_Public_Program → Seeker_18_Weeks → Seed → Sahaja_Yogi
→ Sahaja_Yogi_Dedicated → Sahaja_Mahayogi`.

**Ranh giới:** không tự động suy luận việc hoàn thành lớp 18 tuần, thực hành ba
tháng hay năng lực hướng dẫn từ một message đơn lẻ. Những quyết định này cần
người phụ trách xác nhận và lưu bằng chứng phù hợp.

---

## UC-10 — Xử lý spam, nội dung ngoài phạm vi và ca cần người thật

**Mục tiêu:** bảo vệ seeker và đội chăm sóc khi AI không nên đưa ra câu trả lời
tự động.

**Tác nhân chính:** Hệ thống MAS; Yogi chăm sóc.

**Luồng chính:**

1. Classifier nhận diện spam, sale, nội dung không thuộc phạm vi hoặc câu hỏi
   cần đánh giá của con người.
2. Hệ thống không type/sent câu trả lời không an toàn; nếu có, nó tạo escalation
   trên Telegram để đội xem xét.
3. Yogi kiểm tra lịch sử và quyết định: trả lời thủ công, chuyển người phụ trách,
   đánh dấu spam/unsubscribed hoặc không phản hồi.
4. Trạng thái hard stop được tôn trọng bởi các route proactive sau đó.

**Kết quả:** không có “lời hứa” hay nội dung suy luận nội bộ bị gửi cho seeker;
quyết định nhạy cảm luôn thuộc con người.

## Tiêu chí hoàn thành từ góc nhìn người dùng

- Sau khi đồng bộ, một yogi có thể tìm được seeker và hiểu các touch-point liên
  quan mà không phải dò thủ công nhiều màn hình Facebook.
- Mỗi đề xuất outbound có target, nội dung/hành động, trạng thái và dấu vết phê
  duyệt rõ ràng.
- Một customer turn đã có draft không bị xử lý lặp, nhưng tin nhắn khách mới mở
  lại việc cần chăm sóc.
- Không có DM inbox nào được automation tự gửi. Bước Send là hành động chủ động
  của yogi trên Facebook.
- Lead không có kênh liên lạc không bị hệ thống giả định là có thể nhắn riêng.

## Tài liệu liên quan

- [README](../../README.md) — tổng quan, setup và các bề mặt sản phẩm.
- [Kiến trúc hợp nhất](../architect/architecture.md) — ranh giới runtime, an
  toàn inbox, trạng thái triển khai.
- [MAS strategy](../../memory/mas_strategy.md) — playbook hành trình và chăm
  sóc theo stage; một số nội dung là target strategy, không phải cam kết runtime.
- [QA audit](../report/qa-report.md) — các sai khác đã biết giữa docs, tests và
  implementation theo thời điểm audit.
