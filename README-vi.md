# Chat Bot phục vụ chăm sóc Seekers (Sahaja Yoga Vietnam)

Chào mừng bạn đến với repo **Funnel Tracking**. Mục tiêu của dự án này là tạo ra một nhóm AI Agents phục vụ công việc lưu trữ và quản lý danh sách seekers (các học viên mới) của Sahaja Yoga Vietnam. Điểm nhấn chính là một Chat Bot dùng để chăm sóc seekers khi họ nhắn hỏi vào Facebook Fanpage, sau đó tự động hệ thống sẽ notify (thông báo) sang nhóm Telegram để thuận tiện cho việc xử lý và phản hồi.

## 🌟 Các tính năng chính

1. **Facebook Fanpage Integration**: Tự động xử lý và trả lời tin nhắn từ seekers mới.
2. **Telegram Notification**: Forward tin nhắn hoặc thông báo sang nhóm Telegram chỉ định.
3. **Lưu trữ thông tin Seekers**: Lưu trữ và quản lý dữ liệu seekers theo số điện thoại (được convert sang chuẩn `0xxxxxxxxx` của Việt Nam, có thể tùy chỉnh cho các quốc gia khác).
4. **Agent Memory**: Sử dụng thư mục `memory/agent_memory/` lưu các file Markdown (`lop-hoc.md`, `su-kien.md`) làm ngữ cảnh để bot trả lời thông tin về khóa học và sự kiện.

## 🏗 Kiến trúc dự án & Quy tắc

Dự án này được khởi tạo tuân theo phương pháp Agile XP dành riêng cho AI Agents. Tất cả các quy tắc hoạt động cốt lõi được tổng hợp trong file [`GEMINI.md`](GEMINI.md).

- **`.agents/rules/`**: Các quy tắc định hình hành vi (rules) của AI Agents (như Git Operations, Tool Writing, DevOps QA).
- **`.agents/workflows/`**: Quy trình làm việc (workflows) mà agents sẽ tuân theo.
- **`.agents/skills/`**: Các kĩ năng cụ thể (skills) mà agents có thể sử dụng (ví dụ: lấy dữ liệu từ comment, format dữ liệu).
- **`tools/`**: Chứa các script Python 3.13 đóng vai trò làm công cụ CLI (`webhook_comments.py`, `env_manager.py`).
- **`memory/agent_memory/`**: Nơi lưu giữ kiến thức mạng cho agents, các thông tin về khóa học, log sự kiện, thông tin seekers.
- **`logs/`**: Thư mục lưu các báo cáo chuyển giao (iteration handover reports) và logs.

## 🔑 Universal IDs và Bảo mật

- **Universal ID**: Mọi thành phần đều phải được gán một Universal ID theo cấu trúc `<type>:<section-name-XXX>[:<component_name-YYY>]`.
- **Kiểm soát Bảo mật**: API keys và tokens được lưu trữ bảo mật trong file `.env` sử dụng cơ chế encode/decode của script `tools/env_manager.py` để tránh rò rỉ lên Git.

## 📖 Tài liệu

Đây là dự án Mã nguồn mở (Open Source), chúng tôi cung cấp file README bằng cả tiếng Anh và tiếng Việt:

- [Tiếng Anh (README.md)](README.md)
- [Tiếng Việt (README-vi.md)](README-vi.md)

---

_Dự án Open Source được phát triển bởi cộng đồng._
