# Spike: Production-Grade MAS Chatbot for Facebook Inbox

> **ID**: `doc:spike-adk-mas-001`
> **Date**: 2026-03-20 (updated)
> **Status**: Architecture Defined — Ready for Implementation
> **Goal**: Build a production-grade Google ADK Multi-Agent System (MAS) that handles Facebook inbox conversations using 4 specialized agents (Analyzer, Librarian, Validator, WarmUp), with SOPs, self-improvement cycles, and both reactive and proactive pipelines.

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
adk web .  # then select `adk_agents` in the Web UI

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

## Part 3: Production-Grade MAS — Agent Architecture

### 3.1 Design Philosophy

> **MAS = Software + Simulation of a Living Organization**
>
> - **Software**: Built on Google ADK, developed in iterations, has test cases, has QA gates.
> - **Living Organization**: Has pipelines, information passing, SOPs, and self-improvement through structured logging and introspection.

The MAS operates in **two modes**:

| Mode | Trigger | Pipeline | Purpose |
|---|---|---|---|
| **Reactive** | Customer sends a message | Analyzer → Librarian → Validator → Reply | Handle incoming conversations |
| **Proactive** | Cron/timer (configurable) | WarmUp → Librarian → Validator → Reply | Nurture inactive relationships |

### 3.2 Organization Chart

```
═══════════════════════════════════════════════════════════════════
                    SAHAJA YOGA INBOX MAS
              Production-Grade Chatbot Architecture
═══════════════════════════════════════════════════════════════════

  ┌─────────────────────────────────────────────────────────┐
  │               Orchestrator (Root Agent)                  │
  │     SequentialAgent — routes to reactive or proactive    │
  └─────────────────┬───────────────────────┬───────────────┘
                    │                       │
        ╔═══ REACTIVE MODE ═══╗   ╔═══ PROACTIVE MODE ═══╗
        ║  (on customer msg)  ║   ║  (on cron trigger)   ║
        ╚════════╤════════════╝   ╚══════════╤═══════════╝
                 │                            │
                 ▼                            ▼
        ┌────────────────┐           ┌────────────────┐
        │   🔍 ANALYZER  │           │   🔥 WARMUP    │
        │   (LlmAgent)   │           │   (BaseAgent)  │
        │                │           │                │
        │ • Read history │           │ • Find dormant │
        │ • Detect intent│           │   seekers      │
        │ • Map journey  │           │ • Pick warmup  │
        │   stage        │           │   strategy     │
        └───────┬────────┘           └───────┬────────┘
                │                            │
                │  analysis_result           │  warmup_brief
                ▼                            ▼
        ┌────────────────────────────────────────────┐
        │              📚 LIBRARIAN                  │
        │              (LlmAgent)                    │
        │                                            │
        │ • Query knowledge base                     │
        │ • Class schedules per city                  │
        │ • Events, study materials                   │
        │ • Sahaja Yoga meditation knowledge          │
        │ • FAQ about meditation (broader scope)      │
        └─────────────────────┬──────────────────────┘
                              │
                              │  knowledge_context
                              ▼
        ┌────────────────────────────────────────────┐
        │              ✅ VALIDATOR                   │
        │              (LlmAgent)                    │
        │                                            │
        │ • Gate-check: analysis + knowledge = OK?   │
        │ • Verify tone (warm, non-commercial)       │
        │ • Verify factual accuracy                  │
        │ • Decide: APPROVE / REVISE / ESCALATE      │
        └─────────────────────┬──────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        ┌──────────┐   ┌──────────┐   ┌──────────────┐
        │ APPROVE  │   │  REVISE  │   │  ESCALATE    │
        │ → Reply  │   │ → Loop   │   │ → Telegram   │
        │ via CDP  │   │ back to  │   │   alert to   │
        │          │   │ Librarian│   │   human team  │
        └──────────┘   └──────────┘   └──────────────┘
```

### 3.3 Agent Definitions

#### 3.3.1 Analyzer Agent — `🔍 The Intelligence Officer`

**Role**: First agent in the reactive pipeline. Reads the full interaction history of a customer, understands where they are in their journey, and produces a structured intent analysis.

- **Type**: `LlmAgent`
- **Input State Keys**: `user_message`, `thread_messages`, `seeker_context`
- **Output State Key**: `analysis_result`
- **Tools**: `lookup_seeker()`, `get_thread_messages()`, `get_touchpoints()`

**Output Schema**:

```json
{
  "intent": "question | registration | follow_up | greeting | complaint | thanks | spam",
  "language": "vi | en",
  "sentiment": "positive | neutral | negative",
  "urgency": "low | medium | high",
  "journey_stage": "Unknown | Seeker | Public Program Seeker | 18-Week Seeker | ...",
  "interaction_summary": "Returning seeker, attended 2 classes in Hà Nội, asking about next schedule",
  "context_for_librarian": "Needs Hà Nội class schedule for next week + info on what to expect"
}
```

```python
# code:agent-mas-001:analyzer
analyzer = LlmAgent(
    name="Analyzer",
    model=MODEL_NAME,
    instruction="""You are the Intelligence Officer of a Sahaja Yoga meditation center.

## Your Mission
Analyze the incoming message in the context of the FULL interaction history.
You must understand WHO this person is, WHERE they are in their journey,
and WHAT they truly need — not just what they literally asked.

## Interaction History
{thread_messages}

## Seeker Profile
{seeker_context}

## Current Message
{user_message}

## Analysis Protocol
1. Review all past messages to understand the relationship timeline
2. Identify the seeker's journey stage (Unknown → Seeker → ... → Sahaja Mahayogi)
3. Detect the underlying intent beyond surface-level words
4. Assess emotional state and urgency
5. Prepare a brief for the Librarian: what information does this seeker need?

Output your analysis as structured text:
Intent: <intent>
Language: <language>
Sentiment: <sentiment>
Urgency: <urgency>
Journey Stage: <stage>
Interaction Summary: <1-2 sentences on who this person is>
Context for Librarian: <what knowledge the Librarian should retrieve>""",
    tools=[lookup_seeker, get_thread_messages],
    output_key="analysis_result",
)
```

#### 3.3.2 Librarian Agent — `📚 The Knowledge Keeper`

**Role**: Manages the full knowledge base: Sahaja Yoga class schedules across Vietnam cities, events, study materials, meditation knowledge, and a broader FAQ about meditation in general.

- **Type**: `LlmAgent`
- **Input State Keys**: `analysis_result` (from Analyzer) OR `warmup_brief` (from WarmUp)
- **Output State Key**: `knowledge_context`
- **Tools**: `query_class_schedule()`, `query_events()`, `query_faq()`, `query_materials()`

**Knowledge Domains**:

| Domain | Content | Source |
|---|---|---|
| **Class Schedules** | Free meditation classes in 7 cities (Hà Nội, Bắc Ninh, Hải Phòng, Hưng Yên, Nghệ An, Đà Nẵng, Tp.HCM) | DB: `class_schedules` table |
| **Events** | Workshops, seminars, collective meditations, public programs | DB: `events` table |
| **Study Materials** | Guided meditations, chakra guides, technique instructions | DB: `materials` table |
| **Sahaja Knowledge** | Core Sahaja Yoga teachings, Shri Mataji's guidance | Static knowledge in instruction |
| **Meditation FAQ** | General questions about meditation (not just Sahaja-specific) | Static + web search fallback |

```python
# code:agent-mas-001:librarian
librarian = LlmAgent(
    name="Librarian",
    model=MODEL_NAME,
    instruction="""You are the Knowledge Keeper of a Sahaja Yoga meditation center in Vietnam.

## Your Mission
Based on the Analyzer's brief (or WarmUp brief), retrieve and compose
the most relevant information to help craft a response.

## Analysis / Brief
{analysis_result}
{warmup_brief}

## Knowledge Domains You Manage
1. **Class Schedules**: Free meditation classes in Hà Nội, Bắc Ninh, Hải Phòng,
   Hưng Yên, Nghệ An, Đà Nẵng, Tp. Hồ Chí Minh
2. **Events**: Upcoming workshops, seminars, collective meditations
3. **Study Materials**: Guided meditations, chakra descriptions, technique guides
4. **Sahaja Yoga Knowledge**: Core teachings of Sahaja Yoga meditation
5. **General Meditation FAQ**: Broader questions about meditation practice

## Retrieval Protocol
1. Parse the Analyzer's "Context for Librarian" to identify what's needed
2. Query relevant tools for structured data (schedules, events)
3. For knowledge questions, compose from your training + materials
4. For FAQ, answer with a slightly broader scope (meditation in general)
   while naturally connecting back to Sahaja Yoga
5. Provide the response material in a structured, usable format

## Output Format
Compose a knowledge brief that the Validator can use to craft the final reply:
- **Relevant Facts**: Bullet list of factual data (schedules, addresses, dates)
- **Suggested Tone**: Based on journey stage
- **Draft Response**: A proposed reply incorporating the relevant knowledge
- **Confidence**: high / medium / low (how certain you are about the information)""",
    tools=[query_class_schedule, query_events, query_faq],
    output_key="knowledge_context",
)
```

#### 3.3.3 Validator Agent — `✅ The Quality Gate`

**Role**: Final gate-check before any message is sent. Combines the Analyzer's understanding with the Librarian's knowledge to verify the response is appropriate, accurate, and aligned with the organization's values.

- **Type**: `LlmAgent`
- **Input State Keys**: `analysis_result`, `knowledge_context`, `thread_messages`
- **Output State Key**: `reply_text`, `validation_decision`
- **Tools**: `send_telegram_alert()`, `log_auto_reply()`

**Decision Matrix**:

| Decision | Condition | Action |
|---|---|---|
| **APPROVE** | Confidence ≥ high, tone correct, facts verified | Finalize `reply_text`, send via CDP |
| **REVISE** | Minor issues in tone or missing info | Loop back to Librarian with revision notes |
| **ESCALATE** | Low confidence, complaint, sensitive topic, or advanced-stage seeker | Send Telegram alert to human team, do NOT auto-reply |

```python
# code:agent-mas-001:validator
validator = LlmAgent(
    name="Validator",
    model=MODEL_NAME,
    instruction="""You are the Quality Gate of a Sahaja Yoga meditation center's chatbot.

## Your Mission
You are the LAST checkpoint before a message is sent to a real human being.
Your job is to ensure the response is appropriate, accurate, and warm.

## Analyzer's Assessment
{analysis_result}

## Librarian's Knowledge Brief
{knowledge_context}

## Conversation Thread
{thread_messages}

## Validation Checklist
1. ✅ TONE: Is the reply warm, genuine, and non-pushy?
2. ✅ LANGUAGE: Does it match the seeker's language (vi/en)?
3. ✅ ACCURACY: Are all facts correct (dates, locations, times)?
4. ✅ RELEVANCE: Does it address the seeker's actual intent?
5. ✅ NON-COMMERCIAL: Is meditation presented as always FREE?
6. ✅ APPROPRIATENESS: Is it suitable for the seeker's journey stage?
7. ✅ BREVITY: Is it concise (2-5 sentences for chat)?

## Decision Protocol
- If ALL checks pass → APPROVE: Output the final polished reply
- If minor issues exist → REVISE: Describe what needs fixing
- If sensitive/uncertain → ESCALATE: Flag for human review

## Output Format
Decision: APPROVE | REVISE | ESCALATE
Reason: <why this decision>
Reply: <the final message text, ONLY if APPROVE>
Revision Notes: <what to fix, ONLY if REVISE>
Escalation Reason: <why a human must handle this, ONLY if ESCALATE>""",
    tools=[send_telegram_alert, log_auto_reply],
    output_key="reply_text",
)
```

#### 3.3.4 WarmUp Agent — `🔥 The Relationship Builder`

**Role**: Proactive agent that triggers periodically (configurable cron). Identifies dormant or recently engaged seekers and initiates warm, nurturing outreach to strengthen the relationship.

- **Type**: `BaseAgent` (custom, non-LLM for selection logic) + `LlmAgent` (for message composition)
- **Input**: Timer/cron trigger + FrankenSQLite seeker data
- **Output State Key**: `warmup_brief` (passed to Librarian)
- **Tools**: `find_dormant_seekers()`, `get_warmup_history()`, `select_warmup_strategy()`

**WarmUp Strategies by Journey Stage**:

| Journey Stage | Days Since Last Contact | Strategy | Example |
|---|---|---|---|
| **Seeker** | 3-7 days | Gentle reminder about free classes | "We'd love to see you at our next free class!" |
| **Public Program Seeker** | 7-14 days | Share a meditation tip or event | "Here's a simple technique you can try at home..." |
| **18-Week Seeker** | 14-21 days | Check-in on their practice | "How has your meditation practice been going?" |
| **Registered** | 1-3 days | Confirm upcoming class details | "Just a reminder: your class is this Saturday!" |
| **Attending** | 14-30 days | Deepen engagement, share content | "Have you tried morning meditation? Here's a guide..." |

**Constraints**:
- Maximum 1 warmup message per seeker per 7 days
- Never WarmUp seekers marked as "spam" or "unsubscribed"
- Always pass through Librarian → Validator before sending
- Log every warmup attempt in `warmup_campaigns` table

```python
# code:agent-mas-001:warmup
class WarmUpSelector(BaseAgent):
    """Custom agent that selects seekers for warm-up outreach.

    This is a BaseAgent (no LLM) — it uses pure database logic
    to identify candidates and select warmup strategies.
    """

    async def _run_async_impl(self, ctx):
        dormant = find_dormant_seekers()
        for seeker in dormant["seekers"]:
            strategy = select_warmup_strategy(
                journey_stage=seeker["lead_stage"],
                days_since_contact=seeker["days_dormant"]
            )
            if strategy:
                ctx.session.state["warmup_brief"] = (
                    f"Seeker: {seeker['name']} | "
                    f"Stage: {seeker['lead_stage']} | "
                    f"Dormant: {seeker['days_dormant']} days | "
                    f"Strategy: {strategy['type']} | "
                    f"City: {seeker['city']}"
                )
                yield Event(author=self.name, content=strategy["type"])


warmup_selector = WarmUpSelector(name="WarmUpSelector")

warmup_composer = LlmAgent(
    name="WarmUpComposer",
    model=MODEL_NAME,
    instruction="""You compose warm, nurturing outreach messages for dormant seekers.

## WarmUp Brief
{warmup_brief}

## Guidelines
1. Keep it SHORT (1-3 sentences). This is a casual check-in, not a newsletter.
2. Be personal — reference their city and journey stage
3. Never be pushy or salesy. Meditation is always free.
4. Match the seeker's language (default Vietnamese for Vietnam seekers)
5. Include a gentle call-to-action (visit class, try a technique, ask a question)

Output ONLY the message text. No metadata.""",
    output_key="knowledge_context",  # feeds into Validator
)
```

### 3.4 Pipeline Definitions

#### Reactive Pipeline (Message → Response)

```python
# code:agent-mas-001:reactive-pipeline
reactive_pipeline = SequentialAgent(
    name="ReactivePipeline",
    description="Handle incoming customer messages: Analyze → Retrieve → Validate → Reply",
    sub_agents=[analyzer, librarian, validator],
)
```

#### Proactive Pipeline (Timer → WarmUp → Response)

```python
# code:agent-mas-001:proactive-pipeline
proactive_pipeline = SequentialAgent(
    name="ProactivePipeline",
    description="Periodic warm-up outreach: Select → Compose → Validate → Reply",
    sub_agents=[warmup_selector, warmup_composer, validator],
)
```

#### Root Orchestrator

```python
# code:agent-mas-001:root-orchestrator
root_agent = Agent(
    name="SahajaYogaMAS",
    model=MODEL_NAME,
    description="Production-grade MAS for Sahaja Yoga Facebook inbox",
    instruction="""You are the orchestrator of the Sahaja Yoga Inbox MAS.

Route incoming work to the appropriate pipeline:
- If there is a `user_message` in state → use ReactivePipeline
- If there is a `warmup_trigger` in state → use ProactivePipeline

Always ensure the pipeline completes fully before sending any reply.""",
    sub_agents=[reactive_pipeline, proactive_pipeline],
)
```

### 3.5 State Keys (Complete)

| Key | Set By | Consumed By | Type |
|---|---|---|---|
| `user_message` | CDP Fetch | Analyzer | `str` |
| `thread_id` | CDP Fetch | Analyzer, Librarian | `str` |
| `sender_id` | CDP Fetch | Analyzer | `str` |
| `thread_messages` | `get_thread_messages()` | Analyzer, Validator | `list[dict]` |
| `seeker_context` | `lookup_seeker()` | Analyzer | `dict` |
| `analysis_result` | Analyzer | Librarian, Validator | `str` (structured) |
| `warmup_brief` | WarmUp Selector | Librarian | `str` |
| `warmup_trigger` | Cron/Timer | Orchestrator | `bool` |
| `knowledge_context` | Librarian / WarmUp Composer | Validator | `str` |
| `reply_text` | Validator (APPROVE) | CDP Reply tool | `str` |
| `validation_decision` | Validator | Orchestrator, Logger | `str` |

### 3.6 Communication Guidelines (Shared Across All Agents)

All agents encode these values in their instructions via a shared `COMMUNICATION_GUIDELINES` constant:

1. **Warmth**: Greet with joy and genuine care
2. **Patience**: Never rush or pressure a seeker
3. **Compassion**: Understand each person's unique journey
4. **Bilingual**: Respond in the seeker's language (vi/en)
5. **Non-commercial**: Meditation is **always** free — we never charge, we never sell
6. **Accuracy**: Never fabricate class schedules, dates, or locations
7. **Brevity**: Chat replies should be 2-5 sentences. This is Messenger, not email.

---

## Part 4: Full Integration Loop

### 4.1 Reactive Mode — End-to-End Flow

```python
# code:agent-mas-001:reactive-loop
def run_reactive_cycle(page_id: str):
    """Fetch → Find unreplied → Analyzer → Librarian → Validator → Reply."""
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        cdp_page = browser.contexts[0].new_page()
        conn = get_db_connection()

        try:
            # 1. FETCH: Scrape inbox via CDP
            _scrape_inbox(cdp_page, page_id, "1d", 50, conn)

            # 2. FIND: Query unreplied threads from FrankenSQLite
            unreplied = find_unreplied_threads(conn, page_id)

            # 3. PROCESS: Each thread through the Reactive Pipeline
            for thread in unreplied["threads"]:
                # Load context into session state
                session_state = {
                    "user_message": get_latest_customer_message(conn, thread["thread_id"]),
                    "thread_id": thread["thread_id"],
                    "thread_messages": get_thread_messages(thread["thread_id"]),
                    "seeker_context": lookup_seeker(thread["thread_id"]),
                }

                # Run ADK Reactive Pipeline
                reply = run_adk_pipeline(reactive_pipeline, session_state)

                # 4. REPLY via CDP (if Validator approved)
                if reply and reply.get("validation_decision") == "APPROVE":
                    navigate_to_thread(cdp_page, page_id, thread["thread_name"])
                    send_reply_via_cdp(cdp_page, reply["reply_text"], dry_run=DRY_RUN)
                    log_auto_reply(thread["thread_id"], reply["reply_text"], "validator")

                # 5. LOG: Every cycle is logged for introspection
                log_agent_cycle(thread["thread_id"], session_state, reply)

        finally:
            cdp_page.close()
```

### 4.2 Proactive Mode — WarmUp Cycle

```python
# code:agent-mas-001:proactive-loop
def run_warmup_cycle(page_id: str):
    """Periodically warm up dormant seekers."""
    conn = get_db_connection()

    # 1. Find dormant seekers (BaseAgent logic, no LLM)
    dormant = find_dormant_seekers(page_id, max_seekers=5)

    for seeker in dormant["seekers"]:
        # Skip if recently warmed up
        if was_recently_warmed_up(seeker["thread_id"], days=7):
            continue

        # 2. Run through Proactive Pipeline
        session_state = {
            "warmup_trigger": True,
            "warmup_brief": compose_warmup_brief(seeker),
            "thread_id": seeker["thread_id"],
        }
        reply = run_adk_pipeline(proactive_pipeline, session_state)

        # 3. Send warmup message via CDP (if approved)
        if reply and reply.get("validation_decision") == "APPROVE":
            with create_cdp_page() as cdp_page:
                navigate_to_thread(cdp_page, page_id, seeker["thread_name"])
                send_reply_via_cdp(cdp_page, reply["reply_text"], dry_run=DRY_RUN)
                log_warmup_campaign(seeker["thread_id"], reply["reply_text"])

        # 4. Log for introspection
        log_agent_cycle(seeker["thread_id"], session_state, reply, mode="warmup")
```

### 4.3 Scheduling & Runner

```python
# code:agent-mas-001:runner
# tools/inbox_mas_runner.py

import schedule
import time

PAGE_ID = "119587786260266"

# Reactive: check inbox every 5 minutes
schedule.every(5).minutes.do(run_reactive_cycle, page_id=PAGE_ID)

# Proactive: warm up dormant seekers daily at 9 AM
schedule.every().day.at("09:00").do(run_warmup_cycle, page_id=PAGE_ID)

while True:
    schedule.run_pending()
    time.sleep(30)
```

### 4.4 Extended Database Schema

```sql
-- Existing table (enhanced)
CREATE TABLE IF NOT EXISTS auto_replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    reply_text TEXT NOT NULL,
    agent_name TEXT DEFAULT 'validator',
    pipeline_mode TEXT DEFAULT 'reactive',  -- 'reactive' or 'proactive'
    confidence REAL DEFAULT 1.0,
    validation_decision TEXT DEFAULT 'APPROVE',  -- APPROVE/REVISE/ESCALATE
    escalated BOOLEAN DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- NEW: Knowledge query log (what the Librarian retrieved)
CREATE TABLE IF NOT EXISTS knowledge_queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    query_type TEXT NOT NULL,           -- 'schedule', 'event', 'faq', 'material'
    query_text TEXT NOT NULL,
    result_summary TEXT,
    confidence TEXT DEFAULT 'high',     -- 'high', 'medium', 'low'
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- NEW: Agent cycle log (for introspection & self-improvement)
CREATE TABLE IF NOT EXISTS agent_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT,
    pipeline_mode TEXT NOT NULL,        -- 'reactive' or 'proactive'
    analyzer_output TEXT,
    librarian_output TEXT,
    validator_decision TEXT,
    final_reply TEXT,
    escalation_reason TEXT,
    processing_time_ms INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- NEW: WarmUp campaign tracker
CREATE TABLE IF NOT EXISTS warmup_campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    seeker_name TEXT,
    journey_stage TEXT,
    strategy_type TEXT,                 -- 'gentle_reminder', 'tip_share', 'check_in', etc.
    message_text TEXT NOT NULL,
    sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    response_received BOOLEAN DEFAULT 0,
    response_at DATETIME
);
```

### 4.5 Tool Mapping (Complete)

| ADK Tool | Agent | Source | Method |
|---|---|---|---|
| `lookup_seeker()` | Analyzer | `adk_agents/tools/seeker_tools.py` | SQL on `users` table |
| `get_thread_messages()` | Analyzer | `adk_agents/tools/seeker_tools.py` | SQL on `messages` table |
| `query_class_schedule()` | Librarian | **NEW** `adk_agents/tools/knowledge_tools.py` | SQL on `class_schedules` |
| `query_events()` | Librarian | **NEW** `adk_agents/tools/knowledge_tools.py` | SQL on `events` |
| `query_faq()` | Librarian | **NEW** `adk_agents/tools/knowledge_tools.py` | Static + DB |
| `send_telegram_alert()` | Validator | `tools/webhook_comments.py` | Telegram Bot API |
| `log_auto_reply()` | Validator | `adk_agents/tools/facebook_tools.py` | SQL on `auto_replies` |
| `find_dormant_seekers()` | WarmUp | **NEW** `adk_agents/tools/warmup_tools.py` | SQL on `users` |
| `get_warmup_history()` | WarmUp | **NEW** `adk_agents/tools/warmup_tools.py` | SQL on `warmup_campaigns` |
| `fetch_inbox()` | Runner | `tools/fetch_fb_messages.py` | CDP `_scrape_inbox()` |
| `send_reply_via_cdp()` | Runner | `adk_agents/tools/facebook_tools.py` | CDP keyboard input |

---

## Part 5: SOP & Living Organization Model

### 5.1 MAS as a Living Organization

This MAS is not just software — it is a **simulation of a living organization** with defined roles, protocols, and continuous improvement mechanisms.

```
┌──────────────────────────────────────────────────────────────┐
│                    THE LIVING ORGANIZATION                    │
│                                                              │
│  ┌─────────────────┐     ┌─────────────────┐                │
│  │   SOFTWARE      │     │  ORGANIZATION   │                │
│  │                 │     │                 │                │
│  │ • Google ADK    │     │ • Pipelines     │                │
│  │ • Python agents │     │ • SOPs          │                │
│  │ • Test cases    │     │ • Info passing  │                │
│  │ • QA gates      │     │ • Introspection │                │
│  │ • CI/CD         │     │ • Self-improve  │                │
│  └────────┬────────┘     └────────┬────────┘                │
│           └────────────┬──────────┘                          │
│                        ▼                                     │
│              ┌──────────────────┐                            │
│              │  PRODUCTION-GRADE │                            │
│              │     CHATBOT       │                            │
│              └──────────────────┘                            │
└──────────────────────────────────────────────────────────────┘
```

### 5.2 Standard Operating Procedures (SOPs)

Each agent follows a defined SOP — a structured protocol embedded in its instruction text.

| Agent | SOP Name | Protocol |
|---|---|---|
| **Analyzer** | `SOP-ANALYZE-001` | 1. Load thread history → 2. Lookup seeker profile → 3. Identify journey stage → 4. Detect intent → 5. Assess sentiment/urgency → 6. Compose Librarian brief |
| **Librarian** | `SOP-RETRIEVE-001` | 1. Parse Analyzer brief → 2. Identify knowledge domain → 3. Query tools (schedule/event/FAQ) → 4. Compose knowledge brief → 5. Rate confidence |
| **Validator** | `SOP-VALIDATE-001` | 1. Run 7-point checklist → 2. Cross-check facts → 3. Verify tone → 4. Decide: APPROVE / REVISE / ESCALATE → 5. Log decision |
| **WarmUp** | `SOP-WARMUP-001` | 1. Query dormant seekers → 2. Check warmup history (≤1 per 7 days) → 3. Select strategy by journey stage → 4. Compose brief → 5. Pass to Librarian → Validator chain |

### 5.3 Information Passing Protocol

Agents communicate through **ADK session state keys** — a shared memory space scoped to each session.

```
 ┌──────────────────────────────────────────────────────┐
 │                  SESSION STATE                        │
 │                                                      │
 │  user_message ──────────────→ Analyzer               │
 │  thread_messages ───────────→ Analyzer, Validator     │
 │  seeker_context ────────────→ Analyzer                │
 │                                                      │
 │  analysis_result ──→ Analyzer ──→ Librarian           │
 │  warmup_brief ─────→ WarmUp ───→ Librarian            │
 │                                                      │
 │  knowledge_context ────→ Librarian ──→ Validator      │
 │                                                      │
 │  reply_text ────────→ Validator ──→ CDP Reply         │
 │  validation_decision → Validator ──→ Orchestrator     │
 └──────────────────────────────────────────────────────┘
```

**Key Design Decisions**:
- Each agent writes to its own `output_key` — no agent overwrites another's output
- The Validator is the ONLY agent authorized to produce `reply_text`
- The WarmUp agent shares the same `knowledge_context` key as the Librarian, enabling pipeline reuse
- All state is ephemeral per session — long-term memory lives in FrankenSQLite

### 5.4 Self-Improvement Cycle

The MAS improves itself through structured logging and periodic introspection.

```
┌──────────────────────────────────────────────────────────┐
│                SELF-IMPROVEMENT CYCLE                     │
│                                                          │
│  1. EXECUTE                                              │
│     └─ Every agent cycle logs to `agent_logs` table      │
│                                                          │
│  2. OBSERVE                                              │
│     └─ Weekly introspection query:                       │
│        • How many ESCALATE decisions? Why?               │
│        • How many REVISE loops? What failed?             │
│        • Response time distribution                      │
│        • Customer re-engagement rate after WarmUp        │
│                                                          │
│  3. ANALYZE                                              │
│     └─ Identify patterns:                                │
│        • Recurring escalation topics → add to Librarian  │
│        • Frequent REVISE reasons → refine SOPs           │
│        • Low-confidence intents → improve Analyzer       │
│                                                          │
│  4. ADAPT                                                │
│     └─ Update agent instructions (SOPs) based on logs    │
│     └─ Add new FAQ to knowledge base                     │
│     └─ Adjust WarmUp strategies based on response rates  │
│                                                          │
│  (Repeat weekly)                                         │
└──────────────────────────────────────────────────────────┘
```

**Introspection Queries** (run weekly by human or scheduled script):

```sql
-- Escalation rate by intent
SELECT
    json_extract(analyzer_output, '$.intent') AS intent,
    COUNT(*) AS total,
    SUM(CASE WHEN validator_decision = 'ESCALATE' THEN 1 ELSE 0 END) AS escalated,
    ROUND(100.0 * SUM(CASE WHEN validator_decision = 'ESCALATE' THEN 1 ELSE 0 END) / COUNT(*), 1) AS escalation_pct
FROM agent_logs
WHERE created_at > datetime('now', '-7 days')
GROUP BY intent
ORDER BY escalation_pct DESC;

-- WarmUp effectiveness
SELECT
    strategy_type,
    COUNT(*) AS sent,
    SUM(response_received) AS responded,
    ROUND(100.0 * SUM(response_received) / COUNT(*), 1) AS response_rate
FROM warmup_campaigns
WHERE sent_at > datetime('now', '-30 days')
GROUP BY strategy_type
ORDER BY response_rate DESC;
```

### 5.5 Testing Strategy

The MAS is built as **production-grade software** — every component is testable.

| Test Layer | What | How |
|---|---|---|
| **Unit Tests** | Individual tool functions (`lookup_seeker`, `query_class_schedule`) | `pytest` with mock DB |
| **Agent Tests** | Each agent in isolation (does Analyzer produce valid JSON?) | ADK `test_agent()` with fixture messages |
| **Pipeline Tests** | Full reactive/proactive pipeline with mock data | `pytest` + ADK Runner with `InMemorySessionService` |
| **Integration Tests** | CDP reply in dry-run mode on real inbox | Playwright + `dry_run=True` |
| **Introspection Tests** | SQL queries on `agent_logs` return expected patterns | `pytest` with seeded DB |

```bash
# Run all tests
pytest tests/ -v --tb=short

# Run agent-specific tests
pytest tests/test_analyzer.py tests/test_librarian.py tests/test_validator.py -v

# Run pipeline integration test
pytest tests/test_reactive_pipeline.py -v
```

### 5.6 Iteration Roadmap

| Iteration | Goal | Agents | Status |
|---|---|---|---|
| **v0.1** | Basic 2-agent pipeline (Classifier → Responder) | 2 | ✅ Done |
| **v0.2** | Analyzer + Librarian (knowledge retrieval) | 2 | 🔲 Next |
| **v0.3** | Add Validator (quality gate) | 3 | 🔲 Planned |
| **v0.4** | Add WarmUp (proactive outreach) | 4 | 🔲 Planned |
| **v0.5** | Self-improvement cycle (logging + introspection) | 4 | 🔲 Planned |
| **v1.0** | Production deployment with full SOP coverage | 4+ | 🔲 Future |

---

## Next Steps

1. **Implement Analyzer agent** — replace Classifier with history-aware intent analysis
2. **Build knowledge tools** — `query_class_schedule()`, `query_events()`, `query_faq()`
3. **Implement Librarian agent** — knowledge retrieval with confidence scoring
4. **Add Validator agent** — gate-check with APPROVE/REVISE/ESCALATE decisions
5. **Create `agent_logs` table** — structured logging for every agent cycle
6. **Build WarmUp agent** — dormant seeker selection + strategy engine
7. **Write test suite** — unit, agent, pipeline, and integration tests
8. **Deploy polling runner** — `tools/inbox_mas_runner.py` with `schedule` library

---

## References

- [ADK GitHub](https://github.com/google/adk-python) · [ADK Docs](https://google.github.io/adk-docs)
- [Multi-Agent Systems](https://google.github.io/adk-docs/agents/multi-agents/) · [LLM Agents](https://google.github.io/adk-docs/agents/llm-agents/)
- [BaseAgent](https://google.github.io/adk-docs/agents/custom-agents/) · [Callbacks](https://google.github.io/adk-docs/callbacks/)
- Existing: [fetch_fb_messages.py](file:///Users/steve/sahajayogavn/funnel-tracking/tools/fetch_fb_messages.py) (CDP scraper)
- Existing: [agent.py](file:///Users/steve/sahajayogavn/funnel-tracking/adk_agents/agent.py) (v0.1 pipeline)
- KI: Browser Automation & Scraping Patterns · Funnel Tracking Platform Architecture
