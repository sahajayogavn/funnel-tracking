# Test Plan — Inbox Decoupled Jobs

**Universal ID:** `doc:inbox-decoupled-jobs-test-plan-001`
**Verifies:** `prd:inbox-decoupled-jobs-001` / `doc:inbox-decoupled-jobs-001`
**Date:** 2026-09-17

Quy ước:
- Mọi test mới nằm trong `tests/`, mang tag `# code:test-decoupled-001:<component>`.
- Test unit chạy offline với DB tạm (`tmp_path`), không cần Chrome, không cần LLM.
- Test E2E browser **chỉ** nhắm thread "Hung Bui" (Rule 10.1) và **không** gửi tin (Rule 10.3).
- Test nào cần LLM thì auto-skip nếu thiếu `OPENAI_API_BASE`/`OPENAI_API_KEY` (như `test_adk_e2e.py`).

## 0. Lệnh chạy tổng

```bash
.venv/bin/python -m pytest tests/test_decoupled_propose.py tests/test_decoupled_classify.py \
  tests/test_decoupled_fetch_qa.py -v
cd web && npm run build && node ../tests/test_decoupled_spinner.mjs
```

Kiểm tra static (không cần chạy code):

```bash
# DoD-1: không còn detect_city ngoài classify
grep -rn "detect_city" fb_pipeline/inbox tools/l5_inbox_mas_runner.py tools/l5_fetch_fb_messages.py
# Kỳ vọng: 0 dòng (hoặc chỉ tham số default None chưa dùng, có comment deprecation)

# PROPOSE không dùng browser
grep -n "sync_playwright\|scrape_inbox\|_browser_job" tools/l5_inbox_mas_runner.py
grep -n "run_propose_cycle\|run_reply_cycle" -A3 tools/l5_scheduler_routes.py | grep -c "_browser_job"
# Kỳ vọng: runner không import sync_playwright; route propose không có decorator _browser_job
```

## 1. `code:test-decoupled-001:propose` — `tests/test_decoupled_propose.py`

| ID | Tên | Cách làm | Pass khi |
| --- | --- | --- | --- |
| P-01 | Predicate chọn đúng thread | DB tạm: T1 last msg Customer chưa propose; T2 last msg Page; T3 Customer đã propose seq=5 và last seq=5; T4 Customer đã propose seq=5, last seq=7 | Trả về `[T1, T4]`, theo `inbox_sort_index` |
| P-02 | Dedup theo `last_message_seq` | Chạy propose 2 lần liên tiếp trên cùng DB (mock MAS trả text cố định, mock Telegram) | `telegram_hitl_queue` có đúng 1 row/thread; `payload_json.last_message_seq` = MAX(seq) |
| P-03 | Khách nhắn thêm → propose lại | Sau P-02 insert message Customer seq+1 cho T1, chạy lại | Có row mới cho T1 với seq mới; row cũ giữ nguyên |
| P-04 | Freshness gate: fetch failed | `fetch_log` mới nhất `qa_status='failed'` | Route trả `{"status":"skipped","reason":"fetch_untrusted"}`, không gọi MAS |
| P-05 | Freshness gate: fetch quá 6h | `fetched_at = now - 7h`, `qa_status='passed'` | skipped `fetch_untrusted` |
| P-06 | Freshness gate: fetch cũ chưa có QA | `qa_status IS NULL`, `fetched_at` 1h trước | Chạy bình thường (tương thích ngược) |
| P-07 | Không cầm browser lock | Tạo lock file `inbox_fetch_cli` giả (dùng API `l2_activity_lock`) rồi chạy propose | Không trả `cli_fetch_active`; hoàn thành bình thường |
| P-08 | Overlap tick bị skip | Giữ `_propose_lock` từ thread test, gọi route | `{"status":"skipped","reason":"propose_in_progress"}` |
| P-09 | Sanitizer chặn reasoning leak | Mock MAS trả `"**Crafting a reply…"` | Không có row queue; log chứa `no_reply` |
| P-10 | Không SQLite lock khi chạy chồng fetch | Thread A giữ transaction ghi `messages` 2 s; thread B chạy propose (busy_timeout mặc định) | B hoàn thành, không `database is locked` |
| P-11 | E2E Hung Bui (cần LLM) | `l5_inbox_mas_runner.py --page-id 1548373332058326 --target-thread "Hung Bui" --max-threads 1` | 1 row queue route `INBOX_REPLY`; không mở Chrome (kiểm tra không có lock `scan_inbox` mới); chạy lần 2 → 0 row mới |

## 2. `code:test-decoupled-001:classify` — `tests/test_decoupled_classify.py`

| ID | Tên | Cách làm | Pass khi |
| --- | --- | --- | --- |
| C-01 | Fetch không ghi city | Gọi `persist_thread_record` với user mới (fixture thread record) | `users.city IS NULL`, `program_code IS NULL`, `classification_verified_at IS NULL` |
| C-02 | Fetch không xoá city cũ | User đã có `city='Hà Nội'`, verified; fetch lại thread với tin Customer mới | `city` vẫn `'Hà Nội'`; `last_interaction` tăng → predicate stale = true |
| C-03 | Predicate stale ba trạng thái | Ba user: NULL verified; verified < last_interaction; verified ≥ last_interaction | `count_stale_users` = 2 |
| C-04 | CLASSIFY là chủ ghi duy nhất | `grep -rn "UPDATE users SET.*city" fb_pipeline tools adk_agents` | Chỉ có trong `l5_fetch_fb_city_classify.py` / `contracts/l1_city_llm.py` |
| C-05 | Unknown là kết luận, không phải mặc định | Mock LLM trả Unknown cho 1 user | `city='Unknown'` **và** `classification_verified_at` được set |
| C-06 | Live (cần LLM) | Fetch Hung Bui → chờ 1 tick `[CLASSIFY]` | user Hung Bui có `classification_verified_at ≥ last_interaction` |

## 3. `code:test-decoupled-001:spinner` — `tests/test_decoupled_spinner.mjs` + static

| ID | Tên | Cách làm | Pass khi |
| --- | --- | --- | --- |
| S-01 | SQL status đúng | Gọi `getSeekers()` trên DB tạm với 3 user (NULL verified / stale / done-Unknown / done-city) | `classificationStatus` lần lượt `pending`, `pending`, `unknown`, `done` |
| S-02 | Không so timestamp trong JS | `grep -n "classification_verified_at" web/src/lib/*.ts` | Chỉ xuất hiện trong chuỗi SQL, không có `new Date(` cạnh nó |
| S-03 | Render pending có spinner | Render `SeekersTable` với seeker `pending` + city `'Hà Nội'` (React Testing Library hoặc static HTML snapshot) | `<td>` thứ 5 chứa badge "Hà Nội" **và** phần tử `.spinner[aria-label]` |
| S-04 | Render unknown không spinner | seeker `unknown` | badge xám "—", không có `.spinner` |
| S-05 | Render done | seeker `done` | Giống hiện tại (`getCityStyle`) |
| S-06 | Reduced motion | `globals.css` | Có `@media (prefers-reduced-motion: reduce)` tắt animation của `.spinner` |
| S-07 | Filter City không gộp pending vào Unknown | Lọc `city='Hà Nội'` với seeker pending + city Hà Nội | Seeker vẫn hiện |
| S-08 | Tự tắt spinner (thủ công) | Mở `/seekers` sau fetch Hung Bui, đợi tick CLASSIFY | Spinner biến mất ≤ 90 s không cần reload thủ công |
| S-09 | Build sạch | `cd web && npm run build` | 0 lỗi type; `lint` không cảnh báo mới |

## 4. `code:test-decoupled-001:fetch-qa` — `tests/test_decoupled_fetch_qa.py`

### 4.1 `normalize_for_qa` (thuần, nhiều case — đây là phần quan trọng nhất)

| ID | Input DB | Input DOM (preview) | Kỳ vọng |
| --- | --- | --- | --- |
| N-01 | `"Chào chị, lớp bắt đầu 19h"` | `"Bạn: Chào chị, lớp bắt đầu 19h"` | match, sender DOM = Page |
| N-02 | `"Em ở Hà Nội ạ, em muốn đăng ký lớp thiền buổi tối"` | `"Em ở Hà Nội ạ, em muốn đăng ký lớp thiền…"` | match (prefix) |
| N-03 | `"dòng 1\ndòng 2"` | `"dòng 1 dòng 2"` | match (gộp whitespace) |
| N-04 | `"dòng 1\\ndòng 2"` (literal) | `"dòng 1 dòng 2"` | match (unescape) |
| N-05 | `"Cảm ơn ạ"` | `"Cảm ơn ạ 👍"` (reaction) | match (strip reaction) |
| N-06 | `"Cảm ơn ạ"` | `"Cảm ơn anh nhiều ạ"` | **không** match → hard |
| N-07 | `"ok"` | `"ok, em sẽ đến"` | **không** match (chuỗi ngắn < 8 ký tự phải bằng toàn bộ) |
| N-08 | `"[attachment]"` | `""` | pass (cả hai là attachment/rỗng) |
| N-09 | `"Em hỏi lịch"` | `""` | soft |
| N-10 | `"Hà Nội"` (NFD) | `"Hà Nội"` (NFC) | match (NFC) |
| N-11 | Ký tự hoa/thường | `"CHÀO"` vs `"chào"` | match (casefold) |

### 4.2 QA-1 / QA-2 với DOM giả

Dùng fixture `visible_threads` (list dict giống Stage 1 trả về) thay cho Playwright.

| ID | Tên | Cách làm | Pass khi |
| --- | --- | --- | --- |
| Q-01 | Top-10 khớp hoàn toàn | DOM = DB | `summary.hard=0, soft=0`, `qa_status='passed'`, không gọi Telegram |
| Q-02 | Thread mới sau fetch | DOM có thread mới ở rank 1, time label > `fetch_started_at` | 1 `soft`, `qa_status='warn'`, không Telegram |
| Q-03 | DB thiếu thread | DOM rank 3 không có trong DB | `hard`, `qa_status='failed'`, Telegram gọi 1 lần với route `FETCH-QA` |
| Q-04 | Lệch thứ tự > 1 | Hoán vị rank 2↔5 | `hard` |
| Q-05 | Lệch thứ tự 1 vị trí | Hoán vị rank 2↔3 | `soft` (dung sai) |
| Q-06 | Last message sai | DB last msg của rank 1 khác DOM preview (N-06) | `hard`, báo cáo JSON có `dom_raw/dom_norm/db_raw/db_norm` |
| Q-07 | So bằng ID không phải tên | Hai thread cùng `thread_name`, khác PSID | Mapping đúng theo `canonical_thread_id`, không hard giả |
| Q-08 | Báo cáo được ghi | Bất kỳ | File `logs/fetch-qa/<page>-<ts>.json` tồn tại, `fetch_log.qa_report_path` trỏ đúng |
| Q-09 | Timeout | Mock DOM reader sleep 70 s | Trả về ≤ 61 s, `qa_status='timeout'`, Telegram gọi |
| Q-10 | Không gõ/click | Mock page object ghi lại mọi call | Không có `click`/`type`/`fill`/`press` ngoài scroll sidebar |
| Q-11 | `--skip-qa` | CLI fetch với cờ | `qa_status='skipped'`, không đọc DOM |
| Q-12 | Chạy trong lock | Kiểm tra `read_activity('scheduler_browser')` trong lúc QA chạy (mock fetch cycle) | Lock vẫn được giữ suốt QA, nhả sau QA |
| Q-13 | HITL exec bỏ qua route FETCH-QA | Row queue route `FETCH-QA` status pending, giả lập 👍 | `hitl_execution_job` không mở thread, đánh dấu `acknowledged` |

### 4.3 Live (thủ công, page `1548373332058326`)

| ID | Cách làm | Pass khi |
| --- | --- | --- |
| L-01 | `l5_fetch_fb_messages.py --action fetch_messages --cdp --page-id 1548373332058326 --time-range 7d` | Log có `[FETCH-QA] … qa_status=passed`, file JSON có 10 thread |
| L-02 | Sửa tay `messages.content` của thread rank 1 (backup DB trước theo `memory/agent_memory/backups/`), chạy lại fetch | `failed` + tin Telegram có tên thread và đường dẫn báo cáo; khôi phục DB sau test |
| L-03 | Đo thời gian QA trong log | `duration_s < 15` với preview-only |

## 5. Regression bắt buộc chạy lại

```bash
.venv/bin/python -m pytest tests/test_l5_inbox_mas_runner.py tests/test_l4_inbox_persistence.py \
  tests/test_parallel_fetch*.py tests/test_mas_recommend.py -v
.venv/bin/python -m pytest tests/test_e2e_mas_strategy_hung_bui.py -v   # cần LLM
```

Snapshot gate (Rule 10.1): sau bất kỳ thay đổi nào ở `l3_pipeline.py`/`l3_parallel_fetch.py`
(bước classify-001, fetch-qa-001) phải chạy lại script Hung Bui và diff
`tests/hungbui_test_output.json` — 0 khác biệt.

## 6. Ma trận thoả mãn

| PRD component | Test IDs |
| --- | --- |
| `prd:inbox-decoupled-jobs-001:propose-001` | P-01…P-11 |
| `prd:inbox-decoupled-jobs-001:classify-001` | C-01…C-06 |
| `prd:inbox-decoupled-jobs-001:city-spinner-001` | S-01…S-09 |
| `prd:inbox-decoupled-jobs-001:fetch-qa-001` | N-01…N-11, Q-01…Q-13, L-01…L-03 |
