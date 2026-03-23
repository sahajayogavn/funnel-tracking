---
trigger: always_on
glob: "adk_agents/**/*.py"
description: ADK Agent Software Development Rules
---

# ADK Agent Software Development Rules

These rules govern the creation and modification of Google ADK agents in `adk_agents/`.

1. **Package Structure**: The `adk_agents/` directory is a Python package. `__init__.py` must import `root_agent` from `agent.py`.
2. **ADK Commands**: Run locally with `adk run adk_agents/` (CLI) or `adk web .` from the repo root, then select `adk_agents` in the Web UI.
3. **Agent Naming**: Use descriptive names matching the actual workflow role (e.g., `MessageClassifier`, `Responder`, `Reactor`, `WarmUpComposer`, `EventAdvertiser`).
4. **Tools Location**: ADK tool functions go in `adk_agents/tools/`. Each tool is a plain Python function with docstrings.
5. **No sys.path Hacks in agent.py**: Use relative imports (`from .tools.seeker_tools import ...`). Only tool files that bridge to `tools/` CLI scripts may use `sys.path`.
6. **State Keys**: Use consistent state keys across agents: `classification`, `seeker_context`, `reply_text`, `thread_messages`, `knowledge_context`, `warmup_brief`, `event_details`, `reaction_content`, and `reaction_sender` where applicable.
7. **Model Config**: Model name is read from `ADK_MODEL` env var, defaulting to `openai/gpt-5.4`. Supports Gemini, GPT, Claude via LiteLLM.
8. **Dry-Run by Default**: All CDP reply functions default to `dry_run=True`. Set `dry_run=False` only in production.
9. **Universal IDs**: Tag agents with `# code:agent-mas-001:<component>` in comments.
10. **Database**: ADK tools read/write via `FrankenSQLite` at `memory/agent_memory/frankensqlite.db`. Use `get_db_connection()` from `tools/fetch_fb_messages.py`.
