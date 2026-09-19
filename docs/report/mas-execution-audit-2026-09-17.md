# Audit thực thi MAS Inbox — 2026-09-17

**Mã tài liệu:** `doc:mas-execution-audit-001`
**Phạm vi:** route inbox reply của MAS (`tools/l5_inbox_mas_runner.py` →
`adk_agents/tools/l5_seeker_tools.py` → `tools/l5_inbox_mas_pipeline.py` →
`adk_agents/agent.py`), dữ liệu `action_queue`, `telegram_hitl_queue`,
`llm_calls`, `messages` trong `memory/agent_memory/frankensqlite.db` tại thời
điểm 2026-09-17 ~14:00 (Asia/Ho_Chi_Minh).
**Kế hoạch khắc phục:** [`docs/PRDs/mas-time-aware-care-plan.md`](../PRDs/mas-time-aware-care-plan.md)
(`prd:mas-time-aware-001`).

> **Bổ sung sau rà soát 17/09:** các kết luận và số liệu dưới đây là snapshot
> audit ban đầu, không phải mô tả toàn bộ code/DB hiện tại. Workspace đã có
> thay đổi time gate, message kind và retrieval. Xem
> [rà soát strategy/memory](mas-strategy-memory-review-2026-09-17.md)
> (`doc:mas-strategy-memory-review-001`) để biết số đo hiện tại, giới hạn bằng
> chứng và kế hoạch tiếp theo. Lượt rà soát bổ sung chỉ cập nhật tài liệu.

## 1. Kết luận ngắn

MAS inbox hiện tạo đề xuất trả lời cho **mọi thread có tin cuối mang nhãn
`Customer`**, không biết bây giờ là mấy giờ, không biết tin đó bao nhiêu tuổi,
không phân biệt tin thật với banner hệ thống, và không có lựa chọn "không cần
trả lời". Hệ quả đo được:

| Chỉ số | Giá trị |
| --- | --- |
| `action_queue.reply_message` — approved / executed | **0 / 0** |
| `action_queue.reply_message` — rejected | 213 (222 xoá bằng `manual_clear`, 11 từ webui) |
| Thread "unreplied" theo `find_unreplied_threads` | 85 |
| …trong đó tin cuối là **banner hệ thống**, không phải tin khách | **31 (36%)** |
| Thread có banner `X replied to an ad.` lưu dưới `sender='Customer'` | 99 |
| LLM calls route inbox ngày 2026-09-17 | 244 calls, ~255K token vào, TB 41 s/call |
| Knowledge context nạp vào **mỗi** call | 48 778 ký tự (~60 KB, ~20K token) |

Chưa có đề xuất nào của MAS từng được con người duyệt.

## 2. Ca điển hình — Quang Chiến

Dữ liệu thô trong `messages` (`thread_name = 'Quang Chien Nguyen'`):

```text
seq  sender    content                                     message_timestamp  recorded_at
 6   Customer  Nguyễn Quang Chiến , số 0878620783 …        Sun 12:04 AM       2026-09-16 17:01
 8   Page      (yogi xác nhận đăng ký)                     Mon 6:54 AM        2026-09-16 17:01
 9   Page      Hẹn chú Chiến vào 14h30 Chủ Nhật (20/09)…   Mon 6:54 AM        2026-09-16 17:01
10   Customer  Xin trân trọng cám ơn !                     Mon 11:10 AM       2026-09-16 17:01
11   Page      Dạ !                                        Mon 11:10 AM       2026-09-16 17:01
14   Customer  Quang Chien Nguyen replied to an ad.        Sun 12:04 AM       2026-09-16 19:02
```

- Hội thoại đã được **con người đóng** ở seq 11 (`Dạ !`), `users.last_interaction
  = 2026-09-14 11:10:00`.
- Lần fetch 2 giờ sau chèn lại banner *"replied to an ad."* thành tin `Customer`
  mới (seq 14) — timestamp còn cũ hơn seq 0. Đây là lỗi parser + dedup.
- `find_unreplied_threads` thấy tin cuối là `Customer` ⇒ thread "cần trả lời".
- LLM nhận `[Customer] Quang Chien Nguyen replied to an ad.` không kèm thời
  gian, không kèm `now` ⇒ soạn *"Dạ không có gì ạ 🙏 …"* cho lời cảm ơn 3 ngày
  trước mà yogi đã đáp rồi.

## 3. Phân loại 40 đề xuất gần nhất (`action_queue` id 208–251)

| Loại | Số | Ví dụ |
| --- | --- | --- |
| Banner hệ thống bị coi là tin khách (`replied to an ad`, `replied to a post`, `Bạn đang phản hồi bình luận…`, `:::REACTION_LOVE:::`) | 14 | #251 Quang Chiến, #233 Đỗ Tuấn Anh, #209 Nguyen Minh, #230 Lien Le Ai |
| Câu kết thúc hội thoại ("cảm ơn", "vâng ạ", "Dạ okee", "Tks") từ nhiều tuần trước | 10 | #227 Omi Đinh — trả lời "M cám ơn" của **24/06**, gần 3 tháng sau |
| Tin thật nhưng quá hạn, MAS trả lời như thể vừa nhận | ~10 | #241 Hoàng Yến: "em tới 158 Đào Duy Anh rồi" (23/08) → hôm nay MAS đáp "Em xuống tầng trệt nhé" |
| Duplicate rỗng | 5 | #219–223 Trader Nguyễn, `action_text` trống, cùng phút |

## 4. Nguyên nhân gốc trong kiến trúc

### 4.1 Mù thời gian (nghiêm trọng nhất)
- `tools/l5_inbox_mas_pipeline.py:43-47` dựng prompt `[{sender}] {content}` —
  **vứt bỏ timestamp**.
- Không agent nào trong `adk_agents/agent.py` nhận `now`; session state chỉ có
  `thread_messages`, `seeker_context`, `knowledge_context`.
- `messages.message_timestamp` là chuỗi tương đối của Facebook (`Mon 11:10 AM`);
  443/3 551 dòng không có năm. Kể cả đưa vào prompt cũng không suy ra tuổi tin.
- `users.last_interaction` đã là ISO tuyệt đối nhưng không được dùng để gate.

### 4.2 Trigger sai bản chất
`find_unreplied_threads` (`adk_agents/tools/l5_seeker_tools.py:118-131`) chỉ
hỏi *"tin cuối có phải Customer không"*. Không lọc tuổi, không lọc loại nội dung,
không có state hội thoại (`open_question` / `registered_awaiting_confirm` /
`closed_by_human` / `stale`).

### 4.3 Không có lựa chọn "không trả lời"
Schema output của `BatchInboxAgent` bắt buộc `reply_text`; sentinel duy nhất là
`[OUT_OF_SCOPE]` cho spam. Prompt còn ép *"If the last message is from the
Customer: Respond directly"*. Với "Vâng ạ", LLM buộc phải bịa ra một câu đáp.

### 4.4 Banner hệ thống đi vào DB như tin khách
Các chuỗi `X replied to an ad.`, `X replied to a post. View post`, `Bạn đang
phản hồi bình luận của người dùng về bài viết…`, `:::REACTION_*:::` được lưu với
`sender='Customer'`. UNIQUE `(sender, content, timestamp)` không chặn được vì
banner tái xuất với ngữ cảnh khác ở lần fetch sau.

### 4.5 Stage gate bị kích bởi việc *tạo đề xuất*
`evaluate_stage_gate` chạy ngay trong vòng tạo draft
(`tools/l5_inbox_mas_runner.py:216`): 73 lần `promoted` trong `mas_decisions`
trong khi 0 draft được duyệt.

> Lưu ý quyết định của chủ dự án: MAS **được phép** nói "CLB đã nhận được
> thông tin đăng ký" khi thông tin đăng ký từ tin khách thật đã được pipeline
> lưu vào `users` và hiển thị ở `/seekers`. Không cần người duyệt để thừa nhận
> việc tiếp nhận này. Banner không phải bằng chứng đăng ký, nhưng cũng không
> chứng minh rằng trước đó chưa đăng ký: phải kiểm tra hồ sơ/lịch sử trước khi
> kết luận cho từng ca. Tiếp nhận không đồng nghĩa đã xếp lớp hay đã tham dự.

### 4.6 Knowledge context phình 60 KB mỗi call
`tools/l5_inbox_mas_context.py` nạp 6 file, không lọc theo city/intent:

| File | Bytes | Tỷ lệ | Vấn đề |
| --- | --- | --- | --- |
| `mas_strategy.md` (250 dòng đầu) | 16.0K | 26% | Dòng 18–64 là sơ đồ ASCII kiến trúc hệ thống (Telegram HITL, CDP, DB) — nội bộ vận hành |
| `research.md` | 13.7K | 22% | Tổng quan nghiên cứu khoa học; chỉ cần khi seeker hỏi về bằng chứng khoa học |
| `lop-hoc.md` | 11.4K | 19% | Cần, nhưng kèm bảng QR có đường dẫn file, lệnh CLI, quy tắc đặt tên file |
| `faq.md` | 8.8K | 14% | Ổn |
| `SOUL.md` | 6.8K | 11% | Trùng — instruction `BatchInboxAgent` đã inline SOUL |
| `su-kien.md` | 4.0K | 7% | Toàn sự kiện tháng 3–4/2026 đã qua |
| Bảng "Liên hệ các trung tâm" | — | — | Lặp trong 3 file |

Bug kèm theo: `"\\n"` literal ở `l5_inbox_mas_context.py:38,43` và
`l5_inbox_mas_runner.py:218-227` ⇒ prompt và tin Telegram chứa ký tự `\n` chữ
thay vì xuống dòng.

### 4.7 Vòng lặp chạy mù
`tools/run_inbox_mas_loop.sh` chạy fetch → classify → MAS mỗi 15 phút bất kể có
tin mới hay không ⇒ 244 call/ngày.

### 4.8 EventPipeline chết lâm sàng
Bảng `events` có 0 dòng dù `su-kien.md` có dữ liệu ⇒ route event không bao giờ
chạy.

## 5. Hướng khắc phục (tóm tắt; chi tiết ở PRD)

1. **Time Gate + Conversation State** trước khi gọi LLM: lọc banner, nhận diện
   `closed_by_human`, phân nhánh theo tuổi tin (`<24h` reply, `1–7d` reply có
   nhãn LATE, `>7d` chuyển warm-up), inject `now` + timestamp tuyệt đối, thêm
   sentinel `[NO_REPLY: <lý do>]`.
2. **MAS Nhắc lịch lớp** chạy 08:00–09:00 mỗi ngày: một digest lên
   Telegram/`/queue` liệt kê seeker đã ghi danh các lớp diễn ra trong 24–36 h
   tới; chỉ khi quản trị viên "Mở phiên" mới sinh draft từng người.
3. **MAS Sau buổi học** (sáng hôm sau): checklist điểm danh → bằng chứng
   attendance cho stage gate.
4. **Cảnh báo SLA đăng ký chưa xác nhận**: SĐT xuất hiện >2 h chưa có tin
   `Page` từ người thật → ping Telegram (không draft).
5. **Morning brief + trigger theo tin mới** thay polling 15 phút.
6. **Knowledge retrieval theo city/intent** + few-shot từ tin `Page` do yogi gõ.
7. **Dọn tầng dữ liệu**: `messages.kind`, `message_at` ISO, tách stage gate khỏi
   vòng draft, nạp `events` từ `su-kien.md`.

## 6. Truy vấn tái kiểm

Các query phản ánh DB tại thời điểm chạy. Bảng trace/queue đã dọn sẽ không tái
tạo được thống kê lịch sử; cần snapshot phù hợp. Đơn vị ký tự, byte và token
khác nhau: mức ~20K token phía trên là ước lượng cũ, chưa phải số đo tokenizer.

```sql
-- Thread "unreplied" có tin cuối là banner
WITH last AS (SELECT thread_id, MAX(seq) max_seq FROM messages GROUP BY thread_id),
lm AS (SELECT m.* FROM messages m JOIN last ON last.thread_id=m.thread_id AND last.max_seq=m.seq)
SELECT COUNT(*) FROM lm WHERE sender='Customer' AND (
  content LIKE '% replied to an ad%' OR content LIKE '% replied to a post%'
  OR content LIKE 'Bạn đang phản hồi bình luận%' OR content LIKE '%:::REACTION%');

-- Trạng thái hàng đợi reply
SELECT status, approval_source, COUNT(*) FROM action_queue
WHERE queue_type='reply_message' GROUP BY 1,2;

-- Khối lượng LLM theo ngày
SELECT date(started_at), route, COUNT(*), SUM(tokens_in), ROUND(AVG(duration_ms))
FROM llm_calls GROUP BY 1,2 ORDER BY 1 DESC;
```
