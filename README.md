# Chat Bot for Seeker Care (Sahaja Yoga Vietnam)

Welcome to the **Funnel Tracking** repository. This project aims to build an AI Agents group dedicated to storing and managing the list of seekers (new learners) of Sahaja Yoga Vietnam. The main objective is to provide a Chat Bot that serves to care for new seekers when they send inquiry messages to the Facebook Fanpage, and automatically notifies the Telegram group for convenient and timely responses.

## 🌟 Key Features

1. **Facebook Fanpage Integration**: Automatically process and respond to messages from new seekers.
2. **Telegram Notification**: Forward inquiries to a designated Telegram group.
3. **Seeker Information Storage**: Store and manage seeker data primarily by phone number (converted to the `0xxxxxxxxx` format for Vietnamese phone numbers, but adjustable for other countries).
4. **Agent Memory**: Utilize a `memory/agent/` directory consisting of Markdown documents (such as `lop-hoc.md`, `su-kien.md`) to provide the conversational agents with context about current courses and events.

## 🏗 Project Architecture & Rules

This project is initialized according to the Agile XP methodology tailored for AI Agents. All core operational guidelines are consolidated in [`GEMINI.md`](GEMINI.md).

- **`.agents/rules/`**: Rules governing the behavior and boundaries of the AI agents (e.g., Git Operations, Tool Writing, DevOps QA).
- **`.agents/workflows/`**: Workflow configurations defining the steps agents take to accomplish tasks.
- **`.agents/skills/`**: Specific capabilities that agents can utilize.
- **`tools/`**: Python 3.13 scripts and utilities acting as CLIs (`webhook_comments.py`, `env_manager.py`).
- **`memory/agent_memory/`**: The knowledge base of the agents, storing course lists, event details, and seeker logs.
- **`logs/`**: Directory for iteration handover reports and execution logs.

## 🔑 Universal IDs and Security

- **Universal ID**: Every component must be assigned a Universal ID following the `<type>:<section-name-XXX>[:<component_name-YYY>]` format.
- **Security Check**: Secure API keys and tokens are stored in the `.env` file but must be encoded/decoded using `tools/env_manager.py` to prevent Git leaks.

## 📖 English and Vietnamese Documentation

We provide documentation in both [English (README.md)](README.md) and [Vietnamese (README-vi.md)](README-vi.md) to facilitate open-source contributions.

## 🛠 Getting Started

1. Clone the repository.
2. Install Python dependencies (Wait for `requirements.txt`).
3. Set up the environment variables for Facebook Webhooks and Telegram Bot tokens.
4. Run the API endpoints to start listening for events.

---

_This project is Open Source._
