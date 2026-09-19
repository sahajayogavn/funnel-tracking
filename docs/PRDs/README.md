# PRDs

**Universal ID:** `doc:readme-prds-001`

Nơi lưu Product Requirements Documents (PRD). `PRD.md` là tệp giữ chỗ chưa chứa yêu cầu có thể truy vết.

PRD đang hoạt động:

- [prd-inbox-parallel-fetch.md](prd-inbox-parallel-fetch.md) (`prd:inbox-parallel-fetch-001`): quét Inbox song song với `--workers N` (orchestrator + worker tabs). Thiết kế tại `doc:inbox-fetch-pipeline-001`.
- [prd-inbox-decoupled-jobs.md](prd-inbox-decoupled-jobs.md) (`prd:inbox-decoupled-jobs-001`): tách Inbox thành các job độc lập — Fetch chỉ crawl, Classify (LLM city/program), Fetch-QA (đối chiếu top-10 với inbox thật, cảnh báo Telegram), Propose (không dùng browser). Thiết kế tại `doc:inbox-decoupled-jobs-001`.
- [mas-time-aware-care-plan.md](mas-time-aware-care-plan.md) (`prd:mas-time-aware-001`): có implementation P0–P3 trong workspace; **chưa nghiệm thu lại đầy đủ**. §7 bổ sung kế hoạch A–F sau rà soát strategy/memory, chưa triển khai: thống nhất quyền hạn, evidence, lịch 08:30, phiên/attendance và retrieval. Audit gốc `doc:mas-execution-audit-001`; rà soát bổ sung `doc:mas-strategy-memory-review-001`.
- [prd-llm-observability.md](prd-llm-observability.md) (`prd:llm-observability-001`): warmup chỉ kích hoạt thủ công từ `/queues`; ghi mọi lượt gọi MAS/LLM vào `llm_calls`; trang `/llm` để review, filter, copy prompt/response. Kèm bảng rà soát mọi điểm kích hoạt MAS/LLM. Thiết kế tại `doc:llm-observability-001`.

Khi bổ sung PRD, hãy đặt tệp tại đây, nêu mã yêu cầu ổn định và liên kết tới thiết kế, mã nguồn và kiểm thử tương ứng.
