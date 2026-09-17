# Phân loại thành phố và chương trình lớp

Mỗi seeker vẫn được **sắp xếp/lọc theo thành phố**, nhưng một thành phố có thể có nhiều lớp khác nhau theo địa điểm và khung giờ. Vì vậy, thành phố không phải là định danh đủ để gửi thông tin lớp.

## Mã chương trình

Mỗi lớp cố định có một mã ngắn, đọc được trực tiếp:

- `20h-T3-Hoàng Quốc Việt-HN`
- `14h30-CN-Vương Thừa Vũ-HN`
- `15h30-CN-Vương Thừa Vũ-HN`

Mã bao gồm giờ, thứ, địa điểm và thành phố. Hai lớp cùng `40 Vương Thừa Vũ` vẫn là hai chương trình khác nhau khi khung giờ khác nhau.

Danh mục nguồn là [Google Sheet lớp cố định](https://docs.google.com/spreadsheets/d/1g2sOGrla4GXmOpn7dHpRjeSnEA28xDBHeNK_RBOZmLM/edit?usp=sharing). Bất cứ thay đổi lịch nào phải cập nhật danh mục mã đồng thời; không tạo mã từ tên thành phố đơn thuần.

## Luồng phát hiện

1. Luồng inbox chạy phát hiện **thành phố bằng LLM trước**; tin nhắn seeker được ưu tiên hơn trả lời của Page, và quảng cáo là tín hiệu yếu nhất.
2. Cùng ngữ cảnh đó, LLM đối chiếu danh mục chương trình cố định để lưu `program_code` chỉ khi seeker thể hiện quan tâm, chọn hoặc xác nhận tham gia **một lớp cụ thể** và nội dung xác định rõ giờ/thứ/địa điểm. Nội dung quảng cáo hay một câu trả lời của Page, đứng riêng lẻ, không đủ bằng chứng vì có thể quảng cáo nhiều lớp trong cùng thành phố.
3. Nếu thành phố rõ nhưng lớp chưa rõ, giữ `program_code` trống. Bảng Seekers không hiển thị lớp nào dưới badge thành phố trong trường hợp này.
4. Bộ lọc `Chương trình` chỉ trả về seekers có `program_code` khớp chính xác; bộ lọc thành phố vẫn hoạt động độc lập.

Ví dụ: seeker nhắc “CN 14:30 ở Vương Thừa Vũ” được gán `14h30-CN-Vương Thừa Vũ-HN`; không được gán vào lớp 15:30 cùng địa điểm.

Để backfill dữ liệu hiện có sau khi triển khai, chạy `python tools/classify_city.py --action classify_all --force`. Lệnh này dùng cùng LLM và ghi cả `city` lẫn `program_code` khi lớp được xác định rõ.
