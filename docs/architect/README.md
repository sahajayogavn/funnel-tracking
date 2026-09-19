# Architecture

**Universal ID:** `doc:readme-architecture-001`

Tài liệu kiến trúc hợp nhất hiện hành là [architecture.md](architecture.md). Tài liệu này tổng hợp kiến trúc nền tảng, các quyết định an toàn Inbox và phần đối chiếu với triển khai hiện tại.

Tài liệu chuyên sâu theo phân hệ:

- [mas-care-execution-contract.md](mas-care-execution-contract.md) (`doc:mas-care-execution-contract-001`): contract đích về ingestion, gate, phiên chăm sóc, queue/Telegram và bằng chứng; tách phần kỹ thuật khỏi MAS Strategy, chưa phải chứng nhận runtime.

- [mas-response-quality-runbook.md](mas-response-quality-runbook.md): quy trình xem xét một reply MAS đáng ngờ và sửa đúng lớp lỗi.

- [mas-debugging-playbook.md](mas-debugging-playbook.md) (`doc:mas-debugging-playbook-001`): điểm vào điều tra một case MAS cụ thể — công cụ `tools/l5_mas_trace_debug.py`, bản đồ codebase MAS, và nhật ký kỹ thuật các nguyên nhân đã tìm ra.

- [inbox-fetch-pipeline.md](inbox-fetch-pipeline.md) (`doc:inbox-fetch-pipeline-001`): thiết kế pipeline quét Inbox — hai giai đoạn hiện tại và mô hình orchestrator/worker (`--workers N`) kèm kế hoạch triển khai.

Các tệp kiến trúc cũ ở cấp `docs/` vẫn được giữ để không làm hỏng liên kết lịch sử và các báo cáo đang tham chiếu chúng.
