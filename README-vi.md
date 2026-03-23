# Chat Bot phục vụ chăm sóc Seekers (Sahaja Yoga Vietnam)

Chào mừng bạn đến với repo **Funnel Tracking**. Mục tiêu của dự án này là tạo ra một nhóm AI Agents phục vụ lưu trữ và quản lý danh sách seekers (học viên mới) của Sahaja Yoga Vietnam. Điểm nhấn chính là một Chat Bot dùng để chăm sóc seekers khi họ nhắn hỏi vào Facebook Fanpage, sau đó tự động thông báo sang nhóm Telegram để thuận tiện xử lý và phản hồi.

## 🌟 Các tính năng chính

1. **Facebook Fanpage Integration**: Tự động fetch và xử lý tin nhắn từ seekers mới.
2. **Telegram Notification**: Forward tin nhắn sang nhóm Telegram chỉ định.
3. **Lưu trữ thông tin Seekers**: Lưu trữ dữ liệu seekers (SĐT, email, thành phố, FB URL) trong FrankenSQLite.
4. **Agent Memory**: Inbox MAS nạp `memory/SOUL.md`, `memory/agent_memory/faq.md`, `memory/agent_memory/lop-hoc.md`, `memory/agent_memory/su-kien.md`, `memory/research.md`, và `memory/mas_strategy.md` vào runtime `knowledge_context`.
5. **AI Agent Software (ADK)**: Google ADK vận hành luồng trả lời inbox (Classifier → Responder), còn các route react / warm-up / event hiện là scaffold trong scheduler và tools.
6. **Web Dashboard**: Ứng dụng Next.js 16 với Dashboard, Seekers CRM, Network Graph, Journey Workflow.

## 🏗 Kiến trúc dự án & Quy tắc

Dự án được khởi tạo theo phương pháp Agile XP dành cho AI Agents. Quy tắc cốt lõi trong [`GEMINI.md`](GEMINI.md).

| Thư mục                | Mục đích                                                                |
| ---------------------- | ----------------------------------------------------------------------- |
| `.agents/rules/`       | Quy tắc hành vi của AI Agents (Git Operations, Tool Writing, ADK, DevOps QA) |
| `.agents/workflows/`   | Quy trình làm việc của agents                                           |
| `.agents/skills/`      | Kĩ năng (skills) mà agents có thể sử dụng                               |
| `adk_agents/`          | Google ADK agents cho inbox reply cùng định nghĩa route react / warm-up / event |
| `tools/`               | Script Python 3.13 CLI và các wrapper chuẩn `l5_*` cho operator / scheduler |
| `web/`                 | Ứng dụng web Next.js 16 (Dashboard, Seekers CRM, Graph, Journey)        |
| `tests/`               | Unit tests cho tất cả tools                                             |
| `memory/agent_memory/` | Kiến thức — khóa học, sự kiện, log seekers, FrankenSQLite DB            |
| `logs/`                | Báo cáo chuyển giao và logs                                             |
| `docs/`                | Tài liệu kiến trúc (`ARCHITECTURE.md`)                                   |

## 🔧 Công cụ Fetch Tin Nhắn Facebook (`tools/fetch_fb_messages.py`)

Công cụ CLI sử dụng Playwright để fetch tin nhắn từ Facebook Business Inbox.

### Cách hoạt động

1. **Mở FB Business Inbox** qua CDP credentials đã lưu
2. **Cuộn sidebar tin nhắn** bằng `mouse.wheel()` để kích hoạt FB tải thêm threads
3. **Click từng thread hiển thị** và trích xuất tin nhắn, quảng cáo, timestamp
4. **Lưu vào FrankenSQLite** — bảng `threads`, `messages`, `users`
5. **Lọc theo ngày** — dừng khi thread vượt quá `--time_range`

### Cách sử dụng

```bash
# Fetch tin nhắn trong 7 ngày gần nhất
python tools/fetch_fb_messages.py --pageId <PAGE_ID> --credential <CRED_NAME> --time_range 7d --action fetch_messages

# Bắt buộc fetch mới (bỏ qua cache 1 giờ)
python tools/fetch_fb_messages.py --pageId <PAGE_ID> --credential <CRED_NAME> --action fetch_messages --refresh

# Liệt kê danh sách users theo thời gian tương tác
python tools/fetch_fb_messages.py --pageId <PAGE_ID> --action get_list_unique_user --time_range 7d

# Xem tin nhắn của một user cụ thể
python tools/fetch_fb_messages.py --pageId <PAGE_ID> --action fetch_message_by_user --userId <THREAD_ID>
```

### Tham số CLI

| Tham số        | Mặc định         | Mô tả                                                                        |
| -------------- | ---------------- | ---------------------------------------------------------------------------- |
| `--pageId`     | _bắt buộc_       | Facebook Page ID                                                             |
| `--credential` | `default`        | Tên CDP credential                                                           |
| `--time_range` | `7d`             | Khoảng thời gian: `1d`, `7d`, `30d`, `90d`                                   |
| `--action`     | `fetch_messages` | Hành động: `fetch_messages`, `get_list_unique_user`, `fetch_message_by_user` |
| `--refresh`    | `false`          | Bắt buộc fetch mới, bỏ qua cache 1 giờ                                       |
| `--maxThreads` | `200`            | Số threads tối đa để đồng bộ                                                 |
| `--userId`     | `None`           | User ID cho hành động `fetch_message_by_user`                                |

### Database Schema (FrankenSQLite)

- **`threads`**: `id`, `page_id`, `thread_name`, `last_synced_time`
- **`messages`**: `thread_id`, `sender`, `content`, `message_timestamp`, `seq` (UNIQUE cùng sender/content/timestamp)
- **`users`**: `thread_id`, `thread_name`, `phone`, `email`, `fb_url`, `city`, `last_interaction`, `last_synced_at`
- **`auto_replies`**: nhật ký audit trả lời inbox có hỗ trợ `dry_run`
- **`fetch_log`**: `page_id`, `timestamp`, `threads_count`, `messages_count`

## 🛤 Các giai đoạn seeker

```text
Web journey:     Unknown → Seeker → Seeker_Public_Program → Seeker_18_Weeks → Seed → Sahaja_Yogi → Sahaja_Yogi_Dedicated → Sahaja_Mahayogi
Strategy labels: User    → Follower / Curious Seeker      → Registered      → Deep Learner   → Sahaja Yogi
```

Journey engine phía web định nghĩa runtime keys, còn MAS strategy chuẩn hóa chúng thành nhãn seeker-facing.

## 🔑 Universal IDs và Bảo mật

- **Universal ID**: Mọi thành phần theo cấu trúc `<type>:<section-name-XXX>[:<component_name-YYY>]`.
- **Bảo mật**: API keys lưu trong `.env`, encode/decode qua `tools/env_manager.py`.

## 🛠 Bắt đầu

1. Clone repo.
2. Tạo môi trường Python 3.13: `python3.13 -m venv .venv && source .venv/bin/activate`
3. Cài đặt Python: `pip install -e .` (dùng `pyproject.toml`)
4. Cài đặt trình duyệt: `playwright install chromium`
5. Cấu hình credentials: `python tools/env_manager.py`
6. Chạy tools: `python tools/fetch_fb_messages.py --pageId <PAGE_ID> --action fetch_messages`
7. Chạy ADK agents: `adk run adk_agents/` (CLI) hoặc `adk web .` từ thư mục gốc repo (Web UI; sau đó chọn `adk_agents`)
8. Chạy Web Dashboard: `cd web && npm install && npm run dev`

## 📖 Tài liệu

- [English (README.md)](README.md) | [Tiếng Việt (README-vi.md)](README-vi.md)

---

_Dự án Open Source được phát triển bởi cộng đồng._
