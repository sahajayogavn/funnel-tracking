# Thông tin Sự Kiện (Events)

File này lưu trữ thông tin về các sự kiện cộng đồng của Sahaja Yoga Vietnam. File này hiện được nạp trực tiếp vào `knowledge_context` của inbox MAS, nên thông tin ngày, thành phố, đối tượng, và cách đăng ký cần rõ ràng để agent có thể dùng lại khi trả lời seekers.

**Nguyên tắc sử dụng**:
- Ưu tiên trả lời từ các mục **sắp diễn ra**.
- Nếu sự kiện đã qua, chỉ dùng làm tham chiếu lịch sử chứ không quảng bá như sự kiện mới.
- Nếu seeker hỏi lịch cụ thể trong tháng mà file chưa ghi ngày chính xác, hãy nói sẽ xác nhận lại với anh/chị trong CLB.

---

## 🗂️ Sự kiện đã diễn ra — Workshop Thiền & Sáng Tạo Ốc, HCM (22/03/2026)

- **Trạng thái**: Đã diễn ra, chỉ dùng làm lịch sử tham chiếu
- **Khu vực**: TP. Hồ Chí Minh
- **Thời gian**: 9:00 – 11:00, ngày 22/03/2026
- **Địa điểm**: 81 Trần Quốc Thảo, P. Võ Thị Sáu, Q.3 (Hội Văn Học Nghệ Thuật)
- **Học phí**: Hoàn toàn miễn phí
- **Số lượng**: Giới hạn 20 người
- **Đăng ký**: Nhắn tin trực tiếp cho Page hoặc để lại bình luận
- **Liên hệ HCM / Vũng Tàu**: Dung — 0937820098
- **Nhân dịp**: Kỷ niệm 103 năm ngày sinh Shri Mataji Nirmala Devi
- **Đối tượng**: Người mới muốn trải nghiệm Thiền định. Ưu tiên anh chị đã thiền cùng Sahaja Yoga giới thiệu.
- **Nội dung**:
  - **Phần 1 — Thiền cùng Sahaja Yoga**: Trải nghiệm sự bình an bên trong. Hướng dẫn bước-từng-bước, giúp bất kỳ ai cũng có thể cảm nhận sự bình an thuần khiết.
  - **Phần 2 — Sáng tạo Ốc**: Để bàn tay theo dòng năng lượng, tạo tác phẩm nghệ thuật từ vỏ ốc mang hơi thở đại dương. Tác phẩm hoàn thiện được mang về như một món quà tâm hồn.

---

## 🌿 Sự kiện sắp diễn ra — Chương trình Thiền & Âm nhạc, Đà Nẵng / Hội An / Huế (Tháng 4/2026)

- **Khu vực**: Đà Nẵng, Hội An, Huế
- **Thời gian**: Tháng 4/2026 (liên hệ để biết lịch cụ thể)
- **Học phí**: Hoàn toàn miễn phí
- **Đăng ký**: Nhắn tin trực tiếp cho Page
- **Liên hệ Đà Nẵng**: Hưng — 0948546920
- **Liên hệ Hội An**: Mai — 0383888651
- **Thực hiện bởi**: Các cô chú, anh chị đến từ Úc và Việt Nam
- **Đối tượng**: Các công ty, khách sạn/resort và các câu lạc bộ
- **Hình thức phù hợp**:
  - Team building
  - Workshop chăm sóc nhân sự
  - Trải nghiệm đặc biệt cho khách hàng
- **Nội dung**: Âm nhạc nhẹ nhàng kết hợp cùng thiền định đơn giản, dễ thực hành — tạo không gian thư giãn, cân bằng và kết nối cho tập thể.

---

## 📱 Mã QR Nhóm Zalo (cho sự kiện)

Khi sự kiện có nhóm Zalo riêng, hãy tạo QR code bằng CLI tool:

```bash
.venv/bin/python tools/generate_qr.py --url "https://zalo.me/g/<group_id>"
```

QR sẽ được lưu tại `memory/agent_memory/qr_codes/` với tên file `safe_filename(URL).png`.
Bot gửi ảnh QR cho seekers để họ quét và tham gia nhóm Zalo sự kiện.

_(Hiện tại chưa có nhóm Zalo riêng cho sự kiện. Xem `lop-hoc.md` để biết QR các lớp học.)_

---

## 📞 Liên hệ các trung tâm Sahaja Yoga

| Khu vực | Người liên hệ | Số điện thoại |
| --- | --- | --- |
| Hà Nội | Linh | 0814478038 |
| Hà Nội | Hùng | 0366667975 |
| Nghệ An | Thảo | 0979482830 |
| Đà Nẵng | Hưng | 0948546920 |
| Hội An | Mai | 0383888651 |
| Hồ Chí Minh / Vũng Tàu | Dung | 0937820098 |

- **Fanpage**: https://www.facebook.com/SahajaVietnam

---

_(Bot sẽ kiểm tra file này để thông tin cho Seekers nếu họ quan tâm đến sự kiện offline)_
