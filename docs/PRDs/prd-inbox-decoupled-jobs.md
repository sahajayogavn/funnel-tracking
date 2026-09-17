# PRD: Tách Inbox thành các job độc lập (Fetch / Classify / Fetch-QA / Propose)

**Universal ID:** `prd:inbox-decoupled-jobs-001`
**Design:** `doc:inbox-decoupled-jobs-001` — [`../design/inbox-decoupled-jobs.md`](../design/inbox-decoupled-jobs.md)
**Test plan:** `doc:inbox-decoupled-jobs-test-plan-001` — [`../tests/test-plan-inbox-decoupled-jobs.md`](../tests/test-plan-inbox-decoupled-jobs.md)
**Status:** Proposed — chờ triển khai
**Date:** 2026-09-17
**Requested by:** operator (page `1548373332058326`)
**Builds on:** `prd:inbox-parallel-fetch-001`, `code:tool-citydetect-001`, `code:inbox-activity-lock-001`

## 1. Bối cảnh

Sau `prd:inbox-parallel-fetch-001`, crawl inbox đã chạy đa worker và commit
`b0f833b` đã tách LLM city/program thành route `[CLASSIFY]` chạy thread riêng.
Tuy nhiên vẫn còn ba chỗ trộn lẫn "việc của browser" với "việc của LLM/DB":

1. `persist_thread_record()` vẫn nhận `detect_city` và có thể ghi `users.city`
   inline trong lúc fetch, trong khi `[CLASSIFY]` cũng ghi cột đó → hai process
   cùng sở hữu một cột.
2. `run_inbox_cycle()` (`tools/l5_inbox_mas_runner.py`) mở Playwright, crawl
   50 thread ("JIT pre-flight") rồi mới gọi MAS propose. Nó cầm Chrome trong
   suốt thời gian gọi LLM và tạo crawler thứ hai ghi cùng DB.
3. Không có bước QA nào xác nhận DB khớp với inbox thật sau khi fetch kết
   thúc; lỗi parser chỉ được phát hiện khi người vận hành nhìn thấy.
4. UI cột City không phân biệt được "chưa suy luận" và "suy luận ra Unknown",
   nên người vận hành không biết CLASSIFY có đang chạy hay không.

## 2. Mục tiêu

| # | Mục tiêu | Thành phần PRD |
| --- | --- | --- |
| G1 | Fetch chỉ crawl. Mọi suy luận city/program do `[CLASSIFY]` thực hiện, là process duy nhất ghi `users.city` / `users.program_code` / `users.classification_verified_at`. | `prd:inbox-decoupled-jobs-001:classify-001` |
| G2 | Cột City trong `/seekers` hiện spinner khi user đang chờ/đang được phân loại; tắt khi đã phân loại (kể cả kết quả Unknown). | `prd:inbox-decoupled-jobs-001:city-spinner-001` |
| G3 | Cuối mỗi `[FETCH]`, trước khi nhả browser lock, chạy QA hai tầng (top-10 threads, last message) so DB với inbox thật; lỗi cứng → gửi Telegram HITL + ghi báo cáo. | `prd:inbox-decoupled-jobs-001:fetch-qa-001` |
| G4 | Propose reply là route `[PROPOSE]` không dùng browser, chạy song song với fetch, phát hiện việc bằng predicate trên DB, chỉ ghi `telegram_hitl_queue`. | `prd:inbox-decoupled-jobs-001:propose-001` |

## 3. Ngoài phạm vi

- Không thay đổi cách `hitl_execution_job.py` gõ tin sau khi có 👍 (Rule 10.3).
- Không thay đổi thuật toán LLM city/program hay prompt của MAS Responder.
- Không thêm message queue ngoài SQLite; DB + cột trạng thái là hàng đợi.
- Không rollback dữ liệu fetch khi QA fail; QA chỉ cảnh báo và hạ cờ tin cậy.

## 4. Yêu cầu chức năng

### 4.1 `prd:inbox-decoupled-jobs-001:classify-001` — CLASSIFY là chủ sở hữu duy nhất

- R1.1 Fetch (`scrape_inbox`, `run_parallel_fetch`, `persist_thread_record`)
  **không** gọi `detect_city` và không ghi `users.city`, `users.program_code`,
  `users.classification_verified_at`. Với user mới, `city` để `NULL`.
- R1.2 Predicate stale duy nhất là `STALE_CLASSIFICATION_SQL`
  (`tools/l5_fetch_fb_city_classify.py`). Fetch không cần đánh thức
  CLASSIFY: `last_interaction` tăng là đủ để predicate đúng.
- R1.3 Tick `[CLASSIFY]` trong scheduler ≤ 60 s. Overlap bị bỏ qua bởi
  `_classify_lock` (đã có).
- R1.4 `run_inbox_cycle` không truyền `detect_city` nữa (xem G4 — hàm này sẽ
  không còn crawl).

### 4.2 `prd:inbox-decoupled-jobs-001:city-spinner-001` — Spinner cột City

- R2.1 `Seeker` (`web/src/lib/types.ts`) thêm
  `classificationStatus: 'pending' | 'done' | 'unknown'`.
- R2.2 Trạng thái tính **trong SQL** tại `web/src/lib/queries.ts`, dùng đúng
  biểu thức của `STALE_CLASSIFICATION_SQL`:
  - `pending`: `classification_verified_at IS NULL OR last_interaction > classification_verified_at`
  - `unknown`: không stale và `city` là `NULL`/`'Unknown'`
  - `done`: còn lại
- R2.3 `seekers-table.tsx` cột City: `pending` → badge hiện có (nếu có city
  cũ) + icon xoay + `title="Đang bóc tách & suy luận city/program…"`;
  `unknown` → badge xám "—" không xoay; `done` → như hiện tại.
- R2.4 Spinner là CSS thuần trong `globals.css` (`@keyframes`), không thêm
  thư viện. Tôn trọng `prefers-reduced-motion`.
- R2.5 Bảng tự refresh (polling hiện có hoặc `router.refresh()` ≤ 30 s) để
  spinner tắt dần theo từng batch CLASSIFY commit.

### 4.3 `prd:inbox-decoupled-jobs-001:fetch-qa-001` — QA gate cuối fetch

- R3.1 Chạy **bên trong** `run_fetch_cycle` sau khi orchestrator xong, trước
  khi `scheduler_browser_cycle` nhả lock, và cũng chạy ở CLI
  `l5_fetch_fb_messages.py --action fetch_messages` (cờ `--skip-qa` để tắt).
- R3.2 **QA-1 (thứ tự top-10):** đọc 10 thread đầu sidebar (không click vào
  thread), map sang `canonical_thread_id` qua `_compute_thread_id`, so với
  `SELECT id FROM threads WHERE page_id=? ORDER BY inbox_sort_index LIMIT 10`.
  - Thread có ở DOM, chưa có trong DB, và xuất hiện *sau* thời điểm fetch bắt
    đầu → cảnh báo mềm (`soft`).
  - Thread có trong DB top-10 nhưng không có trong DOM top-10, hoặc sai thứ
    tự → lỗi cứng (`hard`).
- R3.3 **QA-2 (last message top-10):** lấy preview last message từ sidebar
  (sender + text + time label), so với message có `MAX(seq)` của thread trong
  DB qua `normalize_for_qa()`:
  1. Áp dụng cùng chuỗi normalize của fetch (unescape `\n` literal, bỏ hậu tố
     reaction, gộp whitespace, NFC).
  2. Match text khi `a in b or b in a` sau normalize (preview bị FB cắt "…").
  3. Match sender (`Customer`/`Page`), và timestamp cùng phút nếu có.
  - Không match text → `hard`. Match text nhưng lệch sender/time → `soft`.
- R3.4 Kết quả ghi `logs/fetch-qa/<page_id>-<ts>.json` gồm: từng thread,
  raw + normalized của cả DB và DOM, verdict, thời gian. Tên tệp ghi vào
  log INFO.
- R3.5 Có ≥ 1 `hard` → `send_proposal_to_telegram(route="FETCH-QA", ...)`
  với bảng diff rút gọn (≤ 10 dòng) và đường dẫn tệp báo cáo; ghi
  `fetch_log.qa_status='failed'`. Chỉ `soft` → `qa_status='warn'`, không gửi.
  Sạch → `qa_status='passed'`.
- R3.6 QA không được vượt 60 s; timeout → `qa_status='timeout'` + Telegram.
- R3.7 QA chỉ đọc DOM; tuyệt đối không gõ/click gửi gì (Rule 10.1, 10.3).

### 4.4 `prd:inbox-decoupled-jobs-001:propose-001` — PROPOSE không dùng browser

- R4.1 `run_reply_cycle` → đổi tên/alias `run_propose_cycle`, **không** dùng
  `@_browser_job`, không mở Playwright, không gọi `scrape_inbox`.
- R4.2 Predicate "cần propose": message `MAX(seq)` của thread có
  `sender='Customer'` **và** chưa có row `telegram_hitl_queue` với
  `thread_id` đó và `payload_json.last_message_seq >= MAX(seq)`. Mỗi đề xuất
  ghi `last_message_seq` vào `payload_json` để chống propose trùng.
- R4.3 Gate độ tươi: chỉ propose khi `fetch_log` mới nhất của page có
  `qa_status IN ('passed','warn')` và `fetched_at` trong 6 giờ. Nếu fetch gần
  nhất `failed`/`timeout` → route trả `{"status":"skipped","reason":"fetch_untrusted"}`.
- R4.4 Chạy daemon thread với `_propose_lock` (giống `_classify_lock`);
  overlap → skipped.
- R4.5 `_sanitize_reply()` bắt buộc trước khi ghi queue; rỗng → log
  `no_reply`, không ghi queue (Rule 10.2).
- R4.6 `run_inbox_cycle` trong `tools/l5_inbox_mas_runner.py` xoá Step 1
  (JIT scrape); `--target-thread "Hung Bui"` vẫn hoạt động cho E2E.

## 5. Yêu cầu phi chức năng

- Mỗi job ghi log riêng theo tag `[FETCH]`, `[FETCH-QA]`, `[CLASSIFY]`,
  `[PROPOSE]`; log DEBUG in Universal ID của thành phần đang chạy.
- Không job nào giữ SQLite write lock quá một batch (commit từng batch).
- Chỉ `[FETCH]` (gồm `[FETCH-QA]`) và `hitl_execution_job` được cầm
  `scheduler_browser`.

## 6. Tiêu chí nghiệm thu (Definition of Done)

- DoD-1 `grep detect_city fb_pipeline/inbox tools/l5_inbox_mas_runner.py` không
  còn call site ghi `users.city` ngoài `l5_fetch_fb_city_classify.py`.
- DoD-2 Trang `/seekers` hiện spinner với user mới fetch, tự tắt sau khi
  `[CLASSIFY]` chạy; user Unknown đã verify không xoay.
- DoD-3 Chạy fetch thật trên page `1548373332058326` tạo tệp
  `logs/fetch-qa/*.json` với `qa_status='passed'`; cố ý sửa 1 message trong DB
  → lần QA sau `failed` và có tin Telegram.
- DoD-4 `[PROPOSE]` và `[FETCH]` chạy chồng thời gian mà không có `Skipped:
  cli_fetch_active` từ PROPOSE và không có lỗi `database is locked`.
- DoD-5 E2E `--target-thread "Hung Bui"` tạo đúng 1 row `telegram_hitl_queue`
  và chạy lần 2 không tạo thêm (chống trùng theo `last_message_seq`).
- DoD-6 Toàn bộ test trong `doc:inbox-decoupled-jobs-test-plan-001` pass.

## 7. Thứ tự triển khai đề xuất

1. `propose-001` (gỡ conflict browser lớn nhất; tiền đề cho fetch một process).
2. `classify-001` (dọn `detect_city` inline; nhỏ, sau khi propose không crawl).
3. `city-spinner-001` (nhìn thấy ngay, độc lập với 1–2).
4. `fetch-qa-001` (cần fetch đã ổn định một process; cân chỉnh
   `normalize_for_qa` nhiều nhất).

## 8. Ma trận thoả mãn (State Matrix)

| PRD ID | Code ID dự kiến | Test ID |
| --- | --- | --- |
| `prd:inbox-decoupled-jobs-001:classify-001` | `code:tool-citydetect-001:*` (mở rộng), `code:inbox-decoupled-jobs-001:fetch-no-classify` | `code:test-decoupled-001:classify` |
| `prd:inbox-decoupled-jobs-001:city-spinner-001` | `code:web-db-003:classification-status`, `code:web-ui-seekers-001:city-spinner` | `code:test-decoupled-001:spinner` |
| `prd:inbox-decoupled-jobs-001:fetch-qa-001` | `code:inbox-fetch-qa-001:{top10,last-message,normalize,report,telegram}` | `code:test-decoupled-001:fetch-qa` |
| `prd:inbox-decoupled-jobs-001:propose-001` | `code:inbox-propose-001:{route,predicate,freshness-gate,dedup}` | `code:test-decoupled-001:propose` |
