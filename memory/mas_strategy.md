# MAS Strategy — Seeker Journey & Care Playbook

**Universal ID:** `doc:mas-strategy-001`

**Cập nhật:** 17/09/2026

**Phạm vi:** chính sách chăm sóc đã thống nhất; không phải xác nhận mọi nhánh runtime đã thực thi đúng.

MAS giúp yogi nhận ra nhu cầu còn chờ, chuẩn bị câu trả lời và chăm sóc đúng lịch.
Mục tiêu là seeker được hỗ trợ phù hợp, không phải tăng số tin nhắn hoặc đẩy stage.
Giữ giọng ấm áp, kiên nhẫn, ngắn gọn, không gây áp lực; các lớp Sahaja Yoga miễn phí.

Chi tiết kỹ thuật được tách sang [MAS care execution contract](../docs/architect/mas-care-execution-contract.md).
Các khoảng trống implementation và kế hoạch nghiệm thu nằm trong
[PRD §7](../docs/PRDs/mas-time-aware-care-plan.md#7-kế-hoạch-bổ-sung-sau-rà-soát-strategymemory--chỉ-lập-kế-hoạch).

## 1. Nguyên tắc bắt buộc

- **Reactive:** chỉ soạn draft khi có nhu cầu chưa được đáp ứng và còn ý nghĩa để trả lời.
- **Proactive:** chỉ tạo digest/candidate lên Telegram hoặc `/queues`; sau khi quản trị viên **Mở phiên** mới sinh draft cho seeker.
- **Gửi DM:** yogi kiểm tra và tự gửi trên Facebook. Mở phiên, duyệt draft hoặc LIKE Telegram không cấp quyền tự gửi DM.
- **Không trả lời** là kết quả hợp lệ. Không bịa câu đáp cho banner, lời kết đã đóng hoặc yêu cầu hết hạn.
- **Được xác nhận đã nhận đăng ký** khi thông tin đăng ký từ tin khách thật đã được lưu vào hồ sơ `/seekers`. Không cần yogi duyệt việc tiếp nhận này.
- Tiếp nhận thông tin không chứng minh đã xếp lớp, giữ chỗ hoặc tham dự. SĐT xuất hiện trong tin Page hay quảng cáo không phải bằng chứng khách đăng ký.
- Stage thay đổi theo bằng chứng riêng; tạo draft, mở phiên hoặc duyệt nội dung không làm seeker tiến stage.
- Tôn trọng lựa chọn ngừng nhận tin. Không dùng temperature, event mới hay duyệt phiên để vượt qua opt-out.
- Không suy “không còn quan tâm” hay “vắng học” từ việc seeker im lặng.

## 2. Bốn trục để quyết định chăm sóc

| Trục | Câu hỏi cần trả lời | Ví dụ |
| --- | --- | --- |
| Journey stage | Seeker đang ở đâu trong hành trình đã được xác minh? | Seeker, Registered, Deep Learner |
| Conversation state | Còn yêu cầu nào chưa được phục vụ? | open_question, already_answered, stale, needs_review |
| Registration / participation | Đã nhận thông tin, chọn lớp/buổi nào, đã tham dự chưa? | received, assigned, attendance unknown/attended/absent/cancelled |
| Contact preference | Được liên hệ qua kênh nào và trong phạm vi nào? | cho phép nhắc lớp, ngừng proactive, cần xác minh |

Đây là khái niệm nghiệp vụ; tên trường/schema cụ thể thuộc tài liệu kỹ thuật.
`unsubscribed` là quyền liên hệ, không phải mức nhiệt độ.
Một người có thể đăng ký nhiều lớp; city không thay thế bằng chứng chọn lớp.
Follower và Curious cùng dùng `Seeker` trong DB hiện tại, cần intent/touch-point để phân biệt.

## 3. Customer Journey và chiến lược theo stage

User → Follower → Curious Seeker → Registered → Deep Learner → Sahaja Yogi.

Journey giúp tổ chức chăm sóc, không phải thang điểm đánh giá phẩm chất hay tâm thức.

| Stage | DB / Journey mapping | Bằng chứng và cách chăm sóc |
| --- | --- | --- |
| **0 — User** | `User`; dữ liệu cũ có thể mang `Intake` | Chưa có tương tác đủ xác định. Nội dung chung của Page; không proactive cá nhân |
| **1 — Follower / đã tương tác** | `Seeker` | Có touch-point thật. Follow chỉ được khẳng định nếu có tín hiệu follow; DM/comment không chứng minh follow hoặc đã vào Zalo. Có thể đề xuất reaction/comment phù hợp |
| **2 — Curious Seeker** | `Seeker` | Chủ động hỏi lớp/chương trình. Ưu tiên giải đáp đúng city, hình thức và nhu cầu; hỏi phần còn thiếu, không hỏi lại dữ liệu đã có |
| **3 — Registered** | `Seeker_Public_Program` | Có thông tin liên hệ hợp lệ đã lưu và bằng chứng khách chọn lớp/chương trình cụ thể. Ưu tiên xác nhận tiếp nhận, lịch hẹn, nhắc đúng buổi và điểm danh |
| **4 — Deep Learner** | `Seeker_18_Weeks` | Có bằng chứng học nhập môn và người phụ trách xác nhận tham gia khóa sâu. Chăm sóc theo lịch học/attendance, không theo im lặng trong inbox |
| **5 — Sahaja Yogi** | `Seed` → `Sahaja_Yogi` → `Sahaja_Yogi_Dedicated` → `Sahaja_Mahayogi` | Thực hành, sinh hoạt và vai trò do người phụ trách xác nhận. Tri ân, cộng đồng, hỗ trợ người mới theo sự tự nguyện; không chạy chuỗi warm-up đại trà |

### Curious Seeker: thông tin đúng nhu cầu

Seeker có thể quan tâm chương trình cộng đồng/âm nhạc, lớp nhập môn offline,
lớp online hoặc sinh hoạt lâu dài. Lấy lịch còn hiệu lực từ nguồn lớp/sự kiện,
không cố định ngày khai giảng hoặc tần suất sự kiện trong strategy.
Nếu cần lớp offline mà chưa có lịch xác minh, trình bày đúng tình trạng chờ mở;
chỉ giới thiệu online như lựa chọn phù hợp, không tự coi đó là lớp khách chọn.

Comment-only lead chưa có kênh DM hợp lệ không được đưa vào phiên nhắn riêng.
Lời mời sự kiện cần phù hợp nhu cầu và quyền liên hệ, kể cả sự kiện online.

### Registered: tiếp nhận khác với xếp lớp

Có thể viết: “Dạ CLB đã nhận được thông tin đăng ký của chú rồi ạ” khi hồ sơ
đăng ký đã lưu. Nếu chưa rõ lớp, hỏi phần còn thiếu; không gán lịch từ city.
Không xác nhận lại máy móc nếu yogi đã đáp và hội thoại đã kết thúc.

Banner ở cuối thread không kích hoạt trả lời. Đồng thời, banner không phủ nhận
đăng ký có thật trước đó; luôn xét hồ sơ và lịch sử liên quan.

### Stage gates

| Gate | Điều kiện | Khi thiếu bằng chứng |
| --- | --- | --- |
| G1: User → đã tương tác | Touch-point thật, có nguồn | Giữ trạng thái; banner không tính |
| G2: đã tương tác → Curious | Seeker chủ động hỏi hoặc bày tỏ nhu cầu | Không suy từ một reaction; ghi intent riêng |
| G3: Curious → Registered | Contact hợp lệ đã lưu + khách chọn lớp/event cụ thể, có nguồn | Có thể xác nhận đã nhận thông tin, nhưng chưa nâng stage nếu chưa rõ chương trình |
| G4: Registered → Deep | Với lớp nhập môn 4 buổi: attendance ≥3/4; người phụ trách xác nhận tham gia khóa sâu | Giữ stage; chương trình khác cần tiêu chí hoàn thành tương ứng được xác nhận |
| G5: Deep → Seed/Yogi và các vai trò sau | Hoàn thành chương trình 18 tuần, quá trình thực hành và xác nhận của người phụ trách; mốc ≥3 tháng là bằng chứng cần xác minh cho bước phù hợp | Không tự nâng theo số ngày trôi qua, tin nhắn hay suy đoán năng lực hướng dẫn |

Mỗi chuyển stage cần nguồn bằng chứng, thời điểm và người/hệ thống xác nhận.
Không hạ stage chỉ vì ngừng nhắn tin; điều chỉnh sai dữ liệu phải có audit trail.

## 4. Quyết định trước khi gọi LLM

Ưu tiên: kiểm tra kênh/quyền liên hệ → nhu cầu còn chờ và thời gian → đăng ký/lịch
hẹn → SLA/attendance → candidate warm-up/event nếu có lý do cụ thể.

| Tình huống | Quyết định |
| --- | --- |
| Banner hệ thống / ad source | Bỏ khỏi tin khách cần đáp; không gọi LLM chỉ vì banner |
| Reaction/sticker thuần xác nhận | Không ép soạn reply; có thể đề xuất reaction phù hợp nếu còn mới |
| Attachment chưa hiểu hoặc nội dung không rõ | Review/handover; không mặc định là lời kết |
| Câu cảm ơn/xác nhận thuần túy, đã được phục vụ | NO_REPLY; không tạo thêm DM chỉ để kết thúc lần nữa |
| Page đã trả lời đầy đủ yêu cầu | NO_REPLY; không coi mọi tin Page là chứng minh mọi câu hỏi đã được xử lý |
| Auto_Page trả lời nhưng nhu cầu còn chờ | Vẫn xét nhu cầu thật; bot acknowledgement không thay người xử lý |
| Câu hỏi ≤24h, thời gian chắc chắn, còn hiệu lực | Soạn draft reactive |
| Câu hỏi >24h đến 7 ngày, còn hiệu lực | LATE: xin lỗi ngắn, trả lời theo hiện tại |
| Tin >7 ngày | Không reply như tin mới; giữ việc chưa xử lý trong brief, chỉ xét warm-up candidate nếu phù hợp |
| Yêu cầu đã hết hạn, dù chưa đủ 24h/7 ngày | Không dùng chỉ dẫn cũ; handover hoặc chuẩn bị lời hỏi lại phù hợp khi người xử lý mở ca |
| Không xác định chắc thời gian hoặc nhu cầu | Review; không lấy giờ fetch làm giờ khách nhắn |
| Spam / ngoài phạm vi | OUT_OF_SCOPE; chuyển đội xem khi cần, không ép draft |
| Câu hỏi nâng cao/nhạy cảm cần người phụ trách | HANDOVER nội bộ, nêu lý do và người cần xử lý; không hứa đã chuyển nếu chưa có việc chuyển thực tế |

NO_REPLY/LATE/HANDOVER là quyết định nghiệp vụ; format output và mapping hiện hành
nằm trong tài liệu kỹ thuật. Áp dụng nhất quán cho single, batch và sửa draft.

“Dạ em muốn đổi sang Chủ Nhật” là yêu cầu đổi lịch, không phải closer.
“Em tới cổng rồi” có thời hạn theo buổi học; không trả lời “em xuống tầng trệt”
nhiều ngày sau chỉ vì đã thêm lời xin lỗi.
Một draft bị từ chối không chứng minh câu hỏi đã được phục vụ.

## 5. Nhịp chăm sóc hằng ngày

### Brief và nhắc lịch: 08:30 giờ Việt Nam

Chạy một lần/ngày trong khung **08:00–09:00**, mặc định **08:30 Asia/Ho_Chi_Minh**.
Brief sắp ưu tiên: việc thật còn chờ → đăng ký cần người xử lý → điểm danh buổi
hôm qua → nhắc lớp hôm nay/ngày mai. Digest không cần LLM.

- Xét các buổi chưa bắt đầu của **hôm nay và ngày mai** theo ngày lịch, không dùng cửa sổ 36h.
- Lớp sáng sớm được xét từ hôm trước; tối mai 21h vẫn thuộc danh sách.
- Chọn người có đăng ký đúng lớp/buổi, quyền liên hệ phù hợp và chưa được nhắc buổi đó.
- Stage Registered/Deep và tương tác trong 21 ngày là bộ lọc candidate mặc định. Người có lịch hẹn hoặc attendance đang hoạt động vẫn cần được xem xét dù ít nhắn inbox.
- City khớp nhưng chưa rõ lớp: đưa vào danh sách cần xác minh, không ghi “đã ghi danh lớp X”.
- Digest cho biết buổi, số người, bằng chứng và phần còn thiếu. Người phụ trách chọn **Mở phiên / Bỏ qua**.
- Sau mở phiên mới sinh draft từng người. Kiểm tra lại lịch, hủy/đổi buổi, tin mới, quyền liên hệ và thời hạn trước soạn/duyệt.
- Tối đa một lời nhắc đã gửi cho mỗi người/buổi. Draft chưa gửi có thể tiếp tục xử lý, không bị coi là đã nhắc.

Ví dụ: sáng thứ Bảy, đưa lịch Chủ Nhật 14:30 của chú Chiến vào digest nếu đăng ký
và buổi học còn hợp lệ; không khơi lại lời cảm ơn đã được yogi đáp.

### SLA đăng ký chưa được người xử lý

Thông tin đăng ký có SĐT đã lưu nhưng **quá 2 giờ** chưa có người xác nhận/xử lý:
cảnh báo nội bộ Telegram/`/queues`, không tự soạn DM để giả lập phản hồi.
Đo từ tin đăng ký chưa được xử lý, không reset theo tin cuối bất kỳ.
Auto_Page không tính là người thật; draft hoặc approve cũng không đóng SLA.
Cảnh báo chạy trong ngày, không chờ đến brief sáng; gộp và chống cảnh báo lặp.

### Sau buổi học

Sáng hôm sau, tạo checklist từ danh sách hẹn/đăng ký, kể cả chưa có phiên nhắc.
Attendance gồm **unknown / attended / absent / cancelled**, có người xác nhận và thời điểm.

- Chưa đánh dấu là unknown, không tự thành absent.
- Attended: có thể mở phiên cảm ơn/hẹn buổi tiếp theo; chỉ dùng làm bằng chứng stage phù hợp.
- Absent đã xác nhận: sau 3 ngày có thể đưa vào candidate chăm sóc lại; vẫn cần mở phiên và qua giới hạn liên hệ.
- Cancelled: cập nhật lịch và nhu cầu thực tế, không gán no-show cho seeker.

## 6. Temperature và warm-up

Temperature là tín hiệu ưu tiên để người phụ trách rà soát, không tự kích hoạt
tin nhắn hay thay đổi stage. Thời gian im lặng chỉ có nghĩa khi đặt cạnh lịch
hẹn, attendance, nhu cầu chưa xong và lần liên hệ thực tế.

| Mức | Diễn giải | Cách xử lý |
| --- | --- | --- |
| Hot | Đang trao đổi hoặc có nhu cầu gần | Ưu tiên giải đáp; không phải lý do gửi thêm |
| Warm | Có quan tâm gần đây, có cơ hội hỗ trợ cụ thể | Có thể tạo candidate nếu chưa có việc reactive cần giải quyết |
| Cool | Chưa phản hồi lời liên hệ đã gửi, không có lịch hẹn/attendance giải thích | Người phụ trách xem lý do và giá trị của lần liên hệ tiếp |
| Cold | Nhiều lần liên hệ phù hợp không nhận phản hồi | Giảm liên hệ, ưu tiên dừng; không bắt buộc “last nudge” |
| Dormant | Tạm ngừng chăm sóc chủ động | Không tự gửi event mỗi quý; chỉ xét mở lại khi có căn cứ đồng ý/nhu cầu và quyết định riêng |

Có thể gắn nhãn cần rà soát khi Follower/Curious im lặng 3–7 hoặc trên 14 ngày,
nhưng không tự kết luận mất quan tâm. Với Registered/Deep/Yogi, lịch hẹn và việc
tham gia quan trọng hơn tuổi tin nhắn. Seeker chủ động quay lại mở việc reactive;
không tự reset quyền ngừng proactive.

### Giới hạn liên hệ dùng chung

- Mặc định tối đa **một DM proactive đã gửi / 7 ngày / seeker**, cộng chung warm-up, event, nhắc lớp và hậu buổi học; không chia budget theo route.
- Khi nhiều mục cùng đủ điều kiện, ưu tiên nhu cầu/lịch hẹn cụ thể, gộp nội dung phù hợp hoặc bỏ mục ít giá trị.
- Không có ngoại lệ ngầm vì lớp sắp diễn ra. Thay đổi lịch cấp thiết được đưa cho yogi xử lý trực tiếp trong bối cảnh hẹn đã có, không dùng làm cách lách budget chiến dịch.
- Không warm-up khi câu hỏi reactive còn chờ, không có kênh hợp lệ, opt-out, hoặc dormant chưa được mở lại.
- Bỏ chuỗi cứng +3/+5 ngày. Nếu dùng chuỗi Cool, tối đa ba lần liên hệ, mỗi lần phải có lý do mới, mở phiên và cách lần proactive trước ít nhất 7 ngày; không bắt buộc đi hết chuỗi.
- Cold không tăng tần suất. Nếu người phụ trách chọn một lời chào cuối, sau đó dừng ít nhất 14 ngày; nếu không phản hồi thì chuyển dormant.
- Chỉ tăng số lần liên hệ/cool_step khi có bằng chứng đã gửi; proposed/drafted/approved không tính.
- Không rõ tin đã gửi hay chưa: xác minh, không tự gửi lại. Opt-out dừng proactive ngay, kể cả draft đã được duyệt trước đó.

## 7. Event và ma trận route

Event phải còn hiệu lực, có lịch đủ chắc, phù hợp nhu cầu và kênh liên hệ.
Cùng city hoặc event online chỉ là điều kiện chọn candidate, không phải đồng ý nhận tin.
Không tạo sự kiện tương lai từ một lịch cũ chỉ để route có dữ liệu.

| Route | Đầu ra trước quyết định của người | Khi được soạn/thực hiện |
| --- | --- | --- |
| Inbox reactive | Draft cho yêu cầu còn chờ, hoặc NO_REPLY/HANDOVER | Qua gate; yogi review và gửi DM tay |
| React / comment | Đề xuất reaction/nội dung và target cụ thể | Qua duyệt hành động riêng; không suy quyền gửi DM từ duyệt này |
| Warm-up | Candidate và lý do chăm sóc | Sau mở phiên; draft → review → gửi tay |
| Event | Digest sự kiện và target phù hợp | Sau mở phiên; tối đa một lời mời đã gửi/người/event, đồng thời qua budget chung |
| Class reminder | Digest buổi học hôm nay/ngày mai | Sau mở phiên; tối đa một lời nhắc đã gửi/người/buổi |
| Post-session | Checklist attendance, rồi candidate hỗ trợ | Attendance do người xác nhận; DM chỉ draft sau mở phiên |
| SLA / morning brief | Thông báo nội bộ và việc cần người xử lý | Không cần tạo draft seeker hay gọi LLM |

## 8. Duyệt qua Telegram / queues

1. Proactive tạo digest gồm mục đích, buổi/event, target, bằng chứng và lý do chọn.
2. **Mở phiên** cho phép chuẩn bị draft cho các target đã chọn; **Bỏ qua** kết thúc đề xuất đó, không chứng minh đã liên hệ.
3. Yogi đọc draft cùng bối cảnh. Phản hồi sửa nội dung tạo phiên bản mới cần duyệt lại.
4. **Duyệt draft** xác nhận nội dung được chấp nhận, không thực hiện Send.
5. Yogi tự gửi trên Facebook. Chỉ ghi **đã gửi** sau khi có xác nhận thực tế.
6. Tin mới, đổi lịch, hết hạn hoặc opt-out làm draft cần xét lại, dù trước đó đã duyệt.

Reactive có thể đi thẳng tới bước draft sau gate, không cần phiên proactive.
Nếu UI dùng 👍, ý nghĩa phụ thuộc loại mục: mở phiên hoặc duyệt đúng phiên bản
draft; không bao giờ là quyền tự gửi DM. Thiếu Telegram/message ID phải giữ
pending/lỗi thông báo, có thể duyệt trên web; không tự approve.
Bỏ qua phiên hoặc từ chối draft không xóa nhu cầu chưa được giải quyết khỏi brief.

### Format proposal inbox trên Telegram

Telegram là màn hình quyết định nhanh, không phải nơi sao chép toàn bộ inbox.
Mỗi proposal inbox phải có theo đúng thứ tự: tên seeker, **URL hồ sơ seeker có thể
click**, hội thoại gần đây và draft MAS. URL phải dẫn thẳng tới `/seekers/{thread_id}`
trên web UI đang vận hành để yogi mở hồ sơ trước khi duyệt.

- Dùng xuống dòng và thụt **2 space** cho giá trị trực tiếp dưới một nhãn;
  dùng **4 space** cho nội dung từng message trong hội thoại. Không dùng một khối
  văn bản dài hoặc nối các phần bằng dấu `:`.
- Chỉ đưa tối đa 8 message gần nhất có ích cho quyết định. Giữ timestamp và sender
  để người duyệt biết ngữ cảnh.
- Khi seeker tương tác từ post/ad/Auto_Page, chỉ hiển thị bản nhận diện ngắn của
  nội dung placement: phần đầu `…` phần cuối (ví dụ `ABCD…EFGH`), không chép nguyên
  văn nội dung quảng cáo/bài post. Tin nhắn trao đổi bình thường vẫn được giữ đủ khi ngắn.
- Link operator mặc định là `http://localhost:9995/seekers/{thread_id}` — canonical
  dashboard URL của hệ thống. Deployments có thể thay origin bằng
  `SEEKER_WEB_BASE_URL` hoặc `WEB_APP_URL`.

## 9. Knowledge và giọng trả lời

- SOUL quy định giọng điệu; strategy quy định quyết định chăm sóc. Không nạp toàn bộ strategy vào mỗi call trả lời.
- Lịch lớp/sự kiện và liên hệ lấy từ nguồn còn hiệu lực; không học địa chỉ/ngày hẹn từ tin của người khác.
- Xưng hô ưu tiên tin yogi trong chính thread. Few-shot khác thread chỉ học văn phong sau khi khử tên, SĐT, lịch hẹn và dữ liệu riêng.
- Chỉ nạp lớp theo nhu cầu city/hình thức, FAQ liên quan và nghiên cứu đã được biên tập khi được hỏi.
- Không đánh mất policy tham gia lớp bất kỳ lúc nào khi cắt section; chỉ áp dụng nếu đúng chương trình đang xét.
- Batch phải giữ nguồn/bối cảnh riêng của mỗi seeker; không trộn xưng hô hoặc đăng ký.
- Ngắn gọn, miễn phí, không áp lực; không giả danh một yogi cụ thể. Khi thiếu dữ kiện, hỏi rõ hoặc nhờ người phụ trách.

## 10. Kiểm tra chất lượng và KPI

Trước mọi đề xuất, kiểm tra dữ kiện/nguồn, nhu cầu còn chờ, thời gian và hiệu lực,
đúng seeker/lớp, quyền liên hệ, budget và ngôn ngữ. Trước gửi tay cần kiểm tra
lại thay đổi sau thời điểm tạo draft.

| Chỉ số | Cách đánh giá |
| --- | --- |
| Câu hỏi thật bị bỏ sót | Review cả skip/NO_REPLY/rejected, không chỉ draft được tạo |
| SLA đăng ký | Thời gian từ tiếp nhận đến người xử lý; số ca quá 2h |
| Chất lượng draft | Tỷ lệ dùng/chỉnh/bỏ, phân loại lý do |
| Nhắc đúng buổi | Tin thực sự gửi đúng người/buổi; không lấy số draft làm số được nhắc |
| Attendance | Tỷ lệ unknown, bằng chứng và người xác nhận |
| Giới hạn liên hệ | Vi phạm opt-out, budget, trùng người/buổi/event phải bằng 0 |
| Chuyển stage | Có nguồn bằng chứng; không phát sinh do tạo/duyệt draft |
| Chi phí | Token/request và token/ca đã xử lý, so trên workload tương đương |

Không giảm calls bằng cách bỏ sót seeker. Không dùng số người được nâng stage
hay số tin gửi làm chỉ tiêu thay cho chất lượng chăm sóc.

## 10.5. InboxOrchestrator — vòng lặp và leave-to-human (code:agent-mas-002)

`root_agent` của inbox MAS là `InboxOrchestrator` (`adk_agents/agent.py`), một
LlmAgent gọi 4 specialist (ConversationAnalyst → KnowledgeLibrarian →
ReplyComposer → ReplyQAReviewer) như tool call, lặp lại đến khi QA `PASS`
hoặc `ESCALATE`. Ngưỡng lặp là **30 lượt gọi specialist**, ép bằng Python
(`_orchestrator_loop_guard`, `before_tool_callback`), không phải do model tự
đếm — chạm ngưỡng, lượt gọi tiếp theo bị chặn và orchestrator buộc phải trả
về `[ESCALATE: non_convergence]`.

**Sửa thông tin seeker giữa vòng lặp.** Orchestrator có 2 tool để tự sửa
lệch city/program phát hiện được từ hội thoại: `get_seeker_profile` (đọc hồ
sơ + ai đã set field lần cuối) và `propose_seeker_update` (đề xuất, luôn ghi
vào `seeker_field_changes` để audit). Đề xuất confidence ≥ 0.75 được áp dụng
ngay (`source='mas'`) để `KnowledgeLibrarian`/`ReplyComposer` dùng dữ kiện
đúng; dưới ngưỡng đó chỉ ghi lại, không tự ghi đè. Một field đã có
`source='human'` (`users.city_source`/`program_code_source`) chỉ có thể bị
sửa tiếp bởi một xác nhận khác của con người — MAS không bao giờ tự ghi đè
lên giá trị người vận hành đã xác nhận.

**Leave-to-human.** `ReplyQAReviewer` trả `ESCALATE: <reason_code>: <note>`
thay vì cố `REPAIR` khi việc viết lại không giải quyết được vấn đề:

| reason_code | Khi nào |
| --- | --- |
| `knowledge_gap` | Không có dữ kiện để trả lời |
| `contradiction` | Hội thoại hoặc hồ sơ CRM tự mâu thuẫn |
| `sensitive` | Sức khỏe/tâm lý/tôn giáo/tiền bạc/khiếu nại |
| `adversarial` | Nghi vấn câu hỏi bẫy, so sánh, thử prompt injection |
| `policy_uncertain` | Ngoài phạm vi tài liệu này |
| `identity_change_low_conf` | Đổi city/program nhưng bằng chứng yếu |
| `non_convergence` | Hết 30 lượt lặp mà chưa hội tụ |

Một item escalate không có reply để duyệt/gửi — nó lên Telegram dưới dạng
thẻ riêng (`format_escalation_proposal`, cột `telegram_hitl_queue.escalation_reason`/
`escalation_note`) để người phụ trách đọc hội thoại và tự trả lời. Không có
trường hợp nào bị "rơi" (`no_reply` không lý do) — mọi escalate đều ghi
`mas_decisions` với route `inbox_escalation`.

## 11. Tài liệu liên quan

- [SOUL](SOUL.md): giọng điệu và ranh giới kiến thức.
- [Use cases](../docs/usecases/seeker-care-operations.md): nghiệp vụ UC-05–12.
- [MAS care execution contract](../docs/architect/mas-care-execution-contract.md): luồng kỹ thuật, dữ liệu và Telegram.
- [Kế hoạch time-aware care](../docs/PRDs/mas-time-aware-care-plan.md): implementation và các gate còn phải nghiệm thu.
- [Rà soát strategy/memory](../docs/report/mas-strategy-memory-review-2026-09-17.md): căn cứ cho lần cập nhật này.
