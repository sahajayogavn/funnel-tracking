# Worker queue và checkpoint/resume

Fix áp dụng cho parallel fetch (`--cdp --workers 2` hoặc `3`).

- Worker kiểm tra stop trước khi lấy task; nếu stop chạy đua với `get`, trả task lại queue.
- Production dùng chung queue cho task/retry. Worker chỉ thoát bình thường khi discovery hoàn tất và `unfinished_tasks == 0`, bao gồm task đang xử lý. Không gửi sentinel trong chế độ này.
- Retry tối đa hai lần mỗi lần chạy; reload sidebar trước khi worker nhận lại task do chính nó thất bại. Circuit breaker vẫn reload/retire tab lỗi, không kích global stop vì lỗi locate.
- Bổ sung `critical()` cho logger wrapper để quality reject được ghi result trước khi worker dừng.
- Journal `logs/parallel-fetch/run_<id>.jsonl`: task được flush + fsync trước dispatch; result được flush + fsync sau xử lý. Khóa file độc quyền ngăn hai tiến trình resume cùng run. Không cần lease vì chỉ một tiến trình sở hữu journal; sau crash, task chưa có kết quả được claim lại khi resume.
- `tasks_dispatched == terminal_results + durable_pending` được kiểm tra trước khi trả stats. Stats có `checkpoint_path`, `resumable`, `discovery_complete` và danh sách pending.

Chạy lại chính lệnh fetch ban đầu, thêm:

```sh
--resume logs/parallel-fetch/run_<id>.jsonl
```

Giữ nguyên page, time range, maxThreads, refresh, early-exit và targetMessages; tham số khác checkpoint sẽ bị từ chối. Đường dẫn checkpoint được log ngay đầu run. Giữ file này cho đến khi xử lý xong.

Nếu discovery đã hoàn tất, resume không quét sidebar; chỉ chạy task chưa có result hoặc lỗi điều hướng/tab/temporary block. Task persisted, no_messages và quality reject không tự động fetch lại. Quality reject vẫn được báo trong kết quả run, cần xử lý riêng. Nếu discovery bị ngắt, resume quét sidebar để tìm phần chưa được ghi checkpoint, bỏ qua task đã biết và không dùng early-exit.

Bảo đảm là at-least-once: crash sau commit dữ liệu nhưng trước ghi result có thể chạy lại task đó; persistence hiện có dedup. Không hứa exactly-once giữa database và journal. Checkpoint lưu task-level, không resume giữa lịch sử của một conversation.

Run cũ trước bản sửa chưa có journal này không thể truyền assignment log trực tiếp vào `--resume`. Các thread đã lưu vẫn nằm trong database; muốn khôi phục chính xác backlog cũ cần chuyển dữ liệu `unfinished_tasks` của assignment log sang checkpoint sau khi xác minh page/range. Không khởi chạy lại batch Facebook thật trong quá trình kiểm thử bản sửa.
