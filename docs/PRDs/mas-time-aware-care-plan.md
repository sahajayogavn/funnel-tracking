# PRD: MAS nhận thức thời gian & chăm sóc chủ động có nhịp

**Universal ID:** `prd:mas-time-aware-001`
**Audit gốc:** `doc:mas-execution-audit-001` — [`../report/mas-execution-audit-2026-09-17.md`](../report/mas-execution-audit-2026-09-17.md)
**Status:** Có implementation P0–P3 trong workspace; chưa nghiệm thu lại đầy đủ. Kế hoạch bổ sung A–F ở §7 chưa triển khai.
**Date:** 2026-09-17
**Requested by:** operator (page `1548373332058326`)
**Builds on:** `prd:inbox-decoupled-jobs-001`, `code:tool-inbox-mas-001`, `code:tool-scheduler-001`, `code:agent-mas-001`

## 0. Quyết định đã chốt với chủ dự án

1. MAS **được phép** viết "CLB đã nhận được thông tin đăng ký" khi thông tin từ
   tin đăng ký thật đã được fetch pipeline lưu vào `users` (hiển thị ở
   `/seekers`). Banner không cung cấp bằng chứng này; phải xét hồ sơ/lịch sử.
   Không đòi duyệt tay mới được xác nhận đã tiếp nhận; không suy ra đã xếp lớp,
   giữ chỗ hay tham dự chỉ từ việc tiếp nhận.
2. MAS Nhắc lịch chạy **một lần mỗi ngày trong khung 08:00–09:00**, không chạy
   theo mốc T-24h/T-2h trước từng buổi.
3. Mọi route proactive chỉ tạo **thông báo** lên Telegram / `/queues`; draft cho
   từng seeker chỉ sinh sau khi quản trị viên bấm "Mở phiên". Draft không bao
   giờ được automation gửi (giữ nguyên UC-05/UC-06).
4. Tất cả 7 hướng trong audit §5 đều được duyệt; thứ tự triển khai theo §2.

## 1. Mục tiêu đo được

| Mục tiêu | Hiện tại | Sau P1 | Sau P3 |
| --- | --- | --- | --- |
| Đề xuất reply bị reject / clear | ~100% | < 40% | < 20% |
| Thread "unreplied" giả (banner/closer) đưa vào LLM | 36% + ~25% | 0% | 0% |
| LLM calls route inbox / ngày | ~244 | < 60 | < 40 |
| Token knowledge / call | ~20K | ~20K | < 6K |
| Đề xuất được approve / tuần | 0 | > 0 | ≥ 10 |
| Seeker đã ghi danh nhận nhắc lịch trước buổi học | 0 | 0 | ≥ 80% (qua phiên do người mở) |

## 2. Lộ trình — 4 phase, mỗi phase có gate

```
P0 Dữ liệu sạch ──► P1 Time Gate + NO_REPLY ──► P2 Proactive (Nhắc lịch, Sau buổi, SLA) ──► P3 Chi phí & chất lượng
   (2 ngày)            (2 ngày)                     (3–4 ngày)                                   (2 ngày)
```

Không phase nào được bắt đầu khi gate của phase trước chưa xanh.

---

## P0 — Dọn tầng dữ liệu (điều kiện cần)

### P0.1 `messages.kind` — tách banner ra khỏi tin khách
- **ID:** `code:inbox-msg-kind-001`
- **Thay đổi:**
  - Thêm cột `messages.kind TEXT NOT NULL DEFAULT 'message'` với miền
    `message | system_banner | reaction | attachment | ad_source`.
  - Parser `fb_pipeline/browser/inbox/thread_detail_parser.py` gắn `kind` ngay
    lúc trích xuất, dựa trên danh sách mẫu cố định (không dùng LLM):
    `^\S.* replied to an ad\.$`, `^\S.* replied to a post\. View post$`,
    `^Bạn đang phản hồi bình luận`, `:::REACTION_\w+:::`, `sent an attachment`,
    `--- [AD SOURCE]`.
  - Migration một lần: `UPDATE messages SET kind=... WHERE content LIKE ...`
    cho dữ liệu cũ; ghi số dòng thay đổi vào `logs/`.
  - `find_unreplied_threads`, `get_thread_messages` (khi dựng prompt) và
    `last_interaction` chỉ xét `kind='message'`.
- **Gate:** truy vấn "thread unreplied có tin cuối là banner" trong audit §6 trả
  về **0**; snapshot Hung Bui vẫn 100% nhất quán (rule 10.1).

### P0.2 `messages.message_at` — timestamp tuyệt đối ISO
- **ID:** `code:inbox-msg-abs-time-001`
- **Thay đổi:**
  - Thêm cột `message_at TEXT` (ISO local `YYYY-MM-DD HH:MM:SS`).
  - Tái dùng bộ resolve đang tạo ra `users.last_interaction` (đã đúng cho
    `Mon 11:10 AM` → `2026-09-14 11:10:00`) để điền cho từng message tại thời
    điểm fetch, lấy `recorded_at` làm mốc quy chiếu cho chuỗi tương đối.
  - Backfill dữ liệu cũ theo cùng logic; dòng không resolve được thì để NULL và
    dùng `timestamp` (recorded_at) làm fallback có gắn cờ `approx=1`.
- **Gate:** ≥ 95% message có `message_at`; sai lệch với `users.last_interaction`
  của cùng thread ≤ 1 phút trên mẫu 50 thread.

### P0.3 Sửa literal `\n`
- **ID:** `code:tool-inbox-mas-001:newline-fix`
- `tools/l5_inbox_mas_context.py:38,43`, `tools/l5_inbox_mas_runner.py:218-227`
  đổi `"\\n"` → `"\n"`. Test: tin Telegram và prompt không chứa chuỗi `\n` chữ.

### P0.4 Tách stage gate khỏi vòng tạo draft
- **ID:** `code:stage-gate-decouple-001`
- Bỏ `evaluate_stage_gate` khỏi `run_inbox_cycle`. Stage gate chỉ chạy khi có
  một trong hai bằng chứng: (a) tin khách `kind='message'` chứa SĐT hợp lệ và
  `users.program_code` khác NULL; (b) bản ghi attendance từ P2.2.
- **Gate:** không còn `mas_decisions.route='stage_gate'` phát sinh từ chu kỳ
  MAS reply.

### P0.5 Nạp `events` từ `su-kien.md`
- **ID:** `code:events-import-001`
- CLI `tools/l5_events_sync.py --from memory/agent_memory/su-kien.md` parse các
  mục "Sự kiện sắp diễn ra" vào bảng `events`; chạy trong scheduler mỗi ngày.
  Đồng thời đánh dấu `su-kien.md` mục đã qua để P3 loại khỏi knowledge.
- **Gate:** `SELECT COUNT(*) FROM events WHERE event_date >= date('now')` > 0
  khi file có sự kiện tương lai.

---

## P1 — Time Gate + Conversation State + NO_REPLY

### P1.1 `conversation_state()` — thuần Python, chạy trước LLM
- **ID:** `code:inbox-conv-state-001`, file mới `fb_pipeline/contracts/l1_conversation_state.py`
- Input: danh sách message (`kind='message'`) + `now` (Asia/Ho_Chi_Minh).
- Output:

  | Trường | Miền / ý nghĩa |
  | --- | --- |
  | `state` | `open_question` · `registered_awaiting_confirm` · `closed_by_human` · `closer_only` · `stale` · `no_customer_message` |
  | `last_customer_at`, `last_human_page_at` | ISO; `Auto_Page` **không** tính là người thật |
  | `age_hours` | `now - last_customer_at` |
  | `has_phone`, `phone` | regex SĐT VN trên tin khách thật |
  | `is_closer` | tin khách cuối khớp bộ từ kết thúc (`cảm ơn|cám ơn|vâng|dạ|ok|oke|okee|tks|thanks|👍|🙏` ± dấu câu, ≤ 6 từ) |

- Luật quyết định (`decide_inbox_action`):

  | Điều kiện | Hành động |
  | --- | --- |
  | `no_customer_message` | skip |
  | `is_closer` **và** có tin `Page` người thật ngay trước | `closed_by_human` → skip; tuỳ chọn đề xuất reaction ❤️ qua route Reactor |
  | `is_closer`, không có tin Page trước, `age_hours ≤ 24` | reply ngắn (cho phép) |
  | `has_phone`, chưa có tin `Page` người thật sau đó | `registered_awaiting_confirm` → reply **và** bật SLA (P2.3) |
  | `age_hours ≤ 24` | reply |
  | `24 < age_hours ≤ 168` | reply với cờ `LATE` (prompt yêu cầu nhận lỗi trả lời muộn, không giả vờ vừa nhận) |
  | `age_hours > 168` | không reply; chuyển candidate sang route warm-up (đã có decision core chống spam 7 ngày) |

- Mọi quyết định ghi `mas_decisions(route='inbox_gate', decision, reason,
  payload={state, age_hours})` để review được.

### P1.2 Inject thời gian vào prompt
- **ID:** `code:agent-mas-001:time-context`
- Session state thêm `now_context`: `"Bây giờ là Thứ Năm 17/09/2026 14:05
  (Asia/Ho_Chi_Minh)"`. Mỗi dòng hội thoại dựng dạng
  `[2026-09-14 11:10 | Customer] Xin trân trọng cám ơn !`.
- Thêm vào instruction của `Responder` và `BatchInboxAgent` mục **Time
  Awareness**: dùng `now_context` để suy ra "hôm nay/ngày mai/Chủ Nhật này" khi
  nói về lịch lớp; nếu `LATE` phải mở đầu bằng lời xin lỗi ngắn.

### P1.3 Sentinel `[NO_REPLY: <lý do>]`
- **ID:** `code:agent-mas-001:no-reply-sentinel`
- Thêm vào schema output (single + batch): `reply_text` có thể là
  `[NO_REPLY: closer]`, `[NO_REPLY: already_answered]`, `[NO_REPLY: stale]`.
  Runner coi như `no_reply`, ghi `mas_decisions`, **không** tạo action_queue.
- Sửa câu ép *"If the last message is from the Customer: Respond directly"* thành
  *"…respond only if there is an unserved question or request"*.
- Cập nhật `adk_agents/inbox_mas.evalset.json` thêm 4 ca: closer đã đóng, banner,
  tin 20 ngày tuổi, đăng ký có SĐT (kỳ vọng xác nhận đã nhận).

### P1.4 Chống duplicate rỗng
- `enqueue_action` từ chối `action_text` rỗng; `has_active_proposal` kiểm tra
  theo `(target_id, queue_type, status IN (pending, approved))` **trước** khi gọi
  LLM chứ không phải sau (tiết kiệm call).

### Gate P1
- Chạy `--once --max-threads 20` trên DB hiện tại: ≥ 60% thread bị gate skip
  với lý do đúng (đối chiếu tay 20 ca); 0 đề xuất cho banner/closer đã đóng.
- Ca Quang Chiến, Omi Đinh, Hoàng Yến (#251, #227, #241) đều ra `skip`/`stale`.
- `tests/test_conversation_state.py` (`code:test-validation-001:conv-state`)
  ≥ 15 case, 100% pass.

---

## P2 — Proactive có nhịp (thông báo trước, draft sau)

### P2.1 MAS Nhắc lịch lớp — chạy 08:00–09:00 hằng ngày
- **ID:** `code:route-class-reminder-001`, UC mới **UC-11**
- **Nguồn lịch:** parse `lop-hoc.md` thành cấu trúc
  `{program_code, city, weekday, time, address|zoom, zalo_url}` (file mới
  `fb_pipeline/contracts/l1_class_schedule.py`; unit test khoá cứng 8 lớp hiện
  có). `program_code` phải khớp giá trị mà `[CLASSIFY]` đã ghi vào
  `users.program_code`.
- **Job:** `schedule.every().day.at("08:30")` trong `tools/l5_scheduler.py`
  (cấu hình `--reminder-time`, mặc định 08:30, chấp nhận 08:00–09:00).
  1. Tìm các lớp diễn ra trong cửa sổ `[now, now + 36h]` (bao phủ lớp tối nay và
     sáng/chiều mai).
  2. Với mỗi lớp: seeker có `lead_stage ∈ {Seeker_Public_Program, Seeker_18_Weeks}`,
     `program_code` khớp (fallback: `city` khớp + có SĐT), `last_interaction`
     trong 21 ngày, chưa có `reminder_log` cho `(thread_id, class_key, session_date)`.
  3. Không gọi LLM. Tạo **một** bản ghi `action_queue(queue_type='session_proposal',
     target_type='class_session', action_text=digest)` và gửi digest Telegram:
     > 📅 Tối nay Thứ Ba 20:00 — HN Hoàng Quốc Việt: 6 seeker đã ghi danh
     > (Chiến, Hồng An, …). Chủ Nhật 14:30 — Vương Thừa Vũ: 3 seeker.
     > 👍 Mở phiên nhắc · 👎 Bỏ qua
  4. Khi approve (Telegram 👍 hoặc nút trên `/queues`): worker sinh draft từng
     seeker bằng `WarmUpComposer` với brief `{tên, xưng hô lấy từ tin Page gần
     nhất, lớp, giờ, địa chỉ/Zoom, zalo_url}` → `action_queue.reply_message`
     như hiện nay để yogi gửi tay. Ghi `reminder_log`.
- **Bảng mới:** `reminder_log(thread_id, class_key, session_date, digest_id,
  draft_id, created_at)`.
- **UI:** `/queues` thêm tab "Phiên" hiển thị digest và nút Mở phiên / Bỏ qua.

### P2.2 MAS Sau buổi học — điểm danh
- **ID:** `code:route-post-session-001`, UC mới **UC-12**
- Cùng job 08:30: với mỗi lớp đã diễn ra trong 24 h trước **và** có
  `reminder_log`, gửi digest checklist: *"Hôm qua Vương Thừa Vũ có 3 người hẹn —
  ai đã tới?"* với từng tên có nút ✅/❌ (Telegram inline keyboard hoặc `/queues`).
- Kết quả ghi `attendance(thread_id, class_key, session_date, attended, source)`.
- `attended=1` → bằng chứng cho stage gate (P0.4b); ✅ đồng thời tạo draft "cảm
  ơn + hẹn buổi 2". `attended=0` → candidate warm-up nhẹ sau 3 ngày (đi qua
  decision core hiện có).

### P2.3 Cảnh báo SLA "đăng ký chưa xác nhận"
- **ID:** `code:route-registration-sla-001`
- Job mỗi 30 phút (không LLM): thread ở `registered_awaiting_confirm` (P1.1)
  với `age_hours > 2` và chưa có cảnh báo trong 12 h → Telegram:
  *"⏰ Nguyễn Hữu Sáu để SĐT 0393140362 lúc 16:16 — 2h05 chưa ai xác nhận."*
  kèm link Business Inbox. Ghi `mas_decisions(route='sla_alert')`.
- Đây là thông báo cho người, không phải draft; MAS reply cho thread này vẫn
  đi theo P1 bình thường.

### P2.4 Morning brief + trigger theo tin mới
- **ID:** `code:route-morning-brief-001`
- 08:00: một tin Telegram tổng hợp — số thread chờ người thật (theo tuổi), số
  SLA đang mở, các phiên nhắc lịch hôm nay, số draft đang pending ở `/queues`.
- `run_inbox_mas_loop.sh`/scheduler chỉ gọi `run_mas` khi lần fetch vừa rồi có
  `fetch_log.messages_count > 0` **hoặc** có thread đổi `state` — thay vì mỗi
  15 phút vô điều kiện.

### Gate P2
- Chạy thử 3 ngày liên tiếp với `--dry-run`: digest đúng lớp/đúng ngày, không
  seeker nào xuất hiện 2 lần cho cùng buổi; 0 draft sinh ra khi chưa approve.
- Với tài khoản Hung Bui: mở một phiên thật, draft hiện ở `/queues`, gửi tay
  thành công (rule 10.1).

---

## P3 — Chi phí & chất lượng draft

### P3.1 Knowledge retrieval theo city/intent
- **ID:** `code:tool-inbox-mas-001:knowledge-retrieval`
- Thay `load_knowledge_context()` "nạp tất cả" bằng `build_knowledge(seeker,
  classification)`:

  | Nguồn | Khi nào nạp | Ghi chú |
  | --- | --- | --- |
  | `SOUL.md` | không nạp cho `BatchInboxAgent` (đã inline); nạp cho `Responder` | bỏ trùng |
  | `lop-hoc.md` | chỉ **section của city** seeker (+ Online); bỏ bảng QR đường dẫn file, lệnh CLI, quy tắc tên file | parse theo heading `##` |
  | `faq.md` | chỉ khi `intent ∈ {question}` và câu hỏi khớp keyword của ≤ 2 mục | |
  | `research.md` | chỉ khi tin khách chứa `khoa học|nghiên cứu|bằng chứng|có tác dụng` | |
  | `su-kien.md` / `events` | chỉ sự kiện `event_date ≥ today` cùng city | |
  | `mas_strategy.md` | chỉ bảng "Stage → cách trả lời" (đã có trong prompt batch) — **bỏ** sơ đồ ASCII kiến trúc | |
  | Bảng liên hệ trung tâm | một bản duy nhất, chỉ dòng của city | gỡ khỏi `faq.md`, `su-kien.md` |

- Mục tiêu < 6K token/call. Đo bằng `llm_calls.tokens_in` trước/sau.

### P3.2 Few-shot từ chính yogi
- Đưa 3–5 tin `Page` (người thật, `kind='message'`) gần nhất của **cùng city**
  vào prompt dưới mục *"Cách CLB thường trả lời"* — lấy xưng hô (chú/cô/anh/chị),
  địa chỉ, độ dài. Loại tin `Auto_Page`.

### P3.3 Dọn file knowledge (không đụng code)
- Chuyển sơ đồ kiến trúc trong `mas_strategy.md` sang `docs/architect/`.
- Gộp bảng liên hệ về một file `memory/agent_memory/lien-he.md`.
- Đánh dấu sự kiện đã qua trong `su-kien.md` bằng frontmatter `status: past`.

### Gate P3
- `tokens_in` trung bình/call giảm ≥ 60%; eval set ADK pass không giảm; 10 ca
  đối chiếu tay không có trường hợp lấy nhầm lớp city khác.

---

## 3. Bản đồ file & sở hữu (cho sprint)

| Phase | File chính | Loại thay đổi |
| --- | --- | --- |
| P0.1–0.2 | `fb_pipeline/browser/inbox/thread_detail_parser.py`, `fb_pipeline/persistence/l4_sqlite_store.py`, migration mới `tools/l5_migrate_messages_kind.py` | schema + parser |
| P0.3 | `tools/l5_inbox_mas_context.py`, `tools/l5_inbox_mas_runner.py` | sửa 4 dòng |
| P0.4 | `tools/l5_inbox_mas_runner.py`, `adk_agents/tools/l5_stage_tools.py` | tách logic |
| P0.5 | `tools/l5_events_sync.py` (mới), `tools/l5_scheduler.py` | CLI mới |
| P1 | `fb_pipeline/contracts/l1_conversation_state.py` (mới), `adk_agents/tools/l5_seeker_tools.py`, `tools/l5_inbox_mas_pipeline.py`, `adk_agents/agent.py`, `adk_agents/inbox_mas.evalset.json`, `tools/l5_action_queue.py` | logic gate + prompt |
| P2.1–2.2 | `fb_pipeline/contracts/l1_class_schedule.py` (mới), `tools/l5_scheduler_routes.py`, `tools/l5_scheduler.py`, `tools/l5_telegram_hitl.py`, `web/src/app/queues/*`, `web/src/app/api/action-queue/*` | route mới + UI |
| P2.3–2.4 | `tools/l5_scheduler_routes.py`, `tools/run_inbox_mas_loop.sh` | job nhẹ |
| P3 | `tools/l5_inbox_mas_context.py`, `memory/agent_memory/*.md`, `memory/mas_strategy.md` | retrieval + dọn file |

Test đi kèm mỗi phase đặt trong `tests/` với tag `# code:test-validation-001:<layer>`.

## 4. Rủi ro & cách xử lý

| Rủi ro | Xử lý |
| --- | --- |
| Backfill `message_at` sai với chuỗi tương đối cũ (`Sun 12:04 AM` không biết tuần nào) | Dùng `recorded_at` làm mốc; nếu chênh > 7 ngày so với `last_interaction` thì đặt NULL + `approx` thay vì đoán |
| Bộ từ closer bắt nhầm câu hỏi ngắn ("ok vậy học ở đâu?") | Chỉ coi là closer khi ≤ 6 từ **và** không chứa `?`/từ nghi vấn; có test |
| `program_code` chưa được classify cho seeker cũ → nhắc lịch bỏ sót | Fallback city + SĐT; digest ghi rõ "khớp theo city" để người quyết |
| Nhắc lịch làm phiền người đã học ổn định | Chỉ nhắc `Seeker_Public_Program`/`Seeker_18_Weeks`, `last_interaction` ≤ 21 ngày, mỗi buổi một lần, và luôn qua tay quản trị viên |
| Retrieval theo city bỏ sót lớp Online | Luôn kèm section Online cho mọi city |

## 5. Ngoài phạm vi

- Tự động gửi bất kỳ tin nào tới seeker (giữ nguyên ranh giới UC-05/UC-06).
- Thay model LLM hay đổi nhà cung cấp.
- Chăm sóc comment-only lead (chưa có kênh gửi).

## 6. Trạng thái triển khai (2026-09-17)

Đây là ghi nhận của lần triển khai trước, không phải kết quả kiểm thử lại trong
lượt phân tích strategy/memory. Các dấu ✅ không thay thế nghiệm thu A–F ở §7;
đặc biệt stage gate vẫn được gọi trong runner khi có SĐT và knowledge ~4K chỉ
đúng với một số input inbox, không đúng với loader mặc định/mọi route.

| Hạng mục | Trạng thái | Bằng chứng |
| --- | --- | --- |
| P0.1 `messages.kind` | ✅ | `fb_pipeline/contracts/l1_message_kind.py`, migration `tools/l5_migrate_messages_kind.py` — 453 banner + 8 reaction rows tách khỏi tin khách; unreplied giả từ 85 → 28 candidate thật |
| P0.2 `messages.message_at` | ✅ | `fb_pipeline/contracts/l1_message_time.py` — 3 551/3 551 dòng resolve được (kể cả `3/8/17, 12:58 PM` với U+202F); `users.last_interaction` tính lại cho 477 user |
| P0.3 literal `\n` | ✅ | `l5_inbox_mas_context.py`, `l5_inbox_mas_runner.py`, `l5_inbox_mas_thread.py` |
| P0.4 tách stage gate | ✅ | chỉ chạy khi `state.has_phone` (runner) hoặc khi có attendance ✅ (`run_attendance_sync`); touch-point chỉ đếm tin khách thật |
| P0.5 `events` từ `su-kien.md` | ✅ | `tools/l5_events_sync.py`, chạy trong `run_daily_care_cycle`; hiện 0 sự kiện vì file chỉ còn sự kiện đã qua |
| P1.1 Conversation state + Time Gate | ✅ | `fb_pipeline/contracts/l1_conversation_state.py`; quyết định ghi `mas_decisions.route='inbox_gate'` |
| P1.2 `now_context` + timestamp từng dòng | ✅ | `l5_inbox_mas_pipeline.py`, prompt Classifier/Responder/BatchInboxAgent |
| P1.3 `[NO_REPLY: …]` | ✅ | prompt + runner/recommend/thread/HITL-regen đều xử lý |
| P1.4 chống duplicate/rỗng trước LLM | ✅ | `has_active_proposal` trước batch; `enqueue_action` từ chối text rỗng |
| P2.1 Nhắc lịch 08:30 | ✅ | `tools/l5_proactive_routes.py` (`run_class_reminder_digest`, `run_session_open_cycle`), agent `ClassReminderComposer`, bảng `reminder_log`, queue `session_proposal`, UI tab 5 ở `/queues` |
| P2.2 Điểm danh | ✅ | `run_post_session_checklist`, `run_attendance_sync`, bảng `attendance`, queue `attendance_check`, UI tab 6 |
| P2.3 SLA đăng ký | ✅ | `run_registration_sla_cycle`, bảng `sla_alerts`, `send_telegram_notification` |
| P2.4 Morning brief + trigger theo tin mới | ✅ | `run_morning_brief`; `run_inbox_mas_loop.sh` chỉ gọi MAS khi fetch có `messages_found > 0` (`FUNNEL_MAS_ALWAYS=1` để bỏ qua) |
| P3.1 Knowledge retrieval | ✅ | `build_knowledge_context()` — ~4K ký tự/call thay cho 49K; `mas_strategy.md` và `research.md` không còn nạp mặc định |
| P3.2 Few-shot từ tin yogi | ✅ | `recent_human_page_examples()` cùng city |
| P3.3 Dọn file knowledge | ⏳ | chưa sửa file `.md`; retrieval đã bỏ qua phần kỹ thuật nên không còn gấp |
| Tests | ✅ | `tests/test_conversation_state.py` (39), `tests/test_class_schedule_and_care_routes.py` (13); toàn bộ suite unit 476 pass |

**Vận hành:** `tools/l5_scheduler.py --page-id 1548373332058326 --routes care [--care-time 08:30] [--live]`
đăng ký job hằng ngày + poller 2 phút (mở phiên / điểm danh) + SLA 30 phút. Không có `--live`, mọi route chỉ log và
trả về dry-run. Các loop `run_inbox_mas_loop.sh` đang chạy cần khởi động lại để nhận script mới.

## 7. Kế hoạch bổ sung sau rà soát strategy/memory — chỉ lập kế hoạch

**Universal ID:** `prd:mas-time-aware-001:strategy-alignment`  
**Căn cứ:** [MAS Strategy & Memory Review](../report/mas-strategy-memory-review-2026-09-17.md).

Phần này điều chỉnh các yêu cầu cũ có xung đột; chưa sửa code, memory runtime,
migration, scheduler hay gửi Telegram trong lượt cập nhật tài liệu này.

| Thứ tự | Phạm vi | Gate hoàn thành |
| --- | --- | --- |
| A | Hợp nhất strategy/use cases/SOUL/prompt: received khác assigned/attended; proactive digest → mở phiên → draft → gửi tay | Không còn auto-send DM hoặc prompt ép trả lời mọi tin; single/batch/regen thống nhất |
| B | Sửa closer, thời gian chưa chắc/intent hết hạn, persistence evidence, stage event độc lập và cursor theo tin mới | Không bỏ yêu cầu “Dạ em muốn đổi sang Chủ Nhật”; draft không nâng stage; reject không làm câu hỏi biến mất |
| C | Catalog lớp có hiệu lực và registration đúng lớp; SLA tính từ tin đăng ký; brief 08:30 | Không suy lớp từ city; lớp 21h ngày mai và 05:30 sáng mai đều được xét; không biến khóa cũ thành lịch vô hạn |
| D | Mở phiên atomic/idempotent; recheck page, opt-out, expiry; attendance có unknown; trạng thái gửi riêng | Retry không trùng; thiếu Telegram không auto-approve; chưa điểm danh không thành absent; dry-run không ghi |
| E | Retrieval theo thread/nhiệm vụ ở mọi composer; ví dụ đã khử dữ liệu riêng; catalog một nguồn chuẩn | Không lẫn thông tin giữa seekers, không mất policy tham gia lớp bất kỳ lúc nào; đo tokens thực tế |
| F | Replay DB tạm/fixture đã ẩn danh, shadow rồi pilot riêng | Không draft banner/closed/expired; proactive chỉ draft sau mở phiên; không DM tự gửi; đo false-negative/SLA/chi phí |

Các quyết định thiết kế cập nhật:

- Lịch mặc định 08:30 **Asia/Ho_Chi_Minh**, xét buổi còn ở tương lai của **hôm
  nay và ngày mai** theo ngày lịch, thay cửa sổ 36h. SLA vẫn chạy nhẹ trong ngày.
- City khớp chỉ tạo candidate cần chọn lớp; không đủ điều kiện viết “đã ghi danh
  lớp X”. `registration_received` phải có nguồn persistence xác nhận, không chỉ regex.
- Timestamp chưa chắc không dùng giờ fetch để giả định tin mới. `LATE` phải
  kiểm tra còn có thể đáp ứng yêu cầu hay không; hỏi ở cổng có thời hạn khác FAQ.
- Stage evaluation chuyển khỏi runner draft sang evidence events; có SĐT và từ
  “lớp” trong tin Page chưa chứng minh seeker chọn chương trình cụ thể.
- Xưng hô học từ cùng thread; ví dụ khác thread chỉ học văn phong sau khi khử
  dữ liệu riêng. Dữ kiện lịch/địa chỉ lấy từ catalog đã xác minh.
- Attendance: unknown/attended/absent/cancelled, có người xác nhận và ngày giờ;
  không phụ thuộc vào việc đã có reminder_log. No-show chỉ sau xác nhận absent.
- Budget chống spam dựa trên **đã gửi**, dùng chung mọi proactive route; draft,
  approval hoặc mở phiên không phải bằng chứng đã liên hệ. Warm-up ba ngày sau
  vắng chỉ là candidate và vẫn qua budget/mở phiên.
- Events=0 không phải lỗi nếu không có sự kiện tương lai đủ dữ kiện. Không tạo
  dữ liệu giả hoặc khôi phục sự kiện tháng 4 như sự kiện mới để làm route chạy.

KPI: ưu tiên câu hỏi thật không bị bỏ sót, thời gian được người xử lý, tỷ lệ draft
được dùng/chỉnh/bỏ, nhắc đúng buổi đã gửi và attendance unknown. Calls/ngày và
token/seeker là phép đo hiệu quả, không được tối ưu bằng cách bỏ qua nhu cầu.
Định lượng baseline mới trước khi xác nhận các mục tiêu giảm chi phí trong §1.
