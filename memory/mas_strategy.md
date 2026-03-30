# MAS Strategy — Seeker Customer Journey & Warm-up Playbook

**Universal ID**: `doc:mas-strategy-001`

> Tài liệu này định nghĩa Customer Journey của seeker xuyên suốt funnel, từ lần chạm đầu tiên đến khi trở thành Sahaja Yogi. Mỗi giai đoạn (stage) có chiến lược chăm sóc cụ thể, được thực thi bởi các MAS trigger routes (React, Warm-up, Event Advertising).

---

## Customer Journey Overview

```text
Stage 0        Stage 1           Stage 2            Stage 3        Stage 4          Stage 5
─────── ──────────────── ──────────────────── ─────────────── ──────────────── ────────────────
 User   →  Follower      →  Curious Seeker    →  Registered   →  Deep Learner  →  Sahaja Yogi
         (Quan tâm Page)   (Hỏi chương trình)   (Đã ghi danh)   (Lớp 18 tuần)   (Thực hành)
```

### Kiến trúc Tổng thể MAS (System Architecture)

```text
                         ┌─────────────────────────────────────┐
                         │         FACEBOOK PAGE               │
                         │  (Posts · Comments · DMs · Ads)     │
                         └──────────────┬──────────────────────┘
                                        │
                    ┌───────────────────┼───────────────────────┐
                    │                   │                       │
                    ▼                   ▼                       ▼
           ┌──────────────┐   ┌──────────────┐       ┌──────────────┐
           │  Comment /   │   │  DM Inbox    │       │  Ad Clicks / │
           │  Reaction    │   │  (Messages)  │       │  Form Submit │
           └──────┬───────┘   └──────┬───────┘       └──────┬───────┘
                  │                  │                       │
                  ▼                  ▼                       ▼
         ┌────────────────────────────────────────────────────────┐
         │               DATA LAYER (FrankenSQLite)              │
         │  users · messages · comments · comment_users          │
         └────────────────────────┬───────────────────────────────┘
                                  │
         ┌────────────────────────┼───────────────────────┐
         │                        │                       │
         ▼                        ▼                       ▼
  ┌──────────────┐      ┌──────────────┐       ┌──────────────────┐
  │ INBOX MAS    │      │ ROUTE 1:     │       │ ROUTE 2 + 3:     │
  │ (Reactive)   │      │ REACT        │       │ WARM-UP / EVENT  │
  │              │      │              │       │ (Proactive)      │
  │ • Classify   │      │ • ❤️ React   │       │                  │
  │ • Draft reply│      │ • Reply      │       │ • AI Bundle Gen  │
  └──────┬───────┘      └──────┬───────┘       └───────┬──────────┘
         │                     │                       │
         ▼                     ▼                       ▼
   ┌─────────────────────────────────────────────────────────────┐
   │                  TELEGRAM HITL QUEUE                        │
   │ (Human 100% 👍 Reaction Approval & Feedback Rewrite Loop)   │
   └───────────────────────────┬─────────────────────────────────┘
                               │
                               ▼
                ┌────────────────────────────┐
                │ Async run_scheduler_loop   │
                │ Fire CDP & Send 💯 Reaction│
                └────────────────────────────┘
```

### Stage-to-Stage Transition Signals (Tín hiệu chuyển giai đoạn)

```text
  ┌─────────┐   Comment/React/   ┌──────────┐   DM hỏi info    ┌───────────┐   Gửi SĐT/    ┌────────────┐
  │ Stage 0 │   Follow/Like      │ Stage 1  │   chương trình   │ Stage 2   │   Email       │  Stage 3   │
  │  USER   │ ─────────────────→ │ FOLLOWER │ ────────────────→ │ CURIOUS   │ ────────────→ │ REGISTERED │
  │         │                    │          │                   │ SEEKER    │               │            │
  └─────────┘                    └──────────┘                   └───────────┘               └─────┬──────┘
   Chưa có                       DB: Seeker                     DB: Seeker                        │
   identity                      touch-point≥1                  DM/comment                  DB: Seeker_PP
                                                                hỏi info                    SĐT/email hợp lệ
                                                                                                  │
                                                                                                  ▼
                                 ┌──────────┐   Hoàn thành     ┌───────────┐   Tham dự     ┌─────────────┐
                                 │ Stage 5  │   18 tuần +      │ Stage 4   │   ≥3/4 buổi   │  Stage 3    │
                                 │  SAHAJA  │ ◄──────────────── │ DEEP      │ ◄──────────── │  REGISTERED │
                                 │  YOGI    │   thực hành 3th  │ LEARNER   │   lớp nhập    │             │
                                 └──────────┘                   └───────────┘   môn          └─────────────┘
                                  DB: Seed →                    DB: Seeker_18W
                                  Sahaja_Mahayogi
```

---

## Stage 0: User (Chưa xác định)

**DB `lead_stage`**: `User` · **Journey Engine**: `User`

Khách ghé thăm Page nhưng chưa có tương tác nào được ghi nhận.

| Touch-point | Mô tả |
| --- | --- |
| Xem bài post | Impression, chưa action |
| Xem video | Watch time, chưa comment/react |

### Chiến lược chăm sóc

- **Không có hành động proactive** — chưa có dữ liệu identity
- **Content marketing**: Đăng bài hấp dẫn (video thiền, music chill, tips sức khỏe) để kéo tương tác
- **Quảng cáo nhắm mục tiêu**: Retarget users xem video > 50% bằng ad dạng "Trải nghiệm miễn phí"

---

## Stage 1: Follower — *Quan tâm đến Page* (Người theo dõi)

**DB `lead_stage`**: `Seeker` · **Journey Engine**: `User → Seeker`

Người dùng đã có hành động gắn kết với Page: Follow, Subscribe, Like Page, Join Group Facebook, hoặc Join nhóm Zalo.

| Touch-point | Signal | DB Table |
| --- | --- | --- |
| Follow / Subscribe Page | React hoặc Follow action | `users` (tạo mới khi có DM) |
| Like / React bài post | Reaction trên post | `reactions` |
| Comment bài post | Comment công khai | `comments`, `comment_users` |
| Join Group / Zalo | Từ link trong post/ad | `users` (city detection) |

### Chiến lược chăm sóc

| Kênh | Hành động | MAS Route |
| --- | --- | --- |
| **React** | Reaction bài comment bằng ❤️ Love hoặc 👍 Like để acknowledge | Route 1: React |
| **Comment reply** | Auto-reply comment hỏi thông tin → mời inbox để tư vấn riêng | Route 1: React |
| **Warm-up** | Nếu đã inbox 1 lần rồi im lặng 3–5 ngày → gửi tin nhắn mở lời nhẹ nhàng: _"Chào bạn, mình là SY VN 🙏 Bạn có muốn tìm hiểu thêm về thiền không?"_ | Route 2: Warm-up |
| **Content** | Chia sẻ thêm video/bài viết phù hợp sở thích (music vs wellness) | Manual |

### Điều kiện chuyển Stage

- User gửi DM hỏi về chương trình cộng đồng hoặc lớp học → chuyển sang **Stage 2: Curious Seeker**

---

## Stage 2: Curious Seeker — *Hỏi về Chương trình / Lớp học* (Người tìm hiểu)

**DB `lead_stage`**: `Seeker` · **Journey Engine**: `Seeker`

Seeker đã chủ động inbox hoặc comment hỏi thông tin. Có 2 nhánh quan tâm:

### Nhánh A: Chương trình cộng đồng

| Chương trình | Mô tả | Tần suất |
| --- | --- | --- |
| 🎵 Thiền Âm nhạc | Trải nghiệm thiền kết hợp âm nhạc Ấn Độ | Chủ Nhật tuần 3 / tháng |
| 🌿 Thiền Trị liệu | Workshop chăm sóc sức khỏe qua thiền | Theo sự kiện |

### Nhánh B: Lớp học chính quy

| Lớp | Mô tả | Hình thức |
| --- | --- | --- |
| 📚 Lớp 4 tuần | Khóa nhập môn thiền Sahaja Yoga (4 buổi) | Online / Offline |
| 💻 Lớp Online | Khóa căn bản qua Zoom, khai giảng mùng 1 & 15 hàng tháng | Online |

### Chiến lược chăm sóc

| Kênh | Hành động | MAS Route |
| --- | --- | --- |
| **Draft reply** | Soạn ngay câu trả lời khi seeker hỏi → cung cấp thông tin chương trình từ `lop-hoc.md` / `su-kien.md`; con người sẽ review và gửi thủ công | Inbox MAS |
| **Event Advertising** | Nếu seeker ở đúng city có sự kiện → gửi thông báo event cá nhân hóa | Route 3: Event |
| **Warm-up** | Nếu seeker hỏi rồi im lặng 3–7 ngày → gửi reminder nhẹ: _"Bạn ơi, lớp thiền miễn phí sắp khai giảng ngày [date] 🧘 Bạn còn muốn tham gia không?"_ | Route 2: Warm-up |
| **Telegram** | Thông báo sang Telegram group để yogis follow-up gọi điện hoặc nhắn riêng | Inbox MAS |

### Điều kiện chuyển Stage

- Seeker gửi thông tin đăng ký (tên, SĐT, email) → chuyển sang **Stage 3: Registered**

---

## Stage 3: Registered — *Đã ghi danh* (Đã đăng ký)

**DB `lead_stage`**: `Seeker_Public_Program` · **Journey Engine**: `Seeker → Seeker_Public_Program`

Seeker đã gửi thông tin đăng ký (SĐT, email) cho một lớp hoặc chương trình cụ thể.

| Touch-point | Signal | DB Table |
| --- | --- | --- |
| Gửi SĐT qua DM | Phone regex match | `users.phone` |
| Gửi email qua DM | Email regex match | `users.email` |
| Đăng ký via form | Google Form / Zalo link | External → manual import |

### Chiến lược chăm sóc

| Kênh | Hành động | MAS Route |
| --- | --- | --- |
| **Confirm** | Gửi tin xác nhận đăng ký + chi tiết lớp (thời gian, địa chỉ/link Zoom, chuẩn bị) | Inbox MAS |
| **Reminder T-1** | 1 ngày trước lớp → nhắc lịch: _"Ngày mai lớp thiền bắt đầu lúc 19:30 🧘 Bạn nhớ tham gia nhé!"_ | Route 2: Warm-up |
| **Reminder T-0** | Sáng ngày lớp học → gửi link Zoom hoặc bản đồ offline | Route 2: Warm-up |
| **No-show** | Nếu đăng ký nhưng không tham dự → 2 ngày sau gửi: _"Mình thấy bạn chưa kịp tham gia lớp vừa rồi. Lớp tiếp theo vào [date], bạn đăng ký lại nhé!"_ | Route 2: Warm-up |
| **Event cross-sell** | Nếu đăng ký lớp → mời tham gia Thiền Âm nhạc cùng city | Route 3: Event |

### Điều kiện chuyển Stage

- Hoàn thành lớp 4 tuần hoặc lớp Online căn bản → sẵn sàng vào **Stage 4: Deep Learner**

---

## Stage 4: Deep Learner — *Khóa học chuyên sâu* (Học viên chuyên sâu)

**DB `lead_stage`**: `Seeker_18_Weeks` · **Journey Engine**: `Seeker_Public_Program → Seeker_18_Weeks`

Seeker đã hoàn thành lớp nhập môn và tiếp tục tham gia khóa 18 tuần (offline, online, hoặc thực hành thiền định online dài hạn cùng nhóm).

| Hình thức | Mô tả | Tần suất |
| --- | --- | --- |
| 🏫 Lớp 18 tuần Offline | Lớp tại địa điểm (HN, HCM, ĐN...) | 1 buổi/tuần × 18 tuần |
| 💻 Lớp 18 tuần Online | Qua Zoom/Google Meet | 1 buổi/tuần × 18 tuần |
| 🧘 Thực hành thiền Online | Thiền tập thể online dài hạn cùng nhóm | Hàng ngày / hàng tuần |

### Chiến lược chăm sóc

| Kênh | Hành động | MAS Route |
| --- | --- | --- |
| **Weekly reminder** | Mỗi tuần nhắc lịch buổi học tiếp theo + tóm tắt bài trước | Route 2: Warm-up |
| **Encouragement** | Khi đạt milestone (tuần 6, tuần 12, tuần 18) → gửi lời chúc mừng | Route 2: Warm-up |
| **Re-engage** | Nếu vắng 2 buổi liên tiếp → gửi tin nhắn: _"Bạn ơi, nhóm thiền nhớ bạn! Nếu cần hỗ trợ gì mình sẵn lòng giúp 🙏"_ | Route 2: Warm-up |
| **Community** | Mời vào nhóm Zalo/Telegram của lớp để trao đổi bài tập | Manual |
| **Event** | Mời tham gia sự kiện cộng đồng (Thiền Âm nhạc, Puja) song song | Route 3: Event |

### Điều kiện chuyển Stage

- Hoàn thành khóa 18 tuần + duy trì thực hành thiền ≥ 3 tháng → chuyển sang **Stage 5: Sahaja Yogi**

---

## Stage 5: Sahaja Yogi — *Người thực hành* (Yogi)

**DB `lead_stage`**: `Seed` → `Sahaja_Yogi` → `Sahaja_Yogi_Dedicated` → `Sahaja_Mahayogi`

Journey Engine: `Seed → Sahaja_Yogi → Sahaja_Yogi_Dedicated → Sahaja_Mahayogi`

Seeker đã trở thành Sahaja Yogi — người thực hành thiền thường xuyên, có thể tham gia hướng dẫn người mới.

| Sub-stage | Mô tả | Thời gian |
| --- | --- | --- |
| 🌱 Seed | Đã hoàn thành 18 tuần, đang tập thành thói quen | 0–3 tháng |
| 🧘 Sahaja Yogi | Thực hành ổn định, tham gia collective | 3–6 tháng |
| 💎 Dedicated | Tận tâm, hướng dẫn người mới | 6+ tháng |
| 👑 Mahayogi | Leadership cộng đồng, tổ chức hoạt động | Lâu dài |

### Chiến lược chăm sóc

| Kênh | Hành động | MAS Route |
| --- | --- | --- |
| **Invite to lead** | Mời hướng dẫn seeker mới tại city của họ | Manual |
| **Event organize** | Mời tổ chức/hỗ trợ sự kiện cộng đồng | Route 3: Event |
| **Mentorship** | Assign làm mentor cho seeker Stage 3–4 | Manual |
| **Recognition** | Khi đạt milestone → gửi lời tri ân | Route 2: Warm-up |

---

## Mô hình Nhiệt độ Seeker (Seeker Temperature Model)

> Chiến lược tổng thể khi seeker trở nên "lạnh nhạt" — áp dụng cho **mọi stage** trong Customer Journey. Hệ thống tự động đánh giá "nhiệt độ" của seeker dựa trên thời gian im lặng và hành vi, sau đó kích hoạt chiến lược re-engagement phù hợp.

### Bốn mức nhiệt độ

```text
🔥 HOT (Nóng)     →  Đang tương tác tích cực, phản hồi trong 24h
🟡 WARM (Ấm)      →  Có tương tác gần đây nhưng đang chậm lại
🔵 COOL (Mát)     →  Im lặng kéo dài, có dấu hiệu mất quan tâm
❄️ COLD (Lạnh)     →  Không phản hồi sau nhiều lần warm-up
```

### Temperature × Action Decision Matrix (Ma trận Quyết định)

```text
  ┌─────────────────────────────────────────────────────────────────────────┐
  │                    TEMPERATURE DECISION ENGINE                        │
  │                                                                       │
  │   Seeker last_interaction_at → tính silence_days                      │
  │                │                                                      │
  │                ▼                                                      │
  │   ┌────────────────────────┐                                          │
  │   │ Lookup threshold bảng  │  (Stage × silence_days → temperature)    │
  │   │ "Ngưỡng nhiệt độ"     │                                          │
  │   └───────────┬────────────┘                                          │
  │               │                                                       │
  │     ┌─────────┼─────────┬─────────────┐                               │
  │     ▼         ▼         ▼             ▼                               │
  │   🔥 HOT   🟡 WARM   🔵 COOL      ❄️ COLD                            │
  │     │         │         │             │                               │
  │     ▼         ▼         ▼             ▼                               │
  │  Maintain   Stimulate  Re-engage    Last Resort                       │
  │  (reply     (share     (3-step      (1 last                           │
  │   <2h)       value)     sequence)    nudge)                           │
  │     │         │         │             │                               │
  │     ▼         ▼         ▼             ▼                               │
  │  ┌──────┐  ┌──────┐  ┌──────┐     ┌─────────┐                        │
  │  │Reply?│  │Reply?│  │Reply?│     │ Reply?  │                        │
  │  │Yes→🔥│  │Yes→🔥│  │Yes→🟡│     │ Yes→🟡  │                        │
  │  │No→🟡 │  │No→🔵 │  │No→❄️ │     │ No→DORM │                        │
  │  └──────┘  └──────┘  └──────┘     └─────────┘                        │
  └─────────────────────────────────────────────────────────────────────────┘
```

### Ngưỡng nhiệt độ theo Stage

| Stage | 🔥 Hot | 🟡 Warm | 🔵 Cool | ❄️ Cold |
| --- | --- | --- | --- | --- |
| **Follower** | < 3 ngày | 3–7 ngày | 7–21 ngày | > 21 ngày |
| **Curious Seeker** | < 3 ngày | 3–7 ngày | 7–14 ngày | > 14 ngày |
| **Registered** | < 2 ngày | 2–5 ngày | 5–14 ngày | > 14 ngày |
| **Deep Learner** | < 7 ngày | 1–2 buổi vắng | 3 buổi vắng | > 4 tuần vắng |
| **Sahaja Yogi** | Tham gia collective | 2–4 tuần vắng | 1–3 tháng vắng | > 3 tháng vắng |

### Chiến lược xử lý theo nhiệt độ

#### 🔥 Hot → Duy trì (Maintain)

- **Mục tiêu**: Giữ momentum tương tác, không để nguội
- **Hành động**: Soạn phản hồi nhanh (< 2h), cung cấp giá trị liên tục, rồi để người vận hành review và gửi thủ công
- **MAS**: Inbox MAS draft-only + Route 1 React ngay lập tức

#### 🟡 Warm → Kích thích (Stimulate)

- **Mục tiêu**: Tăng tần suất tương tác trở lại mức Hot
- **Hành động**: Gửi nội dung giá trị (tips thiền, video mới, event sắp tới)
- **MAS**: Route 2 Warm-up — tin nhắn mang tính chia sẻ, không bán hàng
- **Ví dụ**: _"Mình vừa đăng video hướng dẫn thiền buổi tối rất hay, bạn xem thử nhé 🧘"_

#### 🔵 Cool → Re-engage (Tiếp cận lại)

- **Mục tiêu**: Tìm hiểu lý do im lặng, cung cấp "cửa vào" mới
- **Chuỗi hành động 3 bước**:

| Bước | Thời điểm | Nội dung | Phong cách |
| --- | --- | --- | --- |
| 1️⃣ Check-in | Đầu giai đoạn Cool | Hỏi thăm, không đề cập lớp/event | Ấm áp, cá nhân |
| 2️⃣ Giá trị | +3 ngày sau bước 1 | Chia sẻ tips/video/podcast hữu ích | Cho đi, không yêu cầu |
| 3️⃣ Cơ hội mới | +5 ngày sau bước 2 | Giới thiệu event/lớp mới phù hợp | Mời nhẹ nhàng |

- **Ví dụ chuỗi Cool**:
  - Bước 1: _"Chào bạn, lâu rồi mình không thấy bạn. Hy vọng bạn khỏe 🙏"_
  - Bước 2: _"Mình chia sẻ bạn mẹo thiền 5 phút mỗi sáng giúp tỉnh táo cả ngày 🌿"_
  - Bước 3: _"Cuối tuần này có Thiền Âm nhạc tại [city], hoàn toàn miễn phí. Bạn muốn tham gia không?"_

#### ❄️ Cold → Sunset hoặc Last Resort (Lần cuối)

- **Mục tiêu**: Một nỗ lực cuối cùng trước khi chuyển sang chế độ "im lặng tôn trọng"
- **Hành động**:

| Bước | Nội dung | Sau đó |
| --- | --- | --- |
| 🔔 Last nudge | Tin nhắn cuối cùng, chân thành: _"Mình luôn ở đây nếu bạn muốn quay lại 🙏 Chúc bạn mọi điều tốt đẹp!"_ | Đợi 14 ngày |
| ☀️ Nếu phản hồi | Chuyển về 🟡 Warm, bắt đầu lại chuỗi Stimulate | Reset timer |
| 🌙 Nếu im lặng | **Sunset** — ngừng warm-up proactive, chỉ gửi event quảng bá lớn (1 lần/quý) | Đánh dấu `dormant` |

### Quy tắc chống spam (Anti-spam Rules)

| Quy tắc | Chi tiết |
| --- | --- |
| **Max warm-up frequency** | Tối đa 1 tin nhắn warm-up / 7 ngày / seeker |
| **Cool sequence limit** | Max 3 bước trong chuỗi Cool, sau đó chuyển Cold |
| **Cold sunset** | Sau "last nudge", chờ tối thiểu 14 ngày trước khi gửi bất kỳ thứ gì |
| **Dormant quarterly** | Seekers `dormant` chỉ nhận 1 event notification / quý |
| **Instant stop** | Nếu seeker nói "không muốn nhận tin" → đánh dấu `unsubscribed`, dừng mọi proactive |
| **Escalation** | Không bao giờ tăng tần suất khi seeker đang nguội — luôn giảm dần |

### Transition Flow khi Nguội Lạnh

```text
🔥 Hot ──(im lặng)──→ 🟡 Warm ──(tiếp tục im lặng)──→ 🔵 Cool ──(3 bước thất bại)──→ ❄️ Cold
                          │                                  │                               │
                          ▼                                  ▼                               ▼
                    Stimulate                           Re-engage                    Last Resort
                    (chia sẻ giá trị)               (chuỗi 3 bước)               (sunset protocol)
                          │                                  │                               │
                          ▼                                  ▼                               ▼
                   Phản hồi? → 🔥                   Phản hồi? → 🟡                  Phản hồi? → 🟡
                   Không? → 🔵                      Không? → ❄️                    Không? → Dormant
```

### DB Schema Support

```sql
-- Cột mới trong bảng users / comment_users
ALTER TABLE users ADD COLUMN temperature TEXT DEFAULT 'warm';
  -- Giá trị: 'hot', 'warm', 'cool', 'cold', 'dormant', 'unsubscribed'
ALTER TABLE users ADD COLUMN last_warmup_at DATETIME;
ALTER TABLE users ADD COLUMN warmup_count INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN cool_step INTEGER DEFAULT 0;
  -- 0 = chưa bắt đầu, 1/2/3 = đang trong chuỗi Cool
```

---

## Warm-up Strategy Matrix

Bảng tổng hợp chiến lược warm-up theo stage và thời gian im lặng:

| Stage | Thời gian im lặng | Chiến lược Warm-up | Ví dụ tin nhắn |
| --- | --- | --- | --- |
| Follower | 3–5 ngày | Mở lời, hỏi thăm | _"Chào bạn 🙏 Bạn có muốn tìm hiểu về thiền không?"_ |
| Curious Seeker | 3–7 ngày | Nhắc lớp miễn phí | _"Lớp thiền miễn phí sắp khai giảng [date] 🧘"_ |
| Curious Seeker | 7–14 ngày | Chia sẻ tips thiền | _"Mình chia sẻ bạn cách thiền đơn giản tại nhà nhé 🌿"_ |
| Registered | 1 ngày trước lớp | Reminder lịch học | _"Ngày mai lớp bắt đầu 19:30, bạn nhớ nhé!"_ |
| Registered | 2 ngày sau no-show | Mời đăng ký lại | _"Bạn chưa kịp tham gia? Lớp tiếp theo [date]!"_ |
| Deep Learner | 2 buổi vắng | Re-engage | _"Nhóm thiền nhớ bạn! Cần hỗ trợ gì không? 🙏"_ |
| Deep Learner | Milestone | Chúc mừng | _"Chúc mừng bạn đã hoàn thành tuần 12! 🎉"_ |
| Sahaja Yogi | Lâu dài | Tri ân + invite | _"Cảm ơn bạn đã đồng hành cùng SY VN ❤️"_ |

---

## Event Advertising Strategy

| Điều kiện | Hành động | Ưu tiên |
| --- | --- | --- |
| Có sự kiện mới tại city X | Gửi tin nhắn cho seekers tại city X | `Registered` > `Curious Seeker` > `Follower` |
| Seeker ở city khác nhưng event online | Gửi cho tất cả seekers từ Stage 1+ | `Deep Learner` > `Registered` |
| Event Thiền Âm nhạc | Target seekers hỏi về _"âm nhạc"_ hoặc _"trị liệu"_ | Nhánh A interest |
| Khai giảng lớp 4 tuần | Target seekers hỏi về _"lớp học"_ hoặc _"thiền"_ | Nhánh B interest |

### Constraints

- Max 1 event notification per seeker per event
- Không gửi quảng cáo cho seekers đã unsubscribe hoặc bị đánh dấu spam
- Ưu tiên seekers có `lead_stage` = `Seeker` hoặc `Seeker_Public_Program`

---

## MAS Route Mapping

| Route | Mục đích | Stage áp dụng |
| --- | --- | --- |
| **Inbox MAS** | Auto-classify và soạn draft tin nhắn cho người review gửi thủ công | All stages (reactive) |
| **Route 1: React** | React comment/message | Stage 1–2 |
| **Route 2: Warm-up** | Proactive nurturing dormant seekers | Stage 1–4 |
| **Route 3: Event** | City-targeted event notifications | Stage 1–5 |

### Route × Stage Coverage Matrix

```text
              Stage 0   Stage 1    Stage 2    Stage 3    Stage 4    Stage 5
              User      Follower   Curious    Registered Deep Learn Yogi
  ┌─────────┬──────────┬──────────┬──────────┬──────────┬──────────┬──────────┐
  │Inbox MAS│    ·     │    ██    │    ██    │    ██    │    ██    │    ██    │
  │(React.) │          │ classify │ classify │ classify │ classify │ classify │
  ├─────────┼──────────┼──────────┼──────────┼──────────┼──────────┼──────────┤
  │Route 1  │    ·     │    ██    │    ██    │    ·     │    ·     │    ·     │
  │React    │          │ ❤️ react │ reply    │          │          │          │
  ├─────────┼──────────┼──────────┼──────────┼──────────┼──────────┼──────────┤
  │Route 2  │    ·     │    ██    │    ██    │    ██    │    ██    │    ·     │
  │Warm-up  │          │ mở lời  │ nhắc lớp │ reminder │ re-engage│          │
  ├─────────┼──────────┼──────────┼──────────┼──────────┼──────────┼──────────┤
  │Route 3  │    ·     │    ██    │    ██    │    ██    │    ██    │    ██    │
  │Event    │          │ city-ad  │ interest │ cross-   │ community│ organize │
  │         │          │          │ match    │ sell evt │ event    │ + lead   │
  └─────────┴──────────┴──────────┴──────────┴──────────┴──────────┴──────────┘
    ██ = Active       · = Không áp dụng
```

---

## QA Check Gates — Kiểm soát Chất lượng MAS

> Mỗi stage transition và mỗi MAS action đều phải qua **QA Gate** trước khi thực thi. Gate = điều kiện tối thiểu bắt buộc đạt trước khi cho phép chuyển tiếp hoặc gửi tin nhắn.

### Pipeline QA Flow

```text
┌──────────┐    GATE 1    ┌──────────┐    GATE 2    ┌──────────┐    GATE 3    ┌──────────┐
│ Stage 0  │──────✓──────→│ Stage 1  │──────✓──────→│ Stage 2  │──────✓──────→│ Stage 3  │
│  User    │              │ Follower │              │ Curious  │              │Registered│
└──────────┘              └──────────┘              └──────────┘              └──────────┘
                                                                                  │
                               ┌──────────┐    GATE 5    ┌──────────┐    GATE 4   │
                               │ Stage 5  │◄─────✓──────│ Stage 4  │◄─────✓──────┘
                               │  Yogi    │              │Deep Learn│
                               └──────────┘              └──────────┘

    ✓ = QA Gate PASS        ✗ = BLOCK (không cho chuyển stage)
    Mỗi Gate kiểm tra: DATA · CONSENT · TIMING · CONTENT
```

### Gate Criteria theo Stage

| Gate | Transition | Tiêu chí bắt buộc | Nếu FAIL |
| --- | --- | --- | --- |
| **G1** | User → Follower | ✅ Có ≥1 touch-point ghi nhận (comment/react/DM) | ❌ Giữ User, không warm-up |
| **G2** | Follower → Curious | ✅ Seeker chủ động hỏi (DM hoặc comment hỏi info) | ❌ Giữ Follower, tiếp tục content |
| **G3** | Curious → Registered | ✅ Có SĐT hoặc email hợp lệ · ✅ Có lớp/event cụ thể | ❌ Giữ Curious, hỏi lại thông tin |
| **G4** | Registered → Deep | ✅ Đã tham dự ≥3/4 buổi lớp nhập môn | ❌ Giữ Registered, mời lớp mới |
| **G5** | Deep → Yogi | ✅ Hoàn thành 18 tuần · ✅ Thực hành ≥3 tháng | ❌ Giữ Deep Learner, encourage |

### Message QA Gate — Trước khi gửi tin nhắn

```text
Tin nhắn được tạo bởi MAS
         │
         ▼
    ┌─────────────────┐
    │ CHECK 1: Timing │──── Lần warm-up cuối > 7 ngày? ──── NO → ⛔ BLOCK (anti-spam)
    └────────┬────────┘
             │ YES
             ▼
    ┌─────────────────┐
    │ CHECK 2: Status │──── Seeker = unsubscribed/dormant? ── YES → ⛔ BLOCK
    └────────┬────────┘
             │ NO
             ▼
    ┌─────────────────┐
    │ CHECK 3: Content│──── Tin nhắn có cá nhân hóa? ─────── NO → ⚠️ REVIEW thủ công
    └────────┬────────┘
             │ YES
             ▼
    ┌─────────────────┐
    │ CHECK 4: Tone   │──── Giọng văn ấm áp, không sales? ── NO → ⚠️ REWRITE
    └────────┬────────┘
             │ YES
             ▼
    ┌─────────────────┐
    │ TELEGRAM HITL   │──── Gửi Telegram chờ duyệt:
    └────────┬────────┘     • Nhận REPLY ──→ LLM sửa nội dung ──→ Soạn lại propose
             │              • Nhận LIKE (👍) ──→ DUYỆT PASS
             ▼
       ✅ GỬI TIN NHẮN (Hit Enter thực thi)
```

### Quick Audit Checklist

Chạy audit định kỳ (tuần/tháng) để phát hiện lệch chiến lược:

| # | Câu hỏi kiểm tra | Query hint | Kỳ vọng |
| --- | --- | --- | --- |
| 1 | Có seeker nào Stage 3+ mà thiếu SĐT/email? | `lead_stage IN (...) AND phone IS NULL AND email IS NULL` | 0 records |
| 2 | Có seeker nào nhận >1 warm-up trong 7 ngày? | `warmup_count` vs `last_warmup_at` gap | 0 violations |
| 3 | Có seeker `unsubscribed` vẫn nhận tin? | `temperature = 'unsubscribed' AND last_warmup_at > date(...)` | 0 records |
| 4 | Có seeker `cold` bị warm-up >1 lần/tháng? | `temperature = 'cold' AND warmup_count ...` | 0 violations |
| 5 | Tỷ lệ phản hồi warm-up Route 2? | Response count / warm-up sent count | >15% là tốt |
| 6 | Seeker Stage 2 im lặng >14 ngày không chuyển Cool? | `last_interaction` check | 0 missed |

### Anti-patterns — Dấu hiệu MAS đang chạy sai

```text
⚠️  ANTI-PATTERN                          ✅  ĐÚNG CÁCH
─────────────────────────────────────────────────────────────────────
❌ Gửi 3 tin trong 5 ngày cho 1 seeker    → Max 1 tin / 7 ngày
❌ Warm-up seeker chưa từng DM            → Chỉ warm-up từ Stage 1+
❌ Chuyển Stage không kiểm tra Gate        → Luôn check Gate trước
❌ Gửi event cho seeker khác city          → Match city trước khi gửi
❌ Reply máy móc, copy-paste              → Cá nhân hóa (tên, city, interest)
❌ Tiếp tục gửi khi seeker nói "dừng"     → Instant stop → unsubscribed
❌ Escalate tần suất khi seeker nguội      → Luôn giảm dần frequency
```

### Tổng kết Gate

```text
                    DATA         CONSENT       TIMING        CONTENT
                   ─────        ─────────     ────────      ─────────
  Stage Gate:     [✓ touch]    [✓ opt-in]   [✓ interval]  [  N/A   ]
  Message Gate:   [✓ valid]    [✓ active ]  [✓ anti-spam] [✓ tone  ]
                        │            │            │             │
                        └────────────┴────────────┴─────────────┘
                                          │
                                   ALL ✓ = PROCEED
                                   ANY ✗ = BLOCK / REVIEW
```

---

## Telegram Human-in-the-Loop (HITL) Workflow

> Thay vì tự động gửi tin nhắn hoặc chỉ lưu nháp chờ người dùng review trực tiếp trên trang Facebook, MAS áp dụng cơ chế **Human-in-the-Loop qua Telegram** cho TẤT CẢ các nghiệp vụ: Inbox, Comment, Warm-up, và Event.

### Cấu hình Telegram
- **Group ID**: `-1003703002550` (biến môi trường `SYVN_TELEGRAM_GROUP_ID`)
- **Bot Token**: Lưu tại `TELEGRAM_BOT_TOKEN` trong file `.env`

### Quy trình xét duyệt (Approval Flow)
Mọi đề xuất hành động từ MAS (soạn tin nhắn inbox, reply comment, danh sách warm-up, hoặc event) đều phải trải qua luồng duyệt bằng reaction trên Telegram:

1. **Gợi ý (Propose)**: Agent gửi tin nhắn đề xuất (nội dung draft, list khách hàng target) vào group Telegram.
2. **Lắng nghe (Listen)**: Agent lắng nghe phản hồi đối với tin nhắn đề xuất đó.
   - **Trường hợp 1 (Duyệt - LIKE)**: Nếu tin nhắn nhận được reaction biểu tượng **LIKE** (👍), MAS tiến hành thực thi hành động (hit enter gửi tin nhắn Facebook / tiến hành chiến dịch gửi luồng).
   - **Trường hợp 2 (Điều chỉnh - REPLY)**: Nếu Operator/Admin **reply** lại tin nhắn đó với nội dung chỉ đạo cụ thể:
     - Agent đọc nội dung reply.
     - Dựng LLM prompt để phân tích yêu cầu của người duyệt, kết hợp context hiện tại.
     - Soạn lại một đề xuất mới và gửi lại vào Telegram.
     - Vòng lặp tiếp tục: Chỉ khi đề xuất _mới_ nhận được reaction **LIKE**, MAS mới thực thi lệnh gửi thực sự.

### Áp dụng cho các MAS Route
- **Inbox MAS (Task 1)**: Soạn draft reply cho DM → gửi Telegram duyệt → LIKE → Hit Enter gửi Messenger.
- **Route 1 - React/Reply Comment (Task 2)**: Đề xuất nội dung trả lời comment Fanpage → gửi Telegram duyệt → LIKE → gửi reply comment thật trên Fanpage.
- **Route 2 - Warm-up & Route 3 - Event (Task 3)**: Lên plan danh sách target và nội dung message mẫu → gửi Telegram duyệt → Administrator có thể reply để yêu cầu phân tích lại (vd: "Loại bỏ người dùng A", "Chỉnh lại văn phong dài hơn") → Agent lên lại plan mới theo yêu cầu → LIKE → thực thi gửi hàng loạt.

---

## Alignment với Journey Engine (`journey-engine.ts`)

| mas_strategy Stage | `lead_stage` trong DB | Journey Engine Key |
| --- | --- | --- |
| Stage 0: User | `User` | `User` |
| Stage 1: Follower | `Seeker` | `User` → `Seeker` |
| Stage 2: Curious Seeker | `Seeker` | `Seeker` |
| Stage 3: Registered | `Seeker_Public_Program` | `Seeker_Public_Program` |
| Stage 4: Deep Learner | `Seeker_18_Weeks` | `Seeker_18_Weeks` |
| Stage 5: Sahaja Yogi | `Seed` → `Sahaja_Mahayogi` | `Seed` → `Sahaja_Mahayogi` |

---

## End-to-End Pipeline — AI Agent Execution Guide

> Sơ đồ toàn bộ luồng xử lý từ khi nhận signal đến khi gửi tin nhắn.
> AI Agent PHẢI tuân theo pipeline này cho MỌI action.

```text
  ╔═══════════════════════════════════════════════════════════════════════════╗
  ║                     FULL MAS EXECUTION PIPELINE                         ║
  ╚═══════════════════════════════════════════════════════════════════════════╝

  PHASE 1: DATA INGESTION (Thu thập dữ liệu)
  ────────────────────────────────────────────
  Facebook Page
      │
      ├── fetch_fb_messages.py ──→ messages table  ──→ users table
      ├── webhook_comments.py  ──→ comments table  ──→ comment_users table
      └── city classification  ──→ users.city_classified


  PHASE 2: CLASSIFICATION (Phân loại)
  ────────────────────────────────────
  New signal arrives (DM / Comment / Reaction)
      │
      ▼
  ┌──────────────────┐
  │  Inbox MAS       │
  │  (ADK Agent)     │
  │                  │
  │  1. Classifier   │──→ intent: greeting / ask_class / ask_event / register / other
  │  2. Responder    │──→ draft reply (từ lop-hoc.md, su-kien.md, faq.md)
  │  3. Human review │──→ người vận hành kiểm tra và gửi thủ công
  │  4. Telegram     │──→ notify yogis khi cần follow-up thủ công
  └──────────────────┘


  PHASE 3: STAGE EVALUATION (Đánh giá giai đoạn)
  ───────────────────────────────────────────────
      │
      ▼
  ┌────────────────────────────────────────────────────────┐
  │  Xác định Stage hiện tại (từ users.lead_stage)        │
  │                                                        │
  │  Có đủ điều kiện chuyển Stage? ──→ Check QA Gate      │
  │      │                                                 │
  │      ├── PASS ──→ UPDATE lead_stage ──→ Log transition │
  │      └── FAIL ──→ Giữ nguyên Stage ──→ Log reason     │
  └────────────────────────────────────────────────────────┘


  PHASE 4: TEMPERATURE CHECK (Kiểm tra nhiệt độ)
  ───────────────────────────────────────────────
      │
      ▼
  ┌────────────────────────────────────────────────────────┐
  │  silence_days = NOW() - last_interaction_at            │
  │                                                        │
  │  Lookup "Ngưỡng nhiệt độ theo Stage" ──→ temperature   │
  │      │                                                 │
  │      ├── 🔥 HOT   ──→ Maintain (draft reply)           │
  │      ├── 🟡 WARM  ──→ Stimulate (Route 2)              │
  │      ├── 🔵 COOL  ──→ Re-engage (3-step sequence)      │
  │      ├── ❄️ COLD  ──→ Last Resort → Dormant            │
  │      └── 🚫 UNSUB ──→ STOP all proactive               │
  └────────────────────────────────────────────────────────┘


  PHASE 5: MESSAGE QA GATE & TELEGRAM HITL
  ───────────────────────────────────────────────
      │
      ▼
  ┌────────────────────────────────────────────────────────┐
  │  CHECK 1-4: Rules Validation (Timing, Tone, Limits)    │
  │      │                                                 │
  │      ▼ ALL ✅                                          │
  │  TELEGRAM PROPOSE: Gửi nội dung draft/plan vào Group   │
  │      │                                                 │
  │      ├── Nhận REPLY ──→ LLM phân tích lại ──→ Propose  │
  │      └── Nhận LIKE 👍 ──→ APPROVE (Chuyển Phase 6)    │
  └────────────────────────────────────────────────────────┘


  PHASE 6: EXECUTION (Thực thi Auto-Send)
  ─────────────────────────────
      │
      ▼
  ┌─────────────────────────────────────────────────────┐
  │  (Chỉ thực thi khi đã có LIKE reaction từ Telegram)  │
  │                                                      │
  │  Route 1: React    ──→ Gửi ❤️ react + reply comment │
  │  Route 2: Warm-up  ──→ Hit enter gửi DM hàng loạt    │
  │  Route 3: Event    ──→ Hit enter gửi Event notify    │
  │  Inbox MAS         ──→ Hit enter gửi DM Inbox        │
  │                                                      │
  │  UPDATE: last_warmup_at, warmup_count, cool_step    │
  │  LOG:    action + Universal ID to ./logs/            │
  └─────────────────────────────────────────────────────┘
```
