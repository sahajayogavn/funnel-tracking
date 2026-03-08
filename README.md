# Chat Bot for Seeker Care (Sahaja Yoga Vietnam)

Welcome to the **Funnel Tracking** repository. This project builds AI Agents to store and manage seekers (new learners) of Sahaja Yoga Vietnam. The main objective is a Chat Bot that cares for new seekers when they message the Facebook Fanpage, and automatically notifies the Telegram group for timely responses.

## 🌟 Key Features

1. **Facebook Fanpage Integration**: Automatically fetch and process inbox messages from seekers.
2. **Telegram Notification**: Forward inquiries to a designated Telegram group.
3. **Seeker Information Storage**: Store and manage seeker data (phone, email, city, FB URL) in FrankenSQLite.
4. **Agent Memory**: Utilize `memory/agent_memory/` Markdown documents (`lop-hoc.md`, `su-kien.md`) for conversational context.

## 🏗 Project Architecture & Rules

Initialized according to Agile XP methodology for AI Agents. All core guidelines are in [`GEMINI.md`](GEMINI.md).

| Directory              | Purpose                                                                     |
| ---------------------- | --------------------------------------------------------------------------- |
| `.agents/rules/`       | Rules governing AI agent behavior (Git Operations, Tool Writing, DevOps QA) |
| `.agents/workflows/`   | Workflow configurations for agent tasks                                     |
| `.agents/skills/`      | Agent capabilities and skills                                               |
| `tools/`               | Python 3.13 CLI scripts (`fetch_fb_messages.py`, `env_manager.py`)          |
| `tests/`               | Unit tests for all tools                                                    |
| `memory/agent_memory/` | Knowledge base — course lists, events, seeker logs, FrankenSQLite DB        |
| `logs/`                | Iteration handover reports and execution logs                               |

## 🔧 Facebook Message Fetcher (`tools/fetch_fb_messages.py`)

A Playwright-based CLI tool that fetches messages from Facebook Business Inbox.

### How It Works

1. **Opens FB Business Inbox** via saved CDP credentials
2. **Scrolls the thread sidebar** using `mouse.wheel()` to trigger FB's React infinite scroll
3. **Clicks each visible thread** and extracts messages, ad context, timestamps
4. **Saves to FrankenSQLite** — `threads`, `messages`, `users` tables
5. **Date filtering** — stops when threads exceed `--time_range`

### Usage

```bash
# Fetch messages from the last 7 days
python tools/fetch_fb_messages.py --pageId <PAGE_ID> --credential <CRED_NAME> --time_range 7d --action fetch_messages

# Force refresh (bypass 1-hour cache)
python tools/fetch_fb_messages.py --pageId <PAGE_ID> --credential <CRED_NAME> --action fetch_messages --refresh

# List unique users sorted by last interaction
python tools/fetch_fb_messages.py --pageId <PAGE_ID> --action get_list_unique_user --time_range 7d

# Fetch messages for a specific user
python tools/fetch_fb_messages.py --pageId <PAGE_ID> --action fetch_message_by_user --userId <THREAD_ID>
```

### CLI Arguments

| Argument       | Default          | Description                                                               |
| -------------- | ---------------- | ------------------------------------------------------------------------- |
| `--pageId`     | _required_       | Facebook Page ID                                                          |
| `--credential` | `default`        | CDP credential name                                                       |
| `--time_range` | `7d`             | Time range: `1d`, `7d`, `30d`, `90d`                                      |
| `--action`     | `fetch_messages` | Action: `fetch_messages`, `get_list_unique_user`, `fetch_message_by_user` |
| `--refresh`    | `false`          | Force fresh fetch, bypass 1-hour cache                                    |
| `--maxThreads` | `200`            | Maximum number of threads to sync                                         |
| `--userId`     | `None`           | User ID for `fetch_message_by_user` action                                |

### Database Schema (FrankenSQLite)

- **`threads`**: `id`, `page_id`, `thread_name`, `last_synced_time`
- **`messages`**: `thread_id`, `sender`, `content`, `message_timestamp` (UNIQUE)
- **`users`**: `thread_id`, `thread_name`, `phone`, `email`, `fb_url`, `city`, `last_interaction`
- **`fetch_log`**: `page_id`, `timestamp`, `threads_count`, `messages_count`

## 🔑 Universal IDs and Security

- **Universal ID**: Every component follows `<type>:<section-name-XXX>[:<component_name-YYY>]` format.
- **Security**: API keys stored in `.env`, encoded/decoded via `tools/env_manager.py`.

## 🛠 Getting Started

1. Clone the repository.
2. Create Python 3.13 virtual environment: `python3.13 -m venv .venv && source .venv/bin/activate`
3. Install dependencies: `pip install playwright && playwright install chromium`
4. Set up credentials: `python tools/env_manager.py`
5. Run: `python tools/fetch_fb_messages.py --pageId <PAGE_ID> --action fetch_messages`

## 📖 Documentation

- [English (README.md)](README.md) | [Tiếng Việt (README-vi.md)](README-vi.md)

---

_This project is Open Source._
