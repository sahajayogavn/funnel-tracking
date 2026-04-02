# Agent Instructions & Workflow Rules

This file documents the core operational rules and architectural patterns for the AI agents working in this repository.
**ALL UPDATES TO THIS FILE MUST BE IN ENGLISH.**

> **⚠️ SYNC RULE: `GEMINI.md` ↔ `CLAUDE.md` are paired files and MUST always have identical content.**
> Whenever you modify `GEMINI.md`, you MUST immediately apply the same changes to `CLAUDE.md`, and vice versa. Never update one without the other.

## 1. Project Directory Structure

The repository is structured to support Agile XP methodologies for AI Agents, ensuring separation of rules, workflows, agent memory, and executed code.

- **`.agents/rules/`**: Rules governing the behavior and boundaries of the AI agents.
- **`.agents/workflows/`**: Workflow configurations defining the steps agents take to accomplish tasks.
- **`.agents/skills/`**: Specific capabilities that agents can utilize (symlinked from `~/duyhunghd6/agent-skills/skills`).
- **`adk_agents/`**: Google ADK multi-agent system package (Classifier, Responder). Run with `.venv/bin/adk web .` from project root (see Section 8).
- **`tools/`**: Python scripts and utilities for external integrations (`webhook_comments.py`, etc.).
- **`web/`**: Next.js 16 full-stack web application (Dashboard, Seekers CRM, Network Graph, Journey Workflow).
- **`memory/agent_memory/`**: The knowledge base of the agents, storing course lists, event details, seeker logs, and FrankenSQLite DB.
- **`logs/`**: Directory for iteration handover reports and execution logs.
- **`docs/`**: Architecture documentation and planning documents.

## 2. Web Application (Next.js)

The full-stack web dashboard lives in `web/` and runs on **port 9994** by default.

- **Dev Server**: `cd web && npm run dev` → `http://localhost:9994`
- **Production Build**: `cd web && npm run build && npm start`
- **Database**: Reads existing `memory/agent_memory/frankensqlite.db` via `better-sqlite3` (readonly)
- **Rules**: Follow `.agents/rules/fullstack-rules.md` for server/client boundary, dynamic rendering, etc.

## 3. Python Environment Setup

All Python tools strictly run in an isolated environment.

- **Python Version**: `3.13.0` managed via `pyenv`.
- **Virtual Environment**: An isolated `.venv` must be created within the project root (`python3.13 -m venv .venv`).
- Ensure all execution is performed from within this activated `.venv` to prevent interference with macOS system libraries.

## 4. Credential and Token Security (Guardrails)

To prevent accidentally leaking sensitive keys to Git, the system utilizes an encode/decode workflow for `.env`.

- **Target Credentials**: `OPENAI_COMPATIBLE_URL`, `OPENAI_COMPATIBLE_KEY`, `OPENAI_COMPATIBLE_MODELS`, `GOOGLE_SHEET_CREDENTIALS`, `FACEBOOK_FANPAGE_APP_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
- **Guardrail Script**: Scripts use `tools/env_manager.py` to securely load these values.
- **Workflow**: Credentials are obfuscated via Base64 encoding into the `.env` file using the `save_credentials()` function and loaded seamlessly into `os.environ` via `load_credentials()` during script execution. Never commit raw `.env` files.

## 5. Modular Rules & Workflows

All actionable rules for the AI Agents are stored in the `.agents/rules/` directory for modularity and easy reference.

- **[Git Operations Rules](.agents/rules/git-operations.md)**: Guidelines for committing and pushing.
- **[Tool Writing Rules](.agents/rules/tool-writing.md)**: Standards for creating Python tools as CLI applications and explicitly defining database structure using FrankenSQLite.
- **[DevOps, QA & Testing Rules](.agents/rules/devops-qa.md)**: Rules for writing unit tests, QA, E2E testing, code coverage, and pre-commit checks.
- **[Full-Stack Web Rules](.agents/rules/fullstack-rules.md)**: Rules for Next.js web app development (server/client boundary, Tailwind, React Flow, graph visualization).
- **[ADK Agent Rules](.agents/rules/adk-agent-rules.md)**: Rules for Google ADK multi-agent system development (`adk_agents/` package structure, tool conventions, state keys).

## 6. Universal ID System

Every component, feature, document, or trace in this system must be assigned a **Universal ID**.

### 2.1. ID Format

The structure of a Universal ID is:
`<type>:<section-name-XXX>[:<component_name-YYY>]`

- Valid `<type>` values: `prd`, `logs`, `code`, `doc`, `bug`.
- Examples:
  - `prd:tool-fbpage-001` (Larger entity)
  - `prd:tool-fbpage-001:read-msg-001` (Component/smaller entity)

### 2.2. Relationship and State Matrix

The Universal IDs establish a loose connection (step-by-step relation) across different domains of the system.

- Mapping across domains forms a **State Matrix Satisfy**. For instance, a set of Plan IDs or Code IDs can satisfy a single PRD ID (`code:tool-fbpage-001:read-msg-001` satisfies `prd:tool-fbpage-001:read-msg-001`).

### 2.3. Placement Rules

Universal IDs must be explicitly embedded within the entities they represent:

- **PRD/Documentation**: Must be placed in the frontmatter/metadata or explicitly at the top of the sections.
- **Code**: Must be placed in code comments directly above the relevant function, class, or module.
- **Logs**: Must be printed at the `DEBUG` level during execution.

## 7. Logging & Handover Iterations

- All log files generated by the tools or the system must be saved in the `./logs/` directory.
- **Iteration Handover**: At the end of each iteration or work session, the agent must generate a handover report.
- **Starting an Iteration**: At the beginning of the next iteration, the agent must read the previous iteration's handover report and combine it with recent logs from `./logs/`. The agent must focus on tracing the Universal IDs to understand the state of the system, debug, and improve the logic.

## 8. Google ADK Testing

The `adk_agents/` package uses Google ADK 1.27+ with LiteLLM for OpenAI-compatible LLM routing.

### Prerequisites

- `google-adk` and `litellm` must be installed in `.venv`
- LLM credentials must be decoded from Base64 and set as env vars:
  ```bash
  export OPENAI_API_BASE=$(echo "aHR0cDovLzEwLjAuMS40Mjo4MzE3L3Yx" | base64 -d)
  export OPENAI_API_KEY=$(echo "aHVuZ2J1aS0yNTE2" | base64 -d)
  ```

### Interactive Testing (ADK Web UI)

```bash
OPENAI_API_BASE=$(echo "aHR0cDovLzEwLjAuMS40Mjo4MzE3L3Yx" | base64 -d) \
OPENAI_API_KEY=$(echo "aHVuZ2J1aS0yNTE2" | base64 -d) \
.venv/bin/adk web .
```

- **IMPORTANT**: Run from project root with `.` (not `adk web adk_agents/`)
- Opens Web UI at `http://127.0.0.1:8000`
- Select `adk_agents` from the app dropdown

### Automated E2E Tests (pytest)

```bash
OPENAI_API_BASE=$(echo "aHR0cDovLzEwLjAuMS40Mjo4MzE3L3Yx" | base64 -d) \
OPENAI_API_KEY=$(echo "aHVuZ2J1aS0yNTE2" | base64 -d) \
.venv/bin/python -m pytest tests/test_adk_e2e.py -v
```

- Tests auto-skip if `OPENAI_API_BASE`/`OPENAI_API_KEY` are not set
- Uses ADK `Runner` + `InMemorySessionService` (no browser needed)
- ~50s total for 10 tests against live LLM

### ADK Eval (Rubric-Based LLM-as-Judge)

```bash
OPENAI_API_BASE=$(echo "aHR0cDovLzEwLjAuMS40Mjo4MzE3L3Yx" | base64 -d) \
OPENAI_API_KEY=$(echo "aHVuZ2J1aS0yNTE2" | base64 -d) \
.venv/bin/adk eval adk_agents/ adk_agents/inbox_mas.evalset.json --print_detailed_results
```

- Eval sets are JSON files in `adk_agents/*.evalset.json`
- Each eval case has rubrics that an LLM judge scores

### ADK Agent Instruction Tips

- Use `{var_name?}` (with `?` suffix) for optional template variables in agent instructions — they resolve to empty string if not in session state, preventing `KeyError` crashes during interactive testing.
- The model is configured via `ADK_MODEL` env var, defaulting to `openai/gpt-5.4`.

## 9. QR Code Generation (Zalo Group Links)

QR code PNGs for Zalo group invite links are stored in `memory/agent_memory/qr_codes/`.

### CLI Tool

```bash
# Generate QR for a Zalo group URL
.venv/bin/python tools/generate_qr.py --url "https://zalo.me/g/<group_id>"

# Generate multiple QR codes
.venv/bin/python tools/generate_qr.py \
    --url "https://zalo.me/g/abc123" \
    --url "https://zalo.me/g/xyz789"

# List existing QR codes with reverse URL lookup
.venv/bin/python tools/generate_qr.py --list
```

### Filename Convention

URLs are converted to safe filenames via `safe_filename(URL)`:
- Replace all non-alphanumeric characters with `_`
- Collapse consecutive `_` into one
- Append `.png`

Example: `https://zalo.me/g/dxknkh602` → `https_zalo_me_g_dxknkh602.png`

### Integration with Agent Memory

- **`lop-hoc.md`**: Each class with a Zalo group has a `**Nhóm Zalo**` field and a QR mapping table.
- **`su-kien.md`**: Events can reference QR codes when Zalo groups are created.
- **Dependencies**: `pip install "qrcode[pil]"` (already in `.venv`).

## 10. Inbox MAS E2E Safety Rules

These rules are **absolute** and must never be violated:

### 10.1 E2E Test Target — Hung Bui Only

- When running any browser-based E2E test for the Inbox MAS pipeline (e.g. `inbox_mas_runner.py --once`), you **MUST** restrict the test to **Hung Bui's thread only** (`thread_name = "Hung Bui"`), because that is the developer's own Facebook account.
- **NEVER** run a browser E2E test against real customer threads. Real customers must not receive any test, draft, or accidental messages.
- To target Hung Bui specifically, pass `--max-threads 1` and confirm the first unreplied thread is Hung Bui before proceeding, or use a `--target-thread "Hung Bui"` flag if implemented.
- **UI Parser Changes**: Whenever you implement modifications to the DOM parser or crawler module, you **MUST** run a test script specifically targeting "Hung Bui" to verify that parsing successfully extracts all messages without missing any, BEFORE you can commit any code.
- **Snapshot Consistency Gate**: The extracted message output for the "Hung Bui" thread (e.g. `tests/hungbui_test_output.json`) MUST be 100% consistent across test runs. If successive test executions yield different message arrays or identical texts extracted varying numbers of times, the DOM fetching logic is considered FAILED. You must root-cause the extraction variance before committing.

### 10.2 Reply Sanitizer — Always Strip Reasoning Leaks

- The `_sanitize_reply()` function in `tools/l5_inbox_mas_runner.py` **MUST** be applied to every `reply_text` before it is typed into any Facebook message box.
- A reply that starts with `**Crafting...`, `I need to...`, `Let me...`, or any chain-of-thought narration is a **reasoning leak** and must be blocked — never sent.
- If `_sanitize_reply()` returns an empty string, the thread must be logged as `no_reply` and skipped. Do NOT type empty or reasoning-only text.

### 10.3 Inbox Replies MUST Be Pure Async (No Live CDP Drafting)

- **Do not automatically type or send messages to users without Telegram HITL.**
- The MAS Runner loop must strictly generate the AI response and queue the proposal to the Telegram DB (`telegram_hitl_queue`) and then quickly proceed to the next iteration without opening the Playwright browser interface.
- The `navigate_to_thread` and `send_reply_via_cdp` functions are EXCLUSIVELY reserved for the independent `hitl_execution_job.py` daemon, which only triggers AFTER receiving a 👍 LIKE reaction from a human operator in Telegram.
- You must never write MAS LLM evaluation logic that synchronously attempts to type out the draft in the UI before a human has approved the action.

### 10.4 Page ID Configuration

- The target main page for Inbox MAS automation is **Thiền Sahaja Yoga Việt Nam**.
- The explicit numeric `page-id` (and `asset_id`) for this page is `1548373332058326`.
- ALWAYS use this ID when invoking the runner (`--page-id 1548373332058326`) or generating E2E test scripts.

### 10.5 Architecture Layer Validation Gates

- Any modifications to the inbox extraction, normalization, persistence, or MAS runner orchestrator must pass the strict Layer Validation Gates (`doc:architecture-validation-001`).
- All validation tests enforcing these gates must carry the universal ID tag `# code:test-validation-001:<layer-name>`.
