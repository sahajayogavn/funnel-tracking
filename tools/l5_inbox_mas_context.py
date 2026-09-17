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


# code:tool-inbox-mas-001:llm-source-of-truth
def get_llm_config() -> dict:
    """Read the canonical LLM configuration from the project `.env`.

    Legacy OPENAI_API_* variables are used only when the `.env` has no
    corresponding value, so a stale shell value cannot override the provider
    configured for this project.
    """
    from tools.env_manager import load_credentials

    creds = load_credentials(prefer_environment=False)
    api_base = (creds.get("OPENAI_COMPATIBLE_URL") or os.environ.get("OPENAI_API_BASE", "")).strip()
    api_key = creds.get("OPENAI_COMPATIBLE_KEY") or os.environ.get("OPENAI_API_KEY", "")
    model = (creds.get("OPENAI_COMPATIBLE_MODELS") or os.environ.get("ADK_MODEL", "gpt-5.4")).strip()
    model = model.split(",", 1)[0].strip()

    if not api_base or not api_key:
        raise RuntimeError(
            "LLM credentials not found. Set OPENAI_COMPATIBLE_URL and "
            "OPENAI_COMPATIBLE_KEY in the project .env."
        )
    return {"api_base": api_base, "api_key": api_key, "model": model}


def setup_llm_env():
    """Configure LLM environment variables for ADK/LiteLLM."""
    config = get_llm_config()
    os.environ["OPENAI_API_BASE"] = config["api_base"]
    os.environ["OPENAI_API_KEY"] = config["api_key"]
    os.environ["ADK_MODEL"] = (
        config["model"] if config["model"].startswith("openai/")
        else f"openai/{config['model']}"
    )
    logger.info(
        "LLM configured from project .env: base=%s... model=%s",
        config["api_base"][:30], os.environ["ADK_MODEL"],
    )
    return config
