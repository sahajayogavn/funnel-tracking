# Design: Inbox Decoupled Jobs — Fetch / Classify / Fetch-QA / Propose

**Universal ID:** `doc:inbox-decoupled-jobs-001`
**Satisfies:** `prd:inbox-decoupled-jobs-001` — [`../PRDs/prd-inbox-decoupled-jobs.md`](../PRDs/prd-inbox-decoupled-jobs.md)
**Related:** `doc:inbox-fetch-pipeline-001`, `code:inbox-activity-lock-001`, `code:tool-citydetect-001`
**Date:** 2026-09-17

## 1. Nguyên tắc

> **Cái gì đụng Chrome thì xếp hàng qua `scheduler_browser` lock. Cái gì chỉ
> đụng SQLite + LLM thì chạy tự do và phát hiện việc bằng predicate trên DB.**

SQLite là hàng đợi. Không thêm broker. Mỗi job có một *predicate* (câu SQL
trả lời "còn việc không?"), một *lock trong process* chống chồng tick, và một
*chủ sở hữu duy nhất* cho các cột nó ghi.

| Job | Tag log | Browser | Predicate | Cột sở hữu | Lock |
| --- | --- | --- | --- | --- | --- |
| Fetch | `[FETCH]` | có | scheduler tick / CLI | `threads.*`, `messages.*`, `users.{thread_name,last_interaction,phone,email}` | `scheduler_browser` / `inbox_fetch_cli` |
| Fetch-QA | `[FETCH-QA]` | có (read-only DOM) | chạy ngay sau Fetch, cùng lock | `fetch_log.qa_status` | thừa kế của Fetch |
| Classify | `[CLASSIFY]` | không | `STALE_CLASSIFICATION_SQL` | `users.{city,program_code,classification_verified_at}` | `_classify_lock` |
| Propose | `[PROPOSE]` | không | "last msg là Customer & chưa có đề xuất cho seq đó" | `telegram_hitl_queue` (insert) | `_propose_lock` |
| HITL exec | `[HITL]` | có | `telegram_hitl_queue.status='approved'` | `telegram_hitl_queue.status` | `scheduler_browser` |

```
                     ┌────────────────────┐
   Chrome ──────────►│ [FETCH]            │──── threads / messages ────┐
   scheduler_browser │ crawl only         │                            │
                     └─────────┬──────────┘                            ▼
                               │ same lock                 ┌───────────────────────┐
                               ▼                           │    frankensqlite.db   │
                     ┌────────────────────┐   read top-10  │ users.classification_ │
                     │ [FETCH-QA]         │◄───────────────│   verified_at         │
                     │ DOM read-only      │                │ fetch_log.qa_status   │
                     └───┬────────────────┘                │ telegram_hitl_queue   │
                         │ hard fail → Telegram HITL       └───┬─────────────┬─────┘
                                                               │ stale?      │ unreplied?
                                                               ▼             ▼
                                                    ┌──────────────┐  ┌──────────────┐
                                                    │ [CLASSIFY]   │  │ [PROPOSE]    │
                                                    │ LLM, no      │  │ LLM, no      │
                                                    │ browser      │  │ browser      │
                                                    └──────────────┘  └──────┬───────┘
                                                                             ▼
                                              telegram_hitl_queue ──👍──► hitl_execution_job (browser)
```

## 2. `prd:…:classify-001` — CLASSIFY là chủ sở hữu duy nhất của city/program

### Hiện trạng
- `persist_thread_record(conn, record, detect_city, …)` (`fb_pipeline/inbox/l3_pipeline.py:181`) và
  `run_inbox_cycle` (`tools/l5_inbox_mas_runner.py:106`) còn truyền `detect_city` → fetch ghi
  `users.city` heuristic inline.
- `[CLASSIFY]` (`tools/l5_scheduler_routes.py:438`) đã đúng mô hình: daemon thread,
  `_classify_lock`, commit từng batch, predicate `STALE_CLASSIFICATION_SQL`.

### Thay đổi
1. Xoá tham số `detect_city` khỏi `persist_thread_record` / `scrape_inbox` / `run_parallel_fetch`
   (hoặc giữ tham số với default `None` một release để không vỡ caller, nhưng không gọi).
   Universal ID: `code:inbox-decoupled-jobs-001:fetch-no-classify`.
2. Với user mới: `INSERT … city = NULL`. Không ghi `'Unknown'` từ fetch — `'Unknown'` là
   *kết luận* của CLASSIFY, còn `NULL` là *chưa chạy*. Phân biệt này là nền cho spinner.
3. Không cần "đánh thức": fetch cập nhật `users.last_interaction` khi có tin Customer mới →
   predicate stale tự đúng ở tick sau. Tick CLASSIFY ≤ 60 s.

### Vì sao không gộp lại vào fetch
Retrospective 2026-09-17 (comment trong `run_classify_cycle`): LLM pass dài hơn crawl và
chặn mọi route browser trong khi Chrome nằm không. Tách xong thì fetch 110 phút không phụ
thuộc LLM endpoint có sống hay không.

## 3. `prd:…:city-spinner-001` — Spinner cột City

### Dữ liệu trước, UI sau
UI hiện chỉ có `city: string`; không phân biệt được "chưa chạy" và "đã chạy ra Unknown".
Thêm `classificationStatus` tính trong SQL — **không** kéo về JS để so `Date`, vì
`last_interaction` là giờ local Facebook, còn `classification_verified_at` cũng được ghi
theo local (xem comment `code:tool-citydetect-001:stale-predicate`).

```sql
-- web/src/lib/queries.ts  (code:web-db-003:classification-status)
CASE
  WHEN u.classification_verified_at IS NULL
    OR u.last_interaction > u.classification_verified_at THEN 'pending'
  WHEN u.city IS NULL OR u.city = 'Unknown'              THEN 'unknown'
  ELSE 'done'
END AS classificationStatus
```

Lưu ý: `getSeekers` hiện gộp `city` bằng `MAX(CASE …)` qua nhiều nguồn (`users`, `ad_profiles`).
`classificationStatus` chỉ theo `users` (nguồn CLASSIFY ghi), nên khi `pending` mà đã có city
từ nguồn khác thì vẫn hiện badge + spinner.

### UI (`code:web-ui-seekers-001:city-spinner`)

| status | Render |
| --- | --- |
| `pending` | badge hiện có (nếu `city` khác Unknown) + `<span class="spinner" aria-label="Đang suy luận">` |
| `unknown` | badge xám `—` |
| `done` | như hiện tại (`getCityStyle`) |

- Spinner: `@keyframes spin` trong `globals.css`, 14px, `border-top-color` theo màu badge,
  `@media (prefers-reduced-motion: reduce)` → thay bằng dấu `…` tĩnh.
- Bảng đã là client component; đảm bảo có refresh ≤ 30 s (polling hiện có hoặc
  `router.refresh()`), để spinner tắt dần theo từng batch CLASSIFY commit.
- Filter City: `pending` không được rơi vào bucket `Unknown` khi lọc — filter theo
  `city` string như cũ, nhưng badge tooltip nói rõ đang chờ.

## 4. `prd:…:fetch-qa-001` — QA gate cuối fetch

### Vị trí
Bên **trong** `run_fetch_cycle` (và CLI fetch) sau khi orchestrator trả về, **trước** khi
`scheduler_browser_cycle` nhả lock. Nếu tách ra process riêng, một fetch khác có thể xen
giữa và làm sai kết quả so sánh. Module mới: `fb_pipeline/inbox/l3_fetch_qa.py`.

### QA-1: thứ tự top-10 (`code:inbox-fetch-qa-001:top10`)
- DOM: đọc 10 thread đầu sidebar bằng cùng hàm discovery Stage 1 (không click thread);
  thu `visible_thread` → `_compute_thread_id(page_id, visible_thread, name, preview)` để có
  ID canonical, so bằng ID chứ không so tên.
- DB: `SELECT id, thread_name FROM threads WHERE page_id=? ORDER BY inbox_sort_index LIMIT 10`.
- Verdict:
  - DOM có, DB không có, và thread đó có time label mới hơn `fetch_started_at` → `soft`
    (khách vừa nhắn sau khi fetch bắt đầu).
  - DB top-10 có thread không nằm trong DOM top-10, hoặc thứ tự lệch > 1 vị trí → `hard`.

### QA-2: last message top-10 (`code:inbox-fetch-qa-001:last-message`)
Đây là chỗ khó nhất: DB lưu message đã qua dedup (`bug-inbox-message-dedup-001`: gộp `\n`
literal, bỏ reaction muộn), còn sidebar preview bị FB cắt "…" và có tiền tố "Bạn: ".

```python
# code:inbox-fetch-qa-001:normalize
def normalize_for_qa(text: str) -> str:
    t = unicodedata.normalize("NFC", text or "")
    t = t.replace("\\n", "\n")                 # literal \n như fetch làm
    t = strip_reaction_suffix(t)               # tái dùng hàm của dedup
    t = re.sub(r"^(Bạn|You):\s*", "", t)       # tiền tố page ở preview
    t = t.rstrip("…").rstrip("...")
    return re.sub(r"\s+", " ", t).strip().casefold()
```

Match khi: `db in dom or dom in db` (sau normalize, và chuỗi ngắn hơn ≥ 8 ký tự hoặc bằng
toàn bộ) **và** sender khớp (`Bạn:` ⇒ Page) **và** time label cùng phút nếu parse được.
- Text không chứa nhau → `hard`.
- Text khớp, sender/time lệch → `soft`.
- Preview rỗng (attachment/sticker) và DB last message cũng là attachment marker → `pass`;
  chỉ một bên rỗng → `soft`.

Bắt đầu bằng preview sidebar (≈ 0 click, < 10 s). Chỉ khi tỉ lệ `soft` do preview cắt quá
cao mới nâng cấp thành mở thread (đắt ~10×) — quyết định sau khi có 1 tuần số liệu.

### Báo cáo & cảnh báo (`code:inbox-fetch-qa-001:report`, `:telegram`)
- `logs/fetch-qa/<page_id>-<YYYYmmdd-HHMMSS>.json`:
  `{page_id, fetch_started_at, finished_at, qa1:[{rank, dom_id, db_id, verdict}],
    qa2:[{thread_id, dom_raw, dom_norm, db_raw, db_norm, sender_dom, sender_db, verdict}],
    summary:{hard, soft, pass}, duration_s}`.
- `fetch_log` thêm cột `qa_status TEXT` (`passed|warn|failed|timeout|skipped`) và
  `qa_report_path TEXT` qua `_ensure_column`.
- `hard ≥ 1` hoặc timeout → `send_proposal_to_telegram(route="FETCH-QA", thread_id=None,
  proposed_text=<bảng diff ≤ 10 dòng>, payload={"report": path})`. Đây là thông báo, không
  phải đề xuất hành động; `hitl_execution_job` phải bỏ qua route này.
- Timeout QA 60 s (`asyncio`/`threading.Timer`), không được kéo dài thời gian cầm lock.
- QA **chỉ đọc** DOM (Rule 10.1/10.3).

## 5. `prd:…:propose-001` — PROPOSE không dùng browser

### Hiện trạng
`run_inbox_cycle` = attach CDP → `scrape_inbox(…, 50)` → `find_unreplied_threads` → MAS →
Telegram. Cầm browser trong lúc gọi LLM và là crawler thứ hai.

### Thay đổi
1. `run_reply_cycle` → `run_propose_cycle` (`code:inbox-propose-001:route`), giữ alias tên cũ
   cho scheduler config. Không `@_browser_job`, không Playwright, không `scrape_inbox`.
   Daemon thread + `_propose_lock`, giống `run_classify_cycle`.
2. `run_inbox_cycle` xoá Step 1 (JIT scrape) và mọi `sync_playwright()`. Giữ
   `--target-thread "Hung Bui"` cho E2E.
3. Predicate (`code:inbox-propose-001:predicate`), mở rộng `find_unreplied_threads`:

```sql
WITH last AS (
  SELECT thread_id, MAX(seq) AS max_seq FROM messages GROUP BY thread_id
),
lm AS (
  SELECT m.thread_id, m.sender, m.seq FROM messages m JOIN last ON last.thread_id=m.thread_id AND last.max_seq=m.seq
),
proposed AS (
  SELECT thread_id, MAX(CAST(json_extract(payload_json,'$.last_message_seq') AS INTEGER)) AS seq
  FROM telegram_hitl_queue WHERE route='INBOX_REPLY' GROUP BY thread_id
)
SELECT lm.thread_id FROM lm JOIN threads t ON t.id=lm.thread_id
LEFT JOIN proposed p ON p.thread_id=lm.thread_id
WHERE t.page_id=? AND lm.sender='Customer' AND (p.seq IS NULL OR p.seq < lm.seq)
ORDER BY t.inbox_sort_index LIMIT ?;
```

4. Mỗi đề xuất ghi `payload_json.last_message_seq` (`code:inbox-propose-001:dedup`) → chạy
   lại không tạo trùng; khách nhắn thêm → seq tăng → được propose lại.
5. Gate độ tươi (`code:inbox-propose-001:freshness-gate`): `fetch_log` mới nhất của page phải
   có `qa_status IN ('passed','warn')` (hoặc NULL cho fetch cũ chưa có QA) và
   `fetched_at` ≤ 6 h. Không đạt → `{"status":"skipped","reason":"fetch_untrusted"}`.
   Đây là chỗ Fetch-QA và Propose khớp nhau: QA fail thì đề xuất trả lời không được sinh
   trên lịch sử thiếu tin nhắn.
6. `_sanitize_reply()` bắt buộc; rỗng → `no_reply`, không ghi queue.

### Tương tác với các lock
- PROPOSE không cầm và không chờ `scheduler_browser`/`inbox_fetch_cli`. Chạy chồng FETCH là
  bình thường. SQLite: PROPOSE chỉ INSERT vào `telegram_hitl_queue` từng dòng, commit ngay,
  không giữ write lock quá 1 câu lệnh.
- HITL exec (browser) vẫn đi qua `@_browser_job("[HITL]")` như cũ.

## 6. Thứ tự triển khai

1. **propose-001** — gỡ crawler thứ hai và conflict browser lớn nhất.
2. **classify-001** — dọn `detect_city` inline (nhỏ, sau khi propose không crawl).
3. **city-spinner-001** — độc lập, nhìn thấy ngay.
4. **fetch-qa-001** — cần fetch đã ổn định một process; cân chỉnh `normalize_for_qa` nhiều nhất.

Mỗi bước có thể giao cho một agent riêng; ranh giới file:

| Bước | File được sửa | Không đụng |
| --- | --- | --- |
| propose-001 | `tools/l5_scheduler_routes.py` (route), `tools/l5_inbox_mas_runner.py`, `adk_agents/tools/l5_seeker_tools.py`, `tools/l5_telegram_hitl.py` (payload seq) | `fb_pipeline/inbox/*`, `web/` |
| classify-001 | `fb_pipeline/inbox/l3_pipeline.py`, `l3_parallel_fetch.py`, `tools/l5_fetch_fb_messages.py` | `l5_fetch_fb_city_classify.py` logic LLM, `web/` |
| city-spinner-001 | `web/src/lib/{types,queries}.ts`, `web/src/components/seekers-table.tsx`, `web/src/app/globals.css` | mọi file Python |
| fetch-qa-001 | mới `fb_pipeline/inbox/l3_fetch_qa.py`, `tools/l5_scheduler_routes.py` (`run_fetch_cycle`), `tools/l5_fetch_fb_messages.py` (`--skip-qa`), `l4_sqlite_store.py` (`fetch_log` cols) | MAS/LLM, `web/` |
