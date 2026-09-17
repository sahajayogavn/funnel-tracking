# Design documents

**Universal ID:** `doc:readme-design-001`

Nơi lưu tài liệu thiết kế kỹ thuật (`doc:` type) thoả mãn một PRD cụ thể, cùng các khai báo
kế hoạch triển khai UI (`plan-declaration.json`).

- [inbox-decoupled-jobs.md](inbox-decoupled-jobs.md) (`doc:inbox-decoupled-jobs-001`): tách Inbox
  thành 4 job độc lập — Fetch (browser) / Fetch-QA (browser, read-only) / Classify (LLM) /
  Propose (LLM). Thoả mãn `prd:inbox-decoupled-jobs-001`; test plan
  `doc:inbox-decoupled-jobs-test-plan-001`.
- [llm-observability.md](llm-observability.md) (`doc:llm-observability-001`): bảng `llm_calls`
  (bắt mọi lượt gọi LLM qua ADK callbacks + HTTP wrapper), trang `/llm` để review/filter/copy,
  và chuyển warmup sang kích hoạt thủ công từ `/queues`. Thoả mãn `prd:llm-observability-001`.
- `plan-declaration.json`: khai báo rollout UI (`rl-2026-09-15-001`) cho chọn vùng bảng seeker
  và hàng đợi phê duyệt.

Quy ước: mỗi thiết kế ghi Universal ID ngay dưới H1, liên kết ngược tới PRD và test plan, và
liệt kê Code ID dự kiến trong ma trận thoả mãn để agent implement gắn đúng tag vào code.
