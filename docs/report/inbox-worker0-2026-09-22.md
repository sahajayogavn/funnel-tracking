# Orchestrator là worker 0

## Hành vi

- Fetch mặc định dùng **một tab**, không mở worker thread/tab phụ. `--worker N` và `--workers N` tương đương; N là tổng số tab, giới hạn 1–3. Classify-city vẫn mặc định concurrency 10.
- Discovery gọi fetch chi tiết ngay trong từng viewport, trước lượt cuộn tiếp theo. Worker 0 chỉ click card hiện có, không nhảy offset, không cuộn sidebar tìm lại và không navigate rời discovery. Task lỗi được retry sau hoặc giao worker phụ; stop gate làm discovery dừng và checkpoint vẫn đánh dấu chưa discovery xong.
- Task chưa có ID được worker 0 xử lý tại chỗ. Khi xác minh panel thành công, ghi `record.selected_item_id` và canonical `record.thread_id` vào journal **trước** khi scan lịch sử. Task đã có ID được chia giữa worker 0 và worker phụ nếu có. Không scan hai lần một task chỉ để lấy ID.
- Worker phụ và worker 0 sau discovery tìm bằng sidebar trước. Khi không tìm được card, dùng ID đã lưu (`selected_item_id` hoặc `psid_hint`) để tạo URL `asset_id + mailbox_id + selected_item_id + thread_type=FB_MESSAGE`, navigate và xác minh trước khi đọc/lưu.
- Worker 0 tham gia drain queue sau discovery; worker phụ nghỉ không còn đồng nghĩa bỏ trống toàn bộ pool. Worker 0 vẫn có circuit breaker khi drain; backlog được giữ nếu không còn tab hoạt động.
- Resume hoạt động cả với `--workers 1`. Giữ nguyên page/range/refresh/maxThreads/target của run trước; số worker có thể thay đổi. Checkpoint cũ tương thích vì URL được dựng từ ID thay vì yêu cầu thêm field.
- Non-CDP `scrape_inbox` cũng dùng engine worker 0; không mở tab CDP phụ. CLI resume hiện yêu cầu `--cdp`.

## Sử dụng

```sh
# Mặc định: chính tab orchestrator fetch chi tiết
.venv/bin/python tools/l5_fetch_fb_messages.py --pageId PAGE_ID --cdp --time_range 1080d --refresh --maxThreads 4000

# Hai tab tổng cộng: worker 0 và worker 1
# Thêm vào lệnh trên: --worker 2

# Tiếp tục run: giữ nguyên các tham số run, thêm:
# --resume logs/parallel-fetch/run_<id>.jsonl
```

## Kiểm thử

`tests/test_inbox_worker0.py` kiểm tra fetch trước discovery tiếp theo, không mở thread phụ ở N=1, checkpoint ID trước khi history scan chết, giữ sidebar viewport, chia việc cho worker phụ, worker 0 drain sau khi mọi peer nghỉ, default CLI và alias. Suite locator kiểm tra sidebar-first/URL-fallback; suite checkpoint giữ kiểm tra restart, journal bị ghi dở, khóa và pending accounting.

Không chạy batch Facebook thật trong quá trình triển khai. Fallback vẫn cần trang tải và xác minh đúng recipient; không bảo đảm Facebook luôn phản hồi.
