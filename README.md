# Chat Bot for Seeker Care (Sahaja Yoga Vietnam)

Welcome to the **Funnel Tracking** repository. This project builds AI Agents to store and manage seekers (new learners) of Sahaja Yoga Vietnam. The main objective is a Chat Bot that cares for new seekers when they message the Facebook Fanpage, and automatically notifies the Telegram group for timely responses.

## 🌟 Key Features

1. **Facebook Fanpage Integration**: Automatically fetch and process inbox messages from seekers.
2. **Telegram Notification**: Forward inquiries to a designated Telegram group.
3. **Seeker Information Storage**: Store and manage seeker data (phone, email, city, FB URL) in FrankenSQLite.
4. **Agent Memory**: Utilize `memory/agent_memory/` Markdown documents (`lop-hoc.md`, `su-kien.md`) for conversational context.
5. **Web Dashboard**: Next.js 16 full-stack application with:
   - 📊 **Dashboard**: Stats overview of all seekers, posts, messages
   - 👥 **Seekers CRM**: Sortable table with GitHub-style activity histogram, city badges, hover journey tooltip
   - 🕸️ **Network Graph**: WebGL-accelerated graph (Page → 7 Cities → Posts → Users)
   - 🛤️ **Journey Workflow**: React Flow AI pipeline from Unknown → Sahaja Mahayogi

## 🏗 Project Architecture & Rules

Initialized according to Agile XP methodology for AI Agents. All core guidelines are in [`GEMINI.md`](GEMINI.md).

| Directory              | Purpose                                                                                 |
| ---------------------- | --------------------------------------------------------------------------------------- |
| `.agents/rules/`       | Rules governing AI agent behavior (Git Operations, Tool Writing, Full-Stack, DevOps QA) |
| `.agents/workflows/`   | Workflow configurations for agent tasks                                                 |
| `.agents/skills/`      | Agent capabilities and skills (7 symlinked skills)                                      |
| `tools/`               | Python 3.13 CLI scripts (`fetch_fb_messages.py`, `fetch_comments.py`)                   |
| `web/`                 | Next.js 16 full-stack web application                                                   |
| `tests/`               | Unit tests for all tools                                                                |
| `memory/agent_memory/` | Knowledge base — course lists, events, seeker logs, FrankenSQLite DB                    |
| `logs/`                | Iteration handover reports and execution logs                                           |
| `docs/`                | Architecture documentation (`ARCHITECTURE.md`)                                          |

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

## 🔧 Facebook Comment Fetcher (`tools/fetch_comments.py`)

A Playwright-based CLI tool that fetches public comments from Facebook Page posts.

### How It Works

1. **Opens FB Business Inbox** (Post view) via saved CDP credentials
2. **Scrolls the post sidebar** to load all posts within date range
3. **Clicks each post thread** and extracts comments (dual JS strategy)
4. **Saves to FrankenSQLite** — `posts`, `comments`, `comment_users` tables
5. **Date filtering** — stops when posts exceed `--time_range`

### Usage

```bash
# Fetch comments from the last 90 days
python tools/fetch_comments.py --pageId <PAGE_ID> --credential <CRED_NAME> --time_range 90d --action fetch_comments

# List all comments for a specific post
python tools/fetch_comments.py --pageId <PAGE_ID> --action get_comments_by_post --postId <POST_ID>

# List unique commenters
python tools/fetch_comments.py --pageId <PAGE_ID> --action get_comment_users --time_range 30d
```

### Comment Database Schema

- **`posts`**: `id`, `page_id`, `post_name`, `post_url`, `last_synced_time`
- **`comments`**: `post_id`, `commenter_name`, `comment_text`, `fb_profile_url`, `fb_user_id`, `is_reply`, `comment_date`
- **`comment_users`**: `post_id`, `commenter_name`, `fb_user_id`, `fb_profile_url`, `phone`, `email`, `city`, `lead_stage`
- **`comment_fetch_log`**: `page_id`, `fetched_at`, `posts_found`, `comments_found`

## 📊 Data Model: Social Network → Customer Journey

```
Page (page_id = 1548373332058326)
├── Post (post_id) ← ads, video, image, text
│   ├── Comment (by UserID) ← public touch-point
│   │   └── Reply (by PageID) ← our response
│   └── comment_users CRM table
├── Thread (thread_id) ← private DM touch-point
│   ├── Message (by UserID or PageID)
│   └── users CRM table
└── Unified User Identity (fb_user_id + fb_profile_url)
    └── Customer Journey: Intake → Engaged → Registered → Attending
```

Every interaction (comment, message, reply) is a **touch-point** in the seeker's journey. The AI Agent uses these touch-points to personalize responses and maximize engagement at each funnel stage.

## 🌐 Web Dashboard (`web/`)

A Next.js 16 full-stack application that visualizes seeker data from FrankenSQLite.

### Setup

```bash
cd web && npm install && npm run dev
# Open http://localhost:3000
```

### Pages

| Route      | Description                                                        |
| ---------- | ------------------------------------------------------------------ |
| `/`        | Dashboard with stats overview                                      |
| `/seekers` | CRM table with GitHub-style histogram, search, sort, hover journey |
| `/graph`   | WebGL network graph: Page → 7 Cities → Posts → Users               |
| `/journey` | AI journey workflow: Unknown → Seeker → ... → Sahaja Mahayogi      |

### Tech Stack

- **Framework**: Next.js 16 (App Router, Turbopack)
- **Database**: better-sqlite3 (readonly) → existing `frankensqlite.db`
- **Graph**: react-force-graph-2d (Canvas2D WebGL-accelerated)
- **Journey**: @xyflow/react (React Flow)
- **Styling**: Tailwind CSS + custom dark theme

## 🛤️ Seeker Journey Stages

```
Unknown → Seeker → Public Program → 18 Weeks → Seed → Sahaja Yogi → Dedicated → Mahayogi
```

Each stage transition is triggered by touch-points (comments, messages, registrations, attendance).
The journey engine in `web/src/lib/journey-engine.ts` defines all transition rules.

## 🔑 Universal IDs and Security

- **Universal ID**: Every component follows `<type>:<section-name-XXX>[:<component_name-YYY>]` format.
- **Security**: API keys stored in `.env`, encoded/decoded via `tools/env_manager.py`.

## 🛠 Getting Started

1. Clone the repository.
2. Create Python 3.13 virtual environment: `python3.13 -m venv .venv && source .venv/bin/activate`
3. Install dependencies: `pip install playwright && playwright install chromium`
4. Set up credentials: `python tools/env_manager.py`
5. Run Python tools: `python tools/fetch_fb_messages.py --pageId <PAGE_ID> --action fetch_messages`
6. Run Web Dashboard: `cd web && npm install && npm run dev`

## 📖 Documentation

- [English (README.md)](README.md) | [Tiếng Việt (README-vi.md)](README-vi.md)
- [Architecture](docs/ARCHITECTURE.md)

---

_This project is Open Source._
