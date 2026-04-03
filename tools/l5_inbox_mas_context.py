import os
import logging

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
logger = logging.getLogger("inbox_mas_context")

# code:tool-inbox-mas-001:knowledge-loader
KNOWLEDGE_FILES = [
    "memory/SOUL.md",
    "memory/agent_memory/faq.md",
    "memory/agent_memory/lop-hoc.md",
    "memory/agent_memory/su-kien.md",
    "memory/research.md",
    "memory/mas_strategy.md",
]

# Large files that should be truncated to save LLM context window tokens.
# mas_strategy.md is 46KB but only the first ~250 lines (Stage 0-5 journey
# definitions) are relevant for crafting inbox replies. The rest is operational
# routing/technical architecture that the LLM doesn't need.
KNOWLEDGE_FILE_MAX_LINES = {
    "memory/mas_strategy.md": 250,
}


def load_knowledge_context() -> str:
    """Load markdown knowledge files into a single prompt context string."""
    sections = []
    for relative_path in KNOWLEDGE_FILES:
        absolute_path = os.path.join(PROJECT_ROOT, relative_path)
        try:
            with open(absolute_path, "r", encoding="utf-8") as f:
                max_lines = KNOWLEDGE_FILE_MAX_LINES.get(relative_path)
                if max_lines:
                    content = "".join(f.readlines()[:max_lines]).strip()
                else:
                    content = f.read().strip()
                sections.append(f"## Source: {relative_path}\\n{content}")
        except FileNotFoundError:
            logger.warning(f"Knowledge file missing: {relative_path}")
        except Exception as exc:
            logger.warning(f"Knowledge file load failed ({relative_path}): {exc}")
    return "\\n\\n".join(section for section in sections if section)


def setup_llm_env():
    """Configure LLM environment variables for ADK/LiteLLM."""
    # Load credentials from env_manager
    from tools.env_manager import load_credentials
    creds = load_credentials()

    # Set OpenAI-compatible vars for LiteLLM
    api_base = creds.get("OPENAI_COMPATIBLE_URL", os.environ.get("OPENAI_COMPATIBLE_URL", ""))
    api_key = creds.get("OPENAI_COMPATIBLE_KEY", os.environ.get("OPENAI_COMPATIBLE_KEY", ""))

    if api_base:
        os.environ["OPENAI_API_BASE"] = api_base
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key

    logger.info(f"LLM configured: base={api_base[:30]}... model={os.environ.get('ADK_MODEL', 'openai/gpt-5.4')}")
