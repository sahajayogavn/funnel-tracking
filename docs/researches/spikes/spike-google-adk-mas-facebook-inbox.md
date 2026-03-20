# Spike: Google ADK Multi-Agent System for Facebook Inbox

> **ID**: `doc:spike-adk-mas-001`
> **Date**: 2026-03-20
> **Status**: Research
> **Goal**: Build a Google ADK-based Multi-Agent System (MAS) that reads Facebook inbox messages via CDP and auto-replies using defined workflows.

---

## Part 1: Google ADK Architecture

### 1.1 What is Google ADK?

Google's **Agent Development Kit (ADK)** is an open-source, **code-first** Python framework for building, evaluating, and deploying AI agents and multi-agent systems. Released at Google Cloud NEXT 2025.

- **Repository**: `github.com/google/adk-python`
- **Docs**: `google.github.io/adk-docs`
- **Install**: `pip install google-adk`
- **Python**: 3.9+ (3.10+ recommended)

| Principle | Description |
|---|---|
| **Code-First** | Define agent logic, tools, and orchestration directly in Python |
| **Multi-Agent by Design** | Compose multiple specialized agents into hierarchies |
| **Model-Agnostic** | Supports Gemini, GPT-4o, Claude, Mistral via LiteLLM |
| **Deployment-Agnostic** | Deploy on Cloud Run, GKE, local, or any platform |
| **Event-Driven Runtime** | Runner processes events yielded by agents |

### 1.2 Agent Types

#### LLM Agents (`LlmAgent` / `Agent`)
Powered by large language models. Configurable: `name`, `model`, `instruction`, `tools`, `sub_agents`. Support `output_key` for structured I/O and `{state_key}` template substitution.

```python
from google.adk.agents import Agent

root_agent = Agent(
    name="inbox_handler",
    model="gemini-2.0-flash",
    instruction="You handle incoming Facebook messages and route them appropriately.",
    tools=[classify_message, send_reply],
    sub_agents=[greeter_agent, qualifier_agent]
)
```

#### Workflow Agents (Orchestrators)
Non-LLM agents that control execution flow:

| Agent | Behavior |
|---|---|
| `SequentialAgent` | Executes sub-agents one after another, shares state |
| `ParallelAgent` | Executes sub-agents concurrently |
| `LoopAgent` | Repeats sub-agents until stop condition |

```python
from google.adk.agents import SequentialAgent, LlmAgent

classify = LlmAgent(name="Classify", output_key="category")
respond = LlmAgent(name="Respond", instruction="Based on {category}, craft reply.")
pipeline = SequentialAgent(name="InboxPipeline", sub_agents=[classify, respond])
```

#### Custom Agents (`BaseAgent`)
Inherit from `BaseAgent` for non-LLM logic (database lookups, API calls).

### 1.3 Runtime Architecture

```
User Message → Runner → Agent → LLM → Tool Call → Tool Result → LLM → Response
                 ↕
            Session/State
```

| Component | Role |
|---|---|
| **Runner** | Orchestrates execution loop, processes events |
| **Tools** | Python functions the agent can invoke |
| **Callbacks** | Hooks: `before_agent`, `after_agent`, `before_tool`, `after_tool` |
| **Session** | Conversation container (messages, state, context) |

### 1.4 Project Structure (ADK Standard)

```
funnel-tracking/
├── adk_agents/                   # ADK agent package
│   ├── __init__.py               # from .agent import root_agent
│   ├── agent.py                  # Root agent definition
│   ├── tools/                    # Tool functions
│   │   ├── facebook_tools.py     # CDP-based FB inbox read/reply
│   │   ├── seeker_tools.py       # CRM lookup/update
│   │   └── notification_tools.py # Telegram notifications
│   └── sub_agents/               # Specialized agents
│       ├── classifier.py         # Message intent classifier
│       ├── responder.py          # Auto-reply generator
│       └── escalator.py          # Human escalation handler
├── .env                          # API keys (Gemini, Telegram)
└── tools/                        # Existing CLI tools
    └── fetch_fb_messages.py      # CDP scraper (1,579 lines)
```

### 1.5 Running ADK Agents

```bash
# CLI
adk run adk_agents/

# Web UI (Dev)
adk web adk_agents/

# Programmatic (Production)
runner = Runner(agent=root_agent, app_name="funnel_mas")
```

---

## Part 2: Facebook Inbox via CDP (No Graph API)

### ⚠️ Critical Constraint

Facebook **closed the Graph API** for reading inbox messages. The only viable approach is **browser automation via CDP (Chrome DevTools Protocol)** at port 9222. This is already implemented in `tools/fetch_fb_messages.py`.

### 2.1 CDP-Only Architecture

```
Chrome Browser (port 9222)  ──CDP──→  Playwright
     │                                    │
     │ Human-authenticated               _scrape_inbox()
     │ FB session                         │
     │                                    ▼
     │                              FrankenSQLite
     │                                    │
     │                              ADK Pipeline
     │                              (Classify→CRM→Respond→Escalate)
     │                                    │
     │                                    ▼
     └─────────────────────────  send_reply_via_cdp()
                                  (Type in FB inbox UI)
```

**Both reading AND replying happen via CDP. No external API needed.**

### 2.2 Existing `_scrape_inbox()` Helper

Already battle-tested in `tools/fetch_fb_messages.py` (1,579 lines):

```python
# Connects to Chrome at port 9222
browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
cdp_page = browser.contexts[0].new_page()  # NEVER reuse user's tab

# Single-Pass Scroll-Click Pattern (handles virtualized list)
while scroll_round < max_scroll_rounds:
    visible_threads = page.evaluate('() => document.querySelectorAll("._ikh")')
    for vt in visible_threads:
        thread_el.click()            # Click while element is in DOM
        extract_messages(page)        # Extract bubbles immediately
        store_to_frankensqlite(conn)  # Save to DB
    page.mouse.wheel(0, 600)          # Scroll for more threads
```

### 2.3 Key Selectors (March 2026)

| Target | Selector |
|---|---|
| Thread Container | `div[data-pagelet="GenericBizInboxThreadListViewBody"]` |
| Individual Thread | `._ikh` |
| Message Region | `div[aria-label*="Message list container"]` |
| Message Bubbles | `div.x1fqp7bg` |
| Page Message | Class `x13a6bvl` |
| Customer Message | Class `x1nhvcw1` |

### 2.4 CDP Reply (New Capability)

```python
def send_reply_via_cdp(page, reply_text: str) -> bool:
    """Type and send a reply in the currently active FB inbox thread."""
    reply_selector = (
        'div[aria-label*="Reply"], '
        'div[aria-label*="Nhắn tin"], '
        'div[role="textbox"][contenteditable="true"]'
    )
    reply_box = page.wait_for_selector(reply_selector, timeout=5000)
    reply_box.click()
    page.keyboard.type(reply_text, delay=50)  # Simulate human typing
    page.keyboard.press("Enter")
    return True
```

### 2.5 Polling Model (No Webhooks Available)

```
┌─────────────────────────────────────┐
│  Fetch Inbox  →  ADK Process  →     │
│  Reply via CDP  →  Sleep 5min  →    │
│  (repeat)                           │
└─────────────────────────────────────┘
```

| Aspect | CDP Polling | Graph API Webhook |
|---|---|---|
| **Availability** | ✅ Works | ❌ Closed by Facebook |
| **Latency** | 1-5 min | ~1 second |
| **Auth** | Browser session (100%) | Token fragility |
| **Infrastructure** | Local Mac only | Needs public HTTPS |
| **Reply** | Type in UI via CDP | Send API (closed) |

---

## Part 3: MAS Agent Roles & Workflows

### 3.1 Organization Chart

```
                 ┌─────────────┐
                 │  Reception  │  ← Root Agent (Coordinator)
                 │  Manager    │
                 └──────┬──────┘
                        │
         ┌──────────────┼──────────────┐
         ▼              ▼              ▼
  ┌─────────────┐ ┌──────────┐ ┌──────────────┐
  │ Receptionist│ │  Guide   │ │  Supervisor  │
  │ (Classifier)│ │(Responder)│ │ (Escalator)  │
  └──────┬──────┘ └──────────┘ └──────┬───────┘
         ▼                            ▼
  ┌─────────────┐              ┌──────────────┐
  │ CRM Clerk   │              │  Telegram    │
  │ (DB Lookup) │              │  Notifier    │
  └─────────────┘              └──────────────┘
```

### 3.2 Agent Roles

#### Receptionist (Classifier)
- **Type**: `LlmAgent`
- **Output**: `intent` (greeting/question/registration/follow_up/spam), `language` (vi/en), `sentiment`, `urgency`

#### CRM Clerk (Database Agent)
- **Type**: `BaseAgent` / Tool functions
- **Tools**: `lookup_seeker(fb_user_id)`, `get_touchpoints()`, `get_journey_stage()`

#### Guide (Responder)
- **Type**: `LlmAgent`
- **Context-aware by journey stage**:

| Stage | Response Strategy |
|---|---|
| **New** | Welcome, introduce meditation, invite to free class |
| **Intake** | Share class schedule, answer basic questions |
| **Engaged** | Deepen engagement, share experiences |
| **Registered** | Confirm details, send reminders |
| **Attending** | Personal follow-up, deeper content |

#### Supervisor (Escalator)
- **Type**: `LlmAgent` with tools
- **Escalation triggers**: Negative sentiment, complaint, advanced-stage seeker, low confidence
- **Tools**: `send_telegram_alert()`, `flag_for_review()`

### 3.3 Workflow Patterns

```python
# Standard pipeline (80%+ of messages)
standard_pipeline = SequentialAgent(
    name="StandardPipeline",
    sub_agents=[receptionist, crm_clerk, guide, supervisor]
)

# With parallel context gathering
mas_root = SequentialAgent(
    name="SahajaYogaInboxMAS",
    sub_agents=[
        receptionist,
        ParallelAgent(name="ContextGathering", sub_agents=[crm_clerk, knowledge_base]),
        guide,
        supervisor
    ]
)
```

### 3.4 State Keys

| Key | Set By | Used By |
|---|---|---|
| `user_message` | Webhook handler | Receptionist |
| `sender_id` | Webhook handler | CRM Clerk |
| `classification` | Receptionist | Guide, Supervisor |
| `seeker_context` | CRM Clerk | Guide |
| `reply_text` | Guide | Supervisor, Reply tool |

### 3.5 Communication Guidelines

All agents share these core values in their instructions:
1. **Warmth**: Greet with joy and genuine care
2. **Patience**: Never rush or pressure a seeker
3. **Compassion**: Understand each person's unique journey
4. **Bilingual**: Respond in the seeker's language (vi/en)
5. **Non-commercial**: Meditation is always free

---

## Part 4: Full Integration Loop

### 4.1 End-to-End Flow

```python
def run_inbox_cycle(page_id: str):
    """Fetch → Find unreplied → ADK process → Reply via CDP."""
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        cdp_page = browser.contexts[0].new_page()
        conn = get_db_connection()

        try:
            # 1. FETCH via CDP
            _scrape_inbox(cdp_page, page_id, "1d", 50, conn)

            # 2. FIND UNREPLIED in FrankenSQLite
            unreplied = find_unreplied_threads(conn)

            # 3. For each: ADK process → CDP reply
            for thread in unreplied:
                messages = get_thread_messages(conn, thread['id'])
                reply = process_thread_with_adk(messages, thread['id'])
                if reply:
                    navigate_to_thread(cdp_page, thread['thread_name'])
                    send_reply_via_cdp(cdp_page, reply)
                    log_auto_reply(conn, thread['id'], reply)
        finally:
            cdp_page.close()  # Close only our tab
```

### 4.2 New DB Table

```sql
CREATE TABLE IF NOT EXISTS auto_replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    reply_text TEXT NOT NULL,
    agent_name TEXT DEFAULT 'responder',
    confidence REAL DEFAULT 1.0,
    escalated BOOLEAN DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### 4.3 Tool Mapping

| ADK Tool | Source | Method |
|---|---|---|
| `fetch_inbox()` | `tools/fetch_fb_messages.py` | CDP `_scrape_inbox()` |
| `lookup_seeker()` | FrankenSQLite | SQL on `users` table |
| `send_reply()` | **NEW** | CDP `send_reply_via_cdp()` |
| `send_telegram_alert()` | `tools/webhook_comments.py` | Telegram Bot API |

---

## Next Steps

1. **Prototype `send_reply_via_cdp()`** — test typing + sending in FB inbox
2. **Build ADK 2-agent pipeline** — Classifier + Responder only
3. **Create polling script** — `tools/inbox_mas_runner.py`
4. **Test end-to-end** — Fetch → Classify → Reply on test messages
5. **Add escalation** — Telegram notification for uncertain replies

---

## References

- [ADK GitHub](https://github.com/google/adk-python) · [ADK Docs](https://google.github.io/adk-docs)
- [Multi-Agent Systems](https://google.github.io/adk-docs/agents/multi-agents/) · [LLM Agents](https://google.github.io/adk-docs/agents/llm-agents/)
- Existing: [fetch_fb_messages.py](file:///Users/steve/sahajayogavn/funnel-tracking/tools/fetch_fb_messages.py) (CDP scraper)
- KI: Browser Automation & Scraping Patterns · Funnel Tracking Platform Architecture
