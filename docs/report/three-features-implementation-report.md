# Báo Cáo Triển Khai 3 Tính Năng Funnel Tracking & Hàng Đợi MAS

**Universal ID**: `doc:report-three-features-001`  
**Ngày hoàn thành**: 15/09/2026  
**Trạng thái**: Hoàn tất thành công (Succeeded)  

---

## 1. Mục tiêu và Phạm vi

Triển khai trọn vẹn 3 tính năng cốt lõi cho nền tảng Funnel Tracking của Thiền Sahaja Yoga Việt Nam theo yêu cầu điều phối viên:
1. **Accessible Seven-Star Progress Indicator & Seeker Detail Journey Timeline (`/seekers`)**:
   - Thay thế hiển thị lead stage đơn điệu bằng thước đo tiến trình 7 sao chuẩn trợ năng (`role="meter"`, `aria-valuenow`, `aria-label`).
   - Dòng thời gian hành trình 7 giai đoạn hiển thị thời gian tương đối (`formatRelativeElapsed`: ví dụ `3d`, `3m 2d`, `3y 4m`, `12h`), các chặng trước và hiện tại sáng màu, các chặng tương lai tối màu.
   - Tích hợp đề xuất MAS đang chờ duyệt (pending) ngay trong hồ sơ seeker kèm nút Duyệt / Từ chối.
   - Bảo toàn dữ liệu canonical stage được lưu trong cơ sở dữ liệu (`memory/agent_memory/frankensqlite.db`), không ghi đè mất stage gốc.
2. **Bộ lọc Thành phố và Khoảng thời gian (City & Date-Range Filters)**:
   - Tích hợp thanh lọc `FunnelFilterBar` đồng nhất trên `/seekers`, `/graph`, và `/journey`.
   - Lưu trữ và phục hồi trạng thái lọc qua `localStorage` với key `sahaja_funnel_filters` khi tải lại trang hoặc điều hướng client-side.
   - Cập nhật dữ liệu động: `/graph` gọi API lọc nodes/edges theo thành phố và ngày, `/journey` tự động tính lại số lượng seeker ở mỗi node chặng hành trình.
3. **Bộ điều khiển tạo đề xuất an toàn ("Run Recommendations" Controls)**:
   - Tạo CLI tool `tools/l5_recommendations.py` và API `POST /api/action-queue/recommendations` hỗ trợ đầy đủ ngữ cảnh (`reply`, `comment`, `warmup`, `event`, `all`).
   - Giao diện trực quan trên `/queues` với bảng điều khiển "Tạo đề xuất MAS" cho từng ngữ cảnh, nút làm mới và thông báo kết quả.
   - Modal tạo đề xuất hàng loạt trên `/seekers` và nút tạo đề xuất cá nhân hóa trong thanh chi tiết bên phải seeker.
   - Tuyệt đối tuân thủ quy tắc an toàn: Mọi đề xuất đưa vào `action_queue` với trạng thái `pending`, không bao giờ tự động gửi tin nhắn hoặc kích hoạt CDP trực tiếp.

---

## 2. Chi tiết các tệp đã tạo và cập nhật

### A. Backend & Python CLI
- `tools/l5_recommendations.py` (`# code:tool-recommendations-001`): Quét cơ sở dữ liệu tìm tin nhắn chưa rep, comment chưa phản hồi, seekers ngủ đông cần warm-up, và seekers cần thông báo sự kiện; tạo đề xuất vào bảng `action_queue` với `status = 'pending'`, tích hợp cơ chế deduplication ngăn trùng lặp.
- `tests/test_recommendations.py`: Bộ kiểm thử tự động 4 kịch bản cho công cụ đề xuất, xác nhận không auto-send tin nhắn.

### B. Next.js Web Application
- `web/src/lib/types.ts`: Bổ sung 7 chặng canonical (`CanonicalStage`), hàm `getStageNumber`, `getStageLabel`, và hàm tính thời gian trôi qua định dạng chuẩn `formatRelativeElapsed`.
- `web/src/lib/queries.ts`: Cập nhật `getAllSeekers` tôn trọng canonical stage từ DB; bổ sung hàm `getSeekerActionQueueItems`; nâng cấp `getGraphData` hỗ trợ lọc theo city, startDate, endDate.
- `web/src/app/api/action-queue/route.ts`: API GET (danh sách queue items hoặc lọc theo seeker) và POST (enqueue thủ công).
- `web/src/app/api/action-queue/recommendations/route.ts`: API POST kích hoạt quét và sinh đề xuất an toàn theo context hoặc cho cá nhân seeker.
- `web/src/app/api/graph/route.ts`: Nhận query params `city`, `startDate`, `endDate` và truyền vào `getGraphData`.
- `web/src/components/seven-star-progress.tsx`: Component thanh 7 ngôi sao tương tác chuẩn a11y (`role="meter"`, nhãn phụ bên dưới, tooltip mô tả).
- `web/src/components/seeker-journey-timeline.tsx`: Component dòng thời gian 7 chặng với thời gian tương đối, trạng thái giai đoạn, và danh sách đề xuất hàng đợi hành động trực tiếp.
- `web/src/components/funnel-filter-bar.tsx`: Thanh lọc dùng chung cho toàn bộ app với dropdown Thành phố, chọn ngày bắt đầu - kết thúc, nút chọn nhanh 7 ngày / 30 ngày / Tất cả, tự động đồng bộ `localStorage['sahaja_funnel_filters']`.
- `web/src/components/seekers-table.tsx`: Tích hợp `FunnelFilterBar`, `SevenStarProgress` trong bảng, `SevenStarProgress` và `SeekerJourneyTimeline` trong sidebar chi tiết, cùng Modal Tạo đề xuất hàng loạt.
- `web/src/components/seeker-detail.tsx`: Trang chi tiết seeker `/seekers/[id]` tích hợp thanh 7 sao và timeline hành trình tương ứng.
- `web/src/components/network-graph.tsx`: Tích hợp `FunnelFilterBar` đồng bộ với backend graph query.
- `web/src/components/journey-flow.tsx` & `web/src/app/journey/page.tsx`: Tích hợp `FunnelFilterBar` và tính toán lại số lượng seeker động tại mỗi node chặng.
- `web/src/components/action-queues.tsx`: Bảng điều khiển "Tạo đề xuất MAS" trên `/queues` với 5 nút kích hoạt theo từng ngữ cảnh và nút làm mới dữ liệu.

---

## 3. Kết quả Kiểm thử & Đảm bảo Chất lượng

1. **Unit Test Python**:
   - `pytest tests/test_recommendations.py`: 4/4 kiểm thử đỗ trong 0.07 giây.
   - Toàn bộ suite không E2E (`pytest tests/ -k "not e2e"`): **257 passed**, 0 failed trong 6.94 giây.
2. **Next.js Production Build**:
   - Chạy `npm run build` trong `web/`: Biên dịch thành công 100% không cảnh báo lỗi type hay cú pháp.
3. **Kiểm thử Tích hợp HTTP**:
   - `curl` kiểm tra các trang `/seekers`, `/graph`, `/journey`, `/queues`: Đều phản hồi mã **200 OK**.
   - `POST /api/action-queue/recommendations`: Sinh thành công 15 đề xuất `pending` cho context `reply` và deduplicate chính xác khi gọi lại (0 trùng lặp).
   - Duyệt và từ chối item qua `/api/action-queue/[id]`: Chuyển trạng thái chính xác sang `approved` và `rejected`.
