# Architecture

**Universal ID:** `doc:readme-architecture-001`

Tài liệu kiến trúc hợp nhất hiện hành là [architecture.md](architecture.md). Tài liệu này tổng hợp kiến trúc nền tảng, các quyết định an toàn Inbox và phần đối chiếu với triển khai hiện tại.

Tài liệu chuyên sâu theo phân hệ:

- [inbox-fetch-pipeline.md](inbox-fetch-pipeline.md) (`doc:inbox-fetch-pipeline-001`): thiết kế pipeline quét Inbox — hai giai đoạn hiện tại và mô hình orchestrator/worker (`--workers N`) kèm kế hoạch triển khai.

Các tệp kiến trúc cũ ở cấp `docs/` vẫn được giữ để không làm hỏng liên kết lịch sử và các báo cáo đang tham chiếu chúng.
