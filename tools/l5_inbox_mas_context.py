import os
import logging

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
logger = logging.getLogger("inbox_mas_context")

# code:tool-inbox-mas-001:knowledge-loader
# Used by the warm-up / event composers (full-file fallback). The inbox reply
# path uses build_knowledge_context() below instead. research.md and
# mas_strategy.md were dropped from the default bundle: the former is only
# relevant when a seeker asks for scientific evidence, the latter is internal
# operating strategy (including a system architecture diagram) that no seeker
# facing message needs.
KNOWLEDGE_FILES = [
    "memory/SOUL.md",
    "memory/agent_memory/faq.md",
    "memory/agent_memory/lop-hoc.md",
    "memory/agent_memory/su-kien.md",
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
                sections.append(f"## Source: {relative_path}\n{content}")
        except FileNotFoundError:
            logger.warning(f"Knowledge file missing: {relative_path}")
        except Exception as exc:
            logger.warning(f"Knowledge file load failed ({relative_path}): {exc}")
    return "\n\n".join(section for section in sections if section)


# code:tool-inbox-mas-001:knowledge-retrieval
# Targeted retrieval replaces "load everything": only the class sections for the
# seeker's city (+ Online), FAQ entries that match the question, research only
# when asked for evidence, future events only, and a single contact line. This
# cuts ~60 KB per call to a few KB and stops the model borrowing another city's
# class. mas_strategy.md (system architecture) is never sent to the model.
import re as _re

_CITY_TOKENS = {
    "hà nội": ("hà nội", "ha noi", "hn"),
    "hồ chí minh": ("hồ chí minh", "ho chi minh", "hcm", "sài gòn", "sai gon"),
    "đà nẵng": ("đà nẵng", "da nang"),
    "hội an": ("hội an", "hoi an"),
    "nghệ an": ("nghệ an", "nghe an"),
    "vũng tàu": ("vũng tàu",), "hưng yên": ("hưng yên",), "hải phòng": ("hải phòng",),
}
_RESEARCH_TRIGGERS = ("khoa học", "nghiên cứu", "bằng chứng", "tác dụng", "hiệu quả", "chứng minh", "science", "research")
_FAQ_STOPWORDS = {"thiền", "không", "như", "thế", "nào", "khi", "gì", "có", "là", "và", "của", "cho", "để", "được", "lúc", "bao", "lâu", "mới"}


def _split_sections(text: str) -> list[tuple[str, str]]:
    parts = _re.split(r"^## ", text, flags=_re.MULTILINE)
    intro = parts[0].strip()
    sections = [(h.strip(), b) for h, _, b in (chunk.partition("\n") for chunk in parts[1:])]
    return [("", intro)] + sections if intro else sections


def _read(relative_path: str) -> str:
    try:
        with open(os.path.join(PROJECT_ROOT, relative_path), "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def normalize_city(city: str | None) -> str | None:
    text = (city or "").lower().replace("tp.", "").replace("tp ", "").strip()
    if not text or text in ("unknown", "online"):
        return None
    for key, tokens in _CITY_TOKENS.items():
        if any(tok in text for tok in tokens):
            return key
    return text


def _section_matches_city(heading: str, body: str, cities: set[str]) -> bool:
    area = ""
    match = _re.search(r"\*\*Khu vực(?: áp dụng)?\*\*:\s*(.+)", body)
    if match:
        area = match.group(1).lower()
    haystack = f"{heading.lower()} {area}"
    if "online" in haystack:
        return True
    if not cities:
        return True  # unknown city: keep every class section
    return any(tok in haystack for city in cities for tok in _CITY_TOKENS.get(city, (city,)))


def _strip_internal_lines(body: str) -> str:
    kept = []
    for line in body.splitlines():
        low = line.lower()
        if any(marker in low for marker in ("generate_qr", "safe_filename", "qr_codes/", "**tạo qr", "**xem danh sách", "**quy tắc tên file", "_(bot sẽ")):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _class_sections(cities: set[str]) -> str:
    text = _read("memory/agent_memory/lop-hoc.md")
    out = []
    for heading, body in _split_sections(text):
        if not heading:
            continue  # the intro is operator guidance, not seeker-facing
        if heading.startswith("📱") or heading.startswith("📞"):
            continue
        if _section_matches_city(heading, body, cities):
            out.append(f"### {heading}\n{_strip_internal_lines(body)}")
    return "\n\n".join(out)


def _faq_sections(question_text: str, max_items: int = 2) -> str:
    if not question_text:
        return ""
    words = {w for w in _re.findall(r"[\w']+", question_text.lower()) if len(w) > 2 and w not in _FAQ_STOPWORDS}
    if not words:
        return ""
    scored = []
    for heading, body in _split_sections(_read("memory/agent_memory/faq.md")):
        if not heading or heading.startswith("📞"):
            continue
        hay = f"{heading} {body}".lower()
        score = sum(1 for w in words if w in hay)
        if score >= 2:
            scored.append((score, heading, body))
    scored.sort(reverse=True)
    return "\n\n".join(f"### {h}\n{b.strip()}" for _, h, b in scored[:max_items])


def _event_is_past(text: str) -> bool:
    """True when the section names a month/year or dd/mm/yyyy already behind us."""
    from datetime import date
    today = date.today()
    for d, m, y in _re.findall(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text):
        try:
            if date(int(y), int(m), int(d)) < today:
                return True
        except ValueError:
            continue
    for m, y in _re.findall(r"[Tt]háng\s+(\d{1,2})/(\d{4})", text):
        if (int(y), int(m)) < (today.year, today.month):
            return True
    return False


def _future_events(cities: set[str]) -> str:
    out = []
    for heading, body in _split_sections(_read("memory/agent_memory/su-kien.md")):
        if not heading or "sắp diễn ra" not in heading.lower():
            continue
        if _event_is_past(f"{heading}\n{body}"):
            continue
        if cities and not any(tok in f"{heading} {body}".lower() for c in cities for tok in _CITY_TOKENS.get(c, (c,))):
            continue
        out.append(f"### {heading}\n{body.strip()}")
    return "\n\n".join(out)


def _contacts(cities: set[str]) -> str:
    text = _read("memory/agent_memory/lop-hoc.md")
    rows = _re.findall(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([\d ]{9,})\s*\|$", text, flags=_re.MULTILINE)
    lines = []
    for area, person, phone in rows:
        if not cities or normalize_city(area) in cities:
            lines.append(f"- {area}: {person} — {phone.strip()}")
    return "\n".join(lines)


# code:tool-inbox-mas-001:few-shot-page
def recent_human_page_examples(cities=None, limit: int = 4) -> list[str]:
    """Real replies volunteers typed recently (same city when known). They carry
    the right form of address, addresses and length better than any rule."""
    from fb_pipeline.persistence.l4_sqlite_store import get_db_connection
    city_set = {c for c in (normalize_city(c) for c in (cities or [])) if c}
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT m.content, u.city FROM messages m
            JOIN users u ON u.thread_id = m.thread_id
            WHERE m.sender = 'Page' AND m.kind = 'message'
              AND length(m.content) BETWEEN 40 AND 400
              AND m.content NOT LIKE 'Chào % bạn quan tâm%'
            ORDER BY m.message_at DESC LIMIT 200
            """
        ).fetchall()
    except Exception:
        return []
    finally:
        conn.close()
    picked = []
    for row in rows:
        if city_set and normalize_city(row["city"]) not in city_set:
            continue
        text = " ".join((row["content"] or "").split())
        if text and text not in picked:
            picked.append(text)
        if len(picked) >= limit:
            break
    return picked


def build_knowledge_context(cities=None, question_text: str = "", include_soul: bool = True,
                            include_examples: bool = True) -> str:
    """Assemble only the knowledge relevant to these seekers and this question."""
    city_set = {c for c in (normalize_city(c) for c in (cities or [])) if c}
    parts = []
    if include_examples:
        examples = recent_human_page_examples(cities)
        if examples:
            parts.append("## Cách CLB thường trả lời (tin nhắn thật của tình nguyện viên — học xưng hô, độ dài, địa chỉ)\n"
                         + "\n".join(f"- {e}" for e in examples))
    if include_soul:
        soul = _read("memory/SOUL.md").strip()
        if soul:
            parts.append(f"## Source: memory/SOUL.md\n{soul}")
    classes = _class_sections(city_set)
    if classes:
        parts.append(f"## Lớp học phù hợp (memory/agent_memory/lop-hoc.md)\n{classes}")
    faq = _faq_sections(question_text)
    if faq:
        parts.append(f"## FAQ liên quan (memory/agent_memory/faq.md)\n{faq}")
    if any(t in (question_text or "").lower() for t in _RESEARCH_TRIGGERS):
        research = _read("memory/research.md").strip()
        if research:
            parts.append(f"## Source: memory/research.md\n{research[:6000]}")
    events = _future_events(city_set)
    if events:
        parts.append(f"## Sự kiện sắp diễn ra (memory/agent_memory/su-kien.md)\n{events}")
    contacts = _contacts(city_set)
    if contacts:
        parts.append(f"## Liên hệ CLB theo khu vực\n{contacts}")
    return "\n\n".join(parts)


# code:tool-inbox-mas-001:llm-source-of-truth
# Compatibility re-exports: all MAS modules now share this Gemini-only lib.
from tools.l5_llm_provider import get_llm_config, setup_llm_env
