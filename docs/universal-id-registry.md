# Universal ID registry

**Universal ID:** `doc:universal-id-registry-001`

Đây là sổ đăng ký chuẩn cho Universal ID của tài liệu trong `docs/`. Mỗi tài
liệu có một ID ổn định theo mẫu `doc:<tên-ngắn-kebab-case>-<số-thứ-tự-ba-chữ-số>`.
ID không thay đổi khi đổi tên hoặc di chuyển tệp; chỉ cập nhật cột đường dẫn.

## Quy tắc quản lý

1. Khi tạo tài liệu, thêm Universal ID ngay dưới tiêu đề H1 và đăng ký nó trong
   bảng này trong cùng thay đổi.
2. Không tái sử dụng ID của tài liệu đã xóa. Giữ dòng đó trong registry với
   trạng thái `retired` và đường dẫn cuối cùng.
3. Mỗi tài liệu chuẩn có đúng một ID. Tệp mirror/legacy được phép dùng cùng ID
   với tài liệu chuẩn chỉ khi dòng registry ghi rõ lý do.
4. README thư mục cũng là tài liệu có thể tham chiếu, vì vậy có ID riêng.

## Danh sách đang hoạt động

| Universal ID | Đường dẫn chuẩn | Trạng thái / ghi chú |
| --- | --- | --- |
| `doc:architecture-001` | `docs/architect/architecture.md` | active; `docs/ARCHITECTURE.md` là mirror tương thích lịch sử của cùng tài liệu. |
| `doc:architecture-decisions-001` | `docs/architecture-decisions.md` | active |
| `doc:architecture-audit-001` | `docs/report/architecture-decisions.md` | active |
| `doc:dev1-stage-gate-audit-001` | `docs/report/dev1-stage-audit.md` | active |
| `doc:dev3-event-route-audit-001` | `docs/report/dev3-event-audit.md` | active |
| `doc:event-route-audit-001` | `docs/report/event-audit.md` | active |
| `doc:facebook-virtualized-scrolling-retrospective-001` | `docs/reports/retrospective-facebook-virtualized-scrolling.md` | active |
| `doc:inbox-decoupled-jobs-001` | `docs/design/inbox-decoupled-jobs.md` | active; thiết kế tách Inbox thành 4 job (Fetch / Fetch-QA / Classify / Propose), thoả mãn `prd:inbox-decoupled-jobs-001`. |
| `doc:inbox-decoupled-jobs-test-plan-001` | `docs/tests/test-plan-inbox-decoupled-jobs.md` | active; test plan cho `prd:inbox-decoupled-jobs-001`; test code mang tag `code:test-decoupled-001:*`. |
| `doc:inbox-fetch-pipeline-001` | `docs/architect/inbox-fetch-pipeline.md` | active; canonical inbox ingestion design (two-stage crawl + `--workers` orchestrator/worker model). |
| `doc:llm-observability-001` | `docs/design/llm-observability.md` | active; bảng `llm_calls` + trang `/llm`, warmup thủ công; thoả mãn `prd:llm-observability-001`. |
| `doc:llm-observability-test-plan-001` | `docs/tests/test-plan-llm-observability.md` | active; test code tag `code:test-llm-obs-001:*`. |
| `doc:mas-strategy-test-plan-001` | `docs/tests/test-plan.md` | active |
| `doc:prd-001` | `docs/PRDs/PRD.md` | placeholder; reserve this ID for the first PRD or retire it intentionally. |
| `prd:inbox-parallel-fetch-001` | `docs/PRDs/prd-inbox-parallel-fetch.md` | active; first traceable PRD (`prd:` type per CLAUDE.md §6), satisfied by `doc:inbox-fetch-pipeline-001` and `code:inbox-parallel-fetch-001:*`. |
| `prd:inbox-decoupled-jobs-001` | `docs/PRDs/prd-inbox-decoupled-jobs.md` | active; thành phần `:classify-001`, `:city-spinner-001`, `:fetch-qa-001`, `:propose-001`. Thiết kế `doc:inbox-decoupled-jobs-001`. |
| `prd:llm-observability-001` | `docs/PRDs/prd-llm-observability.md` | active; thành phần `:warmup-manual-001`, `:trace-001`, `:page-001`, `:stats-001`. Chứa bảng rà soát các điểm kích hoạt MAS/LLM (2026-09-17). |
| `doc:qa-audit-001` | `docs/report/qa-report.md` | active |
| `doc:readme-architecture-001` | `docs/architect/README.md` | active |
| `doc:readme-audit-reports-001` | `docs/report/README.md` | active |
| `doc:readme-design-001` | `docs/design/README.md` | active |
| `doc:readme-prds-001` | `docs/PRDs/README.md` | active |
| `doc:readme-reports-001` | `docs/reports/README.md` | active |
| `doc:readme-researches-001` | `docs/researches/README.md` | active |
| `doc:readme-research-spikes-001` | `docs/researches/spikes/README.md` | active |
| `doc:readme-test-docs-001` | `docs/tests/README.md` | active |
| `doc:readme-usecases-001` | `docs/usecases/README.md` | active |
| `doc:spike-adk-mas-001` | `docs/researches/spikes/spike-google-adk-mas-facebook-inbox.md` | active; existing ID retained. |
| `doc:spike-city-detect-001` | `docs/researches/spikes/spike-city-detection-design.md` | active; existing ID retained. |
| `doc:stage-gates-audit-001` | `docs/report/stage-gates-audit.md` | active |
| `doc:test-report-001` | `docs/reports/test-report.md` | active |
| `doc:universal-id-registry-001` | `docs/universal-id-registry.md` | active; registry của chính nó. |
| `doc:usecases-001` | `docs/usecases/seeker-care-operations.md` | active |
| `doc:warmup-audit-001` | `docs/report/warmup-audit.md` | active |
| `doc:warmup-route-audit-001` | `docs/report/dev2-warmup-audit.md` | active |

## Các ID được giữ lại

| Universal ID | Đường dẫn cuối cùng | Trạng thái / lý do |
| --- | --- | --- |
| _Chưa có_ | — | Chưa có tài liệu nào bị retire kể từ khi registry được tạo. |
